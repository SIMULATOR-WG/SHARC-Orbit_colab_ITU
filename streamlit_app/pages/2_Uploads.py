"""Uploads — list filings + systems + re-scan notices/masks for existing filings."""
from __future__ import annotations

import streamlit as st
import pandas as pd

from lib import srs_inspect, storage, theme, tour
from lib.manual import help_expander
from lib.widgets import confirm_delete_button
from lib.state import (
    current_system_id, set_current_system_id,
    current_mask_id, set_current_mask_id,
    use_persisted_state, set_persisted_state,
)

st.set_page_config(page_title="Uploads · SHARC-Orbit", page_icon=":material/folder_open:", layout="wide")
theme.inject()

st.title("Registered filings & systems")
help_expander("uploads")
tour.maybe_render("uploads")
st.caption(
    "A filing is one MDB/XML on disk; each notice (ntc_id) + mask choice "
    "is one **system**. Filings registered before the multi-notice wizard "
    "(or with only a default system) can be re-scanned below."
)

uploads = storage.list_uploads()
systems = storage.list_systems()

if not uploads:
    st.info("No filings yet. Use **Upload** to register your first one.")
    st.page_link("pages/1_Upload.py", label="Upload a filing", icon=":material/upload:")
    st.stop()

# ── Filing name prefix ───────────────────────────────────────────────────────
_pfx_prev = use_persisted_state("filing.prefix", {"value": ""})
with st.container(border=True):
    st.markdown("**Filing name prefix**")
    st.caption(
        "Common prefix for filing names. **Apply** renames every existing "
        "filing (idempotent — won't double-prefix) and is remembered as the "
        "default prefix for new uploads."
    )
    c_pfx1, c_pfx2 = st.columns([4, 2])
    with c_pfx1:
        pfx = st.text_input(
            "Prefix", value=_pfx_prev.get("value", ""),
            placeholder="e.g. ANATEL — ", label_visibility="collapsed",
        )
    with c_pfx2:
        if st.button("Apply to all filings", icon=":material/drive_file_rename_outline:",
                      disabled=not pfx.strip()):
            set_persisted_state("filing.prefix", {"value": pfx.strip()})
            n = storage.apply_filing_prefix(pfx.strip())
            st.success(f"Prefix applied — {n} filing(s) renamed.")
            st.rerun()

# ── Maintenance ─────────────────────────────────────────────────────────────
with st.container(border=True):
    st.markdown("**Maintenance**")
    sel_to_delete = st.multiselect(
        "Select filings to delete",
        options=[u["id"] for u in uploads],
        format_func=lambda i: next(
            (f"{u['label']} ({u['id']})" for u in uploads if u["id"] == i), i
        ),
    )
    def _del_selected():
        n = storage.delete_uploads(sel_to_delete)
        st.toast(f"Deleted {n} filing(s) + cascaded systems.", icon=":material/delete:")

    def _del_all():
        n = storage.delete_all_uploads()
        st.toast(f"Deleted {n} filing(s) + systems + on-disk uploads.",
                 icon=":material/delete_forever:")

    cols = st.columns([2, 2, 3])
    with cols[0]:
        confirm_delete_button(
            "Delete selected", key="del_sel_uploads", on_confirm=_del_selected,
            disabled=not sel_to_delete,
            message=f"Delete {len(sel_to_delete)} selected filing(s) and their "
                    "systems? This cannot be undone.",
        )
    with cols[1]:
        confirm_delete_button(
            "Delete ALL filings", key="del_all_uploads", on_confirm=_del_all,
            button_type="primary", icon=":material/delete_forever:",
            message=f"Delete ALL {len(uploads)} filing(s), every system, and the "
                    "on-disk uploads? This cannot be undone.",
        )
    with cols[2]:
        st.caption(
            "Cascades to systems. To also clean run artifact directories, "
            "delete the matching runs on the **Runs** page."
        )

st.subheader("Filings")
udf = pd.DataFrame(
    [
        {
            "id": u["id"],
            "label": u["label"],
            "network": u.get("network_name") or "—",
            "srs_path": u["srs_path"],
            "mask_path": u.get("mask_path") or "—",
            "created_at (BRT)": storage.fmt_local(u["created_at"]),
        }
        for u in uploads
    ]
)
st.dataframe(udf, hide_index=True, width='stretch')

st.subheader("Systems")
if systems:
    sdf = pd.DataFrame(
        [
            {
                "system_id": s["id"],
                "filing": s.get("upload_label") or s["upload_id"],
                "ntc_id": s.get("ntc_id") or "—",
                "sat_name": s.get("sat_name") or "—",
                "admin": s.get("admin") or "—",
            }
            for s in systems
        ]
    )
    st.dataframe(sdf, hide_index=True, width='stretch')

    # ── Orbital parameters per system ──────────────────────────────────
    st.subheader("Orbital parameters")
    sys_options = [s["id"] for s in systems]

    def _sys_label(sid: str) -> str:
        s = next((x for x in systems if x["id"] == sid), {})
        return (
            f"{sid} · {s.get('upload_label') or s.get('upload_id', '?')} · "
            f"ntc {s.get('ntc_id') or '—'} · "
            f"{s.get('sat_name') or '—'}"
        )

    # Default to the cross-page shared system_id so the orbital panel
    # comes back where you left it (and stays in sync with Single-entry /
    # Mask Viewer).
    _shared_sid = current_system_id()
    _default_idx = (
        sys_options.index(_shared_sid) if _shared_sid in sys_options else 0
    )
    pick = st.selectbox(
        "Pick a system to inspect",
        options=sys_options, format_func=_sys_label,
        index=_default_idx,
        key="orbit_sys_pick",
        width="stretch",
    )
    if pick and pick != _shared_sid:
        set_current_system_id(pick)
    if pick:
        row = next((s for s in systems if s["id"] == pick), None)
        if row:
            params = srs_inspect.orbital_params(
                row["srs_path"], row.get("ntc_id"),
            )
            planes = params.get("planes") or []
            if not planes:
                st.warning(
                    "Could not read orbital data from the MDB. The file may be "
                    "unsupported, corrupt, or inaccessible."
                )
            else:
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Satellite name", params.get("sat_name") or "—")
                m2.metric("Notice (`ntc_id`)", params.get("ntc_id") or "—")
                m3.metric("Orbital planes", params.get("nbr_planes") or len(planes))
                m4.metric("Total satellites", params.get("nbr_sat_total") or 0)
                if params.get("x_zone_deg") is not None:
                    st.caption(
                        f"Exclusion zone declared in SRS: "
                        f"±{params['x_zone_deg']:.2f}°"
                    )

                # Per-plane orbital table
                orb_df = pd.DataFrame(
                    [
                        {
                            "plane (orb_id)": p["orb_id"],
                            "sats/plane": p["nbr_sat_pl"],
                            "a (km)": f"{p['semi_major_axis_km']:.3f}",
                            "altitude (km)": f"{p['altitude_km']:.3f}",
                            "perigee (km)": f"{p['perigee_km']:.3f}",
                            "apogee (km)": f"{p['apogee_km']:.3f}",
                            "e": f"{p['eccentricity']:.6f}",
                            "i (°)": f"{p['inclin_deg']:.3f}",
                            "Ω RAAN (°)": f"{p['right_asc_deg']:.3f}",
                            "ω peri (°)": f"{p['perigee_arg_deg']:.3f}",
                            "period (min)": f"{p['period_min']:.3f}",
                            "Ω̇ (°/day)": f"{p['precession_deg_day']:.4f}",
                            "sun-synch": "✓" if p["sun_synch"] else "—",
                            "stat-keep": "✓" if p["station_keep"] else "—",
                        }
                        for p in planes
                    ]
                )
                st.dataframe(orb_df, hide_index=True, width='stretch')

                # ── Frequency bands (masks + groups) ───────────────
                bands = srs_inspect.frequency_bands(
                    row["srs_path"], row.get("ntc_id"),
                )
                masks = bands.get("masks") or []
                groups = bands.get("groups") or []

                st.subheader("Operating frequency bands")

                if masks:
                    st.markdown("**Masks** (declared in the SRS `mask_info` table):")
                    mdf = pd.DataFrame(
                        [
                            {
                                "mask_id": m["mask_id"],
                                "type": m["type"],
                                "freq_min (GHz)": f"{m['freq_min_ghz']:.3f}",
                                "freq_max (GHz)": f"{m['freq_max_ghz']:.3f}",
                                "bandwidth (MHz)": f"{(m['freq_max_ghz'] - m['freq_min_ghz']) * 1000:.1f}",
                                "subtype": m["subtype"],
                            }
                            for m in masks
                        ]
                    )
                    st.dataframe(mdf, hide_index=True, width='stretch')

                    # Per-mask "View" buttons — open the Mask Viewer in a
                    # new browser tab. Only PFD masks (`type == "PFD"`) can
                    # be rendered (the viewer uses load_pfd_mask_from_xml);
                    # EIRP / Other masks are listed but not linked.
                    pfd_masks = [m for m in masks if m["type"] == "PFD"]
                    if pfd_masks:
                        # Scoped CSS — compact font + tighter padding for
                        # this row of mask-view buttons only.
                        st.markdown(
                            "<style>"
                            ".so-mask-btns .stButton button { "
                            "font-size: var(--so-fs-small) !important; "
                            "padding: 3px 10px !important; "
                            "font-weight: 500 !important; }"
                            "</style>"
                            "<div class='so-mask-btns'></div>",
                            unsafe_allow_html=True,
                        )
                        st.caption("Open a PFD mask in the **Mask Viewer**:")
                        vcols = st.columns(min(4, len(pfd_masks)) or 1)
                        for i, m in enumerate(pfd_masks):
                            with vcols[i % len(vcols)]:
                                label = (
                                    f"mask {m['mask_id']} · "
                                    f"{m['freq_min_ghz']:.1f}–"
                                    f"{m['freq_max_ghz']:.1f} GHz"
                                )
                                if st.button(
                                    label,
                                    icon=":material/blur_on:",
                                    key=f"view_mask_{row['id']}_{m['mask_id']}",
                                    help="Open in Mask Viewer",
                                ):
                                    # Persist via shared state — `st.switch_page`
                                    # drops query params, so we cannot rely on
                                    # `st.query_params[...] = ...`. The Mask
                                    # Viewer falls back to `current_*` when no
                                    # query params are present.
                                    set_current_system_id(row["id"])
                                    set_current_mask_id(int(m["mask_id"]))
                                    st.switch_page("pages/B_Mask_Viewer.py")
                    if any(m["type"] != "PFD" for m in masks):
                        st.caption(
                            "Non-PFD masks (EIRP / Other) are listed above "
                            "but the viewer only renders PFD masks."
                        )
                else:
                    st.caption("No mask entries found for this notice.")

                if groups:
                    st.markdown(
                        "**Emission / reception groups** "
                        "(declared in the SRS `grp` table — Tx and Rx bands):"
                    )
                    gdf = pd.DataFrame(
                        [
                            {
                                "grp_id": g["grp_id"],
                                "side": g["emi_rcp"],
                                "beam": g["beam_name"],
                                "freq_min (GHz)": (
                                    f"{g['freq_min_ghz']:.3f}"
                                    if g["freq_min_ghz"] is not None else "—"
                                ),
                                "freq_max (GHz)": (
                                    f"{g['freq_max_ghz']:.3f}"
                                    if g["freq_max_ghz"] is not None else "—"
                                ),
                                "min elev (°)": (
                                    f"{g['elev_min_deg']:.2f}"
                                    if g["elev_min_deg"] is not None else "—"
                                ),
                            }
                            for g in groups
                        ]
                    )
                    st.dataframe(gdf, hide_index=True, width='stretch')
                elif not masks:
                    st.caption("No `grp` rows found either — the MDB may not "
                                 "declare operating bands for this notice.")

                with st.expander("Raw orbital_params + frequency_bands (JSON)",
                                  expanded=False):
                    st.json({"orbital": params, "frequency": bands})
else:
    st.info("No systems registered yet.")

st.divider()
st.subheader("Filing details / re-scan / delete")
ids = [u["id"] for u in uploads]
labels = {u["id"]: f"{u['label']} ({u['id']})" for u in uploads}
_last_upload = use_persisted_state("uploads.inspect_upload_id", "")
_inspect_idx = ids.index(_last_upload) if _last_upload in ids else 0
sel = st.selectbox(
    "Inspect filing", options=ids, format_func=lambda i: labels[i],
    index=_inspect_idx,
)
if sel and sel != _last_upload:
    set_persisted_state("uploads.inspect_upload_id", sel)

if sel:
    item = storage.get_upload(sel)
    if not item:
        st.error(f"Filing {sel} not found.")
        st.stop()

    st.json(item)
    nsys = [s for s in systems if s["upload_id"] == sel]
    st.write(f"**{len(nsys)} system(s)** registered for this filing.")
    for s in nsys:
        cols = st.columns([4, 1])
        with cols[0]:
            st.write(
                f"`{s['id']}` · ntc {s.get('ntc_id') or '—'} · "
                f"{s.get('sat_name') or '—'}"
            )
        with cols[1]:
            confirm_delete_button(
                "Delete", key=f"del_sys_{s['id']}",
                on_confirm=lambda sid=s["id"]: (storage.delete_system(sid),
                                                st.toast("System deleted.")),
                message=f"Delete system `{s['id']}` (ntc {s.get('ntc_id') or '—'})?",
            )

    st.markdown("---")
    st.markdown("**Re-scan notices / masks** — discover (or re-discover) systems in this filing without re-uploading.")
    if st.button("Re-scan now", key=f"rescan_{sel}"):
        # bust the lru_cache so the read picks up any change
        srs_inspect.list_notices.cache_clear()
        srs_inspect.list_masks_in_srs.cache_clear()
        srs_inspect.list_masks_in_mask_mdb.cache_clear()
        st.session_state[f"rescan_open_{sel}"] = True

    if st.session_state.get(f"rescan_open_{sel}"):
        with st.container(border=True):
            notices = srs_inspect.list_notices(item["srs_path"])
            if not notices:
                st.warning(
                    "No notices found in the `non_geo` table. The MDB may be a "
                    "mask-only database, or the table is missing/empty. Use the "
                    "engine-default system below."
                )
            else:
                st.success(f"Detected **{len(notices)}** notice(s).")
                for n in notices:
                    st.write(f"• `{n['ntc_id']}` — {n.get('sat_name') or '?'} ({n.get('admin') or '—'})")

            ntc_ids = [n["ntc_id"] for n in notices] if notices else [None]
            labels_by_id = ({n["ntc_id"]: n["label"] for n in notices}
                            if notices else {None: "(no ntc_id)"})
            picked_ntcs = st.multiselect(
                "Notices to register as systems",
                options=ntc_ids,
                default=ntc_ids,
                format_func=lambda i: labels_by_id.get(i, str(i)),
                key=f"rescan_ntcs_{sel}",
            )

            # Show masks informationally — engine handles distribution via mask_lnk1.
            for ntc in picked_ntcs:
                masks = srs_inspect.list_masks_for_filing(
                    item["srs_path"], item.get("mask_path"), ntc,
                )
                if not masks:
                    st.caption(f"• Notice `{ntc or '-'}` — no PFD mask detected.")
                elif len(masks) == 1:
                    st.caption(f"• Notice `{ntc or '-'}` — 1 P-mask (id {masks[0]['mask_id']}).")
                else:
                    ids = ", ".join(str(m["mask_id"]) for m in masks)
                    st.caption(
                        f"• Notice `{ntc or '-'}` — {len(masks)} P-masks ({ids}). "
                        "Engine distributes them across satellites per mask_lnk1."
                    )

            if st.button("Register selected systems", icon=":material/check:",
                          type="primary", key=f"rescan_commit_{sel}"):
                added = []
                if not picked_ntcs:
                    added.append(storage.add_system(
                        upload_id=sel, ntc_id=None, mask_id=None, label=item["label"],
                    ))
                else:
                    for ntc in picked_ntcs:
                        row = next((n for n in notices if n["ntc_id"] == ntc), {})
                        added.append(storage.add_system(
                            upload_id=sel, ntc_id=ntc, mask_id=None,
                            sat_name=row.get("sat_name"),
                            admin=row.get("admin"),
                        ))
                st.success(f"Added/refreshed {len(added)} system(s): " + ", ".join(f"`{x}`" for x in added))
                st.session_state[f"rescan_open_{sel}"] = False
                st.rerun()

    st.markdown("---")
    col1, col2 = st.columns(2)
    with col1:
        confirm_delete_button(
            "Delete entire filing (cascades systems)", key=f"del_filing_{sel}",
            on_confirm=lambda sid=sel: (storage.delete_upload(sid),
                                        st.toast(f"Filing {sid} + systems removed.")),
            message=f"Delete filing `{sel}` and ALL its systems? This cannot be undone.",
        )
    with col2:
        st.page_link("pages/4_Aggregate.py", label="Use in Aggregate", icon=":material/grid_view:")
