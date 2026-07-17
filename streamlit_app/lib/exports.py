"""exports.py — XLSX exports (campaign report + per-run ITU-style workbook).

Uses openpyxl directly so we keep the dependency footprint small. Pandas is
used only for the simpler tabular sheets.
"""
from __future__ import annotations

import io
from typing import Any, Iterable

import pandas as pd


def run_to_xlsx(sim_data: dict[str, Any], params: dict[str, Any] | None = None) -> bytes:
    """Per-run workbook mirroring the ITU BR EPFDResults layout (R24/R25).

    Sheets (each maps to a normative block of S.1503-4):
      ``run_def``    — run attributes / background info (§D7.2, §D2.1)
      ``result_def`` — specification points Ji/Pi (§D7.1.3): Article 22 and,
                       for aggregate runs, Resolution 76 (``limit_set`` column)
      ``results``    — statement + Table 17 rows (§D7.3.1 / §D7.3.2)
      ``cdf``        — the headline CDF/CCDF table (§D7.3.3; the envelope on
                       aggregate method_2 runs)
      ``pdf``        — the 0.1 dB-bin histogram (§D7.1.1), when embedded
      ``cdf_per_point``/``cdf_per_system`` — the full overlay curves shown in
                       the Results-page chart, when the artifact carries them
    Units follow A2.1 Table 1 and are spelled out in the column headers.
    """
    ident = sim_data.get("identification") or {}
    art22 = sim_data.get("article22") or {}
    units = sim_data.get("units") or {}
    dts = sim_data.get("dual_time_step") or {}
    wcg = sim_data.get("wcg") or {}
    epfd_unit = units.get("epfd") or "dBW/m^2/40kHz"

    run_def = pd.DataFrame(
        [
            ("ntc_id", ident.get("ntc_id")),
            ("sat_name", ident.get("sat_name")),
            ("mask_id", ident.get("mask_id")),
            ("mask_source", ident.get("mask_source")),
            ("epfd_type", sim_data.get("epfd_type")),
            ("service", art22.get("service")),
            ("frequency_run_mhz", art22.get("frequency_run_mhz")),
            ("reference_bandwidth_khz", art22.get("reference_bandwidth_khz")),
            ("es_antenna_diameter_cm", art22.get("_epfd_rf_diam_cm")
             or art22.get("epfd_rf_diam_cm")),
            ("es_reference_pattern", art22.get("_epfd_rf_pattern_rr")
             or art22.get("epfd_rf_pattern_rr")),
            ("input_source", sim_data.get("input_source")),
            ("n_satellites", sim_data.get("n_satellites")),
            ("fine_step_s", dts.get("fine_step_s")),
            ("coarse_step_s", dts.get("coarse_step_s")),
            ("num_time_steps_fine_equiv", dts.get("num_time_steps")),
            ("n_exec_steps", dts.get("n_exec_steps")),
            ("wcg_es_lat_deg", wcg.get("es_lat_deg")),
            ("wcg_es_lon_deg", wcg.get("es_lon_deg")),
            ("wcg_gso_lon_deg", wcg.get("gso_lon_deg")),
        ],
        columns=["parameter", "value"],
    )

    res76 = sim_data.get("resolution76") or {}
    _lim_rows = [
        ("Article 22", float(l[0]), float(l[1]))
        for l in (art22.get("limits") or [])
        if isinstance(l, (list, tuple)) and len(l) >= 2
    ] + [
        ("Resolution 76 (aggregate)", float(l[0]), float(l[1]))
        for l in (res76.get("limits") or [])
        if isinstance(l, (list, tuple)) and len(l) >= 2
    ]
    result_def = pd.DataFrame(
        _lim_rows,
        columns=["limit_set", f"Ji_epfd [{epfd_unit}]", "Pi_pct [%]"],
    )

    t17 = sim_data.get("table17") or []
    results = pd.DataFrame(
        [
            {
                f"Ji_epfd [{epfd_unit}]": r.get("Ji_dBW"),
                "Pi_pct [%]": r.get("Pi_pct"),
                "result": "Pass" if r.get("pass") else "Fail",
                "Py_pct [%]": r.get("Py_pct"),
            }
            for r in t17
        ]
    )
    verdict = pd.DataFrame(
        [("overall_result", str(sim_data.get("compliance") or "unknown").upper()),
         ("worst_margin_dB",
          (sim_data.get("compliance_detail") or {}).get("worst_margin_dB"))],
        columns=["parameter", "value"],
    )

    cdf = pd.DataFrame({
        f"epfd [{epfd_unit}]": sim_data.get("ccdf_bins_db") or [],
        "pct_time_exceeded [%]": sim_data.get("ccdf_pct") or [],
    })

    hist = sim_data.get("histogram") or {}
    pdf_df = pd.DataFrame({
        f"bin_low [{epfd_unit}]": hist.get("bin_low_db") or [],
        "duration_s [s]": hist.get("duration_s") or [],
        "probability [fraction]": hist.get("probability") or [],
    })

    def _curves_sheet(entries: list, tag: str) -> pd.DataFrame:
        """One (epfd, pct) column pair per stored curve — ragged lengths padded
        with blanks so every point of every curve survives the export."""
        cols: dict[str, pd.Series] = {}
        for i, p in enumerate(entries):
            b, q = p.get("ccdf_bins_db"), p.get("ccdf_pct")
            if not b or not q:
                continue
            key = p.get("index", p.get("system_index", i))
            cols[f"{tag}{key}_epfd [{epfd_unit}]"] = pd.Series(b, dtype=float)
            cols[f"{tag}{key}_pct [%]"] = pd.Series(q, dtype=float)
        return pd.DataFrame(cols)

    per_point_df = _curves_sheet(sim_data.get("per_point") or [], "pt")
    per_system_df = _curves_sheet(sim_data.get("per_system") or [], "sys")

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        run_def.to_excel(xw, sheet_name="run_def", index=False)
        result_def.to_excel(xw, sheet_name="result_def", index=False)
        verdict.to_excel(xw, sheet_name="results", index=False, startrow=0)
        if not results.empty:
            results.to_excel(xw, sheet_name="results", index=False,
                             startrow=len(verdict) + 2)
        cdf.to_excel(xw, sheet_name="cdf", index=False)
        pdf_df.to_excel(xw, sheet_name="pdf", index=False)
        if not per_point_df.empty:
            per_point_df.to_excel(xw, sheet_name="cdf_per_point", index=False)
        if not per_system_df.empty:
            per_system_df.to_excel(xw, sheet_name="cdf_per_system", index=False)
    return buf.getvalue()


def campaign_to_xlsx(
    *,
    campaign: dict[str, Any],
    runs: Iterable[dict[str, Any]],
) -> bytes:
    """Render a multi-sheet XLSX for an Anatel campaign.

    Sheet `campaign`   — single row with campaign metadata.
    Sheet `runs`       — one row per child run (status, method, params, metrics).
    Sheet `params`     — denormalized run params.
    """
    runs_list = list(runs)
    cdf = pd.DataFrame([
        {
            "id": campaign.get("id"),
            "label": campaign.get("label"),
            "created_at": campaign.get("created_at"),
            "n_runs": len(runs_list),
        }
    ])
    rdf = pd.DataFrame(
        [
            {
                "id": r.get("id"),
                "kind": r.get("kind"),
                "method": r.get("method"),
                "status": r.get("status"),
                "progress_pct": r.get("progress_pct"),
                "created_at": r.get("created_at"),
                "updated_at": r.get("updated_at"),
                "error_message": r.get("error_message"),
                "result_path": r.get("result_path"),
            }
            for r in runs_list
        ]
    )
    pdf_rows: list[dict[str, Any]] = []
    import json

    for r in runs_list:
        try:
            params = json.loads(r.get("params_json") or "{}")
        except Exception:  # noqa: BLE001
            params = {}
        pdf_rows.append({"run_id": r.get("id"), **{k: str(v) for k, v in params.items()}})
    pdf = pd.DataFrame(pdf_rows)

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        cdf.to_excel(xw, sheet_name="campaign", index=False)
        rdf.to_excel(xw, sheet_name="runs", index=False)
        if not pdf.empty:
            pdf.to_excel(xw, sheet_name="params", index=False)
    return buf.getvalue()
