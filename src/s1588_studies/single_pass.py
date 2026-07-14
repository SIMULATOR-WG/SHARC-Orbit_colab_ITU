"""single_pass.py — Single-pass EPFD↓ engine with tiling (Study 2/3).

Inverts the grid sweep loops: instead of a full time simulation per point
(propagating the constellation from scratch at each point), it propagates the
constellation **once per timestep** and evaluates **all points of the tile**
with those same positions. Reuses the validated kernel
``_accumulate_epfd_visible_satellites`` → CCDF identical to the single-entry
engine (without per-point ΔM realignment).

Propagation cost:
    current (run/point):   N_geom × num_time_steps × N_sats
    single-pass tiled:     n_tiles × num_time_steps × N_sats   (n_tiles ≪ N_geom)

Memory bounded by ``tile_size`` (one EPFD accumulator per live tile point).
``tile_size`` is the only knob: large for a small problem (1 tile), tuned by
RAM for a large problem.

Modeling decision: this engine runs the real timeline at each point, **without**
optimizing the mean-anomaly offset (ΔM) per geometry — the time sweep covers the
constellation phases. It differs from the legacy method 2, which realigned per
point (incompatible with shared propagation).
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import numpy as np

_WCG_ROOT = Path(__file__).resolve().parents[2]
if str(_WCG_ROOT) not in sys.path:
    sys.path.insert(0, str(_WCG_ROOT))

from src.main import build_downlink_engine_inputs, DownlinkEngineInputs  # type: ignore[import]
from src.epfd_calculator import (  # type: ignore[import]
    _accumulate_epfd_visible_satellites,
    _min_operating_height_km_batch,
    _resolve_max_co_freq,
    _epfd_gso_min_elevation_active,
)
from src.orbit_propagator import (  # type: ignore[import]
    build_constellation_cache,
    propagate_and_to_ecef_batch,
)
from src.coordinates import lla_to_ecef, gso_position_ecef  # type: ignore[import]
from src.geometry import compute_elevation  # type: ignore[import]
from src.epfd_stream_accumulator import EPFDStreamAccumulator  # type: ignore[import]

from .geometry import GeometryPoint
from .percentiles import NORMATIVE_PERCENTAGES, extract_percentiles

RE_KM = 6378.137


@dataclass
class PointResult:
    index: int
    es_lat_deg: float
    es_lon_deg: float
    gso_lon_deg: float
    max_epfd_dbw_m2: float
    ccdf_bins_db: list[float]
    ccdf_pct: list[float]
    percentiles: dict[str, float]
    n_steps: int


@dataclass
class _PointState:
    """Per-point precomputation, reused across all timesteps of the tile."""
    geom: GeometryPoint
    index: int
    es_ecef: np.ndarray
    es_x: float
    es_y: float
    es_z: float
    R_enu: np.ndarray
    min_elevation_deg: float
    sin_min_el: float
    max_co_freq: int
    acc: EPFDStreamAccumulator = field(default_factory=EPFDStreamAccumulator)


def _make_point_state(
    geom: GeometryPoint, index: int, *, min_elevation_deg: float, max_co_freq_by_lat: list,
) -> _PointState:
    lat_r = math.radians(geom.es_lat_deg)
    lon_r = math.radians(geom.es_lon_deg)
    sl, cl = math.sin(lat_r), math.cos(lat_r)
    so, co = math.sin(lon_r), math.cos(lon_r)
    R_enu = np.array([
        [-so,       co,      0.0],
        [-sl * co, -sl * so,  cl],
        [cl * co,   cl * so,  sl],
    ])
    es_ecef = np.asarray(
        lla_to_ecef(geom.es_lat_deg, geom.es_lon_deg, 0.0), dtype=np.float64,
    )
    return _PointState(
        geom=geom,
        index=index,
        es_ecef=es_ecef,
        es_x=float(es_ecef[0]),
        es_y=float(es_ecef[1]),
        es_z=float(es_ecef[2]),
        R_enu=R_enu,
        min_elevation_deg=float(min_elevation_deg),
        sin_min_el=math.sin(math.radians(min_elevation_deg)),
        max_co_freq=_resolve_max_co_freq(geom.es_lat_deg, max_co_freq_by_lat or []),
    )


def _chunks(seq: list, size: int) -> Iterable[list]:
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def run_single_pass_grid(
    *,
    config: dict,
    geometries: list[GeometryPoint],
    num_time_steps: int,
    tstep_s: float,
    tile_size: int = 256,
    inputs: DownlinkEngineInputs | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
) -> list[PointResult]:
    """Run the single-pass tiled grid sweep.

    Args:
        config: filing cfg (same as used by ``run_s1503_pair``); engine inputs
            are built once via ``build_downlink_engine_inputs``.
        geometries: grid points (ES lat/lon + GSO lon).
        num_time_steps: time steps (fixed step).
        tstep_s: step duration (s).
        tile_size: number of points evaluated per tile (memory knob).
        inputs: pre-built inputs (optional; otherwise built from config).
        progress_cb(done, total): progress callback per completed point.
        cancel_cb() -> bool: interrupts between tiles/timesteps. Points of a
            tile interrupted mid-timeline are discarded (only points with the
            full timestep count are returned).

    Returns:
        List of ``PointResult`` in the order of ``geometries`` (possibly a
        prefix subset when cancelled).
    """
    if not geometries:
        return []

    eng = inputs if inputs is not None else build_downlink_engine_inputs(config)
    constellation = eng.constellation
    n_sat = len(constellation)

    # Time-propagation parameters — same as the single-entry engine to match
    # the timeline. raan_dot_artificial = 2π/T_run (sweeps RAAN over the duration).
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
    gso_active = _epfd_gso_min_elevation_active(eng.gso_min_elevation_deg)

    results: list[PointResult] = []
    total = len(geometries)
    done = 0

    indexed = list(enumerate(geometries))
    for tile in _chunks(indexed, max(1, int(tile_size))):
        if cancel_cb and cancel_cb():
            break
        states = [
            _make_point_state(
                g, idx,
                # Same precedence as run_epfd_at_geometry: a per-geometry
                # min_elevation_deg overrides the engine-wide value.
                min_elevation_deg=(
                    g.min_elevation_deg
                    if g.min_elevation_deg is not None
                    else eng.min_elevation_deg
                ),
                max_co_freq_by_lat=eng.max_co_freq_by_lat,
            )
            for idx, g in tile
        ]

        interrupted = False
        t_s = 0.0
        for step in range(num_time_steps):
            if cancel_cb and cancel_cb():
                interrupted = True
                break
            pos_all, vel_all = propagate_and_to_ecef_batch(
                constellation, t_s,
                raan_dot_artificial_rad_s=raan_dot_artificial,
                raan_dot_override_rad_s=eng.raan_dot_override_rad_s,
                _cache=prop_cache,
                wdelta_deg=wdelta_deg,
                t_run_s=t_run_s,
            )
            gso_cache: dict[float, np.ndarray] = {}

            for st in states:
                glon = st.geom.gso_lon_deg
                gso_ecef = gso_cache.get(glon)
                if gso_ecef is None:
                    gso_ecef = gso_position_ecef(glon, t_s)
                    gso_cache[glon] = gso_ecef

                diff = pos_all - st.es_ecef
                enu = (st.R_enu @ diff.T).T
                ranges = np.linalg.norm(diff, axis=1)
                sin_el = np.where(ranges > 1e-6, enu[:, 2] / ranges, -1.0)
                num_horizon = int(np.count_nonzero(sin_el >= 0.0))
                num_visible = int(np.count_nonzero(sin_el >= st.sin_min_el))

                visible_idx = np.where(sin_el >= 0.0)[0]
                if gso_active and visible_idx.size > 0:
                    el_gso = compute_elevation(
                        st.es_ecef, gso_ecef, st.geom.es_lat_deg, st.geom.es_lon_deg,
                    )
                    if el_gso < float(eng.gso_min_elevation_deg):
                        visible_idx = np.array([], dtype=np.int64)

                standard_epfd, override_epfd, min_alpha, _ = (
                    _accumulate_epfd_visible_satellites(
                        visible_idx=visible_idx,
                        pos_ecef_all=pos_all,
                        vel_ecef_all=vel_all,
                        es_ecef=st.es_ecef,
                        es_x=st.es_x,
                        es_y=st.es_y,
                        es_z=st.es_z,
                        es_lat_deg=st.geom.es_lat_deg,
                        es_lon_deg=st.geom.es_lon_deg,
                        gso_ecef=gso_ecef,
                        alpha0_deg=eng.alpha0_deg,
                        pfd_mask=eng.pfd_mask,
                        es_antenna=eng.es_antenna,
                        pfd_bw_correction_db=eng.pfd_bw_correction_db,
                        max_co_freq=st.max_co_freq,
                        strict_max_co_freq_total=eng.strict_max_co_freq_total,
                        subsat_lat_all=None,
                        subsat_lon_all=None,
                        sat_local_frames=None,
                        min_operating_height_km_all=min_h,
                        dual_ts=None,
                        t_s=t_s,
                        strict_exclusion_zone=eng.strict_exclusion_zone,
                        min_angle_at_es_deg=eng.min_angle_at_es_deg,
                        min_elevation_deg=st.min_elevation_deg,
                        sin_el_full=sin_el,
                    )
                )
                epfd_sum_linear = sum(standard_epfd) + sum(override_epfd)
                num_contributing = len(standard_epfd) + len(override_epfd)
                epfd_db = (
                    10.0 * math.log10(epfd_sum_linear)
                    if epfd_sum_linear > 0 else -999.0
                )
                st.acc.add(
                    time_s=t_s,
                    epfd_db=epfd_db,
                    duration_s=tstep_s,
                    num_horizon_sats=num_horizon,
                    num_visible_sats=num_visible,
                    num_contributing_sats=num_contributing,
                    min_alpha_deg=min_alpha,
                )
            t_s += tstep_s

        if interrupted:
            # Cancellation mid-tile: discard the partially accumulated points.
            # Emitting them would mix incomplete CCDFs (fewer timesteps) with
            # complete ones, indistinguishable downstream.
            break

        for st in states:
            bins_db_arr, pct_arr = st.acc.build_ccdf()
            bins_db = [float(x) for x in bins_db_arr]
            pct = [float(x) for x in pct_arr]
            percentiles: dict[str, float] = {}
            if bins_db:
                pct_frac = [p / 100.0 for p in pct]
                pdict = extract_percentiles(
                    list(reversed(bins_db)),
                    list(reversed(pct_frac)),
                    NORMATIVE_PERCENTAGES,
                )
                percentiles = {f"{p}%": float(v) for p, v in pdict.items()}
            results.append(PointResult(
                index=st.index,
                es_lat_deg=st.geom.es_lat_deg,
                es_lon_deg=st.geom.es_lon_deg,
                gso_lon_deg=st.geom.gso_lon_deg,
                max_epfd_dbw_m2=float(st.acc.epfd_max_db),
                ccdf_bins_db=bins_db,
                ccdf_pct=pct,
                percentiles=percentiles,
                n_steps=int(st.acc.n_steps),
            ))
            done += 1
            if progress_cb:
                progress_cb(done, total)

    results.sort(key=lambda r: r.index)
    return results
