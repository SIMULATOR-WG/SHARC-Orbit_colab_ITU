"""PFD-mask XML is parsed incrementally, not as one in-memory tree.

A real USASAT-NGSO-3series mask is ~138 MB of XML holding a 179x147x147 grid,
i.e. ~3.9 million ``<pfd>`` elements. Materialising the whole ElementTree
peaked at ~3 GB of RSS to keep a 31 MB float grid, and because glibc does not
return the freed arenas, loading the dozen masks that ``mask_lnk1``
per-satellite routing legitimately needs ratcheted RSS until the process died —
observed as a SIGSEGV inside the garbage collector (``visit_decref``), not a
clean MemoryError.

``PFDMaskXML`` now consumes and clears each ``by_a`` block as the parser closes
it. These tests pin the parts of that contract a unit test can hold:

  * the streamed result equals what the tree-based extraction produces, for the
    3D grid and every metadata field;
  * mask selection by ``mask_id`` still works when several masks share one XML,
    including masks that appear *after* the wanted one;
  * an absent ``mask_id`` still raises;
  * chunk boundaries are irrelevant — a feed size that splits tags mid-token
    parses identically.
"""
from __future__ import annotations

import numpy as np
import pytest

from src.pfd_mask import PFDMaskXML  # type: ignore[import]


def _mask_xml(mask_ids: tuple[int, ...]) -> str:
    """One ``satellite_system`` carrying several 3D masks with distinct data."""
    lats = [-60.0, 0.0, 60.0]
    alphas = [0.0, 2.0, 5.0, 10.0]
    dlons = [-5.0, 0.0, 5.0]
    out = ['<satellite_system ntc_id="123456789" sat_name="TESTSAT">']
    for mid in mask_ids:
        out.append(
            f'<pfd_mask mask_id="{mid}" type="alpha_deltaLongitude" '
            f'refbw_khz="{40.0 + mid}" low_freq_mhz="{10700 + mid}" '
            f'high_freq_mhz="{12750 + mid}" a_name="latitude" b_name="alpha" '
            'c_name="deltaLongitude">'
        )
        for la in lats:
            out.append(f'<by_a a="{la}">')
            for a in alphas:
                out.append(f'<by_b b="{a}">')
                for d in dlons:
                    out.append(f'<pfd c="{d}">{-150.0 - mid - a - abs(d):.3f}</pfd>')
                out.append("</by_b>")
            out.append("</by_a>")
        out.append("</pfd_mask>")
    out.append("</satellite_system>")
    return "\n".join(out)


def _tree_reference(xml: str, mask_id: int) -> PFDMaskXML:
    """Build the mask through the tree-based path (``_load_from_root``)."""
    import xml.etree.ElementTree as ET

    self = PFDMaskXML.__new__(PFDMaskXML)
    from src.pfd_mask import PFDMask  # type: ignore[import]
    PFDMask.__init__(self)
    self._dim = 3
    self._lat_vals = np.array([])
    self._alpha_vals = np.array([])
    self._dlon_vals = np.array([])
    self._pfd_grid = np.array([])
    self._wcg_query_mirror_axis = None
    self._wcg_query_mirror_sign = 1.0
    self._wcg_theta_symmetry_cache = None
    self._load_from_root(ET.fromstring(xml), mask_id)
    return self


META = ("mask_id", "mask_type", "refbw_khz", "low_freq_mhz", "high_freq_mhz",
        "ntc_id", "sat_name", "a_name", "b_name", "c_name")


def _assert_same(streamed: PFDMaskXML, ref: PFDMaskXML) -> None:
    for attr in META:
        assert getattr(streamed, attr) == getattr(ref, attr), attr
    assert np.array_equal(streamed._lat_vals, ref._lat_vals)
    assert np.array_equal(streamed._alpha_vals, ref._alpha_vals)
    assert np.array_equal(streamed._dlon_vals, ref._dlon_vals)
    assert np.array_equal(streamed._pfd_grid, ref._pfd_grid, equal_nan=True)


def test_streamed_mask_equals_the_tree_based_extraction():
    xml = _mask_xml((7,))
    _assert_same(PFDMaskXML.from_xml_content(xml, mask_id=7), _tree_reference(xml, 7))


@pytest.mark.parametrize("wanted", [11, 22, 33])
def test_mask_selection_among_several_in_one_xml(wanted):
    """The wanted mask may be first, middle or last — blocks of the others are
    skipped and released without disturbing the selection."""
    xml = _mask_xml((11, 22, 33))
    streamed = PFDMaskXML.from_xml_content(xml, mask_id=wanted)
    assert streamed.mask_id == wanted
    assert streamed.refbw_khz == 40.0 + wanted  # data really came from that mask
    _assert_same(streamed, _tree_reference(xml, wanted))


def test_missing_mask_id_raises():
    xml = _mask_xml((11, 22))
    with pytest.raises(ValueError, match="mask_id=99"):
        PFDMaskXML.from_xml_content(xml, mask_id=99)


def test_chunk_boundaries_do_not_change_the_result(monkeypatch):
    """A tiny feed size splits tags mid-token; the incremental parser must
    reassemble them exactly."""
    xml = _mask_xml((5,))
    ref = _tree_reference(xml, 5)
    for chunk in (7, 64, 1024):
        monkeypatch.setattr(PFDMaskXML, "_STREAM_CHUNK", chunk)
        _assert_same(PFDMaskXML.from_xml_content(xml, mask_id=5), ref)


def test_bytes_content_with_bom_is_accepted():
    xml = _mask_xml((5,))
    streamed = PFDMaskXML.from_xml_content(
        ("﻿" + xml).encode("utf-8"), mask_id=5,
    )
    _assert_same(streamed, _tree_reference(xml, 5))
