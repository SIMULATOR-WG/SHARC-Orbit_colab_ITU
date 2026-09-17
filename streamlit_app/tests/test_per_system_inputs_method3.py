"""Per-system inputs in a fused megaconstellation (Resolution 76 / method_3).

Each filing must be evaluated with ITS OWN data. These cover the three engine
mechanisms that previously took one filing's value and applied it to all:

  * PFD→EPFD RefBW correction — was the PRIMARY sub-mask's, for every satellite
  * Step 18 bullet ② (gain / OR branch) on-off — was filing 0's
  * Step 21 minimum separation angle at the ES — was filing 0's
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.pfd_mask import PFDMask, PFDMaskMulti  # noqa: E402
from src.epfd_calculator import (  # noqa: E402
    _finalize_epfd_after_max_co_freq,
    _select_standard_epfd_s1503_steps_20_21,
)


class _FlatMask(PFDMask):
    """1D mask returning a constant PFD, with a declared reference bandwidth."""

    def __init__(self, pfd_db: float, refbw_khz: float, mask_id: int = 0):
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


# ── RefBW correction per sub-mask ────────────────────────────────────────────

def _multi_two_filings(corr=None):
    masks = {1: _FlatMask(-150.0, 40.0, 1), 2: _FlatMask(-150.0, 10.0, 2)}
    # 3 satellites of filing A (mask 1), 2 of filing B (mask 2).
    return PFDMaskMulti(masks, [1, 1, 1, 2, 2], bw_correction_db_by_id=corr)


def test_without_correction_the_multi_mask_is_unchanged():
    m = _multi_two_filings()
    assert m.has_bw_correction is False
    out = m.get_pfd_batch(np.zeros(5), np.zeros(5), np.zeros(5), np.arange(5))
    assert np.allclose(out, -150.0)


def test_each_sub_mask_gets_its_own_refbw_correction():
    # Article-22 row at 40 kHz: mask A (40 kHz) → 0 dB, mask B (10 kHz) → +6.02 dB.
    corr = {1: 0.0, 2: 10.0 * np.log10(40.0 / 10.0)}
    m = _multi_two_filings(corr)
    assert m.has_bw_correction is True
    out = m.get_pfd_batch(np.zeros(5), np.zeros(5), np.zeros(5), np.arange(5))
    assert np.allclose(out[:3], -150.0)                      # filing A
    assert np.allclose(out[3:], -150.0 + 6.0206, atol=1e-3)  # filing B
    # Scalar accessor agrees with the batch one.
    assert m.get_pfd(0.0, sat_idx=0) == pytest.approx(out[0])
    assert m.get_pfd(0.0, sat_idx=4) == pytest.approx(out[4])


def test_the_primary_mask_correction_would_have_been_wrong_for_the_other_filing():
    """Regression: the run-wide scalar is the PRIMARY sub-mask's (mask 1 here,
    3 satellites vs 2), so filing B used to enter Step 23 with filing A's
    bandwidth — a 6 dB error on its own contribution."""
    m = _multi_two_filings()
    assert m.primary_mask_id == 1
    run_wide = 10.0 * np.log10(40.0 / m.refbw_khz)   # what the engine computed
    assert run_wide == pytest.approx(0.0)            # right for A, wrong for B
    own_b = 10.0 * np.log10(40.0 / 10.0)
    assert own_b - run_wide == pytest.approx(6.0206, abs=1e-3)


def test_unknown_mask_id_in_the_correction_map_is_rejected():
    with pytest.raises(ValueError, match="unknown mask_id"):
        _multi_two_filings({99: 1.0})


# ── Step 21 minimum separation angle, per system ─────────────────────────────

def _two_system_geometry():
    """ES at the equator; 4 satellites, 2 per system. Within a pair the
    separation seen from the ES is ≈1.65° / ≈0.42°; across pairs it is ≈45°.
    A 5° rule therefore drops one satellite of each pair and never couples the
    two systems."""
    re_km, alt = 6371.0, 1200.0
    es = np.array([re_km, 0.0, 0.0])
    pos = []
    for base in (10.0, 40.0):          # one pair per system, far apart
        for d in (0.0, 0.5):
            a = np.radians(base + d)
            pos.append([(re_km + alt) * np.cos(a), (re_km + alt) * np.sin(a), 0.0])
    return es, np.asarray(pos, dtype=float)


def test_step21_angle_is_taken_per_system():
    es, pos = _two_system_geometry()
    # sat0,sat1 → system 0 ; sat2,sat3 → system 1
    sid = np.array([0, 0, 1, 1], dtype=np.int64)
    items = [(4.0, 0), (3.0, 1), (2.0, 2), (1.0, 3)]

    # System 0 enforces 1° separation (keeps 1 of its pair); system 1 does not.
    std, _ = _finalize_epfd_after_max_co_freq(
        items, [], 10, False, 0.0, es, pos,
        system_id_all=sid,
        max_co_freq_by_system={0: 10, 1: 10},
        min_angle_at_es_by_system={0: 5.0, 1: 0.0},
    )
    assert sorted(std, reverse=True) == [4.0, 2.0, 1.0]

    # Mirror image: the rule now belongs to system 1.
    std, _ = _finalize_epfd_after_max_co_freq(
        items, [], 10, False, 0.0, es, pos,
        system_id_all=sid,
        max_co_freq_by_system={0: 10, 1: 10},
        min_angle_at_es_by_system={0: 0.0, 1: 5.0},
    )
    assert sorted(std, reverse=True) == [4.0, 3.0, 2.0]


def test_scalar_angle_still_applies_when_no_per_system_map_is_given():
    es, pos = _two_system_geometry()
    sid = np.array([0, 0, 1, 1], dtype=np.int64)
    items = [(4.0, 0), (3.0, 1), (2.0, 2), (1.0, 3)]
    std, _ = _finalize_epfd_after_max_co_freq(
        items, [], 10, False, 5.0, es, pos,
        system_id_all=sid,
        max_co_freq_by_system={0: 10, 1: 10},
    )
    assert sorted(std, reverse=True) == [4.0, 2.0]


def test_selector_agrees_with_the_partitioned_call():
    """Sanity: the per-system partition is the per-system selector, nothing else."""
    es, pos = _two_system_geometry()
    sel = _select_standard_epfd_s1503_steps_20_21(
        [(4.0, 0), (3.0, 1)], 10, 5.0, es, pos,
    )
    assert [v for v, _ in sel] == [4.0]


# ── Step 18 bullet ② (gain / OR branch), per system ──────────────────────────

def _kernel_case(strict_scalar=False, strict_all=None):
    """Two satellites INSIDE the exclusion zone (|α| < α₀), so neither is a
    'standard' contributor: they can only enter through the gain (OR) branch.
    Whether they do is exactly what ``strict_exclusion_zone`` decides.
    """
    from src.antenna import create_gso_es_antenna
    from src.epfd_calculator import _accumulate_epfd_visible_satellites

    re_km, alt, gso_r = 6371.0, 1200.0, 42164.0
    es_ecef = np.array([re_km, 0.0, 0.0])
    gso_ecef = np.array([gso_r, 0.0, 0.0])          # GSO on the ES meridian
    # Both satellites within ~1° of the ES→GSO line → |α| small.
    pos = np.array([
        [(re_km + alt) * np.cos(np.radians(d)), (re_km + alt) * np.sin(np.radians(d)), 0.0]
        for d in (0.2, 0.4)
    ])
    vel = np.zeros_like(pos)
    mask = _FlatMask(-140.0, 40.0, 1)
    antenna = create_gso_es_antenna(3.0, 11.7, 0.99, service="FSS")

    std, ovr, _min_alpha, _crit = _accumulate_epfd_visible_satellites(
        np.array([0, 1], dtype=np.int64), pos, vel, es_ecef,
        float(es_ecef[0]), float(es_ecef[1]), float(es_ecef[2]),
        0.0, 0.0, gso_ecef,
        10.0,                 # α₀ = 10° → both satellites are inside the zone
        mask, antenna, 0.0, 100, False,
        None, None, None, None, None, 0.0,
        strict_exclusion_zone=strict_scalar,
        min_elevation_deg=0.0,
        sin_el_full=np.ones(2),
        strict_exclusion_zone_all=strict_all,
    )
    return std, ovr


def test_or_branch_scalar_flag_still_works_both_ways():
    std, ovr = _kernel_case(strict_scalar=False)
    assert std == [] and len(ovr) == 2      # both rescued by the gain branch
    std, ovr = _kernel_case(strict_scalar=True)
    assert std == [] and ovr == []          # strict reading: none rescued


def test_or_branch_is_decided_per_satellite_by_its_own_filing():
    """sat0 belongs to a filing with the OR branch ON, sat1 to one with it OFF.
    Before, filing 0's flag governed both."""
    std, ovr = _kernel_case(
        strict_scalar=True,                       # would suppress everything
        strict_all=np.array([False, True]),       # sat0 ON, sat1 OFF
    )
    assert std == []
    assert len(ovr) == 1

    std, ovr = _kernel_case(
        strict_scalar=False,                      # would rescue everything
        strict_all=np.array([True, False]),       # sat0 OFF, sat1 ON
    )
    assert std == []
    assert len(ovr) == 1


def test_per_satellite_flag_overrides_the_scalar_in_both_directions():
    _, ovr_none = _kernel_case(strict_scalar=False, strict_all=np.array([True, True]))
    assert ovr_none == []
    _, ovr_both = _kernel_case(strict_scalar=True, strict_all=np.array([False, False]))
    assert len(ovr_both) == 2
