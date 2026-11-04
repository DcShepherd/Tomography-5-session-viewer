"""One pinned pick and one hover, shared by a group of index charts.

The Summary's defocus and dose plots set the model: click highlights,
double-click opens, right-click or Esc clears, and hovering shows a tomogram
without clearing the highlight. Charts only draw and report; this owns the pick.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

if TYPE_CHECKING:
    from tomography_session_browser.ui.widgets.index_chart import IndexChart


@dataclass(frozen=True, slots=True)
class RowPick:
    """One tomogram, by row key; it is the same tomogram in every chart of a group."""
    key: str


@dataclass(frozen=True, slots=True)
class BinPick:
    """One count bin of one chart's series lane."""
    chart: IndexChart
    series: int
    index: int


ChartPick = RowPick | BinPick


class ChartSelection(QObject):
    changed = Signal()
    opened = Signal(object, object)  # chart, frozenset of row keys

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.charts: list[IndexChart] = []
        self.pinned: ChartPick | None = None
        self.hovered = ""

    def add(self, chart: IndexChart) -> None:
        self.charts.append(chart)
        chart.hovered.connect(self.hover)
        chart.picked.connect(self.pin)
        chart.opened.connect(lambda rows, chart=chart: self.opened.emit(chart, rows))
        chart.clear_requested.connect(self.clear)
        chart.show_pick(self.pinned, self.hovered)

    def shown(self) -> ChartPick | None:
        """What the readout describes: the hovered tomogram, else the pinned pick."""
        return RowPick(self.hovered) if self.hovered else self.pinned

    def hover(self, key: str) -> None:
        self.hovered = key
        self._publish()

    def pin(self, pick: ChartPick) -> None:
        self.pinned = pick
        self._publish()

    def clear(self) -> None:
        self.pinned = None
        self._publish()

    def _publish(self) -> None:
        for chart in self.charts:
            chart.show_pick(self.pinned, self.hovered)
        self.changed.emit()
