"""The movie's parts in one bar: each sweep solid, each hold hatched where it occurs."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from tomography_session_browser.services.movie.timeline import MovieTimeline
from tomography_session_browser.ui.theme import current_palette

#: The bar of board 3: a 6 px strip in a taller target, and the hatching's period.
BAR_HEIGHT, TARGET_HEIGHT, HATCH = 6, 12, 6


def parts(timeline: MovieTimeline) -> tuple[tuple[int, int, str], ...]:
    """(first frame, frame count, "out", "back" or "hold") for each part of the bar."""
    runs: list[tuple[int, int, str]] = []
    for index, frame in enumerate(timeline.frames):
        kind = "hold" if frame.hold else frame.direction
        if runs and runs[-1][2] == kind:
            runs[-1] = (runs[-1][0], runs[-1][1] + 1, kind)
        else:
            runs.append((index, 1, kind))
    return tuple(runs)


class TimelineBar(QWidget):
    moved = Signal(int)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("movieTimelineBar")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(TARGET_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # The scrubber under the bar is the keyboard control; the bar is a picture of it.
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAccessibleName("Movie timeline")
        self.timeline: MovieTimeline | None = None
        self.index = 0

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(320, TARGET_HEIGHT)

    def set_timeline(self, timeline: MovieTimeline | None, index: int) -> None:
        self.timeline, self.index = timeline, index
        if timeline is not None:
            held = sum(count for _, count, kind in parts(timeline) if kind == "hold")
            self.setAccessibleDescription(f"{timeline.count} frames, {held} of them held")
        self.update()

    def track(self) -> QRectF:
        return QRectF(self.rect()).adjusted(0, (self.height() - BAR_HEIGHT) / 2, 0, -(self.height() - BAR_HEIGHT) / 2)

    def paintEvent(self, _event) -> None:  # noqa: N802
        palette = current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = self.track()
        outline = QPainterPath()
        outline.addRoundedRect(track, BAR_HEIGHT / 2, BAR_HEIGHT / 2)
        painter.fillPath(outline, QColor(palette.panel))
        timeline = self.timeline
        if timeline is None or not timeline.count:
            painter.setPen(QPen(QColor(palette.border), 1))
            painter.drawPath(outline)
            return
        painter.setClipPath(outline)
        total = timeline.count
        # The way out is the accent, the way back its stronger shade; holds are hatched where they occur.
        # The scrubber under the bar marks the preview's frame, so the bar draws no position line.
        fill = {"out": QColor(palette.accent), "back": QColor(palette.accent_strong)}
        runs = parts(timeline)
        for number, (start, count, kind) in enumerate(runs):
            x0 = track.left() + track.width() * start / total
            x1 = track.left() + track.width() * (start + count) / total - (number < len(runs) - 1)
            part = QRectF(x0, track.top(), max(1., x1 - x0), track.height())
            if kind != "hold":
                painter.fillRect(part, fill[kind])
                continue
            painter.fillRect(part, QColor(palette.border_strong))
            painter.save()
            painter.setClipRect(part, Qt.ClipOperation.IntersectClip)
            painter.setPen(QPen(QColor(palette.control_border), HATCH / 2))
            height = track.height()
            x = part.left() - height
            while x < part.right() + height:
                painter.drawLine(QPointF(x, track.bottom()), QPointF(x + height, track.top()))
                x += HATCH
            painter.restore()

    def frame_at(self, x: float) -> int:
        track = self.track()
        if self.timeline is None or track.width() <= 0:
            return 0
        fraction = (x - track.left()) / track.width()
        return max(0, min(self.timeline.count - 1, int(fraction * self.timeline.count)))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.timeline is not None:
            self.moved.emit(self.frame_at(event.position().x()))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if event.buttons() & Qt.MouseButton.LeftButton and self.timeline is not None:
            self.moved.emit(self.frame_at(event.position().x()))
            event.accept()
            return
        super().mouseMoveEvent(event)
