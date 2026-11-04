"""Defocus components for the dashboard."""
from __future__ import annotations
from tomography_session_browser.ui.dashboard.constants import LOGGER
from PySide6.QtCore import QElapsedTimer
from PySide6.QtWidgets import QLabel, QWidget
from tomography_session_browser.presentation.dashboard.models import DashboardModel
from tomography_session_browser.ui.widgets.defocus_plot import DefocusScatterPlot
from tomography_session_browser.ui.widgets.time_chart import TIME_CHART_DEFOCUS_MIN_HEIGHT
from tomography_session_browser.ui.dashboard.common import CardBox, card_header, card_layout
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from tomography_session_browser.ui.dashboard.widget import SessionDashboard


def build_defocus_readout_card(dashboard: SessionDashboard, model: DashboardModel) -> QWidget:
    timer = QElapsedTimer()
    timer.start()
    card = CardBox()
    card.setMinimumHeight(360)
    layout = card_layout(card)

    header, coverage = card_header(
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
    plot.set_highlighted_tilt_series_id(dashboard._highlighted_tilt_series_id)
    plot.pointClicked.connect(dashboard.point_clicked.emit)
    plot.pointDoubleClicked.connect(dashboard.point_double_clicked.emit)
    plot.highlightClearRequested.connect(dashboard.highlight_clear_requested.emit)
    dashboard._defocus_plot = plot
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
