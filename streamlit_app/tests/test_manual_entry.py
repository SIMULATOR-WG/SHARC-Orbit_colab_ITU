"""Tests for manual/parametric constellation entry (R3/R4, plan WS2)."""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.constants import RE_KM  # noqa: E402
from src.main import create_constellation_from_config, _apsides_to_a_e  # noqa: E402


def test_apsides_to_a_e_matches_d637():
    # S.1503-4 §D6.3.7: a = Re + (ha+hp)/2 ; e = (ha−hp)/2a.
    a, e = _apsides_to_a_e(1400.0, 1000.0)
    assert a == pytest.approx(RE_KM + 1200.0)
    assert e == pytest.approx(400.0 / (2.0 * a))
    a2, e2 = _apsides_to_a_e(1200.0, 1200.0)
    assert e2 == 0.0


def test_top_level_apogee_perigee_accepted():
    cfg = {
        "apogee_km": 1200.0, "perigee_km": 1200.0,
        "semi_major_axis_km": None, "eccentricity": None,
        "inclination_deg": 87.9, "num_planes": 2, "sats_per_plane": 3,
    }
    cons = create_constellation_from_config(cfg)
    assert len(cons) == 6
    assert cons[0].a == pytest.approx(RE_KM + 1200.0)
    assert cons[0].e == 0.0


def test_public_planes_alias_with_per_plane_elements():
    cfg = {
        "semi_major_axis_km": 7578.0, "eccentricity": 0.0,
        "inclination_deg": 87.9, "num_planes": 2, "sats_per_plane": 2,
        "planes": [
            {"orb_id": 1, "sats_per_plane": 2, "raan_deg": 10.0,
             "perigee_arg_deg": 30.0, "phase_angles_deg": [5.0, 185.0]},
            {"orb_id": 2, "sats_per_plane": 1, "raan_deg": 200.0,
             "apogee_km": 1400.0, "perigee_km": 1000.0,
             "semi_major_axis_km": None},
        ],
    }
    cons = create_constellation_from_config(cfg)
    assert len(cons) == 3
    # plane 1: RAAN/ω/phase consumed (not fixed at 0)
    assert math.degrees(cons[0].raan) % 360.0 == pytest.approx(10.0)
    assert math.degrees(cons[0].omega) % 360.0 == pytest.approx(30.0)
    assert math.degrees(cons[0].M) % 360.0 == pytest.approx(5.0)
    assert math.degrees(cons[1].M) % 360.0 == pytest.approx(185.0)
    # plane 2: per-plane apsides converted
    a2, e2 = _apsides_to_a_e(1400.0, 1000.0)
    assert cons[2].a == pytest.approx(a2)
    assert cons[2].e == pytest.approx(e2)


def test_walker_receives_raan0_and_omega():
    cfg = {
        "semi_major_axis_km": 7578.0, "eccentricity": 0.0,
        "inclination_deg": 53.0, "num_planes": 4, "sats_per_plane": 2,
        "raan0_deg": 45.0, "arg_perigee_deg": 90.0,
    }
    cons = create_constellation_from_config(cfg)
    raans = sorted({round(math.degrees(o.raan) % 360.0, 6) for o in cons})
    assert raans == [45.0, 135.0, 225.0, 315.0]
    assert math.degrees(cons[0].omega) % 360.0 == pytest.approx(90.0)


def test_manual_srs_yaml_reader(tmp_path):
    # A registered manual pair: the YAML SRS side must round-trip through the
    # standard reader into an SRSNonGeoSystem usable by every consumer.
    import yaml
    from src.srs_reader import read_srs_mdb, srs_to_constellation_config
    from src import constellation_templates as ct

    doc = {
        "label": "PAIR-TEST",
        "non_gso": {
            "planes": ct.walker_delta(total_sats=8, num_planes=4,
                                      inclination_deg=53.0, altitude_km=550.0),
            "alpha0_deg": 6.0, "min_elevation_deg": 5.0,
        },
    }
    p = tmp_path / "pair_SRS.yaml"
    p.write_text(yaml.safe_dump(doc, sort_keys=False))
    sysm = read_srs_mdb(str(p))
    assert sysm.sat_name == "PAIR-TEST"
    assert len(sysm.orbit_planes) == 4 and sysm.nbr_sat_total == 8
    assert sysm.x_zone_deg == 6.0
    # phases preserved per orbit
    assert sysm.phase_by_orbit[2][1] == pytest.approx(45.0)
    cfg = srs_to_constellation_config(sysm)
    cons = create_constellation_from_config({**cfg, "_planes": cfg.get("planes", [])})
    assert len(cons) == 8
    assert cfg.get("gmst0_deg") == pytest.approx(0.0)


def test_manual_srs_yaml_walker_shortcut(tmp_path):
    import yaml
    from src.srs_reader import read_srs_mdb
    doc = {"label": "SC", "non_gso": {
        "num_planes": 3, "sats_per_plane": 2, "inclination_deg": 87.9,
        "apogee_km": 1200.0, "perigee_km": 1200.0}}
    p = tmp_path / "sc_SRS.yaml"
    p.write_text(yaml.safe_dump(doc))
    sysm = read_srs_mdb(str(p))
    assert len(sysm.orbit_planes) == 3
    assert sysm.orbit_planes[1].right_asc_deg == pytest.approx(120.0)
