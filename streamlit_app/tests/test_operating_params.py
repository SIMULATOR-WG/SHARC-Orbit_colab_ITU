"""Tests for the S.1503-4 §B3.3 non-GSO system operating parameters.

Covers the parser, the four distinct lookup rules the Recommendation prescribes,
the §B5.2 range validation, the "one set per frequency band" rule of §B3.3, and
the bridge that feeds the engine. Where fixtures from the ITU BR's NEXT101 test
case are available they are used; the rule tests are synthetic so that they
assert the *Recommendation*, not the case (NEXT101's arrays are all single-point,
so nearest, linear and scalar coincide there and would hide a wrong rule).
"""
from __future__ import annotations

import os

import numpy as np
import pytest

from src.operating_params import (  # type: ignore[import]
    LatTable,
    MinElevTable,
    OperatingParameterRegistry,
    OperatingParamsError,
    load_from_paths,
    nearest_table_to_bands,
    parse_operating_params_xml,
    require_uplink_population,
    to_engine_config,
)

_SHARED = os.path.join("docs", "_shared")
_XMLS = [
    os.path.join(_SHARED, f"Mask_param_id_{i}_OP_NEXT101.xml") for i in (7, 8, 9)
]
_MASKS_MDB = os.path.join(_SHARED, "127520101 Masks.MDB")
_SRS_MDB = os.path.join(_SHARED, "127520101 SRS.MDB")

_have_xml = all(os.path.exists(p) for p in _XMLS)
_have_mdb = os.path.exists(_MASKS_MDB) and os.path.exists(_SRS_MDB)


# ── §B3.3 lookup rules (synthetic: NEXT101 cannot discriminate them) ─────────

def test_min_duration_uses_nearest_latitude():
    """§B3.3 MIN_DURATION[Latitude]: "the nearest latitude ... will be used"."""
    t = LatTable(np.array([-50.0, 0.0, 50.0]), np.array([400.0, 1000.0, 400.0]))
    assert t.nearest(-50.0) == 400.0
    assert t.nearest(0.0) == 1000.0
    assert t.nearest(-20.0) == 1000.0     # 20 from 0, 30 from −50
    assert t.nearest(-30.0) == 400.0      # 20 from −50, 30 from 0
    assert t.nearest(-90.0) == 400.0      # clamped by nearness, not extrapolated
    assert t.nearest(89.0) == 400.0
    # Exact midpoint resolves to the lower latitude.
    assert t.nearest(-25.0) == 400.0


def test_min_exclude_uses_linear_interpolation():
    """§B3.3 MIN_EXCLUDE: "derived using linear interpolation between data points"."""
    t = LatTable(np.array([-75.0, -45.0, 15.0]), np.array([0.0, 3.0, 5.0]))
    assert t.linear(-75.0) == 0.0
    assert t.linear(-60.0) == pytest.approx(1.5)     # halfway between 0 and 3
    assert t.linear(-15.0) == pytest.approx(4.0)     # halfway between 3 and 5
    assert t.linear(-90.0) == 0.0                     # clamped outside
    assert t.linear(80.0) == 5.0
    # A single declared point applies everywhere — what NEXT101 relies on.
    one = LatTable(np.array([0.0]), np.array([5.0]))
    assert one.linear(-88.0) == 5.0 and one.linear(88.0) == 5.0


def test_min_elev_nearest_latitude_then_linear_in_azimuth():
    """§B3.3 MIN_ELEV: nearest in latitude, then linear interpolation in azimuth."""
    tbl = MinElevTable(
        lats=np.array([-30.0, 30.0]),
        az=(np.array([0.0, 90.0, 180.0, 270.0]), np.array([0.0, 180.0])),
        vals=(np.array([30.0, 40.0, 30.0, 40.0]), np.array([10.0, 20.0])),
    )
    # Latitude picks the table (nearest), azimuth interpolates linearly.
    assert tbl.value(-29.0, 0.0) == 30.0
    assert tbl.value(-29.0, 45.0) == pytest.approx(35.0)
    assert tbl.value(29.0, 90.0) == pytest.approx(15.0)
    # Azimuth closes the circle: 270°→360°/0° must interpolate the short way,
    # not clamp at the last tabulated point.
    assert tbl.value(-29.0, 315.0) == pytest.approx(35.0)
    assert tbl.value(-29.0, 360.0) == pytest.approx(30.0)
    # Strictest value over azimuth, for the azimuth-free WCG search.
    assert tbl.max_over_azimuth(-29.0) == 40.0


def test_nearest_table_to_bands_is_exactly_equivalent():
    """The engine's band transport must reproduce nearest-latitude exactly."""
    t = LatTable(np.array([-50.0, 0.0, 50.0]), np.array([400.0, 1000.0, 400.0]))
    bands = nearest_table_to_bands(t)
    assert bands == [(-90.0, -25.0, 400.0), (-25.0, 25.0, 1000.0), (25.0, 90.0, 400.0)]

    def band_lookup(lat):
        for lo, hi, v in bands:      # engine semantics: first containing band wins
            if lo <= lat <= hi:
                return v
        return 0.0

    for lat in np.arange(-90.0, 90.001, 0.25):
        assert band_lookup(lat) == t.nearest(lat), f"diverges at {lat}"

    assert nearest_table_to_bands(LatTable(np.array([0.0]), np.array([2400.0]))) == [
        (-90.0, 90.0, 2400.0)
    ]


# ── §B5.2 validation ────────────────────────────────────────────────────────

def _xml(body: str, *, lo=19700, hi=20200, pid=7) -> str:
    return (
        '<?xml version="1.0"?><satellite_system sat_name="T" ntc_id="1">'
        f'<non_gso_operating_parameters es_lat_max="+90" es_lat_min="-90" '
        f'es_distance="1883" es_density="0.00000028182" c_name="orb_id" '
        f'b_name="azimuth" a_name="latitude" high_freq_mhz="{hi}" '
        f'low_freq_mhz="{lo}" param_id="{pid}">{body}'
        "</non_gso_operating_parameters></satellite_system>"
    )


def test_min_duration_below_one_second_is_rejected():
    """§B5.2: "MIN_DURATION[Latitude] >= 1 second"; zero is never written."""
    with pytest.raises(OperatingParamsError, match="MIN_DURATION"):
        parse_operating_params_xml(_xml('<min_duration a="0">0</min_duration>'))
    with pytest.raises(OperatingParamsError, match="MIN_DURATION"):
        parse_operating_params_xml(_xml('<min_duration a="0">0.5</min_duration>'))
    # Absent — not zero — is the legal way to select the classic algorithm.
    s = parse_operating_params_xml(_xml(""))[0]
    assert s.min_duration is None
    assert s.uses_track_duration is False
    assert s.min_duration_at(0.0) == 0.0


def test_negative_and_out_of_range_values_are_rejected():
    with pytest.raises(OperatingParamsError, match="MIN_EXCLUDE"):
        parse_operating_params_xml(_xml(
            '<min_exclude c="0"><exclusion_zone_angle a="0">-1</exclusion_zone_angle></min_exclude>'))
    with pytest.raises(OperatingParamsError, match="MAX_CO_FREQ"):
        parse_operating_params_xml(_xml('<max_co_freq a="0">-2</max_co_freq>'))
    with pytest.raises(OperatingParamsError, match="MIN_ELEV"):
        parse_operating_params_xml(_xml(
            '<min_elev a="0"><elev_angle b="0">91</elev_angle></min_elev>'))


def test_frequency_range_must_be_present_and_ordered():
    bad = ('<?xml version="1.0"?><non_gso_operating_parameters param_id="1"/>')
    with pytest.raises(OperatingParamsError, match="low_freq_mhz"):
        parse_operating_params_xml(bad)
    with pytest.raises(OperatingParamsError, match="high_freq_mhz"):
        parse_operating_params_xml(_xml("", lo=20200, hi=19700))


def test_one_set_per_frequency_band_is_enforced():
    """§B3.3: "only one set of operating parameters for any frequency band"."""
    a = parse_operating_params_xml(_xml("", lo=19700, hi=20200, pid=7))
    b = parse_operating_params_xml(_xml("", lo=20000, hi=20500, pid=8))
    reg = OperatingParameterRegistry(a + b)
    assert any(i.severity == "error" for i in reg.issues)
    with pytest.raises(OperatingParamsError, match="overlap"):
        reg.for_band(19700, 20200)


def test_band_not_contained_in_any_single_set_is_an_error():
    a = parse_operating_params_xml(_xml("", lo=17800, hi=18600, pid=8))
    b = parse_operating_params_xml(_xml("", lo=19700, hi=20200, pid=7))
    reg = OperatingParameterRegistry(a + b)
    # Straddles the gap between the two sets.
    with pytest.raises(OperatingParamsError, match="not contained"):
        reg.for_band(18000, 20000)
    # Covered by none at all → None, so the caller can fall back.
    assert reg.for_band(10000, 10500) is None


def test_uplink_population_is_required_only_where_used():
    s = parse_operating_params_xml(
        '<?xml version="1.0"?><non_gso_operating_parameters param_id="1" '
        'low_freq_mhz="27500" high_freq_mhz="30000"/>'
    )[0]
    assert s.es_density_per_km2 is None          # a warning at parse time
    with pytest.raises(OperatingParamsError, match="ES_DENSITY"):
        require_uplink_population(s)


# ── engine bridge ───────────────────────────────────────────────────────────

def test_track_duration_is_downlink_only():
    """§D5.2 (printed p. 104): "the minimum track duration is not used for the
    epfd(up) case"."""
    s = parse_operating_params_xml(_xml('<min_duration a="0">2400</min_duration>'))[0]
    assert to_engine_config(s, direction="down")["min_duration_by_lat"] == [
        (-90.0, 90.0, 2400.0)
    ]
    assert to_engine_config(s, direction="up")["min_duration_by_lat"] == []


def test_min_angle_at_es_suppressed_when_min_duration_non_zero():
    """§B3.3 MIN_ANGLE_AT_ES: "Not applicable if the MIN_DURATION[Latitude] is
    non-zero"."""
    body = '<min_duration a="0">2400</min_duration>'
    with_td = parse_operating_params_xml(
        _xml(body).replace('param_id="7"', 'param_id="7" min_angle_at_es="5"'))[0]
    cfg = to_engine_config(with_td, direction="down")
    assert cfg["min_angle_at_es_deg"] == 0.0
    assert cfg["_operating_params"]["min_angle_at_es_suppressed"] is True

    # Without MIN_DURATION the angle survives.
    without = parse_operating_params_xml(
        _xml("").replace('param_id="7"', 'param_id="7" min_angle_at_es="5"'))[0]
    cfg2 = to_engine_config(without, direction="down")
    assert cfg2["min_angle_at_es_deg"] == 5.0
    assert cfg2["_operating_params"]["min_angle_at_es_suppressed"] is False


def test_header_attribute_form_is_accepted():
    """EPS §6.7.2.1 prints min_duration as a header attribute; the arrays of
    §6.7.2.4/6.7.2.5 are what real files use. Accept both, array wins."""
    attr = parse_operating_params_xml(
        _xml("").replace('param_id="7"', 'param_id="7" min_duration="400"'))[0]
    assert attr.min_duration_at(0.0) == 400.0

    both = parse_operating_params_xml(
        _xml('<min_duration a="0">2400</min_duration>').replace(
            'param_id="7"', 'param_id="7" min_duration="400"'))[0]
    assert both.min_duration_at(0.0) == 2400.0        # the array wins
    assert any("array wins" in str(i) for i in both.issues)


# ── the BR's NEXT101 case ───────────────────────────────────────────────────

@pytest.mark.skipif(not _have_xml, reason="NEXT101 operating-parameter XMLs not available")
def test_next101_xml_values():
    reg = load_from_paths(_XMLS)
    by_id = {s.param_id: s for s in reg}
    assert set(by_id) == {7, 8, 9}
    assert (by_id[7].low_freq_mhz, by_id[7].high_freq_mhz) == (19700.0, 20200.0)
    assert (by_id[8].low_freq_mhz, by_id[8].high_freq_mhz) == (17800.0, 18600.0)
    assert (by_id[9].low_freq_mhz, by_id[9].high_freq_mhz) == (27500.0, 30000.0)
    # Only set 7 carries MIN_DURATION — that is the whole point of the case.
    assert by_id[7].min_duration_at(0.0) == 2400.0
    assert by_id[7].uses_track_duration is True
    for pid in (8, 9):
        assert by_id[pid].min_duration is None
        assert by_id[pid].uses_track_duration is False
    # Values shared by all three sets.
    for s in reg:
        assert s.max_co_freq_at(0.0) == 3
        assert s.alpha0_deg(0.0) == 5.0
        assert s.eps0_deg(0.0, 123.0) == 10.0
        assert s.es_distance_km == 1883.0
        assert s.es_density_per_km2 == pytest.approx(2.8182e-7)


@pytest.mark.skipif(not _have_mdb, reason="NEXT101 masks MDB not available")
def test_next101_sets_resolve_per_examined_band():
    """Each examined band resolves to exactly one set, and only the
    19.7–20.2 GHz downlink selects §D5.1.4.2."""
    from src.operating_params import load_from_mask_mdb  # type: ignore[import]

    reg = load_from_mask_mdb(_MASKS_MDB, ntc_id="127520101")
    assert len(reg) == 3
    expected = {
        (19700, 20200): (7, True),
        (17800, 18600): (8, False),
        (27500, 28600): (9, False),
        (29500, 30000): (9, False),
    }
    for (lo, hi), (pid, td) in expected.items():
        s = reg.for_band(lo, hi)
        assert s is not None and s.param_id == pid, f"{lo}-{hi}"
        assert s.uses_track_duration is td, f"{lo}-{hi}"


@pytest.mark.skipif(not _have_mdb, reason="NEXT101 MDBs not available")
def test_next101_loader_selects_the_variant_without_any_override():
    """Closes F01: the §D5.1.4.2 variant becomes reachable from filing data.

    Before this, MIN_DURATION was read from a ``sat_oper`` column that does not
    exist, so the variant could only be forced by hand.
    """
    from src.main import load_from_srs  # type: ignore[import]

    down_td = load_from_srs(_SRS_MDB, pfd_mask_mdb=_MASKS_MDB, ntc_id="127520101",
                            simulation_frequency_ghz=19.95)
    assert down_td["non_gso"]["min_duration_by_lat"] == [(-90.0, 90.0, 2400.0)]
    assert down_td["non_gso"]["max_co_freq_by_lat"] == [(-90.0, 90.0, 3)]

    # Negative control: the other downlink band of the same notice stays classic.
    down_classic = load_from_srs(_SRS_MDB, pfd_mask_mdb=_MASKS_MDB, ntc_id="127520101",
                                 simulation_frequency_ghz=18.2)
    assert down_classic["non_gso"]["min_duration_by_lat"] == []
