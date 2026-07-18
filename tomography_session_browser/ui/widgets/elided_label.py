from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics, QResizeEvent
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget


class ElidedLabel(QLabel):
    """Single-line label that elides text to its current width.

    Qt labels do not elide automatically. This small helper keeps long
    session names, paths, and metadata values from pushing adjacent controls
    out of alignment while preserving the full value in a tooltip.
    """

    def __init__(
        self,
        text: str = "",
        *,
        mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight,
        parent: QWidget | None = None,
        tooltip: bool = True,
    ) -> None:
        super().__init__(parent)
        self._mode = mode
        self._full_text = ""
        self._sync_tooltip = tooltip
        self.setWordWrap(False)
        self.setMinimumWidth(24)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt API
        self._full_text = str(text or "")
        if self._sync_tooltip:
            self.setToolTip(self._full_text)
        self._apply_elision()

    def text_full(self) -> str:
        return self._full_text

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._apply_elision()

    def _apply_elision(self) -> None:
        metrics = QFontMetrics(self.font())
        width = max(self.width() - 4, 12)
        super().setText(metrics.elidedText(self._full_text, self._mode, width))
