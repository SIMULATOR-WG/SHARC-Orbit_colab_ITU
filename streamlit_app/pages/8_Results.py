"""Results — CCDF, normative percentiles, EPFD timeline.

Reads the artifact `sim_data.json` produced by the Single-entry / Aggregate
workers and renders it. Falls back to a demo chart when no artifact is available.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import streamlit as st

from lib import RUNS_DIR, plots, storage, theme, tour
from lib.manual import help_expander
from lib.state import current_run_id, set_current_run_id

st.set_page_config(page_title="Results · SHARC-Orbit", page_icon=":material/insights:", layout="wide")
theme.inject()

st.title("Results")
help_expander("results")
tour.maybe_render("results")
st.caption("CCDF, normative percentiles, time series (Article 22 / Resolution 76).")


def _load_sim_data(run_id: str) -> dict[str, Any] | None:
    p = RUNS_DIR / run_id / "sim_data.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _limit_curves(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Build limit_curves for Article 22 (single-entry) and Resolution 76 (aggregate)."""
    out: list[dict[str, Any]] = []
    art22 = data.get("article22") or {}
    res76 = data.get("resolution76") or {}
    method = data.get("method") or data.get("kind")
    a22_pts = art22.get("limits") or []
    r76_pts = res76.get("limits") or []

    def _split(pts):
        return [float(p[0]) for p in pts], [float(p[1]) for p in pts]

    is_single_entry = method is None or data.get("kind") in ("s1503", "single")
    if a22_pts:
        bins_db, pct = _split(a22_pts)
        out.append({
            "name": f"Article 22 limit{' (single-entry)' if is_single_entry else ''}",
            "epfd": bins_db, "percent": pct,
            "color": "#ef4444", "dash": "dash", "width": 1.6,
        })
    if r76_pts:
        bins_db, pct = _split(r76_pts)
        out.append({
            "name": "Resolution 76 limit (aggregate)",
            "epfd": bins_db, "percent": pct,
            "color": "#fbbf24", "dash": "dot", "width": 1.6,
        })
    return out


def _plot_ccdf(data: dict[str, Any]) -> None:
    series: list[dict[str, Any]] = []
    bins = data.get("ccdf_bins_db") or []
    pct = data.get("ccdf_pct") or []
    method = data.get("method") or data.get("kind") or "result"
    color = {
        "method_1": "#ff8c42", "method_2": "#ef4444", "method_3": "#34d399",
        "method_4": "#a78bfa", "method_5": "#60a5fa",
    }.get(method, "#4fd1c5")

    # method_4: plot ALL per-WCG curves equally — user judges which is worst.
    # The top-level (worst) is suppressed to avoid duplication.
    is_method_4 = method == "method_4"
    if bins and pct and not is_method_4:
        series.append({"name": method, "epfd": bins, "percent": pct,
                        "color": color, "width": 2.4})

    # Palette for per-WCG curves
    _palette = [
        "#ff8c42", "#34d399", "#a78bfa", "#60a5fa", "#f472b6",
        "#fbbf24", "#22d3ee", "#ef4444", "#84cc16", "#e879f9",
    ]
    for w in data.get("per_wcg") or []:
        if not (w.get("ccdf_bins_db") and w.get("ccdf_pct")):
            continue
        wi = int(w.get("wcg_index", -1))
        wc = w.get("wcg") or w
        es_la = wc.get("es_lat_deg") if isinstance(wc, dict) else None
        es_lo = wc.get("es_lon_deg") if isinstance(wc, dict) else None
        max_e = w.get("max_epfd_dbw")
        tag = f"WCG#{wi}"
        if es_la is not None and es_lo is not None:
            tag += f" · ES({es_la:.1f}°,{es_lo:.1f}°)"
        if isinstance(max_e, (int, float)):
            tag += f" · max {max_e:.2f} dB"
        series.append({
            "name": tag,
            "epfd": w["ccdf_bins_db"], "percent": w["ccdf_pct"],
            "color": _palette[wi % len(_palette)] if wi >= 0 else "#a78bfa",
            "width": 1.8,
        })

    # §D5.1.4.2 track-duration: overlay each slide-window set's CCDF (light);
    # the headline curve is their worst-per-level envelope.
    for p in data.get("per_window") or []:
        if p.get("ccdf_bins_db") and p.get("ccdf_pct"):
            wi = int(p.get("window_index", -1))
            series.append({
                "name": f"window set #{wi}",
                "epfd": p["ccdf_bins_db"], "percent": p["ccdf_pct"],
                "color": "rgba(148,163,184,0.55)", "width": 1.0, "dash": "dot",
            })

    # Grid convolution (method_2 / method_5): overlay the convolved CCDF of each
    # grid point (light); the headline is their worst-per-level envelope.
    if method in ("method_2", "method_5"):
        for p in data.get("per_point") or []:
            if p.get("ccdf_bins_db") and p.get("ccdf_pct"):
                series.append({
                    "name": f"pt#{p.get('index')} (conv.)",
                    "epfd": p["ccdf_bins_db"], "percent": p["ccdf_pct"],
                    "color": "rgba(96,165,250,0.55)", "width": 1.3,
                })

    # post_sum overlay (method_3)
    ps = data.get("post_sum") or {}
    if ps.get("ccdf_bins_db") and ps.get("ccdf_pct"):
        series.append({
            "name": "post_sum (convolution complement)",
            "epfd": ps["ccdf_bins_db"], "percent": ps["ccdf_pct"],
            "color": "#fbbf24", "dash": "dash", "width": 1.5,
        })

    # per-system overlays (light) for method_1/3
    for i, p in enumerate(data.get("per_system") or []):
        if p.get("ccdf_bins_db") and p.get("ccdf_pct"):
            series.append({
                "name": f"single-entry [{i}]",
                "epfd": p["ccdf_bins_db"], "percent": p["ccdf_pct"],
                "color": "#94a3b8", "width": 1.0, "dash": "dot",
            })

    # Overlay external reference curves from uploaded results MDB(s).
    _ext_palette = ["#f59e0b", "#10b981", "#e11d48", "#8b5cf6", "#06b6d4", "#eab308"]
    ext_curves = _external_mdb_curves()
    for i, c in enumerate(ext_curves):
        series.append({
            "name": c["label"],
            "epfd": c["epfd"], "percent": c["percent"],
            "color": _ext_palette[i % len(_ext_palette)],
            "width": 2.0, "dash": "dashdot",
        })

    if not series:
        st.info("No CCDF in this artifact.")
        return
    h = int(st.session_state.get("ccdf_height", 520))
    x_min, x_max = st.session_state.get("ccdf_xrange", (-220, -140))
    fig = plots.ccdf_chart(
        series, title=f"CCDF — {method}",
        limit_curves=_limit_curves(data),
        height=h, x_min=x_min, x_max=x_max,
    )
    st.plotly_chart(fig, width='stretch')
    _render_run_wcg(data)
    _render_external_wcg(ext_curves)


def _external_mdb_curves() -> list[dict[str, Any]]:
    """Upload results .mdb file(s) and parse their CCDF curves for overlay.

    The "Compare external MDB" feature. Re-parses on each
    rerun (parsing is fast); failures are surfaced per-file without
    blocking the chart.
    """
    from lib import mdb_results
    files = st.file_uploader(
        "Compare external results MDB(s)",
        type=["mdb"], accept_multiple_files=True, key="ccdf_ext_mdb",
        help="Upload SHARC results `.mdb` file(s); their CCDF curves "
             "(table `cdf`) overlay on this chart.",
    )
    curves: list[dict[str, Any]] = []
    for f in files or []:
        try:
            label = f.name.rsplit(".", 1)[0]
            curves.extend(
                mdb_results.parse_results_mdb(f.getvalue(), source_label=label)
            )
        except Exception as exc:  # noqa: BLE001
            st.warning(f"{f.name}: {exc}")
    if curves:
        st.caption(f"{len(curves)} external curve(s) overlaid.")
    return curves


def _render_run_wcg(data: dict[str, Any]) -> None:
    """Express, in text, the worst-case geometry SHARC-Orbit found for this run.

    Mirrors :func:`_render_external_wcg` so the software's WCG sits next to the
    overlaid external MDB's WCG for direct comparison. Reuses the per-method
    geometry collector; caps the list for grid methods (method_2/5).
    """
    geos = [g for g in (_collect_geometry_points(data).get("geometries") or [])
            if g.get("es_lat") is not None and g.get("gso_lon") is not None]
    if not geos:
        return
    with st.container(border=True):
        st.caption("**Worst-case geometry found by SHARC-Orbit**")
        _CAP = 12
        for g in geos[:_CAP]:
            me = g.get("max_epfd")
            me_txt = (f" · max EPFD↓ {me:.2f} dBW/m²"
                      if isinstance(me, (int, float)) else "")
            st.markdown(
                f"- **{g.get('label', 'WCG')}** · "
                f"ES ({g['es_lat']:.2f}°, {g['es_lon']:.2f}°), "
                f"GSO {g['gso_lon']:.2f}°{me_txt}"
            )
        if len(geos) > _CAP:
            st.caption(f"… +{len(geos) - _CAP} more geometries (see map below)")


def _render_external_wcg(curves: list[dict[str, Any]]) -> None:
    """Express, in text, the WCG and run parameters of each overlaid external MDB.

    The results `.mdb` carries, per result, the worst-case geometry
    (`worst_es_lat/long`, `worst_gso_long`) and the run settings (time steps,
    reference bandwidth, frequency, antenna). Show them so the overlaid CCDF is
    traceable to the geometry and the parameters that produced it.
    """
    rows = [c for c in curves if c.get("wcg") or c.get("params")]
    if not rows:
        return

    def _fmt_step(s: Any) -> str | None:
        if not isinstance(s, (int, float)) or s <= 0:
            return None
        return f"{s * 1000.0:.0f} ms" if s < 1.0 else f"{s:.3f} s"

    with st.container(border=True):
        st.caption("**Overlaid external MDB(s) — geometry & run parameters**")
        for c in rows:
            ntc = f" · ntc {c['ntc_id']}" if c.get("ntc_id") else ""
            passed = c.get("pass")
            verdict = "" if passed is None else (
                " · :green[pass]" if passed else " · :red[fail]")
            st.markdown(f"- **{c['label']}**{ntc}{verdict}")
            w = c.get("wcg")
            if w:
                st.caption(
                    f"WCG: ES ({w['es_lat_deg']:.2f}°, {w['es_lon_deg']:.2f}°), "
                    f"GSO {w['gso_lon_deg']:.2f}°"
                )
            p = c.get("params") or {}
            bits: list[str] = []
            if p.get("num_timesteps"):
                bits.append(f"N steps {int(p['num_timesteps']):,}")
            fs, cs = _fmt_step(p.get("fine_timestep_s")), _fmt_step(p.get("coarse_timestep_s"))
            if fs:
                bits.append(f"Δt fine {fs}")
            if cs:
                bits.append(f"Δt coarse {cs}")
            if p.get("reference_bandwidth_khz"):
                bits.append(f"RefBW {p['reference_bandwidth_khz']:g} kHz")
            if p.get("freq_ghz"):
                bits.append(f"f {p['freq_ghz']:g} GHz")
            if p.get("dish_size_m"):
                bits.append(f"Ø {p['dish_size_m']:g} m")
            if p.get("beamwidth_deg"):
                bits.append(f"BW {p['beamwidth_deg']:g}°")
            if p.get("gain_pattern"):
                bits.append(f"pattern {p['gain_pattern']}")
            pc = p.get("pct_complete")
            if isinstance(pc, (int, float)) and 0 < pc < 100.0:
                bits.append(f":orange[{pc:g}% complete]")
            if bits:
                st.caption(" · ".join(bits))


_MISSING = object()


def _truncation_note(data: dict[str, Any]) -> str:
    """Help text for percentiles nulled by the S.1588 tail truncation."""
    floor = data.get("truncation_floor_pct")
    base = ("Below the truncation floor — removed by the S.1588 "
            "low-probability tail truncation (no reliable value).")
    if isinstance(floor, (int, float)):
        return f"{base} Floor: {floor:g} % of time."
    return base


def _render_metrics(data: dict[str, Any]) -> None:
    # Multi-configuration labelling (R2): results are PER configuration.
    _mc = data.get("multi_config") or {}
    if _mc.get("type") == "M":
        st.info(
            f"Mutually-exclusive configuration **{_mc.get('config_label') or '?'}"
            f"** of **{_mc.get('nbr_config') or '?'}** for this notice "
            f"(label via {_mc.get('label_source') or '—'}). Do not aggregate "
            "EPFD with the sibling configurations (S.1503-4 §D2.1).",
            icon=":material/call_split:",
        )
    cols = st.columns(5)
    me = data.get("max_epfd_dbw_m2_40khz")
    with cols[0]:
        st.metric(
            "Max EPFD↓",
            f"{me:.2f}" if isinstance(me, (int, float)) else "—",
        )
    pcts = data.get("percentiles") or {}
    for i, key in enumerate(["10%", "1%", "0.1%", "0.01%"]):
        # accept both '10%' and '10.0%' keys (engine emits %)
        v = pcts.get(key, pcts.get(key.replace("%", ".0%"), _MISSING))
        with cols[i + 1]:
            if v is None:
                # Worker emits null for percentiles below the truncation
                # floor when the S.1588 tail truncation is active.
                st.metric(f"@ {key}", "n/a", help=_truncation_note(data))
                st.caption("below truncation floor")
            else:
                st.metric(
                    f"@ {key}",
                    f"{v:.2f}" if isinstance(v, (int, float)) else "—",
                )


def _fmt_dt(s: Any) -> str:
    """Human time step: ms below 1 s, else seconds."""
    if not isinstance(s, (int, float)) or s <= 0:
        return "—"
    return f"{s * 1000.0:.0f} ms" if s < 1.0 else f"{s:.3f} s"


def _render_time_step(data: dict[str, Any]) -> None:
    """Dual time step: the Δt values used and how many fine/coarse steps ran.

    Reads the single-entry worker's ``dual_time_step`` block, falling back to
    the ``config`` block emitted by the visualization export for other run
    kinds (the key names differ between the two).
    """
    dts = data.get("dual_time_step")
    if dts:
        mode = str(dts.get("mode") or "—")
        fine = dts.get("fine_step_s")
        coarse = dts.get("coarse_step_s")
        ncoarse = dts.get("ncoarse")
        n_total = dts.get("num_time_steps")
    else:
        cfg = data.get("config") or {}
        mode = str(cfg.get("dual_time_step_mode") or "—")
        fine = cfg.get("dual_fine_step_s_used")
        coarse = cfg.get("dual_coarse_step_s_used")
        ncoarse = cfg.get("dual_ncoarse_used")
        n_total = cfg.get("num_time_steps_used")
        dts = cfg  # counts share the same key names below
    n_fine = dts.get("n_fine_steps_executed")
    n_coarse = dts.get("n_coarse_steps_executed")
    n_exec = dts.get("n_exec_steps")

    # Nothing useful to show (e.g. older runs without the fields).
    if fine is None and n_fine is None and n_total is None:
        return

    with st.container(border=True):
        st.subheader("Temporal sampling (S.1503-4 §D4.7 dual time step)")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Mode", "dual (fine+coarse)" if mode == "s1503" else mode)
        c2.metric("Δt fine (§D4.2)", _fmt_dt(fine))
        c3.metric("Δt coarse (§D4.7)", _fmt_dt(coarse),
                  help=f"= fine × Ncoarse ({ncoarse})" if ncoarse else None)
        c4.metric(
            "N (fine-equivalent)",
            f"{int(n_total):,}" if isinstance(n_total, (int, float)) else "—",
            help="Total run length in fine-step units (S.1503-4 NSTEPS).",
        )

        if n_fine is not None or n_coarse is not None:
            d1, d2, d3 = st.columns(3)
            d1.metric(
                "Fine steps executed",
                f"{int(n_fine):,}" if isinstance(n_fine, (int, float)) else "—",
            )
            d2.metric(
                "Coarse steps executed",
                f"{int(n_coarse):,}" if isinstance(n_coarse, (int, float)) else "—",
            )
            d3.metric(
                "Total iterations",
                f"{int(n_exec):,}" if isinstance(n_exec, (int, float)) else "—",
                help="Actual time loop iterations (fine + coarse). Lower than the "
                     "fine-equivalent N because each coarse step spans Ncoarse "
                     "fine steps.",
            )
            if isinstance(n_exec, (int, float)) and isinstance(n_total, (int, float)) and n_total:
                saved = 100.0 * (1.0 - float(n_exec) / float(n_total))
                if saved > 0.5:
                    st.caption(
                        f"Dual stepping ran {int(n_exec):,} iterations instead of "
                        f"{int(n_total):,} ({saved:.1f}% fewer) by coarsening "
                        "non-critical regions."
                    )


def _render_track_duration(data: dict[str, Any]) -> None:
    """S.1503-4 §D5.1.4.2 track-duration variant: window parameters + verdict note."""
    td = data.get("track_duration")
    if not td:
        return
    with st.container(border=True):
        st.subheader("Track duration — sliding windows (S.1503-4 §D5.1.4.2)")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("MIN_DURATION", f"{td.get('min_duration_s', 0):.0f} s")
        c2.metric("N_SW (steps/window)", f"{int(td.get('n_sw', 0)):,}")
        c3.metric(
            "N_TW (window sets)", f"{int(td.get('n_tw', 0)):,}",
            help="Independent slide-window phase alignments analysed "
                 "(offset by MIN_SLIDING_TIME = N_MSL fine steps).",
        )
        c4.metric("MIN_SLIDING_TIME", f"{td.get('min_sliding_time_s', 0):.2f} s")
        pw = data.get("per_window") or []
        worst = data.get("worst_window_index", -1)
        st.caption(
            f"Ran {len(pw)} window set(s); headline CCDF is the worst-per-level "
            "envelope across sets (the network complies only if **every** set "
            "complies). "
            + (f"Worst peak in set #{worst}. " if isinstance(worst, int) and worst >= 0 else "")
            + "Dual time step is disabled for this variant (defined in fine steps)."
        )


def _render_art22_scenario(data: dict[str, Any]) -> None:
    """Textual normative config (Article 22 scenario) backing the limit curve."""
    a = data.get("article22") or {}
    if not a:
        return
    bits: list[str] = []
    if a.get("rr_reference"):
        bits.append(str(a["rr_reference"]))
    if a.get("service"):
        bits.append(str(a["service"]))
    fr_mhz = a.get("frequency_run_mhz")
    if isinstance(fr_mhz, (int, float)):
        bits.append(f"run {fr_mhz:.2f} MHz")
    diam_cm = a.get("_epfd_rf_diam_cm")
    if isinstance(diam_cm, (int, float)):
        bits.append(f"ES {float(diam_cm) / 100.0:.2f} m")
    bw = a.get("reference_bandwidth_khz")
    if isinstance(bw, (int, float)):
        bits.append(f"{float(bw):.0f} kHz")
    if a.get("_epfd_rf_pattern_rr"):
        bits.append(str(a["_epfd_rf_pattern_rr"]))
    if bits:
        st.caption("**Normative config (Article 22):** " + " · ".join(bits))


def _render_compliance(data: dict[str, Any]) -> None:
    cd = data.get("compliance")
    if cd is None:
        return
    if cd == "pass":
        st.markdown(theme.pill("COMPLIANT", "ok"), unsafe_allow_html=True)
    elif cd == "fail":
        st.markdown(theme.pill("NON-COMPLIANT", "error"), unsafe_allow_html=True)
    else:
        st.markdown(theme.pill(str(cd).upper(), "warn"), unsafe_allow_html=True)
        msg = data.get("message")
        if msg:
            st.info(msg)


_PALETTE = [
    "#ff8c42", "#34d399", "#a78bfa", "#60a5fa", "#f472b6",
    "#fbbf24", "#22d3ee", "#ef4444", "#84cc16", "#e879f9",
]


def _collect_geometry_points(data: dict[str, Any]) -> dict[str, Any]:
    """Build ES + GSO + grid point lists per method. Returns dict with all lists."""
    method = data.get("method")
    kind = data.get("kind")
    es: list[dict[str, Any]] = []
    gso: list[dict[str, Any]] = []
    grid: list[dict[str, Any]] = []
    grid_gso_lons: list[float] = []
    geometries: list[dict[str, Any]] = []  # for click-to-modal CCDF picker

    if kind in ("single", "s1503") or method == "method_1":
        # single-entry: from data["wcg"]; or method_1 per_system
        if kind in ("single", "s1503"):
            wcg = data.get("wcg") or {}
            if wcg:
                color = _PALETTE[0]
                es.append({"lat": wcg["es_lat_deg"], "lon": wcg["es_lon_deg"],
                            "label": "WCG", "color": color,
                            "max_epfd": data.get("max_epfd_dbw_m2_40khz")})
                gso.append({"lon": wcg["gso_lon_deg"], "label": "GSO"})
                geometries.append({
                    "label": "WCG", "ccdf_bins_db": data.get("ccdf_bins_db"),
                    "ccdf_pct": data.get("ccdf_pct"),
                    "color": color,
                    "max_epfd": data.get("max_epfd_dbw_m2_40khz"),
                    "es_lat": wcg["es_lat_deg"], "es_lon": wcg["es_lon_deg"],
                    "gso_lon": wcg["gso_lon_deg"],
                })
        else:
            for i, p in enumerate(data.get("per_system") or []):
                w = (p.get("wcg") or {})
                if not w or w.get("es_lat_deg") is None:
                    continue
                color = _PALETTE[i % len(_PALETTE)]
                es.append({"lat": w["es_lat_deg"], "lon": w["es_lon_deg"],
                            "label": f"sys{i}", "color": color,
                            "max_epfd": p.get("max_epfd_dbw")})
                gso.append({"lon": w["gso_lon_deg"], "label": f"GSO sys{i}"})
                geometries.append({
                    "label": f"system #{i}", "ccdf_bins_db": p.get("ccdf_bins_db"),
                    "ccdf_pct": p.get("ccdf_pct"),
                    "color": color,
                    "max_epfd": p.get("max_epfd_dbw"),
                    "es_lat": w["es_lat_deg"], "es_lon": w["es_lon_deg"],
                    "gso_lon": w["gso_lon_deg"],
                })
    elif method == "method_3":
        geo = data.get("geometry") or {}
        if geo:
            color = _PALETTE[0]
            es.append({"lat": geo["es_lat_deg"], "lon": geo["es_lon_deg"],
                        "label": "WCG_agg", "color": color,
                        "max_epfd": data.get("max_epfd_dbw_m2_40khz")})
            gso.append({"lon": geo["gso_lon_deg"], "label": "GSO agg"})
            geometries.append({
                "label": "joint WCG_agg",
                "ccdf_bins_db": data.get("ccdf_bins_db"),
                "ccdf_pct": data.get("ccdf_pct"),
                "color": color,
                "max_epfd": data.get("max_epfd_dbw_m2_40khz"),
                "es_lat": geo["es_lat_deg"], "es_lon": geo["es_lon_deg"],
                "gso_lon": geo["gso_lon_deg"],
            })
    elif method == "method_4":
        for w in data.get("per_wcg") or []:
            wi = int(w.get("wcg_index", -1))
            wc = w.get("wcg") or w
            la = wc.get("es_lat_deg")
            lo = wc.get("es_lon_deg")
            gl = wc.get("gso_lon_deg")
            if la is None or lo is None or gl is None:
                continue
            color = _PALETTE[wi % len(_PALETTE)] if wi >= 0 else "#a78bfa"
            es.append({"lat": la, "lon": lo, "label": f"WCG#{wi}",
                        "color": color, "max_epfd": w.get("max_epfd_dbw")})
            gso.append({"lon": gl, "label": f"GSO#{wi}", "color": color})
            geometries.append({
                "label": f"WCG#{wi}", "ccdf_bins_db": w.get("ccdf_bins_db"),
                "ccdf_pct": w.get("ccdf_pct"), "color": color,
                "max_epfd": w.get("max_epfd_dbw"),
                "es_lat": la, "es_lon": lo, "gso_lon": gl,
            })
    elif method in ("method_2", "method_5"):
        per_point = data.get("per_point") or []
        seen: set[tuple[float, float]] = set()
        seen_g: set[float] = set()
        for p in per_point:
            la, lo, gl = p.get("es_lat_deg"), p.get("es_lon_deg"), p.get("gso_lon_deg")
            if la is None or lo is None:
                continue
            k = (round(la, 3), round(lo, 3))
            if k not in seen:
                seen.add(k)
                grid.append({"lat": la, "lon": lo})
            if gl is not None and gl not in seen_g:
                seen_g.add(gl)
                grid_gso_lons.append(gl)
            if method in ("method_2", "method_5") and p.get("ccdf_bins_db"):
                geometries.append({
                    "label": f"pt#{p.get('index')} · ES({la:.1f},{lo:.1f}) · GSO {gl:.1f}",
                    "ccdf_bins_db": p.get("ccdf_bins_db"),
                    "ccdf_pct": p.get("ccdf_pct"),
                    "color": "#60a5fa",
                    "max_epfd": p.get("max_epfd_dbw"),
                    "es_lat": la, "es_lon": lo, "gso_lon": gl,
                })
    return {"es": es, "gso": gso, "grid": grid,
             "grid_gso_lons": grid_gso_lons, "geometries": geometries}


@st.dialog("CCDF at selected geometry", width="large")
def _show_geometry_ccdf(geom: dict[str, Any],
                          limits: list[dict[str, Any]] | None = None) -> None:
    st.markdown(
        f"**{geom['label']}** · ES ({geom.get('es_lat'):.2f}°, {geom.get('es_lon'):.2f}°)"
        f" · GSO {geom.get('gso_lon'):.2f}°"
    )
    if geom.get("max_epfd") is not None:
        st.metric("Max EPFD↓", f"{geom['max_epfd']:.2f}")
    bins = geom.get("ccdf_bins_db") or []
    pct = geom.get("ccdf_pct") or []
    if not bins or not pct:
        st.info("No CCDF stored for this geometry.")
        return
    fig = plots.ccdf_chart(
        [{"name": geom["label"], "epfd": bins, "percent": pct,
          "color": geom.get("color", "#4fd1c5"), "width": 2.2}],
        limit_curves=limits or [],
        title=f"CCDF — {geom['label']}",
        height=460,
    )
    st.plotly_chart(fig, width='stretch')
    if limits:
        st.caption(" · ".join(
            l.get("name", "limit") for l in limits
        ))


def _render_wcg_explanation(data: dict[str, Any]) -> None:
    """Explain WHY the single-entry WCG sits where it does (S.1503-4 §D3.1.2).

    Decomposes EPFD = PFD(mask) + G_rel(antenna) and states whether the
    geometry won as an isolated EPFD peak or via the angular-velocity tie-break
    over a flat plateau, with the per-latitude profile from the WCGA.
    """
    e = data.get("wcg_explanation")
    if not e:
        return
    wcg = data.get("wcg") or {}
    es_lat = float(wcg.get("es_lat_deg", 0.0))
    sel = e.get("selection")
    with st.expander("Why this WCG? — single-entry geometry explained", expanded=False):
        c1, c2, c3 = st.columns(3)
        c1.metric("Single-entry EPFD", f"{e['epfd_dBW']:.2f} dBW/m²/40 kHz")
        c2.metric("PFD (from mask)", f"{e['pfd_dBW']:.2f}")
        c3.metric("ES relative gain", f"{e['es_gain_rel_dB']:.2f} dB")
        st.caption(
            f"EPFD = PFD + G_rel = {e['pfd_dBW']:.2f} + ({e['es_gain_rel_dB']:.2f}) "
            f"= {e['epfd_dBW']:.2f} dBW  ·  at ES {es_lat:.1f}°"
        )
        if sel == "angular-velocity tie-break":
            lr = e.get("plateau_es_lat_range") or [es_lat, es_lat]
            st.markdown(
                f"**Selected by the angular-velocity tie-break.** "
                f"{e.get('n_tied_top_bin', 0)} latitudes tie within "
                f"**{e.get('plateau_db', 0.0):.2f} dB** (a flat EPFD plateau over ES "
                f"{lr[0]:.0f}° … {lr[1]:.0f}°). Per S.1503-4 §D3.1.2 the worst geometry "
                f"is then the one with the **lowest apparent angular velocity** "
                f"(**{e.get('angular_velocity_deg_s', 0.0):.3f}°/s** here) — it persists "
                f"longest and dominates the time statistics → ES **{es_lat:.1f}°**."
            )
        else:
            st.markdown(
                f"**Selected as an isolated EPFD peak** at ES **{es_lat:.1f}°** "
                f"(no plateau tie within 0.1 dB)."
            )
        if e.get("in_exclusion_zone") and e.get("alpha0_deg") is not None:
            st.caption(
                f"Worst satellite is INSIDE the GSO-arc exclusion zone "
                f"(α={e['alpha_deg']:.2f}° < α₀={e['alpha0_deg']:.0f}°), near the ES "
                f"boresight (off-axis {e['offaxis_deg']:.2f}°) — counted via the gain "
                f"OR-condition (S.1503-4 D5.1.4.1 Step 18)."
            )
        prof = e.get("profile") or []
        if prof:
            import plotly.graph_objects as go  # noqa: PLC0415
            xs = [p["es_lat_deg"] for p in prof]
            ys = [p["epfd_dBW"] for p in prof]
            fig = go.Figure()
            fig.add_scatter(x=xs, y=ys, mode="lines+markers", name="single-entry EPFD",
                            line=dict(color="#1f9c8a"), marker=dict(size=4))
            fig.add_scatter(x=[es_lat], y=[e["epfd_dBW"]], mode="markers", name="WCG",
                            marker=dict(color="#e0a64a", size=14, symbol="star"))
            fig.update_layout(
                height=300, margin=dict(l=10, r=10, t=10, b=10),
                xaxis_title="ES latitude (°)",
                yaxis_title="single-entry EPFD (dBW/m²/40 kHz)",
                legend=dict(orientation="h", y=1.02, x=0),
            )
            st.plotly_chart(fig, use_container_width=True)
            st.caption(
                "EPFD vs ES latitude across the WCGA latitude sweep. A flat top → "
                "the tie-break (lowest angular velocity) sets the WCG; a sharp peak → "
                "EPFD magnitude sets it."
            )


def _render_globe(data: dict[str, Any]) -> None:
    bundle = _collect_geometry_points(data)
    es, gso, grid, grid_gso_lons = (
        bundle["es"], bundle["gso"], bundle["grid"], bundle["grid_gso_lons"]
    )
    geometries = bundle["geometries"]
    if not (es or gso or grid):
        return
    method = data.get("method") or data.get("kind") or "geometry"
    limits = _limit_curves(data)
    with st.container(border=True):
        st.subheader("Geometry on the globe")
        _render_wcg_explanation(data)
        h = st.slider(
            "Globe height (px)", min_value=300, max_value=1000,
            value=int(st.session_state.get("globe_height", 520)),
            step=20, key="globe_height",
        )
        fig = plots.globe_chart(
            es_points=es, gso_points=gso, grid_points=grid,
            grid_gso_lons=grid_gso_lons,
            title=f"Geometry — {method}",
            height=h,
        )
        # Trace layout in `plots.globe_chart` (only present traces count):
        #   0: ES grid · 1: ES · 2: GSO · 3: GSO sweep
        trace_layout: list[str] = []
        if grid:
            trace_layout.append("grid")
        if es:
            trace_layout.append("es")
        if gso:
            trace_layout.append("gso")
        if grid_gso_lons:
            trace_layout.append("gso_sweep")

        select_key = f"geom_pick_{run_id}"
        chart_key = f"globe_chart_{run_id}"

        # ── Emphasise the geometry currently selected in the picker ────
        # Use Plotly's `selectedpoints` on each trace — the selected
        # marker style (amber, larger) defined in `plots.globe_chart`
        # then renders that point as highlighted on both ES and GSO.
        sel_label = st.session_state.get(select_key)
        sel_geom = next(
            (g for g in geometries if g["label"] == sel_label),
            None,
        )
        if sel_geom is not None:
            _es_key = (round(float(sel_geom.get("es_lat") or 0), 3),
                        round(float(sel_geom.get("es_lon") or 0), 3))
            _gso_lon_key = round(float(sel_geom.get("gso_lon") or 0), 3)

            def _find_idx(items: list[dict[str, Any]],
                            match: callable) -> int | None:
                for k, it in enumerate(items):
                    if match(it):
                        return k
                return None

            trace_ofs = 0
            if grid:
                idx = _find_idx(grid, lambda p: (
                    round(float(p.get("lat") or 0), 3),
                    round(float(p.get("lon") or 0), 3),
                ) == _es_key)
                if idx is not None:
                    fig.data[trace_ofs].selectedpoints = [idx]
                trace_ofs += 1
            if es:
                idx = _find_idx(es, lambda p: (
                    round(float(p.get("lat") or 0), 3),
                    round(float(p.get("lon") or 0), 3),
                ) == _es_key)
                if idx is not None:
                    fig.data[trace_ofs].selectedpoints = [idx]
                trace_ofs += 1
            if gso:
                idx = _find_idx(gso, lambda p: round(
                    float(p.get("lon") or 0), 3) == _gso_lon_key)
                if idx is not None:
                    fig.data[trace_ofs].selectedpoints = [idx]
                trace_ofs += 1
            if grid_gso_lons:
                sweep_match = [
                    k for k, lon in enumerate(grid_gso_lons)
                    if round(float(lon), 3) == _gso_lon_key
                ]
                if sweep_match:
                    fig.data[trace_ofs].selectedpoints = sweep_match[:1]

        # Globe with on_select — clicks update the selectbox below.
        event = st.plotly_chart(
            fig, width='stretch',
            on_select="rerun", selection_mode="points",
            key=chart_key,
        )

        # Translate a click into the corresponding geometry label and pre-set
        # the selectbox value before the widget renders. The chart's selection
        # event persists across reruns, so remember the last processed click
        # and only act on a NEW one — otherwise any rerun would re-apply the
        # stale selection and undo a manual pick in the selectbox below.
        sel_pts = ((event or {}).get("selection") or {}).get("points") or []
        _last_click_key = f"_last_click_{run_id}"
        if (sel_pts and geometries
                and sel_pts != st.session_state.get(_last_click_key)):
            st.session_state[_last_click_key] = sel_pts
            p = sel_pts[0]
            click_la = p.get("lat")
            click_lo = p.get("lon")
            if click_la is None or click_lo is None:
                cn = p.get("curve_number")
                pn = p.get("point_number")
                if cn is not None and pn is not None and 0 <= cn < len(trace_layout):
                    kind = trace_layout[cn]
                    src = {"grid": grid, "es": es, "gso": gso}.get(kind)
                    if src and 0 <= pn < len(src):
                        click_la = src[pn].get("lat", 0.0)
                        click_lo = src[pn].get("lon")
            if click_la is not None and click_lo is not None:
                key_click = (round(float(click_la), 3), round(float(click_lo), 3))

                def _match(g: dict[str, Any]) -> bool:
                    try:
                        return (round(float(g.get("es_lat")), 3),
                                round(float(g.get("es_lon")), 3)) == key_click
                    except (TypeError, ValueError):
                        return False

                hit = next((g for g in geometries if _match(g)), None)
                if hit is not None:
                    st.session_state[select_key] = hit["label"]

        if geometries:
            labels = [g["label"] for g in geometries]
            default_label = st.session_state.get(select_key, labels[0])
            if default_label not in labels:
                default_label = labels[0]
            choice = st.selectbox(
                "Geometry (click a point on the globe to select)",
                options=labels,
                index=labels.index(default_label),
                key=select_key,
            )
            if st.button("Show CCDF", key=f"geom_btn_{run_id}",
                          icon=":material/insights:"):
                pick = next((g for g in geometries if g["label"] == choice), None)
                if pick:
                    _show_geometry_ccdf(pick, limits=limits)


def _render_percentiles_bar(data: dict[str, Any]) -> None:
    pcts = data.get("percentiles") or {}
    if not pcts:
        return
    # Percentiles below the S.1588 truncation floor arrive as None — keep
    # them out of the bar chart and list them explicitly instead.
    plottable = {k: v for k, v in pcts.items() if isinstance(v, (int, float))}
    below = [k for k, v in pcts.items() if v is None]
    if plottable:
        fig = plots.percentiles_chart(
            plottable, title="Normative percentiles (Resolution 76)")
        st.plotly_chart(fig, width='stretch')
    if below:
        st.caption(
            "n/a (below truncation floor): " + ", ".join(below)
            + ". " + _truncation_note(data)
        )


# ─── page body ───────────────────────────────────────────────────────────────

qp = st.query_params
run_id = qp.get("run_id") or current_run_id()
runs = storage.list_runs(limit=200)
_run_ids = [r["id"] for r in runs]

# Validate against the DB, not the limited listing above — a legitimate
# older run outside the top-200 page must still open directly.
if not run_id or storage.get_run(run_id) is None:
    if runs:
        _shared = current_run_id()
        _idx = _run_ids.index(_shared) if _shared in _run_ids else 0
        run_id = st.selectbox(
            "Pick a run",
            options=_run_ids,
            index=_idx,
            format_func=lambda i: f"{i} · {storage.display_kind(next((r['kind'] for r in runs if r['id']==i), ''))} · {next((r.get('method') or '—' for r in runs if r['id']==i), '')}",
        )
    else:
        st.info("No runs yet. Launch from **Single-entry** or **Aggregate**.")

if run_id:
    set_current_run_id(run_id)
    run = storage.get_run(run_id)
    if not run:
        st.error(f"Run {run_id} not found.")
        st.stop()

    st.markdown(
        f"**Run `{run['id']}`** · type `{storage.display_kind(run['kind'])}` · method `{run.get('method') or '—'}` · status `{run['status']}`"
    )
    data = _load_sim_data(run_id)
    if not data:
        st.warning("Artifact `sim_data.json` not present yet. Wait until the run finishes.")
        st.stop()

    _render_compliance(data)
    _render_metrics(data)
    _render_art22_scenario(data)
    _render_track_duration(data)
    _render_time_step(data)

    with st.container(border=True):
        st.subheader("CCDF")
        col_h, col_x = st.columns([2, 4])
        with col_h:
            st.slider(
                "Chart height (px)", min_value=300, max_value=1200,
                value=int(st.session_state.get("ccdf_height", 520)),
                step=20, key="ccdf_height",
            )
        with col_x:
            st.slider(
                "EPFD↓ x-axis range (dBW/m²/40 kHz)",
                min_value=-260, max_value=-100,
                value=tuple(st.session_state.get("ccdf_xrange", (-220, -140))),
                step=1, key="ccdf_xrange",
            )
        _plot_ccdf(data)

    with st.container(border=True):
        st.subheader("Normative percentiles")
        _render_percentiles_bar(data)

    _render_globe(data)

    with st.expander("Raw artifact (sim_data.json)"):
        st.json(data)

    artifacts = sorted(p.name for p in (RUNS_DIR / run_id).iterdir() if p.is_file())
    st.write("**Files on disk:**", artifacts)

    # R9 — whole results package as a single .zip (built in memory on demand).
    import io as _io
    import zipfile as _zipfile
    _buf = _io.BytesIO()
    with _zipfile.ZipFile(_buf, "w", compression=_zipfile.ZIP_DEFLATED) as _zf:
        for name in artifacts:
            _zf.write(RUNS_DIR / run_id / name, arcname=f"{run_id}/{name}")
    _czip, _cxlsx = st.columns(2)
    with _czip:
        st.download_button(
            f"Download all ({run_id}.zip)",
            icon=":material/folder_zip:",
            type="primary",
            data=_buf.getvalue(),
            file_name=f"{run_id}.zip",
            mime="application/zip",
            key=f"dl_zip_{run_id}",
        )
    # R24 — ITU-style per-run workbook (run_def/result_def/results/cdf/pdf).
    with _cxlsx:
        try:
            from lib.exports import run_to_xlsx  # noqa: PLC0415
            _xbytes = run_to_xlsx(data)
            st.download_button(
                "Results workbook (.xlsx, ITU-style tabs)",
                icon=":material/table_view:",
                data=_xbytes,
                file_name=f"{run_id}_results.xlsx",
                mime=("application/vnd.openxmlformats-officedocument"
                      ".spreadsheetml.sheet"),
                key=f"dl_xlsx_{run_id}",
            )
        except Exception as _exc:  # noqa: BLE001
            st.caption(f"xlsx export unavailable: {_exc}")

    for name in artifacts:
        path = RUNS_DIR / run_id / name
        st.download_button(
            name,
            icon=":material/download:",
            data=path.read_bytes(),
            file_name=name,
            key=f"dl_{run_id}_{name}",
        )
