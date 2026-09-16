"""Frequency letter bands, shared by the filters and the strip charts.

Anatel publishes an ``rf_bands`` label per licensed station; ITU SNS notices
carry no such column. Deriving the band from the frequencies themselves is what
lets one control cover both catalogues, and lets a chart tooltip name the band a
segment sits in. Kept out of ``br_occupancy`` so the chart does not have to
import the catalogue machinery to name a band.
"""
from __future__ import annotations

# Anatel publishes an ``rf_bands`` label per licensed station; ITU SNS notices
# carry no such column, so a filter built on it can only ever see half the
# catalogue. These edges let a band be DERIVED from the frequencies themselves,
# which every row has, so the same control works for both sources.
#
# The edges follow satellite practice rather than IEEE 521 radar letters, which
# is what the Anatel labels themselves follow: C starts at 3.4 GHz (extended C
# downlink) instead of 4.0, and the Ku/Ka split sits at 17.7 GHz (the FSS
# allocation boundary) instead of 18.0. Checked against the published labels:
# on the 27 Anatel stations that declare exactly one letter, the band derived
# here always contains it.
#
# The last entry is open-ended so nothing is silently unclassifiable — the SNS
# catalogue carries science allocations up to 333 GHz.
LETTER_BANDS: tuple[tuple[str, float, float], ...] = (
    ("VHF",   0.03,   0.3),
    ("UHF",   0.3,    1.0),
    ("L",     1.0,    2.0),
    ("S",     2.0,    3.4),
    ("C",     3.4,    8.0),
    ("X",     8.0,   10.7),
    ("Ku",   10.7,   17.7),
    ("Ka",   17.7,   31.0),
    ("Q/V",  31.0,   75.0),
    ("W",    75.0,  110.0),
    (">110", 110.0, float("inf")),
)

LETTER_BAND_NAMES: tuple[str, ...] = tuple(n for n, _lo, _hi in LETTER_BANDS)


def frequency_presets() -> "dict[str, tuple[float, float]]":
    """Named ranges offered to fill the From/To boxes, label → (low, high) GHz.

    Two groups, letter bands first because that is how a band is named in
    conversation: C, Ku, Ka. Their edges are :data:`LETTER_BANDS`, the same
    ones the derived-band filter uses, so picking "Ku" here and picking Ku
    there select the same systems. After them come the Article 22 epfd bands,
    taken from the tables the engine already carries
    (``src/data/article22_limits.json``), so the ranges an examination actually
    runs at are one click away and have a single source of truth. Duplicate
    ranges are collapsed and every entry carries its own range in the label.

    Never raises: if the Article 22 tables cannot be imported, the letter bands
    alone are returned, because this only feeds a convenience control.
    """
    out: "dict[str, tuple[float, float]]" = {}
    by_range: "dict[tuple[float, float], list[str]]" = {}
    try:
        from src.article22_tables import ARTICLE22_TABLES  # noqa: PLC0415

        for ref, table in ARTICLE22_TABLES.items():
            short = str(ref).split("TABLE")[-1].strip() or str(ref)
            service = str(table.get("service") or "")
            for band in table.get("bands") or []:
                rng = band.get("range") or []
                if len(rng) != 2:
                    continue
                key = (float(rng[0]), float(rng[1]))
                tag = f"{short}{' ' + service if service else ''}"
                if tag not in by_range.setdefault(key, []):
                    by_range[key].append(tag)
    except Exception:  # noqa: BLE001 — convenience control, never fatal
        by_range = {}

    for name, lo, hi in LETTER_BANDS:
        if hi == float("inf"):
            continue
        out[f"{name} · {lo:g}–{hi:g} GHz"] = (lo, hi)

    for (lo, hi), tags in sorted(by_range.items()):
        out[f"Art. 22 {' / '.join(tags)} · {lo:g}–{hi:g} GHz"] = (lo, hi)
    return out


def letter_bands_for(intervals: "list[tuple[float, float]]") -> list[str]:
    """Letter bands touched by ``intervals`` (GHz), in ascending frequency.

    Half-open intersection: an interval ending exactly on a band edge does not
    pull in the band above it.
    """
    out: list[str] = []
    for name, lo, hi in LETTER_BANDS:
        for a, b in intervals:
            if float(a) < hi and lo < float(b):
                out.append(name)
                break
    return out


def intervals_touch_range(
    intervals: "list[tuple[float, float]]",
    low_ghz: float | None,
    high_ghz: float | None,
) -> bool:
    """True when any interval overlaps ``[low_ghz, high_ghz]``.

    Either bound may be ``None``, meaning open on that side. An empty interval
    list never matches.
    """
    lo = -float("inf") if low_ghz is None else float(low_ghz)
    hi = float("inf") if high_ghz is None else float(high_ghz)
    if hi < lo:
        lo, hi = hi, lo
    return any(float(a) <= hi and lo <= float(b) for a, b in intervals)




def slider_stops(extra: "list[float] | None" = None) -> list[float]:
    """Frequencies a range slider may stop on, ascending, in GHz.

    A linear slider is useless here: the catalogue runs from 0.03 to 333 GHz, so
    the whole Ku band is under 2% of the track and cannot be grabbed. Instead of
    a continuous axis this offers the edges that actually mean something — every
    letter-band boundary and every Article 22 band edge — plus whatever extra
    values the caller passes (typically the extremes of the current selection).
    Dragging therefore lands on a real boundary, and the text boxes beside it
    stay available for a frequency that is not one.
    """
    stops: set[float] = set()
    for _name, lo, hi in LETTER_BANDS:
        stops.add(float(lo))
        if hi != float("inf"):
            stops.add(float(hi))
    for lo, hi in frequency_presets().values():
        stops.add(float(lo))
        stops.add(float(hi))
    for v in (extra or []):
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f > 0:
            stops.add(round(f, 6))
    return sorted(stops)


def nearest_stop(value: float, stops: "list[float]") -> float:
    """The stop closest to ``value``; ``stops`` must be non-empty."""
    return min(stops, key=lambda s: abs(s - float(value)))
