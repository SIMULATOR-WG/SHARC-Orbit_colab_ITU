"""Tests for the parametric PFD-mask generator (WS3 foundation, R5/R28/R29)."""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.mask_generator import (  # noqa: E402
    BeamSpec, MaskGenParams, NULL_DB,
    generate_pfd_mask_azel, write_pfd_mask_xml,
)
from src.pfd_mask import load_pfd_mask_from_xml_content  # noqa: E402


def _params(**kw) -> MaskGenParams:
    base = dict(
        altitude_km=1200.0,
        beams=[BeamSpec(power_dbw=10.0, peak_gain_dbi=30.0, hpbw_deg=5.0)],
        low_freq_mhz=17700.0, high_freq_mhz=18600.0, refbw_khz=40.0,
    )
    base.update(kw)
    return MaskGenParams(**base)


def test_nadir_pfd_matches_analytic():
    # Single nadir beam: pfd(0,0) = P + Gmax − 10log10(4π h²) (§C2.3.1).
    p = _params()
    m = generate_pfd_mask_azel(p, lat_grid_deg=[0.0],
                               az_grid_deg=[0.0], el_grid_deg=[0.0])
    expected = 10.0 + 30.0 - 10.0 * math.log10(4 * math.pi * (1200e3) ** 2)
    assert m["pfd"][0, 0, 0] == pytest.approx(expected, abs=1e-9)


def test_off_boresight_rolloff_and_earth_miss():
    p = _params()
    m = generate_pfd_mask_azel(p, lat_grid_deg=[0.0],
                               az_grid_deg=[0.0, 2.5, 80.0],
                               el_grid_deg=[0.0])
    pfd = m["pfd"][0, :, 0]
    # −3 dB at HPBW/2 (2.5°), modulo the tiny slant-range increase.
    assert pfd[0] - pfd[1] == pytest.approx(3.0, abs=0.05)
    # 80° off nadir at 1200 km misses the Earth → §C1 null.
    assert pfd[2] == NULL_DB


def test_nco_limits_simultaneous_beams():
    beams = [BeamSpec(10.0, 30.0, 5.0), BeamSpec(10.0, 30.0, 5.0),
             BeamSpec(10.0, 30.0, 5.0)]
    m_all = generate_pfd_mask_azel(_params(beams=beams),
                                   lat_grid_deg=[0.0], az_grid_deg=[0.0],
                                   el_grid_deg=[0.0])
    m_1 = generate_pfd_mask_azel(_params(beams=beams, n_co=1),
                                 lat_grid_deg=[0.0], az_grid_deg=[0.0],
                                 el_grid_deg=[0.0])
    # 3 equal co-located beams: +10log10(3) vs a single one.
    assert (m_all["pfd"][0, 0, 0] - m_1["pfd"][0, 0, 0]
            == pytest.approx(10.0 * math.log10(3.0), abs=1e-9))


def test_operating_latitude_band_nulls():
    p = _params(lat_min_deg=-50.0, lat_max_deg=50.0)
    m = generate_pfd_mask_azel(p, lat_grid_deg=[-60.0, 0.0, 60.0],
                               az_grid_deg=[0.0], el_grid_deg=[0.0])
    assert m["pfd"][0, 0, 0] == NULL_DB
    assert m["pfd"][2, 0, 0] == NULL_DB
    assert m["pfd"][1, 0, 0] > -200.0


def test_xml_roundtrip_through_engine_reader():
    p = _params(sat_name="GEN-TEST", ntc_id="42", mask_id=7)
    m = generate_pfd_mask_azel(p, lat_grid_deg=[-10.0, 0.0, 10.0],
                               az_grid_deg=np.arange(-10.0, 10.1, 5.0),
                               el_grid_deg=np.arange(-10.0, 10.1, 5.0))
    xml = write_pfd_mask_xml(m, p)
    pm = load_pfd_mask_from_xml_content(xml, mask_id=7)
    assert pm.mask_type == "azimuth_elevation"
    assert pm.refbw_khz == 40.0
    assert pm.low_freq_mhz == 17700.0
    # Grid identity: engine query at grid nodes reproduces the generated value.
    for (i, la), (j, az), (k, el) in [((1, 0.0), (2, 0.0), (2, 0.0)),
                                      ((0, -10.0), (0, -10.0), (4, 10.0))]:
        assert pm.get_pfd(az, la, el) == pytest.approx(
            round(m["pfd"][i, j, k], 2), abs=0.005)


def test_gso_arc_avoidance_switches_nadir_beam_by_latitude():
    # Single nadir beam + alpha0=6°: at the EQUATOR the boresight cell sees
    # the GSO at the zenith (alpha ~ 0 -> beam OFF, C2.2); at 60° latitude the
    # GSO is far to the south (alpha large -> beam ON).
    p = _params(gso_arc_alpha0_deg=6.0)
    m = generate_pfd_mask_azel(p, lat_grid_deg=[0.0, 60.0],
                               az_grid_deg=[0.0], el_grid_deg=[0.0])
    assert m["pfd"][0, 0, 0] == NULL_DB          # equator: inside zone -> off
    assert m["pfd"][1, 0, 0] > -200.0            # 60°: on
    # Without mitigation both latitudes are served.
    m2 = generate_pfd_mask_azel(_params(), lat_grid_deg=[0.0, 60.0],
                                az_grid_deg=[0.0], el_grid_deg=[0.0])
    assert m2["pfd"][0, 0, 0] > -200.0


def test_option1_native_peak_at_alpha_zero():
    # Native lat × α × ΔLong generation (§C2.4.1 max-binning): at the equator
    # with a single nadir beam, the peak cell is (α≈0, ΔLong≈0) and its value
    # matches the analytic nadir pfd (the boresight sample lands there).
    from src.mask_generator import generate_pfd_mask_alpha_dlon
    p = _params(sat_name="GEN1", ntc_id="7", mask_id=1)
    m = generate_pfd_mask_alpha_dlon(
        p, lat_grid_deg=[0.0],
        alpha_grid_deg=np.arange(-20.0, 20.1, 2.0),
        dlon_grid_deg=np.arange(-20.0, 20.1, 2.0),
        sample_step_deg=0.5,
    )
    pfd = m["pfd"][0]
    i0 = int(np.argmin(np.abs(m["alpha"])))
    j0 = int(np.argmin(np.abs(m["dlon"])))
    peak_ij = np.unravel_index(np.argmax(pfd), pfd.shape)
    assert abs(m["alpha"][peak_ij[0]]) <= 2.0
    expected = 10.0 + 30.0 - 10.0 * math.log10(4 * math.pi * (1200e3) ** 2)
    assert pfd[i0, j0] == pytest.approx(expected, abs=0.2)
    # Off-peak alpha rows roll off (beam pattern), never above the peak.
    assert pfd.max() <= pfd[peak_ij] + 1e-9


def test_option1_native_xml_roundtrip():
    from src.mask_generator import generate_pfd_mask_alpha_dlon
    p = _params(sat_name="GEN1", ntc_id="7", mask_id=1)
    m = generate_pfd_mask_alpha_dlon(
        p, lat_grid_deg=[-10.0, 0.0, 10.0],
        alpha_grid_deg=np.arange(-10.0, 10.1, 5.0),
        dlon_grid_deg=np.arange(-10.0, 10.1, 5.0),
        sample_step_deg=1.0,
    )
    xml = write_pfd_mask_xml(m, p, mask_type="alpha_deltaLongitude")
    pm = load_pfd_mask_from_xml_content(xml, mask_id=1)
    assert pm.mask_type == "alpha_deltaLongitude"
    v = pm.get_pfd(0.0, 0.0, 0.0)
    assert v == pytest.approx(round(float(m["pfd"][1,
        int(np.argmin(np.abs(m["alpha"]))),
        int(np.argmin(np.abs(m["dlon"])))]), 2), abs=0.005)


def test_alpha_cutoff_mode_option1():
    # Direct alpha-axis cutoff: |alpha| < alpha0 rows carry NULL, all beams ON
    # (mask value just outside the zone is the full main-beam envelope).
    from src.mask_generator import generate_pfd_mask_alpha_dlon
    p = _params(gso_arc_alpha0_deg=6.0, gso_arc_mode="alpha_cutoff")
    m = generate_pfd_mask_alpha_dlon(
        p, lat_grid_deg=[0.0],
        alpha_grid_deg=np.arange(-20.0, 20.1, 2.0),
        dlon_grid_deg=np.arange(-10.0, 10.1, 5.0),
        sample_step_deg=1.0,
    )
    a = m["alpha"]
    pfd = m["pfd"][0]
    inside = np.abs(a) < 6.0
    assert (pfd[inside] == NULL_DB).all()
    # outside the zone the mask is populated (equator, nadir beam still ON)
    assert (pfd[~inside] > -900.0).any()


def test_alpha_cutoff_mode_azel():
    # Same cutoff on the az/el grid. At the equator an EQUATORIAL ground point
    # (el=0, any az) lies in the GSO-arc plane -> alpha=0 -> in zone; a point
    # displaced NORTH (el=30 -> alpha ~ 29 deg) is out of the zone.
    p = _params(gso_arc_alpha0_deg=6.0, gso_arc_mode="alpha_cutoff")
    m = generate_pfd_mask_azel(p, lat_grid_deg=[0.0],
                               az_grid_deg=[0.0], el_grid_deg=[0.0, 30.0])
    assert m["pfd"][0, 0, 0] == NULL_DB          # nadir/equatorial: in zone
    assert m["pfd"][0, 0, 1] > -900.0            # 30 deg north: out of zone


def test_unreachable_alpha_rows_filled():
    # At sat lat -10 deg the visible-cap alpha image has a REAL gap
    # (~[4..20] deg): those rows must be filled by alpha-axis interpolation
    # (full-grid mask, C1 continuity), not left as -1000 bands.
    from src.mask_generator import generate_pfd_mask_alpha_dlon
    p = _params()
    m = generate_pfd_mask_alpha_dlon(
        p, lat_grid_deg=[-10.0],
        alpha_grid_deg=np.arange(-40.0, 40.1, 2.0),
        dlon_grid_deg=np.arange(-30.0, 30.1, 10.0),
        sample_step_deg=0.5,
    )
    pfd = m["pfd"][0]
    assert (pfd > -900.0).all()          # no null bands anywhere on the grid


def test_alpha_cutoff_user_fill_value():
    # User-defined zone value: the |alpha| < alpha0 band carries it verbatim
    # on both grids (default -1000 remains the C1 null).
    from src.mask_generator import generate_pfd_mask_alpha_dlon
    p = _params(gso_arc_alpha0_deg=6.0, gso_arc_mode="alpha_cutoff",
                gso_arc_fill_dbw=-185.0)
    m = generate_pfd_mask_alpha_dlon(
        p, lat_grid_deg=[0.0],
        alpha_grid_deg=np.arange(-20.0, 20.1, 2.0),
        dlon_grid_deg=np.arange(-10.0, 10.1, 5.0),
        sample_step_deg=1.0,
    )
    inside = np.abs(m["alpha"]) < 6.0
    assert (m["pfd"][0][inside] == -185.0).all()
    m2 = generate_pfd_mask_azel(p, lat_grid_deg=[0.0],
                                az_grid_deg=[0.0], el_grid_deg=[0.0, 30.0])
    assert m2["pfd"][0, 0, 0] == -185.0           # nadir cell: in zone
    assert m2["pfd"][0, 0, 1] > -180.0            # north cell: out of zone


def test_projection_symmetry_north_south():
    # Regression: a freshly generated alpha/dLong mask, projected through the
    # VIEWER's exact query path (engine alpha/optimal-GSO + dLong + subsat-lat
    # row), must peak at the sub-satellite point in BOTH hemispheres. An old
    # converter-generated mask painted southern footprints mirrored/offset.
    from src.mask_generator import generate_pfd_mask_alpha_dlon
    from src.mask_converter import _sat_ecef
    from src.geometry import (compute_alpha_and_optimal_gso_multi_es_batch,
                              delta_longitude_s1503_deg)
    from src.coordinates import ecef_to_lla
    from src.constants import RE_KM

    p = _params(mask_id=1)
    m = generate_pfd_mask_alpha_dlon(
        p, lat_grid_deg=np.arange(-90.0, 90.1, 10.0),
        alpha_grid_deg=np.arange(-90.0, 90.1, 2.0),
        dlon_grid_deg=np.arange(-30.0, 30.1, 2.0),
        sample_step_deg=1.0,
    )
    xml = write_pfd_mask_xml(m, p, mask_type="alpha_deltaLongitude")
    mask = load_pfd_mask_from_xml_content(xml, mask_id=1)

    for sat_lat in (45.0, -45.0):
        sat = _sat_ecef(sat_lat, 0.0, 1200.0)
        subsat_lat, subsat_lon, _ = ecef_to_lla(sat)
        lat = np.arange(-90.0, 90.01, 2.0)
        lon = np.arange(-30.0, 30.01, 2.0)
        LAT, LON = np.meshgrid(lat, lon, indexing="ij")
        latr = np.radians(LAT.ravel())
        lonr = np.radians(LON.ravel())
        es = np.column_stack((RE_KM * np.cos(latr) * np.cos(lonr),
                              RE_KM * np.cos(latr) * np.sin(lonr),
                              RE_KM * np.sin(latr)))
        diff = sat[None, :] - es
        rng = np.linalg.norm(diff, axis=1)
        vis = np.einsum("ij,ij->i", es / RE_KM, diff) / rng >= 0.0
        a_v, gso = compute_alpha_and_optimal_gso_multi_es_batch(
            es[vis], sat, LAT.ravel()[vis], LON.ravel()[vis])
        gso_lon = np.degrees(np.arctan2(gso[:, 1], gso[:, 0]))
        d_v = delta_longitude_s1503_deg(gso_lon,
                                        np.full_like(gso_lon, subsat_lon))
        pfd = mask.get_pfd_batch(a_v, np.full_like(a_v, subsat_lat), d_v)
        pk = np.argmax(pfd)
        lat_pk = LAT.ravel()[vis][pk]
        lon_pk = LON.ravel()[vis][pk]
        assert abs(lat_pk - subsat_lat) <= 2.0, (sat_lat, lat_pk)
        assert abs(lon_pk - subsat_lon) <= 2.0, (sat_lat, lon_pk)


def test_alpha_sign_hemisphere_mirror_figs56_59():
    # S.1503-4 D6.4.4.3 Figures 56-59: the alpha sign MIRRORS between
    # hemispheres. Zenith/nadir geometry (no forward crossing of the XY
    # plane, R_z0 = infinity): ABOVE the visible arc -> alpha<0 for a
    # northern ES (Fig 57) and alpha>0 for a SOUTHERN one (Fig 59). The
    # nadir-beam peak of a generated mask therefore sits on the NEGATIVE
    # alpha side for northern satellite latitudes and on the POSITIVE side
    # for southern ones (Figs 56/58).
    from src.mask_converter import _sat_ecef, _alpha_dlon_batch
    from src.geometry import compute_alpha_angle_fast_components
    from src.mask_generator import generate_pfd_mask_alpha_dlon

    for es_lat, exp_sign in ((10.0, -1), (-10.0, +1), (45.0, -1), (-45.0, +1)):
        es = _sat_ecef(es_lat, 0.0, 0.0)
        sat = _sat_ecef(es_lat, 0.0, 1200.0)      # satellite at the zenith
        a_e = compute_alpha_angle_fast_components(
            es_x=es[0], es_y=es[1], es_z=es[2],
            ng_x=sat[0], ng_y=sat[1], ng_z=sat[2],
            es_lat_deg=es_lat, es_lon_deg=0.0)
        a_g, _ = _alpha_dlon_batch(es[None, :], sat, sat_lon_deg=0.0)
        assert np.sign(a_e) == exp_sign, (es_lat, a_e)
        assert np.sign(float(a_g[0])) == exp_sign, (es_lat, float(a_g[0]))
        assert a_e == pytest.approx(float(a_g[0]), abs=1e-3)

    # Mask-level: nadir-beam peak side flips with the satellite hemisphere.
    p = _params()
    m = generate_pfd_mask_alpha_dlon(
        p, lat_grid_deg=[-45.0, 45.0],
        alpha_grid_deg=np.arange(-90.0, 90.1, 2.0),
        dlon_grid_deg=[0.0], sample_step_deg=1.0)
    a = m["alpha"]
    pk_south = a[int(np.argmax(m["pfd"][0][:, 0]))]
    pk_north = a[int(np.argmax(m["pfd"][1][:, 0]))]
    assert pk_south > 0 and pk_north < 0
    assert pk_south == pytest.approx(-pk_north, abs=2.0)
