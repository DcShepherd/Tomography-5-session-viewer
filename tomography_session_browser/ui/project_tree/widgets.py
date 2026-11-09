"""The project tree's widget and row painter, and the data roles its rows carry."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTreeWidgetItem

from tomography_session_browser.presentation.design_tokens import RADIUS_CONTROL, SPACE_S
from tomography_session_browser.presentation.help_content import InteractionKind, LocalBinding
from tomography_session_browser.presentation.project_tree import ProjectTreeGroup
from tomography_session_browser.ui.icons import themed_icon
from tomography_session_browser.ui.list_decorations import (
    is_project_badge,
    paint_project_badge,
    paint_selection_marker,
    project_badge_rect,
)
from tomography_session_browser.ui.motion import ItemHoverAnimator
from tomography_session_browser.ui.motion.selection import SelectionGlide
from tomography_session_browser.ui.theme import current_palette
from tomography_session_browser.ui.widgets.smooth_tree import SmoothScrollTree

#: The row's entity (a group, session, sample, entity group or acquisition item).
OBJECT_ROLE = int(Qt.ItemDataRole.UserRole)
EXPANSION_ROLE = int(Qt.ItemDataRole.UserRole) + 1
HIGHLIGHT_ROLE = int(Qt.ItemDataRole.UserRole) + 2
#: Absolute path carried by a "Recent sessions" row so it can be reopened.
RECENT_PATH_ROLE = int(Qt.ItemDataRole.UserRole) + 3
FOLDED_SESSION_ROLE = int(Qt.ItemDataRole.UserRole) + 4
PROJECT_GROUP_ROLE = int(Qt.ItemDataRole.UserRole) + 5
#: A project row marked for a new user group (B4); display only, cleared by every rebuild.
MARK_ROLE = int(Qt.ItemDataRole.UserRole) + 6
#: The ``UserGroup`` a heading row stands for; the row has no scope, so no ``OBJECT_ROLE``.
USER_GROUP_ROLE = int(Qt.ItemDataRole.UserRole) + 7
MARK_MODIFIERS = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier


def is_group_row(item: QTreeWidgetItem | None) -> bool:
    """A project group's own row: the only rows that can be marked and grouped."""

    return item is not None and isinstance(item.data(0, PROJECT_GROUP_ROLE) or item.data(0, OBJECT_ROLE), ProjectTreeGroup)


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

    #: A press that marked a row: its release and double-click are not clicks.
    _marking = False

    # -- marks (B4): Ctrl or Shift + click, Ctrl + Space, Escape ---------------
    # A mark never reaches Qt's selection, so the current row, the scope and the
    # narrow window's drawer stay as they were.

    def _mark_target(self, event) -> QTreeWidgetItem | None:
        if event.button() != Qt.MouseButton.LeftButton or not event.modifiers() & MARK_MODIFIERS:
            return None
        position = event.position().toPoint()
        item = self.itemAt(position)
        if item is None or not is_group_row(item) or position.x() < self.visualItemRect(item).left():
            return None
        return item

    def toggle_mark(self, item: QTreeWidgetItem) -> None:
        item.setData(0, MARK_ROLE, not item.data(0, MARK_ROLE))
        self.viewport().update(self.visualItemRect(item).adjusted(-self.indentation(), -2, self.viewport().width(), 2))

    def marked_items(self) -> list[QTreeWidgetItem]:
        found: list[QTreeWidgetItem] = []
        pending = [self.topLevelItem(index) for index in range(self.topLevelItemCount())]
        while pending:
            item = pending.pop(0)
            if item is None:
                continue
            if item.data(0, MARK_ROLE):
                found.append(item)
            pending[:0] = [item.child(index) for index in range(item.childCount())]
        return found

    def clear_marks(self) -> bool:
        marked = self.marked_items()
        for item in marked:
            item.setData(0, MARK_ROLE, False)
        if marked:
            self.viewport().update()
        return bool(marked)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        item = self._mark_target(event)
        if item is None:
            super().mousePressEvent(event)
            return
        self._marking = True
        self.toggle_mark(item)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._marking:
            self._marking = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt API
        item = self._mark_target(event)
        if item is None:
            super().mouseDoubleClickEvent(event)
            return
        # A quick second Ctrl + click arrives as a double-click: it marks again.
        self._marking = True
        self.toggle_mark(item)
        event.accept()

    def _is_mark_key(self, event) -> bool:
        if event.key() == Qt.Key.Key_Space and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            return True
        return event.key() == Qt.Key.Key_Escape and bool(self.marked_items())

    def event(self, event) -> bool:  # noqa: A003 - Qt API
        # Window shortcuts (Escape leaves Focus, for one) must not take a key that marks or clears marks.
        if event.type() == QEvent.Type.ShortcutOverride and self._is_mark_key(event):
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() == Qt.Key.Key_Space and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            current = self.currentItem()
            if current is not None and is_group_row(current):
                self.toggle_mark(current)
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape and self.clear_marks():
            event.accept()
            return
        super().keyPressEvent(event)

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
    selection_glide: SelectionGlide | None = None

    def initStyleOption(self, option: QStyleOptionViewItem, index) -> None:  # noqa: N802 - Qt API
        super().initStyleOption(option, index)
        # A project badge (LS, AT, DC, !) is drawn as a tag by ``paint``; the
        # item keeps its text for tooltips, filtering and screen readers.
        if index.column() == 1 and is_project_badge(option.text):
            option.text = ""
        # A marked row shows a ticked box (``_paint_mark``) in place of its
        # icon; the decoration keeps its space, so the text does not move.
        if index.column() == 0 and index.data(MARK_ROLE):
            option.icon = QIcon()

    def paint(self, painter, option: QStyleOptionViewItem, index) -> None:  # noqa: N802 - Qt API
        paint_option = QStyleOptionViewItem(option)
        paint_option.state &= ~QStyle.StateFlag.State_HasFocus
        hovered = bool(paint_option.state & QStyle.StateFlag.State_MouseOver)
        paint_option.state &= ~QStyle.StateFlag.State_MouseOver
        selected = bool(paint_option.state & QStyle.StateFlag.State_Selected)
        highlighted = bool(index.sibling(index.row(), 0).data(HIGHLIGHT_ROLE))
        if index.column() == 0:
            palette = current_palette()
            if selected and self.selection_glide is None:
                self._paint_row_fill(painter, paint_option, QColor(palette.selection))
            elif highlighted and not selected:
                fill = QColor(palette.accent)
                fill.setAlpha(34)
                self._paint_row_fill(painter, paint_option, fill)
            elif index.data(MARK_ROLE):
                fill = QColor(palette.accent)
                fill.setAlpha(22)
                self._paint_row_fill(painter, paint_option, fill)
            elif not selected:
                progress = (
                    self.hover_animator.progress(index)
                    if self.hover_animator is not None
                    else (1.0 if hovered else 0.0)
                )
                if progress > 0.001:
                    fill = QColor(palette.surface_alt)
                    fill.setAlphaF(min(1.0, progress) * fill.alphaF())
                    self._paint_row_fill(painter, paint_option, fill)
            if self.selection_glide is not None:
                self.selection_glide.paint(painter, paint_option.rect)
        super().paint(painter, paint_option, index)
        badge = index.data(Qt.ItemDataRole.DisplayRole) if index.column() == 1 else None
        if isinstance(badge, str) and is_project_badge(badge):
            paint_project_badge(painter, self.badge_rect(paint_option), badge, current_palette())
        # Column 0 only: the marker belongs to the row, and ``paint`` runs
        # once per column.
        if index.column() == 0 and (highlighted or (selected and self.selection_glide is None)):
            paint_selection_marker(
                painter, paint_option.rect, current_palette(), strong=highlighted
            )
        if index.column() == 0 and index.data(MARK_ROLE):
            mark_option = QStyleOptionViewItem(paint_option)
            self.initStyleOption(mark_option, index)  # Where the style puts the (blank) icon.
            self._paint_mark(painter, mark_option)

    @staticmethod
    def _paint_mark(painter, option: QStyleOptionViewItem) -> None:
        """A ticked box in place of the row's icon: the row is marked for a new group."""

        palette = current_palette()
        style = option.widget.style() if option.widget is not None else None
        icon_rect = (style.subElementRect(QStyle.SubElement.SE_ItemViewItemDecoration, option, option.widget)
                     if style is not None else QRect())
        if not icon_rect.isValid():
            icon_rect = QRect(option.rect.left() + 2, option.rect.top(), option.decorationSize.width(), option.rect.height())
        side = min(16, icon_rect.height(), option.rect.height() - 4)
        box = QRectF(icon_rect.center().x() - side / 2 + 0.5, option.rect.center().y() - side / 2 + 0.5, side, side)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(palette.primary_fill))
        painter.drawRoundedRect(box, 3, 3)
        themed_icon("check", color=palette.primary_text, size=int(side) - 4).paint(painter, box.adjusted(2, 2, -2, -2).toRect())
        painter.restore()

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


# Contextual keys handled by this module; consumed by the shared Help sheet.
LOCAL_KEYS = (
    LocalBinding('project_tree.mark', 'Project tree', InteractionKind.KEYBOARD, ('Ctrl+Space',),
                 'Mark or unmark the current top-level row for a new group. The dataset on show does not change'),
    LocalBinding('project_tree.mark-click', 'Project tree', InteractionKind.MOUSE, ('Ctrl+click', 'Shift+click'),
                 'Mark or unmark a top-level row for a new group. Right-click a marked row to group the marked rows'),
    LocalBinding('project_tree.clear-marks', 'Project tree with marked rows', InteractionKind.KEYBOARD, ('Esc',),
                 'Clear all marks. In the Project drawer, the next Esc closes the drawer'),
)
