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
    compute_angular_separation_from_ref_vector,
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
    shared = init.get("shared")
    if shared is not None:
        global _EPFD_SHARED
        _EPFD_SHARED = shared


# Large, call-invariant simulation inputs (constellation, WCG, PFD mask, ES
# antenna) for the currently-dispatched chunk/window-block sweep. Set once —
# directly in the parent process (_set_epfd_shared) and once per Pool/executor
# worker (_apply_epfd_globals, via the "shared" key of the init snapshot) —
# instead of being embedded in every chunk/block task tuple. Re-pickling
# those large objects per task (rather than once per worker) was the root
# cause of the Phase 2/3 OOM crashes on large filings — see BACKLOG.md.
_EPFD_SHARED: dict = {}


def _set_epfd_shared(constellation, wcg, pfd_mask, es_antenna) -> None:
    global _EPFD_SHARED
    _EPFD_SHARED = dict(
        constellation=constellation, wcg=wcg, pfd_mask=pfd_mask, es_antenna=es_antenna,
    )


def _epfd_shared_fields(constellation, wcg, pfd_mask, es_antenna):
    """Resolves the 4 large call-invariant fields for a chunk/block worker:
    the values as given when they're real (sequential path, direct/test
    calls), else from ``_EPFD_SHARED`` when the task carried ``None``
    placeholders (Pool/executor dispatch)."""
    if constellation is not None:
        return constellation, wcg, pfd_mask, es_antenna
    g = _EPFD_SHARED
    return g["constellation"], g["wcg"], g["pfd_mask"], g["es_antenna"]


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


def _epfd_global_snapshot(numba_threads: int, shared: dict | None = None) -> dict:
    """Snapshot of the engine global state a worker must re-apply, captured in
    the parent at dispatch time. ``numba_threads`` is parameterised so the
    built-in Pool keeps its computed per-worker thread budget while cluster
    workers pin it to 1. ``shared``, when given, is the large call-invariant
    payload (see ``_EPFD_SHARED``) applied once per worker instead of once
    per task."""
    snap = {
        "numba_threads": int(numba_threads),
        "gmst0_deg": get_earth_rotation_initial_deg(),
        "gso_mode": get_gso_longitude_mode(),
        "alpha_method": get_alpha_method(),
    }
    if shared is not None:
        snap["shared"] = shared
    return snap


def _epfd_executor_init(shared: dict | None = None) -> dict:
    """Snapshot for injected cluster executors: Numba pinned to 1 thread/worker
    (the executor controls task concurrency)."""
    return _epfd_global_snapshot(1, shared=shared)


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


@dataclass(frozen=True)
class SelectionConfig:
    """Configuration for the satellite selection strategy (Part D, Step 20).

    ``strategy``:
      - ``"s1503"``            : normative rule (§D5.1.4.1 Steps 19–22), unchanged.
      - ``"top_n_elev_random"``: Top-N highest-elevation random selection
        (WP 4A Doc 4A/442-E). Rank the α₀/ε₀-eligible ("standard") satellites by
        elevation, keep the top ``top_n``, draw ``n_select`` at random (no
        replacement). The OR branch (Step 22) is disabled for this strategy.
      - ``"hybrid_rand_he"``  : hybrid random + highest-elevation (WP 4A Doc
        4A/493-E). Draw Nco random + take Nco highest-elevation from the standard
        set, union them, then keep the Nco highest-epfd (Nco = MAX_CO_FREQ). OR
        branch disabled. ``top_n`` / ``n_select`` are unused by this strategy.
      - ``"alpha_table"``     : deterministic quota selection (WP 4A Doc 4A/312).
        Driven by a per-sub-run ``TSSAccumulator`` (passed separately, since it is
        mutable state — not carried in this frozen config). The OR branch (Step
        22) STAYS ON (normative loop). ``top_n`` / ``n_select`` / ``seed`` unused.
        ``alpha_bin_deg = 0`` (default) uses the normative TSS cases — the
        declared intervals of Doc 4A/312 p. 110. A positive value subdivides
        them, which is a NON-NORMATIVE sensitivity knob: it splits each case's
        credit among the sub-bins and so changes the Step-20 "highest TSS"
        comparison. See ``alpha_table.build_tss_cases``.

    ``n_select`` is the *requested* number of tracked satellites; the effective
    number is ``min(n_select, MAX_CO_FREQ)`` when a positive ``MAX_CO_FREQ`` is
    declared, and is further bounded by the pool size (``top_n`` / #candidates).

    ``seed`` seeds a per-time-step RNG keyed by ``(seed, step_index)`` so the
    result is reproducible **independently of chunking/parallelism**. ``None`` →
    non-deterministic (OS entropy).
    """
    strategy: str = "s1503"
    top_n: int = 5
    n_select: int = 1
    seed: int | None = None
    alpha_bin_deg: float = 0.0


def _rng_for_step(seed: int | None, step_index: int) -> np.random.Generator:
    """Deterministic per-time-step RNG.

    Same ``(seed, step_index)`` ⇒ same draw regardless of how the time axis was
    split across chunks/workers — the key never depends on chunk boundaries.
    ``seed is None`` ⇒ non-deterministic (fresh OS entropy).
    """
    if seed is None:
        return np.random.default_rng()
    return np.random.default_rng(
        np.random.SeedSequence(entropy=int(seed), spawn_key=(int(step_index),))
    )


def _select_top_n_elev_random(
    standard_items: list[tuple[float, int]],
    elev_by_k: dict[int, float],
    top_n: int,
    n_select: int,
    rng: np.random.Generator,
) -> list[tuple[float, int]]:
    """Top-N highest-elevation random selection (Doc 4A/442-E §4.1).

    ``standard_items`` are the α₀/ε₀-eligible candidates ``(epfd↓ᵢ, k)``;
    ``elev_by_k`` maps sat index ``k`` → elevation (deg). Steps: (2) sort by
    elevation descending, deterministic tie-break by ``k`` ascending — so the
    Top-N set is reproducible *before* the random draw; (3) keep the top
    ``top_n``; (4) draw ``n_select`` distinct satellites at random. Returns
    the chosen ``(epfd↓ᵢ linear, sat index)`` pairs; the caller sums the
    values (Step 23) and may use the indices for per-sat diagnostics.
    """
    if not standard_items:
        return []
    ordered = sorted(
        standard_items,
        key=lambda it: (-elev_by_k.get(it[1], -90.0), it[1]),
    )
    pool = ordered[: max(1, int(top_n))]
    n_eff = min(int(n_select), len(pool))
    if n_eff <= 0:
        return []
    if n_eff >= len(pool):
        chosen = pool
    else:
        pick = rng.choice(len(pool), size=n_eff, replace=False)
        chosen = [pool[int(i)] for i in pick]
    return [(float(epfd), int(k)) for epfd, k in chosen]


def _select_hybrid_rand_he(
    standard_items: list[tuple[float, int]],
    elev_by_k: dict[int, float],
    n_co: int,
    rng: np.random.Generator,
) -> list[tuple[float, int]]:
    """Hybrid random + highest-elevation selection (Doc 4A/493-E §3.1).

    "Worst case between the two most common methods." Over the α₀/ε₀-eligible
    candidates ``(epfd↓ᵢ, k)`` (``elev_by_k`` maps ``k`` → elevation deg), with
    ``n_co`` = number of co-frequency satellites (``MAX_CO_FREQ``):

      1. draw ``n_co`` satellites at random (no replacement) — the Random set;
      2. take the ``n_co`` highest-elevation satellites — the HE set
         (elevation desc, deterministic tie-break by ``k`` for reproducibility);
      3. union the two sets, de-duplicated by ``k`` (a high sat may also be drawn);
      4. rank the union by epfd↓ descending and keep the top ``n_co``.

    Returns the kept ``(epfd↓ᵢ linear, sat index)`` pairs; the caller sums the
    values (Step 23). More conservative than random-only or HE-only, but
    bounded above by the full worst-interferer rule (which ranks *all* visible
    sats, not just this union). The OR branch (Step 22) is disabled for this
    strategy.
    """
    if not standard_items:
        return []
    n = max(1, int(n_co))
    m = len(standard_items)
    # HE set: n_co highest by elevation (tie-break by k).
    he_ordered = sorted(
        standard_items,
        key=lambda it: (-elev_by_k.get(it[1], -90.0), it[1]),
    )
    he_set = he_ordered[: min(n, m)]
    # Random set: n_co drawn at random from all eligible.
    if n >= m:
        rand_set = list(standard_items)
    else:
        pick = rng.choice(m, size=n, replace=False)
        rand_set = [standard_items[int(i)] for i in pick]
    # Union, de-duplicated by satellite index k.
    union: dict[int, float] = {}
    for epfd, k in he_set:
        union[int(k)] = float(epfd)
    for epfd, k in rand_set:
        union[int(k)] = float(epfd)
    # Rank by epfd desc (tie-break by k for determinism), keep top n_co.
    ranked = sorted(union.items(), key=lambda kv: (-kv[1], kv[0]))
    return [(float(epfd), int(k)) for k, epfd in ranked[:n]]


def _select_standard_epfd_s1503_steps_20_21(
    items: list[tuple[float, int]],
    max_co_freq: int,
    min_angle_at_es_deg: float,
    es_ecef: np.ndarray,
    pos_ecef_all: np.ndarray,
) -> list[tuple[float, int]]:
    """S.1503-4 §D.5.1.4.1 Steps 19–21 among *standard* contributors (|α| ≥ α₀).

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

    Returns ``(epfd, satellite_index)`` pairs (index into the constellation),
    so callers that need to know *which* satellites survived selection (e.g.
    for elevation logging) can recover it — plain-float callers just discard
    the index.
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
            epfd, k = pool.pop(j)
            out.append((float(epfd), int(k)))
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
    return [(float(e), int(k)) for e, k in selected]


def _finalize_epfd_after_max_co_freq(
    standard_items: list[tuple[float, int]],
    override_items: list[tuple[float, int]],
    max_co_freq: int,
    strict_max_co_freq_total: bool,
    min_angle_at_es_deg: float,
    es_ecef: np.ndarray,
    pos_ecef_all: np.ndarray,
    *,
    system_id_all: np.ndarray | None = None,
    max_co_freq_by_system: dict[int, int] | None = None,
    per_system_out: dict[int, list] | None = None,
    selection_config: "SelectionConfig | None" = None,
    step_index: int = 0,
    elev_by_k: dict[int, float] | None = None,
    alpha_by_k: dict[int, float] | None = None,
    alpha_tss: "object | None" = None,
    t_s: float = 0.0,
    alpha_step_weight: float = 1.0,
) -> tuple[list[float], list[float], list[int], list[int]]:
    """Steps 19–22 §D5.1.4.1: standard selection (20–21), then OR branch (22).

    **Non-normative strategies (``selection_config``):** when
    ``selection_config.strategy == "top_n_elev_random"`` the standard selection
    (Steps 20–21) is replaced by the Top-N highest-elevation random draw
    (Doc 4A/442-E); the effective count is ``min(n_select, MAX_CO_FREQ)`` (the
    ``min`` reading: never exceed the co-frequency ceiling). The OR branch is
    disabled for that strategy (returns ``[]``). ``strategy == "s1503"`` (default)
    leaves everything below unchanged.

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

    Returns ``(standard_epfd, override_epfd, standard_idx, override_idx)`` —
    the value lists are the historical return shape; the index lists are
    aligned 1:1 with them (same order) so callers that need to know which
    satellites survived selection can zip them back together.
    """
    # --- Non-normative strategy dispatch (fixed- or dual-step, OR disabled) ---
    # Dispatched BEFORE the per-system OR attribution below: the strategies
    # return early and (except alpha_table) drop the Step 22 branch entirely,
    # so attributing override_items first would over-count.
    if selection_config is not None and selection_config.strategy == "top_n_elev_random":
        n_sel = int(selection_config.n_select)
        if max_co_freq and max_co_freq > 0:
            n_sel = min(n_sel, int(max_co_freq))  # never exceed the co-freq ceiling
        rng = _rng_for_step(selection_config.seed, step_index)
        chosen = _select_top_n_elev_random(
            standard_items,
            elev_by_k or {},
            selection_config.top_n,
            n_sel,
            rng,
        )
        # OR branch (Step 22) disabled for this strategy — see SelectionConfig.
        return ([v for v, _k in chosen], [], [k for _v, k in chosen], [])

    if selection_config is not None and selection_config.strategy == "hybrid_rand_he":
        # Nco = number of co-frequency satellites (MAX_CO_FREQ); paper §3.1.
        n_co = int(max_co_freq) if (max_co_freq and max_co_freq > 0) else 1
        rng = _rng_for_step(selection_config.seed, step_index)
        chosen = _select_hybrid_rand_he(
            standard_items,
            elev_by_k or {},
            n_co,
            rng,
        )
        # OR branch (Step 22) disabled for this strategy.
        return ([v for v, _k in chosen], [], [k for _v, k in chosen], [])

    if selection_config is not None and selection_config.strategy == "alpha_table":
        # Alpha table (Doc 4A/312): TSS quota selection. Unlike the two papers
        # above, this lives inside the full normative loop, so the OR branch
        # (Step 22) STAYS ON — return the override contributors unchanged.
        if alpha_tss is None:
            raise ValueError(
                "alpha_table strategy requires an alpha_tss accumulator "
                "(built per sub-run by the envelope orchestrator)."
            )
        prune = None
        if min_angle_at_es_deg and float(min_angle_at_es_deg) > 0.0:
            _min_ang = float(min_angle_at_es_deg)

            def prune(sel_k: int, remaining: list[tuple[float, int]],
                      _ang=_min_ang) -> list[tuple[float, int]]:
                # Step 21: drop candidates within MIN_ANGLE_AT_ES of the chosen
                # satellite (pool-only; never touches TSS credit — Decision 2).
                pos_sel = pos_ecef_all[sel_k]
                return [
                    (e, k) for (e, k) in remaining
                    if _separation_angle_deg_at_es(es_ecef, pos_ecef_all[k], pos_sel)
                    + 1e-12 >= _ang
                ]

        sel_idx: list[int] = []
        selected = alpha_tss.select(
            standard_items, alpha_by_k or {}, float(t_s), prune,
            weight=float(alpha_step_weight),
            selected_idx_out=sel_idx,
        )
        return (
            selected,
            [v for v, _k in override_items],
            sel_idx,
            [k for _v, k in override_items],
        )

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
        standard_pairs: list[tuple[float, int]] = []
        for sid, group in by_sys.items():
            n_sys = int(max_co_freq_by_system.get(sid, max_co_freq))
            sel_pairs = _select_standard_epfd_s1503_steps_20_21(
                group, n_sys, min_angle_at_es_deg, es_ecef, pos_ecef_all,
            )
            standard_pairs.extend(sel_pairs)
            if per_system_out is not None:
                # The group IS one system, so every selected value belongs to
                # `sid` — no index round-trip needed.
                slot = per_system_out.setdefault(sid, [0.0, 0])
                slot[0] += float(sum(v for v, _k in sel_pairs))
                slot[1] += len(sel_pairs)
        # OR (Step 22) is kept intact (normative). strict_total ignored.
        standard_epfd = [v for v, _k in standard_pairs]
        standard_idx = [k for _v, k in standard_pairs]
        override_epfd = [v for v, _k in override_items]
        override_idx = [k for _v, k in override_items]
        return standard_epfd, override_epfd, standard_idx, override_idx

    standard_pairs = _select_standard_epfd_s1503_steps_20_21(
        standard_items,
        max_co_freq,
        min_angle_at_es_deg,
        es_ecef,
        pos_ecef_all,
    )
    standard_epfd = [v for v, _k in standard_pairs]
    standard_idx = [k for _v, k in standard_pairs]
    ov_epfd = [v for v, _k in override_items]
    ov_idx = [k for _v, k in override_items]
    if max_co_freq <= 0:
        return standard_epfd, ov_epfd, standard_idx, ov_idx
    if strict_max_co_freq_total:
        combined = [(float(v), False, k) for v, k in standard_pairs] + [
            (float(v), True, k) for v, k in override_items
        ]
        if len(combined) <= max_co_freq:
            return standard_epfd, ov_epfd, standard_idx, ov_idx
        combined.sort(key=lambda item: item[0], reverse=True)
        selected = combined[:max_co_freq]
        return (
            [value for value, is_ov, _k in selected if not is_ov],
            [value for value, is_ov, _k in selected if is_ov],
            [k for _value, is_ov, k in selected if not is_ov],
            [k for _value, is_ov, k in selected if is_ov],
        )
    return standard_epfd, ov_epfd, standard_idx, ov_idx


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
    selection_config: "SelectionConfig | None" = None,
    step_index: int = 0,
    alpha_tss: "object | None" = None,
    alpha_step_weight: float = 1.0,
    per_sat_out: dict | None = None,
    per_system_out: dict[int, list] | None = None,
    contributing_idx_out: list[int] | None = None,
) -> tuple[list[float], list[float], float, bool]:
    """Returns (standard_linear, override_linear, min_alpha_deg, any_critical_gain).

    ``per_system_out`` (method_3): pass-through to
    :func:`_finalize_epfd_after_max_co_freq` — filled with this step's linear
    EPFD per ``system_id``, so a joint run can accumulate per-system CCDFs in
    the same pass. Inert unless ``system_id_all`` is also given.

    ``contributing_idx_out``: when a list is passed, it is extended with the
    global satellite indices of every value in the returned
    ``standard_linear + override_linear`` (same order), i.e. the satellites
    that survived Steps 19–22 selection. Purely additive — does not change
    selection behavior or the function's own return values.

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
            visible_idx = visible_idx[alt_vis >= (h_min_vis - 1e-9)]
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
        if not strict_exclusion_zone:
            cand = (
                np.arange(n_vis, dtype=np.int64)
                if per_sat_out is not None
                else np.nonzero(~is_standard_arr)[0]
            )
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
            return [], [], min_alpha, any_critical_gain

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
        override_items = [
            (float(epfd_lin[j]), int(idx_k[j]))
            for j in range(elig_j.size)
            if not std_mask[j]
        ]

        # Elevation-based strategies need elevation per standard candidate.
        elev_by_k = None
        if selection_config is not None and selection_config.strategy in (
            "top_n_elev_random", "hybrid_rand_he",
        ):
            elev_by_k = {}
            if sin_el_full is not None:
                _se = np.clip(np.asarray(sin_el_full, dtype=np.float64), -1.0, 1.0)
                for j in range(elig_j.size):
                    if std_mask[j]:
                        kk = int(idx_k[j])
                        elev_by_k[kk] = math.degrees(math.asin(float(_se[kk])))

        # Alpha table needs |α| per standard candidate (for TSS binning).
        # alpha_e = alpha_arr[elig_j] is aligned with idx_k / std_mask (elig order).
        alpha_by_k = None
        if selection_config is not None and selection_config.strategy == "alpha_table":
            alpha_by_k = {
                int(idx_k[j]): float(abs(alpha_e[j]))
                for j in range(elig_j.size)
                if std_mask[j]
            }

        standard_epfd, override_epfd, standard_idx, override_idx = _finalize_epfd_after_max_co_freq(
            standard_items,
            override_items,
            max_co_freq,
            strict_max_co_freq_total,
            min_angle_at_es_deg,
            es_ecef,
            pos_ecef_all,
            system_id_all=system_id_all,
            max_co_freq_by_system=max_co_freq_by_system,
            per_system_out=per_system_out,
            selection_config=selection_config,
            step_index=step_index,
            elev_by_k=elev_by_k,
            alpha_by_k=alpha_by_k,
            alpha_tss=alpha_tss,
            t_s=t_s,
            alpha_step_weight=alpha_step_weight,
        )
        if contributing_idx_out is not None:
            contributing_idx_out.extend(standard_idx)
            contributing_idx_out.extend(override_idx)

        return standard_epfd, override_epfd, min_alpha, any_critical_gain

    # --- Scalar path (``_compute_pfd_3d``): 1D masks (and Az/El fallback) ---
    min_alpha = 180.0
    any_critical_gain = False
    standard_items: list[tuple[float, int]] = []
    override_items: list[tuple[float, int]] = []
    _need_elev = (
        selection_config is not None
        and selection_config.strategy in ("top_n_elev_random", "hybrid_rand_he")
    )
    elev_by_k: dict[int, float] | None = {} if _need_elev else None
    _need_alpha = (
        selection_config is not None
        and selection_config.strategy == "alpha_table"
    )
    alpha_by_k: dict[int, float] | None = {} if _need_alpha else None
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
        is_alpha_outside = abs(alpha) >= _alpha0_k
        is_eps0_ok = bool(eps0_ok_vis[j_local])
        is_standard = is_alpha_outside and is_eps0_ok
        if per_sat_out is not None:
            # Collect mode: OR flag evaluated for every satellite (§D5.1.4.2).
            is_or_flag = s1503_or_condition_include(
                es_antenna, offaxis, _alpha0_k, theta_planar,
                disable_or_condition=strict_exclusion_zone,
            )
            is_override = (not is_standard) and is_or_flag
        else:
            is_or_flag = is_override = (not is_standard) and s1503_or_condition_include(
                es_antenna, offaxis, _alpha0_k, theta_planar,
                disable_or_condition=strict_exclusion_zone,
            )
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
            if elev_by_k is not None and sin_el_full is not None:
                elev_by_k[int(k)] = math.degrees(
                    math.asin(max(-1.0, min(1.0, float(sin_el_full[k]))))
                )
            if alpha_by_k is not None:
                alpha_by_k[int(k)] = float(abs(alpha))
        else:
            override_items.append((epfd_i, int(k)))

    if per_sat_out is not None:
        per_sat_out["idx"] = np.asarray(_ps_idx, dtype=np.int64)
        per_sat_out["epfd_lin"] = np.asarray(_ps_epfd, dtype=np.float64)
        per_sat_out["std"] = np.asarray(_ps_std, dtype=bool)
        per_sat_out["orx"] = np.asarray(_ps_orx, dtype=bool)
        return [], [], min_alpha, any_critical_gain

    standard_epfd, override_epfd, standard_idx, override_idx = _finalize_epfd_after_max_co_freq(
        standard_items,
        override_items,
        max_co_freq,
        strict_max_co_freq_total,
        min_angle_at_es_deg,
        es_ecef,
        pos_ecef_all,
        system_id_all=system_id_all,
        max_co_freq_by_system=max_co_freq_by_system,
        per_system_out=per_system_out,
        selection_config=selection_config,
        step_index=step_index,
        elev_by_k=elev_by_k,
        alpha_by_k=alpha_by_k,
        alpha_tss=alpha_tss,
        t_s=t_s,
        alpha_step_weight=alpha_step_weight,
    )
    if contributing_idx_out is not None:
        contributing_idx_out.extend(standard_idx)
        contributing_idx_out.extend(override_idx)

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
    override_items: list[tuple[float, int]] = []

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
            override_items.append((epfd_i, k))

    standard_epfd, override_epfd, _standard_idx, _override_idx = _finalize_epfd_after_max_co_freq(
        standard_items,
        override_items,
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
    selection_config = None
    if len(args) == 26:
        (start_step, num_steps, t_start, tstep_s, constellation,
         wcg, pfd_mask, es_antenna, alpha0_deg, min_elevation_deg,
         pfd_bw_correction_db, raan_dot_artificial_rad_s, raan_dot_override_rad_s,
         max_co_freq_by_lat, strict_max_co_freq_total, strict_exclusion_zone,
         min_angle_at_es_deg, wdelta_deg, t_run_s, gso_min_elevation_deg,
         keep_full_history, system_id_per_sat, max_co_freq_by_system,
         min_el_all, alpha0_all, selection_config) = args
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

    constellation, wcg, pfd_mask, es_antenna = _epfd_shared_fields(
        constellation, wcg, pfd_mask, es_antenna)

    acc = EPFDStreamAccumulator()
    results: list[EPFDTimeStepResult] | None = [] if keep_full_history else None
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

    for _i_step in range(num_steps):
        _global_step = start_step + _i_step
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
        _contrib_idx: list[int] = []
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
            per_system_out=per_system_step,
            selection_config=selection_config,
            step_index=_global_step,
            contributing_idx_out=_contrib_idx,
        )

        epfd_sum_linear = sum(standard_epfd) + sum(override_epfd)
        num_contributing = len(standard_epfd) + len(override_epfd)

        epfd_agg_db = (
            10.0 * math.log10(epfd_sum_linear) if epfd_sum_linear > 0 else -999.0
        )
        _contrib_idx_arr = np.asarray(_contrib_idx, dtype=np.int64)
        acc.add(
            time_s=t_s,
            epfd_db=epfd_agg_db,
            duration_s=tstep_s,
            num_horizon_sats=num_horizon,
            num_visible_sats=num_visible,
            num_contributing_sats=num_contributing,
            min_alpha_deg=min_alpha,
            is_fine=True,  # fixed step: uniform Δt (no coarse)
            contrib_sat_idx=_contrib_idx_arr,
            contrib_elev_deg=np.degrees(np.arcsin(np.clip(sin_el[_contrib_idx_arr], -1.0, 1.0))),
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
    if len(args) == 29:
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

    constellation, wcg, pfd_mask, es_antenna = _epfd_shared_fields(
        constellation, wcg, pfd_mask, es_antenna)

    acc = EPFDStreamAccumulator()
    results: list[EPFDTimeStepResult] | None = [] if keep_full_history else None
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
        _contrib_idx: list[int] = []
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
                per_system_out=per_system_step,
                contributing_idx_out=_contrib_idx,
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
        _contrib_idx_arr = np.asarray(_contrib_idx, dtype=np.int64)
        acc.add(
            time_s=t_s,
            epfd_db=epfd_agg_db,
            duration_s=dt_weight,
            num_horizon_sats=num_horizon,
            num_visible_sats=num_visible,
            num_contributing_sats=num_contributing,
            min_alpha_deg=min_alpha,
            is_fine=(dt <= dual_ts_fine_s + 1e-12),
            contrib_sat_idx=_contrib_idx_arr,
            contrib_elev_deg=np.degrees(np.arcsin(np.clip(sin_el[_contrib_idx_arr], -1.0, 1.0))),
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

    constellation, wcgs, pfd_mask, es_antenna = _epfd_shared_fields(
        constellation, wcgs, pfd_mask, es_antenna)

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

            _contrib_idx: list[int] = []
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
                contributing_idx_out=_contrib_idx,
            )

            epfd_sum_linear = sum(standard_epfd) + sum(override_epfd)
            num_contributing = len(standard_epfd) + len(override_epfd)

            epfd_agg_db = (
                10.0 * math.log10(epfd_sum_linear) if epfd_sum_linear > 0 else -999.0
            )
            _contrib_idx_arr = np.asarray(_contrib_idx, dtype=np.int64)
            accs[i].add(
                time_s=t_s,
                epfd_db=epfd_agg_db,
                duration_s=tstep_s,
                num_horizon_sats=num_horizon,
                num_visible_sats=num_visible,
                num_contributing_sats=num_contributing,
                min_alpha_deg=min_alpha,
                is_fine=True,  # fixed step (multi-ES): uniform Δt
                contrib_sat_idx=_contrib_idx_arr,
                contrib_elev_deg=np.degrees(np.arcsin(np.clip(sin_el[_contrib_idx_arr], -1.0, 1.0))),
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


# =====================================================================
#  Reference-vector satellite selection (US proposal R23-WP4A-C-0519)
# =====================================================================

def _get_lowest_avg_ref_vec_separation(
    constellation: list,
    t_s: float,
    tstep_s: float,
    T_steps: int,
    M: int,
    es_ecef: np.ndarray,
    es_lat_deg: float,
    es_lon_deg: float,
    sin_min_el: float,
    alpha0_deg: float,
    ref_az_deg: float,
    ref_el_deg: float,
    max_co_freq: int,
    wcg_ref_sat_idx: int,
    raan_dot_artificial_rad_s: float,
    raan_dot_override_rad_s: float | None,
    wdelta_deg: float,
    t_run_s: float,
    _prop_cache=None,
) -> list[int]:
    """Select up to *max_co_freq* satellites closest to the reference vector.

    Implements the GetLowestAvgRefVecSeparation algorithm from the US proposal
    R23-WP4A-C-0519 (replacing S.1503-4 Steps 19–21 for the reference-vector
    selection mode):

    1. Propagate the constellation at T_steps future time steps.
    2. Keep only satellites eligible (ε ≥ ε₀ AND α > α₀) at ALL future steps.
    3. Compute angular separation from the reference vector at each future step.
    4. Keep the M largest (worst) samples per satellite, average them.
    5. Sort by ascending average separation; return the top *max_co_freq* indices.

    The satellite that originated the WCG (``wcg_ref_sat_idx``) is always
    included in the result if eligible, consuming one of the *max_co_freq* slots.
    """
    N = len(constellation)
    if N == 0 or T_steps <= 0 or max_co_freq <= 0:
        return []

    M = max(1, min(M, T_steps))

    # Compute ENU rotation matrix from ES lat/lon (same convention as _simulate_chunk)
    lat_r = math.radians(es_lat_deg)
    lon_r = math.radians(es_lon_deg)
    sl, cl = math.sin(lat_r), math.cos(lat_r)
    so, co = math.sin(lon_r), math.cos(lon_r)
    R_enu = np.array([
        [-so,        co,       0.0],
        [-sl * co,  -sl * so,  cl ],
        [ cl * co,   cl * so,  sl ],
    ])

    # Propagate constellation at T_steps future times and collect per-step data.
    # sep_matrix[k, s] = angular separation of satellite k at step s (degrees)
    # eligible[k] = True only if ε ≥ ε₀ AND α > α₀ at ALL future steps
    sep_matrix = np.full((N, T_steps), 180.0, dtype=np.float64)
    eligible = np.ones(N, dtype=bool)

    for s in range(T_steps):
        t_future = t_s + s * tstep_s
        pos_future, _ = propagate_and_to_ecef_batch(
            constellation, t_future,
            raan_dot_artificial_rad_s=raan_dot_artificial_rad_s,
            raan_dot_override_rad_s=raan_dot_override_rad_s,
            _cache=_prop_cache,
            wdelta_deg=wdelta_deg,
            t_run_s=t_run_s,
        )

        # Elevation filter: sin_el = ENU_z / range
        diff = pos_future - es_ecef
        enu = (R_enu @ diff.T).T
        ranges = np.linalg.norm(diff, axis=1)
        ranges = np.maximum(ranges, 1e-15)
        sin_el = enu[:, 2] / ranges
        el_ok = sin_el >= sin_min_el

        # Alpha filter: use the fast batch function
        alpha_arr, _ = compute_alpha_and_optimal_gso_fixed_es_batch(
            es_ecef, pos_future, es_lat_deg, es_lon_deg, step_deg=1.0,
        )
        alpha_ok = np.abs(alpha_arr) > alpha0_deg

        # A satellite is eligible only if it passes BOTH criteria at every step
        eligible &= el_ok & alpha_ok

        # Angular separation from reference vector at this future step
        sep = compute_angular_separation_from_ref_vector(
            es_ecef, pos_future, es_lat_deg, es_lon_deg, ref_az_deg, ref_el_deg,
        )
        sep_matrix[:, s] = sep

    # For ineligible satellites, set separation to infinity so they sort last
    sep_matrix[~eligible] = np.inf

    # Average of the M worst (largest) separation samples per satellite
    sep_sorted = np.sort(sep_matrix, axis=1)[:, ::-1]  # descending per row
    avg_sep = np.mean(sep_sorted[:, :M], axis=1)

    # Force wcg_ref_sat_idx ineligibility to inf if truly ineligible
    if not eligible[wcg_ref_sat_idx]:
        avg_sep[wcg_ref_sat_idx] = np.inf

    # Build ranked list: wcg_ref_sat_idx first (if eligible), then fill remaining slots
    eligible_any = eligible.copy()
    ranked = list(np.argsort(avg_sep))

    result: list[int] = []
    ref_eligible = bool(eligible_any[wcg_ref_sat_idx])
    if ref_eligible:
        result.append(int(wcg_ref_sat_idx))

    for idx in ranked:
        if len(result) >= max_co_freq:
            break
        if int(idx) == wcg_ref_sat_idx:
            continue
        if avg_sep[idx] < np.inf:
            result.append(int(idx))

    return result


def _compute_epfd_for_selected_sats(
    selected_idx: list[int],
    pos_ecef_all: np.ndarray,
    vel_ecef_all: np.ndarray,
    es_ecef: np.ndarray,
    es_lat_deg: float,
    es_lon_deg: float,
    gso_ecef: np.ndarray,
    pfd_mask: PFDMask,
    es_antenna: EarthStationAntenna,
    pfd_bw_correction_db: float,
    t_s: float,
) -> tuple[float, float, int]:
    """Compute aggregate EPFD↓ for a pre-selected, fixed set of satellites.

    This is used when reference-vector selection is active: the satellite set was
    chosen by ``_get_lowest_avg_ref_vec_separation`` and is held for T seconds.
    There is no eligibility re-check, no MAX_CO_FREQ cap, and no OR condition —
    the selection is already the operator's complete policy choice.

    Returns ``(epfd_dBW, min_alpha_deg, num_contributing)``.
    """
    if not selected_idx:
        return -999.0, 180.0, 0

    epfd_sum = 0.0
    min_alpha = 180.0
    n_contrib = 0

    for k in selected_idx:
        pos_ecef = pos_ecef_all[k]
        vel_ecef = vel_ecef_all[k]

        alpha = compute_alpha_angle_fast_components(
            es_x=float(es_ecef[0]),
            es_y=float(es_ecef[1]),
            es_z=float(es_ecef[2]),
            ng_x=float(pos_ecef[0]),
            ng_y=float(pos_ecef[1]),
            ng_z=float(pos_ecef[2]),
            es_lat_deg=es_lat_deg,
            es_lon_deg=es_lon_deg,
        )
        if abs(alpha) < abs(min_alpha):
            min_alpha = alpha

        offaxis = compute_offaxis_angle(es_ecef, pos_ecef, gso_ecef)
        theta_planar = None
        if es_antenna.requires_planar_angle:
            _, theta_planar = compute_offaxis_and_planar_angle(
                es_ecef, pos_ecef, gso_ecef, es_lat_deg, es_lon_deg,
            )

        subsat_lat, subsat_lon, _ = ecef_to_lla(pos_ecef)
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
            sat_local_frame=None,
            sat_idx=int(k),
        )
        epfd_i = 10.0 ** (pfd_db / 10.0) * es_antenna.relative_gain_linear(offaxis, theta_planar)
        epfd_sum += epfd_i
        n_contrib += 1

    epfd_db = 10.0 * math.log10(epfd_sum) if epfd_sum > 0.0 else -999.0
    return epfd_db, min_alpha, n_contrib


def _simulate_ref_vec_window_block(args):
    """Chunk worker for reference-vector selection (US proposal R23-WP4A-C-0519),
    parallel over blocks of whole hold-windows sized from MIN_DURATION/N_SW.

    Each hold-window is self-contained: ``_get_lowest_avg_ref_vec_separation``
    is a pure function of the window's own start time (no dependency on any
    prior window's selection), so a block boundary landing on a window
    boundary needs no halo/shared state — the same guarantee
    ``_simulate_window_block`` already relies on for the worst-case §D5.1.4.2
    variant. Returns ``{"acc": EPFDStreamAccumulator}`` — a single timeline,
    not per-window-set stats, since reference-vector mode never uses the
    N_TW sliding-window-set envelope (see
    ``artifacts/WP4A_519_track_duration_consolidation_decision.md``).
    """
    (blk_win_start, blk_win_count, windows, constellation, wcg, pfd_mask,
     es_antenna, alpha0_deg, min_elevation_deg, pfd_bw_correction_db,
     ref_az_deg, ref_el_deg, ref_vec_time_window_P_pct, wcg_ref_sat_idx,
     raan_dot_artificial_rad_s, raan_dot_override_rad_s,
     max_co_freq_by_lat, wdelta_deg, t_run_s) = args

    constellation, wcg, pfd_mask, es_antenna = _epfd_shared_fields(
        constellation, wcg, pfd_mask, es_antenna)

    T_steps = windows.n_sw
    t_fine = windows.t_fine_s
    M = max(1, math.floor(T_steps * ref_vec_time_window_P_pct / 100.0))

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
    max_co_freq = _resolve_max_co_freq(es_lat, max_co_freq_by_lat or [])

    _prop_cache = build_constellation_cache(
        constellation, raan_dot_override_rad_s=raan_dot_override_rad_s,
    )

    acc = EPFDStreamAccumulator()
    n_steps_total = windows.n_steps_stats

    for win_idx in range(blk_win_start, blk_win_start + blk_win_count):
        window_t0 = win_idx * T_steps * t_fine
        selected_idx = _get_lowest_avg_ref_vec_separation(
            constellation=constellation,
            t_s=window_t0,
            tstep_s=t_fine,
            T_steps=T_steps,
            M=M,
            es_ecef=es_ecef,
            es_lat_deg=es_lat,
            es_lon_deg=es_lon,
            sin_min_el=sin_min_el,
            alpha0_deg=alpha0_deg,
            ref_az_deg=ref_az_deg,
            ref_el_deg=ref_el_deg,
            max_co_freq=max_co_freq,
            wcg_ref_sat_idx=wcg_ref_sat_idx,
            raan_dot_artificial_rad_s=raan_dot_artificial_rad_s,
            raan_dot_override_rad_s=raan_dot_override_rad_s,
            wdelta_deg=wdelta_deg,
            t_run_s=t_run_s,
            _prop_cache=_prop_cache,
        )
        selected_arr = np.asarray(selected_idx, dtype=np.int64)

        for s in range(T_steps):
            g = win_idx * T_steps + s
            if g >= n_steps_total:
                break
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
            num_horizon = int(np.count_nonzero(sin_el >= 0.0))
            num_visible = int(np.count_nonzero(sin_el >= sin_min_el))

            epfd_db, min_alpha, num_contrib = _compute_epfd_for_selected_sats(
                selected_idx=selected_idx,
                pos_ecef_all=pos_ecef_all,
                vel_ecef_all=vel_ecef_all,
                es_ecef=es_ecef,
                es_lat_deg=es_lat,
                es_lon_deg=es_lon,
                gso_ecef=gso_ecef,
                pfd_mask=pfd_mask,
                es_antenna=es_antenna,
                pfd_bw_correction_db=pfd_bw_correction_db,
                t_s=t_s,
            )
            acc.add(
                time_s=t_s,
                epfd_db=epfd_db,
                duration_s=t_fine,
                num_horizon_sats=num_horizon,
                num_visible_sats=num_visible,
                num_contributing_sats=num_contrib,
                min_alpha_deg=min_alpha,
                is_fine=True,
                contrib_sat_idx=selected_arr,
                contrib_elev_deg=np.degrees(np.arcsin(
                    np.clip(sin_el[selected_arr], -1.0, 1.0))),
            )

    return {"acc": acc}


def run_epfd_simulation_ref_vec(
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
    wdelta_deg: float = 0.0,
    t_run_s: float = 0.0,
    ref_vec_az_deg: float = 0.0,
    ref_vec_el_deg: float = 90.0,
    ref_vec_time_window_P_pct: float = 100.0,
    wcg_ref_sat_idx: int = 0,
) -> EPFDSimulationResult:
    """EPFD↓ with reference-vector satellite selection (US proposal
    R23-WP4A-C-0519), sharing the §D5.1.4.2 ``MIN_DURATION``/``N_SW``
    windowing machinery with ``run_epfd_simulation_windowed`` as the sole
    hold-duration source — see
    ``artifacts/WP4A_519_track_duration_consolidation_decision.md``.

    Unlike the worst-case §D5.1.4.2 variant: no Step-22 OR/sidelobe branch
    (the reference-vector selection is the operator's complete policy — see
    ``_compute_epfd_for_selected_sats``), and no N_TW sliding-window-set
    envelope (``windows`` must have ``n_tw == 1``; build it via
    ``compute_track_duration_windows(..., single_set=True)``).

    Parallelism is over **blocks of whole hold-windows**: each window's
    selection (``_get_lowest_avg_ref_vec_separation``) is a pure function of
    its own start time, so blocks are fully self-contained — no cross-task
    state, bit-identical whether run standalone, via a local Pool, or via an
    injected cluster executor.
    """
    if windows.n_tw != 1:
        raise ValueError(
            "run_epfd_simulation_ref_vec requires a single-set TrackDurationWindows "
            "(compute_track_duration_windows(..., single_set=True)); got "
            f"n_tw={windows.n_tw}."
        )

    result = EPFDSimulationResult(wcg=wcg)
    result.windows = windows

    es_ecef = _es_ecef_from_wcg(wcg)
    if np.linalg.norm(es_ecef) < RE_KM * 0.9:
        logger.warning("ES position invalid, aborting reference-vector simulation.")
        return result

    import multiprocessing
    if n_jobs < 1:
        n_jobs = multiprocessing.cpu_count()

    total_windows = windows.n_repeat
    if n_jobs <= 1:
        win_per_block = total_windows
    else:
        target_blocks = min(1024, max(n_jobs * 8, n_jobs))
        win_per_block = max(1, int(math.ceil(total_windows / target_blocks)))

    # constellation/wcg/pfd_mask/es_antenna are shipped once per worker (see
    # _EPFD_SHARED) rather than embedded in every block task below.
    _set_epfd_shared(constellation, wcg, pfd_mask, es_antenna)

    tasks = []
    b = 0
    while b < total_windows:
        cnt = min(win_per_block, total_windows - b)
        tasks.append((
            b, cnt, windows, None, None, None, None,
            alpha0_deg, min_elevation_deg, pfd_bw_correction_db,
            ref_vec_az_deg, ref_vec_el_deg, ref_vec_time_window_P_pct,
            wcg_ref_sat_idx, raan_dot_artificial_rad_s, raan_dot_override_rad_s,
            max_co_freq_by_lat or [], wdelta_deg, t_run_s,
        ))
        b += cnt

    logger.info(
        "Starting EPFD↓ reference-vector simulation (R23-WP4A-C-0519): "
        "N_SW=%d (%.1fs hold), N_Repeat=%d windows, %d sats, T_fine=%.4fs, "
        "%d parallel blocks (%d windows each) over %d jobs.",
        windows.n_sw, windows.min_duration_s, windows.n_repeat,
        len(constellation), windows.t_fine_s, len(tasks), win_per_block, n_jobs,
    )

    if n_jobs == 1:
        for task in tasks:
            result.acc.merge(_simulate_ref_vec_window_block(task)["acc"])
    elif _EPFD_EXECUTOR is not None:
        logger.info(
            "  Reference-vector dispatch via injected executor (%d blocks)", len(tasks)
        )
        for res in _EPFD_EXECUTOR(
            _simulate_ref_vec_window_block, tasks, _epfd_executor_init(shared=_EPFD_SHARED)
        ):
            result.acc.merge(res["acc"])
    else:
        numba_thr = _compute_epfd_numba_threads(n_jobs)
        with multiprocessing.Pool(
            processes=min(n_jobs, len(tasks)),
            initializer=_epfd_pool_initializer,
            initargs=(_epfd_global_snapshot(numba_thr, shared=_EPFD_SHARED),),
        ) as pool:
            for res in pool.imap(_simulate_ref_vec_window_block, tasks):
                result.acc.merge(res["acc"])

    result.acc.finalize_decimated()
    result.build_cdf()

    total_duration_s = windows.n_steps_stats * windows.t_fine_s
    logger.info(
        "Reference-vector simulation complete: %d steps, duration=%.1fs (%.2fh)",
        result.acc.n_steps, total_duration_s, total_duration_s / 3600.0,
    )
    return result


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
    on_chunk: "Callable[[EPFDStreamAccumulator, int, int], None] | None" = None,
    selection_config: "SelectionConfig | None" = None,
    alpha_tss: "object | None" = None,
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

    ``alpha_tss``: for ``strategy == "alpha_table"``, a per-sub-run mutable
    ``TSSAccumulator`` (Doc 4A/312). It carries state across all steps, so the
    run is forced serial (``n_jobs = 1``); parallelism happens *between* the 7
    envelope tables, one accumulator each.

    Reference-vector satellite selection (US proposal R23-WP4A-C-0519) is
    handled by the separate ``run_epfd_simulation_ref_vec`` — it shares the
    §D5.1.4.2 ``MIN_DURATION``/``N_SW`` windowing with
    ``run_epfd_simulation_windowed`` rather than this function's own
    per-step loop. See
    ``artifacts/WP4A_519_track_duration_consolidation_decision.md``.
    """
    result = EPFDSimulationResult(wcg=wcg, keep_full_history=keep_full_history)

    _strategy = (
        getattr(selection_config, "strategy", "s1503")
        if selection_config is not None else "s1503"
    )

    # Random strategies draw from a per-step RNG keyed by (seed, step_index).
    # With the dual time step, chunks are split by *time* and each step's Δt is
    # data-dependent (fine near the WCG, coarse elsewhere), so there is no
    # chunk-independent global step index — the parallel dual path cannot honour
    # the (seed, step_index) reproducibility contract. The *sequential* dual walk
    # can: step_index = step_count is a clean monotonic counter. So allow the
    # dual step for these strategies but force serial execution (n_jobs=1),
    # mirroring the alpha_table serial rule. The 's1503' and 'alpha_table' paths
    # are untouched.
    _random_strat = _strategy in ("top_n_elev_random", "hybrid_rand_he")
    if _random_strat and dual_ts is not None and n_jobs != 1:
        logger.info(
            "%s + dual time step: forcing n_jobs=1 (the per-step RNG needs a "
            "chunk-independent step index, only guaranteed by the serial walk).",
            _strategy,
        )
        n_jobs = 1

    # Alpha table + dual step (Decision 8): supported in the s1503_gain mode
    # only, where Δt is decided *before* selection so the TSS step weight
    # w = Δt/T_fine is well defined. The legacy alpha_threshold mode decides Δt
    # *after* the accumulate (from min_alpha), leaving the weight undefined — so
    # that combination stays unsupported.
    if (
        _strategy == "alpha_table"
        and dual_ts is not None
        and getattr(dual_ts, "mode", "") != "s1503_gain"
    ):
        raise NotImplementedError(
            "alpha_table supports the dual time step only in the 's1503_gain' "
            f"mode (got '{getattr(dual_ts, 'mode', '?')}'), because the TSS step "
            "weight must be known before selection. Use dual mode 's1503' or "
            "disable the dual step."
        )

    # Alpha table is stateful across the whole run (TSS credit vector) → serial.
    _is_alpha = (
        selection_config is not None
        and getattr(selection_config, "strategy", "s1503") == "alpha_table"
    )
    if _is_alpha:
        if alpha_tss is None:
            raise ValueError(
                "alpha_table strategy requires an alpha_tss accumulator "
                "(built per sub-run by the envelope orchestrator)."
            )
        if n_jobs != 1:
            logger.info("alpha_table: forcing n_jobs=1 (TSS state is serial).")
            n_jobs = 1

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

            # Fine-equivalent weight of this step (Decision 8): 1 on a fixed/fine
            # step, Ncoarse on a coarse dual step. In s1503_gain mode Δt is final
            # here (decided before selection); alpha_threshold is rejected upstream
            # for the alpha table, so this weight is always well defined for it.
            alpha_step_weight = (dt / dual_ts.fine) if dual_ts is not None else 1.0

            # Step 9bis: accrue TSS credit once per time step (before selection),
            # scaled by the step's fine-equivalent duration.
            if alpha_tss is not None:
                alpha_tss.update(weight=alpha_step_weight)

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
            _contrib_idx: list[int] = []
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
                    per_system_out=per_system_step,
                    selection_config=selection_config,
                    step_index=step_count,
                    alpha_tss=alpha_tss,
                    alpha_step_weight=alpha_step_weight,
                    contributing_idx_out=_contrib_idx,
                )
            )
            epfd_sum_linear = sum(standard_epfd) + sum(override_epfd)
            num_contributing = len(standard_epfd) + len(override_epfd)
            epfd_aggregate_dBW = (
                10.0 * math.log10(epfd_sum_linear) if epfd_sum_linear > 0 else -999.0
            )
            _contrib_idx_arr = np.asarray(_contrib_idx, dtype=np.int64)
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
                contrib_sat_idx=_contrib_idx_arr,
                contrib_elev_deg=np.degrees(np.arcsin(np.clip(sin_el[_contrib_idx_arr], -1.0, 1.0))),
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

        _set_epfd_shared(constellation, wcg, pfd_mask, es_antenna)

        chunks_dts = []
        t_c = 0.0
        while t_c < T_total - 1e-9:
            t_end_c = min(t_c + chunk_dur, T_total)
            chunks_dts.append((
                t_c, t_end_c,
                dual_ts.mode, dual_ts.fine, dual_ts.coarse, dual_ts.ncoarse,
                gain_thr, dual_ts.threshold,
                None, None, None, None,
                alpha0_deg, min_elevation_deg, pfd_bw_correction_db,
                raan_dot_artificial_rad_s, raan_dot_override_rad_s,
                max_co_freq_by_lat or [], strict_max_co_freq_total,
                strict_exclusion_zone, min_angle_at_es_deg,
                wdelta_deg, t_run_s, gso_min_elevation_deg,
                keep_full_history,
                system_id_per_sat, max_co_freq_by_system,
                min_el_all, alpha0_all,
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
            for batch_res in _EPFD_EXECUTOR(
                _simulate_chunk_dual_ts, chunks_dts, _epfd_executor_init(shared=_EPFD_SHARED)
            ):
                _consume_dual(batch_res)
        else:
            with multiprocessing.Pool(
                processes=n_jobs,
                initializer=_epfd_pool_initializer,
                initargs=(_epfd_global_snapshot(numba_thr, shared=_EPFD_SHARED),),
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
        _set_epfd_shared(constellation, wcg, pfd_mask, es_antenna)

        chunks = []
        current_step = 0
        current_t = 0.0

        while current_step < nsteps:
            count = min(steps_per_job, nsteps - current_step)
            chunks.append((
                current_step, count, current_t, tstep_s,
                None, None, None, None,
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
                selection_config,
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
            for batch_res in _EPFD_EXECUTOR(
                _simulate_chunk, chunks, _epfd_executor_init(shared=_EPFD_SHARED)
            ):
                _consume_fixed(batch_res)
        else:
            with multiprocessing.Pool(
                processes=n_jobs,
                initializer=_epfd_pool_initializer,
                initargs=(_epfd_global_snapshot(numba_thr, shared=_EPFD_SHARED),),
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
    ranked = sorted(eligible, key=lambda k: peak.get(k, 0.0), reverse=True)
    if max_co_freq and max_co_freq > 0:
        selected = set(ranked[:max_co_freq])
    else:
        selected = set(ranked)  # 0 ⇒ unlimited

    # Steps 21–22: per-step aggregate over the fixed tracked set plus OR sats.
    # OR contributors are satellites NOT among the window-eligible/standard set
    # (Step 22 mirrors §D5.1.4.1 bullet ②: OR is the *alternative* to the α₀/ε₀
    # standard path). A window-eligible satellite dropped by the MAX_CO_FREQ cap
    # is genuinely dropped — it must not re-enter through the OR branch, else it
    # would defeat the cap. Selected sats count once (no double counting).
    for g, idx, epfd_lin, _std, orx in buffer:
        if g >= stats_end_step:
            # Window completes for eligibility, but this step is past the run
            # duration → excluded from statistics (§D5.1.4.2 Step 22 tail).
            continue
        agg = 0.0
        for j in range(idx.size):
            k = int(idx[j])
            if k in selected or (orx[j] and k not in eligible):
                agg += float(epfd_lin[j])
        epfd_db = 10.0 * math.log10(agg) if agg > 0.0 else -999.0
        win_stats.add(time_s=g * t_fine_s, epfd_db=epfd_db, duration_s=t_fine_s)


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
     system_id_per_sat, max_co_freq_by_system) = args

    constellation, wcg, pfd_mask, es_antenna = _epfd_shared_fields(
        constellation, wcg, pfd_mask, es_antenna)

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
        env = np.maximum(env, rev / total * 100.0)
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
    selection_config: "SelectionConfig | None" = None,
) -> EPFDSimulationResult:
    """EPFD↓ with the track-duration sliding-window variant (S.1503-4 §D5.1.4.2).

    Runs one independent simulation per slide-window set (``N_TW`` of them) and
    combines their CDFs into a worst-per-level envelope. Fine step only (the
    variant is defined in fine time steps; the dual time step of §D4.7.1 does
    not apply). Parallelism is over **blocks of whole windows**, which are
    self-contained — so the standalone loop, the built-in Pool and the injected
    cluster executor all produce identical statistics (no cross-task state).

    **Selection scope (§D5.1.4.2 Step 20).** Per-window selection is: full-window
    α₀/ε₀ eligibility (Step 19) → rank eligible by window-peak epfd↓ (Step 19bis)
    → keep the top ``MAX_CO_FREQ[lat]`` + the OR/sidelobe sats (Step 20/22). Step
    20 does **not** include the MIN_ANGLE_AT_ES pruning of §D5.1.4.1 Step 21, the
    non-normative ``strict_max_co_freq_total`` joint cap, or the Resolution-76
    per-system MAX_CO_FREQ partitioning — so those inputs are accepted (for a
    uniform call signature with the standard path) but **not applied here**, and
    a warning is emitted when any is active.
    """
    result = EPFDSimulationResult(wcg=wcg)
    result.windows = windows

    if selection_config is not None and getattr(selection_config, "strategy", "s1503") != "s1503":
        raise NotImplementedError(
            f"selection strategy '{selection_config.strategy}' is not yet supported "
            "on the track-duration windowed path (planned for Phase 2). "
            "Run without track duration (MIN_DURATION=0) for Phase 1."
        )

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
    if _ignored:
        logger.warning(
            "  §D5.1.4.2 windowed selection follows Step 20 (window-peak "
            "MAX_CO_FREQ + OR) and does NOT apply: %s. These are honoured only "
            "on the standard §D5.1.4.1 path.",
            ", ".join(_ignored),
        )

    max_co_freq_by_system: dict[int, int] | None = None
    if system_id_per_sat is not None and max_co_freq_by_lat_per_system is not None:
        max_co_freq_by_system = {
            int(sid): _resolve_max_co_freq(wcg.es_lat_deg, tbl or [])
            for sid, tbl in enumerate(max_co_freq_by_lat_per_system)
        }

    import multiprocessing
    if n_jobs < 1:
        n_jobs = multiprocessing.cpu_count()

    # Parallel unit = a block of whole windows within a set. Windows are
    # independent, so blocks fan out with no halo/shared state — and this yields
    # parallelism even for a single window set (N_TW == 1, the common case for a
    # small MIN_DURATION). Size the blocks so the fleet stays busy.
    total_windows = windows.n_tw * windows.n_repeat
    if n_jobs <= 1:
        win_per_block = windows.n_repeat  # one block per set (sequential)
    else:
        target_blocks = min(1024, max(n_jobs * 8, n_jobs))
        win_per_block = max(1, int(math.ceil(total_windows / target_blocks)))

    _set_epfd_shared(constellation, wcg, pfd_mask, es_antenna)

    tasks = []
    for w in range(windows.n_tw):
        b = 0
        while b < windows.n_repeat:
            cnt = min(win_per_block, windows.n_repeat - b)
            tasks.append((
                w, b, cnt, windows, None, None, None, None,
                alpha0_deg, min_elevation_deg, pfd_bw_correction_db,
                raan_dot_artificial_rad_s, raan_dot_override_rad_s,
                max_co_freq_by_lat or [], strict_max_co_freq_total, strict_exclusion_zone,
                min_angle_at_es_deg, wdelta_deg, t_run_s, gso_min_elevation_deg,
                system_id_per_sat, max_co_freq_by_system,
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
    if total_prop >= 5e8:
        logger.warning(
            "  Track-duration variant is heavy: ≈%.3g step-propagations "
            "(no dual-step acceleration — this variant is defined in fine "
            "steps). Prefer the cluster or a coarser MIN_DURATION/T_fine ratio.",
            float(total_prop),
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
        for res in _EPFD_EXECUTOR(
            _simulate_window_block, tasks, _epfd_executor_init(shared=_EPFD_SHARED)
        ):
            _consume(res)
    else:
        numba_thr = _compute_epfd_numba_threads(n_jobs)
        with multiprocessing.Pool(
            processes=min(n_jobs, len(tasks)),
            initializer=_epfd_pool_initializer,
            initargs=(_epfd_global_snapshot(numba_thr, shared=_EPFD_SHARED),),
        ) as pool:
            # Ordered imap: blocks merge in task order in every path (sequential,
            # Pool, executor) so statistics stay bit-identical across modes.
            for res in pool.imap(_simulate_window_block, tasks):
                _consume(res)

    window_stats = [w for w in win_by_index if w is not None]
    result.window_stats = window_stats
    result.per_window_ccdf = [w.build_ccdf() for w in window_stats]

    # Headline CCDF = worst-per-level envelope (correct go/no-go across sets).
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
        result.acc = headline

    logger.info(
        "Windowed simulation complete: %d/%d sets, envelope points=%d",
        len(window_stats), windows.n_tw, bins_desc.size,
    )
    return result


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
    selection_config: "SelectionConfig | None" = None,
) -> list[EPFDSimulationResult]:
    """Runs the EPFD↓ simulation for multiple ES/WCG with shared dynamics.

    Note: when ``dual_ts`` is active, each ES may follow different time
    steps (dependent on ``min_alpha``). In that case, it is not possible to share
    exactly the same timeline and the computation falls back to individual runs.
    """
    if len(wcgs) == 0:
        return []

    if selection_config is not None and getattr(selection_config, "strategy", "s1503") != "s1503":
        raise NotImplementedError(
            f"selection strategy '{selection_config.strategy}' is not yet supported "
            "on the shared-dynamics multi-ES path (Phase 1). It runs on the "
            "single-ES fixed-step path; run each ES separately for now."
        )

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
        _set_epfd_shared(constellation, valid_wcgs, pfd_mask, es_antenna)

        chunks = []
        current_step = 0
        current_t = 0.0
        while current_step < nsteps:
            count = min(steps_per_job, nsteps - current_step)
            chunks.append((
                current_step, count, current_t, tstep_s,
                None, None, None, None,
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
            initargs=(_epfd_global_snapshot(numba_thr, shared=_EPFD_SHARED),),
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
    return float(pct[min(n_ge, pct.size) - 1])


def _epfd_at_exceedance_pct(sim_result: EPFDSimulationResult, pct_threshold: float) -> float:
    """Returns the CCDF EPFD at the requested exceedance percentage."""
    if len(sim_result.cdf_epfd_dBW) == 0:
        return -999.0
    if pct_threshold <= 0:
        return float(sim_result.cdf_epfd_dBW[0])
    if pct_threshold >= 100:
        return float(sim_result.cdf_epfd_dBW[-1])

    idx = np.searchsorted(sim_result.cdf_percentage, pct_threshold)
    if idx >= len(sim_result.cdf_epfd_dBW):
        return float(sim_result.cdf_epfd_dBW[-1])
    return float(sim_result.cdf_epfd_dBW[idx])


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

        # Table 17 row (§D7.3.2): Py = simulated probability of exceeding Ji,
        # read from the probability table; pass ⇔ Py ≤ Pi (equivalent to the
        # margin criterion above at the same specification point).
        py = _pct_exceeding_level(sim_result, limit_bin_dBW)
        table17.append({
            "Ji_dBW": float(limit_dBW),
            "Pi_pct": float(pct_threshold),
            "Py_pct": float(py),
            "pass": bool(margin >= 0.0),
        })

        if margin < 0.0:
            compliant = False
        if margin < worst_margin:
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