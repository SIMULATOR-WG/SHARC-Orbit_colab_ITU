"""Campaign — multi-run dashboard + XLSX export."""
from __future__ import annotations

import streamlit as st
import pandas as pd

from lib import exports, launcher, storage, theme, tour
from lib.manual import help_expander
from lib.state import set_current_run_id

st.set_page_config(page_title="Campaign · SHARC-Orbit", page_icon=":material/dashboard:", layout="wide")
theme.inject()

st.title("Campaign dashboard")
help_expander("campaign")
tour.maybe_render("campaign")
st.caption("Aggregated view of all runs in a campaign + XLSX export.")

launcher.sync_all_running()
campaigns = storage.list_campaigns(limit=200)
if not campaigns:
    st.info("No campaigns yet. Launch one on the **Launcher** page.")
    st.page_link("pages/5_Launcher.py", label="Launcher", icon=":material/play_circle:")
    st.stop()

qp = st.query_params
default_id = qp.get("campaign_id") if "campaign_id" in qp else campaigns[0]["id"]
sel = st.selectbox(
    "Campaign",
    options=[c["id"] for c in campaigns],
    index=max(0, next((i for i, c in enumerate(campaigns) if c["id"] == default_id), 0)),
    format_func=lambda i: next((f"{c['label']} ({c['id']})" for c in campaigns if c["id"] == i), i),
)
st.query_params["campaign_id"] = sel

cm = storage.get_campaign(sel)
runs = storage.list_runs(limit=500, campaign_id=sel)

st.subheader("Metadata")
st.json(cm)

st.subheader(f"Runs ({len(runs)})")
if runs:
    df = pd.DataFrame(
        [
            {
                "id": r["id"], "method": r.get("method"), "status": r["status"],
                "progress": f"{(r.get('progress_pct') or 0):.0f}%",
                "created_at (BRT)": storage.fmt_local(r["created_at"]),
                "finished_at (BRT)": storage.fmt_local(r.get("finished_at")),
            }
            for r in runs
        ]
    )
    st.dataframe(df, hide_index=True, width='stretch')

    cols = st.columns(2)
    with cols[0]:
        try:
            xlsx_bytes = exports.campaign_to_xlsx(campaign=cm, runs=runs)
            st.download_button(
                "Export XLSX",
                icon=":material/download:",
                data=xlsx_bytes,
                file_name=f"campaign_{sel}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"XLSX export failed: {exc} (install openpyxl)")
    with cols[1]:
        if st.button("Refresh", icon=":material/refresh:"):
            st.rerun()

    st.divider()
    st.subheader("Per-run quick links")
    for r in runs:
        col_a, col_b, col_c = st.columns([3, 1, 1])
        with col_a:
            st.write(f"`{r['id']}` · {r.get('method') or '—'} · {r['status']}")
        with col_b:
            if st.button("Status", icon=":material/pending:", key=f"st_{r['id']}"):
                # `st.switch_page` resets st.query_params — persist the pick
                # via shared state AND the 1.58 `query_params` kwarg so the
                # destination opens THIS run (not the fallback one).
                set_current_run_id(r["id"])
                st.switch_page("pages/7_Status.py",
                               query_params={"run_id": r["id"]})
        with col_c:
            if st.button("Results", icon=":material/insights:", key=f"re_{r['id']}"):
                set_current_run_id(r["id"])
                st.switch_page("pages/8_Results.py",
                               query_params={"run_id": r["id"]})
else:
    st.info("Campaign has no runs yet.")
