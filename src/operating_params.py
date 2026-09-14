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

    def nearest(self, lat_deg: float) -> float:
        """"The nearest latitude to that in the table will be used."

        Ties in ``|Δlat|`` resolve to the lower latitude, matching the
        nearest-latitude convention already used for pfd masks
        (:mod:`src.pfd_mask`), so one run never mixes two tie rules.
        """
        d = np.abs(self.lats - float(lat_deg))
        return float(self.vals[int(np.argmin(d))])

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

    def value(self, lat_deg: float, azimuth_deg: float) -> float:
        i = int(np.argmin(np.abs(self.lats - float(lat_deg))))
        a, v = self.az[i], self.vals[i]
        if a.size == 1:
            return float(v[0])
        # Azimuth is a circle: close it so interpolation across 350°→0° takes
        # the short way instead of clamping at the last tabulated point.
        return float(np.interp(float(azimuth_deg) % 360.0, a, v, period=360.0))

    def max_over_azimuth(self, lat_deg: float) -> float:
        """Strictest ε₀ at a latitude.

        The WCG search evaluates eligibility before any satellite azimuth
        exists, so it needs one scalar; taking the maximum is the conservative
        choice (it admits the fewest satellites).
        """
        i = int(np.argmin(np.abs(self.lats - float(lat_deg))))
        return float(self.vals[i].max())


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

    def covers(self, freq_mhz: float) -> bool:
        return self.low_freq_mhz <= float(freq_mhz) <= self.high_freq_mhz

    def contains_band(self, low_mhz: float, high_mhz: float) -> bool:
        return self.low_freq_mhz <= float(low_mhz) and float(high_mhz) <= self.high_freq_mhz

    def band_label(self) -> str:
        return f"{self.low_freq_mhz:.0f}–{self.high_freq_mhz:.0f} MHz"


# ── parsing ─────────────────────────────────────────────────────────────────

def _attr_float(el, name, default=None):
    raw = el.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(str(raw).strip())
    except ValueError:
        return default


def _attr_int(el, name, default=None):
    v = _attr_float(el, name, None)
    return default if v is None else int(round(v))


def _parse_lat_array(parent, tag) -> LatTable | None:
    pairs = []
    for el in parent.findall(tag):
        a = _attr_float(el, "a")
        if a is None or el.text is None or el.text.strip() == "":
            continue
        try:
            pairs.append((a, float(el.text.strip())))
        except ValueError:
            continue
    if not pairs:
        return None
    xs, ys = _sorted_pairs(pairs)
    return LatTable(xs, ys)


def _parse_min_exclude(parent) -> dict[int, LatTable]:
    out: dict[int, LatTable] = {}
    for blk in parent.findall("min_exclude"):
        orb = _attr_int(blk, "c", 0) or 0
        pairs = []
        for el in blk.findall("exclusion_zone_angle"):
            a = _attr_float(el, "a")
            if a is None or el.text is None or el.text.strip() == "":
                continue
            try:
                pairs.append((a, float(el.text.strip())))
            except ValueError:
                continue
        if pairs:
            xs, ys = _sorted_pairs(pairs)
            out[int(orb)] = LatTable(xs, ys)
    return out


def _parse_min_elev(parent) -> MinElevTable | None:
    lats, azs, vals = [], [], []
    for blk in parent.findall("min_elev"):
        a = _attr_float(blk, "a")
        if a is None:
            continue
        pairs = []
        for el in blk.findall("elev_angle"):
            b = _attr_float(el, "b")
            if b is None or el.text is None or el.text.strip() == "":
                continue
            try:
                pairs.append((b, float(el.text.strip())))
            except ValueError:
                continue
        if not pairs:
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

        min_dur = _parse_lat_array(blk, "min_duration")
        max_cof = _parse_lat_array(blk, "max_co_freq")
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
            es_lat_min_deg=_attr_float(blk, "es_lat_min", -90.0),
            es_lat_max_deg=_attr_float(blk, "es_lat_max", 90.0),
            es_density_per_km2=_attr_float(blk, "es_density"),
            es_distance_km=_attr_float(blk, "es_distance"),
            # §B3.3: "Assumed to be zero if not provided". The Recommendation's
            # own example header misspells this as ``angle_at_es``.
            min_angle_at_es_deg=(
                _attr_float(blk, "min_angle_at_es")
                if _attr_float(blk, "min_angle_at_es") is not None
                else _attr_float(blk, "angle_at_es", 0.0)
            ),
            min_angle_at_sat_deg=_attr_float(blk, "min_angle_at_sat", 0.0),
            max_co_freq_sat=_attr_int(blk, "max_co_freq_sat", None),
            min_exclude=_parse_min_exclude(blk),
            max_co_freq=max_cof,
            min_duration=min_dur,
            min_elev=_parse_min_elev(blk),
            source=source,
            issues=(),
        )
        issues.extend(validate_set(s))
        errs = [i for i in issues if i.severity == "error" or (strict and i.severity == "warning")]
        if errs:
            raise OperatingParamsError(
                f"{source}: " + "; ".join(str(e) for e in errs)
            )
        sets.append(OperatingParameterSet(**{**s.__dict__, "issues": tuple(issues)}))
    return sets


def validate_set(s: OperatingParameterSet) -> list[ValidationIssue]:
    """§B5.2 "Non-GSO system operating parameters ranges" plus EPS §6.7.2.2.

    Errors are values the Recommendation declares invalid; warnings are values
    that are only needed for a run type this examination may not be doing (the
    ES population is used by epfd(up) only) or that the BR's own test case
    exercises (single-point latitude arrays).
    """
    out: list[ValidationIssue] = []
    pid = s.param_id

    if s.min_duration is not None:
        bad = s.min_duration.vals[s.min_duration.vals < MIN_DURATION_MIN_S]
        if bad.size:
            out.append(ValidationIssue(
                "error", "B5.2/MIN_DURATION>=1s",
                f"values {sorted(set(bad.tolist()))} below 1 s; an absent array, "
                "not a zero, selects the classic §D5.1.4.1 algorithm", pid))
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
            if b.low_freq_mhz < a.high_freq_mhz:
                out.append(ValidationIssue(
                    "error", "B3.3/one-set-per-band",
                    f"sets {a.param_id} ({a.band_label()}) and {b.param_id} "
                    f"({b.band_label()}) overlap", None))
        return out

    def raise_on_errors(self) -> None:
        errs = [i for i in self.issues if i.severity == "error"]
        if errs:
            raise OperatingParamsError("; ".join(str(e) for e in errs))

    def for_band(
        self, low_mhz: float, high_mhz: float,
    ) -> OperatingParameterSet | None:
        """The set whose range contains the examined band.

        Returns ``None`` when no set covers it — the caller decides whether that
        is an error (an examination) or a fall-back to the legacy SRS columns.
        Raises when more than one matches, which the overlap check should have
        caught already.
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
            return hits[0]
        # A band straddling two sets, or covered by none, is a data error for an
        # examination; report it precisely rather than silently picking one.
        mid = 0.5 * (float(low_mhz) + float(high_mhz))
        touching = [s for s in self.sets if s.covers(low_mhz) or s.covers(high_mhz) or s.covers(mid)]
        if touching:
            raise OperatingParamsError(
                f"band {low_mhz:.0f}–{high_mhz:.0f} MHz is not contained in any "
                f"single operating-parameter set; it touches "
                f"{', '.join(f'{s.param_id} ({s.band_label()})' for s in touching)}."
            )
        return None

    def for_frequency(self, freq_mhz: float) -> OperatingParameterSet | None:
        self.raise_on_errors()
        hits = [s for s in self.sets if s.covers(freq_mhz)]
        if len(hits) > 1:
            raise OperatingParamsError(
                f"{freq_mhz:.3f} MHz falls in {len(hits)} operating-parameter sets."
            )
        return hits[0] if hits else None

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
    lats = tbl.lats
    vals = tbl.vals
    if lats.size == 1:
        return [(-90.0, 90.0, float(vals[0]))]
    edges = [-90.0]
    edges += [0.5 * (float(lats[i]) + float(lats[i + 1])) for i in range(lats.size - 1)]
    edges.append(90.0)
    return [
        (edges[i], edges[i + 1], float(vals[i]))
        for i in range(lats.size)
    ]


def to_engine_config(
    s: OperatingParameterSet,
    *,
    direction: str = "down",
) -> dict:
    """Config fragment the EPFD engine consumes for one examined band.

    ``direction`` is ``'down'`` or ``'up'``. Track duration is **downlink only**:
    §D5.2 (printed p. 104) states *"Note that the minimum track duration is not
    used for the epfd(up) case."* — so ``min_duration_by_lat`` is emitted only
    for a downlink examination.

    MIN_ANGLE_AT_ES is dropped wherever MIN_DURATION is non-zero, per §B3.3
    (*"Not applicable if the MIN_DURATION[Latitude] is non-zero"*).
    """
    cfg: dict = {}
    md_bands: list[tuple[float, float, float]] = []
    if direction == "down" and s.min_duration is not None:
        md_bands = [b for b in nearest_table_to_bands(s.min_duration) if b[2] > 0.0]
    cfg["min_duration_by_lat"] = md_bands

    if s.max_co_freq is not None:
        cfg["max_co_freq_by_lat"] = [
            (lo, hi, int(round(v))) for lo, hi, v in nearest_table_to_bands(s.max_co_freq)
        ]

    angle = float(s.min_angle_at_es_deg or 0.0)
    if md_bands:
        angle = 0.0
    cfg["min_angle_at_es_deg"] = angle

    cfg["_operating_params"] = {
        "param_id": s.param_id,
        "band_mhz": [s.low_freq_mhz, s.high_freq_mhz],
        "direction": direction,
        "source": s.source,
        "track_duration": bool(md_bands),
        "min_angle_at_es_suppressed": bool(md_bands) and float(s.min_angle_at_es_deg or 0.0) > 0.0,
        "lookup_rules": {
            "min_duration": "nearest latitude (§B3.3)",
            "max_co_freq": "nearest latitude (§B3.3)",
            "min_exclude": "linear interpolation (§B3.3)",
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
    "nearest_table_to_bands", "to_engine_config",
]
