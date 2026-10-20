"""A bounded atlas preview that uses the space between a card's labels."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QSizePolicy, QWidget

from tomography_session_browser.ui.widgets.atlas_thumbnail import AtlasThumbnail


ATLAS_PREVIEW_MAX_SIDE = 160


class GrowingAtlasPreview(QWidget):
    """Resize the picture without growing the card's minimum or decoding again."""

    def __init__(self, path: str | Path | None, *, description: str) -> None:
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.picture = AtlasThumbnail(
            path, QSize(80, 80), request_side=ATLAS_PREVIEW_MAX_SIDE,
            description=description, fit="contain", parent=self,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(80, 80)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(48, 48)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        side = max(1, min(self.width(), self.height(), ATLAS_PREVIEW_MAX_SIDE))
        self.picture.set_display_size(QSize(side, side))
        self.picture.move((self.width() - side) // 2, 0)
