"""The collection summary page (design review B3.2).

Header, outcome panel with its by-sample table, the acquisition timeline and
applied defocus, camera dose (always drawn, with its empty state inline) and
Needs attention. The counts row, the search-map overview card and the Batch
positions card are not on this page: the outcome panel counts them by sample
and Needs attention routes to the rows that matter.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QWidget

from tomography_session_browser.presentation.dashboard.models import DashboardModel
from tomography_session_browser.presentation.dashboard.summary import CollectionSummary
from tomography_session_browser.services.timeline_service import SessionTimeline
from tomography_session_browser.ui.dashboard.acquisition_sections import acquisition_sections
from tomography_session_browser.ui.dashboard.attention import attention_anchor, build_needs_attention
from tomography_session_browser.ui.dashboard.header import build_summary_header
from tomography_session_browser.ui.dashboard.outcome import build_outcome_panel

if TYPE_CHECKING:
    from tomography_session_browser.ui.dashboard.widget import SessionDashboard


def build_collection_summary_page(
    dashboard: SessionDashboard,
    model: DashboardModel,
    summary: CollectionSummary,
    timeline: SessionTimeline | None,
    defer_heavy_cards: bool,
) -> list[QWidget]:
    """The page's sections, top to bottom."""

    attention = build_needs_attention(dashboard, summary.needs_attention)
    anchor = attention_anchor(dashboard, attention, summary.needs_attention)
    return [
        build_summary_header(dashboard, model, summary.source_folders, extra=anchor),
        build_outcome_panel(dashboard, model, summary.by_sample),
        *acquisition_sections(dashboard, model, timeline, defer_heavy_cards),
        attention,
    ]
