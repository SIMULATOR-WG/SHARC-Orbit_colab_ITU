"""
nonNco_links.py

main functions:
  • get_valid_links: returns the links eligible for SL2SL EPFD calculation
  • select_links: returns a subset of links from the valid links that is
                  actually used for the EPFD statistics
  • plot_selected_links: visualization of the selected links
  • compute_ngso_offaxis_angle: gets the angle for antenna gain computation
                                for ngso sidelobe pfd
"""

import numpy as np
import math
from collections import defaultdict

from .coordinates import (
    angle_between,
    lla_to_ecef,
    unit_vector,
)
from .geometry import (
    compute_alpha_angle,
    compute_alpha_angle_fast_multi_es_batch,
)

_ES_CACHE = {}


def get_valid_links(deployed_ESs,
                    ngso_sat_ecef,
                    victim_es_ecef,
                    t_s,
                    sin_el,
                    *,
                    victim_exclusion_alpha_deg: float = 4.0,
                    min_elevation_deg: float = 25.0,
                    gso_arc_separation_deg: float = 20.0):
    """
    Finds all valid ES-satellite links for the SL2SL computation.

    A valid link satisfies:
        - NGSO sat outside the victim's exclusion zone
          (|alpha| > ``victim_exclusion_alpha_deg`` — pass the run's own α₀)
        - satellite above ``min_elevation_deg`` at the served ES
        - |alpha| at the served ES > ``gso_arc_separation_deg``
          (keeps served beams away from the GSO arc)

    Parameters
    ----------
    deployed_ESs : (M, 2) ndarray
        Rows of (lat_deg, lon_deg) of the served-cell grid.
    ngso_sat_ecef : (N, 3) ndarray of the constellation sats' ECEF
    victim_es_ecef : ECEF of the victim ES

    Returns
    -------
    valid_links : list
        List of selected tuples:
            (sat_ecef, ES_lat, ES_lon, elevation, es_ecef)
    """
    # ESs are fixed: ECEF computed once, not per time step. Cache keyed on
    # the grid CONTENT hash, not id() (id() is per-process and can be reused
    # after GC — meaningless under a spawn multiprocessing pool).
    arr = np.ascontiguousarray(np.asarray(deployed_ESs, dtype=np.float64))
    key = (arr.shape, hash(arr.tobytes()))
    if key not in _ES_CACHE:
        lat = arr[:, 0].copy()
        lon = arr[:, 1].copy()
        ecef = np.array([lla_to_ecef(la, lo, 0.0) for la, lo in zip(lat, lon)])
        rlat, rlon = np.radians(lat), np.radians(lon)
        up = np.stack([np.cos(rlat) * np.cos(rlon),
                       np.cos(rlat) * np.sin(rlon),
                       np.sin(rlat)], axis=1)
        _ES_CACHE.clear()  # one grid per run — no need to hold stale ones
        _ES_CACHE[key] = (lat, lon, ecef, up)
    ES_lat, ES_lon, es_ecef, es_up = _ES_CACHE[key]

    valid_links = []

    # Iterate over the visible satellites (sin_el >= 0)
    for sat_ecef in ngso_sat_ecef[sin_el >= 0]:
        # alpha of the NGSO as seen by the WCG victim ES
        alpha_with_victim = compute_alpha_angle(victim_es_ecef,
                                                sat_ecef,
                                                0,
                                                t_s)

        # Independent of the served ES: if it fails, every ES would fail
        if not np.abs(alpha_with_victim) > float(victim_exclusion_alpha_deg):
            continue

        # elevation at every served ES at once
        d = sat_ecef.reshape(1, 3) - es_ecef
        rng = np.linalg.norm(d, axis=1)
        elevation = np.degrees(np.arcsin(np.clip(
            np.einsum("ij,ij->i", d, es_up) / rng, -1.0, 1.0)))

        # cheap filter before alpha (elevation > threshold already implies > 0)
        cand = np.nonzero(elevation > float(min_elevation_deg))[0]
        if cand.size == 0:
            continue

        # alpha only for the candidates, batched
        alpha = compute_alpha_angle_fast_multi_es_batch(
            es_ecef[cand], sat_ecef, ES_lat[cand], ES_lon[cand])

        for i in cand[np.abs(alpha) > float(gso_arc_separation_deg)]:
            valid_links.append((sat_ecef,
                                ES_lat[i],
                                ES_lon[i],
                                elevation[i],
                                es_ecef[i]))

    return valid_links


def select_links(valid_links):
    """
    Greedily selects one valid link per Earth station while ensuring
    that each satellite is assigned at most once (NCo = 1).

    Parameters
    ----------
    valid_links : list
        List of tuples:
            (sat_ecef, ES_lat, ES_lon, elevation, es_ecef)

    Returns
    -------
    selected_links : list
        List of selected tuples:
            (sat_ecef, ES_lat, ES_lon, elevation, es_ecef)
    """

    # Group candidate links by Earth station
    es_candidates = defaultdict(list)

    for sat_ecef, es_lat, es_lon, elevation, es_ecef in valid_links:
        es_key = (es_lat, es_lon)

        es_candidates[es_key].append((elevation, sat_ecef, es_ecef))

    # Greedy assignment
    assigned_satellites = set()

    selected_links = []

    for es_key in es_candidates:
        es_lat, es_lon = es_key

        # Highest elevation first
        candidates = sorted(
            es_candidates[es_key],
            key=lambda x: x[0],
            reverse=True,
        )

        for elevation, sat_ecef, es_ecef in candidates:
            sat_key = tuple(np.asarray(sat_ecef))

            # Satellite already assigned?
            if sat_key in assigned_satellites:
                continue

            selected_links.append((sat_ecef, es_lat, es_lon, elevation, es_ecef))
            assigned_satellites.add(sat_key)

            # Move to next Earth station
            break

    return selected_links


def compute_ngso_offaxis_angle(
    ngso_sat_ecef: np.ndarray,
    serving_es_ecef: np.ndarray,
    victim_es_ecef: np.ndarray,
) -> np.array:
    """
    Off-axis angle of the NGSO transmit antenna.

    It is the angle between
        NGSO -> serving ES  (antenna boresight)
    and
        NGSO -> victim ES   (interference direction)
    """

    dir_serving = unit_vector(serving_es_ecef - ngso_sat_ecef)
    dir_victim = unit_vector(victim_es_ecef - ngso_sat_ecef)

    return math.degrees(
        angle_between(dir_serving, dir_victim)
    )


def compute_ngso_offaxis_angle_batch(
    ngso_sat_ecef: np.ndarray,
    serving_es_ecef: np.ndarray,
    victim_es_ecef: np.ndarray,
) -> np.ndarray:
    """
    Computes the off-axis angle (degrees) of each NGSO transmit antenna.

    Parameters
    ----------
    ngso_sat_ecef : (N,3) ndarray
        ECEF positions of the NGSO satellites.

    serving_es_ecef : (N,3) ndarray
        ECEF positions of the serving Earth station for each satellite.

    victim_es_ecef : (3,) ndarray
        ECEF position of the victim Earth station.

    Returns
    -------
    off_axis_deg : (N,) ndarray
        Off-axis angle (degrees) for each NGSO satellite.
    """

    # NGSO -> serving ES
    dir_serving = serving_es_ecef - ngso_sat_ecef

    # NGSO -> victim ES (broadcast victim ES over all satellites)
    dir_victim = victim_es_ecef[np.newaxis, :] - ngso_sat_ecef

    # Normalize row-wise
    dir_serving /= np.linalg.norm(dir_serving, axis=1, keepdims=True)
    dir_victim /= np.linalg.norm(dir_victim, axis=1, keepdims=True)

    # Row-wise dot product
    dot = np.sum(dir_serving * dir_victim, axis=1)
    dot = np.clip(dot, -1.0, 1.0)

    return np.degrees(np.arccos(dot))
