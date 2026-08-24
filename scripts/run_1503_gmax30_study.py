# -*- coding: utf-8 -*-
"""run_1503_gmax30_study.py — the "G" matrix: Gmax−30 three-way comparison.

Closes the Step-18 Gmax−30 debate by running, on ONE common WCG and one
filing, the three readings of the annulus between ``GRX(α₀)`` and
``Gmax − 30 dB``:

    A  keep it at MAIN-BEAM pfd      (current text)          drop_gmax30 = off
    B  drop it                       (Doc 4A/1029 §5)        drop_gmax30 = epfd
    C  reclassify as SIDE LOBE       (Doc 4A/791 St.1 sc.2)  B + SL2SL annulus

plus the ``all_non_nco`` and ``in_zone`` scope variants. Two antenna axes
are crossed on EVERY scope: the victim ES is the current S.1428 at both
Table 22-1A diameters (0.6 / 1.2 m) and the interferer side lobe is
S.1528 rec 1.2 and 1.4 — the serving pfd itself comes from the FILING
MASK, so only the side-lobe pattern changes between the a/b twins.

**Band.** 10.7 GHz filing (323520263), Table 22-1A victim diameters. This is
forced by the physics: the annulus is non-empty only where the Gmax−30
candidate governs, i.e. ``GRX_rel(α₀) > −30 dB``. At α₀ = 4°:

    D = 0.6 m : 17.8 GHz −24.79 dB (ring to 6.47°) · 10.7 GHz −18.34 dB (9.72°)
    D = 1.2 m : 17.8 GHz −30.81 dB (EMPTY)        · 10.7 GHz −26.39 dB (5.58°)
    D = 3.0 m : empty in both bands (analytically inert — no run)

so the sheet's 1.2 m annulus rows only exist at 10.7 GHz.

**Geometry.** One WCG PER VICTIM DIAMETER, pinned from the machine-3 B2
baselines (they are this study's own config-A level-1 rows). The two
differ by 22 deg of great circle, so a single shared geometry would run
one diameter off its own worst case; what the A/B/C comparison needs is a
geometry common across CONFIGURATIONS, which this satisfies.

**Time base.** Every row runs on a FIXED time step: the SL2SL add-on requires
it, and keeping the non-SL2SL baselines on the same convention makes the
comparison exact. Level 1 uses the filing's own full §D4 count (``--level1
auto``, the default) — the sheet's 11 213 028 is the 17.8 GHz filing's
number and would be meaningless here; pass ``--level1 11213028`` to force it
anyway.

Usage (from the repo root, venv active):

    python scripts/run_1503_gmax30_study.py list
    python scripts/run_1503_gmax30_study.py geometry            # show WCGs
    python scripts/run_1503_gmax30_study.py geometry --set D0.6=lat,lon,gso
    python scripts/run_1503_gmax30_study.py run --node 1        # machine 1
    python scripts/run_1503_gmax30_study.py run --node 2        # machine 2
    python scripts/run_1503_gmax30_study.py run --only G4a G4b
    python scripts/run_1503_gmax30_study.py adopt      # register copied-in runs
    python scripts/run_1503_gmax30_study.py report

State (geometry + run ids) lives in
``streamlit_app/data/campaigns/gmax30_sl2sl/campaign_state.json`` — the study
is resumable and completed runs are skipped (``--force`` re-runs).
Records are keyed ``<row>_d<drops>``, so raising a drop count queues the
row again instead of reporting the shorter run as the longer one.
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

#: Geometry is pinned PER VICTIM DIAMETER, not once for the study: the WCGA
#: on this filing lands 22 deg apart for the two Table 22-1A diameters, so a
#: single "common WCG" would run one of them off its own worst case. What the
#: comparison actually requires (contribution §5.3) is a geometry held common
#: across CONFIGURATIONS (A / B / C) — which holding it per diameter
#: satisfies, while each diameter still sits on its own worst case.
#:
#: Both values come from the block-B2 baselines run on machine 3 (0.1 deg
#: WCGA, full §D4 time base) — which ARE the study's own level-1 config-A
#: rows (G1a ≡ B2-0-d060, G1b ≡ B2-0-d120):
#:
#:   0.6 m — run 8c8259c07b3b: alpha 3.4012 deg, elev 10.0 deg,
#:           epfd@WCG −167.025 dBW/m², max −163.2 (fail)
#:   1.2 m — run 2ff9173e17d9: alpha 0.0960 deg, elev 10.0 deg,
#:           epfd@WCG −171.613 dBW/m², max −165.6 (fail)
#:
#: Note both reference satellites sit INSIDE the exclusion cone
#: (alpha < alpha0 = 4 deg), i.e. they are admitted by the Step-18 GAIN
#: branch — exactly the regime the Gmax−30 candidate governs, so these
#: geometries are the relevant ones for this study.
GEOM_BY_DIAMETER: dict[float, dict[str, Any]] = {
    0.6: {"es_lat": -74.752270, "es_lon": -104.095508,
          "gso_lon": -105.737813,
          "source": "B2-0-d060 run 8c8259c07b3b (0.1 deg WCGA, machine 3)"},
    1.2: {"es_lat": -64.459828, "es_lon": -44.157290,
          "gso_lon": -86.839430,
          "source": "B2-0-d120 run 2ff9173e17d9 (0.1 deg WCGA, machine 3)"},
}

#: Diameter the ``geometry --bootstrap`` fallback searches with.
GEOM_SEARCH_DIAMETER_M = 1.2

GRID_RADIUS_KM = 315.0
GRID_SPACING_KM = 21.0

# ─── Measured cost model (10.7 GHz filing, this machine) ───────────────────
# Timings measured on the real runs, not estimated:
#     no-SL2SL      20 k -> 4.3 min      full base -> 2 h 44
#     annulus       20 k -> 4.1 min · 200 k -> 7.4 min
#     all_non_nco   20 k -> 10.5 min
# Solving the two annulus points separates setup from marginal cost, and the
# 5x apparent drop in ms/step between 20 k and 200 k is just this setup being
# diluted — short runs are setup-dominated, so LONG runs are nearly free per
# extra drop:
SETUP_S = 224.0                 # grid build + ECEF cache + mask load (~3.7 min)
MS_PER_STEP = {                 # marginal cost per drop, by SL2SL profile
    None: 0.86,                 # calibrated to the measured 2 h 44 full run
    "annulus_gmax30": 1.10,
    "in_zone": 1.40,
    "all_non_nco": 20.3,        # ~800 candidate sats/step — the only heavy one
}
#: Step count of the filing's full §D4 base, for ESTIMATES only (the engine
#: derives the real one; level-1 rows pass steps=None = auto).
FULL_BASE_STEPS_EST = 11_213_028

#: Drop counts (raised from the first pass: 20 k / 200 k were setup-bound).
DROPS_ANNULUS = 2_000_000       # ~0.7 h — the annulus/in_zone rows
DROPS_ALL_NON_NCO = 500_000     # ~2.9 h — 2 M here would be ~11.3 h
DROPS_PLAIN = 2_000_000         # ~0.5 h — the matched no-SL2SL references


def _est_hours(steps: int | None, scope: str | None) -> float:
    """Wall time from the measured model. ``steps=None`` = full §D4 base."""
    n = FULL_BASE_STEPS_EST if steps is None else int(steps)
    return (SETUP_S + n * MS_PER_STEP[scope] / 1000.0) / 3600.0


def _g(id_: str, node: int, diameter: float, config: str,
       steps: int | None, *, sl: dict | None = None, note: str = "",
       done: bool = False, optional: bool = False) -> dict[str, Any]:
    """One matrix row. ``config`` is "A" (both candidates) or "B" (GRX(α₀)).

    ``steps=None`` runs the filing's full §D4 base. ``est_h`` is derived from
    the measured cost model — never hand-written.
    """
    return {
        "id": id_, "node": node, "diameter": diameter, "config": config,
        "steps": steps, "est_h": _est_hours(steps, (sl or {}).get("scope")),
        "sl": sl, "note": note, "done": done, "optional": optional,
    }


def _sl(scope: str, pattern: str, source: str = "mask") -> dict:
    return {"scope": scope, "pattern": pattern, "pfd_source": source}


# ─── The G matrix (spreadsheet, row by row) ─────────────────────────────────
# The ``node`` field is the default 2-way split (hand-balanced); ``--split N``
# re-balances any N by estimated cost. Run ``list`` for the current totals.
MATRIX: list[dict[str, Any]] = [
    # Every SL2SL scope is run at BOTH Table 22-1A victim diameters (S.1428
    # 0.6 / 1.2 m — the CURRENT recommendation, not the 4A1d-3 revision) and
    # with BOTH S.1528 interferer patterns (rec 1.2 / 1.4), so each cell of
    # the sheet has its 2x2. Rows differing only in one axis are adjacent.

    # ── Full §D4 baselines, no SL2SL (the A/B pair that frames everything) ──
    _g("G1a", 1, 0.6, "A", None, done=True,
       note="A · both candidates (current text) — B2-0-d060, run 8c8259c07b3b"),
    _g("G1b", 2, 1.2, "A", None, done=True,
       note="A · both candidates (current text) — B2-0-d120, run 2ff9173e17d9"),
    _g("G2a", 1, 0.6, "B", None, note="B · only GRX(α₀) [4A/1029 §5]"),
    _g("G2b", 2, 1.2, "B", None, note="B · only GRX(α₀)"),

    # ── Matched no-SL2SL references, same drop count as the C rows ──
    _g("G3a", 1, 0.6, "A", DROPS_PLAIN, note="A reference for the 0.6 m C rows"),
    _g("G3b", 2, 0.6, "B", DROPS_PLAIN, note="B reference for the 0.6 m C rows"),
    _g("G3c", 2, 1.2, "A", DROPS_PLAIN, note="A reference for the 1.2 m C rows"),
    _g("G3d", 1, 1.2, "B", DROPS_PLAIN, note="B reference for the 1.2 m C rows"),

    # ── C: the annulus radiating as a side lobe (the third reading) ──
    _g("G4a", 1, 0.6, "B", DROPS_ANNULUS, sl=_sl("annulus_gmax30", "1.2"),
       note="C · annulus as side lobe · 0.6 m · S.1528 1.2"),
    _g("G4b", 2, 0.6, "B", DROPS_ANNULUS, sl=_sl("annulus_gmax30", "1.4"),
       note="C · annulus as side lobe · 0.6 m · S.1528 1.4"),
    _g("G6a", 1, 1.2, "B", DROPS_ANNULUS, sl=_sl("annulus_gmax30", "1.2"),
       note="C · annulus as side lobe · 1.2 m · S.1528 1.2"),
    _g("G6b", 2, 1.2, "B", DROPS_ANNULUS, sl=_sl("annulus_gmax30", "1.4"),
       note="C · annulus as side lobe · 1.2 m · S.1528 1.4"),

    # ── The France/Viasat-style scope: every non-Nco satellite radiates ──
    # The heavy profile (~20 ms/drop, ~800 candidates/step) — hence 500 k.
    _g("G5a", 1, 0.6, "B", DROPS_ALL_NON_NCO, sl=_sl("all_non_nco", "1.2"),
       note="all non-Nco side lobes · 0.6 m · S.1528 1.2"),
    _g("G5b", 2, 0.6, "B", DROPS_ALL_NON_NCO, sl=_sl("all_non_nco", "1.4"),
       note="all non-Nco side lobes · 0.6 m · S.1528 1.4"),
    _g("G5c", 2, 1.2, "B", DROPS_ALL_NON_NCO, sl=_sl("all_non_nco", "1.2"),
       note="all non-Nco side lobes · 1.2 m · S.1528 1.2"),
    _g("G5d", 1, 1.2, "B", DROPS_ALL_NON_NCO, sl=_sl("all_non_nco", "1.4"),
       note="all non-Nco side lobes · 1.2 m · S.1528 1.4"),

    # ── Definitive annulus statistic: the full §D4 base ──
    _g("G7a", 1, 0.6, "B", None, sl=_sl("annulus_gmax30", "1.2"),
       note="annulus on the FULL base · 0.6 m · S.1528 1.2"),
    _g("G7b", 2, 0.6, "B", None, sl=_sl("annulus_gmax30", "1.4"),
       note="annulus on the FULL base · 0.6 m · S.1528 1.4"),
    # 1.2 m is already covered at 2 M by G6a/G6b, so the full base here buys
    # precision rather than a new answer — optional, and the first to drop.
    _g("G7c", 2, 1.2, "B", None, sl=_sl("annulus_gmax30", "1.2"),
       optional=True, note="annulus on the FULL base · 1.2 m · S.1528 1.2"),
    _g("G7d", 1, 1.2, "B", None, sl=_sl("annulus_gmax30", "1.4"),
       optional=True, note="annulus on the FULL base · 1.2 m · S.1528 1.4"),

    # ── in_zone scope (everything inside the cone radiates) ──
    _g("G8a", 1, 0.6, "B", DROPS_ANNULUS, sl=_sl("in_zone", "1.2"),
       optional=True, note="in_zone scope · 0.6 m · S.1528 1.2"),
    _g("G8b", 2, 0.6, "B", DROPS_ANNULUS, sl=_sl("in_zone", "1.4"),
       optional=True, note="in_zone scope · 0.6 m · S.1528 1.4"),
]


def _steps_for(row: dict, level1_steps: int | None) -> int | None:
    """The row's drop count. ``None`` = the filing's full §D4 base;
    ``--level1-steps`` forces a literal count on the full-base rows."""
    if row["steps"] is None:
        return level1_steps
    return int(row["steps"])


def _run_key(row: dict, params: dict) -> str:
    """State key for a row AT A GIVEN DROP COUNT.

    Keying on the row id alone would make a rescaled row look already-done:
    raising ``DROPS_ANNULUS`` from 200 k to 2 M would silently report the
    200 k run as the 2 M answer. The count is part of the identity.
    """
    n = params.get("num_time_steps")
    return f"{row['id']}_d{'full' if not n else n}"


def _row_of_key(key: str) -> dict | None:
    return next((r for r in MATRIX if r["id"] == key.split("_d")[0]), None)


def _migrate_state_keys(state: dict) -> None:
    """Re-key legacy bare-id records by the drop count they actually used,
    read back from the run's own params.json."""
    runs = state.get("runs") or {}
    moved = {}
    for key, rec in list(runs.items()):
        if "_d" in key:
            continue
        pf = (REPO / "streamlit_app" / "data" / "runs" / rec["run_id"]
              / "params.json")
        try:
            n = json.loads(pf.read_text(encoding="utf-8")).get("num_time_steps")
        except Exception:  # noqa: BLE001 — unreadable run: leave it alone
            continue
        moved[key] = f"{key}_d{'full' if not n else n}"
    for old_k, new_k in moved.items():
        runs[new_k] = runs.pop(old_k)
    if moved:
        camp._save_state(state)
        print(f"state: re-keyed {len(moved)} legacy record(s) by drop count")


def _geom_for(row: dict, state: dict) -> dict[str, Any]:
    """The WCG this row runs at: the local state's per-diameter override if
    present, else the pinned value."""
    d = float(row["diameter"])
    st_geom = (state.get("geometry") or {}).get(f"D{d:g}")
    if st_geom:
        return st_geom
    g = GEOM_BY_DIAMETER.get(d)
    if not g:
        raise SystemExit(
            f"no WCG pinned for D={d} m — add it to GEOM_BY_DIAMETER or set "
            f"it locally: geometry --set D{d:g}=lat,lon,gso"
        )
    return g


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
        # "D0.6=lat,lon,gso" (or bare "lat,lon,gso" applied to --diameter)
        spec = args.set
        if "=" in spec:
            key, vals = spec.split("=", 1)
            key = key.strip()
        else:
            key, vals = f"D{float(args.diameter):g}", spec
        lat, lon, gso = (float(x) for x in vals.split(","))
        state.setdefault("geometry", {})[key] = {
            "es_lat": lat, "es_lon": lon, "gso_lon": gso, "source": "manual"}
        camp._save_state(state)
    if args.from_run:
        sim = json.loads(
            (Path(args.from_run) / "sim_data.json").read_text(encoding="utf-8"))
        w = sim.get("wcg") or {}
        p = json.loads(
            (Path(args.from_run) / "params.json").read_text(encoding="utf-8"))
        d = float(p.get("es_antenna_diameter_m") or args.diameter)
        state.setdefault("geometry", {})[f"D{d:g}"] = {
            "es_lat": float(w["es_lat_deg"]), "es_lon": float(w["es_lon_deg"]),
            "gso_lon": float(w["gso_lon_deg"]),
            "source": f"{args.from_run} (D={d} m)",
        }
        camp._save_state(state)
    if args.bootstrap:
        d = float(args.diameter)
        sys_row = camp._resolve_system(NTC, NTC_SRS_HINT)
        params: dict[str, Any] = {
            "service": "FSS", "run_static_es": False,
            "num_time_steps": 8, "dual_time_step_mode": "off",
            "wcga_s1503": True, "s1503_step_deg": float(args.step_deg),
            "es_antenna_diameter_m": d,
        }
        params.update(camp._filing_params(sys_row))
        print(f"bootstrapping the WCG for D={d} m: {args.step_deg}° WCGA on "
              f"{Path(sys_row['srs_path']).name} (token 8-step sim)…")
        run_id, ok = camp._run_one(f"geom-D{d:g}", params)
        if not ok:
            raise SystemExit("bootstrap run failed — see its worker.log")
        run_dir = REPO / "streamlit_app" / "data" / "runs" / run_id
        sim = json.loads((run_dir / "sim_data.json").read_text(encoding="utf-8"))
        w = sim.get("wcg") or {}
        state.setdefault("geometry", {})[f"D{d:g}"] = {
            "es_lat": float(w["es_lat_deg"]), "es_lon": float(w["es_lon_deg"]),
            "gso_lon": float(w["gso_lon_deg"]),
            "source": f"bootstrap run {run_id} ({args.step_deg}°, D={d} m)",
        }
        camp._save_state(state)

    print("pinned in the script (used unless overridden locally):")
    for d, g in GEOM_BY_DIAMETER.items():
        print(f"  D={d} m: ES {g['es_lat']:.6f}, {g['es_lon']:.6f} · "
              f"GSO {g['gso_lon']:.6f}   [{g['source']}]")
    ov = state.get("geometry") or {}
    print("\nlocal overrides:", json.dumps(ov, indent=2) if ov else "(none)")


# ─── adopt (runs copied in from another node) ───────────────────────────────

def _norm_params(p: dict) -> dict:
    r"""Params reduced to what identifies a ROW, so a run produced on another
    machine still matches: the repo prefix of the filing paths differs there
    (e.g. ``C:\Sharc\...`` vs ``D:\work\...``) while the MDBs themselves are
    byte-identical — they are tracked in git with checksums for exactly this.
    """
    q = {k: v for k, v in p.items() if k != "result_path"}
    for k in ("srs_path", "mask_path"):
        if q.get(k):
            q[k] = Path(str(q[k])).name
    return q


def _canonical_params(state: dict, sys_row: dict) -> dict[str, dict]:
    """``{state key: identifying params}`` for every runnable matrix row."""
    out: dict[str, dict] = {}
    for row in MATRIX:
        try:
            pw = _build_params(row, _geom_for(row, state), sys_row, None, None)
        except SystemExit:      # no geometry pinned for that diameter
            continue
        out[_run_key(row, pw)] = _norm_params(pw)
    return out


def cmd_adopt(args) -> None:
    """Match completed runs on disk to matrix rows and record them.

    The study runs across machines and finished run folders get copied back;
    without this their results are invisible to ``report`` and ``run`` would
    redo them.
    """
    state = camp._load_state()
    _migrate_state_keys(state)
    sys_row = camp._resolve_system(NTC, NTC_SRS_HINT)
    want = _canonical_params(state, sys_row)
    known = {v["run_id"] for v in (state.get("runs") or {}).values()}

    hits: dict[str, list[tuple[str, bool, float]]] = {}
    runs_dir = REPO / "streamlit_app" / "data" / "runs"
    for d in sorted(runs_dir.iterdir() if runs_dir.is_dir() else []):
        pf, sf = d / "params.json", d / "sim_data.json"
        if not (pf.is_file() and sf.is_file()):
            continue            # unfinished run — nothing to adopt
        try:
            got = _norm_params(json.loads(pf.read_text(encoding="utf-8")))
        except Exception:       # noqa: BLE001
            continue
        key = next((k for k, w in want.items() if w == got), None)
        if key:
            hits.setdefault(key, []).append(
                (d.name, d.name in known, sf.stat().st_mtime))

    added = 0
    for key in sorted(hits):
        cands = hits[key]
        rec = (state.get("runs") or {}).get(key)
        if rec and rec.get("status") == "success" and not args.force:
            print(f"  {key:16s} already recorded ({rec['run_id']})"
                  + (f" · {len(cands) - 1} duplicate(s) on disk"
                     if len(cands) > 1 else ""))
            continue
        # Prefer a run this machine already knows about, else the newest.
        pick = sorted(cands, key=lambda c: (not c[1], -c[2]))[0][0]
        extra = [c[0] for c in cands if c[0] != pick]
        print(f"  {key:16s} -> {pick}  ADOPTED"
              + (f" (ignoring duplicate {', '.join(extra)})" if extra else ""))
        if not args.dry_run:
            state.setdefault("runs", {})[key] = {
                "run_id": pick, "status": "success", "adopted": True}
            added += 1
    if added:
        camp._save_state(state)
    ext_done = {k for k in want
                if (_row_of_key(k) or {}).get("done")}
    missing = [k for k in want if k not in hits and k not in ext_done
               and not (state.get("runs") or {}).get(k)]
    print(f"\n{added} record(s) added"
          + (" (dry run — nothing written)" if args.dry_run else ""))
    if missing:
        print("still to run: " + " ".join(sorted(missing)))


# ─── list / run / report ───────────────────────────────────────────────────

def _apply_drop_overrides(args) -> None:
    """CLI overrides for the drop counts, re-deriving each row's estimate."""
    over = {}
    if getattr(args, "drops_annulus", None):
        over["annulus_gmax30"] = int(args.drops_annulus)
        over["in_zone"] = int(args.drops_annulus)
    if getattr(args, "drops_all_non_nco", None):
        over["all_non_nco"] = int(args.drops_all_non_nco)
    if getattr(args, "drops_plain", None):
        over[None] = int(args.drops_plain)
    if not over:
        return
    for r in MATRIX:
        if r["steps"] is None:      # full-base rows are never rescaled here
            continue
        scope = (r["sl"] or {}).get("scope")
        if scope in over:
            r["steps"] = over[scope]
            r["est_h"] = _est_hours(r["steps"], scope)


def _split_nodes(rows: list[dict], n_nodes: int) -> dict[str, int]:
    """Longest-processing-time greedy balance of ``rows`` over ``n_nodes``.

    Used when ``--split`` differs from the matrix's own 2-way a/b assignment
    (e.g. spreading a 2 M all_non_nco run over 4 machines).
    """
    load = [0.0] * n_nodes
    out: dict[str, int] = {}
    for r in sorted(rows, key=lambda x: x["est_h"], reverse=True):
        k = min(range(n_nodes), key=lambda i: load[i])
        out[r["id"]] = k + 1
        load[k] += r["est_h"]
    return out


def _node_of(row: dict, assign: dict[str, int] | None) -> int:
    return assign[row["id"]] if assign else row["node"]


def cmd_list(args) -> None:
    _apply_drop_overrides(args)
    pend = [r for r in MATRIX if not r["done"]]
    assign = (_split_nodes(pend, args.split)
              if args.split and args.split != 2 else None)
    n_nodes = args.split if args.split else 2
    tot = {i + 1: 0.0 for i in range(n_nodes)}
    for r in MATRIX:
        sl = r.get("sl")
        steps = r["steps"]
        bits = [f"D={r['diameter']}m", f"cfg {r['config']}",
                f"drops={'full §D4' if steps is None else format(steps, ',')}"]
        bits.append(f"SL2SL {sl['scope']} · S.1528 {sl['pattern']} · "
                    f"pfd={sl['pfd_source']}" if sl else "SL2SL off")
        flags = []
        if r["done"]:
            flags.append("DONE-externally")
        if r["optional"]:
            flags.append("optional")
        nd = "  -  " if r["done"] else f"node{_node_of(r, assign)}"
        print(f"{r['id']:5s} {nd} ~{r['est_h']:5.2f} h | " + " · ".join(bits)
              + (f"  [{', '.join(flags)}]" if flags else ""))
        if not r["done"]:
            tot[_node_of(r, assign)] += r["est_h"]
    print("\nestimated wall time — " + " · ".join(
        f"node {k}: {v:.1f} h" for k, v in sorted(tot.items())))
    print(f"total compute: {sum(tot.values()):.1f} h over {n_nodes} node(s)")
    print("cost model: setup 3.7 min + marginal "
          + ", ".join(f"{k or 'no-SL2SL'}={v} ms/drop"
                      for k, v in MS_PER_STEP.items()))


def cmd_run(args) -> None:
    _apply_drop_overrides(args)
    state = camp._load_state()
    _migrate_state_keys(state)
    sys_row = camp._resolve_system(NTC, NTC_SRS_HINT)
    print(f"filing: {sys_row['id']} ({Path(sys_row['srs_path']).name})")
    for d, g in sorted(GEOM_BY_DIAMETER.items()):
        gg = (state.get("geometry") or {}).get(f"D{d:g}") or g
        print(f"WCG D={d} m: ES {gg['es_lat']:.4f}, {gg['es_lon']:.4f} · "
              f"GSO {gg['gso_lon']:.4f}  [{gg.get('source')}]")

    pend = [r for r in MATRIX if not r["done"]]
    assign = (_split_nodes(pend, args.split)
              if args.split and args.split != 2 else None)
    todo = [r for r in MATRIX
            if (not args.only or r["id"] in args.only)
            and (args.node is None or _node_of(r, assign) == args.node)
            and (args.include_optional or not r["optional"])]
    print(f"study {STUDY_ID}: {len(todo)} row(s) queued · "
          f"{time.strftime('%Y-%m-%d %H:%M:%S')}")
    pos = 0
    for row in todo:
        pos += 1
        params = _build_params(row, _geom_for(row, state), sys_row,
                              args.level1_steps, args.steps_override)
        key = _run_key(row, params)
        rec = state["runs"].get(key)
        if rec and rec.get("status") == "success" and not args.force:
            print(f"[{key}] already done (run {rec['run_id']}) — skipping")
            continue
        if row["done"] and not args.force:
            print(f"[{key}] marked done externally — skipping "
                  "(--force runs it here)")
            continue
        sl = row.get("sl")
        print(f"\n[{key}] D={row['diameter']}m cfg={row['config']} "
              f"drops={params.get('num_time_steps', 'auto §D4')}"
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
    _migrate_state_keys(state)
    rows_out = []
    for key, rec in state.get("runs", {}).items():
        row = _row_of_key(key)
        d = REPO / "streamlit_app" / "data" / "runs" / rec["run_id"]
        e: dict[str, Any] = {
            "id": key, "run_id": rec["run_id"], "status": rec.get("status"),
            "diameter_m": row["diameter"] if row else "",
            "config": row["config"] if row else "",
            "drops": key.split("_d")[-1] if "_d" in key else "",
            "sl_diameter_m": row["diameter"] if row else "",
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
    cols = ["id", "run_id", "status", "drops", "diameter_m", "config",
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
    ls = sub.add_parser("list")
    ls.add_argument("--split", type=int, default=2)
    ls.add_argument("--drops-annulus", type=int, default=None)
    ls.add_argument("--drops-all-non-nco", type=int, default=None)
    ls.add_argument("--drops-plain", type=int, default=None)
    g = sub.add_parser("geometry")
    g.add_argument("--bootstrap", action="store_true",
                   help="resolve the common WCG by running the WCGA here")
    g.add_argument("--step-deg", type=float, default=0.1,
                   help="WCGA grid step for --bootstrap (default 0.1)")
    g.add_argument("--set", default=None,
                   metavar="[D0.6=]lat,lon,gso",
                   help="pin a per-diameter WCG locally (overrides the "
                        "script's value)")
    g.add_argument("--from-run", default=None, metavar="RUN_DIR",
                   help="take the WCG from a finished run (its own diameter)")
    g.add_argument("--diameter", type=float, default=GEOM_SEARCH_DIAMETER_M,
                   help="which diameter --bootstrap/--set applies to")
    r = sub.add_parser("run")
    r.add_argument("--node", type=int, default=None,
                   help="run only this node's share of the matrix")
    r.add_argument("--split", type=int, default=2,
                   help="number of nodes to balance over (default 2 = the "
                        "matrix's own a/b assignment; >2 re-balances by "
                        "estimated cost, e.g. --split 4 for a 2 M all_non_nco)")
    r.add_argument("--drops-annulus", type=int, default=None,
                   help=f"drops for the annulus/in_zone rows "
                        f"(default {DROPS_ANNULUS:,})")
    r.add_argument("--drops-all-non-nco", type=int, default=None,
                   help=f"drops for the all_non_nco rows "
                        f"(default {DROPS_ALL_NON_NCO:,}; 2 M is ~11 h)")
    r.add_argument("--drops-plain", type=int, default=None,
                   help=f"drops for the matched no-SL2SL rows "
                        f"(default {DROPS_PLAIN:,})")
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
    a = sub.add_parser("adopt")
    a.add_argument("--dry-run", action="store_true")
    a.add_argument("--force", action="store_true",
                   help="re-point rows that already have a record")

    sub.add_parser("report")
    args = ap.parse_args()
    {"list": cmd_list, "geometry": cmd_geometry, "run": cmd_run,
     "adopt": cmd_adopt, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
