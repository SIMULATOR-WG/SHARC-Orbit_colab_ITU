"""Selection of Resolution 76 (Rev.WRC-23) aggregate epfd_down limits (Tables 1A-1D).

These limits apply to the AGGREGATE epfd produced by multiple non-GSO FSS
systems (see S.1588). They are separate from the single-entry Article 22
limits (selected in :mod:`src.article22_tables`).

The curve points live in ``data/resolution76_limits.json`` for easier auditing.

Curve format: ``[[epfd_dB, percent_time_exceeded], ...]`` (CCDF, ascending pct) —
same convention as ``data/article22_limits.json``. The PDF tables are published
as "% time NOT exceeded"; we store ``pct = 100 - pct_not_exceeded``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.article22_tables import (
    _applies_22_5c4 as _ramp_applies_table1a,
    _applies_22_5c8 as _ramp_applies_table1d,
    _rr_22_5c_lat_limit_db,
    _rr_22_5c_lat_limit_db_vec,
)

logger = logging.getLogger(__name__)

_DATA_PATH = Path(__file__).resolve().parent / "data" / "resolution76_limits.json"


def _load_tables() -> dict[str, dict[str, Any]]:
    with _DATA_PATH.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)
    return {k: v for k, v in raw.items() if not k.startswith("_")}


RESOLUTION76_TABLES: dict[str, dict[str, Any]] = _load_tables()


def _band_range(band: Any) -> tuple[float, float]:
    if isinstance(band, dict):
        lo, hi = band["range"]
        return float(lo), float(hi)
    return float(band[0]), float(band[1])


def _band_regions(band: Any) -> list[int]:
    if isinstance(band, dict):
        regs = band.get("regions")
        if regs:
            return [int(r) for r in regs]
    return [1, 2, 3]


def _band_contains(freq_ghz: float, band: Any) -> bool:
    lo, hi = _band_range(band)
    return lo - 1e-9 <= float(freq_ghz) <= hi + 1e-9


def _select_table_reference(freq_ghz: float, service: str) -> str | None:
    svc = str(service or "FSS").upper()
    if svc == "BSS":
        if any(_band_contains(freq_ghz, b) for b in RESOLUTION76_TABLES["Resolution 76, TABLE 1D"]["bands"]):
            return "Resolution 76, TABLE 1D"
        return None
    for ref in ("Resolution 76, TABLE 1A", "Resolution 76, TABLE 1B", "Resolution 76, TABLE 1C"):
        if any(_band_contains(freq_ghz, b) for b in RESOLUTION76_TABLES[ref]["bands"]):
            return ref
    return None


def _has_lat_dep_limit(rr_reference: str, rf_diam_cm: float, bw_khz: float) -> str | None:
    """Returns "Res76.1A.fn2" / "Res76.1D.fn1" / None.

    Same -160/-165.3 ramp as Article 22 22.5C.4/22.5C.8.
    """
    ref = str(rr_reference or "").strip()
    if abs(float(bw_khz) - 40.0) > 1e-6:
        return None
    if ref == "Resolution 76, TABLE 1A" and float(rf_diam_cm) > 60.0 + 1e-6:
        return "Res76.1A.fn2"
    if ref == "Resolution 76, TABLE 1D" and float(rf_diam_cm) >= 180.0 - 1e-6:
        return "Res76.1D.fn1"
    return None


def lat_limit_db(lat_deg):
    """100%-time aggregate epfd ramp from Tables 1A fn2 / 1D fn1.

    Same numerical ramp as Article 22 (22.5C.4 / 22.5C.8); reuses the helper to
    avoid duplicating the formula.
    """
    try:
        import numpy as np
        if isinstance(lat_deg, np.ndarray):
            return _rr_22_5c_lat_limit_db_vec(lat_deg)
    except ImportError:
        pass
    return _rr_22_5c_lat_limit_db(float(lat_deg))


def select_resolution76_limits(
    *,
    freq_ghz: float,
    antenna_diameter_m: float,
    service: str,
    bw_khz: float = 40.0,
) -> dict[str, Any] | None:
    """Pick the aggregate mask matching (freq, service, antenna, BW).

    Diameter selection: largest declared D <= effective antenna; if none,
    smallest declared D (most strict). Same rule used for Article 22.
    """
    ref = _select_table_reference(freq_ghz=freq_ghz, service=service)
    if ref is None:
        return None

    table = RESOLUTION76_TABLES[ref]
    masks = list(table["masks"])
    available_bws = sorted({float(m["bw_khz"]) for m in masks if m.get("bw_khz") is not None})
    if not available_bws:
        return None

    bw_target = min(available_bws, key=lambda v: abs(v - float(bw_khz)))
    masks_bw = [m for m in masks if abs(float(m["bw_khz"]) - bw_target) < 1e-9]
    ant_cm = float(antenna_diameter_m) * 100.0
    qualified = [m for m in masks_bw if float(m["d_cm"]) <= ant_cm + 1e-6]
    chosen = (
        max(qualified, key=lambda m: float(m["d_cm"]))
        if qualified
        else min(masks_bw, key=lambda m: float(m["d_cm"]))
    )

    rf_diam_cm = float(chosen["d_cm"])
    bw_sel = float(chosen["bw_khz"])
    return {
        "limits": [list(p) for p in chosen["curve"]],
        "reference_bandwidth_khz": bw_sel,
        "rr_reference": ref,
        "rf_diam_cm": rf_diam_cm,
        "rf_pattern_rr": str(chosen["pattern"]),
        "lat_dependent_limit_note": _has_lat_dep_limit(ref, rf_diam_cm, bw_sel),
    }


def apply_resolution76_limits_to_config(config: dict) -> None:
    """Populate ``config["resolution76_limits"]`` from the active filing config.

    Reads the same sources as :func:`apply_article22_limits_to_config`:
      * service / antenna diameter from ``gso_es``
      * frequency from ``pfd_mask.simulation_frequency_ghz`` or ``non_gso.frequency_ghz``
      * reference bandwidth from ``article22_limits.reference_bandwidth_khz``
        (defaulting to 40 kHz) — kept aligned so single-entry and aggregate use
        the same BW unless the caller overrides explicitly.
    """
    ngso = config.setdefault("non_gso", {})
    gso_es = config.setdefault("gso_es", {})
    pfd_cfg = config.setdefault("pfd_mask", {})
    art22 = config.get("article22_limits", {}) or {}
    res76 = config.setdefault("resolution76_limits", {})

    service = str(gso_es.get("service", "FSS")).upper()
    antenna_diameter_m = float(gso_es.get("antenna_diameter_m", 1.2) or 1.2)
    ref_bw_khz = float(
        res76.get("reference_bandwidth_khz")
        or art22.get("reference_bandwidth_khz")
        or 40.0
    )
    freq_ghz_raw = pfd_cfg.get("simulation_frequency_ghz")
    if freq_ghz_raw is None:
        freq_ghz_raw = ngso.get("frequency_ghz")
    freq_ghz = float(freq_ghz_raw or 0.0)

    selected = select_resolution76_limits(
        freq_ghz=freq_ghz,
        antenna_diameter_m=antenna_diameter_m,
        service=service,
        bw_khz=ref_bw_khz,
    )
    if selected is None:
        logger.warning(
            "No Resolution 76 table found for f=%.6f GHz, service=%s, D=%.3f m",
            freq_ghz, service, antenna_diameter_m,
        )
        return

    res76["limits"] = selected["limits"]
    res76["reference_bandwidth_khz"] = selected["reference_bandwidth_khz"]
    res76["rr_reference"] = selected["rr_reference"]
    res76["_epfd_rf_diam_cm"] = selected["rf_diam_cm"]
    res76["_epfd_rf_pattern_rr"] = selected["rf_pattern_rr"]
    res76["lat_dependent_limit_note"] = selected["lat_dependent_limit_note"]

    logger.info(
        "Selected Resolution 76 aggregate limits: %s, BW=%.0f kHz, Dref=%.0f cm, pattern=%s",
        selected["rr_reference"],
        float(selected["reference_bandwidth_khz"]),
        float(selected["rf_diam_cm"]),
        selected["rf_pattern_rr"],
    )
    if selected["lat_dependent_limit_note"]:
        logger.info(
            "  Note %s applicable: 100%%-time aggregate threshold varies with latitude "
            "(-160 dB up to 57.5; ramp down to -165.3 dB from 63.75).",
            selected["lat_dependent_limit_note"],
        )


def extract_resolution76_from_cfg(cfg: dict) -> dict | None:
    """Return the Res 76 payload after :func:`apply_resolution76_limits_to_config`."""
    res76 = cfg.get("resolution76_limits") if isinstance(cfg, dict) else None
    if not isinstance(res76, dict) or not res76.get("limits"):
        return None
    return {
        "limits": [list(p) for p in res76.get("limits", [])],
        "reference_bandwidth_khz": float(res76.get("reference_bandwidth_khz", 40.0) or 40.0),
        "rr_reference": str(res76.get("rr_reference", "") or ""),
        "rf_diam_cm": res76.get("_epfd_rf_diam_cm"),
        "rf_pattern_rr": res76.get("_epfd_rf_pattern_rr"),
        "lat_dependent_limit_note": res76.get("lat_dependent_limit_note"),
    }
