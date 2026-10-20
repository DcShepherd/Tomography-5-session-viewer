"""Constants components for the dashboard."""
from __future__ import annotations
import logging

LOGGER = logging.getLogger("tomography_session_browser.ui.widgets.session_dashboard")


SIDE_BY_SIDE_CHARTS_MIN_PX = 2360


READABLE_ROW_MAX_PX = 720


DASHBOARD_REFILL_VEIL_OPACITY = 0.28


DASHBOARD_REFILL_VEIL_MS = 160


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
