"""Tests for the constellation wizard templates (WS2 item 4)."""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.constants import RE_KM, MU_KM3_S2  # noqa: E402
from src import constellation_templates as ct  # noqa: E402
from src.main import create_constellation_from_config  # noqa: E402


def _spawn(planes):
    """Templates must be directly consumable by the engine."""
    cfg = {
        "planes": planes,
        "semi_major_axis_km": 7000.0, "eccentricity": 0.0,
        "inclination_deg": 0.0, "num_planes": len(planes), "sats_per_plane": 1,
    }
    return create_constellation_from_config(cfg)


def test_walker_delta_geometry():
    pls = ct.walker_delta(total_sats=8, num_planes=4, phasing_factor=1,
                          inclination_deg=53.0, altitude_km=550.0)
    assert [p["raan_deg"] for p in pls] == [0.0, 90.0, 180.0, 270.0]
    # inter-plane phasing F*360/T = 45°
    assert pls[1]["phase_angles_deg"][0] == pytest.approx(45.0)
    cons = _spawn(pls)
    assert len(cons) == 8
    assert cons[0].a == pytest.approx(RE_KM + 550.0)


def test_walker_star_half_circle():
    pls = ct.walker_star(total_sats=36, num_planes=18, phasing_factor=1,
                         altitude_km=1200.0)
    raans = [p["raan_deg"] for p in pls]
    assert max(raans) < 180.0  # OneWeb-like: nodes over half the circle
    assert pls[0]["inclination_deg"] == pytest.approx(87.9)


def test_equatorial_ring_o3b_like():
    pls = ct.equatorial_ring(n_sats=8, altitude_km=8062.0)
    assert len(pls) == 1
    assert pls[0]["inclination_deg"] == 0.0
    assert len(pls[0]["phase_angles_deg"]) == 8


def test_molniya_tundra_igso():
    m = ct.molniya(n_planes=3)
    assert all(p["perigee_arg_deg"] == 270.0 and p["inclination_deg"] == 63.4
               for p in m)
    cons = _spawn(m)  # apsides converted per plane
    assert cons[0].e > 0.5
    t = ct.tundra(n_planes=3)
    a_geo = (MU_KM3_S2 * (ct.T_SIDEREAL_S / (2 * math.pi)) ** 2) ** (1 / 3)
    assert t[0]["semi_major_axis_km"] == pytest.approx(a_geo)
    q = ct.igso(n_sats=3)
    assert len(q) == 3 and q[0]["eccentricity"] == 0.0


def test_multi_shell_renumbers_orb_ids():
    sh1 = ct.walker_delta(total_sats=4, num_planes=2, inclination_deg=53.0,
                          altitude_km=550.0)
    sh2 = ct.walker_delta(total_sats=4, num_planes=2, inclination_deg=70.0,
                          altitude_km=570.0)
    combo = ct.multi_shell(sh1, sh2)
    assert [p["orb_id"] for p in combo] == [1, 2, 3, 4]
    assert len(_spawn(combo)) == 8


def test_sun_sync_inclination():
    i = ct.sun_synchronous_inclination_deg(700.0)
    assert 97.5 < i < 98.5  # canonical ~98.2° at 700 km


def test_repeat_ground_track_a():
    # 14 revs/day → LEO ~ 880 km class semi-major axis
    a = ct.repeat_ground_track_a_km(14, 1)
    assert 7200.0 < a < 7300.0
    # sanity: period times revs == sidereal day
    T = 2 * math.pi * math.sqrt(a ** 3 / MU_KM3_S2)
    assert T * 14 == pytest.approx(ct.T_SIDEREAL_S, rel=1e-9)
