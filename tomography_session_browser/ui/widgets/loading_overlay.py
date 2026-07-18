from __future__ import annotations

import math
import time

from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget


class LoadingOverlay(QWidget):
    """Prominent theme-aware loading overlay with a five-dot wave animation."""

    hidden = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._message = "Loading session..."
        self._detail = "Please wait while the session is prepared."
        self._phase = 0.0
        self._shown_at = 0.0
        self._minimum_visible_ms = 350
        self._fade_duration_ms = 220
        self._fade_animation: QPropertyAnimation | None = None
        self._opacity_effect: QGraphicsOpacityEffect | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(42)
        self._timer.timeout.connect(self._advance)
        self._last_tick_at = 0.0
        self._tick_intervals_ms: list[float] = []
        self._dropped_frames = 0
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Loading overlay")
        self.setAccessibleDescription(
            "Shows session loading progress while metadata and interface models are prepared."
        )
        self.hide()

    def show_loading(self, message: str, detail: str | None = None) -> None:
        self._cancel_fade()
        self._message = message
        if detail is not None:
            self._detail = detail
        if self.parentWidget() is not None:
            self.setGeometry(self.parentWidget().rect())
        self._shown_at = time.monotonic()
        self._reset_timing()
        self.show()
        self.raise_()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        if not self._timer.isActive():
            self._timer.start()
        self.update()

    def update_loading_message(self, message: str, detail: str | None = None) -> None:
        self._message = message
        if detail is not None:
            self._detail = detail
        if self.isVisible():
            self.raise_()
            self.update()

    def hide_loading(self, *, fade: bool = True) -> None:
        if not self.isVisible():
            self._timer.stop()
            return
        if not fade:
            self._cancel_fade()
            self._timer.stop()
            self.hide()
            self.hidden.emit()
            return
        elapsed_ms = int((time.monotonic() - self._shown_at) * 1000)
        remaining_ms = self._minimum_visible_ms - elapsed_ms
        if remaining_ms > 0:
            QTimer.singleShot(remaining_ms, lambda: self.hide_loading(fade=True))
            return
        self._fade_out()

    # CamelCase aliases mirror the API named in the user request.
    def showLoading(self, message: str) -> None:  # noqa: N802
        self.show_loading(message)

    def updateLoadingMessage(self, message: str) -> None:  # noqa: N802
        self.update_loading_message(message)

    def hideLoading(self) -> None:  # noqa: N802
        self.hide_loading()

    def _cancel_fade(self) -> None:
        if self._fade_animation is not None:
            self._fade_animation.stop()
            self._fade_animation.deleteLater()
            self._fade_animation = None
        if self._opacity_effect is not None:
            if self.graphicsEffect() is self._opacity_effect:
                self.setGraphicsEffect(None)
            self._opacity_effect = None

    def _fade_out(self) -> None:
        if self._fade_animation is not None:
            return
        effect = QGraphicsOpacityEffect(self)
        effect.setOpacity(1.0)
        self.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(self._fade_duration_ms)
        animation.setStartValue(1.0)
        animation.setEndValue(0.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._opacity_effect = effect
        self._fade_animation = animation

        def finish() -> None:
            self._timer.stop()
            self.hide()
            if self.graphicsEffect() is effect:
                self.setGraphicsEffect(None)
            self._fade_animation = None
            self._opacity_effect = None
            animation.deleteLater()
            self.hidden.emit()

        animation.finished.connect(finish)
        animation.start()

    def _advance(self) -> None:
        now = time.monotonic()
        if self._last_tick_at:
            interval_ms = (now - self._last_tick_at) * 1000
            self._tick_intervals_ms.append(interval_ms)
            expected_ms = max(1, self._timer.interval())
            if interval_ms > expected_ms * 2.5:
                self._dropped_frames += max(1, int(interval_ms // expected_ms) - 1)
        self._last_tick_at = now
        self._phase = (self._phase + 0.12) % (math.pi * 2.0)
        self.update()

    def performance_snapshot(self) -> dict[str, float | int]:
        intervals = list(self._tick_intervals_ms)
        intervals.sort()
        count = len(intervals)
        median = intervals[count // 2] if count else 0.0
        maximum = intervals[-1] if count else 0.0
        return {
            "target_interval_ms": self._timer.interval(),
            "tick_count": count,
            "median_interval_ms": round(median, 1),
            "max_interval_ms": round(maximum, 1),
            "dropped_frames": self._dropped_frames,
        }

    def _reset_timing(self) -> None:
        self._last_tick_at = 0.0
        self._tick_intervals_ms = []
        self._dropped_frames = 0

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt signature
        from tomography_session_browser.ui.branding import draw_stack_mark
        from tomography_session_browser.ui.theme import MONO_FONT_NAME, SANS_FONT_NAME, current_palette

        palette = current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            scrim = QColor(palette.background)
            scrim.setAlpha(212 if palette.name == "dark" else 176)
            painter.fillRect(self.rect(), scrim)

            panel_width = min(540, max(340, self.width() - 96))
            content_width = max(220, int(panel_width - 64))

            title_font = QFont(SANS_FONT_NAME, 16, QFont.Weight.DemiBold)
            detail_font = QFont(SANS_FONT_NAME, 10)
            footer_font = QFont(MONO_FONT_NAME, 8)

            title_height = _text_height(title_font, content_width, self._message)
            detail_height = _text_height(detail_font, content_width, self._detail, word_wrap=True)
            footer_height = _text_height(footer_font, content_width, "Parsing files and metadata from disk")
            dot_wave_height = 72
            brand_size = 30.0
            brand_gap = 14.0
            top_padding = 28.0
            panel_height = max(
                268,
                int(
                    top_padding
                    + brand_size
                    + brand_gap
                    + title_height
                    + 12
                    + detail_height
                    + 26
                    + dot_wave_height
                    + 20
                    + footer_height
                    + 28
                ),
            )
            panel = QRectF(
                (self.width() - panel_width) / 2,
                (self.height() - panel_height) / 2,
                panel_width,
                panel_height,
            )
            painter.setPen(QPen(QColor(palette.border_strong), 1.0))
            painter.setBrush(QColor(palette.surface))
            painter.drawRoundedRect(panel, 10, 10)

            brand_rect = QRectF(
                panel.center().x() - brand_size / 2,
                panel.top() + top_padding,
                brand_size,
                brand_size,
            )
            draw_stack_mark(painter, brand_rect, palette.name)

            painter.setFont(title_font)
            painter.setPen(QColor(palette.text_strong))
            title_rect = QRectF(
                panel.left() + 32,
                brand_rect.bottom() + brand_gap,
                content_width,
                title_height,
            )
            painter.drawText(
                title_rect,
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                self._message,
            )

            painter.setFont(detail_font)
            painter.setPen(QColor(palette.text_muted))
            detail_rect = QRectF(
                panel.left() + 32,
                title_rect.bottom() + 10,
                content_width,
                detail_height,
            )
            painter.drawText(
                detail_rect,
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                self._detail,
            )

            # Loader #10: five dots arranged horizontally, translating
            # vertically in a staggered wave. The dots use the current text
            # colour so the animation tracks light/dark themes.
            dot_color = QColor(palette.accent)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(dot_color)
            dot_radius = 7.0
            spacing = 31.0
            base_x = panel.center().x() - spacing * 2
            base_y = detail_rect.bottom() + 48
            for index in range(5):
                phase = self._phase - index * 0.62
                y = base_y + math.sin(phase) * 23.0
                scale = 0.86 + 0.14 * (math.sin(phase) + 1.0) / 2.0
                painter.drawEllipse(QPointF(base_x + spacing * index, y), dot_radius * scale, dot_radius * scale)

            painter.setFont(footer_font)
            painter.setPen(QColor(palette.text_muted))
            footer = QRectF(panel.left() + 32, panel.bottom() - 26 - footer_height, content_width, footer_height)
            painter.drawText(footer, Qt.AlignmentFlag.AlignCenter, "Parsing files and metadata from disk")
        finally:
            painter.end()


def _text_height(font: QFont, width: int, text: str, *, word_wrap: bool = False) -> int:
    """Return a conservative text height for custom-painted overlay labels."""

    from PySide6.QtGui import QFontMetrics

    metrics = QFontMetrics(font)
    flags = Qt.AlignmentFlag.AlignCenter
    if word_wrap:
        flags |= Qt.TextFlag.TextWordWrap
    bounds = metrics.boundingRect(QRect(0, 0, width, 10_000), flags, text or " ")
    return max(metrics.lineSpacing(), bounds.height()) + 4
