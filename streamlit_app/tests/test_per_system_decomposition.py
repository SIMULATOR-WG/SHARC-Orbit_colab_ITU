"""Single-pass per-system decomposition of a method_3 joint run.

``run_epfd_simulation`` fed with ``system_id_per_sat`` +
``max_co_freq_by_lat_per_system`` accumulates, in the SAME pass, one
sub-accumulator per system (``acc.per_system``). These tests lock the three
properties the worker relies on to stop re-simulating each system separately:

  * **Sum invariant** — at every step, the linear sum of the per-system EPFD
    equals the joint EPFD. This is the whole point: Step 23 is a linear sum and
    each satellite belongs to exactly one system, so the joint curve decomposes
    exactly. It must hold *with heterogeneous MAX_CO_FREQ per system*, since the
    Steps 20-21 selection is partitioned by system.
  * **Same time base** — every sub-accumulator sees every step (silent systems
    included), so the CCDF denominator ``total_duration_s`` matches the joint
    one and the curves are comparable percentile-by-percentile.
  * **Sequential ≡ parallel** — the per-system merge is commutative, so chunked
    execution reproduces the single-process result bit-for-bit.
"""
from __future__ import annotations

import math

import numpy as np

from src.antenna import ITURS1428Antenna  # type: ignore[import]
from src.epfd_calculator import run_epfd_simulation  # type: ignore[import]
from src.orbit_propagator import OrbitalElements  # type: ignore[import]
from src.pfd_mask import PFDMaskXML  # type: ignore[import]
from src.wcg_search import WCGResult, lla_to_ecef  # type: ignore[import]


N_SYSTEMS = 3
SATS_PER_SYSTEM = 24
NSTEPS = 240  # < DECIM_TARGET_POINTS, so the decimated trace keeps EVERY step


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


def _fused_constellation() -> tuple[list[OrbitalElements], np.ndarray]:
    """N_SYSTEMS constellations fused into one, plus the system id per satellite.

    Distinct inclination bands per system so each contributes at different
    times — a fixture where one system dominates every step would make the sum
    invariant trivially satisfiable.
    """
    combined: list[OrbitalElements] = []
    system_id: list[int] = []
    for sid in range(N_SYSTEMS):
        rng = np.random.RandomState(100 + sid)
        for _ in range(SATS_PER_SYSTEM):
            combined.append(OrbitalElements(
                a=7178.0 + 200.0 * sid,
                e=0.0,
                i=math.radians(rng.uniform(20 + 20 * sid, 40 + 20 * sid)),
                raan=math.radians(rng.uniform(0, 360)),
                omega=0.0,
                M=math.radians(rng.uniform(0, 360)),
            ))
            system_id.append(sid)
    return combined, np.asarray(system_id, dtype=np.int64)


def _wcg() -> WCGResult:
    return WCGResult(
        theta_deg=0, phi_deg=0, es_lat_deg=0.0, es_lon_deg=0.0, gso_lon_deg=0.0,
        alpha_deg=0, offaxis_deg=0, pfd_dBW=0, es_gain_rel_dB=0, epfd_dBW=0,
        elevation_deg=0, es_ecef_exact=lla_to_ecef(0.0, 0.0, 0.0),
    )


# Heterogeneous MAX_CO_FREQ: system 0 uncapped (0 = unlimited), 1 capped at 3,
# 2 capped at 1 — exercises the per-system partition of Steps 20-21.
PER_SYSTEM_NCO = [
    [(-90.0, 90.0, 0)],
    [(-90.0, 90.0, 3)],
    [(-90.0, 90.0, 1)],
]


def _run(n_jobs: int):
    combined, system_id = _fused_constellation()
    return run_epfd_simulation(
        constellation=combined,
        wcg=_wcg(),
        pfd_mask=_alpha_mask(),
        es_antenna=ITURS1428Antenna(1.2, 12.0, 0.65),
        alpha0_deg=2.0,
        min_elevation_deg=10.0,
        tstep_s=1.0,
        nsteps=NSTEPS,
        dual_ts=None,
        n_jobs=n_jobs,
        pfd_bw_correction_db=0.0,
        system_id_per_sat=system_id,
        max_co_freq_by_lat_per_system=PER_SYSTEM_NCO,
    )


def _lin(db: float) -> float:
    return 0.0 if db <= -900.0 else 10.0 ** (db / 10.0)


def test_per_system_accumulators_are_populated():
    res = _run(n_jobs=1)
    acc = res.acc
    assert set(acc.per_system.keys()) == set(range(N_SYSTEMS))
    # Non-vacuity: the joint run must carry real EPFD, and at least two systems
    # must contribute — otherwise the sum invariant below proves nothing.
    assert acc.n_steps_valid > 0
    contributing = [sid for sid, sub in acc.per_system.items() if sub.n_steps_valid > 0]
    assert len(contributing) >= 2, f"fixture too weak: only {contributing} contribute"


def test_per_system_sum_reproduces_joint_step_by_step():
    """Σ_systems 10^(epfd_i/10) == 10^(epfd_joint/10) at every step."""
    res = _run(n_jobs=1)
    acc = res.acc
    joint_t = acc.decim_t_s
    assert len(joint_t) == NSTEPS, "fixture must keep an undecimated trace"

    for sub in acc.per_system.values():
        assert sub.decim_t_s == joint_t  # same steps, same order

    for k in range(NSTEPS):
        joint_lin = _lin(acc.decim_epfd_db[k])
        parts_lin = sum(_lin(sub.decim_epfd_db[k]) for sub in acc.per_system.values())
        assert math.isclose(parts_lin, joint_lin, rel_tol=1e-9, abs_tol=1e-30), (
            f"step {k}: parts={parts_lin!r} joint={joint_lin!r}"
        )


def test_per_system_shares_the_joint_time_base():
    """CCDF denominators must match, else per-system percentages are inflated."""
    acc = _run(n_jobs=1).acc
    for sid, sub in acc.per_system.items():
        assert sub.n_steps == acc.n_steps, sid
        assert math.isclose(sub.total_duration_s, acc.total_duration_s, rel_tol=1e-12)


def test_sequential_and_parallel_agree_per_system():
    seq = _run(n_jobs=1).acc
    par = _run(n_jobs=3).acc
    assert np.array_equal(seq.duration_per_bin, par.duration_per_bin)
    assert set(seq.per_system) == set(par.per_system)
    for sid, sub_seq in seq.per_system.items():
        sub_par = par.per_system[sid]
        assert np.array_equal(sub_seq.duration_per_bin, sub_par.duration_per_bin), sid
        assert sub_seq.n_steps == sub_par.n_steps, sid
        b_seq, p_seq = sub_seq.build_ccdf()
        b_par, p_par = sub_par.build_ccdf()
        assert np.array_equal(b_seq, b_par), sid
        assert np.allclose(p_seq, p_par, atol=1e-12), sid
