"""
countries.py — Grid point filter by country (Resolution 76 Study 2).

Loads `visualization/data/countries.geojson` (FeatureCollection with `id` in
ISO 3166-1 alpha-3) and provides:

    list_countries() → [{"id": "BRA", "name": "Brazil"}, ...]
    point_in_countries(lat, lon, codes) → bool

Point-in-polygon algorithm: classic ray casting, lightweight (no shapely).
Handles MultiPolygon (list of polygons with optional holes — the first ring
is the exterior; the rest are holes).

Lazy in-memory cache of the GeoJSON.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Sequence

_GEOJSON_PATH = Path(__file__).resolve().parents[2] / "visualization" / "data" / "countries.geojson"

_CACHE: dict | None = None


def _load() -> dict:
    global _CACHE
    if _CACHE is None:
        with _GEOJSON_PATH.open("r", encoding="utf-8") as f:
            _CACHE = json.load(f)
    return _CACHE


def list_countries() -> list[dict]:
    """List all countries in the dataset."""
    data = _load()
    out: list[dict] = []
    for feat in data.get("features", []):
        code = feat.get("id")
        name = feat.get("properties", {}).get("name")
        if code and name:
            out.append({"id": str(code), "name": str(name)})
    out.sort(key=lambda x: x["name"])
    return out


def _point_in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    """Ray casting: point in a closed ring of [lon, lat] coordinates."""
    inside = False
    n = len(ring)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > lat) != (yj > lat)) and (
            lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-30) + xi
        ):
            inside = not inside
        j = i
    return inside


def _point_in_polygon(lon: float, lat: float, polygon: list[list[list[float]]]) -> bool:
    """GeoJSON polygon: first ring = exterior; the rest = holes."""
    if not polygon:
        return False
    if not _point_in_ring(lon, lat, polygon[0]):
        return False
    for hole in polygon[1:]:
        if _point_in_ring(lon, lat, hole):
            return False
    return True


def _point_in_geometry(lon: float, lat: float, geom: dict) -> bool:
    g_type = geom.get("type")
    coords = geom.get("coordinates")
    if not coords:
        return False
    if g_type == "Polygon":
        return _point_in_polygon(lon, lat, coords)
    if g_type == "MultiPolygon":
        for poly in coords:
            if _point_in_polygon(lon, lat, poly):
                return True
        return False
    return False


def point_in_countries(lat: float, lon: float, codes: Sequence[str]) -> bool:
    """Test whether (lat, lon) falls inside any country whose ID is in `codes`.

    `codes` is a list of ISO 3166-1 alpha-3 (case-insensitive). Empty / None
    returns False — use `not codes` beforehand for "the whole globe".

    Normalizes lon to [-180, 180] and lat to [-90, 90].
    """
    if not codes:
        return False
    code_set = {str(c).upper() for c in codes}
    data = _load()

    # Normalize lon to [-180, 180].
    lon_norm = ((float(lon) + 180.0) % 360.0) - 180.0
    lat_norm = max(-90.0, min(90.0, float(lat)))

    for feat in data.get("features", []):
        if str(feat.get("id") or "").upper() not in code_set:
            continue
        geom = feat.get("geometry")
        if geom and _point_in_geometry(lon_norm, lat_norm, geom):
            return True
    return False


def filter_country_codes(codes: Sequence[str] | None) -> list[str]:
    """Validate and normalize a list of codes. Returns [] if invalid/empty."""
    if not codes:
        return []
    valid = {c["id"] for c in list_countries()}
    out: list[str] = []
    for c in codes:
        s = str(c).strip().upper()
        if s in valid and s not in out:
            out.append(s)
    return out
