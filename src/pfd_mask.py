"""
pfd_mask.py — Reading and interpolation of the non-GSO system PFD mask.

Supports two formats:
  1. Simple CSV (angle, pfd)
  2. SRS/ITU XML (the real BR format)
     XML structure:
       <satellite_system>
         <pfd_mask mask_id="1" type="alpha_deltaLongitude"
                   a_name="latitude" b_name="alpha" c_name="deltaLongitude"
                   refbw_khz="40">
           <by_a a="-60">
             <by_b b="-80">
               <pfd c="-80">-145</pfd>
               <pfd c="80">-145</pfd>
             </by_b>
             ...
           </by_a>
           ...
         </pfd_mask>
       </satellite_system>

Reference: ITU-R S.1503-4, Section D.3.1.1 / D5.1.5 and §D6.4.4 (ΔLong), Part D:
  - Latitude table: the one whose latitude is closest to the value computed in the simulation
    (no linear interpolation between distinct latitude planes).
  - Axes (α, Δlong) or (az, el): linear interpolation; outside the tabulated range,
    the last valid value at the edges is used (equivalent to ``clip`` on the b/c axes).
"""

from __future__ import annotations
import csv
import logging
import math
import os
import xml.etree.ElementTree as ET
import numpy as np
from scipy.interpolate import interp1d


logger = logging.getLogger(__name__)


# =====================================================================
#  PFDMask class — Unified interface
# =====================================================================

class PFDMask:
    """Unified interface for a PFD mask.

    Supports querying via:
      - get_pfd(alpha_deg) → for 1D masks
      - get_pfd(alpha_deg, lat_deg, delta_lon_deg) → for 3D masks
    """

    def __init__(self):
        self.mask_type: str = "alpha"
        self.mask_id: int = 0
        self.low_freq_mhz: float = 0.0
        self.high_freq_mhz: float = 0.0
        self.refbw_khz: float = 40.0
        self.sat_name: str = ""
        self.ntc_id: str = ""
        self._dim: int = 1   # 1 or 3

    def get_pfd(self, alpha_deg: float, lat_deg: float = 0.0,
                delta_lon_deg: float = 0.0,
                sat_idx: int | None = None) -> float:
        """Return PFD (dBW/m²/BWref) for the given angles.

        ``sat_idx`` is accepted for compatibility with :class:`PFDMaskMulti`.
        Single-mask implementations ignore this parameter.
        """
        raise NotImplementedError

    def get_pfd_batch(
        self,
        alpha_deg: np.ndarray,
        lat_deg: np.ndarray,
        delta_lon_deg: np.ndarray,
        sat_indices: np.ndarray | None = None,
    ) -> np.ndarray:
        """Interpolate PFD for 1D arrays of equal size (fallback: scalar loop).

        ``sat_indices`` is accepted for compatibility with :class:`PFDMaskMulti`
        and ignored in single-id masks — all satellites use the same curve.
        """
        del sat_indices  # noqa — ignored in single-mask
        alpha_deg = np.asarray(alpha_deg, dtype=np.float64).ravel()
        lat_deg = np.asarray(lat_deg, dtype=np.float64).ravel()
        delta_lon_deg = np.asarray(delta_lon_deg, dtype=np.float64).ravel()
        n = alpha_deg.size
        out = np.empty(n, dtype=np.float64)
        for i in range(n):
            out[i] = self.get_pfd(
                float(alpha_deg[i]),
                float(lat_deg[i]),
                float(delta_lon_deg[i]),
            )
        return out

    def get_pfd_linear(self, alpha_deg: float, lat_deg: float = 0.0,
                       delta_lon_deg: float = 0.0) -> float:
        """Return PFD in linear units (W/m²/BWref)."""
        return 10.0 ** (self.get_pfd(alpha_deg, lat_deg, delta_lon_deg) / 10.0)

    def detect_wcg_theta_symmetry(self, tol_db: float = 1e-3) -> tuple[bool, str]:
        """Return (symmetric, reason) for the θ∈[-90°, +90°] reduction in the WCGA."""
        return False, "mask_type_not_supported"

    def content_hash(self) -> str:
        """Deterministic hash of the mask's **numeric content**.

        Two masks with identical PFD curves (same grid, axes, reference BW)
        must produce the same hash even when registered under distinct
        ``mask_id`` values. This lets the WCGA deduplicate
        ``(a, e, i, mask_content_hash)`` keys, avoiding re-running the search
        for masks that are 100% equal. Subclasses override this; the base
        returns a hash that distinguishes only minimal metadata.
        """
        import hashlib
        h = hashlib.blake2b(digest_size=16)
        h.update(f"{self.mask_type}|{self.refbw_khz:.9g}|{self._dim}".encode("utf-8"))
        return h.hexdigest()


# =====================================================================
#  PFDMask1D — Simple CSV (angle → pfd)
# =====================================================================

class PFDMask1D(PFDMask):
    """1D PFD mask loaded from CSV."""

    def __init__(self, filepath: str, mask_type: str = "alpha"):
        super().__init__()
        self.mask_type = mask_type
        self._dim = 1
        self.angles: list[float] = []
        self.pfd_values: list[float] = []
        self._load(filepath)
        # interp1d (scipy) is kept for external compatibility, but the
        # fast path uses np.interp directly on NumPy arrays.
        self._interp = interp1d(
            self.angles, self.pfd_values,
            kind="linear", bounds_error=False,
            fill_value=(self.pfd_values[0], self.pfd_values[-1])
        )
        self._angles_arr = np.asarray(self.angles, dtype=np.float64)
        self._pfd_arr = np.asarray(self.pfd_values, dtype=np.float64)
        self._fill_left = float(self._pfd_arr[0])
        self._fill_right = float(self._pfd_arr[-1])

    def _load(self, filepath: str):
        with open(filepath, "r") as f:
            reader = csv.reader(f)
            next(reader)  # header
            for row in reader:
                if len(row) >= 2:
                    self.angles.append(float(row[0].strip()))
                    self.pfd_values.append(float(row[1].strip()))

        pairs = sorted(zip(self.angles, self.pfd_values))
        self.angles = [p[0] for p in pairs]
        self.pfd_values = [p[1] for p in pairs]

    def get_pfd(self, alpha_deg: float, lat_deg: float = 0.0,
                delta_lon_deg: float = 0.0,
                sat_idx: int | None = None) -> float:
        del sat_idx  # noqa
        # np.interp is bit-for-bit equivalent to interp1d(linear, fill=edge)
        # when the input data is sorted — which _load guarantees.
        return float(np.interp(
            abs(alpha_deg), self._angles_arr, self._pfd_arr,
            left=self._fill_left, right=self._fill_right,
        ))

    def get_pfd_batch(
        self,
        alpha_deg: np.ndarray,
        lat_deg: np.ndarray,
        delta_lon_deg: np.ndarray,
        sat_indices: np.ndarray | None = None,
    ) -> np.ndarray:
        del sat_indices  # noqa — ignored in single-mask
        a = np.abs(np.asarray(alpha_deg, dtype=np.float64).ravel())
        return np.interp(
            a, self._angles_arr, self._pfd_arr,
            left=self._fill_left, right=self._fill_right,
        )

    def __repr__(self):
        return (f"PFDMask1D(type={self.mask_type}, "
                f"angles=[{self.angles[0]:.1f}..{self.angles[-1]:.1f}]°, "
                f"pfd=[{self.pfd_values[0]:.1f}..{self.pfd_values[-1]:.1f}] dBW/m²)")

    def detect_wcg_theta_symmetry(self, tol_db: float = 1e-3) -> tuple[bool, str]:
        return True, "mask_1d_depends_only_on_alpha"

    def content_hash(self) -> str:
        import hashlib
        h = hashlib.blake2b(digest_size=16)
        h.update(b"1d|")
        h.update(f"{self.refbw_khz:.9g}|".encode())
        h.update(self._angles_arr.tobytes())
        h.update(self._pfd_arr.tobytes())
        h.update(f"|{self._fill_left:.9g}|{self._fill_right:.9g}".encode())
        return h.hexdigest()


# =====================================================================
#  PFDMaskXML — SRS/ITU XML format (3D: latitude × alpha × deltaLon)
# =====================================================================

class PFDMaskXML(PFDMask):
    """3D PFD mask loaded from XML (SRS/BR format).

    Axes:
      a (by_a) → latitude of the subsatellite point (°)
      b (by_b) → alpha angle (°) or another angle depending on the mask type
      c (pfd)  → deltaLongitude (°) = LongAlpha − LongNGSO (ITU-R S.1503 §D6.4.4)

    ITU-R S.1503 rule (Part D): the table whose latitude is closest to the
    simulation value is selected; within that table, linear interpolation is
    applied on the other axes, using the edge value outside the tabulated range.
    """

    def __init__(self, filepath: str, mask_id: int | None = None):
        super().__init__()
        self._dim = 3
        self._lat_vals: np.ndarray = np.array([])
        self._alpha_vals: np.ndarray = np.array([])
        self._dlon_vals: np.ndarray = np.array([])
        self._pfd_grid: np.ndarray = np.array([])
        self._wcg_query_mirror_axis: str | None = None
        self._wcg_query_mirror_sign: float = 1.0
        self._wcg_theta_symmetry_cache: tuple[bool, str] | None = None
        self._load(filepath, mask_id)

    @classmethod
    def from_xml_content(cls, xml_content: str | bytes, mask_id: int | None = None) -> "PFDMaskXML":
        """Create an XML PFD mask from in-memory content."""
        self = cls.__new__(cls)
        PFDMask.__init__(self)
        self._dim = 3
        self._lat_vals = np.array([])
        self._alpha_vals = np.array([])
        self._dlon_vals = np.array([])
        self._pfd_grid = np.array([])
        self._wcg_query_mirror_axis = None
        self._wcg_query_mirror_sign = 1.0
        self._wcg_theta_symmetry_cache = None
        self._load_from_content(xml_content, mask_id)
        return self

    def _load(self, filepath: str, mask_id: int | None):
        tree = ET.parse(filepath)
        root = tree.getroot()
        self._load_from_root(root, mask_id)

    def _load_from_content(self, xml_content: str | bytes, mask_id: int | None):
        if isinstance(xml_content, bytes):
            try:
                xml_text = xml_content.decode("utf-8-sig")
            except UnicodeDecodeError:
                xml_text = xml_content.decode("latin1", errors="replace")
        else:
            xml_text = xml_content
        root = ET.fromstring(xml_text)
        self._load_from_root(root, mask_id)

    def _load_from_root(self, root: ET.Element, mask_id: int | None):
        # satellite_system attributes
        self.ntc_id = root.get("ntc_id", "")
        self.sat_name = root.get("sat_name", "")

        # Find the correct PFD mask
        masks = root.findall("pfd_mask")
        mask_elem = None
        for m in masks:
            if mask_id is not None:
                if int(m.get("mask_id", "-1")) == mask_id:
                    mask_elem = m
                    break
            else:
                mask_elem = m
                break

        if mask_elem is None:
            raise ValueError(
                f"PFD mask mask_id={mask_id} not found in the XML."
            )

        self.mask_id = int(mask_elem.get("mask_id", "0"))
        self.low_freq_mhz = float(mask_elem.get("low_freq_mhz", "0"))
        self.high_freq_mhz = float(mask_elem.get("high_freq_mhz", "0"))
        self.mask_type = mask_elem.get("type", "alpha_deltaLongitude")
        self.refbw_khz = float(mask_elem.get("refbw_khz", "40"))

        self.a_name = mask_elem.get("a_name", "latitude")
        self.b_name = mask_elem.get("b_name", "alpha")
        self.c_name = mask_elem.get("c_name", "deltaLongitude")

        # Extract data: a (latitude) → b (alpha) → c (deltaLon) → PFD
        # We store the c→PFD profile of each (a,b) pair separately so we can
        # later interpolate onto the global grid without using erroneous
        # global averages.
        lat_set: set[float] = set()
        alpha_set: set[float] = set()
        dlon_set: set[float] = set()
        # raw_ab[(a_val, b_val)] = sorted list of (c_val, pfd_val)
        raw_ab: dict[tuple[float, float], list[tuple[float, float]]] = {}

        for by_a in mask_elem.findall("by_a"):
            a_val = float(by_a.get("a", "0"))
            lat_set.add(a_val)
            for by_b in by_a.findall("by_b"):
                b_val = float(by_b.get("b", "0"))
                alpha_set.add(b_val)
                cp: list[tuple[float, float]] = []
                for pfd_elem in by_b.findall("pfd"):
                    c_val = float(pfd_elem.get("c", "0"))
                    pfd_val = float(pfd_elem.text.strip())
                    dlon_set.add(c_val)
                    cp.append((c_val, pfd_val))
                raw_ab[(a_val, b_val)] = sorted(cp, key=lambda x: x[0])

        # Sort global axes
        self._lat_vals = np.array(sorted(lat_set))
        self._alpha_vals = np.array(sorted(alpha_set))
        self._dlon_vals = np.array(sorted(dlon_set))

        n_lat = len(self._lat_vals)
        n_alpha = len(self._alpha_vals)
        n_dlon = len(self._dlon_vals)

        self._pfd_grid = np.full((n_lat, n_alpha, n_dlon), np.nan)

        lat_idx = {v: i for i, v in enumerate(self._lat_vals)}
        alpha_idx = {v: i for i, v in enumerate(self._alpha_vals)}

        # Fill each row (i, j, :) by interpolating the c→PFD profile defined
        # for that (a, b) pair onto all points of the global c axis.
        # np.interp uses nearest-neighbor at the ends (edge fill_value).
        for (a_val, b_val), cp in raw_ab.items():
            if not cp:
                continue
            i = lat_idx[a_val]
            j = alpha_idx[b_val]
            c_pts = np.array([x[0] for x in cp])
            pfd_pts = np.array([x[1] for x in cp])
            self._pfd_grid[i, j, :] = np.interp(
                self._dlon_vals, c_pts, pfd_pts,
                left=pfd_pts[0], right=pfd_pts[-1],
            )

        # Handle any (a,b) pairs with no data at all in the XML. Missing
        # cells are NaN; very low explicit values (e.g. -1000) are mask data
        # and must be preserved.
        if np.any(np.isnan(self._pfd_grid)):
            for i in range(n_lat):
                for k in range(n_dlon):
                    col = self._pfd_grid[i, :, k]
                    valid = ~np.isnan(col)
                    missing = ~valid
                    if not np.any(missing):
                        continue
                    if np.any(valid):
                        col[missing] = np.interp(
                            self._alpha_vals[missing],
                            self._alpha_vals[valid],
                            col[valid],
                            left=col[valid][0],
                            right=col[valid][-1],
                        )
                    else:
                        col[missing] = -1000.0

        self._configure_wcg_symmetry_helpers()

    def _configure_wcg_symmetry_helpers(self) -> None:
        # S.1503 D5 states that PFD masks are not assumed symmetric in
        # azimuth/elevation/alpha/deltaLong. A one-sided XML axis therefore means
        # out-of-range queries are clamped to the nearest valid edge, not mirrored.
        self._wcg_query_mirror_axis = None
        self._wcg_query_mirror_sign = 1.0

    @staticmethod
    def _prepare_axis(values: np.ndarray, axis: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return lower/upper indices and linear weights for a monotonic axis."""
        values = np.asarray(values, dtype=np.float64).ravel()
        axis = np.asarray(axis, dtype=np.float64)
        if axis.size == 1:
            idx = np.zeros(values.shape[0], dtype=np.int64)
            weights = np.zeros(values.shape[0], dtype=np.float64)
            return idx, idx, weights

        clipped = np.clip(values, axis[0], axis[-1])
        idx_hi = np.searchsorted(axis, clipped, side="right")
        idx_hi = np.clip(idx_hi, 1, axis.size - 1)
        idx_lo = idx_hi - 1
        lo = axis[idx_lo]
        hi = axis[idx_hi]
        denom = hi - lo
        weights = np.zeros_like(clipped, dtype=np.float64)
        valid = np.abs(denom) > 1e-12
        weights[valid] = (clipped[valid] - lo[valid]) / denom[valid]
        return idx_lo, idx_hi, weights

    def _normalize_queries(
        self,
        alpha_deg: np.ndarray | float,
        lat_deg: np.ndarray | float,
        delta_lon_deg: np.ndarray | float,
        *,
        apply_mirror: bool = False,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        alpha_deg = np.asarray(alpha_deg, dtype=np.float64).ravel()
        lat_deg = np.asarray(lat_deg, dtype=np.float64).ravel()
        delta_lon_deg = np.asarray(delta_lon_deg, dtype=np.float64).ravel()
        dlon = ((delta_lon_deg + 180.0) % 360.0) - 180.0
        dlon = np.where((dlon == -180.0) & (delta_lon_deg > 0.0), 180.0, dlon)
        if apply_mirror and self._wcg_query_mirror_axis == "b":
            alpha_deg = self._wcg_query_mirror_sign * np.abs(alpha_deg)
        if apply_mirror and self._wcg_query_mirror_axis == "c":
            dlon = self._wcg_query_mirror_sign * np.abs(dlon)
        return alpha_deg, lat_deg, dlon

    def _nearest_lat_indices(self, lat_q: np.ndarray) -> np.ndarray:
        """Index of the closest tabulated latitude (S.1503: nearest {Latitude} table).

        Ties in |Δlat| are resolved by the lowest index (first latitude in the sorted grid).
        """
        lat_q = np.asarray(lat_q, dtype=np.float64).ravel()
        if self._lat_vals.size == 0:
            raise ValueError("PFD mask has no latitude values")
        if self._lat_vals.size == 1:
            return np.zeros(lat_q.shape[0], dtype=np.int64)
        d = np.abs(lat_q[:, np.newaxis] - self._lat_vals[np.newaxis, :])
        return np.argmin(d, axis=1).astype(np.int64)

    def _interp_nearest_lat_bilinear_batch(
        self,
        alpha_deg: np.ndarray | float,
        lat_deg: np.ndarray | float,
        delta_lon_deg: np.ndarray | float,
        *,
        apply_mirror: bool = False,
    ) -> np.ndarray:
        """Bilinear interpolation in (α, Δlong) on the plane of the nearest tabulated latitude."""
        alpha_q, lat_q, dlon_q = self._normalize_queries(
            alpha_deg,
            lat_deg,
            delta_lon_deg,
            apply_mirror=apply_mirror,
        )
        i_lat = self._nearest_lat_indices(lat_q)
        alpha_lo, alpha_hi, w_alpha = self._prepare_axis(alpha_q, self._alpha_vals)
        dlon_lo, dlon_hi, w_dlon = self._prepare_axis(dlon_q, self._dlon_vals)

        c000 = self._pfd_grid[i_lat, alpha_lo, dlon_lo]
        c001 = self._pfd_grid[i_lat, alpha_lo, dlon_hi]
        c010 = self._pfd_grid[i_lat, alpha_hi, dlon_lo]
        c011 = self._pfd_grid[i_lat, alpha_hi, dlon_hi]

        c00 = c000 + (c001 - c000) * w_dlon
        c01 = c010 + (c011 - c010) * w_dlon
        return np.asarray(c00 + (c01 - c00) * w_alpha, dtype=np.float64)

    def get_pfd(self, alpha_deg: float, lat_deg: float = 0.0,
                delta_lon_deg: float = 0.0,
                sat_idx: int | None = None) -> float:
        """Interpolate PFD for (subsatellite latitude, α or az, Δlong or el).

        Latitude: nearest table (ITU-R S.1503). Axes b/c: linear + edge (last valid value).
        """
        del sat_idx  # noqa
        return float(
            self._interp_nearest_lat_bilinear_batch(alpha_deg, lat_deg, delta_lon_deg)[0]
        )

    def get_pfd_batch(
        self,
        alpha_deg: np.ndarray,
        lat_deg: np.ndarray,
        delta_lon_deg: np.ndarray,
        sat_indices: np.ndarray | None = None,
    ) -> np.ndarray:
        """Batch version with the same normative rule as ``get_pfd``."""
        del sat_indices  # noqa — ignored in single-mask
        return self._interp_nearest_lat_bilinear_batch(
            alpha_deg,
            lat_deg,
            delta_lon_deg,
            apply_mirror=False,
        )

    def _detect_theta_symmetry_via_sampling(
        self,
        mirror_axis: str,
        tol_db: float,
    ) -> tuple[bool, str]:
        eps = 1e-9
        if mirror_axis == "c":
            mirror_vals = np.unique(np.abs(self._dlon_vals[np.abs(self._dlon_vals) > eps]))
            if mirror_vals.size == 0:
                return True, "deltaLongitude_axis_degenerate"
            for lat in self._lat_vals:
                alpha_q = np.repeat(self._alpha_vals, mirror_vals.size)
                lat_q = np.full(alpha_q.shape, lat, dtype=np.float64)
                dlon_q = np.tile(mirror_vals, self._alpha_vals.size)
                pos = self._interp_nearest_lat_bilinear_batch(alpha_q, lat_q, dlon_q, apply_mirror=False)
                neg = self._interp_nearest_lat_bilinear_batch(alpha_q, lat_q, -dlon_q, apply_mirror=False)
                max_err = float(np.max(np.abs(pos - neg))) if pos.size else 0.0
                if max_err > tol_db:
                    return False, f"deltaLongitude_asymmetry_max_err={max_err:.6f}dB"
            return True, "deltaLongitude_symmetry_verified"

        if mirror_axis == "b":
            mirror_vals = np.unique(np.abs(self._alpha_vals[np.abs(self._alpha_vals) > eps]))
            if mirror_vals.size == 0:
                return True, "azimuth_axis_degenerate"
            for lat in self._lat_vals:
                az_q = np.repeat(mirror_vals, self._dlon_vals.size)
                lat_q = np.full(az_q.shape, lat, dtype=np.float64)
                el_q = np.tile(self._dlon_vals, mirror_vals.size)
                pos = self._interp_nearest_lat_bilinear_batch(az_q, lat_q, el_q, apply_mirror=False)
                neg = self._interp_nearest_lat_bilinear_batch(-az_q, lat_q, el_q, apply_mirror=False)
                max_err = float(np.max(np.abs(pos - neg))) if pos.size else 0.0
                if max_err > tol_db:
                    return False, f"azimuth_asymmetry_max_err={max_err:.6f}dB"
            return True, "azimuth_symmetry_verified"

        return False, "mask_type_not_supported"

    def detect_wcg_theta_symmetry(self, tol_db: float = 1e-3) -> tuple[bool, str]:
        if self._wcg_theta_symmetry_cache is not None:
            return self._wcg_theta_symmetry_cache

        mirror_axis = None
        if self.mask_type == "alpha_deltaLongitude":
            mirror_axis = "c"
        elif self.mask_type == "azimuth_elevation":
            mirror_axis = "b"
        else:
            self._wcg_theta_symmetry_cache = (False, f"mask_type={self.mask_type}_not_supported")
            return self._wcg_theta_symmetry_cache

        self._wcg_theta_symmetry_cache = self._detect_theta_symmetry_via_sampling(mirror_axis, tol_db)
        return self._wcg_theta_symmetry_cache

    @property
    def lat_range(self) -> tuple[float, float]:
        return float(self._lat_vals[0]), float(self._lat_vals[-1])

    @property
    def alpha_range(self) -> tuple[float, float]:
        return float(self._alpha_vals[0]), float(self._alpha_vals[-1])

    @property
    def dlon_range(self) -> tuple[float, float]:
        return float(self._dlon_vals[0]), float(self._dlon_vals[-1])

    def content_hash(self) -> str:
        import hashlib
        h = hashlib.blake2b(digest_size=16)
        h.update(f"xml3d|{self.mask_type}|{self.refbw_khz:.9g}|".encode())
        h.update(np.asarray(self._lat_vals, dtype=np.float64).tobytes())
        h.update(np.asarray(self._alpha_vals, dtype=np.float64).tobytes())
        h.update(np.asarray(self._dlon_vals, dtype=np.float64).tobytes())
        h.update(np.asarray(self._pfd_grid, dtype=np.float64).tobytes())
        h.update(
            f"|{self._wcg_query_mirror_axis}|{self._wcg_query_mirror_sign:.9g}".encode()
        )
        return h.hexdigest()

    def __repr__(self) -> str:
        return (
            f"PFDMaskXML(sat={self.sat_name}, mask_id={self.mask_id}, "
            f"type={self.mask_type}, "
            f"freq={self.low_freq_mhz:.0f}-{self.high_freq_mhz:.0f} MHz, "
            f"refbw={self.refbw_khz:.0f} kHz, "
            f"lat=[{self._lat_vals[0]:.0f}..{self._lat_vals[-1]:.0f}]°, "
            f"alpha=[{self._alpha_vals[0]:.0f}..{self._alpha_vals[-1]:.0f}]°, "
            f"dlon=[{self._dlon_vals[0]:.0f}..{self._dlon_vals[-1]:.0f}]°)"
        )

    def to_dict(self) -> dict:
        """Export mask data for external use (JSON)."""
        return {
            "type": self.mask_type,
            "mask_id": self.mask_id,
            "axes": {
                "a": self._lat_vals.tolist(),
                "b": self._alpha_vals.tolist(),
                "c": self._dlon_vals.tolist(),
                # Legacy keys for backward compatibility
                "lat": self._lat_vals.tolist(),
                "alpha": self._alpha_vals.tolist(),
                "dlon": self._dlon_vals.tolist(),
            },
            "axis_names": {
                "a": self.a_name,
                "b": self.b_name,
                "c": self.c_name,
            },
            "values": self._pfd_grid.flatten().tolist(), # Flatten for easier JSON
            "shape": self._pfd_grid.shape,
        }


# =====================================================================
#  Factory — loads automatically based on extension
# =====================================================================

def load_pfd_mask(filepath: str, mask_type: str = "alpha",
                  mask_id: int | None = None) -> PFDMask:
    """Load a PFD mask from a CSV or XML file.

    Automatic detection by extension:
      .xml → PFDMaskXML (3D, SRS/BR format)
      .csv → PFDMask1D (1D)
    """
    ext = os.path.splitext(filepath)[1].lower()
    if ext == ".xml":
        return PFDMaskXML(filepath, mask_id=mask_id)
    else:
        return PFDMask1D(filepath, mask_type=mask_type)


def load_pfd_mask_from_xml_content(xml_content: str | bytes,
                                   mask_id: int | None = None) -> PFDMask:
    """Load a PFD mask from in-memory XML."""
    return PFDMaskXML.from_xml_content(xml_content, mask_id=mask_id)


# =====================================================================
#  PFDMaskMulti — wrapper for systems with a distinct mask_id per orbit
# =====================================================================

class PFDMaskMulti(PFDMask):
    """Poly-mask wrapper for S.1503-4 scenarios with a ``mask_id`` per orbit.

    Each satellite in the constellation queries its own mask via
    :attr:`mask_id_per_sat` (positionally aligned with the ``constellation``).
    Backwards compatibility with the :class:`PFDMask` interface is preserved —
    all consumers (calculator and WCGA) keep calling
    ``get_pfd_batch(alpha, lat, dlon, sat_indices=idx_k)``; when
    ``sat_indices`` is passed, the call is partitioned by ``mask_id`` and each
    slice evaluates the corresponding mask.

    Derived attributes (refbw_khz, ranges, metadata) are taken from the
    *primary* mask — chosen as the most frequent one in ``mask_id_per_sat``
    or, in a tie, the one with the lowest ``mask_id``. This guarantees a
    consistent reference BW for the global ``bw_correction_db`` correction;
    masks with BW differing from one another require future PRs (a rare case
    in real filings).
    """

    def __init__(
        self,
        masks_by_id: dict[int, PFDMask],
        mask_id_per_sat: list[int] | np.ndarray,
    ) -> None:
        super().__init__()
        if not masks_by_id:
            raise ValueError("PFDMaskMulti requires at least 1 mask in masks_by_id.")
        self._masks_by_id: dict[int, PFDMask] = {int(k): v for k, v in masks_by_id.items()}
        self._mask_id_per_sat: np.ndarray = np.asarray(mask_id_per_sat, dtype=np.int64).ravel()
        if self._mask_id_per_sat.size == 0:
            raise ValueError("PFDMaskMulti requires a non-empty mask_id_per_sat.")
        # Validation: every mask_id in mask_id_per_sat must exist in masks_by_id
        # (the -1 sentinel is tolerated for satellites without a mapping — uses primary).
        unique_ids = set(int(x) for x in np.unique(self._mask_id_per_sat))
        unknown = unique_ids - {-1} - set(self._masks_by_id.keys())
        if unknown:
            raise ValueError(
                f"mask_id(s) {sorted(unknown)} referenced by mask_id_per_sat were not provided in masks_by_id."
            )

        # Mixed coordinate geometries across sub-masks are supported, but only
        # when every sub-mask has the same dimensionality. The EPFD↓ engine
        # routes each satellite through its OWN sub-mask's coordinate system
        # (alpha/Δλ vs azimuth/elevation) — see ``is_mixed_geometry`` and the
        # per-satellite query paths in epfd_calculator / wcg_search. Mixing 1D
        # with 3D masks is rejected (the 1D query carries no lat/second axis, so
        # there is no coherent per-sat dispatch).
        types = {str(getattr(m, "mask_type", "")) for m in self._masks_by_id.values()}
        dims = {int(getattr(m, "_dim", 1)) for m in self._masks_by_id.values()}
        if len(dims) > 1:
            raise NotImplementedError(
                "PFDMaskMulti cannot mix 1D and 3D sub-masks; "
                f"got dim(s)={sorted(dims)}."
            )
        # True when sub-masks use different coordinate systems (e.g. method_3
        # fuses an azimuth_elevation filing with an alpha_deltaLongitude one).
        self.is_mixed_geometry: bool = len(types) > 1
        self._submask_types: set[str] = types

        # Primary mask choice: the one with the most satellites; tie → lowest mask_id.
        from collections import Counter
        counts = Counter(int(x) for x in self._mask_id_per_sat if int(x) != -1)
        if counts:
            top = max(counts.values())
            primary_id = min(mid for mid, c in counts.items() if c == top)
        else:
            primary_id = min(self._masks_by_id.keys())
        self._primary_id: int = int(primary_id)
        primary = self._masks_by_id[self._primary_id]

        # Inherit metadata from the primary mask to satisfy callers (refbw, dim, etc.).
        self.mask_type = primary.mask_type
        self.mask_id = self._primary_id
        self.low_freq_mhz = primary.low_freq_mhz
        self.high_freq_mhz = primary.high_freq_mhz
        self.refbw_khz = primary.refbw_khz
        self.sat_name = primary.sat_name
        self.ntc_id = primary.ntc_id
        self._dim = primary._dim

    @property
    def primary_mask_id(self) -> int:
        return self._primary_id

    @property
    def primary_mask(self) -> PFDMask:
        return self._masks_by_id[self._primary_id]

    @property
    def mask_id_per_sat(self) -> np.ndarray:
        return self._mask_id_per_sat

    @property
    def masks_by_id(self) -> dict[int, PFDMask]:
        return self._masks_by_id

    def mask_for_sat(self, sat_idx: int) -> PFDMask:
        mid = int(self._mask_id_per_sat[int(sat_idx)])
        if mid == -1:
            return self._masks_by_id[self._primary_id]
        return self._masks_by_id[mid]

    def is_azel_per_sat(self, sat_indices: np.ndarray) -> np.ndarray:
        """Boolean ``(len(sat_indices),)``: True where the satellite's OWN
        sub-mask is ``azimuth_elevation``.

        Used by the vectorized EPFD↓ accumulator to route each satellite through
        its own coordinate system in a mixed-geometry fusion (``method_3``).
        The ``-1`` sentinel (no per-sat mapping) falls back to the primary mask.
        """
        sat_indices = np.asarray(sat_indices, dtype=np.int64).ravel()
        azel_ids = {
            int(mid) for mid, m in self._masks_by_id.items()
            if str(getattr(m, "mask_type", "")) == "azimuth_elevation"
        }
        mids = self._mask_id_per_sat[sat_indices]
        out = np.isin(mids, list(azel_ids)) if azel_ids else np.zeros(mids.shape, dtype=bool)
        # -1 → primary mask's type.
        prim_azel = str(getattr(self, "mask_type", "")) == "azimuth_elevation"
        out[mids == -1] = prim_azel
        return out

    def get_pfd(
        self,
        alpha_deg: float,
        lat_deg: float = 0.0,
        delta_lon_deg: float = 0.0,
        sat_idx: int | None = None,
    ) -> float:
        if sat_idx is None:
            return self.primary_mask.get_pfd(alpha_deg, lat_deg, delta_lon_deg)
        return self.mask_for_sat(int(sat_idx)).get_pfd(alpha_deg, lat_deg, delta_lon_deg)

    def get_pfd_batch(
        self,
        alpha_deg: np.ndarray,
        lat_deg: np.ndarray,
        delta_lon_deg: np.ndarray,
        sat_indices: np.ndarray | None = None,
    ) -> np.ndarray:
        alpha_deg = np.asarray(alpha_deg, dtype=np.float64).ravel()
        lat_deg = np.asarray(lat_deg, dtype=np.float64).ravel()
        delta_lon_deg = np.asarray(delta_lon_deg, dtype=np.float64).ravel()
        n = alpha_deg.size
        if n == 0:
            return np.empty(0, dtype=np.float64)
        if sat_indices is None:
            # No per-satellite identification — uses the primary mask (back-compat
            # for callers not yet migrated). In a real multi-mask scenario,
            # propagating sat_indices is mandatory for the normative correction.
            return self.primary_mask.get_pfd_batch(alpha_deg, lat_deg, delta_lon_deg)

        sat_indices = np.asarray(sat_indices, dtype=np.int64).ravel()
        if sat_indices.size != n:
            raise ValueError(
                f"sat_indices size ({sat_indices.size}) ≠ batch size ({n})."
            )
        mids = self._mask_id_per_sat[sat_indices]
        out = np.empty(n, dtype=np.float64)
        # Iterate over each unique mask_id: 1 vectorized call per subset.
        for mid in np.unique(mids):
            sel = mids == mid
            if not np.any(sel):
                continue
            mid_int = int(mid)
            mask = self._masks_by_id.get(
                mid_int,
                self._masks_by_id[self._primary_id] if mid_int == -1 else None,
            )
            if mask is None:
                raise KeyError(f"PFDMaskMulti: mask_id={mid_int} missing in masks_by_id.")
            out[sel] = mask.get_pfd_batch(
                alpha_deg[sel], lat_deg[sel], delta_lon_deg[sel],
            )
        return out

    def detect_wcg_theta_symmetry(self, tol_db: float = 1e-3) -> tuple[bool, str]:
        """θ symmetry holds for the set **only if** it holds for every mask.

        Each satellite uses its own mask; the WCGA reduces θ to the half-circle
        only when all masks exhibit the same normative symmetry.
        """
        all_sym = True
        reasons: list[str] = []
        for mid, mask in self._masks_by_id.items():
            sym, why = mask.detect_wcg_theta_symmetry(tol_db=tol_db)
            reasons.append(f"mask_id={mid}:{'sym' if sym else 'asym'}({why})")
            if not sym:
                all_sym = False
        return all_sym, " ; ".join(reasons)

    def __repr__(self) -> str:
        return (
            f"PFDMaskMulti(primary_mask_id={self._primary_id}, "
            f"mask_ids={sorted(self._masks_by_id.keys())}, "
            f"sats={self._mask_id_per_sat.size})"
        )
