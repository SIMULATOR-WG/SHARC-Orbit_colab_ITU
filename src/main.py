"""
main.py — Main entry point of the WCG Downlink.

Supports two operating modes:
  A) Via a YAML configuration file
  B) Via real SRS files: MDB (orbit) + XML (PFD mask)

Full flow:
  1. Load parameters (YAML or MDB+XML)
  2. Create the non-GSO constellation
  3. Create the GSO ES antenna (ITU-R S.1428-1)
  4. Load the PFD mask
  5. Run the WCG search (D.3.1.2)
  6. Compute the time step / number of steps (D.4)
  7. Run the temporal EPFD↓ simulation (D.5)
  8. Build the CDF and check compliance with Art. 22
  9. Generate report and plots

Reference: ITU-R S.1503-4 (09/2023)
"""

from __future__ import annotations
import os
import sys
import math
import random
import time
import logging
import argparse
import numpy as np

from .constants import DEG2RAD, RAD2DEG, RE_KM
from .orbit_propagator import (
    OrbitalElements, create_walker_constellation,
    elements_to_eci, eci_to_mean_anomaly, classify_orbit_case,
)
from .pfd_mask import load_pfd_mask, load_pfd_mask_from_xml_content, PFDMask
from .antenna import create_gso_es_antenna, EarthStationAntenna, s1503_or_condition_include
from .wcg_search import search_wcg, search_wcg_s1503, WCGResult, _compute_pfd_3d
from .time_step import (
    compute_time_step_and_count, compute_time_step_and_count_multi,
    group_sub_constellations, DualTimeStep,
    compute_orbital_period, repeat_track_is_physical,
)
from .epfd_calculator import (
    run_epfd_simulation, run_epfd_simulation_multi_es, run_epfd_simulation_windowed,
    check_article22_compliance, EPFDSimulationResult, ComplianceResult,
    epfd_aggregate_dBW_at_instant, _resolve_min_duration,
)
from .time_step import compute_track_duration_windows
from .coordinates import (
    lla_to_ecef, gso_position_ecef, eci_to_ecef, eci_vel_to_ecef,
    set_earth_rotation_initial_deg, get_earth_rotation_initial_deg,
)
from .geometry import (
    compute_elevation, compute_alpha_angle_fast,
    compute_offaxis_angle, compute_offaxis_and_planar_angle, compute_angular_velocity,
    compute_alpha_and_optimal_gso,
    set_alpha_method, get_alpha_method,
    set_gso_longitude_mode,
)
from .article22_tables import apply_article22_limits_to_config
from .s1503_figure13_wcg_lon import apply_s1503_figure13_wcg_longitude_adjustment
from .exceptions import NoValidGeometry, InvalidManualGeometry

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("wcg_main")

# S.1503-4 D6.3.4: RAAN offset due to station keeping (Wdelta). Default False; can
# be overridden via config["simulation"]["apply_station_keeping_wdelta"] (frontend/CLI).
APPLY_STATION_KEEPING_WDELTA_DEFAULT = False


def _format_percentage_value(pct: float) -> str:
    """Format percentages with adaptive precision for compliance logs."""
    value = float(pct)
    if abs(value) >= 10.0:
        return f"{value:.1f}%"
    if abs(value) >= 1.0:
        return f"{value:.2f}%"
    if abs(value) >= 0.1:
        return f"{value:.3f}%"
    if abs(value) >= 0.01:
        return f"{value:.4f}%"
    return f"{value:.5f}%"


def _s1503_table8_gso_defaults(freq_ghz: float) -> tuple[float, float]:
    """Return (εGSO, θadB) from Table 8 of S.1503-4 as a function of frequency."""
    f = float(freq_ghz or 0.0)
    if f < 10.0:
        return 10.0, 1.5
    if f < 17.0:
        return 10.0, 4.0
    return 20.0, 1.55


def load_config(path: str) -> dict:
    """Load a YAML configuration file."""
    import yaml
    with open(path, "r") as f:
        cfg = yaml.safe_load(f)
    cfg["_config_path"] = os.path.abspath(path)
    return cfg


def _resolve_data_path(path: str) -> str:
    """Resolve the path of a data file (root or data/)."""
    if os.path.exists(path):
        return path
    data_path = os.path.join("data", os.path.basename(path))
    if os.path.exists(data_path):
        return data_path
    return path


def apply_max_co_freq_override_to_non_gso(ngso: dict, n: int | None) -> None:
    """Override ``max_co_freq_by_lat`` (S.1503 Steps 19–22, ``sat_oper`` table).

    - ``n is None``: leave unchanged.
    - ``n == 0``: unlimited (empty list → no co-frequency cutoff).
    - ``n >= 1``: a single global range ``[-90°, 90°] → n`` standard satellites.
    """
    if n is None:
        return
    if n < 0:
        raise ValueError("max_co_freq override must be >= 0 (0 = unlimited)")
    if n == 0:
        ngso["max_co_freq_by_lat"] = []
        logger.info("MAX_CO_FREQ: CLI override — unlimited (0, ignores sat_oper)")
    else:
        ngso["max_co_freq_by_lat"] = [(-90.0, 90.0, int(n))]
        logger.info(
            f"MAX_CO_FREQ: CLI override — [-90°, 90°] → {int(n)} standard satellites "
            "(ignores MDB sat_oper)"
        )


def load_from_srs(mdb_path: str, xml_path: str | None = None,
                  pfd_mask_mdb: str | None = None,
                  mask_id: int | None = None,
                  epfd_limits_mdb: str | None = None,
                  epfd_limits_mask_id: int | None = None,
                  ntc_id: str | None = None,
                  service: str = "FSS") -> dict:
    """Load parameters from SRS files.

    Supported modes:
      - SRS MDB + PFD mask XML
      - SRS MDB + MASK MDB (binary/OLE field in the ``masks`` table)

    Returns a dictionary in the same format as the YAML config so the
    pipeline can be reused.

    The Article 22 limits are assigned automatically from RR Tables 22-1A
    to 22-1E, based on frequency, service and applicable antenna diameter.

    ``ntc_id`` (optional) selects the system in the ``non_geo`` table when the
    MDB contains several notices; the mask in ``mask_info`` is filtered by the
    same ``ntc_id`` when the column exists.
    """
    from .srs_reader import (read_srs_mdb, read_mask_info,
                              read_group_for_mask,
                              srs_to_constellation_config,
                              read_sat_oper, read_sat_oper_min_duration)
    if bool(xml_path) == bool(pfd_mask_mdb):
        raise ValueError("Provide exactly one mask source: xml_path or pfd_mask_mdb.")
    # pfd_mask_mdb with no explicit mask_id is resolved below from mask_lnk1
    # precedence (see the mask-selection block); it raises there only if the
    # filing declares no PFD mask at all.

    mdb_path = _resolve_data_path(mdb_path)
    if xml_path:
        xml_path = _resolve_data_path(xml_path)
    if pfd_mask_mdb:
        pfd_mask_mdb = _resolve_data_path(pfd_mask_mdb)

    logger.info(f"Reading SRS MDB: {mdb_path}")
    system = read_srs_mdb(mdb_path, ntc_id=ntc_id)
    constellation_cfg = srs_to_constellation_config(system)
    constellation_cfg["max_co_freq_by_lat"] = read_sat_oper(mdb_path, system.ntc_id)
    # MIN_DURATION bands (§D5.1.4.2 track-duration variant); empty ⇒ standard path.
    constellation_cfg["min_duration_by_lat"] = read_sat_oper_min_duration(mdb_path, system.ntc_id)

    # Read mask information to obtain the run frequency.
    # S.1503 D2: FrequencyRun = fmin + RefBW/2.
    masks = read_mask_info(mdb_path, ntc_id=system.ntc_id)
    pfd_masks = [m for m in masks if m.f_mask == "P"]

    # MASK MDB mode without an explicit mask_id: resolve from mask_lnk1
    # precedence (emi_rcp=E → lowest grp_id → lowest seq_no) instead of
    # blindly taking the first declared PFD. Wildcard (orb -1) wins, else the
    # lowest orbit's precedence-first; first PFD declared as a last resort.
    if pfd_mask_mdb and mask_id is None:
        try:
            from .srs_reader import read_mask_assignment_all
            _amap = read_mask_assignment_all(
                mdb_path, ntc_id=system.ntc_id, f_mask_filter="P") or {}
        except Exception:  # noqa: BLE001
            _amap = {}
        if _amap.get(-1):
            mask_id = int(_amap[-1][0])
        elif _amap:
            mask_id = int(_amap[sorted(_amap)[0]][0])
        elif pfd_masks:
            mask_id = int(pfd_masks[0].mask_id)
        else:
            raise ValueError(
                f"pfd_mask_mdb mode: no PFD mask (f_mask='P') in {pfd_mask_mdb}."
            )

    selected_pfd_mask = None
    selected_group = None
    freq_ghz = 0.0
    if mask_id is not None:
        for m in pfd_masks:
            if m.mask_id == mask_id:
                selected_pfd_mask = m
                break
    elif pfd_masks:
        selected_pfd_mask = pfd_masks[0]
        if mask_id is None:
            mask_id = selected_pfd_mask.mask_id

    if mask_id is not None:
        selected_group = read_group_for_mask(
            mdb_path,
            ntc_id=system.ntc_id,
            mask_id=int(mask_id),
            preferred_emi_rcp="E",
        )

    # Nominal RefBW before querying the limits MDB (Art. 22 default = 40 kHz).
    ref_bw_khz_eff = 40.0
    effective_freq_min_ghz = None
    effective_freq_max_ghz = None
    if selected_pfd_mask is not None:
        effective_freq_min_ghz = float(selected_pfd_mask.freq_min_ghz)
        effective_freq_max_ghz = float(selected_pfd_mask.freq_max_ghz)
        if selected_group is not None:
            if selected_group.freq_min_ghz is not None:
                effective_freq_min_ghz = max(effective_freq_min_ghz, float(selected_group.freq_min_ghz))
            if selected_group.freq_max_ghz is not None:
                effective_freq_max_ghz = min(effective_freq_max_ghz, float(selected_group.freq_max_ghz))
            if effective_freq_max_ghz < effective_freq_min_ghz:
                logger.warning(
                    "Mask and group ranges do not intersect "
                    "(mask %.3f-%.3f GHz, grp %.3f-%.3f GHz); using the mask range.",
                    float(selected_pfd_mask.freq_min_ghz),
                    float(selected_pfd_mask.freq_max_ghz),
                    float(selected_group.freq_min_ghz or 0.0),
                    float(selected_group.freq_max_ghz or 0.0),
                )
                effective_freq_min_ghz = float(selected_pfd_mask.freq_min_ghz)
                effective_freq_max_ghz = float(selected_pfd_mask.freq_max_ghz)
        half_bw_ghz = ref_bw_khz_eff / 2_000_000.0
        freq_ghz = float(effective_freq_min_ghz) + half_bw_ghz
        # Ensure the frequency stays within the mask range.
        freq_ghz = min(max(freq_ghz, float(effective_freq_min_ghz)), float(effective_freq_max_ghz))

    constellation_cfg["frequency_ghz"] = freq_ghz
    if selected_group is not None and selected_group.elev_min_deg is not None:
        constellation_cfg["min_elevation_deg"] = float(selected_group.elev_min_deg)
    gso_min_elev_default_deg, theta_adb_default_deg = _s1503_table8_gso_defaults(freq_ghz)

    if selected_pfd_mask is not None:
        logger.info(
            "Run frequency (S.1503 D2): "
            f"fmin={float(effective_freq_min_ghz):.6f} GHz + RefBW/2 "
            f"({ref_bw_khz_eff:.0f} kHz/2) = {freq_ghz:.6f} GHz"
        )
        logger.info(
            ">>> CONFIGURED SIMULATION FREQUENCY: "
            f"{freq_ghz:.6f} GHz (mask_id={mask_id}, range "
            f"{float(effective_freq_min_ghz):.3f}-{float(effective_freq_max_ghz):.3f} GHz)"
        )
    else:
        logger.warning("PFD mask not found to compute FrequencyRun; using 0.0 GHz.")
    logger.info(f"PFD mask: mask_id={mask_id}")
    if selected_group is not None and selected_group.elev_min_deg is not None:
        logger.info(
            "Operational MIN_ELEV loaded from grp: %.2f° (grp_id=%s)",
            float(selected_group.elev_min_deg),
            selected_group.grp_id,
        )

    # Build compatible config
    config = {
        "non_gso": {
            "semi_major_axis_km": constellation_cfg["semi_major_axis_km"],
            "eccentricity": constellation_cfg["eccentricity"],
            "inclination_deg": constellation_cfg["inclination_deg"],
            "raan_deg": 0.0,
            "arg_perigee_deg": 0.0,
            "true_anomaly_deg": 0.0,
            "num_planes": constellation_cfg["num_planes"],
            "sats_per_plane": constellation_cfg["sats_per_plane"],
            "inter_plane_phasing_factor": 1,
            "alpha0_deg": constellation_cfg["alpha0_deg"],
            "min_elevation_deg": constellation_cfg.get("min_elevation_deg", 5.0),
            "min_operating_height_km": constellation_cfg.get("min_operating_height_km", 0.0),
            "max_co_freq_by_lat": constellation_cfg.get("max_co_freq_by_lat", []),
            "min_duration_by_lat": constellation_cfg.get("min_duration_by_lat", []),
            "frequency_ghz": freq_ghz,
            "gso_min_elevation_deg": gso_min_elev_default_deg,
            "apply_gso_min_elevation": True,
            "strict_max_co_freq_total": False,
            "s1503_theta_adb_deg": theta_adb_default_deg,
            # SRS-specific plane data
            "_planes": constellation_cfg.get("planes", []),
            "_period_s": constellation_cfg.get("period_s", 0.0),
            "_rpt_period_s": constellation_cfg.get("rpt_period_s", 0.0),
            "_f_precess": constellation_cfg.get("f_precess", False),
            "_precession_deg_day": constellation_cfg.get("precession_deg_day", 0.0),
            "_f_stn_keep": constellation_cfg.get("f_stn_keep", False),
            "_keep_range_deg": constellation_cfg.get("keep_range_deg", 0.0),
            "_gmst0_deg": constellation_cfg.get("gmst0_deg", 0.0),
            "_gmst0_max_dev_deg": constellation_cfg.get("gmst0_max_dev_deg", 0.0),
        },
        "gso_es": {
            "antenna_diameter_m": 1.2,  # S.1503 default
            "antenna_efficiency": 0.99,
            "service": str(service or "FSS").upper(),
        },
        "gso_sat": {
            "longitude_deg": 0.0,
            "inclination_deg": 0.0,
        },
        "wcg_search": {
            "phi_step_deg": 0.5,
            "phi_max_deg": 70.0,
            "theta_min_deg": -90.0,
            "theta_max_deg": 270.0,
            "s1503_trail_all_points": False,
        },
        "simulation": {
            "coarse_time_step_s": 1.0,
            "fine_time_step_s": 0.0,
            "fine_step_alpha_threshold_deg": 2.0,
            "dual_time_step_mode": "s1503",
            "s1503_nhit": 16,
            "itu_software": "itu_epfd",
            "s1503_phi_coarse_deg": 1.5,
            "s1503_ncoarse": None,
            "s1503_literal_time_step": True,
            "_coarse_step_overridden": False,
            "_fine_step_overridden": False,
            "num_time_steps": 0,
        },
        "pfd_mask": {
            "type": "alpha",
            "mask_id": mask_id,
            "srs_mdb": os.path.abspath(mdb_path),
            "freq_min_ghz": (
                float(effective_freq_min_ghz) if effective_freq_min_ghz is not None else None
            ),
            "freq_max_ghz": (
                float(effective_freq_max_ghz) if effective_freq_max_ghz is not None else None
            ),
            "mask_freq_min_ghz": (
                float(selected_pfd_mask.freq_min_ghz) if selected_pfd_mask is not None else None
            ),
            "mask_freq_max_ghz": (
                float(selected_pfd_mask.freq_max_ghz) if selected_pfd_mask is not None else None
            ),
            "grp_id": selected_group.grp_id if selected_group is not None else None,
            "grp_emi_rcp": selected_group.emi_rcp if selected_group is not None else None,
            "grp_freq_min_ghz": selected_group.freq_min_ghz if selected_group is not None else None,
            "grp_freq_max_ghz": selected_group.freq_max_ghz if selected_group is not None else None,
            "grp_elev_min_deg": selected_group.elev_min_deg if selected_group is not None else None,
        },
        "article22_limits": {
            "reference_bandwidth_khz": 40.0,
            # Format: [limit_dBW, % of time EPFD *exceeds* the value (CCDF)]
            # Table 22-1A: 19.7-20.2 GHz, D/λ ≥ 100, BWref = 40 kHz
            "limits": [
                [-160.0,   0.0],   # maximum EPFD ≤ -160 dBW (never exceed)
                [-163.0,   0.3],   # exceeded for at most 0.3% of the time
                [-166.0,   2.0],   # exceeded for at most 2% of the time
                [-169.0,  10.0],   # exceeded for at most 10% of the time
                [-172.0, 100.0],   # exceeded for 100% of the time (floor, trivial)
            ],
        },
        "_config_path": os.path.abspath(mdb_path),
        "_srs_mdb_path": os.path.abspath(mdb_path),
        "_srs_system": system,
        "_srs_group": selected_group,
    }
    # Mutually-exclusive configurations (AP4 A.4.b.3.b-d / R2): expose what
    # this db declares so the UI/worker can label results per configuration.
    if getattr(system, "multi_config_type", ""):
        from .srs_reader import detect_orbit_config  # noqa: PLC0415
        config["multi_config"] = {
            "type": system.multi_config_type,
            **detect_orbit_config(mdb_path, system),
        }
    if xml_path:
        config["pfd_mask"]["source"] = "xml_file"
        config["pfd_mask"]["file"] = xml_path
    else:
        config["pfd_mask"]["source"] = "mask_mdb"
        config["pfd_mask"]["mdb_file"] = pfd_mask_mdb

    # Try to load a local config.yaml to override defaults
    # (Allows the user to set wcg_search, gso_sat, etc. in the YAML)
    try:
        if os.path.exists("config.yaml"):
            local_cfg = load_config("config.yaml")
            logger.info("Merging settings from config.yaml...")
            for section in ["wcg_search", "gso_sat", "gso_es", "simulation"]:
                if section in local_cfg:
                    config[section].update(local_cfg[section])
    except Exception as e:
        logger.warning(f"Error reading config.yaml for override: {e}")

    if epfd_limits_mdb or epfd_limits_mask_id is not None:
        logger.info(
            "--epfd-limits-* arguments ignored: EPFD limits are now selected "
            "internally from RR Tables 22-1A to 22-1E."
        )

    apply_article22_limits_to_config(config)

    return config


def load_from_manual(manual: dict) -> dict:
    """Build a full engine config from a manual/parametric system (R3/R4).

    ``manual`` uses the same public sections as ``config.example.yaml``:
    ``non_gso`` (with ``planes:`` or the Walker shortcut, apogee/perigee or
    a/e per §D6.3.7), ``pfd_mask`` (standalone XML/CSV), and optional
    ``gso_es``/``gso_sat``/``wcg_search``/``simulation``/``article22_limits``
    overrides. Fills the same defaults skeleton :func:`load_from_srs` builds
    for filings, derives the run frequency from the mask (fmin + RefBW/2,
    S.1503-4 §D2) when ``non_gso.frequency_ghz`` is absent, and tags the
    config with ``input_source='manual'`` (R26).
    """
    manual = dict(manual or {})
    ngso_in = dict(manual.get("non_gso") or {})

    planes = list(ngso_in.get("planes") or ngso_in.get("_planes") or [])
    if planes:
        ngso_in.setdefault("num_planes", len(planes))
        ngso_in.setdefault(
            "sats_per_plane",
            max(int(p.get("sats_per_plane", 0) or 0) for p in planes) or 1,
        )
        p0 = planes[0]
        ngso_in.setdefault("inclination_deg", p0.get("inclination_deg", 0.0))
        if ngso_in.get("semi_major_axis_km") in (None, 0, 0.0):
            if p0.get("semi_major_axis_km"):
                ngso_in["semi_major_axis_km"] = p0["semi_major_axis_km"]
                ngso_in.setdefault("eccentricity", p0.get("eccentricity", 0.0))
            elif p0.get("apogee_km") is not None and p0.get("perigee_km") is not None:
                _a, _e = _apsides_to_a_e(p0["apogee_km"], p0["perigee_km"])
                ngso_in["semi_major_axis_km"] = _a
                ngso_in.setdefault("eccentricity", _e)
    if (ngso_in.get("semi_major_axis_km") in (None, 0, 0.0)
            and ngso_in.get("apogee_km") is not None
            and ngso_in.get("perigee_km") is not None):
        _a, _e = _apsides_to_a_e(ngso_in["apogee_km"], ngso_in["perigee_km"])
        ngso_in["semi_major_axis_km"] = _a
        ngso_in.setdefault("eccentricity", _e)

    for key in ("semi_major_axis_km", "inclination_deg", "num_planes",
                "sats_per_plane"):
        if ngso_in.get(key) in (None, ""):
            raise ValueError(
                f"manual non_gso config missing required key '{key}' "
                "(directly, via apogee/perigee, or via planes[0])"
            )
    ngso_in.setdefault("eccentricity", 0.0)

    # PFD mask (B4.1): standalone XML (SRS/BR schema) or 1-D CSV.
    pfd_in = dict(manual.get("pfd_mask") or {})
    mask_file = pfd_in.get("file")
    mask_id = pfd_in.get("mask_id")

    # Run frequency (§D2): explicit, else mask fmin + RefBW/2.
    art22_in = dict(manual.get("article22_limits") or {})
    ref_bw_khz = float(art22_in.get("reference_bandwidth_khz", 40.0) or 40.0)
    freq_ghz = ngso_in.get("frequency_ghz")
    mask_fmin_ghz = mask_fmax_ghz = None
    if mask_file and str(mask_file).lower().endswith(".xml"):
        try:
            from .pfd_mask import load_pfd_mask  # noqa: PLC0415
            _m = load_pfd_mask(str(mask_file), mask_id=mask_id)
            if getattr(_m, "low_freq_mhz", 0.0):
                mask_fmin_ghz = float(_m.low_freq_mhz) / 1000.0
                mask_fmax_ghz = float(_m.high_freq_mhz) / 1000.0
                if freq_ghz is None:
                    freq_ghz = mask_fmin_ghz + ref_bw_khz / 2_000_000.0
                    freq_ghz = min(max(freq_ghz, mask_fmin_ghz), mask_fmax_ghz)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Manual mask preload failed (%s); frequency must "
                           "be given explicitly.", exc)
    if freq_ghz is None:
        raise ValueError(
            "manual config needs non_gso.frequency_ghz or an XML mask with a "
            "frequency range"
        )
    freq_ghz = float(freq_ghz)
    gso_min_elev_deg, theta_adb_deg = _s1503_table8_gso_defaults(freq_ghz)

    config: dict = {
        "non_gso": {
            "raan_deg": 0.0,
            "arg_perigee_deg": 0.0,
            "true_anomaly_deg": 0.0,
            "inter_plane_phasing_factor": 1,
            "alpha0_deg": 6.0,
            "min_elevation_deg": 5.0,
            "min_operating_height_km": 0.0,
            "max_co_freq_by_lat": [],
            "min_duration_by_lat": [],
            "gso_min_elevation_deg": gso_min_elev_deg,
            "apply_gso_min_elevation": True,
            "strict_max_co_freq_total": False,
            "s1503_theta_adb_deg": theta_adb_deg,
            **ngso_in,
            "frequency_ghz": freq_ghz,
            "_planes": planes,
        },
        "gso_es": {
            "antenna_diameter_m": 1.2,
            "antenna_efficiency": 0.99,
            "service": "FSS",
            **(manual.get("gso_es") or {}),
        },
        "gso_sat": {
            "longitude_deg": 0.0,
            "inclination_deg": 0.0,
            **(manual.get("gso_sat") or {}),
        },
        "wcg_search": {
            "phi_step_deg": 0.5,
            "phi_max_deg": 70.0,
            "theta_min_deg": -90.0,
            "theta_max_deg": 270.0,
            "s1503_trail_all_points": False,
            **(manual.get("wcg_search") or {}),
        },
        "simulation": {
            "coarse_time_step_s": 1.0,
            "fine_time_step_s": 0.0,
            "fine_step_alpha_threshold_deg": 2.0,
            "dual_time_step_mode": "s1503",
            "s1503_nhit": 16,
            "itu_software": "itu_epfd",
            "s1503_phi_coarse_deg": 1.5,
            "s1503_ncoarse": None,
            "s1503_literal_time_step": True,
            "_coarse_step_overridden": False,
            "_fine_step_overridden": False,
            "num_time_steps": 0,
            **(manual.get("simulation") or {}),
        },
        "pfd_mask": {
            "type": "alpha",
            "source": pfd_in.get("source", "xml_file" if mask_file else "none"),
            "file": mask_file,
            "mask_id": mask_id,
            "freq_min_ghz": mask_fmin_ghz,
            "freq_max_ghz": mask_fmax_ghz,
            "mask_freq_min_ghz": mask_fmin_ghz,
            "mask_freq_max_ghz": mask_fmax_ghz,
        },
        "article22_limits": {
            "reference_bandwidth_khz": ref_bw_khz,
            **art22_in,
        },
        "_config_path": "manual",
        "input_source": "manual",
        "manual_label": manual.get("label") or "manual system",
    }
    if str(config["gso_es"].get("service", "FSS")).upper():
        config["gso_es"]["service"] = str(config["gso_es"]["service"]).upper()

    apply_article22_limits_to_config(config)
    return config


def create_constellation_with_masks(
    ngso_cfg: dict,
    mask_assignment: dict[int, int] | None,
    *,
    mask_assignment_per_sat: dict[tuple[int, int | None], list[int]] | None = None,
    emitter_filter=None,
) -> tuple[list[OrbitalElements], list[int]]:
    """Create the constellation and return a parallel list of ``mask_id`` per satellite.

    Granularity resolved in order:

    1. ``mask_assignment_per_sat`` (dict ``(orb_id, sat_orb_id|None) →
       list[mask_id]``) when provided. Resolution falls back through:
       ``(orb, sat)`` → ``(orb, None)`` → ``(-1, None)``. Allows assigning
       distinct masks to individual satellites within the same orbit.
    2. ``mask_assignment`` (dict ``orb_id → mask_id``, legacy) — applies to
       all satellites in the orbit.
    3. No mapping → all satellites get ``-1`` (sentinel: the upstream pipeline
       resolves the single legacy ``mask_id``).

    When both mappings are populated, ``mask_assignment_per_sat`` takes
    precedence — ``mask_assignment`` remains as an additional fallback.

    Keeps :func:`create_constellation_from_config` intact — backwards compat.
    This function is the new entry point for the multi-mask flow (S.1503-4).
    """
    from .srs_reader import resolve_mask_for_orbit, resolve_masks_for_sat

    constellation = create_constellation_from_config(ngso_cfg)
    planes_data = ngso_cfg.get("_planes", []) or ngso_cfg.get("planes", [])
    mapping = dict(mask_assignment or {})
    per_sat = dict(mask_assignment_per_sat or {})

    def _resolve(orb_id: int, sat_orb_id: int | None) -> int:
        if per_sat:
            masks = resolve_masks_for_sat(per_sat, orb_id, sat_orb_id)
            if masks:
                return int(masks[0])
        if mapping:
            r = resolve_mask_for_orbit(mapping, orb_id)
            if r is not None:
                return int(r)
        return -1

    # Per-satellite emission-band membership (grp ⋈ mask_lnk1). When no filter
    # is supplied (or it has no data), every satellite stays active.
    def _active(orb_id: int, sat_orb_id: int | None) -> bool:
        if emitter_filter is None:
            return True
        return bool(emitter_filter.is_active(orb_id, sat_orb_id))

    mask_per_sat: list[int] = []
    active_flags: list[bool] = []
    if planes_data:
        for plane_info in planes_data:
            n_sats = int(plane_info.get("sats_per_plane", 0))
            if n_sats < 1:
                continue
            orb_id = int(plane_info.get("orb_id", 0))
            # sat_orb_id is 1-based in the SRS (phase / mask_lnk1 table).
            for sat_orb_idx in range(1, n_sats + 1):
                mask_per_sat.append(_resolve(orb_id, sat_orb_idx))
                active_flags.append(_active(orb_id, sat_orb_idx))
    else:
        # Synthetic Walker Delta — no orb_id; uses wildcard if present. The grp
        # band filter cannot map a synthetic sat to an orbit, so keep all.
        wildcard_orb = _resolve(-999_999, None)  # force fallback
        mask_per_sat = [wildcard_orb] * len(constellation)
        active_flags = [True] * len(constellation)

    if len(mask_per_sat) != len(constellation):
        # Defensive: planes_data and constellation must align. Truncate/extend
        # with the same sentinel to avoid a downstream IndexError.
        if len(mask_per_sat) < len(constellation):
            pad = len(constellation) - len(mask_per_sat)
            mask_per_sat.extend([-1] * pad)
            active_flags.extend([True] * pad)
        else:
            mask_per_sat = mask_per_sat[: len(constellation)]
            active_flags = active_flags[: len(constellation)]

    # Drop satellites that do not emit in the simulation band (S.1503-4: only
    # co-frequency emitters contribute). Falls back to the full constellation if
    # the filter would remove everything (defensive — avoids an empty run).
    if emitter_filter is not None and getattr(emitter_filter, "has_data", False) \
            and not all(active_flags):
        kept = [
            (sat, mid)
            for sat, mid, act in zip(constellation, mask_per_sat, active_flags)
            if act
        ]
        if kept:
            n_before = len(constellation)
            constellation = [k[0] for k in kept]
            mask_per_sat = [k[1] for k in kept]
            logger.info(
                "Emitter band filter: simulating %d/%d satellites operating in band.",
                len(constellation), n_before,
            )
        else:
            logger.warning(
                "Emitter band filter matched 0 satellites; keeping the full "
                "constellation (check grp/mask_lnk1 band data vs the simulation "
                "frequency)."
            )

    return constellation, mask_per_sat


def _apsides_to_a_e(apogee_km: float, perigee_km: float) -> tuple[float, float]:
    """(a, e) from apogee/perigee ALTITUDES per S.1503-4 §D6.3.7:
    a = Re + (ha + hp)/2 ; e = (ha − hp)/(2a)."""
    a = RE_KM + (float(apogee_km) + float(perigee_km)) / 2.0
    e = (float(apogee_km) - float(perigee_km)) / (2.0 * a) if a > 0 else 0.0
    return a, max(0.0, e)


def create_constellation_from_config(ngso_cfg: dict) -> list[OrbitalElements]:
    """Create the constellation from the configuration.

    If SRS-specific plane data is available (the ``_planes`` field — or its
    public YAML alias ``planes``), uses the real RAAN/ω/phase of each plane.
    Otherwise, uses a standard Walker Delta.

    Manual/parametric entry (R3/R4): apogee/perigee ALTITUDES are accepted in
    place of (semi-major axis, eccentricity), converted per §D6.3.7 — both at
    the top level (``apogee_km``/``perigee_km``) and per plane.
    """
    # Public alias for the per-plane list (manual YAML entry) — same schema
    # as the SRS-derived `_planes` (see load_from_srs).
    if "planes" in ngso_cfg and not ngso_cfg.get("_planes"):
        ngso_cfg["_planes"] = ngso_cfg["planes"]

    # Apogee/perigee altitudes → (a, e) per §D6.3.7 (AP4 A.4.b.4.d/e).
    if (ngso_cfg.get("semi_major_axis_km") in (None, 0, 0.0)
            and ngso_cfg.get("apogee_km") is not None
            and ngso_cfg.get("perigee_km") is not None):
        _a, _e = _apsides_to_a_e(ngso_cfg["apogee_km"], ngso_cfg["perigee_km"])
        ngso_cfg["semi_major_axis_km"] = _a
        ngso_cfg.setdefault("eccentricity", _e)
        if not ngso_cfg.get("eccentricity"):
            ngso_cfg["eccentricity"] = _e

    a_km = ngso_cfg["semi_major_axis_km"]
    e = ngso_cfg["eccentricity"]
    i_deg = ngso_cfg["inclination_deg"]
    num_planes = ngso_cfg["num_planes"]
    sats_per_plane = ngso_cfg["sats_per_plane"]
    omega_deg = ngso_cfg.get("arg_perigee_deg", 0.0)
    min_operating_height_km = float(ngso_cfg.get("min_operating_height_km", 0.0) or 0.0)

    planes_data = ngso_cfg.get("_planes", [])

    if planes_data:
        # Use the real orbital parameters of each SRS plane
        constellation: list[OrbitalElements] = []
        for plane_info in planes_data:
            n_sats = plane_info.get("sats_per_plane", sats_per_plane)
            if n_sats < 1:
                continue
            # Plane-specific orbital parameters. Apogee/perigee altitudes are
            # accepted per plane too (§D6.3.7), for manual YAML entry.
            if (plane_info.get("semi_major_axis_km") in (None, 0, 0.0)
                    and plane_info.get("apogee_km") is not None
                    and plane_info.get("perigee_km") is not None):
                _ap, _ep = _apsides_to_a_e(plane_info["apogee_km"],
                                           plane_info["perigee_km"])
                plane_info["semi_major_axis_km"] = _ap
                plane_info.setdefault("eccentricity", _ep)
            a_plane = plane_info.get("semi_major_axis_km", a_km)
            e_plane = plane_info.get("eccentricity", e)
            i_plane = plane_info.get("inclination_deg", i_deg)
            raan_deg = plane_info.get("raan_deg", 0.0)
            omega_plane = plane_info.get("perigee_arg_deg", omega_deg)
            h_min_plane_km = float(
                plane_info.get("min_operating_height_km", min_operating_height_km) or 0.0
            )
            phase_angles = plane_info.get("phase_angles_deg")
            has_official_phase = isinstance(phase_angles, list) and len(phase_angles) > 0

            # Initial phase:
            # 1) official from the SRS (phase table), when available per satellite
            # 2) fallback: uniform spacing in mean anomaly
            delta_M = 360.0 / n_sats
            for s in range(n_sats):
                M_deg = None
                if has_official_phase and s < len(phase_angles):
                    ph = phase_angles[s]
                    if ph is not None:
                        try:
                            M_deg = float(ph) % 360.0
                        except (TypeError, ValueError):
                            M_deg = None
                if M_deg is None:
                    M_deg = (s * delta_M) % 360.0

                oe = OrbitalElements.from_degrees(
                    a=a_plane, e=e_plane, i_deg=i_plane,
                    raan_deg=raan_deg, omega_deg=omega_plane,
                    M_deg=M_deg,
                    min_operating_height_km=h_min_plane_km,
                )
                constellation.append(oe)

        return constellation
    else:
        # Standard Walker Delta. raan0/ω from the config now reach the
        # generator (they were silently fixed at 0 before — R4).
        phasing = ngso_cfg.get("inter_plane_phasing_factor", 1)
        return create_walker_constellation(
            a=a_km, e=e, i_deg=i_deg,
            num_planes=num_planes,
            sats_per_plane=sats_per_plane,
            phasing_factor=phasing,
            raan0_deg=float(ngso_cfg.get("raan0_deg", ngso_cfg.get("raan_deg", 0.0)) or 0.0),
            omega_deg=float(omega_deg or 0.0),
            min_operating_height_km=min_operating_height_km,
        )


def _normalize_lon_deg(lon_deg: float) -> float:
    """Normalize longitude to the interval [-180, 180)."""
    return ((lon_deg + 180.0) % 360.0) - 180.0


def _evaluate_fixed_es_gso_geometry(
    constellation: list[OrbitalElements],
    es_lat_deg: float,
    es_lon_deg: float,
    gso_lon_deg: float,
    t_s: float,
    pfd_mask: PFDMask,
    es_antenna: EarthStationAntenna,
    alpha0_deg: float,
    min_elevation_deg: float,
    gso_min_elevation_deg: float,
    pfd_bw_correction_db: float,
    strict_exclusion_zone: bool = False,
) -> tuple[WCGResult | None, float]:
    """Evaluate aggregate EPFD for a fixed ES/GSO geometry at t_s."""
    es_lon_n = _normalize_lon_deg(es_lon_deg)
    gso_lon_n = _normalize_lon_deg(gso_lon_deg)
    es_ecef = lla_to_ecef(es_lat_deg, es_lon_n, 0.0)
    gso_ecef = gso_position_ecef(gso_lon_n, t_s)
    gso_elevation = compute_elevation(es_ecef, gso_ecef, es_lat_deg, es_lon_n)
    # S.1503-4 §D.3.1.2 (p.55): el_GSO ≥ ε_GSO gates only the AND-branch of the
    # per-satellite store criterion — a near-boresight contribution is still
    # counted via the gain OR-branch even when the GSO arc is below ε_GSO. So the
    # geometry is NOT rejected outright on GSO elevation (the previous
    # `return None` here dropped valid high-latitude geometries, e.g. ES≈66°).
    gso_elev_ok = gso_elevation >= gso_min_elevation_deg

    best_single: WCGResult | None = None
    best_ang_vel = math.inf
    bin_size_db = 0.1
    epfd_sum_linear = 0.0

    for sat_idx, oe in enumerate(constellation):
        sat_eci, sat_vel_eci = elements_to_eci(oe)
        sat_ecef = eci_to_ecef(sat_eci, t_s)
        sat_vel_ecef = eci_vel_to_ecef(sat_vel_eci, sat_ecef, t_s)
        sat_alt_km = float(np.linalg.norm(sat_ecef)) - RE_KM
        sat_h_min_km = max(0.0, float(getattr(oe, "min_operating_height_km", 0.0) or 0.0))
        if sat_h_min_km > 0.0 and sat_alt_km < sat_h_min_km - 1e-9:
            continue

        elevation = compute_elevation(es_ecef, sat_ecef, es_lat_deg, es_lon_n)
        # S.1503-4 Step 11 visibility is the horizon/line-of-sight check
        # (§D6.4.3).  The operating elevation ε0 gates only the Step 18
        # standard branch, not the gain OR-branch.
        if elevation < 0.0:
            continue

        alpha = compute_alpha_angle_fast(es_ecef, sat_ecef, gso_ecef)
        offaxis = compute_offaxis_angle(es_ecef, sat_ecef, gso_ecef)
        theta_planar = None
        if es_antenna.requires_planar_angle:
            _, theta_planar = compute_offaxis_and_planar_angle(
                es_ecef, sat_ecef, gso_ecef, es_lat_deg, es_lon_n
            )
        # Store criterion (S.1503-4 Step 18): (α≥α₀ AND el_nGSO≥ε₀
        # AND el_GSO≥ε_GSO) OR G(φ) > min(Gmax−30, G(α₀)).
        and_branch = (
            abs(alpha) >= alpha0_deg
            and elevation >= min_elevation_deg
            and gso_elev_ok
        )
        or_branch = s1503_or_condition_include(
            es_antenna, offaxis, alpha0_deg, theta_planar,
            disable_or_condition=strict_exclusion_zone,
        )
        if not (and_branch or or_branch):
            continue

        _, _, lon_alpha = compute_alpha_and_optimal_gso(
            es_ecef, sat_ecef, es_lat_deg, es_lon_n,
        )
        pfd_db = _compute_pfd_3d(
            pfd_mask=pfd_mask,
            alpha_deg=alpha,
            ngso_sat_eci=sat_eci,
            ngso_sat_vel_eci=sat_vel_eci,
            es_lon_deg=es_lon_n,
            t_s=t_s,
            es_lat_deg=es_lat_deg,
            gso_ecef=gso_ecef,
            pfd_bw_correction_db=pfd_bw_correction_db,
            ngso_sat_ecef=sat_ecef,
            es_ecef_cached=es_ecef,
            gso_lon_deg=lon_alpha,
            sat_idx=sat_idx,
        )
        g_rel_db = es_antenna.relative_gain(offaxis, theta_planar)
        epfd_db = pfd_db + g_rel_db
        epfd_sum_linear += 10.0 ** (epfd_db / 10.0)

        ang_vel = compute_angular_velocity(es_ecef, sat_ecef, sat_vel_ecef)
        candidate = WCGResult(
            theta_deg=0.0,
            phi_deg=0.0,
            es_lat_deg=es_lat_deg,
            es_lon_deg=es_lon_n,
            gso_lon_deg=gso_lon_n,
            alpha_deg=alpha,
            offaxis_deg=offaxis,
            pfd_dBW=pfd_db,
            es_gain_rel_dB=g_rel_db,
            epfd_dBW=epfd_db,
            elevation_deg=elevation,
            angular_velocity_deg_s=ang_vel,
            ref_sat_eci=sat_eci.copy(),
            search_trail=[],
        )

        if best_single is None:
            best_single = candidate
            best_ang_vel = ang_vel
            continue
        if candidate.epfd_dBW > best_single.epfd_dBW + bin_size_db:
            best_single = candidate
            best_ang_vel = ang_vel
        elif abs(candidate.epfd_dBW - best_single.epfd_dBW) <= bin_size_db and ang_vel < best_ang_vel:
            best_single = candidate
            best_ang_vel = ang_vel

    if best_single is None:
        return None, -999.0
    # Return the worst single-entry contributor (highest EPFD; ties broken by
    # lower angular velocity), not the satellite-0 candidate.
    best_single.epfd_aggregate_dBW = (
        10.0 * math.log10(epfd_sum_linear) if epfd_sum_linear > 0.0 else -999.0
    )
    return best_single, best_single.epfd_aggregate_dBW


def _optimize_delta_m_for_fixed_es_gso(
    constellation: list[OrbitalElements],
    es_lat_deg: float,
    es_lon_deg: float,
    gso_lon_deg: float,
    t_s: float,
    pfd_mask: PFDMask,
    es_antenna: EarthStationAntenna,
    alpha0_deg: float,
    min_elevation_deg: float,
    gso_min_elevation_deg: float,
    pfd_bw_correction_db: float,
) -> tuple[float, WCGResult | None]:
    """Select the ΔM that maximizes aggregate EPFD for fixed ES/GSO."""
    original_Ms = [oe.M for oe in constellation]
    best_delta_rad = 0.0
    best_result: WCGResult | None = None
    best_aggregate_db = -math.inf
    tested_keys: set[float] = set()

    def evaluate_delta(delta_rad: float) -> None:
        nonlocal best_delta_rad, best_result, best_aggregate_db
        delta_wrapped = float(delta_rad % (2.0 * math.pi))
        key = round(math.degrees(delta_wrapped), 4)
        if key in tested_keys:
            return
        tested_keys.add(key)

        for idx, oe in enumerate(constellation):
            oe.M = (original_Ms[idx] + delta_wrapped) % (2.0 * math.pi)
        candidate, agg_db = _evaluate_fixed_es_gso_geometry(
            constellation=constellation,
            es_lat_deg=es_lat_deg,
            es_lon_deg=es_lon_deg,
            gso_lon_deg=gso_lon_deg,
            t_s=t_s,
            pfd_mask=pfd_mask,
            es_antenna=es_antenna,
            alpha0_deg=alpha0_deg,
            min_elevation_deg=min_elevation_deg,
            gso_min_elevation_deg=gso_min_elevation_deg,
            pfd_bw_correction_db=pfd_bw_correction_db,
        )
        if candidate is None:
            return
        if agg_db > best_aggregate_db + 1e-9:
            best_aggregate_db = agg_db
            best_delta_rad = delta_wrapped
            best_result = candidate

    for deg in np.arange(0.0, 360.0, 10.0):
        evaluate_delta(math.radians(float(deg)))
    coarse_best_deg = math.degrees(best_delta_rad)
    for deg in np.arange(coarse_best_deg - 10.0, coarse_best_deg + 10.001, 1.0):
        evaluate_delta(math.radians(float(deg)))
    fine_best_deg = math.degrees(best_delta_rad)
    for deg in np.arange(fine_best_deg - 1.0, fine_best_deg + 1.001, 0.1):
        evaluate_delta(math.radians(float(deg)))

    for idx, oe in enumerate(constellation):
        oe.M = (original_Ms[idx] + best_delta_rad) % (2.0 * math.pi)
    return best_delta_rad, best_result


from dataclasses import dataclass as _dataclass, field as _field


@_dataclass
class DownlinkEngineInputs:
    """Objects + scalars ready for the EPFD↓ engine, built ONCE from the cfg.

    Mirrors the setup phase of :func:`run_wcg_downlink` (cfg extraction,
    constellation, mask, antenna, BW correction) **without** the WCG search.
    Used by the single-pass engine (Study 2/3) that propagates the constellation
    1×/timestep and sweeps N grid points reusing the
    ``_accumulate_epfd_visible_satellites`` kernel.
    """
    constellation: list
    mask_id_per_sat: list
    pfd_mask: object
    es_antenna: object
    alpha0_deg: float
    min_elevation_deg: float
    max_co_freq_by_lat: list
    pfd_bw_correction_db: float
    gso_min_elevation_deg: float
    strict_max_co_freq_total: bool
    strict_exclusion_zone: bool
    min_angle_at_es_deg: float
    freq_ghz: float
    gmst0_deg: float
    multi_mask_active: bool
    # Temporal propagation parameters (RAAN/station-keeping). raan_dot_artificial
    # is derived per run (2π/T_run) — only the flag here; the single-pass engine
    # computes T_run = num_time_steps × tstep_s.
    artificial_precession: bool
    raan_dot_override_rad_s: float | None
    apply_station_keeping: bool
    wdelta_deg_requested: float
    num_planes: int
    # Temporal sampling reference S.1503-4 §D4.2 (fine step + N). Used by the
    # vectorized/single-pass engine when the user does not fix time_step_s.
    s1503_ref_tstep_s: float
    s1503_ref_nsteps: int


def _resolve_mask_lnk1_assignments(
    config: dict, pfd_cfg: dict,
) -> tuple[dict[int, int], dict[tuple[int, int | None], list[int]]]:
    """Resolve the mask_lnk1 mapping (S.1503-4 multi-mask) from the SRS MDB.

    Fine granularity: ``(orb_id, sat_orb_id) → [mask_id]``. The f_mask='P'
    filter ensures only PFD (downlink) masks are admitted — uplink (E) /
    inter-sat (S) masks listed on the same orbit/satellite are ignored for
    epfd↓. ``mask_assignment`` (keyed by orbit) is kept as a fallback for
    callers that still use the reduced form. Returns empty mappings
    (single-mask mode) when the SRS reference is missing or reading fails.
    """
    mask_assignment: dict[int, int] = {}
    mask_assignment_per_sat: dict[tuple[int, int | None], list[int]] = {}
    srs_sys_ref = config.get("_srs_system")
    srs_mdb_for_assignment = config.get("_srs_mdb_path") or pfd_cfg.get("srs_mdb")
    ntc_id_for_assignment = getattr(srs_sys_ref, "ntc_id", None) if srs_sys_ref is not None else None
    if srs_mdb_for_assignment and ntc_id_for_assignment:
        try:
            from .srs_reader import (
                read_mask_assignment_all,
                read_mask_assignment_per_sat,
            )
            mask_assignment_per_sat = read_mask_assignment_per_sat(
                srs_mdb_for_assignment,
                ntc_id=ntc_id_for_assignment,
                f_mask_filter="P",
            ) or {}
            assignment_all = read_mask_assignment_all(
                srs_mdb_for_assignment,
                ntc_id=ntc_id_for_assignment,
                f_mask_filter="P",
            ) or {}
            mask_assignment = {
                orb: lst[0] for orb, lst in assignment_all.items() if lst
            }
        except Exception as exc:  # noqa: BLE001 — log + single-mask fallback
            logger.warning(
                "Failed reading mask_lnk1 (%s); continuing in single-mask mode.", exc,
            )
            mask_assignment = {}
            mask_assignment_per_sat = {}
    return mask_assignment, mask_assignment_per_sat


def build_downlink_engine_inputs(config: dict) -> DownlinkEngineInputs:
    """Build the EPFD↓ engine inputs from the cfg, without the WCG search.

    Replicates the assembly of :func:`run_wcg_downlink` (sections 1–3) in a
    lean way (no verbose logs). Keeps the essential side effects:
    ``set_earth_rotation_initial_deg`` (GMST0) and the ``setdefault`` calls on
    ``ngso_cfg``. Does NOT modify ``run_wcg_downlink`` — the single-entry path
    stays identical.
    """
    ngso_cfg = config["non_gso"]
    gso_es_cfg = config["gso_es"]
    pfd_cfg = config["pfd_mask"]
    art22_cfg = config["article22_limits"]
    sim_cfg = config.get("simulation", {})

    # GMST0 (same logic as run_wcg_downlink).
    gmst0_override_deg = sim_cfg.get("earth_rotation_initial_deg", None)
    if gmst0_override_deg is not None:
        gmst0_deg = float(gmst0_override_deg)
    elif "_gmst0_deg" in ngso_cfg:
        gmst0_deg = float(ngso_cfg.get("_gmst0_deg", 0.0))
    else:
        gmst0_deg = 0.0
    set_earth_rotation_initial_deg(gmst0_deg)

    # α-method / GSO-longitude globals (same config keys and defaults as
    # run_wcg_downlink): engines built from these inputs must not inherit the
    # module-level state left behind by a previous run.
    alpha_method = str(sim_cfg.get("alpha_method", "sweep")).strip().lower()
    if alpha_method not in ("sweep", "analytical"):
        logger.warning(f"alpha_method '{alpha_method}' invalid; using 'sweep'.")
        alpha_method = "sweep"
    set_alpha_method(alpha_method)
    gso_lon_mode = str(sim_cfg.get("gso_longitude_mode", "arc_optimal")).strip().lower()
    if gso_lon_mode not in ("arc_optimal", "es_meridian"):
        logger.warning(f"gso_longitude_mode '{gso_lon_mode}' invalid; using 'arc_optimal'.")
        gso_lon_mode = "arc_optimal"
    set_gso_longitude_mode(gso_lon_mode)

    alpha0_deg = ngso_cfg["alpha0_deg"]
    min_elev_deg = ngso_cfg["min_elevation_deg"]
    max_co_freq_by_lat = ngso_cfg.get("max_co_freq_by_lat", [])
    freq_ghz = ngso_cfg["frequency_ghz"]
    num_planes = int(ngso_cfg.get("num_planes", 0) or 0)
    a_km = float(ngso_cfg["semi_major_axis_km"])
    ecc = float(ngso_cfg["eccentricity"])
    i_deg = float(ngso_cfg["inclination_deg"])
    sats_per_plane = int(ngso_cfg.get("sats_per_plane", 0) or 0)
    min_operating_height_km = float(ngso_cfg.get("min_operating_height_km", 0.0) or 0.0)
    gso_min_elev_default_deg, theta_adb_default_deg = _s1503_table8_gso_defaults(freq_ghz)
    gso_min_elev_deg = float(ngso_cfg.get("gso_min_elevation_deg", gso_min_elev_default_deg))
    apply_gso_min_elev = bool(ngso_cfg.get("apply_gso_min_elevation", True))
    strict_max_co_freq_total = bool(ngso_cfg.get("strict_max_co_freq_total", False))
    strict_exclusion_zone = bool(ngso_cfg.get("strict_exclusion_zone", False))
    min_angle_at_es_deg = float(ngso_cfg.get("min_angle_at_es_deg", 0.0) or 0.0)
    gso_min_elev_effective_deg = gso_min_elev_deg if apply_gso_min_elev else -90.0
    ngso_cfg.setdefault("gso_min_elevation_deg", gso_min_elev_deg)
    ngso_cfg.setdefault("apply_gso_min_elevation", apply_gso_min_elev)
    ngso_cfg.setdefault("strict_max_co_freq_total", strict_max_co_freq_total)
    ngso_cfg.setdefault("strict_exclusion_zone", strict_exclusion_zone)
    ngso_cfg.setdefault("min_angle_at_es_deg", min_angle_at_es_deg)
    ngso_cfg.setdefault("s1503_theta_adb_deg", theta_adb_default_deg)

    # mask_lnk1 resolution (multi-mask S.1503-4).
    mask_assignment, mask_assignment_per_sat = _resolve_mask_lnk1_assignments(
        config, pfd_cfg,
    )

    # Optional: restrict the simulated constellation to satellites that actually
    # emit at the simulation frequency, resolved from the SRS grp ⋈ mask_lnk1
    # tables (grp band = operating-frequency authority). Opt-in; off → the full
    # constellation is simulated (legacy behavior).
    emitter_filter = None
    if bool(sim_cfg.get("restrict_emitters_to_sim_band", False)):
        srs_mdb_for_band = config.get("_srs_mdb_path") or pfd_cfg.get("srs_mdb")
        srs_sys_for_band = config.get("_srs_system")
        ntc_for_band = getattr(srs_sys_for_band, "ntc_id", None) if srs_sys_for_band is not None else None
        if srs_mdb_for_band and ntc_for_band:
            try:
                from .srs_reader import read_emitters_in_band
                emitter_filter = read_emitters_in_band(
                    srs_mdb_for_band, ntc_id=ntc_for_band,
                    freq_ghz=freq_ghz, emi_rcp="E",
                )
                if emitter_filter.has_data:
                    logger.info(
                        "Emitter band filter @ %.4f GHz: %d active grp(s), "
                        "wildcard=%s, whole-orbits=%d, specific-sats=%d.",
                        freq_ghz, emitter_filter.n_active_grps,
                        emitter_filter.wildcard_all,
                        len(emitter_filter.active_orbits),
                        len(emitter_filter.active_sats),
                    )
                else:
                    logger.info(
                        "Emitter band filter requested but grp/mask_lnk1 data is "
                        "unavailable; simulating the full constellation."
                    )
            except Exception as exc:  # noqa: BLE001 — never block the run
                logger.warning(
                    "Emitter band filter failed (%s); simulating full constellation.", exc,
                )
                emitter_filter = None

    constellation, mask_id_per_sat = create_constellation_with_masks(
        ngso_cfg, mask_assignment, mask_assignment_per_sat=mask_assignment_per_sat,
        emitter_filter=emitter_filter,
    )

    _unique_mask_ids = sorted({int(m) for m in mask_id_per_sat if int(m) != -1})
    multi_mask_active = len(_unique_mask_ids) > 1

    # GSO ES antenna.
    es_diameter = gso_es_cfg["antenna_diameter_m"]
    es_efficiency = gso_es_cfg.get("antenna_efficiency", 0.99)
    es_service = str(gso_es_cfg.get("service", "FSS")).upper()
    es_antenna = create_gso_es_antenna(es_diameter, freq_ghz, es_efficiency, service=es_service)

    # PFD mask.
    mask_id = pfd_cfg.get("mask_id", None)
    pfd_source = pfd_cfg.get("source", "xml_file")
    if pfd_source == "mask_mdb":
        from .srs_reader import read_pfd_mask_xml_from_mdb
        pfd_mask_mdb = _resolve_data_path(pfd_cfg["mdb_file"])
        srs_sys = config.get("_srs_system")
        ntc_id = getattr(srs_sys, "ntc_id", None) if srs_sys is not None else None
        if multi_mask_active:
            from .srs_reader import load_pfd_masks_for_ids
            from .pfd_mask import PFDMaskMulti
            masks_by_id = load_pfd_masks_for_ids(pfd_mask_mdb, _unique_mask_ids, ntc_id=ntc_id)
            pfd_mask = PFDMaskMulti(masks_by_id, mask_id_per_sat)
        else:
            # mask_id here is already resolved (explicit, or mask_lnk1
            # precedence — see the mask-selection block near the top).
            pfd_xml_content = read_pfd_mask_xml_from_mdb(
                pfd_mask_mdb, mask_id=int(mask_id), ntc_id=ntc_id,
            )
            pfd_mask = load_pfd_mask_from_xml_content(pfd_xml_content, mask_id=mask_id)
    else:
        pfd_file = _resolve_data_path(pfd_cfg["file"])
        pfd_mask = load_pfd_mask(pfd_file, mask_type=pfd_cfg.get("type", "alpha"), mask_id=mask_id)

    # BW correction.
    limit_bw_khz = art22_cfg.get("reference_bandwidth_khz", 40.0)
    mask_bw_khz = pfd_mask.refbw_khz
    bw_correction_db = 10.0 * math.log10(limit_bw_khz / mask_bw_khz)

    # Artificial precession (same logic as run_wcg_downlink, lines ~1612-1625).
    artificial_precession = sim_cfg.get("artificial_precession")
    if artificial_precession is None and "artificial_precession" not in sim_cfg:
        if "_rpt_period_s" in ngso_cfg or "_f_precess" in ngso_cfg:
            rpt = ngso_cfg.get("_rpt_period_s", 0) or 0
            f_precess = ngso_cfg.get("_f_precess", False)
            if rpt <= 0 and num_planes < 12 and not f_precess:
                artificial_precession = True
    if artificial_precession is None:
        artificial_precession = False
    artificial_precession = bool(artificial_precession)

    # MDB precession override.
    raan_dot_override_rad_s = None
    if sim_cfg.get("use_precession_mdb", False):
        pday = ngso_cfg.get("_precession_deg_day", 0.0)
        if pday != 0.0:
            raan_dot_override_rad_s = (pday * DEG2RAD) / 86400.0

    # S.1503-4 D6.3.6 (Fig. 52): the three orbit cases are mutually exclusive.
    # Admin-supplied precession (Case 3) takes precedence over the artificial/
    # forced precession of Case 1 — they must never be combined.
    if raan_dot_override_rad_s is not None and artificial_precession:
        logger.warning(
            "Admin precession (Case 3) and artificial precession (Case 1) both "
            "requested — disabling artificial precession per D6.3.6 (mutually exclusive)."
        )
        artificial_precession = False

    # Station keeping (S.1503-4 D6.3.4).
    apply_station_keeping = bool(
        sim_cfg.get("apply_station_keeping_wdelta", APPLY_STATION_KEEPING_WDELTA_DEFAULT)
    )
    f_stn_keep = ngso_cfg.get("_f_stn_keep", False)
    wdelta_deg_requested = float(ngso_cfg.get("_keep_range_deg", 0.0) or 0.0) if f_stn_keep else 0.0

    # Temporal sampling reference S.1503-4 §D4.2 (fine step + N).
    # §D4.6.1 applies when station keeping maintains the declared repeat track
    # (SRS orbit.f_stn_keep) — the BR software keys on this flag, not on the
    # unperturbed geometric closure of the track (validated against the
    # official EPFDRESULTS test runs: Skybridge ntc101 Y→repeating even at
    # 5.56 orbits/repeat; Boeing ntc102 N→non-repeating despite a declared
    # 1-day period).
    repeating = bool(sim_cfg.get("repeating_ground_track", False))
    repeat_days = float(sim_cfg.get("repeat_period_days", 1.0) or 1.0)
    if not repeating and (ngso_cfg.get("_rpt_period_s", 0) or 0) > 0:
        rpt_s = ngso_cfg["_rpt_period_s"]
        if rpt_s >= 3600 and bool(ngso_cfg.get("_f_stn_keep", False)):
            repeat_days = rpt_s / 86400.0
            repeating = True
    _orbit_case = classify_orbit_case(
        repeating_ground_track=repeating,
        admin_precession=raan_dot_override_rad_s is not None,
        inclination_deg=i_deg,
    )
    logger.info(f"Orbit propagation model (D6.3.6): Case {_orbit_case}")
    nhit_s1503 = int(sim_cfg.get("s1503_nhit", 16) or 16)
    literal_s1503_d42 = bool(sim_cfg.get("s1503_literal_time_step", True))
    _limits = art22_cfg.get("limits", []) if isinstance(art22_cfg, dict) else []
    # Nmin (D4.6, Table 13): the limits are stored in EXCEEDANCE (CCDF)
    # convention, so the rarest event is the SMALLEST strictly positive
    # exceedance percentage (0.3% ↔ "not exceeded for 99.7% of the time").
    _min_exc_pct = None
    try:
        _cands = [
            float(it[1]) for it in _limits
            if isinstance(it, (list, tuple)) and len(it) >= 2 and float(it[1]) > 0.0
        ]
        if _cands:
            _min_exc_pct = min(_cands)
    except Exception:
        _min_exc_pct = None
    # §D4.1 √Nsatellites needs the REAL fleet size — heterogeneous filings
    # (e.g. CRC STEAM-2: planes of 1/20/43/58 sats) undercount badly via
    # num_planes × sats_per_plane(plane 0).
    _n_sat_total = sum(
        int(p.get("sats_per_plane", 0) or 0)
        for p in (ngso_cfg.get("_planes") or [])
    ) or None
    # §D4 reading (itu_software) — S.1503-4 is ambiguous on whether Ntracks
    # follows N'hit in the §D4.1 recalc. "itu_epfd" (default; legacy
    # "transfinite" maps here) = reading B: N'track = N'hit, as in the current
    # ITU 'T' v5.45 runs; "s1503_4" (legacy agenium/br_space) = reading A:
    # Ntracks kept at 16. θ3dB = 70λ/D in both (all official runs).
    _itu_sw = str(sim_cfg.get("itu_software", "itu_epfd") or "itu_epfd").lower()
    _reading_b = _itu_sw.startswith(("transfinite", "itu"))
    # §D4.1 multi-sub rule: dimension each sub-constellation with its own
    # (a, e, i); smallest Δt + longest run win. Fall back to the top-level
    # single-orbit parameters when per-plane data is unavailable.
    _subs = group_sub_constellations(ngso_cfg.get("_planes") or []) or [{
        "a_km": a_km, "e": ecc, "i_deg": i_deg,
        "num_planes": num_planes, "sats_per_plane": sats_per_plane,
        "min_operating_height_km": min_operating_height_km,
    }]
    try:
        _ts_ref = compute_time_step_and_count_multi(
            _subs,
            min_elevation_deg=min_elev_deg,
            artificial_precession=artificial_precession,
            repeating_ground_track=repeating, repeat_period_days=repeat_days,
            theta_3db_deg=es_antenna.theta_3db_deg, nhit=nhit_s1503,
            literal_s1503_d42=literal_s1503_d42,
            min_exceedance_pct=_min_exc_pct, ntracks=nhit_s1503,
            n_sat_total=_n_sat_total,
            reduce_ntracks_1e8=_reading_b,
        )
        s1503_ref_tstep_s = float(_ts_ref.tstep_s)
        s1503_ref_nsteps = int(_ts_ref.nsteps)
    except Exception as exc:  # noqa: BLE001 — conservative fallback
        logger.warning("Failed computing S.1503 time reference (%s).", exc)
        s1503_ref_tstep_s = 1.0
        s1503_ref_nsteps = 1000

    return DownlinkEngineInputs(
        constellation=constellation,
        mask_id_per_sat=mask_id_per_sat,
        pfd_mask=pfd_mask,
        es_antenna=es_antenna,
        alpha0_deg=alpha0_deg,
        min_elevation_deg=min_elev_deg,
        max_co_freq_by_lat=max_co_freq_by_lat,
        pfd_bw_correction_db=bw_correction_db,
        gso_min_elevation_deg=gso_min_elev_effective_deg,
        strict_max_co_freq_total=strict_max_co_freq_total,
        strict_exclusion_zone=strict_exclusion_zone,
        min_angle_at_es_deg=min_angle_at_es_deg,
        freq_ghz=freq_ghz,
        gmst0_deg=gmst0_deg,
        multi_mask_active=multi_mask_active,
        artificial_precession=artificial_precession,
        raan_dot_override_rad_s=raan_dot_override_rad_s,
        apply_station_keeping=apply_station_keeping,
        wdelta_deg_requested=wdelta_deg_requested,
        num_planes=num_planes,
        s1503_ref_tstep_s=s1503_ref_tstep_s,
        s1503_ref_nsteps=s1503_ref_nsteps,
    )


def run_wcg_downlink(config: dict) -> tuple[
    list[OrbitalElements], WCGResult | None, EPFDSimulationResult | None, ComplianceResult | None,
    WCGResult | None, EPFDSimulationResult | None, ComplianceResult | None
]:
    """Run the full WCG Downlink pipeline."""
    ngso_cfg = config["non_gso"]
    gso_es_cfg = config["gso_es"]
    wcg_cfg = config["wcg_search"]
    sim_cfg = config["simulation"]
    pfd_cfg = config["pfd_mask"]
    art22_cfg = config["article22_limits"]

    gmst0_override_deg = sim_cfg.get("earth_rotation_initial_deg", None)
    if gmst0_override_deg is not None:
        gmst0_deg = float(gmst0_override_deg)
        gmst0_src = "override (config/CLI)"
    elif "_gmst0_deg" in ngso_cfg:
        gmst0_deg = float(ngso_cfg.get("_gmst0_deg", 0.0))
        gmst0_src = "SRS (right_asc-long_asc)"
    else:
        gmst0_deg = 0.0
        gmst0_src = "default"
    set_earth_rotation_initial_deg(gmst0_deg)

    # ================================================================
    #  1. Non-GSO constellation
    # ================================================================
    logger.info("=" * 70)
    logger.info("  WCG DOWNLINK — ITU-R S.1503-4")
    logger.info("=" * 70)
    logger.info(
        f"  Initial Earth rotation (GMST0): {get_earth_rotation_initial_deg():.4f}° "
        f"[{gmst0_src}]"
    )
    if gmst0_src.startswith("SRS"):
        gmst0_dev = float(ngso_cfg.get("_gmst0_max_dev_deg", 0.0) or 0.0)
        if gmst0_dev > 1.0:
            logger.warning(
                f"  GMST0 inferred with inter-plane dispersion = {gmst0_dev:.3f}° "
                "(check RAAN/long_asc consistency in the SRS)"
            )

    a_km = ngso_cfg["semi_major_axis_km"]
    e = ngso_cfg["eccentricity"]
    i_deg = ngso_cfg["inclination_deg"]
    num_planes = ngso_cfg["num_planes"]
    sats_per_plane = ngso_cfg["sats_per_plane"]
    alpha0_deg = ngso_cfg["alpha0_deg"]
    min_elev_deg = ngso_cfg["min_elevation_deg"]
    min_operating_height_km = float(ngso_cfg.get("min_operating_height_km", 0.0) or 0.0)
    max_co_freq_by_lat: list = ngso_cfg.get("max_co_freq_by_lat", [])
    freq_ghz = ngso_cfg["frequency_ghz"]
    gso_min_elev_default_deg, theta_adb_default_deg = _s1503_table8_gso_defaults(freq_ghz)
    gso_min_elev_deg = float(ngso_cfg.get("gso_min_elevation_deg", gso_min_elev_default_deg))
    apply_gso_min_elev = bool(ngso_cfg.get("apply_gso_min_elevation", True))
    strict_max_co_freq_total = bool(ngso_cfg.get("strict_max_co_freq_total", False))
    if strict_max_co_freq_total:
        logger.warning(
            "strict_max_co_freq_total=True: joint cap (standard+OR) — optional mode "
            "that departs from ITU-R S.1503-4 §D5.1.4.1 (Steps 19–22)."
        )
    strict_exclusion_zone = bool(ngso_cfg.get("strict_exclusion_zone", False))
    min_angle_at_es_deg = float(ngso_cfg.get("min_angle_at_es_deg", 0.0) or 0.0)
    gso_min_elev_effective_deg = gso_min_elev_deg if apply_gso_min_elev else -90.0
    ngso_cfg.setdefault("gso_min_elevation_deg", gso_min_elev_deg)
    ngso_cfg.setdefault("apply_gso_min_elevation", apply_gso_min_elev)
    ngso_cfg.setdefault("strict_max_co_freq_total", strict_max_co_freq_total)
    ngso_cfg.setdefault("strict_exclusion_zone", strict_exclusion_zone)
    ngso_cfg.setdefault("min_angle_at_es_deg", min_angle_at_es_deg)
    ngso_cfg.setdefault("s1503_theta_adb_deg", theta_adb_default_deg)

    altitude_km = a_km - RE_KM
    T_orb = compute_orbital_period(a_km)

    # If real per-plane data came in (SRS), the real total may differ from num_planes × sats_per_plane
    planes_data = ngso_cfg.get("_planes", [])
    if planes_data:
        total_sats_real = sum(p.get("sats_per_plane", sats_per_plane) for p in planes_data)
        logger.info(
            f"Constellation (SRS): {len(planes_data)} planes, "
            f"{total_sats_real} satellites total "
            f"(sats/plane per plane: {[p.get('sats_per_plane', sats_per_plane) for p in planes_data]})"
        )
    else:
        total_sats_real = num_planes * sats_per_plane
        logger.info(f"Constellation (Walker): {total_sats_real} satellites ({num_planes}P × {sats_per_plane}S)")

    logger.info(f"  a = {a_km:.2f} km (alt ≈ {altitude_km:.1f} km)")
    logger.info(f"  e = {e:.4f}, i = {i_deg:.1f}°")
    logger.info(f"  T_orb = {T_orb:.1f} s ({T_orb/60:.1f} min)")
    logger.info(f"  α₀ = {alpha0_deg:.1f}° (exclusion zone)")
    logger.info(f"  ε₀ = {min_elev_deg:.1f}° (min elevation)")
    if min_operating_height_km > 0.0:
        logger.info(f"  H_MIN = {min_operating_height_km:.1f} km (minimum operating height)")
    if max_co_freq_by_lat:
        logger.info(
            "  MAX_CO_FREQ: "
            + ", ".join(f"lat[{f:.0f}°,{t:.0f}°]→{n}" for f, t, n in max_co_freq_by_lat)
        )
    else:
        logger.info("  MAX_CO_FREQ: unlimited (no sat_oper restriction)")
    logger.info(
        "  MAX_CO_FREQ (ITU-R S.1503-4 §D5.1.4.1): "
        + (
            "non-normative extension — joint standard+OR cap (--strict-max-co-freq-total)"
            if strict_max_co_freq_total
            else "normative — cap only on Steps 19–21; OR contributors (Step 22) without this limit"
        )
    )
    logger.info(
        "  disable_or_condition (predicate GRX(φ) > min(−30 dB, GRX(α₀)) in the |α|<α₀ zone): "
        + ("ON (treated as false — no gain-based rescue)" if strict_exclusion_zone else "OFF (S.1503-4 OR applied)")
    )
    if apply_gso_min_elev:
        logger.info(
            f"  εGSO = {gso_min_elev_deg:.1f}° "
            f"(Table 8 default @ f={freq_ghz:.2f} GHz: {gso_min_elev_default_deg:.1f}°; applied=ON)"
        )
    else:
        logger.info(
            f"  εGSO = {gso_min_elev_deg:.1f}° "
            f"(Table 8 default @ f={freq_ghz:.2f} GHz: {gso_min_elev_default_deg:.1f}°; applied=OFF)"
        )
    logger.info(f"  f = {freq_ghz:.2f} GHz")
    logger.info(f"  >>> SIMULATION FREQUENCY USED: {freq_ghz:.6f} GHz <<<")

    # mask_lnk1 mapping resolution (S.1503-4 multi-mask).
    mask_assignment, mask_assignment_per_sat = _resolve_mask_lnk1_assignments(
        config, pfd_cfg,
    )

    constellation, mask_id_per_sat = create_constellation_with_masks(
        ngso_cfg,
        mask_assignment,
        mask_assignment_per_sat=mask_assignment_per_sat,
    )
    logger.info(f"  Constellation created: {len(constellation)} satellites ✓")

    # Auto-detect multi-mask: enabled when more than one distinct mask (excluding
    # the -1 sentinel) appears in the parallel list. Sentinel only → single-mask mode.
    _unique_mask_ids_in_sats = sorted({int(m) for m in mask_id_per_sat if int(m) != -1})
    _multi_mask_active = len(_unique_mask_ids_in_sats) > 1
    if _multi_mask_active:
        logger.info(
            "  Multi-mask S.1503-4 detected: %d distinct masks in use "
            "(mask_ids=%s).",
            len(_unique_mask_ids_in_sats),
            _unique_mask_ids_in_sats,
        )

    # ================================================================
    #  2. GSO ES antenna (ITU-R S.1428-1)
    # ================================================================
    es_diameter = gso_es_cfg["antenna_diameter_m"]
    es_efficiency = gso_es_cfg.get("antenna_efficiency", 0.99)

    es_service = str(gso_es_cfg.get("service", "FSS")).upper()
    es_antenna = create_gso_es_antenna(es_diameter, freq_ghz, es_efficiency, service=es_service)
    logger.info(f"GSO ES antenna: {es_antenna}")
    logger.info(f"GSO ES service: {es_service}")

    # ================================================================
    #  3. PFD mask
    # ================================================================
    mask_id = pfd_cfg.get("mask_id", None)
    pfd_source = pfd_cfg.get("source", "xml_file")
    if pfd_source == "mask_mdb":
        from .srs_reader import read_pfd_mask_xml_from_mdb
        pfd_mask_mdb = _resolve_data_path(pfd_cfg["mdb_file"])
        srs_sys = config.get("_srs_system")
        ntc_id = getattr(srs_sys, "ntc_id", None) if srs_sys is not None else None

        if _multi_mask_active:
            # Multi-mask: load each unique mask_id once and pack it into a
            # PFDMaskMulti, routed by mask_id_per_sat.
            from .srs_reader import load_pfd_masks_for_ids
            from .pfd_mask import PFDMaskMulti

            masks_by_id = load_pfd_masks_for_ids(
                pfd_mask_mdb,
                _unique_mask_ids_in_sats,
                ntc_id=ntc_id,
            )
            pfd_mask = PFDMaskMulti(masks_by_id, mask_id_per_sat)
            logger.info(
                "PFD mask source: MASK MDB multi-mask (%s); %d masks loaded.",
                pfd_mask_mdb, len(masks_by_id),
            )
        else:
            # Legacy single-mask.
            pfd_xml_content = read_pfd_mask_xml_from_mdb(
                pfd_mask_mdb,
                mask_id=int(mask_id),
                ntc_id=ntc_id,
            )
            pfd_mask = load_pfd_mask_from_xml_content(pfd_xml_content, mask_id=mask_id)
            logger.info(f"PFD mask source: MASK MDB ({pfd_mask_mdb})")
    else:
        pfd_file = _resolve_data_path(pfd_cfg["file"])
        pfd_mask = load_pfd_mask(
            pfd_file,
            mask_type=pfd_cfg.get("type", "alpha"),
            mask_id=mask_id,
        )
        logger.info(f"PFD mask source: XML ({pfd_file})")
    logger.info(f"PFD mask: {pfd_mask}")

    # Bandwidth (BW) correction
    # EPFD_ref_limit = PFD_ref_mask + 10 * log10(BW_ref_limit / BW_ref_mask)
    limit_bw_khz = art22_cfg.get("reference_bandwidth_khz", 40.0)
    mask_bw_khz = pfd_mask.refbw_khz
    bw_correction_db = 10.0 * math.log10(limit_bw_khz / mask_bw_khz)

    if abs(bw_correction_db) > 0.01:
        logger.info(f"Bandwidth correction: {bw_correction_db:+.2f} dB")
        logger.info(f"  (Mask BW: {mask_bw_khz} kHz -> Limit BW: {limit_bw_khz} kHz)")
    logger.info("")
    logger.info("─" * 50)
    logger.info("  PHASE 1: Worst-Case Geometry (WCG) search")
    logger.info("─" * 50)

    # Reference satellite selection for WCG
    wcg_ref_sat_idx = 0
    if wcg_cfg.get("random_start_sat", False):
        ref_sat = random.choice(constellation)
        wcg_ref_sat_idx = constellation.index(ref_sat)
        logger.info(f"Using random satellite for WCG: index {wcg_ref_sat_idx}")
    else:
        ref_sat = constellation[0]

    ref_pos_eci, _ = elements_to_eci(ref_sat)

    n_jobs = sim_cfg.get("n_jobs", -1)

    # Alpha computation method: "sweep" (classic sweep) or "analytical" (direct Newton)
    alpha_method = str(sim_cfg.get("alpha_method", "sweep")).strip().lower()
    if alpha_method not in ("sweep", "analytical"):
        logger.warning(f"alpha_method '{alpha_method}' invalid; using 'sweep'.")
        alpha_method = "sweep"
    set_alpha_method(alpha_method)
    logger.info(f"  α computation method: {alpha_method}")

    gso_lon_mode = str(sim_cfg.get("gso_longitude_mode", "arc_optimal")).strip().lower()
    if gso_lon_mode not in ("arc_optimal", "es_meridian"):
        logger.warning(f"gso_longitude_mode '{gso_lon_mode}' invalid; using 'arc_optimal'.")
        gso_lon_mode = "arc_optimal"
    set_gso_longitude_mode(gso_lon_mode)
    logger.info(
        f"  GSO longitude for α: {gso_lon_mode} "
        f"({'visible arc (optimal)' if gso_lon_mode == 'arc_optimal' else 'ES longitude (meridian)'})"
    )

    # ================================================================
    #  4. WCG SEARCH (D.3.1.2) - Multi-position Search
    # ================================================================
    logger.info("  Starting WCG search over multiple orbital positions...")
    wcg_search_t0 = time.perf_counter()

    def _no_geometry_diagnostics() -> dict:
        """Config knobs that gate the WCG store criteria — the actionable levers
        when the search finds no valid geometry (attached to NoValidGeometry).
        Only reads scalars assigned before the search branch split, so it is safe
        from every no-geometry return site."""
        return {
            "alpha0_deg": float(alpha0_deg),
            "min_elevation_deg": float(min_elev_deg),
            "gso_min_elevation_deg": float(gso_min_elev_deg),
            "gso_min_elevation_effective_deg": float(gso_min_elev_effective_deg),
            "apply_gso_min_elevation": bool(apply_gso_min_elev),
            "strict_exclusion_zone": bool(strict_exclusion_zone),
            "frequency_ghz": float(freq_ghz),
            "n_satellites": int(total_sats_real),
        }

    use_s1503_algo = wcg_cfg.get("use_s1503_algo", False)
    manual_cfg = wcg_cfg.get("manual_wcg", {}) if isinstance(wcg_cfg.get("manual_wcg", {}), dict) else {}
    # CLI/export_visualization use enabled=True; old API runs may only have the degree keys.
    manual_enabled = bool(manual_cfg.get("enabled", False)) or (
        all(k in manual_cfg for k in ("es_lat_deg", "es_lon_deg", "gso_lon_deg"))
    )

    if manual_enabled:
        # ── Manual geometry (no WCG search) ─────────────────────────────────
        es_lat = float(manual_cfg["es_lat_deg"])
        es_lon = float(manual_cfg["es_lon_deg"])
        gso_lon = float(manual_cfg["gso_lon_deg"])
        align_constellation = bool(manual_cfg.get("align_constellation", True))
        if not (-90.0 <= es_lat <= 90.0):
            raise InvalidManualGeometry(f"Invalid manual ES latitude: {es_lat:.3f}° (expected [-90, 90])")
        if not (-180.0 <= es_lon <= 180.0):
            raise InvalidManualGeometry(f"Invalid manual ES longitude: {es_lon:.3f}° (expected [-180, 180])")
        if not (-180.0 <= gso_lon <= 180.0):
            raise InvalidManualGeometry(f"Invalid manual GSO longitude: {gso_lon:.3f}° (expected [-180, 180])")
        logger.info("  Mode: Manual geometry (WCG bypass)")
        logger.info(f"    Manual ES:  lat={es_lat:+.4f}°, lon={es_lon:+.4f}°")
        logger.info(f"    Manual GSO:  lon={gso_lon:+.4f}°")
        logger.info(f"    Constellation alignment: {'ON' if align_constellation else 'OFF'}")

        if align_constellation:
            delta_m, wcg_result = _optimize_delta_m_for_fixed_es_gso(
                constellation=constellation,
                es_lat_deg=es_lat,
                es_lon_deg=es_lon,
                gso_lon_deg=gso_lon,
                t_s=0.0,
                pfd_mask=pfd_mask,
                es_antenna=es_antenna,
                alpha0_deg=alpha0_deg,
                min_elevation_deg=min_elev_deg,
                gso_min_elevation_deg=gso_min_elev_effective_deg,
                pfd_bw_correction_db=bw_correction_db,
            )
        else:
            delta_m = 0.0
            wcg_result, _ = _evaluate_fixed_es_gso_geometry(
                constellation=constellation,
                es_lat_deg=es_lat,
                es_lon_deg=es_lon,
                gso_lon_deg=gso_lon,
                t_s=0.0,
                pfd_mask=pfd_mask,
                es_antenna=es_antenna,
                alpha0_deg=alpha0_deg,
                min_elevation_deg=min_elev_deg,
                gso_min_elevation_deg=gso_min_elev_effective_deg,
                pfd_bw_correction_db=bw_correction_db,
            )
        if wcg_result is None:
            logger.error("FAILURE: manual ES/GSO geometry invalid for the elevation/exclusion criteria.")
            raise NoValidGeometry(
                "Manual ES/GSO geometry is invalid for the elevation/exclusion "
                "criteria (minimum elevation ε₀, GSO-arc elevation εGSO, "
                "exclusion angle α₀).",
                diagnostics={
                    **_no_geometry_diagnostics(),
                    "mode": "manual",
                    "es_lat_deg": es_lat,
                    "es_lon_deg": es_lon,
                    "gso_lon_deg": gso_lon,
                },
            )

        ref_sat = constellation[0]
        ref_pos_eci = wcg_result.ref_sat_eci.copy()
        best_overall_wcg = wcg_result
        best_M = math.degrees(ref_sat.M)
        if align_constellation:
            logger.info(
                f"  Manual alignment applied: ΔM={math.degrees(delta_m):+.3f}° "
                f"(ref_sat.M={best_M:.3f}°)"
            )
        else:
            logger.info(
                f"  Manual alignment disabled: ΔM={math.degrees(delta_m):+.3f}° "
                f"(ref_sat.M={best_M:.3f}°)"
            )
        logger.info(
            f"  Final manual geometry: ES=({wcg_result.es_lat_deg:+.4f}°, {wcg_result.es_lon_deg:+.4f}°), "
            f"GSO lon={wcg_result.gso_lon_deg:+.4f}°, EPFD agg={wcg_result.epfd_aggregate_dBW:.2f} dB"
        )
    elif use_s1503_algo:
        # ── Analytical algorithm S.1503-4 §D.3.1 (WCGA_Down) ───────────
        logger.info("  Mode: WCGA S.1503-4 (sweeping the system satellites)")
        if wcg_cfg.get("random_start_sat", False):
            logger.info("  Warning: random_start_sat ignored in global WCGA S.1503 mode.")

        _search_wcg_s1503_impl = search_wcg_s1503

        step_size_deg = wcg_cfg.get("s1503_step_deg", 0.1)
        if "s1503_symmetric_mask" in wcg_cfg and wcg_cfg.get("s1503_symmetric_mask") is not None:
            symmetric_mask = bool(wcg_cfg.get("s1503_symmetric_mask"))
            logger.info(
                "  WCGA symmetry: manual override %s.",
                "enabled" if symmetric_mask else "disabled",
            )
        else:
            try:
                symmetric_mask, symmetry_reason = pfd_mask.detect_wcg_theta_symmetry()
            except Exception as exc:
                symmetric_mask = False
                symmetry_reason = f"symmetry_detection_failed:{exc}"
                logger.warning(
                    "  Failed to detect WCGA symmetry automatically; using full circle. (%s)",
                    exc,
                )
            logger.info(
                "  WCGA symmetry detected automatically: %s (%s).",
                "yes" if symmetric_mask else "no",
                symmetry_reason,
            )
        collect_all_points = bool(wcg_cfg.get("s1503_trail_all_points", False))
        bin_size_db = 0.1

        # S.1503-4 §D.3.1.2 — `EPFDThreshold[lat]`. For the cases where Notes
        # 22.5C.4 (Table 22-1A, D>60 cm) or 22.5C.8 (Table 22-1D, D≥180 cm)
        # impose a latitude-varying limit, the WCGA ranking becomes based on
        # the margin (EPFD − limit). In the other cases the threshold is
        # constant and the ordering coincides with that of absolute EPFD (legacy).
        from .article22_tables import build_epfd_threshold_by_lat_fn
        epfd_threshold_by_lat_fn = build_epfd_threshold_by_lat_fn(
            rr_reference=art22_cfg.get("rr_reference"),
            rf_diam_cm=art22_cfg.get("_epfd_rf_diam_cm"),
            reference_bandwidth_khz=float(
                art22_cfg.get("reference_bandwidth_khz", 40.0) or 40.0
            ),
            curve=art22_cfg.get("limits"),
        )
        if getattr(epfd_threshold_by_lat_fn, "latitude_dependent", False):
            logger.info(
                "  EPFDThreshold[lat] active (Note %s): WCGA ranks by margin "
                "(EPFD − limit(lat)); limits −160 dB (|lat|≤57.5°) → −165.3 dB "
                "(|lat|≥63.75°).",
                getattr(epfd_threshold_by_lat_fn, "note", "?"),
            )

        best_overall_wcg = None
        best_overall_margin = -float("inf")
        best_overall_epfd = -float("inf")
        best_overall_ang_vel = float("inf")
        best_overall_idx = None

        # S.1503 D3.1.2: iterate satellites in database order and avoid
        # recomputing already-checked combinations. In multi-mask mode, the key
        # includes the mask **content hash** — not the mask_id. Masks registered
        # under different mask_ids but with a 100% identical PFD curve collapse
        # to the same key, avoiding redundant WCGA.
        checked_orbit_keys: set[tuple] = set()
        total_sats = len(constellation)
        evaluated_unique = 0

        # Cache mask_id → content_hash to avoid recomputing per sat.
        _mask_hash_cache: dict[int, str] = {}

        def _mask_hash_for_sat(_sat_idx_zero_based: int) -> str:
            mid = int(mask_id_per_sat[_sat_idx_zero_based])
            cached = _mask_hash_cache.get(mid)
            if cached is not None:
                return cached
            sub = pfd_mask.mask_for_sat(_sat_idx_zero_based)  # type: ignore[union-attr]
            h = sub.content_hash()
            _mask_hash_cache[mid] = h
            return h

        def _orbit_key(_oe, _sat_idx_zero_based: int) -> tuple:
            base = (
                round(float(_oe.a), 6),
                round(float(_oe.e), 9),
                round(float(_oe.i), 9),
            )
            if _multi_mask_active:
                return base + (_mask_hash_for_sat(_sat_idx_zero_based),)
            return base

        _unique_keys_pre: set[tuple] = set()
        for _idx, _oe in enumerate(constellation):
            _unique_keys_pre.add(_orbit_key(_oe, _idx))
        total_unique_orbits = len(_unique_keys_pre)
        if _multi_mask_active:
            # Count unique mask_ids vs unique hashes to detect redundancy.
            unique_hashes = {h for h in _mask_hash_cache.values()}
            duped_ids = len(_mask_hash_cache) - len(unique_hashes)
            if duped_ids > 0:
                logger.info(
                    "  WCGA dedup: %d mask_id(s) with redundant PFD content "
                    "(collapsed to %d unique curve(s)).",
                    duped_ids, len(unique_hashes),
                )
        logger.info(
            f"  WCGA: {total_unique_orbits} unique "
            f"{'(a,e,i,mask_hash)' if _multi_mask_active else '(a,e,i)'} "
            f"combination(s) to evaluate out of {total_sats} satellites."
        )

        for sat_idx, sat_oe in enumerate(constellation, start=1):
            orbit_key = _orbit_key(sat_oe, sat_idx - 1)
            if orbit_key in checked_orbit_keys:
                continue
            checked_orbit_keys.add(orbit_key)
            evaluated_unique += 1

            _mask_id_log = f", mask_id={mask_id_per_sat[sat_idx - 1]}" if _multi_mask_active else ""
            logger.info(
                f"  > WCGA sat {sat_idx}/{total_sats} "
                f"(a={sat_oe.a:.3f} km, e={sat_oe.e:.6f}, i={math.degrees(sat_oe.i):.3f}°"
                f"{_mask_id_log})"
            )

            # In multi-mask mode, the per-satellite WCGA runs against its own
            # specific mask (and not the aggregator's primary one). The
            # PFDMaskMulti is kept for the final aggregator, which queries each
            # aggressor's mask via sat_idx; here we pass the ref_sat point curve.
            if _multi_mask_active:
                _ref_sat_mask = pfd_mask.mask_for_sat(sat_idx - 1)  # type: ignore[union-attr]
            else:
                _ref_sat_mask = pfd_mask

            cand_wcg = _search_wcg_s1503_impl(
                oe_ref=sat_oe,
                t_s=0.0,
                pfd_mask=_ref_sat_mask,
                es_antenna=es_antenna,
                alpha0_deg=alpha0_deg,
                min_elevation_deg=min_elev_deg,
                gso_min_elevation_deg=gso_min_elev_effective_deg,
                pfd_bw_correction_db=bw_correction_db,
                step_size_deg=step_size_deg,
                symmetric_mask=symmetric_mask,
                n_jobs=n_jobs,
                collect_all_points=collect_all_points,
                max_co_freq_by_lat=max_co_freq_by_lat,
                strict_exclusion_zone=strict_exclusion_zone,
                orbit_idx=evaluated_unique,
                total_orbits=total_unique_orbits,
                epfd_threshold_by_lat_fn=epfd_threshold_by_lat_fn,
            )
            if cand_wcg is None:
                continue

            cand_epfd = float(cand_wcg.epfd_dBW)
            cand_ang_vel = float(getattr(cand_wcg, "angular_velocity_deg_s", math.inf))
            # Margin (S.1503 §D.3.1.2): inter-orbit ranking also by margin when
            # 22.5C.4/8 applies (when it does not, the threshold is constant and
            # comparing by margin is equivalent to comparing by absolute EPFD).
            cand_threshold = float(epfd_threshold_by_lat_fn(cand_wcg.es_lat_deg))
            cand_margin = cand_epfd - cand_threshold
            if (
                cand_margin > best_overall_margin + bin_size_db
                or (
                    abs(cand_margin - best_overall_margin) <= bin_size_db
                    and cand_ang_vel < best_overall_ang_vel
                )
            ):
                best_overall_wcg = cand_wcg
                best_overall_margin = cand_margin
                best_overall_epfd = cand_epfd
                best_overall_ang_vel = cand_ang_vel
                best_overall_idx = sat_idx - 1

        logger.info(
            f"  Global WCGA S.1503: {evaluated_unique} unique orbital combinations "
            f"evaluated out of {total_sats} satellites."
        )

        # best_overall_wcg already carries search_trail_all of the winning orbit
        # (collect_all_points was passed directly in the loop call above).
        # No re-run is needed.
        wcg_result = best_overall_wcg

        if wcg_result is not None and best_overall_idx is not None and np.linalg.norm(wcg_result.ref_sat_eci) > 1e-6:
            wcg_ref_sat_idx = best_overall_idx
            ref_sat = constellation[best_overall_idx]
            M_orig_0 = ref_sat.M
            M_wcg_rad = eci_to_mean_anomaly(ref_sat, wcg_result.ref_sat_eci)
            delta_M = (M_wcg_rad - M_orig_0) % (2.0 * math.pi)
            if delta_M > math.pi:
                delta_M -= 2.0 * math.pi
            for oe in constellation:
                oe.M = (oe.M + delta_M) % (2.0 * math.pi)
            ref_sat = constellation[best_overall_idx]
            ref_pos_eci = wcg_result.ref_sat_eci.copy()
            best_M = math.degrees(ref_sat.M)
            logger.info(
                f"  Alignment to WCG: ΔM = {math.degrees(delta_M):+.1f}° applied to the whole constellation "
                f"(sat={best_overall_idx}, M={best_M:.1f}°, ω_ang={best_overall_ang_vel:.6f} deg/s)"
            )
        else:
            best_M = ref_sat.M * RAD2DEG
    else:
        # ── Uniform grid (original algorithm) ───────────────────────────
        logger.info("  Mode: WCG search on a uniform grid (legacy; not WCGA §D.3.1.2)")
        # Define mean-anomaly candidates to cover maximum latitude and the equator
        # 0 (ascending equator), 90 (lat max), 180 (descending equator), 270 (lat min)
        candidate_Ms = [0.0, 90.0, 180.0, 270.0]

        best_overall_wcg = None
        best_overall_epfd = -999.0

        # Preserve the original satellite for restoration
        orig_M = ref_sat.M

        for M_cand in candidate_Ms:
            # Position the reference satellite at the candidate anomaly
            ref_sat.M = M_cand * DEG2RAD
            cand_pos_eci, cand_vel_eci = elements_to_eci(ref_sat)

            logger.info(f"  > Evaluating WCG for satellite at M={M_cand:.0f}°...")

            cand_wcg = search_wcg(
                ngso_sat_eci=cand_pos_eci,
                ngso_sat_vel_eci=cand_vel_eci,
                t_s=0.0,
                pfd_mask=pfd_mask,
                es_antenna=es_antenna,
                alpha0_deg=alpha0_deg,
                min_elevation_deg=min_elev_deg,
                gso_min_elevation_deg=gso_min_elev_effective_deg,
                phi_step_deg=wcg_cfg.get("phi_step_deg", 0.5),
                phi_max_deg=wcg_cfg.get("phi_max_deg", 70.0),
                theta_min_deg=wcg_cfg.get("theta_min_deg", -90.0),
                theta_max_deg=wcg_cfg.get("theta_max_deg", 270.0),
                fixed_es_lat=wcg_cfg.get("es_lat_deg"),
                fixed_es_lon=wcg_cfg.get("es_lon_deg"),
                n_jobs=n_jobs,
                pfd_bw_correction_db=bw_correction_db,
                strict_exclusion_zone=strict_exclusion_zone,
            )

            if cand_wcg and cand_wcg.epfd_dBW > best_overall_epfd:
                best_overall_epfd = cand_wcg.epfd_dBW
                best_overall_wcg = cand_wcg
                best_M = M_cand
                ref_pos_eci = cand_pos_eci

        # Apply the best mean anomaly found to the reference satellite
        if best_overall_wcg is not None:
            ref_sat.M = best_M * DEG2RAD
            logger.info(f"  Adjusting reference satellite to M={best_M:.1f}° (WCG)")
        else:
            ref_sat.M = orig_M

        constellation[0] = ref_sat
        wcg_ref_sat_idx = 0
        logger.info(f"  Constellation[0].M updated to {constellation[0].M * RAD2DEG:.1f}°")

        wcg_result = best_overall_wcg
        if wcg_result:
            logger.info(f"  Best WCG found with satellite at M={best_M:.0f}° (EPFD={best_overall_epfd:.2f} dB)")

    if wcg_result is None:
        logger.error("FAILURE: No valid geometry found in the WCG search!")
        logger.error("Check: exclusion angle α₀, minimum elevation, PFD mask.")
        raise NoValidGeometry(
            "No valid geometry found in the WCG search. Check the exclusion "
            "angle α₀, the minimum elevation ε₀, the GSO-arc minimum elevation "
            "εGSO (Table 8), and the PFD mask.",
            diagnostics={**_no_geometry_diagnostics(), "mode": "search"},
        )
    wcg_search_elapsed_s = time.perf_counter() - wcg_search_t0

    # ================================================================
    #  4.2 WCG NOMINALS (visualization / trail; raw WCGA values)
    # ================================================================
    # Fig. 13 (longitudinal adjustment) runs **after** Phase 2 (fine step resolved),
    # see ``apply_s1503_figure13_wcg_longitude_adjustment``.
    wcg_result.es_lat_nominal = wcg_result.es_lat_deg
    wcg_result.es_lon_nominal = wcg_result.es_lon_deg
    wcg_result.gso_lon_nominal = wcg_result.gso_lon_deg

    logger.info("  WCG nominals (raw search output, before Fig. 13):")
    logger.info(f"    ES (nominal = search): {wcg_result.es_lat_nominal:.4f}°, {wcg_result.es_lon_nominal:.4f}°")

    # ================================================================
    #  4.3 ALIGNMENT VERIFICATION (Debug)
    # ================================================================
    # Confirms consistency of the reference position used by the WCG at t=0
    if manual_enabled:
        verify_pos_eci = wcg_result.ref_sat_eci.copy()
    else:
        verify_pos_eci, _ = elements_to_eci(constellation[wcg_ref_sat_idx])
    verify_ecef = eci_to_ecef(verify_pos_eci, 0.0)
    ref_ecef = eci_to_ecef(ref_pos_eci, 0.0)
    
    dist_eci = np.linalg.norm(verify_pos_eci - ref_pos_eci)
    dist_ecef = np.linalg.norm(verify_ecef - ref_ecef)
    
    if np.linalg.norm(getattr(wcg_result, "es_ecef_exact", np.zeros(3))) > RE_KM * 0.9:
        es_ecef_verify = wcg_result.es_ecef_exact.copy()
    else:
        es_ecef_verify = lla_to_ecef(wcg_result.es_lat_deg, wcg_result.es_lon_deg, 0.0)
    gso_ecef_verify = gso_position_ecef(wcg_result.gso_lon_deg, 0.0)
    
    elev_verify = compute_elevation(es_ecef_verify, verify_ecef,
                                     wcg_result.es_lat_deg, wcg_result.es_lon_deg)
    alpha_verify = compute_alpha_angle_fast(es_ecef_verify, verify_ecef, gso_ecef_verify)
    
    logger.info(f"  Alignment verification (t=0):")
    logger.info(f"    ref_pos_eci (WCG):      [{ref_pos_eci[0]:.3f}, {ref_pos_eci[1]:.3f}, {ref_pos_eci[2]:.3f}] km")
    if manual_enabled:
        logger.info(f"    evaluated satellite (sim): [{verify_pos_eci[0]:.3f}, {verify_pos_eci[1]:.3f}, {verify_pos_eci[2]:.3f}] km")
    else:
        logger.info(
            f"    constellation[{wcg_ref_sat_idx}] (sim): "
            f"[{verify_pos_eci[0]:.3f}, {verify_pos_eci[1]:.3f}, {verify_pos_eci[2]:.3f}] km"
        )
    logger.info(f"    Δ ECI:  {dist_eci:.6f} km ({dist_eci*1000:.3f} m)")
    logger.info(f"    Δ ECEF: {dist_ecef:.6f} km ({dist_ecef*1000:.3f} m)")
    if manual_enabled:
        logger.info(f"    Marker sat elevation: {elev_verify:.2f}°")
        logger.info(f"    Marker sat α:         {alpha_verify:.2f}°")
    else:
        logger.info(f"    Ref sat elevation: {elev_verify:.2f}° (WCG: {wcg_result.elevation_deg:.2f}°)")
        logger.info(f"    Ref sat α:         {alpha_verify:.2f}° (WCG: {wcg_result.alpha_deg:.2f}°)")
    if dist_eci < 0.001:
        logger.info(f"    ✅ Perfect alignment confirmed!")
    else:
        if manual_enabled:
            logger.warning(f"    ⚠️ MISALIGNMENT: {dist_eci:.3f} km between WCG ref and evaluated satellite")
        else:
            logger.warning(
                f"    ⚠️ MISALIGNMENT: {dist_eci:.3f} km between WCG ref and "
                f"constellation[{wcg_ref_sat_idx}]"
            )

    # ================================================================
    #  4.5 AGGREGATE EPFD AT THE WCG (S.1503-4 D.3.1.2 + D.5)
    # ================================================================
    # The ``epfd_aggregate_dBW`` value is filled after PHASE 2 (time step),
    # with the same logic as the temporal simulation (MAX_CO_FREQ, standard/override,
    # ``propagate_and_to_ecef_batch`` at t=0), to align the "WCG peak" to instant t=0 of D.5.

    logger.info(f"  WCG found:")
    logger.info(f"    θ = {wcg_result.theta_deg:.2f}°, φ = {wcg_result.phi_deg:.2f}°")
    logger.info(f"    ES: lat = {wcg_result.es_lat_deg:.2f}°, lon = {wcg_result.es_lon_deg:.2f}°")
    logger.info(f"    GSO: lon = {wcg_result.gso_lon_deg:.2f}°")
    logger.info(f"    α = {wcg_result.alpha_deg:.2f}°")
    logger.info(f"    off-axis φ = {wcg_result.offaxis_deg:.2f}°")
    logger.info(f"    Single-entry EPFD = {wcg_result.epfd_dBW:.1f} dBW/m²/BWref")
    logger.info(f"    Elevation = {wcg_result.elevation_deg:.1f}°")

    # ================================================================
    #  5. TIME STEP (D.4)
    # ================================================================
    logger.info("")
    logger.info("─" * 50)
    logger.info("  PHASE 2: Time Step computation")
    logger.info("─" * 50)

    # Artificial precession: explicit (CLI/config) or auto via MDB (f_precess, rpt_period, num_planes)
    artificial_precession = sim_cfg.get("artificial_precession")
    if artificial_precession is None and "artificial_precession" not in sim_cfg:
        # Auto (only when MDB data is available): non-repeating orbit + few planes
        if "_rpt_period_s" in ngso_cfg or "_f_precess" in ngso_cfg:
            rpt = ngso_cfg.get("_rpt_period_s", 0) or 0
            f_precess = ngso_cfg.get("_f_precess", False)
            if rpt <= 0 and num_planes < 12 and not f_precess:
                artificial_precession = True
                logger.info(
                    f"Auto artificial precession (MDB: f_precess=N, rpt_period=0, {num_planes} planes): "
                    "enabled for better CCDF sampling"
                )
    if artificial_precession is None:
        artificial_precession = False

    # MDB precession: use precession_deg_day instead of J2 when requested
    raan_dot_override_rad_s = None
    if sim_cfg.get("use_precession_mdb", False):
        pday = ngso_cfg.get("_precession_deg_day", 0.0)
        if pday != 0.0:
            raan_dot_override_rad_s = (pday * DEG2RAD) / 86400.0
            logger.info(
                f"Using MDB precession: {pday:.4f} °/day → Ω̇ = {math.degrees(raan_dot_override_rad_s):.6f} °/s"
            )

    # S.1503-4 D6.3.6 (Fig. 52): the three orbit cases are mutually exclusive.
    # Admin-supplied precession (Case 3) takes precedence over the artificial/
    # forced precession of Case 1 — they must never be combined.
    if raan_dot_override_rad_s is not None and artificial_precession:
        logger.warning(
            "Admin precession (Case 3) and artificial precession (Case 1) both "
            "requested — disabling artificial precession per D6.3.6 (mutually exclusive)."
        )
        artificial_precession = False

    # §D4 reading A/B (see build_downlink_engine_inputs).
    _itu_sw = str(sim_cfg.get("itu_software", "itu_epfd") or "itu_epfd").lower()
    _reading_b = _itu_sw.startswith(("transfinite", "itu"))
    theta_3db_deg = es_antenna.theta_3db_deg
    nhit_s1503 = int(sim_cfg.get("s1503_nhit", 16) or 16)
    literal_s1503_d42 = bool(sim_cfg.get("s1503_literal_time_step", True))
    sim_meta = config.setdefault("simulation", {})

    repeating = sim_cfg.get("repeating_ground_track", False)
    repeat_days = sim_cfg.get("repeat_period_days", 1.0)
    if not repeating and ngso_cfg.get("_rpt_period_s", 0) > 0:
        # §D4.6.1 keyed on station keeping (orbit.f_stn_keep), matching the BR
        # software — see build_downlink_engine_inputs for the rationale.
        rpt_s = ngso_cfg["_rpt_period_s"]
        _, _n_orb = repeat_track_is_physical(rpt_s, a_km)
        if rpt_s >= 3600 and bool(ngso_cfg.get("_f_stn_keep", False)):
            repeat_days = rpt_s / 86400.0
            repeating = True
            logger.info(
                f"Repeating orbit (MDB f_stn_keep=Y): P_repeat = {repeat_days:.2f} days "
                f"({_n_orb:.4f} orbits/repeat, track held by station keeping)"
            )
        elif rpt_s >= 3600:
            logger.info(
                f"Declared repeat (P_repeat = {rpt_s / 86400.0:.2f} days) ignored: "
                f"f_stn_keep=N (no station keeping to hold the track; "
                f"{_n_orb:.4f} orbits/repeat) — using non-repeating §D4.6.2 dimensioning."
            )
    _orbit_case = classify_orbit_case(
        repeating_ground_track=bool(repeating),
        admin_precession=raan_dot_override_rad_s is not None,
        inclination_deg=i_deg,
    )
    logger.info(f"Orbit propagation model (D6.3.6): Case {_orbit_case}")
    limits_for_nmin = art22_cfg.get("limits", []) if isinstance(art22_cfg, dict) else []
    # Nmin (D4.6, Table 13): the limits are stored in EXCEEDANCE (CCDF)
    # convention (see ``article22_limits`` above), so the rarest event is the
    # SMALLEST strictly positive exceedance percentage in the table
    # (0.3% ↔ "not exceeded for 99.7% of the time" → Nmin = NS·100/0.3).
    min_exc_pct = None
    try:
        pct_candidates = [
            float(item[1]) for item in limits_for_nmin
            if isinstance(item, (list, tuple)) and len(item) >= 2 and float(item[1]) > 0.0
        ]
        if pct_candidates:
            min_exc_pct = min(pct_candidates)
    except Exception:
        min_exc_pct = None

    phi_coarse_deg = float(sim_cfg.get("s1503_phi_coarse_deg", 1.5) or 1.5)
    # Real fleet size for §D4.1 √Nsatellites (heterogeneous filings).
    _n_sat_total = sum(
        int(p.get("sats_per_plane", 0) or 0)
        for p in (ngso_cfg.get("_planes") or [])
    ) or None
    # §D4.1 multi-sub rule (see build_downlink_engine_inputs).
    _subs = group_sub_constellations(ngso_cfg.get("_planes") or []) or [{
        "a_km": a_km, "e": e, "i_deg": i_deg,
        "num_planes": num_planes, "sats_per_plane": sats_per_plane,
        "min_operating_height_km": min_operating_height_km,
    }]
    _ts_result = compute_time_step_and_count_multi(
        _subs,
        min_elevation_deg=min_elev_deg,
        artificial_precession=artificial_precession,
        repeating_ground_track=repeating,
        repeat_period_days=repeat_days,
        theta_3db_deg=theta_3db_deg,
        nhit=nhit_s1503,
        literal_s1503_d42=literal_s1503_d42,
        min_exceedance_pct=min_exc_pct,
        ntracks=nhit_s1503,
        phi_coarse_deg=phi_coarse_deg,
        n_sat_total=_n_sat_total,
        reduce_ntracks_1e8=_reading_b,
    )
    s1503_tstep = _ts_result.tstep_s
    s1503_nsteps = _ts_result.nsteps
    s1503_raan_dot_artificial = _ts_result.raan_dot_artificial_rad_s
    # Ncoarse consistent with the Nhit actually used (D4.7, and N'coarse when the
    # §D4.1 1e8 recalculation reduced the resolution for non-repeating orbits).
    recommended_ncoarse = int(_ts_result.ncoarse)

    sim_meta["_s1503_reference_time_step_s"] = float(s1503_tstep)
    sim_meta["_s1503_reference_num_time_steps"] = int(s1503_nsteps)
    sim_meta["_s1503_reference_raan_dot_artificial_rad_s"] = float(s1503_raan_dot_artificial)
    sim_meta["_s1503_reference_dual_fine_step_s"] = float(s1503_tstep)
    sim_meta["_s1503_reference_dual_coarse_step_s"] = float(s1503_tstep * recommended_ncoarse)
    sim_meta["_s1503_reference_dual_ncoarse"] = int(recommended_ncoarse)

    if sim_cfg.get("num_time_steps", 0) > 0:
        nsteps = int(sim_cfg["num_time_steps"])
        fine_ov = bool(sim_cfg.get("_fine_step_overridden", False))
        coarse_ov = bool(sim_cfg.get("_coarse_step_overridden", False))
        fine_val = float(sim_cfg.get("fine_time_step_s", 0.0) or 0.0)

        # Without --fine-time-step-s / --coarse-time-step-s: same literal §D4.2 Tfine as
        # when num_time_steps=0 (do not use coarse_time_step_s by default, e.g. 1.0 s).
        if fine_ov and fine_val > 0.0:
            tstep = fine_val
        elif (not coarse_ov) and fine_val > 0.0:
            tstep = fine_val
        else:
            tstep = float(s1503_tstep)

        raan_dot_artificial = 0.0
        if artificial_precession:
            T_run = nsteps * tstep
            raan_dot_artificial = (2.0 * math.pi) / T_run
            logger.info(f"  Artificial precession active: Ω̇_art = {math.degrees(raan_dot_artificial):.4f} °/s")
        logger.info(f"  S.1503 reference: Δt={s1503_tstep:.4f}s, N={s1503_nsteps}")
        logger.info(
            "  S.1503 dual-step reference: "
            f"Tfine={s1503_tstep:.6f}s, Tcoarse={s1503_tstep * recommended_ncoarse:.6f}s, "
            f"Ncoarse={recommended_ncoarse}"
        )
        logger.info(
            f"  Manual N={nsteps}; effective Tfine (time base)={tstep:.6f}s "
            f"({'fine override' if fine_ov and fine_val > 0 else 'literal S.1503 §D4.2'})"
        )
    else:
        tstep, nsteps, raan_dot_artificial = (
            s1503_tstep,
            s1503_nsteps,
            s1503_raan_dot_artificial,
        )

    # Dual time step
    dual_ts = None
    dual_mode = str(sim_cfg.get("dual_time_step_mode", "s1503")).strip().lower()
    if dual_mode not in {"s1503", "alpha_threshold", "off"}:
        logger.warning(
            f"dual_time_step_mode='{dual_mode}' invalid. Using 's1503'."
        )
        dual_mode = "s1503"

    if dual_mode == "s1503":
        fine_override = bool(sim_cfg.get("_fine_step_overridden", False))
        coarse_override = bool(sim_cfg.get("_coarse_step_overridden", False))

        fine_step = float(sim_cfg.get("fine_time_step_s", 0.0) or 0.0) if fine_override else 0.0
        if fine_step <= 0.0:
            fine_step = tstep

        ncoarse_cfg = sim_cfg.get("s1503_ncoarse", None)
        if ncoarse_cfg is None:
            ncoarse = recommended_ncoarse
        else:
            ncoarse = max(1, int(ncoarse_cfg))

        coarse_step = float(sim_cfg.get("coarse_time_step_s", 0.0) or 0.0) if coarse_override else 0.0
        if coarse_step <= 0.0:
            coarse_step = fine_step * ncoarse
        else:
            ratio = coarse_step / fine_step
            ratio_i = max(1, int(round(ratio)))
            if abs(ratio - ratio_i) > 1e-9:
                logger.warning(
                    "Coarse Δt is not an integer multiple of fine Δt; "
                    "adjusting to an integer multiple per D4.7."
                )
                coarse_step = fine_step * ratio_i
            ncoarse = ratio_i

        dual_ts = DualTimeStep(
            coarse_step_s=coarse_step,
            fine_step_s=fine_step,
            mode="s1503_gain",
            ncoarse=ncoarse,
            es_antenna=es_antenna,
            alpha0_deg=alpha0_deg,
            disable_or_condition=strict_exclusion_zone,
        )
        _nhit_eff = float(_ts_result.nhit_eff)
        _nhit_note = (
            f"Nhit={nhit_s1503}"
            if abs(_nhit_eff - nhit_s1503) < 1e-9
            else f"Nhit={nhit_s1503}→N'hit={_nhit_eff:.4f} (§D4.1 1e8 recalc)"
        )
        logger.info(
            "  Dual time step (S.1503 D4.7): "
            f"fine={dual_ts.fine:.6f}s, coarse={dual_ts.coarse:.6f}s, "
            f"Ncoarse={dual_ts.ncoarse}, θ3dB={theta_3db_deg:.4f}°, {_nhit_note}"
        )
        sim_meta["_resolved_dual_fine_step_s"] = float(dual_ts.fine)
        sim_meta["_resolved_dual_coarse_step_s"] = float(dual_ts.coarse)
        sim_meta["_resolved_dual_ncoarse"] = int(dual_ts.ncoarse)
    elif dual_mode == "alpha_threshold":
        if sim_cfg.get("fine_time_step_s", 0) > 0:
            dual_ts = DualTimeStep(
                coarse_step_s=sim_cfg["coarse_time_step_s"],
                fine_step_s=sim_cfg["fine_time_step_s"],
                alpha_threshold_deg=sim_cfg.get("fine_step_alpha_threshold_deg", 2.0),
                mode="alpha_threshold",
            )
            logger.info(
                f"  Dual time step (legacy α): coarse={dual_ts.coarse:.3f}s, "
                f"fine={dual_ts.fine:.3f}s, threshold={dual_ts.threshold:.1f}°"
            )

    sim_meta["_resolved_time_step_s"] = float(tstep)
    sim_meta["_resolved_num_time_steps"] = int(nsteps)
    sim_meta["_resolved_raan_dot_artificial_rad_s"] = float(raan_dot_artificial)

    static_wcg = None
    static_sim_result = None
    static_compliance = None
    static_sim_elapsed_s = 0.0
    static_shared_timeline = False

    static_es_cfg = sim_cfg.get("static_es")
    run_static_es = bool(sim_cfg.get("run_static_es", True))
    if static_es_cfg and run_static_es:
        s_cfg = static_es_cfg
        from .wcg_search import WCGResult
        static_wcg = WCGResult(
            theta_deg=0, phi_deg=0,
            es_lat_deg=s_cfg["lat_deg"],
            es_lon_deg=s_cfg["lon_deg"],
            gso_lon_deg=s_cfg.get("gso_lon_deg", s_cfg["lon_deg"]),
            alpha_deg=0, offaxis_deg=0, pfd_dBW=0, es_gain_rel_dB=0, epfd_dBW=0, elevation_deg=0
        )
    elif static_es_cfg and not run_static_es:
        logger.info("")
        logger.info("  PHASE 3B: EPFD↓ simulation for the Static ES disabled via configuration/CLI.")

    # S.1503-4 D6.3.4: Station keeping Wdelta
    apply_station_keeping = bool(
        sim_cfg.get("apply_station_keeping_wdelta", APPLY_STATION_KEEPING_WDELTA_DEFAULT)
    )
    f_stn_keep = ngso_cfg.get("_f_stn_keep", False)
    wdelta_deg_requested = ngso_cfg.get("_keep_range_deg", 0.0) if f_stn_keep else 0.0
    wdelta_deg = wdelta_deg_requested if apply_station_keeping else 0.0
    t_run_s = float(nsteps) * float(tstep)
    if wdelta_deg > 0.0:
        logger.info(
            f"  Station keeping active: Wdelta = ±{wdelta_deg:.2f}°, "
            f"T_run = {t_run_s:.1f} s ({t_run_s / 86400:.2f} days)"
        )
    elif wdelta_deg_requested > 0.0 and not apply_station_keeping:
        logger.info(
            f"  Station keeping (Wdelta ±{wdelta_deg_requested:.2f}° in the SRS) not applied "
            f"(apply_station_keeping_wdelta=False)."
        )
    sim_meta["_wdelta_deg"] = wdelta_deg
    sim_meta["_wdelta_deg_requested"] = wdelta_deg_requested

    # ─── S.1503-4 §D5.1.4.2: track-duration (sliding-window) variant ───
    # Active when sat_oper declares MIN_DURATION != 0 at the ES latitude. The
    # variant is defined in fine time steps, so the dual time step (§D4.7.1)
    # does not apply — it is disabled while this variant runs.
    _min_dur_by_lat = ngso_cfg.get("min_duration_by_lat", []) or []

    def _windows_for_es(es_lat_deg: float):
        md = _resolve_min_duration(es_lat_deg, _min_dur_by_lat)
        if md <= 0.0:
            return None
        min_orb_s = min(
            (compute_orbital_period(oe.a) for oe in constellation), default=T_orb
        )
        w = compute_track_duration_windows(
            min_duration_s=md, t_fine_s=tstep, nsteps=nsteps,
            min_orbital_period_s=min_orb_s, n_satellites=len(constellation),
        )
        # A window shorter than one fine step (N_SW ≤ 1) carries no track-duration
        # information: the §D5.1.4.2 algorithm degenerates to the standard
        # §D5.1.4.1 per-step result. Route to the standard engine (faster, keeps
        # the dual time step) instead of running ~Nstep all-fine steps for an
        # identical answer.
        if w.n_sw <= 1:
            logger.warning(
                "  MIN_DURATION (%.0fs) at ES lat %.2f° ≤ T_fine (%.3fs) → the "
                "track-duration window is below the time resolution (N_SW=1); "
                "using the standard §D5.1.4.1 path. Set MIN_DURATION ≥ 2×T_fine "
                "(≈%.0fs) to engage the sliding-window variant.",
                md, es_lat_deg, tstep, 2.0 * tstep,
            )
            return None
        return w

    windows_main = _windows_for_es(wcg_result.es_lat_deg)
    if windows_main is not None:
        _md_req = _resolve_min_duration(wcg_result.es_lat_deg, _min_dur_by_lat)
        logger.info(
            "  §D5.1.4.2 track-duration variant ACTIVE at ES lat %.2f°: "
            "MIN_DURATION requested=%.0fs, T_fine=%.3fs → N_SW=%d "
            "(effective window=%.0fs), N_MSL=%d, N_TW=%d sets, N_Repeat=%d, "
            "N_TotalSteps=%d (dual time step disabled for this variant).",
            wcg_result.es_lat_deg, _md_req, windows_main.t_fine_s, windows_main.n_sw,
            windows_main.min_duration_s, windows_main.n_msl, windows_main.n_tw,
            windows_main.n_repeat, windows_main.n_total_steps,
        )
        sim_meta["_track_duration"] = {
            "active": True,
            "min_duration_requested_s": _md_req,
            "min_duration_s": windows_main.min_duration_s,
            "min_sliding_time_s": windows_main.min_sliding_time_s,
            "n_sw": windows_main.n_sw,
            "n_msl": windows_main.n_msl,
            "n_tw": windows_main.n_tw,
            "n_repeat": windows_main.n_repeat,
            "n_total_steps": windows_main.n_total_steps,
            "t_fine_s": windows_main.t_fine_s,
        }

    # The static/reference ES may sit in a different latitude band, so resolve
    # its MIN_DURATION independently — a filing can require the variant for one
    # ES and not the other (MIN_DURATION varies only by latitude, §D5.1.4).
    windows_static = (
        _windows_for_es(static_wcg.es_lat_deg) if static_wcg is not None else None
    )
    if windows_static is not None and windows_main is None:
        logger.info(
            "  §D5.1.4.2 track-duration variant ACTIVE for the Static ES only "
            "(lat %.2f°, MIN_DURATION=%.0fs); main WCG lat has MIN_DURATION=0.",
            static_wcg.es_lat_deg, windows_static.min_duration_s,
        )

    # ITU-R S.1503-4 § D3 + Fig. 13: align ES/GSO longitudes to the temporal model § D6.3
    # (after the fine step and the Wdelta/t_run parameters of the same run).
    if not manual_enabled and bool(sim_cfg.get("s1503_figure13", True)):
        fine_f13 = float(dual_ts.fine) if dual_ts is not None else float(tstep)
        sim_meta["s1503_figure13"] = apply_s1503_figure13_wcg_longitude_adjustment(
            wcg_result=wcg_result,
            constellation=constellation,
            dominant_sat_idx=wcg_ref_sat_idx,
            fine_dt_s=fine_f13,
            raan_dot_artificial_rad_s=float(raan_dot_artificial),
            raan_dot_override_rad_s=raan_dot_override_rad_s,
            wdelta_deg=float(wdelta_deg),
            t_run_s=float(t_run_s),
        )
    else:
        sim_meta["s1503_figure13"] = {
            "applied": False,
            "reason": "manual_wcg" if manual_enabled else "disabled_by_config",
        }

    # Aggregate EPFD↓ at t=0: same D.5 rule (MAX_CO_FREQ), but **without** the D6.3.4 Wdelta offset
    # in the propagator. With Wdelta>0, at t=0 the RAAN receives −Wdelta and the constellation does
    # not coincide with Phase 1 — the single-entry WCG would end up above the aggregate (impossible
    # in linear power).
    wcg_result.epfd_aggregate_dBW = epfd_aggregate_dBW_at_instant(
        constellation=constellation,
        t_s=0.0,
        wcg=wcg_result,
        pfd_mask=pfd_mask,
        es_antenna=es_antenna,
        alpha0_deg=alpha0_deg,
        min_elevation_deg=min_elev_deg,
        pfd_bw_correction_db=bw_correction_db,
        max_co_freq_by_lat=max_co_freq_by_lat,
        strict_max_co_freq_total=strict_max_co_freq_total,
        raan_dot_artificial_rad_s=raan_dot_artificial,
        raan_dot_override_rad_s=raan_dot_override_rad_s,
        strict_exclusion_zone=strict_exclusion_zone,
        wdelta_deg=0.0,
        t_run_s=0.0,
        min_angle_at_es_deg=min_angle_at_es_deg,
        gso_min_elevation_deg=gso_min_elev_effective_deg,
    )
    logger.info("")
    logger.info(
        f"  Aggregate WCG EPFD at t=0 (S.1503 D.5, MAX_CO_FREQ; nominal geometry, without Wdelta): "
        f"{wcg_result.epfd_aggregate_dBW:.1f} dBW/m²/BWref"
    )

    # PHASE 3: Temporal EPFD simulation (main WCG + optional static ES)
    keep_full_history = bool(sim_cfg.get("keep_full_history", False))
    if keep_full_history:
        approx_bytes_per_step = 320
        approx_total_bytes = approx_bytes_per_step * int(nsteps)
        approx_gb = approx_total_bytes / (1024 ** 3)
        logger.warning(
            "keep_full_history=True: each step is retained in RAM (~%d B). "
            "For N=%d steps, estimated cost ~%.2f GiB *per ES*. "
            "The streaming accumulator (CCDF/diagnostics/decimated trace) is "
            "already preserved in <1 MB; turn off keep_full_history for simulations "
            "with large N (>~1e6).",
            approx_bytes_per_step, int(nsteps), approx_gb,
        )
    sim_t0 = time.perf_counter()
    if windows_main is not None or windows_static is not None:
        # ─── Track-duration variant (§D5.1.4.2): each ES on its own path,
        # windowed when its latitude declares MIN_DURATION>0, standard otherwise.
        if windows_main is not None:
            sim_result = run_epfd_simulation_windowed(
                constellation=constellation,
                wcg=wcg_result,
                pfd_mask=pfd_mask,
                es_antenna=es_antenna,
                alpha0_deg=alpha0_deg,
                min_elevation_deg=min_elev_deg,
                windows=windows_main,
                n_jobs=n_jobs,
                pfd_bw_correction_db=bw_correction_db,
                raan_dot_artificial_rad_s=raan_dot_artificial,
                raan_dot_override_rad_s=raan_dot_override_rad_s,
                max_co_freq_by_lat=max_co_freq_by_lat,
                strict_max_co_freq_total=strict_max_co_freq_total,
                strict_exclusion_zone=strict_exclusion_zone,
                min_angle_at_es_deg=min_angle_at_es_deg,
                wdelta_deg=wdelta_deg,
                t_run_s=t_run_s,
                gso_min_elevation_deg=gso_min_elev_effective_deg,
            )
        else:
            # Main WCG ES has MIN_DURATION=0 → standard path (only the static ES
            # needs the windowed variant here).
            sim_result = run_epfd_simulation(
                constellation=constellation, wcg=wcg_result, pfd_mask=pfd_mask,
                es_antenna=es_antenna, alpha0_deg=alpha0_deg,
                min_elevation_deg=min_elev_deg, tstep_s=tstep, nsteps=nsteps,
                dual_ts=dual_ts, n_jobs=n_jobs, pfd_bw_correction_db=bw_correction_db,
                raan_dot_artificial_rad_s=raan_dot_artificial,
                raan_dot_override_rad_s=raan_dot_override_rad_s,
                max_co_freq_by_lat=max_co_freq_by_lat,
                strict_max_co_freq_total=strict_max_co_freq_total,
                strict_exclusion_zone=strict_exclusion_zone,
                min_angle_at_es_deg=min_angle_at_es_deg, wdelta_deg=wdelta_deg,
                t_run_s=t_run_s, gso_min_elevation_deg=gso_min_elev_effective_deg,
                keep_full_history=keep_full_history,
            )
        sim_elapsed_s = time.perf_counter() - sim_t0
        if static_wcg is not None:
            logger.info("")
            logger.info(
                "  PHASE 3B: EPFD↓ simulation for the Static ES ("
                + static_es_cfg.get("name", "Static") + ")"
            )
            static_t0 = time.perf_counter()
            if windows_static is not None:
                static_sim_result = run_epfd_simulation_windowed(
                    constellation=constellation, wcg=static_wcg, pfd_mask=pfd_mask,
                    es_antenna=es_antenna, alpha0_deg=alpha0_deg,
                    min_elevation_deg=min_elev_deg, windows=windows_static,
                    n_jobs=n_jobs, pfd_bw_correction_db=bw_correction_db,
                    raan_dot_artificial_rad_s=raan_dot_artificial,
                    raan_dot_override_rad_s=raan_dot_override_rad_s,
                    max_co_freq_by_lat=max_co_freq_by_lat,
                    strict_max_co_freq_total=strict_max_co_freq_total,
                    strict_exclusion_zone=strict_exclusion_zone,
                    min_angle_at_es_deg=min_angle_at_es_deg, wdelta_deg=wdelta_deg,
                    t_run_s=t_run_s, gso_min_elevation_deg=gso_min_elev_effective_deg,
                )
            else:
                # Static ES latitude has MIN_DURATION=0 → standard path for it.
                static_sim_result = run_epfd_simulation(
                    constellation=constellation, wcg=static_wcg, pfd_mask=pfd_mask,
                    es_antenna=es_antenna, alpha0_deg=alpha0_deg,
                    min_elevation_deg=min_elev_deg, tstep_s=tstep, nsteps=nsteps,
                    dual_ts=None, n_jobs=n_jobs, pfd_bw_correction_db=bw_correction_db,
                    raan_dot_artificial_rad_s=raan_dot_artificial,
                    raan_dot_override_rad_s=raan_dot_override_rad_s,
                    max_co_freq_by_lat=max_co_freq_by_lat,
                    strict_max_co_freq_total=strict_max_co_freq_total,
                    strict_exclusion_zone=strict_exclusion_zone,
                    min_angle_at_es_deg=min_angle_at_es_deg, wdelta_deg=wdelta_deg,
                    t_run_s=t_run_s, gso_min_elevation_deg=gso_min_elev_effective_deg,
                    keep_full_history=keep_full_history,
                )
            static_sim_elapsed_s = time.perf_counter() - static_t0
    elif static_wcg is not None and dual_ts is None:
        logger.info("")
        logger.info(
            "  PHASE 3B: EPFD↓ simulation for the Static ES ("
            + static_es_cfg.get("name", "Static")
            + ") with shared orbital propagation."
        )
        multi_results = run_epfd_simulation_multi_es(
            constellation=constellation,
            wcgs=[wcg_result, static_wcg],
            pfd_mask=pfd_mask,
            es_antenna=es_antenna,
            alpha0_deg=alpha0_deg,
            min_elevation_deg=min_elev_deg,
            tstep_s=tstep,
            nsteps=nsteps,
            dual_ts=None,
            n_jobs=n_jobs,
            pfd_bw_correction_db=bw_correction_db,
            raan_dot_artificial_rad_s=raan_dot_artificial,
            raan_dot_override_rad_s=raan_dot_override_rad_s,
            max_co_freq_by_lat=max_co_freq_by_lat,
            strict_max_co_freq_total=strict_max_co_freq_total,
            strict_exclusion_zone=strict_exclusion_zone,
            min_angle_at_es_deg=min_angle_at_es_deg,
            wdelta_deg=wdelta_deg,
            t_run_s=t_run_s,
            gso_min_elevation_deg=gso_min_elev_effective_deg,
            keep_full_history=keep_full_history,
        )
        sim_result = multi_results[0]
        static_sim_result = multi_results[1]
        sim_elapsed_s = time.perf_counter() - sim_t0
        static_sim_elapsed_s = sim_elapsed_s
        static_shared_timeline = True
    else:
        sim_result = run_epfd_simulation(
            constellation=constellation,
            wcg=wcg_result,
            pfd_mask=pfd_mask,
            es_antenna=es_antenna,
            alpha0_deg=alpha0_deg,
            min_elevation_deg=min_elev_deg,
            tstep_s=tstep,
            nsteps=nsteps,
            dual_ts=dual_ts,
            n_jobs=n_jobs,
            pfd_bw_correction_db=bw_correction_db,
            raan_dot_artificial_rad_s=raan_dot_artificial,
            raan_dot_override_rad_s=raan_dot_override_rad_s,
            max_co_freq_by_lat=max_co_freq_by_lat,
            strict_max_co_freq_total=strict_max_co_freq_total,
            strict_exclusion_zone=strict_exclusion_zone,
            min_angle_at_es_deg=min_angle_at_es_deg,
            wdelta_deg=wdelta_deg,
            t_run_s=t_run_s,
            gso_min_elevation_deg=gso_min_elev_effective_deg,
            keep_full_history=keep_full_history,
        )
        sim_elapsed_s = time.perf_counter() - sim_t0

        # Executed dual time step tally (fine vs coarse) from the run.
        try:
            _acc = sim_result.acc
            sim_meta["_resolved_n_fine_steps"] = int(_acc.n_fine_steps)
            sim_meta["_resolved_n_coarse_steps"] = int(_acc.n_coarse_steps)
            sim_meta["_resolved_n_exec_steps"] = int(_acc.n_steps)
            logger.info(
                "  Dual time step executed: %d fine + %d coarse = %d iterations "
                "(%d fine-equivalent steps).",
                _acc.n_fine_steps, _acc.n_coarse_steps, _acc.n_steps, nsteps,
            )
        except Exception:  # noqa: BLE001 — telemetry only
            pass

        if static_wcg is not None:
            logger.info("")
            logger.info("  PHASE 3B: EPFD↓ simulation for the Static ES (" + static_es_cfg.get("name", "Static") + ")")
            static_t0 = time.perf_counter()
            static_sim_result = run_epfd_simulation(
                constellation=constellation,
                wcg=static_wcg,
                pfd_mask=pfd_mask,
                es_antenna=es_antenna,
                alpha0_deg=alpha0_deg,
                min_elevation_deg=min_elev_deg,
                tstep_s=tstep,
                nsteps=nsteps,
                dual_ts=dual_ts,
                n_jobs=n_jobs,
                pfd_bw_correction_db=bw_correction_db,
                raan_dot_artificial_rad_s=raan_dot_artificial,
                raan_dot_override_rad_s=raan_dot_override_rad_s,
                max_co_freq_by_lat=max_co_freq_by_lat,
                strict_max_co_freq_total=strict_max_co_freq_total,
                strict_exclusion_zone=strict_exclusion_zone,
                min_angle_at_es_deg=min_angle_at_es_deg,
                wdelta_deg=wdelta_deg,
                t_run_s=t_run_s,
                gso_min_elevation_deg=gso_min_elev_effective_deg,
                keep_full_history=keep_full_history,
            )
            static_sim_elapsed_s = time.perf_counter() - static_t0
    # ================================================================
    #  7. ARTICLE 22 COMPLIANCE
    # ================================================================
    logger.info("")
    logger.info("─" * 50)
    logger.info("  PHASE 4: Compliance verification (Article 22)")
    logger.info("─" * 50)

    limits = [(lim[0], lim[1]) for lim in art22_cfg["limits"]]
    ref_bw_khz = art22_cfg.get("reference_bandwidth_khz", 40.0)

    compliance = check_article22_compliance(
        sim_result=sim_result,
        limits=limits,
        reference_bandwidth_khz=ref_bw_khz,
    )

    if static_sim_result:
        static_compliance = check_article22_compliance(
            sim_result=static_sim_result,
            limits=limits,
            reference_bandwidth_khz=ref_bw_khz,
        )

    if compliance.compliant:
        logger.info("  ✅  COMPLIANT — System meets the Article 22 limits")
    else:
        logger.info("  ❌  NON-COMPLIANT — System exceeds the Article 22 limits")

    logger.info(f"  Worst margin: {compliance.worst_margin_dB:+.2f} dB")
    logger.info(
        f"    at limit: {compliance.worst_limit_dBW:.1f} dBW @ "
        f"{_format_percentage_value(compliance.worst_percentage)}"
    )

    logger.info("")
    logger.info("  Margins per limit:")
    for limit, pct, margin in compliance.margins_dB:
        status = "OK" if margin >= 0 else "FAIL"
        logger.info(
            f"    [{status:4s}] {limit:.1f} dBW @ {_format_percentage_value(pct)} "
            f"→ margin = {margin:+.2f} dB"
        )

    # ================================================================
    #  8. STATISTICS
    # ================================================================
    _acc = getattr(sim_result, "acc", None)
    if _acc is not None and _acc.n_steps_valid > 0:
        logger.info("")
        logger.info("  EPFD↓ statistics:")
        logger.info(f"    Maximum:  {_acc.epfd_max_db:.2f} dBW/m²/BWref")
        logger.info(f"    Mean:     {_acc.epfd_mean_db():.2f} dBW/m²/BWref")
        logger.info(f"    Minimum:  {_acc.epfd_min_valid_db:.2f} dBW/m²/BWref")
        logger.info(f"    Steps with contribution: {_acc.n_steps_valid}/{_acc.n_steps}")

    # ================================================================
    #  9. FINAL SUMMARY — WORST-CASE GEOMETRY
    # ================================================================
    if manual_enabled:
        _wcga_motor_txt = "manual (outside WCGA S.1503-4 D.3.1)"
    elif use_s1503_algo:
        _wcga_motor_txt = (
            f"S.1503 — Python, "
            f"step={float(wcg_cfg.get('s1503_step_deg', 0.1) or 0.1):g}°, "
            f"n_jobs={n_jobs}, alpha_method={alpha_method}, gso_longitude_mode={gso_lon_mode}"
        )
    else:
        _wcga_motor_txt = "uniform grid (legacy)"

    logger.info("")
    logger.info(f"  WCGA (run reproduction): {_wcga_motor_txt}")
    logger.info("╔" + "═" * 68 + "╗")
    logger.info("║{:^68}║".format("FINAL RESULT — WORST-CASE GEOMETRY (WCG)"))
    logger.info("╠" + "═" * 68 + "╣")
    logger.info("║  {:.<32} {:>32} ║".format(
        "ES — Latitude ",
        f"{wcg_result.es_lat_deg:+.4f} °"))
    logger.info("║  {:.<32} {:>32} ║".format(
        "ES — Longitude ",
        f"{wcg_result.es_lon_deg:+.4f} °"))
    logger.info("║  {:.<32} {:>32} ║".format(
        "GSO satellite — Longitude ",
        f"{wcg_result.gso_lon_deg:+.4f} °"))
    logger.info("╠" + "─" * 68 + "╣")
    logger.info("║  {:.<32} {:>32} ║".format(
        "Angle α (ES→NGSO vs GSO) ",
        f"{wcg_result.alpha_deg:.4f} °"))
    logger.info("║  {:.<32} {:>32} ║".format(
        "Off-axis angle φ (ES) ",
        f"{wcg_result.offaxis_deg:.4f} °"))
    logger.info("║  {:.<32} {:>32} ║".format(
        "NGSO elevation seen from ES ",
        f"{wcg_result.elevation_deg:.4f} °"))
    logger.info("╠" + "─" * 68 + "╣")
    logger.info("║  {:.<32} {:>32} ║".format(
        "EPFD↓ single-entry (WCG) ",
        f"{wcg_result.epfd_dBW:.2f} dBW/m²/BWref"))
    logger.info("║  {:.<32} {:>32} ║".format(
        "EPFD↓ aggregate (WCG) ",
        f"{wcg_result.epfd_aggregate_dBW:.2f} dBW/m²/BWref"))
    _acc_summ = getattr(sim_result, "acc", None)
    if _acc_summ is not None and _acc_summ.n_steps_valid > 0:
        logger.info("║  {:.<32} {:>32} ║".format(
            "EPFD↓ max. simulation ",
            f"{_acc_summ.epfd_max_db:.2f} dBW/m²/BWref"))
    logger.info("╠" + "─" * 68 + "╣")
    # Use narrow symbols (width 1) to keep the box aligned.
    # "Wide" emoji (✅/❌) count as 1 char in format() but occupy 2 visual columns.
    _conf_sym = "[ OK ]  COMPLIANT" if compliance.compliant else "[ -- ]  NON-COMPLIANT"
    logger.info("║  {:.<32} {:>32} ║".format(
        "Article 22 compliance ",
        _conf_sym))
    logger.info("║  {:.<32} {:>32} ║".format(
        "Worst margin ",
        f"{compliance.worst_margin_dB:+.2f} dB"))
    logger.info("╠" + "─" * 68 + "╣")
    logger.info("║  {:.<32} {:>32} ║".format(
        "WCG search time (wall-clock) ",
        f"{wcg_search_elapsed_s:.2f} s"))
    logger.info("║  {:.<32} {:>32} ║".format(
        "Temporal simulation time (WCG) ",
        f"{sim_elapsed_s:.2f} s"))
    if static_sim_result is not None:
        static_time_txt = f"{static_sim_elapsed_s:.2f} s"
        if static_shared_timeline:
            static_time_txt += " (shared)"
        logger.info("║  {:.<32} {:>32} ║".format(
            "Temporal simulation time (static ES) ",
            static_time_txt))
    logger.info("╚" + "═" * 68 + "╝")

    logger.info("")
    logger.info("=" * 70)
    logger.info("  SIMULATION COMPLETE")
    logger.info("=" * 70)

    return constellation, wcg_result, sim_result, compliance, static_wcg, static_sim_result, static_compliance


# =====================================================================
#  Plot generation
# =====================================================================

def plot_results(
    sim_result: EPFDSimulationResult,
    compliance: ComplianceResult,
    limits: list[tuple[float, float]],
    output_dir: str = ".",
    sat_name: str = "",
):
    """Generate simulation plots."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib not available — plots not generated.")
        return

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    title = "WCG Downlink — EPFD↓ Analysis (ITU-R S.1503-4)"
    if sat_name:
        title += f"\n{sat_name}"
    fig.suptitle(title, fontsize=14, fontweight="bold")

    # --- 1. EPFD vs Time ---
    # For large N, the full series does not exist — use the accumulator's decimated trace.
    ax1 = axes[0, 0]
    _acc_plot = getattr(sim_result, "acc", None)
    if _acc_plot is not None and _acc_plot.decim_t_s:
        times_h = np.asarray(_acc_plot.decim_t_s, dtype=float) / 3600.0
        epfd_vals = np.asarray(_acc_plot.decim_epfd_db, dtype=float)
    else:
        times_h = np.array([ts.time_s for ts in sim_result.time_steps]) / 3600.0
        epfd_vals = np.array([ts.epfd_aggregate_dBW for ts in sim_result.time_steps])
    valid = epfd_vals > -900
    if np.any(valid):
        ax1.scatter(times_h[valid], epfd_vals[valid], s=1, alpha=0.3, c="steelblue")
    ax1.set_xlabel("Time (h)")
    ax1.set_ylabel("EPFD↓ (dBW/m²/BWref)")
    ax1.set_title("EPFD↓ vs Time")
    ax1.grid(True, alpha=0.3)

    # --- 2. CDF with Art. 22 limits ---
    ax2 = axes[0, 1]
    if len(sim_result.cdf_epfd_dBW) > 0:
        ax2.plot(sim_result.cdf_epfd_dBW, sim_result.cdf_percentage,
                 "b-", linewidth=1.5, label="EPFD↓ CDF")
        for limit, pct in limits:
            ax2.plot(limit, pct, "r^", markersize=10)
            ax2.annotate(f"{limit:.0f} dBW\n@ {pct:.1f}%",
                        (limit, pct), textcoords="offset points",
                        xytext=(10, 5), fontsize=7)
    ax2.set_xlabel("EPFD↓ (dBW/m²/BWref)")
    ax2.set_ylabel("% Time exceeded")
    ax2.set_title("CDF vs Art. 22 limits")
    ax2.legend()
    ax2.grid(True, alpha=0.3)

    # --- 3. Number of satellites ---
    ax3 = axes[1, 0]
    if _acc_plot is not None and _acc_plot.decim_t_s:
        n_hor = np.asarray(_acc_plot.decim_n_hor, dtype=float)
        n_vis = np.asarray(_acc_plot.decim_n_vis, dtype=float)
        n_cont = np.asarray(_acc_plot.decim_n_cont, dtype=float)
    else:
        n_hor = np.array([ts.num_horizon_sats for ts in sim_result.time_steps])
        n_vis = np.array([ts.num_visible_sats for ts in sim_result.time_steps])
        n_cont = np.array([ts.num_contributing_sats for ts in sim_result.time_steps])
    ax3.plot(times_h, n_hor, "c-", alpha=0.45, linewidth=0.5, label="Elev ≥ 0°")
    ax3.plot(times_h, n_vis, "b-", alpha=0.5, linewidth=0.5, label="Oper. (elev ≥ ε₀)")
    ax3.plot(times_h, n_cont, "r-", alpha=0.5, linewidth=0.5, label="EPFD contrib.")
    ax3.set_xlabel("Time (h)")
    ax3.set_ylabel("Number of satellites")
    ax3.set_title("Satellites: horizon / operational / contributing")
    ax3.legend()
    ax3.grid(True, alpha=0.3)

    # --- 4. Minimum alpha ---
    ax4 = axes[1, 1]
    if _acc_plot is not None and _acc_plot.decim_t_s:
        min_alpha = np.asarray(_acc_plot.decim_min_alpha_deg, dtype=float)
    else:
        min_alpha = np.array([ts.min_alpha_deg for ts in sim_result.time_steps])
    valid_alpha = min_alpha < 180
    if np.any(valid_alpha):
        ax4.scatter(times_h[valid_alpha], min_alpha[valid_alpha],
                   s=1, alpha=0.3, c="darkorange")
    ax4.axhline(y=sim_result.wcg.alpha_deg, color="red", linestyle="--",
               alpha=0.5, label=f"α₀ WCG = {sim_result.wcg.alpha_deg:.1f}°")
    ax4.set_xlabel("Time (h)")
    ax4.set_ylabel("Minimum α (°)")
    ax4.set_title("Minimum α angle vs Time")
    ax4.legend()
    ax4.grid(True, alpha=0.3)

    plt.tight_layout()
    output_path = os.path.join(output_dir, "wcg_epfd_results.png")
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    logger.info(f"Plot saved at: {output_path}")
    plt.close(fig)


# =====================================================================
#  CLI
# =====================================================================

def main():
    parser = argparse.ArgumentParser(
        description="WCG Downlink EPFD Calculator — ITU-R S.1503-4"
    )

    # Mode 1: YAML config
    parser.add_argument(
        "--config", "-c", type=str, default=None,
        help="Path to the YAML configuration file"
    )

    # Mode 2: MDB + XML
    parser.add_argument(
        "--mdb", type=str, default=None,
        help="Path to the SRS MDB file (e.g.: 323520210 SRS.MDB)"
    )
    parser.add_argument(
        "--pfd-xml", type=str, default=None,
        help="Path to the PFD mask XML file (e.g.: Mask_id_1_PFD.xml)"
    )
    parser.add_argument(
        "--pfd-mask-mdb", type=str, default=None,
        help="Path to the MASK MDB file with the masks table (e.g.: 320520275MASK.mdb)"
    )
    parser.add_argument(
        "--mask-id", type=int, default=None,
        help="ID of the PFD mask to use (if there are multiple in the XML)"
    )
    parser.add_argument(
        "--ntc-id", type=str, default=None, metavar="ID",
        help=(
            "Notice (ntc_id) in the SRS MDB non_geo table when there are several systems. "
            "Default: first row of non_geo. Filters mask_info by the same ntc_id if the column exists."
        ),
    )
    parser.add_argument(
        "--service", type=str, default=None, choices=["FSS", "BSS"],
        help=(
            "GSO earth station service. "
            "Use BSS to apply the antenna pattern from Rec. ITU-R BO.1443-3; "
            "default: FSS."
        ),
    )

    # General options
    parser.add_argument(
        "--output", "-o", type=str, default=".",
        help="Output directory for plots and reports"
    )
    parser.add_argument(
        "--no-plot", action="store_true",
        help="Do not generate plots"
    )
    parser.add_argument(
        "--nsteps", type=int, default=0,
        help="Number of time steps (0 = automatic)"
    )
    parser.add_argument(
        "--coarse-time-step-s", "--coarse_time_step", type=float, default=None,
        help=(
            "Override the coarse simulation Δt (s). "
            "Example: --coarse-time-step-s 0.1"
        )
    )
    parser.add_argument(
        "--fine-time-step-s", "--fine_time_step", type=float, default=None,
        help=(
            "Override the fine simulation Δt (s). "
            "In S.1503 dual mode, use it to force Tfine."
        )
    )
    parser.add_argument(
        "--dual-time-step-mode", type=str, default=None,
        choices=["s1503", "alpha_threshold", "off"],
        help=(
            "Dual time step mode: "
            "'s1503' (normative gain-based rule), "
            "'alpha_threshold' (legacy), "
            "'off' (single step)."
        ),
    )
    parser.add_argument(
        "--fine-step-alpha-threshold-deg", "--fine_step_alpha_threshold",
        type=float, default=None,
        help="|α| threshold (degrees) for the legacy dual-step (mode=alpha_threshold)."
    )
    parser.add_argument(
        "--s1503-nhit", type=int, default=None,
        help="Nhit for the literal S.1503 computation of Tfine (default 16)."
    )
    parser.add_argument(
        "--s1503-phi-coarse-deg", type=float, default=None,
        help="Topocentric angle φcoarse of the S.1503 dual-step (default 1.5°)."
    )
    parser.add_argument(
        "--s1503-ncoarse", type=int, default=None,
        help="Override Ncoarse in the S.1503 dual-step."
    )
    parser.add_argument(
        "--no-s1503-literal-time-step", action="store_true",
        help="Disable the literal D4.2 formula (uses the legacy heuristic for Tfine)."
    )
    parser.add_argument(
        "--keep-full-history", action="store_true",
        help=(
            "Retain all temporal simulation steps in RAM "
            "(~300 B/step). Off by default: CCDF/diagnostics/decimated "
            "trace come from the streaming accumulator (<1 MB regardless of N). "
            "For N in the millions, do NOT enable."
        ),
    )
    parser.add_argument(
        "--earth-rotation-initial-deg", type=float, default=None,
        help=(
            "Override GMST0 (degrees) at t=0 to synchronize ECI↔ECEF. "
            "If omitted, uses the value inferred from the SRS (right_asc-long_asc), when available."
        )
    )
    parser.add_argument(
        "--alpha0", type=float, default=None,
        help=(
            "Override the exclusion zone α₀ (degrees). "
            "Uses the MDB/YAML value if omitted. "
            "Example: --alpha0 2.5"
        )
    )
    parser.set_defaults(apply_gso_min_elevation=True)
    parser.add_argument(
        "--apply-gso-min-elevation",
        dest="apply_gso_min_elevation",
        action="store_true",
        help="Force elGSO >= εGSO (Table 8). Default: enabled.",
    )
    parser.add_argument(
        "--no-apply-gso-min-elevation",
        dest="apply_gso_min_elevation",
        action="store_false",
        help=(
            "Disable the elGSO >= εGSO check (effective εGSO −90° in the WCG search; legacy)."
        ),
    )
    parser.add_argument(
        "--es-antenna-diameter", type=float, default=None,
        help=(
            "Override the ES antenna diameter (m). "
            "Uses the MDB/YAML value if omitted. "
            "Example: --es-antenna-diameter 1.2"
        )
    )
    parser.add_argument(
        "--reference-bandwidth-khz", type=float, default=None,
        help=(
            "Override the normative Art. 22 reference bandwidth "
            "(e.g.: 40 or 1000 kHz)."
        )
    )
    parser.add_argument(
        "--wcga-s1503", action="store_true", default=False,
        help=(
            "Use the analytical WCGA_Down algorithm from ITU-R S.1503-4 §D.3.1 "
            "instead of the default uniform grid. More faithful to the recommendation: "
            "iterates satellite latitudes + binary searches on the α=α₀ contours."
        )
    )
    parser.add_argument(
        "--s1503-step", type=float, default=None,
        help="Grid step for --wcga-s1503 (degrees, default 0.1 per S.1503-4)."
    )
    parser.add_argument(
        "--s1503-trail-all-points", action="store_true",
        help=(
            "In --wcga-s1503 mode, store/export all the search's tested points "
            "(may greatly increase memory and JSON/CZML size)."
        )
    )
    parser.add_argument(
        "--wcg-manual", action="store_true",
        help=(
            "Skip the WCG search and use manual geometry. Requires "
            "--wcg-manual-es-lat, --wcg-manual-es-lon and --wcg-manual-gso-lon."
        )
    )
    parser.add_argument(
        "--wcg-manual-no-align", action="store_true",
        help=(
            "In --wcg-manual mode, disable automatic constellation alignment "
            "(uses ΔM=0 and keeps the original phases)."
        )
    )
    parser.add_argument(
        "--wcg-manual-es-lat", type=float, default=None,
        help="Latitude (degrees) of the earth station (ES) in the manual geometry."
    )
    parser.add_argument(
        "--wcg-manual-es-lon", type=float, default=None,
        help="Longitude (degrees) of the earth station (ES) in the manual geometry."
    )
    parser.add_argument(
        "--wcg-manual-gso-lon", type=float, default=None,
        help="Longitude (degrees) of the GSO satellite in the manual geometry."
    )
    parser.add_argument(
        "--use-precession-mdb", action="store_true",
        help="Use precession_deg_day from the MDB instead of J2 in the propagator (fallback to declared data)",
    )
    parser.add_argument(
        "--artificial-precession", action="store_true",
        help=(
            "Enable artificial precession for non-repeating orbits (S.1503-4 D4.6.2, D6.3.5). "
            "Speeds up the nodal precession for better CCDF sampling in constellations with few planes."
        )
    )
    parser.add_argument(
        "--no-static-es", action="store_true",
        help="Do not run the additional static ES simulation (runs only the worst-geometry ES)."
    )
    parser.add_argument(
        "--max-co-freq", type=int, default=None, metavar="N",
        help=(
            "Override MAX_CO_FREQ (sat_oper / S.1503): maximum number of co-frequency standard "
            "satellites in the EPFD aggregation. Use 0 for unlimited. Default: MDB sat_oper table."
        ),
    )
    parser.add_argument(
        "--strict-max-co-freq-total", action="store_true",
        help=(
            "[Optional, non-normative] Caps the total (standard+OR) to MAX_CO_FREQ by the "
            "largest epfd_i. The default (flag absent) follows ITU-R S.1503-4 §D5.1.4.1: "
            "MAX_CO_FREQ only in the Steps 19–21 cycle; Step 22 adds OR contributors without this cap."
        ),
    )

    args = parser.parse_args()

    # ── Determine operating mode ──
    if args.mdb and (args.pfd_xml or args.pfd_mask_mdb):
        if args.pfd_xml:
            logger.info("Mode: MDB + XML (real SRS data)")
        else:
            logger.info("Mode: MDB + MASK MDB (real SRS data)")
        if args.pfd_xml and args.pfd_mask_mdb:
            parser.error("Use only one mask source: --pfd-xml or --pfd-mask-mdb")
        if args.pfd_mask_mdb and args.mask_id is None:
            parser.error("--pfd-mask-mdb mode requires --mask-id")
        config = load_from_srs(
            args.mdb,
            xml_path=args.pfd_xml,
            pfd_mask_mdb=args.pfd_mask_mdb,
            mask_id=args.mask_id,
            ntc_id=args.ntc_id,
            service=args.service or "FSS",
        )
    elif args.config:
        logger.info("Mode: YAML configuration")
        config = load_config(args.config)
    else:
        # Try to auto-detect files in the current directory
        mdb_files = [f for f in os.listdir(".") if f.endswith(".MDB") or f.endswith(".mdb")]
        xml_files = [f for f in os.listdir(".") if f.endswith(".xml") and "PFD" in f.upper()]

        if mdb_files and xml_files:
            logger.info(f"Auto-detected: MDB={mdb_files[0]}, XML={xml_files[0]}")
            config = load_from_srs(
                mdb_files[0], xml_files[0], mask_id=args.mask_id, ntc_id=args.ntc_id,
                service=args.service or "FSS",
            )
        elif os.path.exists("config.yaml"):
            config = load_config("config.yaml")
        else:
            parser.error(
                "Provide --config config.yaml OR --mdb file.MDB + (--pfd-xml mask.xml or --pfd-mask-mdb MASK.mdb)"
            )
            return

    if args.service is not None:
        config.setdefault("gso_es", {})["service"] = args.service
    else:
        config.setdefault("gso_es", {}).setdefault("service", "FSS")

    if args.max_co_freq is not None:
        if args.max_co_freq < 0:
            parser.error("--max-co-freq must be >= 0 (0 = unlimited)")
        apply_max_co_freq_override_to_non_gso(config["non_gso"], args.max_co_freq)

    # Override nsteps if specified
    if args.nsteps > 0:
        config["simulation"]["num_time_steps"] = args.nsteps
        config["simulation"]["coarse_time_step_s"] = config["simulation"].get(
            "coarse_time_step_s", 1.0
        )
    if args.coarse_time_step_s is not None:
        if args.coarse_time_step_s <= 0.0:
            parser.error("--coarse-time-step-s/--coarse_time_step must be > 0")
        old_dt = config.setdefault("simulation", {}).get("coarse_time_step_s", 1.0)
        config["simulation"]["coarse_time_step_s"] = float(args.coarse_time_step_s)
        config["simulation"]["_coarse_step_overridden"] = True
        logger.info(
            "Coarse Δt overridden via --coarse-time-step-s: "
            f"{old_dt:.4f}s → {args.coarse_time_step_s:.4f}s"
        )
    if args.fine_time_step_s is not None:
        if args.fine_time_step_s < 0.0:
            parser.error("--fine-time-step-s/--fine_time_step must be >= 0")
        old_dt_f = config.setdefault("simulation", {}).get("fine_time_step_s", 0.0)
        config["simulation"]["fine_time_step_s"] = float(args.fine_time_step_s)
        config["simulation"]["_fine_step_overridden"] = True
        logger.info(
            "Fine Δt overridden via --fine-time-step-s: "
            f"{old_dt_f:.4f}s → {args.fine_time_step_s:.4f}s"
        )
    if args.dual_time_step_mode is not None:
        config.setdefault("simulation", {})["dual_time_step_mode"] = args.dual_time_step_mode
        logger.info(f"Dual Time Step mode: {args.dual_time_step_mode}")
    if args.fine_step_alpha_threshold_deg is not None:
        if args.fine_step_alpha_threshold_deg <= 0.0:
            parser.error("--fine-step-alpha-threshold-deg/--fine_step_alpha_threshold must be > 0")
        config.setdefault("simulation", {})["fine_step_alpha_threshold_deg"] = float(
            args.fine_step_alpha_threshold_deg
        )
    if args.s1503_nhit is not None:
        if args.s1503_nhit <= 0:
            parser.error("--s1503-nhit must be > 0")
        config.setdefault("simulation", {})["s1503_nhit"] = int(args.s1503_nhit)
    if args.s1503_phi_coarse_deg is not None:
        if args.s1503_phi_coarse_deg <= 0.0:
            parser.error("--s1503-phi-coarse-deg must be > 0")
        config.setdefault("simulation", {})["s1503_phi_coarse_deg"] = float(args.s1503_phi_coarse_deg)
    if args.s1503_ncoarse is not None:
        if args.s1503_ncoarse <= 0:
            parser.error("--s1503-ncoarse must be > 0")
        config.setdefault("simulation", {})["s1503_ncoarse"] = int(args.s1503_ncoarse)
    if args.no_s1503_literal_time_step:
        config.setdefault("simulation", {})["s1503_literal_time_step"] = False
        logger.info("Literal D4.2 computation disabled via --no-s1503-literal-time-step")
    if getattr(args, "keep_full_history", False):
        config.setdefault("simulation", {})["keep_full_history"] = True
        logger.warning(
            "keep_full_history active via --keep-full-history: each step "
            "retained in RAM. Use only for small N (debug)."
        )
    if args.earth_rotation_initial_deg is not None:
        config.setdefault("simulation", {})["earth_rotation_initial_deg"] = float(
            args.earth_rotation_initial_deg
        )
        logger.info(
            "GMST0 overridden via --earth-rotation-initial-deg: "
            f"{args.earth_rotation_initial_deg:.4f}°"
        )

    # WCGA algorithm override (always; overrides any config.yaml that might force WCGA)
    config.setdefault("wcg_search", {})["use_s1503_algo"] = bool(args.wcga_s1503)
    if args.wcga_s1503:
        logger.info("WCGA S.1503-4 algorithm enabled via --wcga-s1503")
    else:
        logger.info("Legacy WCG mode (θ/φ grid): use_s1503_algo=False")
    if args.s1503_step is not None:
        config.setdefault("wcg_search", {})["s1503_step_deg"] = args.s1503_step
        logger.info(f"WCGA S.1503-4 step: {args.s1503_step}°")
    if args.s1503_trail_all_points:
        config.setdefault("wcg_search", {})["s1503_trail_all_points"] = True
        logger.info("WCGA S.1503: full trail (all points) enabled via CLI.")
    if args.wcg_manual:
        required = {
            "wcg-manual-es-lat": args.wcg_manual_es_lat,
            "wcg-manual-es-lon": args.wcg_manual_es_lon,
            "wcg-manual-gso-lon": args.wcg_manual_gso_lon,
        }
        missing = [k for k, v in required.items() if v is None]
        if missing:
            parser.error("--wcg-manual mode requires: " + ", ".join("--" + m for m in missing))
        config.setdefault("wcg_search", {})["manual_wcg"] = {
            "enabled": True,
            "es_lat_deg": float(args.wcg_manual_es_lat),
            "es_lon_deg": float(args.wcg_manual_es_lon),
            "gso_lon_deg": float(args.wcg_manual_gso_lon),
            "align_constellation": not bool(args.wcg_manual_no_align),
        }
        logger.info(
            "Manual WCG mode enabled via CLI "
            f"(ES={args.wcg_manual_es_lat:+.3f}°, {args.wcg_manual_es_lon:+.3f}°; "
            f"GSO lon={args.wcg_manual_gso_lon:+.3f}°; "
            f"align={'ON' if not args.wcg_manual_no_align else 'OFF'})"
        )
        if args.wcga_s1503:
            logger.info("Warning: --wcga-s1503 ignored because --wcg-manual is active.")

    # Artificial precession override if specified via CLI
    if args.artificial_precession:
        config.setdefault("simulation", {})["artificial_precession"] = True
        logger.info("Artificial precession enabled via --artificial-precession")
    if args.use_precession_mdb:
        config.setdefault("simulation", {})["use_precession_mdb"] = True
        logger.info("MDB precession enabled via --use-precession-mdb")
    if args.no_static_es:
        config.setdefault("simulation", {})["run_static_es"] = False
        logger.info("Additional static ES simulation disabled via --no-static-es")

    # Override alpha0 if specified via CLI
    if args.alpha0 is not None:
        old = config["non_gso"].get("alpha0_deg", 0.0)
        config["non_gso"]["alpha0_deg"] = args.alpha0
        logger.info(f"α₀ overridden via --alpha0: {old:.2f}° → {args.alpha0:.2f}°")
    config.setdefault("non_gso", {})["apply_gso_min_elevation"] = bool(args.apply_gso_min_elevation)
    config.setdefault("non_gso", {})["strict_max_co_freq_total"] = bool(args.strict_max_co_freq_total)
    logger.info(
        "εGSO check on the downlink: "
        + (
            "disabled via --no-apply-gso-min-elevation"
            if not args.apply_gso_min_elevation
            else "enabled (S.1503 default)"
        )
    )
    logger.info(
        "MAX_CO_FREQ: "
        + (
            "joint cap extension via --strict-max-co-freq-total (non-normative)"
            if args.strict_max_co_freq_total
            else "S.1503 default Steps 19–22 (cap only in the standard cycle)"
        )
    )
    if args.es_antenna_diameter is not None:
        if args.es_antenna_diameter <= 0.0:
            parser.error("--es-antenna-diameter must be > 0")
        old_d = config.setdefault("gso_es", {}).get("antenna_diameter_m", 1.2)
        config["gso_es"]["antenna_diameter_m"] = float(args.es_antenna_diameter)
        logger.info(
            "ES antenna diameter overridden via --es-antenna-diameter: "
            f"{old_d:.3f} m → {args.es_antenna_diameter:.3f} m"
        )
    if args.reference_bandwidth_khz is not None:
        if args.reference_bandwidth_khz <= 0.0:
            parser.error("--reference-bandwidth-khz must be > 0")
        old_bw = float(config.setdefault("article22_limits", {}).get("reference_bandwidth_khz", 40.0) or 40.0)
        config["article22_limits"]["reference_bandwidth_khz"] = float(args.reference_bandwidth_khz)
        logger.info(
            "Art. 22 ref BW overridden via --reference-bandwidth-khz: "
            f"{old_bw:.0f} kHz → {float(args.reference_bandwidth_khz):.0f} kHz"
        )

    apply_article22_limits_to_config(config)

    # ── Run ──
    try:
        (constellation, wcg_result, sim_result, compliance,
         static_wcg, static_sim_result, static_compliance) = run_wcg_downlink(config)
    except (NoValidGeometry, InvalidManualGeometry) as exc:
        logger.error(str(exc))
        sys.exit(1)

    if wcg_result is None:
        sys.exit(1)

    # ── Plots ──
    if not args.no_plot and sim_result is not None:
        limits = [(lim[0], lim[1]) for lim in config["article22_limits"]["limits"]]
        sat_name = config["non_gso"].get("_srs_system", {})
        if hasattr(sat_name, 'sat_name'):
            sat_name = sat_name.sat_name
        elif isinstance(sat_name, dict):
            sat_name = sat_name.get("sat_name", "")
        else:
            sat_name = ""

        # Try to get it from the SRS system
        srs_sys = config.get("_srs_system", None)
        if srs_sys and hasattr(srs_sys, "sat_name"):
            sat_name = srs_sys.sat_name

        plot_results(sim_result, compliance, limits, args.output, sat_name)

    if compliance is not None and not compliance.compliant:
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
