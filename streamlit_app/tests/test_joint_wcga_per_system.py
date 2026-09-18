"""Joint WCGA over a fused megaconstellation (method_3 / Resolution 76).

S.1503-4 §D3.1.2, applied literally: one single-entry search per unique
(a, e, i, system, mask) set, every input from the set's OWN filing, then the
normative cross-set rule (margin, 0.1 dB bin, lowest angular velocity).
"""
import math
import os
import sys
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.orbit_propagator import OrbitalElements  # noqa: E402
from src.pfd_mask import PFDMask, PFDMaskMulti  # noqa: E402
from streamlit_app.lib.job_runners.s1588_worker import (  # noqa: E402
    _gmst0_for_cfg,
    _joint_wcga,
    _rotate_raan_for_common_gmst0,
)


class _FlatMask(PFDMask):
    def __init__(self, pfd_db: float, refbw_khz: float, mask_id: int):
        super().__init__()
        self._pfd = float(pfd_db)
        self.refbw_khz = float(refbw_khz)
        self.mask_id = int(mask_id)
        self._dim = 1
        self.mask_type = "alpha"

    def get_pfd(self, alpha_deg, lat_deg=0.0, delta_lon_deg=0.0, sat_idx=None):
        return self._pfd

    def get_pfd_batch(self, alpha_deg, lat_deg, delta_lon_deg, sat_indices=None):
        return np.full(np.asarray(alpha_deg).size, self._pfd, dtype=float)

    def content_hash(self) -> str:
        return f"flat:{self._pfd}:{self.refbw_khz}"

    def detect_wcg_theta_symmetry(self, tol_db: float = 1e-3):
        return False, "flat"


def _oe(a_km, i_deg, e=0.001, raan_deg=0.0, M_deg=0.0):
    return OrbitalElements(a=a_km, e=e, i=math.radians(i_deg),
                           raan=math.radians(raan_deg), omega=0.0, M=math.radians(M_deg))


def _thr(const_db):
    fn = lambda lat: const_db  # noqa: E731
    return fn


def _fixture():
    """Two filings.
      sys0: shell A (7000 km / 53°) ×2 sats + shell B (8000 km / 87.9°) ×1 — same e,
            same mask → the OLD key (0, e, 0, mask_id) collapsed A and B.
      sys1: shell A again (same a, e, i as sys0's) — the key must still split it,
            because every WCGA input is per filing.
    """
    combined = [_oe(7000, 53), _oe(7000, 53, M_deg=90), _oe(8000, 87.9), _oe(7000, 53)]
    system_id_per_sat = [0, 0, 0, 1]
    mask_id_per_sat = [1, 1, 1, 2]
    masks = {1: _FlatMask(-150.0, 40.0, 1), 2: _FlatMask(-152.0, 10.0, 2)}
    pfd_multi = PFDMaskMulti(masks, mask_id_per_sat)

    cfgs = [
        {"article22_limits": {"reference_bandwidth_khz": 40.0},
         "wcg_search": {"s1503_symmetric_mask": True}},
        {"article22_limits": {"reference_bandwidth_khz": 40.0},
         "wcg_search": {}},                       # → auto-detect → False
    ]
    per_system = dict(
        per_system_antenna=[SimpleNamespace(tag="ant0"), SimpleNamespace(tag="ant1")],
        per_system_alpha0=[10.0, 3.0],
        per_system_eps0=[5.0, 25.0],
        per_system_caps=[
            {"gso_min_elevation_deg": 7.0, "strict_exclusion_zone": False},
            {"gso_min_elevation_deg": 12.0, "strict_exclusion_zone": True},
        ],
        per_system_nco=[[("nco0",)], [("nco1",)]],
        per_system_thr=[_thr(-160.0), _thr(-165.3)],
    )
    return combined, system_id_per_sat, mask_id_per_sat, pfd_multi, cfgs, per_system


def _fake_result(epfd, ang_vel, lat=10.0):
    return SimpleNamespace(
        epfd_dBW=epfd, angular_velocity_deg_s=ang_vel,
        es_lat_deg=lat, es_lon_deg=0.0, gso_lon_deg=0.0, alpha_deg=3.0,
        ref_sat_eci=np.array([7000.0, 0.0, 0.0]),
    )


def test_one_search_per_orbit_system_mask_set_and_every_input_is_the_owners():
    combined, sid, mid, multi, cfgs, ps = _fixture()
    calls = []

    def search_fn(**kw):
        calls.append(kw)
        return _fake_result(-160.0, 0.05)

    best, best_idx, meta = _joint_wcga(
        cfgs=cfgs, combined=combined, pfd_multi=multi,
        mask_id_per_sat=mid, system_id_per_sat=sid, step_deg=0.1, n_jobs=1,
        search_fn=search_fn, progress=lambda f: None, **ps,
    )
    # 3 sets, not 2 (old key) and not 4 (two sats of the same shell dedup).
    assert meta["unique_sets"] == 3 and len(calls) == 3
    by_ref = {int(c["orbit_idx"]): c for c in calls}
    owners = [0, 0, 1]                    # ref sats 0, 2, 3 → sys 0, 0, 1
    for k, kw in by_ref.items():
        s = owners[k]
        assert kw["es_antenna"] is ps["per_system_antenna"][s]
        assert kw["alpha0_deg"] == ps["per_system_alpha0"][s]
        assert kw["min_elevation_deg"] == ps["per_system_eps0"][s]
        assert kw["gso_min_elevation_deg"] == ps["per_system_caps"][s]["gso_min_elevation_deg"]
        assert kw["strict_exclusion_zone"] == ps["per_system_caps"][s]["strict_exclusion_zone"]
        assert kw["max_co_freq_by_lat"] is ps["per_system_nco"][s]
        assert kw["epfd_threshold_by_lat_fn"] is ps["per_system_thr"][s]
        assert kw["step_size_deg"] == 0.1
    # RefBW correction of the OWN mask: sys0 mask 40 kHz → 0 dB; sys1 mask 10 kHz → +6.02 dB.
    assert by_ref[0]["pfd_bw_correction_db"] == pytest.approx(0.0)
    assert by_ref[2]["pfd_bw_correction_db"] == pytest.approx(10 * math.log10(4.0), abs=1e-6)
    # θ symmetry: sys0 explicit True, sys1 auto-detected on its own mask → False.
    assert by_ref[0]["symmetric_mask"] is True
    assert by_ref[2]["symmetric_mask"] is False
    # The mask handed to each search is the raw sub-mask, not the fused multi.
    assert by_ref[0]["pfd_mask"] is multi.masks_by_id[1]
    assert by_ref[2]["pfd_mask"] is multi.masks_by_id[2]


def test_cross_set_rule_ranks_by_own_margin_not_absolute_epfd():
    combined, sid, mid, multi, cfgs, ps = _fixture()
    # sys1's set has the HIGHER absolute EPFD but the LOWER margin
    # (its own limit is −165.3, sys0's is −160.0).
    results = {0: _fake_result(-158.0, 0.05), 1: _fake_result(-159.0, 0.05), 2: _fake_result(-157.0, 0.05)}

    def search_fn(**kw):
        return results[int(kw["orbit_idx"])]

    best, best_idx, meta = _joint_wcga(
        cfgs=cfgs, combined=combined, pfd_multi=multi,
        mask_id_per_sat=mid, system_id_per_sat=sid, step_deg=0.1, n_jobs=1,
        search_fn=search_fn, progress=lambda f: None, **ps,
    )
    # margins: set0 = +2.0, set1 = +1.0, set2 = -157 + 165.3 = +8.3 → set2 wins
    assert meta["winning_system"] == 1 and best_idx == 3
    assert meta["best_margin_db"] == pytest.approx(8.3)
    # Now make sys1's margin lose despite the higher EPFD.
    results[2] = _fake_result(-164.0, 0.05)          # margin +1.3 < +2.0
    best, best_idx, meta = _joint_wcga(
        cfgs=cfgs, combined=combined, pfd_multi=multi,
        mask_id_per_sat=mid, system_id_per_sat=sid, step_deg=0.1, n_jobs=1,
        search_fn=search_fn, progress=lambda f: None, **ps,
    )
    assert meta["winning_system"] == 0 and best_idx == 0
    assert [c["margin_db"] for c in meta["candidates"]] == pytest.approx([2.0, 1.0, 1.3])


def test_tie_within_0p1_db_goes_to_the_lowest_angular_velocity():
    combined, sid, mid, multi, cfgs, ps = _fixture()
    results = {0: _fake_result(-158.00, 0.050), 1: _fake_result(-158.05, 0.020), 2: _fake_result(-170.0, 0.001)}

    def search_fn(**kw):
        return results[int(kw["orbit_idx"])]

    best, best_idx, meta = _joint_wcga(
        cfgs=cfgs, combined=combined, pfd_multi=multi,
        mask_id_per_sat=mid, system_id_per_sat=sid, step_deg=0.1, n_jobs=1,
        search_fn=search_fn, progress=lambda f: None, **ps,
    )
    # set1 is 0.05 dB below set0 but slower → wins the bin; set2 is far below.
    assert best_idx == 2 and meta["winning_system"] == 0
    assert meta["best_ang_vel_deg_s"] == pytest.approx(0.020)


def test_a_failed_set_is_skipped_not_fatal_and_no_result_raises():
    combined, sid, mid, multi, cfgs, ps = _fixture()

    def flaky(**kw):
        if int(kw["orbit_idx"]) == 0:
            raise RuntimeError("boom")
        return _fake_result(-158.0, 0.05)

    best, best_idx, meta = _joint_wcga(
        cfgs=cfgs, combined=combined, pfd_multi=multi,
        mask_id_per_sat=mid, system_id_per_sat=sid, step_deg=0.1, n_jobs=1,
        search_fn=flaky, progress=lambda f: None, fallback_local=lambda r: False, **ps,
    )
    assert len(meta["candidates"]) == 2

    with pytest.raises(RuntimeError, match="no valid result"):
        _joint_wcga(
            cfgs=cfgs, combined=combined, pfd_multi=multi,
            mask_id_per_sat=mid, system_id_per_sat=sid, step_deg=0.1, n_jobs=1,
            search_fn=lambda **kw: None, progress=lambda f: None, **ps,
        )


# ── GMST0: one Earth, every filing's ground track honoured ───────────────────

def test_gmst0_precedence_matches_the_per_filing_driver():
    assert _gmst0_for_cfg({"simulation": {"earth_rotation_initial_deg": 12.5},
                           "non_gso": {"_gmst0_deg": 300.0}}) == 12.5
    assert _gmst0_for_cfg({"simulation": {}, "non_gso": {"_gmst0_deg": 300.0}}) == 300.0
    assert _gmst0_for_cfg({"simulation": {}, "non_gso": {}}) == 0.0


def test_raan_rotation_reproduces_each_filings_own_ecef_geometry():
    from src.coordinates import eci_to_ecef, set_earth_rotation_initial_deg
    from src.orbit_propagator import elements_to_eci

    oe = _oe(7500, 60, raan_deg=40.0, M_deg=30.0)
    g_own, g_joint = 210.0, 75.0

    # The filing's own run: its own GMST0, its filed RAAN.
    set_earth_rotation_initial_deg(g_own)
    own_ecef = eci_to_ecef(elements_to_eci(oe)[0], 0.0)

    # The joint run: joint GMST0, RAAN rotated by (g_joint − g_own).
    rotated = _rotate_raan_for_common_gmst0([oe], g_own, g_joint)[0]
    set_earth_rotation_initial_deg(g_joint)
    joint_ecef = eci_to_ecef(elements_to_eci(rotated)[0], 0.0)
    set_earth_rotation_initial_deg(0.0)

    assert np.allclose(own_ecef, joint_ecef, atol=1e-6)
    # Nothing but RAAN moved; J2 rates untouched (rotation about z).
    assert rotated.a == oe.a and rotated.i == oe.i and rotated.M == oe.M
    assert rotated.raan_dot == oe.raan_dot
    # Same GMST0 → same list back, untouched.
    assert _rotate_raan_for_common_gmst0([oe], 33.0, 33.0)[0] is oe


# ── Fig. 13 on a fused constellation with per-filing dynamics folded in ──────

def test_fig13_call_path_used_by_the_joint_run_moves_es_and_gso_together():
    """The joint run calls Fig. 13 with ZERO rates because §D6.3 dynamics are
    already folded per filing into the elements; the function must accept
    that and translate ES and GSO by the same Δlon (relative geometry kept)."""
    from src.coordinates import set_earth_rotation_initial_deg
    from src.orbit_propagator import elements_to_eci
    from src.s1503_figure13_wcg_lon import apply_s1503_figure13_wcg_longitude_adjustment
    from src.wcg_search import WCGResult

    set_earth_rotation_initial_deg(0.0)
    # Winner sits mid-orbit; the WCGA snapshot is the same satellite a bit
    # further along its track (M+15°), so a real longitude shift is needed.
    oe = _oe(7500, 60, raan_deg=20.0, M_deg=40.0)
    snap = _oe(7500, 60, raan_deg=20.0, M_deg=55.0)
    ref_eci, _ = elements_to_eci(snap)
    wcg = WCGResult(
        theta_deg=0.0, phi_deg=0.0, es_lat_deg=30.0, es_lon_deg=10.0, gso_lon_deg=15.0,
        alpha_deg=3.0, offaxis_deg=3.0, pfd_dBW=-150.0, es_gain_rel_dB=-20.0,
        epfd_dBW=-170.0, elevation_deg=40.0, ref_sat_eci=ref_eci,
    )
    out = apply_s1503_figure13_wcg_longitude_adjustment(
        wcg_result=wcg, constellation=[oe], dominant_sat_idx=0, fine_dt_s=5.0,
        raan_dot_artificial_rad_s=0.0, raan_dot_override_rad_s=None,
        wdelta_deg=0.0, t_run_s=86400.0,
    )
    assert out["applied"] is True, out
    d_es = ((wcg.es_lon_deg - 10.0 + 180.0) % 360.0) - 180.0
    d_gso = ((wcg.gso_lon_deg - 15.0 + 180.0) % 360.0) - 180.0
    assert d_es == pytest.approx(out["corr_deg"], abs=1e-9)
    assert d_gso == pytest.approx(out["corr_deg"], abs=1e-9)
    assert abs(out["corr_deg"]) > 1e-3          # a genuine shift, not a no-op
    assert out["lat_err_deg"] < 0.05            # found the crossing latitude
