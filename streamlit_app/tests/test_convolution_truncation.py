"""S.1588 Annex 1 §1 low-probability tail truncation + dB-domain convolution.

The convolution drives the high-power tail to extremely low, unreliable
probabilities; the truncation drops every output point exceeded for less than
a given "shortest percentage of the time".
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.s1588_studies.convolution import convolve_ccdfs_db  # noqa: E402
from streamlit_app.lib.job_runners import s1588_worker as worker  # noqa: E402

_A = ([-150.0, -160.0, -170.0], [0.1, 5.0, 100.0])
_B = ([-150.0, -160.0, -170.0], [0.1, 5.0, 100.0])


def test_truncation_removes_low_pct_tail():
    full_b, full_p = convolve_ccdfs_db([_A, _B])
    tr_b, tr_p = convolve_ccdfs_db([_A, _B], truncate_tail_pct=0.1)
    # every surviving point is exceeded for >= the floor % of the time
    assert (tr_p >= 0.1 - 1e-9).all()
    # the unreliable ~0 % end is gone; fewer points than the full curve
    assert tr_p.min() > full_p.min()
    assert len(tr_p) < len(full_p)
    # dropping the high-power/low-% tail lowers the reported peak epfd
    assert tr_b[0] <= full_b[0] + 1e-9


def test_disabled_keeps_full_tail():
    full_b, full_p = convolve_ccdfs_db([_A, _B])
    none_b, none_p = convolve_ccdfs_db([_A, _B], truncate_tail_pct=None)
    # None must be a no-op: identical curve to the default call
    assert np.array_equal(none_b, full_b)
    assert np.array_equal(none_p, full_p)
    # the untruncated curve reaches the unreliable near-0 % end
    # (both -150 peaks coincide: P = 0.001² → 1e-4 %)
    assert none_p.min() < 0.1


def test_floor_selection_logic():
    ccdfs = [([-150, -160], [0.2, 100.0]), ([-150, -160], [0.05, 100.0])]
    # disabled
    assert worker._truncation_floor({}, ccdfs) is None
    # explicit value ignored while the toggle is off
    assert worker._truncation_floor({"truncate_tail_pct": 0.5}, ccdfs) is None
    # auto: smallest positive % across inputs
    assert worker._truncation_floor({"truncate_tail": True}, ccdfs) == 0.05
    # explicit wins when valid
    assert worker._truncation_floor(
        {"truncate_tail": True, "truncate_tail_pct": 1.0}, ccdfs
    ) == 1.0
    # malformed explicit falls back to auto
    assert worker._truncation_floor(
        {"truncate_tail": True, "truncate_tail_pct": "x"}, ccdfs
    ) == 0.05


def test_mass_conservation():
    # inputs reaching 100 %: the aggregate CCDF must also top out at 100 %
    _, pct = convolve_ccdfs_db([_A, _B])
    assert abs(pct.max() - 100.0) < 1e-9
    # inputs with "no signal" time (max pct < 100): zero-power masses combine
    # as p0_agg = p0_a * p0_b -> aggregate tops out at (1 - 0.2 * 0.2) = 96 %
    a = ([-150.0, -160.0], [0.1, 80.0])
    _, pct2 = convolve_ccdfs_db([a, a])
    assert abs(pct2.max() - 96.0) < 1e-9


def test_two_constant_systems_sum_to_3db():
    # two systems pinned at -160 dB -> aggregate constant at
    # 10*log10(2e-16) = -156.9897 dB (within one 0.1 dB grid bin)
    const = ([-160.0, -161.0], [100.0, 100.0])
    bins, pct = convolve_ccdfs_db([const, const])
    level_100 = bins[np.argmax(pct >= 100.0 - 1e-9)]
    assert abs(level_100 - (-156.9897)) < 0.06
