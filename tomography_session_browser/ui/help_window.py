"""Modeless, searchable Help using semantic records and shared native styling."""
from html import escape
from urllib.parse import quote, unquote

from PySide6.QtCore import QEvent, QRectF, QSignalBlocker, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QImage, QKeySequence, QPainter, QTextDocument
from PySide6.QtWidgets import QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton, QTextBrowser, QVBoxLayout, QWidget

import tomography_session_browser
from tomography_session_browser.presentation.context_stack import entity_display_label
from tomography_session_browser.presentation.design_tokens import MONO_FONT_NAME, SPACE_M, SPACE_S, TYPE_BODY, TYPE_CAPTION, TYPE_HEADING, TYPE_LEAD
from tomography_session_browser.presentation.help_content import (
    HelpBinding, HelpPage, InteractionKind, PAGE_LABELS, bindings_for_tab, search_help,
)
from tomography_session_browser.presentation.help_pages import help_sections
from tomography_session_browser.presentation.tab_keys import TabKey, VIEWER_TAB_KEYS
from tomography_session_browser.ui.list_decorations import PROJECT_BADGE_SIZE, paint_project_badge
from tomography_session_browser.ui.theme import current_palette


def eligibility_label(tabs: frozenset[TabKey]) -> str:
    if tabs == frozenset(TabKey):
        return "Everywhere"
    if tabs == frozenset(VIEWER_TAB_KEYS):
        return "Image tabs"
    return ", ".join(entity_display_label(tab) for tab in TabKey if tab in tabs)


class HelpWindow(QDialog):
    def __init__(self, parent: QWidget, *, load_action: QAction | None = None):
        self.ready = False
        super().__init__(parent, Qt.WindowType.Window)
        self.setObjectName("helpWindow")
        self.setWindowTitle("Help · Tomography Session Browser")
        self.setModal(False)
        self.setSizeGripEnabled(True)
        self.bindings: tuple[HelpBinding, ...] = ()
        self.sections = help_sections()
        self.page = HelpPage.START
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACE_M, SPACE_M, SPACE_M, SPACE_M)
        root.setSpacing(SPACE_M)
        search_row = QHBoxLayout()
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Search all Help pages…")
        self.search.setAccessibleName("Search Help")
        self.clear_search = QPushButton("Clear search", self)
        self.clear_search.setAutoDefault(False)
        self.clear_search.clicked.connect(self.clear_query)
        search_row.addWidget(self.search, 1)
        search_row.addWidget(self.clear_search)
        root.addLayout(search_row)
        body = QHBoxLayout()
        body.setSpacing(SPACE_M)
        self.navigation = QListWidget(self)
        self.navigation.setAccessibleName("Help pages")
        self.navigation.setFixedWidth(180)
        for label in PAGE_LABELS.values():
            self.navigation.addItem(label)
        body.addWidget(self.navigation)
        column = QVBoxLayout()
        column.setSpacing(SPACE_S)
        self.tab_chooser = QComboBox(self)
        self.tab_chooser.setAccessibleName("Controls for tab")
        self.tab_chooser.addItem("All tabs", None)
        for tab in TabKey:
            self.tab_chooser.addItem(entity_display_label(tab), tab)
        column.addWidget(self.tab_chooser)
        self.start_intro = QWidget(self)
        intro_layout = QVBoxLayout(self.start_intro)
        intro_layout.setContentsMargins(0, 0, 0, 0)
        intro_layout.setSpacing(SPACE_S)
        intro = next(section for section in self.sections if section.identity == "start.intro")
        title = QLabel(PAGE_LABELS[HelpPage.START], self.start_intro)
        title.setObjectName("screenTitle")
        intro_layout.addWidget(title)
        for text in (intro.title, *intro.paragraphs):
            label = QLabel(text, self.start_intro)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            intro_layout.addWidget(label)
        self.load_button = QPushButton("Load sessions…", self)
        self.load_button.setObjectName("primaryButton")
        self.load_button.setAutoDefault(False)
        if load_action is None:
            self.load_button.setEnabled(False)
            self.load_button.setToolTip("Open Help from the main window to load sessions")
        else:
            self.addAction(load_action)
            self.load_button.setEnabled(load_action.isEnabled())
            self.load_button.clicked.connect(load_action.trigger)
            load_action.changed.connect(lambda: self.load_button.setEnabled(load_action.isEnabled()))
            self.load_button.setToolTip(load_action.toolTip())
        load_row = QHBoxLayout()
        load_row.addWidget(self.load_button)
        key = QLabel(load_action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
                     if load_action is not None else "", self.start_intro)
        key.setObjectName("viewerStatus")
        load_row.addWidget(key)
        load_row.addStretch(1)
        intro_layout.addLayout(load_row)
        column.addWidget(self.start_intro)
        self.browser = QTextBrowser(self)
        self.browser.setObjectName("helpDocument")
        self.browser.setAccessibleName("Help content")
        self.browser.setFrameShape(QFrame.Shape.NoFrame)
        self.browser.setOpenLinks(False)
        self.browser.setOpenExternalLinks(False)
        self.browser.anchorClicked.connect(self.follow_link)
        column.addWidget(self.browser, 1)
        body.addLayout(column, 1)
        root.addLayout(body, 1)
        footer = QHBoxLayout()
        self.about = QLabel(f"Tomography Session Browser · {tomography_session_browser.__version__}", self)
        self.about.setObjectName("viewerStatus")
        footer.addWidget(self.about)
        footer.addStretch(1)
        close = QPushButton("Close", self)
        close.setAutoDefault(False)
        close.clicked.connect(self.close)
        footer.addWidget(close)
        root.addLayout(footer)
        self.navigation.currentRowChanged.connect(self.page_changed)
        self.tab_chooser.currentIndexChanged.connect(self.render)
        self.search.textChanged.connect(self.render)
        area = parent.screen().availableGeometry()
        self.setMinimumSize(min(640, area.width() - 48), min(420, area.height() - 48))
        self.resize(min(920, area.width() - 48), min(760, area.height() - 48))
        self.ready = True
        parent.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - QWidget facade
        if watched is self.parentWidget() and event.type() == QEvent.Type.Close:
            # Close handlers can decline while tag recovery is unresolved.
            QTimer.singleShot(0, self, self.close_with_owner)
        return super().eventFilter(watched, event)

    def close_with_owner(self) -> None:
        owner = self.parentWidget()
        if owner is not None and not owner.isVisible():
            self.close()

    def open_page(self, page: HelpPage, current: TabKey | None, bindings: tuple[HelpBinding, ...]) -> None:
        self.bindings = bindings
        with QSignalBlocker(self.search), QSignalBlocker(self.tab_chooser), QSignalBlocker(self.navigation):
            self.search.clear()
            self.tab_chooser.setCurrentIndex(self.tab_chooser.findData(current))
            self.navigation.setCurrentRow(tuple(HelpPage).index(page))
        self.page = page
        self.render()

    def page_changed(self, row: int) -> None:
        if 0 <= row < len(HelpPage):
            self.page = tuple(HelpPage)[row]
            with QSignalBlocker(self.search):
                self.search.clear()
            self.render()

    def clear_query(self) -> None:
        self.search.clear()
        self.search.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def changeEvent(self, event) -> None:  # noqa: N802 - QWidget facade
        super().changeEvent(event)
        if self.ready and event.type() in (QEvent.Type.StyleChange, QEvent.Type.PaletteChange, QEvent.Type.FontChange):
            position = self.browser.verticalScrollBar().value()
            self.render()
            self.browser.verticalScrollBar().setValue(position)

    def badge_resources(self) -> None:
        palette, dpr = current_palette(), self.devicePixelRatioF()
        for badge in dict.fromkeys(section.badge for section in self.sections if section.badge):
            width, height = PROJECT_BADGE_SIZE
            image = QImage(round(width*dpr), round(height*dpr), QImage.Format.Format_ARGB32_Premultiplied)
            image.setDevicePixelRatio(dpr)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            try:
                paint_project_badge(painter, QRectF(0, 0, width, height), badge, palette)
            finally:
                painter.end()
            self.browser.document().addResource(QTextDocument.ResourceType.ImageResource, QUrl("badge:" + badge), image)

    def render(self, *_args) -> None:
        if not self.ready:
            return
        palette = current_palette()
        query = self.search.text().strip()
        self.clear_search.setVisible(bool(query))
        self.tab_chooser.setVisible(not query and self.page == HelpPage.KEYBOARD)
        self.start_intro.setVisible(not query and self.page == HelpPage.START)
        self.browser.document().setDefaultStyleSheet(f"""
            body {{ color: {palette.text}; font-size: {TYPE_BODY}pt; }}
            h1 {{ color: {palette.text_strong}; font-size: {TYPE_HEADING}pt; margin-bottom: 12px; }}
            h2 {{ color: {palette.text_strong}; font-size: {TYPE_LEAD}pt; margin-top: 20px; margin-bottom: 6px; }}
            p {{ margin-top: 4px; margin-bottom: 12px; line-height: 135%; }}
            a {{ color: {palette.accent}; }}
            .context {{ color: {palette.text_muted}; font-size: {TYPE_CAPTION}pt; }}
            .key {{ font-family: '{MONO_FONT_NAME}'; background-color: {palette.surface_hi};
                    color: {palette.text_strong}; border: 1px solid {palette.border}; padding: 4px; }}
            .binding {{ border-bottom: 1px solid {palette.border}; padding: 8px 0; }}
        """)
        chunks = []
        if query:
            from tomography_session_browser.domain.display_names import count_phrase
            hits = search_help(query, self.sections, self.bindings)
            chunks.append(f"<h1>{escape(count_phrase(len(hits), 'Help result')) if hits else 'No Help results'}</h1>")
            if not hits:
                chunks.append("<p>Try different words, or use Clear search.</p>")
            for hit in hits:
                link = f"help://{hit.page.value}/{quote(hit.section_id, safe='')}"
                context = hit.context
                if hit.binding is not None:
                    context = PAGE_LABELS[hit.page] + " · " + context + " · " + eligibility_label(hit.binding.tabs)
                chunks.append(f'<h2><a href="{escape(link)}">{escape(hit.title)}</a></h2>'
                              f'<p class="context">{escape(context)}</p>'
                              f'<p>{escape(hit.text)}</p>')
        else:
            if self.page != HelpPage.START:
                chunks.append(f"<h1>{escape(PAGE_LABELS[self.page])}</h1>")
            for section in self.sections:
                if section.page == self.page and section.identity != "start.intro":
                    width, height = PROJECT_BADGE_SIZE
                    badge = f'<img src="badge:{escape(section.badge)}" width="{width}" height="{height}"> ' if section.badge else ""
                    chunks.append(f'<a name="{escape(section.identity)}"></a><h2>{badge}{escape(section.title)}</h2>')
                    chunks.extend(f"<p>{escape(paragraph)}</p>" for paragraph in section.paragraphs)
            if self.page == HelpPage.KEYBOARD:
                rows = bindings_for_tab(self.bindings, self.tab_chooser.currentData())
                for kind, heading in ((InteractionKind.KEYBOARD, "Keyboard"), (InteractionKind.MOUSE, "Mouse and pointer")):
                    relevant = [binding for binding in rows if binding.kind == kind]
                    if relevant:
                        chunks.append(f"<h2>{heading}</h2>")
                    for binding in relevant:
                        keys = "<td> / </td>".join(f'<td class="key">{escape(token)}</td>' for token in binding.tokens)
                        chunks.append(f'<a name="{escape(binding.identity)}"></a><table cellspacing="3"><tr>{keys}</tr></table>'
                            f'<p class="binding"><b>{escape(binding.description)}</b><br>'
                            f'<span class="context">{escape(binding.context)} · {escape(eligibility_label(binding.tabs))}</span></p>')
        self.browser.setHtml("".join(chunks))
        self.badge_resources()

    def follow_link(self, url: QUrl) -> None:
        if url.scheme() != "help":
            return
        try:
            page = HelpPage(url.host())
        except ValueError:
            return
        anchor = unquote(url.path().lstrip("/"))
        current = self.tab_chooser.currentData()
        binding = next((binding for binding in self.bindings if binding.identity == anchor), None)
        if binding is not None and current not in binding.tabs:
            current = None  # Show the match under All tabs rather than hiding it.
        self.open_page(page, current, self.bindings)
        self.browser.scrollToAnchor(anchor)
