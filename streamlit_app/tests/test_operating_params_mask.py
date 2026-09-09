"""S.1503-4 operating-parameter masks (``f_mask='R'``) as the sat_oper fallback.

The EPS V41 structure retires ``sat_oper`` and moves MAX_CO_FREQ / MIN_DURATION
into an XML mask, one set per contiguous frequency range. Pre-S.1503-4 filings
keep using ``sat_oper``, so the fallback must never disturb them.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.srs_reader import (  # noqa: E402
    _lat_entries_to_bands,
    parse_operating_params_xml,
    read_operating_params_mask,
    read_sat_oper,
    read_sat_oper_min_duration,
)

_V41_SRS = REPO / "ITU_fillings" / "ITU_doc" / "127520101 SRS.MDB"
_V41_MASKS = REPO / "ITU_fillings" / "ITU_doc" / "127520101 Masks.MDB"
_V10_SRS = REPO / "docs" / "test_data" / "MCSAT_LEO_Ka_SRS.mdb"

_needs_v41 = pytest.mark.skipif(
    not (_V41_SRS.exists() and _V41_MASKS.exists()),
    reason="EPS V41 sample package not present",
)

_XML = """<?xml version="1.0"?>
<satellite_system sat_name="X" ntc_id="1">
  <non_gso_operating_parameters low_freq_mhz="19700" high_freq_mhz="20200"
      param_id="7" es_distance="1883" es_density="0.00000028182">
    <min_exclude c="0"><exclusion_zone_angle a="0">5</exclusion_zone_angle></min_exclude>
    <max_co_freq a="0">3</max_co_freq>
    <min_duration a="0">2400</min_duration>
    <min_elev a="0"><elev_angle b="0">10</elev_angle></min_elev>
  </non_gso_operating_parameters>
</satellite_system>"""


# ── latitude banding ─────────────────────────────────────────────────────────

def test_single_latitude_covers_the_sphere():
    """Nearest-latitude semantics: one point applies everywhere."""
    assert _lat_entries_to_bands([(0.0, 3.0)]) == [(-90.0, 90.0, 3.0)]


def test_multiple_latitudes_split_at_midpoints():
    bands = _lat_entries_to_bands([(-40.0, 1.0), (0.0, 2.0), (40.0, 3.0)])
    assert bands == [
        (-90.0, -20.0, 1.0),
        (-20.0, 20.0, 2.0),
        (20.0, 90.0, 3.0),
    ]


def test_empty_entries_give_no_bands():
    assert _lat_entries_to_bands([]) == []


# ── XML parsing ──────────────────────────────────────────────────────────────

def test_parses_every_field_including_nested_ones():
    (op,) = parse_operating_params_xml(_XML)
    assert (op.param_id, op.low_freq_mhz, op.high_freq_mhz) == (7, 19700.0, 20200.0)
    assert op.max_co_freq == [(-90.0, 90.0, 3.0)]
    assert op.min_duration == [(-90.0, 90.0, 2400.0)]
    # Nested under <min_elev>/<min_exclude> rather than direct text.
    assert op.min_elev == [(-90.0, 90.0, 10.0)]
    assert op.exclusion_zone == [(-90.0, 90.0, 5.0)]


def test_absent_min_duration_yields_empty_not_zero():
    """A set without MIN_DURATION selects the classic path, not a 0 s window."""
    (op,) = parse_operating_params_xml(_XML.replace(
        '<min_duration a="0">2400</min_duration>', ""))
    assert op.min_duration == []


def test_malformed_xml_raises():
    with pytest.raises(ValueError):
        parse_operating_params_xml("<not-closed")


# ── set selection by frequency ───────────────────────────────────────────────

@_needs_v41
@pytest.mark.parametrize("freq_mhz,param_id,has_track", [
    (19700.02, 7, True),    # the track-duration band
    (20200.0, 7, True),     # inclusive upper edge
    (17800.0, 8, False),    # classic downlink
    (18600.0, 8, False),
    (29000.0, 9, False),    # uplink
])
def test_frequency_picks_the_containing_set(freq_mhz, param_id, has_track):
    op = read_operating_params_mask(str(_V41_MASKS), "127520101", freq_mhz)
    assert op is not None and op.param_id == param_id
    assert bool(op.min_duration) is has_track
    assert op.max_co_freq == [(-90.0, 90.0, 3.0)]  # Nco=3 on every set


@_needs_v41
def test_frequency_outside_every_set_returns_none():
    assert read_operating_params_mask(str(_V41_MASKS), "127520101", 12000.0) is None


# ── the fallback wiring ──────────────────────────────────────────────────────

@_needs_v41
def test_fallback_supplies_nco_and_min_duration_when_sat_oper_empty():
    """This filing's sat_oper is empty; both values must come from the mask."""
    from src.srs_reader import read_sat_oper as _rso
    assert _rso(str(_V41_SRS), "127520101") == []  # no mask path → nothing

    nco = read_sat_oper(str(_V41_SRS), "127520101", str(_V41_MASKS), 19700.02)
    assert nco == [(-90.0, 90.0, 3)]
    assert isinstance(nco[0][2], int)  # Nco is a satellite count

    md = read_sat_oper_min_duration(
        str(_V41_SRS), "127520101", str(_V41_MASKS), 19700.02)
    assert md == [(-90.0, 90.0, 2400.0)]


@_needs_v41
def test_fallback_respects_the_band_that_declares_no_tracking():
    """17.8-18.6 GHz is param 8 — Nco applies, tracking does not."""
    assert read_sat_oper(
        str(_V41_SRS), "127520101", str(_V41_MASKS), 18000.0) == [(-90.0, 90.0, 3)]
    assert read_sat_oper_min_duration(
        str(_V41_SRS), "127520101", str(_V41_MASKS), 18000.0) == []


@pytest.mark.skipif(not _V10_SRS.exists(), reason="v10 sample not present")
def test_sat_oper_still_wins_for_pre_s1503_4_filings():
    """The fallback must not shadow a populated sat_oper."""
    assert read_sat_oper(str(_V10_SRS), "101") == [(-90.0, 90.0, 50)]
    # Even with a mask path supplied, the table takes precedence.
    assert read_sat_oper(
        str(_V10_SRS), "101", str(_V41_MASKS), 19700.02) == [(-90.0, 90.0, 50)]


def test_missing_mask_mdb_is_not_an_error():
    """No R mask (every legacy filing) simply means no fallback."""
    assert read_operating_params_mask("does/not/exist.mdb", "1", 19700.0) is None


@_needs_v41
def test_load_from_srs_pins_nco_and_tracking_from_the_mask():
    """End-to-end: the engine config carries both values off the R mask."""
    from src.main import load_from_srs

    cfg = load_from_srs(str(_V41_SRS), pfd_mask_mdb=str(_V41_MASKS),
                        ntc_id="127520101", mask_id=4)
    ngso = cfg["non_gso"]
    assert ngso["max_co_freq_by_lat"] == [(-90.0, 90.0, 3)]
    assert ngso["min_duration_by_lat"] == [(-90.0, 90.0, 2400.0)]

    # mask 1 is the classic band: Nco yes, tracking no.
    cfg1 = load_from_srs(str(_V41_SRS), pfd_mask_mdb=str(_V41_MASKS),
                         ntc_id="127520101", mask_id=1)
    assert cfg1["non_gso"]["max_co_freq_by_lat"] == [(-90.0, 90.0, 3)]
    assert cfg1["non_gso"]["min_duration_by_lat"] == []
