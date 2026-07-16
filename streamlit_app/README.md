# SHARC-Orbit — Streamlit UI

Python-only UI for the Stage 1 EPFD aggregation simulator
(ITU-R S.1503-4 single-entry · Resolution 76 multi-system aggregation
studies, methods under review).

- 100 % Python — no other languages, no proprietary tech.
- No external APIs (UIT integrations discussed case by case).
- No authentication / no client-server access control.
- Open source, distributable via GitHub (target: `SIMULATOR-WG/SHARC-Orbit`).

## Status

Local single-user Streamlit UI for the simulator. The numerical engine in
`../src/` is reused as-is (no fork, no duplication) — the UI only wraps it.

---

## Quickstart (Linux / macOS)

The project standardises on [`uv`](https://docs.astral.sh/uv/) for the Python
interpreter and the venv — same tool on every platform, and it pins the exact
patch (**3.12.3**) the Ray cluster requires.

```bash
# from repository root

# install uv once (skip if already present):
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env                 # add uv to PATH (or restart the shell)

uv python install 3.12.3                # fetch the pinned interpreter (one-time)
uv venv --python 3.12.3 .venv           # create the venv on that exact patch
source .venv/bin/activate
uv pip install -r requirements.txt      # .mdb parsing is pure-Python (access-parser); no OS binary needed

export PYTHONPATH="$(pwd)"              # the engine in src/ must be importable
streamlit run streamlit_app/app.py
```

Browser opens at `http://localhost:8501`. No authentication; single-user local.

---

## Windows

Two ways to run:

- **Option A — Native Windows**: works for the full Streamlit app. `.mdb`
  parsing is pure-Python (`access-parser`) and the engine no longer forces
  `fork`, so `multiprocessing` falls back to `spawn` automatically.
- **Option B — WSL2 (recommended)**: same code path as the production Linux
  server, `fork`-based workers, full feature parity (Ray cluster, port helpers).

### Option A — Native Windows (PowerShell)

Uses [`uv`](https://docs.astral.sh/uv/) — same tool as Linux/macOS — so no
separate python.org install is needed; uv provides the pinned **3.12.3**
interpreter. From the repository root:

```powershell
# install uv once (skip if already present):
winget install --id astral-sh.uv --accept-package-agreements --accept-source-agreements
#   then close & reopen PowerShell so PATH refreshes

uv python install 3.12.3                # fetch the pinned interpreter (one-time)
uv venv --python 3.12.3 .venv           # create the venv on that exact patch
.\.venv\Scripts\Activate.ps1            # if blocked: Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
uv pip install -r requirements.txt      # .mdb parsing is pure-Python; no OS binary needed

$env:PYTHONPATH = (Get-Location).Path  # cmd.exe:  set PYTHONPATH=%cd%
streamlit run streamlit_app/app.py
```

**Known limitations on native Windows** (all degrade gracefully; none change
the simulation result):

- **Distributed (Ray) mode is experimental** — use the default **standalone**
  (single-machine) mode; multi-node clustering is WSL2/Linux only.
- **`--kill-port` is a no-op** (relies on Unix `fuser`/`lsof`); free port 8501
  manually or pass `--server.port`.
- The legacy `src/launcher_ui.py` HTTP launcher is unsupported (POSIX process
  groups); the Streamlit app does not use it.
- Worker cold start is slightly slower (`spawn` re-imports vs `fork`).

### Option B — WSL2

Run inside WSL2 (Ubuntu) for full parity with the Linux server.

#### 1. Install WSL2 + Ubuntu

In an elevated PowerShell:

```powershell
wsl --install -d Ubuntu-22.04
# reboot if prompted; create UNIX user when Ubuntu first launches
```

#### 2. Allocate RAM/CPU (host Windows, optional)

Create `%USERPROFILE%\.wslconfig` (e.g. `C:\Users\You\.wslconfig`):

```
[wsl2]
memory=16GB
processors=8
swap=4GB
```

Run `wsl --shutdown` once to apply.

#### 3. Install dependencies inside WSL2

Use [`uv`](https://docs.astral.sh/uv/) for the interpreter + venv (same tool as
the other platforms; pins the exact **3.12.3** the Ray cluster requires —
Ubuntu's system `python3` is 3.10 and must not be relied on).

```bash
sudo apt update
sudo apt install -y git                            # .mdb parsing is pure-Python; no apt package needed
curl -LsSf https://astral.sh/uv/install.sh | sh    # installs uv into ~/.local/bin
source ~/.local/bin/env                            # add uv to PATH (or restart the shell)
```

#### 4. Clone + setup (inside WSL2)

> **Important:** put the repo under your **WSL filesystem** (`~/projects/`),
> NOT under `/mnt/c/...`. The `/mnt/c` mount is 10–50× slower for file I/O
> and Numba JIT cache writes.

```bash
cd ~ && mkdir -p projects && cd projects
git clone <repo_url> sharc-orbit
cd sharc-orbit
uv python install 3.12.3            # one-time: fetch the pinned interpreter
uv venv --python 3.12.3 .venv       # create the venv on that exact patch
source .venv/bin/activate
uv pip install -r requirements.txt  # install all deps into .venv
```

#### 5. Run

```bash
export PYTHONPATH="$(pwd)"
streamlit run streamlit_app/app.py
```

Open `http://localhost:8501` in any Windows browser — WSL2 forwards the
loopback port automatically.

#### 6. Editor (optional)

Install **VS Code + "Remote — WSL"** extension. From the Ubuntu shell:

```bash
code .
```

Opens VS Code wired into WSL2 — editing, terminal, debugging all run
inside the Linux env.

#### Where to put MDB files (WSL2)

If the SRS / MASK MDBs live on Windows (`C:\Users\You\Documents\...`), copy
them into the WSL2 filesystem before using:

```bash
cp /mnt/c/Users/You/Documents/STEAM-2*.MDB ~/projects/sharc-orbit/data/
```

Then upload from the Streamlit UI as normal (files are served from
`~/projects/sharc-orbit/streamlit_app/data/uploads/...`).

---

## Layout

```
src/                            # numerical engine (ITU-R S.1503-4), pure Python — the UI wraps it
├── main.py                     # WCG↓ pipeline entrypoint (ITU-R S.1503-4)
├── constants.py                # physical constants + Earth model (S.1503-4)
├── coordinates.py              # ECI / ECEF / LLA / topocentric transforms
├── orbit_propagator.py         # Kepler + J2 propagator, Walker constellations
├── constellation_templates.py  # parametric constellation patterns (Walker/HEO/IGSO, multi-shell)
├── time_step.py                # time-step + step-count (S.1503-4 §D.4)
├── geometry.py                 # α / X angle + orbital geometry (Numba)
├── antenna.py                  # ITU antenna patterns (GSO / BSS earth stations)
├── pfd_mask.py                 # PFD mask read + interpolation (CSV / XML)
├── mask_converter.py           # PFD mask α/Δlon ↔ Az/El conversion
├── mask_generator.py           # parametric PFD-mask generator (S.1503-4 Part C, §C2.3.1 envelope)
├── wcg_search.py               # Worst-Case Geometry search (S.1503-4 §D.3)
├── epfd_calculator.py          # EPFD↓ computation + statistics (S.1503-4)
├── epfd_stream_accumulator.py  # O(1)-memory streaming EPFD↓ accumulator
├── srs_reader.py               # SRS/ITU .mdb reader (pure-Python access-parser)
├── mdb_writer.py               # write manual system → JET4 SRS/Mask .mdb pair (Jackcess bridge)
├── exceptions.py               # NoValidGeometry / InvalidManualGeometry domain exceptions
├── article22_tables.py         # ITU-R Article 22 EPFD↓ limits (Tables 22-1A..E)
├── resolution76_tables.py      # ITU-R Resolution 76 aggregate EPFD limits
├── export_visualization.py     # export results to CZML (CesiumJS viewer)
├── orbit_tracks_parquet.py     # segmented ECEF orbit tracks → Parquet (CZML)
├── s1503_figure13_wcg_lon.py   # S.1503-4 Figure 13 longitudinal WCG correction
├── launcher_ui.py              # legacy standalone HTTP launcher (unused by Streamlit)
├── __init__.py                 # pins NUMBA_THREADING_LAYER=workqueue (fork-safe)
├── data/                       # normative limit tables (JSON, tracked)
│   ├── article22_limits.json
│   └── resolution76_limits.json
└── s1588_studies/              # Resolution 76 Studies 1–3 (multi-system aggregation)
    ├── runner.py               # fixed-geometry EPFD run (no WCG search)
    ├── single_pass.py          # tiled single-pass EPFD, shared propagation
    ├── multi_system.py         # joint N-system run, linear EPFD power sum
    ├── vectorized_kernel.py    # vectorized EPFD kernel (M points × N sats/step)
    ├── convolution.py          # CCDF convolution for multi-system (Studies 1–2)
    ├── percentiles.py          # normative percentiles (10/1/0.1/0.01%) from CCDF
    ├── geometry.py             # ES/GSO point descriptor + grid generation
    └── countries.py            # point-in-polygon country filter (GeoJSON)

streamlit_app/
├── app.py                  # entrypoint (sidebar label "SHARC-Orbit" via st.navigation)
├── pages/                  # multi-page (Streamlit convention)
│   ├── 0_Cluster.py        # Ray runtime config + node control
│   ├── 1_Upload.py
│   ├── 2_Uploads.py
│   ├── 3_Single_entry.py   # Single-system run (ITU-R S.1503-4)
│   ├── 4_Aggregate.py      # Multi-system aggregation (4 methods under study)
│   ├── 5_Launcher.py       # Campaign launcher
│   ├── 6_Runs.py           # Run history (per-row Results/Status buttons)
│   ├── 7_Status.py         # Run progress + log stream
│   ├── 8_Results.py        # CCDF, percentiles, EPFD timeline, 3D globe
│   ├── 9_Campaign.py       # Campaign dashboard + XLSX export
│   ├── A_Help.py           # In-app manual (workflow, methods, glossary)
│   ├── B_Mask_Viewer.py    # Interactive PFD mask visualiser
│   ├── C_Constellation.py  # NGSO constellation 3D viewer (Article 22 scenarios + per-sat mask footprint)
│   ├── E_Manual_System.py  # Parametric NGSO entry (template wizard → EPFD↓ run / register-as-filing)
│   └── F_Mask_Generator.py # Parametric PFD-mask generator (S.1503-4 Part C)
├── lib/
│   ├── engine.py           # wraps ../src/ engine calls
│   ├── storage.py          # SQLite + filesystem persistence
│   ├── state.py            # st.session_state helpers + disk persistence
│   ├── plots.py            # Plotly chart factories (CCDF, EPFD, globe)
│   ├── workers.py          # subprocess management for long runs
│   ├── launcher.py         # bridge worker ↔ DB ↔ filesystem
│   ├── filings.py          # SRS preview
│   ├── srs_inspect.py      # notice/mask listing + satellite count
│   ├── estimator.py        # runtime + workload estimator (auto-calibrated)
│   ├── exports.py          # per-run results workbook (run_to_xlsx) + campaign XLSX reports
│   ├── theme.py            # CSS overrides + Material Symbols
│   ├── widgets.py          # select_described (selectbox with per-option hints)
│   ├── art22_ui.py         # Article 22 scenario tree helpers (shared by 3/4/5)
│   ├── manual.py           # per-page contextual help snippets
│   ├── tour.py             # stateful guided tour across the workflow pages
│   ├── cluster.py          # Ray runtime + parallel dispatch + node control
│   ├── wcga_cluster.py     # Ray executor for the WCGA search (S.1503 §D.3)
│   ├── epfd_cluster.py     # Ray executor for the EPFD↓ time-chunk sweep
│   ├── hwinfo.py           # CPU/RAM detection for thread/worker budgeting
│   ├── mdb_results.py      # parse external results .mdb → CCDF overlay curves
│   ├── plan.py             # cost-aware LPT task ordering for cluster dispatch
│   ├── result_artifacts.py # per-run CSV/PNG artifacts (CCDF · histogram · timeseries · geometries · table17 · maps)
│   ├── report.py           # S.1503-4 §D7.3 examination summary → summary.html
│   └── job_runners/
│       ├── s1503_worker.py # single-system worker
│       └── s1588_worker.py # multi-system worker (method_1..4)
├── data/                   # SQLite + uploads + runs + cluster.json (gitignored)
├── tests/                  # pytest
│   ├── test_smoke.py       # end-to-end worker + storage
│   ├── test_hwinfo.py
│   ├── test_cluster_lpt.py
│   └── test_mdb_results.py
└── .streamlit/config.toml  # theme, server settings
```

---

## Article 22 scenario selector

Single-entry (3), Aggregate (4) and the campaign Launcher (5) expose an
optional **Article 22 downlink scenario** tree (`lib/art22_ui.py`):
**EPFD↓ → service (FSS/BSS) → frequency run → option (ES antenna · ref BW ·
pattern)**, built from the filing's PFD bands (Single-entry) or the
**common** band across selected systems (Aggregate / Launcher). A chosen
leaf overrides Service / ES antenna / reference BW / simulation frequency
(Single-entry also pins the PFD `mask_id`); the Service + ES-antenna widgets
mirror it. Left on **Auto**, the engine auto-resolves the mask from
`mask_lnk1` precedence (`emi_rcp=E` → `grp_id` → `seq_no`; explicit choice
wins) and the limits per filing. The resolved normative config and a run
`timing` block are written into `sim_data.json` / `summary.json` and shown
on Results; the Runs **reload** restores the scenario.

---

## Manual system, Mask generator & Constellation viewer

Three pages build or inspect systems **without a run against a stored filing**.

### Manual system (parametric NGSO entry)

`pages/E_Manual_System.py` — define an NGSO system with no SRS filing. Pick a
constellation template (Walker Delta / Star, equatorial ring, single
plane/train, Molniya, Tundra, IGSO) to pre-fill an editable plane table (RAAN
`O[N]`, ω `W[N]`, per-satellite phases `V[N]` per §B3.2); **Append** composes
multi-shell systems. Helpers set the sun-synchronous inclination or a
repeat-ground-track altitude. Preview on the 3D globe, attach a standalone
PFD-mask XML (§C4.2; run frequency defaults to mask `fmin + RefBW/2` per §D2
when left at 0), then either:

- **Launch EPFD↓ run** — straight through the same engine path as MDB filings
  (`src.main.load_from_manual`); the run's `params.json` records
  `input_source=manual`.
- **Register as filing** — writes a real JET4 `<base>_SRS.mdb` +
  `<base>_Mask.mdb` pair via a cross-platform Java/Jackcess helper and hands
  off to **Upload** Step 2. Writing real `.mdb` files needs a JRE with the
  `jdk.compiler` module; when it is absent the page automatically falls back to
  a YAML SRS-equivalent + mask-XML pair (the button label reflects which).

### Mask generator (S.1503-4 Part C)

`pages/F_Mask_Generator.py` — build a PFD mask from beam parameters:
`pfdᵢ = Pᵢ + Gᵢ(θ) − 10log₁₀(4πd²)` per cell (§C2.3.1), summed over the `N_co`
strongest beams (§C2.4). Choose the format — **Option 1** (lat × α × ΔLong,
§C2.4.1) or **Option 2** (lat × az × el, §C2.4.2), both generated natively (no
conversion). GSO-arc avoidance (§C2.2) is either a direct `|α| < α₀` cutoff
with a user fill value or a boresight beam switch-off that keeps sidelobe
leakage; an operating-latitude band nulls the rows outside it to −1000 dBW
(§C1). Preview the per-latitude heat-map (fully-off slices are explained, not
blank) and download the round-trippable §C4.2 XML — usable directly as a
Manual-system mask.

### Constellation viewer

`pages/C_Constellation.py` — a 3D globe of the NGSO constellation for a
registered filing, **no run needed**. Views are Article 22 scenarios (service ×
band × ref-BW × table, deduplicated over ES antenna diameter) or **complete**
(all frequencies); for a scenario the page resolves which satellites emit in
the band (`grp` ⋈ `mask_lnk1`) and highlights them. Click a satellite to
isolate it and project its PFD mask onto the ground as a heat-map **footprint**
(grid resolution adjustable, floor 0.1°, with a min-PFD filter, a min-elevation
cutoff and a hover read-out). Both globes carry a lat/lon graticule with the
**equator emphasized** — the equatorial GSO-arc plane and the reference for the
α / ΔLong geometry.

---

## Isolation rules

`streamlit_app/` imports only from:

- Python stdlib
- The pip dependencies listed in `requirements.txt`
- The numerical engine at `../src/`

`streamlit_app/` **does not** import any client-server / auth stack
(FastAPI / SQLAlchemy / Authentik). The CI check below enforces it:

```bash
! grep -rE "from backend|import backend|fastapi|sqlalchemy|authentik|asyncpg|minio|httpx" \
    streamlit_app/lib streamlit_app/pages streamlit_app/app.py
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| "0 notices detected" on Upload | unsupported/corrupt MDB, or `non_geo` absent | confirm the file is a valid SRS MDB |
| Streamlit very slow to start | Numba cold-start JIT | First call always takes 5–10 s extra; subsequent calls fast |
| Streamlit reads `/mnt/c` | repo in Windows filesystem | move to `~/projects/` inside WSL2 |
| ImportError `from src.X` | PYTHONPATH not set | `export PYTHONPATH="$(pwd)"` from repo root |
| Run finishes with an orange **NO_GEOMETRY** pill | WCG search completed but no GSO geometry cleared the S.1503-4 store criteria | Expected outcome, not a crash — Results shows the message + diagnostics (the gating knobs that bit); widen the search or relax the reported knobs (min elevation ε₀, α₀ exclusion, strict exclusion zone) |
| Manual-system run fails with "invalid manual geometry" | Out-of-range manual ES / GSO coordinates | Fix the ES lat/lon or GSO longitude on the Manual system page (within valid ranges) |

---

## Distributed processing (Ray)

Aggregate runs (methods 1–5) dispatch per-filing / per-geometry sub-sims
through `lib/cluster.py`. Two modes via the **Cluster** page:

| Mode | Use when |
|---|---|
| `standalone` (default) | One machine. Loops sequential. The engine already saturates local cores via Numba/joblib, so Ray adds no benefit here. |
| `cluster_client` | Multiple machines. Streamlit acts as the Ray driver and dispatches tasks to a Ray head daemon. |

Persistent config in `streamlit_app/data/cluster.json`. The Cluster
page is a 4-state machine (`STANDALONE` / `RAY CLUSTER ACTIVE` /
`RAY CLUSTER UNREACHABLE` / `RAY NOT INSTALLED`) — each state shows only
the action that makes sense from there.

### Multi-machine setup

The Cluster page wraps `ray start` so you usually don't need a
terminal.

1. **Head node** (the machine running Streamlit is a fine choice):
   Cluster → *Enable distributed mode* → fill `CPUs for head` (caps how
   many CPUs this node advertises to Ray), GCS / client / dashboard
   ports → **Start Ray head on this host**.

   The UI auto-switches persisted mode to `cluster_client` and points
   the driver at `ray://<HEAD_IP>:10001`. Head endpoint (IP, GCS
   address, dashboard URL) is displayed and persisted.

   Terminal equivalent:
   ```bash
   ray start --head --port=6379 --dashboard-host=0.0.0.0 --num-cpus=<N>
   ```

2. **Worker nodes.** On every worker:
   - Install Python with the **exact patch version** as the head — the cluster
     standardises on **3.12.3** (Ray compares `major.minor.micro` on every node
     and rejects a patch mismatch). Easiest: `uv python install 3.12.3`.
   - Install `ray[default]` matching the head's version **exactly**.
   - Network reachability to the head's GCS port (`6379` default).
   - Run: `ray start --address=<HEAD_IP>:6379` (optionally with
     `--num-cpus=N` to cap).
   - The repo and the uploaded MDBs do **not** need to live on the
     worker — Ray ships `src/`, `streamlit_app/` and the uploads
     directory automatically via `runtime_env` on the first task
     (see *File shipping* below).

3. Back on the head's UI, hit **Query status**. The Nodes table lists
   every alive worker (address, CPU, GPU, memory).

4. Launch an Aggregate run. The worker log shows `Ray active ·
   working_dir shipped: gcs://...` and tasks fan out.

5. **Tear down**: *Stop cluster & return to standalone* on each
   machine, or `ray stop` via SSH.

If the head dies between sessions, the UI lands on
`RAY CLUSTER UNREACHABLE` with three buttons — *Re-check*,
*Restart head*, *Return to standalone* — to recover.

### Overlay networks (VPN — Tailscale, Nebula, ZeroTier, WireGuard…)

When workers will reach the head through an overlay network rather
than the local LAN, Ray must advertise the **overlay IP** to peers —
not the default-route LAN IP it would auto-detect.

In the *Start Ray head* form, the **Bind IP (`--node-ip-address`)**
selector enumerates this host's IPv4 interfaces. Overlay-style
addresses in 100.64.0.0/10 (CGNAT range — what Tailscale and similar
overlays use) appear at the top of the list. Pick that entry and Ray
will:

- Bind GCS / object manager / Ray Client to that IP.
- Advertise it to every joining worker.

Workers must do the equivalent — when running `ray start` on a worker
host, pass its own overlay IP:

```bash
ray start --address=<HEAD_OVERLAY_IP>:6379 \
    --node-ip-address=<WORKER_OVERLAY_IP>
```

Tradeoffs vs LAN: no public ports, NAT traversal handled by the
overlay, workers can sit on any network — at the cost of ~5–15 ms
extra RTT and ~5–10 % bandwidth loss (WireGuard MTU 1280).
Irrelevant for the SHARC-Orbit workload — `working_dir` ships once
per content hash; tasks are CPU-bound, not network-bound.

The bind IP is persisted in `data/cluster.json` (`node_ip_address`)
and reused automatically when the UI restarts the head.

### File shipping (no manual rsync)

`cluster.uploads_runtime_env()` builds a Ray `runtime_env` that ships:

- `src/` and `streamlit_app/` as **`py_modules`** — engine + helpers
  importable on every Ray worker, even on a fresh remote host.
- `streamlit_app/data/uploads/` as **`working_dir`** — SRS/mask MDBs
  available to tasks at the worker's CWD.

Ray hashes the content, caches per node, and re-ships only when files
change. Default working_dir cap is raised from 100 MiB to **2 GiB** via
`RAY_RUNTIME_ENV_WORKING_DIR_UPLOAD_SIZE_LIMIT_BYTES`.

Filing paths in the worker payload are **relative** (`<upload_id>/<file>.MDB`).
The task resolver tries in order:
1. The driver's absolute path (works on single-machine).
2. CWD-relative path (Ray's extracted `working_dir`).
3. Worker's local `streamlit_app/data/uploads/<rel>` (manual rsync).

No need for usernames to match between head and workers.

### Thread pinning

Each Ray task is pinned to **1 thread per library** via env vars in
the runtime_env: `NUMBA_NUM_THREADS=1`, `OMP_NUM_THREADS=1`,
`MKL_NUM_THREADS=1`, `OPENBLAS_NUM_THREADS=1`, `NUMEXPR_NUM_THREADS=1`.

This makes Ray's `num_cpus` cap = total active threads (no
oversubscription from Numba's internal threading).

### Parallelism per method

| Method | Tasks dispatched |
|---|---|
| method_1 | `N` filings (per-filing single-entry sim) |
| method_2 | `n_grid_points × N` (geometry × filing) |
| method_3 | joint sim sequential; post_sum `N` tasks parallel |
| method_4 | `N` WCGAs + `N × N` (WCG × filing) sims |

Each Ray task receives only JSON-serialisable dicts (filing payload
with relative paths + common params + lat/lon/gso_lon).

### Standalone fallback

If `ray` is not installed, or mode = `standalone`, every `parallel_*`
call collapses to a plain Python loop. The worker code path is the
same — only the dispatch wrapper changes. Develop locally without Ray,
turn it on when you need to spread the load across machines.

---

## Roadmap

Done:
- Ray distributed worker pool (method_1..4 dispatch via `lib/cluster.py`)
- Ray runtime_env shipping (`src/` + `streamlit_app/` as `py_modules`,
  `uploads/` as `working_dir`) — no manual rsync on worker hosts
- Cluster page state machine (Standalone / Active / Unreachable / Missing)
- Head daemon start/stop from the UI
- Bind IP picker for VPN / overlay networks (Tailscale, Nebula, …)
- Configurable WCG search & dual time-step controls (S.1503-4 §D.4.7)
- Per-geometry CCDF modals on the 3D globe (Single-entry / Aggregate)
- Calibrated runtime estimator using past runs
- In-app Help page + contextual `help_expander` on every workflow page
- Click-on-globe selects the matching geometry in the picker (Results)
- `finished_at` worker-emitted timestamp + duration column in Runs
- Timestamps stored in UTC, displayed in BRT (`storage.fmt_local`)
- Uploads: per-system orbital parameters + operating frequency bands
- Mask Viewer page — interactive PFD mask visualiser (heatmap + slice
  + calculator), ported from the legacy HTML viewer (minus mask
  conversion)
- Cross-page state (`current_system_id` / `current_mask_id` /
  `current_run_id`) — selecting on one page propagates to the others
- Manual system page — parametric NGSO entry (constellation-template wizard,
  editable plane table, 3D preview) that launches EPFD↓ through the same engine
  path as filings, or registers a real MDB / YAML+XML pair via the Upload flow
- Mask generator page — parametric PFD masks (S.1503-4 Part C); Option 1
  (α/ΔLong) and Option 2 (az/el) generated natively, with GSO-arc avoidance
  (α-cutoff or beam switch-off) and an operating-latitude band
- Constellation viewer page — Article 22-scenario 3D globe with per-satellite
  PFD-mask footprint and an emphasized-equator graticule; runs directly off a
  filing (no simulation)
- Per-run result artifacts next to `sim_data.json` — `ccdf_epfd.csv`,
  `epfd_histogram.csv`, `epfd_timeseries.csv(.gz)`, `geometries.csv`,
  `table17.csv` and `ccdf.png` / `histogram.png` / `map.png`, all with unit
  headers (A2.1 Table 1)
- S.1503-4 §D7.3 examination report — `summary.html` (Pass/Fail statement +
  Table 17 + CDF), an ITU-style per-run `.xlsx` (run_def / result_def /
  results / cdf / pdf tabs) and a one-click **Download all** `.zip` on Results
- Structured `no_geometry` run outcome (orange pill) when the WCG search finds
  no geometry meeting the store criteria — a completed run with message +
  diagnostics, not a traceback

Pending:
- Docker image for one-command worker deployment
- XLSX exporter v2: metrics / percentiles / compliance / cross-method
  comparison (current export is a raw DB dump)
- Multi-user / auth gating (intentionally out of scope for this build)
