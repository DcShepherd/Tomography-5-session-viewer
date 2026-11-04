"""The two rows of a viewer list that show no object: a group's header and a divider (B4.3).

They are rows of the list so the list keeps one scroll, one filter and one
keyboard, but they are not items: a header or divider is neither selectable nor
enabled, and the delegate paints them here instead of as a name, a summary and
a status. Colours come from the palette, so both themes follow the stylesheet.
"""
from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QStyleOptionViewItem, QTreeWidgetItem

from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.presentation.design_tokens import SPACE_M, SPACE_S, STATUS_TONE_COLOURS, TYPE_CAPTION
from tomography_session_browser.presentation.viewer_entries import Divider, Entry, GroupHeader
from tomography_session_browser.ui.theme import current_palette
from tomography_session_browser.ui.viewer.constants import (
    LIST_DIVIDER_HEIGHT_PX,
    LIST_DIVIDER_LABEL_HEIGHT_PX,
    LIST_GROUP_HEADER_HEIGHT_PX,
    LIST_GROUP_SHOWN_ROLE,
    VIEWER_ENTRY_ROLE,
)

#: Where a divider's line stops short of the list's right edge.
_RIGHT_INSET_PX = 10


def group_row_height(entry: Entry) -> int:
    """How tall a header or a divider is; the list is uneven on purpose."""

    if isinstance(entry, GroupHeader):
        return LIST_GROUP_HEADER_HEIGHT_PX
    if isinstance(entry, Divider) and entry.label:
        return LIST_DIVIDER_LABEL_HEIGHT_PX
    return LIST_DIVIDER_HEIGHT_PX


def group_row_size(entry: Entry, width: int) -> QSize:
    return QSize(width, group_row_height(entry))


def header_count_text(header: GroupHeader, shown: int) -> str:
    """"21", or "5 exposures" for a header that counts a noun."""

    return count_phrase(shown, header.noun) if header.noun else str(shown)


def header_failed_text(failed: int) -> str:
    """"3 failed", or nothing. A list already filtered to Failed passes 0: it says so once."""

    return f"{failed} failed" if failed > 0 else ""


def header_accessible_text(header: GroupHeader, shown: int, failed: int) -> str:
    parts = [header.label, header.detail, header_count_text(header, shown), header_failed_text(failed)]
    return ", ".join(part for part in parts if part)


def make_group_row(entry: Entry) -> QTreeWidgetItem:
    """The list row for a header or divider: no flags, so it cannot be selected,
    activated or reached by the keyboard, and its own accessible text."""

    row = QTreeWidgetItem([entry.label if entry.label else ""])
    row.setFlags(Qt.ItemFlag.NoItemFlags)
    row.setData(0, VIEWER_ENTRY_ROLE, entry)
    row.setSizeHint(0, QSize(0, group_row_height(entry)))
    if isinstance(entry, Divider):
        row.setData(0, Qt.ItemDataRole.AccessibleTextRole, entry.label or "Next batch")
    # A header's counts and its accessible text are written by ``RowIndex.refresh_groups``,
    # which every fill and every filter ends with: that is their one home.
    return row


def paint_group_row(painter: QPainter, option: QStyleOptionViewItem, index, entry: Entry) -> None:
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if isinstance(entry, GroupHeader):
        _paint_header(painter, option, index, entry)
    elif isinstance(entry, Divider):
        _paint_divider(painter, option, entry)
    painter.restore()


def _paint_header(painter: QPainter, option: QStyleOptionViewItem, index, header: GroupHeader) -> None:
    theme = current_palette()
    shown, failed = index.data(LIST_GROUP_SHOWN_ROLE) or (header.count, header.failed)
    rect = option.rect.adjusted(SPACE_M, SPACE_S, -SPACE_M, 0)
    font = QFont(option.font)
    font.setPointSizeF(TYPE_CAPTION)
    painter.setFont(font)
    metrics = painter.fontMetrics()
    count_text = header_count_text(header, shown)
    failed_text = header_failed_text(failed)

    # The right side first: a counted noun ("5 exposures") sits flush right.
    right = rect.right()
    if header.noun:
        painter.setPen(QColor(theme.text_muted))
        width = metrics.horizontalAdvance(count_text)
        painter.drawText(QRectF(right - width, rect.top(), width, rect.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, count_text)
        right -= width + SPACE_S

    # Then what follows the label inline: a plain count, and the failed note.
    inline: list[tuple[str, QColor, bool]] = []
    if not header.noun:
        inline.append((count_text, QColor(theme.text_muted), False))
    if failed_text:
        inline.append((failed_text, QColor(getattr(theme, STATUS_TONE_COLOURS["bad"])), True))
    inline_width = sum(metrics.horizontalAdvance(text) + SPACE_S for text, _colour, _bold in inline)

    left = rect.left()
    label_width = max(int(right - left - inline_width), 16)
    bold = QFont(font)
    bold.setBold(True)
    painter.setFont(bold)
    label = painter.fontMetrics().elidedText(header.label, Qt.TextElideMode.ElideMiddle, label_width)
    painter.setPen(QColor(theme.text))
    painter.drawText(QRectF(left, rect.top(), label_width, rect.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label)
    left += painter.fontMetrics().horizontalAdvance(label)

    painter.setFont(font)
    if header.detail:
        detail = f" · {header.detail}"
        room = max(int(right - left - inline_width), 0)
        elided = metrics.elidedText(detail, Qt.TextElideMode.ElideMiddle, room)
        painter.setPen(QColor(theme.text_muted))
        painter.drawText(QRectF(left, rect.top(), room, rect.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided)
        left += metrics.horizontalAdvance(elided)
    for text, colour, emphasised in inline:
        left += SPACE_S
        painter.setFont(bold if emphasised else font)
        painter.setPen(colour)
        width = painter.fontMetrics().horizontalAdvance(text)
        painter.drawText(QRectF(left, rect.top(), width, rect.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        left += width


def _paint_divider(painter: QPainter, option: QStyleOptionViewItem, divider: Divider) -> None:
    theme = current_palette()
    rect = option.rect
    left = rect.left() + SPACE_M
    right = rect.right() - _RIGHT_INSET_PX
    painter.setPen(QPen(QColor(theme.border), 1))
    y = rect.center().y()
    if not divider.label:
        painter.drawLine(left, y, right, y)
        return
    font = QFont(option.font)
    font.setPointSizeF(TYPE_CAPTION)
    painter.setFont(font)
    metrics = painter.fontMetrics()
    text = metrics.elidedText(divider.label, Qt.TextElideMode.ElideMiddle, max(right - left, 16))
    painter.setPen(QColor(theme.text_muted))
    painter.drawText(QRect(left, rect.top(), metrics.horizontalAdvance(text) + 1, rect.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
    line_from = left + metrics.horizontalAdvance(text) + SPACE_S
    if line_from < right:
        painter.setPen(QPen(QColor(theme.border), 1))
        painter.drawLine(line_from, y, right, y)
