"""Dual time step + non-normative selection strategies (top_n_elev_random,
hybrid_rand_he, alpha_table).

The random strategies draw from a per-step RNG keyed by (seed, step_index).
They are supported on the dual time step only via the *sequential* walk, whose
``step_index = step_count`` is a chunk-independent monotonic counter. The engine
forces ``n_jobs=1`` for that combination.

The alpha_table strategy is deterministic and stateful (TSS credit). It also
supports the dual step, but only in the ``s1503_gain`` mode, where Δt is known
before selection so the TSS step weight ``w = Δt/T_fine`` is well defined
(Decision 8): both the credit accrual and the per-pick spend scale by ``w`` so
the *time-weighted* selected-α distribution still tracks the declared CDF.
These tests pin those contracts.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from src.wcg_search import WCGResult, lla_to_ecef  # type: ignore[import]
from src.antenna import ITURS1428Antenna  # type: ignore[import]
from src.pfd_mask import PFDMaskXML  # type: ignore[import]
from src.orbit_propagator import OrbitalElements  # type: ignore[import]
from src.time_step import DualTimeStep  # type: ignore[import]
from src.tss_accumulator import TSSAccumulator  # type: ignore[import]
from src.alpha_table import build_tss_cases  # type: ignore[import]
from src.epfd_calculator import run_epfd_simulation, SelectionConfig  # type: ignore[import]


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


def _constellation(n=200, seed=0):
    rng = np.random.RandomState(seed)
    return [
        OrbitalElements(
            a=7178.0, e=0.0, i=math.radians(rng.uniform(20, 90)),
            raan=math.radians(rng.uniform(0, 360)), omega=0.0,
            M=math.radians(rng.uniform(0, 360)),
        )
        for _ in range(n)
    ]


def _wcg():
    return WCGResult(
        theta_deg=0, phi_deg=0, es_lat_deg=0.0, es_lon_deg=0.0, gso_lon_deg=0.0,
        alpha_deg=0, offaxis_deg=0, pfd_dBW=0, es_gain_rel_dB=0, epfd_dBW=0,
        elevation_deg=0, es_ecef_exact=lla_to_ecef(0.0, 0.0, 0.0),
    )


def _dual(ant):
    return DualTimeStep(coarse_step_s=4.0, fine_step_s=1.0, mode="s1503_gain",
                        ncoarse=4, es_antenna=ant, alpha0_deg=2.0)


# Scenario chosen so the random draw actually bites: a dense constellation and a
# low elevation gate leave many α₀/ε₀-eligible candidates per step, and Nco=1 /
# top_n=10 forces one of many to be drawn — so different seeds diverge.
def _run(strategy, *, dual, n_jobs, seed=42, nsteps=300, keep_full_history=False,
         top_n=10, n_select=1, max_co=1):
    ant = ITURS1428Antenna(1.2, 12.0, 0.65)
    return run_epfd_simulation(
        constellation=_constellation(), wcg=_wcg(), pfd_mask=_alpha_mask(),
        es_antenna=ant, alpha0_deg=2.0, min_elevation_deg=5.0,
        tstep_s=1.0, nsteps=nsteps, dual_ts=_dual(ant) if dual else None,
        n_jobs=n_jobs, pfd_bw_correction_db=0.0,
        max_co_freq_by_lat=[(-90.0, 90.0, max_co)],
        keep_full_history=keep_full_history,
        selection_config=SelectionConfig(
            strategy=strategy, top_n=top_n, n_select=n_select, seed=seed,
        ),
    )


def _epfd_series(res) -> np.ndarray:
    return np.array([r.epfd_aggregate_dBW for r in res.time_steps], dtype=float)


@pytest.mark.parametrize("strategy", ["top_n_elev_random", "hybrid_rand_he"])
def test_dual_step_runs_for_random_strategies(strategy):
    """Dual step no longer raises for the random strategies and actually skips
    steps (coarse blocks fire), so fewer iterations than the fine-equivalent."""
    res = _run(strategy, dual=True, n_jobs=1)
    a = res.acc
    assert a.n_fine_steps + a.n_coarse_steps == a.n_steps
    assert a.n_steps < 300                       # dual stepping did skip steps
    assert a.n_coarse_steps > 0


@pytest.mark.parametrize("strategy", ["top_n_elev_random", "hybrid_rand_he"])
def test_dual_step_forces_serial_and_is_reproducible(strategy):
    """Requesting n_jobs>1 with the dual step is silently forced serial; a fixed
    seed makes two runs bit-for-bit identical (same per-step EPFD sequence)."""
    r1 = _run(strategy, dual=True, n_jobs=4, seed=7, keep_full_history=True)  # forced serial
    r2 = _run(strategy, dual=True, n_jobs=1, seed=7, keep_full_history=True)
    assert np.array_equal(_epfd_series(r1), _epfd_series(r2))


@pytest.mark.parametrize("strategy", ["top_n_elev_random", "hybrid_rand_he"])
def test_different_seed_changes_draw(strategy):
    """Different seeds pick different satellites, so the per-step EPFD sequence
    diverges at some steps (the random draw genuinely bites in this scenario)."""
    ra = _run(strategy, dual=True, n_jobs=1, seed=1, keep_full_history=True)
    rb = _run(strategy, dual=True, n_jobs=1, seed=2, keep_full_history=True)
    ea, eb = _epfd_series(ra), _epfd_series(rb)
    assert ea.shape == eb.shape
    assert np.count_nonzero(np.abs(ea - eb) > 1e-9) > 0


def test_s1503_dual_step_unaffected():
    """The normative path with the dual step is untouched by the change."""
    res = _run("s1503", dual=True, n_jobs=1)
    a = res.acc
    assert a.n_fine_steps + a.n_coarse_steps == a.n_steps
    assert a.n_steps < 300


# --------------------------------------------------------------------------- #
#  alpha_table + dual step (Decision 8: time-weighted TSS credit)
# --------------------------------------------------------------------------- #

def _tss(nco=2, bin_deg=0.0):
    pairs = [(2.0, 0.3), (5.0, 0.6), (10.0, 0.85), (20.0, 1.0)]
    edges, masses = build_tss_cases(pairs, bin_deg=bin_deg)
    return TSSAccumulator(bin_edges=edges, masses=masses, nco=nco)


def _run_alpha(*, dual, dual_mode="s1503_gain", nsteps=300):
    ant = ITURS1428Antenna(1.2, 12.0, 0.65)
    d = None
    if dual:
        d = DualTimeStep(coarse_step_s=4.0, fine_step_s=1.0, mode=dual_mode,
                         ncoarse=4, es_antenna=ant, alpha0_deg=2.0)
    return run_epfd_simulation(
        constellation=_constellation(), wcg=_wcg(), pfd_mask=_alpha_mask(),
        es_antenna=ant, alpha0_deg=2.0, min_elevation_deg=5.0,
        tstep_s=1.0, nsteps=nsteps, dual_ts=d, n_jobs=1, pfd_bw_correction_db=0.0,
        max_co_freq_by_lat=[(-90.0, 90.0, 2)], keep_full_history=True,
        selection_config=SelectionConfig(strategy="alpha_table"),
        alpha_tss=_tss(),
    )


def test_alpha_table_runs_with_dual_step():
    """alpha_table + s1503_gain dual step runs and actually skips steps."""
    res = _run_alpha(dual=True)
    a = res.acc
    assert a.n_fine_steps + a.n_coarse_steps == a.n_steps
    assert a.n_steps < 300
    assert a.n_coarse_steps > 0


def test_alpha_table_dual_is_deterministic():
    """alpha_table has no RNG: two dual runs are bit-for-bit identical."""
    r1 = _run_alpha(dual=True)
    r2 = _run_alpha(dual=True)
    assert np.array_equal(_epfd_series(r1), _epfd_series(r2))


def test_alpha_table_rejects_alpha_threshold_dual():
    """The legacy alpha_threshold dual mode leaves the TSS step weight undefined
    (Δt decided after selection) → unsupported."""
    with pytest.raises(NotImplementedError):
        _run_alpha(dual=True, dual_mode="alpha_threshold", nsteps=50)


def test_alpha_table_fixed_step_unchanged():
    """With a fixed step the weight is 1 everywhere, so the fixed-step alpha_table
    result is reproducible (regression guard for the w=1 reduction)."""
    r1 = _run_alpha(dual=False)
    r2 = _run_alpha(dual=False)
    assert np.array_equal(_epfd_series(r1), _epfd_series(r2))


def test_tss_weighted_accrual_and_spend():
    """Decision 8 unit check: a coarse update accrues w×, a coarse pick spends w×,
    and w=1 reduces to the per-step form."""
    # Weighted accrual: one step at weight 4 == four unit steps.
    a = _tss(nco=2, bin_deg=2.0)
    b = _tss(nco=2, bin_deg=2.0)
    a.update(weight=4.0)
    for _ in range(4):
        b.update(weight=1.0)
    assert np.allclose(a.credits, b.credits)

    # Weighted spend: picking one satellite in bin(|α|≈3°) at weight 4 removes
    # 4 credits from that bin (vs 1 at unit weight).
    base = _tss(nco=1, bin_deg=2.0)
    base.update(weight=1.0)
    before = base.credits.copy()
    # A single eligible candidate at |α| = 3° (epfd irrelevant, nco=1 slot).
    base.select([(1.0, 7)], {7: 3.0}, t_s=0.0, weight=4.0)
    bin_idx = base._bin_of(3.0)
    spent = before[bin_idx] - base.credits[bin_idx]
    assert math.isclose(spent, 4.0, rel_tol=1e-9)
