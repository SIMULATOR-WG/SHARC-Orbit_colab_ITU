"""SHARC-Orbit — Streamlit UI entrypoint.

Run with:
    streamlit run streamlit_app/app.py

Uses ``st.navigation`` so the sidebar label of the entrypoint is "SHARC-Orbit"
instead of the filename. Each page under ``pages/`` is registered explicitly.
"""
from __future__ import annotations

import streamlit as st

from lib import storage, theme, engine, git_revision
from lib.manual import help_expander

st.set_page_config(
    page_title="SHARC-Orbit",
    # An emoji favicon is rendered by the frontend as an inline `data:image/svg+xml`
    # URI. A `:material/…:` icon would instead be fetched from
    # fonts.gstatic.com — the only other outbound call the UI used to make.
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="expanded",
)
theme.inject()
storage.init_db()


# ─── Home page (rendered as a function, registered via st.Page) ─────────────
def home() -> None:
    _rev = git_revision()
    _ver = str(_rev["label"])
    _on_main = str(_rev["branch"]) in ("main", "master")
    tcol, vcol = st.columns([4, 2])
    with tcol:
        st.title(":material/satellite_alt: SHARC-Orbit")
        st.caption(
            "Python-only UI · ITU-R S.1503-4 (single-entry) · "
            "Resolution 76 (aggregate, methods under study)"
        )
    with vcol:
        st.markdown(
            f"<div style='text-align:right;padding-top:8px'>"
            f"{theme.pill(_ver, 'ok' if _on_main and not _rev['dirty'] else 'warn')}"
            f"</div>",
            unsafe_allow_html=True,
        )
    help_expander("home")
    st.warning(
        "**System under development** — currently intended for experimentation "
        "only. Results must not be used for normative or decision-making purposes.",
        icon=":material/warning:",
    )
    st.markdown(
        """
Python-only user interface for the EPFD aggregation simulator. Single-entry
follows **ITU-R S.1503-4**. Aggregation methods are under study (alternatives
beyond a single ITU-R recommendation). The numerical engine (`src/`) is
reused unchanged.

Use the **left sidebar** to navigate between pages.
        """
    )

    with st.container(border=True):
        st.subheader("Engine status")
        info = engine.engine_health()
        if info["ok"]:
            st.markdown(theme.pill("engine OK", "ok"), unsafe_allow_html=True)
            st.markdown(
                f"<div class='so-mono'>engine root: {info['engine_root']}</div>"
                "<div style='height:14px'></div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(theme.pill("engine UNAVAILABLE", "error"), unsafe_allow_html=True)
            st.error(info.get("error", "Unknown engine error"))
            st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)

    st.subheader("Workflow")
    cols = st.columns(3)
    with cols[0]:
        st.markdown("**1. Register filings**")
        st.markdown(
            "Upload an SRS `.mdb` and its PFD mask `.mdb` on the **Upload** page."
        )
    with cols[1]:
        st.markdown("**2. Configure & run**")
        st.markdown(
            "Pick systems on **Single-entry** (one system) or **Aggregate** "
            "(multi-system aggregation) to launch runs."
        )
    with cols[2]:
        st.markdown("**3. Analyse**")
        st.markdown(
            "Inspect **Runs**, follow **Status**, then open **Results** "
            "for CCDF, normative percentiles and EPFD timelines."
        )

    st.divider()
    st.subheader("Conformance notes")
    st.markdown(
        """
* The app does not download catalogues, filings or masks. You supply the files.
* No authentication or client-server access control.
* No code in other languages or proprietary tech.
* Open source on GitHub: `SIMULATOR-WG/SHARC-Orbit`.
* Run history is the local database. Results can export an XLSX of the run.
        """
    )

    # ── Footer ─────────────────────────────────────────────────────────
    from datetime import datetime
    _year = datetime.now().year
    _start = 2026
    _year_range = f"{_start}" if _year <= _start else f"{_start}–{_year}"
    st.markdown(
        f"""
<style>
.so-footer {{
    margin-top: 28px;
    padding: 14px 0 6px;
    border-top: 1px solid rgba(255,255,255,0.08);
    font-size: 11px;
    color: #6b7280;
    line-height: 1.55;
    text-align: center;
}}
.so-footer a {{ color: #94a3b8; text-decoration: none; }}
.so-footer a:hover {{ color: var(--so-accent); }}
.so-footer .so-row {{ margin: 2px 0; }}
.so-footer code {{ font-size: 11px; color: #94a3b8; background: transparent; }}
</style>
<div class="so-footer">
    <div class="so-row">
        <strong>SHARC-Orbit</strong> · {_ver} · ITU-R S.1503-4 · Resolution 76
        · Python-only Streamlit UI
    </div>
    <div class="so-row">
        Engine reused unchanged from <code>src/</code> ·
        Storage: local SQLite + filesystem ·
        Optional distributed processing via Ray
    </div>
    <div class="so-row">
        Source · <a href="https://github.com/SIMULATOR-WG/SHARC-Orbit"
                    target="_blank">github.com/SIMULATOR-WG/SHARC-Orbit</a>
        · Issues &amp; feedback on the same repository
    </div>
    <div class="so-row" style="margin-top:8px;">
        © {_year_range} SHARC-Orbit contributors · Released under an
        open-source license (see <code>LICENSE</code>)
    </div>
</div>
        """,
        unsafe_allow_html=True,
    )


# ─── Register pages with Material Symbols (clean line icons) ────────────────
# Grouped, because a flat list of seventeen entries reads as a wall. Passing a
# mapping to ``st.navigation`` renders one labelled block per key; the empty key
# is the "no heading" group and keeps the home page at the top, above the
# headings. Note that sidebar sections are labels, not disclosure widgets: the
# Streamlit 1.58 API makes a group collapsible only with ``position="top"``.
PAGES = {
    "": [
        st.Page(home,                       title="SHARC-Orbit",   icon=":material/home:",
                default=True),
    ],
    "Input": [
        st.Page("pages/1_Upload.py",        title="Upload",        icon=":material/upload:"),
        st.Page("pages/2_Uploads.py",       title="Uploads",       icon=":material/folder_open:"),
        st.Page("pages/E_Manual_System.py", title="Manual system", icon=":material/edit_note:",
                url_path="manual_system", visibility="hidden"),
        st.Page("pages/F_Mask_Generator.py", title="Mask generator", icon=":material/auto_fix_high:",
                url_path="mask_generator", visibility="hidden"),
    ],
    "Inspect": [
        st.Page("pages/B_Mask_Viewer.py",   title="Mask viewer",   icon=":material/blur_on:",
                url_path="mask_viewer"),
        st.Page("pages/C_Constellation.py", title="Constellation", icon=":material/public:",
                url_path="constellation"),
    ],
    "Examine": [
        st.Page("pages/3_Single_entry.py",  title="Single-entry",  icon=":material/looks_one:"),
        st.Page("pages/4_Aggregate.py",     title="Aggregate",     icon=":material/grid_view:"),
        st.Page("pages/H_National_Occupancy.py", title="National occupancy",
                # Vertical bars under a radio arc: occupancy is "how much of the
                # spectrum is taken", not "there is a tower somewhere".
                # The URL is user-visible — it sits in the address bar and in any
                # bookmark — so it may not name one country either.
                icon=":material/key_visualizer:", url_path="national_occupancy"),
    ],
    "Runs & results": [
        st.Page("pages/5_Launcher.py",      title="Launcher",      icon=":material/play_circle:",
                visibility="hidden"),
        st.Page("pages/9_Campaign.py",      title="Campaign",      icon=":material/dashboard:",
                visibility="hidden"),
        st.Page("pages/6_Runs.py",          title="Runs",          icon=":material/list:"),
        st.Page("pages/7_Status.py",        title="Status",        icon=":material/pending:"),
        st.Page("pages/8_Results.py",       title="Results",       icon=":material/insights:"),
    ],
    "System": [
        st.Page("pages/0_Cluster.py",       title="Cluster",       icon=":material/hub:"),
        st.Page("pages/A_Help.py",          title="Help",          icon=":material/help_outline:"),
    ],
}

nav = st.navigation(PAGES)
nav.run()
