"""Support components for the timeline."""
from __future__ import annotations
import re
from math import ceil
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from tomography_session_browser.ui.theme import current_palette


def timeline_count_phrase(count: int, singular: str, plural: str | None = None) -> str:
    noun = singular if count == 1 else (plural or f"{singular}s")
    return f"{count} {noun}"


def group_key(label: str) -> tuple[str, str]:
    text = label.strip()
    stem = re.sub(r"\.[^.]+$", "", text)
    match = re.match(r"([A-Za-z][A-Za-z .-]*?)(?:[_ -]?\d.*)?$", stem)
    if match:
        base = match.group(1).strip(" _-.")
    else:
        base = re.split(r"[_ -]\d", stem, maxsplit=1)[0].strip(" _-.")
    if not base:
        base = stem or text
    pretty = base.replace("_", " ").strip()
    if pretty.isupper():
        label_text = pretty
    else:
        label_text = " ".join(part.capitalize() if part.islower() else part for part in pretty.split())
    return pretty.lower(), label_text


def status_icon(status: str, device_pixel_ratio: float = 1.0) -> QIcon:
    theme = current_palette()
    normalized = (status or "").lower()
    if normalized in {"failed", "missing", "error"}:
        color = QColor(theme.chart_red)
    elif normalized in {"warning", "incomplete"}:
        color = QColor(theme.chart_amber)
    elif normalized in {"neutral", "unknown", "queued"}:
        color = QColor(theme.chart_grey)
    else:
        color = QColor(theme.chart_green)
    dpr = max(1.0, float(device_pixel_ratio))
    pixmap = QPixmap(ceil(12 * dpr), ceil(12 * dpr))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(theme.border_strong), 1))
        painter.setBrush(color)
        painter.drawEllipse(2, 2, 8, 8)
    finally:
        painter.end()
    return QIcon(pixmap)

