"""
convolution.py — Convolution of EPFD CCDFs (Resolution 76).

Study 1: convolution of the individual post-WCG CCDFs (`method_1`).
Study 2: convolution of the per-geometry CCDFs (`method_2`).

Conventions:
    `cdf` here = CCDF (Complementary CDF). The accumulator
    (`EPFDStreamAccumulator.build_ccdf`) uses the **inclusive** convention:
    pct[i] = P[X >= bin_i] — the last ascending point carries the mass of the
    peak bin. All PMF/CCDF conversions in this module follow that convention.

Aggregation of a sum of independent random variables:
    If Z = X + Y (linear power), the PMF of Z is the convolution of the PMFs
    of X and Y. EPFD CCDFs live on dB grids, so `convolve_ccdfs_db` performs
    the fold **directly in the dB domain**: for every pair of bins
    (a_dB, p_a) × (b_dB, p_b) the aggregate level is
    z = 10·log10(10^(a/10) + 10^(b/10)) with probability p_a·p_b, accumulated
    on a fine fixed-step dB grid. This keeps full resolution far below the
    peak (a linear-power grid would collapse everything more than
    ~10·log10(n_bins) dB below the maximum into the first bins).
"""

from __future__ import annotations
import math
from typing import Iterable, Sequence
import numpy as np


# Step of the common dB grid used by the dB-domain fold. Matches
# ``EPFDStreamAccumulator._BIN_SIZE_DB`` (0.1 dB) so the per-system CCDF bins
# map exactly onto the aggregate grid.
_DB_GRID_STEP = 0.1

# Rows per outer-product chunk in the pairwise fold (memory bound:
# _FOLD_CHUNK × n_input_bins float64 temporaries).
_FOLD_CHUNK = 512


def _validate_ccdf(epfd_bins: np.ndarray, ccdf: np.ndarray, name: str) -> None:
    if epfd_bins.shape != ccdf.shape:
        raise ValueError(
            f"{name}: shapes differ ({epfd_bins.shape} vs {ccdf.shape})"
        )
    if epfd_bins.ndim != 1:
        raise ValueError(f"{name}: expected 1-D, got {epfd_bins.ndim}-D")
    if epfd_bins.size < 2:
        raise ValueError(f"{name}: needs at least 2 bins")
    diffs = np.diff(epfd_bins)
    if not np.allclose(diffs, diffs[0], rtol=1e-6, atol=1e-9):
        raise ValueError(f"{name}: epfd_bins is not uniform (steps: {diffs})")
    if np.any(ccdf < -1e-9) or np.any(ccdf > 1.0 + 1e-9):
        raise ValueError(f"{name}: ccdf out of [0, 1] (range [{ccdf.min()}, {ccdf.max()}])")
    if np.any(np.diff(ccdf) > 1e-9):
        raise ValueError(f"{name}: ccdf must be monotonically non-increasing")


def _ccdf_to_pmf(ccdf: np.ndarray) -> np.ndarray:
    """Inclusive CCDF P[X >= x_i] → PMF P[X = x_i] on the same bins.

    Returns an array of the same size where:
        pmf[i]  = ccdf[i] - ccdf[i+1]   (i < n-1)
        pmf[-1] = ccdf[-1]              (mass of the peak bin — kept)
    sum(pmf) = ccdf[0]. Any mass below the first bin (1 - ccdf[0], e.g. time
    with no signal) is NOT included; callers must carry it explicitly.
    """
    pmf = np.empty_like(ccdf)
    pmf[:-1] = ccdf[:-1] - ccdf[1:]
    pmf[-1] = ccdf[-1]
    return np.clip(pmf, 0.0, 1.0)


def _pmf_to_ccdf(pmf: np.ndarray) -> np.ndarray:
    """PMF → inclusive CCDF P[X >= x_i] = sum_{j >= i} pmf[j]."""
    ccdf = np.cumsum(pmf[::-1])[::-1]
    return np.clip(ccdf, 0.0, 1.0)


def convolve_ccdf(
    epfd_bins_a: Sequence[float],
    ccdf_a: Sequence[float],
    epfd_bins_b: Sequence[float],
    ccdf_b: Sequence[float],
) -> tuple[np.ndarray, np.ndarray]:
    """Convolve two CCDFs on uniform grids (same step `Δ`).

    Returns `(epfd_bins, ccdf)` in linear power scale (inclusive convention).

    Important: bins must be in **linear scale** (W/m²/Hz) — the sum of powers
    only maps to an index shift on a linear grid. For CCDFs on dB grids use
    `convolve_ccdfs_db`, which folds directly in the dB domain.
    """
    a_bins = np.asarray(epfd_bins_a, dtype=float)
    b_bins = np.asarray(epfd_bins_b, dtype=float)
    a_ccdf = np.asarray(ccdf_a, dtype=float)
    b_ccdf = np.asarray(ccdf_b, dtype=float)

    _validate_ccdf(a_bins, a_ccdf, "ccdf_a")
    _validate_ccdf(b_bins, b_ccdf, "ccdf_b")

    da = a_bins[1] - a_bins[0]
    db = b_bins[1] - b_bins[0]
    if not np.isclose(da, db, rtol=1e-6, atol=1e-12):
        raise ValueError(
            f"different steps in the two CCDFs: {da} vs {db}; "
            "re-sample to a common Δ first."
        )
    step = da

    pmf_a = _ccdf_to_pmf(a_ccdf)
    pmf_b = _ccdf_to_pmf(b_ccdf)
    # Mass below the first bin (1 - ccdf[0]) is lumped at the first bin to
    # conserve total probability (approximation: on the linear EPFD grids
    # used here the first bin is ~0 W, i.e. "no signal").
    pmf_a[0] += max(0.0, 1.0 - a_ccdf[0])
    pmf_b[0] += max(0.0, 1.0 - b_ccdf[0])

    pmf_c = np.convolve(pmf_a, pmf_b, mode="full")

    out_bins = a_bins[0] + b_bins[0] + step * np.arange(pmf_c.size)
    out_ccdf = _pmf_to_ccdf(pmf_c)
    return out_bins, out_ccdf


def convolve_ccdfs(
    ccdfs: Iterable[tuple[Sequence[float], Sequence[float]]],
) -> tuple[np.ndarray, np.ndarray]:
    """Chained convolution of N CCDFs (multi-system Study 1).

    Each item: `(epfd_bins, ccdf)`. Returns the final `(bins, ccdf)`.
    """
    iterator = iter(ccdfs)
    try:
        first_bins, first_ccdf = next(iterator)
    except StopIteration:
        raise ValueError("convolve_ccdfs: no CCDF provided")
    out_bins = np.asarray(first_bins, dtype=float)
    out_ccdf = np.asarray(first_ccdf, dtype=float)
    for bins, ccdf in iterator:
        out_bins, out_ccdf = convolve_ccdf(out_bins, out_ccdf, bins, ccdf)
    return out_bins, out_ccdf


def _resample_linear_uniform(
    bins_linear: np.ndarray,
    ccdf: np.ndarray,
    n_points: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Re-sample CCDF to a uniform linear grid with `n_points` bins.

    `bins_linear` and `ccdf` must be monotonic (increasing bins, decreasing
    ccdf). `n_points` ≥ 2.
    """
    if bins_linear.size < 2 or n_points < 2:
        raise ValueError("re-sampling requires >= 2 points")
    x_min = float(bins_linear[0])
    x_max = float(bins_linear[-1])
    new_bins = np.linspace(x_min, x_max, n_points)
    new_ccdf = np.interp(new_bins, bins_linear, ccdf)
    new_ccdf = np.minimum.accumulate(new_ccdf[::-1])[::-1]
    return new_bins, np.clip(new_ccdf, 0.0, 1.0)


def _ccdf_db_desc_to_pmf(
    bins_db: Sequence[float],
    pct: Sequence[float],
    name: str,
) -> tuple[np.ndarray, np.ndarray, float]:
    """`build_ccdf()` output (bins dB desc, % desc) → `(bins_db_asc, pmf, p0)`.

    Inclusive convention: pct[i] = P[X >= bin_i] in % of time. `p0` is the
    point mass at zero linear power ("no signal" time the histogram never
    sees): p0 = 1 - max(pct)/100.
    """
    bins_arr = np.asarray(bins_db, dtype=float)
    pct_arr = np.asarray(pct, dtype=float)
    if bins_arr.size != pct_arr.size or bins_arr.size < 2:
        raise ValueError(
            f"{name}: invalid CCDF (size={bins_arr.size}); needs >= 2 points"
        )
    order_asc = np.argsort(bins_arr)
    bins_asc = bins_arr[order_asc]
    cc = np.clip(pct_arr[order_asc] / 100.0, 0.0, 1.0)
    # Enforce non-increasing CCDF along ascending bins.
    cc = np.minimum.accumulate(cc)
    pmf = _ccdf_to_pmf(cc)
    p0 = max(0.0, 1.0 - float(cc[0]))
    return bins_asc, pmf, p0


def _fold_pmf_db(
    agg_pmf: np.ndarray,
    agg_p0: float,
    grid_db: np.ndarray,
    bins_b: np.ndarray,
    pmf_b: np.ndarray,
    p0_b: float,
) -> tuple[np.ndarray, float]:
    """One fold step: aggregate ⊕ system, pairwise power sum in dB.

    `agg_pmf` lives on `grid_db` (uniform step); `(bins_b, pmf_b, p0_b)` is the
    incoming system PMF on its own dB bins. The zero-power masses are carried
    explicitly (zero + X = X):
        p_a·p_b  → bin of 10·log10(10^(a/10) + 10^(b/10))
        p0_b·agg → stays on the aggregate bins
        p0_a·b   → stays on the system bins
        p0_a·p0_b → new zero-power mass
    """
    grid0 = float(grid_db[0])
    step = float(grid_db[1] - grid_db[0])
    n_bins = grid_db.size

    def to_idx(values_db: np.ndarray) -> np.ndarray:
        idx = np.rint((values_db - grid0) / step).astype(np.int64)
        return np.clip(idx, 0, n_bins - 1)

    new_pmf = np.zeros(n_bins, dtype=np.float64)

    nz_g = np.nonzero(agg_pmf > 0.0)[0]
    nz_b = np.nonzero(pmf_b > 0.0)[0]
    g_db, pg = grid_db[nz_g], agg_pmf[nz_g]
    b_db, pb = bins_b[nz_b], pmf_b[nz_b]

    # signal × signal — chunked outer products keep memory bounded.
    if g_db.size and b_db.size:
        b_lin = np.power(10.0, b_db / 10.0)
        for i0 in range(0, g_db.size, _FOLD_CHUNK):
            g_lin = np.power(10.0, g_db[i0:i0 + _FOLD_CHUNK] / 10.0)
            z_db = 10.0 * np.log10(g_lin[:, None] + b_lin[None, :])
            prob = pg[i0:i0 + _FOLD_CHUNK, None] * pb[None, :]
            np.add.at(new_pmf, to_idx(z_db).ravel(), prob.ravel())

    # zero-power cross terms: zero + X = X.
    if p0_b > 0.0 and nz_g.size:
        new_pmf[nz_g] += p0_b * pg
    if agg_p0 > 0.0 and b_db.size:
        np.add.at(new_pmf, to_idx(b_db), agg_p0 * pb)

    return new_pmf, agg_p0 * p0_b


def convolve_ccdfs_db(
    ccdfs_db_desc: Iterable[tuple[Sequence[float], Sequence[float]]],
    *,
    resample_points: int = 4096,
    output_n_points: int = 4096,
    eps_linear: float = 1e-30,
    truncate_tail_pct: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Convolve N CCDFs coming from the S.1503 accumulator (bins in dB, % of time).

    Each item: `(bins_dB_desc, percentage_desc)`. Format of
    `EPFDStreamAccumulator.build_ccdf()` — descending bins in dBW/m²/BW_ref,
    `percentage` in % of time (0 to 100), **inclusive** (pct[i] = P[X >= bin_i]).

    Procedure (dB-domain fold — full resolution below the peak):
        1. Convert each CCDF to a PMF on its own dB bins plus an explicit
           point mass `p0` at zero linear power (time with no signal:
           p0 = 1 - max(pct)/100; zero + X = X when aggregating).
        2. Build a common dB grid with fixed step 0.1 dB (aligned with the
           accumulator bins), from the lowest input bin up to the aggregate
           peak 10·log10(Σ_i 10^(max_i/10)).
        3. Fold systems iteratively: every bin pair (a_dB, p_a) × (b_dB, p_b)
           contributes p_a·p_b at z = 10·log10(10^(a/10) + 10^(b/10)).
        4. Aggregate PMF → inclusive CCDF, returned as
           `(bins_dB_desc, percentage_desc)` in the same input format.

    The sum of EPFDs across independent systems is a linear power sum;
    convolution of the PMFs is probabilistically equivalent.

    `resample_points` and `eps_linear` are kept for backward compatibility and
    are no longer used (the fold no longer re-samples on a linear-power grid).
    `output_n_points` caps the number of output points (down-sampling).

    ``truncate_tail_pct`` (S.1588 Annex 1, §1): the convolution drives the
    high-power tail to extremely low probabilities the underlying simulations
    cannot support. When set (> 0), the aggregate curve is truncated at that
    "shortest percentage of the time" — every output point exceeded for less
    than ``truncate_tail_pct`` % of the time is dropped (the curve no longer
    runs all the way down to the unreliable ~0 % point). ``None`` keeps the
    full convolved tail.
    """
    ccdfs_list = list(ccdfs_db_desc)
    if not ccdfs_list:
        raise ValueError("convolve_ccdfs_db: no CCDF provided")

    systems = [
        _ccdf_db_desc_to_pmf(bins_db, pct, f"ccdf[{k}]")
        for k, (bins_db, pct) in enumerate(ccdfs_list)
    ]

    # Common dB grid: from the lowest input bin up to the aggregate peak
    # (linear sum of the per-system maxima), fixed fine step.
    step = _DB_GRID_STEP
    grid0 = min(float(bins[0]) for bins, _, _ in systems)
    agg_max_db = 10.0 * math.log10(
        sum(10.0 ** (float(bins[-1]) / 10.0) for bins, _, _ in systems)
    )
    n_bins = int(math.ceil((agg_max_db - grid0) / step + 1e-9)) + 2
    grid_db = grid0 + step * np.arange(n_bins, dtype=np.float64)

    bins0, pmf0, p0 = systems[0]
    agg_pmf = np.zeros(n_bins, dtype=np.float64)
    idx0 = np.clip(
        np.rint((bins0 - grid0) / step).astype(np.int64), 0, n_bins - 1
    )
    np.add.at(agg_pmf, idx0, pmf0)
    agg_p0 = p0

    for bins_b, pmf_b, p0_b in systems[1:]:
        agg_pmf, agg_p0 = _fold_pmf_db(
            agg_pmf, agg_p0, grid_db, bins_b, pmf_b, p0_b
        )

    support = np.nonzero(agg_pmf > 0.0)[0]
    if support.size == 0:
        # Degenerate: all the time has zero power in every system.
        return np.array([], dtype=float), np.array([], dtype=float)
    lo, hi = int(support[0]), int(support[-1])

    ccdf = np.cumsum(agg_pmf[::-1])[::-1]  # inclusive: P[X >= grid_i]
    out_bins_db = grid_db[lo:hi + 1][::-1].copy()
    out_pct = np.clip(ccdf[lo:hi + 1][::-1] * 100.0, 0.0, 100.0)

    if output_n_points and out_bins_db.size > output_n_points:
        idx = np.linspace(0, out_bins_db.size - 1, output_n_points).astype(int)
        out_bins_db = out_bins_db[idx]
        out_pct = out_pct[idx]

    # S.1588: optionally truncate the unreliable low-probability (high-power)
    # tail — keep only levels exceeded for >= truncate_tail_pct % of the time.
    if truncate_tail_pct is not None and float(truncate_tail_pct) > 0.0:
        keep = out_pct >= float(truncate_tail_pct) - 1e-12
        if keep.any():
            out_bins_db = out_bins_db[keep]
            out_pct = out_pct[keep]

    return out_bins_db, out_pct
