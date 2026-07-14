"""state.py — st.session_state helpers + disk persistence of form params.

Streamlit `st.session_state` lives only inside a session. To survive page
reloads and restarts, the small `last_form_*.json` snapshots are written to
data/. Heavy state (run metadata, artifacts) lives in SQLite (storage.py).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import streamlit as st

from . import DATA_ROOT


def _state_file(key: str) -> Path:
    return DATA_ROOT / f"state_{key}.json"


def load_persisted(key: str, default: Any) -> Any:
    p = _state_file(key)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_persisted(key: str, value: Any) -> None:
    try:
        _state_file(key).write_text(json.dumps(value, indent=2), encoding="utf-8")
    except OSError:
        pass


def use_persisted_state(key: str, default: Any) -> Any:
    """Persisted session state — bootstraps from disk, mirrors writes back to disk."""
    if key not in st.session_state:
        st.session_state[key] = load_persisted(key, default)
    return st.session_state[key]


def set_persisted_state(key: str, value: Any) -> None:
    st.session_state[key] = value
    save_persisted(key, value)


# ────────────────────────────────────────────────────────────────────────────
#  Cross-page selections — single source of truth so picks made on one page
#  carry over to the others (Single-entry ↔ Aggregate ↔ Mask Viewer ↔ Runs ↔
#  Status ↔ Results). Each selection is mirrored to disk via
#  ``set_persisted_state`` so it survives reloads.
# ────────────────────────────────────────────────────────────────────────────

_SEL_SYSTEM = "selection.system_id"
_SEL_SYSTEMS = "selection.system_ids"   # multi-select (Aggregate, Launcher)
_SEL_MASK = "selection.mask_id"
_SEL_RUN = "selection.run_id"


def current_system_id(default: str | None = None) -> str | None:
    return use_persisted_state(_SEL_SYSTEM, default)


def set_current_system_id(value: str | None) -> None:
    set_persisted_state(_SEL_SYSTEM, value)


def current_system_ids(default: list[str] | None = None) -> list[str]:
    return list(use_persisted_state(_SEL_SYSTEMS, default or []))


def set_current_system_ids(values: list[str]) -> None:
    set_persisted_state(_SEL_SYSTEMS, list(values or []))


def current_mask_id(default: int | None = None) -> int | None:
    v = use_persisted_state(_SEL_MASK, default)
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def set_current_mask_id(value: int | None) -> None:
    set_persisted_state(_SEL_MASK, int(value) if value is not None else None)


def current_run_id(default: str | None = None) -> str | None:
    return use_persisted_state(_SEL_RUN, default)


def set_current_run_id(value: str | None) -> None:
    set_persisted_state(_SEL_RUN, value)
