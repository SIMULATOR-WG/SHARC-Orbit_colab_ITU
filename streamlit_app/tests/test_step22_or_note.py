"""§D5.1.4.1 Step 22 / §D5.1.4.2 Step 20 and their shared Note.

Printed p. 99 (§D5.1.4.1) and p. 100 (§D5.1.4.2) state the gain branch without
any qualifier — "Repeat Step 23 for those satellites for which GRX(φ) >
min[Gmax − 30 dB, GRX(α₀[Latitude])]" — and the Note removes from it only the
satellites that are already counted: "if a satellite is on the list of the
highest MAX_CO_FREQ[lat] satellites and also for which GRX(φ) > min[…] it
should be included only once as part of the MAX_CO_FREQ[lat] satellites".

So a satellite that met α₀/ε₀ but lost the Step-20 ranking is NOT on that list,
and it contributes through the gain branch. Both algorithms must read it the
same way, otherwise the N_SW = 1 degeneracy between them is a coincidence of
the fixture rather than a property.
"""
from __future__ import annotations

import numpy as np

from src.epfd_calculator import (  # type: ignore[import]
    _finalize_epfd_after_max_co_freq,
    _or_from_capped,
    _process_closed_window,
    _window_aggregate_dense,
)
from src.epfd_stream_accumulator import EPFDWindowStats  # type: ignore[import]


def _es():
    return np.array([6378.0, 0.0, 0.0])


def _pos(n):
    # Positions are used only by the MIN_ANGLE_AT_ES pruning, inert here.
    return np.tile(np.array([[7000.0, 0.0, 0.0]]), (n, 1))


def test_capped_standard_satellite_enters_through_the_gain_branch():
    """Standard path: the cap keeps 1 of 2; the dropped one is in the gain cone."""
    standard = [(3.0, 1), (1.5, 2)]          # sat 2 loses the Step-20 ranking
    std_or = [(1.5, 2)]                      # ... and satisfies the gain condition
    std, ov = _finalize_epfd_after_max_co_freq(
        standard, [0.5], max_co_freq=1, strict_max_co_freq_total=False,
        min_angle_at_es_deg=0.0, es_ecef=_es(), pos_ecef_all=_pos(4),
        std_or_items=std_or,
    )
    assert std == [3.0], std                 # the tracked set is the top one
    assert sorted(ov) == [0.5, 1.5], ov      # gain-only sat + the capped one
    assert sum(std) + sum(ov) == 5.0


def test_the_note_counts_a_tracked_satellite_only_once():
    """A satellite both tracked and in the gain cone contributes once."""
    standard = [(3.0, 1), (1.5, 2)]
    std_or = [(3.0, 1), (1.5, 2)]            # both satisfy the gain condition
    std, ov = _finalize_epfd_after_max_co_freq(
        standard, [], max_co_freq=1, strict_max_co_freq_total=False,
        min_angle_at_es_deg=0.0, es_ecef=_es(), pos_ecef_all=_pos(4),
        std_or_items=std_or,
    )
    assert std == [3.0]
    assert ov == [1.5], ov                   # sat 1 is NOT added a second time
    assert sum(std) + sum(ov) == 4.5


def test_or_from_capped_drops_exactly_the_selected_indices():
    assert _or_from_capped([(1.0, 1), (2.0, 2), (3.0, 3)], {2}) == [1.0, 3.0]
    assert _or_from_capped(None, {1}) == []
    assert _or_from_capped([(1.0, 1)], {1}) == []


def _window_with_capped_gain_sat():
    """Window where sat 2 is eligible, capped by MAX_CO_FREQ=1, and gain-lit."""
    N_SAT, N_SW = 4, 3
    E = np.zeros((N_SW, N_SAT)); S = np.zeros((N_SW, N_SAT), bool)
    O = np.zeros((N_SW, N_SAT), bool)
    for g in range(N_SW):
        E[g, 1], S[g, 1] = 3.0, True                  # tracked (highest peak)
        E[g, 2], S[g, 2], O[g, 2] = 1.5, True, True   # eligible, capped, gain-lit
        E[g, 3], O[g, 3] = 0.5, True                  # gain-only
    buffer = []
    for g in range(N_SW):
        idx = np.array([1, 2, 3], dtype=np.int64)
        buffer.append((g, idx, E[g, idx].copy(), S[g, idx].copy(), O[g, idx].copy()))
    return E, S, O, buffer


def test_windowed_default_follows_the_printed_note():
    """§D5.1.4.2 Step 20: default must admit the capped-but-gain-lit satellite."""
    E, S, O, buffer = _window_with_capped_gain_sat()
    agg = _window_aggregate_dense(E, S, O, 1)         # default reading
    assert np.allclose(agg, 3.0 + 1.5 + 0.5), agg
    # The study switch reproduces the earlier, narrower reading.
    narrow = _window_aggregate_dense(E, S, O, 1, or_rescues_capped=False)
    assert np.allclose(narrow, 3.0 + 0.5), narrow
    assert 10.0 * np.log10(agg[0] / narrow[0]) > 1.0


def test_windowed_scalar_and_dense_agree_on_the_default():
    E, S, O, buffer = _window_with_capped_gain_sat()
    rec = EPFDWindowStats()
    _process_closed_window(buffer=buffer, max_co_freq=1, stats_end_step=10**9,
                           t_fine_s=1.0, win_stats=rec)
    dense = _window_aggregate_dense(E, S, O, 1)
    assert abs(rec.epfd_max_db - 10.0 * np.log10(float(dense[0]))) < 1e-12
