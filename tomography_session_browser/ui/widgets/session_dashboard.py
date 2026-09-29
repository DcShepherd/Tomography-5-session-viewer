"""The Session-tab dashboard.

Composes the smaller dashboard widgets into a single scrollable view. The
dashboard is purely presentational — it consumes ``DashboardModel`` and the
``SessionTimeline`` produced by other modules so it can be rebuilt cheaply
whenever the user opens a new session.

Click signals bubble up to ``MainWindow`` via ``navigate_requested(target)``
so the existing tab-switching logic can be reused.
"""

from __future__ import annotations

import logging
import math

from PySide6.QtCore import (
    QAbstractTableModel,
    QElapsedTimer,
    QModelIndex,
    QPoint,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QLayoutItem,
    QScrollArea,
    QSizePolicy,
    QTableView,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from tomography_session_browser.domain.display_names import count_phrase as _count_phrase
from tomography_session_browser.services.timeline_service import SessionTimeline
from tomography_session_browser.ui.motion import SmoothScroller, veil
from tomography_session_browser.ui.icons import themed_icon
from tomography_session_browser.ui.context_stack import ContextStack
from tomography_session_browser.ui.session_presenter import (
    AtlasRowModel,
    AtlasSummaryModel,
    BatchPositionProgressModel,
    CollectionHealthModel,
    DashboardModel,
    DashboardFilterModel,
    DoseInformationPointModel,
    SampleProgressModel,
    SearchMapCompletionModel,
    SearchMapOverviewModel,
    SearchMapOverviewRowModel,
    StatCardModel,
    WarningGroupModel,
    WarningRowModel,
    dose_information_point_tooltip,
)
from tomography_session_browser.ui.widgets.defocus_plot import DefocusScatterPlot
from tomography_session_browser.ui.widgets.completion_bar import CompletionBar
from tomography_session_browser.ui.widgets.dashboard_card import (
    DASHBOARD_CARD_MARGINS,
    DASHBOARD_CARD_SPACING,
    DASHBOARD_HEADER_SPACING,
)
from tomography_session_browser.ui.widgets.dose_information_plot import DoseInformationScatterPlot
from tomography_session_browser.ui.theme import (
    SPACE_L,
    SPACE_M,
    SPACE_S,
    SPACE_XS,
    SPACE_XXL,
    TIER_LANDMARK,
    TIER_PRIMARY,
    TIER_SUPPORTING,
    apply_tier,
)
from tomography_session_browser.ui.fit_first import balanced_rows
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
from tomography_session_browser.ui.widgets.stat_card import StatCard, is_zero_value
from tomography_session_browser.ui.widgets.time_chart import (
    TIME_CHART_DEFOCUS_MIN_HEIGHT,
    TIME_CHART_TIMELINE_MIN_HEIGHT,
)
from tomography_session_browser.ui.widgets.timeline_strip import TimelineStrip

LOGGER = logging.getLogger(__name__)

# Retained as a public layout contract for downstream tests/extensions. Warning
# rows now word-wrap instead of enforcing this width directly.
_WARNING_LABEL_MIN_WIDTH = 88

# Height of every item in the dashboard header's meta row: the copy button's
# size, so a row holding the path and one without it line up.
_META_ROW_HEIGHT = 28

# Very wide screens (plan D3). From this content width (about a 2400 px
# workspace, less the page margins and scroll bar) the timeline and defocus
# charts share a row instead of each spanning ~3000 px at 4K.
SIDE_BY_SIDE_CHARTS_MIN_PX = 2360
# Rows of text with an action or outcome at their far end (warnings, the
# most affected search maps) stop here, so at 4K a "View" button sits by its
# message rather than ~500 px away. Rows stay left-aligned.
READABLE_ROW_MAX_PX = 720
# A rebuilt dashboard says so: a veil lifts off the view, starting this
# opaque (the old card fades began at 72%).
DASHBOARD_REFILL_VEIL_OPACITY = 0.28
DASHBOARD_REFILL_VEIL_MS = 160


# Tab targets for the click-through navigation. These mirror the labels in
# ``main_window.TAB_LABELS`` and are intentionally kept here rather than
# imported to keep this file free of cross-module coupling.
_TAB_BY_LABEL = {
    "Atlases": "Atlas",
    "Overviews": "Overview",
    "Search maps": "Search map",
    "Batch positions": "Batch position",
    "Tilt series": "Tilt series",
}


def theme_safe_text_on(color: str) -> str:
    """Choose dark or light text for a filled status/accent pill."""

    value = color.strip().lstrip("#")
    if len(value) != 6:
        from tomography_session_browser.ui.theme import current_palette

        return current_palette().text_strong
    from tomography_session_browser.ui.theme import current_palette

    theme = current_palette()
    red = int(value[0:2], 16) / 255.0
    green = int(value[2:4], 16) / 255.0
    blue = int(value[4:6], 16) / 255.0
    luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    return theme.safe_text_dark if luminance >= 0.52 else theme.safe_text_light


def _translucent(color: str, alpha: int) -> str:
    """Return ``color`` as an ``rgba(...)`` string for a soft chip fill."""

    value = color.strip().lstrip("#")
    if len(value) != 6:
        return "transparent"
    red, green, blue = (int(value[index : index + 2], 16) for index in (0, 2, 4))
    return f"rgba({red}, {green}, {blue}, {alpha})"


def _status_colors() -> dict[str, str]:
    """Palette-aware dashboard status colours from the Slate & Sage guide."""

    from tomography_session_browser.ui.theme import current_palette

    theme = current_palette()
    return {
        # New tilt-series outcome labels (driven from
        # ``services.tilt_series_validation``).
        "Complete": theme.chart_green,
        "Incomplete": theme.chart_amber,
        "Failed": theme.chart_red,
        "Unknown": theme.unknown,
        # Header completeness pill / warning row severities.
        "complete": theme.chart_green,
        "warning": theme.chart_amber,
        "error": theme.chart_red,
        "failed": theme.chart_red,
        "missing": theme.chart_red,
        "neutral": theme.unknown,
    }


#: Status vocabulary to tone (``theme.DASHBOARD_TONE_COLOURS``), matching
#: ``_status_colors`` key for key.
_STATUS_TONES = {
    "Complete": "good",
    "Incomplete": "warn",
    "Failed": "bad",
    "Unknown": "unknown",
    "complete": "good",
    "warning": "warn",
    "error": "bad",
    "failed": "bad",
    "missing": "bad",
    "neutral": "unknown",
}


def _status_tone(key: str | None, fallback: str = "unknown") -> str:
    """The tone a status key is shown in; ``fallback`` for unknown keys."""

    if not key:
        return fallback
    return _STATUS_TONES.get(key, fallback)


def _status_dot(tone: str, *, compact: bool = False, width: int = 12) -> QLabel:
    """A status glyph whose colour is data and whose size is style.

    The colour varies per row, so the dot carries its ``tone`` and the style
    sheet colours it: an inline colour baked the palette in, and a theme
    switch had to rebuild the dashboard to recolour it.
    """

    dot = QLabel("●")
    dot.setObjectName("statusDot")
    dot.setProperty("compact", bool(compact))
    dot.setProperty("tone", tone)
    dot.setFixedWidth(width)
    return dot


def _progress_row_name(text: str, *, compact: bool) -> ElidedLabel:
    """The entity name on a dashboard progress row: primary content."""

    name = ElidedLabel(text)
    name.setObjectName("progressRowName")
    name.setProperty("compact", bool(compact))
    apply_tier(name, TIER_PRIMARY)
    name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    name.setMinimumWidth(_ROW_LABEL_MIN_WIDTH_COMPACT if compact else _ROW_LABEL_MIN_WIDTH)
    return name


def _status_color(key: str | None, fallback: str | None = None) -> str:
    from tomography_session_browser.ui.theme import current_palette

    theme = current_palette()
    if not key:
        return fallback or theme.unknown
    return _status_colors().get(key, fallback or theme.unknown)


class _CardBox(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("dashboardCard")
        # Cards expand horizontally to fill the dashboard column but keep
        # their content-driven height. ``MinimumExpanding`` together with the
        # caller-set minimum height keeps a card from shrinking below its
        # content-needed size while still letting the layout balance widths
        # consistently across rebuilds.
        self.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Preferred)


class _ProgressStrip(QWidget):
    """Small multi-segment progress bar used by compact summary cards."""

    def __init__(
        self,
        row: SampleProgressModel | BatchPositionProgressModel,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._row = row
        self.setFixedHeight(8)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt signature
        from tomography_session_browser.ui.theme import current_palette

        theme = current_palette()
        colors = [
            (self._row.complete, QColor(theme.chart_green)),
            (self._row.warning, QColor(theme.chart_amber)),
            (self._row.failed, QColor(theme.chart_red)),
            (self._row.queued, QColor(theme.border_strong)),
        ]
        total = max(self._row.total, 1)
        rect = QRectF(self.rect())
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.surface_hi))
            painter.drawRoundedRect(rect, 4, 4)
            x = rect.left()
            for count, color in colors:
                if count <= 0:
                    continue
                width = rect.width() * (count / total)
                segment = QRectF(x, rect.top(), width, rect.height())
                painter.setBrush(color)
                painter.drawRoundedRect(segment, 4, 4)
                x += width
        finally:
            painter.end()


class _DoseSummaryPanel(QWidget):
    """Responsive metric block for the Dose Information card.

    The card itself supplies the heading via the standard
    :func:`_card_header` (matching the Defocus Readout card); this panel
    only renders the median / range metric pair and an optional warning
    chip. The "(e⁻/Å²)" unit is shown next to each metric label so the
    information is preserved even without a verbose card heading.
    """

    TWO_COLUMN_MIN_WIDTH = 560
    UNIT_LABEL = "e⁻/Å²"

    def __init__(
        self,
        *,
        median_value: str,
        range_value: str,
        source_label: str,
        status: str,
        status_reason: str | None,
        note: str | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._median_value = median_value
        self._range_value = range_value
        self._source_label = source_label
        self._status = status
        self._status_reason = status_reason
        self._note = note
        self._stacked = False

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(SPACE_M)

        self._metrics_wrap = QWidget(self)
        self._layout.addWidget(self._metrics_wrap)
        self._chip: QLabel | None = None
        if status == "warning" and status_reason:
            self._chip = QLabel(status_reason.title(), self)
            self._chip.setObjectName("searchMapSummaryChip")
            self._chip.setToolTip(note or status_reason.title())
            self._chip.setProperty("tone", _status_tone("warning"))
            self._chip.setProperty("spaced", True)
            self._layout.addWidget(self._chip, alignment=Qt.AlignmentFlag.AlignLeft)

        self._rebuild_metrics(stacked=False)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt signature
        super().resizeEvent(event)
        visible_width = min(self.width(), self._visible_viewport_width())
        stacked = visible_width < self.TWO_COLUMN_MIN_WIDTH
        if stacked != self._stacked:
            self._rebuild_metrics(stacked=stacked)

    def _visible_viewport_width(self) -> int:
        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                return parent.viewport().width()
            parent = parent.parentWidget()
        return self.width()

    def _rebuild_metrics(self, *, stacked: bool) -> None:
        old_layout = self._metrics_wrap.layout()
        if old_layout is not None:
            while old_layout.count():
                item = old_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
            QWidget().setLayout(old_layout)
        self._stacked = stacked

        if stacked:
            layout = QVBoxLayout(self._metrics_wrap)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(SPACE_S)
            layout.addWidget(_dose_metric_widget("Median", self._median_value, stacked=True))
            layout.addWidget(_dose_metric_widget("Range", self._range_value, stacked=True))
            return

        layout = QHBoxLayout(self._metrics_wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_XXL)
        layout.addWidget(_dose_metric_widget("Median", self._median_value), stretch=0)
        layout.addWidget(_dose_metric_widget("Range", self._range_value), stretch=0)
        layout.addStretch(1)


class _SampleRowWidget(QWidget):
    clicked = Signal(str)

    def __init__(self, sample_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sample_id = sample_id
        self.setObjectName("dashboardInteractiveRow")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Open dashboard item")

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt signature
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._sample_id)
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt signature
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space}:
            self.clicked.emit(self._sample_id)
            event.accept()
            return
        super().keyPressEvent(event)


class _DashboardPage(QWidget):
    """The dashboard's scrolling page, opaque (plan F5.5).

    Opaque, Qt scrolls the page by moving pixels it already has and paints
    only the strip coming into view; otherwise every step of an eased wheel
    scroll repainted every card and chart (39 ms frames natively, against
    16.5 ms). It paints its own background, because the style sheet's pass
    over a scroll area's page clears ``autoFillBackground``.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt signature
        from tomography_session_browser.ui.theme import current_palette

        QPainter(self).fillRect(event.rect(), QColor(current_palette().background))


class _SideBySideRow(QWidget):
    """Two cards in a row when the page is very wide, stacked otherwise (D3).

    Switches the layout's direction rather than rebuilding it, like the
    context header, so the cards (and their plots) are never recreated.
    """

    def __init__(self, cards: list[QWidget], *, min_width: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._min_width = min_width
        self._layout = QBoxLayout(QBoxLayout.Direction.TopToBottom, self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        # Stacked, they are sections on a page; side by side, cards in a row.
        self._layout.setSpacing(SPACE_L)
        for card in cards:
            self._layout.addWidget(card, stretch=1)
        self._side_by_side = False

    def is_side_by_side(self) -> bool:
        return self._side_by_side

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt signature
        super().resizeEvent(event)
        side_by_side = event.size().width() >= self._min_width
        if side_by_side != self._side_by_side:
            self._side_by_side = side_by_side
            self._layout.setDirection(
                QBoxLayout.Direction.LeftToRight if side_by_side else QBoxLayout.Direction.TopToBottom
            )
            self._layout.setSpacing(SPACE_M if side_by_side else SPACE_L)


class _ResponsiveCardGrid(QWidget):
    """Reflow dashboard cards into balanced rows without horizontal scrolling.

    Rows are split as evenly as the column count allows, and a shorter row
    stretches to the full width (plan C4): five cards in three columns used
    to leave an empty sixth slot. Each row spans the same grid, so every row
    fills the width: with rows of 3 and 2 the grid has 6 columns, each card
    of the first row spans 2 and each of the second spans 3.
    """

    def __init__(self, cards: list[QWidget], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cards = list(cards)
        self._columns = 0
        self._layout: QGridLayout | None = None
        self._reflow(self._column_count_for_width(self.width()))

    @staticmethod
    def _column_count_for_width(width: int) -> int:
        if width >= 1120:
            return 5
        if width >= 680:
            return 3
        if width >= 440:
            return 2
        return 1

    def _reflow(self, columns: int) -> None:
        rows = balanced_rows(len(self._cards), columns) or [0]
        if rows[0] == self._columns and self._layout is not None:
            return
        # A fresh grid each time: stale columns from a wider arrangement
        # would otherwise keep their spacing. Empty the old grid first, so
        # moving it onto a throwaway widget (which detaches it from this one)
        # does not take the cards with it.
        if self._layout is not None:
            while self._layout.count():
                self._layout.takeAt(0)
            QWidget().setLayout(self._layout)
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(SPACE_M)
        layout.setVerticalSpacing(SPACE_M)
        span_total = math.lcm(*rows) if rows[0] else 1
        cards = iter(self._cards)
        for row, size in enumerate(rows):
            span = span_total // max(size, 1)
            for position in range(size):
                layout.addWidget(next(cards), row, position * span, 1, span)
        for column in range(span_total):
            layout.setColumnStretch(column, 1)
        self._layout = layout
        self._columns = rows[0]

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt signature
        super().resizeEvent(event)
        self._reflow(self._column_count_for_width(event.size().width()))

    def column_count(self) -> int:
        """Return the active column count for regression tests."""

        return self._columns


class _SearchMapOverviewTableModel(QAbstractTableModel):
    """Virtual table model for large search-map overview lists."""

    _HEADERS = ("Search map", "Status", "Batch positions")

    def __init__(self, rows: list[SearchMapOverviewRowModel], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows = list(rows)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self._HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        column = index.column()
        if role == Qt.ItemDataRole.ToolTipRole:
            return _search_map_overview_tooltip(row)
        if role == Qt.ItemDataRole.TextAlignmentRole and column != 0:
            return Qt.AlignmentFlag.AlignCenter
        if role == Qt.ItemDataRole.ForegroundRole:
            return QColor(_status_color(row.status))
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if column == 0:
            return row.label
        if column == 1:
            return _search_map_status_label(row)
        if column == 2:
            return row.summary
        return None

    def row(self, index: int) -> SearchMapOverviewRowModel | None:
        if 0 <= index < len(self._rows):
            return self._rows[index]
        return None

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:  # noqa: N802
        reverse = order == Qt.SortOrder.DescendingOrder
        key_funcs = {
            0: lambda row: row.label.casefold(),
            1: lambda row: _search_map_status_label(row),
            2: lambda row: (
                row.acquired_batch_positions,
                row.batch_positions,
            ) if row.acquisition_associated else (-1, -1),
        }
        self.layoutAboutToBeChanged.emit()
        self._rows.sort(key=key_funcs.get(column, key_funcs[0]), reverse=reverse)
        # Keep non-acquisition maps at the bottom even when users sort table
        # columns. They are contextual records, not completion failures.
        self._rows.sort(key=lambda row: not row.acquisition_associated)
        self.layoutChanged.emit()


class _DoseInformationTableModel(QAbstractTableModel):
    """Virtual detail table for dose points, created only when the card exists."""

    _HEADERS = ("Tilt series", "Frame", "Dose e⁻/Å²", "Tilt", "Acquisition", "Source")

    def __init__(self, rows: list[DoseInformationPointModel], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows = list(rows)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self._HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        column = index.column()
        if role == Qt.ItemDataRole.ToolTipRole:
            return dose_information_point_tooltip(row)
        if role == Qt.ItemDataRole.TextAlignmentRole and column in {1, 2, 3}:
            return Qt.AlignmentFlag.AlignCenter
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if column == 0:
            return row.tilt_series_name
        if column == 1:
            return f"{row.frame_index}/{row.frame_count or '?'}"
        if column == 2:
            return _format_dose(row.dose_e_per_angstrom2)
        if column == 3:
            return "n/a" if row.tilt_angle is None else f"{row.tilt_angle:g}"
        if column == 4:
            if row.acquisition_time is not None:
                return row.acquisition_time.isoformat(sep=" ", timespec="seconds")
            return f"Frame order {row.frame_order}"
        if column == 5:
            return row.metadata_source
        return None


def _status_label(status: str) -> str:
    labels = {
        "complete": "complete",
        "warning": "incomplete",
        "failed": "failed",
        "missing": "missing",
        "neutral": "unknown",
    }
    return labels.get(status, status or "unknown")


def _search_map_status_label(row: SearchMapOverviewRowModel) -> str:
    if not row.acquisition_associated:
        return "N/A"
    return _status_label(row.status)


# Floor widths for the identifying label in dashboard list rows. Without a
# floor these labels sit next to a fixed-width status chip and collapse to a
# single character plus an ellipsis ("o…", "R…"), which makes the sample and
# batch-position cards unreadable. The chip is the part that can afford to
# lose characters, not the name.
#: "N/A" and "unknown" are opaque on their own — say what they mean.
_SEARCH_MAP_STATUS_TOOLTIPS = {
    "complete": "Every batch position on this search map produced a tilt series.",
    "incomplete": "Some batch positions on this search map are still missing a tilt series.",
    "failed": "At least one batch position on this search map failed.",
    "unknown": "Not enough metadata to judge whether acquisition completed.",
    "N/A": "No batch positions or tilt series are associated with this search map, "
    "so there is nothing to complete.",
}

_ROW_LABEL_MIN_WIDTH = 132
_ROW_LABEL_MIN_WIDTH_COMPACT = 86


def _batch_outcome_chip_parts(row_model: BatchPositionProgressModel) -> list[str]:
    """Return the non-zero outcome phrases for a batch-position row chip.

    Always yields at least one phrase so a row with no recorded outcomes
    still reads as "0 complete" rather than showing an empty chip.
    """

    parts: list[str] = []
    if row_model.complete or not (row_model.failed or row_model.warning):
        parts.append(f"{row_model.complete} complete")
    if row_model.failed:
        parts.append(f"{row_model.failed} failed")
    if row_model.warning:
        parts.append(f"{row_model.warning} partial")
    return parts


def _search_map_overview_tooltip(row: SearchMapOverviewRowModel) -> str:
    lines = [row.label, f"Status: {_search_map_status_label(row)}", row.summary]
    if not row.acquisition_associated:
        lines.append("No batch positions or tilt series are associated with this search map.")
    return "\n".join(lines)


def _format_dose(value: float | None) -> str:
    if value is None:
        return "n/a"
    if abs(value) >= 100:
        return f"{value:.0f}"
    if abs(value) >= 10:
        return f"{value:.1f}"
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _dose_metric_widget(label: str, value: str, *, stacked: bool = False) -> QWidget:
    wrap = QWidget()
    wrap.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
    if not stacked:
        wrap.setMinimumWidth(170)
        wrap.setMaximumWidth(240)
    layout = QVBoxLayout(wrap)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(SPACE_XS)

    title = QLabel(label)
    title.setObjectName("metaKey")
    title.setWordWrap(False)
    layout.addWidget(title)

    number = QLabel(value)
    number.setObjectName("healthValue")
    number.setToolTip(value)
    number.setWordWrap(False)
    number.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    number.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Preferred)
    layout.addWidget(number)
    return wrap


def _card_with_title(title: str) -> tuple[_CardBox, QVBoxLayout]:
    card = _CardBox()
    layout = QVBoxLayout(card)
    layout.setContentsMargins(*DASHBOARD_CARD_MARGINS)
    layout.setSpacing(DASHBOARD_CARD_SPACING)
    title_label = QLabel(title.upper())
    title_label.setObjectName("cardTitle")
    apply_tier(title_label, TIER_SUPPORTING)
    title_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    layout.addWidget(title_label)
    return card, layout


def _card_layout(card: _CardBox) -> QVBoxLayout:
    layout = QVBoxLayout(card)
    layout.setContentsMargins(*DASHBOARD_CARD_MARGINS)
    layout.setSpacing(DASHBOARD_CARD_SPACING)
    return layout


class _FlowLayout(QLayout):
    """Left-to-right layout that wraps onto a new line when it runs out of width.

    Qt ships no flow layout. A ``QHBoxLayout`` of fixed-size chips reports
    the sum of their widths as its minimum, so one row of five status chips
    was enough to push the whole dashboard wider than its scroll viewport.

    An item wider than a whole line is given the line's width rather than
    overflowing it, so an eliding label inside it (a long path) elides.
    ``line_spacing`` separates wrapped lines; it defaults to ``spacing``.
    """

    def __init__(self, *, spacing: int = 8, line_spacing: int | None = None) -> None:
        super().__init__()
        self._items: list[QLayoutItem] = []
        self.setContentsMargins(0, 0, 0, 0)
        self._gap = spacing
        self._line_gap = spacing if line_spacing is None else line_spacing

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802 - Qt API
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 - Qt API
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 - Qt API
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientations:  # noqa: N802 - Qt API
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt API
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt API
        return self._layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 - Qt API
        super().setGeometry(rect)
        self._layout(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802 - Qt API
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _layout(self, rect: QRect, *, apply: bool) -> int:
        margins = self.contentsMargins()
        line_left = rect.x() + margins.left()
        x = line_left
        y = rect.y() + margins.top()
        right = rect.right() - margins.right()
        line_height = 0
        for item in self._items:
            hint = item.sizeHint()
            width = max(0, min(hint.width(), right - line_left + 1))
            if line_height and x + width - 1 > right:
                x = line_left
                y += line_height + self._line_gap
                line_height = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), QSize(width, hint.height())))
            x += width + self._gap
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()


def _card_header(title: str, badge: str | None = None, tooltip: str | None = None) -> tuple[QHBoxLayout, QLabel | None]:
    header = QHBoxLayout()
    header.setContentsMargins(0, 0, 0, 0)
    header.setSpacing(DASHBOARD_HEADER_SPACING)
    title_label = QLabel(title.upper())
    title_label.setObjectName("cardTitle")
    title_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    header.addWidget(title_label, alignment=Qt.AlignmentFlag.AlignTop)
    header.addStretch(1)
    badge_label: QLabel | None = None
    if badge is not None:
        badge_label = QLabel(badge)
        badge_label.setObjectName("sampleCount")
        if tooltip:
            badge_label.setToolTip(tooltip)
        header.addWidget(badge_label)
    return header, badge_label


class SessionDashboard(QWidget):
    """The widget that replaces the old ``QTreeWidget`` Session-tab summary."""

    navigate_requested = Signal(str)
    search_map_clicked = Signal(str)
    sample_clicked = Signal(str)
    filter_requested = Signal(str, str, str)  # (destination tab, query, reason)
    point_clicked = Signal(str, object)  # tilt_series_id, 1-based frame_index | None
    point_double_clicked = Signal(str, object)  # tilt_series_id, 1-based frame_index | None
    timeline_tilt_series_requested = Signal(str, object)  # tilt_series_id, 1-based frame_index | None
    highlight_clear_requested = Signal()
    warning_details_requested = Signal(str, object)  # category, raw messages
    # Generic per-tile selection: emitted with the destination tab label and
    # the item id when the user clicks a status tile on any of the four
    # count cards. The main window resolves the id back to the underlying
    # entity and selects it in the appropriate viewer.
    tile_item_clicked = Signal(str, str)  # (destination, item_id)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sessionDashboard")
        self._context_stack: ContextStack | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("dashboardScroll")
        self._scroll.viewport().setObjectName("dashboardViewport")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        SmoothScroller(self._scroll)  # eased wheel scrolling (plan F5.5)
        outer.addWidget(self._scroll)

        self._content = _DashboardPage()
        self._content.setObjectName("dashboardContent")
        # The page is always exactly the viewport's width. QScrollArea sizes
        # its widget to the viewport but never below the widget's minimum
        # width, the aggregate of every wide row; ignoring it horizontally
        # means that minimum can never hold the page wider than what shows.
        # Pinning the maximum to the dashboard's own width (the old fix) left
        # the page 11 px too wide whenever the vertical scroll bar appeared,
        # until some later layout pass let it go: the cards then shifted left
        # a moment after a load had finished.
        self._content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._scroll.setWidget(self._content)
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self._content_layout.setContentsMargins(SPACE_L, SPACE_L, SPACE_L, SPACE_L)
        self._content_layout.setSpacing(SPACE_L)

        self._placeholder = QLabel("Open a session folder to review acquisition health, warnings, and linked data.")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Use the QSS-styled ``metaPlain`` object name instead of hard-coding
        # a colour — keeps the placeholder readable on both themes.
        self._placeholder.setObjectName("metaPlain")
        self._placeholder.setStyleSheet("padding: 60px;")
        self._content_layout.addWidget(self._placeholder)
        self._content_layout.addStretch(1)
        self._timeline_display_mode = "auto"
        self._model: DashboardModel | None = None
        self._highlighted_tilt_series_id: str | None = None
        self._timeline_strip: TimelineStrip | None = None
        self._defocus_plot: DefocusScatterPlot | None = None
        self._dose_plot: DoseInformationScatterPlot | None = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Session dashboard")
        self.setAccessibleDescription(
            "Acquisition summary. Press Escape to clear a highlighted tilt series."
        )

    # ------------------------------------------------------------------ API

    def set_model(
        self,
        model: DashboardModel | None,
        timeline: SessionTimeline | None = None,
        *,
        animate: bool = False,
        defer_heavy_cards: bool = False,
        highlighted_tilt_series_id: str | None = None,
    ) -> None:
        # Wipe the previous content and rebuild from scratch. The dashboard
        # is rebuilt only on session load / refresh, so the simpler clear &
        # rebuild flow is fine.
        self._clear()
        if model is None:
            self._model = None
            self._highlighted_tilt_series_id = None
            self._placeholder.show()
            self._content_layout.addWidget(self._placeholder)
            self._content_layout.addStretch(1)
            return
        self._placeholder.hide()
        self._model = model
        self._highlighted_tilt_series_id = self._normalise_highlight_id(model, highlighted_tilt_series_id)

        self._content_layout.addWidget(self._build_header(model))

        # Section ordering depends on what the session actually contains. We
        # keep collection scopes dense and time-oriented: metric cards first,
        # then full-width acquisition timeline, then full-width applied
        # defocus. Atlas-only sessions still use the richer atlas summary.
        if not model.has_atlases and not model.has_collection_data:
            empty = QLabel(
                "Session loaded, but no atlases, overviews, search maps, batch positions, or "
                "tilt series were detected. Warnings below may explain what metadata was available."
            )
            empty.setObjectName("metaPlain")
            empty.setWordWrap(True)
            empty.setStyleSheet("padding: 24px;")
            self._content_layout.addWidget(empty)

        if not model.has_collection_data and model.has_atlases and model.atlas_summary is not None:
            self._content_layout.addWidget(self._build_atlas_summary_card(model.atlas_summary))

        if model.has_collection_data:
            self._content_layout.addWidget(self._build_counts_row(model))
            timeline_card = self._build_timeline_card(model, timeline)
            if not defer_heavy_cards and model.defocus_readout.points:
                # Both are time-based charts; on a very wide page they share
                # a row (plan D3), and stack as before otherwise.
                self._content_layout.addWidget(
                    _SideBySideRow(
                        [timeline_card, self._build_defocus_readout_card(model)],
                        min_width=SIDE_BY_SIDE_CHARTS_MIN_PX,
                    )
                )
            else:
                self._content_layout.addWidget(timeline_card)
            if not defer_heavy_cards:
                if model.dose_information.has_points:
                    self._content_layout.addWidget(self._build_dose_information_card(model))
            else:
                LOGGER.debug(
                    "dashboard heavy cards deferred defocus_points=%d dose_points=%d",
                    len(model.defocus_readout.points),
                    len(model.dose_information.points),
                )

            lower_cards: list[QWidget] = []
            if model.search_map_overview.total:
                lower_cards.append(
                    self._build_search_maps_overview_card(model.search_map_overview)
                )
            if model.batch_positions:
                lower_cards.append(
                    self._build_batch_positions_card(model)
                )
            lower_cards.append(self._build_warnings_card(model))
            self._content_layout.addWidget(_ResponsiveCardGrid(lower_cards))
        if not model.has_collection_data:
            self._content_layout.addWidget(self._build_warnings_card(model))
        self._content_layout.addStretch(1)
        if animate:
            QTimer.singleShot(0, self._animate_loaded_content)

    def refresh_theme(self) -> None:
        """Recolour the dashboard in place after a theme switch.

        The style sheet recolours what it styles, statuses included (they
        carry a ``tone``); painted parts read the palette when they paint.
        What is left is drawn once at build time: icons, the stat cards'
        status dots and tile colours, and the completion bars. A switch used
        to rebuild the whole dashboard for these (~150 ms with its clean-up).
        """

        for icon in self.findChildren(QLabel, "metaIcon"):
            name = icon.property("iconName")
            if isinstance(name, str):
                icon.setPixmap(themed_icon(name, size=14).pixmap(QSize(14, 14)))
        for button in self.findChildren(QToolButton, "metaCopyButton"):
            button.setIcon(themed_icon("copy", size=14))
        for card in self.findChildren(StatCard):
            card.refresh_theme()
        for bar in self.findChildren(CompletionBar):
            bar.refresh_theme()
        for widget in self.findChildren(QWidget):
            widget.update()

    def _animate_loaded_content(self) -> None:
        """Say the dashboard now shows another scope: a veil lifts off it.

        One veil over the view. It was a staggered fade of up to seven cards,
        each through an opacity effect that rendered the card, charts and
        all, off screen every frame (plan principle 8).
        """

        from tomography_session_browser.ui.theme import current_palette

        veil(
            self._scroll.viewport(),
            QColor(current_palette().background),
            start_opacity=DASHBOARD_REFILL_VEIL_OPACITY,
            duration_ms=DASHBOARD_REFILL_VEIL_MS,
        )

    # ------------------------------------------------------------------ build

    def _clear(self) -> None:
        # Remove every child widget from the content layout. ``takeAt`` on the
        # outer layout returns layout items; we deleteLater on their widgets.
        self._timeline_strip = None
        self._defocus_plot = None
        self._dose_plot = None
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None and widget is not self._placeholder:
                widget.deleteLater()

    def set_context_stack(self, stack: ContextStack | None) -> None:
        """Supply the review scope shown above the dashboard title."""

        self._context_stack = stack

    def _context_eyebrow_text(self, title: str = "") -> str:
        """The review scope, unless the title below already says it.

        The context header sits above every page including this one, and the
        dashboard title is usually the scope's own name — so on a session-scope
        dashboard an eyebrow repeating it made the same words appear three
        times within two lines. It earns its place when the title is something
        narrower than the scope (a sample inside a linked group), and stays out
        of the way when it is not.
        """

        stack = getattr(self, "_context_stack", None)
        if stack is None or not stack.scope_label:
            return ""
        # Compare the bare scope name, not the badged label: the badge is
        # already on the context header and in the project tree, so it is not
        # on its own a reason to repeat the name.
        if title and stack.scope.casefold() == title.casefold():
            return ""
        return stack.scope_label.upper()

    def _build_header(self, model: DashboardModel) -> QWidget:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_S)

        title_row = QHBoxLayout()
        title_row.setSpacing(SPACE_M)
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(SPACE_XS)
        # The eyebrow used to read a hard-coded "PROJECT · SESSION" on every
        # screen, which told the reviewer nothing about where they were. It
        # now shows the real review scope supplied by the context stack.
        eyebrow_text = self._context_eyebrow_text(model.title)
        eyebrow = QLabel(eyebrow_text)
        eyebrow.setObjectName("screenEyebrow")
        apply_tier(eyebrow, TIER_SUPPORTING)
        eyebrow.setToolTip(eyebrow_text)
        eyebrow.setVisible(bool(eyebrow_text))
        left.addWidget(eyebrow)
        title = ElidedLabel(model.title)
        title.setObjectName("screenTitle")
        apply_tier(title, TIER_LANDMARK)
        title.setToolTip(model.title)
        left.addWidget(title)
        title_row.addLayout(left, stretch=1)
        layout.addLayout(title_row)

        # One meta row (plan C2): the stacked caps labels cost ~220 px of
        # height and put the copy button a screen-width away from the path.
        # Groups wrap as whole units, and the path elides in the middle once
        # it has a line to itself.
        meta_wrap = QWidget()
        meta_wrap.setObjectName("dashboardMetaRow")
        meta_row = _FlowLayout(spacing=SPACE_L, line_spacing=SPACE_XS)
        meta_wrap.setLayout(meta_row)
        if model.kind:
            kind_icon = (
                "link"
                if "linked" in model.kind.casefold()
                else "session-dashboard"
            )
            meta_row.addWidget(self._meta_item("Kind", model.kind, icon_name=kind_icon))
        if model.microscope:
            meta_row.addWidget(self._meta_item("Microscope", model.microscope, icon_name="microscope"))
        if model.acquisition_start and model.acquisition_end:
            window_text = f"{model.acquisition_start}  →  {model.acquisition_end}"
            if model.acquisition_duration:
                window_text += f"  ({model.acquisition_duration})"
            meta_row.addWidget(self._meta_item("Acquisition", window_text, icon_name="clock"))

        copy = QToolButton()
        copy.setObjectName("metaCopyButton")
        copy.setIcon(themed_icon("copy", size=14))
        copy.setIconSize(QSize(14, 14))
        copy.setToolTip("Copy session path")
        copy.setAccessibleName("Copy session path")
        copy.setFixedSize(_META_ROW_HEIGHT, _META_ROW_HEIGHT)
        copy.setCursor(Qt.CursorShape.PointingHandCursor)
        copy.clicked.connect(lambda: self._copy(model.path, source=copy))
        meta_row.addWidget(
            self._meta_item(
                "Path",
                model.path,
                icon_name="folder-open",
                icon_accessible_name="Session folder",
                value_object_name="metaValuePath",
                elide_mode=Qt.TextElideMode.ElideMiddle,
                trailing=copy,
            )
        )
        layout.addWidget(meta_wrap)
        return wrap

    def _meta_item(
        self,
        label: str,
        value: str,
        *,
        icon_name: str,
        icon_accessible_name: str | None = None,
        value_object_name: str = "metaValue",
        elide_mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight,
        trailing: QWidget | None = None,
    ) -> QWidget:
        """One inline ``[icon] Key: value`` group of the header's meta row."""

        item = QWidget()
        item.setObjectName("dashboardMetaItem")
        item.setMinimumHeight(_META_ROW_HEIGHT)
        row = QHBoxLayout(item)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE_XS)
        row.addWidget(
            self._metadata_icon_label(icon_name, icon_accessible_name or label),
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )
        key = QLabel(f"{label}:")
        key.setObjectName("metaKey")
        row.addWidget(key)
        val = ElidedLabel(value, mode=elide_mode, prefer_full_width=True)
        val.setObjectName(value_object_name)
        val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        val.setAccessibleName(label)
        row.addWidget(val, stretch=1)
        if trailing is not None:
            row.addWidget(trailing)
        return item

    @staticmethod
    def _metadata_icon_label(icon_name: str, accessible_name: str) -> QLabel:
        icon = QLabel()
        icon.setObjectName("metaIcon")
        icon.setProperty("iconName", icon_name)
        icon.setPixmap(themed_icon(icon_name, size=14).pixmap(QSize(14, 14)))
        icon.setFixedSize(14, 16)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setAccessibleName(f"{accessible_name} icon")
        return icon

    def _build_collection_health_card(
        self,
        health: CollectionHealthModel,
        filters: list[DashboardFilterModel],
    ) -> QWidget:
        card, layout = _card_with_title("Collection health")
        layout.setSpacing(SPACE_M)

        stats = QGridLayout()
        stats.setContentsMargins(0, 0, 0, 0)
        stats.setHorizontalSpacing(SPACE_M)
        stats.setVerticalSpacing(SPACE_S)
        items = [
            ("Tilt series", f"{health.total_tilt_series:,}"),
            ("Complete", f"{health.complete:,}"),
            ("Incomplete", f"{health.incomplete:,}"),
            ("Failed", f"{health.failed:,}"),
            ("Unknown", f"{health.unknown:,}"),
            ("Search maps", f"{health.search_maps:,}"),
            ("Batch positions", f"{health.batch_positions:,}"),
            ("Samples", f"{health.samples:,}"),
        ]
        if health.acquisition_duration:
            items.append(("Duration", health.acquisition_duration))
        for index, (label, value) in enumerate(items):
            row = index // 5
            column = index % 5
            stats.addWidget(self._health_metric(label, value), row, column)
            stats.setColumnStretch(column, 1)
        layout.addLayout(stats)

        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(SPACE_S)
        for filter_model in filters:
            button = QToolButton()
            button.setObjectName("dashboardFilterButton")
            button.setText(f"{filter_model.label}  {filter_model.count:,}")
            button.setToolTip(filter_model.tooltip)
            button.setEnabled(filter_model.count > 0 or not filter_model.query)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(
                lambda _checked=False,
                tab=filter_model.tab,
                query=filter_model.query,
                reason=filter_model.tooltip: self.filter_requested.emit(tab, query, reason)
            )
            filter_row.addWidget(button)
        filter_row.addStretch(1)
        layout.addLayout(filter_row)

        if health.missing_mdoc or health.orphaned_failed_tilts:
            notes = []
            if health.missing_mdoc:
                notes.append(f"{health.missing_mdoc:,} tilt series without usable MDOC metadata")
            if health.orphaned_failed_tilts:
                notes.append(
                    f"{health.orphaned_failed_tilts:,} orphaned failed tilt series grouped by inferred batch position"
                )
            note = QLabel(" · ".join(notes))
            note.setObjectName("metaPlain")
            note.setWordWrap(True)
            layout.addWidget(note)
        return card

    def _health_metric(self, label: str, value: str) -> QWidget:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_XS)
        title = QLabel(label.upper())
        title.setObjectName("cardTitle")
        layout.addWidget(title)
        number = ElidedLabel(value)
        number.setObjectName("healthValue")
        number.setToolTip(value)
        layout.addWidget(number)
        return wrap

    def _build_counts_row(self, model: DashboardModel) -> QWidget:
        # Palette slots, not colours, so a card can recolour in place.
        accents = ("accent", "chart_green", "chart_amber", "chart_blue")
        cards: list[QWidget] = []
        empty: list[StatCardModel] = []
        for index, card_model in enumerate(model.counts):
            # Always populate the destination from the model when present;
            # fall back to a label-driven map so older models still navigate.
            if not card_model.destination:
                card_model.destination = _TAB_BY_LABEL.get(card_model.label, card_model.label)
            if is_zero_value(card_model.value):
                empty.append(card_model)
                continue
            # The accent follows the card's place in the model, so a card keeps
            # its colour whether or not an earlier card was folded away.
            card = self._build_stat_card(card_model, accents[index % len(accents)])
            cards.append(card)
        if not empty:
            return _ResponsiveCardGrid(cards)
        # Plan C8: a full card saying "0 · No entries in this scope" three
        # times over is noise. Zero counts are still stated, on one line.
        wrap = QWidget()
        stack = QVBoxLayout(wrap)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(SPACE_S)
        if cards:
            stack.addWidget(_ResponsiveCardGrid(cards))
        stack.addWidget(self._zero_counts_line(empty))
        return wrap

    @staticmethod
    def _zero_counts_line(empty: list[StatCardModel]) -> QLabel:
        # U+00A0 inside each entry, so a wrap falls between entries only.
        entries = (f"{card.label} {card.value}".replace(" ", " ") for card in empty)
        text = " · ".join(entries) + " — none in this scope"
        line = QLabel(text)
        line.setObjectName("dashboardZeroCounts")
        apply_tier(line, TIER_SUPPORTING)
        line.setWordWrap(True)
        line.setAccessibleName(text)
        return line

    def _build_stat_card(self, card_model: StatCardModel, accent_role: str) -> StatCard:
        card = StatCard(model=card_model, accent_role=accent_role)
        card.clicked.connect(self.navigate_requested.emit)
        # Tile clicks navigate AND select the underlying item.
        card.tile_clicked.connect(self._on_stat_tile_clicked)
        return card

    def _on_stat_tile_clicked(self, item_id: str, destination: str) -> None:
        """Bubble tile clicks up so the main window can select the item."""

        # Always emit the generic signal so the main window can resolve the
        # id and select the underlying entity. We also keep the
        # search-map-specific signal for backwards compatibility with the
        # completion-bar card which only carries SearchMap rows.
        self.tile_item_clicked.emit(destination, item_id)
        if destination == "Search map":
            self.search_map_clicked.emit(item_id)

    def _build_defocus_readout_card(self, model: DashboardModel) -> QWidget:
        timer = QElapsedTimer()
        timer.start()
        card = _CardBox()
        card.setMinimumHeight(360)
        layout = _card_layout(card)

        header, coverage = _card_header(
            "DEFOCUS READOUT",
            f"{model.defocus_readout.defocus_tilt_series:,} / "
            f"{model.defocus_readout.total_tilt_series:,} tilt series",
            "Tilt series with per-image MRC or MDOC Defocus values / tilt series in this selection",
        )
        layout.addLayout(header)

        subtitle = QLabel("Per-image MRC Defocus, with angle-aligned MDOC fallback")
        subtitle.setObjectName("metaKey")
        subtitle.setWordWrap(True)
        subtitle.setToolTip(
            "The plot uses the Defocus value recorded for each image. The FEI MRC "
            "extended header is preferred because it is tied to the stack frame; "
            "angle-aligned MDOC Defocus is cross-checked and used as a fallback. "
            "TargetDefocus, application AppliedDefocus, and measured CTF defocus "
            "are different quantities and are not plotted."
        )
        layout.addWidget(subtitle)

        plot = DefocusScatterPlot()
        plot.setMinimumHeight(TIME_CHART_DEFOCUS_MIN_HEIGHT)
        plot.set_model(model.defocus_readout)
        plot.set_highlighted_tilt_series_id(self._highlighted_tilt_series_id)
        plot.pointClicked.connect(self.point_clicked.emit)
        plot.pointDoubleClicked.connect(self.point_double_clicked.emit)
        plot.highlightClearRequested.connect(self.highlight_clear_requested.emit)
        self._defocus_plot = plot
        layout.addWidget(plot, stretch=1)

        if model.defocus_readout.note:
            note = QLabel(model.defocus_readout.note)
            note.setObjectName("metaKey")
            note.setWordWrap(True)
            layout.addWidget(note)
        elif (
            model.defocus_readout.has_points
            and model.defocus_readout.timestamp_tilt_series
            < model.defocus_readout.defocus_tilt_series
        ):
            note = QLabel(
                f"Timestamps available for "
                f"{model.defocus_readout.timestamp_tilt_series:,} / "
                f"{model.defocus_readout.defocus_tilt_series:,} plotted tilt series"
            )
            note.setObjectName("metaKey")
            note.setWordWrap(True)
            layout.addWidget(note)

        LOGGER.debug(
            "defocus readout card built points=%d widget_ms=%d",
            len(model.defocus_readout.points),
            timer.elapsed(),
        )
        return card

    def _build_dose_information_card(self, model: DashboardModel) -> QWidget:
        timer = QElapsedTimer()
        timer.start()
        dose_model = model.dose_information
        card = _CardBox()
        card.setMinimumHeight(360)
        layout = _card_layout(card)

        source = dose_model.source_label or "MRC frame metadata"
        median_text = _format_dose(dose_model.median_dose_e_per_angstrom2)
        range_text = (
            f"{_format_dose(dose_model.min_dose_e_per_angstrom2)}–"
            f"{_format_dose(dose_model.max_dose_e_per_angstrom2)}"
        )
        # Match the Defocus Readout card heading shape (cardTitle + badge)
        # instead of the previous redundant "CAMERA DOSE PER IMAGE (e⁻/Å²)"
        # heading. A single subtitle carries the unit and metadata source,
        # mirroring the source subtitle on the Defocus Readout card.
        header, _badge = _card_header(
            "CAMERA DOSE",
            f"{dose_model.dose_tilt_series:,} / {dose_model.total_tilt_series:,} tilt series",
            f"Tilt series with dose metadata / tilt series in this selection. Source: {source}",
        )
        layout.addLayout(header)
        subtitle = QLabel(f"Per image, e⁻/Å² · {source}")
        subtitle.setObjectName("metaKey")
        subtitle.setWordWrap(True)
        subtitle.setToolTip(f"Source: {source}")
        layout.addWidget(subtitle)
        layout.addWidget(
            _DoseSummaryPanel(
                median_value=median_text,
                range_value=range_text,
                source_label=source,
                status=dose_model.status,
                status_reason=dose_model.status_reason,
                note=dose_model.note,
            )
        )

        plot = DoseInformationScatterPlot()
        plot.setMinimumHeight(TIME_CHART_DEFOCUS_MIN_HEIGHT)
        plot.set_model(dose_model)
        plot.set_highlighted_tilt_series_id(self._highlighted_tilt_series_id)
        plot.pointClicked.connect(self.point_clicked.emit)
        plot.pointDoubleClicked.connect(self.point_double_clicked.emit)
        plot.highlightClearRequested.connect(self.highlight_clear_requested.emit)
        self._dose_plot = plot
        layout.addWidget(plot, stretch=1)

        detail_container = QWidget()
        detail_layout = QVBoxLayout(detail_container)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(0)
        detail_container.setVisible(False)
        table: QTableView | None = None

        toggle = QToolButton()
        toggle.setObjectName("dashboardFilterButton")
        toggle.setCheckable(True)
        toggle.setText("View dose details")
        toggle.setToolTip("Build and show the plotted MRC dose metadata values for this card")

        def _toggle_details(checked: bool) -> None:
            nonlocal table
            if checked and table is None:
                table_timer = QElapsedTimer()
                table_timer.start()
                table = QTableView(detail_container)
                table.setObjectName("dashboardSearchMapTable")
                table.setModel(_DoseInformationTableModel(dose_model.points, table))
                table.setAlternatingRowColors(True)
                table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
                table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
                table.verticalHeader().hide()
                table.horizontalHeader().setStretchLastSection(True)
                table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
                for column in range(1, 6):
                    table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
                table.setMinimumHeight(220)
                detail_layout.addWidget(table)
                LOGGER.debug(
                    "dose details table built rows=%d widget_ms=%d",
                    len(dose_model.points),
                    table_timer.elapsed(),
                )
            detail_container.setVisible(checked)
            toggle.setText("Hide dose details" if checked else "View dose details")

        toggle.toggled.connect(_toggle_details)
        layout.addWidget(toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(detail_container)

        LOGGER.debug(
            "dose information card built plotted_points=%d table_built=False widget_ms=%d",
            len(dose_model.points),
            timer.elapsed(),
        )
        return card

    def _build_completion_card(self, model: DashboardModel) -> QWidget:
        card = _CardBox()
        layout = _card_layout(card)

        header, _count = _card_header("SEARCH MAP COMPLETION", _count_phrase(len(model.search_map_completion), "map"))
        layout.addLayout(header)

        card.setMinimumHeight(260)
        if not model.search_map_completion:
            empty = QLabel("No tile counts available for the search maps in this session.")
            empty.setObjectName("metaPlain")
            empty.setWordWrap(True)
            layout.addWidget(empty)
            layout.addStretch(1)
            return card

        visible = model.search_map_completion[:13]
        columns = 2 if len(visible) > 6 else 1
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(SPACE_M)
        grid.setVerticalSpacing(SPACE_XS)
        rows_per_column = (len(visible) + columns - 1) // columns if columns > 1 else len(visible)
        for index, sm_model in enumerate(visible):
            row = index % rows_per_column if columns > 1 else index
            column = index // rows_per_column if columns > 1 else 0
            grid.addWidget(self._build_completion_bar(sm_model), row, column)
            grid.setColumnStretch(column, 1)
        layout.addLayout(grid)

        if len(model.search_map_completion) > len(visible):
            more = QLabel(f"+{len(model.search_map_completion) - len(visible)} more search maps")
            more.setObjectName("metaKey")
            layout.addWidget(more)
        layout.addStretch(1)
        card.setMinimumHeight(330 if columns > 1 else 260)
        return card

    def _build_search_maps_overview_card(self, overview: SearchMapOverviewModel) -> QWidget:
        card = _CardBox()
        layout = _card_layout(card)

        header, _count = _card_header(
            "SEARCH MAPS OVERVIEW",
            _count_phrase(overview.total, "map"),
            tooltip=(
                "Search maps classified by whether their batch positions produced tilt series.\n"
                "The Search maps count card at the top of the dashboard classifies the same maps "
                "by whether the map's own tiles were acquired, so the two breakdowns differ."
            ),
        )
        layout.addLayout(header)

        subtitle = QLabel("Batch position completion per search map")
        subtitle.setObjectName("cardSubvalue")
        layout.addWidget(subtitle)

        # Five fixed-width chips in one row set a ~465px floor on this card,
        # which is most of why the lower dashboard row could not fit its
        # viewport. A flow layout lets them wrap on narrow windows instead of
        # forcing a horizontal scrollbar across the whole dashboard.
        summary = _FlowLayout(spacing=8)
        for label, value, status in (
            ("complete", overview.complete, "complete"),
            ("incomplete", overview.incomplete, "warning"),
            ("failed", overview.failed, "failed"),
            ("unknown", overview.unknown, "neutral"),
            ("N/A", overview.not_associated, "neutral"),
        ):
            chip = QLabel(f"{value:,} {label}")
            chip.setObjectName("searchMapSummaryChip")
            # A zero count is not a finding. Painting "0 incomplete" amber and
            # "0 failed" red drew the eye to the absence of a problem.
            chip.setProperty("tone", _status_tone(status if value else "neutral"))
            chip.setToolTip(_SEARCH_MAP_STATUS_TOOLTIPS.get(label, label))
            summary.addWidget(chip)
        layout.addLayout(summary)

        if overview.worst:
            worst_title = QLabel("MOST AFFECTED")
            worst_title.setObjectName("cardTitle")
            worst_title.setToolTip(
                "Search maps with a failed, incomplete, or unresolved batch position. "
                "Fully acquired maps are not listed here."
            )
            layout.addWidget(worst_title)
            for row in overview.worst:
                layout.addWidget(self._search_map_overview_row(row))
        elif overview.total:
            empty = QLabel("No search map in this scope has an outstanding batch position.")
            empty.setObjectName("metaPlain")
            empty.setWordWrap(True)
            layout.addWidget(empty)
        else:
            empty = QLabel("No search map completion metadata is available for this scope.")
            empty.setObjectName("metaPlain")
            empty.setWordWrap(True)
            layout.addWidget(empty)

        # Build the QTableView + its model lazily on first toggle click.
        # The previous code constructed both up-front and then hid the
        # widget; on large sessions that cost dashboard-build latency
        # every refresh, even when the user never opened the table. The
        # dose-details card already uses this lazy pattern — see
        # _build_dose_card above. Closure-captured ``table_ref`` lets us
        # both build on demand and re-show it on subsequent toggles.
        detail_container = QWidget()
        detail_layout = QVBoxLayout(detail_container)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(0)
        detail_container.setVisible(False)
        table_ref: list[QTableView | None] = [None]

        toggle = QToolButton()
        toggle.setObjectName("dashboardFilterButton")
        toggle.setCheckable(True)
        toggle.setText("Show all search maps")
        toggle.setToolTip("Open a virtualised table of every search map in this scope")

        def _toggle_search_map_details(checked: bool) -> None:
            if checked and table_ref[0] is None:
                build_timer = QElapsedTimer()
                build_timer.start()
                table = QTableView(detail_container)
                table.setObjectName("dashboardSearchMapTable")
                table.setModel(_SearchMapOverviewTableModel(overview.rows, table))
                table.setSortingEnabled(True)
                table.setAlternatingRowColors(True)
                table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
                table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
                table.verticalHeader().hide()
                table.horizontalHeader().setStretchLastSection(True)
                table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
                for column in range(1, 6):
                    table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
                table.setMinimumHeight(220)
                table.doubleClicked.connect(
                    lambda index, _table=table: self._search_map_table_activated(_table, index)
                )
                detail_layout.addWidget(table)
                table_ref[0] = table
                LOGGER.debug(
                    "search-map details table built rows=%d widget_ms=%d",
                    len(overview.rows),
                    build_timer.elapsed(),
                )
            detail_container.setVisible(checked)
            toggle.setText("Hide search map table" if checked else "Show all search maps")

        toggle.toggled.connect(_toggle_search_map_details)

        layout.addWidget(toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(detail_container)
        layout.addStretch(1)
        card.setMinimumHeight(330)
        return card

    def _search_map_overview_row(self, row: SearchMapOverviewRowModel) -> QWidget:
        wrap = _SampleRowWidget(row.id)
        wrap.clicked.connect(self.search_map_clicked.emit)
        wrap.setCursor(Qt.CursorShape.PointingHandCursor)
        wrap.setToolTip(_search_map_overview_tooltip(row))
        wrap.setAccessibleName(f"Open search map {row.label}")
        # The outcome stays by the map's name on a wide card (plan D3).
        wrap.setMaximumWidth(READABLE_ROW_MAX_PX)
        layout = QHBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_S)
        dot = _status_dot(_status_tone(row.status), compact=True)
        layout.addWidget(dot)
        label = ElidedLabel(row.label)
        label.setObjectName("metaValue")
        apply_tier(label, TIER_PRIMARY)
        label.setToolTip(row.label)
        layout.addWidget(label, stretch=1)
        status_text = {
            "done": "Complete",
            "complete": "Complete",
            "partial": "Incomplete",
            "incomplete": "Incomplete",
            "failed": "Failed",
            "missing": "Unavailable",
            "unknown": "Unavailable",
        }.get(row.status.strip().lower(), row.status.replace("_", " ").title())
        if status_text:
            status = QLabel(status_text)
            status.setObjectName("sampleCount")
            status.setProperty("tone", _status_tone(row.status))
            status.setToolTip(_search_map_overview_tooltip(row))
            layout.addWidget(status)
        summary = QLabel(row.summary)
        summary.setObjectName("metaPlain")
        summary.setToolTip(row.summary)
        layout.addWidget(summary)
        return wrap

    def _search_map_table_activated(self, table: QTableView, index: QModelIndex) -> None:
        model = table.model()
        if not isinstance(model, _SearchMapOverviewTableModel):
            return
        row = model.row(index.row())
        if row is not None:
            self.search_map_clicked.emit(row.id)

    def _build_samples_card(self, model: DashboardModel) -> QWidget:
        card = _CardBox()
        layout = _card_layout(card)

        header, _count = _card_header("SAMPLES", f"{model.sample_count} active")
        layout.addLayout(header)

        visible = model.samples[:13]
        columns = 2 if len(visible) > 6 else 1
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(SPACE_M)
        grid.setVerticalSpacing(SPACE_XS)
        rows_per_column = (len(visible) + columns - 1) // columns if columns > 1 else len(visible)
        for index, sample in enumerate(visible):
            row = index % rows_per_column if columns > 1 else index
            column = index // rows_per_column if columns > 1 else 0
            grid.addWidget(self._sample_row(sample, compact=columns > 1), row, column)
            grid.setColumnStretch(column, 1)
        layout.addLayout(grid)

        if len(model.samples) > len(visible):
            layout.addWidget(
                self._more_button(
                    _count_phrase(len(model.samples) - len(visible), "more sample"),
                    "Tilt series",
                    "Open the Tilt series tab to see every sample in this scope",
                )
            )
        layout.addStretch(1)
        card.setMinimumHeight(330 if columns > 1 else 260)
        return card

    def _sample_row(self, row_model: SampleProgressModel, *, compact: bool = False) -> QWidget:
        row = _SampleRowWidget(row_model.id)
        row.clicked.connect(self.sample_clicked.emit)
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_XS)
        row.setMinimumHeight(40 if compact else 46)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(SPACE_S)
        tone = (
            _status_tone("failed")
            if row_model.failed
            else _status_tone("warning")
            if row_model.warning
            else _status_tone("complete")
            if row_model.complete
            else _status_tone("neutral")
        )
        dot = _status_dot(tone, compact=compact)
        top.addWidget(dot)

        name = _progress_row_name(row_model.label, compact=compact)
        top.addWidget(name, stretch=1)

        chip_parts = [
            _count_phrase(row_model.batch_positions, "batch position"),
            _count_phrase(row_model.tilt_series, "tilt series", "tilt series"),
        ]
        if row_model.failed:
            chip_parts.append(f"{row_model.failed} failed")
        if row_model.warning:
            chip_parts.append(f"{row_model.warning} partial")
        chip = QLabel(" · ".join(chip_parts))
        chip.setObjectName("sampleBadge")
        chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top.addWidget(chip)
        layout.addLayout(top)
        layout.addWidget(_ProgressStrip(row_model))
        return row

    def _more_button(self, label: str, destination: str, tooltip: str) -> QToolButton:
        """Return a "+N more ..." control that opens the owning tab.

        These counts used to be plain muted labels, so the only way to reach
        the hidden rows was to guess which tab held them.
        """

        button = QToolButton()
        button.setObjectName("dashboardFilterButton")
        button.setText(f"+{label}")
        button.setToolTip(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda: self.navigate_requested.emit(destination))
        return button

    def _build_batch_positions_card(self, model: DashboardModel) -> QWidget:
        card = _CardBox()
        layout = _card_layout(card)

        # The card lists parsed batch positions *plus* display-only groups
        # inferred from orphaned failed tilt series, so its total can exceed
        # the Batch position tab's count. Say so instead of leaving the two
        # numbers to contradict each other.
        inferred = sum(1 for row in model.batch_positions if row.inferred)
        parsed = len(model.batch_positions) - inferred
        count_text = _count_phrase(parsed, "position")
        if inferred:
            count_text = f"{count_text} · {inferred} inferred"
        header, count_label = _card_header("BATCH POSITIONS", count_text)
        if inferred and count_label is not None:
            count_label.setToolTip(
                f"{_count_phrase(parsed, 'batch position')} were parsed from BatchPositionsList.xml.\n"
                f"{_count_phrase(inferred, 'further group')} shown here were inferred from failed tilt "
                "series that have no parsed batch position, so they are not counted by the Batch "
                "position tab."
            )
        layout.addLayout(header)

        if not model.batch_positions:
            empty = QLabel("No batch positions were detected for this sample.")
            empty.setObjectName("metaPlain")
            empty.setWordWrap(True)
            layout.addWidget(empty)
            layout.addStretch(1)
            card.setMinimumHeight(260)
            return card

        visible = model.batch_positions[:13]
        columns = 2 if len(visible) > 6 else 1
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(SPACE_M)
        grid.setVerticalSpacing(SPACE_XS)
        rows_per_column = (len(visible) + columns - 1) // columns if columns > 1 else len(visible)
        for index, batch in enumerate(visible):
            row = index % rows_per_column if columns > 1 else index
            column = index // rows_per_column if columns > 1 else 0
            grid.addWidget(self._batch_position_row(batch, compact=columns > 1), row, column)
            grid.setColumnStretch(column, 1)
        layout.addLayout(grid)

        if len(model.batch_positions) > len(visible):
            layout.addWidget(
                self._more_button(
                    _count_phrase(len(model.batch_positions) - len(visible), "more batch position"),
                    "Batch position",
                    "Open the Batch position tab to see every position in this scope",
                )
            )
        layout.addStretch(1)
        card.setMinimumHeight(330 if columns > 1 else 260)
        return card

    def _batch_position_row(
        self,
        row_model: BatchPositionProgressModel,
        *,
        compact: bool = False,
    ) -> QWidget:
        row = _SampleRowWidget(row_model.id)
        row.clicked.connect(lambda item_id: self.tile_item_clicked.emit("Batch position", item_id))
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        row.setToolTip(row_model.tooltip)

        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_XS)
        row.setMinimumHeight(40 if compact else 46)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(SPACE_S)
        tone = (
            _status_tone("failed")
            if row_model.status == "failed"
            else _status_tone("warning")
            if row_model.status == "warning"
            else _status_tone("complete")
            if row_model.status == "complete"
            else _status_tone("neutral")
        )
        dot = _status_dot(tone, compact=compact)
        top.addWidget(dot)

        name = _progress_row_name(row_model.label, compact=compact)
        name.setToolTip(row_model.tooltip)
        top.addWidget(name, stretch=1)

        # Zero-valued outcomes are dropped so the chip stays narrow enough to
        # leave the position name legible, matching how the sample rows above
        # already build their chip. "0 failed" on eighteen healthy rows was
        # both noise and the reason the names had no room.
        pieces = _batch_outcome_chip_parts(row_model)
        if row_model.inferred:
            # Mark the row itself — the header count alone would not tell the
            # reader *which* entries are not real batch positions. Folded
            # into the existing chip rather than added as a second widget so
            # it costs the card no extra minimum width.
            pieces.insert(0, "inferred")
        chip = QLabel(" · ".join(pieces))
        chip.setObjectName("sampleBadge")
        chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chip.setToolTip(row_model.tooltip)
        top.addWidget(chip)
        layout.addLayout(top)
        layout.addWidget(_ProgressStrip(row_model))
        return row

    def _build_completion_bar(self, sm_model: SearchMapCompletionModel) -> CompletionBar:
        bar = CompletionBar(
            identifier=sm_model.id,
            label=sm_model.label,
            acquired=sm_model.acquired,
            planned=sm_model.planned,
            failed_count=sm_model.failed_tilt_series,
            failed_batch_count=sm_model.failed_batch_groups,
        )
        bar.clicked.connect(self.search_map_clicked.emit)
        return bar

    def _build_timeline_card(self, model: DashboardModel, timeline: SessionTimeline | None) -> QWidget:
        card = _CardBox()
        layout = _card_layout(card)
        header, _badge = _card_header("ACQUISITION TIMELINE")
        if timeline is None or not timeline.segments:
            # Plan C8: nothing to plot, so no 320 px empty chart and no mode
            # buttons that would switch between two empty views. The strip
            # says the same words when it has nothing to draw.
            layout.addLayout(header)
            note = QLabel("No timestamped acquisitions to plot.")
            note.setObjectName("timelineEmptyNote")
            apply_tier(note, TIER_SUPPORTING)
            layout.addWidget(note)
            return card
        # Full-width row; the strip itself keeps density/lane minimums so
        # labels and bars are not compressed as the dashboard resizes.
        card.setMinimumHeight(TIME_CHART_TIMELINE_MIN_HEIGHT + 40)
        strip = TimelineStrip()
        strip.setMinimumHeight(TIME_CHART_TIMELINE_MIN_HEIGHT)
        mode_buttons: list[QToolButton] = []
        for label, mode in (("Density", "density"), ("Lanes", "lanes"), ("Auto", "auto")):
            button = QToolButton()
            button.setObjectName("dashboardFilterButton")
            button.setText(label)
            button.setCheckable(True)
            button.setChecked(mode == self._timeline_display_mode)
            button.setToolTip(
                "Binned session-scale density view"
                if mode == "density"
                else "Grouped lanes by sample or batch position"
                if mode == "lanes"
                else "Density for large sessions, lanes for smaller scopes"
            )
            button.clicked.connect(
                lambda _checked=False, selected=mode, buttons=mode_buttons, strip=strip: self._set_timeline_mode(strip, buttons, selected)
            )
            mode_buttons.append(button)
            header.addWidget(button)
        layout.addLayout(header)
        strip.set_segment_metadata(model.timeline_items)
        strip.set_display_mode(self._timeline_display_mode)
        strip.set_highlighted_tilt_series_id(self._highlighted_tilt_series_id)
        strip.set_timeline(timeline)
        strip.segment_clicked.connect(self._timeline_segment_clicked)
        self._timeline_strip = strip
        layout.addWidget(strip, stretch=1)
        return card

    def _set_timeline_mode(self, strip: TimelineStrip, buttons: list[QToolButton], mode: str) -> None:
        if mode not in {"auto", "density", "lanes"}:
            mode = "auto"
        self._timeline_display_mode = mode
        for button in buttons:
            button.setChecked(button.text().lower() == mode)
        strip.set_display_mode(mode)

    def _timeline_segment_clicked(self, tilt_series_id: str, frame_index: object = None) -> None:
        if tilt_series_id:
            self.timeline_tilt_series_requested.emit(tilt_series_id, frame_index)

    def set_highlighted_tilt_series_id(self, tilt_series_id: str | None) -> dict[str, int]:
        timings: dict[str, int] = {}
        self._highlighted_tilt_series_id = (
            self._normalise_highlight_id(self._model, tilt_series_id)
            if self._model is not None
            else tilt_series_id
        )
        if self._timeline_strip is not None:
            timer = QElapsedTimer()
            timer.start()
            self._timeline_strip.set_highlighted_tilt_series_id(self._highlighted_tilt_series_id)
            timings["timeline_ms"] = timer.elapsed()
        if self._defocus_plot is not None:
            timer = QElapsedTimer()
            timer.start()
            self._defocus_plot.set_highlighted_tilt_series_id(self._highlighted_tilt_series_id)
            timings["defocus_ms"] = timer.elapsed()
        if self._dose_plot is not None:
            timer = QElapsedTimer()
            timer.start()
            self._dose_plot.set_highlighted_tilt_series_id(self._highlighted_tilt_series_id)
            timings["dose_ms"] = timer.elapsed()
        return timings

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt signature
        if event.key() == Qt.Key.Key_Escape and self._highlighted_tilt_series_id:
            self.highlight_clear_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    @staticmethod
    def _normalise_highlight_id(model: DashboardModel | None, tilt_series_id: str | None) -> str | None:
        if model is None:
            return None
        if not tilt_series_id:
            return None
        if tilt_series_id in model.timeline_items:
            return tilt_series_id
        if any(point.tilt_series_id == tilt_series_id for point in model.defocus_readout.points):
            return tilt_series_id
        if any(point.tilt_series_id == tilt_series_id for point in model.dose_information.points):
            return tilt_series_id
        return None

    def _build_atlas_summary_card(self, atlas_summary: AtlasSummaryModel) -> QWidget:
        """Atlas-specific dashboard block.

        Shown for atlas-only sessions (where the collection cards would
        otherwise all read zero) and as a complement to the collection cards
        on mixed sessions.

        The headline row displays four distinct counts (atlas sessions /
        data-collection samples / samples with atlas / sample atlases)
        rather than a single ambiguous "Atlases" number — the previous
        implementation conflated the standalone screening session with
        per-grid atlases and data-collection sample atlases, producing
        an inflated 14 / 14 readout for the 12-grid test dataset.
        """

        counts = atlas_summary.counts
        card, layout = _card_with_title("Atlas summary")

        # Top-line summary row. Three distinct counts in user-requested
        # order: atlas-session totals first, then how many samples have
        # an atlas, then how many of those are actually data-collection
        # samples. The previously-shown "Sample atlases" field has been
        # dropped — for the common case (one atlas per sample) it is
        # numerically identical to "Samples with atlas".
        summary_row = QHBoxLayout()
        summary_row.setSpacing(SPACE_L)
        summary_row.addWidget(
            self._meta_pair_compact("Atlas sessions", str(counts.atlas_session_count))
        )
        summary_row.addWidget(
            self._meta_pair_compact(
                "Samples with atlas", str(counts.samples_with_atlas_count)
            )
        )
        summary_row.addWidget(
            self._meta_pair_compact(
                "Data collection samples", str(counts.data_collection_sample_count)
            )
        )
        # With a single atlas its own row below already states pixel size and
        # image size, so the summary pairs only repeated them.
        show_ranges = len(atlas_summary.rows) != 1
        if show_ranges and atlas_summary.pixel_size_range_um is not None:
            lo, hi = atlas_summary.pixel_size_range_um
            value = (
                f"{lo:.2f} µm/px"
                if abs(lo - hi) < 1e-6
                else f"{lo:.2f} – {hi:.2f} µm/px"
            )
            summary_row.addWidget(self._meta_pair_compact("Pixel size", value))
        if show_ranges and atlas_summary.image_size_range is not None:
            small, large = atlas_summary.image_size_range
            text = (
                f"{small[0]} × {small[1]} px"
                if small == large
                else f"{small[0]} × {small[1]} – {large[0]} × {large[1]} px"
            )
            summary_row.addWidget(self._meta_pair_compact("Image size", text))
        summary_row.addStretch(1)
        wrap = QWidget()
        wrap.setLayout(summary_row)
        layout.addWidget(wrap)

        # Per-atlas rows (capped to keep the card compact)
        max_rows = 8
        for row_model in atlas_summary.rows[:max_rows]:
            layout.addWidget(self._atlas_row_widget(row_model))
        if len(atlas_summary.rows) > max_rows:
            more = QLabel(f"+{len(atlas_summary.rows) - max_rows} more atlas{'es' if len(atlas_summary.rows) - max_rows != 1 else ''}")
            more.setObjectName("metaKey")
            layout.addWidget(more)
        return card

    def _meta_pair_compact(self, label: str, value: str) -> QWidget:
        """Smaller variant of ``_meta_pair`` for use inside dashboard cards."""

        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(SPACE_XS)
        l = QLabel(label.upper())
        l.setObjectName("cardTitle")
        v.addWidget(l)
        # Ask for the whole value's width. A plain ElidedLabel sized itself to
        # its elided text, so "0.47 µm/px" stayed "0.47 µ…" beside a trailing
        # stretch holding most of the card's width.
        val = ElidedLabel(value, prefer_full_width=True)
        val.setObjectName("metaValue")
        v.addWidget(val)
        return wrap

    def _atlas_row_widget(self, row_model: AtlasRowModel) -> QWidget:
        wrap = QWidget()
        layout = QHBoxLayout(wrap)
        layout.setContentsMargins(0, SPACE_XS, 0, SPACE_XS)
        layout.setSpacing(SPACE_S)

        sample_label = ElidedLabel(row_model.sample_label)
        sample_label.setObjectName("metaValue")
        sample_label.setMinimumWidth(160)
        sample_label.setToolTip(row_model.sample_label)
        layout.addWidget(sample_label)

        details: list[str] = []
        if row_model.image_size is not None:
            details.append(f"{row_model.image_size[0]} × {row_model.image_size[1]} px")
        if row_model.pixel_size_um is not None:
            details.append(f"{row_model.pixel_size_um:.2f} µm/px")
        if row_model.tile_count:
            details.append(f"{row_model.tile_count} tiles")
        if row_model.acquisition_time:
            details.append(row_model.acquisition_time)
        if not details:
            details.append("(no additional metadata)")
        details_text = "    ".join(details)
        details_label = ElidedLabel(details_text)
        details_label.setObjectName("metaPlain")
        details_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(details_label, stretch=1)
        return wrap

    def _build_warnings_card(self, model: DashboardModel) -> QWidget:
        card = _CardBox()
        layout = _card_layout(card)

        # "2,821 total" counted individual warning strings, 2,805 of which
        # were the same NaN message repeated per file. Lead with the number
        # of distinct issues so the headline reflects how much there is to
        # actually look at, and keep the raw total as secondary detail.
        distinct = len(model.warning_groups) if model.warning_groups else len(model.warnings)
        # Keep the badge short: this card is the narrowest column in the
        # lower dashboard row, and an unelided header label sets the card's
        # minimum width. The raw message count lives in the tooltip.
        header, badge_label = _card_header("WARNINGS", _count_phrase(distinct, "issue"))
        if badge_label is not None:
            badge_label.setToolTip(
                f"{_count_phrase(distinct, 'distinct kind')} of warning across "
                f"{_count_phrase(len(model.warnings), 'message')}. "
                "A single malformed field repeated across many files counts once here."
            )
        layout.addLayout(header)

        if not model.warnings:
            empty = QLabel("No warnings.")
            empty.setObjectName("metaPlain")
            layout.addWidget(empty)
            layout.addStretch(1)
            return card
        if model.warning_groups:
            for group in model.warning_groups[:6]:
                layout.addWidget(self._warning_group_row(group))
            if len(model.warning_groups) > 6:
                more = QToolButton(card)
                more.setText(f"Show all {len(model.warning_groups):,} warning groups")
                more.setObjectName("inlineLink")
                more.setCursor(Qt.CursorShape.PointingHandCursor)
                more.setAccessibleName("Show all warning groups")
                all_items = [
                    f"{group.label}: {item}"
                    for group in model.warning_groups
                    for item in group.items
                ]
                more.clicked.connect(
                    lambda _checked=False, items=all_items: self.warning_details_requested.emit(
                        "All warnings", items
                    )
                )
                layout.addWidget(more)
            layout.addStretch(1)
            return card
        # Show the top N error/warning rows; long lists collapse to a counter.
        top = [row for row in model.warnings if row.severity == "error"][:5]
        top.extend(row for row in model.warnings if row.severity == "warning")
        top = top[:8]
        if not top:
            top = model.warnings[:5]
        for row in top:
            layout.addWidget(self._warning_row(row))
        if len(model.warnings) > len(top):
            more = QLabel(f"+{len(model.warnings) - len(top)} more (mostly informational)")
            more.setObjectName("metaKey")
            layout.addWidget(more)
        layout.addStretch(1)
        return card

    def _warning_row(self, row: WarningRowModel) -> QWidget:
        wrap = QWidget()
        wrap.setMaximumWidth(READABLE_ROW_MAX_PX)
        layout = QHBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_S)
        dot = _status_dot(_status_tone(row.severity), width=14)
        layout.addWidget(dot)
        if row.sample:
            sample = ElidedLabel(row.sample)
            sample.setObjectName("metaKey")
            apply_tier(sample, TIER_SUPPORTING)
            sample.setFixedWidth(90)
            layout.addWidget(sample)
        msg = ElidedLabel(row.message)
        msg.setObjectName("metaPlain")
        apply_tier(msg, TIER_PRIMARY)
        msg.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(msg, stretch=1)
        return wrap

    def _warning_group_row(self, group: WarningGroupModel) -> QWidget:
        wrap = QFrame()
        wrap.setObjectName("warningGroup")
        # "View" belongs by its message: uncapped, a 4K card put it ~500 px
        # away (plan D3).
        wrap.setMaximumWidth(READABLE_ROW_MAX_PX)
        layout = QHBoxLayout(wrap)
        layout.setContentsMargins(SPACE_M, SPACE_S, SPACE_M, SPACE_S)
        layout.setSpacing(SPACE_S)

        count = QLabel(f"{group.count:,}")
        count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        count.setObjectName("warningCount")
        count.setMinimumWidth(max(54, count.fontMetrics().horizontalAdvance(count.text()) + 20))
        count.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        count.setProperty("tone", _status_tone(group.severity, "grey"))
        layout.addWidget(count)

        label = QLabel(group.label)
        label.setObjectName("warningGroupLabel")
        apply_tier(label, TIER_PRIMARY)
        label.setWordWrap(True)
        label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        # The count is affected objects; the raw messages are what the tooltip
        # carries, so say which is which rather than leaving a bare number.
        count.setToolTip(
            f"{group.count:,} affected · {len(group.items):,} warning messages"
        )
        label.setToolTip("\n".join(group.items[:8]))
        layout.addWidget(label, stretch=1)

        view = QToolButton(wrap)
        view.setText("View")
        view.setObjectName("inlineLink")
        view.setCursor(Qt.CursorShape.PointingHandCursor)
        view.setToolTip(f"View {group.label.lower()} details")
        view.setAccessibleName(f"View {group.label} details")
        view.clicked.connect(
            lambda _checked=False, label=group.label, items=list(group.items):
            self.warning_details_requested.emit(label, items)
        )
        layout.addWidget(view)
        return wrap

    # ----- helpers

    def _copy(self, text: str, *, source: QToolButton | None = None) -> None:
        from PySide6.QtGui import QGuiApplication

        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
            if source is not None:
                original_tooltip = source.toolTip()
                source.setToolTip("Copied")
                QToolTip.showText(
                    source.mapToGlobal(source.rect().bottomLeft()),
                    "Copied",
                    source,
                )
                QTimer.singleShot(
                    1200,
                    lambda button=source, tooltip=original_tooltip: button.setToolTip(tooltip),
                )
