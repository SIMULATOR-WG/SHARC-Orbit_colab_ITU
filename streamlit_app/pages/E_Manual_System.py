"""Manual System — parametric constellation entry + wizard (R3/R4, plan WS2).

Define an NGSO system without an SRS filing: pick a known constellation
pattern (Walker Delta/Star, equatorial ring, train, Molniya, Tundra, IGSO —
optionally stacked as multi-shell), edit the generated plane list, attach a
standalone PFD-mask XML (SRS/BR schema, S.1503-4 §C4.2), preview the
constellation on the 3D globe, and launch an EPFD↓ run. The engine consumes
the manual definition through ``load_from_manual`` (same defaults skeleton as
SRS filings; run frequency = mask fmin + RefBW/2 per §D2 when not given).
"""
from __future__ import annotations

import time
from typing import Any

import numpy as np
import pandas as pd
import streamlit as st

from lib import UPLOADS_DIR, launcher, plots, theme
from lib.state import set_current_run_id

st.set_page_config(
    page_title="Manual System · SHARC-Orbit",
    page_icon=":material/edit_note:",
    layout="wide",
)
theme.inject()

st.title("Manual system (parametric entry)")
st.caption(
    "Define an NGSO system **without an SRS filing** — S.1503-4 Part B "
    "parameters (B3.1/B3.2), per-plane elements per §D6.3.7, standalone PFD "
    "mask XML (§C4.2). Use a template to pre-fill, edit plane-by-plane, "
    "preview, then launch."
)

# ─── 1 · Constellation wizard ────────────────────────────────────────────────

from src import constellation_templates as ct  # noqa: E402

st.subheader("1 · Constellation")

TEMPLATES = {
    "Walker Delta (i:T/P/F)": "walker_delta",
    "Walker Star (polar, RAAN 0–180°)": "walker_star",
    "Equatorial ring (O3b-like)": "equatorial",
    "Single plane / train": "train",
    "Molniya (HEO 12 h)": "molniya",
    "Tundra (HEO 24 h)": "tundra",
    "IGSO (figure-eight)": "igso",
}

cw1, cw2 = st.columns([2, 3])
with cw1:
    tpl_name = st.selectbox("Template", options=list(TEMPLATES.keys()),
                            key="man_tpl")
    tpl = TEMPLATES[tpl_name]
with cw2:
    st.caption(
        "Templates PRE-FILL the plane table below (RAAN **O[N]**, ω **W[N]**, "
        "per-satellite phases **V[N]** — §B3.2); everything stays editable. "
        "Generate more than once with *Append* to compose a **multi-shell** "
        "system (Starlink/Kuiper-style)."
    )

p1, p2, p3, p4, p5 = st.columns(5)
with p1:
    alt_km = st.number_input("Altitude (km)", 200.0, 45_000.0, 1200.0, 50.0,
                             key="man_alt")
with p2:
    inc_deg = st.number_input("Inclination (°)", 0.0, 180.0, 87.9, 0.1,
                              key="man_inc")
with p3:
    n_planes = st.number_input("Planes", 1, 72, 18, 1, key="man_np")
with p4:
    spp = st.number_input("Sats/plane", 1, 200, 40, 1, key="man_spp")
with p5:
    phasing = st.number_input("Phasing F", 0, 50, 1, 1, key="man_f")

with st.expander("Helpers: sun-synchronous / repeat ground track"):
    h1, h2, h3 = st.columns(3)
    with h1:
        if st.button("Set sun-synchronous inclination for this altitude",
                      key="man_ss"):
            try:
                st.session_state["man_inc"] = round(
                    ct.sun_synchronous_inclination_deg(float(alt_km)), 3)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    with h2:
        rgt_revs = st.number_input("Repeat: revs", 1, 20, 14, 1, key="man_revs")
        rgt_days = st.number_input("per days", 1, 30, 1, 1, key="man_days")
    with h3:
        if st.button("Set altitude for repeating ground track", key="man_rgt"):
            a = ct.repeat_ground_track_a_km(int(rgt_revs), int(rgt_days))
            from src.constants import RE_KM  # noqa: PLC0415
            st.session_state["man_alt"] = round(a - RE_KM, 1)
            st.rerun()

def _generate() -> list[dict]:
    total = int(n_planes) * int(spp)
    if tpl == "walker_delta":
        return ct.walker_delta(total_sats=total, num_planes=int(n_planes),
                               phasing_factor=int(phasing),
                               inclination_deg=float(inc_deg),
                               altitude_km=float(alt_km))
    if tpl == "walker_star":
        return ct.walker_star(total_sats=total, num_planes=int(n_planes),
                              phasing_factor=int(phasing),
                              inclination_deg=float(inc_deg),
                              altitude_km=float(alt_km))
    if tpl == "equatorial":
        return ct.equatorial_ring(n_sats=int(spp), altitude_km=float(alt_km))
    if tpl == "train":
        return ct.train(n_sats=int(spp), altitude_km=float(alt_km),
                        inclination_deg=float(inc_deg))
    if tpl == "molniya":
        return ct.molniya(n_planes=int(n_planes), sats_per_plane=int(spp))
    if tpl == "tundra":
        return ct.tundra(n_planes=int(n_planes))
    return ct.igso(n_sats=int(spp), inclination_deg=float(inc_deg))


g1, g2, g3 = st.columns([1.2, 1.2, 4])
with g1:
    if st.button("Generate (replace)", type="primary", icon=":material/auto_awesome:",
                  key="man_gen"):
        st.session_state["man_planes"] = _generate()
with g2:
    if st.button("Append (multi-shell)", icon=":material/library_add:",
                  key="man_append"):
        cur = list(st.session_state.get("man_planes") or [])
        st.session_state["man_planes"] = ct.multi_shell(cur, _generate()) \
            if cur else _generate()

planes: list[dict] = list(st.session_state.get("man_planes") or [])
if planes:
    df = pd.DataFrame(planes)
    if "phase_angles_deg" in df.columns:
        df["phase_angles_deg"] = df["phase_angles_deg"].apply(
            lambda v: ", ".join(f"{x:g}" for x in v) if isinstance(v, list) else v)
    edited = st.data_editor(df, num_rows="dynamic", width="stretch",
                            key="man_editor")
    # Back to plane dicts (phases parsed from the comma list).
    planes = []
    for _, row in edited.iterrows():
        d = {k: v for k, v in row.items() if pd.notna(v)}
        ph = d.get("phase_angles_deg")
        if isinstance(ph, str):
            try:
                d["phase_angles_deg"] = [float(x) for x in ph.split(",") if x.strip()]
            except ValueError:
                d.pop("phase_angles_deg", None)
        for k in ("orb_id", "sats_per_plane"):
            if k in d:
                d[k] = int(d[k])
        planes.append(d)
    n_sats_total = sum(int(p.get("sats_per_plane", 0)) for p in planes)
    st.caption(f"{len(planes)} plane(s) · {n_sats_total} satellites")

    # ── 3D preview ──
    with st.expander("3D preview", expanded=False):
        try:
            from src.main import create_constellation_from_config  # noqa: PLC0415
            from src.orbit_propagator import propagate_and_to_ecef_batch  # noqa: PLC0415
            from src.constants import RE_KM  # noqa: PLC0415
            cfg_prev = {
                "planes": [dict(p) for p in planes],
                "semi_major_axis_km": None, "eccentricity": None,
                "apogee_km": float(alt_km), "perigee_km": float(alt_km),
                "inclination_deg": float(inc_deg),
                "num_planes": len(planes), "sats_per_plane": int(spp),
            }
            cons = create_constellation_from_config(cfg_prev)
            pos, _ = propagate_and_to_ecef_batch(cons, 0.0)
            pos = np.asarray(pos) / RE_KM
            fig = plots.earth_3d_chart(
                satellites_xyz=[tuple(p) for p in pos.tolist()],
                title=f"Preview — {len(cons)} satellites",
                height=560,
            )
            st.plotly_chart(fig, width="stretch")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Preview failed: {exc}")
else:
    st.info("Generate a template (or Append shells) to build the plane table.")

# ─── 2 · Operating parameters + PFD mask ────────────────────────────────────

st.subheader("2 · Operating parameters & PFD mask")
o1, o2, o3, o4 = st.columns(4)
with o1:
    label = st.text_input("System label", value="Manual system", key="man_label")
with o2:
    alpha0 = st.number_input("α₀ exclusion angle (°)", 0.0, 30.0, 6.0, 0.5,
                             key="man_alpha0")
with o3:
    min_elev = st.number_input("ES min elevation ε₀ (°)", 0.0, 60.0, 5.0, 0.5,
                               key="man_eps0")
with o4:
    freq_ghz = st.number_input(
        "Frequency (GHz, 0 = derive from mask)", 0.0, 100.0, 0.0, 0.1,
        key="man_freq",
        help="0 → run frequency = mask fmin + RefBW/2 (S.1503-4 §D2).",
    )

mask_up = st.file_uploader(
    "PFD mask XML (SRS/BR schema — <satellite_system><pfd_mask …>)",
    type=["xml"], key="man_mask",
)
mask_id_in = st.number_input("mask_id (when the XML holds several)", 0, 9999, 0,
                             1, key="man_mask_id",
                             help="0 = first/only mask in the file.")

# ─── 3 · Launch ──────────────────────────────────────────────────────────────

st.subheader("3 · Run")
r1, r2, r3 = st.columns(3)
with r1:
    n_steps = st.number_input("Time steps", 100, 100_000_000, 86_400, 100,
                              key="man_steps")
with r2:
    dt_s = st.number_input("Coarse Δt (s)", 0.1, 600.0, 1.0, 0.1, key="man_dt")
with r3:
    diam_m = st.number_input("GSO ES antenna Ø (m)", 0.3, 18.0, 1.2, 0.1,
                             key="man_diam")

# ── Register as filing (hand-off to the Upload flow) ────────────────────────
# Writes the manual pair into the uploads area and drops the user on Upload
# Step 2 with both files pending, so the save/registration is confirmed
# through the standard flow. Preferred pair: REAL Access .mdb (SRS + Mask)
# written via the cross-platform Jackcess helper (Linux/Windows, needs a
# JRE); fallback: SRS-equivalent YAML + the normative §C4.1 mask XML.
from src import mdb_writer as _mdbw  # noqa: E402

_mdb_ok, _mdb_why = _mdbw.availability()
_reg_label = ("Register as filing — real MDB pair (Upload flow)" if _mdb_ok
              else "Register as filing — YAML+XML pair (Upload flow)")
if not _mdb_ok:
    st.caption(f"MDB writer unavailable ({_mdb_why}) — the pair will be "
                "registered as YAML + mask XML instead.")
if st.button(_reg_label, icon=":material/upload:",
              disabled=not planes or mask_up is None, key="man_register"):
    import uuid
    import yaml as _yaml

    upload_id = uuid.uuid4().hex[:12]
    dst = UPLOADS_DIR / upload_id
    dst.mkdir(parents=True, exist_ok=True)
    slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)[:40]
    manual_doc: dict[str, Any] = {
        "label": label,
        "non_gso": {
            "planes": planes,
            "alpha0_deg": float(alpha0),
            "min_elevation_deg": float(min_elev),
            **({"frequency_ghz": float(freq_ghz)} if freq_ghz > 0 else {}),
        },
        "gso_es": {"antenna_diameter_m": float(diam_m)},
    }

    if _mdb_ok:
        # Mask frequency range for mask_info (read from the XML itself).
        _fmin = _fmax = None
        _mid = int(mask_id_in) if mask_id_in > 0 else 1
        try:
            from src.pfd_mask import load_pfd_mask_from_xml_content  # noqa: PLC0415
            _pm = load_pfd_mask_from_xml_content(
                mask_up.getvalue(),
                mask_id=int(mask_id_in) if mask_id_in > 0 else None)
            _mid = int(getattr(_pm, "mask_id", _mid) or _mid)
            if getattr(_pm, "low_freq_mhz", 0.0):
                _fmin = float(_pm.low_freq_mhz) / 1000.0
                _fmax = float(_pm.high_freq_mhz) / 1000.0
        except Exception:  # noqa: BLE001
            pass
        _ntc = f"9{time.strftime('%y%m%d%H%M')[:8]}"   # synthetic notice id
        with st.spinner("Writing Access MDB pair (Jackcess)…"):
            srs_dst, mask_dst = _mdbw.write_manual_pair(
                manual_doc, dst, base_name=slug or "Manual",
                mask_xml=mask_up.getvalue(), ntc_id=_ntc, mask_id=_mid,
                mask_freq_min_ghz=_fmin, mask_freq_max_ghz=_fmax,
            )
    else:
        mask_dst = dst / (mask_up.name or f"{slug}_Mask.xml")
        mask_dst.write_bytes(mask_up.getvalue())
        manual_doc["pfd_mask"] = {"source": "xml_file", "file": str(mask_dst)}
        srs_dst = dst / f"{slug}_SRS.yaml"
        srs_dst.write_text(_yaml.safe_dump(manual_doc, sort_keys=False,
                                           allow_unicode=True),
                           encoding="utf-8")

    st.session_state["upload_pending"] = {
        "upload_id": upload_id,
        "label": label,
        "srs_path": str(srs_dst),
        "mask_path": str(mask_dst),
        "uploaded_name": srs_dst.name,
        "network_name": label,
        "preview": {"network_name": label, "manual": True},
        "ts": time.strftime("%Y-%m-%dT%H-%M-%S"),
    }
    st.session_state["upload_stage"] = "notices"
    st.switch_page("pages/1_Upload.py")

if st.button("Launch EPFD↓ run", type="primary", icon=":material/rocket_launch:",
              disabled=not planes or mask_up is None, key="man_launch"):
    # Persist the uploaded mask under UPLOADS_DIR so workers can read it.
    mdir = UPLOADS_DIR / "manual" / time.strftime("%Y%m%d-%H%M%S")
    mdir.mkdir(parents=True, exist_ok=True)
    mask_path = mdir / (mask_up.name or "mask.xml")
    mask_path.write_bytes(mask_up.getvalue())

    manual_cfg: dict[str, Any] = {
        "label": label,
        "non_gso": {
            "planes": planes,
            "alpha0_deg": float(alpha0),
            "min_elevation_deg": float(min_elev),
            **({"frequency_ghz": float(freq_ghz)} if freq_ghz > 0 else {}),
        },
        "pfd_mask": {
            "source": "xml_file",
            "file": str(mask_path),
            **({"mask_id": int(mask_id_in)} if mask_id_in > 0 else {}),
        },
        "gso_es": {"antenna_diameter_m": float(diam_m)},
    }
    params = {
        "num_time_steps": int(n_steps),
        "time_step_s": float(dt_s),
        "es_antenna_diameter_m": float(diam_m),
    }
    run_id = launcher.launch_s1503_manual(manual_cfg=manual_cfg, params=params)
    set_current_run_id(run_id)
    st.toast(f"Run `{run_id}` launched", icon=":material/play_circle:")
    st.switch_page("pages/7_Status.py", query_params={"run_id": run_id})

st.caption(
    "The manual definition (planes O/W/V per §B3.2, apsides per §D6.3.7, mask "
    "per §C4.2) is persisted in the run's params.json — input_source=manual "
    "(R26). Same engine path as filings from there on."
)
