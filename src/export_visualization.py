"""
export_visualization.py — Exports WCG simulation data for CesiumJS.

Generates:
  1. satellites.czml  — positions over time (ECEF, ``referenceFrame: FIXED``)
  2. sim_data.json    — EPFD time series, alpha, counts, WCG, search trail
  3. Starts a local HTTP server for the visualization

Usage:
  python3 -m src.export_visualization \
      --config config.yaml --nsteps 1000 --serve --kill-port

  python3 -m src.export_visualization \
      --mdb "323520210 SRS.MDB" --pfd-xml Mask_id_1_PFD.xml \
      --mask-id 1 --nsteps 1000 --serve --port 8080 --kill-port
"""

from __future__ import annotations
import os
import sys
import json
import math
import numbers
import time
import errno
import signal
import logging
import argparse
import colorsys
import subprocess
from collections import defaultdict
import http.server
import numpy as np
from datetime import datetime, timedelta

from .constants import RE_KM, GSO_RADIUS_KM
from .coordinates import ecef_to_lla, eci_to_ecef, lla_to_ecef, gso_position_ecef
from .orbit_propagator import OrbitalElements, elements_to_eci
from .wcg_search import WCGResult, WCGSearchPoint
from .epfd_calculator import EPFDSimulationResult, ComplianceResult
from .pfd_mask import PFDMask
from .time_step import compute_orbital_period
from .orbit_tracks_parquet import (
    ORBIT_MANIFEST_NAME,
    append_sat_cart_as_rows,
    downsample_cartesian_epoch_samples,
    DEFAULT_ORBIT_SEGMENT_S,
    write_orbit_tracks_segmented,
)

logger = logging.getLogger(__name__)

EPOCH_ISO = "2024-01-01T00:00:00Z"


def _sanitize_for_json(obj: object) -> object:
    """Ensures RFC 8259 JSON: no NaN/Infinity (json.dump allow_nan=True breaks strict parsers)."""
    if obj is None or isinstance(obj, (str, bool)):
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(x) for x in obj]
    if isinstance(obj, numbers.Integral) and not isinstance(obj, bool):
        return int(obj)
    if isinstance(obj, numbers.Real) and not isinstance(obj, bool):
        v = float(obj)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    if isinstance(obj, np.generic):
        return _sanitize_for_json(obj.item())
    return obj


def _build_time_series_list(result: EPFDSimulationResult) -> list[dict]:
    """Time series for the panel.

    When ``keep_full_history=True`` returns all steps (O(N) bytes cost).
    Otherwise, returns the accumulator's **adaptive decimated trace**
    (~10k points), which is enough for the EPFD vs time chart and keeps
    the JSON payload under control.
    """
    if getattr(result, "keep_full_history", False) and result.time_steps:
        out = []
        for ts in result.time_steps:
            out.append({
                "t": round(ts.time_s, 2),
                "epfd": round(ts.epfd_aggregate_dBW, 2),
                "vis_h": ts.num_horizon_sats,
                "vis": ts.num_visible_sats,
                "cont": ts.num_contributing_sats,
                "alpha": round(ts.min_alpha_deg, 2),
            })
        return out
    acc = getattr(result, "acc", None)
    if acc is None or not acc.decim_t_s:
        return []
    out = []
    for t, e, vh, v, c, a in zip(
        acc.decim_t_s, acc.decim_epfd_db,
        acc.decim_n_hor, acc.decim_n_vis, acc.decim_n_cont, acc.decim_min_alpha_deg,
    ):
        out.append({
            "t": round(float(t), 2),
            "epfd": round(float(e), 2),
            "vis_h": int(vh),
            "vis": int(v),
            "cont": int(c),
            "alpha": round(float(a), 2),
        })
    return out


def _peak_epfd_aggregate_time_s(result: EPFDSimulationResult) -> float | None:
    """``time_s`` instant of the step with maximum aggregate EPFD↓ (highest dBW, > -900).

    Prefers the streaming accumulator's ``peak_time_s`` (always available and exact);
    falls back to scanning the history only when ``keep_full_history=True``.
    """
    acc = getattr(result, "acc", None)
    if acc is not None and acc.peak_time_s is not None:
        return float(acc.peak_time_s)
    best_t: float | None = None
    best_epfd: float | None = None
    for ts in result.time_steps:
        e = float(ts.epfd_aggregate_dBW)
        if e <= -900.0:
            continue
        if best_epfd is None or e > best_epfd:
            best_epfd = e
            best_t = float(ts.time_s)
    return best_t


def _iso_time(t_s: float) -> str:
    dt = datetime(2024, 1, 1) + timedelta(seconds=t_s)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _search_lat_key(p: WCGSearchPoint) -> float | None:
    v = getattr(p, "search_lat_deg", None)
    if v is None:
        return None
    return round(float(v), 6)


def _select_trail_points_for_czml(
    pts: list[WCGSearchPoint],
    max_n: int | None,
    stratify_by_search_lat: bool,
) -> list[WCGSearchPoint]:
    """Selects points for the CZML.

    If ``stratify_by_search_lat`` and there is a limit, sampling is **by search latitude**
    (proportional), so that filtering by latitude in the UI does not leave only a few points.
    """
    if not pts:
        return []
    if not stratify_by_search_lat:
        step = 1 if max_n is None else max(1, len(pts) // max_n)
        return pts[::step]
    if max_n is None or len(pts) <= max_n:
        by_lat: dict[float | None, list[WCGSearchPoint]] = defaultdict(list)
        for p in pts:
            by_lat[_search_lat_key(p)].append(p)
        keys = sorted(by_lat.keys(), key=lambda x: (x is None, x if x is not None else 0.0))
        out: list[WCGSearchPoint] = []
        for k in keys:
            grp = sorted(by_lat[k], key=lambda p: (p.theta_deg, p.phi_deg))
            out.extend(grp)
        return out
    by_lat = defaultdict(list)
    for p in pts:
        by_lat[_search_lat_key(p)].append(p)
    for k in by_lat:
        by_lat[k].sort(key=lambda p: (p.theta_deg, p.phi_deg))
    keys = sorted(by_lat.keys(), key=lambda x: (x is None, x if x is not None else 0.0))
    total = len(pts)
    out = []
    remaining = max_n
    n_keys = len(keys)
    for ki, k in enumerate(keys):
        grp = by_lat[k]
        n_g = len(grp)
        if ki == n_keys - 1:
            budget = remaining
        else:
            budget = max(1, int(round(max_n * n_g / total)))
        budget = min(budget, n_g, remaining)
        if budget <= 0:
            continue
        if budget >= n_g:
            out.extend(grp)
        else:
            for j in range(budget):
                idx = int(round(j * (n_g - 1) / max(budget - 1, 1))) if budget > 1 else 0
                out.append(grp[min(idx, n_g - 1)])
        remaining = max_n - len(out)
        if remaining <= 0:
            break
    return out[:max_n]


def generate_czml(
    constellation: list[OrbitalElements],
    wcg: WCGResult,
    sim_duration_s: float,
    sample_interval_s: float = 60.0,
    num_planes: int = 24,
    static_wcg: WCGResult | None = None,
    static_es_name: str = "Static ES",
    trail_all_czml_max_points: int = 0,
    trail_stratify_by_search_latitude: bool = True,
    *,
    orbit_tracks_output_dir: str | None = None,
    orbit_segment_s: float = DEFAULT_ORBIT_SEGMENT_S,
    orbit_preview_max_points_per_sat: int = 240,
    orbit_parquet_max_rows: int | None = None,
) -> list[dict]:
    """Generates CZML packets for CesiumJS.

    Positions in ECEF with ``referenceFrame: FIXED`` (earth-fixed), aligned with the
    simulation engine: non-GSO via ``eci_to_ecef(..., t)``, GSO via
    :func:`gso_position_ecef` over the CZML clock interval.

    If ``orbit_tracks_output_dir`` is set, writes full samples to segmented
    Parquet (multiple files per ``orbit_segment_s`` s) and places only a coarse
    per-satellite preview in the CZML (temporal LOD).
    """
    czml: list[dict] = []
    pq_entity_id: list[str] = []
    pq_t: list[float] = []
    pq_x: list[float] = []
    pq_y: list[float] = []
    pq_z: list[float] = []
    use_orbit_parquet = bool(orbit_tracks_output_dir)
    preview_cap = max(4, int(orbit_preview_max_points_per_sat)) if use_orbit_parquet else 0
    n_samples = int(sim_duration_s / sample_interval_s) + 1
    end_iso = _iso_time(sim_duration_s)
    total_sats = len(constellation)
    sats_per_plane = total_sats // num_planes if num_planes > 0 else total_sats

    # ── Document ──
    czml.append({
        "id": "document",
        "name": "WCG Downlink — ITU-R S.1503-4",
        "version": "1.0",
        "clock": {
            "interval": f"{EPOCH_ISO}/{end_iso}",
            "currentTime": EPOCH_ISO,
            "multiplier": 60,
            "range": "LOOP_STOP",
            "step": "SYSTEM_CLOCK_MULTIPLIER",
        },
    })

    # ── Earth Station (FIXED frame — rotates with the Earth) ──
    czml.append({
        "id": "earth_station",
        "name": "GSO ES (WCG adjusted)",
        "description": (
            f"Lat: {wcg.es_lat_deg:.3f}°, Lon: {wcg.es_lon_deg:.3f}°<br>"
            f"α = {wcg.alpha_deg:.2f}°<br>"
            f"Individual WCG/NGSO EPFD: {wcg.epfd_dBW:.1f} dBW<br>"
            f"<b>Aggregate WCG EPFD↓: {wcg.epfd_aggregate_dBW:.1f} dBW</b>"
        ),
        "position": {
            "referenceFrame": "FIXED",
            "cartographicDegrees": [wcg.es_lon_deg, wcg.es_lat_deg, 0],
        },
        "point": {
            "pixelSize": 8,
            "color": {"rgba": [0, 255, 100, 255]},
            "outlineColor": {"rgba": [255, 255, 255, 200]},
            "outlineWidth": 1,
        },
        "label": {
            "text": "ES (WCG)",
            "font": "11px Inter, sans-serif",
            "fillColor": {"rgba": [0, 255, 100, 255]},
            "outlineColor": {"rgba": [0, 0, 0, 200]},
            "outlineWidth": 2,
            "style": "FILL_AND_OUTLINE",
            "pixelOffset": {"cartesian2": [0, -15]},
            "show": True,
        },
    })

    # ── Nominal Earth Station (WCG Search Grid Result — BEFORE correction) ──
    czml.append({
        "id": "nominal_earth_station",
        "name": "ES (Nominal — Grid Search)",
        "description": "Raw position obtained from the grid search (Spherical Earth)",
        "position": {
            "referenceFrame": "FIXED",
            "cartographicDegrees": [wcg.es_lon_nominal, wcg.es_lat_nominal, 0],
        },
        "point": {
            "pixelSize": 6,
            "color": {"rgba": [255, 200, 0, 255]}, # Yellow/Orange
            "outlineColor": {"rgba": [0, 0, 0, 200]},
            "outlineWidth": 1,
        },
        "label": {
            "text": "ES (Search)",
            "font": "10px Inter, sans-serif",
            "fillColor": {"rgba": [255, 200, 0, 255]},
            "outlineColor": {"rgba": [0, 0, 0, 200]},
            "outlineWidth": 1.5,
            "style": "FILL_AND_OUTLINE",
            "pixelOffset": {"cartesian2": [0, 15]},
            "show": True,
        },
    })

    # ── Static Earth Station (if provided) ──
    if static_wcg:
        czml.append({
            "id": "static_earth_station",
            "name": f"{static_es_name} ({static_wcg.es_lat_deg:.3f}°, {static_wcg.es_lon_deg:.3f}°)",
            "position": {
                "referenceFrame": "FIXED",
                "cartographicDegrees": [static_wcg.es_lon_deg, static_wcg.es_lat_deg, 0],
            },
            "point": {
                "pixelSize": 8,
                "color": {"rgba": [0, 229, 255, 255]}, # Cyan
                "outlineColor": {"rgba": [255, 255, 255, 200]},
                "outlineWidth": 1,
            },
            "label": {
                "text": static_es_name,
                "font": "11px Inter, sans-serif",
                "fillColor": {"rgba": [0, 229, 255, 255]},
                "outlineColor": {"rgba": [0, 0, 0, 200]},
                "outlineWidth": 2,
                "style": "FILL_AND_OUTLINE",
                "pixelOffset": {"cartesian2": [0, -15]},
                "show": True,
            },
        })

    # ── GSO Satellite (FIXED / ECEF — sampled over time, same as the EPFD engine) ──
    gso_alt_m = (GSO_RADIUS_KM - RE_KM) * 1000.0
    gso_cart_m: list[float] = []
    for si in range(n_samples):
        t = si * sample_interval_s
        pos_km = gso_position_ecef(wcg.gso_lon_deg, t)
        gso_cart_m.extend([
            round(t, 1),
            round(float(pos_km[0]) * 1000.0, 0),
            round(float(pos_km[1]) * 1000.0, 0),
            round(float(pos_km[2]) * 1000.0, 0),
        ])
    czml.append({
        "id": "gso_sat",
        "name": f"GSO Ref ({wcg.gso_lon_deg:.1f}°)",
        "position": {
            "epoch": EPOCH_ISO,
            "referenceFrame": "FIXED",
            "cartesian": gso_cart_m,
            "interpolationAlgorithm": "LAGRANGE",
            "interpolationDegree": min(5, max(0, n_samples - 1)),
        },
        "point": {
            "pixelSize": 8,
            "color": {"rgba": [255, 215, 0, 255]},
            "outlineColor": {"rgba": [255, 255, 255, 180]},
            "outlineWidth": 2,
        },
        "label": {
            "text": f"GSO ({wcg.gso_lon_deg:.1f}°)",
            "font": "11px Inter, sans-serif",
            "fillColor": {"rgba": [255, 215, 0, 255]},
            "outlineColor": {"rgba": [0, 0, 0, 200]},
            "outlineWidth": 2,
            "style": "FILL_AND_OUTLINE",
            "pixelOffset": {"cartesian2": [0, -18]},
            "show": True,
        },
    })

    # ── GSO Arc ──
    gso_arc_pts = []
    for lon in range(-180, 181, 3):
        gso_arc_pts.extend([float(lon), 0.0, gso_alt_m])
    czml.append({
        "id": "gso_arc",
        "name": "GSO Arc",
        "polyline": {
            "positions": {
                "referenceFrame": "FIXED",
                "cartographicDegrees": gso_arc_pts,
            },
            "width": 1.5,
            "material": {
                "solidColor": {"color": {"rgba": [255, 215, 0, 80]}}
            },
        },
    })

    # ── Equator Line ──
    equator_pts = []
    for lon in range(-180, 181, 2):
        equator_pts.extend([float(lon), 0.0, 0.0])
    czml.append({
        "id": "equator",
        "name": "Equator",
        "polyline": {
            "positions": {
                "referenceFrame": "FIXED",
                "cartographicDegrees": equator_pts,
            },
            "width": 1.5,
            "material": {
                "solidColor": {"color": {"rgba": [255, 255, 255, 120]}}
            },
        },
    })

    # ── WCG geometry line (ES → GSO) ──
    es_ecef_m = list(np.array(lla_to_ecef(
        wcg.es_lat_deg, wcg.es_lon_deg, 0.0)) * 1000.0)
    gso_ecef_m = list(np.array(gso_position_ecef(
        wcg.gso_lon_deg, 0.0)) * 1000.0)
    czml.append({
        "id": "wcg_line",
        "name": "WCG Geometry (ES→GSO)",
        "polyline": {
            "positions": {
                "referenceFrame": "FIXED",
                "cartesian": es_ecef_m + gso_ecef_m,
            },
            "width": 2,
            "arcType": "NONE",
            "material": {
                "solidColor": {"color": {"rgba": [0, 255, 100, 120]}}
            },
        },
    })

    # ── WCG reference non-GSO satellite (FIXED — same frame as everything) ──
    if wcg.ref_sat_eci is not None and np.linalg.norm(wcg.ref_sat_eci) > 0:
        ref_ecef_0 = eci_to_ecef(wcg.ref_sat_eci, 0.0)  # t=0 with configured GMST0
        ref_ecef_m = [float(v * 1000.0) for v in ref_ecef_0]
        czml.append({
            "id": "wcg_ref_ngso",
            "name": "Non-GSO Ref (WCG, t=0)",
            "description": "ECEF position of the reference non-GSO satellite at instant t=0",
            "position": {
                "cartesian": ref_ecef_m,
                "referenceFrame": "FIXED",
            },
            "point": {
                "pixelSize": 10,
                "color": {"rgba": [255, 100, 0, 255]},
                "outlineColor": {"rgba": [255, 255, 255, 200]},
                "outlineWidth": 2,
            },
            "label": {
                "text": "Non-GSO (WCG ref)",
                "font": "11px Inter, sans-serif",
                "fillColor": {"rgba": [255, 100, 0, 255]},
                "outlineColor": {"rgba": [0, 0, 0, 200]},
                "outlineWidth": 2,
                "style": "FILL_AND_OUTLINE",
                "pixelOffset": {"cartesian2": [0, -18]},
                "show": True,
            },
        })

        # ES → Non-GSO ref line (both FIXED/ECEF)
        czml.append({
            "id": "wcg_line_ngso",
            "name": "WCG Geometry (ES→Non-GSO)",
            "polyline": {
                "positions": {
                    "referenceFrame": "FIXED",
                    "cartesian": es_ecef_m + ref_ecef_m,
                },
                "width": 2,
                "arcType": "NONE",
                "material": {
                    "solidColor": {"color": {"rgba": [255, 100, 0, 100]}}
                },
            },
        })

    # ── WCG search trail (points evaluated on the surface) ──
    def _append_trail_points(
        trail_points: list[WCGSearchPoint],
        id_prefix: str,
        show_default: bool,
        max_ok: int | None,
        max_excl: int | None,
        max_low: int | None,
        log_label: str,
        stratify_by_search_lat: bool = False,
    ) -> None:
        if not trail_points:
            return
        ok_pts = [p for p in trail_points if p.status == "ok"]
        excl_pts = [p for p in trail_points if p.status == "exclusion"]
        low_pts = [p for p in trail_points if p.status == "low_elev"]

        logger.info(
            f"WCG trail ({log_label}): {len(ok_pts)} ok, "
            f"{len(excl_pts)} excl, {len(low_pts)} low_elev"
        )

        ok_render = _select_trail_points_for_czml(ok_pts, max_ok, stratify_by_search_lat)
        excl_render = _select_trail_points_for_czml(excl_pts, max_excl, stratify_by_search_lat)
        low_render = _select_trail_points_for_czml(low_pts, max_low, stratify_by_search_lat)

        # EPFD color scale: min/max over **all** the ok points of the trail (not just ok_render).
        # This way CZML subsampling and the "by latitude" filter in the UI do not change the color meaning.
        epfd_scale_min: float | None = None
        epfd_scale_max: float | None = None
        epfd_scale_spread = 0.0
        if ok_pts:
            epfd_scale_min = min(p.epfd_dBW for p in ok_pts)
            epfd_scale_max = max(p.epfd_dBW for p in ok_pts)
            epfd_scale_spread = float(epfd_scale_max - epfd_scale_min)
        epfd_scale_range = max(epfd_scale_spread, 1e-12)

        # Valid points — color by EPFD (blue=low, red=high)
        if ok_render:
            for i, p in enumerate(ok_render):
                frac = (
                    0.0 if epfd_scale_spread < 1e-12 else (p.epfd_dBW - epfd_scale_min) / epfd_scale_range
                )
                # Blue→Cyan→Green→Yellow→Red
                r_c, g_c, b_c = colorsys.hsv_to_rgb(
                    0.67 - frac * 0.67, 0.9, 0.9)
                czml.append({
                    "id": f"trail_{id_prefix}ok_{i}",
                    "name": f"WCG θ={p.theta_deg:.1f}° φ={p.phi_deg:.1f}°",
                    "description": (
                        (f"search_lat={p.search_lat_deg:.1f}°, " if p.search_lat_deg is not None else "")
                        +
                        f"α={p.alpha_deg:.1f}°, elev={p.elevation_deg:.1f}°, "
                        f"EPFD={p.epfd_dBW:.1f} dBW"
                    ),
                    "position": {
                        "referenceFrame": "FIXED",
                        "cartographicDegrees": [p.es_lon_deg, p.es_lat_deg, 5000],
                    },
                    "show": show_default,
                    "point": {
                        "pixelSize": 4,
                        "color": {"rgba": [int(r_c*255), int(g_c*255), int(b_c*255), 180]},
                    },
                })

        # Exclusion zone — magenta
        if excl_render:
            for i, p in enumerate(excl_render):
                czml.append({
                    "id": f"trail_{id_prefix}excl_{i}",
                    "name": f"Excl θ={p.theta_deg:.1f}° φ={p.phi_deg:.1f}°",
                    "description": (
                        (f"search_lat={p.search_lat_deg:.1f}°, " if p.search_lat_deg is not None else "")
                        + f"α={p.alpha_deg:.1f}° (inside the exclusion |α|<α₀)"
                    ),
                    "position": {
                        "referenceFrame": "FIXED",
                        "cartographicDegrees": [p.es_lon_deg, p.es_lat_deg, 3000],
                    },
                    "show": show_default,
                    "point": {
                        "pixelSize": 3,
                        "color": {"rgba": [255, 0, 255, 140]},
                    },
                })

        # Low elevation (gray)
        if low_render:
            for i, p in enumerate(low_render):
                czml.append({
                    "id": f"trail_{id_prefix}low_{i}",
                    "name": f"LowElev θ={p.theta_deg:.1f}° φ={p.phi_deg:.1f}°",
                    "description": (
                        (f"search_lat={p.search_lat_deg:.1f}°, " if p.search_lat_deg is not None else "")
                        + f"elev={p.elevation_deg:.1f}° (below the minimum)"
                    ),
                    "position": {
                        "referenceFrame": "FIXED",
                        "cartographicDegrees": [p.es_lon_deg, p.es_lat_deg, 1000],
                    },
                    "show": show_default,
                    "point": {
                        "pixelSize": 2,
                        "color": {"rgba": [120, 120, 140, 60]},
                    },
                })

    # "Improvements" trail (with subsampling; by default stratified by search latitude)
    _append_trail_points(
        trail_points=wcg.search_trail or [],
        id_prefix="",
        show_default=True,
        max_ok=800,
        max_excl=400,
        max_low=200,
        log_label="improvements",
        stratify_by_search_lat=trail_stratify_by_search_latitude,
    )

    # Full trail (search_trail_all). trail_all_czml_max_points <= 0 = no limit (all points in the CZML).
    all_pts = getattr(wcg, "search_trail_all", []) or []
    if all_pts:
        if trail_all_czml_max_points <= 0:
            logger.info(
                "WCG trail (all): %s points in the CZML (no limit).",
                len(all_pts),
            )
            _append_trail_points(
                trail_points=all_pts,
                id_prefix="all_",
                show_default=False,
                max_ok=None,
                max_excl=None,
                max_low=None,
                log_label="all",
                stratify_by_search_lat=trail_stratify_by_search_latitude,
            )
        else:
            n_ok_all = sum(1 for p in all_pts if p.status == "ok")
            n_excl_all = sum(1 for p in all_pts if p.status == "exclusion")
            n_low_all = sum(1 for p in all_pts if p.status == "low_elev")
            n_tot_all = max(1, n_ok_all + n_excl_all + n_low_all)

            max_ok_all = max(1, int(trail_all_czml_max_points * n_ok_all / n_tot_all)) if n_ok_all > 0 else 0
            max_excl_all = max(1, int(trail_all_czml_max_points * n_excl_all / n_tot_all)) if n_excl_all > 0 else 0
            max_low_all = max(1, int(trail_all_czml_max_points * n_low_all / n_tot_all)) if n_low_all > 0 else 0

            logger.info(
                "WCG trail (all): sampling for the CZML (max %s points).",
                trail_all_czml_max_points,
            )

            _append_trail_points(
                trail_points=all_pts,
                id_prefix="all_",
                show_default=False,
                max_ok=max_ok_all,
                max_excl=max_excl_all,
                max_low=max_low_all,
                log_label="all",
                stratify_by_search_lat=trail_stratify_by_search_latitude,
            )

        ok_wcg_sample = [p for p in all_pts if p.status == "ok"]
        if ok_wcg_sample:
            worst_sp = max(
                ok_wcg_sample,
                key=lambda p: (p.epfd_dBW, p.theta_deg, p.phi_deg),
            )
            logger.info(
                "CZML marker: worst EPFD among ok samples = %.2f dBW at (%.4f°, %.4f°)",
                worst_sp.epfd_dBW,
                worst_sp.es_lat_deg,
                worst_sp.es_lon_deg,
            )
            slat = worst_sp.search_lat_deg
            worst_ent: dict = {
                "id": "trail_all_worst_epfd_sample",
                "name": "Worst EPFD (WCGA sample)",
                "description": (
                    f"Among all samples with status ok: EPFD={worst_sp.epfd_dBW:.2f} dBW, "
                    f"θ={worst_sp.theta_deg:.2f}°, φ={worst_sp.phi_deg:.2f}°, "
                    f"α={worst_sp.alpha_deg:.2f}°, elev={worst_sp.elevation_deg:.2f}°"
                    + (f", search_lat={slat:.2f}°" if slat is not None else "")
                ),
                "position": {
                    "referenceFrame": "FIXED",
                    "cartographicDegrees": [
                        worst_sp.es_lon_deg,
                        worst_sp.es_lat_deg,
                        6500.0,
                    ],
                },
                "point": {
                    "pixelSize": 14,
                    "color": {"rgba": [255, 94, 0, 255]},
                    "outlineColor": {"rgba": [255, 255, 255, 255]},
                    "outlineWidth": 3,
                },
                "label": {
                    "text": "Worst EPFD (sample)",
                    "font": "12px Inter, sans-serif",
                    "fillColor": {"rgba": [255, 220, 180, 255]},
                    "outlineColor": {"rgba": [0, 0, 0, 220]},
                    "outlineWidth": 3,
                    "style": "FILL_AND_OUTLINE",
                    "pixelOffset": {"cartesian2": [0, -28]},
                    "show": True,
                },
                "show": True,
            }
            czml.append(worst_ent)

    # ── Non-GSO Satellites (ECEF, FIXED reference frame — consistent with the engine) ──
    logger.info(f"Generating CZML: {total_sats} sats × {n_samples} samples (ECEF/FIXED)...")

    for sat_idx, oe in enumerate(constellation):
        plane_idx = sat_idx // sats_per_plane
        sat_in_plane = sat_idx % sats_per_plane

        # ECEF positions over time (meters)
        # We convert ECI → ECEF using our model (GMST = OMEGA_E * t)
        cart = []
        for si in range(n_samples):
            t = si * sample_interval_s
            oe_t = oe.propagate(t)
            pos_eci, _ = elements_to_eci(oe_t)
            pos_ecef = eci_to_ecef(pos_eci, t)
            cart.extend([
                round(t, 1),
                round(pos_ecef[0] * 1000.0, 0),
                round(pos_ecef[1] * 1000.0, 0),
                round(pos_ecef[2] * 1000.0, 0),
            ])

        sat_entity_id = f"sat_{plane_idx}_{sat_in_plane}"
        if use_orbit_parquet:
            append_sat_cart_as_rows(
                sat_entity_id,
                cart,
                entity_ids=pq_entity_id,
                ts=pq_t,
                xs=pq_x,
                ys=pq_y,
                zs=pq_z,
            )
            czml_cart = downsample_cartesian_epoch_samples(cart, preview_cap)
        else:
            czml_cart = cart

        # Single color for all satellites (uniform visualization requirement)
        rgba = [96, 165, 250, 220]  # light blue

        entity: dict = {
            "id": sat_entity_id,
            "name": f"P{plane_idx}-S{sat_in_plane}",
            "position": {
                "epoch": EPOCH_ISO,
                "cartesian": czml_cart,
                "referenceFrame": "FIXED",
                "interpolationAlgorithm": "LAGRANGE",
                "interpolationDegree": 5,
            },
            "point": {
                "pixelSize": 4,
                "color": {"rgba": rgba},
            },
            "description": f"Plane {plane_idx}, Sat {sat_in_plane}",
        }

        # Orbit path for first sat of each plane
        if sat_in_plane == 0:
            entity["path"] = {
                "show": True,
                "width": 1,
                "material": {
                    "solidColor": {
                        "color": {"rgba": [rgba[0], rgba[1], rgba[2], 50]}
                    }
                },
                "trailTime": sim_duration_s,
                "leadTime": 0,
            }

        czml.append(entity)

        if (sat_idx + 1) % 100 == 0:
            logger.info(f"  {sat_idx + 1}/{total_sats} satellites exported")

    # ── CZML check: nearest animated satellite (t=0) vs wcg_ref_ngso ──
    if wcg.ref_sat_eci is not None and np.linalg.norm(wcg.ref_sat_eci) > 0:
        ref_entity = next((e for e in czml if e.get("id") == "wcg_ref_ngso"), None)

        if ref_entity:
            # WCG ref marker (FIXED/ECEF, meters) at t=0 with configured GMST0.
            ref_cart = ref_entity["position"]["cartesian"]
            ref_t0 = np.array(ref_cart)

            # Search for the animated satellite closest to the WCG marker at t=0
            best_id = None
            best_t0 = None
            best_dist_m = math.inf
            for ent in czml:
                ent_id = str(ent.get("id", ""))
                if not ent_id.startswith("sat_"):
                    continue
                pos = ent.get("position", {})
                cart = pos.get("cartesian")
                if not isinstance(cart, list) or len(cart) < 4:
                    continue
                sat_t0 = np.array([cart[1], cart[2], cart[3]])
                dist_m = float(np.linalg.norm(sat_t0 - ref_t0))
                if dist_m < best_dist_m:
                    best_dist_m = dist_m
                    best_id = ent_id
                    best_t0 = sat_t0

            if best_id is not None and best_t0 is not None:
                logger.info(f"  CZML check (t=0):")
                logger.info(f"    nearest sat ({best_id}): [{best_t0[0]:.0f}, {best_t0[1]:.0f}, {best_t0[2]:.0f}] m")
                logger.info(f"    wcg_ref (FIXED):           [{ref_t0[0]:.0f}, {ref_t0[1]:.0f}, {ref_t0[2]:.0f}] m")
                logger.info(f"    Δ = {best_dist_m:.1f} m")
                if best_dist_m < 1.0:
                    logger.info(f"    ✅ CZML: WCG marker aligned with animated satellite.")
                else:
                    logger.warning(f"    ⚠️ CZML: WCG does not exactly match the sampled satellites (min Δ={best_dist_m:.1f} m).")

    if use_orbit_parquet:
        logger.info(
            "Writing segmented orbit_tracks Parquet (%d ECEF rows)...",
            len(pq_entity_id),
        )
        write_orbit_tracks_segmented(
            orbit_tracks_output_dir,
            entity_id=pq_entity_id,
            t_s=pq_t,
            x_m=pq_x,
            y_m=pq_y,
            z_m=pq_z,
            segment_s=float(orbit_segment_s),
            max_rows=orbit_parquet_max_rows,
        )

    logger.info(f"CZML: {len(czml)} entities")
    return czml


def generate_sim_data(
    wcg: WCGResult,
    sim_result: EPFDSimulationResult,
    compliance: ComplianceResult | None,
    config: dict,
    static_wcg: WCGResult | None = None,
    static_sim_result: EPFDSimulationResult | None = None,
    static_compliance: ComplianceResult | None = None,
    pfd_mask_obj: PFDMask | None = None,
) -> dict:
    """Generates JSON with simulation data for the panels."""

    def _build_export_ccdf(
        result: EPFDSimulationResult,
    ) -> dict:
        """Exports the backend's quantized CCDF for direct use in the frontend."""
        if len(result.cdf_epfd_dBW) == 0:
            return {"epfd": [], "percent": [], "source": "s1503_0p1db_bins", "bin_db": 0.1}

        return {
            "epfd": [round(float(v), 2) for v in result.cdf_epfd_dBW],
            "percent": [round(float(v), 6) for v in result.cdf_percentage],
            "source": "s1503_0p1db_bins",
            "bin_db": 0.1,
        }

    ts_list = _build_time_series_list(sim_result)

    ngso = config.get("non_gso", {})
    sim_meta = config.get("simulation", {})
    apply_gso_min_elev = bool(ngso.get("apply_gso_min_elevation", True))
    strict_max_co_freq_total = bool(ngso.get("strict_max_co_freq_total", False))
    gso_min_elev_deg = ngso.get("gso_min_elevation_deg", None)
    if gso_min_elev_deg is None:
        freq_ghz = float(ngso.get("frequency_ghz", 0.0) or 0.0)
        gso_min_elev_deg = 20.0 if freq_ghz >= 17.0 else 10.0
    srs = config.get("_srs_system", None)
    sat_name = ""
    if srs and hasattr(srs, "sat_name"):
        sat_name = srs.sat_name

    _acc_stats = getattr(sim_result, "acc", None)
    if _acc_stats is not None and _acc_stats.n_steps_valid > 0:
        max_epfd_db = float(_acc_stats.epfd_max_db)
        min_epfd_db = float(_acc_stats.epfd_min_valid_db)
        mean_epfd_db = float(_acc_stats.epfd_mean_db())
        valid_epfd_count = int(_acc_stats.n_steps_valid)
    else:
        valid_epfd = sim_result.epfd_values_dBW[sim_result.epfd_values_dBW > -900] \
            if len(sim_result.epfd_values_dBW) else np.array([])
        if len(valid_epfd) > 0:
            max_epfd_db = float(np.max(valid_epfd))
            min_epfd_db = float(np.min(valid_epfd))
            mean_epfd_db = float(10 * np.log10(np.mean(10 ** (valid_epfd / 10))))
        else:
            max_epfd_db = min_epfd_db = mean_epfd_db = -999.0
        valid_epfd_count = int(len(valid_epfd))
    peak_sim_time_s = _peak_epfd_aggregate_time_s(sim_result)

    # Search trail data for the UI
    trail_summary = []
    if wcg.search_trail:
        ok_pts = [p for p in wcg.search_trail if p.status == "ok"]
        for p in ok_pts:
            trail_summary.append({
                "lat": round(p.es_lat_deg, 2),
                "lon": round(p.es_lon_deg, 2),
                "epfd": round(p.epfd_dBW, 1),
                "alpha": round(p.alpha_deg, 1),
            })

    all_trail = getattr(wcg, "search_trail_all", []) or []
    trail_latitudes_deg = sorted({
        round(float(p.search_lat_deg), 6)
        for p in all_trail
        if getattr(p, "search_lat_deg", None) is not None
    })
    ok_per_lat: dict[float | None, int] = defaultdict(int)
    for p in all_trail:
        if p.status != "ok":
            continue
        lk = round(float(p.search_lat_deg), 6) if getattr(p, "search_lat_deg", None) is not None else None
        ok_per_lat[lk] += 1
    search_trail_ok_counts_by_lat_deg = [
        {"lat_deg": k, "n_ok": n}
        for k, n in sorted(ok_per_lat.items(), key=lambda x: (x[0] is None, x[0] if x[0] is not None else 0.0))
    ]
    all_trail_ok_epfd = [p.epfd_dBW for p in all_trail if p.status == "ok"]
    search_trail_all_ok_epfd_min = (
        round(float(min(all_trail_ok_epfd)), 6) if all_trail_ok_epfd else None
    )
    search_trail_all_ok_epfd_max = (
        round(float(max(all_trail_ok_epfd)), 6) if all_trail_ok_epfd else None
    )
    search_trail_all_worst_sample: dict | None = None
    ok_trail_all = [p for p in all_trail if p.status == "ok"]
    if ok_trail_all:
        wp = max(ok_trail_all, key=lambda p: (p.epfd_dBW, p.theta_deg, p.phi_deg))
        slat_w = getattr(wp, "search_lat_deg", None)
        search_trail_all_worst_sample = {
            "es_lat_deg": round(float(wp.es_lat_deg), 6),
            "es_lon_deg": round(float(wp.es_lon_deg), 6),
            "epfd_dBW": round(float(wp.epfd_dBW), 4),
            "theta_deg": round(float(wp.theta_deg), 4),
            "phi_deg": round(float(wp.phi_deg), 4),
            "alpha_deg": round(float(wp.alpha_deg), 4),
            "elevation_deg": round(float(wp.elevation_deg), 4),
            "search_lat_deg": round(float(slat_w), 6) if slat_w is not None else None,
        }

    # Fallback: if pfd_mask_obj was not passed, try to reload it from the config
    # (export_all / worker usually pass None: cfg["pfd_mask"] is a dict with file or mdb_file, not the object.)
    if pfd_mask_obj is None:
        try:
            from .main import _resolve_data_path
            from .pfd_mask import load_pfd_mask, load_pfd_mask_from_xml_content
            from .srs_reader import read_pfd_mask_xml_from_mdb

            pfd_cfg = config.get("pfd_mask") if isinstance(config.get("pfd_mask"), dict) else {}
            pfd_source = pfd_cfg.get("source")

            if pfd_source == "mask_mdb" and pfd_cfg.get("mdb_file") and pfd_cfg.get("mask_id") is not None:
                pfd_mask_mdb = _resolve_data_path(pfd_cfg["mdb_file"])
                mask_id = pfd_cfg.get("mask_id")
                srs_sys = config.get("_srs_system")
                ntc_id = getattr(srs_sys, "ntc_id", None) if srs_sys is not None else None
                pfd_xml_content = read_pfd_mask_xml_from_mdb(
                    pfd_mask_mdb,
                    mask_id=int(mask_id),
                    ntc_id=ntc_id,
                )
                pfd_mask_obj = load_pfd_mask_from_xml_content(pfd_xml_content, mask_id=mask_id)
                logger.info(
                    "Fallback: PFD mask from the MASK MDB in sim_data: %s (mask_id=%s)",
                    pfd_mask_mdb,
                    str(mask_id),
                )
            else:
                pfd_xml = config.get("pfd_mask_xml_path") or config.get("pfd_xml")
                mask_id = config.get("pfd_mask_id")

                if not pfd_xml and pfd_cfg:
                    pfd_xml = pfd_cfg.get("file")
                    if mask_id is None:
                        mask_id = pfd_cfg.get("mask_id")

                if pfd_xml:
                    if not os.path.exists(pfd_xml):
                        cfg_path = config.get("_config_path")
                        if cfg_path:
                            alt_path = os.path.join(os.path.dirname(cfg_path), pfd_xml)
                            if os.path.exists(alt_path):
                                pfd_xml = alt_path

                    if pfd_xml and os.path.exists(pfd_xml):
                        pfd_xml = _resolve_data_path(pfd_xml)
                        pfd_mask_obj = load_pfd_mask(pfd_xml, mask_id=mask_id)
                        logger.info("Fallback: PFD mask loaded inside sim_data: %s", pfd_xml)
        except Exception as e:
            logger.warning("Fallback PFD load failed: %s", e)

    # PFD mask data for visualization (Alpha vs PFD)
    pfd_curve = []
    if pfd_mask_obj:
        logger.info(f"Generating PFD curve using: {pfd_mask_obj}")
        # Sample PFD(alpha) from 0 to 180 degrees
        alphas = np.linspace(0, 180, 181)
        for a in alphas:
            # For the global visualization, we assume delta_lon=0 and lat=0 if 3D
            try:
                val = pfd_mask_obj.get_pfd(float(a), lat_deg=0.0, delta_lon_deg=0.0)
                if val > -900: # Ignore invalid/extrapolated values
                    pfd_curve.append([float(a), round(float(val), 2)])
            except:
                pass
        logger.info(f"PFD curve generated with {len(pfd_curve)} points")
    else:
        logger.warning("pfd_mask_obj object is None in generate_sim_data; empty PFD curve.")

    # System EIRP estimate for computing the real PFD in the frontend
    # If not configured, use a conservative default value (e.g., 0 dBW or try reading from config)
    # TODO: Read from the MDB if possible. For now, 40 dBW is a reasonable guess for Low Earth Orbit Ka-band
    system_eirp = config.get("system_max_eirp_dbw", 40.0)
    pfd_bw_correction_db = 0.0
    if pfd_mask_obj is not None:
        try:
            limit_bw_khz = float(config.get("article22_limits", {}).get("reference_bandwidth_khz", 40.0))
            mask_bw_khz = float(getattr(pfd_mask_obj, "refbw_khz", 40.0))
            if limit_bw_khz > 0.0 and mask_bw_khz > 0.0:
                pfd_bw_correction_db = float(10.0 * np.log10(limit_bw_khz / mask_bw_khz))
        except Exception:
            pfd_bw_correction_db = 0.0

    wcg_diagnostics = None
    if wcg.ref_sat_eci is not None and np.linalg.norm(wcg.ref_sat_eci) > 0:
        try:
            ref_sat_ecef = eci_to_ecef(wcg.ref_sat_eci, 0.0)
            ref_sat_lat, ref_sat_lon, ref_sat_alt = ecef_to_lla(ref_sat_ecef)
            delta_lon = ((float(wcg.gso_lon_deg) - float(ref_sat_lon) + 180.0) % 360.0) - 180.0
            wcg_diagnostics = {
                "source": "backend_wcg",
                "time_s": 0.0,
                "es_lat_deg": round(float(wcg.es_lat_deg), 6),
                "es_lon_deg": round(float(wcg.es_lon_deg), 6),
                "ref_sat_ecef_km": [round(float(v), 6) for v in ref_sat_ecef.tolist()],
                "ref_sat_lat_deg": round(float(ref_sat_lat), 6),
                "ref_sat_lon_deg": round(float(ref_sat_lon), 6),
                "ref_sat_alt_km": round(float(ref_sat_alt), 6),
                "long_alpha_deg": round(float(wcg.gso_lon_deg), 6),
                "delta_lon_deg": round(float(delta_lon), 6),
                "alpha_deg": round(float(wcg.alpha_deg), 6),
                "offaxis_deg": round(float(wcg.offaxis_deg), 6),
                "theta_planar_deg": (
                    round(float(wcg.planar_angle_deg), 6)
                    if wcg.planar_angle_deg is not None
                    else None
                ),
                "pfd_dBW": round(float(wcg.pfd_dBW), 6),
                "g_rel_dB": round(float(wcg.es_gain_rel_dB), 6),
                "epfd_dBW": round(float(wcg.epfd_dBW), 6),
                "epfd_aggregate_dBW": round(float(wcg.epfd_aggregate_dBW), 6),
                "elevation_deg": round(float(wcg.elevation_deg), 6),
            }
        except Exception as exc:
            logger.warning("Failed to build WCG diagnostics for sim_data: %s", exc)

    data = {
        "wcg": {
            "theta": round(wcg.theta_deg, 2),
            "phi": round(wcg.phi_deg, 2),
            "es_lat": round(wcg.es_lat_deg, 3),
            "es_lon": round(wcg.es_lon_deg, 3),
            "gso_lon": round(wcg.gso_lon_deg, 3),
            "alpha": round(wcg.alpha_deg, 2),
            "offaxis": round(wcg.offaxis_deg, 2),
            "pfd": round(wcg.pfd_dBW, 1),
            "gr_rel": round(wcg.es_gain_rel_dB, 1),
            "epfd": round(wcg.epfd_dBW, 1),
            "epfd_aggregate": round(wcg.epfd_aggregate_dBW, 1),
            "elev": round(wcg.elevation_deg, 1),
            "ref_sat_eci_km": [round(v, 3) for v in wcg.ref_sat_eci.tolist()] if wcg.ref_sat_eci is not None else [0, 0, 0],
            "diagnostics": wcg_diagnostics,
        },
        "search_trail_ok_count": len(trail_summary),
        "search_trail_total": len(wcg.search_trail) if wcg.search_trail else 0,
        "search_trail_best_total": len(wcg.search_trail) if wcg.search_trail else 0,
        "search_trail_all_total": len(all_trail),
        "search_trail_all_available": bool(len(all_trail) > 0),
        "search_trail_latitudes_deg": trail_latitudes_deg,
        "search_trail_ok_counts_by_lat_deg": search_trail_ok_counts_by_lat_deg,
        "search_trail_all_ok_epfd_min": search_trail_all_ok_epfd_min,
        "search_trail_all_ok_epfd_max": search_trail_all_ok_epfd_max,
        "search_trail_all_worst_sample": search_trail_all_worst_sample,
        "search_trail_all_czml_max_points": int(
            config.get("wcg_search", {}).get("s1503_trail_all_czml_max_points", 0)
        ),
        "orbit_segment_s": float(
            (config.get("wcg_search") or {}).get("orbit_segment_s", DEFAULT_ORBIT_SEGMENT_S)
            or DEFAULT_ORBIT_SEGMENT_S
        ),
        "orbit_tracks_partitioned": bool(
            (config.get("wcg_search") or {}).get("orbit_tracks_parquet", True)
        ),
        "time_steps": ts_list,
        "static_es": None,
        "pfd_mask_curve": pfd_curve,
        "pfd_system_eirp": system_eirp,
        "pfd_bw_correction_db": round(float(pfd_bw_correction_db), 6),
        "config": {
            "sat_name": sat_name,
            "ntc_id": getattr(srs, "ntc_id", None) if srs is not None else None,
            "grp_freq_min_ghz": ngso.get("grp_freq_min_ghz"),
            "grp_freq_max_ghz": ngso.get("grp_freq_max_ghz"),
            "num_planes": ngso.get("num_planes", 0),
            "sats_per_plane": ngso.get("sats_per_plane", 0),
            "inclination_deg": ngso.get("inclination_deg", 0),
            "altitude_km": round(ngso.get("semi_major_axis_km", 0) - RE_KM, 1),
            "repeating_ground_track": bool(sim_meta.get("repeating_ground_track", False)),
            "repeat_period_days": sim_meta.get("repeat_period_days"),
            "repeat_period_s": (
                float(ngso.get("_rpt_period_s", 0.0))
                if float(ngso.get("_rpt_period_s", 0.0) or 0.0) > 0.0
                else (
                    float(sim_meta.get("repeat_period_days", 0.0)) * 86400.0
                    if float(sim_meta.get("repeat_period_days", 0.0) or 0.0) > 0.0
                    else None
                )
            ),
            "alpha0_deg": ngso.get("alpha0_deg", 0),
            "strict_exclusion_zone": bool(ngso.get("strict_exclusion_zone", False)),
            "min_elevation_deg": ngso.get("min_elevation_deg", 0),
            "apply_gso_min_elevation": apply_gso_min_elev,
            "strict_max_co_freq_total": strict_max_co_freq_total,
            "gso_min_elevation_deg": gso_min_elev_deg,
            "frequency_ghz": ngso.get("frequency_ghz", 0),
            "es_antenna_diameter_m": config.get("gso_es", {}).get("antenna_diameter_m", 1.2),
            "es_antenna_efficiency": config.get("gso_es", {}).get("antenna_efficiency", 0.65),
            "es_service": config.get("gso_es", {}).get("service", "FSS"),
            "time_step_s_used": sim_meta.get("_resolved_time_step_s"),
            "num_time_steps_used": sim_meta.get("_resolved_num_time_steps"),
            "time_step_s1503": sim_meta.get("_s1503_reference_time_step_s"),
            "num_time_steps_s1503": sim_meta.get("_s1503_reference_num_time_steps"),
            "dual_time_step_mode": sim_meta.get("dual_time_step_mode", "s1503"),
            "dual_fine_step_s_used": sim_meta.get("_resolved_dual_fine_step_s"),
            "dual_coarse_step_s_used": sim_meta.get("_resolved_dual_coarse_step_s"),
            "dual_ncoarse_used": sim_meta.get("_resolved_dual_ncoarse"),
            "dual_fine_step_s1503": sim_meta.get("_s1503_reference_dual_fine_step_s"),
            "dual_coarse_step_s1503": sim_meta.get("_s1503_reference_dual_coarse_step_s"),
            "dual_ncoarse_s1503": sim_meta.get("_s1503_reference_dual_ncoarse"),
            # Executed dual time step tally (how many fine vs coarse steps ran).
            "n_fine_steps_executed": sim_meta.get("_resolved_n_fine_steps"),
            "n_coarse_steps_executed": sim_meta.get("_resolved_n_coarse_steps"),
            "n_exec_steps": sim_meta.get("_resolved_n_exec_steps"),
            "max_co_freq_by_lat": [
                {"lat_fr": f, "lat_to": t, "nbr_op_sat": n}
                for f, t, n in ngso.get("max_co_freq_by_lat", [])
            ],
        },
        "compliance": {
            "compliant": compliance.compliant if compliance else True,
            "worst_margin": round(compliance.worst_margin_dB, 2) if compliance else 999,
            "worst_limit": round(compliance.worst_limit_dBW, 1) if compliance else 0,
            "worst_pct": float(compliance.worst_percentage) if compliance else 0,
        },
        "stats": {
            "max_epfd": round(max_epfd_db, 2) if valid_epfd_count > 0 else -999,
            "max_epfd_time_s": round(float(peak_sim_time_s), 2) if peak_sim_time_s is not None else None,
            "min_epfd": round(min_epfd_db, 2) if valid_epfd_count > 0 else -999,
            "mean_epfd": round(mean_epfd_db, 2) if valid_epfd_count > 0 else -999,
        },
        "ccdf": _build_export_ccdf(sim_result),
        "art22_limits": config.get("article22_limits", {}).get("limits", []),
        "art22_reference": config.get("article22_limits", {}).get("rr_reference", ""),
        "art22_bw_khz": config.get("article22_limits", {}).get("reference_bandwidth_khz", 40.0),
        "art22_mask_id": config.get("article22_limits", {}).get("_epfd_mask_id"),
        "art22_rf_diam_cm": config.get("article22_limits", {}).get("_epfd_rf_diam_cm"),
        "pfd_mask": pfd_mask_obj.to_dict() if pfd_mask_obj and hasattr(pfd_mask_obj, "to_dict") else None,
    }

    # Add Static ES data if available
    if static_sim_result and static_wcg:
        static_ts_list = _build_time_series_list(static_sim_result)

        _acc_static = getattr(static_sim_result, "acc", None)
        if _acc_static is not None and _acc_static.n_steps_valid > 0:
            static_max_epfd = float(_acc_static.epfd_max_db)
            static_valid_count = int(_acc_static.n_steps_valid)
        else:
            static_valid_epfd = (
                static_sim_result.epfd_values_dBW[static_sim_result.epfd_values_dBW > -900]
                if len(static_sim_result.epfd_values_dBW) else np.array([])
            )
            static_max_epfd = float(np.max(static_valid_epfd)) if len(static_valid_epfd) else -999.0
            static_valid_count = int(len(static_valid_epfd))
        static_peak_time_s = _peak_epfd_aggregate_time_s(static_sim_result)

        data["static_sim"] = {
            "name": config.get("simulation", {}).get("static_es", {}).get("name", "Static ES"),
            "es_lat": round(static_wcg.es_lat_deg, 3),
            "es_lon": round(static_wcg.es_lon_deg, 3),
            "gso_lon": round(static_wcg.gso_lon_deg, 3),
            "time_steps": static_ts_list,
            "compliance": {
                "compliant": static_compliance.compliant if static_compliance else True,
                "worst_margin": round(static_compliance.worst_margin_dB, 2) if static_compliance else 999,
            },
            "stats": {
                "max_epfd": round(static_max_epfd, 2) if static_valid_count > 0 else -999,
                "max_epfd_time_s": round(float(static_peak_time_s), 2) if static_peak_time_s is not None else None,
            },
            "ccdf": _build_export_ccdf(static_sim_result)
        }


    return data


def build_mask_preview_sim_data(
    pfd_mask_obj: PFDMask,
    *,
    sat_name: str = "PFD mask preview",
    ntc_id: str | None = None,
    mask_id: int | None = None,
) -> dict:
    """Builds a minimal ``sim_data.json`` just for the viewer: PFD mask, without the S.1503 engine.

    Cesium still needs a minimal CZML (document + clock); that is handled in ``index.html``.
    """
    pfd_curve: list[list[float]] = []
    if pfd_mask_obj is not None:
        for a in np.linspace(0, 180, 181):
            try:
                val = pfd_mask_obj.get_pfd(float(a), lat_deg=0.0, delta_lon_deg=0.0)
                if val > -900:
                    pfd_curve.append([float(a), round(float(val), 2)])
            except Exception:
                pass

    pfd_bw_correction_db = 0.0
    try:
        limit_bw_khz = 40.0
        mask_bw_khz = float(getattr(pfd_mask_obj, "refbw_khz", 40.0))
        if limit_bw_khz > 0.0 and mask_bw_khz > 0.0:
            pfd_bw_correction_db = float(10.0 * np.log10(limit_bw_khz / mask_bw_khz))
    except Exception:
        pfd_bw_correction_db = 0.0

    stub_wcg = {
        "theta": 0.0,
        "phi": 0.0,
        "es_lat": 0.0,
        "es_lon": 0.0,
        "gso_lon": 0.0,
        "alpha": 0.0,
        "offaxis": 0.0,
        "pfd": -999.0,
        "gr_rel": 0.0,
        "epfd": -999.0,
        "epfd_aggregate": -999.0,
        "elev": 0.0,
        "ref_sat_eci_km": [0.0, 0.0, 0.0],
    }
    stub_config = {
        "sat_name": sat_name,
        "ntc_id": ntc_id,
        "grp_freq_min_ghz": None,
        "grp_freq_max_ghz": None,
        "num_planes": 0,
        "sats_per_plane": 0,
        "inclination_deg": 0.0,
        "altitude_km": 0.0,
        "repeating_ground_track": False,
        "repeat_period_days": None,
        "repeat_period_s": None,
        "alpha0_deg": 0.0,
        "min_elevation_deg": 0.0,
        "apply_gso_min_elevation": True,
        "strict_max_co_freq_total": False,
        "gso_min_elevation_deg": 10.0,
        "frequency_ghz": 0.0,
        "es_antenna_diameter_m": 1.2,
        "es_antenna_efficiency": 0.65,
        "es_service": "FSS",
        "time_step_s_used": None,
        "num_time_steps_used": None,
        "time_step_s1503": None,
        "num_time_steps_s1503": None,
        "dual_time_step_mode": "s1503",
        "dual_fine_step_s_used": None,
        "dual_coarse_step_s_used": None,
        "dual_ncoarse_used": None,
        "dual_fine_step_s1503": None,
        "dual_coarse_step_s1503": None,
        "dual_ncoarse_s1503": None,
        "n_fine_steps_executed": None,
        "n_coarse_steps_executed": None,
        "n_exec_steps": None,
        "max_co_freq_by_lat": [],
    }
    data = {
        "mask_preview": True,
        "mask_preview_mask_id": mask_id,
        "wcg": stub_wcg,
        "search_trail_ok_count": 0,
        "search_trail_total": 0,
        "search_trail_best_total": 0,
        "search_trail_all_total": 0,
        "search_trail_all_available": False,
        "search_trail_latitudes_deg": [],
        "search_trail_ok_counts_by_lat_deg": [],
        "search_trail_all_ok_epfd_min": None,
        "search_trail_all_ok_epfd_max": None,
        "search_trail_all_worst_sample": None,
        "search_trail_all_czml_max_points": 0,
        "time_steps": [],
        "static_es": None,
        "pfd_mask_curve": pfd_curve,
        "pfd_system_eirp": 40.0,
        "pfd_bw_correction_db": round(float(pfd_bw_correction_db), 6),
        "config": stub_config,
        "compliance": {
            "compliant": True,
            "worst_margin": 999.0,
            "worst_limit": 0.0,
            "worst_pct": 0.0,
        },
        "stats": {
            "max_epfd": -999.0,
            "max_epfd_time_s": None,
            "min_epfd": -999.0,
            "mean_epfd": -999.0,
        },
        "ccdf": {"epfd": [], "percent": [], "source": "mask_preview", "bin_db": 0.1},
        "art22_limits": [],
        "art22_reference": "",
        "art22_bw_khz": 40.0,
        "art22_mask_id": None,
        "art22_rf_diam_cm": None,
        "pfd_mask": pfd_mask_obj.to_dict() if hasattr(pfd_mask_obj, "to_dict") else None,
    }
    return data


def build_sim_data_json_bytes(
    wcg: WCGResult,
    sim_result: EPFDSimulationResult,
    compliance: ComplianceResult | None,
    config: dict,
    *,
    static_wcg: WCGResult | None = None,
    static_sim_result: EPFDSimulationResult | None = None,
    static_compliance: ComplianceResult | None = None,
    pfd_mask_obj: PFDMask | None = None,
) -> bytes:
    """Same content as ``sim_data.json`` (CCDF, Art. 22, series, compliance) without generating CZML.

    Used when the 3D viewer is omitted but the results panel (API) is still needed.
    """
    data = generate_sim_data(
        wcg,
        sim_result,
        compliance,
        config,
        static_wcg=static_wcg,
        static_sim_result=static_sim_result,
        static_compliance=static_compliance,
        pfd_mask_obj=pfd_mask_obj,
    )
    return json.dumps(_sanitize_for_json(data), allow_nan=False).encode("utf-8")


def export_all(
    constellation: list[OrbitalElements],
    wcg: WCGResult,
    sim_result: EPFDSimulationResult,
    compliance: ComplianceResult | None,
    config: dict,
    static_wcg: WCGResult | None = None,
    static_sim_result: EPFDSimulationResult | None = None,
    static_compliance: ComplianceResult | None = None,
    output_dir: str = "visualization/data",
    sample_interval_s: float = 60.0,
    pfd_mask: PFDMask | None = None,
):
    """Exports all files for visualization."""
    os.makedirs(output_dir, exist_ok=True)

    logger.info(f"Generating CZML and JSON in '{output_dir}'...")

    _acc_exp = getattr(sim_result, "acc", None)
    if _acc_exp is not None and _acc_exp.last_time_s is not None:
        sim_dur = float(_acc_exp.last_time_s)
    else:
        sim_dur = sim_result.time_steps[-1].time_s if sim_result.time_steps else 6360.0
    a_km = config["non_gso"]["semi_major_axis_km"]
    T_orb = compute_orbital_period(a_km)
    sim_dur = max(sim_dur, T_orb)

    num_planes = config["non_gso"].get("num_planes", 24)
    
    # Get Static ES name if available
    static_es_name = "Static ES"
    if "simulation" in config and "static_es" in config["simulation"]:
        static_es_name = config["simulation"]["static_es"].get("name", "Static ES")

    wcg_s = config.get("wcg_search", {}) or {}
    orbit_parquet_on = bool(wcg_s.get("orbit_tracks_parquet", True))
    orbit_preview_pts = int(wcg_s.get("orbit_preview_max_points_per_sat", 240) or 0)
    if orbit_preview_pts <= 0:
        orbit_preview_pts = 240
    orbit_seg_s = float(wcg_s.get("orbit_segment_s", DEFAULT_ORBIT_SEGMENT_S) or DEFAULT_ORBIT_SEGMENT_S)
    if orbit_seg_s <= 0:
        orbit_seg_s = DEFAULT_ORBIT_SEGMENT_S
    _omr = wcg_s.get("orbit_parquet_max_rows")
    orbit_parquet_max_rows: int | None
    if _omr is None or _omr == "":
        orbit_parquet_max_rows = None
    else:
        try:
            orbit_parquet_max_rows = max(1_000_000, int(_omr))
        except (TypeError, ValueError):
            orbit_parquet_max_rows = None
    orbit_out_dir = output_dir if orbit_parquet_on else None
    czml = generate_czml(
        constellation, wcg, sim_dur, sample_interval_s, num_planes,
        static_wcg=static_wcg,
        static_es_name=static_es_name,
        trail_all_czml_max_points=int(wcg_s.get("s1503_trail_all_czml_max_points", 0)),
        trail_stratify_by_search_latitude=bool(wcg_s.get("czml_trail_stratify_by_search_latitude", True)),
        orbit_tracks_output_dir=orbit_out_dir,
        orbit_segment_s=orbit_seg_s,
        orbit_preview_max_points_per_sat=orbit_preview_pts,
        orbit_parquet_max_rows=orbit_parquet_max_rows,
    )
    czml_path = os.path.join(output_dir, "satellites.czml")
    with open(czml_path, "w") as f:
        json.dump(_sanitize_for_json(czml), f, allow_nan=False)
    sz_mb = os.path.getsize(czml_path) / 1024 / 1024
    logger.info(f"CZML: {czml_path} ({sz_mb:.1f} MB)")

    sim_data = generate_sim_data(
        wcg, sim_result, compliance, config,
        static_wcg=static_wcg,
        static_sim_result=static_sim_result,
        static_compliance=static_compliance,
        pfd_mask_obj=pfd_mask
    )
    sim_path = os.path.join(output_dir, "sim_data.json")
    with open(sim_path, "w") as f:
        json.dump(_sanitize_for_json(sim_data), f, allow_nan=False)
    logger.info(f"Sim data: {sim_path}")

    orbit_manifest_path: str | None = (
        os.path.join(output_dir, ORBIT_MANIFEST_NAME) if orbit_parquet_on else None
    )
    if orbit_manifest_path and not os.path.isfile(orbit_manifest_path):
        orbit_manifest_path = None

    return czml_path, sim_path, orbit_manifest_path


def _is_address_already_in_use(exc: OSError) -> bool:
    """True if ``exc`` indicates a TCP port already in use (varies by OS)."""
    code = getattr(exc, "errno", None)
    if code == errno.EADDRINUSE:
        return True
    if code is not None and code == getattr(errno, "WSAEADDRINUSE", -1):
        return True
    msg = str(exc).lower()
    return "address already in use" in msg or "address is already in use" in msg


def kill_listeners_on_port(port: int, *, aggressive: bool = False) -> None:
    """Terminates TCP listening processes on ``port`` (Linux: ``fuser`` or ``lsof``).

    Used with ``--kill-port`` before ``--serve`` and in the ``start_server`` retry.

    ``aggressive=True``: ``fuser -k -9`` (SIGKILL) or ``lsof`` + SIGKILL.
    """
    logger.info(
        f"Freeing port {port} (terminating TCP listeners"
        f"{' — forced mode' if aggressive else ''})..."
    )
    used_fuser = False
    fuser_args = ["fuser", "-k", "-9" if aggressive else "", f"{port}/tcp"]
    fuser_args = [a for a in fuser_args if a]  # remove empty -9 slot when False
    try:
        r = subprocess.run(
            fuser_args,
            capture_output=True,
            text=True,
            timeout=15,
        )
        used_fuser = True
        out = (r.stderr or r.stdout or "").strip()
        if out:
            logger.info(f"  fuser: {out}")
        elif r.returncode == 0:
            logger.info(f"  fuser finished (code {r.returncode})")
    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired:
        logger.warning("  fuser timed out while trying to free the port")

    if not used_fuser:
        try:
            cp = subprocess.run(
                ["lsof", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if cp.returncode == 0 and cp.stdout.strip():
                sig = signal.SIGKILL if aggressive else signal.SIGTERM
                for pid_s in set(cp.stdout.split()):
                    try:
                        pid = int(pid_s)
                        os.kill(pid, sig)
                        logger.info(f"  signal {sig} → PID {pid}")
                    except (ProcessLookupError, ValueError):
                        pass
                time.sleep(0.5 if not aggressive else 0.3)
            else:
                logger.info(f"  No listener on {port} (lsof)")
        except FileNotFoundError:
            logger.warning(
                "  Neither fuser nor lsof found — free the port manually or install psmisc/lsof."
            )
            return

    time.sleep(0.5 if aggressive else 0.35)


def start_server(directory: str, port: int = 8080):
    """Starts the HTTP server for the visualization."""
    os.chdir(directory)

    class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
        def end_headers(self):
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            super().end_headers()

        def log_message(self, format, *args):  # noqa: A002
            pass  # suppress access logs

    class VizHTTPServer(http.server.HTTPServer):
        """Allows reusing the address after a recently closed server (TIME_WAIT)."""
        allow_reuse_address = True

    handler = NoCacheHandler
    httpd: http.server.HTTPServer | None = None
    for attempt in range(3):
        try:
            httpd = VizHTTPServer(("", port), handler)
            break
        except OSError as e:
            if _is_address_already_in_use(e) and attempt < 2:
                aggressive = attempt >= 1
                logger.warning(
                    f"Port {port} already in use — freeing listeners"
                    f"({' SIGKILL' if aggressive else ''}) and trying again..."
                )
                kill_listeners_on_port(port, aggressive=aggressive)
                continue
            logger.error(
                f"Could not bind to port {port}. "
                f"Terminate the process manually, e.g.: fuser -k {port}/tcp"
            )
            raise
    assert httpd is not None
    logger.info(f"HTTP server at http://localhost:{port}")
    logger.info("Press Ctrl+C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="Export the CesiumJS visualization of the WCG Downlink"
    )
    parser.add_argument("--config", "-c", type=str, default=None)
    parser.add_argument("--mdb", type=str, default=None)
    parser.add_argument("--pfd-xml", type=str, default=None)
    parser.add_argument("--pfd-mask-mdb", type=str, default=None)
    parser.add_argument("--mask-id", type=int, default=None)
    parser.add_argument(
        "--service", type=str, default=None, choices=["FSS", "BSS"],
        help=(
            "GSO earth station service. "
            "Use BSS to apply the antenna pattern from Rec. ITU-R BO.1443-3; "
            "default: FSS."
        ),
    )
    parser.add_argument(
        "--ntc-id", type=str, default=None, metavar="ID",
        help=(
            "Notice (ntc_id) in non_geo when the SRS MDB has multiple systems. "
            "Default: first row. Filters mask_info by the same ntc_id if the column exists."
        ),
    )
    parser.add_argument(
        "--nsteps", type=int, default=None,
        help="Number of time steps (0 = automatic). If omitted, keeps the value from config/MDB."
    )
    parser.add_argument(
        "--coarse-time-step-s", "--coarse_time_step", type=float, default=None,
        help=(
            "Overrides the simulation coarse Δt (s). "
            "Example: --coarse-time-step-s 0.1"
        )
    )
    parser.add_argument(
        "--fine-time-step-s", "--fine_time_step", type=float, default=None,
        help=(
            "Sets the fine Δt of the Dual Time Step (s). "
            "Use 0 to disable the Dual Time Step."
        )
    )
    parser.add_argument(
        "--dual-time-step-mode", type=str, default=None,
        choices=["s1503", "alpha_threshold", "off"],
        help=(
            "Dual Time Step mode: "
            "'s1503' (normative rule by gain), "
            "'alpha_threshold' (legacy), "
            "'off' (single step)."
        ),
    )
    parser.add_argument(
        "--fine-step-alpha-threshold-deg", "--fine_step_alpha_threshold",
        type=float, default=None,
        help=(
            "|α| threshold (degrees) for switching from coarse Δt to fine Δt "
            "in the Dual Time Step."
        )
    )
    parser.add_argument(
        "--s1503-nhit", type=int, default=None,
        help="Nhit for the literal S.1503 computation of Tfine (default 16)."
    )
    parser.add_argument(
        "--s1503-phi-coarse-deg", type=float, default=None,
        help="Topocentric angle φcoarse of the S.1503 dual-step (default 1.5°)."
    )
    parser.add_argument(
        "--s1503-ncoarse", type=int, default=None,
        help="Overrides Ncoarse in the S.1503 dual-step."
    )
    parser.add_argument(
        "--no-s1503-literal-time-step", action="store_true",
        help="Disables the literal D4.2 formula (uses the legacy heuristic for Tfine)."
    )
    parser.add_argument(
        "--earth-rotation-initial-deg", type=float, default=None,
        help=(
            "Overrides GMST0 (degrees) at t=0 to synchronize ECI↔ECEF. "
            "If omitted, uses the value inferred from the SRS (right_asc-long_asc), when available."
        )
    )
    parser.add_argument("--sample-interval", type=float, default=60.0,
                        help="CZML sampling interval (s)")
    parser.add_argument("--serve", action="store_true",
                        help="Start the HTTP server after exporting")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--kill-port",
        action="store_true",
        help=(
            "Before starting the server (--serve), terminates processes listening on the port "
            "given by --port (Linux: fuser -k PORT/tcp, with lsof fallback)."
        ),
    )
    parser.add_argument(
        "--alpha0", type=float, default=None,
        help=(
            "Overrides the exclusion zone α₀ (degrees). "
            "Uses the value from the MDB/YAML if omitted. "
            "Example: --alpha0 2.5"
        )
    )
    parser.set_defaults(apply_gso_min_elevation=True)
    parser.add_argument(
        "--apply-gso-min-elevation",
        dest="apply_gso_min_elevation",
        action="store_true",
        help="Enforces elGSO >= εGSO (Table 8). Default: enabled.",
    )
    parser.add_argument(
        "--no-apply-gso-min-elevation",
        dest="apply_gso_min_elevation",
        action="store_false",
        help=(
            "Disables the elGSO >= εGSO check (effective εGSO −90° in the WCG search; legacy)."
        ),
    )
    parser.add_argument(
        "--gso-min-elevation-deg", "--gso_min_elevation_deg", type=float, default=None,
        help=(
            "Overrides εGSO (degrees), used in the explicit elGSO >= εGSO check in the WCG. "
            "Default: S.1503 Table 8 (10° for f<17 GHz; 20° for f≥17 GHz)."
        )
    )
    parser.add_argument(
        "--es-antenna-diameter", type=float, default=None,
        help=(
            "Overrides the ES antenna diameter (m). "
            "Uses the value from the MDB/YAML if omitted. "
            "Example: --es-antenna-diameter 1.2"
        )
    )
    parser.add_argument(
        "--simulation-frequency-ghz", type=float, default=None,
        help=(
            "Overrides the simulation FrequencyRun in GHz. "
            "The value is clamped to the valid range of the mask and the Art. 22 table."
        )
    )
    parser.add_argument(
        "--reference-bandwidth-khz", type=float, default=None,
        help=(
            "Overrides the normative Art. 22 reference bandwidth "
            "(e.g.: 40 or 1000 kHz)."
        )
    )
    parser.add_argument(
        "--wcga-s1503", action="store_true", default=False,
        help=(
            "Uses the analytical WCGA_Down algorithm from ITU-R S.1503-4 §D.3.1 "
            "instead of the default uniform grid."
        )
    )
    parser.add_argument(
        "--s1503-step", type=float, default=None,
        help="Grid step for --wcga-s1503 (degrees, default 0.1)."
    )
    parser.add_argument(
        "--s1503-trail-all-points", action="store_true",
        help=(
            "In --wcga-s1503 mode, stores/exports all tested points "
            "from the search (can greatly increase memory and JSON/CZML size)."
        )
    )
    parser.add_argument(
        "--wcga-no-mask-symmetry", action="store_true",
        help=(
            "In --wcga-s1503 mode, does not use mask symmetry to reduce the grid in θ "
            "(sweeps θ over the full circle; more points than the automatic default)."
        ),
    )
    parser.add_argument(
        "--s1503-trail-all-czml-max-points", type=int, default=None,
        help=(
            "Limit on the number of 'all tested' trail points in the CZML; "
            "≤0 or omitted = no limit (all points; default 0)."
        )
    )
    parser.add_argument(
        "--wcg-manual", action="store_true",
        help=(
            "Skips the WCG search and uses manual geometry. Requires "
            "--wcg-manual-es-lat, --wcg-manual-es-lon and --wcg-manual-gso-lon."
        )
    )
    parser.add_argument(
        "--wcg-manual-no-align", action="store_true",
        help=(
            "In --wcg-manual mode, disables automatic constellation alignment "
            "(uses ΔM=0 and keeps the original phases)."
        )
    )
    parser.add_argument(
        "--wcg-manual-es-lat", type=float, default=None,
        help="Latitude (degrees) of the earth station (ES) in the manual geometry."
    )
    parser.add_argument(
        "--wcg-manual-es-lon", type=float, default=None,
        help="Longitude (degrees) of the earth station (ES) in the manual geometry."
    )
    parser.add_argument(
        "--wcg-manual-gso-lon", type=float, default=None,
        help="Longitude (degrees) of the GSO satellite in the manual geometry."
    )
    parser.add_argument(
        "--use-precession-mdb", action="store_true",
        help="Use precession_deg_day from the MDB instead of J2 in the propagator.",
    )
    parser.add_argument(
        "--artificial-precession", action="store_true",
        help=(
            "Enables artificial precession for non-repeating orbits (S.1503-4 D4.6.2, D6.3.5). "
            "Accelerates the nodal precession for better CCDF sampling."
        )
    )
    parser.add_argument(
        "--no-static-es", action="store_true",
        help="Do not run the additional static ES simulation (runs only the worst-geometry ES)."
    )
    parser.add_argument(
        "--epfd-limits-mdb", type=str, default=None, metavar="PATH",
        help=(
            "Legacy argument, currently ignored. "
            "EPFD limits are assigned internally by Tables 22-1A through 22-1E."
        )
    )
    parser.add_argument(
        "--epfd-limits-mask-id", type=int, default=None, metavar="ID",
        help=(
            "Legacy argument, currently ignored."
        )
    )
    parser.add_argument(
        "--max-co-freq", type=int, default=None, metavar="N",
        help=(
            "Overrides MAX_CO_FREQ (sat_oper / S.1503): maximum number of standard "
            "co-frequency satellites in the EPFD aggregation. Use 0 for unlimited. Default: sat_oper table from the MDB."
        ),
    )
    parser.add_argument(
        "--strict-max-co-freq-total", action="store_true",
        help=(
            "[Optional, non-normative] Caps MAX_CO_FREQ to the standard+OR set. "
            "The default follows ITU-R S.1503-4 §D5.1.4.1 (cap only on Steps 19–21; OR on Step 22)."
        ),
    )
    args = parser.parse_args()

    from .main import (load_config, load_from_srs,
                       create_constellation_from_config, run_wcg_downlink,
                       apply_max_co_freq_override_to_non_gso)
    from .exceptions import NoValidGeometry, InvalidManualGeometry
    from .article22_tables import apply_article22_limits_to_config

    if args.mdb and (args.pfd_xml or args.pfd_mask_mdb):
        if args.pfd_xml and args.pfd_mask_mdb:
            parser.error("Use only one mask source: --pfd-xml or --pfd-mask-mdb")
        if args.pfd_mask_mdb and args.mask_id is None:
            parser.error("--pfd-mask-mdb mode requires --mask-id")
        config = load_from_srs(
            args.mdb,
            xml_path=args.pfd_xml,
            pfd_mask_mdb=args.pfd_mask_mdb,
            mask_id=args.mask_id,
            epfd_limits_mdb=args.epfd_limits_mdb,
            epfd_limits_mask_id=args.epfd_limits_mask_id,
            ntc_id=args.ntc_id,
            service=args.service or "FSS",
        )
    elif args.config:
        config = load_config(args.config)
    else:
        # Try to find files in the root or in 'data/'
        dirs_to_check = [".", "data"]
        mdb_files = []
        xml_files = []
        for d in dirs_to_check:
            if not os.path.isdir(d): continue
            mdb_files += [os.path.join(d, f) for f in os.listdir(d) if f.upper().endswith(".MDB")]
            xml_files += [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".xml") and "PFD" in f.upper()]

        if mdb_files and xml_files:
            logger.info(f"Files detected automatically: {mdb_files[0]}, {xml_files[0]}")
            config = load_from_srs(
                mdb_files[0], xml_files[0],
                mask_id=args.mask_id,
                epfd_limits_mdb=args.epfd_limits_mdb,
                epfd_limits_mask_id=args.epfd_limits_mask_id,
                ntc_id=args.ntc_id,
                service=args.service or "FSS",
            )
        elif os.path.exists("config.yaml"):
            config = load_config("config.yaml")
        else:
            parser.error("Provide --config or --mdb + (--pfd-xml or --pfd-mask-mdb)")
            return

    if args.service is not None:
        config.setdefault("gso_es", {})["service"] = args.service
    else:
        config.setdefault("gso_es", {}).setdefault("service", "FSS")
    if args.epfd_limits_mdb or args.epfd_limits_mask_id is not None:
        logger.info(
            "--epfd-limits-* arguments ignored: EPFD limits are now "
            "selected internally by Tables 22-1A through 22-1E."
        )

    if args.nsteps is not None:
        if args.nsteps < 0:
            parser.error("--nsteps must be >= 0")
        config["simulation"]["num_time_steps"] = args.nsteps
    config["simulation"]["coarse_time_step_s"] = config["simulation"].get(
        "coarse_time_step_s", 1.0)
    if args.coarse_time_step_s is not None:
        if args.coarse_time_step_s <= 0.0:
            parser.error("--coarse-time-step-s/--coarse_time_step must be > 0")
        old_dt = config["simulation"].get("coarse_time_step_s", 1.0)
        config["simulation"]["coarse_time_step_s"] = float(args.coarse_time_step_s)
        config["simulation"]["_coarse_step_overridden"] = True
        logger.info(
            "Coarse Δt overridden via --coarse-time-step-s: "
            f"{old_dt:.4f}s → {args.coarse_time_step_s:.4f}s"
        )
    if args.fine_time_step_s is not None:
        if args.fine_time_step_s < 0.0:
            parser.error("--fine-time-step-s/--fine_time_step must be >= 0")
        old_dt_fine = config["simulation"].get("fine_time_step_s", 0.0)
        config["simulation"]["fine_time_step_s"] = float(args.fine_time_step_s)
        config["simulation"]["_fine_step_overridden"] = True
        logger.info(
            "Fine Δt overridden via --fine-time-step-s: "
            f"{old_dt_fine:.4f}s → {args.fine_time_step_s:.4f}s"
        )
    if args.dual_time_step_mode is not None:
        config.setdefault("simulation", {})["dual_time_step_mode"] = args.dual_time_step_mode
        logger.info(f"Dual Time Step mode: {args.dual_time_step_mode}")
    if args.fine_step_alpha_threshold_deg is not None:
        if args.fine_step_alpha_threshold_deg <= 0.0:
            parser.error("--fine-step-alpha-threshold-deg/--fine_step_alpha_threshold must be > 0")
        old_alpha_thr = config["simulation"].get("fine_step_alpha_threshold_deg", 2.0)
        config["simulation"]["fine_step_alpha_threshold_deg"] = float(
            args.fine_step_alpha_threshold_deg
        )
        logger.info(
            "Dual Time Step threshold overridden via --fine-step-alpha-threshold-deg: "
            f"{old_alpha_thr:.4f}° → {args.fine_step_alpha_threshold_deg:.4f}°"
        )
    if args.s1503_nhit is not None:
        if args.s1503_nhit <= 0:
            parser.error("--s1503-nhit must be > 0")
        config.setdefault("simulation", {})["s1503_nhit"] = int(args.s1503_nhit)
        logger.info(f"S.1503 Nhit overridden via CLI: {args.s1503_nhit}")
    if args.s1503_phi_coarse_deg is not None:
        if args.s1503_phi_coarse_deg <= 0.0:
            parser.error("--s1503-phi-coarse-deg must be > 0")
        config.setdefault("simulation", {})["s1503_phi_coarse_deg"] = float(args.s1503_phi_coarse_deg)
        logger.info(f"S.1503 φcoarse overridden via CLI: {args.s1503_phi_coarse_deg:.4f}°")
    if args.s1503_ncoarse is not None:
        if args.s1503_ncoarse <= 0:
            parser.error("--s1503-ncoarse must be > 0")
        config.setdefault("simulation", {})["s1503_ncoarse"] = int(args.s1503_ncoarse)
        logger.info(f"S.1503 Ncoarse overridden via CLI: {args.s1503_ncoarse}")
    if args.no_s1503_literal_time_step:
        config.setdefault("simulation", {})["s1503_literal_time_step"] = False
        logger.info("Literal D4.2 computation disabled via --no-s1503-literal-time-step")
    if args.earth_rotation_initial_deg is not None:
        config.setdefault("simulation", {})["earth_rotation_initial_deg"] = float(
            args.earth_rotation_initial_deg
        )
        logger.info(
            "GMST0 overridden via --earth-rotation-initial-deg: "
            f"{args.earth_rotation_initial_deg:.4f}°"
        )

    # Override the WCGA algorithm (always; overrides config.yaml that might force WCGA)
    config.setdefault("wcg_search", {})["use_s1503_algo"] = bool(args.wcga_s1503)
    if args.wcga_s1503:
        logger.info("WCGA S.1503-4 algorithm enabled via --wcga-s1503")
    else:
        logger.info("Legacy WCG mode (θ/φ grid): use_s1503_algo=False")
    if args.s1503_step is not None:
        config.setdefault("wcg_search", {})["s1503_step_deg"] = args.s1503_step
        logger.info(f"WCGA S.1503-4 step: {args.s1503_step}°")
    if args.s1503_trail_all_points:
        config.setdefault("wcg_search", {})["s1503_trail_all_points"] = True
        logger.info("WCGA S.1503: full trail (all points) enabled via CLI.")
    if getattr(args, "wcga_no_mask_symmetry", False):
        config.setdefault("wcg_search", {})["s1503_symmetric_mask"] = False
        logger.info("WCGA S.1503: mask symmetry disabled (--wcga-no-mask-symmetry) → full θ grid.")
    if args.s1503_trail_all_czml_max_points is not None:
        v = int(args.s1503_trail_all_czml_max_points)
        config.setdefault("wcg_search", {})["s1503_trail_all_czml_max_points"] = v
        logger.info(
            "WCGA S.1503: full trail CZML limit = %s (%s).",
            v,
            "no limit (all points)" if v <= 0 else f"max {v} points",
        )
    if args.wcg_manual:
        required = {
            "wcg-manual-es-lat": args.wcg_manual_es_lat,
            "wcg-manual-es-lon": args.wcg_manual_es_lon,
            "wcg-manual-gso-lon": args.wcg_manual_gso_lon,
        }
        missing = [k for k, v in required.items() if v is None]
        if missing:
            parser.error("--wcg-manual mode requires: " + ", ".join("--" + m for m in missing))
        config.setdefault("wcg_search", {})["manual_wcg"] = {
            "enabled": True,
            "es_lat_deg": float(args.wcg_manual_es_lat),
            "es_lon_deg": float(args.wcg_manual_es_lon),
            "gso_lon_deg": float(args.wcg_manual_gso_lon),
            "align_constellation": not bool(args.wcg_manual_no_align),
        }
        logger.info(
            "Manual WCG mode enabled via CLI "
            f"(ES={args.wcg_manual_es_lat:+.3f}°, {args.wcg_manual_es_lon:+.3f}°; "
            f"GSO lon={args.wcg_manual_gso_lon:+.3f}°; "
            f"align={'ON' if not args.wcg_manual_no_align else 'OFF'})"
        )
        if args.wcga_s1503:
            logger.info("Warning: --wcga-s1503 ignored because --wcg-manual is active.")

    # Override artificial precession if specified via CLI
    if args.artificial_precession:
        config.setdefault("simulation", {})["artificial_precession"] = True
        logger.info("Artificial precession enabled via --artificial-precession")
    if args.use_precession_mdb:
        config.setdefault("simulation", {})["use_precession_mdb"] = True
        logger.info("MDB precession enabled via --use-precession-mdb")
    if args.no_static_es:
        config.setdefault("simulation", {})["run_static_es"] = False
        logger.info("Additional static ES simulation disabled via --no-static-es")

    # Override alpha0 if specified via CLI
    if args.alpha0 is not None:
        old = config["non_gso"].get("alpha0_deg", 0.0)
        config["non_gso"]["alpha0_deg"] = args.alpha0
        logger.info(f"α₀ overridden via --alpha0: {old:.2f}° → {args.alpha0:.2f}°")
    config.setdefault("non_gso", {})["apply_gso_min_elevation"] = bool(args.apply_gso_min_elevation)
    config.setdefault("non_gso", {})["strict_max_co_freq_total"] = bool(args.strict_max_co_freq_total)
    logger.info(
        "εGSO check on the downlink: "
        + (
            "disabled via --no-apply-gso-min-elevation"
            if not args.apply_gso_min_elevation
            else "enabled (S.1503 default)"
        )
    )
    logger.info(
        "MAX_CO_FREQ: "
        + (
            "combined-set cap extension via --strict-max-co-freq-total (non-normative)"
            if args.strict_max_co_freq_total
            else "S.1503 default Steps 19–22 (cap only in the standard cycle)"
        )
    )
    if args.gso_min_elevation_deg is not None:
        if not (0.0 <= float(args.gso_min_elevation_deg) <= 90.0):
            parser.error("--gso-min-elevation-deg must be in the range [0, 90]")
        old = config["non_gso"].get("gso_min_elevation_deg", None)
        if old is None:
            freq_ghz = float(config.get("non_gso", {}).get("frequency_ghz", 0.0) or 0.0)
            old = 20.0 if freq_ghz >= 17.0 else 10.0
        old = float(old)
        config["non_gso"]["gso_min_elevation_deg"] = float(args.gso_min_elevation_deg)
        logger.info(
            "εGSO overridden via --gso-min-elevation-deg: "
            f"{old:.2f}° → {float(args.gso_min_elevation_deg):.2f}°"
        )
    if args.es_antenna_diameter is not None:
        if args.es_antenna_diameter <= 0.0:
            parser.error("--es-antenna-diameter must be > 0")
        old_d = config.setdefault("gso_es", {}).get("antenna_diameter_m", 1.2)
        config["gso_es"]["antenna_diameter_m"] = float(args.es_antenna_diameter)
        logger.info(
            "ES antenna diameter overridden via --es-antenna-diameter: "
            f"{old_d:.3f} m → {args.es_antenna_diameter:.3f} m"
        )
    if args.simulation_frequency_ghz is not None:
        if args.simulation_frequency_ghz <= 0.0:
            parser.error("--simulation-frequency-ghz must be > 0")
        old_f = float(config.setdefault("non_gso", {}).get("frequency_ghz", 0.0) or 0.0)
        config.setdefault("pfd_mask", {})["simulation_frequency_ghz"] = float(args.simulation_frequency_ghz)
        config["non_gso"]["frequency_ghz"] = float(args.simulation_frequency_ghz)
        logger.info(
            "Simulation frequency overridden via --simulation-frequency-ghz: "
            f"{old_f:.6f} GHz → {float(args.simulation_frequency_ghz):.6f} GHz"
        )
    if args.reference_bandwidth_khz is not None:
        if args.reference_bandwidth_khz <= 0.0:
            parser.error("--reference-bandwidth-khz must be > 0")
        old_bw = float(config.setdefault("article22_limits", {}).get("reference_bandwidth_khz", 40.0) or 40.0)
        config["article22_limits"]["reference_bandwidth_khz"] = float(args.reference_bandwidth_khz)
        logger.info(
            "Art. 22 ref BW overridden via --reference-bandwidth-khz: "
            f"{old_bw:.0f} kHz → {float(args.reference_bandwidth_khz):.0f} kHz"
        )

    if args.max_co_freq is not None:
        if args.max_co_freq < 0:
            parser.error("--max-co-freq must be >= 0 (0 = unlimited)")
        apply_max_co_freq_override_to_non_gso(config["non_gso"], args.max_co_freq)

    apply_article22_limits_to_config(config)

    try:
        (constellation, wcg_result, sim_result, compliance,
         static_wcg, static_sim_result, static_compliance) = run_wcg_downlink(config)
    except (NoValidGeometry, InvalidManualGeometry) as exc:
        logger.error(str(exc))
        sys.exit(1)

    if wcg_result is None:
        logger.error("Simulation failed!")
        sys.exit(1)

    vis_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                           "visualization")
    data_dir = os.path.join(vis_dir, "data")
    
    # Try to load the PFD mask for visualization
    pfd_mask_viz = None
    try:
        from .pfd_mask import load_pfd_mask, load_pfd_mask_from_xml_content
        from .srs_reader import read_pfd_mask_xml_from_mdb
        pfd_xml = None
        pfd_mask_mdb = None
        mask_id = None

        # Try to get it from args or from the loaded config
        if args.pfd_xml:
            pfd_xml = args.pfd_xml
            mask_id = args.mask_id
        elif args.pfd_mask_mdb:
            pfd_mask_mdb = args.pfd_mask_mdb
            mask_id = args.mask_id
        elif "pfd_mask" in config and isinstance(config["pfd_mask"], dict):
            pfd_xml = config["pfd_mask"].get("file")
            pfd_mask_mdb = config["pfd_mask"].get("mdb_file")
            mask_id = config["pfd_mask"].get("mask_id")
            
        if mask_id is None and args.mask_id:
            mask_id = args.mask_id
            
        if pfd_xml:
            # Resolve path relative to data/ if needed
            from .main import _resolve_data_path
            pfd_xml = _resolve_data_path(pfd_xml)
            pfd_mask_viz = load_pfd_mask(pfd_xml, mask_id=mask_id)
            logger.info(f"PFD mask loaded for viz: {pfd_xml} (id={mask_id})")
        elif pfd_mask_mdb:
            from .main import _resolve_data_path
            pfd_mask_mdb = _resolve_data_path(pfd_mask_mdb)
            srs_sys = config.get("_srs_system")
            ntc_id = getattr(srs_sys, "ntc_id", None) if srs_sys is not None else None
            xml_content = read_pfd_mask_xml_from_mdb(
                pfd_mask_mdb,
                mask_id=int(mask_id),
                ntc_id=ntc_id,
            )
            pfd_mask_viz = load_pfd_mask_from_xml_content(xml_content, mask_id=mask_id)
            logger.info(f"PFD mask loaded for viz from the MASK MDB: {pfd_mask_mdb} (id={mask_id})")
    except Exception as e:
        logger.warning(f"Could not load the mask for visualization: {e}")

    export_all(
        constellation, wcg_result, sim_result, compliance, config,
        static_wcg=static_wcg,
        static_sim_result=static_sim_result,
        static_compliance=static_compliance,
        output_dir=data_dir,
        sample_interval_s=args.sample_interval,
        pfd_mask=pfd_mask_viz,
    )

    if args.serve:
        if args.kill_port:
            kill_listeners_on_port(args.port)
        start_server(vis_dir, args.port)


if __name__ == "__main__":
    main()
