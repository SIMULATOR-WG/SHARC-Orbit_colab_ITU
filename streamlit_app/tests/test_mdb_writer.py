"""Tests for the real-MDB pair writer (Jackcess bridge) — manual filings."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src import mdb_writer  # noqa: E402

_ok, _why = mdb_writer.availability()
pytestmark = pytest.mark.skipif(not _ok, reason=f"mdb writer: {_why}")


def _manual():
    from src import constellation_templates as ct
    return {
        "label": "MDB PAIR TEST",
        "non_gso": {
            "planes": ct.walker_delta(total_sats=8, num_planes=4,
                                      inclination_deg=53.0, altitude_km=550.0),
            "alpha0_deg": 6.0, "min_elevation_deg": 5.0,
        },
    }


def _mask_xml() -> bytes:
    import numpy as np
    from src.mask_generator import (BeamSpec, MaskGenParams,
                                    generate_pfd_mask_azel, write_pfd_mask_xml)
    p = MaskGenParams(altitude_km=550.0,
                      beams=[BeamSpec(10.0, 30.0, 5.0)],
                      low_freq_mhz=17700.0, high_freq_mhz=18600.0, mask_id=1)
    m = generate_pfd_mask_azel(p, lat_grid_deg=[-10.0, 0.0, 10.0],
                               az_grid_deg=np.arange(-10.0, 10.1, 5.0),
                               el_grid_deg=np.arange(-10.0, 10.1, 5.0))
    return write_pfd_mask_xml(m, p).encode("utf-8")


def test_pair_roundtrip_through_app_stack(tmp_path):
    from src.constants import RE_KM
    from src.main import create_constellation_from_config
    from src.srs_reader import (read_mask_info, read_pfd_mask_xml_from_mdb,
                                read_srs_mdb, srs_to_constellation_config)

    xml = _mask_xml()
    srs, mask = mdb_writer.write_manual_pair(
        _manual(), tmp_path, base_name="Pair", mask_xml=xml,
        ntc_id="900123", mask_id=1,
        mask_freq_min_ghz=17.7, mask_freq_max_ghz=18.6,
    )
    assert srs.name == "Pair_SRS.mdb" and mask.name == "Pair_Mask.mdb"

    sysm = read_srs_mdb(str(srs))
    assert sysm.sat_name == "MDB PAIR TEST"
    assert sysm.ntc_id == "900123"
    assert len(sysm.orbit_planes) == 4 and sysm.nbr_sat_total == 8
    assert sysm.x_zone_deg == 6.0
    # per-satellite phases survive (F=1 Walker: plane 2 sat 1 at 45 deg)
    assert sysm.phase_by_orbit[2][1] == pytest.approx(45.0)

    cfg = srs_to_constellation_config(sysm)
    # fractional-minute period -> exact semi-major axis round-trip
    assert cfg["semi_major_axis_km"] == pytest.approx(RE_KM + 550.0, abs=0.01)
    assert cfg.get("gmst0_deg") == pytest.approx(0.0)
    cons = create_constellation_from_config({**cfg, "_planes": cfg.get("planes", [])})
    assert len(cons) == 8

    mi = read_mask_info(str(srs), ntc_id="900123")
    assert [(m.mask_id, m.f_mask) for m in mi] == [(1, "P")]

    xml_back = read_pfd_mask_xml_from_mdb(str(mask), 1, ntc_id="900123")
    assert xml_back.strip() == xml.decode("utf-8").strip()
