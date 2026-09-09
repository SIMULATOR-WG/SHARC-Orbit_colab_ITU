"""Tests for the S.1503-4 §D5.1.4.2 track-duration (sliding-window) variant.

Covers the §D5.1.3 window-parameter math, the slim per-window accumulator, and
the windowed engine — including the two properties the feature must guarantee:

  * **Degeneracy**: with MIN_DURATION = T_fine (one-step windows) and unlimited
    MAX_CO_FREQ, the windowed algorithm reduces exactly to the standard
    §D5.1.4.1 fixed-step run — the envelope CCDF must match bit-for-bit.
  * **Standalone ≡ cluster**: parallelism is over independent window sets, so a
    run with ``n_jobs=1`` and one fanned out over a process Pool (the same task
    decomposition the Ray executor uses) produce identical statistics.
"""
from __future__ import annotations

import math

import numpy as np

from src.epfd_stream_accumulator import EPFDWindowStats  # type: ignore[import]
from src.orbit_propagator import OrbitalElements  # type: ignore[import]
from src.wcg_search import WCGResult, lla_to_ecef  # type: ignore[import]
from src.antenna import ITURS1428Antenna  # type: ignore[import]
from src.pfd_mask import PFDMaskXML  # type: ignore[import]
from src.time_step import (  # type: ignore[import]
    compute_track_duration_windows,
    TrackDurationWindows,
)
from src.epfd_calculator import (  # type: ignore[import]
    run_epfd_simulation,
    run_epfd_simulation_windowed,
    run_epfd_simulation_ref_vec,
)


# ── shared tiny fixture (mirrors test_dual_step_counts.py) ───────────────────

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


def _constellation(n=300, seed=1):
    """A constellation dense enough that satellites actually contribute EPFD at
    the equatorial ES of ``_wcg`` — otherwise every CCDF assertion below would
    compare empty-vs-empty and validate nothing. 300 sats over i∈[30°,80°] with
    min_elevation ≈ 5° gives up to ~9 simultaneous contributors, so the
    MAX_CO_FREQ cap and the per-window selection are genuinely exercised."""
    rng = np.random.RandomState(seed)
    return [
        OrbitalElements(
            a=7178.0, e=0.0, i=math.radians(rng.uniform(30, 80)),
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


def _ant():
    return ITURS1428Antenna(1.2, 12.0, 0.65)


# ── §D5.1.3 window-parameter math ────────────────────────────────────────────

def test_window_params_formulas():
    # T_fine=1s, MIN_DURATION=10s → N_SW=10. MST=max(1, orb/(100·Nsat)).
    orb = 6000.0
    nsat = 60
    w = compute_track_duration_windows(
        min_duration_s=10.0, t_fine_s=1.0, nsteps=120,
        min_orbital_period_s=orb, n_satellites=nsat,
    )
    mst = max(1.0, orb / (100.0 * nsat))          # = 1.0 s here
    assert w.n_sw == 10
    assert w.n_msl == math.ceil(mst / 1.0)         # ⌈1.0⌉ = 1
    assert w.n_tw == math.ceil(w.n_sw / w.n_msl)
    assert w.n_repeat == math.ceil(120 / w.n_sw)
    assert w.n_total_steps == w.n_repeat * w.n_sw + (w.n_tw - 1) * w.n_msl
    assert w.n_steps_stats == 120
    assert abs(w.min_duration_s - w.n_sw * w.t_fine_s) < 1e-12


def test_window_params_min_sliding_time_floor():
    # Large constellation drives orb/(100·Nsat) below 1s → MST floored at 1s.
    w = compute_track_duration_windows(
        min_duration_s=30.0, t_fine_s=0.5, nsteps=1000,
        min_orbital_period_s=6000.0, n_satellites=5000,
    )
    assert w.min_sliding_time_s >= 1.0 - 1e-9   # floored at 1 s
    assert w.n_sw == 60                          # ⌊30/0.5⌋
    assert w.n_msl == math.ceil(1.0 / 0.5)       # ⌈1/0.5⌉ = 2


def test_single_set_forces_one_window_set():
    """``single_set=True`` (used by reference-vector selection, see
    artifacts/WP4A_519_track_duration_consolidation_decision.md) skips the
    MIN_SLIDING_TIME formula entirely: N_MSL == N_SW and N_TW == 1,
    regardless of orbital period / satellite count."""
    w_multi = compute_track_duration_windows(
        min_duration_s=10.0, t_fine_s=1.0, nsteps=120,
        min_orbital_period_s=6000.0, n_satellites=60,
    )
    assert w_multi.n_tw > 1  # the non-single_set case is a genuine multi-set here

    w_single = compute_track_duration_windows(
        min_duration_s=10.0, t_fine_s=1.0, nsteps=120,
        min_orbital_period_s=6000.0, n_satellites=60, single_set=True,
    )
    assert w_single.n_sw == w_multi.n_sw == 10  # same hold window either way
    assert w_single.n_tw == 1
    assert w_single.n_msl == w_single.n_sw
    assert w_single.n_repeat == w_multi.n_repeat
    assert w_single.n_total_steps == w_single.n_repeat * w_single.n_sw


def test_window_range_helpers():
    w = compute_track_duration_windows(
        min_duration_s=10.0, t_fine_s=1.0, nsteps=100,
        min_orbital_period_s=6000.0, n_satellites=60,
    )
    for ws in range(w.n_tw):
        s0, s1 = w.set_sim_range(ws)
        assert s0 == ws * w.n_msl
        assert s1 == s0 + w.n_repeat * w.n_sw
        st0, st1 = w.set_stats_range(ws)
        assert st1 - st0 == w.n_steps_stats
        # A window closes exactly at each multiple of N_SW past the set start.
        assert w.window_closes_at(ws, s0 + w.n_sw)
        assert not w.window_closes_at(ws, s0 + w.n_sw - 1)


# ── slim per-window accumulator ──────────────────────────────────────────────

def test_window_stats_add_merge_ccdf():
    a = EPFDWindowStats()
    for t in range(10):
        a.add(time_s=t, epfd_db=-150.0 + t, duration_s=1.0)
    a.add(time_s=99, epfd_db=-999.0, duration_s=1.0)  # null step: time only
    assert a.n_steps == 11
    assert a.n_steps_valid == 10
    assert abs(a.total_duration_s - 11.0) < 1e-9
    assert abs(a.epfd_max_db - (-141.0)) < 1e-9

    b = EPFDWindowStats()
    b.add(time_s=5, epfd_db=-140.0, duration_s=1.0)
    a.merge(b)
    assert a.n_steps == 12
    assert abs(a.epfd_max_db - (-140.0)) < 1e-9

    bins, pct = a.build_ccdf()
    assert bins.size == pct.size > 0
    # Descending levels, non-decreasing cumulative %.
    assert np.all(np.diff(bins) < 0)
    assert np.all(np.diff(pct) >= -1e-9)


# ── degeneracy: windowed(N_SW=1) ≡ standard fixed-step run ───────────────────

def test_one_step_window_matches_standard_fixed_run():
    ant = _ant()
    const = _constellation()
    wcg = _wcg()
    mask = _alpha_mask()
    nsteps = 150
    common = dict(
        constellation=const, wcg=wcg, pfd_mask=mask, es_antenna=ant,
        alpha0_deg=2.0, min_elevation_deg=5.0, pfd_bw_correction_db=0.0,
    )
    # Reference: standard fixed-step §D5.1.4.1 (no dual step, unlimited MAX_CO_FREQ).
    ref = run_epfd_simulation(tstep_s=1.0, nsteps=nsteps, dual_ts=None, n_jobs=1, **common)

    # NON-VACUITY GUARD: the fixture MUST produce real EPFD contributions, else
    # every CCDF comparison below is empty-vs-empty and validates nothing (this
    # exact vacuity previously hid the dense-envelope bug this test now catches).
    assert ref.acc.n_steps_valid > 0, "fixture produced no EPFD — test would be vacuous"
    assert len(ref.cdf_epfd_dBW) > 5

    # MIN_DURATION = T_fine ⇒ N_SW = 1 (one-step windows). Unlimited MAX_CO_FREQ,
    # min_angle_at_es=0 ⇒ per-step selection = every eligible + OR sat, identical
    # to the standard aggregation.
    windows = compute_track_duration_windows(
        min_duration_s=1.0, t_fine_s=1.0, nsteps=nsteps,
        min_orbital_period_s=6000.0, n_satellites=len(const),
    )
    assert windows.n_sw == 1
    win = run_epfd_simulation_windowed(windows=windows, n_jobs=1, **common)

    assert windows.n_tw == 1
    # Envelope CCDF must be the sparse occupied-bins layout (NOT a dense grid
    # down to −350 dBW) and bit-identical to the standard run.
    assert np.array_equal(win.cdf_epfd_dBW, ref.cdf_epfd_dBW)
    assert np.allclose(win.cdf_percentage, ref.cdf_percentage, atol=1e-9)


def test_one_step_window_matches_standard_with_max_co_freq_cap():
    # With a finite MAX_CO_FREQ, a window-eligible satellite dropped by the cap
    # must NOT re-enter via the OR branch — so windowed(N_SW=1, cap=K) must still
    # match the standard §D5.1.4.1 run with the same cap. Locks the Step-22
    # no-re-entry rule (_process_closed_window: `orx and k not in eligible`).
    ant = _ant()
    const = _constellation()
    cap = [(-90.0, 90.0, 3)]  # MAX_CO_FREQ = 3 co-frequency sats
    nsteps = 150
    common = dict(
        constellation=const, wcg=_wcg(), pfd_mask=_alpha_mask(), es_antenna=ant,
        alpha0_deg=2.0, min_elevation_deg=5.0, pfd_bw_correction_db=0.0,
        max_co_freq_by_lat=cap,
    )
    ref = run_epfd_simulation(tstep_s=1.0, nsteps=nsteps, dual_ts=None, n_jobs=1, **common)
    windows = compute_track_duration_windows(
        min_duration_s=1.0, t_fine_s=1.0, nsteps=nsteps,
        min_orbital_period_s=6000.0, n_satellites=len(const),
    )
    win = run_epfd_simulation_windowed(windows=windows, n_jobs=1, **common)
    assert np.array_equal(win.cdf_epfd_dBW, ref.cdf_epfd_dBW)
    assert np.allclose(win.cdf_percentage, ref.cdf_percentage, atol=1e-9)


# ── standalone ≡ cluster (fan-out over independent window sets) ──────────────

def test_windowed_standalone_equals_parallel():
    ant = _ant()
    const = _constellation()
    common = dict(
        constellation=const, wcg=_wcg(), pfd_mask=_alpha_mask(), es_antenna=ant,
        alpha0_deg=2.0, min_elevation_deg=5.0, pfd_bw_correction_db=0.0,
    )
    windows = compute_track_duration_windows(
        min_duration_s=10.0, t_fine_s=1.0, nsteps=120,
        min_orbital_period_s=6000.0, n_satellites=len(const),
    )
    assert windows.n_tw > 1  # a meaningful multi-set case

    seq = run_epfd_simulation_windowed(windows=windows, n_jobs=1, **common)
    par = run_epfd_simulation_windowed(windows=windows, n_jobs=4, **common)

    # Non-vacuity: the multi-set envelope must carry real mass.
    assert len(seq.cdf_epfd_dBW) > 5
    assert any(ws.n_steps_valid > 0 for ws in seq.window_stats)

    # Same number of window sets and a bit-identical envelope CDF.
    assert len(seq.window_stats) == len(par.window_stats) == windows.n_tw
    assert np.array_equal(seq.cdf_epfd_dBW, par.cdf_epfd_dBW)
    assert np.allclose(seq.cdf_percentage, par.cdf_percentage, atol=1e-12)
    # Per-window CCDFs match set-by-set too.
    for (b0, p0), (b1, p1) in zip(seq.per_window_ccdf, par.per_window_ccdf):
        assert np.array_equal(b0, b1)
        assert np.allclose(p0, p1, atol=1e-12)


def test_windowed_run_is_deterministic():
    ant = _ant()
    const = _constellation(seed=3)
    common = dict(
        constellation=const, wcg=_wcg(), pfd_mask=_alpha_mask(), es_antenna=ant,
        alpha0_deg=2.0, min_elevation_deg=5.0, pfd_bw_correction_db=0.0,
    )
    windows = compute_track_duration_windows(
        min_duration_s=8.0, t_fine_s=1.0, nsteps=80,
        min_orbital_period_s=6000.0, n_satellites=len(const),
    )
    r1 = run_epfd_simulation_windowed(windows=windows, n_jobs=1, **common)
    r2 = run_epfd_simulation_windowed(windows=windows, n_jobs=1, **common)
    assert np.array_equal(r1.cdf_epfd_dBW, r2.cdf_epfd_dBW)
    assert np.allclose(r1.cdf_percentage, r2.cdf_percentage, atol=1e-12)


# ── run_epfd_simulation_ref_vec (US proposal R23-WP4A-C-0519, consolidated
#    onto this same MIN_DURATION/N_SW windowing — see
#    artifacts/WP4A_519_track_duration_consolidation_decision.md) ────────────

def test_ref_vec_windowed_standalone_equals_parallel():
    ant = _ant()
    const = _constellation()
    common = dict(
        constellation=const, wcg=_wcg(), pfd_mask=_alpha_mask(), es_antenna=ant,
        alpha0_deg=2.0, min_elevation_deg=5.0, pfd_bw_correction_db=0.0,
        ref_vec_az_deg=0.0, ref_vec_el_deg=90.0, ref_vec_time_window_P_pct=100.0,
        wcg_ref_sat_idx=0,
        # _get_lowest_avg_ref_vec_separation treats max_co_freq<=0 as "select
        # nothing" (unlike _resolve_max_co_freq's 0=unlimited convention used
        # elsewhere) — pass an explicit cap, matching every other ref-vec test.
        max_co_freq_by_lat=[(-90.0, 90.0, 5)],
    )
    windows = compute_track_duration_windows(
        min_duration_s=10.0, t_fine_s=1.0, nsteps=120,
        min_orbital_period_s=6000.0, n_satellites=len(const), single_set=True,
    )
    assert windows.n_tw == 1  # reference-vector mode never uses the N_TW envelope

    seq = run_epfd_simulation_ref_vec(windows=windows, n_jobs=1, **common)
    par = run_epfd_simulation_ref_vec(windows=windows, n_jobs=4, **common)

    # Non-vacuity: real contributing satellites, not empty-vs-empty.
    assert seq.acc.n_steps_valid > 0
    assert len(seq.cdf_epfd_dBW) > 0

    assert np.array_equal(seq.cdf_epfd_dBW, par.cdf_epfd_dBW)
    assert np.allclose(seq.cdf_percentage, par.cdf_percentage, atol=1e-12)

    def to_set(acc):
        s = set()
        for t, idxs, elevs in zip(acc.decim_t_s, acc.decim_contrib_sat_idx, acc.decim_contrib_elev_deg):
            for i, e in zip(idxs, elevs):
                s.add((round(float(t), 6), int(i), round(float(e), 6)))
        return s

    seq_set, par_set = to_set(seq.acc), to_set(par.acc)
    assert len(seq_set) > 0
    assert seq_set == par_set


def test_ref_vec_windowed_run_is_deterministic():
    ant = _ant()
    const = _constellation(seed=3)
    common = dict(
        constellation=const, wcg=_wcg(), pfd_mask=_alpha_mask(), es_antenna=ant,
        alpha0_deg=2.0, min_elevation_deg=5.0, pfd_bw_correction_db=0.0,
        ref_vec_az_deg=0.0, ref_vec_el_deg=90.0, ref_vec_time_window_P_pct=100.0,
        wcg_ref_sat_idx=0,
        # _get_lowest_avg_ref_vec_separation treats max_co_freq<=0 as "select
        # nothing" (unlike _resolve_max_co_freq's 0=unlimited convention used
        # elsewhere) — pass an explicit cap, matching every other ref-vec test.
        max_co_freq_by_lat=[(-90.0, 90.0, 5)],
    )
    windows = compute_track_duration_windows(
        min_duration_s=8.0, t_fine_s=1.0, nsteps=80,
        min_orbital_period_s=6000.0, n_satellites=len(const), single_set=True,
    )
    r1 = run_epfd_simulation_ref_vec(windows=windows, n_jobs=1, **common)
    r2 = run_epfd_simulation_ref_vec(windows=windows, n_jobs=1, **common)
    assert np.array_equal(r1.cdf_epfd_dBW, r2.cdf_epfd_dBW)
    assert np.allclose(r1.cdf_percentage, r2.cdf_percentage, atol=1e-12)


def test_run_epfd_simulation_ref_vec_rejects_multi_set_windows():
    windows = compute_track_duration_windows(
        min_duration_s=10.0, t_fine_s=1.0, nsteps=120,
        min_orbital_period_s=6000.0, n_satellites=60,
    )
    assert windows.n_tw > 1
    try:
        run_epfd_simulation_ref_vec(
            constellation=_constellation(), wcg=_wcg(), pfd_mask=_alpha_mask(),
            es_antenna=_ant(), alpha0_deg=2.0, min_elevation_deg=5.0,
            windows=windows, n_jobs=1, pfd_bw_correction_db=0.0,
        )
        assert False, "expected ValueError for a multi-set TrackDurationWindows"
    except ValueError:
        pass


# ── progress reporting (§D5.1.4.2 driver) ────────────────────────────────────

def _windowed_progress_lines(caplog, n_jobs: int) -> list[str]:
    """Run a small windowed sim and return its progress log lines."""
    import logging
    const = _constellation()
    common = dict(
        constellation=const, wcg=_wcg(), pfd_mask=_alpha_mask(), es_antenna=_ant(),
        alpha0_deg=2.0, min_elevation_deg=5.0, pfd_bw_correction_db=0.0,
    )
    windows = compute_track_duration_windows(
        min_duration_s=10.0, t_fine_s=1.0, nsteps=120,
        min_orbital_period_s=6000.0, n_satellites=len(const),
    )
    with caplog.at_level(logging.INFO, logger="src.epfd_calculator"):
        run_epfd_simulation_windowed(windows=windows, n_jobs=n_jobs, **common)
    return [r.getMessage() for r in caplog.records if "block " in r.getMessage()]


def test_windowed_emits_progress_reaching_100pct(caplog):
    """The windowed path must report progress like the standard path does."""
    lines = _windowed_progress_lines(caplog, n_jobs=1)
    assert lines, "windowed run emitted no progress lines"
    # Every block ticks once, and the last line closes the run at 100%.
    assert "100.00%" in lines[-1]
    assert lines[-1].rstrip().endswith(f"block {len(lines)}/{len(lines)}")
    # Percentage is monotonic non-decreasing.
    pcts = [float(m.split("%")[0].split()[-1]) for m in lines]
    assert pcts == sorted(pcts)
    assert pcts[-1] == 100.0


def test_windowed_progress_step_counts_are_exact(caplog):
    """`step done/total` must land exactly on the §D5.1.3 propagation total."""
    lines = _windowed_progress_lines(caplog, n_jobs=1)
    done, total = lines[-1].split("step ")[1].split()[0].split("/")
    # N_TW · N_Repeat · N_SW for the fixture above.
    windows = compute_track_duration_windows(
        min_duration_s=10.0, t_fine_s=1.0, nsteps=120,
        min_orbital_period_s=6000.0, n_satellites=300,
    )
    expected = windows.n_tw * windows.n_repeat * windows.n_sw
    assert int(total) == expected
    assert int(done) == expected  # no block unaccounted for


def test_windowed_progress_totals_match_across_dispatch_paths(caplog):
    """Block granularity tracks n_jobs, but the propagation total must not.

    ``win_per_block`` is sized from ``n_jobs`` (finer blocks fan out better), so
    the sequential and Pool runs legitimately tick a different number of times.
    What has to agree is the accounting: both close at 100% on the same total.
    """
    def _last(n_jobs):
        caplog.clear()
        lines = _windowed_progress_lines(caplog, n_jobs=n_jobs)
        done, total = lines[-1].split("step ")[1].split()[0].split("/")
        return len(lines), int(done), int(total)

    seq_blocks, seq_done, seq_total = _last(1)
    par_blocks, par_done, par_total = _last(4)

    assert (seq_done, seq_total) == (par_done, par_total)
    assert seq_done == seq_total  # both fully accounted for
    # Finer blocks with more jobs — the property that makes the fan-out work.
    assert par_blocks > seq_blocks
