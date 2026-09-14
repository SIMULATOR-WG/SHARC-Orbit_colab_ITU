"""Orbit-table column spellings and the AP4 minimum-operating-height gate.

Two schema variants carry the same three lengths: SNS v10 writes
``apog``/``apog_exp`` (value × 10^exp) while the S.1503-4 examination structure
writes ``apog_km`` in plain km. Reading only the first spelling made apogee,
perigee and the minimum operating height silently zero, which is not a benign
default: §D6.3.7's a = Re + (ha + hp)/2 then falls back to the declared period,
the §D4 dimensioning inherits that fallback, and the transmit gate is disabled.
"""
from __future__ import annotations

import os

import numpy as np
import pytest

from src.srs_reader import _orbit_length_km  # type: ignore[import]

_SRS = os.path.join("docs", "_shared", "127520101 SRS.MDB")


def test_orbit_length_accepts_both_schema_spellings():
    # S.1503-4 examination structure: plain km.
    assert _orbit_length_km({"apog_km": "8062"}, "apog", "apog_km") == 8062.0
    # SNS v10: value × 10^exp.
    assert _orbit_length_km(
        {"apog": "8.062", "apog_exp": "3"}, "apog", "apog_km") == pytest.approx(8062.0)
    # No exponent means the value is already in km.
    assert _orbit_length_km({"apog": "8062", "apog_exp": "0"}, "apog", "apog_km") == 8062.0
    # The km spelling wins when both are present.
    assert _orbit_length_km(
        {"apog_km": "8062", "apog": "1", "apog_exp": "3"}, "apog", "apog_km") == 8062.0
    # Neither declared: absent, reported as 0.0 for the caller to interpret.
    assert _orbit_length_km({}, "apog", "apog_km") == 0.0


@pytest.mark.skipif(not os.path.exists(_SRS), reason="NEXT101 SRS not available")
def test_next101_apsides_and_min_height_are_read():
    """This filing writes apog_km/perig_km/op_ht_km; all three must come back."""
    from src.srs_reader import read_srs_mdb  # type: ignore[import]

    system = read_srs_mdb(_SRS, ntc_id="127520101")
    p0 = system.orbit_planes[0]
    assert p0.apogee_km == pytest.approx(8062.0)
    assert p0.perigee_km == pytest.approx(8062.0)
    assert p0.op_height_km == pytest.approx(8062.0)
    assert p0.eccentricity == pytest.approx(0.0, abs=1e-12)


@pytest.mark.skipif(not os.path.exists(_SRS), reason="NEXT101 SRS not available")
def test_semi_major_axis_follows_d637_not_the_period():
    """§D6.3.7: a = Re + (ha + hp)/2, once the apsides are actually read."""
    from src.constants import RE_KM  # type: ignore[import]
    from src.main import load_from_srs  # type: ignore[import]

    _MASKS = os.path.join("docs", "_shared", "127520101 Masks.MDB")
    cfg = load_from_srs(_SRS, pfd_mask_mdb=_MASKS, ntc_id="127520101",
                        simulation_frequency_ghz=19.70002)
    a = float(cfg["non_gso"]["semi_major_axis_km"])
    assert a == pytest.approx(RE_KM + 8062.0, abs=1e-6)
    planes = cfg["non_gso"]["_planes"]
    assert planes and all(
        float(p["semi_major_axis_km"]) == pytest.approx(RE_KM + 8062.0, abs=1e-6)
        for p in planes)


def test_min_operating_height_gate_keeps_a_satellite_at_exactly_h_min():
    """A circular filing declares H_min equal to its own altitude.

    The comparison then lands on equality, where the difference between the
    propagated radius and Re + H_min is float noise. With the previous 1 µm
    tolerance the whole constellation could drop out, and the run reported a
    clean PASS over an empty CCDF.
    """
    from src.constants import RE_KM  # type: ignore[import]
    from src.epfd_calculator import _H_MIN_TOL_KM  # type: ignore[import]

    h_min = 8062.0
    # Worst realistic mismatch: a fraction of a millimetre from the propagator.
    for delta_km in (0.0, -1e-9, -1e-6, -1e-4):
        alt = h_min + delta_km
        assert alt >= (h_min - _H_MIN_TOL_KM), delta_km
    # A real 10 m descent below the declared height is still excluded.
    assert not (h_min - 0.01) >= (h_min - _H_MIN_TOL_KM)
    assert _H_MIN_TOL_KM == pytest.approx(1e-3)
    assert RE_KM > 6378.0
