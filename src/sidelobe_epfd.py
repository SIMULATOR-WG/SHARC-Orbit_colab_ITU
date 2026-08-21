"""sidelobe_epfd.py — sidelobe-to-sidelobe (SL2SL) EPFD↓ contribution.

Non-normative study option (WP 4A — Docs 4A/107, 4A/461, 4A/706, 4A/791):
the side-lobe emissions of the visible non-Nco satellites serving OTHER
earth stations, aggregated at the victim GSO ES. Recommendation ITU-R
S.1503-4 does not account for this contribution; this module quantifies it.

Model (deliberately conservative, close to Doc 4A/461):

* a square grid of served ESs around the victim (radius/spacing configurable);
* per time step, links are selected greedily by highest elevation, one beam
  per satellite (Nco = 1 per satellite), gated by the victim's own exclusion
  angle (α₀ of the run), a minimum serving elevation and a GSO-arc
  separation at the served ES (see ``nonNco_links``);
* each serving beam radiates a constant pfd toward its cell
  (``pfd_dbw_m2`` — dBW/m²/RefBW as declared, NOT corrected by the run's
  mask→table RefBW factor: the constant is a study convention, not a mask);
* the victim-direction level follows from the satellite transmit antenna
  discrimination (ITU-R S.1528 recommends 1.2 OR 1.4 — selectable), the
  slant-range ratio, and the victim ES antenna's own relative gain (the
  RUN's antenna, not a hardcoded one).

The per-step result is a single linear EPFD sum the caller accumulates
alongside (and combined with) the standard S.1503-4 aggregate.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .nonNco_links import (
    get_valid_links,
    select_links,
    compute_ngso_offaxis_angle,
)
from .s1528_antennas import calculate_gain_1_2, calculate_gain_1_4
from .geometry import compute_slant_range


@dataclass(frozen=True)
class SidelobeConfig:
    """SL2SL study parameters. ``pattern`` picks WHICH S.1528 model radiates.

    ``frequency_ghz`` feeds the 1.4 (Taylor/Bessel) model; 1.2 is
    frequency-free. The interferer parameters default to the values used in
    the Brazilian WP 4A contribution (Gmax 45 dBi; 1.2: LN = −15 dB,
    HPBW 3.1338°; 1.4: 4 side lobes, SLR 15 dB, circular aperture
    l_r = l_t = 0.28873 m).
    """
    pattern: str = "1.4"                    # "1.2" | "1.4"
    pfd_dbw_m2: float = -140.0              # constant pfd toward the served cell
    frequency_ghz: float = 17.8
    grid_radius_km: float = 315.0
    grid_spacing_km: float = 21.0
    min_elevation_deg: float = 25.0         # serving-link elevation gate
    gso_arc_separation_deg: float = 20.0    # |α| gate at the served ES
    # Interferer (NGSO satellite) antenna:
    interferer_gmax_dbi: float = 45.0
    p12_near_lobe_level_db: float = -15.0   # S.1528 rec 1.2 LN
    p12_hpbw_deg: float = 3.1338
    p14_n_sidelobes: int = 4
    p14_slr_db: float = 15.0
    p14_aperture_m: float = 0.28873         # circular: l_r = l_t


def compute_sidelobe_epfd_step(
    cfg: SidelobeConfig,
    deployed_ESs: np.ndarray,
    pos_ecef_all: np.ndarray,
    sin_el: np.ndarray,
    victim_es_ecef: np.ndarray,
    victim_gain_rel_lin_fn,
    victim_exclusion_alpha_deg: float,
    t_s: float,
) -> tuple[float, int]:
    """One time step of the SL2SL aggregation.

    ``victim_gain_rel_lin_fn(sat_ecef) -> float`` must return the victim ES
    antenna's RELATIVE gain (linear, G(φ)/Gmax) toward ``sat_ecef`` — the
    caller builds it from the run's own ``es_antenna`` + GSO geometry, which
    keeps this module decoupled from antenna classes and absolute Gmax
    bookkeeping.

    Returns ``(epfd_linear_sum, n_links)`` — linear power sum over the
    selected sidelobe links (0.0 when no link qualifies).
    """
    valid = get_valid_links(
        deployed_ESs, pos_ecef_all, victim_es_ecef, t_s, sin_el,
        victim_exclusion_alpha_deg=float(victim_exclusion_alpha_deg),
        min_elevation_deg=cfg.min_elevation_deg,
        gso_arc_separation_deg=cfg.gso_arc_separation_deg,
    )
    if not valid:
        return 0.0, 0
    links = select_links(valid)
    if not links:
        return 0.0, 0

    total_lin = 0.0
    for sat_ecef, _es_lat, _es_lon, _elev, serving_es_ecef in links:
        off_axis = compute_ngso_offaxis_angle(
            sat_ecef, serving_es_ecef, victim_es_ecef,
        )
        if cfg.pattern == "1.2":
            g_abs = float(calculate_gain_1_2(
                np.asarray([off_axis], dtype=np.float64),
                Gmax=cfg.interferer_gmax_dbi,
                near_lobe_level=cfg.p12_near_lobe_level_db,
                HPBW=cfg.p12_hpbw_deg,
            )[0])
        else:
            g_abs = float(calculate_gain_1_4(
                np.asarray([off_axis], dtype=np.float64),
                theta=np.asarray([0.0], dtype=np.float64),
                Gmax=cfg.interferer_gmax_dbi,
                f_GHz=cfg.frequency_ghz,
                n_sidelobes=cfg.p14_n_sidelobes,
                slr=cfg.p14_slr_db,
                l_r=cfg.p14_aperture_m,
                l_t=cfg.p14_aperture_m,
            )[0])
        g_rel_db = g_abs - cfg.interferer_gmax_dbi  # discrimination vs boresight

        d_serving = compute_slant_range(sat_ecef, serving_es_ecef)
        d_victim = compute_slant_range(sat_ecef, victim_es_ecef)
        if d_victim <= 0.0 or d_serving <= 0.0:
            continue
        # pfd at the victim = cell pfd + sat discrimination + spreading ratio
        pfd_victim_db = (
            cfg.pfd_dbw_m2 + g_rel_db
            + 20.0 * math.log10(d_serving / d_victim)
        )
        g_vic_rel_lin = float(victim_gain_rel_lin_fn(sat_ecef))
        total_lin += (10.0 ** (pfd_victim_db / 10.0)) * g_vic_rel_lin

    return total_lin, len(links)
