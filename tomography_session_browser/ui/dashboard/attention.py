"""Needs attention: outcomes to open and findings to read (design review B3.2).

Outcome rows are acquisitions that went wrong, each with its deciding fact,
who holds them and a link that opens exactly those rows in their own tab.
Finding rows are metadata observations in the shared warning classification's
words and counts (affected objects; groups overlap), qualified only when the
model proves it ("every tilt series") or names a few objects.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QBoxLayout, QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from tomography_session_browser.presentation.context_stack import entity_display_label
from tomography_session_browser.presentation.dashboard.models import TILE_STATUS_FAILED, TILE_STATUS_WARNING, DashboardFilterModel
from tomography_session_browser.presentation.dashboard.sections.attention import (
    FindingRowModel,
    NeedsAttentionModel,
    OpenObjectRoute,
    OutcomeRowModel,
    SheetFilterRoute,
)
from tomography_session_browser.presentation.design_tokens import SPACE_L, SPACE_M, SPACE_S
from tomography_session_browser.presentation.workspace import EntityKind
from tomography_session_browser.ui.dashboard.common import CardBox, card_layout, status_dot, status_tone
from tomography_session_browser.ui.dashboard.constants import READABLE_ROW_MAX_PX
from tomography_session_browser.ui.theme import TIER_LANDMARK, TIER_PRIMARY, TIER_SUPPORTING, apply_tier
from tomography_session_browser.ui.widgets.rounded_viewport import round_viewport

if TYPE_CHECKING:
    from tomography_session_browser.ui.dashboard.widget import SessionDashboard

#: Two columns of findings once the card is this wide.
TWO_COLUMN_MIN_PX = 760


def attention_anchor(dashboard: SessionDashboard, card: QWidget,
                     attention: NeedsAttentionModel) -> QToolButton | None:
    """The same route to attention on collection and sample summaries."""
    if attention.is_empty:
        return None
    link = QToolButton()
    link.setObjectName("tableLink")
    link.setText("Needs attention")
    link.setAccessibleName("Go to Needs attention")
    link.setCursor(Qt.CursorShape.PointingHandCursor)
    heading = card.findChild(QLabel, "needsAttentionHeading")
    link.clicked.connect(lambda: dashboard.reveal_section(heading))
    return link


def build_needs_attention(dashboard: SessionDashboard, attention: NeedsAttentionModel) -> QWidget:
    card = CardBox()
    layout = card_layout(card)
    title = QLabel("Needs attention")
    title.setObjectName("needsAttentionHeading")
    title.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    apply_tier(title, TIER_LANDMARK)
    layout.addWidget(title)

    if attention.is_empty:
        layout.addWidget(_caption("Nothing needs attention in this scope."))
        return card

    if attention.outcomes:
        layout.addWidget(_caption("Acquisition outcomes"))
        for row in attention.outcomes:
            layout.addWidget(_outcome_row(dashboard, row))
        note = _inferred_note(attention)
        if note:
            layout.addWidget(note)
    if attention.findings:
        if attention.outcomes:
            layout.addSpacing(SPACE_S)
        header = QHBoxLayout()
        header.setSpacing(SPACE_M)
        header.addWidget(_caption("Metadata findings"))
        hint = QLabel("Counts are affected objects; groups overlap.")
        apply_tier(hint, TIER_SUPPORTING)
        header.addWidget(hint)
        header.addStretch(1)
        layout.addLayout(header)
        evidence = QPlainTextEdit(card)
        evidence.setObjectName("findingParserMessages")
        round_viewport(evidence)
        evidence.setReadOnly(True)
        evidence.setFixedHeight(160)
        evidence.setAccessibleName("Finding parser messages")
        evidence.hide()
        evidence_heading = _caption("Choose Parser messages on a finding to inspect its evidence.")
        evidence_heading.setObjectName("findingEvidenceHeading")

        def show_evidence(finding: FindingRowModel) -> None:
            evidence_heading.setText(f"Parser messages — {finding.label}")
            evidence.setAccessibleName(f"Parser messages: {finding.label}")
            evidence.setPlainText(f"{finding.label} · {len(finding.items):,} parser messages\n\n" + "\n\n".join(finding.items))
            evidence.show()
            dashboard.reveal_section(evidence_heading, focus=evidence)

        layout.addWidget(_FindingGrid(dashboard, attention.findings, show_evidence))
        layout.addWidget(evidence_heading)
        layout.addWidget(evidence)
    return card


def _caption(text: str) -> QLabel:
    label = QLabel(text)
    apply_tier(label, TIER_SUPPORTING)
    label.setWordWrap(True)
    return label


def _outcome_row(dashboard: SessionDashboard, row: OutcomeRowModel) -> QWidget:
    wrap = QWidget()
    wrap.setObjectName("outcomeRow")
    # The link belongs by its message: uncapped, a wide page put it a screen away.
    wrap.setMaximumWidth(READABLE_ROW_MAX_PX)
    layout = QHBoxLayout(wrap)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(SPACE_S)
    tone = "warn" if row.status == TILE_STATUS_WARNING else "bad"
    layout.addWidget(status_dot(tone, width=14), alignment=Qt.AlignmentFlag.AlignTop)

    text = QVBoxLayout()
    text.setContentsMargins(0, 0, 0, 0)
    text.setSpacing(0)
    label = QLabel(row.label)
    label.setObjectName("outcomeCount")
    apply_tier(label, TIER_PRIMARY)
    text.addWidget(label)
    detail = _outcome_detail(row)
    if detail:
        note = QLabel(detail)
        apply_tier(note, TIER_SUPPORTING)
        note.setWordWrap(True)
        text.addWidget(note)
    layout.addLayout(text, stretch=1)

    layout.addWidget(_outcome_link(dashboard, row), alignment=Qt.AlignmentFlag.AlignTop)
    return wrap


def _outcome_link(dashboard: SessionDashboard, row: OutcomeRowModel) -> QToolButton:
    """The row's link, by what its route does: open the rows in their tab, open
    the one object, or narrow this page's sheet."""

    route = row.route
    link = QToolButton()
    link.setObjectName("inlineLink")
    link.setCursor(Qt.CursorShape.PointingHandCursor)
    link.setToolTip(route.tooltip)
    link.setAccessibleName(route.tooltip)
    if isinstance(route, DashboardFilterModel):
        link.setText(f"Show in {entity_display_label(route.tab)}")
        link.clicked.connect(
            lambda _checked=False, route=route: dashboard.filter_requested.emit(route)
        )
    elif isinstance(route, OpenObjectRoute):
        link.setText(f"Show in {entity_display_label(route.tab)}")
        link.clicked.connect(
            lambda _checked=False, route=route: dashboard.tile_item_clicked.emit(route.tab, route.object_id)
        )
    elif isinstance(route, SheetFilterRoute):
        link.setText(f"Show these {route.filter.value.lower()}")
        link.clicked.connect(
            lambda _checked=False, route=route: dashboard.page_filter_requested.emit(route.filter.value)
        )
    return link


def _outcome_detail(row: OutcomeRowModel) -> str:
    """"1 of 35 frames each · alpha 3 · beta 2": the deciding fact, then who holds them."""

    holders = " · ".join(f"{item.sample} {item.count:,}" for item in row.by_sample)
    return " · ".join(part for part in (row.fact, holders) if part)


def _inferred_note(attention: NeedsAttentionModel) -> QLabel | None:
    """The inferred failed batch groups hold the failed series they were named for."""

    groups = list(attention.inferred_group_labels)
    if not groups:
        return None
    failed = next((row.count for row in attention.outcomes if row.kind is EntityKind.TILT_SERIES), 0)
    series = attention.inferred_tilt_series
    held = f"these same {series}" if series == failed else f"{series} of these"
    if len(groups) == 1:
        text = f"The inferred failed batch group, {groups[0]}, holds {held} series. It is not a further failure."
    else:
        names = ", ".join(groups[:-1]) + f" and {groups[-1]}"
        text = (
            f"The {len(groups)} inferred failed batch groups, {names}, hold {held} series. "
            "They are not further failures."
        )
    note = QLabel(text)
    apply_tier(note, TIER_SUPPORTING)
    note.setWordWrap(True)
    note.setObjectName("inferredNote")
    return note


class _FindingGrid(QWidget):
    """Finding rows in one column, or two when there is room."""

    def __init__(self, dashboard: SessionDashboard, findings: tuple[FindingRowModel, ...],
                 show_evidence: Callable[[FindingRowModel], None]) -> None:
        super().__init__()
        self._rows = [_finding_row(dashboard, finding, show_evidence) for finding in findings]
        self._selection = QButtonGroup(self)
        self._selection.setExclusive(True)
        for row in self._rows:
            self._selection.addButton(row.evidence_button)
        self._columns = 0
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(SPACE_L)
        self._grid.setVerticalSpacing(SPACE_S)
        self._reflow(1)

    def column_count(self) -> int:
        return self._columns

    def _reflow(self, columns: int) -> None:
        if columns != self._columns:
            self._columns = columns
            for index in reversed(range(self._grid.count())):
                self._grid.takeAt(index)
            for index, row in enumerate(self._rows):
                self._grid.addWidget(row, index // columns, index % columns)
            for column in range(2):
                self._grid.setColumnStretch(column, 1 if column < columns else 0)
        row_width = max(0, (self.width() - SPACE_L * (columns - 1)) // columns)
        for row in self._rows:
            row.fit_width(row_width)
        row_heights = [max(row.minimumHeight() for row in self._rows[start:start + columns])
                       for start in range(0, len(self._rows), columns)]
        self.setMinimumHeight(sum(row_heights) + SPACE_S * max(0, len(row_heights) - 1))
        self._grid.invalidate()
        self.updateGeometry()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt signature
        super().resizeEvent(event)
        self._reflow(2 if event.size().width() >= TWO_COLUMN_MIN_PX and len(self._rows) > 1 else 1)


class FindingRow(QFrame):
    """Keep a finding's text together; put its actions beside it when they fit."""

    def __init__(self) -> None:
        super().__init__()
        self.actions = QBoxLayout(QBoxLayout.Direction.TopToBottom)
        self.actions.setSpacing(SPACE_S)
        self.evidence_button = QToolButton(self)

    def fit_width(self, width: int) -> None:
        """Choose the action layout before the grid measures its rows."""
        action_width = sum(self.actions.itemAt(i).sizeHint().width() for i in range(self.actions.count()))
        # Leave room for the count, a readable text block, padding and gaps.
        horizontal = width >= action_width + 320
        direction = QBoxLayout.Direction.LeftToRight if horizontal else QBoxLayout.Direction.TopToBottom
        if self.actions.direction() != direction:
            self.actions.setDirection(direction)
            self.layout().invalidate()
        # The grid must never compress a newly stacked action below its hint.
        # Include wrapped text too, and release the old minimum when widening.
        self.setMinimumHeight(self.layout().totalHeightForWidth(width))


def _finding_row(dashboard: SessionDashboard, finding: FindingRowModel,
                 show_evidence: Callable[[FindingRowModel], None]) -> FindingRow:
    wrap = FindingRow()
    wrap.setObjectName("warningGroup")
    layout = QHBoxLayout(wrap)
    layout.setContentsMargins(SPACE_M, SPACE_S, SPACE_M, SPACE_S)
    layout.setSpacing(SPACE_S)

    count = QLabel(f"{finding.count:,}")
    count.setObjectName("warningCount")
    count.setAlignment(Qt.AlignmentFlag.AlignCenter)
    count.setMinimumWidth(max(54, count.fontMetrics().horizontalAdvance(count.text()) + 20))
    count.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
    count.setProperty("tone", status_tone(finding.severity, "grey"))
    count.setToolTip(f"{finding.count:,} affected · {len(finding.items):,} warning messages")
    layout.addWidget(count, alignment=Qt.AlignmentFlag.AlignVCenter)

    text = QVBoxLayout()
    text.setContentsMargins(0, 0, 0, 0)
    text.setSpacing(0)
    label = QLabel(finding.label)
    label.setObjectName("warningGroupLabel")
    apply_tier(label, TIER_PRIMARY)
    label.setWordWrap(True)
    label.setToolTip("\n".join(finding.items[:8]))
    text.addWidget(label)
    if finding.qualifier:
        qualifier = QLabel(finding.qualifier)
        apply_tier(qualifier, TIER_SUPPORTING)
        qualifier.setWordWrap(True)
        qualifier.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        text.addWidget(qualifier)
    if finding.route_reason:
        reason = _caption(finding.route_reason)
        reason.setObjectName("findingRouteReason")
        text.addWidget(reason)
    text.addStretch(1)
    layout.addLayout(text, stretch=1)

    actions = wrap.actions
    if finding.route is not None:
        route = finding.route
        link = QToolButton(wrap)
        link.setObjectName("inlineLink")
        link.setText(f"Show in {entity_display_label(route.tab)}")
        link.setToolTip(route.tooltip)
        link.setAccessibleName(f"{link.text()}: {finding.label}")
        link.setCursor(Qt.CursorShape.PointingHandCursor)
        link.clicked.connect(lambda _checked=False, route=route: dashboard.filter_requested.emit(route))
        actions.addWidget(link)

    view = wrap.evidence_button
    view.setText("Parser messages")
    view.setObjectName("inlineLink")
    view.setCheckable(True)
    view.setCursor(Qt.CursorShape.PointingHandCursor)
    view.setToolTip(f"Show {len(finding.items):,} parser messages in the pane below")
    view.setAccessibleName(f"Parser messages: {finding.label}")

    view.clicked.connect(lambda _checked=False: show_evidence(finding))
    actions.addWidget(view)
    layout.addLayout(actions)
    return wrap
