"""
constants.py — Physical constants and parameters of the Earth model.

Reference: ITU-R S.1503-4, Part D, Section D6.
"""

import math

# ---------------------------------------------------------------------------
#  Earth
# ---------------------------------------------------------------------------
RE_KM = 6378.145                      # Radius of the Earth (km) — S.1503-4 Table 2 (§A2.2)
RE_M = RE_KM * 1e3                    # in meters
RP_KM = 6356.752                      # Polar radius of the Earth (km, WGS-84; not in S.1503-4)
FLAT = 1.0 / 298.257223563            # Flattening (WGS-84; not in S.1503-4)
MU_KM3_S2 = 3.986012e5              # Gravitational parameter μ (km³/s²) — S.1503-4 Table 2 (§A2.2)
MU_M3_S2 = MU_KM3_S2 * 1e9          # μ in m³/s²
J2 = 0.001082636                      # Oblateness coefficient J₂ (S.1503-4)
OMEGA_E = 7.2921151467e-5            # Angular velocity of the Earth (rad/s)
SIDEREAL_DAY_S = 86164.0905          # Sidereal day (s)

# ---------------------------------------------------------------------------
#  Geostationary orbit
# ---------------------------------------------------------------------------
GSO_RADIUS_KM = 42164.2              # GSO orbital radius Rgeo (km) — S.1503-4 Table 2 (§A2.2)
GSO_ALTITUDE_KM = GSO_RADIUS_KM - RE_KM

# ---------------------------------------------------------------------------
#  Conversions
# ---------------------------------------------------------------------------
DEG2RAD = math.pi / 180.0
RAD2DEG = 180.0 / math.pi
C_M_S = 299792458.0                  # Speed of light (m/s)
FOUR_PI = 4.0 * math.pi

# ---------------------------------------------------------------------------
#  Power references
# ---------------------------------------------------------------------------
BOLTZMANN_DBW = -228.6               # k in dBW/K/Hz
