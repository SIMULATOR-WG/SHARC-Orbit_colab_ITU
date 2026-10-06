# -*- coding: utf-8 -*-
"""Resolve the filings the campaign scripts need, on any machine.

Upload ids are generated per installation, so pinning a script to a system id
(``1c5dc7c02695:323520263:_``) only works on the machine that registered it.
Here a filing is named by a short KEY instead, and resolved like this:

  1. Look for its SRS/Masks MDB pair in ``<FILINGS_DIR>/<KEY> - <NTC>/``
     (default ``campaign_data/shared_filings/``, override with the
     ``SHARC_FILINGS_DIR`` environment variable), then in the filing's
     ``fallback_dir`` (for filings already tracked in the repo).
  2. Register it in this installation's DB under a FIXED upload id, so the
     resulting system id is the same on every machine. Registering is
     idempotent — running it again only refreshes the paths.

So a script runs directly: copy the MDBs in once, and the first run registers
them. Nothing has to be done in the Upload page.

    python scripts/campaign_filings.py list       # what is found / missing
    python scripts/campaign_filings.py register   # register everything found

Expected layout (only the 3X filing ships with the repo):

    campaign_data/shared_filings/
        3N - 119520228/   USASAT-NGSO-3series_Config1_SRS_Ku.MDB + ..._Masks.MDB
        L5 - 101/         OneWeb_Ku_SRS_updated.mdb + OneWeb_Ku_Masks_reentered.mdb
        SS1 - 323520044/  323520044 SRS.MDB + 323520044 Masks.MDB
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from streamlit_app.lib import storage  # noqa: E402

#: The filings the campaign scripts use. ``upload_id`` is FIXED on purpose —
#: it is what makes the system id identical on every machine. Never change one
#: once results exist under it. ``srs_hint`` picks the SRS file when the folder
#: holds more than one (case-insensitive substring of the file name).
FILINGS: dict[str, dict[str, Any]] = {
    "3X": {"upload_id": "c5f11a3X0001", "ntc_id": "323520263",
           "sat_name": "USASAT-NGSO-3X", "srs_hint": "10700",
           "fallback_dir": REPO / "campaign_data" / "changes_1503"},
    "3N": {"upload_id": "c5f11a3N0002", "ntc_id": "119520228",
           "sat_name": "USASAT-NGSO-3N", "srs_hint": "srs_ku."},
    "L5": {"upload_id": "c5f11aL50003", "ntc_id": "101",
           "sat_name": "L5 (OneWeb)", "srs_hint": None},
    "SS1": {"upload_id": "c5f11aSS0004", "ntc_id": "323520044",
            "sat_name": "SAILSPACE-1", "srs_hint": None},
}


def filings_dir() -> Path:
    env = os.environ.get("SHARC_FILINGS_DIR", "").strip()
    return Path(env) if env else REPO / "campaign_data" / "shared_filings"


def system_id(key: str) -> str:
    f = FILINGS[key]
    return f"{f['upload_id']}:{f['ntc_id']}:_"


def _pick(folder: Path, f: dict[str, Any]) -> tuple[Path, Path | None] | None:
    """(srs, mask) MDB pair for filing ``f`` inside ``folder``, or None."""
    if not folder.is_dir():
        return None
    mdbs = sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() == ".mdb")
    masks = [p for p in mdbs if "mask" in p.name.lower()]
    srs = [p for p in mdbs if p not in masks]
    if f.get("srs_hint"):
        srs = [p for p in srs if f["srs_hint"] in p.name.lower()] or srs
    # A shared folder (e.g. the tracked campaign_data/changes_1503) can hold
    # other filings too: keep the files that name this NTC, when any do.
    own = [p for p in srs if f["ntc_id"] in p.name]
    srs = own or srs
    own_m = [p for p in masks if f["ntc_id"] in p.name]
    masks = own_m or masks
    if not srs:
        return None
    return srs[0], (masks[0] if masks else None)


def locate(key: str) -> tuple[Path, Path | None] | None:
    f = FILINGS[key]
    for folder in (filings_dir() / f"{key} - {f['ntc_id']}",
                   f.get("fallback_dir")):
        if folder is not None:
            pair = _pick(Path(folder), f)
            if pair:
                return pair
    return None


def register(key: str) -> str | None:
    """Register filing ``key`` from its files; return its system id."""
    f = FILINGS[key]
    pair = locate(key)
    if pair is None:
        return None
    srs, mask = pair
    storage.add_upload(label=f"{f['sat_name']} ({f['ntc_id']})",
                       srs_path=srs, mask_path=mask,
                       network_name=f["sat_name"], upload_id=f["upload_id"],
                       metadata={"campaign_filing": key})
    return storage.add_system(upload_id=f["upload_id"], ntc_id=f["ntc_id"],
                              mask_id=None, sat_name=f["sat_name"])


def resolve(key: str) -> dict[str, Any]:
    """The registered system row for filing ``key``, registering it first if
    needed. Exits with instructions when its MDB files are not found."""
    row = storage.get_system(system_id(key))
    if row is None or not Path(str(row.get("srs_path") or "")).is_file():
        if register(key) is None:
            f = FILINGS[key]
            raise SystemExit(
                f"filing {key} ({f['sat_name']}, ntc {f['ntc_id']}) not found."
                f"\n  Copy its SRS and Masks MDB into "
                f"{filings_dir() / (key + ' - ' + f['ntc_id'])}"
                f"\n  (or set SHARC_FILINGS_DIR) and run again.")
        row = storage.get_system(system_id(key))
    return row


def cmd_list(_args) -> None:
    print(f"filings folder: {filings_dir()}\n")
    for key, f in FILINGS.items():
        pair = locate(key)
        state = "found  " if pair else "MISSING"
        print(f"  [{state}] {key:4s} {f['sat_name']:22s} ntc {f['ntc_id']}")
        if pair:
            print(f"            srs  {pair[0]}")
            print(f"            mask {pair[1] or '(none)'}")
        else:
            print(f"            expected in {filings_dir()}"
                  f"{os.sep}{key} - {f['ntc_id']}")


def cmd_register(_args) -> None:
    for key in FILINGS:
        sid = register(key)
        print(f"  {key:4s} -> {sid or 'not found — skipped'}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("register")
    args = ap.parse_args()
    {"list": cmd_list, "register": cmd_register}[args.cmd](args)


if __name__ == "__main__":
    main()
