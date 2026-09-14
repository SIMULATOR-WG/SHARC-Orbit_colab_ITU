"""The examined band must be the one the run asked for (S.1503-4 §D2).

`mask_lnk1` is keyed by scenario, not by frequency: a filing whose wildcard row
(orb_id −1) points at one PFD mask gives that mask precedence for every orbit,
even on a notice that declares one mask per band. Resolving the default mask by
precedence alone therefore examined whichever band happened to come first, and
because §D2's FrequencyRun was then recomputed from that mask's lower edge, the
Article 22 table and the reference earth-station antenna followed it too. The
NEXT101 fixture is exactly that shape: mask 1 covers 17.8–18.6 GHz and mask 4
covers 19.7–20.2 GHz, and a request for 19.70002 GHz used to run at 17.80002.
"""
from __future__ import annotations

import os

import pytest

_SHARED = os.path.join("docs", "_shared")
_SRS = os.path.join(_SHARED, "127520101 SRS.MDB")
_MASKS = os.path.join(_SHARED, "127520101 Masks.MDB")
_have = os.path.exists(_SRS) and os.path.exists(_MASKS)

pytestmark = pytest.mark.skipif(not _have, reason="NEXT101 MDBs not available")


def _load(**kw):
    from src.main import load_from_srs  # type: ignore[import]
    return load_from_srs(_SRS, pfd_mask_mdb=_MASKS, ntc_id="127520101", **kw)


def test_requested_band_wins_over_mask_lnk1_precedence():
    cfg = _load(simulation_frequency_ghz=19.70002)
    ng = cfg["non_gso"]
    assert ng["frequency_ghz"] == pytest.approx(19.70002, abs=1e-9)
    assert ng.get("_frequency_request_ignored") is None
    # The §B3.3 set of the requested band, not of the precedence mask.
    assert ng["min_duration_by_lat"] == [(-90.0, 90.0, 2400.0)]
    # And the Article 22 table of the requested band.
    assert "22-1C" in str(cfg["article22_limits"]["rr_reference"])


def test_other_band_of_the_same_notice_still_resolves():
    cfg = _load(simulation_frequency_ghz=17.80002)
    ng = cfg["non_gso"]
    assert ng["frequency_ghz"] == pytest.approx(17.80002, abs=1e-9)
    assert ng["min_duration_by_lat"] == []          # set 8 declares no MIN_DURATION
    assert "22-1B" in str(cfg["article22_limits"]["rr_reference"])


def test_explicit_mask_that_does_not_cover_the_request_is_recorded_not_silent():
    """An explicit mask_id wins, but the substitution must be on the record."""
    cfg = _load(simulation_frequency_ghz=19.70002, mask_id=1)
    ng = cfg["non_gso"]
    ign = ng.get("_frequency_request_ignored")
    assert ign is not None, "frequency substitution must be recorded"
    assert ign["requested_ghz"] == pytest.approx(19.70002, abs=1e-9)
    assert ign["mask_id"] == 1
    assert ign["band_ghz"][0] == pytest.approx(17.8, abs=1e-6)
    # Article 22 then clamps the requested frequency into the band of the table
    # it selected for mask 1, so the run ends at that band's upper edge. The
    # record must name the frequency actually used, not the intermediate one.
    assert ng["frequency_ghz"] == pytest.approx(18.6, abs=1e-6)
    assert ign["used_ghz"] == pytest.approx(18.6, abs=1e-6)


def test_frequency_covered_by_no_mask_is_refused():
    with pytest.raises(ValueError) as exc:
        _load(simulation_frequency_ghz=12.0)
    msg = str(exc.value)
    assert "12.000000 GHz" in msg
    assert "17.800" in msg and "19.700" in msg, "the message must list the declared bands"
