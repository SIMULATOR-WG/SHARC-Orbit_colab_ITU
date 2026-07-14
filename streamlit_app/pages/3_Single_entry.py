"""Single-entry EPFD↓ (ITU-R S.1503-4) — functional, advanced options included."""
from __future__ import annotations

import json

import streamlit as st

from lib import launcher, srs_inspect, storage, theme, tour
from lib.state import (
    use_persisted_state, set_persisted_state,
    current_system_id, set_current_system_id,
    set_current_run_id,
)
from lib.widgets import select_described
from lib.manual import help_expander


@st.cache_data(show_spinner=False)
def _art22_tree(srs_path: str, ntc_id: str | None):
    """Article 22 EPFD↓ possibility tree for a filing's PFD (downlink) bands.

    Enumerates the normative
    Art. 22 tables intersecting each PFD mask band → service → frequency run →
    option (ES antenna diameter, reference BW, pattern, limit curve). Each leaf
    carries its ``mask_ref`` (which PFD mask it belongs to). Cached on the SRS
    path + notice. Returns ``{"services": [...]}`` (empty when no PFD band).
    """
    try:
        from src.article22_tables import (  # type: ignore[import]
            list_article22_downlink_possibilities_for_masks,
        )
    except Exception:  # noqa: BLE001
        return {"services": []}
    bands = srs_inspect.frequency_bands(srs_path, ntc_id)
    masks = [
        {
            "mask_id": m["mask_id"],
            "label": f"mask {m['mask_id']}",
            "freq_min_ghz": m["freq_min_ghz"],
            "freq_max_ghz": m["freq_max_ghz"],
        }
        for m in (bands.get("masks") or [])
        if m.get("type") == "PFD" and m.get("freq_min_ghz") is not None
    ]
    if not masks:
        return {"services": []}
    try:
        return list_article22_downlink_possibilities_for_masks(masks)
    except Exception:  # noqa: BLE001
        return {"services": []}


_SERVICE_DESC = {
    "FSS": "Fixed Satellite Service · reference 1.2 m ES antenna pattern.",
    "BSS": "Broadcasting Satellite Service · BSS reference antenna pattern.",
}
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
_ARTIFICIAL_PREC_DESC = {
    "auto": "Engine decides from the SRS (rpt period / f_precess / plane count).",
    "on": "Force artificial RAAN precession on.",
    "off": "Force artificial RAAN precession off.",
}

st.set_page_config(page_title="Single-entry · SHARC-Orbit", page_icon=":material/looks_one:", layout="wide")
theme.inject()

st.title("Single-entry EPFD↓")
help_expander("single_entry")
tour.maybe_render("single_entry")
st.caption("ITU-R S.1503-4 — WCGA search + EPFD↓ simulation + Article 22 compliance.")

systems = storage.list_systems()
orphans = storage.list_orphan_uploads()

if not systems and not orphans and not storage.list_uploads():
    st.warning("No filings registered. Use **Upload** first.")
    st.page_link("pages/1_Upload.py", label="Upload", icon=":material/upload:")
    st.stop()

if orphans:
    with st.container(border=True):
        st.markdown("**Filings without a registered system** — quick-register with engine default mask:")
        for u in orphans:
            cols = st.columns([5, 1])
            with cols[0]:
                st.write(f"`{u['id']}` · {u['label']} · {u.get('network_name') or '—'}")
            with cols[1]:
                if st.button("Register default", icon=":material/add_circle:",
                              key=f"orph_{u['id']}"):
                    storage.ensure_default_system(u["id"])
                    st.rerun()

if not systems:
    st.info("No systems yet. Use the box above or **Upload** to register one.")
    st.stop()

prev = use_persisted_state("s1503.form", {
    "system_id": systems[0]["id"],
    "num_time_steps": 3600,
    "time_step_s": 1.0,
    # None = auto: use the filing's ε₀ (SRS grp.elev_min). S.1503-4 takes ε₀
    # from the non-GSO system params — don't hardcode a default that overrides it.
    "min_elevation_deg": None,
    "service": "FSS",
    "es_antenna_diameter_m": 1.2,
    "wcga_s1503": True,
    "s1503_step_deg": 1.0,
    "wcga_no_mask_symmetry": False,
    "s1503_trail_all_points": False,
    "gso_longitude_mode": "arc_optimal",
    "alpha_method": "analytical",
    "dual_time_step_mode": "on",
    "fine_time_step_s": "",
    "wcg_manual": False,
    "wcg_manual_es_lat": "",
    "wcg_manual_es_lon": "",
    "wcg_manual_gso_lon": "",
})

st.subheader("1. System")
# Shared selection across pages — fall back to per-page form memory, then
# to first system. Picking here propagates to Mask Viewer / Aggregate.
_shared_sid = current_system_id(default=prev.get("system_id"))
_default_idx = next(
    (i for i, s in enumerate(systems) if s["id"] == _shared_sid),
    next((i for i, s in enumerate(systems)
          if s["id"] == prev.get("system_id")), 0),
)
sel_sys = st.selectbox(
    "Pick a system (filing × notice)",
    options=[s["id"] for s in systems],
    index=max(0, _default_idx),
    format_func=lambda i: next(
        (f"{s.get('upload_label') or s['upload_id']} · ntc {s.get('ntc_id') or '—'}"
         for s in systems if s["id"] == i),
        i,
    ),
)
if sel_sys != _shared_sid:
    set_current_system_id(sel_sys)

# ── Mutually-exclusive configurations (R2 / AP4 A.4.b.3.b-d) ─────────────
# One SRS db = one configuration. When the filing declares multi_config_type
# = M, show which configuration this db carries, list the sibling config dbs
# registered for the same notice, and offer launching one run per config.
_mc_run_all = False
_mc_siblings: list[dict] = []
_selrow = next((s for s in systems if s["id"] == sel_sys), None)
if _selrow and _selrow.get("srs_path"):
    _mc = srs_inspect.multi_config_info(_selrow["srs_path"], _selrow.get("ntc_id"))
    if _mc.get("is_multi"):
        _lbl = _mc.get("config_label")
        _nbr = int(_mc.get("nbr_config") or 0)
        # Sibling systems: same notice, different registered db/config.
        _sib_all = [s for s in systems
                    if s.get("ntc_id") == _selrow.get("ntc_id")
                    and s["id"] != sel_sys and s.get("srs_path")]
        _seen_cfgs = {_lbl}
        for s in _sib_all:
            info = srs_inspect.multi_config_info(s["srs_path"], s.get("ntc_id"))
            c = info.get("config_label")
            if c is not None and c not in _seen_cfgs:
                _seen_cfgs.add(c)
                _mc_siblings.append({"system": s, "config_label": c})
        _missing = ([str(k) for k in range(1, _nbr + 1)
                     if k not in _seen_cfgs] if _nbr else [])
        st.warning(
            f"**Multi-configuration notice** (`multi_config_type=M`): this db "
            f"carries configuration **{_lbl if _lbl is not None else '?'}** of "
            f"**{_nbr or '?'}** (label via {_mc.get('source') or 'not found'}). "
            "EPFD must be evaluated **per configuration** — results of sibling "
            "configurations must not be aggregated (S.1503-4 §D2.1)."
        )
        if _mc_siblings:
            _mc_run_all = st.checkbox(
                f"Also launch the {len(_mc_siblings)} registered sibling "
                f"configuration(s) with the same parameters "
                f"(configs {sorted(x['config_label'] for x in _mc_siblings)})",
                value=False, key="mc_run_all",
            )
        if _missing:
            st.caption(
                f"Configuration(s) {', '.join(_missing)} of this notice are "
                "not registered — upload their SRS db(s) to evaluate them."
            )

# ── Article 22 downlink scenario (optional) ──────────────────────────────
# Pick the normative EPFD↓ limit configuration to test against. A chosen
# leaf overrides Service / ES antenna / reference BW / simulation frequency
# and pins the PFD mask. Left on Auto, the engine resolves from the band.
art22_leaf = None
_carried_art22 = None  # Art.22 scenario carried from a reloaded run
_sysrow = storage.get_system(sel_sys)
with st.expander("Article 22 downlink scenario (limits) — optional", expanded=False):
    st.caption(
        "Normative EPFD↓ possibilities for this filing's PFD band(s): "
        "**service → frequency run → ES antenna / reference BW**. Picking a "
        "leaf **overrides** Service, ES antenna, reference BW and simulation "
        "frequency below, and pins the corresponding PFD `mask_id`. Leave on "
        "**Auto** to let the engine resolve from the filing band."
    )
    _tree = _art22_tree(_sysrow["srs_path"], _sysrow.get("ntc_id")) if _sysrow else {"services": []}
    _services = _tree.get("services") or []
    if not _services:
        st.caption("No Article 22 downlink possibility found for this filing's "
                   "PFD band(s) (engine will auto-resolve).")
    else:
        _svc_opts = ["Auto (engine resolves)"] + [s["service"] for s in _services]
        _svc_pick = st.selectbox("Service", _svc_opts, key="art22_svc")
        if _svc_pick != "Auto (engine resolves)":
            _svc_node = next(s for s in _services if s["service"] == _svc_pick)
            _freqs = _svc_node["frequencies"]
            _fi = st.selectbox(
                "Frequency run", options=list(range(len(_freqs))),
                format_func=lambda i: (
                    f"{_freqs[i]['label']} · {_freqs[i]['rr_reference']} · "
                    f"R{','.join(str(r) for r in _freqs[i].get('regions', [])) or '—'} · "
                    f"mask(s) {_freqs[i].get('mask_ids') or '—'}"
                ),
                key="art22_freq",
            )
            _fnode = _freqs[min(_fi, len(_freqs) - 1)]
            _opts = _fnode["options"]
            _oi = st.selectbox(
                "ES antenna / reference BW", options=list(range(len(_opts))),
                format_func=lambda i: f"{_opts[i]['label']} · {_opts[i]['rf_pattern_rr']}",
                key="art22_opt",
            )
            art22_leaf = _opts[min(_oi, len(_opts) - 1)]
            _mref = art22_leaf.get("mask_ref") or {}
            st.success(
                f"Scenario: **{art22_leaf['service']}** · run "
                f"**{art22_leaf['frequency_run_mhz']:.2f} MHz** · ES "
                f"**{art22_leaf['rf_diam_m']:.2f} m** · "
                f"**{art22_leaf['reference_bandwidth_khz']:.0f} kHz** · "
                f"{art22_leaf['rr_reference']} · PFD mask "
                f"**{_mref.get('mask_id', '—')}**"
            )

    # Carry an Article 22 scenario reloaded from a past run (Runs → reload).
    # Active only while no leaf is picked above; uncheck to fall back to auto.
    # Checked by default ONLY right after the Runs → "Reload config" flow
    # (`art22_from_reload`), never for values left over from an old launch.
    if art22_leaf is None:
        _rbw = prev.get("reference_bandwidth_khz")
        _sfreq = prev.get("simulation_frequency_ghz")
        if _rbw is not None or _sfreq is not None:
            if st.checkbox("Apply reloaded Article 22 scenario",
                            value=bool(prev.get("art22_from_reload")),
                            key="art22_carry",
                            help="Scenario from a reloaded run. Uncheck for "
                                 "engine auto-resolve."):
                _carried_art22 = {
                    "reference_bandwidth_khz": _rbw,
                    "simulation_frequency_ghz": _sfreq,
                    "mask_id": prev.get("mask_id"),
                }
                st.caption(
                    "Reloaded scenario: "
                    f"run {float(_sfreq) * 1000.0:.2f} MHz · {float(_rbw):.0f} kHz"
                    if (_sfreq is not None and _rbw is not None) else
                    "Reloaded Article 22 scenario active."
                )

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
    if _carried_art22.get("mask_id") is not None:
        _bits.append(f"PFD mask {int(_carried_art22['mask_id'])}")
    st.warning(
        "A **reloaded Article 22 scenario** will be applied to this launch: "
        + (" · ".join(_bits) or "values from the reloaded run")
        + ". Uncheck **Apply reloaded Article 22 scenario** (expander above) "
        "to let the engine auto-resolve.",
        icon=":material/warning:",
    )

# When an Article 22 scenario leaf is picked in section 1, mirror its
# Service + ES antenna into the section-2 widgets so they show what will run.
_forced_service = str(art22_leaf["service"]).upper() if art22_leaf is not None else None
_forced_diam_m = float(art22_leaf["rf_diam_m"]) if art22_leaf is not None else None

with st.form("s1503_form"):
    st.subheader("2. Simulation parameters")
    st.caption("Empty fields = engine defaults (auto).")
    col1, col2 = st.columns(2)
    with col1:
        num_steps = st.text_input(
            "Number of time steps",
            value=str(prev.get("num_time_steps") or ""),
            placeholder="auto (Obs2 ref. 518 400)",
            help="Engine key: `num_time_steps`. Normative Obs2 reference = "
                 "518 400. Empty = engine default.",
        )
        dt = st.text_input(
            "Coarse time step (s)",
            value=str(prev.get("time_step_s") or ""),
            placeholder="auto",
            help="Engine key: `time_step_s`. Sampling period of the EPFD↓ "
                 "time series. Empty = engine default (1 s).",
        )
        min_elev = st.text_input(
            "Minimum ES elevation ε₀ (°)",
            value=str(prev.get("min_elevation_deg") or ""),
            placeholder="auto (from filing)",
            help="Engine key: `min_elevation_deg` = S.1503-4 ε₀ (min elevation "
                 "of the non-GSO satellite, Step 18 bullet ①). Empty = auto: "
                 "use the filing's value (SRS `grp.elev_min`); falls back to 5° "
                 "if the filing declares none. Enter a value only to OVERRIDE "
                 "the filing. (Engine uses a scalar; S.1503-4 ε₀ is ε₀[lat,az].)",
        )
    with col2:
        _svc_default = (_forced_service or str(prev.get("service", "FSS"))).upper()
        service = select_described(
            "Service", ["FSS", "BSS"], _SERVICE_DESC,
            index=0 if _svc_default == "FSS" else 1,
            help="Engine key: `service`. Selects the reference ES antenna "
                 "pattern. Set automatically by the Article 22 scenario when "
                 "one is picked in section 1.",
        )
        _diam_default = (f"{_forced_diam_m:.2f}" if _forced_diam_m is not None
                         else str(prev.get("es_antenna_diameter_m") or ""))
        diam = st.text_input(
            "ES antenna diameter (m)",
            value=_diam_default,
            placeholder="1.2 (default)",
            help="Engine key: `es_antenna_diameter_m`. Empty = 1.2 m default. "
                 "Set automatically by the Article 22 scenario when one is "
                 "picked in section 1.",
        )
        _freq_default = (
            (f"{float(art22_leaf['frequency_run_ghz']):.6f}".rstrip("0").rstrip("."))
            if art22_leaf is not None and art22_leaf.get("frequency_run_ghz")
            else str(prev.get("simulation_frequency_ghz") or "")
        )
        sim_freq = st.text_input(
            "Simulation frequency (GHz)",
            value=_freq_default,
            placeholder="auto (band start + RefBW/2)",
            help="Engine key: `simulation_frequency_ghz`. The single frequency "
                 "the EPFD↓ run is evaluated at — it selects the PFD mask, the "
                 "Article 22 limit curve, θ3dB and (when enabled) the emitting-"
                 "satellite band filter. Empty = engine auto-resolves from the "
                 "filing band. Overridden by the Article 22 scenario when one is "
                 "picked in section 1.",
        )
        if art22_leaf is not None:
            st.caption("↑ Service, ES antenna & frequency set by the Article 22 scenario.")

    with st.expander("3. WCG search (S.1503-4 §D.3)", expanded=False):
        wcga_s1503 = st.checkbox(
            "Use S.1503-4 WCGA algorithm",
            value=bool(prev.get("wcga_s1503", True)),
            help="Engine key: `wcga_s1503`. Normative search per S.1503-4 "
                 "§D.3 (uniform grid). Disable only when you want a manual WCG.",
        )
        s1503_step = st.text_input(
            "WCGA grid step (°)",
            value=str(prev.get("s1503_step_deg") or ""),
            placeholder="auto",
            help="Engine key: `s1503_step_deg`. Latitude step of the WCGA "
                 "grid. Smaller = finer search, slower.",
        )
        col_a, col_b = st.columns(2)
        with col_a:
            wcga_no_mask_symmetry = st.checkbox(
                "Full θ — no mask symmetry",
                value=bool(prev.get("wcga_no_mask_symmetry", False)),
                help="Engine key: `wcga_no_mask_symmetry`. By default the WCGA "
                     "uses θ ∈ [0, π] assuming the mask is symmetric in Δlon. "
                     "Enable for asymmetric masks (slower).",
            )
            s1503_trail = st.checkbox(
                "Export all visited points",
                value=bool(prev.get("s1503_trail_all_points", False)),
                help="Engine key: `s1503_trail_all_points`. Save every WCGA "
                     "trial point to the run artifacts (for debugging / "
                     "visualisation). Bigger output files.",
            )
        with col_b:
            gso_lon_mode = select_described(
                "GSO longitude mode",
                ["arc_optimal", "es_meridian"], _GSO_LON_DESC,
                index=0 if prev.get("gso_longitude_mode") == "arc_optimal" else 1,
                help="Engine key: `gso_longitude_mode`.",
            )
            # NOTE: no dynamic `disabled=` here — widgets inside a form don't
            # rerun until submit, so the enabled state could never react to
            # the GSO-mode pick above. The engine simply ignores
            # `alpha_method` when GSO mode = es_meridian.
            alpha_method = select_described(
                "α computation method",
                ["sweep", "analytical"], _ALPHA_DESC,
                index=0 if prev.get("alpha_method") == "sweep" else 1,
                help="Engine key: `alpha_method`. Ignored when GSO mode = "
                     "es_meridian (irrelevant in that case).",
            )

        # "Emulate S.1503-2 (ITU BR GIBC reference)" widget hidden — the engine
        # always runs the literal S.1503-4 path. Forced OFF (unchecked): sets
        # `emulate_s1503_2=False` → `strict_exclusion_zone=False`, so the WCGD
        # keeps the gain OR-branch (§D3.1.2) and the temporal EPFD↓ follows
        # Step 18 (no elGSO term). Re-expose the checkbox to reproduce
        # EPFDRESULTS_*.mdb (diverges from S.1503-4).
        emulate_s1503_2 = False

        apply_table8_egso = st.checkbox(
            "Apply Table 8 εGSO gate (S.1503-4)",
            value=not bool(prev.get("disable_gso_min_elevation", False)),
            help="Engine key: `disable_gso_min_elevation` (= not this box). "
                 "Table 8 εGSO is the minimum GSO-arc elevation (20° ≥17 GHz, "
                 "10° <17 GHz) in the WCGD store criterion AND-branch (§D3.1.2) "
                 "and the temporal gate. "
                 "DEFAULT ON: apply εGSO per the literal S.1503-4 (excludes "
                 "victim ES where the GSO arc is below the threshold). "
                 "OFF: Table 8 is NOT applied — the WCGA may place the "
                 "worst-case ES at high latitude (e.g. ≈66°), matching the ITU "
                 "BR / S.1503-2 reference; deviates from the literal S.1503-4.",
        )

    with st.expander("4. Manual WCG (overrides WCGA)", expanded=False):
        wcg_manual = st.checkbox(
            "Use fixed worst-case geometry",
            value=bool(prev.get("wcg_manual", False)),
            help="Engine key: `wcg_manual`. Skip the WCGA search and run the "
                 "EPFD↓ simulation at the (lat, lon, GSO lon) you specify below.",
        )
        col_c, col_d, col_e = st.columns(3)
        with col_c:
            wm_es_lat = st.text_input(
                "ES latitude (°)",
                value=str(prev.get("wcg_manual_es_lat") or ""),
                help="Engine key: `wcg_manual_es_lat`. Latitude of the fixed "
                     "earth station (-90 … +90).",
            )
        with col_d:
            wm_es_lon = st.text_input(
                "ES longitude (°)",
                value=str(prev.get("wcg_manual_es_lon") or ""),
                help="Engine key: `wcg_manual_es_lon`. Longitude of the fixed "
                     "earth station (-180 … +180).",
            )
        with col_e:
            wm_gso_lon = st.text_input(
                "GSO satellite longitude (°)",
                value=str(prev.get("wcg_manual_gso_lon") or ""),
                help="Engine key: `wcg_manual_gso_lon`. Longitude of the "
                     "victim GSO satellite (-180 … +180).",
            )

    with st.expander("5. Time step (dual mode — S.1503-4 §D.4.7)", expanded=False):
        col_f, col_g = st.columns(2)
        with col_f:
            fine_dt = st.text_input(
                "Fine time step (s)",
                value=str(prev.get("fine_time_step_s") or ""),
                placeholder="auto (S.1503-4 §D4.2 literal)",
                help="Engine key: `fine_time_step_s`. Refinement period used "
                     "around the WCG when dual mode is ON. Empty = literal "
                     "§D.4.2 default.",
            )
        with col_g:
            dual_mode = select_described(
                "Dual time step mode",
                ["on", "off"], _DUAL_TS_DESC,
                index=0 if prev.get("dual_time_step_mode", "on") == "on" else 1,
                help="Engine key: `dual_time_step_mode`. Controls whether the "
                     "S.1503-4 §D.4.7 fine refinement runs.",
            )

    with st.expander("6. Orbital dynamics (station keeping / precession — S.1503-4 §D6.3)", expanded=False):
        col_h, col_i = st.columns(2)
        with col_h:
            artificial_prec_mode = select_described(
                "Artificial precession",
                ["off", "on", "auto"], _ARTIFICIAL_PREC_DESC,
                index={"off": 0, "on": 1, "auto": 2}.get(
                    prev.get("artificial_prec_mode", "off"), 0),
                help="Engine key: `artificial_precession`. Default off. "
                     "auto = engine decides from the SRS (rpt period / "
                     "f_precess / plane count). on/off = force.",
            )
            use_prec_mdb = st.checkbox(
                "Use precession from SRS MDB",
                value=bool(prev.get("use_precession_mdb", True)),
                help="Engine key: `use_precession_mdb`. Override RAAN rate with "
                     "the SRS `precession_deg_day` value (when present).",
            )
            force_gmst0_zero = st.checkbox(
                "Force initial Earth rotation GMST0 = 0",
                value=bool(prev.get("force_gmst0_zero", False)),
                help="Engine key: `earth_rotation_initial_deg` (override). "
                     "OFF (default) = use the SRS-inferred GMST0 (RAAN − long_asc). "
                     "ON = force GMST0 = 0 for THIS run only — the filing's value "
                     "is left intact (non-destructive override). GMST0 rigidly "
                     "rotates the WCG longitudes (ES/GSO); EPFD, latitude and the "
                     "ES−GSO Δlon are unchanged. Useful to align WCG longitudes "
                     "with a reference run that used a different frame.",
            )
        with col_i:
            apply_sk = st.checkbox(
                "Apply Wdelta (station keeping)",
                value=bool(prev.get("apply_station_keeping", True)),
                help="Engine key: `apply_station_keeping_wdelta`. S.1503-4 §D6.3.4: "
                     "applies a RAAN offset ±Wdelta·(2t/T_run−1) over the run, "
                     "sweeping the node longitude. Reads ±Wdelta from the SRS "
                     "(_keep_range_deg) when the system declares station keeping.",
            )
            restrict_emitters = st.checkbox(
                "Only satellites emitting in the sim band",
                value=bool(prev.get("restrict_emitters_to_sim_band", True)),
                help="Engine key: `restrict_emitters_to_sim_band`. Simulate only "
                     "the satellites whose transmitting group (SRS `grp`, "
                     "emi_rcp='E') covers the simulation frequency, resolved via "
                     "`grp` ⋈ `mask_lnk1`. DEFAULT ON. Off = whole constellation "
                     "(legacy). Falls back to the full constellation if no group "
                     "matches.",
            )

    with st.expander("7. Track duration (MIN_DURATION — S.1503-4 §D5.1.4.2)", expanded=False):
        st.caption(
            "Sliding-window variant. When the SRS `sat_oper` declares "
            "MIN_DURATION ≠ 0 (minimum time the ES tracks a satellite), the "
            "engine runs the §D5.1.4.2 algorithm automatically. Set a value "
            "below to **force** or **override** MIN_DURATION for all latitudes "
            "(useful for manual systems or what-if studies). 0 / empty = use "
            "the filing's own value (or the standard §D5.1.4.1 path)."
        )
        min_duration_s = st.text_input(
            "MIN_DURATION (s) — override",
            value=str(prev.get("min_duration_s") or ""),
            placeholder="auto (from SRS sat_oper; 0 = standard path)",
            help="Engine key: `min_duration_s`. > 0 forces the sliding-window "
                 "variant with N_SW = ⌊MIN_DURATION/T_fine⌋ fine steps per "
                 "window. Cost scales with N_TW ≈ N_SW/N_MSL window sets — a "
                 "large MIN_DURATION with a small T_fine is expensive.",
        )

    # ── Workload + runtime estimate ────────────────────────────────────────
    from lib import estimator as _est
    from lib import srs_inspect as _si
    _cal = _est.calibrate_from_runs()
    def _i(s):
        try:
            return int(float((s or "").strip()))
        except (ValueError, TypeError):
            return None
    def _f(s):
        try:
            return float((s or "").strip())
        except (ValueError, TypeError):
            return None
    _step = _f(s1503_step) or 1.0
    # Real N_sat from the selected system's MDB (counts orbit.nbr_sat_pl)
    _sys_row = storage.get_system(sel_sys) or {}
    _n_sat_real = _si.count_satellites(
        _sys_row.get("srs_path", ""), _sys_row.get("ntc_id"),
    )
    _n_sat = _n_sat_real if _n_sat_real > 0 else 20

    # ── S.1503-4 §D4 time step / NSTEPS preview (same calc as the engine) ──
    # The engine resolves the frequency run + Article 22 limits (→ Nmin) from the
    # filing PFD band + service + ES diameter; pass those, plus the pinned
    # scenario frequency/BW when a scenario was selected, so the preview matches.
    _fmin = _fmax = None
    try:
        _bands = _si.frequency_bands(_sys_row.get("srs_path", ""),
                                     _sys_row.get("ntc_id"))
        _pfd = [m for m in _bands.get("masks", []) if m.get("type") == "PFD"]
        if _pfd:
            _fmin = min(float(m["freq_min_ghz"]) for m in _pfd)
            _fmax = max(float(m["freq_max_ghz"]) for m in _pfd)
    except Exception:  # noqa: BLE001
        _fmin = _fmax = None
    _sim_freq = None
    if art22_leaf is not None and art22_leaf.get("frequency_run_ghz"):
        _sim_freq = float(art22_leaf["frequency_run_ghz"])
    elif _f(sim_freq):  # manual "Simulation frequency (GHz)" field
        _sim_freq = _f(sim_freq)
    elif prev.get("simulation_frequency_ghz"):
        _sim_freq = float(prev["simulation_frequency_ghz"])
    _ref_bw = (float(art22_leaf["reference_bandwidth_khz"])
               if art22_leaf is not None and art22_leaf.get("reference_bandwidth_khz")
               else 40.0)
    _diam_m = _f(diam) or (_forced_diam_m or 1.2)
    _tsp = _est.preview_time_step(
        srs_path=_sys_row.get("srs_path", ""),
        ntc_id=_sys_row.get("ntc_id"),
        diameter_m=_diam_m, service=service,
        freq_min_ghz=_fmin, freq_max_ghz=_fmax,
        simulation_frequency_ghz=_sim_freq,
        reference_bandwidth_khz=_ref_bw,
        repeating=None,
        nhit=int(prev.get("s1503_nhit", 16) or 16),
        phi_coarse_deg=float(prev.get("s1503_phi_coarse_deg", 1.5) or 1.5),
        artificial_precession=(artificial_prec_mode == "on"),
        min_elevation_deg=_f(min_elev) or 10.0,
        num_steps_override=_i(num_steps),
        fine_step_override=_f(fine_dt),
        coarse_step_override=_f(dt),
    )
    # Feed the real NSTEPS into the wall-time estimate when available.
    _nsteps = _tsp.nsteps if _tsp.ok else (_i(num_steps) or 86_400)
    est = _est.estimate_single_entry(n_sat=_n_sat, n_time_steps=_nsteps,
                                       s1503_step_deg=_step)
    with st.container(border=True):
        cal_tag = (
            f" · calibrated from {_cal.get('n_samples', 0)} past run(s)"
            if _cal.get("calibrated") else " · heuristic baseline"
        )
        st.markdown(
            f"**Estimated workload** · N_sat = {_n_sat}"
            + (" (from MDB)" if _n_sat_real > 0 else " (fallback)")
            + cal_tag
        )
        if _tsp.ok:
            t1, t2, t3, t4, t5 = st.columns(5)
            _above = _tsp.nsteps - _tsp.nmin
            t1.metric(
                "Time steps (N)", f"{_tsp.nsteps:,}",
                delta=f"{_above:+,} vs Nmin" if _above else "= Nmin",
                delta_color="off",
            )
            t2.metric("Min. steps (Nmin)", f"{_tsp.nmin:,}",
                      help="S.1503-4 §D4.6, Table 13 — statistical floor "
                           "(NS·100/(100−p), from the Article 22 % of time).")
            t3.metric("Δt fine (§D4.2)", _est._fmt_step(_tsp.fine_step_s))
            t4.metric("Δt coarse (§D4.7)", _est._fmt_step(_tsp.coarse_step_s),
                      help=f"= fine × Ncoarse ({_tsp.ncoarse})")
            t5.metric("Sim. duration", _est._fmt_seconds(_tsp.duration_s))
            st.caption("S.1503-4 §D4 · " + " · ".join(_tsp.notes))
        else:
            st.caption(f"Time step preview unavailable — {_tsp.error}. "
                       "NSTEPS/Δt will be computed by the engine at launch.")
        c1, c2 = st.columns([2, 3])
        with c1:
            st.metric("Total sims", est.n_sims)
            st.metric("Wall time (≈)", _est._fmt_seconds(est.wall_seconds))
        with c2:
            st.write("\n".join(f"• {n}" for n in est.notes))
        st.caption(
            "Form fields don't recompute the estimate while you type. Press "
            "**Recalculate** to refresh N / Δt with the current values "
            "(without launching)."
        )
        st.form_submit_button("🔄 Recalculate estimate")

    submit = st.form_submit_button("Launch run", type="primary", icon=":material/play_circle:")

if submit:
    def _f(s):
        s = (s or "").strip()
        try:
            return float(s)
        except (ValueError, TypeError):
            return None

    def _i(s):
        s = (s or "").strip()
        try:
            return int(float(s))
        except (ValueError, TypeError):
            return None

    params: dict = {"service": service}

    def _set(k, v):
        if v is not None:
            params[k] = v

    _set("num_time_steps", _i(num_steps))
    _set("time_step_s", _f(dt))
    _set("min_elevation_deg", _f(min_elev))
    d = _f(diam)
    if d is not None and d > 0:
        params["es_antenna_diameter_m"] = d
    params["wcga_s1503"] = bool(wcga_s1503)
    _set("s1503_step_deg", _f(s1503_step))
    params["wcga_no_mask_symmetry"] = bool(wcga_no_mask_symmetry)
    params["s1503_trail_all_points"] = bool(s1503_trail)
    params["gso_longitude_mode"] = gso_lon_mode
    params["alpha_method"] = alpha_method
    params["wcg_manual"] = bool(wcg_manual)
    _set("wcg_manual_es_lat", _f(wm_es_lat))
    _set("wcg_manual_es_lon", _f(wm_es_lon))
    _set("wcg_manual_gso_lon", _f(wm_gso_lon))
    _set("fine_time_step_s", _f(fine_dt))
    params["dual_time_step_mode"] = dual_mode
    # Track-duration override (§D5.1.4.2). Only sent when > 0.
    _md_val = _f(min_duration_s)
    if _md_val is not None and _md_val > 0:
        params["min_duration_s"] = _md_val
    # Orbital dynamics. artificial_precession: only sent when forced (auto →
    # leave unset so the engine auto-detects from the SRS).
    if artificial_prec_mode == "on":
        params["artificial_precession"] = True
    elif artificial_prec_mode == "off":
        params["artificial_precession"] = False
    params["use_precession_mdb"] = bool(use_prec_mdb)
    params["force_gmst0_zero"] = bool(force_gmst0_zero)
    params["apply_station_keeping"] = bool(apply_sk)
    params["restrict_emitters_to_sim_band"] = bool(restrict_emitters)
    params["emulate_s1503_2"] = bool(emulate_s1503_2)
    # Table 8 εGSO gate: checkbox unchecked (default) → disable it.
    params["disable_gso_min_elevation"] = not bool(apply_table8_egso)

    # Article 22 scenario leaf overrides service / ES antenna / ref BW /
    # frequency run and pins the PFD mask (see section 1 selector).
    if art22_leaf is not None:
        params["service"] = str(art22_leaf["service"])
        params["es_antenna_diameter_m"] = float(art22_leaf["rf_diam_m"])
        params["reference_bandwidth_khz"] = float(art22_leaf["reference_bandwidth_khz"])
        params["simulation_frequency_ghz"] = float(art22_leaf["frequency_run_ghz"])
        _mref = art22_leaf.get("mask_ref") or {}
        if _mref.get("mask_id") is not None:
            params["mask_id"] = int(_mref["mask_id"])
    elif _carried_art22 is not None:
        # Re-apply an Art.22 scenario reloaded from a past run.
        if _carried_art22.get("reference_bandwidth_khz") is not None:
            params["reference_bandwidth_khz"] = float(_carried_art22["reference_bandwidth_khz"])
        if _carried_art22.get("simulation_frequency_ghz") is not None:
            params["simulation_frequency_ghz"] = float(_carried_art22["simulation_frequency_ghz"])
        if _carried_art22.get("mask_id") is not None:
            params["mask_id"] = int(_carried_art22["mask_id"])

    # Manual simulation frequency: applies only when no Article 22 scenario
    # (or reloaded scenario) already pinned the frequency run.
    if params.get("simulation_frequency_ghz") is None:
        _mf = _f(sim_freq)
        if _mf is not None and _mf > 0:
            params["simulation_frequency_ghz"] = _mf

    set_persisted_state("s1503.form", {
        "reference_bandwidth_khz": params.get("reference_bandwidth_khz"),
        "simulation_frequency_ghz": params.get("simulation_frequency_ghz"),
        "mask_id": params.get("mask_id"),
        "system_id": sel_sys,
        "num_time_steps": _i(num_steps), "time_step_s": _f(dt),
        "min_elevation_deg": _f(min_elev), "service": service,
        "es_antenna_diameter_m": _f(diam),
        "wcga_s1503": bool(wcga_s1503), "s1503_step_deg": _f(s1503_step),
        "wcga_no_mask_symmetry": bool(wcga_no_mask_symmetry),
        "s1503_trail_all_points": bool(s1503_trail),
        "gso_longitude_mode": gso_lon_mode, "alpha_method": alpha_method,
        "wcg_manual": bool(wcg_manual),
        "wcg_manual_es_lat": wm_es_lat, "wcg_manual_es_lon": wm_es_lon,
        "wcg_manual_gso_lon": wm_gso_lon,
        "fine_time_step_s": fine_dt, "dual_time_step_mode": dual_mode,
        "min_duration_s": min_duration_s,
        "artificial_prec_mode": artificial_prec_mode,
        "use_precession_mdb": bool(use_prec_mdb),
        "apply_station_keeping": bool(apply_sk),
        "restrict_emitters_to_sim_band": bool(restrict_emitters),
    })

    run_id = launcher.launch_s1503(system_id=sel_sys, params=params)
    set_current_run_id(run_id)
    st.toast(f"Run `{run_id}` launched", icon=":material/play_circle:")

    # Per-configuration launches (R2): same parameters, sibling config dbs.
    # Each run stays independent — no EPFD aggregation across configurations.
    if _mc_run_all and _mc_siblings:
        for _sib in _mc_siblings:
            _sp = dict(params)
            _sp["system_id"] = _sib["system"]["id"]
            _rid = launcher.launch_s1503(system_id=_sib["system"]["id"], params=_sp)
            st.toast(
                f"Run `{_rid}` launched (config {_sib['config_label']})",
                icon=":material/play_circle:",
            )

    # Run id travels via the kwarg — `st.switch_page` resets st.query_params.
    st.switch_page("pages/7_Status.py", query_params={"run_id": run_id})
