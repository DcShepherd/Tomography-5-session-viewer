"""Optional compiled sampling, bounded row concurrency and canonical slab reuse."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager, ExitStack
import logging
import os
from threading import Lock
import numpy as np

from tomography_session_browser.domain.tomograms import Volume
from tomography_session_browser.services.volume.memory import MemoryBudget, BudgetExceeded
from tomography_session_browser.services.volume.slab_reuse import slab_planes, SLAB_KIND
from tomography_session_browser.services.volume.store import VolumeStore

try:
    import tomography_session_browser.services.volume._reslice as _portable
except ImportError:
    _portable = None

ABI = 2
#: Rows per compiled call; cancellation is checked between calls.
TILE_ROWS = 128
COMPUTE = Lock()  # Render and capture take turns; one call uses up to WORKERS row threads.
WORKERS = min(8, max(1, (os.cpu_count() or 1) // 2))


def select_sampler(portable, load_v3):
    """The x86-64-v3 build where the portable build reports CPU support, else the portable one.

    Both builds compute identical pixels. A missing or mismatched v3 build falls
    back to the portable one; a missing or mismatched portable build leaves the
    NumPy path.
    """
    if portable is None or getattr(portable, "ABI_VERSION", None) != ABI:
        return None
    if getattr(portable, "X86_64_V3", 0):
        try:
            v3 = load_v3()
        except ImportError:
            return portable
        if getattr(v3, "ABI_VERSION", None) == ABI:
            return v3
    return portable


def _load_v3():
    import tomography_session_browser.services.volume._reslice_v3 as v3
    return v3


_reslice = select_sampler(_portable, _load_v3)


def available() -> bool:
    return _reslice is not None and _reslice.ABI_VERSION == ABI


def variant() -> str | None:
    """The compiled build in use, for diagnostics: "x86-64-v3", "portable" or None (NumPy)."""
    if not available():
        return None
    assert _reslice is not None
    return "x86-64-v3" if _reslice.__name__.endswith("_v3") else "portable"


@contextmanager
def compute_lease(cancelled: Callable[[], bool]):
    while not COMPUTE.acquire(timeout=.01):
        if cancelled():
            raise InterruptedError("Obsolete compiled render")
    try:
        if cancelled():
            raise InterruptedError("Obsolete compiled render")
        yield
    finally:
        COMPUTE.release()


def sample_native(volume: Volume, matrix: np.ndarray, offset: np.ndarray,
                  axes: tuple[int, int, int], planes: range, output: np.ndarray,
                  budget: MemoryBudget, *, cancelled: Callable[[], bool]) -> bool:
    """Fill the caller's reserved output, or return False for the NumPy path.

    Source leases verify identity before/after sampling. No Qt, filesystem handle
    or mutable sampler instance escapes this operation. Missing binaries and
    section-only reads retain the original full-quality implementation.
    """
    if not available():
        return False
    with compute_lease(cancelled):
        try:
            return sample_with_reuse(volume, matrix, offset, axes, planes, output, budget,
                                     reuse=True, cancelled=cancelled)
        except (BudgetExceeded, MemoryError):
            budget.retire_group(SLAB_KIND)
        # Retry outside the exception scope, after abandoned arrays/tracebacks die.
        return sample_with_reuse(volume, matrix, offset, axes, planes, output, budget,
                                 reuse=False, cancelled=cancelled)


def sample_with_reuse(volume: Volume, matrix: np.ndarray, offset: np.ndarray,
                      axes: tuple[int, int, int], planes: range, output: np.ndarray,
                      budget: MemoryBudget, *, reuse: bool, cancelled: Callable[[], bool]) -> bool:
    assert _reslice is not None
    matrix, offset = np.ascontiguousarray(matrix), np.ascontiguousarray(offset)
    geometry = (volume.fingerprint, volume.grid, volume.dtype, axes,
                tuple(float(v) for v in matrix.flat), tuple(float(v) for v in offset))
    height, width = output.shape
    # Borrow the source before admitting optional planes; it must stay accounted
    # and pinned while interpolation runs. Reuse never forces source admission.
    with ExitStack() as staging:
        with VolumeStore(budget).read_session(volume) as reader:
            if reader.native is None:
                return False
            slab = staging.enter_context(slab_planes(budget, geometry, planes, (height, width), enabled=reuse))
            for top in range(0, height, TILE_ROWS):
                if cancelled():
                    raise InterruptedError("Obsolete compiled render")
                _reslice.sample(reader.native, matrix, offset, *axes, planes.start, planes.stop,
                                top, min(top + TILE_ROWS, height), WORKERS, output,
                                tuple(slab.arrays) if slab is not None else None,
                                slab.fresh if slab is not None else None)
        # The source lease has checked its fingerprint again before admission.
        if cancelled():
            raise InterruptedError("Obsolete compiled render")
        if slab is not None:
            slab.publish(budget, cancelled)
    return True


if not available():
    logging.getLogger(__name__).info("Compiled volume sampler unavailable; using full-quality NumPy sampling")
