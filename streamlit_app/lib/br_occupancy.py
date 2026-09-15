"""Brazil band-occupancy catalog from Anatel open data + ITU Space IFIC SNS.

Downloads (stdlib urllib only, user-triggered Refresh):

* Anatel ``satelites.zip`` — licensed stations / sub-bands in Brazil.
* BR IFIC online ISO / SRS (subscription) — complete ``SRS.mdb``.
* Public weekly ``ificXXXX.mdb`` from the ITU WIC year page (that week's
  publications only).

Parsed catalogs are cached under ``streamlit_app/data/br_occupancy/``.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen

from . import DATA_ROOT, REPO_ROOT

ITU_WIC_BASE = "https://www.itu.int/sns/wic/"
ITU_WIC_YEAR_URL = "https://www.itu.int/sns/wic/demowic26.html"
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
ANATEL_ZIP_URL = (
    "https://www.anatel.gov.br/dadosabertos/paineis_de_dados/"
    "espectro_e_orbita/satelites.zip"
)
ANATEL_SUBFAIXAS = "stel_satelites_subfaixas.csv"

CACHE_DIR = DATA_ROOT / "br_occupancy"
ANATEL_DIR = CACHE_DIR / "anatel"
SNS_DIR = CACHE_DIR / "sns"
ANATEL_META = CACHE_DIR / "anatel_meta.json"
SNS_META = CACHE_DIR / "sns_meta.json"
ANATEL_CATALOG = CACHE_DIR / "anatel_catalog.json"
SNS_CATALOG = CACHE_DIR / "sns_catalog.json"

_UA = "SHARC-Orbit/1.0 (Brazil occupancy catalog)"
_ADM_BRAZIL = "B"
# ITU Preface special geographical areas that include Brazil.
# XAA = worldwide; XR2 = ITU Region 2 (Americas). Not XR1 / XR3.
_BRAZIL_IN_SERVICE = frozenset({"B", "XR2", "XAA"})
# Bump when Brazil-selection rules change so a cached catalog is rebuilt.
SNS_SELECT_LOGIC = 2
_IFIC_NO_RE_NAME = re.compile(
    r"(?:ific|br[\s._-]*ific)[\s._-]*(\d{4})", re.I
)


@dataclass
class OccupancySystem:
    """One selectable row: Anatel station or SNS notice."""

    id: str
    source: str                    # "anatel" | "sns"
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

    def label(self) -> str:
        bits = [self.name]
        if self.source == "sns" and self.ntc_id:
            bits.append(f"ntc {self.ntc_id}")
        elif self.source == "anatel":
            bits.append("Anatel")
        if self.source == "sns" and self.adm:
            bits.append(self.adm)
        if self.orbit:
            bits.append(self.orbit)
        return " · ".join(bits)


# Letter bands live in their own module so the strip charts can name a band
# without importing the catalogue machinery. Re-exported here because the
# Brazil occupancy page and its tests reach for them through ``br``.
from .freq_bands import (  # noqa: F401
    LETTER_BANDS,
    LETTER_BAND_NAMES,
    frequency_presets,
    intervals_touch_range,
    letter_bands_for,
)


def merge_intervals(ivs: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for lo, hi in sorted(ivs):
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def intersect_sets(
    a: list[tuple[float, float]],
    b: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    res = []
    for la, ha in a:
        for lb, hb in b:
            lo, hi = max(la, lb), min(ha, hb)
            if lo <= hi:
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
        downlink_ghz=[list(x) for x in (d.get("downlink_ghz") or [])],
        uplink_ghz=[list(x) for x in (d.get("uplink_ghz") or [])],
        ntc_id=str(d.get("ntc_id") or ""),
        ntc_type=str(d.get("ntc_type") or ""),
        adm=str(d.get("adm") or ""),
        extra=dict(d.get("extra") or {}),
    )


# ── HTTP ─────────────────────────────────────────────────────────────────────

def http_get_bytes(url: str, *, timeout: float = 120.0) -> bytes:
    req = Request(url, headers={"User-Agent": _UA, "Accept": "*/*"})
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310 — user-triggered catalog refresh
        return resp.read()


def http_download(url: str, dest: Path, *, timeout: float = 600.0) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = Request(url, headers={"User-Agent": _UA, "Accept": "*/*"})
    n = 0
    with urlopen(req, timeout=timeout) as resp, dest.open("wb") as fh:  # noqa: S310
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            fh.write(chunk)
            n += len(chunk)
    return n


# ── ITU WIC index ────────────────────────────────────────────────────────────

class _IficIndexParser(HTMLParser):
    """Collect Space IFIC zip links and nearby publication-date text."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []  # (href, inner_text)
        self._buf: list[str] = []
        self._href: str | None = None
        self.plain: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if re.search(r"ific\d+\.zip", href, re.I):
                self._href = href
                self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            self.links.append((self._href, "".join(self._buf).strip()))
            self._href = None
            self._buf = []

    def handle_data(self, data: str) -> None:
        t = data.strip()
        if t:
            self.plain.append(t)
        if self._href is not None:
            self._buf.append(data)


_IFIC_NO_RE = re.compile(r"ific(\d+)\.zip", re.I)
_DATE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})")


def parse_ific_index(html: str, page_url: str = ITU_WIC_YEAR_URL) -> list[dict[str, str]]:
    """Parse the ITU WIC year page into ``[{ific_no, date, zip_url, label}, ...]``.

    Rows follow document order (latest first on the 2026 page).
    """
    parser = _IficIndexParser()
    parser.feed(html or "")
    dates = _DATE_RE.findall("\n".join(parser.plain))
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for i, (href, label) in enumerate(parser.links):
        m = _IFIC_NO_RE.search(href)
        if not m:
            continue
        no = m.group(1)
        if no in seen:
            continue
        seen.add(no)
        date = dates[i] if i < len(dates) else ""
        out.append({
            "ific_no": no,
            "date": date,
            "zip_url": urljoin(page_url, href),
            "label": (label or no).strip(),
        })
    return out


def latest_ific(entries: list[dict[str, str]]) -> dict[str, str] | None:
    return entries[0] if entries else None


def itu_wic_url_for_year(year: int | None = None) -> str:
    y = int(year or datetime.now(timezone.utc).year)
    return urljoin(ITU_WIC_BASE, f"demowic{y % 100:02d}.html")


def fetch_ific_index(*, year: int | None = None) -> list[dict[str, str]]:
    urls = [itu_wic_url_for_year(year), ITU_WIC_YEAR_URL]
    last_err: Exception | None = None
    seen: set[str] = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        try:
            raw = http_get_bytes(url, timeout=60.0)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            continue
        entries = parse_ific_index(raw.decode("utf-8", errors="replace"), url)
        if entries:
            return entries
    if last_err:
        raise last_err
    return []


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


def parse_anatel_subfaixas_csv(text: str) -> list[OccupancySystem]:
    header, body = _split_csv_rows(text)
    if not header:
        return []
    i_op = _col(header, "Operador")
    i_name = _col(header, "NomeEstacao_STEL_portal", "NomeEstacao")
    i_num = _col(header, "NumEstacao_STEL_portal")
    i_orbit = _col(header, "Tipo_orbita_STEL_portal", "Tipo_orbita")
    i_pos = _col(header, "PosOrbital_STEL_portal", "PosOrbital")
    i_rf = _col(header, "Banda_RF_estacao_STEL_portal", "Banda_RF")
    i_dir = _col(header, "Sentido_STEL_portal", "Sentido")
    i_lo = _col(header, "MedFrequenciaInicialMHz_STEL_portal")
    i_hi = _col(header, "MedFrequenciaFinalMHz_STEL_portal")
    i_valid = _col(header, "Validade_licença_estacao_espacial", "Validade")
    if i_name is None or i_lo is None or i_hi is None:
        raise ValueError("stel_satelites_subfaixas.csv is missing station/frequency columns")

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
        sid = f"anatel:{norm_name(name) or name}"
        sys = grouped.get(sid)
        if sys is None:
            sys = OccupancySystem(
                id=sid,
                source="anatel",
                name=name,
                operator=cell(i_op),
                orbit=cell(i_orbit).upper() or "GEO",
                position=cell(i_pos),
                extra={"station_id": cell(i_num), "validity": cell(i_valid)},
            )
            grouped[sid] = sys
        sentido = cell(i_dir).lower()
        if "subida" in sentido or sentido.startswith("u"):
            sys.uplink_ghz.append([iv[0], iv[1]])
        else:
            sys.downlink_ghz.append([iv[0], iv[1]])
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


def extract_anatel_zip(zip_path: Path, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest)
    # zip may nest a folder
    found = list(dest.rglob(ANATEL_SUBFAIXAS))
    if not found:
        raise FileNotFoundError(f"{ANATEL_SUBFAIXAS} not found inside {zip_path}")
    return found[0]


def bundled_anatel_csv() -> Path | None:
    p = REPO_ROOT / "docs" / "satelites" / ANATEL_SUBFAIXAS
    return p if p.exists() else None


def refresh_anatel(*, timeout: float = 120.0) -> dict[str, Any]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = CACHE_DIR / "satelites.zip"
    n = http_download(ANATEL_ZIP_URL, zip_path, timeout=timeout)
    csv_path = extract_anatel_zip(zip_path, ANATEL_DIR)
    catalog = parse_anatel_subfaixas_csv(_decode_csv_bytes(csv_path.read_bytes()))
    _write_json(ANATEL_CATALOG, [asdict(s) for s in catalog])
    meta = {
        "source_url": ANATEL_ZIP_URL,
        "fetched_at": _now_iso(),
        "bytes": n,
        "csv": str(csv_path),
        "n_systems": len(catalog),
        "n_downlink": sum(1 for s in catalog if s.downlink_ghz),
        "n_uplink": sum(1 for s in catalog if s.uplink_ghz),
    }
    _write_json(ANATEL_META, meta)
    return meta


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

def find_sns_mdb(root: Path) -> Path | None:
    mdbs = [p for p in root.rglob("*") if p.suffix.lower() in {".mdb", ".accdb"}]
    if not mdbs:
        return None

    def score(p: Path) -> tuple[int, int]:
        n = p.name.lower()
        s = 0
        if "srs" in n or "sns" in n:
            s += 20
        if "ific" in n:
            s += 10
        if "sps" in n or "plan" in n:
            s -= 5
        return (s, p.stat().st_size)

    mdbs.sort(key=score, reverse=True)
    return mdbs[0]


def extract_ific_zip(zip_path: Path, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest)
    mdb = find_sns_mdb(dest)
    if mdb is None:
        raise FileNotFoundError(f"No .mdb found inside {zip_path}")
    return mdb


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
    and ``…_part3of4.mdb`` (``grp`` frequency assignments). Weekly
    ``ificXXXX.mdb`` is a single file with both.
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


def parse_sns_brazil(
    mdb_path: Path,
    *,
    anatel_names: set[str] | None = None,
    grp_mdb: Path | None = None,
) -> tuple[list[OccupancySystem], dict[str, Any]]:
    """Index notices that operate in Brazil (any notifying administration)."""
    mdb_path = Path(mdb_path)
    grp_mdb = Path(grp_mdb) if grp_mdb is not None else mdb_path
    notice = _mdb_export(mdb_path, "notice")
    stats: dict[str, Any] = {
        "n_notice_total": len(notice),
        "n_adm_b": 0,
        "n_srv_br": 0,
        "n_es_br": 0,
        "n_name_match": 0,
        "kind": "srs",
    }
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

    es_br: set[str] = set()
    try:
        for row in _mdb_export(mdb_path, "es_ctry"):
            if _cell(row, "ctry").upper() == _ADM_BRAZIL:
                ntc = _cell(row, "ntc_id")
                if ntc:
                    es_br.add(ntc)
    except Exception:  # noqa: BLE001
        pass
    srv_br: set[str] = set()
    try:
        for row in _mdb_export(mdb_path, "srv_area"):
            if ctry_covers_brazil(
                _cell(row, "ctry"), excl=_cell(row, "f_excl_api")
            ):
                ntc = gid_to_ntc.get(_cell(row, "grp_id"), "")
                if ntc:
                    srv_br.add(ntc)
    except Exception:  # noqa: BLE001
        pass

    wanted = anatel_names or set()
    keep_reasons = select_brazil_ntcs(
        notice,
        sat_name_by_ntc,
        srv_br_ntcs=srv_br,
        es_br_ntcs=es_br,
        anatel_names=wanted,
    )
    stats["n_adm_b"] = sum(1 for r in keep_reasons.values() if "adm_b" in r)
    stats["n_srv_br"] = sum(1 for r in keep_reasons.values() if "srv_br" in r)
    stats["n_es_br"] = sum(1 for r in keep_reasons.values() if "es_br" in r)
    stats["n_name_match"] = sum(1 for r in keep_reasons.values() if "anatel_name" in r)
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
        if _cell(row, "emi_rcp").upper().startswith("R"):
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


def reindex_sns_mdb(mdb_path: Path, *, extra_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    catalog_p, grp_p = locate_srs_parts(mdb_path)
    catalog, stats = parse_sns_brazil(
        catalog_p, anatel_names=_anatel_name_set(), grp_mdb=grp_p,
    )
    stats.pop("kind", None)
    meta = {
        **stats,
        **(extra_meta or {}),
        "mdb": str(catalog_p),
        "grp_mdb": str(grp_p),
    }
    return _write_sns_catalog(catalog, meta)


def refresh_sns(*, year: int | None = None, timeout: float = 600.0) -> dict[str, Any]:
    """Download the public weekly ``ificXXXX.mdb`` (not the full SRS)."""
    entries = fetch_ific_index(year=year)
    latest = latest_ific(entries)
    if latest is None:
        raise RuntimeError("No Space IFIC zip link found on the ITU WIC page.")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = SNS_DIR / f"ific{latest['ific_no']}.zip"
    extract_dir = SNS_DIR / f"ific{latest['ific_no']}"
    if zip_path.exists():
        n = zip_path.stat().st_size
    else:
        n = http_download(latest["zip_url"], zip_path, timeout=timeout)
    mdb = find_sns_mdb(extract_dir) or extract_ific_zip(zip_path, extract_dir)
    return reindex_sns_mdb(mdb, extra_meta={
        "kind": "ific",
        "ific_no": latest["ific_no"],
        "ific_date": latest.get("date") or "",
        "zip_url": latest["zip_url"],
        "bytes": n,
    })


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


def ingest_local_srs(src: Path) -> dict[str, Any]:
    """Index a complete SRS.mdb or a folder / split-part of the BR IFIC SRS."""
    src = Path(src).expanduser()
    if not src.exists():
        raise FileNotFoundError(src)
    catalog_p, grp_p = locate_srs_parts(src)
    return reindex_sns_mdb(catalog_p, extra_meta={
        "kind": "srs",
        "ific_no": ific_no_from_name(catalog_p.name) or ific_no_from_name(src.name),
        "ific_date": "",
        "source_path": str(src),
        "bytes": catalog_p.stat().st_size + (
            grp_p.stat().st_size if grp_p.resolve() != catalog_p.resolve() else 0
        ),
    })


def ingest_local_iso(src: Path) -> dict[str, Any]:
    """Extract SRS from a BR IFIC ISO, bookshop zip, or ``srsNNNN.zip``."""
    src = Path(src).expanduser()
    if not src.exists():
        raise FileNotFoundError(src)
    suffix = src.suffix.lower()
    if suffix in {".mdb", ".accdb"} or src.is_dir():
        return ingest_local_srs(src)
    SNS_DIR.mkdir(parents=True, exist_ok=True)
    dest_dir = SNS_DIR / "srs_local"
    dest_dir.mkdir(parents=True, exist_ok=True)
    extracted_iso: Path | None = None
    iso_path = src
    if suffix == ".zip":
        kind = zip_kind(src)
        if kind == "srs":
            catalog = unpack_srs_zip(src, dest_dir)
            return ingest_local_srs(catalog.parent)
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
    return ingest_local_srs(catalog.parent)


def load_sns_catalog() -> list[OccupancySystem]:
    cached = _read_json(SNS_CATALOG)
    meta = sns_meta()
    stale = (
        meta.get("n_notice_total") is None
        or meta.get("select_logic") != SNS_SELECT_LOGIC
    )
    if isinstance(cached, list) and not stale:
        return [system_from_dict(d) for d in cached]
    mdb = Path(str(meta.get("mdb") or ""))
    if mdb.exists() and stale:
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
