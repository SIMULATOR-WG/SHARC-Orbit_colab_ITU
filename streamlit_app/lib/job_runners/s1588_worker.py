"""S.1588 multi-system worker (Resolution 76 — Studies 1, 2, 3).

Usage:
    python -m streamlit_app.lib.job_runners.s1588_worker <params.json>

params.json fields:
    result_path           — directory to write artifacts into
    method                — "method_1" | "method_2" | "method_3"
    filings               — list of {srs_path, mask_path?, mask_id?, ntc_id?}
    num_time_steps        — default 3 600 (1 h @ 1 s — conservative for runtime)
    time_step_s           — default 1.0
    min_elevation_deg     — default 10.0
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
import os
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve()
REPO_ROOT = HERE.parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from streamlit_app.lib import cluster, hwinfo, plan  # noqa: E402


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
    sim["num_time_steps"] = int(common.get("num_time_steps", 3_600))
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
    if "min_elevation_deg" in common:
        cfg.setdefault("non_gso", {})["min_elevation_deg"] = float(common["min_elevation_deg"])
    if common.get("es_antenna_diameter_m"):
        cfg.setdefault("gso_es", {})["antenna_diameter_m"] = float(common["es_antenna_diameter_m"])

    wcg_cfg = cfg.setdefault("wcg_search", {})
    # Default to S.1503-4 §D.3.1 normative algorithm.
    wcg_cfg["use_s1503_algo"] = bool(common.get("wcga_s1503", True))
    if common.get("wcga_no_mask_symmetry"):
        wcg_cfg["s1503_symmetric_mask"] = False
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


def _build_antenna(cfg: dict[str, Any]):
    from src.antenna import create_gso_es_antenna  # type: ignore[import]
    gso_es = cfg["gso_es"]
    return create_gso_es_antenna(
        float(gso_es["antenna_diameter_m"]),
        float(cfg["non_gso"]["frequency_ghz"]),
        float(gso_es.get("antenna_efficiency", 0.99)),
        service=str(gso_es.get("service", "FSS")).upper(),
    )


def _run_single_filing(filing: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    """Run a complete single-system S.1503 pipeline (WCGA + EPFD↓ + compliance)."""
    from src.main import run_wcg_downlink  # type: ignore[import]
    from src.exceptions import NoValidGeometry  # type: ignore[import]
    cfg = _load_cfg(filing, common)
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
    }


def _run_at_geometry(cfg: dict[str, Any], gp, common: dict[str, Any]) -> dict[str, Any]:
    """Run S.1503 for one filing at a fixed geometry (no WCG search)."""
    from src.main import create_constellation_for_config  # type: ignore[import]
    from src.s1588_studies import run_epfd_at_geometry  # type: ignore[import]

    import math as _math

    # Honour restrict_emitters_to_sim_band (default ON in _load_cfg).
    constellation, _mask_ids = create_constellation_for_config(cfg)
    mask = _build_mask(cfg)
    antenna = _build_antenna(cfg)

    sim = cfg["simulation"]
    ngso = cfg["non_gso"]
    nsteps = int(sim["num_time_steps"])
    tstep = float(sim.get("coarse_time_step_s", 1.0))
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
        min_elevation_deg=float(cfg["non_gso"].get("min_elevation_deg",
                                                     common.get("min_elevation_deg", 10.0))),
        n_jobs=int(cfg["simulation"].get("n_jobs", 1)),
        raan_dot_artificial_rad_s=raan_dot_artificial,
        raan_dot_override_rad_s=raan_dot_override,
        wdelta_deg=wdelta_deg,
        t_run_s=t_run_s,
    )
    try:
        sim_dl.build_cdf()
    except Exception:  # noqa: BLE001
        pass
    return {
        "ccdf_bins_db": list(map(float, sim_dl.cdf_epfd_dBW)),
        "ccdf_pct": list(map(float, sim_dl.cdf_percentage)),
        "max_epfd_dbw": float(sim_dl.cdf_epfd_dBW[0]) if len(sim_dl.cdf_epfd_dBW) else None,
        "n_satellites": int(len(constellation) or 0),
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
    min_elev = float(params.get("min_elevation_deg", 10.0))
    country_codes = list(params.get("country_codes") or []) or None

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


def _run_method_3(params: dict[str, Any]) -> dict[str, Any]:
    """Joint simulation (Method 2B): fused megaconstellation, single sim run."""
    from src.main import create_constellation_for_config  # type: ignore[import]
    from src.pfd_mask import PFDMaskMulti  # type: ignore[import]
    from src.epfd_calculator import run_epfd_simulation  # type: ignore[import]
    from src.wcg_search import search_wcg_s1503  # type: ignore[import]
    from src.s1588_studies import geometry_to_wcg_result  # type: ignore[import]
    from src.s1588_studies.geometry import GeometryPoint  # type: ignore[import]

    filings = params["filings"]
    common = {k: v for k, v in params.items() if k not in ("filings", "method", "result_path")}
    num_steps = int(common.get("num_time_steps", 3_600))
    dt = float(common.get("time_step_s", 1.0))
    min_elev = float(common.get("min_elevation_deg", 10.0))

    cfgs = [_load_cfg(f, common) for f in filings]

    _emit("[method_3] fusing constellations into megaconstellation")
    _emit_progress(10)

    combined: list = []
    masks_by_id: dict[int, Any] = {}
    mask_id_per_sat: list[int] = []
    for idx, cfg in enumerate(cfgs):
        # Per-filing emitter band filter (restrict_emitters_to_sim_band).
        const, _ = create_constellation_for_config(cfg)
        if not const:
            continue
        combined.extend(const)
        mask_obj = _build_mask(cfg)
        global_id = idx + 1
        masks_by_id[global_id] = mask_obj
        mask_id_per_sat.extend([global_id] * len(const))

    if not combined:
        raise RuntimeError("method_3: no valid constellation")

    pfd_multi = PFDMaskMulti(masks_by_id=masks_by_id, mask_id_per_sat=mask_id_per_sat)
    es_antenna = _build_antenna(cfgs[0])
    alpha0 = float(cfgs[0]["non_gso"].get("alpha0_deg", 0.0))

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
        n_jobs = int(cfgs[0]["simulation"].get("n_jobs", 1) or 1)
        best_wcg = None
        for k, (key, ref_idx) in enumerate(unique_orbits.items()):
            oe_ref = combined[ref_idx]
            ref_mask = pfd_multi.mask_for_sat(ref_idx)
            _emit_progress(20 + 25.0 * (k + 1) / max(1, len(unique_orbits)))
            try:
                wcg_i = search_wcg_s1503(
                    oe_ref=oe_ref, t_s=0.0,
                    pfd_mask=ref_mask, es_antenna=es_antenna,
                    alpha0_deg=alpha0, min_elevation_deg=min_elev,
                    step_size_deg=step_deg, n_jobs=n_jobs,
                    orbit_idx=k, total_orbits=len(unique_orbits),
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

    _emit("[method_3] joint EPFD↓ simulation")
    _emit_progress(50)
    sim_res = run_epfd_simulation(
        constellation=combined,
        wcg=wcg,
        pfd_mask=pfd_multi,
        es_antenna=es_antenna,
        alpha0_deg=alpha0,
        min_elevation_deg=min_elev,
        tstep_s=dt,
        nsteps=num_steps,
        n_jobs=int(cfgs[0]["simulation"].get("n_jobs", 1)),
    )
    sim_res.build_cdf()
    _emit_progress(82)

    joint_bins = list(map(float, sim_res.cdf_epfd_dBW))
    joint_pct = list(map(float, sim_res.cdf_percentage))

    # post_sum: convolution of single-system CCDFs (as contrast)
    _emit("[method_3] post_sum complement (per-system convolution)")
    _emit_progress(85)
    post_tasks = [(f, common) for f in filings]
    post_costs = plan.filing_costs(filings, common, sim=True, wcga=True)
    try:
        per_system = cluster.parallel_starmap_progress(
            _single_filing_task, post_tasks,
            on_done=_progress_cb(85.0, 95.0),
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
        "n_systems": len(filings),
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
