"""Content-change policy over the shared, bounded snapshot lifecycle."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from collections.abc import Callable
from PySide6.QtCore import QEvent, QObject, QPoint
from PySide6.QtWidgets import QApplication

from tomography_session_browser.domain.tomograms import Tomogram, Volume
from tomography_session_browser.presentation.tomogram_state import TomogramViewState
from tomography_session_browser.presentation.tomogram_versions import GEOMETRY_FIELDS, SharedGeometry, share_geometry
from tomography_session_browser.ui.motion.content_change import ContentTransition
from tomography_session_browser.ui.motion.preferences import motion_enabled
from tomography_session_browser.ui.motion.section_reveal import settle_layouts
from tomography_session_browser.ui.motion.tokens import DURATION_PANEL_MS
from tomography_session_browser.ui.tomograms.pane_transition import PaneTransition, image_bounds
from tomography_session_browser.ui.tomograms.focus_transition import FocusTransition, regions

if TYPE_CHECKING:
    from tomography_session_browser.ui.tomograms.tab import TomogramTab
    from tomography_session_browser.ui.tomograms.rendering import RenderJob

LOGGER = logging.getLogger(__name__)

#: How long after the commit a maximise or restore may still be placing the
#: window. Windows places a maximised window about 20 ms into the move; a
#: later placement retires the move, as any resize does.
LATE_PLACEMENT_MS = DURATION_PANEL_MS / 2


class StageTransition(QObject):
    """One outgoing picture, never an accepted/rendered volume frame.

    Focus also pictures the committed window once, without its stage.
    """

    def __init__(self, tab: TomogramTab) -> None:
        super().__init__(tab)
        self.tab = tab
        self.content = ContentTransition(tab.stage, deferred_start=True, timeout_ms=150,
            hide_annotations=lambda: tab.stage.set_annotations_visible(False),
            restore_annotations=lambda: tab.stage.set_annotations_visible(True))
        self.target: tuple[str, str] | None = None
        self.destination: TomogramViewState | None = None
        self.generation: int | None = None
        self.auto_repeat = False
        self.panes: PaneTransition | None = None
        self.focus: FocusTransition | None = None
        self._changing_layout = False
        self.content.ended.connect(self._ended)

    def attach(self) -> None:
        """Watch keys while the tab shows: on the application, this runs on every event."""
        QApplication.instance().installEventFilter(self)

    def detach(self) -> None:
        QApplication.instance().removeEventFilter(self)
        self.auto_repeat = False  # Its key release may now go unseen.

    def _ended(self, reason: str) -> None:
        if self.focus is not None and reason != "finished":
            LOGGER.info("Focus motion ended early: %s", reason)
        self.target = None
        self.destination = None
        self.generation = None
        self.panes = None
        self.focus = None

    @property
    def committing_layout(self) -> bool:
        return self._changing_layout or self.content.committing_geometry

    def change_focus(self, enabled: bool, change: Callable[[], None]) -> None:
        """Commit shell once; only the bounded outgoing picture moves."""
        tab = self.tab
        self.content.end("newer_request")
        frame = tab.rendering.accepted
        animate = (frame is not None and tab.rendering.is_current() and tab.stage.isVisible()
                   and not tab.playback.timer.isActive() and not self.auto_repeat and motion_enabled(tab.stage))
        self._changing_layout = True
        try:
            if animate:
                window = tab.window()
                placement = (window.isMaximized(), window.isFullScreen())
                self.focus = FocusTransition(regions(tab), image_bounds(tab.stage, window), enabled,
                                             window.mapToGlobal(QPoint()))
                self.target = (frame.job.row_key, frame.job.volume.id)
                tab.stage.set_annotations_visible(False)
                self.content.replace(change, surface=tab.window())
                self.content.move(self.focus.picture_layout(tab), duration_ms=DURATION_PANEL_MS,
                                  beneath=self.focus.beneath(tab))
                if self.content.in_progress and (window.isMaximized(), window.isFullScreen()) != placement:
                    self.content.follow(self._move_focus_again, within_ms=LATE_PLACEMENT_MS)
            else:
                change()
        finally:
            self._changing_layout = False
        if not self.content.in_progress:
            tab.stage.set_annotations_visible(True)
            self._ended("cut")

    def _move_focus_again(self) -> None:
        """The platform placed the Focus window late: move again over that geometry."""
        tab, focus = self.tab, self.focus
        if focus is None:
            return
        LOGGER.debug("Focus window placed late; moving again")
        self._changing_layout = True
        try:
            # Only a frame rendered for the placed stage releases the end hold.
            self.generation = None
            tab.request_render()
            self.content.move(focus.picture_layout(tab), duration_ms=DURATION_PANEL_MS,
                              beneath=focus.beneath(tab))
        finally:
            self._changing_layout = False

    def change_layout(self, mode: str) -> None:
        tab = self.tab
        self.content.end("newer_request")
        frame = tab.rendering.accepted
        if (frame is not None and mode != tab.state.layout and tab.rendering.is_current() and tab.stage.isVisible()
                and not tab.playback.timer.isActive() and not self.auto_repeat and motion_enabled(tab.stage)):
            self.panes = PaneTransition(image_bounds(tab.stage, tab.stage), mode)
            self.target = (frame.job.row_key, frame.job.volume.id)
            tab.stage.set_annotations_visible(False)
            self.content.replace(lambda: None)
        self._changing_layout = True
        try:
            tab.stage.change_layout(mode)
            settle_layouts(tab.stage, tab)
            if self.panes is not None:
                self.content.move(self.panes.picture_layout(tab.stage), duration_ms=DURATION_PANEL_MS)
        finally:
            self._changing_layout = False
        if not self.content.in_progress:
            tab.stage.set_annotations_visible(True)
            self._ended("cut")

    def prepare(self, row: Tomogram, volume: Volume | None, state: TomogramViewState, *, allow: bool = True) -> None:
        """Picture before selection clears panes; a placed version may arrive asynchronously."""
        tab = self.tab
        target = (row.id, volume.id) if volume is not None else None
        # A window-owned version request reinstalls its prepared scope before
        # select_key resumes it. That continuation has no accepted frame left.
        if (target is not None and target == self.target and tab.rendering.accepted is None
                and self.destination is not None and all(
                    getattr(state, name) == getattr(self.destination, name) for name in GEOMETRY_FIELDS)):
            return
        self.content.end("newer_request")
        accepted = tab.rendering.accepted
        if (not allow or self.auto_repeat or not motion_enabled(tab.stage) or not tab.stage.isVisible()
                or volume is None or volume.grid is None or accepted is None or not tab.rendering.is_current()
                or state.layout != tab.state.layout):
            return
        if row.id == tab.state.row_key:
            if volume.id == tab.state.volume_id:
                return
            shared = share_geometry(row, tab.volume(), volume, tab.state, state)
            if not isinstance(shared, SharedGeometry) or any(
                getattr(tab.state, name) != getattr(state, name) for name in GEOMETRY_FIELDS
            ):
                return
        self.target = target
        self.destination = state
        self.generation = None
        # Exclude live annotations from both the outgoing picture and the live
        # panes below it. The primitive alone owns the snapshot and all exits.
        tab.stage.set_annotations_visible(False)
        self.content.replace(lambda: None)
        if not self.content.in_progress:
            tab.stage.set_annotations_visible(True)
            self._ended("cut")

    def requested(self, job: RenderJob) -> None:
        if self.committing_layout or not self.content.in_progress:
            return
        if self.generation is not None or self.target != (job.row_key, job.volume.id):
            self.content.end("newer_request")
        else:
            self.generation = job.generation

    def requesting(self) -> None:
        """Retire an already issued request before even the render debounce."""
        if not self.committing_layout and (self.generation is not None or self.panes is not None or self.focus is not None):
            self.content.end("newer_request")

    def published(self, job: RenderJob) -> None:
        if job.generation == self.generation:
            if self.content.state == "holding":
                self.content.begin()
            else:
                self.content.release()

    def dispose(self) -> None:
        self.content.end("closed")
        self.detach()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        kind = event.type()
        if kind in (QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride):
            self.auto_repeat = event.isAutoRepeat()
        elif kind == QEvent.Type.KeyRelease and not event.isAutoRepeat():
            self.auto_repeat = False
        elif kind in (QEvent.Type.FocusOut, QEvent.Type.WindowDeactivate):
            self.auto_repeat = False
        return False
