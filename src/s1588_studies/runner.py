"""
runner.py — EPFD simulation with FIXED geometry (Study 2 / 3 of Resolution 76).

Wraps `run_epfd_simulation` (from `epfd_calculator`), taking a `GeometryPoint`
instead of a `WCGResult`. Useful for the per-geometry loop in Studies 2 and 3
where the WCG search is replaced by a systematic sweep of the grid
ES × GSO pointing (S.1503-4 §D.6).

Outputs: complete `EPFDSimulationResult` (CCDF, time series, aggregates).
The resulting CCDF is the source for `convolve_ccdf` in Study 2.
"""

from __future__ import annotations
from typing import Optional, TYPE_CHECKING
import math

import numpy as np

from ..coordinates import lla_to_ecef
from ..wcg_search import WCGResult
from ..epfd_calculator import EPFDSimulationResult, run_epfd_simulation
from .geometry import GeometryPoint

if TYPE_CHECKING:
    from ..orbit_propagator import OrbitalElements
    from ..pfd_mask import PFDMask
    from ..antenna import EarthStationAntenna
    from ..time_step import DualTimeStep


def geometry_to_wcg_result(geometry: GeometryPoint) -> WCGResult:
    """Build a minimal `WCGResult` from a `GeometryPoint`.

    Fills in `es_lat_deg`, `es_lon_deg`, `gso_lon_deg` and `es_ecef_exact`
    (the only fields read by `run_epfd_simulation`). The remaining fields stay
    at defaults to preserve an empty audit trail (there is no search).
    """
    es_ecef = np.asarray(
        lla_to_ecef(geometry.es_lat_deg, geometry.es_lon_deg, 0.0), dtype=np.float64
    )
    return WCGResult(
        theta_deg=0.0,
        phi_deg=0.0,
        es_lat_deg=geometry.es_lat_deg,
        es_lon_deg=geometry.es_lon_deg,
        gso_lon_deg=geometry.gso_lon_deg,
        alpha_deg=0.0,
        offaxis_deg=0.0,
        pfd_dBW=-999.0,
        es_gain_rel_dB=0.0,
        epfd_dBW=-999.0,
        elevation_deg=0.0,
        es_ecef_exact=es_ecef,
        es_lat_nominal=geometry.es_lat_deg,
        es_lon_nominal=geometry.es_lon_deg,
        gso_lon_nominal=geometry.gso_lon_deg,
        angular_velocity_deg_s=math.inf,
    )


def run_epfd_at_geometry(
    constellation: "list[OrbitalElements]",
    geometry: GeometryPoint,
    pfd_mask: "PFDMask",
    es_antenna: "EarthStationAntenna",
    *,
    num_time_steps: int,
    time_step_s: float,
    alpha0_deg: float = 0.0,
    min_elevation_deg: float = 0.0,
    dual_ts: "Optional[DualTimeStep]" = None,
    n_jobs: int = -1,
    pfd_bw_correction_db: float = 0.0,
    raan_dot_artificial_rad_s: float = 0.0,
    raan_dot_override_rad_s: Optional[float] = None,
    max_co_freq_by_lat: Optional[list] = None,
    strict_max_co_freq_total: bool = False,
    strict_exclusion_zone: bool = False,
    min_angle_at_es_deg: float = 0.0,
    wdelta_deg: float = 0.0,
    t_run_s: float = 0.0,
    gso_min_elevation_deg: float = -90.0,
    keep_full_history: bool = False,
) -> EPFDSimulationResult:
    """Run an EPFD↓ simulation on a fixed geometry (without WCG search).

    Same semantic signature as `run_epfd_simulation`, but:
        - ES/GSO geometry locked at `geometry`.
        - `nsteps` ← `num_time_steps`, `tstep_s` ← `time_step_s`.

    `num_time_steps` is the normative parameter for Studies 2/3 (Obs2 ref. 518 400).
    """
    if num_time_steps < 1:
        raise ValueError(f"num_time_steps must be >= 1: {num_time_steps}")
    if time_step_s <= 0:
        raise ValueError(f"time_step_s must be > 0: {time_step_s}")

    wcg = geometry_to_wcg_result(geometry)

    effective_min_elev = (
        geometry.min_elevation_deg
        if geometry.min_elevation_deg is not None
        else min_elevation_deg
    )

    return run_epfd_simulation(
        constellation=constellation,
        wcg=wcg,
        pfd_mask=pfd_mask,
        es_antenna=es_antenna,
        alpha0_deg=alpha0_deg,
        min_elevation_deg=effective_min_elev,
        tstep_s=time_step_s,
        nsteps=num_time_steps,
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
