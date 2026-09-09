"""`mask_info` frequency unit normalisation (SNS v10 GHz vs EPS V41 MHz).

The column changed unit between schema versions — EPS V41 §6.5.1.1 footnote:
"in SNS v10 it is currently GHz, care should be taken to introduce this change
in new SNS structure". Filings of both vintages are read by the same code, so
`read_mask_info` infers the unit from magnitude and always returns GHz.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.srs_reader import _mask_info_freq_to_ghz, read_mask_info  # noqa: E402

_V10 = REPO / "docs" / "test_data" / "MCSAT_LEO_Ka_SRS.mdb"
_V41 = REPO / "ITU_fillings" / "ITU_doc" / "127520101 SRS.MDB"


@pytest.mark.parametrize("raw,expected", [
    # SNS v10 — already GHz, must pass through untouched.
    (3.7, 3.7),
    (17.8, 17.8),
    (20.2, 20.2),
    (75.0, 75.0),
    # EPS V41 — MHz, must be divided.
    (3700.0, 3.7),
    (17800.0, 17.8),
    (20200.0, 20.2),
    (75000.0, 75.0),
])
def test_unit_inferred_from_magnitude(raw, expected):
    assert _mask_info_freq_to_ghz(raw) == pytest.approx(expected)


def test_threshold_is_clear_of_every_epfd_band():
    """No real band lands near the cut: 75 GHz vs 3700 MHz leaves a wide gap."""
    highest_ghz = 75.0        # top of the Q/V range, well under the threshold
    lowest_mhz = 3700.0       # 3.7 GHz, the lowest Art. 22 band, in MHz
    assert _mask_info_freq_to_ghz(highest_ghz) == highest_ghz
    assert _mask_info_freq_to_ghz(lowest_mhz) == pytest.approx(3.7)


@pytest.mark.skipif(not _V10.exists(), reason="v10 sample MDB not present")
def test_reads_sns_v10_filing_in_ghz():
    pfd = [m for m in read_mask_info(str(_V10), ntc_id="101") if m.f_mask == "P"]
    assert pfd, "no PFD mask in the v10 sample"
    assert pfd[0].freq_min_ghz == pytest.approx(17.8)
    assert pfd[0].freq_max_ghz == pytest.approx(19.3)


@pytest.mark.skipif(not _V41.exists(), reason="EPS V41 sample MDB not present")
def test_reads_eps_v41_filing_stored_in_mhz():
    pfd = {m.mask_id: m for m in read_mask_info(str(_V41), ntc_id="127520101")
           if m.f_mask == "P"}
    assert set(pfd) == {1, 4}
    assert pfd[1].freq_min_ghz == pytest.approx(17.8)
    assert pfd[1].freq_max_ghz == pytest.approx(18.6)
    # The track-duration band (param_id 7 in the operating-parameter mask).
    assert pfd[4].freq_min_ghz == pytest.approx(19.7)
    assert pfd[4].freq_max_ghz == pytest.approx(20.2)


@pytest.mark.skipif(not _V41.exists(), reason="EPS V41 sample MDB not present")
def test_article22_resolves_for_a_v41_filing():
    """The regression this guards: unnormalised MHz found no Art. 22 table."""
    from src.article22_tables import list_article22_downlink_possibilities

    pfd = [m for m in read_mask_info(str(_V41), ntc_id="127520101")
           if m.f_mask == "P"]
    got = list_article22_downlink_possibilities(
        freq_min_ghz=min(m.freq_min_ghz for m in pfd),
        freq_max_ghz=max(m.freq_max_ghz for m in pfd),
    )
    assert [s["service"] for s in got["services"]] == ["FSS"]
