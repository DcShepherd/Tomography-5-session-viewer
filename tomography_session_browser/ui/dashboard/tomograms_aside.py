"""The Tomograms aside of a summary card (plan P6, D3).

How many tomograms are linked to the acquisitions in this scope, from how many
processing folders, and the way into them: the whole aside opens the
Tomograms tab on its explicit AllTomograms selection. It is drawn only when
there are tomograms.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.presentation.dashboard.models import DashboardFilterModel
from tomography_session_browser.presentation.dashboard.tomogram_summary_input import TomogramSummaryInput
from tomography_session_browser.presentation.design_tokens import SPACE_M, SPACE_S
from tomography_session_browser.presentation.tab_keys import TabKey
from tomography_session_browser.ui.dashboard.common import card_title
from tomography_session_browser.ui.dashboard.route_link import RouteLink
from tomography_session_browser.ui.theme import TIER_PRIMARY, TIER_SUPPORTING, apply_tier

if TYPE_CHECKING:
    from tomography_session_browser.ui.dashboard.widget import SessionDashboard


class TomogramsAside:
    """The aside's area and the pieces a ``SummaryCard`` lays in its rows."""

    def __init__(self, dashboard: SessionDashboard, payload: TomogramSummaryInput) -> None:
        count = len(payload.rows)
        folders = count_phrase(len(payload.sources), "processing folder")
        route = DashboardFilterModel("Tomograms", count, TabKey.TOMOGRAMS, "", "Verified acquisition-linked tomograms")
        self.link = RouteLink(route, dashboard.tomograms_requested.emit, name="Open the Tomograms tab",
                              description=f"{count_phrase(count, 'tomogram')} from {folders}, linked to this acquisition scope.")
        self.link.setProperty("aside", True)
        self.link.setToolTip("Open the tomograms of this scope in the Tomograms tab.")

        self.heading = card_title("Tomograms", TabKey.TOMOGRAMS)

        self.number = QWidget()
        number_row = QHBoxLayout(self.number)
        number_row.setContentsMargins(0, 0, 0, 0)
        number_row.setSpacing(SPACE_S)
        self.count = QLabel(f"{count:,}")
        self.count.setObjectName("outcomeTotal")
        number_row.addWidget(self.count)
        word = QLabel("tomogram" if count == 1 else "tomograms")
        apply_tier(word, TIER_PRIMARY)
        number_row.addWidget(word)
        number_row.addStretch(1)

        self.foot = QWidget()
        self._foot = QVBoxLayout(self.foot)
        self._foot.setContentsMargins(0, 0, 0, 0)
        self._foot.setSpacing(SPACE_M)
        self.caption = QLabel(f"{folders} · acquisition linked")
        apply_tier(self.caption, TIER_SUPPORTING)
        self._foot.addWidget(self.caption)
        self._foot.addStretch(1)
        self.action = QLabel("Open Tomograms →")
        self.action.setObjectName("routeLinkAction")
        self._foot.addWidget(self.action)

    def set_strip(self, strip: bool) -> None:
        """Lay the foot out in a line (a strip under the body) or a column (beside it)."""

        self._foot.setDirection(QVBoxLayout.Direction.LeftToRight if strip else QVBoxLayout.Direction.TopToBottom)
        for widget in (self.link, self.count):
            widget.setProperty("strip", strip)
            style = widget.style()
            style.unpolish(widget)
            style.polish(widget)


def tomograms_aside(dashboard: SessionDashboard, payload: TomogramSummaryInput | None) -> TomogramsAside | None:
    """The aside when there are tomograms to open, otherwise None."""

    return TomogramsAside(dashboard, payload) if payload is not None and payload.rows else None
