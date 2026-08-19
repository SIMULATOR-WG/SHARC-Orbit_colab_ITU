"""Per-system ε₀/α₀ partition of a method_3 joint run.

``run_epfd_simulation`` fed with ``system_id_per_sat`` +
``min_elevation_deg_per_system`` / ``alpha0_deg_per_system`` gates Step-18
eligibility per system: each filing's satellites use that filing's OWN
minimum operating elevation (bullet ①) and exclusion-zone half-angle — the
same criterion each filing gets on its independent method_1 run. One shared
scalar (the pre-partition behaviour, ε₀ = min across filings) lets a stricter
system's satellites transmit below their declared ε₀ and inflates its
contribution at intermediate percentiles.

These tests lock the partition's contract:

  * **Scalar equivalence** — homogeneous per-system lists reproduce the
    scalar run bit-for-bit (no regression for equal-ε₀ aggregates).
  * **Per-filing parity** — each system's sub-accumulator from the joint pass
    matches an independent single-system run with that filing's own scalar
    ε₀/α₀ at the same geometry/time base (Steps 18-22 are intra-system, so
    the fusion must not couple them).
  * **Direction** — a stricter ε₀ can only remove eligible satellites: the
    partitioned joint EPFD is ≤ the shared-min-ε₀ joint EPFD, step by step.
  * **Sequential ≡ parallel** — the arrays survive the chunked path.
"""
from __future__ import annotations

import math

import numpy as np

from src.antenna import ITURS1428Antenna  # type: ignore[import]
from src.epfd_calculator import run_epfd_simulation  # type: ignore[import]
from src.orbit_propagator import OrbitalElements  # type: ignore[import]
from src.pfd_mask import PFDMaskXML  # type: ignore[import]
from src.wcg_search import WCGResult, lla_to_ecef  # type: ignore[import]

N_SYSTEMS = 2
SATS_PER_SYSTEM = 48
NSTEPS = 2400  # < DECIM_TARGET_POINTS (10k), so the trace keeps EVERY step

# Heterogeneous thresholds: system 1 is much stricter in ε₀ and has a wider
# exclusion zone — the pair the shared-scalar convention distorts most.
PER_SYSTEM_EPS0 = [5.0, 40.0]
PER_SYSTEM_ALPHA0 = [2.0, 6.0]
PER_SYSTEM_NCO = [
    [(-90.0, 90.0, 0)],
    [(-90.0, 90.0, 2)],
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


def _system_constellation(sid: int) -> list[OrbitalElements]:
    # Both systems fly over the ES latitude (45°, see _wcg) so satellites of
    # each sweep the full elevation range there — the ε₀ partition then has
    # real candidates between 5° and 40° elevation to gate.
    rng = np.random.RandomState(100 + sid)
    return [
        OrbitalElements(
            a=7178.0 + 200.0 * sid,
            e=0.0,
            i=math.radians(rng.uniform(45 + 5 * sid, 60 + 5 * sid)),
            raan=math.radians(rng.uniform(0, 360)),
            omega=0.0,
            M=math.radians(rng.uniform(0, 360)),
        )
        for _ in range(SATS_PER_SYSTEM)
    ]


def _fused() -> tuple[list[OrbitalElements], np.ndarray]:
    combined: list[OrbitalElements] = []
    system_id: list[int] = []
    for sid in range(N_SYSTEMS):
        const = _system_constellation(sid)
        combined.extend(const)
        system_id.extend([sid] * len(const))
    return combined, np.asarray(system_id, dtype=np.int64)


def _wcg() -> WCGResult:
    return WCGResult(
        theta_deg=0, phi_deg=0, es_lat_deg=45.0, es_lon_deg=0.0, gso_lon_deg=0.0,
        alpha_deg=0, offaxis_deg=0, pfd_dBW=0, es_gain_rel_dB=0, epfd_dBW=0,
        elevation_deg=0, es_ecef_exact=lla_to_ecef(45.0, 0.0, 0.0),
    )


def _run_joint(n_jobs: int, *, eps0=None, alpha0=None,
               scalar_eps0: float = 5.0, scalar_alpha0: float = 2.0,
               nco=None, strict_exclusion_zone: bool = False):
    combined, system_id = _fused()
    return run_epfd_simulation(
        constellation=combined,
        wcg=_wcg(),
        pfd_mask=_alpha_mask(),
        es_antenna=ITURS1428Antenna(1.2, 12.0, 0.65),
        alpha0_deg=scalar_alpha0,
        min_elevation_deg=scalar_eps0,
        tstep_s=1.0,
        nsteps=NSTEPS,
        dual_ts=None,
        n_jobs=n_jobs,
        pfd_bw_correction_db=0.0,
        strict_exclusion_zone=strict_exclusion_zone,
        system_id_per_sat=system_id,
        max_co_freq_by_lat_per_system=nco if nco is not None else PER_SYSTEM_NCO,
        min_elevation_deg_per_system=eps0,
        alpha0_deg_per_system=alpha0,
    )


def _lin(db: float) -> float:
    return 0.0 if db <= -900.0 else 10.0 ** (db / 10.0)


def test_homogeneous_lists_reproduce_the_scalar_run():
    scalar = _run_joint(1).acc
    listed = _run_joint(
        1, eps0=[5.0] * N_SYSTEMS, alpha0=[2.0] * N_SYSTEMS,
    ).acc
    assert scalar.decim_epfd_db == listed.decim_epfd_db
    assert np.array_equal(scalar.duration_per_bin, listed.duration_per_bin)


def _run_solo(sid: int, eps0: float, alpha0: float):
    return run_epfd_simulation(
        constellation=_system_constellation(sid),
        wcg=_wcg(),
        pfd_mask=_alpha_mask(),
        es_antenna=ITURS1428Antenna(1.2, 12.0, 0.65),
        alpha0_deg=alpha0,
        min_elevation_deg=eps0,
        tstep_s=1.0,
        nsteps=NSTEPS,
        dual_ts=None,
        n_jobs=1,
        pfd_bw_correction_db=0.0,
        max_co_freq_by_lat=PER_SYSTEM_NCO[sid],
    ).acc


def _assert_sub_matches_solo(joint_acc, eps0: list, alpha0: list) -> None:
    """Each system's joint sub-accumulator == its own independent run with
    that filing's scalar ε₀/α₀ — step by step, in linear power."""
    assert set(joint_acc.per_system.keys()) == set(range(N_SYSTEMS))
    for sid in range(N_SYSTEMS):
        solo = _run_solo(sid, eps0[sid], alpha0[sid])
        # Non-vacuity: a system that never contributes would make this
        # comparison trivially true (−999 == −999 at every step).
        assert solo.n_steps_valid > 0, f"fixture too weak: system {sid} silent"
        sub = joint_acc.per_system[sid]
        assert sub.decim_t_s == solo.decim_t_s, sid
        for k in range(NSTEPS):
            assert math.isclose(
                _lin(sub.decim_epfd_db[k]), _lin(solo.decim_epfd_db[k]),
                rel_tol=1e-9, abs_tol=1e-30,
            ), (sid, k)


def test_each_system_matches_its_own_independent_run():
    joint = _run_joint(
        1, eps0=PER_SYSTEM_EPS0, alpha0=PER_SYSTEM_ALPHA0,
    ).acc
    _assert_sub_matches_solo(joint, PER_SYSTEM_EPS0, PER_SYSTEM_ALPHA0)


def test_alpha0_partition_is_exercised_alone():
    """α₀-only heterogeneity, ε₀ equal — the α₀ half of the partition must
    carry the parity on its own.

    The main parity fixture pairs α₀=6° with ε₀=40°, where the α-gate never
    binds (every satellite above 40° elevation sits far outside |α| < 6°), so
    an engine that silently dropped ``alpha0_deg_all`` would still pass it.
    Here system 0 declares α₀=6° while the joint run's scalar fallback is 2°:
    a dropped array is loudly visible as a sub-vs-solo mismatch."""
    eps0 = [5.0, 5.0]
    alpha0 = [6.0, 2.0]

    # Non-vacuity: α₀ 6° vs 2° must actually change system 0's own curve at
    # this geometry, else the parity below cannot see a dropped array.
    a6 = _run_solo(0, 5.0, 6.0)
    a2 = _run_solo(0, 5.0, 2.0)
    assert a6.decim_epfd_db != a2.decim_epfd_db, (
        "fixture too weak: α₀ 6° vs 2° indistinguishable for system 0"
    )

    joint = _run_joint(
        1, eps0=eps0, alpha0=alpha0, scalar_alpha0=2.0,
    ).acc
    _assert_sub_matches_solo(joint, eps0, alpha0)


def test_partition_gates_the_stricter_system():
    """Non-vacuity + direction: system 1 (ε₀=40°) must lose eligible
    satellites vs the shared-min run, and the partitioned joint EPFD can
    never exceed the shared-min joint EPFD.

    Runs with ``strict_exclusion_zone=True`` (no Step-18 bullet-② OR path):
    with the OR active, a satellite pushed out of bullet ① by its own ε₀ can
    legitimately re-enter through bullet ② — uncapped by MAX_CO_FREQ — so the
    ≤ direction is only a theorem for the pure-ε₀ gate. Uncapped Nco so every
    gated satellite actually shows up in the sum."""
    uncapped = [[(-90.0, 90.0, 0)]] * N_SYSTEMS
    # scalar ε₀=5° for everyone (the pre-partition shared-min rule)
    shared = _run_joint(1, nco=uncapped, strict_exclusion_zone=True).acc
    parted = _run_joint(
        1, eps0=PER_SYSTEM_EPS0, alpha0=[2.0] * N_SYSTEMS,
        nco=uncapped, strict_exclusion_zone=True,
    ).acc

    shared_lin = [_lin(db) for db in shared.decim_epfd_db]
    parted_lin = [_lin(db) for db in parted.decim_epfd_db]
    assert any(p < s for p, s in zip(parted_lin, shared_lin)), (
        "fixture too weak: ε₀=40° never gated system 1"
    )
    for k, (p, s) in enumerate(zip(parted_lin, shared_lin)):
        assert p <= s * (1.0 + 1e-9), f"step {k}: partitioned {p!r} > shared {s!r}"


def test_sequential_and_parallel_agree_with_partition():
    seq = _run_joint(1, eps0=PER_SYSTEM_EPS0, alpha0=PER_SYSTEM_ALPHA0).acc
    par = _run_joint(3, eps0=PER_SYSTEM_EPS0, alpha0=PER_SYSTEM_ALPHA0).acc
    assert np.array_equal(seq.duration_per_bin, par.duration_per_bin)
    for sid, sub_seq in seq.per_system.items():
        sub_par = par.per_system[sid]
        assert np.array_equal(sub_seq.duration_per_bin, sub_par.duration_per_bin), sid
