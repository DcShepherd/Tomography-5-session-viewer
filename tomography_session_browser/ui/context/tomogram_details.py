"""Typed tomogram Context; no text parsing or independent scientific inference."""
from tomography_session_browser.ui.widgets.link_button import link_button
from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QWidget

from tomography_session_browser.presentation.tomogram_context import TomogramContext
from tomography_session_browser.presentation.design_tokens import SPACE_S, SPACE_XS
from tomography_session_browser.ui.context.fields import ContextScroll, copy_button, evidence_row, file_row, section, text_label, value_row
from tomography_session_browser.ui.dashboard.common import CardBox, card_layout, pill
from tomography_session_browser.ui.widgets.metadata_callout import MetadataCallout
from tomography_session_browser.ui.dashboard.layout import ResponsiveCardGrid


class TomogramDetails(ContextScroll):
    parent_requested = Signal(str)
    #: (row key, 'confirm' or 'unlink') from the alignment warning card's link.
    alignment_requested = Signal(str, str)

    def __init__(self, parent: QWidget, warning_tone: Callable[[str], str]) -> None:
        super().__init__(parent, warning_tone)
        self.model: TomogramContext | None = None
        self.setObjectName('tomogramContext')

    def set_model(self, model: TomogramContext) -> None:
        if self.model == model:
            return
        self.clear()
        self.model = model
        self.setAccessibleName(f'Tomogram Context: {model.name} · {model.version}')
        identity = QWidget(self.body)
        row = QHBoxLayout(identity)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE_S)
        row.addWidget(text_label(model.version, identity, 'metaKey'), 1)
        self.copy_details = copy_button(model.to_text(), identity, label='Copy details')
        self.copy_details.setObjectName('copyTomogramContext')
        row.addWidget(self.copy_details)
        self.layout_.addWidget(identity)
        self.layout_.addWidget(section('From tilt series', self.body))
        self.parent_button = link_button(parent=self.body)
        self.parent_button.setText(model.parent.label)
        self.parent_button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.parent_button.setMinimumWidth(0)
        self.parent_button.setEnabled(model.parent.target_key is not None)
        self.parent_button.setToolTip(model.parent.label + '\n' + model.parent.explanation)
        self.parent_button.setAccessibleDescription(model.parent.explanation)
        self.parent_button.clicked.connect(lambda: self.parent_requested.emit(model.row_key))
        self.layout_.addWidget(self.parent_button)
        if model.parent.target_key is None:
            self.layout_.addWidget(text_label(model.parent.explanation, self.body))
        self.layout_.addWidget(section('Recorded values', self.body))
        cards = []
        for tile in model.tiles:
            card = CardBox(self.body)
            card.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            layout = card_layout(card)
            layout.setContentsMargins(SPACE_S, SPACE_S, SPACE_S, SPACE_S)
            layout.setSpacing(SPACE_XS)
            layout.addWidget(text_label(tile.label, card, 'metaKey'))
            for value in tile.values:
                if len(tile.values) > 1:
                    layout.addWidget(text_label(value.label, card))
                number = text_label(value.display, card, 'healthValue')
                number.setToolTip(value.detail)
                number.setAccessibleName(value.label + ': ' + value.display)
                number.setAccessibleDescription(value.detail)
                number.setProperty('valueState', value.state)
                layout.addWidget(number)
                if value.tag:
                    tag = pill(value.tag, '')
                    tag.setParent(card)
                    tag.setToolTip(value.qualifier)
                    layout.addWidget(tag, alignment=Qt.AlignmentFlag.AlignLeft)
            layout.addStretch(1)
            cards.append(card)
        self.layout_.addWidget(ResponsiveCardGrid(cards, breakpoints=((240, 2),)))
        self.layout_.addWidget(section('Volume', self.body))
        for value in model.volume:
            self.layout_.addWidget(value_row(value, self.body))
        self.layout_.addWidget(section('Files', self.body))
        for file in model.files:
            self.layout_.addWidget(file_row(file, self.body))
        found = self.fold('How each value was found')
        if model.provenance:
            found.layout_.addWidget(text_label(model.provenance, found.body))
        found.layout_.addWidget(text_label(model.alignment, found.body))
        for value in (*[value for tile in model.tiles for value in tile.values], *model.other_values):
            found.layout_.addWidget(evidence_row(value, found.body))
        header = self.fold('Header geometry')
        for value in model.header:
            header.layout_.addWidget(evidence_row(value, header.body))
        if not model.header:
            header.layout_.addWidget(text_label('Absent: no reconstruction selected.', header.body))
        self.warnings(model.warnings)
        if (action := model.alignment_action) is not None:
            self.add_alignment_action(action, model.row_key)
        self.layout_.addStretch(1)
        self.verticalScrollBar().setValue(0)

    def add_alignment_action(self, action, row_key: str) -> None:
        cards = [card for card in self.body.findChildren(MetadataCallout) if card.warning == action.card]
        if len(cards) != 1:
            return
        self.alignment_button = link_button(parent=cards[0])
        self.alignment_button.setText(action.label)
        self.alignment_button.setToolTip(action.card)
        self.alignment_button.clicked.connect(lambda: self.alignment_requested.emit(row_key, action.kind))
        cards[0].add_action(self.alignment_button)
