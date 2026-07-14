"""Tests for the grp-based emitter frequency-band filter.

Two layers:
  1. ``read_emitters_in_band`` — resolves which satellites emit at a frequency
     from the SRS ``grp`` ⋈ ``mask_lnk1`` tables (run against a real SRS MDB
     when present; skipped otherwise).
  2. ``create_constellation_with_masks(..., emitter_filter=...)`` — drops the
     satellites that do not emit in band, keeping ``constellation`` and
     ``mask_id_per_sat`` parallel. Driven by a synthetic ``EmitterBandSelection``
     so it is deterministic and needs no MDB.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.srs_reader import EmitterBandSelection, read_emitters_in_band  # type: ignore[import]
from src.main import create_constellation_with_masks  # type: ignore[import]

_REPO = Path(__file__).resolve().parents[2]
_SRS_MDB = _REPO / "streamlit_app/data/uploads/18061b1348ad/324520180 SRS.MDB"


def _ngso_cfg_two_planes() -> dict:
    return {
        "semi_major_axis_km": 7178.0,
        "eccentricity": 0.0,
        "inclination_deg": 53.0,
        "num_planes": 2,
        "sats_per_plane": 2,
        "arg_perigee_deg": 0.0,
        "_planes": [
            {"orb_id": 1, "sats_per_plane": 2, "raan_deg": 0.0},
            {"orb_id": 2, "sats_per_plane": 2, "raan_deg": 90.0},
        ],
    }


# ── EmitterBandSelection.is_active semantics ────────────────────────────────

def test_selection_no_data_keeps_all():
    sel = EmitterBandSelection(False, False, frozenset(), frozenset(), 0)
    assert sel.is_active(1, 1) is True
    assert sel.is_active(99, 5) is True  # inert when no data


def test_selection_wildcard_all():
    sel = EmitterBandSelection(True, True, frozenset(), frozenset(), 1)
    assert sel.is_active(7, 3) is True
    assert sel.any_active is True


def test_selection_whole_orbit_and_specific_sat():
    sel = EmitterBandSelection(
        True, False, frozenset({1}), frozenset({(2, 1)}), 2,
    )
    assert sel.is_active(1, 1) is True   # orbit 1 wholly active
    assert sel.is_active(1, 9) is True
    assert sel.is_active(2, 1) is True   # specific sat
    assert sel.is_active(2, 2) is False  # not selected
    assert sel.is_active(3, 1) is False


# ── create_constellation_with_masks filtering ───────────────────────────────

def test_filter_drops_inactive_orbit():
    cfg = _ngso_cfg_two_planes()
    sel = EmitterBandSelection(True, False, frozenset({1}), frozenset(), 1)
    cons, masks = create_constellation_with_masks(cfg, None, emitter_filter=sel)
    assert len(cons) == 2          # only orbit 1's two satellites
    assert len(masks) == len(cons)


def test_filter_specific_sat_only():
    cfg = _ngso_cfg_two_planes()
    sel = EmitterBandSelection(True, False, frozenset(), frozenset({(2, 1)}), 1)
    cons, masks = create_constellation_with_masks(cfg, None, emitter_filter=sel)
    assert len(cons) == 1          # only (orb 2, sat 1)
    assert len(masks) == 1


def test_filter_wildcard_keeps_all():
    cfg = _ngso_cfg_two_planes()
    sel = EmitterBandSelection(True, True, frozenset(), frozenset(), 1)
    cons, masks = create_constellation_with_masks(cfg, None, emitter_filter=sel)
    assert len(cons) == 4


def test_filter_none_keeps_all():
    cfg = _ngso_cfg_two_planes()
    cons, masks = create_constellation_with_masks(cfg, None, emitter_filter=None)
    assert len(cons) == 4


def test_filter_zero_match_falls_back_to_full():
    """An empty in-band match must NOT silently produce an empty run."""
    cfg = _ngso_cfg_two_planes()
    sel = EmitterBandSelection(True, False, frozenset({999}), frozenset(), 0)
    cons, masks = create_constellation_with_masks(cfg, None, emitter_filter=sel)
    assert len(cons) == 4          # fallback to the full constellation
    assert len(masks) == 4


# ── read_emitters_in_band against a real SRS MDB ────────────────────────────

@pytest.mark.skipif(not _SRS_MDB.exists(), reason="real SRS MDB not present")
def test_read_emitters_in_band_real_mdb():
    ntc = "324520180"
    # This filing's downlink ('E') group covers ~18 GHz; 12 GHz is outside it.
    in_band = read_emitters_in_band(str(_SRS_MDB), ntc_id=ntc, freq_ghz=18.2, emi_rcp="E")
    out_band = read_emitters_in_band(str(_SRS_MDB), ntc_id=ntc, freq_ghz=12.0, emi_rcp="E")
    assert in_band.has_data is True
    assert in_band.any_active is True          # emitters exist at 18.2 GHz
    assert in_band.n_active_grps >= 1
    assert out_band.has_data is True
    assert out_band.any_active is False        # nothing emits at 12 GHz (downlink)
