"""Aggregate EPFD↓ — multi-system aggregation (Resolution 76 context).

Accepts ≥ 2 systems. The systems may come from a single filing (different
notices in the same MDB) or from multiple filings.
Four aggregation methods exposed: method_1..method_4 (testing alternatives;
not tied to a single ITU-R recommendation). The former method_5 was folded
into method_2, which now keeps the full curve set (per-system + per-point
convolution + envelope); method_5 survives only as a dormant back-compat
alias in the worker for reloading historical runs.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from lib import launcher, srs_inspect, storage, theme, tour
from lib.art22_ui import (
    system_bands as _system_bands,
    merge_intervals as _merge_intervals,
    intersect_sets as _intersect_sets,
    art22_tree_for_bands as _art22_tree_for_bands,
)
from lib.state import (
    use_persisted_state, set_persisted_state, set_current_run_id,
)
from lib.widgets import select_described
from lib.manual import help_expander

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
    "off": "Force artificial RAAN precession off.",
    "on": "Force artificial RAAN precession on.",
    "auto": "Engine decides from the SRS (rpt period / f_precess / plane count).",
}


st.set_page_config(page_title="Aggregate · SHARC-Orbit", page_icon=":material/grid_view:", layout="wide")
theme.inject()

st.title("Aggregate EPFD↓")
help_expander("aggregate")
tour.maybe_render("aggregate")
st.caption(
    "Multi-system aggregation (Resolution 76 context) — pick ≥ 2 systems and "
    "aggregate using one of 4 methods. Systems may belong to the same filing "
    "(different notices) or to distinct filings."
)

METHODS = [
    ("method_1", "Method 1 — convolution at each system's WCG (Study 1, conservative)"),
    ("method_2", "Method 2 — common ES×GSO grid: per-system + per-point convolution + envelope (Study 2 / WP-4A Step-1)"),
    ("method_3", "Method 3 — joint simulation on fused megaconstellation (Study 3)"),
    ("method_4", "Method 4 — per-WCG convolution sweep (didactic / inhomogeneity audit)"),
]

# Per-method long-form hint shown below the radio whenever a method is
# selected. Renders as the engineer-facing explanation of what runs.
_METHOD_HINT: dict[str, str] = {
    "method_1": (
        "**Method 1 — per-WCG convolution (Study 1).**\n\n"
        "1. For each filing, run a full S.1503-4 single-entry simulation at "
        "**its own** worst-case geometry (WCG_i).\n"
        "2. Convolve the resulting CCDFs (PMF ⊗ PMF in linear power) into a "
        "single aggregate CCDF.\n\n"
        "Conservative — every system contributes its individual worst — but "
        "ignores that WCGs of different systems usually don't co-occur."
    ),
    "method_2": (
        "**Method 2 — common ES×GSO grid (Study 2 / WP-4A Step-1).**\n\n"
        "1. Sweep a regular grid over (ES lat, ES lon, GSO lon) — optionally "
        "restricted to selected countries.\n"
        "2. At each grid point, run **all N filings** at that geometry and keep "
        "**every** curve: each filing's raw CCDF (`per_system`) **and** their "
        "convolution (`per_point`).\n"
        "3. Across grid points, build the **envelope** (worst per percentile, "
        "linear scale) — the headline / go-no-go curve.\n\n"
        "One run produces the full curve set: per-system, per-geometry "
        "convolution and the envelope. The Study-2 reading looks at the "
        "envelope; the WP-4A Step-1 reading (doc 1064 Anexo 21) looks at the "
        "per-geometry / per-system curves — both are here, no re-run. "
        "Tighter than Method 1: sampled at geometries that actually co-exist. "
        "Cost grows as `n_grid × N`."
    ),
    "method_3": (
        "**Method 3 — joint sim on the fused megaconstellation (Study 3).**\n\n"
        "1. Merge every filing's constellation into one combined object with "
        "a per-satellite PFD mask (`mask_lnk1` preserved).\n"
        "2. Locate a **joint WCG** via S.1503-4 §D.3.1 on the megaconstellation "
        "(or use manual geometry).\n"
        "3. Run a **single** EPFD↓ simulation at that joint WCG.\n"
        "4. Also produce `post_sum` (convolution of per-system Method 1) "
        "as a contrast curve.\n\n"
        "Physically rigorous: correlations between systems are kept. Highest "
        "compute cost per run."
    ),
    "method_4": (
        "**Method 4 — per-WCG convolution sweep (audit).**\n\n"
        "1. Compute each filing's WCG_i (i = 1…N).\n"
        "2. For **each** WCG_i, run all N filings at that geometry and "
        "convolve them.\n"
        "3. Output N aggregate CCDFs — one per WCG_i — letting you compare "
        "how the choice of geometry affects the aggregate.\n\n"
        "Didactic: surfaces inhomogeneity between systems. Cost ≈ "
        "`N × N` sims."
    ),
}

systems = storage.list_systems()
orphans = storage.list_orphan_uploads()

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

if len(systems) < 2:
    st.warning(
        "Need ≥ **2 systems**. Register more on **Upload** (a single MDB with 2 notices "
        "or with 2 masks per notice already yields 2 systems)."
    )
    st.page_link("pages/1_Upload.py", label="Upload", icon=":material/upload:")
    st.stop()

prev = use_persisted_state("s1588.form", {
    "system_ids": [],
    "method": "method_1",
    "num_time_steps": 3600,
    "time_step_s": 1.0,
    "min_elevation_deg": 10.0,
    "service": "FSS",
    "es_antenna_diameter_m": 1.2,
    "grid_step_deg": 30.0,
    "gso_pointing_step_deg": 30.0,
    "n_geom_max": 50000,
    "geometry_es_lat": "",
    "geometry_es_lon": "",
    "geometry_gso_lon": "",
    "wcga_s1503": True,
    "s1503_step_deg": 1.0,
    "dual_time_step_mode": "on",
    "fine_time_step_s": "",
    "gso_longitude_mode": "arc_optimal",
    "alpha_method": "analytical",
    "country_codes": [],
    "truncate_tail": False,
    "truncate_tail_pct": "",
})

st.subheader("1. Systems")
sel_ids = st.multiselect(
    "Pick ≥ 2 systems",
    options=[s["id"] for s in systems],
    default=[i for i in (prev.get("system_ids") or []) if i in {s["id"] for s in systems}],
    format_func=lambda i: next(
        (f"{s.get('upload_label') or s['upload_id']} · ntc {s.get('ntc_id') or '—'}"
         for s in systems if s["id"] == i),
        i,
    ),
)
art22_leaf = None  # Article 22 scenario leaf for the aggregate (set below)
_carried_art22 = None  # Art.22 scenario carried from a reloaded run
if sel_ids:
    st.caption(
        f"Selected **{len(sel_ids)} system(s)**, from "
        f"**{len({storage.get_system(i)['upload_id'] for i in sel_ids if storage.get_system(i)})}** filing(s)."
    )

    # Valid frequency bands of the selected systems + cross-system overlap.
    # Aggregating EPFD↓ only makes physical sense co-channel — surface the
    # bands here so disjoint selections are caught before launching. Masks
    # that share a band are grouped onto one row (their mask_ids listed),
    # so a system with multiple distinct bands shows one row per band.
    band_rows = []
    per_system_intervals: list[list[tuple[float, float]]] = []
    n_resolved = 0
    n_no_band = 0
    for i in sel_ids:
        s = storage.get_system(i)
        if not s:
            continue
        n_resolved += 1
        label = s.get("upload_label") or s["upload_id"]
        ntc = s.get("ntc_id") or "—"
        sb = _system_bands(s["srs_path"], s.get("ntc_id"))
        if not sb:
            n_no_band += 1
            band_rows.append({
                "system": label, "ntc": ntc, "downlink band (GHz)": "—",
                "bandwidth (MHz)": "—", "PFD masks (mask_id)": "—",
            })
            continue
        for b in sb:
            band_rows.append({
                "system": label, "ntc": ntc,
                "downlink band (GHz)": f"{b['freq_min']:.3f} – {b['freq_max']:.3f}",
                "bandwidth (MHz)": f"{(b['freq_max'] - b['freq_min']) * 1000:.1f}",
                "PFD masks (mask_id)": ", ".join(str(x) for x in b["mask_ids"]) or "—",
            })
        per_system_intervals.append(
            _merge_intervals([(b["freq_min"], b["freq_max"]) for b in sb])
        )

    st.markdown("**Downlink (PFD) frequency bands of selected systems** "
                "(one row per distinct band; PFD masks sharing a band grouped)")
    st.dataframe(pd.DataFrame(band_rows), hide_index=True, width="stretch")

    if len(per_system_intervals) >= 2:
        common = per_system_intervals[0]
        for nxt in per_system_intervals[1:]:
            common = _intersect_sets(common, nxt)
        if common:
            txt = "; ".join(f"{lo:.3f}–{hi:.3f}" for lo, hi in common)
            st.success(
                f"Common downlink band(s) across all resolved systems: "
                f"**{txt} GHz**. Co-frequency EPFD↓ aggregation is meaningful."
            )

            # Article 22 scenario for the aggregate — one shared limit config
            # built from the common band(s). Picking a leaf overrides Service,
            # ES antenna, reference BW and simulation frequency below. Each
            # filing keeps its own PFD mask (no mask_id pinned here).
            with st.expander("Article 22 downlink scenario (limits) — optional",
                              expanded=False):
                st.caption(
                    "Normative EPFD↓ possibilities over the **common** band: "
                    "**service → frequency run → ES antenna / reference BW**. "
                    "A leaf **overrides** Service, ES antenna, reference BW and "
                    "simulation frequency below (applied to every filing). "
                    "Leave on **Auto** to let the engine resolve per filing."
                )
                _tree = _art22_tree_for_bands(tuple(common))
                _services = _tree.get("services") or []
                if not _services:
                    st.caption("No Article 22 downlink possibility for the "
                               "common band (engine will auto-resolve).")
                else:
                    _svc_opts = ["Auto (engine resolves)"] + [s["service"] for s in _services]
                    _svc_pick = st.selectbox("Service", _svc_opts, key="agg_art22_svc")
                    if _svc_pick != "Auto (engine resolves)":
                        _svc_node = next(s for s in _services if s["service"] == _svc_pick)
                        _freqs = _svc_node["frequencies"]
                        _fi = st.selectbox(
                            "Frequency run", options=list(range(len(_freqs))),
                            format_func=lambda i: (
                                f"{_freqs[i]['label']} · {_freqs[i]['rr_reference']} · "
                                f"R{','.join(str(r) for r in _freqs[i].get('regions', [])) or '—'}"
                            ),
                            key="agg_art22_freq",
                        )
                        _fnode = _freqs[min(_fi, len(_freqs) - 1)]
                        _opts = _fnode["options"]
                        _oi = st.selectbox(
                            "ES antenna / reference BW", options=list(range(len(_opts))),
                            format_func=lambda i: f"{_opts[i]['label']} · {_opts[i]['rf_pattern_rr']}",
                            key="agg_art22_opt",
                        )
                        art22_leaf = _opts[min(_oi, len(_opts) - 1)]
                        st.success(
                            f"Scenario: **{art22_leaf['service']}** · run "
                            f"**{art22_leaf['frequency_run_mhz']:.2f} MHz** · ES "
                            f"**{art22_leaf['rf_diam_m']:.2f} m** · "
                            f"**{art22_leaf['reference_bandwidth_khz']:.0f} kHz** · "
                            f"{art22_leaf['rr_reference']} (applies to all filings)"
                        )
                # Carry an Art.22 scenario reloaded from a past run (Runs →
                # reload). Active only while no leaf is picked above. Checked
                # by default ONLY right after the Runs → "Reload config" flow
                # (`art22_from_reload`), never for values merely left over
                # from an old launch.
                if art22_leaf is None:
                    _rbw = prev.get("reference_bandwidth_khz")
                    _sfreq = prev.get("simulation_frequency_ghz")
                    if (_rbw is not None or _sfreq is not None) and st.checkbox(
                        "Apply reloaded Article 22 scenario",
                        value=bool(prev.get("art22_from_reload")),
                        key="agg_art22_carry",
                        help="Scenario from a reloaded run. Uncheck for engine "
                             "auto-resolve.",
                    ):
                        _carried_art22 = {
                            "reference_bandwidth_khz": _rbw,
                            "simulation_frequency_ghz": _sfreq,
                        }
                        st.caption(
                            "Reloaded scenario: "
                            f"run {float(_sfreq) * 1000.0:.2f} MHz · {float(_rbw):.0f} kHz"
                            if (_sfreq is not None and _rbw is not None) else
                            "Reloaded Article 22 scenario active."
                        )
        else:
            st.warning(
                "⚠️ Selected systems have **no common downlink band** "
                "(disjoint PFD ranges). EPFD↓ aggregation across disjoint "
                "bands is not co-channel interference — the combined CCDF "
                "may be physically meaningless."
            )
    if n_no_band:
        st.caption(
            f"{n_no_band} system(s) declare no PFD (downlink) band; "
            "excluded from the overlap check."
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
    st.warning(
        "A **reloaded Article 22 scenario** will be applied to this launch: "
        + (" · ".join(_bits) or "values from the reloaded run")
        + ". Uncheck **Apply reloaded Article 22 scenario** (expander in "
        "section 1) to let the engine auto-resolve.",
        icon=":material/warning:",
    )

st.subheader("2. Aggregation method")
st.markdown(
    """
<style>
/* Aggregation method radio — larger option labels & captions for legibility. */
div[data-testid="stRadio"] [data-baseweb="radio"] > div,
div[data-testid="stRadio"] [data-baseweb="radio"] label {
    font-size: var(--so-fs-h3) !important;
    font-weight: 600 !important;
}
div[data-testid="stRadio"] p,
div[data-testid="stRadio"] [data-baseweb="radio"] + div {
    font-size: var(--so-fs-body) !important;
    line-height: 1.5 !important;
}
</style>
    """,
    unsafe_allow_html=True,
)
_method_ids = [m[0] for m in METHODS]
# Retired methods (e.g. method_5, folded into method_2) may linger in the
# persisted state — fall back to method_1 instead of raising on .index().
_prev_method = prev.get("method", "method_1")
method = st.radio(
    "Method",
    options=_method_ids,
    index=_method_ids.index(_prev_method) if _prev_method in _method_ids else 0,
    captions=[m[1] for m in METHODS],
    horizontal=False,
)
_hint = _METHOD_HINT.get(method)
if _hint:
    with st.container(border=True):
        st.markdown(_hint)

# Mirror an Article 22 scenario leaf (section 1) into the section-3 widgets.
_forced_service = str(art22_leaf["service"]).upper() if art22_leaf is not None else None
_forced_diam_m = float(art22_leaf["rf_diam_m"]) if art22_leaf is not None else None

with st.form("s1588_form"):
    st.subheader("3. Simulation parameters")
    st.caption("Empty fields = engine defaults (auto).")
    col1, col2 = st.columns(2)
    with col1:
        num_steps = st.text_input(
            "Number of time steps",
            value=str(prev.get("num_time_steps") or ""),
            placeholder="auto (Obs2 ref. 518 400)",
            help="Engine key: `num_time_steps`. Lower for quick tests; "
                 "empty = engine default.",
        )
        dt = st.text_input(
            "Coarse time step (s)",
            value=str(prev.get("time_step_s") or ""),
            placeholder="auto",
            help="Engine key: `time_step_s`. Sampling period of the EPFD↓ "
                 "time series. Empty = engine default (1 s).",
        )
        min_elev = st.text_input(
            "Minimum ES elevation (°)",
            value=str(prev.get("min_elevation_deg") or ""),
            placeholder="auto",
            help="Engine key: `min_elevation_deg`. Cut-off elevation for the "
                 "earth station. Empty = engine default (10°).",
        )
    with col2:
        _svc_default = (_forced_service or str(prev.get("service", "FSS"))).upper()
        service = select_described(
            "Service", ["FSS", "BSS"], _SERVICE_DESC,
            index=0 if _svc_default == "FSS" else 1,
            help="Engine key: `service`. Common across all systems in a "
                 "Resolution 76 aggregate study. Set automatically by the "
                 "Article 22 scenario when one is picked in section 1.",
        )
        _diam_default = (f"{_forced_diam_m:.2f}" if _forced_diam_m is not None
                         else str(prev.get("es_antenna_diameter_m") or ""))
        diam = st.text_input(
            "ES antenna diameter (m)",
            value=_diam_default,
            placeholder="1.2 (default)",
            help="Engine key: `es_antenna_diameter_m`. Common ES diameter for "
                 "all systems (Res. 76). Empty = 1.2 m default. Set "
                 "automatically by the Article 22 scenario when picked above.",
        )
        if art22_leaf is not None:
            st.caption("↑ Service & ES antenna set by the Article 22 scenario.")

    if method == "method_2":
        st.markdown("**Grid params (S.1503-4 §D.6)**")
        col_g1, col_g2, col_g3 = st.columns(3)
        with col_g1:
            grid_step = st.text_input(
                "Grid step (°)",
                value=str(prev.get("grid_step_deg") or ""),
                placeholder="10 (typical)",
                help="Engine key: `grid_step_deg`. ES lat/lon grid spacing. "
                     "Smaller = finer (denser sims).",
            )
        with col_g2:
            gpts = st.text_input(
                "GSO pointing step (°)",
                value=str(prev.get("gso_pointing_step_deg") or ""),
                placeholder="same as grid",
                help="Engine key: `gso_pointing_step_deg`. GSO satellite "
                     "longitude grid spacing. Empty = same as Grid step.",
            )
        with col_g3:
            n_geom = st.text_input(
                "Max geometries",
                value=str(prev.get("n_geom_max") or ""),
                placeholder="50000",
                help="Engine key: `n_geom_max`. Hard cap on grid point count "
                     "to keep runtime bounded.",
            )

        # Country filter — restricts grid points to ES inside the
        # selected countries' territories. Empty selection = world-wide.
        try:
            from src.s1588_studies.countries import list_countries
            _all_countries = list_countries()
        except Exception:  # noqa: BLE001
            _all_countries = []
        _country_label = {c["id"]: f"{c['name']}  ({c['id']})"
                            for c in _all_countries}
        _default_codes = [
            c for c in (prev.get("country_codes") or [])
            if c in _country_label
        ]
        country_codes = st.multiselect(
            "Restrict ES grid to countries (empty = world-wide)",
            options=list(_country_label.keys()),
            default=_default_codes,
            format_func=lambda c: _country_label.get(c, c),
            help="Engine key: `country_codes`. Each grid point's ES "
                 "(lat, lon) is tested against the selected countries' "
                 "polygons (Natural Earth ADM0). Only points inside the "
                 "selection are simulated. Useful to focus a Resolution "
                 "76 study on specific administrations.",
        )
    else:
        grid_step = str(prev.get("grid_step_deg") or "")
        gpts = str(prev.get("gso_pointing_step_deg") or "")
        n_geom = str(prev.get("n_geom_max") or "")
        country_codes = list(prev.get("country_codes") or [])

    if method == "method_3":
        with st.expander("Method 3 — manual geometry (empty = joint WCGA on megaconstellation)",
                          expanded=False):
            col_m1, col_m2, col_m3 = st.columns(3)
            with col_m1:
                es_lat = st.text_input(
                    "ES latitude (°)",
                    value=str(prev.get("geometry_es_lat") or ""),
                    help="Engine key: `geometry_es_lat`. Empty = joint WCGA.",
                )
            with col_m2:
                es_lon = st.text_input(
                    "ES longitude (°)",
                    value=str(prev.get("geometry_es_lon") or ""),
                    help="Engine key: `geometry_es_lon`.",
                )
            with col_m3:
                gso_lon = st.text_input(
                    "GSO satellite longitude (°)",
                    value=str(prev.get("geometry_gso_lon") or ""),
                    help="Engine key: `geometry_gso_lon`.",
                )
    else:
        es_lat = es_lon = gso_lon = ""

    if method in ("method_1", "method_3", "method_4"):
        with st.expander("4. WCG search (S.1503-4 §D.3)", expanded=False):
            wcga_s1503 = st.checkbox(
                "Use S.1503-4 WCGA algorithm",
                value=bool(prev.get("wcga_s1503", True)),
                help="Engine key: `wcga_s1503`. Normative S.1503-4 §D.3.1 — "
                     "latitude sweep + (θ, φ) grid + boundary binary search "
                     "for α₀ / ε₀. Disable only for manual geometry.",
            )
            wcga_no_mask_symmetry = st.checkbox(
                "Full θ — no mask symmetry",
                value=bool(prev.get("wcga_no_mask_symmetry", False)),
                help="Engine key: `wcga_no_mask_symmetry`. By default the WCGA "
                     "assumes a mask symmetric in Δlon (θ ∈ [0, π]). Enable for "
                     "asymmetric masks (slower).",
            )
            apply_table8_egso = st.checkbox(
                "Apply Table 8 εGSO gate (S.1503-4)",
                value=not bool(prev.get("disable_gso_min_elevation", False)),
                help="Engine key: `disable_gso_min_elevation` (= not this box). "
                     "Table 8 εGSO = min GSO-arc elevation (20° ≥17 GHz, 10° "
                     "<17 GHz) in the WCGD AND-branch (§D3.1.2) and the temporal "
                     "gate. DEFAULT ON: apply εGSO per the literal S.1503-4. "
                     "OFF: Table 8 NOT applied — the WCGA may place the "
                     "worst-case ES at high latitude (matches ITU BR / "
                     "S.1503-2). Applies to every system in the aggregate.",
            )
            col_w1, col_w2 = st.columns(2)
            with col_w1:
                s1503_step = st.number_input(
                    "WCGA grid step (°)", 0.01, 30.0,
                    float(prev.get("s1503_step_deg", 1.0)), step=0.1, format="%.2f",
                    help="Engine key: `s1503_step_deg`. Latitude step of the "
                         "WCGA grid; smaller = finer search, slower.",
                )
                gso_lon_mode = select_described(
                    "GSO longitude mode",
                    ["arc_optimal", "es_meridian"], _GSO_LON_DESC,
                    index=0 if prev.get("gso_longitude_mode") == "arc_optimal" else 1,
                    help="Engine key: `gso_longitude_mode`.",
                )
            with col_w2:
                alpha_method = select_described(
                    "α computation method",
                    ["sweep", "analytical"], _ALPHA_DESC,
                    index=0 if prev.get("alpha_method") == "sweep" else 1,
                    help="Engine key: `alpha_method`.",
                )
        # Time step — separate retractable topic (mirrors the single-entry page).
        with st.expander("5. Time step (dual mode — S.1503-4 §D.4.7)", expanded=False):
            col_t1, col_t2 = st.columns(2)
            with col_t1:
                fine_dt = st.text_input(
                    "Fine time step (s)",
                    value=str(prev.get("fine_time_step_s") or ""),
                    placeholder="auto (S.1503-4 §D4.2 literal)",
                    help="Engine key: `fine_time_step_s`. Refinement period used "
                         "around the WCG when dual mode is ON. Empty = literal "
                         "§D.4.2 default.",
                )
            with col_t2:
                dual_mode = select_described(
                    "Dual time step mode",
                    ["on", "off"], _DUAL_TS_DESC,
                    index=0 if prev.get("dual_time_step_mode", "on") == "on" else 1,
                    help="Engine key: `dual_time_step_mode`.",
                )
    else:
        wcga_s1503 = bool(prev.get("wcga_s1503", True))
        wcga_no_mask_symmetry = bool(prev.get("wcga_no_mask_symmetry", False))
        apply_table8_egso = not bool(prev.get("disable_gso_min_elevation", False))
        s1503_step = float(prev.get("s1503_step_deg", 1.0))
        gso_lon_mode = prev.get("gso_longitude_mode", "arc_optimal")
        alpha_method = prev.get("alpha_method", "analytical")
        dual_mode = prev.get("dual_time_step_mode", "on")
        fine_dt = str(prev.get("fine_time_step_s") or "")

    # Orbital dynamics — applies to every method's EPFD↓ simulation (per filing).
    # Section number follows the conditional WCG search + Time step expanders
    # (shown only for the WCGA-based methods 1/3/4).
    _orb_num = 6 if method in ("method_1", "method_3", "method_4") else 4
    with st.expander(f"{_orb_num}. Orbital dynamics (station keeping / precession — S.1503-4 §D6.3)", expanded=False):
        col_o1, col_o2 = st.columns(2)
        with col_o1:
            artificial_prec_mode = select_described(
                "Artificial precession",
                ["off", "on", "auto"], _ARTIFICIAL_PREC_DESC,
                index={"off": 0, "on": 1, "auto": 2}.get(
                    prev.get("artificial_prec_mode", "off"), 0),
                help="Engine key: `artificial_precession` (per filing). Default off.",
            )
            use_prec_mdb = st.checkbox(
                "Use precession from SRS MDB",
                value=bool(prev.get("use_precession_mdb", True)),
                help="Engine key: `use_precession_mdb`. Override RAAN rate with "
                     "each SRS `precession_deg_day` (when present).",
            )
        with col_o2:
            apply_sk = st.checkbox(
                "Apply Wdelta (station keeping)",
                value=bool(prev.get("apply_station_keeping", True)),
                help="Engine key: `apply_station_keeping_wdelta`. S.1503-4 §D6.3.4: "
                     "RAAN offset ±Wdelta·(2t/T_run−1) over the run, per filing. "
                     "Reads ±Wdelta from each SRS when station keeping is declared.",
            )

    # Convolution tail truncation (S.1588 Annex 1 §1) — methods that convolve CCDFs.
    # NOTE: inside a form widgets don't rerun until submit, so the floor field
    # stays editable regardless of the checkbox; it is simply ignored when
    # "Truncate low-probability tail" is left unticked.
    truncate_tail = bool(prev.get("truncate_tail", False))
    truncate_tail_pct = str(prev.get("truncate_tail_pct") or "")
    with st.expander(f"{_orb_num + 1}. Convolution tail (S.1588 Annex 1 §1) — optional",
                      expanded=False):
        truncate_tail = st.checkbox(
            "Truncate low-probability tail",
            value=truncate_tail,
            help="Engine key: `truncate_tail`. S.1588 Annex 1 §1: the "
                 "convolution drives the high-power tail to extremely low, "
                 "unreliable probabilities. When enabled, the aggregate "
                 "curve is cut at the shortest % of time (drops the ~0 % "
                 "end of the curve).",
        )
        truncate_tail_pct = st.text_input(
            "Tail floor (% of time)",
            value=truncate_tail_pct,
            placeholder="auto (smallest per-system %)",
            help="Engine key: `truncate_tail_pct`. Only used when the "
                 "checkbox above is ticked. Output points exceeded for "
                 "less than this % of time are removed. Empty = auto: "
                 "the smallest positive % present in the per-system CCDFs.",
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
    _nsteps = _i(num_steps) or 86_400
    _step = _f(s1503_step) if isinstance(s1503_step, str) else (
        float(s1503_step) if isinstance(s1503_step, (int, float)) else 1.0
    )
    _gs = _f(grid_step) if isinstance(grid_step, str) else (
        float(grid_step) if isinstance(grid_step, (int, float)) else 30.0
    )
    _gp = _f(gpts) if isinstance(gpts, str) else (
        float(gpts) if isinstance(gpts, (int, float)) else _gs
    )
    _melev = _f(min_elev) if isinstance(min_elev, str) else 10.0
    _n_sys = max(1, len(sel_ids))
    # Real N_sat per filing — list from MDB
    _sat_counts: list[int] = []
    _real_count_ok = True
    for sid in sel_ids:
        s_row = storage.get_system(sid) or {}
        n = _si.count_satellites(s_row.get("srs_path", ""), s_row.get("ntc_id"))
        if n <= 0:
            _real_count_ok = False
            n = 20  # fallback per filing
        _sat_counts.append(n)
    _n_sat_avg = (sum(_sat_counts) // max(1, len(_sat_counts))) if _sat_counts else 20
    _n_sat_total = sum(_sat_counts) if _sat_counts else 0

    # ── S.1503-4 §D4 time step / NSTEPS preview (representative: first system) ──
    _tsp = None
    if sel_ids:
        _rep_row = storage.get_system(sel_ids[0]) or {}
        _fmin = _fmax = None
        try:
            _bands = _si.frequency_bands(_rep_row.get("srs_path", ""),
                                         _rep_row.get("ntc_id"))
            _pfd = [m for m in _bands.get("masks", []) if m.get("type") == "PFD"]
            if _pfd:
                _fmin = min(float(m["freq_min_ghz"]) for m in _pfd)
                _fmax = max(float(m["freq_max_ghz"]) for m in _pfd)
        except Exception:  # noqa: BLE001
            _fmin = _fmax = None
        _sim_freq = None
        if art22_leaf is not None and art22_leaf.get("frequency_run_ghz"):
            _sim_freq = float(art22_leaf["frequency_run_ghz"])
        elif prev.get("simulation_frequency_ghz"):
            _sim_freq = float(prev["simulation_frequency_ghz"])
        _ref_bw = (float(art22_leaf["reference_bandwidth_khz"])
                   if art22_leaf is not None and art22_leaf.get("reference_bandwidth_khz")
                   else 40.0)
        _diam_m = _f(diam) or 1.2
        _tsp = _est.preview_time_step(
            srs_path=_rep_row.get("srs_path", ""),
            ntc_id=_rep_row.get("ntc_id"),
            diameter_m=_diam_m, service=service,
            freq_min_ghz=_fmin, freq_max_ghz=_fmax,
            simulation_frequency_ghz=_sim_freq,
            reference_bandwidth_khz=_ref_bw,
            repeating=None,
            nhit=int(prev.get("s1503_nhit", 16) or 16),
            phi_coarse_deg=float(prev.get("s1503_phi_coarse_deg", 1.5) or 1.5),
            artificial_precession=(artificial_prec_mode == "on"),
            min_elevation_deg=_melev or 10.0,
            num_steps_override=_i(num_steps),
            fine_step_override=_f(fine_dt),
            coarse_step_override=_f(dt),
        )
        if _tsp.ok:
            _nsteps = _tsp.nsteps
    est = _est.estimate_aggregate(
        method=method, n_systems=_n_sys,
        n_sat_per_system=_n_sat_avg, n_time_steps=_nsteps,
        s1503_step_deg=_step or 1.0,
        grid_step_deg=_gs or 30.0,
        country_codes=list(country_codes) if country_codes else None,
        gso_pointing_step_deg=_gp,
        min_elevation_deg=_melev,
    )
    with st.container(border=True):
        src_note = "(from MDB)" if _real_count_ok else "(partial — some fallback)"
        cal_tag = (
            f" · calibrated from {_cal.get('n_samples', 0)} past run(s)"
            if _cal.get("calibrated") else " · heuristic baseline"
        )
        st.markdown(
            f"**Estimated workload** · N_sat avg = {_n_sat_avg} · total = {_n_sat_total} {src_note}{cal_tag}"
        )
        if _tsp is not None and _tsp.ok:
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
            st.caption(
                f"S.1503-4 §D4 · representative (1st of {_n_sys} system(s); "
                "per-system Δt/N may differ) · " + " · ".join(_tsp.notes)
            )
        elif _tsp is not None:
            st.caption(f"Time step preview unavailable — {_tsp.error}.")
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

    submit = st.form_submit_button("Launch aggregation", type="primary", icon=":material/play_circle:")

if submit:
    if len(sel_ids) < 2:
        st.error("Select at least 2 systems.")
        st.stop()

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

    def _set(key, value):
        if value is not None:
            params[key] = value

    _set("num_time_steps", _i(num_steps))
    _set("time_step_s", _f(dt))
    _set("min_elevation_deg", _f(min_elev))
    d = _f(diam)
    if d is not None and d > 0:
        params["es_antenna_diameter_m"] = d
    params["wcga_s1503"] = bool(wcga_s1503)
    params["wcga_no_mask_symmetry"] = bool(wcga_no_mask_symmetry)
    params["disable_gso_min_elevation"] = not bool(apply_table8_egso)
    _set("s1503_step_deg", _f(str(s1503_step)) if not isinstance(s1503_step, (int, float)) else float(s1503_step))
    params["gso_longitude_mode"] = gso_lon_mode
    params["alpha_method"] = alpha_method
    params["dual_time_step_mode"] = dual_mode
    _set("fine_time_step_s", _f(fine_dt))
    if method == "method_2":
        _set("grid_step_deg", _f(grid_step))
        _set("gso_pointing_step_deg", _f(gpts))
        _set("n_geom_max", _i(n_geom))
        if country_codes:
            params["country_codes"] = list(country_codes)
    if method == "method_3":
        _set("geometry_es_lat", _f(es_lat))
        _set("geometry_es_lon", _f(es_lon))
        _set("geometry_gso_lon", _f(gso_lon))
    # Orbital dynamics (per filing). artificial_precession only sent when forced.
    if artificial_prec_mode == "on":
        params["artificial_precession"] = True
    elif artificial_prec_mode == "off":
        params["artificial_precession"] = False
    params["use_precession_mdb"] = bool(use_prec_mdb)
    params["apply_station_keeping"] = bool(apply_sk)

    # Convolution tail truncation (S.1588 Annex 1 §1) — convolving methods.
    params["truncate_tail"] = bool(truncate_tail)
    if truncate_tail:
        _ttp = _f(truncate_tail_pct)
        if _ttp is not None and _ttp > 0:
            params["truncate_tail_pct"] = _ttp

    # Article 22 scenario leaf (section 1) overrides service / ES antenna /
    # ref BW / frequency run for the whole aggregate. No mask_id pinned —
    # each filing keeps its own PFD mask.
    if art22_leaf is not None:
        params["service"] = str(art22_leaf["service"])
        params["es_antenna_diameter_m"] = float(art22_leaf["rf_diam_m"])
        params["reference_bandwidth_khz"] = float(art22_leaf["reference_bandwidth_khz"])
        params["simulation_frequency_ghz"] = float(art22_leaf["frequency_run_ghz"])
    elif _carried_art22 is not None:
        if _carried_art22.get("reference_bandwidth_khz") is not None:
            params["reference_bandwidth_khz"] = float(_carried_art22["reference_bandwidth_khz"])
        if _carried_art22.get("simulation_frequency_ghz") is not None:
            params["simulation_frequency_ghz"] = float(_carried_art22["simulation_frequency_ghz"])

    set_persisted_state("s1588.form", {
        "reference_bandwidth_khz": params.get("reference_bandwidth_khz"),
        "simulation_frequency_ghz": params.get("simulation_frequency_ghz"),
        "system_ids": sel_ids, "method": method,
        "num_time_steps": _i(num_steps), "time_step_s": _f(dt),
        "min_elevation_deg": _f(min_elev), "service": service,
        "es_antenna_diameter_m": _f(diam),
        "grid_step_deg": _f(grid_step),
        "gso_pointing_step_deg": _f(gpts),
        "n_geom_max": _i(n_geom),
        "country_codes": list(country_codes) if country_codes else [],
        "geometry_es_lat": es_lat, "geometry_es_lon": es_lon,
        "geometry_gso_lon": gso_lon,
        "wcga_s1503": bool(wcga_s1503),
        "wcga_no_mask_symmetry": bool(wcga_no_mask_symmetry),
        "disable_gso_min_elevation": not bool(apply_table8_egso),
        "s1503_step_deg": float(s1503_step) if isinstance(s1503_step, (int, float)) else _f(str(s1503_step)),
        "dual_time_step_mode": dual_mode, "fine_time_step_s": fine_dt,
        "gso_longitude_mode": gso_lon_mode, "alpha_method": alpha_method,
        "artificial_prec_mode": artificial_prec_mode,
        "use_precession_mdb": bool(use_prec_mdb),
        "apply_station_keeping": bool(apply_sk),
        "truncate_tail": bool(truncate_tail),
        "truncate_tail_pct": truncate_tail_pct,
    })

    run_id = launcher.launch_s1588(method=method, system_ids=sel_ids, params=params)
    set_current_run_id(run_id)
    st.toast(f"Run `{run_id}` launched ({method})",
              icon=":material/play_circle:")
    # Jump straight to Status — `st.switch_page` runs while the launch
    # block is still on the call stack, so no intermediate button is
    # rendered (which would vanish on rerun and never fire). The run id
    # travels via the `query_params` kwarg (switch_page resets the params).
    st.switch_page("pages/7_Status.py", query_params={"run_id": run_id})
