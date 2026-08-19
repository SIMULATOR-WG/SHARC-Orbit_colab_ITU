"""Mid-run persistence of a method_3 joint simulation.

A joint run over a megaconstellation takes hours. ``run_epfd_simulation``
therefore accepts an ``on_chunk`` callback, and the worker uses it to snapshot
the joint CCDF, the time series, the per-system curves and the geometry into a
``partial/`` subdirectory as chunks land — so cancelling a long run leaves
usable output instead of nothing.

These tests cover the contract that makes that trustworthy:
  * the callback fires with monotonically advancing progress and a final call at
    100 %, on the parallel and the sequential path alike;
  * a failing callback never kills the simulation (persistence is best-effort);
  * the snapshot files are actually written, parse, and are flagged ``partial``
    so a truncated CCDF can never be mistaken for a finished one.
"""
from __future__ import annotations

import json
import math

import numpy as np

from src.antenna import ITURS1428Antenna  # type: ignore[import]
from src.epfd_calculator import run_epfd_simulation  # type: ignore[import]
from src.orbit_propagator import OrbitalElements  # type: ignore[import]
from src.pfd_mask import PFDMaskXML  # type: ignore[import]
from src.wcg_search import WCGResult, lla_to_ecef  # type: ignore[import]
from streamlit_app.lib.job_runners.s1588_worker import (  # type: ignore[import]
    _partial_snapshot_writer,
    _write_partial_geometry,
)

N_SYSTEMS = 2
NSTEPS = 400


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


def _fused() -> tuple[list[OrbitalElements], np.ndarray]:
    combined, sids = [], []
    for sid in range(N_SYSTEMS):
        rng = np.random.RandomState(7 + sid)
        for _ in range(20):
            combined.append(OrbitalElements(
                a=7178.0 + 150.0 * sid, e=0.0,
                i=math.radians(rng.uniform(30 + 15 * sid, 55 + 15 * sid)),
                raan=math.radians(rng.uniform(0, 360)), omega=0.0,
                M=math.radians(rng.uniform(0, 360)),
            ))
            sids.append(sid)
    return combined, np.asarray(sids, dtype=np.int64)


def _wcg() -> WCGResult:
    return WCGResult(
        theta_deg=0, phi_deg=0, es_lat_deg=12.5, es_lon_deg=-4.25, gso_lon_deg=3.75,
        alpha_deg=0, offaxis_deg=0, pfd_dBW=0, es_gain_rel_dB=0, epfd_dBW=-160.0,
        elevation_deg=0, es_ecef_exact=lla_to_ecef(12.5, -4.25, 0.0),
    )


PER_SYSTEM_NCO = [[(-90.0, 90.0, 0)] for _ in range(N_SYSTEMS)]


def _run(n_jobs: int, on_chunk=None):
    combined, sids = _fused()
    return run_epfd_simulation(
        constellation=combined,
        wcg=_wcg(),
        pfd_mask=_alpha_mask(),
        es_antenna=ITURS1428Antenna(1.2, 12.0, 0.65),
        alpha0_deg=2.0,
        min_elevation_deg=10.0,
        tstep_s=1.0,
        nsteps=NSTEPS,
        dual_ts=None,
        n_jobs=n_jobs,
        pfd_bw_correction_db=0.0,
        system_id_per_sat=sids,
        max_co_freq_by_lat_per_system=PER_SYSTEM_NCO,
        on_chunk=on_chunk,
    )


def test_on_chunk_progress_is_monotonic_and_completes():
    for n_jobs in (1, 2):
        calls: list[tuple[int, int]] = []

        def _cb(acc, done, total):
            calls.append((int(done), int(total)))

        _run(n_jobs, on_chunk=_cb)
        assert calls, f"n_jobs={n_jobs}: callback never fired"
        dones = [d for d, _t in calls]
        assert dones == sorted(dones), f"n_jobs={n_jobs}: progress went backwards"
        assert all(t == NSTEPS for _d, t in calls), f"n_jobs={n_jobs}: wrong total"
        assert dones[-1] == NSTEPS, f"n_jobs={n_jobs}: last call not complete"


def test_failing_callback_does_not_kill_the_run():
    def _boom(acc, done, total):
        raise RuntimeError("disk on fire")

    ref = _run(1)
    res = _run(1, on_chunk=_boom)
    assert res.acc.n_steps == ref.acc.n_steps
    assert np.array_equal(res.acc.duration_per_bin, ref.acc.duration_per_bin)


def test_snapshot_writes_usable_partial_files(tmp_path):
    cfgs: list[dict] = [{} for _ in range(N_SYSTEMS)]
    _, sids = _fused()
    writer = _partial_snapshot_writer(
        tmp_path, _wcg(), cfgs, list(map(int, sids)), min_interval_s=0.0,
    )
    _run(2, on_chunk=writer)

    out = tmp_path / "partial"
    assert out.is_dir()
    for name in ("sim_data.partial.json", "ccdf_epfd.csv", "epfd_timeseries.csv",
                 "geometries.csv"):
        assert (out / name).is_file(), f"missing {name}"

    snap = json.loads((out / "sim_data.partial.json").read_text(encoding="utf-8"))
    # A truncated CCDF must never be mistakable for a finished run.
    assert snap["partial"] is True
    assert snap["method"] == "method_3"
    assert snap["progress"]["steps_total"] == NSTEPS
    assert snap["progress"]["steps_done"] == NSTEPS  # last snapshot always writes
    assert snap["geometry"]["es_lat_deg"] == 12.5
    assert len(snap["ccdf_bins_db"]) == len(snap["ccdf_pct"]) > 0
    assert len(snap["per_system_at_wcg"]) == N_SYSTEMS
    assert snap["n_systems"] == N_SYSTEMS

    # CSV must carry data rows, not just the comment/header preamble.
    rows = [ln for ln in (out / "ccdf_epfd.csv").read_text(encoding="utf-8").splitlines()
            if ln and not ln.startswith("#") and not ln.startswith("epfd_db,")]
    assert rows, "ccdf_epfd.csv has no data rows"


def test_geometry_is_persisted_before_the_simulation(tmp_path):
    _write_partial_geometry(tmp_path, _wcg())
    out = tmp_path / "partial"
    payload = json.loads((out / "wcg.json").read_text(encoding="utf-8"))
    assert payload["partial"] is True
    assert payload["geometry"]["gso_lon_deg"] == 3.75
    assert math.isclose(payload["wcg_epfd_dbw"], -160.0)
    csv = (out / "geometries.csv").read_text(encoding="utf-8")
    assert "12.5000,-4.2500,3.7500" in csv
