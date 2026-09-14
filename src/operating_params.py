"""operating_params.py — non-GSO system operating parameters (S.1503-4 §B3.3).

The Recommendation carries the non-GSO operating regime in an XML file, one set
per frequency range, **not** in the SRS ``sat_oper`` table. §B3.3 (printed p. 12)
lists the parameters and states:

    "There could be different sets of parameters at different frequency bands,
     but only one set of operating parameters for any frequency band used by the
     non-GSO system."

and the Attachment to Part B (printed p. 16):

    "If the extended set of system operating parameters are provided, then values
     should be taken from that XML file rather than the relevant SRS table."

The ITU BR confirmed this in writing on 2026-08-11: *"MIN_DURATION is not in
sat_oper — and never was. The sat_oper table carries only nbr_op_sat"*. In the
examination database these sets are stored as zip-compressed XML blobs in the
masks database with ``f_mask='R'``, registered in ``mask_info`` and linked to the
notice through ``mask_lnk3``; there is deliberately no scenario→set link, so an
examination resolves its set by **frequency containment**.

Each parameter has its **own** lookup rule, quoted from §B3.3 (printed p. 12):

=========================  ==========================================================
MIN_EXCLUDE[Latitude]      "derived using linear interpolation between data points";
                           varies by orbit plane via ``orb_id``, ``orb_id=0``
                           applying to all planes
MIN_ELEV[Lat][Azimuth]     "The nearest latitude to that in the table will be used
                           and then linear interpolation in azimuth"
MIN_DURATION[Latitude]     "the nearest latitude to that in the table will be used"
MAX_CO_FREQ[Latitude]      "the nearest latitude to that in the table will be used"
=========================  ==========================================================

Track duration is downlink-only: §D5.2 (printed p. 104) states *"Note that the
minimum track duration is not used for the epfd(up) case."*
"""

from __future__ import annotations

import logging
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np

logger = logging.getLogger(__name__)

# §B5.2 / EPS §6.7.2.2 ranges.
MIN_DURATION_MIN_S = 1
MIN_DURATION_MAX_S = 99_999
MAX_CO_FREQ_MAX = 9_999


class OperatingParamsError(ValueError):
    """Input that §B5.2 or §B3.3 declares invalid."""


@dataclass(frozen=True)
class ValidationIssue:
    severity: str          # 'error' | 'warning' | 'info'
    rule: str              # e.g. 'B5.2/MIN_DURATION>=1'
    message: str
    param_id: int | None = None

    def __str__(self) -> str:  # pragma: no cover - logging aid
        pid = f"[set {self.param_id}] " if self.param_id is not None else ""
        return f"{self.severity.upper()}: {pid}{self.rule}: {self.message}"


def _sorted_pairs(pairs: list[tuple[float, float]]) -> tuple[np.ndarray, np.ndarray]:
    pairs = sorted(pairs, key=lambda p: p[0])
    xs = np.array([p[0] for p in pairs], dtype=np.float64)
    ys = np.array([p[1] for p in pairs], dtype=np.float64)
    return xs, ys


@dataclass(frozen=True)
class LatTable:
    """A parameter tabulated by latitude."""

    lats: np.ndarray
    vals: np.ndarray

    def midpoints(self) -> np.ndarray:
        """Split latitudes between consecutive points — the Voronoi edges."""
        if self.lats.size < 2:
            return np.empty(0, dtype=np.float64)
        return 0.5 * (self.lats[:-1] + self.lats[1:])

    def nearest(self, lat_deg: float) -> float:
        """"The nearest latitude to that in the table will be used."

        Implemented by searching the **same midpoint array** the engine bridge
        uses to build its bands (:func:`nearest_table_to_bands`), so the two can
        never disagree — not even when a midpoint is not exactly representable
        in binary floating point, which an ``argmin`` formulation gets wrong.
        Ties resolve to the lower latitude — ``side="left"`` leaves a value
        landing exactly on a midpoint in the cell below it, which is also what
        the engine's first-match band lookup does — matching the
        nearest-latitude convention already used for pfd masks
        (:mod:`src.pfd_mask`).
        """
        i = int(np.searchsorted(self.midpoints(), float(lat_deg), side="left"))
        return float(self.vals[min(i, self.vals.size - 1)])

    def linear(self, lat_deg: float) -> float:
        """"derived using linear interpolation between data points".

        Clamped outside the tabulated range: a single declared point therefore
        applies at every latitude, which is what the BR's own NEXT101 case
        relies on (all its arrays are single-point at latitude 0).
        """
        if self.lats.size == 1:
            return float(self.vals[0])
        return float(np.interp(float(lat_deg), self.lats, self.vals))

    @property
    def span(self) -> tuple[float, float]:
        return float(self.lats[0]), float(self.lats[-1])


@dataclass(frozen=True)
class MinElevTable:
    """ES_MINELEV[Latitude][Azimuth]: nearest in latitude, linear in azimuth."""

    lats: np.ndarray
    az: tuple[np.ndarray, ...]
    vals: tuple[np.ndarray, ...]

    def _lat_index(self, lat_deg: float) -> int:
        """Nearest tabulated latitude, ties to the lower one (as LatTable)."""
        if self.lats.size == 1:
            return 0
        mids = 0.5 * (self.lats[:-1] + self.lats[1:])
        return int(np.searchsorted(mids, float(lat_deg), side="right"))

    def value(self, lat_deg: float, azimuth_deg: float) -> float:
        """"The nearest latitude ... will be used and then linear interpolation
        in azimuth" (§B3.3, printed p. 12).

        Azimuth is interpolated **in the coordinate the filing declared**, which
        the Recommendation's own example (printed p. 14) allows to run past 360:

            <min_elev a="-30">
              <elev_angle b="0">30</elev_angle>   <elev_angle b="90">40</elev_angle>
              <elev_angle b="280">30</elev_angle> <elev_angle b="370">40</elev_angle>
            </min_elev>

        There ``b=370`` is a genuine point one turn on from 10°, not a duplicate
        of 0°, so folding the axis with ``numpy.interp(..., period=360)`` — which
        reduces every abscissa modulo 360 and re-sorts — destroys the 280→370
        segment and returns the wrong ε₀ (30.6° instead of 37.8° at azimuth 350).
        Instead: keep the declared abscissa, close the loop only when the table
        does not already span a full turn, and map the query into the turn that
        starts at the first declared azimuth.
        """
        i = self._lat_index(lat_deg)
        a, v = self.az[i], self.vals[i]
        if a.size == 1:
            return float(v[0])
        a0 = float(a[0])
        if float(a[-1]) - a0 < 360.0:
            # Table covers less than a full turn: close the circle explicitly so
            # an azimuth in the untabulated arc interpolates back to the first
            # point instead of clamping at the last one.
            a = np.append(a, a0 + 360.0)
            v = np.append(v, v[0])
        q = a0 + ((float(azimuth_deg) - a0) % 360.0)
        return float(np.interp(q, a, v))

    def max_over_azimuth(self, lat_deg: float) -> float:
        """Strictest ε₀ at a latitude.

        The WCG search evaluates eligibility before any satellite azimuth
        exists, so it needs one scalar; taking the maximum is the conservative
        choice (it admits the fewest satellites).
        """
        return float(self.vals[self._lat_index(lat_deg)].max())


@dataclass(frozen=True)
class OperatingParameterSet:
    """One ``<non_gso_operating_parameters>`` block: the regime for one band."""

    param_id: int
    ntc_id: str
    sat_name: str
    low_freq_mhz: float
    high_freq_mhz: float
    es_lat_min_deg: float = -90.0
    es_lat_max_deg: float = 90.0
    es_density_per_km2: float | None = None
    es_distance_km: float | None = None
    min_angle_at_es_deg: float = 0.0     # §B3.3 "Assumed to be zero if not provided"
    min_angle_at_sat_deg: float = 0.0    # idem
    max_co_freq_sat: int | None = None   # §B3.3: absent ⇒ no cap
    min_exclude: dict[int, LatTable] = field(default_factory=dict)
    max_co_freq: LatTable | None = None
    min_duration: LatTable | None = None
    min_elev: MinElevTable | None = None
    source: str = "<string>"
    issues: tuple[ValidationIssue, ...] = ()

    # ── §B3.3 lookups ────────────────────────────────────────────────────────

    def alpha0_deg(self, lat_deg: float, orb_id: int = 0) -> float | None:
        """MIN_EXCLUDE by latitude (linear) for one orbit plane.

        ``orb_id=0`` in the data means "applies to all orbit planes"; a plane's
        own table takes precedence over the wildcard when both are declared.
        """
        tbl = self.min_exclude.get(int(orb_id)) or self.min_exclude.get(0)
        return None if tbl is None else tbl.linear(lat_deg)

    def eps0_deg(self, lat_deg: float, azimuth_deg: float) -> float | None:
        return None if self.min_elev is None else self.min_elev.value(lat_deg, azimuth_deg)

    def eps0_scalar_deg(self, lat_deg: float) -> float | None:
        return None if self.min_elev is None else self.min_elev.max_over_azimuth(lat_deg)

    def max_co_freq_at(self, lat_deg: float) -> int | None:
        """MAX_CO_FREQ by nearest latitude; ``None`` when the array is absent."""
        if self.max_co_freq is None:
            return None
        return int(round(self.max_co_freq.nearest(lat_deg)))

    def min_duration_at(self, lat_deg: float) -> float:
        """MIN_DURATION by nearest latitude; ``0.0`` when the array is absent.

        An absent array — not a zero — selects the classic §D5.1.4.1 algorithm:
        §B5.2 requires ``MIN_DURATION[Latitude] >= 1 second`` wherever declared,
        so zero is never written.
        """
        if self.min_duration is None:
            return 0.0
        return float(self.min_duration.nearest(lat_deg))

    @property
    def uses_track_duration(self) -> bool:
        """§D5.1.4: the variant applies when MIN_DURATION is non-zero."""
        return self.min_duration is not None and bool((self.min_duration.vals != 0).any())

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(i for i in self.issues if i.severity == "error")

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)

    def raise_on_errors(self) -> None:
        if self.errors:
            raise OperatingParamsError(
                f"operating-parameter set {self.param_id} ({self.band_label()}) "
                "is invalid: " + "; ".join(str(e) for e in self.errors))

    def covers(self, freq_mhz: float) -> bool:
        """Half-open [low, high).

        §B3.3 allows "only one set of operating parameters for any frequency
        band", and real filings butt adjacent bands at a shared edge (17.8–18.6
        then 18.6–19.3). A closed interval would make the shared edge belong to
        both sets and turn a conformant filing into a resolution error.
        """
        f = float(freq_mhz)
        return self.low_freq_mhz <= f < self.high_freq_mhz

    def contains_band(self, low_mhz: float, high_mhz: float) -> bool:
        return self.low_freq_mhz <= float(low_mhz) and float(high_mhz) <= self.high_freq_mhz

    def intersects_band(self, low_mhz: float, high_mhz: float) -> bool:
        """True interval intersection — not a three-point probe."""
        return self.low_freq_mhz < float(high_mhz) and float(low_mhz) < self.high_freq_mhz

    def band_label(self) -> str:
        return f"{self.low_freq_mhz:.0f}–{self.high_freq_mhz:.0f} MHz"


# ── parsing ─────────────────────────────────────────────────────────────────

_UNPARSABLE = object()


def _attr_float(el, name, default=None, *, issues=None, pid=None):
    """Header attribute as float.

    Absent and unparsable are different failures: an absent attribute takes the
    Recommendation's documented default, while a present-but-unparsable one is a
    data error and must not be silently replaced by a permissive default
    (``es_lat_min="-9O"`` would otherwise widen the ES latitude range to its
    maximum). Unparsable values record an error issue and still return the
    default so parsing can continue and report everything at once.
    """
    raw = el.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(str(raw).strip())
    except ValueError:
        if issues is not None:
            issues.append(ValidationIssue(
                "error", f"B3.3/{name}",
                f"attribute {name}={raw!r} is not a number", pid))
        return default


def _attr_int(el, name, default=None, *, issues=None, pid=None):
    v = _attr_float(el, name, None, issues=issues, pid=pid)
    return default if v is None else int(round(v))


def _dedup_lats(pairs, tag, issues, pid):
    """Collapse duplicate latitudes (last wins) and record it.

    Duplicates would otherwise give the nearest-latitude lookup and the engine's
    band transport two different answers for the same latitude.
    """
    seen: dict[float, float] = {}
    for a, v in pairs:
        if a in seen and seen[a] != v:
            issues.append(ValidationIssue(
                "warning", f"B3.3/{tag}",
                f"latitude {a:g} declared twice ({seen[a]:g} then {v:g}); "
                "the last value is used", pid))
        seen[a] = v
    return [(a, seen[a]) for a in sorted(seen)]


def _parse_lat_array(parent, tag, issues=None, pid=None) -> LatTable | None:
    """Parse a latitude-indexed array, reporting every entry it cannot use.

    Silently dropping a malformed entry is how a declared MIN_DURATION array
    becomes ``None`` and the run quietly falls back to the classic §D5.1.4.1
    algorithm, so every drop is recorded and an element that yields no usable
    point at all is an error rather than an absent array.
    """
    issues = issues if issues is not None else []
    els = parent.findall(tag)
    if not els:
        return None
    pairs = []
    for el in els:
        a = _attr_float(el, "a", None, issues=issues, pid=pid)
        if a is None:
            issues.append(ValidationIssue(
                "error", f"B3.3/{tag}",
                f"<{tag}> entry without a usable latitude attribute "
                f"(attributes: {dict(el.attrib) or 'none'})", pid))
            continue
        txt = (el.text or "").strip()
        if txt == "":
            issues.append(ValidationIssue(
                "error", f"B3.3/{tag}", f"<{tag} a=\"{a:g}\"> has no value", pid))
            continue
        try:
            pairs.append((a, float(txt)))
        except ValueError:
            issues.append(ValidationIssue(
                "error", f"B3.3/{tag}",
                f"<{tag} a=\"{a:g}\"> value {txt!r} is not a number", pid))
    if not pairs:
        issues.append(ValidationIssue(
            "error", f"B3.3/{tag}",
            f"<{tag}> is present but yielded no usable data point", pid))
        return None
    xs, ys = _sorted_pairs(_dedup_lats(pairs, tag, issues, pid))
    return LatTable(xs, ys)


def _parse_min_exclude(parent, issues=None, pid=None) -> dict[int, LatTable]:
    """MIN_EXCLUDE by orbit plane.

    §B3.3 (printed p. 12): "This field could vary between non-GSO system orbit
    planes via the orb_id field. If the orb_id field equals 0 then the data
    exclusion zone data applies to all orbit planes."

    A block whose plane attribute is missing or unreadable must NOT silently
    default to 0: that installs one plane's table as the all-planes wildcard.
    The Recommendation's own example (printed p. 14) writes ``oc="2"``, which is
    exactly the typo this guards against.
    """
    issues = issues if issues is not None else []
    out: dict[int, LatTable] = {}
    for blk in parent.findall("min_exclude"):
        if "c" not in blk.attrib:
            issues.append(ValidationIssue(
                "error", "B3.3/min_exclude",
                "<min_exclude> without a 'c' (orb_id) attribute "
                f"(attributes: {dict(blk.attrib) or 'none'}); 0 means all planes "
                "and must be written explicitly", pid))
            continue
        orb = _attr_int(blk, "c", None, issues=issues, pid=pid)
        if orb is None:
            continue
        tbl = _parse_lat_array(blk, "exclusion_zone_angle", issues, pid)
        if tbl is None:
            continue
        if int(orb) in out:
            issues.append(ValidationIssue(
                "warning", "B3.3/min_exclude",
                f"orb_id {orb} declared twice; the last block is used", pid))
        out[int(orb)] = tbl
    return out


def _parse_min_elev(parent, issues=None, pid=None) -> MinElevTable | None:
    issues = issues if issues is not None else []
    lats, azs, vals = [], [], []
    for blk in parent.findall("min_elev"):
        a = _attr_float(blk, "a", None, issues=issues, pid=pid)
        if a is None:
            continue
        pairs = []
        for el in blk.findall("elev_angle"):
            b = _attr_float(el, "b", None, issues=issues, pid=pid)
            txt = (el.text or "").strip()
            if b is None or txt == "":
                issues.append(ValidationIssue(
                    "error", "B3.3/min_elev",
                    f"<elev_angle> entry unusable at latitude {a:g} "
                    f"(attributes: {dict(el.attrib) or 'none'})", pid))
                continue
            try:
                pairs.append((b, float(txt)))
            except ValueError:
                issues.append(ValidationIssue(
                    "error", "B3.3/min_elev",
                    f"<elev_angle b=\"{b:g}\"> value {txt!r} is not a number", pid))
        if not pairs:
            issues.append(ValidationIssue(
                "error", "B3.3/min_elev",
                f"<min_elev a=\"{a:g}\"> yielded no usable elevation", pid))
            continue
        xs, ys = _sorted_pairs(pairs)
        lats.append(a)
        azs.append(xs)
        vals.append(ys)
    if not lats:
        return None
    order = np.argsort(np.array(lats, dtype=np.float64))
    return MinElevTable(
        np.array(lats, dtype=np.float64)[order],
        tuple(azs[i] for i in order),
        tuple(vals[i] for i in order),
    )


def parse_operating_params_xml(
    xml_text: str | bytes,
    *,
    source: str = "<string>",
    strict: bool = False,
) -> list[OperatingParameterSet]:
    """Parse one or more ``<non_gso_operating_parameters>`` blocks.

    Accepts a ``<satellite_system>`` wrapper (the form the BR ships) or a bare
    block. EPS §6.7.2.1 prints ``max_co_freq``/``min_duration``/``min_angle_*``
    as *header attributes* while §6.7.2.4/6.7.2.5 and every real file carry them
    as latitude-indexed *child elements*; both are accepted, and where both
    appear the array wins (it carries strictly more information).

    ``strict`` promotes every validation warning to an error.
    """
    if isinstance(xml_text, bytes):
        xml_text = xml_text.decode("utf-8", errors="replace")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise OperatingParamsError(f"{source}: malformed XML ({exc})") from exc

    if root.tag == "non_gso_operating_parameters":
        blocks, sat_name, ntc_id = [root], "", ""
    else:
        blocks = root.findall(".//non_gso_operating_parameters")
        sat_name = (root.get("sat_name") or "").strip()
        ntc_id = (root.get("ntc_id") or "").strip()
    if not blocks:
        raise OperatingParamsError(
            f"{source}: no <non_gso_operating_parameters> element found."
        )

    sets: list[OperatingParameterSet] = []
    for blk in blocks:
        issues: list[ValidationIssue] = []
        pid = _attr_int(blk, "param_id", -1)
        lo = _attr_float(blk, "low_freq_mhz")
        hi = _attr_float(blk, "high_freq_mhz")
        if lo is None or hi is None:
            raise OperatingParamsError(
                f"{source}: set {pid} has no low_freq_mhz/high_freq_mhz; "
                "§B3.3 keys every set on Freq_Min/Freq_Max."
            )
        if hi <= lo:
            raise OperatingParamsError(
                f"{source}: set {pid} has high_freq_mhz ({hi}) <= low_freq_mhz ({lo})."
            )

        min_dur = _parse_lat_array(blk, "min_duration", issues, pid)
        max_cof = _parse_lat_array(blk, "max_co_freq", issues, pid)
        # Header-attribute fallback (EPS §6.7.2.1): a scalar is a single-point
        # array at latitude 0, which the nearest-latitude rule spreads over the
        # whole range.
        if min_dur is None and _attr_float(blk, "min_duration") is not None:
            min_dur = LatTable(np.array([0.0]), np.array([_attr_float(blk, "min_duration")]))
            issues.append(ValidationIssue(
                "info", "B3.3/min_duration",
                "taken from the header attribute (EPS §6.7.2.1 form)", pid))
        elif min_dur is not None and _attr_float(blk, "min_duration") is not None:
            issues.append(ValidationIssue(
                "warning", "B3.3/min_duration",
                "declared both as header attribute and as array; the array wins", pid))
        if max_cof is None and _attr_float(blk, "max_co_freq") is not None:
            max_cof = LatTable(np.array([0.0]), np.array([_attr_float(blk, "max_co_freq")]))

        s = OperatingParameterSet(
            param_id=pid,
            ntc_id=(blk.get("ntc_id") or ntc_id or "").strip(),
            sat_name=sat_name,
            low_freq_mhz=lo,
            high_freq_mhz=hi,
            es_lat_min_deg=_attr_float(blk, "es_lat_min", -90.0, issues=issues, pid=pid),
            es_lat_max_deg=_attr_float(blk, "es_lat_max", 90.0, issues=issues, pid=pid),
            es_density_per_km2=_attr_float(blk, "es_density", issues=issues, pid=pid),
            es_distance_km=_attr_float(blk, "es_distance", issues=issues, pid=pid),
            # §B3.3: "Assumed to be zero if not provided". The Recommendation's
            # own example header misspells this as ``angle_at_es``.
            min_angle_at_es_deg=(
                _attr_float(blk, "min_angle_at_es")
                if _attr_float(blk, "min_angle_at_es") is not None
                else _attr_float(blk, "angle_at_es", 0.0)
            ),
            min_angle_at_sat_deg=_attr_float(blk, "min_angle_at_sat", 0.0, issues=issues, pid=pid),
            max_co_freq_sat=_attr_int(blk, "max_co_freq_sat", None, issues=issues, pid=pid),
            min_exclude=_parse_min_exclude(blk, issues, pid),
            max_co_freq=max_cof,
            min_duration=min_dur,
            min_elev=_parse_min_elev(blk, issues, pid),
            source=source,
            issues=(),
        )
        issues.extend(validate_set(s))
        if strict:
            errs = [i for i in issues if i.severity in ("error", "warning")]
            if errs:
                raise OperatingParamsError(f"{source}: " + "; ".join(str(e) for e in errs))
        # Otherwise the set is kept WITH its issues. A §B5.2 violation is scoped
        # to the offending set: aborting the whole document would drop the other
        # sets of the same notice — including the one actually being examined —
        # and silently revert the run to the classic §D5.1.4.1 algorithm.
        sets.append(OperatingParameterSet(**{**s.__dict__, "issues": tuple(issues)}))
    return sets


def validate_set(
    s: OperatingParameterSet,
    *,
    orbit_plane_ids: "Iterable[int] | None" = None,
) -> list[ValidationIssue]:
    """§B5.2 "Non-GSO system operating parameters ranges" plus EPS §6.7.2.2.

    Errors are values the Recommendation declares invalid; warnings are values
    that are only needed for a run type this examination may not be doing (the
    ES population is used by epfd(up) only) or that the BR's own test case
    exercises (single-point latitude arrays).

    ``orbit_plane_ids`` are the orb_id values the filing declares (SRS
    ``orbit_set``/``orbit`` tables). When given, the §B5.3 completeness rule is
    enforced: *"if the MIN_EXCLUDE varies by orbit plane, that a value is
    defined for each orbit plane"*. Without it that check cannot run — the set
    alone does not know how many planes the constellation has — so it is
    skipped rather than guessed.
    """
    out: list[ValidationIssue] = []
    pid = s.param_id

    if s.min_duration is not None:
        v = s.min_duration.vals
        # A declared 0 is "no tracking duration at this latitude", which §D5.1.4
        # reads as the classic algorithm there; it is dropped at :meth:`tracked_
        # latitudes` rather than rejected. Only a value strictly inside (0, 1) is
        # the §B5.2 violation.
        bad = v[(v > 0.0) & (v < MIN_DURATION_MIN_S)]
        if bad.size:
            out.append(ValidationIssue(
                "error", "B5.2/MIN_DURATION>=1s",
                f"values {sorted(set(bad.tolist()))} in (0, 1) s; §B5.2 requires "
                "MIN_DURATION >= 1 second where it is declared", pid))
        if (v < 0.0).any():
            out.append(ValidationIssue(
                "error", "B5.2/MIN_DURATION>=1s", "negative MIN_DURATION", pid))
        big = s.min_duration.vals[s.min_duration.vals > MIN_DURATION_MAX_S]
        if big.size:
            out.append(ValidationIssue(
                "error", "EPS6.7.2.2/min_duration<=99999",
                f"values {sorted(set(big.tolist()))} above 99 999 s", pid))

    if s.max_co_freq is not None:
        if (s.max_co_freq.vals < 0).any():
            out.append(ValidationIssue(
                "error", "B5.2/MAX_CO_FREQ>=0", "negative value", pid))
        if (s.max_co_freq.vals > MAX_CO_FREQ_MAX).any():
            out.append(ValidationIssue(
                "error", "EPS6.7.2.2/max_co_freq<=9999", "value above 9 999", pid))

    for orb, tbl in s.min_exclude.items():
        if (tbl.vals < 0).any():
            out.append(ValidationIssue(
                "error", "B5.2/MIN_EXCLUDE>=0",
                f"negative exclusion-zone angle for orb_id={orb}", pid))

    if s.min_elev is not None:
        for v in s.min_elev.vals:
            if (v < 0).any() or (v > 90).any():
                out.append(ValidationIssue(
                    "error", "B5.2/MIN_ELEV>=0",
                    "elevation outside [0, 90]", pid))
                break
        for a in s.min_elev.az:
            if (a < 0).any() or (a > 360).any():
                out.append(ValidationIssue(
                    "warning", "EPS6.8.3.2/azimuth[0,360]",
                    "azimuth outside [0, 360]; normalised modulo 360", pid))
                break

    if s.min_angle_at_es_deg is not None and s.min_angle_at_es_deg < 0:
        out.append(ValidationIssue("error", "B5.2/MIN_ANGLE_AT_ES>=0", "negative", pid))
    if s.min_angle_at_sat_deg is not None and s.min_angle_at_sat_deg < 0:
        out.append(ValidationIssue("error", "B5.2/MIN_ANGLE_AT_SAT>=0", "negative", pid))
    if s.max_co_freq_sat is not None and s.max_co_freq_sat < 0:
        out.append(ValidationIssue("error", "B5.2/MAX_CO_FREQ_SAT>=0", "negative", pid))

    if not (-90.0 <= s.es_lat_min_deg < 90.0):
        out.append(ValidationIssue("error", "B5.2/ES_LAT_MIN", "outside [−90, +90)", pid))
    if not (-90.0 < s.es_lat_max_deg <= 90.0):
        out.append(ValidationIssue("error", "B5.2/ES_LAT_MAX", "outside (−90, +90]", pid))
    if s.es_lat_max_deg <= s.es_lat_min_deg:
        out.append(ValidationIssue(
            "error", "B5.2/ES_LAT_MAX>ES_LAT_MIN",
            f"{s.es_lat_max_deg} <= {s.es_lat_min_deg}", pid))

    # ES population is an epfd(up) input; a downlink-only examination does not
    # need it, so its absence is a warning here and an error only at the point
    # of use (see ``require_uplink_population``).
    if s.es_density_per_km2 is not None and s.es_density_per_km2 <= 0:
        out.append(ValidationIssue("error", "B5.2/ES_DENSITY>0", "not > 0", pid))
    if s.es_density_per_km2 is None:
        out.append(ValidationIssue(
            "warning", "B5.2/ES_DENSITY>0", "absent; required for epfd(up) only", pid))
    if s.es_distance_km is not None and s.es_distance_km < 0:
        out.append(ValidationIssue("error", "B5.2/ES_DISTANCE>=0", "negative", pid))

    # §B5.3 (printed p. 17): "That if the MIN_EXCLUDE varies by orbit plane,
    # that a value is defined for each orbit plane." orb_id 0 is the all-planes
    # wildcard, so a set that declares only 0 does not vary by plane and needs
    # nothing else; one that declares any non-zero plane must cover them all
    # (the wildcard does NOT fill the gaps — ``alpha0_deg`` prefers the plane's
    # own table, and a plane with no table would silently inherit a value the
    # filing never stated for it).
    _planes = {int(k) for k in s.min_exclude}
    _varies_by_plane = bool(_planes - {0})
    if _varies_by_plane:
        if orbit_plane_ids is not None:
            declared = {int(p) for p in orbit_plane_ids}
            missing = sorted(declared - _planes)
            if missing:
                out.append(ValidationIssue(
                    "error", "B5.3/MIN_EXCLUDE-per-plane",
                    f"MIN_EXCLUDE varies by orbit plane but planes {missing} "
                    f"have no exclusion-zone table (declared: "
                    f"{sorted(_planes)})", pid))
            extra = sorted(_planes - declared - {0})
            if extra:
                out.append(ValidationIssue(
                    "warning", "B5.3/MIN_EXCLUDE-per-plane",
                    f"MIN_EXCLUDE declared for orb_id {extra}, which the filing "
                    "does not list as orbit planes", pid))
        elif 0 in _planes:
            out.append(ValidationIssue(
                "warning", "B5.3/MIN_EXCLUDE-per-plane",
                f"MIN_EXCLUDE mixes the all-planes wildcard (orb_id 0) with "
                f"per-plane tables {sorted(_planes - {0})}; planes without a "
                "table of their own fall back to the wildcard", pid))
        else:
            out.append(ValidationIssue(
                "info", "B5.3/MIN_EXCLUDE-per-plane",
                f"MIN_EXCLUDE varies by orbit plane ({sorted(_planes)}); "
                "completeness against the filing's plane list was not checked "
                "(no orbit_plane_ids supplied)", pid))

    # §D5.2 (printed p. 104): "the minimum track duration is not used for the
    # epfd(up) case". A set covering only uplink bands with a declared
    # MIN_DURATION is not an error, but it is inert and worth flagging.
    if s.uses_track_duration and s.min_duration is not None:
        span = s.min_duration.span
        if span[0] == span[1]:
            out.append(ValidationIssue(
                "info", "B3.3/MIN_DURATION",
                f"single-point latitude array at {span[0]:g}°; the "
                "nearest-latitude rule applies it at every ES latitude", pid))
    return out


def require_uplink_population(s: OperatingParameterSet) -> None:
    """Raise when a set lacks the ES population an epfd(up) run needs."""
    missing = [n for n, v in (("ES_DENSITY", s.es_density_per_km2),
                              ("ES_DISTANCE", s.es_distance_km)) if v is None]
    if missing:
        raise OperatingParamsError(
            f"set {s.param_id} ({s.band_label()}) lacks {', '.join(missing)}, "
            "required for an epfd(up) run (§B5.2)."
        )


# ── registry ────────────────────────────────────────────────────────────────

class OperatingParameterRegistry:
    """All operating-parameter sets of one notice, resolved by frequency.

    §B3.3: *"There could be different sets of parameters at different frequency
    bands, but only one set of operating parameters for any frequency band used
    by the non-GSO system."* That is enforced here: overlapping set ranges are
    rejected, and a band must fall inside exactly one set.
    """

    def __init__(self, sets: list[OperatingParameterSet]):
        self.sets = tuple(sorted(sets, key=lambda s: (s.low_freq_mhz, s.param_id)))
        self.issues = tuple(self._check_overlap())

    def __len__(self) -> int:
        return len(self.sets)

    def __iter__(self):
        return iter(self.sets)

    def _check_overlap(self) -> list[ValidationIssue]:
        out: list[ValidationIssue] = []
        for a, b in zip(self.sets, self.sets[1:]):
            if b.low_freq_mhz < a.high_freq_mhz:  # half-open: a shared edge is legal
                out.append(ValidationIssue(
                    "error", "B3.3/one-set-per-band",
                    f"sets {a.param_id} ({a.band_label()}) and {b.param_id} "
                    f"({b.band_label()}) overlap", None))
        return out

    def raise_on_errors(self) -> None:
        """Structural errors of the registry itself (overlapping set ranges).

        Per-set §B5.2 errors are raised by :meth:`OperatingParameterSet.raise_on_errors`
        only for the set actually resolved, so one invalid set cannot sink a run
        that examines a different band.
        """
        errs = [i for i in self.issues if i.severity == "error"]
        if errs:
            raise OperatingParamsError("; ".join(str(e) for e in errs))

    @property
    def invalid_sets(self) -> list[OperatingParameterSet]:
        return [s for s in self.sets if s.has_errors]

    def for_band(
        self, low_mhz: float, high_mhz: float,
    ) -> OperatingParameterSet | None:
        """The set whose range contains the examined band.

        Returns ``None`` only when **no** set touches the band — the caller may
        then fall back to the legacy SRS columns. A band that overlaps a set
        without being contained in it is a data error and is raised, using a true
        interval intersection rather than sampling three points (a set narrower
        than half the band would slip through a three-point probe).

        Errors are raised only for the set actually resolved: an invalid set in
        a band nobody is examining must not sink the run.
        """
        self.raise_on_errors()
        hits = [s for s in self.sets if s.contains_band(low_mhz, high_mhz)]
        if len(hits) > 1:
            raise OperatingParamsError(
                f"band {low_mhz:.0f}–{high_mhz:.0f} MHz falls in "
                f"{len(hits)} operating-parameter sets "
                f"({', '.join(str(s.param_id) for s in hits)}); §B3.3 allows one."
            )
        if hits:
            hits[0].raise_on_errors()
            return hits[0]
        touching = [s for s in self.sets if s.intersects_band(low_mhz, high_mhz)]
        if touching:
            raise OperatingParamsError(
                f"band {low_mhz:.0f}–{high_mhz:.0f} MHz is not contained in any "
                f"single operating-parameter set; it overlaps "
                f"{', '.join(f'{s.param_id} ({s.band_label()})' for s in touching)}."
            )
        return None

    def for_frequency(self, freq_mhz: float) -> OperatingParameterSet | None:
        """The set covering one frequency (half-open, so shared edges are fine)."""
        self.raise_on_errors()
        hits = [s for s in self.sets if s.covers(freq_mhz)]
        if not hits:
            # The top edge of the highest set is closed, so a run exactly at it
            # still resolves rather than falling off the end.
            hits = [s for s in self.sets if s.high_freq_mhz == float(freq_mhz)]
        if len(hits) > 1:
            raise OperatingParamsError(
                f"{freq_mhz:.3f} MHz falls in {len(hits)} operating-parameter sets."
            )
        if hits:
            hits[0].raise_on_errors()
            return hits[0]
        return None

    def summary(self) -> list[dict]:
        return [{
            "param_id": s.param_id,
            "band_mhz": [s.low_freq_mhz, s.high_freq_mhz],
            "min_duration_s": (None if s.min_duration is None
                               else float(s.min_duration.vals.max())),
            "track_duration": s.uses_track_duration,
            "max_co_freq": (None if s.max_co_freq is None
                            else int(s.max_co_freq.vals.max())),
            "min_exclude_deg": (None if not s.min_exclude
                                else float(next(iter(s.min_exclude.values())).vals.max())),
            "min_elev_deg": (None if s.min_elev is None
                             else float(max(v.max() for v in s.min_elev.vals))),
            "min_angle_at_es_deg": s.min_angle_at_es_deg,
            "source": s.source,
            "n_issues": len(s.issues),
        } for s in self.sets]


# ── loaders ─────────────────────────────────────────────────────────────────

def load_from_paths(paths, *, strict: bool = False) -> OperatingParameterRegistry:
    """Read operating-parameter sets from loose ``.xml`` files."""
    sets: list[OperatingParameterSet] = []
    for p in paths:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            sets.extend(parse_operating_params_xml(fh.read(), source=str(p), strict=strict))
    return OperatingParameterRegistry(sets)


def load_from_texts(items, *, strict: bool = False) -> OperatingParameterRegistry:
    """Read from ``(source_label, xml_text)`` pairs — the UI upload path."""
    sets: list[OperatingParameterSet] = []
    for label, text in items:
        sets.extend(parse_operating_params_xml(text, source=str(label), strict=strict))
    return OperatingParameterRegistry(sets)


def load_from_mask_mdb(
    mask_mdb_path: str,
    ntc_id: str | None = None,
    *,
    strict: bool = False,
) -> OperatingParameterRegistry:
    """Read the ``f_mask='R'`` sets from the masks database.

    This is where the examination database keeps them: zip-compressed XML blobs
    in the ``masks`` table, registered in ``mask_info`` and linked to the notice
    through ``mask_lnk3``.
    """
    from .srs_reader import read_mask_xml_from_mdb, list_masks_from_mask_mdb

    rows = list_masks_from_mask_mdb(mask_mdb_path, ntc_id=ntc_id, f_mask="R")
    sets: list[OperatingParameterSet] = []
    for row in rows:
        mid = int(row["mask_id"])
        xml = read_mask_xml_from_mdb(
            mask_mdb_path, mid, ntc_id=ntc_id,
            required_root="<non_gso_operating_parameters",
            f_mask="R", what="operating-parameters mask",
        )
        sets.extend(parse_operating_params_xml(
            xml, source=f"{mask_mdb_path}#mask_id={mid}", strict=strict))
    if sets:
        logger.info(
            "  Operating-parameter masks (f_mask='R'): %d set(s) — %s",
            len(sets),
            ", ".join(f"{s.param_id} [{s.band_label()}]"
                      f"{' MIN_DURATION=' + format(s.min_duration.vals.max(), '.0f') + 's' if s.uses_track_duration else ''}"
                      for s in sets),
        )
    return OperatingParameterRegistry(sets)


# ── engine bridge ───────────────────────────────────────────────────────────

def nearest_table_to_bands(tbl: LatTable) -> list[tuple[float, float, float]]:
    """Convert a nearest-latitude point table to the engine's band transport.

    The engine carries per-latitude parameters as ``(lat_fr, lat_to, value)``
    bands resolved by containment, while §B3.3 tabulates MIN_DURATION and
    MAX_CO_FREQ at *points* resolved by nearest latitude. The two agree exactly
    when the bands are the Voronoi cells of the points — split at the midpoints
    between consecutive latitudes — so this conversion is lossless rather than
    an approximation.

    Band edges are inclusive on both sides and the engine returns the first
    match, so a latitude exactly on a midpoint resolves to the lower point,
    which is the tie rule of :meth:`LatTable.nearest`.
    """
    vals = tbl.vals
    if tbl.lats.size == 1:
        return [(-90.0, 90.0, float(vals[0]))]
    mids = tbl.midpoints()
    edges = [-90.0] + [float(m) for m in mids] + [90.0]
    return [(edges[i], edges[i + 1], float(vals[i])) for i in range(vals.size)]


def to_engine_config(
    s: OperatingParameterSet,
    *,
    direction: str = "down",
    es_lat_deg: float | None = None,
) -> dict:
    """Config fragment the EPFD engine consumes for one examined band.

    The Attachment to Part B (printed p. 16) is explicit about precedence: *"If
    the extended set of system operating parameters are provided, then values
    should be taken from that XML file rather than the relevant SRS table."* So
    every parameter the set declares is emitted here, not just the two the
    engine happened to already read from ``sat_oper``.

    ``direction`` is ``'down'`` or ``'up'``. Track duration is **downlink only**:
    §D5.2 (printed p. 104) states *"Note that the minimum track duration is not
    used for the epfd(up) case."* — so ``min_duration_by_lat`` is emitted only
    for a downlink examination.

    MIN_ANGLE_AT_ES is dropped wherever the filing **declares** a track
    duration, per §B3.3 (*"Not applicable if the MIN_DURATION[Latitude] is
    non-zero"*). That is a property of the data, not of the run direction, so an
    uplink examination of the same set drops it too.

    ``es_lat_deg`` resolves the latitude-dependent scalars (α₀, ε₀). When it is
    None the tables travel unresolved under ``_operating_params`` and the caller
    resolves them once the worst-case geometry is known.
    """
    s.raise_on_errors()

    cfg: dict = {}
    md_bands: list[tuple[float, float, float]] = []
    if direction == "down" and s.min_duration is not None:
        md_bands = [
            b for b in nearest_table_to_bands(s.min_duration)
            if b[2] >= MIN_DURATION_MIN_S
        ]
    cfg["min_duration_by_lat"] = md_bands

    if s.max_co_freq is not None:
        cfg["max_co_freq_by_lat"] = [
            (lo, hi, int(round(v))) for lo, hi, v in nearest_table_to_bands(s.max_co_freq)
        ]

    # §B3.3: applicability follows the declared data, not the direction.
    track_declared = s.uses_track_duration
    angle = 0.0 if track_declared else float(s.min_angle_at_es_deg or 0.0)
    cfg["min_angle_at_es_deg"] = angle

    # MIN_EXCLUDE (α₀) and MIN_ELEV (ε₀): the Attachment's precedence rule means
    # these supersede the SRS x_zone / the 5° default. Emitted as scalars at the
    # examined latitude when it is known, and always as tables for the per-
    # satellite / per-step arrays the engine builds.
    # A single-point table is latitude-independent by construction, so its
    # scalar is exact even before the worst-case geometry is known — which is
    # the shape of every array in the BR's own NEXT101 case. A multi-point table
    # is only resolved once the ES latitude exists; it travels in the tables
    # below and the caller resolves it late.
    def _scalar(tbl_lookup, single_point: bool):
        if es_lat_deg is not None:
            return tbl_lookup(es_lat_deg)
        return tbl_lookup(0.0) if single_point else None

    _a0_tbl = s.min_exclude.get(0) or (
        next(iter(s.min_exclude.values())) if len(s.min_exclude) == 1 else None)
    a0 = _scalar(lambda la: s.alpha0_deg(la),
                 _a0_tbl is not None and _a0_tbl.lats.size == 1)
    if a0 is not None:
        cfg["alpha0_deg"] = float(a0)
    e0 = _scalar(lambda la: s.eps0_scalar_deg(la),
                 s.min_elev is not None and s.min_elev.lats.size == 1)
    if e0 is not None:
        cfg["min_elevation_deg"] = float(e0)
    cfg["_alpha0_table"] = {
        int(k): (v.lats.tolist(), v.vals.tolist()) for k, v in s.min_exclude.items()
    } or None
    cfg["_min_elev_table"] = (
        None if s.min_elev is None else {
            "lats": s.min_elev.lats.tolist(),
            "az": [a.tolist() for a in s.min_elev.az],
            "vals": [v.tolist() for v in s.min_elev.vals],
        }
    )
    if s.min_angle_at_sat_deg:
        cfg["min_angle_at_sat_deg"] = float(s.min_angle_at_sat_deg)
    if s.max_co_freq_sat is not None:
        cfg["max_co_freq_sat"] = int(s.max_co_freq_sat)
    if s.es_density_per_km2 is not None:
        cfg["es_density_per_km2"] = float(s.es_density_per_km2)
    if s.es_distance_km is not None:
        cfg["es_distance_km"] = float(s.es_distance_km)

    cfg["_operating_params"] = {
        "param_id": s.param_id,
        "band_mhz": [s.low_freq_mhz, s.high_freq_mhz],
        "direction": direction,
        "source": s.source,
        "es_lat_deg": es_lat_deg,
        "track_duration": bool(md_bands),
        "track_duration_declared": bool(track_declared),
        "min_angle_at_es_declared_deg": float(s.min_angle_at_es_deg or 0.0),
        "min_angle_at_es_suppressed": bool(track_declared) and float(s.min_angle_at_es_deg or 0.0) > 0.0,
        "alpha0_deg": cfg.get("alpha0_deg"),
        "min_elevation_deg": cfg.get("min_elevation_deg"),
        "max_co_freq": (None if s.max_co_freq is None
                        else s.max_co_freq_at(es_lat_deg if es_lat_deg is not None else 0.0)),
        "supersedes_srs": True,
        "lookup_rules": {
            "min_duration": "nearest latitude (§B3.3)",
            "max_co_freq": "nearest latitude (§B3.3)",
            "min_exclude": "linear interpolation, per orb_id (§B3.3)",
            "min_elev": "nearest latitude then linear in azimuth (§B3.3)",
        },
        "issues": [str(i) for i in s.issues],
    }
    return cfg


__all__ = [
    "OperatingParamsError", "ValidationIssue", "LatTable", "MinElevTable",
    "OperatingParameterSet", "OperatingParameterRegistry",
    "parse_operating_params_xml", "validate_set", "require_uplink_population",
    "load_from_paths", "load_from_texts", "load_from_mask_mdb",
    "nearest_table_to_bands", "to_engine_config", "MIN_DURATION_MIN_S",
]
