"""A tree that scrolls per pixel and eases every scroll it makes (plan F5.5).

Lists and trees scrolled a whole row per step, and a selection that landed
off-screen (a jump from another tab, a keyboard step) cut straight to it.
This tree scrolls in pixels, eases wheel notches through a
:class:`~tomography_session_browser.ui.motion.SmoothScroller`, and eases
``scrollTo`` as well: Qt still decides where the row should end up; the tree
only travels there instead of jumping. Which row is current, and when, is
unchanged.

A branch the reviewer opens or closes (its arrow, a double-click, or the
keyboard) also opens along its height (plan F5.6): the tree changes at once,
and a :class:`~tomography_session_browser.ui.motion.BranchReveal` shows the
rows below travelling to their new place. Branches opened by the program
(restoring expansion, filtering) change instantly, as before.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QModelIndex, QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QAbstractItemView, QTreeWidget, QTreeWidgetItem, QWidget
import shiboken6

from tomography_session_browser.ui.motion import BranchReveal, SmoothScroller, motion_enabled

#: Keys that open (True) or close (False) the current branch.
_BRANCH_KEYS = {
    Qt.Key.Key_Right: True,
    Qt.Key.Key_Plus: True,
    Qt.Key.Key_Left: False,
    Qt.Key.Key_Minus: False,
}
#: Keys that may toggle the current branch either way (activation, "*").
_TOGGLE_KEYS = frozenset({Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Asterisk})


@dataclass
class _BranchWatch:
    """The tree as it was just before an input that may toggle ``item``."""

    item: QTreeWidgetItem
    expanded: bool
    extent: int
    scroll: int
    size: QSize
    picture: QPixmap


def _descends_from(index: QModelIndex, ancestor: QModelIndex) -> bool:
    parent = index.parent()
    while parent.isValid():
        if parent == ancestor:
            return True
        parent = parent.parent()
    return False


class SmoothScrollTree(QTreeWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._branch_watch: _BranchWatch | None = None
        self._branch_reveal: BranchReveal | None = None
        # First: setting the scroll mode already calls ``scrollTo``.
        self.scroller = SmoothScroller(self)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        # Anything that moves or changes the rows makes a reveal's pictures
        # stale: show the tree as it is.
        self.verticalScrollBar().valueChanged.connect(self._end_branch_reveal)
        model = self.model()
        for signal in (model.rowsInserted, model.rowsRemoved, model.modelReset, model.layoutChanged):
            signal.connect(self._end_branch_reveal)

    def scrollTo(self, index, hint=QAbstractItemView.ScrollHint.EnsureVisible) -> None:  # noqa: N802 - Qt API
        bar = self.verticalScrollBar()
        before = bar.value()
        super().scrollTo(index, hint)  # where Qt would put the row
        after = bar.value()
        if after == before or not self.isVisible() or getattr(self, "scroller", None) is None:
            return
        # Back to where the view was, then travel there. Nothing has been
        # painted in between.
        bar.setValue(before)
        self.scroller.scroll_to(after)

    def scrollToItem(self, item, hint=QAbstractItemView.ScrollHint.EnsureVisible) -> None:  # noqa: N802 - Qt API
        # QTreeWidget's own scrollToItem calls the C++ scrollTo directly, so
        # it would never reach the override above.
        self.scrollTo(self.indexFromItem(item), hint)

    # -- branches opening and closing (plan F5.6) ---------------------------

    @property
    def branch_reveal(self) -> BranchReveal | None:
        reveal = self._branch_reveal
        return reveal if reveal is not None and shiboken6.isValid(reveal) and reveal.isVisible() else None

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._end_branch_reveal()
        position = event.position().toPoint()
        item = self.itemAt(position)
        # Only a press on the branch arrow toggles; a press on the row selects.
        if item is not None and position.x() < self.visualItemRect(item).left():
            self._watch_branch(item)
        super().mousePressEvent(event)
        # Styles differ on whether the press or the release toggles.
        self._check_branch(keep=True)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().mouseReleaseEvent(event)
        self._check_branch(keep=False)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._end_branch_reveal()
        self._watch_branch(self.itemAt(event.position().toPoint()))
        super().mouseDoubleClickEvent(event)
        self._check_branch(keep=False)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._end_branch_reveal()
        item = self.currentItem()
        key = event.key()
        if item is not None and (key in _TOGGLE_KEYS or _BRANCH_KEYS.get(key) == (not item.isExpanded())):
            self._watch_branch(item)
        super().keyPressEvent(event)
        self._check_branch(keep=False)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._end_branch_reveal()
        super().resizeEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._end_branch_reveal()
        super().hideEvent(event)

    def _end_branch_reveal(self, *_args) -> None:
        reveal = self._branch_reveal
        self._branch_reveal = None
        if reveal is not None and shiboken6.isValid(reveal):
            reveal.dismiss()

    def _watch_branch(self, item: QTreeWidgetItem | None) -> None:
        """Keep a picture of the tree in case this input toggles ``item``.

        Only for a row with children and something visible below it, and only
        on direct input, so a click on a row pays nothing.
        """

        self._branch_watch = None
        if item is None or item.childCount() == 0 or not self.isVisible() or not motion_enabled(self):
            return
        viewport = self.viewport()
        top = self.visualItemRect(item).bottom() + 1
        if top < 0 or top >= viewport.height():
            return
        index = self.indexFromItem(item)
        self._branch_watch = _BranchWatch(
            item=item,
            expanded=item.isExpanded(),
            extent=self._branch_extent(index) if item.isExpanded() else 0,
            scroll=self.verticalScrollBar().value(),
            size=viewport.size(),
            picture=viewport.grab(),
        )

    def _check_branch(self, *, keep: bool) -> None:
        """Start a reveal if the watched branch changed; ``keep`` waits for a release."""

        watch = self._branch_watch
        if watch is None:
            return
        try:
            expanded = watch.item.isExpanded()
            index = self.indexFromItem(watch.item)
        except RuntimeError:  # the item went with a rebuilt tree
            self._branch_watch = None
            return
        if expanded == watch.expanded:
            if not keep:
                self._branch_watch = None
            return
        self._branch_watch = None
        if not index.isValid():
            return
        self.executeDelayedItemsLayout()
        viewport = self.viewport()
        # The rows above the branch must not have moved, and the viewport
        # must keep its size: a scroll bar about to appear or go would
        # re-lay out every row a moment later.
        if (
            self.verticalScrollBar().value() != watch.scroll
            or viewport.size() != watch.size
            or self._scroll_bar_will_toggle()
        ):
            return
        top = self.visualRect(index).bottom() + 1
        height = viewport.height() - top
        extent = min(self._branch_extent(index) if expanded else watch.extent, height)
        if top < 0 or height <= 0 or extent <= 0:
            return
        now = viewport.grab()
        opened, closed = (now, watch.picture) if expanded else (watch.picture, now)
        self._branch_reveal = BranchReveal(viewport, top, opened, closed, extent, opening=expanded)

    def _branch_extent(self, index: QModelIndex) -> int:
        """Height of the visible rows under an open branch, up to a viewport."""

        top = self.visualRect(index).bottom() + 1
        limit = self.viewport().height()
        bottom = top
        below = self.indexBelow(index)
        while below.isValid() and _descends_from(below, index):
            bottom = self.visualRect(below).bottom() + 1
            if bottom - top >= limit:
                break
            below = self.indexBelow(below)
        return bottom - top

    def _scroll_bar_will_toggle(self) -> bool:
        if self.verticalScrollBarPolicy() != Qt.ScrollBarPolicy.ScrollBarAsNeeded:
            return False
        bar = self.verticalScrollBar()
        return (bar.maximum() > bar.minimum()) != bar.isVisible()
