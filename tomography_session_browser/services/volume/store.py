"""Operation-scoped read-only leases; memmaps never escape their worker."""
from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
from collections.abc import Callable
from typing import Literal
import numpy as np
import mmap

from tomography_session_browser.domain.tomograms import Volume
from tomography_session_browser.parsers.volume_header import fingerprint, is_cloud_placeholder
from tomography_session_browser.services.volume.memory import VOLUME_MEMORY, MemoryBudget, BudgetExceeded


class SourceUnavailable(ValueError):
    """The volume file is only in the cloud, or it changed after the app read it."""

    def __init__(self, message: str, reason: Literal["cloud", "changed"]) -> None:
        super().__init__(message)
        self.reason = reason


class VolumeUnreadable(ValueError):
    """The file is local and current, but its plane holds no usable image values."""


def require_unchanged(volume: Volume, message: str = "The volume changed while reading. Reload the processing folder.") -> None:
    if fingerprint(volume.path) != volume.fingerprint:
        raise SourceUnavailable(message, "changed")


def require_current(volume: Volume, message: str) -> None:
    """Refuse a cloud-only or changed volume with a typed reason and the caller's text."""
    if is_cloud_placeholder(volume.path):
        raise SourceUnavailable(message, "cloud")
    require_unchanged(volume, message)


def resident_key(volume: Volume) -> tuple:
    return ("resident-source", volume.fingerprint, volume.grid, volume.dtype)


class VolumeStore:
    def __init__(self, budget: MemoryBudget = VOLUME_MEMORY) -> None:
        self.budget = budget

    @contextmanager
    def read_session(self, volume: Volume, *, whole: bool = True) -> Iterator[ReadSession]:
        """Borrow an admitted source, else map if budget allows, else stream sections.

        Reserve every potentially touched file page, including mapping alignment.
        This optional speedup never evicts useful planes and holds no mapping after
        the operation. This reader never promotes a source or allocates a resident copy.
        """
        with self.budget.borrow(resident_key(volume)) as resident:
            if resident is not None:
                require_current(volume, "The volume is unavailable or changed. Reload the processing folder.")
                yield ReadSession(self, volume, resident)
                require_unchanged(volume)
                return
        reservation = None
        if whole:
            try:
                reservation = self.budget.reserve(volume.fingerprint.size + 8192, "mapped-source", evict=False)
            except BudgetExceeded:
                pass
        if reservation is None:
            yield ReadSession(self, volume, None)
        else:
            with reservation, self.lease(volume) as native:
                yield ReadSession(self, volume, native)

    @contextmanager
    def plane_lease(self, volume: Volume, index: int, axis: str = "XY", slices: int = 1,
                    *, speculative: bool = False, cancelled: Callable[[], bool] = lambda: False,
                    share: bool = True) -> Iterator[np.ndarray]:
        """Lease one native plane. ``share=False`` reads without adding the plane to the
        shared cache, so a long export never evicts the planes the viewer has cached."""
        if cancelled():
            raise InterruptedError("Obsolete native-plane request")
        require_current(volume, "The volume is unavailable or changed. Make it local and reload the processing folder.")
        key = (volume.fingerprint, volume.grid, volume.dtype, axis, index, slices)
        with self.budget.borrow(key) as cached:
            if cached is not None:
                yield cached
                require_unchanged(volume)
                return
        assert volume.grid is not None
        x, y, z = volume.grid.shape_xyz
        pixels = {"XY": x*y, "XZ": x*z, "YZ": y*z}[axis]
        size = pixels * (4 if slices > 1 else np.dtype(volume.dtype).itemsize)
        storage_z, storage_y, storage_x = volume.grid.shape_zyx
        mapping_pages = min(volume.fingerprint.size, storage_x * storage_y * np.dtype(volume.dtype).itemsize + 8192)
        with self.budget.reserve(size + mapping_pages, "native-read", speculative=speculative) as reservation:
            array = self.plane(volume, index, axis, slices, cancelled=cancelled)
            if cancelled():
                raise InterruptedError("Obsolete native-plane request")
            if not share:
                array.setflags(write=False)
                yield array
                require_unchanged(volume)
                return
            with self.budget.adopt(key, array, reservation) as cached:
                del array
                yield cached
                require_unchanged(volume)

    @contextmanager
    def lease(self, volume: Volume) -> Iterator[np.ndarray]:
        grid = volume.grid
        if grid is None:
            raise ValueError("This volume has no usable grid. " + " ".join(volume.warnings))
        if is_cloud_placeholder(volume.path):
            raise SourceUnavailable("The volume is cloud-only. Make it available locally, then reload.", "cloud")
        require_unchanged(volume, "The volume changed or was replaced. Reload the processing folder.")
        mapped = np.memmap(volume.path, dtype=volume.dtype, mode="r", offset=volume.data_offset,
                           shape=grid.shape_zyx, order="C")
        mapping = mapped.base
        assert isinstance(mapping, mmap.mmap)
        try:
            storage_axes = tuple(reversed(grid.axes_crs))
            native = mapped.transpose(tuple(storage_axes.index(axis) for axis in (3, 2, 1)))
            yield native
            require_unchanged(volume)
        finally:
            mapping.close()

    @contextmanager
    def section(self, volume: Volume, index: int) -> Iterator[np.ndarray]:
        assert volume.grid is not None
        _, rows, columns = volume.grid.shape_zyx
        size = rows * columns * np.dtype(volume.dtype).itemsize
        mapped = np.memmap(volume.path, dtype=volume.dtype, mode="r",
                           offset=volume.data_offset + index * size, shape=(rows, columns))
        mapping = mapped.base
        assert isinstance(mapping, mmap.mmap)
        try:
            yield mapped
        finally:
            mapping.close()

    def plane(self, volume: Volume, index: int, axis: str = "XY", slices: int = 1,
              *, cancelled: Callable[[], bool] = lambda: False) -> np.ndarray:
        """Copy mapped side planes directly; stream sections when admission fails.

        Only the requested native plane survives the worker lease. Side planes
        reuse one budgeted mapping when possible; large sources stream sections.
        """
        grid = volume.grid
        if grid is None or axis not in ("XY", "XZ", "YZ"):
            raise ValueError("A usable axis-aligned volume grid is required.")
        require_current(volume, "The volume is cloud-only or changed. Make it local and reload.")
        if slices < 1 or slices > 101 or slices % 2 != 1:
            raise ValueError("Slice averaging requires an odd count from 1 to 101.")
        horizontal, vertical, normal = {"XY": (1, 2, 3), "XZ": (1, 3, 2), "YZ": (3, 2, 1)}[axis]
        dims = dict(enumerate(grid.shape_xyz, start=1))
        if not 0 <= index < dims[normal]:
            raise ValueError("Plane is outside this reconstruction.")
        start, stop = max(0, index-slices//2), min(dims[normal], index+slices//2+1)
        # Match the surviving storage strides. Forcing a C-order YZ output
        # turns a sequential gather into repeated long-stride page walks.
        order = "F" if grid.axes_crs.index(vertical) < grid.axes_crs.index(horizontal) else "C"
        output = np.zeros((dims[vertical], dims[horizontal]),
                          dtype=np.float32 if slices > 1 else volume.dtype, order=order)
        storage_normal = grid.axes_crs.index(normal)
        sections = range(start, stop) if storage_normal == 2 else range(grid.shape_zyx[0])
        with self.read_session(volume, whole=storage_normal != 2) as reader:
            streamed_slab = reader.native is None and storage_normal == 2 and slices > 1
            if reader.native is not None:
                # Arrange a view only: slab, output rows, output columns. Nothing
                # referencing this mapping survives the operation-scoped lease.
                permutation = tuple((3, 2, 1).index(a) for a in (normal, vertical, horizontal))
                view = reader.native.transpose(permutation)
                chunk_axis = 0 if order == "C" else 1
                for top in range(0, output.shape[chunk_axis], 32):
                    if cancelled():
                        raise InterruptedError("Obsolete native-plane request")
                    span = [slice(None), slice(None)]
                    span[chunk_axis] = slice(top, top+32)
                    target = output[tuple(span)]
                    if slices == 1:
                        target[:] = view[(index, *span)]
                    else:
                        np.mean(view[(slice(start, stop), *span)], axis=0, dtype=np.float32, out=target)
            else:
                for section_index in sections:
                    if cancelled():
                        raise InterruptedError("Obsolete native-plane request")
                    with reader.section(section_index) as mapped:
                        if storage_normal == 2:
                            values = mapped if grid.axes_crs[1] == vertical else mapped.T
                            if slices > 1:
                                output += values
                            else:
                                output[:] = values
                        else:
                            if storage_normal == 1:
                                values = mapped[index, :] if slices == 1 else np.mean(mapped[start:stop, :], axis=0, dtype=np.float32)
                            else:
                                values = mapped[:, index] if slices == 1 else np.mean(mapped[:, start:stop], axis=1, dtype=np.float32)
                            if grid.axes_crs[2] == vertical:
                                output[section_index, :] = values
                            else:
                                output[:, section_index] = values
        if streamed_slab:
            output /= stop-start
        require_unchanged(volume)
        return output



class ReadSession:
    """Worker-local view; neither this object nor its slices may outlive the lease."""

    def __init__(self, store: VolumeStore, volume: Volume, native: np.ndarray | None) -> None:
        self.store, self.volume, self.native = store, volume, native

    @contextmanager
    def section(self, index: int) -> Iterator[np.ndarray]:
        if self.native is None:
            with self.store.section(self.volume, index) as section:
                yield section
        else:
            assert self.volume.grid is not None
            order = tuple((3, 2, 1).index(axis) for axis in reversed(self.volume.grid.axes_crs))
            yield self.native.transpose(order)[index]
