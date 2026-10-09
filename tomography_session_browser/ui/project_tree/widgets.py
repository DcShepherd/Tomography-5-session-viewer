"""The project tree's widget and row painter, and the data roles its rows carry."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from tomography_session_browser.presentation.design_tokens import RADIUS_CONTROL, SPACE_S
from tomography_session_browser.ui.list_decorations import (
    is_project_badge,
    paint_project_badge,
    paint_selection_marker,
    project_badge_rect,
)
from tomography_session_browser.ui.motion import ItemHoverAnimator
from tomography_session_browser.ui.theme import current_palette
from tomography_session_browser.ui.widgets.smooth_tree import SmoothScrollTree

#: The row's entity (a group, session, sample, entity group or acquisition item).
OBJECT_ROLE = int(Qt.ItemDataRole.UserRole)
EXPANSION_ROLE = int(Qt.ItemDataRole.UserRole) + 1
HIGHLIGHT_ROLE = int(Qt.ItemDataRole.UserRole) + 2
#: Absolute path carried by a "Recent sessions" row so it can be reopened.
RECENT_PATH_ROLE = int(Qt.ItemDataRole.UserRole) + 3


class ProjectTreeWidget(SmoothScrollTree):
    """Project tree whose hover belongs entirely to :class:`ItemHoverAnimator`.

    It scrolls per pixel and eases its scrolls, including the scroll to a row
    selected from elsewhere (plan F5.5).

    Qt item views track a hover row from plain mouse moves and hover events,
    and the native style then paints a snapping "hot" box behind the expander
    arrow that the style sheet cannot reach. Button-less moves and hover
    events are kept out of that internal tracking. The animator still sees
    them first, through its viewport event filter; drags and clicks are
    unaffected.
    """

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.buttons() == Qt.MouseButton.NoButton:
            event.accept()
            return
        super().mouseMoveEvent(event)

    def viewportEvent(self, event) -> bool:  # noqa: N802 - Qt API
        if event.type() in (QEvent.Type.HoverEnter, QEvent.Type.HoverMove, QEvent.Type.HoverLeave):
            return True
        return super().viewportEvent(event)

    def drawBranches(self, painter, rect, index) -> None:  # noqa: N802 - Qt API
        # The Windows 11 style marks a selected row with its own pill, in the
        # system accent colour, in the branch area just before the branches
        # are drawn. The delegate already marks the row with the palette's
        # accent bar (plan C9), so the pill is painted out and the native
        # expander drawn on the plain panel. Styling ``::branch`` instead
        # would hide the native arrow.
        painter.fillRect(rect, QColor(current_palette().panel))
        super().drawBranches(painter, rect, index)


class ProjectTreeDelegate(QStyledItemDelegate):
    """Paint the project tree's row fills once per row, without focus trails.

    Hover, selection and the cross-view highlight are all painted here as one
    full-row fill rather than by the style sheet. ``::item`` rules apply per
    column, so the ``:hover`` rule (which also snapped on and off) and the
    ``:selected`` rule each drew a separate rounded cell for the name and the
    badge with a seam between them (plan C9), and the branch area took a third
    block. The tree carries ``paintsRowSelection``, which turns the sheet's
    per-column selection fill off.
    """

    hover_animator: ItemHoverAnimator | None = None

    def initStyleOption(self, option: QStyleOptionViewItem, index) -> None:  # noqa: N802 - Qt API
        super().initStyleOption(option, index)
        # A project badge (LS, AT, DC, !) is drawn as a tag by ``paint``; the
        # item keeps its text for tooltips, filtering and screen readers.
        if index.column() == 1 and is_project_badge(option.text):
            option.text = ""

    def paint(self, painter, option: QStyleOptionViewItem, index) -> None:  # noqa: N802 - Qt API
        paint_option = QStyleOptionViewItem(option)
        paint_option.state &= ~QStyle.StateFlag.State_HasFocus
        hovered = bool(paint_option.state & QStyle.StateFlag.State_MouseOver)
        paint_option.state &= ~QStyle.StateFlag.State_MouseOver
        selected = bool(paint_option.state & QStyle.StateFlag.State_Selected)
        highlighted = bool(index.sibling(index.row(), 0).data(HIGHLIGHT_ROLE))
        if index.column() == 0:
            palette = current_palette()
            if selected:
                self._paint_row_fill(painter, paint_option, QColor(palette.selection))
            elif highlighted:
                fill = QColor(palette.accent)
                fill.setAlpha(34)
                self._paint_row_fill(painter, paint_option, fill)
            else:
                progress = (
                    self.hover_animator.progress(index)
                    if self.hover_animator is not None
                    else (1.0 if hovered else 0.0)
                )
                if progress > 0.001:
                    fill = QColor(palette.surface_alt)
                    fill.setAlphaF(min(1.0, progress) * fill.alphaF())
                    self._paint_row_fill(painter, paint_option, fill)
        super().paint(painter, paint_option, index)
        badge = index.data(Qt.ItemDataRole.DisplayRole) if index.column() == 1 else None
        if isinstance(badge, str) and is_project_badge(badge):
            paint_project_badge(painter, self.badge_rect(paint_option), badge, current_palette())
        # Column 0 only: the marker belongs to the row, and ``paint`` runs
        # once per column.
        if index.column() == 0 and (highlighted or selected):
            paint_selection_marker(
                painter, paint_option.rect, current_palette(), strong=highlighted
            )

    @staticmethod
    def _row_fill_right(option: QStyleOptionViewItem) -> float:
        """Where the row's fill (hover, selection) ends on the right."""

        view = option.widget
        return view.viewport().width() - 3 if view is not None else option.rect.right()

    @classmethod
    def badge_rect(cls, option: QStyleOptionViewItem) -> QRectF:
        """The badge's tag in the badge column, inset from the row's fill.

        The column runs to the viewport's edge but the fill stops 3 px short,
        so a badge placed against the column's edge sat on the selected row's
        rounded border and read as clipped. It keeps the same inset from the
        fill's edge as the name has from its left (``SPACE_S``).
        """

        cell = QRectF(option.rect)
        cell.setRight(min(cell.right(), cls._row_fill_right(option)))
        return project_badge_rect(cell, right_inset=SPACE_S)

    @classmethod
    def _paint_row_fill(cls, painter, option: QStyleOptionViewItem, fill: QColor) -> None:
        """One rounded fill from the name to the badge, painted in column 0."""

        right = cls._row_fill_right(option)
        rect = QRectF(option.rect.left(), option.rect.top(), right - option.rect.left(), option.rect.height())
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), RADIUS_CONTROL, RADIUS_CONTROL)
        painter.restore()
