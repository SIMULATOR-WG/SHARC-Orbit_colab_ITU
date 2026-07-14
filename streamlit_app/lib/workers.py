"""workers.py — long-running simulation orchestration via subprocess.

Spawns a Python child that calls the engine on its own, with stdout/stderr
streamed back to a queue the UI page reads via st.fragment. Avoids blocking
Streamlit's script run.

Initial implementation: a register of background runs in-process. Runs survive
page reruns of the same Streamlit session. They DO NOT survive a Streamlit
restart — for that, a persistent job runner would be needed.
"""
from __future__ import annotations

import os
import queue
import signal
import subprocess
import sys
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import REPO_ROOT

# Cap on retained log lines per run. The engine emits \r progress fragments
# by the tens of thousands; an unbounded buffer would leak memory for the
# whole server lifetime. 5000 lines is plenty for the UI tail view.
MAX_LOG_LINES = 5000
# Bound on undrained queue entries (no page polling the run). Oldest lines
# are dropped first, mirroring the deque cap downstream.
_MAX_QUEUE_LINES = 20_000
# Grace before a POSIX cancel escalates from SIGTERM to SIGKILL.
_CANCEL_KILL_TIMEOUT_S = 5.0


class _LogBuffer(deque):
    """Bounded log deque that also supports list-style slicing
    (pages read tails via ``handle.logs[-300:]``)."""

    def __getitem__(self, item):  # type: ignore[override]
        if isinstance(item, slice):
            return list(self)[item]
        return super().__getitem__(item)


@dataclass
class RunHandle:
    run_id: str
    proc: subprocess.Popen
    log_queue: queue.Queue
    started_at: str
    cwd: Path
    finished: bool = False
    return_code: int | None = None
    logs: _LogBuffer = field(
        default_factory=lambda: _LogBuffer(maxlen=MAX_LOG_LINES)
    )


_REGISTRY: dict[str, RunHandle] = {}


def _reader(handle: RunHandle) -> None:
    """Stream subprocess output. Treats both \\r and \\n as line terminators.

    The engine emits progress bars with \\r (terminal overwrite). With plain
    readline(), all the \\r updates pile up until the next \\n — so the UI
    sees one giant concatenated line. Here we read raw bytes and emit each
    \\r- or \\n-terminated fragment as a separate log entry.
    """
    raw = handle.proc.stdout
    if raw is None:
        return
    # Switch to binary if needed (text mode wraps a TextIOWrapper around the
    # buffered binary stream — read raw bytes from .buffer).
    buf_reader = getattr(raw, "buffer", None) or raw
    buf = b"" if buf_reader is not raw else ""
    is_bytes = isinstance(buf, bytes)

    def _emit(line):
        if is_bytes:
            line = line.decode("utf-8", errors="replace")
        if not line:
            return
        # Never block the reader thread: when nobody drains the queue,
        # drop the oldest entry instead of growing without bound.
        while True:
            try:
                handle.log_queue.put_nowait(line)
                return
            except queue.Full:
                try:
                    handle.log_queue.get_nowait()
                except queue.Empty:
                    pass

    while True:
        try:
            chunk = buf_reader.read(1024) if is_bytes else buf_reader.read(1024)
        except (OSError, ValueError):
            break
        if not chunk:
            if buf:
                _emit(buf)
            break
        buf += chunk
        sep_r = b"\r" if is_bytes else "\r"
        sep_n = b"\n" if is_bytes else "\n"
        while True:
            i_r = buf.find(sep_r)
            i_n = buf.find(sep_n)
            if i_r == -1 and i_n == -1:
                break
            if i_r == -1:
                idx = i_n
            elif i_n == -1:
                idx = i_r
            else:
                idx = min(i_r, i_n)
            piece = buf[:idx]
            buf = buf[idx + 1:]
            _emit(piece)
    handle.proc.wait()
    handle.return_code = handle.proc.returncode
    handle.finished = True


def spawn(
    run_id: str,
    *,
    args: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> RunHandle:
    """Start a subprocess. `args` is the command (e.g. ['python', '-m', 'mod', ...]).

    The subprocess inherits PYTHONPATH so that `import src.X` works from anywhere.
    """
    proc_env = os.environ.copy()
    proc_env.setdefault("PYTHONPATH", str(REPO_ROOT))
    if env:
        proc_env.update(env)
    # Isolate the worker (and the engine's multiprocessing.Pool children)
    # in its own process group/session so cancel() can take down the whole
    # tree — terminate() on the direct child alone leaves the Pool
    # grandchildren computing.
    popen_kwargs: dict[str, Any] = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(cwd or REPO_ROOT),
        env=proc_env,
        text=True,
        bufsize=1,
        **popen_kwargs,
    )
    handle = RunHandle(
        run_id=run_id,
        proc=proc,
        log_queue=queue.Queue(maxsize=_MAX_QUEUE_LINES),
        started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        cwd=cwd or REPO_ROOT,
    )
    t = threading.Thread(target=_reader, args=(handle,), daemon=True)
    t.start()
    _REGISTRY[run_id] = handle
    return handle


def pid_alive(pid: int) -> bool:
    """Best-effort liveness check for a worker PID (cross-platform).

    Used after a UI restart, when the in-memory handle is gone but the
    detached worker process may still be computing.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by someone else
    except OSError:
        return False


def get(run_id: str) -> RunHandle | None:
    return _REGISTRY.get(run_id)


def discard(run_id: str) -> None:
    """Drop a handle from the registry (called after its final DB sync,
    so terminated runs don't leak handles for the server's lifetime)."""
    _REGISTRY.pop(run_id, None)


def registered_ids() -> list[str]:
    """Snapshot of run_ids currently held in the in-memory registry."""
    return list(_REGISTRY.keys())


def drain(handle: RunHandle, max_lines: int = 200) -> list[str]:
    """Pop pending log lines and append to handle.logs."""
    out: list[str] = []
    for _ in range(max_lines):
        try:
            line = handle.log_queue.get_nowait()
        except queue.Empty:
            break
        handle.logs.append(line)
        out.append(line)
    return out


def cancel(run_id: str) -> bool:
    """Terminate a run's whole process tree.

    The engine spawns a ``multiprocessing.Pool`` with up to ``cpu_count``
    children; terminating only the direct child leaves those grandchildren
    computing. POSIX: signal the process group (the child was spawned with
    ``start_new_session=True``) with SIGTERM, escalating to SIGKILL after
    ~5 s. Windows: ``taskkill /T /F`` kills the full tree.
    """
    h = _REGISTRY.get(run_id)
    if h is None or h.finished:
        return False
    if os.name == "nt":
        return _cancel_tree_windows(h)
    return _cancel_tree_posix(h)


def _cancel_tree_windows(h: RunHandle) -> bool:
    try:
        res = subprocess.run(
            ["taskkill", "/PID", str(h.proc.pid), "/T", "/F"],
            capture_output=True, timeout=30,
        )
        if res.returncode != 0 and h.proc.poll() is None:
            # taskkill unavailable/refused — at least kill the direct child.
            h.proc.kill()
        return True
    except (OSError, subprocess.SubprocessError):
        try:
            h.proc.kill()
            return True
        except OSError:
            return False


def _cancel_tree_posix(h: RunHandle) -> bool:
    try:
        pgid = os.getpgid(h.proc.pid)
    except OSError:
        pgid = None
    try:
        if pgid is not None:
            os.killpg(pgid, signal.SIGTERM)
        else:
            h.proc.terminate()
    except OSError:
        try:
            h.proc.terminate()
        except OSError:
            return False

    def _escalate() -> None:
        try:
            h.proc.wait(timeout=_CANCEL_KILL_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            pass
        # Mop up any group member still alive (pool children that ignored
        # SIGTERM, or the child itself if it refused to exit).
        if pgid is not None:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except OSError:
                pass  # group already gone — nothing left to kill
        if h.proc.poll() is None:
            try:
                h.proc.kill()
            except OSError:
                pass

    threading.Thread(target=_escalate, daemon=True).start()
    return True
