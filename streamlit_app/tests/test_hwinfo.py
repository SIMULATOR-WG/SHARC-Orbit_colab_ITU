"""Hardware capture (hwinfo)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from streamlit_app.lib import hwinfo  # noqa: E402


def test_local_hardware_has_core_fields():
    hw = hwinfo.local_hardware()
    assert hw["hostname"]
    assert isinstance(hw["cpu_count_logical"], int) and hw["cpu_count_logical"] >= 1
    assert hw["cpu_model"]  # never empty (falls back to platform/machine)


def test_run_hardware_standalone_when_ray_not_initialised(monkeypatch):
    # No remote nodes when Ray isn't initialised in-process.
    monkeypatch.setattr(hwinfo, "cluster_nodes", lambda: [])
    hw = hwinfo.run_hardware()
    assert hw["distributed"] is False
    assert "captured_by" in hw and hw["captured_by"]["hostname"]
    assert "nodes" not in hw


def test_run_hardware_distributed_shape(monkeypatch):
    fake_nodes = [
        {"node_id": "a", "alive": True, "cpu": 8.0, "mem_total_bytes": 10},
        {"node_id": "b", "alive": True, "cpu": 16.0, "mem_total_bytes": 20},
        {"node_id": "c", "alive": False, "cpu": 4.0, "mem_total_bytes": 5},
    ]
    monkeypatch.setattr(hwinfo, "cluster_nodes", lambda: fake_nodes)
    hw = hwinfo.run_hardware()
    assert hw["distributed"] is True
    assert hw["n_nodes"] == 2  # only alive nodes counted
    assert hw["nodes"] == fake_nodes


def test_cluster_nodes_empty_without_ray(monkeypatch):
    # Simulate ray missing / not initialised → no crash, empty list.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "ray":
            raise ImportError("no ray")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert hwinfo.cluster_nodes() == []
