"""Constants components for the dashboard."""
from __future__ import annotations
from tomography_session_browser.presentation.tab_keys import TabKey
import logging

LOGGER = logging.getLogger("tomography_session_browser.ui.widgets.session_dashboard")


META_ROW_HEIGHT = 28


SIDE_BY_SIDE_CHARTS_MIN_PX = 2360


READABLE_ROW_MAX_PX = 720


DASHBOARD_REFILL_VEIL_OPACITY = 0.28


DASHBOARD_REFILL_VEIL_MS = 160


TAB_BY_LABEL = {
    "Atlases": TabKey.ATLAS,
    "Overviews": TabKey.OVERVIEW,
    "Search maps": TabKey.SEARCH_MAP,
    "Batch positions": TabKey.BATCH_POSITION,
    "Tilt series": TabKey.TILT_SERIES,
}


STATUS_TONES = {
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


SEARCH_MAP_STATUS_TOOLTIPS = {
    "complete": "Every batch position on this search map produced a tilt series.",
    "incomplete": "Some batch positions on this search map are still missing a tilt series.",
    "failed": "At least one batch position on this search map failed.",
    "unknown": "Not enough metadata to judge whether acquisition completed.",
    "N/A": "No batch positions or tilt series are associated with this search map, "
    "so there is nothing to complete.",
}


ROW_LABEL_MIN_WIDTH = 132


ROW_LABEL_MIN_WIDTH_COMPACT = 86


