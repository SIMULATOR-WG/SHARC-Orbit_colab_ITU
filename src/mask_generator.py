"""mask_generator.py — parametric PFD-mask generation (S.1503-4 Part C).

Foundation for plan WS3 (R5–R7, R27–R29):

* :class:`BeamSpec` / :class:`MaskGenParams` — the §C2.3.1 inputs (max power
  per beam in the reference bandwidth, satellite antenna gain pattern, number
  of simultaneous co-frequency beams, losses) plus §C2.2/§C1 operational
  constraints (GSO-arc exclusion α₀, min/max operating latitude → −1000 dBW).
* :func:`generate_pfd_mask_azel` — Option 2 of §C2.1/§C2.4.2: the pfd mask on
  a (latitude × azimuth × elevation) grid in the satellite local frame. Each
  cell is the **maximum envelope** (§C1, §C2.4) of
  ``pfd_i = P_i + G_i(θ_off) − 10·log10(4π d²)``  (§C2.3.1, d in metres)
  summed over the ``n_co`` strongest simultaneously-allowed beams.
* :func:`write_pfd_mask_xml` — §C4.1/§C4.2 Table 5 XML serialiser
  (``<satellite_system><pfd_mask …><by_a><by_b><pfd c=…>``), round-trippable
  through :class:`src.pfd_mask.PFDMaskXML`.

Option 1 (lat × α × ΔLong) is obtained by converting the az/el mask with the
existing :mod:`src.mask_converter` (the §C2.1 formats are interchangeable).

Geometry (satellite local frame, spherical Earth §A2.3): a boresight ray at
(az, el) from a satellite at height ``h`` intersects the Earth iff its nadir
angle ``η`` satisfies ``sin η ≤ Re/(Re+h)``; slant range
``d = (Re+h)·cos η − sqrt(Re² − ((Re+h)·sin η)²)``. Directions that miss the
Earth carry the §C1 null value (−1000 dBW).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .antenna import SimpleCircularBeamAntenna
from .constants import RE_KM

NULL_DB = -1000.0  # §C1 — "no transmission" marker


@dataclass
class BeamSpec:
    """One satellite beam (§C2.3.1 inputs).

    ``az_deg``/``el_deg`` — boresight in the satellite local frame (0,0 =
    nadir). ``power_dbw`` — maximum power delivered to the antenna in the
    reference bandwidth, dB(W/BWref). Gain: simple circular pattern
    (peak + HPBW, cos^n roll-off) — table-driven patterns can be added later.
    """
    power_dbw: float
    peak_gain_dbi: float
    hpbw_deg: float
    az_deg: float = 0.0
    el_deg: float = 0.0

    def antenna(self) -> SimpleCircularBeamAntenna:
        return SimpleCircularBeamAntenna(self.peak_gain_dbi, self.hpbw_deg)


@dataclass
class MaskGenParams:
    """Inputs of the pfd-mask generation (§B4.1 + §C2.2/§C2.3)."""
    altitude_km: float
    beams: list[BeamSpec]
    low_freq_mhz: float
    high_freq_mhz: float
    refbw_khz: float = 40.0
    n_co: int = 0                     # max simultaneous co-freq beams (0 = all)
    losses_db: float = 0.0            # feeder/pol losses subtracted from EIRP
    # §C1/§C2.2 operational constraints:
    lat_min_deg: float = -90.0        # outside → −1000 dBW rows
    lat_max_deg: float = 90.0
    # §C2.2 GSO-arc avoidance (active when ``gso_arc_alpha0_deg`` is set):
    # * ``beam_off`` — a beam is switched OFF (per satellite latitude) when its
    #   boresight ground cell lies inside |α| < α₀; the mask keeps the sidelobe
    #   leakage of the beams that remain on (cell-centre observance).
    # * ``alpha_cutoff`` — the mask cells whose ground point sees the satellite
    #   at |α| < α₀ carry the null value directly (idealised full suppression
    #   toward the zone — the operator's declaration that nothing is radiated
    #   at in-zone α; on the Option 1 grid this is a direct α-axis cutoff).
    gso_arc_alpha0_deg: float | None = None
    gso_arc_mode: str = "beam_off"    # "beam_off" | "alpha_cutoff"
    # Value written inside the exclusion zone in ``alpha_cutoff`` mode
    # (dB(W/(m²·BWref))). Default −1000 = the §C1 null; operators may declare
    # a finite floor instead (e.g. a residual-emission level).
    gso_arc_fill_dbw: float = NULL_DB
    sat_name: str = "MANUAL"
    ntc_id: str = "0"
    mask_id: int = 1


def _slant_range_km(h_km: float, eta_rad: np.ndarray) -> np.ndarray:
    """Slant range from satellite to the Earth surface along nadir angle η.

    NaN where the ray misses the Earth (sin η > Re/(Re+h))."""
    r = RE_KM + h_km
    s = r * np.sin(eta_rad)
    inside = s <= RE_KM
    root = np.sqrt(np.clip(RE_KM**2 - s**2, 0.0, None))
    d = r * np.cos(eta_rad) - root
    return np.where(inside & (np.cos(eta_rad) > 0), d, np.nan)


def _angle_between_dirs(az1, el1, az2, el2):
    """Angle (deg) between two (az, el) directions of the local frame."""
    a1, e1 = np.radians(az1), np.radians(el1)
    a2, e2 = np.radians(az2), np.radians(el2)
    # Unit vectors: x = cos e sin a, y = sin e, z = cos e cos a (z = nadir).
    v1 = np.stack([np.cos(e1) * np.sin(a1), np.sin(e1), np.cos(e1) * np.cos(a1)],
                  axis=-1)
    v2 = np.stack([np.cos(e2) * np.sin(a2), np.sin(e2), np.cos(e2) * np.cos(a2)],
                  axis=-1)
    dot = np.clip((v1 * v2).sum(axis=-1), -1.0, 1.0)
    return np.degrees(np.arccos(dot))


def generate_pfd_mask_azel(
    params: MaskGenParams,
    *,
    lat_grid_deg: np.ndarray | list | None = None,
    az_grid_deg: np.ndarray | list | None = None,
    el_grid_deg: np.ndarray | list | None = None,
) -> dict:
    """Option 2 pfd mask (§C2.4.2): grid (lat × az × el) → max-envelope PFD.

    Returns ``{"lat", "az", "el", "pfd"}`` with ``pfd`` shaped
    ``(n_lat, n_az, n_el)`` in dB(W/(m²·BWref)). Latitudes outside the
    operating band get the §C1 null value.
    """
    lat = np.asarray(lat_grid_deg if lat_grid_deg is not None
                     else np.arange(-90.0, 90.1, 10.0), dtype=float)
    az = np.asarray(az_grid_deg if az_grid_deg is not None
                    else np.arange(-90.0, 90.1, 2.0), dtype=float)
    el = np.asarray(el_grid_deg if el_grid_deg is not None
                    else np.arange(-90.0, 90.1, 2.0), dtype=float)
    if not params.beams:
        raise ValueError("MaskGenParams.beams must not be empty")

    AZ, EL = np.meshgrid(az, el, indexing="ij")
    sheet_all = _pfd_sheet(params, AZ, EL,
                           np.ones(len(params.beams), dtype=bool))
    cutoff = (params.gso_arc_alpha0_deg is not None
              and params.gso_arc_mode == "alpha_cutoff")
    pfd = np.empty((lat.size, az.size, el.size))
    for i, la in enumerate(lat):
        if not (params.lat_min_deg <= la <= params.lat_max_deg):
            pfd[i] = NULL_DB                          # §C1 non-operating band
            continue
        if cutoff:
            # Direct α cutoff: fill every mask cell whose ground point sees
            # the satellite inside |α| < α₀ at this latitude with the
            # user-defined zone value (default −1000, §C1 null).
            a_cell = _cell_alpha_at_lat(params, float(la), AZ, EL)
            zone = np.abs(a_cell) < float(params.gso_arc_alpha0_deg)
            pfd[i] = np.where(zone, float(params.gso_arc_fill_dbw), sheet_all)
            continue
        on = _beams_on_at_lat(params, float(la))
        pfd[i] = sheet_all if on.all() else _pfd_sheet(params, AZ, EL, on)
    return {"lat": lat, "az": az, "el": el, "pfd": pfd}


def _cell_alpha_at_lat(params: MaskGenParams, lat_deg: float,
                       AZ: np.ndarray, EL: np.ndarray) -> np.ndarray:
    """Signed α (§D6.4.4.1) of each (az, el) cell's ground point at the given
    satellite latitude. ``inf`` where the direction misses the Earth."""
    from .mask_converter import (  # noqa: PLC0415
        _alpha_dlon_batch, _az_el_to_directions_batch, _build_frame,
        _ray_earth_batch, _sat_ecef,
    )
    sat = _sat_ecef(float(lat_deg), 0.0, float(params.altitude_km))
    frame = _build_frame(sat)
    dirs = _az_el_to_directions_batch(AZ.ravel(), EL.ravel(), frame)
    ground = _ray_earth_batch(sat, dirs)
    ok = np.isfinite(ground).all(axis=1)
    out = np.full(AZ.size, np.inf)
    if ok.any():
        a_s, _ = _alpha_dlon_batch(ground[ok], sat, sat_lon_deg=0.0)
        out[ok] = a_s
    return out.reshape(AZ.shape)


def _pfd_sheet(params: MaskGenParams, AZ: np.ndarray, EL: np.ndarray,
               beam_on: np.ndarray) -> np.ndarray:
    """§C2.4 max envelope over an (az, el) sheet from the ON beams.

    ``pfd_i = P_i + G_i(θ_off) − 10log10(4πd²)`` per cell (§C2.3.1), summed
    over the ``n_co`` strongest ON beams. Directions missing the Earth (and
    all cells when every beam is off) carry the §C1 null value.
    """
    if not beam_on.any():
        return np.full(AZ.shape, NULL_DB)
    eta = np.radians(_angle_between_dirs(AZ, EL, 0.0, 0.0))
    d_km = _slant_range_km(params.altitude_km, eta)
    hits = np.isfinite(d_km)
    with np.errstate(invalid="ignore", divide="ignore"):
        spread_db = 10.0 * np.log10(4.0 * math.pi * (d_km * 1000.0) ** 2)

    on_beams = [b for b, keep in zip(params.beams, beam_on) if keep]
    per_beam = np.full((len(on_beams),) + AZ.shape, -np.inf)
    for b, beam in enumerate(on_beams):
        ant = beam.antenna()
        off = _angle_between_dirs(AZ, EL, beam.az_deg, beam.el_deg)
        gain = np.vectorize(ant.gain)(off)
        per_beam[b] = np.where(
            hits, beam.power_dbw - params.losses_db + gain - spread_db, -np.inf,
        )
    n_co = params.n_co if params.n_co and params.n_co > 0 else len(params.beams)
    n_co = min(n_co, len(on_beams))
    lin = 10.0 ** (per_beam / 10.0)
    lin_sorted = np.sort(lin, axis=0)[::-1]
    total = lin_sorted[:n_co].sum(axis=0)
    with np.errstate(divide="ignore"):
        sheet = 10.0 * np.log10(total)
    return np.where(hits & np.isfinite(sheet), sheet, NULL_DB)


def _beams_on_at_lat(params: MaskGenParams, lat_deg: float) -> np.ndarray:
    """§C2.2 GSO-arc avoidance: beams whose boresight ground cell falls inside
    the exclusion zone |α| < α₀ at this satellite latitude are switched OFF.

    α of each boresight cell reuses the converter geometry (§D6.4.4.1 signed
    alpha over the visible GSO arc). Boresights that miss the Earth are OFF.
    """
    on = np.ones(len(params.beams), dtype=bool)
    if params.gso_arc_alpha0_deg is None:
        return on
    from .mask_converter import (  # noqa: PLC0415
        _alpha_dlon_batch, _az_el_to_directions_batch, _build_frame,
        _ray_earth_batch, _sat_ecef,
    )
    sat = _sat_ecef(float(lat_deg), 0.0, float(params.altitude_km))
    frame = _build_frame(sat)
    b_az = np.array([b.az_deg for b in params.beams], dtype=float)
    b_el = np.array([b.el_deg for b in params.beams], dtype=float)
    dirs = _az_el_to_directions_batch(b_az, b_el, frame)
    ground = _ray_earth_batch(sat, dirs)              # (B,3), NaN on miss
    ok = np.isfinite(ground).all(axis=1)
    on[~ok] = False
    if ok.any():
        alpha_b, _ = _alpha_dlon_batch(ground[ok], sat, sat_lon_deg=0.0)
        inside = np.abs(alpha_b) < float(params.gso_arc_alpha0_deg)
        idx_ok = np.nonzero(ok)[0]
        on[idx_ok[inside]] = False
    return on


def generate_pfd_mask_alpha_dlon(
    params: MaskGenParams,
    *,
    lat_grid_deg: np.ndarray | list | None = None,
    alpha_grid_deg: np.ndarray | list | None = None,
    dlon_grid_deg: np.ndarray | list | None = None,
    sample_step_deg: float = 0.5,
) -> dict:
    """Option 1 pfd mask (lat × α × ΔLong) generated NATIVELY per §C2.4.1.

    For each satellite latitude, the visible cap is sampled densely in the
    satellite local frame; every sample carries its pfd (§C2.3.1 envelope of
    the ON beams) and its (α, ΔLong) coordinates (§D6.4.4 signed α over the
    visible GSO arc, ΔLong = Long_α − Long_NGSO). Each output cell takes the
    **maximum** pfd of the samples that fall in it — the §C2.4.1 rule of
    keeping the max pfd along the iso-α set per ΔLong interval. Cells never
    hit by a sample are filled along the ΔLong axis (edge-extension /
    interpolation, matching the reader's §C4.2 partial-grid semantics); rows
    with no samples at all carry the §C1 null value.

    Returns ``{"lat", "alpha", "dlon", "pfd"}`` with ``pfd`` shaped
    ``(n_lat, n_alpha, n_dlon)``.
    """
    from .mask_converter import (  # noqa: PLC0415
        _alpha_dlon_batch, _az_el_to_directions_batch, _build_frame,
        _ray_earth_batch, _sat_ecef,
    )

    lat = np.asarray(lat_grid_deg if lat_grid_deg is not None
                     else np.arange(-90.0, 90.1, 10.0), dtype=float)
    alpha = np.asarray(alpha_grid_deg if alpha_grid_deg is not None
                       else np.arange(-70.0, 70.1, 2.0), dtype=float)
    dlon = np.asarray(dlon_grid_deg if dlon_grid_deg is not None
                      else np.arange(-90.0, 90.1, 2.0), dtype=float)
    if not params.beams:
        raise ValueError("MaskGenParams.beams must not be empty")

    # Dense sampling of the visible cap (only directions that hit the Earth).
    s = np.arange(-90.0, 90.0 + 1e-9, float(sample_step_deg))
    AZs, ELs = np.meshgrid(s, s, indexing="ij")
    eta = np.radians(_angle_between_dirs(AZs, ELs, 0.0, 0.0))
    hit = np.isfinite(_slant_range_km(params.altitude_km, eta))
    az_f = AZs[hit]
    el_f = ELs[hit]

    # Bin edges (midpoints) for nearest-cell max-binning.
    def _edges(g: np.ndarray) -> np.ndarray:
        mid = (g[1:] + g[:-1]) / 2.0
        return np.concatenate(([-np.inf], mid, [np.inf]))

    a_edges = _edges(alpha)
    d_edges = _edges(dlon)

    cutoff = (params.gso_arc_alpha0_deg is not None
              and params.gso_arc_mode == "alpha_cutoff")
    pfd = np.full((lat.size, alpha.size, dlon.size), NULL_DB)
    for i, la in enumerate(lat):
        if not (params.lat_min_deg <= la <= params.lat_max_deg):
            continue                                   # §C1 → NULL row
        # ``alpha_cutoff``: every beam stays ON; the zone is nulled directly
        # on the α axis of the output grid (below, after binning).
        on = (np.ones(len(params.beams), dtype=bool) if cutoff
              else _beams_on_at_lat(params, float(la)))
        if not on.any():
            continue
        vals = _pfd_sheet(params, az_f, el_f, on)      # (N,) flat samples
        sat = _sat_ecef(float(la), 0.0, float(params.altitude_km))
        frame = _build_frame(sat)
        dirs = _az_el_to_directions_batch(az_f, el_f, frame)
        ground = _ray_earth_batch(sat, dirs)
        ok = np.isfinite(ground).all(axis=1) & (vals > -900.0)
        if not ok.any():
            continue
        a_s, d_s = _alpha_dlon_batch(ground[ok], sat, sat_lon_deg=0.0)
        v_s = vals[ok]
        ia = np.digitize(a_s, a_edges) - 1
        idl = np.digitize(d_s, d_edges) - 1
        sheet = np.full((alpha.size, dlon.size), -np.inf)
        np.maximum.at(sheet, (ia, idl), v_s)           # §C2.4.1 max per cell
        # Fill un-sampled cells along ΔLong within each sampled α row
        # (edge-extension + linear interpolation, as the §C4.2 reader does).
        for r in range(alpha.size):
            row = sheet[r]
            good = np.isfinite(row)
            if not good.any():
                continue
            row[~good] = np.interp(dlon[~good], dlon[good], row[good])
            sheet[r] = row
        # Second pass — α rows with NO sample at all. These α values are
        # UNREACHABLE at this satellite latitude (the §D6.4.4.4 visible-arc
        # α jumps past them when the minimising arc point moves to the arc
        # edge), so the engine never queries them there; real filings still
        # grid them (full-grid masks, §C1 continuity). Fill by interpolation /
        # edge-extension along the α axis, per ΔLong column.
        row_ok = np.isfinite(sheet).any(axis=1)
        if row_ok.any() and not row_ok.all():
            for c in range(dlon.size):
                col = sheet[:, c]
                good = np.isfinite(col)
                if not good.any():
                    continue
                col[~good] = np.interp(alpha[~good], alpha[good], col[good])
                sheet[:, c] = col
        if cutoff:
            # Direct α-axis cutoff: fill the |α| < α₀ rows of the output grid
            # with the user-defined zone value (default −1000, §C1 null).
            sheet[np.abs(alpha) < float(params.gso_arc_alpha0_deg), :] = \
                float(params.gso_arc_fill_dbw)
        pfd[i] = np.where(np.isfinite(sheet), sheet, NULL_DB)
    return {"lat": lat, "alpha": alpha, "dlon": dlon, "pfd": pfd}


def write_pfd_mask_xml(
    mask: dict,
    params: MaskGenParams,
    *,
    mask_type: str = "azimuth_elevation",
) -> str:
    """Serialise a generated mask to the §C4.1/§C4.2 (Table 5) XML schema.

    ``mask`` is the dict from :func:`generate_pfd_mask_azel` (or an
    equivalent lat×b×c grid). One mask per file (§C4.1). Round-trips through
    :class:`src.pfd_mask.PFDMaskXML`.
    """
    import xml.etree.ElementTree as ET

    if mask_type == "azimuth_elevation":
        b_name, c_name = "azimuth", "elevation"
        b_vals, c_vals = mask["az"], mask["el"]
    else:
        b_name, c_name = "alpha", "deltaLongitude"
        b_vals, c_vals = mask["alpha"], mask["dlon"]

    root = ET.Element("satellite_system", {
        "ntc_id": str(params.ntc_id), "sat_name": str(params.sat_name),
    })
    pm = ET.SubElement(root, "pfd_mask", {
        "mask_id": str(int(params.mask_id)),
        "low_freq_mhz": f"{params.low_freq_mhz:g}",
        "high_freq_mhz": f"{params.high_freq_mhz:g}",
        "refbw_khz": f"{params.refbw_khz:g}",
        "type": mask_type,
        "a_name": "latitude", "b_name": b_name, "c_name": c_name,
    })
    pfd = np.asarray(mask["pfd"], dtype=float)
    for i, a in enumerate(np.asarray(mask["lat"], dtype=float)):
        by_a = ET.SubElement(pm, "by_a", {"a": f"{a:g}"})
        for j, b in enumerate(np.asarray(b_vals, dtype=float)):
            by_b = ET.SubElement(by_a, "by_b", {"b": f"{b:g}"})
            for k, c in enumerate(np.asarray(c_vals, dtype=float)):
                e = ET.SubElement(by_b, "pfd", {"c": f"{c:g}"})
                e.text = f"{pfd[i, j, k]:.2f}"
    ET.indent(root)
    return ET.tostring(root, encoding="unicode", xml_declaration=True)
