"""cluster.py — Ray runtime control + parallel dispatch helper.

Three modes (persisted to `data/cluster.json`):

* ``standalone`` — no Ray. All loops sequential. Default. Works without
  installing ``ray``.
* ``local`` — start a local Ray runtime on this machine (head + workers
  in the same process tree). Useful on a single fat box (many cores).
* ``cluster_client`` — connect to an external Ray head at
  ``address`` (e.g. ``ray://10.0.0.5:10001`` or ``auto`` if started via
  ``ray start --head`` on this machine).

When mode != standalone *and* the ``ray`` package is importable,
``parallel_starmap_progress`` dispatches tasks as Ray remote functions.
Otherwise the helper falls back to a plain Python loop — so the worker
code path is identical either way.

Worker subprocesses read ``data/cluster.json`` directly (the launcher
does not need to know).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Iterable

from . import DATA_ROOT

logger = logging.getLogger(__name__)

# Silence Ray's accelerator FutureWarning ("Ray will no longer override
# accelerator visible devices ... num_gpus=0"). Pure noise for a CPU-only
# workload; opting into the future behaviour quiets it.
os.environ.setdefault("RAY_ACCEL_ENV_VAR_OVERRIDE_ON_ZERO", "0")

CFG_PATH = DATA_ROOT / "cluster.json"


def _head_is_local(cfg: dict[str, Any]) -> bool:
    """True when the configured head node IP belongs to this machine.

    Lets ``ensure_init`` connect as a native driver (robust) instead of a
    ray:// client (fragile) when the head runs on the same host.
    """
    head_ip = ((cfg.get("head_info", {}) or {}).get("ip") or "").strip()
    if not head_ip:
        m = re.search(r"ray://([0-9.]+)", cfg.get("address", "") or "")
        head_ip = m.group(1) if m else ""
    if not head_ip:
        return False
    try:
        local_ips = {d["ip"] for d in list_local_ipv4()}
    except Exception:  # noqa: BLE001
        return False
    return head_ip in local_ips

DEFAULT_CFG: dict[str, Any] = {
    "mode": "standalone",   # standalone | local | cluster_client
    "address": "",          # only used when mode = cluster_client
    "num_cpus": 0,          # 0 = auto-detect (local only)
    "head_info": {},        # last head start: {ip, gcs_address, client_address, dashboard}
    "node_ip_address": "",  # bind IP for `ray start` (empty = let Ray auto-detect)
}


# ─── Persistence ────────────────────────────────────────────────────────────


def load() -> dict[str, Any]:
    """Read cluster.json. Returns DEFAULT_CFG if absent/corrupt."""
    if CFG_PATH.exists():
        try:
            data = json.loads(CFG_PATH.read_text(encoding="utf-8"))
            return {**DEFAULT_CFG, **data}
        except Exception:  # noqa: BLE001
            pass
    return dict(DEFAULT_CFG)


def save(cfg: dict[str, Any]) -> None:
    CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
    merged = {**DEFAULT_CFG, **(cfg or {})}
    CFG_PATH.write_text(json.dumps(merged, indent=2), encoding="utf-8")


# ─── Ray runtime ────────────────────────────────────────────────────────────


def is_ray_available() -> bool:
    try:
        import ray  # noqa: F401
        return True
    except ImportError:
        return False


_INIT_TIMEOUT_S = 8.0


def _init_with_timeout(target_fn, timeout: float) -> tuple[bool, str]:
    """Run target_fn() in a daemon thread; return (ok, error_msg).

    Ray's `ray.init(address=...)` can hang for ~60 s waiting on a dead
    GCS, and a hung gRPC client can outlive the streamlit script run.
    Bounding it via a thread keeps the UI responsive. If the thread
    overshoots, we leak it (daemon=True) so the streamlit process can
    still exit cleanly.
    """
    import threading
    result: dict[str, Any] = {"err": None}

    def _runner():
        try:
            target_fn()
        except Exception as exc:  # noqa: BLE001
            result["err"] = f"{type(exc).__name__}: {exc}"

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return False, f"ray.init timed out after {timeout:.0f} s (daemon unreachable?)"
    if result["err"]:
        return False, result["err"]
    return True, ""


def _ray_is_initialized_safe() -> bool:
    """`ray.is_initialized()` raises if a previous client channel was
    closed mid-flight; treat any error as 'not initialised'."""
    try:
        import ray
        return bool(ray.is_initialized())
    except Exception:  # noqa: BLE001
        # Force a clean shutdown so the next ray.init() rebuilds state.
        try:
            import ray
            ray.shutdown()
        except Exception:  # noqa: BLE001
            pass
        return False


def ensure_init(*, runtime_env: dict[str, Any] | None = None) -> dict[str, Any]:
    """Initialize Ray per saved cfg. Idempotent. Returns status dict.

    Returned dict keys: ``active`` (bool), ``mode``, ``address`` (optional),
    ``error`` (optional). When ``mode == "standalone"`` or ray missing,
    returns ``{"active": False, "mode": <m>}``. Bounded by an 8 s timeout
    so a stale daemon can't hang the UI.

    ``runtime_env`` is forwarded to ``ray.init`` — used by job workers
    that need to ship a ``working_dir`` (the SRS/mask MDBs) to every
    Ray node. Has no effect once Ray is already initialised in this
    process (Ray locks the runtime_env at first init).
    """
    cfg = load()
    mode = cfg.get("mode", "standalone")
    if mode == "standalone":
        return {"active": False, "mode": mode}
    if not is_ray_available():
        return {"active": False, "mode": mode, "error": "ray not installed"}
    import ray
    if _ray_is_initialized_safe():
        return {"active": True, "mode": mode}

    if mode == "cluster_client":
        # Threaded timeout — connecting to a dead head can hang ~60 s
        # which would otherwise lock up Streamlit.
        addr = (cfg.get("address") or "").strip() or "auto"

        # If the head node is THIS machine, connect as a *native driver*
        # (GCS address) instead of the ray:// client. The Ray client channel
        # tunnels every object/task through a single gRPC stream that drops
        # under heavy data load ("Failed to reconnect the data channel",
        # "Put failed") — and on failure the caller silently falls back to a
        # local-only run. A native driver talks to the local raylet directly:
        # robust, no client-channel bottleneck. Falls back to ray:// if the
        # driver connect fails.
        if _head_is_local(cfg):
            gcs = (cfg.get("head_info", {}) or {}).get("gcs_address") or "auto"

            def _do_init_driver():
                kw: dict[str, Any] = dict(
                    address=gcs, ignore_reinit_error=True, log_to_driver=False,
                )
                if runtime_env:
                    kw["runtime_env"] = runtime_env
                ray.init(**kw)

            ok, err = _init_with_timeout(_do_init_driver, _INIT_TIMEOUT_S)
            if ok:
                return {"active": True, "mode": mode, "address": gcs,
                        "connection": "driver"}
            logger.warning(
                "Ray native-driver connect to local head (%s) failed (%s); "
                "falling back to ray:// client.", gcs, err,
            )

        def _do_init():
            kw: dict[str, Any] = dict(
                address=addr, ignore_reinit_error=True, log_to_driver=False,
            )
            if runtime_env:
                kw["runtime_env"] = runtime_env
            ray.init(**kw)

        ok, err = _init_with_timeout(_do_init, _INIT_TIMEOUT_S)
        if ok:
            return {"active": True, "mode": mode, "address": addr,
                    "connection": "client"}
        return {"active": False, "mode": mode, "address": addr, "error": err}

    # local — must run on the main thread, otherwise Ray cannot install
    # its SIGTERM handler and subsequent `ray.cluster_resources()` calls
    # silently return empty. No timeout wrap here.
    num_cpus = int(cfg.get("num_cpus") or 0) or None
    try:
        kw: dict[str, Any] = dict(
            num_cpus=num_cpus, ignore_reinit_error=True, log_to_driver=False,
        )
        if runtime_env:
            kw["runtime_env"] = runtime_env
        ray.init(**kw)
        return {"active": True, "mode": mode}
    except Exception as exc:  # noqa: BLE001
        return {"active": False, "mode": mode,
                "error": f"{type(exc).__name__}: {exc}"}


def shutdown() -> None:
    """Shut down any Ray runtime attached to this process. Always safe."""
    try:
        import ray
    except ImportError:
        return
    # Always call shutdown — `is_initialized()` itself can raise on a
    # half-closed gRPC channel. ray.shutdown() is no-op when nothing
    # is up.
    try:
        ray.shutdown()
    except Exception:  # noqa: BLE001
        pass


def _call_with_timeout(fn, timeout: float, default: Any = None) -> tuple[Any, str]:
    """Run fn() in a daemon thread; return (value_or_default, err_msg)."""
    import threading
    result: dict[str, Any] = {"val": default, "err": ""}

    def _runner():
        try:
            result["val"] = fn()
        except Exception as exc:  # noqa: BLE001
            result["err"] = f"{type(exc).__name__}: {exc}"

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return default, f"timed out after {timeout:.0f}s"
    return result["val"], result["err"]


def status() -> dict[str, Any]:
    """UI-friendly status. Initializes Ray if mode != standalone.

    Every Ray RPC is bounded by a small timeout so a flaky/dead daemon
    can never hang the caller.
    """
    cfg = load()
    out: dict[str, Any] = {**cfg, "available": is_ray_available()}
    info = ensure_init()
    out.update(info)
    if info.get("active"):
        import ray
        errors: list[str] = []

        res, err = _call_with_timeout(ray.cluster_resources, 5.0, {})
        if err:
            errors.append(f"cluster_resources: {err}")
        out["resources"] = res

        ares, err = _call_with_timeout(ray.available_resources, 5.0, {})
        if err:
            errors.append(f"available_resources: {err}")
        out["available_resources"] = ares

        nodes, err = _call_with_timeout(ray.nodes, 5.0, [])
        if err:
            errors.append(f"nodes: {err}")
        # Always populate nodes/node_details even on partial failure
        out["nodes"] = len(nodes) if nodes else 0
        out["node_details"] = [
            {
                "node_id": n.get("NodeID"),
                "alive": n.get("Alive"),
                "resources": n.get("Resources", {}),
                "node_manager_address": n.get("NodeManagerAddress"),
            }
            for n in (nodes or [])
        ]
        if errors:
            out["error"] = " · ".join(errors)
    return out


# ─── Parallel dispatch ──────────────────────────────────────────────────────


#: Env var each Ray task reads (via s1588_worker._task_n_jobs) to learn
#: how many CPUs IT reserved, so the engine can chunk its own time-step loop
#: across that many real OS processes (multiprocessing.Pool) instead of
#: Numba threads — see ``_task_scoped`` for why threads don't work here.
TASK_CPUS_ENV = "SHARC_TASK_CPUS"


def _task_scoped(fn: Callable, num_cpus: float) -> Callable:
    """Wrap ``fn`` so it knows its OWN Ray CPU reservation via ``TASK_CPUS_ENV``.

    ``uploads_runtime_env`` pins every thread library (Numba/OMP/MKL/...) to
    1 thread for every Ray worker process — a *process-startup* env var, so
    it cannot be raised later at runtime (``numba.set_num_threads(N)`` can
    only ever LOWER the ceiling ``NUMBA_NUM_THREADS`` was started with).
    That pin exists precisely to stop Numba from grabbing every core in each
    of the many tasks Ray runs concurrently — correct, but it also means a
    single heavy task (e.g. a 30,000-satellite filing next to a 30-satellite
    one in the same grid sweep) can never get more raw compute by asking for
    more threads: the ceiling was fixed before Python even started.

    The parallelism axis that DOES still work per-task is the engine's own
    ``multiprocessing.Pool`` (its ``n_jobs`` parameter, real OS processes —
    each still capped to 1 Numba thread by the same env var, but N processes
    genuinely use N cores). This wrapper just publishes ``num_cpus`` (the
    reservation Ray granted this task) so the engine call sites in
    ``s1588_worker.py`` can pass it through as their own ``n_jobs`` instead
    of the flat default of 1 — Ray's "N tasks in parallel" (one per reserved
    slot) and the engine's "N processes within one task" now scale on the
    SAME budget instead of one silently doing nothing.
    """
    cap = max(1, int(round(num_cpus)))

    def _wrapped(*args, **kwargs):
        os.environ[TASK_CPUS_ENV] = str(cap)
        return fn(*args, **kwargs)

    # Preserve the original name/docstring for the Ray dashboard — otherwise
    # every task shows up as the generic "_wrapped", making a stalled or
    # failed task impossible to identify at a glance.
    try:
        import functools
        functools.update_wrapper(_wrapped, fn)
    except Exception:  # noqa: BLE001
        pass
    return _wrapped


def _costs_to_num_cpus(
    costs: list[float] | None, *, base_num_cpus: float, cpu_cap: float,
) -> list[float] | None:
    """Scale each task's Ray CPU reservation (and so its engine ``n_jobs``
    budget, via ``_task_scoped`` / ``TASK_CPUS_ENV``) to its relative
    ``costs`` weight.

    One flat ``num_cpus`` for every task is wrong when a batch mixes wildly
    different sizes — e.g. a grid sweep over 3 filings where one has 30
    satellites and another has 30,000: giving both the same single-process
    budget makes the heavy filing's tasks take far longer than they need
    to, while Ray happily runs many of the cheap filing's tasks concurrently
    (they don't need the extra cores). Scaling by cost fixes both: the
    cheapest task in the batch keeps ``base_num_cpus`` (unchanged
    behaviour when every task is similar), heavier ones reserve
    proportionally more.

    ``cpu_cap`` bounds any single task's reservation so it can't claim the
    whole node and serialize every other task (including other instances
    of the same heavy filing) behind it — callers pick a fraction of the
    node's total so at least a few tasks always fit concurrently.

    Returns ``None`` (caller keeps the flat ``base_num_cpus``) when
    ``costs`` is missing or degenerate (all zero/negative).
    """
    if not costs or not any(c > 0 for c in costs):
        return None
    min_cost = min(c for c in costs if c > 0)
    return [
        min(cpu_cap, max(base_num_cpus, base_num_cpus * (c / min_cost)))
        for c in costs
    ]


def parallel_starmap_progress(
    fn: Callable,
    items: list,
    *,
    num_cpus: float = 1.0,
    on_done: Callable[[int, int], None] | None = None,
    runtime_env: dict[str, Any] | None = None,
    costs: list[float] | None = None,
    stall_timeout_s: float | None = None,
) -> list:
    """Run ``fn(*item)`` for each tuple in ``items``.

    Uses ``ray.remote(num_cpus=num_cpus)`` and ``ray.wait`` for streaming
    completion when Ray is active; otherwise runs sequentially. Results
    preserve input ordering. ``on_done(i_done, n_total)`` fires after
    each task completes (caller-supplied; can be used for PROGRESS lines).

    ``runtime_env`` is passed to ``ray.init`` so the job's working_dir
    (containing the SRS/mask MDBs) is shipped to every Ray node.

    ``costs`` (optional, aligned to ``items``) drives two scheduling
    decisions, both purely optimisation hints — a wrong estimate only
    changes ordering/CPU shares, never correctness — and both fall back to
    plain behaviour (input order / flat ``num_cpus``) when ``None`` or
    mis-sized:

    * **LPT dispatch order**: tasks are *dispatched* heaviest-first so the
      slowest unit starts earliest, minimising makespan under
      heterogeneous task weights. Results are always returned in input
      order regardless of dispatch order.
    * **Per-task CPU reservation** (Ray-active only, see
      ``_costs_to_num_cpus``): the cheapest task in the batch keeps
      ``num_cpus``; heavier ones reserve proportionally more, capped so
      one task can't claim the whole node. Published to the task via
      ``TASK_CPUS_ENV`` (see ``_task_scoped``) so it can size the engine's
      own ``n_jobs`` (real multiprocessing) accordingly — see
      ``s1588_worker._task_n_jobs``.

    ``stall_timeout_s``: abort (TimeoutError) when **no** task completes
    for that long — a hung remote task must not block the job forever.
    ``None`` reads the ``SHARC_RAY_STALL_TIMEOUT_S`` env var; 0/unset
    disables the deadline (heartbeat lines are still emitted every wait
    cycle so a stalled job is visible in the run logs). On any abort the
    still-pending futures are cancelled — the first exception must not
    leave the rest of the job computing on the cluster.
    """
    if not items:
        return []
    n = len(items)
    # LPT dispatch order: a permutation of [0..n). Heaviest first.
    if costs is not None and len(costs) == n:
        order = sorted(range(n), key=lambda i: float(costs[i]), reverse=True)
    else:
        order = list(range(n))

    info = ensure_init(runtime_env=runtime_env)
    if info.get("active"):
        import ray
        # Publish each task's OWN CPU reservation via TASK_CPUS_ENV — see
        # _task_scoped. Sequential fallback below deliberately skips this
        # (nothing else runs concurrently there, so the engine's own -1/
        # auto n_jobs default is correct as-is).
        #
        # Per-item reservation from `costs`: a flat num_cpus is wrong when
        # the batch mixes wildly different task sizes (e.g. one filing
        # with 30 satellites next to one with 30,000 in the same grid
        # sweep) — see _costs_to_num_cpus. Capped at 1/4 of the node's
        # advertised CPUs so the heaviest task still leaves room for a
        # few tasks (including other instances of the same heavy filing)
        # to run concurrently, rather than serializing everything behind
        # a single giant reservation.
        try:
            total_cpus = float(ray.cluster_resources().get("CPU", 0)) or None
        except Exception:  # noqa: BLE001
            total_cpus = None
        cpu_cap = max(num_cpus, (total_cpus or float(os.cpu_count() or 1)) / 4.0)
        per_item_cpus = _costs_to_num_cpus(costs, base_num_cpus=num_cpus, cpu_cap=cpu_cap)

        results: list = [None] * n
        # SPREAD across nodes. Without it, ``ray.put`` shared args (see
        # broadcast_shared_in_tuples) live on the driver's node and Ray's
        # data-locality preference pins every task there — starving remote
        # workers. SPREAD overrides locality; the shared object is fetched
        # peer-to-peer to each node once (object store, not the client
        # channel). Submit heaviest-first for LPT.
        fut_to_idx: dict[Any, int] = {}
        for i in order:
            item_cpus = per_item_cpus[i] if per_item_cpus is not None else num_cpus
            rfn = ray.remote(_task_scoped(fn, item_cpus))
            opts = {"num_cpus": item_cpus, "scheduling_strategy": "SPREAD"}
            fut_to_idx[rfn.options(**opts).remote(*items[i])] = i
        pending = list(fut_to_idx.keys())
        done_count = 0
        if stall_timeout_s is None:
            try:
                stall_timeout_s = float(
                    os.environ.get("SHARC_RAY_STALL_TIMEOUT_S", "") or 0.0
                )
            except ValueError:
                stall_timeout_s = 0.0
        import time as _time
        wait_chunk_s = 30.0  # finite ray.wait — never block forever silently
        last_done_t = _time.monotonic()
        try:
            while pending:
                done, pending = ray.wait(
                    pending, num_returns=1, timeout=wait_chunk_s,
                )
                if not done:
                    stalled_s = _time.monotonic() - last_done_t
                    # Heartbeat — workers stream stdout to the run logs, so
                    # a stalled job stays visible instead of going silent.
                    print(
                        f"[cluster] heartbeat: {done_count}/{n} done, "
                        f"{len(pending)} pending, {stalled_s:.0f}s since "
                        "last completion", flush=True,
                    )
                    if stall_timeout_s and stalled_s > stall_timeout_s:
                        raise TimeoutError(
                            f"Ray tasks stalled: no completion for "
                            f"{stalled_s:.0f}s ({done_count}/{n} done)"
                        )
                    continue
                for fut in done:
                    results[fut_to_idx[fut]] = ray.get(fut)
                    done_count += 1
                    last_done_t = _time.monotonic()
                    if on_done is not None:
                        try:
                            on_done(done_count, n)
                        except Exception:  # noqa: BLE001
                            pass
        except BaseException:
            # First failure (task exception, stall timeout, Ctrl+C) aborts
            # the job — cancel everything still pending so the cluster is
            # not left burning CPU on a result nobody will collect.
            for fut in pending:
                try:
                    ray.cancel(fut, force=True)
                except Exception:  # noqa: BLE001
                    pass
            raise
        return results
    # Sequential fallback — honour LPT order too (harmless: same total
    # work, identical progress semantics to the Ray path).
    results = [None] * n
    done_count = 0
    for i in order:
        results[i] = fn(*items[i])
        done_count += 1
        if on_done is not None:
            try:
                on_done(done_count, n)
            except Exception:  # noqa: BLE001
                pass
    return results


# ─── Convenience: env hint for child subprocesses ───────────────────────────


def _is_broadcastable(x: Any) -> bool:
    """Heavy enough to be worth shipping once via ray.put (skip scalars)."""
    return not isinstance(x, (int, float, str, bool, bytes, type(None)))


def broadcast_shared_in_tuples(tuples: list[tuple]) -> list[tuple]:
    """``ray.put`` objects that repeat across ``tuples`` once; replace each
    occurrence with its ``ObjectRef``.

    Cuts Ray-client-channel traffic from once-per-task to once-per-shared-
    object. Critical in ``ray://`` client mode, where serializing a large
    object (constellation / PFD mask / antenna) into every task's args can
    saturate and drop the data channel (``Put failed``). The remote wrapper
    must restore the objects via :func:`resolve_refs_in_tuple`.

    No-op (returns input) when Ray isn't initialised or nothing is shared.
    """
    if not tuples:
        return tuples
    try:
        import ray
        if not ray.is_initialized():
            return tuples
    except Exception:  # noqa: BLE001
        return tuples

    counts: dict[int, int] = {}
    objs: dict[int, Any] = {}
    for t in tuples:
        for x in t:
            if _is_broadcastable(x):
                k = id(x)
                counts[k] = counts.get(k, 0) + 1
                objs[k] = x

    refs: dict[int, Any] = {}
    for k, c in counts.items():
        if c >= 2:
            try:
                refs[k] = ray.put(objs[k])
            except Exception:  # noqa: BLE001
                pass
    if not refs:
        return tuples
    return [tuple(refs.get(id(x), x) for x in t) for t in tuples]


def resolve_refs_in_tuple(t: tuple) -> tuple:
    """Restore any ``ObjectRef`` elements (from :func:`broadcast_shared_in_tuples`)
    back to their values. Called inside the remote worker before the engine
    function runs."""
    try:
        import ray
    except Exception:  # noqa: BLE001
        return t
    return tuple(
        ray.get(x) if isinstance(x, ray.ObjectRef) else x for x in t
    )


def env_overlay() -> dict[str, str]:
    """Env vars to pass to a worker subprocess so it skips ray banner spam."""
    overlay = {}
    cfg = load()
    if cfg.get("mode") != "standalone":
        overlay["RAY_DEDUP_LOGS"] = "1"
        overlay["RAY_DISABLE_IMPORT_WARNING"] = "1"
    return overlay


def uploads_runtime_env(
    *, size_limit_gb: float = 2.0, keep_subdirs: "Iterable[str] | None" = None,
) -> dict[str, Any] | None:
    """Build a Ray runtime_env that ships:

    * ``streamlit_app/data/uploads/`` as ``working_dir`` (SRS/mask MDBs
      accessible to tasks by relative path).
    * ``src/`` and ``streamlit_app/`` as ``py_modules`` so the engine
      and helper packages are importable on every Ray worker, even when
      the worker host has no SHARC-Orbit checkout.

    ``keep_subdirs``: upload paths (or their leading directory names) this run
    actually needs. Every OTHER top-level upload directory is excluded, so the
    shipped ``working_dir`` package carries this run's MDBs instead of the
    whole upload library — which grows without bound and is re-zipped,
    re-uploaded and re-unpacked on every worker node. A big package is also a
    bigger target for a corrupt unpack (Ray reuses a content-hashed package,
    so one bad zip keeps failing every task on that node). Falls back to
    shipping everything when the list is empty.

    Returns ``None`` when Ray is unavailable / mode is standalone.
    """
    cfg = load()
    if cfg.get("mode") == "standalone" or not is_ray_available():
        return None
    from . import UPLOADS_DIR, REPO_ROOT
    # Raise default working_dir size limit (Ray defaults to ~100 MB).
    limit_bytes = int(float(size_limit_gb) * 1024 * 1024 * 1024)
    os.environ["RAY_RUNTIME_ENV_WORKING_DIR_UPLOAD_SIZE_LIMIT_BYTES"] = str(limit_bytes)

    env: dict[str, Any] = {
        "py_modules": [
            str((REPO_ROOT / "src").resolve()),
            str((REPO_ROOT / "streamlit_app").resolve()),
        ],
        # Pin every thread library inside Ray tasks to 1 thread. Ray's
        # ``num_cpus`` cap controls *concurrent tasks*, not threads;
        # without this Numba/OMP/MKL each detect the host's full core
        # count and oversubscribe (N tasks × full cores). With these
        # env vars, total parallelism = the cluster's CPU budget.
        "env_vars": {
            "NUMBA_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "RAY_ACCEL_ENV_VAR_OVERRIDE_ON_ZERO": "0",
        },
        # Trim noise from the shipped packages.
        # NOTE: `src/data/` contains required JSON tables (Article 22,
        # Resolution 76); do NOT exclude it. We only strip the
        # *streamlit_app* runtime dirs (uploads/runs/exports/db).
        "excludes": [
            "**/__pycache__/**",
            "**/*.pyc",
            "**/.pytest_cache/**",
            "**/.venv/**",
            "**/tests/**",
            # streamlit_app/data subdirs only — paths relative to package root
            "data/uploads/**",
            "data/runs/**",
            "data/exports/**",
            "data/sharc_orbit.db*",
            "data/state/**",
            "data/cluster.json",
        ],
    }
    if UPLOADS_DIR.exists() and any(UPLOADS_DIR.rglob("*")):
        env["working_dir"] = str(UPLOADS_DIR.resolve())
        # Ship only the upload dirs this run needs. Patterns are anchored to
        # each uploaded root ("/name/**"), so they cannot touch the src/ and
        # streamlit_app/ py_modules.
        keep = {
            str(s).replace("\\", "/").strip("/").split("/")[0]
            for s in (keep_subdirs or []) if s
        }
        if keep:
            env["excludes"] = list(env["excludes"]) + [
                f"/{child.name}/**"
                for child in sorted(UPLOADS_DIR.iterdir())
                if child.is_dir() and child.name not in keep
            ]
    return env


def uploads_dir_size_bytes() -> int:
    """Total size of ``streamlit_app/data/uploads/`` (for UI hints)."""
    from . import UPLOADS_DIR
    total = 0
    if not UPLOADS_DIR.exists():
        return 0
    for p in UPLOADS_DIR.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def cli_hints() -> dict[str, str]:
    """Strings the UI can show to operators starting a real cluster."""
    return {
        "head": "ray start --head --port=6379 --dashboard-host=0.0.0.0",
        "worker": "ray start --address=<HEAD_IP>:6379",
        "stop": "ray stop",
        "client_address": "ray://<HEAD_IP>:10001",
    }


# ─── Local node daemon control (start/stop ray from the UI) ─────────────────


def _ray_cli() -> str | None:
    """Resolve absolute path to the ray CLI (None if missing).

    Tries PATH first, then falls back to the directory of the current
    Python interpreter — this catches the common case where Streamlit
    is invoked via `.venv/bin/python -m streamlit ...` without the venv
    being activated, so `which ray` misses it.
    """
    cli = shutil.which("ray")
    if cli:
        return cli
    import sys
    candidate = Path(sys.executable).parent / "ray"
    if candidate.exists() and os.access(candidate, os.X_OK):
        return str(candidate)
    # On Windows the script gets a `.exe` suffix
    candidate_exe = Path(sys.executable).parent / "ray.exe"
    if candidate_exe.exists():
        return str(candidate_exe)
    return None


def ray_node_status() -> dict[str, Any]:
    """Query the local Ray daemon (`ray status`). Detects whether this host
    is already running a head or attached as a worker.
    """
    cli = _ray_cli()
    if cli is None:
        return {"running": False, "error": "ray CLI not found in PATH"}
    try:
        out = subprocess.run(
            [cli, "status"],
            capture_output=True, text=True, timeout=8,
        )
        return {
            "running": out.returncode == 0,
            "stdout": out.stdout,
            "stderr": out.stderr,
            "returncode": out.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"running": False, "error": "ray status timed out"}
    except Exception as exc:  # noqa: BLE001
        return {"running": False, "error": f"{type(exc).__name__}: {exc}"}


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mGKHJ]")


def _parse_head_output(text: str) -> dict[str, Any]:
    """Pull head node IP / addresses out of `ray start --head` output.

    Ray emits ANSI colour codes around the values, so strip them first.
    The "dashboard at" announcement spans two lines — we look for an
    IP:PORT pattern on the next non-empty line.
    """
    text = _ANSI_RE.sub("", text)
    info: dict[str, Any] = {}
    m = re.search(r"Local node IP:\s*([0-9.]+|[0-9A-Fa-f:]+)", text)
    if m:
        info["ip"] = m.group(1)
    m = re.search(r"--address=['\"]?([0-9A-Fa-f.:\[\]]+:\d+)['\"]?", text)
    if m:
        info["gcs_address"] = m.group(1)
    # Derive ip from gcs_address as fallback
    if "ip" not in info and "gcs_address" in info:
        info["ip"] = info["gcs_address"].rsplit(":", 1)[0]
    # Dashboard URL is announced on the next line ("dashboard at\n  IP:PORT")
    # Require a full IPv4 octet pattern to avoid matching the log timestamp.
    m = re.search(
        r"dashboard at[^\n]*\n[^\n]*?(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+)",
        text,
    )
    if m:
        info["dashboard"] = f"http://{m.group(1)}"
    return info


def list_local_ipv4() -> list[dict[str, str]]:
    """Enumerate IPv4 addresses bound to this host's network interfaces.

    Returns a list of ``{"name", "ip", "kind"}`` dicts. ``kind`` is one of:

    * ``"vpn"``       — IP in 100.64.0.0/10 (CGNAT range used by overlay
                         networks such as Tailscale, Nebula, etc.)
    * ``"private"``   — RFC 1918 (10/8, 172.16/12, 192.168/16)
    * ``"loopback"``  — 127.0.0.0/8
    * ``"public"``    — everything else (rare on a workstation)
    """
    out: list[dict[str, str]] = []
    try:
        import psutil
        import socket as _s
        for iface, addrs in psutil.net_if_addrs().items():
            for a in addrs:
                if getattr(a, "family", None) != _s.AF_INET:
                    continue
                ip = a.address
                if not ip or ip.count(".") != 3:
                    continue
                first, second = ip.split(".")[0:2]
                if first == "127":
                    kind = "loopback"
                elif first == "100" and 64 <= int(second) <= 127:
                    kind = "vpn"
                elif first in ("10", "192") or (first == "172" and 16 <= int(second) <= 31):
                    kind = "private"
                else:
                    kind = "public"
                out.append({"name": iface, "ip": ip, "kind": kind})
    except Exception:  # noqa: BLE001
        pass
    # Order: vpn, private, public, loopback — most useful first
    order = {"vpn": 0, "private": 1, "public": 2, "loopback": 3}
    out.sort(key=lambda x: (order.get(x["kind"], 9), x["name"]))
    return out


def start_head(*, port: int = 6379, dashboard_host: str = "0.0.0.0",
                dashboard_port: int = 8265,
                ray_client_server_port: int = 10001,
                num_cpus: int | None = None,
                node_ip_address: str | None = None) -> dict[str, Any]:
    """Spawn `ray start --head` as a daemon. Returns the CLI's own dict.

    Idempotent: if a daemon is already running, the CLI prints a clear
    error and returncode is non-zero — we surface that instead of failing.

    ``num_cpus`` caps the CPU count this head node advertises to Ray. None
    or 0 = auto-detect (uses all logical cores).
    """
    cli = _ray_cli()
    if cli is None:
        return {"ok": False, "error": "ray CLI not found in PATH"}
    cmd = [
        cli, "start", "--head",
        f"--port={port}",
        f"--dashboard-host={dashboard_host}",
        f"--dashboard-port={dashboard_port}",
        f"--ray-client-server-port={ray_client_server_port}",
    ]
    if num_cpus and int(num_cpus) > 0:
        cmd.append(f"--num-cpus={int(num_cpus)}")
    if node_ip_address and node_ip_address.strip():
        cmd.append(f"--node-ip-address={node_ip_address.strip()}")
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        ok = out.returncode == 0
        head_info: dict[str, Any] = {}
        if ok:
            head_info = _parse_head_output((out.stdout or "") + "\n" + (out.stderr or ""))
            if head_info.get("ip"):
                head_info["client_address"] = (
                    f"ray://{head_info['ip']}:{ray_client_server_port}"
                )
            if head_info.get("ip") and "gcs_address" not in head_info:
                head_info["gcs_address"] = f"{head_info['ip']}:{port}"
            # Persist alongside the cluster config
            cfg = load()
            cfg["head_info"] = head_info
            save(cfg)
        return {
            "ok": ok,
            "cmd": " ".join(cmd),
            "stdout": out.stdout,
            "stderr": out.stderr,
            "returncode": out.returncode,
            "head_info": head_info,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "ray start --head timed out (90 s)"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def start_worker(*, address: str, num_cpus: int | None = None,
                   node_ip_address: str | None = None) -> dict[str, Any]:
    """Spawn `ray start --address=...` to attach this host to a remote head.

    ``num_cpus`` caps the CPU count this worker advertises. None/0 = auto.
    ``node_ip_address`` binds Ray to a specific local interface (needed
    when the worker is on a VPN like Tailscale and should advertise its
    tailnet IP rather than the LAN default).
    """
    if not address.strip():
        return {"ok": False, "error": "address is empty"}
    cli = _ray_cli()
    if cli is None:
        return {"ok": False, "error": "ray CLI not found in PATH"}
    cmd = [cli, "start", f"--address={address.strip()}"]
    if num_cpus and int(num_cpus) > 0:
        cmd.append(f"--num-cpus={int(num_cpus)}")
    if node_ip_address and node_ip_address.strip():
        cmd.append(f"--node-ip-address={node_ip_address.strip()}")
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        return {
            "ok": out.returncode == 0,
            "cmd": " ".join(cmd),
            "stdout": out.stdout,
            "stderr": out.stderr,
            "returncode": out.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "ray start --address timed out (90 s)"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def stop_node() -> dict[str, Any]:
    """Run `ray stop` on this host (kills any local Ray daemon)."""
    cli = _ray_cli()
    if cli is None:
        return {"ok": False, "error": "ray CLI not found in PATH"}
    try:
        out = subprocess.run([cli, "stop"], capture_output=True, text=True, timeout=30)
        if out.returncode == 0:
            cfg = load()
            cfg["head_info"] = {}
            save(cfg)
        return {
            "ok": out.returncode == 0,
            "stdout": out.stdout,
            "stderr": out.stderr,
            "returncode": out.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "ray stop timed out (30 s)"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
