"""sidelobe_epfd.py — sidelobe-to-sidelobe (SL2SL) EPFD↓ contribution.

Non-normative study option (WP 4A — Docs 4A/107, 4A/461, 4A/706, 4A/791):
the side-lobe emissions of non-Nco satellites serving OTHER earth stations,
aggregated at the victim GSO ES. Recommendation ITU-R S.1503-4 does not
account for this contribution; this module quantifies it.

Model:

* a square grid of served ESs around the victim (radius/spacing configurable);
* per time step, links are selected greedily by highest elevation, one beam
  per satellite (Nco = 1 per satellite), gated by a minimum serving elevation
  and a GSO-arc separation at the served ES (see ``nonNco_links``);
* WHICH satellites may radiate is set by ``SidelobeConfig.scope`` (see below);
* the serving-beam pfd is either a **constant** (``pfd_source="constant"``,
  the Doc 4A/461 / 4A/706 convention) or the **filing's own pfd mask**
  evaluated AT THE SERVED-LINK GEOMETRY (``pfd_source="mask"``, the
  Doc 4A/791 convention);
* the victim-direction level follows from the satellite transmit antenna
  discrimination (ITU-R S.1528 recommends 1.2 OR 1.4 — selectable), the
  slant-range ratio, and the victim ES antenna's own relative gain (the
  RUN's antenna, not a hardcoded one).

**Scopes** (``SidelobeConfig.scope``):

``"outside_zone"`` (default, historical behaviour)
    Every visible satellite passing the victim |α| gate — "all side lobes".
``"annulus_gmax30"``
    Only the satellites the Step-18 gain test admits through the
    ``Gmax − 30 dB`` candidate but NOT through ``GRX(α₀)`` — i.e. exactly the
    set that configuration A (current text) counts at MAIN-BEAM pfd and
    configuration B (Doc 4A/1029 §5) drops. Running this scope together with
    the Gmax−30 ablation implements the third option of the debate
    (Doc 4A/791 Study 1 scenario 2): reclassify them as side lobe.
``"in_zone"``
    Every satellite admitted by the Step-18 gain branch (not standard).
``"all_non_nco"``
    Every satellite that is NOT in the Nco (Steps 19–21 selected) set: the
    in-zone gain-branch satellites plus the standard ones dropped by the
    MAX_CO_FREQ cap. The physically complete "all side lobes" reading.

The per-step result is a linear EPFD sum the caller accumulates alongside
(and combined with) the standard S.1503-4 aggregate.
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
from .geometry import (
    compute_slant_range,
    compute_alpha_and_optimal_gso_fixed_es_batch,
    delta_longitude_s1503_deg,
)
from .coordinates import ecef_to_lla

SCOPES = ("outside_zone", "annulus_gmax30", "in_zone", "all_non_nco")
PFD_SOURCES = ("constant", "mask")

#: Scopes that supply an explicit Step-18 candidate set. The victim |α| gate
#: must be skipped for all of them: the in-cone ones (annulus / in_zone) would
#: be rejected outright, and for the out-of-cone part of ``all_non_nco`` the
#: gate is redundant (those satellites are outside the cone by definition of
#: "standard"). Only ``outside_zone``, which has no explicit set, needs it.
_SCOPES_WITH_CANDIDATE_SET = ("annulus_gmax30", "in_zone", "all_non_nco")


@dataclass(frozen=True)
class SidelobeConfig:
    """SL2SL study parameters. ``pattern`` picks WHICH S.1528 model radiates.

    ``frequency_ghz`` feeds the 1.4 (Taylor/Bessel) model; 1.2 is
    frequency-free. The interferer parameters default to the values used in
    the Brazilian WP 4A contribution (Gmax 45 dBi; 1.2: LN = −15 dB,
    HPBW 3.1338°; 1.4: 4 side lobes, SLR 15 dB, circular aperture
    l_r = l_t = 0.28873 m).

    ``pfd_source``:
      * ``"constant"`` (default) — every serving beam radiates
        ``pfd_dbw_m2`` toward its cell. NOT corrected by the run's
        mask→table RefBW factor: the constant is a study convention (as
        declared in Docs 4A/461 / 4A/706), not a mask.
      * ``"mask"`` — the pfd comes from the filing's own PFD mask, queried at
        the SERVED-link geometry, and the run's ``pfd_bw_correction_db`` IS
        applied (same as the normative path). This asymmetry is deliberate.

    ``scope`` selects which satellites may radiate — see the module
    docstring. ``alpha_gate_deg`` overrides the victim |α| gate and is used
    by ``outside_zone`` only (the in-cone scopes disable that gate).

    Defaults reproduce the historical behaviour exactly.
    """
    pattern: str = "1.4"                    # "1.2" | "1.4"
    pfd_source: str = "constant"            # "constant" | "mask"
    scope: str = "outside_zone"             # see SCOPES
    alpha_gate_deg: float | None = None     # None = the run's α₀
    pfd_dbw_m2: float = -140.0              # used only when pfd_source="constant"
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

    def __post_init__(self) -> None:
        if self.scope not in SCOPES:
            raise ValueError(f"scope must be one of {SCOPES}, got {self.scope!r}")
        if self.pfd_source not in PFD_SOURCES:
            raise ValueError(
                f"pfd_source must be one of {PFD_SOURCES}, got {self.pfd_source!r}"
            )
        if self.pattern not in ("1.2", "1.4"):
            raise ValueError(f"pattern must be '1.2' or '1.4', got {self.pattern!r}")

    @property
    def needs_step18_classification(self) -> bool:
        """True when the scope is derived from the Step-18 split, so the
        caller must supply ``cand_sat_idx``."""
        return self.scope != "outside_zone"

    @property
    def applies_victim_alpha_gate(self) -> bool:
        return self.scope not in _SCOPES_WITH_CANDIDATE_SET


def _interferer_rel_gain_db(cfg: SidelobeConfig, off_axis_deg: float) -> float:
    """S.1528 discrimination (dB, relative to boresight) at ``off_axis_deg``."""
    if cfg.pattern == "1.2":
        g_abs = float(calculate_gain_1_2(
            np.asarray([off_axis_deg], dtype=np.float64),
            Gmax=cfg.interferer_gmax_dbi,
            near_lobe_level=cfg.p12_near_lobe_level_db,
            HPBW=cfg.p12_hpbw_deg,
        )[0])
    else:
        g_abs = float(calculate_gain_1_4(
            np.asarray([off_axis_deg], dtype=np.float64),
            theta=np.asarray([0.0], dtype=np.float64),
            Gmax=cfg.interferer_gmax_dbi,
            f_GHz=cfg.frequency_ghz,
            n_sidelobes=cfg.p14_n_sidelobes,
            slr=cfg.p14_slr_db,
            l_r=cfg.p14_aperture_m,
            l_t=cfg.p14_aperture_m,
        )[0])
    return g_abs - cfg.interferer_gmax_dbi


def _served_pfd_from_mask(
    pfd_mask,
    pfd_bw_correction_db: float,
    sat_idx: int,
    sat_ecef: np.ndarray,
    served_es_ecef: np.ndarray,
    served_lat_deg: float,
    served_lon_deg: float,
    subsat_lat_deg: float,
    subsat_lon_deg: float,
) -> float:
    """pfd (dBW/m²/RefBW_table) the satellite radiates toward its OWN served
    cell, from the filing's mask.

    The mask is queried at the SERVED-link geometry — the direction the beam
    actually points — not at the victim geometry (querying the victim
    direction would return the main-beam-toward-the-victim value, which is
    precisely the assumption this study replaces).
    """
    from .wcg_search import _compute_mask_az_el_per_sat_frame_batch  # noqa: PLC0415

    sat_1 = np.asarray(sat_ecef, dtype=np.float64).reshape(1, 3)
    idx_1 = np.asarray([sat_idx], dtype=np.int64)
    lat_1 = np.asarray([subsat_lat_deg], dtype=np.float64)

    if getattr(pfd_mask, "is_mixed_geometry", False):
        raise NotImplementedError(
            "SL2SL with pfd_source='mask' does not support mixed-geometry "
            "fused masks (method_3); run the single-filing study instead."
        )

    if getattr(pfd_mask, "mask_type", "") == "azimuth_elevation":
        # (azimuth, sub-sat latitude, elevation) in the satellite's own frame,
        # toward the SERVED ES.
        az, el = _compute_mask_az_el_per_sat_frame_batch(served_es_ecef, sat_1)
        if not np.isfinite(az[0]) or not np.isfinite(el[0]):
            return -1000.0
        val = pfd_mask.get_pfd_batch(
            np.asarray([az[0]]), lat_1, np.asarray([el[0]]), sat_indices=idx_1,
        )
    else:
        # alpha / ΔLongitude mask: α and the optimal GSO longitude AT THE
        # SERVED ES (its own topocentric geometry).
        alpha, gso_ecef = compute_alpha_and_optimal_gso_fixed_es_batch(
            served_es_ecef, sat_1, float(served_lat_deg), float(served_lon_deg),
        )
        gso_lon = math.degrees(math.atan2(float(gso_ecef[0][1]),
                                          float(gso_ecef[0][0])))
        dlon = delta_longitude_s1503_deg(
            np.asarray([gso_lon]), np.asarray([subsat_lon_deg]),
        )
        val = pfd_mask.get_pfd_batch(
            np.asarray([float(alpha[0])]), lat_1, dlon, sat_indices=idx_1,
        )
    return float(np.asarray(val).ravel()[0]) + float(pfd_bw_correction_db)


def compute_sidelobe_epfd_step(
    cfg: SidelobeConfig,
    deployed_ESs: np.ndarray,
    pos_ecef_all: np.ndarray,
    sin_el: np.ndarray,
    victim_es_ecef: np.ndarray,
    victim_gain_rel_lin_fn,
    victim_exclusion_alpha_deg: float,
    t_s: float,
    *,
    cand_sat_idx: np.ndarray | None = None,
    pfd_mask=None,
    pfd_bw_correction_db: float = 0.0,
    subsat_lat_all: np.ndarray | None = None,
    subsat_lon_all: np.ndarray | None = None,
) -> tuple[float, int, int]:
    """One time step of the SL2SL aggregation.

    ``victim_gain_rel_lin_fn(sat_ecef) -> float`` must return the victim ES
    antenna's RELATIVE gain (linear, G(φ)/Gmax) toward ``sat_ecef`` — the
    caller builds it from the run's own ``es_antenna`` + GSO geometry, which
    keeps this module decoupled from antenna classes and absolute Gmax
    bookkeeping.

    ``cand_sat_idx``: constellation indices allowed to radiate (required for
    every scope except ``outside_zone``; ``None`` means "all visible", the
    historical behaviour).

    Returns ``(epfd_linear_sum, n_links, n_cand_without_link)``. The third
    value is the diagnostic of §7 of the spec: how many candidate satellites
    found NO valid served cell. A large count means the served-ES grid is too
    small (or the arc gate too strict) for this scope, and the side-lobe
    contribution is being understated.
    """
    if cfg.needs_step18_classification and cand_sat_idx is None:
        raise ValueError(
            f"scope={cfg.scope!r} requires cand_sat_idx (the Step-18 subset)"
        )
    if cfg.pfd_source == "mask" and pfd_mask is None:
        raise ValueError("pfd_source='mask' requires a pfd_mask")

    n_cand = (
        int(np.asarray(cand_sat_idx).size) if cand_sat_idx is not None
        else int(np.count_nonzero(np.asarray(sin_el) >= 0.0))
    )
    if n_cand == 0:
        return 0.0, 0, 0

    alpha_gate = (
        float(cfg.alpha_gate_deg) if cfg.alpha_gate_deg is not None
        else float(victim_exclusion_alpha_deg)
    )
    valid = get_valid_links(
        deployed_ESs, pos_ecef_all, victim_es_ecef, t_s, sin_el,
        victim_exclusion_alpha_deg=alpha_gate,
        min_elevation_deg=cfg.min_elevation_deg,
        gso_arc_separation_deg=cfg.gso_arc_separation_deg,
        cand_idx=cand_sat_idx,
        apply_victim_alpha_gate=cfg.applies_victim_alpha_gate,
    )
    if not valid:
        return 0.0, 0, n_cand
    links = select_links(valid)
    if not links:
        return 0.0, 0, n_cand

    total_lin = 0.0
    for sat_idx, sat_ecef, served_lat, served_lon, _elev, serving_es_ecef in links:
        off_axis = compute_ngso_offaxis_angle(
            sat_ecef, serving_es_ecef, victim_es_ecef,
        )
        g_rel_db = _interferer_rel_gain_db(cfg, off_axis)

        d_serving = compute_slant_range(sat_ecef, serving_es_ecef)
        d_victim = compute_slant_range(sat_ecef, victim_es_ecef)
        if d_victim <= 0.0 or d_serving <= 0.0:
            continue

        if cfg.pfd_source == "mask":
            if subsat_lat_all is not None and subsat_lon_all is not None:
                ss_lat = float(subsat_lat_all[sat_idx])
                ss_lon = float(subsat_lon_all[sat_idx])
            else:
                ss_lat, ss_lon, _ = ecef_to_lla(sat_ecef)
            pfd_served = _served_pfd_from_mask(
                pfd_mask, pfd_bw_correction_db, int(sat_idx), sat_ecef,
                serving_es_ecef, served_lat, served_lon, ss_lat, ss_lon,
            )
        else:
            pfd_served = float(cfg.pfd_dbw_m2)

        # pfd at the victim = served-cell pfd + sat discrimination + spreading
        pfd_victim_db = (
            pfd_served + g_rel_db + 20.0 * math.log10(d_serving / d_victim)
        )
        g_vic_rel_lin = float(victim_gain_rel_lin_fn(sat_ecef))
        total_lin += (10.0 ** (pfd_victim_db / 10.0)) * g_vic_rel_lin

    n_linked_sats = len({int(row[0]) for row in links})
    return total_lin, len(links), max(0, n_cand - n_linked_sats)
