"""One cancellable small native-plane prefetch; no UI publication or opened state."""
from threading import Event

import numpy as np
from PySide6.QtCore import QRunnable, QThreadPool
from tomography_session_browser.services.volume.store import VolumeStore
from tomography_session_browser.services.volume.memory import MIB
from tomography_session_browser.services.volume.render import plane_geometry
from tomography_session_browser.presentation.tomogram_versions import review_versions


class PrefetchTask(QRunnable):
    def __init__(self, volume, axis, plane, slices, cancelled) -> None:
        super().__init__()
        self.volume, self.axis, self.plane, self.slices, self.cancelled = volume, axis, plane, slices, cancelled

    def run(self) -> None:
        try:
            with VolumeStore().plane_lease(self.volume, self.plane, self.axis, self.slices,
                    speculative=True, cancelled=self.cancelled.is_set):
                pass
        except (OSError, ValueError, RuntimeError):
            pass  # Optional read never makes an unavailable source look opened.


class NativePrefetch:
    def __init__(self, tab) -> None:
        self.tab = tab
        self.pool = QThreadPool(tab)
        self.pool.setMaxThreadCount(1)
        self.cancelled = Event()

    def cancel(self) -> None:
        self.cancelled.set()
        self.pool.clear()

    def request(self) -> None:
        self.cancel()
        tab, volume = self.tab, self.tab.volume()
        if any(tab.state.rotation):
            return
        if volume is None or volume.grid is None or not tab.isVisible():
            return
        axis, slices = tab.state.axis, tab.state.slices
        if tab.playback.timer.isActive():
            plane = (tab.state.plane or 0) + tab.playback.direction
            if not 0 <= plane < plane_geometry(volume, axis)[2]:
                return
        else:
            keys = tab.review_list.ordered_keys
            if tab.state.row_key not in keys or keys.index(tab.state.row_key) + 1 >= len(keys):
                return
            row = tab.review_list.rows[keys[keys.index(tab.state.row_key)+1]].tomogram
            choices = review_versions(row)
            volume = next((v for v in choices if v.id == tab.last_versions.get(row.id)), next(iter(choices), None))
            if volume is None or volume.grid is None:
                return
            memory = tab.view_memory.get((row.id, volume.id))
            if memory and any(memory.rotation):
                return
            axis, slices = (memory.axis, memory.slices) if memory else ("XY", 1)
            plane = memory.plane if memory and memory.plane is not None else plane_geometry(volume, axis)[2] // 2
        nx, ny, _ = plane_geometry(volume, axis)
        if nx * ny * max(4, np.dtype(volume.dtype).itemsize) > 8 * MIB:
            return
        self.cancelled = Event()
        self.pool.start(PrefetchTask(volume, axis, plane, slices, self.cancelled), -1)
