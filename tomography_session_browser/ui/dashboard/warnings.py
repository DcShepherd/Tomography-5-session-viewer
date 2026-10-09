"""Warnings components for the dashboard."""
from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QToolButton, QWidget
from tomography_session_browser.domain.display_names import count_phrase as _count_phrase
from tomography_session_browser.presentation.dashboard.models import DashboardModel, WarningGroupModel, WarningRowModel
from tomography_session_browser.presentation.design_tokens import SPACE_M, SPACE_S
from tomography_session_browser.ui.theme import TIER_PRIMARY, TIER_SUPPORTING, apply_tier
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
from tomography_session_browser.ui.dashboard.common import CardBox, card_header, card_layout, status_dot, status_tone
from tomography_session_browser.ui.dashboard.constants import READABLE_ROW_MAX_PX
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from tomography_session_browser.ui.dashboard.widget import SessionDashboard


def build_warnings_card(dashboard: SessionDashboard, model: DashboardModel) -> QWidget:
    card = CardBox()
    layout = card_layout(card)

    # "2,821 total" counted individual warning strings, 2,805 of which
    # were the same NaN message repeated per file. Lead with the number
    # of distinct issues so the headline reflects how much there is to
    # actually look at, and keep the raw total as secondary detail.
    distinct = len(model.warning_groups) if model.warning_groups else len(model.warnings)
    # Keep the badge short: this card is the narrowest column in the
    # lower dashboard row, and an unelided header label sets the card's
    # minimum width. The raw message count lives in the tooltip.
    header, badge_label = card_header("WARNINGS", _count_phrase(distinct, "issue"))
    if badge_label is not None:
        badge_label.setToolTip(
            f"{_count_phrase(distinct, 'distinct kind')} of warning across "
            f"{_count_phrase(len(model.warnings), 'message')}. "
            "A single malformed field repeated across many files counts once here."
        )
    layout.addLayout(header)

    if not model.warnings:
        empty = QLabel("No warnings.")
        empty.setObjectName("metaPlain")
        layout.addWidget(empty)
        layout.addStretch(1)
        return card
    if model.warning_groups:
        for group in model.warning_groups[:6]:
            layout.addWidget(dashboard._warning_group_row(group))
        if len(model.warning_groups) > 6:
            more = QToolButton(card)
            more.setText(f"Show all {len(model.warning_groups):,} warning groups")
            more.setObjectName("inlineLink")
            more.setCursor(Qt.CursorShape.PointingHandCursor)
            more.setAccessibleName("Show all warning groups")
            all_items = [
                f"{group.label}: {item}"
                for group in model.warning_groups
                for item in group.items
            ]
            more.clicked.connect(
                lambda _checked=False, items=all_items: dashboard.warning_details_requested.emit(
                    "All warnings", items
                )
            )
            layout.addWidget(more)
        layout.addStretch(1)
        return card
    # Show the top N error/warning rows; long lists collapse to a counter.
    top = [row for row in model.warnings if row.severity == "error"][:5]
    top.extend(row for row in model.warnings if row.severity == "warning")
    top = top[:8]
    if not top:
        top = model.warnings[:5]
    for row in top:
        layout.addWidget(dashboard._warning_row(row))
    if len(model.warnings) > len(top):
        more = QLabel(f"+{len(model.warnings) - len(top)} more (mostly informational)")
        more.setObjectName("metaKey")
        layout.addWidget(more)
    layout.addStretch(1)
    return card


def warning_row(row: WarningRowModel) -> QWidget:
    wrap = QWidget()
    wrap.setMaximumWidth(READABLE_ROW_MAX_PX)
    layout = QHBoxLayout(wrap)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(SPACE_S)
    dot = status_dot(status_tone(row.severity), width=14)
    layout.addWidget(dot)
    if row.sample:
        sample = ElidedLabel(row.sample)
        sample.setObjectName("metaKey")
        apply_tier(sample, TIER_SUPPORTING)
        sample.setFixedWidth(90)
        layout.addWidget(sample)
    msg = ElidedLabel(row.message)
    msg.setObjectName("metaPlain")
    apply_tier(msg, TIER_PRIMARY)
    msg.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    layout.addWidget(msg, stretch=1)
    return wrap


def warning_group_row(dashboard: SessionDashboard, group: WarningGroupModel) -> QWidget:
    wrap = QFrame()
    wrap.setObjectName("warningGroup")
    # "View" belongs by its message: uncapped, a 4K card put it ~500 px
    # away (plan D3).
    wrap.setMaximumWidth(READABLE_ROW_MAX_PX)
    layout = QHBoxLayout(wrap)
    layout.setContentsMargins(SPACE_M, SPACE_S, SPACE_M, SPACE_S)
    layout.setSpacing(SPACE_S)

    count = QLabel(f"{group.count:,}")
    count.setAlignment(Qt.AlignmentFlag.AlignCenter)
    count.setObjectName("warningCount")
    count.setMinimumWidth(max(54, count.fontMetrics().horizontalAdvance(count.text()) + 20))
    count.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
    count.setProperty("tone", status_tone(group.severity, "grey"))
    layout.addWidget(count)

    label = QLabel(group.label)
    label.setObjectName("warningGroupLabel")
    apply_tier(label, TIER_PRIMARY)
    label.setWordWrap(True)
    label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    # The count is affected objects; the raw messages are what the tooltip
    # carries, so say which is which rather than leaving a bare number.
    count.setToolTip(
        f"{group.count:,} affected · {len(group.items):,} warning messages"
    )
    label.setToolTip("\n".join(group.items[:8]))
    layout.addWidget(label, stretch=1)

    view = QToolButton(wrap)
    view.setText("View")
    view.setObjectName("inlineLink")
    view.setCursor(Qt.CursorShape.PointingHandCursor)
    view.setToolTip(f"View {group.label.lower()} details")
    view.setAccessibleName(f"View {group.label} details")
    view.clicked.connect(
        lambda _checked=False, label=group.label, items=list(group.items):
        dashboard.warning_details_requested.emit(label, items)
    )
    layout.addWidget(view)
    return wrap
