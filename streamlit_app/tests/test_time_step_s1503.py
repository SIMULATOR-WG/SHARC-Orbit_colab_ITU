"""Focused tests for S.1503 time-step dimensioning."""
from __future__ import annotations

from src.antenna import ITURS1428Antenna  # type: ignore[import]
from src.time_step import compute_time_step_and_count  # type: ignore[import]


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
