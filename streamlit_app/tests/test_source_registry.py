"""Several indexed filing catalogues at once, instead of a single slot.

Until now every ingest wrote the same two files, so indexing a second SRS
destroyed the first with no message. The developer's own cache is the evidence:
a weekly IFIC indexed at 07:40 and replaced by the full SRS at 09:59, with the
IFIC's mdb still on disk and its catalogue gone.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

APP = REPO / "streamlit_app"
if str(APP) not in sys.path:                 # the page imports `lib.…`
    sys.path.insert(0, str(APP))

from streamlit_app.lib import occupancy as occ  # noqa: E402


@pytest.fixture()
def cache(tmp_path, monkeypatch):
    """Point every cache path at a temp dir; the real one is never touched."""
    monkeypatch.setattr(occ, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(occ, "SOURCES_DIR", tmp_path / "sources")
    monkeypatch.setattr(occ, "SOURCES_INDEX", tmp_path / "sources" / "index.json")
    monkeypatch.setattr(occ, "SNS_CATALOG", tmp_path / "sns_catalog.json")
    monkeypatch.setattr(occ, "SNS_META", tmp_path / "sns_meta.json")
    return tmp_path


def _sys(ntc: str, name: str = "SAT") -> occ.OccupancySystem:
    return occ.OccupancySystem(id=f"sns:{ntc}", source="sns", name=name,
                              ntc_id=ntc, adm="B", downlink_ghz=[[10.7, 11.7]])


def test_two_catalogues_coexist(cache):
    a = occ._write_source(occ.CatalogSource(id="srs3079", label="Full SRS"),
                         [_sys("111"), _sys("222")], {"n_notice_total": 9})
    b = occ._write_source(occ.CatalogSource(id="ific3085", label="Weekly 3085",
                                          kind="ific"),
                         [_sys("111"), _sys("333")], {"n_notice_total": 4})
    ids = {s.id for s in occ.read_sources()}
    assert ids == {"srs3079", "ific3085"}, ids
    assert a.n_systems == 2 and b.n_systems == 2
    # Both catalogues survive, including the notice they share.
    assert len(occ.load_source_catalog("srs3079")) == 2
    assert len(occ.load_source_catalog("ific3085")) == 2


def test_shared_ntc_ids_do_not_shadow_each_other(cache):
    """77 of 174 weekly-IFIC notices also appear in the full SRS."""
    occ._write_source(occ.CatalogSource(id="srs3079", label="a"), [_sys("111")], {})
    occ._write_source(occ.CatalogSource(id="ific3085", label="b"), [_sys("111")], {})
    one = occ.load_source_catalog("srs3079")[0]
    two = occ.load_source_catalog("ific3085")[0]
    assert one.id != two.id, (one.id, two.id)
    assert one.source_id == "srs3079" and two.source_id == "ific3085"
    # Keyed by id, as the page does, neither disappears.
    assert len({one.id, two.id}) == 2


def test_removing_one_leaves_the_other(cache):
    occ._write_source(occ.CatalogSource(id="a", label="a"), [_sys("1")], {})
    occ._write_source(occ.CatalogSource(id="b", label="b"), [_sys("2")], {})
    assert occ.remove_source("a") is True
    assert [s.id for s in occ.read_sources()] == ["b"]
    assert occ.load_source_catalog("a") == []
    assert len(occ.load_source_catalog("b")) == 1
    # Removing something that is not there is a no-op, not an error.
    assert occ.remove_source("a") is False


def test_legacy_single_slot_is_adopted_not_lost(cache):
    """Whoever already indexed an SRS keeps it when the registry appears."""
    occ._write_json(occ.SNS_CATALOG, [{"id": "sns:777", "source": "sns",
                                     "name": "OLD", "ntc_id": "777"}])
    occ._write_json(occ.SNS_META, {"ific_no": "3079", "kind": "srs",
                                 "n_notice_total": 15909,
                                 "fetched_at": "2026-09-10T12:59:29Z"})
    sources = occ.read_sources()
    assert len(sources) == 1, sources
    src = sources[0]
    assert src.ific_no == "3079" and src.n_systems == 1
    assert "3079" in src.label
    rows = occ.load_source_catalog(src.id)
    assert rows and rows[0].source_id == src.id
    # The old files are left in place for an older build to read.
    assert occ.SNS_CATALOG.exists()
    # And the migration runs once.
    before = json.loads(occ.SOURCES_INDEX.read_text())
    occ.read_sources()
    assert json.loads(occ.SOURCES_INDEX.read_text()) == before


def test_source_id_is_readable_and_stable(cache):
    assert occ.source_id_for({"ific_no": "3079", "kind": "srs"}) == "srs3079"
    assert occ.source_id_for({"ific_no": "3085", "kind": "ific"}) == "ific3085"
    # No IFIC number: fall back to the file name, sanitised.
    assert occ.source_id_for({"mdb": "/tmp/My SRS (copy).mdb"}) == "My-SRS-copy-"
    assert occ.source_id_for({}) == "source"


def test_describe_states_the_provenance(cache):
    src = occ._write_source(
        occ.CatalogSource(id="srs3079", label="Full SRS"),
        [_sys("1")], {"n_notice_total": 15909, "ific_no": "3079",
                      "fetched_at": "2026-09-10T12:59:29Z"})
    text = src.describe()
    assert "1 system(s)" in text and "15909 notices" in text
    assert "IFIC 3079" in text and "2026-09-10" in text


def test_page_lists_the_registered_catalogues(tmp_path, monkeypatch):
    """The panel in the free space of the Catalogs card (item 4)."""
    from streamlit.testing.v1 import AppTest  # noqa: PLC0415

    # The page imports these as `lib.…`; patch the same module objects it will
    # see, not a second copy under the `streamlit_app.` prefix.
    import lib.manual as manual  # noqa: PLC0415
    import lib.state as state  # noqa: PLC0415
    import lib.occupancy as page_occ  # noqa: PLC0415

    for attr in ("CACHE_DIR", "SOURCES_DIR", "SOURCES_INDEX", "SNS_CATALOG",
                 "SNS_META", "ANATEL_CATALOG", "ANATEL_META"):
        monkeypatch.setattr(page_occ, attr, {
            "CACHE_DIR": tmp_path,
            "SOURCES_DIR": tmp_path / "sources",
            "SOURCES_INDEX": tmp_path / "sources" / "index.json",
            "SNS_CATALOG": tmp_path / "sns_catalog.json",
            "SNS_META": tmp_path / "sns_meta.json",
            "ANATEL_CATALOG": tmp_path / "anatel_catalog.json",
            "ANATEL_META": tmp_path / "anatel_meta.json",
        }[attr])

    monkeypatch.setattr(manual, "help_expander", lambda *a, **k: None)
    monkeypatch.setattr(state, "DATA_ROOT", tmp_path)
    occ = page_occ
    occ._write_source(occ.CatalogSource(id="srs3079", label="Full SRS 3079"),
                     [_sys("111")], {"n_notice_total": 15909, "ific_no": "3079"})
    occ._write_source(occ.CatalogSource(id="ific3085", label="Weekly 3085",
                                      kind="ific"),
                     [_sys("222")], {"n_notice_total": 174, "ific_no": "3085"})
    (tmp_path / "state_br_occupancy.form.json").write_text(json.dumps({
        "sources": [], "orbit": "all", "direction": "downlink", "rf_bands": [],
        "country": "", "country_rule": "serves", "letter_bands": [],
        "freq_low_ghz": "", "freq_high_ghz": "", "chart_range": [],
        "system_ids": [], "query": "",
    }))

    page = str(REPO / "streamlit_app" / "pages" / "H_National_Occupancy.py")
    at = AppTest.from_file(page, default_timeout=300)
    at.run()
    assert not at.exception, [e.value for e in at.exception]

    md = "".join(str(m.value) for m in at.markdown)
    assert "Indexed filings" in md
    assert "Full SRS 3079" in md and "Weekly 3085" in md
    # Both are offered as separate sources to filter on.
    picker = [m for m in at.multiselect if m.label == "Source"]
    assert picker, [m.label for m in at.multiselect]
    labels = " ".join(picker[0].options)
    assert "Full SRS 3079" in labels and "Weekly 3085" in labels
    # And each one can be forgotten.
    assert [b for b in at.button if b.key == "br_rm_srs3079"]


def _page_app(tmp_path, monkeypatch, state_overrides=None):
    from streamlit.testing.v1 import AppTest  # noqa: PLC0415

    import lib.occupancy as page_occ  # noqa: PLC0415
    import lib.manual as manual  # noqa: PLC0415
    import lib.state as state  # noqa: PLC0415

    monkeypatch.setattr(manual, "help_expander", lambda *a, **k: None)
    monkeypatch.setattr(state, "DATA_ROOT", tmp_path)
    for attr, value in (
        ("CACHE_DIR", tmp_path), ("SOURCES_DIR", tmp_path / "sources"),
        ("SOURCES_INDEX", tmp_path / "sources" / "index.json"),
        ("SNS_CATALOG", tmp_path / "sns_catalog.json"),
        ("SNS_META", tmp_path / "sns_meta.json"),
        ("ANATEL_CATALOG", tmp_path / "anatel_catalog.json"),
        ("ANATEL_META", tmp_path / "anatel_meta.json"),
        ("SNS_DIR", tmp_path / "sns"),
    ):
        monkeypatch.setattr(page_occ, attr, value)
    (tmp_path / "state_br_occupancy.form.json").write_text(json.dumps({
        "sources": ["anatel"], "orbit": "all", "direction": "downlink",
        "rf_bands": [], "country": "", "country_rule": "serves",
        "letter_bands": [], "freq_low_ghz": "", "freq_high_ghz": "",
        "chart_range": [], "system_ids": [], "query": "",
        **(state_overrides or {}),
    }))
    at = AppTest.from_file(
        str(REPO / "streamlit_app" / "pages" / "H_National_Occupancy.py"),
        default_timeout=300)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at, page_occ


def test_add_another_catalogue_block_is_in_the_free_column(tmp_path, monkeypatch):
    """Item 4's affordance: bring in a second SRS beside the first."""
    at, _ = _page_app(tmp_path, monkeypatch)
    md = "".join(str(m.value) for m in at.markdown)
    assert "Add another filing catalogue" in md

    keys = {t.key for t in at.text_input if t.key}
    assert {"br_add_label", "br_add_path"} <= keys, keys
    go = [b for b in at.button if b.key == "br_add_go"]
    assert go, [b.key for b in at.button]
    # Nothing to index yet, so the action is refused rather than misfiring.
    assert go[0].disabled is True

    # And it says what it does to what is already there.
    caps = " ".join(str(c.value) for c in at.caption)
    assert "never over it" in caps, caps


def test_indexing_a_second_catalogue_keeps_the_first(tmp_path, monkeypatch):
    """The regression this whole slice exists for."""
    at, page_occ = _page_app(tmp_path, monkeypatch)
    page_occ._write_source(page_occ.CatalogSource(id="srs3079", label="First"),
                          [_sys("111")], {"ific_no": "3079"})
    page_occ._write_source(page_occ.CatalogSource(id="ific3085", label="Second",
                                                kind="ific"),
                          [_sys("222")], {"ific_no": "3085"})
    ids = [s.id for s in page_occ.read_sources()]
    assert ids == ["srs3079", "ific3085"], ids
    assert len(page_occ.load_filings()) == 2
    assert len(page_occ.load_filings(["srs3079"])) == 1


def test_catalogs_card_is_not_organised_around_one_country(tmp_path, monkeypatch):
    """Anatel is one national catalogue among others, not a section of its own."""
    at, _ = _page_app(tmp_path, monkeypatch)
    md = "".join(str(m.value) for m in at.markdown)

    # One card for licensed catalogues, whoever issues them.
    assert "National licensed catalogues" in md
    assert "**Anatel** · licensed stations" not in md

    # The three ways in are all offered.
    assert "Add a national licensed catalogue" in md   # a country's CSV
    assert "Add another filing catalogue" in md        # extra SRS / filings
    assert "Indexed filings" in md                     # what is loaded

    # And no headline still claims the page is about Brazil.
    assert "operate in Brazil" not in md
    assert "OPERATE IN BRAZIL" not in md.upper()


def test_provenance_caption_names_the_rule_not_the_country(tmp_path, monkeypatch):
    """A legacy index says so, instead of presenting Brazil as the subject."""
    at, page_occ = _page_app(tmp_path, monkeypatch)
    page_occ._write_json(page_occ.SNS_META, {"n_notice_total": 15909,
                                           "n_systems": 999, "n_srv_br": 907,
                                           "n_adm_b": 107, "ific_no": "3079"})
    at2, _ = _page_app(tmp_path, monkeypatch)
    caps = " ".join(str(c.value) for c in at2.caption)
    assert "Brazil-only rule" not in caps or "re-index" in caps, caps


def test_no_country_or_authority_in_the_interface_copy(tmp_path, monkeypatch):
    """It is a platform for any administration, so none is built in.

    A country appears only as DATA — an ITU symbol in a filter, the label a
    user typed. Never as a download button or a shipped catalogue.
    """
    at, page_occ = _page_app(tmp_path, monkeypatch)
    shown = " ".join(
        [str(m.value) for m in at.markdown]
        + [str(c.value) for c in at.caption]
        + [str(b.label) for b in at.button]
        + [str(t.label) for t in at.text_input]
        + [str(m.label) for m in at.multiselect]
        + [str(s.label) for s in at.selectbox]
    )
    for banned in ("Anatel", "Brazilian", "occupancy in Brazil", "Brazil catalogue"):
        assert banned not in shown, (banned, shown[:400])
    # "BR IFIC" is the ITU Radiocommunication Bureau's product, not Brazil.
    assert "Brazil" not in shown.replace("BR IFIC", ""), shown[:400]
    assert not hasattr(page_occ, "national_providers")
    assert not hasattr(page_occ, "refresh_anatel")


def test_no_built_in_national_catalogue(tmp_path, monkeypatch):
    """A licensed catalogue is uploaded. No administration is fetched for you."""
    _page_app(tmp_path, monkeypatch)
    page = (REPO / "streamlit_app" / "pages" / "H_National_Occupancy.py").read_text()
    assert "refresh_anatel" not in page
    assert "national_providers" not in page
    assert '"Anatel"' not in page and "'Anatel'" not in page
    assert "Add a national licensed catalogue" in page


def test_a_newly_added_catalogue_is_selected_and_reaches_the_chart(tmp_path, monkeypatch):
    """Indexed, listed and invisible was the failure: the saved selection
    predates the new catalogue, so it was never ticked and never drawn."""
    at, page_occ = _page_app(tmp_path, monkeypatch)
    page_occ._write_source(page_occ.CatalogSource(id="first", label="First"),
                          [_sys("1", "SAT1")], {})
    # A user who already knows "first" and picked only it.
    (tmp_path / "state_br_occupancy.form.json").write_text(json.dumps({
        "sources": ["first"], "known_sources": ["anatel", "first"],
        "orbit": "all", "direction": "downlink", "rf_bands": [], "country": "",
        "country_rule": "serves", "letter_bands": [], "freq_low_ghz": "",
        "freq_high_ghz": "", "chart_range": [], "system_ids": [], "query": "",
    }))
    page_occ._write_source(page_occ.CatalogSource(id="second", label="Second"),
                          [_sys("2", "SAT2")], {})

    from streamlit.testing.v1 import AppTest  # noqa: PLC0415

    at = AppTest.from_file(
        str(REPO / "streamlit_app" / "pages" / "H_National_Occupancy.py"),
        default_timeout=300)
    at.run()
    assert not at.exception, [e.value for e in at.exception]

    sel = [m for m in at.multiselect if m.label == "Source"][0]
    assert set(sel.value) == {"first", "second"}, sel.value
    assert any("Newly indexed" in str(c.value) for c in at.caption)
    counts = [str(c.value) for c in at.caption if "after filters" in str(c.value)]
    assert counts and counts[0].startswith("2 "), counts


def test_a_deliberately_unticked_source_stays_unticked(tmp_path, monkeypatch):
    """Auto-selection fires once, for genuinely new ids only."""
    at, page_occ = _page_app(tmp_path, monkeypatch)
    page_occ._write_source(page_occ.CatalogSource(id="a", label="A"), [_sys("1")], {})
    page_occ._write_source(page_occ.CatalogSource(id="b", label="B"), [_sys("2")], {})
    (tmp_path / "state_br_occupancy.form.json").write_text(json.dumps({
        "sources": ["a"], "known_sources": ["anatel", "a", "b"],
        "orbit": "all", "direction": "downlink", "rf_bands": [], "country": "",
        "country_rule": "serves", "letter_bands": [], "freq_low_ghz": "",
        "freq_high_ghz": "", "chart_range": [], "system_ids": [], "query": "",
    }))

    from streamlit.testing.v1 import AppTest  # noqa: PLC0415

    at = AppTest.from_file(
        str(REPO / "streamlit_app" / "pages" / "H_National_Occupancy.py"),
        default_timeout=300)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    sel = [m for m in at.multiselect if m.label == "Source"][0]
    assert sel.value == ["a"], sel.value


def test_unrelated_files_become_separate_catalogues():
    """Two filings dropped together must not be read as parts of one SRS.

    `locate_srs_parts` picks the first .mdb in a folder when no name says
    `partNofM`, so handing it two unrelated filings indexed one and dropped the
    other in silence.
    """
    groups = occ.group_upload_names(["USASAT-Ku_TTC.MDB", "324520180 SRS.MDB"])
    assert len(groups) == 2, groups
    assert all(len(g) == 1 for g in groups)


def test_split_parts_stay_one_catalogue():
    groups = occ.group_upload_names(
        ["srs3079_part1of4.mdb", "srs3079_part3of4.mdb"])
    assert groups == [["srs3079_part1of4.mdb", "srs3079_part3of4.mdb"]], groups

    mixed = occ.group_upload_names(
        ["a_part1of2.mdb", "a_part2of2.mdb", "b.mdb"])
    assert len(mixed) == 2
    assert ["b.mdb"] in mixed


def test_staging_puts_each_catalogue_in_its_own_directory(tmp_path):
    files = [("USASAT.MDB", b"one"), ("324520180 SRS.MDB", b"two")]
    jobs = occ.stage_upload_groups(files, tmp_path)
    assert len(jobs) == 2, jobs
    paths = [p for _hint, p in jobs]
    # A single file is handed over as the file itself...
    assert all(p.is_file() for p in paths), paths
    # ...in its own directory, so one ingest cannot see the other's bytes.
    assert len({p.parent for p in paths}) == 2
    assert {p.read_bytes() for p in paths} == {b"one", b"two"}


def test_staging_keeps_split_parts_together(tmp_path):
    files = [("srs1_part1of2.mdb", b"a"), ("srs1_part2of2.mdb", b"b"),
             ("other.mdb", b"c")]
    jobs = occ.stage_upload_groups(files, tmp_path)
    assert len(jobs) == 2, jobs
    by_hint = {h: p for h, p in jobs}
    split = [p for h, p in jobs if p.is_dir()]
    assert len(split) == 1, jobs
    assert {f.name for f in split[0].iterdir()} == {
        "srs1_part1of2.mdb", "srs1_part2of2.mdb"}
    # The split group's name hint loses the part suffix.
    assert any(h.lower().startswith("srs1") and "part" not in h.lower()
               for h in by_hint), list(by_hint)
