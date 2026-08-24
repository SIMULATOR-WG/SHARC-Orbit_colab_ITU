# -*- coding: utf-8 -*-
"""`wcga_flat_threshold` — the flat-vs-ramp WCGA ranking threshold.

S.1503-2 (CheckCase) and -4 (WCGD_CheckCase) both say only to "calculate
EPFDThreshold from latitude of point P", and neither states whether the
Article 22 latitude notes (22.5C.4 / 22.5C.8) belong to that threshold. The
default reads them in, which gives high-latitude points up to 5.3 dB of
ranking advantage; this flag reads them out, so the two geometries can be
compared on the same filing instead of argued about.

Runs under pytest or directly: `python tests/test_wcga_flat_threshold.py`.
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.article22_tables import (  # type: ignore[import]
    build_epfd_threshold_by_lat_fn,
)

T22_1A = "Article 22, TABLE 22-1A"
T22_1D = "Article 22, TABLE 22-1D"
CURVE = [[-160.0, 0.0], [-165.0, 0.001], [-170.0, 1.0]]


def _fn(diam_cm, flat, ref=T22_1A, bw=40.0):
    return build_epfd_threshold_by_lat_fn(
        rr_reference=ref, rf_diam_cm=diam_cm,
        reference_bandwidth_khz=bw, curve=CURVE,
        flat_threshold=flat)


def _close(a, b, tol=1e-6):
    return abs(float(a) - float(b)) <= tol


def test_ramp_is_the_default_where_the_note_applies():
    f = _fn(120.0, False)
    assert f.note == "22.5C.4" and f.latitude_dependent
    assert _close(f(0.0), -160.0), "flat below 57.5 deg"
    assert _close(f(70.0), -165.3), "ramped above 63.75 deg"
    assert _close(f(-70.0), -165.3), "the ramp is on |lat|, not signed lat"


def test_flag_suppresses_the_ramp_and_says_so():
    f = _fn(120.0, True)
    assert not f.latitude_dependent
    assert f.note == "flat(22.5C.4 suppressed)", (
        f"provenance must name what was suppressed, got {f.note!r}")
    assert _close(f.baseline_db, -160.0)
    assert np.allclose(f(np.array([0.0, 50.0, 70.0, -74.75])), -160.0)


def test_flat_removes_the_high_latitude_ranking_advantage():
    """The point of the whole exercise, stated numerically."""
    ramp, flat = _fn(120.0, False), _fn(120.0, True)
    assert _close(ramp(0.0) - ramp(-74.75), 5.3), (
        "the ramp is what gives a southern point its edge")
    assert _close(flat(0.0) - flat(-74.75), 0.0, 1e-9)


def test_no_op_for_a_60_cm_victim():
    """22.5C.4 needs D > 60 cm strictly, so a 60 cm victim ALREADY ranks flat.

    This is why the flag cannot explain a 60 cm WCG: there was never a ramp
    to suppress. Any divergence there has another cause.
    """
    off, on = _fn(60.0, False), _fn(60.0, True)
    assert not off.latitude_dependent and not on.latitude_dependent
    assert _close(off.baseline_db, on.baseline_db)
    lats = np.array([0.0, -74.75])
    assert np.allclose(off(lats), on(lats))


def test_vectorised_and_scalar_agree():
    for flat in (False, True):
        f = _fn(120.0, flat)
        lats = [0.0, 57.5, 60.0, 63.75, 80.0]
        assert np.allclose(f(np.array(lats)), [f(x) for x in lats])


def test_flag_covers_both_notes():
    for ref, diam, note in ((T22_1D, 180.0, "22.5C.8"),
                            (T22_1A, 300.0, "22.5C.4")):
        assert _fn(diam, False, ref=ref).latitude_dependent, note
        f = _fn(diam, True, ref=ref)
        assert not f.latitude_dependent and note in f.note


def test_bandwidth_outside_40khz_is_untouched():
    """The notes are 40 kHz only, so there is no ramp to suppress."""
    off, on = _fn(120.0, False, bw=4000.0), _fn(120.0, True, bw=4000.0)
    assert not off.latitude_dependent and not on.latitude_dependent
    assert on.note == "flat", "do not claim a suppression that never happened"


def test_default_argument_keeps_the_ramp():
    """Callers that never heard of the flag must be unaffected."""
    f = build_epfd_threshold_by_lat_fn(
        rr_reference=T22_1A, rf_diam_cm=120.0,
        reference_bandwidth_khz=40.0, curve=CURVE)
    assert f.latitude_dependent and f.note == "22.5C.4"


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as exc:
            fails += 1
            print(f"  FAIL  {name}: {exc}")
    print(f"\n{'all passed' if not fails else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)
