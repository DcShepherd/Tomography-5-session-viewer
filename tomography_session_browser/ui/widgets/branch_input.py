"""Transient row identities for the two pictures in a tree branch reveal."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QModelIndex, QPoint, QPersistentModelIndex, QRect
from PySide6.QtWidgets import QTreeWidget


@dataclass(frozen=True)
class PaintedRow:
    index: QPersistentModelIndex
    rect: QRect


def visible_row_snapshot(tree: QTreeWidget) -> tuple[PaintedRow, ...]:
    """Capture only the viewport's rows; model mutations invalidate their indexes."""
    viewport = tree.viewport()
    index = QModelIndex()
    for y in range(viewport.height()):
        index = tree.indexAt(QPoint(viewport.width() // 2, y))
        if index.isValid():
            index = index.siblingAtColumn(0)
            break
    rows = []
    while index.isValid():
        rect = tree.visualRect(index)
        if rect.top() >= viewport.height():
            break
        rows.append(PaintedRow(QPersistentModelIndex(index), rect))
        index = tree.indexBelow(index)
    return tuple(rows)


def row_at(rows: tuple[PaintedRow, ...], y: int) -> PaintedRow | None:
    return next((row for row in rows if row.rect.top() <= y <= row.rect.bottom()
                 and row.index.isValid()), None)
