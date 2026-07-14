"""Tests for the dual time step fine/coarse execution tally.

The accumulator now counts how many fine vs coarse steps the temporal loop
actually executed (``n_fine_steps`` / ``n_coarse_steps``), aggregated across
parallel chunks via ``merge``. These are surfaced in the run metadata
(``n_fine_steps_executed`` / ``n_coarse_steps_executed`` / ``n_exec_steps``)
and shown on the Results page.
"""
from __future__ import annotations

import math

import numpy as np

from src.epfd_stream_accumulator import EPFDStreamAccumulator  # type: ignore[import]
from src.orbit_propagator import OrbitalElements  # type: ignore[import]
from src.wcg_search import WCGResult, lla_to_ecef  # type: ignore[import]
from src.antenna import ITURS1428Antenna  # type: ignore[import]
from src.pfd_mask import PFDMaskXML  # type: ignore[import]
from src.time_step import DualTimeStep  # type: ignore[import]
from src.epfd_calculator import run_epfd_simulation  # type: ignore[import]


def _add(acc, t, is_fine):
    acc.add(time_s=t, epfd_db=-150.0, duration_s=1.0, num_horizon_sats=1,
            num_visible_sats=1, num_contributing_sats=1, min_alpha_deg=5.0,
            is_fine=is_fine)


def test_accumulator_counts_and_merge():
    a = EPFDStreamAccumulator()
    for i, f in enumerate([True, True, False, True, False, None]):
        _add(a, i, f)
    assert a.n_fine_steps == 3
    assert a.n_coarse_steps == 2  # the None step is not counted
    assert a.n_steps == 6

    b = EPFDStreamAccumulator()
    _add(b, 99, False)
    a.merge(b)
    assert a.n_fine_steps == 3
    assert a.n_coarse_steps == 3
    assert a.n_steps == 7


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


def test_sequential_dual_step_counts():
    ant = ITURS1428Antenna(1.2, 12.0, 0.65)
    dual = DualTimeStep(coarse_step_s=4.0, fine_step_s=1.0, mode="s1503_gain",
                        ncoarse=4, es_antenna=ant, alpha0_deg=2.0)
    nsteps = 400
    res = run_epfd_simulation(
        constellation=_constellation(), wcg=_wcg(), pfd_mask=_alpha_mask(),
        es_antenna=ant, alpha0_deg=2.0, min_elevation_deg=10.0,
        tstep_s=1.0, nsteps=nsteps, dual_ts=dual, n_jobs=1,
        pfd_bw_correction_db=0.0,
    )
    a = res.acc
    # Every executed iteration is classified fine or coarse.
    assert a.n_fine_steps + a.n_coarse_steps == a.n_steps
    # Dual stepping runs fewer iterations than the fine-equivalent NSTEPS.
    assert a.n_steps < nsteps
    # Fine-equivalent reconstruction: fine + coarse×Ncoarse ≈ nsteps.
    assert a.n_fine_steps + a.n_coarse_steps * dual.ncoarse == nsteps


def test_fixed_step_counts_all_fine():
    ant = ITURS1428Antenna(1.2, 12.0, 0.65)
    nsteps = 200
    res = run_epfd_simulation(
        constellation=_constellation(), wcg=_wcg(), pfd_mask=_alpha_mask(),
        es_antenna=ant, alpha0_deg=2.0, min_elevation_deg=10.0,
        tstep_s=1.0, nsteps=nsteps, dual_ts=None, n_jobs=1,
        pfd_bw_correction_db=0.0,
    )
    a = res.acc
    assert a.n_coarse_steps == 0
    assert a.n_fine_steps == a.n_steps == nsteps
