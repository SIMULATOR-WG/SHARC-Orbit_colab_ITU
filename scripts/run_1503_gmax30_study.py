# -*- coding: utf-8 -*-
"""run_1503_gmax30_study.py — the "G" matrix: Gmax−30 three-way comparison.

Closes the Step-18 Gmax−30 debate by running, on ONE common WCG and one
filing, the three readings of the annulus between ``GRX(α₀)`` and
``Gmax − 30 dB``:

    A  keep it at MAIN-BEAM pfd      (current text)          drop_gmax30 = off
    B  drop it                       (Doc 4A/1029 §5)        drop_gmax30 = epfd
    C  reclassify as SIDE LOBE       (Doc 4A/791 St.1 sc.2)  B + SL2SL annulus

plus the ``all_non_nco`` and ``in_zone`` scope variants and both S.1528
patterns (1.2 / 1.4), with the serving pfd taken from the FILING MASK.

**Band.** 10.7 GHz filing (323520263), Table 22-1A victim diameters. This is
forced by the physics: the annulus is non-empty only where the Gmax−30
candidate governs, i.e. ``GRX_rel(α₀) > −30 dB``. At α₀ = 4°:

    D = 0.6 m : 17.8 GHz −24.79 dB (ring to 6.47°) · 10.7 GHz −18.34 dB (9.72°)
    D = 1.2 m : 17.8 GHz −30.81 dB (EMPTY)        · 10.7 GHz −26.39 dB (5.58°)
    D = 3.0 m : empty in both bands (analytically inert — no run)

so the sheet's 1.2 m annulus rows only exist at 10.7 GHz.

**Time base.** Every row runs on a FIXED time step: the SL2SL add-on requires
it, and keeping the non-SL2SL baselines on the same convention makes the
comparison exact. Level 1 uses the filing's own full §D4 count (``--level1
auto``, the default) — the sheet's 11 213 028 is the 17.8 GHz filing's
number and would be meaningless here; pass ``--level1 11213028`` to force it
anyway.

Usage (from the repo root, venv active):

    python scripts/run_1503_gmax30_study.py list
    python scripts/run_1503_gmax30_study.py geometry --bootstrap
    python scripts/run_1503_gmax30_study.py geometry --set 1.23,-45.6,-44.0
    python scripts/run_1503_gmax30_study.py run --node 1        # machine 1
    python scripts/run_1503_gmax30_study.py run --node 2        # machine 2
    python scripts/run_1503_gmax30_study.py run --only G4a G4b
    python scripts/run_1503_gmax30_study.py report

State (geometry + run ids) lives in
``streamlit_app/data/campaigns/gmax30_sl2sl/campaign_state.json`` — the study
is resumable and completed runs are skipped (``--force`` re-runs).
"""
from __future__ import annotations

import argparse
import csv
import json
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

# Reuse the campaign runner (progress/ETA logging, DB registration, resume)
# under this study's own campaign id and state file.
import run_1503_campaign as camp  # noqa: E402
from streamlit_app.lib import storage  # noqa: E402

STUDY_ID = "gmax30_sl2sl"
camp.use_campaign(STUDY_ID)

#: 10.7 GHz filing — see the band note in the module docstring.
NTC = camp.NTC_LOW
NTC_SRS_HINT = camp.NTC_LOW_SRS_HINT

#: The WCG search that anchors the whole study runs with this victim (middle
#: Table 22-1A diameter); every row then reuses that ONE geometry, which is
#: what makes the A/B/C ablations comparable (§5.3 of the contribution).
GEOM_SEARCH_DIAMETER_M = 1.2

GRID_RADIUS_KM = 315.0
GRID_SPACING_KM = 21.0

STEPS_L2 = 20_000
STEPS_L3 = 200_000


def _g(id_: str, node: int, level: int, diameter: float, config: str,
       est_h: float, *, sl: dict | None = None, note: str = "",
       done: bool = False, optional: bool = False) -> dict[str, Any]:
    """One matrix row. ``config`` is "A" (both candidates) or "B" (GRX(α₀))."""
    return {
        "id": id_, "node": node, "level": level, "diameter": diameter,
        "config": config, "est_h": est_h, "sl": sl, "note": note,
        "done": done, "optional": optional,
    }


def _sl(scope: str, pattern: str, source: str = "mask") -> dict:
    return {"scope": scope, "pattern": pattern, "pfd_source": source}


# ─── The G matrix (spreadsheet, row by row) ─────────────────────────────────
# Node split balances the two long poles (G5a/G5b at ~9 h and G2a/G2b at ~4 h):
# node 1 ≈ 13.4 h, node 2 ≈ 13.8 h (G7c optional).
MATRIX: list[dict[str, Any]] = [
    # ── Level 1: full §D4 baselines, no SL2SL ──
    _g("G1a", 1, 1, 0.6, "A", 4.0, done=True,
       note="A · both candidates (current text) — already done"),
    _g("G1b", 2, 1, 1.2, "A", 4.0, done=True,
       note="A · both candidates (current text) — already done"),
    _g("G2a", 1, 1, 0.6, "B", 4.0, note="B · only GRX(α₀) [4A/1029 §5]"),
    _g("G2b", 2, 1, 1.2, "B", 4.0, note="B · only GRX(α₀)"),
    # ── Level 2: short runs, the A/B/C three-way at 0.6 m ──
    _g("G3a", 1, 2, 0.6, "A", 0.02, note="A · both candidates (short)"),
    _g("G3b", 2, 2, 0.6, "B", 0.02, note="B · only GRX(α₀) (short)"),
    _g("G4a", 1, 2, 0.6, "B", 0.04, sl=_sl("annulus_gmax30", "1.2"),
       note="C · annulus as side lobe, S.1528 1.2"),
    _g("G4b", 2, 2, 0.6, "B", 0.04, sl=_sl("annulus_gmax30", "1.4"),
       note="C · annulus as side lobe, S.1528 1.4"),
    _g("G5a", 1, 2, 0.6, "B", 9.0, sl=_sl("all_non_nco", "1.2"),
       note="all non-Nco side lobes (France/Viasat-style scope), 1.2"),
    _g("G5b", 2, 2, 0.6, "B", 9.0, sl=_sl("all_non_nco", "1.4"),
       note="all non-Nco side lobes, 1.4"),
    _g("G6a", 1, 2, 1.2, "B", 0.04, sl=_sl("annulus_gmax30", "1.2"),
       note="C at 1.2 m (annulus still non-empty at 10.7 GHz), 1.2"),
    _g("G6b", 2, 2, 1.2, "B", 0.04, sl=_sl("annulus_gmax30", "1.4"),
       note="C at 1.2 m, 1.4"),
    # ── Level 3: longer statistics on the annulus rows ──
    _g("G7a", 1, 3, 0.6, "B", 0.34, sl=_sl("annulus_gmax30", "1.2"),
       note="annulus, 200k steps, 1.2"),
    _g("G7b", 2, 3, 0.6, "B", 0.34, sl=_sl("annulus_gmax30", "1.4"),
       note="annulus, 200k steps, 1.4"),
    _g("G7c", 2, 3, 0.6, "B", 0.42, sl=_sl("in_zone", "1.4"), optional=True,
       note="in_zone scope (optional)"),
]


def _steps_for(row: dict, level1_steps: int | None) -> int | None:
    if row["level"] == 1:
        return level1_steps          # None = the filing's own full §D4 base
    return STEPS_L2 if row["level"] == 2 else STEPS_L3


def _build_params(row: dict, geom: dict, sys_row: dict,
                  level1_steps: int | None,
                  steps_override: int | None) -> dict[str, Any]:
    p: dict[str, Any] = {"service": "FSS", "run_static_es": False}
    p.update(camp._filing_params(sys_row))
    p["es_antenna_diameter_m"] = float(row["diameter"])
    # Fixed step for the whole study — see the module docstring.
    p["dual_time_step_mode"] = "off"
    steps = steps_override or _steps_for(row, level1_steps)
    if steps:
        p["num_time_steps"] = int(steps)
    # One common WCG for every row (comparability of the ablations).
    p.update({
        "wcg_manual": True,
        "wcg_manual_es_lat": float(geom["es_lat"]),
        "wcg_manual_es_lon": float(geom["es_lon"]),
        "wcg_manual_gso_lon": float(geom["gso_lon"]),
        "wcg_manual_align": False,
    })
    # Config A keeps both candidates; B ablates Gmax−30 in the EPFD path only
    # (the WCG is pinned, so the WCGA scope is irrelevant here).
    p["drop_gmax30"] = (row["config"] == "B")
    p["mods_in_wcga"] = False
    sl = row.get("sl")
    if sl:
        p.update({
            "sidelobe_enabled": True,
            "sidelobe_scope": sl["scope"],
            "sidelobe_pattern": sl["pattern"],
            "sidelobe_pfd_source": sl["pfd_source"],
            "sidelobe_grid_radius_km": GRID_RADIUS_KM,
            "sidelobe_grid_spacing_km": GRID_SPACING_KM,
        })
    return p


# ─── geometry ──────────────────────────────────────────────────────────────

def cmd_geometry(args) -> None:
    state = camp._load_state()
    if args.set:
        lat, lon, gso = (float(x) for x in args.set.split(","))
        state["geometry"]["G"] = {"es_lat": lat, "es_lon": lon,
                                  "gso_lon": gso, "source": "manual"}
        camp._save_state(state)
    if args.from_run:
        sim = json.loads(
            (Path(args.from_run) / "sim_data.json").read_text(encoding="utf-8"))
        w = sim.get("wcg") or {}
        state["geometry"]["G"] = {
            "es_lat": float(w["es_lat_deg"]), "es_lon": float(w["es_lon_deg"]),
            "gso_lon": float(w["gso_lon_deg"]), "source": str(args.from_run),
        }
        camp._save_state(state)
    if args.bootstrap:
        sys_row = camp._resolve_system(NTC, NTC_SRS_HINT)
        params: dict[str, Any] = {
            "service": "FSS", "run_static_es": False,
            "num_time_steps": 8, "dual_time_step_mode": "off",
            "wcga_s1503": True, "s1503_step_deg": float(args.step_deg),
            "es_antenna_diameter_m": GEOM_SEARCH_DIAMETER_M,
        }
        params.update(camp._filing_params(sys_row))
        print(f"bootstrapping the common WCG: {args.step_deg}° WCGA on "
              f"{Path(sys_row['srs_path']).name}, victim "
              f"{GEOM_SEARCH_DIAMETER_M} m (token 8-step sim)…")
        run_id, ok = camp._run_one("geom-G", params)
        if not ok:
            raise SystemExit("bootstrap run failed — see its worker.log")
        run_dir = REPO / "streamlit_app" / "data" / "runs" / run_id
        sim = json.loads((run_dir / "sim_data.json").read_text(encoding="utf-8"))
        w = sim.get("wcg") or {}
        state["geometry"]["G"] = {
            "es_lat": float(w["es_lat_deg"]), "es_lon": float(w["es_lon_deg"]),
            "gso_lon": float(w["gso_lon_deg"]),
            "source": f"bootstrap run {run_id} ({args.step_deg}°, "
                      f"{GEOM_SEARCH_DIAMETER_M} m)",
        }
        camp._save_state(state)
    print(json.dumps(state.get("geometry", {}), indent=2))
    if "G" not in state.get("geometry", {}):
        print("\nCommon WCG unresolved — run `geometry --bootstrap` (searches "
              "it here) or `geometry --set lat,lon,gso`. Every G row needs it: "
              "the A/B/C comparison is only valid at ONE geometry.")


# ─── list / run / report ───────────────────────────────────────────────────

def cmd_list(args) -> None:
    tot = {1: 0.0, 2: 0.0}
    for r in MATRIX:
        sl = r.get("sl")
        bits = [f"D={r['diameter']}m", f"cfg {r['config']}",
                f"L{r['level']}", f"steps={_steps_for(r, None) or 'auto §D4'}"]
        if sl:
            bits.append(f"SL2SL {sl['scope']} · S.1528 {sl['pattern']} · "
                        f"pfd={sl['pfd_source']}")
        else:
            bits.append("SL2SL off")
        flags = []
        if r["done"]:
            flags.append("DONE-externally")
        if r["optional"]:
            flags.append("optional")
        print(f"{r['id']:5s} node{r['node']} ~{r['est_h']:5.2f} h | "
              + " · ".join(bits) + (f"  [{', '.join(flags)}]" if flags else ""))
        if not r["done"]:
            tot[r["node"]] += r["est_h"]
    print(f"\nestimated wall time — node 1: {tot[1]:.1f} h · "
          f"node 2: {tot[2]:.1f} h")


def cmd_run(args) -> None:
    state = camp._load_state()
    geom = (state.get("geometry") or {}).get("G")
    if not geom:
        raise SystemExit(
            "Common WCG unresolved — run `geometry --bootstrap` (or --set) "
            "first. Every G row must share ONE geometry."
        )
    sys_row = camp._resolve_system(NTC, NTC_SRS_HINT)
    print(f"filing: {sys_row['id']} ({Path(sys_row['srs_path']).name})")
    print(f"common WCG: ES {geom['es_lat']:.4f}, {geom['es_lon']:.4f} · "
          f"GSO {geom['gso_lon']:.4f}  [{geom.get('source')}]")

    todo = [r for r in MATRIX
            if (not args.only or r["id"] in args.only)
            and (args.node is None or r["node"] == args.node)
            and (args.include_optional or not r["optional"])]
    print(f"study {STUDY_ID}: {len(todo)} row(s) queued · "
          f"{time.strftime('%Y-%m-%d %H:%M:%S')}")
    pos = 0
    for row in todo:
        pos += 1
        key = row["id"]
        rec = state["runs"].get(key)
        if rec and rec.get("status") == "success" and not args.force:
            print(f"[{key}] already done (run {rec['run_id']}) — skipping")
            continue
        if row["done"] and not args.force:
            print(f"[{key}] marked done externally — skipping "
                  "(--force runs it here)")
            continue
        params = _build_params(row, geom, sys_row,
                              args.level1_steps, args.steps_override)
        sl = row.get("sl")
        print(f"\n[{key}] D={row['diameter']}m cfg={row['config']} "
              f"L{row['level']} steps={params.get('num_time_steps', 'auto §D4')}"
              + (f" · SL2SL {sl['scope']}/{sl['pattern']}/{sl['pfd_source']}"
                 if sl else "")
              + f"  (~{row['est_h']:.2f} h est.)")
        if args.dry_run:
            print("  params:", json.dumps(
                {k: v for k, v in params.items()
                 if k not in ("srs_path", "mask_path")}, indent=2))
            continue
        run_id, ok = camp._run_one(key, params,
                                   position=f" ({pos}/{len(todo)})")
        state["runs"][key] = {"run_id": run_id,
                              "status": "success" if ok else "failed"}
        camp._save_state(state)
        if not ok and not args.keep_going:
            raise SystemExit(f"[{key}] failed — aborting (see worker.log). "
                             "Use --keep-going to continue.")


def cmd_report(args) -> None:
    state = camp._load_state()
    rows_out = []
    for key, rec in state.get("runs", {}).items():
        row = next((r for r in MATRIX if r["id"] == key), None)
        d = REPO / "streamlit_app" / "data" / "runs" / rec["run_id"]
        e: dict[str, Any] = {
            "id": key, "run_id": rec["run_id"], "status": rec.get("status"),
            "diameter_m": row["diameter"] if row else "",
            "config": row["config"] if row else "",
            "level": row["level"] if row else "",
        }
        try:
            sim = json.loads((d / "sim_data.json").read_text(encoding="utf-8"))
            comp = sim.get("compliance_detail") or {}
            e["max_epfd_dbw_40khz"] = sim.get("max_epfd_dbw_m2_40khz")
            e["worst_margin_db"] = comp.get("worst_margin_dB")
            e["worst_pct"] = comp.get("worst_percentage")
            e["compliant"] = sim.get("compliance")
            sl = sim.get("sidelobe") or {}
            e["sl_scope"] = sl.get("scope", "")
            e["sl_pattern"] = sl.get("pattern", "")
            e["sl_pfd_source"] = sl.get("pfd_source", "")
            e["sl_only_max_dbw"] = sl.get("max_epfd_dbw", "")
            e["sl_total_max_dbw"] = sl.get("total_max_epfd_dbw", "")
            dg = sl.get("diagnostics") or {}
            e["sl_cand_median"] = dg.get("n_candidates_median", "")
            e["sl_cand_p90"] = dg.get("n_candidates_p90", "")
            e["sl_cand_max"] = dg.get("n_candidates_max", "")
            e["sl_pct_steps_with_cand"] = dg.get("pct_steps_with_candidate", "")
            e["sl_cand_without_link"] = dg.get("n_cand_without_link", "")
            mods = sim.get("s1503_modifications") or {}
            e["drop_gmax30"] = bool(mods.get("drop_gmax30", False))
        except Exception as exc:  # noqa: BLE001
            e["error"] = str(exc)
        rows_out.append(e)
    rows_out.sort(key=lambda r: str(r["id"]))
    out = camp.STATE_DIR / "gmax30_study_results.csv"
    camp.STATE_DIR.mkdir(parents=True, exist_ok=True)
    cols = ["id", "run_id", "status", "level", "diameter_m", "config",
            "drop_gmax30", "max_epfd_dbw_40khz", "worst_margin_db",
            "worst_pct", "compliant", "sl_scope", "sl_pattern",
            "sl_pfd_source", "sl_only_max_dbw", "sl_total_max_dbw",
            "sl_cand_median", "sl_cand_p90", "sl_cand_max",
            "sl_pct_steps_with_cand", "sl_cand_without_link", "error"]
    with open(out, "w", newline="", encoding="utf-8") as fh:
        fh.write("# Gmax-30 three-way study (A keep at main beam / B drop / "
                 "C reclassify as side lobe). sl_cand_without_link > 0 means "
                 "the served-ES grid is too small for that scope and the "
                 "side-lobe contribution is UNDERSTATED.\n")
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows_out)
    print(f"wrote {out} ({len(rows_out)} rows)")
    for r in rows_out:
        print(f"  {r['id']:5s} {str(r.get('status')):8s} "
              f"max={r.get('max_epfd_dbw_40khz')} "
              f"margin={r.get('worst_margin_db')} "
              f"SL-only={r.get('sl_only_max_dbw')} "
              f"total={r.get('sl_total_max_dbw')}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    g = sub.add_parser("geometry")
    g.add_argument("--bootstrap", action="store_true",
                   help="resolve the common WCG by running the WCGA here")
    g.add_argument("--step-deg", type=float, default=0.1,
                   help="WCGA grid step for --bootstrap (default 0.1)")
    g.add_argument("--set", default=None, metavar="lat,lon,gso")
    g.add_argument("--from-run", default=None, metavar="RUN_DIR")
    r = sub.add_parser("run")
    r.add_argument("--node", type=int, choices=[1, 2], default=None,
                   help="run only this node's share of the matrix")
    r.add_argument("--only", nargs="*")
    r.add_argument("--force", action="store_true")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--keep-going", action="store_true")
    r.add_argument("--include-optional", action="store_true")
    r.add_argument("--level1-steps", type=int, default=None,
                   help="force the level-1 step count (default: the filing's "
                        "own full §D4 base)")
    r.add_argument("--steps-override", type=int, default=None,
                   help="tiny step count for smoke-testing the matrix")
    sub.add_parser("report")
    args = ap.parse_args()
    {"list": cmd_list, "geometry": cmd_geometry,
     "run": cmd_run, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
