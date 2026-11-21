"""The report dialog's tick tree (plan B4, Q5 A): ticks choose the content, so there is no selection.

The tree had borrowed the main project tree's object name, so it took that
tree's rules without its delegate: hover covered only the indent, a selection
drew three blocks with seams, and a selected row had no effect on the report.

Here a click on a row ticks it, and Space ticks the current row from either
column. Each click of a double click counts, as on a check box, and a double
click does not fold the row. The hover is one row-wide fill, faded by
``ItemHoverAnimator``, which also follows reduced motion. Keyboard focus is a
ring round the current row, shown after Tab focus or when a key moves the
current row, not after a click. The tick boxes are the themed ``QCheckBox``
boxes (``#reportScopeTree::indicator`` in the sheet). The tree handles no keys
of its own: Qt's item view does.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QModelIndex, QPersistentModelIndex, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QHoverEvent, QPainter, QPen
from PySide6.QtWidgets import QAbstractItemView, QTreeWidget, QTreeWidgetItem, QWidget

from tomography_session_browser.presentation.design_tokens import RADIUS_CONTROL
from tomography_session_browser.ui.motion import ItemHoverAnimator
from tomography_session_browser.ui.theme import current_palette
from tomography_session_browser.ui.widgets.keyboard_focus import keyboard_focus_reason

_Press = tuple[QPersistentModelIndex, Qt.CheckState]


class ReportScopeTree(QTreeWidget):
    """A two-column tick tree: no selection, a click or Space ticks, one row-wide hover."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("reportScopeTree")
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)  # Ticks choose the rows.
        # A double click is two clicks on the box: it does not also fold the row.
        self.setExpandsOnDoubleClick(False)
        self._pressed: _Press | None = None
        self._double: _Press | None = None
        self._mouse_press = False
        self._focus_visible = False
        self._revealed: QPersistentModelIndex | None = None
        self.hover_animator = ItemHoverAnimator(self)
        self.itemClicked.connect(self._tick_on_click)
        self.itemDoubleClicked.connect(self._remember_double_click)
        self.selectionModel().currentChanged.connect(self._current_changed)
        self.verticalScrollBar().actionTriggered.connect(self._drop_reveal)
        # After Qt's own slot, which marks the row under the pointer (see the hover part).
        self.verticalScrollBar().valueChanged.connect(self._forget_qt_hover)
        self.horizontalScrollBar().valueChanged.connect(self._forget_qt_hover)

    # -- ticks ------------------------------------------------------------------

    def _current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        # Space ticks the row from either column, because only the first column holds the box.
        if current.isValid() and current.column() != 0:
            self.setCurrentIndex(current.siblingAtColumn(0))
            return
        if self.hasFocus() and not self._mouse_press:
            self._set_focus_visible(True)  # a key moved the current row: show where it is
            self._drop_reveal()
        self.viewport().update()          # the focus ring spans the row, not one cell

    @staticmethod
    def _press(item: QTreeWidgetItem | None) -> _Press | None:
        if item is None:
            return None
        tree = item.treeWidget()
        return (QPersistentModelIndex(tree.indexFromItem(item)), item.checkState(0)) if tree is not None else None

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._set_focus_visible(False)
        self._drop_reveal()
        left = event.button() == Qt.MouseButton.LeftButton
        self._pressed = self._press(self.itemAt(event.position().toPoint())) if left else None
        self._mouse_press = True
        try:
            super().mousePressEvent(event)
        finally:
            self._mouse_press = False

    def _remember_double_click(self, item: QTreeWidgetItem, _column: int) -> None:
        # Its release sends no click, but it is a second click: it counts, as
        # on the box itself and on a QCheckBox, so a fast second click undoes
        # the first. Qt emits no double click for the expander arrow.
        self._double = self._press(item)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        double, self._double = self._double, None
        super().mouseReleaseEvent(event)
        item = self.itemAt(event.position().toPoint())
        if double is not None and item is not None:
            self._tick(double, item)

    def _tick_on_click(self, item: QTreeWidgetItem, _column: int) -> None:
        """Tick a clicked row. Qt emits no click for the expander arrow, so it folds the row without a tick."""
        pressed, self._pressed = self._pressed, None
        if pressed is not None:
            self._tick(pressed, item)

    def _tick(self, pressed: _Press, item: QTreeWidgetItem) -> None:
        """Tick ``item`` if the press was on it, unless the click was on its box and the box already changed."""
        if QModelIndex(pressed[0]) != self.indexFromItem(item) or item.checkState(0) != pressed[1]:
            return
        flags = item.flags()
        if not flags & Qt.ItemFlag.ItemIsUserCheckable or not flags & Qt.ItemFlag.ItemIsEnabled:
            return
        # As Qt's own box: a partly ticked parent becomes ticked.
        item.setCheckState(0, Qt.CheckState.Unchecked if pressed[1] == Qt.CheckState.Checked else Qt.CheckState.Checked)

    # -- reveal -----------------------------------------------------------------

    def reveal(self, item: QTreeWidgetItem) -> None:
        """Scroll ``item``'s top ancestor to the top, keeping ``item`` in view (F5).

        The group and atlas rows above a preset then show. The dialog's layout
        settles after it is shown, so the tree does this again each time it is
        resized, until the reviewer scrolls, clicks or moves the current row.
        """
        self._revealed = QPersistentModelIndex(self.indexFromItem(item))
        self._apply_reveal()

    def _drop_reveal(self, *_args) -> None:
        self._revealed = None

    def _apply_reveal(self) -> None:
        index = QModelIndex(self._revealed) if self._revealed is not None else QModelIndex()
        item = self.itemFromIndex(index) if index.isValid() else None
        if item is None:
            self._revealed = None
            return
        top = item
        while top.parent() is not None:
            top = top.parent()
        self.scrollToItem(top, QAbstractItemView.ScrollHint.PositionAtTop)
        self.scrollToItem(item, QAbstractItemView.ScrollHint.EnsureVisible)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        if self._revealed is not None:
            self._apply_reveal()

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._drop_reveal()
        super().wheelEvent(event)

    # -- focus ring ---------------------------------------------------------------

    def _set_focus_visible(self, visible: bool) -> None:
        if visible != self._focus_visible:
            self._focus_visible = visible
            self.viewport().update()

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._set_focus_visible(keyboard_focus_reason(event.reason()))
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().focusOutEvent(event)
        self.viewport().update()

    def focus_ring_visible(self) -> bool:
        return self._focus_visible and self.hasFocus() and self.currentIndex().isValid()

    # -- hover --------------------------------------------------------------------
    # As ``ProjectTreeWidget``: button-less moves and hover events stay out of
    # Qt's own hover tracking, so the native style paints no snapping "hot"
    # box. The animator sees them first, through its viewport event filter.
    # Two more paths mark Qt's hover row: a move with a button held (a click
    # that drags one pixel) and a scroll, which reads the pointer position.
    # The leave that would clear the row is held back above, so the native
    # style kept a lighter block in the row's indent, beside the animator's
    # fill, until the pointer left the tree (plan B6 finding). Each path is
    # followed at once by a leave, before the row is painted.

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.buttons() == Qt.MouseButton.NoButton:
            event.accept()
            return
        super().mouseMoveEvent(event)
        self._forget_qt_hover()

    def _forget_qt_hover(self, *_args) -> None:
        """Tell Qt's item view that the pointer left, so it marks no row as hovered."""
        outside = QPointF(-1, -1)
        # Straight to the item view: the animator's filter does not see it, and its hover stays.
        super().viewportEvent(QHoverEvent(QEvent.Type.HoverLeave, outside, outside, outside))

    def viewportEvent(self, event) -> bool:  # noqa: N802 - Qt API
        if event.type() in (QEvent.Type.HoverEnter, QEvent.Type.HoverMove, QEvent.Type.HoverLeave):
            return True
        return super().viewportEvent(event)

    def drawRow(self, painter, option, index) -> None:  # noqa: N802 - Qt API
        """One rounded fill under the whole row (indent, box, name and type), then the ring on top."""
        row = index.siblingAtColumn(0)
        rect = QRectF(0, option.rect.top(), self.viewport().width(), option.rect.height()).adjusted(0.5, 0.5, -0.5, -0.5)
        palette = current_palette()
        progress = self.hover_animator.progress(row)
        if progress > 0.001 and self.isEnabled():
            fill = QColor(palette.surface_alt)
            fill.setAlphaF(min(1.0, progress) * fill.alphaF())
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(rect, RADIUS_CONTROL, RADIUS_CONTROL)
            painter.restore()
        super().drawRow(painter, option, index)
        if self.focus_ring_visible() and row == self.currentIndex().siblingAtColumn(0):
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor(palette.accent), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(0.75, 0.75, -0.75, -0.75), RADIUS_CONTROL, RADIUS_CONTROL)
            painter.restore()
