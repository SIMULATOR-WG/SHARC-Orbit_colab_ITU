"""Regression test for the S.1503-4 §D3 / Figure 13 WCG longitude adjustment.

The spec: after the WCGA (point-mass, static t=0), shift the ES/GSO pair by the
longitude difference between the WCGA's satellite position and the point where
the full §D6.3 orbit model actually crosses the target latitude (fine time
step, first orbit) — so the dominant satellite passes through the worst-case
geometry during the run.

The invariant this locks: the *relative* geometry is preserved —
``lon_full − es_after == lon_pm − es_before`` (mod 360). The opposite sign
(shipped once as a bug) moves the ES/GSO away from the real crossing and
doubles the mismatch.
"""
from __future__ import annotations

import math

import numpy as np

from src.orbit_propagator import (  # type: ignore[import]
    OrbitalElements, propagate_and_to_ecef_batch, build_constellation_cache,
)
from src.coordinates import ecef_to_lla, lla_to_ecef  # type: ignore[import]
from src.wcg_search import WCGResult  # type: ignore[import]
from src.s1503_figure13_wcg_lon import (  # type: ignore[import]
    apply_s1503_figure13_wcg_longitude_adjustment,
)


def _norm(x: float) -> float:
    return ((x + 180.0) % 360.0) - 180.0


def test_figure13_shift_preserves_relative_geometry():
    oe = OrbitalElements(a=7178.0, e=0.0, i=math.radians(50.0),
                         raan=0.0, omega=0.0, M=0.0)
    const = [oe]
    cache = build_constellation_cache(const, raan_dot_override_rad_s=None)

    # A latitude the satellite genuinely crosses (full model, t1=600 s).
    t1 = 600.0
    pos, _ = propagate_and_to_ecef_batch(
        const, t_s=t1, raan_dot_artificial_rad_s=0.0,
        raan_dot_override_rad_s=None, _cache=cache,
        wdelta_deg=0.0, t_run_s=0.0,
    )
    lat1, lon1, alt1 = ecef_to_lla(np.asarray(pos[0], dtype=float))

    # Fabricated WCGA snapshot: same latitude, longitude deliberately 7° east.
    lon_pm = _norm(lon1 + 7.0)
    ref_eci = lla_to_ecef(lat1, lon_pm, alt1)  # GMST0=0 → ECI == ECEF at t=0

    es_before, gso_before = 10.0, 15.0
    wcg = WCGResult(
        theta_deg=0, phi_deg=0, es_lat_deg=0.0, es_lon_deg=es_before,
        gso_lon_deg=gso_before, alpha_deg=0, offaxis_deg=0, pfd_dBW=0,
        es_gain_rel_dB=0, epfd_dBW=0, elevation_deg=0,
        es_ecef_exact=lla_to_ecef(0.0, es_before, 0.0),
        ref_sat_eci=np.asarray(ref_eci, dtype=float),
    )

    meta = apply_s1503_figure13_wcg_longitude_adjustment(
        wcg_result=wcg, constellation=const, dominant_sat_idx=0,
        fine_dt_s=1.0, raan_dot_artificial_rad_s=0.0,
        raan_dot_override_rad_s=None, wdelta_deg=0.0, t_run_s=0.0,
    )

    assert meta["applied"] is True
    # Shift = lon_full − lon_pm (≈ −7°): ES/GSO move toward the real crossing.
    assert abs(meta["corr_deg"] - (-7.0)) < 1e-6

    rel_wcga = _norm(lon_pm - es_before)
    rel_temporal = _norm(meta["lon_full_deg"] - float(wcg.es_lon_deg))
    assert abs(_norm(rel_temporal - rel_wcga)) < 1e-6

    # GSO shifted by the same amount (pair translated rigidly).
    assert abs(_norm(float(wcg.gso_lon_deg) - gso_before - meta["corr_deg"])) < 1e-9


def test_figure13_zero_when_models_agree():
    # If the WCGA snapshot coincides with the full model's crossing, no shift.
    oe = OrbitalElements(a=7178.0, e=0.0, i=math.radians(50.0),
                         raan=0.0, omega=0.0, M=0.0)
    const = [oe]
    cache = build_constellation_cache(const, raan_dot_override_rad_s=None)
    t1 = 600.0
    pos, _ = propagate_and_to_ecef_batch(
        const, t_s=t1, raan_dot_artificial_rad_s=0.0,
        raan_dot_override_rad_s=None, _cache=cache,
        wdelta_deg=0.0, t_run_s=0.0,
    )
    lat1, lon1, alt1 = ecef_to_lla(np.asarray(pos[0], dtype=float))
    wcg = WCGResult(
        theta_deg=0, phi_deg=0, es_lat_deg=0.0, es_lon_deg=10.0,
        gso_lon_deg=15.0, alpha_deg=0, offaxis_deg=0, pfd_dBW=0,
        es_gain_rel_dB=0, epfd_dBW=0, elevation_deg=0,
        es_ecef_exact=lla_to_ecef(0.0, 10.0, 0.0),
        ref_sat_eci=np.asarray(lla_to_ecef(lat1, lon1, alt1), dtype=float),
    )
    meta = apply_s1503_figure13_wcg_longitude_adjustment(
        wcg_result=wcg, constellation=const, dominant_sat_idx=0,
        fine_dt_s=1.0, raan_dot_artificial_rad_s=0.0,
        raan_dot_override_rad_s=None, wdelta_deg=0.0, t_run_s=0.0,
    )
    assert abs(meta.get("corr_deg", 0.0)) < 1e-9
    assert abs(float(wcg.es_lon_deg) - 10.0) < 1e-9
