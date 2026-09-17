"""`emi_rcp` is relative to the notified station, so its sense flips per kind.

The page read every frequency group with the space-station convention, which
put every earth-station band in the opposite direction. It is not a cosmetic
label: `OccupancySystem.intervals("downlink")` is empty for such a row, so the
system is dropped by the direction filter and never reaches the bars. A filing
uploaded for study is mostly earth stations — 86 of the 87 notices in
USASAT-NGSO-3series_Config1_SRS_Ku_TTC — so the whole catalogue went missing.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import streamlit_app.lib.br_occupancy as br  # noqa: E402


# One satellite and one earth station, both RECEIVING, in the two bands the
# real file uses: the satellite at 13.75-14 GHz (an uplink feeds it) and the
# earth station at 12.2-12.75 GHz (a downlink feeds it).
_NOTICE = [
    {"ntc_id": "1", "ntc_type": "N", "adm": "USA"},
    {"ntc_id": "2", "ntc_type": "S", "adm": "USA"},
]
_GRP = [
    {"grp_id": "10", "ntc_id": "1", "emi_rcp": "R",
     "freq_min": "13750", "freq_max": "14000"},
    {"grp_id": "11", "ntc_id": "1", "emi_rcp": "E",
     "freq_min": "17800", "freq_max": "18600"},
    {"grp_id": "20", "ntc_id": "2", "emi_rcp": "R",
     "freq_min": "12200", "freq_max": "12750"},
    {"grp_id": "21", "ntc_id": "2", "emi_rcp": "E",
     "freq_min": "14000", "freq_max": "14500"},
]


@pytest.fixture()
def fake_srs(monkeypatch):
    def _export(path, table):
        if table == "notice":
            return list(_NOTICE)
        if table == "grp":
            return list(_GRP)
        return []

    monkeypatch.setattr(br, "_mdb_export", _export)
    return Path("nowhere.mdb")


def test_direction_follows_the_notified_station(fake_srs):
    systems, _ = br.parse_sns_catalog(fake_srs)
    by_ntc = {s.ntc_id: s for s in systems}

    space = by_ntc["1"]
    assert space.kind == "space"
    # A satellite that receives is fed by an uplink.
    assert space.uplink_ghz == [[13.75, 14.0]]
    assert space.downlink_ghz == [[17.8, 18.6]]

    earth = by_ntc["2"]
    assert earth.kind == "earth"
    # An earth station that receives is fed by a downlink — the case the
    # space-station reading got backwards.
    assert earth.downlink_ghz == [[12.2, 12.75]]
    assert earth.uplink_ghz == [[14.0, 14.5]]


def test_earth_station_survives_the_downlink_filter(fake_srs):
    systems, _ = br.parse_sns_catalog(fake_srs)
    earth = next(s for s in systems if s.ntc_id == "2")
    # 10.7-17.7 GHz downlink: what the page asked for when it showed nothing.
    assert br.intervals_touch_range(earth.intervals("downlink"), 10.7, 17.7)


def _write_v2_source(root: Path, sid: str) -> None:
    d = root / sid
    d.mkdir(parents=True)
    (d / "catalog.json").write_text(json.dumps([
        {"id": f"sns:{sid}:2", "source": "sns", "name": "ES", "kind": "earth",
         "ntc_id": "2", "adm": "USA",
         "downlink_ghz": [], "uplink_ghz": [[12.2, 12.75]]},
        {"id": f"sns:{sid}:1", "source": "sns", "name": "SAT", "kind": "space",
         "ntc_id": "1", "adm": "USA",
         "downlink_ghz": [[17.8, 18.6]], "uplink_ghz": []},
    ]))
    (d / "meta.json").write_text(json.dumps({"select_logic": 2}))


def test_an_indexed_catalogue_is_corrected_without_re_reading_the_mdb(tmp_path, monkeypatch):
    """The MDB may be minutes of work away, or gone. The swap is exact."""
    monkeypatch.setattr(br, "SOURCES_DIR", tmp_path)
    _write_v2_source(tmp_path, "old")

    rows = br.load_source_catalog("old")
    earth = next(s for s in rows if s.kind == "earth")
    space = next(s for s in rows if s.kind == "space")
    assert earth.downlink_ghz == [[12.2, 12.75]] and earth.uplink_ghz == []
    # A space row was already right and must not move.
    assert space.downlink_ghz == [[17.8, 18.6]] and space.uplink_ghz == []

    meta = json.loads((tmp_path / "old" / "meta.json").read_text())
    assert meta["select_logic"] == br._EARTH_DIR_FIXED_AT

    # Loading again must not swap back.
    again = next(s for s in br.load_source_catalog("old") if s.kind == "earth")
    assert again.downlink_ghz == [[12.2, 12.75]]


def test_a_current_catalogue_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(br, "SOURCES_DIR", tmp_path)
    d = tmp_path / "new"
    d.mkdir()
    (d / "catalog.json").write_text(json.dumps([
        {"id": "sns:new:2", "source": "sns", "name": "ES", "kind": "earth",
         "ntc_id": "2", "downlink_ghz": [[12.2, 12.75]], "uplink_ghz": []},
    ]))
    (d / "meta.json").write_text(json.dumps(
        {"select_logic": br.SNS_SELECT_LOGIC}))

    row = br.load_source_catalog("new")[0]
    assert row.downlink_ghz == [[12.2, 12.75]] and row.uplink_ghz == []
