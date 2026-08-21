#!/usr/bin/env python3
"""Plots the CDF (and optionally the PDF) of elevation_deg from a
contributing_sat_elevations.csv (or .csv.gz) artifact produced by
SHARC-Orbit (src/main.py or the Streamlit Results page).

Usage:
    python plot_contributing_elevation_cdf.py contributing_sat_elevations.csv
    python plot_contributing_elevation_cdf.py run.csv.gz --pdf -o elev.png --show
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd


def plot_elevation(
    plt, csv_path: Path, output_path: Path | None, show: bool,
    include_pdf: bool, bins: int,
) -> Path:
    df = pd.read_csv(csv_path, comment="#")
    if "elevation_deg" not in df.columns:
        raise ValueError(
            f"'elevation_deg' column not found in {csv_path} "
            f"(columns: {list(df.columns)})"
        )

    elev = np.sort(df["elevation_deg"].to_numpy(dtype=float))
    n = elev.size
    if n == 0:
        raise ValueError(f"No elevation samples in {csv_path}")
    cdf = np.arange(1, n + 1) / n

    ncols = 2 if include_pdf else 1
    fig, axes = plt.subplots(1, ncols, figsize=(8 * ncols, 5), dpi=150)
    ax_cdf = axes[0] if include_pdf else axes

    ax_cdf.step(elev, cdf, where="post", color="#0e7490", lw=1.8)
    ax_cdf.set_xlabel("Elevation angle (°)")
    ax_cdf.set_ylabel("Cumulative probability")
    ax_cdf.set_title("CDF")
    ax_cdf.set_ylim(0.0, 1.0)
    ax_cdf.grid(True, alpha=0.3)

    if include_pdf:
        ax_pdf = axes[1]
        ax_pdf.hist(elev, bins=bins, density=True, color="#0e7490", alpha=0.75,
                    edgecolor="white", linewidth=0.5)
        ax_pdf.set_xlabel("Elevation angle (°)")
        ax_pdf.set_ylabel("Probability density")
        ax_pdf.set_title("PDF")
        ax_pdf.grid(True, alpha=0.3)

    fig.suptitle(f"Elevation — EPFD-contributing satellites\n{csv_path.name} ({n} samples)")
    fig.tight_layout()

    default_suffix = ".cdf_pdf.png" if include_pdf else ".cdf.png"
    out = output_path or csv_path.with_suffix("").with_suffix(default_suffix)
    fig.savefig(out)
    print(f"Samples: {n}  min={elev[0]:.2f}°  median={np.median(elev):.2f}°  max={elev[-1]:.2f}°")
    print(f"Plot saved at: {out}")

    if show:
        plt.show()
    plt.close(fig)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path, help="Path to contributing_sat_elevations.csv[.gz]")
    parser.add_argument("-o", "--output", type=Path, default=None,
                         help="Output image path (default: <csv_path>.cdf.png, "
                              "or .cdf_pdf.png when --pdf is set)")
    parser.add_argument("--pdf", action="store_true",
                         help="Also plot the PDF (histogram) alongside the CDF")
    parser.add_argument("--bins", type=int, default=40,
                         help="Number of histogram bins for the PDF (default: 40)")
    parser.add_argument("--show", action="store_true", help="Also display the plot interactively")
    args = parser.parse_args()

    if not args.csv_path.exists():
        print(f"File not found: {args.csv_path}", file=sys.stderr)
        sys.exit(1)

    import matplotlib
    if not args.show:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plot_elevation(plt, args.csv_path, args.output, args.show, args.pdf, args.bins)


if __name__ == "__main__":
    main()
