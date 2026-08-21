"""Time-Step-Satellites (TSS) accumulator for the ``alpha_table`` selection
(WP 4A Doc 4A/312, §D5.1.4.1 Steps 4bis2 / 9bis / 12bis / 20).

Deterministic, quota-based satellite selection: each α bin accrues "credit"
proportional to the declared target mass (Step 9bis); at each time step the
eligible satellites are classified into bins (Step 12bis) and the operating
satellites are drawn from the highest-credit bins that currently have a
satellite, spending one credit per selection (Step 20). Over the run, the
distribution of selected α converges to the declared table as far as geometry
allows; a persistently growing credit is the diagnostic that the declared table
is infeasible for the constellation.

Unlike ``SelectionConfig`` (immutable, passed by value to workers), a
``TSSAccumulator`` is **mutable per sub-run state**: it lives across the whole
sequential time loop of one alpha-table run, so it is never shared *within* a
run (parallelism is *between* the 7 envelope tables, not within one). When the
envelope runs one table per process, the accumulator is built inside the worker
and pickled back once, at the end, carrying its final credit state — plain
NumPy arrays and ints, no live handles.

Encoded modelling decisions:
  * Decision 2 — credit is decremented only on an effective Step-20 selection;
    the MIN_ANGLE_AT_ES pruning (Step 21, supplied by the caller as ``prune_fn``)
    acts only on the candidate pool, never on credit; if pruning empties the
    pool before Nco is reached, the step stops with credits intact.
  * Decision 3 — during the first orbit of the WCG-causing satellite, that
    satellite, when eligible, is selected unconditionally, occupying one Nco
    slot. It **does** spend credit from its own case: in Doc 4A/312 p. 111 the
    "Decrement the count of the TSS Counter of the selected case by one" bullet
    sits at the same level as the "first orbit" and "Otherwise" bullets, so it
    governs both branches. The remaining slots follow the quota rule.
  * Decision 8 — dual time step. The declared α table is a *time-fraction* CDF,
    so under the §D4.7 dual step (where a coarse step spans ``w = Δt/T_fine``
    fine-equivalent steps and fires far from the WCG, where α is large) the
    quota must track the *time-weighted* selected-α distribution, not the raw
    per-iteration count. Both the Step-9bis accrual and the Step-20 spend are
    therefore scaled by that step weight ``w``: ``update(weight=w)`` accrues
    ``w·Nco·masses`` and each Step-20 pick spends ``w`` credit from its bin.
    With a fixed step ``w = 1`` and this reduces exactly to the per-step form.
    The first-orbit exception (Decision 3) still spends no credit.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

# A candidate is (epfd_linear, sat_index_k).
Candidate = tuple[float, int]
# prune_fn(selected_k, remaining_candidates) -> remaining_candidates filtered.
PruneFn = "callable[[int, list[Candidate]], list[Candidate]] | None"


@dataclass
class TSSAccumulator:
    """Mutable per-sub-run quota state for one alpha table.

    Parameters
    ----------
    bin_edges, masses : from ``alpha_table.build_tss_cases`` (len B+1, B). The
                        last edge is ``+inf`` — the final case of Doc 4A/312
                        p. 110 is unbounded (``α_n ≤ α``).
    nco               : Nco[lat] — co-frequency satellite count for this ES latitude.
    wcg_ref_sat_idx   : index of the WCG-causing satellite (first-orbit exception).
    t_end_first_orbit : simulation time (s) at which that satellite's first orbit
                        ends; the exception applies while ``t_s < t_end_first_orbit``.
    """
    bin_edges: np.ndarray
    masses: np.ndarray
    nco: int
    wcg_ref_sat_idx: int | None = None
    t_end_first_orbit: float = -math.inf

    credits: np.ndarray = field(init=False)
    max_credit_seen: np.ndarray = field(init=False)  # temporal peak per bin (Decision 7)
    n_wcg_exception_hits: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        self.bin_edges = np.asarray(self.bin_edges, dtype=np.float64)
        self.masses = np.asarray(self.masses, dtype=np.float64)
        if self.bin_edges.size != self.masses.size + 1:
            raise ValueError("bin_edges must have exactly one more element than masses.")
        self.nco = int(self.nco)
        if self.nco < 1:
            raise ValueError(f"nco must be >= 1 for the alpha table, got {self.nco}.")
        n_bins = self.masses.size
        self.credits = np.zeros(n_bins, dtype=np.float64)
        self.max_credit_seen = np.zeros(n_bins, dtype=np.float64)

    # -- Step 9bis: accrue credit proportional to the target mass, once per step.
    def update(self, weight: float = 1.0) -> None:
        """Accrue one step of credit. ``weight`` is the step's fine-equivalent
        duration ``Δt/T_fine`` (Decision 8); ``1.0`` for a fixed/fine step."""
        self.credits += float(weight) * self.nco * self.masses
        np.maximum(self.max_credit_seen, self.credits, out=self.max_credit_seen)

    # -- Step 12bis helper: which α case a satellite falls in. The last case is
    # unbounded, so any α at or above the last declared angle lands there.
    def _bin_of(self, alpha_deg: float) -> int:
        b = int(np.searchsorted(self.bin_edges, alpha_deg, side="right")) - 1
        if b < 0:
            return 0
        if b >= self.masses.size:
            return self.masses.size - 1
        return b

    # -- Step 20: select the operating satellites for this step.
    def select(
        self,
        standard_items: list[Candidate],
        alpha_by_k: dict[int, float],
        t_s: float,
        prune_fn: PruneFn = None,
        weight: float = 1.0,
    ) -> list[float]:
        """Return the selected satellites' epfd↓ᵢ (linear). ``standard_items`` are
        the α₀/ε₀-eligible candidates ``(epfd, k)``; ``alpha_by_k`` maps ``k`` →
        |α| (deg). ``prune_fn`` applies MIN_ANGLE_AT_ES after each pick.
        ``weight`` is the step's fine-equivalent duration ``Δt/T_fine`` and scales
        the per-pick credit spend (Decision 8); ``1.0`` for a fixed/fine step."""
        w = float(weight)
        candidates: list[Candidate] = list(standard_items)
        selected: list[float] = []
        slots = self.nco

        # Decision 3 — first-orbit WCG exception: occupies a slot and, like any
        # other Step-20 pick, decrements its own case (Doc 4A/312 p. 111).
        if (
            self.wcg_ref_sat_idx is not None
            and t_s < self.t_end_first_orbit
            and slots > 0
        ):
            for it in candidates:
                if it[1] == self.wcg_ref_sat_idx:
                    selected.append(float(it[0]))
                    candidates.remove(it)
                    slots -= 1
                    self.n_wcg_exception_hits += 1
                    self.credits[self._bin_of(alpha_by_k.get(it[1], math.inf))] -= w
                    if prune_fn is not None:
                        candidates = prune_fn(it[1], candidates)
                    break

        # Step 20 quota loop.
        while slots > 0 and candidates:
            # Group remaining candidates by α case (Step 12bis, on live pool).
            # A missing α is treated as "beyond the last declared angle".
            by_bin: dict[int, list[Candidate]] = {}
            for it in candidates:
                by_bin.setdefault(self._bin_of(alpha_by_k.get(it[1], math.inf)), []).append(it)
            # Choose bin: highest credit → most satellites → lowest α (lowest bin).
            best_bin = max(
                by_bin,
                key=lambda b: (self.credits[b], len(by_bin[b]), -b),
            )
            # Within the bin, the highest single-entry epfd (worst-case in-bin).
            chosen = max(by_bin[best_bin], key=lambda it: it[0])
            selected.append(float(chosen[0]))
            self.credits[best_bin] -= w            # Decision 2/8: spend w credit
            candidates.remove(chosen)
            slots -= 1
            if prune_fn is not None:               # Step 21 on the pool only
                candidates = prune_fn(chosen[1], candidates)
        return selected

    # -- Decision 7: diagnostics exposed per sub-run.
    def diagnostics(self, n_steps: int | None = None) -> dict:
        max_abs = float(np.max(np.abs(self.credits))) if self.credits.size else 0.0
        peak = float(np.max(self.max_credit_seen)) if self.max_credit_seen.size else 0.0
        out = {
            "tss_max_abs": max_abs,                     # required by Doc 4A/312
            "tss_peak_temporal": peak,                  # highest ever reached
            "residual_by_bin": self.credits.copy(),     # where infeasibility concentrates
            "wcg_exception_hits": self.n_wcg_exception_hits,
        }
        if n_steps:
            # A residual growing ~linearly with steps ⇒ structural infeasibility.
            out["tss_max_abs_per_step"] = max_abs / float(n_steps)
        return out
