"""Redirect — territorial WCGA now lives on Single-entry.

Selecting countries under Geometry → S.1503-4 WCGA runs the same
country-constrained worker this page used to launch.
"""
from __future__ import annotations

import streamlit as st

from lib.state import set_persisted_state, use_persisted_state

st.set_page_config(page_title="Single-entry · SHARC-Orbit", layout="wide")

# Carry leftover territorial-form state into Single-entry once.
_old = use_persisted_state("country_wcg.form", {})
if _old.get("country_codes"):
    _new = dict(use_persisted_state("s1503.form", {}))
    for k, v in _old.items():
        if v is not None and v != "":
            _new[k] = v
    _new["geom_mode"] = "wcga"
    _new["wcga_s1503"] = True
    _new["wcg_manual"] = False
    set_persisted_state("s1503.form", _new)

st.switch_page("pages/3_Single_entry.py")
