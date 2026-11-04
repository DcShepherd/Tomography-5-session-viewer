"""Tomograms Summary renders one immutable, version-explicit input."""
from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING
from tomography_session_browser.domain.display_names import count_phrase
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from tomography_session_browser.presentation.dashboard.models import DashboardFilterModel
from tomography_session_browser.presentation.dashboard.summary import summary_from_input
from tomography_session_browser.presentation.dashboard.tomogram_summary_input import TomogramSummaryInput
from tomography_session_browser.presentation.tomogram_review_state import review_counts
from tomography_session_browser.presentation.design_tokens import SPACE_L, SPACE_S, SPACE_XS
from tomography_session_browser.presentation.recorded_metrics import METRIC_COLUMNS
from tomography_session_browser.presentation.tab_keys import TabKey
from tomography_session_browser.ui.dashboard.common import CardBox, card_header, card_layout
from tomography_session_browser.ui.dashboard.layout import ResponsiveCardGrid
from tomography_session_browser.ui.dashboard.processing_evidence import EvidenceTable, TableEntry
from tomography_session_browser.ui.dashboard.processing_outcome import ProcessingOutcome, info, note
from tomography_session_browser.ui.theme import TIER_LANDMARK, TIER_PRIMARY, apply_tier
from tomography_session_browser.ui.tomograms.folding_section import FoldingSection
from tomography_session_browser.ui.widgets.chart_selection import BinPick, ChartSelection
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
from tomography_session_browser.ui.widgets.index_chart import IndexChart

if TYPE_CHECKING:
    from tomography_session_browser.ui.dashboard.widget import SessionDashboard


class ProcessingSummaryPage(QWidget):
    def __init__(self, dashboard: SessionDashboard, payload: TomogramSummaryInput) -> None:
        super().__init__(dashboard)
        self.dashboard, self.summary = dashboard, summary_from_input(payload)
        summary = self.summary
        self.charts: list[IndexChart] = []
        self.selection = ChartSelection(self)
        self.selection.changed.connect(self.show_readout)
        self.selection.opened.connect(lambda chart, keys: self.route(keys, chart.model.title))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_L)
        header = QWidget(self)
        flow = QHBoxLayout(header)
        flow.setContentsMargins(0, 0, 0, 0)
        flow.setSpacing(SPACE_S)
        title = ElidedLabel(payload.title, parent=header, prefer_full_width=True)
        title.setObjectName('screenTitle')
        apply_tier(title, TIER_LANDMARK)
        flow.addWidget(title, 1)
        open_button = QPushButton('Open in Tomograms tab →', header)
        open_button.setObjectName('openTomogramsSummary')
        open_button.setEnabled(bool(payload.rows))
        open_button.clicked.connect(lambda: self.route(summary.row_keys, 'All tomograms'))
        flow.addWidget(open_button)
        layout.addWidget(header)
        if payload.sources:
            noun = 'source folder' if summary.source_count == 1 else 'source folders'
            layout.addWidget(note(f'Processed with {summary.pipeline} · {summary.source_count} {noun}', self))
        if summary.notice:
            layout.addWidget(note(summary.notice, self))
        if summary.missing_version_count:
            layout.addWidget(note(f'{summary.version} missing for {summary.missing_version_count} of {count_phrase(summary.tomogram_count, "tomogram")}. '
                                  'Volume figures use the available versions only.', self))
        self.outcome_card = ProcessingOutcome(self, summary)
        layout.addWidget(self.outcome_card)
        readout = CardBox(self)
        readout.setObjectName('dashboardCard')
        readout_row = QHBoxLayout(readout)
        readout_row.setContentsMargins(SPACE_S, SPACE_XS, SPACE_S, SPACE_XS)
        readout_row.setSpacing(SPACE_S)
        self.hover_name = ElidedLabel('', parent=readout, prefer_full_width=True)
        self.hover_values = note('', readout)
        apply_tier(self.hover_name, TIER_PRIMARY)
        self.hover_values.setObjectName('tomogramChartReadout')
        readout_row.addWidget(self.hover_name)
        readout_row.addWidget(self.hover_values, 1)
        readout.setVisible(bool(summary.charts))
        if summary.charts:
            layout.addWidget(readout)
        else:
            self.hover_name.hide()
            self.hover_values.hide()
        chart_cards = []
        for chart_model in summary.charts:
            card = CardBox(self)
            body = card_layout(card)
            chart_header, _ = card_header(chart_model.title, unit=chart_model.unit)
            chart_header.addWidget(info(chart_model.description + ' All recorded metrics cover every tomogram, independent of version.', card))
            body.addLayout(chart_header)
            chart = IndexChart(chart_model, card, focus_frame=card)
            self.charts.append(chart)
            self.selection.add(chart)
            body.addWidget(chart)
            chart_cards.append(card)
        if chart_cards:
            layout.addWidget(ResponsiveCardGrid(chart_cards, breakpoints=((760, 2),)))
        else:
            layout.addWidget(note('Thickness, alignment, CTF and defocus charts appear when comparable recorded values are available.', self))
        self.show_readout()
        from tomography_session_browser.ui.dashboard.tag_cards import TagCards
        self.tag_cards = TagCards(self, payload)
        layout.addWidget(self.tag_cards)
        if summary.pixel_residual_count:
            layout.addWidget(note(f'{summary.pixel_residual_count} in pixels, not plotted. See All recorded values; pixels are not converted to nm.', self))
        evidence = CardBox(self)
        body = card_layout(evidence)
        recorded = FoldingSection(f'All recorded values · {count_phrase(summary.tomogram_count, "row")}', evidence)
        recorded.setObjectName('recordedValuesSection')
        recorded.layout_.addWidget(note('Recorded values and source fields across all tomograms, independent of version. '
            'Alignment import is unverified; runs are not ranked across settings. Press Enter or double-click a row to inspect its evidence in Context.', recorded.body))
        rows = tuple(TableEntry((row.name, *row.values), frozenset((row.key,)), row.detail,
                                (row.name, *row.sort_values)) for row in summary.metric_rows)
        table = EvidenceTable(('Tomogram', 'Alignment evidence', *(title for _, title in METRIC_COLUMNS)), rows, recorded.body)
        table.setObjectName('recordedMetricsTable')
        table.setAccessibleName('All recorded tomogram metrics')
        table.rows_requested.connect(lambda keys: self.route(keys, 'Recorded tomogram evidence'))
        recorded.layout_.addWidget(table)
        body.addWidget(recorded)
        geometry = 'geometry' if summary.geometry_count == 1 else 'geometries'
        volumes = FoldingSection(f'Volumes · {summary.geometry_count} declared {geometry}' if summary.geometry_count else 'Volumes', evidence)
        volumes.setObjectName('processingVolumesSection')
        rows = tuple(TableEntry((row.label, *row.columns), row.row_keys, row.detail,
                                (row.label, *row.sort_values)) for row in summary.volume_rows)
        table = EvidenceTable(('Tomogram / geometry group', 'Size, physical voxels', 'Voxel X × Y × Z', 'Slab', 'Mode', 'On disk'), rows, volumes.body)
        table.setObjectName('processingVolumesTable')
        table.setAccessibleName('Reconstruction volumes, grouped by geometry above 24 tomograms')
        table.rows_requested.connect(lambda keys: self.route(keys, 'Reconstruction volume geometry'))
        volumes.layout_.addWidget(table)
        body.addWidget(volumes)
        layout.addWidget(evidence)
        if summary.warnings:
            layout.addWidget(note('\n'.join(summary.warnings), self))

    def route(self, keys: frozenset[str], label: str) -> None:
        if self.dashboard._processing_page is self:
            self.dashboard.filter_requested.emit(DashboardFilterModel(label, len(keys), TabKey.TOMOGRAMS, '',
                label, row_keys=keys, scope_label=label))

    def update_review(self, payload: TomogramSummaryInput) -> None:
        self.summary = replace(self.summary, input=payload, review=review_counts(row.review for row in payload.rows))
        self.outcome_card.update_review(payload)
        self.tag_cards.update_payload(payload)

    def show_readout(self) -> None:
        """The hovered tomogram, else the highlighted one or bin, else how to choose one."""
        pick = self.selection.shown()
        if isinstance(pick, BinPick):
            self.hover_name.setText(pick.chart.model.title)
            text = pick.chart.describe(pick)
        elif pick is None or not self.charts:
            self.hover_name.setText('Point to a tomogram')
            text = 'Click: highlight · Double-click: open · Right-click or Esc: clear'
        else:
            first = self.charts[0].model
            index = first.row_keys.index(pick.key)
            self.hover_name.setText(f'{index + 1} · {first.names[index]}')
            values = []
            for chart in self.charts:
                for series in chart.model.series:
                    value = series.values[index]
                    values.append(f'{chart.model.title}, {series.label}: {value:g} {series.unit}' if value is not None
                                  else f'{chart.model.title}, {series.label}: {series.missing[index]}')
            text = ' · '.join(values)
        self.hover_values.setText(text)
        self.hover_values.setToolTip(text)
        self.hover_values.setAccessibleDescription(text)
