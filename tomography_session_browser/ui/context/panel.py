"""The context panel: a heading over the metadata of what is in view.

Display only. ``MainWindow`` decides what to describe (the selection, the
tab's item or the scope) and hands over the title and text; this owns the
widgets, the heading rule and the cache that spares rebuilding a scope's
description it already shows.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

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
        #: The item ``show_item`` last described, with the title and text it
        #: wrote: the heading names the item only while the panel still shows
        #: exactly that (anything written since has replaced it).
        self._item: Any = None
        self._item_shown: tuple[str, str] | None = None
        #: The title and text of a scope's description, while it is still what
        #: the panel shows: on the Session tab the page titles the scope, so
        #: the panel's own title line would only repeat it.
        self._scope_shown: tuple[str, str] | None = None

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

    def show_item(self, title: str, text: str, *, item: Any, scope: bool = False) -> None:
        """Describe ``item``; ``item`` is what ``sync_heading`` matches to the page.

        ``scope`` says the description is a scope's summary (a session, sample or
        group), which the Session tab's own page titles.
        """

        self.metadata.set_title(title)
        self.metadata.set_text(text)
        self._item = item
        self._item_shown = (self.metadata.text(), self.metadata.toPlainText())
        self._scope_shown = self._item_shown if scope else None

    def describes(self, item: Any) -> bool:
        """Whether the panel is still showing the description of ``item``."""

        return (
            item is not None
            and item is self._item
            and self._item_shown == (self.metadata.text(), self.metadata.toPlainText())
        )

    def show_scope(self, scope_key: str, title: str, describe: Callable[[], str]) -> None:
        """Describe a scope, unless the panel already shows it.

        Building a scope's description walks the whole group. The title check
        keeps this self-correcting: if a selection overwrote the panel since,
        the titles differ and the description is rebuilt.
        """

        if self._scope_key == scope_key and self.shows_scope() and self.metadata.text() == title:
            return
        self.metadata.set_title(title)
        self.metadata.set_text(describe())
        self._scope_key = scope_key
        self._scope_shown = (self.metadata.text(), self.metadata.toPlainText())

    def shows_scope(self) -> bool:
        """Whether the panel still shows a scope's description, as it wrote it."""

        return self._scope_shown == (self.metadata.text(), self.metadata.toPlainText())

    def sync_heading(self, tab_label: str, page_item: Any) -> None:
        """Name the item once (harmonisation A1).

        When the panel describes the item the tab's page already titles
        (``page_item``), the heading says what kind of item it is ("TILT
        SERIES") and the panel's own copy of the name hides. Otherwise the
        heading is "CONTEXT" over the name. Display only: the title's text,
        and ``metadata.text()``, are unchanged. The rule is the item's
        identity, not a comparison of two strings that happen to match.
        """

        title = self.metadata.text()
        echoes = tab_label in CONTEXT_HEADING_KINDS and bool(title) and self.describes(page_item)
        # A summary: the Session tab titles the scope it describes, so the
        # heading stays "CONTEXT" and its own title line steps aside.
        titled_by_summary = tab_label == TabKey.SESSION and self.shows_scope()
        kind = CONTEXT_HEADING_KINDS[tab_label] if echoes else ""
        self.heading.setText(kind.upper() if kind else CONTEXT_HEADING)
        self.heading.setAccessibleName(f"{kind}: {title}" if kind else "Context")
        self.metadata.set_title_shown(not (echoes or titled_by_summary))
