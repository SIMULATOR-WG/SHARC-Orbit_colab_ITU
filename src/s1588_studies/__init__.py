"""
s1588_studies — Numerical core for Resolution 76 (Studies 1, 2, 3) over S.1503.

Submodules:
    geometry    — geometric ES/GSO point descriptor (with pointing and antenna).
    convolution — convolution of CCDFs across NGSO systems.
    percentiles — extraction of the normative percentiles (10, 1, 0.1, 0.01 %).

Function `estimate_geometry_grid_count` exported for use by the estimation API.
"""

from .geometry import (
    GeometryPoint,
    compute_grid_fingerprint,
    estimate_geometry_grid_count,
    iter_geometry_grid,
)
from .convolution import convolve_ccdf, convolve_ccdfs, convolve_ccdfs_db
from .percentiles import (
    NORMATIVE_PERCENTAGES,
    extract_percentiles,
    percentile_from_ccdf,
)
from .runner import geometry_to_wcg_result, run_epfd_at_geometry
from .multi_system import (
    JointSimulationResult,
    JointSystemSpec,
    run_joint_epfd_simulation,
)

__all__ = [
    "GeometryPoint",
    "compute_grid_fingerprint",
    "estimate_geometry_grid_count",
    "iter_geometry_grid",
    "convolve_ccdf",
    "convolve_ccdfs",
    "convolve_ccdfs_db",
    "NORMATIVE_PERCENTAGES",
    "extract_percentiles",
    "percentile_from_ccdf",
    "geometry_to_wcg_result",
    "run_epfd_at_geometry",
    "JointSimulationResult",
    "JointSystemSpec",
    "run_joint_epfd_simulation",
]
