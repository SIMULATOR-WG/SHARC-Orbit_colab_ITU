"""report.py — per-run summary report following S.1503-4 §D7.3 (R17–R22).

``write_summary_html(result_path, sim_data)`` renders a self-contained
``summary.html`` with the three normative blocks:

1. **Statement of the result** (§D7.3.1) — overall Pass/Fail per §D7.1.4.
2. **Summary table** (§D7.3.2, Table 17) — one row per Article 22
   specification point: Ji, Pi, Pass/fail, Py.
3. **Probability (CDF) table** (§D7.3.3) — the calculated CDF used in the
   decision process (rendered as a scrollable table + embedded CCDF image
   when ``ccdf.png`` exists in the run directory).

Plus the background information §D7.2 mandates (antenna diameter, reference
pattern, limits table) and the run identification (R18–R21). Pure f-string
HTML — no template-engine or browser dependency.
"""
from __future__ import annotations

import base64
import html as _html
from pathlib import Path
from typing import Any


def _esc(v: Any) -> str:
    return _html.escape("" if v is None else str(v))


_CSS = """
body{font:14px/1.5 system-ui,sans-serif;color:#111;margin:2rem auto;max-width:960px;padding:0 1rem}
h1{font-size:1.35rem;border-bottom:2px solid #0e7490;padding-bottom:.3rem}
h2{font-size:1.05rem;margin-top:1.6rem;color:#0e7490}
table{border-collapse:collapse;margin:.6rem 0;width:100%}
th,td{border:1px solid #cbd5e1;padding:.3rem .55rem;text-align:right;font-size:13px}
th{background:#e0f2fe;text-align:center}
td.l{text-align:left}
.pass{color:#15803d;font-weight:700}.fail{color:#b91c1c;font-weight:700}
.statement{font-size:1.15rem;padding:.6rem .9rem;border-radius:8px;display:inline-block;margin:.4rem 0}
.statement.pass{background:#dcfce7}.statement.fail{background:#fee2e2}
.scroll{max-height:340px;overflow-y:auto;border:1px solid #cbd5e1}
.scroll table{margin:0;border:none}
.meta{color:#555;font-size:12px}
img{max-width:100%;border:1px solid #cbd5e1;border-radius:6px}
footer{margin-top:2rem;font-size:11px;color:#777;border-top:1px solid #ddd;padding-top:.5rem}
"""


def summary_html(sim_data: dict[str, Any], *, ccdf_png: bytes | None = None) -> str:
    """Render the §D7.3 report as a self-contained HTML string."""
    ident = sim_data.get("identification") or {}
    art22 = sim_data.get("article22") or {}
    units = sim_data.get("units") or {}
    dts = sim_data.get("dual_time_step") or {}
    detail = sim_data.get("compliance_detail") or {}
    epfd_unit = units.get("epfd") or "dBW/m^2/40kHz"

    comp = str(sim_data.get("compliance") or "unknown").lower()
    verdict = {"pass": "PASS", "fail": "FAIL"}.get(comp, comp.upper())
    vclass = "pass" if comp == "pass" else "fail"

    # ── Table 17 ──
    t17 = sim_data.get("table17") or []
    if t17:
        rows = "\n".join(
            "<tr>"
            f"<td>{r.get('Ji_dBW'):.1f}</td><td>{r.get('Pi_pct'):.5g}</td>"
            f"<td class='{'pass' if r.get('pass') else 'fail'}'>"
            f"{'Pass' if r.get('pass') else 'Fail'}</td>"
            f"<td>{r.get('Py_pct'):.5g}</td></tr>"
            for r in t17
        )
        t17_html = f"""
<table>
<tr><th colspan="2">Specification point</th><th>Result</th><th>Simulation point</th></tr>
<tr><th>J<sub>i</sub> [{_esc(epfd_unit)}]</th><th>P<sub>i</sub> [%]</th>
<th>Pass/fail</th><th>P<sub>y</sub> [%]</th></tr>
{rows}
</table>"""
    else:
        t17_html = "<p class='meta'>No specification points available.</p>"

    # ── CDF table (D7.3.3) ──
    bins = sim_data.get("ccdf_bins_db") or []
    pct = sim_data.get("ccdf_pct") or []
    cdf_rows = "\n".join(
        f"<tr><td>{float(b):.1f}</td><td>{float(p):.6g}</td></tr>"
        for b, p in zip(bins, pct)
    )
    cdf_html = (
        f"<div class='scroll'><table><tr><th>EPFD↓ [{_esc(epfd_unit)}]</th>"
        f"<th>% time exceeded</th></tr>{cdf_rows}</table></div>"
        if bins else "<p class='meta'>No CDF data.</p>"
    )
    img_html = ""
    if ccdf_png:
        b64 = base64.b64encode(ccdf_png).decode("ascii")
        img_html = f"<p><img alt='CCDF' src='data:image/png;base64,{b64}'></p>"

    # ── identification / background (D7.2, R18–R21) ──
    def _row(k: str, v: Any) -> str:
        return f"<tr><td class='l'>{_esc(k)}</td><td class='l'>{_esc(v)}</td></tr>"

    ident_rows = [
        _row("Notice (ntc_id)", ident.get("ntc_id")),
        _row("Satellite system", ident.get("sat_name")),
        _row("PFD mask id / source", f"{ident.get('mask_id')} / {ident.get('mask_source')}"),
        _row("EPFD type (Direction §D2.1)", sim_data.get("epfd_type")),
        _row("Service", art22.get("service")),
        _row("Run frequency [MHz]", art22.get("frequency_run_mhz")),
        _row("Reference bandwidth [kHz]", art22.get("reference_bandwidth_khz")),
        _row("ES antenna diameter [cm]", art22.get("_epfd_rf_diam_cm")
             or art22.get("epfd_rf_diam_cm")),
        _row("ES reference pattern", art22.get("_epfd_rf_pattern_rr")
             or art22.get("epfd_rf_pattern_rr")),
        _row("Input source", sim_data.get("input_source")),
        _row("Satellites simulated", sim_data.get("n_satellites")),
        _row("Fine / coarse time step [s]",
             f"{dts.get('fine_step_s')} / {dts.get('coarse_step_s')}"),
        _row("Time steps (fine-equivalent / executed)",
             f"{dts.get('num_time_steps')} / {dts.get('n_exec_steps')}"),
        _row("Worst margin [dB]", detail.get("worst_margin_dB")),
    ]
    wcg = sim_data.get("wcg") or {}
    if wcg:
        ident_rows.append(_row(
            "WCG (ES lat, ES lon, GSO lon) [deg]",
            f"{wcg.get('es_lat_deg'):.3f}, {wcg.get('es_lon_deg'):.3f}, "
            f"{wcg.get('gso_lon_deg'):.3f}",
        ))

    units_line = " · ".join(f"{k}: {v}" for k, v in units.items())

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>SHARC-Orbit — EPFD run summary</title><style>{_CSS}</style></head><body>
<h1>EPFD examination summary — Rec. ITU-R S.1503-4 §D7.3</h1>

<h2>1 · Statement of the result (§D7.3.1)</h2>
<p class="statement {vclass}">{verdict}</p>

<h2>2 · Summary table (§D7.3.2, Table 17)</h2>
{t17_html}

<h2>3 · Probability table — CDF (§D7.3.3, for information)</h2>
{img_html}
{cdf_html}

<h2>Run identification &amp; background information (§D7.2)</h2>
<table>{''.join(ident_rows)}</table>

<footer>Units (S.1503-4 A2.1 Table 1): {_esc(units_line)}<br>
Generated by SHARC-Orbit.</footer>
</body></html>"""


def write_summary_html(result_path: Path, sim_data: dict[str, Any]) -> str | None:
    """Write ``summary.html`` in the run directory; embeds ``ccdf.png`` when
    present. Returns the file name, or ``None`` when there is nothing to say
    (no compliance information at all)."""
    if not (sim_data.get("compliance") or sim_data.get("table17")):
        return None
    png = None
    p = Path(result_path) / "ccdf.png"
    if p.exists():
        png = p.read_bytes()
    out = Path(result_path) / "summary.html"
    out.write_text(summary_html(sim_data, ccdf_png=png), encoding="utf-8")
    return out.name
