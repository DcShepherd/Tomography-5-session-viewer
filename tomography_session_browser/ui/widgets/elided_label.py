from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QFontMetrics, QPainter, QResizeEvent, QTextLayout, QTextOption
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget


class ElidedLabel(QLabel):
    """Single-line label that elides text to its current width.

    Qt labels do not elide automatically. This small helper keeps long
    session names, paths, and metadata values from pushing adjacent controls
    out of alignment while preserving the full value in a tooltip.

    ``prefer_full_width``: report the *full* text's width as the size hint, so
    a layout with room to spare gives the label its whole value. By default
    the hint follows the elided rendering, which is self-reinforcing: once
    elided, a label never asks for more room again, even when a trailing
    stretch is soaking up hundreds of free pixels beside it. It still shrinks
    and elides when space genuinely runs out.
    """

    def __init__(
        self,
        text: str = "",
        *,
        mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight,
        parent: QWidget | None = None,
        tooltip: bool = True,
        prefer_full_width: bool = False,
    ) -> None:
        super().__init__(parent)
        self._mode = mode
        self._full_text = ""
        self._sync_tooltip = tooltip
        self._prefer_full_width = prefer_full_width
        self.setWordWrap(False)
        self.setMinimumWidth(24)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt API
        self._full_text = str(text or "")
        if self._sync_tooltip:
            self.setToolTip(self._full_text)
        self._apply_elision()
        if self._prefer_full_width:
            self.updateGeometry()

    def text_full(self) -> str:
        return self._full_text

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        hint = super().sizeHint()
        if self._prefer_full_width:
            margins = self.contentsMargins()
            full = QFontMetrics(self.font()).horizontalAdvance(self._full_text)
            hint.setWidth(full + margins.left() + margins.right() + 6)
        return hint

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._apply_elision()

    def _apply_elision(self) -> None:
        metrics = QFontMetrics(self.font())
        width = max(self.width() - 4, 12)
        super().setText(metrics.elidedText(self._full_text, self._mode, width))


class WrappedElidedLabel(QLabel):
    """Word-wraps onto at most ``max_lines`` lines, eliding only the last one.

    For inline explanations that must be readable without hovering, such as
    the context header's unavailable-action reasons, but must not grow a strip
    without bound. A single-line elided label cut them off after the first
    reason. ``text()`` and :meth:`text_full` return the complete text, so
    accessibility and callers see everything.
    """

    def __init__(self, text: str = "", *, max_lines: int = 2, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._max_lines = max(1, int(max_lines))
        self._full_text = ""
        self.setWordWrap(False)
        self.setMinimumWidth(24)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt API
        self._full_text = str(text or "")
        super().setText(self._full_text)
        self.updateGeometry()
        self.update()

    def text_full(self) -> str:
        return self._full_text

    def visible_lines(self, width: int | None = None) -> list[str]:
        """The lines that are drawn at ``width`` (default: current width)."""

        margins = self.contentsMargins()
        available = (self.width() if width is None else width) - margins.left() - margins.right() - 2
        return self._wrap(max(available, 12))

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt API
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt API
        margins = self.contentsMargins()
        lines = max(1, len(self.visible_lines(width)))
        return lines * QFontMetrics(self.font()).lineSpacing() + margins.top() + margins.bottom()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        margins = self.contentsMargins()
        metrics = QFontMetrics(self.font())
        return QSize(
            metrics.horizontalAdvance(self._full_text) + margins.left() + margins.right() + 6,
            self.heightForWidth(self.width()) if self.width() > 0 else metrics.lineSpacing(),
        )

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        margins = self.contentsMargins()
        return QSize(24, QFontMetrics(self.font()).lineSpacing() + margins.top() + margins.bottom())

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        try:
            painter.setFont(self.font())
            painter.setPen(self.palette().color(self.foregroundRole()))
            rect = self.contentsRect()
            line_height = QFontMetrics(self.font()).lineSpacing()
            top = rect.top()
            for line in self.visible_lines():
                painter.drawText(
                    QRect(rect.left(), top, rect.width(), line_height),
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                    line,
                )
                top += line_height
        finally:
            painter.end()

    def _wrap(self, width: int) -> list[str]:
        text = self._full_text
        if not text:
            return []
        layout = QTextLayout(text, self.font())
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WordWrap)
        layout.setTextOption(option)
        layout.beginLayout()
        spans: list[tuple[int, int]] = []
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            spans.append((line.textStart(), line.textLength()))
        layout.endLayout()
        metrics = QFontMetrics(self.font())
        lines: list[str] = []
        for index, (start, length) in enumerate(spans[: self._max_lines]):
            if index == self._max_lines - 1 and len(spans) > self._max_lines:
                lines.append(metrics.elidedText(text[start:].strip(), Qt.TextElideMode.ElideRight, width))
            else:
                lines.append(text[start : start + length].rstrip())
        return lines
