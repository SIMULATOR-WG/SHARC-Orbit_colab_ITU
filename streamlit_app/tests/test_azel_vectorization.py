"""Parity tests for the vectorized azimuth/elevation EPFD↓ path.

The az/el mask accumulation was moved from a scalar per-satellite loop onto the
same batch path used by alpha/Δλ masks (``_pfd_mask_uses_batch`` now covers all
3D masks). These tests pin the two az/el-specific legs to their scalar
references so the vectorization cannot drift:

  1. Geometry — ``_compute_mask_az_el_per_sat_frame_batch`` (one ES, N sats,
     each in its own local frame) must equal the scalar
     ``_compute_mask_az_el_from_frame`` row by row, including NaN on degenerate
     rows (ES coincident with the satellite).
  2. Interpolation — ``PFDMaskXML.get_pfd_batch`` must equal elementwise
     ``get_pfd`` for an az/el mask.

Everything else on the accumulation path (alpha classification, off-axis gain,
ε₀, OR-condition, MAX_CO_FREQ) is shared with the already-validated alpha batch
path, so equivalence of these two legs implies equivalence of the whole.
"""
from __future__ import annotations

import numpy as np
import pytest

from src.wcg_search import (  # type: ignore[import]
    _build_sat_local_frame_ecef,
    _compute_mask_az_el_from_frame,
    _compute_mask_az_el_per_sat_frame_batch,
)
from src.pfd_mask import PFDMaskXML  # type: ignore[import]

_RE_KM = 6378.0


def _random_sats(n: int, seed: int) -> np.ndarray:
    rng = np.random.RandomState(seed)
    dirs = rng.randn(n, 3)
    dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)
    radii = _RE_KM + rng.uniform(500.0, 36000.0, size=n)
    return dirs * radii[:, None]


def test_azel_geometry_batch_matches_scalar():
    sats = _random_sats(3000, seed=0)
    es = np.array([_RE_KM, 0.0, 0.0], dtype=float)

    az_s = np.empty(len(sats))
    el_s = np.empty(len(sats))
    for i, s in enumerate(sats):
        frame = _build_sat_local_frame_ecef(s)
        r = _compute_mask_az_el_from_frame(es, s, frame)
        if r is None:
            az_s[i] = el_s[i] = np.nan
        else:
            az_s[i], el_s[i] = r

    az_b, el_b = _compute_mask_az_el_per_sat_frame_batch(es, sats)

    # NaN pattern must agree exactly (degenerate-row handling).
    assert np.array_equal(np.isnan(az_s), np.isnan(az_b))
    assert np.array_equal(np.isnan(el_s), np.isnan(el_b))

    m = ~np.isnan(az_s)
    assert np.max(np.abs(az_s[m] - az_b[m])) < 1e-9
    assert np.max(np.abs(el_s[m] - el_b[m])) < 1e-9


def test_azel_degenerate_row_is_nan():
    sats = _random_sats(4, seed=3)
    es = sats[0].copy()  # ES coincident with satellite 0 → degenerate
    az_b, el_b = _compute_mask_az_el_per_sat_frame_batch(es, sats)
    assert np.isnan(az_b[0]) and np.isnan(el_b[0])
    assert not np.isnan(az_b[1])  # other rows still valid


def _build_azel_mask() -> PFDMaskXML:
    lats = [-30.0, 0.0, 30.0]
    azs = [-10.0, 0.0, 10.0]
    els = [-5.0, 0.0, 5.0]

    def model(lat, az, el):
        return -150.0 + 0.1 * lat - 0.7 * abs(az) - 0.5 * abs(el)

    xml = [
        '<satellite_system ntc_id="t" sat_name="t">',
        '<pfd_mask mask_id="1" type="azimuth_elevation" refbw_khz="40" '
        'a_name="latitude" b_name="azimuth" c_name="elevation">',
    ]
    for la in lats:
        xml.append(f'<by_a a="{la}">')
        for az in azs:
            xml.append(f'<by_b b="{az}">')
            for el in els:
                xml.append(f'<pfd c="{el}">{model(la, az, el):.4f}</pfd>')
            xml.append("</by_b>")
        xml.append("</by_a>")
    xml.append("</pfd_mask></satellite_system>")
    return PFDMaskXML.from_xml_content("\n".join(xml), mask_id=1)


def test_azel_get_pfd_batch_matches_scalar():
    mask = _build_azel_mask()
    assert mask.mask_type == "azimuth_elevation"
    assert getattr(mask, "_dim", None) == 3

    rng = np.random.RandomState(1)
    az_q = rng.uniform(-12.0, 12.0, 500)
    lat_q = rng.uniform(-35.0, 35.0, 500)
    el_q = rng.uniform(-7.0, 7.0, 500)

    batch = mask.get_pfd_batch(az_q, lat_q, el_q)
    scalar = np.array([
        mask.get_pfd(alpha_deg=az_q[i], lat_deg=lat_q[i], delta_lon_deg=el_q[i])
        for i in range(az_q.size)
    ])
    assert np.allclose(batch, scalar, atol=1e-12)


def test_azel_mask_uses_batch_path():
    """The 3D az/el mask must qualify for the vectorized accumulation path."""
    from src.epfd_calculator import _pfd_mask_uses_batch  # type: ignore[import]

    mask = _build_azel_mask()
    assert _pfd_mask_uses_batch(mask) is True


def _build_alpha_mask() -> PFDMaskXML:
    lats = [-30.0, 0.0, 30.0]
    alphas = [0.0, 5.0, 10.0]
    dlons = [-5.0, 0.0, 5.0]
    xml = [
        '<satellite_system ntc_id="t" sat_name="t">',
        '<pfd_mask mask_id="2" type="alpha_deltaLongitude" refbw_khz="40" '
        'a_name="latitude" b_name="alpha" c_name="deltaLongitude">',
    ]
    for la in lats:
        xml.append(f'<by_a a="{la}">')
        for al in alphas:
            xml.append(f'<by_b b="{al}">')
            for dl in dlons:
                xml.append(f'<pfd c="{dl}">{-150.0 - al - abs(dl):.4f}</pfd>')
            xml.append("</by_b>")
        xml.append("</by_a>")
    xml.append("</pfd_mask></satellite_system>")
    return PFDMaskXML.from_xml_content("\n".join(xml), mask_id=2)


def test_mixed_type_multi_is_accepted_and_flagged():
    """A PFDMaskMulti fusing az/el + alpha/Δλ (method_3) is supported.

    Mixed coordinate systems are allowed as long as the dimensionality matches;
    the Multi flags itself ``is_mixed_geometry`` so the accumulator routes each
    satellite through its own sub-mask.
    """
    from src.pfd_mask import PFDMaskMulti  # type: ignore[import]

    azel = _build_azel_mask()      # mask_id 1, azimuth_elevation
    alpha = _build_alpha_mask()    # mask_id 2, alpha_deltaLongitude
    m = PFDMaskMulti({1: azel, 2: alpha}, mask_id_per_sat=[1, 2, 1, 2])
    assert m.is_mixed_geometry is True
    # is_azel_per_sat must reflect each satellite's OWN sub-mask.
    got = m.is_azel_per_sat(np.array([0, 1, 2, 3]))
    assert got.tolist() == [True, False, True, False]


def test_homogeneous_multi_not_flagged_mixed():
    from src.pfd_mask import PFDMaskMulti  # type: ignore[import]

    m = PFDMaskMulti({1: _build_azel_mask()}, mask_id_per_sat=[1, 1, 1])
    assert m.mask_type == "azimuth_elevation"
    assert m.is_mixed_geometry is False


def test_mixed_dim_multi_rejected():
    """Mixing 1D and 3D sub-masks has no coherent per-sat dispatch → reject."""
    from src.pfd_mask import PFDMaskMulti, PFDMask1D  # type: ignore[import]

    # A trivial 1D mask object (no file) — set just the attributes the guard reads.
    class _Fake1D(PFDMask1D):
        def __init__(self):
            self._dim = 1
            self.mask_type = "alpha"

    with pytest.raises(NotImplementedError):
        PFDMaskMulti({1: _build_azel_mask(), 2: _Fake1D()}, mask_id_per_sat=[1, 2])


def test_mixed_geometry_per_sat_routing_parity():
    """The accumulator's per-sat (b, c) selection + get_pfd_batch must equal a
    reference that queries each satellite's own sub-mask with its own
    coordinate system."""
    from src.pfd_mask import PFDMaskMulti  # type: ignore[import]

    azel = _build_azel_mask()    # id 1 → (az, lat, el)
    alpha = _build_alpha_mask()  # id 2 → (alpha, lat, dlon)
    # 6 sats, alternating mask assignment.
    mid_per_sat = np.array([1, 2, 1, 2, 1, 2])
    multi = PFDMaskMulti({1: azel, 2: alpha}, mask_id_per_sat=mid_per_sat)

    rng = np.random.RandomState(7)
    n = 6
    sat_idx = np.arange(n)
    lat = rng.uniform(-30, 30, n)
    az = rng.uniform(-10, 10, n)
    el = rng.uniform(-5, 5, n)
    alpha_v = rng.uniform(0, 10, n)
    dlon = rng.uniform(-5, 5, n)

    is_azel = multi.is_azel_per_sat(sat_idx)
    b = np.where(is_azel, az, alpha_v)
    c = np.where(is_azel, el, dlon)
    batch = multi.get_pfd_batch(b, lat, c, sat_indices=sat_idx)

    ref = np.empty(n)
    for i in range(n):
        mk = multi.mask_for_sat(i)
        if mk.mask_type == "azimuth_elevation":
            ref[i] = mk.get_pfd(alpha_deg=az[i], lat_deg=lat[i], delta_lon_deg=el[i])
        else:
            ref[i] = mk.get_pfd(alpha_deg=alpha_v[i], lat_deg=lat[i], delta_lon_deg=dlon[i])
    assert np.allclose(batch, ref, atol=1e-12)


def _build_typed_mask(mtype: str, mid: int) -> PFDMaskXML:
    lats = [-80.0, -40.0, 0.0, 40.0, 80.0]
    b_vals = [0.0, 1.0, 2.0, 5.0, 10.0] if mtype == "alpha_deltaLongitude" else [-10.0, -5.0, 0.0, 5.0, 10.0]
    c_vals = [-8.0, -4.0, 0.0, 4.0, 8.0]
    bn = "alpha" if mtype == "alpha_deltaLongitude" else "azimuth"
    cn = "deltaLongitude" if mtype == "alpha_deltaLongitude" else "elevation"
    x = [f'<satellite_system><pfd_mask mask_id="{mid}" type="{mtype}" refbw_khz="40" '
         f'a_name="latitude" b_name="{bn}" c_name="{cn}">']
    for la in lats:
        x.append(f'<by_a a="{la}">')
        for bb in b_vals:
            x.append(f'<by_b b="{bb}">')
            for cc in c_vals:
                x.append(f'<pfd c="{cc}">{-140.0 - 0.3*abs(bb) - 0.2*abs(cc) + 0.05*la:.4f}</pfd>')
            x.append("</by_b>")
        x.append("</by_a>")
    x.append("</pfd_mask></satellite_system>")
    return PFDMaskXML.from_xml_content("\n".join(x), mask_id=mid)


def test_mixed_geometry_aggregate_batch_matches_scalar_end_to_end():
    """End-to-end: a mixed az/el + alpha/Δλ fusion (method_3) through the real
    ``epfd_aggregate_dBW_at_instant`` (vectorized accumulator with per-sat
    routing) must equal the independent scalar fallback to machine precision."""
    import math

    from src.orbit_propagator import OrbitalElements  # type: ignore[import]
    from src.wcg_search import WCGResult, lla_to_ecef  # type: ignore[import]
    from src.antenna import ITURS1428Antenna  # type: ignore[import]
    from src.pfd_mask import PFDMaskMulti  # type: ignore[import]
    from src.epfd_calculator import (  # type: ignore[import]
        epfd_aggregate_dBW_at_instant,
        _epfd_aggregate_dBW_at_instant_scalar_fallback,
    )

    azel = _build_typed_mask("azimuth_elevation", 1)
    alpha = _build_typed_mask("alpha_deltaLongitude", 2)

    rng = np.random.RandomState(2)
    sats, mids = [], []
    for k in range(400):
        sats.append(OrbitalElements(
            a=7178.0, e=0.0, i=math.radians(rng.uniform(10, 90)),
            raan=math.radians(rng.uniform(0, 360)), omega=0.0,
            M=math.radians(rng.uniform(0, 360)),
        ))
        mids.append(1 if k % 2 == 0 else 2)
    multi = PFDMaskMulti({1: azel, 2: alpha}, mask_id_per_sat=mids)
    assert multi.is_mixed_geometry is True

    ant = ITURS1428Antenna(1.2, 12.0, 0.65)
    wcg = WCGResult(
        theta_deg=0, phi_deg=0, es_lat_deg=0.0, es_lon_deg=0.0, gso_lon_deg=0.0,
        alpha_deg=0, offaxis_deg=0, pfd_dBW=0, es_gain_rel_dB=0, epfd_dBW=0,
        elevation_deg=0, es_ecef_exact=lla_to_ecef(0.0, 0.0, 0.0),
    )
    kw = dict(constellation=sats, t_s=0.0, wcg=wcg, pfd_mask=multi, es_antenna=ant,
              alpha0_deg=2.0, min_elevation_deg=10.0, pfd_bw_correction_db=0.0)

    batch = epfd_aggregate_dBW_at_instant(**kw)
    scalar = _epfd_aggregate_dBW_at_instant_scalar_fallback(**kw)
    assert batch > -900.0  # the scenario has real contributors
    assert abs(batch - scalar) < 1e-9
