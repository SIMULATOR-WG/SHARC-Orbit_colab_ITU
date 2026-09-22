"""National band-occupancy catalogues: licensed stations + ITU Space IFIC SNS.

Three kinds of input, any of which may be absent:

* a national licensed-station table, uploaded by the administration that
  issued it, in the documented CSV shape;
* BR IFIC online ISO / SRS (subscription) — complete ``SRS.mdb``,
  plus any additional filing ``.mdb`` the user indexes for study.

The public weekly ``ificXXXX.mdb`` is not a source: it only carries that
week's publications and does not hold the assignments an occupancy
evaluation needs.

Every notice is indexed; the country is a FILTER over the result, not a
property of the index, so one cache serves every administration.

There is no built-in national catalogue and no download of one. An
administration that wants its licensed stations on the chart uploads a table.

Parsed catalogs are cached under ``streamlit_app/data/br_occupancy/`` — the
directory keeps its original name so an existing installation does not lose
the catalogues it has already indexed.
"""
from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from . import DATA_ROOT, REPO_ROOT

ITU_BRIFIC_BASE = "https://www.itu.int/epublications/brific-space"
# Same Download-ISO control as the BR IFIC portal banner.
_BRIFIC_ISO_PATH = "iso"
_BRIFIC_SRS_ISO_PATHS = (
    "/databases/SRS_Data/srs3079.zip",
    "/Databases/SRS_Data/srs3079.zip",
    "/databases/SRS_Data/SRS.mdb",
    "/Databases/SRS_Data/SRS.mdb",
    "/Databases/SRS_Data/srs.mdb",
    "/SRS_Data/SRS.mdb",
)
ANATEL_SUBFAIXAS = "stel_satelites_subfaixas.csv"

# The on-disk name predates the generalisation and is deliberately NOT renamed:
# it holds every catalogue an installation has indexed, and renaming it would
# orphan them. Only the code above it stopped naming one country.
CACHE_DIR = DATA_ROOT / "br_occupancy"
ANATEL_DIR = CACHE_DIR / "anatel"
SNS_DIR = CACHE_DIR / "sns"
ANATEL_META = CACHE_DIR / "anatel_meta.json"
SNS_META = CACHE_DIR / "sns_meta.json"
ANATEL_CATALOG = CACHE_DIR / "anatel_catalog.json"
SNS_CATALOG = CACHE_DIR / "sns_catalog.json"
# Registry of indexed filing catalogues. Until now the page held exactly ONE:
# every ingest wrote the two files above, so indexing a second SRS destroyed
# the first without saying so — the developer's own cache shows a weekly IFIC
# indexed at 07:40 and overwritten by the full SRS at 09:59.
SOURCES_DIR = CACHE_DIR / "sources"
SOURCES_INDEX = SOURCES_DIR / "index.json"

# The two tags a licensed row can carry. "anatel" is the catalogue tag that
# keeps its historic name so a cached JSON stays byte-identical; anything an
# administration uploads is "national". Both mean the same thing — a licensed
# station, not an ITU filing — so every test of "is this licensed?" must
# accept both, which is what `is_national_source` is for.
SRC_ANATEL = "anatel"
SRC_NATIONAL = "national"
_NATIONAL_SOURCES = frozenset({SRC_ANATEL, SRC_NATIONAL})


def is_national_source(source: str) -> bool:
    """True for a licensed-station row, whoever licensed it."""
    return (source or "") in _NATIONAL_SOURCES


_ADM_BRAZIL = "B"
# ITU Preface special geographical areas that include Brazil.
# XAA = worldwide; XR2 = ITU Region 2 (Americas). Not XR1 / XR3.
_BRAZIL_IN_SERVICE = frozenset({"B", "XR2", "XAA"})
# Bump when Brazil-selection rules change so a cached catalog is rebuilt.
SNS_SELECT_LOGIC = 3
# The one select_logic a catalogue can be lifted out of without re-reading the
# MDB: version 2 wrote earth-station bands in the wrong direction, and the two
# lists are a plain swap. Pinned to the exact version so a later bump cannot
# re-invert rows that are already right.
_EARTH_DIR_FIXED_AT = 3
_IFIC_NO_RE_NAME = re.compile(
    r"(?:ific|br[\s._-]*ific)[\s._-]*(\d{4})", re.I
)


@dataclass
class OccupancySystem:
    """One selectable row: Anatel station or SNS notice."""

    id: str
    source: str                    # "anatel" | "national" | "sns"
    name: str
    operator: str = ""
    orbit: str = ""                # GEO | NGEO | G | N | …
    position: str = ""
    rf_bands: list[str] = field(default_factory=list)
    downlink_ghz: list[list[float]] = field(default_factory=list)
    uplink_ghz: list[list[float]] = field(default_factory=list)
    ntc_id: str = ""
    ntc_type: str = ""
    adm: str = ""
    # Country evidence, attached at index time so the filter can run AFTER
    # indexing. The SRS locates a notice in four different ways and they do not
    # agree in coverage: 15 909 of 15 909 notices carry a notifying
    # administration, but only 2 186 carry a service area, so a filter built on
    # service areas alone would lose most of the file.
    ntwk_org: str = ""                              # inter/regional org, not a country
    srv_ctry: list[str] = field(default_factory=list)   # service area covers these
    srv_excl: list[str] = field(default_factory=list)   # ... explicitly excluded
    es_ctry: list[str] = field(default_factory=list)    # earth stations in these
    kind: str = ""                                  # "space" | "earth" | ""
    source_id: str = ""                             # which indexed catalogue
    extra: dict[str, Any] = field(default_factory=dict)

    def intervals(self, direction: str) -> list[tuple[float, float]]:
        """``direction``: ``downlink`` / ``uplink`` / ``both``."""
        d = [(float(a), float(b)) for a, b in self.downlink_ghz]
        u = [(float(a), float(b)) for a, b in self.uplink_ghz]
        if direction == "uplink":
            return merge_intervals(u)
        if direction == "both":
            return merge_intervals(d + u)
        return merge_intervals(d)

    def country_codes(self, *, rule: str = "serves") -> set[str]:
        """ITU symbols this system can be said to occupy spectrum in.

        ``rule`` chooses how much evidence counts:
          ``notified`` — only the notifying administration;
          ``serves``   — plus the declared service area and earth-station
                         countries, which is the reading the Brazil-only page
                         used and the one that keeps a foreign filing that
                         covers the country.

        ``srv_excl`` is NOT subtracted here, and that is deliberate.
        ``f_excl_api`` sits on ``srv_area(grp_id, ctry)``, so an exclusion
        belongs to ONE frequency group, not to the notice: a filing whose
        Ku group serves a country and whose Ka group excludes it does occupy
        spectrum there. Subtracting at system level would drop it, which is a
        granularity error, not a stricter reading — and it is what made an
        earlier version of this method disagree with the shipped catalogue.
        The excluded codes stay on the row as evidence for whoever needs the
        detail.
        """
        out = {c for c in [(self.adm or "").upper()] if c}
        if rule != "notified":
            out |= {c.upper() for c in self.srv_ctry if c}
            out |= {c.upper() for c in self.es_ctry if c}
        return out

    def label(self) -> str:
        """What the picker and the chart rows show.

        This used to print the literal "Anatel" for every licensed row, which
        put Brazil's regulator on another administration's own stations: a
        table uploaded with adm="MEX" rendered as "MEXSAT-1 · Anatel · GEO".
        The row says what it is and which administration licensed it, the same
        way a filing row names its notifying administration.
        """
        bits = [self.name]
        if self.source == "sns" and self.ntc_id:
            bits.append(f"ntc {self.ntc_id}")
        elif is_national_source(self.source):
            bits.append("licensed")
        if self.adm:
            bits.append(self.adm)
        if self.orbit:
            bits.append(self.orbit)
        return " · ".join(bits)


# Letter bands live in their own module so the strip charts can name a band
# without importing the catalogue machinery. Re-exported here because the
# occupancy page and its tests reach for them through ``occ``.
from .freq_bands import (  # noqa: F401
    LETTER_BANDS,
    LETTER_BAND_NAMES,
    frequency_presets,
    intervals_touch_range,
    letter_bands_for,
    nearest_stop,
    slider_stops,
)


def merge_intervals(ivs: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for lo, hi in sorted(ivs):
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


# Narrowest overlap that counts as a shared band, in GHz (1 Hz).
_MIN_OVERLAP_GHZ = 1e-9


def intersect_sets(
    a: list[tuple[float, float]],
    b: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    res = []
    for la, ha in a:
        for lb, hb in b:
            lo, hi = max(la, lb), min(ha, hb)
            # A shared EDGE is not a shared band. 10.7–12.75 against
            # 12.75–14.5 used to yield the zero-width 12.75–12.75, which the
            # page then announced as a common occupied band and printed as
            # "12.750–12.750 GHz". Require real width; 1 Hz is far below any
            # assignment and far above float noise on GHz-scale edges.
            if hi - lo > _MIN_OVERLAP_GHZ:
                res.append((lo, hi))
    return merge_intervals(res)


# ── numbers / names ──────────────────────────────────────────────────────────

def parse_mhz(value: Any) -> float | None:
    """Parse a MHz cell that may use a Brazilian decimal comma."""
    if value is None:
        return None
    s = str(value).strip().replace("\xa0", "").replace(" ", "")
    if not s or s in {".", "-"}:
        return None
    s = s.replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v > 0.0 else None


def mhz_to_ghz_interval(lo_mhz: Any, hi_mhz: Any) -> tuple[float, float] | None:
    lo, hi = parse_mhz(lo_mhz), parse_mhz(hi_mhz)
    if lo is None or hi is None:
        return None
    a, b = sorted((lo / 1000.0, hi / 1000.0))
    if b <= 0 or b - a < 0:
        return None
    return (a, b)


def norm_name(name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", (name or "").upper())


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def _read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def system_from_dict(d: dict[str, Any]) -> OccupancySystem:
    return OccupancySystem(
        id=str(d.get("id") or ""),
        source=str(d.get("source") or ""),
        name=str(d.get("name") or ""),
        operator=str(d.get("operator") or ""),
        orbit=str(d.get("orbit") or ""),
        position=str(d.get("position") or ""),
        rf_bands=list(d.get("rf_bands") or []),
        # Legacy national rows were written before the licensing administration
        # was recorded; they are Anatel's, so they are Brazil's.
        adm=str(d.get("adm") or (_ADM_BRAZIL if d.get("source") == "anatel" else "")),
        ntwk_org=str(d.get("ntwk_org") or ""),
        srv_ctry=[str(c) for c in (d.get("srv_ctry") or [])],
        srv_excl=[str(c) for c in (d.get("srv_excl") or [])],
        es_ctry=[str(c) for c in (d.get("es_ctry") or [])],
        kind=str(d.get("kind") or ""),
        source_id=str(d.get("source_id") or ""),
        downlink_ghz=[list(x) for x in (d.get("downlink_ghz") or [])],
        uplink_ghz=[list(x) for x in (d.get("uplink_ghz") or [])],
        ntc_id=str(d.get("ntc_id") or ""),
        ntc_type=str(d.get("ntc_type") or ""),
        extra=dict(d.get("extra") or {}),
    )


def brific_iso_url(ific_no: str) -> str:
    """Portal 'Download ISO' URL for one BR IFIC edition."""
    no = str(ific_no).strip()
    return (
        f"{ITU_BRIFIC_BASE}/api/v1/ific/edition/{quote(no, safe='')}"
        f"/document?path={_BRIFIC_ISO_PATH}&download=true"
    )


def ific_no_from_name(name: str) -> str:
    """Best-effort IFIC number from an ISO / MDB file name."""
    m = _IFIC_NO_RE_NAME.search(name or "")
    if m:
        return m.group(1)
    m = re.search(r"(?<!\d)(\d{4})(?!\d)", name or "")
    return m.group(1) if m else ""


# ── Anatel zip / CSV ─────────────────────────────────────────────────────────

def _decode_csv_bytes(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _split_csv_rows(text: str) -> tuple[list[str], list[list[str]]]:
    import csv
    from io import StringIO

    sample = text[:4096]
    dialect = csv.excel
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";" if sample.count(";") >= sample.count(",") else ","
    reader = csv.reader(StringIO(text), dialect)
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        return [], []
    header = [c.strip() for c in rows[0]]
    return header, rows[1:]


# Direction vocabulary. The Anatel file says "Subida" / "Descida"; another
# administration will say something else, and the old test — "subida, or starts
# with u" — filed everything it did not recognise as downlink, silently. A word
# that matches neither list is still treated as downlink (the conservative
# reading for a band-occupancy survey) but is reported, so the user learns that
# a column was not understood instead of reading a half-empty uplink chart.
_UPLINK_WORDS = (
    "subida", "uplink", "up-link", "up link", "up", "e-s", "e/s",
    "earth-to-space", "earth to space", "ascendente", "ascending", "ul",
)
_DOWNLINK_WORDS = (
    "descida", "downlink", "down-link", "down link", "down", "s-e", "s/e",
    "space-to-earth", "space to earth", "descendente", "descending", "dl",
)


def _dir_match(value: str, words: "tuple[str, ...]") -> bool:
    v = (value or "").strip().lower()
    if not v:
        return False
    return v in words or any(w in v for w in words if len(w) > 2)


def _is_uplink(value: str) -> bool:
    return _dir_match(value, _UPLINK_WORDS)


def _is_downlink(value: str) -> bool:
    return _dir_match(value, _DOWNLINK_WORDS)


def _col(header: list[str], *needles: str) -> int | None:
    lower = [h.lower() for h in header]
    for n in needles:
        nl = n.lower()
        for i, h in enumerate(lower):
            if h == nl:
                return i
    for n in needles:
        nl = n.lower()
        for i, h in enumerate(lower):
            if nl in h:
                return i
    return None


def parse_anatel_subfaixas_csv(
    text: str,
    *,
    adm: str = _ADM_BRAZIL,
    source: str = SRC_ANATEL,
    id_prefix: str = SRC_ANATEL,
) -> list[OccupancySystem]:
    header, body = _split_csv_rows(text)
    if not header:
        return []
    # Anatel's own spellings first, then English equivalents, so an
    # administration that produces the same table in its own language is read by
    # meaning rather than by Anatel's exact Portuguese headers. The frequency
    # columns are the ones that used to match one spelling only, and a file that
    # named them differently failed whole — see the help entry for the contract.
    i_op = _col(header, "Operador", "operator", "licensee")
    i_name = _col(header, "NomeEstacao_STEL_portal", "NomeEstacao",
                  "station", "station_name", "satellite", "name")
    i_num = _col(header, "NumEstacao_STEL_portal", "station_id", "licence", "license")
    i_orbit = _col(header, "Tipo_orbita_STEL_portal", "Tipo_orbita", "orbit")
    i_pos = _col(header, "PosOrbital_STEL_portal", "PosOrbital",
                 "orbital_position", "longitude", "position")
    i_rf = _col(header, "Banda_RF_estacao_STEL_portal", "Banda_RF", "rf_band", "band")
    i_dir = _col(header, "Sentido_STEL_portal", "Sentido", "direction", "link")
    i_lo = _col(header, "MedFrequenciaInicialMHz_STEL_portal",
                "freq_min_mhz", "frequency_min_mhz", "freq_min", "lower_mhz", "start_mhz")
    i_hi = _col(header, "MedFrequenciaFinalMHz_STEL_portal",
                "freq_max_mhz", "frequency_max_mhz", "freq_max", "upper_mhz", "stop_mhz")
    i_valid = _col(header, "Validade_licença_estacao_espacial", "Validade",
                   "valid_until", "expiry")
    if i_name is None or i_lo is None or i_hi is None:
        # Name the spellings the parser ACCEPTS, not a description of them:
        # the message used to ask for "lower frequency (MHz)", which is not an
        # alias, so a user who added exactly that header failed again.
        missing = [n for n, i in (("station", i_name),
                                  ("freq_min_mhz", i_lo),
                                  ("freq_max_mhz", i_hi)) if i is None]
        raise ValueError(
            "the licensed-station table is missing the mandatory column(s) "
            "(or an accepted alias of them): "
            + ", ".join(missing)
            + ". Header seen: " + "; ".join(header[:12])
            + ". See the page help for the required fields."
        )

    unknown_dir: set[str] = set()
    grouped: dict[str, OccupancySystem] = {}
    for row in body:
        def cell(idx: int | None) -> str:
            if idx is None or idx >= len(row):
                return ""
            return str(row[idx]).strip()

        name = cell(i_name)
        if not name:
            continue
        iv = mhz_to_ghz_interval(cell(i_lo), cell(i_hi))
        if iv is None:
            continue
        sid = f"{id_prefix}:{norm_name(name) or name}"
        sys = grouped.get(sid)
        if sys is None:
            sys = OccupancySystem(
                id=sid,
                source=source,
                name=name,
                operator=cell(i_op),
                orbit=cell(i_orbit).upper() or "GEO",
                position=cell(i_pos),
                # A licensed station is in the licensing country by definition.
                # Without this the national catalogue carried no country at all
                # and vanished under any country filter — the one source whose
                # country is never in doubt.
                adm=adm,
                kind="space",
                extra={"station_id": cell(i_num), "validity": cell(i_valid)},
            )
            grouped[sid] = sys
        if _is_uplink(cell(i_dir)):
            sys.uplink_ghz.append([iv[0], iv[1]])
        else:
            sys.downlink_ghz.append([iv[0], iv[1]])
            if not _is_downlink(cell(i_dir)):
                unknown_dir.add(cell(i_dir).strip())
        rf = cell(i_rf)
        if rf and rf not in sys.rf_bands:
            sys.rf_bands.append(rf)

    out = []
    for sys in grouped.values():
        sys.downlink_ghz = [list(p) for p in merge_intervals(
            [(a, b) for a, b in sys.downlink_ghz])]
        sys.uplink_ghz = [list(p) for p in merge_intervals(
            [(a, b) for a, b in sys.uplink_ghz])]
        out.append(sys)
    out.sort(key=lambda s: s.name.lower())
    return out


def bundled_anatel_csv() -> Path | None:
    p = REPO_ROOT / "docs" / "satelites" / ANATEL_SUBFAIXAS
    return p if p.exists() else None


def load_anatel_catalog() -> list[OccupancySystem]:
    cached = _read_json(ANATEL_CATALOG)
    if isinstance(cached, list) and cached:
        return [system_from_dict(d) for d in cached]
    csv_path = None
    local = ANATEL_DIR / ANATEL_SUBFAIXAS
    if local.exists():
        csv_path = local
    else:
        found = list(ANATEL_DIR.rglob(ANATEL_SUBFAIXAS)) if ANATEL_DIR.exists() else []
        csv_path = found[0] if found else bundled_anatel_csv()
    if csv_path is None:
        return []
    catalog = parse_anatel_subfaixas_csv(_decode_csv_bytes(csv_path.read_bytes()))
    _write_json(ANATEL_CATALOG, [asdict(s) for s in catalog])
    return catalog


# ── SNS / Space IFIC ─────────────────────────────────────────────────────────

def _score_srs_iso_path(path: str) -> int:
    n = path.replace("\\", "/").lower()
    base = Path(n).name
    s = 0
    if "srs" in n:
        s += 20
    if "srs_data" in n:
        s += 10
    if base.startswith("ific") and "srs" not in n:
        s -= 15
    if "sps" in n or "gims" in n or "30b" in n:
        s -= 20
    return s


def _xorriso_find(iso_path: Path, name_glob: str) -> list[str]:
    xorriso = shutil.which("xorriso")
    if not xorriso:
        return []
    proc = subprocess.run(
        [xorriso, "-indev", str(iso_path), "-find", "/", "-name", name_glob, "--"],
        capture_output=True, text=True, timeout=300, check=False,
    )
    blob = (proc.stdout or "") + "\n" + (proc.stderr or "")
    paths = re.findall(r"'(/[^']+)'", blob)
    return [p for p in paths if not p.endswith("/")]


def _xorriso_list_mdb(iso_path: Path) -> list[str]:
    return [
        p for p in _xorriso_find(iso_path, "*.mdb")
        if p.lower().endswith(".mdb")
    ]


def _xorriso_extract(iso_path: Path, iso_mdb: str, dest: Path) -> None:
    xorriso = shutil.which("xorriso")
    if not xorriso:
        raise FileNotFoundError("xorriso is not on PATH")
    tmp = dest.with_name(dest.name + ".extracting")
    if tmp.exists():
        tmp.unlink()
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [xorriso, "-osirrox", "on", "-indev", str(iso_path),
         "-extract", iso_mdb, str(tmp), "--"],
        capture_output=True, text=True, timeout=3600, check=False,
    )
    if proc.returncode != 0 or not tmp.exists():
        err = (proc.stderr or proc.stdout or "").strip()[-2000:]
        raise RuntimeError(f"xorriso extract {iso_mdb} failed: {err or proc.returncode}")
    tmp.replace(dest)


def extract_srs_from_iso(iso_path: Path, dest: Path) -> Path:
    """Pull the SRS out of a BR IFIC ISO (``databases/SRS_Data/srsNNNN.zip``).

    ``dest`` is a directory for the split parts, or a ``.mdb`` file for a
    single-file SRS. Returns the catalog MDB (part 1).
    """
    if not iso_path.exists():
        raise FileNotFoundError(iso_path)
    if shutil.which("xorriso") is None:
        raise RuntimeError(
            "Need `xorriso` to extract the SRS from the ISO "
            "(install package `xorriso` / `libisoburn`)."
        )
    dest = Path(dest)
    dest_dir = dest if dest.suffix.lower() not in {".mdb", ".accdb"} else dest.parent
    dest_dir.mkdir(parents=True, exist_ok=True)

    zips = _xorriso_find(iso_path, "*srs*.zip") + _xorriso_find(iso_path, "*SRS*.zip")
    zips = list(dict.fromkeys(zips))
    last_err: Exception | None = None
    for iso_zip in zips:
        try:
            zpath = dest_dir / Path(iso_zip).name
            _xorriso_extract(iso_path, iso_zip, zpath)
            catalog = unpack_srs_zip(zpath, dest_dir)
            if dest.suffix.lower() in {".mdb", ".accdb"} and catalog != dest:
                shutil.copy2(catalog, dest)
                return dest
            return catalog
        except Exception as exc:  # noqa: BLE001
            last_err = exc

    tried: list[str] = []
    listed = _xorriso_list_mdb(iso_path)
    listed.sort(key=_score_srs_iso_path, reverse=True)
    candidates = [p for p in listed if _score_srs_iso_path(p) > 0]
    for known in _BRIFIC_SRS_ISO_PATHS:
        if known.lower().endswith(".mdb") and known not in candidates:
            candidates.append(known)
    mdb_dest = dest if dest.suffix.lower() in {".mdb", ".accdb"} else dest_dir / "SRS.mdb"
    for iso_mdb in candidates:
        tried.append(iso_mdb)
        try:
            _xorriso_extract(iso_path, iso_mdb, mdb_dest)
            return mdb_dest
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            continue
    raise FileNotFoundError(
        f"No SRS (srsNNNN.zip / SRS.mdb) in {iso_path}. Tried zips {zips} "
        f"and mdbs {tried}. Last error: {last_err}"
    )


def locate_srs_parts(src: Path) -> tuple[Path, Path]:
    """``(catalog_mdb, grp_mdb)`` for a single SRS file or ITU split parts.

    Recent BR IFICs ship ``srsNNNN_part1of4.mdb`` (notice / geo / srv_area)
    and ``…_part3of4.mdb`` (``grp`` frequency assignments). A single
    ``.mdb`` that already holds both is used as catalog and as ``grp``.
    """
    src = Path(src).expanduser().resolve()
    if src.is_file() and src.suffix.lower() in {".mdb", ".accdb"}:
        files = [
            p for p in src.parent.iterdir()
            if p.is_file() and p.suffix.lower() in {".mdb", ".accdb"}
        ]
        if src not in [p.resolve() for p in files]:
            files.append(src)
    elif src.is_dir():
        files = [
            p for p in src.iterdir()
            if p.is_file() and p.suffix.lower() in {".mdb", ".accdb"}
        ]
    else:
        raise FileNotFoundError(src)
    if not files:
        raise FileNotFoundError(f"No .mdb under {src}")
    by_part: dict[int, Path] = {}
    for p in files:
        m = re.search(r"part(\d+)of(\d+)", p.name, re.I)
        if m:
            by_part[int(m.group(1))] = p
    if by_part:
        catalog = by_part.get(1) or by_part[min(by_part)]
        grp = by_part.get(3) or catalog
        return catalog, grp
    one = src if src.is_file() else files[0]
    return one, one


def group_upload_names(names: "list[str]") -> "list[list[str]]":
    """Split dropped file names into independent catalogues.

    The ITU ships one SRS as ``srsNNNN_part1of4.mdb`` … ``_part4of4.mdb``, and
    those belong together. Two unrelated filings do not: dropped in one folder
    they look like parts to :func:`locate_srs_parts`, which then picks the
    first and indexes only that one, silently losing the rest. Grouping by the
    part-name stem keeps a split SRS whole and gives every other file its own
    catalogue.
    """
    groups: dict[str, list[str]] = {}
    singles: list[list[str]] = []
    for name in names:
        stem = Path(name).name
        m = re.search(r"(.*?)[_-]?part\d+of\d+", stem, re.I)
        if m and m.group(1):
            groups.setdefault(m.group(1).lower(), []).append(name)
        else:
            singles.append([name])
    return [sorted(v) for v in groups.values()] + singles


def stage_upload_groups(
    files: "list[tuple[str, bytes]]", root: Path,
) -> "list[tuple[str, Path]]":
    """Write each independent catalogue into its own directory under ``root``.

    Returns ``(name_hint, path)`` per group: the path is the single file when
    the group holds one, and the directory when it holds split parts, which is
    what the ingest functions expect.
    """
    by_name = dict(files)
    out: list[tuple[str, Path]] = []
    for i, group in enumerate(group_upload_names([n for n, _ in files])):
        dest = root / f"g{i:02d}"
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)
        for name in group:
            (dest / Path(name).name).write_bytes(by_name[name])
        hint = Path(group[0]).stem
        if len(group) == 1:
            out.append((hint, dest / Path(group[0]).name))
        else:
            out.append((re.sub(r"[_-]?part\d+of\d+", "", hint, flags=re.I), dest))
    return out


def unpack_srs_zip(zip_path: Path, dest_dir: Path) -> Path:
    """Unzip ``srsNNNN.zip`` (split MDBs) and return the catalog part."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest_dir)
    catalog, _grp = locate_srs_parts(dest_dir)
    return catalog


def zip_kind(zip_path: Path) -> str:
    """``iso`` (bookshop image), ``srs`` (split SRS mdbs), or ``empty``."""
    with zipfile.ZipFile(zip_path) as zf:
        names = [n.lower() for n in zf.namelist() if not n.endswith("/")]
    if any(n.endswith(".iso") for n in names):
        return "iso"
    if any(n.endswith(".mdb") or n.endswith(".accdb") for n in names):
        return "srs"
    return "empty"


def _mdb_export(mdb_path: Path, table: str) -> list[dict[str, str]]:
    from . import engine  # lazy — pulls src/ onto sys.path

    return engine.srs_reader_module()._run_mdb_export(str(mdb_path), table)


def _cell(row: dict[str, str], key: str) -> str:
    return (row.get(key) or "").strip().strip('"')


def ctry_covers_brazil(ctry: str, *, excl: str = "") -> bool:
    """True if an SNS ``srv_area.ctry`` code covers Brazil.

    Country ``B``, ITU Region 2 (``XR2``) and worldwide (``XAA``).
    ``f_excl_api='Y'`` means the code is *excluded* from the service area.
    """
    if (excl or "").strip().upper() == "Y":
        return False
    return (ctry or "").strip().upper() in _BRAZIL_IN_SERVICE


def select_brazil_ntcs(
    notices: list[dict[str, str]],
    sat_name_by_ntc: dict[str, str],
    *,
    srv_br_ntcs: set[str],
    es_br_ntcs: set[str],
    anatel_names: set[str],
) -> dict[str, list[str]]:
    """``ntc_id → reasons`` for systems that **operate in Brazil**.

    Notifying administration need not be ``B``. A USA/CHN/… filing is kept
    when its service area covers Brazil (``B`` / ``XR2`` / ``XAA``), it has
    an earth station in B, or the satellite name matches an Anatel licence.
    ``adm=B`` is still kept (national filings).
    """
    wanted = anatel_names or set()
    keep: dict[str, list[str]] = {}
    for row in notices:
        ntc = _cell(row, "ntc_id")
        if not ntc:
            continue
        reasons: list[str] = []
        if _cell(row, "adm").upper() == _ADM_BRAZIL:
            reasons.append("adm_b")
        if ntc in srv_br_ntcs:
            reasons.append("srv_br")
        if ntc in es_br_ntcs:
            reasons.append("es_br")
        sat = sat_name_by_ntc.get(ntc) or _cell(row, "sat_name")
        if sat and norm_name(sat) in wanted:
            reasons.append("anatel_name")
        if reasons:
            keep[ntc] = reasons
    return keep


def parse_sns_catalog(
    mdb_path: Path,
    *,
    anatel_names: set[str] | None = None,
    grp_mdb: Path | None = None,
    keep_only: str = "",
) -> tuple[list[OccupancySystem], dict[str, Any]]:
    """Index an SRS, keeping every notice and attaching its country evidence.

    The page used to index only what operated in Brazil — 999 of 15 909 — which
    made the country a property of the INDEX and so unchangeable without a
    re-index. Everything is kept now and the country becomes a filter over the
    result; the expensive reads were already unconditional, so this costs no
    extra time, only a larger cached catalogue.

    ``keep_only`` restores the old behaviour for one ITU symbol (``"B"`` for
    Brazil), which the tests use to show the two agree.
    """
    # Validated before any I/O: the compatibility path reproduces one rule and
    # silently indexing everything instead would be worse than refusing.
    if keep_only.upper() not in ("", _ADM_BRAZIL):
        raise ValueError(
            f"keep_only={keep_only!r} is not supported; pass '' to index the "
            "whole SRS and filter by country afterwards."
        )
    mdb_path = Path(mdb_path)
    grp_mdb = Path(grp_mdb) if grp_mdb is not None else mdb_path
    notice = _mdb_export(mdb_path, "notice")
    stats: dict[str, Any] = {"n_notice_total": len(notice), "kind": "srs"}
    if keep_only:
        # Declared only on the compatibility path: these four count notices
        # against ONE country's rule, so on a general index they are not a
        # zero, they are a category error.
        stats.update(n_adm_b=0, n_srv_br=0, n_es_br=0, n_name_match=0)
    if not notice:
        return [], stats

    sat_name_by_ntc: dict[str, str] = {}
    geo_lon: dict[str, str] = {}
    for table in ("geo", "non_geo", "com_el"):
        try:
            rows = _mdb_export(mdb_path, table)
        except Exception:  # noqa: BLE001
            continue
        for row in rows:
            ntc = _cell(row, "ntc_id")
            sat = _cell(row, "sat_name")
            if ntc and sat and ntc not in sat_name_by_ntc:
                sat_name_by_ntc[ntc] = sat
            lon = _cell(row, "long_nom")
            if ntc and lon and ntc not in geo_lon:
                geo_lon[ntc] = lon

    grp_rows = _mdb_export(grp_mdb, "grp")
    gid_to_ntc = {_cell(r, "grp_id"): _cell(r, "ntc_id") for r in grp_rows}

    # Country evidence for EVERY notice, not just the ones in one country.
    es_ctry: dict[str, set[str]] = {}
    try:
        for row in _mdb_export(mdb_path, "es_ctry"):
            ntc, code = _cell(row, "ntc_id"), _cell(row, "ctry").upper()
            if ntc and code:
                es_ctry.setdefault(ntc, set()).add(code)
    except Exception:  # noqa: BLE001
        pass
    srv_ctry: dict[str, set[str]] = {}
    srv_excl: dict[str, set[str]] = {}
    try:
        for row in _mdb_export(mdb_path, "srv_area"):
            ntc = gid_to_ntc.get(_cell(row, "grp_id"), "")
            code = _cell(row, "ctry").upper()
            if not ntc or not code:
                continue
            excluded = _cell(row, "f_excl_api").strip().upper() == "Y"
            (srv_excl if excluded else srv_ctry).setdefault(ntc, set()).add(code)
    except Exception:  # noqa: BLE001
        pass
    # Brazil's selection machinery belongs to the compatibility path ONLY.
    # It used to run on every index, which was three separate wrongs: it read
    # srv_area a second whole time (the 1.7 GB SRS pays for that), it loaded
    # Anatel's licensed names to name-match them against another country's
    # filings, and it wrote four Brazil tallies into the meta.json of every
    # catalogue — an uploaded USA filing carried "n_adm_b": 0, and the page
    # printed those counters next to the systems count.
    if keep_only:
        # The compatibility sets are rebuilt with the ORIGINAL per-row
        # predicate. Deriving them from the aggregated dicts above is not the
        # same thing: a notice with one row (ctry=B, excl=N) and another
        # (ctry=B, excl=Y) is a hit row-by-row but is cancelled by an aggregate
        # subtraction, which cost exactly one notice of the 999 when measured
        # against the cached catalogue.
        es_br = {n for n, c in es_ctry.items() if _ADM_BRAZIL in c}
        srv_br: set[str] = set()
        try:
            for row in _mdb_export(mdb_path, "srv_area"):
                if ctry_covers_brazil(_cell(row, "ctry"),
                                      excl=_cell(row, "f_excl_api")):
                    _n = gid_to_ntc.get(_cell(row, "grp_id"), "")
                    if _n:
                        srv_br.add(_n)
        except Exception:  # noqa: BLE001
            pass
        keep_reasons = select_brazil_ntcs(
            notice,
            sat_name_by_ntc,
            srv_br_ntcs=srv_br,
            es_br_ntcs=es_br,
            anatel_names=anatel_names or set(),
        )
        stats["n_adm_b"] = sum(1 for r in keep_reasons.values() if "adm_b" in r)
        stats["n_srv_br"] = sum(1 for r in keep_reasons.values() if "srv_br" in r)
        stats["n_es_br"] = sum(1 for r in keep_reasons.values() if "es_br" in r)
        stats["n_name_match"] = sum(
            1 for r in keep_reasons.values() if "anatel_name" in r)
    else:
        # Every notice, no per-country provenance. The country is a filter over
        # the result now, so a reason vocabulary written in one country's terms
        # would only be able to say "no" about everybody else.
        keep_reasons = {
            _cell(r, "ntc_id"): [] for r in notice if _cell(r, "ntc_id")
        }
    notice_by_ntc = {_cell(r, "ntc_id"): r for r in notice}

    grouped: dict[str, OccupancySystem] = {}
    for ntc, reasons in keep_reasons.items():
        row = notice_by_ntc[ntc]
        ntc_type = _cell(row, "ntc_type").upper()
        grouped[ntc] = OccupancySystem(
            id=f"sns:{ntc}",
            source="sns",
            name=sat_name_by_ntc.get(ntc) or ntc,
            operator=_cell(row, "ntwk_org"),
            orbit={"G": "GEO", "N": "NGEO"}.get(ntc_type, ntc_type or "—"),
            position=geo_lon.get(ntc, ""),
            ntc_id=ntc,
            ntc_type=ntc_type,
            adm=_cell(row, "adm"),
            ntwk_org=_cell(row, "ntwk_org"),
            srv_ctry=sorted(srv_ctry.get(ntc, ())),
            srv_excl=sorted(srv_excl.get(ntc, ())),
            es_ctry=sorted(es_ctry.get(ntc, ())),
            kind=("earth" if ntc_type in ("S", "R", "T")
                  else "space" if ntc_type in ("G", "N") else ""),
            extra={
                "prov": _cell(row, "prov"),
                "ntf_rsn": _cell(row, "ntf_rsn"),
                "keep": reasons,
            },
        )

    for row in grp_rows:
        ntc = _cell(row, "ntc_id")
        sys = grouped.get(ntc)
        if sys is None:
            continue
        iv = mhz_to_ghz_interval(row.get("freq_min"), row.get("freq_max"))
        if iv is None:
            continue
        # `emi_rcp` is written relative to the station the notice is FOR, so
        # the same letter means opposite directions on the two kinds of notice:
        # a satellite that receives is fed by an uplink, an earth station that
        # receives is fed by a downlink. Reading every group with the space
        # convention put every earth-station band in the wrong direction —
        # 7 645 of the 15 909 SRS notices are earth stations, and an uploaded
        # filing is mostly earth stations (86 of 87 in USASAT-NGSO-3series), so
        # a Downlink filter over such a catalogue returned nothing at all.
        receives = _cell(row, "emi_rcp").upper().startswith("R")
        uplink = (not receives) if sys.kind == "earth" else receives
        if uplink:
            sys.uplink_ghz.append([iv[0], iv[1]])
        else:
            sys.downlink_ghz.append([iv[0], iv[1]])

    out = []
    for sys in grouped.values():
        sys.downlink_ghz = [list(p) for p in merge_intervals(
            [(a, b) for a, b in sys.downlink_ghz])]
        sys.uplink_ghz = [list(p) for p in merge_intervals(
            [(a, b) for a, b in sys.uplink_ghz])]
        out.append(sys)
    out.sort(key=lambda s: (s.name.lower(), s.ntc_id))
    stats["n_systems"] = len(out)
    return out, stats


def parse_sns_brazil(
    mdb_path: Path,
    *,
    anatel_names: set[str] | None = None,
    grp_mdb: Path | None = None,
) -> tuple[list[OccupancySystem], dict[str, Any]]:
    """Brazil-only indexing — the behaviour before the country became a filter.

    Kept so the change can be shown to be behaviour-preserving; new code should
    call :func:`parse_sns_catalog` and filter afterwards.
    """
    return parse_sns_catalog(mdb_path, anatel_names=anatel_names,
                             grp_mdb=grp_mdb, keep_only=_ADM_BRAZIL)


# ── registry of indexed filing catalogues ───────────────────────────────────

@dataclass
class CatalogSource:
    """One indexed filing catalogue: a complete SRS, or an uploaded filing."""

    id: str
    label: str
    kind: str = "srs"                  # "srs" | "ific"
    n_systems: int = 0
    n_notice_total: int = 0
    fetched_at: str = ""
    mdb: str = ""
    ific_no: str = ""
    enabled: bool = True

    @property
    def dir(self) -> Path:
        return SOURCES_DIR / self.id

    def describe(self) -> str:
        bits = [f"{self.n_systems} system(s)"]
        if self.n_notice_total:
            bits.append(f"{self.n_notice_total} notices in the file")
        if self.ific_no:
            bits.append(f"IFIC {self.ific_no}")
        if self.fetched_at:
            bits.append(f"indexed {self.fetched_at.replace('T', ' ').replace('Z', ' UTC')}")
        return " · ".join(bits)


def source_id_for(meta: dict[str, Any]) -> str:
    """A stable, readable id for a catalogue about to be registered.

    Derived from what the file says about itself rather than from a content
    hash: hashing part1of4 alone means reading 1.77 GB, which is minutes of
    work to answer a question the metadata already answers.
    """
    ific = str(meta.get("ific_no") or "").strip()
    kind = str(meta.get("kind") or "srs").strip() or "srs"
    if ific:
        return f"{kind}{ific}"
    stem = Path(str(meta.get("mdb") or "")).stem or "source"
    return re.sub(r"[^A-Za-z0-9_-]+", "-", stem)[:48] or "source"


def read_sources() -> list[CatalogSource]:
    """Every registered catalogue, migrating the single legacy slot on first use."""
    _migrate_legacy_sns()
    rows = _read_json(SOURCES_INDEX)
    if not isinstance(rows, list):
        return []
    out = []
    for d in rows:
        if not isinstance(d, dict) or not d.get("id"):
            continue
        out.append(CatalogSource(**{
            k: d.get(k, getattr(CatalogSource, k, None))
            for k in ("id", "label", "kind", "n_systems", "n_notice_total",
                      "fetched_at", "mdb", "ific_no", "enabled")
            if k in d or k in ("id", "label")
        }))
    return out


def _write_sources(sources: list[CatalogSource]) -> None:
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    _write_json(SOURCES_INDEX, [asdict(s) for s in sources])


def _migrate_legacy_sns() -> None:
    """Fold the one pre-registry catalogue into the registry, once.

    The old files are left where they are: a downgrade to an earlier build must
    still find them, and they cost 0.7 MB.
    """
    if SOURCES_INDEX.exists():
        return
    cached = _read_json(SNS_CATALOG)
    if not isinstance(cached, list) or not cached:
        return
    meta = sns_meta()
    sid = source_id_for(meta)
    src = CatalogSource(
        id=sid,
        label=str(meta.get("ific_no") and f"IFIC {meta['ific_no']}" or "Indexed SRS"),
        kind=str(meta.get("kind") or "srs"),
        n_systems=len(cached),
        n_notice_total=int(meta.get("n_notice_total") or 0),
        fetched_at=str(meta.get("fetched_at") or ""),
        mdb=str(meta.get("mdb") or ""),
        ific_no=str(meta.get("ific_no") or ""),
    )
    src.dir.mkdir(parents=True, exist_ok=True)
    _write_json(src.dir / "catalog.json",
                [{**d, "source_id": sid} for d in cached])
    _write_json(src.dir / "meta.json", meta)
    _write_sources([src])


def _write_source(src: CatalogSource, catalog: list[OccupancySystem],
                  meta: dict[str, Any]) -> CatalogSource:
    """Register (or replace) one catalogue without touching the others."""
    src.dir.mkdir(parents=True, exist_ok=True)
    # A licensed catalogue is not a filing, so its rows are not prefixed "sns:".
    _pfx = SRC_NATIONAL if src.kind == "national" else "sns"
    for sysm in catalog:
        sysm.source_id = src.id
        # Two catalogues share ntc_ids — 77 of the 174 weekly-IFIC notices also
        # appear in the full SRS — so the id must name its source or one row
        # silently shadows the other wherever systems are keyed by id.
        if not sysm.id.startswith(f"{_pfx}:{src.id}:"):
            sysm.id = f"{_pfx}:{src.id}:{sysm.ntc_id or sysm.name}"
    _write_json(src.dir / "catalog.json", [asdict(s) for s in catalog])
    meta = dict(meta)
    meta["n_systems"] = len(catalog)
    meta["fetched_at"] = meta.get("fetched_at") or _now_iso()
    meta["select_logic"] = SNS_SELECT_LOGIC
    _write_json(src.dir / "meta.json", meta)

    src.n_systems = len(catalog)
    src.n_notice_total = int(meta.get("n_notice_total") or 0)
    src.fetched_at = str(meta["fetched_at"])
    src.mdb = str(meta.get("mdb") or "")
    src.ific_no = str(meta.get("ific_no") or "")
    others = [s for s in read_sources() if s.id != src.id]
    _write_sources([*others, src])
    return src


def _swap_earth_directions(rows: list[dict[str, Any]]) -> int:
    """Undo the space-convention reading of `emi_rcp` on earth-station rows.

    Catalogues indexed under select_logic < 3 stored every earth station's
    bands in the opposite direction. The two lists are a plain swap, so the
    correction is exact and needs no second pass over the MDB — which matters,
    because re-indexing the whole SRS costs minutes and the MDB may be gone.
    """
    n = 0
    for d in rows:
        if d.get("kind") != "earth":
            continue
        if not d.get("downlink_ghz") and not d.get("uplink_ghz"):
            continue
        d["downlink_ghz"], d["uplink_ghz"] = (
            d.get("uplink_ghz") or [], d.get("downlink_ghz") or [])
        n += 1
    return n


def load_source_catalog(source_id: str) -> list[OccupancySystem]:
    src_dir = SOURCES_DIR / source_id
    rows = _read_json(src_dir / "catalog.json")
    if not isinstance(rows, list):
        return []
    meta = _read_json(src_dir / "meta.json")
    meta = meta if isinstance(meta, dict) else {}
    try:
        logic = int(meta.get("select_logic") or 0)
    except (TypeError, ValueError):
        logic = 0
    if logic == _EARTH_DIR_FIXED_AT - 1:
        _swap_earth_directions(rows)
        meta["select_logic"] = _EARTH_DIR_FIXED_AT
        _write_json(src_dir / "catalog.json", rows)
        _write_json(src_dir / "meta.json", meta)
    return [system_from_dict(d) for d in rows]


def remove_source(source_id: str) -> bool:
    """Forget one catalogue. The indexed MDB on disk is left alone."""
    keep = [s for s in read_sources() if s.id != source_id]
    if len(keep) == len(read_sources()):
        return False
    shutil.rmtree(SOURCES_DIR / source_id, ignore_errors=True)
    _write_sources(keep)
    return True


def pick_catalog_csv(zip_bytes: bytes) -> tuple[str, bytes]:
    """The licensed-station table inside an uploaded zip, as (name, bytes).

    Anatel's zip carries eight members and only one of them is the sub-band
    table, so a foreign zip is read the same way: the Anatel member name first,
    then the only CSV, then the largest one — and an explicit error rather than
    a guess when there is no CSV at all.
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        exact = [n for n in names if Path(n).name.lower() == ANATEL_SUBFAIXAS.lower()]
        csvs = [n for n in names if n.lower().endswith(".csv")]
        if exact:
            pick = exact[0]
        elif len(csvs) == 1:
            pick = csvs[0]
        elif csvs:
            pick = max(csvs, key=lambda n: zf.getinfo(n).file_size)
        else:
            raise ValueError(
                "that zip holds no .csv: " + (", ".join(names[:6]) or "(empty)")
            )
        return pick, zf.read(pick)


def register_national_csv(
    text: str, *, label: str, adm: str, slug: str = "",
) -> CatalogSource:
    """Index a licensed-station table supplied by an administration.

    Same registry as the filing catalogues, with ``kind="national"``: one page
    then lists every loaded dataset in one place, and a national catalogue can
    be removed the same way. ``adm`` is the ITU symbol of the licensing
    administration, which is what makes the rows findable by country.

    Raises ``ValueError`` when the table cannot be trusted, rather than drawing
    a plausible-looking wrong chart.
    """
    sid = re.sub(r"[^A-Za-z0-9_-]+", "-", (slug or label or adm or "national")).strip("-")
    sid = f"nat-{sid[:40].lower() or 'catalogue'}"
    # Tagged as its own catalogue, not as Anatel's. The tag reaches the user:
    # it is the "source" column of the Assignments table and of the CSV the
    # page exports, so stamping "anatel" on another administration's stations
    # misattributed them in a file that leaves the tool.
    catalog = parse_anatel_subfaixas_csv(
        text, adm=(adm or "").strip().upper(),
        source=SRC_NATIONAL, id_prefix=sid,
    )
    if not catalog:
        raise ValueError("no station rows found in that table")
    edges = [e for s_ in catalog for iv in s_.intervals("both") for e in iv]
    if not edges:
        raise ValueError("no usable frequency pair in that table")
    # The GHz-instead-of-MHz trap: it parses cleanly and draws bands a thousand
    # times too narrow, so nothing downstream would ever complain.
    if max(edges) < 1.0:
        raise ValueError(
            f"the highest frequency in that table is {max(edges) * 1000:.3f} MHz. "
            "Frequencies must be in MHz — a file in GHz parses without error "
            "and draws bands a thousand times too narrow."
        )
    src = CatalogSource(id=sid, label=label or sid, kind="national")
    meta = {
        "kind": "national",
        "adm": (adm or "").strip().upper(),
        "n_notice_total": len(catalog),
        "freq_max_mhz": round(max(edges) * 1000.0, 3),
    }
    return _write_source(src, catalog, meta)


def _anatel_name_set() -> set[str]:
    return {norm_name(s.name) for s in load_anatel_catalog() if s.name}


def _write_sns_catalog(
    catalog: list[OccupancySystem],
    meta: dict[str, Any],
) -> dict[str, Any]:
    _write_json(SNS_CATALOG, [asdict(s) for s in catalog])
    meta = dict(meta)
    meta["n_systems"] = len(catalog)
    meta["fetched_at"] = meta.get("fetched_at") or _now_iso()
    meta["select_logic"] = SNS_SELECT_LOGIC
    _write_json(SNS_META, meta)
    return meta


def reindex_sns_mdb(mdb_path: Path, *, extra_meta: dict[str, Any] | None = None,
                    label: str = "", keep_only: str = "") -> dict[str, Any]:
    """Index one SRS and REGISTER it, instead of overwriting the previous one.

    ``keep_only='B'`` reproduces the old Brazil-only index; the default keeps
    every notice so the country stays a filter.
    """
    catalog_p, grp_p = locate_srs_parts(mdb_path)
    catalog, stats = parse_sns_catalog(
        catalog_p, grp_mdb=grp_p, keep_only=keep_only,
        # Only the compatibility path name-matches against a licensed
        # catalogue, so only it pays for loading one.
        anatel_names=_anatel_name_set() if keep_only else None,
    )
    kind = stats.pop("kind", "srs") or "srs"
    meta = {
        **stats,
        **(extra_meta or {}),
        "kind": kind,
        "mdb": str(catalog_p),
        "grp_mdb": str(grp_p),
    }
    sid = source_id_for(meta)
    src = CatalogSource(
        id=sid,
        label=label or (f"IFIC {meta['ific_no']}" if meta.get("ific_no") else sid),
        kind=kind,
    )
    _write_source(src, catalog, meta)
    # The single-slot files stay in step so an older build still reads them.
    return _write_sns_catalog(catalog, meta)


def extract_iso_from_zip(zip_path: Path, dest_dir: Path) -> Path:
    """Pull the largest ``.iso`` out of an ITU bookshop zip (``S_IFICxxxx.iso``)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        names = [
            n for n in zf.namelist()
            if n.lower().endswith(".iso") and not n.endswith("/")
        ]
        if not names:
            raise FileNotFoundError(f"No .iso inside {zip_path}")
        names.sort(key=lambda n: zf.getinfo(n).file_size, reverse=True)
        name = names[0]
        dest = dest_dir / Path(name).name
        info = zf.getinfo(name)
        if dest.exists() and dest.stat().st_size == info.file_size:
            return dest
        tmp = dest.with_suffix(dest.suffix + ".partial")
        with zf.open(name) as src, tmp.open("wb") as out:
            shutil.copyfileobj(src, out, 1024 * 1024)
        tmp.replace(dest)
    return dest


def ingest_local_srs(src: Path, *, label: str = "") -> dict[str, Any]:
    """Index a complete SRS.mdb or a folder / split-part of the BR IFIC SRS."""
    src = Path(src).expanduser()
    if not src.exists():
        raise FileNotFoundError(src)
    catalog_p, grp_p = locate_srs_parts(src)
    return reindex_sns_mdb(catalog_p, label=label, extra_meta={
        "kind": "srs",
        "ific_no": ific_no_from_name(catalog_p.name) or ific_no_from_name(src.name),
        "ific_date": "",
        "source_path": str(src),
        "bytes": catalog_p.stat().st_size + (
            grp_p.stat().st_size if grp_p.resolve() != catalog_p.resolve() else 0
        ),
    })


def ingest_local_iso(src: Path, *, label: str = "") -> dict[str, Any]:
    """Extract SRS from a BR IFIC ISO, bookshop zip, or ``srsNNNN.zip``."""
    src = Path(src).expanduser()
    if not src.exists():
        raise FileNotFoundError(src)
    suffix = src.suffix.lower()
    if suffix in {".mdb", ".accdb"} or src.is_dir():
        return ingest_local_srs(src, label=label)
    SNS_DIR.mkdir(parents=True, exist_ok=True)
    dest_dir = SNS_DIR / "srs_local"
    dest_dir.mkdir(parents=True, exist_ok=True)
    extracted_iso: Path | None = None
    iso_path = src
    if suffix == ".zip":
        kind = zip_kind(src)
        if kind == "srs":
            catalog = unpack_srs_zip(src, dest_dir)
            return ingest_local_srs(catalog.parent, label=label)
        if kind != "iso":
            raise ValueError(f"No .iso or SRS .mdb inside {src.name}")
        extracted_iso = extract_iso_from_zip(src, SNS_DIR / "brific_iso")
        iso_path = extracted_iso
        suffix = ".iso"
    if suffix != ".iso":
        raise ValueError(
            f"Expected a BR IFIC .iso, bookshop .zip, srsNNNN.zip, or SRS .mdb, "
            f"got {src.name}"
        )
    try:
        catalog = extract_srs_from_iso(iso_path, dest_dir)
    finally:
        if extracted_iso is not None:
            try:
                extracted_iso.unlink()
            except OSError:
                pass
    return ingest_local_srs(catalog.parent, label=label)


def load_sns_catalog() -> list[OccupancySystem]:
    cached = _read_json(SNS_CATALOG)
    meta = sns_meta()
    # The earth-station direction fix is a pure swap on rows already written,
    # so it is applied in place rather than counted as staleness: re-indexing
    # the whole SRS costs minutes and would be triggered by merely opening the
    # page. Anything that really needs the MDB re-read still goes through the
    # staleness branch below.
    try:
        _logic = int(meta.get("select_logic") or 0)
    except (TypeError, ValueError):
        _logic = 0
    if isinstance(cached, list) and _logic == _EARTH_DIR_FIXED_AT - 1:
        _swap_earth_directions(cached)
        meta["select_logic"] = _EARTH_DIR_FIXED_AT
        _write_json(SNS_CATALOG, cached)
        _write_json(SNS_META, meta)
        return [system_from_dict(d) for d in cached]
    stale = (
        meta.get("n_notice_total") is None
        or meta.get("select_logic") != SNS_SELECT_LOGIC
    )
    if isinstance(cached, list) and not stale:
        return [system_from_dict(d) for d in cached]
    # ``Path("")`` is ``PosixPath(".")``, which always exists, so an absent or
    # empty "mdb" entry sent the loader off to re-index the CURRENT DIRECTORY as
    # an SRS database. On a fresh clone — where data/ is gitignored, so the meta
    # file does not exist at all — that raised FileNotFoundError and the page
    # died with a traceback under "1. Catalogs", before the setup card that
    # tells the user what to do could render.
    _mdb_raw = str(meta.get("mdb") or "").strip()
    mdb = Path(_mdb_raw) if _mdb_raw else None
    if mdb is not None and mdb.exists() and stale:
        reindex_sns_mdb(mdb, extra_meta={k: v for k, v in meta.items()
                                         if k not in {"n_systems"}})
        cached = _read_json(SNS_CATALOG)
        if isinstance(cached, list):
            return [system_from_dict(d) for d in cached]
    if isinstance(cached, list):
        return [system_from_dict(d) for d in cached]
    return []


def anatel_meta() -> dict[str, Any]:
    return _read_json(ANATEL_META) or {}


def sns_meta() -> dict[str, Any]:
    return _read_json(SNS_META) or {}


def load_filings(source_ids: "list[str] | None" = None) -> list[OccupancySystem]:
    """Systems from the registered filing catalogues.

    ``source_ids=None`` means every enabled one. Falls back to the legacy
    single-slot catalogue when nothing is registered yet, so a cache written by
    an older build still shows up.
    """
    srcs = [s for s in read_sources() if s.enabled]
    if source_ids is not None:
        wanted = set(source_ids)
        srcs = [s for s in srcs if s.id in wanted]
    if not srcs:
        return load_sns_catalog() if source_ids is None else []
    out: list[OccupancySystem] = []
    for src in srcs:
        out.extend(load_source_catalog(src.id))
    return out


def combined_catalog(*, sources: tuple[str, ...] = ("anatel", "sns")) -> list[OccupancySystem]:
    out: list[OccupancySystem] = []
    if "anatel" in sources:
        out.extend(load_anatel_catalog())
    if "sns" in sources:
        out.extend(load_sns_catalog())
    return out


def common_intervals(
    systems: list[OccupancySystem],
    direction: str,
) -> list[tuple[float, float]]:
    ivs = [s.intervals(direction) for s in systems]
    ivs = [x for x in ivs if x]
    if not ivs:
        return []
    acc = ivs[0]
    for nxt in ivs[1:]:
        acc = intersect_sets(acc, nxt)
    return acc


def union_intervals(
    systems: list[OccupancySystem],
    direction: str,
) -> list[tuple[float, float]]:
    acc: list[tuple[float, float]] = []
    for s in systems:
        acc.extend(s.intervals(direction))
    return merge_intervals(acc)
