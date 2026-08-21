# -*- coding: utf-8 -*-
"""run_1503_campaign.py — WP-4A "changes 1503" simulation campaign runner.

Reproduces the campaign matrix (blocks A / B1 / B2) without touching the
Streamlit UI. Every run is registered in the app's runs DB (kind="single",
campaign_id="changes_1503"), so results appear on the Results page exactly
like UI-launched runs.

Usage (from the repo root, venv active):

    python scripts/run_1503_campaign.py list                # show the matrix
    python scripts/run_1503_campaign.py geometry            # show/resolve WCGs
    python scripts/run_1503_campaign.py geometry --from-run A1=<run_dir>
    python scripts/run_1503_campaign.py geometry --set A=-0.445,-99.344,<gso>
    python scripts/run_1503_campaign.py run                 # run all pending
    python scripts/run_1503_campaign.py run --only A3a A4   # subset
    python scripts/run_1503_campaign.py run --steps-override 50   # smoke mode
    python scripts/run_1503_campaign.py report              # campaign_results.csv

State (resolved geometries + run ids/status) lives in
    streamlit_app/data/campaigns/changes_1503/campaign_state.json
so the campaign is resumable: completed runs are skipped (use --force to
re-run).

Geometry policy (matches the campaign sheet):
  * Blocks A and B1 pin the ES at the 17.8-GHz WCG (0.445°S, 99.344°W). The
    GSO longitude is NOT in the sheet — resolve it once from the finished A1
    baseline (`geometry --from-run A1=<dir-with-sim_data.json>`), set it by
    hand (`geometry --set`), or let the script bootstrap it by running the
    WCGA (that IS the expensive part of A1; the sim after it is cheap).
  * Block B2 searches its own WCG (grid 0.1°) in the B2-0 baselines; the
    B2-1..5 strategy runs then reuse the geometry found by B2-0-d120.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# Legacy Windows consoles default to cp1252 — the matrix uses °/→/·.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from streamlit_app.lib import storage  # noqa: E402
from streamlit_app.lib.launcher import _relpath_under_uploads  # noqa: E402

CAMPAIGN_ID = "changes_1503"
STATE_DIR = REPO / "streamlit_app" / "data" / "campaigns" / CAMPAIGN_ID
STATE_PATH = STATE_DIR / "campaign_state.json"

# ─── Filings (resolved from the app's systems DB by ntc_id) ─────────────────
#: 17.8 GHz filing (blocks A + B1). PDF Table 1: USASAT-NGSO-3X, 324520180.
NTC_HIGH = "324520180"
#: 10.7 GHz filing (block B2). Two extracts exist for 323520263; the campaign
#: uses the 10.7-GHz one — preferred by "10700" in the SRS filename.
NTC_LOW = "323520263"
NTC_LOW_SRS_HINT = "10700"

#: Victim ES antenna diameter (m) per block. 17.8 GHz runs follow the PDF
#: (1.0 m, Table 22-1B); B2 strategy runs default to 1.2 m (Table 22-1A
#: middle reference) — the B2-0 baselines sweep all three 22-1A diameters.
DIAM_HIGH = 1.0
DIAM_B2_STRATEGY = 1.2

#: Steps per phase (campaign sheet).
STEPS_FULL_HIGH = 11_213_028   # §D4 subset-3 reference of the 17.8 filing
STEPS_STRATEGY = 2_000_000

DEFAULT_SEED = 42


def _mk(id_: str, block: str, **kw) -> dict[str, Any]:
    d = {"id": id_, "block": block}
    d.update(kw)
    return d


# ─── The campaign matrix (mirrors the spreadsheet row by row) ────────────────
MATRIX: list[dict[str, Any]] = [
    # ── Block A: 324520180 @ 17.8 GHz, declared Nco=32, fixed WCG ──
    _mk("A1", "A", strategy="s1503", steps=STEPS_FULL_HIGH, note="Baseline (already done externally — kept for completeness; skipped unless --force)", done_externally=True),
    _mk("A2", "A", strategy="s1503", steps=STEPS_FULL_HIGH, step22=False, emulate_s1503_2=True, note="Normative with the whole Step-18 gain test removed ('Neither', Sec. 5)"),
    _mk("A3a", "A", strategy="top_n_elev_random", top_n=32, n_select=32, steps=STEPS_STRATEGY, note="Deterministic limit: the 32 highest elevations"),
    _mk("A3b", "A", strategy="top_n_elev_random", top_n=64, n_select=32, steps=STEPS_STRATEGY),
    _mk("A3c", "A", strategy="top_n_elev_random", top_n=96, n_select=32, steps=STEPS_STRATEGY),
    _mk("A3d", "A", strategy="top_n_elev_random", top_n=128, n_select=32, steps=STEPS_STRATEGY, note="Highest randomness; under-estimation ceiling"),
    _mk("A4", "A", strategy="hybrid_rand_he", steps=STEPS_STRATEGY, note="Nco=32 lists; re-run of the existing round WITH Step 22 ON"),
    _mk("A5", "A", strategy="ref_vector", rv_el=90.0, rv_az=0.0, rv_T=1.0, rv_P=100.0, steps=STEPS_STRATEGY, note="Zenith, T=1s ≡ highest elevation up to 32 sats (fixes the long-term round, OR ON)"),
    _mk("A6", "A", strategy="ref_vector", rv_el=90.0, rv_az=0.0, rv_T=12.0, rv_P=50.0, steps=STEPS_STRATEGY, note="US canonical config (4A/791 Study 2)"),
    _mk("A7a", "A", strategy="ref_vector", rv_el=60.0, rv_az=0.0, rv_T=12.0, rv_P=50.0, steps=STEPS_STRATEGY, note="Tilted vector: flees the GSO arc at the equator"),
    _mk("A7b", "A", strategy="ref_vector", rv_el=45.0, rv_az=0.0, rv_T=12.0, rv_P=50.0, steps=STEPS_STRATEGY),
    # ── Block B1: same filing, MAX_CO_FREQ forced to 1 ──
    _mk("B1-0", "B1", strategy="s1503", max_co_freq=1, steps=STEPS_FULL_HIGH, note="Ablation: isolates the Nco effect (same constellation/mask/WCG)"),
    _mk("B1-1", "B1", strategy="top_n_elev_random", max_co_freq=1, top_n=1, n_select=1, steps=STEPS_STRATEGY, note="Pure highest elevation (Galaxy Space)"),
    _mk("B1-2", "B1", strategy="top_n_elev_random", max_co_freq=1, top_n=5, n_select=1, steps=STEPS_STRATEGY, note="Doc 4A/442 case"),
    _mk("B1-3", "B1", strategy="top_n_elev_random", max_co_freq=1, top_n=10, n_select=1, steps=STEPS_STRATEGY),
    _mk("B1-4", "B1", strategy="top_n_elev_random", max_co_freq=1, top_n=20, n_select=1, steps=STEPS_STRATEGY),
    _mk("B1-5", "B1", strategy="hybrid_rand_he", max_co_freq=1, steps=STEPS_STRATEGY, note="1 random + 1 highest elevation; keep the worst epfd"),
    _mk("B1-6", "B1", strategy="hybrid_rand_he", max_co_freq=1, hybrid_list_size=10, steps=STEPS_STRATEGY, note="(sheet had a duplicate 'B1-5') 10 random + 10 highest elevation; keep the worst 1"),
    _mk("B1-7", "B1", strategy="ref_vector", max_co_freq=1, rv_el=90.0, rv_az=0.0, rv_T=1.0, rv_P=100.0, steps=STEPS_STRATEGY),
    _mk("B1-8", "B1", strategy="ref_vector", max_co_freq=1, rv_el=90.0, rv_az=0.0, rv_T=12.0, rv_P=50.0, steps=STEPS_STRATEGY, note="US config with Nco=1 (as 4A/791 Study 2)"),
    # ── Block B2: 323520263 @ 10.7 GHz, declared Nco=1, own WCG ──
    _mk("B2-0-d060", "B2", strategy="s1503", diameter=0.6, steps=None, wcga=True, note="Low-band baseline, victim 0.6 m (22-1A); full §D4 time base + 0.1° WCGA"),
    _mk("B2-0-d120", "B2", strategy="s1503", diameter=1.2, steps=None, wcga=True, geometry_source=True, note="Low-band baseline, victim 1.2 m — its WCG anchors B2-1..5"),
    _mk("B2-0-d300", "B2", strategy="s1503", diameter=3.0, steps=None, wcga=True, note="Low-band baseline, victim 3.0 m"),
    _mk("B2-1", "B2", strategy="top_n_elev_random", top_n=5, n_select=1, steps=STEPS_STRATEGY, diameter=DIAM_B2_STRATEGY),
    _mk("B2-2", "B2", strategy="top_n_elev_random", top_n=20, n_select=1, steps=STEPS_STRATEGY, diameter=DIAM_B2_STRATEGY),
    _mk("B2-3", "B2", strategy="hybrid_rand_he", steps=STEPS_STRATEGY, diameter=DIAM_B2_STRATEGY, note="Nco=1 (declared)"),
    _mk("B2-4", "B2", strategy="ref_vector", rv_el=90.0, rv_az=0.0, rv_T=1.0, rv_P=100.0, steps=STEPS_STRATEGY, diameter=DIAM_B2_STRATEGY),
    _mk("B2-5", "B2", strategy="ref_vector", rv_el=90.0, rv_az=0.0, rv_T=12.0, rv_P=50.0, steps=STEPS_STRATEGY, diameter=DIAM_B2_STRATEGY),
]

#: Fixed ES position of the 17.8-GHz WCG (campaign sheet / PDF §5.4).
GEOM_A_ES = (-0.445, -99.344)


# ─── State ───────────────────────────────────────────────────────────────────

def _load_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"geometry": {}, "runs": {}}


def _save_state(st: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(st, indent=2), encoding="utf-8")


# ─── Filing resolution ───────────────────────────────────────────────────────

def _resolve_system(ntc: str, srs_hint: str | None = None) -> dict[str, Any]:
    rows = [r for r in storage.list_systems() if str(r.get("ntc_id")) == ntc]
    # Prefer the tracked campaign filings (registered by `setup`) — same
    # bytes on every machine, checksum-verified.
    camp = [r for r in rows if "campaign_data" in str(r.get("srs_path", ""))]
    rows = camp or rows
    if srs_hint:
        hinted = [r for r in rows if srs_hint in str(r.get("srs_path", ""))]
        rows = hinted or rows
    if not rows:
        raise SystemExit(
            f"No registered system with ntc_id={ntc}"
            + (f" (srs~'{srs_hint}')" if srs_hint else "")
            + " — register the filing on the Upload page first."
        )
    return rows[0]


def _filing_params(sys_row: dict[str, Any]) -> dict[str, Any]:
    return {
        "srs_path": sys_row["srs_path"],
        "mask_path": sys_row.get("mask_path"),
        "srs_relpath": _relpath_under_uploads(sys_row["srs_path"]),
        "mask_relpath": _relpath_under_uploads(sys_row.get("mask_path")),
        "mask_id": sys_row.get("mask_id"),
        "ntc_id": sys_row.get("ntc_id"),
        "system_id": sys_row["id"],
    }


# ─── Params assembly ─────────────────────────────────────────────────────────

def _build_params(row: dict[str, Any], seed: int | None,
                  geom: dict[str, Any], steps_override: int | None,
                  sys_high: dict, sys_low: dict) -> dict[str, Any]:
    block = row["block"]
    sys_row = sys_low if block == "B2" else sys_high
    p: dict[str, Any] = {"service": "FSS", "run_static_es": False}
    p.update(_filing_params(sys_row))
    p["es_antenna_diameter_m"] = float(row.get("diameter", DIAM_HIGH))

    steps = steps_override or row.get("steps")
    if steps:
        p["num_time_steps"] = int(steps)

    # Geometry: WCGA search for the B2-0 baselines; pinned manual WCG else.
    if row.get("wcga"):
        p["wcga_s1503"] = True
        p["s1503_step_deg"] = 0.1
    else:
        g = geom.get("B2" if block == "B2" else "A")
        if not g:
            raise SystemExit(
                f"[{row['id']}] geometry for block "
                f"{'B2' if block == 'B2' else 'A/B1'} not resolved — run "
                "`geometry --from-run ...`, `geometry --set ...`, or run the "
                "B2-0 baselines first (their WCG is captured automatically)."
            )
        p.update({
            "wcg_manual": True,
            "wcg_manual_es_lat": float(g["es_lat"]),
            "wcg_manual_es_lon": float(g["es_lon"]),
            "wcg_manual_gso_lon": float(g["gso_lon"]),
            "wcg_manual_align": False,
        })

    if row.get("max_co_freq") is not None:
        p["max_co_freq"] = int(row["max_co_freq"])
    if row.get("emulate_s1503_2"):
        p["emulate_s1503_2"] = True  # Step-18 gain test fully removed

    strat = row["strategy"]
    if strat in ("top_n_elev_random", "hybrid_rand_he"):
        p["selection_strategy"] = strat
        p["include_override"] = bool(row.get("step22", True))
        p["dual_time_step_mode"] = "off"  # parallel fixed step
        if seed is not None:
            p["seed"] = int(seed)
        if strat == "top_n_elev_random":
            p["top_n"] = int(row["top_n"])
            p["n_select"] = int(row["n_select"])
        if row.get("hybrid_list_size"):
            p["hybrid_list_size"] = int(row["hybrid_list_size"])
    elif strat == "ref_vector":
        p["ref_vec_selection"] = True
        p["ref_vec_az_deg"] = float(row.get("rv_az", 0.0))
        p["ref_vec_el_deg"] = float(row.get("rv_el", 90.0))
        p["ref_vec_time_window_P_pct"] = float(row.get("rv_P", 100.0))
        p["min_duration_s"] = float(row.get("rv_T", 1.0))  # hold T
        p["include_override"] = bool(row.get("step22", True))

    return p


def _expand_rows(only: list[str] | None) -> list[tuple[str, dict, int | None]]:
    """(run_key, row, seed) per actual run — expands multi-seed rows."""
    out = []
    for row in MATRIX:
        if only and row["id"] not in only:
            continue
        seeds = row.get("seeds")
        if seeds:
            for sd in seeds:
                out.append((f"{row['id']}-s{sd}", row, int(sd)))
        elif row["strategy"] in ("top_n_elev_random", "hybrid_rand_he"):
            out.append((row["id"], row, DEFAULT_SEED))
        else:
            out.append((row["id"], row, None))
    return out


# ─── Execution ───────────────────────────────────────────────────────────────

def _hms(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s_ = divmod(rem, 60)
    return f"{h:d}h{m:02d}m" if h else f"{m:d}m{s_:02d}s"


def _run_one(run_key: str, params: dict[str, Any],
             position: str = "") -> tuple[str, bool]:
    run_id = storage.create_run(kind="single", method=None, params=params,
                                campaign_id=CAMPAIGN_ID)
    run_dir = REPO / "streamlit_app" / "data" / "runs" / run_id
    params = dict(params)
    params["result_path"] = str(run_dir)
    (run_dir / "params.json").write_text(json.dumps(params, indent=2),
                                         encoding="utf-8")
    storage.update_run(run_id, status="running", progress_pct=0.0)
    log_path = run_dir / "worker.log"
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    t0 = time.time()
    print(f"  [{run_key}]{position} started {time.strftime('%H:%M:%S')} · "
          f"run {run_id} · full log: {log_path}")
    # Console policy: one timestamped progress line per ≥5% advance or ≥120 s
    # of silence, with elapsed + ETA. The ETA extrapolates linearly over the
    # EPFD phase (progress ≥15%; 2–15% is loading/WCGA, non-linear).
    last_pct = -1.0
    last_emit = t0
    sim_t0: float | None = None
    sim_p0 = 0.0
    with open(log_path, "w", encoding="utf-8") as lf:
        proc = subprocess.Popen(
            [sys.executable, "-m",
             "streamlit_app.lib.job_runners.s1503_worker",
             str(run_dir / "params.json")],
            cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env=env, bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            lf.write(line)
            line = line.rstrip()
            now = time.time()
            if line.startswith("PROGRESS:"):
                try:
                    pct = float(line.split(":", 1)[1])
                except ValueError:
                    continue
                storage.update_run(run_id, progress_pct=pct)
                if pct >= 15.0 and sim_t0 is None:
                    sim_t0, sim_p0 = now, pct
                if pct - last_pct >= 5.0 or (now - last_emit) >= 120.0:
                    eta = ""
                    if sim_t0 is not None and 100.0 > pct > sim_p0:
                        rate = (pct - sim_p0) / max(1e-9, now - sim_t0)
                        rem_s = (100.0 - pct) / rate
                        eta = (f" · ETA ~{_hms(rem_s)} (~"
                               + time.strftime(
                                   "%H:%M", time.localtime(now + rem_s))
                               + ")")
                    print(f"  [{run_key}] {time.strftime('%H:%M:%S')} "
                          f"{pct:6.2f}% · elapsed {_hms(now - t0)}{eta}",
                          flush=True)
                    last_pct, last_emit = pct, now
            elif line and any(k in line for k in (
                    "modification", "•", "DONE", "ERROR", "Artifacts",
                    "scope:", "windowing ACTIVE", "SL2SL")):
                print(f"  [{run_key}] {line}", flush=True)
        rc = proc.wait()
    dt = time.time() - t0
    ok = (rc == 0 and (run_dir / "summary.json").exists())
    storage.update_run(
        run_id,
        status=("success" if ok else "failed"),
        progress_pct=(100.0 if ok else None),
        error_message=("" if ok else f"exit code {rc}"),
        finished_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )
    print(f"  [{run_key}] {'OK' if ok else 'FAILED'} in {_hms(dt)} "
          f"(run {run_id}) → {run_dir}")
    return run_id, ok


def _capture_b2_geometry(state: dict[str, Any], run_dir: Path) -> None:
    """After a B2-0 baseline, pin block-B2 geometry from its found WCG."""
    try:
        sim = json.loads((run_dir / "sim_data.json").read_text(encoding="utf-8"))
        w = sim.get("wcg") or {}
        state["geometry"]["B2"] = {
            "es_lat": float(w["es_lat_deg"]),
            "es_lon": float(w["es_lon_deg"]),
            "gso_lon": float(w["gso_lon_deg"]),
            "source": str(run_dir),
        }
        _save_state(state)
        print(f"  geometry[B2] captured: {state['geometry']['B2']}")
    except Exception as exc:  # noqa: BLE001
        print(f"  WARN: could not capture B2 geometry: {exc}")


def cmd_run(args) -> None:
    state = _load_state()
    sys_high = _resolve_system(NTC_HIGH)
    sys_low = _resolve_system(NTC_LOW, NTC_LOW_SRS_HINT)
    print(f"17.8 GHz filing: {sys_high['id']} ({Path(sys_high['srs_path']).name})")
    print(f"10.7 GHz filing: {sys_low['id']} ({Path(sys_low['srs_path']).name})")

    only = list(args.only or [])
    if args.group:
        only += [r["id"] for r in MATRIX if r["block"] in args.group]
    todo = _expand_rows(only or None)
    # geometry-source baselines first, then the rest in sheet order
    todo.sort(key=lambda t: (not t[1].get("geometry_source", False),))
    print(f"campaign: {len(todo)} run(s) queued · "
          f"{time.strftime('%Y-%m-%d %H:%M:%S')}")
    _pos = 0
    for run_key, row, seed in todo:
        _pos += 1
        rec = state["runs"].get(run_key)
        if rec and rec.get("status") == "success" and not args.force:
            print(f"[{run_key}] already done (run {rec['run_id']}) — skipping")
            continue
        if row.get("done_externally") and not args.force:
            print(f"[{run_key}] marked done externally — skipping "
                  "(use --force to re-run)")
            continue
        try:
            params = _build_params(row, seed, state["geometry"],
                                   args.steps_override, sys_high, sys_low)
        except SystemExit as exc:
            print(exc)
            if args.keep_going:
                continue
            raise
        print(f"\n[{run_key}] {row['strategy']}"
              + (f" seed={seed}" if seed is not None else "")
              + f" · steps={params.get('num_time_steps', 'auto (§D4)')}"
              + (f" · Nco→{row['max_co_freq']}" if row.get("max_co_freq") else ""))
        if args.dry_run:
            print("  params:", json.dumps(
                {k: v for k, v in params.items()
                 if k not in ("srs_path", "mask_path")}, indent=2))
            continue
        run_id, ok = _run_one(run_key, params,
                              position=f" ({_pos}/{len(todo)})")
        state["runs"][run_key] = {
            "run_id": run_id,
            "status": "success" if ok else "failed",
            "seed": seed,
        }
        _save_state(state)
        if ok and row.get("geometry_source"):
            _capture_b2_geometry(
                state, REPO / "streamlit_app" / "data" / "runs" / run_id)
        if not ok and not args.keep_going:
            raise SystemExit(f"[{run_key}] failed — aborting (see worker.log). "
                             "Use --keep-going to continue past failures.")


# ─── Geometry helpers ────────────────────────────────────────────────────────

def cmd_geometry(args) -> None:
    state = _load_state()
    if args.bootstrap:
        # Resolve a block's geometry by running the WCGA here once (0.1° grid)
        # with a token 8-step simulation — the search IS the expensive part;
        # the sim after it is negligible. Captures the found WCG into state.
        blk = args.bootstrap.upper()
        if blk not in ("A", "B2"):
            raise SystemExit("--bootstrap takes A or B2")
        sys_row = (_resolve_system(NTC_HIGH) if blk == "A"
                   else _resolve_system(NTC_LOW, NTC_LOW_SRS_HINT))
        params: dict[str, Any] = {"service": "FSS", "run_static_es": False,
                                  "num_time_steps": 8,
                                  "wcga_s1503": True, "s1503_step_deg": 0.1,
                                  "es_antenna_diameter_m": (
                                      DIAM_HIGH if blk == "A"
                                      else DIAM_B2_STRATEGY)}
        params.update(_filing_params(sys_row))
        print(f"bootstrapping geometry[{blk}] via a 0.1° WCGA on "
              f"{Path(sys_row['srs_path']).name} (token 8-step sim)...")
        run_id, ok = _run_one(f"geom-{blk}", params)
        if not ok:
            raise SystemExit("bootstrap run failed — see its worker.log")
        run_dir = REPO / "streamlit_app" / "data" / "runs" / run_id
        sim = json.loads((run_dir / "sim_data.json").read_text(encoding="utf-8"))
        w = sim.get("wcg") or {}
        state["geometry"][blk] = {
            "es_lat": float(w["es_lat_deg"]),
            "es_lon": float(w["es_lon_deg"]),
            "gso_lon": float(w["gso_lon_deg"]),
            "source": f"bootstrap run {run_id}",
        }
        _save_state(state)
    if args.set:
        for spec in args.set:
            blk, vals = spec.split("=", 1)
            lat, lon, gso = (float(x) for x in vals.split(","))
            state["geometry"][blk] = {"es_lat": lat, "es_lon": lon,
                                      "gso_lon": gso, "source": "manual"}
        _save_state(state)
    if args.from_run:
        for spec in args.from_run:
            blk_key, path = spec.split("=", 1)
            blk = "A" if blk_key.upper() in ("A", "A1") else blk_key.upper()
            sim = json.loads((Path(path) / "sim_data.json").read_text(encoding="utf-8"))
            w = sim.get("wcg") or {}
            state["geometry"][blk] = {
                "es_lat": float(w["es_lat_deg"]),
                "es_lon": float(w["es_lon_deg"]),
                "gso_lon": float(w["gso_lon_deg"]),
                "source": str(path),
            }
        _save_state(state)
    print(json.dumps(state.get("geometry", {}), indent=2))
    if "A" not in state.get("geometry", {}):
        print(
            f"\nBlock A/B1 geometry unresolved. The sheet pins the ES at "
            f"{GEOM_A_ES} but the GSO longitude must come from the A1 "
            "baseline: geometry --from-run A1=<run_dir>  (or --set "
            "A=-0.445,-99.344,<gso_lon>)."
        )


# ─── Report ──────────────────────────────────────────────────────────────────

def _read_elev_csv(run_dir: Path) -> tuple[float | None, float | None]:
    """(median elevation deg, handoffs/day approx) from the decimated trace."""
    f = run_dir / "contributing_sat_elevations.csv"
    fz = run_dir / "contributing_sat_elevations.csv.gz"
    if fz.exists():
        text = gzip.decompress(fz.read_bytes()).decode("utf-8")
    elif f.exists():
        text = f.read_text(encoding="utf-8")
    else:
        return None, None
    elevs: list[float] = []
    sets_by_t: dict[float, set] = {}
    rd = csv.reader(io.StringIO(text))
    for rowc in rd:
        if not rowc or rowc[0].startswith("#") or rowc[0] == "t_s":
            continue
        try:
            t, k, el = float(rowc[0]), int(rowc[1]), float(rowc[2])
        except (ValueError, IndexError):
            continue
        elevs.append(el)
        sets_by_t.setdefault(t, set()).add(k)
    if not elevs:
        return None, None
    med = statistics.median(elevs)
    ts = sorted(sets_by_t)
    if len(ts) < 2:
        return med, None
    changes = sum(
        1 for a, b in zip(ts, ts[1:]) if sets_by_t[a] != sets_by_t[b]
    )
    span_days = (ts[-1] - ts[0]) / 86400.0
    handoffs_day = changes / span_days if span_days > 0 else None
    return med, handoffs_day


def cmd_report(args) -> None:
    state = _load_state()
    rows_out = []
    for run_key, rec in sorted(state.get("runs", {}).items()):
        run_dir = REPO / "streamlit_app" / "data" / "runs" / rec["run_id"]
        entry: dict[str, Any] = {
            "id": run_key, "run_id": rec["run_id"],
            "status": rec.get("status"), "seed": rec.get("seed"),
        }
        try:
            sim = json.loads((run_dir / "sim_data.json").read_text(encoding="utf-8"))
            entry["max_epfd_dbw_40khz"] = sim.get("max_epfd_dbw_m2_40khz")
            comp = sim.get("compliance_detail") or {}
            entry["worst_margin_art22_db"] = comp.get("worst_margin_dB")
            entry["compliant"] = sim.get("compliance")
            mods = sim.get("s1503_modifications") or {}
            entry["modifications"] = json.dumps(mods, ensure_ascii=False) if mods else ""
            med, ho = _read_elev_csv(run_dir)
            entry["median_contrib_elev_deg"] = round(med, 2) if med is not None else ""
            # Decimated trace (~10k points) — an approximation, undercounts
            # fast handoffs on long runs. Stated in the CSV header.
            entry["handoffs_per_day_approx"] = round(ho, 1) if ho else ""
        except Exception as exc:  # noqa: BLE001
            entry["error"] = str(exc)
        rows_out.append(entry)
    out = STATE_DIR / "campaign_results.csv"
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    cols = ["id", "run_id", "status", "seed", "max_epfd_dbw_40khz",
            "worst_margin_art22_db", "compliant",
            "median_contrib_elev_deg", "handoffs_per_day_approx",
            "modifications", "error"]
    with open(out, "w", newline="", encoding="utf-8") as fh:
        fh.write("# handoffs_per_day_approx is derived from the DECIMATED "
                 "elevation trace (~10k points) — undercounts on long runs\n")
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows_out)
    print(f"wrote {out} ({len(rows_out)} rows)")


def cmd_import(args) -> None:
    """Register run folders copied from another machine into THIS app's DB.

    Copy the other machine's run folders (streamlit_app/data/runs/<id>/) into
    this machine's runs directory (run ids are uuid4 — collision-free across
    machines), then:

        python scripts/run_1503_campaign.py import <dir-with-run-folders> ...

    Each folder with a params.json is inserted into the runs table
    (campaign_id=changes_1503, status from summary.json presence) and merged
    into the campaign state. Also accepts the other machine's
    campaign_state.json to recover the campaign-key ↔ run-id mapping (pass
    it via --state).
    """
    import sqlite3  # noqa: PLC0415
    state = _load_state()
    other_keys: dict[str, str] = {}
    if args.state:
        other = json.loads(Path(args.state).read_text(encoding="utf-8"))
        other_keys = {v["run_id"]: k for k, v in (other.get("runs") or {}).items()}
    runs_dir = REPO / "streamlit_app" / "data" / "runs"
    n_ok = n_skip = 0
    for src in args.paths:
        src = Path(src)
        candidates = [src] if (src / "params.json").exists() else sorted(
            d for d in src.iterdir() if d.is_dir() and (d / "params.json").exists()
        )
        for d in candidates:
            rid = d.name
            dest = runs_dir / rid
            if not dest.exists():
                import shutil  # noqa: PLC0415
                shutil.copytree(d, dest)
            params = json.loads((dest / "params.json").read_text(encoding="utf-8"))
            ok = (dest / "summary.json").exists()
            # Insert with the ORIGINAL id (create_run would mint a new one).
            db = REPO / "streamlit_app" / "data" / "sharc_orbit.db"
            storage.init_db()
            with sqlite3.connect(db) as cx:
                exists = cx.execute(
                    "SELECT 1 FROM runs WHERE id=?", (rid,)).fetchone()
                if exists:
                    n_skip += 1
                    continue
                now = time.strftime("%Y-%m-%dT%H:%M:%S")
                cx.execute(
                    "INSERT INTO runs (id, kind, method, status, progress_pct, "
                    "params_json, result_path, campaign_id, created_at, "
                    "updated_at, finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (rid, "single", None,
                     "success" if ok else "failed",
                     100.0 if ok else 0.0,
                     json.dumps(params), str(dest), CAMPAIGN_ID, now, now, now),
                )
            key = other_keys.get(rid, rid)
            state["runs"][key] = {
                "run_id": rid,
                "status": "success" if ok else "failed",
                "seed": params.get("seed"),
            }
            n_ok += 1
            print(f"imported {key} -> {rid} ({'success' if ok else 'failed'})")
    _save_state(state)
    print(f"done: {n_ok} imported, {n_skip} already present")


CAMPAIGN_DATA = REPO / "campaign_data" / "changes_1503"
#: Deterministic upload ids so `setup` is idempotent and every machine ends
#: up with the SAME system ids (upload_id:ntc:_).
UPLOAD_ID_HIGH = "c1503high178"
UPLOAD_ID_LOW = "c1503low107"


def cmd_setup(_args) -> None:
    """Register the tracked campaign filings (campaign_data/changes_1503)
    into this machine's app DB. Idempotent — INSERT OR REPLACE on fixed ids.
    Verifies the SHA-256 checksums first, so every machine provably runs the
    same bytes."""
    import hashlib  # noqa: PLC0415
    sums = {}
    for line in (CAMPAIGN_DATA / "CHECKSUMS.sha256").read_text(
            encoding="utf-8").splitlines():
        if line.strip():
            h, name = line.split(None, 1)
            sums[name.strip()] = h
    for name, want in sums.items():
        got = hashlib.sha256((CAMPAIGN_DATA / name).read_bytes()).hexdigest()
        if got != want:
            raise SystemExit(f"CHECKSUM MISMATCH: {name} — re-pull the branch.")
    print(f"checksums OK ({len(sums)} files)")

    uid_h = storage.add_upload(
        label="campaign changes_1503 · USASAT-NGSO-3X 17.8 GHz (324520180)",
        srs_path=CAMPAIGN_DATA / "324520180 SRS.MDB",
        mask_path=CAMPAIGN_DATA / "324520180 Masks.MDB",
        upload_id=UPLOAD_ID_HIGH,
    )
    sid_h = storage.add_system(upload_id=uid_h, ntc_id=NTC_HIGH, mask_id=None,
                               sat_name="USASAT-NGSO-3X")
    uid_l = storage.add_upload(
        label="campaign changes_1503 · USASAT-NGSO-3X 10.7 GHz (323520263)",
        srs_path=CAMPAIGN_DATA / "323520263 USASAT-NGSO-3X SRS 10700 and 14000.MDB",
        mask_path=CAMPAIGN_DATA / "323520263 USASAT-NGSO-3X Masks.MDB",
        upload_id=UPLOAD_ID_LOW,
    )
    sid_l = storage.add_system(upload_id=uid_l, ntc_id=NTC_LOW, mask_id=None,
                               sat_name="USASAT-NGSO-3X")
    print(f"registered: {sid_h}")
    print(f"registered: {sid_l}")
    print("done — `run` will now resolve the campaign filings from "
          "campaign_data/ on this machine.")


def cmd_list(_args) -> None:
    for run_key, row, seed in _expand_rows(None):
        bits = [row["strategy"]]
        if row.get("top_n"):
            bits.append(f"N={row['top_n']} M={row['n_select']}")
        if row.get("hybrid_list_size"):
            bits.append(f"lists={row['hybrid_list_size']}")
        if row["strategy"] == "ref_vector":
            bits.append(f"az={row.get('rv_az', 0)}° el={row.get('rv_el', 90)}° "
                        f"T={row.get('rv_T')}s P={row.get('rv_P')}%")
        if row.get("max_co_freq") is not None:
            bits.append(f"Nco→{row['max_co_freq']}")
        if seed is not None:
            bits.append(f"seed={seed}")
        if row.get("emulate_s1503_2"):
            bits.append("gain-test REMOVED")
        bits.append(f"steps={row.get('steps') or 'auto'}")
        bits.append(f"Step22={'ON' if row.get('step22', True) else 'OFF'}")
        print(f"{run_key:12s} [{row['block']:2s}] " + " · ".join(str(b) for b in bits))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("setup")
    g = sub.add_parser("geometry")
    g.add_argument("--set", nargs="*", metavar="BLK=lat,lon,gso")
    g.add_argument("--from-run", nargs="*", metavar="BLK=run_dir")
    g.add_argument("--bootstrap", default=None, metavar="A|B2",
                   help="resolve the block's WCG by running the 0.1° WCGA "
                        "here once (token 8-step sim)")
    r = sub.add_parser("run")
    r.add_argument("--only", nargs="*", help="campaign IDs to run")
    r.add_argument("--group", nargs="*", choices=["A", "B1", "B2"],
                   help="run whole blocks (machine split): --group A")
    r.add_argument("--force", action="store_true")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--keep-going", action="store_true")
    r.add_argument("--steps-override", type=int, default=None,
                   help="tiny step count for smoke testing the matrix")
    sub.add_parser("report")
    imp = sub.add_parser("import")
    imp.add_argument("paths", nargs="+",
                     help="run folder(s) or a directory of run folders "
                          "copied from another machine")
    imp.add_argument("--state", default=None,
                     help="the other machine's campaign_state.json (recovers "
                          "the campaign-key mapping)")
    args = ap.parse_args()
    {"list": cmd_list, "setup": cmd_setup, "geometry": cmd_geometry,
     "run": cmd_run, "report": cmd_report,
     "import": cmd_import}[args.cmd](args)


if __name__ == "__main__":
    main()
