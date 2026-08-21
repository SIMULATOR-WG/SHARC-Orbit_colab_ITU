"""Direct unit tests for the reference-vector selection algorithm
(US proposal R23-WP4A-C-0519), covering the pieces that
tests/test_geometry_ref_vec.py (geometry primitive only) and
tests/test_ref_vec_dual_step.py (dual-time-step interlock only) don't touch:

- _get_lowest_avg_ref_vec_separation: eligibility-for-the-whole-window gate,
  the M-worst-then-average ranking, the WCG-satellite carve-out, and the
  degenerate zenith/T=1/M=1 (= highest elevation) case.
- _compute_epfd_for_selected_sats: the fixed-set linear-power summation and
  its empty-selection sentinel.
- run_epfd_simulation_ref_vec's hold-and-reselect cadence (reselect every
  MIN_DURATION-derived window, reuse the same set in between).

_get_lowest_avg_ref_vec_separation is exercised directly, with
propagate_and_to_ecef_batch / compute_alpha_and_optimal_gso_fixed_es_batch
mocked out so each future lookahead step returns a hand-picked elevation per
satellite (real Kepler propagation would make the expected numbers
impossible to hand-verify). The reference-vector separation itself
(compute_angular_separation_from_ref_vector) runs for real against those
positions.
"""
from __future__ import annotations

import math
from unittest.mock import patch

import numpy as np

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.coordinates import lla_to_ecef, gso_position_ecef  # type: ignore[import]
from src.antenna import ITURS1428Antenna  # type: ignore[import]
from src.pfd_mask import PFDMaskXML  # type: ignore[import]
from src.orbit_propagator import OrbitalElements  # type: ignore[import]
from src.wcg_search import WCGResult  # type: ignore[import]
from src.time_step import compute_track_duration_windows  # type: ignore[import]
from src.epfd_calculator import (  # type: ignore[import]
    _get_lowest_avg_ref_vec_separation,
    _compute_epfd_for_selected_sats,
    run_epfd_simulation_ref_vec,
)

RE = 6371.0  # km — arbitrary but self-consistent within this file

# ES at lat=0, lon=0: ECEF = [RE, 0, 0].
# ENU there: East=[0,1,0], North=[0,0,1], Up=[1,0,0] (same convention as
# tests/test_geometry_ref_vec.py).
ES = np.array([RE, 0.0, 0.0])


def _sat_at_elevation(el_deg: float, r_km: float = 1000.0) -> np.ndarray:
    """A satellite due North of the ES (az=0) at the given elevation."""
    el = math.radians(el_deg)
    return np.array([RE + r_km * math.sin(el), 0.0, r_km * math.cos(el)])


def _positions(elevations_per_sat: list[float]) -> np.ndarray:
    return np.array([_sat_at_elevation(e) for e in elevations_per_sat])


def _mock_lookahead(steps: list[list[float]], n_sat: int):
    """Build (propagate_mock, alpha_mock) for a T_steps-long lookahead.

    ``steps[s]`` is the list of per-satellite elevations (degrees) at future
    step ``s``. The alpha filter is bypassed (always "outside the GSO arc").
    """
    pos_side_effect = [
        (_positions(el_list), np.zeros((n_sat, 3))) for el_list in steps
    ]
    alpha_side_effect = [
        (np.full(n_sat, 90.0), None) for _ in steps
    ]
    return pos_side_effect, alpha_side_effect


def _run_selection(steps, n_sat, **kwargs):
    pos_se, alpha_se = _mock_lookahead(steps, n_sat)
    defaults = dict(
        constellation=[object()] * n_sat,  # unused: propagate_and_to_ecef_batch is mocked
        t_s=0.0,
        tstep_s=1.0,
        sin_min_el=math.sin(math.radians(10.0)),
        alpha0_deg=0.0,
        ref_az_deg=0.0,
        ref_el_deg=90.0,
        raan_dot_artificial_rad_s=0.0,
        raan_dot_override_rad_s=None,
        wdelta_deg=0.0,
        t_run_s=0.0,
    )
    defaults.update(kwargs)
    with patch("src.epfd_calculator.propagate_and_to_ecef_batch", side_effect=pos_se), \
         patch("src.epfd_calculator.compute_alpha_and_optimal_gso_fixed_es_batch", side_effect=alpha_se):
        return _get_lowest_avg_ref_vec_separation(
            es_ecef=ES, es_lat_deg=0.0, es_lon_deg=0.0,
            T_steps=len(steps), **defaults,
        )


# ── Eligibility must hold for the WHOLE T-step window ──────────────────────

def test_dip_below_min_elevation_at_any_step_excludes_for_whole_window():
    # sat0: 45 deg the whole window -> eligible.
    # sat1: 45,45,45 but dips to 5deg (< 10deg min) at step 1 -> excluded entirely,
    #       even though it's fine at steps 0 and 2.
    # sat2: below horizon (-10deg) at every step -> excluded.
    steps = [
        [45.0, 45.0, -10.0],
        [45.0,  5.0, -10.0],
        [45.0, 45.0, -10.0],
    ]
    result = _run_selection(
        steps, n_sat=3, M=3, max_co_freq=2, wcg_ref_sat_idx=0,
    )
    assert result == [0]


def test_no_eligible_satellite_returns_empty_list():
    steps = [[-10.0, -10.0]]  # both below horizon
    result = _run_selection(
        steps, n_sat=2, M=1, max_co_freq=2, wcg_ref_sat_idx=0,
    )
    assert result == []


# ── WCG reference satellite carve-out (opt-in; NOT in the US proposal) ──────
# Default behaviour is now the pure 4A/519 ranking: the WCG-origin satellite
# gets no special treatment (with Nco=1 the old always-on force-inclusion
# replaced the ranking entirely whenever that satellite was eligible).

def test_wcg_ref_satellite_not_forced_by_default():
    # sat0 is the WCG ref satellite and the WORST ranked; default (no force)
    # must select purely by ranking.
    steps = [[10.0, 45.0, 80.0]]
    result = _run_selection(
        steps, n_sat=3, M=1, max_co_freq=2, wcg_ref_sat_idx=0,
    )
    assert result == [2, 1]


def test_wcg_ref_satellite_forced_first_when_opted_in():
    # sat0: el=10 (separation 80 deg, worst) -- but it's the WCG ref satellite.
    # sat1: el=45 (separation 45 deg).
    # sat2: el=80 (separation 10 deg, best).
    steps = [[10.0, 45.0, 80.0]]
    result = _run_selection(
        steps, n_sat=3, M=1, max_co_freq=2, wcg_ref_sat_idx=0,
        force_wcg_sat=True,
    )
    # ref satellite (0) forced in first, remaining single slot goes to the
    # best-ranked of the others (2), not sat1.
    assert result == [0, 2]


def test_wcg_ref_satellite_skipped_when_ineligible():
    # sat0 is the WCG ref satellite but never visible -> must not appear,
    # and must not consume a slot (even with the force-inclusion opted in).
    steps = [[-10.0, 45.0, 80.0]]
    result = _run_selection(
        steps, n_sat=3, M=1, max_co_freq=1, wcg_ref_sat_idx=0,
        force_wcg_sat=True,
    )
    assert result == [2]


# ── M-worst-then-average ranking ────────────────────────────────────────────

def test_worst_m_then_average_prefers_the_steadier_satellite():
    # sat A elevations -> separations from zenith [10,12,30,11,9]; worst-3 avg = 17.67
    # sat B elevations -> separations from zenith [10,11, 9,10,11]; worst-3 avg = 10.67
    # sat C: always below horizon (used as an ineligible wcg_ref so it doesn't
    # force a slot, leaving the outcome to be decided purely by ranking).
    el_A = [90.0 - s for s in (10, 12, 30, 11, 9)]
    el_B = [90.0 - s for s in (10, 11, 9, 10, 11)]
    el_C = [-10.0] * 5
    steps = [[el_A[s], el_B[s], el_C[s]] for s in range(5)]
    result = _run_selection(
        steps, n_sat=3, M=3, max_co_freq=1, wcg_ref_sat_idx=2,
    )
    assert result == [1]  # B (steadier) beats A (one bad excursion)


# ── Degenerate case: zenith V, T_steps=1, M=1 == highest elevation ──────────

def test_zenith_single_step_selects_highest_elevation():
    # A 5th, always-below-horizon satellite is used as the (ineligible) WCG
    # ref satellite so the carve-out doesn't force a slot, isolating pure
    # ranking-by-separation-from-zenith among satellites 0-3.
    elevations = [10.0, 50.0, 30.0, 70.0, -10.0]
    steps = [elevations]
    result = _run_selection(
        steps, n_sat=5, M=1, max_co_freq=1, wcg_ref_sat_idx=4,
    )
    assert result == [3]  # 70 deg is the highest elevation / smallest separation


# ── _compute_epfd_for_selected_sats ─────────────────────────────────────────

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


def test_empty_selection_returns_sentinel():
    epfd_db, min_alpha, n_contrib = _compute_epfd_for_selected_sats(
        selected_idx=[],
        pos_ecef_all=np.zeros((0, 3)), vel_ecef_all=np.zeros((0, 3)),
        es_ecef=ES, es_lat_deg=0.0, es_lon_deg=0.0,
        gso_ecef=np.zeros(3), pfd_mask=_alpha_mask(),
        es_antenna=ITURS1428Antenna(1.2, 12.0, 0.65),
        pfd_bw_correction_db=0.0, t_s=0.0,
    )
    assert epfd_db == -999.0
    assert min_alpha == 180.0
    assert n_contrib == 0


def test_duplicate_satellite_doubles_linear_power():
    # ES, NGSO sat (zenith, 1000km alt) and GSO sat (same longitude) are
    # exactly colinear from Earth's centre -> off-axis angle = alpha = 0,
    # a fully deterministic geometry.
    es_ecef = lla_to_ecef(0.0, 0.0, 0.0)
    sat_ecef = lla_to_ecef(0.0, 0.0, 1000.0)
    gso_ecef = gso_position_ecef(0.0, 0.0)
    pos = np.array([sat_ecef])
    vel = np.zeros((1, 3))
    ant = ITURS1428Antenna(1.2, 12.0, 0.65)
    mask = _alpha_mask()

    epfd_single, alpha_single, n_single = _compute_epfd_for_selected_sats(
        selected_idx=[0], pos_ecef_all=pos, vel_ecef_all=vel,
        es_ecef=es_ecef, es_lat_deg=0.0, es_lon_deg=0.0,
        gso_ecef=gso_ecef, pfd_mask=mask, es_antenna=ant,
        pfd_bw_correction_db=0.0, t_s=0.0,
    )
    epfd_double, alpha_double, n_double = _compute_epfd_for_selected_sats(
        selected_idx=[0, 0], pos_ecef_all=pos, vel_ecef_all=vel,
        es_ecef=es_ecef, es_lat_deg=0.0, es_lon_deg=0.0,
        gso_ecef=gso_ecef, pfd_mask=mask, es_antenna=ant,
        pfd_bw_correction_db=0.0, t_s=0.0,
    )

    assert n_single == 1
    assert n_double == 2
    assert math.isfinite(epfd_single) and epfd_single > -999.0
    assert abs(alpha_single) < 1e-6
    assert abs(alpha_double) < 1e-6
    # Same satellite counted twice -> linear power doubles -> +10*log10(2) dB.
    assert abs(epfd_double - (epfd_single + 10.0 * math.log10(2.0))) < 1e-6


# ── run_epfd_simulation: hold_counter reselects every T_steps ──────────────

def _constellation(n=20, seed=0):
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


def test_hold_counter_reselects_every_window():
    """run_epfd_simulation_ref_vec's hold-and-reselect cadence: one call to
    _get_lowest_avg_ref_vec_separation per MIN_DURATION-derived window
    (N_SW), not one per step — the previous selection really is held for
    the whole window, not recomputed every step. Duration now comes solely
    from MIN_DURATION/N_SW (see
    artifacts/WP4A_519_track_duration_consolidation_decision.md), so the
    windows are built via compute_track_duration_windows instead of a
    standalone T parameter."""
    ant = ITURS1428Antenna(1.2, 12.0, 0.65)
    nsteps = 12
    windows = compute_track_duration_windows(
        min_duration_s=5.0, t_fine_s=1.0, nsteps=nsteps,
        min_orbital_period_s=6000.0, n_satellites=60, single_set=True,
    )
    assert windows.n_sw == 5  # sanity: same T_steps=5 as the pre-consolidation test
    with patch(
        "src.epfd_calculator._get_lowest_avg_ref_vec_separation",
        return_value=[0],
    ) as mock_sel:
        run_epfd_simulation_ref_vec(
            constellation=_constellation(), wcg=_wcg(), pfd_mask=_alpha_mask(),
            es_antenna=ant, alpha0_deg=2.0, min_elevation_deg=10.0,
            windows=windows, n_jobs=1, pfd_bw_correction_db=0.0,
            ref_vec_az_deg=0.0, ref_vec_el_deg=90.0, ref_vec_time_window_P_pct=100.0,
            wcg_ref_sat_idx=0,
        )
    # 12 steps, N_SW=5 -> windows starting at steps 0, 5, 10: 3 calls,
    # not 12 (i.e. the previous selection really is held, not recomputed
    # every step).
    assert mock_sel.call_count == 3
