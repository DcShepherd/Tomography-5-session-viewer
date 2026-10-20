"""The collection outcome panel: the headline, its bar and the by-sample table
(design review B3.2).

What was recorded, how it came out, and which sample holds what. Planned
acquisitions that were never recorded are not counted. The failed and
incomplete counts are links that open exactly those rows in their own tab; a
sample's name is a link to its own summary when it names exactly one collection
sample, and says why it is not otherwise.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from tomography_session_browser.presentation.dashboard.models import DashboardFilterModel, DashboardModel
from tomography_session_browser.presentation.dashboard.sections.samples import BySampleRowModel
from tomography_session_browser.presentation.design_tokens import SPACE_L, SPACE_M, SPACE_S, SPACE_XS
from tomography_session_browser.ui.dashboard.common import CardBox, card_layout, status_dot, status_tone
from tomography_session_browser.ui.theme import TIER_LANDMARK, TIER_PRIMARY, TIER_SUPPORTING, apply_tier
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel

if TYPE_CHECKING:
    from tomography_session_browser.ui.dashboard.widget import SessionDashboard

BAR_HEIGHT_PX = 10
MINI_BAR_WIDTH_PX = 92
MINI_BAR_HEIGHT_PX = 6

#: The status of a tilt series, in the order the bar and the headline give them.
_OUTCOMES = ("complete", "incomplete", "failed", "unknown")
_OUTCOME_TONES = {"complete": "good", "incomplete": "warn", "failed": "bad", "unknown": "unknown"}
_FILTER_LABELS = {"incomplete": "Incomplete", "failed": "Failed", "unknown": "Unknown"}


class OutcomeBar(QWidget):
    """Complete, incomplete, failed and unknown as one bar.

    Each non-empty bar fills its width with its own outcome proportions.
    Painted from the palette when it paints, so a theme switch needs no rebuild.
    """

    def __init__(self, counts: dict[str, int], *, height: int = BAR_HEIGHT_PX,
                 width: int | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._counts = {name: counts.get(name, 0) for name in _OUTCOMES}
        self.setFixedHeight(height)
        if width is not None:
            self.setFixedWidth(width)
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        else:
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        total = sum(self._counts.values())
        spoken = ", ".join(f"{self._counts[name]} {name}" for name in _OUTCOMES if self._counts[name])
        self.setAccessibleName(f"{spoken} of {total} recorded tilt series" if total else "No recorded tilt series")

    def segments(self) -> list[tuple[str, int]]:
        """The non-empty segments, in order: ``(outcome, count)``."""

        return [(name, self._counts[name]) for name in _OUTCOMES if self._counts[name]]

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt signature
        from tomography_session_browser.ui.theme import current_palette

        palette = current_palette()
        colours = {
            "complete": palette.chart_green,
            "incomplete": palette.chart_amber,
            "failed": palette.chart_red,
            "unknown": palette.unknown,
        }
        rect = QRectF(self.rect())
        radius = rect.height() / 2
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(palette.surface_hi))
            painter.drawRoundedRect(rect, radius, radius)
            total = sum(self._counts.values())
            if total <= 0:
                return
            outline = QPainterPath()
            outline.addRoundedRect(rect, radius, radius)
            painter.setClipPath(outline)
            span = rect.width()
            x = rect.left()
            for name, count in self.segments():
                width = span * count / total
                painter.setBrush(QColor(colours[name]))
                painter.drawRect(QRectF(x, rect.top(), width, rect.height()))
                x += width
        finally:
            painter.end()


def build_outcome_panel(dashboard: SessionDashboard, model: DashboardModel, rows: tuple[BySampleRowModel, ...]) -> QWidget:
    card = CardBox()
    layout = card_layout(card)

    head = QHBoxLayout()
    head.setSpacing(SPACE_M)
    title = QLabel("Collection outcome")
    apply_tier(title, TIER_LANDMARK)
    head.addWidget(title)
    head.addStretch(1)
    note = QLabel("Planned acquisitions that were never recorded are not counted.")
    apply_tier(note, TIER_SUPPORTING)
    note.setWordWrap(True)
    note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    head.addWidget(note, stretch=2)
    layout.addLayout(head)

    layout.addWidget(outcome_headline(dashboard, model))
    counts = _outcome_counts(model)
    bar = OutcomeBar(counts)
    bar.setObjectName("outcomeBar")
    layout.addWidget(bar)
    if rows:
        layout.addWidget(_by_sample_table(dashboard, rows))
    return card


def _outcome_counts(model: DashboardModel) -> dict[str, int]:
    outcomes = model.outcomes
    return {
        "complete": outcomes.complete,
        "incomplete": outcomes.incomplete,
        "failed": outcomes.failed,
        "unknown": outcomes.unknown,
    }


def outcome_headline(dashboard: SessionDashboard, model: DashboardModel, *, noun: str = "recorded tilt series") -> QWidget:
    """"85 recorded tilt series · 80 complete · ● 5 failed": the counts that can
    open rows are links, the rest are read. ``noun`` follows the total ("recorded"
    on a card already titled Tilt series)."""

    wrap = QWidget()
    wrap.setObjectName("outcomeHeadline")
    row = QHBoxLayout(wrap)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(SPACE_L)
    flow = row
    counts = _outcome_counts(model)
    total = sum(counts.values())

    first = QWidget()
    first_row = QHBoxLayout(first)
    first_row.setContentsMargins(0, 0, 0, 0)
    first_row.setSpacing(SPACE_S)
    number = QLabel(f"{total:,}")
    number.setObjectName("outcomeTotal")
    first_row.addWidget(number)
    word = QLabel(noun)
    apply_tier(word, TIER_PRIMARY)
    first_row.addWidget(word)
    flow.addWidget(first)

    filters = {route.label: route for route in model.filters}
    for name in _OUTCOMES:
        count = counts[name]
        if not count:
            continue
        route = filters.get(_FILTER_LABELS.get(name, ""))
        flow.addWidget(_count_item(dashboard, name, count, route))
    flow.addStretch(1)
    return wrap


def _count_item(dashboard: SessionDashboard, name: str, count: int, route: DashboardFilterModel | None) -> QWidget:
    item = QWidget()
    row = QHBoxLayout(item)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(SPACE_XS)
    if name == "complete":
        number = QLabel(f"{count:,}")
        number.setObjectName("outcomeCount")
        row.addWidget(number)
        word = QLabel(name)
        apply_tier(word, TIER_PRIMARY)
        row.addWidget(word)
        return item
    row.addWidget(status_dot(_OUTCOME_TONES[name], width=14))
    text = f"{count:,} {name}"
    if route is None:
        label = QLabel(text)
        label.setObjectName("toneText")
        label.setProperty("tone", _OUTCOME_TONES[name])
        apply_tier(label, TIER_PRIMARY)
        row.addWidget(label)
        return item
    button = QToolButton()
    button.setObjectName("inlineLink")
    button.setProperty("tone", _OUTCOME_TONES[name])
    button.setText(text)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setToolTip(route.tooltip)
    button.setAccessibleName(route.tooltip)
    button.clicked.connect(
        lambda _checked=False, route=route: dashboard.filter_requested.emit(route)
    )
    row.addWidget(button)
    return item


# ------------------------------------------------------------------ by-sample table


def _by_sample_table(dashboard: SessionDashboard, rows: tuple[BySampleRowModel, ...]) -> QWidget:
    grid_widget = QWidget()
    grid_widget.setObjectName("bySampleTable")
    grid = QGridLayout(grid_widget)
    grid.setContentsMargins(0, SPACE_XS, 0, 0)
    grid.setHorizontalSpacing(SPACE_L)
    grid.setVerticalSpacing(SPACE_S)
    # Sample and the counts are small, the tilt-series column carries a bar.
    for column, stretch in enumerate((3, 8, 6, 5, 3)):
        grid.setColumnStretch(column, stretch)
    for column, heading in enumerate(("Sample", "Tilt series", "Batch positions", "Search maps", "Overviews")):
        label = QLabel(heading)
        label.setObjectName("tableHead")
        grid.addWidget(label, 0, column)
    for index, row in enumerate(rows, start=1):
        grid.addWidget(_sample_cell(dashboard, row), index, 0, Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(_tilt_cell(row), index, 1, Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(_batch_cell(row), index, 2, Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(_maps_cell(row), index, 3, Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(_plain(f"{row.overviews:,}"), index, 4, Qt.AlignmentFlag.AlignVCenter)
    return grid_widget


def _plain(text: str, *, tier: str = TIER_PRIMARY) -> QLabel:
    label = QLabel(text)
    apply_tier(label, tier)
    return label


def _toned(text: str, tone: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("toneText")
    label.setProperty("tone", status_tone(tone))
    return label


def _row(*parts: QWidget) -> QWidget:
    cell = QWidget()
    layout = QHBoxLayout(cell)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(SPACE_XS)
    for part in parts:
        layout.addWidget(part)
    layout.addStretch(1)
    return cell


def _sample_cell(dashboard: SessionDashboard, row: BySampleRowModel) -> QWidget:
    """The sample's name: a link to its summary when the row names exactly one
    collection sample, plain (with the reason) when it does not."""

    cell = QWidget()
    layout = QVBoxLayout(cell)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    if len(row.collection_sample_ids) == 1:
        sample_id = row.collection_sample_ids[0]
        button = QToolButton()
        button.setObjectName("tableLink")
        button.setText(row.label)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(f"Open {row.label}'s summary")
        button.setAccessibleName(f"Open {row.label} summary")
        button.clicked.connect(lambda _checked=False, sample_id=sample_id: dashboard.scope_requested.emit(sample_id))
        layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignLeft)
        return cell
    name = ElidedLabel(row.label)
    apply_tier(name, TIER_PRIMARY)
    layout.addWidget(name)
    reason = QLabel(f"{len(row.collection_sample_ids)} collection samples share this grid")
    apply_tier(reason, TIER_SUPPORTING)
    reason.setWordWrap(True)
    layout.addWidget(reason)
    cell.setToolTip(
        "More than one collection sample is linked to this grid, so there is no single summary to open."
    )
    return cell


def _tilt_cell(row: BySampleRowModel) -> QWidget:
    counts = {
        "complete": row.tilt_complete,
        "incomplete": row.tilt_incomplete,
        "failed": row.tilt_failed,
        "unknown": row.tilt_unknown,
    }
    bar = OutcomeBar(counts, height=MINI_BAR_HEIGHT_PX, width=MINI_BAR_WIDTH_PX)
    parts: list[QWidget] = [bar, _plain(f"{row.tilt_complete:,} of {row.tilt_total:,}")]
    for count, word, tone in (
        (row.tilt_failed, "failed", "failed"),
        (row.tilt_incomplete, "incomplete", "warning"),
    ):
        if count:
            parts += [_plain("·", tier=TIER_SUPPORTING), _toned(f"{count:,} {word}", tone)]
    if row.tilt_unknown:
        parts += [_plain("·", tier=TIER_SUPPORTING), _plain(f"{row.tilt_unknown:,} unknown", tier=TIER_SUPPORTING)]
    return _row(*parts)


def _batch_cell(row: BySampleRowModel) -> QWidget:
    parts: list[QWidget] = [_plain(f"{row.batch_positions:,} recorded")]
    if row.inferred_failed_batches:
        inferred = _toned(f"+ {row.inferred_failed_batches:,} inferred failed", "failed")
        apply_tier(inferred, TIER_SUPPORTING)
        parts.append(inferred)
    return _row(*parts)


def _maps_cell(row: BySampleRowModel) -> QWidget:
    parts: list[QWidget] = [_plain(f"{row.search_maps:,}")]
    if row.incomplete_search_maps:
        parts += [_plain("·", tier=TIER_SUPPORTING), _toned(f"{row.incomplete_search_maps:,} incomplete", "warning")]
    return _row(*parts)
