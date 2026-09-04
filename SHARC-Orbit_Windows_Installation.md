# SHARC-Orbit — Native Windows installation

Windows PowerShell · Python 3.12.3 · uv · Streamlit · local standalone mode.
Companion to the root [`README.md`](README.md).

**[1. Install](#first-installation) · [2. Run](#executing) · [3. Update](#updating) · [4. Troubleshooting](#troubleshooting) · [5. Special cases](#special-cases)**

> Commands copied from a PDF may wrap across lines — the wraps are visual only.
> Prefer copying from this Markdown file.

---

<a id="first-installation"></a>

## 1. Install (first time)

One-time procedure, in PowerShell (no Administrator needed). If this machine
already has a working clone, go to [§ 3. Update](#updating) instead.

**a. Install the tools** — Git, GitHub CLI, uv — then **close and reopen
PowerShell** so `PATH` refreshes:

```powershell
winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
winget install --id GitHub.cli -e --source winget --accept-package-agreements --accept-source-agreements
winget install --id astral-sh.uv -e --source winget --accept-package-agreements --accept-source-agreements
```

**b. Sign in to GitHub and clone.** The browser opens — sign in with an account
that has access to `SIMULATOR-WG/SHARC-Orbit` and authorize GitHub CLI:

```powershell
gh auth login --hostname github.com --git-protocol https --web

New-Item -ItemType Directory -Force -Path "$HOME\Documents\Python\Projetos" | Out-Null
Set-Location "$HOME\Documents\Python\Projetos"
gh repo clone SIMULATOR-WG/SHARC-Orbit sharc-orbit
Set-Location .\sharc-orbit
```

**c. Python 3.12.3, venv, dependencies.** uv provides the interpreter — do not
install Python from the Microsoft Store or python.org. The exact patch
**3.12.3** is required (`3.12` alone can pick a newer patch):

```powershell
uv python install 3.12.3
uv venv --python 3.12.3 .venv
uv pip install --python .\.venv\Scripts\python.exe -r requirements.txt
```

Verify:

```powershell
& .\.venv\Scripts\python.exe --version
& .\.venv\Scripts\python.exe -c "import streamlit, plotly, numpy, pandas, scipy, numba, matplotlib, ray, access_parser; print('dependencies OK')"
```

Expect `Python 3.12.3` and `dependencies OK`.

**d. Run:**

```powershell
$env:PYTHONPATH = (Get-Location).Path
& .\.venv\Scripts\python.exe -m streamlit run .\streamlit_app\app.py
```

The browser opens at `http://localhost:8501`. Stop with `Ctrl+C`. Done.

Two rules behind the commands above:

- Never activate `.venv`. Calling `.venv\Scripts\python.exe` directly sidesteps
  PowerShell `ExecutionPolicy` entirely.
- `PYTHONPATH` must be the repository root (the engine lives in `src/`) and is
  per-window — set it in every new terminal, or use the start script below.

Once installed, SHARC-Orbit makes **no outbound network connection**. Internet
is needed only to install and to update.

---

<a id="executing"></a>

## 2. Run (day to day)

```powershell
Set-Location "$HOME\Documents\Python\Projetos\sharc-orbit"
$env:PYTHONPATH = (Get-Location).Path
& .\.venv\Scripts\python.exe -m streamlit run .\streamlit_app\app.py
```

### Optional start script

Create `run_sharc_orbit.ps1` once, in the repository root:

```powershell
@'
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONPATH = $PSScriptRoot
& "$PSScriptRoot\.venv\Scripts\python.exe" -m streamlit run "$PSScriptRoot\streamlit_app\app.py"
'@ | Set-Content -Encoding UTF8 .\run_sharc_orbit.ps1
```

Run it with:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\run_sharc_orbit.ps1
```

### MDB files

Send SRS / mask `.mdb` filings through the app's **Upload** page — never copy
them into `streamlit_app\data\uploads\` by hand. The app registers each filing
in its database on upload; a file dropped in the folder will not appear in the
UI.

---

<a id="updating"></a>

## 3. Update

Stop Streamlit (`Ctrl+C`), then:

```powershell
Set-Location "$HOME\Documents\Python\Projetos\sharc-orbit"
git status
git pull --ff-only origin main
uv pip install --python .\.venv\Scripts\python.exe -r requirements.txt
```

Then run as in [§ 2](#executing).

- `git status` should show a clean tree, or only untracked files you added
  (such as `run_sharc_orbit.ps1` or `data\exports\`) — those never block the
  update. Modified **tracked** files: see [§ 4](#troubleshooting).
- Uploads, run history and the app database under `streamlit_app\data\`
  survive the update.
- Do **not** clone a second copy, do **not** use `git reset --hard`, and if
  `git pull --ff-only` fails do **not** retry with a plain `git pull` — see
  [§ 4](#troubleshooting).

---

<a id="troubleshooting"></a>

## 4. Troubleshooting

| Problem | Fix |
|---|---|
| `winget` / `git` / `gh` / `uv` not recognized | Close and reopen PowerShell. For `winget` itself: install **App Installer** from the Microsoft Store |
| `repository not found` | `gh auth status`, then `gh auth login --hostname github.com --git-protocol https --web`. Org uses SSO: `gh auth refresh -h github.com` and authorize in the browser |
| `Your local changes … would be overwritten` on pull | Tracked files were edited locally — copy them aside or commit them on another branch, then pull again |
| `Not possible to fast-forward` | Local `main` has commits GitHub does not: `git switch -c backup-local-main`, then ask whoever maintains the clone. Never force with plain `git pull` or `reset --hard` |
| `.venv` Python is not 3.12.3 | Stop Streamlit first (Windows locks a running `.venv` — *"being used by another process"*), then `Remove-Item -Recurse -Force .\.venv` and redo § 1c |
| `No module named src` | Run from the repository root and set `$env:PYTHONPATH = (Get-Location).Path` |
| `No module named streamlit` / other import errors | Re-run the dependency install (§ 1c, last command) |
| Port 8501 busy | Append `--server.port 8502` to the run command and open `http://localhost:8502` |
| First run is slow | Numba compiles on first call — expected; later calls are fast |
| `Activate.ps1` blocked by ExecutionPolicy | Activation is never needed — call `.venv\Scripts\python.exe` directly |
| "0 notices detected" on Upload | The file is not a valid SRS MDB (or `non_geo` is absent) |
| Dependency compile error mentioning C/C++ | Install **Microsoft Visual C++ Build Tools** — only if the error explicitly asks for it |

---

<a id="special-cases"></a>

## 5. Special cases

Skip this section unless one of the headings applies to you.

### Restricted network

Allowlist five hosts (HTTPS/443) — needed **only during install and update**:
see [README — Restricted network](README.md#restricted-network--hosts-to-allowlist).
On such networks also:

```powershell
# uv otherwise tries Astral's CDN before GitHub and can stall on a silent drop:
$env:UV_PYTHON_INSTALL_MIRROR = "https://github.com/astral-sh/python-build-standalone/releases/download"
```

Those five are what SHARC-Orbit itself needs. Git, GitHub CLI and `uv` are
prerequisites — if `winget` cannot reach them on that network, ask whoever
administers it to install the three tools, then continue from § 1b.

<a id="offline-install"></a>

### Offline / air-gapped machine

Prepare both pieces on a connected Windows machine with the same Python 3.12.3:

```powershell
# 1. dependency wheelhouse (pip wheel builds the one sdist-only package, access-parser):
uv pip install --python .\.venv\Scripts\python.exe pip
& .\.venv\Scripts\python.exe -m pip wheel -r requirements.txt -w .\wheels

# 2. interpreter: uv python dir prints where the managed CPython lives
uv python dir
```

Copy `wheels\` next to the clone and the `cpython-3.12.3-*` folder to the same
`uv python dir` location on the offline machine, then:

```powershell
uv venv --python 3.12.3 .venv
uv pip install --python .\.venv\Scripts\python.exe --offline --no-index --find-links .\wheels -r requirements.txt
```

### Writing real `.mdb` filings (JDK)

Only the **Manual system → Register as filing** button writes real JET4 `.mdb`
pairs, via the Java helper in `tools\jackcess\`. It needs a full JDK
(`jdk.compiler` module — a JRE is not enough):

```powershell
winget install --id Microsoft.OpenJDK.21 -e --source winget --accept-package-agreements --accept-source-agreements
```

Without a JDK the button falls back to a YAML + mask-XML filing; everything
else works unchanged. Nothing else needs Java.

### Joining a Ray cluster from Windows

Standalone (default) needs none of this. To join a Ray head on another node:

```powershell
$env:RAY_ENABLE_WINDOWS_OR_OSX_CLUSTER = "1"   # Ray refuses Windows multi-node without it
ray start --address=<HEAD_IP>:6379
```

The Python patch must match the head exactly — 3.12.3, which § 1c already
gives you. Ray marks Windows clustering experimental; prefer WSL2 or Linux
for multi-node work
([README — Joining a Ray cluster from Windows](README.md#joining-a-ray-cluster-from-windows)).

### Windows vs Linux notes

- Workers start with `spawn` (not `fork`): slightly slower cold start, same
  results.
- `src\launcher_ui.py` and the `--kill-port` helper are Unix-only leftovers —
  the app uses neither.
- Dependencies come from the root `requirements.txt` only. There is no root
  `pyproject.toml`; do not run `uv sync` (the `streamlit_app/pyproject.toml`
  stub omits `ray` and `access-parser`).
