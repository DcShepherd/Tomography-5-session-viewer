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

from PySide6.QtCore import QAbstractTableModel, QElapsedTimer, QModelIndex, QTimer, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QAbstractItemView,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from tomography_session_browser.services.timeline_service import SessionTimeline
from tomography_session_browser.ui.animations import fade_in_layout_children
from tomography_session_browser.ui.icons import themed_icon
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
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
from tomography_session_browser.ui.widgets.stat_card import StatCard
from tomography_session_browser.ui.widgets.time_chart import (
    TIME_CHART_DEFOCUS_MIN_HEIGHT,
    TIME_CHART_TIMELINE_MIN_HEIGHT,
)
from tomography_session_browser.ui.widgets.timeline_strip import TimelineStrip

LOGGER = logging.getLogger(__name__)


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
        self._layout.setSpacing(12)

        self._metrics_wrap = QWidget(self)
        self._layout.addWidget(self._metrics_wrap)
        self._chip: QLabel | None = None
        if status == "warning" and status_reason:
            self._chip = QLabel(status_reason.title(), self)
            self._chip.setObjectName("searchMapSummaryChip")
            self._chip.setToolTip(note or status_reason.title())
            chip_color = _status_color("warning")
            self._chip.setStyleSheet(f"border-color: {chip_color}; color: {chip_color}; margin-top: 4px;")
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
            layout.setSpacing(10)
            layout.addWidget(_dose_metric_widget("Median", self._median_value, stacked=True))
            layout.addWidget(_dose_metric_widget("Range", self._range_value, stacked=True))
            return

        layout = QHBoxLayout(self._metrics_wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(38)
        layout.addWidget(_dose_metric_widget("Median", self._median_value), stretch=0)
        layout.addWidget(_dose_metric_widget("Range", self._range_value), stretch=0)
        layout.addStretch(1)


class _SampleRowWidget(QWidget):
    clicked = Signal(str)

    def __init__(self, sample_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sample_id = sample_id

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt signature
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._sample_id)
            event.accept()
            return
        super().mousePressEvent(event)


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


def _count_phrase(count: int, singular: str, plural: str | None = None) -> str:
    noun = singular if count == 1 else (plural or f"{singular}s")
    return f"{count} {noun}"


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
    layout.setSpacing(2)

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
    title_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    layout.addWidget(title_label)
    return card, layout


def _card_layout(card: _CardBox) -> QVBoxLayout:
    layout = QVBoxLayout(card)
    layout.setContentsMargins(*DASHBOARD_CARD_MARGINS)
    layout.setSpacing(DASHBOARD_CARD_SPACING)
    return layout


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
    filter_requested = Signal(str, str)  # (destination tab, query)
    point_clicked = Signal(str, object)  # tilt_series_id, 1-based frame_index | None
    point_double_clicked = Signal(str, object)  # tilt_series_id, 1-based frame_index | None
    timeline_tilt_series_requested = Signal(str, object)  # tilt_series_id, 1-based frame_index | None
    highlight_clear_requested = Signal()
    # Generic per-tile selection: emitted with the destination tab label and
    # the item id when the user clicks a status tile on any of the four
    # count cards. The main window resolves the id back to the underlying
    # entity and selects it in the appropriate viewer.
    tile_item_clicked = Signal(str, str)  # (destination, item_id)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sessionDashboard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("dashboardScroll")
        self._scroll.viewport().setObjectName("dashboardViewport")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(self._scroll)

        self._content = QWidget()
        self._content.setObjectName("dashboardContent")
        self._scroll.setWidget(self._content)
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(16, 16, 16, 16)
        self._content_layout.setSpacing(14)

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
            self._content_layout.addWidget(self._build_timeline_card(model, timeline))
            if not defer_heavy_cards:
                self._content_layout.addWidget(self._build_defocus_readout_card(model))
                if model.dose_information.has_points:
                    self._content_layout.addWidget(self._build_dose_information_card(model))
            else:
                LOGGER.debug(
                    "dashboard heavy cards deferred defocus_points=%d dose_points=%d",
                    len(model.defocus_readout.points),
                    len(model.dose_information.points),
                )

            lower = QHBoxLayout()
            lower.setContentsMargins(0, 0, 0, 0)
            lower.setSpacing(14)
            if model.search_map_overview.total:
                lower.addWidget(
                    self._build_search_maps_overview_card(model.search_map_overview),
                    stretch=2,
                    alignment=Qt.AlignmentFlag.AlignTop,
                )
            if model.batch_positions:
                lower.addWidget(
                    self._build_batch_positions_card(model),
                    stretch=2,
                    alignment=Qt.AlignmentFlag.AlignTop,
                )
            lower.addWidget(
                self._build_warnings_card(model),
                stretch=1,
                alignment=Qt.AlignmentFlag.AlignTop,
            )
            lower_wrap = QWidget()
            lower_wrap.setLayout(lower)
            self._content_layout.addWidget(lower_wrap)
        if not model.has_collection_data:
            self._content_layout.addWidget(self._build_warnings_card(model))
        self._content_layout.addStretch(1)
        if animate:
            QTimer.singleShot(0, self._animate_loaded_content)

    def _animate_loaded_content(self) -> None:
        fade_in_layout_children(
            self._content_layout,
            duration_ms=160,
            stagger_ms=20,
            max_widgets=7,
            start_opacity=0.72,
            allow_hidden=False,
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

    def _build_header(self, model: DashboardModel) -> QWidget:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        title_row = QHBoxLayout()
        title_row.setSpacing(12)
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(3)
        eyebrow = QLabel("PROJECT · SESSION")
        eyebrow.setObjectName("screenEyebrow")
        left.addWidget(eyebrow)
        title = ElidedLabel(model.title)
        title.setObjectName("screenTitle")
        left.addWidget(title)
        title_row.addLayout(left, stretch=1)

        pill = QLabel(model.completeness_label)
        pill.setObjectName(f"statusPill_{model.completeness}")
        pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pill_color = _status_color(model.completeness)
        pill.setStyleSheet(
            "padding: 0 10px; border-radius: 10px; min-height: 22px; "
            f"background: {pill_color}; "
            f"color: {theme_safe_text_on(pill_color)}; font-weight: 600; font-size: 9pt;"
        )
        title_row.addWidget(pill)
        layout.addLayout(title_row)

        meta_wrap = QWidget()
        meta_grid = QGridLayout(meta_wrap)
        meta_grid.setContentsMargins(0, 0, 0, 0)
        meta_grid.setHorizontalSpacing(18)
        meta_grid.setVerticalSpacing(6)
        column = 0
        if model.kind:
            meta_grid.addWidget(self._meta_pair("Kind", model.kind), 0, column)
            meta_grid.setColumnStretch(column, 1)
            column += 1
        if model.microscope:
            meta_grid.addWidget(self._meta_pair("Microscope", model.microscope), 0, column)
            meta_grid.setColumnStretch(column, 1)
            column += 1
        if model.acquisition_start and model.acquisition_end:
            window_text = f"{model.acquisition_start}  →  {model.acquisition_end}"
            if model.acquisition_duration:
                window_text += f"  ({model.acquisition_duration})"
            span = max(column, 1)
            meta_grid.addWidget(self._meta_pair("Acquisition", window_text, wrap=True), 1, 0, 1, span)
        layout.addWidget(meta_wrap)

        # Path strip: middle-elided + copy button.
        path_row = QHBoxLayout()
        path_row.setSpacing(6)
        path_label = QLabel("Path:")
        path_label.setObjectName("metaKey")
        path_row.addWidget(path_label)
        path_value = ElidedLabel(model.path, mode=Qt.TextElideMode.ElideMiddle)
        path_value.setObjectName("metaValuePath")
        path_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        path_value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        path_row.addWidget(path_value, stretch=1)

        copy = QToolButton()
        copy.setObjectName("metaCopyButton")
        copy.setIcon(themed_icon("copy", size=14))
        copy.setIconSize(QSize(14, 14))
        copy.setToolTip("Copy session path")
        copy.setFixedSize(22, 22)
        copy.setCursor(Qt.CursorShape.PointingHandCursor)
        copy.clicked.connect(lambda: self._copy(model.path))
        path_row.addWidget(copy)
        layout.addLayout(path_row)
        return wrap

    def _meta_pair(self, label: str, value: str, *, wrap: bool = False) -> QWidget:
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        l = QLabel(label.upper())
        l.setObjectName("cardTitle")
        v.addWidget(l)
        if wrap:
            val = QLabel(value)
            val.setWordWrap(True)
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            val.setToolTip(value)
        else:
            val = ElidedLabel(value)
        val.setObjectName("metaValue")
        v.addWidget(val)
        return container

    def _build_collection_health_card(
        self,
        health: CollectionHealthModel,
        filters: list[DashboardFilterModel],
    ) -> QWidget:
        card, layout = _card_with_title("Collection health")
        layout.setSpacing(12)

        stats = QGridLayout()
        stats.setContentsMargins(0, 0, 0, 0)
        stats.setHorizontalSpacing(14)
        stats.setVerticalSpacing(8)
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
        filter_row.setSpacing(8)
        for filter_model in filters:
            button = QToolButton()
            button.setObjectName("dashboardFilterButton")
            button.setText(f"{filter_model.label}  {filter_model.count:,}")
            button.setToolTip(filter_model.tooltip)
            button.setEnabled(filter_model.count > 0 or not filter_model.query)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(
                lambda _checked=False, tab=filter_model.tab, query=filter_model.query: self.filter_requested.emit(tab, query)
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
        layout.setSpacing(1)
        title = QLabel(label.upper())
        title.setObjectName("cardTitle")
        layout.addWidget(title)
        number = ElidedLabel(value)
        number.setObjectName("healthValue")
        number.setToolTip(value)
        layout.addWidget(number)
        return wrap

    def _build_counts_row(self, model: DashboardModel) -> QWidget:
        wrap = QWidget()
        layout = QGridLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(12)
        from tomography_session_browser.ui.theme import current_palette

        theme = current_palette()
        accents = (theme.accent, theme.chart_green, theme.chart_amber, theme.chart_blue)
        for index, card_model in enumerate(model.counts):
            # Always populate the destination from the model when present;
            # fall back to a label-driven map so older models still navigate.
            if not card_model.destination:
                card_model.destination = _TAB_BY_LABEL.get(card_model.label, card_model.label)
            card = self._build_stat_card(card_model, accents[index % len(accents)])
            layout.addWidget(card, 0, index)
            layout.setColumnStretch(index, 1)
        return wrap

    def _build_stat_card(self, card_model: StatCardModel, accent: str) -> StatCard:
        card = StatCard(model=card_model, accent=accent)
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

        header, _count = _card_header("SEARCH MAP COMPLETION", f"{len(model.search_map_completion)} maps")
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
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
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

        header, _count = _card_header("SEARCH MAPS OVERVIEW", f"{overview.total:,} maps")
        layout.addLayout(header)

        summary = QHBoxLayout()
        summary.setSpacing(8)
        for label, value, status in (
            ("complete", overview.complete, "complete"),
            ("incomplete", overview.incomplete, "warning"),
            ("failed", overview.failed, "failed"),
            ("unknown", overview.unknown, "neutral"),
            ("N/A", overview.not_associated, "neutral"),
        ):
            chip = QLabel(f"{value:,} {label}")
            chip.setObjectName("searchMapSummaryChip")
            color = _status_color(status)
            chip.setStyleSheet(f"border-color: {color}; color: {color};")
            summary.addWidget(chip)
        summary.addStretch(1)
        layout.addLayout(summary)

        if overview.worst:
            worst_title = QLabel("MOST AFFECTED")
            worst_title.setObjectName("cardTitle")
            layout.addWidget(worst_title)
            for row in overview.worst:
                layout.addWidget(self._search_map_overview_row(row))
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
        layout = QHBoxLayout(wrap)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(8)
        dot = QLabel("●")
        dot.setFixedWidth(12)
        dot.setStyleSheet(f"color: {_status_color(row.status)}; font-size: 10pt;")
        layout.addWidget(dot)
        label = ElidedLabel(row.label)
        label.setObjectName("metaValue")
        label.setToolTip(row.label)
        layout.addWidget(label, stretch=1)
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
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        rows_per_column = (len(visible) + columns - 1) // columns if columns > 1 else len(visible)
        for index, sample in enumerate(visible):
            row = index % rows_per_column if columns > 1 else index
            column = index // rows_per_column if columns > 1 else 0
            grid.addWidget(self._sample_row(sample, compact=columns > 1), row, column)
            grid.setColumnStretch(column, 1)
        layout.addLayout(grid)

        if len(model.samples) > len(visible):
            more = QLabel(f"+{len(model.samples) - len(visible)} more samples")
            more.setObjectName("metaKey")
            layout.addWidget(more)
        layout.addStretch(1)
        card.setMinimumHeight(330 if columns > 1 else 260)
        return card

    def _sample_row(self, row_model: SampleProgressModel, *, compact: bool = False) -> QWidget:
        row = _SampleRowWidget(row_model.id)
        row.clicked.connect(self.sample_clicked.emit)
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 2)
        layout.setSpacing(4)
        row.setMinimumHeight(40 if compact else 46)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(7)
        dot = QLabel("●")
        color = (
            _status_color("failed")
            if row_model.failed
            else _status_color("warning")
            if row_model.warning
            else _status_color("complete")
            if row_model.complete
            else _status_color("neutral")
        )
        dot.setStyleSheet(f"color: {color}; font-size: {'10' if compact else '11'}pt;")
        dot.setFixedWidth(12)
        top.addWidget(dot)

        name = ElidedLabel(row_model.label)
        name.setObjectName("metaValue")
        name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        name.setStyleSheet(f"font-weight: 600; font-size: {'9.2' if compact else '10'}pt;")
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

    def _build_batch_positions_card(self, model: DashboardModel) -> QWidget:
        card = _CardBox()
        layout = _card_layout(card)

        header, _count = _card_header("BATCH POSITIONS", f"{len(model.batch_positions)} positions")
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
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        rows_per_column = (len(visible) + columns - 1) // columns if columns > 1 else len(visible)
        for index, batch in enumerate(visible):
            row = index % rows_per_column if columns > 1 else index
            column = index // rows_per_column if columns > 1 else 0
            grid.addWidget(self._batch_position_row(batch, compact=columns > 1), row, column)
            grid.setColumnStretch(column, 1)
        layout.addLayout(grid)

        if len(model.batch_positions) > len(visible):
            more = QLabel(f"+{len(model.batch_positions) - len(visible)} more batch positions")
            more.setObjectName("metaKey")
            layout.addWidget(more)
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
        layout.setContentsMargins(0, 0, 0, 2)
        layout.setSpacing(4)
        row.setMinimumHeight(40 if compact else 46)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(7)
        dot = QLabel("●")
        color = (
            _status_color("failed")
            if row_model.status == "failed"
            else _status_color("warning")
            if row_model.status == "warning"
            else _status_color("complete")
            if row_model.status == "complete"
            else _status_color("neutral")
        )
        dot.setStyleSheet(f"color: {color}; font-size: {'10' if compact else '11'}pt;")
        dot.setFixedWidth(12)
        top.addWidget(dot)

        name = ElidedLabel(row_model.label)
        name.setObjectName("metaValue")
        name.setToolTip(row_model.tooltip)
        name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        name.setStyleSheet(f"font-weight: 600; font-size: {'9.2' if compact else '10'}pt;")
        top.addWidget(name, stretch=1)

        pieces = [f"{row_model.complete} complete", f"{row_model.failed} failed"]
        if row_model.warning:
            pieces.append(f"{row_model.warning} partial")
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
        summary_row.setSpacing(20)
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
        if atlas_summary.pixel_size_range_um is not None:
            lo, hi = atlas_summary.pixel_size_range_um
            value = f"{lo:.2f} µm" if abs(lo - hi) < 1e-6 else f"{lo:.2f} – {hi:.2f} µm"
            summary_row.addWidget(self._meta_pair_compact("Pixel size", value))
        if atlas_summary.image_size_range is not None:
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
        v.setSpacing(2)
        l = QLabel(label.upper())
        l.setObjectName("cardTitle")
        v.addWidget(l)
        val = ElidedLabel(value)
        val.setObjectName("metaValue")
        v.addWidget(val)
        return wrap

    def _atlas_row_widget(self, row_model: AtlasRowModel) -> QWidget:
        wrap = QWidget()
        layout = QHBoxLayout(wrap)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(10)

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

        header, _count = _card_header("WARNINGS", f"{len(model.warnings):,} total")
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
                more = QLabel(f"+{len(model.warning_groups) - 6} more groups")
                more.setObjectName("metaKey")
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
        layout = QHBoxLayout(wrap)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(8)
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {_status_color(row.severity)}; font-size: 11pt;")
        dot.setFixedWidth(14)
        layout.addWidget(dot)
        if row.sample:
            sample = ElidedLabel(row.sample)
            sample.setObjectName("metaKey")
            sample.setFixedWidth(90)
            layout.addWidget(sample)
        msg = ElidedLabel(row.message)
        msg.setObjectName("metaPlain")
        msg.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(msg, stretch=1)
        return wrap

    def _warning_group_row(self, group: WarningGroupModel) -> QWidget:
        from tomography_session_browser.ui.theme import current_palette

        theme = current_palette()
        color = _status_color(group.severity, theme.chart_grey)
        wrap = QFrame()
        wrap.setObjectName("warningGroup")
        layout = QHBoxLayout(wrap)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        count = QLabel(f"{group.count:,}")
        count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        count.setObjectName("warningCount")
        count.setMinimumWidth(max(54, count.fontMetrics().horizontalAdvance(count.text()) + 20))
        count.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        count.setStyleSheet(f"color: {color}; border-color: {color};")
        layout.addWidget(count)

        label = ElidedLabel(group.label)
        label.setObjectName("metaValue")
        label.setStyleSheet("font-weight: 600;")
        label.setToolTip("\n".join(group.items[:8]))
        layout.addWidget(label, stretch=1)

        arrow = QLabel("›")
        arrow.setObjectName("metaKey")
        layout.addWidget(arrow)
        return wrap

    # ----- helpers

    def _copy(self, text: str) -> None:
        from PySide6.QtGui import QGuiApplication

        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
