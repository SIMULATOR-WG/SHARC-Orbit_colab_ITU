"""band_chart.py — ITU-style frequency band occupancy strips.

Reproduces the look of the ITU "Network Structure Navigation" panel: one
gray track per row with green segments where the notice declares bands,
"Lower / Upper" MHz labels and a "(N bands)" count. Two layouts:

* ``shared_scale=False`` (ITU-faithful, single system): each row is scaled to
  its own min–max — Tx and Rx bands far apart both fill their track, exactly
  like the ITU panel.
* ``shared_scale=True`` (aggregate): every row shares one frequency axis so
  cross-system overlap is visible; an optional vertical marker pins the
  selected simulation frequency across all rows.

Pure HTML/CSS rendered via ``st.markdown`` — no plotting dependency. All
segments use a single green (identity lives in the row label, never in a
hue); the "common overlap" row is set apart structurally (ring + bold
label), and the frequency marker is the amber status color. Native
``title`` tooltips carry the exact ranges.
"""
from __future__ import annotations

import html as _html

import streamlit as st

_GREEN = "#5eb37a"          # occupied band segment (single data hue)
_GREEN_SOFT = "rgba(94, 179, 122, 0.45)"  # PFD-fallback fill (labeled as such)
_TRACK = "rgba(124, 141, 176, 0.16)"       # empty track
_MARKER = "#fbbf24"         # pinned simulation frequency (status/warn hue)

_CSS = """
<style>
.so-bandchart { margin: 4px 0 10px; }
.so-bc-title { font-size: var(--so-fs-h3, 16px); font-weight: 600;
               color: var(--so-accent, #4fd1c5); margin: 0 0 6px; }
.so-bc-head { display: flex; justify-content: space-between;
              font-size: var(--so-fs-caption, 12px); font-weight: 600;
              color: var(--so-accent, #4fd1c5); margin: 0 0 2px; }
.so-bc-row { display: flex; align-items: flex-start; gap: 12px;
             margin: 7px 0; }
.so-bc-label { flex: 0 0 230px; font-size: var(--so-fs-small, 13px);
               line-height: 1.25; padding-top: 1px;
               overflow-wrap: anywhere; }
.so-bc-label .so-bc-sub { display: block;
                          font-size: var(--so-fs-caption, 12px);
                          opacity: 0.65; }
.so-bc-body { flex: 1 1 auto; min-width: 0; }
.so-bc-track { position: relative; height: 18px; border-radius: 4px;
               background: %(track)s; overflow: hidden; }
.so-bc-seg { position: absolute; top: 0; bottom: 0; border-radius: 4px;
             background: %(green)s; min-width: 3px; }
.so-bc-seg.pfd { background: %(green_soft)s;
                 outline: 1px dashed %(green)s; outline-offset: -1px; }
.so-bc-row.common .so-bc-track { outline: 1px solid var(--so-accent, #4fd1c5);
                                 outline-offset: 1px; }
.so-bc-row.common .so-bc-label { font-weight: 700; }
.so-bc-marker { position: absolute; top: -2px; bottom: -2px; width: 2px;
                background: %(marker)s; border-radius: 1px; }
.so-bc-under { display: flex; justify-content: space-between;
               font-size: var(--so-fs-caption, 12px);
               color: var(--so-text, #e6eaf2); opacity: 0.8;
               margin-top: 2px; }
.so-bc-axis { display: flex; justify-content: space-between;
              font-size: var(--so-fs-caption, 12px); font-weight: 600;
              margin-top: 4px; }
.so-bc-legend { font-size: var(--so-fs-caption, 12px); opacity: 0.75;
                margin-top: 6px; }
.so-bc-chip { display: inline-block; width: 10px; height: 10px;
              border-radius: 2px; vertical-align: -1px; margin-right: 4px; }
</style>
""" % {"track": _TRACK, "green": _GREEN, "green_soft": _GREEN_SOFT,
       "marker": _MARKER}


def _fmt_mhz(freq_ghz: float) -> str:
    """17.8 GHz → \"17 800 MHz\" (thin-space thousands, ITU-panel style)."""
    mhz = float(freq_ghz) * 1000.0
    txt = f"{mhz:,.0f}" if abs(mhz - round(mhz)) < 5e-3 else f"{mhz:,.2f}"
    return txt.replace(",", " ") + " MHz"


def _seg_html(lo: float, hi: float, x0: float, x1: float,
              kind: str) -> str:
    span = max(x1 - x0, 1e-12)
    left = (lo - x0) / span * 100.0
    width = max((hi - lo) / span * 100.0, 0.15)
    tip = _html.escape(f"{_fmt_mhz(lo)} – {_fmt_mhz(hi)}")
    cls = "so-bc-seg pfd" if kind == "pfd" else "so-bc-seg"
    return (f'<div class="{cls}" style="left:{left:.3f}%;'
            f'width:{width:.3f}%;" title="{tip}"></div>')


def bands_chart_html(
    rows: "list[dict]",
    *,
    title: str | None = None,
    shared_scale: bool = True,
    marker_ghz: float | None = None,
    marker_label: str | None = None,
) -> str:
    """Build the chart HTML.

    ``rows``: ``[{"label": str, "bands": [(lo_ghz, hi_ghz), ...],
    "kind": "tx"|"pfd"|"common", "sublabel": str|None}, ...]``. Bands must be
    disjoint & sorted (use ``art22_ui.merge_intervals``). Empty ``bands`` →
    gray track + "(0 bands)", like the ITU ISL row.
    """
    all_edges = [e for r in rows for b in r.get("bands") or [] for e in b]
    if marker_ghz is not None:
        all_edges.append(float(marker_ghz))
    parts = [_CSS, '<div class="so-bandchart">']
    if title:
        parts.append(f'<div class="so-bc-title">{_html.escape(title)}</div>')
    if not all_edges:
        parts.append('<div class="so-bc-legend">No declared band.</div></div>')
        return "".join(parts)

    gmin, gmax = min(all_edges), max(all_edges)
    pad = max((gmax - gmin) * 0.015, 1e-6)
    gmin, gmax = gmin - pad, gmax + pad

    if not shared_scale:
        parts.append('<div class="so-bc-head" style="margin-left:242px">'
                     '<span>Lower</span><span>Frequency limits</span>'
                     '<span>Upper</span></div>')

    for r in rows:
        bands = list(r.get("bands") or [])
        kind = str(r.get("kind") or "tx")
        row_cls = "so-bc-row common" if kind == "common" else "so-bc-row"
        sub = r.get("sublabel")
        label = _html.escape(str(r.get("label") or ""))
        if sub:
            label += f'<span class="so-bc-sub">{_html.escape(str(sub))}</span>'
        if shared_scale:
            x0, x1 = gmin, gmax
        elif bands:
            lo_r = min(b[0] for b in bands)
            hi_r = max(b[1] for b in bands)
            pad_r = max((hi_r - lo_r) * 0.005, 1e-6)
            x0, x1 = lo_r - pad_r, hi_r + pad_r
        else:
            x0, x1 = gmin, gmax
        segs = "".join(_seg_html(lo, hi, x0, x1, kind) for lo, hi in bands)
        if marker_ghz is not None and x0 <= float(marker_ghz) <= x1:
            mpos = (float(marker_ghz) - x0) / (x1 - x0) * 100.0
            mtip = _html.escape(marker_label or _fmt_mhz(float(marker_ghz)))
            segs += (f'<div class="so-bc-marker" style="left:{mpos:.3f}%;"'
                     f' title="{mtip}"></div>')
        under = ""
        if not shared_scale:
            lo_txt = _fmt_mhz(min(b[0] for b in bands)) if bands else "—"
            hi_txt = _fmt_mhz(max(b[1] for b in bands)) if bands else "—"
            under = (f'<div class="so-bc-under"><span>{lo_txt}</span>'
                     f'<span>({len(bands)} band{"s" if len(bands) != 1 else ""})'
                     f'</span><span>{hi_txt}</span></div>')
        else:
            under = (f'<div class="so-bc-under"><span></span>'
                     f'<span>({len(bands)} band{"s" if len(bands) != 1 else ""})'
                     f'</span><span></span></div>')
        parts.append(
            f'<div class="{row_cls}">'
            f'<div class="so-bc-label">{label}</div>'
            f'<div class="so-bc-body"><div class="so-bc-track">{segs}</div>'
            f'{under}</div></div>'
        )

    if shared_scale:
        mid = ""
        if marker_ghz is not None:
            mid = (f'<span style="color:{_MARKER}">▲ '
                   + _html.escape(marker_label or _fmt_mhz(float(marker_ghz)))
                   + "</span>")
        parts.append(
            f'<div class="so-bc-axis" style="margin-left:242px">'
            f'<span>{_fmt_mhz(gmin)}</span>{mid}<span>{_fmt_mhz(gmax)}</span>'
            f'</div>'
        )

    legend = (f'<span class="so-bc-chip" style="background:{_GREEN}"></span>'
              "declared band")
    if any((r.get("kind") == "pfd") for r in rows):
        legend += (f' &nbsp; <span class="so-bc-chip" '
                   f'style="background:{_GREEN_SOFT};outline:1px dashed '
                   f'{_GREEN}"></span>PFD mask band')
    if marker_ghz is not None:
        legend += (f' &nbsp; <span class="so-bc-chip" '
                   f'style="background:{_MARKER}"></span>simulation frequency')
    parts.append(f'<div class="so-bc-legend">{legend}</div>')
    parts.append("</div>")
    return "".join(parts)


def st_bands_chart(rows: "list[dict]", **kwargs) -> None:
    """Render the occupancy chart into the current Streamlit container."""
    st.markdown(bands_chart_html(rows, **kwargs), unsafe_allow_html=True)
