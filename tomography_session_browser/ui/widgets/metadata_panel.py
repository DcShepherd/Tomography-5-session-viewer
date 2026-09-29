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
import math
import re
from typing import Iterable

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtGui import QFontMetrics, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from tomography_session_browser.ui.icons import themed_icon
from tomography_session_browser.ui.motion import SmoothScroller
from tomography_session_browser.ui.theme import SPACE_M, SPACE_S, SPACE_XS
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
    "Batch positions": "Batch targets",
    "Linked data collections": "Collections",
    "Data collection magnification": "Magnification",
    "Pixel size (Original)": "Pixel size",
    "Date of data collection": "Date",
    "Total data collection time": "Duration",
    "Tile metadata files": "Tile metadata",
    "Linked batch positions": "Linked targets",
    "Linked tilt series": "Linked tilt series",
    "Acquisition spot size": "Spot size",
    "Expected source": "Count source",
    "Number of frames": "Frames",
}
_KEY_COLUMN_MIN_WIDTH = 88
_KEY_COLUMN_MAX_WIDTH = 240
_KEY_COLUMN_VIEWPORT_FRACTION = 0.62
_VALUE_COLUMN_MIN_WIDTH = 88
# The key column fits the *typical* key, not the longest: one outlier such as
# "Frames with parsed metadata" used to widen the column for every row and
# squeeze each value onto three lines. The outlier key wraps instead.
_KEY_COLUMN_PERCENTILE = 0.75
_KEY_OUTLIER_RATIO = 1.5
# Room between the value column and the scroll bar.
_BODY_RIGHT_MARGIN = SPACE_S
_EMPTY_TITLE = "No selection"

# Display-only glue for values. A number stays with its unit, and a unit such
# as "Å/px" never breaks at its slash (Qt's line breaker allows a break after
# "/"). Both characters are removed again when a value is copied.
_WORD_JOINER = "⁠"
_NO_BREAK_SPACE = " "
_UNIT_TOKENS = frozenset(
    {
        "Å", "Å/px", "nm", "nm/px", "µm", "μm", "µm/px", "μm/px", "um", "mm", "pm",
        "px", "kV", "V", "eV", "keV", "s", "ms", "min", "h", "°", "%", "×", "Hz",
        "fps", "e/Å²", "e⁻/Å²", "e-/Å²", "e/Å^2", "MB", "GB", "KB",
    }
)
_NUMBER_RE = re.compile(r"^[-+±~≈]?\d[\d.,]*$")
_PATH_SEPARATOR_RE = re.compile(r"[\\/]+")


def display_value(value: str) -> str:
    """Return ``value`` with display-only glue that keeps units intact."""

    words = value.split(" ")
    glued: list[str] = []
    for word in words:
        if "/" in word.strip("/") and not word.startswith("/"):
            word = word.replace("/", f"{_WORD_JOINER}/{_WORD_JOINER}")
        glued.append(word)
    if not glued:
        return value
    result = glued[0]
    for previous, raw, word in zip(words, words[1:], glued[1:]):
        unit = raw.rstrip(",;:)")
        number = previous.lstrip("(")
        separator = _NO_BREAK_SPACE if unit in _UNIT_TOKENS and _NUMBER_RE.match(number) else " "
        result += separator + word
    return result


def clean_copied_text(text: str) -> str:
    """Undo :func:`display_value` so copied values are the source text."""

    return text.replace(_WORD_JOINER, "").replace(_NO_BREAK_SPACE, " ")


def short_path(path: str) -> str:
    """``parent › name`` for a filesystem path, the part that identifies it."""

    parts = [part for part in _PATH_SEPARATOR_RE.split(path.strip()) if part]
    if len(parts) >= 2:
        return f"{parts[-2]} › {parts[-1]}"
    return parts[-1] if parts else path
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


class _PathValueLabel(ElidedLabel):
    """A path that shows its identifying end when the whole path cannot fit.

    Middle-eliding a full Windows path at dock width produced ``C:\\…mrc``,
    which identifies nothing. Show the longest of these that fits: the full
    path, ``parent › name``, the file name, or the file name middle-elided.
    ``text_full()``, the tooltip and the copy button keep the complete path.
    """

    def _apply_elision(self) -> None:
        metrics = QFontMetrics(self.font())
        width = max(self.width() - 4, 12)
        full = self.text_full()
        short = short_path(full)
        name = short.rsplit(" › ", 1)[-1]
        for candidate in (full, short, name):
            if metrics.horizontalAdvance(candidate) <= width:
                shown = candidate
                break
        else:
            shown = metrics.elidedText(name, Qt.TextElideMode.ElideMiddle, width)
        QLabel.setText(self, shown)


class _ValueLabel(QLabel):
    """A metadata value that never widens the panel and never splits a unit.

    * A multi-word value wraps at spaces, with numbers glued to their units.
    * A single unbreakable token (an identifier such as
      ``SearchMap_20260320_022428``) is middle-elided with the full value in the
      tooltip. Such a token used to force the whole panel body wider than the
      dock, which then clipped *every* row at the right edge.
    * Copying returns the source text, without the display-only glue.
    """

    def __init__(self, value: str, tooltip: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("metaValue")
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        # Ignored: the row gives the value whatever width is left, and the
        # value's own text can never demand more.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        self.setMinimumWidth(1)
        self._full = ""
        self._extra_tooltip: str | None = None
        self._single_token: bool | None = None
        self.set_value(value, tooltip)

    def value(self) -> str:
        return self._full

    def set_value(self, value: str, tooltip: str | None = None) -> None:
        self._full = value
        self._extra_tooltip = tooltip or None
        single_token = bool(value) and not any(character.isspace() for character in value)
        if single_token != self._single_token:
            self._single_token = single_token
            self.setWordWrap(not single_token)
        self._refresh()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt signature
        super().resizeEvent(event)
        if self._single_token:
            self._refresh()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt signature
        super().showEvent(event)
        if self._single_token:
            self._refresh()

    def _refresh(self) -> None:
        if self._single_token:
            # Elide only against a real, laid-out width. Before the label is
            # shown its geometry is a placeholder, and eliding against it
            # truncated short values such as "Nanoprobe".
            if self.isVisible():
                width = max(self.width() - 2, 12)
                shown = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideMiddle, width)
            else:
                shown = self._full
        else:
            shown = display_value(self._full)
        if super().text() != shown:
            super().setText(shown)
        elided = bool(self._single_token) and shown != self._full
        tooltip = self._extra_tooltip or (self._full if elided else "")
        if self.toolTip() != tooltip:
            self.setToolTip(tooltip)

    def _text_to_copy(self) -> str:
        if self._single_token and super().text() != self._full:
            return self._full
        return clean_copied_text(self.selectedText() or self._full)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt signature
        if event.matches(QKeySequence.StandardKey.Copy):
            _set_clipboard(self._text_to_copy())
            event.accept()
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt signature
        menu = QMenu(self)
        copy_selection = menu.addAction("Copy")
        copy_selection.setEnabled(self.hasSelectedText())
        copy_selection.triggered.connect(lambda: _set_clipboard(self._text_to_copy()))
        copy_value = menu.addAction("Copy value")
        copy_value.triggered.connect(lambda: _set_clipboard(self._full))
        menu.exec(event.globalPos())


def _set_clipboard(text: str) -> None:
    clipboard = QGuiApplication.clipboard()
    if clipboard is not None:
        clipboard.setText(text)


class _PathRow(QWidget):
    def __init__(self, key: str, value: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = key
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_S)

        layout.addWidget(_key_label(key))

        self._value = _PathValueLabel(value, mode=Qt.TextElideMode.ElideMiddle, parent=self)
        self._value.setObjectName("metaValuePath")
        layout.addWidget(self._value, stretch=1)

        copy_button = QToolButton(self)
        copy_button.setObjectName("metaCopyButton")
        copy_button.setIcon(themed_icon("copy", size=14))
        copy_button.setIconSize(QSize(14, 14))
        copy_button.setToolTip("Copy full path")
        copy_button.setAccessibleName(f"Copy {key.lower()}")
        copy_button.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_button.setFixedSize(28, 28)
        self._copy_button = copy_button
        copy_button.clicked.connect(self._copy_to_clipboard)
        layout.addWidget(copy_button, alignment=Qt.AlignmentFlag.AlignTop)

    def set_value(self, value: str) -> None:
        if self._value.text_full() != value:
            self._value.setText(value)

    def _copy_to_clipboard(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._value.text_full())
            original_tooltip = self._copy_button.toolTip()
            self._copy_button.setToolTip("Copied")
            QToolTip.showText(
                self._copy_button.mapToGlobal(self._copy_button.rect().bottomLeft()),
                "Copied",
                self._copy_button,
            )
            QTimer.singleShot(
                1200,
                lambda button=self._copy_button, tooltip=original_tooltip: button.setToolTip(tooltip),
            )


class _KeyValueRow(QWidget):
    def __init__(self, key: str, value: str, tooltip: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = key
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_S)

        layout.addWidget(_key_label(key))

        self._value_label = _ValueLabel(value, tooltip, self)
        layout.addWidget(self._value_label, stretch=1)

    def set_value(self, value: str, tooltip: str | None = None) -> None:
        self._value_label.set_value(value, tooltip)
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
        outer.setSpacing(SPACE_S)

        # The panel's own "CONTEXT" heading now sits above this line (plan
        # C11), so with nothing selected the title says that rather than
        # repeating the heading.
        self._title = ElidedLabel(_EMPTY_TITLE, mode=Qt.TextElideMode.ElideRight, parent=self)
        self._title.setObjectName("contextTitle")
        outer.addWidget(self._title)

        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("metadataScroll")
        self._scroll.viewport().setObjectName("metadataViewport")
        self._scroll.viewport().installEventFilter(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        SmoothScroller(self._scroll)  # eased wheel scrolling (plan F5.5)
        outer.addWidget(self._scroll, stretch=1)

        self._body = QWidget()
        self._body.setObjectName("metadataBody")
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, SPACE_XS, _BODY_RIGHT_MARGIN, SPACE_XS)
        self._body_layout.setSpacing(SPACE_XS)
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
        self._title.setText(text or _EMPTY_TITLE)

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
        self._update_key_column_width()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt signature
        super().resizeEvent(event)
        self._update_key_column_width()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt signature
        if watched is self._scroll.viewport() and event.type() == QEvent.Type.Resize:
            self._update_key_column_width()
        return super().eventFilter(watched, event)

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
        # Every row owns its own horizontal layout, so Qt cannot align their
        # first columns for us. Derive one shared width from the rendered font
        # and the current viewport before laying the body out.
        self._update_key_column_width()
        self._body.adjustSize()

    def _update_key_column_width(self) -> None:
        labels = self.findChildren(QLabel, "metaKey")
        if not labels:
            return
        viewport_width = self._scroll.viewport().width()
        if viewport_width <= 0:
            return

        widths = sorted(
            label.fontMetrics().horizontalAdvance(label.text()) + 4
            for label in labels
        )
        # Fit every ordinary key on one line, but let a genuine outlier (much
        # longer than the typical key) wrap rather than squeezing every value.
        typical = widths[max(0, math.ceil(len(widths) * _KEY_COLUMN_PERCENTILE) - 1)]
        required = max(width for width in widths if width <= typical * _KEY_OUTLIER_RATIO)
        viewport_width -= _BODY_RIGHT_MARGIN
        available = max(_KEY_COLUMN_MIN_WIDTH, viewport_width - _VALUE_COLUMN_MIN_WIDTH)
        responsive_cap = max(
            _KEY_COLUMN_MIN_WIDTH,
            int(viewport_width * _KEY_COLUMN_VIEWPORT_FRACTION),
        )
        width = max(
            _KEY_COLUMN_MIN_WIDTH,
            min(required, available, responsive_cap, _KEY_COLUMN_MAX_WIDTH),
        )
        for label in labels:
            label.setFixedWidth(width)

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
            spacer.setFixedHeight(SPACE_S)
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
            row.setContentsMargins(SPACE_M, 0, 0, 0)
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
