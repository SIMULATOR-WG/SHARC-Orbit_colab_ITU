"""Focused tests for S.1503 time-step dimensioning."""
from __future__ import annotations

from src.antenna import ITURS1428Antenna  # type: ignore[import]
from src.time_step import (  # type: ignore[import]
    compute_time_step_and_count,
    compute_time_step_and_count_multi,
    group_sub_constellations,
)


def test_d41_recalculation_allows_fractional_nhit() -> None:
    """S.1503-4 D4.1 defines N'hit without rounding or clamping.

    The MCSAT LEO Ka filing lands in the >1e8 non-repeating branch:
    Ncoarse=20, Nsat=18*43, so N'hit = 16 / min(20, sqrt(774)) = 0.8.
    Clamping this to 1 makes the time step too small and the run too long.
    """
    ant = ITURS1428Antenna(1.0, 17.80002, 0.99)

    res = compute_time_step_and_count(
        a_km=7578.145,
        e=0.0,
        i_deg=87.9000015258789,
        num_planes=18,
        sats_per_plane=43,
        min_elevation_deg=5.0,
        min_operating_height_km=1200.0,
        repeating_ground_track=False,
        artificial_precession=False,
        theta_3db_deg=ant.theta_3db_deg,
        nhit=16,
        literal_s1503_d42=True,
        min_exceedance_pct=0.029,
        ntracks=16,
        phi_coarse_deg=1.5,
    )

    assert res.nhit_eff == 0.8
    assert res.ncoarse == 1
    # BR_Space v10 reference run (EPFDRESULTS_MCSAT_LEO_Ka): fine step
    # 4.256 s (θ3dB = 70λ/D), 23 811 330 steps (ours +0.057% from Norbits
    # rounding).
    assert res.tstep_s == 4.256
    assert res.nsteps == 23_824_941


def _steam2_crc_kwargs(**overrides):
    """CRC STEAM-2 filing (ntc 321520026, IFIC 2985): heterogeneous planes
    (72×1 + 36×20 + 4×43 + 6×58 = 1312 sats), lowest sub-constellation
    540 km / 53.2°, non-repeating. header nbr_planes=129, plane0 has 1 sat."""
    kwargs = dict(
        a_km=540.0 + 6378.145,
        e=0.0,
        i_deg=53.2,
        num_planes=129,
        sats_per_plane=1,
        min_elevation_deg=10.0,
        min_operating_height_km=540.0,
        repeating_ground_track=False,
        theta_3db_deg=1.178958,  # 70λ/D, 1 m @ 17.80002 GHz
        nhit=16,
        literal_s1503_d42=True,
        min_exceedance_pct=0.029,
        ntracks=16,
        phi_coarse_deg=1.5,
    )
    kwargs.update(overrides)
    return kwargs


def test_d41_nsat_total_heterogeneous_planes() -> None:
    """§D4.1 √Nsatellites must use the REAL fleet size (Σ nbr_sat_pl).

    Without n_sat_total the factor collapses to √(129×1)=11.36 < Ncoarse=20,
    inflating N'hit (1.409 instead of 0.8) and the step count.
    """
    wrong = compute_time_step_and_count(**_steam2_crc_kwargs())
    assert abs(wrong.nhit_eff - 16.0 / (129 ** 0.5)) < 1e-3  # undercounted

    fixed = compute_time_step_and_count(**_steam2_crc_kwargs(n_sat_total=1312))
    # min(Ncoarse=20, √1312=36.2) = 20 → N'hit = 0.8, Δt = 1.903 s.
    assert fixed.nhit_eff == 0.8
    assert fixed.tstep_s == 1.903


def test_d41_reduce_ntracks_toggle_transfinite() -> None:
    """reduce_ntracks_1e8=True is §D4 reading B (Ntracks follows N'hit, run
    collapses ~20×); the default reading A keeps Ntracks=16.

    Official reference (EPFDResults_321520026_17.8, 'T' v5.45, 1 m dish):
    Δt = 1.88 s, N = 4 766 818. This single-sub call dimensions on the
    540 km/53.2° sub only, so it lands ~1.3% off — the exact match needs the
    §D4.1 multi-sub rule (see test_d41_multi_sub_rule_steam2).
    """
    br = compute_time_step_and_count(**_steam2_crc_kwargs(n_sat_total=1312))
    tf = compute_time_step_and_count(
        **_steam2_crc_kwargs(n_sat_total=1312, reduce_ntracks_1e8=True)
    )
    assert tf.tstep_s == br.tstep_s == 1.903  # Δt unaffected by Ntracks
    assert tf.nsteps == 4_706_626
    assert br.nsteps == 94_120_496  # Ntracks=16 kept (reading A)
    assert abs(tf.nsteps - 4_766_818) / 4_766_818 < 0.015


# CRC STEAM-2 sub-constellations: (alt km, inc°, planes, sats total).
_STEAM2_SUBS = [
    (540.0, 53.2, 72, 72),
    (570.0, 70.0, 36, 720),
    (560.0, 97.6, 10, 520),
]


def _steam2_multi_subs() -> list[dict]:
    return [
        {
            "a_km": alt + 6378.145, "e": 0.0, "i_deg": inc,
            "num_planes": npl, "sats_per_plane": max(1, nsat // npl),
            "min_operating_height_km": alt,
        }
        for alt, inc, npl, nsat in _STEAM2_SUBS
    ]


def test_d41_multi_sub_rule_steam2() -> None:
    """§D4.1 multi-sub rule: smallest Δt + longest run over sub-constellations.

    On STEAM-2 the 560 km/97.6° sub wins Δt (1.880 s, not the lowest-altitude
    540 km sub's 1.903 s — the near-retrograde inclination raises ω) while the
    540 km sub sets the longest run. Official 'T' v5.45 reference
    (EPFDResults_321520026_17.8, 1 m): Δt = 1.88 s, N = 4 766 818 —
    reading B lands within 0.1%; Δt is identical in both readings.
    """
    common = dict(
        min_elevation_deg=10.0, repeating_ground_track=False,
        theta_3db_deg=1.178958,  # 70λ/D, 1 m @ 17.80002 GHz
        nhit=16, literal_s1503_d42=True, min_exceedance_pct=0.029,
        ntracks=16, phi_coarse_deg=1.5, n_sat_total=1312,
    )
    reading_a = compute_time_step_and_count_multi(
        _steam2_multi_subs(), reduce_ntracks_1e8=False, **common)
    reading_b = compute_time_step_and_count_multi(
        _steam2_multi_subs(), reduce_ntracks_1e8=True, **common)
    assert reading_a.tstep_s == reading_b.tstep_s == 1.880
    assert reading_b.nsteps == 4_764_207
    assert abs(reading_b.nsteps - 4_766_818) / 4_766_818 < 0.001
    assert reading_a.nsteps == 95_271_970  # Ntracks=16 kept


def test_group_sub_constellations() -> None:
    """Planes sharing (a, e, i) collapse into one dimensioning set."""
    planes = []
    for alt, inc, npl, nsat in _STEAM2_SUBS:
        for _ in range(npl):
            planes.append({
                "semi_major_axis_km": alt + 6378.145,
                "eccentricity": 0.0,
                "inclination_deg": inc,
                "sats_per_plane": nsat // npl,
                "min_operating_height_km": alt,
            })
    subs = group_sub_constellations(planes)
    assert len(subs) == 3
    by_inc = {round(s["i_deg"], 1): s for s in subs}
    assert by_inc[53.2]["num_planes"] == 72
    assert by_inc[97.6]["num_planes"] == 10
    assert by_inc[70.0]["sats_per_plane"] == 20
