"""wcga_cluster.py — Ray-backed executor for the WCGA latitude sweep.

Injected into the engine via ``wcg_search.set_wcga_executor`` so a single
heavy single-entry WCGA can fan its per-latitude work out across the Ray
cluster instead of being capped to one machine's cores.

The decomposition (one ``_WCGState`` per satellite latitude) and the merge
are exactly the engine's local ``multiprocessing.Pool`` path — only the
*executor* differs — so results are parity-preserving. Each remote worker
runs single-threaded (Numba pinned to 1); cluster parallelism comes from
running many latitude tasks at once, not from threads inside one.
"""
from __future__ import annotations

from typing import Any, Callable

from . import cluster


def _wcga_remote_task(worker_fn: Callable, args: tuple, init: dict[str, Any]) -> Any:
    """Remote body: re-apply per-worker engine state, then run one latitude.

    Ray workers are fresh processes that never ran the engine's Pool
    initializer, so GMST0 / Numba threads / GSO-longitude mode must be set
    here before computing.
    """
    import os
    from src import wcg_search as _w  # type: ignore[import]

    os.environ["WCG_GMST0_DEG"] = str(init.get("gmst0_deg", "0.0"))
    os.environ["WCG_MAIN_PID"] = str(init.get("main_pid", ""))
    try:
        _w.set_numba_num_threads(int(init.get("numba_threads", 1) or 1))
    except Exception:  # noqa: BLE001
        pass
    gso_mode = init.get("gso_mode")
    if gso_mode:
        try:
            _w.set_gso_longitude_mode(gso_mode)
        except Exception:  # noqa: BLE001
            pass
    alpha_method = init.get("alpha_method")
    if alpha_method:
        try:
            _w.set_alpha_method(alpha_method)
        except Exception:  # noqa: BLE001
            pass
    # Restore any broadcast ObjectRefs (oe_ref / common shipped once).
    return worker_fn(cluster.resolve_refs_in_tuple(args))


def _make_executor(on_progress: Callable[[int, int], None] | None = None) -> Callable:
    """Build an executor matching ``wcg_search.set_wcga_executor``'s contract."""

    def _executor(worker_fn: Callable, args_list: list, init: dict[str, Any]) -> list:
        # Ship shared oe_ref / common once via ray.put (channel-friendly).
        shared = cluster.broadcast_shared_in_tuples(list(args_list))
        items = [(worker_fn, a, init) for a in shared]
        return cluster.parallel_starmap_progress(
            _wcga_remote_task, items, on_done=on_progress,
        )

    return _executor


def enable(
    *,
    runtime_env: dict[str, Any] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> bool:
    """Install the Ray WCGA executor into the engine.

    Initialises Ray (idempotent) and, only if a runtime is active, injects
    the executor. Returns True when the distributed path is armed, False
    when Ray is unavailable/standalone (engine keeps its local Pool path).
    ``runtime_env`` should ship ``src/`` as a py_module so remote workers
    can ``import wcg_search`` (see ``cluster.uploads_runtime_env``).
    """
    from src import wcg_search  # type: ignore[import]

    info = cluster.ensure_init(runtime_env=runtime_env)
    if not info.get("active"):
        return False
    wcg_search.set_wcga_executor(_make_executor(on_progress))
    return True


def disable() -> None:
    """Restore the engine's built-in multiprocessing.Pool WCGA path."""
    from src import wcg_search  # type: ignore[import]

    wcg_search.set_wcga_executor(None)
