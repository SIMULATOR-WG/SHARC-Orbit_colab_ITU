# SHARC-Orbit — Streamlit UI

Python-only UI for the Stage 1 EPFD aggregation simulator
(ITU-R S.1503-4 single-entry · Resolution 76 multi-system aggregation
studies, methods under review).

- 100 % Python — no other languages, no proprietary tech.
- No external APIs (UIT integrations discussed case by case).
- No authentication / no client-server access control.
- Open source, distributable via GitHub (target: `SIMULATOR-WG/SHARC-Orbit`).

## About this version

This is an **academic version**, written in Python to facilitate collaboration
between ITU Members. It is **not a commercial solution** and is not
intended to be one.

The software is **still under active development**: features, methods, and
numerical results may change, and some parts are still being validated.
**Suggestions for improvement are very welcome.**

## Status

Local single-user Streamlit UI for the simulator. The numerical engine in
`../src/` is reused as-is (no fork, no duplication) — the UI only wraps it.

**What the UI covers today:**

- **Single-entry EPFD↓** (ITU-R S.1503-4) from SRS/ITU `.mdb` filings — WCG↓
  search, dual time-step, Article 22 limit checks.
- **SNS mutually-exclusive configurations** — reads the AP4 config fields
  (`multi_config_type` / `nbr_config` / `orbit_set_id`) and, for `'M'` filings,
  shows an S/M badge and lets you pick which orbit-set configuration is
  simulated, so distinct configs are never summed into one fictitious
  constellation.
- **Manual / parametric NGSO systems** — define a system with no filing via a
  constellation-template wizard (Walker δ/★, equatorial ring, train, Molniya,
  Tundra, IGSO, multi-shell), edit the plane list, preview on the 3D globe,
  attach a PFD mask, and launch EPFD↓ through the same engine path as MDB
  filings; optionally register it as a real `.mdb` filing pair or YAML+XML.
- **Parametric PFD-mask generator** (S.1503-4 Part C) — build a PFD mask from
  beam parameters (native Option 1 α×Δlon or Option 2 Az/El) with GSO-arc
  mitigation (beam-off / α-cutoff) and an operating-latitude band, and export
  round-trippable C4.2 XML usable directly as a Manual-System mask.
- **Per-run artifacts + §D7.3 report** — every finished run drops CCDF /
  histogram / timeseries / geometry / Table 17 CSVs and PNGs, a `summary.html`
  examination report (Pass/Fail statement + Table 17 + CDF), an ITU-style
  multi-sheet XLSX, and a one-click Download-all `.zip`.
- **Constellation / footprint viewer** — orbit geometry with WCG↓
  geometry-validation feedback and an emphasized-equator graticule on both 3D
  globes.
- **Resolution 76 multi-system aggregation** — methods 1–5 (studies, methods
  under review), optionally distributed across a Ray cluster.

> **Architecture:** for the design, module layout, and data flow of the UI and
> engine, see [`ARCHITECTURE.md`](ARCHITECTURE.md).

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

> **Optional — writing real `.mdb` filing pairs.** *Reading* SRS/mask MDBs is
> pure-Python (`access-parser`) and needs nothing extra. Only the **Manual
> system → Register as filing** path — which writes a real JET4
> `<base>_SRS.mdb` + `<base>_Mask.mdb` via a Java/Jackcess helper
> (`tools/jackcess/`) — needs a Java runtime that includes the `jdk.compiler`
> module (a full JDK, not a stripped JRE). Without it, that button falls back to
> a YAML + mask-XML filing and everything else works unchanged.

---

## Windows

There are two ways to run on Windows:

- **Option A — Native Windows**: works for the full Streamlit app. `.mdb`
  parsing is pure-Python (`access-parser`) and the engine no longer forces
  `fork`, so `multiprocessing` falls back to `spawn` automatically. Good for a
  local single-machine analysis.
- **Option B — WSL2 (recommended)**: same code path as the production Linux
  server, `fork`-based workers (faster cold start), and full feature parity
  (Ray cluster, port helpers).

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

Browser opens at `http://localhost:8501`.

**Known limitations on native Windows** (none affect the core simulation
result; all degrade gracefully):

- **Distributed (Ray) multi-node mode is experimental on Windows.** Single-machine
  **standalone** is the default and needs nothing extra. To join a Ray cluster from
  a Windows node, see *Joining a Ray cluster from Windows* below — it works, but Ray
  upstream marks it experimental.
- **`--kill-port` CLI helper is a no-op** (it relies on the Unix `fuser`/`lsof`).
  If port 8501 is busy, close the other process or pass `--server.port`.
- **The legacy `src/launcher_ui.py` HTTP launcher is unsupported** (uses POSIX
  process groups). The Streamlit app does not use it — ignore it.
- Worker **cold start is slightly slower** (`spawn` re-imports modules; `fork`
  on Linux does not).

#### Joining a Ray cluster from Windows

Standalone runs need none of this — skip unless you are joining this Windows
machine to a Ray **head** on another node.

Two gates have to be cleared, in order:

**1. Ray refuses multi-node on Windows unless you opt in.** Without the env var,
`ray start` aborts with *"Multi-node Ray clusters are not supported on Windows
and OSX."* Set it in the same shell before `ray start` (PowerShell):

```powershell
$env:RAY_ENABLE_WINDOWS_OR_OSX_CLUSTER = "1"
ray start --address=<HEAD_IP>:6379
```

**2. The Python patch version must match the head exactly.** Ray compares
`major.minor.micro` on every node; a mismatch aborts with *"Version mismatch …
Python: 3.12.3 … Python: 3.12.10"*. The cluster here standardises on **3.12.3**.
If you followed **Option A** (uv with `uv python install 3.12.3`), you are
already on the right patch — nothing to do. Do **not** create the venv from
`winget install Python.Python.3.12`: it only installs the newest patch
(e.g. 3.12.10) and will **not** match the head.

Then activate, set the env var, and `ray start --address=…` as in gate 1. The
`ray[default]==2.55.1` wheel in `requirements.txt` has a Windows build for
3.12, so Ray installs natively — no WSL2 needed for a Windows worker.

> Ray has **no Windows wheel for Python 3.13** as of 2.55.1. If `pip install -r
> requirements.txt` fails on `ray==2.55.1` with *"No matching distribution"*,
> you are on 3.13 — drop to 3.12.3 per the steps above.

### Option B — WSL2

Run SHARC-Orbit inside WSL2 (Ubuntu) for full parity with the Linux server.

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

`requirements.txt` pins modern wheels (`pandas==3.0.3`, `numpy==2.4.6`, …)
that require **Python ≥ 3.11**. Ubuntu 22.04 ships only Python 3.10, so do
**not** rely on the system `python3`. Use [`uv`](https://docs.astral.sh/uv/)
to manage an isolated interpreter and the venv.

```bash
sudo apt update
sudo apt install -y git                      # .mdb parsing is pure-Python (access-parser); no apt package needed
curl -LsSf https://astral.sh/uv/install.sh | sh   # installs uv into ~/.local/bin
source ~/.local/bin/env                      # add uv to PATH (or restart shell)
```

> **Pin the exact patch version (3.12.3).** Ray requires the Python
> `major.minor.micro` to match on **every** node of a cluster. Asking `uv`
> for bare `3.12` pulls the newest patch (e.g. 3.12.13) and a node on a
> different patch will be rejected with a `Version mismatch` error. The whole
> cluster here standardises on **3.12.3**.

#### 4. Clone + setup (inside WSL2)

> **Important:** put the repo under your **WSL filesystem** (`~/projects/`),
> NOT under `/mnt/c/...`. The `/mnt/c` mount is 10–50× slower for file I/O
> and Numba JIT cache writes.

```bash
cd ~ && mkdir -p projects && cd projects
git clone <repo_url> sharc-orbit
cd sharc-orbit
uv python install 3.12.3            # one-time: fetch the pinned interpreter
uv venv --python 3.12.3 .venv       # create venv on that exact patch
uv pip install -r requirements.txt  # install all deps into .venv
```

#### 5. Run

```bash
source .venv/bin/activate
export PYTHONPATH="$(pwd)"
streamlit run streamlit_app/app.py
```

Open `http://localhost:8501` in any Windows browser — WSL2 forwards the
loopback port automatically.

> **Numba + fork:** the engine runs `@njit(parallel=True)` and then forks via
> `multiprocessing.Pool`. Numba's default Linux threading layer (GNU OpenMP)
> is not fork-safe and aborts with *"fork() called from a process already
> using GNU OpenMP"*. This is handled in code — `src/__init__.py` pins
> `NUMBA_THREADING_LAYER=workqueue` before numba is imported, so no env var
> or manual step is needed.

#### 6. Ray cluster across the LAN (multi-node, optional)

Single-machine runs need **nothing here** — the engine saturates local cores
via Numba/multiprocessing and Ray adds no benefit (see the **Cluster** page).
This section is only for joining a WSL2 node to a Ray **head** on another
machine on the LAN.

**The WSL2 NAT problem.** By default WSL2 sits behind a NAT (`172.31.x.x`).
A worker started there advertises that private IP, which the head cannot
route back to — so the head's GCS health-check fails and the raylet exits
with *"this node manager has mistakenly been marked as dead by the GCS"*.
The fix is **mirrored networking**, which gives the WSL distro the Windows
host's LAN IP (`192.168.0.x`) directly. Requires Windows 11 22H2+.

**a. Enable mirrored networking** — `networkingMode=mirrored` goes under the
same `[wsl2]` block as the resource limits from §2. `Set-Content` **replaces**
the whole file, so write all the keys you want in one go. In PowerShell:

```powershell
@"
[wsl2]
memory=16GB
processors=8
swap=4GB
networkingMode=mirrored
"@ | Set-Content -Encoding ASCII $env:USERPROFILE\.wslconfig
```

Adjust (or drop) the `memory`/`processors`/`swap` lines to match your host.
To check the result: `Get-Content $env:USERPROFILE\.wslconfig`.

**b. Allow inbound traffic to WSL** — in an **elevated** PowerShell (mirrored
mode subjects WSL traffic to the Windows Firewall):

```powershell
Set-NetFirewallHyperVVMSetting -Name '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -DefaultInboundAction Allow
```

**c. Restart WSL** and verify the node now has a LAN IP, not `172.31.x`:

```powershell
wsl --shutdown
```
```bash
ip -4 addr show eth0 | grep inet      # expect 192.168.0.x
```

**d. Join the head** (its Python must also be 3.12.3 — see §3):

```bash
source .venv/bin/activate
ray start --address=<head_ip>:6379
```

#### 7. Editor (optional)

Install **VS Code + "Remote — WSL"** extension. From the Ubuntu shell:

```bash
code .
```

Opens VS Code wired into WSL2 — editing, terminal, debugging all run
inside the Linux env.

**Shortcut from Windows Explorer:** with the repo folder open in Explorer,
type `code .` in the address bar and press Enter — VS Code launches on that
folder without opening a shell first.

**Check / switch where it runs:** the indicator at the **bottom-left** of
the VS Code status bar shows the current context — for WSL it reads
**`WSL: Ubuntu-22.04`**. If it's blank (plain Windows), click it and choose
*Reopen Folder in WSL* so the integrated terminal, Python interpreter and
debugger all run inside Linux. Always confirm it says `WSL: …` before
running — a Windows-side terminal won't find the `.venv`.

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
├── time_step.py                # time-step + step-count (S.1503-4 §D.4)
├── geometry.py                 # α / X angle + orbital geometry (Numba)
├── antenna.py                  # ITU antenna patterns (GSO / BSS earth stations)
├── pfd_mask.py                 # PFD mask read + interpolation (CSV / XML)
├── mask_converter.py           # PFD mask α/Δlon ↔ Az/El conversion
├── mask_generator.py           # parametric PFD mask generator (Part C: Az/El Option 2 + native α/Δlon Option 1)
├── wcg_search.py               # Worst-Case Geometry search (S.1503-4 §D.3)
├── epfd_calculator.py          # EPFD↓ computation + statistics (S.1503-4)
├── epfd_stream_accumulator.py  # O(1)-memory streaming EPFD↓ accumulator
├── srs_reader.py               # SRS/ITU .mdb reader (pure-Python access-parser; also YAML SRS-equivalent)
├── constellation_templates.py  # parametric NGSO patterns (Walker, ring, train, Molniya, Tundra, IGSO, multi-shell)
├── mdb_writer.py               # write manual system → real JET4 .mdb pair (Java/Jackcess helper)
├── exceptions.py               # domain exceptions (NoValidGeometry, InvalidManualGeometry)
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
│   ├── C_Constellation.py  # Constellation / footprint viewer (WCG↓ geometry check + equator graticule)
│   ├── E_Manual_System.py  # Manual / parametric NGSO entry + template wizard + register-as-filing
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
│   ├── exports.py          # XLSX exports (campaign reports + ITU-style per-run workbook)
│   ├── result_artifacts.py # per-run CSV/PNG artifacts (CCDF, histogram, timeseries, geometries, Table 17, maps)
│   ├── report.py           # S.1503-4 §D7.3 examination summary.html (statement + Table 17 + CDF)
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

### `data/` directory (runtime state — gitignored)

Created on first launch under `streamlit_app/data/`. Paths are defined in
`streamlit_app/lib/__init__.py` (`DB_PATH`, `UPLOADS_DIR`, `RUNS_DIR`,
`EXPORTS_DIR`). All produced artifacts are stored **per run** under
`runs/<run_id>/`.

```
data/
├── sharc_orbit.db            # SQLite index: runs / systems / uploads
│                             #   (status, kind, params_json, result_path, …)
├── uploads/<upload_id>/      # uploaded SRS + mask filings (.mdb / .accdb / .xml)
├── runs/<run_id>/            # per-run artifacts
│   ├── params.json           # input parameters of the run
│   ├── summary.json          # quick metrics (compliance, max EPFD, percentiles)
│   ├── sim_data.json         # full result artifact read by the Results page:
│   │                         #   CCDF, decimated EPFD↓ timeline, WCG, Article 22 /
│   │                         #   Resolution 76 detail, hardware, timing, and
│   │                         #   dual_time_step { mode, fine_step_s, coarse_step_s,
│   │                         #   ncoarse, num_time_steps, n_fine_steps_executed,
│   │                         #   n_coarse_steps_executed, n_exec_steps }
│   ├── ccdf_epfd.csv         # CCDF table (EPFD level vs % time exceeded) — A2.1 unit headers
│   ├── epfd_histogram.csv    # PDF/histogram behind the CCDF (0.1 dB bins, §D7.1.1)
│   ├── epfd_timeseries.csv   # decimated EPFD↓ vs time trace (→ .csv.gz over 50k pts)
│   ├── geometries.csv        # tested WCG geometries (ES lat/lon + GSO longitude)
│   ├── table17.csv           # §D7.3.2 Table 17 (per Article 22 point: Ji, Pi, Py, pass)
│   ├── ccdf.png              # CCDF curve image
│   ├── histogram.png         # histogram image
│   ├── map.png               # tested-geometry map (plate-carrée)
│   ├── summary.html          # §D7.3 examination summary (statement + Table 17 + CDF)
│   ├── worker.log            # run log (streamed to the Status page)
│   └── worker.pid            # worker PID (present only while running)
├── exports/                  # generated XLSX / campaign export files
├── cluster.json              # Ray cluster mode + head endpoint
└── state_*.json              # persisted UI form / selection state
```

A run that only has `params.json` + `worker.pid` is still in progress (or
failed before writing results); a completed run has `summary.json`,
`sim_data.json` and `worker.log`, plus the per-run CSV/PNG artifacts and
`summary.html` above — written best-effort, so a plotting error never fails an
otherwise-finished run. The results `.xlsx` workbook and the Download-all
`.zip` are built on demand from the Results page, not stored on disk.

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
| Run ends with "no valid geometry" | The WCG↓ search cleared no worst-case GSO geometry against the store criteria (min elevation ε₀, εGSO, exclusion angle α₀, PFD mask) under the current knobs — a legitimate outcome, not a crash | Relax the gating knobs named in the message (min elevation / exclusion angle), widen the geometry grid, or check the filing's band / mask |
| Manual system "invalid manual geometry" | A manual earth-station / GSO coordinate is out of range | Enter valid earth-station lat/lon and a valid GSO longitude on the Manual system page |

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
- Manual / parametric NGSO systems (**Manual system** page): constellation-
  template wizard (Walker δ/★, equatorial ring, train, Molniya, Tundra, IGSO,
  multi-shell), editable plane list, 3D preview, attach a PFD mask, and launch
  EPFD↓ through the same engine path as MDB filings — plus register the system
  as a real `.mdb` filing pair (Java/Jackcess) or a YAML+XML filing
- Parametric PFD-mask generator (**Mask generator** page, S.1503-4 Part C):
  native Option 1 (α×Δlon) and Option 2 (Az/El) envelopes, GSO-arc mitigation
  (beam-off / α-cutoff) and operating-latitude band, round-trippable C4.2 XML
- SNS mutually-exclusive configurations: reads `multi_config_type` /
  `nbr_config` / `orbit_set_id`, shows an S/M badge on Single-entry, and lets
  you pick which orbit-set config is simulated (distinct 'M' configs are never
  summed)
- Per-run result artifacts + S.1503-4 §D7.3 report stack: CCDF / histogram /
  timeseries / geometries / Table 17 CSVs and PNGs, a `summary.html` (Pass/Fail
  statement + Table 17 + CDF), an ITU-style multi-sheet XLSX, and a
  Download-all `.zip` on Results
- **Constellation** viewer: WCG↓ geometry-validation feedback and an
  emphasized-equator graticule on both 3D globes
- Graceful `NoValidGeometry` outcome — structured diagnostics instead of a
  traceback when the WCG search clears no geometry

Pending:
- Docker image for one-command worker deployment
- XLSX exporter v2: metrics / percentiles / compliance / cross-method
  comparison (current export is a raw DB dump)
- Multi-user / auth gating (intentionally out of scope for this build)
