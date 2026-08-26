# -*- coding: utf-8 -*-
"""run_10ghz_newwcg_campaign.py — re-run the 10.7 GHz campaign on the new WCG.

The WCGA merge fix (e8013b1) changed the worst-case geometry at 10.7 GHz, so
every result in that band has to be produced again. Blocks and dependency
order come from the campaign sheet:

    A (geometry)  ->  B, C, E in parallel  ->  D after C
                                               (SL2SL rides on Gmax-30 config B)

Block A is the hinge: its WCGA searches produce the geometry that every other
row pins with ``wcg_manual``. Nothing outside A may run until the diameter it
needs is resolved, and the script refuses rather than silently falling back.

**Three nodes.** Block A is 5 searches x 3.5 h and its outputs gate everything,
so it is spread first: A1 (0.6 m) and A2 (1.2 m) unlock 33 of the 38 rows and
go on separate nodes to finish soonest. The remaining blocks are then balanced
by cost with a longest-processing-time pass, respecting each row's geometry.

Because the nodes are separate machines, the geometries have to travel:

    node 1:  geometry --run          # runs the A rows assigned here
             geometry                # prints what it resolved
    nodes 2, 3:  geometry --set D0.6=lat,lon,gso   (or --from-run <dir>)
             run --node 2

``adopt`` registers finished runs copied in from another machine, matching them
to a row by their identifying parameters, so ``report`` sees the whole matrix
however the work was divided.

Usage:
    python scripts/run_10ghz_newwcg_campaign.py list [--split 3]
    python scripts/run_10ghz_newwcg_campaign.py geometry [--run] [--set ...]
    python scripts/run_10ghz_newwcg_campaign.py run --node 1
    python scripts/run_10ghz_newwcg_campaign.py adopt
    python scripts/run_10ghz_newwcg_campaign.py report

State lives in streamlit_app/data/campaigns/newwcg_10ghz/campaign_state.json;
completed rows are skipped, so a node can be stopped and resumed.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
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
from streamlit_app.lib import storage  # noqa: E402

CAMPAIGN_ID = "newwcg_10ghz"
camp.use_campaign(CAMPAIGN_ID)

#: Cost model from the sheet, measured on this machine. est_h = steps * ms/step
#: (setup excluded, as in the sheet), and a WCGA search at 0.1 deg is flat 3.5 h.
WCGA_SEARCH_H = 3.50
MS_PER_STEP = {None: 0.90, "annulus_gmax30": 0.97, "all_non_nco": 10.30}
FULL_BASE_STEPS = 11_905_543          # this filing's own §D4 base, measured

#: The two filings. NTC_LOW is the 10.7 GHz one the campaign is about; the
#: 324520180 row (A5) is a control: if the fix moved that WCG too, the 17.8 GHz
#: blocks of Contributions 1 and 2 need redoing as well.
FILINGS = {
    "323520263": (camp.NTC_LOW, camp.NTC_LOW_SRS_HINT),
    "324520180": (camp.NTC_HIGH, None),
}


def _sl(scope: str, pattern: str, grid_km: float) -> dict:
    return {"scope": scope, "pattern": pattern, "pfd_source": "mask",
            "grid_radius_km": float(grid_km)}


def _row(id_: str, block: str, filing: str, diameter: float, *,
         geom: str | None, steps: int | None = None, sl: dict | None = None,
         prio: str = "obrig", extra: dict | None = None,
         note: str = "") -> dict[str, Any]:
    """One sheet row. ``geom`` is the block-A row whose WCG this one pins;
    ``None`` marks a block-A row (it produces a geometry instead)."""
    if geom is None:
        est = WCGA_SEARCH_H
    else:
        n = FULL_BASE_STEPS if steps is None else int(steps)
        est = n * MS_PER_STEP[(sl or {}).get("scope")] / 1000.0 / 3600.0
    return {"id": id_, "block": block, "filing": filing,
            "diameter": float(diameter), "geom": geom, "steps": steps,
            "sl": sl, "prio": prio, "extra": dict(extra or {}),
            "note": note, "est_h": est}


MATRIX: list[dict[str, Any]] = [
    # ── A — geometry (corrected WCGA): produces the WCG the rest pins ──
    _row("A1", "A", "323520263", 0.6, geom=None,
         note="geometry for blocks C, D, E (60 cm)"),
    _row("A2", "A", "323520263", 1.2, geom=None,
         note="geometry for blocks B, C, D, E (120 cm)"),
    _row("A3", "A", "323520263", 3.0, geom=None, prio="obrig",
         note="analytically inert case (theta_30=2.51 deg < alpha_0=4 deg)"),
    _row("A4", "A", "323520263", 10.0, geom=None, prio="opc",
         note="fourth Table 22-1A reference diameter"),
    _row("A5", "A", "324520180", 1.0, geom=None, prio="recom",
         note="control: did the 17.8 GHz WCG move too?"),

    # ── B — selection strategies (Contribution 1, §3.3.1.3 / §3.3.2) ──
    _row("B1", "B", "323520263", 1.2, geom="A2", steps=2_000_000,
         extra={"selection_strategy": "s1503", "max_co_freq": 1},
         note="normative reference (C1 Fig. 3.4 / Tab. 2)"),
    _row("B2", "B", "323520263", 1.2, geom="A2", steps=2_000_000,
         extra={"selection_strategy": "top_n_elev_random",
                "top_n": 5, "n_select": 1},
         note="Doc 4A/442 — Top-5 + 1 random"),
    _row("B3", "B", "323520263", 1.2, geom="A2", steps=2_000_000,
         extra={"selection_strategy": "top_n_elev_random",
                "top_n": 20, "n_select": 1},
         note="Doc 4A/442 — Top-20 + 1 random"),
    _row("B4", "B", "323520263", 1.2, geom="A2", steps=2_000_000,
         extra={"selection_strategy": "hybrid_rand_he", "max_co_freq": 1},
         note="Doc 4A/493 — hybrid random + highest elevation"),
    # T is NOT a parameter of its own: the hold comes from MIN_DURATION, so
    # T = 1/12/40/160 s is set through min_duration_s. See the worker's note
    # on WP4A_519_track_duration_consolidation_decision.md.
    _row("B5", "B", "323520263", 1.2, geom="A2", steps=2_000_000,
         extra={"ref_vec_selection": True, "ref_vec_az_deg": 0.0,
                "ref_vec_el_deg": 90.0, "ref_vec_time_window_P_pct": 50.0,
                "min_duration_s": 1.0},
         note="Doc 4A/519 — zenith, T=1 s, P=50% (C1 Fig. 3.6)"),
    _row("B6", "B", "323520263", 1.2, geom="A2", steps=2_000_000,
         extra={"ref_vec_selection": True, "ref_vec_az_deg": 0.0,
                "ref_vec_el_deg": 90.0, "ref_vec_time_window_P_pct": 50.0,
                "min_duration_s": 12.0},
         note="Doc 4A/519 — zenith, T=12 s, P=50%"),
    _row("B7", "B", "323520263", 1.2, geom="A2", steps=2_000_000, prio="opc",
         extra={"ref_vec_selection": True, "ref_vec_az_deg": 0.0,
                "ref_vec_el_deg": 90.0, "ref_vec_time_window_P_pct": 50.0,
                "min_duration_s": 40.0},
         note="long-T range"),
    _row("B8", "B", "323520263", 1.2, geom="A2", steps=2_000_000, prio="opc",
         extra={"ref_vec_selection": True, "ref_vec_az_deg": 0.0,
                "ref_vec_el_deg": 90.0, "ref_vec_time_window_P_pct": 50.0,
                "min_duration_s": 160.0},
         note="long-T range"),
    _row("B9", "B", "323520263", 0.6, geom="A1", steps=2_000_000, prio="opc",
         extra={"selection_strategy": "s1503", "max_co_freq": 1},
         note="repeat the block at 60 cm for the diameter pair"),

    # ── C — Gmax-30 condition, configs A and B (Contribution 3, §3.4) ──
    _row("C1", "C", "323520263", 0.6, geom="A1", steps=None,
         extra={"drop_gmax30": False}, note="Tab. 3.2 — 60 cm, config A"),
    _row("C2", "C", "323520263", 0.6, geom="A1", steps=None,
         extra={"drop_gmax30": True}, note="Tab. 3.2 — 60 cm, config B"),
    _row("C3", "C", "323520263", 1.2, geom="A2", steps=None,
         extra={"drop_gmax30": False}, note="Tab. 3.2 — 120 cm, config A"),
    _row("C4", "C", "323520263", 1.2, geom="A2", steps=None,
         extra={"drop_gmax30": True}, note="Tab. 3.2 — 120 cm, config B"),
    _row("C5", "C", "323520263", 3.0, geom="A3", steps=None, prio="recom",
         extra={"drop_gmax30": False},
         note="analytically inert case at 3 m"),

    # ── D — SL2SL side lobe over config B (Contribution 3, §4.4) ──
    # The 3000 km grid is the fix for the 94% of annulus candidates that found
    # no served ES inside 315 km; D5/D6 repeat one cell on the old grid to
    # measure that effect rather than assert it.
    _row("D1", "D", "323520263", 0.6, geom="A1", steps=2_000_000,
         sl=_sl("annulus_gmax30", "1.2", 3000), extra={"drop_gmax30": True},
         note="annulus, widened grid"),
    _row("D2", "D", "323520263", 0.6, geom="A1", steps=2_000_000,
         sl=_sl("annulus_gmax30", "1.4", 3000), extra={"drop_gmax30": True},
         note="annulus, widened grid"),
    _row("D3", "D", "323520263", 1.2, geom="A2", steps=2_000_000,
         sl=_sl("annulus_gmax30", "1.2", 3000), extra={"drop_gmax30": True},
         note="annulus, widened grid"),
    _row("D4", "D", "323520263", 1.2, geom="A2", steps=2_000_000,
         sl=_sl("annulus_gmax30", "1.4", 3000), extra={"drop_gmax30": True},
         note="annulus, widened grid"),
    _row("D5", "D", "323520263", 0.6, geom="A1", steps=2_000_000, prio="recom",
         sl=_sl("annulus_gmax30", "1.2", 315), extra={"drop_gmax30": True},
         note="same scope on the old grid: measures the grid effect"),
    _row("D6", "D", "323520263", 1.2, geom="A2", steps=2_000_000, prio="recom",
         sl=_sl("annulus_gmax30", "1.2", 315), extra={"drop_gmax30": True},
         note="pair of D5 at 120 cm"),
    _row("D7", "D", "323520263", 0.6, geom="A1", steps=500_000,
         sl=_sl("all_non_nco", "1.2", 315), extra={"drop_gmax30": True},
         note="broad scope (C3 Fig. 4.4)"),
    _row("D8", "D", "323520263", 0.6, geom="A1", steps=500_000,
         sl=_sl("all_non_nco", "1.4", 315), extra={"drop_gmax30": True},
         note="broad scope"),
    _row("D9", "D", "323520263", 1.2, geom="A2", steps=500_000,
         sl=_sl("all_non_nco", "1.2", 315), extra={"drop_gmax30": True},
         note="broad scope"),
    _row("D10", "D", "323520263", 1.2, geom="A2", steps=500_000,
         sl=_sl("all_non_nco", "1.4", 315), extra={"drop_gmax30": True},
         note="broad scope"),
    _row("D11", "D", "323520263", 0.6, geom="A1", steps=FULL_BASE_STEPS,
         prio="opc", sl=_sl("annulus_gmax30", "1.4", 3000),
         extra={"drop_gmax30": True},
         note="annulus on the full base, if D2 turns out relevant"),

    # ── E — S.1428 pattern in 10.7-15 GHz (Contribution 2) ──
    _row("E1", "E", "323520263", 0.6, geom="A1", steps=2_000_000,
         extra={"use_proposed_antenna": False, "mods_in_wcga": False},
         note="60 cm pair: current"),
    _row("E2", "E", "323520263", 0.6, geom="A1", steps=2_000_000,
         extra={"use_proposed_antenna": True, "mods_in_wcga": False},
         note="60 cm pair: proposed"),
    _row("E3", "E", "323520263", 1.2, geom="A2", steps=2_000_000,
         extra={"use_proposed_antenna": False, "mods_in_wcga": False},
         note="120 cm pair: current"),
    _row("E4", "E", "323520263", 1.2, geom="A2", steps=2_000_000,
         extra={"use_proposed_antenna": True, "mods_in_wcga": False},
         note="120 cm pair: proposed"),
    _row("E5", "E", "323520263", 3.0, geom="A3", steps=2_000_000,
         extra={"use_proposed_antenna": False, "mods_in_wcga": False},
         note="300 cm pair: current (D/lambda > 100, other pattern branch)"),
    _row("E6", "E", "323520263", 3.0, geom="A3", steps=2_000_000,
         extra={"use_proposed_antenna": True, "mods_in_wcga": False},
         note="300 cm pair: proposed"),
    _row("E7", "E", "323520263", 10.0, geom="A4", steps=2_000_000, prio="opc",
         extra={"use_proposed_antenna": False, "mods_in_wcga": False},
         note="1000 cm pair: current"),
    _row("E8", "E", "323520263", 10.0, geom="A4", steps=2_000_000, prio="opc",
         extra={"use_proposed_antenna": True, "mods_in_wcga": False},
         note="1000 cm pair: proposed"),
]

BY_ID = {r["id"]: r for r in MATRIX}


# ─── node assignment ───────────────────────────────────────────────────────

def _assign(n_nodes: int, include_optional: bool) -> dict[str, int]:
    """Block A first (it gates everything), then the rest by longest-first.

    A1 and A2 unlock 33 of the 38 rows, so they take separate nodes and run
    before anything else. The remaining rows are balanced by estimated cost.
    """
    rows = [r for r in MATRIX if include_optional or r["prio"] != "opc"]
    # Only A1/A2/A3 gate the mandatory work, so they get one node each and run
    # first — every other node is idle on their output otherwise. A4 and A5
    # gate nothing mandatory (A4 only the optional E7/E8, A5 nothing in this
    # band at all), so they are balanced like ordinary rows instead of sitting
    # in front of a gating search.
    GATING = ("A1", "A2", "A3")
    a_rows = [r for r in rows if r["id"] in GATING]
    rest = [r for r in rows if r["id"] not in GATING]

    out: dict[str, int] = {}
    load = [0.0] * n_nodes
    for i, r in enumerate(sorted(a_rows, key=lambda x: GATING.index(x["id"]))):
        k = i % n_nodes
        out[r["id"]] = k + 1
        load[k] += r["est_h"]
    for r in sorted(rest, key=lambda x: x["est_h"], reverse=True):
        k = min(range(n_nodes), key=lambda i: load[i])
        out[r["id"]] = k + 1
        load[k] += r["est_h"]
    return out


# ─── geometry ──────────────────────────────────────────────────────────────

def _geom_key(row: dict) -> str:
    """State key for a geometry: the block-A row id (e.g. "A2")."""
    return row["id"] if row["block"] == "A" else str(row["geom"])


def _geom_of(row: dict, state: dict) -> dict[str, Any]:
    g = (state.get("geometry") or {}).get(_geom_key(row))
    if not g:
        raise SystemExit(
            f"[{row['id']}] needs the geometry from {_geom_key(row)}, which is "
            f"not resolved here. Run `geometry --run` on the node that owns it, "
            f"then bring it over with `geometry --set {_geom_key(row)}="
            f"lat,lon,gso` or `geometry --from-run <run_dir>`."
        )
    return g


def _params_for(row: dict, state: dict) -> dict[str, Any]:
    ntc, hint = FILINGS[row["filing"]]
    sys_row = camp._resolve_system(ntc, hint)
    p: dict[str, Any] = {
        "service": "FSS", "run_static_es": False,
        "es_antenna_diameter_m": row["diameter"],
        # Fixed step throughout: the SL2SL add-on requires it and it keeps the
        # non-SL2SL rows on the same convention, so the blocks stay comparable.
        "dual_time_step_mode": "off",
    }
    p.update(camp._filing_params(sys_row))

    if row["block"] == "A":
        # The search itself. The sheet asks for "symmetry auto", which the
        # worker cannot express: it always pins s1503_symmetric_mask (see
        # s1503_worker.py:191), so auto-detection is unreachable through
        # params. Full circle is used — the setting every earlier run on this
        # filing had, which keeps the new geometry comparable to the old one.
        p.update({"wcga_s1503": True, "s1503_step_deg": 0.1,
                  "wcga_no_mask_symmetry": True,
                  # The search precedes the time base, so a token count buys
                  # the geometry without paying for an EPFD phase.
                  "num_time_steps": 8})
        return p

    g = _geom_of(row, state)
    p.update({"wcga_s1503": False, "wcg_manual": True,
              "wcg_manual_es_lat": float(g["es_lat"]),
              "wcg_manual_es_lon": float(g["es_lon"]),
              "wcg_manual_gso_lon": float(g["gso_lon"]),
              "wcg_manual_align": False})
    if row["steps"]:
        p["num_time_steps"] = int(row["steps"])
    sl = row.get("sl")
    if sl:
        p.update({"sidelobe_enabled": True,
                  "sidelobe_scope": sl["scope"],
                  "sidelobe_pattern": sl["pattern"],
                  "sidelobe_pfd_source": sl["pfd_source"],
                  "sidelobe_grid_radius_km": sl["grid_radius_km"],
                  # Keep ~700 grid ES at any radius: the 21 km spacing of the
                  # 315 km grid would be 64k stations at 3000 km.
                  "sidelobe_grid_spacing_km": (
                      21.0 if sl["grid_radius_km"] <= 400 else 200.0)})
    p.update(row["extra"])
    return p


def cmd_geometry(args) -> None:
    state = camp._load_state()
    state.setdefault("geometry", {})
    if args.set:
        key, vals = args.set.split("=", 1)
        lat, lon, gso = (float(x) for x in vals.split(","))
        state["geometry"][key.strip()] = {
            "es_lat": lat, "es_lon": lon, "gso_lon": gso, "source": "manual"}
        camp._save_state(state)
    if args.from_run:
        d = Path(args.from_run)
        sim = json.loads((d / "sim_data.json").read_text(encoding="utf-8"))
        prm = json.loads((d / "params.json").read_text(encoding="utf-8"))
        w = sim.get("wcg") or {}
        dia = float(prm.get("es_antenna_diameter_m"))
        key = args.key or next(
            (r["id"] for r in MATRIX
             if r["block"] == "A" and r["diameter"] == dia), None)
        if not key:
            raise SystemExit(f"no block-A row has D={dia} m — pass --key")
        state["geometry"][key] = {
            "es_lat": float(w["es_lat_deg"]), "es_lon": float(w["es_lon_deg"]),
            "gso_lon": float(w["gso_lon_deg"]),
            "source": f"{d.name} (D={dia} m)"}
        camp._save_state(state)
    if args.run:
        assign = _assign(args.split, args.include_optional)
        mine = [r for r in MATRIX if r["block"] == "A"
                and (args.include_optional or r["prio"] != "opc")
                and (args.node is None or assign.get(r["id"]) == args.node)]
        for row in mine:
            if state["geometry"].get(row["id"]) and not args.force:
                print(f"[{row['id']}] already resolved — skipping")
                continue
            print(f"\n[{row['id']}] WCGA 0.1° · D={row['diameter']} m · "
                  f"filing {row['filing']} · ~{row['est_h']:.2f} h")
            print("  geometry only: the EPFD phase runs 8 token steps, "
                  "so this run's CCDF/max/compliance mean nothing - the "
                  "band's numbers come from blocks C, D and E on the "
                  "pinned geometry.")
            run_id, ok = camp._run_one(row["id"], _params_for(row, state))
            # Read the geometry BEFORE judging the exit code. A block-A row is
            # a search; the 8-step EPFD phase after it exists only because the
            # pipeline always runs one, and its output is discarded. If that
            # token phase dies (the engine's nested Pool does fail
            # occasionally), the search that already succeeded is still in
            # sim_data.json — throwing away 3.5 h over a phase whose result
            # nobody reads would be absurd.
            d = REPO / "streamlit_app" / "data" / "runs" / run_id
            w = {}
            try:
                sim = json.loads((d / "sim_data.json").read_text(encoding="utf-8"))
                w = sim.get("wcg") or {}
            except Exception:  # noqa: BLE001
                pass
            if not w.get("es_lat_deg"):
                print(f"[{row['id']}] no WCG in run {run_id} - see its "
                      f"worker.log ({'worker exited ok' if ok else 'worker failed'})")
                if not args.keep_going:
                    raise SystemExit(1)
                continue
            if not ok:
                print(f"[{row['id']}] the token EPFD phase failed, but the "
                      f"WCGA produced a geometry - keeping it, since that is "
                      f"block A's only output (run {run_id})")
            state["geometry"][row["id"]] = {
                "es_lat": float(w["es_lat_deg"]),
                "es_lon": float(w["es_lon_deg"]),
                "gso_lon": float(w["gso_lon_deg"]),
                # Labelled geometry-only so nobody later mines this run's EPFD:
                # 8 steps make its CCDF, max and compliance verdict meaningless.
                "source": f"run {run_id} (0.1 deg, D={row['diameter']} m, "
                          f"GEOMETRY ONLY - its EPFD phase is a token)"}
            camp._save_state(state)

    print("\nresolved geometries:")
    for r in [x for x in MATRIX if x["block"] == "A"]:
        g = state["geometry"].get(r["id"])
        if g:
            print(f"  {r['id']} (D={r['diameter']:g} m): ES {g['es_lat']:.6f}, "
                  f"{g['es_lon']:.6f} · GSO {g['gso_lon']:.6f}   "
                  f"[{g.get('source')}]")
        else:
            print(f"  {r['id']} (D={r['diameter']:g} m): — not resolved")
    blocked = sorted({_geom_key(r) for r in MATRIX if r["block"] != "A"
                      and not state["geometry"].get(_geom_key(r))})
    if blocked:
        print("\nrows are blocked until these resolve: " + " ".join(blocked))


# ─── list / run / adopt / report ───────────────────────────────────────────

def cmd_list(args) -> None:
    assign = _assign(args.split, args.include_optional)
    state = camp._load_state()
    geo = state.get("geometry") or {}
    tot = {i + 1: 0.0 for i in range(args.split)}
    cur = None
    for r in MATRIX:
        if not args.include_optional and r["prio"] == "opc":
            continue
        if r["block"] != cur:
            cur = r["block"]
            print(f"\n── block {cur} ──")
        node = assign[r["id"]]
        tot[node] += r["est_h"]
        sl = r.get("sl")
        bits = [f"D={r['diameter']:g}m",
                f"drops={'full §D4' if r['steps'] is None and r['block'] != 'A' else ('search' if r['block'] == 'A' else format(r['steps'], ','))}"]
        if sl:
            bits.append(f"SL {sl['scope']}/{sl['pattern']}/"
                        f"{sl['grid_radius_km']:g}km")
        gk = _geom_key(r)
        ready = "" if r["block"] == "A" else (
            "" if geo.get(gk) else f"  [waits on {gk}]")
        print(f"  {r['id']:4s} node{node} ~{r['est_h']:5.2f} h  "
              f"{' · '.join(bits):46s} {r['prio']:6s}{ready}")
    print("\nestimated wall time — " + " · ".join(
        f"node {k}: {v:.1f} h" for k, v in sorted(tot.items())))
    print(f"total {sum(tot.values()):.1f} h over {args.split} node(s) · "
          f"{sum(1 for r in MATRIX if args.include_optional or r['prio'] != 'opc')} row(s)")
    print("cost model (sheet): WCGA 0.1° = 3.50 h · " + " · ".join(
        f"{k or 'no SL2SL'} {v} ms/step" for k, v in MS_PER_STEP.items()))


def _norm(p: dict) -> dict:
    q = {k: v for k, v in p.items() if k != "result_path"}
    for k in ("srs_path", "mask_path"):
        if q.get(k):
            q[k] = Path(str(q[k])).name
    return q


def _run_key(row: dict, params: dict) -> str:
    n = params.get("num_time_steps")
    return f"{row['id']}_d{'full' if not n else n}"


def cmd_run(args) -> None:
    state = camp._load_state()
    assign = _assign(args.split, args.include_optional)
    # NOTE: the optional filter has to come BEFORE the node lookup — `assign`
    # only holds the rows it was built from, so indexing it for an excluded
    # row raises KeyError.
    todo = [r for r in MATRIX
            if r["block"] != "A"
            and (args.include_optional or r["prio"] != "opc")
            and (not args.only or r["id"] in args.only)
            and (not args.block or r["block"] in args.block)
            and (args.node is None or assign.get(r["id"]) == args.node)]
    # D rides on C's configuration, so keep the sheet's order within a node.
    todo.sort(key=lambda r: (r["block"], r["id"]))
    print(f"campaign {CAMPAIGN_ID}: {len(todo)} row(s) queued · "
          f"{time.strftime('%Y-%m-%d %H:%M:%S')}")
    for pos, row in enumerate(todo, 1):
        params = _params_for(row, state)   # raises if the geometry is missing
        key = _run_key(row, params)
        rec = (state.get("runs") or {}).get(key)
        if rec and rec.get("status") == "success" and not args.force:
            print(f"[{key}] already done (run {rec['run_id']}) — skipping")
            continue
        sl = row.get("sl")
        print(f"\n[{key}] block {row['block']} · D={row['diameter']:g}m · "
              f"drops={params.get('num_time_steps', 'auto §D4')}"
              + (f" · SL {sl['scope']}/{sl['pattern']}/{sl['grid_radius_km']:g}km"
                 if sl else "")
              + f"  (~{row['est_h']:.2f} h est.)  {row['note']}")
        if args.dry_run:
            print("  " + json.dumps({k: v for k, v in params.items()
                                     if "path" not in k}, indent=2))
            continue
        run_id, ok = camp._run_one(key, params, position=f" ({pos}/{len(todo)})")
        state.setdefault("runs", {})[key] = {
            "run_id": run_id, "status": "success" if ok else "failed"}
        camp._save_state(state)
        if not ok and not args.keep_going:
            raise SystemExit(f"[{key}] failed — see worker.log. "
                             "Use --keep-going to continue.")


def cmd_adopt(args) -> None:
    """Register finished runs already on disk (copied in from another node)."""
    state = camp._load_state()
    want: dict[str, dict] = {}
    for row in MATRIX:
        if row["block"] == "A":
            continue
        try:
            want[_run_key(row, _params_for(row, state))] = _norm(
                _params_for(row, state))
        except SystemExit:
            continue          # geometry not resolved here: cannot match it
    known = {v["run_id"] for v in (state.get("runs") or {}).values()}
    runs = REPO / "streamlit_app" / "data" / "runs"
    added = 0
    for d in sorted(runs.iterdir() if runs.is_dir() else []):
        if not ((d / "params.json").is_file() and (d / "sim_data.json").is_file()):
            continue
        try:
            got = _norm(json.loads((d / "params.json").read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            continue
        key = next((k for k, w in want.items() if w == got), None)
        if not key:
            continue
        rec = (state.get("runs") or {}).get(key)
        if rec and rec.get("status") == "success" and not args.force:
            print(f"  {key:14s} already recorded ({rec['run_id']})")
            continue
        print(f"  {key:14s} -> {d.name}  ADOPTED"
              + ("" if d.name not in known else " (already known run id)"))
        if not args.dry_run:
            state.setdefault("runs", {})[key] = {
                "run_id": d.name, "status": "success", "adopted": True}
            added += 1
    if added:
        camp._save_state(state)
    print(f"\n{added} record(s) added"
          + (" (dry run)" if args.dry_run else ""))


def cmd_report(args) -> None:
    state = camp._load_state()
    out_rows = []
    for key, rec in sorted((state.get("runs") or {}).items()):
        row = BY_ID.get(key.split("_d")[0])
        d = REPO / "streamlit_app" / "data" / "runs" / rec["run_id"]
        e: dict[str, Any] = {
            "id": key, "run_id": rec["run_id"], "status": rec.get("status"),
            "block": row["block"] if row else "", "note": row["note"] if row else "",
            "diameter_m": row["diameter"] if row else "",
            "drops": key.split("_d")[-1],
        }
        try:
            sim = json.loads((d / "sim_data.json").read_text(encoding="utf-8"))
            comp = sim.get("compliance_detail") or {}
            e["max_epfd_dbw_40khz"] = sim.get("max_epfd_dbw_m2_40khz")
            e["worst_margin_db"] = comp.get("worst_margin_dB")
            e["worst_pct"] = comp.get("worst_percentage")
            e["compliant"] = sim.get("compliance")
            w = sim.get("wcg") or {}
            e["es_lat"], e["es_lon"] = w.get("es_lat_deg"), w.get("es_lon_deg")
            sl = sim.get("sidelobe") or {}
            e["sl_scope"] = sl.get("scope", "")
            e["sl_pattern"] = sl.get("pattern", "")
            e["sl_grid_km"] = sl.get("grid_radius_km", "")
            e["sl_only_max_dbw"] = sl.get("max_epfd_dbw", "")
            e["sl_total_max_dbw"] = sl.get("total_max_epfd_dbw", "")
            dg = sl.get("diagnostics") or {}
            e["sl_cand_without_link"] = dg.get("n_cand_without_link", "")
        except Exception as exc:  # noqa: BLE001
            e["error"] = str(exc)
        out_rows.append(e)
    cols = ["id", "block", "run_id", "status", "diameter_m", "drops",
            "es_lat", "es_lon", "max_epfd_dbw_40khz", "worst_margin_db",
            "worst_pct", "compliant", "sl_scope", "sl_pattern", "sl_grid_km",
            "sl_only_max_dbw", "sl_total_max_dbw", "sl_cand_without_link",
            "note", "error"]
    camp.STATE_DIR.mkdir(parents=True, exist_ok=True)
    out = camp.STATE_DIR / "newwcg_10ghz_results.csv"
    with open(out, "w", newline="", encoding="utf-8") as fh:
        fh.write("# 10.7 GHz re-run on the corrected WCG (fix e8013b1). "
                 "sl_cand_without_link > 0 means the served-ES grid is too "
                 "small for that scope and the side-lobe figure is a FLOOR.\n")
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(out_rows)
    print(f"wrote {out} ({len(out_rows)} rows)")
    for r in out_rows:
        print(f"  {r['id']:14s} {str(r.get('status')):8s} "
              f"max={r.get('max_epfd_dbw_40khz')} "
              f"margin={r.get('worst_margin_db')} "
              f"SL={r.get('sl_only_max_dbw')}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("list", "geometry", "run", "adopt", "report"):
        sp = sub.add_parser(name)
        sp.add_argument("--split", type=int, default=3,
                        help="number of nodes to balance over (default 3)")
        sp.add_argument("--include-optional", action="store_true",
                        help="also queue the rows marked 'opc'")
        if name in ("geometry", "run"):
            sp.add_argument("--node", type=int, default=None)
            sp.add_argument("--force", action="store_true")
            sp.add_argument("--keep-going", action="store_true")
        if name == "geometry":
            sp.add_argument("--run", action="store_true",
                            help="run this node's block-A searches")
            sp.add_argument("--set", default=None, metavar="A2=lat,lon,gso",
                            help="pin a geometry resolved on another node")
            sp.add_argument("--from-run", default=None, metavar="RUN_DIR",
                            help="take the geometry from a finished run")
            sp.add_argument("--key", default=None,
                            help="which block-A row --from-run fills")
        if name == "run":
            sp.add_argument("--only", nargs="*")
            sp.add_argument("--block", nargs="*",
                            help="restrict to blocks, e.g. --block B E")
            sp.add_argument("--dry-run", action="store_true")
        if name == "adopt":
            sp.add_argument("--dry-run", action="store_true")
            sp.add_argument("--force", action="store_true")
    args = ap.parse_args()
    {"list": cmd_list, "geometry": cmd_geometry, "run": cmd_run,
     "adopt": cmd_adopt, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
