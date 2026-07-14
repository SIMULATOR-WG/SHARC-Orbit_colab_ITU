"""mdb_writer.py — write REAL Access .mdb pairs (SRS + Mask) for manual systems.

The only cross-platform way to WRITE JET4 ``.mdb`` files (Linux **and**
Windows) is the Java Jackcess library — the Python stack (access_parser,
mdbtools) is read-only and the Microsoft ACE driver is Windows-only. This
module keeps all logic in Python and shells out to a ~90-line generic Java
utility (``tools/jackcess/MdbWriter.java``, single-file source launch — a
stock JRE with the ``jdk.compiler`` module suffices, no ``javac``).

Output pair (readable by ``read_srs_mdb`` / ``read_pfd_mask_xml_from_mdb``
and by the ITU tooling):

* ``<base>_SRS.mdb``  — ``non_geo`` + ``orbit`` + ``phase`` + ``mask_info``
* ``<base>_Mask.mdb`` — ``mask_info`` + ``masks`` (mask XML zipped into an
  OLE blob, the ITU layout)
"""
from __future__ import annotations

import io
import math
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from .constants import MU_KM3_S2, RE_KM

JACKCESS_DIR = Path(__file__).resolve().parents[1] / "tools" / "jackcess"
_WRITER = JACKCESS_DIR / "MdbWriter.java"


def availability() -> tuple[bool, str]:
    """(ok, reason) — Java + jars + writer present?"""
    java = shutil.which("java")
    if not java:
        return False, "java (JRE) not found on PATH"
    if not _WRITER.exists():
        return False, f"{_WRITER} missing"
    if not list(JACKCESS_DIR.glob("jackcess-*.jar")):
        return False, f"jackcess jar missing in {JACKCESS_DIR}"
    try:
        mods = subprocess.run([java, "--list-modules"], capture_output=True,
                              text=True, timeout=30).stdout
    except Exception as exc:  # noqa: BLE001
        return False, f"java probe failed: {exc}"
    if "jdk.compiler" not in mods:
        return False, "JRE lacks the jdk.compiler module (install a full JDK)"
    return True, ""


def is_available() -> bool:
    return availability()[0]


def _run_writer(spec_dir: Path, out_mdb: Path) -> None:
    cp = str(JACKCESS_DIR / "*")
    res = subprocess.run(
        ["java", "-cp", cp, str(_WRITER), str(spec_dir), str(out_mdb)],
        capture_output=True, text=True, timeout=300,
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"MdbWriter failed (rc={res.returncode}): "
            f"{res.stderr.strip() or res.stdout.strip()}"
        )


def _tsv(path: Path, header: list[str], rows: list[list]) -> None:
    def cell(v) -> str:
        if v is None:
            return ""
        if isinstance(v, float):
            return f"{v:.10g}"
        return str(v)
    lines = ["\t".join(header)]
    lines += ["\t".join(cell(v) for v in row) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _period_dhm(a_km: float) -> tuple[int, int, float]:
    """Orbital period (from a, Kepler) split into SRS prd_ddd/hh/mm fields.

    Minutes carry the FRACTION (the reader parses them as float), so the
    period — and therefore the semi-major axis recovered from it — round-trips
    exactly instead of truncating to whole minutes.
    """
    T = 2.0 * math.pi * math.sqrt(float(a_km) ** 3 / MU_KM3_S2)
    d, rem = divmod(T, 86400.0)
    h, rem = divmod(rem, 3600.0)
    m = rem / 60.0
    return int(d), int(h), m


def write_manual_pair(
    manual: dict,
    out_dir: str | Path,
    *,
    base_name: str,
    mask_xml: bytes,
    ntc_id: str = "0",
    mask_id: int = 1,
    mask_freq_min_ghz: float | None = None,
    mask_freq_max_ghz: float | None = None,
) -> tuple[Path, Path]:
    """Write ``<base>_SRS.mdb`` + ``<base>_Mask.mdb`` from a manual system.

    ``manual`` follows the Manual-System schema (``label`` + ``non_gso`` with
    ``planes:``). Returns the two paths.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ng = dict(manual.get("non_gso") or {})
    label = str(manual.get("label") or base_name)
    planes = list(ng.get("planes") or ng.get("_planes") or [])
    if not planes:
        raise ValueError("manual system has no planes")

    alpha0 = float(ng.get("alpha0_deg") or 0.0)
    nbr_sat_td = sum(int(p.get("sats_per_plane", 0) or 0) for p in planes)

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)

        # ── SRS spec ────────────────────────────────────────────────────────
        srs_spec = tdp / "srs"
        srs_spec.mkdir()
        _tsv(srs_spec / "non_geo.tsv",
             ["ntc_id:long", "sat_name:text", "ref_body:text",
              "nbr_plane:long", "nbr_sat_td:long", "f_x_zone:text",
              "x_zone:double", "f_constell:text", "multi_config_type:text"],
             [[int(ntc_id), label[:60], "T", len(planes), nbr_sat_td,
               "Y" if alpha0 > 0 else "N", alpha0, "Y", "S"]])

        orbit_rows, phase_rows = [], []
        for k, pl in enumerate(planes, start=1):
            a = pl.get("semi_major_axis_km") or ng.get("semi_major_axis_km")
            e = float(pl.get("eccentricity",
                             ng.get("eccentricity", 0.0)) or 0.0)
            if not a and pl.get("apogee_km") is not None \
                    and pl.get("perigee_km") is not None:
                ha, hp = float(pl["apogee_km"]), float(pl["perigee_km"])
                a = RE_KM + (ha + hp) / 2.0
                e = (ha - hp) / (2.0 * a)
            if not a:
                raise ValueError(f"plane {k}: no orbit size")
            a = float(a)
            apog = a * (1.0 + e) - RE_KM
            perig = a * (1.0 - e) - RE_KM
            d, h, m = _period_dhm(a)
            raan = float(pl.get("raan_deg", 0.0) or 0.0)
            orb_id = int(pl.get("orb_id", k) or k)
            orbit_rows.append([
                orb_id, int(ntc_id),
                int(pl.get("sats_per_plane", 1) or 1),
                raan,
                float(pl.get("inclination_deg",
                             ng.get("inclination_deg", 0.0)) or 0.0),
                d, h, m,
                apog, 0, perig, 0,
                float(pl.get("perigee_arg_deg", 0.0) or 0.0),
                float(ng.get("min_operating_height_km") or 0.0), 0,
                "N", "N", 0.0,
                raan,          # long_asc = RAAN → manual epoch GMST0 = 0
                0.0, "N",
            ])
            for s, ph in enumerate(pl.get("phase_angles_deg") or [], start=1):
                if ph is None:
                    continue
                phase_rows.append([int(ntc_id), orb_id, s, float(ph), 0, 0])

        _tsv(srs_spec / "orbit.tsv",
             ["orb_id:long", "ntc_id:long", "nbr_sat_pl:long",
              "right_asc:double", "inclin_ang:double",
              "prd_ddd:long", "prd_hh:long", "prd_mm:double",
              "apog:double", "apog_exp:long", "perig:double", "perig_exp:long",
              "perig_arg:double", "op_ht:double", "op_ht_exp:long",
              "f_stn_keep:text", "f_precess:text", "precession:double",
              "long_asc:double", "keep_rnge:double", "f_sunsynch:text"],
             orbit_rows)
        _tsv(srs_spec / "phase.tsv",
             ["ntc_id:long", "orb_id:long", "orb_sat_id:long",
              "phase_ang:double", "d_ref:long", "t_ref:long"],
             phase_rows)

        fmin = mask_freq_min_ghz if mask_freq_min_ghz is not None else 0.0
        fmax = mask_freq_max_ghz if mask_freq_max_ghz is not None else 0.0
        mask_info_hdr = ["ntc_id:long", "mask_id:long", "f_mask:text",
                         "f_mask_type:text", "freq_min:double",
                         "freq_max:double"]
        mask_info_row = [[int(ntc_id), int(mask_id), "P", "A", fmin, fmax]]
        _tsv(srs_spec / "mask_info.tsv", mask_info_hdr, mask_info_row)

        # Optional tables probed by the load path (mask precedence, ε₀,
        # MAX_CO_FREQ, emitter filters). Created EMPTY so the guarded readers
        # find them and stay silent instead of logging read errors.
        _tsv(srs_spec / "grp.tsv",
             ["ntc_id:long", "grp_id:long", "emi_rcp:text", "freq_min:double",
              "freq_max:double", "elev_min:double"], [])
        _tsv(srs_spec / "mask_lnk1.tsv",
             ["ntc_id:long", "grp_id:long", "orb_id:long", "sat_orb_id:long",
              "mask_id:long", "seq_no:long"], [])
        _tsv(srs_spec / "sat_oper.tsv",
             ["ntc_id:long", "lat_fr:double", "lat_to:double",
              "nbr_op_sat:long"], [])

        # ── Mask spec (masks.mask = ZIP(xml) OLE blob, ITU layout) ─────────
        mask_spec = tdp / "mask"
        mask_spec.mkdir()
        zbuf = io.BytesIO()
        with zipfile.ZipFile(zbuf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("mask.xml", mask_xml)
        blob_path = tdp / "mask_blob.zip"
        blob_path.write_bytes(zbuf.getvalue())
        _tsv(mask_spec / "mask_info.tsv", mask_info_hdr, mask_info_row)
        _tsv(mask_spec / "masks.tsv",
             ["ntc_id:long", "mask_id:long", "seq_no:long", "mask:blobfile"],
             [[int(ntc_id), int(mask_id), 1, str(blob_path)]])

        srs_out = out_dir / f"{base_name}_SRS.mdb"
        mask_out = out_dir / f"{base_name}_Mask.mdb"
        _run_writer(srs_spec, srs_out)
        _run_writer(mask_spec, mask_out)
    return srs_out, mask_out
