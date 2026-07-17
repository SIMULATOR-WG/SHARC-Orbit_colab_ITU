"""Aggregation CCDF envelope (method_2): worst epfd at EVERY observed
percentage — the envelope axis must be the union of the input curves'
percentage breakpoints, not a fixed resampled grid."""
from __future__ import annotations

from streamlit_app.lib.job_runners.s1588_worker import _ccdf_envelope


def test_envelope_keeps_every_breakpoint() -> None:
    # Two step-like CCDFs with tail structure far below 0.1% — a fixed
    # linspace(0.0001, 100, 1024) axis (~0.098% spacing) would sample this
    # region once and miss the crossover entirely.
    a = ([-160.0, -170.0, -180.0], [0.0005, 0.05, 99.0])
    b = ([-155.0, -175.0, -178.0], [0.0002, 0.02, 99.5])
    bins, pct = _ccdf_envelope([a, b])

    # Union of breakpoints, nothing lost, nothing invented.
    assert sorted(pct) == sorted([0.0002, 0.0005, 0.02, 0.05, 99.0, 99.5])

    by_pct = dict(zip(pct, bins))
    # At each native breakpoint the envelope is the max over both curves
    # (curve values via linear-power interpolation on the other's axis).
    assert by_pct[0.0002] == -155.0                     # only b reaches here (a clamps at -160)
    assert abs(by_pct[0.05] - (-170.0)) < 0.5           # a's own point, b already below
    assert by_pct[0.0005] > -160.0 - 1e-9 or abs(by_pct[0.0005] - (-155.0)) < 1.2
    # Monotone non-increasing epfd with increasing percentage.
    pairs = sorted(zip(pct, bins))
    for (p1, b1), (p2, b2) in zip(pairs, pairs[1:]):
        assert b2 <= b1 + 1e-9


def test_envelope_respects_truncation_floor() -> None:
    a = ([-160.0, -170.0], [0.0005, 50.0])
    bins, pct = _ccdf_envelope([a], pct_lo=0.01)
    assert min(pct) >= 0.01  # breakpoints below the floor dropped
    assert len(pct) == 1 and pct[0] == 50.0


def test_envelope_keeps_deep_tail() -> None:
    # No truncation floor → the envelope must reach the smallest observed
    # breakpoint (100·1/N of the longest run), with no arbitrary 0.0001% cut.
    a = ([-166.8, -170.0, -190.0], [1e-6, 0.01, 99.0])
    b = ([-167.5, -172.0, -191.0], [5e-5, 0.02, 99.5])
    bins, pct = _ccdf_envelope([a, b])
    assert min(pct) == 1e-6
    by_pct = dict(zip(pct, bins))
    assert by_pct[1e-6] == -166.8  # deep-tail max survives
    # Zero-exceedance entries never enter the axis.
    c = ([-160.0, -180.0], [0.0, 10.0])
    bins_c, pct_c = _ccdf_envelope([c])
    assert pct_c == [10.0]


def test_envelope_empty() -> None:
    assert _ccdf_envelope([]) == ([], [])
