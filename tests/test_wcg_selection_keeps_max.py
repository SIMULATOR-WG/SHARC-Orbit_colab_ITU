# -*- coding: utf-8 -*-
"""Does the WCGA really keep the worst (highest) geometry?

Prompted by a report that the "Why this WCG?" panel showed the WCG star up to
7.5 dB ABOVE the plotted per-latitude curve, which looked like the search
saving the wrong point. It was not: the stored curve is a 1-in-N sample that
dropped the winning latitude, while the winner and the tie statistics come
from the full profile. These tests pin the selection contract so a real
regression is distinguishable from that display artefact.

The contract (S.1503-4 D3.1.2) is deliberately NOT "highest EPFD wins":
among geometries whose EPFD is the same, the worst is the one with the lowest
apparent angular velocity, because it persists longest. "The same" needs a
tolerance, and that is _WCGState.BIN_SIZE.

Runs under pytest or directly:
    python tests/test_wcg_selection_keeps_max.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.wcg_search import _WCGState, WCGResult  # type: ignore[import]


def _res(epfd: float) -> WCGResult:
    return WCGResult(
        theta_deg=0.0, phi_deg=0.0, es_lat_deg=0.0, es_lon_deg=0.0,
        gso_lon_deg=0.0, es_lat_nominal=0.0, es_lon_nominal=0.0,
        gso_lon_nominal=0.0, alpha_deg=0.0, offaxis_deg=0.0, pfd_dBW=0.0,
        es_gain_rel_dB=0.0, epfd_dBW=epfd, elevation_deg=10.0,
        angular_velocity_deg_s=0.0, ref_sat_eci=None,
    )


def _feed(points):
    st = _WCGState()
    for epfd, ang_vel in points:
        st.update(epfd, ang_vel, _res(epfd))
    return st


def test_a_clear_peak_wins_whenever_it_arrives():
    """A point well above the running best must reset the bin and win, even
    with the worst angular velocity in the set, and regardless of order."""
    late = _feed([(-170.0, 0.01), (-169.0, 0.02), (-160.0, 9.99)])
    early = _feed([(-160.0, 9.99), (-169.0, 0.02), (-170.0, 0.01)])
    assert late.best_epfd == -160.0, late.best_epfd
    assert early.best_epfd == -160.0, early.best_epfd


def test_inside_the_tie_window_the_slowest_wins():
    """This is the Recommendation's rule, not a bug: 0.09 dB below the peak
    but far slower is the worse geometry."""
    st = _feed([(-160.00, 5.0), (-160.09, 0.1)])
    assert st.best_epfd == -160.09, st.best_epfd
    assert st.bin_top_margin == -160.00, st.bin_top_margin


def test_outside_the_tie_window_a_slow_point_is_rejected():
    """0.2 dB is not 'the same EPFD', however slowly the satellite moves."""
    st = _feed([(-160.00, 5.0), (-160.20, 0.001)])
    assert st.best_epfd == -160.0, st.best_epfd


def test_winner_is_never_more_than_the_tie_window_below_the_peak():
    """The invariant that matters for trusting a reported WCG."""
    pts = [(-170.0, 1.0), (-165.0, 0.5), (-164.95, 0.4), (-160.0, 3.0),
           (-160.05, 2.0), (-160.5, 0.01), (-175.0, 0.001)]
    st = _feed(pts)
    peak = max(e for e, _ in pts)
    assert st.bin_top_margin == peak, (st.bin_top_margin, peak)
    assert peak - st.best_epfd <= _WCGState.BIN_SIZE + 1e-9, (
        f"winner {st.best_epfd} is more than {_WCGState.BIN_SIZE} dB "
        f"below the peak {peak}")


def test_tie_window_is_the_documented_width():
    """If this changes, every stored wcg_explanation's tie count changes with
    it — the panel reads the window from the run, so keep them in step."""
    assert _WCGState.BIN_SIZE == 0.1



# ─── the merge/cap ordering defect ──────────────────────────────────────────
# Found from real logs: orbits reporting "no valid geometry found" after
# admitting ~10^9 points. Capping the candidate list by angular velocity BEFORE
# pruning it to the margin window could discard the very candidate that had
# just raised the window, then drop every survivor for being outside it.


def _fill_to_cap(state, margin=-171.60, base_av=0.001):
    """Saturate the candidate list inside one tight, slow, low-margin bin."""
    for k in range(_WCGState.BIN_CAND_MAX):
        state.update(margin - (k % 90) * 0.001, base_av + k * 1e-7,
                     _res(margin))
    return state


def test_merging_a_better_but_faster_candidate_keeps_it():
    acc = _fill_to_cap(_WCGState())
    assert len(acc._bin_candidates) == _WCGState.BIN_CAND_MAX
    incoming = _WCGState()
    incoming.update(-162.75, 0.90, _res(-162.75))   # far better, but fast
    acc.merge(incoming)
    assert acc.best_result is not None, (
        "merge dropped the only candidate in the new window — the search would "
        "report 'no valid geometry found' with ~10^9 admitted points")
    assert acc.best_epfd == -162.75, acc.best_epfd
    assert acc.bin_top_margin == -162.75


def test_update_keeps_a_candidate_that_raises_the_top_on_a_full_list():
    st = _fill_to_cap(_WCGState())
    st.update(-171.55, 9.99, _res(-171.55))   # raises the top, fastest of all
    assert st.best_result is not None, "full list + new top emptied the bin"
    assert st.bin_top_margin == -171.55
    # D3.1.2 still governs the choice: slowest inside the window wins.
    assert st.best_ang_vel <= 9.99


def test_a_winner_survives_however_many_partials_are_merged():
    """The real search merges one partial per swept latitude — thousands."""
    acc = _WCGState()
    for _ in range(40):
        acc.merge(_fill_to_cap(_WCGState()))
    winner = _WCGState()
    winner.update(-150.00, 5.0, _res(-150.00))
    acc.merge(winner)
    assert acc.best_result is not None and acc.best_epfd == -150.00


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as exc:
            fails += 1
            print(f"  FAIL  {name}: {exc}")
    print(f"\n{'all passed' if not fails else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)
