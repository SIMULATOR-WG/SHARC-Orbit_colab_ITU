# WCG Downlink — ITU-R S.1503-4

# Numba's default threading layer on Linux is GNU OpenMP (libgomp), which is
# NOT safe across os.fork(). The engine runs @njit(parallel=True)/prange and
# then forks via multiprocessing.Pool (see src/epfd_calculator.py), so libgomp
# aborts with "fork() called from a process already using GNU OpenMP". Pin the
# layer to the fork-safe built-in "workqueue" before any numba import. This
# package is the import chokepoint for every numba-using module under src/, so
# setting it here guarantees it lands before numba initializes. setdefault keeps
# any explicit override (e.g. a cluster/CI env) authoritative.
import os as _os

_os.environ.setdefault("NUMBA_THREADING_LAYER", "workqueue")
