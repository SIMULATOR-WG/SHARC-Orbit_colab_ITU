"""Country-constrained single-entry — WCGA (S.1503-4) with ES in countries.

Same WCGA algorithm as **Single-entry** (§D.3.1), but only geometries whose
earth station falls inside the selected countries are accepted. Not an
ES×GSO lat/lon grid search.
"""
from __future__ import annotations

import streamlit as st

from lib import launcher, srs_inspect, storage, theme, tour
from lib.band_chart import st_bands_chart
from lib.art22_ui import (
    merge_intervals as _merge_intervals,
    intersect_sets as _intersect_sets,
    system_tx_subbands as _tx_subbands,
)
from lib.state import (
    use_persisted_state, set_persisted_state,
    current_system_id, set_current_system_id,
    set_current_run_id,
)
from lib.widgets import select_described
from lib.manual import help_expander


@st.cache_data(show_spinner=False)
def _art22_tree(srs_path: str, ntc_id: str | None):
    try:
        from src.article22_tables import (  # type: ignore[import]
            list_article22_downlink_possibilities_for_masks,
        )
    except Exception:  # noqa: BLE001
        return {"services": []}
    bands = srs_inspect.frequency_bands(srs_path, ntc_id)
    tx_iv = _merge_intervals([
        (float(g["freq_min_ghz"]), float(g["freq_max_ghz"]))
        for g in (bands.get("groups") or [])
        if str(g.get("emi_rcp", "")).upper().startswith(("TX", "E"))
        and g.get("freq_min_ghz") is not None
        and g.get("freq_max_ghz") is not None
    ])
    masks = []
    for m in (bands.get("masks") or []):
        if m.get("type") != "PFD" or m.get("freq_min_ghz") is None:
            continue
        iv = [(float(m["freq_min_ghz"]), float(m["freq_max_ghz"]))]
        if tx_iv:
            iv = _intersect_sets(iv, tx_iv)
        for lo, hi in iv:
            masks.append({
                "mask_id": m["mask_id"],
                "label": f"mask {m['mask_id']}",
                "freq_min_ghz": lo,
                "freq_max_ghz": hi,
            })
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
_RAAN_SWEEP_DESC = {
    "auto": (
        "Per orbit from the filing (S.1503 §D4.6.1): each repeating shell "
        "(f_stn_keep=Y and rpt_period ≥ 1 h) → Ω sweep OFF; each "
        "non-repeating shell → ON. ΔΩ (from the winner) rotates all ON "
        "orbits together; OFF / repeating shells keep filed RAAN."
    ),
    "on": "Force Ω sweep + ΔΩ for every orbit (even locked repeating tracks).",
    "off": "Force Ω sweep OFF for every orbit — keep filed RAAN (ES filter only).",
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
    "auto": "Engine decides from the SRS (rpt period / f_precess / plane count).",
    "on": "Force artificial RAAN precession on.",
    "off": "Force artificial RAAN precession off.",
}


st.set_page_config(
    page_title="Country single-entry · SHARC-Orbit",
    page_icon=":material/public:",
    layout="wide",
)
theme.inject()

st.title("Country-constrained single-entry")
help_expander("country_single_entry")
tour.maybe_render("country_single_entry")
st.caption(
    "Same **WCGA** as Single-entry (S.1503-4 §D.3.1), restricted so the "
    "worst-case ES must lie inside the selected countries — then EPFD↓ + "
    "Article 22 compliance."
)

systems = storage.list_systems()
if not systems:
    st.warning("No filings registered. Use **Upload** first.")
    st.page_link("pages/1_Upload.py", label="Upload", icon=":material/upload:")
    st.stop()

prev = use_persisted_state("country_wcg.form", {
    "system_id": systems[0]["id"],
    "num_time_steps": 3600,
    "time_step_s": 1.0,
    # None = auto: use the filing's ε₀ (SRS grp.elev_min) — same as Single-entry.
    "min_elevation_deg": None,
    "service": "FSS",
    "es_antenna_diameter_m": 1.2,
    "wcga_s1503": True,
    "s1503_step_deg": 1.0,
    "wcga_no_mask_symmetry": True,
    "s1503_trail_all_points": False,
    "gso_longitude_mode": "arc_optimal",
    "alpha_method": "analytical",
    "dual_time_step_mode": "on",
    "fine_time_step_s": "",
    "itu_software": "itu_epfd",
    "artificial_prec_mode": "off",
    "use_precession_mdb": True,
    "force_gmst0_zero": False,
    "apply_station_keeping": True,
    "restrict_emitters_to_sim_band": True,
    "disable_gso_min_elevation": False,
    "min_duration_s": "",
    "country_codes": ["BRA"],
    "country_raan_sweep": "auto",
})
if not prev.get("_full_theta_default_v1"):
    prev = dict(prev)
    prev["wcga_no_mask_symmetry"] = True
    prev["_full_theta_default_v1"] = True
    set_persisted_state("country_wcg.form", prev)

st.subheader("1. System")
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
        (f"{s.get('sat_name') or s.get('upload_label') or s['upload_id']}"
         f" · ntc {s.get('ntc_id') or '—'}"
         for s in systems if s["id"] == i),
        i,
    ),
)
if sel_sys != _shared_sid:
    set_current_system_id(sel_sys)

# ── Mutually-exclusive configurations (same as Single-entry) ──────────────
_mc_run_all = False
_mc_siblings: list[dict] = []
_selrow = next((s for s in systems if s["id"] == sel_sys), None)
if _selrow and _selrow.get("srs_path"):
    _mc = srs_inspect.multi_config_info(_selrow["srs_path"], _selrow.get("ntc_id"))
    if _mc.get("is_multi"):
        _lbl = _mc.get("config_label")
        _nbr = int(_mc.get("nbr_config") or 0)
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
        st.info(
            srs_inspect.format_multi_config_notice(_mc),
            icon=":material/call_split:",
        )
        if _mc_siblings:
            _mc_run_all = st.checkbox(
                f"Also launch the {len(_mc_siblings)} registered sibling "
                f"configuration(s) with the same parameters "
                f"(configs {sorted(x['config_label'] for x in _mc_siblings)})",
                value=False, key="cwcg_mc_run_all",
            )
        if _missing:
            st.caption(
                f"Configuration(s) {', '.join(_missing)} of this notice are "
                "not registered — upload their SRS db(s) to evaluate them."
            )

_navrow = next((s for s in systems if s["id"] == sel_sys), None)
if _navrow and _navrow.get("srs_path"):
    try:
        _fb = srs_inspect.frequency_bands(_navrow["srs_path"],
                                          _navrow.get("ntc_id"))
    except Exception:  # noqa: BLE001
        _fb = {"masks": [], "groups": []}

    def _grp_iv(prefixes: tuple[str, ...]) -> list[tuple[float, float]]:
        return _merge_intervals([
            (float(g["freq_min_ghz"]), float(g["freq_max_ghz"]))
            for g in (_fb.get("groups") or [])
            if str(g.get("emi_rcp", "")).upper().startswith(prefixes)
            and g.get("freq_min_ghz") is not None
            and g.get("freq_max_ghz") is not None
        ])

    _pfd_iv = _merge_intervals([
        (float(m["freq_min_ghz"]), float(m["freq_max_ghz"]))
        for m in (_fb.get("masks") or [])
        if m.get("type") == "PFD" and m.get("freq_min_ghz") is not None
    ])
    st_bands_chart(
        [
            {"label": "Emission (Tx)", "bands": _grp_iv(("TX", "E")),
             "kind": "tx", "sublabel": "SRS grp · emi_rcp='E'"},
            {"label": "Reception (Rx)", "bands": _grp_iv(("RX", "R")),
             "kind": "tx", "sublabel": "SRS grp · emi_rcp='R'"},
            {"label": "PFD masks (downlink)", "bands": _pfd_iv,
             "kind": "pfd", "sublabel": "mask_info · f_mask='P'"},
        ],
        title="Network band occupancy (SRS)",
        shared_scale=False,
    )

art22_leaf = None
_carried_art22 = None
_sysrow = storage.get_system(sel_sys)
with st.expander("Article 22 downlink scenario (limits) — optional", expanded=False):
    st.caption(
        "Same as Single-entry: pick a leaf to pin service / ES / ref BW / "
        "frequency / PFD mask_id."
    )
    _tree = (_art22_tree(_sysrow["srs_path"], _sysrow.get("ntc_id"))
             if _sysrow else {"services": []})
    _services = _tree.get("services") or []
    if not _services:
        st.caption("No Article 22 downlink possibility found for this filing.")
    else:
        _svc_opts = ["Auto (engine resolves)"] + [s["service"] for s in _services]
        _svc_pick = st.selectbox("Service", _svc_opts, key="cwcg_art22_svc")
        if _svc_pick != "Auto (engine resolves)":
            _svc_node = next(s for s in _services if s["service"] == _svc_pick)
            _freqs = _svc_node["frequencies"]
            _fi = st.selectbox(
                "Frequency run", options=list(range(len(_freqs))),
                format_func=lambda i: (
                    f"{_freqs[i]['label']} · {_freqs[i]['rr_reference']} · "
                    f"mask(s) {_freqs[i].get('mask_ids') or '—'}"
                ),
                key="cwcg_art22_freq",
            )
            _fnode = _freqs[min(_fi, len(_freqs) - 1)]
            _opts = _fnode["options"]
            _oi = st.selectbox(
                "ES antenna / reference BW", options=list(range(len(_opts))),
                format_func=lambda i: f"{_opts[i]['label']} · {_opts[i]['rf_pattern_rr']}",
                key="cwcg_art22_opt",
            )
            art22_leaf = _opts[min(_oi, len(_opts) - 1)]

    if art22_leaf is None:
        _rbw = prev.get("reference_bandwidth_khz")
        _sfreq = prev.get("simulation_frequency_ghz")
        if _rbw is not None or _sfreq is not None:
            if st.checkbox("Apply reloaded Article 22 scenario",
                            value=bool(prev.get("art22_from_reload")),
                            key="cwcg_art22_carry"):
                _carried_art22 = {
                    "reference_bandwidth_khz": _rbw,
                    "simulation_frequency_ghz": _sfreq,
                    "mask_id": prev.get("mask_id"),
                }

_forced_service = str(art22_leaf["service"]).upper() if art22_leaf is not None else None
_forced_diam_m = float(art22_leaf["rf_diam_m"]) if art22_leaf is not None else None

st.subheader("2. Countries (ES domain)")
st.info(
    "Same WCGA as Single-entry (satellite-latitude sweep + θ/φ + α₀/ε₀ "
    "boundaries), with ES restricted to the selected countries. "
    "**RAAN (Ω) sweep** defaults to **per orbit** (§D4.6.1): repeating "
    "shells OFF, others ON; ΔΩ rotates all ON orbits vs the winner. Not a "
    "separate ES×GSO lat/lon grid.",
    icon=":material/info:",
)

from src.country_constrained_wcg import resolve_country_raan_sweep  # noqa: E402
from src.s1588_studies.countries import list_countries  # noqa: E402

_all_countries = list_countries()
_id_to_label = {c["id"]: f"{c['name']} ({c['id']})" for c in _all_countries}
_valid_ids = set(_id_to_label)
_prev_codes = [c for c in (prev.get("country_codes") or ["BRA"]) if c in _valid_ids]
if not _prev_codes and "BRA" in _valid_ids:
    _prev_codes = ["BRA"]

country_codes = st.multiselect(
    "Countries (ISO 3166-1 alpha-3)",
    options=list(_id_to_label.keys()),
    default=_prev_codes,
    format_func=lambda c: _id_to_label.get(c, c),
    help="Worst-case ES must fall inside at least one of these territories.",
)

# Auto-detect from selected filing (§D4.6.1 per orbit; report mix when present).
_sys_for_raan = storage.get_system(sel_sys) or {}
_auto_policy, _raan_det = resolve_country_raan_sweep(
    "auto",
    srs_path=_sys_for_raan.get("srs_path"),
    ntc_id=_sys_for_raan.get("ntc_id"),
)
_rpt_d = _raan_det.get("rpt_period_days")
_rpt_txt = f"{_rpt_d:.2f} d" if _rpt_d else "—"
_n_rep = int(_raan_det.get("n_repeating_planes") or 0)
_n_non = int(_raan_det.get("n_non_repeating_planes") or 0)
_auto_sweep = (_auto_policy is True) or (
    _auto_policy == "auto" and not _raan_det.get("repeating")
)
if _raan_det.get("mixed"):
    st.warning(
        f"Filing: **mixed orbits** — {_n_rep} repeating plane(s) (sweep **OFF**) "
        f"+ {_n_non} non-repeating (sweep **ON**). Auto applies §D4.6.1 "
        f"**per orbit**; ΔΩ rotates all ON orbits relative to the winner.",
        icon=":material/join:",
    )
elif _raan_det.get("repeating"):
    st.success(
        f"Filing: **repeating ground track** "
        f"(f_stn_keep={_raan_det.get('f_stn_keep')}, P_repeat={_rpt_txt}, "
        f"planes={_n_rep}) → auto RAAN sweep **OFF** (preserve filed track).",
        icon=":material/lock:",
    )
else:
    st.warning(
        f"Filing: **non-repeating** "
        f"(f_stn_keep={_raan_det.get('f_stn_keep')}, P_repeat={_rpt_txt}, "
        f"planes={_n_non or _raan_det.get('n_planes') or '—'}) → "
        f"auto RAAN sweep **ON** (worst-case phasing over the country).",
        icon=":material/sync:",
    )

with st.form("country_wcg_form"):
    st.subheader("3. Simulation parameters")
    st.caption(
        "Empty fields = engine defaults (auto). Same keys as Single-entry "
        "(WCGA always on; Manual WCG disabled on this page)."
    )
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
                 "if the filing declares none.",
        )
    with col2:
        _svc_default = (_forced_service or str(prev.get("service", "FSS"))).upper()
        service = select_described(
            "Service", ["FSS", "BSS"], _SERVICE_DESC,
            index=0 if _svc_default == "FSS" else 1,
            help="Engine key: `service`. Selects the reference ES antenna "
                 "pattern. Set automatically by the Article 22 scenario when "
                 "one is picked above.",
        )
        _diam_default = (f"{_forced_diam_m:.2f}" if _forced_diam_m is not None
                         else str(prev.get("es_antenna_diameter_m") or ""))
        diam = st.text_input(
            "ES antenna diameter (m)",
            value=_diam_default,
            placeholder="1.2 (default)",
            help="Engine key: `es_antenna_diameter_m`. Empty = 1.2 m default.",
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
            help="Engine key: `simulation_frequency_ghz`. Empty = engine "
                 "auto-resolves from the filing band.",
        )
        if art22_leaf is not None:
            st.caption("↑ Service, ES antenna & frequency set by the Article 22 scenario.")

    with st.expander("4. WCG search (S.1503-4 §D.3)", expanded=True):
        st.caption(
            "WCGA is always on here (`wcga_s1503=True`); Manual WCG is off. "
            "Only the ES domain is country-filtered — same search knobs as "
            "Single-entry, plus RAAN (Ω) sweep."
        )
        _raan_prev = str(prev.get("country_raan_sweep") or "auto").lower()
        if _raan_prev in ("true", "1"):
            _raan_prev = "on"
        elif _raan_prev in ("false", "0"):
            _raan_prev = "off"
        if _raan_prev not in ("auto", "on", "off"):
            _raan_prev = "auto"
        country_raan_sweep = select_described(
            "RAAN (Ω) sweep",
            ["auto", "on", "off"], _RAAN_SWEEP_DESC,
            index=["auto", "on", "off"].index(_raan_prev),
            help="Country-only. Engine key: `country_raan_sweep`.",
        )
        if country_raan_sweep == "auto":
            if _raan_det.get("mixed"):
                st.caption(
                    f"Effective: **per orbit** — {_n_rep} OFF / {_n_non} ON."
                )
            else:
                st.caption(
                    f"Effective for this filing: sweep "
                    f"**{'ON' if _auto_sweep else 'OFF'}** "
                    f"(all orbits)."
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
                value=bool(prev.get("wcga_no_mask_symmetry", True)),
                help="Engine key: `wcga_no_mask_symmetry`. DEFAULT ON: full θ "
                     "(no Δlon symmetry). Uncheck to restrict θ ∈ [0, π] when "
                     "the mask is symmetric in Δlon (faster).",
            )
            s1503_trail = st.checkbox(
                "Export all visited points",
                value=bool(prev.get("s1503_trail_all_points", False)),
                help="Engine key: `s1503_trail_all_points`. Save every WCGA "
                     "trial point to the run artifacts.",
            )
        with col_b:
            gso_lon_mode = select_described(
                "GSO longitude mode",
                ["arc_optimal", "es_meridian"], _GSO_LON_DESC,
                index=0 if prev.get("gso_longitude_mode") == "arc_optimal" else 1,
                help="Engine key: `gso_longitude_mode`.",
            )
            alpha_method = select_described(
                "α computation method",
                ["sweep", "analytical"], _ALPHA_DESC,
                index=0 if prev.get("alpha_method") == "sweep" else 1,
                help="Engine key: `alpha_method`. Ignored when GSO mode = "
                     "es_meridian.",
            )
        # Same as Single-entry: emulate S.1503-2 stays forced OFF.
        emulate_s1503_2 = False
        apply_table8_egso = st.checkbox(
            "Apply Table 8 εGSO gate (S.1503-4)",
            value=not bool(prev.get("disable_gso_min_elevation", False)),
            help="Engine key: `disable_gso_min_elevation` (= not this box). "
                 "DEFAULT ON: apply εGSO per literal S.1503-4. OFF: may place "
                 "the worst-case ES at high latitude (ITU BR / S.1503-2 style).",
        )

    with st.expander("5. Time step (dual mode — S.1503-4 §D.4.7)", expanded=False):
        col_f, col_g = st.columns(2)
        with col_f:
            fine_dt = st.text_input(
                "Fine time step (s)",
                value=str(prev.get("fine_time_step_s") or ""),
                placeholder="auto (S.1503-4 §D4.2 literal)",
                help="Engine key: `fine_time_step_s`. Refinement period used "
                     "around the WCG when dual mode is ON.",
            )
        with col_g:
            dual_mode = select_described(
                "Dual time step mode",
                ["on", "off"], _DUAL_TS_DESC,
                index=0 if prev.get("dual_time_step_mode", "on") == "on" else 1,
                help="Engine key: `dual_time_step_mode`.",
            )
        itu_software_label = select_described(
            "S.1503-4 §D4 reading (time-step dimensioning)",
            _ITU_SW_OPTIONS, _ITU_SW_DESC,
            index=1 if str(prev.get("itu_software", "itu_epfd")).lower().startswith(
                ("transfinite", "itu")) else 0,
            help="Engine key: `itu_software`. Affects NSTEPS only — not EPFD physics.",
        )
        itu_software = ("itu_epfd" if itu_software_label.startswith("Reading B")
                        else "s1503_4")

    with st.expander("6. Orbital dynamics (station keeping / precession — S.1503-4 §D6.3)", expanded=False):
        col_h, col_i = st.columns(2)
        with col_h:
            artificial_prec_mode = select_described(
                "Artificial precession",
                ["off", "on", "auto"], _ARTIFICIAL_PREC_DESC,
                index={"off": 0, "on": 1, "auto": 2}.get(
                    prev.get("artificial_prec_mode", "off"), 0),
                help="Engine key: `artificial_precession`. Default off. "
                     "auto = engine decides from the SRS.",
            )
            use_prec_mdb = st.checkbox(
                "Use precession from SRS MDB",
                value=bool(prev.get("use_precession_mdb", True)),
                help="Engine key: `use_precession_mdb`.",
            )
            force_gmst0_zero = st.checkbox(
                "Force initial Earth rotation GMST0 = 0",
                value=bool(prev.get("force_gmst0_zero", False)),
                help="Engine key: `earth_rotation_initial_deg` (override).",
            )
        with col_i:
            apply_sk = st.checkbox(
                "Apply Wdelta (station keeping)",
                value=bool(prev.get("apply_station_keeping", True)),
                help="Engine key: `apply_station_keeping_wdelta`. S.1503-4 §D6.3.4.",
            )
            restrict_emitters = st.checkbox(
                "Only satellites emitting in the sim band",
                value=bool(prev.get("restrict_emitters_to_sim_band", True)),
                help="Engine key: `restrict_emitters_to_sim_band`. DEFAULT ON.",
            )

    with st.expander("7. Track duration (MIN_DURATION — S.1503-4 §D5.1.4.2)", expanded=False):
        st.caption(
            "Sliding-window variant. Set a value below to **force** or "
            "**override** MIN_DURATION for all latitudes. 0 / empty = filing "
            "value (or standard §D5.1.4.1 path)."
        )
        min_duration_s = st.text_input(
            "MIN_DURATION (s) — override",
            value=str(prev.get("min_duration_s") or ""),
            placeholder="auto (from SRS sat_oper; 0 = standard path)",
            help="Engine key: `min_duration_s`.",
        )

    # ── Workload + runtime estimate (same preview as Single-entry) ─────────
    from lib import estimator as _est
    from lib import srs_inspect as _si
    _cal = _est.calibrate_from_runs()

    def _i_est(s):
        try:
            return int(float((s or "").strip()))
        except (ValueError, TypeError):
            return None

    def _f_est(s):
        try:
            return float((s or "").strip())
        except (ValueError, TypeError):
            return None

    _step = _f_est(s1503_step) or 1.0
    _sys_row = storage.get_system(sel_sys) or {}
    _n_sat_real = _si.count_satellites(
        _sys_row.get("srs_path", ""), _sys_row.get("ntc_id"),
    )
    _n_sat = _n_sat_real if _n_sat_real > 0 else 20
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
    elif _f_est(sim_freq):
        _sim_freq = _f_est(sim_freq)
    elif prev.get("simulation_frequency_ghz"):
        _sim_freq = float(prev["simulation_frequency_ghz"])
    _ref_bw = (float(art22_leaf["reference_bandwidth_khz"])
               if art22_leaf is not None and art22_leaf.get("reference_bandwidth_khz")
               else 40.0)
    _diam_m = _f_est(diam) or (_forced_diam_m or 1.2)
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
        min_elevation_deg=_f_est(min_elev) or 10.0,
        num_steps_override=_i_est(num_steps),
        fine_step_override=_f_est(fine_dt),
        coarse_step_override=_f_est(dt),
        itu_software=itu_software,
    )
    _nsteps = _tsp.nsteps if _tsp.ok else (_i_est(num_steps) or 86_400)
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
            t2.metric("Min. steps (Nmin)", f"{_tsp.nmin:,}")
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
            "Country RAAN sweep (when ON) adds Ω candidates inside WCGA — "
            "wall-time estimate is a lower bound. Press **Recalculate** to "
            "refresh without launching."
        )
        st.form_submit_button("🔄 Recalculate estimate")

    submit = st.form_submit_button(
        "Launch country-constrained run",
        type="primary",
        icon=":material/play_circle:",
    )

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

    if not country_codes:
        st.error("Select at least one country.", icon=":material/error:")
        st.stop()

    params: dict = {
        "service": service,
        "country_codes": list(country_codes),
        "country_raan_sweep": country_raan_sweep,
        "study_mode": "country_constrained",
        "wcga_s1503": True,
        "wcg_manual": False,
    }

    def _set(k, v):
        if v is not None:
            params[k] = v

    _set("num_time_steps", _i(num_steps))
    _set("time_step_s", _f(dt))
    _set("min_elevation_deg", _f(min_elev))
    d = _f(diam)
    if d is not None and d > 0:
        params["es_antenna_diameter_m"] = d
    _set("s1503_step_deg", _f(s1503_step))
    params["wcga_no_mask_symmetry"] = bool(wcga_no_mask_symmetry)
    params["s1503_trail_all_points"] = bool(s1503_trail)
    params["gso_longitude_mode"] = gso_lon_mode
    params["alpha_method"] = alpha_method
    params["dual_time_step_mode"] = dual_mode
    _set("fine_time_step_s", _f(fine_dt))
    params["itu_software"] = itu_software
    params["restrict_emitters_to_sim_band"] = bool(restrict_emitters)
    params["emulate_s1503_2"] = bool(emulate_s1503_2)
    params["disable_gso_min_elevation"] = not bool(apply_table8_egso)
    params["use_precession_mdb"] = bool(use_prec_mdb)
    params["force_gmst0_zero"] = bool(force_gmst0_zero)
    params["apply_station_keeping"] = bool(apply_sk)
    if artificial_prec_mode == "on":
        params["artificial_precession"] = True
    elif artificial_prec_mode == "off":
        params["artificial_precession"] = False
    _md_val = _f(min_duration_s)
    if _md_val is not None and _md_val > 0:
        params["min_duration_s"] = _md_val

    if art22_leaf is not None:
        params["service"] = str(art22_leaf["service"])
        params["es_antenna_diameter_m"] = float(art22_leaf["rf_diam_m"])
        params["reference_bandwidth_khz"] = float(art22_leaf["reference_bandwidth_khz"])
        params["simulation_frequency_ghz"] = float(art22_leaf["frequency_run_ghz"])
        _mref = art22_leaf.get("mask_ref") or {}
        if _mref.get("mask_id") is not None:
            params["mask_id"] = int(_mref["mask_id"])
    elif _carried_art22 is not None:
        if _carried_art22.get("reference_bandwidth_khz") is not None:
            params["reference_bandwidth_khz"] = float(_carried_art22["reference_bandwidth_khz"])
        if _carried_art22.get("simulation_frequency_ghz") is not None:
            params["simulation_frequency_ghz"] = float(_carried_art22["simulation_frequency_ghz"])
        if _carried_art22.get("mask_id") is not None:
            params["mask_id"] = int(_carried_art22["mask_id"])

    if params.get("simulation_frequency_ghz") is None:
        _mf = _f(sim_freq)
        if _mf is not None and _mf > 0:
            params["simulation_frequency_ghz"] = _mf

    _pin = params.get("simulation_frequency_ghz")
    if _pin is not None and params.get("restrict_emitters_to_sim_band"):
        _sys_check = storage.get_system(sel_sys) or {}
        _tx = _tx_subbands(_sys_check.get("srs_path", ""),
                           _sys_check.get("ntc_id"))
        if _tx and not any(
            b["freq_min"] - 1e-9 <= float(_pin) <= b["freq_max"] + 1e-9
            for b in _tx
        ):
            _txt = "; ".join(f"{b['freq_min']:.4f}–{b['freq_max']:.4f}"
                             for b in _tx)
            st.error(
                f"No transmitting group of this filing covers "
                f"**{float(_pin) * 1000.0:.2f} MHz** — this db's operating "
                f"Tx sub-band(s): **{_txt} GHz**. Pick a frequency inside "
                "them (see the band chart above), use the sibling extract "
                "that carries this band, or disable **Only satellites "
                "emitting in the sim band**. Run not launched.",
                icon=":material/error:",
            )
            st.stop()

    set_persisted_state("country_wcg.form", {
        "system_id": sel_sys,
        "num_time_steps": _i(num_steps),
        "time_step_s": _f(dt),
        "min_elevation_deg": _f(min_elev),
        "service": params.get("service", service),
        "es_antenna_diameter_m": params.get("es_antenna_diameter_m"),
        "simulation_frequency_ghz": params.get("simulation_frequency_ghz"),
        "reference_bandwidth_khz": params.get("reference_bandwidth_khz"),
        "mask_id": params.get("mask_id"),
        "country_codes": list(country_codes),
        "country_raan_sweep": country_raan_sweep,
        "s1503_step_deg": params.get("s1503_step_deg"),
        "wcga_no_mask_symmetry": bool(wcga_no_mask_symmetry),
        "s1503_trail_all_points": bool(s1503_trail),
        "gso_longitude_mode": gso_lon_mode,
        "alpha_method": alpha_method,
        "dual_time_step_mode": dual_mode,
        "fine_time_step_s": fine_dt,
        "itu_software": itu_software,
        "artificial_prec_mode": artificial_prec_mode,
        "use_precession_mdb": bool(use_prec_mdb),
        "force_gmst0_zero": bool(force_gmst0_zero),
        "apply_station_keeping": bool(apply_sk),
        "restrict_emitters_to_sim_band": bool(restrict_emitters),
        "disable_gso_min_elevation": not bool(apply_table8_egso),
        "min_duration_s": min_duration_s,
    })

    run_id = launcher.launch_country_wcg(system_id=sel_sys, params=params)
    set_current_run_id(run_id)
    st.toast(f"Run `{run_id}` launched", icon=":material/play_circle:")

    if _mc_run_all and _mc_siblings:
        for _sib in _mc_siblings:
            _sp = dict(params)
            _sp["system_id"] = _sib["system"]["id"]
            _rid = launcher.launch_country_wcg(
                system_id=_sib["system"]["id"], params=_sp,
            )
            st.toast(
                f"Run `{_rid}` launched (config {_sib['config_label']})",
                icon=":material/play_circle:",
            )

    st.switch_page("pages/7_Status.py", query_params={"run_id": run_id})
