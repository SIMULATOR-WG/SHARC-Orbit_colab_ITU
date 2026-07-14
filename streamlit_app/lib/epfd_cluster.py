"""epfd_cluster.py — Ray-backed executor for the EPFD time-chunk sweep.

Injected into the engine via ``epfd_calculator.set_epfd_executor`` so a
single heavy temporal simulation can fan its time-chunks out across the Ray
cluster instead of being capped to one machine's cores.

The decomposition (one ``EPFDStreamAccumulator`` per time-chunk, re-propagated
from scratch) and the merge are exactly the engine's local
``multiprocessing.Pool`` path — only the executor differs — so results are
parity-preserving. Each remote worker runs single-threaded (Numba pinned to
1); cluster parallelism comes from running many chunks at once.

Unlike the local Pool (forked, so it inherits the run's global engine state),
fresh Ray workers must re-apply GMST0 / GSO-longitude mode / alpha method /
Numba threads before computing — carried in the ``init`` dict the engine
passes through.
"""
from __future__ import annotations

from typing import Any, Callable

from . import cluster


def _epfd_remote_task(worker_fn: Callable, chunk: tuple, init: dict[str, Any]) -> Any:
    """Remote body: re-apply engine global state, then run one time-chunk."""
    from src import coordinates as _coord  # type: ignore[import]
    from src import geometry as _geom  # type: ignore[import]

    if init.get("gmst0_deg") is not None:
        try:
            _coord.set_earth_rotation_initial_deg(float(init["gmst0_deg"]))
        except Exception:  # noqa: BLE001
            pass
    if init.get("gso_mode"):
        try:
            _geom.set_gso_longitude_mode(init["gso_mode"])
        except Exception:  # noqa: BLE001
            pass
    if init.get("alpha_method"):
        try:
            _geom.set_alpha_method(init["alpha_method"])
        except Exception:  # noqa: BLE001
            pass
    try:
        _geom.set_numba_num_threads(int(init.get("numba_threads", 1) or 1))
    except Exception:  # noqa: BLE001
        pass

    # Restore any broadcast ObjectRefs (constellation/wcg/mask/antenna shipped once).
    return worker_fn(cluster.resolve_refs_in_tuple(chunk))


def _make_executor(on_progress: Callable[[int, int], None] | None = None) -> Callable:
    """Build an executor matching ``epfd_calculator.set_epfd_executor``."""

    def _executor(worker_fn: Callable, chunks: list, init: dict[str, Any]) -> list:
        # Ship shared constellation/wcg/mask/antenna once via ray.put: each
        # chunk then carries only ObjectRefs + light scalars (channel-friendly).
        shared = cluster.broadcast_shared_in_tuples(list(chunks))
        items = [(worker_fn, c, init) for c in shared]
        return cluster.parallel_starmap_progress(
            _epfd_remote_task, items, on_done=on_progress,
        )

    return _executor


def enable(
    *,
    runtime_env: dict[str, Any] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> bool:
    """Install the Ray EPFD executor into the engine.

    Initialises Ray (idempotent) and injects the executor only if a runtime
    is active. Returns True when the distributed path is armed, False when
    Ray is unavailable/standalone (engine keeps its local Pool). ``runtime_env``
    should ship ``src/`` as a py_module so remote workers can import the
    engine (see ``cluster.uploads_runtime_env``).
    """
    from src import epfd_calculator  # type: ignore[import]

    info = cluster.ensure_init(runtime_env=runtime_env)
    if not info.get("active"):
        return False
    epfd_calculator.set_epfd_executor(_make_executor(on_progress))
    return True


def disable() -> None:
    """Restore the engine's built-in multiprocessing.Pool EPFD path."""
    from src import epfd_calculator  # type: ignore[import]

    epfd_calculator.set_epfd_executor(None)
