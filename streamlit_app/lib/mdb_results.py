"""mdb_results.py — parse a results .mdb (Access) into CCDF curves for overlay.

Implements the "Compare external MDB" feature: reads the ``cdf`` table
(epfd vs calc_percentage per result_id) and, when present, the ``results``
table for human-readable labels. Reads the MDB via the engine's
``_run_mdb_export`` helper (pure-Python ``access_parser``), the same parser
path as the SRS reader.
"""
from __future__ import annotations

import os
import tempfile
from typing import Any


def _to_float(x: Any) -> float | None:
    try:
        return float(str(x).strip())
    except (ValueError, TypeError, AttributeError):
        return None


def _to_bool(x: Any) -> bool | None:
    """Coerce the Access ``pass`` Boolean (bool / '0'/'1' / 'True'/'False')."""
    if isinstance(x, bool):
        return x
    s = str(x).strip().lower()
    if s in ("1", "true", "yes"):
        return True
    if s in ("0", "false", "no", ""):
        return False
    return None


def _label(rid: str, meta: dict[str, Any] | None, source_label: str) -> str:
    parts = [f"ID {rid}"]
    if meta:
        et = (str(meta.get("epfd_type") or "")).strip()
        fr = _to_float(meta.get("freq_used"))
        ds = _to_float(meta.get("dish_size"))
        if et:
            parts.append(et)
        if fr:
            # freq_used is in MHz in SRS results; show GHz.
            fr_ghz = fr / 1000.0 if fr > 100.0 else fr
            parts.append(f"{fr_ghz:g} GHz")
        if ds:
            parts.append(f"Ø{ds:g} m")
    base = " · ".join(parts)
    return f"{source_label} · {base}" if source_label else base


def parse_results_mdb(data: bytes, *, source_label: str = "") -> list[dict[str, Any]]:
    """Parse uploaded ``.mdb`` bytes into CCDF curves.

    Each curve: ``{result_id, label, epfd: [...], percent: [...],
    limit_percent: [...]}``, ready to append to ``plots.ccdf_chart`` series.
    Raises ``RuntimeError`` with a clear message when the file has no usable
    ``cdf`` data.
    """
    from src.srs_reader import _run_mdb_export  # type: ignore[import]

    with tempfile.NamedTemporaryFile(suffix=".mdb", delete=False) as tf:
        tf.write(data)
        path = tf.name
    try:
        cdf_rows = _run_mdb_export(path, "cdf")
        if not cdf_rows:
            raise RuntimeError(
                "No 'cdf' table found (or it is empty). Is this a results MDB?"
            )
        results_rows = _run_mdb_export(path, "results") or []
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    meta: dict[str, dict[str, Any]] = {}
    for r in results_rows:
        rid = str(r.get("result_id", "")).strip()
        if rid:
            meta[rid] = r

    groups: dict[str, list[dict[str, Any]]] = {}
    for r in cdf_rows:
        rid = str(r.get("result_id", "")).strip() or "?"
        groups.setdefault(rid, []).append(r)

    curves: list[dict[str, Any]] = []
    for rid, rows in groups.items():
        rows.sort(key=lambda x: _to_float(x.get("sequence")) or 0.0)
        epfd: list[float] = []
        percent: list[float] = []
        limit: list[float | None] = []
        for x in rows:
            e = _to_float(x.get("epfd"))
            p = _to_float(x.get("calc_percentage"))
            if e is None or p is None:
                continue
            epfd.append(e)
            percent.append(p)
            limit.append(_to_float(x.get("limit_percentage")))
        if not epfd:
            continue
        m = meta.get(rid) or {}
        # Worst-case geometry (table `results`): ES position + GSO longitude.
        la = _to_float(m.get("worst_es_lat"))
        lo = _to_float(m.get("worst_es_long"))
        gl = _to_float(m.get("worst_gso_long"))
        wcg = (
            {"es_lat_deg": la, "es_lon_deg": lo, "gso_lon_deg": gl}
            if None not in (la, lo, gl) else None
        )
        ntc = str(m.get("ntc_id") or "").strip() or None
        _fr = _to_float(m.get("freq_used"))
        curves.append({
            "result_id": rid,
            "label": _label(rid, meta.get(rid), source_label),
            "epfd": epfd,
            "percent": percent,
            "limit_percent": limit,
            "wcg": wcg,
            "ntc_id": ntc,
            "pass": _to_bool(m.get("pass")),
            # Run parameters the ITU sw recorded in `results` (shown on overlay).
            "params": {
                "fine_timestep_s": _to_float(m.get("fine_timestep_used")),
                # ITU spells the column "course" (sic) — coarse step.
                "coarse_timestep_s": _to_float(m.get("course_timestep_used")),
                "num_timesteps": _to_float(m.get("number_timesteps")),
                # ITU stores reference_bandwidth in MHz (like freq_used) → kHz.
                "reference_bandwidth_khz": (
                    _rb * 1000.0
                    if (_rb := _to_float(m.get("reference_bandwidth"))) is not None
                    else None
                ),
                "beamwidth_deg": _to_float(m.get("beamwidth")),
                "gain_pattern": (str(m.get("gain_pattern") or "").strip() or None),
                "service": (str(m.get("service") or "").strip() or None),
                "dish_size_m": _to_float(m.get("dish_size")),
                "freq_ghz": (_fr / 1000.0 if _fr and _fr > 100.0 else _fr),
                "pct_complete": _to_float(m.get("percentage_complete")),
            },
        })

    if not curves:
        raise RuntimeError("MDB parsed but no usable CCDF curve was found.")
    return curves
