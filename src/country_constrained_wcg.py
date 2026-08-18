"""country_constrained_wcg.py — WCGA with ES restricted to countries.

Activates an optional filter on the **same** S.1503-4 §D.3.1 WCGA used by
Single-entry (``search_wcg_s1503``):

1. Only ES locations inside the selected country polygons are stored.
2. Optionally sweeps RAAN (Ω) so the satellite ground-track longitude can
   reach the country (then ΔΩ is applied after the search).

RAAN sweep defaults to **auto** from the filing (same rule as §D4.6.1),
**per orbit shape**:

* repeating ground track (``f_stn_keep`` and ``rpt_period ≥ 1 h``) → sweep OFF
  for that orbit (preserve the filed Earth-fixed track);
* otherwise → sweep ON for that orbit (worst-case phasing over the country).

Filings that mix repeating and non-repeating shells (e.g. USASAT-NGSO-3) are
handled orbit-by-orbit; post-search ΔΩ (from the winner) is applied to **all**
sweep-ON satellites so relative RAAN among free shells is preserved, while
repeating / OFF shells keep the filed RAAN. Force on/off overrides still
apply to every orbit.

This does **not** replace WCGA with an ES×GSO latitude/longitude grid.
Aggregate / Single-entry leave the filter unset and behave exactly as before.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from .s1588_studies.countries import filter_country_codes
from .wcg_search import clear_wcga_country_codes, set_wcga_country_codes

# Return type of resolve: True/False = force; "auto" = per-orbit §D4.6.1.
RaanSweepPolicy = bool | str


def is_repeating_ground_track(
    f_stn_keep: bool,
    rpt_period_s: float,
) -> bool:
    """§D4.6.1: station keeping must hold a declared repeat ≥ 1 h."""
    return bool(f_stn_keep) and float(rpt_period_s or 0.0) >= 3600.0


def oe_repeating_ground_track(oe: Any) -> bool:
    """True when an ``OrbitalElements`` (or duck-type) has a locked track."""
    return is_repeating_ground_track(
        bool(getattr(oe, "f_stn_keep", False)),
        float(getattr(oe, "rpt_period_s", 0.0) or 0.0),
    )


def _plane_flags_from_yaml(ngso: dict[str, Any]) -> list[tuple[bool, float]]:
    planes = ngso.get("planes") or ngso.get("_planes") or []
    if planes:
        out: list[tuple[bool, float]] = []
        for p in planes:
            f_sk = bool(
                p.get("f_stn_keep")
                if p.get("f_stn_keep") is not None
                else (
                    ngso.get("f_stn_keep")
                    or ngso.get("_f_stn_keep")
                    or ngso.get("station_keep")
                    or False
                )
            )
            rpt = float(
                p.get("rpt_period_s")
                if p.get("rpt_period_s") is not None
                else (
                    ngso.get("rpt_period_s")
                    or ngso.get("_rpt_period_s")
                    or 0.0
                )
            )
            out.append((f_sk, rpt))
        return out
    f_sk = bool(
        ngso.get("f_stn_keep")
        or ngso.get("_f_stn_keep")
        or ngso.get("station_keep")
        or False
    )
    rpt = float(ngso.get("rpt_period_s") or ngso.get("_rpt_period_s") or 0.0)
    return [(f_sk, rpt)]


def filing_repeating_ground_track(
    srs_path: str | Path | None,
    ntc_id: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Detect §D4.6.1 repeating ground track(s) from an SRS filing.

    ``repeating`` mirrors plane[0] (legacy). Details also report the mix across
    all planes: ``mixed``, ``n_repeating_planes``, ``n_non_repeating_planes``.

    On read failure, ``repeating=False`` (conservative: allow Ω sweep unless
    the filing clearly locks the track).
    """
    details: dict[str, Any] = {
        "f_stn_keep": False,
        "rpt_period_s": 0.0,
        "repeating": False,
        "source": None,
        "n_repeating_planes": 0,
        "n_non_repeating_planes": 0,
        "mixed": False,
    }
    if not srs_path:
        return False, details
    path = Path(srs_path)
    if not path.exists():
        return False, details

    try:
        if path.suffix.lower() in (".yaml", ".yml"):
            import yaml  # noqa: PLC0415
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            ngso = raw.get("non_gso") or raw.get("constellation") or {}
            flags = _plane_flags_from_yaml(ngso)
            details["source"] = "yaml"
        else:
            from .srs_reader import read_srs_mdb  # noqa: PLC0415
            system = read_srs_mdb(str(path), ntc_id=ntc_id)
            planes = getattr(system, "orbit_planes", None) or []
            if not planes:
                return False, details
            flags = [
                (bool(getattr(p, "f_stn_keep", False)),
                 float(getattr(p, "rpt_period_s", 0.0) or 0.0))
                for p in planes
            ]
            details["source"] = "srs"
    except Exception:  # noqa: BLE001
        return False, details

    if not flags:
        return False, details

    n_rep = sum(1 for f_sk, rpt in flags if is_repeating_ground_track(f_sk, rpt))
    n_non = len(flags) - n_rep
    f_sk0, rpt0 = flags[0]
    repeating0 = is_repeating_ground_track(f_sk0, rpt0)

    details["f_stn_keep"] = f_sk0
    details["rpt_period_s"] = rpt0
    details["repeating"] = repeating0
    details["n_planes"] = len(flags)
    details["n_repeating_planes"] = n_rep
    details["n_non_repeating_planes"] = n_non
    details["mixed"] = bool(n_rep > 0 and n_non > 0)
    if rpt0 > 0:
        details["rpt_period_days"] = rpt0 / 86400.0
    return repeating0, details


def resolve_country_raan_sweep(
    mode: str | bool | None,
    srs_path: str | Path | None = None,
    ntc_id: str | None = None,
) -> tuple[RaanSweepPolicy, dict[str, Any]]:
    """Resolve country WCGA RAAN-sweep policy.

    Returns ``(policy, details)`` where ``policy`` is:

    * ``True`` / ``False`` — force ON / OFF for every orbit
    * ``"auto"`` — per-orbit §D4.6.1 (repeating → OFF, else ON)

    ``details`` always includes filing mix stats from
    :func:`filing_repeating_ground_track`.
    """
    repeating, det = filing_repeating_ground_track(srs_path, ntc_id)
    if mode is True or (isinstance(mode, str) and mode.strip().lower() in ("on", "true", "1")):
        return True, {
            **det,
            "mode": "force_on",
            "policy": "force_on",
            "raan_sweep": True,
        }
    if mode is False or (isinstance(mode, str) and mode.strip().lower() in ("off", "false", "0")):
        return False, {
            **det,
            "mode": "force_off",
            "policy": "force_off",
            "raan_sweep": False,
        }
    # auto → per-orbit (legacy bool ``raan_sweep`` = plane[0] for UI captions)
    return "auto", {
        **det,
        "mode": "auto",
        "policy": "per_orbit",
        "raan_sweep": (not repeating) if not det.get("mixed") else None,
    }


@contextmanager
def country_constrained_wcga(
    country_codes: Sequence[str],
    *,
    raan_sweep: RaanSweepPolicy = True,
) -> Iterator[list[str]]:
    """Context manager: run WCGA accepting only ES inside ``country_codes``.

    ``raan_sweep``:

    * ``True`` / ``False`` — force Ω sweep for every orbit
    * ``"auto"`` — per-orbit from each OE's ``f_stn_keep`` / ``rpt_period_s``

    Yields the normalized ISO alpha-3 list. Always clears the filter on exit
    so a subsequent normative Single-entry / Aggregate run is unaffected.
    """
    codes = filter_country_codes(country_codes)
    if not codes:
        raise ValueError("country_codes must list at least one valid ISO alpha-3 code")
    set_wcga_country_codes(codes, raan_sweep=raan_sweep)
    try:
        yield codes
    finally:
        clear_wcga_country_codes()
