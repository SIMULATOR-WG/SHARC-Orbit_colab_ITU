"""Country-constrained single-entry worker.

Usage:
    python -m streamlit_app.lib.job_runners.country_wcg_worker <params.json>

Runs the **same** S.1503-4 WCGA + EPFD↓ pipeline as ``s1503_worker``, with the
WCGA ES domain restricted to ``params['country_codes']``. Does not use an
ES×GSO latitude/longitude grid search.

RAAN (Ω) sweep defaults to **auto per orbit** (§D4.6.1 repeating → OFF for
that orbit). Override with ``params['country_raan_sweep']`` =
``"auto"`` | ``"on"`` | ``"off"`` (or bool).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve()
REPO_ROOT = HERE.parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _emit(line: str) -> None:
    print(line, flush=True)


def _patch_artifacts(result_path: Path, meta: dict[str, Any]) -> None:
    for name in ("sim_data.json", "summary.json"):
        p = result_path / name
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        data["country_constrained_wcg"] = meta
        if name == "sim_data.json":
            data["kind"] = "country_constrained_s1503"
            data["wcg_source"] = "country_constrained_wcga"
            wcg = data.get("wcg") or {}
            if wcg and meta.get("es_lat_deg") is None:
                meta = {
                    **meta,
                    "es_lat_deg": wcg.get("es_lat_deg"),
                    "es_lon_deg": wcg.get("es_lon_deg"),
                    "gso_lon_deg": wcg.get("gso_lon_deg"),
                    "epfd_peak_sat_dBW": wcg.get("epfd_dBW"),
                }
                data["country_constrained_wcg"] = meta
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _run(params: dict[str, Any]) -> dict[str, Any]:
    from src.country_constrained_wcg import (  # noqa: PLC0415
        country_constrained_wcga,
        resolve_country_raan_sweep,
    )
    from streamlit_app.lib.job_runners import s1503_worker  # noqa: PLC0415

    result_path = Path(params["result_path"])
    result_path.mkdir(parents=True, exist_ok=True)

    codes = list(params.get("country_codes") or [])
    if not codes:
        raise ValueError("country_codes is required (list of ISO alpha-3)")

    # Same Single-entry engine path: WCGA on, no manual geometry override.
    run_params = dict(params)
    run_params["wcg_manual"] = False
    run_params["wcga_s1503"] = True
    run_params.pop("wcg_manual_es_lat", None)
    run_params.pop("wcg_manual_es_lon", None)
    run_params.pop("wcg_manual_gso_lon", None)

    mode = params.get("country_raan_sweep", "auto")
    policy, det = resolve_country_raan_sweep(
        mode,
        srs_path=params.get("srs_path"),
        ntc_id=params.get("ntc_id"),
    )

    n_rep = int(det.get("n_repeating_planes") or 0)
    n_non = int(det.get("n_non_repeating_planes") or 0)
    mixed = bool(det.get("mixed"))

    if policy is True:
        note = (
            "S.1503-4 WCGA with ES restricted to selected countries; "
            "RAAN (Ω) sweep forced ON for every orbit, then ΔΩ on all "
            "sweep-ON sats (relative to the winner)"
        )
        sweep_label = "ON (forced)"
    elif policy is False:
        note = (
            "S.1503-4 WCGA with ES restricted to selected countries; "
            "RAAN (Ω) sweep forced OFF — filed ground track preserved"
        )
        sweep_label = "OFF (forced)"
    else:
        if mixed:
            note = (
                "S.1503-4 WCGA with ES restricted to selected countries; "
                f"RAAN (Ω) sweep per orbit (auto): {n_rep} repeating plane(s) "
                f"OFF, {n_non} non-repeating ON; ΔΩ on all ON sats "
                "(relative to the winner)"
            )
            sweep_label = f"auto per-orbit (mix: {n_rep} OFF / {n_non} ON)"
        elif det.get("repeating"):
            note = (
                "S.1503-4 WCGA with ES restricted to selected countries; "
                "RAAN (Ω) sweep OFF for all orbits (repeating track / "
                "f_stn_keep)"
            )
            sweep_label = "OFF (auto / repeating)"
        else:
            note = (
                "S.1503-4 WCGA with ES restricted to selected countries; "
                "RAAN (Ω) sweep ON for all orbits (non-repeating), then "
                "ΔΩ on all ON sats (relative to the winner)"
            )
            sweep_label = "ON (auto / non-repeating)"

    meta: dict[str, Any] = {
        "country_codes": codes,
        "search_note": note,
        "s1503_step_deg": run_params.get("s1503_step_deg"),
        "raan_sweep_policy": det.get("policy", "per_orbit"),
        "raan_sweep_mode": det.get("mode", "auto"),
        # Legacy bool: True/False when uniform; None when mixed auto.
        "raan_sweep": det.get("raan_sweep"),
        "filing_repeating_ground_track": bool(det.get("repeating")),
        "filing_f_stn_keep": bool(det.get("f_stn_keep")),
        "filing_rpt_period_s": float(det.get("rpt_period_s") or 0.0),
        "filing_mixed_repeating": mixed,
        "n_repeating_planes": n_rep,
        "n_non_repeating_planes": n_non,
    }
    if det.get("rpt_period_days") is not None:
        meta["filing_rpt_period_days"] = float(det["rpt_period_days"])

    _emit(
        f"Country-constrained WCGA: countries={codes} · "
        f"RAAN sweep={sweep_label} "
        f"(mode={det.get('mode')}, mixed={mixed}, "
        f"repeating_planes={n_rep}, non_repeating_planes={n_non}, "
        f"plane0_f_stn_keep={det.get('f_stn_keep')}, "
        f"plane0_rpt_period_s={det.get('rpt_period_s')})"
    )
    with country_constrained_wcga(codes, raan_sweep=policy) as codes_n:
        meta["country_codes"] = list(codes_n)
        (result_path / "country_constrained_wcg.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8",
        )
        _emit("Delegating to S.1503 worker (WCGA + EPFD↓)")
        summary = s1503_worker._run(run_params)

    # Enrich meta from the written WCG after the run (including no_geometry).
    sim_path = result_path / "sim_data.json"
    if sim_path.exists():
        try:
            sim = json.loads(sim_path.read_text(encoding="utf-8"))
            wcg = sim.get("wcg") or {}
            if wcg:
                meta.update({
                    "es_lat_deg": wcg.get("es_lat_deg"),
                    "es_lon_deg": wcg.get("es_lon_deg"),
                    "gso_lon_deg": wcg.get("gso_lon_deg"),
                    "epfd_peak_sat_dBW": wcg.get("epfd_dBW"),
                })
            align = (sim.get("config") or {}).get("_country_wcg_alignment") or {}
            if align:
                meta["delta_raan_deg"] = align.get("delta_raan_deg")
                meta["n_sats_aligned"] = align.get("n_sats_aligned")
            if sim.get("compliance") == "no_geometry":
                meta["no_geometry"] = True
                meta["no_geometry_message"] = sim.get("message")
                diag = sim.get("diagnostics") or {}
                if diag:
                    meta["no_geometry_diagnostics"] = diag
        except (OSError, json.JSONDecodeError):
            pass
    (result_path / "country_constrained_wcg.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8",
    )
    _patch_artifacts(result_path, meta)
    return summary


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: country_wcg_worker <params.json>", file=sys.stderr)
        return 2
    params_path = Path(sys.argv[1])
    if not params_path.exists():
        print(f"params not found: {params_path}", file=sys.stderr)
        return 2
    try:
        params = json.loads(params_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"invalid params JSON: {exc}", file=sys.stderr)
        return 2
    try:
        _run(params)
    except Exception as exc:  # noqa: BLE001
        _emit(f"ERROR:{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
