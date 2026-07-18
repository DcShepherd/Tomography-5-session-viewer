"""Structured metadata panel.

Renders a block of "Key: value" / "Section:" / indented lines as a compact,
readable layout with elided paths and a copy button — replacing the previous
QPlainTextEdit which word-wrapped long absolute paths and crowded the panel.

Input is plain text (the same string ``describe_object`` already returns) so
the rest of the app does not need to learn a new data type. Lines are parsed
with simple, conservative heuristics:

* a line ending with ":" is a section header (or a key with an empty value);
* a line of the form ``key: value`` becomes a key/value row;
* a value that looks like a filesystem path is rendered with middle-elision
  and a tooltip + clipboard-copy button;
* a blank line renders as vertical spacing;
* anything else is rendered as a single muted row.

The widget is fully presentational — no domain imports.

When ``set_text`` is called repeatedly with text whose row structure hasn't
changed (e.g. tilt-frame scrubbing only updates the value of "Frame:" and
"Tilt angle:" lines), the panel updates the existing value labels in place
instead of tearing down and rebuilding the whole widget tree. This avoids
the visible flash that a full rebuild produces on every slider tick.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from tomography_session_browser.ui.icons import themed_icon
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel

_PATH_HINTS = ("/", "\\")
_PATH_KEY_HINTS = (
    "path",
    "folder",
    "directory",
    "mrc",
    "mdoc",
    "xml",
    "jpg",
    "image",
    "stack",
    "file",
)
_LABEL_ALIASES = {
    "Automatic name": "Auto name",
    "Loaded sessions": "Sessions",
    "Linked data collections": "Linked data collections",
    "Data collection magnification": "Magnification",
    "Pixel size (Original)": "Pixel size",
    "Date of data collection": "Date",
    "Total data collection time": "Duration",
    "Tile metadata files": "Tile metadata",
    "Linked batch positions": "Linked batch positions",
    "Linked tilt series": "Linked tilt series",
}
_KEY_COLUMN_MIN_WIDTH = 78
_KEY_COLUMN_MAX_WIDTH = 112
_PROVENANCE_TOOLTIP_KEYS = {
    "Detector",
    "Search spot size",
    "Acquisition spot size",
    "Probe mode",
}


def _display_key(key: str) -> str:
    return _LABEL_ALIASES.get(key, key)


def _key_label(key: str) -> QLabel:
    label = QLabel(f"{_display_key(key)}:")
    label.setObjectName("metaKey")
    label.setMinimumWidth(_KEY_COLUMN_MIN_WIDTH)
    label.setMaximumWidth(_KEY_COLUMN_MAX_WIDTH)
    label.setWordWrap(True)
    label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
    return label


def _looks_like_path(key: str, value: str) -> bool:
    if not value:
        return False
    lower_key = key.strip().lower()
    if any(hint in lower_key for hint in _PATH_KEY_HINTS):
        if any(hint in value for hint in _PATH_HINTS):
            return True
    if len(value) > 60 and any(hint in value for hint in _PATH_HINTS):
        return True
    if len(value) >= 3 and value[1:3] == ":\\":  # Windows drive letter
        return True
    if value.startswith("/") and len(value) > 12:
        return True
    return False


def _display_value_and_tooltip(key: str, value: str) -> tuple[str, str | None]:
    if key.strip() not in _PROVENANCE_TOOLTIP_KEYS:
        return value, None
    clean_value, separator, suffix = value.strip().rpartition(" (")
    if not separator or not suffix.endswith(")") or not clean_value.strip():
        return value, None
    source = suffix[:-1].strip()
    if not source:
        return value, None
    return clean_value.strip(), f"Source: {source}"


class _PathRow(QWidget):
    def __init__(self, key: str, value: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = key
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        layout.addWidget(_key_label(key))

        self._value = ElidedLabel(value, mode=Qt.TextElideMode.ElideMiddle, parent=self)
        self._value.setObjectName("metaValuePath")
        layout.addWidget(self._value, stretch=1)

        copy_button = QToolButton(self)
        copy_button.setObjectName("metaCopyButton")
        copy_button.setIcon(themed_icon("copy", size=14))
        copy_button.setIconSize(QSize(14, 14))
        copy_button.setToolTip("Copy full path")
        copy_button.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_button.setFixedSize(22, 22)
        copy_button.clicked.connect(self._copy_to_clipboard)
        layout.addWidget(copy_button, alignment=Qt.AlignmentFlag.AlignTop)

    def set_value(self, value: str) -> None:
        if self._value.text_full() != value:
            self._value.setText(value)

    def _copy_to_clipboard(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._value.text_full())


class _KeyValueRow(QWidget):
    def __init__(self, key: str, value: str, tooltip: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = key
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        layout.addWidget(_key_label(key))

        self._value_label = QLabel(value)
        self._value_label.setObjectName("metaValue")
        self._value_label.setWordWrap(True)
        self._value_label.setToolTip(tooltip or value)
        self._value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._value_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        layout.addWidget(self._value_label, stretch=1)

    def set_value(self, value: str, tooltip: str | None = None) -> None:
        if self._value_label.text() != value:
            self._value_label.setText(value)
        next_tooltip = tooltip or value
        if self._value_label.toolTip() != next_tooltip:
            self._value_label.setToolTip(next_tooltip)
        row_tooltip = tooltip or ""
        if self.toolTip() != row_tooltip:
            self.setToolTip(row_tooltip)


class _SectionHeader(QLabel):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("metaSection")


class _PlainRow(QLabel):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("metaPlain")
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)


@dataclass(frozen=True, slots=True)
class _RowSpec:
    """Parsed representation of one rendered row.

    ``signature`` identifies the row's *structure* (kind + key/identifying
    text) and is what the diff path compares between successive set_text
    calls. ``value`` is the mutable text inside the row that can be updated
    in place without rebuilding the widget.
    """

    kind: str  # "section" | "kv" | "path" | "plain" | "spacer"
    signature: tuple
    value: str
    indented: bool
    tooltip: str | None = None


class MetadataPanel(QWidget):
    """Right-hand context panel: structured rows with elided paths."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("metadataPanel")
        self.setAccessibleName("Context panel")
        self.setAccessibleDescription(
            "Structured metadata, warnings, file paths, and linked-object context for the current selection."
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        self._title = ElidedLabel("Context", mode=Qt.TextElideMode.ElideRight, parent=self)
        self._title.setObjectName("contextTitle")
        outer.addWidget(self._title)

        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("metadataScroll")
        self._scroll.viewport().setObjectName("metadataViewport")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self._scroll, stretch=1)

        self._body = QWidget()
        self._body.setObjectName("metadataBody")
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 2, 0, 2)
        self._body_layout.setSpacing(5)
        self._body_layout.addStretch(1)
        self._scroll.setWidget(self._body)

        # Parallel to the rows currently in ``_body_layout`` (excluding the
        # trailing stretch). Used by ``set_text`` to decide whether to update
        # in place or rebuild.
        self._row_specs: list[_RowSpec] = []
        self._row_widgets: list[QWidget] = []

        self._placeholder = "Metadata, warnings, and linked objects will appear here."
        self.set_text(self._placeholder)

    # ----- public API -----

    def set_title(self, text: str) -> None:
        self._title.setText(text or "Context")

    def set_text(self, text: str) -> None:
        self._raw_text = text or ""
        if not text or not text.strip():
            specs = [_RowSpec("plain", ("plain", self._placeholder), self._placeholder, False)]
        else:
            specs = list(self._parse(text.splitlines()))

        # Fast path: same structure as last render → just update mutable
        # values on the existing widgets. This is the common case for tilt
        # frame scrubbing and avoids the deleteLater/rebuild flash.
        if self._signatures_match(specs):
            self._update_in_place(specs)
            return

        # Structure changed → rebuild. Suppress paint updates so the user
        # doesn't see a momentary blank panel during teardown.
        self._body.setUpdatesEnabled(False)
        try:
            self._clear()
            for spec in specs:
                widget = self._build_widget(spec)
                self._add(widget)
                self._row_specs.append(spec)
                self._row_widgets.append(widget)
            self._finalize()
        finally:
            self._body.setUpdatesEnabled(True)

    def refresh_theme(self) -> None:
        for button in self.findChildren(QToolButton, "metaCopyButton"):
            button.setIcon(themed_icon("copy", size=14))

    # ----- legacy / Qt-style accessors --------------------------------------
    # The panel replaces a QLabel + QPlainTextEdit pair. Older callers expect
    # ``.text() / .setText()`` and ``.toPlainText() / .setPlainText()`` to keep
    # working, so we mirror those signatures. Tests rely on this as well.

    def text(self) -> str:
        return self._title.text_full()

    def toPlainText(self) -> str:  # noqa: N802 — Qt API name kept for compat
        return getattr(self, "_raw_text", "")

    def setText(self, text: str) -> None:  # noqa: N802 — Qt API name kept for compat
        self.set_title(text)

    def setPlainText(self, text: str) -> None:  # noqa: N802 — Qt API name kept for compat
        self.set_text(text)

    # ----- helpers -----

    def _clear(self) -> None:
        # Remove every widget except the trailing stretch.
        while self._body_layout.count() > 1:
            item = self._body_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._row_specs = []
        self._row_widgets = []

    def _add(self, widget: QWidget) -> None:
        # Insert before the trailing stretch item.
        self._body_layout.insertWidget(self._body_layout.count() - 1, widget)

    def _finalize(self) -> None:
        # Trigger a re-layout so newly added rows take their final size.
        self._body.adjustSize()

    def _signatures_match(self, specs: list[_RowSpec]) -> bool:
        if len(specs) != len(self._row_specs):
            return False
        for new_spec, old_spec in zip(specs, self._row_specs):
            if new_spec.kind != old_spec.kind:
                return False
            if new_spec.signature != old_spec.signature:
                return False
            # ``plain`` and ``section`` rows have no separate value slot, so
            # any text change is a structural change for those kinds.
            if new_spec.kind in {"plain", "section", "spacer"}:
                continue
            # For kv/path the indent class is part of the structure (it
            # changes contentsMargins, which we don't update in place).
            if new_spec.indented != old_spec.indented:
                return False
        return True

    def _update_in_place(self, specs: list[_RowSpec]) -> None:
        for spec, widget in zip(specs, self._row_widgets):
            if spec.kind == "kv":
                widget.set_value(spec.value, spec.tooltip)  # type: ignore[attr-defined]
            elif spec.kind == "path":
                widget.set_value(spec.value)  # type: ignore[attr-defined]
        self._row_specs = list(specs)

    def _build_widget(self, spec: _RowSpec) -> QWidget:
        if spec.kind == "spacer":
            spacer = QWidget()
            spacer.setFixedHeight(6)
            return spacer
        if spec.kind == "section":
            return _SectionHeader(spec.value)
        if spec.kind == "path":
            row: QWidget = _PathRow(spec.signature[1], spec.value)
        elif spec.kind == "kv":
            row = _KeyValueRow(spec.signature[1], spec.value, spec.tooltip)
        else:  # plain
            row = _PlainRow(spec.value)
        if spec.indented:
            row.setContentsMargins(12, 0, 0, 0)
        return row

    def _parse(self, lines: Iterable[str]) -> Iterable[_RowSpec]:
        for raw in lines:
            line = raw.rstrip()
            if not line.strip():
                yield _RowSpec("spacer", ("spacer",), "", False)
                continue
            stripped = line.lstrip()
            indented = len(line) - len(stripped) > 0
            if ":" in stripped:
                key, _, value = stripped.partition(":")
                key = key.strip()
                value = value.strip()
                if value == "":
                    yield _RowSpec("section", ("section", key), key, indented)
                    continue
                if _looks_like_path(key, value):
                    yield _RowSpec("path", ("path", key, indented), value, indented)
                    continue
                value, tooltip = _display_value_and_tooltip(key, value)
                yield _RowSpec("kv", ("kv", key, indented), value, indented, tooltip)
                continue
            yield _RowSpec("plain", ("plain", stripped), stripped, indented)
