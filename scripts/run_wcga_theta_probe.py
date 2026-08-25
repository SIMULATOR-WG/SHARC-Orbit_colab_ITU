# -*- coding: utf-8 -*-
"""run_wcga_theta_probe.py — why does the FULL sweep pick a worse winner?

On the 3X filing at 10.7 GHz the two sweep modes disagree, and not in the
direction they can legitimately disagree: the full-circle sweep, which covers
a superset of what the symmetric sweep covers, returns a candidate that is
*worse* by the algorithm's own ranking.

    symmetric (method 1)   ES ~0.45 S / 99.4 W    single-entry -162.7 dBW/m2
    full circle (ours)     ES 64.46 S / 44.16 W   single-entry -171.6 dBW/m2

Ranking is margin = PFD + Grel(phi) - EPFDThreshold[lat] (D.3.1.2), and the
tie window is 0.1 dB wide, so -2.7 dB should beat -6.3 dB outright. More
coverage cannot find a worse winner unless the better candidate is never
evaluated, or is evaluated with a different number.

THE SUSPECT is the theta sampling. D.3.1.3.4 sets

    NumThetaSteps = RoundUp(2*pi*phi / PhiStepSize)      (no mode dependence)
    ThetaStepSize = (ThetaMax - ThetaMin) / NumThetaSteps

with the interval pi when the mask is assumed symmetric and 2*pi when it is
not. So the FULL sweep samples theta HALF as finely. A peak narrow in theta
is then resolvable by the symmetric grid and steppable-over by the full one.
The special cases do not rescue it: they target alpha = 0 and +/-alpha0, while
the method-1 winner sits at alpha = 0.43 deg, an ordinary grid point.

This script runs the WCGA only (8 time steps: the search precedes the time
base and does not depend on it) over the cells below, dumping the full trail
so candidates can be re-ranked offline without re-searching.

    python scripts/run_wcga_theta_probe.py list
    python scripts/run_wcga_theta_probe.py run --only sym
    python scripts/run_wcga_theta_probe.py run --step 0.5      # scout first
    python scripts/run_wcga_theta_probe.py report

Cost: a 0.1 deg WCGA is ~3-4 h per cell on a 20-32 core machine. Scout at
0.5 deg (~30 min) before committing to the full grid.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import run_1503_campaign as camp  # noqa: E402

OUT = REPO / "streamlit_app" / "data" / "campaigns" / "wcga_theta_probe"

#: The method-1 winner, for the point evaluation. From the aggregate run.
M1_ES_LAT, M1_ES_LON = -0.45, -99.4

CELLS: list[dict[str, Any]] = [
    # id            symmetric  equal-density   what it answers
    {"id": "sym", "symmetric": True, "equal_density": False,
     "note": "reproduces method 1 — expected to land near the equator"},
    {"id": "full", "symmetric": False, "equal_density": False,
     "note": "what we run today — expected to land in the south"},
    {"id": "full_eq", "symmetric": False, "equal_density": True,
     "note": "THE TEST: full coverage at the symmetric sweep's theta density"},
]


def _params(cell: dict, step_deg: float, diameter: float) -> dict[str, Any]:
    sys_row = camp._resolve_system(camp.NTC_LOW, camp.NTC_LOW_SRS_HINT)
    p: dict[str, Any] = {
        "service": "FSS", "run_static_es": False,
        "es_antenna_diameter_m": float(diameter),
        "wcga_s1503": True, "s1503_step_deg": float(step_deg),
        # The WCGA runs before the time base is computed, so a token count
        # buys the search without paying for the EPFD simulation.
        "num_time_steps": 8, "dual_time_step_mode": "off",
        # `wcga_no_mask_symmetry=True` means DO NOT assume symmetry, i.e. the
        # full 2*pi sweep. The name is the negation of the cell's flag.
        "wcga_no_mask_symmetry": not bool(cell["symmetric"]),
        "wcga_theta_equal_density": bool(cell["equal_density"]),
        # The whole point: keep every visited point so the ranking can be
        # audited offline instead of trusting the single reported winner.
        "s1503_trail_all_points": True,
        "gso_longitude_mode": "arc_optimal",
        "alpha_method": "analytical",
    }
    p.update(camp._filing_params(sys_row))
    return p


def _run_dir(cell_id: str, step_deg: float, diameter: float) -> Path:
    return OUT / f"{cell_id}_D{diameter:g}_s{step_deg:g}".replace(".", "p")


def cmd_list(args) -> None:
    print(f"filing: {camp.NTC_LOW} · victim {args.diameter} m · "
          f"step {args.step}°\n")
    for c in CELLS:
        d = _run_dir(c["id"], args.step, args.diameter)
        done = (d / "sim_data.json").is_file()
        print(f"  {c['id']:8s} symmetric={str(c['symmetric']):5s} "
              f"equal_density={str(c['equal_density']):5s} "
              f"{'DONE' if done else '    '}  {c['note']}")
    print("\ntheta steps per phi ring, as the code computes them:")
    import math
    for phi in (1.0, 10.0, 45.0, 63.94):
        n = max(16, math.ceil(2.0 * math.pi * phi / args.step))
        print(f"  phi={phi:6.2f}°  NumThetaSteps={n:6d} → "
              f"symmetric Δθ={180.0/n:7.4f}°  ·  "
              f"full Δθ={360.0/n:7.4f}°  ·  full+equal Δθ={180.0/n:7.4f}°")


def cmd_run(args) -> None:
    cells = [c for c in CELLS if not args.only or c["id"] in args.only]
    print(f"{len(cells)} cell(s) · step {args.step}° · D {args.diameter} m · "
          f"{time.strftime('%Y-%m-%d %H:%M:%S')}")
    for i, c in enumerate(cells, 1):
        d = _run_dir(c["id"], args.step, args.diameter)
        if (d / "sim_data.json").is_file() and not args.force:
            print(f"[{c['id']}] already done — skipping")
            continue
        d.mkdir(parents=True, exist_ok=True)
        p = _params(c, args.step, args.diameter)
        p["result_path"] = str(d)
        (d / "params.json").write_text(json.dumps(p, indent=2),
                                       encoding="utf-8")
        print(f"\n[{c['id']}] ({i}/{len(cells)}) {c['note']}")
        print(f"  started {time.strftime('%H:%M:%S')} · log {d/'worker.log'}")
        if args.dry_run:
            print("  " + json.dumps({k: v for k, v in p.items()
                                     if "path" not in k}, indent=2))
            continue
        t0 = time.time()
        import os
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        if c["equal_density"]:
            env["SHARC_WCGA_THETA_EQUAL_DENSITY"] = "1"
        with open(d / "worker.log", "w", encoding="utf-8") as lf:
            proc = subprocess.Popen(
                [sys.executable, "-m",
                 "streamlit_app.lib.job_runners.s1503_worker",
                 str(d / "params.json")],
                cwd=str(REPO), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", env=env, bufsize=1)
            for line in proc.stdout:
                lf.write(line)
                lf.flush()
                if any(k in line for k in ("WCG found", "ES: lat", "GSO: lon",
                                           "Single-entry EPFD", "α =",
                                           "EPFDThreshold", "theta density")):
                    print("   " + line.rstrip()[:150])
            proc.wait()
        print(f"  exit {proc.returncode} · {(time.time()-t0)/60:.1f} min")


def _trail_of(d: Path) -> list[dict] | None:
    """The visited-points trail, wherever the artifacts put it."""
    for name in ("trail.json", "wcga_trail.json", "sim_data.json"):
        f = d / name
        if not f.is_file():
            continue
        data = json.loads(f.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        for key in ("trail", "wcga_trail", "s1503_trail", "all_points"):
            if isinstance(data.get(key), list) and data[key]:
                return data[key]
    return None


def cmd_report(args) -> None:
    print(f"{'cell':9s} {'ES lat':>9s} {'ES lon':>10s} {'GSO lon':>10s} "
          f"{'alpha':>7s} {'elev':>6s} {'single-entry':>12s} {'trail':>8s}")
    for c in CELLS:
        d = _run_dir(c["id"], args.step, args.diameter)
        f = d / "sim_data.json"
        if not f.is_file():
            print(f"{c['id']:9s} (not run)")
            continue
        sim = json.loads(f.read_text(encoding="utf-8"))
        w = sim.get("wcg") or {}
        tr = _trail_of(d)
        print(f"{c['id']:9s} {w.get('es_lat_deg', float('nan')):9.4f} "
              f"{w.get('es_lon_deg', float('nan')):10.4f} "
              f"{w.get('gso_lon_deg', float('nan')):10.4f} "
              f"{w.get('alpha_deg', float('nan')):7.3f} "
              f"{w.get('elevation_deg', float('nan')):6.2f} "
              f"{str(w.get('epfd_dbw_m2', '—')):>12s} "
              f"{len(tr) if tr else 0:8d}")
    print("\nIf `full_eq` lands where `sym` lands, the theta sampling is the "
          "whole story and the finding is that D.3.1.3.4's step count makes "
          "the result depend on the symmetry assumption.")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("list", "run", "report"):
        sp = sub.add_parser(name)
        sp.add_argument("--step", type=float, default=0.1,
                        help="WCGA latitude step (default 0.1; use 0.5 to "
                             "scout in ~30 min instead of ~3-4 h)")
        sp.add_argument("--diameter", type=float, default=1.2)
        if name == "run":
            sp.add_argument("--only", nargs="*")
            sp.add_argument("--force", action="store_true")
            sp.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    {"list": cmd_list, "run": cmd_run, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
