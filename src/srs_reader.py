"""
srs_reader.py — Reader for SRS (Space Radio Service) MDB orbital data.

Extracts NGSO system parameters from the SRS/ITU .MDB file,
including:
  • Orbital parameters (table `orbit`)
  • System information (table `non_geo`)
  • Mask information (table `mask_info`)

Reads the MDB (Access/JET) format directly in pure Python via
``access_parser`` — no external executable (mdbtools) or ODBC driver.

Orbital data format (table `orbit`):
  ntc_id, orb_id, nbr_sat_pl, right_asc, inclin_ang,
  prd_ddd, prd_hh, prd_mm,  ← orbital period
  apog, apog_exp, perig, perig_exp, perig_arg,
  op_ht, op_ht_exp,
  f_stn_keep, rpt_prd_dd, rpt_prd_hh, rpt_prd_mm, rpt_prd_ss,
  f_precess, precession, long_asc, keep_rnge, f_sunsynch, ...
"""

from __future__ import annotations
import io
import math
import os
import logging
import zipfile
from datetime import datetime
from dataclasses import dataclass, field

try:
    from access_parser import AccessParser
    HAS_ACCESS_PARSER = True
except ImportError:
    HAS_ACCESS_PARSER = False

from .constants import RE_KM, MU_KM3_S2, DEG2RAD

logger = logging.getLogger(__name__)


@dataclass
class SRSOrbitPlane:
    """Data for one SRS orbital plane."""
    orb_id: int
    nbr_sat_pl: int             # satellites per plane
    right_asc_deg: float        # RAAN (°)
    inclin_deg: float           # inclination (°)
    period_s: float             # orbital period (s)
    apogee_km: float            # apogee (km altitude)
    perigee_km: float           # perigee (km altitude)
    perigee_arg_deg: float      # argument of perigee (°)
    op_height_km: float         # operational height (km)
    f_stn_keep: bool            # station keeping flag
    rpt_period_s: float         # repeat period (s)
    f_precess: bool             # precession flag
    precession_deg_day: float   # precession rate (°/day)
    long_asc_deg: float         # longitude of the ascending node (°)
    keep_range_deg: float       # station keeping range (°)
    f_sun_synch: bool           # sun-synchronous flag
    # AP4 A.4.b.3.d — mutually-exclusive configuration subset this plane
    # belongs to (0 = not declared / single-config filing).
    orbit_set_id: int = 0

    @property
    def semi_major_axis_km(self) -> float:
        """Compute the semi-major axis from the period."""
        T = self.period_s
        a_cubed = MU_KM3_S2 * (T / (2.0 * math.pi))**2
        return a_cubed ** (1.0 / 3.0)

    @property
    def altitude_km(self) -> float:
        """Mean altitude."""
        return (self.apogee_km + self.perigee_km) / 2.0

    @property
    def eccentricity(self) -> float:
        """Eccentricity from apogee and perigee."""
        r_a = self.apogee_km + RE_KM
        r_p = self.perigee_km + RE_KM
        if r_a + r_p < 1:
            return 0.0
        return (r_a - r_p) / (r_a + r_p)


@dataclass
class SRSNonGeoSystem:
    """Data for the SRS NGSO system."""
    ntc_id: str
    sat_name: str
    ref_body: str               # "T" = Earth
    nbr_planes: int             # number of orbital planes
    nbr_sat_total: int          # nbr_sat_td (total active sats, density field — ≠ sats/plane)
    density: float              # density (sats/km²)
    avg_dist_km: float          # average distance between satellites
    f_x_zone: bool              # exclusion zone flag
    x_zone_deg: float           # exclusion zone angle (°)
    f_constellation: bool       # constellation flag
    # AP4 A.4.b.3.b — 'S' = single configuration, 'M' = multiple mutually-
    # exclusive configurations ('' on legacy filings without the field).
    multi_config_type: str = ""
    # AP4 A.4.b.3.c — number of mutually-exclusive subsets declared.
    nbr_config: int = 0
    orbit_planes: list[SRSOrbitPlane] = field(default_factory=list)
    # Official initial phases per orbit/satellite, when available in the phase table:
    # phase_by_orbit[orb_id][orb_sat_id] = phase_ang (degrees)
    phase_by_orbit: dict[int, dict[int, float]] = field(default_factory=dict)


def _read_manual_srs_yaml(path: str, ntc_id: "str | None" = None) -> "SRSNonGeoSystem":
    """Build an :class:`SRSNonGeoSystem` from a manual-system YAML (R3/R4).

    The YAML is the ``config.example.yaml``/Manual-System schema (``label`` +
    ``non_gso`` with ``planes:`` or the Walker shortcut). This makes a
    registered manual filing readable by every consumer of ``read_srs_mdb``
    (Constellation viewer, workers, inspectors) without an Access db —
    writing real JET/MDB files is not possible on this stack (the parsers
    are read-only), so the SRS side of a manual pair is carried as YAML.
    """
    import yaml as _yaml

    with open(path, "r", encoding="utf-8") as fh:
        data = _yaml.safe_load(fh) or {}
    ng = dict(data.get("non_gso") or {})
    label = str(data.get("label") or "manual system")

    planes_in = list(ng.get("planes") or ng.get("_planes") or [])
    if not planes_in:
        # Walker shortcut → synthesize the plane list (RAAN over 360°).
        n_pl = int(ng.get("num_planes") or 1)
        spp = int(ng.get("sats_per_plane") or 1)
        f = int(ng.get("inter_plane_phasing_factor") or 1)
        d_raan = 360.0 / max(1, n_pl)
        d_m = 360.0 / max(1, spp)
        d_ph = f * 360.0 / max(1, n_pl * spp)
        planes_in = [
            {
                "orb_id": p + 1, "sats_per_plane": spp,
                "inclination_deg": ng.get("inclination_deg", 0.0),
                "raan_deg": float(ng.get("raan0_deg", 0.0)) + p * d_raan,
                "perigee_arg_deg": ng.get("arg_perigee_deg", 0.0),
                "semi_major_axis_km": ng.get("semi_major_axis_km"),
                "eccentricity": ng.get("eccentricity", 0.0),
                "apogee_km": ng.get("apogee_km"),
                "perigee_km": ng.get("perigee_km"),
                "phase_angles_deg": [(s * d_m + p * d_ph) % 360.0
                                     for s in range(spp)],
            }
            for p in range(n_pl)
        ]

    alpha0 = float(ng.get("alpha0_deg") or 0.0)
    system = SRSNonGeoSystem(
        ntc_id=str(ntc_id or data.get("ntc_id") or ""),
        sat_name=label,
        ref_body="T",
        nbr_planes=len(planes_in),
        nbr_sat_total=sum(int(p.get("sats_per_plane", 0) or 0)
                          for p in planes_in),
        density=0.0, avg_dist_km=0.0,
        f_x_zone=alpha0 > 0.0, x_zone_deg=alpha0,
        f_constellation=True,
    )

    h_min = float(ng.get("min_operating_height_km") or 0.0)
    for k, pl in enumerate(planes_in, start=1):
        a = pl.get("semi_major_axis_km") or ng.get("semi_major_axis_km")
        e = float(pl.get("eccentricity", ng.get("eccentricity", 0.0)) or 0.0)
        if not a and pl.get("apogee_km") is not None \
                and pl.get("perigee_km") is not None:
            ha, hp = float(pl["apogee_km"]), float(pl["perigee_km"])
            a = RE_KM + (ha + hp) / 2.0          # §D6.3.7
            e = (ha - hp) / (2.0 * a)
        if not a:
            raise ValueError(f"manual plane {k}: no orbit size "
                             "(semi_major_axis_km or apogee/perigee)")
        a = float(a)
        period_s = 2.0 * math.pi * math.sqrt(a ** 3 / MU_KM3_S2)
        raan = float(pl.get("raan_deg", 0.0) or 0.0)
        orb_id = int(pl.get("orb_id", k) or k)
        plane = SRSOrbitPlane(
            orb_id=orb_id,
            nbr_sat_pl=int(pl.get("sats_per_plane", 1) or 1),
            right_asc_deg=raan,
            inclin_deg=float(pl.get("inclination_deg",
                                    ng.get("inclination_deg", 0.0)) or 0.0),
            period_s=period_s,
            apogee_km=a * (1.0 + e) - RE_KM,
            perigee_km=a * (1.0 - e) - RE_KM,
            perigee_arg_deg=float(pl.get("perigee_arg_deg", 0.0) or 0.0),
            op_height_km=h_min,
            f_stn_keep=False, rpt_period_s=0.0,
            f_precess=False, precession_deg_day=0.0,
            # long_asc = RAAN → inferred GMST0 = 0 (manual epoch).
            long_asc_deg=raan,
            keep_range_deg=0.0, f_sun_synch=False,
        )
        system.orbit_planes.append(plane)
        phases = pl.get("phase_angles_deg")
        if isinstance(phases, list) and phases:
            system.phase_by_orbit[orb_id] = {
                s + 1: float(ph) for s, ph in enumerate(phases)
                if ph is not None
            }

    logger.info("Manual SRS (YAML): %s — %d plane(s), %d sat(s)",
                label, len(system.orbit_planes), system.nbr_sat_total)
    return system


def detect_orbit_config_raw(
    mdb_path: str,
    multi_config_type: str,
    nbr_config: int,
    orbit_set_ids: "set[int] | list[int]",
) -> dict:
    """Low-level configuration-label cascade (R2 / AP4 A.4.b.3.b-d).

    One SRS ``.mdb`` always holds exactly ONE configuration; operators label it
    in three inconsistent ways, so resolution is a cascade:

    1. ``orbit.orbit_set_id`` — when every plane declares the same non-zero id.
    2. Parent-folder token ``Config<N>`` in the path.
    3. Filename token ``Config<N>``.

    Returns ``{"config_label": int | None, "source": str | None,
    "is_multi": bool, "nbr_config": int}``. ``config_label`` stays ``None``
    for single-config filings or when no label is found. Geometry (e.g. a
    config spanning several inclinations) intentionally plays no role — only
    declared labels are trusted.
    """
    import re as _re

    out = {
        "config_label": None,
        "source": None,
        "is_multi": str(multi_config_type or "").upper() == "M",
        "nbr_config": int(nbr_config or 0),
    }
    sets = {int(s) for s in orbit_set_ids if int(s) > 0}
    if len(sets) == 1:
        out["config_label"] = int(next(iter(sets)))
        out["source"] = "orbit_set_id"
        return out
    if len(sets) > 1:
        # Contradicts the 1-db-=-1-config data model; surface it, pick none.
        logger.warning(
            "SRS declares multiple orbit_set_id values in one db (%s) — "
            "unexpected; per-config selection unavailable.", sorted(sets),
        )
        return out

    path = str(mdb_path)
    m = _re.search(r"config[\s_-]*(\d+)", os.path.basename(os.path.dirname(path)),
                   _re.IGNORECASE)
    if m:
        out["config_label"] = int(m.group(1))
        out["source"] = "folder"
        return out
    m = _re.search(r"config[\s_-]*(\d+)", os.path.basename(path), _re.IGNORECASE)
    if m:
        out["config_label"] = int(m.group(1))
        out["source"] = "filename"
    return out


def detect_orbit_config(mdb_path: str, system: "SRSNonGeoSystem") -> dict:
    """Configuration-label cascade over a loaded :class:`SRSNonGeoSystem`.

    See :func:`detect_orbit_config_raw` for the resolution rules.
    """
    return detect_orbit_config_raw(
        mdb_path,
        system.multi_config_type,
        system.nbr_config,
        {p.orbit_set_id for p in system.orbit_planes},
    )


@dataclass
class SRSMaskInfo:
    """SRS mask information."""
    mask_id: int
    freq_min_ghz: float         # Minimum frequency (GHz)
    freq_max_ghz: float         # Maximum frequency (GHz)
    f_mask: str                 # "P" = PFD, "E" = EIRP, "S" = other
    f_mask_type: str            # "A" = alpha, "O" = other


@dataclass
class SRSGroupInfo:
    """Operational information for an SRS group linked to the mask."""
    grp_id: int
    ntc_id: str
    emi_rcp: str
    beam_name: str
    freq_min_ghz: float | None
    freq_max_ghz: float | None
    elev_min_deg: float | None


def _try_parse_itu_date_value(raw: str | None) -> float | None:
    """Convert typical ITU/SRS date values (e.g. YYYYMMDD) into a UTC timestamp."""
    if raw is None:
        return None
    s = str(raw).strip().strip('"').replace("-", "").replace("/", "")
    if not s or not s[0].isdigit():
        return None
    # YYYYMMDD or YYYYMMDDhhmmss (numeric prefix)
    i = 0
    while i < len(s) and s[i].isdigit():
        i += 1
    digits = s[:i]
    if len(digits) >= 8:
        ymd = digits[:8]
        try:
            return datetime.strptime(ymd, "%Y%m%d").timestamp()
        except ValueError:
            pass
    return None


def _ntc_id_sort_int(row: dict) -> int:
    raw = row.get("ntc_id", "")
    s = str(raw).strip().strip('"')
    try:
        return int(s)
    except ValueError:
        return -1


def _pick_non_geo_date_column(rows: list[dict]) -> str | None:
    """Pick a date column in ``non_geo`` if there are parseable values."""
    if not rows:
        return None
    priority = (
        "d_rcv",
        "d_not",
        "date_rcv",
        "rcv_date",
        "ntc_date",
        "notice_date",
        "d_sub",
        "d_ntc",
        "d_latest",
    )
    keys: set[str] = set()
    for r in rows:
        keys.update(r.keys())
    for k in priority:
        if k not in keys:
            continue
        if any(_try_parse_itu_date_value(r.get(k)) is not None for r in rows):
            return k
    # Heuristic: columns with rcv/date or prefix d_ holding parseable values
    for k in sorted(keys):
        kl = k.lower()
        if not ("date" in kl or "rcv" in kl or kl.startswith("d_") or kl.startswith("dntc")):
            continue
        if any(_try_parse_itu_date_value(r.get(k)) is not None for r in rows):
            return k
    return None


def _sort_non_geo_rows_most_recent_first(rows: list[dict]) -> list[dict]:
    """Sort ``non_geo`` rows so the most recent notice comes first."""
    if len(rows) <= 1:
        return rows
    col = _pick_non_geo_date_column(rows)

    def sort_key(r: dict) -> tuple:
        # Larger tuple = more recent; reverse=True puts dated notices first.
        if col:
            ts = _try_parse_itu_date_value(r.get(col))
            if ts is not None:
                return (1, ts, _ntc_id_sort_int(r))
        n = _ntc_id_sort_int(r)
        return (0, float(n), n)

    return sorted(rows, key=sort_key, reverse=True)


def list_non_geo_systems(mdb_path: str) -> list[dict]:
    """List notices/systems from the ``non_geo`` table of an SRS MDB.

    The order is **most recent to oldest**: uses the MDB date column when
    available; otherwise, descending numeric ``ntc_id``.
    """
    if not os.path.exists(mdb_path):
        raise FileNotFoundError(f"MDB file not found: {mdb_path}")

    rows = _run_mdb_export(mdb_path, "non_geo")
    rows = _sort_non_geo_rows_most_recent_first(rows)
    systems: list[dict] = []
    seen_ntc: set[str] = set()
    for row in rows:
        ntc_id = row.get("ntc_id", "").strip().strip('"')
        if not ntc_id or ntc_id in seen_ntc:
            continue
        seen_ntc.add(ntc_id)
        sat_name = row.get("sat_name", "").strip().strip('"')
        admin = row.get("adm", row.get("admin", "")).strip().strip('"')
        systems.append({
            "ntc_id": ntc_id,
            "sat_name": sat_name,
            "admin": admin,
            "label": f"{ntc_id} — {sat_name}" if sat_name else ntc_id,
        })
    return systems


# Cache of opened AccessParser instances, keyed by (abspath, mtime_ns). Parsing
# the JET catalogue is the costly step; SRS ingestion reads several tables from
# the same file, so we reuse the parser across calls within a process.
_ACCESS_CACHE: dict[tuple[str, int], "AccessParser"] = {}


def _open_access(mdb_path: str) -> "AccessParser | None":
    if not HAS_ACCESS_PARSER:
        logger.error(
            "access_parser not installed; cannot read MDB. "
            "Install with: pip install access-parser"
        )
        return None
    abs_path = os.path.abspath(mdb_path)
    try:
        key = (abs_path, os.stat(abs_path).st_mtime_ns)
    except OSError as e:
        logger.error(f"Cannot stat MDB '{mdb_path}': {e}")
        return None
    db = _ACCESS_CACHE.get(key)
    if db is None:
        try:
            db = AccessParser(abs_path)
        except Exception as e:  # noqa: BLE001 — parser raises bare exceptions
            logger.error(f"Failed to open MDB '{mdb_path}': {e!r}")
            return None
        _ACCESS_CACHE[key] = db
    return db


def _ap_value_to_str(v: object) -> str:
    """Normalise an access_parser cell to the same string shape the legacy
    ``mdb-export`` path produced, so every downstream parser keeps working
    unchanged.

    - ``None`` → ``""``.
    - bytes/bytearray → latin1 text (1:1 byte mapping; preserves OLE/ZIP blobs
      so the mask extractor finds the ``PK\\x03\\x04`` header intact).
    - ``datetime`` → ``mm/dd/yy HH:MM:SS`` (mdb-export's format). Keeping this
      format means ``_try_parse_itu_date_value`` behaves identically to before.
    """
    if v is None:
        return ""
    if isinstance(v, (bytes, bytearray)):
        return bytes(v).decode("latin1", errors="replace")
    if isinstance(v, datetime):
        return v.strftime("%m/%d/%y %H:%M:%S")
    if isinstance(v, bool):
        return "1" if v else "0"
    return str(v)


def _run_mdb_export(mdb_path: str, table: str) -> list[dict]:
    """Read a whole MDB table, returning ``list[dict[str, str]]``.

    Drop-in replacement for the former mdbtools/ODBC reader: same return shape
    (rows of string-valued dicts keyed by column name), pure Python.
    """
    db = _open_access(mdb_path)
    if db is None:
        return []
    try:
        cols = db.parse_table(table)  # {column_name: [values...]}
    except Exception as e:  # noqa: BLE001 — parser raises bare exceptions
        logger.error(f"Error reading table '{table}' from '{mdb_path}': {e!r}")
        return []
    if not cols:
        return []
    names = list(cols.keys())
    nrows = max((len(v) for v in cols.values()), default=0)
    rows: list[dict] = []
    for i in range(nrows):
        rows.append({
            c: _ap_value_to_str(cols[c][i] if i < len(cols[c]) else None)
            for c in names
        })
    return rows


def read_pfd_mask_xml_from_mdb(
    mask_mdb_path: str,
    mask_id: int,
    ntc_id: str | None = None,
) -> str:
    """Read the PFD mask XML directly from the MASK MDB (table ``masks``).

    The ``mask`` field usually stores a ZIP (OLE/BINARY blob) containing the XML.
    """
    if not os.path.exists(mask_mdb_path):
        raise FileNotFoundError(f"MASK MDB file not found: {mask_mdb_path}")

    rows = _run_mdb_export(mask_mdb_path, "masks")
    if not rows:
        raise ValueError(
            "Table 'masks' empty or not found in the MASK MDB."
        )

    mask_id_s = str(int(mask_id))
    ntc_id_s = str(ntc_id).strip() if ntc_id is not None else None
    candidates = []
    for row in rows:
        row_mid = str(_parse_int(row.get("mask_id", "")))
        row_ntc = row.get("ntc_id", "").strip().strip('"')
        if row_mid != mask_id_s:
            continue
        if ntc_id_s is not None and row_ntc != ntc_id_s:
            continue
        candidates.append(row)

    if not candidates:
        raise ValueError(
            f"Mask mask_id={mask_id} not found in {mask_mdb_path}"
            + (f" for ntc_id={ntc_id_s}" if ntc_id_s else "")
            + "."
        )

    row = candidates[0]
    payload_txt = row.get("mask", "")
    if payload_txt is None or payload_txt == "":
        raise ValueError(
            f"Empty 'mask' field for mask_id={mask_id} in {mask_mdb_path}."
        )

    payload = payload_txt.encode("latin1", errors="ignore")
    if not payload:
        raise ValueError(
            f"Invalid payload for mask_id={mask_id} in {mask_mdb_path}."
        )

    # Some dumps may contain extra bytes before the ZIP.
    pk_idx = payload.find(b"PK\x03\x04")
    if pk_idx > 0:
        payload = payload[pk_idx:]

    xml_bytes: bytes
    if payload.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(payload), "r") as zf:
                names = zf.namelist()
                if not names:
                    raise ValueError("Mask ZIP has no internal files.")
                xml_name = next((n for n in names if n.lower().endswith(".xml")), names[0])
                xml_bytes = zf.read(xml_name)
        except zipfile.BadZipFile as e:
            raise ValueError(
                f"Mask payload is not a valid ZIP (mask_id={mask_id})."
            ) from e
    else:
        # Fallback: in some databases the payload may already be raw XML.
        xml_bytes = payload

    xml_start = xml_bytes.find(b"<")
    if xml_start > 0:
        xml_bytes = xml_bytes[xml_start:]

    try:
        xml_text = xml_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        xml_text = xml_bytes.decode("latin1", errors="replace")

    if "<pfd_mask" not in xml_text:
        raise ValueError(
            f"Content extracted from MASK MDB does not contain <pfd_mask> (mask_id={mask_id})."
        )

    logger.info(
        f"PFD mask loaded from MASK MDB: mask_id={mask_id}, "
        f"ntc_id={row.get('ntc_id', '').strip().strip('\"') or 'n/a'}"
    )
    return xml_text


def load_pfd_masks_for_ids(
    mask_mdb_path: str,
    mask_ids: "list[int] | set[int] | tuple[int, ...]",
    *,
    ntc_id: str | None = None,
) -> dict[int, "object"]:
    """Load multiple PFD masks from the MASK MDB indexed by ``mask_id``.

    Multi-mask version of :func:`read_pfd_mask_xml_from_mdb` + XML parsing.
    Useful when the SRS declares distinct masks per orbit (S.1503-4): the
    pipeline reads the unique set of ``mask_id`` required by the
    ``orb_id → mask_id`` mapping (via :func:`read_mask_assignment`) and
    materializes each :class:`PFDMask` only once for reuse across satellites.

    Returns ``dict[mask_id, PFDMask]``. Failure on any mask propagates the
    exception (it does not stay silent: a missing declared mask means an
    inconsistent filing and should break the pipeline early).
    """
    from .pfd_mask import load_pfd_mask_from_xml_content  # late import avoids cycle

    out: dict[int, object] = {}
    for mid in {int(m) for m in mask_ids if m is not None and int(m) > 0}:
        xml_text = read_pfd_mask_xml_from_mdb(mask_mdb_path, mask_id=mid, ntc_id=ntc_id)
        out[mid] = load_pfd_mask_from_xml_content(xml_text, mask_id=mid)
    return out


def list_pfd_masks_from_mask_mdb(mask_mdb_path: str, ntc_id: str | None = None) -> list[dict]:
    """List entries of the ``masks`` table from a separate MASK MDB."""
    if not os.path.exists(mask_mdb_path):
        raise FileNotFoundError(f"MASK MDB file not found: {mask_mdb_path}")

    rows = _run_mdb_export(mask_mdb_path, "masks")
    if not rows:
        raise ValueError("Table 'masks' empty or not found in the MASK MDB.")

    ntc_id_s = str(ntc_id).strip() if ntc_id is not None else None
    items: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for row in rows:
        row_ntc = row.get("ntc_id", "").strip().strip('"')
        if ntc_id_s is not None and row_ntc != ntc_id_s:
            continue
        if row.get("f_mask", "").strip().strip('"') != "P":
            continue
        mask_id = _parse_int(row.get("mask_id", "0"))
        key = (row_ntc, mask_id)
        if key in seen:
            continue
        seen.add(key)
        sat_name = row.get("sat_name", "").strip().strip('"')
        items.append({
            "ntc_id": row_ntc,
            "mask_id": mask_id,
            "sat_name": sat_name,
            "mask_type": row.get("f_mask_type", "").strip().strip('"'),
            "label": f"mask_id {mask_id} · {row_ntc or 'n/a'} · {sat_name or 'PFD'}",
        })
    items.sort(key=lambda x: (x["ntc_id"], x["mask_id"]))
    return items


def _parse_float(val: str, default: float = 0.0) -> float:
    """Convert a string to float, returning default if empty."""
    if val is None or val.strip() == "":
        return default
    try:
        return float(val.strip().strip('"'))
    except ValueError:
        return default


def _parse_int(val: str, default: int = 0) -> int:
    if val is None or val.strip() == "":
        return default
    try:
        return int(float(val.strip().strip('"')))
    except ValueError:
        return default


def _parse_bool(val: str) -> bool:
    if val is None:
        return False
    v = val.strip().strip('"').upper()
    return v in ("Y", "YES", "TRUE", "1")


def _period_to_seconds(ddd: str, hh: str, mm: str, ss: str = "0") -> float:
    """Convert a period (ddd, hh, mm, ss) to seconds."""
    d = _parse_float(ddd)
    h = _parse_float(hh)
    m = _parse_float(mm)
    s = _parse_float(ss)
    return d * 86400.0 + h * 3600.0 + m * 60.0 + s


def read_srs_mdb(mdb_path: str, ntc_id: str | None = None) -> SRSNonGeoSystem:
    """Read NGSO system parameters from an SRS MDB file.

    Returns SRSNonGeoSystem with all orbital planes.

    Parameters
    ----------
    ntc_id
        If provided, selects the row in ``non_geo`` with that ``ntc_id``.
        If ``None``, uses the first row of the table (legacy behavior).
    """
    if not os.path.exists(mdb_path):
        raise FileNotFoundError(f"MDB file not found: {mdb_path}")

    # Manual filings (R3/R4): the SRS side of a manual pair is a YAML in the
    # Manual-System schema — parsed into the same SRSNonGeoSystem.
    if str(mdb_path).lower().endswith((".yaml", ".yml")):
        return _read_manual_srs_yaml(str(mdb_path), ntc_id=ntc_id)

    # --- non_geo table ---
    non_geo_rows = _run_mdb_export(mdb_path, "non_geo")
    if not non_geo_rows:
        raise ValueError("Table 'non_geo' empty or not found.")

    if ntc_id is not None:
        want = str(ntc_id).strip().strip('"')
        ng = None
        for row in non_geo_rows:
            row_ntc = row.get("ntc_id", "").strip().strip('"')
            if row_ntc == want:
                ng = row
                break
        if ng is None:
            available = sorted(
                {r.get("ntc_id", "").strip().strip('"') for r in non_geo_rows if r.get("ntc_id")}
            )
            raise ValueError(
                f"ntc_id={want!r} not found in non_geo ({mdb_path}). "
                f"Available: {available}"
            )
        logger.info(f"non_geo selected by ntc_id={want!r}")
    else:
        ng = non_geo_rows[0]
    system = SRSNonGeoSystem(
        ntc_id=ng.get("ntc_id", "").strip().strip('"'),
        sat_name=ng.get("sat_name", "").strip().strip('"'),
        ref_body=ng.get("ref_body", "T").strip().strip('"'),
        nbr_planes=_parse_int(ng.get("nbr_plane", "0")),
        nbr_sat_total=_parse_int(ng.get("nbr_sat_td", "0")),
        density=_parse_float(ng.get("density", "0")),
        avg_dist_km=_parse_float(ng.get("avg_dist", "0")),
        f_x_zone=_parse_bool(ng.get("f_x_zone", "")),
        x_zone_deg=_parse_float(ng.get("x_zone", "0")),
        f_constellation=_parse_bool(ng.get("f_constell", "")),
        # AP4 A.4.b.3.b/c — absent on legacy schemas (pre-v10 SRS).
        multi_config_type=str(ng.get("multi_config_type") or "").strip().strip('"').upper(),
        nbr_config=_parse_int(ng.get("nbr_config", "0")),
    )

    logger.info(f"System: {system.sat_name} (ntc_id={system.ntc_id})")
    logger.info(f"  Planes (nbr_plane): {system.nbr_planes}, "
                f"Total active sats (nbr_sat_td): {system.nbr_sat_total} "
                f"[Real sats/plane → see orbit table]")
    logger.info(f"  Exclusion zone: {'Y' if system.f_x_zone else 'N'}, "
                f"X={system.x_zone_deg:.1f}°")

    # --- orbit table ---
    orbit_rows = _run_mdb_export(mdb_path, "orbit")

    for row in orbit_rows:
        row_ntc = row.get("ntc_id", "").strip().strip('"')
        if row_ntc != system.ntc_id:
            continue

        period_s = _period_to_seconds(
            row.get("prd_ddd", "0"),
            row.get("prd_hh", "0"),
            row.get("prd_mm", "0"),
        )

        rpt_period_s = _period_to_seconds(
            row.get("rpt_prd_dd", "0"),
            row.get("rpt_prd_hh", "0"),
            row.get("rpt_prd_mm", "0"),
            row.get("rpt_prd_ss", "0"),
        )

        # Apogee and perigee — with exponent (field × 10^exp)
        apog = _parse_float(row.get("apog", "0"))
        apog_exp = _parse_int(row.get("apog_exp", "0"))
        perig = _parse_float(row.get("perig", "0"))
        perig_exp = _parse_int(row.get("perig_exp", "0"))
        op_ht = _parse_float(row.get("op_ht", "0"))
        op_ht_exp = _parse_int(row.get("op_ht_exp", "0"))

        apogee_km = apog * (10.0 ** apog_exp) if apog_exp != 0 else apog
        perigee_km = perig * (10.0 ** perig_exp) if perig_exp != 0 else perig
        op_height_km = op_ht * (10.0 ** op_ht_exp) if op_ht_exp != 0 else op_ht

        plane = SRSOrbitPlane(
            orb_id=_parse_int(row.get("orb_id", "0")),
            nbr_sat_pl=_parse_int(row.get("nbr_sat_pl", "0")),
            right_asc_deg=_parse_float(row.get("right_asc", "0")),
            inclin_deg=_parse_float(row.get("inclin_ang", "0")),
            period_s=period_s,
            apogee_km=apogee_km,
            perigee_km=perigee_km,
            perigee_arg_deg=_parse_float(row.get("perig_arg", "0")),
            op_height_km=op_height_km,
            f_stn_keep=_parse_bool(row.get("f_stn_keep", "")),
            rpt_period_s=rpt_period_s,
            f_precess=_parse_bool(row.get("f_precess", "")),
            precession_deg_day=_parse_float(row.get("precession", "0")),
            long_asc_deg=_parse_float(row.get("long_asc", "0")),
            keep_range_deg=_parse_float(row.get("keep_rnge", "0")),
            f_sun_synch=_parse_bool(row.get("f_sunsynch", "")),
            orbit_set_id=_parse_int(row.get("orbit_set_id", "0")),
        )
        system.orbit_planes.append(plane)

    n_planes = len(system.orbit_planes)
    total_sats = sum(p.nbr_sat_pl for p in system.orbit_planes)
    logger.info(f"  Orbital planes read: {n_planes}  →  total in simulation: {total_sats} sats")

    # Mutually-exclusive configurations (AP4 A.4.b.3.b-d / R2): one SRS db
    # always carries ONE configuration; flag which, so downstream can report
    # per-config and campaigns can pair the sibling dbs of the same notice.
    if system.multi_config_type == "M":
        _cfg_info = detect_orbit_config(mdb_path, system)
        logger.warning(
            "Notice %s declares %d mutually-exclusive configurations "
            "(multi_config_type=M); this db carries configuration %s "
            "(source: %s). EPFD must be evaluated PER configuration — do not "
            "aggregate this db with its sibling configuration dbs.",
            system.ntc_id, system.nbr_config,
            _cfg_info["config_label"] if _cfg_info["config_label"] is not None else "?",
            _cfg_info["source"] or "not found",
        )

    if system.orbit_planes:
        p0 = system.orbit_planes[0]
        logger.info(f"  Plane 1: a={p0.semi_major_axis_km:.2f} km, "
                    f"e={p0.eccentricity:.4f}, i={p0.inclin_deg:.1f}°")
        logger.info(f"  Period: {p0.period_s:.1f} s ({p0.period_s/60:.1f} min)")
        logger.info(f"  Alt: apog={p0.apogee_km:.0f} km, perig={p0.perigee_km:.0f} km")
        logger.info(f"  Sats/plane (orbit.nbr_sat_pl): {p0.nbr_sat_pl}")

    # --- phase table (official initial phases per satellite) ---
    # May not exist/be populated for some filings; in that case we keep the uniform fallback.
    try:
        phase_rows = _run_mdb_export(mdb_path, "phase")
    except Exception:
        phase_rows = []

    # Resolve duplicates by (ntc_id, orb_id, orb_sat_id) preferring the largest (d_ref, t_ref).
    phase_best: dict[tuple[int, int], tuple[int, int, float]] = {}
    for row in phase_rows:
        row_ntc = row.get("ntc_id", "").strip().strip('"')
        if row_ntc != system.ntc_id:
            continue

        orb_id = _parse_int(row.get("orb_id", "0"))
        orb_sat_id = _parse_int(row.get("orb_sat_id", "0"))
        phase_raw = row.get("phase_ang", "")
        if orb_id <= 0 or orb_sat_id <= 0:
            continue
        if phase_raw is None or phase_raw.strip().strip('"') == "":
            continue

        phase_ang = _parse_float(phase_raw, default=0.0) % 360.0
        d_ref = _parse_int(row.get("d_ref", "0"))
        t_ref = _parse_int(row.get("t_ref", "0"))

        key = (orb_id, orb_sat_id)
        prev = phase_best.get(key)
        if prev is None or (d_ref, t_ref) >= (prev[0], prev[1]):
            phase_best[key] = (d_ref, t_ref, phase_ang)

    for (orb_id, orb_sat_id), (_, _, phase_ang) in phase_best.items():
        per_orbit = system.phase_by_orbit.setdefault(orb_id, {})
        per_orbit[orb_sat_id] = phase_ang

    if system.phase_by_orbit:
        phase_count = sum(len(v) for v in system.phase_by_orbit.values())
        logger.info(
            f"  Initial phases (phase table): {phase_count} entries "
            f"in {len(system.phase_by_orbit)} orbits"
        )
    else:
        logger.info("  phase table has no applicable data — using uniform phase per plane")

    return system


def read_sat_oper(mdb_path: str, ntc_id: str) -> list[tuple[float, float, int]]:
    """Read the ``sat_oper`` table from the SRS MDB and return MAX_CO_FREQ per latitude band.

    Returns a list of tuples ``(lat_fr, lat_to, nbr_op_sat)`` sorted by ``lat_fr``.
    ``nbr_op_sat`` is the maximum number of co-frequency satellites operating simultaneously
    (Step 19 of Annex D of ITU-R S.1503-4). Returns an empty list if the table does not exist.

    See :func:`read_sat_oper_min_duration` for the parallel MIN_DURATION table
    (§D5.1.4.2 track-duration variant), which is now implemented.
    """
    result, _min_dur = _read_sat_oper_bands(mdb_path, ntc_id)
    if result:
        logger.info(
            f"  MAX_CO_FREQ (sat_oper): {len(result)} latitude band(s) — "
            + ", ".join(f"[{f:.0f}°,{t:.0f}°]→{n}" for f, t, n in result)
        )
    return result


def read_sat_oper_min_duration(mdb_path: str, ntc_id: str) -> list[tuple[float, float, float]]:
    """MIN_DURATION per latitude band from ``sat_oper`` (S.1503-4 §D5.1.4.2).

    Returns ``(lat_fr, lat_to, min_duration_s)`` sorted by ``lat_fr`` for the
    bands with a non-zero MIN_DURATION; an empty list means the standard
    §D5.1.4.1 path applies (no track-duration handling needed). The ``min_dur``
    (Access) / ``min_duration`` (XML) column absent ⇒ 0.
    """
    _bands, min_dur = _read_sat_oper_bands(mdb_path, ntc_id)
    nz = [(f, t, d) for f, t, d in min_dur if abs(d) > 1e-9]
    if nz:
        logger.info(
            "  MIN_DURATION (sat_oper): %d latitude band(s) — "
            "triggers §D5.1.4.2 sliding-window variant: "
            + ", ".join(f"[{f:.0f}°,{t:.0f}°]→{d:.0f}s" for f, t, d in nz),
            len(nz),
        )
    return nz


def _read_sat_oper_bands(
    mdb_path: str, ntc_id: str,
) -> tuple[list[tuple[float, float, int]], list[tuple[float, float, float]]]:
    """Single scan of ``sat_oper`` returning both the MAX_CO_FREQ bands and the
    parallel MIN_DURATION bands (both sorted by ``lat_fr``)."""
    try:
        rows = _run_mdb_export(mdb_path, "sat_oper")
    except Exception:
        return [], []
    if not rows:
        return [], []

    ntc_id_s = str(ntc_id).strip()
    result: list[tuple[float, float, int]] = []
    min_dur_bands: list[tuple[float, float, float]] = []
    for row in rows:
        row_ntc = row.get("ntc_id", "").strip().strip('"')
        if row_ntc != ntc_id_s:
            continue
        lat_fr = _parse_float(row.get("lat_fr", ""), default=-90.0)
        lat_to = _parse_float(row.get("lat_to", ""), default=90.0)
        nbr_op = _parse_int(row.get("nbr_op_sat", "0"))
        # MIN_DURATION may appear as ``min_dur`` (Access) or ``min_duration`` (XML).
        min_dur_raw = row.get("min_dur", row.get("min_duration", "0"))
        try:
            min_dur_s = _parse_float(min_dur_raw, default=0.0)
        except Exception:
            min_dur_s = 0.0
        result.append((lat_fr, lat_to, nbr_op))
        min_dur_bands.append((lat_fr, lat_to, min_dur_s))

    result.sort(key=lambda x: x[0])
    min_dur_bands.sort(key=lambda x: x[0])
    return result, min_dur_bands


def read_mask_info(mdb_path: str, ntc_id: str | None = None) -> list[SRSMaskInfo]:
    """Read mask information from the SRS MDB.

    If ``ntc_id`` is provided and the table has an ``ntc_id`` column, keeps only
    rows for that system (aligned with ``read_srs_mdb``).
    """
    rows = _run_mdb_export(mdb_path, "mask_info")
    ntc_f = str(ntc_id).strip().strip('"') if ntc_id else None
    masks = []
    for row in rows:
        if ntc_f is not None:
            row_ntc = row.get("ntc_id", "").strip().strip('"')
            if row_ntc and row_ntc != ntc_f:
                continue
        masks.append(SRSMaskInfo(
            mask_id=_parse_int(row.get("mask_id", "0")),
            freq_min_ghz=_parse_float(row.get("freq_min", "0")),
            freq_max_ghz=_parse_float(row.get("freq_max", "0")),
            f_mask=row.get("f_mask", "").strip().strip('"'),
            f_mask_type=row.get("f_mask_type", "").strip().strip('"'),
        ))
    return masks


def read_group_for_mask(
    mdb_path: str,
    *,
    ntc_id: str,
    mask_id: int,
    preferred_emi_rcp: str | None = "E",
) -> SRSGroupInfo | None:
    """Resolve the ``grp`` group applicable to a mask via ``mask_lnk1``.

    For epfd(down), the PFD mask normally links to the transmitting group
    (``emi_rcp='E'``). When there are multiple candidates, it prioritizes:
    1. preferred ``emi_rcp``
    2. group with ``elev_min`` defined
    3. lowest ``grp_id`` (stable)
    """
    if not os.path.exists(mdb_path):
        raise FileNotFoundError(f"MDB file not found: {mdb_path}")

    ntc_id_s = str(ntc_id).strip().strip('"')
    rows_grp = _run_mdb_export(mdb_path, "grp")
    rows_lnk = _run_mdb_export(mdb_path, "mask_lnk1")
    if not rows_grp or not rows_lnk:
        return None

    grp_by_id: dict[tuple[str, int], dict] = {}
    for row in rows_grp:
        row_ntc = row.get("ntc_id", "").strip().strip('"')
        gid = _parse_int(row.get("grp_id", "0"))
        if not row_ntc or gid <= 0:
            continue
        grp_by_id[(row_ntc, gid)] = row

    candidates: list[SRSGroupInfo] = []
    for row in rows_lnk:
        row_ntc = row.get("ntc_id", "").strip().strip('"')
        if row_ntc != ntc_id_s:
            continue
        row_mask_id = _parse_int(row.get("mask_id", "0"))
        if row_mask_id != int(mask_id):
            continue
        grp_id = _parse_int(row.get("grp_id", "0"))
        grp_row = grp_by_id.get((row_ntc, grp_id))
        if not grp_row:
            continue

        freq_min_mhz = _parse_float(grp_row.get("freq_min", ""), default=0.0)
        freq_max_mhz = _parse_float(grp_row.get("freq_max", ""), default=0.0)
        elev_min_deg = _parse_float(grp_row.get("elev_min", ""), default=float("nan"))
        candidates.append(
            SRSGroupInfo(
                grp_id=grp_id,
                ntc_id=row_ntc,
                emi_rcp=grp_row.get("emi_rcp", "").strip().strip('"').upper(),
                beam_name=grp_row.get("beam_name", "").strip().strip('"'),
                freq_min_ghz=(freq_min_mhz / 1000.0) if freq_min_mhz > 0.0 else None,
                freq_max_ghz=(freq_max_mhz / 1000.0) if freq_max_mhz > 0.0 else None,
                elev_min_deg=(
                    float(elev_min_deg) if math.isfinite(elev_min_deg) and elev_min_deg > 0.0 else None
                ),
            )
        )

    if not candidates:
        return None

    pref = str(preferred_emi_rcp or "").upper()
    candidates.sort(
        key=lambda item: (
            0 if pref and item.emi_rcp == pref else 1,
            0 if item.elev_min_deg is not None else 1,
            item.grp_id,
        )
    )
    chosen = candidates[0]
    logger.info(
        "SRS group associated with the mask: grp_id=%s, emi_rcp=%s, beam=%s, band=%s-%s GHz, elev_min=%s",
        chosen.grp_id,
        chosen.emi_rcp or "-",
        chosen.beam_name or "-",
        f"{chosen.freq_min_ghz:.3f}" if chosen.freq_min_ghz is not None else "-",
        f"{chosen.freq_max_ghz:.3f}" if chosen.freq_max_ghz is not None else "-",
        f"{chosen.elev_min_deg:.2f}°" if chosen.elev_min_deg is not None else "-",
    )
    return chosen


@dataclass
class EmitterBandSelection:
    """Which satellites emit within a frequency band, from ``grp`` ⋈ ``mask_lnk1``.

    A satellite ``(orb_id, sat_orb_id)`` is *active* when ``mask_lnk1`` links it
    to a transmitting ``grp`` (``emi_rcp='E'`` for epfd↓) whose declared band
    ``[freq_min, freq_max]`` contains the queried frequency. The ``grp`` band is
    the operating-frequency authority; the PFD mask (bound separately via
    ``mask_lnk1``) is NOT required to contain it.
    """

    has_data: bool                              # grp + mask_lnk1 were readable
    wildcard_all: bool                          # an active grp links orb_id == -1
    active_orbits: frozenset                     # whole-orbit active (sat_orb_id blank/0)
    active_sats: frozenset                       # specific (orb_id, sat_orb_id) active
    n_active_grps: int = 0

    def is_active(self, orb_id: int, sat_orb_id: int | None) -> bool:
        """True if the satellite emits in the queried band.

        With no usable ``grp``/``mask_lnk1`` data the selection is inert
        (``has_data=False``) and every satellite is treated as active so the
        caller never silently drops the whole constellation.
        """
        if not self.has_data or self.wildcard_all:
            return True
        if int(orb_id) in self.active_orbits:
            return True
        if sat_orb_id is not None and (int(orb_id), int(sat_orb_id)) in self.active_sats:
            return True
        return False

    @property
    def any_active(self) -> bool:
        return self.wildcard_all or bool(self.active_orbits) or bool(self.active_sats)


def read_emitters_in_band(
    mdb_path: str,
    *,
    ntc_id: str | int,
    freq_ghz: float,
    emi_rcp: str | None = "E",
    tol_ghz: float = 1e-9,
) -> EmitterBandSelection:
    """Satellites emitting at ``freq_ghz``, resolved from ``grp`` ⋈ ``mask_lnk1``.

    Selects transmitting groups (``emi_rcp``, default ``'E'``) whose band
    ``[freq_min, freq_max]`` (MHz in the SRS) contains ``freq_ghz``, then
    collects the ``(orb_id, sat_orb_id)`` emitters that ``mask_lnk1`` links to
    those groups. ``orb_id == -1`` is a wildcard (all orbits); a blank/zero
    ``sat_orb_id`` means the whole orbit.

    Returns ``has_data=False`` (no filtering) when ``grp``/``mask_lnk1`` are
    absent or unreadable — the caller must then keep the whole constellation.
    """
    if not os.path.exists(mdb_path):
        raise FileNotFoundError(f"MDB file not found: {mdb_path}")
    ntc_s = str(ntc_id).strip().strip('"')
    try:
        rows_grp = _run_mdb_export(mdb_path, "grp")
        rows_lnk = _run_mdb_export(mdb_path, "mask_lnk1")
    except Exception:  # noqa: BLE001 — missing tables ⇒ no filtering
        rows_grp = rows_lnk = None
    if not rows_grp or not rows_lnk:
        return EmitterBandSelection(False, False, frozenset(), frozenset(), 0)

    pref = str(emi_rcp or "").strip().upper()
    f = float(freq_ghz)

    # Transmitting groups whose declared band contains the queried frequency.
    active_grps: set[int] = set()
    for row in rows_grp:
        if row.get("ntc_id", "").strip().strip('"') != ntc_s:
            continue
        gid = _parse_int(row.get("grp_id", "0"))
        if gid <= 0:
            continue
        if pref and row.get("emi_rcp", "").strip().strip('"').upper() != pref:
            continue
        fmin_mhz = _parse_float(row.get("freq_min", ""), default=0.0)
        fmax_mhz = _parse_float(row.get("freq_max", ""), default=0.0)
        if fmin_mhz <= 0.0 or fmax_mhz <= 0.0:
            continue
        fmin_g, fmax_g = fmin_mhz / 1000.0, fmax_mhz / 1000.0
        if fmin_g > fmax_g:
            fmin_g, fmax_g = fmax_g, fmin_g
        if (fmin_g - tol_ghz) <= f <= (fmax_g + tol_ghz):
            active_grps.add(gid)

    wildcard_all = False
    active_orbits: set[int] = set()
    active_sats: set[tuple[int, int]] = set()
    for row in rows_lnk:
        if row.get("ntc_id", "").strip().strip('"') != ntc_s:
            continue
        if _parse_int(row.get("grp_id", "0")) not in active_grps:
            continue
        orb_id = _parse_int(row.get("orb_id", "0"))
        sat_raw = str(row.get("sat_orb_id", "")).strip().strip('"')
        sat_id = _parse_int(sat_raw) if sat_raw not in ("", "0") else 0
        if orb_id == -1:
            wildcard_all = True
        elif sat_id > 0:
            active_sats.add((orb_id, sat_id))
        else:
            active_orbits.add(orb_id)

    return EmitterBandSelection(
        has_data=True,
        wildcard_all=wildcard_all,
        active_orbits=frozenset(active_orbits),
        active_sats=frozenset(active_sats),
        n_active_grps=len(active_grps),
    )


def read_mask_assignment_all(
    mdb_path: str,
    *,
    ntc_id: str | int,
    preferred_emi_rcp: str | None = "E",
    f_mask_filter: str | None = None,
    granularity: str = "orbit",
) -> dict[int, list[int]]:
    """Resolve ``orb_id → list[mask_id]`` reading ``mask_lnk1`` from the SRS MDB.

    Granularity per **orbit** (default). For per-**satellite** granularity
    (``sat_orb_id`` populated in ``mask_lnk1``), use
    :func:`read_mask_assignment_per_sat`.

    Schema of the ``mask_lnk1`` table: ``(ntc_id, grp_id, orb_id, sat_orb_id,
    mask_id, seq_no)``. ``orb_id == -1`` is a wildcard.

    Rules:
    * Only the provided ``ntc_id`` is considered.
    * ``sat_orb_id`` is ignored (rows with sat_orb_id populated also fall into
      the bag of the corresponding orbit; per-sat granularity requires a dedicated API).
    * Each unique ``(orb_id, mask_id)`` pair enters the list only once.
    * The order within each list follows the priority ``preferred_emi_rcp``
      → lowest ``grp_id`` → lowest ``seq_no``, with de-dup by ``mask_id``.
    * When ``f_mask_filter`` is provided (``"P"``, ``"E"``, ``"S"``), only
      masks whose ``f_mask`` (``masks`` table) matches are kept. Essential
      for EPFD↓ — an orbit may declare mask 1 (S/inter-sat), mask 2 (E/UL)
      and mask 3 (P/DL) at the same time; the "correct" one depends on the calculation type.

    The ``granularity`` parameter is reserved for a future internal migration;
    the current value is validated but the function always returns per orbit. For
    per-sat resolution use :func:`read_mask_assignment_per_sat`.
    """
    if granularity not in ("orbit", "sat"):
        raise ValueError(f"invalid granularity: {granularity!r}")
    if not os.path.exists(mdb_path):
        raise FileNotFoundError(f"MDB file not found: {mdb_path}")

    ntc_id_s = str(ntc_id).strip().strip('"')
    rows_lnk = _run_mdb_export(mdb_path, "mask_lnk1")
    if not rows_lnk:
        return {}

    rows_grp = _run_mdb_export(mdb_path, "grp")
    grp_emi: dict[tuple[str, int], str] = {}
    for row in rows_grp:
        row_ntc = row.get("ntc_id", "").strip().strip('"')
        gid = _parse_int(row.get("grp_id", "0"))
        if row_ntc and gid > 0:
            grp_emi[(row_ntc, gid)] = row.get("emi_rcp", "").strip().strip('"').upper()

    # f_mask filter: cross-references read_mask_info to determine the type of each mask.
    allowed_mask_ids: set[int] | None = None
    if f_mask_filter is not None:
        f_target = str(f_mask_filter).strip().upper()
        allowed_mask_ids = {
            int(m.mask_id)
            for m in read_mask_info(mdb_path, ntc_id=ntc_id_s)
            if str(m.f_mask).strip().upper() == f_target
        }

    pref = str(preferred_emi_rcp or "").upper()
    # For each orb_id, collect ALL masks (with stable ranking for
    # consumers that still expect "first first").
    bucket: dict[int, list[tuple[int, int, int, int]]] = {}
    # tuple: (priority_pref, grp_id, seq_no, mask_id)
    for row in rows_lnk:
        row_ntc = row.get("ntc_id", "").strip().strip('"')
        if row_ntc != ntc_id_s:
            continue
        orb_id = _parse_int(row.get("orb_id", "0"))
        mask_id = _parse_int(row.get("mask_id", "0"))
        grp_id = _parse_int(row.get("grp_id", "0"))
        seq_no = _parse_int(row.get("seq_no", "0"))
        if mask_id <= 0:
            continue
        if allowed_mask_ids is not None and mask_id not in allowed_mask_ids:
            continue
        emi = grp_emi.get((row_ntc, grp_id), "")
        priority_pref = 0 if pref and emi == pref else 1
        bucket.setdefault(orb_id, []).append((priority_pref, grp_id, seq_no, mask_id))

    out: dict[int, list[int]] = {}
    for orb_id, entries in bucket.items():
        entries.sort()
        seen: set[int] = set()
        ordered: list[int] = []
        for _, _, _, mid in entries:
            if mid in seen:
                continue
            seen.add(mid)
            ordered.append(mid)
        out[orb_id] = ordered
    return out


def read_mask_assignment_per_sat(
    mdb_path: str,
    *,
    ntc_id: str | int,
    preferred_emi_rcp: str | None = "E",
    f_mask_filter: str | None = None,
) -> dict[tuple[int, int | None], list[int]]:
    """Resolve ``(orb_id, sat_orb_id|None) → list[mask_id]`` per satellite.

    Fine S.1503-4 granularity: ``mask_lnk1`` may assign specific masks to
    individual satellites via ``sat_orb_id``. When the field is blank/zero,
    the entry applies to **all** satellites of the orbit (key
    ``(orb_id, None)``). ``orb_id == -1`` remains a global wildcard.

    The caller should use :func:`resolve_masks_for_sat`, which applies a
    hierarchical fallback: explicit ``(orb, sat)`` → ``(orb, None)`` → ``(-1, None)``.

    Returns the mapping ``dict[(orb_id, sat_orb_id_or_None), list[mask_id]]``.
    List order follows the priority ``preferred_emi_rcp`` → ``grp_id`` →
    ``seq_no`` with de-dup by ``mask_id``. ``f_mask_filter`` operates as in
    :func:`read_mask_assignment_all`.
    """
    if not os.path.exists(mdb_path):
        raise FileNotFoundError(f"MDB file not found: {mdb_path}")

    ntc_id_s = str(ntc_id).strip().strip('"')
    rows_lnk = _run_mdb_export(mdb_path, "mask_lnk1")
    if not rows_lnk:
        return {}

    rows_grp = _run_mdb_export(mdb_path, "grp")
    grp_emi: dict[tuple[str, int], str] = {}
    for row in rows_grp:
        row_ntc = row.get("ntc_id", "").strip().strip('"')
        gid = _parse_int(row.get("grp_id", "0"))
        if row_ntc and gid > 0:
            grp_emi[(row_ntc, gid)] = row.get("emi_rcp", "").strip().strip('"').upper()

    allowed_mask_ids: set[int] | None = None
    if f_mask_filter is not None:
        f_target = str(f_mask_filter).strip().upper()
        allowed_mask_ids = {
            int(m.mask_id)
            for m in read_mask_info(mdb_path, ntc_id=ntc_id_s)
            if str(m.f_mask).strip().upper() == f_target
        }

    pref = str(preferred_emi_rcp or "").upper()
    bucket: dict[tuple[int, int | None], list[tuple[int, int, int, int]]] = {}
    for row in rows_lnk:
        row_ntc = row.get("ntc_id", "").strip().strip('"')
        if row_ntc != ntc_id_s:
            continue
        orb_id = _parse_int(row.get("orb_id", "0"))
        mask_id = _parse_int(row.get("mask_id", "0"))
        grp_id = _parse_int(row.get("grp_id", "0"))
        seq_no = _parse_int(row.get("seq_no", "0"))
        sat_raw = row.get("sat_orb_id", "")
        sat_orb_id: int | None
        if isinstance(sat_raw, str) and sat_raw.strip() == "":
            sat_orb_id = None
        else:
            sat_orb_id = _parse_int(sat_raw)
            if sat_orb_id <= 0:
                sat_orb_id = None
        if mask_id <= 0:
            continue
        if allowed_mask_ids is not None and mask_id not in allowed_mask_ids:
            continue
        emi = grp_emi.get((row_ntc, grp_id), "")
        priority_pref = 0 if pref and emi == pref else 1
        bucket.setdefault((orb_id, sat_orb_id), []).append(
            (priority_pref, grp_id, seq_no, mask_id)
        )

    out: dict[tuple[int, int | None], list[int]] = {}
    for key, entries in bucket.items():
        entries.sort()
        seen: set[int] = set()
        ordered: list[int] = []
        for _, _, _, mid in entries:
            if mid in seen:
                continue
            seen.add(mid)
            ordered.append(mid)
        out[key] = ordered
    return out


def resolve_masks_for_sat(
    assignment_per_sat: dict[tuple[int, int | None], list[int]],
    orb_id: int,
    sat_orb_id: int | None,
) -> list[int]:
    """Resolve the ``mask_id`` applicable to a specific satellite with fallback.

    Order:
    1. Explicit entry ``(orb_id, sat_orb_id)``.
    2. Orbit fallback ``(orb_id, None)`` — applies to all sats of the orbit.
    3. Wildcard fallback ``(-1, None)``.
    4. Empty list if nothing applies.
    """
    if sat_orb_id is not None and (orb_id, sat_orb_id) in assignment_per_sat:
        return [int(m) for m in assignment_per_sat[(orb_id, sat_orb_id)]]
    if (orb_id, None) in assignment_per_sat:
        return [int(m) for m in assignment_per_sat[(orb_id, None)]]
    if (-1, None) in assignment_per_sat:
        return [int(m) for m in assignment_per_sat[(-1, None)]]
    return []


def resolve_masks_for_orbit(
    assignment_all: dict[int, list[int]],
    orb_id: int,
) -> list[int]:
    """List of ``mask_id`` applicable to ``orb_id`` (wildcard fallback ``-1``).

    Multi-mask version of :func:`resolve_mask_for_orbit`. When the orbit does
    not appear explicitly in the mapping, returns the wildcard list
    ``assignment_all[-1]`` (if present), otherwise an empty list.
    """
    if orb_id in assignment_all:
        return [int(m) for m in assignment_all[orb_id]]
    if -1 in assignment_all:
        return [int(m) for m in assignment_all[-1]]
    return []


def read_mask_assignment(
    mdb_path: str,
    *,
    ntc_id: str | int,
    preferred_emi_rcp: str | None = "E",
) -> dict[int, int]:
    """Resolve ``orb_id → mask_id`` reading ``mask_lnk1`` from the SRS MDB.

    .. note::
       Backwards-compat: returns a single mask per orbit (the first in priority
       order). For cases where the same orbit operates with **multiple** PFD
       masks (common in filings that split bands), use
       :func:`read_mask_assignment_all`, which preserves the full list.

    Schema of the ``mask_lnk1`` table (SRS ITU): ``(ntc_id, grp_id, orb_id,
    sat_orb_id, mask_id, seq_no)``. ``orb_id == -1`` is a wildcard.
    """
    full = read_mask_assignment_all(
        mdb_path, ntc_id=ntc_id, preferred_emi_rcp=preferred_emi_rcp,
    )
    # Keeps legacy behavior: 1 mask per orbit (the first in priority).
    return {orb: lst[0] for orb, lst in full.items() if lst}


def resolve_mask_for_orbit(
    assignment: dict[int, int],
    orb_id: int,
) -> int | None:
    """Resolve the ``mask_id`` for an ``orb_id`` by querying the mapping with fallback.

    Order:
    1. Explicit entry ``assignment[orb_id]``.
    2. Wildcard ``assignment[-1]`` when present.
    3. ``None`` if nothing applies.
    """
    if orb_id in assignment:
        return int(assignment[orb_id])
    if -1 in assignment:
        return int(assignment[-1])
    return None


def read_epfd_limits_from_mdb(
    mdb_path: str,
    freq_ghz: float,
    antenna_diameter_m: float,
    bw_khz: float = 40.0,
    service: str = "FSS",
    direction: str = "down",
    override_mask_id: int | None = None,
) -> dict | None:
    """Read EPFD limits from the limits MDB (e.g. EPFD_limits_RES85.mdb),
    automatically selecting the correct mask via the ``rf_epfd_mask`` table.

    Selection logic
    ---------------
    1. Filters ``rf_epfd_mask`` by: ``mask_argmt=0`` (basic limits with no extra
       argument dependency), service, direction, BW and frequency range.
    2. Uses ``rf_diam`` (minimum antenna diameter in cm) and selects the row
       with the largest ``rf_diam ≤ antenna_diameter_m × 100``.
    3. Uses the found ``mask_id`` to extract ``time_percent`` and ``epfd``
       from ``epfd_time``.

    Parameters
    ----------
    mdb_path            : path to the limits MDB
    freq_ghz            : center frequency of the simulation (GHz)
    antenna_diameter_m  : earth station antenna diameter (m)
    bw_khz              : reference bandwidth (kHz)
    service             : service (e.g. "FSS")
    direction           : link direction ("down" or "up")
    override_mask_id    : if provided, bypasses automatic selection and uses this mask_id

    Returns
    -------
    dict with:
        ``limits``                  — list of [epfd_dBW, exceedance_pct]
        ``reference_bandwidth_khz`` — reference BW read from the MDB
        ``rr_reference``            — e.g. "Article 22, TABLE 22-1A"
        ``mask_id``                 — selected mask_id
        ``rf_diam_cm``              — minimum diameter of the selected curve (cm)
    or None if no compatible mask is found.
    """
    # ── Step 1: select mask_id via rf_epfd_mask ──────────────────────
    if override_mask_id is not None:
        selected_mask_id = override_mask_id
        selected_bw = bw_khz
        selected_diam = 0.0
        selected_rr = ""
    else:
        rows_mask = _run_mdb_export(mdb_path, "rf_epfd_mask")
        ant_cm = antenna_diameter_m * 100.0

        # (rf_diam_cm, mask_id, bw_khz, rr_reference)
        candidates: list[tuple[float, int, float, str]] = []
        for r in rows_mask:
            # mask_argmt=0: basic limits (% of time) with no extra argument
            if _parse_int(r.get("mask_argmt", "99")) != 0:
                continue
            if r.get("rr_service", "").strip().strip('"') != service:
                continue
            if r.get("link_direction", "").strip().strip('"') != direction:
                continue
            row_bw = _parse_float(r.get("rf_band_wdth", "0"))
            if row_bw > 0 and abs(row_bw - bw_khz) > 1.0:
                continue
            freq_min = _parse_float(r.get("freq_min", "0"))
            freq_max = _parse_float(r.get("freq_max", "0"))
            if not (freq_min <= freq_ghz <= freq_max):
                continue
            rf_diam = _parse_float(r.get("rf_diam", "0"))
            mid = _parse_int(r.get("mask_id", "0"))
            rr_ref = r.get("rr_reference", "").strip().strip('"')
            candidates.append((rf_diam, mid, row_bw if row_bw > 0 else bw_khz, rr_ref))

        if not candidates:
            logger.warning(
                f"read_epfd_limits_from_mdb: no mask found for "
                f"freq={freq_ghz:.3f} GHz, D={antenna_diameter_m:.2f} m, "
                f"BW={bw_khz:.0f} kHz in {mdb_path}"
            )
            return None

        # Select: largest rf_diam ≤ actual diameter (cm) — strictest applicable curve
        qualified = [(d, mid, bw, rr) for d, mid, bw, rr in candidates if d <= ant_cm]
        if qualified:
            chosen = max(qualified, key=lambda x: x[0])
        else:
            # Fallback: curve with the smallest available rf_diam
            chosen = min(candidates, key=lambda x: x[0])
            logger.warning(
                f"Antenna {ant_cm:.0f} cm smaller than the smallest available rf_diam "
                f"({chosen[0]:.0f} cm) — using mask_id={chosen[1]} as fallback"
            )

        selected_diam, selected_mask_id, selected_bw, selected_rr = chosen
        logger.info(
            f"EPFD mask selected: mask_id={selected_mask_id}, "
            f"{selected_rr}, rf_diam≥{selected_diam:.0f} cm, BW={selected_bw:.0f} kHz"
        )

    # ── Step 2: read time_percent and epfd from epfd_time ─────────────────────
    rows_time = _run_mdb_export(mdb_path, "epfd_time")
    time_rows = [r for r in rows_time
                 if _parse_int(r.get("mask_id", "")) == selected_mask_id]

    if not time_rows:
        logger.warning(f"epfd_limits_mdb: mask_id={selected_mask_id} missing from "
                       f"epfd_time ({mdb_path})")
        return None

    time_rows.sort(key=lambda r: _parse_int(r.get("mask_step", "0")))

    limits = []
    rr_ref_time = ""
    for r in time_rows:
        epfd = _parse_float(r.get("epfd", "0"))
        time_pct = _parse_float(r.get("time_percent", "0"))
        exceedance = 100.0 - time_pct  # % of time for which EPFD exceeds this value
        limits.append([epfd, exceedance])
        if not rr_ref_time:
            rr_ref_time = r.get("rr_reference", "").strip().strip('"')

    rr_ref_final = selected_rr or rr_ref_time

    logger.info(
        f"EPFD limits loaded: {rr_ref_final}, mask_id={selected_mask_id}, "
        f"BW={selected_bw:.0f} kHz, {len(limits)} points"
    )
    return {
        "limits": limits,
        "reference_bandwidth_khz": selected_bw,
        "rr_reference": rr_ref_final,
        "mask_id": selected_mask_id,
        "rf_diam_cm": selected_diam,
    }


def suggest_services_from_limits_mdb(
    mdb_path: str,
    freq_min_ghz: float,
    freq_max_ghz: float,
    direction: str = "down",
) -> list[str]:
    """List candidate services (e.g. FSS/BSS) in the limits MDB for the band."""
    if not os.path.exists(mdb_path):
        raise FileNotFoundError(f"MDB file not found: {mdb_path}")

    rows = _run_mdb_export(mdb_path, "rf_epfd_mask")
    services: set[str] = set()
    for row in rows:
        row_dir = row.get("link_direction", "").strip().strip('"')
        if direction and row_dir and row_dir != direction:
            continue
        row_service = row.get("rr_service", "").strip().strip('"').upper()
        if not row_service:
            continue
        row_fmin = _parse_float(row.get("freq_min", "0"))
        row_fmax = _parse_float(row.get("freq_max", "0"))
        overlaps = row_fmax >= float(freq_min_ghz) and row_fmin <= float(freq_max_ghz)
        if overlaps:
            services.add(row_service)
    return sorted(services)


def srs_to_constellation_config(system: SRSNonGeoSystem) -> dict:
    """Convert SRS data to the constellation configuration format.

    Returns a dictionary compatible with the YAML config.
    """
    if not system.orbit_planes:
        raise ValueError("No orbital plane found in the SRS.")

    p0 = system.orbit_planes[0]  # Reference plane

    # Infer the number of planes
    num_planes = len(system.orbit_planes)
    if system.nbr_planes > 0:
        num_planes = system.nbr_planes

    # Semi-major axis: prefer the actual orbit geometry (apogee/perigee) —
    # this is what the BR software uses (validated against EPFDRESULTS: Boeing
    # ntc102 apog=20182 km vs op_ht=20000 km → ITU Δt matches apogee). The
    # AP4 minimum operating height (op_ht) is a transmit gate, not the orbit.
    if p0.altitude_km > 100.0:
        semi_major_axis = p0.altitude_km + RE_KM
        logger.info(
            f"Using apogee/perigee from the MDB: h={p0.altitude_km:.2f} km "
            f"(a={semi_major_axis:.2f} km)"
        )
    elif p0.op_height_km > 100:
        semi_major_axis = p0.op_height_km + RE_KM
        logger.info(f"Using operational altitude from the MDB: {p0.op_height_km} km (a={semi_major_axis:.2f} km)")
    else:
        semi_major_axis = p0.semi_major_axis_km
        logger.info(f"Using orbital period to compute SMA: a={semi_major_axis:.2f} km")

    # SRS ``orbit.op_ht`` (× 10^op_ht_exp) is the AP4 "minimum operating
    # height" of the space station — the source of the S.1503 parameter
    # MIN_OPERATING_HEIGHT used by §D4.2 and by the epfd altitude filter
    # (satellites below H_min do not transmit). Emit it both globally
    # (smallest positive value across planes — conservative for §D4.2) and
    # per plane.
    op_heights_km = [p.op_height_km for p in system.orbit_planes if p.op_height_km > 0.0]
    min_operating_height_km = min(op_heights_km) if op_heights_km else 0.0
    if min_operating_height_km > 0.0:
        logger.info(
            f"Minimum operating height (orbit.op_ht): {min_operating_height_km:.1f} km"
        )

    # GMST0 (initial Earth rotation) inferred from the (RAAN, long_asc) pairs:
    # long_asc ≈ RAAN - GMST0  =>  GMST0 ≈ RAAN - long_asc.
    gmst0_candidates: list[float] = []
    for plane in system.orbit_planes:
        if math.isfinite(plane.right_asc_deg) and math.isfinite(plane.long_asc_deg):
            gmst0_candidates.append((plane.right_asc_deg - plane.long_asc_deg) % 360.0)

    gmst0_deg = 0.0
    gmst0_max_dev_deg = 0.0
    if gmst0_candidates:
        s = sum(math.sin(math.radians(a)) for a in gmst0_candidates)
        c = sum(math.cos(math.radians(a)) for a in gmst0_candidates)
        gmst0_deg = math.degrees(math.atan2(s, c)) % 360.0
        gmst0_max_dev_deg = max(
            abs(((a - gmst0_deg + 180.0) % 360.0) - 180.0) for a in gmst0_candidates
        )
        logger.info(
            f"GMST0 inferred from the SRS: {gmst0_deg:.4f}° "
            f"(maximum deviation between planes: {gmst0_max_dev_deg:.3f}°)"
        )

    config = {
        "semi_major_axis_km": semi_major_axis,
        "eccentricity": p0.eccentricity,
        "inclination_deg": p0.inclin_deg,
        "num_planes": num_planes,
        "sats_per_plane": p0.nbr_sat_pl,
        "inter_plane_phasing_factor": 1,
        "alpha0_deg": system.x_zone_deg if system.f_x_zone else 0.0,
        "min_elevation_deg": 5.0,  # default S.1503
        "min_operating_height_km": min_operating_height_km,  # H_min (orbit.op_ht)
        "frequency_ghz": 0.0,     # will be filled in by mask_info
        "period_s": p0.period_s,
        "rpt_period_s": p0.rpt_period_s,  # ground track repeat period (s)
        "f_precess": p0.f_precess,        # precession flag (MDB)
        "precession_deg_day": p0.precession_deg_day,  # precession rate (°/day)
        "f_stn_keep": p0.f_stn_keep,      # station keeping flag
        "keep_range_deg": p0.keep_range_deg,  # Wdelta: station keeping half-range (°)
        "gmst0_deg": gmst0_deg,           # initial Earth rotation (ECEF vs ECI)
        "gmst0_max_dev_deg": gmst0_max_dev_deg,
    }

    # Some filings bring ``right_asc`` zeroed for all planes, but
    # ``long_asc`` correctly distributed in terrestrial longitude. In those cases,
    # we reconstruct the inertial RAAN so the constellation does not collapse into
    # multiple overlapping satellites.
    right_asc_values = [
        float(p.right_asc_deg) % 360.0
        for p in system.orbit_planes
        if math.isfinite(p.right_asc_deg)
    ]
    long_asc_values = [
        float(p.long_asc_deg) % 360.0
        for p in system.orbit_planes
        if math.isfinite(p.long_asc_deg)
    ]
    right_asc_span_deg = 0.0
    long_asc_span_deg = 0.0
    if right_asc_values:
        right_asc_span_deg = max(right_asc_values) - min(right_asc_values)
    if long_asc_values:
        long_asc_span_deg = max(long_asc_values) - min(long_asc_values)
    derive_raan_from_long_asc = (
        len(system.orbit_planes) > 1
        and right_asc_span_deg < 1e-6
        and long_asc_span_deg > 1.0
    )
    if derive_raan_from_long_asc:
        logger.info(
            "Degenerate RAAN in the SRS; reconstructing RAAN per plane via "
            "RAAN = long_asc + GMST0."
        )

    # Detailed information for each plane (full orbital parameters)
    config["planes"] = []
    for plane in system.orbit_planes:
        a_km = plane.semi_major_axis_km
        if plane.op_height_km > 100:
            a_km = plane.op_height_km + RE_KM
        n_sats = plane.nbr_sat_pl
        phase_map = system.phase_by_orbit.get(plane.orb_id, {})
        # 0-based list to ease direct use in create_constellation_from_config
        phase_angles_deg = [
            (float(phase_map[sid]) if sid in phase_map else None)
            for sid in range(1, n_sats + 1)
        ]
        raan_deg = plane.right_asc_deg
        if derive_raan_from_long_asc:
            raan_deg = (plane.long_asc_deg + gmst0_deg) % 360.0
        config["planes"].append({
            "orb_id": plane.orb_id,
            "semi_major_axis_km": a_km,
            "eccentricity": plane.eccentricity,
            "inclination_deg": plane.inclin_deg,
            "raan_deg": raan_deg,
            "perigee_arg_deg": plane.perigee_arg_deg,
            "long_asc_deg": plane.long_asc_deg,
            "sats_per_plane": n_sats,
            "phase_angles_deg": phase_angles_deg,
            "min_operating_height_km": (
                plane.op_height_km if plane.op_height_km > 0.0 else 0.0
            ),
        })

    return config
