"""A viewer list's rows, found by what they show and never by where they sit.

Every row that shows an object carries the object and its identity key
(``domain.identity.entity_key_text``); a row that shows none (a group's
header, B4.3) carries neither and is not counted, selected, restored or
decorated as an item. Nothing outside this module reads a row's position as
the position of its object, so a list may gain a header above any row without
a restore picking the wrong one.

The list's scroll position is remembered the same way: as the object row at
the top of the viewport and where its top edge sat (:meth:`RowIndex.anchor`),
so it survives a rebuild that changes the rows' positions.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

from tomography_session_browser.domain.identity import entity_key_text
from tomography_session_browser.presentation.viewer_entries import Divider, GroupHeader
from tomography_session_browser.ui.viewer.constants import LIST_GROUP_SHOWN_ROLE, LIST_TAGS_ROLE, VIEWER_ENTRY_ROLE, VIEWER_KEY_ROLE, VIEWER_OBJECT_ROLE
from tomography_session_browser.ui.viewer.group_rows import header_accessible_text


class RowIndex:
    """The object rows of one list, by key."""

    def __init__(self, tree: QTreeWidget) -> None:
        self._tree = tree
        self._rows: dict[str, QTreeWidgetItem] = {}

    def clear(self) -> None:
        """Forget every row; called as the list is emptied for a refill."""

        self._rows.clear()

    def add(self, row: QTreeWidgetItem, value: Any, key: str | None = None) -> None:
        """Make ``row`` show ``value``. Two values with one key keep the first row."""

        key = key if key is not None else entity_key_text(value)
        row.setData(0, VIEWER_OBJECT_ROLE, value)
        row.setData(0, VIEWER_KEY_ROLE, key)
        self._rows.setdefault(key, row)

    def row_for_key(self, key: str | None) -> QTreeWidgetItem | None:
        return self._rows.get(key) if key else None

    def row_for_value(self, value: Any) -> QTreeWidgetItem | None:
        return self._rows.get(entity_key_text(value))

    def value_for_key(self, key: str | None) -> Any | None:
        row = self.row_for_key(key)
        return row.data(0, VIEWER_OBJECT_ROLE) if row is not None else None

    def object_rows(self) -> Iterator[QTreeWidgetItem]:
        """Rows that show an object, in list order, hidden ones included."""

        tree = self._tree
        for index in range(tree.topLevelItemCount()):
            row = tree.topLevelItem(index)
            if row.data(0, VIEWER_OBJECT_ROLE) is not None:
                yield row

    def visible_rows(self) -> list[QTreeWidgetItem]:
        return [row for row in self.object_rows() if not row.isHidden()]

    def refresh_groups(self, *, failed_filtered: bool = False) -> None:
        """Bring each header and divider in line with the rows the filter left.

        A header shows the count of the rows left under it (and how many of
        those failed, unless the list is already filtered to Failed, which says
        so once) and hides when none are left. A divider shows only between
        shown runs: a labelled one when its own run has a row left, a plain
        hairline when there is a shown row on both sides of it in its group.
        """

        rows = [self._tree.topLevelItem(index) for index in range(self._tree.topLevelItemCount())]
        entries = [row.data(0, VIEWER_ENTRY_ROLE) for row in rows]
        if all(entry is None for entry in entries):
            return
        # A group runs from one header to the next; rows before the first header form one of their own.
        bounds = sorted({0, *(i for i, entry in enumerate(entries) if isinstance(entry, GroupHeader))}) + [len(rows)]
        for start, end in zip(bounds, bounds[1:], strict=False):
            self._refresh_group(rows[start:end], entries[start:end], failed_filtered)
        self._tree.viewport().update()

    @staticmethod
    def _refresh_group(rows: list[QTreeWidgetItem], entries: list[Any], failed_filtered: bool) -> None:
        shown = [entry is None and not row.isHidden() for row, entry in zip(rows, entries, strict=True)]
        seen_before = False
        for position, (row, entry) in enumerate(zip(rows, entries, strict=True)):
            if isinstance(entry, Divider):
                run_end = next((i for i in range(position + 1, len(rows)) if isinstance(entries[i], Divider)), len(rows))
                after = any(shown[position + 1 : run_end])
                row.setHidden(not after if entry.label else not (seen_before and after))
            seen_before = seen_before or shown[position]
        head = entries[0] if isinstance(entries[0], GroupHeader) else None
        if head is not None:
            count = sum(shown)
            failed = 0 if failed_filtered else sum(
                1 for flag, row in zip(shown, rows, strict=True) if flag and "failed" in (row.data(0, LIST_TAGS_ROLE) or ())
            )
            rows[0].setData(0, LIST_GROUP_SHOWN_ROLE, (count, failed))
            rows[0].setData(0, Qt.ItemDataRole.AccessibleTextRole, header_accessible_text(head, count, failed))
            rows[0].setHidden(count == 0)

    def counts(self) -> tuple[int, int]:
        """``(shown, all)`` object rows: the numbers "n of N" is made of."""

        rows = list(self.object_rows())
        return sum(1 for row in rows if not row.isHidden()), len(rows)

    # ------------------------------------------------------------------ scroll

    def anchor(self) -> tuple[str | None, int]:
        """The object row at the top of the viewport and its top edge's offset.

        The offset is signed: negative when the row is partly scrolled past.
        If the top of the viewport shows only a header or divider, the anchor is
        the first object row below it, with a positive offset. ``(None, 0)``
        when no object row is in the list.
        """

        tree = self._tree
        row = tree.itemAt(0, 0)
        while row is not None and row.data(0, VIEWER_OBJECT_ROLE) is None:
            row = tree.itemBelow(row)
        if row is None:
            return None, 0
        return row.data(0, VIEWER_KEY_ROLE), tree.visualItemRect(row).top()

    def restore_anchor(self, key: str | None, offset: int) -> bool:
        """Scroll so the anchor row's top sits ``offset`` below the viewport top.

        False (and nothing moved) when the row is gone or hidden by the query,
        so the caller keeps the selection in view instead.
        """

        row = self.row_for_key(key)
        if row is None or row.isHidden():
            return False
        tree = self._tree
        bar = tree.verticalScrollBar()
        # ``visualItemRect`` lays the rows out first, so the bar's range is current.
        target = bar.value() + tree.visualItemRect(row).top() - offset
        scroller = getattr(tree, "scroller", None)
        if scroller is not None:
            scroller.scroll_to(target, animate=False)
        else:
            bar.setValue(target)
        return True
