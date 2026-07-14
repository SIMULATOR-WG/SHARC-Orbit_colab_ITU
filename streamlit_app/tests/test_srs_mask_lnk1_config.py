"""SRS mask_lnk1 integration tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.main import _resolve_mask_lnk1_assignments, load_from_srs  # type: ignore[import]


_REPO = Path(__file__).resolve().parents[2]
_MCSAT_SRS = _REPO / "docs/test_data/MCSAT_LEO_Ka_SRS.mdb"


@pytest.mark.skipif(not _MCSAT_SRS.exists(), reason="MCSAT SRS MDB not present")
def test_load_from_srs_keeps_source_mdb_for_mask_lnk1() -> None:
    """Config returned from load_from_srs must be enough to resolve mask_lnk1."""
    cfg = load_from_srs(
        str(_MCSAT_SRS),
        pfd_mask_mdb=str(_MCSAT_SRS),
        mask_id=3,
        ntc_id="101",
        service="FSS",
    )

    assert cfg["_srs_mdb_path"] == str(_MCSAT_SRS.resolve())
    assert cfg["pfd_mask"]["srs_mdb"] == str(_MCSAT_SRS.resolve())

    by_orbit, by_sat = _resolve_mask_lnk1_assignments(cfg, cfg["pfd_mask"])

    assert by_orbit == {orb_id: 3 for orb_id in range(1, 19)}
    assert by_sat == {(orb_id, None): [3] for orb_id in range(1, 19)}
