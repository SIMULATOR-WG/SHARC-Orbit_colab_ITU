"""estimator.py — workload + runtime estimates for Single-entry and Aggregate.

All estimates are heuristic — orders of magnitude based on default-host
benchmarks (one core ~ 3.5 GHz, Numba JIT warm). Real runtime depends on
the host, the engine config (dual time step), filings size and Python
overhead. Use as a sanity check, not as a contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import engine  # ensures src.* importable
from src.constants import RE_KM  # noqa: E402 — engine put src on sys.path
from src.time_step import compute_nmin_s1503  # noqa: E402
from src.article22_tables import apply_article22_limits_to_config  # noqa: E402


# ─── Baseline costs (calibrated against a 28-sat run) ───────────────────────
# Reference benchmark (Walker MEO, 28 sats, S.1503-4 default step):
#   WCGA wall-clock ≈ 14.6 s   (Phase 1)
#   EPFD↓ sim (30 steps) ≈ 13.2 s   (Phase 3, dominated by JIT warmup)
# Constants below split the cost into (a) JIT/setup overhead and (b) the
# linear term per (sat × step). The model is:
#     t_wcga  ≈ T_WCGA_SETUP + N_sat × (lat_span_deg / step_deg) × T_WCGA_ITER
#     t_sim   ≈ T_SIM_SETUP + N_sat × N_steps × T_SIM_PER_STEP
# Tunable when better benchmarks are gathered from the runs table.

T_WCGA_SETUP_S = 2.0
T_WCGA_ITER_S = 3.2e-3       # per (sat × 1° latitude scan) — derived from 14.6s/(28×162)

T_SIM_SETUP_S = 8.0          # Numba JIT + process bootstrap (cold start)
T_SIM_PER_STEP_S = 5.0e-6    # per (sat × step) — single core, warm

# Convolution cost (negligible vs sims) — flat overhead per call.
_T_CONVOLUTION_OVERHEAD_S = 0.05

# Compatibility aliases (older code paths)
_T_WCGA_PER_SAT_LAT_DEG_S = T_WCGA_ITER_S
_T_EPFD_PER_SAT_STEP_S = T_SIM_PER_STEP_S


# ─── Helpers ────────────────────────────────────────────────────────────────


def _fmt_seconds(s: float) -> str:
    if s < 0:
        s = 0
    if s < 60:
        return f"{s:.1f} s"
    if s < 3600:
        return f"{s / 60:.1f} min"
    if s < 86400:
        return f"{s / 3600:.2f} h"
    return f"{s / 86400:.2f} d"


@dataclass
class Estimate:
    label: str
    n_sims: int
    n_time_steps: int
    n_satellites_total: int
    wall_seconds: float
    notes: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "n_sims": self.n_sims,
            "n_time_steps": self.n_time_steps,
            "n_satellites_total": self.n_satellites_total,
            "wall_seconds": self.wall_seconds,
            "wall_human": _fmt_seconds(self.wall_seconds),
            "notes": self.notes,
        }


def _t_wcga(n_sat: int, step_deg: float, lat_span_deg: float = 162.0) -> float:
    """Estimated WCGA time for one filing (incl. setup)."""
    step = max(0.05, float(step_deg))
    return T_WCGA_SETUP_S + n_sat * (lat_span_deg / step) * T_WCGA_ITER_S


def _t_sim(n_sat: int, n_steps: int) -> float:
    """Estimated EPFD↓ simulation time (incl. JIT setup)."""
    return T_SIM_SETUP_S + n_sat * n_steps * T_SIM_PER_STEP_S


# ─── Public API ─────────────────────────────────────────────────────────────


_calibrate_cached = None


def calibrate_from_runs() -> dict[str, float]:
    """Refine T_SIM_PER_STEP_S using completed single-entry runs in the DB.

    Cached via ``st.cache_data(ttl=60)`` when Streamlit is available — the
    raw scan reads up to 200 runs from SQLite plus one summary.json each,
    far too expensive to repeat on every form rerun. Falls back to the
    uncached implementation outside a Streamlit context.
    """
    global _calibrate_cached
    if _calibrate_cached is None:
        try:
            import streamlit as st  # noqa: PLC0415 — optional at runtime
            _calibrate_cached = st.cache_data(ttl=60, show_spinner=False)(
                _calibrate_from_runs_impl
            )
        except Exception:  # noqa: BLE001
            _calibrate_cached = _calibrate_from_runs_impl
    return _calibrate_cached()


def _calibrate_from_runs_impl() -> dict[str, float]:
    """Uncached scan: reads `runs` table for successful single-entry runs,
    parses their `params_json` (N_steps) and disk artifact (`summary.json`
    → n_satellites), and uses (updated_at − created_at) as wall time.
    Returns the new inferred per-(sat × step) constant; mutates module
    globals in-place. Skips silently if no usable data.
    """
    global T_SIM_PER_STEP_S, T_SIM_SETUP_S
    try:
        from datetime import datetime
        import json
        from . import storage, RUNS_DIR
        samples = []
        for r in storage.list_runs(limit=200):
            if r["status"] != "success" or r["kind"] not in ("single", "s1503"):
                continue
            try:
                params = json.loads(r.get("params_json") or "{}")
                n_steps = int(params.get("num_time_steps") or 0)
                t0 = datetime.fromisoformat(r["created_at"])
                t1 = datetime.fromisoformat(r["updated_at"])
                wall = (t1 - t0).total_seconds()
                if n_steps <= 0 or wall <= 0:
                    continue
                # Read n_sat from summary
                summ = RUNS_DIR / r["id"] / "summary.json"
                if not summ.exists():
                    continue
                meta = json.loads(summ.read_text(encoding="utf-8"))
                n_sat = int(meta.get("n_satellites") or 0)
                if n_sat <= 0:
                    continue
                samples.append((n_sat, n_steps, wall))
            except Exception:  # noqa: BLE001
                continue
        if len(samples) < 2:
            return {"calibrated": False, "n_samples": len(samples)}
        # Solve t = a + b × (N_sat × N_steps) via least squares
        import numpy as np
        x = np.array([ns * st for (ns, st, _w) in samples], dtype=float)
        y = np.array([w for (_a, _b, w) in samples], dtype=float)
        A = np.vstack([np.ones_like(x), x]).T
        a, b = np.linalg.lstsq(A, y, rcond=None)[0]
        if a > 0 and b > 0:
            T_SIM_SETUP_S = float(a)
            T_SIM_PER_STEP_S = float(b)
        return {
            "calibrated": True, "n_samples": len(samples),
            "T_SIM_SETUP_S": float(T_SIM_SETUP_S),
            "T_SIM_PER_STEP_S": float(T_SIM_PER_STEP_S),
        }
    except Exception as exc:  # noqa: BLE001
        return {"calibrated": False, "error": f"{type(exc).__name__}: {exc}"}


def estimate_single_entry(*, n_sat: int, n_time_steps: int,
                            s1503_step_deg: float = 1.0) -> Estimate:
    twcga = _t_wcga(n_sat, s1503_step_deg)
    tsim = _t_sim(n_sat, n_time_steps)
    wall = twcga + tsim
    return Estimate(
        label="Single-entry",
        n_sims=1,
        n_time_steps=n_time_steps,
        n_satellites_total=n_sat,
        wall_seconds=wall,
        notes=[
            f"WCGA cost ≈ {_fmt_seconds(twcga)}",
            f"EPFD sim cost ≈ {_fmt_seconds(tsim)}",
            "Numba-warm; first call adds ~5–10 s JIT.",
        ],
    )


def _fmt_step(s: float) -> str:
    """Human time step: ms below 1 s, else seconds."""
    if s is None:
        return "—"
    if s < 1.0:
        return f"{s * 1000.0:.0f} ms"
    return f"{s:.3f} s"


@dataclass
class TimeStepPreview:
    """S.1503-4 §D4 time-step / step-count preview for the workload panel.

    All fields are the values the engine will actually use for the run (with
    any manual Number-of-steps / fine / coarse overrides already applied to the
    ``effective_*`` fields). ``ok=False`` means the orbit/antenna could not be
    resolved (``error`` says why) and the panel should fall back to the rough
    heuristic.
    """
    ok: bool
    nsteps: int                  # effective number of time steps
    fine_step_s: float           # effective fine Δt (S.1503-4 §D4.2)
    coarse_step_s: float         # effective coarse Δt (§D4.7 = fine × Ncoarse)
    ncoarse: int                 # Ncoarse (N'coarse when the 1e8 recalc fired)
    nhit_requested: int          # Nhit asked for (16 by default)
    nhit_eff: float              # Nhit actually used (N'hit after §D4.1 1e8 recalc)
    nmin: int                    # statistical floor Nmin (D4.6, Table 13)
    duration_s: float            # nsteps × fine Δt
    repeating: bool
    auto_nsteps: int             # S.1503 auto count before manual N override
    reduced_1e8: bool            # True if §D4.1 1e8 recalc reduced the resolution
    nmin_floor_hit: bool         # True if Nmin extended the run (non-repeating)
    overridden: bool             # True if a manual N / Δt override is in effect
    theta_3db_deg: float
    notes: list[str]
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "nsteps": self.nsteps,
            "fine_step_s": self.fine_step_s,
            "coarse_step_s": self.coarse_step_s,
            "fine_step_human": _fmt_step(self.fine_step_s),
            "coarse_step_human": _fmt_step(self.coarse_step_s),
            "ncoarse": self.ncoarse,
            "nhit_requested": self.nhit_requested,
            "nhit_eff": self.nhit_eff,
            "nmin": self.nmin,
            "duration_s": self.duration_s,
            "duration_human": _fmt_seconds(self.duration_s),
            "repeating": self.repeating,
            "auto_nsteps": self.auto_nsteps,
            "reduced_1e8": self.reduced_1e8,
            "nmin_floor_hit": self.nmin_floor_hit,
            "overridden": self.overridden,
            "theta_3db_deg": self.theta_3db_deg,
            "notes": self.notes,
            "error": self.error,
        }


def _min_exceedance_pct_from_curve(curve: Any) -> float | None:
    """Smallest strictly-positive exceedance % in an Art. 22 CCDF curve.

    Mirrors the engine (``main`` / ``compute_nmin_s1503``): the curve is stored
    in the EXCEEDANCE convention, so the rarest event drives Nmin.
    """
    try:
        cands = [
            float(p[1]) for p in curve
            if isinstance(p, (list, tuple)) and len(p) >= 2 and float(p[1]) > 0.0
        ]
        return min(cands) if cands else None
    except Exception:  # noqa: BLE001
        return None


def preview_time_step(
    *,
    srs_path: str | None,
    ntc_id: str | None,
    diameter_m: float,
    service: str = "FSS",
    efficiency: float = 0.65,
    freq_min_ghz: float | None = None,
    freq_max_ghz: float | None = None,
    simulation_frequency_ghz: float | None = None,
    reference_bandwidth_khz: float = 40.0,
    repeating: bool | None = None,
    repeat_days: float = 1.0,
    nhit: int = 16,
    phi_coarse_deg: float = 1.5,
    artificial_precession: bool = False,
    min_elevation_deg: float = 10.0,
    num_steps_override: int | None = None,
    fine_step_override: float | None = None,
    coarse_step_override: float | None = None,
) -> TimeStepPreview:
    """Compute the S.1503-4 §D4 time step(s) and step count for a real system.

    Reads the constellation from the SRS MDB and replicates the engine's own
    dimensioning (``src.time_step.compute_time_step_and_count``) so the workload
    panel shows the **same** Δt / NSTEPS the run will use — including the §D4.1
    1e8 resolution recalculation and the §D4.6 Nmin floor — before launching.

    The frequency run **and** the Article 22 limit curve (which fixes Nmin) are
    resolved with the engine's own ``apply_article22_limits_to_config`` from the
    filing PFD band + service + ES diameter (and a pinned scenario frequency,
    when one was selected), so Nmin matches what the run actually enforces — not
    just the explicit-scenario case.
    """
    from . import srs_inspect  # noqa: PLC0415 — local to avoid import cycles

    def _fail(msg: str) -> TimeStepPreview:
        return TimeStepPreview(
            ok=False, nsteps=0, fine_step_s=0.0, coarse_step_s=0.0, ncoarse=1,
            nhit_requested=int(nhit), nhit_eff=float(nhit), nmin=0, duration_s=0.0,
            repeating=bool(repeating or False), auto_nsteps=0, reduced_1e8=False,
            nmin_floor_hit=False, overridden=False, theta_3db_deg=0.0,
            notes=[], error=msg,
        )

    if not srs_path:
        return _fail("no SRS path")

    # Resolve the frequency run + Article 22 limit curve exactly like the engine
    # (s1503_worker → apply_article22_limits_to_config). This fixes both θ3dB
    # (fine step) and the Nmin floor, even when no scenario was picked.
    a22_cfg: dict = {
        "gso_es": {"service": service, "antenna_diameter_m": float(diameter_m)},
        "pfd_mask": {},
        "article22_limits": {"reference_bandwidth_khz": float(reference_bandwidth_khz)},
    }
    if freq_min_ghz is not None and freq_max_ghz is not None:
        a22_cfg["pfd_mask"]["freq_min_ghz"] = float(freq_min_ghz)
        a22_cfg["pfd_mask"]["freq_max_ghz"] = float(freq_max_ghz)
    if simulation_frequency_ghz is not None:
        a22_cfg["pfd_mask"]["simulation_frequency_ghz"] = float(simulation_frequency_ghz)
    rr_reference = None
    limits = None
    freq_run = None
    try:
        apply_article22_limits_to_config(a22_cfg)
        freq_run = a22_cfg.get("non_gso", {}).get("frequency_ghz")
        limits = a22_cfg.get("article22_limits", {}).get("limits")
        rr_reference = a22_cfg.get("article22_limits", {}).get("rr_reference")
    except Exception:  # noqa: BLE001 — fall back to the raw inputs below
        pass

    frequency_ghz = freq_run or simulation_frequency_ghz
    if frequency_ghz is None and freq_min_ghz is not None and freq_max_ghz is not None:
        frequency_ghz = (float(freq_min_ghz) + float(freq_max_ghz)) / 2.0
    if not frequency_ghz or frequency_ghz <= 0.0:
        return _fail("no simulation frequency (pick an Article 22 scenario or set it)")

    # Nmin floor comes from the resolved limit curve (None ⇒ engine default).
    min_exceedance_pct = _min_exceedance_pct_from_curve(limits) if limits else None

    op = srs_inspect.orbital_params(srs_path, ntc_id)
    planes = op.get("planes") or []
    if not planes:
        return _fail("orbital parameters unavailable in the MDB")

    p0 = planes[0]
    # a_km: prefer operational altitude when declared (>100 km), like build_config.
    op_height0 = float(p0.get("op_height_km", 0.0) or 0.0)
    if op_height0 > 100.0:
        a_km = op_height0 + RE_KM
    else:
        a_km = float(p0["semi_major_axis_km"])
    e = float(p0["eccentricity"])
    i_deg = float(p0["inclin_deg"])
    # Match srs_to_constellation_config exactly: prefer the header plane count
    # when present (> 0), else the number of plane rows.
    num_planes = int(op.get("nbr_planes") or 0) or len(planes)
    sats_per_plane = int(p0["nbr_sat_pl"])
    op_heights = [float(p.get("op_height_km", 0.0) or 0.0) for p in planes]
    op_heights = [h for h in op_heights if h > 0.0]
    min_operating_height_km = min(op_heights) if op_heights else 0.0

    # Repeating ground track: explicit flag wins; else auto-detect from the SRS
    # repeat period — but only when the track PHYSICALLY closes (near-integer
    # orbits per repeat), mirroring the engine
    # (src.time_step.repeat_track_is_physical). A declared repeat that does not
    # close (e.g. a MEO at ~2.027 orbits/day) falls through to non-repeating
    # §D4.6.2 — exactly what the run now does — so the estimate matches.
    if repeating is None:
        from src.time_step import repeat_track_is_physical  # noqa: PLC0415
        rpt_s = float(p0.get("rpt_period_s", 0.0) or 0.0)
        _rep_ok, _ = repeat_track_is_physical(rpt_s, a_km)
        if rpt_s >= 3600.0 and _rep_ok:
            repeating = True
            repeat_days = rpt_s / 86400.0
        else:
            repeating = False

    # Earth-station 3 dB beamwidth (drives the §D4.2 fine step).
    try:
        from src.antenna import create_gso_es_antenna  # type: ignore[import]
        es_ant = create_gso_es_antenna(
            float(diameter_m), float(frequency_ghz), float(efficiency), service=service,
        )
        theta_3db_deg = float(es_ant.theta_3db_deg)
    except Exception as exc:  # noqa: BLE001
        return _fail(f"antenna: {type(exc).__name__}: {exc}")

    try:
        from src.time_step import compute_time_step_and_count  # type: ignore[import]
        res = compute_time_step_and_count(
            a_km=a_km, e=e, i_deg=i_deg,
            num_planes=num_planes, sats_per_plane=sats_per_plane,
            min_elevation_deg=float(min_elevation_deg),
            min_operating_height_km=min_operating_height_km,
            artificial_precession=bool(artificial_precession),
            repeating_ground_track=bool(repeating),
            repeat_period_days=float(repeat_days),
            theta_3db_deg=theta_3db_deg,
            nhit=int(nhit),
            min_exceedance_pct=min_exceedance_pct,
            ntracks=int(nhit),
            phi_coarse_deg=float(phi_coarse_deg),
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(f"time step: {type(exc).__name__}: {exc}")

    nmin = compute_nmin_s1503(min_exceedance_pct)
    auto_nsteps = int(res.nsteps)
    auto_fine = float(res.tstep_s)
    ncoarse = int(res.ncoarse)
    reduced_1e8 = abs(float(res.nhit_eff) - float(nhit)) > 1e-9
    # The engine extends a short run up to the Nmin floor (repeating: via Nrep;
    # non-repeating: via Norbits) and may overshoot it when rounding, so a run
    # that landed within ~2× of Nmin was almost certainly floor-bound.
    nmin_floor_hit = nmin <= auto_nsteps <= 2 * nmin

    # Apply manual overrides exactly as the engine does (main: num_time_steps /
    # fine_time_step_s / coarse_time_step_s).
    eff_fine = float(fine_step_override) if (fine_step_override and fine_step_override > 0) else auto_fine
    eff_nsteps = int(num_steps_override) if (num_steps_override and num_steps_override > 0) else auto_nsteps
    if coarse_step_override and coarse_step_override > 0:
        ratio = max(1, int(round(float(coarse_step_override) / eff_fine)))
        eff_coarse = eff_fine * ratio
        ncoarse = ratio
    else:
        eff_coarse = eff_fine * ncoarse
    overridden = bool(
        (num_steps_override and num_steps_override > 0)
        or (fine_step_override and fine_step_override > 0)
        or (coarse_step_override and coarse_step_override > 0)
    )

    notes: list[str] = []
    notes.append("Repeating ground track (§D4.6.1)" if repeating
                 else "Non-repeating orbit (§D4.6.2)")
    if rr_reference:
        notes.append(f"Art.22 {rr_reference.replace('Article 22, ', '')} "
                     f"@ {frequency_ghz:.3f} GHz")
    else:
        notes.append(f"no Art.22 table @ {frequency_ghz:.3f} GHz "
                     "(Nmin uses engine default)")
    if reduced_1e8:
        notes.append(
            f"§D4.1 1e8 recalc: resolution reduced (Nhit {nhit}→{res.nhit_eff:.3f}, "
            f"N'coarse={res.ncoarse}) to keep ≤1e8 steps"
        )
    if nmin_floor_hit:
        notes.append("run extended to the Nmin floor")
    if overridden:
        notes.append("manual override in effect (Number of steps / Δt fields)")

    return TimeStepPreview(
        ok=True,
        nsteps=eff_nsteps,
        fine_step_s=eff_fine,
        coarse_step_s=eff_coarse,
        ncoarse=int(ncoarse),
        nhit_requested=int(nhit),
        nhit_eff=float(res.nhit_eff),
        nmin=int(nmin),
        duration_s=eff_nsteps * eff_fine,
        repeating=bool(repeating),
        auto_nsteps=auto_nsteps,
        reduced_1e8=reduced_1e8,
        nmin_floor_hit=nmin_floor_hit,
        overridden=overridden,
        theta_3db_deg=theta_3db_deg,
        notes=notes,
    )


def _grid_count(grid_step_deg: float, gso_step_deg: float | None,
                  min_elev: float | None,
                  country_codes: list[str] | None = None) -> int:
    """Count grid points exactly the way the worker will: when
    ``country_codes`` is non-empty, run ``iter_geometry_grid`` with the
    filter so the estimate reflects only points inside the selected
    territories. Otherwise use the engine's fast analytic estimator."""
    if country_codes:
        try:
            s = engine.s1588_module()
            return sum(1 for _ in s.iter_geometry_grid(
                grid_step_deg=float(grid_step_deg),
                gso_pointing_step_deg=(
                    float(gso_step_deg) if gso_step_deg else None
                ),
                min_elevation_deg=(
                    float(min_elev) if min_elev is not None else None
                ),
                country_codes=country_codes,
            ))
        except Exception:  # noqa: BLE001
            pass
    try:
        s = engine.s1588_module()
        return int(s.estimate_geometry_grid_count(
            grid_step_deg=float(grid_step_deg),
            gso_pointing_step_deg=float(gso_step_deg) if gso_step_deg else None,
            min_elevation_deg=float(min_elev) if min_elev is not None else None,
        ))
    except Exception:  # noqa: BLE001
        # Fallback: rough analytic count (no country filter applied)
        gs = max(1.0, float(grid_step_deg))
        ng = int(180.0 / gs + 1) * int(360.0 / gs + 1)
        gs2 = max(1.0, float(gso_step_deg) if gso_step_deg else gs)
        ng *= int(360.0 / gs2 + 1)
        return ng


def estimate_aggregate(*, method: str, n_systems: int, n_sat_per_system: int,
                         n_time_steps: int, s1503_step_deg: float = 1.0,
                         grid_step_deg: float = 30.0,
                         gso_pointing_step_deg: float | None = None,
                         min_elevation_deg: float | None = 10.0,
                         country_codes: list[str] | None = None) -> Estimate:
    n_total_sats = max(1, n_systems) * max(1, n_sat_per_system)
    tsim_per_filing = _t_sim(n_sat_per_system, n_time_steps)
    twcga_per_filing = _t_wcga(n_sat_per_system, s1503_step_deg)

    if method == "method_1":
        wall = n_systems * (twcga_per_filing + tsim_per_filing) + _T_CONVOLUTION_OVERHEAD_S
        n_sims = n_systems
        notes = [
            f"WCGA + EPFD per filing × {n_systems}",
            f"≈ {n_systems} sims × {_fmt_seconds(twcga_per_filing + tsim_per_filing)}",
        ]
    elif method == "method_2":
        ng = _grid_count(grid_step_deg, gso_pointing_step_deg, min_elevation_deg,
                         country_codes)
        wall = ng * n_systems * tsim_per_filing
        n_sims = ng * n_systems
        notes = [
            f"grid points: {ng}",
            f"sims: {ng} × {n_systems} = {ng*n_systems}",
            f"≈ {_fmt_seconds(tsim_per_filing)} per sim",
        ]
    elif method == "method_3":
        # joint WCGA on combined + 1 joint sim
        t_joint_wcga = _t_wcga(n_total_sats, s1503_step_deg)
        t_joint_sim = _t_sim(n_total_sats, n_time_steps)
        # post_sum: N per-system single-entries
        t_post = n_systems * (twcga_per_filing + tsim_per_filing)
        wall = t_joint_wcga + t_joint_sim + t_post
        n_sims = 1 + n_systems  # joint + post_sum components
        notes = [
            f"joint WCGA on {n_total_sats} sats ≈ {_fmt_seconds(t_joint_wcga)}",
            f"joint EPFD sim ≈ {_fmt_seconds(t_joint_sim)}",
            f"post_sum (N single-entries) ≈ {_fmt_seconds(t_post)}",
        ]
    elif method == "method_4":
        wall = n_systems * twcga_per_filing + (n_systems * n_systems) * tsim_per_filing
        n_sims = n_systems * n_systems
        notes = [
            f"WCGAs: {n_systems} (one per filing)",
            f"geometry sims: N × N = {n_systems * n_systems}",
            f"≈ {_fmt_seconds(tsim_per_filing)} per sim",
        ]
    elif method == "method_5":
        ng = _grid_count(grid_step_deg, gso_pointing_step_deg, min_elevation_deg,
                         country_codes)
        wall = ng * n_systems * tsim_per_filing
        n_sims = ng * n_systems
        notes = [
            f"grid points: {ng}",
            f"sims: {ng} × {n_systems} = {ng * n_systems} (no convolution)",
        ]
    else:
        wall = 0.0
        n_sims = 0
        notes = [f"unknown method: {method}"]

    return Estimate(
        label=f"Aggregate · {method}",
        n_sims=n_sims,
        n_time_steps=n_time_steps,
        n_satellites_total=n_total_sats,
        wall_seconds=wall,
        notes=notes,
    )
