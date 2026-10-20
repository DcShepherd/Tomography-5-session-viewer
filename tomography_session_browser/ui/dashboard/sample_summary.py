"""The sample summary page (design review B3.3).

For one sample, one linked sample, or one entity in focus: the header (name and
time span), the cards (Tilt series, with the batch-grouped band, then Batch
positions, Search maps, Overviews and the Atlas), the acquisition timeline and
applied defocus, camera dose, and Needs attention. The search-map overview
card, the Batch positions card and the warnings card are not on this page:
the cards count them and Needs attention routes to the rows that matter.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QWidget

from tomography_session_browser.presentation.dashboard.models import DashboardModel
from tomography_session_browser.presentation.dashboard.summary import SampleSummary
from tomography_session_browser.services.timeline_service import SessionTimeline
from tomography_session_browser.ui.dashboard.acquisition_sections import acquisition_sections
from tomography_session_browser.ui.dashboard.attention import attention_anchor, build_needs_attention
from tomography_session_browser.ui.dashboard.header import build_summary_header
from tomography_session_browser.ui.dashboard.sample_cards import build_sample_cards

if TYPE_CHECKING:
    from tomography_session_browser.ui.dashboard.widget import SessionDashboard


def build_sample_summary_page(
    dashboard: SessionDashboard,
    model: DashboardModel,
    summary: SampleSummary,
    timeline: SessionTimeline | None,
    defer_heavy_cards: bool,
) -> list[QWidget]:
    """The page's sections, top to bottom."""

    attention = build_needs_attention(dashboard, summary.needs_attention)
    return [
        build_summary_header(dashboard, model, extra=attention_anchor(dashboard, attention, summary.needs_attention)),
        build_sample_cards(dashboard, model),
        *acquisition_sections(dashboard, model, timeline, defer_heavy_cards),
        attention,
    ]
