"""engine.py — Thin wrappers around the numerical engine at ../src/.

Adds ../ to sys.path on import so `from src.s1588_studies import ...` works
regardless of where Streamlit is launched from. Never imports from backend/.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from . import REPO_ROOT

_repo = str(REPO_ROOT)
if _repo not in sys.path:
    sys.path.insert(0, _repo)


def s1588_module() -> Any:
    """Lazy import of `src.s1588_studies` (the engine namespace).

    Used as `mod = s1588_module(); mod.convolve_ccdfs_db(...)`. Lazy because
    numba/scipy/joblib are heavy to import — avoid paying that cost on every
    Streamlit re-render of pages that don't need the engine.
    """
    import src.s1588_studies as mod  # type: ignore[import]
    return mod


def epfd_calculator_module() -> Any:
    """Lazy import of `src.epfd_calculator`."""
    import src.epfd_calculator as mod  # type: ignore[import]
    return mod


def wcg_search_module() -> Any:
    import src.wcg_search as mod  # type: ignore[import]
    return mod


def antenna_module() -> Any:
    import src.antenna as mod  # type: ignore[import]
    return mod


def pfd_mask_module() -> Any:
    import src.pfd_mask as mod  # type: ignore[import]
    return mod


def srs_reader_module() -> Any:
    import src.srs_reader as mod  # type: ignore[import]
    return mod


def article22_module() -> Any:
    import src.article22_tables as mod  # type: ignore[import]
    return mod


def resolution76_module() -> Any:
    import src.resolution76_tables as mod  # type: ignore[import]
    return mod


def engine_health() -> dict[str, Any]:
    """Verify the engine is reachable. Returns a status payload for the UI."""
    info: dict[str, Any] = {"engine_root": str(REPO_ROOT / "src"), "ok": False, "modules": {}}
    try:
        mod = s1588_module()
        info["modules"]["s1588_studies"] = sorted(getattr(mod, "__all__", []))[:8]
        info["ok"] = True
    except Exception as exc:  # noqa: BLE001
        info["error"] = f"{type(exc).__name__}: {exc}"
    return info
