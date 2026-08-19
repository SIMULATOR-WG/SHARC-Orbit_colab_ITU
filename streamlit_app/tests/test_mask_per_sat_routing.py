"""Per-satellite PFD-mask routing in the fixed-geometry aggregate paths.

method_1's engine assembly (``build_downlink_engine_inputs``) routes each
satellite through its OWN mask_lnk1 mask (``PFDMaskMulti``) whenever the
filing declares more than one PFD mask id — e.g. one mask per orbital shell.
The fixed-geometry paths (method_2/3/4/5) used to discard the per-sat ids
returned by ``create_constellation_for_config`` and radiate the whole
constellation with the single ``cfg["pfd_mask"]["mask_id"]`` — one shell's
mask applied to every satellite, which skews multi-shell filings by whole dB
versus method_1 at the same geometry.

These tests pin the contract of ``_build_mask_for_sats``, the shared fix:
  * single-mask filings (XML source, or MDB with ≤1 distinct id) fall back to
    ``_build_mask`` — bit-identical to the previous behaviour;
  * multi-mask MDB filings return a ``PFDMaskMulti`` that routes each
    satellite to its own mask, with the ``-1`` sentinel resolving to the
    filing's primary (most frequent) mask.
"""
from __future__ import annotations

import pytest

from src.pfd_mask import PFDMaskMulti, PFDMaskXML  # type: ignore[import]
from streamlit_app.lib.job_runners import s1588_worker as worker  # type: ignore[import]


def _alpha_mask(mask_id: int, base_pfd: float) -> PFDMaskXML:
    lats, alphas, dlons = [-60.0, 0.0, 60.0], [0.0, 2.0, 5.0, 10.0], [-5.0, 0.0, 5.0]
    x = [f'<satellite_system><pfd_mask mask_id="{mask_id}" '
         'type="alpha_deltaLongitude" refbw_khz="40" a_name="latitude" '
         'b_name="alpha" c_name="deltaLongitude">']
    for la in lats:
        x.append(f'<by_a a="{la}">')
        for a in alphas:
            x.append(f'<by_b b="{a}">')
            for d in dlons:
                x.append(f'<pfd c="{d}">{base_pfd - a - abs(d):.3f}</pfd>')
            x.append("</by_b>")
        x.append("</by_a>")
    x.append("</pfd_mask></satellite_system>")
    return PFDMaskXML.from_xml_content("\n".join(x), mask_id=mask_id)


def _cfg(source: str = "mask_mdb") -> dict:
    return {
        "pfd_mask": {
            "source": source,
            "mdb_file": "/nonexistent/mask.mdb",
            "file": "/nonexistent/mask.xml",
            "mask_id": 10,
        },
    }


@pytest.fixture()
def single_mask_sentinel(monkeypatch):
    """_build_mask replaced by a sentinel so the fallback path is observable."""
    sentinel = object()
    monkeypatch.setattr(worker, "_build_mask", lambda cfg: sentinel)
    return sentinel


def test_xml_source_falls_back_to_single_mask(single_mask_sentinel):
    # XML filings carry exactly one mask — per-sat ids must not change that.
    mask = worker._build_mask_for_sats(_cfg(source="xml_file"), [10, 20, 30])
    assert mask is single_mask_sentinel


def test_single_distinct_id_falls_back_to_single_mask(single_mask_sentinel):
    mask = worker._build_mask_for_sats(_cfg(), [10, 10, -1, 10])
    assert mask is single_mask_sentinel


def test_empty_or_absent_ids_fall_back_to_single_mask(single_mask_sentinel):
    assert worker._build_mask_for_sats(_cfg(), []) is single_mask_sentinel
    assert worker._build_mask_for_sats(_cfg(), None) is single_mask_sentinel
    assert worker._build_mask_for_sats(_cfg(), [-1, -1]) is single_mask_sentinel


def test_multi_mask_routes_each_satellite_to_its_own_mask(monkeypatch):
    m10 = _alpha_mask(10, -150.0)
    m20 = _alpha_mask(20, -140.0)

    class _SrsSystem:
        ntc_id = "123456789"

    def _fake_loader(mdb_path, mask_ids, *, ntc_id=None):
        assert mdb_path == "/nonexistent/mask.mdb"
        assert sorted(mask_ids) == [10, 20]
        # The MASK MDB may hold several notices — the filing's own ntc_id must
        # reach the loader (same as method_1's engine assembly).
        assert ntc_id == "123456789"
        return {10: m10, 20: m20}

    import src.srs_reader as srs_reader
    monkeypatch.setattr(srs_reader, "load_pfd_masks_for_ids", _fake_loader)

    cfg = _cfg()
    cfg["_srs_system"] = _SrsSystem()
    ids = [10, 20, 10, -1]
    mask = worker._build_mask_for_sats(cfg, ids)

    assert isinstance(mask, PFDMaskMulti)
    assert mask.mask_for_sat(0) is m10
    assert mask.mask_for_sat(1) is m20
    assert mask.mask_for_sat(2) is m10
    # -1 sentinel → primary mask (most frequent id: 10).
    assert mask.mask_for_sat(3) is m10
    assert mask.primary_mask_id == 10
    # Engine-facing metadata comes from the primary mask.
    assert mask.refbw_khz == m10.refbw_khz
