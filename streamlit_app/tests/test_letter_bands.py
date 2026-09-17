"""Letter-band derivation for the occupancy filters.

Anatel publishes an ``rf_bands`` label per licensed station; ITU SNS notices
carry no such column, so a filter built on that label can only see half the
catalogue. These helpers derive the band from the frequencies every row has.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from streamlit_app.lib.occupancy import (  # noqa: E402
    LETTER_BANDS,
    LETTER_BAND_NAMES,
    intervals_touch_range,
    letter_bands_for,
)

_CAT = str(REPO / "streamlit_app" / "data" / "br_occupancy")


def test_band_table_is_contiguous_and_open_ended():
    """No gaps between bands, and nothing above the last edge is unclassifiable."""
    for (_n1, _lo1, hi1), (_n2, lo2, _hi2) in zip(LETTER_BANDS, LETTER_BANDS[1:]):
        assert hi1 == lo2, (_n1, _n2)
    assert LETTER_BANDS[-1][2] == float("inf")
    assert len(set(LETTER_BAND_NAMES)) == len(LETTER_BAND_NAMES)


def test_known_satellite_bands_derive_as_expected():
    assert letter_bands_for([(3.625, 4.2), (5.85, 6.725)]) == ["C"]
    assert letter_bands_for([(10.7, 12.75), (13.75, 14.5)]) == ["Ku"]
    assert letter_bands_for([(17.7, 21.2), (27.5, 30.0)]) == ["Ka"]
    assert letter_bands_for([(1.525, 1.6)]) == ["L"]
    assert letter_bands_for([(0.137, 0.15)]) == ["VHF"]
    # Science allocations well above W must still land somewhere.
    assert letter_bands_for([(317.12, 333.12)]) == [">110"]
    # Ordered by ascending frequency, and a multi-band system lists them all.
    assert letter_bands_for([(27.5, 30.0), (3.7, 4.2)]) == ["C", "Ka"]


def test_band_edges_are_half_open():
    """An interval ending on an edge does not pull in the band above."""
    assert letter_bands_for([(16.0, 17.7)]) == ["Ku"]
    assert letter_bands_for([(17.7, 18.0)]) == ["Ka"]
    assert letter_bands_for([]) == []


def test_range_filter_semantics():
    ivs = [(19.7, 20.2)]
    assert intervals_touch_range(ivs, 19.0, 21.0)
    assert intervals_touch_range(ivs, 20.2, None)      # touching the edge counts
    assert intervals_touch_range(ivs, None, 19.7)
    assert not intervals_touch_range(ivs, None, 10.0)
    assert not intervals_touch_range(ivs, 30.0, None)
    assert not intervals_touch_range([], 0.0, 1000.0)
    # Reversed bounds are tolerated, so a typo does not silently empty the list.
    assert intervals_touch_range(ivs, 21.0, 19.0)


@pytest.mark.skipif(not os.path.exists(os.path.join(_CAT, "anatel_catalog.json")),
                    reason="Anatel catalogue not cached")
def test_derived_band_agrees_with_the_published_anatel_label():
    """On stations declaring exactly one letter, the derived set must contain it."""
    rows = json.load(open(os.path.join(_CAT, "anatel_catalog.json")))
    letters = {"VHF", "UHF", "L", "S", "C", "X", "Ku", "Ka"}
    checked = 0
    for r in rows:
        decl = list(r.get("rf_bands") or [])
        if len(decl) != 1 or decl[0] not in letters:
            continue
        ivs = [tuple(x) for x in (r.get("downlink_ghz") or [])]
        ivs += [tuple(x) for x in (r.get("uplink_ghz") or [])]
        assert decl[0] in letter_bands_for(ivs), (r.get("name"), decl, ivs)
        checked += 1
    assert checked >= 20, f"only {checked} single-label stations checked"


@pytest.mark.skipif(not os.path.exists(os.path.join(_CAT, "sns_catalog.json")),
                    reason="SNS catalogue not cached")
def test_every_sns_notice_lands_in_at_least_one_band():
    """The point of the feature: ITU filings must be selectable by band."""
    rows = json.load(open(os.path.join(_CAT, "sns_catalog.json")))
    assert rows
    unclassified = []
    for r in rows:
        ivs = [tuple(x) for x in (r.get("downlink_ghz") or [])]
        ivs += [tuple(x) for x in (r.get("uplink_ghz") or [])]
        if ivs and not letter_bands_for(ivs):
            unclassified.append(r.get("name"))
    assert not unclassified, unclassified[:5]


def test_frequency_presets_lead_with_band_names():
    """The picker that fills the From/To boxes is a list of band names."""
    from streamlit_app.lib.occupancy import frequency_presets  # noqa: PLC0415

    presets = frequency_presets()
    names = [k.split(" ·")[0] for k in presets]
    # Letter bands first, in ascending frequency, matching the filter's edges.
    assert names[: len(LETTER_BAND_NAMES) - 1] == list(LETTER_BAND_NAMES[:-1]), names[:11]
    for name, lo, hi in LETTER_BANDS:
        if hi == float("inf"):
            continue                      # open-ended band has no range to fill
        label = next(k for k in presets if k.split(" ·")[0] == name)
        assert presets[label] == (lo, hi), label
    # Article 22 bands follow, and at least the three epfd(down) ones are there.
    art = [k for k in presets if k.startswith("Art. 22")]
    assert any("19.7–20.2" in k for k in art), art
    assert any("17.8–18.6" in k for k in art), art
    assert any("10.7–11.7" in k for k in art), art
    # Every entry is a usable, ordered range.
    for label, (lo, hi) in presets.items():
        assert 0 < lo < hi < float("inf"), label


def test_frequency_presets_survive_a_missing_article22_table(monkeypatch):
    """The picker is a convenience: a broken import must not empty it."""
    import builtins

    from streamlit_app.lib import occupancy as occ  # noqa: PLC0415

    real_import = builtins.__import__

    def boom(name, *a, **k):
        if name == "src.article22_tables":
            raise ImportError("simulated")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", boom)
    presets = occ.frequency_presets()
    assert [k.split(" ·")[0] for k in presets] == list(LETTER_BAND_NAMES[:-1])


def test_slider_stops_are_meaningful_edges():
    """A linear slider is useless over 0.03-333 GHz; the stops carry the meaning."""
    from streamlit_app.lib.freq_bands import nearest_stop, slider_stops  # noqa: PLC0415

    stops = slider_stops()
    assert stops == sorted(stops)
    assert len(set(stops)) == len(stops), "duplicate stops"
    assert all(s > 0 for s in stops)
    assert float("inf") not in stops

    # Every finite letter-band edge is reachable.
    for _name, lo, hi in LETTER_BANDS:
        assert lo in stops
        if hi != float("inf"):
            assert hi in stops
    # So are the epfd(down) Article 22 edges an examination runs at.
    for edge in (17.8, 18.6, 19.7, 20.2, 10.7, 11.7):
        assert edge in stops, edge

    # Extra values from the caller are folded in, and junk is ignored.
    with_extra = slider_stops([0.031, 333.12, -5, None, "x"])
    assert 0.031 in with_extra and 333.12 in with_extra
    assert all(s > 0 for s in with_extra)

    assert nearest_stop(19.70002, stops) == 19.7
    assert nearest_stop(0.0, stops) == stops[0]
