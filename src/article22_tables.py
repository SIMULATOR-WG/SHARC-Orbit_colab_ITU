"""Static selection of Article 22 limits (Tables 22-1A through 22-1E).

Primary normative source:
  - data/2400594-RR-Vol 1-E-A5.pdf

The curve points live in `src/data/article22_limits.json`, kept separate from
the code to ease auditing and maintenance. The goal is to remove the runtime
dependency on the limits MDB.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Notes 22.5C.4 (Table 22-1A) and 22.5C.8 (Table 22-1D): single-entry limits
# 100 % of the time as a function of latitude.
#
# RR Vol. I (Ed. 2024), Art. 22:
#   –160 dB(W/(m²·40 kHz))                          for  0 < |lat| ≤ 57.5°
#   –160 + 3.4 · (57.5 − |lat|)/4                   for  57.5 < |lat| ≤ 63.75°
#   –165.3 dB(W/(m²·40 kHz))                        for  63.75 < |lat|
#
# Applicability:
#   • 22.5C.4 — Table 22-1A, ES antennas with D > 60 cm
#   • 22.5C.8 — Table 22-1D (BSS), ES antennas with D ≥ 180 cm (i.e. 180, 240, 300 cm)
#
# This block replaces the 0 %-of-the-time cap of the CCDF curve for these
# combinations; for all other cases the curve's own 0 %-time threshold
# (constant in latitude) is used as the baseline.
# ---------------------------------------------------------------------------

_RR_22_5C_LAT_BREAK1_DEG = 57.5
_RR_22_5C_LAT_BREAK2_DEG = 63.75
_RR_22_5C_LIMIT_LOW_DB = -160.0
_RR_22_5C_LIMIT_HIGH_DB = -165.3
# Slope of the linear ramp (dB per degree) between the two breakpoints.
_RR_22_5C_RAMP_SLOPE = 3.4 / 4.0  # = 0.85 dB/°


def _rr_22_5c_lat_limit_db(lat_deg: float) -> float:
    """Return the 100 %-time threshold of 22.5C.4/22.5C.8 in dB(W/(m²·40 kHz)).

    Evaluates the normative linear ramp as a function of absolute latitude.
    """
    abs_lat = abs(float(lat_deg))
    if abs_lat <= _RR_22_5C_LAT_BREAK1_DEG:
        return _RR_22_5C_LIMIT_LOW_DB
    if abs_lat >= _RR_22_5C_LAT_BREAK2_DEG:
        return _RR_22_5C_LIMIT_HIGH_DB
    return _RR_22_5C_LIMIT_LOW_DB + _RR_22_5C_RAMP_SLOPE * (_RR_22_5C_LAT_BREAK1_DEG - abs_lat)


def _rr_22_5c_lat_limit_db_vec(lat_deg: np.ndarray) -> np.ndarray:
    """Vectorized version of :func:`_rr_22_5c_lat_limit_db` (numpy)."""
    abs_lat = np.abs(np.asarray(lat_deg, dtype=float))
    out = np.where(
        abs_lat <= _RR_22_5C_LAT_BREAK1_DEG,
        _RR_22_5C_LIMIT_LOW_DB,
        np.where(
            abs_lat >= _RR_22_5C_LAT_BREAK2_DEG,
            _RR_22_5C_LIMIT_HIGH_DB,
            _RR_22_5C_LIMIT_LOW_DB + _RR_22_5C_RAMP_SLOPE * (_RR_22_5C_LAT_BREAK1_DEG - abs_lat),
        ),
    )
    return out.astype(float, copy=False)


def _applies_22_5c4(rr_reference: str, rf_diam_cm: float, bw_khz: float) -> bool:
    """Compliance with Note 22.5C.4 (Table 22-1A, FSS ES with D > 60 cm, BW=40 kHz)."""
    if rr_reference != "Article 22, TABLE 22-1A":
        return False
    if abs(float(bw_khz) - 40.0) > 1e-6:
        return False
    return float(rf_diam_cm) > 60.0 + 1e-6


def _applies_22_5c8(rr_reference: str, rf_diam_cm: float, bw_khz: float) -> bool:
    """Compliance with Note 22.5C.8 (Table 22-1D BSS, D ∈ {180, 240, 300} cm, BW=40 kHz)."""
    if rr_reference != "Article 22, TABLE 22-1D":
        return False
    if abs(float(bw_khz) - 40.0) > 1e-6:
        return False
    return float(rf_diam_cm) >= 180.0 - 1e-6


def _baseline_epfd_threshold_db(curve: list[list[float]] | None) -> float:
    """Baseline (cap) of the CCDF curve: EPFD at the 0 %-of-the-time point.

    When 22.5C.4 / 22.5C.8 do not apply, this value (constant in latitude) is
    used as the threshold in the WCGA. It preserves the absolute-EPFD ranking,
    since the same constant is subtracted from all points.
    """
    if not curve:
        return 0.0
    best_epfd = None
    for point in curve:
        try:
            epfd_db = float(point[0])
            pct = float(point[1])
        except (TypeError, ValueError, IndexError):
            continue
        if pct <= 1e-12:
            # The pct=0 point defines the "never exceed" cap of the curve.
            if best_epfd is None or epfd_db > best_epfd:
                best_epfd = epfd_db
    if best_epfd is not None:
        return best_epfd
    # Fallback: lowest EPFD (worst case) only as a reference constant.
    try:
        return float(min(curve, key=lambda p: float(p[0]))[0])
    except Exception:
        return 0.0


class EpfdLatThresholdFn:
    """Serializable (pickle-safe) callable for the WCGA `EPFDThreshold[lat]`.

    Built by :func:`build_epfd_threshold_by_lat_fn`. Uses the ramp from notes
    22.5C.4/22.5C.8 when applicable; otherwise returns a constant threshold.

    Attributes useful for logging/diagnostics:

        note (str)                : "22.5C.4", "22.5C.8" or "const".
        latitude_dependent (bool) : True if the threshold varies with latitude.
        baseline_db (float)       : constant threshold (when applicable).
    """

    __slots__ = ("note", "latitude_dependent", "baseline_db")

    def __init__(self, note: str, latitude_dependent: bool, baseline_db: float):
        self.note: str = str(note)
        self.latitude_dependent: bool = bool(latitude_dependent)
        self.baseline_db: float = float(baseline_db)

    def __call__(self, lat_deg):
        if self.latitude_dependent:
            if isinstance(lat_deg, np.ndarray):
                return _rr_22_5c_lat_limit_db_vec(lat_deg)
            return _rr_22_5c_lat_limit_db(float(lat_deg))
        if isinstance(lat_deg, np.ndarray):
            return np.full_like(np.asarray(lat_deg, dtype=float), self.baseline_db)
        return self.baseline_db

    def __repr__(self) -> str:
        if self.latitude_dependent:
            return f"EpfdLatThresholdFn(note={self.note!r}, lat_dependent=True)"
        return f"EpfdLatThresholdFn(note='const', baseline_db={self.baseline_db:.2f} dB)"


def build_epfd_threshold_by_lat_fn(
    rr_reference: str | None,
    rf_diam_cm: float | None,
    *,
    reference_bandwidth_khz: float = 40.0,
    curve: list[list[float]] | None = None,
) -> EpfdLatThresholdFn:
    """Build the `EPFDThreshold[lat]` function used by the WCGA (S.1503 §D.3.1.2).

    Rules:
      * If `rr_reference` + `rf_diam_cm` + `bw` fall under 22.5C.4 or 22.5C.8,
        use the normative latitude ramp (-160 dB → -165.3 dB).
      * Otherwise, use the 0 %-time baseline of `curve` (constant).
      * If there is no information, return the constant 0.0 — equivalent to
        ranking by absolute EPFD (legacy WCGA behavior, since the constant
        cancels out in the margin comparison).
    """
    ref = str(rr_reference or "").strip()
    d_cm = float(rf_diam_cm) if rf_diam_cm is not None else float("nan")
    bw = float(reference_bandwidth_khz)

    if _applies_22_5c4(ref, d_cm, bw):
        return EpfdLatThresholdFn(note="22.5C.4", latitude_dependent=True, baseline_db=0.0)
    if _applies_22_5c8(ref, d_cm, bw):
        return EpfdLatThresholdFn(note="22.5C.8", latitude_dependent=True, baseline_db=0.0)

    baseline_db = _baseline_epfd_threshold_db(curve) if curve is not None else 0.0
    return EpfdLatThresholdFn(note="const", latitude_dependent=False, baseline_db=baseline_db)

_DATA_PATH = Path(__file__).resolve().parent / "data" / "article22_limits.json"


# Each band in ARTICLE22_TABLES[*]["bands"] is normalized to a dict with at least
#   {"range": [lo, hi], "regions": [...]} and optional service_override / pattern_override.
# Legacy list form [lo, hi] still accepted via _band_range/_band_regions.
def _band_range(band: Any) -> tuple[float, float]:
    if isinstance(band, dict):
        rng = band["range"]
        return float(rng[0]), float(rng[1])
    return float(band[0]), float(band[1])


def _band_regions(band: Any) -> list[int]:
    if isinstance(band, dict):
        regs = band.get("regions")
        if regs:
            return [int(r) for r in regs]
    return [1, 2, 3]


def _band_service_override(band: Any) -> str | None:
    if isinstance(band, dict):
        val = band.get("service_override")
        return str(val) if val else None
    return None


def _band_pattern_override(band: Any) -> str | None:
    if isinstance(band, dict):
        val = band.get("pattern_override")
        return str(val) if val else None
    return None


def _load_article22_tables() -> dict[str, dict[str, Any]]:
    with _DATA_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


ARTICLE22_TABLES: dict[str, dict[str, Any]] = _load_article22_tables()


def _band_contains(freq_ghz: float, band: Any) -> bool:
    lo, hi = _band_range(band)
    return lo - 1e-9 <= float(freq_ghz) <= hi + 1e-9


def _band_overlaps(freq_min_ghz: float, freq_max_ghz: float, band: Any) -> bool:
    lo, hi = _band_range(band)
    return hi >= float(freq_min_ghz) and lo <= float(freq_max_ghz)


def _band_overlaps_positive_width(freq_min_ghz: float, freq_max_ghz: float, band: Any) -> bool:
    band_lo, band_hi = _band_range(band)
    lo = max(float(freq_min_ghz), band_lo)
    hi = min(float(freq_max_ghz), band_hi)
    return hi - lo > 1e-9


def _select_table_reference(freq_ghz: float, service: str) -> str | None:
    svc = str(service or "FSS").upper()
    if svc == "BSS":
        if any(_band_contains(freq_ghz, b) for b in ARTICLE22_TABLES["Article 22, TABLE 22-1D"]["bands"]):
            return "Article 22, TABLE 22-1D"
        return None

    if any(_band_contains(freq_ghz, b) for b in ARTICLE22_TABLES["Article 22, TABLE 22-1E"]["bands"]):
        return "Article 22, TABLE 22-1E"
    if any(_band_contains(freq_ghz, b) for b in ARTICLE22_TABLES["Article 22, TABLE 22-1A"]["bands"]):
        return "Article 22, TABLE 22-1A"
    if any(_band_contains(freq_ghz, b) for b in ARTICLE22_TABLES["Article 22, TABLE 22-1B"]["bands"]):
        return "Article 22, TABLE 22-1B"
    if any(_band_contains(freq_ghz, b) for b in ARTICLE22_TABLES["Article 22, TABLE 22-1C"]["bands"]):
        return "Article 22, TABLE 22-1C"
    return None


def _pick_band_for_range(
    bands: list[Any],
    *,
    freq_min_ghz: float,
    freq_max_ghz: float,
    prefer_freq_ghz: float | None = None,
) -> tuple[float, float] | None:
    overlaps = [band for band in bands if _band_overlaps(freq_min_ghz, freq_max_ghz, band)]
    if not overlaps:
        return None
    if prefer_freq_ghz is not None:
        for band in overlaps:
            if _band_contains(prefer_freq_ghz, band):
                return _band_range(band)
    return _band_range(min(overlaps, key=lambda band: _band_range(band)[0]))


def _select_table_reference_for_range(
    *,
    freq_min_ghz: float,
    freq_max_ghz: float,
    service: str,
    prefer_freq_ghz: float | None = None,
) -> tuple[str, tuple[float, float]] | None:
    svc = str(service or "FSS").upper()
    candidates: list[tuple[str, tuple[float, float]]] = []

    if svc == "BSS":
        band = _pick_band_for_range(
            ARTICLE22_TABLES["Article 22, TABLE 22-1D"]["bands"],
            freq_min_ghz=freq_min_ghz,
            freq_max_ghz=freq_max_ghz,
            prefer_freq_ghz=prefer_freq_ghz,
        )
        if band is not None:
            candidates.append(("Article 22, TABLE 22-1D", band))
    else:
        for ref in (
            "Article 22, TABLE 22-1E",
            "Article 22, TABLE 22-1A",
            "Article 22, TABLE 22-1B",
            "Article 22, TABLE 22-1C",
        ):
            band = _pick_band_for_range(
                ARTICLE22_TABLES[ref]["bands"],
                freq_min_ghz=freq_min_ghz,
                freq_max_ghz=freq_max_ghz,
                prefer_freq_ghz=prefer_freq_ghz,
            )
            if band is not None:
                candidates.append((ref, band))

    if not candidates:
        return None

    if prefer_freq_ghz is not None:
        for ref, band in candidates:
            lo, hi = band
            if lo - 1e-9 <= float(prefer_freq_ghz) <= hi + 1e-9:
                return ref, band
    return min(candidates, key=lambda item: item[1][0])


def suggest_services_for_frequency_range(freq_min_ghz: float, freq_max_ghz: float) -> list[str]:
    services: set[str] = set()
    for ref, table in ARTICLE22_TABLES.items():
        if any(_band_overlaps(freq_min_ghz, freq_max_ghz, band) for band in table["bands"]):
            services.add(str(table["service"]).upper())
    return sorted(services)


def _compute_frequency_run_for_band(
    *,
    freq_min_ghz: float,
    freq_max_ghz: float,
    bw_khz: float,
) -> float:
    half_bw_ghz = float(bw_khz) / 2_000_000.0
    freq_run = float(freq_min_ghz) + half_bw_ghz
    return min(max(freq_run, float(freq_min_ghz)), float(freq_max_ghz))


def list_article22_downlink_possibilities(
    *,
    freq_min_ghz: float,
    freq_max_ghz: float,
) -> dict[str, Any]:
    """Expand the normative Art. 22 possibilities for EPFD downlink.

    The returned structure is frontend-oriented and follows the hierarchy:

    EPFDdown -> service -> frequency run -> (diameter, BW, pattern, limits)

    To avoid spurious nodes for bands that merely touch the mask band at the
    edge, only intersections with positive width are considered.
    """
    service_nodes: dict[str, dict[str, Any]] = {}
    option_count = 0

    for rr_reference, table in ARTICLE22_TABLES.items():
        default_service = str(table["service"]).upper()
        overlapping_bands = [
            band for band in table["bands"]
            if _band_overlaps_positive_width(freq_min_ghz, freq_max_ghz, band)
        ]
        if not overlapping_bands:
            continue

        masks_by_bw: dict[float, list[dict[str, Any]]] = {}
        for item in table["masks"]:
            bw_khz = float(item["bw_khz"])
            masks_by_bw.setdefault(bw_khz, []).append(item)

        for band in overlapping_bands:
            band_lo, band_hi = _band_range(band)
            regions = _band_regions(band)
            svc_override = _band_service_override(band)
            pattern_override = _band_pattern_override(band)
            service = (svc_override or default_service).upper()

            run_min = max(float(freq_min_ghz), band_lo)
            run_max = min(float(freq_max_ghz), band_hi)
            if run_max - run_min <= 1e-9:
                continue

            service_node = service_nodes.setdefault(
                service,
                {
                    "service": service,
                    "label": service,
                    "frequencies": [],
                },
            )

            regions_key = ",".join(str(r) for r in regions)

            for bw_khz, bw_masks in sorted(masks_by_bw.items(), key=lambda item: item[0]):
                frequency_run_ghz = _compute_frequency_run_for_band(
                    freq_min_ghz=run_min,
                    freq_max_ghz=run_max,
                    bw_khz=bw_khz,
                )
                frequency_node = {
                    "id": (
                        f"{service}|{rr_reference}|R{regions_key}|"
                        f"{band_lo:.6f}|{band_hi:.6f}|{bw_khz:.3f}"
                    ),
                    "label": f"{frequency_run_ghz * 1000.0:.2f} MHz",
                    "rr_reference": rr_reference,
                    "service": service,
                    "regions": regions,
                    "band_start_ghz": band_lo,
                    "band_end_ghz": band_hi,
                    "frequency_run_ghz": float(frequency_run_ghz),
                    "frequency_run_mhz": float(frequency_run_ghz * 1000.0),
                    "reference_bandwidth_khz": float(bw_khz),
                    "options": [],
                }

                for item in sorted(bw_masks, key=lambda mask: float(mask["d_cm"])):
                    rf_diam_cm = float(item["d_cm"])
                    rf_diam_m = rf_diam_cm / 100.0
                    rf_pattern = pattern_override or str(item["pattern"])
                    option = {
                        "id": (
                            f"{service}|{rr_reference}|R{regions_key}|"
                            f"{band_lo:.6f}|{band_hi:.6f}|{bw_khz:.3f}|{rf_diam_cm:.1f}"
                        ),
                        "label": f"{rf_diam_m:.2f} m, {bw_khz:.0f} kHz",
                        "service": service,
                        "rr_reference": rr_reference,
                        "regions": regions,
                        "band_start_ghz": band_lo,
                        "band_end_ghz": band_hi,
                        "frequency_run_ghz": float(frequency_run_ghz),
                        "frequency_run_mhz": float(frequency_run_ghz * 1000.0),
                        "reference_bandwidth_khz": float(bw_khz),
                        "rf_diam_cm": rf_diam_cm,
                        "rf_diam_m": rf_diam_m,
                        "rf_pattern_rr": rf_pattern,
                        "limits": [list(point) for point in item["curve"]],
                    }
                    frequency_node["options"].append(option)
                    option_count += 1

                service_node["frequencies"].append(frequency_node)

    services = sorted(service_nodes.values(), key=lambda item: str(item["service"]))
    for service_node in services:
        service_node["frequencies"].sort(
            key=lambda item: (
                float(item["frequency_run_ghz"]),
                float(item["reference_bandwidth_khz"]),
                str(item["rr_reference"]),
                ",".join(str(r) for r in item.get("regions", [])),
            )
        )

    return {
        "root_label": "EPFDdown",
        "services": services,
        "option_count": option_count,
    }


def list_article22_downlink_possibilities_for_masks(
    masks: list[dict[str, Any]],
) -> dict[str, Any]:
    """EPFDdown tree aggregated for **all** masks of a filing.

    Multi-mask version of :func:`list_article22_downlink_possibilities`. Each
    mask declared in the filing (via SRS ``mask_lnk1`` + intersection with the
    applicable group) contributes its own set of Art. 22 runs.

    Leaves with **the same normative config** (rr_reference + sub-band +
    regions + BW + diameter + pattern) are **aggregated** when covered by
    multiple masks: the node carries ``mask_refs`` (a list) and the UI shows all
    ``mask_id`` values in a single card. ``mask_ref`` (singular) is still filled
    with the first item of the list for compatibility with legacy callers. Each
    ``mask_id`` still generates an independent run — the aggregation is purely
    visual / for cataloging.

    Each item in ``masks`` must contain at least ``freq_min_ghz`` and
    ``freq_max_ghz``; optional fields (``mask_id``, ``label``, ``ntc_id``,
    ``srs_uri``…) travel inside ``mask_ref``/``mask_refs`` to the frontend.
    """
    # Internal buffer indexed by the "aggregable" normative key.
    # Each entry accumulates the list of mask_refs that cover this config.
    @dataclass
    class _OptionAgg:
        option: dict[str, Any]
        mask_refs: list[dict[str, Any]]

    @dataclass
    class _FreqAgg:
        freq_node: dict[str, Any]
        mask_refs: list[dict[str, Any]]
        options: dict[tuple, _OptionAgg]

    service_buckets: dict[str, dict[tuple, _FreqAgg]] = {}

    def _opt_key(opt: dict[str, Any]) -> tuple:
        return (
            str(opt["rr_reference"]),
            tuple(opt.get("regions", []) or []),
            float(opt["band_start_ghz"]),
            float(opt["band_end_ghz"]),
            float(opt["reference_bandwidth_khz"]),
            float(opt["rf_diam_cm"]),
            str(opt["rf_pattern_rr"]),
        )

    def _freq_key(fn: dict[str, Any]) -> tuple:
        return (
            str(fn["rr_reference"]),
            tuple(fn.get("regions", []) or []),
            float(fn["band_start_ghz"]),
            float(fn["band_end_ghz"]),
            float(fn["reference_bandwidth_khz"]),
        )

    for mask_idx, mask in enumerate(masks):
        if "freq_min_ghz" not in mask or "freq_max_ghz" not in mask:
            raise ValueError(
                f"mask[{mask_idx}] requires 'freq_min_ghz' and 'freq_max_ghz'."
            )
        single = list_article22_downlink_possibilities(
            freq_min_ghz=float(mask["freq_min_ghz"]),
            freq_max_ghz=float(mask["freq_max_ghz"]),
        )
        mask_ref = {k: v for k, v in mask.items() if k not in ("freq_min_ghz", "freq_max_ghz")}
        mask_ref.setdefault("mask_index", mask_idx)
        mask_ref["freq_min_ghz"] = float(mask["freq_min_ghz"])
        mask_ref["freq_max_ghz"] = float(mask["freq_max_ghz"])

        for svc_node in single.get("services", []):
            svc_key = str(svc_node["service"])
            svc_bucket = service_buckets.setdefault(svc_key, {})
            for freq_node in svc_node.get("frequencies", []):
                fk = _freq_key(freq_node)
                freq_agg = svc_bucket.get(fk)
                if freq_agg is None:
                    new_freq = dict(freq_node)
                    new_freq.pop("options", None)
                    new_freq["service"] = svc_node["service"]
                    new_freq["mask_refs"] = []
                    freq_agg = _FreqAgg(freq_node=new_freq, mask_refs=[], options={})
                    svc_bucket[fk] = freq_agg
                if mask_ref not in freq_agg.mask_refs:
                    freq_agg.mask_refs.append(mask_ref)

                for opt in freq_node.get("options", []):
                    ok = _opt_key(opt)
                    agg = freq_agg.options.get(ok)
                    if agg is None:
                        new_opt = dict(opt)
                        new_opt.pop("mask_ref", None)
                        new_opt["mask_refs"] = []
                        agg = _OptionAgg(option=new_opt, mask_refs=[])
                        freq_agg.options[ok] = agg
                    if mask_ref not in agg.mask_refs:
                        agg.mask_refs.append(mask_ref)

    services_out: list[dict[str, Any]] = []
    option_count = 0
    for svc_key in sorted(service_buckets.keys()):
        svc_bucket = service_buckets[svc_key]
        svc_node = {"service": svc_key, "label": svc_key, "frequencies": []}
        freq_nodes_ordered: list[dict[str, Any]] = []
        for fk, freq_agg in svc_bucket.items():
            mask_refs = freq_agg.mask_refs
            mask_ids_sorted = sorted(
                int(mr["mask_id"]) for mr in mask_refs if mr.get("mask_id") is not None
            )
            tag = "|".join(str(m) for m in mask_ids_sorted) or "noid"
            freq_node = freq_agg.freq_node
            freq_node["id"] = f"{freq_node['id']}|masks-{tag}"
            freq_node["mask_refs"] = mask_refs
            freq_node["mask_ref"] = mask_refs[0] if mask_refs else None
            freq_node["mask_ids"] = mask_ids_sorted
            options_list: list[dict[str, Any]] = []
            for ok in sorted(freq_agg.options.keys()):
                agg = freq_agg.options[ok]
                opt_mask_ids = sorted(
                    int(mr["mask_id"]) for mr in agg.mask_refs if mr.get("mask_id") is not None
                )
                opt_tag = "|".join(str(m) for m in opt_mask_ids) or "noid"
                opt = agg.option
                opt["id"] = f"{opt['id']}|masks-{opt_tag}"
                opt["mask_refs"] = agg.mask_refs
                opt["mask_ref"] = agg.mask_refs[0] if agg.mask_refs else None
                opt["mask_ids"] = opt_mask_ids
                options_list.append(opt)
                option_count += 1
            freq_node["options"] = options_list
            freq_nodes_ordered.append(freq_node)
        freq_nodes_ordered.sort(
            key=lambda item: (
                float(item["frequency_run_ghz"]),
                float(item["reference_bandwidth_khz"]),
                str(item["rr_reference"]),
                ",".join(str(r) for r in item.get("regions", [])),
            )
        )
        svc_node["frequencies"] = freq_nodes_ordered
        services_out.append(svc_node)

    return {
        "root_label": "EPFDdown",
        "services": services_out,
        "option_count": option_count,
    }


def list_article22_downlink_runs(
    *,
    freq_min_ghz: float,
    freq_max_ghz: float,
) -> list[dict[str, Any]]:
    """Flat list of RunDescriptor for EPFD downlink (S.1503-4).

    Each item represents an isolated *run*: the intersection between the
    declared interval of the non-GSO system and an Art. 22 sub-band, multiplied
    by each combination (reference BW, ES diameter, antenna pattern). It is the
    "exploded" form of the tree exposed by
    :func:`list_article22_downlink_possibilities`.

    Fields:
        rr_reference       : "Article 22, TABLE 22-1A" …
        link_direction     : "epfd_down" (fixed in this version)
        service            : "FSS" | "BSS" — already accounts for service_override
                             (e.g. 22-1B 17.3-17.7 R2 → BSS).
        regions            : list of ITU regions (1/2/3) where the regime applies.
        band_start_ghz     : lower limit of the Art. 22 sub-band.
        band_end_ghz       : upper limit.
        run_min_ghz        : max(f_min_mask, band_start).
        run_max_ghz        : min(f_max_mask, band_end).
        rf_diam_cm         : diameter of the reference ES antenna.
        bw_khz             : reference bandwidth.
        rf_pattern_rr      : gain pattern (with pattern_override applied).
        frequency_run_ghz  : run_min_ghz + bw_khz/2 (S.1503 D2), clamped.
        epfd_threshold_curve : [epfd_dB, percent_time] points of the normative CCDF.
    """
    out: list[dict[str, Any]] = []
    for rr_reference, table in ARTICLE22_TABLES.items():
        default_service = str(table["service"]).upper()
        for band in table["bands"]:
            if not _band_overlaps_positive_width(freq_min_ghz, freq_max_ghz, band):
                continue
            band_lo, band_hi = _band_range(band)
            regions = _band_regions(band)
            svc_override = _band_service_override(band)
            pattern_override = _band_pattern_override(band)
            service = (svc_override or default_service).upper()

            run_min = max(float(freq_min_ghz), band_lo)
            run_max = min(float(freq_max_ghz), band_hi)
            if run_max - run_min <= 1e-9:
                continue

            for mask in table["masks"]:
                bw_khz = float(mask["bw_khz"])
                rf_diam_cm = float(mask["d_cm"])
                freq_run = _compute_frequency_run_for_band(
                    freq_min_ghz=run_min,
                    freq_max_ghz=run_max,
                    bw_khz=bw_khz,
                )
                out.append({
                    "rr_reference": rr_reference,
                    "link_direction": "epfd_down",
                    "service": service,
                    "regions": regions,
                    "band_start_ghz": band_lo,
                    "band_end_ghz": band_hi,
                    "run_min_ghz": run_min,
                    "run_max_ghz": run_max,
                    "rf_diam_cm": rf_diam_cm,
                    "rf_diam_m": rf_diam_cm / 100.0,
                    "bw_khz": bw_khz,
                    "rf_pattern_rr": pattern_override or str(mask["pattern"]),
                    "frequency_run_ghz": float(freq_run),
                    "frequency_run_mhz": float(freq_run * 1000.0),
                    "epfd_threshold_curve": [list(p) for p in mask["curve"]],
                })

    out.sort(key=lambda d: (
        float(d["frequency_run_ghz"]),
        str(d["service"]),
        str(d["rr_reference"]),
        ",".join(str(r) for r in d["regions"]),
        float(d["bw_khz"]),
        float(d["rf_diam_cm"]),
    ))
    return out


def list_article22_downlink_runs_for_masks(
    masks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Explode Art. 22 runs for multiple masks of the same non-GSO system.

    Complies with S.1503-4: masks with intersections are **not merged**. Each
    mask generates its own set of runs; on a frequency overlap, runs are
    duplicated (1 per mask), each carrying isolated geometry and powers. The
    caller passes a list of mask descriptors — each item must have at least
    `freq_min_ghz` and `freq_max_ghz`; optional fields (`mask_id`, `label`,
    `srs_uri`, `mask_uri`, ...) travel in the ``mask_ref`` field of each run so
    the orchestrator can pair PEs with FilingPairs.

    S.1503-4 example (Set A 10-18, Set B 15-20): runs exclusive to A in
    [10,15], exclusive to B in [18,20], duplicated in [15,18] — including the
    17.3-17.7 GHz sub-band that appears twice (one with A's mask_ref, another
    with B's mask_ref).
    """
    out: list[dict[str, Any]] = []
    for mask_idx, mask in enumerate(masks):
        if "freq_min_ghz" not in mask or "freq_max_ghz" not in mask:
            raise ValueError(
                f"mask[{mask_idx}] requires 'freq_min_ghz' and 'freq_max_ghz'."
            )
        runs = list_article22_downlink_runs(
            freq_min_ghz=float(mask["freq_min_ghz"]),
            freq_max_ghz=float(mask["freq_max_ghz"]),
        )
        mask_ref = {k: v for k, v in mask.items() if k not in ("freq_min_ghz", "freq_max_ghz")}
        mask_ref.setdefault("mask_index", mask_idx)
        mask_ref["freq_min_ghz"] = float(mask["freq_min_ghz"])
        mask_ref["freq_max_ghz"] = float(mask["freq_max_ghz"])
        for run in runs:
            run = dict(run)
            run["mask_ref"] = mask_ref
            out.append(run)
    out.sort(key=lambda d: (
        float(d["frequency_run_ghz"]),
        int(d["mask_ref"].get("mask_index", 0)),
        str(d["service"]),
        str(d["rr_reference"]),
        ",".join(str(r) for r in d["regions"]),
        float(d["bw_khz"]),
        float(d["rf_diam_cm"]),
    ))
    return out


def select_article22_limits(
    *,
    freq_ghz: float,
    antenna_diameter_m: float,
    service: str,
    bw_khz: float = 40.0,
) -> dict[str, Any] | None:
    ref = _select_table_reference(freq_ghz=freq_ghz, service=service)
    if ref is None:
        return None

    table = ARTICLE22_TABLES[ref]
    masks = list(table["masks"])
    available_bws = sorted({float(item["bw_khz"]) for item in masks if item.get("bw_khz") is not None})
    if not available_bws:
        return None

    bw_target = min(available_bws, key=lambda v: abs(v - float(bw_khz)))
    masks_bw = [item for item in masks if abs(float(item["bw_khz"]) - bw_target) < 1e-9]
    ant_cm = float(antenna_diameter_m) * 100.0
    # Epsilon guards float noise in m→cm conversion (e.g. D=0.6 m vs 60 cm);
    # same rule as select_resolution76_limits.
    qualified = [item for item in masks_bw if float(item["d_cm"]) <= ant_cm + 1e-6]
    if qualified:
        chosen = max(qualified, key=lambda item: float(item["d_cm"]))
    else:
        chosen = min(masks_bw, key=lambda item: float(item["d_cm"]))

    pattern = str(chosen["pattern"])

    return {
        "limits": [list(item) for item in chosen["curve"]],
        "reference_bandwidth_khz": float(chosen["bw_khz"]),
        "rr_reference": ref,
        "rf_diam_cm": float(chosen["d_cm"]),
        "rf_pattern_rr": pattern,
    }


def apply_article22_limits_to_config(config: dict) -> None:
    ngso = config.setdefault("non_gso", {})
    gso_es = config.setdefault("gso_es", {})
    pfd_cfg = config.setdefault("pfd_mask", {})
    art22 = config.setdefault("article22_limits", {})

    service = str(gso_es.get("service", "FSS")).upper()
    antenna_diameter_m = float(gso_es.get("antenna_diameter_m", 1.2) or 1.2)
    ref_bw_khz = float(art22.get("reference_bandwidth_khz", 40.0) or 40.0)
    user_freq_ghz = pfd_cfg.get("simulation_frequency_ghz")
    if user_freq_ghz is None:
        user_freq_ghz = ngso.get("frequency_ghz")

    freq_min = pfd_cfg.get("freq_min_ghz")
    freq_max = pfd_cfg.get("freq_max_ghz")
    selected = None
    effective_band: tuple[float, float] | None = None
    freq_ghz = float(user_freq_ghz or 0.0)

    if freq_min is not None and freq_max is not None:
        band_choice = _select_table_reference_for_range(
            freq_min_ghz=float(freq_min),
            freq_max_ghz=float(freq_max),
            service=service,
            prefer_freq_ghz=freq_ghz if freq_ghz > 0.0 else None,
        )
        if band_choice is not None:
            ref, effective_band = band_choice
            table = ARTICLE22_TABLES[ref]
            masks = list(table["masks"])
            available_bws = sorted({float(item["bw_khz"]) for item in masks if item.get("bw_khz") is not None})
            if available_bws:
                bw_target = min(available_bws, key=lambda v: abs(v - float(ref_bw_khz)))
                ant_cm = float(antenna_diameter_m) * 100.0
                masks_bw = [item for item in masks if abs(float(item["bw_khz"]) - bw_target) < 1e-9]
                qualified = [item for item in masks_bw if float(item["d_cm"]) <= ant_cm + 1e-6]
                chosen = max(qualified, key=lambda item: float(item["d_cm"])) if qualified else min(masks_bw, key=lambda item: float(item["d_cm"]))
                pattern = str(chosen["pattern"])
                selected = {
                    "limits": [list(item) for item in chosen["curve"]],
                    "reference_bandwidth_khz": float(chosen["bw_khz"]),
                    "rr_reference": ref,
                    "rf_diam_cm": float(chosen["d_cm"]),
                    "rf_pattern_rr": pattern,
                }

    if selected is None:
        if freq_min is not None and freq_max is not None:
            half_bw_ghz = ref_bw_khz / 2_000_000.0
            freq_run = float(freq_min) + half_bw_ghz
            freq_run = min(max(freq_run, float(freq_min)), float(freq_max))
            ngso["frequency_ghz"] = freq_run
            freq_ghz = freq_run
        selected = select_article22_limits(
            freq_ghz=freq_ghz,
            antenna_diameter_m=antenna_diameter_m,
            service=service,
            bw_khz=ref_bw_khz,
        )
    if selected is None:
        logger.warning(
            "No Article 22 table found for f=%.6f GHz, service=%s, D=%.3f m",
            freq_ghz, service, antenna_diameter_m,
        )
        return

    if freq_min is not None and freq_max is not None:
        band_start = float(effective_band[0]) if effective_band is not None else float(freq_min)
        band_end = float(effective_band[1]) if effective_band is not None else float(freq_max)
        run_min = max(float(freq_min), band_start)
        run_max = min(float(freq_max), band_end)
        if user_freq_ghz is not None and math.isfinite(float(user_freq_ghz)):
            freq_run = min(max(float(user_freq_ghz), run_min), run_max)
        else:
            half_bw_ghz = float(selected["reference_bandwidth_khz"]) / 2_000_000.0
            freq_run = run_min + half_bw_ghz
            freq_run = min(max(freq_run, run_min), run_max)
        ngso["frequency_ghz"] = freq_run
        freq_ghz = freq_run

    art22["limits"] = selected["limits"]
    art22["reference_bandwidth_khz"] = selected["reference_bandwidth_khz"]
    art22["rr_reference"] = selected["rr_reference"]
    art22["_epfd_mask_id"] = None
    art22["_epfd_rf_diam_cm"] = selected["rf_diam_cm"]
    art22["_epfd_rf_pattern_rr"] = selected["rf_pattern_rr"]

    # Notes 22.5C.4 / 22.5C.8: signal to the pipeline whether the 100%-time
    # threshold varies by latitude for this combination (Table, diameter,
    # reference BW).
    bw_sel_khz = float(selected["reference_bandwidth_khz"])
    d_cm_sel = float(selected["rf_diam_cm"])
    ref_sel = str(selected["rr_reference"])
    is_5c4 = _applies_22_5c4(ref_sel, d_cm_sel, bw_sel_khz)
    is_5c8 = _applies_22_5c8(ref_sel, d_cm_sel, bw_sel_khz)
    art22["lat_dependent_limit_note"] = (
        "22.5C.4" if is_5c4 else ("22.5C.8" if is_5c8 else None)
    )

    logger.info(
        "Selected Art. 22 limits: %s, BW=%.0f kHz, Dref=%.0f cm, pattern=%s",
        selected["rr_reference"],
        float(selected["reference_bandwidth_khz"]),
        float(selected["rf_diam_cm"]),
        selected["rf_pattern_rr"],
    )
    if art22["lat_dependent_limit_note"]:
        logger.info(
            "  Note %s applicable: 100%%-time EPFDThreshold varies with latitude "
            "(-160 dB up to 57.5°; ramp down to -165.3 dB from 63.75°).",
            art22["lat_dependent_limit_note"],
        )
    logger.info(
        "FrequencyRun adjusted by Art. 22 limits: %.6f GHz",
        freq_ghz,
    )
