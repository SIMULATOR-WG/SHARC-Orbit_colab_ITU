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
