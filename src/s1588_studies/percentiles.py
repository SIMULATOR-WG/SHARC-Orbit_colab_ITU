"""
percentiles.py — Normative percentiles Resolution 76 / S.1588.

Standard percentiles (% of time during which EPFD > x):
    10 %, 1 %, 0.1 %, 0.01 %

CCDF is P[EPFD > x]. To extract the percentile p%:
    find x* such that CCDF(x*) = p/100.
"""

from __future__ import annotations
from typing import Sequence
import numpy as np


NORMATIVE_PERCENTAGES: tuple[float, ...] = (10.0, 1.0, 0.1, 0.01)


def percentile_from_ccdf(
    epfd_bins: Sequence[float],
    ccdf: Sequence[float],
    percentage: float,
) -> float:
    """Return x such that CCDF(x) = percentage / 100.

    `percentage` in % (e.g.: 1.0 = 1%). Performs linear interpolation between bins.
    If the CCDF never reaches the level (the curve starts below), returns bin[0].
    If it never decays below it, returns bin[-1].
    """
    if not 0.0 < percentage <= 100.0:
        raise ValueError(f"percentage must be in (0, 100]: {percentage}")
    bins = np.asarray(epfd_bins, dtype=float)
    cc = np.asarray(ccdf, dtype=float)
    if bins.shape != cc.shape:
        raise ValueError(f"shapes differ: {bins.shape} vs {cc.shape}")
    if bins.size == 0:
        raise ValueError("epfd_bins empty")

    target = percentage / 100.0

    if cc[0] <= target:
        return float(bins[0])
    if cc[-1] >= target:
        return float(bins[-1])

    idx = np.searchsorted(-cc, -target, side="left")
    idx = int(np.clip(idx, 1, bins.size - 1))

    x0, x1 = bins[idx - 1], bins[idx]
    y0, y1 = cc[idx - 1], cc[idx]
    if y0 == y1:
        return float(x0)
    return float(x0 + (target - y0) * (x1 - x0) / (y1 - y0))


def extract_percentiles(
    epfd_bins: Sequence[float],
    ccdf: Sequence[float],
    percentages: Sequence[float] = NORMATIVE_PERCENTAGES,
) -> dict[float, float]:
    """Return `{percentage: epfd_x}` for each requested level."""
    return {
        float(p): percentile_from_ccdf(epfd_bins, ccdf, p) for p in percentages
    }
