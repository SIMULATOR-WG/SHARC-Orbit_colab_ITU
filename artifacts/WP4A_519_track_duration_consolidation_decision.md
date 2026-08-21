## Decision: consolidate reference-vector `T` onto the existing §D5.1.4.2 track-duration windowing

### Context

`ref_vec_track_duration_T_s` (US proposal R23-WP4A-C-0519, "Reference Vector
with Track Duration") and `MIN_DURATION` (ITU-R S.1503-4 §D5.1.4.2, the
sliding-window track-duration variant, implemented separately and earlier)
turn out to be two independent implementations of the same underlying idea:
**hold a fixed satellite set for a duration D, gated by full-window
eligibility, then re-rank and re-select.**

They currently live in two unrelated code paths (`run_epfd_simulation`'s
`ref_vec_selection` branch vs. `run_epfd_simulation_windowed`) and can
silently conflict: in `run_wcg_downlink` (`src/main.py`), `windows_main`
(driven by `MIN_DURATION`) is resolved and takes priority *before*
`ref_vec_selection` is even read. If a filing declares `MIN_DURATION > 0` at
the ES latitude (or a user sets an override) **and** `ref_vec_selection` is
also enabled, the windowed path is chosen and `ref_vec_selection`/
`ref_vec_az_deg`/etc. are silently dropped — no warning, no error.

### Decision

Consolidate onto §D5.1.4.2's windowing (`TrackDurationWindows`,
`MIN_DURATION` → `N_SW`) as the **single** hold-duration mechanism. Make
reference-vector selection a **pluggable ranking policy** inside that
windowing framework (closest-to-reference-vector V, mean of the M worst
angular-separation samples) rather than maintaining a second, parallel
"hold and re-select" implementation with its own duration parameter.

This also means the previously-scoped "Option B" work (parallelizing the
standalone `ref_vec_selection` sequential loop via a dedicated
block-dispatch worker) is no longer needed as separate work — consolidating
onto the windowed architecture inherits `_simulate_window_block`'s existing
block-of-whole-windows parallel dispatch for free.

### Resolved: does Step 22 (OR/sidelobe condition) apply under reference-vector ranking?

**No — OR/Step 22 stays off entirely when reference-vector ranking is
active.**

Step 22 is a worst-case conservatism safety net for the normative Steps
19-21 pipeline: even if a satellite fails the primary α₀/ε₀ eligibility
test, if its gain-weighted contribution is still large, keep it anyway —
so the eligibility gate never accidentally underestimates a significant
interferer. That rationale is tied specifically to worst-case-seeking.

Reference-vector selection has no equivalent concept to attach it to: its
eligibility test isn't a proxy for "might this be a big interferer," it's a
direct proxy for "could the operator's tracking algorithm even be pointing
near this satellite." A satellite failing eligibility simply isn't being
tracked — there's no "but it might interfere a lot" escape hatch, because
the model isn't hunting for interference. This matches both the proposal
text ("no OR condition... the operator's selection algorithm implicitly
decides which satellites contribute") and the existing standalone
implementation's own docstring (`_compute_epfd_for_selected_sats`: *"no
OR condition — the selection is already the operator's complete policy
choice"*).

**Implementation note:** the two eligibility tests are not byte-identical
today — the windowed/worst-case path uses `|α| ≥ α₀`, reference-vector
selection uses `|α| > α₀` (strict). Preserve each policy's own boundary
condition exactly during the refactor; do not unify them.

### Resolved: does the N_TW sliding-window-set worst-envelope apply under reference-vector ranking?

**No — skip N_TW/`MIN_SLIDING_TIME` for reference-vector mode. Use a single,
unshifted window sequence (`N_TW=1`) derived from `MIN_DURATION`/`N_SW`
only.**

`MIN_SLIDING_TIME` is not a filing-declared or operator-chosen parameter —
it's a pure §D5.1.3 mechanical formula:

```python
mst_s = max(1.0, min_orbital_period_s / (100.0 * n_satellites))
```
(`src/time_step.py:752`)

Its only purpose is generating `N_TW` phase-shifted realizations of the same
windowing process so the reported statistic isn't an artifact of an
arbitrary "when does window 0 start" choice — then taking the worst-per-
level envelope across them, because Article 22 compliance is inherently a
conservative bound. That rationale is specific to the worst-case ranking
philosophy of §D5.1.4.2. It carries no independent operational meaning to
transfer to a differently-motivated (realistic, non-worst-case) selection
policy — applying it there would quietly reintroduce worst-case-hunting
into a method whose entire premise is rejecting that, and the proposal text
never mentions window-phase averaging at all.

### Net effect

One hold-duration mechanism (`MIN_DURATION`/`N_SW`), two selectable ranking
policies within it:

| | Worst-case (existing, §D5.1.4.2) | Reference-vector (US proposal) |
|---|---|---|
| Eligibility | `\|α\| ≥ α₀` ∧ `ε ≥ ε₀`, held for entire window | `\|α\| > α₀` ∧ `ε ≥ ε₀`, held for entire window |
| Per-satellite window score | peak EPFD↓ | mean of M worst angular-separation-from-V samples |
| Selection cap | top `MAX_CO_FREQ` | top `Nco`, WCG-origin satellite force-included if eligible |
| OR/Step 22 | applies | does not apply |
| Window-set envelope (N_TW) | applies (`MIN_SLIDING_TIME`-derived) | does not apply (`N_TW=1`) |

No conflict is possible anymore — there's one hold-duration source, and the
ranking policy determines which selection rule and reporting treatment run
inside it.
