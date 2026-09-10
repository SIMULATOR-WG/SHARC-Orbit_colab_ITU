# -*- coding: utf-8 -*-
"""run_contrib5_individual.py — single-entry runs for the four Contribution-5
filings, at Brazil's constrained WCG and at a fixed point.

These are INDIVIDUAL (single-entry) simulations, one per filing per geometry —
not an aggregate. No convolution, no joint run: each filing is simulated on its
own and its own CCDF is what comes out. The aggregate options A-D of the
contribution are a separate exercise.

Geometries. The unconstrained per-system WCG (Option A) is already in hand and
is NOT re-run here. What is missing is:

  brazil  the WCGA with the earth-station domain restricted to Brazil
          (country_wcg_worker, country_codes=["BRA"]). One search per filing,
          so each system gets its own Brazil worst case.
  fixed   a single pinned geometry, ES -10.3333 / -53.2, GSO -53.2 — central
          Brazil, roughly the country's geometric centre. No search.

That is 4 filings x 2 geometries = 8 runs, numbered 1-8 so they can be split
across machines by index:

    python scripts/run_contrib5_individual.py list
    python scripts/run_contrib5_individual.py run --range 1-4    # this machine
    python scripts/run_contrib5_individual.py run --range 5-8    # another machine
    python scripts/run_contrib5_individual.py run --only 3 7
    python scripts/run_contrib5_individual.py report

Common parameters, from the contribution's §3: frequency run at 10 700.02 MHz,
reference bandwidth 40 kHz, victim GSO FSS earth station with a 1.2 m antenna
(Rec. ITU-R S.1428-1), time step 5 s over one month (518 400 steps, the Stage 2
convention). Nco comes from each filing's own sat_oper (1, 1, 40 and 1 for 3X,
3N, OneWeb L5 and SAILSPACE-1) and is therefore NOT overridden here — note
OneWeb's 40, which is what makes its single-entry statistics differ in kind
from the others'.

Finished runs are published to the shared results directory automatically
(see scripts/results_publish.py), so every machine's output ends up in one
place without hand-copying.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
for _p in (REPO, REPO / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import run_1503_campaign as camp  # noqa: E402
from results_publish import publish_run  # noqa: E402
from streamlit_app.lib import storage  # noqa: E402

CAMPAIGN_ID = "contrib5_individual"
camp.use_campaign(CAMPAIGN_ID)

#: §3 of the contribution. The victim is 1.2 m for every row: the comparison is
#: between systems, so the earth station has to be the same one throughout.
COMMON: dict[str, Any] = {
    "service": "FSS",
    "run_static_es": False,
    "es_antenna_diameter_m": 1.2,
    "simulation_frequency_ghz": 10.70002,
    "reference_bandwidth_khz": 40.0,
    # 5 s over one month. `time_step_s` fixes the step; without it the engine
    # derives its own from §D4 and the run would not be the Stage 2 convention.
    "time_step_s": 5.0,
    "num_time_steps": 518_400,
    "dual_time_step_mode": "off",
}

#: The fixed geometry: ES lat, ES lon, GSO lon.
FIXED_GEOMETRY = (-10.3333, -53.2, -53.2)

#: Brazil, as the country-polygon dataset names it.
BRAZIL = ["BRA"]

#: The four filings of §3, by the system id registered in this installation.
#: Pinned by id, not by NTC: 323520263 exists here in both a "10700 and 14000"
#: and an "11700" version, and 119520228 in several, so resolving by NTC would
#: silently pick whichever came first.
FILINGS: list[dict[str, str]] = [
    {"key": "3X", "system_id": "1c5dc7c02695:323520263:_",
     "name": "USASAT-NGSO-3X", "notice": "323520263", "nco": "1"},
    {"key": "3N", "system_id": "16b4f057e268:119520228:_",
     "name": "USASAT-NGSO-3N (Config. 1)", "notice": "319520421",
     "nco": "1", "note": "examination data is notice 119520228"},
    {"key": "L5", "system_id": "fabb40a2b4e5:101:_",
     "name": "L5 (OneWeb)", "notice": "317520534", "nco": "40"},
    {"key": "SS1", "system_id": "c609543708a8:323520044:_",
     "name": "SAILSPACE-1", "notice": "323520044", "nco": "1"},
]

GEOMETRIES = ("brazil", "fixed")


def _matrix() -> list[dict[str, Any]]:
    """The eight rows, numbered 1-8. Geometry-major so that `--range 1-4` is
    all four filings at one geometry rather than half of each."""
    rows = []
    n = 0
    for geom in GEOMETRIES:
        for f in FILINGS:
            n += 1
            rows.append({"n": n, "geom": geom, **f,
                         "id": f"{n:02d}_{geom}_{f['key']}"})
    return rows


MATRIX = _matrix()


def _params_for(row: dict[str, Any]) -> dict[str, Any]:
    known = {r["id"]: r for r in storage.list_systems()}
    sys_row = known.get(row["system_id"])
    if sys_row is None:
        raise SystemExit(
            f"[{row['id']}] system {row['system_id']} is not registered on "
            f"this machine. `python scripts/run_contrib5_individual.py list` "
            f"shows what is available; register the filing in the UI first."
        )
    p = dict(COMMON)
    for k in ("srs_path", "mask_path", "mask_id", "ntc_id"):
        if sys_row.get(k) is not None:
            p[k] = sys_row[k]
    p["system_id"] = row["system_id"]
    if row["geom"] == "brazil":
        # Country-constrained WCGA: the search runs, but only over earth
        # stations inside Brazil.
        p.update({"wcga_s1503": True, "wcg_manual": False,
                  "country_codes": BRAZIL,
                  "s1503_step_deg": 0.1})
    else:
        lat, lon, gso = FIXED_GEOMETRY
        p.update({"wcga_s1503": False, "wcg_manual": True,
                  "wcg_manual_es_lat": lat, "wcg_manual_es_lon": lon,
                  "wcg_manual_gso_lon": gso, "wcg_manual_align": False})
    return p


def _worker_for(row: dict[str, Any]) -> str:
    return ("streamlit_app.lib.job_runners.country_wcg_worker"
            if row["geom"] == "brazil"
            else "streamlit_app.lib.job_runners.s1503_worker")


def _run_one(row: dict[str, Any], pos: str = "") -> tuple[str, bool]:
    """Mirrors camp._run_one but picks the worker per geometry — the Brazil
    rows need the country-constrained one."""
    params = _params_for(row)
    run_id = storage.create_run(kind="single", method=None, params=params,
                                campaign_id=CAMPAIGN_ID)
    run_dir = REPO / "streamlit_app" / "data" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    params["result_path"] = str(run_dir)
    (run_dir / "params.json").write_text(json.dumps(params, indent=2),
                                         encoding="utf-8")
    storage.update_run(run_id, status="running", progress_pct=0.0)
    log = run_dir / "worker.log"
    t0 = time.time()
    print(f"  [{row['id']}]{pos} started {time.strftime('%H:%M:%S')} · "
          f"run {run_id} · log {log}")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    with open(log, "w", encoding="utf-8") as lf:
        proc = subprocess.Popen(
            [sys.executable, "-m", _worker_for(row), str(run_dir / "params.json")],
            cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env=env, bufsize=1)
        last = 0.0
        for line in proc.stdout:
            lf.write(line)
            lf.flush()
            if any(k in line for k in ("WCG found", "ES: lat", "GSO: lon",
                                       "Single-entry EPFD", "PHASE",
                                       "Traceback", "ERROR", "no valid")):
                print("    " + line.rstrip()[:150])
            elif "%" in line and time.time() - last > 120:
                last = time.time()
                print("    " + line.rstrip()[:110])
        rc = proc.wait()
    ok = (rc == 0 and (run_dir / "summary.json").exists())
    storage.update_run(run_id, status=("success" if ok else "failed"),
                       progress_pct=(100.0 if ok else None),
                       error_message=("" if ok else f"exit code {rc}"),
                       finished_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    dt = time.time() - t0
    print(f"  [{row['id']}] {'OK' if ok else 'FAILED'} in "
          f"{dt / 3600:.2f} h (run {run_id})")
    if ok:
        try:
            publish_run(run_id, row=row["id"], campaign=CAMPAIGN_ID)
        except Exception as exc:  # noqa: BLE001
            print(f"  [{row['id']}] publish skipped: {exc}")
    return run_id, ok


def _selected(args) -> list[dict[str, Any]]:
    if args.range:
        lo, _, hi = args.range.partition("-")
        lo, hi = int(lo), int(hi or lo)
        return [r for r in MATRIX if lo <= r["n"] <= hi]
    if args.only:
        want = {int(x) for x in args.only}
        return [r for r in MATRIX if r["n"] in want]
    return list(MATRIX)


def cmd_list(args) -> None:
    known = {r["id"] for r in storage.list_systems()}
    state = camp._load_state()
    done = {k for k, v in (state.get("runs") or {}).items()
            if v.get("status") == "success"}
    print("four Contribution-5 filings, individually (single-entry), "
          "1.2 m victim, 10 700.02 MHz, 5 s x 518 400 steps\n")
    cur = None
    for r in MATRIX:
        if r["geom"] != cur:
            cur = r["geom"]
            where = ("WCGA restricted to Brazil (country_codes=BRA)"
                     if cur == "brazil"
                     else f"fixed geometry ES {FIXED_GEOMETRY[0]}, "
                          f"{FIXED_GEOMETRY[1]} · GSO {FIXED_GEOMETRY[2]}")
            print(f"── {cur}: {where}")
        flags = []
        if r["system_id"] not in known:
            flags.append("FILING NOT REGISTERED HERE")
        if r["id"] in done:
            flags.append("DONE")
        print(f"  {r['n']:2d}  {r['id']:18s} {r['name']:28s} "
              f"notice {r['notice']}  Nco={r['nco']}"
              + (f"   [{', '.join(flags)}]" if flags else ""))
    print("\nsplit by index, e.g. --range 1-4 here and --range 5-8 on the "
          "other machine.")
    missing = [r["n"] for r in MATRIX if r["system_id"] not in known]
    if missing:
        print(f"\nrows {missing} cannot run here: their filing is not "
              f"registered. Systems available:")
        for s in sorted(storage.list_systems(), key=lambda x: x["id"]):
            print(f"  {s['id']:26s} ntc={s.get('ntc_id')}")


def cmd_run(args) -> None:
    rows = _selected(args)
    state = camp._load_state()
    print(f"{CAMPAIGN_ID}: {len(rows)} row(s) · "
          f"{time.strftime('%Y-%m-%d %H:%M:%S')}")
    for i, row in enumerate(rows, 1):
        rec = (state.get("runs") or {}).get(row["id"])
        if rec and rec.get("status") == "success" and not args.force:
            print(f"  [{row['id']}] already done (run {rec['run_id']}) — "
                  f"skipping")
            continue
        geom = ("Brazil-constrained WCGA" if row["geom"] == "brazil"
                else f"fixed ES {FIXED_GEOMETRY[0]}, {FIXED_GEOMETRY[1]}")
        print(f"\n[{row['id']}] {row['name']} · {geom}")
        if args.dry_run:
            p = _params_for(row)
            print("  " + json.dumps({k: v for k, v in p.items()
                                     if "path" not in k}, indent=2))
            continue
        run_id, ok = _run_one(row, pos=f" ({i}/{len(rows)})")
        state.setdefault("runs", {})[row["id"]] = {
            "run_id": run_id, "status": "success" if ok else "failed"}
        camp._save_state(state)
        if not ok and not args.keep_going:
            raise SystemExit(f"[{row['id']}] failed — see its worker.log. "
                             "Use --keep-going to continue.")


def cmd_report(args) -> None:
    state = camp._load_state()
    out = []
    for key, rec in sorted((state.get("runs") or {}).items()):
        row = next((r for r in MATRIX if r["id"] == key), None)
        d = REPO / "streamlit_app" / "data" / "runs" / rec["run_id"]
        e: dict[str, Any] = {
            "id": key, "run_id": rec["run_id"], "status": rec.get("status"),
            "filing": row["name"] if row else "",
            "notice": row["notice"] if row else "",
            "geometry": row["geom"] if row else "",
        }
        try:
            sim = json.loads((d / "sim_data.json").read_text(encoding="utf-8"))
            w = sim.get("wcg") or {}
            comp = sim.get("compliance_detail") or {}
            e.update({
                "es_lat": w.get("es_lat_deg"), "es_lon": w.get("es_lon_deg"),
                "gso_lon": w.get("gso_lon_deg"),
                "max_epfd_dbw_40khz": sim.get("max_epfd_dbw_m2_40khz"),
                "worst_margin_db": comp.get("worst_margin_dB"),
                "worst_pct": comp.get("worst_percentage"),
                "compliant": sim.get("compliance"),
            })
            cc = sim.get("country_constrained_wcg")
            if cc:
                e["country"] = ",".join(cc.get("country_codes") or [])
        except Exception as exc:  # noqa: BLE001
            e["error"] = str(exc)
        out.append(e)
    cols = ["id", "filing", "notice", "geometry", "run_id", "status",
            "country", "es_lat", "es_lon", "gso_lon", "max_epfd_dbw_40khz",
            "worst_margin_db", "worst_pct", "compliant", "error"]
    camp.STATE_DIR.mkdir(parents=True, exist_ok=True)
    dst = camp.STATE_DIR / "contrib5_individual_results.csv"
    with open(dst, "w", newline="", encoding="utf-8") as fh:
        fh.write("# Contribution 5, INDIVIDUAL (single-entry) runs per filing. "
                 "Not an aggregate: no convolution, no joint run.\n")
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(out)
    print(f"wrote {dst} ({len(out)} rows)")
    for r in out:
        print(f"  {r['id']:18s} {str(r.get('status')):8s} "
              f"max={r.get('max_epfd_dbw_40khz')} "
              f"margin={r.get('worst_margin_db')} @ {r.get('worst_pct')}%")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    r = sub.add_parser("run")
    r.add_argument("--range", default=None, metavar="LO-HI",
                   help="run rows LO through HI (e.g. 1-4)")
    r.add_argument("--only", nargs="*", default=None, metavar="N",
                   help="run these row numbers")
    r.add_argument("--force", action="store_true")
    r.add_argument("--keep-going", action="store_true")
    r.add_argument("--dry-run", action="store_true")
    sub.add_parser("report")
    args = ap.parse_args()
    {"list": cmd_list, "run": cmd_run, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
