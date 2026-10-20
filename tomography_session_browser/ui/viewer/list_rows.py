"""List rows for the image viewer."""
from __future__ import annotations
from pathlib import Path
from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem
from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.ui.list_decorations import paint_selection_marker
from tomography_session_browser.ui.motion import ItemHoverAnimator, blend
from tomography_session_browser.services.atlas_marker_style import LEAF_STATUS_WIDTH_DP
from tomography_session_browser.ui.theme import current_palette
from tomography_session_browser.presentation.design_tokens import NORMAL_STATUS_GLYPHS, RADIUS_CONTROL, SPACE_M, SPACE_S, SPACE_XS, STATUS_TONE_COLOURS, STATUS_TONE_FILL_ALPHA, TYPE_BODY, TYPE_CAPTION
from tomography_session_browser.ui.viewer.group_rows import group_row_size, paint_group_row
from tomography_session_browser.ui.viewer.constants import VIEWER_ENTRY_ROLE, LIST_PRIMARY_ROLE, LIST_ROW_HEIGHT_PX, LIST_ROW_SINGLE_HEIGHT_PX, LIST_ROW_NAME_HEIGHT_PX, LIST_ROW_SUMMARY_HEIGHT_PX, LIST_STATUS_ROLE, LIST_SUMMARY_ROLE, SHARED_NAME_PREFIX_PROPERTY


def fit_list_name(name: str, metrics: QFontMetrics, width: int, shared_prefix: str = "") -> str:
    """Fit a list-row name into ``width``, keeping the part that tells rows apart.

    Middle-elision alone wasted the kept characters on the prefix every row
    shares: every search-map row read ``Searc…21641``. When a name does not
    fit and starts with the list's shared prefix, that prefix gives way first
    (``…20260320_021641``); the full name stays in the tooltip and the
    context header. Display only.
    """

    if metrics.horizontalAdvance(name) <= width:
        return name
    if shared_prefix and name.startswith(shared_prefix):
        remainder = name[len(shared_prefix):]
        # The leading "…" is the only sign that the shared prefix was dropped.
        if metrics.horizontalAdvance("…" + remainder) <= width:
            return "…" + remainder
        # Past the shared prefix, the tail is what differs between rows (for
        # acquisitions it is the time), so keep the tail: "…320_021641".
        return metrics.elidedText(remainder, Qt.TextElideMode.ElideLeft, width)
    return metrics.elidedText(name, Qt.TextElideMode.ElideMiddle, width)


def list_name_top(rect: QRect, *, has_summary: bool) -> int:
    """Where a row's name is drawn: at the top, over its summary line, or
    centred when it has no summary (a complete row is just its name and glyph).
    Single-line names are centred in their compact row."""

    if has_summary:
        return rect.top() + SPACE_S
    return rect.center().y() - LIST_ROW_NAME_HEIGHT_PX // 2


class ViewerListDelegate(QStyledItemDelegate):
    hover_animator: ItemHoverAnimator | None = None

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # noqa: N802 - Qt API
        entry = index.data(VIEWER_ENTRY_ROLE)
        width = super().sizeHint(option, index).width()
        if entry is not None:
            return group_row_size(entry, width)
        height = LIST_ROW_HEIGHT_PX if index.data(LIST_SUMMARY_ROLE) else LIST_ROW_SINGLE_HEIGHT_PX
        return QSize(width, height)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:  # noqa: N802 - Qt API
        entry = index.data(VIEWER_ENTRY_ROLE)
        if entry is not None:
            paint_group_row(painter, option, index, entry)
            return

        theme = current_palette()
        primary = str(index.data(LIST_PRIMARY_ROLE) or index.data(Qt.ItemDataRole.DisplayRole) or "")
        summary = str(index.data(LIST_SUMMARY_ROLE) or "")
        status = str(index.data(LIST_STATUS_ROLE) or "")

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = option.rect.adjusted(0, 1, 0, -1)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        # Hover fades in and out (plan F4) instead of snapping between fills.
        hover = self.hover_animator.progress(index) if self.hover_animator is not None else float(hovered)
        fill = (
            QColor(theme.selection)
            if selected
            else blend(QColor(theme.panel), QColor(theme.surface_alt), hover)
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(rect.adjusted(2, 1, -2, -1), RADIUS_CONTROL, RADIUS_CONTROL)
        if selected:
            # The selection fill is ~1.2:1 against the panel; the accent bar
            # is what actually makes the current row findable.
            paint_selection_marker(painter, rect.adjusted(2, 0, 0, 0), theme)

        painter.setPen(QPen(QColor(theme.border), 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())

        left = rect.left() + SPACE_M
        decoration = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(decoration, QIcon) and not decoration.isNull():
            icon_size = 16
            icon_rect = QRect(
                left,
                rect.center().y() - icon_size // 2,
                icon_size,
                icon_size,
            )
            painter.drawPixmap(
                icon_rect,
                decoration.pixmap(QSize(icon_size, icon_size)),
            )
            left += icon_size + SPACE_S
        right = rect.right() - SPACE_M
        top = list_name_top(rect, has_summary=bool(summary))

        badge_font = QFont(option.font)
        badge_font.setPointSizeF(TYPE_CAPTION)
        painter.setFont(badge_font)
        mark = list_status_mark(status)
        mark_rect = list_status_mark_rect(status, right, rect.center().y(), painter.fontMetrics())

        text_right = mark_rect.left() - (SPACE_S if mark == "glyph" else SPACE_M) if mark else right
        text_width = max(int(text_right - left), 16)

        name_font = QFont(option.font)
        name_font.setBold(True)
        name_font.setPointSizeF(TYPE_BODY)
        painter.setFont(name_font)
        name_metrics = painter.fontMetrics()
        painter.setPen(QColor(theme.selection_text if selected else theme.text_strong))
        prefix = option.widget.property(SHARED_NAME_PREFIX_PROPERTY) if option.widget is not None else None
        painter.drawText(
            QRectF(left, top, text_width, LIST_ROW_NAME_HEIGHT_PX),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            fit_list_name(primary, name_metrics, text_width, str(prefix or "")),
        )

        summary_font = QFont(option.font)
        summary_font.setPointSizeF(TYPE_CAPTION)
        painter.setFont(summary_font)
        summary_metrics = painter.fontMetrics()
        painter.setPen(QColor(theme.text_muted))
        painter.drawText(
            QRectF(
                left,
                top + LIST_ROW_NAME_HEIGHT_PX + SPACE_XS,
                text_width,
                LIST_ROW_SUMMARY_HEIGHT_PX,
            ),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            summary_metrics.elidedText(summary, Qt.TextElideMode.ElideRight, text_width),
        )

        if mark == "glyph":
            paint_list_status_glyph(painter, mark_rect, status)
        elif mark == "pill":
            semantic_color, bg = status_badge_colors(status)
            painter.setPen(QPen(semantic_color, 1))
            painter.setBrush(bg)
            painter.drawRoundedRect(mark_rect, mark_rect.height() / 2, mark_rect.height() / 2)
            painter.setPen(QColor(theme.text_strong))
            painter.setFont(badge_font)
            draw_centered_badge_text(painter, mark_rect, status)

        painter.restore()


LIST_STATUS_GLYPH_PX = 14


LIST_STATUS_PILL_MIN_PX = 54


LIST_STATUS_PILL_HEIGHT_PX = 26


def list_status_mark(status: str) -> str:
    """How a list row shows its status word: "glyph", "pill", or "" (none)."""

    if not status:
        return ""
    return "glyph" if status.strip().lower() in NORMAL_STATUS_GLYPHS else "pill"


def list_status_mark_rect(status: str, right: float, centre_y: float, metrics: QFontMetrics) -> QRectF:
    """Where a row's glyph or pill sits, flush with ``right``."""

    mark = list_status_mark(status)
    if mark == "glyph":
        size = LIST_STATUS_GLYPH_PX
        return QRectF(right - size, centre_y - size / 2, size, size)
    if mark == "pill":
        width = max(LIST_STATUS_PILL_MIN_PX, metrics.horizontalAdvance(status) + 22)
        height = LIST_STATUS_PILL_HEIGHT_PX
        return QRectF(right - width, centre_y - height / 2, width, height)
    return QRectF(right, centre_y, 0, 0)


def paint_list_status_glyph(painter: QPainter, rect: QRectF, status: str) -> None:
    """V1's glyph for a normal status, in the colour its pill would have had."""

    colour, _fill = status_badge_colors(status)
    centre = rect.center()
    # As the Atlas leaf: a ring, and for a disc a fill just inside it.
    ring = rect.width() / 2.0 - 1.0
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(colour, LEAF_STATUS_WIDTH_DP))
    painter.drawEllipse(centre, ring, ring)
    if NORMAL_STATUS_GLYPHS[status.strip().lower()] == "disc":
        fill = ring - 1.6
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colour)
        painter.drawEllipse(centre, fill, fill)
    painter.restore()


def draw_centered_badge_text(painter: QPainter, rect: QRectF, text: str) -> None:
    """Draw badge text with an explicit baseline so it is visually centered.

    Qt's rectangle-based AlignCenter can sit a touch high or low depending on
    the active font and platform metrics. These status pills are small enough
    that the drift is noticeable, so centre the actual text bounds inside the
    badge background.
    """

    metrics = painter.fontMetrics()
    available_width = max(1, int(rect.width() - 14))
    elided = metrics.elidedText(text, Qt.TextElideMode.ElideRight, available_width)
    text_width = metrics.horizontalAdvance(elided)
    x = rect.center().x() - (text_width / 2)
    baseline = rect.center().y() + ((metrics.ascent() - metrics.descent()) / 2)
    painter.drawText(QPointF(x, baseline), elided)


def status_tone(status: str) -> str:
    """The ``design_tokens.STATUS_TONE_COLOURS`` tone a status word is drawn in."""

    raw = status.strip().lower()
    if raw in {"done", "ok", "acquired", "complete", "completed", "available", "with acquisitions", "no acquisitions"}:
        return "good"
    if raw in {"partial", "incomplete", "warning", "warn"}:
        return "warn"
    if raw in {"failed", "fail", "error", "missing"}:
        return "bad"
    if raw in {"queued", "pending", "selected"}:
        return "info"
    return "neutral"


def status_chip_tone(word: str) -> str | None:
    """The header status pill's tone for ``word``, or None for a word the
    header would not draw as a status. The context panel draws its
    ``Status`` row with this, so the two pills agree (harmonisation C1)."""

    return status_tone(word) if is_status_chip(word) else None


def status_badge_colors(status: str) -> tuple[QColor, QColor]:
    fg = QColor(getattr(current_palette(), STATUS_TONE_COLOURS[status_tone(status)]))
    bg = QColor(fg)
    bg.setAlpha(STATUS_TONE_FILL_ALPHA)
    return fg, bg


STATUS_CHIP_TOOLTIPS = {
    "acquired": "Acquisition completed for this position.",
    "done": "Acquisition completed.",
    "complete": "Every planned image was acquired.",
    "completed": "Every planned image was acquired.",
    "available": "The source file is present and readable.",
    "partial": "Fewer images than expected were acquired.",
    "incomplete": "Fewer images than expected were acquired.",
    "warning": "Acquired, but the metadata reports a problem.",
    "warn": "Acquired, but the metadata reports a problem.",
    "failed": "Acquisition stopped before a usable series was produced.",
    "fail": "Acquisition stopped before a usable series was produced.",
    "error": "The source data could not be interpreted.",
    "missing": "The expected source file was not found.",
    "empty": "No images are associated with this item.",
    "queued": "Planned but not yet acquired.",
    "pending": "Planned but not yet acquired.",
    "unknown": "There is not enough metadata to classify this item.",
}


def status_chip_tooltip(value: str) -> str:
    normalized = value.strip().lower()
    explanation = STATUS_CHIP_TOOLTIPS.get(normalized)
    return f"{value} — {explanation}" if explanation else value


def is_status_chip(value: str) -> bool:
    normalized = value.strip().lower()
    return normalized in {
        "acquired",
        "done",
        "complete",
        "completed",
        "partial",
        "incomplete",
        "warning",
        "warn",
        "failed",
        "fail",
        "error",
        "missing",
        "empty",
        "unknown",
        # The other words ``display_status_label`` gives the header chip.
        "available",
        "pending",
    }


def warning_count_text(warnings: list[str]) -> str:
    if not warnings:
        return ""
    return f" · {count_phrase(len(warnings), 'warning')}"


def warning_tooltip(warnings: list[str]) -> str:
    if not warnings:
        return ""
    return "\nWarnings:\n" + "\n".join(f"- {warning}" for warning in warnings)


def path_size_bytes(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0

