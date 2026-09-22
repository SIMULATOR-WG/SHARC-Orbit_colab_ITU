"""ES×GSO grid honours MIN_DURATION per earth-station latitude."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "streamlit_app"))
sys.path.insert(0, str(REPO))

from lib.job_runners.s1588_worker import (  # noqa: E402
    _apply_track_duration_mode,
    _raise_if_joint_track_duration,
    _windows_for_latitude,
)
from src.s1588_studies.geometry import GeometryPoint  # noqa: E402
from src.s1588_studies import runner  # noqa: E402


def _sat(a_km: float = 7078.0) -> SimpleNamespace:
    return SimpleNamespace(a=a_km)


def _cfg(bands: list | None = None, ring_cap_mb: float = 8192.0) -> dict:
    return {
        "non_gso": {"min_duration_by_lat": list(bands or [])},
        "simulation": {"track_duration_max_ring_mb": ring_cap_mb},
    }


def test_no_min_duration_stays_classic() -> None:
    windows, info = _windows_for_latitude(_cfg(), 10.0, 1.0, 100, [_sat()])
    assert windows is None and info is None


def test_window_opens_at_declaring_latitude() -> None:
    cfg = _cfg([(-90.0, 90.0, 10.0)])
    windows, info = _windows_for_latitude(cfg, 12.0, 1.0, 100, [_sat()])
    assert windows is not None and info is not None
    assert info["active"] is True
    assert info["n_sw"] == 10
    assert info["min_duration_s"] == pytest.approx(10.0)


def test_zero_latitude_degenerates_to_classic() -> None:
    cfg = _cfg([(-20.0, 20.0, 10.0), (20.0, 90.0, 0.0)])
    windows, info = _windows_for_latitude(cfg, 40.0, 1.0, 100, [_sat()])
    assert windows is None
    assert info["active"] is False
    assert info["reason"] == "min_duration_zero_at_es_latitude"


def test_window_shorter_than_fine_step_degenerates() -> None:
    cfg = _cfg([(-90.0, 90.0, 0.4)])
    windows, info = _windows_for_latitude(cfg, 0.0, 1.0, 50, [_sat()])
    assert windows is None
    assert info["reason"] == "window_shorter_than_t_fine"
    assert info["n_sw"] == 1


def test_ring_buffer_cap_fails_loudly() -> None:
    cfg = _cfg([(-90.0, 90.0, 10.0)], ring_cap_mb=1e-9)
    with pytest.raises(ValueError, match="too large to buffer"):
        _windows_for_latitude(cfg, 0.0, 1.0, 100, [_sat()])


def test_force_and_off_overrides() -> None:
    cfg = _cfg([(-90.0, 90.0, 2400.0)])
    _apply_track_duration_mode(cfg, {"track_duration_mode": "off"})
    assert cfg["non_gso"]["min_duration_by_lat"] == []
    assert cfg["non_gso"]["_track_duration_override"]["mode"] == "off"

    _apply_track_duration_mode(
        cfg, {"track_duration_mode": "force", "min_duration_s": 30},
    )
    assert cfg["non_gso"]["min_duration_by_lat"] == [(-90.0, 90.0, 30.0)]
    assert cfg["non_gso"]["_track_duration_override"]["mode"] == "force"


def test_force_requires_a_duration() -> None:
    with pytest.raises(ValueError, match="numeric min_duration_s"):
        _apply_track_duration_mode({"non_gso": {}}, {"track_duration_mode": "force"})


def test_method_3_still_refuses_track_duration() -> None:
    with pytest.raises(NotImplementedError, match="method 3"):
        _raise_if_joint_track_duration([
            {"non_gso": {"min_duration_by_lat": [(-90.0, 90.0, 10.0)]}},
        ])
    _raise_if_joint_track_duration([{"non_gso": {"min_duration_by_lat": []}}])


def test_fixed_geometry_dispatches_the_windowed_engine(monkeypatch) -> None:
    seen: dict = {}

    def _windowed(**kwargs):
        seen["windowed"] = kwargs["windows"]
        seen["caps"] = kwargs["max_co_freq_by_lat"]
        seen["alpha0"] = kwargs["alpha0_deg"]
        return "windowed"

    def _classic(**kwargs):
        seen["classic"] = True
        return "classic"

    monkeypatch.setattr(runner, "run_epfd_simulation_windowed", _windowed)
    monkeypatch.setattr(runner, "run_epfd_simulation", _classic)
    marker = object()
    out = runner.run_epfd_at_geometry(
        constellation=[],
        geometry=GeometryPoint(es_lat_deg=1.0, es_lon_deg=2.0, gso_lon_deg=3.0),
        pfd_mask=None,
        es_antenna=None,
        num_time_steps=10,
        time_step_s=1.0,
        alpha0_deg=4.0,
        max_co_freq_by_lat=[(-90.0, 90.0, 3)],
        windows=marker,
    )
    assert out == "windowed"
    assert seen["windowed"] is marker
    assert seen["alpha0"] == 4.0
    assert seen["caps"] == [(-90.0, 90.0, 3)]
    assert "classic" not in seen

    out = runner.run_epfd_at_geometry(
        constellation=[],
        geometry=GeometryPoint(es_lat_deg=1.0, es_lon_deg=2.0, gso_lon_deg=3.0),
        pfd_mask=None,
        es_antenna=None,
        num_time_steps=10,
        time_step_s=1.0,
    )
    assert out == "classic"
