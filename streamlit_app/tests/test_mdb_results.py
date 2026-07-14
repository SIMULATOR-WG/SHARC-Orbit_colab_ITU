"""Parse results MDB into CCDF curves (mdb_results)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from streamlit_app.lib import mdb_results  # noqa: E402

_MDB = REPO_ROOT / "data" / "EPFDRESULTS_3COM.MDB"
_have = _MDB.exists()


@pytest.mark.skipif(not _have, reason="results MDB unavailable")
def test_parse_real_results_mdb():
    curves = mdb_results.parse_results_mdb(_MDB.read_bytes(), source_label="X")
    assert curves, "expected at least one curve"
    c = curves[0]
    assert c["label"].startswith("X · ")
    assert len(c["epfd"]) == len(c["percent"]) > 0
    # CCDF: percentage is monotonically non-increasing as EPFD rises.
    assert c["percent"][0] >= c["percent"][-1]
    # EPFD sorted by sequence (ascending bins).
    assert c["epfd"][0] <= c["epfd"][-1]
    assert len(c["limit_percent"]) == len(c["epfd"])


def test_empty_bytes_raises():
    # Invalid bytes: the MDB export helper swallows the parse failure and
    # yields no rows, so parse_results_mdb raises its own RuntimeError
    # ("No 'cdf' table found ...").
    with pytest.raises(RuntimeError, match="No 'cdf' table"):
        mdb_results.parse_results_mdb(b"not an mdb")


def test_to_float_and_label_helpers():
    assert mdb_results._to_float("12.5") == 12.5
    assert mdb_results._to_float("  -3 ") == -3.0
    assert mdb_results._to_float("x") is None
    # freq MHz → GHz, dish, type composed into label
    lbl = mdb_results._label(
        "7", {"epfd_type": "D", "freq_used": "17800", "dish_size": "1.2"}, "src"
    )
    assert "src · ID 7" in lbl and "17.8 GHz" in lbl and "Ø1.2 m" in lbl
