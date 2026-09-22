"""manual.py — per-page contextual help snippets.

Used by the ``help_expander(page_id)`` helper to drop a *Help on this page*
expander on the main workflow pages. Keeps copy in one place so the Help
page index and the per-page hints stay in sync.
"""
from __future__ import annotations

import streamlit as st


_HELP: dict[str, str] = {
    "upload": (
        "**Upload page**\n\n"
        "Register an SRS filing. Two-stage flow:\n\n"
        "1. **Files** — pick the SRS `.mdb` **and** the PFD mask `.mdb` "
        "give the filing a label. The MDB is copied into "
        "`streamlit_app/data/uploads/<id>/`.\n"
        "2. **Notices** — the page parses the MDB, lists every notice "
        "(`ntc_id`) and PFD mask found. Tick the notices you want to "
        "register as **systems** (each becomes a separate row in "
        "Uploads / pickable in Single-entry / Aggregate).\n\n"
        "One MDB can hold multiple notices; you can register them "
        "individually or all at once.\n\n"
        "The Step-2 label is **suggested from the SRS network/satellite "
        "name** + the **filing-name prefix** (set on the Uploads page) "
        "+ a timestamp — editable. The Uploads prefix box can also "
        "rename all existing filings at once."
    ),
    "uploads": (
        "**Uploads page**\n\n"
        "List of every registered filing + system. Three per-system "
        "panels surface what the MDB declared:\n\n"
        "* **Orbital parameters** — number of planes, satellites per "
        "plane, semi-major axis / altitude / perigee / apogee / "
        "eccentricity, inclination, RAAN, argument of perigee, period, "
        "precession, sun-synch / station-keeping flags. Read directly "
        "from the SRS `orbit` table.\n"
        "* **Operating frequency bands** — masks (`mask_info` table) "
        "with PFD / EIRP / Other categories + groups (`grp` table) "
        "with Tx/Rx side, beam, min elevation.\n"
        "* **View mask** buttons — open a PFD mask in the **Mask Viewer**.\n\n"
        "Selection picker at the top is shared with Single-entry / "
        "Mask Viewer via the cross-page state, so it remembers your "
        "last pick across reloads. The lower section lets you inspect "
        "/ re-scan / delete a specific filing."
    ),
    "single_entry": (
        "**Single-entry page (ITU-R S.1503-4)**\n\n"
        "Runs the full single-system pipeline on **one** system. "
        "Pick the geometry in section 2:\n\n"
        "1. **S.1503-4 WCGA** — Worst-case geometry algorithm per "
        "§D.3.1: latitude sweep + (θ, φ) grid + binary search on "
        "α₀ / ε₀ boundaries, then EPFD↓ at the WCG. Select one or more "
        "countries to restrict the ES domain (**territorial WCGA**) and "
        "enable the RAAN (Ω) sweep (auto per orbit, §D4.6.1).\n"
        "2. **Defined geometry** — skip the WCGA; run EPFD↓ at one "
        "ES / GSO coordinate.\n"
        "3. **ES×GSO grid (no WCGA)** — same algorithm as Aggregate "
        "method 2 on this filing: EPFD↓ at every grid point, envelope "
        "= worst CCDF. Optional country filter. Not a WCGA. Filings "
        "with MIN_DURATION ≠ 0 are not supported on this path.\n\n"
        "Then **compliance** — CCDF compared to Article 22 §22.5 "
        "limits.\n\n"
        "**Retractable sections** (collapsed) expose WCG search "
        "(`s1503_step`, `gso_longitude_mode`, `alpha_method`), defined "
        "geometry, the dual time step, and **Orbital dynamics** — "
        "station keeping (`Wdelta`), artificial precession, and "
        "precession-from-SRS-MDB (§D6.3)."
    ),
    "country_single_entry": (
        "**Territorial WCGA** now lives on **Single-entry**.\n\n"
        "Pick **Geometry → S.1503-4 WCGA** and select one or more "
        "countries. Empty selection = world-wide §D.3.1 WCGA. "
        "Non-empty = territorial ES domain + RAAN (Ω) sweep."
    ),
    "aggregate": (
        "**Aggregate page (Resolution 76 studies)**\n\n"
        "Combines **two or more** systems and runs one of four "
        "aggregation methods. Each item in the radio describes its "
        "strategy and cost; the box below the radio expands with a "
        "step-by-step explanation when you pick a method.\n\n"
        "Pick `method_1` for the conservative per-WCG convolution, "
        "`method_2` for the grid sweep, `method_3` for the "
        "joint megaconstellation sim, or `method_4` for the per-WCG "
        "audit. See the [Help page](./Help) for the full table.\n\n"
        "The WCG search / Time step / **Orbital dynamics** (station "
        "keeping, precession) sections apply per filing, same as "
        "Single-entry.\n\n"
        "The **Convolution tail (S.1588 Annex 1 §1)** section sits right "
        "after *Orbital dynamics* in the form. The *Tail floor* field is "
        "always editable but only used when the checkbox is ticked. It "
        "applies to the convolving methods; reloading "
        "a run's config from the **Runs** page restores the truncation "
        "settings too."
    ),
    "launcher": (
        "**Launcher page (campaign mode)**\n\n"
        "Launches **multiple methods on the same set of systems** as a "
        "single campaign. Useful when you want to compare aggregation "
        "approaches under identical inputs.\n\n"
        "Mirrors **every Aggregate option** — WCG search, time step, "
        "**Orbital dynamics** (station keeping / precession) and the "
        "method-2 grid params (grid / GSO step, max geometries, country "
        "filter). Set once → applied to all selected methods. The form is "
        "**persisted** across visits (same defaults as Aggregate).\n\n"
        "All child runs share a `campaign_id` and roll up in the "
        "**Campaign** dashboard (with XLSX export)."
    ),
    "runs": (
        "**Runs page**\n\n"
        "Local history of every run (DB-synced). Each row's **actions** "
        "column has four shortcut buttons:\n\n"
        "- :material/insights: → open **Results** for that run.\n"
        "- :material/pending: → open **Status** for that run.\n"
        "- :material/settings_backup_restore: → **reload this study's "
        "config** into the matching form (Single-entry / Aggregate), "
        "pre-filled, so you can re-run with the same settings. Warns if "
        "the original filing was deleted.\n"
        "- :material/delete: → **delete** that run + its artifacts "
        "(asks to confirm).\n\n"
        "Click the `id` button to pre-select the run in *Inspect / "
        "delete* below. The **Maintenance** toolbar at the top bulk-"
        "deletes runs by status — failed / cancelled / success — or "
        "**ALL runs** (also confirmed)."
    ),
    "status": (
        "**Status page**\n\n"
        "Live worker log + progress bar. The fragment polls every 2 s "
        "and prints the raw engine output (Numba progress bars are "
        "split on `\\r` so they don't pile up into one giant line).\n\n"
        "While the run is active it also shows **host CPU%/memory** and, "
        "when distributed, **per-worker** utilisation (probed ~10 s). "
        "Probing stops once the run finishes. Tail / log font size are "
        "persisted in session state."
    ),
    "results": (
        "**Results page**\n\n"
        "- **CCDF chart** — main aggregate curve + per-WCG (method_4) "
        "or per-(point, system) (method_2) overlays. Article 22 / "
        "Resolution 76 limit curves drawn dashed/dotted.\n"
        "- **Normative percentiles** — bar chart at the Resolution 76 "
        "thresholds (100 %, 10 %, 1 %, 0.1 %, …). When the S.1588 "
        "convolution-tail truncation is active, percentiles below the "
        "truncation floor have no reliable value and show as "
        "**n/a (below truncation floor)**.\n"
        "- **Globe** — ES + GSO + grid markers in an orthographic "
        "projection. Click a point to pre-select the matching geometry "
        "in the *Geometry* picker below; press **Show CCDF** for a "
        "dialog with that point's CCDF + limit overlays.\n"
        "- **EPFD timeline** (single-entry only) — per-sample EPFD↓ "
        "trace from the simulation.\n"
        "- **Compare external MDB(s)** — upload SHARC results `.mdb` "
        "file(s) to overlay their reference CCDF curves (table `cdf`) "
        "on the chart."
    ),
    "campaign": (
        "**Campaign page**\n\n"
        "Aggregated view of every run sharing the selected `campaign_id`. "
        "Lists each child run with status/progress, then exports a "
        "multi-sheet XLSX (`campaign`, `runs`, `params`) capturing the "
        "campaign for archival."
    ),
    "mask_viewer": (
        "**Mask Viewer**\n\n"
        "Interactive visualiser for an ITU-R S.1503-4 PFD mask.\n\n"
        "* Open from **Uploads** → *View mask N · freq* button, or "
        "select a mask directly via the picker at the top of this page.\n"
        "* Header metrics: mask id, type, shape, PFD value range.\n"
        "* Sliders use **real degrees** of the A and B axes (not "
        "indices) so STEAM-2 latitudes show `-55° … +55°` etc.\n"
        "* Heatmap toggles between *Uniform* (equal-width cells) and "
        "*Proportional* (real degrees on the x-axis).\n"
        "* Calculator computes PFD at any `(a, b, c)` triple using "
        "bilinear or trilinear interpolation (matches the engine's "
        "runtime behaviour).\n\n"
        "Only PFD masks are renderable. Mask coordinate conversion is "
        "not ported from the legacy HTML viewer — use the engine CLI "
        "for that."
    ),
    "cluster": (
        "**Cluster page (optional)**\n\n"
        "Configure Ray distributed processing. **Standalone** already "
        "saturates this machine's cores (WCGA + EPFD pools). Switch to "
        "**Ray cluster** to spread across machines — both the aggregate "
        "sub-simulations AND the interior of a single heavy run (WCGA "
        "latitude sweep + EPFD time-chunks) fan out.\n\n"
        "When the head runs on this host the driver connects natively "
        "(GCS address), not the fragile `ray://` client. Attach workers "
        "with `ray start --address=<HEAD>:6379`. See the Help page for "
        "the multi-host walk-through."
    ),
    "national_occupancy": (
        "**National band occupancy**\n\n"
        "Survey of frequency occupancy in one country, from two kinds of "
        "catalogue the page holds:\n\n"
        "* **A national licensed catalogue** — optional: licensed stations and "
        "the sub-bands they hold. Upload your administration's table in the "
        "shape described under **Licensed-station table format** below. "
        "The page works without any of this; it then shows the filings "
        "alone.\n"
        "* **ITU full SRS** — the filings. The whole SRS is "
        "indexed, and the **country is a filter**, not a property of the "
        "index: each notice carries its notifying administration, its "
        "declared service area and the countries of its earth stations, "
        "so you choose the country on screen and can change it without "
        "re-indexing. A filing counts as operating in a country either "
        "because that administration notified it, or, under the looser "
        "rule, because it serves the country — directly, through its ITU "
        "Region (`XR1`/`XR2`/`XR3`) or worldwide (`XAA`). Symbols are the "
        "ITU's, not ISO: `B` is Brazil, `F` France, `D` Germany, `G` the "
        "United Kingdom.\n"
        "* **Full SRS** — paste the path to the BR IFIC ISO you already "
        "downloaded (or to `Databases/SRS_Data/SRS.mdb` extracted from "
        "it). The page pulls `SRS.mdb` out of the ISO with `xorriso` and "
        "indexes every notice in it. Wait until the ISO "
        "download has finished before indexing.\n\n"
        "Filter by country, source, orbit and RF band, pick systems, and the "
        "page draws the same shared-axis occupancy strips as "
        "**Aggregate**, plus the **common overlap** of the selection. "
        "This is a survey tool — it does not launch EPFD runs.\n\n"
        "**Bringing your own licensed catalogue.** The national catalogue is "
        "optional: the ITU side of the page works on its own. An "
        "administration that wants its licensed stations drawn beside the "
        "filings supplies one table in the documented shape — see "
        "**Licensed-station table format** below for the fields."
    ),
    "national_catalog_format": (
        "**Licensed-station table format**\n\n"
        "One CSV, or a zip containing it. **One row per station per "
        "sub-band**, not one row per station: a station with four sub-bands "
        "has four rows, and the page merges them.\n\n"
        "| Column | Meaning | Required | Example |\n"
        "| --- | --- | --- | --- |\n"
        "| station | Name of the licensed space station. Rows sharing a name "
        "become one system. | **yes** | `STARONE C3` |\n"
        "| freq_min_mhz | Lower edge of the sub-band, **in MHz**. | **yes** | "
        "`10700` |\n"
        "| freq_max_mhz | Upper edge of the sub-band, in MHz. | **yes** | "
        "`11700` |\n"
        "| direction | Which way the sub-band goes. | no, but without it "
        "every row counts as downlink | `downlink` / `uplink` |\n"
        "| operator | Licensee, shown under the station name. | no | "
        "`EMBRATEL` |\n"
        "| orbit | `GEO` or `NGEO`; drives the Orbit filter. | no | `GEO` |\n"
        "| orbital_position | Longitude or slot, shown on the row. | no | "
        "`70W` |\n"
        "| rf_band | The administration's own band label. | no | `Ku` |\n"
        "| station_id | Licence or station number. | no | `1234` |\n"
        "| valid_until | Licence validity, carried as provenance. | no | "
        "`2030-12-31` |\n\n"
        "**Details that bite.**\n\n"
        "* **Column names** may be in English as above, or the Portuguese "
        "names of the reference open-data table (`NomeEstacao_STEL_portal`, "
        "`MedFrequenciaInicialMHz_STEL_portal`, `Sentido_STEL_portal`, …). "
        "Matching is case-insensitive and also accepts a name that merely "
        "contains the one listed.\n"
        "* **Frequencies are MHz.** A file in GHz parses without complaint and "
        "draws bands a thousand times too narrow; the page checks the range "
        "and refuses a file whose highest frequency is under 1 000.\n"
        "* **Decimal separator** may be a dot or a comma: `1635,725` and "
        "`1635.725` are both read as 1 635.725 MHz.\n"
        "* **Delimiter** is detected, semicolon or comma. **Encoding** may be "
        "UTF-8 (with or without BOM), CP1252 or Latin-1.\n"
        "* **Direction** is matched by word, not by language: `subida` / "
        "`uplink` / `Earth-to-space` / `E-S` / `ascendente` all mean uplink, "
        "and the descending equivalents mean downlink. A word in neither list "
        "is filed as downlink and reported, so a column the page did not "
        "understand is visible rather than silent.\n"
        "* Columns the page does not use are ignored, so an export with extra "
        "fields is fine."
    ),
}


def help_expander(page_id: str, *, label: str = "Help on this page",
                  extra: "tuple[str, ...] | None" = None) -> None:
    """Drop a collapsed expander on the page with its contextual snippet.

    ``extra`` names further ``_HELP`` entries to fold in under the first, each
    in its own nested expander — for reference material a page needs at hand
    but should not open with, such as an input-file contract.

    Silently no-op when ``page_id`` is unknown; unknown ``extra`` keys are
    skipped the same way.
    """
    text = _HELP.get(page_id)
    if not text:
        return
    with st.expander(label, expanded=False, icon=":material/help_outline:"):
        st.markdown(text)
        for key in (extra or ()):
            body = _HELP.get(key)
            if not body:
                continue
            title = body.lstrip().split("\n", 1)[0].strip("* ")
            with st.expander(title, expanded=False):
                st.markdown(body)
        st.page_link(
            "pages/A_Help.py",
            label="Open full manual",
            icon=":material/menu_book:",
        )
