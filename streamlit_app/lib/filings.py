"""filings.py — Helpers around SRS filings (.mdb / .xml) registration.

Delegates parsing to the engine when present. Falls back to filename
heuristics when the engine cannot read the file.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import engine


def fallback_network_name(filename: str) -> str:
    base = re.sub(r"\.[^.]+$", "", filename).strip()
    for prefix in ("SRS_Exam-", "SRS-", "Exam-", "Mask-"):
        if base.startswith(prefix):
            base = base[len(prefix):]
    return base or "network"


def preview_srs(srs_path: Path) -> dict[str, Any]:
    """Best-effort preview of a .mdb / .xml filing.

    Returns a dict with at least {network_name, ok, error?}. Tries the engine
    first; falls back to filename heuristic.
    """
    out: dict[str, Any] = {"ok": False}
    try:
        srs = engine.srs_reader_module()
        listing = None
        for fn in ("list_filings", "list_networks", "list_non_geo_systems"):
            if hasattr(srs, fn):
                try:
                    listing = getattr(srs, fn)(str(srs_path))
                    break
                except TypeError:
                    listing = getattr(srs, fn)(str(srs_path), None)
                    break
                except Exception:
                    continue
        if listing:
            first = listing[0] if isinstance(listing, list) and listing else listing
            net = None
            if isinstance(first, dict):
                net = first.get("network_name") or first.get("sat_name") or first.get("ntc_name")
            out["ok"] = True
            out["network_name"] = net or fallback_network_name(srs_path.name)
            out["raw"] = first if isinstance(first, dict) else None
            return out
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
    out["network_name"] = fallback_network_name(srs_path.name)
    return out
