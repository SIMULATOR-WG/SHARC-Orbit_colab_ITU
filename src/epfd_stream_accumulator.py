"""Streaming EPFD↓ accumulator — O(1) memory in N steps.

Replaces ``list[EPFDTimeStepResult]`` in simulations with millions/hundreds
of millions of steps. Keeps a histogram quantized in 0.1 dB bins
(S.1503-4 D7.1.3 compatible) weighted by the ``duration_s`` of each step,
diagnostic aggregates (max/min/means of horizon/visible/contributing
sats and global minimum of α), and an adaptive decimated trace for the
EPFD vs time plot in the panel.

Picklable (only ``np.ndarray`` and basic types), so that ``multiprocessing``
workers return the accumulator itself (a few KB) instead of lists of
objects (hundreds of MB for 100M steps).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


# Band covered by the histogram (covers all physical EPFD of the problem).
_BIN_MIN_DB = -350.0
_BIN_MAX_DB = 50.0
_BIN_SIZE_DB = 0.1
_NBINS = int(round((_BIN_MAX_DB - _BIN_MIN_DB) / _BIN_SIZE_DB))  # 4000

# Target for the decimated trace preserved for the EPFD↓ vs time plot.
DECIM_TARGET_POINTS = 10_000


def _empty_hist() -> np.ndarray:
    return np.zeros(_NBINS, dtype=np.float64)


def _empty_count_hist() -> np.ndarray:
    # Histogram of satellite counts (0..NSAT_HIST_MAX).
    # Kept under ``add`` to avoid dynamic allocation.
    return np.zeros(NSAT_HIST_MAX + 1, dtype=np.int64)


# Conservative upper bound (LEO megaconstellations ~ a few thousand).
NSAT_HIST_MAX = 16_383


def _bin_index(value_db: float) -> int:
    if not np.isfinite(value_db):
        return -1
    idx = int(math.floor((value_db - _BIN_MIN_DB) / _BIN_SIZE_DB + 1e-12))
    if idx < 0 or idx >= _NBINS:
        return -1
    return idx


@dataclass
class EPFDStreamAccumulator:
    """Aggregated streaming state of an EPFD↓ simulation.

    Memory:
      - EPFD histogram: ``_NBINS * 8 B`` ≈ 32 KB
      - count histograms (3×): ``3 * (NSAT_HIST_MAX+1) * 8 B`` ≈ 384 KB
      - decimated trace: ~10k points × ~7 floats ≈ 0.6 MB
      → total < 1 MB regardless of N steps.
    """

    duration_per_bin: np.ndarray = field(default_factory=_empty_hist)
    n_horizon_hist: np.ndarray = field(default_factory=_empty_count_hist)
    n_visible_hist: np.ndarray = field(default_factory=_empty_count_hist)
    n_contrib_hist: np.ndarray = field(default_factory=_empty_count_hist)

    n_steps: int = 0
    n_steps_valid: int = 0  # EPFD > -900
    n_fine_steps: int = 0   # dual time step: steps taken at the fine Δt
    n_coarse_steps: int = 0  # dual time step: steps taken at the coarse Δt
    total_duration_s: float = 0.0
    sum_n_horizon: float = 0.0
    sum_n_visible: float = 0.0
    sum_n_contrib: float = 0.0
    max_n_horizon: int = 0
    max_n_visible: int = 0
    max_n_contrib: int = 0

    epfd_max_db: float = -np.inf
    epfd_min_valid_db: float = np.inf
    min_alpha_global_deg: float = float("inf")

    peak_time_s: float | None = None
    first_time_s: float | None = None
    last_time_s: float | None = None

    # Adaptive decimated trace (mirrored arrays — saves memory).
    decim_capacity: int = DECIM_TARGET_POINTS
    decim_stride: int = 1
    decim_seen: int = 0  # steps seen since the last stride change
    decim_t_s: list[float] = field(default_factory=list)
    decim_epfd_db: list[float] = field(default_factory=list)
    decim_n_hor: list[int] = field(default_factory=list)
    decim_n_vis: list[int] = field(default_factory=list)
    decim_n_cont: list[int] = field(default_factory=list)
    decim_min_alpha_deg: list[float] = field(default_factory=list)
    decim_duration_s: list[float] = field(default_factory=list)

    # Multi-system aggregation (Resolution 76 / method_3): one sub-accumulator
    # per ``system_id``, fed from the SAME pass as the joint one — each system's
    # own linear-power contribution at the joint geometry. Because the joint
    # EPFD of a step is the linear sum over satellites, and each satellite
    # belongs to exactly one system, these curves sum back to the joint one
    # sample-for-sample; no re-simulation is needed to decompose the aggregate.
    # Same class (not a slim variant) so ``add``/``merge``/``build_ccdf``/the
    # decimated trace all work unchanged — ~1 MB per system, and a run has a
    # handful. Nested one level only: sub-accumulators keep this dict empty.
    per_system: dict[int, "EPFDStreamAccumulator"] = field(default_factory=dict)

    # ─────────────────────────────────────────────────────────────────────
    #  Insertion
    # ─────────────────────────────────────────────────────────────────────

    def system_acc(self, system_id: int) -> "EPFDStreamAccumulator":
        """Sub-accumulator for ``system_id``, created on first use."""
        sid = int(system_id)
        sub = self.per_system.get(sid)
        if sub is None:
            sub = EPFDStreamAccumulator(decim_capacity=self.decim_capacity)
            self.per_system[sid] = sub
        return sub

    def add(
        self,
        time_s: float,
        epfd_db: float,
        duration_s: float,
        num_horizon_sats: int,
        num_visible_sats: int,
        num_contributing_sats: int,
        min_alpha_deg: float,
        is_fine: bool | None = None,
    ) -> None:
        """Adds a step to the accumulator. ``O(1)`` in memory/time.

        ``is_fine`` classifies the step for the dual time step tally: ``True`` →
        fine Δt, ``False`` → coarse Δt, ``None`` → not counted (unknown).
        """
        if duration_s <= 0.0 or not np.isfinite(duration_s):
            return

        self.n_steps += 1
        if is_fine is True:
            self.n_fine_steps += 1
        elif is_fine is False:
            self.n_coarse_steps += 1
        self.total_duration_s += float(duration_s)
        if self.first_time_s is None:
            self.first_time_s = float(time_s)
        self.last_time_s = float(time_s)

        if np.isfinite(epfd_db) and epfd_db > -900.0:
            idx = _bin_index(float(epfd_db))
            if idx >= 0:
                self.duration_per_bin[idx] += float(duration_s)
            self.n_steps_valid += 1
            if epfd_db > self.epfd_max_db:
                self.epfd_max_db = float(epfd_db)
                self.peak_time_s = float(time_s)
            if epfd_db < self.epfd_min_valid_db:
                self.epfd_min_valid_db = float(epfd_db)

        h = int(num_horizon_sats) if num_horizon_sats is not None else 0
        v = int(num_visible_sats) if num_visible_sats is not None else 0
        c = int(num_contributing_sats) if num_contributing_sats is not None else 0
        if 0 <= h <= NSAT_HIST_MAX:
            self.n_horizon_hist[h] += 1
        if 0 <= v <= NSAT_HIST_MAX:
            self.n_visible_hist[v] += 1
        if 0 <= c <= NSAT_HIST_MAX:
            self.n_contrib_hist[c] += 1
        self.sum_n_horizon += h
        self.sum_n_visible += v
        self.sum_n_contrib += c
        if h > self.max_n_horizon:
            self.max_n_horizon = h
        if v > self.max_n_visible:
            self.max_n_visible = v
        if c > self.max_n_contrib:
            self.max_n_contrib = c

        if np.isfinite(min_alpha_deg) and min_alpha_deg < self.min_alpha_global_deg:
            self.min_alpha_global_deg = float(min_alpha_deg)

        self._decim_consider(
            float(time_s),
            float(epfd_db),
            float(duration_s),
            h, v, c, float(min_alpha_deg),
        )

    # ─────────────────────────────────────────────────────────────────────
    #  Adaptive decimation
    # ─────────────────────────────────────────────────────────────────────

    def _decim_consider(
        self,
        t_s: float,
        epfd_db: float,
        duration_s: float,
        n_hor: int,
        n_vis: int,
        n_cont: int,
        min_alpha_deg: float,
    ) -> None:
        self.decim_seen += 1
        if (self.decim_seen - 1) % self.decim_stride != 0:
            return
        self.decim_t_s.append(t_s)
        self.decim_epfd_db.append(epfd_db)
        self.decim_n_hor.append(n_hor)
        self.decim_n_vis.append(n_vis)
        self.decim_n_cont.append(n_cont)
        self.decim_min_alpha_deg.append(min_alpha_deg)
        self.decim_duration_s.append(duration_s)
        if len(self.decim_t_s) > 2 * self.decim_capacity:
            self._decim_halve()

    def _decim_halve(self) -> None:
        """Doubles ``decim_stride`` and removes half the points in the buffer."""
        self.decim_stride *= 2
        self.decim_t_s = self.decim_t_s[::2]
        self.decim_epfd_db = self.decim_epfd_db[::2]
        self.decim_n_hor = self.decim_n_hor[::2]
        self.decim_n_vis = self.decim_n_vis[::2]
        self.decim_n_cont = self.decim_n_cont[::2]
        self.decim_min_alpha_deg = self.decim_min_alpha_deg[::2]
        self.decim_duration_s = self.decim_duration_s[::2]

    # ─────────────────────────────────────────────────────────────────────
    #  Merge (parallel)
    # ─────────────────────────────────────────────────────────────────────

    def merge(self, other: "EPFDStreamAccumulator") -> None:
        """Merges another accumulator into this one (commutative operation)."""
        if other is None or other.n_steps == 0:
            return

        self.duration_per_bin += other.duration_per_bin
        self.n_horizon_hist += other.n_horizon_hist
        self.n_visible_hist += other.n_visible_hist
        self.n_contrib_hist += other.n_contrib_hist

        self.n_steps += other.n_steps
        self.n_steps_valid += other.n_steps_valid
        self.n_fine_steps += other.n_fine_steps
        self.n_coarse_steps += other.n_coarse_steps
        self.total_duration_s += other.total_duration_s
        self.sum_n_horizon += other.sum_n_horizon
        self.sum_n_visible += other.sum_n_visible
        self.sum_n_contrib += other.sum_n_contrib
        self.max_n_horizon = max(self.max_n_horizon, other.max_n_horizon)
        self.max_n_visible = max(self.max_n_visible, other.max_n_visible)
        self.max_n_contrib = max(self.max_n_contrib, other.max_n_contrib)

        if other.epfd_max_db > self.epfd_max_db:
            self.epfd_max_db = other.epfd_max_db
            self.peak_time_s = other.peak_time_s
        elif (other.epfd_max_db == self.epfd_max_db
              and other.peak_time_s is not None
              and (self.peak_time_s is None or other.peak_time_s < self.peak_time_s)):
            # Commutative on an exact tie: the earliest instant wins, so merge
            # order (pool vs injected executor) cannot change the reported peak.
            self.peak_time_s = other.peak_time_s
        if other.epfd_min_valid_db < self.epfd_min_valid_db:
            self.epfd_min_valid_db = other.epfd_min_valid_db
        if other.min_alpha_global_deg < self.min_alpha_global_deg:
            self.min_alpha_global_deg = other.min_alpha_global_deg

        if other.first_time_s is not None and (
            self.first_time_s is None or other.first_time_s < self.first_time_s
        ):
            self.first_time_s = other.first_time_s
        if other.last_time_s is not None and (
            self.last_time_s is None or other.last_time_s > self.last_time_s
        ):
            self.last_time_s = other.last_time_s

        # Decimated trace: concatenate, sort by time and re-decimate if it exceeds
        # the target. The result may have a different resolution than the source
        # (each chunk may have distinct strides), but this is acceptable: the trace
        # is only for the visual panel.
        self.decim_t_s.extend(other.decim_t_s)
        self.decim_epfd_db.extend(other.decim_epfd_db)
        self.decim_n_hor.extend(other.decim_n_hor)
        self.decim_n_vis.extend(other.decim_n_vis)
        self.decim_n_cont.extend(other.decim_n_cont)
        self.decim_min_alpha_deg.extend(other.decim_min_alpha_deg)
        self.decim_duration_s.extend(other.decim_duration_s)
        self.decim_seen += other.decim_seen
        while len(self.decim_t_s) > 2 * self.decim_capacity:
            self._decim_halve()

        # Per-system sub-accumulators (recurses exactly one level: a
        # sub-accumulator's own ``per_system`` is always empty).
        for sid, other_sub in other.per_system.items():
            self.system_acc(sid).merge(other_sub)

    def finalize_decimated(self) -> None:
        """Reorders the decimated trace by time (parallel chunks arrive out of order)."""
        for sub in self.per_system.values():
            sub.finalize_decimated()
        if not self.decim_t_s:
            return
        idx = np.argsort(np.asarray(self.decim_t_s, dtype=np.float64), kind="stable")
        if np.array_equal(idx, np.arange(len(self.decim_t_s))):
            return
        self.decim_t_s = [self.decim_t_s[i] for i in idx]
        self.decim_epfd_db = [self.decim_epfd_db[i] for i in idx]
        self.decim_n_hor = [self.decim_n_hor[i] for i in idx]
        self.decim_n_vis = [self.decim_n_vis[i] for i in idx]
        self.decim_n_cont = [self.decim_n_cont[i] for i in idx]
        self.decim_min_alpha_deg = [self.decim_min_alpha_deg[i] for i in idx]
        self.decim_duration_s = [self.decim_duration_s[i] for i in idx]

    # ─────────────────────────────────────────────────────────────────────
    #  Outputs
    # ─────────────────────────────────────────────────────────────────────

    def build_ccdf(self) -> tuple[np.ndarray, np.ndarray]:
        """Returns ``(epfd_db_desc, percentage_desc)`` for the CCDF S.1503-4 D7.1.3."""
        total = float(self.total_duration_s)
        if total <= 0.0:
            return np.array([]), np.array([])
        nz = np.where(self.duration_per_bin > 0.0)[0]
        if nz.size == 0:
            return np.array([]), np.array([])
        bin_centers = _BIN_MIN_DB + nz.astype(np.float64) * _BIN_SIZE_DB
        weights_asc = self.duration_per_bin[nz]
        bins_desc = bin_centers[::-1]
        weights_desc = weights_asc[::-1]
        # Clamped for the same reason as in EPFDWindowStats.build_ccdf: a
        # cumulative share of the run cannot exceed 100 %, and a few-ulp
        # overshoot would fail §D7.1.3 on the 100 %-time row.
        percentages = np.minimum(np.cumsum(weights_desc) / total * 100.0, 100.0)
        return bins_desc, percentages

    def decimated_series(self) -> dict[str, list]:
        return {
            "t_s": list(self.decim_t_s),
            "epfd_dBW": list(self.decim_epfd_db),
            "num_horizon_sats": list(self.decim_n_hor),
            "num_visible_sats": list(self.decim_n_vis),
            "num_contributing_sats": list(self.decim_n_cont),
            "min_alpha_deg": list(self.decim_min_alpha_deg),
            "duration_s": list(self.decim_duration_s),
        }

    @property
    def mean_n_horizon(self) -> float:
        return self.sum_n_horizon / self.n_steps if self.n_steps else 0.0

    @property
    def mean_n_visible(self) -> float:
        return self.sum_n_visible / self.n_steps if self.n_steps else 0.0

    @property
    def mean_n_contrib(self) -> float:
        return self.sum_n_contrib / self.n_steps if self.n_steps else 0.0

    def epfd_mean_db(self) -> float:
        """Duration-weighted linear mean, converted to dB.

        Steps with null EPFD (no contributors, reported as −999 dB and not
        binned) carry zero linear power but still occupy simulated time, so
        the mean is taken over the **total** duration — not only the time
        spent in valid bins.
        """
        total = float(self.total_duration_s)
        if total <= 0.0:
            return -999.0
        nz = np.where(self.duration_per_bin > 0.0)[0]
        if nz.size == 0:
            return -999.0
        bin_centers = _BIN_MIN_DB + nz.astype(np.float64) * _BIN_SIZE_DB
        w = self.duration_per_bin[nz]
        # Weighted linear sum (mean of the linear power over the total time;
        # null-EPFD time contributes 0 to the numerator only).
        lin = np.power(10.0, bin_centers / 10.0)
        mean_lin = float(np.sum(w * lin) / total)
        if mean_lin <= 0.0:
            return -999.0
        return 10.0 * math.log10(mean_lin)


@dataclass
class EPFDWindowStats:
    """Slim per-window-set statistics for S.1503-4 §D5.1.4.2.

    Same 0.1 dB binning contract as :class:`EPFDStreamAccumulator` but without
    the satellite-count histograms and the decimated trace (N_TW can reach
    hundreds of sets; each instance stays ≈ 32 KB). ``merge`` is commutative,
    so per-chunk instances combine identically regardless of arrival order.
    """

    duration_per_bin: np.ndarray = field(default_factory=_empty_hist)
    n_steps: int = 0
    n_steps_valid: int = 0
    total_duration_s: float = 0.0
    epfd_max_db: float = -np.inf
    epfd_min_valid_db: float = np.inf
    peak_time_s: float | None = None

    # §D5.1.4.2 Step 19/19bis diagnostics, per closed window of this set.
    # An empty tracked set (no satellite met the §D5.1.4 condition through the
    # WHOLE window) is not an error, but it collapses the aggregate onto the
    # Step-20 gain branch alone and is strongly anti-conservative: it is what a
    # MIN_DURATION longer than any achievable track looks like. Counting it is
    # the only way the result can be read for what it is.
    n_windows: int = 0
    n_windows_no_eligible: int = 0   # Step 19 produced no eligible satellite
    n_windows_capped: int = 0        # Step 19bis had to drop satellites (MAX_CO_FREQ bit)
    sum_eligible: int = 0            # Σ eligible satellites over closed windows

    def note_window(self, n_elig: int, capped: bool) -> None:
        self.n_windows += 1
        self.sum_eligible += int(n_elig)
        if n_elig == 0:
            self.n_windows_no_eligible += 1
        if capped:
            self.n_windows_capped += 1

    @property
    def empty_window_fraction(self) -> float:
        return (self.n_windows_no_eligible / self.n_windows) if self.n_windows else 0.0

    def add(self, time_s: float, epfd_db: float, duration_s: float) -> None:
        if duration_s <= 0.0 or not np.isfinite(duration_s):
            return
        self.n_steps += 1
        self.total_duration_s += float(duration_s)
        if np.isfinite(epfd_db) and epfd_db > -900.0:
            idx = _bin_index(float(epfd_db))
            if idx >= 0:
                self.duration_per_bin[idx] += float(duration_s)
            self.n_steps_valid += 1
            if epfd_db > self.epfd_max_db:
                self.epfd_max_db = float(epfd_db)
                self.peak_time_s = float(time_s)
            if epfd_db < self.epfd_min_valid_db:
                self.epfd_min_valid_db = float(epfd_db)

    def add_batch(
        self,
        epfd_db: np.ndarray,
        duration_s: float,
        first_time_s: float = 0.0,
        time_step_s: float = 0.0,
    ) -> None:
        """Vectorized :meth:`add` over a whole closed window.

        Equivalent to calling :meth:`add` once per entry of ``epfd_db`` with a
        constant ``duration_s``, but in a handful of numpy operations. The
        single-pass §D5.1.4.2 engine closes one window per fine time step, so a
        per-step Python call would dominate the run (it is ~35x the cost of the
        whole vectorized window reduction).

        ``first_time_s``/``time_step_s`` locate the entries in time, used only to
        report ``peak_time_s``; pass ``time_step_s=0`` to skip that bookkeeping.

        Binning is bit-identical to :func:`_bin_index`. The bin weights are
        accumulated as ``count * duration_s`` rather than by repeated addition,
        so a bin's weight can differ from the scalar path in the last ulp.
        """
        arr = np.asarray(epfd_db, dtype=np.float64)
        n = int(arr.size)
        if n == 0 or duration_s <= 0.0 or not np.isfinite(duration_s):
            return
        self.n_steps += n
        self.total_duration_s += float(duration_s) * n

        valid = np.isfinite(arr) & (arr > -900.0)
        n_valid = int(valid.sum())
        if n_valid == 0:
            return
        vals = arr[valid]
        self.n_steps_valid += n_valid

        # Clip before the cast: a finite-but-huge epfd (a degenerate mask, a bad
        # bandwidth correction) passes the > −900 filter and would make the
        # int64 cast emit a RuntimeWarning on every closed window. Both paths
        # drop the entry either way; clipping keeps the log readable.
        scaled = np.clip((vals - _BIN_MIN_DB) / _BIN_SIZE_DB + 1e-12,
                         -1.0, float(_NBINS) + 1.0)
        idx = np.floor(scaled).astype(np.int64)
        keep = (idx >= 0) & (idx < _NBINS)
        if keep.any():
            counts = np.bincount(idx[keep], minlength=_NBINS)
            self.duration_per_bin += counts.astype(np.float64) * float(duration_s)

        vmax = float(vals.max())
        if vmax > self.epfd_max_db:
            self.epfd_max_db = vmax
            if time_step_s:
                # First occurrence of the max, matching the scalar path's
                # strict ``>`` comparison.
                j = int(np.flatnonzero(valid)[int(np.argmax(vals))])
                self.peak_time_s = float(first_time_s + j * time_step_s)
            # Without a time step the instant is unknown; leave whatever was
            # already recorded rather than erasing it.
        vmin = float(vals.min())
        if vmin < self.epfd_min_valid_db:
            self.epfd_min_valid_db = vmin

    def merge(self, other: "EPFDWindowStats") -> None:
        """Combine two partial accumulators.

        Commutative and associative in every field, ``peak_time_s`` included:
        on an exact tie in ``epfd_max_db`` the **earliest** instant wins, which
        matches the scalar path's first-occurrence rule and makes the result
        independent of the order chunks happen to arrive in. Exact ties are not
        exotic — the pfd mask clamps at its edge and the antenna gain pattern
        saturates, so identical window peaks are common.
        """
        if other is None or (other.n_steps == 0 and other.n_windows == 0):
            return
        self.duration_per_bin += other.duration_per_bin
        self.n_steps += other.n_steps
        self.n_steps_valid += other.n_steps_valid
        self.total_duration_s += other.total_duration_s
        self.n_windows += other.n_windows
        self.n_windows_no_eligible += other.n_windows_no_eligible
        self.n_windows_capped += other.n_windows_capped
        self.sum_eligible += other.sum_eligible
        if other.epfd_max_db > self.epfd_max_db:
            self.epfd_max_db = other.epfd_max_db
            self.peak_time_s = other.peak_time_s
        elif (other.epfd_max_db == self.epfd_max_db
              and other.peak_time_s is not None
              and (self.peak_time_s is None or other.peak_time_s < self.peak_time_s)):
            self.peak_time_s = other.peak_time_s
        if other.epfd_min_valid_db < self.epfd_min_valid_db:
            self.epfd_min_valid_db = other.epfd_min_valid_db

    def build_ccdf(self) -> tuple[np.ndarray, np.ndarray]:
        """Same CCDF contract as :meth:`EPFDStreamAccumulator.build_ccdf`."""
        total = float(self.total_duration_s)
        if total <= 0.0:
            return np.array([]), np.array([])
        nz = np.where(self.duration_per_bin > 0.0)[0]
        if nz.size == 0:
            return np.array([]), np.array([])
        bin_centers = _BIN_MIN_DB + nz.astype(np.float64) * _BIN_SIZE_DB
        weights_desc = self.duration_per_bin[nz][::-1]
        # A cumulative fraction of the total time cannot exceed 1, but the
        # running sum can overshoot the total by a few ulp, and a Py of
        # 100.00000000000017 fails the "Py <= Pi" test of §D7.1.3 on the
        # 100 %-time row for no physical reason. Clamp at the source so the
        # exported CDF never shows more than 100 % either.
        percentages = np.minimum(np.cumsum(weights_desc) / total * 100.0, 100.0)
        return bin_centers[::-1], percentages


__all__ = ["EPFDStreamAccumulator", "EPFDWindowStats", "DECIM_TARGET_POINTS"]
