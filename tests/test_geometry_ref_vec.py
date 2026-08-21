"""
Unit tests for compute_angular_separation_from_ref_vector (geometry.py).

Test coordinate system:
  ES at lat=0°, lon=0°  →  ECEF = [RE, 0, 0]
  ENU rotation at this point:
    East  → ECEF  [0, 1, 0]   (y-axis)
    North → ECEF  [0, 0, 1]   (z-axis)
    Up    → ECEF  [1, 0, 0]   (x-axis)

This makes expected angles trivial to derive analytically.
"""

import math
import unittest
import numpy as np

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.geometry import compute_angular_separation_from_ref_vector

RE = 6371.0   # km
H  = 1000.0   # satellite altitude above ES (km) — magnitude only

# ES at equator/prime-meridian
ES = np.array([RE, 0.0, 0.0])
LAT, LON = 0.0, 0.0

# Cardinal satellite positions (direction from ES)
SAT_ZENITH = np.array([[RE + H, 0.0,     0.0    ]])   # az=any,  el=+90°
SAT_EAST   = np.array([[RE,     H,        0.0    ]])   # az=+90°, el=  0°
SAT_NORTH  = np.array([[RE,     0.0,      H      ]])   # az=  0°, el=  0°
SAT_SOUTH  = np.array([[RE,     0.0,     -H      ]])   # az=180°, el=  0°
SAT_WEST   = np.array([[RE,    -H,        0.0    ]])   # az=270°, el=  0°

# Satellite at az=0°, el=45° → ENU = [0, 1/√2, 1/√2]
_s = H / math.sqrt(2.0)
SAT_N45    = np.array([[RE + _s, 0.0, _s]])            # North at 45° elevation


def _sep(sat, ref_az, ref_el):
    """Helper: scalar separation for a single-satellite batch."""
    return float(compute_angular_separation_from_ref_vector(
        ES, sat, LAT, LON, ref_az, ref_el,
    )[0])


class TestZenithReference(unittest.TestCase):
    """Reference vector pointing straight up (az=0°, el=90°)."""

    def test_zenith_sat_zero_separation(self):
        self.assertAlmostEqual(_sep(SAT_ZENITH, 0.0, 90.0), 0.0, places=6)

    def test_east_sat_is_90(self):
        self.assertAlmostEqual(_sep(SAT_EAST, 0.0, 90.0), 90.0, places=6)

    def test_north_sat_is_90(self):
        self.assertAlmostEqual(_sep(SAT_NORTH, 0.0, 90.0), 90.0, places=6)

    def test_south_sat_is_90(self):
        self.assertAlmostEqual(_sep(SAT_SOUTH, 0.0, 90.0), 90.0, places=6)

    def test_west_sat_is_90(self):
        self.assertAlmostEqual(_sep(SAT_WEST, 0.0, 90.0), 90.0, places=6)

    def test_north_45el_is_45(self):
        self.assertAlmostEqual(_sep(SAT_N45, 0.0, 90.0), 45.0, places=5)


class TestHorizonReferences(unittest.TestCase):
    """Reference vector in the horizontal plane."""

    def test_east_ref_east_sat_zero(self):
        self.assertAlmostEqual(_sep(SAT_EAST, 90.0, 0.0), 0.0, places=6)

    def test_east_ref_west_sat_180(self):
        self.assertAlmostEqual(_sep(SAT_WEST, 90.0, 0.0), 180.0, places=6)

    def test_east_ref_north_sat_90(self):
        self.assertAlmostEqual(_sep(SAT_NORTH, 90.0, 0.0), 90.0, places=6)

    def test_east_ref_zenith_sat_90(self):
        self.assertAlmostEqual(_sep(SAT_ZENITH, 90.0, 0.0), 90.0, places=6)

    def test_north_ref_north_sat_zero(self):
        self.assertAlmostEqual(_sep(SAT_NORTH, 0.0, 0.0), 0.0, places=6)

    def test_north_ref_south_sat_180(self):
        self.assertAlmostEqual(_sep(SAT_SOUTH, 0.0, 0.0), 180.0, places=6)

    def test_north_ref_east_sat_90(self):
        self.assertAlmostEqual(_sep(SAT_EAST, 0.0, 0.0), 90.0, places=6)

    def test_north_ref_zenith_sat_90(self):
        self.assertAlmostEqual(_sep(SAT_ZENITH, 0.0, 0.0), 90.0, places=6)

    def test_north_ref_north45el_is_45(self):
        # Satellite at az=0°, el=45°  vs  North reference (az=0°, el=0°)
        # dot([0, 1/√2, 1/√2], [0, 1, 0]) = 1/√2  → 45°
        self.assertAlmostEqual(_sep(SAT_N45, 0.0, 0.0), 45.0, places=5)


class TestBatch(unittest.TestCase):
    """Batch (N, 3) input vs per-satellite scalar calls."""

    def test_batch_matches_scalar(self):
        sats = np.vstack([SAT_ZENITH, SAT_EAST, SAT_NORTH, SAT_SOUTH, SAT_WEST])
        result = compute_angular_separation_from_ref_vector(ES, sats, LAT, LON, 0.0, 90.0)
        expected = [0.0, 90.0, 90.0, 90.0, 90.0]
        self.assertEqual(result.shape, (5,))
        for i, exp in enumerate(expected):
            self.assertAlmostEqual(float(result[i]), exp, places=5,
                                   msg=f"Satellite {i}: expected {exp}°, got {result[i]:.6f}°")

    def test_empty_batch(self):
        result = compute_angular_separation_from_ref_vector(
            ES, np.zeros((0, 3)), LAT, LON, 0.0, 90.0,
        )
        self.assertEqual(result.shape, (0,))

    def test_single_row_1d_input(self):
        sat_1d = np.array([RE + H, 0.0, 0.0])
        result = compute_angular_separation_from_ref_vector(ES, sat_1d, LAT, LON, 0.0, 90.0)
        self.assertEqual(result.shape, (1,))
        self.assertAlmostEqual(float(result[0]), 0.0, places=6)


class TestOutputBounds(unittest.TestCase):
    """Separation must always be in [0°, 180°]."""

    def test_all_separations_in_range(self):
        rng = np.random.default_rng(42)
        # Random satellites at 1000 km altitude in random directions above ES
        dirs = rng.standard_normal((200, 3))
        dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)
        sats = ES + H * dirs
        for az in [0.0, 45.0, 90.0, 135.0, 180.0]:
            for el in [0.0, 30.0, 60.0, 90.0]:
                result = compute_angular_separation_from_ref_vector(
                    ES, sats, LAT, LON, az, el,
                )
                self.assertTrue(np.all(result >= 0.0),
                                msg=f"Negative separation for az={az}, el={el}")
                self.assertTrue(np.all(result <= 180.0 + 1e-9),
                                msg=f"Separation > 180° for az={az}, el={el}")


class TestNonEquatorialES(unittest.TestCase):
    """Verify the function gives consistent results at a non-trivial ES location."""

    def test_satellite_on_same_direction_as_ref_gives_zero(self):
        # ES at lat=45°, lon=30°
        lat, lon = 45.0, 30.0
        lat_r, lon_r = math.radians(lat), math.radians(lon)
        sl, cl = math.sin(lat_r), math.cos(lat_r)
        so, co = math.sin(lon_r), math.cos(lon_r)
        # ES ECEF
        es = np.array([RE * cl * co, RE * cl * so, RE * sl])
        # Reference vector: az=60°, el=30°
        az_r, el_r = math.radians(60.0), math.radians(30.0)
        # Build ref in ENU, then convert to ECEF direction for the satellite
        r_east  = math.sin(az_r) * math.cos(el_r)
        r_north = math.cos(az_r) * math.cos(el_r)
        r_up    = math.sin(el_r)
        # ENU→ECEF rotation (transpose of R_enu)
        R_enu_T = np.array([
            [-so,     -sl * co,  cl * co],
            [ co,     -sl * so,  cl * so],
            [ 0.0,    cl,        sl     ],
        ])
        dir_ecef = R_enu_T @ np.array([r_east, r_north, r_up])
        sat = (es + H * dir_ecef).reshape(1, 3)

        result = compute_angular_separation_from_ref_vector(es, sat, lat, lon, 60.0, 30.0)
        self.assertAlmostEqual(float(result[0]), 0.0, places=4)


if __name__ == "__main__":
    unittest.main()
