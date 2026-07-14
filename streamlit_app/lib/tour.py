"""tour.py — stateful guided tour across the workflow pages.

A true element-spotlight tour (intro.js style) is fragile in Streamlit:
the DOM re-renders every interaction and components live in iframes, so
CSS-selector targeting breaks. Instead this tour is driven by
``st.session_state`` + native page navigation:

* ``start()`` arms the tour and jumps to the first step's page.
* each workflow page calls ``maybe_render("<page_id>")`` near the top;
  when the tour is active *and* the current step belongs to that page,
  a callout banner with the step's explanation + Next/Skip is shown.
* ``Next`` advances the step and ``st.switch_page``-s to the next page.

So the explanation appears contextually on the real page it describes,
and the user is walked through the end-to-end flow.
"""
from __future__ import annotations

from typing import Any

import streamlit as st

# Ordered walkthrough of the end-to-end flow. ``page`` is the path passed to
# st.switch_page; ``page_id`` is matched against each page's maybe_render call.
STEPS: list[dict[str, str]] = [
    {
        "page_id": "upload",
        "page": "pages/1_Upload.py",
        "title": "Register a filing",
        "message": (
            "Everything starts here. Upload an **SRS `.mdb`** (the filing) and "
            "its **PFD mask `.mdb`**, then pick the notice(s) to register as "
            "**systems**. One MDB can hold several notices — each becomes a "
            "row you can run later.\n\n"
            "👉 Register at least one system, then hit **Next**."
        ),
    },
    {
        "page_id": "uploads",
        "page": "pages/2_Uploads.py",
        "title": "Inspect what was declared",
        "message": (
            "Every registered system shows up here. Expand a system to read "
            "its **orbital parameters** (planes, sats/plane, altitude, "
            "inclination, station-keeping / precession flags) and its "
            "**frequency bands / masks** — all read straight from the SRS.\n\n"
            "The picker at the top is shared with Single-entry / Aggregate."
        ),
    },
    {
        "page_id": "single_entry",
        "page": "pages/3_Single_entry.py",
        "title": "Flow A — Single-entry (one system)",
        "message": (
            "Runs the full **ITU-R S.1503-4** pipeline on one system: "
            "**WCGA** (worst-case geometry) → **EPFD↓** time simulation → "
            "**compliance** vs Article 22.\n\n"
            "Retractable sections expose WCG search, time step, manual WCG and "
            "orbital dynamics (station keeping / precession). Empty fields = "
            "engine defaults."
        ),
    },
    {
        "page_id": "aggregate",
        "page": "pages/4_Aggregate.py",
        "title": "Flow B — Aggregate (many systems)",
        "message": (
            "Combines **two or more** systems via one of five Resolution 76 "
            "methods (per-filing convolution, geometry grid, joint "
            "megaconstellation, per-WCG sweep …). Pick the method and the same "
            "WCG / time-step / orbital-dynamics options apply per filing.\n\n"
            "Heavy runs fan out across the Ray cluster automatically."
        ),
    },
    {
        "page_id": "launcher",
        "page": "pages/5_Launcher.py",
        "title": "Campaign launcher — many methods at once",
        "message": (
            "Runs **several aggregation methods on the same systems** as one "
            "reproducible campaign. It mirrors every Aggregate option (WCG "
            "search, time step, orbital dynamics, grid) — set once, applied to "
            "all selected methods. The form is **persisted** between visits.\n\n"
            "Each child run shares a `campaign_id` and rolls up in the Campaign "
            "dashboard."
        ),
    },
    {
        "page_id": "status",
        "page": "pages/7_Status.py",
        "title": "Watch the run",
        "message": (
            "Live status, progress and streaming logs (refresh every 2 s). "
            "While a run is active you also see **host CPU/memory** and, when "
            "distributed, **per-worker** utilisation. Cancel from here if "
            "needed."
        ),
    },
    {
        "page_id": "results",
        "page": "pages/8_Results.py",
        "title": "Read the results",
        "message": (
            "CCDF (log-y), normative percentiles, time series and geometry on "
            "the globe. You can **upload reference results `.mdb`** files to "
            "overlay their CCDF curves for comparison."
        ),
    },
    {
        "page_id": "campaign",
        "page": "pages/9_Campaign.py",
        "title": "Campaign dashboard",
        "message": (
            "Aggregated view of every run sharing a `campaign_id`: per-child "
            "status / progress / metrics, and a one-click **XLSX export** "
            "(campaign + runs + params sheets) for archival. Pick a child run "
            "to jump to its Status/Results."
        ),
    },
    {
        "page_id": "cluster",
        "page": "pages/0_Cluster.py",
        "title": "(Optional) Distribute across machines",
        "message": (
            "Standalone uses all local cores. To scale further, start a Ray "
            "head here and attach worker nodes — heavy WCGA latitude sweeps "
            "and EPFD time-chunks then fan out across every node.\n\n"
            "That's the whole flow. **Finish** to end the tour."
        ),
    },
]


def is_active() -> bool:
    return bool(st.session_state.get("tour_active"))


def start() -> None:
    """Arm the tour and jump to the first step."""
    st.session_state["tour_active"] = True
    st.session_state["tour_step"] = 0
    st.switch_page(STEPS[0]["page"])


def stop() -> None:
    st.session_state["tour_active"] = False


def _step_index() -> int:
    return int(st.session_state.get("tour_step", 0))


def maybe_render(page_id: str) -> None:
    """Render the tour callout if the active step belongs to this page."""
    if not is_active():
        return
    i = _step_index()
    if i < 0 or i >= len(STEPS):
        stop()
        return
    step = STEPS[i]
    n = len(STEPS)

    if step["page_id"] != page_id:
        # Tour is on another step — gently point the user back on track.
        st.info(
            f"🧭 Guided tour is on step {i + 1}/{n} "
            f"(**{step['title']}**). Use the sidebar to open that page, "
            "or skip the tour below.",
            icon=":material/tour:",
        )
        if st.button("Skip tour", key=f"tour_skip_off_{page_id}_{i}"):
            stop()
            st.rerun()
        return

    with st.container(border=True):
        st.markdown(f"#### 🧭 Tour · step {i + 1}/{n} — {step['title']}")
        st.markdown(step["message"])
        c1, c2, _ = st.columns([1, 1, 5])
        if i + 1 < n:
            if c1.button("Next ▶", key=f"tour_next_{i}", type="primary"):
                st.session_state["tour_step"] = i + 1
                st.switch_page(STEPS[i + 1]["page"])
        else:
            if c1.button("Finish ✓", key="tour_finish", type="primary"):
                stop()
                st.rerun()
        if i > 0:
            if c2.button("◀ Back", key=f"tour_back_{i}"):
                st.session_state["tour_step"] = i - 1
                st.switch_page(STEPS[i - 1]["page"])
        else:
            if c2.button("Skip ✕", key="tour_skip_first"):
                stop()
                st.rerun()


def overview_md() -> str:
    """Markdown overview of the flows, for the Help / entry page."""
    lines = ["**Guided tour — the end-to-end flow**", ""]
    for k, s in enumerate(STEPS, 1):
        lines.append(f"{k}. **{s['title']}**")
    return "\n".join(lines)
