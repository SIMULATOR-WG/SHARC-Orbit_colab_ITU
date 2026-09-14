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
import pytest

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


# ── conformant single pass ≡ legacy per-set dispatch ─────────────────────────

def _sp_common(n=300, seed=1, cap=None):
    common = dict(
        constellation=_constellation(n=n, seed=seed), wcg=_wcg(), pfd_mask=_alpha_mask(),
        es_antenna=_ant(), alpha0_deg=2.0, min_elevation_deg=5.0, pfd_bw_correction_db=0.0,
    )
    if cap is not None:
        common["max_co_freq_by_lat"] = cap
    return common


def _assert_same_windowed(a, b, ctx=""):
    """Per-set CCDFs, envelope and step accounting must agree.

    Bin levels are compared exactly. Percentages use a tight relative tolerance
    because the single-pass path accumulates a bin's weight as
    ``count * T_fine`` (``EPFDWindowStats.add_batch``) while the legacy path adds
    ``T_fine`` once per step, which can differ in the last ulp.
    """
    assert len(a.window_stats) == len(b.window_stats), f"n sets {ctx}"
    for i, (x, y) in enumerate(zip(a.window_stats, b.window_stats)):
        assert x.n_steps == y.n_steps, f"n_steps set {i} {ctx}"
        assert x.n_steps_valid == y.n_steps_valid, f"n_steps_valid set {i} {ctx}"
        assert abs(x.total_duration_s - y.total_duration_s) <= 1e-9 * max(1.0, y.total_duration_s)
        assert np.allclose(x.duration_per_bin, y.duration_per_bin, rtol=1e-12, atol=0.0), \
            f"hist set {i} {ctx}"
    for i, ((b0, p0), (b1, p1)) in enumerate(zip(a.per_window_ccdf, b.per_window_ccdf)):
        assert np.array_equal(b0, b1), f"ccdf bins set {i} {ctx}"
        assert np.allclose(p0, p1, rtol=1e-12, atol=0.0), f"ccdf pct set {i} {ctx}"
    assert np.array_equal(a.cdf_epfd_dBW, b.cdf_epfd_dBW), f"envelope bins {ctx}"
    assert np.allclose(a.cdf_percentage, b.cdf_percentage, rtol=1e-12, atol=0.0), \
        f"envelope pct {ctx}"


def test_single_pass_matches_legacy_no_cap():
    """§D5.1.3: N_TotalSteps is one timeline, not N_TW of them.

    The conformant single pass must reproduce the legacy per-set dispatch
    exactly — same eligibility (Step 19), ranking (Step 19bis), cap (Step 20),
    aggregate (Step 21) and run-duration truncation (Step 22).
    """
    common = _sp_common()
    windows = compute_track_duration_windows(
        min_duration_s=10.0, t_fine_s=1.0, nsteps=120,
        min_orbital_period_s=6000.0, n_satellites=len(common["constellation"]),
    )
    assert windows.n_tw > 1 and windows.n_sw > 1  # a meaningful multi-set case
    sp = run_epfd_simulation_windowed(windows=windows, n_jobs=1, single_pass=True, **common)
    lg = run_epfd_simulation_windowed(windows=windows, n_jobs=1, single_pass=False, **common)
    assert any(w.n_steps_valid > 0 for w in sp.window_stats), "fixture produced no EPFD"
    assert len(sp.cdf_epfd_dBW) > 5
    _assert_same_windowed(sp, lg, "no cap")


def test_single_pass_matches_legacy_with_cap_and_partial_window():
    """Same, with MAX_CO_FREQ biting and Nstep not a multiple of N_SW.

    ``nsteps=125`` with ``N_SW=7`` leaves the last window partly outside the run
    duration, which is the Step-22 tail rule; the cap forces the Step-19bis
    ranking (and therefore the tie-break) to matter.
    """
    common = _sp_common(seed=5, cap=[(-90.0, 90.0, 2)])
    windows = compute_track_duration_windows(
        min_duration_s=7.0, t_fine_s=1.0, nsteps=125,
        min_orbital_period_s=6000.0, n_satellites=len(common["constellation"]),
    )
    assert windows.n_sw == 7 and windows.n_repeat * windows.n_sw > 125
    sp = run_epfd_simulation_windowed(windows=windows, n_jobs=1, single_pass=True, **common)
    lg = run_epfd_simulation_windowed(windows=windows, n_jobs=1, single_pass=False, **common)
    assert any(w.n_steps_valid > 0 for w in sp.window_stats)
    for w in sp.window_stats:
        assert w.n_steps == 125, "each set's statistics must span exactly Nstep steps"
    _assert_same_windowed(sp, lg, "cap + partial window")


def test_single_pass_chunking_is_transparent():
    """The N_SW−1 halo must make chunk boundaries invisible."""
    common = _sp_common(seed=9)
    windows = compute_track_duration_windows(
        min_duration_s=8.0, t_fine_s=1.0, nsteps=90,
        min_orbital_period_s=6000.0, n_satellites=len(common["constellation"]),
    )
    seq = run_epfd_simulation_windowed(windows=windows, n_jobs=1, single_pass=True, **common)
    par = run_epfd_simulation_windowed(windows=windows, n_jobs=4, single_pass=True, **common)
    _assert_same_windowed(seq, par, "chunked")


def test_step19bis_tie_break_is_label_stable():
    """F06: the Step-19bis ranking must not depend on satellite labelling.

    The Recommendation states no tie-break for Step 19bis (and not even a sort
    direction), yet exact ties in window-peak epfd are common in practice —
    pfd-mask edge clamping and the antenna gain floor produce identical peaks.
    Ranking a ``set[int]`` by peak alone left the choice to hash-table slot
    order, so the same physics with a reindexed constellation kept a different
    satellite under the MAX_CO_FREQ cap. The total key ``(-peak, index)`` makes
    the choice deterministic and label-stable.

    Both satellites peak at 1.0 in step 0, so the cap must break a genuine tie;
    they differ in step 1, so the aggregate reveals which one was kept. Values
    follow the *label*, not the position, so re-ordering the arrays changes only
    the labelling.
    """
    from src.epfd_calculator import _process_closed_window  # type: ignore[import]
    from src.epfd_stream_accumulator import EPFDWindowStats  # type: ignore[import]

    VAL = {"lo": (1.0, 0.25), "hi": (1.0, 0.5)}  # (step 0, step 1)

    def aggregate(lo_label, hi_label, order):
        """Window with two tied satellites; ``order`` sets the array layout."""
        role = {lo_label: "lo", hi_label: "hi"}
        buf = []
        for step in (0, 1):
            idx = np.array(order, dtype=np.int64)
            ep = np.array([VAL[role[k]][step] for k in order], dtype=np.float64)
            flag = np.array([True, True])
            buf.append((step, idx, ep, flag, np.array([False, False])))
        ws = EPFDWindowStats()
        _process_closed_window(buffer=buf, max_co_freq=1, stats_end_step=10**9,
                               t_fine_s=1.0, win_stats=ws)
        return ws

    # 35 and 122 land in an ascending-order-defying hash slot order for a
    # Python set of small ints, which is exactly what used to decide the tie.
    for a, b in ((35, 122), (3, 5), (157, 213), (8, 16)):
        lo, hi = min(a, b), max(a, b)
        for order in ((a, b), (b, a)):
            ws = aggregate(lo, hi, order)
            # The lower index wins the tie, so step 1 contributes 0.25 (-6.02 dB),
            # never 0.5 (-3.01 dB).
            kept = ws.duration_per_bin[_bin(10.0 * math.log10(0.25))]
            dropped = ws.duration_per_bin[_bin(10.0 * math.log10(0.5))]
            assert kept == 1.0, f"labels {(lo, hi)} order {order}: lower index not kept"
            assert dropped == 0.0, f"labels {(lo, hi)} order {order}: higher index kept"


def _bin(value_db):
    from src.epfd_stream_accumulator import _bin_index  # type: ignore[import]
    return _bin_index(value_db)


def test_window_aggregate_dense_matches_scalar_exactly():
    """The vectorized window reduction must equal the scalar one, value by value.

    The engine-level A/B tests compare 0.1 dB-binned statistics, which absorb
    differences below ~1e-5 dB and cannot see a selection change that never
    crosses a bin edge. This locks the two implementations at full float
    precision on a window built to exercise every branch:

      * satellites tied on window peak exactly at the MAX_CO_FREQ boundary
        (Step 19bis tie-break),
      * an eligible satellite ranked below the cap (dropped, and under the
        project's Step-20 reading it must not re-enter via the gain branch),
      * a gain-only (OR) satellite that is never α₀/ε₀-standard,
      * a satellite that drops out of the recorded set mid-window, so it fails
        the Step-19 full-duration test.
    """
    from src.epfd_calculator import (  # type: ignore[import]
        _process_closed_window, _window_aggregate_dense,
    )
    from src.epfd_stream_accumulator import EPFDWindowStats  # type: ignore[import]

    N_SAT, N_SW = 12, 5
    rng = np.random.RandomState(7)

    # Sat 4 and sat 9: eligible, tied peak. Sat 2: eligible, lower peak.
    # Sat 7: OR-only. Sat 11: standard except at step 2 (fails Step 19).
    idx_by_step, ep_by_step, std_by_step, orx_by_step = [], [], [], []
    for step in range(N_SW):
        present = [2, 4, 7, 9] + ([11] if True else [])
        present = sorted(present + [11])
        ep, std, orx = [], [], []
        for k in present:
            if k == 4:
                v = 1.0 if step == 0 else 0.1 + 0.01 * step
                std.append(True); orx.append(False)
            elif k == 9:
                v = 1.0 if step == 0 else 0.2 + 0.01 * step   # tied peak with 4
                std.append(True); orx.append(False)
            elif k == 2:
                v = 0.5 + 0.001 * step
                std.append(True); orx.append(True)            # eligible AND in gain cone
            elif k == 7:
                v = 0.05 + 0.001 * step
                std.append(False); orx.append(True)           # OR-only
            else:  # k == 11
                v = 0.3
                std.append(step != 2); orx.append(False)      # breaks full-duration
            ep.append(v)
        idx_by_step.append(np.array(present, dtype=np.int64))
        ep_by_step.append(np.array(ep, dtype=np.float64))
        std_by_step.append(np.array(std, dtype=bool))
        orx_by_step.append(np.array(orx, dtype=bool))

    buffer = [
        (g, idx_by_step[g], ep_by_step[g], std_by_step[g], orx_by_step[g])
        for g in range(N_SW)
    ]

    E = np.zeros((N_SW, N_SAT)); S = np.zeros((N_SW, N_SAT), bool); O = np.zeros((N_SW, N_SAT), bool)
    for g in range(N_SW):
        E[g, idx_by_step[g]] = ep_by_step[g]
        S[g, idx_by_step[g]] = std_by_step[g]
        O[g, idx_by_step[g]] = orx_by_step[g]

    class _Recorder(EPFDWindowStats):
        def __init__(self):
            super().__init__()
            self.seen = []
        def add(self, time_s, epfd_db, duration_s):  # noqa: D102
            self.seen.append(epfd_db)
            super().add(time_s=time_s, epfd_db=epfd_db, duration_s=duration_s)

    for cap in (0, 1, 2, 3, 5):
        rec = _Recorder()
        _process_closed_window(buffer=buffer, max_co_freq=cap, stats_end_step=10**9,
                               t_fine_s=1.0, win_stats=rec)
        scalar = np.array(rec.seen, dtype=np.float64)

        agg = _window_aggregate_dense(E, S, O, cap)
        with np.errstate(divide="ignore"):
            dense = np.where(agg > 0.0, 10.0 * np.log10(agg), -999.0)

        assert dense.shape == scalar.shape, f"cap={cap}"
        # Not bit-exact by construction: the scalar path adds the contributing
        # satellites sequentially in ascending index order, while numpy sums the
        # dense row pairwise. Measured worst case here is 10 ulp (1.6e-15
        # relative), i.e. ~1e-15 dB against a 0.1 dB bin — fourteen orders of
        # magnitude below anything the statistics can resolve. A selection
        # difference, by contrast, moves the value by whole dB and is caught.
        assert np.allclose(dense, scalar, rtol=1e-13, atol=0.0), (
            f"cap={cap}: dense {dense} vs scalar {scalar}"
        )

    # Non-vacuity: the cap must actually bite, and the tie must actually occur.
    elig = S.all(axis=0)
    assert set(np.flatnonzero(elig)) == {2, 4, 9}, "expected exactly three eligible sats"
    assert E[:, 4].max() == E[:, 9].max(), "fixture lost the peak tie"


# ── single-pass chunk sizing (halo budget) ───────────────────────────────────

def test_singlepass_chunks_cover_the_timeline_exactly():
    from src.epfd_calculator import _singlepass_chunks  # type: ignore[import]

    for n_total, n_sw, n_jobs in ((101_759, 960, 8), (1_842_499, 1250, 128),
                                  (4_006_654, 6956, 8), (2000, 960, 8), (500, 10, 4)):
        ch = _singlepass_chunks(n_total, n_sw, n_jobs)
        assert ch[0][0] == 0 and ch[-1][1] == n_total
        for (a, b), (c, _d) in zip(ch, ch[1:]):
            assert b == c, "chunks must be contiguous"
            assert b > a, "chunks must be non-empty"


def test_singlepass_chunking_respects_the_halo_budget():
    """Each chunk re-propagates N_SW − 1 steps, so chunk count is not free.

    The work per fine step is uniform, so the wall time on ``n_jobs`` workers is
    minimised at exactly ``n_jobs`` chunks; oversubscribing only multiplies the
    halo. The previous rule targeted 4×n_jobs chunks with a 4·N_SW floor, which
    on a real run (N_TotalSteps 101 759, N_SW 960, 8 jobs) produced 27 chunks and
    wasted 24.5% of the propagation budget on halos.
    """
    from src.epfd_calculator import _singlepass_chunks  # type: ignore[import]

    n_total, n_sw, n_jobs = 101_759, 960, 8
    ch = _singlepass_chunks(n_total, n_sw, n_jobs)
    halo = sum(min(a, n_sw - 1) for a, _ in ch)
    assert len(ch) == n_jobs, "one chunk per worker is the optimum for uniform work"
    assert halo / n_total < 0.10, f"halo overhead {halo / n_total:.1%} too high"

    # A cluster-scale split pays more halo, but still bounded.
    ch2 = _singlepass_chunks(1_842_499, 1250, 128)
    halo2 = sum(min(a, 1249) for a, _ in ch2)
    assert len(ch2) == 128
    assert halo2 / 1_842_499 < 0.10


def test_singlepass_chunking_collapses_when_halo_would_dominate():
    """A run barely longer than one window must not be split at all."""
    from src.epfd_calculator import _singlepass_chunks  # type: ignore[import]

    assert _singlepass_chunks(2000, 960, 8) == [(0, 2000)]      # halo 959 per chunk
    assert _singlepass_chunks(101_759, 960, 1) == [(0, 101_759)]
    # Just above the ratio threshold it may split, but never so far that a chunk
    # carries less real work than its own halo.
    for n_total in (10_000, 50_000, 200_000):
        ch = _singlepass_chunks(n_total, 960, 8)
        for a, b in ch:
            if a:  # the first chunk has no halo
                assert (b - a) >= (960 - 1), "chunk smaller than its own halo"


# ── §D5.1.4.2 Step 20: the two readings of the gain branch ───────────────────

def _or_branch_window():
    """One window where a capped-but-eligible satellite is also in the gain cone.

    Sat 1 and sat 2 are α₀/ε₀-standard through the whole window (Step-19
    eligible); Step 19bis ranks by window peak, so with MAX_CO_FREQ = 1 sat 1
    (peak 3.0) is kept and sat 2 (peak 1.5) is dropped. Sat 2 also carries the
    Step-18 gain flag, so the two readings of Step 20 disagree about it by a
    known amount. Sat 3 is gain-only and enters under both readings.
    """
    N_SAT, N_SW = 4, 3
    E = np.zeros((N_SW, N_SAT)); S = np.zeros((N_SW, N_SAT), bool)
    O = np.zeros((N_SW, N_SAT), bool)
    for g in range(N_SW):
        E[g, 1], S[g, 1] = 3.0, True            # highest peak → kept by the cap
        E[g, 2], S[g, 2], O[g, 2] = 1.5, True, True   # eligible, capped, in gain cone
        E[g, 3], O[g, 3] = 0.5, True            # gain-only
    buffer = []
    for g in range(N_SW):
        idx = np.array([1, 2, 3], dtype=np.int64)
        buffer.append((g, idx, E[g, idx].copy(), S[g, idx].copy(), O[g, idx].copy()))
    return E, S, O, buffer


def test_step20_or_branch_readings_differ_by_the_capped_satellite():
    """B07: both readings are implemented and differ by exactly the capped sat.

    The printed Step 20 does not say whether a window-eligible satellite that
    Step 19bis dropped under MAX_CO_FREQ may re-enter through the gain ("OR")
    branch. Default (``or_rescues_capped=False``) keeps it out, so the cap is
    binding; ``True`` lets it back in and is the more conservative reading. The
    gap here is exact and known: 3.5 → 5.0 linear = 1.549 dB.
    """
    from src.epfd_calculator import _window_aggregate_dense  # type: ignore[import]

    E, S, O, _buf = _or_branch_window()
    strict = _window_aggregate_dense(E, S, O, 1, or_rescues_capped=False)
    loose = _window_aggregate_dense(E, S, O, 1, or_rescues_capped=True)
    assert np.allclose(strict, 3.0 + 0.5), strict          # sat 1 + gain-only sat 3
    assert np.allclose(loose, 3.0 + 1.5 + 0.5), loose      # + the capped sat 2
    gap_db = 10.0 * np.log10(loose[0] / strict[0])
    assert abs(gap_db - 10.0 * math.log10(5.0 / 3.5)) < 1e-12, gap_db
    assert abs(gap_db - 1.5490196) < 1e-6, gap_db


def test_step20_or_branch_readings_agree_between_dense_and_scalar():
    """Both readings must be the same in the vectorized and the scalar path."""
    from src.epfd_calculator import (  # type: ignore[import]
        _process_closed_window, _window_aggregate_dense,
    )

    E, S, O, buffer = _or_branch_window()
    for flag in (False, True):
        rec = EPFDWindowStats()
        _process_closed_window(buffer=buffer, max_co_freq=1, stats_end_step=10**9,
                               t_fine_s=1.0, win_stats=rec, or_rescues_capped=flag)
        dense = _window_aggregate_dense(E, S, O, 1, or_rescues_capped=flag)
        expected_db = 10.0 * math.log10(float(dense[0]))
        assert abs(rec.epfd_max_db - expected_db) < 1e-12, flag


# ── §D5.1.4.2 Step 19 diagnostics (empty tracked set) ────────────────────────

def test_empty_tracked_set_is_counted_not_silent():
    """B09: a window with no eligible satellite must be tallied.

    With no satellite α₀/ε₀-standard through the whole window, Step 19 yields
    nothing and the aggregate falls back to the gain branch alone — strongly
    anti-conservative, and indistinguishable from a clean low-EPFD result
    unless it is counted.
    """
    from src.epfd_calculator import _process_closed_window  # type: ignore[import]

    N_SW = 4
    buffer = []
    for g in range(N_SW):
        idx = np.array([1, 2], dtype=np.int64)
        ep = np.array([1.0, 2.0])
        std = np.array([g != 1, g != 2])    # each sat breaks at a different step
        orx = np.array([False, False])
        buffer.append((g, idx, ep, std, orx))

    rec = EPFDWindowStats()
    _process_closed_window(buffer=buffer, max_co_freq=0, stats_end_step=10**9,
                           t_fine_s=1.0, win_stats=rec)
    assert rec.n_windows == 1
    assert rec.n_windows_no_eligible == 1
    assert rec.empty_window_fraction == 1.0
    assert rec.epfd_max_db == -999.0 or rec.n_steps_valid == 0


def test_window_diagnostics_are_reported_by_the_engine():
    """The tallies must survive the merge and reach the result object."""
    common = _sp_common(seed=3, cap=[(-90.0, 90.0, 2)])
    windows = compute_track_duration_windows(
        min_duration_s=6.0, t_fine_s=1.0, nsteps=60,
        min_orbital_period_s=6000.0, n_satellites=len(common["constellation"]),
    )
    res = run_epfd_simulation_windowed(windows=windows, n_jobs=1, **common)
    d = res.window_diagnostics
    assert d["n_windows_closed"] > 0
    assert 0.0 <= d["empty_window_fraction"] <= 1.0
    assert len(d["per_set_empty_fraction"]) == len(res.window_stats)
    # Every closed window is accounted for in exactly one bucket or the other.
    assert d["n_windows_no_eligible"] <= d["n_windows_closed"]
    assert d["n_windows_capped"] <= d["n_windows_closed"]
    assert sum(w.n_windows for w in res.window_stats) == d["n_windows_closed"]


def test_window_diagnostics_survive_parallel_dispatch():
    """A-12: N_MSL > 1 (so N_TW > 1) over several chunks, tallies included."""
    common = _sp_common(seed=11, cap=[(-90.0, 90.0, 3)])
    # MIN_SLIDING_TIME = T_orb / (100 · n_sat) (§D5.1.3), so a long dimensioning
    # period is what makes N_MSL > 1: 90 000 / (100 · 300) = 3 s = 3 fine steps.
    windows = compute_track_duration_windows(
        min_duration_s=9.0, t_fine_s=1.0, nsteps=90,
        min_orbital_period_s=90_000.0, n_satellites=len(common["constellation"]),
    )
    assert (windows.n_msl, windows.n_tw) == (3, 3), (windows.n_msl, windows.n_tw)
    seq = run_epfd_simulation_windowed(windows=windows, n_jobs=1, **common)
    par = run_epfd_simulation_windowed(windows=windows, n_jobs=4, **common)
    _assert_same_windowed(seq, par, "diagnostics")
    assert par.window_diagnostics["n_windows_closed"] == \
        seq.window_diagnostics["n_windows_closed"]
    for x, y in zip(seq.window_stats, par.window_stats):
        assert x.n_windows == y.n_windows
        assert x.n_windows_no_eligible == y.n_windows_no_eligible
        assert x.n_windows_capped == y.n_windows_capped


# ── headline accumulator honesty (timeline vs window-set samples) ────────────

def test_headline_acc_reports_the_timeline_not_the_window_set():
    """C10: ``acc`` is one window set; the run extent is N_TotalSteps × T_fine."""
    common = _sp_common(seed=2)
    windows = compute_track_duration_windows(
        min_duration_s=8.0, t_fine_s=1.0, nsteps=80,
        min_orbital_period_s=6000.0, n_satellites=len(common["constellation"]),
    )
    res = run_epfd_simulation_windowed(windows=windows, n_jobs=1, **common)
    assert res.n_timeline_steps == windows.n_total_steps
    assert res.timeline_t_fine_s == windows.t_fine_s
    assert res.acc.first_time_s == 0.0
    assert res.acc.last_time_s == (windows.n_total_steps - 1) * windows.t_fine_s
    # The set's own sample count is N_Repeat × N_SW and is NOT the timeline.
    assert res.acc.n_steps == windows.n_steps_stats


def test_wdelta_ramp_spans_the_windowed_timeline():
    """A-07: §D6.3.4 ramps W_delta over the run the engine actually walks."""
    common = _sp_common(seed=4)
    windows = compute_track_duration_windows(
        min_duration_s=5.0, t_fine_s=1.0, nsteps=50,
        min_orbital_period_s=6000.0, n_satellites=len(common["constellation"]),
    )
    # t_run_s sized for the standard path (50 s) vs the windowed timeline.
    short = run_epfd_simulation_windowed(
        windows=windows, n_jobs=1, wdelta_deg=1.0, t_run_s=50.0, **common)
    exact = run_epfd_simulation_windowed(
        windows=windows, n_jobs=1, wdelta_deg=1.0,
        t_run_s=windows.t_total_duration_s, **common)
    # The engine retimes the ramp, so the mismatched t_run_s must not change it.
    assert np.array_equal(short.cdf_epfd_dBW, exact.cdf_epfd_dBW)
    assert np.allclose(short.cdf_percentage, exact.cdf_percentage, rtol=1e-12)


# ── §D5.1.4.2 Step 18 ② / Step 20: the gain (OR) branch must be live ─────────

def test_or_branch_actually_contributes_in_the_windowed_engine():
    """G-01: an engine configuration where the gain branch fires.

    The shared fixture (1.2 m dish, α₀ = 2°) never produces a satellite that is
    inside the exclusion zone with relative gain above min(−30 dB, G(α₀)), so
    every recorded satellite had ``orx = False`` and deleting the whole OR term
    from the reducer still passed every equivalence test. A wide beam (0.55 m,
    D/λ = 22) with α₀ = 20° puts satellites inside the zone whose gain keeps
    them in, so the OR term carries real EPFD.

    ``strict_exclusion_zone=True`` is exactly "no rescue by gain" (S.1503-2
    §5.1.4), so it is the reference: the two runs must differ, and the run with
    the OR branch must be the higher one. Deleting ``O & ~eligible`` from the
    reducer collapses the first run onto the second and fails this test.
    """
    common = dict(
        constellation=_constellation(n=300, seed=1), wcg=_wcg(),
        pfd_mask=_alpha_mask(), es_antenna=ITURS1428Antenna(0.55, 12.0, 0.65),
        alpha0_deg=20.0, min_elevation_deg=5.0, pfd_bw_correction_db=0.0,
        max_co_freq_by_lat=[(-90.0, 90.0, 2)],
    )
    windows = compute_track_duration_windows(
        min_duration_s=6.0, t_fine_s=1.0, nsteps=60,
        min_orbital_period_s=6000.0, n_satellites=300,
    )
    with_or = run_epfd_simulation_windowed(
        windows=windows, n_jobs=1, strict_exclusion_zone=False, **common)
    no_or = run_epfd_simulation_windowed(
        windows=windows, n_jobs=1, strict_exclusion_zone=True, **common)

    assert with_or.acc.n_steps_valid > 0 and no_or.acc.n_steps_valid > 0
    assert with_or.acc.epfd_max_db > no_or.acc.epfd_max_db + 1.0, (
        "the gain branch must add EPFD: "
        f"{with_or.acc.epfd_max_db} vs {no_or.acc.epfd_max_db}"
    )
    assert not (
        np.array_equal(with_or.cdf_epfd_dBW, no_or.cdf_epfd_dBW)
        and np.allclose(with_or.cdf_percentage, no_or.cdf_percentage)
    ), "OR branch made no difference — the fixture no longer exercises it"


# ── §D7.1.3 percentage columns and the 100 %-time row ────────────────────────

def test_cumulative_percentage_never_exceeds_100():
    """A share of the run cannot exceed 100 %, and a stray ulp decided a row.

    The last CCDF point is a cumulative sum divided by the same total, so it can
    land on 100.00000000000017. That value fails ``Py <= Pi`` on the 100 %-time
    row of §D7.1.3 and turned the row that carries no constraint into a Fail.
    """
    from src.epfd_stream_accumulator import (  # type: ignore[import]
        EPFDStreamAccumulator, EPFDWindowStats,
    )

    def _feed(acc, full):
        rng = np.random.RandomState(3)
        for i in range(400):
            db = -150.0 - rng.rand() * 40.0
            dur = float(rng.rand() + 0.1)
            if full:
                acc.add(time_s=float(i), epfd_db=db, duration_s=dur,
                        num_horizon_sats=1, num_visible_sats=1,
                        num_contributing_sats=1, min_alpha_deg=10.0)
            else:
                acc.add(time_s=float(i), epfd_db=db, duration_s=dur)

    for acc, full in ((EPFDStreamAccumulator(), True), (EPFDWindowStats(), False)):
        # Many unequal weights so the running sum cannot match the total exactly.
        _feed(acc, full)
        _bins, pct = acc.build_ccdf()
        assert pct.size > 0
        assert pct.max() <= 100.0, pct.max()
        assert pct[-1] == pytest.approx(100.0, abs=1e-9)


def test_hundred_percent_row_is_not_failed_by_rounding():
    """The vacuous row must pass whenever the curve simply covers the run."""
    from src.epfd_calculator import (  # type: ignore[import]
        EPFDSimulationResult, check_article22_compliance,
    )

    res = EPFDSimulationResult(wcg=_wcg())
    res.cdf_epfd_dBW = np.array([-164.0, -170.0, -187.4, -188.4])
    # Deliberately overshoot, as a real cumulative sum can.
    res.cdf_percentage = np.array([0.01, 5.0, 99.9, 100.00000000000017])
    comp = check_article22_compliance(
        sim_result=res, limits=[(-187.4, 100.0)], reference_bandwidth_khz=40.0)
    assert comp.table17[0]["pass"] is True, comp.table17
