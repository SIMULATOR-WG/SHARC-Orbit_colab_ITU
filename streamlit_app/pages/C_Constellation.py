"""Constellation Viewer — 3D globe of the non-GSO constellation, filtered by
Article 22 scenario.

A *scenario* here is an Article 22 downlink run **deduplicated over the ES
antenna diameter** (the diameter changes only the ES gain/limit, never which
satellites transmit). So the distinct views are service × band × ref-BW × table.
For each scenario the page resolves which satellites EMIT in that band
(``grp`` ⋈ ``mask_lnk1`` at the scenario's run frequency) and renders the
constellation as a 3D globe (snapshot at t=0) with a toggle:

* **Só emissores** — only the satellites active in the band.
* **Todos (emissores destacados)** — full constellation, emitters highlighted.

A **Completa (todas as frequências)** scenario shows every satellite.

Operates directly on a registered filing (SRS MDB) — no simulation run needed.
"""
from __future__ import annotations

import json
import math
from typing import Any

import numpy as np
import streamlit as st
import streamlit.components.v1 as components
import plotly.graph_objects as go

from lib import storage, theme, srs_inspect, plots
from lib.state import current_system_id, set_current_system_id

st.set_page_config(
    page_title="Constellation Viewer · SHARC-Orbit",
    page_icon=":material/public:",
    layout="wide",
)
theme.inject()

st.title("Constellation Viewer")
st.caption(
    "3D NGSO constellation by **Article 22 scenario** (service × band × BW × "
    "table — antenna diameter is collapsed, as it does not change which "
    "satellites transmit) or **complete** (all frequencies). Highlights the "
    "satellites emitting in the scenario band. Operates directly on the filing "
    "— no run needed. Click a satellite to isolate it and project its PFD mask "
    "as a heat-map footprint."
)

EMIT_COLOR = "#fde047"   # bright yellow — emitters
DIM_COLOR = "#64748b"    # slate — non-emitters


# ─── System picker ───────────────────────────────────────────────────────────

_systems = [s for s in storage.list_systems() if s.get("srs_path")]
if not _systems:
    st.info(
        "No system registered with an SRS. Use the **Upload** page to "
        "register a filing."
    )
    st.page_link("pages/1_Upload.py", label="Go to Upload", icon=":material/upload:")
    st.stop()

_keys = [s["id"] for s in _systems]
_labels = {
    s["id"]: (
        f"{s['id']} · {s.get('upload_label') or '?'} · "
        f"ntc {s.get('ntc_id') or '—'} · {s.get('sat_name') or ''}"
    )
    for s in _systems
}
_shared = current_system_id()
if st.session_state.get("constellation_picker") not in _keys:
    st.session_state["constellation_picker"] = _shared if _shared in _keys else _keys[0]

system_id = st.selectbox(
    "Filing / system",
    options=_keys,
    format_func=lambda k: _labels.get(k, k),
    key="constellation_picker",
)
if system_id != _shared:
    set_current_system_id(system_id)

# Switching filing clears any isolated-satellite selection — satellite indices,
# mask ids and the pinned point are all filing-specific.
if st.session_state.get("_constel_prev_system") != system_id:
    st.session_state["_constel_prev_system"] = system_id
    for _k in ("constel_sel_idx", "constel_step", "constel_mask_id", "constel_pt",
               "constel_pick_sat", "_constel_pending_pick",
               "_constel_last_click_sel", "_constel_last_click_scn"):
        st.session_state.pop(_k, None)

_sysrow = storage.get_system(system_id) or {}
srs_path = _sysrow.get("srs_path")
ntc_id = _sysrow.get("ntc_id")
if not srs_path:
    st.error(f"System `{system_id}` has no SRS path.")
    st.stop()


# ─── Constellation build (cached per filing) ─────────────────────────────────

@st.cache_resource(show_spinner="Building constellation…")
def _constellation_resource(srs_path: str, ntc_id: str | None) -> dict[str, Any]:
    """Read SRS → orbital elements and keep the propagatable ``cons`` object.

    Single source of truth for both the *scenario* view (positions at t=0) and
    the *selection* view (propagation at arbitrary ``t``). Returns the
    constellation object, t=0 positions (Re-scaled), per-sat (orb_id,
    sat_orb_id) tags, per-sat plane index, ``a_km``/``gmst0`` for propagation,
    and an orbital summary. ``{"error": ...}`` on failure.

    Cached as a *resource* (not data) because ``cons`` is a list of live
    ``OrbitalElements`` — propagated on demand by :func:`_footprint`.
    """
    from src.srs_reader import read_srs_mdb, srs_to_constellation_config
    from src.main import create_constellation_from_config
    from src.orbit_propagator import propagate_and_to_ecef_batch
    from src.coordinates import set_earth_rotation_initial_deg
    from src.constants import RE_KM

    try:
        system = read_srs_mdb(srs_path, ntc_id=ntc_id)
        cfg = srs_to_constellation_config(system)
    except Exception as exc:  # noqa: BLE001
        msg = f"Could not read/convert the SRS: {exc}"
        if "non_geo" in str(exc):
            msg += (
                " — this file has no non-GSO constellation (it is likely a "
                "MASK/results MDB registered as the SRS, or a GSO-only filing). "
                "Pick a valid NGSO filing above."
            )
        return {"error": msg}

    planes = cfg.get("planes", [])
    gmst0 = float(cfg.get("gmst0_deg", 0.0) or 0.0)
    set_earth_rotation_initial_deg(gmst0)
    ngso = {**cfg, "_planes": planes}
    cons = create_constellation_from_config(ngso)
    if not cons:
        return {"error": "Empty constellation (no planes/satellites in the SRS)."}

    pos, _vel = propagate_and_to_ecef_batch(cons, 0.0)
    pos = np.asarray(pos, dtype=float) / float(RE_KM)

    # Parallel tags, replicating create_constellation_from_config's iteration
    # order (planes in order; s = 0..n-1; sats with n<1 skipped).
    sats_pp_global = int(cfg.get("sats_per_plane", 0) or 0)
    tags: list[tuple[int, int]] = []
    plane_idx: list[int] = []
    pi = 0
    for pl in planes:
        n = int(pl.get("sats_per_plane", sats_pp_global) or 0)
        if n < 1:
            continue
        oid = int(pl.get("orb_id", 0) or 0)
        for s in range(n):
            tags.append((oid, s + 1))
            plane_idx.append(pi)
        pi += 1

    if len(tags) != pos.shape[0]:
        # Index alignment broke — disable per-sat filtering rather than mislabel.
        tags = []
        plane_idx = []

    a_km = float(cfg.get("semi_major_axis_km", 0.0))
    summary = {
        "a_km": a_km,
        "alt_km": a_km - float(RE_KM),
        "i_deg": float(cfg.get("inclination_deg", 0.0)),
        "ecc": float(cfg.get("eccentricity", 0.0)),
        "num_planes": int(cfg.get("num_planes", 0)),
        "sats_per_plane": sats_pp_global,
        "total": int(pos.shape[0]),
        "sat_name": getattr(system, "sat_name", ""),
    }
    return {
        "cons": cons,
        "pos": pos,
        "tags": tags,
        "plane_idx": plane_idx,
        "a_km": a_km,
        "gmst0": gmst0,
        "summary": summary,
    }


def _build_constellation(srs_path: str, ntc_id: str | None) -> dict[str, Any] | None:
    """Scenario-view accessor: t=0 positions + tags/plane/summary (JSON-ish).

    Thin wrapper over :func:`_constellation_resource` so both views share the
    exact same per-satellite index order (critical for click→satellite mapping).
    """
    res = _constellation_resource(srs_path, ntc_id)
    if "error" in res:
        return {"error": res["error"]}
    return {
        "pos": np.asarray(res["pos"], dtype=float).tolist(),
        "tags": res["tags"],
        "plane_idx": res["plane_idx"],
        "summary": res["summary"],
    }


# ─── Article 22 scenarios (cached per filing) ────────────────────────────────

@st.cache_data(show_spinner=False)
def _scenarios(srs_path: str, ntc_id: str | None) -> dict[str, Any]:
    """Article 22 downlink scenarios for the filing's PFD masks, deduplicated
    over the ES antenna diameter."""
    from src.article22_tables import list_article22_downlink_runs_for_masks

    try:
        bands = srs_inspect.frequency_bands(srs_path, ntc_id)
    except Exception:  # noqa: BLE001
        bands = {"masks": []}
    pfd = [m for m in (bands.get("masks") or []) if m.get("type") == "PFD"]
    masks = [
        {
            "freq_min_ghz": float(m["freq_min_ghz"]),
            "freq_max_ghz": float(m["freq_max_ghz"]),
            "mask_id": m.get("mask_id"),
        }
        for m in pfd
        if m.get("freq_min_ghz") is not None and m.get("freq_max_ghz") is not None
    ]
    try:
        runs = list_article22_downlink_runs_for_masks(masks) if masks else []
    except Exception:  # noqa: BLE001
        runs = []

    seen: set[tuple] = set()
    out: list[dict[str, Any]] = []
    for r in runs:
        mref = r.get("mask_ref") or {}
        key = (
            r["service"], r["rr_reference"],
            round(float(r["band_start_ghz"]), 4), round(float(r["band_end_ghz"]), 4),
            round(float(r["run_min_ghz"]), 4), round(float(r["run_max_ghz"]), 4),
            round(float(r["bw_khz"]), 3),
            tuple(r.get("regions") or []),
            mref.get("mask_id"),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return {"scenarios": out, "n_pfd_masks": len(pfd)}


# ─── Emitter selection (cached per filing + frequency) ───────────────────────

@st.cache_data(show_spinner=False)
def _emitter_flags(
    srs_path: str, ntc_id: str | None, freq_ghz: float, tags: tuple[tuple[int, int], ...]
) -> dict[str, Any]:
    """Per-satellite emitting flags at ``freq_ghz``. Falls back to all-active
    when the filing has no grp/mask_lnk1 band data or tags are unavailable."""
    from src.srs_reader import read_emitters_in_band

    if not tags:
        return {"flags": None, "note": "sem tags (filtro por satélite indisponível)"}
    try:
        sel = read_emitters_in_band(srs_path, ntc_id=ntc_id, freq_ghz=float(freq_ghz))
    except Exception as exc:  # noqa: BLE001
        return {"flags": None, "note": f"falha grp/mask_lnk1 ({exc}); mostrando todos"}
    if not sel.has_data:
        return {"flags": None, "note": "filing sem grp/mask_lnk1; mostrando todos"}
    if sel.wildcard_all:
        return {"flags": [True] * len(tags), "note": "grp wildcard (orb_id=-1): todos emitem"}
    flags = [bool(sel.is_active(o, s)) for (o, s) in tags]
    return {"flags": flags, "note": ""}


# ─── Per-satellite mask footprint (selection view) ───────────────────────────

FOOTPRINT_GRID_DEG = 0.5   # ground grid resolution for the heat-map (default)


@st.cache_data(show_spinner=False)
def _masks_for_sat(
    srs_path: str, mask_path: str | None, ntc_id: str | None,
    orb_id: int, sat_orb_id: int | None,
) -> dict[str, Any]:
    """PFD ``mask_id`` list linked to a satellite via ``mask_lnk1`` (§D5.1.5).

    Uses :func:`read_mask_assignment_per_sat` + :func:`resolve_masks_for_sat`
    (explicit ``(orb,sat)`` → orbit ``(orb,None)`` → wildcard ``(-1,None)``).
    Falls back to *all* PFD masks in the filing when the linkage is absent.
    """
    from src.srs_reader import read_mask_assignment_per_sat, resolve_masks_for_sat

    assign: dict = {}
    for path in [p for p in (srs_path, mask_path) if p]:
        try:
            a = read_mask_assignment_per_sat(path, ntc_id=ntc_id)
        except Exception:  # noqa: BLE001
            continue
        if a:
            assign = a
            break
    linked = [int(m) for m in resolve_masks_for_sat(
        assign, int(orb_id), int(sat_orb_id) if sat_orb_id else None,
    )]
    # Fallback: every PFD mask declared in the filing.
    try:
        bands = srs_inspect.frequency_bands(srs_path, ntc_id)
        all_pfd = sorted({
            int(m["mask_id"]) for m in (bands.get("masks") or [])
            if m.get("type") == "PFD" and m.get("mask_id") is not None
        })
    except Exception:  # noqa: BLE001
        all_pfd = []

    # Keep only masks that actually have curve data on disk: a filing may
    # *declare* a mask_id (mask_info / mask_lnk1) whose `masks` XML blob is
    # absent, which would fail at query time. Loadability is cached.
    def _ok(mid: int) -> bool:
        return _load_mask_obj(mask_path, srs_path, ntc_id, int(mid)) is not None

    linked = [m for m in linked if _ok(m)]
    all_pfd = [m for m in all_pfd if _ok(m)]
    return {"linked": linked, "all_pfd": all_pfd}


@st.cache_resource(show_spinner=False)
def _load_mask_obj(mask_path: str | None, srs_path: str | None,
                   ntc_id: str | None, mask_id: int):
    """Load the ``PFDMask`` object (not a dict) for footprint queries.

    Mirrors ``B_Mask_Viewer._load_mask`` resolution (MASK MDB first, then the
    SRS MDB's embedded XML) but returns the live object so we can call
    ``get_pfd_batch``. ``None`` when no source holds the curve.
    """
    from src.srs_reader import read_pfd_mask_xml_from_mdb
    from src.pfd_mask import load_pfd_mask_from_xml_content

    for path in [p for p in (mask_path, srs_path) if p]:
        try:
            xml = read_pfd_mask_xml_from_mdb(path, int(mask_id), ntc_id=ntc_id)
        except Exception:  # noqa: BLE001
            continue
        if xml:
            try:
                return load_pfd_mask_from_xml_content(xml, mask_id=int(mask_id))
            except Exception:  # noqa: BLE001
                continue
    return None


@st.cache_data(show_spinner="Computing footprint…")
def _footprint(
    srs_path: str, mask_path: str | None, ntc_id: str | None, mask_id: int,
    sel_idx: int, step: int, grid_deg: float, min_elev_deg: float,
) -> dict[str, Any]:
    """PFD mask projected onto the globe for one satellite at time ``step·Δt``.

    For every visible ground point (satellite above ``min_elev_deg``) the PFD
    is queried with the **same S.1503-4 axes the EPFD↓ engine uses**:
    latitude = sub-satellite latitude; for α/Δλ masks α is the topocentric
    NGSO↔optimal-GSO angle and Δλ = Long(optGSO) − Long(subsat); for az/el
    masks the (azimuth, elevation) are taken in the satellite's own local
    frame. Returns the ``(n_lat, n_lon)`` PFD grid (``NaN`` where invisible).
    """
    from src.orbit_propagator import propagate_and_to_ecef_batch
    from src.coordinates import set_earth_rotation_initial_deg, ecef_to_lla
    from src.constants import RE_KM
    from src.time_step import compute_orbital_period
    from src.geometry import (
        compute_alpha_and_optimal_gso_multi_es_batch, delta_longitude_s1503_deg,
    )

    res = _constellation_resource(srs_path, ntc_id)
    if "error" in res:
        return {"error": res["error"]}
    cons = res["cons"]
    if not (0 <= sel_idx < len(cons)):
        return {"error": "Índice de satélite fora do intervalo."}

    mask = _load_mask_obj(mask_path, srs_path, ntc_id, int(mask_id))
    if mask is None:
        return {"error": f"Máscara {mask_id} sem curva disponível no filing."}
    if getattr(mask, "_dim", 1) != 3:
        return {"error": "Máscara 1D (CSV) não suportada no footprint 3D."}

    set_earth_rotation_initial_deg(float(res["gmst0"]))
    T = compute_orbital_period(float(res["a_km"]))
    dt = (T / 120.0) if T > 0 else 60.0
    t_s = float(step) * dt
    pos_all, _vel = propagate_and_to_ecef_batch(cons, t_s)
    sat_ecef = np.asarray(pos_all[sel_idx], dtype=np.float64)
    subsat_lat, subsat_lon, sat_alt = ecef_to_lla(sat_ecef)

    lat_deg = np.arange(-90.0, 90.0 + 1e-6, grid_deg)
    lon_deg = np.arange(-180.0, 180.0 + 1e-6, grid_deg)
    LAT, LON = np.meshgrid(lat_deg, lon_deg, indexing="ij")
    latf = LAT.ravel()
    lonf = LON.ravel()
    latr = np.radians(latf)
    lonr = np.radians(lonf)
    cl = np.cos(latr)
    # Spherical Earth (Re), matching src.coordinates.lla_to_ecef (S.1503 D6.1).
    es = np.column_stack((
        RE_KM * cl * np.cos(lonr),
        RE_KM * cl * np.sin(lonr),
        RE_KM * np.sin(latr),
    ))

    diff = sat_ecef[np.newaxis, :] - es
    rng = np.linalg.norm(diff, axis=1)
    up = es / RE_KM   # radial unit vector (|es| = Re on the sphere)
    sin_el = np.einsum("ij,ij->i", up, diff) / np.where(rng > 1e-9, rng, 1.0)
    vis = sin_el >= math.sin(math.radians(float(min_elev_deg)))

    pfd_flat = np.full(latf.shape, np.nan, dtype=np.float64)
    if np.any(vis):
        es_v = es[vis]
        lat_v = latf[vis]
        lon_v = lonf[vis]
        lat_q = np.full(lat_v.shape, float(subsat_lat), dtype=np.float64)
        if getattr(mask, "mask_type", "") == "azimuth_elevation":
            from src.wcg_search import (
                _build_sat_local_frames_ecef_batch,
                _compute_mask_az_el_from_frame_batch,
            )
            z, y, x = _build_sat_local_frames_ecef_batch(sat_ecef[np.newaxis, :])
            frame = (*z[0], *y[0], *x[0])
            az, el = _compute_mask_az_el_from_frame_batch(es_v, sat_ecef, frame)
            good = np.isfinite(az) & np.isfinite(el)
            pv = np.full(lat_v.shape, np.nan, dtype=np.float64)
            if np.any(good):
                pv[good] = mask.get_pfd_batch(
                    az[good], lat_q[good], el[good],
                )
        else:
            alpha, gso = compute_alpha_and_optimal_gso_multi_es_batch(
                es_v, sat_ecef, lat_v, lon_v,
            )
            gso_lon = np.degrees(np.arctan2(gso[:, 1], gso[:, 0]))
            dlon = delta_longitude_s1503_deg(
                gso_lon, np.full(gso_lon.shape, float(subsat_lon)),
            )
            pv = mask.get_pfd_batch(alpha, lat_q, dlon)
        # Sentinel very-low values (-999/-1000) are "no data" → transparent.
        pv = np.where(pv > -900.0, pv, np.nan)
        pfd_flat[vis] = pv

    pfd_grid = pfd_flat.reshape(LAT.shape)
    return {
        "pfd_grid": pfd_grid,
        "lat_deg": lat_deg,
        "lon_deg": lon_deg,
        "sat_xyz": (sat_ecef / RE_KM).tolist(),
        "subsat_lat": float(subsat_lat),
        "subsat_lon": float(subsat_lon),
        "sat_alt_km": float(sat_alt),
        "t_s": t_s,
        "dt": dt,
        "T": T,
        "n_vis": int(vis.sum()),
        "refbw_khz": float(getattr(mask, "refbw_khz", 40.0) or 40.0),
        "mask_type": getattr(mask, "mask_type", ""),
    }


# ─── Load + render ───────────────────────────────────────────────────────────

_data = _build_constellation(srs_path, ntc_id)
if _data is None or "error" in _data:
    st.error(_data.get("error", "Failed to build the constellation.") if _data else "Failed.")
    st.info("Pick a different filing above — this one is not a readable NGSO SRS.")
    st.stop()

pos = np.asarray(_data["pos"], dtype=float)
tags = [tuple(t) for t in _data["tags"]]
summary = _data["summary"]
N = summary["total"]
mask_path = _sysrow.get("mask_path")

_plane_list = _data.get("plane_idx") or []
have_planes = len(_plane_list) == N and N > 0
plane_arr = np.asarray(_plane_list, dtype=int) if have_planes else None
orb_arr = np.asarray([t[0] for t in tags], dtype=int) if (tags and len(tags) == N) else None
have_tags = len(tags) == N and N > 0


def _sat_label(i: int) -> str:
    if not (0 <= i < N):
        return f"#{i}"
    o, s = tags[i] if i < len(tags) else (0, 0)
    pl = f" · plane {int(plane_arr[i])}" if have_planes else ""
    return f"#{i} · orb {o} · sat {s}{pl}"


def _lla_to_unit_xyz(lat_deg: float, lon_deg: float, r: float = 1.0) -> tuple:
    la, lo = math.radians(lat_deg), math.radians(lon_deg)
    return (r * math.cos(la) * math.cos(lo),
            r * math.cos(la) * math.sin(lo),
            r * math.sin(la))


def _xyz_to_lla(x: float, y: float, z: float) -> tuple[float, float]:
    """Unit-sphere ECEF-like xyz → (lat°, lon°)."""
    r = math.sqrt(x * x + y * y + z * z) or 1.0
    return (math.degrees(math.asin(max(-1.0, min(1.0, z / r)))),
            math.degrees(math.atan2(y, x)))


def _grid_pfd(lat: float, lon: float, lat_deg, lon_deg, pfd_grid) -> float | None:
    """Nearest-cell PFD at (lat, lon) from a footprint grid; None if no data."""
    lat_arr = np.asarray(lat_deg, dtype=float)
    lon_arr = np.asarray(lon_deg, dtype=float)
    i = int(np.argmin(np.abs(lat_arr - lat)))
    j = int(np.argmin(np.abs(((lon_arr - lon + 180.0) % 360.0) - 180.0)))
    v = np.asarray(pfd_grid, dtype=float)[i, j]
    return float(v) if np.isfinite(v) else None


def _globe_component_html(
    fig: go.Figure,
    *,
    lat_deg,
    lon_deg,
    pfd_grid,
    refbw_khz: float,
    heat_curve: int | None,
    height: int,
) -> str:
    """Self-contained HTML for the footprint globe with a hover read-out box.

    Streamlit's ``st.plotly_chart`` cannot report Plotly hover events, so the
    selection-view globe is embedded as raw Plotly (CDN) inside an iframe with:
      * a fixed corner ``<div>`` updated on ``plotly_hover`` (lat/lon + PFD from
        a JS-side copy of the footprint grid — mirrors :func:`_grid_pfd`), so the
        read-out never sits under the pointer;
      * camera persistence via ``localStorage`` (restored on load) so zoom/orbit
        survive the step/clear reruns that reload the iframe.
    """
    fig_html = fig.to_html(
        include_plotlyjs="cdn", full_html=False, div_id="cglobe",
        config={"displaylogo": False, "scrollZoom": True, "responsive": True},
    )
    grid = np.asarray(pfd_grid, dtype=float)
    grid_j = json.dumps(
        [[None if not np.isfinite(v) else round(float(v), 2) for v in row]
         for row in grid]
    )
    lat_j = json.dumps([round(float(v), 4) for v in np.asarray(lat_deg, float)])
    lon_j = json.dumps([round(float(v), 4) for v in np.asarray(lon_deg, float)])
    heat_j = "null" if heat_curve is None else str(int(heat_curve))
    return f"""
<div style="position:relative;width:100%;">
  {fig_html}
  <div id="creadout" style="position:absolute;top:64px;left:14px;z-index:1000;
       background:rgba(6,9,18,0.90);border:1px solid #22d3ee;border-radius:8px;
       padding:8px 11px;color:#e6eaf2;font:12px/1.45 system-ui,sans-serif;
       min-width:158px;pointer-events:none;box-shadow:0 2px 10px rgba(0,0,0,.45);">
    <div style="color:#22d3ee;font-weight:600;margin-bottom:3px;">Footprint point</div>
    <div id="crd">hover the mask…</div>
  </div>
</div>
<script>
(function(){{
  const latDeg={lat_j}, lonDeg={lon_j}, grid={grid_j};
  const refbw={refbw_khz:.0f}, heatCurve={heat_j}, KEY='constelGlobeCam';
  function nearestLat(v){{let bi=0,bd=Infinity;for(let i=0;i<latDeg.length;i++){{
    let d=Math.abs(latDeg[i]-v);if(d<bd){{bd=d;bi=i;}}}}return bi;}}
  function nearestLon(v){{let bi=0,bd=Infinity;for(let i=0;i<lonDeg.length;i++){{
    let d=Math.abs(((lonDeg[i]-v+540)%360)-180);if(d<bd){{bd=d;bi=i;}}}}return bi;}}
  function attach(){{
    const gd=document.getElementById('cglobe');
    if(!gd||!gd.on){{setTimeout(attach,120);return;}}
    try{{const c=localStorage.getItem(KEY);if(c)Plotly.relayout(gd,{{'scene.camera':JSON.parse(c)}});}}catch(e){{}}
    gd.on('plotly_relayout',function(ev){{
      // Save the RESOLVED current camera on any relayout (zoom, rotate,
      // double-click / modebar reset) — so a reset takes precedence over the
      // previously persisted view instead of being overwritten on next reload.
      try{{
        const sc=(gd.layout&&gd.layout.scene&&gd.layout.scene.camera)
                 ||(gd._fullLayout&&gd._fullLayout.scene&&gd._fullLayout.scene.camera);
        if(sc) localStorage.setItem(KEY,JSON.stringify(sc));
      }}catch(e){{}}
    }});
    gd.on('plotly_hover',function(ev){{
      const p=ev.points&&ev.points[0];if(!p)return;
      if(heatCurve!==null&&p.curveNumber!==heatCurve)return;
      const x=p.x,y=p.y,z=p.z;if(x==null||y==null||z==null)return;
      const r=Math.sqrt(x*x+y*y+z*z)||1;
      const lat=Math.asin(Math.max(-1,Math.min(1,z/r)))*180/Math.PI;
      const lon=Math.atan2(y,x)*180/Math.PI;
      const gi=nearestLat(lat),gj=nearestLon(lon);
      const pfd=(grid[gi]&&grid[gi][gj]!=null)?grid[gi][gj]:null;
      document.getElementById('crd').innerHTML=
        'lat '+lat.toFixed(2)+'\\u00b0<br>lon '+lon.toFixed(2)+'\\u00b0<br>PFD '+
        (pfd==null?'\\u2014':(pfd.toFixed(1)+' dBW/m\\u00b2/'+refbw+' kHz'));
    }});
  }}
  attach();
}})();
</script>
"""


def _globe_scenario_html(
    fig: go.Figure,
    *,
    sat_pos,
    apply_key: str,
    query_key: str,
    height: int,
) -> str:
    """Scenario globe as raw Plotly (iframe) with a click→select bridge.

    Streamlit does not deliver click/selection events for 3D (gl3d) charts
    (verified empirically), so selection is done client-side: clicking anywhere
    on the globe fires ``plotly_click`` on the base surface, we pick the NEAREST
    satellite (by 3D distance to the clicked xyz among ``sat_pos``), write its
    index into a top-window query param and programmatically click a hidden
    parent Streamlit button — that reruns Python, which reads the query param.
    Camera is persisted in ``localStorage`` (shared with the footprint globe).
    """
    fig_html = fig.to_html(
        include_plotlyjs="cdn", full_html=False, div_id="sglobe",
        config={"displaylogo": False, "scrollZoom": True, "responsive": True},
    )
    # sat_pos rows: [x, y, z, global_index]
    sat_j = json.dumps([[round(float(a), 5), round(float(b), 5),
                         round(float(c), 5), int(i)] for a, b, c, i in sat_pos])
    return f"""
<div style="position:relative;width:100%;">
  {fig_html}
</div>
<script>
(function(){{
  const SAT={sat_j}, KEY='constelGlobeCam', QK='{query_key}', AK='{apply_key}';
  // gl3d fires plotly_hover reliably but NOT plotly_click, so remember the last
  // hovered point and commit the selection on the DOM click.
  let lastPt=null;
  function commit(){{
    if(!lastPt||!SAT.length)return;
    const x=lastPt[0],y=lastPt[1],z=lastPt[2];
    let best=-1,bd=Infinity;
    for(const s of SAT){{const dx=s[0]-x,dy=s[1]-y,dz=s[2]-z;const d=dx*dx+dy*dy+dz*dz;
      if(d<bd){{bd=d;best=s[3];}}}}
    if(best<0)return;
    try{{
      const u=new URL(window.top.location); u.searchParams.set(QK,String(best));
      window.top.history.pushState({{}},'',u);
      const btn=window.top.document.querySelector('.st-key-'+AK+' button');
      if(btn) btn.click();
    }}catch(e){{}}
  }}
  function attach(){{
    const gd=document.getElementById('sglobe');
    if(!gd||!gd.on){{setTimeout(attach,120);return;}}
    try{{const c=localStorage.getItem(KEY);if(c)Plotly.relayout(gd,{{'scene.camera':JSON.parse(c)}});}}catch(e){{}}
    gd.on('plotly_relayout',function(ev){{
      // Save the RESOLVED current camera on any relayout (zoom, rotate,
      // double-click / modebar reset) — so a reset takes precedence over the
      // previously persisted view instead of being overwritten on next reload.
      try{{
        const sc=(gd.layout&&gd.layout.scene&&gd.layout.scene.camera)
                 ||(gd._fullLayout&&gd._fullLayout.scene&&gd._fullLayout.scene.camera);
        if(sc) localStorage.setItem(KEY,JSON.stringify(sc));
      }}catch(e){{}}
    }});
    gd.on('plotly_hover',function(ev){{
      const p=ev.points&&ev.points[0];
      if(p&&p.x!=null&&p.y!=null&&p.z!=null) lastPt=[p.x,p.y,p.z];
    }});
    gd.addEventListener('click',commit);
  }}
  attach();
}})();
</script>
"""


def _clicked_idx(event: Any, registry: dict[int, np.ndarray], key: str) -> int | None:
    """Map a NEW plotly point-click to a global satellite index.

    ``registry`` maps ``curve_number`` → array of global sat indices in point
    order. The selection event persists across reruns, so only a click that
    differs from the last processed one for this chart ``key`` is acted on
    (same anti-stale guard as pages/8_Results.py)."""
    pts = ((event or {}).get("selection") or {}).get("points") or []
    last_key = f"_constel_last_click_{key}"
    if not pts or pts == st.session_state.get(last_key):
        return None
    st.session_state[last_key] = pts
    p = pts[0]
    cn = p.get("curve_number")
    pn = p.get("point_number")
    if pn is None:
        pn = p.get("point_index")
    arr = registry.get(cn)
    if arr is not None and pn is not None and 0 <= int(pn) < len(arr):
        return int(arr[int(pn)])
    return None


# Selection state (a satellite is "isolated" → footprint mode).
sel_idx = st.session_state.get("constel_sel_idx")
if sel_idx is not None and not (0 <= int(sel_idx) < N):
    sel_idx = None
    st.session_state["constel_sel_idx"] = None


# ═══════════════════════════════════════════════════════════════════════════
#  SELECTION / FOOTPRINT MODE — one satellite isolated, mask projected as heat
# ═══════════════════════════════════════════════════════════════════════════
if sel_idx is not None:
    sel_idx = int(sel_idx)
    o_sel, s_sel = tags[sel_idx] if sel_idx < len(tags) else (0, None)

    step = int(st.session_state.get("constel_step", 0))

    # ── Controls: mask picker, min elevation, min PFD, grid ──
    c_mask, c_elev, c_pfd, c_grid = st.columns([2.2, 1.5, 1.5, 1.5])

    # Masks associated with THIS satellite (mask_lnk1); fallback = all PFD masks.
    ma = _masks_for_sat(srs_path, mask_path, ntc_id, int(o_sel), s_sel)
    linked = ma["linked"]
    mask_opts = linked or ma["all_pfd"]
    with c_mask:
        if not mask_opts:
            st.selectbox("Mask", options=["—"], disabled=True,
                          key="constel_mask_none")
            mask_id = None
        else:
            stored = st.session_state.get("constel_mask_id")
            idx0 = mask_opts.index(stored) if stored in mask_opts else 0
            mask_id = st.selectbox(
                "Associated mask",
                options=mask_opts, index=idx0,
                format_func=lambda m: f"mask {m}"
                + ("" if m in linked else " (unlinked)"),
                key="constel_mask_pick",
                help="PFD masks linked to this satellite via mask_lnk1. "
                     "No link → all PFD masks in the filing.",
            )
            st.session_state["constel_mask_id"] = mask_id
    with c_elev:
        min_elev = st.slider(
            "Min. elevation (°)", min_value=0.0, max_value=40.0, value=0.0, step=1.0,
            help="Visibility = satellite above this angle on each point's local "
                 "horizon (Step 11, §D6.4.3).",
            key="constel_min_elev",
        )
    with c_pfd:
        min_pfd = st.slider(
            "Min. PFD (dBW/m²/refBW)",
            min_value=-1000.0, max_value=-100.0, value=-1000.0, step=1.0,
            help="Ground points whose projected PFD is below this threshold "
                 "are not drawn (−1000 = show everything).",
            key="constel_min_pfd",
        )
    with c_grid:
        grid_deg = st.slider(
            "Grid (°)", min_value=0.1, max_value=5.0,
            value=FOOTPRINT_GRID_DEG, step=0.1,
            help="Interpolation granularity of the footprint. Finer (smaller°) "
                 "= smoother heat-map but slower to compute/render. Floor 0.1°.",
            key="constel_grid_deg",
        )

    if not linked and mask_opts:
        st.caption("This satellite has no mask linked in mask_lnk1 — showing "
                    "all PFD masks in the filing.")

    # ── Compute footprint ──
    fp = None
    if mask_id is not None:
        fp = _footprint(
            srs_path, mask_path, ntc_id, int(mask_id),
            sel_idx, step, float(grid_deg), float(min_elev),
        )

    # ── Metrics for the isolated satellite ──
    mm1, mm2, mm3, mm4, mm5, mm6 = st.columns(6)
    mm1.metric("Satellite", _sat_label(sel_idx))
    if fp and "error" not in fp:
        mm2.metric("Step (t)", f"{step} · {fp['t_s']:.0f} s")
        frac = (fp["t_s"] / fp["T"] * 100.0) if fp.get("T") else 0.0
        mm3.metric("Orbit fraction", f"{frac:.1f}%")
        mm4.metric("Sub-satellite (lat, lon)",
                    f"{fp['subsat_lat']:.2f}°, {fp['subsat_lon']:.2f}°")
        mm5.metric("Altitude", f"{fp['sat_alt_km']:.0f} km")
        mm6.metric("Visible points", f"{fp['n_vis']}")
    else:
        mm2.metric("Step (t)", f"{step}")
        mm4.metric("Altitude", f"{summary['alt_km']:.0f} km")

    # ── Globe with an in-container hover read-out box ──
    # Rendered as raw Plotly inside an iframe (st.components) because
    # st.plotly_chart cannot report hover events: a fixed corner box updates on
    # plotly_hover with the ground point's lat/lon + PFD, and the camera is
    # persisted in localStorage so zoom/orbit survive the step/clear reruns.
    sat_name = summary["sat_name"] or system_id
    title = f"{sat_name} — {_sat_label(sel_idx)}" + (
        f" · mask {mask_id}" if mask_id is not None else "")
    fig = plots.earth_3d_chart(
        satellites_xyz=None, title=title, uirevision="constel-globe",
    )
    # Lat/lon graticule with the equator emphasized.
    fig.add_traces(plots.graticule_3d())
    # Equator (lat=0) traced distinctly — the equatorial GSO-arc plane, the
    # reference for α/Δlong and the footprint's north/south split.
    fig.add_trace(plots.equator_3d())

    GLOBE_H = 760
    heat_cn: int | None = None
    js_lat = [0.0]
    js_lon = [0.0]
    js_grid = [[None]]
    if fp and "error" not in fp:
        pfd_grid = np.asarray(fp["pfd_grid"], dtype=float)
        # Min-PFD filter (applied post-hoc — no footprint recompute): cells
        # below the threshold become transparent (and un-hoverable).
        pfd_disp = np.where(pfd_grid >= float(min_pfd), pfd_grid, np.nan)
        # Colour scale spans the DISPLAYED range so it rescales to the lowest
        # PFD still shown as the threshold moves.
        shown = pfd_disp[np.isfinite(pfd_disp)]
        zmin = float(shown.min()) if shown.size else None
        zmax = float(shown.max()) if shown.size else None
        heat_cn = len(fig.data)
        fig.add_trace(plots.earth_3d_heatmap_surface(
            pfd_disp, fp["lat_deg"], fp["lon_deg"],
            refbw_khz=fp["refbw_khz"], zmin=zmin, zmax=zmax,
        ))
        js_lat, js_lon, js_grid = fp["lat_deg"], fp["lon_deg"], pfd_disp
        if not np.any(np.isfinite(pfd_grid)):
            st.info("No point on the globe currently has visibility to the "
                     "satellite (or the mask does not cover this geometry).")
        elif not np.any(np.isfinite(pfd_disp)):
            st.info(f"No ground point reaches the Min. PFD threshold "
                     f"({min_pfd:.0f} dBW/m²/refBW) at this step.")
    elif fp and "error" in fp:
        st.warning(fp["error"])

    sat_xyz = (fp["sat_xyz"] if (fp and "error" not in fp)
               else pos[sel_idx].tolist())
    sat_hover = (
        f"{_sat_label(sel_idx)}<br>lat {fp['subsat_lat']:.2f}° · "
        f"lon {fp['subsat_lon']:.2f}°<br>alt {fp['sat_alt_km']:.0f} km"
        if (fp and "error" not in fp) else _sat_label(sel_idx)
    )
    fig.add_trace(go.Scatter3d(
        x=[sat_xyz[0]], y=[sat_xyz[1]], z=[sat_xyz[2]],
        mode="markers",
        marker=dict(size=7, color="#22d3ee", symbol="diamond",
                      line=dict(color="#ffffff", width=1)),
        name=f"satellite {_sat_label(sel_idx)}",
        hovertext=[sat_hover], hoverinfo="text",
    ))

    if fp and "error" not in fp:
        sx, sy, sz = _lla_to_unit_xyz(fp["subsat_lat"], fp["subsat_lon"], 1.001)
        fig.add_trace(go.Scatter3d(
            x=[sx], y=[sy], z=[sz], mode="markers",
            marker=dict(size=4, color="#ffffff", symbol="x"),
            name="sub-satellite point",
            hovertext=[f"sub-satellite point<br>lat {fp['subsat_lat']:.2f}° · "
                       f"lon {fp['subsat_lon']:.2f}°"],
            hoverinfo="text",
        ))

    fig.update_layout(legend=dict(orientation="h", y=-0.02))

    refbw = float(fp["refbw_khz"]) if (fp and "error" not in fp) else 40.0
    col_btn, col_globe = st.columns([0.55, 9], gap="small",
                                     vertical_alignment="top")
    with col_btn:
        # Icon-only step / clear cluster, dropped down to sit just below the
        # in-globe "Footprint point" box (which lives inside the iframe, so the
        # buttons cannot go literally inside it). CSS pins all three to the same
        # fixed narrow width and right-aligns them so they hug the globe's edge.
        st.markdown("<div style='height:150px'></div>", unsafe_allow_html=True)
        st.markdown(
            "<style>.st-key-constel_btncol button{min-width:0;width:46px;"
            "margin-left:auto;margin-right:0;display:block;"
            "padding-left:0;padding-right:0;}</style>",
            unsafe_allow_html=True,
        )
        with st.container(key="constel_btncol"):
            if st.button("◀", key="constel_step_prev", help="Step backward"):
                st.session_state["constel_step"] = step - 1
                st.rerun()
            if st.button("▶", key="constel_step_next", help="Step forward"):
                st.session_state["constel_step"] = step + 1
                st.rerun()
            if st.button("✕", type="primary", key="constel_clear",
                          help="Clear selection"):
                st.session_state["constel_sel_idx"] = None
                st.session_state["constel_step"] = 0
                st.rerun()
    with col_globe:
        components.html(
            _globe_component_html(
                fig, lat_deg=js_lat, lon_deg=js_lon, pfd_grid=js_grid,
                refbw_khz=refbw, heat_curve=heat_cn, height=GLOBE_H,
            ),
            height=GLOBE_H + 12, scrolling=False,
        )

    st.caption(
        "Heat-map = PFD (dBW/m²/refBW) that a GSO earth station at each visible "
        "point would receive from this satellite (worst-case α/Δλ, ITU-R "
        f"S.1503-4). {grid_deg:g}° grid, Earth radius = 1. Hover a "
        "footprint point to read its lat/lon and PFD in the top-left box (kept "
        "off the pointer). Left buttons: ◀ ▶ step in orbit · ✕ clear selection."
    )

    # ── Projected mask data ──
    if mask_id is not None:
        _m = _load_mask_obj(mask_path, srs_path, ntc_id, int(mask_id))
        if _m is not None:
            md = _m.to_dict()
            axes = md.get("axes") or {}
            an = md.get("axis_names") or {}
            a = np.asarray(axes.get("a") or [], dtype=float)
            b = np.asarray(axes.get("b") or [], dtype=float)
            c = np.asarray(axes.get("c") or [], dtype=float)
            vals = np.asarray(md.get("values") or [], dtype=float)
            vv = vals[vals > -900.0]
            refbw = float(getattr(_m, "refbw_khz", 40.0) or 40.0)
            lo = float(getattr(_m, "low_freq_mhz", 0.0) or 0.0)
            hi = float(getattr(_m, "high_freq_mhz", 0.0) or 0.0)
            band = f"{lo/1000:.3f}–{hi/1000:.3f} GHz" if (lo > 0 and hi > 0) else "—"
            shape_s = " × ".join(str(s) for s in (md.get("shape") or ()))

            with st.expander(f"Projected mask data — mask {mask_id}", expanded=True):
                d1, d2, d3, d4 = st.columns(4)
                d1.metric("Mask id", int(mask_id))
                d2.metric("Type", _m.mask_type)
                d3.metric("Reference BW", f"{refbw:.0f} kHz")
                d4.metric("Frequency band", band)

                def _ax(arr: np.ndarray, name: str) -> str:
                    if arr.size == 0:
                        return f"**{name}** —"
                    return f"**{name}** {arr.min():.1f}…{arr.max():.1f}° (n={arr.size})"

                st.caption(
                    f"Axes — {_ax(a, an.get('a', 'a'))} · {_ax(b, an.get('b', 'b'))} "
                    f"· {_ax(c, an.get('c', 'c'))} · shape {shape_s or '—'}"
                )
                if vv.size:
                    st.caption(
                        f"Mask PFD range: {vv.min():.1f} … {vv.max():.1f} "
                        f"dBW/m²/{refbw:.0f} kHz"
                    )
                if fp and "error" not in fp:
                    pg = np.asarray(fp["pfd_grid"], dtype=float)
                    pgf = pg[np.isfinite(pg)]
                    rng = (f"{pgf.min():.1f} … {pgf.max():.1f}" if pgf.size else "—")
                    st.caption(
                        f"Projected footprint (this step): PFD {rng} "
                        f"dBW/m²/{refbw:.0f} kHz · {fp['n_vis']} visible point(s) · "
                        f"sub-sat ({fp['subsat_lat']:.2f}°, {fp['subsat_lon']:.2f}°) · "
                        f"alt {fp['sat_alt_km']:.0f} km · t={fp['t_s']:.0f} s"
                    )
                try:
                    st.page_link(
                        "pages/B_Mask_Viewer.py",
                        label="Open in Mask Viewer for the full 2D view",
                        icon=":material/blur_on:",
                    )
                except Exception:  # noqa: BLE001 — page registry may be absent
                    pass

    st.stop()


# ═══════════════════════════════════════════════════════════════════════════
#  SCENARIO MODE — full constellation, Article 22 filter (click to isolate)
# ═══════════════════════════════════════════════════════════════════════════
_scn = _scenarios(srs_path, ntc_id)
scenarios = _scn["scenarios"]


def _scn_label(r: dict[str, Any]) -> str:
    reg = ",".join(str(x) for x in (r.get("regions") or []))
    mref = r.get("mask_ref") or {}
    mid = mref.get("mask_id")
    return (
        f"{r['service']} · {r['band_start_ghz']:.3f}–{r['band_end_ghz']:.3f} GHz · "
        f"run {r['frequency_run_ghz']:.4f} GHz · {r['bw_khz']:.0f} kHz · "
        f"{r['rr_reference']} · R{reg}"
        + (f" · mask {mid}" if mid is not None else "")
    )


COMPLETE = "__complete__"
options = [COMPLETE] + list(range(len(scenarios)))
labels = {COMPLETE: f"Complete — all frequencies ({N} satellites)"}
for i, r in enumerate(scenarios):
    labels[i] = _scn_label(r)

col_sel, col_filt, col_color = st.columns([3, 1.4, 1.6])
with col_sel:
    choice = st.selectbox(
        "Scenario (Article 22, antenna diameter collapsed)",
        options=options,
        format_func=lambda k: labels[k],
        help="Each scenario = service × band × BW × table. Antenna diameter is "
             "not a factor (it does not change which satellites transmit). "
             "'Complete' shows all.",
    )
with col_filt:
    filt = st.radio(
        "Filter",
        options=["Emitters only", "All"],
        help="Show only satellites that emit in the scenario band, or all.",
    )
with col_color:
    color_by = st.radio(
        "Color",
        options=["Emitter", "Orbital plane"],
        horizontal=True,
        help="Emitter: yellow=emits in band, grey=not. Orbital plane: one color "
             "per plane (Turbo).",
    )

# Resolve emitter flags for the chosen scenario.
note = ""
if choice == COMPLETE:
    emit = np.ones(N, dtype=bool)
else:
    r = scenarios[choice]
    res = _emitter_flags(srs_path, ntc_id, float(r["frequency_run_ghz"]), tuple(tags))
    if res["flags"] is None:
        emit = np.ones(N, dtype=bool)
        note = res["note"]
    else:
        emit = np.asarray(res["flags"], dtype=bool)
        note = res["note"]

n_emit = int(emit.sum())

# ── Metrics ──
m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("Satellites (total)", N)
m2.metric("Emitters in scenario", n_emit if choice != COMPLETE else N)
m3.metric("Planes", summary["num_planes"])
m4.metric("Altitude", f"{summary['alt_km']:.0f} km")
m5.metric("Inclination", f"{summary['i_deg']:.1f}°")
m6.metric("Eccentricity", f"{summary['ecc']:.4f}")

if note:
    st.warning(note)
if choice != COMPLETE and n_emit == 0:
    st.info(
        "No satellite emits at this scenario's frequency "
        f"({scenarios[choice]['frequency_run_ghz']:.4f} GHz) per grp/mask_lnk1."
    )

# A globe click is delivered as ?cpick=IDX (set by the iframe, which then clicks
# the hidden apply button to force this rerun). Consume it into the non-widget
# pending key BEFORE the picker widget is created, then strip it from the URL.
_cpick = st.query_params.get("cpick")
if _cpick is not None:
    try:
        _cpi = int(_cpick)
    except (TypeError, ValueError):
        _cpi = None
    try:
        del st.query_params["cpick"]
    except Exception:  # noqa: BLE001
        pass
    if (_cpi is not None and 0 <= _cpi < N
            and _cpi != st.session_state.get("constel_pick_sat")):
        st.session_state["_constel_pending_pick"] = _cpi

# ── Satellite picker ──
# A globe click stores its target in a NON-widget key; apply it to the picker
# BEFORE the widget is instantiated (Streamlit forbids mutating a widget's
# session_state key after the widget is created in the same run).
if "_constel_pending_pick" in st.session_state:
    _pend = st.session_state.pop("_constel_pending_pick")
    if isinstance(_pend, int) and 0 <= _pend < N:
        st.session_state["constel_pick_sat"] = _pend

# Hidden button clicked by the globe iframe (via the same-origin parent DOM) to
# commit a click-selection — its only role is to trigger the rerun that reads
# ?cpick above.
st.markdown(
    "<style>.st-key-constel_apply{position:absolute;left:-9999px;height:0;"
    "overflow:hidden;}</style>", unsafe_allow_html=True)
st.button("apply pick", key="constel_apply")

pick_i: int | None = None
if have_tags:
    cpk, cbtn = st.columns([4, 1.2])
    with cpk:
        pick_i = st.selectbox(
            "Highlight satellite (or click one on the globe)",
            options=list(range(N)),
            format_func=_sat_label,
            key="constel_pick_sat",
            help="Click anywhere on the globe to highlight the nearest "
                 "satellite, or pick it here; then press *Project mask* to "
                 "isolate it and project its mask as a heat-map.",
        )
    with cbtn:
        st.markdown("<div style='height:1.7em'></div>", unsafe_allow_html=True)
        if st.button("Project mask", icon=":material/public:",
                      key="constel_isolate_btn"):
            st.session_state["constel_sel_idx"] = int(pick_i)
            st.session_state["constel_step"] = 0
            st.session_state["constel_mask_id"] = None
            st.rerun()

# ── 3D globe ──
title = (
    f"{summary['sat_name'] or system_id} — "
    + ("all frequencies" if choice == COMPLETE else _scn_label(scenarios[choice]))
)
fig = plots.earth_3d_chart(
    satellites_xyz=None, title=title, uirevision="constel-globe",
)
# Lat/lon graticule with the equator emphasized (user-requested reference).
fig.add_traces(plots.graticule_3d())
registry: dict[int, np.ndarray] = {}


def _add_sats(idx_arr: np.ndarray, **kwargs) -> None:
    """Add a Scatter3d for the given global sat indices and register them for
    click→index resolution."""
    if idx_arr.size == 0:
        return
    cn = len(fig.data)
    P = pos[idx_arr]
    trace = go.Scatter3d(
        x=P[:, 0].tolist(), y=P[:, 1].tolist(), z=P[:, 2].tolist(),
        mode="markers", **kwargs,
    )
    fig.add_trace(trace)
    registry[cn] = np.asarray(idx_arr, dtype=int)


shown = emit if filt == "Emitters only" else np.ones(N, dtype=bool)
shown_idx = np.nonzero(shown)[0]
emit_idx = np.nonzero(emit)[0]
nonemit_idx = np.nonzero(~emit)[0]
n_shown = int(shown.sum())

if color_by == "Orbital plane":
    if not have_planes:
        st.warning("Plane indices unavailable (misalignment); coloring uniformly.")
        _add_sats(shown_idx, marker=dict(size=2.6, color=EMIT_COLOR),
                  name=f"satellites ({n_shown})")
    else:
        pl = plane_arr[shown_idx]
        orbs = orb_arr[shown_idx] if orb_arr is not None else None
        txt = [
            f"{_sat_label(int(shown_idx[k]))}"
            for k in range(shown_idx.size)
        ]
        _add_sats(
            shown_idx,
            marker=dict(
                size=2.6, color=pl.tolist(), colorscale="Turbo",
                cmin=0, cmax=int(plane_arr.max()),
                colorbar=dict(title="Plane", thickness=12),
            ),
            text=txt, hoverinfo="text",
            name=f"satellites ({n_shown}) · {int(plane_arr.max()) + 1} planes",
        )
else:  # Emitter
    if filt == "Emitters only":
        _add_sats(emit_idx, marker=dict(size=2.8, color=EMIT_COLOR),
                  name=f"emitters ({n_emit})")
    else:
        _add_sats(nonemit_idx, marker=dict(size=1.8, color=DIM_COLOR, opacity=0.45),
                  name=f"non-emitters ({N - n_emit})")
        _add_sats(emit_idx, marker=dict(size=2.8, color=EMIT_COLOR),
                  name=f"emitters ({n_emit})")

# Highlight the satellite currently chosen in the picker (before projecting).
if pick_i is not None and 0 <= int(pick_i) < N:
    hp = pos[int(pick_i)]
    cn = len(fig.data)
    fig.add_trace(go.Scatter3d(
        x=[hp[0]], y=[hp[1]], z=[hp[2]], mode="markers",
        marker=dict(size=10, color="#f472b6", symbol="diamond",
                      line=dict(color="#ffffff", width=2)),
        name=f"selected · {_sat_label(int(pick_i))}",
        hovertext=[_sat_label(int(pick_i))], hoverinfo="text",
    ))
    registry[cn] = np.array([int(pick_i)], dtype=int)

fig.update_layout(legend=dict(orientation="h", y=-0.02))
# Rendered as an iframe (not st.plotly_chart) because Streamlit does not deliver
# 3D click events; the component's JS selects the nearest satellite on click and
# bridges the index back via query param + the hidden "constel_apply" button.
sat_pos = [
    (float(pos[i, 0]), float(pos[i, 1]), float(pos[i, 2]), int(i))
    for i in shown_idx.tolist()
]
components.html(
    _globe_scenario_html(
        fig, sat_pos=sat_pos, apply_key="constel_apply", query_key="cpick",
        height=760,
    ),
    height=772, scrolling=False,
)
st.caption("Click anywhere on the globe to highlight the nearest satellite, "
            "then press **Project mask**. Drag to orbit · scroll to zoom.")

# ── Scenario detail ──
if choice != COMPLETE:
    r = scenarios[choice]
    with st.expander("Scenario detail (Article 22)", expanded=False):
        st.json({
            "service": r["service"],
            "rr_reference": r["rr_reference"],
            "regions": r.get("regions"),
            "band_ghz": [r["band_start_ghz"], r["band_end_ghz"]],
            "run_ghz": [r["run_min_ghz"], r["run_max_ghz"]],
            "frequency_run_ghz": r["frequency_run_ghz"],
            "bw_khz": r["bw_khz"],
            "rf_pattern_rr": r.get("rf_pattern_rr"),
            "mask_ref": r.get("mask_ref"),
            "note": "antenna diameter collapsed (does not affect the emitter set)",
        })

st.caption(
    f"{_scn['n_pfd_masks']} PFD mask(s) in filing · "
    f"{len(scenarios)} Art. 22 scenario(s) (after collapsing diameter) · "
    "globe at t=0 (ECEF), Earth radius = 1. "
    "Click a satellite (or use the picker) to isolate it and project its mask."
)
