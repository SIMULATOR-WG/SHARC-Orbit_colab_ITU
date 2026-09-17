"""The marked state of pills / segmented control must survive the theme CSS.

Streamlit renders every option of ``st.pills`` as a button, and the theme gives
all buttons one accent-outline look with ``!important``. That flattened the
selected option into the unselected one, so clicking a pill produced no visible
change. The active option carries its own testid; it must be styled after the
generic rule, and differently.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from streamlit_app.lib import theme  # noqa: E402

_CSS = theme.CSS if hasattr(theme, "CSS") else theme._CSS  # type: ignore[attr-defined]


def _rule_start(selector: str) -> int:
    i = _CSS.find(selector)
    assert i >= 0, f"{selector} not styled at all"
    return i


def test_active_pill_is_styled_and_after_the_generic_button_rule():
    generic = _rule_start('button[data-testid^="stBaseButton"]')
    active = _rule_start('button[data-testid="stBaseButton-pillsActive"]')
    assert active > generic, (
        "the active-pill rule must come after the generic button rule, "
        "otherwise equal-specificity !important declarations keep the "
        "generic look and marking is invisible"
    )


def test_active_and_inactive_pills_do_not_share_a_background():
    def _decl(selector: str, prop: str) -> str:
        i = _rule_start(selector)
        body = _CSS[i: _CSS.find("}", i)]
        m = re.search(rf"{prop}\s*:\s*([^;]+);", body)
        assert m, f"{prop} missing from the rule for {selector}"
        return m.group(1).strip()

    active_bg = _decl('button[data-testid="stBaseButton-pillsActive"]', "background")
    idle_bg = _decl('button[data-testid="stBaseButton-pills"]', "background")
    assert active_bg != idle_bg, (active_bg, idle_bg)
    assert "!important" in active_bg and "!important" in idle_bg


def test_segmented_control_gets_the_same_treatment():
    for sel in (
        'button[data-testid="stBaseButton-segmented_controlActive"]',
        'button[data-testid="stBaseButton-segmented_control"]',
    ):
        _rule_start(sel)


def test_active_pill_text_is_forced_too():
    """The generic rule also forces child colour, so the inverse must as well."""
    i = _rule_start('button[data-testid="stBaseButton-pillsActive"] *')
    body = _CSS[i: _CSS.find("}", i)]
    assert "color" in body and "!important" in body


def test_rejected_upload_stays_removable():
    """The rejection sentence must not cover the delete button.

    Streamlit puts the reason and the delete button in one row. Left to size
    themselves, a sentence like "application/x-msaccess files are not allowed."
    grew over the button and the only way to clear it was a page reload.
    """
    start = _rule_start('[data-testid="stFileUploaderFile"]')
    body = _CSS[start: _CSS.find("}", start)]
    assert "display: flex" in body

    # The text side must be allowed to shrink and wrap...
    i = _rule_start('[data-testid="stFileUploaderFile"] > *:not(:last-child)')
    text_rule = _CSS[i: _CSS.find("}", i)]
    assert "min-width: 0" in text_rule, "a flex item never shrinks without it"
    assert "overflow-wrap: anywhere" in text_rule

    # ...and the button must keep its own space, above anything that overflows.
    j = _rule_start('[data-testid="stFileUploaderFile"] button')
    btn_rule = _CSS[j: _CSS.find("}", j)]
    assert "flex: 0 0 auto" in btn_rule
    assert "z-index" in btn_rule
