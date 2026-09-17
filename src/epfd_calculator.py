"""
epfd_calculator.py — EPFD↓ computation and statistical analysis.

Implements the main time simulation loop per
ITU-R S.1503-4, Part D, Section D.5:

  For each time step:
    1. Propagate the NGSO constellation
    2. Select NGSO satellites visible from the ES by the horizon/line-of-sight
       criterion of §D6.4.3.
    3. For each visible NGSO satellite:
       a. Compute alpha angle
       b. Check the Step 18 store criterion:
          (``α ≥ α₀`` and ``ε ≥ ε₀``) or the gain OR condition
       c. Obtain PFD from the mask
       d. Compute the off-axis angle at the ES
       e. Compute the relative gain of the ES
       f. Compute EPFD↓ = PFD + Gr,rel (dB)
    4. Aggregate EPFD↓ (§D5.1.4.1 Steps 18–23): ``MAX_CO_FREQ`` limits only the
       iterative loop Steps 19–21 among *standard* contributors
       (``α ≥ α₀``, ``ε ≥ ε₀``); Step 22 adds contributors solely by the
       gain criterion (OR), without that joint cap (anti-duplication note
       in the text of the recommendation).
    5. Store in the results vector
  At the end: generate the CDF and compare against Article 22 limits

  The optional ``strict_max_co_freq_total`` mode applies a single cap over
  standard+OR and does **not** reproduce the Steps 19–22 separation of ITU-R S.1503-4.
"""

from __future__ import annotations
import math
import time
import logging
import numpy as np
from dataclasses import dataclass, field
from typing import Callable

from .constants import RE_KM
from .coordinates import (
    ecef_to_lla, ecef_to_lla_batch, lla_to_ecef, gso_position_ecef,
    get_earth_rotation_initial_deg, set_earth_rotation_initial_deg,
)
from .orbit_propagator import (
    OrbitalElements,
    propagate_and_to_ecef_batch, build_constellation_cache,
)
from .geometry import (
    compute_alpha_angle_fast_components,
    compute_alpha_and_optimal_gso_fixed_es_batch,
    compute_offaxis_angle,
    compute_offaxis_and_planar_angle,
    compute_offaxis_and_planar_angle_batch,
    compute_offaxis_angle_batch,
    compute_elevation,
    alpha_batch_uses_numba_parallel,
    set_numba_num_threads,
    delta_longitude_s1503_deg,
    get_gso_longitude_mode,
    set_gso_longitude_mode,
    get_alpha_method,
    set_alpha_method,
)
from .pfd_mask import PFDMask
from .antenna import EarthStationAntenna, s1503_or_condition_include
from .time_step import DualTimeStep, TrackDurationWindows
from .wcg_search import (
    WCGResult, _compute_pfd_3d, _relative_gain_batch,
    _compute_mask_az_el_per_sat_frame_batch,
)
from .epfd_stream_accumulator import EPFDStreamAccumulator, EPFDWindowStats

logger = logging.getLogger(__name__)

# Tolerance of the AP4 minimum-operating-height gate, in km (1 m). A circular
# filing declares H_min equal to its own altitude, so the gate lands on the
# equality case and must not turn on float noise.
_H_MIN_TOL_KM = 1e-3


def _epfd_gso_min_elevation_active(gso_min_elevation_deg: float) -> bool:
    """True if the S.1503 check ``elGSO >= εGSO`` (Table 8) is active.

    ``-90°`` disables the filter, aligned with ``gso_min_elev_effective_deg`` in ``main.py``.
    """
    return float(gso_min_elevation_deg) > -89.0


def _es_ecef_from_wcg(wcg: WCGResult) -> np.ndarray:
    """ECEF position of the ES: ``es_ecef_exact`` from the WCG when valid, otherwise LLA→ECEF."""
    es_ecef_exact = getattr(wcg, "es_ecef_exact", None)
    if (
        isinstance(es_ecef_exact, np.ndarray)
        and es_ecef_exact.size == 3
        and np.linalg.norm(es_ecef_exact) > RE_KM * 0.9
    ):
        return es_ecef_exact.astype(np.float64, copy=False)
    return np.asarray(lla_to_ecef(wcg.es_lat_deg, wcg.es_lon_deg, 0.0), dtype=np.float64)


def _apply_epfd_globals(init: dict) -> None:
    """Re-apply the engine's global state in a worker process.

    On ``fork`` (Linux/WSL2) workers already inherit the parent's globals, so
    this is a harmless re-assignment of the same values. On ``spawn`` (native
    Windows / ``set_start_method('spawn')``) workers re-import the modules with
    their defaults, so re-applying here is *required* for correctness — without
    it the chunk workers would silently use ``alpha_method='sweep'``,
    ``gso_mode='arc_optimal'`` and Earth-rotation 0.0 regardless of the run's
    configuration. Used by both the built-in Pool and injected cluster executors.
    """
    set_numba_num_threads(int(init.get("numba_threads", 1) or 1))
    set_earth_rotation_initial_deg(float(init.get("gmst0_deg", 0.0) or 0.0))
    gso = init.get("gso_mode")
    if gso:
        set_gso_longitude_mode(gso)
    alpha = init.get("alpha_method")
    if alpha:
        set_alpha_method(alpha)


def _epfd_pool_initializer(init: dict) -> None:
    """Pool worker initializer — re-applies engine globals (see
    :func:`_apply_epfd_globals`)."""
    _apply_epfd_globals(init)


# Optional injected executor for the time-chunk sweep. When set, it replaces
# the built-in multiprocessing.Pool so a single heavy simulation can be
# fanned out across a cluster. The engine never imports the executor — pure
# dependency injection (see ``set_epfd_executor``). Mirrors the WCGA hook in
# ``wcg_search``.
_EPFD_EXECUTOR = None


def set_epfd_executor(executor) -> None:
    """Inject an alternative executor for the EPFD time-chunk sweep.

    ``executor(worker_fn, chunks, init)`` must run ``worker_fn(chunk)`` for
    every chunk and return the list of result dicts (order irrelevant — the
    accumulator merge is commutative). ``init`` carries the engine global
    state each remote worker must re-apply before computing (the local Pool
    inherits it via fork; fresh cluster workers do not): keys ``gmst0_deg``,
    ``gso_mode``, ``alpha_method``, ``numba_threads``.

    Pass ``None`` to restore the built-in ``multiprocessing.Pool`` path.
    """
    global _EPFD_EXECUTOR
    _EPFD_EXECUTOR = executor


def get_epfd_executor():
    return _EPFD_EXECUTOR


def _epfd_global_snapshot(numba_threads: int) -> dict:
    """Snapshot of the engine global state a worker must re-apply, captured in
    the parent at dispatch time. ``numba_threads`` is parameterised so the
    built-in Pool keeps its computed per-worker thread budget while cluster
    workers pin it to 1."""
    return {
        "numba_threads": int(numba_threads),
        "gmst0_deg": get_earth_rotation_initial_deg(),
        "gso_mode": get_gso_longitude_mode(),
        "alpha_method": get_alpha_method(),
    }


def _epfd_executor_init() -> dict:
    """Snapshot for injected cluster executors: Numba pinned to 1 thread/worker
    (the executor controls task concurrency)."""
    return _epfd_global_snapshot(1)


def _compute_epfd_numba_threads(n_jobs: int) -> int:
    """Computes Numba threads per worker to avoid oversubscription."""
    import os
    total_cores = os.cpu_count() or 1
    if not alpha_batch_uses_numba_parallel() or n_jobs <= 1:
        return total_cores
    return max(1, total_cores // n_jobs)


def _pfd_mask_uses_batch(pfd_mask: PFDMask) -> bool:
    """Whether the mask supports the vectorized (batch) accumulation path.

    Both 3D mask flavours — alpha/Δλ and azimuth/elevation — are vectorized in
    the EPFD↓ accumulator: only the mask-query axes differ (Δλ uses the
    optimal-GSO longitude; Az/El uses each satellite's local frame). 1D masks
    stay on the scalar path.
    """
    return getattr(pfd_mask, "_dim", 1) == 3


def _pfd_mask_uses_alpha_delta_batch(pfd_mask: PFDMask) -> bool:
    """3D **alpha/Δλ-only** batch predicate.

    Narrower than :func:`_pfd_mask_uses_batch`: excludes azimuth/elevation. Kept
    for callers whose batch kernels only implement the alpha/Δλ axes (e.g. the
    S.1588 Tier-2 vectorized kernel, which raises ``NotImplementedError`` for
    Az/El masks).
    """
    return (
        getattr(pfd_mask, "_dim", 1) == 3
        and getattr(pfd_mask, "mask_type", "") != "azimuth_elevation"
    )


def _min_operating_height_km_batch(
    constellation: list[OrbitalElements], n_sat: int,
) -> np.ndarray | None:
    """Vector (n_sat,) with H_min per satellite; ``None`` if all entries are zero."""
    if n_sat <= 0:
        return None
    vals = np.zeros(n_sat, dtype=np.float64)
    for k in range(n_sat):
        if k < len(constellation):
            vals[k] = max(
                0.0,
                float(getattr(constellation[k], "min_operating_height_km", 0.0) or 0.0),
            )
    if not np.any(vals > 0.0):
        return None
    return vals


def _separation_angle_deg_at_es(
    es_ecef: np.ndarray,
    pos_a: np.ndarray,
    pos_b: np.ndarray,
) -> float:
    """Angle at the ES point between the lines-of-sight to two satellites (degrees)."""
    es = np.asarray(es_ecef, dtype=np.float64).ravel()[:3]
    pa = np.asarray(pos_a, dtype=np.float64).ravel()[:3]
    pb = np.asarray(pos_b, dtype=np.float64).ravel()[:3]
    va = pa - es
    vb = pb - es
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na < 1e-9 or nb < 1e-9:
        return 180.0
    c = float(np.dot(va, vb) / (na * nb))
    c = max(-1.0, min(1.0, c))
    return math.degrees(math.acos(c))


def _select_standard_epfd_s1503_steps_20_21(
    items: list[tuple[float, int]],
    max_co_freq: int,
    min_angle_at_es_deg: float,
    es_ecef: np.ndarray,
    pos_ecef_all: np.ndarray,
) -> list[tuple[float, int]]:
    """S.1503-4 §D.5.1.4.1 Steps 19–21 among *standard* contributors (|α| ≥ α₀).

    Returns the selected ``(epfd_linear, satellite index)`` pairs. The indices
    are what the Step-20 Note needs: a satellite already on this list must not
    be counted a second time through the gain branch.

    Step 20: pick the standard satellite with the highest ``epfd↓``; Step 23 (sum)
    is applied by the caller over the returned list. Step 21: remove
    candidates that do not meet ``MIN_ANGLE_AT_ES`` relative to the chosen one;
    Step 19: repeat while the number of selected standard contributors
    is below ``MAX_CO_FREQ``.

    With ``min_angle_at_es_deg <= 0``, Step 21 is inert; the loop coincides
    with successively removing the global maximum from the pool — equivalent to the
    ``max_co_freq`` highest ``epfd↓`` values (descending order).

    ``max_co_freq <= 0`` means *no count cap* (Step 19 never stops the loop),
    but the Step 21 angular pruning still applies when
    ``min_angle_at_es_deg > 0``.
    """
    if not items:
        return []
    if min_angle_at_es_deg <= 0.0:
        if max_co_freq <= 0:
            return [(float(epfd), int(k)) for epfd, k in items]
        pool = [(float(epfd), int(k)) for epfd, k in items]
        out: list[tuple[float, int]] = []
        while len(out) < max_co_freq and pool:
            j = max(range(len(pool)), key=lambda i: pool[i][0])
            out.append(pool.pop(j))
        return out
    pool: list[tuple[float, int]] = [(float(epfd), int(k)) for epfd, k in items]
    selected: list[tuple[float, int]] = []
    min_ang = float(min_angle_at_es_deg)
    while pool and (max_co_freq <= 0 or len(selected) < max_co_freq):
        best_j = max(range(len(pool)), key=lambda j: pool[j][0])
        chosen_epfd, chosen_k = pool.pop(best_j)
        selected.append((chosen_epfd, chosen_k))
        pos_chosen = pos_ecef_all[chosen_k]
        new_pool: list[tuple[float, int]] = []
        for epfd, k in pool:
            sep = _separation_angle_deg_at_es(es_ecef, pos_ecef_all[k], pos_chosen)
            if sep + 1e-12 >= min_ang:
                new_pool.append((epfd, k))
        pool = new_pool
    return selected


def _or_from_capped(
    std_or_items: list[tuple[float, int]] | None,
    chosen_idx: set[int],
) -> list[float]:
    """Step-22 contributions of α₀/ε₀ satellites that Step 20 did not keep.

    ``std_or_items`` are the ``(epfd_linear, index)`` pairs of satellites that
    passed the α₀/ε₀ test AND satisfy the gain condition. Those the cap kept are
    dropped here, because the Note counts them once, as part of the
    MAX_CO_FREQ list.
    """
    if not std_or_items:
        return []
    return [float(v) for v, k in std_or_items if int(k) not in chosen_idx]


def _finalize_epfd_after_max_co_freq(
    standard_items: list[tuple[float, int]],
    override_epfd: list[float],
    max_co_freq: int,
    strict_max_co_freq_total: bool,
    min_angle_at_es_deg: float,
    es_ecef: np.ndarray,
    pos_ecef_all: np.ndarray,
    *,
    system_id_all: np.ndarray | None = None,
    max_co_freq_by_system: dict[int, int] | None = None,
    override_items: list[tuple[float, int]] | None = None,
    per_system_out: dict[int, list] | None = None,
    std_or_items: list[tuple[float, int]] | None = None,
    min_angle_at_es_by_system: dict[int, float] | None = None,
) -> tuple[list[float], list[float]]:
    """Steps 19–22 §D5.1.4.1: standard selection (20–21), then OR branch (22).

    With ``strict_max_co_freq_total=False`` (the **normative** default
    behavior in the project), ``MAX_CO_FREQ`` applies only to the standard
    ones; the Step 22 (OR) values are all kept — consistent with the note about not
    duplicating satellites already counted in the Step 20 set.

    With ``strict_max_co_freq_total=True``, a joint cap is applied over
    standard+OR by highest ``epfd_i`` (an **extension** not described in S.1503).

    **Multi-system aggregation (Resolution 76, joint/method_3):** when
    ``system_id_all`` (system_id per sat) and ``max_co_freq_by_system``
    (system_id → MAX_CO_FREQ already resolved at the ES latitude) are provided,
    the Steps 20–21 selection is **partitioned by system** — each constellation
    applies its own ``MAX_CO_FREQ`` (intra-system parameter), then sums.
    MAX_CO_FREQ does not couple systems: per-system selection commutes with the
    linear sum. Without partitioning, it would collapse all systems into a single N
    (anti-conservative with heterogeneous tables). ``strict_max_co_freq_total`` is
    ignored in this mode (a joint cap makes no sense across systems).

    ``per_system_out`` (method_3 single-pass decomposition): when a dict is
    passed together with ``system_id_all``, it is filled with
    ``system_id → [linear EPFD, n_contributors]`` over that system's SELECTED
    satellites for this step. Since Step 23 is a linear sum and each satellite
    belongs to exactly one system, these values sum back to the joint step
    total — which is what lets the per-system CCDFs be accumulated in the SAME
    pass instead of re-simulating each system separately.
    ``override_items`` carries the Step 22 (OR) values with their satellite
    indices so the OR branch can be attributed too (``override_epfd`` alone is
    index-less). Caveat: with the non-normative ``strict_max_co_freq_total=True``
    AND a step having zero standard contributors, the OR values can be trimmed
    downstream while ``per_system_out`` already counted them all — the per-system
    split may then slightly over-count for such steps. The joint result is
    unaffected either way.
    """
    if per_system_out is not None and system_id_all is not None and override_items:
        # Step 22 (OR) values are kept in full in the normative path, so they
        # can be attributed straight from their satellite indices.
        for _v, _k in override_items:
            _sid = int(system_id_all[_k])
            _slot = per_system_out.setdefault(_sid, [0.0, 0])
            _slot[0] += float(_v)
            _slot[1] += 1

    if system_id_all is not None and max_co_freq_by_system is not None and standard_items:
        # Partition standard_items by system_id and apply each system's N.
        by_sys: dict[int, list[tuple[float, int]]] = {}
        for epfd, k in standard_items:
            sid = int(system_id_all[k])
            by_sys.setdefault(sid, []).append((float(epfd), int(k)))
        standard_epfd = []
        chosen_idx: set[int] = set()
        for sid, group in by_sys.items():
            n_sys = int(max_co_freq_by_system.get(sid, max_co_freq))
            # Step 21's minimum separation angle at the ES is an INTRA-system
            # parameter (it stops one constellation's own satellites being
            # counted twice from nearly the same direction), so each system
            # applies its own — never filing 0's.
            ang_sys = float(
                (min_angle_at_es_by_system or {}).get(sid, min_angle_at_es_deg)
            )
            sel_pairs = _select_standard_epfd_s1503_steps_20_21(
                group, n_sys, ang_sys, es_ecef, pos_ecef_all,
            )
            selected = [v for v, _k in sel_pairs]
            chosen_idx.update(int(k) for _v, k in sel_pairs)
            standard_epfd.extend(selected)
            if per_system_out is not None:
                # The group IS one system, so every selected value belongs to
                # `sid` — no index round-trip needed.
                slot = per_system_out.setdefault(sid, [0.0, 0])
                slot[0] += float(sum(selected))
                slot[1] += len(selected)
        # OR (Step 22) is kept intact (normative). strict_total ignored.
        ov = list(override_epfd) + _or_from_capped(std_or_items, chosen_idx)
        return standard_epfd, ov

    sel_pairs = _select_standard_epfd_s1503_steps_20_21(
        standard_items,
        max_co_freq,
        min_angle_at_es_deg,
        es_ecef,
        pos_ecef_all,
    )
    standard_epfd = [v for v, _k in sel_pairs]
    # Step 22 + Note (printed p. 99 / p. 100): the gain branch takes every
    # satellite meeting GRX(φ) > min[Gmax − 30 dB, GRX(α₀)], and the Note
    # removes from it only those already on the MAX_CO_FREQ list. A satellite
    # that met α₀/ε₀ but lost the Step-20 ranking is NOT on that list, so it
    # contributes here.
    ov = list(override_epfd) + _or_from_capped(
        std_or_items, {int(k) for _v, k in sel_pairs})
    if max_co_freq <= 0:
        return standard_epfd, ov
    if strict_max_co_freq_total:
        combined = [(float(v), False) for v in standard_epfd] + [
            (float(v), True) for v in ov
        ]
        if len(combined) <= max_co_freq:
            return standard_epfd, ov
        combined.sort(key=lambda item: item[0], reverse=True)
        selected = combined[:max_co_freq]
        return (
            [value for value, is_ov in selected if not is_ov],
            [value for value, is_ov in selected if is_ov],
        )
    return standard_epfd, ov


def _accumulate_epfd_visible_satellites(
    visible_idx: np.ndarray,
    pos_ecef_all: np.ndarray,
    vel_ecef_all: np.ndarray,
    es_ecef: np.ndarray,
    es_x: float,
    es_y: float,
    es_z: float,
    es_lat_deg: float,
    es_lon_deg: float,
    gso_ecef: np.ndarray,
    alpha0_deg: float,
    pfd_mask: PFDMask,
    es_antenna: EarthStationAntenna,
    pfd_bw_correction_db: float,
    max_co_freq: int,
    strict_max_co_freq_total: bool,
    subsat_lat_all: np.ndarray | None,
    subsat_lon_all: np.ndarray | None,
    sat_local_frames: list | None,
    min_operating_height_km_all: np.ndarray | None,
    dual_ts: DualTimeStep | None,
    t_s: float,
    strict_exclusion_zone: bool = False,
    min_angle_at_es_deg: float = 0.0,
    min_elevation_deg: float = 0.0,
    sin_el_full: np.ndarray | None = None,
    system_id_all: np.ndarray | None = None,
    max_co_freq_by_system: dict[int, int] | None = None,
    min_elevation_deg_all: np.ndarray | None = None,
    alpha0_deg_all: np.ndarray | None = None,
    strict_exclusion_zone_all: np.ndarray | None = None,
    min_angle_at_es_by_system: dict[int, float] | None = None,
    per_sat_out: dict | None = None,
    per_system_out: dict[int, list] | None = None,
) -> tuple[list[float], list[float], float, bool]:
    """Returns (standard_linear, override_linear, min_alpha_deg, any_critical_gain).

    ``per_system_out`` (method_3): pass-through to
    :func:`_finalize_epfd_after_max_co_freq` — filled with this step's linear
    EPFD per ``system_id``, so a joint run can accumulate per-system CCDFs in
    the same pass. Inert unless ``system_id_all`` is also given.

    ``per_sat_out`` (track-duration collect mode, S.1503-4 §D5.1.4.2): when a
    dict is passed, the per-step MAX_CO_FREQ selection (Steps 19–22 of
    §D5.1.4.1) is **skipped** — selection happens per window at the caller —
    and the dict is filled with aligned arrays over the recorded satellites:
    ``idx`` (global sat indices, ascending), ``epfd_lin`` (per-sat epfd↓ᵢ,
    linear), ``std`` (bool, Step 18 bullet ①: |α| ≥ α₀ ∧ ε ≥ ε₀) and ``orx``
    (bool, gain OR condition, evaluated for *every* recorded satellite so the
    window aggregation can honour the no-double-counting note of §D5.1.4.2).
    The returned lists are empty in this mode.

    Visibility convention (S.1503-4 §D.5.1.4.1, Steps 11 and 18):
      - ``visible_idx`` must be the set **above the horizon** (Step 11,
        §D.6.4.3): ``sin_el >= 0``. The minimum elevation filter ``ε₀`` (Step
        18, bullet ①) is applied *internally* here using ``sin_el_full`` and
        ``min_elevation_deg``, so that the OR branch (bullet ②) can
        consider low-elevation satellites that do not satisfy ``ε ≥ ε₀``
        — faithful to the text of the Recommendation.
      - To preserve compatibility, if ``sin_el_full is None`` or
        ``min_elevation_deg == 0``, no additional ``ε₀`` filter is
        applied in this function (it is assumed the caller has already restricted
        ``visible_idx`` to the horizon or to ``ε ≥ ε₀``).

    Final composition:
      - ``is_standard = (|α| ≥ α₀) ∧ (ε ≥ ε₀)``  → counts toward
        ``MAX_CO_FREQ`` (Steps 19–21).
      - ``is_override = ¬is_standard ∧ GRX(φ) > min(Gmax−30, GRX(α₀))``
        → enters Step 22, without double counting.
    """
    override_epfd: list[float] = []

    if per_sat_out is not None:
        per_sat_out["idx"] = np.empty(0, dtype=np.int64)
        per_sat_out["epfd_lin"] = np.empty(0, dtype=np.float64)
        per_sat_out["std"] = np.empty(0, dtype=bool)
        per_sat_out["orx"] = np.empty(0, dtype=bool)

    if visible_idx.size == 0:
        return [], [], 180.0, False

    if min_operating_height_km_all is not None and visible_idx.size > 0:
        h_min_vis = np.asarray(min_operating_height_km_all[visible_idx], dtype=np.float64)
        if np.any(h_min_vis > 0.0):
            alt_vis = np.linalg.norm(pos_ecef_all[visible_idx], axis=1) - RE_KM
            # The AP4 minimum operating height is a declared altitude, and a
            # circular filing declares H_min EQUAL to its own altitude. The
            # comparison then lands on the equality case, where the difference
            # is pure float noise from the propagator and the Earth radius
            # constant: with a 1e-9 km (1 µm) tolerance the whole constellation
            # can drop out and the run reports a clean PASS over an empty CCDF.
            # One metre is below any physical meaning of a declared height and
            # far above that noise.
            visible_idx = visible_idx[alt_vis >= (h_min_vis - _H_MIN_TOL_KM)]
            if visible_idx.size == 0:
                return [], [], 180.0, False

    if min_elevation_deg_all is not None and sin_el_full is not None:
        # Per-system ε₀ (method_3 joint runs): each satellite gates on its OWN
        # system's threshold in Step 18 bullet ① — one shared scalar would let
        # a stricter system's satellites transmit below their declared ε₀.
        _sin_min_el_all = np.sin(np.radians(
            np.asarray(min_elevation_deg_all, dtype=np.float64)
        ))
        _eps0_ok_full = np.asarray(sin_el_full, dtype=np.float64) >= _sin_min_el_all
        eps0_ok_vis = _eps0_ok_full[visible_idx]
    elif sin_el_full is not None and float(min_elevation_deg) > 0.0:
        _sin_min_el_local = math.sin(math.radians(float(min_elevation_deg)))
        _eps0_ok_full = np.asarray(sin_el_full, dtype=np.float64) >= _sin_min_el_local
        eps0_ok_vis = _eps0_ok_full[visible_idx]
    else:
        eps0_ok_vis = np.ones(visible_idx.size, dtype=bool)

    # Per-system α₀ (method_3): per-visible-satellite exclusion-zone half-angle;
    # None → the scalar alpha0_deg applies to every satellite (single-entry).
    alpha0_vis = (
        np.asarray(alpha0_deg_all, dtype=np.float64)[visible_idx]
        if alpha0_deg_all is not None else None
    )
    # Step 18 bullet ② (gain / OR branch) can be disabled per FILING
    # (``strict_exclusion_zone``). In a fused run the flag is per satellite:
    # one filing's stricter reading must not suppress another filing's OR
    # satellites, nor the other way round.
    strict_excl_vis = (
        np.asarray(strict_exclusion_zone_all, dtype=bool)[visible_idx]
        if strict_exclusion_zone_all is not None else None
    )

    if _pfd_mask_uses_batch(pfd_mask):
        pos_vis = pos_ecef_all[visible_idx]
        alpha_arr, gso_alpha_ecef = compute_alpha_and_optimal_gso_fixed_es_batch(
            es_ecef, pos_vis, es_lat_deg, es_lon_deg, step_deg=1.0,
        )
        min_alpha_idx = int(np.argmin(np.abs(alpha_arr)))
        min_alpha = float(alpha_arr[min_alpha_idx])
        offaxis_arr = compute_offaxis_angle_batch(es_ecef, pos_vis, gso_ecef)
        theta_arr = None
        if es_antenna.requires_planar_angle:
            theta_arr = compute_offaxis_and_planar_angle_batch(
                np.repeat(es_ecef.reshape(1, 3), pos_vis.shape[0], axis=0),
                pos_vis,
                np.repeat(gso_ecef.reshape(1, 3), pos_vis.shape[0], axis=0),
                np.full(pos_vis.shape[0], es_lat_deg, dtype=np.float64),
                np.full(pos_vis.shape[0], es_lon_deg, dtype=np.float64),
            )[1]

        # GRX_rel(φ) for all visible satellites in a single batch call —
        # reuses the vectorized S.1428 kernel (scalar fallback for other
        # patterns); numerically equivalent to the per-satellite loop.
        g_rel_arr = _relative_gain_batch(es_antenna, offaxis_arr, theta_arr)

        any_critical_gain = False
        if (
            dual_ts is not None
            and dual_ts.mode == "s1503_gain"
            and dual_ts._gain_threshold_db is not None
        ):
            any_critical_gain = bool(np.any(g_rel_arr > dual_ts._gain_threshold_db))

        is_alpha_outside = np.abs(alpha_arr) >= (
            alpha0_vis if alpha0_vis is not None else alpha0_deg
        )
        n_vis = visible_idx.size
        # Step 18 bullet ①: |α| ≥ α₀  AND  ε_NGSO ≥ ε₀[lat][AzNGSO].
        is_standard_arr = is_alpha_outside & eps0_ok_vis
        override = np.zeros(n_vis, dtype=bool)
        # Step 18 bullet ②: GRX(φ) > min(Gmax−30, GRX(α₀)). Independent of α and ε₀.
        # Evaluated for every sat not already in standard (avoids double counting).
        # Same rule as ``s1503_or_condition_include``, vectorized: GRX_rel(α₀)
        # is evaluated at the **same** planar θ as the point (BO.1443 / 2D).
        # Collect mode (§D5.1.4.2) needs the OR flag for standard sats too.
        # Evaluate the gain condition for EVERY visible satellite whose own
        # filing enables the OR branch, not only the non-standard ones: printed
        # Step 22 / Step 20 apply it without qualification, and the Note then
        # removes only those already on the MAX_CO_FREQ list. A satellite that
        # met α₀/ε₀ but lost the Step-20 ranking is not on that list and must
        # be able to enter here.
        if strict_excl_vis is not None:
            cand = np.nonzero(~strict_excl_vis)[0].astype(np.int64)
        elif not strict_exclusion_zone:
            cand = np.arange(n_vis, dtype=np.int64)
        else:
            cand = np.empty(0, dtype=np.int64)
        if cand.size > 0:
            theta_cand = None if theta_arr is None else theta_arr[cand]
            g_rel_at_a0 = _relative_gain_batch(
                es_antenna,
                (alpha0_vis[cand] if alpha0_vis is not None
                 else np.full(cand.size, alpha0_deg, dtype=float)),
                theta_cand,
            )
            override[cand] = g_rel_arr[cand] > np.minimum(-30.0, g_rel_at_a0)
        eligible = is_standard_arr | override
        if not np.any(eligible):
            return [], override_epfd, min_alpha, any_critical_gain

        elig_j = np.nonzero(eligible)[0]
        alpha_e = alpha_arr[elig_j]
        idx_k = visible_idx[elig_j]

        if subsat_lat_all is not None and subsat_lon_all is not None:
            lat_e = subsat_lat_all[idx_k].astype(np.float64)
            lon_e = subsat_lon_all[idx_k].astype(np.float64)
        else:
            lat_e, lon_e, _ = ecef_to_lla_batch(pos_ecef_all[idx_k])

        if getattr(pfd_mask, "is_mixed_geometry", False):
            # Mixed-geometry fusion (e.g. method_3 fuses an azimuth_elevation
            # filing with an alpha_deltaLongitude one): route EACH satellite
            # through its OWN sub-mask's coordinate system. Both coordinate sets
            # are computed in batch, then selected per-sat by mask type.
            is_azel = pfd_mask.is_azel_per_sat(idx_k)
            az_e, el_e = _compute_mask_az_el_per_sat_frame_batch(
                es_ecef, pos_ecef_all[idx_k],
            )
            gso_lon_alpha = np.degrees(np.arctan2(gso_alpha_ecef[elig_j, 1], gso_alpha_ecef[elig_j, 0]))
            dlon_e = delta_longitude_s1503_deg(gso_lon_alpha, lon_e)
            # b-axis = azimuth for az/el sats, alpha otherwise; c-axis likewise.
            b_axis = np.where(is_azel, az_e, alpha_e)
            c_axis = np.where(is_azel, el_e, dlon_e)
            degenerate = is_azel & (np.isnan(az_e) | np.isnan(el_e))
            b_axis = np.where(degenerate, 0.0, b_axis)
            c_axis = np.where(degenerate, 0.0, c_axis)
            pfd_db = pfd_mask.get_pfd_batch(
                b_axis, lat_e, c_axis, sat_indices=idx_k,
            ) + pfd_bw_correction_db
            if np.any(degenerate):
                pfd_db = np.where(degenerate, -1000.0, pfd_db)
        elif getattr(pfd_mask, "mask_type", "") == "azimuth_elevation":
            # Az/El mask: query (azimuth, sub-sat latitude, elevation) computed
            # in each satellite's own local frame. Degenerate rows (ES on the
            # satellite) → −1000 dB, matching the scalar path's None handling.
            az_e, el_e = _compute_mask_az_el_per_sat_frame_batch(
                es_ecef, pos_ecef_all[idx_k],
            )
            degenerate = np.isnan(az_e) | np.isnan(el_e)
            az_q = np.where(degenerate, 0.0, az_e)
            el_q = np.where(degenerate, 0.0, el_e)
            pfd_db = pfd_mask.get_pfd_batch(
                az_q, lat_e, el_q, sat_indices=idx_k,
            ) + pfd_bw_correction_db
            if np.any(degenerate):
                pfd_db = np.where(degenerate, -1000.0, pfd_db)
        else:
            gso_lon_alpha = np.degrees(np.arctan2(gso_alpha_ecef[elig_j, 1], gso_alpha_ecef[elig_j, 0]))
            dlon_e = delta_longitude_s1503_deg(gso_lon_alpha, lon_e)
            pfd_db = pfd_mask.get_pfd_batch(
                alpha_e, lat_e, dlon_e, sat_indices=idx_k,
            ) + pfd_bw_correction_db

        # relative_gain_linear ≡ 10^(GRX_rel/10) — reuses the batch gains.
        g_lin = 10.0 ** (g_rel_arr[elig_j] / 10.0)
        epfd_lin = (10.0 ** (pfd_db / 10.0)) * g_lin

        if per_sat_out is not None:
            per_sat_out["idx"] = idx_k.astype(np.int64, copy=False)
            per_sat_out["epfd_lin"] = np.asarray(epfd_lin, dtype=np.float64)
            per_sat_out["std"] = is_standard_arr[elig_j]
            per_sat_out["orx"] = override[elig_j]
            return [], [], min_alpha, any_critical_gain

        std_mask = is_standard_arr[elig_j]
        standard_items = [
            (float(epfd_lin[j]), int(idx_k[j]))
            for j in range(elig_j.size)
            if std_mask[j]
        ]
        override_epfd = epfd_lin[~std_mask].tolist()
        # α₀/ε₀ satellites that ALSO satisfy the gain condition. Those the cap
        # keeps are counted once, inside the MAX_CO_FREQ list; the rest belong
        # to Step 22 (printed Note, p. 99).
        _or_mask = override[elig_j]
        _std_or = std_mask & _or_mask
        std_or_items = (
            [
                (float(epfd_lin[j]), int(idx_k[j]))
                for j in range(elig_j.size)
                if _std_or[j]
            ]
            if bool(_std_or.any()) else None
        )
        # Same values as `override_epfd`, paired with their satellite index —
        # built only when the per-system split is requested (method_3).
        override_items = (
            [
                (float(epfd_lin[j]), int(idx_k[j]))
                for j in range(elig_j.size)
                if not std_mask[j]
            ]
            if per_system_out is not None else None
        )

        standard_epfd, override_epfd = _finalize_epfd_after_max_co_freq(
            standard_items,
            override_epfd,
            max_co_freq,
            strict_max_co_freq_total,
            min_angle_at_es_deg,
            es_ecef,
            pos_ecef_all,
            system_id_all=system_id_all,
            max_co_freq_by_system=max_co_freq_by_system,
            override_items=override_items,
            per_system_out=per_system_out,
            std_or_items=std_or_items,
            min_angle_at_es_by_system=min_angle_at_es_by_system,
        )

        return standard_epfd, override_epfd, min_alpha, any_critical_gain

    # --- Scalar path (``_compute_pfd_3d``): 1D masks (and Az/El fallback) ---
    min_alpha = 180.0
    any_critical_gain = False
    standard_items: list[tuple[float, int]] = []
    # Step 22 (OR) values with their satellite index — only collected when the
    # per-system split is requested (method_3); mirrors `override_epfd`.
    _override_items_scalar: list[tuple[float, int]] = []
    # α₀/ε₀ satellites that also satisfy the gain condition (Step 22 + Note).
    _std_or_items_scalar: list[tuple[float, int]] = []
    _ps_idx: list[int] = []
    _ps_epfd: list[float] = []
    _ps_std: list[bool] = []
    _ps_orx: list[bool] = []
    for j_local, k in enumerate(visible_idx):
        pos_ecef = pos_ecef_all[k]
        vel_ecef = vel_ecef_all[k]

        alpha = compute_alpha_angle_fast_components(
            es_x=es_x, es_y=es_y, es_z=es_z,
            ng_x=float(pos_ecef[0]), ng_y=float(pos_ecef[1]), ng_z=float(pos_ecef[2]),
            es_lat_deg=es_lat_deg, es_lon_deg=es_lon_deg,
        )
        if abs(alpha) < abs(min_alpha):
            min_alpha = alpha
        offaxis = compute_offaxis_angle(es_ecef, pos_ecef, gso_ecef)
        theta_planar = None
        if es_antenna.requires_planar_angle:
            _, theta_planar = compute_offaxis_and_planar_angle(
                es_ecef, pos_ecef, gso_ecef, es_lat_deg, es_lon_deg
            )
        if dual_ts is not None and dual_ts.mode == "s1503_gain":
            if dual_ts.is_critical_gain(es_antenna.relative_gain(offaxis, theta_planar)):
                any_critical_gain = True

        _alpha0_k = (
            float(alpha0_deg_all[k]) if alpha0_deg_all is not None else alpha0_deg
        )
        _strict_excl_k = (
            bool(strict_exclusion_zone_all[k])
            if strict_exclusion_zone_all is not None else strict_exclusion_zone
        )
        is_alpha_outside = abs(alpha) >= _alpha0_k
        is_eps0_ok = bool(eps0_ok_vis[j_local])
        is_standard = is_alpha_outside and is_eps0_ok
        if per_sat_out is not None:
            # Collect mode: OR flag evaluated for every satellite (§D5.1.4.2).
            is_or_flag = s1503_or_condition_include(
                es_antenna, offaxis, _alpha0_k, theta_planar,
                disable_or_condition=_strict_excl_k,
            )
            is_override = (not is_standard) and is_or_flag
        else:
            # Same reading as the vectorised branch: the gain condition is
            # evaluated for EVERY satellite (printed Step 22 has no qualifier);
            # ``is_override`` stays the non-standard part, and the standard
            # satellites that also satisfy it are carried separately so the
            # Note can drop the ones the cap keeps.
            is_or_flag = s1503_or_condition_include(
                es_antenna, offaxis, _alpha0_k, theta_planar,
                disable_or_condition=_strict_excl_k,
            )
            is_override = (not is_standard) and is_or_flag
        if not is_standard and not is_override:
            continue

        if subsat_lat_all is not None and subsat_lon_all is not None:
            subsat_lat = float(subsat_lat_all[k])
            subsat_lon = float(subsat_lon_all[k])
        else:
            subsat_lat, subsat_lon, _ = ecef_to_lla(pos_ecef)

        slf = None if sat_local_frames is None else sat_local_frames[k]
        pfd_db = _compute_pfd_3d(
            pfd_mask=pfd_mask,
            alpha_deg=alpha,
            ngso_sat_eci=pos_ecef,
            ngso_sat_vel_eci=vel_ecef,
            es_lon_deg=es_lon_deg,
            t_s=t_s,
            es_lat_deg=es_lat_deg,
            gso_ecef=gso_ecef,
            pfd_bw_correction_db=pfd_bw_correction_db,
            ngso_sat_ecef=pos_ecef,
            es_ecef_cached=es_ecef,
            subsat_lat_deg=subsat_lat,
            subsat_lon_deg=subsat_lon,
            sat_local_frame=slf,
            sat_idx=int(k),
        )

        epfd_i = 10.0 ** (pfd_db / 10.0) * es_antenna.relative_gain_linear(offaxis, theta_planar)
        if per_sat_out is not None:
            _ps_idx.append(int(k))
            _ps_epfd.append(float(epfd_i))
            _ps_std.append(bool(is_standard))
            _ps_orx.append(bool(is_or_flag))
            continue
        if is_standard:
            standard_items.append((epfd_i, int(k)))
            if is_or_flag:
                _std_or_items_scalar.append((float(epfd_i), int(k)))
        else:
            override_epfd.append(epfd_i)
            if per_system_out is not None:
                _override_items_scalar.append((float(epfd_i), int(k)))

    if per_sat_out is not None:
        per_sat_out["idx"] = np.asarray(_ps_idx, dtype=np.int64)
        per_sat_out["epfd_lin"] = np.asarray(_ps_epfd, dtype=np.float64)
        per_sat_out["std"] = np.asarray(_ps_std, dtype=bool)
        per_sat_out["orx"] = np.asarray(_ps_orx, dtype=bool)
        return [], [], min_alpha, any_critical_gain

    standard_epfd, override_epfd = _finalize_epfd_after_max_co_freq(
        standard_items,
        override_epfd,
        max_co_freq,
        strict_max_co_freq_total,
        min_angle_at_es_deg,
        es_ecef,
        pos_ecef_all,
        system_id_all=system_id_all,
        max_co_freq_by_system=max_co_freq_by_system,
        override_items=(_override_items_scalar if per_system_out is not None else None),
        per_system_out=per_system_out,
        std_or_items=(_std_or_items_scalar or None),
        min_angle_at_es_by_system=min_angle_at_es_by_system,
    )

    return standard_epfd, override_epfd, min_alpha, any_critical_gain


def _resolve_max_co_freq(lat_deg: float, max_co_freq_by_lat: list) -> int:
    """Returns the MAX_CO_FREQ applicable to the ES latitude (Steps 19-22, S.1503-4).

    Iterates over the latitude bands in ``max_co_freq_by_lat`` (list of tuples
    ``(lat_fr, lat_to, nbr_op_sat)``) and returns ``nbr_op_sat`` for the band that
    contains ``lat_deg``.  Returns 0 (= unlimited) if the list is empty or if
    no band covers the latitude — the conservative choice (no cap → more
    satellites included in the aggregate).
    """
    if not max_co_freq_by_lat:
        return 0
    for lat_fr, lat_to, nco in max_co_freq_by_lat:
        if lat_fr <= lat_deg <= lat_to:
            return int(nco)
    return 0


def _resolve_min_duration(lat_deg: float, min_duration_by_lat: list) -> float:
    """MIN_DURATION (s) applicable to the ES latitude (§D5.1.4.2 track duration).

    ``min_duration_by_lat`` is a list of ``(lat_fr, lat_to, min_duration_s)``.
    Returns 0.0 (= standard §D5.1.4.1 path) when the list is empty or no band
    covers ``lat_deg``.
    """
    if not min_duration_by_lat:
        return 0.0
    for lat_fr, lat_to, dur in min_duration_by_lat:
        if lat_fr <= lat_deg <= lat_to:
            return float(dur)
    return 0.0


def _epfd_aggregate_dBW_at_instant_scalar_fallback(
    constellation: list[OrbitalElements],
    t_s: float,
    wcg: WCGResult,
    pfd_mask: PFDMask,
    es_antenna: EarthStationAntenna,
    alpha0_deg: float,
    min_elevation_deg: float,
    pfd_bw_correction_db: float,
    max_co_freq_by_lat: list | None = None,
    strict_max_co_freq_total: bool = False,
    raan_dot_artificial_rad_s: float = 0.0,
    raan_dot_override_rad_s: float | None = None,
    strict_exclusion_zone: bool = False,
    wdelta_deg: float = 0.0,
    t_run_s: float = 0.0,
    min_angle_at_es_deg: float = 0.0,
    gso_min_elevation_deg: float = -90.0,
) -> float:
    """Scalar fallback for the instantaneous aggregate at the WCG.

    Used only when the vectorized path returns empty/invalid, to avoid
    inconsistencies between the single-entry WCG and the aggregate reported at t=0.
    """
    es_lat = wcg.es_lat_deg
    es_lon = wcg.es_lon_deg
    gso_lon = wcg.gso_lon_deg
    es_ecef = _es_ecef_from_wcg(wcg)
    if np.linalg.norm(es_ecef) < RE_KM * 0.9:
        return -999.0

    gso_ecef = gso_position_ecef(gso_lon, t_s)
    # S.1503-4 EPFD↓ Step 18 has NO elGSO term — the per-instant elGSO
    # hard-exclusion is an S.1503-2 behaviour, applied only when emulating it
    # (strict_exclusion_zone). εGSO still gates the WCGD search (§D3.1.2).
    if strict_exclusion_zone and _epfd_gso_min_elevation_active(gso_min_elevation_deg):
        if compute_elevation(es_ecef, gso_ecef, es_lat, es_lon) < float(gso_min_elevation_deg):
            return -999.0
    max_co_freq = _resolve_max_co_freq(es_lat, max_co_freq_by_lat or [])
    standard_items: list[tuple[float, int]] = []
    override_epfd: list[float] = []

    pos_ecef_all, vel_ecef_all = propagate_and_to_ecef_batch(
        constellation, t_s,
        raan_dot_artificial_rad_s=raan_dot_artificial_rad_s,
        raan_dot_override_rad_s=raan_dot_override_rad_s,
        wdelta_deg=wdelta_deg,
        t_run_s=t_run_s,
    )

    for k in range(len(constellation)):
        pos_ecef = pos_ecef_all[k]
        vel_ecef = vel_ecef_all[k]
        elev = compute_elevation(es_ecef, pos_ecef, es_lat, es_lon)
        # S.1503-4 Step 11: visibility = horizon (§D.6.4.3); ε₀ enters in
        # bullet ① of Step 18 and in the standard vs override decision below.
        if elev < 0.0:
            continue

        alpha = compute_alpha_angle_fast_components(
            es_x=float(es_ecef[0]),
            es_y=float(es_ecef[1]),
            es_z=float(es_ecef[2]),
            ng_x=float(pos_ecef[0]),
            ng_y=float(pos_ecef[1]),
            ng_z=float(pos_ecef[2]),
            es_lat_deg=es_lat,
            es_lon_deg=es_lon,
        )
        offaxis = compute_offaxis_angle(es_ecef, pos_ecef, gso_ecef)
        theta_planar = None
        if es_antenna.requires_planar_angle:
            _, theta_planar = compute_offaxis_and_planar_angle(
                es_ecef, pos_ecef, gso_ecef, es_lat, es_lon
            )

        is_alpha_outside = abs(alpha) >= alpha0_deg
        is_eps0_ok = elev >= float(min_elevation_deg)
        is_standard = is_alpha_outside and is_eps0_ok
        is_override = (not is_standard) and s1503_or_condition_include(
            es_antenna, offaxis, alpha0_deg, theta_planar,
            disable_or_condition=strict_exclusion_zone,
        )
        if not is_standard and not is_override:
            continue

        subsat_lat, subsat_lon, _ = ecef_to_lla(pos_ecef)
        pfd_db = _compute_pfd_3d(
            pfd_mask=pfd_mask,
            alpha_deg=alpha,
            ngso_sat_eci=pos_ecef,
            ngso_sat_vel_eci=vel_ecef,
            es_lon_deg=es_lon,
            t_s=t_s,
            es_lat_deg=es_lat,
            gso_ecef=gso_ecef,
            pfd_bw_correction_db=pfd_bw_correction_db,
            ngso_sat_ecef=pos_ecef,
            es_ecef_cached=es_ecef,
            subsat_lat_deg=subsat_lat,
            subsat_lon_deg=subsat_lon,
            sat_local_frame=None,
            sat_idx=int(k),
        )
        epfd_i = 10.0 ** (pfd_db / 10.0) * es_antenna.relative_gain_linear(offaxis, theta_planar)
        if is_standard:
            standard_items.append((epfd_i, k))
        else:
            override_epfd.append(epfd_i)

    standard_epfd, override_epfd = _finalize_epfd_after_max_co_freq(
        standard_items,
        override_epfd,
        max_co_freq,
        strict_max_co_freq_total,
        min_angle_at_es_deg,
        es_ecef,
        pos_ecef_all,
    )

    epfd_sum_linear = sum(standard_epfd) + sum(override_epfd)
    return 10.0 * math.log10(epfd_sum_linear) if epfd_sum_linear > 0.0 else -999.0


def epfd_aggregate_dBW_at_instant(
    constellation: list[OrbitalElements],
    t_s: float,
    wcg: WCGResult,
    pfd_mask: PFDMask,
    es_antenna: EarthStationAntenna,
    alpha0_deg: float,
    min_elevation_deg: float,
    pfd_bw_correction_db: float,
    max_co_freq_by_lat: list | None = None,
    strict_max_co_freq_total: bool = False,
    raan_dot_artificial_rad_s: float = 0.0,
    raan_dot_override_rad_s: float | None = None,
    strict_exclusion_zone: bool = False,
    wdelta_deg: float = 0.0,
    t_run_s: float = 0.0,
    min_angle_at_es_deg: float = 0.0,
    gso_min_elevation_deg: float = -90.0,
) -> float:
    """EPFD↓ aggregated at an instant, with the same rule as ``run_epfd_simulation`` / D.5.

    Includes the MAX_CO_FREQ cap (S.1503-4 §D.5.1.4.1 Steps 19–22) and standard/override separation.
    Uses ``propagate_and_to_ecef_batch`` and vectorized elevation as in the parallel chunks.
    ``dual_ts=None`` — the dual step only affects the choice of Δt, not the linear sum.

    **Station keeping (D6.3.4):** With ``wdelta_deg > 0`` and ``t_run_s > 0``, at ``t_s=0`` the
    propagator applies a RAAN offset ``Wdelta·(2t_s/T_run - 1) = -Wdelta``, shifting the
    constellation relative to the geometry used in the WCG search (nominal). For the
    ``epfd_aggregate_dBW`` value reported alongside the **single-entry WCG**, use
    ``wdelta_deg=0`` and ``t_run_s=0`` to coincide with the Phase 1 orbit; the time
    series continues with Wdelta in the D.5 steps.
    """
    es_lat = wcg.es_lat_deg
    es_lon = wcg.es_lon_deg
    gso_lon = wcg.gso_lon_deg
    es_ecef = _es_ecef_from_wcg(wcg)
    if np.linalg.norm(es_ecef) < RE_KM * 0.9:
        return -999.0

    es_x, es_y, es_z = float(es_ecef[0]), float(es_ecef[1]), float(es_ecef[2])
    lat_r = math.radians(es_lat)
    lon_r = math.radians(es_lon)
    sl, cl = math.sin(lat_r), math.cos(lat_r)
    so, co = math.sin(lon_r), math.cos(lon_r)
    R_enu = np.array([
        [-so,        co,       0.0],
        [-sl * co,  -sl * so,  cl ],
        [ cl * co,   cl * so,  sl ],
    ])
    max_co_freq = _resolve_max_co_freq(es_lat, max_co_freq_by_lat or [])

    gso_ecef = gso_position_ecef(gso_lon, t_s)
    # S.1503-4 Step 18 has no elGSO term; the per-instant elGSO hard-exclusion
    # is S.1503-2 emulation only (strict_exclusion_zone). WCGD keeps εGSO.
    if strict_exclusion_zone and _epfd_gso_min_elevation_active(gso_min_elevation_deg):
        if compute_elevation(es_ecef, gso_ecef, es_lat, es_lon) < float(gso_min_elevation_deg):
            return -999.0
    pos_ecef_all, vel_ecef_all = propagate_and_to_ecef_batch(
        constellation, t_s,
        raan_dot_artificial_rad_s=raan_dot_artificial_rad_s,
        raan_dot_override_rad_s=raan_dot_override_rad_s,
        wdelta_deg=wdelta_deg,
        t_run_s=t_run_s,
    )
    diff_all = pos_ecef_all - es_ecef
    enu_all = (R_enu @ diff_all.T).T
    ranges = np.linalg.norm(diff_all, axis=1)
    sin_el = np.where(ranges > 1e-6, enu_all[:, 2] / ranges, -1.0)
    # S.1503-4 §D.5.1.4.1 Step 11: visibility = above the horizon (§D.6.4.3).
    # ε₀ is applied inside _accumulate_... as the bullet ① condition, to
    # allow bullet ② (gain OR) to rescue low-elevation satellites.
    visible_idx = np.where(sin_el >= 0.0)[0]

    n_sat_agg = int(pos_ecef_all.shape[0])
    min_h_agg = _min_operating_height_km_batch(constellation, n_sat_agg)

    standard_epfd, override_epfd, _, _ = _accumulate_epfd_visible_satellites(
        visible_idx=visible_idx,
        pos_ecef_all=pos_ecef_all,
        vel_ecef_all=vel_ecef_all,
        es_ecef=es_ecef,
        es_x=es_x,
        es_y=es_y,
        es_z=es_z,
        es_lat_deg=es_lat,
        es_lon_deg=es_lon,
        gso_ecef=gso_ecef,
        alpha0_deg=alpha0_deg,
        pfd_mask=pfd_mask,
        es_antenna=es_antenna,
        pfd_bw_correction_db=pfd_bw_correction_db,
        max_co_freq=max_co_freq,
        strict_max_co_freq_total=strict_max_co_freq_total,
        subsat_lat_all=None,
        subsat_lon_all=None,
        sat_local_frames=None,
        min_operating_height_km_all=min_h_agg,
        dual_ts=None,
        t_s=t_s,
        strict_exclusion_zone=strict_exclusion_zone,  # already present; ensures propagation
        min_angle_at_es_deg=min_angle_at_es_deg,
        min_elevation_deg=min_elevation_deg,
        sin_el_full=sin_el,
    )
    epfd_sum_linear = sum(standard_epfd) + sum(override_epfd)
    epfd_agg_db = 10.0 * math.log10(epfd_sum_linear) if epfd_sum_linear > 0 else -999.0

    if epfd_agg_db <= -900.0:
        logger.warning(
            "epfd_aggregate_dBW_at_instant: vectorized path returned empty at the WCG; "
            "recomputing via scalar fallback for consistency."
        )
        epfd_agg_db = _epfd_aggregate_dBW_at_instant_scalar_fallback(
            constellation=constellation,
            t_s=t_s,
            wcg=wcg,
            pfd_mask=pfd_mask,
            es_antenna=es_antenna,
            alpha0_deg=alpha0_deg,
            min_elevation_deg=min_elevation_deg,
            pfd_bw_correction_db=pfd_bw_correction_db,
            max_co_freq_by_lat=max_co_freq_by_lat,
            strict_max_co_freq_total=strict_max_co_freq_total,
            raan_dot_artificial_rad_s=raan_dot_artificial_rad_s,
            raan_dot_override_rad_s=raan_dot_override_rad_s,
            strict_exclusion_zone=strict_exclusion_zone,
            wdelta_deg=wdelta_deg,
            t_run_s=t_run_s,
            min_angle_at_es_deg=min_angle_at_es_deg,
            gso_min_elevation_deg=gso_min_elevation_deg,
        )

    return epfd_agg_db


def _progress_bar(pct: float, width: int = 28) -> str:
    """Returns a simple text bar for logs."""
    pct_c = max(0.0, min(100.0, pct))
    filled = int(round((pct_c / 100.0) * width))
    return "[" + ("#" * filled) + ("-" * (width - filled)) + "]"




@dataclass
class EPFDTimeStepResult:
    """EPFD result for a single time step."""
    time_s: float
    epfd_aggregate_dBW: float
    # Satellites with geometric elevation ≥ 0° (above the ES local horizon).
    num_horizon_sats: int
    # Satellites with elevation ≥ ε₀ (S.1503 operational minimum / config).
    num_visible_sats: int
    num_contributing_sats: int
    min_alpha_deg: float
    duration_s: float = 1.0  # real interval duration (important for the weighted CCDF)


@dataclass
class EPFDSimulationResult:
    """Complete result of the EPFD↓ simulation.

    Memory: by default, it does **not** retain ``time_steps`` (an O(N) cost that is
    impractical over hundreds of millions of steps). The streaming accumulator ``acc``
    keeps an EPFD histogram (0.1 dB bins), a histogram of satellite counts,
    diagnostic aggregates and an adaptive decimated trace for the panel — all
    in <1 MB regardless of N.

    If ``keep_full_history`` is True (debug/explanation mode), each step is
    also accumulated in ``time_steps`` (legacy format). **Warn the user
    about the RAM cost** before enabling for N>~1e6.
    """
    wcg: WCGResult
    acc: EPFDStreamAccumulator = field(default_factory=EPFDStreamAccumulator)
    keep_full_history: bool = False
    time_steps: list[EPFDTimeStepResult] = field(default_factory=list)
    epfd_values_dBW: np.ndarray = field(default_factory=lambda: np.array([]))
    cdf_epfd_dBW: np.ndarray = field(default_factory=lambda: np.array([]))
    cdf_percentage: np.ndarray = field(default_factory=lambda: np.array([]))

    # Track-duration variant (S.1503-4 §D5.1.4.2). Empty for the standard run.
    # ``window_stats[w]`` is the CDF material of slide-window set ``w``; the
    # top-level ``cdf_*`` above hold the worst-per-level envelope across sets
    # (correct go/no-go: the network complies iff *every* window set complies).
    window_stats: list = field(default_factory=list)
    per_window_ccdf: list = field(default_factory=list)  # list[(bins_desc, pct_desc)]
    windows: "TrackDurationWindows | None" = None
    worst_window_index: int = -1
    # Windowed runs only. ``n_timeline_steps`` is N_TotalSteps (§D5.1.3): the
    # length of the single simulated timeline, which is NOT ``acc.n_steps`` —
    # the headline ``acc`` is one window *set*, so its step count is
    # N_Repeat × N_SW (the samples that set contributes to its own CCDF).
    n_timeline_steps: int = 0
    timeline_t_fine_s: float = 0.0
    # §D5.1.4.2 Step 19/19bis tallies (see ``_finalize_windowed_result``).
    window_diagnostics: dict = field(default_factory=dict)
    # Inputs the windowed selection accepted for signature uniformity but does
    # NOT apply (MIN_ANGLE_AT_ES, strict_max_co_freq_total, Res.76 per-system
    # MAX_CO_FREQ), and which reading of Step 20's gain branch ran.
    selection_inputs_ignored: list = field(default_factory=list)
    or_rescues_capped: bool = False

    def build_cdf(self):
        """Builds the CCDF from the accumulator (or from the history if retained).

        In light of S.1503-4 D7.1.3, the statistic is accumulated in 0.1 dB
        bins weighted by the real ``duration_s`` of each interval, which is
        essential when the dual time step is active (fine steps near
        the GSO arc represent less real time than coarse steps and must
        not be over-represented in the distribution). ``cdf_epfd_dBW`` stores
        the quantized levels (0.1 dB steps) and ``cdf_percentage[i]`` the
        percentage of total time during which the EPFD exceeds ``cdf_epfd_dBW[i]``.
        """
        # Windowed variant (§D5.1.4.2): the envelope CDF across slide-window sets
        # is authoritative and already stored — the headline ``acc`` is only a
        # representative histogram, so do not rebuild from it.
        if self.window_stats:
            return

        # Preferred path: the streaming accumulator already has the histogram ready.
        if self.acc is not None and self.acc.n_steps > 0:
            bins_desc, pct = self.acc.build_ccdf()
            self.cdf_epfd_dBW = bins_desc
            self.cdf_percentage = pct
            if self.keep_full_history and self.time_steps:
                self.epfd_values_dBW = np.array(
                    [ts.epfd_aggregate_dBW for ts in self.time_steps], dtype=float
                )
            return

        if len(self.time_steps) == 0:
            return

        epfd = np.array([ts.epfd_aggregate_dBW for ts in self.time_steps], dtype=float)
        durations = np.array([ts.duration_s for ts in self.time_steps], dtype=float)
        self.epfd_values_dBW = epfd

        valid = np.isfinite(epfd) & np.isfinite(durations) & (epfd > -900.0) & (durations > 0.0)
        if not np.any(valid):
            self.cdf_epfd_dBW = np.array([])
            self.cdf_percentage = np.array([])
            return

        epfd = epfd[valid]
        durations = durations[valid]

        bin_size_db = 0.1
        epfd_bin = np.floor(epfd / bin_size_db + 1e-12) * bin_size_db

        bins_asc, inverse = np.unique(epfd_bin, return_inverse=True)
        weights_asc = np.bincount(inverse, weights=durations, minlength=len(bins_asc)).astype(float)

        total_time = durations.sum()
        if total_time <= 0.0:
            self.cdf_epfd_dBW = np.array([])
            self.cdf_percentage = np.array([])
            return

        bins_desc = bins_asc[::-1]
        weights_desc = weights_asc[::-1]
        percentages = np.cumsum(weights_desc) / total_time * 100.0

        self.cdf_epfd_dBW = bins_desc
        self.cdf_percentage = percentages


class _DualTSProxy:
    """Lightweight DualTimeStep proxy for pool workers (picklable)."""
    __slots__ = ("mode", "fine", "coarse", "ncoarse", "_gain_threshold_db")

    def __init__(
        self,
        mode: str,
        fine: float,
        coarse: float,
        ncoarse: int,
        gain_threshold_db: float | None,
    ) -> None:
        self.mode = mode
        self.fine = fine
        self.coarse = coarse
        self.ncoarse = ncoarse
        self._gain_threshold_db = gain_threshold_db

    def is_critical_gain(self, g_db: float) -> bool:
        thr = self._gain_threshold_db
        if thr is None:
            return False
        return g_db > thr


def _acc_add_per_system(
    acc,
    per_system_step: dict[int, list],
    all_sids: np.ndarray | None,
    t_s: float,
    duration_s: float,
    min_alpha_deg: float,
    is_fine: bool | None = None,
) -> None:
    """Feeds one step's per-system split into ``acc``'s sub-accumulators.

    **Every** system is advanced on **every** step — including systems that
    contributed nothing (EPFD reported as −999, exactly as the joint engine does
    for a step with no contributors). This is not cosmetic: the CCDF denominator
    is ``total_duration_s``, so skipping the silent steps of a system would
    shrink its denominator and inflate its percentages. Advancing all of them
    keeps every per-system curve on the joint run's time base, which is what
    makes the curves comparable percentile-by-percentile and their linear sum
    reproduce the joint one.

    ``min_alpha_deg`` is the **joint** step value (α is a geometry property of
    the step, not of a system) and the horizon/visible counts are left at 0 —
    only the contributing count is per-system. So a sub-accumulator's
    ``min_alpha``/horizon/visible fields mirror the step, not the system.
    """
    if all_sids is None:
        return
    for sid in all_sids:
        sid = int(sid)
        lin, n_contrib = per_system_step.get(sid, (0.0, 0))
        acc.system_acc(sid).add(
            time_s=t_s,
            epfd_db=(10.0 * math.log10(lin) if lin > 0.0 else -999.0),
            duration_s=duration_s,
            num_horizon_sats=0,
            num_visible_sats=0,
            num_contributing_sats=int(n_contrib),
            min_alpha_deg=min_alpha_deg,
            is_fine=is_fine,
        )


# Helper for parallelism (module level)
def _simulate_chunk(args):
    """Simulates a time interval (chunk) with vectorized batch propagation.

    For each time step:
      1. Propagates the whole constellation at once (NumPy batch).
      2. Converts ECI→ECEF in batch (single matrix rotation).
      3. Computes elevation for all satellites in batch (precomputed ENU matrix).
      4. Iterates only over the visible satellites for alpha/PFD (small subset).

    Returns ``{"acc": EPFDStreamAccumulator, "ts": list[EPFDTimeStepResult] | None}``.
    ``ts`` is ``None`` except when ``keep_full_history=True`` (last element of args).
    """
    # Compat: unpacking tolerant of chunks without ``keep_full_history`` at the end
    # and/or without the multi-system aggregation fields (system_id + table/system
    # + per-satellite ε₀/α₀ arrays).
    system_id_per_sat = None
    max_co_freq_by_system = None
    min_el_all = None
    alpha0_all = None
    # Per-system extras (method_3): {"strict_excl_all": per-sat bool array,
    # "min_angle_by_system": {system_id: Step-21 min separation angle}}.
    per_system_extra = None
    if len(args) == 26:
        (start_step, num_steps, t_start, tstep_s, constellation,
         wcg, pfd_mask, es_antenna, alpha0_deg, min_elevation_deg,
         pfd_bw_correction_db, raan_dot_artificial_rad_s, raan_dot_override_rad_s,
         max_co_freq_by_lat, strict_max_co_freq_total, strict_exclusion_zone,
         min_angle_at_es_deg, wdelta_deg, t_run_s, gso_min_elevation_deg,
         keep_full_history, system_id_per_sat, max_co_freq_by_system,
         min_el_all, alpha0_all, per_system_extra) = args
    elif len(args) == 25:
        (start_step, num_steps, t_start, tstep_s, constellation,
         wcg, pfd_mask, es_antenna, alpha0_deg, min_elevation_deg,
         pfd_bw_correction_db, raan_dot_artificial_rad_s, raan_dot_override_rad_s,
         max_co_freq_by_lat, strict_max_co_freq_total, strict_exclusion_zone,
         min_angle_at_es_deg, wdelta_deg, t_run_s, gso_min_elevation_deg,
         keep_full_history, system_id_per_sat, max_co_freq_by_system,
         min_el_all, alpha0_all) = args
    elif len(args) == 23:
        (start_step, num_steps, t_start, tstep_s, constellation,
         wcg, pfd_mask, es_antenna, alpha0_deg, min_elevation_deg,
         pfd_bw_correction_db, raan_dot_artificial_rad_s, raan_dot_override_rad_s,
         max_co_freq_by_lat, strict_max_co_freq_total, strict_exclusion_zone,
         min_angle_at_es_deg, wdelta_deg, t_run_s, gso_min_elevation_deg,
         keep_full_history, system_id_per_sat, max_co_freq_by_system) = args
    elif len(args) == 21:
        (start_step, num_steps, t_start, tstep_s, constellation,
         wcg, pfd_mask, es_antenna, alpha0_deg, min_elevation_deg,
         pfd_bw_correction_db, raan_dot_artificial_rad_s, raan_dot_override_rad_s,
         max_co_freq_by_lat, strict_max_co_freq_total, strict_exclusion_zone,
         min_angle_at_es_deg, wdelta_deg, t_run_s, gso_min_elevation_deg,
         keep_full_history) = args
    else:
        (start_step, num_steps, t_start, tstep_s, constellation,
         wcg, pfd_mask, es_antenna, alpha0_deg, min_elevation_deg,
         pfd_bw_correction_db, raan_dot_artificial_rad_s, raan_dot_override_rad_s,
         max_co_freq_by_lat, strict_max_co_freq_total, strict_exclusion_zone,
         min_angle_at_es_deg, wdelta_deg, t_run_s, gso_min_elevation_deg) = args
        keep_full_history = False

    acc = EPFDStreamAccumulator()
    results: list[EPFDTimeStepResult] | None = [] if keep_full_history else None
    _strict_excl_all = (per_system_extra or {}).get("strict_excl_all")
    _min_angle_by_system = (per_system_extra or {}).get("min_angle_by_system")
    t_s = t_start

    es_lat = wcg.es_lat_deg
    es_lon = wcg.es_lon_deg
    gso_lon = wcg.gso_lon_deg
    es_ecef = _es_ecef_from_wcg(wcg)
    es_x, es_y, es_z = float(es_ecef[0]), float(es_ecef[1]), float(es_ecef[2])

    lat_r = math.radians(es_lat)
    lon_r = math.radians(es_lon)
    sl, cl = math.sin(lat_r), math.cos(lat_r)
    so, co = math.sin(lon_r), math.cos(lon_r)
    R_enu = np.array([
        [-so,        co,       0.0],
        [-sl * co,  -sl * so,  cl ],
        [ cl * co,   cl * so,  sl ],
    ])
    max_co_freq = _resolve_max_co_freq(es_lat, max_co_freq_by_lat)

    N = len(constellation)

    _prop_cache = build_constellation_cache(
        constellation, raan_dot_override_rad_s=raan_dot_override_rad_s,
    )
    # Constellation-invariant within the chunk — hoisted out of the time loop.
    min_h_chunk = _min_operating_height_km_batch(constellation, N)
    sin_min_el_arr = (
        np.sin(np.radians(min_el_all)) if min_el_all is not None else None
    )

    # method_3 per-system decomposition in the SAME pass: the full system id set
    # (hoisted — every step must advance every system, see _acc_add_per_system).
    all_sids = (
        np.unique(system_id_per_sat)
        if (system_id_per_sat is not None and max_co_freq_by_system is not None)
        else None
    )

    for _ in range(num_steps):
        gso_ecef = gso_position_ecef(gso_lon, t_s)

        pos_ecef_all, vel_ecef_all = propagate_and_to_ecef_batch(
            constellation, t_s,
            raan_dot_artificial_rad_s=raan_dot_artificial_rad_s,
            raan_dot_override_rad_s=raan_dot_override_rad_s,
            _cache=_prop_cache,
            wdelta_deg=wdelta_deg,
            t_run_s=t_run_s,
        )
        # pos_ecef_all: (N, 3)

        # 2. Batch elevation: sin(elev) = (R_enu @ diff)[2] / |diff|
        diff_all = pos_ecef_all - es_ecef          # (N, 3)
        enu_all  = (R_enu @ diff_all.T).T           # (N, 3)
        ranges   = np.linalg.norm(diff_all, axis=1) # (N,)
        sin_el   = np.where(ranges > 1e-6, enu_all[:, 2] / ranges, -1.0)

        num_horizon = int(np.count_nonzero(sin_el >= 0.0))

        # S.1503-4 Step 11: visibility = horizon. ε₀ enters in bullet ① inside
        # _accumulate_... to preserve the OR path (bullet ②).
        visible_idx = np.where(sin_el >= 0.0)[0]
        # S.1503-4 Step 18 has no elGSO term; per-instant elGSO hard-exclusion
        # is S.1503-2 emulation only (strict_exclusion_zone). WCGD keeps εGSO.
        if strict_exclusion_zone and _epfd_gso_min_elevation_active(gso_min_elevation_deg):
            if compute_elevation(es_ecef, gso_ecef, es_lat, es_lon) < float(gso_min_elevation_deg):
                visible_idx = np.array([], dtype=np.int64)

        # num_visible = sats with ε ≥ ε₀ (reporting semantics; the accumulator
        # receives the horizon set and filters ε₀ internally for Step 18 ①).
        num_visible = int(np.count_nonzero(
            sin_el >= (sin_min_el_arr if sin_min_el_arr is not None
                       else math.sin(math.radians(min_elevation_deg)))
        ))

        per_system_step: dict[int, list] | None = {} if all_sids is not None else None
        standard_epfd, override_epfd, min_alpha, _ = _accumulate_epfd_visible_satellites(
            visible_idx=visible_idx,
            pos_ecef_all=pos_ecef_all,
            vel_ecef_all=vel_ecef_all,
            es_ecef=es_ecef,
            es_x=es_x,
            es_y=es_y,
            es_z=es_z,
            es_lat_deg=es_lat,
            es_lon_deg=es_lon,
            gso_ecef=gso_ecef,
            alpha0_deg=alpha0_deg,
            pfd_mask=pfd_mask,
            es_antenna=es_antenna,
            pfd_bw_correction_db=pfd_bw_correction_db,
            max_co_freq=max_co_freq,
            strict_max_co_freq_total=strict_max_co_freq_total,
            subsat_lat_all=None,
            subsat_lon_all=None,
            sat_local_frames=None,
            min_operating_height_km_all=min_h_chunk,
            dual_ts=None,
            t_s=t_s,
            strict_exclusion_zone=strict_exclusion_zone,
            min_angle_at_es_deg=min_angle_at_es_deg,
            min_elevation_deg=min_elevation_deg,
            sin_el_full=sin_el,
            system_id_all=system_id_per_sat,
            max_co_freq_by_system=max_co_freq_by_system,
            min_elevation_deg_all=min_el_all,
            alpha0_deg_all=alpha0_all,
            strict_exclusion_zone_all=_strict_excl_all,
            min_angle_at_es_by_system=_min_angle_by_system,
            per_system_out=per_system_step,
        )

        epfd_sum_linear = sum(standard_epfd) + sum(override_epfd)
        num_contributing = len(standard_epfd) + len(override_epfd)

        epfd_agg_db = (
            10.0 * math.log10(epfd_sum_linear) if epfd_sum_linear > 0 else -999.0
        )
        acc.add(
            time_s=t_s,
            epfd_db=epfd_agg_db,
            duration_s=tstep_s,
            num_horizon_sats=num_horizon,
            num_visible_sats=num_visible,
            num_contributing_sats=num_contributing,
            min_alpha_deg=min_alpha,
            is_fine=True,  # fixed step: uniform Δt (no coarse)
        )
        if per_system_step is not None:
            _acc_add_per_system(
                acc, per_system_step, all_sids, t_s, tstep_s, min_alpha,
                is_fine=True,
            )
        if results is not None:
            results.append(EPFDTimeStepResult(
                time_s=t_s,
                epfd_aggregate_dBW=epfd_agg_db,
                num_horizon_sats=num_horizon,
                num_visible_sats=num_visible,
                num_contributing_sats=num_contributing,
                min_alpha_deg=min_alpha,
                duration_s=tstep_s,
            ))
        t_s += tstep_s

    return {"acc": acc, "ts": results}


def _simulate_chunk_dual_ts(args):
    """Chunk worker for time simulation with parallel dual time step.

    Covers the real interval [t_start, t_end).  Each chunk starts with a
    conservative state (prev_any_critical_gain=True) to ensure that no
    critical zone is missed at the boundaries between chunks.

    Returns ``{"acc": EPFDStreamAccumulator, "ts": list | None}``.
    """
    system_id_per_sat = None
    max_co_freq_by_system = None
    min_el_all = None
    alpha0_all = None
    # Per-system extras (method_3): {"strict_excl_all": per-sat bool array,
    # "min_angle_by_system": {system_id: Step-21 min separation angle}}.
    per_system_extra = None
    if len(args) == 30:
        (t_start, t_end,
         dual_ts_mode, dual_ts_fine_s, dual_ts_coarse_s, dual_ts_ncoarse,
         dual_ts_gain_threshold_db, dual_ts_alpha_threshold_deg,
         constellation, wcg, pfd_mask, es_antenna, alpha0_deg, min_elevation_deg,
         pfd_bw_correction_db, raan_dot_artificial_rad_s, raan_dot_override_rad_s,
         max_co_freq_by_lat, strict_max_co_freq_total, strict_exclusion_zone,
         min_angle_at_es_deg, wdelta_deg, t_run_s, gso_min_elevation_deg,
         keep_full_history, system_id_per_sat, max_co_freq_by_system,
         min_el_all, alpha0_all, per_system_extra) = args
    elif len(args) == 29:
        (t_start, t_end,
         dual_ts_mode, dual_ts_fine_s, dual_ts_coarse_s, dual_ts_ncoarse,
         dual_ts_gain_threshold_db, dual_ts_alpha_threshold_deg,
         constellation, wcg, pfd_mask, es_antenna, alpha0_deg, min_elevation_deg,
         pfd_bw_correction_db, raan_dot_artificial_rad_s, raan_dot_override_rad_s,
         max_co_freq_by_lat, strict_max_co_freq_total, strict_exclusion_zone,
         min_angle_at_es_deg, wdelta_deg, t_run_s, gso_min_elevation_deg,
         keep_full_history, system_id_per_sat, max_co_freq_by_system,
         min_el_all, alpha0_all) = args
    elif len(args) == 27:
        (t_start, t_end,
         dual_ts_mode, dual_ts_fine_s, dual_ts_coarse_s, dual_ts_ncoarse,
         dual_ts_gain_threshold_db, dual_ts_alpha_threshold_deg,
         constellation, wcg, pfd_mask, es_antenna, alpha0_deg, min_elevation_deg,
         pfd_bw_correction_db, raan_dot_artificial_rad_s, raan_dot_override_rad_s,
         max_co_freq_by_lat, strict_max_co_freq_total, strict_exclusion_zone,
         min_angle_at_es_deg, wdelta_deg, t_run_s, gso_min_elevation_deg,
         keep_full_history, system_id_per_sat, max_co_freq_by_system) = args
    elif len(args) == 25:
        (t_start, t_end,
         dual_ts_mode, dual_ts_fine_s, dual_ts_coarse_s, dual_ts_ncoarse,
         dual_ts_gain_threshold_db, dual_ts_alpha_threshold_deg,
         constellation, wcg, pfd_mask, es_antenna, alpha0_deg, min_elevation_deg,
         pfd_bw_correction_db, raan_dot_artificial_rad_s, raan_dot_override_rad_s,
         max_co_freq_by_lat, strict_max_co_freq_total, strict_exclusion_zone,
         min_angle_at_es_deg, wdelta_deg, t_run_s, gso_min_elevation_deg,
         keep_full_history) = args
    else:
        (t_start, t_end,
         dual_ts_mode, dual_ts_fine_s, dual_ts_coarse_s, dual_ts_ncoarse,
         dual_ts_gain_threshold_db, dual_ts_alpha_threshold_deg,
         constellation, wcg, pfd_mask, es_antenna, alpha0_deg, min_elevation_deg,
         pfd_bw_correction_db, raan_dot_artificial_rad_s, raan_dot_override_rad_s,
         max_co_freq_by_lat, strict_max_co_freq_total, strict_exclusion_zone,
         min_angle_at_es_deg, wdelta_deg, t_run_s, gso_min_elevation_deg) = args
        keep_full_history = False

    acc = EPFDStreamAccumulator()
    results: list[EPFDTimeStepResult] | None = [] if keep_full_history else None
    _strict_excl_all = (per_system_extra or {}).get("strict_excl_all")
    _min_angle_by_system = (per_system_extra or {}).get("min_angle_by_system")
    t_s = t_start
    prev_any_critical_gain = True
    step_count = 0

    es_lat = wcg.es_lat_deg
    es_lon = wcg.es_lon_deg
    gso_lon = wcg.gso_lon_deg
    es_ecef = _es_ecef_from_wcg(wcg)
    es_x, es_y, es_z = float(es_ecef[0]), float(es_ecef[1]), float(es_ecef[2])

    lat_r = math.radians(es_lat)
    lon_r = math.radians(es_lon)
    sl, cl = math.sin(lat_r), math.cos(lat_r)
    so, co = math.sin(lon_r), math.cos(lon_r)
    R_enu = np.array([
        [-so,       co,      0.0],
        [-sl * co, -sl * so,  cl],
        [ cl * co,  cl * so,  sl],
    ])
    max_co_freq = _resolve_max_co_freq(es_lat, max_co_freq_by_lat)
    N = len(constellation)

    _prop_cache = build_constellation_cache(
        constellation, raan_dot_override_rad_s=raan_dot_override_rad_s,
    )
    # Constellation-invariant within the chunk — hoisted out of the time loop.
    min_h = _min_operating_height_km_batch(constellation, N)
    sin_min_el_arr = (
        np.sin(np.radians(min_el_all)) if min_el_all is not None else None
    )
    # method_3 per-system decomposition in the SAME pass — mirrors the
    # fixed-step chunk worker. Without this a dual-time-step joint run left
    # ``acc.per_system`` empty, so the worker fell back to
    # ``_decompose_by_resimulation`` (one full-length simulation PER SYSTEM,
    # about as expensive as the joint run itself).
    all_sids = (
        np.unique(system_id_per_sat)
        if (system_id_per_sat is not None and max_co_freq_by_system is not None)
        else None
    )
    _proxy = _DualTSProxy(
        dual_ts_mode, dual_ts_fine_s, dual_ts_coarse_s,
        dual_ts_ncoarse, dual_ts_gain_threshold_db,
    )

    while t_s < t_end - 1e-9:
        remaining_fine = (t_end - t_s) / dual_ts_fine_s

        if dual_ts_mode == "s1503_gain":
            if (
                step_count == 0
                or remaining_fine < dual_ts_ncoarse
                or prev_any_critical_gain
            ):
                dt = dual_ts_fine_s
            else:
                dt = dual_ts_coarse_s
        else:
            dt = dual_ts_fine_s  # alpha_threshold: updated after min_alpha

        gso_ecef = gso_position_ecef(gso_lon, t_s)
        pos_ecef_all, vel_ecef_all = propagate_and_to_ecef_batch(
            constellation, t_s,
            raan_dot_artificial_rad_s=raan_dot_artificial_rad_s,
            raan_dot_override_rad_s=raan_dot_override_rad_s,
            _cache=_prop_cache,
            wdelta_deg=wdelta_deg,
            t_run_s=t_run_s,
        )

        diff_all = pos_ecef_all - es_ecef
        enu_all = (R_enu @ diff_all.T).T
        ranges = np.linalg.norm(diff_all, axis=1)
        sin_el = np.where(ranges > 1e-6, enu_all[:, 2] / ranges, -1.0)
        num_horizon = int(np.count_nonzero(sin_el >= 0.0))

        # S.1503-4 Step 11: horizon (§D.6.4.3); ε₀ applied inside the
        # accumulator for Step 18 bullet ①.
        visible_idx = np.where(sin_el >= 0.0)[0]
        # S.1503-4 Step 18 has no elGSO term; per-instant elGSO hard-exclusion
        # is S.1503-2 emulation only (strict_exclusion_zone). WCGD keeps εGSO.
        if strict_exclusion_zone and _epfd_gso_min_elevation_active(gso_min_elevation_deg):
            if compute_elevation(es_ecef, gso_ecef, es_lat, es_lon) < float(gso_min_elevation_deg):
                visible_idx = np.array([], dtype=np.int64)

        per_system_step: dict[int, list] | None = {} if all_sids is not None else None
        standard_epfd, override_epfd, min_alpha, any_critical_gain = (
            _accumulate_epfd_visible_satellites(
                visible_idx=visible_idx,
                pos_ecef_all=pos_ecef_all,
                vel_ecef_all=vel_ecef_all,
                es_ecef=es_ecef,
                es_x=es_x, es_y=es_y, es_z=es_z,
                es_lat_deg=es_lat,
                es_lon_deg=es_lon,
                gso_ecef=gso_ecef,
                alpha0_deg=alpha0_deg,
                pfd_mask=pfd_mask,
                es_antenna=es_antenna,
                pfd_bw_correction_db=pfd_bw_correction_db,
                max_co_freq=max_co_freq,
                strict_max_co_freq_total=strict_max_co_freq_total,
                subsat_lat_all=None,
                subsat_lon_all=None,
                sat_local_frames=None,
                min_operating_height_km_all=min_h,
                dual_ts=_proxy,
                t_s=t_s,
                strict_exclusion_zone=strict_exclusion_zone,
                min_angle_at_es_deg=min_angle_at_es_deg,
                min_elevation_deg=min_elevation_deg,
                sin_el_full=sin_el,
                system_id_all=system_id_per_sat,
                max_co_freq_by_system=max_co_freq_by_system,
                min_elevation_deg_all=min_el_all,
                alpha0_deg_all=alpha0_all,
                strict_exclusion_zone_all=_strict_excl_all,
                min_angle_at_es_by_system=_min_angle_by_system,
                per_system_out=per_system_step,
            )
        )

        if dual_ts_mode == "alpha_threshold":
            dt = dual_ts_fine_s if min_alpha < dual_ts_alpha_threshold_deg else dual_ts_coarse_s

        # The last step of the chunk may overshoot t_end; the interval
        # [t_end, t_s+dt) belongs to (and is weighted by) the next chunk.
        # Clamp the accumulated duration so chunk boundaries are not
        # double-counted in the time-weighted CCDF.
        dt_weight = min(dt, t_end - t_s)

        epfd_sum_linear = sum(standard_epfd) + sum(override_epfd)
        epfd_agg_db = (
            10.0 * math.log10(epfd_sum_linear) if epfd_sum_linear > 0 else -999.0
        )
        num_visible = int(np.count_nonzero(
            sin_el >= (sin_min_el_arr if sin_min_el_arr is not None
                       else math.sin(math.radians(min_elevation_deg)))
        ))
        num_contributing = len(standard_epfd) + len(override_epfd)
        acc.add(
            time_s=t_s,
            epfd_db=epfd_agg_db,
            duration_s=dt_weight,
            num_horizon_sats=num_horizon,
            num_visible_sats=num_visible,
            num_contributing_sats=num_contributing,
            min_alpha_deg=min_alpha,
            is_fine=(dt <= dual_ts_fine_s + 1e-12),
        )
        if per_system_step is not None:
            # Same clamped duration as the joint add above, so per-system
            # CCDF denominators stay identical to the joint one.
            _acc_add_per_system(
                acc, per_system_step, all_sids, t_s, dt_weight, min_alpha,
                is_fine=(dt <= dual_ts_fine_s + 1e-12),
            )
        if results is not None:
            results.append(EPFDTimeStepResult(
                time_s=t_s,
                epfd_aggregate_dBW=epfd_agg_db,
                num_horizon_sats=num_horizon,
                num_visible_sats=num_visible,
                num_contributing_sats=num_contributing,
                min_alpha_deg=min_alpha,
                duration_s=dt_weight,
            ))

        t_s += dt
        step_count += 1
        prev_any_critical_gain = any_critical_gain

    return {"acc": acc, "ts": results}


def _simulate_chunk_multi_es(args):
    """Simulates a chunk for multiple ES/WCG reusing the same orbital dynamics.

    Returns ``{"acc": [EPFDStreamAccumulator,...], "ts": [list|None,...]}`` per ES.
    """
    if len(args) == 21:
        (start_step, num_steps, t_start, tstep_s, constellation, wcgs,
         pfd_mask, es_antenna, alpha0_deg, min_elevation_deg,
         pfd_bw_correction_db, raan_dot_artificial_rad_s, raan_dot_override_rad_s,
         max_co_freq_by_lat, strict_max_co_freq_total, strict_exclusion_zone,
         min_angle_at_es_deg, wdelta_deg, t_run_s, gso_min_elevation_deg,
         keep_full_history) = args
    else:
        (start_step, num_steps, t_start, tstep_s, constellation, wcgs,
         pfd_mask, es_antenna, alpha0_deg, min_elevation_deg,
         pfd_bw_correction_db, raan_dot_artificial_rad_s, raan_dot_override_rad_s,
         max_co_freq_by_lat, strict_max_co_freq_total, strict_exclusion_zone,
         min_angle_at_es_deg, wdelta_deg, t_run_s, gso_min_elevation_deg) = args
        keep_full_history = False

    n_wcg = len(wcgs)
    accs: list[EPFDStreamAccumulator] = [EPFDStreamAccumulator() for _ in range(n_wcg)]
    results_by_wcg: list[list[EPFDTimeStepResult] | None] = [
        ([] if keep_full_history else None) for _ in range(n_wcg)
    ]
    t_s = t_start

    es_contexts: list[tuple[float, float, float, np.ndarray, np.ndarray, float, float, float, float]] = []
    for wcg in wcgs:
        es_lat = wcg.es_lat_deg
        es_lon = wcg.es_lon_deg
        gso_lon = wcg.gso_lon_deg
        es_ecef = _es_ecef_from_wcg(wcg)

        lat_r = math.radians(es_lat)
        lon_r = math.radians(es_lon)
        sl, cl = math.sin(lat_r), math.cos(lat_r)
        so, co = math.sin(lon_r), math.cos(lon_r)
        R_enu = np.array([
            [-so,        co,       0.0],
            [-sl * co,  -sl * so,  cl ],
            [ cl * co,   cl * so,  sl ],
        ])
        sin_min_el = math.sin(math.radians(min_elevation_deg))
        max_co_freq_es = _resolve_max_co_freq(es_lat, max_co_freq_by_lat)
        es_contexts.append((
            es_lat, es_lon, gso_lon, es_ecef, R_enu, sin_min_el,
            float(es_ecef[0]), float(es_ecef[1]), float(es_ecef[2]),
            max_co_freq_es,
        ))

    _prop_cache = build_constellation_cache(
        constellation, raan_dot_override_rad_s=raan_dot_override_rad_s,
    )
    # Constellation-invariant within the chunk — hoisted out of the time loop.
    min_h_multi = _min_operating_height_km_batch(constellation, len(constellation))

    for _ in range(num_steps):
        pos_ecef_all, vel_ecef_all = propagate_and_to_ecef_batch(
            constellation, t_s,
            raan_dot_artificial_rad_s=raan_dot_artificial_rad_s,
            raan_dot_override_rad_s=raan_dot_override_rad_s,
            _cache=_prop_cache,
            wdelta_deg=wdelta_deg,
            t_run_s=t_run_s,
        )
        # Sub-satellite points in batch (single vectorized ECEF→LLA call).
        subsat_lat_all, subsat_lon_all, _ = ecef_to_lla_batch(pos_ecef_all)
        # Az/El masks are always 3D, so they take the vectorized path (which
        # builds the local frames in batch on demand); the scalar path now only
        # serves 1D masks, which need no frame. Hence no per-sat pre-build.
        sat_local_frames: list[tuple[float, ...] | None] | None = None

        for i, (es_lat, es_lon, gso_lon, es_ecef, R_enu, sin_min_el, es_x, es_y, es_z, max_co_freq) in enumerate(es_contexts):
            gso_ecef = gso_position_ecef(gso_lon, t_s)

            # 2. Batch elevation for this ES
            diff_all = pos_ecef_all - es_ecef          # (N, 3)
            enu_all  = (R_enu @ diff_all.T).T           # (N, 3)
            ranges   = np.linalg.norm(diff_all, axis=1) # (N,)
            sin_el   = np.where(ranges > 1e-6, enu_all[:, 2] / ranges, -1.0)

            num_horizon = int(np.count_nonzero(sin_el >= 0.0))

            # S.1503-4 Step 11: horizon; ε₀ enters in Step 18 ① inside the
            # accumulator (sin_min_el is kept only for reporting).
            visible_idx = np.where(sin_el >= 0.0)[0]
            # S.1503-4 Step 18 has no elGSO term; per-instant elGSO hard-exclusion
            # is S.1503-2 emulation only (strict_exclusion_zone). WCGD keeps εGSO.
            if strict_exclusion_zone and _epfd_gso_min_elevation_active(gso_min_elevation_deg):
                if compute_elevation(es_ecef, gso_ecef, es_lat, es_lon) < float(gso_min_elevation_deg):
                    visible_idx = np.array([], dtype=np.int64)

            num_visible = int(np.count_nonzero(sin_el >= sin_min_el))

            standard_epfd, override_epfd, min_alpha, _ = _accumulate_epfd_visible_satellites(
                visible_idx=visible_idx,
                pos_ecef_all=pos_ecef_all,
                vel_ecef_all=vel_ecef_all,
                es_ecef=es_ecef,
                es_x=es_x,
                es_y=es_y,
                es_z=es_z,
                es_lat_deg=es_lat,
                es_lon_deg=es_lon,
                gso_ecef=gso_ecef,
                alpha0_deg=alpha0_deg,
                pfd_mask=pfd_mask,
                es_antenna=es_antenna,
                pfd_bw_correction_db=pfd_bw_correction_db,
                max_co_freq=max_co_freq,
                strict_max_co_freq_total=strict_max_co_freq_total,
                subsat_lat_all=subsat_lat_all,
                subsat_lon_all=subsat_lon_all,
                sat_local_frames=sat_local_frames,
                min_operating_height_km_all=min_h_multi,
                dual_ts=None,
                t_s=t_s,
                strict_exclusion_zone=strict_exclusion_zone,
                min_angle_at_es_deg=min_angle_at_es_deg,
                min_elevation_deg=min_elevation_deg,
                sin_el_full=sin_el,
            )

            epfd_sum_linear = sum(standard_epfd) + sum(override_epfd)
            num_contributing = len(standard_epfd) + len(override_epfd)

            epfd_agg_db = (
                10.0 * math.log10(epfd_sum_linear) if epfd_sum_linear > 0 else -999.0
            )
            accs[i].add(
                time_s=t_s,
                epfd_db=epfd_agg_db,
                duration_s=tstep_s,
                num_horizon_sats=num_horizon,
                num_visible_sats=num_visible,
                num_contributing_sats=num_contributing,
                min_alpha_deg=min_alpha,
                is_fine=True,  # fixed step (multi-ES): uniform Δt
            )
            if results_by_wcg[i] is not None:
                results_by_wcg[i].append(EPFDTimeStepResult(
                    time_s=t_s,
                    epfd_aggregate_dBW=epfd_agg_db,
                    num_horizon_sats=num_horizon,
                    num_visible_sats=num_visible,
                    num_contributing_sats=num_contributing,
                    min_alpha_deg=min_alpha,
                    duration_s=tstep_s,
                ))

        t_s += tstep_s

    return {"acc": accs, "ts": results_by_wcg}


def run_epfd_simulation(
    constellation: list[OrbitalElements],
    wcg: WCGResult,
    pfd_mask: PFDMask,
    es_antenna: EarthStationAntenna,
    alpha0_deg: float,
    min_elevation_deg: float,
    tstep_s: float,
    nsteps: int,
    dual_ts: DualTimeStep | None = None,
    n_jobs: int = -1,  # -1 = auto
    pfd_bw_correction_db: float = 0.0,
    raan_dot_artificial_rad_s: float = 0.0,
    raan_dot_override_rad_s: float | None = None,
    max_co_freq_by_lat: list | None = None,
    strict_max_co_freq_total: bool = False,
    strict_exclusion_zone: bool = False,
    min_angle_at_es_deg: float = 0.0,
    wdelta_deg: float = 0.0,
    t_run_s: float = 0.0,
    gso_min_elevation_deg: float = -90.0,
    keep_full_history: bool = False,
    system_id_per_sat: np.ndarray | None = None,
    max_co_freq_by_lat_per_system: list | None = None,
    min_elevation_deg_per_system: list | None = None,
    alpha0_deg_per_system: list | None = None,
    strict_exclusion_zone_per_system: list | None = None,
    min_angle_at_es_deg_per_system: list | None = None,
    on_chunk: "Callable[[EPFDStreamAccumulator, int, int], None] | None" = None,
) -> EPFDSimulationResult:
    """Runs the complete EPFD↓ time simulation.

    Supports parallelism if dual_ts is None (fixed step).

    ``keep_full_history``: when True, retains the full
    ``EPFDTimeStepResult`` vector per step (RAM cost ~300 B/step). For
    simulations with >~1e6 steps, leave it **False** — the streaming accumulator
    preserves the exact CCDF (0.1 dB bins S.1503-4 D7.1.3), aggregates and a
    decimated trace for the panel, in <1 MB regardless of N.

    ``on_chunk(acc, steps_done, steps_total)``: called after each chunk is
    merged, with the **partially merged** accumulator. Lets a caller persist
    intermediate results so a run cancelled hours in still leaves a usable
    CCDF / time series on disk. The engine stays storage-agnostic — what to
    write is entirely the callback's business. Callback exceptions are
    swallowed: persistence must never kill a simulation. Fires on the
    sequential path too (on its progress heartbeat), so both modes behave the
    same from the caller's side.
    """
    result = EPFDSimulationResult(wcg=wcg, keep_full_history=keep_full_history)

    # GSO ES position (ECEF, fixed on the surface)
    es_ecef = _es_ecef_from_wcg(wcg)
    es_x, es_y, es_z = float(es_ecef[0]), float(es_ecef[1]), float(es_ecef[2])

    # Check whether the ES is in a valid position
    if np.linalg.norm(es_ecef) < RE_KM * 0.9:
        logger.warning("ES position invalid, aborting simulation.")
        return result

    # Multi-system aggregation (joint/method_3): resolve MAX_CO_FREQ per system
    # at the ES latitude (constant for the run). `system_id_per_sat` labels each
    # satellite of the merged megaconstellation. Enables per-system Steps 20–21
    # selection in `_finalize_epfd_after_max_co_freq` (intra-system parameter, does not couple).
    max_co_freq_by_system: dict[int, int] | None = None
    if system_id_per_sat is not None and max_co_freq_by_lat_per_system is not None:
        max_co_freq_by_system = {
            int(sid): _resolve_max_co_freq(wcg.es_lat_deg, tbl or [])
            for sid, tbl in enumerate(max_co_freq_by_lat_per_system)
        }

    # Per-system ε₀/α₀ (method_3 joint runs), expanded to per-satellite arrays
    # via system_id_per_sat: each system's own Step-18 thresholds gate its own
    # satellites — one shared scalar would let a stricter system's satellites
    # transmit below their declared ε₀ (or against the wrong exclusion-zone
    # half-angle). The scalar alpha0_deg / min_elevation_deg args then serve
    # only callers without a per-system list (all single-entry paths). Lists
    # are indexed by system id (original cfg order, same convention as
    # max_co_freq_by_lat_per_system).
    min_el_all: np.ndarray | None = None
    alpha0_all: np.ndarray | None = None
    strict_excl_all: np.ndarray | None = None
    min_angle_by_system: dict[int, float] | None = None
    if system_id_per_sat is not None:
        _sid_arr = np.asarray(system_id_per_sat, dtype=np.int64)
        if min_elevation_deg_per_system is not None:
            min_el_all = np.asarray(
                min_elevation_deg_per_system, dtype=np.float64
            )[_sid_arr]
        if alpha0_deg_per_system is not None:
            alpha0_all = np.asarray(
                alpha0_deg_per_system, dtype=np.float64
            )[_sid_arr]
        # Step 18 bullet ② on/off per filing, expanded per satellite.
        if strict_exclusion_zone_per_system is not None:
            strict_excl_all = np.asarray(
                strict_exclusion_zone_per_system, dtype=bool
            )[_sid_arr]
        # Step 21 minimum separation angle at the ES, keyed by system id. Kept
        # as a dict (not a per-sat array) because Steps 20-21 already select
        # system by system, and the angle governs that selection, not a gate.
        if min_angle_at_es_deg_per_system is not None:
            min_angle_by_system = {
                int(sid): float(v)
                for sid, v in enumerate(min_angle_at_es_deg_per_system)
            }

    # --- SEQUENTIAL MODE (n_jobs=1 or no dual_ts when n_jobs=1) ---
    if n_jobs == 1:
            
        t_s = 0.0
        step_count = 0
        fine_steps_elapsed = 0.0
        prev_any_critical_gain = True
        total_sats = len(constellation)
        progress_every = max(1, nsteps // 40)  # ~2.5%
        progress_tick = -1
        last_progress_wall = time.monotonic()
        progress_heartbeat_s = 2.0
    
        _dual_tag = (
            f", dual time step ({dual_ts.mode}): fine={dual_ts.fine:.4f}s, coarse={dual_ts.coarse:.4f}s, Ncoarse={dual_ts.ncoarse}"
            if dual_ts is not None else ", fixed time step"
        )
        logger.info(
            f"Starting EPFD↓ simulation (Sequential): {nsteps} steps, {total_sats} satellites{_dual_tag}"
        )

        # Precompute the ENU matrix and sin(ε_min) for vectorized elevation filtering
        lat_r = math.radians(wcg.es_lat_deg)
        lon_r = math.radians(wcg.es_lon_deg)
        sl, cl = math.sin(lat_r), math.cos(lat_r)
        so, co = math.sin(lon_r), math.cos(lon_r)
        R_enu_seq = np.array([
            [-so,       co,      0.0],
            [-sl * co, -sl * so,  cl ],
            [ cl * co,  cl * so,  sl ],
        ])
        sin_min_el_seq = math.sin(math.radians(min_elevation_deg))
        sin_min_el_all_seq = (
            np.sin(np.radians(min_el_all)) if min_el_all is not None else None
        )
        max_co_freq_seq = _resolve_max_co_freq(wcg.es_lat_deg, max_co_freq_by_lat or [])

        _prop_cache_seq = build_constellation_cache(
            constellation, raan_dot_override_rad_s=raan_dot_override_rad_s,
        )
        # Constellation-invariant within the run — hoisted out of the time loop.
        min_h_seq = _min_operating_height_km_batch(constellation, total_sats)
        # method_3 per-system decomposition in the SAME pass (see
        # _acc_add_per_system); None outside a multi-system joint run.
        all_sids_seq = (
            np.unique(system_id_per_sat)
            if (system_id_per_sat is not None and max_co_freq_by_system is not None)
            else None
        )

        while fine_steps_elapsed < nsteps - 1e-9:
            if dual_ts is None:
                dt = tstep_s
            elif dual_ts.mode == "s1503_gain":
                remaining_fine_steps = nsteps - fine_steps_elapsed
                dt = dual_ts.select_step_s1503(
                    is_first_step=(step_count == 0),
                    remaining_fine_steps=remaining_fine_steps,
                    prev_any_critical_gain=prev_any_critical_gain,
                )
            else:
                dt = tstep_s

            gso_ecef = gso_position_ecef(wcg.gso_lon_deg, t_s)

            pos_ecef_all, vel_ecef_all = propagate_and_to_ecef_batch(
                constellation, t_s,
                raan_dot_artificial_rad_s=raan_dot_artificial_rad_s,
                raan_dot_override_rad_s=raan_dot_override_rad_s,
                _cache=_prop_cache_seq,
                wdelta_deg=wdelta_deg,
                t_run_s=t_run_s,
            )

            # Batch elevation
            diff_all = pos_ecef_all - es_ecef
            enu_all  = (R_enu_seq @ diff_all.T).T
            ranges   = np.linalg.norm(diff_all, axis=1)
            sin_el   = np.where(ranges > 1e-6, enu_all[:, 2] / ranges, -1.0)
            num_horizon = int(np.count_nonzero(sin_el >= 0.0))

            # S.1503-4 Step 11: horizon; ε₀ applied inside the accumulator.
            visible_idx = np.where(sin_el >= 0.0)[0]
            # S.1503-4 Step 18 has no elGSO term; per-instant elGSO hard-exclusion
            # is S.1503-2 emulation only (strict_exclusion_zone). WCGD keeps εGSO.
            if strict_exclusion_zone and _epfd_gso_min_elevation_active(gso_min_elevation_deg):
                if compute_elevation(es_ecef, gso_ecef, wcg.es_lat_deg, wcg.es_lon_deg) < float(
                    gso_min_elevation_deg
                ):
                    visible_idx = np.array([], dtype=np.int64)

            num_visible = int(np.count_nonzero(
                sin_el >= (sin_min_el_all_seq if sin_min_el_all_seq is not None
                           else sin_min_el_seq)
            ))

            per_system_step: dict[int, list] | None = (
                {} if all_sids_seq is not None else None
            )
            standard_epfd, override_epfd, min_alpha, any_critical_gain = (
                _accumulate_epfd_visible_satellites(
                    visible_idx=visible_idx,
                    pos_ecef_all=pos_ecef_all,
                    vel_ecef_all=vel_ecef_all,
                    es_ecef=es_ecef,
                    es_x=es_x,
                    es_y=es_y,
                    es_z=es_z,
                    es_lat_deg=wcg.es_lat_deg,
                    es_lon_deg=wcg.es_lon_deg,
                    gso_ecef=gso_ecef,
                    alpha0_deg=alpha0_deg,
                    pfd_mask=pfd_mask,
                    es_antenna=es_antenna,
                    pfd_bw_correction_db=pfd_bw_correction_db,
                    max_co_freq=max_co_freq_seq,
                    strict_max_co_freq_total=strict_max_co_freq_total,
                    subsat_lat_all=None,
                    subsat_lon_all=None,
                    sat_local_frames=None,
                    min_operating_height_km_all=min_h_seq,
                    dual_ts=dual_ts,
                    t_s=t_s,
                    strict_exclusion_zone=strict_exclusion_zone,
                    min_angle_at_es_deg=min_angle_at_es_deg,
                    min_elevation_deg=min_elevation_deg,
                    sin_el_full=sin_el,
                    system_id_all=system_id_per_sat,
                    max_co_freq_by_system=max_co_freq_by_system,
                    min_elevation_deg_all=min_el_all,
                    alpha0_deg_all=alpha0_all,
                    strict_exclusion_zone_all=strict_excl_all,
                    min_angle_at_es_by_system=min_angle_by_system,
                    per_system_out=per_system_step,
                )
            )

            epfd_sum_linear = sum(standard_epfd) + sum(override_epfd)
            num_contributing = len(standard_epfd) + len(override_epfd)

            epfd_aggregate_dBW = (
                10.0 * math.log10(epfd_sum_linear) if epfd_sum_linear > 0 else -999.0
            )
            if dual_ts is not None and dual_ts.mode == "alpha_threshold":
                dt = dual_ts.get_step(min_alpha)
            _is_fine = dual_ts is None or dt <= dual_ts.fine + 1e-12
            result.acc.add(
                time_s=t_s,
                epfd_db=epfd_aggregate_dBW,
                duration_s=dt,
                num_horizon_sats=num_horizon,
                num_visible_sats=num_visible,
                num_contributing_sats=num_contributing,
                min_alpha_deg=min_alpha,
                is_fine=_is_fine,
            )
            if per_system_step is not None:
                _acc_add_per_system(
                    result.acc, per_system_step, all_sids_seq, t_s, dt, min_alpha,
                    is_fine=_is_fine,
                )
            if keep_full_history:
                result.time_steps.append(EPFDTimeStepResult(
                    time_s=t_s, epfd_aggregate_dBW=epfd_aggregate_dBW,
                    num_horizon_sats=num_horizon,
                    num_visible_sats=num_visible, num_contributing_sats=num_contributing,
                    min_alpha_deg=min_alpha, duration_s=dt,
                ))

            t_s += dt
            if dual_ts is not None:
                fine_steps_elapsed += dt / dual_ts.fine
            else:
                fine_steps_elapsed += 1.0
            step_count += 1
            prev_any_critical_gain = any_critical_gain

            progress_value = int(min(nsteps, math.floor(fine_steps_elapsed)))
            now_wall = time.monotonic()
            should_log_tick = (progress_value // progress_every) > progress_tick
            should_log_heartbeat = (now_wall - last_progress_wall) >= progress_heartbeat_s
            is_last = fine_steps_elapsed >= nsteps - 1e-9
            if should_log_tick or should_log_heartbeat or is_last:
                progress_tick = progress_value // progress_every
                last_progress_wall = now_wall
                pct = min(100.0, (fine_steps_elapsed / max(1.0, float(nsteps))) * 100.0)
                bar = _progress_bar(pct)
                logger.info(
                    f"  {bar} {pct:6.2f}%  "
                    f"step {step_count}/{nsteps}  t={t_s:.1f}s  EPFD={epfd_aggregate_dBW:.1f}"
                )
                if on_chunk is not None:
                    result.acc.finalize_decimated()
                    try:
                        on_chunk(result.acc, step_count, nsteps)
                    except Exception:  # noqa: BLE001 — persistence is best-effort
                        logger.debug("on_chunk callback failed", exc_info=True)

    # --- PARALLEL MODE WITH DUAL TIME STEP ---
    elif dual_ts is not None:
        import multiprocessing
        if n_jobs < 1:
            n_jobs = multiprocessing.cpu_count()
        numba_thr = _compute_epfd_numba_threads(n_jobs)

        T_total = nsteps * dual_ts.fine  # total real T (exact: Σ dt_i = nsteps * fine)
        chunks_per_core = 16
        max_chunks_cap = 256
        target_chunks = min(max_chunks_cap, max(n_jobs * chunks_per_core, n_jobs))
        chunk_dur = T_total / max(1, target_chunks)
        gain_thr = dual_ts._gain_threshold_db

        chunks_dts = []
        t_c = 0.0
        while t_c < T_total - 1e-9:
            t_end_c = min(t_c + chunk_dur, T_total)
            chunks_dts.append((
                t_c, t_end_c,
                dual_ts.mode, dual_ts.fine, dual_ts.coarse, dual_ts.ncoarse,
                gain_thr, dual_ts.threshold,
                constellation, wcg, pfd_mask, es_antenna,
                alpha0_deg, min_elevation_deg, pfd_bw_correction_db,
                raan_dot_artificial_rad_s, raan_dot_override_rad_s,
                max_co_freq_by_lat or [], strict_max_co_freq_total,
                strict_exclusion_zone, min_angle_at_es_deg,
                wdelta_deg, t_run_s, gso_min_elevation_deg,
                keep_full_history,
                system_id_per_sat, max_co_freq_by_system,
                min_el_all, alpha0_all,
                {"strict_excl_all": strict_excl_all,
                 "min_angle_by_system": min_angle_by_system},
            ))
            t_c = t_end_c

        logger.info(
            f"Starting EPFD↓ simulation (Parallel DualTS): {nsteps} fine-steps, "
            f"T={T_total:.1f}s, {len(chunks_dts)} chunks, {n_jobs} procs"
        )

        done_fine_eq = 0.0
        total_chunks = len(chunks_dts)
        chunk_idx = 0

        def _consume_dual(batch_res):
            nonlocal done_fine_eq, chunk_idx
            chunk_idx += 1
            batch_acc = batch_res["acc"]
            done_fine_eq += float(batch_acc.total_duration_s) / dual_ts.fine
            result.acc.merge(batch_acc)
            if keep_full_history and batch_res["ts"]:
                result.time_steps.extend(batch_res["ts"])
            pct = min(100.0, done_fine_eq / max(1, nsteps) * 100.0)
            step_eq = int(round(done_fine_eq))
            bar = _progress_bar(pct)
            logger.info(
                f"  {bar} {pct:6.2f}%  "
                f"step {step_eq}/{nsteps}  chunk {chunk_idx}/{total_chunks}"
            )
            if on_chunk is not None:
                result.acc.finalize_decimated()
                try:
                    on_chunk(result.acc, step_eq, nsteps)
                except Exception:  # noqa: BLE001 — persistence is best-effort
                    logger.debug("on_chunk callback failed", exc_info=True)

        if _EPFD_EXECUTOR is not None:
            logger.info(
                "  EPFD dispatch via injected executor (%d dual-ts chunks)", total_chunks
            )
            for batch_res in _EPFD_EXECUTOR(_simulate_chunk_dual_ts, chunks_dts, _epfd_executor_init()):
                _consume_dual(batch_res)
        else:
            with multiprocessing.Pool(
                processes=n_jobs,
                initializer=_epfd_pool_initializer,
                initargs=(_epfd_global_snapshot(numba_thr),),
            ) as pool:
                for batch_res in pool.imap(_simulate_chunk_dual_ts, chunks_dts):
                    _consume_dual(batch_res)

        result.acc.finalize_decimated()
        if keep_full_history:
            result.time_steps.sort(key=lambda s: s.time_s)
        t_s = T_total
        step_count = nsteps

    # --- PARALLEL MODE (Fixed Step) ---
    else:
        import multiprocessing
        if n_jobs < 1: n_jobs = multiprocessing.cpu_count()
        numba_thr = _compute_epfd_numba_threads(n_jobs)

        logger.info(
            f"Starting EPFD↓ simulation (Parallel): {nsteps} steps, "
            f"{n_jobs} procs, {numba_thr} numba_threads/proc"
        )
        
        chunks_per_core = 48
        max_chunks_cap = 512
        min_chunk_steps = 1000
        target_chunks = min(max_chunks_cap, max(n_jobs * chunks_per_core, n_jobs))
        steps_per_job = max(min_chunk_steps, int(math.ceil(nsteps / max(1, target_chunks))))
        chunks = []
        current_step = 0
        current_t = 0.0
        
        while current_step < nsteps:
            count = min(steps_per_job, nsteps - current_step)
            chunks.append((
                current_step, count, current_t, tstep_s,
                constellation, wcg, pfd_mask, es_antenna,
                alpha0_deg, min_elevation_deg, pfd_bw_correction_db,
                raan_dot_artificial_rad_s, raan_dot_override_rad_s,
                max_co_freq_by_lat or [], strict_max_co_freq_total,
                strict_exclusion_zone,
                min_angle_at_es_deg,
                wdelta_deg, t_run_s,
                gso_min_elevation_deg,
                keep_full_history,
                system_id_per_sat, max_co_freq_by_system,
                min_el_all, alpha0_all,
                {"strict_excl_all": strict_excl_all,
                 "min_angle_by_system": min_angle_by_system},
            ))
            current_step += count
            current_t += count * tstep_s
        logger.info(
            f"  Parallel: chunk_size={steps_per_job} steps, total_chunks={len(chunks)}"
        )

        done_steps = 0
        total_chunks = len(chunks)
        chunk_idx = 0

        def _consume_fixed(batch_res):
            nonlocal done_steps, chunk_idx
            chunk_idx += 1
            batch_acc = batch_res["acc"]
            done_steps += int(batch_acc.n_steps)
            result.acc.merge(batch_acc)
            if keep_full_history and batch_res["ts"]:
                result.time_steps.extend(batch_res["ts"])
            pct = done_steps / max(1, nsteps) * 100.0
            bar = _progress_bar(pct)
            logger.info(
                f"  {bar} {pct:6.2f}%  "
                f"step {done_steps}/{nsteps}  "
                f"chunk {chunk_idx}/{total_chunks}"
            )
            if on_chunk is not None:
                # Chunks arrive in order on the local Pool (imap), but not under
                # an injected cluster executor — sort the partial trace so the
                # snapshot is time-ordered either way (no-op when already is).
                result.acc.finalize_decimated()
                try:
                    on_chunk(result.acc, done_steps, nsteps)
                except Exception:  # noqa: BLE001 — persistence is best-effort
                    logger.debug("on_chunk callback failed", exc_info=True)

        if _EPFD_EXECUTOR is not None:
            logger.info(
                "  EPFD dispatch via injected executor (%d chunks)", total_chunks
            )
            for batch_res in _EPFD_EXECUTOR(_simulate_chunk, chunks, _epfd_executor_init()):
                _consume_fixed(batch_res)
        else:
            with multiprocessing.Pool(
                processes=n_jobs,
                initializer=_epfd_pool_initializer,
                initargs=(_epfd_global_snapshot(numba_thr),),
            ) as pool:
                for batch_res in pool.imap(_simulate_chunk, chunks):
                    _consume_fixed(batch_res)

        result.acc.finalize_decimated()
        if keep_full_history:
            result.time_steps.sort(key=lambda s: s.time_s)

        # Update the final time for logging
        t_s = tstep_s * nsteps
        step_count = nsteps

    # Sequential finalizes the decimated trace (parallel chunks already sort above).
    if n_jobs == 1:
        result.acc.finalize_decimated()

    # Build the CDF
    result.build_cdf()

    logger.info(
        f"Simulation complete: {step_count} steps, "
        f"duration={t_s:.1f}s ({t_s/3600:.2f}h)"
    )

    # Dual time step diagnostic: ratio of real evaluations / fine-eq.
    # Useful to verify whether coarse steps are actually being exploited
    # (S.1503-4 D4.7.1). In zones without critical gain, dt=Tcoarse and each
    # iteration consumes Ncoarse fine-eq → ratio ~ 1/Ncoarse.
    actual_evals = int(getattr(result.acc, "n_steps", 0))
    if dual_ts is not None and actual_evals > 0 and nsteps > 0:
        nc = max(1, int(getattr(dual_ts, "ncoarse", 1)))
        fine_evals_lower = max(1, int(nsteps // nc))
        ratio_used = actual_evals / float(nsteps)
        savings_pct = (1.0 - ratio_used) * 100.0
        avg_step_s = (t_s / actual_evals) if actual_evals > 0 else 0.0
        avg_eq = avg_step_s / max(dual_ts.fine, 1e-12)
        logger.info(
            "  Dual time step: real evaluations=%d, fine-eq=%d, "
            "Ncoarse=%d, ratio=%.4f (savings ~%.1f%%), "
            "mean Δt=%.4fs (= %.2f × Tfine; fine-only bound=%.4fs)",
            actual_evals, int(nsteps), nc, ratio_used, savings_pct,
            avg_step_s, avg_eq, float(dual_ts.fine),
        )
        if avg_eq < 1.01:
            logger.info(
                "  Dual time step: coarse step ALMOST NEVER triggered — "
                "check prev_any_critical_gain / disable_or_condition / "
                "es_antenna.relative_gain(α₀)."
            )

    return result


# ─────────────────────────────────────────────────────────────────────────────
#  Track-duration variant — S.1503-4 §D5.1.4.2 (sliding windows)
# ─────────────────────────────────────────────────────────────────────────────

def _process_closed_window(
    buffer: list,
    max_co_freq: int,
    stats_end_step: int,
    t_fine_s: float,
    win_stats: EPFDWindowStats,
    or_rescues_capped: bool = True,
) -> None:
    """Aggregate one closed sliding window into ``win_stats`` (§D5.1.4.2 Steps 19–22).

    ``buffer`` holds, for each of the ``N_SW`` steps of the window (in order), a
    tuple ``(global_step, idx, epfd_lin, std, orx)`` produced by the collect mode
    of :func:`_accumulate_epfd_visible_satellites` (``idx`` global sat indices;
    ``epfd_lin`` per-sat epfd↓ᵢ linear; ``std``/``orx`` the two Step-18 flags).

    Steps: (19) a satellite is *window-eligible* only if it satisfied the α₀/ε₀
    standard condition at **every** step of the window; (19bis) eligible sats are
    ranked by their peak epfd↓ over the window; (20) the top ``MAX_CO_FREQ`` of
    them form the fixed tracked set ``S``; then for each step the aggregate is
    ``Σ_{s∈S} epfd_s + Σ_{orx at step, s∉S} epfd_s`` (no double-counting),
    incremented into the window-set statistics for steps within the run duration.
    """
    n_sw = len(buffer)
    if n_sw == 0:
        return

    # Step 19: intersection of standard-satellites across ALL steps.
    eligible: set[int] | None = None
    peak: dict[int, float] = {}
    for _g, idx, epfd_lin, std, _orx in buffer:
        std_ids = set(int(idx[j]) for j in range(idx.size) if std[j])
        eligible = std_ids if eligible is None else (eligible & std_ids)
        # Track window-peak epfd for every recorded satellite (for ranking).
        for j in range(idx.size):
            k = int(idx[j])
            v = float(epfd_lin[j])
            if v > peak.get(k, -1.0):
                peak[k] = v
    if eligible is None:
        eligible = set()

    # Step 19bis + 20: rank eligible by peak epfd↓, keep the top MAX_CO_FREQ.
    # The Recommendation states no tie-break (§D5.1.4.2 Step 19bis reads only
    # "sort this list of satellites by maximum epfd↓[nSat] per satellite", and
    # does not even state the sort direction). Sorting a ``set[int]`` with a
    # single key left ties to Python's hash-table iteration order, so the result
    # depended on satellite *labelling*: the same physics with a reindexed
    # constellation kept a different satellite under the MAX_CO_FREQ cap. Exact
    # ties are common in practice — pfd-mask edge clamping and the antenna gain
    # floor produce identical window peaks (57 of 126 windows in one fixture).
    # The total key (−peak, index) makes the choice deterministic and
    # label-stable; it is a documented project convention, not a Rec. rule.
    ranked = sorted(eligible, key=lambda k: (-peak.get(k, 0.0), k))
    if max_co_freq and max_co_freq > 0:
        selected = set(ranked[:max_co_freq])
    else:
        selected = set(ranked)  # 0 ⇒ unlimited
    win_stats.note_window(
        len(eligible),
        bool(max_co_freq and max_co_freq > 0 and len(eligible) > max_co_freq),
    )

    # Steps 21–22: per-step aggregate over the fixed tracked set plus OR sats.
    # OR contributors are satellites NOT among the window-eligible/standard set
    # (Step 22 mirrors §D5.1.4.1 bullet ②: OR is the *alternative* to the α₀/ε₀
    # standard path). A window-eligible satellite dropped by the MAX_CO_FREQ cap
    # is genuinely dropped — it must not re-enter through the OR branch, else it
    # would defeat the cap. Selected sats count once (no double counting).
    # Printed Step 20 + Note (p. 100): the gain branch carries no qualifier and
    # the Note removes from it only satellites already on the MAX_CO_FREQ list,
    # so the block set is the TRACKED set. ``or_rescues_capped=False`` selects
    # the other reading (block every window-eligible satellite), which the text
    # does not support and which lowers epfd; study switch only.
    or_blocked = selected if or_rescues_capped else eligible
    for g, idx, epfd_lin, _std, orx in buffer:
        if g >= stats_end_step:
            # Window completes for eligibility, but this step is past the run
            # duration → excluded from statistics (§D5.1.4.2 Step 22 tail).
            continue
        agg = 0.0
        for j in range(idx.size):
            k = int(idx[j])
            if k in selected or (orx[j] and k not in or_blocked):
                agg += float(epfd_lin[j])
        epfd_db = 10.0 * math.log10(agg) if agg > 0.0 else -999.0
        win_stats.add(time_s=g * t_fine_s, epfd_db=epfd_db, duration_s=t_fine_s)


# A chunk must carry at least this many times its own halo of real work.
_HALO_WORK_RATIO = 4


def _window_aggregate_dense(
    E: np.ndarray,
    S: np.ndarray,
    O: np.ndarray,
    max_co_freq: int,
    row_order: list | None = None,
    active: np.ndarray | None = None,
    eligible: np.ndarray | None = None,
    stats_out: dict | None = None,
    or_rescues_capped: bool = True,
) -> np.ndarray:
    """Vectorized §D5.1.4.2 Steps 19, 19bis, 20 and 21 for one closed window.

    ``E``/``S``/``O`` are the dense ``[N_SW × N_sat]`` ring arrays holding the
    whole window: linear epfd↓ᵢ, the Step-18 α₀/ε₀ flag and the Step-18 gain
    (OR) flag, zero/False where a satellite did not contribute at that step.

    Steps 19 and 19bis are reductions down the whole window and are order-free,
    so they run over the ring arrays as stored. Step 21 is per step, so the
    returned aggregate is assembled in chronological order following
    ``row_order`` — a list of ``(start, stop)`` ring-row ranges, oldest first
    (default: the array as given).

    Semantics match :func:`_process_closed_window` exactly, including the
    project's reading of Step 20 in which the gain-condition set excludes
    window-eligible satellites, and the deterministic ``(−peak, index)``
    tie-break of Step 19bis.
    """
    if eligible is None:
        elig = S.all(axis=0)                                  # Step 19
        cand = np.flatnonzero(elig)
    else:
        # The ring maintains the Step-19 tally incrementally, so eligibility
        # costs nothing here instead of scanning an N_SW × N_sat array that is
        # ~99% zeros for a real constellation.
        cand = np.asarray(eligible, dtype=np.int64)
        elig = np.zeros(E.shape[1], dtype=bool)
        elig[cand] = True
    n_elig = int(cand.size)
    capped = bool(max_co_freq and max_co_freq > 0 and n_elig > max_co_freq)
    if stats_out is not None:
        stats_out["n_elig"] = n_elig
        stats_out["capped"] = capped
    sel_mask = np.zeros(E.shape[1], dtype=bool)
    if n_elig:
        if capped:
            # Step 19bis: rank by window-peak epfd↓. An eligible satellite is
            # recorded at every step of the window, so a plain column max over
            # the dense view equals the max over its recorded steps.
            peak = E[:, cand].max(axis=0)
            # (−peak, index): the same total key the scalar path uses. Here it
            # is belt-and-braces — ``cand`` comes from ``flatnonzero`` in
            # ascending order and a stable sort would already break ties the
            # same way — but stating it explicitly keeps the two paths provably
            # aligned if either sort ever changes.
            order = np.lexsort((cand, -peak))                 # (−peak, index)
            sel = cand[order[:max_co_freq]]
        else:
            sel = cand                                        # 0 ⇒ unlimited
        sel_mask[sel] = True

    # Steps 20–21: the fixed tracked set plus the gain-condition satellites that
    # are not window-eligible, each counted once.
    # Reduce only over satellites that appear somewhere in the window. For a
    # real filing that is a few dozen columns out of thousands.
    cols = (np.arange(E.shape[1]) if active is None
            else np.asarray(active, dtype=np.int64))
    if cols.size == 0:
        n = E.shape[0] if row_order is None else sum(b - a for a, b in row_order)
        return np.zeros(n, dtype=E.dtype)
    sel_c = sel_mask[cols]
    # Step 20 + Note, printed p. 100. The gain branch is written WITHOUT any
    # qualification — "plus those satellites for which GRX(φ) > min[Gmax − 30 dB,
    # GRX(α₀[Latitude])]" — and the Note removes from it only the satellites
    # that are already counted: "if a satellite is on the list of the highest
    # MAX_CO_FREQ[lat] satellites and also for which GRX(φ) > min[…] it should
    # be included only once as part of the MAX_CO_FREQ[lat] satellites".
    # So the gate is "not in the tracked set" (``or_rescues_capped=True``, the
    # conformant default). The alternative gate, "not window-eligible", also
    # bars eligible satellites that Step 19bis dropped under the cap, which the
    # printed text does not say and which lowers epfd; it is kept only as a
    # study switch.
    gate_c = (~sel_mask[cols]) if or_rescues_capped else ~elig[cols]
    if row_order is None:
        row_order = [(0, E.shape[0])]
    parts = []
    for a, b in row_order:
        Ec = E[a:b][:, cols]
        contrib = sel_c[None, :] | (O[a:b][:, cols] & gate_c[None, :])
        parts.append((Ec * contrib).sum(axis=1))
    return parts[0] if len(parts) == 1 else np.concatenate(parts)


class _WindowRing:
    """Dense ring buffer holding the last ``N_SW`` fine steps.

    Every sliding window still open at step ``g`` started less than ``N_SW``
    steps earlier — family ``w``'s current window starts at
    ``w·N_MSL + N_SW·⌊(g − w·N_MSL)/N_SW⌋`` — so one buffer of depth ``N_SW``
    serves all ``N_TW`` families at once. That is what makes the single global
    pass of §D5.1.3 possible.
    """

    __slots__ = ("n_sw", "E", "S", "O", "head", "_idx", "n_seen", "n_std")

    def __init__(self, n_sw: int, n_sat: int):
        self.n_sw = int(n_sw)
        self.E = np.zeros((self.n_sw, n_sat), dtype=np.float64)
        self.S = np.zeros((self.n_sw, n_sat), dtype=bool)
        self.O = np.zeros((self.n_sw, n_sat), dtype=bool)
        self.head = 0
        # Per-satellite tallies over the rows currently resident, maintained in
        # O(#recorded) per push. They turn the two window reductions from
        # O(N_SW · N_sat) over a ~99% empty array into O(N_SW · n_active):
        # ``n_std == n_sw`` is Step 19 eligibility without scanning, and
        # ``n_seen > 0`` is the small column set worth reducing at all.
        self._idx: list[np.ndarray] = [np.empty(0, dtype=np.int64)] * self.n_sw
        self.n_seen = np.zeros(n_sat, dtype=np.int32)
        self.n_std = np.zeros(n_sat, dtype=np.int32)

    def push(self, idx: np.ndarray, epfd_lin: np.ndarray,
             std: np.ndarray, orx: np.ndarray) -> None:
        r = self.head % self.n_sw
        old = self._idx[r]
        if old.size:                      # evict the row this one overwrites
            self.n_seen[old] -= 1
            np.subtract.at(self.n_std, old[self.S[r, old]], 1)
            self.E[r, old] = 0.0
            self.S[r, old] = False
            self.O[r, old] = False
        if idx.size:
            self.E[r, idx] = epfd_lin
            self.S[r, idx] = std
            self.O[r, idx] = orx
            self.n_seen[idx] += 1
            np.add.at(self.n_std, idx[std], 1)
        self._idx[r] = idx
        self.head += 1

    def active(self) -> np.ndarray:
        """Satellites recorded at least once in the resident window."""
        return np.flatnonzero(self.n_seen)

    def eligible(self) -> np.ndarray:
        """Step 19: standard at EVERY resident row, without scanning the ring."""
        return np.flatnonzero(self.n_std == self.n_sw)

    def slices(self) -> list[tuple[int, int]]:
        """Ring-row ranges spanning the window that ends at the last push,
        ordered oldest-first as ``[(start, stop), ...]``."""
        r = self.head % self.n_sw
        if r == 0:
            return [(0, self.n_sw)]
        return [(r, self.n_sw), (0, r)]


def _close_table(n_sw: int, n_msl: int, n_tw: int) -> list[list[int]]:
    """For each residue ``(g+1) mod N_SW``, the families whose window closes.

    Family ``w`` closes a window at step ``g`` when ``(g + 1 − w·N_MSL)`` is a
    positive multiple of ``N_SW``. Precomputing the residue table keeps the
    per-step cost at O(1) instead of scanning all ``N_TW`` families.
    """
    table: list[list[int]] = [[] for _ in range(n_sw)]
    for w in range(n_tw):
        table[(w * n_msl) % n_sw].append(w)
    return table


def _singlepass_chunks(
    n_total: int, n_sw: int, n_jobs: int,
) -> list[tuple[int, int]]:
    """Split ``[0, N_TotalSteps)`` for the single-pass windowed engine.

    Each chunk re-propagates an ``N_SW − 1`` halo to prime its ring buffer, so
    the number of chunks is not free. Every fine step costs the same, so the
    work is uniform and the wall time with ``c`` chunks on ``n_jobs`` workers is
    about ``ceil(c / n_jobs) · (n_total / c + halo)``. For ``c <= n_jobs`` that
    is minimised at ``c = n_jobs``: splitting further shrinks no worker's share
    and adds one more halo each time. Oversubscribing — the usual load-balancing
    trick — is counter-productive here for the same reason.

    ``c`` is additionally capped so a chunk always carries several times its own
    halo; otherwise a short run spends most of its time re-priming ring buffers
    (a 2 000-step run with ``N_SW = 960`` would pay a 959-step halo per chunk).
    """
    n_total = int(n_total)
    if n_jobs <= 1 or n_total <= 0:
        return [(0, max(0, n_total))]
    halo = max(0, int(n_sw) - 1)
    n_chunks = int(n_jobs)
    if halo:
        n_chunks = min(n_chunks, max(1, n_total // (_HALO_WORK_RATIO * halo)))
    n_chunks = max(1, n_chunks)
    size = int(math.ceil(n_total / n_chunks))
    out: list[tuple[int, int]] = []
    c = 0
    while c < n_total:
        e = min(c + size, n_total)
        out.append((c, e))
        c = e
    return out


def _simulate_singlepass_chunk(args):
    """Single global pass over a contiguous step range (picklable).

    Implements §D5.1.3 as printed: ``N_TotalSteps = N_repeat·N_SW +
    (N_TW − 1)·N_MSL`` is *"the run duration plus the time required to complete
    all the time windows"* — one timeline, not ``N_TW`` of them. Each fine step
    is propagated **once** and feeds every family whose window contains it.

    The chunk owns the window closes in ``[own_start, own_end)`` and simulates
    ``[own_start − (N_SW − 1), own_end)`` so the ring buffer is primed; the halo
    steps are propagated but produce no statistics of their own.

    Returns ``{w: EPFDWindowStats}`` for the families that closed here.
    """
    (own_start, own_end, windows,
     constellation, wcg, pfd_mask, es_antenna,
     alpha0_deg, min_elevation_deg, pfd_bw_correction_db,
     raan_dot_artificial_rad_s, raan_dot_override_rad_s,
     max_co_freq_by_lat, strict_exclusion_zone,
     wdelta_deg, t_run_s, gso_min_elevation_deg, or_rescues_capped) = args

    t_fine = windows.t_fine_s
    n_sw = windows.n_sw
    n_msl = windows.n_msl
    n_tw = windows.n_tw
    n_repeat = windows.n_repeat
    n_steps_stats = windows.n_steps_stats

    es_lat = wcg.es_lat_deg
    es_lon = wcg.es_lon_deg
    gso_lon = wcg.gso_lon_deg
    es_ecef = _es_ecef_from_wcg(wcg)
    es_x, es_y, es_z = float(es_ecef[0]), float(es_ecef[1]), float(es_ecef[2])

    lat_r = math.radians(es_lat)
    lon_r = math.radians(es_lon)
    sl, cl = math.sin(lat_r), math.cos(lat_r)
    so, co = math.sin(lon_r), math.cos(lon_r)
    R_enu = np.array([
        [-so,        co,       0.0],
        [-sl * co,  -sl * so,  cl ],
        [ cl * co,   cl * so,  sl ],
    ])
    max_co_freq = _resolve_max_co_freq(es_lat, max_co_freq_by_lat or [])

    N = len(constellation)
    _prop_cache = build_constellation_cache(
        constellation, raan_dot_override_rad_s=raan_dot_override_rad_s,
    )
    min_h = _min_operating_height_km_batch(constellation, N)

    ring = _WindowRing(n_sw, N)
    table = _close_table(n_sw, n_msl, n_tw)
    out: dict[int, EPFDWindowStats] = {}

    sim_start = max(0, own_start - (n_sw - 1))
    per_sat: dict = {}
    for g in range(sim_start, own_end):
        t_s = g * t_fine
        gso_ecef = gso_position_ecef(gso_lon, t_s)
        pos_ecef_all, vel_ecef_all = propagate_and_to_ecef_batch(
            constellation, t_s,
            raan_dot_artificial_rad_s=raan_dot_artificial_rad_s,
            raan_dot_override_rad_s=raan_dot_override_rad_s,
            _cache=_prop_cache,
            wdelta_deg=wdelta_deg,
            t_run_s=t_run_s,
        )
        diff_all = pos_ecef_all - es_ecef
        enu_all = (R_enu @ diff_all.T).T
        ranges = np.linalg.norm(diff_all, axis=1)
        sin_el = np.where(ranges > 1e-6, enu_all[:, 2] / ranges, -1.0)

        visible_idx = np.where(sin_el >= 0.0)[0]
        if strict_exclusion_zone and _epfd_gso_min_elevation_active(gso_min_elevation_deg):
            if compute_elevation(es_ecef, gso_ecef, es_lat, es_lon) < float(gso_min_elevation_deg):
                visible_idx = np.array([], dtype=np.int64)

        per_sat.clear()
        _accumulate_epfd_visible_satellites(
            visible_idx=visible_idx,
            pos_ecef_all=pos_ecef_all,
            vel_ecef_all=vel_ecef_all,
            es_ecef=es_ecef,
            es_x=es_x, es_y=es_y, es_z=es_z,
            es_lat_deg=es_lat, es_lon_deg=es_lon,
            gso_ecef=gso_ecef,
            alpha0_deg=alpha0_deg,
            pfd_mask=pfd_mask,
            es_antenna=es_antenna,
            pfd_bw_correction_db=pfd_bw_correction_db,
            max_co_freq=max_co_freq,
            strict_max_co_freq_total=False,
            subsat_lat_all=None,
            subsat_lon_all=None,
            sat_local_frames=None,
            min_operating_height_km_all=min_h,
            dual_ts=None,
            t_s=t_s,
            strict_exclusion_zone=strict_exclusion_zone,
            min_angle_at_es_deg=0.0,
            min_elevation_deg=min_elevation_deg,
            sin_el_full=sin_el,
            per_sat_out=per_sat,
        )
        ring.push(per_sat["idx"], per_sat["epfd_lin"], per_sat["std"], per_sat["orx"])

        if g < own_start:
            continue  # halo: primes the ring, owns no window close

        for w in table[(g + 1) % n_sw]:
            set_start = w * n_msl
            rel = g + 1 - set_start
            if rel < n_sw or rel > n_repeat * n_sw:
                continue
            win_start = g + 1 - n_sw
            stats_end = set_start + n_steps_stats
            if win_start >= stats_end:
                continue  # whole window past this family's run duration

            _wdiag: dict = {}
            agg = _window_aggregate_dense(
                ring.E, ring.S, ring.O, max_co_freq, row_order=ring.slices(),
                active=ring.active(), eligible=ring.eligible(),
                stats_out=_wdiag, or_rescues_capped=or_rescues_capped,
            )

            n_keep = min(n_sw, stats_end - win_start)
            if n_keep < n_sw:
                agg = agg[:n_keep]
            with np.errstate(divide="ignore", invalid="ignore"):
                epfd_db = np.where(agg > 0.0, 10.0 * np.log10(agg), -999.0)

            ws = out.get(w)
            if ws is None:
                ws = EPFDWindowStats()
                out[w] = ws
            ws.add_batch(
                epfd_db, duration_s=t_fine,
                first_time_s=win_start * t_fine, time_step_s=t_fine,
            )
            ws.note_window(int(_wdiag.get("n_elig", 0)),
                           bool(_wdiag.get("capped", False)))

    return {"single_pass": True, "sets": out}


def _simulate_window_block(args):
    """Simulate a contiguous block of whole windows of set ``w`` (picklable).

    The unit of parallel work is a **block of complete sliding windows** within
    one set. Each window is self-contained (eligibility, peak-ranking and the
    per-step aggregate are all computed inside its own ``N_SW`` fine steps), so a
    block boundary that falls on a window boundary needs no halo and no shared
    state — the standalone loop, the local Pool and the injected cluster executor
    are bit-identical. Splitting by window (rather than by set) gives real
    parallelism even when there is a single window set (``N_TW == 1``).

    Block ``blk_win_start .. blk_win_start+blk_win_count`` of set ``w`` covers
    global steps ``[w·N_MSL + blk_win_start·N_SW, … + blk_win_count·N_SW)``.
    Returns ``{"w": w, "win": EPFDWindowStats}`` (blocks of the same set merge).
    """
    (w, blk_win_start, blk_win_count, windows,
     constellation, wcg, pfd_mask, es_antenna,
     alpha0_deg, min_elevation_deg, pfd_bw_correction_db,
     raan_dot_artificial_rad_s, raan_dot_override_rad_s,
     max_co_freq_by_lat, strict_max_co_freq_total, strict_exclusion_zone,
     min_angle_at_es_deg, wdelta_deg, t_run_s, gso_min_elevation_deg,
     system_id_per_sat, max_co_freq_by_system, or_rescues_capped) = args

    t_fine = windows.t_fine_s
    n_sw = windows.n_sw
    set_start = w * windows.n_msl
    start = set_start + blk_win_start * n_sw
    end = start + blk_win_count * n_sw
    stats_end = set_start + windows.n_steps_stats

    win_stats = EPFDWindowStats()

    es_lat = wcg.es_lat_deg
    es_lon = wcg.es_lon_deg
    gso_lon = wcg.gso_lon_deg
    es_ecef = _es_ecef_from_wcg(wcg)
    es_x, es_y, es_z = float(es_ecef[0]), float(es_ecef[1]), float(es_ecef[2])

    lat_r = math.radians(es_lat)
    lon_r = math.radians(es_lon)
    sl, cl = math.sin(lat_r), math.cos(lat_r)
    so, co = math.sin(lon_r), math.cos(lon_r)
    R_enu = np.array([
        [-so,        co,       0.0],
        [-sl * co,  -sl * so,  cl ],
        [ cl * co,   cl * so,  sl ],
    ])
    max_co_freq = _resolve_max_co_freq(es_lat, max_co_freq_by_lat or [])

    N = len(constellation)
    _prop_cache = build_constellation_cache(
        constellation, raan_dot_override_rad_s=raan_dot_override_rad_s,
    )
    min_h = _min_operating_height_km_batch(constellation, N)

    buffer: list = []
    for g in range(start, end):
        t_s = g * t_fine
        gso_ecef = gso_position_ecef(gso_lon, t_s)
        pos_ecef_all, vel_ecef_all = propagate_and_to_ecef_batch(
            constellation, t_s,
            raan_dot_artificial_rad_s=raan_dot_artificial_rad_s,
            raan_dot_override_rad_s=raan_dot_override_rad_s,
            _cache=_prop_cache,
            wdelta_deg=wdelta_deg,
            t_run_s=t_run_s,
        )
        diff_all = pos_ecef_all - es_ecef
        enu_all = (R_enu @ diff_all.T).T
        ranges = np.linalg.norm(diff_all, axis=1)
        sin_el = np.where(ranges > 1e-6, enu_all[:, 2] / ranges, -1.0)

        visible_idx = np.where(sin_el >= 0.0)[0]
        if strict_exclusion_zone and _epfd_gso_min_elevation_active(gso_min_elevation_deg):
            if compute_elevation(es_ecef, gso_ecef, es_lat, es_lon) < float(gso_min_elevation_deg):
                visible_idx = np.array([], dtype=np.int64)

        per_sat: dict = {}
        _accumulate_epfd_visible_satellites(
            visible_idx=visible_idx,
            pos_ecef_all=pos_ecef_all,
            vel_ecef_all=vel_ecef_all,
            es_ecef=es_ecef,
            es_x=es_x, es_y=es_y, es_z=es_z,
            es_lat_deg=es_lat, es_lon_deg=es_lon,
            gso_ecef=gso_ecef,
            alpha0_deg=alpha0_deg,
            pfd_mask=pfd_mask,
            es_antenna=es_antenna,
            pfd_bw_correction_db=pfd_bw_correction_db,
            max_co_freq=max_co_freq,
            strict_max_co_freq_total=strict_max_co_freq_total,
            subsat_lat_all=None,
            subsat_lon_all=None,
            sat_local_frames=None,
            min_operating_height_km_all=min_h,
            dual_ts=None,
            t_s=t_s,
            strict_exclusion_zone=strict_exclusion_zone,
            min_angle_at_es_deg=min_angle_at_es_deg,
            min_elevation_deg=min_elevation_deg,
            sin_el_full=sin_el,
            system_id_all=system_id_per_sat,
            max_co_freq_by_system=max_co_freq_by_system,
            per_sat_out=per_sat,
        )
        buffer.append((g, per_sat["idx"], per_sat["epfd_lin"], per_sat["std"], per_sat["orx"]))

        # Window closes every N_SW steps counted from the set start.
        if (g + 1 - set_start) % n_sw == 0:
            _process_closed_window(
                buffer=buffer,
                max_co_freq=max_co_freq,
                stats_end_step=stats_end,
                t_fine_s=t_fine,
                win_stats=win_stats,
                or_rescues_capped=or_rescues_capped,
            )
            buffer = []

    return {"w": int(w), "win": win_stats}


def _build_worst_envelope(
    window_ccdfs: list, total_by_set: list,
) -> tuple[np.ndarray, np.ndarray]:
    """Worst-per-level CCDF envelope across window sets (§D7.1 go/no-go).

    At each 0.1 dB level, the envelope takes the largest exceedance-% over all
    sets. Compliance against the envelope holds iff every set complies. Returns
    ``(bins_desc, pct_desc)`` in the same layout as
    :meth:`EPFDStreamAccumulator.build_ccdf`.
    """
    from .epfd_stream_accumulator import _NBINS, _BIN_MIN_DB, _BIN_SIZE_DB
    env = np.zeros(_NBINS, dtype=np.float64)
    occupied = np.zeros(_NBINS, dtype=bool)
    for win, total in zip(window_ccdfs, total_by_set):
        if total <= 0.0:
            continue
        rev = np.cumsum(win.duration_per_bin[::-1])[::-1]  # time with epfd ≥ level_i
        # Clamped for the same reason as in ``build_ccdf``: a cumulative share
        # of the run cannot exceed 100 %, and a few-ulp overshoot would fail the
        # §D7.1.3 test on the 100 %-time row.
        env = np.maximum(env, np.minimum(rev / total * 100.0, 100.0))
        occupied |= win.duration_per_bin > 0.0
    # Report ONLY the levels that some set actually occupies (union of bins with
    # mass) — exactly like EPFDStreamAccumulator.build_ccdf. Using ``env > 0``
    # instead would emit every bin from the histogram floor (−350 dBW) up to the
    # peak (the reverse-cumulative exceedance is positive at all those levels),
    # producing a dense spurious flat tail that diverges from the standard path
    # and breaks the N_SW=1 degeneracy equivalence.
    nz = np.where(occupied)[0]
    if nz.size == 0:
        return np.array([]), np.array([])
    levels = _BIN_MIN_DB + nz.astype(np.float64) * _BIN_SIZE_DB
    return levels[::-1], env[nz][::-1]


def _finalize_windowed_result(result, windows, win_by_index):
    """Assemble the §D5.1.4.2 result from the per-family statistics.

    Shared by both dispatches (single pass and legacy per-set), so the two
    can only differ in how the statistics were gathered, never in how they
    are turned into per-set CCDFs (Step 23) and the go/no-go envelope
    (Step 24).
    """
    window_stats = [w for w in win_by_index if w is not None]
    result.window_stats = window_stats
    result.per_window_ccdf = [w.build_ccdf() for w in window_stats]

    # Headline CCDF = worst-per-level envelope. §D5.1.4.2 runs the examination
    # once per window set and the network complies only if EVERY set complies,
    # so the per-level maximum of the N_TW exceedance curves is pass-equivalent
    # to testing each set: at the Article 22 level of a given row the envelope
    # carries the largest percentage any set recorded, and §D7.1.3 Step 4-5
    # (``check_article22_compliance``) compares exactly that percentage against
    # the row threshold. One set failing therefore fails the envelope, and the
    # envelope passing means no set exceeded the threshold anywhere.
    totals = [w.total_duration_s for w in window_stats]
    bins_desc, pct_desc = _build_worst_envelope(window_stats, totals)
    result.cdf_epfd_dBW = bins_desc
    result.cdf_percentage = pct_desc

    # Headline accumulator (for the histogram artifact / peak metrics) = the set
    # with the highest recorded peak epfd↓. Sets with no valid step keep the
    # ``-inf`` peak init; argmax still returns a valid index (0 if all are -inf),
    # and the copied min/max stay at their (-inf / +inf) defaults — the CLI
    # STATISTICS block is gated on ``n_steps_valid > 0``, so nothing prints for a
    # fully-null run.
    if window_stats:
        wi = int(np.argmax([w.epfd_max_db for w in window_stats]))
        result.worst_window_index = wi
        worst = window_stats[wi]
        headline = EPFDStreamAccumulator()
        headline.duration_per_bin = worst.duration_per_bin.copy()
        headline.total_duration_s = worst.total_duration_s
        headline.n_steps = worst.n_steps
        headline.n_steps_valid = worst.n_steps_valid
        headline.n_fine_steps = worst.n_steps  # all-fine by construction
        headline.epfd_max_db = worst.epfd_max_db
        headline.epfd_min_valid_db = worst.epfd_min_valid_db  # real min, not the peak
        headline.peak_time_s = worst.peak_time_s
        # The windowed engine walks ONE timeline, [0, N_TotalSteps) at T_fine,
        # so the extent of the run is the timeline — not the extent of the
        # samples this particular set happened to keep. Consumers that size the
        # animation / report from ``acc`` (export_visualization) would otherwise
        # see ``None`` and fall back to a hard-coded duration.
        headline.first_time_s = 0.0
        headline.last_time_s = max(0, int(windows.n_total_steps) - 1) * float(windows.t_fine_s)
        result.acc = headline

    result.n_timeline_steps = int(windows.n_total_steps)
    result.timeline_t_fine_s = float(windows.t_fine_s)

    # §D5.1.4.2 Step 19 diagnostics. An empty tracked set means no satellite
    # stayed within α₀/ε₀ for a WHOLE window, so the aggregate falls back to
    # the Step-20 gain branch alone and the statistic is anti-conservative.
    # That is exactly what MIN_DURATION longer than any achievable track looks
    # like, and it reads as a clean PASS unless it is reported.
    n_win = sum(w.n_windows for w in window_stats)
    n_empty = sum(w.n_windows_no_eligible for w in window_stats)
    n_capped = sum(w.n_windows_capped for w in window_stats)
    result.window_diagnostics = {
        "n_windows_closed": int(n_win),
        "n_windows_no_eligible": int(n_empty),
        "n_windows_capped": int(n_capped),
        "empty_window_fraction": (n_empty / n_win) if n_win else 0.0,
        "mean_eligible_per_window": (
            sum(w.sum_eligible for w in window_stats) / n_win) if n_win else 0.0,
        "per_set_empty_fraction": [w.empty_window_fraction for w in window_stats],
    }
    if n_win and n_empty:
        frac = 100.0 * n_empty / n_win
        msg = (
            "  §D5.1.4.2 Step 19: %d of %d closed windows (%.1f%%) had NO "
            "eligible satellite — no satellite held the α₀/ε₀ condition for a "
            "whole MIN_DURATION window, so those steps aggregate only the "
            "Step-20 gain branch. Results are NOT conservative where this "
            "happens; it usually means MIN_DURATION exceeds the achievable "
            "track duration for this constellation and ES latitude."
        )
        if frac >= 50.0:
            logger.warning(msg, n_empty, n_win, frac)
        else:
            logger.info(msg, n_empty, n_win, frac)
    logger.info(
        "Windowed simulation complete: %d/%d sets, envelope points=%d, "
        "%d windows closed (%d capped by MAX_CO_FREQ, %.2f eligible/window avg)",
        len(window_stats), windows.n_tw, bins_desc.size, n_win, n_capped,
        result.window_diagnostics["mean_eligible_per_window"],
    )
    return result


def run_epfd_simulation_windowed(
    constellation: list[OrbitalElements],
    wcg: WCGResult,
    pfd_mask: PFDMask,
    es_antenna: EarthStationAntenna,
    alpha0_deg: float,
    min_elevation_deg: float,
    windows: TrackDurationWindows,
    n_jobs: int = -1,
    pfd_bw_correction_db: float = 0.0,
    raan_dot_artificial_rad_s: float = 0.0,
    raan_dot_override_rad_s: float | None = None,
    max_co_freq_by_lat: list | None = None,
    strict_max_co_freq_total: bool = False,
    strict_exclusion_zone: bool = False,
    min_angle_at_es_deg: float = 0.0,
    wdelta_deg: float = 0.0,
    t_run_s: float = 0.0,
    gso_min_elevation_deg: float = -90.0,
    system_id_per_sat: np.ndarray | None = None,
    max_co_freq_by_lat_per_system: list | None = None,
    single_pass: bool = True,
    or_rescues_capped: bool = True,
) -> EPFDSimulationResult:
    """EPFD↓ with the track-duration sliding-window variant (S.1503-4 §D5.1.4.2).

    Combines the ``N_TW`` slide-window sets' CDFs into a worst-per-level
    envelope. Fine step only: the window accounting of §D5.1.3 is defined
    entirely in fine time steps, and §D5.1.4.2 Step 5's "otherwise Ncoarse = 1
    all the time" branch is the one taken here. (Note the Recommendation does
    provide for the dual time step inside this variant — Steps 5/6 are
    word-for-word those of §D5.1.4.1 and Step 22 carries a rule for a coarse
    step straddling a window close — so this is a project choice, not a gap in
    the text.)

    ``single_pass`` selects the dispatch:

    * ``True`` (default, **conformant**) — one global pass over
      ``[0, N_TotalSteps)``. §D5.1.3 defines ``N_TotalSteps = N_repeat·N_SW +
      (N_TW − 1)·N_MSL`` as *"the run duration plus the time required to
      complete all the time windows"*: a single timeline. Every fine step is
      propagated once and feeds each family whose window contains it, because
      all windows open at step ``g`` began less than ``N_SW`` steps earlier.
      Parallel work is a contiguous step range plus an ``N_SW − 1`` halo that
      primes the ring buffer.
    * ``False`` (legacy) — one independent simulation per set, which
      re-propagates the same global grid ``N_TW`` times. Kept for A/B
      verification; on a real filing it costs ~10³ times the conformant path.

    Both paths must produce the same statistics; that equivalence is the
    subject of ``test_single_pass_matches_legacy``. "Same" here means identical
    up to summation rounding, not bit-identical: the single pass reduces a
    zero-padded ``N_sat`` row pairwise while the legacy path adds only the
    recorded contributors in ascending index order, which can differ in the last
    ulp (~1e-15 dB against a 0.1 dB bin). A selection difference, by contrast,
    moves the value by whole dB.

    **Selection scope (§D5.1.4.2 Step 20).** Per-window selection is: full-window
    α₀/ε₀ eligibility (Step 19) → rank eligible by window-peak epfd↓ (Step 19bis)
    → keep the top ``MAX_CO_FREQ[lat]`` + the OR/sidelobe sats (Step 20/22). Step
    20 does **not** include the MIN_ANGLE_AT_ES pruning of §D5.1.4.1 Step 21, the
    non-normative ``strict_max_co_freq_total`` joint cap, or the Resolution-76
    per-system MAX_CO_FREQ partitioning — so those inputs are accepted (for a
    uniform call signature with the standard path) but **not applied here**, and
    a warning is emitted when any is active.

    ``or_rescues_capped`` selects the reading of the printed Step 20. The
    default ``True`` follows the text: Step 20 adds "those satellites for which
    GRX(φ) > min[Gmax − 30 dB, GRX(α₀[Latitude])]" with no further qualifier,
    and the Note removes from that set only the satellites already on the list
    of the highest MAX_CO_FREQ[lat] — so a window-eligible satellite that Step
    19bis dropped under the cap DOES contribute through the gain branch.
    ``False`` bars every window-eligible satellite from the gain branch, which
    the printed text does not say and which lowers epfd; it is retained only to
    reproduce earlier runs and to quantify the difference.
    """
    result = EPFDSimulationResult(wcg=wcg)
    result.windows = windows

    es_ecef = _es_ecef_from_wcg(wcg)
    if np.linalg.norm(es_ecef) < RE_KM * 0.9:
        logger.warning("ES position invalid, aborting windowed simulation.")
        return result

    # Selection modifiers outside §D5.1.4.2 Step 20 are not applied in the
    # windowed selection — warn rather than silently ignore (the standard-path
    # call passes them uniformly).
    _ignored = []
    if float(min_angle_at_es_deg) > 0.0:
        _ignored.append(f"MIN_ANGLE_AT_ES={min_angle_at_es_deg:g}°")
    if strict_max_co_freq_total:
        _ignored.append("strict_max_co_freq_total")
    if system_id_per_sat is not None and max_co_freq_by_lat_per_system is not None:
        _ignored.append("per-system MAX_CO_FREQ (Res.76)")
    # F-P02/F-P03: a log line is not provenance. Carry the list on the result so
    # the run artifact and the §D7.3 report can state which inputs the windowed
    # selection did not apply — otherwise a run with MIN_ANGLE_AT_ES declared
    # looks identical to one without it.
    result.selection_inputs_ignored = list(_ignored)
    result.or_rescues_capped = bool(or_rescues_capped)
    if _ignored:
        logger.warning(
            "  §D5.1.4.2 windowed selection follows Step 20 (window-peak "
            "MAX_CO_FREQ + OR) and does NOT apply: %s. These are honoured only "
            "on the standard §D5.1.4.1 path.",
            ", ".join(_ignored),
        )

    if wdelta_deg > 0.0:
        # §D6.3.4 ramps the station-keeping offset linearly over the run. The
        # windowed variant does not simulate ``nsteps × tstep`` — it walks
        # N_TotalSteps fine steps — so a ``t_run_s`` sized for the standard path
        # would compress or stretch the ramp over the wrong span.
        t_timeline = float(windows.t_total_duration_s)
        if t_run_s <= 0.0 or abs(t_run_s - t_timeline) > 1e-6 * max(1.0, t_timeline):
            logger.info(
                "  §D6.3.4 W_delta ramp retimed to the windowed timeline: "
                "t_run %.1f s → %.1f s (N_TotalSteps=%d × T_fine=%.4f s).",
                t_run_s, t_timeline, int(windows.n_total_steps), windows.t_fine_s,
            )
            t_run_s = t_timeline

    max_co_freq_by_system: dict[int, int] | None = None
    if system_id_per_sat is not None and max_co_freq_by_lat_per_system is not None:
        max_co_freq_by_system = {
            int(sid): _resolve_max_co_freq(wcg.es_lat_deg, tbl or [])
            for sid, tbl in enumerate(max_co_freq_by_lat_per_system)
        }

    import multiprocessing
    if n_jobs < 1:
        n_jobs = multiprocessing.cpu_count()

    if single_pass:
        # Conformant dispatch: one global pass over [0, N_TotalSteps). Parallel
        # unit = a contiguous step range; each task re-propagates an N_SW − 1
        # halo to prime its ring buffer, so chunks must stay well above N_SW.
        n_total = int(windows.n_total_steps)
        n_sw = int(windows.n_sw)
        chunks = _singlepass_chunks(n_total, n_sw, n_jobs)
        halo_overhead = sum(min(a, n_sw - 1) for a, _ in chunks)
        ring_mb = n_sw * len(constellation) * 10 / 1e6
        logger.info(
            "Starting EPFD↓ windowed simulation (§D5.1.4.2, single pass): "
            "N_TW=%d, N_SW=%d, N_MSL=%d, N_Repeat=%d, N_TotalSteps=%d, "
            "%d sats, T_fine=%.4fs, %d chunk(s) over %d jobs, "
            "halo overhead %d steps (%.1f%%), ring ≈%.1f MB/worker.",
            windows.n_tw, n_sw, windows.n_msl, windows.n_repeat, n_total,
            len(constellation), windows.t_fine_s, len(chunks), n_jobs,
            halo_overhead, 100.0 * halo_overhead / max(1, n_total), ring_mb,
        )
        if len(chunks) == 1 and n_jobs > 1:
            logger.warning(
                "  Single-pass dispatch collapsed to ONE chunk on %d jobs: "
                "N_TotalSteps=%d is too short to split without the N_SW−1=%d "
                "halo dominating (each chunk must carry ≥%dx its halo). The run "
                "is effectively sequential; this is a dimensioning consequence "
                "of §D5.1.3, not an error.",
                n_jobs, n_total, n_sw - 1, _HALO_WORK_RATIO,
            )
        if ring_mb > 2000.0:
            logger.warning(
                "  Ring buffer ≈%.0f MB per worker (N_SW=%d × %d sats). "
                "Reduce n_jobs or fall back to single_pass=False.",
                ring_mb, n_sw, len(constellation),
            )

        tasks = [(
            a, b, windows, constellation, wcg, pfd_mask, es_antenna,
            alpha0_deg, min_elevation_deg, pfd_bw_correction_db,
            raan_dot_artificial_rad_s, raan_dot_override_rad_s,
            max_co_freq_by_lat or [], strict_exclusion_zone,
            wdelta_deg, t_run_s, gso_min_elevation_deg, or_rescues_capped,
        ) for a, b in chunks]

        win_by_index_sp: list = [None] * windows.n_tw

        def _consume_sp(res):
            for w, ws in res["sets"].items():
                if win_by_index_sp[w] is None:
                    win_by_index_sp[w] = ws
                else:
                    win_by_index_sp[w].merge(ws)

        if n_jobs == 1 or len(tasks) == 1:
            for task in tasks:
                _consume_sp(_simulate_singlepass_chunk(task))
        elif _EPFD_EXECUTOR is not None:
            logger.info("  Single-pass dispatch via injected executor (%d chunks)", len(tasks))
            for res in _EPFD_EXECUTOR(_simulate_singlepass_chunk, tasks, _epfd_executor_init()):
                _consume_sp(res)
        else:
            numba_thr = _compute_epfd_numba_threads(n_jobs)
            with multiprocessing.Pool(
                processes=min(n_jobs, len(tasks)),
                initializer=_epfd_pool_initializer,
                initargs=(_epfd_global_snapshot(numba_thr),),
            ) as pool:
                for res in pool.imap(_simulate_singlepass_chunk, tasks):
                    _consume_sp(res)

        return _finalize_windowed_result(result, windows, win_by_index_sp)

    # Legacy dispatch (A/B reference): parallel unit = a block of whole windows
    # within a set. Windows are independent, so blocks fan out with no halo and
    # no shared state — but each set re-propagates the whole global grid.
    total_windows = windows.n_tw * windows.n_repeat
    if n_jobs <= 1:
        win_per_block = windows.n_repeat  # one block per set (sequential)
    else:
        target_blocks = min(1024, max(n_jobs * 8, n_jobs))
        win_per_block = max(1, int(math.ceil(total_windows / target_blocks)))

    tasks = []
    for w in range(windows.n_tw):
        b = 0
        while b < windows.n_repeat:
            cnt = min(win_per_block, windows.n_repeat - b)
            tasks.append((
                w, b, cnt, windows, constellation, wcg, pfd_mask, es_antenna,
                alpha0_deg, min_elevation_deg, pfd_bw_correction_db,
                raan_dot_artificial_rad_s, raan_dot_override_rad_s,
                max_co_freq_by_lat or [], strict_max_co_freq_total, strict_exclusion_zone,
                min_angle_at_es_deg, wdelta_deg, t_run_s, gso_min_elevation_deg,
                system_id_per_sat, max_co_freq_by_system, or_rescues_capped,
            ))
            b += cnt

    # Cost model: ≈ N_TW × Nstep fine-step propagations (one full run per window
    # set); each block buffers ≤ one window (N_SW steps × visible sats).
    total_prop = windows.n_tw * windows.n_repeat * windows.n_sw
    logger.info(
        "Starting EPFD↓ windowed simulation (§D5.1.4.2): N_TW=%d sets, "
        "N_SW=%d, N_MSL=%d, N_Repeat=%d, %d sats, T_fine=%.4fs, "
        "≈%.3g total step-propagations (~%d× the standard run), "
        "%d parallel blocks (%d windows each) over %d jobs.",
        windows.n_tw, windows.n_sw, windows.n_msl, windows.n_repeat,
        len(constellation), windows.t_fine_s, float(total_prop), windows.n_tw,
        len(tasks), win_per_block, n_jobs,
    )
    logger.warning(
        "  Legacy per-set dispatch: ≈%.3g step-propagations, ~%dx the %d steps "
        "§D5.1.3 asks to simulate (N_TotalSteps). Kept for A/B verification "
        "only — use single_pass=True for production runs.",
        float(total_prop), windows.n_tw, int(windows.n_total_steps),
    )

    win_by_index: list = [None] * windows.n_tw

    def _consume(res):
        w = int(res["w"])
        if win_by_index[w] is None:
            win_by_index[w] = res["win"]
        else:
            win_by_index[w].merge(res["win"])  # merge blocks of the same set

    if n_jobs == 1:
        for task in tasks:
            _consume(_simulate_window_block(task))
    elif _EPFD_EXECUTOR is not None:
        logger.info("  Windowed dispatch via injected executor (%d blocks)", len(tasks))
        for res in _EPFD_EXECUTOR(_simulate_window_block, tasks, _epfd_executor_init()):
            _consume(res)
    else:
        numba_thr = _compute_epfd_numba_threads(n_jobs)
        with multiprocessing.Pool(
            processes=min(n_jobs, len(tasks)),
            initializer=_epfd_pool_initializer,
            initargs=(_epfd_global_snapshot(numba_thr),),
        ) as pool:
            # Ordered imap: blocks merge in task order in every path (sequential,
            # Pool, executor) so statistics stay bit-identical across modes.
            for res in pool.imap(_simulate_window_block, tasks):
                _consume(res)

    return _finalize_windowed_result(result, windows, win_by_index)


def run_epfd_simulation_multi_es(
    constellation: list[OrbitalElements],
    wcgs: list[WCGResult],
    pfd_mask: PFDMask,
    es_antenna: EarthStationAntenna,
    alpha0_deg: float,
    min_elevation_deg: float,
    tstep_s: float,
    nsteps: int,
    dual_ts: DualTimeStep | None = None,
    n_jobs: int = -1,
    pfd_bw_correction_db: float = 0.0,
    raan_dot_artificial_rad_s: float = 0.0,
    raan_dot_override_rad_s: float | None = None,
    max_co_freq_by_lat: list | None = None,
    strict_max_co_freq_total: bool = False,
    strict_exclusion_zone: bool = False,
    min_angle_at_es_deg: float = 0.0,
    wdelta_deg: float = 0.0,
    t_run_s: float = 0.0,
    gso_min_elevation_deg: float = -90.0,
    keep_full_history: bool = False,
) -> list[EPFDSimulationResult]:
    """Runs the EPFD↓ simulation for multiple ES/WCG with shared dynamics.

    Note: when ``dual_ts`` is active, each ES may follow different time
    steps (dependent on ``min_alpha``). In that case, it is not possible to share
    exactly the same timeline and the computation falls back to individual runs.
    """
    if len(wcgs) == 0:
        return []

    # The dual time step depends on each ES state → fallback for accuracy.
    if dual_ts is not None:
        logger.info(
            "Dual Time Step active in multi-ES simulation: running each ES separately "
            "(without sharing dynamics) to preserve the adaptive timeline."
        )
        return [
            run_epfd_simulation(
                constellation=constellation,
                wcg=wcg,
                pfd_mask=pfd_mask,
                es_antenna=es_antenna,
                alpha0_deg=alpha0_deg,
                min_elevation_deg=min_elevation_deg,
                tstep_s=tstep_s,
                nsteps=nsteps,
                dual_ts=dual_ts,
                n_jobs=n_jobs,
                pfd_bw_correction_db=pfd_bw_correction_db,
                raan_dot_artificial_rad_s=raan_dot_artificial_rad_s,
                raan_dot_override_rad_s=raan_dot_override_rad_s,
                max_co_freq_by_lat=max_co_freq_by_lat,
                strict_max_co_freq_total=strict_max_co_freq_total,
                strict_exclusion_zone=strict_exclusion_zone,
                min_angle_at_es_deg=min_angle_at_es_deg,
                wdelta_deg=wdelta_deg,
                t_run_s=t_run_s,
                gso_min_elevation_deg=gso_min_elevation_deg,
                keep_full_history=keep_full_history,
            )
            for wcg in wcgs
        ]

    results = [EPFDSimulationResult(wcg=wcg, keep_full_history=keep_full_history) for wcg in wcgs]
    valid_mask = np.ones(len(wcgs), dtype=bool)
    for i, wcg in enumerate(wcgs):
        es_ecef = lla_to_ecef(wcg.es_lat_deg, wcg.es_lon_deg, 0.0)
        if np.linalg.norm(es_ecef) < RE_KM * 0.9:
            valid_mask[i] = False
            logger.warning(
                f"Invalid ES in multi-ES (idx={i}, lat={wcg.es_lat_deg:.4f}, "
                f"lon={wcg.es_lon_deg:.4f}) — simulation of this ES will be empty."
            )

    valid_wcgs = [wcg for i, wcg in enumerate(wcgs) if valid_mask[i]]
    if len(valid_wcgs) == 0:
        return results

    # --- SEQUENTIAL MODE ---
    if n_jobs == 1:
        logger.info(
            f"Starting EPFD↓ multi-ES simulation (Sequential): {nsteps} steps, "
            f"{len(constellation)} satellites, {len(valid_wcgs)} ES"
        )
        t_s = 0.0
        progress_every = max(1, nsteps // 40)  # ~2.5%

        chunk_res = _simulate_chunk_multi_es((
            0, nsteps, 0.0, tstep_s, constellation, valid_wcgs,
            pfd_mask, es_antenna, alpha0_deg, min_elevation_deg,
            pfd_bw_correction_db, raan_dot_artificial_rad_s, raan_dot_override_rad_s,
            max_co_freq_by_lat or [], strict_max_co_freq_total,
            strict_exclusion_zone,
            min_angle_at_es_deg,
            wdelta_deg, t_run_s,
            gso_min_elevation_deg,
            keep_full_history,
        ))

        chunk_accs = chunk_res["acc"]
        chunk_ts = chunk_res["ts"]
        for idx_valid, acc_i in enumerate(chunk_accs):
            idx_original = int(np.where(valid_mask)[0][idx_valid])
            results[idx_original].acc.merge(acc_i)
            if keep_full_history and chunk_ts[idx_valid]:
                results[idx_original].time_steps.extend(chunk_ts[idx_valid])

        for step_count in range(progress_every, nsteps + 1, progress_every):
            pct = min(100.0, step_count / nsteps * 100.0)
            bar = _progress_bar(pct)
            logger.info(
                f"  {bar} {pct:6.2f}%  "
                f"step {step_count}/{nsteps}"
            )

    # --- PARALLEL MODE (Fixed Step) ---
    else:
        import multiprocessing
        if n_jobs < 1:
            n_jobs = multiprocessing.cpu_count()
        numba_thr = _compute_epfd_numba_threads(n_jobs)

        logger.info(
            f"Starting EPFD↓ multi-ES simulation (Parallel): {nsteps} steps, "
            f"{n_jobs} procs, {numba_thr} numba_threads/proc, {len(valid_wcgs)} ES"
        )

        chunks_per_core = 48
        max_chunks_cap = 512
        min_chunk_steps = 1000
        target_chunks = min(max_chunks_cap, max(n_jobs * chunks_per_core, n_jobs))
        steps_per_job = max(min_chunk_steps, int(math.ceil(nsteps / max(1, target_chunks))))
        chunks = []
        current_step = 0
        current_t = 0.0
        while current_step < nsteps:
            count = min(steps_per_job, nsteps - current_step)
            chunks.append((
                current_step, count, current_t, tstep_s,
                constellation, valid_wcgs, pfd_mask, es_antenna,
                alpha0_deg, min_elevation_deg, pfd_bw_correction_db,
                raan_dot_artificial_rad_s, raan_dot_override_rad_s,
                max_co_freq_by_lat or [], strict_max_co_freq_total,
                strict_exclusion_zone,
                min_angle_at_es_deg,
                wdelta_deg, t_run_s,
                gso_min_elevation_deg,
                keep_full_history,
            ))
            current_step += count
            current_t += count * tstep_s
        logger.info(
            f"  Parallel multi-ES: chunk_size={steps_per_job} steps, total_chunks={len(chunks)}"
        )

        with multiprocessing.Pool(
            processes=n_jobs,
            initializer=_epfd_pool_initializer,
            initargs=(_epfd_global_snapshot(numba_thr),),
        ) as pool:
            results_batches = pool.imap(_simulate_chunk_multi_es, chunks)

            done_steps = 0
            total_chunks = len(chunks)
            chunk_idx = 0
            for batch_res in results_batches:
                chunk_idx += 1
                batch_accs = batch_res["acc"]
                batch_ts = batch_res["ts"]
                done_steps += int(batch_accs[0].n_steps) if batch_accs else 0
                for idx_valid, acc_i in enumerate(batch_accs):
                    idx_original = int(np.where(valid_mask)[0][idx_valid])
                    results[idx_original].acc.merge(acc_i)
                    if keep_full_history and batch_ts[idx_valid]:
                        results[idx_original].time_steps.extend(batch_ts[idx_valid])
                pct = done_steps / max(1, nsteps) * 100.0
                bar = _progress_bar(pct)
                logger.info(
                    f"  {bar} {pct:6.2f}%  "
                    f"step {done_steps}/{nsteps}  "
                    f"chunk {chunk_idx}/{total_chunks}"
                )

    # CDF per ES
    for res in results:
        res.acc.finalize_decimated()
        if keep_full_history:
            res.time_steps.sort(key=lambda s: s.time_s)
        res.build_cdf()

    logger.info(
        f"Multi-ES simulation complete: {nsteps} steps, "
        f"duration={tstep_s * nsteps:.1f}s ({(tstep_s * nsteps)/3600:.2f}h), "
        f"total ES={len(wcgs)}"
    )

    return results


# =====================================================================
#  Article 22 compliance verification
# =====================================================================

@dataclass
class ComplianceResult:
    """Result of the compliance verification."""
    compliant: bool
    margins_dB: list[tuple[float, float, float]]  # (limit, pct, margin)
    worst_margin_dB: float
    worst_limit_dBW: float
    worst_percentage: float
    # S.1503-4 §D7.3.2 Table 17 — one row per specification point:
    # {"Ji_dBW": limit, "Pi_pct": spec %, "Py_pct": simulated % of time the
    #  epfd exceeds Ji (from the probability table), "pass": bool}.
    table17: list[dict] = field(default_factory=list)


def _pct_exceeding_level(sim_result: EPFDSimulationResult, level_dBW: float) -> float:
    """Simulated % of time the epfd exceeds ``level_dBW`` (Table 17 ``Py``).

    ``cdf_epfd_dBW`` holds the quantized levels in descending order and
    ``cdf_percentage[i]`` the cumulative % of time with epfd ≥ level ``i``
    (inclusive convention of :meth:`EPFDSimulationResult.build_cdf`).
    """
    bins = np.asarray(sim_result.cdf_epfd_dBW, dtype=float)
    pct = np.asarray(sim_result.cdf_percentage, dtype=float)
    if bins.size == 0:
        return 0.0
    # Count of (descending) bins with level >= level_dBW.
    n_ge = int(np.searchsorted(-bins, -float(level_dBW), side="right"))
    if n_ge <= 0:
        return 0.0
    # Defensive clamp: Py is a share of the run and the comparison against P_i
    # is exact, so a stray ulp above 100 must not decide a row.
    return float(min(pct[min(n_ge, pct.size) - 1], 100.0))


def _epfd_at_exceedance_pct(sim_result: EPFDSimulationResult, pct_threshold: float) -> float:
    """EPFD level the CCDF reaches at ``pct_threshold`` % of time.

    Used for the **reported margin only**; the Pass/Fail decision is made on
    ``P_t`` by :func:`check_article22_compliance`, per §D7.1.3 Step 4-5.

    When the curve never accumulates ``pct_threshold`` % of time the level is
    undefined, and this returns ``-inf``. It previously returned the lowest
    occupied bin, which made the verdict depend on the run's quietest step: two
    runs of the same filing differing by one fine time step could disagree.
    """
    bins = np.asarray(sim_result.cdf_epfd_dBW, dtype=float)
    pct = np.asarray(sim_result.cdf_percentage, dtype=float)
    if bins.size == 0:
        return float("-inf")
    if pct_threshold <= 0:
        return float(bins[0])                       # J_max: the highest occupied level
    idx = int(np.searchsorted(pct, pct_threshold))
    if idx >= bins.size:
        return float("-inf")                        # curve never reaches this %
    return float(bins[idx])


def _round_down_to_bin_db(value_dB: float, bin_size_dB: float = 0.1) -> float:
    """Rounds down with a maximum precision of 0.1 dB, per S.1503 D7.1.3."""
    return float(math.floor(float(value_dB) / float(bin_size_dB) + 1e-12) * float(bin_size_dB))


def check_article22_compliance(
    sim_result: EPFDSimulationResult,
    limits: list[tuple[float, float]],
    reference_bandwidth_khz: float = 40.0,
) -> ComplianceResult:
    """Checks whether the results meet the Article 22 limits.

    Parameters
    ----------
    sim_result : simulation result
    limits : list of (epfd_limit_dBW, percentage) from Article 22
    reference_bandwidth_khz : reference bandwidth (kHz). Informational only —
        kept for API compatibility with existing callers. No rescaling is
        applied here: the simulated EPFD is already expressed in
        dBW/m²/BWref (the mask→limit bandwidth conversion happens upstream
        via ``pfd_bw_correction_db``) and ``limits`` are selected by
        ``article22_tables`` for that same reference bandwidth.

    Returns
    -------
    ComplianceResult
    """
    if len(sim_result.cdf_epfd_dBW) == 0:
        return ComplianceResult(
            compliant=True, margins_dB=[], worst_margin_dB=999.0,
            worst_limit_dBW=0.0, worst_percentage=0.0,
        )

    compliant = True
    margins = []
    table17: list[dict] = []
    worst_margin = 999.0
    worst_limit = 0.0
    worst_pct = 0.0

    for limit_dBW, pct_threshold in limits:
        limit_bin_dBW = _round_down_to_bin_db(limit_dBW, 0.1)
        epfd_at_pct = _epfd_at_exceedance_pct(sim_result, pct_threshold)
        margin = limit_bin_dBW - epfd_at_pct

        margins.append((limit_dBW, pct_threshold, margin))

        # §D7.1.3 decides on the PROBABILITY, not on a level:
        #   "Step 4: From the CDF find Pt, the probability that pfd value Ji was
        #    exceeded as obtained by the software."
        #   "Step 5: If Pi < Pt then record Pass ... Else record Fail."  ← as
        # printed (p. 135). Taken here as an editorial inversion: P_i is the
        # percentage of time Article 22 ALLOWS the limit J_i to be exceeded and
        # P_t is the percentage the simulation produced, so compliance is
        # P_t ≤ P_i. The printed inequality would declare a system compliant
        # exactly when it exceeds the limit more often than allowed, which
        # contradicts §D7.1.4 and RR Article 22 itself. This is a documented
        # reading of the text, not a transcription of it.
        #
        # P_t is `_pct_exceeding_level`, which is well defined for every J_i
        # whether or not the curve ever accumulates P_i % of time. Deciding on
        # the level instead made the verdict depend on the lowest occupied bin.
        #
        # Two rows are edge cases under this convention, and the repository
        # treats them as follows:
        #   * P_i = 0   — the hard cap: no exceedance is allowed at all, so the
        #                 row passes only if J_max is strictly below J_i (see
        #                 the branch below; equality fails).
        #   * P_i = 100 — vacuous: P_t can never exceed 100%, so the row always
        #                 passes. It carries no constraint and is kept only so
        #                 the Table 17 row count matches the Article 22 table.
        # The margin in dB is reported alongside but is NOT the criterion: at
        # these two rows a positive margin and a Fail (or the reverse) can and
        # do coexist, so the verdict always comes from the probability test.
        py = _pct_exceeding_level(sim_result, limit_bin_dBW)
        if pct_threshold <= 0.0:
            # THE P = 0 ROW IS THE RECOMMENDATION'S 100 %-TIME ROW. The two
            # percentage columns are complements and it is easy to read one for
            # the other, so, once and explicitly:
            #
            #   RR Article 22 tables  — "percentage of time during which epfd
            #                            may NOT be exceeded"
            #   this repository       — percentage of time during which epfd IS
            #                            exceeded (a CCDF ordinate)
            #
            # The two add to 100. RR Vol. I Art. 22 states the single-entry
            # limit "100 % of the time" as −160 dB(W/(m²·40 kHz)) for Table
            # 22-1A, and that value is exactly the point stored here with
            # pct = 0 (see src/data/article22_limits.json, 22-1A/60 cm:
            # [[-160.0, 0.0], …]). So J_100 of §D7.1.3's final stage is the row
            # whose stored percentage is ZERO, and the row stored with 100 % is
            # the Recommendation's P_i = 0 end, which carries no constraint.
            #
            # Printed §D7.1.3, final stage (p. 135): "From the CDF, find the
            # maximum pfd value recorded during the software run, J_max.
            # Compare it with the pfd limit specified for 100 % time, J_100. If
            # J_max < J_100 then record Pass … If J_max ≥ J_100 then record
            # Fail." Strict "<" to pass, and equality fails — which is not an
            # edge case here, because J_max is snapped to the same 0.1 dB grid
            # as the limit.
            j_max = float(sim_result.cdf_epfd_dBW[0]) if len(sim_result.cdf_epfd_dBW) else float("-inf")
            row_pass = bool(j_max < limit_bin_dBW)
        else:
            row_pass = bool(py <= pct_threshold)

        table17.append({
            "Ji_dBW": float(limit_dBW),
            "Pi_pct": float(pct_threshold),
            "Py_pct": float(py),
            "pass": row_pass,
        })

        if not row_pass:
            compliant = False
            # Only a failing point can set the worst margin; a point the curve
            # never reaches has no finite margin and must not win the ranking.
            if math.isfinite(margin) and margin < worst_margin:
                worst_margin = margin
                worst_limit = limit_bin_dBW
                worst_pct = float(pct_threshold)
        elif math.isfinite(margin) and margin < worst_margin and compliant:
            worst_margin = margin
            worst_limit = limit_bin_dBW
            worst_pct = float(pct_threshold)

    return ComplianceResult(
        compliant=compliant,
        margins_dB=margins,
        worst_margin_dB=worst_margin,
        worst_limit_dBW=worst_limit,
        worst_percentage=worst_pct,
        table17=table17,
    )
