"""SHARC-Orbit Streamlit UI helpers.

Importable namespace for shared modules: engine, storage, state, plots,
workers, filings, exports, theme. Only depends on stdlib, pip
dependencies in requirements.txt, and the numerical engine at ../src/.

Never imports any client-server / auth stack.
"""
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = APP_ROOT.parent
ENGINE_ROOT = REPO_ROOT / "src"
DATA_ROOT = APP_ROOT / "data"
DB_PATH = DATA_ROOT / "sharc_orbit.db"
UPLOADS_DIR = DATA_ROOT / "uploads"
RUNS_DIR = DATA_ROOT / "runs"
EXPORTS_DIR = DATA_ROOT / "exports"

for d in (DATA_ROOT, UPLOADS_DIR, RUNS_DIR, EXPORTS_DIR):
    d.mkdir(parents=True, exist_ok=True)


def git_revision() -> dict[str, str | bool]:
    """Current Git branch + short SHA of this checkout.

    Returns ``branch``, ``sha``, ``dirty``, ``label`` (e.g. ``main · a1b2c3d``).
    Detached HEAD uses the SHA as the branch label. Uncommitted changes
    append ``*``. If Git is unavailable, ``label`` is ``dev``.
    """
    import subprocess  # noqa: PLC0415

    def _git(*args: str) -> str:
        try:
            r = subprocess.run(
                ["git", "-C", str(REPO_ROOT), *args],
                capture_output=True, text=True, timeout=2, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        if r.returncode != 0:
            return ""
        return (r.stdout or "").strip()

    sha = _git("rev-parse", "--short", "HEAD")
    if not sha:
        return {"branch": "unknown", "sha": "dev", "dirty": False, "label": "dev"}
    branch = _git("rev-parse", "--abbrev-ref", "HEAD") or "HEAD"
    if branch == "HEAD":
        branch = sha
    dirty = bool(_git("status", "--porcelain"))
    label = f"{branch} · {sha}"
    if dirty:
        label += "*"
    return {"branch": branch, "sha": sha, "dirty": dirty, "label": label}


def app_version() -> str:
    """Label shown on the home page: Git branch · short SHA."""
    return str(git_revision()["label"])
