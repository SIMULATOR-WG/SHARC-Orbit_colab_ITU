"""Domain exceptions for the SHARC-Orbit S.1503 engine."""
from __future__ import annotations


class NoValidGeometry(RuntimeError):
    """Raised when the WCG search completes but finds no geometry satisfying the
    S.1503-4 store criteria (minimum elevation ε₀, GSO-arc elevation εGSO,
    exclusion angle α₀, PFD mask).

    This is a *legitimate outcome*, not a bug: under the given filing and knobs
    the search space may contain no worst-case co-frequency GSO geometry above
    ε₀. Subclasses ``RuntimeError`` so existing ``except Exception`` handlers
    still catch it, while carrying structured ``diagnostics`` (the config knobs
    that gate the criteria) so the UI/log can explain *why* and what to change.
    """

    def __init__(self, message: str, *, diagnostics: dict | None = None) -> None:
        super().__init__(message)
        self.diagnostics: dict = diagnostics or {}


class InvalidManualGeometry(ValueError):
    """Raised when a user-supplied manual ES/GSO coordinate is out of range.

    Subclasses ``ValueError`` (it is malformed input, not a search outcome), so
    it stays distinct from :class:`NoValidGeometry` and lets CLI callers catch it
    for a clean message instead of dumping a raw traceback, while the job-runner
    workers still route it through their generic failure handler.
    """

