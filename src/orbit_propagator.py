"""
orbit_propagator.py — Kepler + J2 orbital propagator.

Implements analytical propagation with J2 secular perturbation per
ITU-R S.1503-4, Part D, Section D.6.3.

Secular perturbations considered:
  • RAAN precession (Ω̇)
  • Argument of perigee precession (ω̇)
  • Mean anomaly variation (Ṁ) corrected by J2
"""

from __future__ import annotations
import math
import numpy as np
from dataclasses import dataclass, field
from .constants import RE_KM, MU_KM3_S2, J2, DEG2RAD, RAD2DEG, OMEGA_E
from .coordinates import gmst


@dataclass
class OrbitalElements:
    """Keplerian orbital elements."""
    a: float          # Semi-major axis (km)
    e: float          # Eccentricity
    i: float          # Inclination (rad)
    raan: float       # Right ascension of the ascending node Ω (rad)
    omega: float      # Argument of perigee ω (rad)
    M: float          # Mean anomaly M (rad)
    min_operating_height_km: float = 0.0  # H_min (SRS); 0 = no altitude filter

    # J2 secular derivatives (computed at initialization)
    raan_dot: float = field(default=0.0, init=False)
    omega_dot: float = field(default=0.0, init=False)
    M_dot: float = field(default=0.0, init=False)
    n: float = field(default=0.0, init=False)   # Mean motion

    def __post_init__(self):
        self._compute_j2_rates()

    def _compute_j2_rates(self):
        """Compute the J2 secular precession rates."""
        a = self.a
        e = self.e
        i = self.i

        n0 = math.sqrt(MU_KM3_S2 / a**3)        # Unperturbed mean motion
        p = a * (1.0 - e**2)                     # Semi-latus rectum
        eta = math.sqrt(1.0 - e**2)
        k = (3.0 / 2.0) * J2 * (RE_KM / p)**2

        cos_i = math.cos(i)
        sin_i = math.sin(i)

        # Secular rates (rad/s) — S.1503-4 D6.3.2 eqs (20)-(22). The J2
        # precession of the node and perigee is driven by the J2-CORRECTED
        # mean motion n̄ (eq 20), NOT the point-mass n0.
        n_bar = n0 * (1.0 + k * eta * (1.0 - 1.5 * sin_i**2))       # n̄  (eq 20)
        self.raan_dot = -k * n_bar * cos_i                          # Ω̇  (eq 21)
        self.omega_dot = k * n_bar * (2.0 - 2.5 * sin_i**2)         # ω̇  (eq 22)
        self.M_dot = n_bar                                          # Ṁ  (= n̄)
        self.n = n0                                                 # point-mass n0 (Case 3, eq 53)

    def propagate(self, dt_s: float) -> "OrbitalElements":
        """Propagate the orbital elements by dt_s seconds (J2 secular).

        Reuses the J2 rates computed in the original element to avoid
        recomputing trigonometric sines/cosines at each step.
        """
        child = object.__new__(OrbitalElements)
        child.a = self.a
        child.e = self.e
        child.i = self.i
        child.raan = self.raan + self.raan_dot * dt_s
        child.omega = self.omega + self.omega_dot * dt_s
        child.M = self.M + self.M_dot * dt_s
        # Reuse J2 rates (depend only on a, e, i — unchanged)
        child.raan_dot = self.raan_dot
        child.omega_dot = self.omega_dot
        child.M_dot = self.M_dot
        child.n = self.n
        child.min_operating_height_km = self.min_operating_height_km
        return child

    @classmethod
    def from_degrees(
        cls,
        a: float,
        e: float,
        i_deg: float,
        raan_deg: float,
        omega_deg: float,
        M_deg: float,
        *,
        min_operating_height_km: float = 0.0,
    ) -> "OrbitalElements":
        return cls(
            a=a, e=e,
            i=i_deg * DEG2RAD,
            raan=raan_deg * DEG2RAD,
            omega=omega_deg * DEG2RAD,
            M=M_deg * DEG2RAD,
            min_operating_height_km=float(min_operating_height_km or 0.0),
        )


# =====================================================================
#  Kepler: Mean anomaly → Eccentric anomaly (Newton-Raphson)
# =====================================================================

def solve_kepler(M: float, e: float, tol: float = 1e-12, max_iter: int = 50) -> float:
    """Solve Kepler's equation M = E - e·sin(E) via Newton-Raphson.
    Returns E in radians."""
    M = M % (2.0 * math.pi)
    E = M if e < 0.8 else math.pi
    for _ in range(max_iter):
        dE = (E - e * math.sin(E) - M) / (1.0 - e * math.cos(E))
        E -= dE
        if abs(dE) < tol:
            break
    return E


# =====================================================================
#  Orbital elements → ECI position/velocity
# =====================================================================

def elements_to_eci(oe: OrbitalElements) -> tuple[np.ndarray, np.ndarray]:
    """Convert orbital elements into ECI position and velocity (km, km/s)."""
    E = solve_kepler(oe.M, oe.e)

    # True anomaly
    cos_E = math.cos(E)
    sin_E = math.sin(E)
    sqrt1me2 = math.sqrt(1.0 - oe.e**2)

    nu = math.atan2(sqrt1me2 * sin_E, cos_E - oe.e)

    # Distance
    r = oe.a * (1.0 - oe.e * cos_E)

    # Position and velocity in the orbital plane (perifocal)
    cos_nu = math.cos(nu)
    sin_nu = math.sin(nu)

    x_pf = r * cos_nu
    y_pf = r * sin_nu

    p = oe.a * (1.0 - oe.e**2)
    h = math.sqrt(MU_KM3_S2 * p)

    vx_pf = -(MU_KM3_S2 / h) * sin_nu
    vy_pf = (MU_KM3_S2 / h) * (oe.e + cos_nu)

    # Perifocal → ECI rotation
    cos_o = math.cos(oe.omega)
    sin_o = math.sin(oe.omega)
    cos_O = math.cos(oe.raan)
    sin_O = math.sin(oe.raan)
    cos_i = math.cos(oe.i)
    sin_i = math.sin(oe.i)

    R = np.array([
        [cos_O * cos_o - sin_O * sin_o * cos_i,
         -cos_O * sin_o - sin_O * cos_o * cos_i,
         sin_O * sin_i],
        [sin_O * cos_o + cos_O * sin_o * cos_i,
         -sin_O * sin_o + cos_O * cos_o * cos_i,
         -cos_O * sin_i],
        [sin_o * sin_i,
         cos_o * sin_i,
         cos_i]
    ])

    pos = R @ np.array([x_pf, y_pf, 0.0])
    vel = R @ np.array([vx_pf, vy_pf, 0.0])

    return pos, vel


def eci_to_mean_anomaly(oe: OrbitalElements, r_eci_km: np.ndarray) -> float:
    """Given orbital elements (a, e, i, raan, omega) and an ECI position (km),
    return the mean anomaly M (rad) that produces that position.

    Useful for aligning the reference satellite to the WCG position after
    search_wcg_s1503 (which returns ref_sat_eci but does not update oe.M).
    """
    r_eci = np.asarray(r_eci_km, dtype=float).reshape(3)
    cos_o = math.cos(oe.omega)
    sin_o = math.sin(oe.omega)
    cos_O = math.cos(oe.raan)
    sin_O = math.sin(oe.raan)
    cos_i = math.cos(oe.i)
    sin_i = math.sin(oe.i)
    R = np.array([
        [cos_O * cos_o - sin_O * sin_o * cos_i,
         -cos_O * sin_o - sin_O * cos_o * cos_i,
         sin_O * sin_i],
        [sin_O * cos_o + cos_O * sin_o * cos_i,
         -sin_O * sin_o + cos_O * cos_o * cos_i,
         -cos_O * sin_i],
        [sin_o * sin_i,
         cos_o * sin_i,
         cos_i]
    ])
    r_pf = R.T @ r_eci
    x_pf, y_pf = float(r_pf[0]), float(r_pf[1])
    nu = math.atan2(y_pf, x_pf)
    e = oe.e
    if e < 1e-10:
        M = nu
    else:
        sin_h = math.sqrt(max(0, 1 - e)) * math.sin(nu / 2)
        cos_h = math.sqrt(max(0, 1 + e)) * math.cos(nu / 2)
        E = 2.0 * math.atan2(sin_h, cos_h)
        M = E - e * math.sin(E)
    return M % (2.0 * math.pi)


# =====================================================================
#  Walker constellation (plane / satellite generation)
# =====================================================================

def create_walker_constellation(
    a: float, e: float, i_deg: float,
    num_planes: int, sats_per_plane: int,
    phasing_factor: int = 1,
    raan0_deg: float = 0.0,
    omega_deg: float = 0.0,
    min_operating_height_km: float = 0.0,
) -> list[OrbitalElements]:
    """Create a Walker Delta constellation.

    T/P/F = (num_planes * sats_per_plane) / num_planes / phasing_factor
    """
    total = num_planes * sats_per_plane
    delta_raan = 360.0 / num_planes
    delta_M = 360.0 / sats_per_plane
    phase_offset = phasing_factor * 360.0 / total

    constellation: list[OrbitalElements] = []

    for p in range(num_planes):
        raan = raan0_deg + p * delta_raan
        for s in range(sats_per_plane):
            M = (s * delta_M + p * phase_offset) % 360.0
            oe = OrbitalElements.from_degrees(
                a=a, e=e, i_deg=i_deg,
                raan_deg=raan, omega_deg=omega_deg, M_deg=M,
                min_operating_height_km=min_operating_height_km,
            )
            constellation.append(oe)

    return constellation


# =====================================================================
#  Whole-constellation propagation
# =====================================================================

def propagate_constellation(
    constellation: list[OrbitalElements],
    dt_s: float
) -> list[OrbitalElements]:
    """Propagate all satellites in the constellation by dt_s."""
    return [oe.propagate(dt_s) for oe in constellation]


def constellation_positions_eci(
    constellation: list[OrbitalElements]
) -> list[np.ndarray]:
    """Return ECI positions of all satellites."""
    return [elements_to_eci(oe)[0] for oe in constellation]


# =====================================================================
#  Orbit-model case selection (S.1503-4 D6.3.6, Fig. 52)
# =====================================================================

def classify_orbit_case(
    *,
    repeating_ground_track: bool,
    admin_precession: bool,
    inclination_deg: float | None = None,
    equatorial_tol_deg: float = 1e-3,
) -> int:
    """Classify the propagation model per S.1503-4 D6.3.6 (Fig. 52).

    Returns the case number (1, 2 or 3); the three are mutually exclusive:

      Case 1 — no repeating ground track: point mass + J2 secular + forced
               (artificial) precession D_artificial. (eqs 46-47)
      Case 2 — repeating ground track, NO admin precession: J2 secular +
               station-keeping Wdelta. (eqs 48-50)
      Case 3 — repeating ground track + administration-supplied precession:
               point-mass mean anomaly (n0), ω held constant, admin Ω̇ +
               Wdelta. (eqs 51-53)

    Per the Fig. 52 note, an equatorial (i ≈ 0) constellation is a special
    case treated as Case 1 with the forced precession set to zero (the
    ascending node is undefined). It still classifies as Case 1 here; the
    forced-precession-zero handling is applied where the artificial rate is
    formed (see time_step.compute_time_step_and_count).
    """
    if not repeating_ground_track:
        return 1
    return 3 if admin_precession else 2


# =====================================================================
#  Vectorized whole-constellation propagation (NumPy batch)
# =====================================================================

def build_constellation_cache(
    constellation: list[OrbitalElements],
    raan_dot_override_rad_s: float | None = None,
) -> dict[str, np.ndarray]:
    """Pre-compute constant constellation arrays for reuse across time steps.

    Called once before the time loop; the resulting dict is passed
    as ``_cache`` to ``propagate_and_to_ecef_batch``.
    """
    N = len(constellation)
    a = np.array([oe.a for oe in constellation], dtype=np.float64)
    e = np.array([oe.e for oe in constellation], dtype=np.float64)
    i_arr = np.array([oe.i for oe in constellation], dtype=np.float64)
    raan0 = np.array([oe.raan for oe in constellation], dtype=np.float64)
    omega0 = np.array([oe.omega for oe in constellation], dtype=np.float64)
    omega_dot = np.array([oe.omega_dot for oe in constellation], dtype=np.float64)
    M0 = np.array([oe.M for oe in constellation], dtype=np.float64)
    M_dot = np.array([oe.M_dot for oe in constellation], dtype=np.float64)
    if raan_dot_override_rad_s is not None:
        # Case 3 (D6.3.6, eqs 51-53): administration-supplied precession.
        # ω is HELD CONSTANT (no J2 perigee drift, eq 51) and the mean anomaly
        # advances at the POINT-MASS n0 (eq 53), NOT the J2-corrected n̄.
        raan_dot_base = np.full(N, raan_dot_override_rad_s, dtype=np.float64)
        omega_dot = np.zeros(N, dtype=np.float64)
        M_dot = np.array([oe.n for oe in constellation], dtype=np.float64)
    else:
        raan_dot_base = np.array([oe.raan_dot for oe in constellation], dtype=np.float64)
    cos_i = np.cos(i_arr)
    sin_i = np.sin(i_arr)
    p_a = a * (1.0 - e * e)
    h_a = np.sqrt(MU_KM3_S2 * p_a)
    return {
        "a": a, "e": e, "cos_i": cos_i, "sin_i": sin_i,
        "raan0": raan0, "raan_dot_base": raan_dot_base,
        "omega0": omega0, "omega_dot": omega_dot,
        "M0": M0, "M_dot": M_dot,
        "h_a": h_a,
    }


def propagate_and_to_ecef_batch(
    constellation: list[OrbitalElements],
    t_s: float,
    raan_dot_artificial_rad_s: float = 0.0,
    raan_dot_override_rad_s: float | None = None,
    _cache: dict[str, np.ndarray] | None = None,
    wdelta_deg: float = 0.0,
    t_run_s: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Propagate the constellation to t_s and convert to ECEF in a single
    vectorized pipeline.

    Parameters
    ----------
    constellation : list of OrbitalElements
    t_s : time since t=0 (s)
    raan_dot_artificial_rad_s : artificial nodal precession rate (rad/s),
        per S.1503-4 D6.3.5. When > 0, accelerates the rotation of the
        orbital plane for better sampling in non-repeating orbits.
    raan_dot_override_rad_s : optional. When set, replaces the J2 rate
        (oe.raan_dot) with the MDB rate (e.g. precession_deg_day). Fallback
        for operator-declared data.
    _cache : arrays pre-computed via ``build_constellation_cache``.
    wdelta_deg : Wdelta — station keeping half-range in degrees (S.1503-4 D6.3.4).
        When > 0, adds ``Wdelta·(2t/Trun − 1)`` to the RAAN, making the
        ascending-node longitude sweep ±Wdelta during the simulation.
    t_run_s : total simulation duration in seconds (required when
        ``wdelta_deg > 0``).

    Returns
    -------
    pos_ecef : (N, 3) array in km (ECEF)
    vel_ecef : (N, 3) array in km/s (ECEF)
    """
    raan_dot_extra = raan_dot_artificial_rad_s

    # S.1503-4 D6.3.4: Wdelta station keeping offset (rad)
    wdelta_offset_rad = 0.0
    if wdelta_deg != 0.0 and t_run_s > 0.0:
        wdelta_offset_rad = math.radians(wdelta_deg) * (2.0 * t_s / t_run_s - 1.0)

    if _cache is not None:
        a = _cache["a"]
        e = _cache["e"]
        cos_i = _cache["cos_i"]
        sin_i = _cache["sin_i"]
        h_a = _cache["h_a"]
        raan = _cache["raan0"] + (_cache["raan_dot_base"] + raan_dot_extra) * t_s + wdelta_offset_rad
        omega = _cache["omega0"] + _cache["omega_dot"] * t_s
        M = _cache["M0"] + _cache["M_dot"] * t_s
    else:
        N = len(constellation)
        a     = np.array([oe.a         for oe in constellation])
        e     = np.array([oe.e         for oe in constellation])
        i_arr = np.array([oe.i         for oe in constellation])
        if raan_dot_override_rad_s is not None:
            # Case 3 (D6.3.6, eqs 51-53): ω held constant, M at point-mass n0.
            raan_dot_base = np.full(N, raan_dot_override_rad_s)
            omega = np.array([oe.omega for oe in constellation])
            M     = np.array([oe.M + oe.n * t_s for oe in constellation])
        else:
            raan_dot_base = np.array([oe.raan_dot for oe in constellation])
            omega = np.array([oe.omega + oe.omega_dot * t_s for oe in constellation])
            M     = np.array([oe.M     + oe.M_dot     * t_s for oe in constellation])
        raan = np.array([oe.raan + (rd + raan_dot_extra) * t_s + wdelta_offset_rad for oe, rd in zip(constellation, raan_dot_base)])
        cos_i = np.cos(i_arr)
        sin_i = np.sin(i_arr)
        p_a = a * (1.0 - e * e)
        h_a = np.sqrt(MU_KM3_S2 * p_a)

    # Kepler (iterative Newton-Raphson, convergence < 1e-12 rad)
    E = M.copy()
    for _ in range(10):
        sin_E = np.sin(E); cos_E = np.cos(E)
        dE = (E - e * sin_E - M) / (1.0 - e * cos_E)
        E -= dE
        if np.all(np.abs(dE) < 1e-14):
            break
    sin_E = np.sin(E); cos_E = np.cos(E)

    sqrt1me2 = np.sqrt(1.0 - e * e)
    nu   = np.arctan2(sqrt1me2 * sin_E, cos_E - e)
    r    = a * (1.0 - e * cos_E)

    cos_nu = np.cos(nu); sin_nu = np.sin(nu)
    x_pf   =  r * cos_nu;           
    y_pf  =  r * sin_nu
    vx_pf  = -(MU_KM3_S2 / h_a) * sin_nu
    vy_pf  =  (MU_KM3_S2 / h_a) * (e + cos_nu)

    cos_o = np.cos(omega);  sin_o = np.sin(omega)
    cos_O = np.cos(raan);   sin_O = np.sin(raan)

    R11 =  cos_O * cos_o - sin_O * sin_o * cos_i
    R12 = -cos_O * sin_o - sin_O * cos_o * cos_i
    R21 =  sin_O * cos_o + cos_O * sin_o * cos_i
    R22 = -sin_O * sin_o + cos_O * cos_o * cos_i
    R31 =  sin_o * sin_i
    R32 =  cos_o * sin_i

    px = R11 * x_pf + R12 * y_pf
    py = R21 * x_pf + R22 * y_pf
    pz = R31 * x_pf + R32 * y_pf
    vx = R11 * vx_pf + R12 * vy_pf
    vy = R21 * vx_pf + R22 * vy_pf
    vz = R31 * vx_pf + R32 * vy_pf

    # ECI → ECEF (rotation by -GMST), including GMST0
    theta = gmst(t_s)
    c = math.cos(theta); s = math.sin(theta)
    ecef_x =  c * px + s * py
    ecef_y = -s * px + c * py
    ecef_z =  pz
    # v_ECEF = R·v_ECI − ω×r_ECEF  (S.1503-4 Eq. 29)
    vecef_x =  c * vx + s * vy + OMEGA_E * ecef_y
    vecef_y = -s * vx + c * vy - OMEGA_E * ecef_x
    vecef_z =  vz

    pos_ecef = np.column_stack([ecef_x, ecef_y, ecef_z])
    vel_ecef = np.column_stack([vecef_x, vecef_y, vecef_z])
    return pos_ecef, vel_ecef
