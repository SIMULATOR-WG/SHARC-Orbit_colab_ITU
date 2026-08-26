## Fix: the WCGA candidate-bin bookkeeping discarded the worst case

### Symptom

Orbits were logging

```
WCGA S.1503-4: no valid geometry found.
```

immediately after logging that they had **admitted hundreds of millions of
points**:

```
Grid done: 836249709 points evaluated (ok=808391423, excl=0, low_elev=27858286)
WCGA extreme cases phase (6 sequential): 0.12s
WCGA S.1503-4: no valid geometry found.
```

That is not a physical outcome. Every point with `status == "ok"` has passed
admission (§D5.1.4.1 Step 18: `α ≥ α₀ ∧ ε ≥ ε₀ ∧ elGSO ≥ ε_GSO`, or the gain
OR-condition) and is by definition a candidate geometry. 808 million
candidates cannot yield no winner. The search was finding the point and the
bookkeeping was throwing it away.

Because the message reads like an empty search space, the loss was silent: the
run continued, the remaining orbits produced a winner, and that winner was
reported as *the* WCG.

### How much was lost

Counting `> WCGA sat` starts against `done`/`no valid geometry` outcomes per
run (`323520263`, 10.7 GHz, unless noted):

| run | D | orbits | winners | lost |
|---|---|---|---|---|
| `8c8259c07b3b` | 0.6 m | 9 | 1 | **8** |
| `427ded55c8b2` | 0.6 m | 9 | 1 | **8** |
| `b71d735376e3` | 0.6 m | 9 | 1 | **8** |
| `2ff9173e17d9` | 1.2 m | 9 | 8 | 1 |
| `803ffb8b20a3` | 1.2 m, 4 filings | 45 | 38 | 7 |
| `49e62b78b7b7` | — | 20 | 14 | 6 |
| `1fa966fe9c25` | — | 11 | 6 | 5 |
| `f9de80c85557` | — | 11 | 7 | 4 |
| `09a69702f21c` | — | 11 | 10 | 1 |

The three 0.6 m runs kept **one orbit out of nine**, which is why they agree to
four decimals: they all lost the same eight and kept the same one. The
geometry the Gmax−30 study pinned for 0.6 m was therefore not the worst case
across the constellation — it was the only survivor.

### Mechanism

`_WCGState` keeps the winner in a **bin**: candidates within `BIN_SIZE = 0.1`
dB of the best margin seen so far, from which the winner is the one with the
lowest apparent angular velocity (S.1503-4 §D3.1.2 — a slower satellite
persists longer and dominates the time statistics). Two fields drive it:

- `bin_top_margin` — the highest margin seen; the window is
  `[bin_top_margin − BIN_SIZE, bin_top_margin]`.
- `_bin_candidates` — the entries inside that window, capped at
  `BIN_CAND_MAX = 4096` so a degenerate plateau cannot grow without bound.

The grid is evaluated one partial state per swept satellite latitude and the
partials are merged. `merge()` did this:

```python
self.bin_top_margin = max(self.bin_top_margin, other.bin_top_margin)
self._bin_candidates.extend(other._bin_candidates)
if len(self._bin_candidates) > self.BIN_CAND_MAX:
    self._bin_candidates.sort(key=lambda t: t[1])          # by ang_vel
    self._bin_candidates = self._bin_candidates[: self.BIN_CAND_MAX]
self._prune_candidates()                                   # by margin window
self._recompute_winner()
```

The cap is applied **before** the prune, and it ranks by angular velocity.
"Slowest" says nothing about margin. So when a merge raised
`bin_top_margin` — that is, when the incoming partial carried a *better*
candidate — the sequence was:

1. `bin_top_margin` correctly becomes the new, higher value.
2. The cap sorts by `ang_vel` and keeps the slowest 4096. The candidate that
   raised the top need not be slow, so it can be cut here.
3. `_prune_candidates()` then drops every survivor for sitting more than
   `BIN_SIZE` below the *new* top — which they do, because they came from the
   old, lower bin.
4. `_bin_candidates` is empty, so `_recompute_winner()` sets
   `best_result = None`, and the caller reports "no valid geometry found".

`update()` had the same hazard on a full list: it refused the insert unless
the newcomer was slower than the current worst, so a point that raised the top
while moving fast was dropped, and the following prune could empty the bin.

### Why it bit on these runs and not others

Two conditions have to coincide, and both are ordinary on this workload.

**The cap has to saturate.** That takes thousands of near-tied candidates,
which is what a fine grid produces: 2961 swept latitudes at 0.1° against 1481
at 0.2°. The same orbit that failed at 0.1° completed at 0.2°.

**Margin and EPFD have to disagree.** With Note 22.5C.4 active the threshold
ramps from −160 dB (|lat| ≤ 57.5°) to −165.3 dB (|lat| ≥ 63.75°), so two
candidates 5.3 dB apart in raw EPFD can be level in margin, and a
high-latitude candidate can be slow while the better-margin one is not. Where
the threshold is constant, margin ≡ EPFD and the coincidence is far less
likely.

### The change

Prune to the margin window **first**, cap afterwards — inside the window,
comparing angular velocity is what §D3.1.2 asks for and cannot discard the
winner, because the winner defines the window.

```python
self.bin_top_margin = max(self.bin_top_margin, other.bin_top_margin)
self._bin_candidates.extend(other._bin_candidates)
self._prune_candidates()                       # window first
if len(self._bin_candidates) > self.BIN_CAND_MAX:
    self._bin_candidates.sort(key=lambda t: t[1])
    del self._bin_candidates[self.BIN_CAND_MAX:]
self._recompute_winner()
```

`update()` becomes the same shape — append, prune, cap — instead of refusing
the insert when full.

The failure branch also stopped conflating two different situations. No
winner with `n_ok > 0` is a bookkeeping failure and is logged as `ERROR`
saying so; no winner with `n_ok == 0` is a genuinely empty search space and
stays a `WARNING`, now naming the exclusion and low-elevation counts.

### Reproduction

Saturate the bin with slow, low-margin candidates, then merge a better one
that happens to be faster:

```python
acc = _WCGState()
for k in range(_WCGState.BIN_CAND_MAX):
    acc.update(-171.60 - (k % 90) * 0.001, 0.001 + k * 1e-7, _res(-171.60))
incoming = _WCGState()
incoming.update(-162.75, 0.90, _res(-162.75))
acc.merge(incoming)
```

Before: `bin_top_margin = -162.75`, `_bin_candidates = []`,
`best_result = None`. After: one candidate, `best_epfd = -162.75`.

### Confirmation on real data

The same orbit (mask 10604, i = 148°) re-run at the same 0.1° step, with an
identical grid — **836 249 709 points evaluated, 808 391 423 admitted**, the
same numbers as the failing run — produced:

```
WCGA S.1503-4 done: EPFD=-162.74 dBW, ES=(0.56°, -101.03°), α=-0.000°
```

Same search, same admitted set, a winner instead of nothing.

That candidate is **8.9 dB more severe** than the −171.61 the 1.2 m run had
been reporting, and better in margin by 3.6 dB (−2.74 against −6.31: at
|lat| = 0.56° the threshold is −160, not −165.3). It also matches what method
1 and the ITU software find (~0.45°S, 99.4°W, −162.7). So this defect — not
the 22.5C.4 ramp, not the θ sampling asymmetry of §D.3.1.3.4, not the Table 8
ε_GSO gate — is what made our WCG diverge from theirs. Those three were
investigated and each ruled out on its own evidence; this one reproduces.

### Provenance

**Inherited, not introduced by the campaign branch.** `main`'s `merge()` and
`update()` are byte-identical to the versions above, the branch diff
`main..HEAD` changes zero lines of the bin bookkeeping, and both
`BIN_CAND_MAX` and its truncation arrived together in `3395bfb`
(2026-06-08, "publish: 98f32819 launcher art 22"), which is on `main`.

Any WCGA result produced since that commit may be affected, on any branch,
and more so the finer the grid.

### Consequences

Every pinned geometry taken from an affected run has to be re-derived, and
everything pinned on it re-run. That is what the 10.7 GHz re-run campaign
(`scripts/run_10ghz_newwcg_campaign.py`) exists for. The 0.6 m geometry is the
one to distrust most: it rested on a single surviving orbit.

### Tests

`tests/test_wcg_selection_keeps_max.py`. Five tests pin the selection contract
— a clear peak wins from any position in the stream, the slowest wins inside
the window, a slower point outside it is rejected, the winner is never more
than the window below the peak, and the window is the documented 0.1 dB — and
three cover this defect: merging a better-but-faster candidate, `update()` on
a full list, and a winner surviving many merged partials.
