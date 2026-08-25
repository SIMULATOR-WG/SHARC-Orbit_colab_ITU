# -*- coding: utf-8 -*-
"""run_aggregate_method4.py — the aggregate study's method 4, run locally.

Method 4 (`_run_method_4`): compute each filing's own WCG g_i, then simulate
EVERY system at each g_i and convolve the per-system CCDFs there; the reported
aggregate is the worst of those geometries. With N filings that is N WCGA
searches plus N**2 fixed-geometry simulations, so the cost grows quadratically
in the number of systems — 3 filings is 9 simulations, 4 is 16.

The reference run was produced elsewhere with 3 filings (Starlink 3X,
Sailspace, OneWeb) and its params carry that machine's Linux paths. This
script rebuilds the same parameter set against the filings registered in THIS
installation, resolved through the app's own systems table so the SRS/mask
pairing cannot drift from what the UI would use.

    python scripts/run_aggregate_method4.py list
    python scripts/run_aggregate_method4.py run
    python scripts/run_aggregate_method4.py run --add 9bc1e72b490c
    python scripts/run_aggregate_method4.py report

`--add` takes a system id from `list` and appends it as the fourth filing.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from streamlit_app.lib import storage  # noqa: E402

OUT = REPO / "streamlit_app" / "data" / "campaigns" / "aggregate_method4"

#: The three systems of the published aggregate, by the system id registered
#: here. Pinned by id rather than by NTC because this installation holds more
#: than one filing per NTC (323520263 exists in a 10700+14000 and an 11700
#: version) and picking the wrong one would be silent.
BASE_SYSTEMS: list[tuple[str, str]] = [
    ("1c5dc7c02695:323520263:_", "Starlink USASAT-NGSO-3X (10700 and 14000)"),
    ("c609543708a8:323520044:_", "Sailspace"),
    ("fabb40a2b4e5:101:_", "OneWeb Ku"),
]

#: Exactly the reference run's parameters — see the aggregate's metodo4
#: params.json. Kept verbatim so this run is comparable to it.
COMMON: dict[str, Any] = {
    "method": "method_4",
    "service": "FSS",
    "simulation_frequency_ghz": 10.70002,
    "es_antenna_diameter_m": 1.2,
    "reference_bandwidth_khz": 40.0,
    "truncate_tail": True,
    "wcga_s1503": True,
    "wcga_no_mask_symmetry": False,
    "gso_longitude_mode": "arc_optimal",
    "alpha_method": "analytical",
    "dual_time_step_mode": "off",
    "s1503_step_deg": 0.1,
    "n_jobs": 10,
}


def _systems_by_id() -> dict[str, dict]:
    return {r["id"]: r for r in storage.list_systems()}


def _filing_of(row: dict) -> dict[str, Any]:
    srs, mask = row.get("srs_path"), row.get("mask_path")
    if not srs or not Path(srs).is_file():
        raise SystemExit(f"SRS missing for {row['id']}: {srs}")
    if not mask or not Path(mask).is_file():
        raise SystemExit(f"mask missing for {row['id']}: {mask}")
    return {"ntc_id": str(row.get("ntc_id")),
            "srs_path": str(srs), "mask_path": str(mask)}


def _selected(args) -> list[tuple[str, str]]:
    sel = list(BASE_SYSTEMS)
    for sid in (getattr(args, "add", None) or []):
        if any(sid == s for s, _ in sel):
            continue
        sel.append((sid, "added via --add"))
    return sel


def cmd_list(args) -> None:
    known = _systems_by_id()
    print("the aggregate's three systems, as resolved here:")
    for sid, label in BASE_SYSTEMS:
        row = known.get(sid)
        ok = "OK " if row else "MISSING"
        print(f"  [{ok}] {sid:26s} {label}")
        if row:
            print(f"          srs  {Path(row['srs_path']).name}")
            print(f"          mask {Path(row.get('mask_path') or '—').name}")
    print("\nother systems registered here (candidates for a 4th filing):")
    for sid, row in sorted(known.items()):
        if any(sid == s for s, _ in BASE_SYSTEMS):
            continue
        print(f"  {sid:26s} ntc={str(row.get('ntc_id')):10s} "
              f"{Path(row.get('srs_path') or '—').name[:52]}")
    n = len(_selected(args))
    print(f"\nselected: {n} filing(s) → {n} WCGA search(es) + {n * n} "
          f"fixed-geometry simulation(s)")


def cmd_run(args) -> None:
    known = _systems_by_id()
    sel = _selected(args)
    filings = []
    for sid, label in sel:
        row = known.get(sid)
        if row is None:
            raise SystemExit(f"system not registered here: {sid}")
        filings.append(_filing_of(row))
        print(f"  filing: {sid}  ({label})")

    d = OUT / f"n{len(filings)}"
    d.mkdir(parents=True, exist_ok=True)
    params = dict(COMMON)
    params["filings"] = filings
    params["result_path"] = str(d)
    if args.n_jobs:
        params["n_jobs"] = int(args.n_jobs)
    (d / "params.json").write_text(json.dumps(params, indent=2),
                                   encoding="utf-8")
    print(f"\nmethod_4 · {len(filings)} filing(s) · "
          f"{len(filings)**2} geometry simulations · out {d}")
    if args.dry_run:
        print(json.dumps({k: v for k, v in params.items() if k != "filings"},
                         indent=2))
        return

    t0 = time.time()
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    log = d / "worker.log"
    print(f"started {time.strftime('%H:%M:%S')} · full log {log}")
    with open(log, "w", encoding="utf-8") as lf:
        proc = subprocess.Popen(
            [sys.executable, "-m",
             "streamlit_app.lib.job_runners.s1588_worker",
             str(d / "params.json")],
            cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env=env, bufsize=1)
        for line in proc.stdout:
            lf.write(line)
            lf.flush()
            if any(k in line for k in ("[method_4]", "PROGRESS", "WCG",
                                       "ERROR", "Traceback", "DONE")):
                print("  " + line.rstrip()[:160])
        proc.wait()
    print(f"exit {proc.returncode} · {(time.time()-t0)/3600:.2f} h")


def cmd_report(args) -> None:
    for d in sorted(OUT.glob("n*")):
        f = d / "summary.json"
        if not f.is_file():
            print(f"{d.name}: not finished")
            continue
        s = json.loads(f.read_text(encoding="utf-8"))
        print(f"\n=== {d.name} ===")
        print(f"  systems: {s.get('n_systems')}  ·  "
              f"aggregate max: {s.get('max_epfd_dbw')}")
        sim = d / "sim_data.json"
        if sim.is_file():
            data = json.loads(sim.read_text(encoding="utf-8"))
            for e in data.get("per_wcg", []):
                print(f"    WCG {e['wcg_index']}: "
                      f"ES {e['es_lat_deg']:.4f}, {e['es_lon_deg']:.4f} · "
                      f"GSO {e['gso_lon_deg']:.4f} · "
                      f"max {e.get('max_epfd_dbw')}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("list", "run", "report"):
        sp = sub.add_parser(name)
        if name in ("list", "run"):
            sp.add_argument("--add", nargs="*", default=[],
                            help="system id(s) to append as extra filing(s)")
        if name == "run":
            sp.add_argument("--n-jobs", type=int, default=None)
            sp.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    {"list": cmd_list, "run": cmd_run, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
