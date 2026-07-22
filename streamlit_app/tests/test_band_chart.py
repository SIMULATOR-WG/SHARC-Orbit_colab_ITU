"""band_chart.py — ITU-style occupancy strip HTML generation."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from streamlit_app.lib.band_chart import bands_chart_html, _fmt_mhz  # noqa: E402


def _rows():
    return [
        {"label": "SYS-A · ntc 101", "bands": [(17.8, 18.6), (18.8, 19.3)],
         "kind": "tx"},
        {"label": "SYS-B · ntc 102", "bands": [(18.0, 19.7)], "kind": "pfd",
         "sublabel": "PFD mask band — no grp data"},
        {"label": "Common operating (all systems)", "bands": [(18.0, 18.6)],
         "kind": "common"},
    ]


NNBSP = " "  # narrow no-break space — thousands separator


def test_fmt_mhz_thousands():
    assert _fmt_mhz(17.8) == f"17{NNBSP}800 MHz"
    assert _fmt_mhz(19.2995) == f"19{NNBSP}299.50 MHz"


def test_shared_scale_layout():
    html = bands_chart_html(_rows(), title="Band occupancy",
                            shared_scale=True, marker_ghz=18.2,
                            marker_label="18 200.00 MHz (frequency run)")
    assert "Band occupancy" in html
    assert html.count('class="so-bc-row') == 3
    assert html.count("so-bc-seg") >= 4          # 2 + 1 + 1 segments
    assert 'so-bc-seg pfd' in html               # PFD-fallback styling
    assert 'so-bc-row common' in html            # ringed common row
    assert html.count('class="so-bc-marker"') == 3   # marker on every row
    assert "(2 bands)" in html and "(1 band)" in html
    assert "frequency run" in html
    # Shared axis rendered (global lower/upper labels)
    assert 'class="so-bc-axis"' in html


def test_per_row_scale_itu_layout():
    rows = [
        {"label": "Emission (Tx)", "bands": [(17.7, 18.55), (18.8, 19.7)],
         "kind": "tx"},
        {"label": "Reception (Rx)", "bands": [(27.5, 30.0)], "kind": "tx"},
        {"label": "ISL", "bands": [], "kind": "tx"},
    ]
    html = bands_chart_html(rows, shared_scale=False)
    assert "Lower" in html and "Upper" in html and "Frequency limits" in html
    assert f"17{NNBSP}700 MHz" in html and f"19{NNBSP}700 MHz" in html
    assert f"27{NNBSP}500 MHz" in html and f"30{NNBSP}000 MHz" in html
    assert "(0 bands)" in html                   # empty ISL-style row
    assert 'class="so-bc-axis"' not in html      # no global axis in ITU mode


def test_empty_rows_no_crash():
    html = bands_chart_html([{"label": "X", "bands": [], "kind": "tx"}])
    assert "No declared band." in html


def test_labels_escaped():
    html = bands_chart_html(
        [{"label": "<script>x</script>", "bands": [(1.0, 2.0)], "kind": "tx"}])
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
