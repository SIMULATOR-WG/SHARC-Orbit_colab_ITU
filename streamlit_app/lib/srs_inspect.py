"""srs_inspect.py — list notices (ntc_ids) and PFD masks in an MDB.

Delegates to `src.srs_reader.list_non_geo_systems` and
`src.srs_reader.list_pfd_masks_from_mask_mdb`. Caches results in-memory,
keyed by absolute path **plus** (st_mtime_ns, st_size) — a file replaced
at the same path busts the cache instead of serving stale data.
"""
from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Any

from . import engine  # noqa: F401 — ensures sys.path includes REPO_ROOT


def _file_sig(path: str | None) -> tuple[int, int] | None:
    """(st_mtime_ns, st_size) cache-key component for file freshness.

    ``None`` for a missing/unstatable file — still distinct from any real
    signature, so the cache refreshes when the file (re)appears.
    """
    if not path:
        return None
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def list_notices(mdb_path: str) -> list[dict[str, Any]]:
    """Return notices in an SRS MDB. Empty list when the path is not an MDB."""
    return _list_notices(mdb_path, _file_sig(mdb_path))


@functools.lru_cache(maxsize=64)
def _list_notices(mdb_path: str, _sig) -> list[dict[str, Any]]:
    p = Path(mdb_path)
    if not p.exists() or p.suffix.lower() not in (".mdb", ".accdb"):
        return []
    from src.srs_reader import list_non_geo_systems  # type: ignore[import]
    try:
        return list_non_geo_systems(str(p))
    except Exception:  # noqa: BLE001
        return []


def list_masks_in_srs(mdb_path: str, ntc_id: str | None) -> list[dict[str, Any]]:
    """List PFD masks (f_mask='P') in the **SRS** MDB (mask_info table) for one notice."""
    return _list_masks_in_srs(mdb_path, ntc_id, _file_sig(mdb_path))


@functools.lru_cache(maxsize=64)
def _list_masks_in_srs(mdb_path: str, ntc_id: str | None, _sig) -> list[dict[str, Any]]:
    p = Path(mdb_path)
    if not p.exists() or p.suffix.lower() not in (".mdb", ".accdb"):
        return []
    from src.srs_reader import read_mask_info  # type: ignore[import]
    try:
        items = read_mask_info(str(p), ntc_id=ntc_id)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for m in items:
        if m.f_mask != "P":
            continue
        out.append({
            "mask_id": int(m.mask_id),
            "freq_min_ghz": float(m.freq_min_ghz),
            "freq_max_ghz": float(m.freq_max_ghz),
            "label": f"mask_id {int(m.mask_id)} · {m.freq_min_ghz:.2f}-{m.freq_max_ghz:.2f} GHz",
        })
    return out


def list_masks_in_mask_mdb(mask_mdb_path: str, ntc_id: str | None) -> list[dict[str, Any]]:
    """List PFD masks in a separate MASK MDB for one notice."""
    return _list_masks_in_mask_mdb(mask_mdb_path, ntc_id, _file_sig(mask_mdb_path))


@functools.lru_cache(maxsize=64)
def _list_masks_in_mask_mdb(mask_mdb_path: str, ntc_id: str | None, _sig) -> list[dict[str, Any]]:
    p = Path(mask_mdb_path)
    if not p.exists() or p.suffix.lower() not in (".mdb", ".accdb"):
        return []
    from src.srs_reader import list_pfd_masks_from_mask_mdb  # type: ignore[import]
    try:
        items = list_pfd_masks_from_mask_mdb(str(p), ntc_id=ntc_id)
    except Exception:  # noqa: BLE001
        # fallback without ntc filter
        try:
            return list_pfd_masks_from_mask_mdb(str(p), None)
        except Exception:  # noqa: BLE001
            return []
    return items


# Callers (e.g. the Uploads re-scan button) clear caches through the public
# wrappers; forward cache_clear to the underlying lru_cache'd functions.
list_notices.cache_clear = _list_notices.cache_clear  # type: ignore[attr-defined]
list_masks_in_srs.cache_clear = _list_masks_in_srs.cache_clear  # type: ignore[attr-defined]
list_masks_in_mask_mdb.cache_clear = _list_masks_in_mask_mdb.cache_clear  # type: ignore[attr-defined]


def list_masks_for_filing(srs_path: str, mask_path: str | None,
                           ntc_id: str | None) -> list[dict[str, Any]]:
    """Best-effort mask listing.

    Order:
        1. If `mask_path` is a separate `.mdb`, list from it (filtered by ntc_id).
        2. Else, list from the SRS `mask_info` table.
        3. If `mask_path` is `.xml`, return a single synthetic entry.
    """
    if mask_path and Path(mask_path).suffix.lower() == ".xml":
        return [{
            "mask_id": None, "source": "xml",
            "freq_min_ghz": None, "freq_max_ghz": None,
            "label": f"xml · {Path(mask_path).name}",
        }]
    if mask_path and Path(mask_path).suffix.lower() in (".mdb", ".accdb"):
        items = list_masks_in_mask_mdb(mask_path, ntc_id)
        if items:
            return items
    return list_masks_in_srs(srs_path, ntc_id)


def count_satellites(mdb_path: str, ntc_id: str | None) -> int:
    """Total satellites of the notice, summed over the orbit table (`nbr_sat_pl`).

    Returns 0 when the MDB cannot be parsed.
    """
    return _count_satellites(mdb_path, ntc_id, _file_sig(mdb_path))


@functools.lru_cache(maxsize=64)
def _count_satellites(mdb_path: str, ntc_id: str | None, _sig) -> int:
    p = Path(mdb_path)
    if not p.exists() or p.suffix.lower() not in (".mdb", ".accdb"):
        return 0
    from src.srs_reader import read_srs_mdb  # type: ignore[import]
    try:
        system = read_srs_mdb(str(p), ntc_id=ntc_id)
    except Exception:  # noqa: BLE001
        return 0
    if getattr(system, "orbit_planes", None):
        return int(sum(p.nbr_sat_pl for p in system.orbit_planes))
    # Fallback: nbr_sat_td is the notice's TOTAL active satellite count
    # (density field — not per-plane), so use it directly; nbr_plane is a
    # last resort lower bound when the total is absent.
    total = int(getattr(system, "nbr_sat_total", 0) or 0)
    if total > 0:
        return total
    return int(getattr(system, "nbr_planes", 0) or 0)


def orbital_params(mdb_path: str, ntc_id: str | None) -> dict[str, Any]:
    """Return the constellation's orbital parameters for one notice.

    Output shape::

        {
            "ntc_id": "...",
            "sat_name": "...",
            "nbr_planes": int,
            "nbr_sat_total": int,
            "x_zone_deg": float | None,
            "planes": [
                {
                    "orb_id": int,
                    "nbr_sat_pl": int,
                    "semi_major_axis_km": float,
                    "altitude_km": float,
                    "apogee_km": float,
                    "perigee_km": float,
                    "eccentricity": float,
                    "inclin_deg": float,
                    "right_asc_deg": float,
                    "perigee_arg_deg": float,
                    "period_s": float,
                    "period_min": float,
                    "long_asc_deg": float,
                    "precession_deg_day": float,
                    "sun_synch": bool,
                    "station_keep": bool,
                },
                ...
            ],
        }

    Returns an empty payload ``{"planes": [], ...}`` when the MDB cannot
    be parsed.
    """
    return _orbital_params(mdb_path, ntc_id, _file_sig(mdb_path))


@functools.lru_cache(maxsize=64)
def _orbital_params(mdb_path: str, ntc_id: str | None, _sig) -> dict[str, Any]:
    empty = {"ntc_id": ntc_id, "sat_name": None, "nbr_planes": 0,
              "nbr_sat_total": 0, "x_zone_deg": None, "planes": []}
    p = Path(mdb_path)
    if not p.exists() or p.suffix.lower() not in (".mdb", ".accdb"):
        return empty
    from src.srs_reader import read_srs_mdb  # type: ignore[import]
    try:
        system = read_srs_mdb(str(p), ntc_id=ntc_id)
    except Exception:  # noqa: BLE001
        return empty
    planes = []
    for op in getattr(system, "orbit_planes", []) or []:
        planes.append({
            "orb_id": int(op.orb_id),
            "nbr_sat_pl": int(op.nbr_sat_pl),
            "semi_major_axis_km": float(op.semi_major_axis_km),
            "altitude_km": float(op.altitude_km),
            "apogee_km": float(op.apogee_km),
            "perigee_km": float(op.perigee_km),
            "op_height_km": float(op.op_height_km),
            "eccentricity": float(op.eccentricity),
            "inclin_deg": float(op.inclin_deg),
            "right_asc_deg": float(op.right_asc_deg),
            "perigee_arg_deg": float(op.perigee_arg_deg),
            "period_s": float(op.period_s),
            "period_min": float(op.period_s) / 60.0,
            "rpt_period_s": float(op.rpt_period_s),
            "long_asc_deg": float(op.long_asc_deg),
            "precession_deg_day": float(op.precession_deg_day),
            "sun_synch": bool(op.f_sun_synch),
            "station_keep": bool(op.f_stn_keep),
        })
    return {
        "ntc_id": getattr(system, "ntc_id", ntc_id),
        "sat_name": getattr(system, "sat_name", None),
        "nbr_planes": int(getattr(system, "nbr_planes", len(planes)) or 0),
        "nbr_sat_total": int(sum(p["nbr_sat_pl"] for p in planes)),
        "x_zone_deg": (
            float(getattr(system, "x_zone_deg", 0.0))
            if getattr(system, "f_x_zone", False) else None
        ),
        "planes": planes,
    }


_MASK_TYPE = {"P": "PFD", "E": "EIRP", "S": "Other"}
_EMI_RCP = {"E": "Tx (emit)", "R": "Rx (receive)"}


def frequency_bands(mdb_path: str, ntc_id: str | None) -> dict[str, Any]:
    """Return the operating bands declared in an SRS MDB for one notice.

    Combines two views:

    * **Masks** — every row in the ``mask_info`` table for this notice.
      Each carries `(mask_id, type, freq_min_ghz, freq_max_ghz)` where
      `type ∈ {"PFD", "EIRP", "Other"}` (from the `f_mask` flag).
    * **Groups** — every emission/reception group from the ``grp`` table
      for this notice, with `(grp_id, emi_rcp, beam_name, freq_min_ghz,
      freq_max_ghz, elev_min_deg)`. `emi_rcp` reports Tx vs Rx side.

    Returns ``{"masks": [], "groups": []}`` on a parse failure (engine
    raises) or missing tables.
    """
    return _frequency_bands(mdb_path, ntc_id, _file_sig(mdb_path))


@functools.lru_cache(maxsize=64)
def _frequency_bands(mdb_path: str, ntc_id: str | None, _sig) -> dict[str, Any]:
    out: dict[str, Any] = {"masks": [], "groups": []}
    p = Path(mdb_path)
    if not p.exists() or p.suffix.lower() not in (".mdb", ".accdb"):
        return out

    # ── masks ────────────────────────────────────────────────────────
    try:
        from src.srs_reader import read_mask_info  # type: ignore[import]
        masks = read_mask_info(str(p), ntc_id=ntc_id)
    except Exception:  # noqa: BLE001
        masks = []
    for m in masks:
        out["masks"].append({
            "mask_id": int(m.mask_id),
            "type": _MASK_TYPE.get(m.f_mask, m.f_mask or "?"),
            "freq_min_ghz": float(m.freq_min_ghz),
            "freq_max_ghz": float(m.freq_max_ghz),
            "subtype": m.f_mask_type or "—",
        })

    # ── groups (emission/reception bands) ────────────────────────────
    try:
        from src.srs_reader import _run_mdb_export, _parse_int, _parse_float  # type: ignore[import]
        rows_grp = _run_mdb_export(str(p), "grp")
    except Exception:  # noqa: BLE001
        rows_grp = []
    ntc_f = str(ntc_id).strip().strip('"') if ntc_id else None
    for row in rows_grp or []:
        row_ntc = row.get("ntc_id", "").strip().strip('"')
        if ntc_f and row_ntc and row_ntc != ntc_f:
            continue
        try:
            fmin_mhz = _parse_float(row.get("freq_min", ""), default=0.0)
            fmax_mhz = _parse_float(row.get("freq_max", ""), default=0.0)
            elev = _parse_float(row.get("elev_min", ""), default=float("nan"))
            emi = row.get("emi_rcp", "").strip().strip('"').upper()
            out["groups"].append({
                "grp_id": _parse_int(row.get("grp_id", "0")),
                "emi_rcp": _EMI_RCP.get(emi, emi or "?"),
                "beam_name": row.get("beam_name", "").strip().strip('"') or "—",
                "freq_min_ghz": (fmin_mhz / 1000.0) if fmin_mhz > 0 else None,
                "freq_max_ghz": (fmax_mhz / 1000.0) if fmax_mhz > 0 else None,
                "elev_min_deg": elev if elev == elev else None,  # NaN check
            })
        except Exception:  # noqa: BLE001
            continue

    # Sort groups by emi_rcp then freq_min then grp_id for readability
    out["groups"].sort(key=lambda g: (
        g["emi_rcp"] or "z",
        g["freq_min_ghz"] if g["freq_min_ghz"] is not None else 9e9,
        g["grp_id"],
    ))
    return out


def system_label(*, label: str, ntc_id: str | None,
                  sat_name: str | None, mask_id: int | None) -> str:
    parts = [label or "filing"]
    if ntc_id:
        parts.append(f"ntc {ntc_id}")
    if sat_name:
        parts.append(sat_name)
    if mask_id is not None:
        parts.append(f"mask {mask_id}")
    return " · ".join(parts)


def multi_config_info(mdb_path: str, ntc_id: str | None) -> dict[str, Any]:
    """Mutually-exclusive configuration declaration of a filing (R2).

    Light-weight (raw table export, no full SRS parse). Returns::

        {"type": "S"|"M"|"", "nbr_config": int,
         "config_label": int|None, "source": str|None, "is_multi": bool}
    """
    return _multi_config_info(str(mdb_path), ntc_id, _file_sig(mdb_path))


@functools.lru_cache(maxsize=128)
def _multi_config_info(mdb_path: str, ntc_id: str | None, _sig) -> dict[str, Any]:
    from src.srs_reader import _run_mdb_export, detect_orbit_config_raw  # noqa: PLC0415

    def _clean(v: Any) -> str:
        return str(v or "").strip().strip('"')

    try:
        ng_rows = _run_mdb_export(mdb_path, "non_geo")
    except Exception:  # noqa: BLE001
        ng_rows = []
    row = None
    for r in ng_rows or []:
        if ntc_id is None or _clean(r.get("ntc_id")) == str(ntc_id):
            row = r
            break
    if row is None:
        return {"type": "", "nbr_config": 0, "config_label": None,
                "source": None, "is_multi": False}

    mct = _clean(row.get("multi_config_type")).upper()
    try:
        nbr = int(_clean(row.get("nbr_config")) or 0)
    except ValueError:
        nbr = 0

    set_ids: set[int] = set()
    try:
        for orb in _run_mdb_export(mdb_path, "orbit") or []:
            if ntc_id is not None and _clean(orb.get("ntc_id")) not in ("", str(ntc_id)):
                continue
            try:
                set_ids.add(int(_clean(orb.get("orbit_set_id")) or 0))
            except ValueError:
                pass
    except Exception:  # noqa: BLE001
        pass

    out = detect_orbit_config_raw(mdb_path, mct, nbr, set_ids)
    out["type"] = mct
    return out
