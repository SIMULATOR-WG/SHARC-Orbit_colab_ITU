"""``mask_info`` frequency units: SNS v10 stores GHz, the S.1503-4 examination
structure stores MHz (EPS V41 §6.5.1.1, which flags the change: *"In SNS v10 it
is currently GHz, care should be taken to introduce this change in new SNS
structure."*).

The same column carries both and there is no unit field, so the reader infers the
unit from magnitude and normalises to GHz. Reading an MHz band as GHz is not a
cosmetic error: the Article 22 possibility tree clips the PFD mask bands against
the transmitting ``grp`` bands (which *are* converted), the intersection comes
back empty, and the UI then offers no Article 22 configuration at all. That is
exactly what the BR's NEXT101 case hit.
"""
from __future__ import annotations

import os

import pytest

from src.srs_reader import _mask_freq_to_ghz, read_mask_info  # type: ignore[import]

_NEXT101 = os.path.join("docs", "_shared", "127520101 SRS.MDB")
_V10 = os.path.join("docs", "test_data", "EPFD_Test_Data.mdb")


# ── the discriminator ───────────────────────────────────────────────────────

def test_ghz_bands_pass_through_unchanged():
    """Article 22 bands top out around 50 GHz, so a GHz value never reaches the cut."""
    for lo, hi in ((10.7, 12.75), (17.8, 18.6), (19.7, 20.2), (27.5, 30.0), (10.0, 40.0)):
        assert _mask_freq_to_ghz(lo, hi) == (lo, hi, "GHz")


def test_mhz_bands_are_converted():
    """An MHz value can never fall below 10 000 for an Article 22 band."""
    assert _mask_freq_to_ghz(17800.0, 18600.0) == (17.8, 18.6, "MHz")
    assert _mask_freq_to_ghz(19700.0, 20200.0) == (19.7, 20.2, "MHz")
    assert _mask_freq_to_ghz(27500.0, 30000.0) == (27.5, 30.0, "MHz")


def test_absent_band_is_not_invented():
    """Zero means "not declared", not 0 Hz — it must not be scaled."""
    assert _mask_freq_to_ghz(0.0, 0.0) == (0.0, 0.0, "GHz")
    # One endpoint declared, the other absent: the declared one still decides.
    lo, hi, unit = _mask_freq_to_ghz(0.0, 20200.0)
    assert (lo, hi, unit) == (0.0, 20.2, "MHz")


def test_row_straddling_the_threshold_is_reported_not_guessed(caplog):
    """A row with one endpoint each side of the cut is internally inconsistent."""
    with caplog.at_level("ERROR"):
        out = _mask_freq_to_ghz(18.6, 19700.0, mask_id=4, source="x.mdb")
    assert out == (18.6, 19700.0, "GHz")          # left alone, not halved
    assert "straddles" in caplog.text and "mask_id=4" in caplog.text


# ── real filings ────────────────────────────────────────────────────────────

@pytest.mark.skipif(not os.path.exists(_NEXT101), reason="NEXT101 SRS not available")
def test_next101_mask_info_normalises_to_ghz():
    masks = read_mask_info(_NEXT101, ntc_id="127520101")
    assert masks, "no mask_info rows"
    assert all(m.unit_in_db == "MHz" for m in masks)
    bands = {(m.mask_id, round(m.freq_min_ghz, 3), round(m.freq_max_ghz, 3)) for m in masks}
    assert (1, 17.8, 18.6) in bands       # PFD, the classic downlink band
    assert (4, 19.7, 20.2) in bands       # PFD, the track-duration band
    assert (6, 27.5, 30.0) in bands       # e.i.r.p. ES, uplink
    for m in masks:
        assert m.freq_min_mhz == pytest.approx(m.freq_min_ghz * 1000.0)


@pytest.mark.skipif(not os.path.exists(_V10), reason="v10 fixture not available")
def test_legacy_v10_mask_info_is_untouched():
    masks = read_mask_info(_V10, ntc_id="101")
    assert masks
    assert all(m.unit_in_db == "GHz" for m in masks)
    assert all(0.0 < m.freq_min_ghz < 1000.0 for m in masks)


@pytest.mark.skipif(not os.path.exists(_NEXT101), reason="NEXT101 SRS not available")
def test_next101_offers_article22_configurations():
    """The regression this fix exists for: the tree must not come back empty.

    Reproduces what the Single-entry page does — clip the PFD mask bands to the
    transmitting ``grp`` bands, then enumerate the Article 22 possibilities.
    """
    from src.article22_tables import (  # type: ignore[import]
        list_article22_downlink_possibilities_for_masks,
    )
    from streamlit_app.lib import srs_inspect  # type: ignore[import]

    srs_inspect._frequency_bands.cache_clear()
    fb = srs_inspect.frequency_bands(_NEXT101, "127520101")

    tx = sorted(
        (float(g["freq_min_ghz"]), float(g["freq_max_ghz"]))
        for g in (fb.get("groups") or [])
        if str(g.get("emi_rcp", "")).upper().startswith(("TX", "E"))
        and g.get("freq_min_ghz") is not None
    )
    merged: list[tuple[float, float]] = []
    for lo, hi in tx:
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))

    pfd = [(float(m["freq_min_ghz"]), float(m["freq_max_ghz"]))
           for m in fb["masks"] if m["type"] == "PFD"]
    clipped = [(max(a, c), min(b, d)) for a, b in pfd for c, d in merged
               if min(b, d) > max(a, c)]
    assert clipped, "PFD × Tx intersection empty — the unit bug is back"

    tree = list_article22_downlink_possibilities_for_masks([
        {"mask_id": i, "label": f"m{i}", "freq_min_ghz": lo, "freq_max_ghz": hi}
        for i, (lo, hi) in enumerate(clipped)
    ])
    services = tree.get("services") or []
    assert services, "no Article 22 service offered"

    refs = {f["rr_reference"] for s in services for f in s["frequencies"]}
    # Both downlink bands of the notice must be reachable: the classic one and
    # the one whose operating-parameter set declares MIN_DURATION.
    assert any("22-1B" in r for r in refs), f"17.8–18.6 GHz missing: {refs}"
    assert any("22-1C" in r for r in refs), f"19.7–20.2 GHz missing: {refs}"
