"""storage.py — SQLite + filesystem persistence (replaces Postgres + MinIO).

Tables (created on demand):
    uploads     — registered SRS filings (.mdb / .xml + metadata)
    runs        — simulation runs (params, status, paths to artifacts)
    campaigns   — multi-run campaigns

Artifacts (CCDF, sim_data, EPFD timeline, .czml) are stored on the filesystem
under ../data/runs/{run_id}/ and referenced by path in the `runs` table.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import DB_PATH, RUNS_DIR, UPLOADS_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS uploads (
    id              TEXT PRIMARY KEY,
    label           TEXT NOT NULL,
    network_name    TEXT,
    srs_path        TEXT NOT NULL,
    mask_path       TEXT,
    created_at      TEXT NOT NULL,
    metadata_json   TEXT
);

CREATE TABLE IF NOT EXISTS systems (
    id              TEXT PRIMARY KEY,           -- (upload_id):(ntc_id):(mask_id)
    upload_id       TEXT NOT NULL,
    ntc_id          TEXT,
    mask_id         INTEGER,
    sat_name        TEXT,
    admin           TEXT,
    label           TEXT,
    metadata_json   TEXT,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (upload_id) REFERENCES uploads(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_systems_upload ON systems(upload_id);

CREATE TABLE IF NOT EXISTS runs (
    id              TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,           -- s1503 | s1588 | campaign-child
    method          TEXT,                    -- method_1..method_5
    status          TEXT NOT NULL,           -- pending | running | success | failed | cancelled
    progress_pct    REAL DEFAULT 0,
    params_json     TEXT,
    result_path     TEXT,                    -- directory data/runs/{id}/
    campaign_id     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    finished_at     TEXT,                    -- worker-emitted termination time
    error_message   TEXT
);

CREATE TABLE IF NOT EXISTS campaigns (
    id              TEXT PRIMARY KEY,
    label           TEXT NOT NULL,
    params_json     TEXT,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at);
CREATE INDEX IF NOT EXISTS idx_runs_campaign ON runs(campaign_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Default display timezone for ISO timestamps shown in the UI.
# Storage stays in UTC; the helper below only converts at render time.
DISPLAY_TZ_NAME = "America/Sao_Paulo"


def fmt_local(
    iso_str: str | None,
    *,
    tz_name: str = DISPLAY_TZ_NAME,
    fallback: str = "—",
) -> str:
    """Render a UTC ISO timestamp in the local display timezone (default
    America/Sao_Paulo). Returns ``fallback`` for empty/invalid input.

    Output format: ``YYYY-MM-DD HH:MM:SS`` (no tz suffix — implied by the
    column header). Storage in the DB remains UTC; this only affects
    presentation.
    """
    if not iso_str:
        return fallback
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone(ZoneInfo(tz_name))
        return local.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:  # noqa: BLE001
        return iso_str  # last resort: show raw


_KIND_LEGACY_MAP = {"s1503": "single", "s1588": "aggregate"}


def display_kind(kind: str | None) -> str:
    if not kind:
        return "—"
    return _KIND_LEGACY_MAP.get(kind, kind)


def _conn() -> sqlite3.Connection:
    cx = sqlite3.connect(DB_PATH, isolation_level=None, timeout=10.0)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA journal_mode=WAL")
    cx.execute("PRAGMA foreign_keys=ON")
    return cx


def init_db() -> None:
    with _conn() as cx:
        cx.executescript(SCHEMA)
        # Forward migration for DBs created before finished_at existed.
        try:
            cols = {r["name"] for r in cx.execute("PRAGMA table_info(runs)")}
            if "finished_at" not in cols:
                cx.execute("ALTER TABLE runs ADD COLUMN finished_at TEXT")
        except sqlite3.OperationalError:
            pass


# ─── uploads ────────────────────────────────────────────────────────────────

def add_upload(
    *,
    label: str,
    srs_path: Path,
    mask_path: Path | None,
    network_name: str | None = None,
    metadata: dict[str, Any] | None = None,
    upload_id: str | None = None,
) -> str:
    init_db()
    if not upload_id:
        upload_id = uuid.uuid4().hex[:12]
    with _conn() as cx:
        cx.execute(
            "INSERT OR REPLACE INTO uploads "
            "(id, label, network_name, srs_path, mask_path, created_at, metadata_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                upload_id,
                label,
                network_name,
                str(srs_path),
                str(mask_path) if mask_path else None,
                _now(),
                json.dumps(metadata or {}),
            ),
        )
    return upload_id


def list_uploads() -> list[dict[str, Any]]:
    init_db()
    with _conn() as cx:
        rows = cx.execute(
            "SELECT * FROM uploads ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_upload(upload_id: str) -> dict[str, Any] | None:
    init_db()
    with _conn() as cx:
        row = cx.execute("SELECT * FROM uploads WHERE id=?", (upload_id,)).fetchone()
    return dict(row) if row else None


def set_upload_label(upload_id: str, label: str) -> None:
    """Rename a filing (uploads.label). The systems list joins this as
    ``upload_label``, so the new name shows everywhere immediately."""
    init_db()
    with _conn() as cx:
        cx.execute("UPDATE uploads SET label=? WHERE id=?", (label, upload_id))


def apply_filing_prefix(prefix: str) -> int:
    """Prepend ``prefix`` to every filing name that doesn't already start with
    it. Idempotent (re-applying the same prefix is a no-op). Returns the count
    of filings renamed."""
    prefix = (prefix or "").strip()
    if not prefix:
        return 0
    n = 0
    for up in list_uploads():
        label = up.get("label") or ""
        if not label.startswith(prefix):
            set_upload_label(up["id"], f"{prefix}{label}")
            n += 1
    return n


def delete_upload(upload_id: str, *, remove_files: bool = True) -> None:
    import shutil
    init_db()
    with _conn() as cx:
        cx.execute("DELETE FROM systems WHERE upload_id=?", (upload_id,))
        cx.execute("DELETE FROM uploads WHERE id=?", (upload_id,))
    if remove_files:
        from . import UPLOADS_DIR
        p = UPLOADS_DIR / upload_id
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)


def delete_uploads(upload_ids: list[str], *, remove_files: bool = True) -> int:
    n = 0
    for uid in upload_ids:
        delete_upload(uid, remove_files=remove_files)
        n += 1
    return n


def delete_all_uploads(*, remove_files: bool = True) -> int:
    init_db()
    with _conn() as cx:
        rows = cx.execute("SELECT id FROM uploads").fetchall()
    return delete_uploads([r["id"] for r in rows], remove_files=remove_files)


# ─── systems (one row per (upload_id, ntc_id, mask_id) tuple) ───────────────


def _system_id(upload_id: str, ntc_id: str | None, mask_id: int | None) -> str:
    return f"{upload_id}:{ntc_id or '_'}:{mask_id if mask_id is not None else '_'}"


def add_system(
    *,
    upload_id: str,
    ntc_id: str | None,
    mask_id: int | None,
    sat_name: str | None = None,
    admin: str | None = None,
    label: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    init_db()
    sid = _system_id(upload_id, ntc_id, mask_id)
    if not label:
        bits = [sat_name or "system"]
        if ntc_id:
            bits.append(f"ntc {ntc_id}")
        label = " · ".join(bits)
    with _conn() as cx:
        cx.execute(
            "INSERT OR REPLACE INTO systems "
            "(id, upload_id, ntc_id, mask_id, sat_name, admin, label, metadata_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, upload_id, ntc_id, mask_id, sat_name, admin, label,
             json.dumps(metadata or {}), _now()),
        )
    return sid


def list_systems(upload_id: str | None = None) -> list[dict[str, Any]]:
    init_db()
    with _conn() as cx:
        if upload_id:
            rows = cx.execute(
                "SELECT s.*, u.label AS upload_label, u.srs_path, u.mask_path "
                "FROM systems s JOIN uploads u ON u.id = s.upload_id "
                "WHERE s.upload_id=? ORDER BY s.created_at DESC",
                (upload_id,),
            ).fetchall()
        else:
            rows = cx.execute(
                "SELECT s.*, u.label AS upload_label, u.srs_path, u.mask_path "
                "FROM systems s JOIN uploads u ON u.id = s.upload_id "
                "ORDER BY s.created_at DESC"
            ).fetchall()
    return [dict(r) for r in rows]


def get_system(system_id: str) -> dict[str, Any] | None:
    init_db()
    with _conn() as cx:
        row = cx.execute(
            "SELECT s.*, u.label AS upload_label, u.srs_path, u.mask_path "
            "FROM systems s JOIN uploads u ON u.id = s.upload_id "
            "WHERE s.id=?",
            (system_id,),
        ).fetchone()
    return dict(row) if row else None


def delete_system(system_id: str) -> None:
    init_db()
    with _conn() as cx:
        cx.execute("DELETE FROM systems WHERE id=?", (system_id,))


def list_orphan_uploads() -> list[dict[str, Any]]:
    """Uploads that have no rows in systems."""
    init_db()
    with _conn() as cx:
        rows = cx.execute(
            "SELECT u.* FROM uploads u "
            "LEFT JOIN systems s ON s.upload_id = u.id "
            "WHERE s.id IS NULL ORDER BY u.created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def ensure_default_system(upload_id: str) -> str:
    """Create (if absent) a default system with (upload_id, None, None) and return its id."""
    existing = list_systems(upload_id=upload_id)
    if existing:
        return existing[0]["id"]
    up = get_upload(upload_id)
    label = up["label"] if up else "filing"
    return add_system(upload_id=upload_id, ntc_id=None, mask_id=None,
                      label=f"{label} (engine default)")


# ─── runs ───────────────────────────────────────────────────────────────────

def create_run(
    *,
    kind: str,
    method: str | None = None,
    params: dict[str, Any] | None = None,
    campaign_id: str | None = None,
) -> str:
    init_db()
    run_id = uuid.uuid4().hex[:12]
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    now = _now()
    with _conn() as cx:
        cx.execute(
            "INSERT INTO runs (id, kind, method, status, progress_pct, params_json, "
            "result_path, campaign_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                kind,
                method,
                "pending",
                0.0,
                json.dumps(params or {}),
                str(run_dir),
                campaign_id,
                now,
                now,
            ),
        )
    return run_id


def update_run(
    run_id: str,
    *,
    status: str | None = None,
    progress_pct: float | None = None,
    error_message: str | None = None,
    finished_at: str | None = None,
) -> None:
    init_db()
    fields: list[str] = []
    values: list[Any] = []
    if status is not None:
        fields.append("status=?")
        values.append(status)
    if progress_pct is not None:
        fields.append("progress_pct=?")
        values.append(float(progress_pct))
    if error_message is not None:
        fields.append("error_message=?")
        values.append(error_message)
    if finished_at is not None:
        fields.append("finished_at=?")
        values.append(finished_at)
    if not fields:
        return
    fields.append("updated_at=?")
    values.append(_now())
    values.append(run_id)
    with _conn() as cx:
        cx.execute(f"UPDATE runs SET {', '.join(fields)} WHERE id=?", values)


def delete_run(run_id: str, *, remove_files: bool = True) -> None:
    """Remove a run from the DB and (optionally) its files on disk."""
    import shutil
    init_db()
    with _conn() as cx:
        cx.execute("DELETE FROM runs WHERE id=?", (run_id,))
    if remove_files:
        from . import RUNS_DIR
        p = RUNS_DIR / run_id
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)


def delete_runs(run_ids: list[str], *, remove_files: bool = True) -> int:
    init_db()
    n = 0
    for rid in run_ids:
        delete_run(rid, remove_files=remove_files)
        n += 1
    return n


def delete_all_runs(*, status: str | None = None, remove_files: bool = True) -> int:
    """Delete every run (optionally filtered by status). Returns number deleted."""
    init_db()
    with _conn() as cx:
        if status:
            rows = cx.execute("SELECT id FROM runs WHERE status=?", (status,)).fetchall()
        else:
            rows = cx.execute("SELECT id FROM runs").fetchall()
    ids = [r["id"] for r in rows]
    return delete_runs(ids, remove_files=remove_files)


def list_runs(limit: int = 200, campaign_id: str | None = None) -> list[dict[str, Any]]:
    init_db()
    with _conn() as cx:
        if campaign_id:
            rows = cx.execute(
                "SELECT * FROM runs WHERE campaign_id=? ORDER BY created_at DESC LIMIT ?",
                (campaign_id, limit),
            ).fetchall()
        else:
            rows = cx.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


def get_run(run_id: str) -> dict[str, Any] | None:
    init_db()
    with _conn() as cx:
        row = cx.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    return dict(row) if row else None


def write_run_artifact(run_id: str, name: str, payload: bytes | str | dict[str, Any]) -> Path:
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    out = run_dir / name
    if isinstance(payload, dict):
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    elif isinstance(payload, str):
        out.write_text(payload, encoding="utf-8")
    else:
        out.write_bytes(payload)
    return out


def read_run_artifact(run_id: str, name: str) -> bytes | None:
    p = RUNS_DIR / run_id / name
    if not p.exists():
        return None
    return p.read_bytes()


def list_run_artifacts(run_id: str) -> list[str]:
    p = RUNS_DIR / run_id
    if not p.exists():
        return []
    return sorted(f.name for f in p.iterdir() if f.is_file())


# ─── campaigns ──────────────────────────────────────────────────────────────

def create_campaign(label: str, params: dict[str, Any] | None = None) -> str:
    init_db()
    cid = uuid.uuid4().hex[:12]
    with _conn() as cx:
        cx.execute(
            "INSERT INTO campaigns (id, label, params_json, created_at) VALUES (?, ?, ?, ?)",
            (cid, label, json.dumps(params or {}), _now()),
        )
    return cid


def list_campaigns(limit: int = 200) -> list[dict[str, Any]]:
    init_db()
    with _conn() as cx:
        rows = cx.execute(
            "SELECT * FROM campaigns ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_campaign(cid: str) -> dict[str, Any] | None:
    init_db()
    with _conn() as cx:
        row = cx.execute("SELECT * FROM campaigns WHERE id=?", (cid,)).fetchone()
    return dict(row) if row else None
