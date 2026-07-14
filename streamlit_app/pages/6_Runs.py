"""Runs — history of all runs (DB-synced, with delete actions)."""
from __future__ import annotations

import json
import time

import streamlit as st
import pandas as pd

from lib import launcher, storage, theme, workers
from lib.manual import help_expander
from lib.widgets import confirm_delete_button
from lib.state import (
    current_run_id, set_current_run_id,
    set_current_system_id, set_persisted_state,
)

st.set_page_config(page_title="Runs · SHARC-Orbit", page_icon=":material/list:", layout="wide")
theme.inject()

st.title("Runs")
help_expander("runs")
st.caption("Local history of simulation runs.")


def _load_config(run: dict) -> None:
    """Reload a run's study configuration into the matching form and open it.

    Maps the persisted run params back to the page's form-state schema
    (`s1503.form` / `s1588.form`) and switches to Single-entry / Aggregate
    pre-filled, so the user can re-run with the same settings.
    """
    try:
        params = json.loads(run.get("params_json") or "{}")
    except Exception:  # noqa: BLE001
        params = {}
    ap = params.get("artificial_precession")
    ap_mode = "on" if ap is True else ("off" if ap is False else "auto")

    # Warn (don't fail) when the run's filing(s) were deleted — the form
    # gracefully falls back (single: first system; aggregate: drops missing
    # ids), but the user should know to re-pick.
    _valid = {s["id"] for s in storage.list_systems()}

    common_keys = [
        "num_time_steps", "time_step_s", "min_elevation_deg", "service",
        "es_antenna_diameter_m", "wcga_s1503", "wcga_no_mask_symmetry",
        "s1503_step_deg", "gso_longitude_mode", "alpha_method",
        "dual_time_step_mode", "fine_time_step_s", "use_precession_mdb",
        "apply_station_keeping", "emulate_s1503_2", "force_gmst0_zero",
        # Article 22 scenario (carried so reload reproduces the exact run).
        "reference_bandwidth_khz", "simulation_frequency_ghz",
    ]
    if run["kind"] == "single":
        keys = common_keys + [
            "s1503_trail_all_points", "wcg_manual",
            "wcg_manual_es_lat", "wcg_manual_es_lon", "wcg_manual_gso_lon",
            "mask_id",  # PFD mask pinned by the Art.22 scenario leaf
        ]
        state = {k: params[k] for k in keys if k in params}
        state["system_id"] = params.get("system_id")
        state["artificial_prec_mode"] = ap_mode
        # Flag the carried Art.22 scenario as coming from an explicit reload —
        # the form pages only pre-enable "Apply reloaded Article 22 scenario"
        # for this flow (not for values left over from an old launch).
        if any(state.get(k) is not None for k in
               ("reference_bandwidth_khz", "simulation_frequency_ghz",
                "mask_id")):
            state["art22_from_reload"] = True
        set_persisted_state("s1503.form", state)
        sid = params.get("system_id")
        if sid and sid not in _valid:
            st.toast("Original filing was deleted — pick a system.",
                     icon=":material/warning:")
        elif sid:
            set_current_system_id(sid)
        st.switch_page("pages/3_Single_entry.py")
    elif run["kind"] == "aggregate":
        keys = common_keys + [
            "grid_step_deg", "gso_pointing_step_deg", "n_geom_max",
            "country_codes", "geometry_es_lat", "geometry_es_lon",
            "geometry_gso_lon",
            # Convolution tail truncation (S.1588 Annex 1 §1).
            "truncate_tail", "truncate_tail_pct",
        ]
        state = {k: params[k] for k in keys if k in params}
        sids = params.get("system_ids") or []
        state["system_ids"] = sids
        state["method"] = params.get("method") or run.get("method") or "method_1"
        state["artificial_prec_mode"] = ap_mode
        if any(state.get(k) is not None for k in
               ("reference_bandwidth_khz", "simulation_frequency_ghz")):
            state["art22_from_reload"] = True
        set_persisted_state("s1588.form", state)
        missing = [i for i in sids if i not in _valid]
        if missing:
            st.toast(f"{len(missing)} original filing(s) deleted — re-pick systems.",
                     icon=":material/warning:")
        st.switch_page("pages/4_Aggregate.py")
    else:
        st.toast("This run has no editable study config.")


def _cancel_if_active(run_id: str) -> None:
    """Cancel a live worker before its run is deleted.

    Without this, deleting a running run leaves an orphan worker that
    recreates the run directory and keeps writing ghost artifacts.
    """
    run = storage.get_run(run_id)
    if not run or run.get("status") not in ("running", "pending"):
        return
    if workers.cancel(run_id):
        handle = workers.get(run_id)
        # Wait for the process to die before files are removed — cover the
        # SIGTERM→SIGKILL escalation window plus margin, otherwise a worker
        # that ignores SIGTERM keeps writing into the deleted run directory.
        deadline = time.monotonic() + workers._CANCEL_KILL_TIMEOUT_S + 2.0
        while time.monotonic() < deadline:
            if handle is None or handle.finished:
                break
            time.sleep(0.1)


def _delete_run(run_id: str) -> None:
    _cancel_if_active(run_id)
    storage.delete_run(run_id)
    st.toast(f"Run {run_id} deleted.")


launcher.sync_all_running()
rows = storage.list_runs(limit=500)
if not rows:
    st.info("No runs yet. Launch from **Single-entry**, **Aggregate** or **Launcher**.")
    st.stop()

# Resolve system name / notice id per run from its params (one lookup map).
_sysmap = {s["id"]: s for s in storage.list_systems()}

# ── Bulk-delete toolbar ─────────────────────────────────────────────────────
with st.container(border=True):
    st.markdown("**Maintenance**")
    def _del_status(status: str):
        n = storage.delete_all_runs(status=status)
        st.toast(f"Deleted {n} {status} run(s).", icon=":material/delete:")

    def _del_all_runs():
        # Cancel live workers first — see _cancel_if_active.
        for _r in storage.list_runs(limit=100_000):
            if _r.get("status") in ("running", "pending"):
                _cancel_if_active(_r["id"])
        n = storage.delete_all_runs()
        st.toast(f"Deleted {n} run(s) + artifacts.", icon=":material/delete_forever:")

    cols = st.columns([2, 2, 2, 3])
    with cols[0]:
        confirm_delete_button(
            "Delete failed runs", key="del_failed",
            on_confirm=lambda: _del_status("failed"),
            message="Delete all failed runs?",
        )
    with cols[1]:
        confirm_delete_button(
            "Delete cancelled runs", key="del_cancelled",
            on_confirm=lambda: _del_status("cancelled"),
            message="Delete all cancelled runs?",
        )
    with cols[2]:
        confirm_delete_button(
            "Delete success runs", key="del_success",
            on_confirm=lambda: _del_status("success"),
            message="Delete all successful runs? Their results will be lost.",
        )
    with cols[3]:
        confirm_delete_button(
            "Delete ALL runs", key="del_all_runs", on_confirm=_del_all_runs,
            button_type="primary", icon=":material/delete_forever:",
            message="Delete ALL runs and their artifact directories on disk? "
                    "This cannot be undone.",
        )

# ── Row-by-row table with Results / Status quick-buttons ──────────────────
# Columns widths: id · system · type · method · status · prog · campaign ·
# created_at · finished_at · duration · actions
_COL_WEIGHTS = [1.6, 2.4, 1.1, 1.1, 1.1, 0.9, 1.3, 1.9, 1.9, 1.1, 3.0]
_TERMINAL = {"success", "failed", "cancelled"}


def _system_cell(run: dict, sysmap: dict) -> tuple[str, str | None]:
    """Resolve a run's system name + notice id from its params.

    Single runs carry one ``system_id``; aggregates carry ``system_ids``.
    Falls back to the params' own ``sat_name``/``ntc_id`` when the filing row
    was deleted.
    """
    try:
        params = json.loads(run.get("params_json") or "{}")
    except Exception:  # noqa: BLE001
        params = {}
    sids = params.get("system_ids")
    if sids:
        srows = [sysmap.get(i) for i in sids]
        names = [s.get("sat_name") or "?" for s in srows if s]
        if not names:
            return f"{len(sids)} systems", None
        head = names[0] + (f" +{len(names) - 1}" if len(names) > 1 else "")
        return head, None
    sid = params.get("system_id")
    s = sysmap.get(sid) if sid else None
    name = (s.get("sat_name") if s else None) or params.get("sat_name") or "—"
    ntc = (s.get("ntc_id") if s else None) or params.get("ntc_id")
    return name, (str(ntc) if ntc else None)


def _duration(created: str, finished: str | None, status: str) -> str:
    """Return a human-readable duration: finished - created (or now - created
    when the run is still in flight)."""
    from datetime import datetime, timezone
    try:
        t0 = datetime.fromisoformat(created)
        if status in _TERMINAL and finished:
            t1 = datetime.fromisoformat(finished)
        else:
            t1 = datetime.now(timezone.utc) if t0.tzinfo else datetime.now()
        s = max(0.0, (t1 - t0).total_seconds())
    except Exception:  # noqa: BLE001
        return "—"
    total = int(s)
    h, rem = divmod(total, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"

st.markdown(
    """
<style>
/* Vertically center the contents of every column cell on this page —
   tallest cell (the action buttons / id) sets the row height, the
   other text/captions align to its centre. */
[data-testid="stHorizontalBlock"] {
    align-items: center !important;
}
[data-testid="stHorizontalBlock"] > [data-testid="stColumn"] > div {
    display: flex !important;
    flex-direction: column !important;
    justify-content: center !important;
    min-height: 32px;
}
[data-testid="stColumn"] p,
[data-testid="stColumn"] code {
    margin: 0 !important;
    line-height: 1.3 !important;
}
/* id column — monospaced, smaller than the body so the full hash fits
   comfortably without dominating the row. */
/* The id button only — scoped by its Streamlit key class so it never
   touches the icon-only action buttons (whose Material ligature would
   break under a monospace font-family override). */
[class*="st-key-pick_row_"] button,
[class*="st-key-pick_row_"] button p,
[class*="st-key-pick_row_"] button span,
[class*="st-key-pick_row_"] button [data-testid="stMarkdownContainer"] p {
    font-size: var(--so-fs-caption) !important;
    font-family: ui-monospace, "JetBrains Mono", Menlo, Consolas, monospace !important;
    line-height: 1 !important;
    letter-spacing: 0 !important;
    font-weight: 400 !important;
}
[class*="st-key-pick_row_"] button {
    padding: 0 8px !important;
    min-height: 28px !important;
    max-height: 28px !important;
    height: 28px !important;
}
/* Vertically center each row cell's content (text, captions, buttons all
   align on their centers despite differing heights). */
[data-testid="stHorizontalBlock"] {
    align-items: center !important;
}
/* Give every row text cell the same 28px line box as the action buttons and
   center its content, so text and buttons share one center line regardless of
   default paragraph margins / element-container gaps. */
[data-testid="stHorizontalBlock"] > [data-testid="stColumn"]
    [data-testid="stMarkdownContainer"] {
    min-height: 28px !important;
    display: flex !important;
    align-items: center !important;
}
[data-testid="stHorizontalBlock"] > [data-testid="stColumn"]
    [data-testid="stMarkdownContainer"] p {
    margin: 0 !important;
    line-height: 1 !important;
}
/* Strip the vertical margins/padding the nested action column block and the
   button element wrappers add, so the buttons are not pushed off the line. */
[data-testid="stHorizontalBlock"] [data-testid="stHorizontalBlock"],
[data-testid="stHorizontalBlock"] [data-testid="stHorizontalBlock"]
    [data-testid="stElementContainer"],
[data-testid="stHorizontalBlock"] [data-testid="stHorizontalBlock"] .stButton {
    margin-top: 0 !important;
    margin-bottom: 0 !important;
    padding-top: 0 !important;
    padding-bottom: 0 !important;
}
/* Action buttons (icon-only, nested 4-column block inside the actions
   cell) — fill their column so they shrink with it instead of overflowing
   and overlapping the neighbour cell at high browser zoom / narrow widths. */
[data-testid="stHorizontalBlock"] [data-testid="stHorizontalBlock"]
    .stButton button {
    width: 30px !important;
    min-width: 28px !important;
    min-height: 28px !important;
    max-height: 28px !important;
    padding: 0 !important;
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
}
[data-testid="stHorizontalBlock"] [data-testid="stHorizontalBlock"]
    .stButton button [data-testid="stIconMaterial"] {
    margin: 0 !important;
    font-size: 18px !important;
    width: 18px !important;
    height: 18px !important;
    line-height: 1 !important;
}
/* Pack the action buttons to the left at their natural width (instead of
   each stretching to fill an equal share of the column) so they stay close
   together; still wraps (2x2) rather than overflow under heavy zoom. */
[data-testid="stHorizontalBlock"] [data-testid="stHorizontalBlock"] {
    flex-wrap: wrap !important;
    row-gap: 4px !important;
    column-gap: 8px !important;
    justify-content: flex-start !important;
}
/* Each action column hugs its 30px button rather than growing. */
[data-testid="stHorizontalBlock"] [data-testid="stHorizontalBlock"]
    > [data-testid="stColumn"] {
    padding-left: 0 !important;
    padding-right: 0 !important;
    flex: 0 0 30px !important;
    min-width: 30px !important;
    width: 30px !important;
}
</style>
    """,
    unsafe_allow_html=True,
)

header = st.columns(_COL_WEIGHTS)
for col, label in zip(
    header,
    ["id", "system", "type", "method", "status", "progress", "campaign",
     "created_at (BRT)", "finished_at (BRT)", "duration", "actions"],
):
    col.markdown(f"**{label}**")

for r in rows:
    cols = st.columns(_COL_WEIGHTS)
    if cols[0].button(r["id"], key=f"pick_row_{r['id']}",
                        help="Select this run in Inspect / delete below"):
        st.session_state["inspect_pick"] = r["id"]
        set_current_run_id(r["id"])
        st.rerun()
    with cols[1]:
        _name, _ntc = _system_cell(r, _sysmap)
        st.write(_name)
        if _ntc:
            st.caption(f"ntc {_ntc}")
    cols[2].write(storage.display_kind(r["kind"]))
    cols[3].write(r.get("method") or "—")
    status = r["status"]
    status_color = {
        "success": ":green[",
        "running": ":blue[",
        "pending": ":orange[",
        "failed": ":red[",
        "cancelled": ":gray[",
    }.get(status, ":gray[")
    cols[4].markdown(f"{status_color}{status}]")
    cols[5].write(f"{(r.get('progress_pct') or 0):.0f}%")
    cols[6].write(r.get("campaign_id") or "—")
    cols[7].caption(storage.fmt_local(r["created_at"]))
    finished_at = r.get("finished_at")
    cols[8].caption(storage.fmt_local(finished_at))
    cols[9].caption(_duration(r["created_at"], finished_at, r["status"]))
    with cols[10]:
        bcol_a, bcol_b, bcol_c, bcol_d = st.columns(4, gap="small")
        with bcol_a:
            if st.button("", icon=":material/insights:",
                          key=f"row_results_{r['id']}",
                          help="Open Results"):
                # `st.switch_page` resets st.query_params — pass the run via
                # the 1.58 `query_params` kwarg (shared state as fallback).
                set_current_run_id(r["id"])
                st.switch_page("pages/8_Results.py",
                               query_params={"run_id": r["id"]})
        with bcol_b:
            if st.button("", icon=":material/pending:",
                          key=f"row_status_{r['id']}",
                          help="Open Status"):
                set_current_run_id(r["id"])
                st.switch_page("pages/7_Status.py",
                               query_params={"run_id": r["id"]})
        with bcol_c:
            if st.button("", icon=":material/settings_backup_restore:",
                          key=f"row_cfg_{r['id']}",
                          help="Reload this study's config into the form"):
                _load_config(r)
        with bcol_d:
            confirm_delete_button(
                "", key=f"row_del_{r['id']}",
                on_confirm=lambda rid=r["id"]: _delete_run(rid),
                message=f"Delete run `{r['id']}` and its artifacts?",
            )

st.divider()
_options = [r["id"] for r in rows]
_default_pick = (
    st.session_state.get("inspect_pick")
    or current_run_id()
)
if _default_pick not in _options:
    _default_pick = _options[0] if _options else None
sel = st.selectbox(
    "Inspect / delete run",
    options=_options,
    index=(_options.index(_default_pick) if _default_pick in _options else 0),
    format_func=lambda i: f"{i} · {storage.display_kind(next((r['kind'] for r in rows if r['id']==i), ''))} · {next((r.get('method') or '—' for r in rows if r['id']==i), '')}",
    key="inspect_pick",
)
if sel:
    r = storage.get_run(sel)
    if r:
        col1, col2 = st.columns(2)
        with col1:
            st.write("**Metadata**")
            st.json({k: v for k, v in r.items() if k != "params_json"})
        with col2:
            st.write("**Params**")
            try:
                st.json(json.loads(r.get("params_json") or "{}"))
            except Exception:  # noqa: BLE001
                st.code(r.get("params_json") or "")
        cols = st.columns(4)
        with cols[0]:
            if st.button("Status", icon=":material/pending:",
                          key=f"go_status_{sel}"):
                # Propagate the local selectbox pick — `st.switch_page`
                # resets st.query_params, so persist via shared state and
                # the 1.58 `query_params` kwarg.
                set_current_run_id(sel)
                st.switch_page("pages/7_Status.py",
                               query_params={"run_id": sel})
        with cols[1]:
            if st.button("Results", icon=":material/insights:",
                          key=f"go_results_{sel}"):
                set_current_run_id(sel)
                st.switch_page("pages/8_Results.py",
                               query_params={"run_id": sel})
        with cols[2]:
            if r["status"] == "running" and st.button("Cancel", key=f"cancel_{sel}"):
                if workers.cancel(sel):
                    storage.update_run(sel, status="cancelled")
                    st.rerun()
        with cols[3]:
            confirm_delete_button(
                "Delete this run", key=f"del_{sel}",
                on_confirm=lambda rid=sel: _delete_run(rid),
                message=f"Delete run `{sel}` and its artifacts?",
            )
