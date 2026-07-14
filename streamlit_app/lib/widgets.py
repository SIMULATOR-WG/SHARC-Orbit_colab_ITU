"""widgets.py — small reusable Streamlit widget helpers.

Currently provides ``select_described`` — a wrapper around ``st.selectbox``
that surfaces a short description for each option directly in the
dropdown items (Streamlit's stock selectbox doesn't support per-option
tooltips, so we fold the hint into the rendered label via ``format_func``).
"""
from __future__ import annotations

from typing import Any, Callable, Sequence

import streamlit as st


def confirm_delete_button(
    label: str,
    *,
    key: str,
    on_confirm: Callable[[], Any],
    message: str,
    dialog_title: str = "Confirm deletion",
    icon: str = ":material/delete:",
    button_type: str = "secondary",
    disabled: bool = False,
) -> None:
    """A delete button that opens a confirmation modal before acting.

    Replaces the old free-click / checkbox-gated delete patterns with a
    consistent two-step confirm. Clicking ``label`` opens a modal; the user
    must press **Delete** to run ``on_confirm`` (which performs the deletion
    and any feedback), or **Cancel** to abort. The page reruns either way.
    """
    @st.dialog(dialog_title)
    def _dlg() -> None:
        st.warning(message, icon=":material/warning:")
        c1, c2 = st.columns(2)
        if c1.button("Delete", type="primary", icon=":material/delete_forever:",
                     key=f"{key}__yes", width="stretch"):
            on_confirm()
            st.rerun()
        if c2.button("Cancel", key=f"{key}__no", width="stretch"):
            st.rerun()

    if st.button(label, icon=icon, type=button_type, key=key, disabled=disabled):
        _dlg()


def select_described(
    label: str,
    options: Sequence[Any],
    descriptions: dict[Any, str],
    *,
    index: int = 0,
    key: str | None = None,
    disabled: bool = False,
    help: str | None = None,
    show_inline: bool = True,
) -> Any:
    """Selectbox where each option carries an inline description.

    Each dropdown item is rendered as ``"value — description"`` so the
    user sees what the choice means at the moment of picking. After
    selection the same string remains in the widget (the chosen value
    stays visible alongside its blurb).

    Parameters
    ----------
    label         widget label
    options       list of values (kept as-is for the return value)
    descriptions  mapping ``option → short text`` (missing keys render
                   without the dash suffix)
    index         default index
    key           Streamlit widget key
    disabled      pass-through to ``st.selectbox``
    help          widget-level tooltip (Streamlit ``?`` icon)
    show_inline   if True (default), also render a small caption below
                   the widget echoing the description of the current
                   selection — handy when the dropdown is closed.
    """
    def _fmt(v: Any) -> str:
        d = descriptions.get(v)
        return f"{v} — {d}" if d else str(v)

    choice = st.selectbox(
        label,
        options=list(options),
        index=index,
        key=key,
        disabled=disabled,
        help=help,
        format_func=_fmt,
    )
    if show_inline and not disabled:
        desc = descriptions.get(choice)
        if desc:
            st.caption(f"↳ {desc}")
    return choice
