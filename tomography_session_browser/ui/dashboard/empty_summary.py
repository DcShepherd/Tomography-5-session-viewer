"""The page for a scope in which nothing was detected (design review B3.5).

The name, a sentence saying so, and Needs attention: what was read of the folder
is the only thing there is to say about it.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QLabel, QWidget

from tomography_session_browser.presentation.dashboard.models import DashboardModel
from tomography_session_browser.presentation.dashboard.summary import EmptySummary
from tomography_session_browser.ui.dashboard.attention import build_needs_attention
from tomography_session_browser.ui.dashboard.header import build_summary_header
from tomography_session_browser.ui.theme import TIER_PRIMARY, apply_tier

if TYPE_CHECKING:
    from tomography_session_browser.ui.dashboard.widget import SessionDashboard

EMPTY_SCOPE_TEXT = (
    "Session loaded, but no atlases, overviews, search maps, batch positions, or tilt series were detected. "
    "Anything noted while reading it is under Needs attention."
)


def build_empty_summary_page(dashboard: SessionDashboard, model: DashboardModel, summary: EmptySummary) -> list[QWidget]:
    """The page's sections, top to bottom."""

    sentence = QLabel(EMPTY_SCOPE_TEXT)
    sentence.setObjectName("emptyScopeText")
    apply_tier(sentence, TIER_PRIMARY)
    sentence.setWordWrap(True)
    return [
        build_summary_header(dashboard, model),
        sentence,
        build_needs_attention(dashboard, summary.needs_attention),
    ]
