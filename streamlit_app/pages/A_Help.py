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
1. **Upload** an SRS `.mdb` and the PFD mask `.mdb`. Each notice
   (`ntc_id`) becomes an independent **system**. Operating-parameter
   `.xml` files are optional.
2. **Uploads** lists what was registered. **Mask viewer** draws one PFD
   mask. **Constellation** draws the non-GSO satellites of a filing.
   None of these three launches a run.
3. **Single-entry** runs ITU-R S.1503-4 on one system. **Aggregate**
   combines two or more.
4. **Status** streams the worker log and the progress bar.
5. On `success`, **Results** plots the CCDF, the Article 22 and
   Resolution 76 limits, and the geometries on the globe.

**National occupancy** is a separate survey: which bands are taken, from
a licensed-station table and a complete SRS you already have on disk.
It does not launch EPFD and it does not download catalogues.

**Cluster** is optional, and only useful with more than one machine.
    """
)

# ─── Workflow diagram ───────────────────────────────────────────────────────

st.subheader("Workflow", anchor="workflow")
st.markdown(
    """
```
Upload ─► Uploads ─► Single-entry ─► Status ─► Results
                  └► Aggregate    ─► Status ─► Results

Inspect (no run):  Uploads ─► Mask viewer
                            └► Constellation

Survey (no run):   National occupancy
```

Engine state lives in `streamlit_app/data/`:
- `uploads/<upload_id>/<file>.mdb` — your SRS / mask files
- `runs/<run_id>/{sim_data.json, summary.json, params.json, log}` — per-run artifacts
- `sharc_orbit.db` — SQLite with uploads, systems and runs
- `cluster.json` — persistent Ray runtime config
- `br_occupancy/` — occupancy catalogues you indexed locally
    """
)

# ─── Pages ──────────────────────────────────────────────────────────────────

st.subheader("Pages", anchor="pages")

st.markdown("**Upload** — Register an SRS filing. Two stages:")
st.markdown(
    """
1. The SRS `.mdb` and the PFD mask `.mdb` are both required. Give the
   filing a label. Operating-parameter `.xml` files are optional, and
   only when the mask database does not already carry them.
2. The page lists the notices. Each ticked notice becomes one
   **system**. PFD masks are detected, not chosen: several masks linked
   by `mask_lnk1` are distributed by the engine.
    """
)

st.markdown(
    "**Uploads** — Registered filings and systems. A filing-name prefix "
    "renames every filing. Maintenance deletes selected filings or all "
    "of them. Per system:\n"
    "* **Orbital parameters** — planes, altitude, eccentricity, "
    "inclination, RAAN, period, precession, sun-synch and "
    "station-keeping, from the SRS `orbit` table. The picker is shared "
    "with Single-entry, Aggregate, Mask viewer and Constellation.\n"
    "* **Operating frequency bands** — masks (`mask_info`) and groups "
    "(`grp`), with Tx/Rx, beam and minimum elevation.\n"
    "* **View mask** — opens that PFD mask in **Mask viewer**.\n"
    "The lower section re-scans a filing or deletes it."
)
st.markdown(
    "**Mask viewer** — One ITU-R S.1503-4 PFD mask: degree sliders, "
    "heatmap, slice plot, and a point calculator. Only PFD masks "
    "render. See [Mask viewer](#mask-viewer)."
)
st.markdown(
    "**Constellation** — 3D globe of a registered non-GSO filing at "
    "t = 0, with no run. Filter an Article 22 scenario down to the "
    "satellites that emit in that band, or show the whole "
    "constellation. Click a satellite and **Project mask** to draw its "
    "PFD footprint; **Open in Mask viewer** continues in 2D."
)
st.markdown(
    "**Single-entry** — **ITU-R S.1503-4** on one system. Section 1 "
    "shows the SRS band chart and an optional Article 22 scenario. "
    "Geometry is the §D.3.1 WCGA (countries restrict the earth-station "
    "domain and enable the RAAN sweep), a defined earth-station / GSO "
    "point, or the **ES×GSO grid** (same algorithm as Aggregate method "
    "2; countries optional). Empty parameter fields use the engine "
    "default."
)
st.markdown(
    "**Aggregate** — Two or more systems, one of four methods "
    "(see [Aggregation methods](#aggregation-methods)). Section 1 shows "
    "whether the selection is co-channel. The picked method's steps "
    "appear under the radio. Empty N and Δt are resolved per filing."
)
st.markdown(
    "**National occupancy** — Which bands are taken. Index a complete "
    "SRS from a BR IFIC ISO, bookshop zip, `srsNNNN.zip` or local "
    "`.mdb` already on disk, and optionally upload a licensed-station "
    "table (ITU symbol, frequencies in MHz). Extra SRS catalogues sit "
    "beside the first one. The country is a filter: **Serves it** or "
    "**Notified by it**. The chart's second line is the letter bands "
    "of that bar. The weekly IFIC file is not a source, and nothing "
    "is downloaded."
)
st.markdown(
    "**Runs** — One row per run:\n"
    "* Columns: id, system, type, method, status, progress, campaign, "
    "**created_at (BRT)**, **finished_at (BRT)**, **duration**, "
    "actions.\n"
    "* The `id` button selects that run in *Inspect / delete*.\n"
    "* The action icons open **Results**, open **Status**, reload the "
    "study into Single-entry or Aggregate, or delete the run.\n"
    "* `finished_at` is stamped by the worker (`FINISHED_AT:<iso>` on "
    "stdout), so it is the termination instant, not a UI poll.\n"
    "* **Maintenance** at the top deletes runs by status, or all of them."
)
st.markdown(
    "**Status** — Progress and the worker log, refreshed every 2 s. "
    "While the run is active the page also shows host CPU and memory "
    "and, on a cluster, per-worker utilisation."
)
st.markdown(
    "**Results** — CCDF with Article 22 and Resolution 76 limits, "
    "normative percentiles, the §D4 dual time step that actually ran, "
    "track-duration windows when MIN_DURATION ≠ 0, and the globe. "
    "Click a point and **Show CCDF** for that geometry. External "
    "results `.mdb` files overlay their reference CCDF."
)
st.markdown(
    "**Cluster** — Optional Ray runtime. Standalone by default. Four "
    "states: standalone, cluster active, unreachable, Ray not "
    "installed. Starting a head lets you pick the **bind IP** (auto, "
    "LAN or overlay). See [Cluster](#cluster-distributed-processing)."
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
| **method_3** | Fuse N constellations + joint WCGA + single joint sim (per-system curves come out of the same pass) | 1 large sim (+ `N` only if `post_sum` is enabled) | Highest fidelity (Study 3). |
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
- **Campaign id** — Optional tag on a run, shown in the **campaign**
  column of **Runs**. It groups runs that were launched together.
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
