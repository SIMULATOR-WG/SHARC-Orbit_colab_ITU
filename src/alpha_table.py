"""Alpha table data model for the ``alpha_table`` satellite selection (WP 4A
Doc 4A/312, working document toward the S.1503 revision).

This module is intentionally free of any dependency on the rest of the engine:
it only validates operator-declared alpha tables, turns their sparse
(angle, probability) CDF pairs into the per-case masses that drive the TSS
accumulator, and generates the 7-table family used by the envelope examination.

Conventions (fixed by the modelling decisions):
  * The table is a **CDF**: ``prob`` is the fraction of time the active
    satellite's topocentric angle to the GSO arc (α) is ``<=`` ``angle``.
    Probability is monotonically non-decreasing; angle strictly increasing
    (Doc 4A/312 §"angle and probability should both monotonically increase").
    The complementary "% time α exceeded" view of Doc 4A/497 is a plotting
    concern (CCDF = 1 − CDF) and is NOT accepted here.
  * **TSS cases are the declared intervals** (Doc 4A/312, p. 110). For declared
    pairs ``(α_1, p_1) … (α_n, p_n)`` the table has exactly ``n + 1`` cases::

        [0, α_1)         TSS_1   += Nco[lat]·p_1
        [α_1, α_2)       TSS_2   += Nco[lat]·(p_2 − p_1)
        …
        [α_n, ∞)         TSS_n+1 += Nco[lat]·(1 − p_n)

    The last case is **unbounded** — every α at or above the last declared
    angle belongs to it, carrying the whole residual mass ``(1 − p_n)``.
    :func:`build_tss_cases` with the default ``bin_deg=0`` reproduces exactly
    this structure; that is the normative granularity, and the granularity the
    Step-20 rule "identify the case with the highest TSS" is defined against.
  * ``bin_deg > 0`` is a **non-normative** refinement: it subdivides the
    *bounded* cases into slices of at most ``bin_deg`` degrees, splitting each
    case's mass evenly among them. Per-case totals are preserved, but the TSS
    *per case* is divided by the number of slices, which changes which case wins
    the Step-20 comparison. Use it only for sensitivity studies — never to
    produce a result presented as conforming to Doc 4A/312.
"""
from __future__ import annotations

import math

import numpy as np

# Pair = (angle_deg, cumulative_probability)
AlphaPair = tuple[float, float]

_EPS = 1e-9


def validate_alpha_pairs(pairs: list[AlphaPair], *, name: str = "alpha table") -> None:
    """Validate a declared CDF table. Raises ``ValueError`` on any violation.

    Rules: at least one pair; angles strictly increasing and in [0, 180);
    probabilities non-decreasing, each in (0, 1]. A malformed table is an
    operator error and is rejected rather than silently repaired.

    Note that ``p = 0`` is rejected: a declared pair carrying no probability
    mass says nothing a CDF anchor needs to say. The 7-table generation never
    emits such pairs — see :func:`generate_seven_tables`.
    """
    if not pairs:
        raise ValueError(f"{name}: empty (needs at least one (angle, prob) pair).")
    prev_a = -np.inf
    prev_p = -np.inf
    for i, (a, p) in enumerate(pairs):
        a = float(a); p = float(p)
        if not (0.0 <= a < 180.0):
            raise ValueError(f"{name}: angle #{i}={a}° out of range [0, 180).")
        if not (0.0 < p <= 1.0 + _EPS):
            raise ValueError(f"{name}: probability #{i}={p} out of range (0, 1].")
        if a <= prev_a:
            raise ValueError(
                f"{name}: angles must strictly increase; #{i}={a}° ≤ previous {prev_a}°."
            )
        if p < prev_p - _EPS:
            raise ValueError(
                f"{name}: probabilities must be non-decreasing (CDF); "
                f"#{i}={p} < previous {prev_p}. (Did you pass a CCDF / '% time exceeded'?)"
            )
        prev_a, prev_p = a, p


def _normative_cases(pairs: list[AlphaPair]) -> tuple[list[float], list[float]]:
    """The ``n + 1`` declared TSS cases of Doc 4A/312 p. 110.

    Returns ``(edges, masses)`` as plain lists with ``len(edges) == len(masses)
    + 1``; ``edges[0] == 0.0`` and ``edges[-1] == math.inf``.
    """
    angles = [float(a) for a, _ in pairs]
    probs = [min(float(p), 1.0) for _, p in pairs]

    edges = [0.0] + angles + [math.inf]
    masses: list[float] = []
    prev_p = 0.0
    for p in probs:
        masses.append(max(0.0, p - prev_p))
        prev_p = p
    masses.append(max(0.0, 1.0 - prev_p))   # tail case [α_n, ∞)

    # A declared first angle of 0° makes case 1 = [0, 0): zero width, so no
    # satellite can ever fall in it and its credit would grow without bound.
    # Fold any zero-width case forward into the next one — the per-case totals
    # of the reachable cases are what the quota rule acts on.
    out_edges = [edges[0]]
    out_masses: list[float] = []
    carry = 0.0
    for i, m in enumerate(masses):
        if edges[i + 1] - edges[i] <= _EPS:
            carry += m
            continue
        out_masses.append(m + carry)
        carry = 0.0
        out_edges.append(edges[i + 1])
    if carry > 0.0 and out_masses:          # unreachable: the tail case is infinite
        out_masses[-1] += carry
    return out_edges, out_masses


def _refine_bounded_cases(
    edges: list[float], masses: list[float], bin_deg: float
) -> tuple[list[float], list[float]]:
    """Subdivide the bounded cases into slices of at most ``bin_deg`` degrees.

    **Non-normative** (see the module docstring). Each bounded case is split
    into ``ceil(width / bin_deg)`` equal slices sharing its mass evenly, so the
    per-case totals — and the case boundaries themselves — are preserved. The
    final unbounded case is never split.
    """
    out_edges = [edges[0]]
    out_masses: list[float] = []
    for i, m in enumerate(masses):
        lo, hi = edges[i], edges[i + 1]
        if not math.isfinite(hi):
            out_masses.append(m)
            out_edges.append(hi)
            continue
        k = max(1, int(math.ceil((hi - lo) / bin_deg - _EPS)))
        step = (hi - lo) / k
        for j in range(1, k + 1):
            out_masses.append(m / k)
            out_edges.append(lo + j * step if j < k else hi)
    return out_edges, out_masses


def build_tss_cases(
    pairs: list[AlphaPair],
    bin_deg: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Build the TSS case edges and masses that drive the accumulator.

    ``bin_deg <= 0`` (the default) returns the **normative** cases of
    Doc 4A/312 p. 110: the declared intervals, with an unbounded last case.
    ``bin_deg > 0`` additionally applies the non-normative uniform refinement of
    :func:`_refine_bounded_cases`.

    Returns ``(edges, masses)`` where ``edges`` has length ``B + 1``, ``masses``
    has length ``B`` and sums to 1. Case ``i`` covers ``[edges[i], edges[i+1])``
    and ``edges[-1]`` is ``+inf``.
    """
    validate_alpha_pairs(pairs)
    edges, masses = _normative_cases(pairs)
    if bin_deg and bin_deg > 0.0:
        edges, masses = _refine_bounded_cases(edges, masses, float(bin_deg))

    e = np.asarray(edges, dtype=np.float64)
    m = np.clip(np.asarray(masses, dtype=np.float64), 0.0, None)
    total = float(m.sum())
    if total <= 0:
        raise ValueError("alpha table has zero total probability mass.")
    m /= total                              # normalize to exactly 1
    return e, m


def _interp_cdf(common_angles: np.ndarray, pairs: list[AlphaPair]) -> np.ndarray:
    a = np.array([x for x, _ in pairs], dtype=np.float64)
    p = np.array([y for _, y in pairs], dtype=np.float64)
    return np.interp(common_angles, a, p, left=0.0, right=float(p[-1]))


def _monotone(p: np.ndarray) -> np.ndarray:
    """Enforce a non-decreasing CDF (guards fp / cross-blends)."""
    return np.maximum.accumulate(np.clip(p, 0.0, 1.0))


def _as_pairs(angles: np.ndarray, probs: np.ndarray) -> list[AlphaPair]:
    """Zip into pairs, trimming the artefacts of the union angle grid.

    The two declared tables need not start or end at the same angle — the
    canonical example in Doc 4A/312 p. 16 has ``min`` spanning 20°–50° and
    ``max`` 30°–60°. On the union grid each table is therefore evaluated outside
    its own declared span, and both extrapolations must be trimmed:

    * **Leading ``p = 0``** (below the table's first declared angle). Not a usable
      CDF anchor, and :func:`validate_alpha_pairs` rejects it. Dropping is exact:
      ``P(α ≤ 20°) = 0`` says nothing the case structure — whose first case runs
      from 0° to the first declared angle — does not already say.
    * **Trailing repeats of the last probability** (above the table's last
      declared angle, where :func:`_interp_cdf` holds ``p`` flat). Keeping them
      would invent a claim the filing never made: a flat stretch from 50° to 60°
      turns into a zero-mass case ``[50, 60)``, which displaces the unbounded
      tail case to ``[60, ∞)`` and so moves every satellite between 50° and 60°
      out of the tail. Trimming leaves the tail starting at the last angle the
      table actually declares.

    Together these make the envelope endpoints (factors 0 and 1) reproduce the
    two declared tables exactly. Genuinely interpolated tables are untouched:
    their probability keeps changing at every union angle, so nothing is flat.
    """
    pairs = [(float(a), float(p)) for a, p in zip(angles.tolist(), probs.tolist())]
    pairs = [(a, p) for a, p in pairs if p > _EPS]
    while len(pairs) >= 2 and abs(pairs[-1][1] - pairs[-2][1]) <= _EPS:
        pairs.pop()
    if not pairs:
        raise ValueError("blended alpha table has no positive probability.")
    return pairs


def generate_seven_tables(
    min_pairs: list[AlphaPair],
    max_pairs: list[AlphaPair],
) -> list[tuple[str, list[AlphaPair]]]:
    """Generate the 7-table envelope family from declared min/max (Decision 6).

    Returns ``[(label, pairs), ...]``: five tables linearly interpolated between
    min and max with factors {0, 0.25, 0.5, 0.75, 1.0} (endpoints = min, max),
    plus MinMax (blend factor ramps 0→1 along α) and MaxMin (ramps 1→0). All on
    the union angle grid of the two declared tables; each result is a valid,
    monotone CDF. The two declared tables may start at different angles.
    """
    validate_alpha_pairs(min_pairs, name="alpha_table_min")
    validate_alpha_pairs(max_pairs, name="alpha_table_max")
    common = np.array(
        sorted({a for a, _ in min_pairs} | {a for a, _ in max_pairs}),
        dtype=np.float64,
    )
    p_min = _interp_cdf(common, min_pairs)
    p_max = _interp_cdf(common, max_pairs)

    out: list[tuple[str, list[AlphaPair]]] = []
    factor_labels = [(0.0, "min"), (0.25, "mid25"), (0.5, "mid50"),
                     (0.75, "mid75"), (1.0, "max")]
    for f, label in factor_labels:
        p = _monotone((1.0 - f) * p_min + f * p_max)
        out.append((label, _as_pairs(common, p)))

    # Diagonal tables: blend factor varies linearly along the angle axis.
    span = float(common[-1] - common[0]) or 1.0
    fa = (common - common[0]) / span            # 0 → 1 along α
    p_minmax = _monotone((1.0 - fa) * p_min + fa * p_max)
    p_maxmin = _monotone(fa * p_min + (1.0 - fa) * p_max)
    out.append(("MinMax", _as_pairs(common, p_minmax)))
    out.append(("MaxMin", _as_pairs(common, p_maxmin)))
    return out
