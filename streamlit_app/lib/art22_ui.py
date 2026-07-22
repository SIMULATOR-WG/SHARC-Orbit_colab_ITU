"""art22_ui.py — shared helpers for the Article 22 downlink scenario selector.

Used by the Single-entry, Aggregate and Launcher pages to:
  * group a filing's PFD (downlink) bands (``system_bands``),
  * merge/intersect frequency intervals (``merge_intervals`` / ``intersect_sets``),
  * build the normative Art. 22 possibility tree over band(s)
    (``art22_tree_for_bands``) — service → frequency run → ES antenna/BW leaf.

The tree is built by ``list_article22_downlink_possibilities_for_masks``.
Cached on inputs.
"""
from __future__ import annotations

import streamlit as st

from . import srs_inspect


def system_bands(srs_path: str, ntc_id: str | None):
    """Distinct **downlink** (PFD) bands of a system, grouped by band.

    Only PFD masks (``f_mask="P"``) are kept — they define the EPFD↓ band;
    EIRP (uplink) and Other masks are dropped. Each entry is one band shared
    by a subset of PFD masks: ``{"freq_min", "freq_max", "mask_ids":[...]}``.
    Sorted by frequency. ``[]`` when no PFD band is declared.

    Cached on (path, ntc_id, file signature) — replacing the MDB at the
    same path busts the cache instead of serving stale bands.
    """
    return _system_bands_cached(srs_path, ntc_id, srs_inspect._file_sig(srs_path))


@st.cache_data(show_spinner=False)
def _system_bands_cached(srs_path: str, ntc_id: str | None, file_sig):
    try:
        bands = srs_inspect.frequency_bands(srs_path, ntc_id)
    except Exception:  # noqa: BLE001
        return []
    groups: dict[tuple[float, float], dict] = {}
    for m in bands.get("masks") or []:
        if m.get("type") != "PFD":
            continue
        if m.get("freq_min_ghz") is None or m.get("freq_max_ghz") is None:
            continue
        key = (round(float(m["freq_min_ghz"]), 4), round(float(m["freq_max_ghz"]), 4))
        g = groups.setdefault(key, {"mask_ids": []})
        if m.get("mask_id") is not None:
            g["mask_ids"].append(int(m["mask_id"]))
    out = []
    for (lo, hi), g in sorted(groups.items()):
        out.append({"freq_min": lo, "freq_max": hi, "mask_ids": sorted(g["mask_ids"])})
    return out


def system_tx_subbands(srs_path: str, ntc_id: str | None):
    """Distinct **transmitting** sub-bands of a system (SRS ``grp``, emi_rcp='E').

    These are the frequency assignments the notice actually operates in —
    the emitting-satellite band filter (``restrict_emitters_to_sim_band``)
    keys on them. Each entry is one sub-band shared by a set of groups:
    ``{"freq_min", "freq_max", "grp_ids": [...], "beams": [...]}``, sorted by
    frequency. ``[]`` when the filing declares no Tx group band (the filter
    is then inert and every satellite simulates).

    Cached on (path, ntc_id, file signature) like :func:`system_bands`.
    """
    return _system_tx_subbands_cached(srs_path, ntc_id,
                                       srs_inspect._file_sig(srs_path))


@st.cache_data(show_spinner=False)
def _system_tx_subbands_cached(srs_path: str, ntc_id: str | None, file_sig):
    try:
        bands = srs_inspect.frequency_bands(srs_path, ntc_id)
    except Exception:  # noqa: BLE001
        return []
    groups: dict[tuple[float, float], dict] = {}
    for g in bands.get("groups") or []:
        # srs_inspect maps emi_rcp 'E' → "Tx (emit)"; raw value kept otherwise.
        if not str(g.get("emi_rcp", "")).upper().startswith(("TX", "E")):
            continue
        if g.get("freq_min_ghz") is None or g.get("freq_max_ghz") is None:
            continue
        key = (round(float(g["freq_min_ghz"]), 4), round(float(g["freq_max_ghz"]), 4))
        e = groups.setdefault(key, {"grp_ids": [], "beams": set()})
        if g.get("grp_id") is not None:
            e["grp_ids"].append(int(g["grp_id"]))
        if g.get("beam_name") and g["beam_name"] != "—":
            e["beams"].add(str(g["beam_name"]))
    out = []
    for (lo, hi), e in sorted(groups.items()):
        out.append({
            "freq_min": lo, "freq_max": hi,
            "grp_ids": sorted(set(e["grp_ids"])),
            "beams": sorted(e["beams"]),
        })
    return out


def merge_intervals(ivs: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Union of possibly-overlapping [lo, hi] intervals → disjoint sorted list."""
    merged: list[tuple[float, float]] = []
    for lo, hi in sorted(ivs):
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def intersect_sets(a: list[tuple[float, float]],
                    b: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Intersection of two interval-sets → disjoint sorted list."""
    res = []
    for la, ha in a:
        for lb, hb in b:
            lo, hi = max(la, lb), min(ha, hb)
            if lo <= hi:
                res.append((lo, hi))
    return merge_intervals(res)


@st.cache_data(show_spinner=False)
def art22_tree_for_bands(intervals: tuple[tuple[float, float], ...]):
    """Article 22 EPFD↓ possibility tree over the given band(s).

    Each interval is fed as a synthetic band to
    ``list_article22_downlink_possibilities_for_masks``. Leaves carry no
    ``mask_id`` (caller decides whether/which to pin). Returns
    ``{"services": [...]}`` (empty when no normative band intersects).
    """
    if not intervals:
        return {"services": []}
    try:
        from src.article22_tables import (  # type: ignore[import]
            list_article22_downlink_possibilities_for_masks,
        )
    except Exception:  # noqa: BLE001
        return {"services": []}
    masks = [{"freq_min_ghz": lo, "freq_max_ghz": hi} for lo, hi in intervals]
    try:
        return list_article22_downlink_possibilities_for_masks(masks)
    except Exception:  # noqa: BLE001
        return {"services": []}
