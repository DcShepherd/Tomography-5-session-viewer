"""Source-level Context with selection coverage and source-specific actions."""
from tomography_session_browser.ui.widgets.link_button import link_button
from collections.abc import Callable, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QMenu, QToolButton, QWidget

from tomography_session_browser.domain.folder_type import ReadAs, TypeFoundBy
from tomography_session_browser.domain.tomograms import ProcessingSource
from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.presentation.folder_type import (
    CHANGE_SELECTION, NO_PROJECT_NOTE, SelectionChange, read_as_line, selection_changes,
)
from tomography_session_browser.presentation.processing_context import ProcessingContext
from tomography_session_browser.presentation.read_times import READ_INTO_APP, read_time_text
from tomography_session_browser.presentation.design_tokens import SPACE_XS
from tomography_session_browser.ui.context.fields import ContextScroll, copy_button, file_row, section, text_label
from tomography_session_browser.ui.file_actions import add_source_actions
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel


def selection_menu_button(parent: QWidget, current: ReadAs, name: str,
                          chosen: Callable[[SelectionChange], None]) -> QToolButton:
    """"Change…" with the menu for a folder read by the user's selection (owner D15 a)."""

    button = link_button(CHANGE_SELECTION, parent)
    button.setAccessibleName(f"Change how {name} is read")
    button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    button.setProperty("menuIndicator", "none")
    button.setProperty("onTextLine", True)  # Level with the first line of the text beside it.
    menu = QMenu(button)
    for index, change in enumerate(selection_changes(current)):
        if change.remove and index:
            menu.addSeparator()
        menu.addAction(change.label, lambda change=change: chosen(change))
    button.setMenu(menu)
    return button


class ProcessingSources(ContextScroll):
    refresh_requested = Signal(str)
    remove_requested = Signal(str)
    #: A source id and the ``SelectionChange`` chosen from its "Change…" menu.
    read_as_requested = Signal(str, object)

    def __init__(self, parent: QWidget, warning_tone: Callable[[str], str]) -> None:
        super().__init__(parent, warning_tone)
        self.sources: tuple[ProcessingSource, ...] = ()
        self.model: ProcessingContext | None = None
        self.setObjectName('processingContext')

    def set_sources(self, sources: Sequence[ProcessingSource]) -> None:
        """Keep inventory independent of whether the item or source view is visible."""
        self.sources = tuple(sources)

    def set_model(self, model: ProcessingContext) -> None:
        if self.model == model:
            return
        self.clear()
        self.model = model
        self.set_sources(tuple(value.source for value in model.sources))
        self.layout_.addWidget(copy_button(model.to_text(), self.body, label='Copy details'))
        self.layout_.addWidget(text_label(f'{count_phrase(model.count, "tomogram")} · {model.version or "All review versions"}', self.body))
        if model.missing:
            self.layout_.addWidget(text_label(f'{model.version} missing for {model.missing} of {count_phrase(model.count, "tomogram")}. '
                                             'Volume figures use present versions only.', self.body))
        if model.notice:
            self.layout_.addWidget(text_label(model.notice, self.body))
        for value in model.sources:
            source = value.source
            self.layout_.addWidget(section(source.name, self.body))
            row = QWidget(self.body)
            actions = QHBoxLayout(row)
            actions.setContentsMargins(0, 0, 0, 0)
            actions.setSpacing(SPACE_XS)
            path = ElidedLabel(str(source.path), parent=row, prefer_full_width=True)
            path.setAccessibleName(str(source.path))
            actions.addWidget(path, 1)
            menu_button = link_button(parent=row)
            menu_button.setText('…')  # in the UI font, unlike '⋯'
            menu_button.setAccessibleName(f'Actions for processing source {source.name}: {source.path}')
            menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            menu_button.setProperty("menuIndicator", "none")
            menu = QMenu(menu_button)
            add_source_actions(menu, source, self.refresh_requested.emit, self.remove_requested.emit)
            menu_button.setMenu(menu)
            actions.addWidget(menu_button)
            self.layout_.addWidget(row)
            if source.display_pipeline:
                self.layout_.addWidget(text_label(f'Pipeline: {source.display_pipeline}', self.body))
            if line := read_as_line(source.found_by):
                self.add_read_as(source, line)
            self.layout_.addWidget(text_label(f'{READ_INTO_APP}: {read_time_text(value.read_at)}', self.body))
            self.layout_.addWidget(section('Source version folders', self.body))
            for file in value.versions:
                self.layout_.addWidget(file_row(file, self.body))
            if value.empty_note:
                self.layout_.addWidget(text_label(value.empty_note, self.body))
        if model.links:
            self.layout_.addWidget(section('Parent links · all selected tomograms', self.body))
            for label, count in model.links:
                self.layout_.addWidget(text_label(f'{label}: {count}', self.body))
        self.warnings(model.warnings)
        self.layout_.addStretch(1)
        self.verticalScrollBar().setValue(0)

    def add_read_as(self, source: ProcessingSource, line: str) -> None:
        """How the app found that the folder holds tomograms, when no record says it (D15 a)."""

        if source.found_by != TypeFoundBy.SELECTION:
            self.layout_.addWidget(text_label(line, self.body))
            # The one home of the no-project note: no banner, card or Summary footer.
            self.layout_.addWidget(text_label(NO_PROJECT_NOTE, self.body, 'metaValueDetail'))
            return
        row = QWidget(self.body)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_XS)
        layout.addWidget(text_label(line, row), 1)
        layout.addWidget(selection_menu_button(row, ReadAs.TOMOGRAMS, source.name,
                                               lambda change: self.read_as_requested.emit(source.id, change)),
                         0, Qt.AlignmentFlag.AlignTop)
        self.layout_.addWidget(row)
