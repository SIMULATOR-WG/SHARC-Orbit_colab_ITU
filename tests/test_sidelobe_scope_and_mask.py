"""SL2SL scope selection and mask-sourced serving pfd (WP 4A study option).

Covers §8 of SPEC_sidelobe_annulus_and_mask_pfd:

1. regression — ``pfd_source="constant"`` + ``scope="outside_zone"`` behaves
   exactly as before (candidate set = all visible, victim α gate applied);
2. mask — the serving pfd is the mask value at the SERVED-link geometry plus
   the run's RefBW correction, NOT the value toward the victim;
3. scope — the annulus is exactly ``¬standard ∧ −30 dB < GRX_rel(φ) ≤
   GRX_rel(α₀)``;
4. guard — ``scope="annulus_gmax30"`` without the Gmax−30 ablation raises;
5. index propagation — ``get_valid_links``/``select_links`` carry the
   constellation index (per-satellite mask routing depends on it).
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from src.coordinates import lla_to_ecef  # type: ignore[import]
from src.nonNco_links import get_valid_links, select_links  # type: ignore[import]
from src.sidelobe_epfd import (  # type: ignore[import]
    SidelobeConfig,
    compute_sidelobe_epfd_step,
)

RE = 6378.137


def _es(lat, lon):
    return np.asarray(lla_to_ecef(lat, lon, 0.0), dtype=np.float64)


def _sat_over(lat, lon, alt_km=604.0):
    """Satellite straight above (lat, lon) — elevation 90° there."""
    v = np.asarray(lla_to_ecef(lat, lon, 0.0), dtype=np.float64)
    return v * (1.0 + alt_km / np.linalg.norm(v))


class _FlatMask:
    """Az/El mask returning a value that depends only on the queried azimuth,
    so a test can tell the served-link query from the victim-direction one."""

    mask_type = "azimuth_elevation"
    is_mixed_geometry = False

    def __init__(self):
        self.calls = []

    def get_pfd_batch(self, a, lat, c, sat_indices=None):
        a = np.asarray(a, dtype=np.float64)
        self.calls.append((float(a[0]), float(np.asarray(c)[0]),
                           None if sat_indices is None else int(np.asarray(sat_indices)[0])))
        # −150 dB, plus 1 dB per degree of |azimuth| — an invertible signature.
        return -150.0 + np.abs(a)


# ── 5) index propagation ────────────────────────────────────────────────────

def test_links_carry_the_constellation_index():
    victim = _es(0.0, 0.0)
    # Two satellites; the grid has one ES far enough from the victim meridian
    # that the arc gate passes.
    grid = np.asarray([[30.0, 40.0]], dtype=np.float64)
    sats = np.stack([_sat_over(0.0, 0.0), _sat_over(30.0, 40.0)])
    sin_el = np.asarray([1.0, 1.0])
    valid = get_valid_links(
        grid, sats, victim, 0.0, sin_el,
        victim_exclusion_alpha_deg=0.0,   # keep both
        min_elevation_deg=5.0, gso_arc_separation_deg=1.0,
    )
    assert valid, "expected at least one valid link"
    for row in valid:
        assert len(row) == 6, "row must be (sat_idx, sat_ecef, lat, lon, elev, es_ecef)"
        assert isinstance(row[0], int)
        assert 0 <= row[0] < sats.shape[0]
    sel = select_links(valid)
    assert sel and all(len(r) == 6 and isinstance(r[0], int) for r in sel)
    # the satellite over the served ES must be the one picked for it
    assert any(r[0] == 1 for r in sel)


def test_candidate_subset_restricts_the_iteration():
    victim = _es(0.0, 0.0)
    grid = np.asarray([[30.0, 40.0]], dtype=np.float64)
    sats = np.stack([_sat_over(0.0, 0.0), _sat_over(30.0, 40.0)])
    sin_el = np.asarray([1.0, 1.0])
    only_0 = get_valid_links(
        grid, sats, victim, 0.0, sin_el,
        victim_exclusion_alpha_deg=0.0, min_elevation_deg=5.0,
        gso_arc_separation_deg=1.0, cand_idx=np.asarray([0]),
    )
    assert all(r[0] == 0 for r in only_0)


def test_alpha_gate_can_be_disabled_for_in_cone_scopes():
    victim = _es(0.0, 0.0)
    grid = np.asarray([[30.0, 40.0]], dtype=np.float64)
    # A satellite ON the victim's GSO direction: |alpha| ~ 0 -> the victim
    # gate would reject it, but the in-cone scopes must keep it.
    sats = np.stack([_sat_over(30.0, 40.0)])
    sin_el = np.asarray([1.0])
    gated = get_valid_links(
        grid, sats, victim, 0.0, sin_el,
        victim_exclusion_alpha_deg=179.0,  # impossible gate -> rejects all
        min_elevation_deg=5.0, gso_arc_separation_deg=1.0,
    )
    assert gated == []
    ungated = get_valid_links(
        grid, sats, victim, 0.0, sin_el,
        victim_exclusion_alpha_deg=179.0,
        min_elevation_deg=5.0, gso_arc_separation_deg=1.0,
        cand_idx=np.asarray([0]), apply_victim_alpha_gate=False,
    )
    assert ungated, "disabling the victim alpha gate must keep the link"


# ── 1) regression: defaults keep the historical behaviour ──────────────────

def test_defaults_are_the_historical_behaviour():
    cfg = SidelobeConfig()
    assert cfg.scope == "outside_zone"
    assert cfg.pfd_source == "constant"
    assert cfg.needs_step18_classification is False
    assert cfg.applies_victim_alpha_gate is True


def test_constant_source_uses_the_declared_pfd_and_ignores_the_mask():
    victim = _es(0.0, 0.0)
    grid = np.asarray([[30.0, 40.0]], dtype=np.float64)
    sats = np.stack([_sat_over(30.0, 40.0)])
    sin_el = np.asarray([1.0])
    mask = _FlatMask()
    lin, n_links, n_no_link = compute_sidelobe_epfd_step(
        SidelobeConfig(pfd_dbw_m2=-140.0, min_elevation_deg=5.0,
                       gso_arc_separation_deg=1.0, alpha_gate_deg=0.0),
        grid, sats, sin_el, victim, lambda _s: 1.0, 4.0, 0.0,
        pfd_mask=mask, pfd_bw_correction_db=-3.0,
    )
    assert n_links == 1
    assert not mask.calls, "constant source must not query the mask"
    assert lin > 0.0


# ── 2) mask source: queried at the SERVED geometry, RefBW applied ──────────

def test_mask_source_queries_the_served_geometry_and_applies_refbw():
    victim = _es(0.0, 0.0)
    served_lat, served_lon = 30.0, 40.0
    grid = np.asarray([[served_lat, served_lon]], dtype=np.float64)
    sats = np.stack([_sat_over(served_lat, served_lon)])
    sin_el = np.asarray([1.0])
    mask = _FlatMask()
    bw = -3.0
    lin, n_links, _ = compute_sidelobe_epfd_step(
        SidelobeConfig(pfd_source="mask", min_elevation_deg=5.0,
                       gso_arc_separation_deg=1.0, alpha_gate_deg=0.0),
        grid, sats, sin_el, victim, lambda _s: 1.0, 4.0, 0.0,
        pfd_mask=mask, pfd_bw_correction_db=bw,
        subsat_lat_all=np.asarray([served_lat]),
        subsat_lon_all=np.asarray([served_lon]),
    )
    assert n_links == 1
    assert mask.calls, "mask source must query the mask"
    az_q, _el_q, idx_q = mask.calls[0]
    assert idx_q == 0, "the constellation index must reach the mask router"
    # The satellite is straight above its served ES: the served direction is
    # nadir, so the az/el query is the nadir one (elevation ~ 90 deg in the
    # sat frame). The victim is ~50 deg away in ground distance — a nadir
    # query is therefore NOT the victim-direction query.
    assert abs(az_q) < 1.0, f"served query should be near-nadir, got az={az_q}"
    # value = mask(-150 + |az|) + bw, and the victim gain factor is 1.0 here;
    # the epfd also carries the S.1528 discrimination and the range ratio,
    # so only check that the RefBW correction moved the result by 3 dB.
    lin_no_bw, _, _ = compute_sidelobe_epfd_step(
        SidelobeConfig(pfd_source="mask", min_elevation_deg=5.0,
                       gso_arc_separation_deg=1.0, alpha_gate_deg=0.0),
        grid, sats, sin_el, victim, lambda _s: 1.0, 4.0, 0.0,
        pfd_mask=_FlatMask(), pfd_bw_correction_db=0.0,
        subsat_lat_all=np.asarray([served_lat]),
        subsat_lon_all=np.asarray([served_lon]),
    )
    assert lin_no_bw > 0.0
    assert 10.0 * math.log10(lin / lin_no_bw) == pytest.approx(bw, abs=1e-9)


def test_mask_source_requires_a_mask():
    with pytest.raises(ValueError, match="requires a pfd_mask"):
        compute_sidelobe_epfd_step(
            SidelobeConfig(pfd_source="mask"),
            np.asarray([[10.0, 10.0]]), np.zeros((1, 3)), np.asarray([1.0]),
            _es(0.0, 0.0), lambda _s: 1.0, 4.0, 0.0,
        )


# ── 3) scope: the annulus definition ───────────────────────────────────────

def test_annulus_is_exactly_the_gmax30_only_set():
    """Reproduces the classification rule on synthetic gains.

    annulus = ¬standard ∧ GRX_rel(φ) > −30 dB ∧ GRX_rel(φ) ≤ GRX_rel(α₀)
    """
    g_rel = np.asarray([-10.0, -25.0, -28.0, -35.0, -20.0])
    g_rel_at_a0 = -27.0          # so min(-30, GRX(α₀)) = -30 governs
    is_standard = np.asarray([False, False, False, False, True])
    annulus = (~is_standard) & (g_rel > -30.0) & (g_rel <= g_rel_at_a0)
    assert annulus.tolist() == [False, False, True, False, False]
    # sat 2 (−28 dB) sits between the two thresholds: admitted by Gmax−30,
    # not by GRX(α₀). sat 0/1/4 are above GRX(α₀) (or standard), sat 3 below −30.
    in_zone = (~is_standard) & (g_rel > np.minimum(-30.0, g_rel_at_a0))
    assert in_zone.tolist() == [True, True, True, False, False]
    assert np.all(g_rel[annulus] <= g_rel_at_a0)


def test_scope_requires_candidate_set():
    for scope in ("annulus_gmax30", "in_zone", "all_non_nco"):
        cfg = SidelobeConfig(scope=scope)
        assert cfg.needs_step18_classification is True
        assert cfg.applies_victim_alpha_gate is False
        with pytest.raises(ValueError, match="requires cand_sat_idx"):
            compute_sidelobe_epfd_step(
                cfg, np.asarray([[10.0, 10.0]]), np.zeros((1, 3)),
                np.asarray([1.0]), _es(0.0, 0.0), lambda _s: 1.0, 4.0, 0.0,
            )


def test_invalid_scope_and_source_are_rejected():
    with pytest.raises(ValueError, match="scope must be one of"):
        SidelobeConfig(scope="nope")
    with pytest.raises(ValueError, match="pfd_source must be one of"):
        SidelobeConfig(pfd_source="nope")


# ── 3b) the ENGINE's Step-18 classification (not just the numpy rule) ──────

def test_engine_step18_classification_populates_the_annulus():
    """Constructed geometry: ES at (0,0), GSO overhead, LEO probes in the
    equatorial plane (|alpha| ~ 0 => never standard) at growing longitude
    offsets, so phi walks across both gain-test thresholds.

    With a 0.6 m victim at 17.8 GHz: GRX_rel(alpha0=4deg) = -24.79 dB and the
    -30 dB contour sits at ~6.47deg, so Gmax-30 governs and the annulus is the
    4.00deg -> 6.47deg ring.
    """
    from src.epfd_calculator import (  # type: ignore[import]
        _accumulate_epfd_visible_satellites,
    )
    from src.antenna import ITURS1428Antenna  # type: ignore[import]
    from src.geometry import compute_offaxis_angle  # type: ignore[import]

    class _Mask3D:
        mask_type = "alpha_deltaLongitude"
        is_mixed_geometry = False
        _dim = 3

        def get_pfd(self, alpha_deg, lat_deg=0.0, delta_lon_deg=0.0):
            return -150.0

        def get_pfd_batch(self, a, lat, c, sat_indices=None):
            return np.full(np.asarray(a).shape, -150.0, dtype=np.float64)

    es = np.asarray(lla_to_ecef(0.0, 0.0, 0.0), dtype=np.float64)
    gso = np.asarray(lla_to_ecef(0.0, 0.0, 35786.0), dtype=np.float64)
    ant = ITURS1428Antenna(diameter_m=0.6, frequency_ghz=17.8)
    alpha0, eps0 = 4.0, 5.0

    offsets = (0.20, 0.30, 0.35, 0.45, 0.55, 0.70, 1.00)
    pos = np.stack([
        np.asarray(lla_to_ecef(0.0, d, 604.0), dtype=np.float64) for d in offsets
    ])
    phis = np.asarray([compute_offaxis_angle(es, p, gso) for p in pos])
    g_rel = np.asarray([ant.relative_gain(float(f), None) for f in phis])
    g_at_a0 = ant.relative_gain(alpha0, None)

    # analytic expectation, straight from the thresholds
    exp_annulus = set(np.nonzero((g_rel > -30.0) & (g_rel <= g_at_a0))[0].tolist())
    exp_in_zone = set(np.nonzero(g_rel > min(-30.0, g_at_a0))[0].tolist())
    assert exp_annulus, "the probe sweep must cross the annulus"

    d = pos - es
    rng = np.linalg.norm(d, axis=1)
    up = es / np.linalg.norm(es)
    sin_el = (d @ up) / rng

    s18: dict = {}
    _accumulate_epfd_visible_satellites(
        visible_idx=np.arange(pos.shape[0]),
        pos_ecef_all=pos, vel_ecef_all=np.zeros_like(pos), es_ecef=es,
        es_x=float(es[0]), es_y=float(es[1]), es_z=float(es[2]),
        es_lat_deg=0.0, es_lon_deg=0.0, gso_ecef=gso,
        alpha0_deg=alpha0, pfd_mask=_Mask3D(), es_antenna=ant,
        pfd_bw_correction_db=0.0, max_co_freq=2,
        strict_max_co_freq_total=False,
        subsat_lat_all=None, subsat_lon_all=None, sat_local_frames=None,
        min_operating_height_km_all=None, dual_ts=None, t_s=0.0,
        min_elevation_deg=eps0, sin_el_full=sin_el,
        step18_out=s18,
    )
    assert set(np.asarray(s18["annulus"]).tolist()) == exp_annulus, (
        s18["annulus"], exp_annulus, phis.round(2), g_rel.round(2))
    assert set(np.asarray(s18["in_zone"]).tolist()) == exp_in_zone
    # every probe is inside the alpha cone => none is standard => nothing was
    # dropped by the MAX_CO_FREQ cap.
    assert np.asarray(s18["non_nco_outside"]).size == 0


# ── 4) guard: annulus scope needs the Gmax−30 ablation ─────────────────────

def test_annulus_scope_without_the_ablation_raises(monkeypatch):
    """The engine-level guard lives in run_wcg_downlink; this pins the
    predicate it relies on (ablation OFF ⇒ refuse)."""
    import src.antenna as antenna  # type: ignore[import]

    monkeypatch.delenv("SHARC_S1503_DROP_GMAX30", raising=False)
    antenna.s1503_gain_test_drops_gmax30._cached = None
    assert antenna.s1503_gain_test_drops_gmax30("epfd") is False

    monkeypatch.setenv("SHARC_S1503_DROP_GMAX30", "epfd")
    antenna.s1503_gain_test_drops_gmax30._cached = None
    assert antenna.s1503_gain_test_drops_gmax30("epfd") is True
    antenna.s1503_gain_test_drops_gmax30._cached = None


def test_empty_candidate_set_is_a_no_op():
    lin, n_links, n_no_link = compute_sidelobe_epfd_step(
        SidelobeConfig(scope="annulus_gmax30"),
        np.asarray([[10.0, 10.0]]), np.zeros((1, 3)), np.asarray([1.0]),
        _es(0.0, 0.0), lambda _s: 1.0, 4.0, 0.0,
        cand_sat_idx=np.empty(0, dtype=np.int64),
    )
    assert (lin, n_links, n_no_link) == (0.0, 0, 0)


def test_unlinked_candidates_are_reported():
    """A candidate whose served cells all fail the gates must be counted."""
    victim = _es(0.0, 0.0)
    grid = np.asarray([[80.0, 170.0]], dtype=np.float64)   # far from the sat
    sats = np.stack([_sat_over(0.0, 0.0)])
    sin_el = np.asarray([1.0])
    lin, n_links, n_no_link = compute_sidelobe_epfd_step(
        SidelobeConfig(scope="in_zone", min_elevation_deg=25.0),
        grid, sats, sin_el, victim, lambda _s: 1.0, 4.0, 0.0,
        cand_sat_idx=np.asarray([0]),
    )
    assert n_links == 0 and n_no_link == 1 and lin == 0.0
