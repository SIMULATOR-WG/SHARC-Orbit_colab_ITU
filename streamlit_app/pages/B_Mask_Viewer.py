"""Mask viewer — reproduces the visualization features of
``visualization/mask_viewer.html`` natively in Streamlit.

Opens via query params from Uploads/Filing screens:

    ?srs_path=<path_or_id>&mask_id=<int>&ntc_id=<id>
    or
    ?mask_mdb=<path>&mask_id=<int>&ntc_id=<id>

Features (mirroring the legacy HTML viewer, minus mask conversion):

* Mask metadata panel (satellite name, frequency band, mask id, type, shape).
* Latitude slider — pick the satellite latitude slice.
* B-axis (azimuth / alpha) slider — pick a slice along axis B.
* Slice plot — line chart of PFD vs C-axis at the selected (lat, B).
* Heatmap — 2D PFD map for the selected latitude, with two axis modes
  (uniform vs proportional).
* PFD calculator — point-wise PFD by trilinear / bilinear interpolation.

Mask conversion (alpha↔az/el) is not ported; use the engine CLI for that.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import streamlit as st
import plotly.graph_objects as go

from lib import storage, theme, srs_inspect
from lib.manual import help_expander
from lib.state import (
    use_persisted_state, set_persisted_state,
    current_system_id, set_current_system_id,
    current_mask_id, set_current_mask_id,
)

st.set_page_config(
    page_title="Mask Viewer · SHARC-Orbit",
    page_icon=":material/blur_on:",
    layout="wide",
)
theme.inject()

st.title("PFD Mask Viewer")
help_expander("mask_viewer")
st.caption(
    "Interactive visualizer for ITU-R S.1503-4 PFD masks. "
    "Open from a filing on the **Uploads** page."
)


# ─── Query-param + storage lookup ───────────────────────────────────────────

qp = st.query_params
mask_id_q = qp.get("mask_id")
ntc_id_q = qp.get("ntc_id")
srs_path_q = qp.get("srs_path")     # path on disk (or upload_id)
mask_mdb_q = qp.get("mask_mdb")     # separate MASK MDB path
system_id_q = qp.get("system_id")   # resolve paths via DB

# ── Persisted last selection ───────────────────────────────────────────────
# Use the cross-page shared (current_system_id, current_mask_id) so picks
# made on Single-entry, Aggregate, Uploads etc. flow into this page too.
_shared_sid = current_system_id()
_shared_mid = current_mask_id()
_last_pick = (
    f"{_shared_sid}::{_shared_mid}"
    if _shared_sid and _shared_mid is not None else ""
)


def _picker_options() -> list[tuple[str, str, str | None, int]]:
    """Build the full list of PFD masks across every registered system.

    Returns ``[(label, system_id, ntc_id, mask_id), ...]`` ordered by filing
    then by mask_id.
    """
    items: list[tuple[str, str, str | None, int]] = []
    for s in storage.list_systems():
        try:
            bands = srs_inspect.frequency_bands(
                s["srs_path"], s.get("ntc_id"),
            )
        except Exception:  # noqa: BLE001
            continue
        for m in bands.get("masks") or []:
            if m.get("type") != "PFD":
                continue
            label = (
                f"{s['id']} · {s.get('upload_label') or '?'} · "
                f"ntc {s.get('ntc_id') or '—'} · "
                f"mask {m['mask_id']} "
                f"({m['freq_min_ghz']:.1f}–{m['freq_max_ghz']:.1f} GHz)"
            )
            items.append((label, s["id"], s.get("ntc_id"), int(m["mask_id"])))
    return items


# Picker fallback when no deep-link params: select a mask + persist choice.
if not (mask_id_q and (srs_path_q or mask_mdb_q or system_id_q)):
    options = _picker_options()
    if not options:
        st.info(
            "No PFD masks available yet. Use the **Upload** page to register "
            "a filing — once at least one PFD mask is detected it will "
            "appear here."
        )
        st.page_link("pages/1_Upload.py", label="Go to Upload",
                      icon=":material/upload:")
        st.stop()

    keys = [f"{sid}::{mid}" for (_, sid, _ntc, mid) in options]
    labels = {k: lab for (lab, sid, _n, mid), k in zip(options, keys)}

    # Seed the widget's session_state value once (first render) or when the
    # stored value points to a system that vanished from `keys`. After that,
    # session_state drives the widget — so DON'T also pass `index=` (that
    # double-source triggers Streamlit's "default value but value set via
    # Session State" warning, and would trap the selector on rerun).
    if st.session_state.get("mask_viewer_picker") not in keys:
        st.session_state["mask_viewer_picker"] = (
            _last_pick if _last_pick in keys else keys[0]
        )

    picked = st.selectbox(
        "Pick a PFD mask",
        options=keys,
        format_func=lambda k: labels[k],
        key="mask_viewer_picker",
    )
    # Find the matching tuple and rewrite the resolution variables so the
    # rest of the page renders normally.
    entry = next((opt for (opt, k) in zip(options, keys) if k == picked), None)
    if entry is None:
        st.stop()
    _lab, system_id_q, ntc_id_q, mid_from_pick = entry
    mask_id_q = str(mid_from_pick)
    if picked != _last_pick:
        set_current_system_id(system_id_q)
        set_current_mask_id(int(mid_from_pick))
    st.caption("Selection persists across reloads. Use the deep links on "
                "Uploads to bookmark specific masks.")
    st.divider()

# Resolve via system_id if given
if system_id_q and not srs_path_q:
    sysrow = storage.get_system(system_id_q)
    if sysrow:
        srs_path_q = sysrow.get("srs_path")
        if not mask_mdb_q:
            mask_mdb_q = sysrow.get("mask_path")
        if not ntc_id_q:
            ntc_id_q = sysrow.get("ntc_id")

# Persist the (system_id, mask_id) combo via cross-page selection so
# Single-entry, Aggregate, Mask Viewer all default to the same item.
if system_id_q and mask_id_q:
    try:
        mid_int = int(mask_id_q)
    except (TypeError, ValueError):
        mid_int = None
    if system_id_q != _shared_sid:
        set_current_system_id(system_id_q)
    if mid_int is not None and mid_int != _shared_mid:
        set_current_mask_id(mid_int)


# ─── Load mask + axes ───────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def _load_mask(srs_path: str | None, mask_mdb: str | None,
                ntc_id: str | None, mask_id: int) -> dict[str, Any] | None:
    """Read the requested mask from MDB(s) and return a JSON-ready dict
    (same shape the legacy HTML viewer consumed).

    Tries the mask MDB first (`pfd_mask_mdb` mode); falls back to the
    SRS MDB's embedded XML (`load_pfd_mask_from_xml_content`).
    """
    from src.srs_reader import read_pfd_mask_xml_from_mdb  # type: ignore
    from src.pfd_mask import load_pfd_mask_from_xml_content  # type: ignore

    candidates: list[str] = []
    if mask_mdb:
        candidates.append(mask_mdb)
    if srs_path:
        candidates.append(srs_path)

    for path in candidates:
        try:
            xml = read_pfd_mask_xml_from_mdb(path, int(mask_id), ntc_id=ntc_id)
            if not xml:
                continue
            pfd = load_pfd_mask_from_xml_content(xml, mask_id=int(mask_id))
            out = pfd.to_dict()
            out["low_freq_mhz"] = float(getattr(pfd, "low_freq_mhz", 0.0) or 0.0)
            out["high_freq_mhz"] = float(getattr(pfd, "high_freq_mhz", 0.0) or 0.0)
            out["refbw_khz"] = float(getattr(pfd, "refbw_khz", 40.0) or 40.0)
            return out
        except Exception:  # noqa: BLE001
            continue
    return None


try:
    mid = int(mask_id_q)
except ValueError:
    st.error(f"Invalid mask_id: `{mask_id_q}`")
    st.stop()

mask_dict = _load_mask(srs_path_q, mask_mdb_q, ntc_id_q, mid)
if not mask_dict:
    # Diagnose the real cause. The PFD curve lives in a `masks` table
    # (a zipped XML blob). `mask_info` only declares metadata (freq band,
    # type). A common case: only the SRS was uploaded, without its
    # companion MASK MDB — so the curve simply isn't on disk.
    from src.srs_reader import _run_mdb_export  # noqa: PLC0415

    cand = [p for p in (mask_mdb_q, srs_path_q) if p]
    has_masks = any(_run_mdb_export(p, "masks") for p in cand)
    def _as_int(v):
        try:
            return int(str(v).strip().strip('"'))
        except (TypeError, ValueError):
            return None

    declared = None
    if srs_path_q:
        for r in _run_mdb_export(srs_path_q, "mask_info"):
            r_ntc = r.get("ntc_id", "").strip().strip('"')
            if _as_int(r.get("mask_id")) == mid and (not ntc_id_q or r_ntc == str(ntc_id_q)):
                declared = r
                break

    if not has_masks:
        msg = (
            f"Mask `{mid}` (notice `{ntc_id_q or '-'}`) has **no curve data** "
            "in the uploaded file(s). The PFD shape is stored in a `masks` "
            "table (zipped XML); this SRS does not contain one"
        )
        if declared:
            msg += (
                f" — `mask_info` only declares it as type "
                f"`{declared.get('f_mask') or '?'}` over "
                f"{declared.get('freq_min') or '?'}–{declared.get('freq_max') or '?'} GHz "
                "(metadata, not the curve)"
            )
        msg += (
            ". Upload the companion **MASK MDB** for this filing and link it "
            "as the mask source on the **Uploads** page, then reopen the viewer."
        )
        st.error(msg)
    else:
        st.error(
            f"Could not load mask `{mid}` for notice `{ntc_id_q or '-'}` from "
            f"`{srs_path_q or mask_mdb_q}`. The `masks` table is present but "
            f"mask `{mid}` is absent or unreadable for this notice."
        )
    st.stop()

# Unpack
mask_type = mask_dict.get("type") or "?"
axes = mask_dict.get("axes") or {}
axis_a = np.asarray(axes.get("a") or axes.get("lat") or [], dtype=float)
axis_b = np.asarray(axes.get("b") or axes.get("alpha") or [], dtype=float)
axis_c = np.asarray(axes.get("c") or axes.get("dlon") or [], dtype=float)
shape = tuple(int(x) for x in (mask_dict.get("shape") or ()))
values = np.asarray(mask_dict.get("values") or [], dtype=float)
if shape:
    values = values.reshape(shape)
axis_names = mask_dict.get("axis_names") or {
    "a": "latitude", "b": "alpha", "c": "deltaLongitude",
}

# Mask out sentinel values (-999 etc.) for visualisation
vals_for_range = values[values > -900]
if vals_for_range.size:
    z_min = float(np.min(vals_for_range))
    z_max = float(np.max(vals_for_range))
else:
    z_min, z_max = -300.0, -130.0


# ─── Metadata panel ─────────────────────────────────────────────────────────

low_freq = float(mask_dict.get("low_freq_mhz") or 0.0)
high_freq = float(mask_dict.get("high_freq_mhz") or 0.0)
refbw_khz = float(mask_dict.get("refbw_khz") or 40.0)
freq_band = (
    f"{low_freq/1000:.2f} – {high_freq/1000:.2f} GHz"
    if (low_freq > 0 and high_freq > 0) else "—"
)

m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("Mask id", mid)
m2.metric("Type", mask_type)
m3.metric("Shape", " × ".join(str(s) for s in shape))
m4.metric("Frequency band", freq_band)
m5.metric("Reference BW", f"{refbw_khz:.0f} kHz")
m6.metric(
    f"PFD range (dBW/m²/{refbw_khz:.0f} kHz)",
    f"{z_min:.1f} … {z_max:.1f}",
)

st.caption(
    f"Axes: **a** = {axis_names.get('a', 'a')} "
    f"({axis_a[0]:.1f}…{axis_a[-1]:.1f}°, n={len(axis_a)}) · "
    f"**b** = {axis_names.get('b', 'b')} "
    f"({axis_b[0]:.1f}…{axis_b[-1]:.1f}°, n={len(axis_b)}) · "
    f"**c** = {axis_names.get('c', 'c')} "
    f"({axis_c[0]:.1f}…{axis_c[-1]:.1f}°, n={len(axis_c)})"
)


# ─── Sliders (lat index + b index) ──────────────────────────────────────────

def _fmt_deg(v: float) -> str:
    return f"{v:.1f}°" if not float(v).is_integer() else f"{int(v)}°"


c_lat, c_b = st.columns([3, 2])
with c_lat:
    lat_val = st.select_slider(
        f"{axis_names.get('a', 'a').title()} slice",
        options=list(axis_a),
        value=float(axis_a[len(axis_a) // 2]),
        format_func=_fmt_deg,
        help=f"Pick a latitude slice. Axis range: "
             f"{_fmt_deg(axis_a[0])} … {_fmt_deg(axis_a[-1])} "
             f"({len(axis_a)} points).",
    )
    lat_idx = int(np.argmin(np.abs(axis_a - lat_val)))
    st.caption(f"Selected: **{_fmt_deg(axis_a[lat_idx])}** "
                f"(index {lat_idx} / {len(axis_a) - 1})")

with c_b:
    b_val = st.select_slider(
        f"{axis_names.get('b', 'b').title()} slice (for the line plot)",
        options=list(axis_b),
        value=float(axis_b[len(axis_b) // 2]),
        format_func=_fmt_deg,
        help=f"Axis range: {_fmt_deg(axis_b[0])} … {_fmt_deg(axis_b[-1])} "
             f"({len(axis_b)} points).",
    )
    b_idx = int(np.argmin(np.abs(axis_b - b_val)))
    st.caption(f"Selected: **{_fmt_deg(axis_b[b_idx])}** "
                f"(index {b_idx} / {len(axis_b) - 1})")


# ─── Plots: heatmap + slice ─────────────────────────────────────────────────

ax_mode = st.radio(
    f"{axis_names.get('b', 'b').title()} axis spacing",
    options=["Uniform (equal cells)", "Proportional (degrees)"],
    index=1,
    horizontal=True,
    help="Proportional (default) uses real degree coordinates on the "
         "x-axis. Uniform gives every cell the same visual width — "
         "switch to it when the alpha axis is highly non-uniform "
         "(e.g. `[-80, -2, 2, 80]`) and the central cells collapse to "
         "a hairline in proportional mode.",
)

col_h, col_s = st.columns([3, 2])

with col_h:
    st.markdown(f"**Heatmap** — {axis_names.get('a', 'a').title()} "
                  f"= {axis_a[lat_idx]:.2f}°")
    z_slice = values[lat_idx].copy()
    z_slice = np.where(z_slice > -900, z_slice, np.nan)
    # transpose so x=b, y=c
    z_plot = z_slice.T

    use_prop = ax_mode.startswith("Proportional")
    if use_prop:
        x_vals = axis_b
        xaxis_cfg = dict(title=f"{axis_names.get('b', 'b').title()} (°)",
                          tickangle=-45)
    else:
        x_vals = np.arange(len(axis_b))
        n_ticks = min(12, len(axis_b))
        step = max(1, len(axis_b) // n_ticks)
        tickvals = list(range(0, len(axis_b), step))
        if tickvals[-1] != len(axis_b) - 1:
            tickvals.append(len(axis_b) - 1)
        ticktext = [f"{axis_b[i]:.1f}" for i in tickvals]
        xaxis_cfg = dict(
            title=f"{axis_names.get('b', 'b').title()} (°)",
            tickvals=tickvals, ticktext=ticktext, tickangle=-45,
        )

    fig_h = go.Figure(data=go.Heatmap(
        z=z_plot, x=x_vals, y=axis_c,
        zmin=z_min, zmax=z_max,
        colorscale="Viridis",
        colorbar=dict(title=f"PFD<br>(dBW/m²/{refbw_khz:.0f} kHz)", thickness=14),
        hoverongaps=False,
        hovertemplate=(
            f"{axis_names.get('b', 'b').title()}: %{{x}}<br>"
            f"{axis_names.get('c', 'c').title()}: %{{y:.2f}}°<br>"
            f"PFD: %{{z:.2f}} dBW/m²/{refbw_khz:.0f} kHz<extra></extra>"
        ),
    ))
    fig_h.update_layout(
        height=460,
        template="plotly_dark",
        margin=dict(l=60, r=10, t=10, b=70),
        xaxis=xaxis_cfg,
        yaxis=dict(title=f"{axis_names.get('c', 'c').title()} (°)"),
    )
    # Mark the selected B slice with a vertical line
    sel_x = b_idx if not use_prop else axis_b[b_idx]
    fig_h.add_vline(x=sel_x, line=dict(color="#fbbf24", width=1.5, dash="dot"))
    st.plotly_chart(fig_h, width='stretch')

with col_s:
    st.markdown(f"**Slice** — {axis_names.get('b', 'b').title()} "
                  f"= {axis_b[b_idx]:.2f}°")
    y_vals = values[lat_idx, b_idx].copy()
    y_vals = np.where(y_vals > -900, y_vals, np.nan)
    fig_s = go.Figure(data=go.Scatter(
        x=axis_c, y=y_vals, mode="lines+markers",
        marker=dict(size=5, color="#4fd1c5"),
        line=dict(color="#4fd1c5", width=2),
        name=f"{axis_names.get('b','b').title()}={axis_b[b_idx]:.2f}°",
        hovertemplate=(
            f"{axis_names.get('c','c').title()}: %{{x:.2f}}°<br>"
            f"PFD: %{{y:.2f}} dBW/m²/{refbw_khz:.0f} kHz<extra></extra>"
        ),
    ))
    fig_s.update_layout(
        height=460,
        template="plotly_dark",
        margin=dict(l=50, r=10, t=10, b=50),
        xaxis=dict(title=f"{axis_names.get('c', 'c').title()} (°)",
                     tickangle=-45),
        yaxis=dict(title=f"PFD (dBW/m²/{refbw_khz:.0f} kHz)", autorange=True),
        showlegend=False,
    )
    st.plotly_chart(fig_s, width='stretch')


# ─── Point calculator (interpolation) ───────────────────────────────────────

st.divider()
st.subheader("PFD calculator")
st.caption(
    "Compute the interpolated PFD value at a query point. Uses bilinear "
    "interpolation on (b, c) with nearest-lat for *azimuth_elevation* and "
    "*alpha_deltaLongitude* masks (matches the engine's runtime behaviour), "
    "trilinear otherwise."
)

calc_a, calc_b, calc_c, calc_run, calc_out = st.columns([1, 1, 1, 1, 2])
with calc_a:
    q_a = st.number_input(
        axis_names.get("a", "a"),
        value=float(axis_a[lat_idx]),
        step=1.0, format="%.2f",
    )
with calc_b:
    q_b = st.number_input(
        axis_names.get("b", "b"),
        value=float(axis_b[b_idx]),
        step=1.0, format="%.2f",
    )
with calc_c:
    q_c = st.number_input(
        axis_names.get("c", "c"),
        value=float(axis_c[len(axis_c) // 2]),
        step=1.0, format="%.2f",
    )


def _bracket(v: float, arr: np.ndarray) -> tuple[int, int, float]:
    """Clamped bracket on a monotonic axis. Returns (i0, i1, w)."""
    if len(arr) == 0:
        return 0, 0, 0.0
    vc = float(np.clip(v, arr[0], arr[-1]))
    if len(arr) == 1:
        return 0, 0, 0.0
    i = int(np.searchsorted(arr, vc, side="right")) - 1
    i = max(0, min(i, len(arr) - 2))
    i0, i1 = i, i + 1
    d = arr[i1] - arr[i0]
    w = 0.0 if abs(d) < 1e-12 else (vc - arr[i0]) / d
    return i0, i1, float(w)


def _nearest(v: float, arr: np.ndarray) -> int:
    return int(np.argmin(np.abs(arr - v)))


def _interp_pfd(qa: float, qb: float, qc: float) -> float | None:
    if values.size == 0:
        return None
    # Bilinear on (b, c), nearest on a for SRS-style masks
    if mask_type in ("azimuth_elevation", "alpha_deltaLongitude"):
        ia = _nearest(qa, axis_a)
        j0, j1, wj = _bracket(qb, axis_b)
        k0, k1, wk = _bracket(qc, axis_c)

        def g(j: int, k: int) -> float | None:
            v = values[ia, j, k]
            return None if v < -900 else float(v)

        cs = [g(j0, k0), g(j0, k1), g(j1, k0), g(j1, k1)]
        if all(c is None for c in cs):
            return None

        def lerp(a, b, t):
            if a is None and b is None:
                return None
            if a is None:
                return b
            if b is None:
                return a
            return a + (b - a) * t
        c0 = lerp(cs[0], cs[2], wj)
        c1 = lerp(cs[1], cs[3], wj)
        return lerp(c0, c1, wk)

    # Trilinear fallback
    i0, i1, wi = _bracket(qa, axis_a)
    j0, j1, wj = _bracket(qb, axis_b)
    k0, k1, wk = _bracket(qc, axis_c)
    cube = []
    for ii in (i0, i1):
        for jj in (j0, j1):
            for kk in (k0, k1):
                v = values[ii, jj, kk]
                cube.append(None if v < -900 else float(v))
    if all(c is None for c in cube):
        return None

    def lerp(a, b, t):
        if a is None and b is None:
            return None
        if a is None:
            return b
        if b is None:
            return a
        return a + (b - a) * t
    c00 = lerp(cube[0], cube[4], wi)
    c01 = lerp(cube[1], cube[5], wi)
    c10 = lerp(cube[2], cube[6], wi)
    c11 = lerp(cube[3], cube[7], wi)
    c0 = lerp(c00, c10, wj)
    c1 = lerp(c01, c11, wj)
    return lerp(c0, c1, wk)


with calc_run:
    st.markdown("")  # spacer
    run = st.button("Compute", icon=":material/calculate:", type="primary",
                      key="mask_calc_btn")
with calc_out:
    placeholder = st.empty()
    if run:
        pfd = _interp_pfd(float(q_a), float(q_b), float(q_c))
        if pfd is None:
            placeholder.error("Out of range / no data at that point.")
        else:
            placeholder.success(f"PFD = **{pfd:.2f} dBW/m²/{refbw_khz:.0f} kHz**")
    else:
        placeholder.caption("Fill the three axis values and press *Compute*.")


with st.expander("Raw mask dict (JSON)", expanded=False):
    st.json({
        "type": mask_type,
        "mask_id": mid,
        "ntc_id": ntc_id_q,
        "axis_names": axis_names,
        "shape": shape,
        "axes_summary": {
            "a": {"n": int(len(axis_a)), "min": float(axis_a[0]),
                  "max": float(axis_a[-1])},
            "b": {"n": int(len(axis_b)), "min": float(axis_b[0]),
                  "max": float(axis_b[-1])},
            "c": {"n": int(len(axis_c)), "min": float(axis_c[0]),
                  "max": float(axis_c[-1])},
        },
    })
