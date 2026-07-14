"""
mask_converter.py — PFD mask conversion between coordinate systems.

Supports:
  alpha_deltaLongitude  ↔  azimuth_elevation
  (S.1503 §D6.4.4)           (S.1503 §D3.1.3.1, satellite local frame)

Strategy for each latitude slice:
  • The satellite is placed at (lat_sat, lon=0°) at altitude h_km.
  • alpha_deltaLong → azimuth_elevation (forward fill):
      For each (az, el) point of the target grid, trace the ray down to the
      Earth's surface, obtain the ES position and compute (alpha, ΔLong) for
      interpolation on the source mask.
  • azimuth_elevation → alpha_deltaLong (scatter + interpolation):
      For each (az, el) point of the source mask, trace the ray and compute
      (alpha, ΔLong); the scattered set is interpolated onto the target grid
      via griddata.

Self-contained geometry (no numba/Cython) for portability.
"""

from __future__ import annotations

import logging
import math
from typing import Optional

import numpy as np
from scipy.interpolate import griddata

from .pfd_mask import PFDMask, PFDMaskXML

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Physical constants
# ---------------------------------------------------------------------------
_RE_KM = 6378.137
_GSO_RADIUS_KM = 42164.17
_DEG = math.pi / 180.0

# ---------------------------------------------------------------------------
# Default target grid parameters
# ---------------------------------------------------------------------------
_DEFAULT_STEP_DEG = 2.0  # output grid resolution (°)


# ---------------------------------------------------------------------------
# Helper geometry (pure Python/NumPy)
# ---------------------------------------------------------------------------

def _sat_ecef(lat_deg: float, lon_deg: float, alt_km: float) -> np.ndarray:
    """ECEF position of the satellite at (lat°, lon°) at altitude alt_km."""
    r = _RE_KM + alt_km
    lat = lat_deg * _DEG
    lon = lon_deg * _DEG
    clat, slat = math.cos(lat), math.sin(lat)
    clon, slon = math.cos(lon), math.sin(lon)
    return np.array([r * clat * clon, r * clat * slon, r * slat], dtype=np.float64)


def _build_frame(sat_ecef: np.ndarray) -> tuple:
    """Satellite local frame (nadir=z, east=y, north=x) in ECEF.

    Returns (zx,zy,zz, yx,yy,yz, xx,xy,xz) as in _build_sat_local_frame_ecef.
    """
    nx, ny, nz = float(sat_ecef[0]), float(sat_ecef[1]), float(sat_ecef[2])
    n_norm = math.sqrt(nx * nx + ny * ny + nz * nz)
    if n_norm < 1e-12:
        return (0.0, 0.0, -1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 0.0)
    zx, zy, zz = -nx / n_norm, -ny / n_norm, -nz / n_norm
    # east = cross(nadir, north_pole=[0,0,1])  →  (zy, -zx, 0)
    yx, yy, yz = zy, -zx, 0.0
    y_norm = math.sqrt(yx * yx + yy * yy)
    if y_norm < 1e-9:
        yx, yy = 0.0, 1.0
    else:
        yx /= y_norm
        yy /= y_norm
    # north = east × nadir
    xx = yy * zz - yz * zy
    xy = yz * zx - yx * zz
    xz = yx * zy - yy * zx
    return (zx, zy, zz, yx, yy, yz, xx, xy, xz)


def _az_el_to_directions_batch(
    az_grid: np.ndarray,
    el_grid: np.ndarray,
    frame: tuple,
) -> np.ndarray:
    """Convert 1-D az and el (°) grids into unit ECEF directions.

    az_grid, el_grid  1-D of equal length (or flattened meshgrid).
    Returns (N, 3).
    S.1503 §D3.1.3.1: nadir=cos(az)cos(el), east=sin(az)cos(el), north=sin(el).
    """
    az = az_grid * _DEG
    el = el_grid * _DEG
    cos_el = np.cos(el)
    nadir_c = np.cos(az) * cos_el
    east_c  = np.sin(az) * cos_el
    north_c = np.sin(el)
    zx, zy, zz, yx, yy, yz, xx, xy, xz = frame
    dx = nadir_c * zx + east_c * yx + north_c * xx
    dy = nadir_c * zy + east_c * yy + north_c * xy
    dz = nadir_c * zz + east_c * yz + north_c * xz
    return np.column_stack([dx, dy, dz])  # (N, 3)


def _ray_earth_batch(sat_ecef: np.ndarray, dirs: np.ndarray) -> np.ndarray:
    """Ray→Earth intersection for multiple directions.

    sat_ecef: (3,), dirs: (N, 3) — unit or not.
    Returns (N, 3); rows whose ray does not hit the Earth are NaN.
    """
    # Normalize dirs
    norms = np.linalg.norm(dirs, axis=1, keepdims=True)
    valid_d = norms.ravel() > 1e-12
    norms = np.where(norms > 1e-12, norms, 1.0)
    d = dirs / norms

    sx, sy, sz = float(sat_ecef[0]), float(sat_ecef[1]), float(sat_ecef[2])
    b = d[:, 0] * sx + d[:, 1] * sy + d[:, 2] * sz  # sat·d
    c_ = sx * sx + sy * sy + sz * sz - _RE_KM * _RE_KM
    disc = b * b - c_
    hit = valid_d & (disc >= 0.0)

    result = np.full((len(dirs), 3), np.nan)
    if not np.any(hit):
        return result

    sq = np.sqrt(np.maximum(disc[hit], 0.0))
    t = -b[hit] - sq  # near intersection
    # If t < 0, try the far intersection
    t_far = -b[hit] + sq
    t = np.where(t >= 0, t, t_far)
    still_valid = t >= 0
    idx = np.where(hit)[0][still_valid]
    t_ok = t[still_valid]

    result[idx] = sat_ecef + t_ok[:, np.newaxis] * d[idx]
    return result


def _ecef_to_latlon_batch(ecef: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Lat/lon (°) for N ECEF points."""
    x, y, z = ecef[:, 0], ecef[:, 1], ecef[:, 2]
    lat = np.degrees(np.arctan2(z, np.sqrt(x * x + y * y)))
    lon = np.degrees(np.arctan2(y, x))
    return lat, lon


def _elevation_batch(es_ecef: np.ndarray, sat_ecef: np.ndarray) -> np.ndarray:
    """Elevation (°) of the satellite as seen from each ES in es_ecef (N,3)."""
    es_lat, es_lon = _ecef_to_latlon_batch(es_ecef)
    lat_r = es_lat * _DEG
    lon_r = es_lon * _DEG
    ux = np.cos(lat_r) * np.cos(lon_r)
    uy = np.cos(lat_r) * np.sin(lon_r)
    uz = np.sin(lat_r)
    dx = sat_ecef[0] - es_ecef[:, 0]
    dy = sat_ecef[1] - es_ecef[:, 1]
    dz = sat_ecef[2] - es_ecef[:, 2]
    r = np.sqrt(dx * dx + dy * dy + dz * dz)
    r = np.maximum(r, 1e-9)
    sin_el = np.clip((ux * dx + uy * dy + uz * dz) / r, -1.0, 1.0)
    return np.degrees(np.arcsin(sin_el))


def _visible_gso_arc_halfwidth_deg_batch(es_lat_deg: np.ndarray) -> np.ndarray:
    """Half-width of the GSO arc visible from each ES (°).

    ITU-R S.1503-4 §D6.4.4.4 + §D6.4.4.2 with x2=0:
    cos(ΔLong_max) = (Re/Rgeo) / cos(LatES). Mirrors
    ``geometry._visible_gso_arc_halfwidth_deg`` (0° when nothing is visible).
    """
    cos_lat = np.cos(es_lat_deg * _DEG)
    safe = np.abs(cos_lat) > 1e-12
    cos_theta_max = np.where(
        safe, (_RE_KM / _GSO_RADIUS_KM) / np.where(safe, cos_lat, 1.0), 1.0
    )
    cos_theta_max = np.clip(cos_theta_max, -1.0, 1.0)
    return np.degrees(np.arccos(cos_theta_max))


def _alpha_sign_xy_plane_batch(es_ecef: np.ndarray, d_en: np.ndarray) -> np.ndarray:
    """Sign of α per ITU-R S.1503-4 §D6.4.4.1, vectorized.

    Port of ``geometry._alpha_sign_xy_plane_numba``: builds the line
    R = R_ES + λ·R_EN (R_EN = R_NGSO − R_ES), intersects the XY plane
    (λ = −es_z/dzn) and compares |R(z=0)| against Rgeo.
    """
    es_x, es_y, es_z = es_ecef[:, 0], es_ecef[:, 1], es_ecef[:, 2]
    dxn, dyn, dzn = d_en[:, 0], d_en[:, 1], d_en[:, 2]

    sign = np.full(es_z.shape, -1.0)

    # ES on the equator: α = -sign(R_EN.z)
    eq = np.abs(es_z) < 1e-9
    sign = np.where(eq & (dzn > 0.0), -1.0, sign)
    sign = np.where(eq & (dzn < 0.0), 1.0, sign)
    sign = np.where(eq & (dzn == 0.0), 0.0, sign)

    dzn_safe = np.where(np.abs(dzn) > 1e-12, dzn, 1.0)
    lam = -es_z / dzn_safe
    # No FORWARD crossing of the XY plane (λ<0, or line parallel to it):
    # R_z=0 = ∞. The R-vs-Rgeo comparison is the below/above-visible-arc test
    # (a LoS pointing at the arc crosses at exactly Rgeo), so ∞ = "above the
    # arc": α<0 for a northern ES and α>0 for a SOUTHERN one (Figs 57/59 —
    # e.g. the zenith over a southern ES is above the arc ⇒ α > 0).
    no_cross = (~eq) & ((np.abs(dzn) < 1e-12) | (lam < 0.0))
    sign = np.where(no_cross, np.where(es_z > 0.0, -1.0, 1.0), sign)

    rest = (~eq) & (~no_cross)
    rxz = es_x + lam * dxn
    ryz = es_y + lam * dyn
    r_z0 = np.sqrt(rxz * rxz + ryz * ryz)

    north = rest & (es_z > 0.0)
    sign = np.where(north, np.where(r_z0 < _GSO_RADIUS_KM, 1.0,
                                    np.where(r_z0 > _GSO_RADIUS_KM, -1.0, 0.0)), sign)

    south = rest & (es_z < 0.0)
    sign = np.where(south, np.where(r_z0 > _GSO_RADIUS_KM, 1.0,
                                    np.where(r_z0 < _GSO_RADIUS_KM, -1.0, 0.0)), sign)

    return sign


def _alpha_dlon_batch(
    es_ecef: np.ndarray,
    sat_ecef: np.ndarray,
    sat_lon_deg: float = 0.0,
    coarse_step_deg: float = 1.0,
    refine_half_deg: float = 2.0,
    refine_step_deg: float = 0.1,
) -> tuple[np.ndarray, np.ndarray]:
    """Signed alpha (°, §D6.4.4.1) and ΔLong (°) for N stations vs a fixed satellite.

    Sweep restricted to the GSO arc **visible** from each ES (§D6.4.4.4),
    centered on the ES longitude, with refinement around the minimum and the
    visibility edges as extra candidates.
    """
    N = es_ecef.shape[0]

    # ES→sat vector (normalized) and per-station visible-arc geometry.
    d_en = sat_ecef[np.newaxis, :] - es_ecef  # (N,3)
    ds_n = np.linalg.norm(d_en, axis=1, keepdims=True)
    ds_n = np.maximum(ds_n, 1e-9)
    ds = d_en / ds_n  # (N,3)

    es_lat, es_lon = _ecef_to_latlon_batch(es_ecef)
    half_deg = _visible_gso_arc_halfwidth_deg_batch(es_lat)  # (N,)

    def _cos_alpha(lon_deg: np.ndarray) -> np.ndarray:
        lon_r = lon_deg * _DEG
        gx = _GSO_RADIUS_KM * np.cos(lon_r) - es_ecef[:, 0]
        gy = _GSO_RADIUS_KM * np.sin(lon_r) - es_ecef[:, 1]
        gz = -es_ecef[:, 2]
        gn = np.sqrt(gx * gx + gy * gy + gz * gz)
        gn = np.maximum(gn, 1e-9)
        return (ds[:, 0] * gx + ds[:, 1] * gy + ds[:, 2] * gz) / gn

    # GSO longitude offsets relative to each ES longitude.
    best_cos = np.full(N, -2.0)
    best_off = np.zeros(N)

    # Coarse sweep over the widest visible arc; per-station offsets outside the
    # local arc are masked out (a GSO point below the ES horizon is invalid).
    max_half = float(np.max(half_deg)) if N else 0.0
    n_coarse = int(math.ceil(max_half / max(coarse_step_deg, 1e-6)))
    for i in range(-n_coarse, n_coarse + 1):
        off = i * coarse_step_deg
        in_arc = np.abs(off) <= half_deg + 1e-12
        if not np.any(in_arc):
            continue
        cos_a = _cos_alpha(es_lon + off)
        improve = in_arc & (cos_a > best_cos)
        best_cos = np.where(improve, cos_a, best_cos)
        best_off = np.where(improve, off, best_off)

    # Visibility edges as explicit candidates (§D6.4.4.1 mentions the two
    # edge-of-visibility points).
    for sgn in (-1.0, 1.0):
        off_edge = sgn * half_deg
        cos_a = _cos_alpha(es_lon + off_edge)
        improve = cos_a > best_cos
        best_cos = np.where(improve, cos_a, best_cos)
        best_off = np.where(improve, off_edge, best_off)

    # Refinement around the best value (resolution refine_step_deg), clamped
    # to the visible arc. Offsets stay relative to the coarse best to avoid
    # accumulation if best_off is updated inside the loop.
    coarse_best_off = best_off.copy()
    offsets = np.arange(
        -refine_half_deg, refine_half_deg + refine_step_deg * 0.5, refine_step_deg
    )
    for off in offsets:
        cand_off = np.clip(coarse_best_off + off, -half_deg, half_deg)
        cos_a = _cos_alpha(es_lon + cand_off)
        improve = cos_a > best_cos
        best_cos = np.where(improve, cos_a, best_cos)
        best_off = np.where(improve, cand_off, best_off)

    alpha_abs = np.degrees(np.arccos(np.clip(best_cos, -1.0, 1.0)))
    # ITU-R S.1503-4 §D6.4.4.1: signed α (the alpha_deltaLongitude axis is signed;
    # the repo does not assume mask symmetry — see pfd_mask WCG symmetry helpers).
    alpha = _alpha_sign_xy_plane_batch(es_ecef, d_en) * alpha_abs

    best_lon_deg = es_lon + best_off
    dlon = best_lon_deg - sat_lon_deg
    # Wrap into (-180, 180]
    dlon = ((dlon + 180.0) % 360.0) - 180.0
    dlon = np.where(dlon == -180.0, 180.0, dlon)
    return alpha, dlon


def _phi0_deg(h_km: float, min_el_deg: float) -> float:
    """Maximum nadir angle φ₀ (S.1503-4 §D.3.1.3.3 / §D6.1.2).

    sin(φ₀) = (Re / (Re + h)) · cos(ε₀).  Mirrors ``wcg_search._calc_phi0``.
    """
    r = _RE_KM + max(0.0, h_km)
    eps = max(0.0, min_el_deg) * _DEG
    ratio = (_RE_KM / r) * math.cos(eps)
    ratio = max(-1.0, min(1.0, ratio))
    return math.degrees(math.asin(ratio))


# ---------------------------------------------------------------------------
# Grid functions
# ---------------------------------------------------------------------------

def _azel_target_grid(
    h_km: float,
    min_el_deg: float,
    step_b_deg: float,
    step_c_deg: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Regular (az, el) grid covering the visibility cone.

    Axis b=azimuth ∈ [-180, 180) with step ``step_b_deg``.
    Axis c=elevation ∈ [-φ₀, +φ₀] with step ``step_c_deg``.
    """
    phi0 = _phi0_deg(h_km, min_el_deg)
    az_vals = np.arange(-180.0, 180.0, step_b_deg)
    el_vals = np.arange(-phi0, phi0 + step_c_deg * 0.5, step_c_deg)
    az_2d, el_2d = np.meshgrid(az_vals, el_vals, indexing="ij")
    return az_vals, el_vals, az_2d.ravel(), el_2d.ravel()


def _alphaDlon_target_grid(
    alpha_max_deg: float,
    dlon_max_deg: float,
    step_b_deg: float,
    step_c_deg: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Regular (α, ΔLong) grid with physical ranges.

    α ∈ [-α_max, +α_max] with step ``step_b_deg``;
    ΔLong ∈ [-d_max, +d_max] with step ``step_c_deg``.
    """
    alpha_max_deg = max(1.0, float(alpha_max_deg))
    dlon_max_deg = max(1.0, float(dlon_max_deg))
    alpha_vals = np.arange(-alpha_max_deg, alpha_max_deg + step_b_deg * 0.5, step_b_deg)
    dlon_vals = np.arange(-dlon_max_deg, dlon_max_deg + step_c_deg * 0.5, step_c_deg)
    al_2d, dl_2d = np.meshgrid(alpha_vals, dlon_vals, indexing="ij")
    return alpha_vals, dlon_vals, al_2d.ravel(), dl_2d.ravel()


# ---------------------------------------------------------------------------
# Conversion per latitude slice
# ---------------------------------------------------------------------------

def _convert_alpha2azel_lat_slice(
    source: PFDMaskXML,
    lat_sat: float,
    sat_ecef: np.ndarray,
    frame: tuple,
    az_vals: np.ndarray,
    el_vals: np.ndarray,
    az_flat: np.ndarray,
    el_flat: np.ndarray,
    min_el_deg: float,
) -> np.ndarray:
    """Slice: alpha_deltaLong → azimuth_elevation.

    Returns an (n_az, n_el) grid of PFD values.
    """
    n_az, n_el = len(az_vals), len(el_vals)

    # Satellite directions for each (az, el)
    dirs = _az_el_to_directions_batch(az_flat, el_flat, frame)

    # Ray→Earth intersection
    es_pts = _ray_earth_batch(sat_ecef, dirs)  # (N, 3), NaN where it does not hit
    valid = ~np.isnan(es_pts[:, 0])

    # Filter by the ES minimum elevation toward the satellite
    if np.any(valid):
        elev = _elevation_batch(es_pts[valid], sat_ecef)
        ok = elev >= min_el_deg
        idx_valid = np.where(valid)[0]
        valid[idx_valid[~ok]] = False

    # Compute alpha and dlon for valid points
    pfd_flat = np.full(len(az_flat), np.nan)
    if np.any(valid):
        alpha_arr, dlon_arr = _alpha_dlon_batch(es_pts[valid], sat_ecef)
        lat_arr = np.full(np.sum(valid), lat_sat)
        pfd_arr = source.get_pfd_batch(alpha_arr, lat_arr, dlon_arr)
        pfd_flat[valid] = pfd_arr

    # Fill NaN with the nearest edge value via nearest-neighbor
    if np.any(~np.isfinite(pfd_flat)) and np.any(np.isfinite(pfd_flat)):
        has_val = np.isfinite(pfd_flat)
        pts_src = np.column_stack([az_flat[has_val], el_flat[has_val]])
        pts_tgt = np.column_stack([az_flat[~has_val], el_flat[~has_val]])
        pfd_flat[~np.isfinite(pfd_flat)] = griddata(
            pts_src, pfd_flat[has_val], pts_tgt, method="nearest"
        )

    # Fallback: if NaN still remain, use the minimum value of the source slice
    still_nan = ~np.isfinite(pfd_flat)
    if np.any(still_nan):
        fallback = float(np.nanmin(source._pfd_grid)) if source._pfd_grid.size else -200.0
        pfd_flat[still_nan] = fallback

    return pfd_flat.reshape(n_az, n_el)


def _convert_azel2alpha_lat_slice(
    source: PFDMaskXML,
    lat_sat: float,
    sat_ecef: np.ndarray,
    frame: tuple,
    alpha_vals: np.ndarray,
    dlon_vals: np.ndarray,
    alpha_flat: np.ndarray,
    dlon_flat: np.ndarray,
    min_el_deg: float,
) -> np.ndarray:
    """Slice: azimuth_elevation → alpha_deltaLong.

    Samples the source grid (az, el), computes (alpha, dlon) and performs
    scattered interpolation onto the target grid.
    Returns an (n_alpha, n_dlon) grid.
    """
    n_al, n_dl = len(alpha_vals), len(dlon_vals)

    # Source grid: axes b and c of the original mask (az and el)
    az_src = source._alpha_vals
    el_src = source._dlon_vals
    az_2d, el_2d = np.meshgrid(az_src, el_src, indexing="ij")
    az_flat_src = az_2d.ravel()
    el_flat_src = el_2d.ravel()

    dirs = _az_el_to_directions_batch(az_flat_src, el_flat_src, frame)
    es_pts = _ray_earth_batch(sat_ecef, dirs)
    valid = ~np.isnan(es_pts[:, 0])

    if np.any(valid):
        elev = _elevation_batch(es_pts[valid], sat_ecef)
        ok = elev >= min_el_deg
        idx_valid = np.where(valid)[0]
        valid[idx_valid[~ok]] = False

    if not np.any(valid):
        fallback = float(np.nanmin(source._pfd_grid)) if source._pfd_grid.size else -200.0
        return np.full((n_al, n_dl), fallback)

    alpha_s, dlon_s = _alpha_dlon_batch(es_pts[valid], sat_ecef)
    lat_s = np.full(np.sum(valid), lat_sat)
    pfd_s = source.get_pfd_batch(az_flat_src[valid], lat_s, el_flat_src[valid])

    # Scattered interpolation onto the target grid
    pts_src = np.column_stack([alpha_s, dlon_s])
    pts_tgt = np.column_stack([alpha_flat, dlon_flat])

    pfd_tgt_lin = griddata(pts_src, pfd_s, pts_tgt, method="linear")
    pfd_tgt_nn  = griddata(pts_src, pfd_s, pts_tgt, method="nearest")
    # Nearest fallback where linear has no coverage
    pfd_tgt = np.where(np.isfinite(pfd_tgt_lin), pfd_tgt_lin, pfd_tgt_nn)

    still_nan = ~np.isfinite(pfd_tgt)
    if np.any(still_nan):
        pfd_tgt[still_nan] = float(np.nanmin(pfd_s))

    return pfd_tgt.reshape(n_al, n_dl)


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------

def convert_mask(
    source: PFDMaskXML,
    *,
    h_km: float = 600.0,
    min_elevation_deg: float = 5.0,
    step_b_deg: float = _DEFAULT_STEP_DEG,
    step_c_deg: float = _DEFAULT_STEP_DEG,
    target_alpha_max_deg: float | None = None,
    target_dlon_max_deg: float = 180.0,
) -> PFDMaskXML:
    """Convert a PFD mask between alpha_deltaLongitude ↔ azimuth_elevation.

    Parameters
    ----------
    source : PFDMaskXML
        Source mask (mask_type must be 'alpha_deltaLongitude' or
        'azimuth_elevation').
    h_km : float
        Representative satellite altitude (km). Used to determine the
        visibility geometry and to position the satellite in each latitude
        slice.
    min_elevation_deg : float
        Minimum ES elevation toward the satellite (°). Defines the visibility
        cone (φ₀).
    step_b_deg, step_c_deg : float
        Output grid step on axes b and c, respectively (°).
        alpha2azel: b=azimuth, c=elevation.
        azel2alpha: b=alpha, c=deltaLongitude.
    target_alpha_max_deg : float | None
        Only for ``azel2alpha``: sets ``α ∈ [-α_max, +α_max]`` on the target
        grid. ``None`` (default) uses φ₀(h, ε₀) — the maximum theoretical α
        visible for the geometry. Useful to reduce (e.g. 30°) when the mask is
        consumed only where α < α₀ (typical FSS).
    target_dlon_max_deg : float
        Only for ``azel2alpha``: sets ``ΔLong ∈ [-d_max, +d_max]`` on the
        target grid. Default 180° (full GSO arc coverage).

    Returns
    -------
    PFDMaskXML
        New mask with the converted type and recomputed axes.
    """
    if not isinstance(source, PFDMaskXML):
        raise TypeError("Only PFDMaskXML (3D) masks are supported.")

    src_type = source.mask_type
    if src_type == "alpha_deltaLongitude":
        target_type = "azimuth_elevation"
        direction = "alpha2azel"
    elif src_type == "azimuth_elevation":
        target_type = "alpha_deltaLongitude"
        direction = "azel2alpha"
    else:
        raise ValueError(
            f"mask_type='{src_type}' not supported. "
            "Expected 'alpha_deltaLongitude' or 'azimuth_elevation'."
        )

    lat_vals = source._lat_vals.copy()
    n_lat = len(lat_vals)

    # Build the target grid (same for all latitude slices)
    if direction == "alpha2azel":
        az_vals, el_vals, az_flat, el_flat = _azel_target_grid(
            h_km, min_elevation_deg, step_b_deg, step_c_deg
        )
        target_b_vals = az_vals
        target_c_vals = el_vals
        target_b_name = "azimuth"
        target_c_name = "elevation"
    else:
        alpha_max = (
            float(target_alpha_max_deg)
            if target_alpha_max_deg is not None
            else _phi0_deg(h_km, min_elevation_deg)
        )
        alpha_vals, dlon_vals, alpha_flat, dlon_flat = _alphaDlon_target_grid(
            alpha_max, float(target_dlon_max_deg), step_b_deg, step_c_deg,
        )
        target_b_vals = alpha_vals
        target_c_vals = dlon_vals
        target_b_name = "alpha"
        target_c_name = "deltaLongitude"

    n_b = len(target_b_vals)
    n_c = len(target_c_vals)
    pfd_grid = np.zeros((n_lat, n_b, n_c), dtype=np.float64)

    logger.info(
        "Converting mask %s→%s: %d lat slices, grid %d×%d, h=%.0f km, min_el=%.1f°",
        src_type, target_type, n_lat, n_b, n_c, h_km, min_elevation_deg,
    )

    for i_lat, lat_sat in enumerate(lat_vals):
        sat = _sat_ecef(float(lat_sat), 0.0, h_km)
        frame = _build_frame(sat)

        if direction == "alpha2azel":
            pfd_grid[i_lat] = _convert_alpha2azel_lat_slice(
                source, float(lat_sat), sat, frame,
                az_vals, el_vals, az_flat, el_flat,
                min_elevation_deg,
            )
        else:
            pfd_grid[i_lat] = _convert_azel2alpha_lat_slice(
                source, float(lat_sat), sat, frame,
                alpha_vals, dlon_vals, alpha_flat, dlon_flat,
                min_elevation_deg,
            )

        logger.debug("  lat %.1f° done (%d/%d)", lat_sat, i_lat + 1, n_lat)

    # Assemble the output PFDMaskXML
    out: PFDMaskXML = PFDMaskXML.__new__(PFDMaskXML)
    PFDMask.__init__(out)
    out._dim = 3
    out.mask_id = source.mask_id
    out.mask_type = target_type
    out.low_freq_mhz = source.low_freq_mhz
    out.high_freq_mhz = source.high_freq_mhz
    out.refbw_khz = source.refbw_khz
    out.sat_name = source.sat_name
    out.ntc_id = source.ntc_id
    out.a_name = "latitude"
    out.b_name = target_b_name
    out.c_name = target_c_name
    out._lat_vals = lat_vals
    out._alpha_vals = target_b_vals.astype(np.float64)
    out._dlon_vals = target_c_vals.astype(np.float64)
    out._pfd_grid = pfd_grid
    out._wcg_query_mirror_axis = None
    out._wcg_query_mirror_sign = 1.0
    out._wcg_theta_symmetry_cache = None

    logger.info("Conversion complete: %s", out)
    return out
