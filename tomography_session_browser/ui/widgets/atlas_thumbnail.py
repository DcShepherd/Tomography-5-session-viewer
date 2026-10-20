"""A small picture of an atlas, fetched on the scheduler's thumbnail lane.

Used by the sample summary's atlas card and, from the next batch, by the atlas
session's contact sheet. The widget draws a placeholder at once and the image
when it arrives; it never decodes anything itself.

A request is made when the widget is first shown, not when it is built: a page
that is built under the loading overlay or off screen costs nothing. The
request asks for the widget's size times the device pixel ratio, capped, and a
result is applied only if it answers the widget's latest request. Destroying
the widget (a dashboard rebuild does) cancels its request.
"""
from __future__ import annotations

import math
from pathlib import Path
import weakref
from typing import Literal

from PySide6.QtCore import QRect, QSize, Qt, Slot
from PySide6.QtGui import QImage, QPainter, QPainterPath, QPaintEvent, QPen, QShowEvent, QColor
from PySide6.QtWidgets import QWidget

from tomography_session_browser.ui.theme import current_palette
from tomography_session_browser.ui.viewer.preview_cache import CachedPreview
from tomography_session_browser.ui.viewer.preview_scheduler import (
    PreviewKey,
    cached_thumbnail,
    forget_thumbnail_client,
    preview_scheduler,
)

#: The longest side a thumbnail is ever decoded at, whatever the screen.
THUMBNAIL_MAX_SIDE = 512
RADIUS = 4


def requested_side(logical_side: int, device_pixel_ratio: float) -> int:
    """The decode size for a picture ``logical_side`` px long on this screen."""

    return min(THUMBNAIL_MAX_SIDE, math.ceil(logical_side * max(1.0, device_pixel_ratio)))


class AtlasThumbnail(QWidget):
    """``path``'s picture at ``size``, rounded and cover-fitted by default.

    ``request_side`` is the longest side to ask the decoder for, in logical
    pixels; it defaults to the widget's own. ``fit="contain"`` shows the whole
    image with its original aspect ratio inside that rectangle.
    """

    def __init__(
        self,
        path: str | Path | None,
        size: QSize,
        *,
        request_side: int | None = None,
        description: str = "",
        placeholder: str = "No image",
        auto_request: bool = True,
        fit: Literal["cover", "contain"] = "cover",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._path = Path(path) if path else None
        self._request_side = request_side or max(size.width(), size.height())
        self._placeholder = placeholder
        self._fit = fit
        # A contact sheet paces its tiles' requests itself, so it turns this off.
        self._auto_request = auto_request
        self._image = QImage()
        self._failed = self._path is None
        self._requested = False
        self._token = 0
        # The scheduler knows this widget only as this key, so a destroyed
        # widget can be cancelled without a reference back to it.
        self._client = object()
        self.destroyed.connect(lambda *_, client=self._client: forget_thumbnail_client(client))
        self.setFixedSize(size)
        self.setObjectName("atlasThumbnail")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAccessibleName(description or "Atlas picture")
        self.setToolTip(description)
        if self._auto_request:
            preview_scheduler().thumbnail_room.connect(self._retry_when_room)

    # ----------------------------------------------------------------- state

    def is_loaded(self) -> bool:
        return not self._image.isNull()

    def has_failed(self) -> bool:
        """No picture to show: no path, or it could not be read."""

        return self._failed

    def thumbnail_image(self) -> QImage:
        return self._image

    def is_requested(self) -> bool:
        """A request is out and no answer has come."""

        return self._requested

    def needs_request(self) -> bool:
        """A picture to fetch that no request is already out for."""

        return self._path is not None and not self.is_loaded() and not self._failed and not self._requested

    def set_display_size(self, size: QSize) -> None:
        """Resize in place (a sheet's tiles follow its width); what was asked of
        the decoder stays as asked."""

        if size != self.size():
            self.setFixedSize(size)
            self.update()

    # --------------------------------------------------------------- request

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt signature
        super().showEvent(event)
        if self._auto_request:
            self.request()

    def request(self, *, urgent: bool = False) -> bool:
        """Ask for the picture; False when the lane's queue was full (repeat later).

        Asking again while a request is out re-sends it, which is how a tile
        scrolled into view (``urgent``) moves to the front.
        """

        if self._path is None or self.is_loaded() or self._failed:
            return True
        key: PreviewKey = (self._path, 0, requested_side(self._request_side, self.devicePixelRatioF()))
        cached = cached_thumbnail(key)
        if cached is not None:
            self._apply(cached)
            return True
        self._token += 1
        me = weakref.ref(self)
        token = self._token

        def receive(preview: CachedPreview) -> None:
            widget = me()
            if widget is not None:
                widget._received(token, preview)

        # Starting a task announces queue room synchronously. Mark this request
        # before that signal so the automatic retry cannot re-enter it.
        self._requested = True
        self._requested = preview_scheduler().request_thumbnail(key, client=self._client, receive=receive, urgent=urgent)
        return self._requested

    @Slot()
    def _retry_when_room(self) -> None:
        if self.isVisible() and self.needs_request():
            self.request()

    def cancel(self) -> None:
        """Withdraw the request, and answer none that is already on its way."""

        self._token += 1
        self._requested = False
        forget_thumbnail_client(self._client)

    def _received(self, token: int, preview: CachedPreview) -> None:
        if token != self._token:
            return
        self._apply(preview)

    def _apply(self, preview: CachedPreview) -> None:
        self._requested = False
        if preview.image.isNull():
            self._failed = True
            self.setToolTip(preview.warnings[0] if preview.warnings else self.toolTip())
        else:
            self._image = preview.image
            self._failed = False
        self.update()

    # ----------------------------------------------------------------- paint

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt signature
        palette = current_palette()
        rect = QRect(0, 0, self.width(), self.height())
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            clip = QPainterPath()
            clip.addRoundedRect(rect.adjusted(0, 0, -1, -1).toRectF(), RADIUS, RADIUS)
            painter.setClipPath(clip)
            painter.fillRect(rect, QColor(palette.canvas))
            if self.is_loaded():
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                if self._fit == "contain":
                    size = self._image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
                    target = QRect((self.width() - size.width()) // 2,
                                   (self.height() - size.height()) // 2, size.width(), size.height())
                    painter.drawImage(target, self._image)
                else:
                    painter.drawImage(rect, self._image, self._cover_source())
            elif self._failed:
                painter.setPen(QColor(palette.text_muted))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self._placeholder)
            painter.setClipping(False)
            painter.setPen(QPen(QColor(palette.border), 1))
            painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), RADIUS, RADIUS)
        finally:
            painter.end()

    def _cover_source(self) -> QRect:
        """The part of the picture that fills the widget (``object-fit: cover``)."""

        image = self._image.size()
        target = self.size()
        scale = max(target.width() / image.width(), target.height() / image.height())
        width = min(image.width(), round(target.width() / scale))
        height = min(image.height(), round(target.height() / scale))
        return QRect((image.width() - width) // 2, (image.height() - height) // 2, width, height)
