"""Tomogram outcome and review band, independent of the visible viewer."""
from __future__ import annotations

from tomography_session_browser.ui.widgets.link_button import link_button
from dataclasses import replace
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QLabel, QToolButton, QToolTip, QVBoxLayout, QWidget

from tomography_session_browser.presentation.dashboard.models import StatusTileModel, TILE_STATUS_COMPLETE, TILE_STATUS_NEUTRAL, TILE_STATUS_WARNING
from tomography_session_browser.presentation.dashboard.summary import ProcessingSummary
from tomography_session_browser.presentation.tomogram_review_state import review_counts
from tomography_session_browser.presentation.dashboard.tomogram_summary_input import TomogramSummaryInput
from tomography_session_browser.presentation.dashboard.tomogram_summary_facts import outcome_facts
from tomography_session_browser.presentation.design_tokens import SPACE_M, SPACE_S, SPACE_XS
from tomography_session_browser.ui.dashboard.common import CardBox, card_layout
from tomography_session_browser.ui.dashboard.layout import FlowLayout, ResponsiveCardGrid
from tomography_session_browser.ui.theme import TIER_PRIMARY, apply_tier
from tomography_session_browser.ui.widgets.status_tile_grid import StatusTileGrid


def note(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName('metaPlain')
    label.setWordWrap(True)
    return label


def info(text: str, parent: QWidget) -> QToolButton:
    button = link_button(parent=parent)
    button.setText('ⓘ')
    button.setAutoRaise(True)
    button.setToolTip(text)
    button.setAccessibleName('Explanation: ' + text)
    button.clicked.connect(lambda: QToolTip.showText(button.mapToGlobal(QPoint(0, button.height())), text, button))
    return button


class ProcessingOutcome(CardBox):
    def __init__(self, page, summary: ProcessingSummary) -> None:
        super().__init__(page)
        self.page, self.summary = page, summary
        body = card_layout(self)
        headline = QWidget(self)
        flow = FlowLayout(spacing=SPACE_M, line_spacing=SPACE_XS)
        headline.setLayout(flow)
        count = QLabel(f'{summary.tomogram_count:,}', headline)
        count.setObjectName('outcomeTotal')
        flow.addWidget(count)
        flow.addWidget(QLabel('tomogram' if summary.tomogram_count == 1 else 'tomograms', headline))
        status = ' · '.join(f'{count} {name}' for name, count in summary.link_status_counts if count)
        flow.addWidget(note(status or 'No tomograms in this selection', headline))
        if summary.attention_keys:
            attention = link_button(f'Needs attention · {len(summary.attention_keys)}', headline)
            attention.setToolTip('Volume or metadata warnings and ambiguous parent links. Opens exactly the counted rows.')
            attention.clicked.connect(lambda: page.route(summary.attention_keys, 'Tomograms needing attention'))
            flow.addWidget(attention)
        if summary.name_proposal_count and summary.chart_key is not None:
            review = link_button(f'{summary.name_proposal_count} match by name only · Review', headline)
            source_id = summary.input.sources[0].id if len(summary.input.sources) == 1 else ''
            review.clicked.connect(lambda: page.dashboard.name_links_requested.emit(source_id))
            flow.addWidget(review)
        flow.addWidget(info('One block per tomogram in list order, or proportional groups when there is insufficient room. '
                            'Opened means an accepted image was displayed, not that it passed scientific review. '
                            'Capture counts come from canonical review history and include all reconstruction versions.', headline))
        body.addWidget(headline)
        self.strip = StatusTileGrid([StatusTileModel(row.tomogram.id, row.tomogram.name, TILE_STATUS_NEUTRAL, '')
                                     for row in summary.input.rows], parent=self)
        self.strip.setObjectName('tomogramReviewStrip')
        self.strip.tile_clicked.connect(lambda key, _: page.route(frozenset((key,)), 'Tomogram review'))
        body.addWidget(self.strip)
        self.review_totals = note('', self)
        self.review_totals.setObjectName('tomogramReviewTotals')
        body.addWidget(self.review_totals)
        tiles = []
        for fact in outcome_facts(summary):
            tile = QWidget(self)
            stack = QVBoxLayout(tile)
            stack.setContentsMargins(0, SPACE_S, 0, 0)
            stack.setSpacing(SPACE_XS)
            header = QWidget(tile)
            row = FlowLayout(spacing=SPACE_XS)
            header.setLayout(row)
            title = QLabel(fact.label.upper(), header)
            title.setObjectName('cardTitle')
            row.addWidget(title)
            row.addWidget(info(fact.explanation, header))
            stack.addWidget(header)
            value = note(fact.value, tile)
            apply_tier(value, TIER_PRIMARY)
            stack.addWidget(value)
            if fact.detail:
                stack.addWidget(note(fact.detail, tile))
            tiles.append(tile)
        body.addWidget(ResponsiveCardGrid(tiles, breakpoints=((740, 4), (360, 2))))
        self.update_review(summary.input)

    def update_review(self, payload: TomogramSummaryInput) -> None:
        self.summary = replace(self.summary, input=payload, review=review_counts(row.review for row in payload.rows))
        review = self.summary.review
        labels = []
        if review.opened is None:
            labels.append('Opened: Unavailable')
        else:
            if review.opened:
                labels.append(f'Opened {review.opened}')
            remaining = self.summary.tomogram_count - review.opened
            if remaining:
                labels.append(f'Not opened {remaining}')
        if review.captures is None:
            labels.append('Captures: Unavailable')
        elif review.captures:
            noun = 'tomogram' if review.capture_tomograms == 1 else 'tomograms'
            labels.append(f'Captures {review.captures} in {review.capture_tomograms} {noun}')
        self.review_totals.setText(' · '.join(labels))
        self.review_totals.setVisible(bool(labels))
        self.review_totals.setToolTip(review.note)
        for tile, row in zip(self.strip.items(), payload.rows, strict=True):
            opened = row.review.opened
            label = 'Opened' if opened else 'Not opened' if opened is False else 'Opened state unavailable'
            status = TILE_STATUS_COMPLETE if opened else TILE_STATUS_NEUTRAL if opened is False else TILE_STATUS_WARNING
            tile.status = status
            tile.tooltip = f'{row.tomogram.name} · {label}\n{row.review.note}'
        self.strip.setAccessibleName('Tomograms opened state; not a scientific quality assessment')
        self.strip.update()
