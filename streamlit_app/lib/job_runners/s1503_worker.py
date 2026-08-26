"""S.1503 single-system worker.

Usage:
    python -m streamlit_app.lib.job_runners.s1503_worker <params.json>

params.json fields (mandatory):
    result_path           — directory to write artifacts into
    srs_path              — path to the SRS .mdb / .accdb / .xml
    mask_path             — path to the PFD mask (.xml or .mdb); may be null if
                            the SRS already carries the mask
    mask_id               — optional integer (required for .mdb mask)
    ntc_id                — optional ITU notice id

params.json fields (optional simulation overrides):
    num_time_steps        — default 86 400 (1 sidereal day @ 1 s)
    time_step_s           — default 1.0
    min_elevation_deg     — default 10.0
    service               — default "FSS"
    es_antenna_diameter_m — overrides config gso_es.antenna_diameter_m
    keep_full_history     — default False (legacy mode, costly)
    alpha_table           — declared {"min": [[deg, cdf], …], "max": […]} pairs
                            for selection_strategy="alpha_table" (Doc 4A/312)
    alpha_table_file      — name of the file those pairs came from. Provenance
                            only; the engine runs off the inline pairs above.
    alpha_bin_deg         — NON-NORMATIVE α sub-bin width inside each declared
                            TSS case. Default 0.0 = the normative declared
                            intervals of Doc 4A/312 p. 110.
    max_co_freq           — overrides MAX_CO_FREQ (Steps 19-22 cap / Nco) for
                            all latitudes; 0 = unlimited. Default: from the
                            filing's sat_oper table.

Outputs (written under result_path):
    sim_data.json         — CCDF, time series, max EPFD, percentiles, WCG,
                            hardware, timing (start/end/duration)
    summary.json          — quick metrics (compliance, max EPFD, percentiles),
                            hardware, timing (start/end/duration)
    PROGRESS lines on stdout (`PROGRESS:<float>`)
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve()
REPO_ROOT = HERE.parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from streamlit_app.lib import hwinfo  # noqa: E402


def _emit_progress(pct: float) -> None:
    print(f"PROGRESS:{max(0.0, min(100.0, pct)):.2f}", flush=True)


def _emit(line: str) -> None:
    print(line, flush=True)


def _extract_percentiles(bins_desc, pct_asc) -> dict[str, float]:
    from src.s1588_studies import NORMATIVE_PERCENTAGES, extract_percentiles  # type: ignore[import]

    pct_for_extract = [float(p) / 100.0 for p in pct_asc]
    out = extract_percentiles(
        list(reversed(list(bins_desc))),
        list(reversed(pct_for_extract)),
        NORMATIVE_PERCENTAGES,
    )
    return {f"{p}%": float(v) for p, v in out.items()}


def _run(params: dict[str, Any]) -> dict[str, Any]:
    import time as _time
    from datetime import datetime as _dt, timezone as _tz
    _t0 = _time.perf_counter()
    _started_at = _dt.now(_tz.utc)

    from src.main import load_from_srs  # type: ignore[import]
    from src.main import run_wcg_downlink  # type: ignore[import]
    from src.main import apply_max_co_freq_override_to_non_gso  # type: ignore[import]
    from src.exceptions import NoValidGeometry  # type: ignore[import]
    from src.article22_tables import apply_article22_limits_to_config  # type: ignore[import]
    from src.resolution76_tables import apply_resolution76_limits_to_config  # type: ignore[import]

    result_path = Path(params["result_path"])
    result_path.mkdir(parents=True, exist_ok=True)

    srs_path = params.get("srs_path")
    mask_path = params.get("mask_path")
    mask_id = params.get("mask_id")
    ntc_id = params.get("ntc_id")
    service = params.get("service", "FSS")
    # User-selected frequency run (Art. 22 scenario / manual field). Passed to
    # load_from_srs so the default mask / group resolution follows the
    # sub-band being simulated instead of blind mask_lnk1 precedence.
    try:
        sim_freq_ghz = (float(params["simulation_frequency_ghz"])
                        if params.get("simulation_frequency_ghz") is not None
                        else None)
    except (TypeError, ValueError):
        sim_freq_ghz = None
    manual_cfg = params.get("manual_cfg")
    # Registered manual filing: the systems row points at a YAML SRS (written
    # by the Manual System page through the Upload flow) — load it as the
    # manual definition; the companion mask is the registered .xml.
    if (not manual_cfg and srs_path
            and str(srs_path).lower().endswith((".yaml", ".yml"))):
        import yaml as _yaml
        with open(srs_path, "r", encoding="utf-8") as _fh:
            manual_cfg = _yaml.safe_load(_fh) or {}
        if mask_path:
            manual_cfg.setdefault("pfd_mask", {})
            manual_cfg["pfd_mask"].update(
                {"source": "xml_file", "file": str(mask_path)})

    _emit_progress(2)
    if manual_cfg:
        # Manual/parametric system (R3/R4): no SRS db — the full non_gso +
        # pfd_mask definition travels in the params payload.
        from src.main import load_from_manual  # type: ignore[import]
        _emit(f"Loading manual system: {manual_cfg.get('label') or 'manual'}")
        cfg = load_from_manual(manual_cfg)
        if params.get("service"):
            cfg.setdefault("gso_es", {})["service"] = str(service).upper()
        mask_path = (manual_cfg.get("pfd_mask") or {}).get("file") or mask_path
        mask_id = (manual_cfg.get("pfd_mask") or {}).get("mask_id", mask_id)
    elif mask_path and str(mask_path).lower().endswith(".xml"):
        _emit(f"Loading filing: {srs_path}")
        cfg = load_from_srs(srs_path, xml_path=str(mask_path), mask_id=mask_id,
                            ntc_id=ntc_id, service=service,
                            simulation_frequency_ghz=sim_freq_ghz)
    elif mask_path:
        # .mdb mask: when mask_id is not explicitly chosen, leave it None so
        # load_from_srs resolves it from mask_lnk1 precedence (emi_rcp=E →
        # grp_id → seq_no) restricted to groups covering the simulation
        # frequency (when one was selected), falling back to the first
        # declared PFD only when the filing has no mask_lnk1 assignment.
        _emit(f"Loading filing: {srs_path}")
        cfg = load_from_srs(srs_path, pfd_mask_mdb=str(mask_path), mask_id=mask_id,
                            ntc_id=ntc_id, service=service,
                            simulation_frequency_ghz=sim_freq_ghz)
    else:
        _emit(f"Loading filing: {srs_path}")
        cfg = load_from_srs(srs_path, xml_path=None, mask_id=mask_id,
                            ntc_id=ntc_id, service=service,
                            simulation_frequency_ghz=sim_freq_ghz)

    sim = cfg.setdefault("simulation", {})
    ngso = cfg.setdefault("non_gso", {})
    gso_es = cfg.setdefault("gso_es", {})
    wcg_search_cfg = cfg.setdefault("wcg_search", {})

    # Single-entry runs the WCG-located simulation only — disable the
    # additional Static ES (Brasilia) phase by default.
    sim["run_static_es"] = bool(params.get("run_static_es", False))

    if "num_time_steps" in params:
        sim["num_time_steps"] = int(params["num_time_steps"])
    if "time_step_s" in params:
        sim["coarse_time_step_s"] = float(params["time_step_s"])
        sim["_coarse_step_overridden"] = True
    if params.get("fine_time_step_s") is not None:
        sim["fine_time_step_s"] = float(params["fine_time_step_s"])
        sim["_fine_step_overridden"] = True
    if params.get("dual_time_step_mode"):
        sim["dual_time_step_mode"] = params["dual_time_step_mode"] if params["dual_time_step_mode"] != "on" else "s1503"
    if params.get("itu_software"):
        sim["itu_software"] = str(params["itu_software"]).lower()
    # ε₀ override: only when the user supplied a value. None/absent = keep the
    # filing's ε₀ (SRS grp.elev_min), per S.1503-4 (ε₀ is a system parameter).
    if params.get("min_elevation_deg") is not None:
        ngso["min_elevation_deg"] = float(params["min_elevation_deg"])
    if "keep_full_history" in params:
        sim["keep_full_history"] = bool(params["keep_full_history"])
    if params.get("es_antenna_diameter_m"):
        gso_es["antenna_diameter_m"] = float(params["es_antenna_diameter_m"])
    if "use_proposed_antenna" in params:
        gso_es["use_proposed_antenna"] = bool(params["use_proposed_antenna"])
    if "proposed_antenna_option" in params:
        gso_es["proposed_antenna_option"] = int(params["proposed_antenna_option"])
    # WCGA: S.1503-4 §D.3.1 algorithm is the normative path. Default ON.
    wcg_search_cfg["use_s1503_algo"] = bool(params.get("wcga_s1503", True))
    if params.get("s1503_step_deg") is not None:
        wcg_search_cfg["s1503_step_deg"] = float(params["s1503_step_deg"])
        # legacy θ/φ grid fallback uses phi_step_deg — keep consistent.
        wcg_search_cfg["phi_step_deg"] = float(params["s1503_step_deg"])
    if params.get("wcga_no_mask_symmetry", True):
        wcg_search_cfg["s1503_symmetric_mask"] = False
    else:
        wcg_search_cfg["s1503_symmetric_mask"] = True
    if params.get("s1503_trail_all_points"):
        wcg_search_cfg["s1503_trail_all_points"] = True
    if params.get("wcga_flat_threshold"):
        wcg_search_cfg["wcga_flat_threshold"] = True
    if params.get("wcga_theta_equal_density"):
        wcg_search_cfg["wcga_theta_equal_density"] = True
        os.environ["SHARC_WCGA_THETA_EQUAL_DENSITY"] = "1"
    if params.get("gso_longitude_mode"):
        wcg_search_cfg["gso_longitude_mode"] = params["gso_longitude_mode"]
    if params.get("alpha_method"):
        # Engine reads alpha_method from cfg["simulation"]; keep a copy
        # under wcg_search for completeness / future hooks.
        sim["alpha_method"] = params["alpha_method"]
        wcg_search_cfg["alpha_method"] = params["alpha_method"]
    # Orbital dynamics (S.1503-4 §D6.3). Only set keys when the user decided,
    # so the engine's SRS-derived auto-detection still applies otherwise.
    if "artificial_precession" in params:  # absent = auto-detect
        sim["artificial_precession"] = bool(params["artificial_precession"])
    if params.get("use_precession_mdb"):
        sim["use_precession_mdb"] = True
    # GMST0: non-destructive per-run override. ON = force 0 via the engine's
    # override path (earth_rotation_initial_deg, precedence over the SRS-inferred
    # ngso `_gmst0_deg`, which is left intact). The Ray init re-applies it (it
    # snapshots get_earth_rotation_initial_deg() after the engine sets it).
    if params.get("force_gmst0_zero"):
        sim["earth_rotation_initial_deg"] = 0.0
    if params.get("apply_station_keeping"):
        sim["apply_station_keeping_wdelta"] = True
    # Restrict the simulated constellation to satellites emitting at the
    # simulation frequency (SRS grp ⋈ mask_lnk1). Opt-in; default off.
    if params.get("restrict_emitters_to_sim_band"):
        sim["restrict_emitters_to_sim_band"] = True
    # S.1503-2 emulation knobs (for comparison against the ITU BR reference sw,
    # which implements S.1503-2): drop the gain OR-rescue inside the exclusion
    # zone (S.1503-2 §5.1.4 counts only satellites OUTSIDE the zone) and disable
    # the S.1503-4 εGSO (Table 8) GSO-minimum-elevation gate (absent in -2).
    if params.get("strict_exclusion_zone"):
        ngso["strict_exclusion_zone"] = True
    if params.get("disable_gso_min_elevation"):
        ngso["apply_gso_min_elevation"] = False
    # "Emulate S.1503-2": single UI switch for the ITU BR GIBC reference
    # behaviour. Sets strict_exclusion_zone, which (a) drops the gain OR-rescue
    # in the WCGD (§D3.1.2) and per-satellite Step 18 logic, and (b) re-enables
    # the per-instant elGSO hard-exclusion in the temporal EPFD↓ sim. With it
    # OFF (default) the engine follows S.1503-4: WCGD OR-branch on, and Step 18
    # has no elGSO term (no per-instant GSO-elevation exclusion).
    if params.get("emulate_s1503_2"):
        ngso["strict_exclusion_zone"] = True

    # Track-duration override (S.1503-4 §D5.1.4.2). ``min_duration_s`` > 0 forces
    # the sliding-window variant across all latitudes, overriding (or supplying,
    # for manual systems) the SRS sat_oper MIN_DURATION. 0/absent keeps whatever
    # the filing declares (empty for manual → standard §D5.1.4.1 path).
    _md = params.get("min_duration_s")
    if _md is not None and float(_md) > 0.0:
        ngso["min_duration_by_lat"] = [(-90.0, 90.0, float(_md))]

    if "selection_strategy" in params:
        sim["selection_strategy"] = params["selection_strategy"]
    if "top_n" in params:
        sim["top_n"] = int(params["top_n"])
    if "n_select" in params:
        sim["n_select"] = int(params["n_select"])
    if "seed" in params and params["seed"] is not None:
        sim["seed"] = int(params["seed"])
    if "include_override" in params:
        sim["include_override"] = bool(params["include_override"])
    if params.get("hybrid_list_size"):
        sim["hybrid_list_size"] = int(params["hybrid_list_size"])
    # Alpha table (Doc 4A/312): declared min/max CDF pairs come pre-parsed from
    # the UI (JSON/YAML) — NOT from the .mdb — and the α-bin width for the TSS
    # densification. run_wcg_downlink reads config["non_gso"]["alpha_table"].
    if params.get("alpha_bin_deg") is not None:
        sim["alpha_bin_deg"] = float(params["alpha_bin_deg"])
    _at = params.get("alpha_table")
    if _at and "min" in _at and "max" in _at:
        ngso["alpha_table"] = {
            "min": [[float(a), float(p)] for a, p in _at["min"]],
            "max": [[float(a), float(p)] for a, p in _at["max"]],
        }
        # ``alpha_table_file`` is provenance only (which declared table this
        # run used) — the engine runs off the inline pairs. Echoed to the log
        # so worker.log identifies the table without opening params.json.
        _at_file = params.get("alpha_table_file")
        _emit(
            f"alpha_table: {len(_at['min'])} min / {len(_at['max'])} max pairs"
            + (f" from '{_at_file}'" if _at_file else " (source file not recorded)")
        )

    # MAX_CO_FREQ override (S.1503-4 Steps 19-22 cap; also Nco for
    # reference-vector selection below). None/absent = keep whatever the
    # filing's sat_oper declares. 0 = unlimited (ignores sat_oper). >=1 forces
    # a single [-90°, 90°] -> N range for every latitude.
    _mcf = params.get("max_co_freq")
    if _mcf is not None:
        apply_max_co_freq_override_to_non_gso(ngso, int(_mcf))

    # Reference-vector satellite selection (US proposal R23-WP4A-C-0519).
    # Only written to the config when explicitly enabled so the engine default
    # (normative MAX_CO_FREQ) is preserved when the key is absent. Hold
    # duration comes solely from MIN_DURATION (the min_duration_s override
    # above, or the filing's own sat_oper value) — see
    # artifacts/WP4A_519_track_duration_consolidation_decision.md — so there
    # is no separate ref_vec_track_duration_T_s param.
    if params.get("ref_vec_selection"):
        sim["ref_vec_selection"] = True
        sim["ref_vec_az_deg"] = float(params.get("ref_vec_az_deg", 0.0))
        sim["ref_vec_el_deg"] = float(params.get("ref_vec_el_deg", 90.0))
        sim["ref_vec_time_window_P_pct"] = float(params.get("ref_vec_time_window_P_pct", 100.0))
        # Conservative extension (NOT in the US proposal): force-include the
        # WCG-origin satellite when eligible, consuming an Nco slot. Default
        # OFF — with Nco=1 it would replace the ranking entirely.
        sim["ref_vec_force_wcg_sat"] = bool(params.get("ref_vec_force_wcg_sat", False))

    # Scope of the "1503 proposal modifications": by default they act on the
    # EPFD↓ simulation only; mods_in_wcga=True extends them to the WCG search
    # (proposed antenna in the WCGA, Step-18 ablation there too, and
    # strategy-aggregated re-ranking of the found geometry — see
    # run_wcg_downlink).
    _mods_in_wcga = bool(params.get("mods_in_wcga"))
    if _mods_in_wcga:
        sim["mods_in_wcga"] = True
        _emit("S.1503 modifications scope: EPFD simulation AND WCG search")

    # Step-18 gain-test ablation (Doc 4A/1029 §5): drop the Gmax−30 dB
    # candidate; GRX(α₀) alone governs the OR branch (α₀ always kept). Env
    # var, not a cfg key: the test sits deep in the WCGA + EPFD hot paths and
    # the env is inherited by every multiprocessing pool child of this worker
    # process. Ray remote workers get it via runtime_env below. Value "both"
    # applies it to the WCG search too; "epfd" keeps the WCGA normative.
    if params.get("drop_gmax30"):
        os.environ["SHARC_S1503_DROP_GMAX30"] = (
            "both" if _mods_in_wcga else "epfd"
        )
        _emit("S.1503 modification ON: Step-18 gain test without the "
              "Gmax−30 dB candidate (GRX(α₀) alone governs) — scope: "
              + ("EPFD + WCGA" if _mods_in_wcga else "EPFD only"))

    # SL2SL sidelobe study (non-normative; src/sidelobe_epfd.py).
    if params.get("sidelobe_enabled"):
        sim["sidelobe_enabled"] = True
        sim["sidelobe_pattern"] = str(params.get("sidelobe_pattern", "1.4"))
        sim["sidelobe_scope"] = str(params.get("sidelobe_scope", "outside_zone"))
        sim["sidelobe_pfd_source"] = str(
            params.get("sidelobe_pfd_source", "constant"))
        if params.get("sidelobe_alpha_gate_deg") is not None:
            sim["sidelobe_alpha_gate_deg"] = float(
                params["sidelobe_alpha_gate_deg"])
        for _k in ("sidelobe_pfd_dbw_m2", "sidelobe_grid_radius_km",
                   "sidelobe_grid_spacing_km", "sidelobe_min_elevation_deg",
                   "sidelobe_gso_arc_separation_deg", "sidelobe_gmax_dbi",
                   "sidelobe_frequency_ghz",
                   "sidelobe_p14_n_sidelobes", "sidelobe_p14_slr_db",
                   "sidelobe_p14_aperture_m",
                   "sidelobe_p12_near_lobe_level_db", "sidelobe_p12_hpbw_deg"):
            if params.get(_k) is not None:
                sim[_k] = float(params[_k])

    # Manual WCG override
    if params.get("wcg_manual") and all(
        params.get(k) is not None for k in ("wcg_manual_es_lat", "wcg_manual_es_lon", "wcg_manual_gso_lon")
    ):
        # run_wcg_downlink reads the manual geometry from the nested `manual_wcg`
        # dict (keys: enabled, es_lat_deg, es_lon_deg, gso_lon_deg). Writing the
        # old flat `wcg_manual_*_deg` keys here left manual_wcg empty, so the
        # override was silently ignored and the WCGA ran instead.
        wcg_search_cfg["manual_wcg"] = {
            "enabled": True,
            "es_lat_deg": float(params["wcg_manual_es_lat"]),
            "es_lon_deg": float(params["wcg_manual_es_lon"]),
            "gso_lon_deg": float(params["wcg_manual_gso_lon"]),
            "align_constellation": bool(params.get("wcg_manual_align", True)),
        }

    # Article 22 downlink scenario (UI tree leaf): pin the reference bandwidth
    # and the simulation frequency run so apply_article22_limits_to_config
    # resolves the exact normative table row the user selected. Service +
    # ES-antenna diameter are already applied above (via params). When absent,
    # the engine auto-resolves from the filing band as before.
    if params.get("reference_bandwidth_khz") is not None:
        cfg.setdefault("article22_limits", {})["reference_bandwidth_khz"] = \
            float(params["reference_bandwidth_khz"])
    if params.get("simulation_frequency_ghz") is not None:
        cfg.setdefault("pfd_mask", {})["simulation_frequency_ghz"] = \
            float(params["simulation_frequency_ghz"])

    apply_article22_limits_to_config(cfg)
    apply_resolution76_limits_to_config(cfg)
    _emit_progress(10)

    # ── Consolidated "1503 proposal modifications" banner in the run log ──
    _mod_lines: list[str] = []
    if bool(cfg.get("gso_es", {}).get("use_proposed_antenna")):
        _mod_lines.append(
            "S.1428 proposed ES antenna — variant "
            + ("A" if int(cfg["gso_es"].get("proposed_antenna_option", 1)) == 1
               else "B")
        )
    _sel0 = sim.get("selection_strategy")
    if _sel0 and _sel0 != "s1503":
        _sel_extra = ""
        if _sel0 == "top_n_elev_random":
            _sel_extra = (f" (N={sim.get('top_n')}, M={sim.get('n_select')}, "
                          f"seed={sim.get('seed')})")
        elif _sel0 == "hybrid_rand_he":
            _sel_extra = f" (seed={sim.get('seed')})"
        elif _sel0 == "alpha_table":
            _sel_extra = f" (α sub-bin={sim.get('alpha_bin_deg', 0.0)}°)"
        _mod_lines.append(f"selection strategy: {_sel0}{_sel_extra}")
    if sim.get("ref_vec_selection"):
        _mod_lines.append(
            "selection strategy: reference vector "
            f"(az={sim.get('ref_vec_az_deg')}°, el={sim.get('ref_vec_el_deg')}°, "
            f"P={sim.get('ref_vec_time_window_P_pct')}%"
            + (", WCG-sat force-include ON" if sim.get("ref_vec_force_wcg_sat")
               else "")
            + ")"
        )
    if (_sel0 and _sel0 in ("top_n_elev_random", "hybrid_rand_he")) or \
            sim.get("ref_vec_selection"):
        _mod_lines.append(
            "Step-22 (OR) satellites: "
            + ("INCLUDED" if sim.get("include_override") else "disabled")
        )
    if os.environ.get("SHARC_S1503_DROP_GMAX30"):
        _mod_lines.append(
            "Step-18 gain test WITHOUT the Gmax−30 dB candidate "
            f"(scope: {os.environ['SHARC_S1503_DROP_GMAX30']})"
        )
    if sim.get("sidelobe_enabled"):
        _src = sim.get("sidelobe_pfd_source", "constant")
        _mod_lines.append(
            f"sidelobe SL2SL — scope={sim.get('sidelobe_scope', 'outside_zone')}, "
            f"S.1528 rec {sim.get('sidelobe_pattern', '1.4')}, pfd="
            + (f"{sim.get('sidelobe_pfd_dbw_m2', -140.0)} dBW/m² (constant)"
               if _src == "constant" else "filing mask @ served-link geometry")
            + f", grid {sim.get('sidelobe_grid_radius_km', 315.0):.0f} km / "
            f"{sim.get('sidelobe_grid_spacing_km', 21.0):.0f} km"
        )
    if _mod_lines:
        _emit("═══ 1503 proposal modifications ACTIVE ═══")
        _emit("  scope: " + ("EPFD simulation AND WCG search"
                             if sim.get("mods_in_wcga") else
                             "EPFD simulation only (normative WCGA)"))
        for _ln in _mod_lines:
            _emit(f"  • {_ln}")
    else:
        _emit("1503 proposal modifications: none (normative S.1503-4 run)")

    _emit("Running WCGA + EPFD↓ simulation (this may take a while)")
    _emit_progress(15)

    # When a Ray cluster is configured, distribute both heavy phases across
    # it — the WCGA latitude sweep and the EPFD↓ time-chunk sweep — which are
    # otherwise capped to one host's cores. Parity-preserving: same work units
    # + merges, only the executor differs. No-op when standalone.
    from streamlit_app.lib import cluster, wcga_cluster, epfd_cluster  # noqa: PLC0415
    _distributed = False
    try:
        rt_env = cluster.uploads_runtime_env(
            filings=[{"srs_path": srs_path, "mask_path": mask_path}],
        )
        # Ray remote workers do not inherit this process's env — propagate the
        # Step-18 ablation flag through the runtime_env (see drop_gmax30 above).
        if rt_env is not None and os.environ.get("SHARC_S1503_DROP_GMAX30"):
            rt_env.setdefault("env_vars", {})["SHARC_S1503_DROP_GMAX30"] = "1"
        wcga_on = wcga_cluster.enable(
            runtime_env=rt_env,
            on_progress=lambda i, n: _emit_progress(15 + 30.0 * i / max(1, n)),
        )
        epfd_on = epfd_cluster.enable(
            runtime_env=rt_env,
            on_progress=lambda i, n: _emit_progress(50 + 35.0 * i / max(1, n)),
        )
        if wcga_on or epfd_on:
            _distributed = True
            _emit(
                "Distributed across Ray cluster: "
                f"WCGA={'on' if wcga_on else 'off'}, EPFD={'on' if epfd_on else 'off'}"
            )
    except Exception as exc:  # noqa: BLE001
        _emit(f"WARN: cluster dispatch unavailable ({exc}); local compute")

    try:
        constellation, wcg_dl, sim_result_dl, comp_dl, _wcg_ul, _sim_ul, _comp_ul = run_wcg_downlink(cfg)
    except NoValidGeometry as exc:
        # Legitimate outcome, not a crash: the WCG search completed but no
        # geometry satisfied the S.1503-4 store criteria. Emit a clean
        # "no_geometry" result (completed run, orange pill) instead of letting
        # it fall through to the generic failure handler / a len(None) crash.
        _emit(f"WCGA: no valid geometry — {exc}")
        _hardware = hwinfo.run_hardware()
        _timing = hwinfo.run_timing(_started_at, _t0)
        no_geom_data: dict[str, Any] = {
            "kind": "s1503",
            "compliance": "no_geometry",
            "message": str(exc),
            "diagnostics": getattr(exc, "diagnostics", {}) or {},
            "wcg": None,
            "ccdf_bins_db": [],
            "ccdf_pct": [],
            "max_epfd_dbw_m2_40khz": None,
            "percentiles": {},
            "n_satellites": 0,
            "num_time_steps": 0,
            "time_step_s": 0.0,
            "hardware": _hardware,
            "timing": _timing,
        }
        (result_path / "sim_data.json").write_text(
            json.dumps(no_geom_data, indent=2), encoding="utf-8"
        )
        summary = {
            "compliance": "no_geometry",
            "message": str(exc),
            "max_epfd_dbw_m2_40khz": None,
            "percentiles": {},
            "n_satellites": 0,
            "hardware": _hardware,
            "timing": _timing,
        }
        (result_path / "summary.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )
        _emit_progress(100)
        _emit("DONE")
        return summary
    finally:
        if _distributed:
            wcga_cluster.disable()
            epfd_cluster.disable()
    _emit_progress(85)

    sim_data: dict[str, Any] = {
        "kind": "s1503",
        # Direction/Service of the run (S.1503-4 §D2.1) made explicit — the
        # engine implements the epfd(down) direction only (R21).
        "epfd_type": "down",
        # Input provenance (R26): SRS/MDB filing vs manual/parametric entry.
        "input_source": "manual" if manual_cfg else "mdb",
        "compliance": "pass" if (comp_dl and comp_dl.compliant) else (
            "fail" if comp_dl else "unknown"
        ),
        "wcg": None,
        "ccdf_bins_db": [],
        "ccdf_pct": [],
        "max_epfd_dbw_m2_40khz": None,
        "percentiles": {},
        "n_satellites": int(len(constellation)) if constellation else 0,
        "num_time_steps": int(sim.get("num_time_steps") or 0),
        "time_step_s": float(sim.get("coarse_time_step_s") or 1.0),
    }

    # Identification of the notice/mask behind this run (R18) — sat_name comes
    # from the SRS system object the loader pins under cfg["_srs_system"].
    _srs_sys = cfg.get("_srs_system")
    _mask_type = None
    _pm = cfg.get("pfd_mask") or {}
    if _pm.get("source") == "xml_file" or (
        mask_path and str(mask_path).lower().endswith(".xml")
    ):
        _mask_type = "xml"
    elif mask_path:
        _mask_type = "mdb"
    sim_data["identification"] = {
        "ntc_id": ntc_id if ntc_id is not None else getattr(_srs_sys, "ntc_id", None),
        "sat_name": getattr(_srs_sys, "sat_name", None) or cfg.get("manual_label"),
        # When the user did not pin a mask, echo the one the loader resolved
        # via mask_lnk1 precedence (kept under cfg["pfd_mask"]).
        "mask_id": mask_id if mask_id is not None else _pm.get("mask_id"),
        "mask_source": _mask_type,
        "srs_path": str(srs_path) if srs_path else None,
        "mask_path": str(mask_path) if mask_path else None,
    }
    # Mutually-exclusive configuration labelling (R2 / AP4 A.4.b.3.b-d): when
    # the filing is multi_config_type=M, record WHICH configuration this db
    # (and therefore this run) represents — results are per-configuration.
    _mc = cfg.get("multi_config")
    if _mc:
        sim_data["multi_config"] = {
            "type": _mc.get("type"),
            "nbr_config": _mc.get("nbr_config"),
            "config_label": _mc.get("config_label"),
            "orbit_set_id": _mc.get("orbit_set_id"),
            "label_source": _mc.get("source"),
        }

    # Units legend (S.1503-4 A2.1 Table 1) — R23: every quantity in the
    # artifact declares its unit here instead of relying on key suffixes.
    _refbw = float((cfg.get("article22_limits") or {}).get(
        "reference_bandwidth_khz") or 40.0)
    sim_data["units"] = {
        "epfd": f"dBW/m^2/{_refbw:.0f}kHz",
        "pfd": f"dBW/m^2/{_refbw:.0f}kHz",
        "reference_bandwidth": "kHz",
        "frequency": "MHz",
        "time": "s",
        "angle": "deg",
        "distance": "km",
        "percentage": "% of simulated time exceeded",
    }

    # Dual time step (S.1503-4 §D4.7): the Δt values resolved by the engine and
    # how many fine vs coarse steps the temporal loop actually executed.
    _acc_dl = getattr(sim_result_dl, "acc", None)
    _td_active = bool(sim.get("_track_duration") and getattr(sim_result_dl, "window_stats", None))
    if _td_active:
        # §D5.1.4.2 runs fine steps only (no dual step). Report it honestly so
        # the Results panel does not advertise a coarse Δt / Ncoarse never used.
        _tf = float(sim["_track_duration"].get("t_fine_s") or 0.0)
        _ntot = int(sim["_track_duration"].get("n_total_steps") or 0)
        _n_exec = getattr(_acc_dl, "n_steps", None)
        sim_data["dual_time_step"] = {
            "mode": "fine-only (track duration §D5.1.4.2)",
            "fine_step_s": _tf,
            "coarse_step_s": _tf,
            "ncoarse": 1,
            "num_time_steps": _ntot,
            "n_fine_steps_executed": _n_exec,
            "n_coarse_steps_executed": 0,
            "n_exec_steps": _n_exec,
        }
    else:
        sim_data["dual_time_step"] = {
            "mode": sim.get("dual_time_step_mode", "s1503"),
            "fine_step_s": sim.get("_resolved_dual_fine_step_s"),
            "coarse_step_s": sim.get("_resolved_dual_coarse_step_s"),
            "ncoarse": sim.get("_resolved_dual_ncoarse"),
            # Fine-equivalent run length (S.1503-4 NSTEPS).
            "num_time_steps": int(
                sim.get("_resolved_num_time_steps") or sim.get("num_time_steps") or 0
            ),
            "n_fine_steps_executed": (
                sim.get("_resolved_n_fine_steps")
                if sim.get("_resolved_n_fine_steps") is not None
                else getattr(_acc_dl, "n_fine_steps", None)
            ),
            "n_coarse_steps_executed": (
                sim.get("_resolved_n_coarse_steps")
                if sim.get("_resolved_n_coarse_steps") is not None
                else getattr(_acc_dl, "n_coarse_steps", None)
            ),
            "n_exec_steps": (
                sim.get("_resolved_n_exec_steps")
                if sim.get("_resolved_n_exec_steps") is not None
                else getattr(_acc_dl, "n_steps", None)
            ),
        }

    if wcg_dl is not None:
        sim_data["wcg"] = {
            "theta_deg": float(wcg_dl.theta_deg),
            "phi_deg": float(wcg_dl.phi_deg),
            "es_lat_deg": float(wcg_dl.es_lat_deg),
            "es_lon_deg": float(wcg_dl.es_lon_deg),
            "gso_lon_deg": float(wcg_dl.gso_lon_deg),
            "alpha_deg": float(wcg_dl.alpha_deg),
            "offaxis_deg": float(wcg_dl.offaxis_deg),
            "epfd_dBW": float(wcg_dl.epfd_dBW),
            "elevation_deg": float(wcg_dl.elevation_deg),
        }
        # Present only for country-constrained WCGA (ΔΩ ground-track alignment).
        _cw_align = sim.get("_country_wcg_alignment")
        if _cw_align:
            sim_data["country_wcg_alignment"] = dict(_cw_align)

        # WCG explanation: decompose the single-entry EPFD and say WHY this
        # geometry won — an isolated EPFD peak vs the angular-velocity tie-break
        # over a flat plateau (S.1503-4 §D3.1.2). Uses the per-latitude profile
        # captured by the WCGA. Drives the annotation on "Geometry on the globe".
        prof = list(getattr(wcg_dl, "latitude_profile", None) or [])
        if prof:
            bin_db = 0.1
            alpha0 = ngso.get("alpha0_deg")
            max_epfd = max(p["epfd_dBW"] for p in prof)
            top = [p for p in prof if p["epfd_dBW"] >= max_epfd - bin_db]
            top_es = sorted(p["es_lat_deg"] for p in top)
            spread = max_epfd - min(p["epfd_dBW"] for p in top)
            # Compact the profile for storage/chart — but NEVER by dropping the
            # points that decided the outcome. Plain `prof[::stepd]` did, and
            # since the winner sits on a needle-thin latitude it was dropped
            # every time: the chart then showed the WCG star floating up to
            # 7.5 dB above a curve that never contained it.
            stepd = max(1, len(prof) // 240)
            _keep_lat = {round(float(p_["es_lat_deg"]), 6) for p_ in top}
            _keep_lat.add(round(float(wcg_dl.es_lat_deg), 6))
            _sampled = list(prof[::stepd])
            _have = {round(float(p_["es_lat_deg"]), 6) for p_ in _sampled}
            _sampled += [p_ for p_ in prof
                         if round(float(p_["es_lat_deg"]), 6) in _keep_lat
                         and round(float(p_["es_lat_deg"]), 6) not in _have]
            _sampled.sort(key=lambda p_: float(p_["es_lat_deg"]))
            sim_data["wcg_explanation"] = {
                "selection": ("angular-velocity tie-break" if len(top) > 1 else "epfd-peak"),
                "epfd_dBW": float(wcg_dl.epfd_dBW),
                "pfd_dBW": float(wcg_dl.pfd_dBW),
                "es_gain_rel_dB": float(wcg_dl.es_gain_rel_dB),
                "alpha_deg": float(wcg_dl.alpha_deg),
                "offaxis_deg": float(wcg_dl.offaxis_deg),
                "alpha0_deg": (float(alpha0) if alpha0 is not None else None),
                "in_exclusion_zone": (
                    bool(abs(float(wcg_dl.alpha_deg)) < float(alpha0))
                    if alpha0 is not None else None
                ),
                "angular_velocity_deg_s": float(wcg_dl.angular_velocity_deg_s),
                "n_tied_top_bin": int(len(top)),
                "tie_window_db": float(bin_db),
                "profile_max_epfd_dBW": float(max_epfd),
                "plateau_db": float(spread),
                "plateau_es_lat_range": (
                    [float(top_es[0]), float(top_es[-1])] if top_es else None
                ),
                # The tied latitudes themselves. The min/max range above says
                # nothing about what lies between them: these ties are usually
                # north/south MIRROR geometries (e.g. -74.75 and +74.75), not a
                # contiguous flat span, and reading the range as a plateau is
                # what makes the chart look wrong.
                "tied_es_lats": [float(x) for x in top_es],
                "tie_is_mirror_pair": bool(
                    len(top_es) == 2
                    and abs(top_es[0] + top_es[-1]) < 0.05
                    and abs(top_es[0]) > 1.0
                ),
                "profile": _sampled,
                "profile_decimation": int(stepd),
            }

    if sim_result_dl is not None:
        try:
            sim_result_dl.build_cdf()
        except Exception:  # noqa: BLE001
            pass
        bins = list(map(float, sim_result_dl.cdf_epfd_dBW))
        pct = list(map(float, sim_result_dl.cdf_percentage))
        sim_data["ccdf_bins_db"] = bins
        sim_data["ccdf_pct"] = pct
        if bins:
            sim_data["max_epfd_dbw_m2_40khz"] = float(bins[0])
        if bins and pct:
            try:
                sim_data["percentiles"] = _extract_percentiles(bins, pct)
            except Exception as exc:  # noqa: BLE001
                _emit(f"WARN: percentile extraction failed: {exc}")

    # SL2SL sidelobe study (non-normative): sidelobe-only CCDF plus the
    # per-step standard+sidelobe total, on the same time base as the headline.
    _sl_acc = getattr(sim_result_dl, "sl_acc", None)
    if _sl_acc is not None:
        try:
            _sb, _sp = _sl_acc.build_ccdf()
            _tb, _tp = sim_result_dl.sl_tot_acc.build_ccdf()
            sim_data["sidelobe"] = {
                "pattern": getattr(sim_result_dl, "sidelobe_pattern", None),
                "scope": getattr(sim_result_dl, "sidelobe_scope", None),
                "pfd_source": getattr(sim_result_dl, "sidelobe_pfd_source", None),
                "alpha_gate_deg": sim.get("sidelobe_alpha_gate_deg"),
                "grid_radius_km": sim.get("sidelobe_grid_radius_km", 315.0),
                "grid_spacing_km": sim.get("sidelobe_grid_spacing_km", 21.0),
                "ccdf_bins_db": [float(x) for x in _sb],
                "ccdf_pct": [float(x) for x in _sp],
                "max_epfd_dbw": (float(_sb[0]) if len(_sb) else None),
                "total_ccdf_bins_db": [float(x) for x in _tb],
                "total_ccdf_pct": [float(x) for x in _tp],
                "total_max_epfd_dbw": (float(_tb[0]) if len(_tb) else None),
            }
            # §7 diagnostics: candidate-count distribution and how often a
            # candidate found no served cell (grid too small => understated).
            _dg = getattr(sim_result_dl, "sl_diag", None) or {}
            _hist = {int(k): int(v) for k, v in (_dg.get("hist") or {}).items()}
            _steps = int(_dg.get("steps") or 0)
            if _hist and _steps:
                _flat = []
                for _n, _c in sorted(_hist.items()):
                    _flat.extend([_n] * _c)
                import statistics as _stats  # noqa: PLC0415
                _n_zero = _hist.get(0, 0)
                sim_data["sidelobe"]["diagnostics"] = {
                    "steps": _steps,
                    "n_candidates_median": float(_stats.median(_flat)),
                    "n_candidates_p90": float(
                        _flat[min(len(_flat) - 1, int(0.90 * len(_flat)))]),
                    "n_candidates_max": max(_hist),
                    "pct_steps_with_candidate": round(
                        100.0 * (_steps - _n_zero) / _steps, 3),
                    "n_cand_without_link": int(
                        _dg.get("n_cand_without_link") or 0),
                }
                _d = sim_data["sidelobe"]["diagnostics"]
                _emit(
                    f"Sidelobe diagnostics: candidates/step median="
                    f"{_d['n_candidates_median']:.0f} p90={_d['n_candidates_p90']:.0f} "
                    f"max={_d['n_candidates_max']} · steps with a candidate="
                    f"{_d['pct_steps_with_candidate']}% · candidates with NO "
                    f"served cell={_d['n_cand_without_link']}"
                )
            _emit(
                "Sidelobe (SL2SL) study: max sidelobe-only EPFD "
                f"{sim_data['sidelobe']['max_epfd_dbw']} dBW · "
                f"max standard+sidelobe {sim_data['sidelobe']['total_max_epfd_dbw']} dBW"
            )
        except Exception as exc:  # noqa: BLE001
            _emit(f"WARN: sidelobe CCDF embed failed: {exc}")

    # Self-describing record of the WP-4A study options active in this run
    # (the "1503 proposal modifications" panel) — consumed by the Results page.
    _mods: dict[str, Any] = {}
    if bool(cfg.get("gso_es", {}).get("use_proposed_antenna")):
        _mods["s1428_proposed_pattern"] = {
            "variant": "A" if int(cfg["gso_es"].get("proposed_antenna_option", 1)) == 1 else "B",
        }
    _sel = sim.get("selection_strategy")
    if _sel and _sel != "s1503":
        _mods["selection_strategy"] = {
            "strategy": _sel,
            "top_n": sim.get("top_n"),
            "n_select": sim.get("n_select"),
            "seed": sim.get("seed"),
            "include_step22_or": bool(sim.get("include_override")),
        }
    if sim.get("ref_vec_selection"):
        _mods["ref_vec_selection"] = {
            "az_deg": sim.get("ref_vec_az_deg"),
            "el_deg": sim.get("ref_vec_el_deg"),
            "time_window_P_pct": sim.get("ref_vec_time_window_P_pct"),
            "include_step22_or": bool(sim.get("include_override")),
            "force_wcg_sat": bool(sim.get("ref_vec_force_wcg_sat")),
        }
    if os.environ.get("SHARC_S1503_DROP_GMAX30"):
        _mods["drop_gmax30"] = True
    if sim.get("sidelobe_enabled"):
        _mods["sidelobe"] = {
            "pattern": sim.get("sidelobe_pattern", "1.4"),
            "scope": sim.get("sidelobe_scope", "outside_zone"),
            "pfd_source": sim.get("sidelobe_pfd_source", "constant"),
        }
    if _mods:
        _mods["scope"] = (
            "epfd_and_wcga" if sim.get("mods_in_wcga") else "epfd_only"
        )
        # Written back into cfg["simulation"] by run_wcg_downlink when the
        # strategy-aggregated re-ranking ran over the WCGA trail.
        if sim.get("mods_in_wcga_rerank"):
            _mods["wcga_rerank"] = sim["mods_in_wcga_rerank"]
        sim_data["s1503_modifications"] = _mods

    # S.1503-4 §D5.1.4.2 track-duration variant: expose the window parameters and
    # per-slide-window-set CCDFs. The headline CCDF above is the worst-per-level
    # envelope across sets (go/no-go holds iff every set complies).
    _td = sim.get("_track_duration")
    # Reference-vector runs use the SAME MIN_DURATION/N_SW windowing as the
    # hold-duration source but accumulate one continuous stream (no
    # per-window-set stats) — surface the window parameters for them too, so
    # the run is self-describing about the hold that was applied.
    if _td and (
        getattr(sim_result_dl, "window_stats", None)
        or _td.get("ranking_policy") == "reference_vector"
    ):
        sim_data["track_duration"] = _td
        try:
            per_window = []
            for _i, (_b, _p) in enumerate(getattr(sim_result_dl, "per_window_ccdf", [])):
                per_window.append({
                    "window_index": int(_i),
                    "ccdf_bins_db": [float(x) for x in _b],
                    "ccdf_pct": [float(x) for x in _p],
                    "max_epfd_dbw": (float(_b[0]) if len(_b) else None),
                })
            sim_data["per_window"] = per_window
            sim_data["worst_window_index"] = int(getattr(sim_result_dl, "worst_window_index", -1))
        except Exception as exc:  # noqa: BLE001
            _emit(f"WARN: per-window CCDF embed failed: {exc}")

    if comp_dl is not None:
        sim_data["compliance_detail"] = {
            "worst_margin_dB": float(comp_dl.worst_margin_dB),
            "worst_limit_dBW": float(comp_dl.worst_limit_dBW),
            "worst_percentage": float(comp_dl.worst_percentage),
        }
        # S.1503-4 §D7.3.2 Table 17 — one row per Article 22 specification
        # point (Ji, Pi, Pass/fail, Py). Consumed by the summary report/xlsx.
        if getattr(comp_dl, "table17", None):
            sim_data["table17"] = list(comp_dl.table17)

    art22 = cfg.get("article22_limits") or {}
    res76 = cfg.get("resolution76_limits") or {}
    if art22:
        _a22 = {k: art22[k] for k in art22 if k != "_full_table"}
        # Pin the full normative scenario so the artifact is self-describing.
        _a22["service"] = str(cfg.get("gso_es", {}).get("service", service)).upper()
        _fr = cfg.get("non_gso", {}).get("frequency_ghz")
        if _fr is not None:
            _a22["frequency_run_ghz"] = float(_fr)
            _a22["frequency_run_mhz"] = float(_fr) * 1000.0
        sim_data["article22"] = _a22
    if res76:
        sim_data["resolution76"] = {k: res76[k] for k in res76 if k != "_full_table"}

    # Compact PDF/histogram (§D7.1.1; 0.1 dB bins §D1.4): only the non-empty
    # bins (~hundreds) — keeps sim_data self-contained for the report/xlsx.
    try:
        import numpy as _np  # noqa: PLC0415
        from src.epfd_stream_accumulator import _BIN_MIN_DB, _BIN_SIZE_DB  # noqa: PLC0415, PLC2701
        _dur = getattr(_acc_dl, "duration_per_bin", None)
        if _dur is not None:
            _dur = _np.asarray(_dur, dtype=float)
            _tot = float(_dur.sum())
            _nz = _np.nonzero(_dur > 0.0)[0]
            if _tot > 0.0 and _nz.size:
                sim_data["histogram"] = {
                    "bin_size_db": _BIN_SIZE_DB,
                    "bin_low_db": [round(_BIN_MIN_DB + int(i) * _BIN_SIZE_DB, 1) for i in _nz],
                    "duration_s": [float(_dur[i]) for i in _nz],
                    "probability": [float(_dur[i] / _tot) for i in _nz],
                }
    except Exception as exc:  # noqa: BLE001
        _emit(f"WARN: histogram embed failed: {exc}")

    hardware = hwinfo.run_hardware()
    timing = hwinfo.run_timing(_started_at, _t0)
    sim_data["hardware"] = hardware
    sim_data["timing"] = timing
    (result_path / "sim_data.json").write_text(json.dumps(sim_data, indent=2), encoding="utf-8")

    # Result artifacts (R11–R15): CCDF/histogram/time-series CSVs + images,
    # written next to sim_data.json. Best-effort — never fails the run.
    try:
        from streamlit_app.lib.result_artifacts import write_run_artifacts  # noqa: PLC0415
        _written = write_run_artifacts(result_path, sim_data, acc=_acc_dl)
        if _written:
            _emit("Artifacts: " + ", ".join(_written))
    except Exception as exc:  # noqa: BLE001
        _emit(f"WARN: artifact export failed: {exc}")

    # Summary report (S.1503-4 §D7.3: statement + Table 17 + CDF) — R17.
    try:
        from streamlit_app.lib.report import write_summary_html  # noqa: PLC0415
        if write_summary_html(result_path, sim_data):
            _emit("Artifacts: summary.html")
    except Exception as exc:  # noqa: BLE001
        _emit(f"WARN: summary report failed: {exc}")

    summary = {
        "compliance": sim_data["compliance"],
        "max_epfd_dbw_m2_40khz": sim_data["max_epfd_dbw_m2_40khz"],
        "percentiles": sim_data["percentiles"],
        "n_satellites": sim_data["n_satellites"],
        "hardware": hardware,
        "timing": timing,
    }
    (result_path / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    _emit_progress(100)
    _emit("DONE")
    return summary


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: s1503_worker <params.json>", file=sys.stderr)
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