"""
geometry.py — Geometric calculations for the WCG algorithm.

Main functions:
  • Computation of the alpha angle (α) — minimum angle at the ES between the
    direction of the non-GSO satellite and any point on the GSO arc
  • Computation of the X angle — minimum angle at the non-GSO satellite between
    the direction of the ES and any point on the GSO arc
  • Computation of the off-axis angle (φ) of the GSO ES toward the non-GSO
  • Geometry (θ, φ) seen from the non-GSO satellite → position of the ES and the GSO satellite
  • Angular velocity of the non-GSO satellite seen from the ES

Reference: ITU-R S.1503-4, Part D, Section D.3.1.2 / D.6.4.
"""

from __future__ import annotations
import math
import logging
import os
import numpy as np
try:
    from numba import njit, prange
    _NUMBA_AVAILABLE = True
except Exception:
    _NUMBA_AVAILABLE = False
    prange = range  # type: ignore

    def njit(*args, **kwargs):
        def _decorator(fn):
            return fn
        return _decorator
from .constants import (
    RE_KM, GSO_RADIUS_KM, DEG2RAD, RAD2DEG, OMEGA_E
)
from .coordinates import (
    unit_vector, angle_between,
    eci_to_ecef, ecef_to_eci,
    lla_to_ecef, ecef_to_lla,
    topocentric_angles, gso_position_ecef,
    gso_position_eci,
)

logger = logging.getLogger(__name__)
_MAIN_PID = os.getpid()
_DISABLE_NUMBA_ENV = os.environ.get("WCG_DISABLE_NUMBA", "").strip().lower() in {
    "1", "true", "yes", "on",
}
if _DISABLE_NUMBA_ENV and _NUMBA_AVAILABLE:
    _NUMBA_AVAILABLE = False

_WARNED_NUMBA_ALPHA_FALLBACK = False
_WARNED_ALPHA_EXPLICIT_RANGE_PATH = False
_LOGGED_NUMBA_PATHS: set[str] = set()

# ---------------------------------------------------------------------------
#  Selection of the alpha computation method: "sweep" (sweep + ternary)
#  or "analytical" (Newton with analytical initial point — ~20× faster).
# ---------------------------------------------------------------------------
_ALPHA_METHOD: str = "sweep"


def set_alpha_method(method: str) -> None:
    """Set the alpha computation algorithm globally."""
    global _ALPHA_METHOD
    if method not in ("sweep", "analytical"):
        raise ValueError(f"invalid alpha_method: {method!r} (use 'sweep' or 'analytical')")
    _ALPHA_METHOD = method
    _log_numba_path_once(
        f"alpha_method_set_{method}",
        f"Alpha computation method set: {method}",
    )


def get_alpha_method() -> str:
    return _ALPHA_METHOD


# ---------------------------------------------------------------------------
#  Reference GSO longitude for α / geometry:
#    "arc_optimal" — minimize |α| along the visible GSO arc (current implementation);
#    "es_meridian" — GSO at the candidate ES longitude (legacy, a single point on the arc).
# ---------------------------------------------------------------------------
_GSO_LON_MODE: str = "arc_optimal"


def set_gso_longitude_mode(mode: str) -> None:
    """Set how to choose the GSO point for α, off-axis and PFD (WCGA / WCG)."""
    global _GSO_LON_MODE
    m = str(mode).strip().lower()
    if m not in ("arc_optimal", "es_meridian"):
        raise ValueError(
            f"invalid gso_longitude_mode: {mode!r} (use 'arc_optimal' or 'es_meridian')"
        )
    _GSO_LON_MODE = m
    _log_numba_path_once(
        f"gso_lon_mode_{m}",
        f"GSO longitude mode for α: {m}",
    )


def get_gso_longitude_mode() -> str:
    return _GSO_LON_MODE


def alpha_batch_uses_numba_parallel() -> bool:
    """Indicate whether the alpha batch kernels already exploit internal parallelism."""
    return bool(_NUMBA_AVAILABLE)


def get_numba_num_threads() -> int:
    """Return the number of threads Numba will use for prange."""
    if not _NUMBA_AVAILABLE:
        return 1
    try:
        import numba
        return numba.config.NUMBA_NUM_THREADS
    except Exception:
        return os.cpu_count() or 1


def set_numba_num_threads(n: int) -> None:
    """Limit the number of Numba threads in the current process.

    Must be called before any ``parallel=True`` kernel is invoked in this
    process (ideally in the Pool initializer).
    """
    if not _NUMBA_AVAILABLE:
        return
    try:
        import numba
        numba.set_num_threads(max(1, n))
    except Exception:
        pass


def _is_main_process_for_fallback_log() -> bool:
    """Return True only in the main process of the WCG run."""
    main_pid = os.environ.get("WCG_MAIN_PID")
    if main_pid is None:
        return os.getpid() == _MAIN_PID
    return str(os.getpid()) == str(main_pid)


def _log_numba_path_once(tag: str, message: str, *, level: str = "info") -> None:
    """Log once per process which path (Numba/Python) was used."""
    if tag in _LOGGED_NUMBA_PATHS:
        return
    if not _is_main_process_for_fallback_log():
        return
    _LOGGED_NUMBA_PATHS.add(tag)
    if level == "warning":
        logger.warning(message)
    else:
        logger.info(message)


# =====================================================================
#  Alpha angle (α) — at the ES, between non-GSO and GSO arc
# =====================================================================


@njit(cache=True, fastmath=True)
def _wrap_deg_180_numba(angle_deg: float) -> float:
    while angle_deg > 180.0:
        angle_deg -= 360.0
    while angle_deg < -180.0:
        angle_deg += 360.0
    return angle_deg


@njit(cache=True, fastmath=True)
def _visible_gso_arc_halfwidth_deg_numba(es_lat_deg: float) -> float:
    # ITU-R S.1503-4 §D6.4.4.4 + §D6.4.4.2 (x2=0): cos(ΔLong_max) = (Re/Rgeo) / cos(LatES).
    cos_lat = math.cos(es_lat_deg * DEG2RAD)
    if abs(cos_lat) < 1e-12:
        return 0.0
    cos_theta_max = (RE_KM / GSO_RADIUS_KM) / cos_lat
    if cos_theta_max < -1.0:
        cos_theta_max = -1.0
    elif cos_theta_max > 1.0:
        cos_theta_max = 1.0
    return math.degrees(math.acos(cos_theta_max))


@njit(cache=True, fastmath=True)
def _wrap_pi_rad_numba(a: float) -> float:
    # Wrap to (-π, π].
    two_pi = 2.0 * math.pi
    while a > math.pi:
        a -= two_pi
    while a <= -math.pi:
        a += two_pi
    return a


@njit(cache=True, fastmath=True)
def _eval_cos_alpha_at_lam_numba(
    lam: float,
    ux: float, uy: float, uz: float,
    es_x: float, es_y: float, es_z: float,
    R: float,
) -> float:
    cl = math.cos(lam)
    sl = math.sin(lam)
    dx = R * cl - es_x
    dy = R * sl - es_y
    dz = -es_z
    norm = math.sqrt(dx * dx + dy * dy + dz * dz)
    if norm < 1e-12:
        return -1.0
    return (dx * ux + dy * uy + dz * uz) / norm


@njit(cache=True, fastmath=True)
def _newton_lambda_alpha_numba(
    lam0: float, lo: float, hi: float,
    ux: float, uy: float, uz: float,
    es_x: float, es_y: float, es_z: float,
    R: float,
):
    lam = lam0
    if lam < lo:
        lam = lo
    elif lam > hi:
        lam = hi
    for _ in range(8):
        cl = math.cos(lam)
        sl = math.sin(lam)
        dx = R * cl - es_x
        dy = R * sl - es_y
        dz = -es_z
        D2 = dx * dx + dy * dy + dz * dz
        N = ux * dx + uy * dy + uz * dz
        Np = R * (-ux * sl + uy * cl)
        D2p = 2.0 * R * (es_x * sl - es_y * cl)
        g = Np * D2 - N * D2p * 0.5
        Npp = R * (-ux * cl - uy * sl)
        D2pp = 2.0 * R * (es_x * cl + es_y * sl)
        gp = Npp * D2 + Np * D2p * 0.5 - N * D2pp * 0.5
        if abs(gp) < 1e-30:
            break
        dlam = -g / gp
        lam += dlam
        if lam < lo:
            lam = lo
        elif lam > hi:
            lam = hi
        if abs(dlam) < 1e-8:
            break
    return lam, _eval_cos_alpha_at_lam_numba(lam, ux, uy, uz, es_x, es_y, es_z, R)


@njit(cache=True, fastmath=True)
def _s1503_tie_break_better_numba(
    lam_a: float, cos_a: float,
    lam_b: float, cos_b: float,
    lon_ngso_rad: float,
) -> bool:
    # Return True if candidate b is better than a, per S.1503-4 §D6.4.4.1:
    # 1) larger cos α (smaller α);
    # 2) tie: smaller |ΔLong| where ΔLong = wrap(lam − LongNGSO);
    # 3) tie: positive ΔLong preferred.
    tol_cos = 1e-12
    if cos_b > cos_a + tol_cos:
        return True
    if cos_b < cos_a - tol_cos:
        return False
    d_a = _wrap_pi_rad_numba(lam_a - lon_ngso_rad)
    d_b = _wrap_pi_rad_numba(lam_b - lon_ngso_rad)
    ad_a = abs(d_a)
    ad_b = abs(d_b)
    tol_dlon = 1e-9
    if ad_b < ad_a - tol_dlon:
        return True
    if ad_b > ad_a + tol_dlon:
        return False
    return d_b > d_a


@njit(cache=True, fastmath=True)
def _gso_alpha_cos_numba(
    d_lon_deg: float,
    es_lon_deg: float,
    es_x: float,
    es_y: float,
    es_z: float,
    ux: float,
    uy: float,
    uz: float,
) -> float:
    lon_rad = math.radians(es_lon_deg + d_lon_deg)
    gx = GSO_RADIUS_KM * math.cos(lon_rad) - es_x
    gy = GSO_RADIUS_KM * math.sin(lon_rad) - es_y
    gz = -es_z
    norm = math.sqrt(gx * gx + gy * gy + gz * gz)
    if norm < 1e-12:
        return -1.0
    return (gx * ux + gy * uy + gz * uz) / norm


@njit(cache=True, fastmath=True)
def _alpha_sign_xy_plane_numba(
    es_x: float,
    es_y: float,
    es_z: float,
    dxn: float,
    dyn: float,
    dzn: float,
    gso_radius_km: float,
) -> float:
    """Sign of α per ITU-R S.1503-4 §D6.4.4.1.

    Builds the line R = R_ES + λ·R_EN where R_EN = R_NGSO − R_ES, finds the
    intersection with the XY plane (R(z)=0 ⇒ λ = −es_z/dzn) and compares |R_z=0| with Rgeo.

    es_*: ES position in km ECEF (or ECI — the Z axis is the polar one in both).
    dxn, dyn, dzn: R_EN vector (ECEF/ECI, km).
    """
    # Equator: α = -sign(R_EN.z)
    if abs(es_z) < 1e-9:
        if dzn > 0.0:
            return -1.0
        elif dzn < 0.0:
            return 1.0
        return 0.0

    # No FORWARD crossing of the XY plane (λ<0, or line parallel to it):
    # R_z=0 = Infinity. Geometrically the R-vs-Rgeo comparison IS the
    # below/above-the-visible-arc test (a LoS pointing exactly at the arc
    # crosses at R = Rgeo), so ∞ counts as "above the arc": α negative for a
    # NORTHERN ES, α POSITIVE for a SOUTHERN one (Figs 57/59 — e.g. the
    # zenith direction over a southern ES is above the arc ⇒ α > 0).
    if abs(dzn) < 1e-12:
        return -1.0 if es_z > 0.0 else 1.0

    lam = -es_z / dzn
    if lam < 0.0:
        return -1.0 if es_z > 0.0 else 1.0

    rxz = es_x + lam * dxn
    ryz = es_y + lam * dyn
    r_z0 = math.sqrt(rxz * rxz + ryz * ryz)

    if es_z > 0.0:  # Northern Hemisphere
        if r_z0 < gso_radius_km:
            return 1.0
        elif r_z0 > gso_radius_km:
            return -1.0
        return 0.0

    # Southern Hemisphere (es_z < 0) — mirrored comparison
    if r_z0 > gso_radius_km:
        return 1.0
    elif r_z0 < gso_radius_km:
        return -1.0
    return 0.0


@njit(cache=True, fastmath=True)
def _compute_alpha_angle_es_meridian_numba(
    es_x: float,
    es_y: float,
    es_z: float,
    ng_x: float,
    ng_y: float,
    ng_z: float,
    es_lat_deg: float,
    es_lon_deg: float,
) -> float:
    """Alpha (signed) using a GSO fixed on the ES meridian (legacy mode)."""
    lon_gso = math.radians(es_lon_deg)
    gx_abs = GSO_RADIUS_KM * math.cos(lon_gso)
    gy_abs = GSO_RADIUS_KM * math.sin(lon_gso)
    gz_abs = 0.0

    dxn = ng_x - es_x
    dyn = ng_y - es_y
    dzn = ng_z - es_z
    dxx = gx_abs - es_x
    dxy = gy_abs - es_y
    dxz = gz_abs - es_z

    n_ngso = math.sqrt(dxn * dxn + dyn * dyn + dzn * dzn)
    n_gso = math.sqrt(dxx * dxx + dxy * dxy + dxz * dxz)
    if n_ngso < 1e-9 or n_gso < 1e-9:
        return 0.0

    ux, uy, uz = dxn / n_ngso, dyn / n_ngso, dzn / n_ngso
    vx, vy, vz = dxx / n_gso, dxy / n_gso, dxz / n_gso
    c = ux * vx + uy * vy + uz * vz
    if c < -1.0:
        c = -1.0
    elif c > 1.0:
        c = 1.0
    min_alpha = math.degrees(math.acos(c))

    # ITU-R S.1503-4 §D6.4.4.1: sign of α via intersection of the ES→NGSO line with the XY plane.
    sign = _alpha_sign_xy_plane_numba(es_x, es_y, es_z, dxn, dyn, dzn, GSO_RADIUS_KM)
    return sign * min_alpha


@njit(cache=True, fastmath=True)
def _compute_alpha_angle_numba(
    es_x: float,
    es_y: float,
    es_z: float,
    ng_x: float,
    ng_y: float,
    ng_z: float,
    es_lat_deg: float,
    es_lon_deg: float,
    step_deg: float,
) -> float:
    search_range_deg = _visible_gso_arc_halfwidth_deg_numba(es_lat_deg)

    dxn = ng_x - es_x
    dyn = ng_y - es_y
    dzn = ng_z - es_z
    n_ngso = math.sqrt(dxn * dxn + dyn * dyn + dzn * dzn)
    if n_ngso < 1e-12:
        return 0.0
    ux, uy, uz = dxn / n_ngso, dyn / n_ngso, dzn / n_ngso

    # Stage 1: coarse sweep (no array allocation)
    best_cos = -2.0
    best_d_lon = 0.0
    d = -search_range_deg
    eps = 1e-12
    while d <= search_range_deg + eps:
        c = _gso_alpha_cos_numba(d, es_lon_deg, es_x, es_y, es_z, ux, uy, uz)
        if c > best_cos:
            best_cos = c
            best_d_lon = d
        d += step_deg

    # Stage 2: local ternary search
    lo_t = max(-search_range_deg, best_d_lon - step_deg)
    hi_t = min(search_range_deg, best_d_lon + step_deg)
    tol = 1e-4
    it = 0
    while it < 60 and (hi_t - lo_t) >= tol:
        m1 = lo_t + (hi_t - lo_t) / 3.0
        m2 = hi_t - (hi_t - lo_t) / 3.0
        c1 = _gso_alpha_cos_numba(m1, es_lon_deg, es_x, es_y, es_z, ux, uy, uz)
        c2 = _gso_alpha_cos_numba(m2, es_lon_deg, es_x, es_y, es_z, ux, uy, uz)
        if c1 < c2:
            lo_t = m1
        else:
            hi_t = m2
        it += 1

    best_d_lon_fine = 0.5 * (lo_t + hi_t)
    cos_fine = _gso_alpha_cos_numba(best_d_lon_fine, es_lon_deg, es_x, es_y, es_z, ux, uy, uz)

    # Tie-break §D6.4.4.1 between the refined optimum and the visibility edges.
    es_lon_rad = math.radians(es_lon_deg)
    R = GSO_RADIUS_KM
    lon_ngso_rad = math.atan2(ng_y, ng_x)
    best_lam = es_lon_rad + math.radians(best_d_lon_fine)
    best_cos = cos_fine

    edge_lo_lam = es_lon_rad - math.radians(search_range_deg)
    edge_hi_lam = es_lon_rad + math.radians(search_range_deg)
    cos_lo_e = _eval_cos_alpha_at_lam_numba(edge_lo_lam, ux, uy, uz, es_x, es_y, es_z, R)
    if _s1503_tie_break_better_numba(best_lam, best_cos, edge_lo_lam, cos_lo_e, lon_ngso_rad):
        best_lam = edge_lo_lam
        best_cos = cos_lo_e
    cos_hi_e = _eval_cos_alpha_at_lam_numba(edge_hi_lam, ux, uy, uz, es_x, es_y, es_z, R)
    if _s1503_tie_break_better_numba(best_lam, best_cos, edge_hi_lam, cos_hi_e, lon_ngso_rad):
        best_lam = edge_hi_lam
        best_cos = cos_hi_e

    if best_cos < -1.0:
        best_cos = -1.0
    elif best_cos > 1.0:
        best_cos = 1.0
    min_alpha = math.degrees(math.acos(best_cos))

    # ITU-R S.1503-4 §D6.4.4.1: sign of α via intersection of the ES→NGSO line with the XY plane.
    sign = _alpha_sign_xy_plane_numba(es_x, es_y, es_z, dxn, dyn, dzn, GSO_RADIUS_KM)
    return sign * min_alpha


@njit(cache=True, fastmath=True)
def _compute_alpha_angle_direct_numba(
    es_x: float,
    es_y: float,
    es_z: float,
    ng_x: float,
    ng_y: float,
    ng_z: float,
    es_lat_deg: float,
    es_lon_deg: float,
) -> float:
    """Alpha via Newton-Raphson with two starting points (S.1503-4 §D6.4.4.4).

    Solves d/dλ [cos α(λ)] = 0 with Newton starting at atan2(uy,ux) and at
    atan2(uy,ux)+π (the spec predicts two roots of the quartic — x=±1).
    Tie-break §D6.4.4.1: smaller |ΔLong|, then positive ΔLong.
    """
    search_range_deg = _visible_gso_arc_halfwidth_deg_numba(es_lat_deg)

    dxn = ng_x - es_x
    dyn = ng_y - es_y
    dzn = ng_z - es_z
    n_ngso = math.sqrt(dxn * dxn + dyn * dyn + dzn * dzn)
    if n_ngso < 1e-12:
        return 0.0
    ux = dxn / n_ngso
    uy = dyn / n_ngso
    uz = dzn / n_ngso

    es_lon_rad = math.radians(es_lon_deg)
    lo = es_lon_rad - math.radians(search_range_deg)
    hi = es_lon_rad + math.radians(search_range_deg)

    R = GSO_RADIUS_KM

    lon_ngso_rad = math.atan2(ng_y, ng_x)

    start_a = math.atan2(uy, ux)
    start_b = _wrap_pi_rad_numba(start_a + math.pi)

    lam_a, cos_a = _newton_lambda_alpha_numba(start_a, lo, hi, ux, uy, uz, es_x, es_y, es_z, R)
    lam_b, cos_b = _newton_lambda_alpha_numba(start_b, lo, hi, ux, uy, uz, es_x, es_y, es_z, R)

    best_lam = lam_a
    best_cos = cos_a
    if _s1503_tie_break_better_numba(best_lam, best_cos, lam_b, cos_b, lon_ngso_rad):
        best_lam = lam_b
        best_cos = cos_b

    cos_lo = _eval_cos_alpha_at_lam_numba(lo, ux, uy, uz, es_x, es_y, es_z, R)
    if _s1503_tie_break_better_numba(best_lam, best_cos, lo, cos_lo, lon_ngso_rad):
        best_lam = lo
        best_cos = cos_lo
    cos_hi = _eval_cos_alpha_at_lam_numba(hi, ux, uy, uz, es_x, es_y, es_z, R)
    if _s1503_tie_break_better_numba(best_lam, best_cos, hi, cos_hi, lon_ngso_rad):
        best_lam = hi
        best_cos = cos_hi

    if best_cos < -1.0:
        best_cos = -1.0
    elif best_cos > 1.0:
        best_cos = 1.0
    min_alpha = math.degrees(math.acos(best_cos))

    # ITU-R S.1503-4 §D6.4.4.1: sign of α via intersection of the ES→NGSO line with the XY plane.
    sign = _alpha_sign_xy_plane_numba(es_x, es_y, es_z, dxn, dyn, dzn, GSO_RADIUS_KM)
    return sign * min_alpha


@njit(cache=True, fastmath=True)
def _compute_alpha_angle_direct_numba_with_gso(
    es_x: float,
    es_y: float,
    es_z: float,
    ng_x: float,
    ng_y: float,
    ng_z: float,
    es_lat_deg: float,
    es_lon_deg: float,
):
    """Same as _compute_alpha_angle_direct_numba, but also returns the GSO (km ECEF) on the arc.

    ITU-R S.1503-4 §D6.4.4.4: Newton-Raphson with **two starting points** (the spec
    predicts two roots of the quartic — x=+1 and x=-1; here in the equivalent λ
    parametrization: atan2(uy,ux) and atan2(uy,ux)+π). Tie-break §D6.4.4.1: smaller
    |ΔLong|, then positive ΔLong.
    """
    search_range_deg = _visible_gso_arc_halfwidth_deg_numba(es_lat_deg)

    dxn = ng_x - es_x
    dyn = ng_y - es_y
    dzn = ng_z - es_z
    n_ngso = math.sqrt(dxn * dxn + dyn * dyn + dzn * dzn)
    if n_ngso < 1e-12:
        lon0 = math.radians(es_lon_deg)
        R = GSO_RADIUS_KM
        return 0.0, R * math.cos(lon0), R * math.sin(lon0), 0.0
    ux = dxn / n_ngso
    uy = dyn / n_ngso
    uz = dzn / n_ngso

    es_lon_rad = math.radians(es_lon_deg)
    lo = es_lon_rad - math.radians(search_range_deg)
    hi = es_lon_rad + math.radians(search_range_deg)

    R = GSO_RADIUS_KM

    # LongNGSO in rad (NGSO sub-satellite) for tie-break §D6.4.4.1.
    lon_ngso_rad = math.atan2(ng_y, ng_x)

    # Two Newton starting points (§D6.4.4.4: x=+1 and x=-1).
    start_a = math.atan2(uy, ux)
    start_b = _wrap_pi_rad_numba(start_a + math.pi)

    lam_a, cos_a = _newton_lambda_alpha_numba(start_a, lo, hi, ux, uy, uz, es_x, es_y, es_z, R)
    lam_b, cos_b = _newton_lambda_alpha_numba(start_b, lo, hi, ux, uy, uz, es_x, es_y, es_z, R)

    best_lam = lam_a
    best_cos = cos_a
    if _s1503_tie_break_better_numba(best_lam, best_cos, lam_b, cos_b, lon_ngso_rad):
        best_lam = lam_b
        best_cos = cos_b

    # Visibility edges as additional candidates (§D6.4.4.1 explicitly mentions
    # "two edge of visibility points").
    cos_lo = _eval_cos_alpha_at_lam_numba(lo, ux, uy, uz, es_x, es_y, es_z, R)
    if _s1503_tie_break_better_numba(best_lam, best_cos, lo, cos_lo, lon_ngso_rad):
        best_lam = lo
        best_cos = cos_lo
    cos_hi = _eval_cos_alpha_at_lam_numba(hi, ux, uy, uz, es_x, es_y, es_z, R)
    if _s1503_tie_break_better_numba(best_lam, best_cos, hi, cos_hi, lon_ngso_rad):
        best_lam = hi
        best_cos = cos_hi

    if best_cos < -1.0:
        best_cos = -1.0
    elif best_cos > 1.0:
        best_cos = 1.0
    min_alpha = math.degrees(math.acos(best_cos))

    # ITU-R S.1503-4 §D6.4.4.1: sign of α via intersection of the ES→NGSO line with the XY plane.
    sign = _alpha_sign_xy_plane_numba(es_x, es_y, es_z, dxn, dyn, dzn, GSO_RADIUS_KM)
    gso_x_abs = R * math.cos(best_lam)
    gso_y_abs = R * math.sin(best_lam)
    return sign * min_alpha, gso_x_abs, gso_y_abs, 0.0


@njit(cache=True, fastmath=True)
def _compute_alpha_angle_numba_with_gso(
    es_x: float,
    es_y: float,
    es_z: float,
    ng_x: float,
    ng_y: float,
    ng_z: float,
    es_lat_deg: float,
    es_lon_deg: float,
    step_deg: float,
):
    """Sweep + ternary, with the optimal GSO position on the visible arc.

    ITU-R S.1503-4 §D6.4.4.1: tie-break (smaller |ΔLong|, then positive) between
    the refined optimum and the visibility edges.
    """
    search_range_deg = _visible_gso_arc_halfwidth_deg_numba(es_lat_deg)

    dxn = ng_x - es_x
    dyn = ng_y - es_y
    dzn = ng_z - es_z
    n_ngso = math.sqrt(dxn * dxn + dyn * dyn + dzn * dzn)
    if n_ngso < 1e-12:
        lon0 = math.radians(es_lon_deg)
        R = GSO_RADIUS_KM
        return 0.0, R * math.cos(lon0), R * math.sin(lon0), 0.0
    ux, uy, uz = dxn / n_ngso, dyn / n_ngso, dzn / n_ngso

    best_cos = -2.0
    best_d_lon = 0.0
    d = -search_range_deg
    eps = 1e-12
    while d <= search_range_deg + eps:
        c = _gso_alpha_cos_numba(d, es_lon_deg, es_x, es_y, es_z, ux, uy, uz)
        if c > best_cos:
            best_cos = c
            best_d_lon = d
        d += step_deg

    lo_t = max(-search_range_deg, best_d_lon - step_deg)
    hi_t = min(search_range_deg, best_d_lon + step_deg)
    tol = 1e-4
    it = 0
    while it < 60 and (hi_t - lo_t) >= tol:
        m1 = lo_t + (hi_t - lo_t) / 3.0
        m2 = hi_t - (hi_t - lo_t) / 3.0
        c1 = _gso_alpha_cos_numba(m1, es_lon_deg, es_x, es_y, es_z, ux, uy, uz)
        c2 = _gso_alpha_cos_numba(m2, es_lon_deg, es_x, es_y, es_z, ux, uy, uz)
        if c1 < c2:
            lo_t = m1
        else:
            hi_t = m2
        it += 1

    best_d_lon_fine = 0.5 * (lo_t + hi_t)
    cos_fine = _gso_alpha_cos_numba(best_d_lon_fine, es_lon_deg, es_x, es_y, es_z, ux, uy, uz)

    es_lon_rad = math.radians(es_lon_deg)
    R = GSO_RADIUS_KM
    lon_ngso_rad = math.atan2(ng_y, ng_x)
    best_lam = es_lon_rad + math.radians(best_d_lon_fine)
    best_cos_v = cos_fine

    edge_lo_lam = es_lon_rad - math.radians(search_range_deg)
    edge_hi_lam = es_lon_rad + math.radians(search_range_deg)
    cos_lo_e = _eval_cos_alpha_at_lam_numba(edge_lo_lam, ux, uy, uz, es_x, es_y, es_z, R)
    if _s1503_tie_break_better_numba(best_lam, best_cos_v, edge_lo_lam, cos_lo_e, lon_ngso_rad):
        best_lam = edge_lo_lam
        best_cos_v = cos_lo_e
    cos_hi_e = _eval_cos_alpha_at_lam_numba(edge_hi_lam, ux, uy, uz, es_x, es_y, es_z, R)
    if _s1503_tie_break_better_numba(best_lam, best_cos_v, edge_hi_lam, cos_hi_e, lon_ngso_rad):
        best_lam = edge_hi_lam
        best_cos_v = cos_hi_e

    if best_cos_v < -1.0:
        best_cos_v = -1.0
    elif best_cos_v > 1.0:
        best_cos_v = 1.0
    min_alpha = math.degrees(math.acos(best_cos_v))

    # ITU-R S.1503-4 §D6.4.4.1: sign of α via intersection of the ES→NGSO line with the XY plane.
    sign = _alpha_sign_xy_plane_numba(es_x, es_y, es_z, dxn, dyn, dzn, GSO_RADIUS_KM)
    gso_x_abs = R * math.cos(best_lam)
    gso_y_abs = R * math.sin(best_lam)
    return sign * min_alpha, gso_x_abs, gso_y_abs, 0.0


if _NUMBA_AVAILABLE:
    @njit(parallel=True, cache=True, fastmath=True)
    def _compute_alpha_and_optimal_gso_fixed_es_direct_numba(
        es_x: float,
        es_y: float,
        es_z: float,
        ng_x: np.ndarray,
        ng_y: np.ndarray,
        ng_z: np.ndarray,
        es_lat_deg: float,
        es_lon_deg: float,
    ) -> tuple:
        n = ng_x.shape[0]
        alpha = np.empty(n, dtype=np.float64)
        gxo = np.empty(n, dtype=np.float64)
        gyo = np.empty(n, dtype=np.float64)
        gzo = np.empty(n, dtype=np.float64)
        for i in prange(n):
            a, gx, gy, gz = _compute_alpha_angle_direct_numba_with_gso(
                es_x, es_y, es_z,
                ng_x[i], ng_y[i], ng_z[i],
                es_lat_deg, es_lon_deg,
            )
            alpha[i] = a
            gxo[i] = gx
            gyo[i] = gy
            gzo[i] = gz
        return alpha, gxo, gyo, gzo

    @njit(parallel=True, cache=True, fastmath=True)
    def _compute_alpha_and_optimal_gso_fixed_es_sweep_numba(
        es_x: float,
        es_y: float,
        es_z: float,
        ng_x: np.ndarray,
        ng_y: np.ndarray,
        ng_z: np.ndarray,
        es_lat_deg: float,
        es_lon_deg: float,
        step_deg: float,
    ) -> tuple:
        n = ng_x.shape[0]
        alpha = np.empty(n, dtype=np.float64)
        gxo = np.empty(n, dtype=np.float64)
        gyo = np.empty(n, dtype=np.float64)
        gzo = np.empty(n, dtype=np.float64)
        for i in prange(n):
            a, gx, gy, gz = _compute_alpha_angle_numba_with_gso(
                es_x, es_y, es_z,
                ng_x[i], ng_y[i], ng_z[i],
                es_lat_deg, es_lon_deg,
                step_deg,
            )
            alpha[i] = a
            gxo[i] = gx
            gyo[i] = gy
            gzo[i] = gz
        return alpha, gxo, gyo, gzo

    @njit(parallel=True, cache=True, fastmath=True)
    def _compute_alpha_angle_multi_es_batch_numba(
        es_x: np.ndarray,
        es_y: np.ndarray,
        es_z: np.ndarray,
        ng_x: float,
        ng_y: float,
        ng_z: float,
        es_lat_deg: np.ndarray,
        es_lon_deg: np.ndarray,
        step_deg: float,
    ) -> np.ndarray:
        n = es_x.shape[0]
        out = np.empty(n, dtype=np.float64)
        for i in prange(n):
            out[i] = _compute_alpha_angle_numba(
                es_x[i], es_y[i], es_z[i],
                ng_x, ng_y, ng_z,
                es_lat_deg[i], es_lon_deg[i], step_deg,
            )
        return out

    @njit(parallel=True, cache=True, fastmath=True)
    def _compute_alpha_angle_multi_es_batch_direct_numba(
        es_x: np.ndarray,
        es_y: np.ndarray,
        es_z: np.ndarray,
        ng_x: float,
        ng_y: float,
        ng_z: float,
        es_lat_deg: np.ndarray,
        es_lon_deg: np.ndarray,
    ) -> np.ndarray:
        n = es_x.shape[0]
        out = np.empty(n, dtype=np.float64)
        for i in prange(n):
            out[i] = _compute_alpha_angle_direct_numba(
                es_x[i], es_y[i], es_z[i],
                ng_x, ng_y, ng_z,
                es_lat_deg[i], es_lon_deg[i],
            )
        return out

    @njit(parallel=True, cache=True, fastmath=True)
    def _compute_alpha_angle_multi_es_batch_es_meridian_numba(
        es_x: np.ndarray,
        es_y: np.ndarray,
        es_z: np.ndarray,
        ng_x: float,
        ng_y: float,
        ng_z: float,
        es_lat_deg: np.ndarray,
        es_lon_deg: np.ndarray,
    ) -> np.ndarray:
        n = es_x.shape[0]
        out = np.empty(n, dtype=np.float64)
        for i in prange(n):
            out[i] = _compute_alpha_angle_es_meridian_numba(
                es_x[i], es_y[i], es_z[i],
                ng_x, ng_y, ng_z,
                es_lat_deg[i], es_lon_deg[i],
            )
        return out

    @njit(parallel=True, cache=True, fastmath=True)
    def _compute_alpha_and_optimal_gso_multi_es_direct_numba(
        es_x: np.ndarray,
        es_y: np.ndarray,
        es_z: np.ndarray,
        ng_x: float,
        ng_y: float,
        ng_z: float,
        es_lat_deg: np.ndarray,
        es_lon_deg: np.ndarray,
    ) -> tuple:
        n = es_x.shape[0]
        alpha = np.empty(n, dtype=np.float64)
        gxo = np.empty(n, dtype=np.float64)
        gyo = np.empty(n, dtype=np.float64)
        gzo = np.empty(n, dtype=np.float64)
        for i in prange(n):
            a, gx, gy, gz = _compute_alpha_angle_direct_numba_with_gso(
                es_x[i], es_y[i], es_z[i],
                ng_x, ng_y, ng_z,
                es_lat_deg[i], es_lon_deg[i],
            )
            alpha[i] = a
            gxo[i] = gx
            gyo[i] = gy
            gzo[i] = gz
        return alpha, gxo, gyo, gzo

    @njit(parallel=True, cache=True, fastmath=True)
    def _compute_alpha_and_optimal_gso_multi_es_sweep_numba(
        es_x: np.ndarray,
        es_y: np.ndarray,
        es_z: np.ndarray,
        ng_x: float,
        ng_y: float,
        ng_z: float,
        es_lat_deg: np.ndarray,
        es_lon_deg: np.ndarray,
        step_deg: float,
    ) -> tuple:
        n = es_x.shape[0]
        alpha = np.empty(n, dtype=np.float64)
        gxo = np.empty(n, dtype=np.float64)
        gyo = np.empty(n, dtype=np.float64)
        gzo = np.empty(n, dtype=np.float64)
        for i in prange(n):
            a, gx, gy, gz = _compute_alpha_angle_numba_with_gso(
                es_x[i], es_y[i], es_z[i],
                ng_x, ng_y, ng_z,
                es_lat_deg[i], es_lon_deg[i],
                step_deg,
            )
            alpha[i] = a
            gxo[i] = gx
            gyo[i] = gy
            gzo[i] = gz
        return alpha, gxo, gyo, gzo


def _visible_gso_arc_halfwidth_deg(es_lat_deg: float) -> float:
    """Half-width of the GSO arc visible from the ES (degrees).

    ITU-R S.1503-4 §D6.4.4.4 + §D6.4.4.2 with x2=0:
    cos(ΔLong_max) = (Re/Rgeo) / cos(LatES).
    """
    cos_lat = math.cos(es_lat_deg * DEG2RAD)
    if abs(cos_lat) < 1e-12:
        return 0.0
    cos_theta_max = (RE_KM / GSO_RADIUS_KM) / cos_lat
    cos_theta_max = max(-1.0, min(1.0, cos_theta_max))
    theta_max = math.degrees(math.acos(cos_theta_max))
    return theta_max


def _wrap_deg_180(angle_deg: float) -> float:
    """Normalize angle to the range [-180, 180]."""
    while angle_deg > 180.0:
        angle_deg -= 360.0
    while angle_deg < -180.0:
        angle_deg += 360.0
    return angle_deg


def delta_longitude_s1503_deg(
    long_alpha_deg: float | np.ndarray,
    long_ngso_deg: float | np.ndarray,
) -> float | np.ndarray:
    """ITU-R S.1503 §D6.4.4: ΔLong = LongAlpha − LongNGSO (°), ramo principal (−180, 180]."""
    la = np.asarray(long_alpha_deg, dtype=np.float64)
    ln = np.asarray(long_ngso_deg, dtype=np.float64)
    d = la - ln
    out = ((d + 180.0) % 360.0) - 180.0
    out = np.where((out == -180.0) & (d > 0.0), 180.0, out)
    if out.shape == ():
        return float(out)
    return out


def _gso_alpha_at_dlon(
    d_lon_deg: float,
    es_lon_deg: float,
    es_ecef: np.ndarray,
    ux: float, uy: float, uz: float,
) -> float:
    """cos(α) between the ES→NGSO direction and ES→GSO(es_lon+d_lon_deg).

    Returns the cosine (larger = smaller angle) for use in minimization.
    """
    lon_rad = math.radians(es_lon_deg + d_lon_deg)
    gx = GSO_RADIUS_KM * math.cos(lon_rad) - es_ecef[0]
    gy = GSO_RADIUS_KM * math.sin(lon_rad) - es_ecef[1]
    gz = -es_ecef[2]
    norm = math.sqrt(gx * gx + gy * gy + gz * gz)
    if norm < 1e-6:
        return -1.0
    return (gx * ux + gy * uy + gz * uz) / norm


def compute_alpha_angle(
    es_ecef: np.ndarray,
    ngso_sat_ecef: np.ndarray,
    gso_sat_ecef: np.ndarray,
    t_s: float,
    search_range_deg: float | None = None,
    step_deg: float = 1.0,
    es_lat_deg: float | None = None,
    es_lon_deg: float | None = None,
) -> float:
    """Compute the minimum α angle between the direction of the non-GSO
    satellite and the GSO arc, seen from the earth station (degrees).

    The search is performed in two stages:
      1. Coarse sweep with step step_deg (vectorized).
      2. Ternary search in the interval [best-step, best+step] to
         find the true minimum with ~0.001° precision.

    This eliminates the systematic error of up to step_deg/2 that occurs when
    the nearest GSO point lies between two grid points — especially
    critical for small α (satellite nearly coplanar with the GSO arc).
    """
    global _WARNED_NUMBA_ALPHA_FALLBACK, _WARNED_ALPHA_EXPLICIT_RANGE_PATH

    if es_lat_deg is None or es_lon_deg is None:
        es_lat_deg, es_lon_deg, _ = ecef_to_lla(es_ecef)

    # Fast JIT path for the standard S.1503 case.
    if _NUMBA_AVAILABLE and search_range_deg is None:
        if _ALPHA_METHOD == "analytical":
            _log_numba_path_once(
                "alpha_scalar_numba_direct",
                "Numba active: geometry.compute_alpha_angle using direct Newton kernel (analytical).",
            )
            return _compute_alpha_angle_direct_numba(
                float(es_ecef[0]),
                float(es_ecef[1]),
                float(es_ecef[2]),
                float(ngso_sat_ecef[0]),
                float(ngso_sat_ecef[1]),
                float(ngso_sat_ecef[2]),
                float(es_lat_deg),
                float(es_lon_deg),
            )
        _log_numba_path_once(
            "alpha_scalar_numba",
            "Numba active: geometry.compute_alpha_angle using JIT kernel (sweep).",
        )
        return _compute_alpha_angle_numba(
            float(es_ecef[0]),
            float(es_ecef[1]),
            float(es_ecef[2]),
            float(ngso_sat_ecef[0]),
            float(ngso_sat_ecef[1]),
            float(ngso_sat_ecef[2]),
            float(es_lat_deg),
            float(es_lon_deg),
            float(step_deg),
        )
    if (
        not _NUMBA_AVAILABLE
        and not _WARNED_NUMBA_ALPHA_FALLBACK
        and _is_main_process_for_fallback_log()
    ):
        logger.warning(
            "Fallback: Numba unavailable in geometry.compute_alpha_angle; "
            "using Python/NumPy implementation."
        )
        _WARNED_NUMBA_ALPHA_FALLBACK = True
    if (
        search_range_deg is not None
        and not _WARNED_ALPHA_EXPLICIT_RANGE_PATH
        and _is_main_process_for_fallback_log()
    ):
        logger.info(
            "Alternative alpha path active: explicit search_range_deg; "
            "using Python/NumPy implementation."
        )
        _WARNED_ALPHA_EXPLICIT_RANGE_PATH = True

    if search_range_deg is None:
        search_range_deg = _visible_gso_arc_halfwidth_deg(es_lat_deg)

    # ES → NGSO direction (unit)
    d_ngso = ngso_sat_ecef - es_ecef
    n_ngso = math.sqrt(float(np.dot(d_ngso, d_ngso)))
    ux, uy, uz = d_ngso[0] / n_ngso, d_ngso[1] / n_ngso, d_ngso[2] / n_ngso

    # ── Stage 1: vectorized coarse sweep ────────────────────────────
    d_lons = np.arange(-search_range_deg, search_range_deg + step_deg, step_deg)
    lons_rad = np.radians(es_lon_deg + d_lons)

    gso_x = GSO_RADIUS_KM * np.cos(lons_rad)
    gso_y = GSO_RADIUS_KM * np.sin(lons_rad)

    dx = gso_x - es_ecef[0]
    dy = gso_y - es_ecef[1]
    dz = np.full(len(d_lons), -es_ecef[2])

    norms = np.sqrt(dx * dx + dy * dy + dz * dz)
    dots = (dx * ux + dy * uy + dz * uz) / norms
    np.clip(dots, -1.0, 1.0, out=dots)

    best_idx = int(np.argmax(dots))
    best_d_lon = float(d_lons[best_idx])

    # ── Stage 2: ternary search in the interval [best-step, best+step] ────
    lo_t = max(-search_range_deg, best_d_lon - step_deg)
    hi_t = min( search_range_deg, best_d_lon + step_deg)
    tol = 1e-4  # ~0.0001° final resolution

    for _ in range(60):            # converges in ~20 iterations
        if hi_t - lo_t < tol:
            break
        m1 = lo_t + (hi_t - lo_t) / 3.0
        m2 = hi_t - (hi_t - lo_t) / 3.0
        c1 = _gso_alpha_at_dlon(m1, es_lon_deg, es_ecef, ux, uy, uz)
        c2 = _gso_alpha_at_dlon(m2, es_lon_deg, es_ecef, ux, uy, uz)
        if c1 < c2:          # we want to maximize the cosine (= minimize the angle)
            lo_t = m1
        else:
            hi_t = m2

    best_d_lon_fine = (lo_t + hi_t) / 2.0
    cos_fine = _gso_alpha_at_dlon(best_d_lon_fine, es_lon_deg, es_ecef, ux, uy, uz)

    # Tie-break §D6.4.4.1 between the optimum and the visibility edges.
    es_lon_rad = math.radians(es_lon_deg)
    ex = float(es_ecef[0]); ey = float(es_ecef[1]); ez = float(es_ecef[2])
    lon_ngso_rad = math.atan2(float(ngso_sat_ecef[1]), float(ngso_sat_ecef[0]))
    best_lam = es_lon_rad + math.radians(best_d_lon_fine)
    best_cos = cos_fine

    edge_lo_lam = es_lon_rad - math.radians(search_range_deg)
    edge_hi_lam = es_lon_rad + math.radians(search_range_deg)
    cos_lo_e = _eval_cos_alpha_at_lam_numba(edge_lo_lam, ux, uy, uz, ex, ey, ez, GSO_RADIUS_KM)
    if _s1503_tie_break_better_numba(best_lam, best_cos, edge_lo_lam, cos_lo_e, lon_ngso_rad):
        best_lam = edge_lo_lam
        best_cos = cos_lo_e
    cos_hi_e = _eval_cos_alpha_at_lam_numba(edge_hi_lam, ux, uy, uz, ex, ey, ez, GSO_RADIUS_KM)
    if _s1503_tie_break_better_numba(best_lam, best_cos, edge_hi_lam, cos_hi_e, lon_ngso_rad):
        best_lam = edge_hi_lam
        best_cos = cos_hi_e

    best_cos = max(-1.0, min(1.0, best_cos))
    min_alpha = math.degrees(math.acos(best_cos))

    # ITU-R S.1503-4 §D6.4.4.1: sign of α via intersection of the ES→NGSO line with the XY plane.
    sign = _alpha_sign_xy_plane_numba(
        ex, ey, ez,
        float(ngso_sat_ecef[0]) - ex,
        float(ngso_sat_ecef[1]) - ey,
        float(ngso_sat_ecef[2]) - ez,
        GSO_RADIUS_KM,
    )
    return sign * min_alpha


def compute_alpha_angle_fast(
    es_ecef: np.ndarray,
    ngso_sat_ecef: np.ndarray,
    gso_sat_ecef: np.ndarray,       # Legacy — kept for compatibility
    es_lat_deg: float | None = None,
    es_lon_deg: float | None = None,
) -> float:
    """Compute the minimum alpha angle (signed) for the visible GSO arc.

    Accepts precomputed es_lat_deg/es_lon_deg to avoid a redundant
    `ecef_to_lla` when the ES is fixed throughout the simulation.
    """
    if get_gso_longitude_mode() == "es_meridian":
        if es_lat_deg is None or es_lon_deg is None:
            es_lat_deg, es_lon_deg, _ = ecef_to_lla(es_ecef)
        return compute_alpha_angle_fast_components(
            float(es_ecef[0]), float(es_ecef[1]), float(es_ecef[2]),
            float(ngso_sat_ecef[0]), float(ngso_sat_ecef[1]), float(ngso_sat_ecef[2]),
            float(es_lat_deg), float(es_lon_deg),
        )
    return compute_alpha_angle(
        es_ecef=es_ecef,
        ngso_sat_ecef=ngso_sat_ecef,
        gso_sat_ecef=gso_sat_ecef,
        t_s=0.0,
        step_deg=1.0,
        es_lat_deg=es_lat_deg,
        es_lon_deg=es_lon_deg,
    )


def _compute_alpha_and_optimal_gso_python(
    es_ecef: np.ndarray,
    ngso_sat_ecef: np.ndarray,
    es_lat_deg: float,
    es_lon_deg: float,
    step_deg: float,
) -> tuple[float, np.ndarray, float]:
    """Fallback without Numba: minimizes the angle along the visible GSO arc (S.1503).

    ITU-R S.1503-4 §D6.4.4.1: tie-break (smaller |ΔLong|, then positive) between
    the refined optimum and the visibility edges.
    """
    search_range_deg = _visible_gso_arc_halfwidth_deg(es_lat_deg)
    d_ngso = ngso_sat_ecef - es_ecef
    n_ngso = math.sqrt(float(np.dot(d_ngso, d_ngso)))
    if n_ngso < 1e-12:
        lon0 = math.radians(es_lon_deg)
        best_gso = np.array([
            GSO_RADIUS_KM * math.cos(lon0),
            GSO_RADIUS_KM * math.sin(lon0),
            0.0,
        ], dtype=np.float64)
        return 0.0, best_gso, float(es_lon_deg)
    ux, uy, uz = d_ngso[0] / n_ngso, d_ngso[1] / n_ngso, d_ngso[2] / n_ngso

    d_lons = np.arange(-search_range_deg, search_range_deg + step_deg, step_deg)
    lons_rad = np.radians(es_lon_deg + d_lons)
    gso_x = GSO_RADIUS_KM * np.cos(lons_rad)
    gso_y = GSO_RADIUS_KM * np.sin(lons_rad)
    dx = gso_x - es_ecef[0]
    dy = gso_y - es_ecef[1]
    dz = np.full(len(d_lons), -es_ecef[2])
    norms = np.sqrt(dx * dx + dy * dy + dz * dz)
    dots = (dx * ux + dy * uy + dz * uz) / norms
    np.clip(dots, -1.0, 1.0, out=dots)
    best_idx = int(np.argmax(dots))
    best_d_lon = float(d_lons[best_idx])

    lo_t = max(-search_range_deg, best_d_lon - step_deg)
    hi_t = min(search_range_deg, best_d_lon + step_deg)
    tol = 1e-4
    for _ in range(60):
        if hi_t - lo_t < tol:
            break
        m1 = lo_t + (hi_t - lo_t) / 3.0
        m2 = hi_t - (hi_t - lo_t) / 3.0
        c1 = _gso_alpha_at_dlon(m1, es_lon_deg, es_ecef, ux, uy, uz)
        c2 = _gso_alpha_at_dlon(m2, es_lon_deg, es_ecef, ux, uy, uz)
        if c1 < c2:
            lo_t = m1
        else:
            hi_t = m2

    best_d_lon_fine = (lo_t + hi_t) / 2.0
    cos_fine = _gso_alpha_at_dlon(best_d_lon_fine, es_lon_deg, es_ecef, ux, uy, uz)

    # Tie-break §D6.4.4.1 between the optimum and the visibility edges.
    es_lon_rad = math.radians(es_lon_deg)
    ex = float(es_ecef[0]); ey = float(es_ecef[1]); ez = float(es_ecef[2])
    lon_ngso_rad = math.atan2(float(ngso_sat_ecef[1]), float(ngso_sat_ecef[0]))
    best_lam = es_lon_rad + math.radians(best_d_lon_fine)
    best_cos = cos_fine

    edge_lo_lam = es_lon_rad - math.radians(search_range_deg)
    edge_hi_lam = es_lon_rad + math.radians(search_range_deg)
    cos_lo_e = _eval_cos_alpha_at_lam_numba(edge_lo_lam, ux, uy, uz, ex, ey, ez, GSO_RADIUS_KM)
    if _s1503_tie_break_better_numba(best_lam, best_cos, edge_lo_lam, cos_lo_e, lon_ngso_rad):
        best_lam = edge_lo_lam
        best_cos = cos_lo_e
    cos_hi_e = _eval_cos_alpha_at_lam_numba(edge_hi_lam, ux, uy, uz, ex, ey, ez, GSO_RADIUS_KM)
    if _s1503_tie_break_better_numba(best_lam, best_cos, edge_hi_lam, cos_hi_e, lon_ngso_rad):
        best_lam = edge_hi_lam
        best_cos = cos_hi_e

    best_cos = max(-1.0, min(1.0, best_cos))
    min_alpha = math.degrees(math.acos(best_cos))

    best_gso = np.array([
        GSO_RADIUS_KM * math.cos(best_lam),
        GSO_RADIUS_KM * math.sin(best_lam),
        0.0,
    ], dtype=np.float64)
    # ITU-R S.1503-4 §D6.4.4.1: sign of α via intersection of the ES→NGSO line with the XY plane.
    sign = _alpha_sign_xy_plane_numba(
        ex, ey, ez,
        float(ngso_sat_ecef[0]) - ex,
        float(ngso_sat_ecef[1]) - ey,
        float(ngso_sat_ecef[2]) - ez,
        GSO_RADIUS_KM,
    )
    lon_deg = math.degrees(math.atan2(float(best_gso[1]), float(best_gso[0])))
    return sign * min_alpha, best_gso, float(lon_deg)


def _compute_alpha_es_meridian_gso(
    es_ecef: np.ndarray,
    ngso_sat_ecef: np.ndarray,
    es_lat_deg: float,
    es_lon_deg: float,
) -> tuple[float, np.ndarray, float]:
    """α with GSO reference at the ES longitude (no optimization along the arc)."""
    gso_sat = gso_position_ecef(es_lon_deg, 0.0)
    d_ngso = ngso_sat_ecef - es_ecef
    d_gso = gso_sat - es_ecef
    n_n = float(np.linalg.norm(d_ngso))
    n_g = float(np.linalg.norm(d_gso))
    if n_n < 1e-9 or n_g < 1e-9:
        return 0.0, gso_sat, float(es_lon_deg)
    ux, uy, uz = d_ngso[0] / n_n, d_ngso[1] / n_n, d_ngso[2] / n_n
    vx, vy, vz = d_gso[0] / n_g, d_gso[1] / n_g, d_gso[2] / n_g
    c = float(ux * vx + uy * vy + uz * vz)
    c = max(-1.0, min(1.0, c))
    min_alpha = math.degrees(math.acos(c))
    az_ngso, _, _ = topocentric_angles(es_ecef, ngso_sat_ecef, es_lat_deg, es_lon_deg)
    az_gso, _, _ = topocentric_angles(es_ecef, gso_sat, es_lat_deg, es_lon_deg)
    # ITU-R S.1503-4 §D6.4.4.1: sign of α via intersection of the ES→NGSO line with the XY plane.
    sign = _alpha_sign_xy_plane_numba(
        float(es_ecef[0]), float(es_ecef[1]), float(es_ecef[2]),
        float(ngso_sat_ecef[0]) - float(es_ecef[0]),
        float(ngso_sat_ecef[1]) - float(es_ecef[1]),
        float(ngso_sat_ecef[2]) - float(es_ecef[2]),
        GSO_RADIUS_KM,
    )
    return sign * min_alpha, gso_sat, float(es_lon_deg)


def compute_alpha_and_optimal_gso(
    es_ecef: np.ndarray,
    ngso_sat_ecef: np.ndarray,
    es_lat_deg: float | None = None,
    es_lon_deg: float | None = None,
    step_deg: float = 1.0,
) -> tuple[float, np.ndarray, float]:
    """α (signed) and reference GSO position for ES→GSO vs ES→NGSO.

    Respects `set_gso_longitude_mode`:
      - ``arc_optimal``: minimizes the angle along the visible GSO arc
        (`set_alpha_method`: analytical or sweep).
      - ``es_meridian``: GSO at the ES longitude (legacy).

    Returns:
        (alpha_deg, gso_ecef_km, gso_lon_deg).
    """
    if es_lat_deg is None or es_lon_deg is None:
        es_lat_deg, es_lon_deg, _ = ecef_to_lla(es_ecef)
    if get_gso_longitude_mode() == "es_meridian":
        return _compute_alpha_es_meridian_gso(
            es_ecef, ngso_sat_ecef, float(es_lat_deg), float(es_lon_deg),
        )
    ex = float(es_ecef[0])
    ey = float(es_ecef[1])
    ez = float(es_ecef[2])
    nx = float(ngso_sat_ecef[0])
    ny = float(ngso_sat_ecef[1])
    nz = float(ngso_sat_ecef[2])
    if _NUMBA_AVAILABLE:
        if _ALPHA_METHOD == "analytical":
            a, gx, gy, gz = _compute_alpha_angle_direct_numba_with_gso(
                ex, ey, ez, nx, ny, nz, float(es_lat_deg), float(es_lon_deg),
            )
        else:
            a, gx, gy, gz = _compute_alpha_angle_numba_with_gso(
                ex, ey, ez, nx, ny, nz, float(es_lat_deg), float(es_lon_deg), float(step_deg),
            )
        gso = np.array([gx, gy, gz], dtype=np.float64)
        lon_deg = math.degrees(math.atan2(gy, gx))
        return float(a), gso, float(lon_deg)
    return _compute_alpha_and_optimal_gso_python(
        es_ecef, ngso_sat_ecef, float(es_lat_deg), float(es_lon_deg), float(step_deg),
    )


def compute_alpha_and_optimal_gso_multi_es_batch(
    es_positions: np.ndarray,
    ngso_position: np.ndarray,
    es_lat_deg: np.ndarray,
    es_lon_deg: np.ndarray,
    step_deg: float = 1.0,
) -> tuple[np.ndarray, np.ndarray]:
    """For N stations and one NGSO: α per row and an (N,3) matrix of optimal GSO in ECEF (km)."""
    es_positions = np.asarray(es_positions, dtype=np.float64)
    ngso_position = np.asarray(ngso_position, dtype=np.float64).reshape(3)
    es_lat_deg = np.asarray(es_lat_deg, dtype=np.float64).ravel()
    es_lon_deg = np.asarray(es_lon_deg, dtype=np.float64).ravel()
    n = es_lat_deg.shape[0]
    if es_positions.ndim == 1:
        es_positions = es_positions.reshape(1, -1)
    if n == 0:
        return np.array([], dtype=np.float64), np.empty((0, 3), dtype=np.float64)
    if get_gso_longitude_mode() == "es_meridian":
        alpha_out = np.empty(n, dtype=np.float64)
        gso_out = np.empty((n, 3), dtype=np.float64)
        for i in range(n):
            a, g, _ = _compute_alpha_es_meridian_gso(
                es_positions[i],
                ngso_position,
                float(es_lat_deg[i]),
                float(es_lon_deg[i]),
            )
            alpha_out[i] = a
            gso_out[i, :] = g
        return alpha_out, gso_out
    if _NUMBA_AVAILABLE:
        if _ALPHA_METHOD == "analytical":
            alpha, gxo, gyo, gzo = _compute_alpha_and_optimal_gso_multi_es_direct_numba(
                es_positions[:, 0], es_positions[:, 1], es_positions[:, 2],
                float(ngso_position[0]), float(ngso_position[1]), float(ngso_position[2]),
                es_lat_deg, es_lon_deg,
            )
        else:
            alpha, gxo, gyo, gzo = _compute_alpha_and_optimal_gso_multi_es_sweep_numba(
                es_positions[:, 0], es_positions[:, 1], es_positions[:, 2],
                float(ngso_position[0]), float(ngso_position[1]), float(ngso_position[2]),
                es_lat_deg, es_lon_deg,
                float(step_deg),
            )
        gso = np.column_stack((gxo, gyo, gzo))
        return alpha, gso
    alpha_out = np.empty(n, dtype=np.float64)
    gso_out = np.empty((n, 3), dtype=np.float64)
    for i in range(n):
        ai, gi, _ = _compute_alpha_and_optimal_gso_python(
            es_positions[i],
            ngso_position,
            float(es_lat_deg[i]),
            float(es_lon_deg[i]),
            float(step_deg),
        )
        alpha_out[i] = ai
        gso_out[i, :] = gi
    return alpha_out, gso_out


def compute_alpha_and_optimal_gso_fixed_es_batch(
    es_ecef: np.ndarray,
    ngso_positions: np.ndarray,
    es_lat_deg: float,
    es_lon_deg: float,
    step_deg: float = 1.0,
) -> tuple[np.ndarray, np.ndarray]:
    """For a fixed ES and N NGSOs: α per satellite and optimal GSO in ECEF (km)."""
    es_ecef = np.asarray(es_ecef, dtype=np.float64).reshape(3)
    ngso_positions = np.asarray(ngso_positions, dtype=np.float64)
    if ngso_positions.ndim == 1:
        ngso_positions = ngso_positions.reshape(1, 3)
    n = ngso_positions.shape[0]
    if n == 0:
        return np.array([], dtype=np.float64), np.empty((0, 3), dtype=np.float64)

    if get_gso_longitude_mode() == "es_meridian":
        alpha_out = np.empty(n, dtype=np.float64)
        gso_out = np.empty((n, 3), dtype=np.float64)
        for i in range(n):
            a, g, _ = _compute_alpha_es_meridian_gso(
                es_ecef,
                ngso_positions[i],
                float(es_lat_deg),
                float(es_lon_deg),
            )
            alpha_out[i] = a
            gso_out[i, :] = g
        return alpha_out, gso_out

    if _NUMBA_AVAILABLE:
        if _ALPHA_METHOD == "analytical":
            alpha, gxo, gyo, gzo = _compute_alpha_and_optimal_gso_fixed_es_direct_numba(
                float(es_ecef[0]), float(es_ecef[1]), float(es_ecef[2]),
                ngso_positions[:, 0], ngso_positions[:, 1], ngso_positions[:, 2],
                float(es_lat_deg), float(es_lon_deg),
            )
        else:
            alpha, gxo, gyo, gzo = _compute_alpha_and_optimal_gso_fixed_es_sweep_numba(
                float(es_ecef[0]), float(es_ecef[1]), float(es_ecef[2]),
                ngso_positions[:, 0], ngso_positions[:, 1], ngso_positions[:, 2],
                float(es_lat_deg), float(es_lon_deg),
                float(step_deg),
            )
        return alpha, np.column_stack((gxo, gyo, gzo))

    alpha_out = np.empty(n, dtype=np.float64)
    gso_out = np.empty((n, 3), dtype=np.float64)
    for i in range(n):
        ai, gi, _ = _compute_alpha_and_optimal_gso_python(
            es_ecef,
            ngso_positions[i],
            float(es_lat_deg),
            float(es_lon_deg),
            float(step_deg),
        )
        alpha_out[i] = ai
        gso_out[i, :] = gi
    return alpha_out, gso_out


def compute_alpha_angle_fast_components(
    es_x: float,
    es_y: float,
    es_z: float,
    ng_x: float,
    ng_y: float,
    ng_z: float,
    es_lat_deg: float,
    es_lon_deg: float,
) -> float:
    """Scalar version of compute_alpha_angle_fast for critical loops."""
    if get_gso_longitude_mode() == "es_meridian":
        if _NUMBA_AVAILABLE:
            _log_numba_path_once(
                "alpha_scalar_es_meridian_numba",
                "Numba active: scalar alpha using legacy mode (es_meridian).",
            )
            return _compute_alpha_angle_es_meridian_numba(
                float(es_x), float(es_y), float(es_z),
                float(ng_x), float(ng_y), float(ng_z),
                float(es_lat_deg), float(es_lon_deg),
            )
        _log_numba_path_once(
            "alpha_scalar_es_meridian_python",
            "Fallback: scalar alpha in legacy mode (es_meridian) on the Python path.",
            level="warning",
        )
        es_ecef = np.array([es_x, es_y, es_z], dtype=np.float64)
        ngso_sat_ecef = np.array([ng_x, ng_y, ng_z], dtype=np.float64)
        alpha, _, _ = _compute_alpha_es_meridian_gso(
            es_ecef=es_ecef,
            ngso_sat_ecef=ngso_sat_ecef,
            es_lat_deg=float(es_lat_deg),
            es_lon_deg=float(es_lon_deg),
        )
        return float(alpha)

    if _NUMBA_AVAILABLE:
        if _ALPHA_METHOD == "analytical":
            return _compute_alpha_angle_direct_numba(
                float(es_x), float(es_y), float(es_z),
                float(ng_x), float(ng_y), float(ng_z),
                float(es_lat_deg), float(es_lon_deg),
            )
        return _compute_alpha_angle_numba(
            float(es_x), float(es_y), float(es_z),
            float(ng_x), float(ng_y), float(ng_z),
            float(es_lat_deg), float(es_lon_deg),
            1.0,
        )

    es_ecef = np.array([es_x, es_y, es_z], dtype=float)
    ngso_sat_ecef = np.array([ng_x, ng_y, ng_z], dtype=float)
    return compute_alpha_angle(
        es_ecef=es_ecef,
        ngso_sat_ecef=ngso_sat_ecef,
        gso_sat_ecef=np.zeros(3, dtype=float),
        t_s=0.0,
        step_deg=1.0,
        es_lat_deg=es_lat_deg,
        es_lon_deg=es_lon_deg,
    )


# =====================================================================
#  Off-axis angle (φ) at the GSO ES antenna
# =====================================================================

def compute_offaxis_angle_batch(
    es_ecef: np.ndarray,
    ngso_positions: np.ndarray,
    gso_sat_ecef: np.ndarray,
) -> np.ndarray:
    """φ (degrees) for N satellites: ``ngso_positions`` shape (N, 3)."""
    ngso_positions = np.asarray(ngso_positions, dtype=np.float64)
    if ngso_positions.size == 0:
        return np.array([], dtype=np.float64)
    if ngso_positions.ndim == 1:
        ngso_positions = ngso_positions.reshape(1, -1)
    d_g = gso_sat_ecef.astype(np.float64) - es_ecef.astype(np.float64)
    n_g = float(np.linalg.norm(d_g))
    if n_g < 1e-15:
        return np.zeros(ngso_positions.shape[0], dtype=np.float64)
    u_g = d_g / n_g
    diff = ngso_positions - es_ecef.reshape(1, 3)
    norms = np.linalg.norm(diff, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-15)
    u_n = diff / norms
    cosv = np.clip((u_n @ u_g).ravel(), -1.0, 1.0)
    return np.degrees(np.arccos(cosv))


def compute_offaxis_and_planar_angle(
    es_ecef: np.ndarray,
    ngso_sat_ecef: np.ndarray,
    gso_sat_ecef: np.ndarray,
    es_lat_deg: float,
    es_lon_deg: float,
) -> tuple[float, float]:
    """Compute (phi, theta) per Annex 2 of Rec. ITU-R BO.1443-3."""
    phi_arr, theta_arr = compute_offaxis_and_planar_angle_batch(
        es_ecef.reshape(1, 3),
        np.asarray(ngso_sat_ecef, dtype=np.float64).reshape(1, 3),
        np.asarray(gso_sat_ecef, dtype=np.float64).reshape(1, 3),
        np.array([es_lat_deg], dtype=np.float64),
        np.array([es_lon_deg], dtype=np.float64),
    )
    return float(phi_arr[0]), float(theta_arr[0])


def compute_offaxis_and_planar_angle_batch(
    es_ecef_all: np.ndarray,
    ngso_positions: np.ndarray,
    gso_positions: np.ndarray,
    es_lat_deg_all: np.ndarray,
    es_lon_deg_all: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute (phi, theta) in batch per Annex 2 of Rec. ITU-R BO.1443-3."""
    es_ecef_all = np.asarray(es_ecef_all, dtype=np.float64)
    ngso_positions = np.asarray(ngso_positions, dtype=np.float64)
    gso_positions = np.asarray(gso_positions, dtype=np.float64)
    es_lat_deg_all = np.asarray(es_lat_deg_all, dtype=np.float64)
    es_lon_deg_all = np.asarray(es_lon_deg_all, dtype=np.float64)

    if es_ecef_all.ndim == 1:
        es_ecef_all = es_ecef_all.reshape(1, -1)
    if ngso_positions.ndim == 1:
        ngso_positions = ngso_positions.reshape(1, -1)
    if gso_positions.ndim == 1:
        gso_positions = gso_positions.reshape(1, -1)

    n = es_ecef_all.shape[0]
    if n == 0:
        return np.array([], dtype=np.float64), np.array([], dtype=np.float64)

    lat_rad = np.radians(es_lat_deg_all)
    lon_rad = np.radians(es_lon_deg_all)
    sin_lat = np.sin(lat_rad)
    cos_lat = np.cos(lat_rad)
    sin_lon = np.sin(lon_rad)
    cos_lon = np.cos(lon_rad)

    if ngso_positions.shape[0] == 1 and n > 1:
        diff_ngso = ngso_positions[0] - es_ecef_all
    else:
        diff_ngso = ngso_positions - es_ecef_all
    if gso_positions.shape[0] == 1 and n > 1:
        diff_gso = gso_positions[0] - es_ecef_all
    else:
        diff_gso = gso_positions - es_ecef_all

    def _enu_components(diff: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        dx = diff[:, 0]
        dy = diff[:, 1]
        dz = diff[:, 2]
        east = -sin_lon * dx + cos_lon * dy
        north = -sin_lat * cos_lon * dx - sin_lat * sin_lon * dy + cos_lat * dz
        up = cos_lat * cos_lon * dx + cos_lat * sin_lon * dy + sin_lat * dz
        return east, north, up

    e_ng, n_ng, u_ng = _enu_components(diff_ngso)
    e_g, n_g, u_g = _enu_components(diff_gso)

    rng_ng = np.linalg.norm(diff_ngso, axis=1)
    rng_g = np.linalg.norm(diff_gso, axis=1)
    rng_ng = np.maximum(rng_ng, 1e-15)
    rng_g = np.maximum(rng_g, 1e-15)

    el_ng = np.degrees(np.arcsin(np.clip(u_ng / rng_ng, -1.0, 1.0)))
    el_g = np.degrees(np.arcsin(np.clip(u_g / rng_g, -1.0, 1.0)))
    az_ng = np.degrees(np.arctan2(e_ng, n_ng))
    az_g = np.degrees(np.arctan2(e_g, n_g))

    delta_az = ((az_ng - az_g + 180.0) % 360.0) - 180.0
    same_az = np.abs(delta_az) < 1e-12

    a = np.radians(90.0 - el_g)
    b = np.radians(90.0 - el_ng)
    ccos = np.cos(a) * np.cos(b) + np.sin(a) * np.sin(b) * np.cos(np.radians(delta_az))
    phi = np.degrees(np.arccos(np.clip(ccos, -1.0, 1.0)))
    phi[same_az] = np.abs(el_g[same_az] - el_ng[same_az])

    theta = np.empty(n, dtype=np.float64)
    theta[same_az] = np.where(el_g[same_az] > el_ng[same_az], 270.0, 90.0)

    valid = ~same_az
    if np.any(valid):
        phi_rad = np.radians(phi[valid])
        a_rad = a[valid]
        b_rad = b[valid]
        denom = np.sin(phi_rad) * np.sin(a_rad)
        denom = np.where(np.abs(denom) < 1e-15, 1e-15, denom)
        cos_b_ang = (np.cos(b_rad) - np.cos(phi_rad) * np.cos(a_rad)) / denom
        b_ang = np.degrees(np.arccos(np.clip(cos_b_ang, -1.0, 1.0)))
        d_az_v = delta_az[valid]

        theta_v = np.where(
            d_az_v < 0.0,
            90.0 + b_ang,
            np.where(b_ang < 90.0, 90.0 - b_ang, 450.0 - b_ang),
        )
        theta[valid] = theta_v % 360.0

    return phi, theta


def compute_alpha_angle_fast_multi_es_batch(
    es_positions: np.ndarray,
    ngso_position: np.ndarray,
    es_lat_deg: np.ndarray,
    es_lon_deg: np.ndarray,
    step_deg: float = 1.0,
) -> np.ndarray:
    """α (degrees) for N ES and a single NGSO ECEF; uses parallel Numba when available."""
    es_positions = np.asarray(es_positions, dtype=np.float64)
    ngso_position = np.asarray(ngso_position, dtype=np.float64).reshape(-1)
    es_lat_deg = np.asarray(es_lat_deg, dtype=np.float64).ravel()
    es_lon_deg = np.asarray(es_lon_deg, dtype=np.float64).ravel()
    if es_positions.size == 0:
        return np.array([], dtype=np.float64)
    if es_positions.ndim == 1:
        es_positions = es_positions.reshape(1, -1)
    n = es_positions.shape[0]
    if get_gso_longitude_mode() == "es_meridian":
        if _NUMBA_AVAILABLE:
            _log_numba_path_once(
                "alpha_batch_multi_es_es_meridian_numba",
                "Numba active: alpha_batch (multi ES) using legacy mode (es_meridian).",
            )
            return _compute_alpha_angle_multi_es_batch_es_meridian_numba(
                es_positions[:, 0], es_positions[:, 1], es_positions[:, 2],
                float(ngso_position[0]), float(ngso_position[1]), float(ngso_position[2]),
                es_lat_deg, es_lon_deg,
            )
        _log_numba_path_once(
            "alpha_batch_multi_es_es_meridian_python",
            "Fallback: alpha_batch (multi ES) in legacy mode (es_meridian) on the Python path.",
            level="warning",
        )
        out = np.empty(n, dtype=np.float64)
        ngso_pos = np.asarray(ngso_position, dtype=np.float64).reshape(3)
        for i in range(n):
            ai, _, _ = _compute_alpha_es_meridian_gso(
                es_positions[i],
                ngso_pos,
                float(es_lat_deg[i]),
                float(es_lon_deg[i]),
            )
            out[i] = ai
        return out

    if _NUMBA_AVAILABLE:
        if _ALPHA_METHOD == "analytical":
            _log_numba_path_once(
                "alpha_batch_multi_es_numba_direct",
                "Numba active: alpha_batch (multi ES) using direct Newton kernel (analytical).",
            )
            return _compute_alpha_angle_multi_es_batch_direct_numba(
                es_positions[:, 0], es_positions[:, 1], es_positions[:, 2],
                float(ngso_position[0]), float(ngso_position[1]), float(ngso_position[2]),
                es_lat_deg, es_lon_deg,
            )
        _log_numba_path_once(
            "alpha_batch_multi_es_numba",
            "Numba active: alpha_batch (multi ES) using parallel JIT kernel (sweep).",
        )
        return _compute_alpha_angle_multi_es_batch_numba(
            es_positions[:, 0], es_positions[:, 1], es_positions[:, 2],
            float(ngso_position[0]), float(ngso_position[1]), float(ngso_position[2]),
            es_lat_deg, es_lon_deg, float(step_deg),
        )
    _log_numba_path_once(
        "alpha_batch_multi_es_python",
        "Fallback: alpha_batch (multi ES) using Python/NumPy path (Numba unavailable).",
        level="warning",
    )
    out = np.empty(n, dtype=np.float64)
    ng_x = float(ngso_position[0])
    ng_y = float(ngso_position[1])
    ng_z = float(ngso_position[2])
    for i in range(n):
        out[i] = compute_alpha_angle_fast_components(
            float(es_positions[i, 0]),
            float(es_positions[i, 1]),
            float(es_positions[i, 2]),
            ng_x, ng_y, ng_z,
            float(es_lat_deg[i]),
            float(es_lon_deg[i]),
        )
    return out


def warmup_geometry_kernels() -> None:
    """Pre-compile the most expensive Numba kernels used in the S.1503 path."""
    if not _NUMBA_AVAILABLE:
        return

    es = np.array([RE_KM, 0.0, 0.0], dtype=np.float64)
    ngso = np.array([RE_KM + 1200.0, 80.0, 40.0], dtype=np.float64)
    es_positions = np.array(
        [
            es,
            np.array([RE_KM - 1.5, 2.0, 1.0], dtype=np.float64),
        ],
        dtype=np.float64,
    )
    es_lat = np.array([0.0, 0.02], dtype=np.float64)
    es_lon = np.array([0.0, 0.02], dtype=np.float64)

    saved = _ALPHA_METHOD
    for method in ("sweep", "analytical"):
        set_alpha_method(method)
        compute_alpha_angle_fast(
            es_ecef=es,
            ngso_sat_ecef=ngso,
            gso_sat_ecef=np.zeros(3, dtype=np.float64),
            es_lat_deg=0.0,
            es_lon_deg=0.0,
        )
        compute_alpha_angle_fast_multi_es_batch(
            es_positions=es_positions,
            ngso_position=ngso,
            es_lat_deg=es_lat,
            es_lon_deg=es_lon,
        )
    set_alpha_method(saved)


def compute_offaxis_angle(
    es_ecef: np.ndarray,
    ngso_sat_ecef: np.ndarray,
    gso_sat_ecef: np.ndarray,
) -> float:
    """Compute the off-axis angle (φ) at the GSO ES antenna.

    It is the angle between the ES boresight direction (→ GSO sat) and
    the direction of the interfering (non-GSO) satellite.

    Returns φ in degrees.
    """
    dir_gso = unit_vector(gso_sat_ecef - es_ecef)
    dir_ngso = unit_vector(ngso_sat_ecef - es_ecef)
    return math.degrees(angle_between(dir_gso, dir_ngso))


# =====================================================================
#  Elevation of the non-GSO satellite seen from the ES
# =====================================================================

def compute_elevation(
    es_ecef: np.ndarray,
    ngso_sat_ecef: np.ndarray,
    es_lat_deg: float,
    es_lon_deg: float,
) -> float:
    """Compute the elevation (degrees) of the non-GSO satellite seen from the ES."""
    _, elev, _ = topocentric_angles(es_ecef, ngso_sat_ecef, es_lat_deg, es_lon_deg)
    return elev


# =====================================================================
#  Geometry (θ, φ) of the non-GSO satellite → position of the ES and GSO sat
# =====================================================================

def theta_phi_to_es_and_gso(
    ngso_sat_eci: np.ndarray,
    theta_rad: float,
    phi_rad: float,
    t_s: float,
) -> tuple[np.ndarray, np.ndarray, float, float]:
    """Given (θ, φ) seen from the non-GSO satellite, compute the corresponding
    positions of the ES and the GSO satellite on the arc.

    Parameters
    ----------
    ngso_sat_eci : ECI position of the non-GSO satellite (km)
    theta_rad : azimuth seen from the non-GSO (rad)
    phi_rad : nadir-target angle seen from the non-GSO (rad)
    t_s : simulation time (s)

    Returns
    -------
    es_ecef : ECEF position of the ES on the surface
    gso_sat_ecef : ECEF position of the GSO satellite
    es_lat_deg : ES latitude
    es_lon_deg : ES longitude

    Procedure (simplified per S.1503-4 D.3.1.2):
      1. The φ angle defines the satellite nadir angle → determines the latitude
         of the observation point on the surface
      2. The θ angle defines the azimuthal orientation
      3. It is projected onto the Earth's surface to find the ES
      4. The GSO satellite is at the ES longitude (worst-case simplification)
    """
    ngso_ecef = eci_to_ecef(ngso_sat_eci, t_s)
    ngso_r = np.linalg.norm(ngso_ecef)

    # Direction of the sub-satellite point (nadir)
    nadir_dir = -unit_vector(ngso_ecef)

    # Local coordinate system at the satellite:
    #   z_local = nadir (toward the Earth's center)
    #   x_local = projection of "north" onto the plane perpendicular to the nadir
    #   y_local = completes the right-handed triad

    # Reference "north" vector
    north = np.array([0.0, 0.0, 1.0])
    # Component of north perpendicular to the nadir
    north_perp = north - np.dot(north, nadir_dir) * nadir_dir
    n_north = np.linalg.norm(north_perp)
    if n_north < 1e-10:
        # Satellite over the pole — uses the X axis as reference
        north_perp = np.array([1.0, 0.0, 0.0])
        north_perp = north_perp - np.dot(north_perp, nadir_dir) * nadir_dir
    north_perp = unit_vector(north_perp)

    east_dir = np.cross(nadir_dir, north_perp)
    east_dir = unit_vector(east_dir)

    # Target direction seen from the satellite (angles θ, φ from the nadir)
    sin_phi = math.sin(phi_rad)
    cos_phi = math.cos(phi_rad)
    sin_theta = math.sin(theta_rad)
    cos_theta = math.cos(theta_rad)

    look_dir = (cos_phi * nadir_dir +
                sin_phi * (cos_theta * north_perp + sin_theta * east_dir))
    look_dir = unit_vector(look_dir)

    # Find the intersection with the Earth's surface (sphere of radius RE)
    # Solves ||ngso_ecef + t * look_dir||² = RE²
    A = np.dot(look_dir, look_dir)
    B = 2.0 * np.dot(ngso_ecef, look_dir)
    C = np.dot(ngso_ecef, ngso_ecef) - RE_KM**2

    discriminant = B**2 - 4.0 * A * C
    if discriminant < 0:
        # Does not intersect the surface — point outside the field of view
        # Returns invalid values
        dummy = np.array([0.0, 0.0, 0.0])
        return dummy, dummy, 0.0, 0.0

    sqrt_disc = math.sqrt(discriminant)
    t1 = (-B - sqrt_disc) / (2.0 * A)
    t2 = (-B + sqrt_disc) / (2.0 * A)

    # The nearest intersection with t > 0
    t_hit = min(t1, t2) if t1 > 0 and t2 > 0 else max(t1, t2)
    if t_hit <= 0:
        dummy = np.array([0.0, 0.0, 0.0])
        return dummy, dummy, 0.0, 0.0

    es_ecef = ngso_ecef + t_hit * look_dir
    es_lat, es_lon, _ = ecef_to_lla(es_ecef)

    # GSO satellite: at the same longitude as the ES (worst case for downlink)
    gso_ecef = gso_position_ecef(es_lon, t_s)

    return es_ecef, gso_ecef, es_lat, es_lon


# =====================================================================
#  Angular velocity of the non-GSO seen from the ES
# =====================================================================

def compute_angular_velocity(
    es_ecef: np.ndarray,
    ngso_pos_ecef: np.ndarray,
    ngso_vel_ecef: np.ndarray,
) -> float:
    """Compute the angular velocity (°/s) of the non-GSO satellite seen from the ES.

    Implements the vector expression of S.1503 D.3.1.3.4:

      r = r_sat - r_es
      v = v_sat - v_es
      ω = |v| sin(ψ) / |r|

    In ECEF the ES is fixed, so ``v_es = 0`` and ``v`` coincides with the
    satellite's ECEF velocity.
    """
    los = np.asarray(ngso_pos_ecef, dtype=np.float64) - np.asarray(es_ecef, dtype=np.float64)
    los_norm = float(np.linalg.norm(los))
    if los_norm < 1e-12:
        return 0.0

    vel = np.asarray(ngso_vel_ecef, dtype=np.float64)
    vel_norm = float(np.linalg.norm(vel))
    if vel_norm < 1e-12:
        return 0.0

    cos_psi = float(np.dot(los, vel)) / (los_norm * vel_norm)
    cos_psi = max(-1.0, min(1.0, cos_psi))
    sin_psi = math.sqrt(max(0.0, 1.0 - cos_psi * cos_psi))
    ang_vel_rad_s = (vel_norm * sin_psi) / los_norm
    return math.degrees(ang_vel_rad_s)


# =====================================================================
#  Distance between satellite and ES
# =====================================================================

def compute_slant_range(
    es_ecef: np.ndarray, sat_ecef: np.ndarray
) -> float:
    """Slant range ES ↔ satellite (km)."""
    return float(np.linalg.norm(sat_ecef - es_ecef))
