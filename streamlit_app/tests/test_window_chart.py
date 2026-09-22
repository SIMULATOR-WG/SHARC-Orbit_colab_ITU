"""The CCDF chart draws the envelope plus the worst window set, not every set."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "streamlit_app"))

from lib.result_artifacts import _worst_window_for_plot  # noqa: E402


def test_chart_keeps_only_the_worst_window_set() -> None:
    data = {
        "worst_window_index": 2,
        "per_window": [
            {"window_index": 0, "ccdf_bins_db": [-160], "ccdf_pct": [1], "max_epfd_dbw": -160},
            {"window_index": 1, "ccdf_bins_db": [-150], "ccdf_pct": [1], "max_epfd_dbw": -150},
            {"window_index": 2, "ccdf_bins_db": [-140], "ccdf_pct": [1], "max_epfd_dbw": -140},
        ],
    }
    chosen = _worst_window_for_plot(data)
    assert chosen is not None
    assert chosen["window_index"] == 2


def test_falls_back_to_the_highest_peak() -> None:
    data = {
        "per_window": [
            {"window_index": 0, "ccdf_bins_db": [-160], "ccdf_pct": [1], "max_epfd_dbw": -160},
            {"window_index": 4, "ccdf_bins_db": [-130], "ccdf_pct": [1], "max_epfd_dbw": -130},
        ],
    }
    chosen = _worst_window_for_plot(data)
    assert chosen["window_index"] == 4
