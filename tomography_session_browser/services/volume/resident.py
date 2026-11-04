"""Optional immutable source admission into the existing evictable memory budget."""
from collections.abc import Callable
import numpy as np

from tomography_session_browser.domain.tomograms import Volume
from tomography_session_browser.parsers.volume_header import fingerprint, is_cloud_placeholder
from tomography_session_browser.services.volume.memory import VOLUME_MEMORY, MemoryBudget, MIB, BudgetExceeded
from tomography_session_browser.services.volume.store import resident_key


def load_resident(volume: Volume, *, cancelled: Callable[[], bool],
                  budget: MemoryBudget = VOLUME_MEMORY) -> bool:
    """Sequential bounded readinto; no unaccounted copy, mapping or live UI reference."""
    def check() -> None:
        if cancelled():
            raise InterruptedError("Obsolete resident source")
        if is_cloud_placeholder(volume.path) or fingerprint(volume.path) != volume.fingerprint:
            raise ValueError("The volume is unavailable or changed. Reload the processing folder.")
        if budget.used() > budget.limit():
            raise BudgetExceeded("Memory allowance fell during optional source admission.")
    check()
    if volume.grid is None:
        raise ValueError("A usable volume grid is required.")
    size = int(np.prod(volume.grid.shape_zyx)) * np.dtype(volume.dtype).itemsize
    # Optional admission leaves at least 128 MiB for foreground work and does
    # not evict useful planes to force a whole source into RAM.
    if budget.used() + size + 128*MIB > budget.limit():
        raise BudgetExceeded("Not enough spare memory for optional source residency.")
    with budget.reserve(size + 8192, "resident-read", evict=False) as reservation:
        storage = np.empty(volume.grid.shape_zyx, dtype=volume.dtype)
        view = storage.data.cast("B")
        with volume.path.open("rb", buffering=0) as stream:
            stream.seek(volume.data_offset)
            offset = 0
            while offset < size:
                check()
                stop = min(size, offset+8*MIB)
                count = stream.readinto(view[offset:stop])
                if not count:
                    raise ValueError("The volume ended while loading. Reload the processing folder.")
                offset += count
        del view
        check()
        axes = tuple(reversed(volume.grid.axes_crs))
        native = storage.transpose(tuple(axes.index(a) for a in (3, 2, 1)))
        native.setflags(write=False)
        with budget.lock:
            if cancelled():
                raise InterruptedError("Obsolete resident source")
            budget.publish(resident_key(volume), native, reservation)
    return True
