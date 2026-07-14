"""
geometry.py — ES/GSO geometric descriptor for Studies 2 and 3 (Resolution 76).

`GeometryPoint` represents a Stage 1 grid point (S.1503-4, Appendix D.6):
earth station (ES) position, longitude of the co-frequency GSO satellite
(pointing) and (optionally) ES/GSO antenna parameters.

`estimate_geometry_grid_count` returns the number of grid points as a function
of the steps `grid_step_deg` and `gso_pointing_step_deg`.

References:
    ITU-R S.1503-4, Appendix D, §D.6 (Worst-case geometry).
    Resolution 76, Study 2 — convolution on the same grid.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Iterator, Optional
import math


@dataclass(frozen=True)
class GeometryPoint:
    """Fixed geometric point for an S.1503 simulation without WCG search.

    Coordinates in degrees. `gso_lon_deg` is the longitude of the co-frequency
    GSO satellite pointed at by the ES.

    Optional fields (`pointing_az_deg`, `pointing_el_deg`, `es_antenna_id`,
    `gso_antenna_id`) preserve the audit trail for the `aggregation_fingerprint`.
    """

    es_lat_deg: float
    es_lon_deg: float
    gso_lon_deg: float
    pointing_az_deg: Optional[float] = None
    pointing_el_deg: Optional[float] = None
    es_antenna_id: Optional[str] = None
    gso_antenna_id: Optional[str] = None
    min_elevation_deg: Optional[float] = None

    def __post_init__(self) -> None:
        if not -90.0 <= self.es_lat_deg <= 90.0:
            raise ValueError(f"es_lat_deg out of [-90, 90]: {self.es_lat_deg}")
        if not -180.0 <= self.es_lon_deg <= 180.0:
            raise ValueError(f"es_lon_deg out of [-180, 180]: {self.es_lon_deg}")
        if not -180.0 <= self.gso_lon_deg <= 180.0:
            raise ValueError(f"gso_lon_deg out of [-180, 180]: {self.gso_lon_deg}")
        if self.min_elevation_deg is not None and not 0.0 <= self.min_elevation_deg <= 90.0:
            raise ValueError(
                f"min_elevation_deg out of [0, 90]: {self.min_elevation_deg}"
            )

    def fingerprint_tuple(self) -> tuple:
        """Canonical tuple used in composing the `aggregation_fingerprint`."""
        return (
            round(self.es_lat_deg, 6),
            round(self.es_lon_deg, 6),
            round(self.gso_lon_deg, 6),
            None if self.pointing_az_deg is None else round(self.pointing_az_deg, 6),
            None if self.pointing_el_deg is None else round(self.pointing_el_deg, 6),
            self.es_antenna_id,
            self.gso_antenna_id,
            None if self.min_elevation_deg is None else round(self.min_elevation_deg, 6),
        )


def _frange_inclusive(start: float, stop: float, step: float) -> Iterator[float]:
    """Float range inclusive at the final endpoint (with tolerance)."""
    if step <= 0:
        raise ValueError(f"step must be positive: {step}")
    n_steps = int(math.floor((stop - start) / step + 1e-9))
    for i in range(n_steps + 1):
        yield start + i * step


def _lon_values(start: float, stop: float, step: float) -> list[float]:
    """Longitude grid values, collapsing the duplicated antimeridian.

    When the range wraps the full circle (e.g. -180..+180) the final endpoint
    is the same physical meridian as the start; keeping both would double its
    statistical weight, so the endpoint is omitted (only when it is actually
    reached by the step).
    """
    values = list(_frange_inclusive(start, stop, step))
    if len(values) > 1:
        span = values[-1] - values[0]
        if abs(math.remainder(span, 360.0)) < 1e-6:
            values.pop()
    return values


def estimate_geometry_grid_count(
    grid_step_deg: float,
    gso_pointing_step_deg: Optional[float] = None,
    min_elevation_deg: Optional[float] = None,
    es_lat_range_deg: tuple[float, float] = (-90.0, 90.0),
    es_lon_range_deg: tuple[float, float] = (-180.0, 180.0),
    gso_lon_range_deg: tuple[float, float] = (-180.0, 180.0),
    country_codes: Optional[list[str]] = None,
) -> int:
    """Count grid points ES (lat × lon) × GSO pointing.

    Aligned with S.1503-4 §D.6.4 (sweep of ES and of the GSO arc in discrete steps).

    `gso_pointing_step_deg` default = `grid_step_deg` (uniform).
    ES points outside GSO visibility (|Δlon| > LIMIT) with minimum elevation
    are counted only if `min_elevation_deg is None`; otherwise a GSO visibility
    filter is applied (geostationary satellite at the equator at 35 786 km).
    """
    if grid_step_deg <= 0:
        raise ValueError(f"grid_step_deg must be positive: {grid_step_deg}")
    if gso_pointing_step_deg is None:
        gso_pointing_step_deg = grid_step_deg
    if gso_pointing_step_deg <= 0:
        raise ValueError(
            f"gso_pointing_step_deg must be positive: {gso_pointing_step_deg}"
        )

    lat_min, lat_max = es_lat_range_deg
    lon_min, lon_max = es_lon_range_deg
    gso_min, gso_max = gso_lon_range_deg

    es_lats = list(_frange_inclusive(lat_min, lat_max, grid_step_deg))
    es_lons = _lon_values(lon_min, lon_max, grid_step_deg)
    gso_lons = _lon_values(gso_min, gso_max, gso_pointing_step_deg)

    use_country_filter = bool(country_codes)
    point_in_countries = None
    if use_country_filter:
        from .countries import point_in_countries as _pin
        point_in_countries = _pin

    if min_elevation_deg is None and not use_country_filter:
        return len(es_lats) * len(es_lons) * len(gso_lons)

    count = 0
    for lat in es_lats:
        for lon in es_lons:
            if use_country_filter and not point_in_countries(lat, lon, country_codes):
                continue
            for gso_lon in gso_lons:
                if (
                    min_elevation_deg is not None
                    and _gso_elevation_deg(lat, lon, gso_lon) < min_elevation_deg
                ):
                    continue
                count += 1
    return count


def _gso_elevation_deg(es_lat_deg: float, es_lon_deg: float, gso_lon_deg: float) -> float:
    """Elevation of the GSO satellite seen from the ES (degrees). Spherical approximation.

    Classic formula:
        cos(γ) = cos(lat_ES) · cos(Δlon)
        El = atan2(cos(γ) - r_E/r_GSO, sin(γ))
    where r_E/r_GSO ≈ 0.1513 (radii in km).
    """
    R_E = 6378.137
    R_GSO = 42164.0
    rho = R_E / R_GSO
    lat = math.radians(es_lat_deg)
    dlon = math.radians(gso_lon_deg - es_lon_deg)
    cos_gamma = math.cos(lat) * math.cos(dlon)
    if cos_gamma <= rho:
        return -90.0
    gamma = math.acos(cos_gamma)
    el = math.atan2(cos_gamma - rho, math.sin(gamma))
    return math.degrees(el)


def compute_grid_fingerprint(
    *,
    grid_step_deg: float,
    gso_pointing_step_deg: Optional[float] = None,
    min_elevation_deg: Optional[float] = None,
    es_lat_range_deg: tuple[float, float] = (-90.0, 90.0),
    es_lon_range_deg: tuple[float, float] = (-180.0, 180.0),
    gso_lon_range_deg: tuple[float, float] = (-180.0, 180.0),
    country_codes: Optional[list[str]] = None,
) -> str:
    """Deterministic SHA-256 hash (32 chars) of the Study 2 Stage 1 grid.

    Included in the `aggregation_fingerprint` to ensure correct reuse.
    """
    import hashlib
    import json

    payload = {
        "grid_step_deg": round(float(grid_step_deg), 6),
        "gso_pointing_step_deg": (
            None
            if gso_pointing_step_deg is None
            else round(float(gso_pointing_step_deg), 6)
        ),
        "min_elevation_deg": (
            None
            if min_elevation_deg is None
            else round(float(min_elevation_deg), 6)
        ),
        "es_lat_range_deg": [round(es_lat_range_deg[0], 6), round(es_lat_range_deg[1], 6)],
        "es_lon_range_deg": [round(es_lon_range_deg[0], 6), round(es_lon_range_deg[1], 6)],
        "gso_lon_range_deg": [round(gso_lon_range_deg[0], 6), round(gso_lon_range_deg[1], 6)],
        "country_codes": sorted([str(c).upper() for c in country_codes]) if country_codes else [],
    }
    blob = json.dumps(payload, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:32]


def iter_geometry_grid(
    grid_step_deg: float,
    gso_pointing_step_deg: Optional[float] = None,
    min_elevation_deg: Optional[float] = None,
    es_lat_range_deg: tuple[float, float] = (-90.0, 90.0),
    es_lon_range_deg: tuple[float, float] = (-180.0, 180.0),
    gso_lon_range_deg: tuple[float, float] = (-180.0, 180.0),
    country_codes: Optional[list[str]] = None,
) -> Iterator[GeometryPoint]:
    """Iterate Stage 1 grid points as `GeometryPoint`.

    `country_codes` (list of ISO 3166-1 alpha-3, optional): restricts ES to
    the points inside the territory of some selected country. Empty list or
    None = the whole globe (default).
    """
    if gso_pointing_step_deg is None:
        gso_pointing_step_deg = grid_step_deg
    lat_min, lat_max = es_lat_range_deg
    lon_min, lon_max = es_lon_range_deg
    gso_min, gso_max = gso_lon_range_deg

    use_country_filter = bool(country_codes)
    point_in_countries = None
    if use_country_filter:
        from .countries import point_in_countries as _pin
        point_in_countries = _pin

    es_lons = _lon_values(lon_min, lon_max, grid_step_deg)
    gso_lons = _lon_values(gso_min, gso_max, gso_pointing_step_deg)
    for lat in _frange_inclusive(lat_min, lat_max, grid_step_deg):
        for lon in es_lons:
            if use_country_filter and not point_in_countries(lat, lon, country_codes):
                continue
            for gso_lon in gso_lons:
                if min_elevation_deg is not None:
                    if _gso_elevation_deg(lat, lon, gso_lon) < min_elevation_deg:
                        continue
                yield GeometryPoint(
                    es_lat_deg=lat,
                    es_lon_deg=lon,
                    gso_lon_deg=gso_lon,
                    min_elevation_deg=min_elevation_deg,
                )
