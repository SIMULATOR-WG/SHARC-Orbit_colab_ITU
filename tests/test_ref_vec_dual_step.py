"""Regression test: reference-vector selection always runs on a fixed
fine-step timeline, never dual-stepped.

Before the §D5.1.4.2/MIN_DURATION consolidation (see
artifacts/WP4A_519_track_duration_consolidation_decision.md), this was a
runtime interlock inside run_epfd_simulation: ref_vec_selection had to force
dual_ts=None because its hold_counter counted loop *iterations* (not elapsed
seconds) and its T-step lookahead sampled the future at a single dt
snapshot — both broken by dual time-stepping.

After the consolidation, reference-vector selection runs entirely through
run_epfd_simulation_ref_vec, which is windowed-dispatch only (like
run_epfd_simulation_windowed) and never accepts a dual_ts parameter at all —
so this is now a structural guarantee rather than a runtime check. This test
just confirms the resulting accumulator really is all-fine-step, no
coarse steps sneaking in via the windowed dispatch machinery.
"""
from __future__ import annotations

import math

import numpy as np

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.orbit_propagator import OrbitalElements  # type: ignore[import]
from src.wcg_search import WCGResult, lla_to_ecef  # type: ignore[import]
from src.antenna import ITURS1428Antenna  # type: ignore[import]
from src.pfd_mask import PFDMaskXML  # type: ignore[import]
from src.time_step import compute_track_duration_windows  # type: ignore[import]
from src.epfd_calculator import run_epfd_simulation_ref_vec  # type: ignore[import]


def _alpha_mask() -> PFDMaskXML:
    lats, alphas, dlons = [-60.0, 0.0, 60.0], [0.0, 2.0, 5.0, 10.0], [-5.0, 0.0, 5.0]
    x = ['<satellite_system><pfd_mask mask_id="1" type="alpha_deltaLongitude" '
         'refbw_khz="40" a_name="latitude" b_name="alpha" c_name="deltaLongitude">']
    for la in lats:
        x.append(f'<by_a a="{la}">')
        for a in alphas:
            x.append(f'<by_b b="{a}">')
            for d in dlons:
                x.append(f'<pfd c="{d}">{-150.0 - a - abs(d):.3f}</pfd>')
            x.append("</by_b>")
        x.append("</by_a>")
    x.append("</pfd_mask></satellite_system>")
    return PFDMaskXML.from_xml_content("\n".join(x), mask_id=1)


def _constellation(n=60, seed=0):
    rng = np.random.RandomState(seed)
    return [
        OrbitalElements(
            a=7178.0, e=0.0, i=math.radians(rng.uniform(20, 90)),
            raan=math.radians(rng.uniform(0, 360)), omega=0.0,
            M=math.radians(rng.uniform(0, 360)),
        )
        for _ in range(n)
    ]


def _wcg():
    return WCGResult(
        theta_deg=0, phi_deg=0, es_lat_deg=0.0, es_lon_deg=0.0, gso_lon_deg=0.0,
        alpha_deg=0, offaxis_deg=0, pfd_dBW=0, es_gain_rel_dB=0, epfd_dBW=0,
        elevation_deg=0, es_ecef_exact=lla_to_ecef(0.0, 0.0, 0.0),
    )


def test_ref_vec_selection_runs_all_fine_steps():
    ant = ITURS1428Antenna(1.2, 12.0, 0.65)
    nsteps = 100
    windows = compute_track_duration_windows(
        min_duration_s=5.0, t_fine_s=1.0, nsteps=nsteps,
        min_orbital_period_s=6000.0, n_satellites=60, single_set=True,
    )
    res = run_epfd_simulation_ref_vec(
        constellation=_constellation(), wcg=_wcg(), pfd_mask=_alpha_mask(),
        es_antenna=ant, alpha0_deg=2.0, min_elevation_deg=10.0,
        windows=windows, n_jobs=1, pfd_bw_correction_db=0.0,
        ref_vec_az_deg=0.0, ref_vec_el_deg=90.0, ref_vec_time_window_P_pct=100.0,
        wcg_ref_sat_idx=0,
    )
    a = res.acc
    # No dual-ts concept in this path at all: every step is fine, and the
    # step count matches nsteps 1:1.
    assert a.n_coarse_steps == 0
    assert a.n_fine_steps == a.n_steps == nsteps
