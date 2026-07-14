"""Help — in-app manual covering the SHARC-Orbit workflow.

Single-file Streamlit page that reuses the same descriptive text shown
inline on the workflow pages (Single-entry, Aggregate, Cluster, etc.)
plus a glossary and troubleshooting matrix.
"""
from __future__ import annotations

import streamlit as st

from lib import theme, tour

st.set_page_config(page_title="Help · SHARC-Orbit",
                    page_icon=":material/help_outline:", layout="wide")
theme.inject()

st.title("Help")
st.caption("In-app manual — workflow, methods, glossary, troubleshooting.")

# ─── Guided tour entry point ─────────────────────────────────────────────────
with st.container(border=True):
    st.markdown(tour.overview_md())
    st.caption(
        "Walks you page-by-page through the whole flow, with an explanation "
        "on each page. You can leave at any step."
    )
    if tour.is_active():
        st.info("Tour in progress — open the highlighted page to continue.")
        if st.button("Stop tour", icon=":material/stop_circle:"):
            tour.stop()
            st.rerun()
    elif st.button("Start guided tour", type="primary", icon=":material/tour:"):
        tour.start()

# ─── Table of contents ──────────────────────────────────────────────────────

toc = st.container(border=True)
toc.markdown(
    """
**Contents**

1. [Quickstart](#quickstart)
2. [Workflow](#workflow)
3. [Pages](#pages)
4. [Aggregation methods](#aggregation-methods)
5. [Mask Viewer](#mask-viewer)
6. [Cluster (distributed processing)](#cluster-distributed-processing)
7. [Timezone (BRT) display](#timezone-brt-display)
8. [Glossary](#glossary)
9. [Troubleshooting](#troubleshooting)
    """
)

# ─── Quickstart ─────────────────────────────────────────────────────────────

st.subheader("Quickstart", anchor="quickstart")
st.markdown(
    """
1. **Upload** an SRS `.mdb` **and** the PFD mask `.mdb` under **Upload**.
   Each notice (`ntc_id`) inside the MDB becomes an independent **system**.
2. Open **Single-entry** to run ITU-R S.1503-4 on **one** system, or
   **Aggregate** to run multi-system aggregation studies on two or more.
3. **Status** streams the worker log + progress bar in real time.
4. When `success`, **Results** plots the CCDF, the regulatory limit
   curves (Article 22, Resolution 76) and the geometries on the globe.
5. **Campaign** bundles a set of runs into a reproducible XLSX report.

The Cluster page is **optional** — only relevant when you want to spread
work across more than one machine.
    """
)

# ─── Workflow diagram ───────────────────────────────────────────────────────

st.subheader("Workflow", anchor="workflow")
st.markdown(
    """
```
Upload  ─►  Uploads  ─►  Single-entry  ─►  Status  ─►  Results
                       └► Aggregate    ─►  Status  ─►  Results
                       └► Launcher (campaign of N methods)
                                       ─►  Status  ─►  Results
                                                     └► Campaign (XLSX)
```

Engine state lives in `streamlit_app/data/`:
- `uploads/<upload_id>/<file>.mdb` — your SRS / mask files
- `runs/<run_id>/{sim_data.json, summary.json, params.json, log}` — per-run artifacts
- `sharc_orbit.db` — SQLite with uploads/systems/runs/campaigns
- `cluster.json` — persistent Ray runtime config
    """
)

# ─── Pages ──────────────────────────────────────────────────────────────────

st.subheader("Pages", anchor="pages")

st.markdown("**Upload** — Register an SRS filing. Two stages:")
st.markdown(
    """
1. Pick the SRS `.mdb` **and** the PFD mask `.mdb` + give the filing a label.
2. The page parses the MDB, lists all notices found and lets you pick
   which ones become **systems**. Click *Register systems* to commit.
    """
)

st.markdown(
    "**Uploads** — List registered uploads + systems. Bulk delete, "
    "re-scan, register orphan filings. Per-system panels:\n"
    "* **Orbital parameters** — semi-major axis, altitude, perigee, "
    "apogee, eccentricity, inclination, RAAN, period, precession, "
    "sun-synch / station-keeping flags (read from the SRS `orbit` table).\n"
    "* **Operating frequency bands** — masks (`mask_info` table) with "
    "PFD / EIRP / Other categories + groups (`grp` table) with Tx/Rx "
    "side, beam, min elevation.\n"
    "* **View mask** buttons — open a PFD mask in the **Mask Viewer**."
)
st.markdown(
    "**Mask Viewer** — Interactive visualizer for an ITU-R S.1503-4 PFD "
    "mask: metadata header, latitude / B-axis sliders (real degrees, not "
    "indices), 2D heatmap with selectable axis spacing, slice line plot, "
    "and point-wise PFD calculator (trilinear / bilinear interpolation). "
    "See [Mask Viewer](#mask-viewer)."
)
st.markdown(
    "**Single-entry** — Run **ITU-R S.1503-4** (WCG search + EPFD↓ "
    "simulation + Article 22 compliance) on one system. Advanced options "
    "expose the §D.3.1 WCGA grid step, §D.4.7 dual time step, and the "
    "manual WCG override. Form labels are human-readable; the engine key "
    "for each field is shown in its tooltip."
)
st.markdown(
    "**Aggregate** — Run a multi-system aggregation study with one of "
    "five methods (see [Aggregation methods](#aggregation-methods)). The "
    "selected method's strategy is shown step-by-step in a panel below "
    "the method picker."
)
st.markdown(
    "**Launcher** — Launch a *campaign* (Methods 1–3 + optionally 4/5) on "
    "the same systems as a reproducible bundle. All child runs share a "
    "`campaign_id` and roll up in **Campaign**."
)
st.markdown(
    "**Runs** — History of every run, one row per run:\n"
    "* Columns: id, type, method, status (coloured), progress, campaign, "
    "**created_at (BRT)**, **finished_at (BRT)**, **duration** "
    "(`hh:mm:ss`), actions.\n"
    "* Click an `id` button → pre-selects that run in the *Inspect / "
    "delete* picker at the bottom.\n"
    "* Per-row icon buttons → **Results** and **Status** for that run.\n"
    "* `finished_at` is stamped by the worker subprocess itself (it "
    "emits `FINISHED_AT:<iso>` on stdout before exit), so the value "
    "reflects the actual termination instant rather than a UI poll lag."
)
st.markdown(
    "**Status** — Real-time progress + worker log. Updates via "
    "`@st.fragment(run_every=2)` — no manual refresh."
)
st.markdown(
    "**Results** — CCDF chart with limit overlays (Article 22 / "
    "Resolution 76), normative percentile bar, 3D globe. Clicking a "
    "point on the globe selects the matching entry in the *Geometry* "
    "picker; pressing **Show CCDF** opens a modal dialog with that "
    "point's CCDF + the regulatory limit curves."
)
st.markdown(
    "**Campaign** — Aggregated view of all runs in a campaign + XLSX "
    "export (`campaign`, `runs`, `params` sheets)."
)
st.markdown(
    "**Cluster** — Optional Ray distributed runtime. Standalone by "
    "default. State machine with 4 states (STANDALONE / RAY CLUSTER "
    "ACTIVE / UNREACHABLE / RAY NOT INSTALLED). Lets you pick a **bind "
    "IP** (auto / LAN / overlay) when starting the head. See [section "
    "below](#cluster-distributed-processing)."
)
st.markdown(
    "**Help** — This page."
)

# ─── Aggregation methods ────────────────────────────────────────────────────

st.subheader("Aggregation methods", anchor="aggregation-methods")
st.markdown(
    """
| Method | Strategy | Cost | When to use |
|---|---|---|---|
| **method_1** | Convolve per-system CCDFs at each filing's own WCG | `N` sims | Conservative aggregate (Study 1). |
| **method_2** | Sweep ES×GSO grid → keep per-system + per-point convolution + envelope | `n_grid × N` sims | Tighter aggregate (Study 2 / WP-4A Step-1). |
| **method_3** | Fuse N constellations + joint WCGA + single joint sim | 1 large sim + `N` post_sum | Highest fidelity (Study 3). |
| **method_4** | For each filing's WCG, simulate all N → convolve | `N × N` sims | Inhomogeneity audit (didactic). |

**Choosing a method**

- Need a single regulatory number? `method_2` (envelope) or `method_3` (joint).
- Want to compare WCG choices? `method_4`.
- Want raw per-(geometry, filing) CCDFs for further analysis? `method_2` keeps them (`per_point[].per_system`).
- Quick first-pass? `method_1`.

> `method_5` was folded into `method_2` (which now keeps the full curve set). It remains only as a dormant back-compat alias for reloading historical runs.

Per-method explanations also appear inline on the Aggregate page after
you select a method.
    """
)

# ─── Mask Viewer ────────────────────────────────────────────────────────────

st.subheader("Mask Viewer", anchor="mask-viewer")
st.markdown(
    """
The Mask Viewer renders an ITU-R S.1503-4 PFD mask interactively. Open
it from the **Uploads** page by clicking *View mask N · freq* on any
PFD mask listed under *Operating frequency bands*. The page also lives
in the sidebar — re-opening it picks up the last viewed mask via the
cross-page state.

**Layout**

* **Header metrics** — mask id, type (`alpha_deltaLongitude` /
  `azimuth_elevation` / …), shape `[nA × nB × nC]`, PFD value range in
  dBW/m².
* **A / B-axis sliders** — operate on the actual degree values of the
  mask grid, not on indices. Range and step come straight from the
  axes read out of the MDB.
* **Heatmap** — 2D PFD map for the selected latitude. Toggle between
  *Uniform (equal cells)* — every cell gets the same visual width
  regardless of the real degree interval (useful for non-uniform alpha
  axes like `[-80, -2, 2, 80]`) — and *Proportional (degrees)* — real
  degree coordinates.
* **Slice plot** — line chart of PFD vs the C-axis at the selected
  (lat, B) cell. A dashed amber vertical line on the heatmap marks the
  current slice.
* **PFD calculator** — type `(a, b, c)` in degrees, hit *Compute* and
  the page returns the interpolated PFD value. Uses bilinear
  interpolation on `(b, c)` with nearest-lat for `azimuth_elevation` /
  `alpha_deltaLongitude` masks (matches the engine's runtime behaviour),
  trilinear otherwise.

**Limitations**

* Only PFD masks (`f_mask = "P"`) can be opened. EIRP / Other masks are
  listed in Uploads but the viewer cannot render them.
* Mask coordinate conversion (alpha↔az/el) — implemented in the legacy
  HTML viewer — was **not ported** to Streamlit. Use the engine CLI
  for that.
    """
)

# ─── Cluster ────────────────────────────────────────────────────────────────

st.subheader("Cluster (distributed processing)", anchor="cluster-distributed-processing")
st.markdown(
    """
Aggregation runs fan out per-task via Ray when a cluster is configured.

**Modes** (persisted in `data/cluster.json`):
- `standalone` — default; all loops sequential. Best for a single machine.
- `cluster_client` — Streamlit acts as a Ray driver, dispatching tasks
  to a remote (or local) Ray head daemon.

**Start a head on this host** (Cluster page → *Start Ray head*):
- Pick `num_cpus`, port (6379), client port (10001), dashboard port (8265).
- The UI auto-switches to `cluster_client` and points at the local head.

**Attach a worker** (on a different machine):
- Install the same Python major.minor + the same Ray version
  (`pip install "ray[default]==<head_version>"`).
- Network must reach the head's GCS port (`6379` by default).
- Run: `ray start --address=<HEAD_IP>:6379`
- The repo and the uploaded MDBs do **not** need to live on the
  worker — Ray ships them via `runtime_env` on the first task.

**File shipping** is automatic:
- `src/` and `streamlit_app/` ship as Ray `py_modules`.
- `streamlit_app/data/uploads/` ships as `working_dir`.
- Workers receive **relative paths**, never the head's absolute paths.

**Thread limits** — each Ray task is pinned to `NUMBA_NUM_THREADS=1`,
`OMP_NUM_THREADS=1` etc., so total CPU usage = `num_cpus` (no
oversubscription from Numba's internal threading).

**Overlay networks (multi-machine across networks)**

When workers reach the head through an overlay / VPN (e.g. Tailscale,
Nebula, ZeroTier, WireGuard) instead of the local LAN, Ray must
advertise the **overlay IP** to peers — not the default-route LAN IP it
would auto-detect. On the *Start head* form, pick the host's overlay
address (typically `100.64.x.x` in the CGNAT range) in the
**Bind IP (`--node-ip-address`)** selector. Workers do the same:

```bash
ray start --address=<HEAD_OVERLAY_IP>:6379 \
    --node-ip-address=<WORKER_OVERLAY_IP>
```

Tradeoffs: no public ports to forward, NAT traversal handled by the
overlay, workers can sit on any network — at the cost of ~5–15 ms
extra RTT and ~5–10 % bandwidth loss vs LAN. Irrelevant for the
SHARC-Orbit workload (working_dir ships once per content hash; tasks
are CPU-bound).

**Bind IP**

When workers reach the head through a VPN / overlay network, the
*Start Ray head* form lets you pick the interface Ray should advertise
to peers. The dropdown enumerates the host's IPv4 interfaces and tags
overlay-style addresses (`100.64.0.0/10`, the CGNAT range used by
Tailscale and friends) as `vpn`, LAN as `private`, etc. Pick `vpn` when
workers will dial in through the overlay; pick `Auto` for plain LAN.

The chosen bind IP is persisted in `data/cluster.json` and reused when
the UI restarts the head.

See the README in `streamlit_app/` for the full multi-host walk-through.
    """
)

# ─── Timezone display ───────────────────────────────────────────────────────

st.subheader("Timezone (BRT) display", anchor="timezone-brt-display")
st.markdown(
    """
All timestamps are stored in **UTC** in the SQLite database (`created_at`,
`updated_at`, `finished_at`). UI columns labelled **(BRT)** are converted
to `America/Sao_Paulo` for display only via `storage.fmt_local(...)`.

Format used in the UI: `YYYY-MM-DD HH:MM:SS` (no tz suffix — the column
header makes it explicit). Workers and the launcher continue to write
UTC, so Ray clusters spanning different timezones stay consistent.
    """
)

# ─── Glossary ───────────────────────────────────────────────────────────────

st.subheader("Glossary", anchor="glossary")
st.markdown(
    """
- **EPFD↓** — Equivalent Power Flux Density downlink. Aggregate
  interference metric at a GSO ES from non-GSO satellites, in
  dBW/m²/40 kHz.
- **WCG** — Worst-case geometry: the (ES lat, ES lon, GSO lon) triple
  that maximises EPFD↓ for a given constellation.
- **WCGA** — Worst-case geometry algorithm per ITU-R S.1503-4 §D.3.1.
  Sweeps satellite latitudes with step `s1503_step_deg`, evaluates a
  regular (θ, φ) grid at each latitude, and binary-searches the
  α = α₀ (excluded zone) and ε = ε₀ (min elevation) boundaries to
  localise the WCG candidate. The grid is in satellite-relative angles
  (θ, φ), **not** in (ES lat, ES lon) directly.
- **CCDF** — Complementary cumulative distribution function:
  `P(EPFD > x)`. The y-axis on the Results plot is the percentage of
  time the aggregate EPFD exceeds the x-axis level. Log-scaled.
- **PFD mask** — Maximum allowed PFD a non-GSO satellite may radiate
  toward GSO, function of (off-axis angle from the GSO arc, frequency).
- **PFDMaskMulti** — A bundle of PFD masks indexed by satellite, used
  when a single notice declares multiple masks (`mask_lnk1` in the SRS
  MDB).
- **Article 22** — RR Art. 22 §22.5 sets single-entry EPFD↓ limits for
  Ku/Ka FSS frequencies. Used as the compliance threshold for
  Single-entry runs.
- **Resolution 76** — WRC-2000 Resolution defines aggregate EPFD↓
  limits across multiple non-GSO systems. Used as the threshold for
  Aggregate runs.
- **Studies 1–3** — Three aggregation methodologies under review by
  the ITU-R WP 4A — implemented here as methods 1, 2, 3 respectively.
- **Notice (`ntc_id`)** — Filing identifier in the SRS database. One
  MDB can carry several notices.
- **System** — In SHARC-Orbit: a `(upload_id, ntc_id)` pair — the unit
  of selection on Single-entry / Aggregate. PFD masks declared in the
  SRS are auto-distributed across satellites via `mask_lnk1`; `mask_id`
  is rarely user-selected (the engine builds a `PFDMaskMulti`
  transparently when several P-masks are associated to one notice).
- **Single-entry vs Aggregate** — Single-entry = one system vs
  Article 22 limits. Aggregate = N systems combined vs Resolution 76.
- **Run** — One execution of either the S.1503 single-system worker
  or the S.1588 multi-system worker. Identified by a 12-hex `id`.
- **`finished_at`** — Termination timestamp emitted by the worker
  itself (the subprocess prints `FINISHED_AT:<iso>` on stdout just
  before exiting). The launcher parses the last such line and writes
  it to the DB column. Used by the Runs page for the *finished_at* and
  *duration* columns.
- **Campaign** — A bundle of related runs (same systems, multiple
  methods) tagged by a shared `campaign_id` for cross-method reporting.
    """
)

# ─── Troubleshooting ────────────────────────────────────────────────────────

st.subheader("Troubleshooting", anchor="troubleshooting")
st.markdown(
    """
| Symptom | Likely cause | Fix |
|---|---|---|
| `0 notices detected` on Upload | Unsupported/corrupt MDB, or `non_geo` table absent | Confirm the file is a valid SRS MDB |
| `ImportError: from src.X` | `PYTHONPATH` not set | `export PYTHONPATH="$(pwd)"` from repo root before launching Streamlit |
| Status stays at 0 % forever | Worker process crashed at startup | Check the Status log for `ERROR:` line; common: bad MDB, missing JSON table in `src/data/` |
| Results page empty | Worker still running, or `sim_data.json` missing | Wait, or re-check the Status page for `failed` status |
| Cluster page `Querying Ray runtime…` hangs | Persisted address points at a dead head | Click *Reset to standalone* (or edit `data/cluster.json`) |
| `ModuleNotFoundError: src` inside Ray task | Worker host can't find the engine | Ensure SHARC-Orbit ships `src/` + `streamlit_app/` as `py_modules`. The UI does this automatically — make sure `cluster.uploads_runtime_env()` returns py_modules in the log. |
| `alpha_method=sweep` despite picking `analytical` | Old config cached | Re-launch the run; the fix writes `alpha_method` into `cfg["simulation"]` since 2026-06-05. |
| Multiselect chips overflow above/below the input | CSS conflict | Refresh hard (Ctrl+Shift+R) — `theme.inject()` may not have applied. |

**Where to look first**

- Status page → log tail (the engine prints `INFO …` and `WARN …` lines).
- `streamlit_app/data/runs/<run_id>/` → `params.json` to verify what
  the worker actually received; `summary.json` for the final metrics.
- Engine logs at the same location capture the WCGA / EPFD progress.
    """
)
