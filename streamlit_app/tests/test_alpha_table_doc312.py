"""Conformance of the ``alpha_table`` selection with WP 4A Doc 4A/312.

Each test pins one clause of the document, cited by page of
``refs/R23-WP4A-C-0312!P1!MSW-E-1.pdf``:

  * p. 110 — the TSS table has ``n + 1`` cases delimited by the *declared*
    angles, with masses ``Nco·p_1``, ``Nco·(p_i − p_{i-1})``, ``Nco·(1 − p_n)``,
    and a final **unbounded** case ``α_n ≤ α``.
  * p. 111 — Step 20 picks the case with the highest TSS (ties: most satellites,
    then lowest α), then the highest single-entry ``epfd_i`` inside it, and
    decrements that case — including on the first-orbit WCG branch.
  * p. 16  — the canonical XML example, whose two declared tables start at
    *different* angles (min at 20°, max at 30°).
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from src.alpha_table import (  # type: ignore[import]
    build_tss_cases, generate_seven_tables, validate_alpha_pairs,
)
from src.tss_accumulator import TSSAccumulator  # type: ignore[import]

# Doc 4A/312 p. 16, verbatim.
DOC_MIN = [(20.0, 0.1), (30.0, 0.25), (40.0, 0.5), (50.0, 1.0)]
DOC_MAX = [(30.0, 0.1), (40.0, 0.25), (50.0, 0.5), (60.0, 1.0)]

EXPECTED_LABELS = ["min", "mid25", "mid50", "mid75", "max", "MinMax", "MaxMin"]


# --------------------------------------------------------------------------- #
#  p. 110 — case structure
# --------------------------------------------------------------------------- #

def test_cases_are_the_declared_intervals():
    """``n`` declared pairs ⇒ ``n + 1`` cases on the declared angles."""
    pairs = [(1.0, 0.02), (20.0, 0.20), (50.0, 0.60), (85.0, 0.95)]
    edges, masses = build_tss_cases(pairs)
    assert edges.tolist()[:-1] == [0.0, 1.0, 20.0, 50.0, 85.0]
    assert masses.size == len(pairs) + 1


def test_case_masses_follow_the_normative_formula():
    """mass_1 = p_1, mass_i = p_i − p_{i-1}, mass_{n+1} = 1 − p_n."""
    pairs = [(1.0, 0.02), (20.0, 0.20), (50.0, 0.60), (85.0, 0.95)]
    _, masses = build_tss_cases(pairs)
    assert masses == pytest.approx([0.02, 0.18, 0.40, 0.35, 0.05], abs=1e-12)
    assert masses.sum() == pytest.approx(1.0, abs=1e-12)


def test_last_case_is_unbounded():
    """``α_n ≤ α`` — every α above the last declared angle lands in the tail
    case and finds the whole ``(1 − p_n)`` mass there, however large α is."""
    pairs = [(1.0, 0.02), (20.0, 0.20), (50.0, 0.60), (85.0, 0.95)]
    edges, masses = build_tss_cases(pairs)
    assert math.isinf(edges[-1])

    tss = TSSAccumulator(bin_edges=edges, masses=masses, nco=2)
    tail = masses.size - 1
    for alpha in (85.0, 90.0, 120.0, 179.9):
        assert tss._bin_of(alpha) == tail, alpha
    tss.update()
    assert tss.credits[tail] == pytest.approx(2 * 0.05, abs=1e-12)


def test_declared_case_granularity_drives_step_20():
    """A wide declared case must compete with its *whole* mass.

    Doc p. 111 compares TSS per case, so the case holding 0.18 of the mass beats
    the one holding 0.02 — even though the first is 19° wide and the second 1°.
    Subdividing the wide case (the non-normative ``bin_deg``) would split its
    credit and invert this outcome, which is exactly why 0 is the default.
    """
    pairs = [(1.0, 0.02), (20.0, 0.20), (50.0, 0.60), (85.0, 0.95)]
    items = [(10.0, 0), (6.0, 1)]                  # sat 0 has the higher epfd…
    alpha_by_k = {0: 0.5, 1: 10.0}                 # …but sits in the small case

    edges, masses = build_tss_cases(pairs)         # normative
    tss = TSSAccumulator(bin_edges=edges, masses=masses, nco=1)
    tss.update()
    assert tss.select(items, alpha_by_k, 0.0) == [6.0]

    edges_b, masses_b = build_tss_cases(pairs, bin_deg=1.0)   # non-normative
    tss_b = TSSAccumulator(bin_edges=edges_b, masses=masses_b, nco=1)
    tss_b.update()
    assert tss_b.select(items, alpha_by_k, 0.0) == [10.0]


def test_sub_binning_preserves_per_case_totals():
    """``bin_deg`` only subdivides: each declared case keeps its exact mass."""
    pairs = [(1.0, 0.02), (20.0, 0.20), (50.0, 0.60), (85.0, 0.95)]
    edges, masses = build_tss_cases(pairs, bin_deg=1.0)
    assert masses.sum() == pytest.approx(1.0, abs=1e-12)
    for lo, hi, want in [(0, 1, 0.02), (1, 20, 0.18), (20, 50, 0.40),
                         (50, 85, 0.35)]:
        sel = (edges[:-1] >= lo) & (edges[:-1] < hi)
        assert masses[sel].sum() == pytest.approx(want, abs=1e-12), (lo, hi)
    assert math.isinf(edges[-1])          # the tail case is never subdivided


def test_zero_width_first_case_is_folded_forward():
    """A declared 0° angle would make case 1 = [0, 0): unreachable, so its
    credit could only grow. Its mass belongs to the next case instead."""
    edges, masses = build_tss_cases([(0.0, 0.10), (10.0, 0.60)])
    assert edges.tolist()[:-1] == [0.0, 10.0]
    assert masses == pytest.approx([0.60, 0.40], abs=1e-12)


# --------------------------------------------------------------------------- #
#  p. 111 — Step 20
# --------------------------------------------------------------------------- #

def test_first_orbit_wcg_pick_decrements_its_case():
    """The "Decrement the count of the TSS Counter of the selected case by one"
    bullet sits at the same level as the "first orbit" and "Otherwise" bullets,
    so it governs both branches."""
    edges, masses = build_tss_cases([(10.0, 0.5), (40.0, 0.9)])
    tss = TSSAccumulator(bin_edges=edges, masses=masses, nco=1,
                         wcg_ref_sat_idx=7, t_end_first_orbit=100.0)
    tss.update()
    before = tss.credits.copy()

    picked = tss.select([(1.0, 7), (9.0, 3)], {7: 5.0, 3: 50.0}, t_s=0.0)

    assert picked == [1.0]                          # the WCG satellite, not the max epfd
    assert tss.n_wcg_exception_hits == 1
    case_of_wcg = tss._bin_of(5.0)
    assert tss.credits[case_of_wcg] == pytest.approx(before[case_of_wcg] - 1.0)
    others = [i for i in range(masses.size) if i != case_of_wcg]
    assert tss.credits[others] == pytest.approx(before[others])


def test_first_orbit_decrement_scales_with_the_step_weight():
    """Under the dual step the spend follows the same ``w`` as the accrual."""
    edges, masses = build_tss_cases([(10.0, 0.5), (40.0, 0.9)])
    tss = TSSAccumulator(bin_edges=edges, masses=masses, nco=1,
                         wcg_ref_sat_idx=7, t_end_first_orbit=100.0)
    tss.update(weight=4.0)
    before = tss.credits.copy()
    tss.select([(1.0, 7)], {7: 5.0}, t_s=0.0, weight=4.0)
    case = tss._bin_of(5.0)
    assert tss.credits[case] == pytest.approx(before[case] - 4.0)


def test_step_20_tie_breaks_in_documented_order():
    """Highest TSS → most satellites → lowest α."""
    edges, masses = build_tss_cases([(10.0, 0.5), (20.0, 0.5), (40.0, 1.0)])
    tss = TSSAccumulator(bin_edges=edges, masses=masses, nco=1)
    tss.update()
    # Cases [0,10) and [10,20) both hold 0.5 of the mass — equal TSS. The first
    # has two candidates, so it wins on count even with the lower epfd.
    picked = tss.select(
        [(1.0, 0), (2.0, 1), (9.0, 2)],
        {0: 5.0, 1: 6.0, 2: 15.0},
        t_s=0.0,
    )
    assert picked == [2.0]


def test_pruning_never_touches_credit():
    """Step 21 acts on the candidate pool only (Doc p. 111 note)."""
    edges, masses = build_tss_cases([(10.0, 0.5), (40.0, 0.9)])
    tss = TSSAccumulator(bin_edges=edges, masses=masses, nco=3)
    tss.update()
    spent = []

    def prune(_sel_k, _remaining):
        return []                       # everything else fails MIN_ANGLE_AT_ES

    before = tss.credits.copy()
    picked = tss.select([(5.0, 0), (4.0, 1), (3.0, 2)], {0: 5.0, 1: 5.0, 2: 5.0},
                        t_s=0.0, prune_fn=prune)
    assert len(picked) == 1             # pool emptied before Nco was reached
    assert (before - tss.credits).sum() == pytest.approx(1.0)   # exactly one spend
    assert spent == []


# --------------------------------------------------------------------------- #
#  p. 16 — the canonical example
# --------------------------------------------------------------------------- #

def test_canonical_doc_example_produces_seven_usable_tables():
    """The declared tables start at different angles (20° vs 30°); all 7
    generated tables must be usable."""
    tables = generate_seven_tables(DOC_MIN, DOC_MAX)
    assert [label for label, _ in tables] == EXPECTED_LABELS
    for label, pairs in tables:
        validate_alpha_pairs(pairs, name=label)          # must not raise
        _, masses = build_tss_cases(pairs)
        assert masses.sum() == pytest.approx(1.0, abs=1e-12), label


def test_endpoint_tables_reproduce_the_declared_ones():
    """With ``p = 0`` pairs dropped, the envelope endpoints are exactly the
    declared tables — no phantom anchor below the first declared angle."""
    tables = dict(generate_seven_tables(DOC_MIN, DOC_MAX))
    assert tables["min"] == pytest.approx(DOC_MIN)
    assert tables["max"] == pytest.approx(DOC_MAX)


def test_interpolated_tables_reach_below_the_max_first_angle():
    """Dropping p=0 must not drop *legitimate* mass: a blend of min and max has
    non-zero probability at 20°, where only ``min`` is declared."""
    tables = dict(generate_seven_tables(DOC_MIN, DOC_MAX))
    for label in ("mid25", "mid50", "mid75"):
        first_angle = tables[label][0][0]
        assert first_angle == 20.0, label
        # 0.75·0.1 + 0.25·0 = 0.075 for mid25, etc.
        assert tables[label][0][1] > 0.0, label


def test_a_ccdf_is_still_rejected():
    """Guard kept from before: a decreasing table is an operator error."""
    with pytest.raises(ValueError, match="non-decreasing"):
        validate_alpha_pairs([(10.0, 0.9), (20.0, 0.5)])
