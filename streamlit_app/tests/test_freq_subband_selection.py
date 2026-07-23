"""Frequency-aware element selection (sub-band operation).

Covers the chain that guarantees only elements operating at the selected
simulation frequency are used:

  1. ``read_emitters_in_band`` exposes ``active_grp_ids`` (groups covering f);
  2. ``read_mask_assignment_all`` / ``read_mask_assignment_per_sat`` accept
     ``restrict_grp_ids`` (mask_lnk1 rows limited to those groups);
  3. ``load_from_srs(simulation_frequency_ghz=...)`` pins the frequency and
     resolves the default mask over the covering groups;
  4. ``create_constellation_for_config`` aborts (strict gate) when the notice
     declares grp bands and none covers the pinned frequency;
  5. ``system_tx_subbands`` (UI helper) lists the Tx sub-bands per system.

MDB-backed tests run against docs/test_data/MCSAT_LEO_Ka_SRS.mdb (grp 301,
emi_rcp='E', 17.8–19.3 GHz) and are skipped when it is absent.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.main import (  # noqa: E402
    _resolve_mask_lnk1_assignments,
    create_constellation_for_config,
    load_from_srs,
)
from src.srs_reader import (  # noqa: E402
    read_emitters_in_band,
    read_mask_assignment_all,
    read_mask_assignment_per_sat,
)

_MCSAT_SRS = REPO / "docs/test_data/MCSAT_LEO_Ka_SRS.mdb"
_MCSAT_MASKS = REPO / "docs/test_data/MCSAT_LEO_Ka_Masks.mdb"

_needs_mcsat = pytest.mark.skipif(
    not _MCSAT_SRS.exists(), reason="MCSAT SRS MDB not present"
)


# ── 1. active_grp_ids ────────────────────────────────────────────────────────

@_needs_mcsat
def test_active_grp_ids_in_band() -> None:
    sel = read_emitters_in_band(
        str(_MCSAT_SRS), ntc_id="101", freq_ghz=18.05, emi_rcp="E")
    assert sel.has_data is True
    assert sel.active_grp_ids == frozenset({301})
    assert sel.any_active is True


@_needs_mcsat
def test_active_grp_ids_out_of_band() -> None:
    sel = read_emitters_in_band(
        str(_MCSAT_SRS), ntc_id="101", freq_ghz=12.0, emi_rcp="E")
    assert sel.has_data is True
    assert sel.active_grp_ids == frozenset()
    assert sel.any_active is False


# ── 2. restrict_grp_ids on the mask_lnk1 resolvers ──────────────────────────

@_needs_mcsat
def test_assignment_restricted_to_covering_group() -> None:
    unrestricted = read_mask_assignment_all(
        str(_MCSAT_SRS), ntc_id="101", f_mask_filter="P")
    restricted = read_mask_assignment_all(
        str(_MCSAT_SRS), ntc_id="101", f_mask_filter="P",
        restrict_grp_ids=frozenset({301}))
    assert restricted == unrestricted  # grp 301 is the only Tx group
    assert read_mask_assignment_all(
        str(_MCSAT_SRS), ntc_id="101", f_mask_filter="P",
        restrict_grp_ids=frozenset({999})) == {}


@_needs_mcsat
def test_assignment_per_sat_restricted() -> None:
    assert read_mask_assignment_per_sat(
        str(_MCSAT_SRS), ntc_id="101", f_mask_filter="P",
        restrict_grp_ids=frozenset({999})) == {}


# ── 3. load_from_srs pins + resolves at the selected frequency ──────────────

@_needs_mcsat
def test_load_from_srs_pins_simulation_frequency() -> None:
    cfg = load_from_srs(
        str(_MCSAT_SRS), pfd_mask_mdb=str(_MCSAT_SRS), ntc_id="101",
        service="FSS", simulation_frequency_ghz=18.05,
    )
    assert cfg["pfd_mask"]["simulation_frequency_ghz"] == pytest.approx(18.05)
    # apply_article22 resolves the run at the pinned frequency (inside band).
    assert cfg["non_gso"]["frequency_ghz"] == pytest.approx(18.05)
    # A PFD mask was resolved via the covering group's mask_lnk1 rows.
    assert cfg["pfd_mask"]["mask_id"] is not None


@_needs_mcsat
def test_mask_lnk1_freq_fallback_when_no_group_covers() -> None:
    """Frequency outside every grp band → unrestricted mapping (no data loss)."""
    cfg = load_from_srs(
        str(_MCSAT_SRS), pfd_mask_mdb=str(_MCSAT_SRS), ntc_id="101",
        service="FSS",
    )
    by_orbit_ref, by_sat_ref = _resolve_mask_lnk1_assignments(
        cfg, cfg["pfd_mask"])
    by_orbit, by_sat = _resolve_mask_lnk1_assignments(
        cfg, cfg["pfd_mask"], freq_ghz=12.0)
    assert by_orbit == by_orbit_ref
    assert by_sat == by_sat_ref


# ── 4. strict gate: no emitter at the pinned frequency ──────────────────────

@_needs_mcsat
def test_strict_gate_raises_outside_operating_subband() -> None:
    cfg = load_from_srs(
        str(_MCSAT_SRS), pfd_mask_mdb=str(_MCSAT_SRS), ntc_id="101",
        service="FSS",
    )
    cfg["simulation"]["restrict_emitters_to_sim_band"] = True
    # Pin a frequency no transmitting group covers (grp 301 = 17.8–19.3 GHz).
    cfg["pfd_mask"]["simulation_frequency_ghz"] = 12.0
    with pytest.raises(ValueError, match="No transmitting group"):
        create_constellation_for_config(cfg)


@_needs_mcsat
def test_strict_gate_passes_inside_operating_subband() -> None:
    cfg = load_from_srs(
        str(_MCSAT_SRS), pfd_mask_mdb=str(_MCSAT_SRS), ntc_id="101",
        service="FSS", simulation_frequency_ghz=18.05,
    )
    cfg["simulation"]["restrict_emitters_to_sim_band"] = True
    constellation, mask_ids = create_constellation_for_config(cfg)
    assert len(constellation) > 0
    assert len(mask_ids) == len(constellation)


@_needs_mcsat
def test_gate_disabled_keeps_full_constellation() -> None:
    cfg = load_from_srs(
        str(_MCSAT_SRS), pfd_mask_mdb=str(_MCSAT_SRS), ntc_id="101",
        service="FSS",
    )
    cfg["simulation"]["restrict_emitters_to_sim_band"] = False
    cfg["pfd_mask"]["simulation_frequency_ghz"] = 12.0  # would trip the gate
    constellation, _ = create_constellation_for_config(cfg)
    assert len(constellation) > 0  # legacy escape hatch: no filtering at all


# ── 4b. schema-mismatch / missing-mapping semantics (synthetic) ─────────────

def _fake_mdb(tmp_path):
    p = tmp_path / "fake.mdb"
    p.write_bytes(b"\x00")
    return str(p)


def _patch_tables(monkeypatch, grp_rows, lnk_rows):
    import src.srs_reader as sr

    def _fake_export(_path, table):
        return {"grp": grp_rows, "mask_lnk1": lnk_rows}[table]

    monkeypatch.setattr(sr, "_run_mdb_export", _fake_export)


_GRP_COVERS = [{"ntc_id": "9", "grp_id": "10", "emi_rcp": "E",
                "freq_min": "17800.0", "freq_max": "18600.0"}]


def test_v105_schema_lnk_without_grp_id_is_wildcard(tmp_path, monkeypatch):
    """SNS v10.5 keys mask_lnk1 by scen_id — groups cover f ⇒ keep all sats."""
    _patch_tables(monkeypatch, _GRP_COVERS, [
        {"ntc_id": "9", "scen_id": "1", "orb_id": "1", "sat_orb_id": "",
         "mask_id": "3", "seq_no": "1"},  # no grp_id column
    ])
    sel = read_emitters_in_band(_fake_mdb(tmp_path), ntc_id="9", freq_ghz=18.0)
    assert sel.has_data is True
    assert sel.wildcard_all is True          # inert-keep, NOT "nothing emits"
    assert sel.is_active(5, 2) is True


def test_no_lnk_rows_for_notice_is_wildcard(tmp_path, monkeypatch):
    """Groups cover f but the notice has no mask_lnk1 rows ⇒ keep all sats."""
    _patch_tables(monkeypatch, _GRP_COVERS, [
        {"ntc_id": "OTHER", "grp_id": "77", "orb_id": "1", "sat_orb_id": "",
         "mask_id": "1", "seq_no": "1"},
    ])
    sel = read_emitters_in_band(_fake_mdb(tmp_path), ntc_id="9", freq_ghz=18.0)
    assert sel.has_data is True
    assert sel.wildcard_all is True


def test_notice_without_tx_groups_is_inert(tmp_path, monkeypatch):
    """Notice declares no Tx (E) group band at all ⇒ filter has no data."""
    _patch_tables(monkeypatch, [
        {"ntc_id": "9", "grp_id": "10", "emi_rcp": "R",
         "freq_min": "27500.0", "freq_max": "30000.0"},
    ], [
        {"ntc_id": "9", "grp_id": "10", "orb_id": "1", "sat_orb_id": "",
         "mask_id": "1", "seq_no": "1"},
    ])
    sel = read_emitters_in_band(_fake_mdb(tmp_path), ntc_id="9", freq_ghz=18.0)
    assert sel.has_data is False             # inert — everything simulates


def test_empty_lnk_table_covering_freq_is_wildcard(tmp_path, monkeypatch):
    """Old-era dbs (2018–2019) ship an EMPTY mask_lnk1 ⇒ keep all sats."""
    _patch_tables(monkeypatch, _GRP_COVERS, [])
    sel = read_emitters_in_band(_fake_mdb(tmp_path), ntc_id="9", freq_ghz=18.0)
    assert sel.has_data is True
    assert sel.wildcard_all is True


def test_empty_lnk_table_out_of_band_stays_abortable(tmp_path, monkeypatch):
    """grp is the band authority even when mask_lnk1 is empty."""
    _patch_tables(monkeypatch, _GRP_COVERS, [])
    sel = read_emitters_in_band(_fake_mdb(tmp_path), ntc_id="9", freq_ghz=12.0)
    assert sel.has_data is True
    assert sel.any_active is False


def test_tx_groups_declared_but_none_covers_stays_abortable(tmp_path, monkeypatch):
    """Tx bands declared, none covers f, mapping present ⇒ nothing active."""
    _patch_tables(monkeypatch, _GRP_COVERS, [
        {"ntc_id": "9", "grp_id": "10", "orb_id": "1", "sat_orb_id": "",
         "mask_id": "1", "seq_no": "1"},
    ])
    sel = read_emitters_in_band(_fake_mdb(tmp_path), ntc_id="9", freq_ghz=12.0)
    assert sel.has_data is True
    assert sel.any_active is False           # strict gate may abort


# ── 5. UI helper: Tx sub-bands per system ────────────────────────────────────

@_needs_mcsat
def test_system_tx_subbands_lists_grp_bands() -> None:
    from streamlit_app.lib.art22_ui import system_tx_subbands
    bands = system_tx_subbands(str(_MCSAT_SRS), "101")
    assert len(bands) == 1
    b = bands[0]
    assert b["freq_min"] == pytest.approx(17.8, abs=1e-6)
    assert b["freq_max"] == pytest.approx(19.3, abs=1e-6)
    assert b["grp_ids"] == [301]
