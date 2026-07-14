"""End-to-end smoke tests for the streamlit_app workers + storage.

Skipped automatically when no real SRS test data is available (`data/320520275SRS.mdb`).
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA = REPO_ROOT / "data"
SRS = DATA / "320520275SRS.mdb"
MASK = DATA / "mask ntc_id 320520275 mask_id 2.xml"


def _have_real_data() -> bool:
    return SRS.exists() and MASK.exists()


@pytest.mark.skipif(not _have_real_data(), reason="no SRS test data on disk")
def test_s1503_worker_end_to_end(tmp_path: Path) -> None:
    params = {
        "result_path": str(tmp_path),
        "srs_path": str(SRS),
        "mask_path": str(MASK),
        "mask_id": None,
        "ntc_id": None,
        "num_time_steps": 30,
        "time_step_s": 1.0,
        "min_elevation_deg": 10.0,
        "service": "FSS",
    }
    pp = tmp_path / "params.json"
    pp.write_text(json.dumps(params))
    r = subprocess.run(
        [sys.executable, "-m", "streamlit_app.lib.job_runners.s1503_worker", str(pp)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, r.stderr
    out = json.loads((tmp_path / "sim_data.json").read_text())
    assert out["kind"] == "s1503"
    assert out["compliance"] in ("pass", "fail", "unknown")
    assert isinstance(out["ccdf_bins_db"], list) and out["ccdf_bins_db"]
    assert isinstance(out["percentiles"], dict)


@pytest.mark.skipif(not _have_real_data(), reason="no SRS test data on disk")
def test_s1588_method_1_worker(tmp_path: Path) -> None:
    rp = tmp_path
    params = {
        "result_path": str(rp),
        "method": "method_1",
        "filings": [{"srs_path": str(SRS), "mask_path": str(MASK)}],
        "num_time_steps": 30, "time_step_s": 1.0,
        "min_elevation_deg": 10.0, "service": "FSS",
    }
    pp = rp / "params.json"
    pp.write_text(json.dumps(params))
    r = subprocess.run(
        [sys.executable, "-m", "streamlit_app.lib.job_runners.s1588_worker", str(pp)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, r.stderr
    out = json.loads((rp / "sim_data.json").read_text())
    assert out["method"] == "method_1"
    assert out["ccdf_bins_db"], "expected CCDF after convolution"


def test_lib_modules_importable() -> None:
    sys.path.insert(0, str(REPO_ROOT / "streamlit_app"))
    from lib import (  # noqa: F401
        storage, state, plots, theme, engine, workers, filings, exports, launcher
    )
    storage.init_db()


def test_engine_reachable() -> None:
    sys.path.insert(0, str(REPO_ROOT / "streamlit_app"))
    from lib import engine
    assert engine.engine_health()["ok"], "engine src/s1588_studies must be importable"
