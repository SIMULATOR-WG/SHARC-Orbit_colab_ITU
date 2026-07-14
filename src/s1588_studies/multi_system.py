"""multi_system.py — Joint simulation (Study 3 / Method 2B of Resolution 76).

Initial implementation: temporal sum of the individual EPFD series.

For each system `i`, run `run_epfd_at_geometry` on the SAME geometry, SAME
`t0`, SAME time step. Result: `EPFD_total(t) = Σ_i 10^(EPFD_i(t)/10)`
(linear power sum). CCDF: distribution of the summed series.

**Limitation:** it does not capture S.1503-4 §D.5.1.4.1 across constellations
(Steps 19–21 OR-of-AND places a co-frequency MAX_CO_FREQ subset simultaneously
in a single constellation; for multi-system, summing all contributors of each
system is a simple approximation). Later iterations may refine it.

Temporal coherence preserved: same grid t = [0, Δt, 2Δt, ..., (N-1)Δt] in all
constellations; identical simulation window.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

import numpy as np

from .geometry import GeometryPoint


@dataclass
class JointSystemSpec:
    """Descriptor of an NGSO system for the joint simulation."""

    label: str
    runner_kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass
class JointSimulationResult:
    """Output of the multi-system joint simulation."""

    geometry: GeometryPoint
    num_time_steps: int
    time_step_s: float
    time_s: np.ndarray
    epfd_total_db: np.ndarray
    epfd_per_system_db: list[np.ndarray]
    ccdf_bins_db: list[float]
    ccdf_pct: list[float]


def _series_to_ccdf(
    epfd_db: np.ndarray,
    *,
    bin_size_db: float = 0.1,
    eps_db: float = -350.0,
) -> tuple[list[float], list[float]]:
    """Build CCDF P[X >= x] from a series in dB.

    Inclusive convention, aligned with ``EPFDStreamAccumulator.build_ccdf``:
    each bin's percentage includes the mass of the bin itself (the last
    ascending point carries the peak-bin mass > 0).
    """
    arr = np.asarray(epfd_db, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return [], []
    arr = np.maximum(arr, eps_db)
    # Integer-index edges: float accumulation in np.arange(lo, hi + bin, bin)
    # can place the last edge just below the maximum sample, silently dropping
    # the peak-bin mass from the histogram.
    lo_idx = int(np.floor(arr.min() / bin_size_db))
    hi_idx = int(np.floor(arr.max() / bin_size_db)) + 1
    bins = np.arange(lo_idx, hi_idx + 1, dtype=float) * bin_size_db
    counts, _ = np.histogram(arr, bins=bins)
    bin_centers = (bins[:-1] + bins[1:]) / 2.0
    total = counts.sum()
    if total == 0:
        return [], []
    ccdf_pct = np.cumsum(counts[::-1])[::-1] / total * 100.0
    bins_desc = bin_centers[::-1]
    pct_desc = ccdf_pct[::-1]
    return list(bins_desc), list(pct_desc)


def _epfd_db_series_to_linear(epfd_db: Sequence[float]) -> np.ndarray:
    return np.power(10.0, np.asarray(epfd_db, dtype=float) / 10.0)


def _extract_time_series(result: Any, label: str) -> tuple[np.ndarray, np.ndarray]:
    """EPFD time series ``(t_s, epfd_db)`` from a system-runner result.

    Accepts either:
      - an object with a non-empty ``timeline`` — list of points with
        ``.t`` (s) / ``.value`` (dB) attributes; or
      - an ``EPFDSimulationResult`` with non-empty ``time_steps`` — list of
        ``EPFDTimeStepResult`` (``time_s`` / ``epfd_aggregate_dBW``). Requires
        the simulation to be run with ``keep_full_history=True``.

    Raises ``ValueError`` when no usable series is present (it would otherwise
    silently become a -999 dB flat line in the joint sum).
    """
    timeline = getattr(result, "timeline", None)
    if timeline:
        ts = np.array([float(tp.t) for tp in timeline], dtype=float)
        vs = np.array([float(tp.value) for tp in timeline], dtype=float)
        return ts, vs
    time_steps = getattr(result, "time_steps", None)
    if time_steps:
        ts = np.array([float(s.time_s) for s in time_steps], dtype=float)
        vs = np.array([float(s.epfd_aggregate_dBW) for s in time_steps], dtype=float)
        return ts, vs
    raise ValueError(
        f"system {label!r}: runner result has no usable time series — expected "
        "a non-empty `timeline` (.t/.value points) or `EPFDSimulationResult."
        "time_steps` (run `run_epfd_at_geometry` with keep_full_history=True)."
    )


def run_joint_epfd_simulation(
    *,
    geometry: GeometryPoint,
    system_runner: Callable[[GeometryPoint, JointSystemSpec], "Any"],
    systems: Iterable[JointSystemSpec],
    num_time_steps: int,
    time_step_s: float,
) -> JointSimulationResult:
    """Simulate N systems on the same geometry, sum the series in linear scale.

    `system_runner(geometry, spec)` must return either an object with a
    non-empty `timeline` attribute (list of points with `.t`/`.value` in dB)
    or an `EPFDSimulationResult` with `time_steps` populated (i.e. run with
    `keep_full_history=True` — e.g. an adapter delegating to
    `run_epfd_at_geometry`), with `time_step_s`/`num_time_steps` consistent
    with the call. A result without a usable series raises `ValueError`.

    Returns `JointSimulationResult` with the summed series and reconstructed CCDF.
    """
    systems_list = list(systems)
    if not systems_list:
        raise ValueError("run_joint_epfd_simulation: no system provided")
    if num_time_steps < 1:
        raise ValueError(f"invalid num_time_steps: {num_time_steps}")
    if time_step_s <= 0:
        raise ValueError(f"invalid time_step_s: {time_step_s}")

    per_system_series_db: list[np.ndarray] = []
    grid_t = np.arange(num_time_steps, dtype=float) * float(time_step_s)
    total_linear = np.full(num_time_steps, 1e-30, dtype=float)

    for spec in systems_list:
        result = system_runner(geometry, spec)
        ts, vs = _extract_time_series(result, spec.label)
        if ts.size != num_time_steps or not np.allclose(
            ts, grid_t, rtol=0.0, atol=time_step_s * 0.51
        ):
            interp_db = np.interp(grid_t, ts, vs, left=-999.0, right=-999.0)
        else:
            interp_db = vs
        per_system_series_db.append(interp_db)
        total_linear += np.power(10.0, interp_db / 10.0)

    total_db = 10.0 * np.log10(np.maximum(total_linear, 1e-30))
    bins, pct = _series_to_ccdf(total_db)

    return JointSimulationResult(
        geometry=geometry,
        num_time_steps=int(num_time_steps),
        time_step_s=float(time_step_s),
        time_s=grid_t,
        epfd_total_db=total_db,
        epfd_per_system_db=per_system_series_db,
        ccdf_bins_db=bins,
        ccdf_pct=pct,
    )
