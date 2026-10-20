"""Timeline components for the dashboard."""
from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QLabel, QToolButton, QWidget
from tomography_session_browser.services.timeline_service import SessionTimeline
from tomography_session_browser.presentation.dashboard.models import DashboardModel
from tomography_session_browser.ui.theme import TIER_SUPPORTING, apply_tier
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
from tomography_session_browser.ui.widgets.time_chart import TIME_CHART_TIMELINE_MIN_HEIGHT
from tomography_session_browser.ui.timeline.widget import TimelineStrip
from tomography_session_browser.ui.dashboard.common import CardBox, card_header, card_layout
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from tomography_session_browser.ui.dashboard.widget import SessionDashboard


def build_timeline_card(dashboard: SessionDashboard, model: DashboardModel, timeline: SessionTimeline | None) -> QWidget:
    card = CardBox()
    layout = card_layout(card)
    header, _badge = card_header("ACQUISITION TIMELINE")
    if timeline is None or not timeline.segments:
        # Plan C8: nothing to plot, so no 320 px empty chart and no mode
        # buttons that would switch between two empty views. The strip
        # says the same words when it has nothing to draw.
        layout.addLayout(header)
        note = QLabel("No timestamped acquisitions to plot.")
        note.setObjectName("timelineEmptyNote")
        apply_tier(note, TIER_SUPPORTING)
        layout.addWidget(note)
        return card
    # Full-width row; the strip itself keeps density/lane minimums so
    # labels and bars are not compressed as the dashboard resizes.
    card.setMinimumHeight(TIME_CHART_TIMELINE_MIN_HEIGHT + 40)
    strip = TimelineStrip()
    strip.setMinimumHeight(TIME_CHART_TIMELINE_MIN_HEIGHT)
    mode_buttons: list[QToolButton] = []
    # One segmented control (harmonisation D2): three separate bordered
    # buttons marked the chosen mode by an accent border only.
    segmented = QFrame(card)
    segmented.setObjectName("segmentedControl")
    segmented.setAccessibleName("Timeline view")
    segmented_layout = QHBoxLayout(segmented)
    # The segments' 1 px inset is their style-sheet margin.
    segmented_layout.setContentsMargins(0, 0, 0, 0)
    segmented_layout.setSpacing(0)
    mode_group = QButtonGroup(segmented)
    mode_group.setExclusive(True)
    for label, mode in (("Density", "density"), ("Lanes", "lanes"), ("Auto", "auto")):
        button = QToolButton(segmented)
        button.setObjectName("segmentButton")
        button.setText(label)
        button.setCheckable(True)
        button.setChecked(mode == dashboard._timeline_display_mode)
        button.setToolTip(
            "Binned session-scale density view"
            if mode == "density"
            else "Grouped lanes by sample or batch position"
            if mode == "lanes"
            else "Lanes when every lane has a row tall enough for its name, otherwise density"
        )
        button.clicked.connect(
            lambda _checked=False, selected=mode, buttons=mode_buttons, strip=strip: dashboard._set_timeline_mode(strip, buttons, selected)
        )
        mode_buttons.append(button)
        mode_group.addButton(button)
        segmented_layout.addWidget(button)
    # Said once when the lane names leave out the prefix they share.
    prefix_note = ElidedLabel(mode=Qt.TextElideMode.ElideMiddle, prefer_full_width=True)
    prefix_note.setObjectName("timelineLanePrefix")
    prefix_note.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    apply_tier(prefix_note, TIER_SUPPORTING)
    header.addWidget(prefix_note)
    header.addWidget(segmented)
    layout.addLayout(header)
    strip.lane_label_prefix_changed.connect(lambda prefix: show_lane_prefix(prefix_note, prefix))
    strip.set_segment_metadata(model.timeline_items)
    strip.set_display_mode(dashboard._timeline_display_mode)
    strip.set_highlighted_tilt_series_id(dashboard._highlighted_tilt_series_id)
    strip.set_timeline(timeline)
    show_lane_prefix(prefix_note, strip.lane_label_prefix())
    strip.segment_clicked.connect(dashboard._timeline_segment_clicked)
    dashboard._timeline_strip = strip
    layout.addWidget(strip, stretch=1)
    return card


def show_lane_prefix(note: ElidedLabel, prefix: str) -> None:
    """Say what the shortened lane names leave out, or nothing when they are whole."""

    note.setText(f"Lanes: {prefix}…" if prefix else "")
    note.setToolTip(f"The lane names leave out {prefix}, which every lane here starts with." if prefix else "")
    note.setVisible(bool(prefix))


def set_timeline_mode(dashboard: SessionDashboard, strip: TimelineStrip, buttons: list[QToolButton], mode: str) -> None:
    if mode not in {"auto", "density", "lanes"}:
        mode = "auto"
    dashboard._timeline_display_mode = mode
    for button in buttons:
        button.setChecked(button.text().lower() == mode)
    strip.set_display_mode(mode)


def timeline_segment_clicked(dashboard: SessionDashboard, tilt_series_id: str, frame_index: object = None) -> None:
    if tilt_series_id:
        dashboard.timeline_tilt_series_requested.emit(tilt_series_id, frame_index)
