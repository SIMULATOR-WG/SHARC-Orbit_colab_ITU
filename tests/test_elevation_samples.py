"""Tests for the default, decimated "elevation of EPFD-contributing
satellites" feature (contributing_sat_elevations.csv).

Covers three layers:
  1. The index-tracking refactor of ``_select_standard_epfd_s1503_steps_20_21``
     / ``_finalize_epfd_after_max_co_freq`` (they used to return bare
     ``list[float]``; they now also return which satellite each value came
     from) — direct, geometry-free unit tests with ``min_angle_at_es_deg=0``
     so ``es_ecef``/``pos_ecef_all`` are never touched.
  2. ``EPFDStreamAccumulator``'s new ``decim_contrib_sat_idx``/
     ``decim_contrib_elev_deg`` decimated fields: halving, merge, and
     ``finalize_decimated`` must keep them index-aligned with ``decim_t_s``
     exactly like the existing decimated fields.
  3. ``run_epfd_simulation`` end-to-end: recorded elevation must match the
     elevation recomputed independently from propagated positions, and
     n_jobs=1 vs n_jobs>1 (parallel chunk dispatch + accumulator merge) must
     produce identical decimated content.
"""
from __future__ import annotations

import math
import sys
import os

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.epfd_calculator import (  # noqa: E402
    _select_standard_epfd_s1503_steps_20_21,
    _finalize_epfd_after_max_co_freq,
    run_epfd_simulation,
)
from src.epfd_stream_accumulator import (  # noqa: E402
    EPFDStreamAccumulator,
    format_contributing_elevation_csv,
)
from src.orbit_propagator import OrbitalElements, propagate_and_to_ecef_batch  # noqa: E402
from src.wcg_search import WCGResult, lla_to_ecef  # noqa: E402
from src.antenna import ITURS1428Antenna  # noqa: E402
from src.pfd_mask import PFDMaskXML  # noqa: E402
from src.geometry import compute_elevation  # noqa: E402

_DUMMY_ES = np.zeros(3)
_DUMMY_POS = np.zeros((1, 3))


# ---------------------------------------------------------------------------
# 1. Index-tracking through Steps 19-22 selection (geometry-free)
# ---------------------------------------------------------------------------

def test_select_standard_no_angle_pruning_pairs_idx_with_value():
    items = [(5.0, 10), (8.0, 20), (3.0, 30), (9.0, 40)]
    out = _select_standard_epfd_s1503_steps_20_21(
        items, max_co_freq=2, min_angle_at_es_deg=0.0,
        es_ecef=_DUMMY_ES, pos_ecef_all=_DUMMY_POS,
    )
    assert out == [(9.0, 40), (8.0, 20)]


def test_select_standard_unlimited_preserves_input_order():
    items = [(5.0, 10), (8.0, 20), (3.0, 30), (9.0, 40)]
    out = _select_standard_epfd_s1503_steps_20_21(
        items, max_co_freq=0, min_angle_at_es_deg=0.0,
        es_ecef=_DUMMY_ES, pos_ecef_all=_DUMMY_POS,
    )
    assert out == items


def test_finalize_plain_branch_idx_alignment():
    standard_items = [(5.0, 10), (8.0, 20), (3.0, 30), (9.0, 40)]
    override_items = [(1.0, 100), (2.0, 200)]
    standard_epfd, override_epfd, standard_idx, override_idx = (
        _finalize_epfd_after_max_co_freq(
            standard_items, override_items, max_co_freq=2,
            strict_max_co_freq_total=False, min_angle_at_es_deg=0.0,
            es_ecef=_DUMMY_ES, pos_ecef_all=_DUMMY_POS,
        )
    )
    assert standard_epfd == [9.0, 8.0]
    assert standard_idx == [40, 20]
    assert override_epfd == [1.0, 2.0]
    assert override_idx == [100, 200]


def test_finalize_strict_max_co_freq_total_mixes_standard_and_override():
    standard_items = [(5.0, 10), (8.0, 20), (3.0, 30), (9.0, 40)]
    override_items = [(7.5, 100), (2.0, 200)]
    standard_epfd, override_epfd, standard_idx, override_idx = (
        _finalize_epfd_after_max_co_freq(
            standard_items, override_items, max_co_freq=3,
            strict_max_co_freq_total=True, min_angle_at_es_deg=0.0,
            es_ecef=_DUMMY_ES, pos_ecef_all=_DUMMY_POS,
        )
    )
    # Joint top-3 by value across standard+override: 9.0(std,40), 8.0(std,20),
    # 7.5(ov,100) — the two lower values (5.0, 3.0 standard; 2.0 override) drop.
    assert standard_epfd == [9.0, 8.0]
    assert standard_idx == [40, 20]
    assert override_epfd == [7.5]
    assert override_idx == [100]


def test_finalize_multi_system_partition_caps_each_system_independently():
    standard_items = [(5.0, 0), (8.0, 1), (3.0, 2), (9.0, 3)]
    override_items = [(1.0, 50), (2.0, 60)]
    system_id_all = np.array([0, 0, 1, 1])
    standard_epfd, override_epfd, standard_idx, override_idx = (
        _finalize_epfd_after_max_co_freq(
            standard_items, override_items, max_co_freq=1,
            strict_max_co_freq_total=False, min_angle_at_es_deg=0.0,
            es_ecef=_DUMMY_ES, pos_ecef_all=_DUMMY_POS,
            system_id_all=system_id_all, max_co_freq_by_system={0: 1, 1: 1},
        )
    )
    # System 0 (sat idx 0,1): best is (8.0, 1). System 1 (sat idx 2,3): best is (9.0, 3).
    assert standard_epfd == [8.0, 9.0]
    assert standard_idx == [1, 3]
    # Multi-system mode keeps the OR branch intact (uncapped).
    assert override_epfd == [1.0, 2.0]
    assert override_idx == [50, 60]


# ---------------------------------------------------------------------------
# 2. EPFDStreamAccumulator decimated contributing-satellite fields
# ---------------------------------------------------------------------------

def test_decim_consider_defaults_to_empty_arrays_when_not_supplied():
    acc = EPFDStreamAccumulator()
    acc.add(time_s=0.0, epfd_db=-160.0, duration_s=1.0, num_horizon_sats=1,
            num_visible_sats=1, num_contributing_sats=0, min_alpha_deg=4.0)
    assert len(acc.decim_contrib_sat_idx) == 1
    assert acc.decim_contrib_sat_idx[0].size == 0
    assert acc.decim_contrib_elev_deg[0].size == 0


def test_decim_halve_keeps_contrib_arrays_aligned_with_t_s():
    acc = EPFDStreamAccumulator()
    acc.decim_capacity = 5  # force halving well before 10k points
    n = 25
    for k in range(n):
        acc.add(
            time_s=float(k), epfd_db=-160.0, duration_s=1.0,
            num_horizon_sats=1, num_visible_sats=1, num_contributing_sats=1,
            min_alpha_deg=4.0,
            contrib_sat_idx=np.array([k]), contrib_elev_deg=np.array([float(k)]),
        )
    assert len(acc.decim_t_s) == len(acc.decim_contrib_sat_idx) == len(acc.decim_contrib_elev_deg)
    assert acc.decim_stride > 1  # halving actually happened
    # Content must still correspond: sat_idx encodes the original time_s.
    for t, idx_arr, elev_arr in zip(acc.decim_t_s, acc.decim_contrib_sat_idx, acc.decim_contrib_elev_deg):
        assert idx_arr.tolist() == [int(t)]
        assert elev_arr.tolist() == [float(t)]


def test_merge_concatenates_contrib_arrays():
    acc1, acc2 = EPFDStreamAccumulator(), EPFDStreamAccumulator()
    acc1.add(time_s=0.0, epfd_db=-160.0, duration_s=1.0, num_horizon_sats=1,
              num_visible_sats=1, num_contributing_sats=1, min_alpha_deg=4.0,
              contrib_sat_idx=np.array([1]), contrib_elev_deg=np.array([30.0]))
    acc2.add(time_s=1.0, epfd_db=-161.0, duration_s=1.0, num_horizon_sats=1,
              num_visible_sats=1, num_contributing_sats=2, min_alpha_deg=4.0,
              contrib_sat_idx=np.array([2, 3]), contrib_elev_deg=np.array([40.0, 50.0]))
    acc1.merge(acc2)
    assert len(acc1.decim_t_s) == 2
    assert len(acc1.decim_contrib_sat_idx) == 2
    total_rows = sum(a.size for a in acc1.decim_contrib_sat_idx)
    assert total_rows == 3


def test_finalize_decimated_reorders_contrib_arrays_with_time():
    # Simulate out-of-order chunk arrival (acc2's step happened *before* acc1's).
    acc1, acc2 = EPFDStreamAccumulator(), EPFDStreamAccumulator()
    acc1.add(time_s=5.0, epfd_db=-160.0, duration_s=1.0, num_horizon_sats=1,
              num_visible_sats=1, num_contributing_sats=1, min_alpha_deg=4.0,
              contrib_sat_idx=np.array([99]), contrib_elev_deg=np.array([5.0]))
    acc2.add(time_s=1.0, epfd_db=-161.0, duration_s=1.0, num_horizon_sats=1,
              num_visible_sats=1, num_contributing_sats=1, min_alpha_deg=4.0,
              contrib_sat_idx=np.array([1]), contrib_elev_deg=np.array([1.0]))
    acc1.merge(acc2)
    assert acc1.decim_t_s == [5.0, 1.0]  # merge just concatenates, unordered
    acc1.finalize_decimated()
    assert acc1.decim_t_s == [1.0, 5.0]
    assert acc1.decim_contrib_sat_idx[0].tolist() == [1]
    assert acc1.decim_contrib_sat_idx[1].tolist() == [99]
    assert acc1.decim_contrib_elev_deg[0].tolist() == [1.0]
    assert acc1.decim_contrib_elev_deg[1].tolist() == [5.0]


def test_format_contributing_elevation_csv_empty_and_content():
    assert format_contributing_elevation_csv(EPFDStreamAccumulator()) is None

    acc = EPFDStreamAccumulator()
    acc.add(time_s=0.0, epfd_db=-160.0, duration_s=1.0, num_horizon_sats=1,
            num_visible_sats=1, num_contributing_sats=2, min_alpha_deg=4.0,
            contrib_sat_idx=np.array([1, 2]), contrib_elev_deg=np.array([30.0, 45.0]))
    csv_text = format_contributing_elevation_csv(acc)
    lines = [l for l in csv_text.splitlines() if not l.startswith("#")]
    assert lines[0] == "t_s,sat_idx,elevation_deg"
    assert lines[1:] == ["0,1,30.0000", "0,2,45.0000"]


# ---------------------------------------------------------------------------
# 3. run_epfd_simulation end-to-end
# ---------------------------------------------------------------------------

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


def _constellation(n=60, seed=0):
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


def _run(n_jobs: int, nsteps: int = 1200, tstep_s: float = 60.0):
    ant = ITURS1428Antenna(1.2, 12.0, 0.65)
    return run_epfd_simulation(
        constellation=_constellation(), wcg=_wcg(), pfd_mask=_alpha_mask(),
        es_antenna=ant, alpha0_deg=2.0, min_elevation_deg=10.0,
        tstep_s=tstep_s, nsteps=nsteps, dual_ts=None, n_jobs=n_jobs,
        pfd_bw_correction_db=0.0,
    )


def test_default_run_populates_decimated_contrib_elevation():
    res = _run(n_jobs=1)
    acc = res.acc
    assert len(acc.decim_t_s) == len(acc.decim_contrib_sat_idx) == len(acc.decim_contrib_elev_deg)
    total_rows = sum(a.size for a in acc.decim_contrib_sat_idx)
    assert total_rows > 0

    const = _constellation()
    es_ecef = lla_to_ecef(0.0, 0.0, 0.0)
    checked = 0
    for t_s, idx_arr, elev_arr in zip(acc.decim_t_s, acc.decim_contrib_sat_idx, acc.decim_contrib_elev_deg):
        if idx_arr.size == 0 or checked >= 5:
            continue
        pos_all, _ = propagate_and_to_ecef_batch(const, t_s)
        for sat_idx, elev_deg in zip(idx_arr, elev_arr):
            expected = compute_elevation(es_ecef, pos_all[sat_idx], 0.0, 0.0)
            assert abs(float(elev_deg) - expected) < 1e-6
        checked += 1
    assert checked > 0


def test_parallel_dispatch_matches_sequential():
    res_seq = _run(n_jobs=1)
    res_par = _run(n_jobs=2)

    def to_set(acc):
        s = set()
        for t, idxs, elevs in zip(acc.decim_t_s, acc.decim_contrib_sat_idx, acc.decim_contrib_elev_deg):
            for i, e in zip(idxs, elevs):
                s.add((round(float(t), 3), int(i), round(float(e), 3)))
        return s

    s1, s2 = to_set(res_seq.acc), to_set(res_par.acc)
    assert len(s1) > 0
    assert s1 == s2
