"""Scale bar for the image viewer."""
from __future__ import annotations
import math
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget
from tomography_session_browser.domain.units import ANGSTROM, MICROMETRE, NANOMETRE
from tomography_session_browser.services.atlas_marker_style import MARKER_INK
from tomography_session_browser.presentation.design_tokens import CANVAS_CHROME, MONO_FONT_NAME
from tomography_session_browser.ui.viewer.constants import MAX_SCALE_BAR_SCREEN_PX, MIN_SCALE_BAR_SCREEN_PX


def nice_scale_length_meters(target_meters: float) -> float | None:
    if target_meters <= 0 or not math.isfinite(target_meters):
        return None
    exponent = math.floor(math.log10(target_meters))
    candidates = [
        multiplier * (10**power)
        for power in range(exponent - 1, exponent + 2)
        for multiplier in (1, 2, 5)
    ]
    candidates = [value for value in candidates if value > 0]
    return min(candidates, key=lambda value: abs(math.log10(value / target_meters)))


def format_pixel_size_label(value_meters: float | None) -> str:
    return format_scale_length_label(value_meters) if value_meters is not None else "unknown"


def format_scale_length_label(value_meters: float) -> str:
    angstrom = value_meters * 1e10
    if angstrom < 100:
        return f"{format_scale_number(angstrom)} {ANGSTROM}"
    nm = value_meters * 1e9
    if nm < 1000:
        return f"{format_scale_number(nm)} {NANOMETRE}"
    um = value_meters * 1e6
    if um < 1000:
        return f"{format_scale_number(um)} {MICROMETRE}"
    return f"{format_scale_number(value_meters * 1e3)} mm"


def format_scale_number(value: float) -> str:
    if value >= 100:
        return f"{value:.0f}"
    if value >= 10:
        return f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{value:.2f}".rstrip("0").rstrip(".")


class ScaleBarWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._length_px = 0.0
        self._label = ""
        self.setObjectName("viewerScaleBar")
        self.setFixedSize(round(MAX_SCALE_BAR_SCREEN_PX + 8), 34)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setToolTip(
            "Scale bar with a snapped physical-distance label."
        )

    def set_scale(self, length_px: float, label: str) -> None:
        self._length_px = max(0.0, min(float(length_px), self.width() - 8.0))
        self._label = label
        self.setVisible(
            self._length_px >= MIN_SCALE_BAR_SCREEN_PX and bool(label)
        )
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt signature
        super().paintEvent(event)
        if self._length_px <= 0 or not self._label:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            x0 = 1.5
            y = 27.0
            x1 = x0 + self._length_px
            font = QFont(MONO_FONT_NAME)
            font.setPixelSize(11)
            font.setWeight(QFont.Weight.Medium)
            painter.setFont(font)
            metrics = QFontMetricsF(font)
            plate_width = min(
                self.width() - 2.0,
                max(20.0, metrics.horizontalAdvance(self._label) + 12.0),
            )
            plate = QColor(MARKER_INK)
            plate.setAlphaF(0.88)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(plate)
            painter.drawRoundedRect(
                QRectF(x0 - 1.0, 1.0, plate_width, 16.0),
                3.0,
                3.0,
            )
            painter.setPen(QColor(CANVAS_CHROME.safe_text_light))
            painter.drawText(
                QRectF(x0 + 5.0, 1.0, plate_width - 10.0, 16.0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                self._label,
            )

            bar_path = QPainterPath()
            bar_path.moveTo(x0, y)
            bar_path.lineTo(x1, y)
            bar_path.moveTo(x0, y - 3.5)
            bar_path.lineTo(x0, y + 3.5)
            bar_path.moveTo(x1, y - 3.5)
            bar_path.lineTo(x1, y + 3.5)
            ink_pen = QPen(QColor(MARKER_INK), 3.4)
            ink_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(ink_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(bar_path)
            light_pen = QPen(QColor(CANVAS_CHROME.safe_text_light), 1.4)
            light_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(light_pen)
            painter.drawPath(bar_path)
        finally:
            painter.end()

