"""
coordinates.py — Coordinate transformations.

Systems used:
  • ECI  (Earth-Centered Inertial)
  • ECEF (Earth-Centered Earth-Fixed)
  • LLA  (Latitude, Longitude, Altitude)
  • Topocentric (Az, El, Range)

Reference: ITU-R S.1503-4, Part D, Section D.6.1 / D.6.2.
"""

from __future__ import annotations
import math
import numpy as np
from .constants import RE_KM, OMEGA_E, DEG2RAD, RAD2DEG, GSO_RADIUS_KM


# =====================================================================
#  ECI  ⟺  ECEF
# =====================================================================
_EARTH_ROTATION_INITIAL_RAD = 0.0


def set_earth_rotation_initial_deg(angle_deg: float) -> None:
    """Set the initial Earth rotation angle (GMST0) in degrees."""
    global _EARTH_ROTATION_INITIAL_RAD
    _EARTH_ROTATION_INITIAL_RAD = math.radians(float(angle_deg)) % (2.0 * math.pi)


def get_earth_rotation_initial_deg() -> float:
    """Return the initial Earth rotation angle (GMST0) in degrees."""
    return math.degrees(_EARTH_ROTATION_INITIAL_RAD)

def gmst(t_s: float) -> float:
    """Greenwich sidereal angle (rad) given the time in seconds since the
    reference epoch (simplified J2000.0 — only delta-t is used)."""
    return (_EARTH_ROTATION_INITIAL_RAD + OMEGA_E * t_s) % (2.0 * math.pi)


def eci_to_ecef(pos_eci: np.ndarray, t_s: float, is_vector: bool = False) -> np.ndarray:
    """Convert ECI → ECEF position by rotation about the Z axis.
    is_vector is accepted for compatibility, but the transformation is purely rotational.
    """
    theta = gmst(t_s)
    c, s = math.cos(theta), math.sin(theta)
    x, y, z = float(pos_eci[0]), float(pos_eci[1]), float(pos_eci[2])
    return np.array([
        c * x + s * y,
        -s * x + c * y,
        z,
    ])


def ecef_to_eci(pos_ecef: np.ndarray, t_s: float, is_vector: bool = False) -> np.ndarray:
    """Convert ECEF → ECI position.
    is_vector is accepted for compatibility, but the transformation is purely rotational.
    """
    theta = gmst(t_s)
    c, s = math.cos(theta), math.sin(theta)
    x, y, z = float(pos_ecef[0]), float(pos_ecef[1]), float(pos_ecef[2])
    return np.array([
        c * x - s * y,
        s * x + c * y,
        z,
    ])


def eci_vel_to_ecef(
    vel_eci: np.ndarray, pos_ecef: np.ndarray, t_s: float,
) -> np.ndarray:
    """Convert ECI → ECEF velocity including the Coriolis term.

    v_ECEF = R(θ)·v_ECI − ω×r_ECEF   (S.1503-4 Eq. 29)
    where ω = [0, 0, Ωe].
    """
    theta = gmst(t_s)
    c, s = math.cos(theta), math.sin(theta)
    vx, vy, vz = float(vel_eci[0]), float(vel_eci[1]), float(vel_eci[2])
    rx, ry = float(pos_ecef[0]), float(pos_ecef[1])
    return np.array([
         c * vx + s * vy + OMEGA_E * ry,
        -s * vx + c * vy - OMEGA_E * rx,
        vz,
    ])


# =====================================================================
#  ECEF  ⟺  LLA (spherical)
# =====================================================================

def ecef_to_lla(pos_ecef: np.ndarray) -> tuple[float, float, float]:
    """Return (lat_deg, lon_deg, alt_km) from an ECEF position (km).

    Uses a spherical Earth model with radius Re per S.1503-4 D6.1.
    """
    x, y, z = float(pos_ecef[0]), float(pos_ecef[1]), float(pos_ecef[2])
    lon = math.atan2(y, x)
    p = math.sqrt(x * x + y * y)
    lat = math.atan2(z, p)
    R = math.sqrt(x * x + y * y + z * z)
    alt = R - RE_KM
    return math.degrees(lat), math.degrees(lon), alt


def ecef_to_lla_batch(pos_ecef: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Convert N ECEF positions (km) into (lat_deg, lon_deg, alt_km), each shape (N,).

    Used in the EPFD simulation to feed the 3D PFD mask in batch.
    """
    pos_ecef = np.asarray(pos_ecef, dtype=np.float64)
    if pos_ecef.ndim == 1:
        pos_ecef = pos_ecef.reshape(1, -1)
    if pos_ecef.shape[0] == 0:
        return (
            np.array([], dtype=np.float64),
            np.array([], dtype=np.float64),
            np.array([], dtype=np.float64),
        )

    x = pos_ecef[:, 0]
    y = pos_ecef[:, 1]
    z = pos_ecef[:, 2]

    lon = np.arctan2(y, x)
    p = np.sqrt(x * x + y * y)
    lat = np.arctan2(z, p)
    R = np.sqrt(x * x + y * y + z * z)
    alt = R - RE_KM

    return np.degrees(lat), np.degrees(lon), alt


def lla_to_ecef(lat_deg: float, lon_deg: float, alt_km: float = 0.0) -> np.ndarray:
    """Convert (lat, lon, alt) to ECEF (km).

    Uses a spherical Earth model with radius Re per S.1503-4 D6.1
    (Eqs. 8-10): x = Re·cos(lat)·cos(lon), etc.
    """
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    R = RE_KM + alt_km
    cos_lat = math.cos(lat)
    sin_lat = math.sin(lat)
    x = R * cos_lat * math.cos(lon)
    y = R * cos_lat * math.sin(lon)
    z = R * sin_lat
    return np.array([x, y, z])


# =====================================================================
#  Sub-satellite point
# =====================================================================

def sub_satellite_point(pos_eci: np.ndarray, t_s: float) -> tuple[float, float]:
    """Return (lat_deg, lon_deg) of the sub-satellite point."""
    pos_ecef = eci_to_ecef(pos_eci, t_s)
    lat, lon, _ = ecef_to_lla(pos_ecef)
    return lat, lon


# =====================================================================
#  Topocentric geometry
# =====================================================================

def topocentric_angles(
    observer_ecef: np.ndarray,
    target_ecef: np.ndarray,
    lat_deg: float,
    lon_deg: float
) -> tuple[float, float, float]:
    """Compute azimuth (°), elevation (°) and range (km) of a target seen
    from an observer on the Earth's surface.

    The ENU (East-North-Up) system is used.
    """
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)

    sin_lat, cos_lat = math.sin(lat), math.cos(lat)
    sin_lon, cos_lon = math.sin(lon), math.cos(lon)

    # ECEF → ENU rotation matrix
    R = np.array([
        [-sin_lon,             cos_lon,            0.0],
        [-sin_lat * cos_lon,  -sin_lat * sin_lon,  cos_lat],
        [ cos_lat * cos_lon,   cos_lat * sin_lon,  sin_lat]
    ])

    diff = target_ecef - observer_ecef
    enu = R @ diff
    e, n, u = enu

    rng = np.linalg.norm(diff)
    if rng > 1e-10:
        val = u / rng
        val = max(-1.0, min(1.0, val))
        elevation = math.degrees(math.asin(val))
    else:
        elevation = 0.0
    azimuth = math.degrees(math.atan2(e, n)) % 360.0

    return azimuth, elevation, rng


def is_visible(elevation_deg: float, min_elevation_deg: float = 0.0) -> bool:
    """Check whether a satellite is above the minimum elevation."""
    return elevation_deg >= min_elevation_deg


# =====================================================================
#  GSO arc point
# =====================================================================

def gso_position_eci(longitude_deg: float, t_s: float) -> np.ndarray:
    """ECI position (km) of a GSO satellite in the geographic longitude slot ``longitude_deg`` (°).

    The slot is fixed to the Earth's equator; in ECI the vector rotates with GMST(t).
    """
    lon_eci = math.radians(longitude_deg) + gmst(t_s)
    x = GSO_RADIUS_KM * math.cos(lon_eci)
    y = GSO_RADIUS_KM * math.sin(lon_eci)
    z = 0.0
    return np.array([x, y, z])


def gso_position_ecef(longitude_deg: float, t_s: float) -> np.ndarray:
    """ECEF position (km) of the GSO slot at ``longitude_deg`` (°) at instant ``t_s`` (s).

    Obtained by ECI→ECEF from :func:`gso_position_eci`, aligned to the same GMST
    as ``eci_to_ecef`` / propagation. For an ideal GEO on the equator, the result is
    numerically constant in ``t_s`` (same longitude in the Earth-fixed frame).
    """
    return eci_to_ecef(gso_position_eci(longitude_deg, t_s), t_s)


# =====================================================================
#  Unit vectors / angle between vectors
# =====================================================================

def unit_vector(v: np.ndarray) -> np.ndarray:
    """Unit vector."""
    n = np.linalg.norm(v)
    if n < 1e-15:
        return v
    return v / n


def angle_between(v1: np.ndarray, v2: np.ndarray) -> float:
    """Angle between two vectors (radians)."""
    u1 = unit_vector(v1)
    u2 = unit_vector(v2)
    dot = np.clip(np.dot(u1, u2), -1.0, 1.0)
    return math.acos(dot)
