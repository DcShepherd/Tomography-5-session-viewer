"""Batch positions components for the dashboard."""
from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from tomography_session_browser.domain.display_names import count_phrase as _count_phrase
from tomography_session_browser.presentation.dashboard.models import BatchPositionProgressModel, DashboardModel
from tomography_session_browser.presentation.design_tokens import SPACE_M, SPACE_S, SPACE_XS
from tomography_session_browser.ui.dashboard.atlas import SampleRowWidget
from tomography_session_browser.ui.dashboard.common import CardBox, ProgressStrip, batch_outcome_chip_parts, card_header, card_layout, progress_row_name, status_dot, status_tone
from tomography_session_browser.presentation.tab_keys import TabKey
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from tomography_session_browser.ui.dashboard.widget import SessionDashboard


def build_batch_positions_card(dashboard: SessionDashboard, model: DashboardModel) -> QWidget:
    card = CardBox()
    layout = card_layout(card)

    # The card lists parsed batch positions *plus* display-only groups
    # inferred from orphaned failed tilt series, so its total can exceed
    # the Batch position tab's count. Say so instead of leaving the two
    # numbers to contradict each other.
    inferred = sum(1 for row in model.batch_positions if row.inferred)
    parsed = len(model.batch_positions) - inferred
    count_text = _count_phrase(parsed, "position")
    if inferred:
        count_text = f"{count_text} · {inferred} inferred"
    header, count_label = card_header("BATCH POSITIONS", count_text)
    if inferred and count_label is not None:
        count_label.setToolTip(
            f"{_count_phrase(parsed, 'batch position')} were parsed from BatchPositionsList.xml.\n"
            f"{_count_phrase(inferred, 'further group')} shown here were inferred from failed tilt "
            "series that have no parsed batch position, so they are not counted by the Batch "
            "position tab."
        )
    layout.addLayout(header)

    if not model.batch_positions:
        empty = QLabel("No batch positions were detected for this sample.")
        empty.setObjectName("metaPlain")
        empty.setWordWrap(True)
        layout.addWidget(empty)
        layout.addStretch(1)
        card.setMinimumHeight(260)
        return card

    visible = model.batch_positions[:13]
    columns = 2 if len(visible) > 6 else 1
    grid = QGridLayout()
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(SPACE_M)
    grid.setVerticalSpacing(SPACE_XS)
    rows_per_column = (len(visible) + columns - 1) // columns if columns > 1 else len(visible)
    for index, batch in enumerate(visible):
        row = index % rows_per_column if columns > 1 else index
        column = index // rows_per_column if columns > 1 else 0
        grid.addWidget(dashboard._batch_position_row(batch, compact=columns > 1), row, column)
        grid.setColumnStretch(column, 1)
    layout.addLayout(grid)

    if len(model.batch_positions) > len(visible):
        layout.addWidget(
            dashboard._more_button(
                _count_phrase(len(model.batch_positions) - len(visible), "more batch position"),
                TabKey.BATCH_POSITION,
                "Open the Batch position tab to see every position in this scope",
            )
        )
    layout.addStretch(1)
    card.setMinimumHeight(330 if columns > 1 else 260)
    return card


def batch_position_row(
    dashboard: SessionDashboard,
    row_model: BatchPositionProgressModel,
    *,
    compact: bool = False,
) -> QWidget:
    row = SampleRowWidget(row_model.id)
    row.clicked.connect(lambda item_id: dashboard.tile_item_clicked.emit(TabKey.BATCH_POSITION, item_id))
    row.setCursor(Qt.CursorShape.PointingHandCursor)
    row.setToolTip(row_model.tooltip)

    layout = QVBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(SPACE_XS)
    row.setMinimumHeight(40 if compact else 46)

    top = QHBoxLayout()
    top.setContentsMargins(0, 0, 0, 0)
    top.setSpacing(SPACE_S)
    tone = (
        status_tone("failed")
        if row_model.status == "failed"
        else status_tone("warning")
        if row_model.status == "warning"
        else status_tone("complete")
        if row_model.status == "complete"
        else status_tone("neutral")
    )
    dot = status_dot(tone, compact=compact)
    top.addWidget(dot)

    name = progress_row_name(row_model.label, compact=compact)
    name.setToolTip(row_model.tooltip)
    top.addWidget(name, stretch=1)

    # Zero-valued outcomes are dropped so the chip stays narrow enough to
    # leave the position name legible, matching how the sample rows above
    # already build their chip. "0 failed" on eighteen healthy rows was
    # both noise and the reason the names had no room.
    pieces = batch_outcome_chip_parts(row_model)
    if row_model.inferred:
        # Mark the row itself — the header count alone would not tell the
        # reader *which* entries are not real batch positions. Folded
        # into the existing chip rather than added as a second widget so
        # it costs the card no extra minimum width.
        pieces.insert(0, "inferred")
    chip = QLabel(" · ".join(pieces))
    chip.setObjectName("sampleBadge")
    chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
    chip.setToolTip(row_model.tooltip)
    top.addWidget(chip)
    layout.addLayout(top)
    layout.addWidget(ProgressStrip(row_model))
    return row
