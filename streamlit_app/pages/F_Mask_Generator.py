"""Mask Generator — parametric PFD masks per S.1503-4 Part C (plan WS3).

Define the satellite beams (§C2.3.1) and operational constraints (§C1/§C2.2,
incl. GSO-arc avoidance), pick the mask format (§C2.1 Option 1
lat × α × ΔLong or Option 2 lat × az × el — both generated NATIVELY, no
conversion), preview it, and download the §C4.2 XML — directly usable as the
mask of a Manual System run.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

from lib import theme

st.set_page_config(
    page_title="Mask Generator · SHARC-Orbit",
    page_icon=":material/auto_fix_high:",
    layout="wide",
)
theme.inject()

st.title("PFD mask generator (S.1503-4 Part C)")
st.caption(
    "Max-envelope pfd mask from beam parameters — "
    "**pfdᵢ = Pᵢ + Gᵢ(θ) − 10log₁₀(4πd²)** per cell (§C2.3.1), summed over "
    "the N_co strongest beams (§C2.4), with GSO-arc avoidance switching "
    "beams off inside |α| < α₀ (§C2.2) and −1000 dBW outside the operating "
    "latitude band (§C1). Both §C2.1 formats are generated natively: "
    "**Option 1** (lat × α × ΔLong, §C2.4.1 iso-α maximum) or **Option 2** "
    "(lat × az × el, §C2.4.2)."
)

from src.mask_generator import (  # noqa: E402
    BeamSpec, MaskGenParams,
    generate_pfd_mask_azel, generate_pfd_mask_alpha_dlon, write_pfd_mask_xml,
)

# ── 1 · System / constraints ────────────────────────────────────────────────
st.subheader("1 · System & constraints")
c1, c2, c3, c4 = st.columns(4)
with c1:
    sat_name = st.text_input("Satellite name", "MANUAL-GEN", key="mg_name")
    alt_km = st.number_input("Altitude (km)", 200.0, 45_000.0, 1200.0, 50.0,
                             key="mg_alt")
with c2:
    fmin = st.number_input("Freq min (MHz)", 1000.0, 100_000.0, 17_700.0,
                           100.0, key="mg_fmin")
    fmax = st.number_input("Freq max (MHz)", 1000.0, 100_000.0, 18_600.0,
                           100.0, key="mg_fmax")
with c3:
    refbw = st.number_input("Ref BW (kHz)", 1.0, 10_000.0, 40.0, 1.0,
                            key="mg_refbw")
    n_co = st.number_input("N_co (0 = all beams)", 0, 64, 0, 1, key="mg_nco")
with c4:
    alpha0 = st.number_input("GSO-arc α₀ (°, 0 = no avoidance)", 0.0, 30.0,
                             0.0, 0.5, key="mg_a0")
    lat_band = st.slider("Operating latitude band (°)", -90.0, 90.0,
                         (-90.0, 90.0), 1.0, key="mg_band")

if alpha0 > 0:
    mz1, mz2 = st.columns([3, 1.4])
    with mz1:
        arc_mode = st.radio(
            "Exclusion-zone modelling (§C2.2)",
            options=["alpha_cutoff", "beam_off"],
            format_func=lambda v: (
                "Direct α cutoff — mask cells with |α| < α₀ set to the zone "
                "value (idealised suppression)" if v == "alpha_cutoff" else
                "Beam switch-off (cell-centre) — in-zone boresight beams OFF; "
                "the mask keeps the sidelobe leakage of the remaining beams"
            ),
            horizontal=False, key="mg_arc_mode",
        )
    with mz2:
        arc_fill = st.number_input(
            "Zone value (dBW/m²/refBW)", min_value=-1000.0, max_value=-50.0,
            value=-1000.0, step=1.0, key="mg_arc_fill",
            help="Value written inside |α| < α₀ in α-cutoff mode. −1000 = "
                 "§C1 null (renders as a gap); a finite level (e.g. −999 or a "
                 "residual-emission floor) fills the band with that value.",
            disabled=(arc_mode != "alpha_cutoff"),
        )
else:
    arc_mode = "beam_off"
    arc_fill = -1000.0

# ── 2 · Beams ───────────────────────────────────────────────────────────────
st.subheader("2 · Beams (§C2.3.1)")
if "mg_beams" not in st.session_state:
    st.session_state["mg_beams"] = pd.DataFrame([
        {"power_dbw": 10.0, "peak_gain_dbi": 30.0, "hpbw_deg": 5.0,
         "az_deg": 0.0, "el_deg": 0.0},
    ])
beams_df = st.data_editor(st.session_state["mg_beams"], num_rows="dynamic",
                          width="stretch", key="mg_beams_editor")

# ── 3 · Format, grid + generate ─────────────────────────────────────────────
st.subheader("3 · Mask format & grid")
mask_kind = st.radio(
    "Mask type (§C2.1)",
    options=["alpha_deltaLongitude", "azimuth_elevation"],
    horizontal=True, key="mg_kind",
    help="Option 1 (α/ΔLong, §C2.4.1) or Option 2 (az/el, §C2.4.2) — "
         "generated natively, no conversion between formats.",
)

g0, g1, g2, g3 = st.columns(4)
with g0:
    lat_step = st.number_input("Latitude step (°)", 1.0, 30.0, 10.0, 1.0,
                               key="mg_lat_step")
if mask_kind == "azimuth_elevation":
    with g1:
        bstep = st.number_input("Az/El step (°)", 0.5, 10.0, 2.0, 0.5,
                                key="mg_bstep")
    with g2:
        half = st.number_input("Az/El half-range (°)", 10.0, 90.0, 70.0, 5.0,
                               key="mg_half")
else:
    with g1:
        astep = st.number_input("α step (°)", 0.5, 10.0, 2.0, 0.5,
                                key="mg_astep")
        amax = st.number_input("α half-range (°)", 10.0, 90.0, 70.0, 5.0,
                               key="mg_amax")
    with g2:
        dstep = st.number_input("ΔLong step (°)", 0.5, 20.0, 2.0, 0.5,
                                key="mg_dstep")
        dmax = st.number_input("ΔLong half-range (°)", 10.0, 180.0, 90.0, 10.0,
                               key="mg_dmax")
    with g3:
        sample = st.number_input("Sampling step (°)", 0.1, 2.0, 0.5, 0.1,
                                 key="mg_sample",
                                 help="Density of the visible-cap sampling "
                                      "behind the §C2.4.1 iso-α maximum — "
                                      "finer = more accurate, slower.")

if st.button("Generate mask", type="primary", icon=":material/auto_fix_high:",
              key="mg_gen"):
    beams = [
        BeamSpec(power_dbw=float(r["power_dbw"]),
                 peak_gain_dbi=float(r["peak_gain_dbi"]),
                 hpbw_deg=float(r["hpbw_deg"]),
                 az_deg=float(r.get("az_deg", 0.0)),
                 el_deg=float(r.get("el_deg", 0.0)))
        for _, r in beams_df.iterrows()
    ]
    if not beams:
        st.error("Define at least one beam.")
        st.stop()
    params = MaskGenParams(
        altitude_km=float(alt_km), beams=beams,
        low_freq_mhz=float(fmin), high_freq_mhz=float(fmax),
        refbw_khz=float(refbw), n_co=int(n_co),
        lat_min_deg=float(lat_band[0]), lat_max_deg=float(lat_band[1]),
        gso_arc_alpha0_deg=float(alpha0) if alpha0 > 0 else None,
        gso_arc_mode=arc_mode,
        gso_arc_fill_dbw=float(arc_fill),
        sat_name=sat_name,
    )
    lat_grid = np.arange(-90.0, 90.0 + 1e-9, float(lat_step))
    with st.spinner("Generating (§C2.4 envelope)…"):
        if mask_kind == "azimuth_elevation":
            mask = generate_pfd_mask_azel(
                params, lat_grid_deg=lat_grid,
                az_grid_deg=np.arange(-float(half), float(half) + 1e-9,
                                      float(bstep)),
                el_grid_deg=np.arange(-float(half), float(half) + 1e-9,
                                      float(bstep)),
            )
        else:
            mask = generate_pfd_mask_alpha_dlon(
                params, lat_grid_deg=lat_grid,
                alpha_grid_deg=np.arange(-float(amax), float(amax) + 1e-9,
                                         float(astep)),
                dlon_grid_deg=np.arange(-float(dmax), float(dmax) + 1e-9,
                                        float(dstep)),
                sample_step_deg=float(sample),
            )
        st.session_state["mg_mask"] = mask
        st.session_state["mg_mask_kind"] = mask_kind
        st.session_state["mg_xml"] = write_pfd_mask_xml(mask, params,
                                                        mask_type=mask_kind)

# ── 4 · Preview + export ────────────────────────────────────────────────────
mask = st.session_state.get("mg_mask")
kind = st.session_state.get("mg_mask_kind")
if mask is not None and kind:
    import plotly.graph_objects as go
    lat = np.asarray(mask["lat"])
    pfd_all = np.asarray(mask["pfd"], dtype=float)
    st.subheader("4 · Preview & export")

    # Axis convention (user-defined): az/el → HORIZONTAL = azimuth,
    # VERTICAL = elevation; α/ΔLong → HORIZONTAL = alpha, VERTICAL = ΔLong.
    if kind == "azimuth_elevation":
        x_vals, x_name = mask["az"], "azimuth (°)"
        y_vals, y_name = mask["el"], "elevation (°)"
        # pfd is (lat, az, el) → transpose to (el, az) = (y, x).
        def _slice(i):
            return pfd_all[i].T
    else:
        x_vals, x_name = mask["alpha"], "alpha (°)"
        y_vals, y_name = mask["dlon"], "deltaLongitude (°)"
        # pfd is (lat, alpha, dlon) → transpose to (dlon, alpha) = (y, x).
        def _slice(i):
            return pfd_all[i].T

    # Slices can be entirely OFF (§C2.2). Default to a populated slice and
    # explain null ones instead of a blank plot.
    pop = np.array([(pfd_all[i] > -999.5).any() for i in range(lat.size)])
    default_i = (int(np.argmin(np.abs(lat) + np.where(pop, 0.0, 1e6)))
                 if pop.any() else len(lat) // 2)
    lat_pick = st.select_slider(
        "Latitude slice",
        options=[float(v) for v in lat], value=float(lat[default_i]),
        key="mg_lat_pick",
        format_func=lambda v: f"{v:g}°"
        + ("" if pop[int(np.argmin(np.abs(lat - v)))] else " (off)"),
    )
    i = int(np.argmin(np.abs(lat - lat_pick)))
    z = np.where(_slice(i) > -999.5, _slice(i), np.nan)
    if not np.isfinite(z).any():
        st.info(
            f"All beams are OFF at satellite latitude {lat[i]:g}° — every "
            "boresight cell falls inside the GSO-arc exclusion zone "
            "(|α| < α₀, §C2.2), so this mask slice is −1000 dBW everywhere. "
            "Pick another latitude slice, lower α₀, or add beams pointed "
            "away from the GSO arc."
        )
    else:
        finite = pfd_all[pfd_all > -999.5]
        fig = go.Figure(go.Heatmap(
            z=z, x=x_vals, y=y_vals, colorscale="Viridis",
            zmin=float(finite.min()), zmax=float(finite.max()),
            colorbar=dict(title="PFD (dBW/m²/refBW)", thickness=14),
        ))
        fig.update_layout(height=480, template="plotly_dark",
                          xaxis_title=x_name, yaxis_title=y_name,
                          margin=dict(l=60, r=10, t=20, b=50))
        st.plotly_chart(fig, width="stretch")
    n_off = int((~pop).sum())
    if n_off:
        off_lats = ", ".join(f"{v:g}°" for v in lat[~pop])
        st.caption(f"{n_off} latitude slice(s) fully OFF by §C2.2: {off_lats}")

    st.download_button(
        f"Download XML — {kind} (§C4.2)",
        icon=":material/download:", type="primary",
        data=st.session_state["mg_xml"].encode("utf-8"),
        file_name=f"pfd_mask_{'azel' if kind == 'azimuth_elevation' else 'alpha_dlon'}.xml",
        mime="application/xml", key="mg_dl",
    )
    st.caption(
        "Use the XML directly on the **Manual system** page (mask upload) or "
        "register it via Upload. Round-trip with the engine reader is "
        "test-covered."
    )
