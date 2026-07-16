"""
antenna.py — Antenna radiation patterns.

Implements the reference antenna patterns per:
  • ITU-R S.1428-1 (GSO earth station antenna)
  • ITU-R BO.1443-3 (BSS earth station antenna)
  • Generic pattern for non-GSO satellite (fixed beamwidth)

Reference: ITU-R S.1503-4, Annex 1, Section D.2.

Compliance (2026 revision): equations checked against the extractions in
``docs/extracted/R-REC-S.1428-1-200102-I!!PDF-E/`` and
``docs/extracted/R-REC-BO.1443-3-201407-I!!PDF-E/`` (branches in φ, Gmax, G1, φm;
BO.1443: thresholds 36.3° / 50°, back lobes M1–M6 and θ intervals).
Gain logarithms are log10(φ) in degrees, as in the standard. The ``efficiency``
parameter is only stored (ITU patterns are envelopes in D/λ, without η in the formulas).
"""

from __future__ import annotations
import math
from .constants import C_M_S, DEG2RAD


class EarthStationAntenna:
    """Common interface for earth station antenna patterns."""

    requires_planar_angle = False

    def gain(self, off_axis_deg: float, theta_deg: float | None = None) -> float:
        raise NotImplementedError

    def relative_gain(self, off_axis_deg: float, theta_deg: float | None = None) -> float:
        return self.gain(off_axis_deg, theta_deg) - self.g_max

    def relative_gain_linear(self, off_axis_deg: float, theta_deg: float | None = None) -> float:
        return 10.0 ** (self.relative_gain(off_axis_deg, theta_deg) / 10.0)

    @property
    def theta_3db_deg(self) -> float:
        """3 dB beamwidth (full-width, degrees): θ3dB = 70·λ/D.

        Convention used by the ITU BR_Space EPFD software for the §D4
        dimensioning beamwidth (both S.1428 and BO.1443) — validated against
        official EPFDRESULTS runs (fine/coarse Δt match exactly). The
        parabolic-region derivation (2·√(3/2.5e-3)·λ/D ≈ 69.28·λ/D) is ~1%
        narrower and yields ~2% more time steps than the BR reference.
        """
        return 70.0 / self.d_over_lambda


# =====================================================================
#  ITU-R S.1428-1 — Antenna pattern for GSO ES
# =====================================================================

class ITURS1428Antenna(EarthStationAntenna):
    """GSO earth station radiation pattern per ITU-R S.1428-1.

    Applicable for D/λ >= 20 (bands 20-25, 25-100 and >100),
    per Rec. ITU-R S.1428-1 (2001), recommends 1.

    Pre-computed at initialization to avoid recomputation.
    """

    def __init__(self, diameter_m: float, frequency_ghz: float,
                 efficiency: float = 0.65):
        self.diameter = diameter_m
        self.frequency = frequency_ghz
        self.efficiency = efficiency

        wavelength = C_M_S / (frequency_ghz * 1e9)
        self.d_over_lambda = diameter_m / wavelength

        if self.d_over_lambda < 20.0:
            raise ValueError(
                f"Rec. ITU-R S.1428-1 requires D/λ >= 20 (current: {self.d_over_lambda:.2f})"
            )

        # Normative bands of Rec. ITU-R S.1428-1.
        if self.d_over_lambda <= 25.0:
            self.regime = "20_25"
            self.g_max = 20.0 * math.log10(self.d_over_lambda) + 7.7
            self.g1 = 29.0 - 25.0 * math.log10(95.0 / self.d_over_lambda)
            self.phi_m = 20.0 / self.d_over_lambda * math.sqrt(self.g_max - self.g1)
            self.phi_plateau_max = 95.0 / self.d_over_lambda
            self.phi_r = None
        elif self.d_over_lambda <= 100.0:
            self.regime = "25_100"
            self.g_max = 20.0 * math.log10(self.d_over_lambda) + 7.7
            self.g1 = 29.0 - 25.0 * math.log10(95.0 / self.d_over_lambda)
            self.phi_m = 20.0 / self.d_over_lambda * math.sqrt(self.g_max - self.g1)
            self.phi_plateau_max = 95.0 / self.d_over_lambda
            self.phi_r = None
        else:
            self.regime = "gt_100"
            self.g_max = 20.0 * math.log10(self.d_over_lambda) + 8.4
            self.g1 = -1.0 + 15.0 * math.log10(self.d_over_lambda)
            self.phi_m = 20.0 / self.d_over_lambda * math.sqrt(self.g_max - self.g1)
            self.phi_r = 15.85 * self.d_over_lambda ** (-0.6)
            self.phi_plateau_max = None

    def gain(self, off_axis_deg: float, theta_deg: float | None = None) -> float:
        """Returns the gain (dBi) for the off-axis angle φ (degrees)."""
        phi = min(abs(off_axis_deg), 180.0)

        if phi < 1e-10:
            return self.g_max

        if self.regime in ("20_25", "25_100"):
            if phi < self.phi_m:
                return self.g_max - 2.5e-3 * (self.d_over_lambda * phi) ** 2
            if phi < self.phi_plateau_max:
                return self.g1
            if phi <= 33.1:
                return 29.0 - 25.0 * math.log10(phi)
            if phi <= 80.0:
                return -9.0
            if self.regime == "20_25":
                return -5.0
            if phi <= 120.0:
                return -4.0
            return -9.0

        if phi < self.phi_m:
            return self.g_max - 2.5e-3 * (self.d_over_lambda * phi) ** 2
        if phi < self.phi_r:
            return self.g1
        if phi < 10.0:
            return 29.0 - 25.0 * math.log10(phi)
        if phi < 34.1:
            return 34.0 - 30.0 * math.log10(phi)
        if phi < 80.0:
            return -12.0
        if phi < 120.0:
            return -7.0
        return -12.0

    def __repr__(self):
        return (f"ITURS1428Antenna(D={self.diameter:.2f}m, "
                f"f={self.frequency:.2f}GHz, "
                f"D/λ={self.d_over_lambda:.1f}, "
                f"G_max={self.g_max:.1f}dBi)")


class ITUBO1443Antenna(EarthStationAntenna):
    """BSS radiation pattern per Rec. ITU-R BO.1443-3."""

    requires_planar_angle = True

    def __init__(self, diameter_m: float, frequency_ghz: float,
                 efficiency: float = 0.65):
        self.diameter = diameter_m
        self.frequency = frequency_ghz
        self.efficiency = efficiency

        wavelength = C_M_S / (frequency_ghz * 1e9)
        self.d_over_lambda = diameter_m / wavelength

        if self.d_over_lambda < 11.0:
            raise ValueError(
                f"Rec. ITU-R BO.1443-3 requires D/λ >= 11 (current: {self.d_over_lambda:.2f})"
            )

        self.g_max = 20.0 * math.log10(self.d_over_lambda) + 8.1

        if self.d_over_lambda <= 25.5:
            self.regime = "11_25p5"
            self.g1 = 29.0 - 25.0 * math.log10(95.0 / self.d_over_lambda)
            self.phi_m = (1.0 / self.d_over_lambda) * math.sqrt((self.g_max - self.g1) / 0.0025)
            self.phi_plateau_max = 95.0 / self.d_over_lambda
            self.phi_r = None
        elif self.d_over_lambda <= 100.0:
            self.regime = "25p5_100"
            self.g1 = 29.0 - 25.0 * math.log10(95.0 / self.d_over_lambda)
            self.phi_m = (1.0 / self.d_over_lambda) * math.sqrt((self.g_max - self.g1) / 0.0025)
            self.phi_plateau_max = 95.0 / self.d_over_lambda
            self.phi_r = None
        else:
            self.regime = "gt_100"
            self.g1 = -1.0 + 15.0 * math.log10(self.d_over_lambda)
            self.phi_m = (1.0 / self.d_over_lambda) * math.sqrt((self.g_max - self.g1) / 0.0025)
            self.phi_r = 15.85 * self.d_over_lambda ** (-0.6)
            self.phi_plateau_max = None

    def _rear_gain_small_aperture(self, phi: float, theta_deg: float | None) -> float:
        if theta_deg is None:
            raise ValueError("BO.1443-3 requires theta_deg for phi >= 50° in the regime 11 <= D/lambda <= 25.5")

        theta = theta_deg % 360.0
        sin_theta = math.sin(math.radians(theta))

        if 56.25 <= theta < 123.75:
            m1 = (2.0 + 8.0 * sin_theta) / math.log10(90.0 / 50.0)
            b1 = m1 * math.log10(50.0) + 10.0
            if phi < 90.0:
                return m1 * math.log10(phi) - b1
            m2 = (-9.0 - 8.0 * sin_theta) / math.log10(180.0 / 90.0)
            b2 = m2 * math.log10(180.0) + 17.0
            return m2 * math.log10(phi) - b2

        if theta < 180.0:
            m3 = (2.0 + 8.0 * sin_theta) / math.log10(120.0 / 50.0)
            b3 = m3 * math.log10(50.0) + 10.0
            if phi < 120.0:
                return m3 * math.log10(phi) - b3
            m4 = (-9.0 - 8.0 * sin_theta) / math.log10(180.0 / 120.0)
            b4 = m4 * math.log10(180.0) + 17.0
            return m4 * math.log10(phi) - b4

        m5 = 2.0 / math.log10(120.0 / 50.0)
        b5 = m5 * math.log10(50.0) + 10.0
        if phi < 120.0:
            return m5 * math.log10(phi) - b5
        m6 = -9.0 / math.log10(180.0 / 120.0)
        b6 = m6 * math.log10(180.0) + 17.0
        return m6 * math.log10(phi) - b6

    def gain(self, off_axis_deg: float, theta_deg: float | None = None) -> float:
        phi = min(abs(off_axis_deg), 180.0)

        if phi < 1e-10:
            return self.g_max

        if self.regime == "11_25p5":
            if phi < self.phi_m:
                return self.g_max - 2.5e-3 * (self.d_over_lambda * phi) ** 2
            if phi < self.phi_plateau_max:
                return self.g1
            if phi < 36.3:
                return 29.0 - 25.0 * math.log10(phi)
            if phi < 50.0:
                return -10.0
            return self._rear_gain_small_aperture(phi, theta_deg)

        if self.regime == "25p5_100":
            if phi < self.phi_m:
                return self.g_max - 2.5e-3 * (self.d_over_lambda * phi) ** 2
            if phi < self.phi_plateau_max:
                return self.g1
            if phi < 33.1:
                return 29.0 - 25.0 * math.log10(phi)
            if phi <= 80.0:
                return -9.0
            if phi <= 120.0:
                return -4.0
            return -9.0

        if phi < self.phi_m:
            return self.g_max - 2.5e-3 * (self.d_over_lambda * phi) ** 2
        if phi < self.phi_r:
            return self.g1
        if phi < 10.0:
            return 29.0 - 25.0 * math.log10(phi)
        if phi < 34.1:
            return 34.0 - 30.0 * math.log10(phi)
        if phi < 80.0:
            return -12.0
        if phi < 120.0:
            return -7.0
        return -12.0

    def __repr__(self):
        return (f"ITUBO1443Antenna(D={self.diameter:.2f}m, "
                f"f={self.frequency:.2f}GHz, "
                f"D/λ={self.d_over_lambda:.1f}, "
                f"G_max={self.g_max:.1f}dBi)")


def s1503_or_condition_include(
    es_antenna: EarthStationAntenna,
    offaxis_deg: float,
    alpha0_deg: float,
    theta_deg: float | None = None,
    *,
    disable_or_condition: bool = False,
) -> bool:
    """S.1503-4 D3.1.2 / D5.1 Step 18: gain OR condition in the zone |α| < α₀.

    In the standard, Step 16 defines ``GRX(φ)`` as the receive gain (dBi). Step 18 uses
    ``GRX(φ) > min(Gmax − 30 dB, GRX(α₀[lat]))`` with the **same** antenna pattern
    evaluated at φ (off-axis for the non-GSO) and at α₀ (exclusion zone parameter).

    In the implementation, we work with gain **relative** to the peak
    ``GRX_rel(x) = GRX(x) − Gmax`` (dB):

        GRX(φ) > min(Gmax−30, GRX(α₀))
        ⇔ GRX_rel(φ) > min(−30 dB, GRX_rel(α₀))

    For patterns with a planar angle (e.g. BO.1443), ``GRX_rel(α₀)`` uses the same
    ``theta_deg`` as the current point, as in the evaluation of ``GRX_rel(φ)``.

    When ``disable_or_condition`` is True (API / ``non_gso.strict_exclusion_zone``),
    this predicate is treated as **always false**: there is no "rescue" by gain inside
    the angular exclusion zone (only the |α| ≥ α₀ branch of the disjunction remains).
    """
    if disable_or_condition:
        return False
    g_rel = es_antenna.relative_gain(offaxis_deg, theta_deg)
    g_rel_at_alpha0 = es_antenna.relative_gain(alpha0_deg, theta_deg)
    threshold_db = min(-30.0, g_rel_at_alpha0)
    return g_rel > threshold_db


def s1503_or_criteria_log(
    es_antenna: EarthStationAntenna,
    alpha_deg: float,
    alpha0_deg: float,
    offaxis_deg: float,
    theta_planar: float | None,
    strict_exclusion_zone: bool,
) -> str:
    """Short text for logs: |α| branch vs exclusion zone and S.1503-4 D3.1.2 gain test.

    Branches:
      - |α| ≥ α₀: geometry outside the angular exclusion zone (does not require the gain test).
      - |α| < α₀: included only if GRX(φ) > min(−30 dB, GRX(α₀)) (relative to the peak).
    """
    if strict_exclusion_zone and abs(alpha_deg) < alpha0_deg:
        return "strict exclusion (no gain OR)"
    if abs(alpha_deg) >= alpha0_deg:
        return "|α|≥α₀"
    g_rel = es_antenna.relative_gain(offaxis_deg, theta_planar)
    ga0 = es_antenna.relative_gain(alpha0_deg, theta_planar)
    thr = min(-30.0, ga0)
    lim = "-30 dB" if abs(thr + 30.0) < 1e-9 else "GRX(α₀)"
    return f"|α|<α₀ ∧ GRX(φ)>{thr:.1f}dB (ref {lim}; GRX(φ)={g_rel:.1f}dB)"


# =====================================================================
#  Simple non-GSO satellite antenna pattern
# =====================================================================

class SimpleCircularBeamAntenna:
    """Simple antenna pattern with a circular beam (cos^n roll-off)."""

    def __init__(self, peak_gain_dbi: float, half_power_beamwidth_deg: float):
        self.peak_gain = peak_gain_dbi
        self.hpbw = half_power_beamwidth_deg

        # Exponent n for cos^n such that gain = -3 dB at θ = HPBW/2
        theta_3db = half_power_beamwidth_deg / 2.0
        if theta_3db > 0:
            self.n = -3.0 / (10.0 * math.log10(
                max(math.cos(theta_3db * DEG2RAD), 1e-10)
            ))
        else:
            self.n = 1.0

    def gain(self, off_axis_deg: float) -> float:
        """Gain (dBi) for the off-axis angle."""
        phi = abs(off_axis_deg)
        if phi >= 90.0:
            return self.peak_gain - 30.0  # Floor
        cos_val = math.cos(phi * DEG2RAD)
        if cos_val <= 0:
            return self.peak_gain - 30.0
        g = self.peak_gain + 10.0 * self.n * math.log10(cos_val)
        return max(g, self.peak_gain - 30.0)


# =====================================================================
#  Factories
# =====================================================================

def create_gso_es_antenna(
    diameter_m: float,
    frequency_ghz: float,
    efficiency: float = 0.65,
    service: str = "FSS",
) -> EarthStationAntenna:
    """Creates the standard GSO ES antenna according to the selected service."""
    service_norm = str(service or "FSS").strip().upper()
    if service_norm == "BSS":
        return ITUBO1443Antenna(diameter_m, frequency_ghz, efficiency)
    return ITURS1428Antenna(diameter_m, frequency_ghz, efficiency)
