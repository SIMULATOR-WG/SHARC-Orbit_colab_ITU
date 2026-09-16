"""Brazil occupancy page: the controls that rewrite the system selection.

Driven through Streamlit's own AppTest, because the interesting failures are
not in the helpers but in the wiring: Streamlit refuses to let a script assign
to a widget's session-state key once that widget exists, so a button placed
below the picker has to do its work in a callback.
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
APP = REPO / "streamlit_app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

_CAT = APP / "data" / "br_occupancy" / "anatel_catalog.json"
_PAGE = str(APP / "pages" / "H_Brazil_Occupancy.py")

pytestmark = pytest.mark.skipif(
    not _CAT.exists(), reason="Anatel catalogue not cached"
)

_BASE = {
    "sources": ["anatel"], "orbit": "all", "direction": "downlink",
    "rf_bands": [], "letter_bands": [], "freq_low_ghz": "", "freq_high_ghz": "",
    "chart_span": "filter", "system_ids": [], "query": "",
}


def _some_ids(n: int) -> list[str]:
    """Ids of ``n`` catalogued systems that survive the downlink filter."""
    rows = json.loads(_CAT.read_text())
    out = [r["id"] for r in rows if r.get("downlink_ghz")]
    assert len(out) >= n, f"catalogue has only {len(out)} usable rows"
    return out[:n]


@pytest.fixture()
def app(tmp_path, monkeypatch):
    """AppTest factory with the persisted state redirected to a temp dir.

    Without this the test would overwrite the developer's own saved filters,
    and successive runs would read each other's leftovers.
    """
    from streamlit.testing.v1 import AppTest

    import lib.manual as manual
    import lib.state as state

    monkeypatch.setattr(manual, "help_expander", lambda *a, **k: None)
    monkeypatch.setattr(state, "DATA_ROOT", tmp_path)

    def _start(**overrides):
        (tmp_path / "state_br_occupancy.form.json").write_text(
            json.dumps({**_BASE, **overrides})
        )
        at = AppTest.from_file(_PAGE, default_timeout=300)
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        return at

    return _start


def _picked(at) -> list[str]:
    return list(at.multiselect[-1].value)


def _mark(at, ids: list[str]):
    """Mark pills the way a click would, then rerun."""
    at.session_state["br_occ_prune_pills"] = list(ids)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_keep_only_marked_narrows_the_selection(app):
    ids = _some_ids(4)
    at = app(system_ids=ids)
    assert _picked(at) == ids

    _mark(at, [ids[0], ids[2]])
    [b for b in at.button if b.label == "Keep only marked"][0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert _picked(at) == [ids[0], ids[2]]
    # The marks are spent: they must not survive into the next action.
    assert list(at.session_state["br_occ_prune_pills"]) == []


def test_remove_marked_drops_them(app):
    ids = _some_ids(4)
    at = app(system_ids=ids)
    _mark(at, [ids[1], ids[3]])
    [b for b in at.button if b.label == "Remove marked"][0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert _picked(at) == [ids[0], ids[2]]


def test_buttons_are_disabled_until_something_is_marked(app):  # noqa: D103
    at = app(system_ids=_some_ids(3))
    for label in ("Keep only marked", "Remove marked"):
        btn = [b for b in at.button if b.label == label]
        assert btn and btn[0].disabled, label


def test_pruner_is_absent_with_a_single_pick(app):
    at = app(system_ids=_some_ids(1))
    assert not [b for b in at.button if b.label == "Keep only marked"]


def test_each_pill_names_its_system_and_bands(app):
    """One pill per picked system, labelled so a choice can be made from it."""
    ids = _some_ids(3)
    at = app(system_ids=ids)
    pills = [p for p in at.pills if "mark" in p.label.lower()]
    assert pills, [p.label for p in at.pills]
    marker = pills[0]
    # AppTest exposes the rendered labels, not the underlying ids.
    labels = list(marker.options)
    assert len(labels) == len(ids)
    assert not any(lab.startswith(("anatel:", "sns:")) for lab in labels), labels
    # The band is what tells two similar-looking filings apart.
    assert all(" · " in lab for lab in labels), labels


def test_marking_is_reported_back_to_the_user(app):
    ids = _some_ids(3)
    at = app(system_ids=ids)
    assert any("Nothing marked yet" in str(c.value) for c in at.caption)
    _mark(at, [ids[1]])
    assert any("of 3 marked" in str(c.value) for c in at.caption), \
        [c.value for c in at.caption]


def test_picker_collapses_once_the_selection_is_long(app):
    """The picker and the marking block list the same systems.

    With a couple of hundred picks the chip wall buries everything under it, so
    past a small selection the picker moves into a collapsed expander and the
    marking block becomes the working surface. It must stay reachable, because
    it is the only way to add a system by name.
    """
    small = app(system_ids=_some_ids(3))
    labels = [e.label for e in small.expander]
    assert not any("Picked systems" in lab for lab in labels), labels
    assert any("Pick systems" in m.label for m in small.multiselect)

    big = app(system_ids=_some_ids(12))
    labels = [e.label for e in big.expander]
    assert any(lab.startswith("Picked systems (12)") for lab in labels), labels
    # Still rendered, just folded away.
    assert any("Pick systems" in m.label for m in big.multiselect)
    assert _picked(big) == _some_ids(12)


def test_orbit_filter_matches_exactly_not_by_substring(app):
    """"GEO" is a substring of "NGEO": the filter used to let NGEO through."""
    at_all = app(orbit="all")
    at_geo = app(orbit="GEO")
    at_ngeo = app(orbit="NGEO")

    def _count(at) -> int:
        cap = [c.value for c in at.caption if "after filters" in str(c.value)]
        assert cap, [c.value for c in at.caption]
        return int(str(cap[0]).split()[0])

    n_all, n_geo, n_ngeo = _count(at_all), _count(at_geo), _count(at_ngeo)
    assert n_geo < n_all, (n_geo, n_all)
    assert n_ngeo > 0
    # The two classes partition the pool: nothing counted twice, nothing lost.
    assert n_geo + n_ngeo == n_all, (n_geo, n_ngeo, n_all)


def test_filtering_does_not_delete_picked_systems(app):
    """A filter governs what the picker OFFERS, never what it holds."""
    ids = _some_ids(4)
    at = app(system_ids=ids, letter_bands=["Ka"])
    assert _picked(at) == ids, "picks must survive a filter that excludes them"
    notes = [i.value for i in at.info if "outside the current filter" in str(i.value)]
    assert notes, [i.value for i in at.info]
    # And the escape hatch is offered explicitly.
    assert any(b.label.startswith("Drop the ") for b in at.button), \
        [b.label for b in at.button]


def test_catalog_section_folds_once_data_is_loaded(app):
    at = app()
    labels = [e.label for e in at.expander]
    assert any(lab.startswith("1. Catalogs —") for lab in labels), labels
    # The heading is gone from the top-level flow, so the filters come first.
    assert not any(str(h.value).startswith("1.") for h in at.subheader), \
        [h.value for h in at.subheader]


def test_assignments_table_is_numeric_and_exportable(app):
    at = app(system_ids=_some_ids(2))
    frames = [d for d in at.dataframe if "low (GHz)" in list(d.value.columns)]
    assert frames, [list(d.value.columns) for d in at.dataframe]
    table = frames[0].value
    for col in ("low (GHz)", "high (GHz)", "bandwidth (MHz)"):
        assert table[col].dtype.kind == "f", (col, table[col].dtype)
    assert "4. Assignments" in [str(h.value) for h in at.subheader]


def _axis_caption(at) -> str:
    hits = [str(c.value) for c in at.caption if str(c.value).startswith("Axis ")]
    return hits[0] if hits else ""


def _chart_axis(at):
    """The two axis end labels the strip chart rendered."""
    import re

    md = "".join(str(m.value) for m in at.markdown)
    m = re.search(
        r"so-bc-axis[^>]*>\s*<span>([^<]+)</span>.*?<span>([^<]+)</span>\s*</div>",
        md, re.S)
    return (m.group(1), m.group(2)) if m else None


def test_chart_range_slider_zooms_without_filtering(app):
    """The slider is a VIEW control: it trims the bars, not the selection.

    It lives beside the chart in section 3 for that reason. An earlier version
    sat among the filters and changed which systems were selected, which is not
    what a zoom does.
    """
    ids = _some_ids(6)
    at = app(system_ids=ids)
    before_axis = _chart_axis(at)
    before_n = _count_after_filters(at)
    assert at.slider, "no range slider beside the chart"
    slider = at.slider[0]
    assert isinstance(slider.value, tuple), slider.value
    assert "Chart range" in slider.label, slider.label

    at.slider[0].set_range(11.211, 12.337).run()
    assert not at.exception, [e.value for e in at.exception]

    # The axis followed the drag, to a value that is not a band edge.
    axis = _chart_axis(at)
    assert axis != before_axis
    assert "11" in axis[0] and "211" in axis[0], axis
    # The selection did not move.
    assert _count_after_filters(at) == before_n
    assert _picked(at) == ids
    assert "11.211" in _axis_caption(at), _axis_caption(at)


def test_chart_range_is_continuous_not_band_edges(app):
    """Any frequency is reachable, not only the letter-band boundaries."""
    at = app(system_ids=_some_ids(6))
    slider = at.slider[0]
    lo, hi = float(slider.min), float(slider.max)
    assert hi > lo
    # A fine step: at least a few hundred positions across the span.
    assert float(slider.step) <= (hi - lo) / 100.0, slider.step
    at.slider[0].set_range(lo + float(slider.step), hi - float(slider.step)).run()
    assert not at.exception, [e.value for e in at.exception]


def test_full_width_draws_everything(app):
    at = app(system_ids=_some_ids(6))
    full_axis = _chart_axis(at)
    slider = at.slider[0]
    at.slider[0].set_range(float(slider.min) + 1.0, float(slider.max) - 1.0).run()
    assert _chart_axis(at) != full_axis
    assert _axis_caption(at)
    at.slider[0].set_range(float(slider.min), float(slider.max)).run()
    assert _chart_axis(at) == full_axis
    assert _axis_caption(at) == "", _axis_caption(at)


def _count_after_filters(at) -> int:
    caps = [str(c.value) for c in at.caption if "after filters" in str(c.value)]
    assert caps, [c.value for c in at.caption]
    return int(caps[0].split()[0])


def _full_span(at) -> tuple[float, float]:
    opts = list(at.select_slider[0].options)
    return float(opts[0]), float(opts[-1])
