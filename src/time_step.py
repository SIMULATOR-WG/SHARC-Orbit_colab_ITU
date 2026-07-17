"""
time_step.py — Time step and number-of-time-steps computation.

Implements the logic of ITU-R S.1503-4 Section D.4 to determine:
  • TSTEP  — time step (s)
  • NSTEPS — total number of time steps

For repeating and non-repeating constellations, applies the procedures
of D4.6.1 and D4.6.2 respectively, including statistical Nmin (Table 13).

The algorithm supports a dual time step:
  • Coarse step: used during most of the simulation
  • Fine step: used in critical regions (S.1503 gain-based mode)
    or in the legacy α-threshold-based mode
"""

from __future__ import annotations
import math
import logging
from typing import NamedTuple, TYPE_CHECKING
from .constants import RE_KM, MU_KM3_S2, OMEGA_E, J2, RAD2DEG

if TYPE_CHECKING:
    from .antenna import EarthStationAntenna

logger = logging.getLogger(__name__)

# S.1503-4 §D4.1: tolerance on the number of time steps above which the
# resolution of non-repeating runs is reduced (the "1e8" recalculation).
NSTEPS_TOLERANCE = 1e8


class TimeStepResult(NamedTuple):
    """Result of :func:`compute_time_step_and_count`.

    Attributes
    ----------
    tstep_s : fine time step Δt (s).
    nsteps : total number of time steps.
    raan_dot_artificial_rad_s : artificial nodal precession rate (rad/s); 0 if off.
    nhit_eff : the Nhit actually used — equals the requested ``nhit`` unless the
        §D4.1 1e8 procedure reduced it (N'hit) for a non-repeating run.
    ncoarse : Ncoarse for the dual time step, consistent with ``nhit_eff``
        (i.e. N'coarse when the 1e8 reduction fired).
    """

    tstep_s: float
    nsteps: int
    raan_dot_artificial_rad_s: float
    nhit_eff: float
    ncoarse: int


def compute_orbital_period(a_km: float) -> float:
    """Orbital period (s) from the semi-major axis."""
    return 2.0 * math.pi * math.sqrt(a_km**3 / MU_KM3_S2)


def repeat_track_is_physical(
    rpt_period_s: float, a_km: float, tol_orbits: float = 0.02,
) -> tuple[bool, float]:
    """Whether a declared repeat period closes the unperturbed ground track.

    DIAGNOSTIC ONLY — the §D4.6.1/§D4.6.2 branch is keyed on the SRS
    ``orbit.f_stn_keep`` flag (station keeping actively holds the declared
    track, so closure is enforced operationally even when the unperturbed
    Kepler track would drift). This matches the BR software: the official
    EPFDRESULTS test runs dimension Skybridge (ntc101, f_stn_keep=Y,
    5.56 orbits/repeat — does NOT close) as repeating, and Boeing (ntc102,
    f_stn_keep=N, ~2.03 orbits/day — nearly closes) as non-repeating.

    Returns ``(is_physical, n_orbits_in_repeat)``.
    """
    if rpt_period_s <= 0.0 or a_km <= 0.0:
        return False, 0.0
    t_orb = compute_orbital_period(a_km)
    if t_orb <= 0.0:
        return False, 0.0
    n_orb = rpt_period_s / t_orb
    return abs(n_orb - round(n_orb)) <= tol_orbits, n_orb


def compute_downlink_fine_step_s1503(
    i_deg: float,
    h_km: float,
    theta_3db_deg: float,
    nhit: float = 16,
) -> float:
    """Compute Tfine (Δtref) per S.1503-4 §D4.2 (epfd↓)."""
    if nhit <= 0:
        raise ValueError("nhit must be > 0")
    if theta_3db_deg <= 0.0:
        raise ValueError("theta_3db_deg must be > 0")

    # S.1503-4 D4.2: φ = (θ₃dB/2) − arcsin[ Re/(Re+h) × sin(θ₃dB/2) ]
    half_bw = float(theta_3db_deg) / 2.0
    sin_arg = (RE_KM / (RE_KM + max(0.0, h_km))) * math.sin(math.radians(half_bw))
    sin_arg = max(-1.0, min(1.0, sin_arg))
    phi_deg = half_bw - math.degrees(math.asin(sin_arg))
    phi_deg = max(phi_deg, 1e-9)

    # Eq. (3): ωs in degrees/s (form equivalent to the recommendation's expression)
    omega_s_deg_s = 0.071 / ((1.0 + max(0.0, h_km) / RE_KM) ** 1.5)
    omega_e_deg_s = math.degrees(OMEGA_E)
    i_rad = math.radians(i_deg)
    omega_deg_s = math.sqrt(
        (omega_s_deg_s * math.cos(i_rad) - omega_e_deg_s) ** 2
        + (omega_s_deg_s * math.sin(i_rad)) ** 2
    )
    omega_deg_s = max(omega_deg_s, 1e-12)

    # Eq. (2), then Eq. (1).  S.1503-4 §D4.1 can reduce Nhit to a
    # fractional N'hit for non-repeating orbits above the 1e8-step threshold.
    dt_pass_s = (2.0 * phi_deg) / omega_deg_s
    dt_ref_s = dt_pass_s / float(nhit)

    # S.1503: round to the nearest non-zero millisecond.
    dt_ms = max(1, int(round(dt_ref_s * 1000.0)))
    return dt_ms / 1000.0


def compute_ncoarse_s1503(
    theta_3db_deg: float,
    nhit: float = 16,
    phi_coarse_deg: float = 1.5,
) -> int:
    """Compute Ncoarse per S.1503-4 §D4.7."""
    if theta_3db_deg <= 0.0:
        raise ValueError("theta_3db_deg must be > 0")
    if nhit <= 0:
        raise ValueError("nhit must be > 0")
    if phi_coarse_deg <= 0.0:
        raise ValueError("phi_coarse_deg must be > 0")

    ncoarse = int(math.floor((float(nhit) * float(phi_coarse_deg)) / float(theta_3db_deg)))
    return max(1, ncoarse)


def compute_nmin_s1503(
    min_exceedance_pct: float | None,
    ns_samples: int = 10,
) -> int:
    """Compute Nmin per S.1503-4 D4.6 (Table 13).

    Table 13 states Nmin = NS * 100 / (100 - p_max), with p_max the largest
    percentage below 100% in the Article 22 limit tables, expressed there as
    "% of time during which the epfd may NOT be exceeded" (e.g. 99.7%).
    This codebase stores the limits in the EXCEEDANCE (CCDF) convention —
    ``[-163, 0.3]`` = exceeded for at most 0.3% of the time — so the
    equivalent form used here is::

        Nmin = ceil(NS * 100 / q),  q = 100 - p_max

    where ``q`` is the SMALLEST strictly positive exceedance percentage in
    the limit table (0.3% ↔ 99.7%).
    """
    if ns_samples <= 0:
        raise ValueError("ns_samples must be > 0")
    if min_exceedance_pct is None:
        return ns_samples * 100

    q = float(min_exceedance_pct)
    if not (0.0 < q <= 100.0):
        raise ValueError("min_exceedance_pct must be in (0, 100]")
    return max(1, int(math.ceil((ns_samples * 100.0) / q)))


def _compute_j2_rates(a_km: float, e: float, i_deg: float) -> tuple[float, float, float]:
    """Return (n, Ωr, ωr) in rad/s per D6.3.2 (J2 secular model)."""
    i_rad = math.radians(i_deg)
    p = a_km * (1.0 - e * e)
    if p <= 0.0:
        raise ValueError("Invalid orbital parameters: p <= 0")
    n0 = math.sqrt(MU_KM3_S2 / (a_km ** 3))
    eta = math.sqrt(max(0.0, 1.0 - e * e))
    k = 1.5 * J2 * (RE_KM / p) ** 2
    cos_i = math.cos(i_rad)
    sin_i = math.sin(i_rad)

    # Secular equations from D6.3.2 eqs (20)-(22) — Ωr and ωr are driven by
    # the J2-corrected mean motion n̄ (eq 20), NOT the point-mass n0.
    n_bar = n0 * (1.0 + k * eta * (1.0 - 1.5 * sin_i * sin_i))  # n̄  (eq 20)
    omega_r = k * n_bar * (2.0 - 2.5 * sin_i * sin_i)           # ωr (eq 22)
    Omega_r = -k * n_bar * cos_i                                # Ωr (eq 21)
    return n_bar, Omega_r, omega_r


def _d42_altitude_km(a_km: float, e: float, min_operating_height_km: float) -> float:
    """Altitude used in §D4.2: ``max(a(1−e)−Re, H_min)`` (H_min from SRS, km)."""
    h_perigee = max(0.0, a_km * (1.0 - e) - RE_KM)
    h_op = max(0.0, float(min_operating_height_km or 0.0))
    return max(h_perigee, h_op)


def compute_time_step_and_count(
    a_km: float,
    e: float,
    i_deg: float,
    num_planes: int,
    sats_per_plane: int,
    min_elevation_deg: float,
    min_operating_height_km: float = 0.0,
    repeating_ground_track: bool = False,
    repeat_period_days: float = 1.0,
    desired_epfd_bins: int = 1000,
    artificial_precession: bool = False,
    theta_3db_deg: float | None = None,
    nhit: int = 16,
    literal_s1503_d42: bool = True,
    min_exceedance_pct: float | None = None,
    ns_samples: int = 10,
    ntracks: int = 16,
    phi_coarse_deg: float = 1.5,
    n_sat_total: int | None = None,
    reduce_ntracks_1e8: bool = False,
) -> TimeStepResult:
    """Compute TSTEP (s) and NSTEPS per S.1503-4, Section D.4.

    Parameters
    ----------
    a_km : semi-major axis (km)
    e : eccentricity
    i_deg : inclination (°)
    num_planes : number of orbital planes
    sats_per_plane : satellites per plane
    min_elevation_deg : minimum elevation (°)
    min_operating_height_km : minimum operating altitude (SRS), km; 0 = perigee only
    repeating_ground_track : whether the ground track is repeating
    repeat_period_days : repeat period (days), if repeating
    min_exceedance_pct : smallest strictly positive exceedance (CCDF) percentage
        in the Article 22 limit tables, for Nmin (D4.6, Table 13)
    desired_epfd_bins : legacy fallback parameter
    artificial_precession : if True, returns raan_dot_artificial for
        non-repeating orbits (S.1503-4 D4.6.2, D6.3.5)
    phi_coarse_deg : φcoarse (°) for Ncoarse (D4.7); also used by the §D4.1
        1e8 recalculation to derive N'coarse.
    n_sat_total : real satellite count (Σ nbr_sat_pl over ALL planes) for the
        √Nsatellites factor of §D4.1. Required for heterogeneous filings —
        ``num_planes × sats_per_plane`` uses the reference plane only and
        undercounts (e.g. CRC STEAM-2: 129×1 vs 1312 real). None = fall back
        to ``num_planes × sats_per_plane``.
    reduce_ntracks_1e8 : reading toggle for the §D4.1 recalculation.
        §D4.5 states Ntrack = Nhit, so "re-calculate ... run time" can be read
        as N'track = N'hit (Sreq widens, Norbits collapses). The official ITU
        engines split by version: Agenium ('A', BR_Space v10, MCSAT 2026) and
        Transfinite v5.35 ('T', MCSAT 2018) keep Ntracks=16 (reading A);
        Transfinite v5.45 ('T', GIBC ≤ v9 — CRC STEAM-2 2021, USASAT-NGSO-3X
        2025) reduces Ntracks (reading B). Δt is unaffected either way.
        False = reading A (default), True = reading B.

    Returns
    -------
    TimeStepResult(tstep_s, nsteps, raan_dot_artificial_rad_s, nhit_eff, ncoarse)

    Notes
    -----
    For non-repeating constellations, S.1503-4 §D4.1 caps the computational
    effort: if the run computed with Nhit dimensions more than ``NSTEPS_TOLERANCE``
    (1e8) time steps, the tracking resolution is reduced to N'hit =
    Nhit / min(Ncoarse, √Nsatellites) and the time step / run time are
    recalculated. The dual-step Ncoarse is updated to
    N'coarse = floor((N'hit/Nhit)·Ncoarse) accordingly.
    """
    T_orb = compute_orbital_period(a_km)
    # §D4.1 √Nsatellites: real fleet size. num_planes × sats_per_plane assumes
    # a homogeneous constellation and undercounts heterogeneous filings.
    if n_sat_total is not None and n_sat_total > 0:
        total_sats = int(n_sat_total)
    else:
        total_sats = max(1, num_planes * sats_per_plane)

    def _fine_step(nhit_local: float) -> float:
        """Fine Δt for a given Nhit (S.1503-4 §D4.2 when literal, else legacy)."""
        if literal_s1503_d42 and theta_3db_deg is not None and theta_3db_deg > 0.0:
            # S.1503-4 §D4.2: altitude for φ and ωs (perigee and/or declared H_min).
            h_min_km = _d42_altitude_km(a_km, e, min_operating_height_km)
            return compute_downlink_fine_step_s1503(
                i_deg=i_deg,
                h_km=h_min_km,
                theta_3db_deg=theta_3db_deg,
                nhit=nhit_local,
            )
        # Legacy heuristic fallback (independent of Nhit).
        r_perigee = a_km * (1.0 - e)
        v_perigee = math.sqrt(MU_KM3_S2 * (2.0 / r_perigee - 1.0 / a_km))
        omega_sat = v_perigee / r_perigee  # rad/s
        angle_per_step = 0.1  # degrees
        tstep_from_angular = math.radians(angle_per_step) / omega_sat
        tstep_max = T_orb / max(total_sats * 10, 100)
        ts = min(tstep_from_angular, tstep_max)
        return max(ts, 0.01)  # Minimum of 10 ms

    def _ncoarse_for(nhit_local: float) -> int:
        """Ncoarse (D4.7) for a given Nhit; 1 when no beamwidth is available."""
        if theta_3db_deg is not None and theta_3db_deg > 0.0:
            return compute_ncoarse_s1503(
                theta_3db_deg=theta_3db_deg,
                nhit=max(1, int(round(nhit_local))),
                phi_coarse_deg=phi_coarse_deg,
            )
        return 1

    # D4.6 (Table 13): minimum number of steps for statistical significance.
    try:
        nmin_s1503 = compute_nmin_s1503(
            min_exceedance_pct=min_exceedance_pct,
            ns_samples=ns_samples,
        )
    except ValueError:
        # Robustness fallback if configuration is out of range.
        nmin_s1503 = max(1, desired_epfd_bins * 10)

    ntracks_eff = max(1, int(ntracks))

    s_artificial_deg_orbit = 0.0
    t_period_s = 0.0

    # Total duration
    if repeating_ground_track:
        # D4.6.1 Repeating orbits (literal). The 1e8 procedure of §D4.1 is
        # explicitly limited to non-repeating orbits, so it is not applied here.
        nhit_eff = float(nhit)
        ncoarse_eff = _ncoarse_for(nhit)
        tstep = _fine_step(nhit)
        P_repeat = max(1.0, float(repeat_period_days) * 86400.0)
        nrepsteps = P_repeat / tstep
        nrepsteps_round = int(round(nrepsteps))
        # Exact-divisor test in integer milliseconds (S.1503 steps are
        # ms-quantized). The absolute float test |x - round(x)| < 1e-12 misses
        # exact divisors not representable in binary for Nrepsteps ~1e5-1e8
        # (e.g. P=86400 s, tstep=0.675 s → error 1.5e-11, yet
        # 86_400_000 % 675 == 0).
        p_ms = int(round(P_repeat * 1000.0))
        dt_ms = int(round(tstep * 1000.0))
        ms_quantized = (
            dt_ms > 0
            and abs(P_repeat * 1000.0 - p_ms) < 1e-6
            and abs(tstep * 1000.0 - dt_ms) < 1e-6
        )
        if ms_quantized:
            divides_exactly = (p_ms % dt_ms == 0)
            if divides_exactly:
                nrepsteps_round = p_ms // dt_ms
        else:
            # Non-ms-quantized step (legacy fallback): relative tolerance.
            divides_exactly = (
                nrepsteps_round > 0
                and abs(nrepsteps - nrepsteps_round) < 1e-9 * max(1.0, nrepsteps)
            )
        if divides_exactly and nrepsteps_round > 0:
            # Tstep' = Tstep * (1 + Nrepsteps) / Nrepsteps
            tstep = tstep * (1.0 + nrepsteps_round) / nrepsteps_round
            logger.info(
                "D4.6.1 adjustment: Tstep divides Prepeat exactly; "
                f"using Tstep'={tstep:.6f}s"
            )

        tsig = nmin_s1503 * tstep
        nrep = max(1, int(math.ceil(tsig / P_repeat)))
        nrun = max(nrep, ntracks_eff)
        trun_target = nrun * P_repeat
        nsteps = max(1, int(math.floor(trun_target / tstep)))
        total_time = nsteps * tstep
    else:
        # D4.6.2 Non-repeating orbits (literal)
        if theta_3db_deg is None or theta_3db_deg <= 0.0:
            raise ValueError("D4.6.2 requires theta_3db_deg > 0")

        def _run_nonrepeating(nhit_local: float, ntracks_local: float | None = None) -> dict:
            """Full D4.6.2 dimensioning for a given Nhit (incl. Nmin extension)."""
            if ntracks_local is None:
                ntracks_local = float(ntracks_eff)
            tstep_l = _fine_step(nhit_local)
            half_bw = float(theta_3db_deg) / 2.0
            h_min_km = _d42_altitude_km(a_km, e, min_operating_height_km)
            sin_arg = (RE_KM / (RE_KM + h_min_km)) * math.sin(math.radians(half_bw))
            sin_arg = max(-1.0, min(1.0, sin_arg))
            phi_deg = half_bw - math.degrees(math.asin(sin_arg))
            phi_deg = max(phi_deg, 1e-12)

            n_rad_s, omega_raan_rad_s, omega_argp_rad_s = _compute_j2_rates(a_km=a_km, e=e, i_deg=i_deg)
            n_deg_min = n_rad_s * RAD2DEG * 60.0
            omega_raan_deg_min = omega_raan_rad_s * RAD2DEG * 60.0
            omega_argp_deg_min = omega_argp_rad_s * RAD2DEG * 60.0
            omega_e_deg_min = 0.250684

            nodal_rate_deg_min = omega_argp_deg_min + n_deg_min
            if abs(nodal_rate_deg_min) < 1e-12:
                raise ValueError("D4.6.2 invalid: nodal rate ~0 deg/min")

            # Step 3
            p_n_min = 360.0 / nodal_rate_deg_min
            # Step 4
            s_pass_l = abs((omega_e_deg_min - omega_raan_deg_min) * p_n_min)
            if s_pass_l < 1e-12:
                raise ValueError("D4.6.2 invalid: Spass ~0")

            # Step 5 — Ntrack stays at 16 through the §D4.1 recalculation
            # (Agenium convention; Transfinite reduces it — see
            # reduce_ntracks_1e8).
            s_req = (2.0 * phi_deg) / float(ntracks_local)
            if s_req <= 0.0:
                raise ValueError("D4.6.2 invalid: Sreq <= 0")
            # Step 6 + 7
            n_orbits_l = max(1, int(math.ceil(180.0 / s_req)))
            # Step 8
            s_total = n_orbits_l * s_pass_l
            # Step 9
            n360_l = int(math.floor(s_total / 360.0))
            # Step 10
            s_actual_l = (360.0 * n360_l) / float(n_orbits_l)
            # Step 11
            s_art_l = s_actual_l - s_pass_l

            t_period_l = abs(p_n_min) * 60.0
            trun_target = t_period_l * n_orbits_l
            nsteps_l = max(1, int(math.floor(trun_target / tstep_l)))

            # Ensure minimum Nmin for statistical significance (Table 13).
            if nsteps_l < nmin_s1503:
                mult = int(math.ceil(nmin_s1503 / max(1, nsteps_l)))
                n_orbits_l *= max(1, mult)
                trun_target = t_period_l * n_orbits_l
                nsteps_l = max(1, int(math.floor(trun_target / tstep_l)))
                s_total = n_orbits_l * s_pass_l
                n360_l = int(math.floor(s_total / 360.0))
                s_actual_l = (360.0 * n360_l) / float(n_orbits_l)
                s_art_l = s_actual_l - s_pass_l

            return {
                "tstep": tstep_l,
                "nsteps": nsteps_l,
                "n_orbits": n_orbits_l,
                "n360": n360_l,
                "s_pass": s_pass_l,
                "s_req": s_req,
                "s_actual": s_actual_l,
                "s_artificial_deg_orbit": s_art_l,
                "t_period_s": t_period_l,
            }

        # Step 1: dimension with the requested Nhit.
        res = _run_nonrepeating(nhit)
        nhit_eff = float(nhit)
        ncoarse_orig = _ncoarse_for(nhit)
        ncoarse_eff = ncoarse_orig

        # S.1503-4 §D4.1: if the run exceeds the 1e8 tolerance, reduce the
        # tracking resolution (N'hit) and recalculate time step and run time.
        if res["nsteps"] > NSTEPS_TOLERANCE:
            factor = max(1.0, min(float(ncoarse_orig), math.sqrt(float(total_sats))))
            # N'hit = Nhit / min(Ncoarse, √Nsatellites).  The Recommendation
            # does not round or clamp this value; for large constellations it
            # can be fractional (e.g. 16/20 = 0.8).
            nhit_prime = float(nhit) / factor
            ntracks_prime = nhit_prime if reduce_ntracks_1e8 else float(ntracks_eff)
            res_reduced = _run_nonrepeating(nhit_prime, ntracks_prime)
            # N'coarse = floor((N'hit/Nhit)·Ncoarse); TS'coarse = TS'·N'coarse.
            ncoarse_eff = max(1, int(math.floor((nhit_prime / float(nhit)) * ncoarse_orig)))
            logger.info(
                "D4.1 1e8 recalculation: nsteps=%d > %.0e with Nhit=%g; "
                "N'hit=%.4f (factor min(Ncoarse=%d, √Nsat=%.2f)=%.4f), "
                "N'coarse=%d, new nsteps=%d (Δt %.6fs → %.6fs)",
                res["nsteps"], NSTEPS_TOLERANCE, float(nhit),
                nhit_prime, ncoarse_orig, math.sqrt(float(total_sats)), factor,
                ncoarse_eff, res_reduced["nsteps"], res["tstep"], res_reduced["tstep"],
            )
            res = res_reduced
            nhit_eff = nhit_prime
            if res["nsteps"] > NSTEPS_TOLERANCE:
                # The §D4.1 procedure is a single recalculation (no loop).
                # Under the Agenium convention (Ntrack kept at 16) the run
                # length can stay above the tolerance; flag it rather than
                # deviate.
                logger.warning(
                    "D4.1: nsteps=%d still exceeds %.0e after the N'hit "
                    "recalculation (run length driven by Ntrack=%g; the "
                    "Agenium convention keeps it — set reduce_ntracks_1e8 "
                    "for the Transfinite reading of §D4.1).",
                    res["nsteps"], NSTEPS_TOLERANCE, ntracks_prime,
                )

        tstep = res["tstep"]
        nsteps = res["nsteps"]
        s_artificial_deg_orbit = res["s_artificial_deg_orbit"]
        t_period_s = res["t_period_s"]
        total_time = nsteps * tstep

        logger.info(
            "D4.6.2 (non-repeating): "
            f"Spass={res['s_pass']:.6f}°, Sreq={res['s_req']:.6f}°, "
            f"Norbits={res['n_orbits']}, N360={res['n360']}, "
            f"Sactual={res['s_actual']:.6f}°, Sart={s_artificial_deg_orbit:.6f}°/orbit"
        )

    # Artificial precession (S.1503-4 D4.6.2, D6.3.5): accelerates the nodal
    # precession for better sampling in non-repeating orbits.
    raan_dot_artificial_rad_s = 0.0
    if artificial_precession and not repeating_ground_track:
        if abs(math.sin(math.radians(i_deg))) < 1e-6:
            # D6.3.6 (Fig. 52 note): an equatorial (i≈0) constellation is
            # Case 1 with the FORCED precession set to zero — the ascending
            # node is undefined, so accelerating it is meaningless.
            logger.info(
                "Equatorial orbit (i≈0): forced (artificial) precession set to "
                "zero per S.1503-4 D6.3.6 note."
            )
        else:
            # Dartificial (deg/s) = Sartificial(deg/orbit) / Tperiod(s), reusing
            # the D4.6.2 terms computed in the main block above — including the
            # Nmin n_orbits adjustment — so the artificial rate matches the run
            # that was actually dimensioned.
            d_artificial_deg_s = s_artificial_deg_orbit / t_period_s
            raan_dot_artificial_rad_s = math.radians(d_artificial_deg_s)

            T_run = nsteps * tstep
            logger.info(
                f"Artificial precession active: Ω̇_art = {math.degrees(raan_dot_artificial_rad_s):.6f} °/s "
                f"(T_run={T_run/3600:.2f} h)"
            )

    logger.info(
        f"Time step: {tstep:.4f} s, "
        f"N_steps: {nsteps}, "
        f"Duration: {nsteps * tstep / 3600:.1f} h, "
        f"T_orb: {T_orb:.1f} s ({T_orb/60:.1f} min)"
    )

    return TimeStepResult(
        tstep_s=tstep,
        nsteps=nsteps,
        raan_dot_artificial_rad_s=raan_dot_artificial_rad_s,
        nhit_eff=float(nhit_eff),
        ncoarse=int(ncoarse_eff),
    )


def group_sub_constellations(planes: list[dict]) -> list[dict]:
    """Group per-plane records into the §D4.1 dimensioning sets.

    Planes sharing the same orbit geometry — (a, e, i) rounded to 1 km /
    1e-3 / 0.1° — form one sub-constellation. Input dicts use the
    ``srs_to_constellation_config`` plane keys (``semi_major_axis_km``,
    ``eccentricity``, ``inclination_deg``, ``sats_per_plane``,
    ``min_operating_height_km``); the output dicts carry the per-sub
    kwargs consumed by :func:`compute_time_step_and_count`.
    """
    groups: dict[tuple, dict] = {}
    for p in planes:
        # §D4 dimensioning altitude (a_km_d4, apogee/perigee geometry first)
        # when the loader provides it; the plain semi-major axis (propagation
        # field, op-height first) otherwise. See srs_to_constellation_config.
        a = float(p.get("a_km_d4") or p.get("semi_major_axis_km", 0.0) or 0.0)
        ecc = float(p.get("eccentricity", 0.0) or 0.0)
        inc = float(p.get("inclination_deg", 0.0) or 0.0)
        key = (round(a), round(ecc, 3), round(inc, 1))
        g = groups.setdefault(key, {
            "a_km": a, "e": ecc, "i_deg": inc,
            "num_planes": 0, "_n_sats": 0, "_min_ops": [],
        })
        g["num_planes"] += 1
        g["_n_sats"] += int(p.get("sats_per_plane", 0) or 0)
        h_op = float(p.get("min_operating_height_km", 0.0) or 0.0)
        if h_op > 0.0:
            g["_min_ops"].append(h_op)
    subs = []
    for g in groups.values():
        n_sats = g.pop("_n_sats")
        min_ops = g.pop("_min_ops")
        g["sats_per_plane"] = max(1, n_sats // max(1, g["num_planes"]))
        g["min_operating_height_km"] = min(min_ops) if min_ops else 0.0
        subs.append(g)
    return subs


def compute_time_step_and_count_multi(
    sub_constellations: list[dict],
    **kwargs,
) -> TimeStepResult:
    """§D4.1 multi-sub-constellation rule over :func:`compute_time_step_and_count`.

    S.1503-4 §D4.1: "If there are multiple sets, e.g. for multiple
    sub-constellations, then the longest run time and smallest time step over
    all sub-constellations should be used." Each set is dimensioned separately
    with its OWN (a, e, i, planes, H_min) — not just the lowest altitude — and
    the combined result carries Δt = min over sets and
    NSTEPS = floor(max Trun / min Δt).

    Validated against the 'T' v5.45 EPFDRESULTS (CRC STEAM-2 A22+97B,
    USASAT-NGSO-3X): Δt exact to the millisecond, NSTEPS within −0.06%
    (STEAM-2: the 560 km/97.6° sub wins Δt over the lower 540 km/53.2° one —
    the near-retrograde inclination raises the relative angular rate ω).

    ``kwargs`` are the shared :func:`compute_time_step_and_count` parameters;
    ``n_sat_total`` must be the WHOLE-constellation Σ (the §D4.1 √Nsatellites
    factor is not per-sub).
    """
    if not sub_constellations:
        raise ValueError("sub_constellations must be non-empty")
    results = [
        compute_time_step_and_count(**sub, **kwargs)
        for sub in sub_constellations
    ]
    if len(results) == 1:
        return results[0]
    winner = min(results, key=lambda r: r.tstep_s)
    trun_max = max(r.tstep_s * r.nsteps for r in results)
    nsteps = int(math.floor(trun_max / winner.tstep_s))
    logger.info(
        "D4.1 multi-sub: %d sets; Δt=%.3f s (winning sub), Trun_max=%.0f s, "
        "NSTEPS=%d", len(results), winner.tstep_s, trun_max, nsteps,
    )
    return winner._replace(nsteps=nsteps)


class DualTimeStep:
    """Dual time step manager.

    Uses the fine time step when the alpha angle is below the threshold,
    otherwise uses the coarse time step.
    """

    def __init__(
        self,
        coarse_step_s: float,
        fine_step_s: float,
        alpha_threshold_deg: float = 2.0,
        mode: str = "alpha_threshold",
        ncoarse: int = 1,
        es_antenna: "EarthStationAntenna | None" = None,
        alpha0_deg: float | None = None,
        disable_or_condition: bool = False,
    ):
        self.coarse = coarse_step_s
        self.fine = fine_step_s
        self.threshold = alpha_threshold_deg
        self.mode = mode
        self.ncoarse = max(1, int(ncoarse))
        self._using_fine = False
        self._gain_threshold_db = None
        if self.mode == "s1503_gain":
            if es_antenna is None or alpha0_deg is None:
                raise ValueError("DualTimeStep(mode='s1503_gain') requires es_antenna and alpha0_deg")
            # With disable_or_condition, the min(−30, GRX(α₀)) threshold does not apply a gain-based “critical” region.
            if disable_or_condition:
                self._gain_threshold_db = float("inf")
            else:
                self._gain_threshold_db = min(-30.0, es_antenna.relative_gain(alpha0_deg))

    def get_step(self, min_alpha_deg: float) -> float:
        """Return the appropriate time step based on the minimum alpha angle."""
        if min_alpha_deg < self.threshold:
            self._using_fine = True
            return self.fine
        self._using_fine = False
        return self.coarse

    def is_critical_gain(self, g_rel_db: float) -> bool:
        """Critical-region condition per S.1503-4 D4.7.1."""
        if self.mode != "s1503_gain" or self._gain_threshold_db is None:
            return False
        return g_rel_db > self._gain_threshold_db

    def select_step_s1503(
        self,
        is_first_step: bool,
        remaining_fine_steps: float,
        prev_any_critical_gain: bool,
    ) -> float:
        """Time-stepping rule from S.1503-4 D5.1 (Sub-steps 6.1–6.3)."""
        if self.mode != "s1503_gain":
            raise ValueError("select_step_s1503 is only valid for mode='s1503_gain'")

        if is_first_step:
            self._using_fine = True
            return self.fine
        if remaining_fine_steps < self.ncoarse:
            self._using_fine = True
            return self.fine
        if prev_any_critical_gain:
            self._using_fine = True
            return self.fine
        self._using_fine = False
        return self.coarse

    @property
    def is_fine(self) -> bool:
        return self._using_fine


class TrackDurationWindows(NamedTuple):
    """Sliding-window parameters of S.1503-4 §D5.1.3 (track-duration variant).

    All step counts are in *fine* time steps. Window set ``w`` (of ``n_tw``)
    starts at step offset ``w * n_msl``; its consecutive windows each span
    ``n_sw`` steps, and its statistics cover exactly ``nsteps`` steps from its
    own offset (the last window may enter the statistics only partially).
    """

    min_duration_s: float        # MIN_DURATION adjusted to n_sw * t_fine
    min_sliding_time_s: float    # MIN_SLIDING_TIME adjusted to n_msl * t_fine
    n_sw: int                    # N_SW  = RoundDown(MIN_DURATION / T_fine)
    n_msl: int                   # N_MSL = RoundUp(MIN_SLIDING_TIME / T_fine)
    n_tw: int                    # N_TW  = RoundUp(N_SW / N_MSL) window sets
    n_repeat: int                # N_Repeat = RoundUp(Nstep / N_SW)
    n_steps_stats: int           # Nstep — steps entering each set's statistics
    n_total_steps: int           # N_Repeat*N_SW + (N_TW − 1)*N_MSL
    t_fine_s: float

    @property
    def t_total_duration_s(self) -> float:
        return self.n_total_steps * self.t_fine_s

    def set_sim_range(self, w: int) -> tuple[int, int]:
        """Global fine-step range ``[start, end)`` simulated for window set ``w``."""
        start = w * self.n_msl
        return start, start + self.n_repeat * self.n_sw

    def set_stats_range(self, w: int) -> tuple[int, int]:
        """Global fine-step range ``[start, end)`` counted in set ``w`` statistics."""
        start = w * self.n_msl
        return start, start + self.n_steps_stats

    def window_closes_at(self, w: int, end_step: int) -> bool:
        """True when a window of set ``w`` ends exactly at global step ``end_step``
        (exclusive end — the window covers ``[end_step − n_sw, end_step)``)."""
        rel = end_step - w * self.n_msl
        if rel < self.n_sw:
            return False
        if rel > self.n_repeat * self.n_sw:
            return False
        return rel % self.n_sw == 0


def compute_track_duration_windows(
    min_duration_s: float,
    t_fine_s: float,
    nsteps: int,
    min_orbital_period_s: float,
    n_satellites: int,
) -> TrackDurationWindows:
    """Window parameters per S.1503-4 §D5.1.3 for the §D5.1.4.2 variant.

    ``min_duration_s`` is the MIN_DURATION resolved at the ES latitude;
    ``t_fine_s``/``nsteps`` are the §D4 fine step and step count of the run.
    """
    if min_duration_s <= 0.0:
        raise ValueError("compute_track_duration_windows requires MIN_DURATION > 0")
    if t_fine_s <= 0.0 or nsteps <= 0 or n_satellites <= 0:
        raise ValueError("t_fine_s, nsteps and n_satellites must be positive")

    mst_s = max(1.0, float(min_orbital_period_s) / (100.0 * float(n_satellites)))

    n_sw = int(math.floor(min_duration_s / t_fine_s + 1e-9))
    if n_sw < 1:
        # MIN_DURATION shorter than one fine step: the window degenerates to a
        # single step and the variant reduces to per-step selection.
        n_sw = 1
    n_msl = max(1, int(math.ceil(mst_s / t_fine_s - 1e-9)))
    n_tw = max(1, int(math.ceil(n_sw / n_msl - 1e-9)))
    n_repeat = max(1, int(math.ceil(nsteps / n_sw - 1e-9)))
    n_total = n_repeat * n_sw + (n_tw - 1) * n_msl

    return TrackDurationWindows(
        min_duration_s=n_sw * t_fine_s,
        min_sliding_time_s=n_msl * t_fine_s,
        n_sw=n_sw,
        n_msl=n_msl,
        n_tw=n_tw,
        n_repeat=n_repeat,
        n_steps_stats=int(nsteps),
        n_total_steps=n_total,
        t_fine_s=float(t_fine_s),
    )
