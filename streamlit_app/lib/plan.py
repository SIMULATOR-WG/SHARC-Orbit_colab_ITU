"""plan.py — cost-aware task planning for cluster dispatch.

Estimates a per-filing processing cost (proportional to N_sat × work) so
the dispatcher can apply LPT (longest-processing-time-first) ordering:
heavy filings start first, which shortens makespan when filing weights
are very heterogeneous.

Every estimate is best-effort. The relevant cost driver here is *known
before the run* — cost is near-linear in ``N_sat × N_steps`` and both are
integers we can read cheaply — so the estimate is low-variance, not a
black box. Any failure (unreadable SRS, exotic filing) yields a uniform
cost of ``1.0`` for that filing, degrading the schedule to plain input
order with no functional impact. See ``cluster.parallel_starmap_progress``
``costs=`` parameter for how the order is consumed.
"""
from __future__ import annotations

from typing import Any

from . import estimator


def filing_n_sat(filing: dict[str, Any], common: dict[str, Any]) -> int | None:
    """Cheap satellite count for one filing.

    Reads only the SRS orbital planes (no PFD mask, no antenna, no
    Article 22 / Resolution 76 limits) — much lighter than ``_load_cfg``.
    Returns ``None`` on any failure so the caller can fall back to a
    uniform cost.
    """
    try:
        from .job_runners.s1588_worker import _resolve_filing_path
        from src.srs_reader import (  # type: ignore[import]
            read_srs_mdb,
            srs_to_constellation_config,
        )

        srs_path = _resolve_filing_path(filing, "srs_path", "srs_relpath")
        if not srs_path:
            return None
        system = read_srs_mdb(srs_path, filing.get("ntc_id"))
        cc = srs_to_constellation_config(system)
        n_planes = int(cc.get("num_planes", 0) or 0)
        sats_per_plane = int(cc.get("sats_per_plane", 0) or 0)
        total = n_planes * sats_per_plane
        return total if total > 0 else None
    except Exception:  # noqa: BLE001
        return None


def filing_costs(
    filings: list[dict[str, Any]],
    common: dict[str, Any],
    *,
    sim: bool = True,
    wcga: bool = True,
) -> list[float]:
    """Estimated processing cost per filing, aligned to ``filings``.

    ``sim``/``wcga`` select which phases contribute (a fixed-geometry
    task has no WCGA term; a WCGA-only step has no sim term). Filings
    whose satellite count can't be resolved get cost ``1.0``.
    """
    # Scheduling proxy only. On auto (no explicit N) the run resolves a
    # per-filing §D4 count; this constant just keeps LPT ordering usable.
    n_steps = int(common.get("num_time_steps") or 3_600)
    step_deg = float(common.get("s1503_step_deg") or 1.0)

    out: list[float] = []
    for f in filings:
        n_sat = filing_n_sat(f, common)
        if not n_sat:
            out.append(1.0)
            continue
        c = 0.0
        if sim:
            c += estimator._t_sim(n_sat, n_steps)
        if wcga:
            c += estimator._t_wcga(n_sat, step_deg)
        out.append(float(c) if c > 0 else 1.0)
    return out
