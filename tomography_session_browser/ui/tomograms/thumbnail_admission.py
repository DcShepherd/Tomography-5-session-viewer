"""Viewport admission only; decoding and cache ownership stay in PreviewScheduler."""
from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, QTimer, Slot
from PySide6.QtWidgets import QWidget

from tomography_session_browser.ui.viewer.preview_scheduler import preview_scheduler
from tomography_session_browser.ui.widgets.atlas_thumbnail import AtlasThumbnail


class CaptureThumbnailAdmission(QObject):
    def __init__(self, host: QWidget, visible: Callable[[], tuple[AtlasThumbnail, ...]],
                 viewport: QWidget | None = None) -> None:
        super().__init__(host)
        self.host, self.visible = host, visible
        self.clients: tuple[AtlasThumbnail, ...] = ()
        self.filling = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.fill)
        host.installEventFilter(self)
        if viewport is not None:
            viewport.installEventFilter(self)
        preview_scheduler().thumbnail_room.connect(self.room)

    def set_clients(self, clients: tuple[AtlasThumbnail, ...]) -> None:
        self.stop()
        for picture in self.clients:
            picture.removeEventFilter(self)
            picture.parentWidget().removeEventFilter(self)
        self.clients = clients
        for picture in clients:
            picture.installEventFilter(self)
            picture.parentWidget().installEventFilter(self)
        self.schedule()

    def stop(self) -> None:
        self.timer.stop()
        for picture in self.clients:
            picture.cancel()

    @Slot()
    def schedule(self) -> None:
        if self.host.isVisible() and self.clients and not self.timer.isActive():
            self.timer.start(0)

    @Slot()
    def room(self) -> None:
        # Starting a decode announces room synchronously. The active fill
        # already handles that room; re-entering would resubmit its clients.
        if not self.filling:
            self.schedule()

    @Slot()
    def fill(self) -> None:
        if self.filling or not self.host.isVisible():
            return
        self.filling = True
        try:
            visible = self.visible()
            eligible = set(visible)
            for picture in self.clients:
                if picture not in eligible and picture.is_requested():
                    picture.cancel()
            urgent = True
            for picture in visible:
                if picture.needs_request():
                    if not picture.request(urgent=urgent):
                        break
                    urgent = False
        finally:
            self.filling = False

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self.host and event.type() == QEvent.Type.Hide:
            self.stop()
        elif event.type() in (QEvent.Type.Show, QEvent.Type.Move, QEvent.Type.Resize,
                              QEvent.Type.LayoutRequest, QEvent.Type.DevicePixelRatioChange):
            self.schedule()
        return super().eventFilter(watched, event)
