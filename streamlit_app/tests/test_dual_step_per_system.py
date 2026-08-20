"""Per-system decomposition survives a DUAL-TIME-STEP joint run.

``run_epfd_simulation`` accumulates one sub-accumulator per system in the same
pass as the joint curve, which is what lets method_3 report per-system curves
for free. The parallel **dual-time-step** chunk worker never passed
``per_system_out``, so ``acc.per_system`` came back empty for a dual joint run
and the worker fell back to ``_decompose_by_resimulation`` — one full-length
simulation PER SYSTEM, about as expensive as the joint run itself. That made
enabling §D4.7 in method_3 a performance trap.

These tests pin the fixed contract on the dual path:

  * the per-system split is populated (so the cheap single-pass decomposition
    is used, not the re-simulation fallback);
  * the linear sum of the per-system EPFD reproduces the joint EPFD at every
    recorded step, with heterogeneous MAX_CO_FREQ per system;
  * the per-system time base matches the joint one — including the clamped
    chunk-boundary durations the dual path uses;
  * sequential and parallel dual runs agree.
"""
from __future__ import annotations

import math

import numpy as np

from src.antenna import ITURS1428Antenna  # type: ignore[import]
from src.epfd_calculator import run_epfd_simulation  # type: ignore[import]
from src.orbit_propagator import OrbitalElements  # type: ignore[import]
from src.pfd_mask import PFDMaskXML  # type: ignore[import]
from src.time_step import DualTimeStep  # type: ignore[import]
from src.wcg_search import WCGResult, lla_to_ecef  # type: ignore[import]

N_SYSTEMS = 2
SATS_PER_SYSTEM = 24
NSTEPS = 1200  # fine-step equivalents; well under the decimation cap

PER_SYSTEM_NCO = [
    [(-90.0, 90.0, 0)],   # uncapped
    [(-90.0, 90.0, 2)],   # capped — exercises the per-system Steps 20-21 split
]


def _alpha_mask() -> PFDMaskXML:
    lats, alphas, dlons = [-60.0, 0.0, 60.0], [0.0, 2.0, 5.0, 10.0], [-5.0, 0.0, 5.0]
    x = ['<satellite_system><pfd_mask mask_id="1" type="alpha_deltaLongitude" '
         'refbw_khz="40" a_name="latitude" b_name="alpha" c_name="deltaLongitude">']
    for la in lats:
        x.append(f'<by_a a="{la}">')
        for a in alphas:
            x.append(f'<by_b b="{a}">')
            for d in dlons:
                x.append(f'<pfd c="{d}">{-150.0 - a - abs(d):.3f}</pfd>')
            x.append("</by_b>")
        x.append("</by_a>")
    x.append("</pfd_mask></satellite_system>")
    return PFDMaskXML.from_xml_content("\n".join(x), mask_id=1)


def _fused() -> tuple[list[OrbitalElements], np.ndarray]:
    combined: list[OrbitalElements] = []
    system_id: list[int] = []
    for sid in range(N_SYSTEMS):
        rng = np.random.RandomState(100 + sid)
        for _ in range(SATS_PER_SYSTEM):
            combined.append(OrbitalElements(
                a=7178.0 + 200.0 * sid, e=0.0,
                i=math.radians(rng.uniform(45 + 5 * sid, 60 + 5 * sid)),
                raan=math.radians(rng.uniform(0, 360)), omega=0.0,
                M=math.radians(rng.uniform(0, 360)),
            ))
            system_id.append(sid)
    return combined, np.asarray(system_id, dtype=np.int64)


def _wcg() -> WCGResult:
    return WCGResult(
        theta_deg=0, phi_deg=0, es_lat_deg=45.0, es_lon_deg=0.0, gso_lon_deg=0.0,
        alpha_deg=0, offaxis_deg=0, pfd_dBW=0, es_gain_rel_dB=0, epfd_dBW=0,
        elevation_deg=0, es_ecef_exact=lla_to_ecef(45.0, 0.0, 0.0),
    )


def _run(n_jobs: int):
    ant = ITURS1428Antenna(1.2, 12.0, 0.65)
    combined, system_id = _fused()
    return run_epfd_simulation(
        constellation=combined,
        wcg=_wcg(),
        pfd_mask=_alpha_mask(),
        es_antenna=ant,
        alpha0_deg=2.0,
        min_elevation_deg=10.0,
        tstep_s=1.0,
        nsteps=NSTEPS,
        dual_ts=DualTimeStep(
            coarse_step_s=4.0, fine_step_s=1.0, mode="s1503_gain",
            ncoarse=4, es_antenna=ant, alpha0_deg=2.0,
        ),
        n_jobs=n_jobs,
        pfd_bw_correction_db=0.0,
        system_id_per_sat=system_id,
        max_co_freq_by_lat_per_system=PER_SYSTEM_NCO,
    )


def _lin(db: float) -> float:
    return 0.0 if db <= -900.0 else 10.0 ** (db / 10.0)


def test_dual_parallel_run_populates_the_per_system_split():
    """Empty here is what forced the expensive re-simulation fallback."""
    acc = _run(n_jobs=3).acc
    assert acc.per_system, "dual parallel run carried no per-system split"
    assert set(acc.per_system.keys()) == set(range(N_SYSTEMS))
    assert acc.n_steps_valid > 0
    contributing = [s for s, sub in acc.per_system.items() if sub.n_steps_valid > 0]
    assert len(contributing) >= 2, f"fixture too weak: only {contributing} contribute"


def test_dual_per_system_sum_reproduces_the_joint_curve():
    acc = _run(n_jobs=3).acc
    joint_t = acc.decim_t_s
    for sub in acc.per_system.values():
        assert sub.decim_t_s == joint_t
    for k in range(len(joint_t)):
        joint_lin = _lin(acc.decim_epfd_db[k])
        parts = sum(_lin(sub.decim_epfd_db[k]) for sub in acc.per_system.values())
        assert math.isclose(parts, joint_lin, rel_tol=1e-9, abs_tol=1e-30), k


def test_dual_per_system_shares_the_joint_time_base():
    """Durations must match the joint ones, clamped chunk boundaries included —
    otherwise per-system CCDF percentages are computed over a different total."""
    acc = _run(n_jobs=3).acc
    for sid, sub in acc.per_system.items():
        assert sub.n_steps == acc.n_steps, sid
        assert math.isclose(sub.total_duration_s, acc.total_duration_s, rel_tol=1e-12), sid


def test_dual_sequential_and_parallel_agree_per_system():
    seq = _run(n_jobs=1).acc
    par = _run(n_jobs=3).acc
    assert set(seq.per_system) == set(par.per_system)
    for sid, sub_seq in seq.per_system.items():
        sub_par = par.per_system[sid]
        assert math.isclose(
            sub_seq.total_duration_s, sub_par.total_duration_s, rel_tol=1e-9,
        ), sid
