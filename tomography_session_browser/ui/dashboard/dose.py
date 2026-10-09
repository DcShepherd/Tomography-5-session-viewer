"""Dose components for the dashboard."""
from __future__ import annotations
from tomography_session_browser.ui.dashboard.constants import LOGGER
from PySide6.QtCore import QElapsedTimer, Qt
from PySide6.QtWidgets import QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QScrollArea, QTableView, QToolButton, QVBoxLayout, QWidget
from tomography_session_browser.presentation.dashboard.models import DashboardModel
from tomography_session_browser.ui.widgets.dose_information_plot import DoseInformationScatterPlot
from tomography_session_browser.presentation.design_tokens import SPACE_M, SPACE_S, SPACE_XXL
from tomography_session_browser.ui.widgets.time_chart import TIME_CHART_DEFOCUS_MIN_HEIGHT
from tomography_session_browser.ui.dashboard.common import CardBox, card_header, card_layout, dose_metric_widget, format_dose, status_tone
from tomography_session_browser.ui.dashboard.dose_table import DoseInformationTableModel
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from tomography_session_browser.ui.dashboard.widget import SessionDashboard


class DoseSummaryPanel(QWidget):
    """Responsive metric block for the Dose Information card.

    The card itself supplies the heading via the standard
    :func:`card_header` (matching the Defocus Readout card); this panel
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
            self._chip.setProperty("tone", status_tone("warning"))
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
            layout.addWidget(dose_metric_widget("Median", self._median_value, stacked=True))
            layout.addWidget(dose_metric_widget("Range", self._range_value, stacked=True))
            return

        layout = QHBoxLayout(self._metrics_wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_XXL)
        layout.addWidget(dose_metric_widget("Median", self._median_value), stretch=0)
        layout.addWidget(dose_metric_widget("Range", self._range_value), stretch=0)
        layout.addStretch(1)


def build_dose_information_card(dashboard: SessionDashboard, model: DashboardModel) -> QWidget:
    timer = QElapsedTimer()
    timer.start()
    dose_model = model.dose_information
    card = CardBox()
    card.setMinimumHeight(360)
    layout = card_layout(card)

    source = dose_model.source_label or "MRC frame metadata"
    median_text = format_dose(dose_model.median_dose_e_per_angstrom2)
    range_text = (
        f"{format_dose(dose_model.min_dose_e_per_angstrom2)}–"
        f"{format_dose(dose_model.max_dose_e_per_angstrom2)}"
    )
    # Match the Defocus Readout card heading shape (cardTitle + badge)
    # instead of the previous redundant "CAMERA DOSE PER IMAGE (e⁻/Å²)"
    # heading. A single subtitle carries the unit and metadata source,
    # mirroring the source subtitle on the Defocus Readout card.
    header, _badge = card_header(
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
        DoseSummaryPanel(
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
    plot.set_highlighted_tilt_series_id(dashboard._highlighted_tilt_series_id)
    plot.pointClicked.connect(dashboard.point_clicked.emit)
    plot.pointDoubleClicked.connect(dashboard.point_double_clicked.emit)
    plot.highlightClearRequested.connect(dashboard.highlight_clear_requested.emit)
    dashboard._dose_plot = plot
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
            table.setModel(DoseInformationTableModel(dose_model.points, table))
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
