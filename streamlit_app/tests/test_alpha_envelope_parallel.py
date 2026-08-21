"""Alpha-table 7-table envelope: cross-table process parallelism.

Each sub-run is serial in time (the TSS credit state is sequential), so the
envelope parallelises *between* the 7 tables — one process per table. The
contract pinned here: running the envelope on a pool must be **bit-for-bit**
identical to the in-process loop, in results, in TSS credit state and in order
(the compliance section picks the binding table by worst margin with a strict
``<``, so the table order decides ties).
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from src.wcg_search import WCGResult, lla_to_ecef  # type: ignore[import]
from src.antenna import ITURS1428Antenna  # type: ignore[import]
from src.pfd_mask import PFDMaskXML  # type: ignore[import]
from src.orbit_propagator import OrbitalElements  # type: ignore[import]
from src.epfd_calculator import SelectionConfig  # type: ignore[import]
from src.main import _run_alpha_table_envelope  # type: ignore[import]

# Declared (min, max) CDF tables — sparse pairs, turned into TSS cases by the
# envelope. Two properties are deliberate:
#
#  * The tables start at *different* angles, like the canonical example of
#    Doc 4A/312 p. 16. On the union grid the later-starting table interpolates to
#    p=0 below its own first angle, and those pairs must be dropped rather than
#    rejected (see ``alpha_table._as_pairs``).
#  * The declared angles span the α range this fixture's geometry actually
#    produces (the eligible pool sits at α ≈ 4° and 30–66°), so several TSS
#    cases are occupied at once and the quota has a real choice to make. With
#    angles stopping short of that range every satellite but one falls into the
#    single unbounded tail case ``[α_n, ∞)``, the selection collapses to "one
#    from each of two cases" and all 7 tables return the identical EPFD series —
#    which would make the equality assertions below vacuous.
MIN_PAIRS = [(2.0, 0.30), (10.0, 0.50), (35.0, 0.75), (50.0, 0.95)]
MAX_PAIRS = [(10.0, 0.10), (35.0, 0.30), (50.0, 0.60), (60.0, 0.90)]

EXPECTED_LABELS = ["min", "mid25", "mid50", "mid75", "max", "MinMax", "MaxMin"]


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


def _constellation(n=120, seed=0):
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


def _sim_kwargs(nsteps=120):
    ant = ITURS1428Antenna(1.2, 12.0, 0.65)
    return dict(
        constellation=_constellation(), wcg=_wcg(), pfd_mask=_alpha_mask(),
        es_antenna=ant, alpha0_deg=2.0, min_elevation_deg=5.0,
        tstep_s=1.0, nsteps=nsteps, dual_ts=None,
        n_jobs=1,  # each sub-run serial (TSS state)
        pfd_bw_correction_db=0.0,
        max_co_freq_by_lat=[(-90.0, 90.0, 2)],
        keep_full_history=True,
        selection_config=SelectionConfig(strategy="alpha_table"),
    )


# Satellite 117 of the fixture constellation is α₀/ε₀-eligible over the whole
# 120 s window, so the Decision-3 first-orbit exception actually fires and its
# hit counter is non-trivial state to round-trip from the worker.
WCG_REF_SAT_IDX = 117


def _envelope(n_jobs: int):
    return _run_alpha_table_envelope(
        sim_kwargs=_sim_kwargs(),
        min_pairs=MIN_PAIRS, max_pairs=MAX_PAIRS,
        bin_deg=0.0, nco=2, wcg_ref_sat_idx=WCG_REF_SAT_IDX,
        t_end_first_orbit=1200.0,   # first-orbit exception active for the whole run
        n_jobs=n_jobs,
    )


def _epfd_series(res) -> np.ndarray:
    return np.array([r.epfd_aggregate_dBW for r in res.time_steps], dtype=float)


@pytest.fixture(scope="module")
def serial_envelope():
    return _envelope(1)


@pytest.fixture(scope="module")
def parallel_envelope():
    return _envelope(4)


def test_envelope_produces_the_seven_tables_in_order(parallel_envelope):
    assert [sr["label"] for sr in parallel_envelope] == EXPECTED_LABELS


def test_parallel_matches_serial_bit_for_bit(serial_envelope, parallel_envelope):
    """Same order, same per-step EPFD sequence, same CCDF for every table."""
    assert [s["label"] for s in serial_envelope] == [p["label"] for p in parallel_envelope]
    for s, p in zip(serial_envelope, parallel_envelope):
        label = s["label"]
        assert np.array_equal(_epfd_series(s["result"]), _epfd_series(p["result"])), label
        assert np.array_equal(s["result"].cdf_epfd_dBW, p["result"].cdf_epfd_dBW), label
        assert np.array_equal(s["result"].cdf_percentage, p["result"].cdf_percentage), label


def test_tss_state_returns_from_the_workers(serial_envelope, parallel_envelope):
    """The mutated accumulator (credits, temporal peak, first-orbit hits) must
    survive the trip back from the worker — it feeds the Decision-7 diagnostics."""
    for s, p in zip(serial_envelope, parallel_envelope):
        label = s["label"]
        assert np.array_equal(s["tss"].credits, p["tss"].credits), label
        assert np.array_equal(s["tss"].max_credit_seen, p["tss"].max_credit_seen), label
        assert s["tss"].n_wcg_exception_hits == p["tss"].n_wcg_exception_hits, label
        # Non-trivial state: the run actually spent credit and used the exception.
        assert np.any(p["tss"].max_credit_seen > 0.0), label
        assert p["tss"].n_wcg_exception_hits > 0, label


def test_tables_are_not_all_identical(parallel_envelope):
    """Sanity: the envelope must actually explore distinct tables, otherwise the
    equality assertions above would be vacuous.

    Both halves matter. Distinct credit vectors prove the 7 accumulators really
    carry per-table state through the pool; distinct EPFD series prove that state
    reaches the selection and changes the outcome."""
    credits = [tuple(np.round(sr["tss"].credits, 6)) for sr in parallel_envelope]
    assert len(set(credits)) == len(parallel_envelope)
    series = [tuple(np.round(_epfd_series(sr["result"]), 6)) for sr in parallel_envelope]
    assert len(set(series)) > 1
