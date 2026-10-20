"""The acquisition sections both summary pages end on (design review B3.2, B3.3).

The timeline with the applied defocus beside it on a very wide page, then the
camera dose, which is always drawn: with no records the card says so inline.
Charts are built after the loading overlay on a large load, so
``defer_heavy_cards`` leaves the defocus and dose out for the caller to add.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QWidget

from tomography_session_browser.presentation.dashboard.models import DashboardModel
from tomography_session_browser.services.timeline_service import SessionTimeline
from tomography_session_browser.ui.dashboard.constants import LOGGER, SIDE_BY_SIDE_CHARTS_MIN_PX
from tomography_session_browser.ui.dashboard.dose import build_dose_empty_card
from tomography_session_browser.ui.dashboard.layout import SideBySideRow

if TYPE_CHECKING:
    from tomography_session_browser.ui.dashboard.widget import SessionDashboard


def acquisition_sections(
    dashboard: SessionDashboard,
    model: DashboardModel,
    timeline: SessionTimeline | None,
    defer_heavy_cards: bool,
) -> list[QWidget]:
    """Timeline (with defocus), then dose, top to bottom."""

    sections: list[QWidget] = []
    timeline_card = dashboard._build_timeline_card(model, timeline)
    if not defer_heavy_cards and model.defocus_readout.points:
        # Both are time-based charts; on a very wide page they share a row
        # (plan D3), and stack otherwise.
        sections.append(
            SideBySideRow(
                [timeline_card, dashboard._build_defocus_readout_card(model)],
                min_width=SIDE_BY_SIDE_CHARTS_MIN_PX,
            )
        )
    else:
        sections.append(timeline_card)
    if not defer_heavy_cards:
        sections.append(
            dashboard._build_dose_information_card(model)
            if model.dose_information.has_points
            else build_dose_empty_card(model)
        )
    else:
        LOGGER.debug(
            "dashboard heavy cards deferred defocus_points=%d dose_points=%d",
            len(model.defocus_readout.points),
            len(model.dose_information.points),
        )
    return sections
