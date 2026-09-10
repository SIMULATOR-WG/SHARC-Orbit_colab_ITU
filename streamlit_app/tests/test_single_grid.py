"""Single-entry ES×GSO grid (Aggregate method_2 on one filing)."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "streamlit_app"))
sys.path.insert(0, str(REPO))

from lib import estimator, launcher  # noqa: E402


def test_estimate_method_2_accepts_one_system() -> None:
    est = estimator.estimate_aggregate(
        method="method_2", n_systems=1, n_sat_per_system=20,
        n_time_steps=100, grid_step_deg=90.0, gso_pointing_step_deg=90.0,
        min_elevation_deg=10.0,
    )
    assert est.n_sims >= 1
    assert "grid points" in " ".join(est.notes).lower() or est.n_sims > 1


def test_launch_s1588_accepts_single_kind() -> None:
    sig = inspect.signature(launcher.launch_s1588)
    assert "kind" in sig.parameters
    assert sig.parameters["kind"].default == "aggregate"


def test_iter_geometry_grid_country_filter_shrinks() -> None:
    from src.s1588_studies import iter_geometry_grid

    world = list(iter_geometry_grid(
        grid_step_deg=30.0, gso_pointing_step_deg=90.0, min_elevation_deg=10.0,
    ))
    br = list(iter_geometry_grid(
        grid_step_deg=30.0, gso_pointing_step_deg=90.0, min_elevation_deg=10.0,
        country_codes=["BRA"],
    ))
    assert world
    assert br
    assert len(br) < len(world)
    assert all(-35.0 <= p.es_lat_deg <= 6.0 for p in br)
