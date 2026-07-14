"""constellation_templates.py — known constellation patterns for manual entry.

Constellation wizard of the manual/parametric path (R3/R4, plan WS2 item 4).
Every generator returns a **plane list in the public YAML schema** consumed by
``create_constellation_from_config`` (``non_gso.planes``): the user can edit
plane-by-plane before running, and the same structure serialises to
``config.yaml``. Nothing here bypasses the engine — templates only PRE-FILL
per-plane elements (RAAN O[N], ω W[N], per-satellite phases V[N] per
S.1503-4 §B3.2 / §D6.3.7).

Patterns (real-filing first): Walker Delta · Walker Star (polar) ·
equatorial ring · single plane/train · multi-shell composition · Molniya ·
Tundra · IGSO. Cross-cutting helpers: sun-synchronous inclination and
repeat-ground-track semi-major axis.
"""
from __future__ import annotations

import math

from .constants import J2, MU_KM3_S2, RE_KM

# Sidereal day (s) — S.1503-4 Table 2 value of Earth rotation rate.
T_SIDEREAL_S = 86164.09


def _plane(orb_id: int, n_sats: int, *, a_km: float | None = None,
           apogee_km: float | None = None, perigee_km: float | None = None,
           e: float = 0.0, i_deg: float, raan_deg: float,
           omega_deg: float = 0.0, phases_deg: list[float] | None = None) -> dict:
    out: dict = {
        "orb_id": int(orb_id),
        "sats_per_plane": int(n_sats),
        "inclination_deg": float(i_deg),
        "raan_deg": float(raan_deg % 360.0),
        "perigee_arg_deg": float(omega_deg % 360.0),
    }
    if apogee_km is not None and perigee_km is not None:
        out["apogee_km"] = float(apogee_km)
        out["perigee_km"] = float(perigee_km)
    else:
        out["semi_major_axis_km"] = float(a_km)
        out["eccentricity"] = float(e)
    if phases_deg is not None:
        out["phase_angles_deg"] = [float(p) % 360.0 for p in phases_deg]
    return out


def walker(
    *,
    total_sats: int,
    num_planes: int,
    phasing_factor: int = 1,
    inclination_deg: float,
    altitude_km: float,
    raan0_deg: float = 0.0,
    raan_spread_deg: float = 360.0,
    omega_deg: float = 0.0,
    first_orb_id: int = 1,
) -> list[dict]:
    """Walker pattern ``i:T/P/F`` as a plane list.

    ``raan_spread_deg`` generalises the two classic families:
    360° → **Walker Delta** (planes over the full node circle);
    180° → **Walker Star** (polar/street-of-coverage, counter-rotating seam).
    Phasing between planes is ``F·360/T`` (V[N] per satellite).
    """
    if num_planes < 1 or total_sats < num_planes:
        raise ValueError("num_planes >= 1 and total_sats >= num_planes required")
    if total_sats % num_planes:
        raise ValueError("total_sats must be a multiple of num_planes")
    spp = total_sats // num_planes
    d_raan = float(raan_spread_deg) / num_planes
    d_m = 360.0 / spp
    d_phase = float(phasing_factor) * 360.0 / total_sats
    return [
        _plane(
            first_orb_id + p, spp,
            a_km=RE_KM + altitude_km, e=0.0,
            i_deg=inclination_deg,
            raan_deg=raan0_deg + p * d_raan,
            omega_deg=omega_deg,
            phases_deg=[(s * d_m + p * d_phase) % 360.0 for s in range(spp)],
        )
        for p in range(num_planes)
    ]


def walker_delta(**kw) -> list[dict]:
    """Walker Delta (RAAN over 360°) — e.g. Starlink shells, Galileo."""
    kw.setdefault("raan_spread_deg", 360.0)
    return walker(**kw)


def walker_star(**kw) -> list[dict]:
    """Walker Star (near-polar, RAAN over 180°) — e.g. Iridium, OneWeb."""
    kw.setdefault("raan_spread_deg", 180.0)
    kw.setdefault("inclination_deg", 87.9)
    return walker(**kw)


def equatorial_ring(*, n_sats: int, altitude_km: float,
                    raan_deg: float = 0.0, first_orb_id: int = 1) -> list[dict]:
    """Single equatorial plane (i≈0) — e.g. O3b (MEO ~8 062 km)."""
    return train(n_sats=n_sats, altitude_km=altitude_km, inclination_deg=0.0,
                 raan_deg=raan_deg, first_orb_id=first_orb_id)


def train(*, n_sats: int, altitude_km: float, inclination_deg: float,
          raan_deg: float = 0.0, omega_deg: float = 0.0,
          phase0_deg: float = 0.0, first_orb_id: int = 1) -> list[dict]:
    """One plane, N satellites evenly spaced in mean anomaly."""
    d = 360.0 / max(1, n_sats)
    return [_plane(first_orb_id, n_sats, a_km=RE_KM + altitude_km, e=0.0,
                   i_deg=inclination_deg, raan_deg=raan_deg,
                   omega_deg=omega_deg,
                   phases_deg=[(phase0_deg + s * d) % 360.0 for s in range(n_sats)])]


def molniya(*, n_planes: int = 3, sats_per_plane: int = 1,
            apogee_km: float = 39_354.0, perigee_km: float = 1_000.0,
            raan0_deg: float = 0.0, first_orb_id: int = 1) -> list[dict]:
    """Molniya-type HEO: critical inclination 63.4°, ω=270° (apogee north)."""
    d_raan = 360.0 / n_planes
    d_m = 360.0 / max(1, sats_per_plane)
    return [
        _plane(first_orb_id + p, sats_per_plane,
               apogee_km=apogee_km, perigee_km=perigee_km,
               i_deg=63.4, raan_deg=raan0_deg + p * d_raan, omega_deg=270.0,
               phases_deg=[s * d_m for s in range(sats_per_plane)])
        for p in range(n_planes)
    ]


def tundra(*, n_planes: int = 3, raan0_deg: float = 0.0,
           ecc: float = 0.27, first_orb_id: int = 1) -> list[dict]:
    """Tundra-type: 24 h geosynchronous HEO, i=63.4°, ω=270° (Sirius-like)."""
    a = (MU_KM3_S2 * (T_SIDEREAL_S / (2.0 * math.pi)) ** 2) ** (1.0 / 3.0)
    d_raan = 360.0 / n_planes
    return [
        _plane(first_orb_id + p, 1, a_km=a, e=ecc, i_deg=63.4,
               raan_deg=raan0_deg + p * d_raan, omega_deg=270.0,
               phases_deg=[p * 360.0 / n_planes])
        for p in range(n_planes)
    ]


def igso(*, n_sats: int = 3, inclination_deg: float = 43.0,
         raan0_deg: float = 0.0, first_orb_id: int = 1) -> list[dict]:
    """Inclined geosynchronous (figure-eight ground track, QZSS-like)."""
    a = (MU_KM3_S2 * (T_SIDEREAL_S / (2.0 * math.pi)) ** 2) ** (1.0 / 3.0)
    d = 360.0 / max(1, n_sats)
    return [
        _plane(first_orb_id + k, 1, a_km=a, e=0.0, i_deg=inclination_deg,
               raan_deg=raan0_deg + k * d, phases_deg=[(-(k * d)) % 360.0])
        for k in range(n_sats)
    ]


def multi_shell(*shells: list[dict]) -> list[dict]:
    """Compose several templates into one system (Starlink/Kuiper-style).

    Re-numbers ``orb_id`` sequentially so every plane keeps a unique id —
    mirroring how multi-shell filings appear as distinct orbit groups in the
    SRS.
    """
    out: list[dict] = []
    oid = 1
    for shell in shells:
        for pl in shell:
            q = dict(pl)
            q["orb_id"] = oid
            oid += 1
            out.append(q)
    return out


# ── Cross-cutting helpers ────────────────────────────────────────────────

SUN_SYNC_RAAN_RATE_RAD_S = math.radians(360.0 / 365.2422) / 86400.0  # ≈0.9856°/day


def sun_synchronous_inclination_deg(altitude_km: float, e: float = 0.0) -> float:
    """Inclination giving Ω̇ = +0.9856°/day at the given altitude (J2 secular,
    S.1503-4 D6.3.2 eq 21 geometry)."""
    a = RE_KM + float(altitude_km)
    p = a * (1.0 - e * e)
    n0 = math.sqrt(MU_KM3_S2 / a ** 3)
    k = 1.5 * J2 * (RE_KM / p) ** 2
    cos_i = -SUN_SYNC_RAAN_RATE_RAD_S / (k * n0)
    if not -1.0 <= cos_i <= 1.0:
        raise ValueError(f"no sun-synchronous inclination at {altitude_km:.0f} km")
    return math.degrees(math.acos(cos_i))


def repeat_ground_track_a_km(revs: int, days: int = 1) -> float:
    """Semi-major axis whose (point-mass) period closes the ground track after
    ``revs`` revolutions in ``days`` sidereal days — feeds DoesRepeat/§B3.1."""
    T = days * T_SIDEREAL_S / float(revs)
    return (MU_KM3_S2 * (T / (2.0 * math.pi)) ** 2) ** (1.0 / 3.0)
