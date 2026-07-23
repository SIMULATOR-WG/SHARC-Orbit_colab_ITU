"""Launcher — multi-method campaign (Studies 1+2+3+4+5 in one bundle)."""
from __future__ import annotations

import streamlit as st

from lib import launcher, storage, theme, tour
from lib.art22_ui import (
    system_bands, system_tx_subbands, merge_intervals, intersect_sets,
    art22_tree_for_bands,
)
from lib.manual import help_expander
from lib.state import use_persisted_state, set_persisted_state
from lib.widgets import select_described

_GSO_LON_DESC = {
    "arc_optimal": "Sweep the GSO satellite across the visible arc to find the worst PFD.",
    "es_meridian": "Pin GSO at the ES meridian (degenerate, faster, less normative).",
}
_ALPHA_DESC = {
    "sweep": "Numerical sweep over α (slower, robust near singular geometries).",
    "analytical": "Closed-form α per S.1503-4 §D (faster, requires regular geometry).",
}
_DUAL_TS_DESC = {
    "on": "Coarse step + S.1503-4 §D.4.7 fine refinement near the WCG (normative).",
    "off": "Single coarse step throughout (faster but less precise around WCG).",
}
_ITU_SW_OPTIONS = [
    "Reading A — Ntracks kept = 16",
    "Reading B — Ntracks follows N'hit (default)",
]
_ITU_SW_DESC = {
    "Reading A — Ntracks kept = 16": (
        "§D4.1 pseudo-code read literally: the 1e8 run-time reduction "
        "redefines only N'hit and N'coarse — Ntracks stays at 16 (§D4.5), "
        "so large non-repeating runs may stay above 1e8 steps. Reproduces "
        "the official engine 'A' runs (BR_Space v10, 2026 — MCSAT, "
        "Skybridge, Boeing) and the 'T' v5.35 MCSAT run (2018) to ≤0.06%."
    ),
    "Reading B — Ntracks follows N'hit (default)": (
        "Reads §D4.5 'Ntrack = Nhit' as an identity that propagates through "
        "the §D4.1 recalculation (N'track = N'hit), shortening large "
        "non-repeating runs ~20–36×. Reproduces the 'T' v5.45 runs "
        "(GIBC ≤ v9 — CRC STEAM-2, USASAT-NGSO-3X) to −0.05% on NSTEPS. "
        "Affects NSTEPS only: Δt (θ3dB = 70·λ/D + the §D4.1 multi-sub rule) "
        "is identical in both readings, and the EPFD statistics are "
        "expected to converge."
    ),
}
_ARTIFICIAL_PREC_DESC = {
    "off": "Force artificial RAAN precession off.",
    "on": "Force artificial RAAN precession on.",
    "auto": "Engine decides from the SRS (rpt period / f_precess / plane count).",
}

st.set_page_config(page_title="Launcher · SHARC-Orbit", page_icon=":material/play_circle:", layout="wide")
theme.inject()

st.title("Campaign launcher")
help_expander("launcher")
tour.maybe_render("launcher")
st.caption(
    "Launches Studies 1–3 (and optionally Methods 4/5) on the same systems "
    "as a single campaign — reproducible bundle."
)

systems = storage.list_systems()
if len(systems) < 2:
    st.warning("Register at least 2 systems on **Upload** before launching a campaign.")
    st.page_link("pages/1_Upload.py", label="Upload", icon=":material/upload:")
    st.stop()

# Persisted form state — same defaults as Single-entry / Aggregate.
prev = use_persisted_state("launcher.form", {
    "label": "campaign-1",
    "system_ids": [],
    "methods": ["method_1", "method_3"],
    "num_time_steps": 3600,
    "time_step_s": 1.0,
    "min_elevation_deg": 10.0,
    "service": "FSS",
    "es_antenna_diameter_m": 1.2,
    "wcga_s1503": True,
    "wcga_no_mask_symmetry": False,
    "s1503_step_deg": 1.0,
    "gso_longitude_mode": "arc_optimal",
    "alpha_method": "analytical",
    "dual_time_step_mode": "on",
    "fine_time_step_s": "",
    "itu_software": "itu_epfd",
    "artificial_prec_mode": "off",
    "use_precession_mdb": True,
    "apply_station_keeping": True,
    "grid_step_deg": 30.0,
    "gso_pointing_step_deg": 30.0,
    "n_geom_max": 50000,
    "country_codes": [],
})
_opt_ids = [s["id"] for s in systems]
_methods_all = ["method_1", "method_2", "method_3", "method_4"]

# Systems + Article 22 scenario live OUTSIDE the form so changing the
# selection updates the scenario tree immediately (forms defer reruns).
st.subheader("1. Systems")
sel_ids = st.multiselect(
    "Systems (≥ 2)",
    options=_opt_ids,
    default=[i for i in (prev.get("system_ids") or []) if i in _opt_ids]
    or _opt_ids[:3],
    format_func=lambda i: next(
        (f"{s.get('upload_label') or s['upload_id']} · ntc {s.get('ntc_id') or '—'}"
         for s in systems if s["id"] == i), i,
    ),
    key="launcher_sel_ids",
)

# Article 22 downlink scenario over the common band — one shared limit config
# applied to every method of the campaign. Picking a leaf overrides Service /
# ES antenna / reference BW / simulation frequency. No mask_id pinned (each
# filing keeps its own PFD mask). Auto = engine resolves per filing.
art22_leaf = None
_carried_art22 = None
# Per-system OPERATING intervals: PFD mask bands ∩ Tx `grp` sub-bands.
# Sliced BR extracts carry orphan masks of other bands — clipping to the
# grp sub-bands keeps the shared scenario inside bands every filing can
# actually run (systems without grp band data fall back to the PFD band).
_per_sys_intervals: list[list[tuple[float, float]]] = []
for _i in sel_ids:
    _s = storage.get_system(_i)
    if not _s:
        continue
    _sb = system_bands(_s["srs_path"], _s.get("ntc_id"))
    if _sb:
        _pfd_iv = merge_intervals([(b["freq_min"], b["freq_max"]) for b in _sb])
        _tx = system_tx_subbands(_s["srs_path"], _s.get("ntc_id"))
        if _tx:
            _tx_iv = merge_intervals([(b["freq_min"], b["freq_max"]) for b in _tx])
            _pfd_iv = intersect_sets(_pfd_iv, _tx_iv)
        if _pfd_iv:
            _per_sys_intervals.append(_pfd_iv)
_common: list[tuple[float, float]] = []
if len(_per_sys_intervals) >= 2:
    _common = _per_sys_intervals[0]
    for _nxt in _per_sys_intervals[1:]:
        _common = intersect_sets(_common, _nxt)

with st.expander("Article 22 downlink scenario (limits) — optional", expanded=False):
    if not _common:
        st.caption("No common downlink band across the selected systems "
                   "(or <2 systems with a PFD band) — engine auto-resolves "
                   "Article 22 limits per filing.")
    else:
        st.caption(
            f"Common **operating** band(s) (PFD ∩ Tx `grp` sub-bands): "
            f"**{'; '.join(f'{lo:.3f}–{hi:.3f}' for lo, hi in _common)} GHz**. "
            "A leaf overrides Service / ES antenna / reference BW / simulation "
            "frequency for **all methods**. Auto = engine resolves per filing."
        )
        _tree = art22_tree_for_bands(tuple(_common))
        _services = _tree.get("services") or []
        if not _services:
            st.caption("No Article 22 possibility for the common band.")
        else:
            _svc_opts = ["Auto (engine resolves)"] + [s["service"] for s in _services]
            _svc_pick = st.selectbox("Service", _svc_opts, key="lnch_art22_svc")
            if _svc_pick != "Auto (engine resolves)":
                _svc_node = next(s for s in _services if s["service"] == _svc_pick)
                _freqs = _svc_node["frequencies"]
                _fi = st.selectbox(
                    "Frequency run", options=list(range(len(_freqs))),
                    format_func=lambda i: (
                        f"{_freqs[i]['label']} · {_freqs[i]['rr_reference']} · "
                        f"R{','.join(str(r) for r in _freqs[i].get('regions', [])) or '—'}"
                    ),
                    key="lnch_art22_freq",
                )
                _fnode = _freqs[min(_fi, len(_freqs) - 1)]
                _opts = _fnode["options"]
                _oi = st.selectbox(
                    "ES antenna / reference BW", options=list(range(len(_opts))),
                    format_func=lambda i: f"{_opts[i]['label']} · {_opts[i]['rf_pattern_rr']}",
                    key="lnch_art22_opt",
                )
                art22_leaf = _opts[min(_oi, len(_opts) - 1)]
                st.success(
                    f"Scenario: **{art22_leaf['service']}** · run "
                    f"**{art22_leaf['frequency_run_mhz']:.2f} MHz** · ES "
                    f"**{art22_leaf['rf_diam_m']:.2f} m** · "
                    f"**{art22_leaf['reference_bandwidth_khz']:.0f} kHz** · "
                    f"{art22_leaf['rr_reference']} (applies to all methods)"
                )
    # Checked by default ONLY when the form state came from an explicit
    # reload (`art22_from_reload`), never for values left over from an old
    # launch.
    if art22_leaf is None:
        _rbw = prev.get("reference_bandwidth_khz")
        _sfreq = prev.get("simulation_frequency_ghz")
        if (_rbw is not None or _sfreq is not None) and st.checkbox(
            "Apply reloaded Article 22 scenario",
            value=bool(prev.get("art22_from_reload")),
            key="lnch_art22_carry",
            help="Scenario from a reloaded campaign. Uncheck for engine auto-resolve.",
        ):
            _carried_art22 = {"reference_bandwidth_khz": _rbw,
                               "simulation_frequency_ghz": _sfreq}

# Visible heads-up (outside the collapsed expander) whenever a reloaded
# Article 22 scenario is about to be re-applied to the next launch — the
# user must never launch with pinned freq/BW without noticing.
if _carried_art22 is not None:
    _bits = []
    if _carried_art22.get("simulation_frequency_ghz") is not None:
        _bits.append(
            f"frequency run "
            f"{float(_carried_art22['simulation_frequency_ghz']) * 1000.0:.2f} MHz")
    if _carried_art22.get("reference_bandwidth_khz") is not None:
        _bits.append(
            f"reference BW "
            f"{float(_carried_art22['reference_bandwidth_khz']):.0f} kHz")
    st.warning(
        "A **reloaded Article 22 scenario** will be applied to every method "
        "of this campaign: "
        + (" · ".join(_bits) or "values from the reloaded campaign")
        + ". Uncheck **Apply reloaded Article 22 scenario** (expander above) "
        "to let the engine auto-resolve.",
        icon=":material/warning:",
    )

# Mirror the leaf's Service + ES antenna into the form widgets below.
_forced_service = str(art22_leaf["service"]).upper() if art22_leaf is not None else None
_forced_diam_m = float(art22_leaf["rf_diam_m"]) if art22_leaf is not None else None

with st.form("campaign_form"):
    label = st.text_input("Campaign label", value=prev.get("label", "campaign-1"))
    methods = st.multiselect(
        "Methods to include",
        options=_methods_all,
        default=[m for m in (prev.get("methods") or []) if m in _methods_all]
        or ["method_1", "method_3"],
    )

    col1, col2 = st.columns(2)
    with col1:
        num_steps = st.number_input(
            "Number of time steps", 1, 10_000_000,
            int(prev.get("num_time_steps", 3600)), step=100,
            help="Engine key: `num_time_steps`. Normative Obs2 reference = "
                 "518 400; 3 600 is a fast smoke test (1 h @ 1 s).",
        )
        dt = st.number_input(
            "Coarse time step (s)", 0.001, 600.0,
            float(prev.get("time_step_s", 1.0)), step=0.1, format="%.3f",
            help="Engine key: `time_step_s`. Sampling period of the EPFD↓ "
                 "time series.",
        )
        min_elev = st.number_input(
            "Minimum ES elevation (°)", 0.0, 89.0,
            float(prev.get("min_elevation_deg", 10.0)), step=0.5,
            help="Engine key: `min_elevation_deg`. Cut-off elevation for the "
                 "earth station.",
        )
    with col2:
        _svc_default = (_forced_service or str(prev.get("service", "FSS"))).upper()
        service = st.selectbox(
            "Service", ["FSS", "BSS"],
            index=0 if _svc_default == "FSS" else 1,
            help="Engine key: `service`. Common across all systems and "
                 "methods. Set automatically by the Article 22 scenario above.",
        )
        diam = st.number_input(
            "ES antenna diameter (m)", 0.0, 100.0,
            float(_forced_diam_m if _forced_diam_m is not None
                  else prev.get("es_antenna_diameter_m", 1.2)),
            step=0.1, format="%.2f",
            help="Engine key: `es_antenna_diameter_m`. Common ES diameter "
                 "for all systems (Resolution 76). 0 = engine default 1.2 m. "
                 "Set automatically by the Article 22 scenario above.",
        )
        if art22_leaf is not None:
            st.caption("↑ Service & ES antenna set by the Article 22 scenario.")

    # The campaign applies these to every selected method; the worker uses
    # each option where the method needs it (WCG/time-step → methods 1/3/4,
    # grid → methods 2/5; orbital dynamics → all).
    with st.expander("WCG search (S.1503-4 §D.3) — methods 1/3/4", expanded=False):
        wcga_s1503 = st.checkbox(
            "Use S.1503-4 WCGA algorithm", value=bool(prev.get("wcga_s1503", True)),
            help="Engine key: `wcga_s1503`.",
        )
        wcga_no_mask_symmetry = st.checkbox(
            "Full θ — no mask symmetry",
            value=bool(prev.get("wcga_no_mask_symmetry", False)),
            help="Engine key: `wcga_no_mask_symmetry`. Enable for asymmetric masks.",
        )
        apply_table8_egso = st.checkbox(
            "Apply Table 8 εGSO gate (S.1503-4)",
            value=not bool(prev.get("disable_gso_min_elevation", False)),
            help="Engine key: `disable_gso_min_elevation` (= not this box). "
                 "Table 8 εGSO = min GSO-arc elevation (20° ≥17 GHz, 10° <17 GHz) "
                 "in the WCGD AND-branch (§D3.1.2) and the temporal gate. "
                 "DEFAULT ON: apply εGSO per the literal S.1503-4. "
                 "OFF: Table 8 NOT applied — WCGA may place the worst-case "
                 "ES at high latitude (matches ITU BR / S.1503-2). "
                 "Applies to all methods.",
        )
        col_w1, col_w2 = st.columns(2)
        with col_w1:
            s1503_step = st.number_input(
                "WCGA grid step (°)", 0.01, 30.0,
                float(prev.get("s1503_step_deg", 1.0)), step=0.1, format="%.2f",
                help="Engine key: `s1503_step_deg`.",
            )
            gso_lon_mode = select_described(
                "GSO longitude mode", ["arc_optimal", "es_meridian"], _GSO_LON_DESC,
                index=0 if prev.get("gso_longitude_mode") == "arc_optimal" else 1,
                help="Engine key: `gso_longitude_mode`.",
            )
        with col_w2:
            alpha_method = select_described(
                "α computation method", ["sweep", "analytical"], _ALPHA_DESC,
                index=0 if prev.get("alpha_method", "analytical") == "sweep" else 1,
                help="Engine key: `alpha_method`.",
            )

    with st.expander("Time step (S.1503-4 §D.4) — §D4 reading: all methods · dual mode: methods 1/3/4", expanded=False):
        col_t1, col_t2 = st.columns(2)
        with col_t1:
            fine_dt = st.text_input(
                "Fine time step (s)", value=str(prev.get("fine_time_step_s") or ""),
                placeholder="auto (S.1503-4 §D4.2 literal)",
                help="Engine key: `fine_time_step_s`. Empty = literal §D.4.2.",
            )
        with col_t2:
            dual_mode = select_described(
                "Dual time step mode", ["on", "off"], _DUAL_TS_DESC,
                index=0 if prev.get("dual_time_step_mode", "on") == "on" else 1,
                help="Engine key: `dual_time_step_mode`.",
            )
        itu_software_label = select_described(
            "S.1503-4 §D4 reading (time-step dimensioning)",
            _ITU_SW_OPTIONS, _ITU_SW_DESC,
            index=1 if str(prev.get("itu_software", "itu_epfd")).lower().startswith(("transfinite", "itu")) else 0,
            help="Engine key: `itu_software`. Two defensible readings of the "
                 "§D4.1 run-time reduction in Rec. S.1503-4 — the text is "
                 "ambiguous on whether Ntracks follows N'hit. Affects NSTEPS "
                 "only (Δt is identical) — not the EPFD physics.",
        )
        itu_software = ("itu_epfd" if itu_software_label.startswith("Reading B")
                        else "s1503_4")

    with st.expander("Orbital dynamics (station keeping / precession — S.1503-4 §D6.3)", expanded=False):
        col_o1, col_o2 = st.columns(2)
        with col_o1:
            artificial_prec_mode = select_described(
                "Artificial precession", ["off", "on", "auto"], _ARTIFICIAL_PREC_DESC,
                index={"off": 0, "on": 1, "auto": 2}.get(
                    prev.get("artificial_prec_mode", "off"), 0),
                help="Engine key: `artificial_precession`. Default off.",
            )
            use_prec_mdb = st.checkbox(
                "Use precession from SRS MDB",
                value=bool(prev.get("use_precession_mdb", True)),
                help="Engine key: `use_precession_mdb`.",
            )
        with col_o2:
            apply_sk = st.checkbox(
                "Apply Wdelta (station keeping)",
                value=bool(prev.get("apply_station_keeping", True)),
                help="Engine key: `apply_station_keeping_wdelta`.",
            )
            restrict_emitters = st.checkbox(
                "Only satellites emitting in the sim band",
                value=bool(prev.get("restrict_emitters_to_sim_band", True)),
                help="Engine key: `restrict_emitters_to_sim_band`. Per filing: "
                     "only satellites whose transmitting group covers the "
                     "simulation frequency. DEFAULT ON.",
            )

    with st.expander("Grid params (S.1503-4 §D.6) — methods 2/5", expanded=False):
        col_g1, col_g2, col_g3 = st.columns(3)
        with col_g1:
            grid_step = st.number_input(
                "Grid step (°)", 1.0, 90.0,
                float(prev.get("grid_step_deg", 30.0)), step=5.0,
                help="Engine key: `grid_step_deg`. ES lat/lon grid spacing.",
            )
        with col_g2:
            gso_pointing_step = st.number_input(
                "GSO pointing step (°)", 0.0, 90.0,
                float(prev.get("gso_pointing_step_deg", 30.0)), step=5.0,
                help="Engine key: `gso_pointing_step_deg`. 0 = same as grid step.",
            )
        with col_g3:
            n_geom = st.number_input(
                "Max geometries", 0, 10_000_000,
                int(prev.get("n_geom_max", 50000)), step=1000,
                help="Engine key: `n_geom_max`. 0 = engine default cap.",
            )
        try:
            from src.s1588_studies.countries import list_countries  # type: ignore[import]
            _all_countries = list_countries()
        except Exception:  # noqa: BLE001
            _all_countries = []
        _country_label = {c["id"]: f"{c['name']}  ({c['id']})" for c in _all_countries}
        country_codes = st.multiselect(
            "Restrict ES grid to countries (empty = world-wide)",
            options=list(_country_label.keys()),
            default=[c for c in (prev.get("country_codes") or [])
                     if c in _country_label],
            format_func=lambda c: _country_label.get(c, c),
            help="Engine key: `country_codes`. Only grid points inside the "
                 "selected countries are simulated.",
        )

    submit = st.form_submit_button("Launch campaign", type="primary", icon=":material/play_circle:")

if submit:
    if len(sel_ids) < 2:
        st.error("Pick at least 2 systems.")
        st.stop()
    if not methods:
        st.error("Pick at least 1 method.")
        st.stop()

    set_persisted_state("launcher.form", {
        "reference_bandwidth_khz": (
            float(art22_leaf["reference_bandwidth_khz"]) if art22_leaf is not None
            else (_carried_art22 or {}).get("reference_bandwidth_khz")),
        "simulation_frequency_ghz": (
            float(art22_leaf["frequency_run_ghz"]) if art22_leaf is not None
            else (_carried_art22 or {}).get("simulation_frequency_ghz")),
        "label": label,
        "system_ids": list(sel_ids),
        "methods": list(methods),
        "num_time_steps": int(num_steps),
        "time_step_s": float(dt),
        "min_elevation_deg": float(min_elev),
        "service": service,
        "es_antenna_diameter_m": float(diam),
        "wcga_s1503": bool(wcga_s1503),
        "wcga_no_mask_symmetry": bool(wcga_no_mask_symmetry),
        "s1503_step_deg": float(s1503_step),
        "gso_longitude_mode": gso_lon_mode,
        "alpha_method": alpha_method,
        "dual_time_step_mode": dual_mode,
        "fine_time_step_s": fine_dt,
        "itu_software": itu_software,
        "artificial_prec_mode": artificial_prec_mode,
        "use_precession_mdb": bool(use_prec_mdb),
        "apply_station_keeping": bool(apply_sk),
        "restrict_emitters_to_sim_band": bool(restrict_emitters),
        "disable_gso_min_elevation": not bool(apply_table8_egso),
        "grid_step_deg": float(grid_step),
        "gso_pointing_step_deg": float(gso_pointing_step),
        "n_geom_max": int(n_geom),
        "country_codes": list(country_codes),
    })

    cm_id = storage.create_campaign(
        label=label,
        params={"system_ids": sel_ids, "methods": methods,
                 "num_time_steps": int(num_steps), "time_step_s": float(dt)},
    )

    # Shared options applied to every method (worker uses each where relevant).
    def _fine() -> float | None:
        try:
            return float((fine_dt or "").strip())
        except (ValueError, TypeError):
            return None

    base: dict = {
        "num_time_steps": int(num_steps),
        "time_step_s": float(dt),
        "min_elevation_deg": float(min_elev),
        "service": service,
        "es_antenna_diameter_m": float(diam) if diam > 0 else None,
        # WCG search
        "wcga_s1503": bool(wcga_s1503),
        "wcga_no_mask_symmetry": bool(wcga_no_mask_symmetry),
        "s1503_step_deg": float(s1503_step),
        "gso_longitude_mode": gso_lon_mode,
        "alpha_method": alpha_method,
        # Time step
        "dual_time_step_mode": dual_mode,
        "itu_software": itu_software,
        # Orbital dynamics
        "use_precession_mdb": bool(use_prec_mdb),
        "apply_station_keeping": bool(apply_sk),
        "restrict_emitters_to_sim_band": bool(restrict_emitters),
        # Table 8 εGSO gate (checkbox unchecked / default → disabled)
        "disable_gso_min_elevation": not bool(apply_table8_egso),
    }
    if _fine() is not None:
        base["fine_time_step_s"] = _fine()
    if artificial_prec_mode == "on":
        base["artificial_precession"] = True
    elif artificial_prec_mode == "off":
        base["artificial_precession"] = False

    # Article 22 scenario (shared across all methods). Leaf overrides service /
    # ES antenna / ref BW / frequency; else re-apply a reloaded scenario.
    if art22_leaf is not None:
        base["service"] = str(art22_leaf["service"])
        base["es_antenna_diameter_m"] = float(art22_leaf["rf_diam_m"])
        base["reference_bandwidth_khz"] = float(art22_leaf["reference_bandwidth_khz"])
        base["simulation_frequency_ghz"] = float(art22_leaf["frequency_run_ghz"])
    elif _carried_art22 is not None:
        if _carried_art22.get("reference_bandwidth_khz") is not None:
            base["reference_bandwidth_khz"] = float(_carried_art22["reference_bandwidth_khz"])
        if _carried_art22.get("simulation_frequency_ghz") is not None:
            base["simulation_frequency_ghz"] = float(_carried_art22["simulation_frequency_ghz"])

    child_ids: list[str] = []
    for m in methods:
        params: dict = dict(base)
        if m == "method_2":
            params["grid_step_deg"] = float(grid_step)
            params["gso_pointing_step_deg"] = (
                float(gso_pointing_step) if gso_pointing_step > 0 else float(grid_step)
            )
            if n_geom > 0:
                params["n_geom_max"] = int(n_geom)
            if country_codes:
                params["country_codes"] = list(country_codes)
        rid = launcher.launch_s1588(method=m, system_ids=sel_ids,
                                       params=params, campaign_id=cm_id)
        child_ids.append(rid)

    st.success(
        f"Campaign `{cm_id}` launched with {len(child_ids)} runs: " +
        ", ".join(f"`{c}`" for c in child_ids)
    )
    st.query_params["campaign_id"] = cm_id
    st.page_link("pages/9_Campaign.py", label="Open Campaign dashboard", icon=":material/dashboard:")
