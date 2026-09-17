"""The national licensed catalogue: parsing, and the format a country must follow.

The page was built around Anatel's ``satelites.zip``. Any administration may
supply the same thing, so the parser has to accept the format by its meaning
rather than by Anatel's exact Portuguese column spellings, and the help has to
state what those fields are.

The anchor for the whole refactor is a golden fixture that is already tracked in
the repository: parsing ``docs/satelites/stel_satelites_subfaixas.csv`` must keep
reproducing the cached Anatel catalogue byte for byte.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from streamlit_app.lib.br_occupancy import (  # noqa: E402
    _decode_csv_bytes,
    parse_anatel_subfaixas_csv,
)

_GOLDEN = REPO / "docs" / "satelites" / "stel_satelites_subfaixas.csv"
_CACHED = REPO / "streamlit_app" / "data" / "br_occupancy" / "anatel_catalog.json"


@pytest.mark.skipif(not _GOLDEN.exists(), reason="Anatel CSV fixture not present")
def test_golden_csv_reproduces_the_cached_catalog():
    """Locks today's behaviour before the parser is generalised."""
    got = [asdict(s) for s in
           parse_anatel_subfaixas_csv(_decode_csv_bytes(_GOLDEN.read_bytes()))]
    assert len(got) == 58
    if _CACHED.exists():
        # Both sides go through the model, so fields added later — with
        # defaults, as the country evidence was — do not make an old cache look
        # like a parser change.
        from streamlit_app.lib.br_occupancy import system_from_dict  # noqa: PLC0415

        cached = json.loads(_CACHED.read_text())
        # Compare on the keys the cache was written with. Fields added later —
        # the licensing administration and the station kind — are asserted
        # separately, because an old file cannot carry them.
        # `adm` exists in the old file but was written empty, and `kind` did
        # not exist at all; both are asserted below instead.
        keys = set(cached[0]) - {"adm", "kind"}
        assert ([{k: r[k] for k in keys} for r in got]
                == [{k: r[k] for k in keys} for r in cached])
    assert all(r["adm"] == "B" for r in got), "a licensed station is in its country"
    assert all(r["kind"] == "space" for r in got)


@pytest.mark.skipif(not _GOLDEN.exists(), reason="Anatel CSV fixture not present")
def test_golden_csv_shape():
    """The properties another administration has to reproduce."""
    raw = _GOLDEN.read_bytes()
    assert raw[:3] == b"\xef\xbb\xbf", "UTF-8 BOM"
    text = _decode_csv_bytes(raw)
    header = text.splitlines()[0]
    assert header.count(";") > header.count(","), "semicolon delimited"
    systems = parse_anatel_subfaixas_csv(text)
    # Frequencies are MHz in the file and GHz on the objects.
    lows = [lo for s in systems for lo, _hi in s.intervals("both")]
    assert min(lows) > 0.1 and max(lows) < 100.0, (min(lows), max(lows))
    # Both directions are recognised, not everything filed as downlink.
    assert any(s.downlink_ghz for s in systems)
    assert any(s.uplink_ghz for s in systems)


def _csv(rows: "list[str]", header: str) -> str:
    return "\n".join([header, *rows])


_HDR_PT = ("Operador;NomeEstacao_STEL_portal;NumEstacao_STEL_portal;"
           "Tipo_orbita_STEL_portal;PosOrbital_STEL_portal;"
           "Banda_RF_estacao_STEL_portal;Sentido_STEL_portal;"
           "MedFrequenciaInicialMHz_STEL_portal;MedFrequenciaFinalMHz_STEL_portal")


def test_portuguese_header_still_parses():
    text = _csv(["OP;SAT-1;1;GEO;70W;Ku;Descida;10700;11700",
                 "OP;SAT-1;1;GEO;70W;Ku;Subida;14000;14500"], _HDR_PT)
    systems = parse_anatel_subfaixas_csv(text)
    assert len(systems) == 1
    s = systems[0]
    assert s.downlink_ghz == [[10.7, 11.7]]
    assert s.uplink_ghz == [[14.0, 14.5]]


def test_brazilian_decimal_comma_is_accepted():
    text = _csv(["OP;SAT-2;2;GEO;70W;L;Descida;1635,725;1636,125"], _HDR_PT)
    s = parse_anatel_subfaixas_csv(text)[0]
    (lo, hi), = s.downlink_ghz
    assert lo == pytest.approx(1.635725)
    assert hi == pytest.approx(1.636125)


def test_missing_mandatory_columns_is_an_error_not_an_empty_result():
    """A silently empty catalogue would look like "this country has no stations"."""
    with pytest.raises(ValueError):
        parse_anatel_subfaixas_csv("Operador;Banda_RF\nOP;Ku")


_HDR_EN = ("operator;station;station_id;orbit;orbital_position;"
           "rf_band;direction;freq_min_mhz;freq_max_mhz")


def test_english_header_parses_the_same_as_the_portuguese_one():
    """Another administration may name the columns in its own language."""
    pt = parse_anatel_subfaixas_csv(_csv(
        ["OP;SAT-1;1;GEO;70W;Ku;Descida;10700;11700",
         "OP;SAT-1;1;GEO;70W;Ku;Subida;14000;14500"], _HDR_PT))
    en = parse_anatel_subfaixas_csv(_csv(
        ["OP;SAT-1;1;GEO;70W;Ku;downlink;10700;11700",
         "OP;SAT-1;1;GEO;70W;Ku;uplink;14000;14500"], _HDR_EN))
    assert len(pt) == len(en) == 1
    assert pt[0].downlink_ghz == en[0].downlink_ghz
    assert pt[0].uplink_ghz == en[0].uplink_ghz


@pytest.mark.parametrize("word", [
    "Subida", "uplink", "UP", "Earth-to-space", "E-S", "ascendente",
])
def test_uplink_vocabulary(word):
    from streamlit_app.lib.br_occupancy import _is_uplink  # noqa: PLC0415

    assert _is_uplink(word), word


@pytest.mark.parametrize("word", [
    "Descida", "downlink", "space-to-earth", "S-E", "descendente",
])
def test_downlink_vocabulary(word):
    from streamlit_app.lib.br_occupancy import _is_downlink, _is_uplink  # noqa: PLC0415

    assert _is_downlink(word), word
    assert not _is_uplink(word), word


def test_missing_column_error_names_what_is_missing():
    """The old message named Anatel's file and said nothing about the column."""
    with pytest.raises(ValueError) as exc:
        parse_anatel_subfaixas_csv("operator;station;rf_band\nOP;SAT;Ku")
    msg = str(exc.value)
    assert "lower frequency (MHz)" in msg and "upper frequency (MHz)" in msg, msg
    assert "station name" not in msg, "station was present; do not report it missing"
    assert "help" in msg.lower()


def test_help_documents_the_format_the_parser_accepts():
    """Item 2's actual ask: the fields must be explained on the page.

    Anything the help promises must be something the parser really accepts, so
    this checks the two against each other rather than just that text exists.
    """
    from streamlit_app.lib.manual import _HELP  # noqa: PLC0415

    doc = _HELP.get("national_catalog_format")
    assert doc, "no help entry for the licensed-station table format"

    # Every mandatory column is named, and marked mandatory.
    for col in ("station", "freq_min_mhz", "freq_max_mhz"):
        assert col in doc, col
    assert doc.count("**yes**") == 3, "exactly three mandatory fields"

    # The traps a foreign file falls into are stated.
    for trap in ("MHz", "Decimal separator", "Delimiter", "Encoding", "Direction"):
        assert trap in doc, trap

    # Column names the help offers must actually resolve in the parser.
    header = ("operator;station;station_id;orbit;orbital_position;"
              "rf_band;direction;freq_min_mhz;freq_max_mhz;valid_until")
    rows = ["OP;SAT;1;GEO;70W;Ku;downlink;10700;11700;2030-12-31"]
    systems = parse_anatel_subfaixas_csv("\n".join([header, *rows]))
    assert len(systems) == 1
    s = systems[0]
    assert s.name == "SAT" and s.operator == "OP" and s.orbit == "GEO"
    assert s.position == "70W" and s.rf_bands == ["Ku"]
    assert s.downlink_ghz == [[10.7, 11.7]]

    # And the page asks for it.
    page = (REPO / "streamlit_app" / "pages" / "H_Brazil_Occupancy.py").read_text()
    assert "national_catalog_format" in page


# ── country evidence on every row ───────────────────────────────────────────

def test_country_codes_reads_the_evidence_it_is_given():
    from streamlit_app.lib.br_occupancy import OccupancySystem  # noqa: PLC0415

    s = OccupancySystem(
        id="x", source="sns", name="N", adm="USA",
        srv_ctry=["B", "XR2"], es_ctry=["ARG"], srv_excl=["XR2"],
    )
    # The notifying administration alone.
    assert s.country_codes(rule="notified") == {"USA"}
    # Everything declared. The exclusion is NOT subtracted: f_excl_api belongs
    # to one frequency group, so a notice that serves a country in one group
    # and excludes it in another still occupies spectrum there.
    assert s.country_codes() == {"USA", "B", "ARG", "XR2"}
    assert s.srv_excl == ["XR2"], "the exclusion is still on the row as evidence"


def test_country_evidence_survives_a_round_trip():
    """Old cached rows must still load, and new ones keep their evidence."""
    from dataclasses import asdict  # noqa: PLC0415

    from streamlit_app.lib.br_occupancy import (  # noqa: PLC0415
        OccupancySystem, system_from_dict,
    )

    s = OccupancySystem(id="x", source="sns", name="N", adm="B",
                        srv_ctry=["XAA"], es_ctry=["B"], kind="space",
                        ntwk_org="EUT")
    back = system_from_dict(asdict(s))
    assert back.srv_ctry == ["XAA"] and back.es_ctry == ["B"]
    assert back.kind == "space" and back.ntwk_org == "EUT"

    # A row written before the fields existed loads with empty evidence, not a
    # KeyError, and then only its administration locates it.
    legacy = system_from_dict({"id": "y", "source": "sns", "name": "M", "adm": "J"})
    assert legacy.srv_ctry == [] and legacy.kind == ""
    assert legacy.country_codes() == {"J"}


def test_keep_only_rejects_a_country_it_cannot_honour():
    """The compatibility path reproduces the Brazil rule and nothing else."""
    from streamlit_app.lib.br_occupancy import parse_sns_catalog  # noqa: PLC0415

    with pytest.raises(ValueError, match="keep_only"):
        parse_sns_catalog(Path("/nonexistent.mdb"), keep_only="USA")


# ── loading a foreign national catalogue ────────────────────────────────────

_HDR_MIN = "station;freq_min_mhz;freq_max_mhz;direction"


def _reg(tmp_path, monkeypatch):
    from streamlit_app.lib import br_occupancy as br  # noqa: PLC0415

    monkeypatch.setattr(br, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(br, "SOURCES_DIR", tmp_path / "sources")
    monkeypatch.setattr(br, "SOURCES_INDEX", tmp_path / "sources" / "index.json")
    monkeypatch.setattr(br, "SNS_CATALOG", tmp_path / "sns_catalog.json")
    monkeypatch.setattr(br, "SNS_META", tmp_path / "sns_meta.json")
    return br


def test_a_foreign_catalogue_registers_and_is_findable_by_country(tmp_path, monkeypatch):
    br = _reg(tmp_path, monkeypatch)
    text = "\n".join([_HDR_MIN,
                      "SAT-A;10700;11700;downlink",
                      "SAT-A;14000;14500;uplink",
                      "SAT-B;3700;4200;downlink"])
    src = br.register_national_csv(text, label="IFT licensed", adm="mex")
    assert src.kind == "national" and src.n_systems == 2
    rows = br.load_source_catalog(src.id)
    assert {r.name for r in rows} == {"SAT-A", "SAT-B"}
    # The licensing administration is what locates them.
    assert all(r.adm == "MEX" for r in rows)
    assert all("MEX" in r.country_codes() for r in rows)
    # It sits in the same registry as the filing catalogues.
    assert src.id in {s.id for s in br.read_sources()}


def test_a_catalogue_in_ghz_is_refused(tmp_path, monkeypatch):
    """It parses cleanly and draws bands a thousand times too narrow."""
    br = _reg(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="MHz"):
        br.register_national_csv(f"{_HDR_MIN}\nSAT;10.7;11.7;downlink",
                                 label="x", adm="X")
    assert br.read_sources() == []


def test_an_empty_or_frequency_less_table_is_refused(tmp_path, monkeypatch):
    br = _reg(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        br.register_national_csv(_HDR_MIN, label="x", adm="X")


def test_zip_member_is_chosen_not_guessed():
    import io  # noqa: PLC0415
    import zipfile  # noqa: PLC0415

    from streamlit_app.lib.br_occupancy import pick_catalog_csv  # noqa: PLC0415

    def _zip(members):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for n, b in members.items():
                z.writestr(n, b)
        return buf.getvalue()

    # Anatel's own member name wins even among several CSVs.
    name, _ = pick_catalog_csv(_zip({
        "other.csv": "a", "stel_satelites_subfaixas.csv": "bb"}))
    assert name == "stel_satelites_subfaixas.csv"
    # A single CSV is taken whatever it is called.
    name, data = pick_catalog_csv(_zip({"readme.txt": "x", "mine.csv": "hello"}))
    assert name == "mine.csv" and data == b"hello"
    # Several unnamed CSVs: the largest, which is the table rather than a note.
    name, _ = pick_catalog_csv(_zip({"a.csv": "x", "b.csv": "x" * 500}))
    assert name == "b.csv"
    with pytest.raises(ValueError, match="no .csv"):
        pick_catalog_csv(_zip({"a.txt": "x"}))
