"""Tests for mutually-exclusive configuration detection (R2, AP4 A.4.b.3.b-d)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.srs_reader import SRSNonGeoSystem, SRSOrbitPlane, detect_orbit_config  # noqa: E402


def _plane(orbit_set_id: int = 0, orb_id: int = 1) -> SRSOrbitPlane:
    return SRSOrbitPlane(
        orb_id=orb_id, nbr_sat_pl=10, right_asc_deg=0.0, inclin_deg=53.0,
        period_s=6000.0, apogee_km=550.0, perigee_km=550.0,
        perigee_arg_deg=0.0, op_height_km=550.0, f_stn_keep=False,
        rpt_period_s=0.0, f_precess=False, precession_deg_day=0.0,
        long_asc_deg=0.0, keep_range_deg=0.0, f_sun_synch=False,
        orbit_set_id=orbit_set_id,
    )


def _system(mct: str = "M", nbr: int = 2, planes=None) -> SRSNonGeoSystem:
    return SRSNonGeoSystem(
        ntc_id="123", sat_name="T", ref_body="T", nbr_planes=1,
        nbr_sat_total=10, density=0.0, avg_dist_km=0.0, f_x_zone=False,
        x_zone_deg=0.0, f_constellation=True,
        multi_config_type=mct, nbr_config=nbr,
        orbit_planes=planes if planes is not None else [_plane()],
    )


def test_orbit_set_id_wins():
    sysm = _system(planes=[_plane(orbit_set_id=3), _plane(orbit_set_id=3, orb_id=2)])
    out = detect_orbit_config("/data/Config1/whatever_SRS.mdb", sysm)
    assert out == {"config_label": 3, "source": "orbit_set_id",
                   "is_multi": True, "nbr_config": 2}


def test_folder_fallback():
    sysm = _system()  # planes without orbit_set_id
    out = detect_orbit_config("/data/ntc/Config2/x_SRS.mdb", sysm)
    assert (out["config_label"], out["source"]) == (2, "folder")


def test_filename_fallback():
    sysm = _system()
    out = detect_orbit_config("/data/ntc/x_Config4_SRS.mdb", sysm)
    assert (out["config_label"], out["source"]) == (4, "filename")


def test_single_config_filing():
    sysm = _system(mct="S", nbr=0)
    out = detect_orbit_config("/data/plain_SRS.mdb", sysm)
    assert out["is_multi"] is False
    assert out["config_label"] is None


def test_conflicting_set_ids_yield_none():
    sysm = _system(planes=[_plane(orbit_set_id=1), _plane(orbit_set_id=2, orb_id=2)])
    out = detect_orbit_config("/data/plain_SRS.mdb", sysm)
    assert out["config_label"] is None


def test_legacy_defaults():
    # Legacy schema: fields absent → dataclass defaults must hold.
    sysm = _system(mct="", nbr=0)
    assert sysm.multi_config_type == ""
    assert sysm.orbit_planes[0].orbit_set_id == 0
