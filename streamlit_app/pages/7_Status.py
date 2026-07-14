"""Status — run progress + streaming logs (DB-synced)."""
from __future__ import annotations

import streamlit as st

from lib import cluster, hwinfo, launcher, storage, theme, tour, workers
from lib.manual import help_expander
from lib.state import current_run_id, set_current_run_id

st.set_page_config(page_title="Status · SHARC-Orbit", page_icon=":material/pending:", layout="wide")
theme.inject()

st.title("Run status")
help_expander("status")
tour.maybe_render("status")

qp = st.query_params
run_id = qp.get("run_id") or current_run_id()
if run_id:
    set_current_run_id(run_id)
if not run_id:
    runs = storage.list_runs(limit=50)
    if not runs:
        st.info("No runs registered yet.")
        st.stop()
    run_id = st.selectbox(
        "Run", options=[r["id"] for r in runs],
        format_func=lambda i: f"{i} · {storage.display_kind(next((r['kind'] for r in runs if r['id']==i), ''))}",
    )

import html as _html

# Slider controls live OUTSIDE the fragment so they don't auto-refresh.
st.subheader("Logs")
col_fs, col_n = st.columns([2, 2])
with col_fs:
    st.slider(
        "Log font size (px)", min_value=8, max_value=24,
        value=int(st.session_state.get("log_font_px", 11)),
        step=1, key="log_font_px",
    )
with col_n:
    st.slider(
        "Lines shown", min_value=50, max_value=2000,
        value=int(st.session_state.get("log_tail_n", 300)),
        step=50, key="log_tail_n",
    )


@st.fragment(run_every=2)
def _status_block() -> None:
    """Re-renders every 2s: status pill + progress + logs."""
    launcher.sync_handle_to_db(run_id)
    cur = storage.get_run(run_id)
    if not cur:
        st.error(f"Run {run_id} not found.")
        return

    status_color = {
        "pending": "warn", "running": "warn", "success": "ok",
        "failed": "error", "cancelled": "warn",
    }.get(cur["status"], "warn")
    st.markdown(theme.pill(cur["status"].upper(), status_color), unsafe_allow_html=True)
    st.progress(min(1.0, max(0.0, (cur.get("progress_pct") or 0) / 100.0)))
    st.caption(
        f"run_id `{cur['id']}` · type `{storage.display_kind(cur['kind'])}` · method `{cur.get('method') or '—'}`"
        f" · progress {(cur.get('progress_pct') or 0):.1f}%"
    )
    if cur.get("error_message"):
        st.error(cur["error_message"])

    # Live resource utilization — only while the run is active. Once it
    # reaches a terminal state we stop probing entirely (no more CPU sampling
    # or Ray-node polling every 2 s).
    if cur["status"] == "running":
        import time as _time
        loc = hwinfo.local_utilization()  # cheap, no Ray — every tick
        if loc.get("cpu_percent") is not None or loc.get("mem_percent") is not None:
            _gb = 1024 ** 3
            rc1, rc2 = st.columns(2)
            rc1.metric("CPU (host)", f"{loc.get('cpu_percent', 0):.0f}%")
            mem_lbl = f"{loc.get('mem_percent', 0):.0f}%"
            if loc.get("mem_used_bytes") and loc.get("mem_total_bytes"):
                mem_lbl += f"  ({loc['mem_used_bytes'] / _gb:.1f}/{loc['mem_total_bytes'] / _gb:.1f} GB)"
            rc2.metric("Memory (host)", mem_lbl)

        # Cluster node probe — the UI process must be connected to Ray to
        # see the workers (the run executes in a separate worker subprocess
        # that holds its own connection). Connect best-effort when a cluster
        # is configured, then probe. Throttle to ~10 s: the probe goes over
        # the Ray client channel, so we don't compete with the job's traffic.
        nodes = st.session_state.get("_status_nodes_cache")
        last = float(st.session_state.get("_status_nodes_ts", 0.0))
        now = _time.time()
        if now - last >= 10.0:
            try:
                if cluster.load().get("mode", "standalone") != "standalone":
                    cluster.ensure_init()  # idempotent; bounded 8 s timeout
                nodes = hwinfo.cluster_utilization()
            except Exception:  # noqa: BLE001
                nodes = []
            st.session_state["_status_nodes_cache"] = nodes
            st.session_state["_status_nodes_ts"] = now
        if nodes:
            st.caption("Cluster workers (live, ~10 s)")
            st.dataframe(
                [
                    {
                        "node": n.get("hostname") or n.get("address"),
                        "CPU %": n.get("cpu_percent"),
                        "Mem %": n.get("mem_percent"),
                    }
                    for n in nodes
                ],
                hide_index=True, width="stretch",
            )
    else:
        # Drop any cached node telemetry once the run is no longer active.
        st.session_state.pop("_status_nodes_cache", None)
        st.session_state.pop("_status_nodes_ts", None)

    handle = workers.get(run_id)
    if handle is None:
        # The handle is discarded right after the final DB sync (and lost on
        # UI restart) — fall back to the log tail persisted on disk so the
        # last output (including errors) stays visible.
        from lib import RUNS_DIR
        log_path = RUNS_DIR / run_id / "worker.log"
        if log_path.exists():
            try:
                disk_tail = log_path.read_text(encoding="utf-8").splitlines()
            except OSError:
                disk_tail = []
            disk_tail = disk_tail[-int(st.session_state.get("log_tail_n", 300)):]
            fs = int(st.session_state.get("log_font_px", 11))
            style = f"font-size:{fs}px;line-height:1.35"
            lines_html = "".join(
                f"<div style='{style}'>{_html.escape(line) or '&nbsp;'}</div>"
                for line in disk_tail
            ) or f"<div style='{style}'>(empty log)</div>"
            st.caption("Worker finished — showing the log tail persisted in worker.log.")
            st.markdown(
                f"<div class='so-log' style='{style};'>{lines_html}</div>",
                unsafe_allow_html=True,
            )
        else:
            st.info(
                "No live worker handle for this run on this Streamlit session "
                "(restart of the UI loses the in-memory subprocess registry; "
                "the run keeps writing to data/runs/<run_id>/ on disk)."
            )
        return
    workers.drain(handle)
    tail = handle.logs[-int(st.session_state.get("log_tail_n", 300)):]
    fs = int(st.session_state.get("log_font_px", 11))
    style = f"font-size:{fs}px;line-height:1.35"
    if not tail:
        lines_html = f"<div style='{style}'>(waiting for output)</div>"
    else:
        # inline font-size on EVERY child so no global CSS rule overrides it
        lines_html = "".join(
            f"<div style='{style}'>{_html.escape(line) or '&nbsp;'}</div>"
            for line in tail
        )
    st.markdown(
        f"<div class='so-log' style='{style};'>"
        f"{lines_html}</div>",
        unsafe_allow_html=True,
    )
    if handle.finished:
        if handle.return_code == 0:
            st.success(f"Worker exited cleanly (code {handle.return_code}).")
        else:
            st.error(f"Worker exited with code {handle.return_code}.")


_status_block()
run = storage.get_run(run_id)  # for the buttons below (cancel)

if not run:
    # Stale pointer to a deleted run (persisted selection or query param).
    # Clear it so reloading the page stops erroring, and offer a way out.
    set_current_run_id(None)
    if "run_id" in st.query_params:
        del st.query_params["run_id"]
    st.divider()
    st.warning(
        f"Run `{run_id}` no longer exists (it was deleted). "
        "Open the Runs page to pick another."
    )
    if st.button("Go to Runs", icon=":material/list:"):
        st.switch_page("pages/6_Runs.py")
    st.stop()

st.divider()
col1, col2 = st.columns(2)
with col1:
    if run["status"] == "running" and st.button("Cancel run", icon=":material/stop_circle:"):
        if workers.cancel(run_id):
            storage.update_run(run_id, status="cancelled")
            st.warning("Cancel requested.")
            st.rerun()
        else:
            st.error("Could not cancel (no live handle or already finished).")
with col2:
    if st.button("Results", icon=":material/insights:",
                  key=f"status_results_{run_id}"):
        # `st.switch_page` resets st.query_params — persist the pick (the
        # run may come from the local selectbox above) via shared state and
        # the 1.58 `query_params` kwarg, so Results opens THIS run.
        set_current_run_id(run_id)
        st.switch_page("pages/8_Results.py",
                       query_params={"run_id": run_id})
