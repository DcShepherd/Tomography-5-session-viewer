"""A viewer list's filter: status segments, a text box, and the line that says what is shown.

The bar owns one canonical query (``is:failed alpha``) behind ``query()`` and
``set_query()``. The segments show and set the one status tag they own; the box
is a view of the rest of the query, so any other tag (``is:missing-mdoc``) stays
visible as text and nothing the query asks for is hidden. The box's clear
button clears its text; Clear all filters and Escape clear the whole query.

Nothing outside the list pane touches the box: dashboard routes, history and
rebuilt lists all go through the query.
"""
from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QResizeEvent, QShortcut, QShowEvent
from PySide6.QtWidgets import QBoxLayout, QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit, QToolButton, QVBoxLayout, QWidget

from tomography_session_browser.presentation.design_tokens import SPACE_S
from tomography_session_browser.services.item_status import SEGMENT_TAGS, join_filter_query, split_owned_tag
from tomography_session_browser.ui.theme import TIER_SUPPORTING, apply_tier
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel

SEGMENT_LABELS = {"failed": "Failed", "incomplete": "Incomplete", "complete": "Complete", "missing": "Missing",
                  "with-acquisitions": "With acquisitions", "no-acquisitions": "No acquisitions"}


class ListFilterBar(QWidget):
    """Segments, box and result line for one list."""

    query_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None, *, offered: tuple[str, ...] = ("failed", "complete")) -> None:
        super().__init__(parent)
        self.setObjectName("viewerFilterBar")
        # The status tags a segment owns, in the order the segments are drawn.
        self._offered = tuple(tag for tag in SEGMENT_TAGS if tag in offered)
        self._status: str | None = None
        self.row_keys: frozenset[str] | None = None
        self.scope_label = ""
        self._syncing = False
        self._clear_button_hooked = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_S)

        self._segments = QFrame(self)
        self._segments.setObjectName("segmentedControl")
        self._segments.setAccessibleName("Status filter")
        row = QHBoxLayout(self._segments)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self._group = QButtonGroup(self._segments)
        self._group.setExclusive(True)
        self._buttons: dict[str | None, QToolButton] = {}
        for tag in (None, *self._offered):
            button = QToolButton(self._segments)
            button.setObjectName("segmentButton")
            button.setCheckable(True)
            button.setToolTip("Every item" if tag is None else f"Only {SEGMENT_LABELS[tag].lower()} items")
            button.clicked.connect(lambda _checked=False, chosen=tag: self._segment_chosen(chosen))
            self._group.addButton(button)
            row.addWidget(button)
            self._buttons[tag] = button
        layout.addWidget(self._segments)

        self.box = QLineEdit(self)
        self.box.setObjectName("viewerFilter")
        self.box.setPlaceholderText("Filter items...")
        self.box.setToolTip("Filter this list by name, status, or summary")
        self.box.setAccessibleName("Filter viewer items")
        self.box.setClearButtonEnabled(True)
        self.box.textChanged.connect(self._box_changed)
        # Escape in the box clears the whole query (not only its text).
        self._escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self.box)
        self._escape.setContext(Qt.ShortcutContext.WidgetShortcut)
        self._escape.activated.connect(lambda: self.set_query(""))
        layout.addWidget(self.box)

        self._line = QWidget(self)
        self._line.setObjectName("viewerResultLine")
        line = QHBoxLayout(self._line)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(SPACE_S)
        text = self._result_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(0)
        self._result = QLabel("", self._line)
        apply_tier(self._result, TIER_SUPPORTING)
        text.addWidget(self._result)
        self._provenance = ElidedLabel(mode=Qt.TextElideMode.ElideMiddle, parent=self._line, prefer_full_width=True)
        self._provenance.setTextFormat(Qt.TextFormat.PlainText)
        apply_tier(self._provenance, TIER_SUPPORTING)
        self._provenance.setVisible(False)
        text.addWidget(self._provenance)
        line.addLayout(text, stretch=1)
        self._clear = QToolButton(self._line)
        self._clear.setObjectName("inlineLink")
        self._clear.setText("Clear all filters")
        self._clear.setToolTip("Clear text, status and the summary's row restriction")
        self._clear.setAccessibleName("Clear all filters")
        self._clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear.clicked.connect(lambda: self.set_query(""))
        line.addWidget(self._clear)
        layout.addWidget(self._line)
        self._line.setVisible(False)
        self.feedback = QLabel("", self)
        self.feedback.setObjectName("viewerFilterFeedback")
        self.feedback.setWordWrap(True)
        apply_tier(self.feedback, TIER_SUPPORTING)
        layout.addWidget(self.feedback)
        self.feedback.hide()
        self.set_counts({}, 0)

    # ------------------------------------------------------------------- query

    def query(self) -> str:
        """The whole query: the segment's status tag, then the box's text."""

        return join_filter_query(self._status, self.box.text())

    def set_query(self, text: str, *, notify: bool = True,
                  row_keys: frozenset[str] | None = None, scope_label: str = "") -> None:
        """Make ``text`` the query. ``set_query("")`` clears the status tag and the text."""

        status, rest = split_owned_tag(text, self._offered)
        self.row_keys = row_keys
        self.scope_label = scope_label if row_keys is not None else ""
        self._status = status
        self._syncing = True
        try:
            self.box.setText(rest)
        finally:
            self._syncing = False
        self._sync_segments()
        self._line.setVisible(self.is_filtered())
        if notify:
            self.query_changed.emit(self.query())

    def _segment_chosen(self, tag: str | None) -> None:
        self._status = tag
        self._sync_segments()
        self._line.setVisible(self.is_filtered())
        self.query_changed.emit(self.query())

    def _box_changed(self, _text: str) -> None:
        if self._syncing:
            return
        self._line.setVisible(self.is_filtered())
        self.query_changed.emit(self.query())

    # ----------------------------------------------------------------- display

    def is_filtered(self) -> bool:
        return bool(self.query()) or self.row_keys is not None

    def description(self) -> str:
        scope = f"From {self.scope_label or 'summary'}" if self.row_keys is not None else ""
        return " · ".join(part for part in (self.query(), scope) if part)

    def _sync_segments(self) -> None:
        for tag, button in self._buttons.items():
            button.setChecked(tag == self._status)

    def set_counts(self, counts: Mapping[str, int], total: int) -> None:
        """Each segment's count: the rows that carry its tag. A segment with
        none is dimmed and cannot be chosen (the current one stays as it is)."""

        for tag, button in self._buttons.items():
            count = total if tag is None else counts.get(tag, 0)
            label = "All" if tag is None else SEGMENT_LABELS[tag]
            button.setText(f"{label} {count:,}")
            button.setAccessibleName(f"{label}, {count} items")
            button.setEnabled(tag is None or count > 0 or tag == self._status)

    def set_result(self, visible: int, total: int) -> None:
        """"Showing 2 of 20", on the line that is there only while a query is set."""

        self._result.setText(f"Showing {visible:,} of {total:,}")
        self._result.setAccessibleName(f"Showing {visible} of {total} items")
        provenance = f"From {self.scope_label}" if self.scope_label else ""
        self._provenance.setText(provenance)
        self._provenance.setAccessibleName(provenance)
        self._provenance.setVisible(bool(provenance))
        self._fit_result()

    def _fit_result(self) -> None:
        """Keep short scopes inline; long names get one separately elided line."""
        full = self._result.fontMetrics().horizontalAdvance(self._result.text())
        if self.scope_label:
            full += SPACE_S + self._provenance.fontMetrics().horizontalAdvance(self._provenance.text_full())
        available = self.width() - self._clear.sizeHint().width() - SPACE_S
        stacked = bool(self.scope_label) and full > available
        self._result_layout.setDirection(QBoxLayout.Direction.TopToBottom if stacked else QBoxLayout.Direction.LeftToRight)
        self._result_layout.setSpacing(0 if stacked else SPACE_S)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._fit_result()

    def set_entity(self, plural: str) -> None:
        self.box.setPlaceholderText(f"Filter {plural.lower()}...")
        self.box.setAccessibleName(f"Filter {plural.lower()}")

    def set_feedback(self, text: str) -> None:
        """Explain the relation between the visible rows and the retained image."""
        self.feedback.setText(text)
        self.feedback.setVisible(bool(text))

    def result_text(self) -> str:
        """Full result/provenance text; empty while no query or restriction is set.

        Only the visible scope label is elided. The canonical string stays
        available to callers, accessibility, history and filter descriptions.
        """

        if not self._line.isVisibleTo(self):
            return ""
        return " · ".join(part for part in (self._result.text(), self._provenance.text_full()) if part)

    def status(self) -> str | None:
        """The status tag the chosen segment owns; ``None`` while All is chosen."""

        return self._status

    def segment_texts(self) -> list[str]:
        """The segments as drawn, All first: ``["All 20", "Failed 2", "Complete 18"]``."""

        return [button.text() for button in self._buttons.values()]

    def segment(self, tag: str | None) -> QToolButton:
        """The button of a segment (``None`` for All), for a caller that presses it."""

        return self._buttons[tag]

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt signature
        super().showEvent(event)
        self._hook_clear_button()

    def _hook_clear_button(self) -> None:
        """Name Qt's text-only clear action; its normal signal preserves the query's other parts."""

        if self._clear_button_hooked:
            return
        for button in self.box.findChildren(QToolButton):
            button.setAccessibleName("Clear text")
            button.setToolTip("Clear text; keep the status and summary filters")
            self._clear_button_hooked = True
