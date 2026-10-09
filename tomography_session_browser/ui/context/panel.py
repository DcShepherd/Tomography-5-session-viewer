"""The context panel: a heading over the metadata of what is in view.

Display only. ``MainWindow`` decides what to describe (the selection, the
tab's item or the scope) and hands over the title and text; this owns the
widgets, the heading rule and the cache that spares rebuilding a scope's
description it already shows.
"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from tomography_session_browser.ui.widgets.metadata_panel import MetadataPanel
from tomography_session_browser.presentation.tab_keys import TabKey

CONTEXT_HEADING = "CONTEXT"
#: The kind the heading names when the page's title already names the item.
CONTEXT_HEADING_KINDS: dict[str, str] = {
    TabKey.ATLAS: "Atlas",
    TabKey.OVERVIEW: "Overview",
    TabKey.SEARCH_MAP: "Search map",
    TabKey.SEARCH: "Search tile",
    TabKey.BATCH_POSITION: "Batch position",
    TabKey.TILT_SERIES: "Tilt series",
}


class ContextPanel:
    def __init__(
        self,
        parent: QWidget,
        *,
        header: Callable[[str, QWidget], QWidget],
        inset: int,
        status_tone: Callable[[str], str],
        warning_tone: Callable[[str], str],
    ) -> None:
        #: The scope the panel last described, so describing it again is skipped.
        self._scope_key: str | None = None

        self.widget = panel = QWidget(parent)
        panel.setObjectName("contextShell")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        heading_row = header(CONTEXT_HEADING, panel)
        # Names the kind of item when the page already names the item (A1).
        self.heading: QLabel = heading_row.findChild(QLabel, "panelHeader")
        layout.addWidget(heading_row)
        body = QWidget(panel)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(inset, 0, inset, inset)
        body_layout.setSpacing(0)
        self.metadata = MetadataPanel(body)
        # One status language (harmonisation C1): the Status row is the
        # header's pill, and a warning takes its dashboard tone, classified by
        # the shared ``services/warning_classification.py`` rules.
        self.metadata.set_status_tone(status_tone)
        self.metadata.set_warning_tone(warning_tone)
        body_layout.addWidget(self.metadata, stretch=1)
        layout.addWidget(body, stretch=1)

    def show_item(self, title: str, text: str) -> None:
        self.metadata.set_title(title)
        self.metadata.set_text(text)

    def show_scope(self, scope_key: str, title: str, describe: Callable[[], str]) -> None:
        """Describe a scope, unless the panel already shows it.

        Building a scope's description walks the whole group. The title check
        keeps this self-correcting: if a selection overwrote the panel since,
        the titles differ and the description is rebuilt.
        """

        if self._scope_key == scope_key and self.metadata.text() == title:
            return
        self.metadata.set_title(title)
        self.metadata.set_text(describe())
        self._scope_key = scope_key

    def sync_heading(self, tab_label: str, page_title: str | None) -> None:
        """Name the item once (harmonisation A1).

        When the panel describes the item the tab's page already titles
        (``page_title``), the heading says what kind of item it is ("TILT
        SERIES") and the panel's own copy of the name hides. Otherwise the
        heading is "CONTEXT" over the name. Display only: the title's text,
        and ``metadata.text()``, are unchanged.
        """

        title = self.metadata.text()
        echoes = (
            page_title is not None
            and tab_label in CONTEXT_HEADING_KINDS
            and bool(title)
            and page_title == title
        )
        kind = CONTEXT_HEADING_KINDS[tab_label] if echoes else ""
        self.heading.setText(kind.upper() if kind else CONTEXT_HEADING)
        self.heading.setAccessibleName(f"{kind}: {title}" if kind else "Context")
        self.metadata.set_title_shown(not echoes)
