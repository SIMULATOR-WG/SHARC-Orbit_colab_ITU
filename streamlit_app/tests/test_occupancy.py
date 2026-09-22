"""Occupancy catalogues: licensed CSV + interval overlap."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from streamlit_app.lib.occupancy import (  # noqa: E402
    OccupancySystem,
    brific_iso_url,
    common_intervals,
    ctry_covers_brazil,
    extract_iso_from_zip,
    extract_srs_from_iso,
    ific_no_from_name,
    locate_srs_parts,
    mhz_to_ghz_interval,
    norm_name,
    parse_anatel_subfaixas_csv,
    parse_mhz,
    select_brazil_ntcs,
    union_intervals,
)

_ANATEL_CSV = """Operador;NomeEstacao_STEL_portal;NumEstacao_STEL_portal;Tipo_orbita_STEL_portal;PosOrbital_STEL_portal;Banda_RF_estacao_STEL_portal;Sentido_STEL_portal;MedFrequenciaInicialMHz_STEL_portal;MedFrequenciaFinalMHz_STEL_portal;BW_Faixa_MHz
OP A;SAT-A;1;GEO;70 W;Ku;Descida ↓;10950;11200;250,00
OP A;SAT-A;1;GEO;70 W;Ku;Subida ↑;14000;14500;500,00
OP A;SAT-A;1;GEO;70 W;C;Descida ↓;3700;4200;500,00
OP B;SAT-B;2;GEO;65 W;Ku;Descida ↓;11100;11450;350,00
OP C;LEO-1;3;NGEO;NGEO;Ka;Descida ↓;17700;19300;1600,00
"""


def test_parse_mhz_comma():
    assert parse_mhz("250,00") == 250.0
    assert parse_mhz("10950") == 10950.0
    assert parse_mhz("") is None


def test_mhz_to_ghz_interval():
    iv = mhz_to_ghz_interval("10950", "11200")
    assert iv == (10.95, 11.2)


def test_parse_anatel_groups_stations_and_directions():
    cat = parse_anatel_subfaixas_csv(_ANATEL_CSV)
    by_name = {s.name: s for s in cat}
    assert set(by_name) == {"SAT-A", "SAT-B", "LEO-1"}
    a = by_name["SAT-A"]
    assert a.source == "anatel"
    assert a.orbit == "GEO"
    assert a.id == f"anatel:{norm_name('SAT-A')}"
    # C + Ku downlink merged into two disjoint intervals
    assert a.intervals("downlink") == [(3.7, 4.2), (10.95, 11.2)]
    assert a.intervals("uplink") == [(14.0, 14.5)]
    assert "Ku" in a.rf_bands and "C" in a.rf_bands
    assert by_name["LEO-1"].orbit == "NGEO"


def test_common_and_union_like_aggregate():
    cat = parse_anatel_subfaixas_csv(_ANATEL_CSV)
    a = next(s for s in cat if s.name == "SAT-A")
    b = next(s for s in cat if s.name == "SAT-B")
    assert common_intervals([a, b], "downlink") == [(11.1, 11.2)]
    union = union_intervals([a, b], "downlink")
    assert union[0] == (3.7, 4.2)
    assert any(
        abs(lo - 10.95) < 1e-9 and abs(hi - 11.45) < 1e-9 for lo, hi in union
    )


def test_common_empty_when_disjoint():
    a = OccupancySystem(
        id="a", source="anatel", name="A",
        downlink_ghz=[[10.95, 11.2]],
    )
    b = OccupancySystem(
        id="b", source="anatel", name="B",
        downlink_ghz=[[17.8, 18.6]],
    )
    assert common_intervals([a, b], "downlink") == []
    assert union_intervals([a, b], "downlink") == [(10.95, 11.2), (17.8, 18.6)]


def test_select_brazil_ntcs_any_adm_if_operates_in_brazil():
    notices = [
        {"ntc_id": "1", "adm": "B", "sat_name": ""},
        {"ntc_id": "2", "adm": "USA", "sat_name": ""},
        {"ntc_id": "3", "adm": "CHN", "sat_name": ""},
        {"ntc_id": "4", "adm": "USA", "sat_name": ""},
        {"ntc_id": "5", "adm": "F", "sat_name": ""},
    ]
    keep = select_brazil_ntcs(
        notices,
        sat_name_by_ntc={"2": "STARONE D1", "3": "ATMOS IOS"},
        srv_br_ntcs={"3", "4"},
        es_br_ntcs=set(),
        anatel_names={norm_name("STARONE D1")},
    )
    assert keep["1"] == ["adm_b"]
    assert keep["2"] == ["anatel_name"]
    assert keep["3"] == ["srv_br"]
    assert keep["4"] == ["srv_br"]          # USA constellation serving Brazil
    assert "5" not in keep                  # Region 1 only, no Brazil service


def test_ctry_covers_brazil_region_world_and_exclude():
    assert ctry_covers_brazil("B")
    assert ctry_covers_brazil("xr2")
    assert ctry_covers_brazil("XAA")
    assert not ctry_covers_brazil("XR1")
    assert not ctry_covers_brazil("USA")
    assert not ctry_covers_brazil("B", excl="Y")
    assert ctry_covers_brazil("B", excl="N")


def test_brific_iso_url_matches_portal():
    assert brific_iso_url("3079") == (
        "https://www.itu.int/epublications/brific-space/api/v1/ific/edition/"
        "3079/document?path=iso&download=true"
    )


def test_ific_no_from_iso_filename():
    assert ific_no_from_name("BR_IFIC_3079.iso") == "3079"
    assert ific_no_from_name("ific3079.iso") == "3079"
    assert ific_no_from_name("S_IFIC3079.iso") == "3079"
    assert ific_no_from_name("SRS.mdb") == ""


def test_extract_srs_from_iso(tmp_path: Path):
    import shutil
    import subprocess

    import pytest

    xorriso = shutil.which("xorriso")
    if xorriso is None:
        pytest.skip("xorriso not installed")
    src = tmp_path / "tree" / "Databases" / "SRS_Data"
    src.mkdir(parents=True)
    (src / "SRS.mdb").write_bytes(b"JET fake srs\n")
    (tmp_path / "tree" / "Databases" / "IFIC_data").mkdir()
    (tmp_path / "tree" / "Databases" / "IFIC_data" / "ific3079.mdb").write_bytes(
        b"ific week\n"
    )
    iso = tmp_path / "BR_IFIC_3079.iso"
    subprocess.run(
        [xorriso, "-as", "mkisofs", "-o", str(iso), str(tmp_path / "tree")],
        check=True, capture_output=True, text=True,
    )
    dest = tmp_path / "out" / "SRS.mdb"
    extract_srs_from_iso(iso, dest)
    assert dest.read_bytes() == b"JET fake srs\n"
    assert ific_no_from_name(iso.name) == "3079"

    import zipfile
    zpath = tmp_path / "R-SP-LN.IS-2026-OAS-Q18-ZIP-M.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.write(iso, "S_IFIC3079.iso")
        zf.writestr("S_IFIC3079.txt", "Name: S_IFIC3079.iso\n")
    extracted = extract_iso_from_zip(zpath, tmp_path / "unz")
    assert extracted.name == "S_IFIC3079.iso"
    assert extracted.stat().st_size == iso.stat().st_size


def test_locate_srs_split_parts(tmp_path: Path):
    d = tmp_path / "srs3079"
    d.mkdir()
    p1 = d / "srs3079_part1of4.mdb"
    p3 = d / "srs3079_part3of4.mdb"
    p2 = d / "srs3079_part2of4.mdb"
    p1.write_bytes(b"1")
    p2.write_bytes(b"2")
    p3.write_bytes(b"3")
    catalog, grp = locate_srs_parts(d)
    assert catalog == p1
    assert grp == p3
    catalog, grp = locate_srs_parts(p1)
    assert catalog == p1 and grp == p3
    single = tmp_path / "ific3079.mdb"
    single.write_bytes(b"x")
    catalog, grp = locate_srs_parts(single)
    assert catalog == grp == single


def test_touching_band_edges_are_not_a_shared_band():
    """10.7-12.75 against 12.75-14.5 share an edge, not a band.

    The zero-width intersection used to be reported on the page as a common
    occupied band and printed as "12.750-12.750 GHz".
    """
    from streamlit_app.lib.occupancy import intersect_sets  # noqa: PLC0415

    assert intersect_sets([(10.7, 12.75)], [(12.75, 14.5)]) == []
    assert intersect_sets([(10.7, 12.75)], [(12.0, 14.5)]) == [(12.0, 12.75)]
    # A real but very narrow overlap still counts.
    assert intersect_sets([(10.7, 12.75)], [(12.7499, 14.5)]) == [(12.7499, 12.75)]


def test_sns_catalog_survives_a_fresh_install(tmp_path, monkeypatch):
    """No meta file, no catalogue: return nothing, do not raise.

    ``Path("")`` is ``PosixPath(".")``, which always exists, so an empty "mdb"
    entry sent the loader off to re-index the current working directory as an
    SRS database and the page died with a traceback before the setup card that
    tells the user what to do could render.
    """
    from streamlit_app.lib import occupancy as occ  # noqa: PLC0415

    monkeypatch.setattr(occ, "SNS_META", tmp_path / "absent_meta.json")
    monkeypatch.setattr(occ, "SNS_CATALOG", tmp_path / "absent_catalog.json")
    assert occ.sns_meta() == {}
    assert occ.load_sns_catalog() == []

    # Meta present but with an empty path is the same trap.
    (tmp_path / "absent_meta.json").write_text(
        '{"mdb": "", "n_notice_total": 1, "select_logic": 0}')
    assert occ.load_sns_catalog() == []
