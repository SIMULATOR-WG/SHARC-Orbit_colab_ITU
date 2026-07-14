"""ITU-R S.1503-4 § D3 + Figure 13 — post-WCGA longitudinal adjustment.

After the WCGA (point-mass model) and with the run's fine step already defined (§ D4),
it computes the longitude difference of the dominant non-GSO between:
  (1) the sub-satellite point at t=0 using the WCGA ECI position (static); and
  (2) the full § D6.3 model (Kepler + secular J2 + the same Wdelta/RAAN options as D5),
      sampling at the fine step over the first orbit, choosing the instant where the
      geocentric latitude is closest to the target latitude (norm: the step that
      gives the closest latitude).

Applies ``corr = lon(2) − lon(1)`` (normalized to [−180°, 180°]) to ``es_lon_deg`` and
``gso_lon_deg`` of the ``WCGResult`` and updates ``es_ecef_exact`` — i.e. the ES/GSO
pair is translated to where the satellite *actually* crosses the target latitude in
the temporal model, preserving the WCGA's relative geometry
(``lon_full − es_after == lon_pm − es_before``), so the dominant satellite passes
through the worst-case geometry during its first orbit (Fig. 13).
"""
from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np

from .constants import MU_KM3_S2
from .coordinates import ecef_to_lla, lla_to_ecef, sub_satellite_point
from .orbit_propagator import OrbitalElements, build_constellation_cache, propagate_and_to_ecef_batch
from .wcg_search import WCGResult

logger = logging.getLogger(__name__)


def _normalize_lon_deg(lon_deg: float) -> float:
    return ((float(lon_deg) + 180.0) % 360.0) - 180.0


def _lon_delta_deg(lon_a_deg: float, lon_b_deg: float) -> float:
    """Difference lon_a − lon_b in [−180, 180]."""
    return _normalize_lon_deg(float(lon_a_deg) - float(lon_b_deg))


def _orbital_period_s_from_a(a_km: float) -> float:
    """Keplerian period (s) used to bound the first orbit in the scan."""
    a = max(float(a_km), 1.0)
    return 2.0 * math.pi * math.sqrt(a**3 / MU_KM3_S2)


def apply_s1503_figure13_wcg_longitude_adjustment(
    *,
    wcg_result: WCGResult,
    constellation: list[OrbitalElements],
    dominant_sat_idx: int,
    fine_dt_s: float,
    raan_dot_artificial_rad_s: float,
    raan_dot_override_rad_s: float | None,
    wdelta_deg: float,
    t_run_s: float,
    max_scan_samples: int = 500_000,
) -> dict[str, Any]:
    """Apply the Fig. 13 longitudinal shift to ``wcg_result`` (in-place).

    Returns metadata for logging / ``simulation`` meta (JSON-serializable).
    """
    out: dict[str, Any] = {"applied": False, "reason": "not_run"}

    ref = getattr(wcg_result, "ref_sat_eci", None)
    if ref is None or float(np.linalg.norm(np.asarray(ref, dtype=float).ravel()[:3])) < 1e-3:
        out["reason"] = "missing_ref_sat_eci"
        return out

    if fine_dt_s <= 0.0 or not math.isfinite(fine_dt_s):
        out["reason"] = "invalid_fine_dt"
        return out

    if dominant_sat_idx < 0 or dominant_sat_idx >= len(constellation):
        out["reason"] = "invalid_dominant_sat_idx"
        return out

    lat_spec, lon_pm = sub_satellite_point(np.asarray(ref, dtype=float).ravel()[:3], 0.0)

    oe_dom = constellation[dominant_sat_idx]
    T = _orbital_period_s_from_a(float(oe_dom.a))
    n_need = int(math.ceil(T / fine_dt_s)) + 2
    dt_scan = float(fine_dt_s)
    if n_need > max_scan_samples:
        dt_scan = T / max(max_scan_samples - 2, 2)
        logger.warning(
            "S.1503 Fig.13: scan limited to %d samples (T≈%.0f s, requested Δt %.6g s); "
            "effective scan Δt ≈%.6g s.",
            max_scan_samples,
            T,
            fine_dt_s,
            dt_scan,
        )

    # Only the dominant satellite's row is consumed by the scan; propagating
    # the whole constellation (N sats × up to max_scan_samples) is wasted
    # work. Propagation is per-satellite independent, so a single-element
    # constellation/cache yields an identical result.
    dominant_only = [oe_dom]
    cache = build_constellation_cache(dominant_only, raan_dot_override_rad_s=raan_dot_override_rad_s)

    best_err = float("inf")
    best_t = 0.0
    best_lon = lon_pm
    best_lat = lat_spec

    t = 0.0
    samples = 0
    while t <= T + 1e-9 and samples < max_scan_samples:
        pos_ecef, _ = propagate_and_to_ecef_batch(
            dominant_only,
            t_s=t,
            raan_dot_artificial_rad_s=raan_dot_artificial_rad_s,
            raan_dot_override_rad_s=raan_dot_override_rad_s,
            _cache=cache,
            wdelta_deg=float(wdelta_deg),
            t_run_s=float(t_run_s),
        )
        row = np.asarray(pos_ecef[0], dtype=float).ravel()[:3]
        lat_k, lon_k, _ = ecef_to_lla(row)
        err = abs(float(lat_k) - float(lat_spec))
        if err < best_err - 1e-15 or (abs(err - best_err) <= 1e-15 and t < best_t - 1e-15):
            best_err = err
            best_t = t
            best_lon = float(lon_k)
            best_lat = float(lat_k)
        t += dt_scan
        samples += 1

    # Shift ES/GSO to the satellite's REAL crossing longitude: the WCG geometry
    # is relative, so translating the pair by (lon_full − lon_pm) makes the
    # temporal-model satellite reproduce the WCGA configuration at t_best.
    # (lon_pm − lon_full — the opposite sign — would move the pair AWAY from
    # the crossing, doubling the mismatch instead of cancelling it.)
    corr = _lon_delta_deg(best_lon, lon_pm)
    if abs(corr) < 1e-14:
        out.update(
            {
                "applied": False,
                "reason": "zero_correction",
                "lat_spec_deg": lat_spec,
                "lon_pm_deg": lon_pm,
                "lon_full_deg": best_lon,
                "t_best_s": best_t,
                "lat_at_t_best_deg": best_lat,
                "corr_deg": 0.0,
            },
        )
        return out

    es0 = float(wcg_result.es_lon_deg)
    gs0 = float(wcg_result.gso_lon_deg)
    wcg_result.es_lon_deg = _normalize_lon_deg(es0 + corr)
    wcg_result.gso_lon_deg = _normalize_lon_deg(gs0 + corr)
    wcg_result.es_ecef_exact = lla_to_ecef(
        float(wcg_result.es_lat_deg),
        float(wcg_result.es_lon_deg),
        0.0,
    ).astype(np.float64, copy=True)

    out.update(
        {
            "applied": True,
            "reason": "ok",
            "lat_spec_deg": lat_spec,
            "lon_pm_deg": lon_pm,
            "lon_full_deg": best_lon,
            "t_best_s": best_t,
            "lat_at_t_best_deg": best_lat,
            "lat_err_deg": best_err,
            "corr_deg": corr,
            "es_lon_before_deg": es0,
            "gso_lon_before_deg": gs0,
            "es_lon_after_deg": float(wcg_result.es_lon_deg),
            "gso_lon_after_deg": float(wcg_result.gso_lon_deg),
            "dt_scan_s": dt_scan,
            "T_orbit_s": T,
            "samples": samples,
        },
    )
    logger.info(
        "S.1503-4 Fig.13: Δlon aplicado = %.6f° (lon_pm=%.4f°, lon_full=%.4f° @ t=%.3f s; "
        "lat_spec=%.4f°, |Δlat|=%.6g°; ES/GSO longitudes atualizadas).",
        corr,
        lon_pm,
        best_lon,
        best_t,
        lat_spec,
        best_err,
    )
    return out
