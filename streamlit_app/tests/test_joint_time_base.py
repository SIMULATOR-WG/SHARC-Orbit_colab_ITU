"""Joint time base of a method_3 run: user inputs win, auto falls to the min.

``_run_method_3`` fuses N filings into one megaconstellation, so it needs ONE
timeline (N, Δt_fine, Δt_coarse). The rule these tests pin:

  * whatever the user supplied is used **as given** — in particular the
    iteration count N is the count of the JOINT run. It used to be applied per
    filing and then re-derived by the §D4.1 rule
    (``N = floor(max Δt_i·N_i / min Δt_i)``), which multiplied the request by
    ``max Δt / min Δt``: two filings whose auto Δt were 26.977 s and 0.94 s
    turned a requested N=100 000 into 2 869 893 steps;
  * whatever is left on auto is the **smallest** value computed across the
    individual systems (§D4.1 read across filings instead of across a single
    filing's sub-constellations);
  * §D4.7's integer-multiple constraint between coarse and fine is enforced.
"""
from __future__ import annotations

from streamlit_app.lib.job_runners.s1588_worker import (  # type: ignore[import]
    _joint_time_base,
)

# (Δt_fine, NSTEPS, Ncoarse) per filing — the real references measured for the
# two notices of EPFD_Test_Data.mdb (ntc 102 and 101) at the shared 0.45 m ES.
REFS = [(26.977, 1518572, 12), (0.94, 654638, 12)]


def test_user_iteration_count_is_used_verbatim():
    fine, coarse, ncoarse, nsteps, src = _joint_time_base(
        REFS, {"num_time_steps": 100_000},
    )
    assert nsteps == 100_000  # NOT 2_869_893
    assert src["n"] == "user"
    # Δt left on auto → smallest fine step across filings.
    assert fine == 0.94
    assert src["fine"] == "auto(min)"


def test_everything_auto_keeps_the_d41_rule():
    fine, coarse, ncoarse, nsteps, src = _joint_time_base(
        REFS, {"num_time_steps": 0},
    )
    assert fine == 0.94
    # floor(longest T_run / smallest Δt) — no requested count to honour.
    assert nsteps == int((26.977 * 1518572) // 0.94)
    assert src == {"n": "auto(min)", "fine": "auto(min)", "coarse": "auto(min)"}


def test_auto_coarse_is_the_smallest_across_filings():
    fine, coarse, ncoarse, _n, src = _joint_time_base(
        REFS, {"num_time_steps": 1000},
    )
    # min(26.977×12, 0.94×12) = 11.28 s, an exact 12× the 0.94 s fine step.
    assert coarse == 0.94 * 12
    assert ncoarse == 12
    assert src["coarse"] == "auto(min)"


def test_user_fine_and_coarse_are_used_as_given():
    fine, coarse, ncoarse, nsteps, src = _joint_time_base(
        REFS,
        {
            "num_time_steps": 100_000,
            "fine_time_step_s": 1.0, "_fine_step_overridden": True,
            "coarse_time_step_s": 10.0, "_coarse_step_overridden": True,
        },
    )
    assert (fine, coarse, ncoarse, nsteps) == (1.0, 10.0, 10, 100_000)
    assert src == {"n": "user", "fine": "user", "coarse": "user"}


def test_unset_override_flag_means_auto():
    """A step present in the cfg without its ``_overridden`` flag is a default,
    not a user choice — ``_load_cfg`` only sets the flag when the launcher
    actually passed the field."""
    fine, coarse, _nc, _n, src = _joint_time_base(
        REFS,
        # coarse_time_step_s=1.0 is the cfg default seen on real runs.
        {"num_time_steps": 1000, "coarse_time_step_s": 1.0},
    )
    assert fine == 0.94
    assert coarse == 0.94 * 12
    assert src["coarse"] == "auto(min)"


def test_coarse_is_snapped_to_an_integer_multiple_of_fine():
    """§D4.7: the coarse ladder must land on fine-step boundaries."""
    fine, coarse, ncoarse, _n, _src = _joint_time_base(
        REFS,
        {
            "num_time_steps": 1000,
            "fine_time_step_s": 1.0, "_fine_step_overridden": True,
            "coarse_time_step_s": 7.3, "_coarse_step_overridden": True,
        },
    )
    assert (fine, coarse, ncoarse) == (1.0, 7.0, 7)


def test_coarse_not_larger_than_fine_collapses_to_single_step():
    fine, coarse, ncoarse, _n, _src = _joint_time_base(
        REFS,
        {
            "num_time_steps": 1000,
            "fine_time_step_s": 5.0, "_fine_step_overridden": True,
            "coarse_time_step_s": 2.0, "_coarse_step_overridden": True,
        },
    )
    assert (fine, coarse, ncoarse) == (5.0, 5.0, 1)


def test_single_filing_is_unaffected_by_the_cross_filing_rule():
    one = [(2.0, 5000, 4)]
    fine, coarse, ncoarse, nsteps, _src = _joint_time_base(one, {"num_time_steps": 0})
    assert (fine, nsteps) == (2.0, 5000)
    assert (coarse, ncoarse) == (8.0, 4)
