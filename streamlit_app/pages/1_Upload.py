"""Upload page — register SRS filings + auto-detect notices and masks.

Workflow:
  1. User uploads SRS .mdb + PFD mask .mdb (both required).
  2. App reads the `non_geo` table to list notices (ntc_ids).
  3. User picks notices to register (one MDB can yield multiple systems).
  4. For each selected notice, the available PFD masks are listed and the
     user picks one mask_id per notice.
  5. Each (upload_id, ntc_id, mask_id) tuple is saved as a row in the
     `systems` table — ready for use in Single-entry / Aggregate.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from lib import UPLOADS_DIR, filings, srs_inspect, storage, theme, tour
from lib.manual import help_expander
from lib.state import use_persisted_state

st.set_page_config(page_title="Upload · SHARC-Orbit", page_icon=":material/upload:", layout="wide")
theme.inject()


def _timestamp_label() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")


# Extensions the app can actually parse (filings.py: SRS .mdb / .xml; the
# PFD mask is .mdb). `st.file_uploader(type=...)` filters client-side only.
_ALLOWED_UPLOAD_EXTS = {".mdb", ".xml"}


def _save_file(uploaded, dst_dir: Path) -> Path:
    # Sanitize the client-supplied filename: a forged name with path
    # components (e.g. `../../x.mdb`) must not escape the upload dir.
    name = Path(uploaded.name or "").name
    if not name or name.startswith("."):
        st.error(f"Invalid upload filename: `{uploaded.name}`.")
        st.stop()
    if Path(name).suffix.lower() not in _ALLOWED_UPLOAD_EXTS:
        st.error(
            f"Unsupported upload extension on `{name}` — allowed: "
            + ", ".join(sorted(_ALLOWED_UPLOAD_EXTS)) + "."
        )
        st.stop()
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / name
    dst.write_bytes(uploaded.getbuffer())
    return dst


st.title("Upload filings")
help_expander("upload")
tour.maybe_render("upload")
st.caption(
    "Register SRS filings (`.mdb`) + the corresponding PFD mask "
    "(`.mdb`). Each notice (ntc_id) in the MDB becomes an independent "
    "**system** that can be combined with others on the Aggregate page "
    "— even within a single MDB."
)


# ─── Stage 1: file upload + label ────────────────────────────────────────────
if "upload_stage" not in st.session_state:
    st.session_state["upload_stage"] = "files"
if "upload_pending" not in st.session_state:
    st.session_state["upload_pending"] = None

if st.session_state["upload_stage"] == "files":
    with st.form("upload_files_form", clear_on_submit=False):
        srs_file = st.file_uploader(
            "SRS database (.mdb)",
            type=["mdb"],
            accept_multiple_files=False,
        )
        mask_file = st.file_uploader(
            "PFD mask (.mdb)",
            type=["mdb"],
            accept_multiple_files=False,
            help="Required — contains the PFD mask referenced by the SRS "
                 "notice. Both files are needed to run S.1503 / S.1588.",
        )
        _pfx = (use_persisted_state("filing.prefix", {"value": ""})
                .get("value", ""))
        label = st.text_input(
            "Filing label",
            value=f"{_pfx}filing-{_timestamp_label()}",
            help="Identifier shown on Uploads / Single-entry / Aggregate. "
                 "Pre-filled with the prefix set on the Uploads page.",
        )
        submit = st.form_submit_button("Next: detect notices/masks",
                                         icon=":material/arrow_forward:",
                                         type="primary")

    if submit:
        if not srs_file:
            st.error("Pick an SRS database file.")
            st.stop()
        if not mask_file:
            st.error("Pick a PFD mask file (`.mdb`).")
            st.stop()
        upload_id = uuid.uuid4().hex[:12]
        dst_dir = UPLOADS_DIR / upload_id
        srs_path = _save_file(srs_file, dst_dir)
        mask_path = _save_file(mask_file, dst_dir) if mask_file else None
        # Parse once now so Step 2 can suggest a label from the network name
        # (and the final register reuses it — no second parse).
        try:
            preview = filings.preview_srs(Path(srs_path))
        except Exception:  # noqa: BLE001
            preview = {}
        st.session_state["upload_pending"] = {
            "upload_id": upload_id,
            "label": label.strip(),
            "srs_path": str(srs_path),
            "mask_path": str(mask_path) if mask_path else None,
            "uploaded_name": srs_file.name,
            "network_name": preview.get("network_name"),
            "preview": preview,
            "ts": _timestamp_label(),
        }
        st.session_state["upload_stage"] = "notices"
        st.rerun()
    st.stop()


# ─── Stage 2: notice + mask picker ───────────────────────────────────────────

pending = st.session_state["upload_pending"]
if not pending:
    st.session_state["upload_stage"] = "files"
    st.rerun()
    st.stop()

st.markdown(f"### Step 2 — Notices in `{Path(pending['srs_path']).name}`")
st.caption(
    "Pick one or more notices (ntc_id) from this MDB. For each selected notice, "
    "the available PFD masks are listed below; pick one mask per notice. Each "
    "(notice, mask) tuple becomes an independent system."
)

is_xml = Path(pending["srs_path"]).suffix.lower() in (".xml", ".yaml", ".yml")
if is_xml:
    notices = []
else:
    with st.spinner("Reading notices from MDB…"):
        notices = srs_inspect.list_notices(pending["srs_path"])

if not notices and not is_xml:
    st.warning(
        "No notices found in the `non_geo` table. This may indicate an unsupported "
        "or corrupt MDB. The filing can still be saved "
        "as a single system without ntc_id."
    )
elif notices:
    st.success(f"Detected **{len(notices)}** notice(s).")

ntc_ids = [n["ntc_id"] for n in notices] if notices else [None]
labels_by_id = {n["ntc_id"]: n["label"] for n in notices} if notices else {None: "(no ntc_id)"}

picked_ntcs = st.multiselect(
    "Notices to register as systems",
    options=ntc_ids,
    default=ntc_ids,
    format_func=lambda i: labels_by_id.get(i, str(i)),
    help="Each notice becomes one system. Mask handling is transparent: if "
         "the filing has multiple PFD masks distributed across satellites "
         "(via mask_lnk1), the engine applies all of them in one simulation.",
)

# Show detected masks per notice purely informationally — no choice required.
for ntc in (picked_ntcs if not is_xml else []):
    masks = srs_inspect.list_masks_for_filing(
        pending["srs_path"], pending.get("mask_path"), ntc,
    )
    if not masks:
        st.caption(f"• Notice `{ntc or '-'}` — no PFD mask (f_mask='P') detected.")
    elif len(masks) == 1:
        m = masks[0]
        st.caption(f"• Notice `{ntc or '-'}` — 1 P-mask detected (id {m.get('mask_id')}).")
    else:
        ids = ", ".join(str(m["mask_id"]) for m in masks)
        st.caption(
            f"• Notice `{ntc or '-'}` — {len(masks)} P-masks detected (ids {ids}). "
            "Engine will distribute them across satellites per mask_lnk1."
        )

# Suggested filing label from the SRS network/satellite name (+ persisted
# prefix). Prefer the notice's sat_name (reliable), then the preview's
# network_name, then the Stage-1 label.
_pfx = use_persisted_state("filing.prefix", {"value": ""}).get("value", "")
_net = ""
if notices:
    _net = (notices[0].get("sat_name") or "").strip()
if not _net:
    _net = (pending.get("network_name") or "").strip()
_ts = pending.get("ts") or ""
if _net:
    _suggested = f"{_pfx}{_net}-{_ts}" if _ts else f"{_pfx}{_net}"
else:
    _suggested = pending["label"]
new_label = st.text_input(
    "Filing label",
    value=_suggested,
    key="upload_final_label",
    help="Suggested from the SRS network name"
         + (" with your filing prefix" if _pfx else "")
         + ". Edit if needed.",
)

col1, col2 = st.columns([1, 1])
with col1:
    if st.button("Back (cancel this upload)", icon=":material/arrow_back:"):
        st.session_state["upload_stage"] = "files"
        st.session_state["upload_pending"] = None
        st.rerun()
with col2:
    confirm = st.button(
        "Register filing + systems",
        icon=":material/check:",
        type="primary",
        disabled=(not picked_ntcs and notices) and not is_xml,
    )

if confirm:
    final_label = (new_label or "").strip() or pending["label"]
    preview = pending.get("preview") or filings.preview_srs(Path(pending["srs_path"]))
    upload_id = pending["upload_id"]
    storage.add_upload(
        upload_id=upload_id,
        label=final_label,
        srs_path=Path(pending["srs_path"]),
        mask_path=Path(pending["mask_path"]) if pending.get("mask_path") else None,
        network_name=preview.get("network_name"),
        metadata={"preview": preview, "uploaded_filename": pending["uploaded_name"],
                   "n_notices_detected": len(notices)},
    )
    # If no notices (XML or empty), register a single system with ntc_id=None
    if not picked_ntcs:
        sid = storage.add_system(
            upload_id=upload_id,
            ntc_id=None,
            mask_id=None,
            label=final_label,
        )
        st.success(f"Registered filing + system `{sid}`.")
    else:
        added = []
        for ntc in picked_ntcs:
            row = next((n for n in notices if n["ntc_id"] == ntc), {})
            sid = storage.add_system(
                upload_id=upload_id,
                ntc_id=ntc,
                mask_id=None,
                sat_name=row.get("sat_name"),
                admin=row.get("admin"),
            )
            added.append(sid)
        st.success(
            f"Registered filing `{upload_id}` with {len(added)} system(s): " +
            ", ".join(f"`{s}`" for s in added)
        )
    st.session_state["upload_stage"] = "files"
    st.session_state["upload_pending"] = None
    st.page_link("pages/2_Uploads.py", label="Go to Uploads list", icon=":material/folder_open:")
