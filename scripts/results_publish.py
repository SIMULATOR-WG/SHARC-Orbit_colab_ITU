# -*- coding: utf-8 -*-
"""Publish finished run folders to a shared results directory.

A campaign may run on several machines. Copying each finished run folder
into a synced folder (e.g. OneDrive) makes every machine's output appear in
one place and keeps it backed up off the working checkout.

**Copy after the run, never write into the synced folder.** Pointing
``result_path`` at the synced folder looks simpler and is the one thing to avoid: the
sync client holds handles on files it is uploading and uploads them while they
are still being written, so a three-hour run risks a PermissionError
mid-write, and the other machine can see a truncated CSV that looks exactly
like a finished result. Copying a completed folder has neither problem.

**The database is never published.** ``streamlit_app/data/sharc_orbit.db`` is
one SQLite file; two machines writing it through a sync client corrupts it.
Only run directories are copied.

Destination resolution, in order:
  1. ``SHARC_RESULTS_DIR`` — the destination root on this machine
     (e.g. a folder inside your OneDrive).
  2. ``DEFAULT_RESULTS_DIR`` below, if it exists.
  3. Nothing — publishing is skipped with a warning, never fatal. A sync
     problem must not fail a simulation that already succeeded.

Layout at the destination — ``<root>/<campaign>/<row>__<run_id>/`` — because
``runs/9cf5cb094e3d`` is unnavigable once there are a hundred of them, while
``newwcg_10ghz/C1_dfull__b0e96206e4e1`` says what it is.

    python scripts/results_publish.py --all           # backfill everything
    python scripts/results_publish.py --run 9cf5cb094e3d
    python scripts/results_publish.py --where         # show the destination
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNS_DIR = REPO / "streamlit_app" / "data" / "runs"

DEFAULT_RESULTS_DIR = Path.home() / "OneDrive" / "SHARC-Orbit results"

#: Windows resolves paths under 260 chars without the \\?\ prefix. A deep
#: destination root leaves little room, so the per-run subpath has to stay
#: short or long artifact names start failing to copy.
_MAX_PATH = 259


def results_root() -> Path | None:
    """The directory to publish into, or None when none is available."""
    env = os.environ.get("SHARC_RESULTS_DIR", "").strip()
    if env:
        p = Path(env)
        try:
            p.mkdir(parents=True, exist_ok=True)
            return p
        except OSError:
            return None
    return DEFAULT_RESULTS_DIR if DEFAULT_RESULTS_DIR.is_dir() else None


def _label_for(run_dir: Path) -> tuple[str, str]:
    """``(campaign, row)`` for a run, read from its own params/DB record."""
    campaign = row = ""
    try:
        p = json.loads((run_dir / "params.json").read_text(encoding="utf-8"))
        # A campaign row carries the filing and the geometry it pinned; the
        # human-facing label is the state key, which only the campaign script
        # knows, so fall back to what params can tell us.
        campaign = str(p.get("campaign_id") or "")
        row = str(p.get("campaign_row") or "")
    except Exception:  # noqa: BLE001
        pass
    if not campaign:
        try:
            sys.path.insert(0, str(REPO))
            from streamlit_app.lib import storage  # noqa: PLC0415
            rec = storage.get_run(run_dir.name) or {}
            campaign = str(rec.get("campaign_id") or "")
        except Exception:  # noqa: BLE001
            pass
    return (campaign or "unassigned"), row


def publish_run(run_id: str, row: str = "", campaign: str = "",
                quiet: bool = False) -> Path | None:
    """Copy one finished run folder to the results directory.

    Idempotent: an already-published run is refreshed only if the source is
    newer, so backfilling twice costs nothing. Returns the destination, or
    None when publishing was skipped.
    """
    root = results_root()
    if root is None:
        if not quiet:
            print("  [publish] no results directory available "
                  "(set SHARC_RESULTS_DIR) — skipped")
        return None
    src = RUNS_DIR / run_id
    if not (src / "sim_data.json").is_file():
        # No sim_data means the run did not finish. Publishing it would put a
        # folder that looks like a result in front of the user.
        if not quiet:
            print(f"  [publish] {run_id}: unfinished (no sim_data) — skipped")
        return None
    if not campaign or not row:
        c, r = _label_for(src)
        campaign = campaign or c
        row = row or r
    name = f"{row}__{run_id}" if row else run_id
    dst = root / campaign / name
    if len(str(dst)) + 40 > _MAX_PATH:      # 40 = room for artifact names
        dst = root / campaign / run_id      # drop the label rather than fail
    try:
        if dst.exists():
            newest_src = max(f.stat().st_mtime for f in src.rglob("*")
                             if f.is_file())
            newest_dst = max((f.stat().st_mtime for f in dst.rglob("*")
                              if f.is_file()), default=0.0)
            if newest_dst >= newest_src:
                if not quiet:
                    print(f"  [publish] {name}: already current")
                return dst
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst, dirs_exist_ok=True)
        if not quiet:
            size = sum(f.stat().st_size for f in dst.rglob("*") if f.is_file())
            print(f"  [publish] {campaign}/{name} ({size / 1e6:.1f} MB)")
        return dst
    except OSError as exc:
        # Never fail a finished simulation over a sync problem.
        print(f"  [publish] WARNING {run_id}: {exc}")
        return None


def publish_all(quiet: bool = False) -> int:
    n = 0
    for src in sorted(RUNS_DIR.iterdir() if RUNS_DIR.is_dir() else []):
        if src.is_dir() and publish_run(src.name, quiet=quiet):
            n += 1
    return n


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="publish every run")
    ap.add_argument("--run", action="append", default=[], metavar="RUN_ID")
    ap.add_argument("--where", action="store_true",
                    help="print the destination and exit")
    args = ap.parse_args()
    root = results_root()
    if args.where or not (args.all or args.run):
        print(f"results directory: {root or '(none available)'}")
        if root:
            n = sum(1 for _ in root.rglob("sim_data.json"))
            print(f"  runs already published there: {n}")
        return
    if args.run:
        for r in args.run:
            publish_run(r)
    if args.all:
        print(f"published {publish_all()} run(s) to {root}")


if __name__ == "__main__":
    main()
