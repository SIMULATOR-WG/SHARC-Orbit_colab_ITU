"""vectorized_kernel.py — Vectorized EPFD↓ kernel [M_points × N_sats] (Tier 2).

Computes the EPFD contribution of all grid points of a tile in ONE time step,
with the constellation propagated 1×. The heavy geometry (angle α via GSO arc
sweep per ES↔sat pair) runs in a double njit loop M×N reusing the validated
scalar ``_compute_alpha_angle_numba_with_gso``; off-axis, elevation and S.1428
antenna gain are vectorized NumPy ops; the 3D PFD mask uses the existing
``get_pfd_batch`` over the flattened eligible pairs; the MAX_CO_FREQ selection
(Steps 19–22) reuses ``_finalize_epfd_after_max_co_freq`` per point.

Constraints:
    - alpha_deltaLongitude mask (3D) — batch path.
    - Antenna without planar angle (ITU-R S.1428). BO.1443 (planar) not supported.

Produces EPFD↓ identical (within numerical tolerance) to the single-entry engine
with ``_ALPHA_METHOD='sweep'`` (default) and without per-point ΔM realignment.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

_WCG_ROOT = Path(__file__).resolve().parents[2]
if str(_WCG_ROOT) not in sys.path:
    sys.path.insert(0, str(_WCG_ROOT))

from numba import njit, prange  # type: ignore[import]

from src.main import build_downlink_engine_inputs, DownlinkEngineInputs  # type: ignore[import]
from src.epfd_calculator import (  # type: ignore[import]
    _min_operating_height_km_batch,
    _resolve_max_co_freq,
    _epfd_gso_min_elevation_active,
    _finalize_epfd_after_max_co_freq,
    _pfd_mask_uses_alpha_delta_batch,
)
from src.geometry import (  # type: ignore[import]
    _compute_alpha_angle_numba_with_gso,
    delta_longitude_s1503_deg,
    compute_elevation,
    get_alpha_method,
    get_gso_longitude_mode,
)
from src.orbit_propagator import (  # type: ignore[import]
    build_constellation_cache,
    propagate_and_to_ecef_batch,
)
from src.coordinates import (  # type: ignore[import]
    lla_to_ecef, gso_position_ecef, ecef_to_lla_batch,
)
from src.antenna import ITURS1428Antenna  # type: ignore[import]
from src.epfd_stream_accumulator import EPFDStreamAccumulator  # type: ignore[import]

from .geometry import GeometryPoint
from .percentiles import NORMATIVE_PERCENTAGES, extract_percentiles
from .single_pass import PointResult

RE_KM = 6378.137


@njit(parallel=True, cache=True, fastmath=True)
def _alpha_gso_grid(
    es_x, es_y, es_z, es_lat, es_lon,
    ng_x, ng_y, ng_z, visible, step_deg,
):
    """α[M,N] and Long(GSO-α)[M,N] via GSO arc sweep per pair.

    Pairs with ``visible[m,n]==False`` get α=999 (sentinel ignored later).
    Reuses the validated njit scalar ``_compute_alpha_angle_numba_with_gso``.
    """
    M = es_x.shape[0]
    N = ng_x.shape[0]
    alpha = np.empty((M, N), dtype=np.float64)
    glon = np.zeros((M, N), dtype=np.float64)
    for m in prange(M):
        for n in range(N):
            if not visible[m, n]:
                alpha[m, n] = 999.0
                continue
            a, gx, gy, gz = _compute_alpha_angle_numba_with_gso(
                es_x[m], es_y[m], es_z[m],
                ng_x[n], ng_y[n], ng_z[n],
                es_lat[m], es_lon[m], step_deg,
            )
            alpha[m, n] = a
            glon[m, n] = math.degrees(math.atan2(gy, gx))
    return alpha, glon


def _s1428_relative_gain_db_batch(ant: ITURS1428Antenna, phi: np.ndarray) -> np.ndarray:
    """Relative gain (dB, G(φ)−Gmax) S.1428 vectorized for φ[...] in degrees."""
    p = np.minimum(np.abs(phi), 180.0)
    dl = ant.d_over_lambda
    g_max = ant.g_max
    g1 = ant.g1
    phi_m = ant.phi_m
    g = np.empty_like(p)
    region_main = p < phi_m
    g[region_main] = g_max - 2.5e-3 * (dl * p[region_main]) ** 2

    if ant.regime in ("20_25", "25_100"):
        ppm = ant.phi_plateau_max
        m_g1 = (~region_main) & (p < ppm)
        g[m_g1] = g1
        m_log = (~region_main) & (p >= ppm) & (p <= 33.1)
        g[m_log] = 29.0 - 25.0 * np.log10(np.maximum(p[m_log], 1e-12))
        m_80 = (~region_main) & (p > 33.1) & (p <= 80.0)
        g[m_80] = -9.0
        if ant.regime == "20_25":
            m_rest = (~region_main) & (p > 80.0)
            g[m_rest] = -5.0
        else:
            m_120 = (~region_main) & (p > 80.0) & (p <= 120.0)
            g[m_120] = -4.0
            m_gt120 = (~region_main) & (p > 120.0)
            g[m_gt120] = -9.0
    else:  # gt_100
        phi_r = ant.phi_r
        m_g1 = (~region_main) & (p < phi_r)
        g[m_g1] = g1
        m_a = (~region_main) & (p >= phi_r) & (p < 10.0)
        g[m_a] = 29.0 - 25.0 * np.log10(np.maximum(p[m_a], 1e-12))
        m_b = (~region_main) & (p >= 10.0) & (p < 34.1)
        g[m_b] = 34.0 - 30.0 * np.log10(np.maximum(p[m_b], 1e-12))
        m_c = (~region_main) & (p >= 34.1) & (p < 80.0)
        g[m_c] = -12.0
        m_d = (~region_main) & (p >= 80.0) & (p < 120.0)
        g[m_d] = -7.0
        m_e = (~region_main) & (p >= 120.0)
        g[m_e] = -12.0

    # φ≈0 → exact Gmax.
    g[p < 1e-10] = g_max
    return g - g_max


@dataclass
class _PointState:
    geom: GeometryPoint
    index: int
    es_ecef: np.ndarray
    R_enu: np.ndarray
    sin_min_el: float
    max_co_freq: int
    acc: EPFDStreamAccumulator = field(default_factory=EPFDStreamAccumulator)


def _make_state(g, idx, *, min_elev, max_co_freq_by_lat):
    lat_r, lon_r = math.radians(g.es_lat_deg), math.radians(g.es_lon_deg)
    sl, cl, so, co = math.sin(lat_r), math.cos(lat_r), math.sin(lon_r), math.cos(lon_r)
    R = np.array([[-so, co, 0.0], [-sl*co, -sl*so, cl], [cl*co, cl*so, sl]])
    es = np.asarray(lla_to_ecef(g.es_lat_deg, g.es_lon_deg, 0.0), dtype=np.float64)
    return _PointState(
        geom=g, index=idx, es_ecef=es, R_enu=R,
        sin_min_el=math.sin(math.radians(min_elev)),
        max_co_freq=_resolve_max_co_freq(g.es_lat_deg, max_co_freq_by_lat or []),
    )


def _chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def run_vectorized_grid(
    *,
    config: dict,
    geometries: list[GeometryPoint],
    num_time_steps: int,
    tstep_s: float,
    tile_size: int = 512,
    inputs: DownlinkEngineInputs | None = None,
    alpha_sweep_step_deg: float = 1.0,
    progress_cb: Callable[[int, int], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
) -> list[PointResult]:
    """Vectorized EPFD↓ grid sweep [M×N] with tiling.

    Same semantics as ``single_pass.run_single_pass_grid`` (real timeline, no
    ΔM realignment, shared propagation) but with the geometry α + off-axis +
    gain + mask evaluated in batch over [M_points × N_sats].
    """
    if not geometries:
        return []

    eng = inputs if inputs is not None else build_downlink_engine_inputs(config)
    # The α kernel hard-codes the GSO arc sweep (_compute_alpha_angle_numba_
    # with_gso); silently diverging from a different configured physics would
    # invalidate the comparison with the scalar engine.
    alpha_method = get_alpha_method()
    gso_lon_mode = get_gso_longitude_mode()
    if alpha_method != "sweep" or gso_lon_mode != "arc_optimal":
        raise ValueError(
            "Vectorized Tier 2 implements only alpha_method='sweep' with "
            "gso_longitude_mode='arc_optimal'; configured: "
            f"alpha_method={alpha_method!r}, "
            f"gso_longitude_mode={gso_lon_mode!r}. "
            "Use the single-pass engine for other modes."
        )
    if not _pfd_mask_uses_alpha_delta_batch(eng.pfd_mask):
        raise NotImplementedError(
            "Vectorized Tier 2 only supports the alpha_deltaLongitude mask (3D batch)."
        )
    if getattr(eng.es_antenna, "requires_planar_angle", False) or not isinstance(
        eng.es_antenna, ITURS1428Antenna
    ):
        raise NotImplementedError(
            "Vectorized Tier 2 only supports the ITU-R S.1428 antenna (without planar angle)."
        )

    constellation = eng.constellation
    n_sat = len(constellation)
    alpha0 = float(eng.alpha0_deg)
    bw = float(eng.pfd_bw_correction_db)
    min_elev = float(eng.min_elevation_deg)
    strict_excl = bool(eng.strict_exclusion_zone)
    g_rel_at_alpha0 = float(eng.es_antenna.relative_gain(alpha0, None))
    or_threshold_db = min(-30.0, g_rel_at_alpha0)
    gso_active = _epfd_gso_min_elevation_active(eng.gso_min_elevation_deg)

    t_run_s = float(num_time_steps) * float(tstep_s)
    raan_dot_artificial = (
        (2.0 * math.pi) / t_run_s
        if eng.artificial_precession and t_run_s > 0.0 else 0.0
    )
    wdelta_deg = eng.wdelta_deg_requested if eng.apply_station_keeping else 0.0

    prop_cache = build_constellation_cache(
        constellation, raan_dot_override_rad_s=eng.raan_dot_override_rad_s,
    )
    min_h = _min_operating_height_km_batch(constellation, n_sat)
    has_min_h = min_h is not None

    results: list[PointResult] = []
    total = len(geometries)
    done = 0

    for tile in _chunks(list(enumerate(geometries)), max(1, int(tile_size))):
        if cancel_cb and cancel_cb():
            break
        states = [
            _make_state(
                g, idx,
                # Same precedence as run_epfd_at_geometry: a per-geometry
                # min_elevation_deg overrides the engine-wide value.
                min_elev=(
                    g.min_elevation_deg
                    if g.min_elevation_deg is not None else min_elev
                ),
                max_co_freq_by_lat=eng.max_co_freq_by_lat,
            )
            for idx, g in tile
        ]
        M = len(states)
        es_ecef = np.array([s.es_ecef for s in states], dtype=np.float64)       # [M,3]
        es_x = np.ascontiguousarray(es_ecef[:, 0])
        es_y = np.ascontiguousarray(es_ecef[:, 1])
        es_z = np.ascontiguousarray(es_ecef[:, 2])
        es_lat = np.array([s.geom.es_lat_deg for s in states], dtype=np.float64)
        es_lon = np.array([s.geom.es_lon_deg for s in states], dtype=np.float64)
        gso_lon = np.array([s.geom.gso_lon_deg for s in states], dtype=np.float64)
        R_enu = np.stack([s.R_enu for s in states], axis=0)                     # [M,3,3]
        max_cf = np.array([s.max_co_freq for s in states], dtype=np.int64)
        sin_min_el = np.array([s.sin_min_el for s in states])[:, None]          # [M,1]
        # GSO ECEF positions: 1 call per unique longitude per step (not per point).
        uniq_gso_lon, inv_gso = np.unique(gso_lon, return_inverse=True)

        interrupted = False
        t_s = 0.0
        for step in range(num_time_steps):
            if cancel_cb and cancel_cb():
                interrupted = True
                break
            pos, _vel = propagate_and_to_ecef_batch(
                constellation, t_s,
                raan_dot_artificial_rad_s=raan_dot_artificial,
                raan_dot_override_rad_s=eng.raan_dot_override_rad_s,
                _cache=prop_cache, wdelta_deg=wdelta_deg, t_run_s=t_run_s,
            )                                                                    # [N,3]
            gso_uniq = np.stack([
                gso_position_ecef(float(lon), t_s) for lon in uniq_gso_lon
            ])                                                                   # [U,3]
            gso_real = gso_uniq[inv_gso]                                         # [M,3]
            ng_x = np.ascontiguousarray(pos[:, 0])
            ng_y = np.ascontiguousarray(pos[:, 1])
            ng_z = np.ascontiguousarray(pos[:, 2])
            subsat_lat, subsat_lon, _ = ecef_to_lla_batch(pos)                   # [N]
            sat_alt = np.linalg.norm(pos, axis=1) - RE_KM                        # [N]

            # Elevation [M,N]: ENU z / range.
            diff = pos[None, :, :] - es_ecef[:, None, :]                         # [M,N,3]
            enu_z = np.einsum("mij,mnj->mni", R_enu[:, 2:3, :], diff)[:, :, 0]   # [M,N]
            rng = np.linalg.norm(diff, axis=2)                                   # [M,N]
            sin_el = np.where(rng > 1e-6, enu_z / np.maximum(rng, 1e-12), -1.0)  # [M,N]

            num_horizon = np.count_nonzero(sin_el >= 0.0, axis=1)               # [M]
            num_visible = np.count_nonzero(sin_el >= sin_min_el, axis=1)         # [M]

            visible = sin_el >= 0.0                                              # [M,N]
            if has_min_h:
                visible &= (sat_alt[None, :] >= (min_h[None, :] - 1e-9))
            # GSO min elevation per point (zeros out all sats if ES does not see GSO).
            if gso_active:
                for m in range(M):
                    if not visible[m].any():
                        continue
                    if compute_elevation(es_ecef[m], gso_real[m], float(es_lat[m]), float(es_lon[m])) < float(eng.gso_min_elevation_deg):
                        visible[m, :] = False

            if not visible.any():
                for m, s in enumerate(states):
                    s.acc.add(
                        time_s=t_s, epfd_db=-999.0, duration_s=tstep_s,
                        num_horizon_sats=int(num_horizon[m]),
                        num_visible_sats=int(num_visible[m]),
                        num_contributing_sats=0, min_alpha_deg=180.0,
                    )
                t_s += tstep_s
                continue

            # α + Long(GSO-α) via njit M×N (sweep).
            alpha, glon_alpha = _alpha_gso_grid(
                es_x, es_y, es_z, es_lat, es_lon,
                ng_x, ng_y, ng_z, visible, float(alpha_sweep_step_deg),
            )                                                                    # [M,N]
            abs_alpha = np.abs(alpha)
            tmp = np.where(visible, abs_alpha, np.inf)
            jmin = np.argmin(tmp, axis=1)
            ma = alpha[np.arange(M), jmin]
            has_vis = visible.any(axis=1)
            min_alpha = np.where(has_vis, ma, 180.0)

            # Off-axis φ[M,N] relative to the real GSO (gso_lon).
            dg = gso_real - es_ecef                                              # [M,3]
            u_g = dg / np.maximum(np.linalg.norm(dg, axis=1, keepdims=True), 1e-15)
            u_n = diff / np.maximum(rng[:, :, None], 1e-15)                      # [M,N,3]
            cosphi = np.clip(np.einsum("mnj,mj->mn", u_n, u_g), -1.0, 1.0)
            phi = np.degrees(np.arccos(cosphi))                                  # [M,N]

            g_rel_db = _s1428_relative_gain_db_batch(eng.es_antenna, phi)        # [M,N]
            g_lin = 10.0 ** (g_rel_db / 10.0)

            eps0_ok = sin_el >= sin_min_el
            is_standard = visible & (abs_alpha >= alpha0) & eps0_ok
            if strict_excl:
                override = np.zeros_like(is_standard)
            else:
                override = visible & (~is_standard) & (g_rel_db > or_threshold_db)
            eligible = is_standard | override

            # PFD mask in batch over the flattened eligible pairs.
            mm, nn = np.nonzero(eligible)
            epfd_lin_flat = np.zeros(mm.shape[0], dtype=np.float64)
            if mm.size > 0:
                alpha_e = alpha[mm, nn]
                lat_e = subsat_lat[nn]
                lon_e = subsat_lon[nn]
                dlon_e = delta_longitude_s1503_deg(glon_alpha[mm, nn], lon_e)
                pfd_db = eng.pfd_mask.get_pfd_batch(
                    alpha_e, lat_e, dlon_e, sat_indices=nn,
                ) + bw
                epfd_lin_flat = (10.0 ** (pfd_db / 10.0)) * g_lin[mm, nn]

            # Regroup per point and finalize (Steps 19–22). ``mm`` is sorted
            # (np.nonzero is row-major), so per-point slices come from
            # searchsorted instead of a Python loop over every pair.
            std_flag = is_standard[mm, nn]
            bounds = np.searchsorted(mm, np.arange(M + 1))

            for m, s in enumerate(states):
                k0, k1 = int(bounds[m]), int(bounds[m + 1])
                flags = std_flag[k0:k1]
                vals = epfd_lin_flat[k0:k1]
                sats = nn[k0:k1]
                std_items = list(zip(vals[flags].tolist(), sats[flags].tolist()))
                ovr_items = vals[~flags].tolist()
                std_epfd, ovr_epfd = _finalize_epfd_after_max_co_freq(
                    std_items, ovr_items, int(max_cf[m]),
                    eng.strict_max_co_freq_total, eng.min_angle_at_es_deg,
                    es_ecef[m], pos,
                )
                tot = sum(std_epfd) + sum(ovr_epfd)
                epfd_db = 10.0 * math.log10(tot) if tot > 0 else -999.0
                s.acc.add(
                    time_s=t_s, epfd_db=epfd_db, duration_s=tstep_s,
                    num_horizon_sats=int(num_horizon[m]),
                    num_visible_sats=int(num_visible[m]),
                    num_contributing_sats=len(std_epfd) + len(ovr_epfd),
                    min_alpha_deg=float(min_alpha[m]),
                )
            t_s += tstep_s

        if interrupted:
            # Cancellation mid-tile: discard the partially accumulated points.
            # Emitting them would mix incomplete CCDFs (fewer timesteps) with
            # complete ones, indistinguishable downstream.
            break

        for s in states:
            bins_db_arr, pct_arr = s.acc.build_ccdf()
            bins_db = [float(x) for x in bins_db_arr]
            pct = [float(x) for x in pct_arr]
            percentiles: dict[str, float] = {}
            if bins_db:
                pf = [p / 100.0 for p in pct]
                pdict = extract_percentiles(
                    list(reversed(bins_db)), list(reversed(pf)), NORMATIVE_PERCENTAGES,
                )
                percentiles = {f"{p}%": float(v) for p, v in pdict.items()}
            results.append(PointResult(
                index=s.index, es_lat_deg=s.geom.es_lat_deg,
                es_lon_deg=s.geom.es_lon_deg, gso_lon_deg=s.geom.gso_lon_deg,
                max_epfd_dbw_m2=float(s.acc.epfd_max_db),
                ccdf_bins_db=bins_db, ccdf_pct=pct, percentiles=percentiles,
                n_steps=int(s.acc.n_steps),
            ))
            done += 1
            if progress_cb:
                progress_cb(done, total)

    results.sort(key=lambda r: r.index)
    return results
