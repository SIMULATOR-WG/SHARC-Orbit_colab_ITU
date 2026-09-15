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


# ── letter band in the segment tooltips ─────────────────────────────────────

def test_segment_tooltip_names_the_letter_band():
    """A range alone says little; the tooltip must name the band it sits in."""
    import re

    from streamlit_app.lib.band_chart import bands_chart_html  # noqa: PLC0415

    html = bands_chart_html(
        [{"label": "NEXT101", "bands": [(3.7, 4.2), (19.7, 20.2)], "kind": "tx"}],
        marker_ghz=19.70002,
    )
    tips = re.findall(r'title="([^"]+)"', html)
    assert any(t.endswith("· C") for t in tips), tips
    assert any(t.endswith("· Ka") and "20" in t for t in tips), tips
    # The marker is a single frequency, which intersects no interval: it must
    # still be named by the band that contains it.
    assert any(t.startswith("19 700.02 MHz") and t.endswith("· Ka") for t in tips), tips


def test_segment_tooltip_marks_a_straddling_range():
    import re

    from streamlit_app.lib.band_chart import bands_chart_html  # noqa: PLC0415

    html = bands_chart_html([{"label": "wide", "bands": [(10.7, 31.0)], "kind": "tx"}])
    tips = re.findall(r'title="([^"]+)"', html)
    assert any(t.endswith("· Ku/Ka") for t in tips), tips


def test_segment_tooltip_degrades_to_numbers_outside_every_band():
    import re

    from streamlit_app.lib.band_chart import bands_chart_html  # noqa: PLC0415

    html = bands_chart_html([{"label": "vlf", "bands": [(0.001, 0.002)], "kind": "tx"}])
    tips = re.findall(r'title="([^"]+)"', html)
    assert tips and all(" · " not in t for t in tips), tips


def test_explicit_marker_label_is_not_rewritten():
    import re

    from streamlit_app.lib.band_chart import bands_chart_html  # noqa: PLC0415

    html = bands_chart_html(
        [{"label": "x", "bands": [(19.7, 20.2)], "kind": "tx"}],
        marker_ghz=19.9, marker_label="FrequencyRun",
    )
    tips = re.findall(r'title="([^"]+)"', html)
    assert "FrequencyRun" in tips, tips


# ── explicit axis span ──────────────────────────────────────────────────────

def test_clip_bands_trims_and_drops():
    from streamlit_app.lib.band_chart import clip_bands  # noqa: PLC0415

    bands = [(3.7, 4.2), (10.7, 12.75), (19.7, 20.2)]
    assert clip_bands(bands, None) == bands
    assert clip_bands(bands, (10.0, 13.0)) == [(10.7, 12.75)]
    assert clip_bands(bands, (11.0, 12.0)) == [(11.0, 12.0)]       # trimmed
    assert clip_bands(bands, (5.0, 6.0)) == []                      # nothing left
    assert clip_bands(bands, (13.0, 10.0)) == [(10.7, 12.75)]       # reversed span
    # A band touching the edge with zero width disappears rather than render.
    assert clip_bands([(4.2, 5.0)], (3.0, 4.2)) == []


def test_span_pins_the_axis_and_hides_the_rest():
    import re

    from streamlit_app.lib.band_chart import bands_chart_html  # noqa: PLC0415

    rows = [{"label": "sat", "bands": [(3.7, 4.2), (19.7, 20.2)], "kind": "tx"}]
    full = bands_chart_html(rows)
    zoomed = bands_chart_html(rows, span_ghz=(19.7, 20.2))

    def axis(html):
        m = re.search(
            r'so-bc-axis[^>]*>\s*<span>([^<]+)</span>.*?<span>([^<]+)</span>\s*</div>',
            html, re.S)
        return (m.group(1), m.group(2)) if m else None

    # Frequencies are formatted with a narrow no-break space as the thousands
    # separator, so compare against the module's own formatter.
    from streamlit_app.lib.band_chart import _fmt_mhz  # noqa: PLC0415

    assert axis(zoomed) == (_fmt_mhz(19.7), _fmt_mhz(20.2)), axis(zoomed)
    assert axis(full) != axis(zoomed)
    # The C-band assignment is gone from the zoomed chart, tooltip included.
    assert _fmt_mhz(4.2) in full and _fmt_mhz(4.2) not in zoomed
    # One band left, and the count under the strip says so.
    assert "(1 band)" in zoomed and "(2 bands)" in full


def test_span_with_nothing_inside_says_so():
    from streamlit_app.lib.band_chart import bands_chart_html  # noqa: PLC0415

    html = bands_chart_html(
        [{"label": "sat", "bands": [(3.7, 4.2)], "kind": "tx"}],
        span_ghz=(19.7, 20.2),
    )
    assert "No declared band in the selected range." in html
