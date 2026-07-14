"""hwinfo.py — capture the hardware a run executed on.

Records the executing host's CPU model / core counts and memory, and —
when the run is distributed over Ray — a per-node breakdown of the
actual compute machines. Persisted into each run's
``summary.json``/``sim_data.json`` so timings can be compared across
machines and the runtime estimator (``estimator.calibrate_from_runs``)
can be made hardware-aware.

All capture is best-effort: any probe failure degrades to a partial dict
rather than raising — recording hardware must never break a run.
"""
from __future__ import annotations

import os
import platform
from typing import Any


def _cpu_model() -> str:
    """Human CPU model. Linux ``/proc/cpuinfo`` first, then platform."""
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as fh:
            for line in fh:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except Exception:  # noqa: BLE001
        pass
    return platform.processor() or platform.machine() or "unknown"


def local_hardware() -> dict[str, Any]:
    """CPU + memory of the host calling this function."""
    info: dict[str, Any] = {
        "hostname": platform.node(),
        "platform": platform.platform(),
        "cpu_model": _cpu_model(),
        "cpu_count_logical": os.cpu_count(),
    }
    try:
        import psutil
        info["cpu_count_physical"] = psutil.cpu_count(logical=False)
        vm = psutil.virtual_memory()
        info["mem_total_bytes"] = int(vm.total)
        info["mem_available_bytes"] = int(vm.available)
        try:
            freq = psutil.cpu_freq()
            if freq and (freq.max or freq.current):
                info["cpu_freq_mhz"] = round(float(freq.max or freq.current), 1)
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass
    return info


def _probe_cpu_model() -> dict[str, Any]:
    """Remote probe body — returns one node's local hardware. Top-level so
    Ray can pickle it; ships with the ``hwinfo`` py_module."""
    return local_hardware()


def local_utilization(sample_interval: float = 0.3) -> dict[str, Any]:
    """Current CPU% and memory% of this host (live, instantaneous).

    ``sample_interval`` blocks briefly so the CPU% reflects the last window
    rather than 0.0 (psutil needs two samples). 0.3 s is fine inside a 2 s
    refresh loop. Returns a partial dict if psutil is unavailable.
    """
    out: dict[str, Any] = {"hostname": platform.node()}
    try:
        import psutil
        out["cpu_percent"] = round(float(psutil.cpu_percent(interval=sample_interval)), 1)
        vm = psutil.virtual_memory()
        out["mem_percent"] = round(float(vm.percent), 1)
        out["mem_used_bytes"] = int(vm.used)
        out["mem_total_bytes"] = int(vm.total)
    except Exception:  # noqa: BLE001
        pass
    return out


def _probe_utilization() -> dict[str, Any]:
    """Remote probe body — one node's live utilization. Top-level for pickle."""
    return local_utilization()


def cluster_utilization() -> list[dict[str, Any]]:
    """Per-node live CPU%/mem% when Ray is *already* initialised in-process.

    Pins a short probe to each alive node. Returns ``[]`` when Ray is
    unavailable / not initialised — never forces a cluster connection.
    """
    try:
        import ray
        from ray.util.scheduling_strategies import NodeAffinitySchedulingStrategy
        if not ray.is_initialized():
            return []
        nodes = ray.nodes()
    except Exception:  # noqa: BLE001
        return []

    # Inline probe (nested fn → cloudpickled by value): runs on any node
    # with psutil, WITHOUT needing streamlit_app shipped via runtime_env.
    def _probe() -> dict[str, Any]:
        import platform as _pf
        out: dict[str, Any] = {"hostname": _pf.node()}
        try:
            import psutil
            out["cpu_percent"] = round(float(psutil.cpu_percent(interval=0.3)), 1)
            vm = psutil.virtual_memory()
            out["mem_percent"] = round(float(vm.percent), 1)
            out["mem_used_bytes"] = int(vm.used)
            out["mem_total_bytes"] = int(vm.total)
        except Exception:  # noqa: BLE001
            pass
        return out

    probe = ray.remote(num_cpus=0)(_probe)
    futures: dict[Any, dict[str, Any]] = {}
    for n in nodes:
        if not n.get("Alive"):
            continue
        nid = n.get("NodeID")
        if not nid:
            continue
        meta = {
            "node_id": nid,
            "address": n.get("NodeManagerAddress"),
            "hostname": n.get("NodeManagerHostname"),
        }
        try:
            fut = probe.options(
                scheduling_strategy=NodeAffinitySchedulingStrategy(nid, soft=False)
            ).remote()
            futures[fut] = meta
        except Exception:  # noqa: BLE001
            pass

    if not futures:
        return []
    out: list[dict[str, Any]] = []
    try:
        ready, _ = ray.wait(list(futures.keys()), num_returns=len(futures), timeout=4.0)
    except Exception:  # noqa: BLE001
        ready = []
    for fut in ready:
        meta = futures[fut]
        try:
            meta.update(ray.get(fut))
        except Exception:  # noqa: BLE001
            pass
        out.append(meta)
    return out


def live_utilization() -> dict[str, Any]:
    """Live CPU%/mem% for display: local host always; cluster nodes when Ray
    is initialised in this process."""
    out: dict[str, Any] = {"local": local_utilization()}
    nodes = cluster_utilization()
    if nodes:
        out["nodes"] = nodes
    return out


def cluster_nodes() -> list[dict[str, Any]]:
    """Per-node hardware when Ray is *already* initialised in this process.

    Core counts + memory come from ``ray.nodes()`` resources (no remote
    execution). CPU model / available memory are enriched best-effort via
    a zero-CPU probe pinned to each alive node. Returns ``[]`` when Ray is
    unavailable or not initialised — never forces a cluster connection
    (so a standalone single-entry run stays local).
    """
    try:
        import ray
        if not ray.is_initialized():
            return []
        nodes = ray.nodes()
    except Exception:  # noqa: BLE001
        return []

    out: list[dict[str, Any]] = []
    for n in nodes:
        # Skip dead/disconnected nodes — they shouldn't pollute the final
        # report (Ray keeps stale records of crashed workers around).
        if not n.get("Alive"):
            continue
        res = n.get("Resources", {}) or {}
        out.append({
            "node_id": n.get("NodeID"),
            "hostname": n.get("NodeManagerHostname"),
            "address": n.get("NodeManagerAddress"),
            "alive": True,
            "cpu": res.get("CPU"),
            "mem_total_bytes": int(res["memory"]) if res.get("memory") else None,
            "object_store_bytes": (
                int(res["object_store_memory"])
                if res.get("object_store_memory") else None
            ),
        })

    try:
        _enrich_node_models(out)
    except Exception:  # noqa: BLE001
        pass
    return out


def _enrich_node_models(nodes: list[dict[str, Any]]) -> None:
    """Pin a tiny probe to each alive node to fetch CPU model + free mem."""
    import ray
    from ray.util.scheduling_strategies import NodeAffinitySchedulingStrategy

    probe = ray.remote(num_cpus=0)(_probe_cpu_model)
    futures: dict[Any, dict[str, Any]] = {}
    for nd in nodes:
        nid = nd.get("node_id")
        if not nid or not nd.get("alive"):
            continue
        fut = probe.options(
            scheduling_strategy=NodeAffinitySchedulingStrategy(nid, soft=False)
        ).remote()
        futures[fut] = nd

    if not futures:
        return
    ready, _ = ray.wait(list(futures.keys()), num_returns=len(futures), timeout=10.0)
    for fut in ready:
        try:
            probed = ray.get(fut)
            nd = futures[fut]
            nd["cpu_model"] = probed.get("cpu_model")
            if probed.get("mem_available_bytes") is not None:
                nd["mem_available_bytes"] = probed["mem_available_bytes"]
            if probed.get("cpu_freq_mhz") is not None:
                nd["cpu_freq_mhz"] = probed["cpu_freq_mhz"]
        except Exception:  # noqa: BLE001
            pass


def run_hardware() -> dict[str, Any]:
    """Hardware descriptor for a run, for persistence into its artifacts.

    Always includes ``captured_by`` (the driver/worker host). When the run
    is distributed (Ray initialised, ≥1 node), includes ``nodes`` (the
    real compute machines) and ``n_nodes``.
    """
    hw: dict[str, Any] = {"captured_by": local_hardware()}
    try:
        from . import cluster
        hw["cluster_mode"] = cluster.load().get("mode", "standalone")
    except Exception:  # noqa: BLE001
        hw["cluster_mode"] = "standalone"

    nodes = cluster_nodes()
    if nodes:
        hw["nodes"] = nodes
        hw["n_nodes"] = sum(1 for n in nodes if n.get("alive"))
        hw["distributed"] = True
    else:
        hw["distributed"] = False
    return hw


def run_timing(started_at, start_perf: float) -> dict[str, Any]:
    """Timing descriptor for a run: start/end wall-clock + total duration.

    Persisted next to ``hardware`` in ``summary.json`` / ``sim_data.json``.
    ``started_at`` is a tz-aware ``datetime`` captured at run start;
    ``start_perf`` is the matching ``time.perf_counter()`` reading
    (monotonic — the authoritative source for the elapsed duration, immune
    to wall-clock adjustments). ``finished_at`` is stamped now.
    """
    import time
    from datetime import datetime, timezone

    finished_at = datetime.now(timezone.utc)
    elapsed = max(0.0, time.perf_counter() - start_perf)
    h = int(elapsed // 3600)
    m = int((elapsed % 3600) // 60)
    s = int(elapsed % 60)
    return {
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": finished_at.isoformat(timespec="seconds"),
        "duration_seconds": round(elapsed, 3),
        "duration_hms": f"{h:02d}:{m:02d}:{s:02d}",
    }
