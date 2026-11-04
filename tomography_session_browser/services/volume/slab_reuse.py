"""Transactional slab-plane admission into the existing evictable memory budget."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
import numpy as np

from tomography_session_browser.services.volume.memory import MemoryBudget, MemoryLease, MIB

SLAB_KIND = "oblique-slab"
SLAB_LIMIT = 64 * MIB


@dataclass
class SlabPlanes:
    arrays: list[np.ndarray]
    fresh: tuple[bool, ...]
    pending: list[tuple[tuple, int, MemoryLease]]

    def publish(self, budget: MemoryBudget, cancelled: Callable[[], bool]) -> None:
        with budget.lock:
            if cancelled():
                raise InterruptedError("Obsolete slab admission")
            if budget.used() > budget.limit():
                return  # Headroom fell during sampling; keep the image, omit optional reuse.
            for key, index, lease in self.pending:
                budget.publish(key, self.arrays[index], lease)


@contextmanager
def slab_planes(budget: MemoryBudget, geometry: tuple, planes: range,
                shape: tuple[int, int], *, enabled: bool) -> Iterator[SlabPlanes | None]:
    """One active geometry, at most 64 MiB; never allocate a second source copy.

    The caller serialises compiled requests. Existing entries remain immutable
    and pinned until the call ends; new entries become visible only on success.
    Level/filter/crop changes reuse native planes because they happen afterwards.
    """
    size = shape[0] * shape[1] * 8
    keys = tuple((SLAB_KIND, geometry, plane) for plane in planes)
    if not enabled or (len(keys) + 1) * size > SLAB_LIMIT:
        budget.retire_group(SLAB_KIND)
        yield None
        return
    budget.retire_group(SLAB_KIND, keep=keys)
    staged = SlabPlanes([], (), [])
    flags: list[bool] = []
    borrowed: list[np.ndarray | None] = []
    with ExitStack() as leases:
        try:
            # Pin the useful overlap before reserving any missing planes.
            borrowed = [leases.enter_context(budget.borrow(key)) for key in keys]
            for index, (key, array) in enumerate(zip(keys, borrowed, strict=True)):
                flags.append(array is None)
                if array is None:
                    lease = leases.enter_context(budget.reserve(size, SLAB_KIND, evict=False, group_limit=SLAB_LIMIT))
                    array = np.empty(shape, dtype=np.float64)
                    staged.pending.append((key, index, lease))
                staged.arrays.append(array)
            array = None
            borrowed.clear()
            staged.fresh = tuple(flags)
            yield staged
        finally:
            # Exceptions can retain tracebacks; drop staged allocations before
            # releasing their reservations, including a failed partial admission.
            array = None
            borrowed.clear()
            staged.arrays.clear()
            staged.pending.clear()
