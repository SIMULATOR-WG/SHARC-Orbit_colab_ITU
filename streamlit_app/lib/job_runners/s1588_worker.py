"""S.1588 multi-system worker (Resolution 76 — Studies 1, 2, 3).

Usage:
    python -m streamlit_app.lib.job_runners.s1588_worker <params.json>

params.json fields:
    result_path           — directory to write artifacts into
    method                — "method_1" | "method_2" | "method_3"
    filings               — list of {srs_path, mask_path?, mask_id?, ntc_id?}
    num_time_steps        — omit/0 = auto (each filing's own S.1503-4 §D4 N)
    time_step_s           — omit = auto (each filing's own §D4.2 fine step)
    min_elevation_deg     — omit = keep each filing's SRS grp.elev_min (ε₀)
    service               — default "FSS"
    es_antenna_diameter_m — optional
    # method_2 only:
    grid_step_deg         — default 30.0 (coarse for runtime)
    gso_pointing_step_deg — default 30.0
    # method_3 only:
    geometry_es_lat       — optional manual ES lat
    geometry_es_lon       — optional manual ES lon
    geometry_gso_lon      — optional manual GSO lon

Outputs:
    sim_data.json         — full aggregate result (CCDF, percentiles, per-system),
                            hardware, timing (start/end/duration)
    summary.json          — quick metrics, hardware, timing (start/end/duration)
"""
from __future__ import annotations

import copy
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve()
REPO_ROOT = HERE.parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from streamlit_app.lib import (  # noqa: E402
    cluster, epfd_cluster, hwinfo, plan, wcga_cluster,
)


def _emit(line: str) -> None:
    print(line, flush=True)


def _emit_progress(pct: float) -> None:
    print(f"PROGRESS:{max(0.0, min(100.0, pct)):.2f}", flush=True)


def _percentiles(bins_desc, pct_asc,
                  truncation_floor_pct: float | None = None) -> dict[str, float | None]:
    """Normative percentiles from a CCDF (bins descending, pct ascending).

    When tail truncation is active, a target below the truncation floor is
    not resolvable from the truncated curve — ``percentile_from_ccdf`` would
    return the truncated peak, an anti-conservative value mislabeled as the
    target percentile. Per the UI contract those entries are emitted as
    ``None`` (JSON null); the Results page renders them as
    "below truncation floor".
    """
    from src.s1588_studies import NORMATIVE_PERCENTAGES, extract_percentiles  # type: ignore[import]
    pct_for_extract = [float(p) / 100.0 for p in pct_asc]
    out = extract_percentiles(
        list(reversed(list(bins_desc))),
        list(reversed(pct_for_extract)),
        NORMATIVE_PERCENTAGES,
    )
    floor = float(truncation_floor_pct) if truncation_floor_pct is not None else None
    return {
        f"{p}%": (None if floor is not None and float(p) < floor else float(v))
        for p, v in out.items()
    }


def _ccdf_envelope(
    curves: "list[tuple[list[float], list[float]]]",
    pct_lo: float = 0.0,
) -> "tuple[list[float], list[float]]":
    """Worst-per-percentage CCDF envelope (linear power scale).

    The envelope is evaluated at EVERY percentage breakpoint observed in any
    input curve (union of all ``ccdf_pct`` values), so no real point is lost —
    a fixed resampled axis (e.g. ``linspace(0.0001, 100, 1024)``) has ~0.1%
    spacing and collapses the whole <0.1% tail (the region the Article 22
    limits actually probe) to a single sample. The tail is kept down to the
    smallest observed breakpoint (100·1/N of the longest run) — no arbitrary
    lower cut. Curves are (bins_db, pct) pairs; breakpoints at or below
    ``pct_lo`` (the truncation floor, when active) are dropped, as are
    zero-exceedance entries. Returns (bins_db descending, pct) like the
    per-point curves.
    """
    import numpy as np

    if not curves:
        return [], []
    common_pct = np.unique(np.concatenate([
        np.asarray(pct, dtype=float) for _bins, pct in curves
    ]))
    common_pct = common_pct[
        (common_pct >= pct_lo) & (common_pct > 0.0) & (common_pct <= 100.0)
    ]
    if common_pct.size == 0:
        return [], []
    env = np.full(common_pct.shape, 1e-30)
    for bins, pct in curves:
        bins_arr = np.asarray(bins, dtype=float)
        pct_arr = np.asarray(pct, dtype=float)
        order = np.argsort(pct_arr)
        bins_lin = np.power(10.0, bins_arr / 10.0)[order]
        pct_asc = pct_arr[order]
        interp = np.interp(common_pct, pct_asc, bins_lin,
                           left=bins_lin[0], right=bins_lin[-1])
        env = np.maximum(env, interp)
    env_db = 10.0 * np.log10(np.maximum(env, 1e-30))
    order_desc = np.argsort(-env_db)
    return ([float(x) for x in env_db[order_desc]],
            [float(x) for x in common_pct[order_desc]])


def _resolve_filing_path(filing: dict[str, Any], abs_key: str, rel_key: str) -> str | None:
    """Resolve a filing's file path, trying (in order):

    1. The absolute path persisted on the head — only valid when the
       task runs on the same machine that uploaded the file.
    2. The relative path under Ray's ``working_dir`` (current CWD on
       remote workers — Ray extracts ``runtime_env.working_dir`` there).
    3. The relative path against this host's ``UPLOADS_DIR`` (covers
       the manual ``rsync`` deployment).

    Returns ``None`` when nothing resolves (caller should error
    descriptively); otherwise an absolute path string.
    """
    abs_p = filing.get(abs_key)
    if abs_p and Path(abs_p).exists():
        return abs_p
    rel = filing.get(rel_key)
    if rel:
        # The launcher emits POSIX relpaths, but payloads written by older
        # Windows heads carry "\" — normalize so a Linux worker resolves
        # them too (forward slashes are fine on Windows).
        rel = str(rel).replace("\\", "/")
        # Ray working_dir extracts to the task's CWD
        cwd_candidate = Path(rel)
        if cwd_candidate.exists():
            return str(cwd_candidate.resolve())
        # Pre-rsync layout under local UPLOADS_DIR
        from streamlit_app.lib import UPLOADS_DIR  # noqa: PLC0415
        local_candidate = UPLOADS_DIR / rel
        if local_candidate.exists():
            return str(local_candidate)
    return abs_p  # Last resort — engine will raise a clear FileNotFound.


def _stat_sig(path: str | None) -> tuple[int, int] | None:
    """(st_mtime_ns, st_size) — file-freshness component of the cfg cache key."""
    if not path:
        return None
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _load_cfg(filing: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    """Load (cached) a config dict for one filing; returns a private deep copy.

    Methods 2/5 (and the cross product of 4) dispatch one task per
    (geometry × filing) — without a cache every task re-reads and re-parses
    the whole MDB. The module-level memo dict below is keyed by the
    filing/common payloads plus the SRS/mask file signatures (mtime + size),
    and pays off on Ray too since workers reuse processes. The deep copy
    keeps the cached instance pristine while callers mutate their own cfg.
    """
    try:
        filing_json = json.dumps(filing, sort_keys=True)
        common_json = json.dumps(common, sort_keys=True)
    except (TypeError, ValueError):
        return _load_cfg_impl(filing, common)  # unhashable payload — no cache
    srs_sig = _stat_sig(_resolve_filing_path(filing, "srs_path", "srs_relpath"))
    mask_sig = _stat_sig(_resolve_filing_path(filing, "mask_path", "mask_relpath"))
    key = (filing_json, common_json, srs_sig, mask_sig)
    cfg = _CFG_CACHE.get(key)
    if cfg is None:
        cfg = _load_cfg_impl(filing, common)
        if len(_CFG_CACHE) >= _CFG_CACHE_MAX:  # simple FIFO bound
            _CFG_CACHE.pop(next(iter(_CFG_CACHE)))
        _CFG_CACHE[key] = cfg
    try:
        return copy.deepcopy(cfg)
    except Exception:  # noqa: BLE001 — uncopyable cfg: fall back to a fresh parse
        return _load_cfg_impl(filing, common)


# Plain dict memo instead of functools.lru_cache: this module runs as
# __main__ (python -m), so Ray/cloudpickle ships its task functions BY VALUE
# to remote workers. An lru_cache wrapper pickles by reference
# ("__main__._load_cfg_cached"), which the Ray worker's __main__
# (default_worker.py) cannot resolve — a dict + plain function pickle by
# value and each worker process simply starts with an empty cache.
_CFG_CACHE: dict[tuple, dict[str, Any]] = {}
_CFG_CACHE_MAX = 32


def _load_cfg_impl(filing: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    """Load and prepare a config dict for one filing (uncached)."""
    from src.main import load_from_srs  # type: ignore[import]
    from src.article22_tables import apply_article22_limits_to_config  # type: ignore[import]
    from src.resolution76_tables import apply_resolution76_limits_to_config  # type: ignore[import]

    srs_path = _resolve_filing_path(filing, "srs_path", "srs_relpath")
    mask_path = _resolve_filing_path(filing, "mask_path", "mask_relpath")
    mask_id = filing.get("mask_id")
    ntc_id = filing.get("ntc_id")
    service = common.get("service", "FSS")
    # Shared frequency run (Art. 22 scenario applied to every filing) — steers
    # each filing's default mask / group resolution to the simulated sub-band.
    try:
        sim_freq_ghz = (float(common["simulation_frequency_ghz"])
                        if common.get("simulation_frequency_ghz") is not None
                        else None)
    except (TypeError, ValueError):
        sim_freq_ghz = None

    if mask_path and str(mask_path).lower().endswith(".xml"):
        cfg = load_from_srs(srs_path, xml_path=str(mask_path), mask_id=mask_id,
                            ntc_id=ntc_id, service=service,
                            simulation_frequency_ghz=sim_freq_ghz)
    elif mask_path:
        # mask_id None → resolved by load_from_srs via mask_lnk1 precedence
        # (emi_rcp=E → grp_id → seq_no), restricted to groups covering the
        # shared frequency run when one is set; first declared PFD as last
        # resort.
        cfg = load_from_srs(srs_path, pfd_mask_mdb=str(mask_path), mask_id=mask_id,
                            ntc_id=ntc_id, service=service,
                            simulation_frequency_ghz=sim_freq_ghz)
    else:
        cfg = load_from_srs(srs_path, xml_path=None, mask_id=mask_id,
                            ntc_id=ntc_id, service=service,
                            simulation_frequency_ghz=sim_freq_ghz)

    # The S.1503-4 §D5.1.4.2 track-duration variant is implemented only for the
    # single-system EPFD↓ engine (S.1503 single-entry). Aggregate/S.1588 studies
    # do not model sliding windows; fail loudly rather than run the wrong path.
    _mdur = cfg.get("non_gso", {}).get("min_duration_by_lat", []) or []
    if any(abs(float(d)) > 1e-9 for _f, _t, d in _mdur):
        raise NotImplementedError(
            "This filing declares MIN_DURATION != 0 (S.1503-4 §D5.1.4.2 "
            "track-duration variant). That variant is supported only in the "
            "single-entry EPFD↓ run, not in aggregate/S.1588 studies. "
            "Run it via Single-entry, or use a filing with MIN_DURATION=0."
        )

    sim = cfg.setdefault("simulation", {})
    sim["run_static_es"] = bool(common.get("run_static_es", False))
    # 0 = auto: each filing gets its own S.1503-4 §D4 time base, exactly as on
    # an independent single-entry run. Only an explicit value pins a shared N.
    sim["num_time_steps"] = int(common.get("num_time_steps") or 0)
    if "time_step_s" in common:
        sim["coarse_time_step_s"] = float(common["time_step_s"])
        sim["_coarse_step_overridden"] = True
    if common.get("fine_time_step_s") is not None:
        sim["fine_time_step_s"] = float(common["fine_time_step_s"])
        sim["_fine_step_overridden"] = True
    if common.get("dual_time_step_mode"):
        sim["dual_time_step_mode"] = (
            common["dual_time_step_mode"] if common["dual_time_step_mode"] != "on" else "s1503"
        )
    if common.get("itu_software"):
        sim["itu_software"] = str(common["itu_software"]).lower()
    # ε₀ override: only when the user supplied a value. Absent/None = keep
    # each filing's ε₀ (SRS grp.elev_min), same as Single-entry.
    if common.get("min_elevation_deg") is not None:
        cfg.setdefault("non_gso", {})["min_elevation_deg"] = float(common["min_elevation_deg"])
    if common.get("es_antenna_diameter_m"):
        cfg.setdefault("gso_es", {})["antenna_diameter_m"] = float(common["es_antenna_diameter_m"])

    wcg_cfg = cfg.setdefault("wcg_search", {})
    # Default to S.1503-4 §D.3.1 normative algorithm.
    wcg_cfg["use_s1503_algo"] = bool(common.get("wcga_s1503", True))
    if common.get("wcga_no_mask_symmetry", True):
        wcg_cfg["s1503_symmetric_mask"] = False
    else:
        wcg_cfg["s1503_symmetric_mask"] = True
    if common.get("s1503_step_deg") is not None:
        wcg_cfg["s1503_step_deg"] = float(common["s1503_step_deg"])
        wcg_cfg["phi_step_deg"] = float(common["s1503_step_deg"])
    if common.get("gso_longitude_mode"):
        # Engine reads gso_longitude_mode from cfg["simulation"]
        # (src/main.py run_wcg_downlink); mirror into wcg_search for tooling.
        sim["gso_longitude_mode"] = common["gso_longitude_mode"]
        wcg_cfg["gso_longitude_mode"] = common["gso_longitude_mode"]
    if common.get("alpha_method"):
        # Engine reads alpha_method from cfg["simulation"]; mirror here.
        sim["alpha_method"] = common["alpha_method"]
        wcg_cfg["alpha_method"] = common["alpha_method"]

    # Orbital dynamics (S.1503-4 §D6.3), per filing. Only set keys the user
    # decided, so the engine's SRS-derived auto-detection still applies.
    if "artificial_precession" in common:  # absent = auto-detect
        sim["artificial_precession"] = bool(common["artificial_precession"])
    if common.get("use_precession_mdb"):
        sim["use_precession_mdb"] = True
    if common.get("apply_station_keeping"):
        sim["apply_station_keeping_wdelta"] = True
    # Co-frequency emitters only (SRS grp ⋈ mask_lnk1). Default ON — satellites
    # whose transmitting group does not cover the simulation frequency do not
    # contribute to EPFD↓. Explicit False keeps the legacy full constellation.
    if "restrict_emitters_to_sim_band" in common:
        sim["restrict_emitters_to_sim_band"] = bool(common["restrict_emitters_to_sim_band"])
    else:
        sim["restrict_emitters_to_sim_band"] = True
    # Table 8 εGSO gate. The Launcher toggle defaults to disabled (same as
    # Single-entry): drop the GSO-min-elevation (εGSO) gate so the WCGA/EPFD
    # do not exclude high-latitude victims (matches the ITU BR / S.1503-2
    # reference). Honored by run_wcg_downlink (method_1) and the grid kernels
    # (build_downlink_engine_inputs → gso_min_elev_effective = -90°).
    if common.get("disable_gso_min_elevation"):
        cfg.setdefault("non_gso", {})["apply_gso_min_elevation"] = False

    # Article 22 downlink scenario (UI tree leaf): one shared limit config for
    # the aggregate — pin reference bandwidth + simulation frequency run so
    # apply_article22_limits_to_config resolves the exact normative table row.
    # Service + ES-antenna are applied above. mask_id is NOT pinned here: each
    # filing keeps its own PFD mask. Absent → engine auto-resolves per filing.
    if common.get("reference_bandwidth_khz") is not None:
        cfg.setdefault("article22_limits", {})["reference_bandwidth_khz"] = \
            float(common["reference_bandwidth_khz"])
    if common.get("simulation_frequency_ghz") is not None:
        cfg.setdefault("pfd_mask", {})["simulation_frequency_ghz"] = \
            float(common["simulation_frequency_ghz"])

    apply_article22_limits_to_config(cfg)
    apply_resolution76_limits_to_config(cfg)
    return cfg


def _system_label(cfg: dict[str, Any], index: int) -> str:
    """Human label for one filing in a multi-system run (CSV/JSON display).

    The worker deliberately stays storage-agnostic (Ray remote workers have
    no access to the head's SQLite DB), so this reads the SRS-parsed system
    object already on ``cfg`` (set by ``load_from_srs``) instead of querying
    ``storage.get_system``.
    """
    srs_sys = cfg.get("_srs_system")
    name = getattr(srs_sys, "sat_name", None) if srs_sys is not None else None
    ntc = getattr(srs_sys, "ntc_id", None) if srs_sys is not None else None
    if name and ntc:
        return f"{name} (ntc {ntc})"
    if name:
        return str(name)
    return f"system_{index}"


def _bw_correction_db(cfg: dict[str, Any], mask: Any) -> float:
    """PFD→EPFD reference-bandwidth correction: 10·log10(RefBW_table / RefBW_mask).

    ``build_downlink_engine_inputs`` (method_1's engine assembly) always
    computes this from ``cfg["article22_limits"]["reference_bandwidth_khz"]``
    vs. the mask's own ``refbw_khz`` and feeds it to the engine — the fixed-
    geometry paths never did, silently assuming the two match (0 dB
    correction). Harmless when the mask already declares the same RefBW as
    the Article 22 table row (e.g. both 40 kHz, the common case), wrong
    whenever they differ.
    """
    import math as _math
    art22_cfg = cfg.get("article22_limits") or {}
    limit_bw_khz = float(art22_cfg.get("reference_bandwidth_khz", 40.0) or 40.0)
    mask_bw_khz = float(getattr(mask, "refbw_khz", 0.0) or 0.0)
    if mask_bw_khz <= 0.0:
        return 0.0
    return 10.0 * _math.log10(limit_bw_khz / mask_bw_khz)


def _apply_orbit_dynamics(
    cfg: dict[str, Any], constellation: list, t_run_s: float,
) -> list:
    """Fold this filing's S.1503-4 §D6.3 orbit case (artificial precession /
    admin-supplied precession / station-keeping wobble) into a COPY of its
    own satellites' orbital elements — instead of the ``raan_dot_artificial
    _rad_s`` / ``raan_dot_override_rad_s`` / ``wdelta_deg`` scalars
    ``run_epfd_simulation`` accepts, which apply ONE shared value to an
    entire propagation batch.

    ``_run_at_geometry`` (method_2/4, single filing per call) passes those
    scalars straight through and that's correct there. method_3 fuses
    satellites from MULTIPLE filings into one ``combined`` list and
    propagates them in a single batch call — passing a scalar there would
    force every system to share ONE filing's precession, silently dropping
    the others' (or, worse, misapplying one system's rate to another's
    satellites). Each Case is a constant (Case 1/3) or affine-in-t (Case 2)
    correction to (raan, raan_dot, omega_dot, M_dot), so it folds cleanly
    into per-satellite elements computed once, upfront — the batch
    propagator then just reads each satellite's own ``raan_dot`` as usual,
    no per-system branching needed inside the hot loop.

    ``t_run_s``: the run's ACTUAL total duration (the joint T_run for
    method_3, shared by every satellite regardless of system) — Case 1/2
    rates are defined relative to it (§D6.3.5/§D6.3.4: sweep exactly one
    revolution / ±Wdelta over the run that's actually happening).

    Mirrors ``_run_at_geometry``'s per-filing Case resolution (same cfg
    fields, same D6.3.6 Case-3-over-Case-1 precedence).
    """
    import copy
    import math as _math

    sim = cfg.get("simulation") or {}
    ngso = cfg.get("non_gso") or {}

    raan_dot_override = None
    if sim.get("use_precession_mdb"):
        pday = float(ngso.get("_precession_deg_day", 0.0) or 0.0)
        if pday:
            raan_dot_override = (pday * _math.pi / 180.0) / 86400.0

    artificial_precession = bool(sim.get("artificial_precession"))
    if raan_dot_override is not None and artificial_precession:
        # §D6.3.6: the three orbit cases are mutually exclusive — admin
        # precession (Case 3) takes precedence over artificial (Case 1).
        artificial_precession = False
    raan_dot_artificial = (
        (2.0 * _math.pi) / t_run_s if (artificial_precession and t_run_s > 0) else 0.0
    )

    wdelta_deg = 0.0
    if sim.get("apply_station_keeping_wdelta") and ngso.get("_f_stn_keep"):
        wdelta_deg = float(ngso.get("_keep_range_deg", 0.0) or 0.0)
    wdelta_rad = _math.radians(wdelta_deg) if wdelta_deg else 0.0

    if raan_dot_override is None and raan_dot_artificial == 0.0 and wdelta_rad == 0.0:
        return constellation  # nothing to fold in — plain J2 propagation

    out = []
    for oe in constellation:
        oe2 = copy.copy(oe)
        if raan_dot_override is not None:
            # Case 3 (§D6.3.6 eqs 51-53): ω held constant, M at point-mass n0.
            oe2.raan_dot = raan_dot_override
            oe2.omega_dot = 0.0
            oe2.M_dot = oe.n
        else:
            oe2.raan_dot = oe.raan_dot + raan_dot_artificial
        if wdelta_rad != 0.0 and t_run_s > 0.0:
            # §D6.3.4: raan(t) = raan0 + raan_dot*t + Wdelta*(2t/Trun - 1)
            #                   = (raan0 - Wdelta) + (raan_dot + 2Wdelta/Trun)*t
            # — affine in t, so it folds into raan0/raan_dot exactly (no
            # need for propagate_and_to_ecef_batch's time-varying term).
            oe2.raan = oe.raan - wdelta_rad
            oe2.raan_dot = oe2.raan_dot + (2.0 * wdelta_rad / t_run_s)
        out.append(oe2)
    return out


def _s1503_normative_caps(cfg: dict[str, Any]) -> dict[str, Any]:
    """MAX_CO_FREQ (Steps 19-22) + OR/exclusion-zone gates for one filing.

    ``run_wcg_downlink`` (method_1's engine, via ``build_downlink_engine_inputs``)
    reads these ``non_gso`` fields and passes them to the engine on every call.
    The fixed-geometry paths (method_2/3/4: ``_run_at_geometry``,
    ``_run_method_3``) build engine inputs by hand and previously omitted them
    entirely — ``max_co_freq_by_lat=None`` decays to *unlimited* co-frequency
    satellites (``_resolve_max_co_freq`` in epfd_calculator.py), so those
    methods counted every visible satellite where method_1 caps at the
    filing's declared MAX_CO_FREQ (SRS ``sat_oper`` table). That alone can
    move the aggregate peak several dB and is not a geometry effect.
    """
    from src.main import _s1503_table8_gso_defaults  # type: ignore[import]
    ngso = cfg["non_gso"]
    freq_ghz = float(ngso.get("frequency_ghz", 0.0) or 0.0)
    gso_default_deg, _theta = _s1503_table8_gso_defaults(freq_ghz)
    gso_min_elev_deg = float(ngso.get("gso_min_elevation_deg", gso_default_deg))
    apply_gso_min_elev = bool(ngso.get("apply_gso_min_elevation", True))
    return {
        "max_co_freq_by_lat": ngso.get("max_co_freq_by_lat") or [],
        "strict_max_co_freq_total": bool(ngso.get("strict_max_co_freq_total", False)),
        "strict_exclusion_zone": bool(ngso.get("strict_exclusion_zone", False)),
        "min_angle_at_es_deg": float(ngso.get("min_angle_at_es_deg", 0.0) or 0.0),
        "gso_min_elevation_deg": gso_min_elev_deg if apply_gso_min_elev else -90.0,
    }


def _build_mask(cfg: dict[str, Any]):
    """Build a PFDMask object from the cfg["pfd_mask"] block."""
    from src.pfd_mask import load_pfd_mask, load_pfd_mask_from_xml_content  # type: ignore[import]
    pfd = cfg["pfd_mask"]
    if pfd.get("source") == "mask_mdb":
        from src.srs_reader import read_pfd_mask_xml_from_mdb  # type: ignore[import]
        srs_sys = cfg.get("_srs_system")
        xml = read_pfd_mask_xml_from_mdb(
            pfd["mdb_file"], int(pfd["mask_id"]),
            ntc_id=getattr(srs_sys, "ntc_id", None) if srs_sys else None,
        )
        return load_pfd_mask_from_xml_content(xml, mask_id=pfd.get("mask_id"))
    return load_pfd_mask(pfd["file"], mask_type=pfd.get("type", "alpha"),
                        mask_id=pfd.get("mask_id"))


def _build_mask_for_sats(cfg: dict[str, Any], mask_id_per_sat: list[int] | None):
    """Per-satellite PFD mask routing (S.1503-4 mask_lnk1), same rule as method_1.

    ``build_downlink_engine_inputs`` (method_1's engine assembly) routes each
    satellite through its OWN mask whenever the filing's mask_lnk1 declares
    more than one PFD mask id (e.g. one mask per orbital shell — USASAT-NGSO-3X
    carries 9 per band). The fixed-geometry paths (method_2/3/4/5) used to
    drop the ``mask_id_per_sat`` list and radiate every satellite with the
    single ``cfg["pfd_mask"]["mask_id"]`` — one shell's mask (mask_lnk1
    precedence, or the user's pick) applied to the whole constellation, which
    skews multi-shell filings by whole dB versus method_1 at the same geometry.

    Single-mask filings (XML file source, or MDB with ≤1 distinct id) fall
    back to :func:`_build_mask` — bit-identical to the previous behaviour.
    """
    from src.pfd_mask import PFDMaskMulti  # type: ignore[import]
    from src.srs_reader import load_pfd_masks_for_ids  # type: ignore[import]
    pfd = cfg.get("pfd_mask") or {}
    unique_ids = sorted({int(m) for m in (mask_id_per_sat or []) if int(m) != -1})
    if pfd.get("source") != "mask_mdb" or len(unique_ids) <= 1:
        return _build_mask(cfg)
    srs_sys = cfg.get("_srs_system")
    masks_by_id = load_pfd_masks_for_ids(
        pfd["mdb_file"], unique_ids,
        ntc_id=getattr(srs_sys, "ntc_id", None) if srs_sys else None,
    )
    return PFDMaskMulti(masks_by_id, mask_id_per_sat)


def _build_antenna(cfg: dict[str, Any]):
    from src.antenna import create_gso_es_antenna  # type: ignore[import]
    gso_es = cfg["gso_es"]
    return create_gso_es_antenna(
        float(gso_es["antenna_diameter_m"]),
        float(cfg["non_gso"]["frequency_ghz"]),
        float(gso_es.get("antenna_efficiency", 0.99)),
        service=str(gso_es.get("service", "FSS")).upper(),
    )


def _dual_time_step_block(
    sim_cfg: dict[str, Any],
    acc: Any | None = None,
    *,
    fine_step_s: float | None = None,
    coarse_step_s: float | None = None,
    ncoarse: int | None = None,
    num_time_steps: int | None = None,
) -> dict[str, Any]:
    """Same contract as the Single-entry ``sim_data["dual_time_step"]`` block.

    Prefer values already resolved on ``sim_cfg`` by ``run_wcg_downlink``;
    optional kwargs fill gaps for fixed-geometry paths that never touch the
    dual-step machinery.
    """
    mode = str(sim_cfg.get("dual_time_step_mode") or "s1503")
    fine = sim_cfg.get("_resolved_dual_fine_step_s")
    if fine is None:
        fine = fine_step_s if fine_step_s is not None else sim_cfg.get("_resolved_time_step_s")
    coarse = sim_cfg.get("_resolved_dual_coarse_step_s")
    if coarse is None:
        coarse = coarse_step_s if coarse_step_s is not None else fine
    nc = sim_cfg.get("_resolved_dual_ncoarse")
    if nc is None:
        nc = ncoarse if ncoarse is not None else (1 if fine and coarse and fine == coarse else None)
    ntot = sim_cfg.get("_resolved_num_time_steps")
    if ntot is None:
        ntot = num_time_steps if num_time_steps is not None else sim_cfg.get("num_time_steps")

    n_fine = sim_cfg.get("_resolved_n_fine_steps")
    n_coarse = sim_cfg.get("_resolved_n_coarse_steps")
    n_exec = sim_cfg.get("_resolved_n_exec_steps")
    if acc is not None:
        if n_fine is None:
            n_fine = getattr(acc, "n_fine_steps", None)
        if n_coarse is None:
            n_coarse = getattr(acc, "n_coarse_steps", None)
        if n_exec is None:
            n_exec = getattr(acc, "n_steps", None)

    out: dict[str, Any] = {
        "mode": mode,
        "fine_step_s": float(fine) if fine is not None else None,
        "coarse_step_s": float(coarse) if coarse is not None else None,
        "ncoarse": int(nc) if nc is not None else None,
        "num_time_steps": int(ntot) if ntot is not None else None,
        "n_fine_steps_executed": int(n_fine) if n_fine is not None else None,
        "n_coarse_steps_executed": int(n_coarse) if n_coarse is not None else None,
        "n_exec_steps": int(n_exec) if n_exec is not None else None,
    }
    # Drop unused helper noise — keep reference §D4 values when present.
    if sim_cfg.get("_s1503_reference_num_time_steps") is not None:
        out["s1503_reference_num_time_steps"] = int(sim_cfg["_s1503_reference_num_time_steps"])
    if sim_cfg.get("_s1503_reference_time_step_s") is not None:
        out["s1503_reference_time_step_s"] = float(sim_cfg["_s1503_reference_time_step_s"])
    return out


def _task_n_jobs(cfg: dict[str, Any], common: dict[str, Any]) -> int:
    """Engine ``n_jobs`` (real OS processes) for a sim dispatched via
    ``cluster.parallel_starmap_progress``.

    Three-way precedence:

    1. An explicit ``cfg["simulation"]["n_jobs"]`` always wins.
    2. This task's OWN Ray CPU reservation, when present — published
       per-task via ``cluster.TASK_CPUS_ENV`` (see ``cluster._task_scoped``).
       Ray's ``num_cpus`` accounting sizes how many tasks run concurrently,
       but does NOT make any single task's own computation faster: every
       Ray worker process has Numba's thread pool capped to 1 thread at
       process startup (``uploads_runtime_env``'s env vars — a ceiling
       ``numba.set_num_threads`` can only lower, never raise). Confirmed
       live: a task that reserved 2.66 CPUs still measured ~100% of ONE
       core across all its threads, not ~266% — the reservation alone did
       nothing. The engine's own ``multiprocessing.Pool`` (this ``n_jobs``:
       real OS processes, each still 1-Numba-thread, but N of them
       genuinely use N cores) is the axis that still scales per task — the
       heavier filings ``_costs_to_num_cpus`` reserves more CPU for (e.g. a
       30,000-satellite filing next to a 30-satellite one in the same grid
       sweep) need this or the extra reservation just idles.
    3. Otherwise, ``common["task_n_jobs"]`` — decided ONCE per run in
       ``_run`` from ``cluster.ensure_init``: ``1`` when Ray is active
       (many tasks run concurrently, one per DEFAULT reserved slot — an
       inner Pool on top would oversubscribe absent the per-task override
       above), ``-1``/auto when standalone (``parallel_starmap_progress``
       degenerates to a sequential loop there, so each task runs alone —
       measured at ~2 of ~22 cores when left at 1). Defaults to ``1`` when
       absent (a caller/test that builds ``common`` without going through
       ``_run``).
    """
    explicit = (cfg.get("simulation") or {}).get("n_jobs")
    if explicit:
        return int(explicit)
    import os
    env_val = os.environ.get(cluster.TASK_CPUS_ENV)
    if env_val:
        try:
            return max(1, int(round(float(env_val))))
        except ValueError:
            pass
    return int(common.get("task_n_jobs", 1) or 1)


def _run_single_filing(filing: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    """Run a complete single-system S.1503 pipeline (WCGA + EPFD↓ + compliance)."""
    from src.main import run_wcg_downlink  # type: ignore[import]
    from src.exceptions import NoValidGeometry  # type: ignore[import]
    cfg = _load_cfg(filing, common)
    # run_wcg_downlink reads n_jobs straight off cfg["simulation"] — set it
    # explicitly (see _task_n_jobs) so a Ray-dispatched task uses its OWN
    # CPU reservation instead of always grabbing every core.
    cfg.setdefault("simulation", {})["n_jobs"] = _task_n_jobs(cfg, common)
    try:
        constellation, wcg_dl, sim_dl, comp_dl, *_ = run_wcg_downlink(cfg)
    except NoValidGeometry as exc:
        # Completed search, no valid geometry — a legitimate per-filing outcome,
        # not a crash. Return an empty result so the aggregate keeps going.
        return {
            "wcg": None,
            "ccdf_bins_db": [],
            "ccdf_pct": [],
            "max_epfd_dbw": None,
            "compliance": "no_geometry",
            "message": str(exc),
            "n_satellites": 0,
        }
    if sim_dl is not None:
        try:
            sim_dl.build_cdf()
        except Exception:  # noqa: BLE001
            pass
    acc = getattr(sim_dl, "acc", None) if sim_dl is not None else None
    return {
        "wcg": {
            "es_lat_deg": float(wcg_dl.es_lat_deg),
            "es_lon_deg": float(wcg_dl.es_lon_deg),
            "gso_lon_deg": float(wcg_dl.gso_lon_deg),
            "epfd_dBW": float(wcg_dl.epfd_dBW),
        } if wcg_dl else None,
        "ccdf_bins_db": list(map(float, sim_dl.cdf_epfd_dBW)) if sim_dl is not None else [],
        "ccdf_pct": list(map(float, sim_dl.cdf_percentage)) if sim_dl is not None else [],
        "max_epfd_dbw": float(sim_dl.cdf_epfd_dBW[0])
        if sim_dl is not None and len(sim_dl.cdf_epfd_dBW) else None,
        "compliance": "pass" if (comp_dl and comp_dl.compliant) else (
            "fail" if comp_dl else "unknown"
        ),
        "n_satellites": int(len(constellation)) if constellation else 0,
        # Per-filing §D4 / §D4.7 time base (auto or overridden) — same keys as
        # Single-entry sim_data["dual_time_step"].
        "dual_time_step": _dual_time_step_block(cfg.get("simulation") or {}, acc),
    }


def _run_at_geometry(cfg: dict[str, Any], gp, common: dict[str, Any]) -> dict[str, Any]:
    """Run S.1503 for one filing at a fixed geometry (no WCG search)."""
    from src.main import create_constellation_for_config, resolve_time_base  # type: ignore[import]
    from src.s1588_studies import run_epfd_at_geometry  # type: ignore[import]

    import math as _math

    # Honour restrict_emitters_to_sim_band (default ON in _load_cfg).
    constellation, mask_ids = create_constellation_for_config(cfg)
    mask = _build_mask_for_sats(cfg, mask_ids)
    antenna = _build_antenna(cfg)

    sim = cfg["simulation"]
    ngso = cfg["non_gso"]
    # Unset N / Δt fall back to this filing's own §D4 reference (auto).
    tstep, nsteps = resolve_time_base(cfg)
    t_run_s = nsteps * tstep

    # Orbital dynamics (S.1503-4 §D6.3) — same semantics as run_wcg_downlink's
    # manual-steps path: artificial precession = one RAAN revolution over T_run.
    raan_dot_artificial = (
        (2.0 * _math.pi) / t_run_s
        if (sim.get("artificial_precession") and t_run_s > 0) else 0.0
    )
    raan_dot_override = None
    if sim.get("use_precession_mdb"):
        pday = float(ngso.get("_precession_deg_day", 0.0) or 0.0)
        if pday:
            raan_dot_override = (pday * _math.pi / 180.0) / 86400.0
    wdelta_deg = 0.0
    if sim.get("apply_station_keeping_wdelta") and ngso.get("_f_stn_keep"):
        wdelta_deg = float(ngso.get("_keep_range_deg", 0.0) or 0.0)

    sim_dl = run_epfd_at_geometry(
        constellation=constellation,
        geometry=gp,
        pfd_mask=mask,
        es_antenna=antenna,
        num_time_steps=nsteps,
        time_step_s=tstep,
        alpha0_deg=float(cfg["non_gso"].get("alpha0_deg", 0.0)),
        min_elevation_deg=float(cfg["non_gso"].get("min_elevation_deg", 5.0)),
        n_jobs=_task_n_jobs(cfg, common),
        raan_dot_artificial_rad_s=raan_dot_artificial,
        raan_dot_override_rad_s=raan_dot_override,
        wdelta_deg=wdelta_deg,
        t_run_s=t_run_s,
        pfd_bw_correction_db=_bw_correction_db(cfg, mask),
        **_s1503_normative_caps(cfg),
    )
    try:
        sim_dl.build_cdf()
    except Exception:  # noqa: BLE001
        pass
    acc = getattr(sim_dl, "acc", None)
    return {
        "ccdf_bins_db": list(map(float, sim_dl.cdf_epfd_dBW)),
        "ccdf_pct": list(map(float, sim_dl.cdf_percentage)),
        "max_epfd_dbw": float(sim_dl.cdf_epfd_dBW[0]) if len(sim_dl.cdf_epfd_dBW) else None,
        "n_satellites": int(len(constellation) or 0),
        # Fixed-geometry path: usually single Δt (no dual). Still record N / Δt.
        "dual_time_step": _dual_time_step_block(
            sim, acc,
            fine_step_s=tstep, coarse_step_s=tstep, ncoarse=1,
            num_time_steps=nsteps,
        ),
    }


def _convolve(
    per_system_ccdfs: list[tuple[list[float], list[float]]],
    truncate_tail_pct: float | None = None,
) -> tuple[list[float], list[float]]:
    from src.s1588_studies import convolve_ccdfs_db  # type: ignore[import]
    bins, pct = convolve_ccdfs_db(
        per_system_ccdfs, truncate_tail_pct=truncate_tail_pct
    )
    return [float(x) for x in bins], [float(x) for x in pct]


def _truncation_floor(
    params: dict[str, Any],
    ccdfs_in: list[tuple[list[float], list[float]]],
) -> float | None:
    """S.1588 low-% tail truncation floor (% of time), or None when disabled.

    Enabled by ``params['truncate_tail']``. An explicit
    ``params['truncate_tail_pct']`` wins; otherwise the floor is the smallest
    positive % present across the input CCDFs — the aggregate must not claim
    finer probability resolution than the per-system simulations provide.
    """
    if not params.get("truncate_tail"):
        return None
    explicit = params.get("truncate_tail_pct")
    if explicit is not None:
        try:
            v = float(explicit)
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    floor: float | None = None
    for _bins, pcts in ccdfs_in:
        for p in pcts:
            if p > 0 and (floor is None or p < floor):
                floor = float(p)
    return floor


# ─── Ray-friendly task wrappers (top-level for pickling) ───────────────────


def _single_filing_task(filing: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    """Top-level wrapper around _run_single_filing — JSON-pickleable args."""
    return _run_single_filing(filing, common)


def _at_geometry_task(filing: dict[str, Any], common: dict[str, Any],
                        lat: float, lon: float, glon: float) -> dict[str, Any]:
    """Top-level wrapper — loads cfg + runs at fixed geometry. Pickleable args."""
    from src.s1588_studies.geometry import GeometryPoint  # type: ignore[import]
    cfg = _load_cfg(filing, common)
    gp = GeometryPoint(es_lat_deg=lat, es_lon_deg=lon, gso_lon_deg=glon)
    return _run_at_geometry(cfg, gp, common)


def _system_contribution_task(
    filing: dict[str, Any], common: dict[str, Any],
    joint_ctx: dict[str, Any], max_co_freq_by_lat: list,
) -> dict[str, Any]:
    """method_3 per-system decomposition: this filing's own satellites' EPFD
    contribution, evaluated with the JOINT run's shared geometry, time base,
    ES antenna and gates (``joint_ctx``) — everything the fused simulation
    applies UNIFORMLY to every satellite regardless of system. The
    constellation/mask, MAX_CO_FREQ and ε₀/α₀ stay per-filing (Steps 18-22
    are intra-system even inside the joint run: the joint pass partitions
    Step-18 eligibility per system, and the caller injects this filing's own
    ε₀/α₀ into ``joint_ctx`` — see ``_decompose_by_resimulation``).

    This does NOT reuse ``_run_at_geometry`` (unlike method_2/4's fixed-
    geometry tasks): that helper derives antenna/α0/ε₀/Δt/N from THIS
    filing's own cfg, which is correct for method_2/4 (systems stay
    independent there) but wrong here — the per-system curves must share
    the joint run's exact parameters so the time series aligns sample-for-
    sample with it and the CCDFs sum (linear power) to the joint headline.
    Rebuilt from plain picklable primitives (``joint_ctx``), matching the
    Ray task convention used elsewhere in this worker.
    """
    from src.main import create_constellation_for_config  # type: ignore[import]
    from src.antenna import create_gso_es_antenna  # type: ignore[import]
    from src.s1588_studies import run_epfd_at_geometry  # type: ignore[import]
    from src.s1588_studies.geometry import GeometryPoint  # type: ignore[import]

    cfg = _load_cfg(filing, common)
    constellation, mask_ids = create_constellation_for_config(cfg)
    empty = {"ccdf_bins_db": [], "ccdf_pct": [], "max_epfd_dbw": None,
             "n_satellites": 0, "timeseries_t_s": [], "timeseries_epfd_db": [],
             "timeseries_duration_s": []}
    if not constellation:
        return empty
    # This filing's own orbit dynamics (§D6.3), relative to the SAME joint
    # T_run as the headline sim — matches _apply_orbit_dynamics's use in the
    # fuse loop, so this decomposition reproduces what actually ran for
    # these satellites (not plain unperturbed J2 propagation).
    constellation = _apply_orbit_dynamics(
        cfg, constellation, joint_ctx["dt"] * joint_ctx["num_steps"],
    )
    mask = _build_mask_for_sats(cfg, mask_ids)
    antenna = create_gso_es_antenna(
        joint_ctx["es_diameter_m"], joint_ctx["es_freq_ghz"],
        joint_ctx["es_efficiency"], service=joint_ctx["es_service"],
    )
    gp = GeometryPoint(es_lat_deg=joint_ctx["lat"], es_lon_deg=joint_ctx["lon"],
                       gso_lon_deg=joint_ctx["glon"])
    sim_i = run_epfd_at_geometry(
        constellation=constellation,
        geometry=gp,
        pfd_mask=mask,
        es_antenna=antenna,
        num_time_steps=joint_ctx["num_steps"],
        time_step_s=joint_ctx["dt"],
        alpha0_deg=joint_ctx["alpha0_deg"],
        min_elevation_deg=joint_ctx["min_elevation_deg"],
        pfd_bw_correction_db=joint_ctx["pfd_bw_correction_db"],
        n_jobs=_task_n_jobs(cfg, common),
        max_co_freq_by_lat=max_co_freq_by_lat,
        strict_max_co_freq_total=joint_ctx["strict_max_co_freq_total"],
        strict_exclusion_zone=joint_ctx["strict_exclusion_zone"],
        min_angle_at_es_deg=joint_ctx["min_angle_at_es_deg"],
        gso_min_elevation_deg=joint_ctx["gso_min_elevation_deg"],
        # No precession/station-keeping: the joint run itself doesn't apply
        # any (raan_dot_artificial_rad_s / raan_dot_override_rad_s /
        # wdelta_deg default to 0/None in _run_method_3's own call) — matched
        # here so the decomposition stays consistent with what actually ran.
    )
    try:
        sim_i.build_cdf()
    except Exception:  # noqa: BLE001
        pass
    acc = getattr(sim_i, "acc", None)
    return {
        "ccdf_bins_db": list(map(float, sim_i.cdf_epfd_dBW)),
        "ccdf_pct": list(map(float, sim_i.cdf_percentage)),
        "max_epfd_dbw": float(sim_i.cdf_epfd_dBW[0]) if len(sim_i.cdf_epfd_dBW) else None,
        "n_satellites": int(len(constellation)),
        "timeseries_t_s": [float(x) for x in (getattr(acc, "decim_t_s", None) or [])],
        "timeseries_epfd_db": [float(x) for x in (getattr(acc, "decim_epfd_db", None) or [])],
        "timeseries_duration_s": [
            float(x) for x in (getattr(acc, "decim_duration_s", None) or [])
        ],
    }


def _progress_cb(pct_start: float, pct_end: float) -> Any:
    """Return a callback `on_done(i, n)` that emits PROGRESS in [start, end]."""
    def _cb(i: int, n: int) -> None:
        if n <= 0:
            return
        _emit_progress(pct_start + (pct_end - pct_start) * i / n)
    return _cb


# ─── method_1 ────────────────────────────────────────────────────────────────


def _run_method_1(params: dict[str, Any]) -> dict[str, Any]:
    filings = params["filings"]
    common = {k: v for k, v in params.items() if k not in ("filings", "method", "result_path")}
    n = len(filings)
    info = cluster.ensure_init()
    _emit(f"[method_1] dispatching {n} filing(s) · mode={info.get('mode')}"
          + (" · ray active" if info.get("active") else " · sequential"))
    tasks = [(f, common) for f in filings]
    costs = plan.filing_costs(filings, common, sim=True, wcga=True)
    per_system = cluster.parallel_starmap_progress(
        _single_filing_task, tasks,
        on_done=_progress_cb(5.0, 85.0),
        costs=costs,
    )
    _emit("[method_1] convolving per-system CCDFs")
    _emit_progress(88)
    ccdfs_in = [(r["ccdf_bins_db"], r["ccdf_pct"]) for r in per_system if r["ccdf_bins_db"]]
    if not ccdfs_in:
        return {"method": "method_1", "error": "no per-system CCDF available", "per_system": per_system}
    floor = _truncation_floor(params, ccdfs_in)
    bins, pct = _convolve(ccdfs_in, floor)
    out = {
        "method": "method_1",
        "ccdf_bins_db": bins,
        "ccdf_pct": pct,
        "max_epfd_dbw_m2_40khz": bins[0] if bins else None,
        "percentiles": _percentiles(bins, pct, floor) if bins else {},
        "per_system": per_system,
        "n_systems": n,
    }
    if floor is not None:
        out["truncation_floor_pct"] = float(floor)
    return out


# ─── method_2 ────────────────────────────────────────────────────────────────


def _run_grid_convolution(params: dict[str, Any], method_label: str) -> dict[str, Any]:
    """Common ES×GSO-grid aggregation — keeps **all** curves at every stage.

    One computation (grid × filings simulation, once) yields the full set of
    curves, so there is no method-specific flag and no re-run to switch views:

      * per grid point → ``per_system`` (each filing's raw CCDF) **and** their
        convolved CCDF;
      * across grid points → the worst-per-percentile **envelope** (headline
        CCDF, used for the go/no-go verdict).

    Both the Study-2 (envelope) and the WP-4A Step-1 (per-geometry, per-system)
    readings are just views over this single output — the caller only sets the
    ``method`` label. Note the per-system detail can make ``sim_data.json`` large
    for a fine world-wide grid (n_points × n_filings CCDFs).
    """
    from src.s1588_studies import iter_geometry_grid  # type: ignore[import]
    import numpy as np

    filings = params["filings"]
    common = {k: v for k, v in params.items() if k not in ("filings", "method", "result_path")}
    grid_step = float(params.get("grid_step_deg", 30.0))
    gso_step = float(params.get("gso_pointing_step_deg", 30.0))
    country_codes = list(params.get("country_codes") or []) or None

    # Grid GSO-visibility cut-off. Explicit UI override wins; otherwise use the
    # least restrictive filing ε₀ (min) so the grid stays usable for every
    # system while each filing's EPFD↓ still uses its own ε₀ from cfg.
    if params.get("min_elevation_deg") is not None:
        min_elev = float(params["min_elevation_deg"])
    else:
        _elevs = [
            float(_load_cfg(f, common)["non_gso"].get("min_elevation_deg", 5.0))
            for f in filings
        ]
        min_elev = min(_elevs) if _elevs else 5.0

    grid_points = list(iter_geometry_grid(
        grid_step_deg=grid_step,
        gso_pointing_step_deg=gso_step,
        min_elevation_deg=min_elev,
        country_codes=country_codes,
    ))
    info = cluster.ensure_init()
    _emit(f"[{method_label}] {len(grid_points)} grid points · {len(filings)} systems"
          + (f" · countries={country_codes}" if country_codes else " · world-wide")
          + (" · ray active" if info.get("active") else " · sequential"))

    # Flatten (grid, filing) into a single task list so Ray can pack workers.
    fc = plan.filing_costs(filings, common, sim=True, wcga=False)
    tasks: list[tuple] = []
    keys: list[tuple[int, int]] = []  # (pi, fi)
    costs: list[float] = []
    for pi, gp in enumerate(grid_points):
        for fi, filing in enumerate(filings):
            tasks.append((filing, common,
                          float(gp.es_lat_deg), float(gp.es_lon_deg),
                          float(gp.gso_lon_deg)))
            keys.append((pi, fi))
            costs.append(fc[fi])

    results = cluster.parallel_starmap_progress(
        _at_geometry_task, tasks,
        on_done=_progress_cb(2.0, 95.0),
        costs=costs,
    )

    # Regroup by grid point
    by_pi: dict[int, list[tuple[int, dict[str, Any]]]] = {}
    for k, r in enumerate(results):
        pi, fi = keys[k]
        by_pi.setdefault(pi, []).append((fi, r))

    # Per grid point: keep BOTH the per-system raw CCDFs and their convolution.
    per_point: list[dict[str, Any]] = []
    # One §D4 time base per filing (identical across grid points for that filing).
    filing_dts: dict[int, dict[str, Any]] = {}
    filing_nsat: dict[int, int] = {}
    for pi, gp in enumerate(grid_points):
        rows = sorted(by_pi.get(pi, []), key=lambda x: x[0])
        per_sys = [
            {
                "system_index": fi,
                "ccdf_bins_db": r["ccdf_bins_db"], "ccdf_pct": r["ccdf_pct"],
                "max_epfd_dbw": r.get("max_epfd_dbw"),
            }
            for fi, r in rows
        ]
        for fi, r in rows:
            if fi not in filing_dts and isinstance(r.get("dual_time_step"), dict):
                filing_dts[fi] = r["dual_time_step"]
            if fi not in filing_nsat and r.get("n_satellites") is not None:
                filing_nsat[fi] = int(r["n_satellites"])
        per_sys_ccdfs = [
            (r["ccdf_bins_db"], r["ccdf_pct"]) for _fi, r in rows if r["ccdf_bins_db"]
        ]
        entry: dict[str, Any] = {
            "index": pi,
            "es_lat_deg": float(gp.es_lat_deg),
            "es_lon_deg": float(gp.es_lon_deg),
            "gso_lon_deg": float(gp.gso_lon_deg),
            "per_system": per_sys,
        }
        if per_sys_ccdfs:
            bins, pct = _convolve(per_sys_ccdfs, _truncation_floor(params, per_sys_ccdfs))
            entry["ccdf_bins_db"] = bins
            entry["ccdf_pct"] = pct
            entry["max_epfd_dbw"] = bins[0] if bins else None
        per_point.append(entry)

    # Envelope (worst per percentage, linear power scale). When tail truncation
    # is active the per-point curves stop at the floor — do NOT extend them flat
    # down to 0.0001% via np.interp(left=...): that would fabricate probability
    # resolution the truncated curves don't have, inconsistent with methods
    # 1/3/4. Restrict the common axis to >= floor.
    conv_points = [p for p in per_point if p.get("ccdf_bins_db")]
    env_floor = _truncation_floor(
        params, [(p["ccdf_bins_db"], p["ccdf_pct"]) for p in conv_points],
    )
    pct_lo = float(env_floor) if env_floor is not None else 0.0
    env_bins, env_pct = _ccdf_envelope(
        [(p["ccdf_bins_db"], p["ccdf_pct"]) for p in conv_points], pct_lo,
    )

    per_system_tb = [
        {
            "system_index": fi,
            "n_satellites": filing_nsat.get(fi),
            "dual_time_step": filing_dts[fi],
        }
        for fi in sorted(filing_dts)
    ]

    out = {
        "method": method_label,
        "grid_step_deg": grid_step,
        "gso_pointing_step_deg": gso_step,
        "country_codes": country_codes or [],
        "n_grid_points": len(grid_points),
        "n_systems": len(filings),
        "ccdf_bins_db": env_bins,
        "ccdf_pct": env_pct,
        "max_epfd_dbw_m2_40khz": env_bins[0] if env_bins else None,
        "percentiles": _percentiles(env_bins, env_pct, env_floor) if env_bins else {},
        "per_point": per_point,
        "per_system": per_system_tb,
    }
    if env_floor is not None:
        out["truncation_floor_pct"] = float(env_floor)
    return out


def _run_method_2(params: dict[str, Any]) -> dict[str, Any]:
    """Study 2 — common ES×GSO grid: convolution per point + worst-per-percentile
    envelope. Shares the full-curve core with method_5 (see
    :func:`_run_grid_convolution`)."""
    return _run_grid_convolution(params, "method_2")


# ─── method_3 ────────────────────────────────────────────────────────────────


_EMPTY_DECOMP: dict[str, Any] = {
    "ccdf_bins_db": [], "ccdf_pct": [], "max_epfd_dbw": None,
    "n_satellites": 0, "timeseries_t_s": [], "timeseries_epfd_db": [],
    "timeseries_duration_s": [],
}


def _decompose_from_joint_acc(
    acc: Any, cfgs: list[dict[str, Any]], system_id_per_sat: list[int],
) -> list[dict[str, Any]] | None:
    """Per-system curves read off the joint run's own accumulator.

    ``run_epfd_simulation``, when given ``system_id_per_sat`` +
    ``max_co_freq_by_lat_per_system``, accumulates one sub-accumulator per
    system in the SAME pass as the joint curve (``acc.per_system``, see
    ``epfd_calculator._acc_add_per_system``). Each sub-accumulator holds that
    system's own linear-power contribution at the joint geometry, on the joint
    time base — exactly what the old per-system re-simulation produced, at zero
    extra cost.

    Returns ``None`` when the joint pass carried no per-system split (engine
    paths that don't populate it yet, e.g. a dual-time-step joint run), so the
    caller can fall back to :func:`_decompose_by_resimulation`. Takes the
    accumulator itself (not the result object) so it also serves the mid-run
    partial snapshots, where only a partially merged accumulator exists.
    """
    per_system = getattr(acc, "per_system", None) if acc is not None else None
    if not per_system:
        return None

    out: list[dict[str, Any]] = []
    for i in range(len(cfgs)):
        sub = per_system.get(i)
        row: dict[str, Any] = {"system_index": i, "label": _system_label(cfgs[i], i)}
        if sub is None:
            out.append({**row, **_EMPTY_DECOMP})
            continue
        bins, pct = sub.build_ccdf()
        out.append({
            **row,
            "ccdf_bins_db": list(map(float, bins)),
            "ccdf_pct": list(map(float, pct)),
            "max_epfd_dbw": float(bins[0]) if len(bins) else None,
            "n_satellites": int(sum(1 for s in system_id_per_sat if int(s) == i)),
            "timeseries_t_s": [float(x) for x in sub.decim_t_s],
            "timeseries_epfd_db": [float(x) for x in sub.decim_epfd_db],
            "timeseries_duration_s": [float(x) for x in sub.decim_duration_s],
        })
    return out


def _write_partial_geometry(result_path: Path, wcg: Any) -> None:
    """Persists the joint WCG into ``partial/`` as soon as the WCGA closes.

    Best-effort: a filesystem hiccup must not abort a run that just spent a
    WCGA finding this geometry.
    """
    from streamlit_app.lib import result_artifacts  # noqa: PLC0415

    payload = {
        "method": "method_3",
        "partial": True,
        "geometry": {
            "es_lat_deg": float(wcg.es_lat_deg),
            "es_lon_deg": float(wcg.es_lon_deg),
            "gso_lon_deg": float(wcg.gso_lon_deg),
        },
        "wcg_epfd_dbw": float(getattr(wcg, "epfd_dBW", float("nan"))),
    }
    try:
        out_dir = result_path / "partial"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "wcg.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        result_artifacts.write_geometries_csv(out_dir, payload)
        _emit(f"[method_3] joint WCG persisted to partial/ "
              f"(ES {payload['geometry']['es_lat_deg']:.2f},"
              f"{payload['geometry']['es_lon_deg']:.2f} · "
              f"GSO {payload['geometry']['gso_lon_deg']:.2f})")
    except Exception as exc:  # noqa: BLE001 — artifacts are best-effort
        _emit(f"WARN: could not persist the joint WCG: {exc}")


def _partial_snapshot_writer(
    result_path: Path,
    wcg: Any,
    cfgs: list[dict[str, Any]],
    system_id_per_sat: list[int],
    *,
    min_interval_s: float = 30.0,
) -> Any:
    """Builds the ``on_chunk`` callback that persists mid-run method_3 results.

    A joint run over a megaconstellation takes hours; cancelling it used to
    leave nothing. This writes the joint CCDF, the decimated time series, the
    per-system curves and the geometry after each chunk, reusing
    ``result_artifacts``' writers — so the files carry the exact same names,
    headers and units as a finished run and open with the normal tooling.

    Everything lands in a **``partial/`` subdirectory**, never the run root: a
    truncated CCDF that looks like a finished one is a real hazard when the
    output feeds a filing. The final run writes the root artifacts as usual.

    Throttled to ``min_interval_s`` because the engine calls back per chunk
    (and per 2 s heartbeat on the sequential path) while each snapshot rewrites
    files of up to ~10k rows. The last chunk always writes, so the newest
    snapshot is never one interval stale.
    """
    from streamlit_app.lib import result_artifacts  # noqa: PLC0415

    out_dir = result_path / "partial"
    geometry = {
        "es_lat_deg": float(wcg.es_lat_deg),
        "es_lon_deg": float(wcg.es_lon_deg),
        "gso_lon_deg": float(wcg.gso_lon_deg),
    }
    state = {"last": 0.0}

    def _on_chunk(acc: Any, steps_done: int, steps_total: int) -> None:
        now = time.monotonic()
        is_last = steps_done >= steps_total
        if not is_last and (now - state["last"]) < min_interval_s:
            return
        state["last"] = now
        out_dir.mkdir(parents=True, exist_ok=True)

        bins, pct = acc.build_ccdf()
        bins_l = list(map(float, bins))
        pct_l = list(map(float, pct))
        snapshot: dict[str, Any] = {
            "method": "method_3",
            # Marks this as a truncated run for anything that reads the JSON.
            "partial": True,
            "progress": {
                "steps_done": int(steps_done),
                "steps_total": int(steps_total),
                "pct": round(100.0 * steps_done / max(1, steps_total), 3),
            },
            "geometry": geometry,
            "ccdf_bins_db": bins_l,
            "ccdf_pct": pct_l,
            "max_epfd_dbw_m2_40khz": bins_l[0] if bins_l else None,
            "percentiles": _percentiles(bins_l, pct_l) if bins_l else {},
            "per_system_at_wcg": (
                _decompose_from_joint_acc(acc, cfgs, system_id_per_sat) or []
            ),
            "n_systems": len(cfgs),
        }
        (out_dir / "sim_data.partial.json").write_text(
            json.dumps(snapshot, indent=2), encoding="utf-8",
        )
        # CSVs only — the PNG writers spin up matplotlib, too slow per chunk.
        for fn in (
            lambda: result_artifacts.write_ccdf_csv(out_dir, snapshot),
            lambda: result_artifacts.write_timeseries_csv(out_dir, snapshot, acc),
            lambda: result_artifacts.write_per_system_ccdf_csv(out_dir, snapshot),
            lambda: result_artifacts.write_per_system_timeseries_csv(out_dir, snapshot),
            lambda: result_artifacts.write_geometries_csv(out_dir, snapshot),
        ):
            try:
                fn()
            except Exception:  # noqa: BLE001 — snapshots are best-effort
                pass

    return _on_chunk


def _decompose_by_resimulation(
    *,
    filings: list[dict[str, Any]],
    cfgs: list[dict[str, Any]],
    common: dict[str, Any],
    wcg: Any,
    dt: float,
    num_steps: int,
    alpha0: float,
    min_elev: float,
    caps0: dict[str, Any],
    per_system_nco: list,
    bw_correction_db: float,
    per_system_eps0: list | None = None,
    per_system_alpha0: list | None = None,
) -> list[dict[str, Any]]:
    """Fallback decomposition: one full simulation per system at the joint WCG.

    Costs about as much as the joint run itself (same step count, and the
    systems' satellites sum to the fused constellation), so this is only for
    engine paths where :func:`_decompose_from_joint_acc` cannot serve.

    ``per_system_eps0`` / ``per_system_alpha0``: each filing's own ε₀/α₀ —
    the joint pass partitions Step-18 eligibility per system, so each task
    must gate this filing's satellites with the SAME thresholds the joint run
    applied to them (falls back to the shared scalars when absent).
    """
    joint_ctx = {
        "lat": float(wcg.es_lat_deg), "lon": float(wcg.es_lon_deg),
        "glon": float(wcg.gso_lon_deg),
        "dt": dt, "num_steps": num_steps,
        "alpha0_deg": alpha0, "min_elevation_deg": min_elev,
        "es_diameter_m": float(cfgs[0]["gso_es"]["antenna_diameter_m"]),
        "es_efficiency": float(cfgs[0]["gso_es"].get("antenna_efficiency", 0.99)),
        "es_service": str(cfgs[0]["gso_es"].get("service", "FSS")).upper(),
        "es_freq_ghz": float(cfgs[0]["non_gso"]["frequency_ghz"]),
        "strict_max_co_freq_total": caps0["strict_max_co_freq_total"],
        "strict_exclusion_zone": caps0["strict_exclusion_zone"],
        "min_angle_at_es_deg": caps0["min_angle_at_es_deg"],
        "gso_min_elevation_deg": caps0["gso_min_elevation_deg"],
        "pfd_bw_correction_db": bw_correction_db,
    }
    tasks = [
        (f, common,
         {**joint_ctx,
          "alpha0_deg": float(per_system_alpha0[i])
          if per_system_alpha0 is not None else alpha0,
          "min_elevation_deg": float(per_system_eps0[i])
          if per_system_eps0 is not None else min_elev},
         per_system_nco[i])
        for i, f in enumerate(filings)
    ]
    costs = plan.filing_costs(filings, common, sim=True, wcga=False)
    try:
        results = cluster.parallel_starmap_progress(
            _system_contribution_task, tasks,
            on_done=_progress_cb(82.0, 88.0),
            costs=costs,
        )
    except Exception as exc:  # noqa: BLE001
        _emit(f"WARN: parallel per-system decomposition failed ({exc}); falling back sequential")
        results = []
        for idx, task_args in enumerate(tasks):
            try:
                results.append(_system_contribution_task(*task_args))
            except Exception as e2:  # noqa: BLE001
                _emit(f"WARN: per-system decomposition fallback {idx}: {e2}")
                results.append(None)
    return [
        {"system_index": i, "label": _system_label(cfgs[i], i), **(r or _EMPTY_DECOMP)}
        for i, r in enumerate(results)
    ]


def _joint_time_base(
    refs: list[tuple[float, int, int]], sim_cfg: dict[str, Any],
) -> tuple[float, float, int, int, dict[str, str]]:
    """Joint (Δt_fine, Δt_coarse, Ncoarse, N) for a fused megaconstellation.

    ``refs`` is one ``(Δt_fine, NSTEPS, Ncoarse)`` §D4 reference per filing
    (see :func:`src.main.compute_s1503_dual_reference`), all dimensioned with
    the joint run's shared victim-ES antenna.

    Rule:

    * a value the USER supplied is used **as given**. In particular the
      iteration count N is the count of the JOINT run. It used to be applied
      per filing and then re-derived by the §D4.1 rule, which multiplied it by
      max Δt / min Δt — two filings whose auto Δt were 26.977 s and 0.94 s
      turned a requested N=100 000 into 2 869 893 steps;
    * a value left on auto is the **smallest** among the values computed for
      the individual systems — §D4.1's "smallest time step over all
      sub-constellations", applied here across filings. For N that means
      ``floor(longest T_run / smallest Δt_fine)``, since no requested count
      exists to honour.

    §D4.7 wants the coarse step to be an integer multiple of the fine one;
    a user-supplied pair is snapped to the nearest multiple exactly as
    ``run_wcg_downlink`` does, and a coarse step that is not larger than the
    fine one collapses the ladder to a single step (``Ncoarse=1``).

    Returns ``(fine, coarse, ncoarse, nsteps, sources)``, where ``sources``
    maps ``n``/``fine``/``coarse`` to ``"user"`` or ``"auto(min)"`` for the
    run log.
    """
    user_n = int(sim_cfg.get("num_time_steps", 0) or 0)
    user_fine = (float(sim_cfg.get("fine_time_step_s", 0.0) or 0.0)
                 if sim_cfg.get("_fine_step_overridden") else 0.0)
    user_coarse = (float(sim_cfg.get("coarse_time_step_s", 0.0) or 0.0)
                   if sim_cfg.get("_coarse_step_overridden") else 0.0)

    fine = user_fine if user_fine > 0.0 else min(r[0] for r in refs)
    nsteps = (user_n if user_n > 0
              else int(math.floor(max(r[0] * r[1] for r in refs) / fine)))
    coarse = (user_coarse if user_coarse > 0.0
              else min(r[0] * max(1, r[2]) for r in refs))
    if coarse > fine:
        ncoarse = max(1, int(round(coarse / fine)))
        coarse = fine * ncoarse
    else:
        ncoarse, coarse = 1, fine

    def _src(from_user: bool) -> str:
        return "user" if from_user else "auto(min)"

    return fine, coarse, ncoarse, nsteps, {
        "n": _src(user_n > 0),
        "fine": _src(user_fine > 0.0),
        "coarse": _src(user_coarse > 0.0),
    }


def _run_method_3(params: dict[str, Any]) -> dict[str, Any]:
    """Joint simulation (Method 2B): fused megaconstellation, single sim run."""
    from src.main import (  # type: ignore[import]
        create_constellation_for_config, compute_s1503_dual_reference,
    )
    from src.time_step import DualTimeStep  # type: ignore[import]
    from src.pfd_mask import PFDMaskMulti  # type: ignore[import]
    from src.epfd_calculator import run_epfd_simulation  # type: ignore[import]
    from src.wcg_search import search_wcg_s1503  # type: ignore[import]
    from src.s1588_studies import geometry_to_wcg_result  # type: ignore[import]
    from src.s1588_studies.geometry import GeometryPoint  # type: ignore[import]
    import math
    import numpy as np

    filings = params["filings"]
    common = {k: v for k, v in params.items() if k not in ("filings", "method", "result_path")}

    cfgs = [_load_cfg(f, common) for f in filings]

    # ε₀/α₀ per system: the engine partitions Step-18 eligibility by system
    # (min_elevation_deg_per_system / alpha0_deg_per_system + system_id_per_sat),
    # so each filing's satellites gate on that filing's OWN thresholds — the
    # same criterion each filing gets on its independent method_1 run. A UI
    # ε₀ override was already applied per filing by _load_cfg, so it shows up
    # here as identical per-system values.
    per_system_eps0 = [
        float(c["non_gso"].get("min_elevation_deg", 5.0)) for c in cfgs
    ]
    per_system_alpha0 = [
        float(c["non_gso"].get("alpha0_deg", 0.0)) for c in cfgs
    ]

    # Scalar ε₀ fallback (engine arg + logs): least restrictive filing value.
    # With the per-system lists above it no longer gates any satellite.
    if common.get("min_elevation_deg") is not None:
        min_elev = float(common["min_elevation_deg"])
    else:
        min_elev = min(per_system_eps0) if per_system_eps0 else 5.0

    if len(set(zip(per_system_eps0, per_system_alpha0))) > 1:
        _emit("[method_3] per-system Step-18 thresholds: " + " · ".join(
            f"sys{i}: ε₀={e:g}° α₀={a:g}°"
            for i, (e, a) in enumerate(zip(per_system_eps0, per_system_alpha0))
        ))

    # Built here (not further down) because the §D4 time-base reference below
    # must use the SAME es_antenna θ_3dB the joint simulation actually applies
    # to every satellite — one shared victim ES/antenna is the correct physical
    # model for a joint aggregate (not each filing's own antenna).
    es_antenna = _build_antenna(cfgs[0])
    alpha0 = float(cfgs[0]["non_gso"].get("alpha0_deg", 0.0))

    # Joint time base (N, Δt_fine, Δt_coarse) — see _joint_time_base. Each
    # filing's own §D4 reference is dimensioned with the JOINT run's shared
    # es_antenna: its θ_3dB is what §D4.2/§D4.7 actually consume, and one
    # shared victim ES is the correct physical model for an aggregate.
    _sim0 = cfgs[0].get("simulation") or {}
    dt, coarse, ncoarse, num_steps, _src = _joint_time_base(
        [compute_s1503_dual_reference(c, es_antenna=es_antenna) for c in cfgs],
        _sim0,
    )
    _emit(
        f"[method_3] joint time base: N={num_steps:,} [{_src['n']}] · "
        f"Δt_fine={dt:.6f}s [{_src['fine']}] · "
        f"Δt_coarse={coarse:.6f}s [{_src['coarse']}] · "
        f"Ncoarse={ncoarse} · T_run={dt * num_steps:,.0f}s"
    )

    _emit("[method_3] fusing constellations into megaconstellation")
    _emit_progress(10)

    # Joint T_run — every satellite propagates over this SAME span regardless
    # of system, so each filing's own artificial-precession/station-keeping
    # rate (§D6.3) is defined relative to it, not that filing's own (possibly
    # shorter) standalone T_run.
    t_run_joint = dt * num_steps

    combined: list = []
    masks_by_id: dict[int, Any] = {}
    mask_id_per_sat: list[int] = []
    system_id_per_sat: list[int] = []
    next_gid = 1
    for idx, cfg in enumerate(cfgs):
        # Per-filing emitter band filter (restrict_emitters_to_sim_band).
        const, local_ids = create_constellation_for_config(cfg)
        if not const:
            continue
        # Fold this system's own orbit dynamics into ITS satellites only
        # (see _apply_orbit_dynamics — run_epfd_simulation's raan_dot_*/
        # wdelta_deg scalars would apply ONE filing's rate to everyone).
        const = _apply_orbit_dynamics(cfg, const, t_run_joint)
        combined.extend(const)
        # This filing's own per-satellite mask routing (mask_lnk1, same rule
        # as method_1 — see _build_mask_for_sats), remapped onto ids GLOBAL
        # to the fused constellation so filings never collide in masks_by_id.
        # Satellites without a mask_lnk1 row (-1) resolve HERE to this
        # filing's own primary mask: left as -1, the fused PFDMaskMulti's
        # fallback would route them to the GLOBAL primary — possibly a
        # foreign system's mask.
        mask_obj = _build_mask_for_sats(cfg, local_ids)
        if isinstance(mask_obj, PFDMaskMulti):
            gid_of = {lid: next_gid + k
                      for k, lid in enumerate(sorted(mask_obj.masks_by_id))}
            for lid, gid in gid_of.items():
                masks_by_id[gid] = mask_obj.masks_by_id[lid]
            next_gid += len(gid_of)
            fallback_gid = gid_of[int(mask_obj.primary_mask_id)]
            mask_id_per_sat.extend(
                gid_of.get(int(m), fallback_gid) for m in local_ids
            )
        else:
            masks_by_id[next_gid] = mask_obj
            mask_id_per_sat.extend([next_gid] * len(const))
            next_gid += 1
        system_id_per_sat.extend([idx] * len(const))

    if not combined:
        raise RuntimeError("method_3: no valid constellation")

    pfd_multi = PFDMaskMulti(masks_by_id=masks_by_id, mask_id_per_sat=mask_id_per_sat)

    # MAX_CO_FREQ (Steps 19-22), per filing — applied PARTITIONED by system
    # (below, via system_id_per_sat) so each constellation's own SRS sat_oper
    # cap is respected instead of silently becoming "unlimited" (see
    # _s1503_normative_caps docstring). caps0 covers the OR/exclusion-zone
    # gates, which are aggregate-wide non-normative extensions (not exposed
    # per filing in the UI), same convention as alpha0/es_antenna above.
    caps0 = _s1503_normative_caps(cfgs[0])
    per_system_nco = [_s1503_normative_caps(c)["max_co_freq_by_lat"] for c in cfgs]
    # PFD->EPFD RefBW correction: Article-22 row (shared limit config, so
    # cfgs[0] serves) vs the fused mask's refbw_khz — which PFDMaskMulti
    # already reduces to the PRIMARY sub-mask's RefBW, the engine's own
    # convention. Sub-masks with differing RefBW keep the primary's (same
    # limitation as method_1's engine assembly).
    bw_correction_db = _bw_correction_db(cfgs[0], pfd_multi)

    # Dual time step (§D4.7) for the joint pass. The aggregate worker used to
    # write dual_time_step_mode into the cfg and then never build a
    # DualTimeStep — the joint run always used a single fine step, so a
    # requested coarse step was silently ignored and every one of the N steps
    # was fine. Built only when a coarse ladder actually exists (Ncoarse > 1)
    # and the mode is not 'off'. α₀ for the gain threshold is the SMALLEST
    # across filings: in a fused run each system has its own α₀ (see
    # per_system_alpha0), and the smallest one yields the widest critical
    # region — i.e. fine steps wherever ANY system needs them, which is the
    # conservative choice for a sampling decision.
    _dual_mode = str(_sim0.get("dual_time_step_mode") or "s1503").strip().lower()
    dual_ts = None
    if _dual_mode != "off" and ncoarse > 1:
        dual_ts = DualTimeStep(
            coarse_step_s=coarse, fine_step_s=dt, mode="s1503_gain",
            ncoarse=ncoarse, es_antenna=es_antenna,
            alpha0_deg=(min(per_system_alpha0) if per_system_alpha0 else alpha0),
            disable_or_condition=caps0["strict_exclusion_zone"],
        )
        _emit(f"[method_3] dual time step (§D4.7) active: fine={dt:.6f}s · "
              f"coarse={coarse:.6f}s · Ncoarse={ncoarse}")

    # geometry: manual or joint WCGA
    if (params.get("geometry_es_lat") is not None and
            params.get("geometry_es_lon") is not None and
            params.get("geometry_gso_lon") is not None):
        gp = GeometryPoint(
            es_lat_deg=float(params["geometry_es_lat"]),
            es_lon_deg=float(params["geometry_es_lon"]),
            gso_lon_deg=float(params["geometry_gso_lon"]),
        )
        wcg = geometry_to_wcg_result(gp)
        _emit(f"[method_3] manual geometry: ES({gp.es_lat_deg},{gp.es_lon_deg}) GSO {gp.gso_lon_deg}")
        _emit_progress(45)
    else:
        _emit("[method_3] joint WCGA on combined constellation (S.1503-4 §D.3.1)")
        _emit_progress(20)
        # Iterate unique orbits of the megaconstellation (key = a, e, i, mask_id)
        unique_orbits: dict[tuple, int] = {}
        for idx, oe in enumerate(combined):
            key = (round(getattr(oe, "a_km", 0.0), 3),
                   round(getattr(oe, "e", 0.0), 6),
                   round(getattr(oe, "i_rad", 0.0), 6),
                   int(mask_id_per_sat[idx]))
            if key not in unique_orbits:
                unique_orbits[key] = idx
        _emit(f"[method_3] {len(unique_orbits)} unique orbit(s) to evaluate")
        step_deg = float(common.get("s1503_step_deg") or 1.0)
        # -1 = all cores (matches run_wcg_downlink's own default, src/main.py).
        # Safe here: the orbit loop below is sequential — nothing else runs
        # concurrently to oversubscribe against (unlike _run_at_geometry /
        # _system_contribution_task, dispatched many-at-once via Ray, which
        # correctly keep n_jobs=1 per task).
        n_jobs = int(cfgs[0]["simulation"].get("n_jobs", -1) or -1)
        best_wcg = None
        for k, (key, ref_idx) in enumerate(unique_orbits.items()):
            oe_ref = combined[ref_idx]
            ref_mask = pfd_multi.mask_for_sat(ref_idx)
            ref_sid = int(system_id_per_sat[ref_idx])
            ref_nco = per_system_nco[ref_sid]
            _emit_progress(20 + 25.0 * (k + 1) / max(1, len(unique_orbits)))
            try:
                wcg_i = search_wcg_s1503(
                    oe_ref=oe_ref, t_s=0.0,
                    pfd_mask=ref_mask, es_antenna=es_antenna,
                    # Owning filing's own ε₀/α₀ — same values the joint
                    # simulation applies to this orbit's satellites.
                    alpha0_deg=per_system_alpha0[ref_sid],
                    min_elevation_deg=per_system_eps0[ref_sid],
                    gso_min_elevation_deg=caps0["gso_min_elevation_deg"],
                    step_size_deg=step_deg, n_jobs=n_jobs,
                    orbit_idx=k, total_orbits=len(unique_orbits),
                    max_co_freq_by_lat=ref_nco,
                    strict_exclusion_zone=caps0["strict_exclusion_zone"],
                )
            except Exception as exc:  # noqa: BLE001
                _emit(f"WARN: WCGA orbit {k}: {exc}")
                continue
            if wcg_i is None:
                continue
            if best_wcg is None or wcg_i.epfd_dBW > best_wcg.epfd_dBW:
                best_wcg = wcg_i
        if best_wcg is None:
            raise RuntimeError("method_3: joint WCGA produced no valid result")
        wcg = best_wcg
        _emit(f"[method_3] WCG_agg: ES({wcg.es_lat_deg:.2f},{wcg.es_lon_deg:.2f}) GSO {wcg.gso_lon_deg:.2f}")
        _emit_progress(45)

    # Persist the joint WCG the moment it is known — it is the headline geometry
    # and it cost a full WCGA to find; no reason to make it wait hours for the
    # simulation to end (or vanish with it on a cancel).
    result_path = Path(params["result_path"])
    _write_partial_geometry(result_path, wcg)
    on_chunk = _partial_snapshot_writer(result_path, wcg, cfgs, system_id_per_sat)

    _emit("[method_3] joint EPFD↓ simulation")
    _emit_progress(50)
    # MAX_CO_FREQ (Steps 19-22) applied PER SYSTEM on the fused constellation
    # (caps0 / per_system_nco computed above, right after fusing combined) —
    # each filing's own SRS sat_oper cap, partitioned by system_id_per_sat
    # (see _s1503_normative_caps / _finalize_epfd_after_max_co_freq). Without
    # this, the joint sim treated Nco as unlimited (method_3 previously
    # dropped it entirely), inflating the peak vs. method_1's per-system cap.
    # raan_dot_artificial_rad_s / raan_dot_override_rad_s / wdelta_deg are
    # deliberately left at their 0/None/0 defaults below: each system's own
    # orbit dynamics (§D6.3) are already folded into `combined`'s elements
    # by _apply_orbit_dynamics above — a scalar here would apply ONE
    # filing's precession to the whole fused constellation.
    sim_res = run_epfd_simulation(
        constellation=combined,
        wcg=wcg,
        pfd_mask=pfd_multi,
        es_antenna=es_antenna,
        alpha0_deg=alpha0,
        min_elevation_deg=min_elev,
        tstep_s=dt,
        nsteps=num_steps,
        dual_ts=dual_ts,
        # -1 = all cores (matches run_wcg_downlink's default) — this is the
        # one joint simulation for the whole run, nothing else contends for
        # cores at this point.
        n_jobs=int(cfgs[0]["simulation"].get("n_jobs", -1) or -1),
        strict_max_co_freq_total=caps0["strict_max_co_freq_total"],
        strict_exclusion_zone=caps0["strict_exclusion_zone"],
        min_angle_at_es_deg=caps0["min_angle_at_es_deg"],
        gso_min_elevation_deg=caps0["gso_min_elevation_deg"],
        pfd_bw_correction_db=bw_correction_db,
        system_id_per_sat=np.asarray(system_id_per_sat, dtype=np.int64),
        max_co_freq_by_lat_per_system=per_system_nco,
        # Step-18 eligibility partitioned per system: each filing's own ε₀/α₀
        # gate its own satellites (method_1's criterion, kept in the fusion).
        min_elevation_deg_per_system=per_system_eps0,
        alpha0_deg_per_system=per_system_alpha0,
        # Snapshot CCDF / time series / per-system curves into partial/ as chunks
        # land, so a cancelled run still leaves something to show.
        on_chunk=on_chunk,
    )
    sim_res.build_cdf()
    _emit_progress(82)

    joint_bins = list(map(float, sim_res.cdf_epfd_dBW))
    joint_pct = list(map(float, sim_res.cdf_percentage))

    # Per-system decomposition AT THE JOINT WCG: each filing's own EPFD
    # contribution (time series + CCDF), evaluated with the EXACT SAME
    # geometry, time base, ES antenna and gates as the joint run above — the
    # linear-power sum of these curves reproduces the joint result at any
    # instant/percentile. Different from post_sum below, which uses each
    # filing's OWN independent WCG/antenna/timeline.
    #
    # Read straight off the joint run: `run_epfd_simulation` accumulated one
    # sub-accumulator per system_id in the SAME pass (see
    # epfd_calculator._acc_add_per_system), so no extra simulation is needed.
    # `_decompose_by_resimulation` is the FALLBACK for engine paths that don't
    # carry the per-system split (e.g. a dual-time-step joint run) — it
    # re-simulates every system over the full joint step count, which costs
    # about as much as the joint run itself.
    per_system_at_wcg = _decompose_from_joint_acc(
        getattr(sim_res, "acc", None), cfgs, system_id_per_sat,
    )
    if per_system_at_wcg is not None:
        _emit(f"[method_3] per-system decomposition: {len(per_system_at_wcg)} system(s) "
              "read from the joint pass (no re-simulation)")
    else:
        _emit("[method_3] joint pass carried no per-system split; falling back to "
              "one simulation per system")
        per_system_at_wcg = _decompose_by_resimulation(
            filings=filings, cfgs=cfgs, common=common, wcg=wcg,
            dt=dt, num_steps=num_steps, alpha0=alpha0, min_elev=min_elev,
            caps0=caps0, per_system_nco=per_system_nco,
            bw_correction_db=bw_correction_db,
            per_system_eps0=per_system_eps0,
            per_system_alpha0=per_system_alpha0,
        )
    _emit_progress(88)

    # post_sum: convolution of single-system CCDFs (as contrast). OFF by default:
    # it is NOT a decomposition of the joint run (each filing gets its OWN WCG,
    # antenna and timeline), so it costs a further full pipeline per filing —
    # roughly doubling the run — for a comparison curve. Opt in per run.
    per_system: list = []
    if not common.get("method3_post_sum"):
        _emit("[method_3] post_sum skipped (set method3_post_sum to compute the "
              "independent-WCG convolution complement)")
    else:
        _emit("[method_3] post_sum complement (per-system convolution)")
        post_tasks = [(f, common) for f in filings]
        post_costs = plan.filing_costs(filings, common, sim=True, wcga=True)
        try:
            per_system = cluster.parallel_starmap_progress(
                _single_filing_task, post_tasks,
                on_done=_progress_cb(88.0, 96.0),
                costs=post_costs,
            )
        except Exception as exc:  # noqa: BLE001
            _emit(f"WARN: parallel post_sum failed ({exc}); falling back sequential")
            per_system = []
            for idx, filing in enumerate(filings):
                try:
                    per_system.append(_run_single_filing(filing, common))
                except Exception as e2:  # noqa: BLE001
                    _emit(f"WARN: per-system fallback {idx}: {e2}")
                    per_system.append({"error": str(e2)})

    post_bins: list[float] = []
    post_pct: list[float] = []
    ccdfs_in = [(r["ccdf_bins_db"], r["ccdf_pct"])
                for r in per_system if r.get("ccdf_bins_db")]
    post_floor = _truncation_floor(params, ccdfs_in)
    if len(ccdfs_in) >= 2:
        post_bins, post_pct = _convolve(ccdfs_in, post_floor)

    # NOTE: truncation only applies to the convolved post_sum complement —
    # the joint (headline) CCDF comes from a single simulation, untruncated.
    post_sum: dict[str, Any] = {
        "ccdf_bins_db": post_bins,
        "ccdf_pct": post_pct,
        "max_epfd_dbw": post_bins[0] if post_bins else None,
        "percentiles": _percentiles(post_bins, post_pct, post_floor)
        if post_bins else {},
    }
    if post_floor is not None and post_bins:
        post_sum["truncation_floor_pct"] = float(post_floor)

    acc = getattr(sim_res, "acc", None)
    return {
        "method": "method_3",
        "geometry": {
            "es_lat_deg": float(wcg.es_lat_deg),
            "es_lon_deg": float(wcg.es_lon_deg),
            "gso_lon_deg": float(wcg.gso_lon_deg),
        },
        "ccdf_bins_db": joint_bins,
        "ccdf_pct": joint_pct,
        "max_epfd_dbw_m2_40khz": joint_bins[0] if joint_bins else None,
        "percentiles": _percentiles(joint_bins, joint_pct) if joint_bins else {},
        "post_sum": post_sum,
        "per_system": per_system,
        "per_system_at_wcg": per_system_at_wcg,
        "n_systems": len(filings),
        # Joint megaconstellation timeline (one shared N / Δt ladder).
        "dual_time_step": _dual_time_step_block(
            cfgs[0].get("simulation") or {}, acc,
            fine_step_s=dt, coarse_step_s=coarse, ncoarse=ncoarse,
            num_time_steps=num_steps,
        ),
    }


# ─── method_4: per-WCG sweep + convolution ──────────────────────────────────


def _run_method_4(params: dict[str, Any]) -> dict[str, Any]:
    """For each filing's WCG g_i, simulate all N systems at g_i and convolve."""
    filings = params["filings"]
    common = {k: v for k, v in params.items() if k not in ("filings", "method", "result_path")}

    info = cluster.ensure_init()
    _emit(f"[method_4] {len(filings)} filing(s)"
          + (" · ray active" if info.get("active") else " · sequential"))

    # 1) Compute each filing's WCG (parallel)
    wcg_tasks = [(f, common) for f in filings]
    wcg_costs = plan.filing_costs(filings, common, sim=True, wcga=True)
    wcg_results = cluster.parallel_starmap_progress(
        _single_filing_task, wcg_tasks,
        on_done=_progress_cb(2.0, 30.0),
        costs=wcg_costs,
    )
    wcgs = [{"index": i, **(r.get("wcg") or {})} for i, r in enumerate(wcg_results)]
    # Per-filing §D4 time base from the WCG phase (full single-entry pipeline).
    per_system_tb = [
        {
            "system_index": i,
            "wcg": r.get("wcg"),
            "n_satellites": r.get("n_satellites"),
            "dual_time_step": r.get("dual_time_step"),
            "max_epfd_dbw": r.get("max_epfd_dbw"),
        }
        for i, r in enumerate(wcg_results)
        if isinstance(r.get("dual_time_step"), dict) or r.get("wcg")
    ]

    # 2) Build cross (WCG g_i × filing j) task list
    sim_fc = plan.filing_costs(filings, common, sim=True, wcga=False)
    sim_tasks: list[tuple] = []
    keys: list[tuple[int, int]] = []  # (wi, fj)
    sim_costs: list[float] = []
    valid_wcg = [(wi, w) for wi, w in enumerate(wcgs) if w.get("es_lat_deg") is not None]
    for wi, w in valid_wcg:
        lat = float(w["es_lat_deg"]); lon = float(w["es_lon_deg"])
        glon = float(w["gso_lon_deg"])
        for fj, filing in enumerate(filings):
            sim_tasks.append((filing, common, lat, lon, glon))
            keys.append((wi, fj))
            sim_costs.append(sim_fc[fj])

    sim_results = cluster.parallel_starmap_progress(
        _at_geometry_task, sim_tasks,
        on_done=_progress_cb(30.0, 95.0),
        costs=sim_costs,
    )

    by_wi: dict[int, list[tuple[int, dict[str, Any]]]] = {}
    for k, r in enumerate(sim_results):
        wi, fj = keys[k]
        by_wi.setdefault(wi, []).append((fj, r))

    per_wcg: list[dict[str, Any]] = []
    for wi, w in valid_wcg:
        per_sys_ccdfs = [
            (r["ccdf_bins_db"], r["ccdf_pct"])
            for _fj, r in sorted(by_wi.get(wi, []), key=lambda x: x[0])
            if r["ccdf_bins_db"]
        ]
        if per_sys_ccdfs:
            floor_i = _truncation_floor(params, per_sys_ccdfs)
            bins, pct = _convolve(per_sys_ccdfs, floor_i)
            entry = {
                "wcg_index": wi,
                "es_lat_deg": float(w["es_lat_deg"]),
                "es_lon_deg": float(w["es_lon_deg"]),
                "gso_lon_deg": float(w["gso_lon_deg"]),
                "ccdf_bins_db": bins, "ccdf_pct": pct,
                "max_epfd_dbw": bins[0] if bins else None,
                "percentiles": _percentiles(bins, pct, floor_i) if bins else {},
            }
            if floor_i is not None:
                entry["truncation_floor_pct"] = float(floor_i)
            per_wcg.append(entry)

    # worst-of WCGs (highest max_epfd)
    if per_wcg:
        worst = max(per_wcg, key=lambda p: p["max_epfd_dbw"] or float("-inf"))
        bins = worst["ccdf_bins_db"]
        pct = worst["ccdf_pct"]
        worst_floor = worst.get("truncation_floor_pct")
    else:
        bins = []
        pct = []
        worst_floor = None

    out = {
        "method": "method_4",
        "n_systems": len(filings),
        "per_system": per_system_tb,
        "per_wcg": per_wcg,
        "ccdf_bins_db": bins,
        "ccdf_pct": pct,
        "max_epfd_dbw_m2_40khz": bins[0] if bins else None,
        "percentiles": _percentiles(bins, pct, worst_floor) if bins else {},
    }
    if worst_floor is not None:
        out["truncation_floor_pct"] = float(worst_floor)
    return out


# ─── method_5: WP-4A Step-1 view of the same grid convolution ──────────────


def _run_method_5(params: dict[str, Any]) -> dict[str, Any]:
    """WP-4A Step-1 reading of the common-grid convolution. Identical computation
    and output to method_2 — the full curve set (per-system + per-point
    convolution + envelope) is produced once and kept (see
    :func:`_run_grid_convolution`); only the ``method`` label differs."""
    return _run_grid_convolution(params, "method_5")


# ─── entrypoint ──────────────────────────────────────────────────────────────


def _run(params: dict[str, Any]) -> dict[str, Any]:
    import time as _time
    from datetime import datetime as _dt, timezone as _tz
    _t0 = _time.perf_counter()
    _started_at = _dt.now(_tz.utc)

    method = params.get("method", "method_1")
    result_path = Path(params["result_path"])
    result_path.mkdir(parents=True, exist_ok=True)

    # Initialize Ray once for the whole job — pass runtime_env so the
    # uploads/ directory is shipped to every remote node. ensure_init is
    # idempotent, so subsequent parallel_starmap_progress calls inherit
    # the same connection.
    rt_env = cluster.uploads_runtime_env()
    init_info = cluster.ensure_init(runtime_env=rt_env)
    if init_info.get("active"):
        wd = (rt_env or {}).get("working_dir")
        if wd:
            _emit(f"Ray active · working_dir shipped: {wd}")
        else:
            _emit(f"Ray active · mode={init_info.get('mode')} (no working_dir)")
    elif init_info.get("error"):
        _emit(f"Ray inactive ({init_info['error']}); falling back to sequential")

    # Inner parallelism for fanned-out tasks (see _task_n_jobs). Decided ONCE
    # here — ensure_init is the single authority on whether Ray is active — and
    # carried in params, so every method's `common` inherits it. Without this
    # the standalone path ran each task single-process AND one at a time.
    params["task_n_jobs"] = 1 if init_info.get("active") else -1
    _emit(
        f"Task inner parallelism: n_jobs={params['task_n_jobs']} "
        + ("(Ray fan-out: one reserved slot per task)" if init_info.get("active")
           else "(sequential fallback: all cores per task)")
    )

    # method_3 is the one method whose dominant work happens in THIS process:
    # one joint WCGA + one joint EPFD↓ simulation over the fused
    # megaconstellation. The other methods are many independent simulations
    # that `cluster.parallel_starmap_progress` already fans out over Ray, so
    # their driver does no heavy compute. Inject the cluster executors for the
    # two driver-side sweeps (WCGA latitude sweep, EPFD time-chunk sweep) —
    # otherwise method_3 stays capped to this node's cores via the engine's own
    # multiprocessing.Pool, however large the cluster. Parity-preserving: same
    # work units and merges, only the executor differs. No-op when standalone.
    # Inert for the Ray-dispatched side phases (post_sum, per-system
    # re-simulation): those run in worker processes, where the engine's
    # module-level hook is unset, so no task nests another fan-out.
    _distributed = False
    if method == "method_3":
        try:
            rt_env_m3 = cluster.uploads_runtime_env()
            wcga_on = wcga_cluster.enable(
                runtime_env=rt_env_m3,
                on_progress=lambda i, n: _emit_progress(20 + 25.0 * i / max(1, n)),
            )
            epfd_on = epfd_cluster.enable(
                runtime_env=rt_env_m3,
                on_progress=lambda i, n: _emit_progress(50 + 32.0 * i / max(1, n)),
            )
            if wcga_on or epfd_on:
                _distributed = True
                _emit("[method_3] distributed across Ray cluster: "
                      f"WCGA={'on' if wcga_on else 'off'} · "
                      f"EPFD={'on' if epfd_on else 'off'}")
        except Exception as exc:  # noqa: BLE001 — local compute is the fallback
            _emit(f"WARN: cluster dispatch unavailable ({exc}); local compute")

    try:
        if method == "method_1":
            out = _run_method_1(params)
        elif method == "method_2":
            out = _run_method_2(params)
        elif method == "method_3":
            out = _run_method_3(params)
        elif method == "method_4":
            out = _run_method_4(params)
        elif method == "method_5":
            out = _run_method_5(params)
        else:
            raise ValueError(f"unknown method: {method}")
    finally:
        if _distributed:
            wcga_cluster.disable()
            epfd_cluster.disable()

    # Attach Article 22 / Resolution 76 limits from the first filing config
    # (Res. 76 assumes common ES diameter + ref BW across systems).
    filings = params.get("filings") or []
    if filings:
        try:
            ref_cfg = _load_cfg(filings[0], {
                k: v for k, v in params.items() if k not in ("filings", "method", "result_path")
            })
            art22 = ref_cfg.get("article22_limits") or {}
            res76 = ref_cfg.get("resolution76_limits") or {}
            if art22:
                _a22 = {k: art22[k] for k in art22 if k != "_full_table"}
                # Pin the full normative scenario (shared across the aggregate).
                _a22["service"] = str(ref_cfg.get("gso_es", {}).get("service", "FSS")).upper()
                _fr = ref_cfg.get("non_gso", {}).get("frequency_ghz")
                if _fr is not None:
                    _a22["frequency_run_ghz"] = float(_fr)
                    _a22["frequency_run_mhz"] = float(_fr) * 1000.0
                out["article22"] = _a22
            if res76:
                out["resolution76"] = {k: res76[k] for k in res76 if k != "_full_table"}
        except Exception as exc:  # noqa: BLE001
            _emit(f"WARN: could not attach Art22/Res76 limits: {exc}")

    hardware = hwinfo.run_hardware()
    timing = hwinfo.run_timing(_started_at, _t0)
    out["hardware"] = hardware
    out["timing"] = timing
    (result_path / "sim_data.json").write_text(json.dumps(out, indent=2), encoding="utf-8")

    # Result artifacts (R10–R12): geometry CSV + map of the tested points and
    # the aggregate CCDF as CSV/PNG. Best-effort — never fails the run.
    try:
        from streamlit_app.lib.result_artifacts import write_run_artifacts  # noqa: PLC0415
        _written = write_run_artifacts(result_path, out, acc=None)
        if _written:
            _emit("Artifacts: " + ", ".join(_written))
    except Exception as exc:  # noqa: BLE001
        _emit(f"WARN: artifact export failed: {exc}")
    summary = {
        "method": out.get("method"),
        "max_epfd_dbw_m2_40khz": out.get("max_epfd_dbw_m2_40khz"),
        "percentiles": out.get("percentiles", {}),
        "n_systems": out.get("n_systems", len(params.get("filings", []))),
        "hardware": hardware,
        "timing": timing,
    }
    # UI contract: expose the truncation floor whenever tail truncation was
    # applied — the Results page renders percentiles below it (None/null)
    # as "below truncation floor".
    if out.get("truncation_floor_pct") is not None:
        summary["truncation_floor_pct"] = float(out["truncation_floor_pct"])
    (result_path / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _emit_progress(100)
    _emit("DONE")
    return summary


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: s1588_worker <params.json>", file=sys.stderr)
        return 2
    params_path = Path(sys.argv[1])
    if not params_path.exists():
        print(f"params not found: {params_path}", file=sys.stderr)
        return 2
    params = json.loads(params_path.read_text(encoding="utf-8"))
    from datetime import datetime, timezone
    try:
        _run(params)
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"FINISHED_AT:{datetime.now(timezone.utc).isoformat(timespec='seconds')}", flush=True)
        print(f"ERROR:{type(exc).__name__}:{exc}", flush=True)
        return 1
    print(f"FINISHED_AT:{datetime.now(timezone.utc).isoformat(timespec='seconds')}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
