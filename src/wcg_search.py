"""
wcg_search.py — Worst-Case Geometry (WCG) search for EPFD↓.

Implements the (θ, φ) search algorithm per ITU-R S.1503-4,
Part D, Section D.3.1.2.

The WCG is defined as the geometry (GSO ES position + reference GSO
satellite) that produces the highest instantaneous EPFD↓.

Selection criteria (D.3.1.2):
  1. Highest instantaneous EPFD↓
  2. In case of a tie: highest percentage of time at that EPFD
  3. In case of a tie: lowest angular velocity of the NGSO as seen from the ES
"""

from __future__ import annotations
import math
import logging
import os
import time
import numpy as np
from dataclasses import dataclass, field
from typing import Callable
try:
    from numba import njit
except Exception:
    def njit(*args, **kwargs):
        def _decorator(fn):
            return fn
        return _decorator

from .constants import DEG2RAD, RE_KM, GSO_RADIUS_KM

# Sub-microdegree tolerance for boundary checks (ε ≥ ε₀, εGSO ≥ εGSO_min,
# |lat| ≤ 81.2°). Without this slack, ULP-level jitter between numerical paths
# (numpy/einsum batch vs scalar/hypot) decides inconsistently
# at points exactly on the boundary (e.g. φ=φ₀ produces elev on the order of
# min_elev_deg ± 1e-13°), yielding different WCG winners between code paths.
# 1e-9° ≈ 0.1 mm on the Earth's surface — negligible vs the S.1503 0.1 dB.
WCG_ELEV_BOUNDARY_TOL_DEG = 1e-9
WCG_LAT_BOUNDARY_TOL_DEG = 1e-9
from .coordinates import (
    eci_to_ecef, ecef_to_lla, ecef_to_lla_batch, lla_to_ecef,
    sub_satellite_point,
    eci_vel_to_ecef,
    get_earth_rotation_initial_deg, set_earth_rotation_initial_deg,
)
from .geometry import (
    theta_phi_to_es_and_gso,
    compute_alpha_and_optimal_gso,
    compute_alpha_and_optimal_gso_multi_es_batch,
    compute_alpha_angle_fast_components,
    delta_longitude_s1503_deg,
    compute_offaxis_angle,
    compute_offaxis_and_planar_angle,
    compute_offaxis_and_planar_angle_batch,
    compute_elevation,
    compute_angular_velocity,
    alpha_batch_uses_numba_parallel,
    get_numba_num_threads,
    set_numba_num_threads,
    get_gso_longitude_mode,
    set_gso_longitude_mode,
    get_alpha_method,
    set_alpha_method,
)
from .orbit_propagator import OrbitalElements
from .pfd_mask import PFDMask
from .antenna import (
    EarthStationAntenna,
    ITURS1428Antenna,
    s1503_or_condition_include,
    s1503_or_criteria_log,
)

logger = logging.getLogger(__name__)
_LOGGED_WCGA_PARALLEL_POLICY = False


@dataclass
class WCGSearchPoint:
    """A single point evaluated during the WCG search."""
    theta_deg: float
    phi_deg: float
    es_lat_deg: float
    es_lon_deg: float
    alpha_deg: float
    elevation_deg: float
    epfd_dBW: float
    status: str  # "ok", "low_elev", "exclusion", "invalid"
    search_lat_deg: float | None = None
    # GSO longitude on the visible arc that minimizes |α| (None = not computed / ES meridian)
    gso_lon_deg: float | None = None


@dataclass
class WCGResult:
    """WCG search result."""
    theta_deg: float            # worst-case θ (°)
    phi_deg: float              # worst-case φ (°)
    es_lat_deg: float           # final ES latitude (°)
    es_lon_deg: float           # final ES longitude (°)
    gso_lon_deg: float          # final GSO satellite longitude (°)
    alpha_deg: float            # α angle at the ES (°)
    offaxis_deg: float          # off-axis angle φ at the ES (°)
    pfd_dBW: float              # PFD at the ES (dBW/m²/BWref)
    es_gain_rel_dB: float       # ES relative gain (dB)
    epfd_dBW: float             # instantaneous EPFD↓ (single-sat) (dBW/m²/BWref)
    elevation_deg: float        # elevation of the NGSO as seen from the ES (°)
    es_ecef_exact: np.ndarray = field(default_factory=lambda: np.zeros(3))
    epfd_aggregate_dBW: float = -999.0  # instantaneous aggregate EPFD↓ at the WCG (dBW/m²/BWref)
    es_lat_nominal: float = 0.0 # raw grid latitude (°)
    es_lon_nominal: float = 0.0 # raw grid longitude (°)
    gso_lon_nominal: float = 0.0 # raw GSO longitude (°)
    angular_velocity_deg_s: float = math.inf  # apparent angular velocity (deg/s)
    # planar θ angle (BSS), when required by the antenna pattern; None = not used
    planar_angle_deg: float | None = None
    # search data for visualization
    ref_sat_eci: np.ndarray = field(default_factory=lambda: np.zeros(3))
    search_trail: list[WCGSearchPoint] = field(default_factory=list)
    search_trail_all: list[WCGSearchPoint] = field(default_factory=list)
    # Per-satellite-latitude best point (EPFD/margin/ang.vel + resulting ES),
    # used to explain WHY the WCG sits where it does (EPFD-peak vs the
    # angular-velocity tie-break over a flat plateau). One dict per swept latitude.
    latitude_profile: list = field(default_factory=list)


def _resolve_wcga_parallel_jobs(
    n_jobs: int, n_lats: int | None = None
) -> tuple[int, int]:
    """Compute (P_procs, T_numba_threads) for hierarchical parallelism.

    Latitudes are the embarrassingly-parallel axis, and a large share of
    each latitude's work is *sequential Python* (boundary binary searches,
    extreme cases) where Numba threads sit idle. So we saturate the cores
    with processes first — one latitude per core — and only spend cores on
    inner Numba threads when there aren't enough latitudes to fill every
    process (e.g. low-inclination orbits). Total P × T ≤ cpu_count to avoid
    oversubscription.

    ``n_lats`` (number of latitudes to sweep) lets the policy size P to the
    actual work; when unknown it falls back to the requested core count.
    """
    global _LOGGED_WCGA_PARALLEL_POLICY

    total_cores = os.cpu_count() or 1

    if n_jobs == 0:
        requested = 1
    elif n_jobs < 0:
        requested = total_cores
    else:
        requested = n_jobs
    requested = max(1, requested)

    if requested == 1:
        return 1, get_numba_num_threads()

    # How many latitudes are there to spread? Default to `requested` when
    # the caller didn't say (keeps prior "fill the cores" intent).
    nlat = int(n_lats) if (n_lats and n_lats > 0) else requested

    # Processes first: one latitude per process, capped by cores / request.
    P = max(1, min(requested, total_cores, nlat))
    # Leftover cores become Numba threads per process — only useful when
    # P < cores (few latitudes) and the alpha batch is Numba-parallel.
    if alpha_batch_uses_numba_parallel():
        T = max(1, total_cores // P)
    else:
        T = 1

    if not _LOGGED_WCGA_PARALLEL_POLICY:
        logger.info(
            "WCGA S.1503: parallelism — %d processes × %d Numba threads "
            "(total_cores=%d, latitudes=%d)", P, T, total_cores, nlat,
        )
        _LOGGED_WCGA_PARALLEL_POLICY = True
    return P, T


def _wcga_pool_initializer(
    numba_threads: int,
    gmst0_deg: str,
    main_pid: str,
    gso_lon_mode: str,
) -> None:
    """Initializer for Pool workers: Numba, GMST0 and GSO/α mode (spawn resets globals)."""
    os.environ["WCG_GMST0_DEG"] = gmst0_deg
    os.environ["WCG_MAIN_PID"] = main_pid
    set_numba_num_threads(numba_threads)
    set_gso_longitude_mode(gso_lon_mode)


def _wcga_pool_worker(args: tuple) -> "_WCGState":
    """Top-level wrapper for _wcgd_calc_at_lat (pickle-friendly)."""
    oe_ref, lat_deg, common = args
    return _wcgd_calc_at_lat(oe_ref, lat_deg, **common)


# Optional injected executor for the WCGA latitude sweep. When set, it
# replaces the built-in multiprocessing.Pool so a single heavy WCGA can be
# fanned out across a cluster. The engine never imports the executor —
# pure dependency injection, so the app layer (Ray) can supply one without
# the engine depending on it. See ``set_wcga_executor``.
_WCGA_EXECUTOR: "Callable | None" = None


def set_wcga_executor(executor: "Callable | None") -> None:
    """Inject an alternative executor for the WCGA per-latitude sweep.

    ``executor(worker_fn, args_list, init)`` must run ``worker_fn(args)``
    for every ``args`` in ``args_list`` and return the results — order does
    not matter (the caller re-sorts by latitude). ``init`` is a dict of
    per-worker setup the executor must apply *inside each remote worker*
    before calling ``worker_fn`` (fresh workers don't run the local Pool
    initializer): keys ``gmst0_deg`` (str), ``main_pid`` (str),
    ``gso_mode`` (str), ``numba_threads`` (int).

    Pass ``None`` to restore the built-in ``multiprocessing.Pool`` path.
    """
    global _WCGA_EXECUTOR
    _WCGA_EXECUTOR = executor


def get_wcga_executor() -> "Callable | None":
    return _WCGA_EXECUTOR


def _lat_has_operating_satellites(
    lat_deg: float,
    max_co_freq_by_lat: list[tuple[float, float, int]] | None,
) -> bool:
    """Returns False only when ``sat_oper`` explicitly zeroes out the latitude."""
    if not max_co_freq_by_lat:
        return True
    for lat_fr, lat_to, nco in max_co_freq_by_lat:
        if lat_fr <= lat_deg <= lat_to:
            return int(nco) > 0
    return True


def _filter_wcga_latitudes_by_sat_oper(
    lat_list: list[float],
    max_co_freq_by_lat: list[tuple[float, float, int]] | None,
) -> tuple[list[float], int]:
    """Applies conservative pruning by ``sat_oper`` without removing ambiguous latitudes."""
    if not max_co_freq_by_lat:
        return lat_list, 0
    filtered = [lat for lat in lat_list if _lat_has_operating_satellites(lat, max_co_freq_by_lat)]
    return filtered, len(lat_list) - len(filtered)


def _build_wcgd_ray_context_from_xyz(
    sat_x: float,
    sat_y: float,
    sat_z: float,
) -> tuple[float, ...] | None:
    """Pre-computes the satellite's local frame for reuse on the boundaries."""
    r = math.sqrt(sat_x * sat_x + sat_y * sat_y + sat_z * sat_z)
    if r < 1e-12:
        return None

    nx = -sat_x / r
    ny = -sat_y / r
    nz = -sat_z / r

    dot_n = nz
    px = -dot_n * nx
    py = -dot_n * ny
    pz = 1.0 - dot_n * nz
    n_np = math.sqrt(px * px + py * py + pz * pz)
    if n_np < 1e-10:
        px, py, pz = 1.0, 0.0, 0.0
        dot_p = px * nx + py * ny + pz * nz
        px -= dot_p * nx
        py -= dot_p * ny
        pz -= dot_p * nz
        n_np = math.sqrt(px * px + py * py + pz * pz)
        if n_np < 1e-12:
            return None
    px /= n_np
    py /= n_np
    pz /= n_np

    ex = ny * pz - nz * py
    ey = nz * px - nx * pz
    ez = nx * py - ny * px
    n_e = math.sqrt(ex * ex + ey * ey + ez * ez)
    if n_e < 1e-12:
        return None
    ex /= n_e
    ey /= n_e
    ez /= n_e

    sat_norm2 = sat_x * sat_x + sat_y * sat_y + sat_z * sat_z
    c_term = sat_norm2 - RE_KM * RE_KM
    return (
        sat_x, sat_y, sat_z,
        nx, ny, nz,
        px, py, pz,
        ex, ey, ez,
        c_term,
    )


def _build_wcgd_ray_context(sat_ecef: np.ndarray) -> tuple[float, ...] | None:
    return _build_wcgd_ray_context_from_xyz(
        float(sat_ecef[0]),
        float(sat_ecef[1]),
        float(sat_ecef[2]),
    )


def _build_wcgd_batch_geom(sat_ecef: np.ndarray):
    """Pre-computes quantities reused across all φ iterations.

    Returns ``(nadir, north_perp, east, sat_norm2, C, sat_local_frame)`` where
    `nadir/north_perp/east` form the local basis used to build the line-of-sight
    vector `look = cp·nadir + sp·(ct·N + st·E)` (same convention as
    `_wcgd_ray_to_earth`), `C = |sat|² − R⊕²` is the term independent of the
    ray-Earth intersection, and `sat_local_frame` is the tuple of the local
    geographic frame (used by azimuth/elevation masks).
    """
    r = float(np.linalg.norm(sat_ecef))
    nadir = -sat_ecef / r
    north = np.array([0.0, 0.0, 1.0])
    north_perp = north - float(np.dot(north, nadir)) * nadir
    n_np = float(np.linalg.norm(north_perp))
    if n_np < 1e-10:
        north_perp = np.array([1.0, 0.0, 0.0])
        north_perp -= float(np.dot(north_perp, nadir)) * nadir
        n_np = float(np.linalg.norm(north_perp))
    north_perp /= n_np
    east = np.cross(nadir, north_perp)
    east /= float(np.linalg.norm(east))

    sat_norm2 = float(np.dot(sat_ecef, sat_ecef))
    C = sat_norm2 - RE_KM * RE_KM
    sat_local_frame = _build_sat_local_frame_ecef(sat_ecef)
    return (nadir, north_perp, east, sat_norm2, C, sat_local_frame)


def _build_sat_local_frame_ecef(ngso_sat_ecef: np.ndarray) -> tuple[float, ...]:
    """Builds the satellite's local frame (nadir, east, north)."""
    nx = float(ngso_sat_ecef[0])
    ny = float(ngso_sat_ecef[1])
    nz = float(ngso_sat_ecef[2])
    n_norm = math.sqrt(nx * nx + ny * ny + nz * nz)
    if n_norm < 1e-12:
        # Degenerate case: neutral frame.
        return (0.0, 0.0, -1.0, 0.0, 1.0, 0.0, -1.0, 0.0, 0.0)

    # z_nadir points from the satellite toward the Earth's center.
    zx = -nx / n_norm
    zy = -ny / n_norm
    zz = -nz / n_norm

    # east = cross(z_nadir, north_pole)
    yx = zy
    yy = -zx
    yz = 0.0
    y_norm = math.sqrt(yx * yx + yy * yy)
    if y_norm < 1e-6:
        yx, yy, yz = 0.0, 1.0, 0.0
    else:
        inv_y = 1.0 / y_norm
        yx *= inv_y
        yy *= inv_y

    # north = cross(east, nadir)
    xx = yy * zz - yz * zy
    xy = yz * zx - yx * zz
    xz = yx * zy - yy * zx

    return (zx, zy, zz, yx, yy, yz, xx, xy, xz)


def _compute_mask_az_el_from_frame(
    es_ecef: np.ndarray,
    ngso_sat_ecef: np.ndarray,
    sat_local_frame: tuple[float, ...],
) -> tuple[float, float] | None:
    """Computes (B,C) of the az/el mask in the satellite's local frame."""
    ex = float(es_ecef[0])
    ey = float(es_ecef[1])
    ez = float(es_ecef[2])
    nx = float(ngso_sat_ecef[0])
    ny = float(ngso_sat_ecef[1])
    nz = float(ngso_sat_ecef[2])

    vx = ex - nx
    vy = ey - ny
    vz = ez - nz
    if (vx * vx + vy * vy + vz * vz) < 1e-6:
        return None

    zx, zy, zz, yx, yy, yz, xx, xy, xz = sat_local_frame
    z_val = vx * zx + vy * zy + vz * zz
    dot_east = vx * yx + vy * yy + vz * yz
    dot_north = vx * xx + vy * xy + vz * xz

    # ITU-R S.1503-4 §D3.1.3.1: cos(az)·cos(el) = nadir/r, sin(el) = north/r,
    # sin(az)·cos(el) = east/r ⇒ el = asin(north/r), az = atan2(east, nadir).
    # The previous implementation used atan2(north, nadir), which underestimates |el|
    # when |east|>0 (i.e., any point off the satellite's meridian).
    v_norm = math.sqrt(vx * vx + vy * vy + vz * vz)
    if v_norm < 1e-9:
        return None
    sin_el = dot_north / v_norm
    if sin_el > 1.0:
        sin_el = 1.0
    elif sin_el < -1.0:
        sin_el = -1.0

    rad2deg = 180.0 / math.pi
    if z_val < 1e-6:
        z_val = 1e-6  # degenerate case (ES on the sat's horizon); avoids atan2(0,0)
    d_az_mask = math.atan2(dot_east, z_val) * rad2deg
    d_el_mask = math.asin(sin_el) * rad2deg
    return d_az_mask, d_el_mask


def _compute_mask_az_el_from_frame_batch(
    es_ecef_all: np.ndarray,
    ngso_sat_ecef: np.ndarray,
    sat_local_frame: tuple[float, ...],
) -> tuple[np.ndarray, np.ndarray]:
    """Vectorized version of `_compute_mask_az_el_from_frame`.

    Returns `(d_az_mask, d_el_mask)` in degrees, with NaN on degenerate rows
    (vector v ≈ 0) — the caller must filter by isnan.
    """
    v = es_ecef_all - ngso_sat_ecef[np.newaxis, :]
    v_norm2 = np.einsum("ij,ij->i", v, v)
    valid = v_norm2 >= 1e-6

    zx, zy, zz, yx, yy, yz, xx, xy, xz = sat_local_frame
    z_val = v[:, 0] * zx + v[:, 1] * zy + v[:, 2] * zz
    dot_east = v[:, 0] * yx + v[:, 1] * yy + v[:, 2] * yz
    dot_north = v[:, 0] * xx + v[:, 1] * xy + v[:, 2] * xz

    # ITU-R S.1503-4 §D3.1.3.1: el = asin(north/r) (not atan2(north, nadir)).
    v_norm = np.sqrt(v_norm2)
    safe_norm = np.where(valid, v_norm, 1.0)
    sin_el = np.clip(dot_north / safe_norm, -1.0, 1.0)
    np.maximum(z_val, 1e-6, out=z_val)  # avoids atan2(0,0) on the sat's horizon

    az = np.degrees(np.arctan2(dot_east, z_val))
    el = np.degrees(np.arcsin(sin_el))
    az = np.where(valid, az, np.nan)
    el = np.where(valid, el, np.nan)
    return az, el


def _build_sat_local_frames_ecef_batch(
    pos_ecef_all: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vectorized :func:`_build_sat_local_frame_ecef` for many satellites.

    ``pos_ecef_all`` is ``(N, 3)``; returns ``(z, y, x)`` each ``(N, 3)`` — the
    nadir, east and north axes of every satellite's local geographic frame.
    Numerically identical to the scalar builder, including both degenerate
    cases (|pos| ≈ 0 → neutral frame; near-polar east singularity → (0, 1, 0)).
    """
    n = np.asarray(pos_ecef_all, dtype=np.float64)
    nrm = np.sqrt(np.einsum("ij,ij->i", n, n))          # (N,)
    safe = nrm >= 1e-12  # scalar uses `n_norm < 1e-12` for the neutral frame
    inv = np.where(safe, 1.0 / np.where(safe, nrm, 1.0), 0.0)

    # z_nadir = -pos / |pos|
    zx = -n[:, 0] * inv
    zy = -n[:, 1] * inv
    zz = -n[:, 2] * inv

    # east = normalize(cross(z, north_pole)) → (zy, -zx, 0)
    yx = zy.copy()
    yy = -zx.copy()
    yz = np.zeros_like(zx)
    ynorm = np.sqrt(yx * yx + yy * yy)
    small = ynorm < 1e-6
    inv_y = np.where(small, 0.0, 1.0 / np.where(small, 1.0, ynorm))
    yx = np.where(small, 0.0, yx * inv_y)
    yy = np.where(small, 1.0, yy * inv_y)

    # north = cross(east, nadir)
    xx = yy * zz - yz * zy
    xy = yz * zx - yx * zz
    xz = yx * zy - yy * zx

    # Degenerate |pos| ≈ 0 → neutral frame (matches the scalar fallback).
    deg = ~safe
    if np.any(deg):
        zx[deg], zy[deg], zz[deg] = 0.0, 0.0, -1.0
        yx[deg], yy[deg], yz[deg] = 0.0, 1.0, 0.0
        xx[deg], xy[deg], xz[deg] = -1.0, 0.0, 0.0

    z = np.stack((zx, zy, zz), axis=1)
    y = np.stack((yx, yy, yz), axis=1)
    x = np.stack((xx, xy, xz), axis=1)
    return z, y, x


def _compute_mask_az_el_per_sat_frame_batch(
    es_ecef: np.ndarray,
    sat_ecef_all: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Az/El mask coordinates for ONE ES seen by MANY satellites.

    Unlike :func:`_compute_mask_az_el_from_frame_batch` (one satellite frame vs
    many ES points, used by the WCGA), this evaluates each satellite in **its
    own** local frame — the form needed by the EPFD↓ per-time-step accumulation.

    ``es_ecef`` is ``(3,)``; ``sat_ecef_all`` is ``(N, 3)``. Returns
    ``(az, el)`` in degrees, with ``NaN`` on degenerate rows (ES coincident with
    the satellite). Numerically identical to the scalar
    :func:`_compute_mask_az_el_from_frame`.
    """
    z, y, x = _build_sat_local_frames_ecef_batch(sat_ecef_all)
    v = np.asarray(es_ecef, dtype=np.float64)[np.newaxis, :] - np.asarray(
        sat_ecef_all, dtype=np.float64
    )
    vn2 = np.einsum("ij,ij->i", v, v)
    valid = vn2 >= 1e-6

    z_val = np.einsum("ij,ij->i", v, z)
    dot_east = np.einsum("ij,ij->i", v, y)
    dot_north = np.einsum("ij,ij->i", v, x)

    vn = np.sqrt(vn2)
    safe = np.where(valid, vn, 1.0)
    sin_el = np.clip(dot_north / safe, -1.0, 1.0)
    np.maximum(z_val, 1e-6, out=z_val)  # avoids atan2(0,0) on the sat's horizon

    az = np.degrees(np.arctan2(dot_east, z_val))
    el = np.degrees(np.arcsin(sin_el))
    az = np.where(valid, az, np.nan)
    el = np.where(valid, el, np.nan)
    return az, el


def _compute_offaxis_angle_batch(
    es_ecef_all: np.ndarray,
    sat_ecef: np.ndarray,
    lon_deg_all: np.ndarray,
) -> np.ndarray:
    """Computes off-axis (degrees) in batch for multiple ES points."""
    lon_rad = np.radians(lon_deg_all)
    gso_ecef_all = np.column_stack((
        GSO_RADIUS_KM * np.cos(lon_rad),
        GSO_RADIUS_KM * np.sin(lon_rad),
        np.zeros_like(lon_rad),
    ))
    v_ngso = sat_ecef[np.newaxis, :] - es_ecef_all
    v_gso = gso_ecef_all - es_ecef_all
    n_ngso = np.linalg.norm(v_ngso, axis=1)
    n_gso = np.linalg.norm(v_gso, axis=1)
    denom = n_ngso * n_gso
    safe = denom > 1e-12
    cosang = np.ones_like(denom)
    cosang[safe] = np.einsum("ij,ij->i", v_ngso[safe], v_gso[safe]) / denom[safe]
    np.clip(cosang, -1.0, 1.0, out=cosang)
    return np.degrees(np.arccos(cosang))


def _compute_offaxis_angle_batch_gso(
    es_ecef_all: np.ndarray,
    sat_ecef: np.ndarray,
    gso_ecef_all: np.ndarray,
) -> np.ndarray:
    """Off-axis (degrees) with explicit GSO positions (N,3) — e.g. optimum on the arc."""
    v_ngso = sat_ecef[np.newaxis, :] - es_ecef_all
    v_gso = gso_ecef_all - es_ecef_all
    n_ngso = np.linalg.norm(v_ngso, axis=1)
    n_gso = np.linalg.norm(v_gso, axis=1)
    denom = n_ngso * n_gso
    safe = denom > 1e-12
    cosang = np.ones_like(denom)
    cosang[safe] = np.einsum("ij,ij->i", v_ngso[safe], v_gso[safe]) / denom[safe]
    np.clip(cosang, -1.0, 1.0, out=cosang)
    return np.degrees(np.arccos(cosang))


def _relative_gain_batch(
    es_antenna: EarthStationAntenna,
    offaxis_deg: np.ndarray,
    theta_deg: np.ndarray | None = None,
) -> np.ndarray:
    """Computes Grel(φ) in batch; scalar fallback for non-S.1428 antennas."""
    if not isinstance(es_antenna, ITURS1428Antenna):
        if theta_deg is None:
            return np.array([es_antenna.relative_gain(float(v)) for v in offaxis_deg], dtype=float)
        return np.array(
            [es_antenna.relative_gain(float(v), float(t)) for v, t in zip(offaxis_deg, theta_deg)],
            dtype=float,
        )

    phi = np.minimum(np.abs(offaxis_deg), 180.0)
    gain = np.empty_like(phi, dtype=float)

    m0 = phi < 1e-10
    gain[m0] = es_antenna.g_max

    m1 = (~m0) & (phi < es_antenna.phi_m)
    if np.any(m1):
        gain[m1] = es_antenna.g_max - 2.5e-3 * (es_antenna.d_over_lambda * phi[m1]) ** 2

    if es_antenna.regime in ("20_25", "25_100"):
        m2 = (~m0) & (~m1) & (phi < es_antenna.phi_plateau_max)
        if np.any(m2):
            gain[m2] = es_antenna.g1

        m3 = (~m0) & (~m1) & (~m2) & (phi <= 33.1)
        if np.any(m3):
            gain[m3] = 29.0 - 25.0 * np.log10(phi[m3])

        m4 = (~m0) & (~m1) & (~m2) & (~m3) & (phi <= 80.0)
        if np.any(m4):
            gain[m4] = -9.0

        if es_antenna.regime == "20_25":
            m5 = ~(m0 | m1 | m2 | m3 | m4)
            if np.any(m5):
                gain[m5] = -5.0
        else:
            m5 = (~m0) & (~m1) & (~m2) & (~m3) & (~m4) & (phi <= 120.0)
            if np.any(m5):
                gain[m5] = -4.0
            m6 = ~(m0 | m1 | m2 | m3 | m4 | m5)
            if np.any(m6):
                gain[m6] = -9.0
    else:
        m2 = (~m0) & (~m1) & (phi < es_antenna.phi_r)
        if np.any(m2):
            gain[m2] = es_antenna.g1

        m3 = (~m0) & (~m1) & (~m2) & (phi < 10.0)
        if np.any(m3):
            gain[m3] = 29.0 - 25.0 * np.log10(phi[m3])

        m4 = (~m0) & (~m1) & (~m2) & (~m3) & (phi < 34.1)
        if np.any(m4):
            gain[m4] = 34.0 - 30.0 * np.log10(phi[m4])

        m5 = (~m0) & (~m1) & (~m2) & (~m3) & (~m4) & (phi < 80.0)
        if np.any(m5):
            gain[m5] = -12.0

        m6 = (~m0) & (~m1) & (~m2) & (~m3) & (~m4) & (~m5) & (phi < 120.0)
        if np.any(m6):
            gain[m6] = -7.0

        m7 = ~(m0 | m1 | m2 | m3 | m4 | m5 | m6)
        if np.any(m7):
            gain[m7] = -12.0

    return gain - es_antenna.g_max


def _compute_pfd_3d(
    pfd_mask: PFDMask,
    alpha_deg: float,
    ngso_sat_eci: np.ndarray,
    ngso_sat_vel_eci: np.ndarray,
    es_lon_deg: float,
    t_s: float,
    es_lat_deg: float = 0.0,
    gso_ecef: np.ndarray | None = None,
    pfd_bw_correction_db: float = 0.0,
    ngso_sat_ecef: np.ndarray | None = None,   # pre-computed by the caller
    es_ecef_cached: np.ndarray | None = None,  # pre-computed by the caller
    subsat_lat_deg: float | None = None,       # pre-computed sub-satellite latitude
    subsat_lon_deg: float | None = None,       # pre-computed sub-satellite longitude
    sat_local_frame: tuple[float, ...] | None = None,  # pre-computed local frame
    gso_lon_deg: float | None = None,  # LongAlpha (§D6.4.4); if None, uses compute_alpha_and_optimal_gso
    sat_idx: int | None = None,        # sat index within the constellation (PFDMaskMulti)
) -> float:
    """Computes PFD. For Az/El, uses coordinates RELATIVE to the satellite's frame.

    The optional ngso_sat_ecef and es_ecef_cached parameters let the caller
    reuse already-computed ECEF positions, avoiding redundant recomputations.
    """
    pfd_val = -1000.0

    # Route through THIS satellite's own sub-mask so the coordinate system
    # (alpha/Δλ vs Az/El) matches the mask actually assigned to it — required
    # for mixed-geometry PFDMaskMulti fusions (method_3). For single masks
    # ``mask_for_sat`` is absent and ``mask_eff`` stays the mask itself.
    mask_eff = pfd_mask
    if sat_idx is not None and hasattr(pfd_mask, "mask_for_sat"):
        mask_eff = pfd_mask.mask_for_sat(sat_idx)

    # 1D mask: depends only on alpha.
    if getattr(mask_eff, "_dim", 3) == 1:
        return mask_eff.get_pfd(alpha_deg) + pfd_bw_correction_db

    if hasattr(mask_eff, "mask_type") and mask_eff.mask_type == "azimuth_elevation":
        # S.1503 §D — azimuth/elevation mask in the satellite's local geographic frame:
        #   B (azimuth)   = East–West deviation from the nadir beam  (East = +)
        #   C (elevation) = North–South deviation from the nadir beam (North = +)
        # The direction of satellite motion does NOT affect the result: the direct
        # geographic projection is equivalent to the "orbital frame → heading
        # rotation" path and does not require the velocity vector.
        ngso_ecef = ngso_sat_ecef if ngso_sat_ecef is not None else eci_to_ecef(ngso_sat_eci, t_s)
        es_ecef   = es_ecef_cached if es_ecef_cached is not None else lla_to_ecef(es_lat_deg, es_lon_deg, 0.0)
        if sat_local_frame is None:
            sat_local_frame = _build_sat_local_frame_ecef(ngso_ecef)
        mask_angles = _compute_mask_az_el_from_frame(es_ecef, ngso_ecef, sat_local_frame)
        if mask_angles is None:
            return -1000.0
        d_az_mask, d_el_mask = mask_angles

        # Compute satellite latitude (sub-satellite point)
        # Required because the PFD mask depends on the satellite latitude, not the ES.
        sat_lat_deg = subsat_lat_deg
        if sat_lat_deg is None:
            sat_lat_deg, _, _ = ecef_to_lla(ngso_ecef)

        pfd_val = mask_eff.get_pfd(
            alpha_deg=d_az_mask,
            lat_deg=sat_lat_deg,
            delta_lon_deg=d_el_mask,
        )
    else:
        # Default case: alpha_deltaLongitude
        # Sub-satellite latitude
        if subsat_lat_deg is None or subsat_lon_deg is None:
            if ngso_sat_ecef is not None:
                sub_lat, sub_lon, _ = ecef_to_lla(ngso_sat_ecef)
            else:
                sub_lat, sub_lon = sub_satellite_point(ngso_sat_eci, t_s)
        else:
            sub_lat, sub_lon = subsat_lat_deg, subsat_lon_deg

        # ITU-R S.1503 §D6.4.4: ΔLong = LongAlpha − LongNGSO (longitude of the GSO point that minimizes α).
        lon_alpha = gso_lon_deg
        if lon_alpha is None:
            ngso_ecef_for_alpha = (
                ngso_sat_ecef if ngso_sat_ecef is not None else eci_to_ecef(ngso_sat_eci, t_s)
            )
            es_ecef_for_alpha = (
                es_ecef_cached if es_ecef_cached is not None
                else lla_to_ecef(es_lat_deg, es_lon_deg, 0.0)
            )
            _, _, lon_alpha = compute_alpha_and_optimal_gso(
                es_ecef_for_alpha, ngso_ecef_for_alpha, es_lat_deg, es_lon_deg,
            )
        delta_lon = delta_longitude_s1503_deg(lon_alpha, sub_lon)

        pfd_val = mask_eff.get_pfd(alpha_deg, lat_deg=sub_lat, delta_lon_deg=delta_lon)

    # Apply BW correction (e.g. 40kHz -> 1MHz)
    return pfd_val + pfd_bw_correction_db


# Helper for parallelism (must be at module level to be picklable)
def _evaluate_batch(args):
    """Evaluates a batch of (theta, phi) points.

    Pre-computes ngso_ecef and ngso_vel_ecef ONCE per batch (the reference
    satellite is the same for all points), passing them as a cache to
    _compute_pfd_3d to eliminate redundant recomputations.
    """
    (points, ngso_sat_eci, ngso_sat_vel_eci, t_s, pfd_mask, es_antenna,
     alpha0_deg, min_elevation_deg, gso_min_elevation_deg, pfd_bw_correction_db,
     strict_exclusion_zone) = args

    # Pre-computed ONCE per batch (same reference satellite)
    ngso_ecef_cached     = eci_to_ecef(ngso_sat_eci, t_s)
    subsat_lat_cached, subsat_lon_cached = sub_satellite_point(ngso_sat_eci, t_s)
    sat_local_frame_cached = _build_sat_local_frame_ecef(ngso_ecef_cached)

    results = []

    for (theta_deg, phi_deg) in points:
        theta_rad = theta_deg * DEG2RAD
        phi_rad   = phi_deg   * DEG2RAD

        es_ecef, _, es_lat, es_lon = theta_phi_to_es_and_gso(
            ngso_sat_eci, theta_rad, phi_rad, t_s
        )

        if np.linalg.norm(es_ecef) < 1.0:
            results.append(WCGSearchPoint(
                theta_deg, phi_deg, 0, 0, 0, 0, -999, "invalid"))
            continue

        # S.1503-4: an ES above 81.2° cannot see the GSO arc — exclude
        if abs(es_lat) > 81.2:
            results.append(WCGSearchPoint(
                theta_deg, phi_deg, es_lat, es_lon, 0, 0, -999, "invalid"))
            continue

        # NGSO elevation (uses pre-computed ngso_ecef).  S.1503-4 §D6.4.3
        # visibility is a horizon/line-of-sight test; ε0/εGSO gate only the
        # Step 18 AND-branch below, not the gain OR-branch.
        elevation = compute_elevation(es_ecef, ngso_ecef_cached, es_lat, es_lon)
        if elevation < -WCG_ELEV_BOUNDARY_TOL_DEG:
            results.append(WCGSearchPoint(
                theta_deg, phi_deg, es_lat, es_lon, 0, elevation, -999, "low_elev"))
            continue

        alpha, gso_ecef, gso_lon = compute_alpha_and_optimal_gso(
            es_ecef, ngso_ecef_cached, es_lat, es_lon,
        )
        gso_elevation = compute_elevation(es_ecef, gso_ecef, es_lat, es_lon)
        # S.1503-4 D3.1.2 / D5.1 Step 18:
        # (|α| ≥ α0 AND εNGSO ≥ ε0 AND εGSO ≥ εGSO,min) OR gain criterion.
        offaxis = compute_offaxis_angle(es_ecef, ngso_ecef_cached, gso_ecef)
        theta_planar = None
        if es_antenna.requires_planar_angle:
            _, theta_planar = compute_offaxis_and_planar_angle(
                es_ecef, ngso_ecef_cached, gso_ecef, es_lat, es_lon
            )
        and_branch = (
            abs(alpha) >= alpha0_deg
            and elevation >= min_elevation_deg - WCG_ELEV_BOUNDARY_TOL_DEG
            and gso_elevation >= gso_min_elevation_deg - WCG_ELEV_BOUNDARY_TOL_DEG
        )
        or_branch = s1503_or_condition_include(
            es_antenna, offaxis, alpha0_deg, theta_planar,
            disable_or_condition=strict_exclusion_zone,
        )
        if not (and_branch or or_branch):
            status = "low_elev" if (
                elevation < min_elevation_deg - WCG_ELEV_BOUNDARY_TOL_DEG
                or gso_elevation < gso_min_elevation_deg - WCG_ELEV_BOUNDARY_TOL_DEG
            ) else "exclusion"
            results.append(WCGSearchPoint(
                theta_deg, phi_deg, es_lat, es_lon, alpha, elevation, -999, status))
            continue

        # Mask PFD (passes pre-computed ECEF to avoid recomputation)
        pfd_db = _compute_pfd_3d(
            pfd_mask=pfd_mask,
            alpha_deg=alpha,
            ngso_sat_eci=ngso_sat_eci,
            ngso_sat_vel_eci=ngso_sat_vel_eci,
            es_lon_deg=es_lon,
            t_s=t_s,
            es_lat_deg=es_lat,
            gso_ecef=gso_ecef,
            pfd_bw_correction_db=pfd_bw_correction_db,
            ngso_sat_ecef=ngso_ecef_cached,
            es_ecef_cached=es_ecef,
            subsat_lat_deg=subsat_lat_cached,
            subsat_lon_deg=subsat_lon_cached,
            sat_local_frame=sat_local_frame_cached,
            gso_lon_deg=gso_lon,
        )

        g_rel_db = es_antenna.relative_gain(offaxis, theta_planar)
        epfd_db  = pfd_db + g_rel_db

        results.append(WCGSearchPoint(
            theta_deg, phi_deg, es_lat, es_lon, alpha, elevation, epfd_db, "ok",
            gso_lon_deg=gso_lon,
        ))

    return results


def _run_grid_search(
    points_to_eval: list[tuple[float, float]],
    ngso_sat_eci: np.ndarray,
    ngso_sat_vel_eci: np.ndarray,
    t_s: float,
    pfd_mask: PFDMask,
    es_antenna: EarthStationAntenna,
    alpha0_deg: float,
    min_elevation_deg: float,
    gso_min_elevation_deg: float,
    pfd_bw_correction_db: float,
    n_jobs: int,
    angular_velocity_fn,
    progress_label: str | None = None,
    strict_exclusion_zone: bool = False,
) -> tuple[WCGResult | None, float, float, list]:
    """Evaluates a list of (theta, phi) points and returns the best WCGResult.

    Returns (best_result, best_epfd, best_ang_vel, trail).
    """
    import multiprocessing

    # Uses _WCGState (S.1503-4 §D.3.1.2 rule with bin_top separated from the
    # winner). Converts points into WCGResult only when they enter the
    # candidate window (avoids overhead).
    state = _WCGState()
    trail: list[WCGSearchPoint] = []

    total_points = len(points_to_eval)
    batch_size = max(1, total_points // (n_jobs * 4))
    batches = [points_to_eval[i:i + batch_size] for i in range(0, total_points, batch_size)]
    total_batches = len(batches)

    pool_args = [
        (batch, ngso_sat_eci, ngso_sat_vel_eci, t_s, pfd_mask, es_antenna,
         alpha0_deg, min_elevation_deg, gso_min_elevation_deg, pfd_bw_correction_db,
         strict_exclusion_zone)
        for batch in batches
    ]

    def _process_batch(batch_res):
        trail.extend(batch_res)
        for pt in batch_res:
            if pt.status != "ok":
                continue
            # O(1) pre-filter: only create a WCGResult if the point fits the
            # current bin window or can form a new bin.
            if pt.epfd_dBW < state.bin_top_margin - state.BIN_SIZE:
                continue
            ang_vel = angular_velocity_fn(pt.es_lat_deg, pt.es_lon_deg)
            gso_lon_pt = (
                pt.gso_lon_deg if pt.gso_lon_deg is not None else pt.es_lon_deg
            )
            result = WCGResult(
                theta_deg=pt.theta_deg, phi_deg=pt.phi_deg,
                es_lat_deg=pt.es_lat_deg, es_lon_deg=pt.es_lon_deg,
                gso_lon_deg=gso_lon_pt,
                es_lat_nominal=pt.es_lat_deg, es_lon_nominal=pt.es_lon_deg,
                gso_lon_nominal=gso_lon_pt,
                alpha_deg=pt.alpha_deg, offaxis_deg=0.0,
                pfd_dBW=0.0, es_gain_rel_dB=0.0,
                epfd_dBW=pt.epfd_dBW, elevation_deg=pt.elevation_deg,
                angular_velocity_deg_s=ang_vel,
                ref_sat_eci=ngso_sat_eci.copy(),
            )
            state.update(pt.epfd_dBW, ang_vel, result)

    done_batches = 0
    done_points = 0
    progress_every = max(1, total_batches // 20)  # ~5%

    def _maybe_log_progress() -> None:
        if not progress_label:
            return
        pct = (done_points / max(1, total_points)) * 100.0
        width = 28
        filled = int(round((pct / 100.0) * width))
        bar = "[" + ("#" * filled) + ("-" * (width - filled)) + "]"
        logger.info(
            f"  {progress_label} {bar} {pct:6.2f}%  "
            f"points {done_points}/{total_points}  "
            f"batches {done_batches}/{total_batches}"
        )

    if n_jobs == 1:
        for args in pool_args:
            batch_res = _evaluate_batch(args)
            _process_batch(batch_res)
            done_batches += 1
            done_points += len(batch_res)
            if done_batches % progress_every == 0 or done_batches == total_batches:
                _maybe_log_progress()
    else:
        gmst0_str = os.environ.get("WCG_GMST0_DEG", "0.0")
        main_pid_str = os.environ.get("WCG_MAIN_PID", str(os.getpid()))
        gso_mode = get_gso_longitude_mode()
        with multiprocessing.Pool(
            processes=n_jobs,
            initializer=_wcga_pool_initializer,
            initargs=(
                get_numba_num_threads(),
                gmst0_str,
                main_pid_str,
                gso_mode,
            ),
        ) as pool:
            for batch_res in pool.imap_unordered(_evaluate_batch, pool_args):
                _process_batch(batch_res)
                done_batches += 1
                done_points += len(batch_res)
                if done_batches % progress_every == 0 or done_batches == total_batches:
                    _maybe_log_progress()

    return state.best_result, state.best_epfd, state.best_ang_vel, trail


def search_wcg(
    ngso_sat_eci: np.ndarray,
    ngso_sat_vel_eci: np.ndarray, # we need the velocity for the frame
    t_s: float,
    pfd_mask: PFDMask,
    es_antenna: EarthStationAntenna,
    alpha0_deg: float,
    min_elevation_deg: float,
    gso_min_elevation_deg: float = 5.0,
    phi_step_deg: float = 0.5,
    phi_max_deg: float = 70.0,
    theta_min_deg: float = -90.0,
    theta_max_deg: float = 270.0,
    fixed_es_lat: float | None = None,
    fixed_es_lon: float | None = None,
    n_jobs: int = -1,  # -1 = auto (CPU count)
    pfd_bw_correction_db: float = 0.0,
    refinement_factor: int = 5,
    refinement_radius_factor: float = 2.0,
    strict_exclusion_zone: bool = False,
) -> WCGResult | None:
    """Runs the WCG search in parallel across the CPU cores.

    After the coarse sweep, performs a local refinement step around the best
    candidate with a step of phi_step_deg / refinement_factor, covering a
    window of refinement_radius_factor * phi_step_deg around (θ*, φ*).
    This ensures good resolution even when the mask has narrow regions
    near α ≈ 0 (which map to small angular windows in (θ, φ)).
    """
    def _angular_velocity_for_point(es_lat_deg: float, es_lon_deg: float) -> float:
        es_ecef = lla_to_ecef(es_lat_deg, es_lon_deg, 0.0)
        pos_ecef = eci_to_ecef(ngso_sat_eci, t_s)
        vel_ecef = eci_vel_to_ecef(ngso_sat_vel_eci, pos_ecef, t_s)
        return compute_angular_velocity(es_ecef, pos_ecef, vel_ecef)
    
    # --- FIXED-POINT MODE ---
    if fixed_es_lat is not None and fixed_es_lon is not None:
        # Original fixed-point logic
        es_lat = fixed_es_lat
        es_lon = fixed_es_lon

        es_ecef = lla_to_ecef(es_lat, es_lon, 0.0)
        ngso_ecef = eci_to_ecef(ngso_sat_eci, t_s)
        sat_local_frame = _build_sat_local_frame_ecef(ngso_ecef)

        alpha, gso_ecef, gso_lon = compute_alpha_and_optimal_gso(
            es_ecef, ngso_ecef, es_lat, es_lon
        )

        elevation = compute_elevation(es_ecef, ngso_ecef, es_lat, es_lon)
        gso_elevation = compute_elevation(es_ecef, gso_ecef, es_lat, es_lon)
        offaxis = compute_offaxis_angle(es_ecef, ngso_ecef, gso_ecef)
        theta_planar = None
        if es_antenna.requires_planar_angle:
            _, theta_planar = compute_offaxis_and_planar_angle(
                es_ecef, ngso_ecef, gso_ecef, es_lat, es_lon
            )

        and_branch = (
            abs(alpha) >= alpha0_deg
            and elevation >= min_elevation_deg - WCG_ELEV_BOUNDARY_TOL_DEG
            and gso_elevation >= gso_min_elevation_deg - WCG_ELEV_BOUNDARY_TOL_DEG
        )
        or_branch = s1503_or_condition_include(
            es_antenna, offaxis, alpha0_deg, theta_planar,
            disable_or_condition=strict_exclusion_zone,
        )
        if and_branch or or_branch:
            status = "ok"
        elif (
            elevation < min_elevation_deg - WCG_ELEV_BOUNDARY_TOL_DEG
            or gso_elevation < gso_min_elevation_deg - WCG_ELEV_BOUNDARY_TOL_DEG
        ):
            status = "low_elev"
        else:
            status = "exclusion"

        pfd_db = _compute_pfd_3d(
            pfd_mask=pfd_mask,
            alpha_deg=alpha,
            ngso_sat_eci=ngso_sat_eci,
            ngso_sat_vel_eci=ngso_sat_vel_eci,
            es_lon_deg=es_lon,
            t_s=t_s,
            es_lat_deg=es_lat,
            gso_ecef=gso_ecef,
            pfd_bw_correction_db=pfd_bw_correction_db,
            ngso_sat_ecef=ngso_ecef,
            es_ecef_cached=es_ecef,
            sat_local_frame=sat_local_frame,
            gso_lon_deg=gso_lon,
        )
        g_rel_db = es_antenna.relative_gain(offaxis, theta_planar)
        epfd_db = pfd_db + g_rel_db
        ang_vel = _angular_velocity_for_point(es_lat, es_lon)

        if status != "ok":
            epfd_db = -999.0
            
        return WCGResult(
            theta_deg=0.0, phi_deg=0.0,
            es_lat_deg=es_lat, es_lon_deg=es_lon, gso_lon_deg=gso_lon,
            es_lat_nominal=es_lat, es_lon_nominal=es_lon, gso_lon_nominal=gso_lon,
            alpha_deg=alpha, offaxis_deg=offaxis,
            pfd_dBW=pfd_db, es_gain_rel_dB=g_rel_db,
            epfd_dBW=epfd_db, elevation_deg=elevation,
            es_ecef_exact=es_ecef.copy(),
            angular_velocity_deg_s=ang_vel,
            ref_sat_eci=ngso_sat_eci.copy(),
            search_trail=[],
        )

    # --- PARALLEL / SEQUENTIAL SEARCH MODE ---
    import multiprocessing

    if n_jobs < 1:
        n_jobs = multiprocessing.cpu_count()
    n_jobs = max(1, n_jobs)

    # ── Phase 1: coarse sweep ─────────────────────────────────────────────
    points_to_eval = []
    phi_deg = phi_step_deg
    while phi_deg <= phi_max_deg:
        num_theta_steps = max(4, int(math.ceil(2.0 * math.pi * phi_deg / phi_step_deg)))
        theta_range = theta_max_deg - theta_min_deg
        theta_step = theta_range / num_theta_steps
        t_deg = theta_min_deg
        while t_deg <= theta_max_deg:
            points_to_eval.append((t_deg, phi_deg))
            t_deg += theta_step
        phi_deg += phi_step_deg

    logger.info(f"Parallel WCG search (coarse): {len(points_to_eval)} points across {n_jobs} cores.")

    _t_coarse = time.perf_counter()
    best_result, best_epfd, best_ang_vel, trail = _run_grid_search(
        points_to_eval, ngso_sat_eci, ngso_sat_vel_eci, t_s,
        pfd_mask, es_antenna, alpha0_deg, min_elevation_deg, gso_min_elevation_deg,
        pfd_bw_correction_db, n_jobs, _angular_velocity_for_point,
        progress_label="WCG coarse:",
        strict_exclusion_zone=strict_exclusion_zone,
    )
    logger.info(f"  WCG coarse phase done: {time.perf_counter() - _t_coarse:.2f}s")

    # ── Phase 2: local refinement around the best candidate ──────────────
    # Important for masks with narrow windows near α ≈ 0: the coarse grid may
    # not sample the region where α changes rapidly densely enough.
    if best_result is not None and refinement_factor > 1:
        fine_step = phi_step_deg / refinement_factor
        radius = refinement_radius_factor * phi_step_deg
        theta_c = best_result.theta_deg
        phi_c   = best_result.phi_deg

        refine_points: list[tuple[float, float]] = []
        phi_r = max(fine_step, phi_c - radius)
        while phi_r <= min(phi_max_deg, phi_c + radius):
            theta_r = theta_c - radius
            while theta_r <= theta_c + radius:
                refine_points.append((theta_r, phi_r))
                theta_r += fine_step
            phi_r += fine_step

        logger.info(
            f"WCG search (refinement ×{refinement_factor}): {len(refine_points)} points "
            f"around (θ={theta_c:.2f}°, φ={phi_c:.2f}°), step={fine_step:.3f}°"
        )

        _t_refine = time.perf_counter()
        ref_result, ref_epfd, ref_ang_vel, ref_trail = _run_grid_search(
            refine_points, ngso_sat_eci, ngso_sat_vel_eci, t_s,
            pfd_mask, es_antenna, alpha0_deg, min_elevation_deg, gso_min_elevation_deg,
            pfd_bw_correction_db, n_jobs, _angular_velocity_for_point,
            progress_label="WCG refinement:",
            strict_exclusion_zone=strict_exclusion_zone,
        )
        logger.info(f"  WCG refinement phase done: {time.perf_counter() - _t_refine:.2f}s")
        trail.extend(ref_trail)

        if ref_result is not None and (
            ref_epfd > best_epfd + 0.1
            or (abs(ref_epfd - best_epfd) <= 0.1 and ref_ang_vel < best_ang_vel)
        ):
            best_result   = ref_result
            best_epfd     = ref_epfd
            best_ang_vel  = ref_ang_vel
            logger.info(
                f"  Refinement improved the WCG: EPFD {best_epfd:.2f} dBW "
                f"(α={best_result.alpha_deg:.3f}°)"
            )
        else:
            logger.info(f"  Refinement confirmed the coarse WCG (no improvement).")

    # Recompute details of the best point (gain, separate pfd) for precision and completeness
    # And compute the AGGREGATE EPFD at the WCG point (per S.1503-4 D.3.1.2)
    _t_post = time.perf_counter()
    if best_result:
        es_ecef = lla_to_ecef(best_result.es_lat_deg, best_result.es_lon_deg, 0.0)
        ngso_ecef = eci_to_ecef(ngso_sat_eci, t_s)
        _, gso_ecef, _ = compute_alpha_and_optimal_gso(
            es_ecef, ngso_ecef, best_result.es_lat_deg, best_result.es_lon_deg,
        )
        
        offaxis = compute_offaxis_angle(es_ecef, ngso_ecef, gso_ecef)
        theta_planar = None
        if es_antenna.requires_planar_angle:
            _, theta_planar = compute_offaxis_and_planar_angle(
                es_ecef, ngso_ecef, gso_ecef, best_result.es_lat_deg, best_result.es_lon_deg
            )
        g_rel_db = es_antenna.relative_gain(offaxis, theta_planar)
        pfd_db = best_result.epfd_dBW - g_rel_db
        
        best_result.offaxis_deg = offaxis
        best_result.es_gain_rel_dB = g_rel_db
        best_result.pfd_dBW = pfd_db
        best_result.search_trail = trail
        
        # --- Aggregate computation at the WCG instant ---
        # (At this stage, we use the full constellation to see the real impact)
        # Important: we need to know the full constellation.
        # Since search_wcg only receives ngso_sat_eci,
        # we leave the aggregate computation to the caller (main.py)
        # to avoid passing the huge list over IPC in multiprocessing.

        logger.info(f"  WCG trail: {len(trail)} points evaluated.")
        logger.info(f"  WCG post-processing phase: {time.perf_counter() - _t_post:.2f}s")

    return best_result


# =====================================================================
#  ITU-R S.1503-4 §D.3.1 — WCGA_Down (Analytical Algorithm)
#
#  Alternative to search_wcg. Faithfully implements the recommendation's
#  pseudocode: it iterates over satellite latitudes (point-mass model) and,
#  for each latitude, uses binary searches on the boundaries α = α₀ and
#  ε = ε₀, plus the regular grid in (θ, φ).
#
#  Function hierarchy (mirroring the recommendation's names):
#   search_wcg_s1503          ← WCGA_Down / GetWCGA_Down
#   _wcgd_calc_at_lat         ← WCGD_CalcAtLat
#   _wcgd_check_case          ← WCGD_CheckCase
#   _wcgd_check_alpha_phi_case← WCGD_CheckAlphaPhiCase
#   _wcgd_check_alpha_elev_case← WCGD_CheckAlphaElevCase
#   _wcgd_check_extreme_case  ← WCGD_CheckExtremeCase
#   _wcgd_get_delta_alpha     ← WCGD_GetDeltaAlpha
#   _wcgd_get_delta_elev      ← WCGD_GetDeltaElev
#   _wcgd_calc_phi_from_theta_elev ← WCGD_CalcPhiFromThetaElev
# =====================================================================

class _WCGState:
    """Mutable state shared among the WCGA subroutines.

    Ranking follows S.1503-4 §D.3.1.2 (WCGD_CheckCase):

        EPFDMargin = PFD + Grel(φ) − EPFDThreshold[latP]
        EPFDbin    = EPFDMargin / BinSize

    and the worst geometry is the one with the **highest** `EPFDMargin` in the
    0.1 dB bin, with ties broken by the **lowest** angular velocity (an
    approximation of "highest % of time at the same EPFD" — cf.
    `docs/fidelidade_s1503.md` §4.2).

    Implementation (important for correctly applying the bin rule):

    * ``bin_top_margin`` = **top of the bin** = highest `margin` seen so far.
      The tie window is always `[bin_top_margin − BIN_SIZE, bin_top_margin]`.
    * ``_bin_candidates`` = list of candidates that fall within the window. When
      a new point raises the top, candidates that leave the window are
      discarded. The **current winner** is always the candidate with the
      **lowest** `ang_vel` within the window (secondary tiebreaker: highest
      `margin`).
    * ``best_margin`` now refers to the ``margin`` of the current winner (it is
      no longer conflated with the top of the bin) — kept for backward compat.

    This design fixes the subtle bug where, in the previous implementation, a
    single ``best_margin`` field served a dual purpose (top of the bin +
    winner's margin). When the winner had a `margin` lower than the top
    already seen, the window became "shifted" and a subsequent point with a
    `margin` only slightly larger than the winner was treated as a "new
    absolute bin", defeating the legitimate winner even though it was within
    the same S.1503-4 window.
    """
    __slots__ = ("best_margin", "best_epfd", "best_ang_vel", "best_result",
                 "bin_top_margin", "_bin_candidates",
                 "trail", "trail_all",
                 "collect_all_points", "search_lat_deg",
                 "n_ok", "n_excl", "n_low_elev")

    # Defensive cap on the candidate list. In practice, few points converge
    # into a 0.1 dB bin; even in dense sweeps the count stays in the dozens.
    # The cap prevents pathological growth in degenerate scenarios (e.g. a
    # flat mask).
    BIN_CAND_MAX = 4096

    def __init__(self, collect_all_points: bool = False, search_lat_deg: float | None = None):
        self.best_margin: float = -math.inf
        self.best_epfd: float = -math.inf
        self.best_ang_vel: float = math.inf
        self.best_result: WCGResult | None = None
        # Top of the bin (separate from the winner).
        self.bin_top_margin: float = -math.inf
        # Each item is a tuple (margin, ang_vel, epfd, result). Kept unsorted;
        # the winner is recomputed on each arrival (the typical list is small,
        # O(10²) in the normal worst case).
        self._bin_candidates: list[tuple[float, float, float, WCGResult]] = []
        self.trail: list[WCGSearchPoint] = []
        self.trail_all: list[WCGSearchPoint] = []
        self.collect_all_points: bool = bool(collect_all_points)
        self.search_lat_deg: float | None = float(search_lat_deg) if search_lat_deg is not None else None
        # Lightweight counters — avoid accumulating millions of objects
        self.n_ok: int = 0
        self.n_excl: int = 0
        self.n_low_elev: int = 0

    BIN_SIZE = 0.1  # dB — rounding tolerance (S.1503-4)

    @property
    def n_total(self) -> int:
        return self.n_ok + self.n_excl + self.n_low_elev

    def _recompute_winner(self) -> None:
        """Recomputes the winner among ``self._bin_candidates`` per the
        S.1503-4 rule: **lowest** ``ang_vel`` within the window; secondary
        tiebreaker by the **highest** ``margin`` (closest to the top of the bin).
        """
        if not self._bin_candidates:
            self.best_margin = -math.inf
            self.best_epfd = -math.inf
            self.best_ang_vel = math.inf
            self.best_result = None
            return
        # min by (ang_vel, -margin): lowest ang_vel; on a tie, highest margin.
        m, av, ep, res = min(
            self._bin_candidates,
            key=lambda t: (t[1], -t[0]),
        )
        self.best_margin = m
        self.best_ang_vel = av
        self.best_epfd = ep
        self.best_result = res

    def _prune_candidates(self) -> None:
        """Remove candidates that left the window `[bin_top − BIN, bin_top]`
        after an update to the top of the bin."""
        thr = self.bin_top_margin - self.BIN_SIZE
        if self._bin_candidates and self._bin_candidates[0][0] < thr:
            # fast path when there is something to prune
            self._bin_candidates = [
                c for c in self._bin_candidates if c[0] >= thr
            ]
            return
        # A full scan is only needed when the first element remained but
        # others may have left (an uncommon case after a merge). Costs O(n).
        if any(c[0] < thr for c in self._bin_candidates):
            self._bin_candidates = [
                c for c in self._bin_candidates if c[0] >= thr
            ]

    def update(
        self,
        epfd: float,
        ang_vel: float,
        result: WCGResult,
        margin: float | None = None,
    ) -> bool:
        """Inserts a candidate point and returns True iff it becomes the
        current winner.

        When ``margin`` is not provided, uses ``epfd`` as a fallback
        (equivalent to `threshold ≡ 0` — legacy mode without latitude
        dependence).
        """
        if margin is None:
            margin = epfd

        # Outside the current bin (below): discard immediately.
        if margin < self.bin_top_margin - self.BIN_SIZE:
            return False

        if margin > self.bin_top_margin + self.BIN_SIZE:
            # New absolute bin: reset the window.
            self.bin_top_margin = margin
            self._bin_candidates = [(margin, ang_vel, epfd, result)]
            self.best_margin = margin
            self.best_ang_vel = ang_vel
            self.best_epfd = epfd
            self.best_result = result
            return True

        # Within the window: add, possibly raise the top, and prune.
        prev_winner_key = (
            (self.best_ang_vel, -self.best_margin)
            if self.best_result is not None
            else (math.inf, math.inf)
        )
        # Raise the top, if applicable.
        if margin > self.bin_top_margin:
            self.bin_top_margin = margin
        # Insert and prune.
        if len(self._bin_candidates) < self.BIN_CAND_MAX:
            self._bin_candidates.append((margin, ang_vel, epfd, result))
        else:
            # In degenerate scenarios: replace the worst candidate (highest
            # ang_vel) if the new one is better; otherwise discard.
            worst_i = max(
                range(len(self._bin_candidates)),
                key=lambda i: self._bin_candidates[i][1],
            )
            if ang_vel < self._bin_candidates[worst_i][1]:
                self._bin_candidates[worst_i] = (margin, ang_vel, epfd, result)
        self._prune_candidates()
        self._recompute_winner()
        new_winner_key = (self.best_ang_vel, -self.best_margin)
        return new_winner_key != prev_winner_key and (
            self.best_result is result  # the winner is the newcomer
        )

    def merge(self, other: "_WCGState") -> None:
        """Incorporates the result of another state (parallel merge)."""
        self.n_ok += other.n_ok
        self.n_excl += other.n_excl
        self.n_low_elev += other.n_low_elev
        self.trail.extend(other.trail)
        if self.collect_all_points:
            self.trail_all.extend(other.trail_all)
        if not other._bin_candidates:
            return
        # Merge the two lists, update the top, and recompute the winner.
        self.bin_top_margin = max(self.bin_top_margin, other.bin_top_margin)
        self._bin_candidates.extend(other._bin_candidates)
        if len(self._bin_candidates) > self.BIN_CAND_MAX:
            # Keep the BIN_CAND_MAX with the lowest ang_vel (these are the
            # winner candidates; those with higher ang_vel will never win
            # while the lower ones are in the window).
            self._bin_candidates.sort(key=lambda t: t[1])
            self._bin_candidates = self._bin_candidates[: self.BIN_CAND_MAX]
        self._prune_candidates()
        self._recompute_winner()

    def add_point(
        self,
        theta_deg: float,
        phi_deg: float,
        lat_deg: float,
        lon_deg: float,
        alpha_deg: float,
        elev_deg: float,
        epfd_dbw: float,
        status: str,
        gso_lon_deg: float | None = None,
    ) -> None:
        if not self.collect_all_points:
            return
        self.trail_all.append(
            WCGSearchPoint(
                theta_deg=theta_deg,
                phi_deg=phi_deg,
                search_lat_deg=self.search_lat_deg,
                es_lat_deg=lat_deg,
                es_lon_deg=lon_deg,
                alpha_deg=alpha_deg,
                elevation_deg=elev_deg,
                epfd_dBW=epfd_dbw,
                status=status,
                gso_lon_deg=gso_lon_deg,
            )
        )


def _is_wcg_candidate_margin(state: "_WCGState", margin_db: float) -> bool:
    """Returns True when the margin (EPFD − EPFDThreshold[lat]) enters the
    contention window for the best WCG.

    The window is `[bin_top − BIN_SIZE, +∞)` — **any** point that can still
    (a) define a new bin (``margin > bin_top + BIN``) or (b) fit the current
    bin (``margin ≥ bin_top − BIN``) needs to be evaluated. The previous test
    used ``state.best_margin`` as the reference, but ``best_margin`` is the
    **winner's** margin and may be below the top of the bin — in those cases
    legitimate points were incorrectly filtered out (cf. the D.3.1.2 bug-fix).
    """
    return margin_db >= state.bin_top_margin - state.BIN_SIZE


# Kept as a compatible alias: when the threshold is constant (or 0.0), the margin
# differs from the EPFD by a constant and the test above reduces to the prior rule.
_is_wcg_candidate_epfd = _is_wcg_candidate_margin


def _place_sat_at_lat(
    oe: "OrbitalElements",
    lat_deg: float,
) -> tuple[np.ndarray, np.ndarray] | None:
    """Places the satellite at the target latitude — point-mass model (S.1503-4 D.3.1.3.2).

    Circular orbit (e ≈ 0):
        Uses the analytical formula sin(ω+ν) = sin(lat)/sin(i).

    Elliptical orbit (e > 0):
        Uses a binary search in M ∈ [0, π], per S.1503-4 D.3.1.3.2:
        "the binary search can start with M = (0, π) and iterate from there."
        The geocentric latitude as a function of M is:
            φ(M) = arcsin(sin(i) · sin(ω + ν(M)))
        which is monotonic in M ∈ [0, π] when ω = ±π/2 (the typical case for
        elliptical NGSO systems).

    ECI position and velocity returned via elements_to_eci, which implements
    the D.3.1.3.2 formulas:
        r_sat = r_sat(cos ν P + sin ν Q)
        v_sat = √(μ/p)(−sin ν P + (e + cos ν) Q)
    through the perifocal frame (P, Q) per §D6.3.3.

    Returns (pos_eci_km, vel_eci_km/s) or None if the latitude is unreachable.
    """
    from .orbit_propagator import elements_to_eci, solve_kepler, OrbitalElements as OE

    i = oe.i
    lat_rad = math.radians(lat_deg)
    sin_i = math.sin(i)

    # ── Degenerate case: inclination ≈ 0 ─────────────────────────────────────
    if sin_i < 1e-9:
        if abs(lat_deg) > 0.001:
            return None
        M = 0.0
        oe_at = OE(a=oe.a, e=oe.e, i=oe.i, raan=oe.raan, omega=oe.omega, M=M)
        return elements_to_eci(oe_at)

    # ── Circular: analytical formula (D.3.1.3.2, paragraph 1) ────────────────
    if oe.e <= 1e-4:
        ratio = math.sin(lat_rad) / sin_i
        if abs(ratio) > 1.0 + 1e-9:
            return None
        ratio = max(-1.0, min(1.0, ratio))
        # ascending pass: ω + ν = arcsin(ratio)
        nu = math.asin(ratio) - oe.omega
        nu = nu % (2.0 * math.pi)
        # ν → E → M
        E = math.atan2(math.sqrt(1.0 - oe.e ** 2) * math.sin(nu),
                       oe.e + math.cos(nu))
        M = E - oe.e * math.sin(E)
        oe_at = OE(a=oe.a, e=oe.e, i=oe.i, raan=oe.raan, omega=oe.omega, M=M)
        return elements_to_eci(oe_at)

    # ── Elliptical: binary search in M ∈ [0, π] (D.3.1.3.2, paragraph 2) ─────
    # Geocentric latitude as a function of the mean anomaly M:
    #   φ(M) = arcsin(sin(i) · sin(ω + ν(M)))
    def _geocentric_lat(M_val: float) -> float:
        E_val = solve_kepler(M_val, oe.e)
        nu_val = math.atan2(
            math.sqrt(1.0 - oe.e ** 2) * math.sin(E_val),
            math.cos(E_val) - oe.e,
        )
        arg = max(-1.0, min(1.0, sin_i * math.sin(oe.omega + nu_val)))
        return math.asin(arg)

    lat_at_0   = _geocentric_lat(0.0)
    lat_at_pi  = _geocentric_lat(math.pi)
    lat_lo_val = min(lat_at_0, lat_at_pi)
    lat_hi_val = max(lat_at_0, lat_at_pi)

    if not (lat_lo_val - 1e-9 <= lat_rad <= lat_hi_val + 1e-9):
        return None  # latitude unreachable in the interval M ∈ [0, π]

    M_lo, M_hi = 0.0, math.pi
    # Orient the bisection so that f(M_lo) ≤ 0 ≤ f(M_hi)
    if lat_at_0 > lat_at_pi:
        M_lo, M_hi = math.pi, 0.0

    for _ in range(60):
        if abs(M_hi - M_lo) < 1e-10:
            break
        M_mid = (M_lo + M_hi) / 2.0
        f_lo  = _geocentric_lat(M_lo)  - lat_rad
        f_mid = _geocentric_lat(M_mid) - lat_rad
        if f_lo * f_mid <= 0.0:
            M_hi = M_mid
        else:
            M_lo = M_mid

    M = (M_lo + M_hi) / 2.0

    # Check for positive altitude at the solution found
    E_sol = solve_kepler(M, oe.e)
    r_sol = oe.a * (1.0 - oe.e * math.cos(E_sol))
    if r_sol - RE_KM < 0:
        return None

    oe_at = OE(a=oe.a, e=oe.e, i=oe.i, raan=oe.raan, omega=oe.omega, M=M)
    return elements_to_eci(oe_at)


def _calc_phi0(r_sat_km: float, min_elev_deg: float) -> float:
    """Maximum nadir angle φ₀ for the minimum elevation (S.1503-4 D.3.1.3.3).

    sin(φ₀) = (RE / r_sat) · cos(ε₀)
    """
    ratio = (RE_KM / r_sat_km) * math.cos(math.radians(min_elev_deg))
    return math.degrees(math.asin(max(-1.0, min(1.0, ratio))))


@njit(cache=True, fastmath=True)
def _ecef_to_lat_lon_deg_scalar(x: float, y: float, z: float) -> tuple[float, float]:
    """Scalar ECEF->(lat, lon) conversion to reduce allocation overhead.

    Spherical Earth model (S.1503-4 D6.1) — exactly the same math as
    ``coordinates.ecef_to_lla_batch``, so the scalar boundary cases and the
    batch grid path agree within ``WCG_ELEV_BOUNDARY_TOL_DEG``.
    """
    lon = math.atan2(y, x)
    p = math.sqrt(x * x + y * y)
    lat = math.atan2(z, p)
    return math.degrees(lat), math.degrees(lon)


@njit(cache=True, fastmath=True)
def _wcgd_ray_to_earth_from_context_scalar(
    theta_rad: float,
    phi_rad: float,
    sat_x: float,
    sat_y: float,
    sat_z: float,
    nx: float,
    ny: float,
    nz: float,
    px: float,
    py: float,
    pz: float,
    ex: float,
    ey: float,
    ez: float,
    c_term: float,
) -> tuple[bool, float, float, float, float, float]:
    """Scalar ray-Earth intersection using a pre-computed frame."""
    sp, cp = math.sin(phi_rad), math.cos(phi_rad)
    st, ct = math.sin(theta_rad), math.cos(theta_rad)
    lx = cp * nx + sp * (ct * px + st * ex)
    ly = cp * ny + sp * (ct * py + st * ey)
    lz = cp * nz + sp * (ct * pz + st * ez)
    n_l = math.sqrt(lx * lx + ly * ly + lz * lz)
    if n_l < 1e-12:
        return False, 0.0, 0.0, 0.0, 0.0, 0.0
    lx /= n_l
    ly /= n_l
    lz /= n_l

    b_term = 2.0 * (sat_x * lx + sat_y * ly + sat_z * lz)
    disc = b_term * b_term - 4.0 * c_term
    if disc < 0.0:
        return False, 0.0, 0.0, 0.0, 0.0, 0.0

    sq = math.sqrt(disc)
    t1 = (-b_term - sq) / 2.0
    t2 = (-b_term + sq) / 2.0
    if t1 > 0.0 and t2 > 0.0:
        t_hit = t1 if t1 < t2 else t2
    else:
        t_hit = t1 if t1 > t2 else t2
    if t_hit <= 0.0:
        return False, 0.0, 0.0, 0.0, 0.0, 0.0

    es_x = sat_x + t_hit * lx
    es_y = sat_y + t_hit * ly
    es_z = sat_z + t_hit * lz
    lat_deg, lon_deg = _ecef_to_lat_lon_deg_scalar(es_x, es_y, es_z)
    return True, es_x, es_y, es_z, lat_deg, lon_deg


def _wcgd_ray_to_earth_scalar(
    sat_x: float,
    sat_y: float,
    sat_z: float,
    theta_rad: float,
    phi_rad: float,
    ray_ctx: tuple[float, ...] | None = None,
) -> tuple[bool, float, float, float, float, float]:
    """Scalar intersection of the (θ, φ) ray with the Earth (same logic as _wcgd_ray_to_earth)."""
    if ray_ctx is None:
        ray_ctx = _build_wcgd_ray_context_from_xyz(sat_x, sat_y, sat_z)
        if ray_ctx is None:
            return False, 0.0, 0.0, 0.0, 0.0, 0.0
    return _wcgd_ray_to_earth_from_context_scalar(theta_rad, phi_rad, *ray_ctx)


def _wcgd_ray_to_earth(
    sat_ecef: np.ndarray,
    theta_rad: float,
    phi_rad: float,
    ray_ctx: tuple[float, ...] | None = None,
) -> tuple[np.ndarray | None, float, float]:
    """Casts a ray from the satellite in the (θ, φ) direction and finds the Earth intersection.

    Uses the same frame as theta_phi_to_es_and_gso (north_perp, east_dir).
    Returns (es_ecef, es_lat_deg, es_lon_deg) or (None, 0, 0) if there is no intersection.
    """
    ok, es_x, es_y, es_z, lat, lon = _wcgd_ray_to_earth_scalar(
        float(sat_ecef[0]), float(sat_ecef[1]), float(sat_ecef[2]), theta_rad, phi_rad, ray_ctx
    )
    if not ok:
        return None, 0.0, 0.0
    return np.array([es_x, es_y, es_z], dtype=float), lat, lon


def _wcgd_get_delta_alpha(
    sat_ecef: np.ndarray,
    theta_rad: float,
    phi_rad: float,
    sign: int,
    alpha0_deg: float,
    ray_ctx: tuple[float, ...] | None = None,
) -> float | None:
    """WCGD_GetDeltaAlpha: returns α(P) − sign·α₀.

    Returns None if the ray does not intersect the Earth.
    """
    ok, es_x, es_y, es_z, lat_p, lon_p = _wcgd_ray_to_earth_scalar(
        float(sat_ecef[0]), float(sat_ecef[1]), float(sat_ecef[2]), theta_rad, phi_rad, ray_ctx
    )
    if not ok:
        return None
    alpha = compute_alpha_angle_fast_components(
        es_x=es_x,
        es_y=es_y,
        es_z=es_z,
        ng_x=float(sat_ecef[0]),
        ng_y=float(sat_ecef[1]),
        ng_z=float(sat_ecef[2]),
        es_lat_deg=lat_p,
        es_lon_deg=lon_p,
    )
    return alpha - sign * alpha0_deg


def _wcgd_get_delta_elev(
    sat_ecef: np.ndarray,
    theta_rad: float,
    phi_rad: float,
    min_elevation_deg: float,
    ray_ctx: tuple[float, ...] | None = None,
) -> float | None:
    """WCGD_GetDeltaElev: returns ε(P) − ε₀."""
    ok, es_x, es_y, es_z, lat_p, lon_p = _wcgd_ray_to_earth_scalar(
        float(sat_ecef[0]), float(sat_ecef[1]), float(sat_ecef[2]), theta_rad, phi_rad, ray_ctx
    )
    if not ok:
        return None

    # Direct elevation (equivalent to topocentric_angles): sin(elev)=n·diff/|diff|
    lat_r = math.radians(lat_p)
    lon_r = math.radians(lon_p)
    nx = math.cos(lat_r) * math.cos(lon_r)
    ny = math.cos(lat_r) * math.sin(lon_r)
    nz = math.sin(lat_r)
    dx = float(sat_ecef[0]) - es_x
    dy = float(sat_ecef[1]) - es_y
    dz = float(sat_ecef[2]) - es_z
    rng = math.sqrt(dx * dx + dy * dy + dz * dz)
    if rng < 1e-12:
        return None
    sin_elev = (nx * dx + ny * dy + nz * dz) / rng
    sin_elev = max(-1.0, min(1.0, sin_elev))
    elev = math.degrees(math.asin(sin_elev))
    return elev - min_elevation_deg


def _bisect_theta(
    sat_ecef: np.ndarray,
    phi_rad: float,
    theta_lo: float,
    theta_hi: float,
    fn,          # callable(theta) → float | None
    tol: float = 1e-5,
) -> float | None:
    """Generic bisection in θ for fn(θ) = 0.

    Returns the θ root, or None if there is no zero crossing in the interval.
    """
    f_lo = fn(theta_lo)
    f_hi = fn(theta_hi)
    if f_lo is None or f_hi is None:
        return None
    if f_lo * f_hi > 0:
        return None  # no crossing
    for _ in range(60):
        if abs(theta_hi - theta_lo) < tol:
            break
        mid = (theta_lo + theta_hi) / 2.0
        f_mid = fn(mid)
        if f_mid is None:
            return None
        if f_lo * f_mid <= 0:
            theta_hi, f_hi = mid, f_mid
        else:
            theta_lo, f_lo = mid, f_mid
    return (theta_lo + theta_hi) / 2.0


def _wcgd_calc_phi_from_theta_elev(
    sat_ecef: np.ndarray,
    theta_rad: float,
    phi_max_rad: float,
    min_elevation_deg: float,
    ray_ctx: tuple[float, ...] | None = None,
    tol: float = 1e-5,
) -> float | None:
    """WCGD_CalcPhiFromThetaElev: bisection in φ for ε(P) = ε₀."""
    phi_lo = 1e-4
    phi_hi = phi_max_rad

    def f(phi):
        return _wcgd_get_delta_elev(sat_ecef, theta_rad, phi, min_elevation_deg, ray_ctx)

    f_lo, f_hi = f(phi_lo), f(phi_hi)
    if f_lo is None or f_hi is None:
        return phi_max_rad
    if f_lo * f_hi > 0:
        return phi_max_rad  # already outside the interval — use the maximum
    for _ in range(60):
        if abs(phi_hi - phi_lo) < tol:
            break
        mid = (phi_lo + phi_hi) / 2.0
        f_mid = f(mid)
        if f_mid is None:
            return None
        if f_lo * f_mid <= 0:
            phi_hi, f_hi = mid, f_mid
        else:
            phi_lo, f_lo = mid, f_mid
    return (phi_lo + phi_hi) / 2.0


def _wcgd_check_case(
    sat_ecef: np.ndarray,
    sat_eci: np.ndarray,
    sat_vel_eci: np.ndarray,
    t_s: float,
    theta_rad: float,
    phi_rad: float,
    pfd_mask: "PFDMask",
    es_antenna: "EarthStationAntenna",
    alpha0_deg: float,
    min_elevation_deg: float,
    gso_min_elevation_deg: float,
    pfd_bw_correction_db: float,
    state: _WCGState,
    es_lat_min: float,
    es_lat_max: float,
    subsat_lat_deg: float | None = None,
    subsat_lon_deg: float | None = None,
    ray_ctx: tuple[float, ...] | None = None,
    sat_local_frame: tuple[float, ...] | None = None,
    strict_exclusion_zone: bool = False,
    epfd_threshold_by_lat_fn: "Callable[[float], float] | None" = None,
) -> None:
    """WCGD_CheckCase: evaluates (θ, φ) and updates the state if it is a better WCG."""
    theta_deg = math.degrees(theta_rad)
    phi_deg = math.degrees(phi_rad)
    es_ecef, lat_p, lon_p = _wcgd_ray_to_earth(sat_ecef, theta_rad, phi_rad, ray_ctx)
    if es_ecef is None:
        state.add_point(theta_deg, phi_deg, 0.0, 0.0, 0.0, 0.0, -999.0, "invalid")
        return
    if abs(lat_p) > 81.2 or lat_p < es_lat_min or lat_p > es_lat_max:
        state.add_point(theta_deg, phi_deg, lat_p, lon_p, 0.0, 0.0, -999.0, "invalid")
        return

    elev = compute_elevation(es_ecef, sat_ecef, lat_p, lon_p)

    alpha, gso_ecef, gso_lon_opt = compute_alpha_and_optimal_gso(
        es_ecef, sat_ecef, es_lat_deg=lat_p, es_lon_deg=lon_p,
    )
    gso_elev = compute_elevation(es_ecef, gso_ecef, lat_p, lon_p)

    # S.1503-4 §D.3.1.2 WCGD_CheckCase store criterion (p.55):
    #   store if  (|α| ≥ α₀ AND el_nGSO ≥ ε₀ AND el_GSO ≥ ε_GSO)
    #             OR  G(φ) > min(Gmax − 30 dB, G(α₀))
    # The elevation / GSO-elevation conditions gate ONLY the first (AND) branch.
    # The gain OR-branch must still rescue near-boresight points even when the
    # GSO arc is below ε_GSO (e.g. a high-latitude ES, where ITU's worst case
    # lives) or the nGSO satellite is below ε₀. Treating el_GSO < ε_GSO as an
    # unconditional exclusion (the previous behaviour) dropped exactly those
    # high-latitude worst-case geometries.
    offaxis = compute_offaxis_angle(es_ecef, sat_ecef, gso_ecef)
    theta_planar = None
    if es_antenna.requires_planar_angle:
        _, theta_planar = compute_offaxis_and_planar_angle(
            es_ecef, sat_ecef, gso_ecef, lat_p, lon_p
        )
    and_branch = (
        abs(alpha) >= alpha0_deg
        and elev >= min_elevation_deg - WCG_ELEV_BOUNDARY_TOL_DEG
        and gso_elev >= gso_min_elevation_deg - WCG_ELEV_BOUNDARY_TOL_DEG
    )
    or_branch = s1503_or_condition_include(
        es_antenna, offaxis, alpha0_deg, theta_planar,
        disable_or_condition=strict_exclusion_zone,
    )
    if not (and_branch or or_branch):
        if (
            elev < min_elevation_deg - WCG_ELEV_BOUNDARY_TOL_DEG
            or gso_elev < gso_min_elevation_deg - WCG_ELEV_BOUNDARY_TOL_DEG
        ):
            state.n_low_elev += 1
            state.add_point(theta_deg, phi_deg, lat_p, lon_p, alpha, elev, -999.0, "low_elev")
        else:
            state.n_excl += 1
            state.add_point(theta_deg, phi_deg, lat_p, lon_p, alpha, elev, -999.0, "exclusion")
        return


    pfd_db = _compute_pfd_3d(
        pfd_mask=pfd_mask,
        alpha_deg=alpha,
        ngso_sat_eci=sat_eci,
        ngso_sat_vel_eci=sat_vel_eci,
        es_lon_deg=lon_p,
        t_s=t_s,
        es_lat_deg=lat_p,
        gso_ecef=gso_ecef,
        pfd_bw_correction_db=pfd_bw_correction_db,
        ngso_sat_ecef=sat_ecef,
        es_ecef_cached=es_ecef,
        subsat_lat_deg=subsat_lat_deg,
        subsat_lon_deg=subsat_lon_deg,
        sat_local_frame=sat_local_frame,
        gso_lon_deg=gso_lon_opt,
    )

    if offaxis is None:
        offaxis = compute_offaxis_angle(es_ecef, sat_ecef, gso_ecef)
    if theta_planar is None and es_antenna.requires_planar_angle:
        _, theta_planar = compute_offaxis_and_planar_angle(
            es_ecef, sat_ecef, gso_ecef, lat_p, lon_p
        )
    g_rel_db = es_antenna.relative_gain(offaxis, theta_planar)
    epfd_db = pfd_db + g_rel_db
    # EPFDThreshold[lat] (S.1503 §D.3.1.2). When not provided, threshold=0
    # and the margin coincides with the absolute EPFD (legacy behavior).
    threshold_db = float(epfd_threshold_by_lat_fn(lat_p)) if epfd_threshold_by_lat_fn is not None else 0.0
    margin_db = epfd_db - threshold_db

    state.n_ok += 1
    state.add_point(
        theta_deg, phi_deg, lat_p, lon_p, alpha, elev, epfd_db, "ok",
        gso_lon_deg=gso_lon_opt,
    )

    # Only compute angular velocity / full object if the point contends for the WCG.
    if not _is_wcg_candidate_margin(state, margin_db):
        return

    sat_vel_ecef = eci_vel_to_ecef(sat_vel_eci, sat_ecef, t_s)
    ang_vel = compute_angular_velocity(es_ecef, sat_ecef, sat_vel_ecef)

    # Discard strictly dominated points: ang_vel ≥ winner AND margin ≤ winner
    # ⇒ it will never win (if the winner leaves the window when the top rises,
    # this point also leaves — it has margin ≤ winner_margin, which by
    # hypothesis is outside).
    if (
        state.best_result is not None
        and ang_vel >= state.best_ang_vel
        and margin_db <= state.best_margin
    ):
        return

    result = WCGResult(
        theta_deg=theta_deg, phi_deg=phi_deg,
        es_lat_deg=lat_p, es_lon_deg=lon_p, gso_lon_deg=gso_lon_opt,
        es_lat_nominal=lat_p, es_lon_nominal=lon_p, gso_lon_nominal=gso_lon_opt,
        alpha_deg=alpha, offaxis_deg=offaxis,
        pfd_dBW=pfd_db, es_gain_rel_dB=g_rel_db,
        epfd_dBW=epfd_db, elevation_deg=elev,
        es_ecef_exact=es_ecef.copy(),
        angular_velocity_deg_s=ang_vel,
        planar_angle_deg=float(theta_planar) if theta_planar is not None else None,
        ref_sat_eci=sat_eci.copy(),
    )
    # Store in the trail only when it improves the WCG (trail always ≤ 1 entry per state)
    if state.update(epfd_db, ang_vel, result, margin=margin_db):
        state.trail = [WCGSearchPoint(
            theta_deg, phi_deg,
            lat_p, lon_p, alpha, elev, epfd_db, "ok",
            search_lat_deg=state.search_lat_deg,
            gso_lon_deg=gso_lon_opt,
        )]


def _wcgd_check_case_batch(
    sat_ecef: np.ndarray,
    sat_eci: np.ndarray,
    sat_vel_eci: np.ndarray,
    sat_vel_ecef: np.ndarray,
    t_s: float,
    theta_vals_rad: np.ndarray,
    phi_rad: float,
    pfd_mask: "PFDMask",
    es_antenna: "EarthStationAntenna",
    alpha0_deg: float,
    min_elevation_deg: float,
    gso_min_elevation_deg: float,
    pfd_bw_correction_db: float,
    state: _WCGState,
    es_lat_min: float,
    es_lat_max: float,
    subsat_lat_deg: float | None = None,
    subsat_lon_deg: float | None = None,
    strict_exclusion_zone: bool = False,
    epfd_threshold_by_lat_fn: "Callable[[float], float] | None" = None,
    sat_local_geom: tuple | None = None,
) -> None:
    """Evaluates multiple points (fixed θ, φ) in batch to reduce Python overhead.

    Reuses pre-computations from the caller (`_wcgd_calc_at_lat`):
      ``sat_local_geom`` = (nadir, north_perp, east, sat_norm2, C_term, sat_local_frame)
      ``subsat_lat_deg`` / ``subsat_lon_deg`` = sub-satellite point
    When not provided, they are computed locally (compatibility).
    """
    if theta_vals_rad.size == 0:
        return
    phi_deg = math.degrees(phi_rad)
    theta_vals_deg_all = np.degrees(theta_vals_rad)

    if subsat_lat_deg is None or subsat_lon_deg is None:
        subsat_lat_deg, subsat_lon_deg = sub_satellite_point(sat_eci, t_s)

    if sat_local_geom is None:
        # Fallback: old caller without pre-computation. Maintains compatibility.
        sat_local_geom = _build_wcgd_batch_geom(sat_ecef)
    nadir, north_perp, east, sat_norm2, C, sat_local_frame_pre = sat_local_geom

    sp = math.sin(phi_rad)
    cp = math.cos(phi_rad)
    st = np.sin(theta_vals_rad)
    ct = np.cos(theta_vals_rad)
    look = cp * nadir + sp * (ct[:, None] * north_perp + st[:, None] * east)

    B = 2.0 * (look @ sat_ecef)
    disc = B * B - 4.0 * C
    valid_disc = disc >= 0.0
    if state.collect_all_points and np.any(~valid_disc):
        for idx in np.nonzero(~valid_disc)[0]:
            state.add_point(float(theta_vals_deg_all[idx]), phi_deg, 0.0, 0.0, 0.0, 0.0, -999.0, "invalid")
    if not np.any(valid_disc):
        return

    sq = np.zeros_like(disc)
    sq[valid_disc] = np.sqrt(disc[valid_disc])
    t1 = (-B - sq) / 2.0
    t2 = (-B + sq) / 2.0
    t_hit = np.where((t1 > 0.0) & (t2 > 0.0), np.minimum(t1, t2), np.maximum(t1, t2))
    valid_hit = valid_disc & (t_hit > 0.0)
    if state.collect_all_points and np.any(valid_disc & ~valid_hit):
        for idx in np.nonzero(valid_disc & ~valid_hit)[0]:
            state.add_point(float(theta_vals_deg_all[idx]), phi_deg, 0.0, 0.0, 0.0, 0.0, -999.0, "invalid")
    valid = valid_hit
    if not np.any(valid):
        return

    theta_idx = np.nonzero(valid)[0]
    es_ecef_all = sat_ecef + t_hit[theta_idx, None] * look[theta_idx]

    # Batch ECEF->LLA conversion (spherical, S.1503-4 D6.1) — same model as
    # the scalar kernel `_ecef_to_lat_lon_deg_scalar` used by the boundary cases.
    lat_all, lon_all, _ = ecef_to_lla_batch(es_ecef_all)
    keep_geo = np.zeros(len(theta_idx), dtype=bool)
    for i in range(len(theta_idx)):
        lat_p = float(lat_all[i])
        lon_p = float(lon_all[i])
        if abs(lat_p) <= 81.2 and es_lat_min <= lat_p <= es_lat_max:
            keep_geo[i] = True
        elif state.collect_all_points:
            state.add_point(
                float(theta_vals_deg_all[theta_idx[i]]),
                phi_deg,
                float(lat_p),
                float(lon_p),
                0.0,
                0.0,
                -999.0,
                "invalid",
            )

    if not np.any(keep_geo):
        return

    theta_idx = theta_idx[keep_geo]
    es_ecef_all = es_ecef_all[keep_geo]
    lat_all = lat_all[keep_geo]
    lon_all = lon_all[keep_geo]

    # Vectorized elevation (geometric equivalent of compute_elevation)
    lat_rad = np.radians(lat_all)
    lon_rad = np.radians(lon_all)
    surface_normal = np.column_stack((
        np.cos(lat_rad) * np.cos(lon_rad),
        np.cos(lat_rad) * np.sin(lon_rad),
        np.sin(lat_rad),
    ))
    diff_sat = sat_ecef - es_ecef_all
    ranges = np.linalg.norm(diff_sat, axis=1)
    safe_ranges = np.where(ranges > 1e-12, ranges, 1.0)
    sin_elev = np.einsum("ij,ij->i", surface_normal, diff_sat) / safe_ranges
    np.clip(sin_elev, -1.0, 1.0, out=sin_elev)
    elev_all = np.degrees(np.arcsin(sin_elev))

    theta_vals_deg = np.degrees(theta_vals_rad[theta_idx])

    # α and the GSO-arc point that minimises α, for EVERY geo-valid ES point
    # (no early elevation pruning — the OR-branch below may still keep low-ε /
    # low-GSO-elevation points).
    alpha_all, gso_ecef_opt = compute_alpha_and_optimal_gso_multi_es_batch(
        es_ecef_all, sat_ecef, lat_all, lon_all, step_deg=1.0,
    )

    # GSO elevation at the point on the arc that minimizes α (S.1503)
    diff_gso = gso_ecef_opt - es_ecef_all
    gso_ranges = np.linalg.norm(diff_gso, axis=1)
    safe_gso_ranges = np.where(gso_ranges > 1e-12, gso_ranges, 1.0)
    sin_gso_elev = np.einsum("ij,ij->i", surface_normal, diff_gso) / safe_gso_ranges
    np.clip(sin_gso_elev, -1.0, 1.0, out=sin_gso_elev)
    gso_elev_all = np.degrees(np.arcsin(sin_gso_elev))

    offaxis_all = _compute_offaxis_angle_batch_gso(es_ecef_all, sat_ecef, gso_ecef_opt)
    theta_planar_all = None
    if es_antenna.requires_planar_angle:
        theta_planar_all = compute_offaxis_and_planar_angle_batch(
            es_ecef_all,
            sat_ecef.reshape(1, 3),
            gso_ecef_opt,
            lat_all,
            lon_all,
        )[1]

    # S.1503-4 §D.3.1.2 WCGD_CheckCase store criterion (p.55):
    #   store if  (|α| ≥ α₀ AND el_nGSO ≥ ε₀ AND el_GSO ≥ ε_GSO)
    #             OR  G(φ) > min(Gmax − 30 dB, G(α₀))
    # The elevation conditions gate ONLY the AND-branch; the gain OR-branch
    # rescues near-boresight points even when el_GSO < ε_GSO (high-latitude ES,
    # where ITU's worst case lives) or el_nGSO < ε₀. Previously el_nGSO/el_GSO
    # were applied as unconditional exclusions BEFORE the OR-branch, which
    # dropped the high-latitude worst-case geometry.
    elev_ok = elev_all >= min_elevation_deg - WCG_ELEV_BOUNDARY_TOL_DEG
    gso_elev_ok = gso_elev_all >= gso_min_elevation_deg - WCG_ELEV_BOUNDARY_TOL_DEG
    and_branch = (np.abs(alpha_all) >= alpha0_deg) & elev_ok & gso_elev_ok
    if strict_exclusion_zone:
        # disable_or_condition: GRX(φ) > min(−30, GRX(α₀)) ≡ false (no gain rescue).
        or_branch = np.zeros(len(alpha_all), dtype=bool)
    else:
        # GRX_rel(φ) > min(−30, GRX_rel(α₀)), GRX_rel(α₀) at the same planar θ as
        # the point (BO.1443 / 2D patterns); S.1428 depends only on |φ|.
        n_all = len(alpha_all)
        alpha0_arr = np.full(n_all, alpha0_deg, dtype=float)
        g_rel_at_a0 = _relative_gain_batch(es_antenna, alpha0_arr, theta_planar_all)
        g_rel_thr = np.minimum(-30.0, g_rel_at_a0)
        g_rel_off = _relative_gain_batch(es_antenna, offaxis_all, theta_planar_all)
        or_branch = g_rel_off > g_rel_thr
    keep_mask = and_branch | or_branch

    # Statistics (+ optional trail) for the dropped points. low_elev when the
    # drop is due to elevation/GSO-elevation; otherwise an angular-zone exclusion.
    drop_mask = ~keep_mask
    if np.any(drop_mask):
        low_elev_drop = drop_mask & (~elev_ok | ~gso_elev_ok)
        excl_drop = drop_mask & ~low_elev_drop
        state.n_low_elev += int(np.count_nonzero(low_elev_drop))
        state.n_excl += int(np.count_nonzero(excl_drop))
        if state.collect_all_points:
            for idx in np.nonzero(low_elev_drop)[0]:
                state.add_point(
                    float(theta_vals_deg[idx]), phi_deg,
                    float(lat_all[idx]), float(lon_all[idx]),
                    float(alpha_all[idx]),
                    float(min(elev_all[idx], gso_elev_all[idx])),
                    -999.0, "low_elev",
                )
            for idx in np.nonzero(excl_drop)[0]:
                state.add_point(
                    float(theta_vals_deg[idx]), phi_deg,
                    float(lat_all[idx]), float(lon_all[idx]),
                    float(alpha_all[idx]), float(elev_all[idx]),
                    -999.0, "exclusion",
                )

    if not np.any(keep_mask):
        return

    kept_idx = np.nonzero(keep_mask)[0]

    es_ecef_kept = es_ecef_all[kept_idx]
    lat_kept = lat_all[kept_idx]
    lon_kept = lon_all[kept_idx]
    elev_kept = elev_all[kept_idx]
    theta_kept_deg = theta_vals_deg[kept_idx]
    alpha_kept = alpha_all[kept_idx]
    offaxis_kept = offaxis_all[kept_idx]
    gso_ecef_kept = gso_ecef_opt[kept_idx]
    gso_lon_kept = np.degrees(np.arctan2(gso_ecef_kept[:, 1], gso_ecef_kept[:, 0]))
    theta_planar_kept = None if theta_planar_all is None else theta_planar_all[kept_idx]

    # Reuse the local frame pre-computed by `_wcgd_calc_at_lat`.
    sat_local_frame = sat_local_frame_pre

    # Vectorized fast path: masks with `get_pfd_batch` that depend only on
    # (alpha[, sub-sat lat, ΔLon]) — covers the common S.1503 formats:
    #   * alpha_deltaLongitude (3D XML);
    #   * 1D (CSV alpha→PFD);
    #   * azimuth_elevation (3D XML in the satellite's local frame).
    n_pts = len(kept_idx)
    pfd_kept: np.ndarray
    mask_type = getattr(pfd_mask, "mask_type", "")
    mask_dim = int(getattr(pfd_mask, "_dim", 3))

    if mask_dim == 1:
        # 1D: depends only on |alpha|. The lat/dlon arguments are ignored,
        # but we pass arrays of the same size to preserve the signature.
        zeros = np.zeros(n_pts, dtype=float)
        pfd_kept = pfd_mask.get_pfd_batch(alpha_kept, zeros, zeros) + pfd_bw_correction_db
    elif (
        mask_type == "alpha_deltaLongitude"
        and subsat_lat_deg is not None
        and subsat_lon_deg is not None
    ):
        lat_q = np.full(n_pts, subsat_lat_deg, dtype=float)
        dlon_q = delta_longitude_s1503_deg(gso_lon_kept, subsat_lon_deg)
        pfd_kept = pfd_mask.get_pfd_batch(alpha_kept, lat_q, dlon_q) + pfd_bw_correction_db
    elif (
        mask_type == "azimuth_elevation"
        and subsat_lat_deg is not None
        and sat_local_frame is not None
    ):
        # Azimuth/elevation in the satellite's local geographic frame.
        # The projection is purely geometric — we vectorize by removing the loop.
        az_b, el_c = _compute_mask_az_el_from_frame_batch(
            es_ecef_kept, sat_ecef, sat_local_frame
        )
        valid_az = ~np.isnan(az_b)
        lat_q = np.full(n_pts, subsat_lat_deg, dtype=float)
        pfd_kept = np.full(n_pts, -1000.0, dtype=float)
        if np.any(valid_az):
            pfd_kept[valid_az] = pfd_mask.get_pfd_batch(
                az_b[valid_az], lat_q[valid_az], el_c[valid_az]
            )
        pfd_kept += pfd_bw_correction_db
    else:
        # Fallback: mask not supported by batch — Python loop (rare).
        pfd_kept = np.empty(n_pts, dtype=float)
        for idx_j, j in enumerate(kept_idx):
            lon_p = float(lon_all[j])
            pfd_kept[idx_j] = _compute_pfd_3d(
                pfd_mask=pfd_mask,
                alpha_deg=float(alpha_all[j]),
                ngso_sat_eci=sat_eci,
                ngso_sat_vel_eci=sat_vel_eci,
                es_lon_deg=lon_p,
                t_s=t_s,
                es_lat_deg=float(lat_all[j]),
                gso_ecef=gso_ecef_opt[j],
                pfd_bw_correction_db=pfd_bw_correction_db,
                ngso_sat_ecef=sat_ecef,
                es_ecef_cached=es_ecef_all[j],
                subsat_lat_deg=subsat_lat_deg,
                subsat_lon_deg=subsat_lon_deg,
                sat_local_frame=sat_local_frame,
                gso_lon_deg=float(gso_lon_kept[idx_j]),
            )

    g_rel_kept = _relative_gain_batch(es_antenna, offaxis_kept, theta_planar_kept)
    epfd_kept = pfd_kept + g_rel_kept
    # Vectorized EPFDThreshold[lat] (S.1503 §D.3.1.2). Without a callable: threshold=0
    # and margin==epfd (legacy behavior).
    if epfd_threshold_by_lat_fn is not None:
        threshold_kept = np.asarray(epfd_threshold_by_lat_fn(lat_kept), dtype=float)
        margin_kept = epfd_kept - threshold_kept
    else:
        threshold_kept = None  # type: ignore[assignment]
        margin_kept = epfd_kept
    # Candidates: points that fit the current bin window OR can form a new
    # bin. Uses `bin_top_margin` (the real top) — not `best_margin` (the
    # winner's margin, which may be below the top).
    candidate_floor = max(
        state.bin_top_margin - state.BIN_SIZE,
        float(np.max(margin_kept)) - state.BIN_SIZE,
    )
    candidate_idx = np.nonzero(margin_kept >= candidate_floor)[0]

    # Count accepted points in batch (add_point only records in
    # collect_all_points mode; outside it the old loop was pure Python overhead).
    n_kept = int(len(kept_idx))
    state.n_ok += n_kept
    if state.collect_all_points and n_kept > 0:
        for idx_j in range(n_kept):
            state.add_point(
                float(theta_kept_deg[idx_j]),
                phi_deg,
                float(lat_kept[idx_j]),
                float(lon_kept[idx_j]),
                float(alpha_kept[idx_j]),
                float(elev_kept[idx_j]),
                float(epfd_kept[idx_j]),
                "ok",
                gso_lon_deg=float(gso_lon_kept[idx_j]),
            )

    for idx_j in candidate_idx:
        epfd_db = float(epfd_kept[idx_j])
        margin_db = float(margin_kept[idx_j])
        if not _is_wcg_candidate_margin(state, margin_db):
            continue

        es_ecef = es_ecef_kept[idx_j]
        lat_p = float(lat_kept[idx_j])
        lon_p = float(lon_kept[idx_j])
        elev = float(elev_kept[idx_j])
        alpha = float(alpha_kept[idx_j])
        offaxis = float(offaxis_kept[idx_j])
        pfd_db = float(pfd_kept[idx_j])
        g_rel_db = float(g_rel_kept[idx_j])
        ang_vel = compute_angular_velocity(es_ecef, sat_ecef, sat_vel_ecef)
        # Discard strictly dominated points (worse ang_vel AND margin lower
        # than the winner) — they will never win.
        if (
            state.best_result is not None
            and ang_vel >= state.best_ang_vel
            and margin_db <= state.best_margin
        ):
            continue

        gso_lon_p = float(gso_lon_kept[idx_j])
        tp = None if theta_planar_kept is None else float(theta_planar_kept[idx_j])
        result = WCGResult(
            theta_deg=float(theta_kept_deg[idx_j]), phi_deg=phi_deg,
            es_lat_deg=lat_p, es_lon_deg=lon_p, gso_lon_deg=gso_lon_p,
            es_lat_nominal=lat_p, es_lon_nominal=lon_p, gso_lon_nominal=gso_lon_p,
            alpha_deg=alpha, offaxis_deg=offaxis,
            pfd_dBW=pfd_db, es_gain_rel_dB=g_rel_db,
            epfd_dBW=epfd_db, elevation_deg=elev,
            es_ecef_exact=es_ecef.copy(),
            angular_velocity_deg_s=ang_vel,
            planar_angle_deg=tp,
            ref_sat_eci=sat_eci.copy(),
        )
        if state.update(epfd_db, ang_vel, result, margin=margin_db):
            state.trail = [WCGSearchPoint(
                float(theta_kept_deg[idx_j]), phi_deg,
                lat_p, lon_p, alpha, elev, epfd_db, "ok",
                search_lat_deg=state.search_lat_deg,
                gso_lon_deg=gso_lon_p,
            )]


def _wcgd_check_alpha_phi_case(
    sat_ecef: np.ndarray,
    sat_eci: np.ndarray,
    sat_vel_eci: np.ndarray,
    t_s: float,
    phi_rad: float,
    phi_max_rad: float,
    sign: int,
    side: str,          # "RHS" or "LHS"
    alpha0_deg: float,
    min_elevation_deg: float,
    gso_min_elevation_deg: float,
    pfd_mask: "PFDMask",
    es_antenna: "EarthStationAntenna",
    pfd_bw_correction_db: float,
    state: _WCGState,
    es_lat_min: float,
    es_lat_max: float,
    subsat_lat_deg: float | None = None,
    subsat_lon_deg: float | None = None,
    ray_ctx: tuple[float, ...] | None = None,
    sat_local_frame: tuple[float, ...] | None = None,
    strict_exclusion_zone: bool = False,
    epfd_threshold_by_lat_fn: "Callable[[float], float] | None" = None,
) -> None:
    """WCGD_CheckAlphaPhiCase: binary search in θ for α(P) = sign·α₀.

    Balanced partition of the circle (S.1503-4 §D.3.1.3.4, "left/right side"):
      RHS = **east** side  → θ_S1503 ∈ [−π/2, +π/2]  →  θ_ours ∈ [ 0, +π]
      LHS = **west** side  → θ_S1503 ∈ [+π/2, +3π/2] →  θ_ours ∈ [−π,  0]
    Each half spans π and their union covers the full circle. In symmetric
    mode (RHS only) the interval coincides exactly with the grid θ ∈ [0, π],
    avoiding the redundant search in the west and multiple roots in the same half.
    """
    theta_lo = 0.0        if side == "RHS" else -math.pi
    theta_hi = math.pi    if side == "RHS" else  0.0

    def f(t):
        return _wcgd_get_delta_alpha(sat_ecef, t, phi_rad, sign, alpha0_deg, ray_ctx)

    theta_root = _bisect_theta(sat_ecef, phi_rad, theta_lo, theta_hi, f)
    if theta_root is not None:
        _wcgd_check_case(
            sat_ecef, sat_eci, sat_vel_eci, t_s,
            theta_root, phi_rad,
            pfd_mask, es_antenna, alpha0_deg, min_elevation_deg, gso_min_elevation_deg,
            pfd_bw_correction_db, state, es_lat_min, es_lat_max,
            subsat_lat_deg=subsat_lat_deg, subsat_lon_deg=subsat_lon_deg,
            ray_ctx=ray_ctx, sat_local_frame=sat_local_frame,
            strict_exclusion_zone=strict_exclusion_zone,
            epfd_threshold_by_lat_fn=epfd_threshold_by_lat_fn,
        )


def _wcgd_check_alpha_elev_case(
    sat_ecef: np.ndarray,
    sat_eci: np.ndarray,
    sat_vel_eci: np.ndarray,
    t_s: float,
    phi_max_rad: float,
    sign: int,
    side: str,
    alpha0_deg: float,
    min_elevation_deg: float,
    gso_min_elevation_deg: float,
    pfd_mask: "PFDMask",
    es_antenna: "EarthStationAntenna",
    pfd_bw_correction_db: float,
    state: _WCGState,
    es_lat_min: float,
    es_lat_max: float,
    subsat_lat_deg: float | None = None,
    subsat_lon_deg: float | None = None,
    ray_ctx: tuple[float, ...] | None = None,
    sat_local_frame: tuple[float, ...] | None = None,
    strict_exclusion_zone: bool = False,
    epfd_threshold_by_lat_fn: "Callable[[float], float] | None" = None,
) -> None:
    """WCGD_CheckAlphaElevCase: bisection in θ for the elevation boundary + α=sign·α₀.

    Same balanced RHS/LHS partition as WCGD_CheckAlphaPhiCase
    (RHS=east=[0, +π]; LHS=west=[−π, 0]).
    """
    theta_lo = 0.0        if side == "RHS" else -math.pi
    theta_hi = math.pi    if side == "RHS" else  0.0

    def f(theta):
        phi = _wcgd_calc_phi_from_theta_elev(
            sat_ecef, theta, phi_max_rad, min_elevation_deg, ray_ctx
        )
        if phi is None:
            return None
        return _wcgd_get_delta_alpha(sat_ecef, theta, phi, sign, alpha0_deg, ray_ctx)

    theta_root = _bisect_theta(sat_ecef, phi_max_rad, theta_lo, theta_hi, f)
    if theta_root is not None:
        phi_root = _wcgd_calc_phi_from_theta_elev(
            sat_ecef, theta_root, phi_max_rad, min_elevation_deg, ray_ctx)
        if phi_root is not None:
            _wcgd_check_case(
                sat_ecef, sat_eci, sat_vel_eci, t_s,
                theta_root, phi_root,
                pfd_mask, es_antenna, alpha0_deg, min_elevation_deg, gso_min_elevation_deg,
                pfd_bw_correction_db, state, es_lat_min, es_lat_max,
                subsat_lat_deg=subsat_lat_deg, subsat_lon_deg=subsat_lon_deg,
                ray_ctx=ray_ctx, sat_local_frame=sat_local_frame,
                strict_exclusion_zone=strict_exclusion_zone,
                epfd_threshold_by_lat_fn=epfd_threshold_by_lat_fn,
            )


def _wcgd_calc_at_lat(
    oe: "OrbitalElements",
    lat_deg: float,
    t_s: float,
    pfd_mask: "PFDMask",
    es_antenna: "EarthStationAntenna",
    alpha0_deg: float,
    min_elevation_deg: float,
    gso_min_elevation_deg: float,
    pfd_bw_correction_db: float,
    step_size_deg: float,
    symmetric_mask: bool,
    es_lat_min: float,
    es_lat_max: float,
    collect_all_points: bool = False,
    strict_exclusion_zone: bool = False,
    epfd_threshold_by_lat_fn: "Callable[[float], float] | None" = None,
) -> _WCGState:
    """WCGD_CalcAtLat: sweeps all (θ, φ) points for the satellite at the given latitude.

    Returns a local _WCGState (no shared state → can run in parallel).
    """
    gmst0_env = os.environ.get("WCG_GMST0_DEG")
    if gmst0_env is not None:
        try:
            set_earth_rotation_initial_deg(float(gmst0_env))
        except ValueError:
            pass

    state = _WCGState(collect_all_points=collect_all_points, search_lat_deg=lat_deg)

    sat = _place_sat_at_lat(oe, lat_deg)
    if sat is None:
        return state
    sat_eci, sat_vel_eci = sat
    sat_ecef = eci_to_ecef(sat_eci, t_s)
    sat_vel_ecef = eci_vel_to_ecef(sat_vel_eci, sat_ecef, t_s)
    subsat_lat_deg, subsat_lon_deg = sub_satellite_point(sat_eci, t_s)
    ray_ctx = _build_wcgd_ray_context(sat_ecef)
    # Pre-compute all sat-local geometry ONCE per satellite (same `sat_ecef`
    # across all φ iterations): nadir/north/east + frame.
    sat_local_geom = _build_wcgd_batch_geom(sat_ecef)
    sat_local_frame = sat_local_geom[5]

    r_sat = float(np.linalg.norm(sat_ecef))
    if r_sat - RE_KM < 100.0:
        return state  # invalid altitude

    phi0_deg = _calc_phi0(r_sat, min_elevation_deg)
    phi0_rad = math.radians(phi0_deg)

    # Special case (θ=0, φ=0) — nadir
    _wcgd_check_case(sat_ecef, sat_eci, sat_vel_eci, t_s, 0.0, 0.0,
                     pfd_mask, es_antenna, alpha0_deg, min_elevation_deg, gso_min_elevation_deg,
                     pfd_bw_correction_db, state, es_lat_min, es_lat_max,
                     subsat_lat_deg=subsat_lat_deg, subsat_lon_deg=subsat_lon_deg,
                     ray_ctx=ray_ctx, sat_local_frame=sat_local_frame,
                     strict_exclusion_zone=strict_exclusion_zone,
                     epfd_threshold_by_lat_fn=epfd_threshold_by_lat_fn)

    # S.1503-4 §D.3.1.3.4: NumPhiSteps = RoundUp(φ0/StepSize); PhiStepSize = φ0/NumPhiSteps
    num_phi_steps = max(1, math.ceil(phi0_deg / step_size_deg))
    phi_step_rad = phi0_rad / num_phi_steps
    phi_step_deg = phi0_deg / num_phi_steps  # = PhiStepSize (degrees)
    theta_grid_cache: dict[int, np.ndarray] = {}

    phi_rad = phi_step_rad
    while phi_rad <= phi0_rad + 1e-9:
        phi_deg_v = math.degrees(phi_rad)

        # θ steps: NumThetaSteps = max(16, RoundUp(2π·φ / PhiStepSize))  (S.1503-4 §D.3.1.3.4)
        # We keep the denominator equal to PhiStepSize (not StepSize) to reproduce
        # the pseudocode literally when φ0 is not an integer multiple of StepSize.
        # S.1503-4 Figure 14: θ_S1503=0 = azimuth = east (cross-track).
        # In our frame θ_ours = π/2 − θ_S1503; the symmetric S.1503 interval
        # [−π/2, +π/2] (south→east→north) maps to [0, π] (north→east→south).
        # Only the symmetric grid is rotated 90°; the full circle stays unchanged.
        num_theta = max(16, math.ceil(2.0 * math.pi * phi_deg_v / phi_step_deg))
        theta_min = 0.0          if symmetric_mask else -0.5 * math.pi
        theta_max = math.pi      if symmetric_mask else  1.5 * math.pi
        theta_vals = theta_grid_cache.get(num_theta)
        if theta_vals is None:
            theta_step = (theta_max - theta_min) / num_theta
            theta_vals = theta_min + theta_step * np.arange(num_theta + 1, dtype=float)
            theta_grid_cache[num_theta] = theta_vals
        _wcgd_check_case_batch(
            sat_ecef=sat_ecef,
            sat_eci=sat_eci,
            sat_vel_eci=sat_vel_eci,
            sat_vel_ecef=sat_vel_ecef,
            t_s=t_s,
            theta_vals_rad=theta_vals,
            phi_rad=phi_rad,
            pfd_mask=pfd_mask,
            es_antenna=es_antenna,
            alpha0_deg=alpha0_deg,
            min_elevation_deg=min_elevation_deg,
            gso_min_elevation_deg=gso_min_elevation_deg,
            pfd_bw_correction_db=pfd_bw_correction_db,
            state=state,
            es_lat_min=es_lat_min,
            es_lat_max=es_lat_max,
            subsat_lat_deg=subsat_lat_deg,
            subsat_lon_deg=subsat_lon_deg,
            strict_exclusion_zone=strict_exclusion_zone,
            epfd_threshold_by_lat_fn=epfd_threshold_by_lat_fn,
            sat_local_geom=sat_local_geom,
        )

        # Binary searches on the boundaries α = sign · α₀
        for sign in (0, 1, -1):
            _wcgd_check_alpha_phi_case(
                sat_ecef, sat_eci, sat_vel_eci, t_s,
                phi_rad, phi0_rad, sign, "RHS",
                alpha0_deg, min_elevation_deg, gso_min_elevation_deg, pfd_mask, es_antenna,
                pfd_bw_correction_db, state, es_lat_min, es_lat_max,
                subsat_lat_deg=subsat_lat_deg, subsat_lon_deg=subsat_lon_deg,
                ray_ctx=ray_ctx, sat_local_frame=sat_local_frame,
                strict_exclusion_zone=strict_exclusion_zone,
                epfd_threshold_by_lat_fn=epfd_threshold_by_lat_fn)
            if not symmetric_mask:
                _wcgd_check_alpha_phi_case(
                    sat_ecef, sat_eci, sat_vel_eci, t_s,
                    phi_rad, phi0_rad, sign, "LHS",
                    alpha0_deg, min_elevation_deg, gso_min_elevation_deg, pfd_mask, es_antenna,
                    pfd_bw_correction_db, state, es_lat_min, es_lat_max,
                    subsat_lat_deg=subsat_lat_deg, subsat_lon_deg=subsat_lon_deg,
                    ray_ctx=ray_ctx, sat_local_frame=sat_local_frame,
                    strict_exclusion_zone=strict_exclusion_zone,
                    epfd_threshold_by_lat_fn=epfd_threshold_by_lat_fn)

        phi_rad += phi_step_rad

    # Searches on the elevation + alpha boundaries
    for sign in (0, 1, -1):
        _wcgd_check_alpha_elev_case(
            sat_ecef, sat_eci, sat_vel_eci, t_s,
            phi0_rad, sign, "RHS",
            alpha0_deg, min_elevation_deg, gso_min_elevation_deg, pfd_mask, es_antenna,
            pfd_bw_correction_db, state, es_lat_min, es_lat_max,
            subsat_lat_deg=subsat_lat_deg, subsat_lon_deg=subsat_lon_deg,
            ray_ctx=ray_ctx, sat_local_frame=sat_local_frame,
            strict_exclusion_zone=strict_exclusion_zone,
            epfd_threshold_by_lat_fn=epfd_threshold_by_lat_fn)
        if not symmetric_mask:
            _wcgd_check_alpha_elev_case(
                sat_ecef, sat_eci, sat_vel_eci, t_s,
                phi0_rad, sign, "LHS",
                alpha0_deg, min_elevation_deg, gso_min_elevation_deg, pfd_mask, es_antenna,
                pfd_bw_correction_db, state, es_lat_min, es_lat_max,
                subsat_lat_deg=subsat_lat_deg, subsat_lon_deg=subsat_lon_deg,
                ray_ctx=ray_ctx, sat_local_frame=sat_local_frame,
                strict_exclusion_zone=strict_exclusion_zone,
                epfd_threshold_by_lat_fn=epfd_threshold_by_lat_fn)

    return state


def _wcgd_check_extreme_case(
    oe: "OrbitalElements",
    t_s: float,
    sign: int,
    theta_val: float,       # +π/2 or −π/2
    alpha0_deg: float,
    min_elevation_deg: float,
    gso_min_elevation_deg: float,
    pfd_mask: "PFDMask",
    es_antenna: "EarthStationAntenna",
    pfd_bw_correction_db: float,
    es_lat_min: float,
    es_lat_max: float,
    collect_all_points: bool = False,
    tol: float = 1e-5,
    strict_exclusion_zone: bool = False,
    epfd_threshold_by_lat_fn: "Callable[[float], float] | None" = None,
) -> _WCGState:
    """WCGD_CheckExtremeCase: bisection in latitude for α=sign·α₀ at the maximum elevation."""
    state = _WCGState(collect_all_points=collect_all_points)
    incl_rad = abs(float(oe.i))
    # Latitude bracket(s) per contour: sign=+1 → α=+α₀ in the north half,
    # sign=−1 → α=−α₀ in the south half (matching `_wcgd_calc_at_lat`'s grid
    # halves). sign=0 → α=0 contour (`_wcgd_get_delta_alpha` with sign=0
    # returns α itself): the crossing can lie in either hemisphere, so both
    # halves are bisected; bracketing each half against the equator also
    # captures the symmetric tangent case where α(0) = 0 exactly
    # (f_lo·f_hi ≤ 0 holds with f = 0 at the endpoint).
    if sign > 0:
        brackets = [(0.0, incl_rad)]
    elif sign < 0:
        brackets = [(-incl_rad, 0.0)]
    else:
        brackets = [(-incl_rad, 0.0), (0.0, incl_rad)]

    def f_at_lat(lat_rad: float) -> float | None:
        sat = _place_sat_at_lat(oe, math.degrees(lat_rad))
        if sat is None:
            return None
        s_eci, _ = sat
        s_ecef = eci_to_ecef(s_eci, t_s)
        ray_ctx = _build_wcgd_ray_context(s_ecef)
        r = float(np.linalg.norm(s_ecef))
        phi0 = math.radians(_calc_phi0(r, min_elevation_deg))
        phi = _wcgd_calc_phi_from_theta_elev(
            s_ecef, theta_val, phi0, min_elevation_deg, ray_ctx
        )
        if phi is None:
            return None
        # α(P) − sign·α₀ (same convention as `_wcgd_check_alpha_phi_case`).
        return _wcgd_get_delta_alpha(s_ecef, theta_val, phi, sign, alpha0_deg, ray_ctx)

    def _bisect_lat(lat_lo: float, lat_hi: float) -> float | None:
        f_lo = f_at_lat(lat_lo)
        f_hi = f_at_lat(lat_hi)
        if f_lo is None or f_hi is None or f_lo * f_hi > 0:
            return None
        for _ in range(60):
            if abs(lat_hi - lat_lo) < tol:
                break
            mid = (lat_lo + lat_hi) / 2.0
            f_mid = f_at_lat(mid)
            if f_mid is None:
                return None
            if f_lo * f_mid <= 0:
                lat_hi, f_hi = mid, f_mid
            else:
                lat_lo, f_lo = mid, f_mid
        return math.degrees((lat_lo + lat_hi) / 2.0)

    for lat_lo, lat_hi in brackets:
        lat_root_deg = _bisect_lat(lat_lo, lat_hi)
        if lat_root_deg is None:
            continue
        sat = _place_sat_at_lat(oe, lat_root_deg)
        if sat is None:
            continue
        sat_eci, sat_vel_eci = sat
        sat_ecef = eci_to_ecef(sat_eci, t_s)
        ray_ctx = _build_wcgd_ray_context(sat_ecef)
        sat_local_frame = _build_sat_local_frame_ecef(sat_ecef)
        r = float(np.linalg.norm(sat_ecef))
        phi0 = math.radians(_calc_phi0(r, min_elevation_deg))
        phi_root = _wcgd_calc_phi_from_theta_elev(
            sat_ecef, theta_val, phi0, min_elevation_deg, ray_ctx
        )
        if phi_root is None:
            continue

        # Same search latitude as _wcgd_calc_at_lat (for trail improvements / UI filtering).
        state.search_lat_deg = float(lat_root_deg)

        _wcgd_check_case(sat_ecef, sat_eci, sat_vel_eci, t_s,
                         theta_val, phi_root,
                         pfd_mask, es_antenna, alpha0_deg, min_elevation_deg,
                         gso_min_elevation_deg,
                         pfd_bw_correction_db, state, es_lat_min, es_lat_max,
                         ray_ctx=ray_ctx, sat_local_frame=sat_local_frame,
                         strict_exclusion_zone=strict_exclusion_zone,
                         epfd_threshold_by_lat_fn=epfd_threshold_by_lat_fn)
    return state


def _wcga_progress_prefix(
    ps: _WCGState,
    es_antenna: EarthStationAntenna,
    alpha0_deg: float,
    strict_exclusion_zone: bool,
) -> str:
    """Log fragment before the WCGA bar: last processed latitude and best local WCG."""
    sat_lat = ps.search_lat_deg
    sat_s = f"{sat_lat:.2f}°" if sat_lat is not None else "?"
    br = ps.best_result
    if br is None:
        return f"[sat_λ={sat_s} WCG=—] "
    crit = s1503_or_criteria_log(
        es_antenna,
        br.alpha_deg,
        alpha0_deg,
        br.offaxis_deg,
        br.planar_angle_deg,
        strict_exclusion_zone,
    )
    return (
        f"[sat_λ={sat_s} ES=({br.es_lat_deg:.2f}°,{br.es_lon_deg:.2f}°) "
        f"GSO_lon={br.gso_lon_deg:.2f}° OR: {crit}] "
    )


def warmup_wcg_search_kernels() -> None:
    """Pre-compiles hot geometric helpers used on the WCGA boundaries."""
    sat_ecef = np.array([RE_KM + 1200.0, 60.0, 20.0], dtype=np.float64)
    ray_ctx = _build_wcgd_ray_context(sat_ecef)
    if ray_ctx is None:
        return
    theta = 0.1
    phi = 0.2
    _wcgd_ray_to_earth_scalar(
        float(sat_ecef[0]),
        float(sat_ecef[1]),
        float(sat_ecef[2]),
        theta,
        phi,
        ray_ctx,
    )
    _wcgd_get_delta_alpha(sat_ecef, theta, phi, 1, 0.0, ray_ctx)
    _wcgd_get_delta_elev(sat_ecef, theta, phi, 5.0, ray_ctx)


def search_wcg_s1503(
    oe_ref: "OrbitalElements",
    t_s: float,
    pfd_mask: "PFDMask",
    es_antenna: "EarthStationAntenna",
    alpha0_deg: float,
    min_elevation_deg: float,
    gso_min_elevation_deg: float = 5.0,
    pfd_bw_correction_db: float = 0.0,
    step_size_deg: float = 0.1,
    symmetric_mask: bool = True,
    es_lat_min: float = -81.2,
    es_lat_max: float = 81.2,
    n_jobs: int = -1,
    collect_all_points: bool = False,
    max_co_freq_by_lat: list[tuple[float, float, int]] | None = None,
    strict_exclusion_zone: bool = False,
    orbit_idx: int = 0,
    total_orbits: int = 0,
    epfd_threshold_by_lat_fn: "Callable[[float], float] | None" = None,
) -> WCGResult | None:
    """WCG search per the analytical algorithm of ITU-R S.1503-4 §D.3.1 (WCGA_Down).

    Alternative to search_wcg (uniform θ/φ grid). Implements the
    recommendation's pseudocode: it iterates over satellite latitudes with a
    step of ``step_size_deg`` and, for each latitude, combines a regular grid
    in (θ, φ) with binary searches on the boundaries α = α₀ and ε = ε₀.

    Parallelized by latitude via multiprocessing.Pool: n_jobs=-1 uses all
    cores, split between processes and internal Numba threads.

    Parameters
    ----------
    oe_ref         : orbital elements of the reference satellite (point-mass)
    t_s            : simulation instant (s) — used as the "static time"
    step_size_deg  : grid step (S.1503-4 recommends 0.1°)
    symmetric_mask : if True, assumes a mask symmetric in ΔLon → grid θ ∈ [0, π]
                     (north→east→south, excluding the west by symmetry).
                     Frame: θ_ours = π/2 − θ_S1503; Fig. 14 of S.1503-4 has θ=0=east.
    n_jobs         : number of cores (-1 = all)
    """
    import multiprocessing as _mp

    os.environ.setdefault("WCG_MAIN_PID", str(os.getpid()))
    os.environ["WCG_GMST0_DEG"] = str(get_earth_rotation_initial_deg())

    incl_deg = math.degrees(oe_ref.i)

    logger.info(
        f"WCGA S.1503-4: i={incl_deg:.1f}°, step={step_size_deg}°, "
        f"α₀={alpha0_deg:.1f}°, ε₀={min_elevation_deg:.1f}°, εGSO={gso_min_elevation_deg:.1f}°"
    )
    if epfd_threshold_by_lat_fn is not None and getattr(epfd_threshold_by_lat_fn, "latitude_dependent", False):
        logger.info(
            "  EPFDThreshold[lat] active (note %s): WCGA ranking by margin "
            "= EPFD − threshold(lat).",
            getattr(epfd_threshold_by_lat_fn, "note", "?"),
        )

    # Build the list of latitudes to evaluate
    if incl_deg < step_size_deg:
        lat_list = [0.0]
    else:
        lat_num_steps = math.ceil(incl_deg / step_size_deg)
        lat_list = [0.0]
        for n in range(1, lat_num_steps + 1):
            lat = incl_deg * n / lat_num_steps
            lat_list.append(lat)
            lat_list.append(-lat)

    lat_list, pruned_latitudes = _filter_wcga_latitudes_by_sat_oper(lat_list, max_co_freq_by_lat)
    if pruned_latitudes > 0:
        logger.info(
            "  WCGA S.1503: %d latitude(s) discarded due to sat_oper/MAX_CO_FREQ=0.",
            pruned_latitudes,
        )

    # Size parallelism to the actual latitude count: one latitude per
    # process to saturate cores (sequential-Python-heavy work).
    P_procs, T_threads = _resolve_wcga_parallel_jobs(n_jobs, n_lats=len(lat_list))

    logger.info(
        f"  {len(lat_list)} latitude positions to evaluate "
        f"(procs={P_procs}, numba_threads={T_threads}, requested={n_jobs})"
    )

    # Fixed arguments for each worker
    common = dict(
        t_s=t_s,
        pfd_mask=pfd_mask,
        es_antenna=es_antenna,
        alpha0_deg=alpha0_deg,
        min_elevation_deg=min_elevation_deg,
        gso_min_elevation_deg=gso_min_elevation_deg,
        pfd_bw_correction_db=pfd_bw_correction_db,
        strict_exclusion_zone=strict_exclusion_zone,
        step_size_deg=step_size_deg,
        symmetric_mask=symmetric_mask,
        es_lat_min=es_lat_min,
        es_lat_max=es_lat_max,
        collect_all_points=collect_all_points,
        epfd_threshold_by_lat_fn=epfd_threshold_by_lat_fn,
    )

    # Orbit progress suffix (for the frontend progress bar)
    _orbit_suffix = f"  orbit {orbit_idx}/{total_orbits}" if total_orbits > 1 else ""

    # Per-latitude execution with progress logs
    partial_states: list[_WCGState] = []
    total_lats = len(lat_list)
    _t_phase = time.perf_counter()
    if _WCGA_EXECUTOR is not None:
        # Distributed dispatch: hand the per-latitude tasks to the injected
        # executor (e.g. Ray). Same work unit (_wcga_pool_worker) and merge
        # as the local Pool path → parity-preserving. Each remote worker is
        # single-threaded (Numba=1); cluster parallelism is across tasks.
        pool_args = [(oe_ref, lat, common) for lat in lat_list]
        init = {
            "gmst0_deg": os.environ.get("WCG_GMST0_DEG", "0.0"),
            "main_pid": os.environ.get("WCG_MAIN_PID", str(os.getpid())),
            "gso_mode": get_gso_longitude_mode(),
            # Fresh (spawned) Ray workers don't inherit the module globals the
            # forked Pool workers do, so the α-computation method must be shipped
            # explicitly — otherwise remote latitudes silently fall back to the
            # default "sweep" instead of the configured method (e.g. analytical).
            "alpha_method": get_alpha_method(),
            "numba_threads": 1,
        }
        logger.info(
            f"  WCGA dispatch via injected executor ({total_lats} latitudes)"
        )
        partial_states = list(_WCGA_EXECUTOR(_wcga_pool_worker, pool_args, init))
    elif P_procs <= 1:
        set_numba_num_threads(T_threads)
        for i, lat in enumerate(lat_list, start=1):
            partial_states.append(_wcgd_calc_at_lat(oe_ref, lat, **common))
            if i % max(1, total_lats // 20) == 0 or i == total_lats:
                pct = (i / total_lats) * 100.0
                width = 28
                filled = int(round((pct / 100.0) * width))
                bar = "[" + ("#" * filled) + ("-" * (width - filled)) + "]"
                logger.info(
                    f"  {_wcga_progress_prefix(partial_states[-1], es_antenna, alpha0_deg, strict_exclusion_zone)}"
                    f"WCGA latitudes {bar} {pct:6.2f}%  lat {i}/{total_lats}{_orbit_suffix}"
                )
    else:
        pool_args = [(oe_ref, lat, common) for lat in lat_list]
        gmst0_str = os.environ.get("WCG_GMST0_DEG", "0.0")
        main_pid_str = os.environ.get("WCG_MAIN_PID", str(os.getpid()))
        gso_mode = get_gso_longitude_mode()
        done = 0
        with _mp.Pool(
            processes=P_procs,
            initializer=_wcga_pool_initializer,
            initargs=(T_threads, gmst0_str, main_pid_str, gso_mode),
        ) as pool:
            for ps in pool.imap_unordered(_wcga_pool_worker, pool_args):
                partial_states.append(ps)
                done += 1
                if done % max(1, total_lats // 20) == 0 or done == total_lats:
                    pct = (done / total_lats) * 100.0
                    width = 28
                    filled = int(round((pct / 100.0) * width))
                    bar = "[" + ("#" * filled) + ("-" * (width - filled)) + "]"
                    logger.info(
                        f"  {_wcga_progress_prefix(ps, es_antenna, alpha0_deg, strict_exclusion_zone)}"
                        f"WCGA latitudes {bar} {pct:6.2f}%  lat {done}/{total_lats}{_orbit_suffix}"
                    )

    _dt_grid = time.perf_counter() - _t_phase
    logger.info(f"  WCGA grid phase: {_dt_grid:.2f}s ({total_lats} latitudes)")

    # Merge order aligned with `lat_list` (merge by
    # latitude index). With Pool, `imap_unordered` returns completions in a
    # variable order; the 0.1 dB bin candidate merge may diverge on numerical ties.
    _t_phase = time.perf_counter()
    _lat_merge_order = {float(lat): idx for idx, lat in enumerate(lat_list)}
    partial_states.sort(
        key=lambda ps: (
            _lat_merge_order[float(ps.search_lat_deg)]
            if ps.search_lat_deg is not None
            else len(lat_list)
        )
    )

    # Merge all partial states
    state = _WCGState(collect_all_points=collect_all_points)
    for ps in partial_states:
        state.merge(ps)
    _dt_merge = time.perf_counter() - _t_phase
    logger.info(f"  WCGA partials sort+merge phase: {_dt_merge:.2f}s")

    logger.info(
        f"  Grid done: {state.n_total} points evaluated "
        f"(ok={state.n_ok}, excl={state.n_excl}, low_elev={state.n_low_elev})"
    )

    # Extreme cases (sequential — few points)
    _t_phase = time.perf_counter()
    for sign in (0, 1, -1):
        for theta_v in (math.pi / 2.0, -math.pi / 2.0):
            ps = _wcgd_check_extreme_case(
                oe_ref, t_s, sign, theta_v,
                alpha0_deg, min_elevation_deg, gso_min_elevation_deg, pfd_mask, es_antenna,
                pfd_bw_correction_db, es_lat_min, es_lat_max,
                collect_all_points=collect_all_points,
                strict_exclusion_zone=strict_exclusion_zone,
                epfd_threshold_by_lat_fn=epfd_threshold_by_lat_fn)
            state.merge(ps)
    _dt_extreme = time.perf_counter() - _t_phase
    logger.info(f"  WCGA extreme cases phase (6 sequential): {_dt_extreme:.2f}s")

    if state.best_result is None:
        logger.warning("WCGA S.1503-4: no valid geometry found.")
        return None

    best = state.best_result
    best.search_trail = state.trail
    best.search_trail_all = state.trail_all
    # Per-latitude best point (one per swept satellite latitude) — feeds the
    # WCG explanation (EPFD/margin/ang.vel vs latitude). Built from the partial
    # states before they were merged; sorted by resulting ES latitude.
    _profile = []
    for ps in partial_states:
        br = getattr(ps, "best_result", None)
        if br is None or br.epfd_dBW <= -900.0:
            continue
        _profile.append({
            "sat_lat_deg": (float(ps.search_lat_deg) if ps.search_lat_deg is not None else None),
            "es_lat_deg": float(br.es_lat_deg),
            "epfd_dBW": float(br.epfd_dBW),
            "margin_dB": (float(ps.best_margin) if getattr(ps, "best_margin", None) is not None else None),
            "alpha_deg": float(br.alpha_deg),
            "offaxis_deg": float(br.offaxis_deg),
            "pfd_dBW": float(br.pfd_dBW),
            "es_gain_rel_dB": float(br.es_gain_rel_dB),
            "angular_velocity_deg_s": float(br.angular_velocity_deg_s),
        })
    _profile.sort(key=lambda r: r["es_lat_deg"])
    best.latitude_profile = _profile
    logger.info(
        f"WCGA S.1503-4 done: EPFD={best.epfd_dBW:.2f} dBW, "
        f"ES=({best.es_lat_deg:.2f}°, {best.es_lon_deg:.2f}°), "
        f"α={best.alpha_deg:.3f}°"
    )
    return best
