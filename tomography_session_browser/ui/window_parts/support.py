from __future__ import annotations
import logging
from collections.abc import (
    Iterable,
)
from typing import (
    Any,
)
from PySide6.QtCore import (
    QRect,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QWidget,
)
from tomography_session_browser.domain.markers import (
    ImageMarker,
)
from tomography_session_browser.services.markers.builders import markers_for_object
from tomography_session_browser.services.markers.context import MarkerContext
from tomography_session_browser.presentation.design_tokens import (
    SPACE_S,
    SPACE_XL,
)

LOGGER = logging.getLogger(__name__)


VIEWER_STATE_CACHE_LIMIT = 96


LOADER_PREWARM_DELAY_MS = 1500


FIRST_RUN_SCREEN_FRACTION = 0.88


FIRST_RUN_MAX_SIZE = (2000, 1100)


WINDOW_MIN_SIZE = (960, 640)


def first_run_window_rect(available: QRect) -> QRect:
    """Return a centred first-run window rect that fits ``available``.

    ``available`` is the screen's usable area (taskbar excluded). The result is
    never smaller than the window's minimum size, so on a screen too small for
    that minimum it may still overflow, exactly as the minimum already implies.
    """

    max_width, max_height = FIRST_RUN_MAX_SIZE
    min_width, min_height = WINDOW_MIN_SIZE
    width = max(min_width, min(max_width, round(available.width() * FIRST_RUN_SCREEN_FRACTION)))
    height = max(min_height, min(max_height, round(available.height() * FIRST_RUN_SCREEN_FRACTION)))
    left = available.left() + max(0, (available.width() - width) // 2)
    top = available.top() + max(0, (available.height() - height) // 2)
    return QRect(left, top, width, height)


def format_tilt_angle(angle: float) -> str:
    if abs(angle) < 0.05:
        return "0.0"
    return f"{angle:+.1f}"


def compact_pair(left: Any, right: Any) -> str | None:
    if left is None and right is None:
        return None
    return f"{left if left is not None else 'unknown'} / {right if right is not None else 'unknown'}"




PANEL_HEADER_HEIGHT_PX = SPACE_XL + 2 * SPACE_S


SIDE_PANEL_INSET_PX = SPACE_S


def panel_header(text: str, parent: QWidget, count: QLabel | None = None) -> QWidget:
    """The small-caps heading row shared by the Project and Context panels.

    The two panels used to disagree (plan C11): the left one opened with a
    small-caps "PROJECT" line and the right one with a large title-case name,
    so their first lines did not line up.
    """

    header = QWidget(parent)
    header.setObjectName("panelHeaderRow")
    header.setFixedHeight(PANEL_HEADER_HEIGHT_PX)
    layout = QHBoxLayout(header)
    layout.setContentsMargins(SIDE_PANEL_INSET_PX, SPACE_S, SIDE_PANEL_INSET_PX, SPACE_S)
    layout.setSpacing(SPACE_S)
    title = QLabel(text, header)
    title.setObjectName("panelHeader")
    layout.addWidget(title)
    layout.addStretch(1)
    if count is not None:
        layout.addWidget(count)
    return header


