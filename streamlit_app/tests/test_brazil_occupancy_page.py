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
