"""launcher.py — spawn workers + register in DB + persist params on disk.

The bridge between Streamlit pages (form submission) and the standalone
worker scripts (`lib/job_runners/`). Reads the system rows from the SQLite
to assemble the worker payload, then spawns a subprocess.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import RUNS_DIR, REPO_ROOT, UPLOADS_DIR, storage, workers

# Run states that must never be overwritten by a late handle sync (e.g. a
# cancelled run whose process exits with a signal on the next Status tick).
_TERMINAL_STATES = ("success", "failed", "cancelled")

# Grace window before a pending run with no worker handle is declared an
# orphan — protects the create_run → spawn window of a run being launched
# concurrently by another session/page.
_ORPHAN_GRACE_S = 30.0


def _params_path(run_id: str) -> Path:
    return RUNS_DIR / run_id / "params.json"


def _relpath_under_uploads(abs_path: str | None) -> str | None:
    """Return path relative to ``UPLOADS_DIR`` (e.g. ``abc123/STEAM-2.MDB``),
    or ``None`` if ``abs_path`` is missing or lives outside ``UPLOADS_DIR``.

    The relpath is what we ship to remote Ray workers — they resolve it
    against their own ``UPLOADS_DIR`` (or Ray's working_dir CWD when the
    files are auto-shipped via runtime_env). Always emitted with POSIX
    separators so a Windows head produces paths a Linux worker can resolve.
    """
    if not abs_path:
        return None
    try:
        return Path(abs_path).resolve().relative_to(UPLOADS_DIR.resolve()).as_posix()
    except ValueError:
        return None


def _system_to_filing(system_id: str) -> dict[str, Any] | None:
    s = storage.get_system(system_id)
    if not s:
        return None
    return {
        # Absolute paths — used on single-machine runs (head opens directly).
        "srs_path": s["srs_path"],
        "mask_path": s.get("mask_path"),
        # Relative paths — used by Ray workers (running on remote hosts
        # where the abs path would point at the head's filesystem).
        "srs_relpath": _relpath_under_uploads(s["srs_path"]),
        "mask_relpath": _relpath_under_uploads(s.get("mask_path")),
        "mask_id": s.get("mask_id"),
        "ntc_id": s.get("ntc_id"),
        "system_id": s["id"],
    }


def launch_s1503(*, system_id: str, params: dict[str, Any]) -> str:
    """Launch a single-system S.1503 run for the given (upload, ntc, mask) tuple."""
    sys_row = _system_to_filing(system_id)
    if not sys_row:
        raise ValueError(f"system {system_id} not found")
    full = dict(params)
    full.update({
        "srs_path": sys_row["srs_path"],
        "mask_path": sys_row.get("mask_path"),
        "srs_relpath": sys_row.get("srs_relpath"),
        "mask_relpath": sys_row.get("mask_relpath"),
        # A caller-supplied mask_id (e.g. an Article 22 scenario leaf picked
        # in the UI) wins over the system row's stored mask_id.
        "mask_id": params.get("mask_id") if params.get("mask_id") is not None
                   else sys_row.get("mask_id"),
        "ntc_id": sys_row.get("ntc_id"),
        "system_id": system_id,
    })
    run_id = storage.create_run(kind="single", method=None, params=full)
    pp = _params_path(run_id)
    pp.parent.mkdir(parents=True, exist_ok=True)
    full["result_path"] = str(pp.parent)
    pp.write_text(json.dumps(full, indent=2), encoding="utf-8")
    h = workers.spawn(
        run_id,
        args=[sys.executable, "-m", "streamlit_app.lib.job_runners.s1503_worker", str(pp)],
        cwd=REPO_ROOT,
    )
    _persist_worker_pid(run_id, h.proc.pid)
    storage.update_run(run_id, status="running", progress_pct=0.0)
    return run_id


def launch_s1503_manual(*, manual_cfg: dict[str, Any],
                        params: dict[str, Any]) -> str:
    """Launch a single S.1503 run for a MANUAL/parametric system (R3/R4).

    No SRS db / systems-table row: the full non_gso + pfd_mask definition
    travels in ``params['manual_cfg']`` and the worker builds the engine
    config via ``load_from_manual``.
    """
    full = dict(params)
    full.update({
        "manual_cfg": manual_cfg,
        "system_id": None,
        "srs_path": None,
        "mask_path": (manual_cfg.get("pfd_mask") or {}).get("file"),
        "mask_id": (manual_cfg.get("pfd_mask") or {}).get("mask_id"),
        "ntc_id": None,
        "input_source": "manual",
        "label": manual_cfg.get("label") or "manual system",
    })
    run_id = storage.create_run(kind="single", method=None, params=full)
    pp = _params_path(run_id)
    pp.parent.mkdir(parents=True, exist_ok=True)
    full["result_path"] = str(pp.parent)
    pp.write_text(json.dumps(full, indent=2), encoding="utf-8")
    h = workers.spawn(
        run_id,
        args=[sys.executable, "-m", "streamlit_app.lib.job_runners.s1503_worker", str(pp)],
        cwd=REPO_ROOT,
    )
    _persist_worker_pid(run_id, h.proc.pid)
    storage.update_run(run_id, status="running", progress_pct=0.0)
    return run_id


def launch_s1588(*, method: str, system_ids: list[str],
                  params: dict[str, Any], campaign_id: str | None = None) -> str:
    """Launch a multi-system S.1588 run for the listed system_ids."""
    filings_payload = []
    for sid in system_ids:
        row = _system_to_filing(sid)
        if not row:
            continue
        filings_payload.append(row)
    if not filings_payload:
        raise ValueError("no valid systems provided")

    payload = dict(params, method=method, filings=filings_payload, system_ids=system_ids)
    run_id = storage.create_run(kind="aggregate", method=method, params=payload,
                                  campaign_id=campaign_id)
    pp = _params_path(run_id)
    pp.parent.mkdir(parents=True, exist_ok=True)
    payload["result_path"] = str(pp.parent)
    pp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    h = workers.spawn(
        run_id,
        args=[sys.executable, "-m", "streamlit_app.lib.job_runners.s1588_worker", str(pp)],
        cwd=REPO_ROOT,
    )
    _persist_worker_pid(run_id, h.proc.pid)
    storage.update_run(run_id, status="running", progress_pct=0.0)
    return run_id


def _persist_worker_pid(run_id: str, pid: int) -> None:
    """Record the worker PID on disk so a restarted UI (which loses the
    in-memory handle registry) can tell a still-running worker from a dead
    one when reconciling orphan runs."""
    try:
        (RUNS_DIR / run_id / "worker.pid").write_text(str(pid), encoding="utf-8")
    except OSError:
        pass


def _read_worker_pid(run_id: str) -> int | None:
    try:
        return int((RUNS_DIR / run_id / "worker.pid").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _persist_worker_log(run_id: str, h: workers.RunHandle) -> None:
    """Write the retained log tail to disk before the handle is discarded."""
    try:
        log_path = RUNS_DIR / run_id / "worker.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # list() snapshots the deque atomically; the reader thread may still
        # be appending.
        log_path.write_text("\n".join(list(h.logs)) + "\n", encoding="utf-8")
    except OSError:
        pass


def sync_handle_to_db(run_id: str) -> None:
    """Inspect a live worker handle and propagate to the DB. Idempotent.

    Terminal DB states (success / failed / cancelled) are never overwritten:
    the cancel flow (pages call ``workers.cancel`` + ``update_run(status=
    "cancelled")``) must not be undone by the next Status tick seeing the
    SIGTERM'd process (return_code=-15, no summary.json) as a failure.
    """
    h = workers.get(run_id)
    if h is None:
        return
    workers.drain(h)
    run = storage.get_run(run_id)
    if run is None:
        # Run row was deleted while the handle was still registered (e.g.
        # delete from another tab). Settle the handle without resurrecting
        # data/runs/<id>/ via _persist_worker_log or DB updates.
        if not h.finished:
            workers.cancel(run_id)
            return
        while workers.drain(h):
            pass
        workers.discard(run_id)
        return
    db_terminal = run.get("status") in _TERMINAL_STATES

    if not h.finished:
        if db_terminal:
            # e.g. cancel requested — wait for process exit before cleanup.
            return
        pct = 0.0
        for line in reversed(h.logs[-300:]):
            if line.startswith("PROGRESS:"):
                try:
                    pct = float(line.split(":", 1)[1])
                    break
                except ValueError:
                    pass
        if pct:
            storage.update_run(run_id, progress_pct=pct)
        return

    # Final drain — loop until the queue is empty: the tail (DONE /
    # FINISHED_AT / ERROR) can still be buffered at the instant the process
    # exits, and a single bounded drain could leave it behind.
    while workers.drain(h):
        pass
    if not db_terminal:
        # Snapshot the log deque atomically (list() never observes a
        # concurrent append mid-iteration; generator-based scans over the
        # live deque can raise "deque mutated during iteration").
        lines = list(h.logs)
        # Last ERROR: line wins — engine/Ray noise that happens to start
        # with "ERROR:" must not mask the worker's own message (emitted last).
        err_line = next(
            (ln for ln in reversed(lines) if ln.startswith("ERROR:")), None,
        )
        finished_line = next(
            (ln for ln in reversed(lines) if ln.startswith("FINISHED_AT:")),
            None,
        )
        finished_at = finished_line.split(":", 1)[1] if finished_line else None
        done_seen = any(ln.strip() == "DONE" for ln in lines)
        # Authoritative completion signal: the worker writes summary.json as its
        # last step. Trust it even if the "DONE" stdout line was lost to the
        # drain race (e.g. campaign children nobody re-syncs).
        summary_ok = (RUNS_DIR / run_id / "summary.json").exists()
        rc = h.return_code
        if rc == 0 and (done_seen or summary_ok):
            storage.update_run(run_id, status="success", progress_pct=100.0,
                                error_message="", finished_at=finished_at)
        elif err_line:
            storage.update_run(run_id, status="failed", error_message=err_line[6:],
                                finished_at=finished_at)
        elif rc is not None and rc < 0:
            # Killed by a signal with no worker-reported error — a cancel
            # (SIGTERM/SIGKILL on the process group), not a real failure.
            storage.update_run(run_id, status="cancelled",
                                error_message=f"terminated by signal {-rc}",
                                finished_at=finished_at)
        else:
            storage.update_run(run_id, status="failed",
                                error_message=f"exit code {rc}",
                                finished_at=finished_at)
    # The run reached a terminal state and was fully drained: persist the
    # log tail and release the handle (otherwise _REGISTRY grows for the
    # whole server lifetime).
    _persist_worker_log(run_id, h)
    workers.discard(run_id)


def _run_age_seconds(r: dict[str, Any]) -> float:
    try:
        created = datetime.fromisoformat(r["created_at"])
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - created).total_seconds()
    except (KeyError, TypeError, ValueError):
        return float("inf")  # unparsable timestamp — don't block reconciliation


def _reconcile_orphan_run(r: dict[str, Any]) -> None:
    """Settle a running/pending run that has no in-memory worker handle.

    Happens after a Streamlit restart: the subprocess registry is gone, but
    the worker may have finished and written ``summary.json`` to disk. Trust
    the disk artifact; otherwise flag the run as failed instead of leaving
    it "running" forever.
    """
    run_id = r["id"]
    if r["status"] == "pending" and _run_age_seconds(r) < _ORPHAN_GRACE_S:
        return  # just created — its spawn may still be in flight
    summary_path = RUNS_DIR / run_id / "summary.json"
    if not summary_path.exists():
        # Workers run in their own process group and survive a UI restart on
        # purpose — a missing handle does not mean a dead worker. Only declare
        # failure once the recorded PID is gone.
        pid = _read_worker_pid(run_id)
        if pid is not None and workers.pid_alive(pid):
            return  # still computing; summary.json will settle it later
    if summary_path.exists():
        finished_at = None
        try:
            meta = json.loads(summary_path.read_text(encoding="utf-8"))
            finished_at = (meta.get("timing") or {}).get("finished_at")
        except (OSError, json.JSONDecodeError):
            pass
        storage.update_run(run_id, status="success", progress_pct=100.0,
                            error_message="", finished_at=finished_at)
    else:
        storage.update_run(run_id, status="failed",
                            error_message="worker handle lost (UI restart?)")


def sync_all_running() -> None:
    for r in storage.list_runs(limit=500):
        if r["status"] not in ("running", "pending"):
            continue
        if workers.get(r["id"]) is not None:
            sync_handle_to_db(r["id"])
        else:
            _reconcile_orphan_run(r)
    # Sweep finished handles whose run is already terminal in the DB (e.g.
    # cancelled from the Runs page) — sync_handle_to_db drains, persists the
    # log tail and discards them; without this they'd never be released.
    for rid in workers.registered_ids():
        h = workers.get(rid)
        if h is None or not h.finished:
            continue
        run = storage.get_run(rid)
        if run is None:
            workers.discard(rid)  # run row deleted — nothing left to sync
        elif run.get("status") in _TERMINAL_STATES:
            sync_handle_to_db(rid)
