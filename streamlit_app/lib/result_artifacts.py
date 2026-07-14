"""result_artifacts.py — per-run result artifacts (CSV + PNG) for R9–R23.

Writes, next to ``sim_data.json`` in the run directory:

* ``ccdf_epfd.csv``       — CCDF table (S.1503-4 §D7.3.3 / §D5.1.6: EPFD level
                            × % of time exceeded). Requirement R11.
* ``epfd_histogram.csv``  — the PDF/histogram behind the CCDF (§D7.1.1, 0.1 dB
                            bins per §D1.4), from the streaming accumulator.
                            Requirement R13.
* ``epfd_timeseries.csv`` — decimated EPFD↓ vs time trace kept by the
                            accumulator (adaptive stride; the *statistics* are
                            accumulated over every step regardless).
                            Requirement R15.
* ``ccdf.png``            — CCDF curve image (R12).
* ``histogram.png``       — histogram image (R14).

All CSVs carry ``#`` header lines stating the units (A2.1 Table 1 of
S.1503-4) — requirement R23. Images use the matplotlib Agg backend (headless,
no browser/kaleido dependency).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

# 0.1 dB bin grid of the streaming accumulator (S.1503-4 §D1.4).
from src.epfd_stream_accumulator import _BIN_MIN_DB, _BIN_SIZE_DB  # noqa: PLC2701


def _refbw_khz(sim_data: dict[str, Any]) -> float:
    art22 = sim_data.get("article22") or {}
    try:
        return float(art22.get("reference_bandwidth_khz") or 40.0)
    except (TypeError, ValueError):
        return 40.0


def _epfd_unit(sim_data: dict[str, Any]) -> str:
    return f"dBW/m^2/{_refbw_khz(sim_data):.0f}kHz"


def write_ccdf_csv(result_path: Path, sim_data: dict[str, Any]) -> str | None:
    """``ccdf_epfd.csv`` — two columns: EPFD level, % of time exceeded."""
    bins = sim_data.get("ccdf_bins_db") or []
    pct = sim_data.get("ccdf_pct") or []
    if not bins or not pct or len(bins) != len(pct):
        return None
    unit = _epfd_unit(sim_data)
    lines = [
        "# SHARC-Orbit CCDF table (S.1503-4 D7.3.3) — cumulative distribution "
        "used in the decision process",
        f"# units: epfd_db [{unit}] · pct_time_exceeded [% of simulated time]",
        "epfd_db,pct_time_exceeded",
    ]
    lines += [f"{float(b):.1f},{float(p):.10g}" for b, p in zip(bins, pct)]
    out = result_path / "ccdf_epfd.csv"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out.name


def write_histogram_csv(result_path: Path, sim_data: dict[str, Any],
                        acc: Any | None) -> str | None:
    """``epfd_histogram.csv`` — the PDF (§D7.1.1): 0.1 dB bins, duration and
    probability per bin. Only non-empty bins are written."""
    dur = getattr(acc, "duration_per_bin", None)
    if dur is None:
        return None
    dur = np.asarray(dur, dtype=float)
    total = float(dur.sum())
    if total <= 0.0:
        return None
    nz = np.nonzero(dur > 0.0)[0]
    unit = _epfd_unit(sim_data)
    lines = [
        "# SHARC-Orbit EPFD histogram/PDF (S.1503-4 D7.1.1; bin size 0.1 dB "
        "per D1.4), weighted by real step duration (D7.1.3)",
        f"# units: bin_low_db/bin_high_db [{unit}] · duration_s [s] · "
        "probability [fraction of simulated time]",
        "bin_low_db,bin_high_db,duration_s,probability",
    ]
    for i in nz:
        lo = _BIN_MIN_DB + i * _BIN_SIZE_DB
        lines.append(
            f"{lo:.1f},{lo + _BIN_SIZE_DB:.1f},{dur[i]:.10g},{dur[i] / total:.10g}"
        )
    out = result_path / "epfd_histogram.csv"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out.name


def write_timeseries_csv(result_path: Path, sim_data: dict[str, Any],
                         acc: Any | None) -> str | None:
    """``epfd_timeseries.csv`` — the accumulator's decimated EPFD↓ trace.

    The trace is DECIMATED (adaptive stride toward ~10k points); the normative
    statistics (PDF/CCDF) are accumulated over every step regardless, so the
    decimation is presentation-only. Stated in the header.
    """
    t = list(getattr(acc, "decim_t_s", None) or [])
    e = list(getattr(acc, "decim_epfd_db", None) or [])
    d = list(getattr(acc, "decim_duration_s", None) or [])
    if not t or len(t) != len(e):
        return None
    if len(d) != len(t):
        d = [float("nan")] * len(t)
    unit = _epfd_unit(sim_data)
    stride = int(getattr(acc, "decim_stride", 1) or 1)
    n_steps = int(getattr(acc, "n_steps", 0) or 0)
    lines = [
        "# SHARC-Orbit EPFD time series (decimated trace; adaptive stride "
        f"{stride}, {len(t)} of {n_steps} steps). Statistics (PDF/CCDF) are "
        "accumulated over ALL steps — decimation affects this trace only.",
        f"# units: t_s [s] · epfd_db [{unit}] · duration_s [s]",
        "t_s,epfd_db,duration_s",
    ]
    lines += [
        f"{float(ti):.6g},{float(ei):.4f},{float(di):.6g}"
        for ti, ei, di in zip(t, e, d)
    ]
    payload = "\n".join(lines) + "\n"
    # R16 — compressed container for long traces (cheap, self-describing).
    if len(t) > 50_000:
        import gzip
        out = result_path / "epfd_timeseries.csv.gz"
        out.write_bytes(gzip.compress(payload.encode("utf-8")))
        return out.name
    out = result_path / "epfd_timeseries.csv"
    out.write_text(payload, encoding="utf-8")
    return out.name


def _mpl():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def write_ccdf_png(result_path: Path, sim_data: dict[str, Any]) -> str | None:
    """``ccdf.png`` — CCDF on a log-y axis, with the Article 22 limit points
    when present in the artifact."""
    bins = sim_data.get("ccdf_bins_db") or []
    pct = sim_data.get("ccdf_pct") or []
    if not bins or not pct:
        return None
    plt = _mpl()
    fig, ax = plt.subplots(figsize=(8, 5), dpi=150)
    ax.semilogy(bins, pct, lw=1.6, color="#0e7490", label="EPFD↓ CCDF")
    limits = ((sim_data.get("article22") or {}).get("limits")) or []
    lx = [float(l[0]) for l in limits if isinstance(l, (list, tuple)) and len(l) >= 2]
    ly = [float(l[1]) for l in limits if isinstance(l, (list, tuple)) and len(l) >= 2]
    if lx:
        ax.plot(lx, ly, "s--", ms=4, lw=1.0, color="#b91c1c", label="Article 22 limit")
    unit = _epfd_unit(sim_data)
    ax.set_xlabel(f"EPFD↓ [{unit}]")
    ax.set_ylabel("% of time exceeded")
    ax.set_title("EPFD↓ CCDF (S.1503-4 D7.3.3)")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(loc="lower left", fontsize=8)
    fig.tight_layout()
    out = result_path / "ccdf.png"
    fig.savefig(out)
    plt.close(fig)
    return out.name


def write_histogram_png(result_path: Path, sim_data: dict[str, Any],
                        acc: Any | None) -> str | None:
    """``histogram.png`` — probability per 0.1 dB bin (PDF, §D7.1.1)."""
    dur = getattr(acc, "duration_per_bin", None)
    if dur is None:
        return None
    dur = np.asarray(dur, dtype=float)
    total = float(dur.sum())
    nz = np.nonzero(dur > 0.0)[0]
    if total <= 0.0 or nz.size == 0:
        return None
    x = _BIN_MIN_DB + nz * _BIN_SIZE_DB
    p = dur[nz] / total
    plt = _mpl()
    fig, ax = plt.subplots(figsize=(8, 5), dpi=150)
    ax.bar(x, p, width=_BIN_SIZE_DB, color="#0e7490", edgecolor="none")
    unit = _epfd_unit(sim_data)
    ax.set_xlabel(f"EPFD↓ [{unit}]")
    ax.set_ylabel("probability (fraction of time)")
    ax.set_title("EPFD↓ distribution — 0.1 dB bins (S.1503-4 D7.1.1)")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    out = result_path / "histogram.png"
    fig.savefig(out)
    plt.close(fig)
    return out.name


def _geometry_rows(sim_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Tested geometries: s1588 aggregate ``per_point`` sweep, or the single
    WCG of an s1503 run (§D3). ``measure_id`` = per_point index (R10)."""
    pts = sim_data.get("per_point") or []
    if pts:
        return [
            {"measure_id": int(p.get("index", k)),
             "es_lat_deg": p.get("es_lat_deg"), "es_lon_deg": p.get("es_lon_deg"),
             "gso_lon_deg": p.get("gso_lon_deg"),
             "max_epfd_db": p.get("max_epfd_dbw")}
            for k, p in enumerate(pts)
        ]
    wcg = sim_data.get("wcg") or {}
    if wcg.get("es_lat_deg") is not None:
        return [{"measure_id": 0,
                 "es_lat_deg": wcg.get("es_lat_deg"),
                 "es_lon_deg": wcg.get("es_lon_deg"),
                 "gso_lon_deg": wcg.get("gso_lon_deg"),
                 "max_epfd_db": sim_data.get("max_epfd_dbw_m2_40khz")}]
    return []


def write_geometries_csv(result_path: Path, sim_data: dict[str, Any]) -> str | None:
    """``geometries.csv`` — tested geometries (ES lat/lon + GSO longitude)
    indexed by measure id (R10)."""
    rows = _geometry_rows(sim_data)
    if not rows:
        return None
    unit = _epfd_unit(sim_data)
    lines = [
        "# SHARC-Orbit tested geometries (S.1503-4 D3 WCG / aggregate sweep)",
        f"# units: lat/lon [deg] · max_epfd_db [{unit}]",
        "measure_id,es_lat_deg,es_lon_deg,gso_lon_deg,max_epfd_db",
    ]
    for r in rows:
        me = r["max_epfd_db"]
        lines.append(
            f"{r['measure_id']},{float(r['es_lat_deg']):.4f},"
            f"{float(r['es_lon_deg']):.4f},{float(r['gso_lon_deg']):.4f},"
            + (f"{float(me):.2f}" if isinstance(me, (int, float)) else "")
        )
    out = result_path / "geometries.csv"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out.name


def write_geometry_map_png(result_path: Path, sim_data: dict[str, Any]) -> str | None:
    """``map.png`` — the tested geometry points on a plate-carrée map (R10).

    ES points colour-coded by max EPFD when available; GSO sub-satellite
    longitudes marked on the equator. Headless matplotlib; coastlines are
    omitted (no cartopy dependency) — the lat/lon grid gives the frame.
    """
    rows = _geometry_rows(sim_data)
    if not rows:
        return None
    plt = _mpl()
    fig, ax = plt.subplots(figsize=(9, 5), dpi=150)
    lats = [float(r["es_lat_deg"]) for r in rows]
    lons = [float(r["es_lon_deg"]) for r in rows]
    vals = [r["max_epfd_db"] for r in rows]
    have_vals = all(isinstance(v, (int, float)) for v in vals) and len(vals) > 1
    if have_vals:
        sc = ax.scatter(lons, lats, c=[float(v) for v in vals], s=28,
                        cmap="inferno", zorder=3, label="ES (tested)")
        fig.colorbar(sc, ax=ax, shrink=0.8,
                     label=f"max EPFD↓ [{_epfd_unit(sim_data)}]")
    else:
        ax.scatter(lons, lats, s=48, marker="*", color="#0e7490", zorder=3,
                   label="ES (tested)")
    glons = sorted({round(float(r["gso_lon_deg"]), 3) for r in rows})
    ax.scatter(glons, [0.0] * len(glons), marker="D", s=30, color="#b91c1c",
               zorder=3, label="GSO sub-satellite")
    ax.set_xlim(-180, 180)
    ax.set_ylim(-90, 90)
    ax.set_xticks(range(-180, 181, 30))
    ax.set_yticks(range(-90, 91, 30))
    ax.grid(True, alpha=0.35)
    ax.axhline(0.0, color="#94a3b8", lw=0.8)
    ax.set_xlabel("longitude [deg]")
    ax.set_ylabel("latitude [deg]")
    ax.set_title("Tested geometries (ES × GSO)")
    ax.legend(loc="lower left", fontsize=8)
    fig.tight_layout()
    out = result_path / "map.png"
    fig.savefig(out)
    plt.close(fig)
    return out.name


def write_table17_csv(result_path: Path, sim_data: dict[str, Any]) -> str | None:
    """``table17.csv`` — the §D7.3.2 summary table (Ji, Pi, Pass/fail, Py)."""
    t17 = sim_data.get("table17") or []
    if not t17:
        return None
    unit = _epfd_unit(sim_data)
    lines = [
        "# SHARC-Orbit summary table (S.1503-4 D7.3.2, Table 17) — one row "
        "per Article 22 specification point",
        f"# units: Ji_dBW [{unit}] · Pi_pct/Py_pct [% of time]",
        "Ji_dBW,Pi_pct,result,Py_pct",
    ]
    lines += [
        f"{float(r.get('Ji_dBW')):.1f},{float(r.get('Pi_pct')):.6g},"
        f"{'Pass' if r.get('pass') else 'Fail'},{float(r.get('Py_pct')):.6g}"
        for r in t17
    ]
    out = result_path / "table17.csv"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out.name


def write_run_artifacts(result_path: Path, sim_data: dict[str, Any],
                        acc: Any | None = None) -> list[str]:
    """Write every artifact that has data available; returns the file names.

    Individual writers fail soft (a plotting error must never kill a finished
    simulation) — callers may log the returned list for visibility.
    """
    written: list[str] = []
    for fn in (
        lambda: write_ccdf_csv(result_path, sim_data),
        lambda: write_histogram_csv(result_path, sim_data, acc),
        lambda: write_timeseries_csv(result_path, sim_data, acc),
        lambda: write_table17_csv(result_path, sim_data),
        lambda: write_geometries_csv(result_path, sim_data),
        lambda: write_geometry_map_png(result_path, sim_data),
        lambda: write_ccdf_png(result_path, sim_data),
        lambda: write_histogram_png(result_path, sim_data, acc),
    ):
        try:
            name = fn()
        except Exception:  # noqa: BLE001 — artifacts are best-effort
            name = None
        if name:
            written.append(name)
    return written
