"""LPT dispatch + cost planning (cluster.parallel_starmap_progress, plan)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from streamlit_app.lib import cluster, plan  # noqa: E402


def _seq_mode(monkeypatch):
    """Force the sequential fallback (no Ray) for deterministic order checks."""
    monkeypatch.setattr(cluster, "ensure_init", lambda **_: {"active": False})


def test_lpt_dispatches_heaviest_first(monkeypatch):
    _seq_mode(monkeypatch)
    call_order: list[int] = []

    def fn(x):
        call_order.append(x)
        return x * 10

    items = [(i,) for i in range(5)]          # payloads 0..4
    costs = [1.0, 5.0, 2.0, 9.0, 3.0]          # heaviest = index 3, then 1, 4, 2, 0
    results = cluster.parallel_starmap_progress(fn, items, costs=costs)

    # Results stay aligned to INPUT order regardless of dispatch order.
    assert results == [0, 10, 20, 30, 40]
    # Execution order is heaviest-cost first.
    assert call_order == [3, 1, 4, 2, 0]


def test_no_costs_preserves_input_order(monkeypatch):
    _seq_mode(monkeypatch)
    call_order: list[int] = []
    items = [(i,) for i in range(4)]
    results = cluster.parallel_starmap_progress(
        lambda x: (call_order.append(x), x)[1], items,
    )
    assert results == [0, 1, 2, 3]
    assert call_order == [0, 1, 2, 3]


def test_progress_callback_counts_completions(monkeypatch):
    _seq_mode(monkeypatch)
    seen: list[tuple[int, int]] = []
    items = [(i,) for i in range(3)]
    cluster.parallel_starmap_progress(
        lambda x: x, items,
        on_done=lambda i, n: seen.append((i, n)),
        costs=[3.0, 1.0, 2.0],
    )
    assert seen == [(1, 3), (2, 3), (3, 3)]


def test_filing_costs_uniform_when_n_sat_unknown(monkeypatch):
    monkeypatch.setattr(plan, "filing_n_sat", lambda f, c: None)
    costs = plan.filing_costs([{"a": 1}, {"b": 2}], {"num_time_steps": 100})
    assert costs == [1.0, 1.0]


def test_filing_costs_monotonic_in_n_sat(monkeypatch):
    sizes = {"small": 10, "big": 200}
    monkeypatch.setattr(plan, "filing_n_sat", lambda f, c: sizes[f["id"]])
    common = {"num_time_steps": 1000, "s1503_step_deg": 1.0}
    costs = plan.filing_costs([{"id": "small"}, {"id": "big"}], common)
    assert costs[1] > costs[0] > 1.0


def test_filing_costs_phase_selection(monkeypatch):
    monkeypatch.setattr(plan, "filing_n_sat", lambda f, c: 50)
    common = {"num_time_steps": 1000, "s1503_step_deg": 1.0}
    both = plan.filing_costs([{}], common, sim=True, wcga=True)[0]
    sim_only = plan.filing_costs([{}], common, sim=True, wcga=False)[0]
    wcga_only = plan.filing_costs([{}], common, sim=False, wcga=True)[0]
    # Phases are additive; each partial is smaller than the sum.
    assert sim_only < both and wcga_only < both
    assert abs((sim_only + wcga_only) - both) < 1e-6
