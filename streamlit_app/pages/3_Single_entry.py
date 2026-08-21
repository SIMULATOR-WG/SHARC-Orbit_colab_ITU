"""Single-entry EPFD↓ (ITU-R S.1503-4) — functional, advanced options included."""
from __future__ import annotations

import json

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
    """Article 22 EPFD↓ possibility tree over the filing's OPERATING bands.

    Enumerates the normative Art. 22 tables intersecting each PFD mask band
    **clipped to the transmitting `grp` sub-bands** → service → frequency run
    → option (ES antenna diameter, reference BW, pattern, limit curve). The
    clip drops orphan masks — sliced BR extracts carry the whole mask_info
    but only the run band's grp/mask_lnk1, and a frequency picked on an
    orphan mask would abort at launch (no co-frequency emitter). A filing
    without Tx grp band data keeps the plain mask bands (filter inert).
    Each leaf carries its ``mask_ref``. Cached on the SRS path + notice.
    """
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
_GSO_LON_DESC = {
    "arc_optimal": "Sweep the GSO satellite across the visible arc to find the worst PFD.",
    "es_meridian": "Pin GSO at the ES meridian (degenerate, faster, less normative).",
}
_ALPHA_DESC = {
    "sweep": "Numerical sweep over α (slower, robust near singular geometries).",
    "analytical": "Closed-form α per S.1503-4 §D (faster, requires regular geometry).",
}
_WCG_SOURCE_OPTS = ["S.1503-4 WCGA", "Defined geometry"]
_WCG_SOURCE_DESC = {
    "S.1503-4 WCGA": (
        "Normative search per S.1503-4 §D.3 (latitude + θ/φ grid). "
        "Engine: `wcga_s1503=True`, `wcg_manual=False`."
    ),
    "Defined geometry": (
        "Skip the WCGA and run EPFD↓ at the ES / GSO coordinates in "
        "section 4. Engine: `wcg_manual=True`, `wcga_s1503=False`."
    ),
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
    "wcga_no_mask_symmetry": True,
    "s1503_trail_all_points": False,
    "gso_longitude_mode": "arc_optimal",
    "alpha_method": "analytical",
    "dual_time_step_mode": "on",
    "fine_time_step_s": "",
    "itu_software": "itu_epfd",
    "wcg_manual": False,
    "wcg_manual_es_lat": "",
    "wcg_manual_es_lon": "",
    "wcg_manual_gso_lon": "",
    "selection_strategy": "s1503",
    "top_n": 5,
    "n_select": 1,
    "seed": "",
    "include_override": False,
    "alpha_bin_deg": 0.0,   # 0 = normative declared TSS cases (Doc 4A/312 p. 110)
})
if not prev.get("_full_theta_default_v1"):
    prev = dict(prev)
    prev["wcga_no_mask_symmetry"] = True
    prev["_full_theta_default_v1"] = True
    set_persisted_state("s1503.form", prev)

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
        (f"{s.get('sat_name') or s.get('upload_label') or s['upload_id']}"
         f" · ntc {s.get('ntc_id') or '—'}"
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
        st.info(
            srs_inspect.format_multi_config_notice(_mc),
            icon=":material/call_split:",
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

# ── Band occupancy (ITU "Network Structure Navigation" style) ────────────
# Tx / Rx group bands (SRS `grp`) + PFD mask band(s) of the selected system,
# each row scaled to its own limits like the ITU panel. The Tx row is what
# the emitting-satellite band filter keys on.
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

# ── Article 22 downlink scenario (optional) ──────────────────────────────
# Pick the normative EPFD↓ limit configuration to test against. A chosen
# leaf overrides Service / ES antenna / reference BW / simulation frequency
# and pins the PFD mask. Left on Auto, the engine resolves from the band.
art22_leaf = None
_carried_art22 = None  # Art.22 scenario carried from a reloaded run
_sysrow = storage.get_system(sel_sys)
with st.expander("Article 22 downlink scenario (limits) — optional", expanded=False):
    st.caption(
        "Normative EPFD↓ possibilities for this filing's **operating** "
        "band(s) (PFD mask ∩ Tx `grp` sub-bands — orphan masks of sliced "
        "extracts are dropped): **service → frequency run → ES antenna / "
        "reference BW**. Picking a leaf **overrides** Service, ES antenna, "
        "reference BW and simulation frequency below, and pins the "
        "corresponding PFD `mask_id`. Leave on **Auto** to let the engine "
        "resolve from the filing band."
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
        
        # Proposed S.1428 revision (A/B) moved to section 8 —
        # "1503 proposal modifications" — where all WP-4A options live.
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
        wcg_source = select_described(
            "WCG source",
            _WCG_SOURCE_OPTS, _WCG_SOURCE_DESC,
            index=1 if prev.get("wcg_manual") else 0,
            help="Mutually exclusive. Engine keys: `wcga_s1503` / `wcg_manual`.",
        )
        wcga_s1503 = wcg_source == "S.1503-4 WCGA"
        wcg_manual = not wcga_s1503
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

    with st.expander("4. Defined geometry", expanded=False):
        st.caption(
            "Used only when **WCG source** = Defined geometry. "
            "Coordinates are ignored if S.1503-4 WCGA is selected."
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
                     "(legacy). When the filing declares grp bands and NONE "
                     "covers the simulation frequency, the run aborts with a "
                     "clear error instead of silently simulating the full "
                     "constellation.",
            )

    with st.expander("7. MAX_CO_FREQ override (Steps 19–22 cap / Nco)", expanded=False):
        st.caption(
            "Caps the number of co-frequency satellites summed into the "
            "aggregate — the same parameter as Nco for reference-vector "
            "selection (section 9 below). Normally comes from the SRS "
            "filing's `sat_oper` table per ES latitude. Set a value below to "
            "**force** or **override** it for all latitudes (useful for "
            "manual systems or what-if studies). Empty = use the filing's "
            "own value (or unlimited for manual systems)."
        )
        max_co_freq_override = st.text_input(
            "MAX_CO_FREQ — override",
            value=str(prev.get("max_co_freq_override") or ""),
            placeholder="auto (from SRS sat_oper; 0 = unlimited)",
            help="Engine key: `max_co_freq`. 0 = unlimited (ignores "
                 "sat_oper). ≥1 forces max_co_freq_by_lat = "
                 "[(-90°, 90°, N)] for every latitude.",
        )

    with st.expander("8. Track duration (MIN_DURATION — S.1503-4 §D5.1.4.2)", expanded=False):
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

    with st.expander("8. 1503 proposal modifications (WP 4A studies)", expanded=False):
        st.caption(
            "Non-normative options under study for the revision of "
            "Recommendation ITU-R S.1503-4. Tick the modifications to apply — "
            "they combine, except where noted. Everything left unticked runs "
            "the normative S.1503-4 algorithm."
        )
        mods_in_wcga = st.checkbox(
            "Apply the ticked modifications in the WCG search too",
            value=bool(prev.get("mods_in_wcga", False)),
            help="OFF (default): the modifications change only the EPFD↓ time "
                 "simulation — the worst-case geometry is found with the "
                 "normative S.1503-4 WCGA. ON: the WCGA also runs with the "
                 "proposed antenna and the Step-18 ablation, and (for the "
                 "selection strategies / reference vector) the found geometry "
                 "is re-ranked among the top WCGA candidates by the "
                 "strategy-aggregated instantaneous EPFD at t=0 — so the WCG "
                 "itself can move, as in the Doc 4A/1029 ablation study. "
                 "The alpha-table strategy is excluded (its TSS quota is "
                 "inherently temporal — undefined at a single instant).",
        )
        st.divider()

        # ── (a) GSO victim ES antenna: proposed S.1428 revision ────────────
        use_proposed = st.checkbox(
            "S.1428 proposed ES antenna pattern (doc 4A1d-3)",
            value=bool(prev.get("use_proposed_antenna", False)),
            help="Replaces the current ITU-R S.1428-1 victim antenna with the "
                 "proposed side/back-lobe revision (15 < f \u2264 30 GHz). "
                 "Engine keys: `use_proposed_antenna`, `proposed_antenna_option`.",
        )
        prop_opt = 1
        if use_proposed:
            prop_opt = st.radio(
                "Proposed pattern variant",
                options=[1, 2],
                index=int(prev.get("proposed_antenna_option", 1)) - 1,
                format_func=lambda v: (
                    "Variant A — −12 dBi floor from 23° (Proposal 1)" if v == 1
                    else "Variant B — floor from 43.65°/34.1° (Proposal 2, conservative)"
                ),
                horizontal=True,
            )
        st.divider()

        # ── (b) Satellite selection strategy (Steps 19–22 replacement) ────
        _nco_hint = None
        try:
            _nco_val = int(str(max_co_freq_override).strip())
            if _nco_val > 0:
                _nco_hint = _nco_val
        except (ValueError, TypeError):
            pass
        mod_selection = st.checkbox(
            "Alternative satellite selection strategy",
            value=bool(prev.get("selection_strategy", "s1503") != "s1503"
                       or prev.get("ref_vec_selection", False)),
            help="Replaces the normative worst-case selection (Steps 19–21) "
                 "in the EPFD↓ time simulation. The WCG search itself is a "
                 "single-reference-satellite geometry hunt (§D3) — Steps 19–22 "
                 "do not exist there, so strategies act on the time simulation "
                 "by construction.",
        )
        selection_strategy = "s1503"
        ref_vec_selection = False
        top_n, n_select, seed = 5, 1, ""
        include_override = False
        alpha_bin_deg = 0.0
        alpha_table_data = None
        alpha_table_file = None
        ref_vec_az_deg = float(prev.get("ref_vec_az_deg", 0.0))
        ref_vec_el_deg = float(prev.get("ref_vec_el_deg", 90.0))
        ref_vec_time_window_P_pct = float(prev.get("ref_vec_time_window_P_pct", 100.0))
        if mod_selection:
            _strat_prev = str(prev.get("selection_strategy", "s1503"))
            if prev.get("ref_vec_selection"):
                _strat_prev = "ref_vector"
            _strat_opts = ["top_n_elev_random", "hybrid_rand_he", "ref_vector", "alpha_table"]
            selection_strategy = st.radio(
                "Strategy",
                options=_strat_opts,
                index=(_strat_opts.index(_strat_prev) if _strat_prev in _strat_opts else 0),
                format_func=lambda v: {
                    "top_n_elev_random": "Top-N highest elevation + random draw (Doc 4A/442)",
                    "hybrid_rand_he": "Hybrid random + highest elevation (Doc 4A/493)",
                    "ref_vector": "Reference vector + track duration (Doc 4A/519, US)",
                    "alpha_table": "Alpha table — TSS quota (Doc 4A/312)",
                }[v],
            )
            if selection_strategy == "top_n_elev_random":
                col_n, col_m, col_s = st.columns(3)
                with col_n:
                    top_n = st.number_input(
                        "N — highest-elevation pool",
                        min_value=1,
                        max_value=(_nco_hint if _nco_hint else 50),
                        value=min(int(prev.get("top_n", 5)),
                                  _nco_hint if _nco_hint else 50),
                        step=1,
                        help="Pool of the N highest-elevation Standard satellites. "
                             + (f"Capped at Nco = {_nco_hint} (MAX_CO_FREQ override, section 7)."
                                if _nco_hint else
                                "Set a MAX_CO_FREQ override (section 7) to cap N at Nco; "
                                "the engine always caps the drawn count at MAX_CO_FREQ."),
                    )
                with col_m:
                    n_select = st.number_input(
                        "M — satellites drawn (≤ N)",
                        min_value=1, max_value=int(top_n),
                        value=min(int(prev.get("n_select", 1)), int(top_n)),
                        step=1,
                        help="Drawn at random (no replacement) from the Top-N pool. "
                             "M acts as an effective Nco′: the engine aggregates "
                             "min(M, MAX_CO_FREQ) satellites per step.",
                    )
                with col_s:
                    seed = st.text_input(
                        "Random seed (optional)", value=str(prev.get("seed", "")),
                        placeholder="e.g. 42",
                        help="Fixed seed for reproducibility. Empty = OS entropy.",
                    )
            elif selection_strategy == "hybrid_rand_he":
                seed = st.text_input(
                    "Random seed (optional)", value=str(prev.get("seed", "")),
                    placeholder="e.g. 42",
                    help="Nco random + Nco highest-elevation lists, merged, ranked "
                         "by EPFD, keep Nco (= MAX_CO_FREQ, section 7). Only the "
                         "random list needs a seed.",
                )
            elif selection_strategy == "ref_vector":
                ref_vec_selection = True
                st.caption(
                    "Hold duration comes from **MIN_DURATION** (track-duration "
                    "override above) — no separate duration parameter. With no "
                    "filing MIN_DURATION and no override, the hold degenerates "
                    "to one fine step. Nco is **MAX_CO_FREQ** (section 7). "
                    "Step 22 (OR) satellites are disabled in this mode."
                )
                col_rv1, col_rv2, col_rv3 = st.columns(3)
                with col_rv1:
                    ref_vec_az_deg = st.number_input(
                        "Vector azimuth (°)",
                        min_value=-180.0, max_value=360.0,
                        value=float(prev.get("ref_vec_az_deg", 0.0)), step=1.0,
                        help="Clockwise from North. 0°=North, 90°=East.",
                    )
                with col_rv2:
                    ref_vec_el_deg = st.number_input(
                        "Vector elevation (°)",
                        min_value=0.0, max_value=90.0,
                        value=float(prev.get("ref_vec_el_deg", 90.0)), step=1.0,
                        help="90° = zenith ≡ highest-elevation selection.",
                    )
                with col_rv3:
                    ref_vec_time_window_P_pct = st.number_input(
                        "Time-window percentile P (%)",
                        min_value=1.0, max_value=100.0,
                        value=float(prev.get("ref_vec_time_window_P_pct", 100.0)),
                        step=1.0,
                        help="M = max(⌊N_SW × P/100⌋, 1) worst samples averaged "
                             "per satellite when ranking.",
                    )
            elif selection_strategy == "alpha_table":
                alpha_bin_deg = st.number_input(
                    "α sub-bin width (deg) — 0 = normative",
                    min_value=0.0, max_value=10.0,
                    value=float(prev.get("alpha_bin_deg", 0.0)), step=0.5,
                    help="0 uses the declared TSS cases of Doc 4A/312 p. 110. "
                         ">0 subdivides each case (sensitivity studies only).",
                )
                if float(alpha_bin_deg) > 0.0:
                    st.warning(
                        f"α sub-bin = {alpha_bin_deg}° subdivides the declared "
                        "TSS cases — non-conforming to Doc 4A/312."
                    )
                up = st.file_uploader(
                    "Alpha table file (JSON or YAML) — declared min/max CDF pairs",
                    type=["json", "yaml", "yml"],
                    help="`min`/`max` lists of [angle_deg, probability] pairs "
                         "(CDF, increasing). NOT read from the .mdb.",
                )

                def _validate_cdf(pairs, who):
                    pa = pp = -1.0
                    for a, p_ in pairs:
                        a, p_ = float(a), float(p_)
                        if a <= pa:
                            raise ValueError(f"{who}: angles must strictly increase (got {a}° after {pa}°).")
                        if p_ < pp:
                            raise ValueError(f"{who}: probabilities must be non-decreasing (CDF); got {p_} after {pp}.")
                        if not (0.0 < p_ <= 1.0):
                            raise ValueError(f"{who}: probability {p_} out of (0, 1].")
                        pa, pp = a, p_

                if up is not None:
                    try:
                        raw = up.getvalue().decode("utf-8")
                        if up.name.lower().endswith((".yaml", ".yml")):
                            import yaml as _yaml
                            data = _yaml.safe_load(raw)
                        else:
                            import json as _json
                            data = _json.loads(raw)
                        _min = [[float(a), float(p_)] for a, p_ in data["min"]]
                        _max = [[float(a), float(p_)] for a, p_ in data["max"]]
                        _validate_cdf(_min, "min"); _validate_cdf(_max, "max")
                        alpha_table_data = {"min": _min, "max": _max}
                        alpha_table_file = up.name
                        st.success(
                            f"Alpha table loaded from `{up.name}`: "
                            f"{len(_min)} min pairs, {len(_max)} max pairs."
                        )
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"Invalid alpha table: {exc}")
        st.divider()

        # ── (c) Step-18 gain test: drop the Gmax−30 dB candidate ──────────
        drop_gmax30 = st.checkbox(
            "Remove the Gmax−30 dB candidate from the Step-18 gain test (Doc 4A/1029 §5)",
            value=bool(prev.get("drop_gmax30", False)),
            help="The OR-branch threshold becomes GRX(α₀) alone — the declared "
                 "exclusion angle α₀ ALWAYS remains; only the wider Gmax−30 "
                 "candidate is removed (the admission cone never extends past "
                 "α₀). Applies to BOTH the WCG search and the EPFD↓ simulation.",
        )
        st.divider()

        # ── (d) Sidelobe-to-sidelobe (SL2SL) contribution ───────────────────
        sidelobe_enabled = st.checkbox(
            "Sidelobe (SL2SL) contribution — S.1528 patterns 1.2/1.4",
            value=bool(prev.get("sidelobe_enabled", False)),
            help="Adds the side-lobe emissions of the visible non-Nco satellites "
                 "serving a terrestrial grid of ESs around the victim "
                 "(constant pfd toward each served cell). Outputs a "
                 "sidelobe-only CCDF and a standard+sidelobe total CCDF. "
                 "Fixed time step only.",
        )
        sidelobe_pattern = str(prev.get("sidelobe_pattern", "1.4"))
        sidelobe_pfd_dbw_m2 = float(prev.get("sidelobe_pfd_dbw_m2", -140.0))
        sidelobe_grid_radius_km = float(prev.get("sidelobe_grid_radius_km", 315.0))
        sidelobe_grid_spacing_km = float(prev.get("sidelobe_grid_spacing_km", 21.0))
        sidelobe_min_elevation_deg = float(prev.get("sidelobe_min_elevation_deg", 25.0))
        sidelobe_gso_arc_separation_deg = float(prev.get("sidelobe_gso_arc_separation_deg", 20.0))
        if sidelobe_enabled:
            if selection_strategy in ("ref_vector", "alpha_table"):
                st.error(
                    "SL2SL is only implemented for the standard fixed-step path — "
                    "not combinable with reference-vector or alpha-table selection. "
                    "Untick one of them before launching."
                )
            if dual_mode != "off":
                st.warning(
                    "SL2SL requires a FIXED time step — set **Dual time step** "
                    "to `off` (section 5 above) or the engine will refuse the run."
                )
            col_sl1, col_sl2, col_sl3 = st.columns(3)
            with col_sl1:
                sidelobe_pattern = st.radio(
                    "NGSO satellite pattern",
                    options=["1.4", "1.2"],
                    index=(0 if sidelobe_pattern != "1.2" else 1),
                    format_func=lambda v: (
                        "S.1528 rec 1.4 — Taylor (low side lobes)" if v == "1.4"
                        else "S.1528 rec 1.2 — alternative (high side lobes)"
                    ),
                )
                sidelobe_pfd_dbw_m2 = st.number_input(
                    "pfd toward served cell (dBW/m²/40kHz)",
                    min_value=-200.0, max_value=-80.0,
                    value=sidelobe_pfd_dbw_m2, step=1.0,
                )
            with col_sl2:
                sidelobe_grid_radius_km = st.number_input(
                    "ES grid half-width (km)", min_value=50.0, max_value=2000.0,
                    value=sidelobe_grid_radius_km, step=21.0,
                )
                sidelobe_grid_spacing_km = st.number_input(
                    "ES grid spacing (km)", min_value=5.0, max_value=100.0,
                    value=sidelobe_grid_spacing_km, step=1.0,
                    help="≥ 20 km respects the minimum co-frequency beam separation.",
                )
            with col_sl3:
                sidelobe_min_elevation_deg = st.number_input(
                    "Serving-link min elevation (°)", min_value=0.0, max_value=90.0,
                    value=sidelobe_min_elevation_deg, step=1.0,
                )
                sidelobe_gso_arc_separation_deg = st.number_input(
                    "GSO-arc separation at served ES (°)",
                    min_value=0.0, max_value=90.0,
                    value=sidelobe_gso_arc_separation_deg, step=1.0,
                )
            _n_side = 2 * int(sidelobe_grid_radius_km / sidelobe_grid_spacing_km) + 1
            st.caption(
                f"Grid: {_n_side}×{_n_side}−1 = {_n_side * _n_side - 1} served ESs "
                "centred on each simulated victim. Victim exclusion gate uses the "
                "run's own α₀; the victim antenna is the run's ES antenna above."
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
        itu_software=itu_software,
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
    params["use_proposed_antenna"] = bool(use_proposed)
    params["proposed_antenna_option"] = int(prop_opt)
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
    params["itu_software"] = itu_software
    # Track-duration override (§D5.1.4.2). Only sent when > 0.
    _md_val = _f(min_duration_s)
    if _md_val is not None and _md_val > 0:
        params["min_duration_s"] = _md_val
    # MAX_CO_FREQ override (Steps 19-22 cap / Nco). 0 is a meaningful value
    # (unlimited) distinct from "not set", so send whenever the field is
    # non-empty rather than gating on > 0 like min_duration_s above.
    _mcf_val = _i(max_co_freq_override)
    if _mcf_val is not None:
        params["max_co_freq"] = _mcf_val
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

    params["selection_strategy"] = selection_strategy
    if top_n is not None and top_n > 0:
        params["top_n"] = int(top_n)
    if n_select is not None and n_select > 0:
        params["n_select"] = int(n_select)
    if seed and seed.strip():
        try:
            params["seed"] = int(seed.strip())
        except ValueError:
            pass

    params["include_override"] = bool(include_override)

    if selection_strategy == "alpha_table":
        params["alpha_bin_deg"] = float(alpha_bin_deg)
        if alpha_table_data is not None:
            params["alpha_table"] = alpha_table_data
            # Recorded in the run's params.json next to the inline pairs: the
            # pairs make the run reproducible, the name says which declared
            # table it was.
            params["alpha_table_file"] = alpha_table_file

    # Reference-vector satellite selection (US proposal R23-WP4A-C-0519).
    # Hold duration comes from MIN_DURATION (min_duration_s above) — no
    # separate track-duration param.
    params["ref_vec_selection"] = bool(ref_vec_selection)
    params["ref_vec_az_deg"] = float(ref_vec_az_deg)
    params["ref_vec_el_deg"] = float(ref_vec_el_deg)
    params["ref_vec_time_window_P_pct"] = float(ref_vec_time_window_P_pct)

    # Step-18 gain-test ablation + SL2SL sidelobe study (section 8).
    params["mods_in_wcga"] = bool(mods_in_wcga)
    params["drop_gmax30"] = bool(drop_gmax30)
    if sidelobe_enabled:
        params["sidelobe_enabled"] = True
        params["sidelobe_pattern"] = str(sidelobe_pattern)
        params["sidelobe_pfd_dbw_m2"] = float(sidelobe_pfd_dbw_m2)
        params["sidelobe_grid_radius_km"] = float(sidelobe_grid_radius_km)
        params["sidelobe_grid_spacing_km"] = float(sidelobe_grid_spacing_km)
        params["sidelobe_min_elevation_deg"] = float(sidelobe_min_elevation_deg)
        params["sidelobe_gso_arc_separation_deg"] = float(sidelobe_gso_arc_separation_deg)

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

    # Pre-launch guard: a pinned frequency outside every Tx `grp` sub-band
    # would abort in the engine (strict emitter band filter) — catch it here
    # before a run is even created. Inert when the filing declares no Tx grp
    # band (the engine filter is inert there too).
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

    set_persisted_state("s1503.form", {
        "reference_bandwidth_khz": params.get("reference_bandwidth_khz"),
        "simulation_frequency_ghz": params.get("simulation_frequency_ghz"),
        "mask_id": params.get("mask_id"),
        "system_id": sel_sys,
        "num_time_steps": _i(num_steps), "time_step_s": _f(dt),
        "min_elevation_deg": _f(min_elev), "service": service,
        "es_antenna_diameter_m": _f(diam),
        "use_proposed_antenna": bool(use_proposed),
        "proposed_antenna_option": int(prop_opt),
        "wcga_s1503": bool(wcga_s1503), "s1503_step_deg": _f(s1503_step),
        "wcga_no_mask_symmetry": bool(wcga_no_mask_symmetry),
        "s1503_trail_all_points": bool(s1503_trail),
        "gso_longitude_mode": gso_lon_mode, "alpha_method": alpha_method,
        "wcg_manual": bool(wcg_manual),
        "wcg_manual_es_lat": wm_es_lat, "wcg_manual_es_lon": wm_es_lon,
        "wcg_manual_gso_lon": wm_gso_lon,
        "fine_time_step_s": fine_dt, "dual_time_step_mode": dual_mode,
        "itu_software": itu_software,
        "min_duration_s": min_duration_s,
        "max_co_freq_override": max_co_freq_override,
        "artificial_prec_mode": artificial_prec_mode,
        "use_precession_mdb": bool(use_prec_mdb),
        "apply_station_keeping": bool(apply_sk),
        "restrict_emitters_to_sim_band": bool(restrict_emitters),
        "selection_strategy": selection_strategy,
        "top_n": int(top_n) if top_n is not None else 5,
        "n_select": int(n_select) if n_select is not None else 1,
        "seed": seed.strip() if seed else "",
        "include_override": False,
        "alpha_bin_deg": float(alpha_bin_deg),
        "ref_vec_selection": bool(ref_vec_selection),
        "ref_vec_az_deg": float(ref_vec_az_deg),
        "ref_vec_el_deg": float(ref_vec_el_deg),
        "ref_vec_time_window_P_pct": float(ref_vec_time_window_P_pct),
        "mods_in_wcga": bool(mods_in_wcga),
        "drop_gmax30": bool(drop_gmax30),
        "sidelobe_enabled": bool(sidelobe_enabled),
        "sidelobe_pattern": str(sidelobe_pattern),
        "sidelobe_pfd_dbw_m2": float(sidelobe_pfd_dbw_m2),
        "sidelobe_grid_radius_km": float(sidelobe_grid_radius_km),
        "sidelobe_grid_spacing_km": float(sidelobe_grid_spacing_km),
        "sidelobe_min_elevation_deg": float(sidelobe_min_elevation_deg),
        "sidelobe_gso_arc_separation_deg": float(sidelobe_gso_arc_separation_deg),
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