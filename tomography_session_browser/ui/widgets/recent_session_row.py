"""The "Recent:" row in the Load sessions window: recent folders, one click away.

The candidates are fixed when the row is made. Only width decides how many show,
and only the queue decides which are available, so a chip never changes place or
meaning under the pointer. A chip names a folder and asks for it to be queued; it
reads no settings, looks at no folder and loads nothing.

A label is display text. Each chip keeps its own stored path, and no path is ever
recovered from what a chip says.

A chip acts when Return or Enter is released, as a button does for Space. The
window moves focus to Load after a keyboard choice, so a chip that acted on the
press would let one held key choose a folder and then load it.
"""

from __future__ import annotations

import enum
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetrics, QKeyEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyleOptionButton,
    QWidget,
)

from tomography_session_browser.domain.identity import path_identity_key
from tomography_session_browser.presentation.design_tokens import SPACE_S
from tomography_session_browser.presentation.help_content import InteractionKind, LocalBinding
from tomography_session_browser.ui.widgets.literal_text import literal_mnemonic, literal_tooltip

RECENT_HEADING = "Recent:"
ALREADY_QUEUED = "Already in the list"
ADD_TO_QUEUE = "Add to the list"
#: The most text one chip shows, in logical pixels.
CHIP_TEXT_LIMIT = 220
#: What a first chip must keep room for, so that it never shrinks to a lone "…".
_SHORTEST_CHIP_TEXT = "x" * 10
#: Events after which text may measure differently at an unchanged width.
METRIC_CHANGE_EVENTS = frozenset(
    {QEvent.Type.FontChange, QEvent.Type.StyleChange, QEvent.Type.DevicePixelRatioChange}
)

_MIDDLE = Qt.TextElideMode.ElideMiddle
_ENTER_KEYS = frozenset({Qt.Key.Key_Return, Qt.Key.Key_Enter})

LOCAL_KEYS = (
    LocalBinding(
        'widgets.recent_session_row.recent-folder-enter', 'Recent folder in Load sessions', InteractionKind.KEYBOARD,
        ('Enter', 'Return', 'Space'), 'Add the folder to the list. Focus then goes to Load, or to Browse if the folder cannot load',
    ),
)


class Activation(enum.Enum):
    """What made a chip act: the window moves focus only after the keyboard."""

    KEYBOARD = "keyboard"
    OTHER = "other"


# -- the keys --------------------------------------------------------------------


def _is_held_enter(event: QEvent) -> bool:
    return (
        event.type() == QEvent.Type.KeyPress
        and isinstance(event, QKeyEvent)
        and event.isAutoRepeat()
        and event.key() in _ENTER_KEYS
    )


class HeldEnterFilter(QObject):
    """Drops repeats of Return and Enter at the controls named.

    When a chip disappears or turns off while its key is held, focus moves on and
    the repeats follow it: Browse would open the picker, Load would load. Only the
    repeats go. A new press, and the mouse, work as before.
    """

    def __init__(self, parent: QObject, receivers: Iterable[QWidget]) -> None:
        super().__init__(parent)
        for receiver in receivers:
            receiver.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API
        return _is_held_enter(event)


class RecentChip(QPushButton):
    """A chip that acts on the release of the key that was pressed on it."""

    activated = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("secondaryAction")
        self.setProperty("recentChip", True)
        # The application's button policy: Tab reaches it, a click does not take focus.
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAutoDefault(False)
        self.setAutoRepeat(False)
        self._armed_key: int | None = None
        self._space_release = False
        self.clicked.connect(self._clicked)

    @property
    def armed(self) -> bool:
        return self._armed_key is not None

    def cancel(self) -> None:
        """Forget a key held on this chip: its release will do nothing."""

        self._armed_key = None
        if self.isDown():
            self.setDown(False)

    def _clicked(self) -> None:
        self.activated.emit(Activation.KEYBOARD if self._space_release else Activation.OTHER)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt API
        if event.key() in _ENTER_KEYS:
            # Taken in every case, so no press reaches the window's default button.
            event.accept()
            plain = not (event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier)
            if plain and not event.isAutoRepeat() and not self.armed and self.isEnabled() and self.hasFocus():
                self._armed_key = event.key()
                self.setDown(True)
            return
        if event.key() == Qt.Key.Key_Escape:
            self.cancel()
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt API
        if event.key() in _ENTER_KEYS:
            event.accept()
            if event.isAutoRepeat() or event.key() != self._armed_key:
                return
            # Cleared first: the window's answer turns this chip off or moves focus.
            self.cancel()
            self.activated.emit(Activation.KEYBOARD)
            return
        self._space_release = not event.isAutoRepeat()
        try:
            super().keyReleaseEvent(event)
        finally:
            self._space_release = False

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt API
        # Hiding, turning off and window deactivation all take focus first, so
        # this one place ends every held key.
        self.cancel()
        super().focusOutEvent(event)


# -- labels ----------------------------------------------------------------------


def _name(path: str) -> str:
    return Path(path).name or path


def _ancestors(path: str) -> list[str]:
    """The folders above ``path``, nearest first."""

    return [part.rstrip("\\/") or part for part in reversed(Path(path).parent.parts)]


def distinguishing_prefix(path: str, candidates: Sequence[str]) -> str:
    """What must stand before ``path``'s name to tell it from same-named candidates.

    Keeps only the ancestors that separate it from a rival still in the running,
    and shows the folders it drops as one ``…``: ``site_1/…/``.
    """

    rivals = [_ancestors(other) for other in candidates if other != path and _name(other) == _name(path)]
    kept: list[tuple[int, str]] = []
    for depth, segment in enumerate(_ancestors(path)):
        if not rivals:
            break
        remaining = [rival for rival in rivals if depth < len(rival) and rival[depth] == segment]
        if len(remaining) < len(rivals):
            kept.append((depth, segment))
        rivals = remaining
    parts: list[str] = []
    previous: int | None = None
    for depth, segment in reversed(kept):
        if previous is not None and previous - depth > 1:
            parts.append("…")
        parts.append(segment)
        previous = depth
    if previous:
        parts.append("…")
    return "".join(part if part.endswith("/") else f"{part}/" for part in parts)


def _elide(metrics: QFontMetrics, text: str, width: float) -> str:
    return metrics.elidedText(text, _MIDDLE, max(1, int(width)))


def _shortened(prefix: str, name: str, metrics: QFontMetrics, budget: int) -> str:
    """The prefix says which folder this is, so the shared name gives way first."""

    if metrics.horizontalAdvance(prefix + name) <= budget:
        return prefix + name
    room = budget - metrics.horizontalAdvance(prefix)
    if room >= budget / 3:
        return prefix + _elide(metrics, name, room)
    room = budget - metrics.horizontalAdvance("/")
    return f"{_elide(metrics, prefix.rstrip('/'), room * 0.6)}/{_elide(metrics, name, room * 0.4)}"


def _around_the_difference(name: str, others: Sequence[str], metrics: QFontMetrics, budget: int) -> str:
    """For names that differ only in the middle, which eliding would remove."""

    first_difference = 0
    while first_difference < len(name) and all(
        first_difference < len(other) and other[first_difference] == name[first_difference] for other in others
    ):
        first_difference += 1
    start = max(0, first_difference - 6)
    end = min(len(name), first_difference + 1)

    def shown(stop: int) -> str:
        return ("…" if start else "") + name[start:stop] + ("…" if stop < len(name) else "")

    while end < len(name) and metrics.horizontalAdvance(shown(end + 1)) <= budget:
        end += 1
    return _elide(metrics, shown(end), budget)


def render_labels(paths: Sequence[str], metrics: QFontMetrics, budget: int) -> tuple[tuple[str, ...], bool]:
    """One label per path, all different, each within ``budget`` pixels.

    Runs once, from the whole candidate set: which chips show and which are
    queued have no part in it, so fitting cannot feed back into the labels. Also
    returns whether the labels had to be numbered, the last resort.
    """

    names = [_name(path) for path in paths]
    full = [distinguishing_prefix(path, paths) + name for path, name in zip(paths, names)]
    labels = [
        _shortened(distinguishing_prefix(path, paths), name, metrics, budget)
        for path, name in zip(paths, names)
    ]
    elided = tuple(labels)
    for index, label in enumerate(elided):
        rivals = [other for position, other in enumerate(names) if position != index and elided[position] == label]
        if rivals:
            labels[index] = _around_the_difference(names[index], rivals, metrics, budget)
    if len(set(labels)) == len(labels):
        return tuple(labels), False
    numbered = []
    for index, text in enumerate(full, start=1):
        suffix = f" [{index}]"
        numbered.append(_elide(metrics, text, budget - metrics.horizontalAdvance(suffix)) + suffix)
    return tuple(numbered), True


# -- the row ---------------------------------------------------------------------


@dataclass(frozen=True)
class _Candidate:
    path: str
    key: str
    full_label: str
    chip: RecentChip


class RecentSessionRow(QWidget):
    """A heading, and a chip for each recent folder that fits, newest first."""

    #: The stored path, and what made the chip act.
    path_chosen = Signal(str, object)
    #: Focus was on a chip that narrowing hid, and no chip before it can take it.
    focus_released = Signal()

    def __init__(self, paths: Iterable[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._paths = tuple(paths)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_S)
        self.heading = QLabel(RECENT_HEADING, self)
        self.heading.setObjectName("dropWellHint")
        layout.addWidget(self.heading)
        self._candidates = tuple(self._candidate(path) for path in self._paths)
        for candidate in self._candidates:
            layout.addWidget(candidate.chip)
        layout.addStretch(1)
        # Several changes can arrive together when the theme changes: fit once,
        # after they have all landed.
        self._refit = QTimer(self)
        self._refit.setSingleShot(True)
        self._refit.setInterval(0)
        self._refit.timeout.connect(self._metrics_changed)

    def _candidate(self, path: str) -> _Candidate:
        chip = RecentChip(self)
        chip.activated.connect(lambda activation, chosen=path: self.path_chosen.emit(chosen, activation))
        full_label = distinguishing_prefix(path, self._paths) + _name(path)
        candidate = _Candidate(path, path_identity_key(path), full_label, chip)
        chip.setAccessibleName(full_label)
        self._describe(candidate)
        return candidate

    @property
    def chips(self) -> tuple[RecentChip, ...]:
        return tuple(candidate.chip for candidate in self._candidates)

    @property
    def paths(self) -> tuple[str, ...]:
        return self._paths

    @staticmethod
    def _describe(candidate: _Candidate) -> None:
        state = ADD_TO_QUEUE if candidate.chip.isEnabled() else ALREADY_QUEUED
        candidate.chip.setToolTip(literal_tooltip(f"{candidate.path}\n{state}"))
        candidate.chip.setAccessibleDescription(f"{candidate.path}. {state}")

    def set_queued(self, keys: Collection[str]) -> None:
        """Make each chip available unless its folder is in the queue."""

        for candidate in self._candidates:
            available = candidate.key not in keys
            if candidate.chip.isEnabled() != available:
                candidate.chip.setEnabled(available)
                self._describe(candidate)

    # -- size and fit ----------------------------------------------------------

    def _metrics(self) -> QFontMetrics:
        return self.chips[0].fontMetrics()

    def _chip_width(self, label: str) -> int:
        """The width a chip would ask for with this label, as ``QPushButton`` works it out."""

        chip = self.chips[0]
        option = QStyleOptionButton()
        chip.initStyleOption(option)
        text = chip.fontMetrics().size(Qt.TextFlag.TextShowMnemonic, literal_mnemonic(label))
        return chip.style().sizeFromContents(QStyle.ContentsType.CT_PushButton, option, text, chip).width()

    def _polish(self) -> None:
        self.heading.ensurePolished()
        for chip in self.chips:
            chip.ensurePolished()

    def _height(self) -> int:
        return max(self.heading.sizeHint().height(), *(chip.sizeHint().height() for chip in self.chips))

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        # Every candidate at its widest, so a hidden chip can come back on widening.
        self._polish()
        labels, _numbered = render_labels(self._paths, self._metrics(), CHIP_TEXT_LIMIT)
        chips = sum(SPACE_S + self._chip_width(label) for label in labels)
        return QSize(self.heading.sizeHint().width() + chips, self._height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        # Room for one chip: the row must never hold the window wider for the others.
        self._polish()
        chip = self._chip_width(_SHORTEST_CHIP_TEXT)
        return QSize(self.heading.sizeHint().width() + SPACE_S + chip, self._height())

    def _fit(self) -> None:
        self._polish()
        metrics = self._metrics()
        available = self.width() - self.heading.sizeHint().width() - SPACE_S
        budget = max(1, min(CHIP_TEXT_LIMIT, available - self._chip_width("")))
        labels, numbered = render_labels(self._paths, metrics, budget)
        used = 0
        for index, (candidate, label) in enumerate(zip(self._candidates, labels)):
            # Counts hidden chips too, so a short third never skips a wide second.
            used += self._chip_width(label) + (SPACE_S if index else 0)
            text = literal_mnemonic(label)
            if candidate.chip.text() != text:
                candidate.chip.setText(text)
            candidate.chip.setAccessibleName(f"{candidate.full_label} [{index + 1}]" if numbered else candidate.full_label)
            visible = index == 0 or used <= available
            if not visible and candidate.chip.hasFocus() and candidate.chip.isVisibleTo(self):
                self._hand_on_focus(index)
            candidate.chip.setVisible(visible)

    def _hand_on_focus(self, index: int) -> None:
        """Before a focused chip hides: else Qt moves on, perhaps to Cancel."""

        self.chips[index].cancel()
        earlier = [chip for chip in self.chips[:index] if chip.isVisibleTo(self) and chip.isEnabled()]
        if earlier:
            earlier[-1].setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self.focus_released.emit()

    def _metrics_changed(self) -> None:
        self.updateGeometry()
        self._fit()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._fit()

    def event(self, event) -> bool:  # noqa: D102 - Qt override
        if event.type() in METRIC_CHANGE_EVENTS:
            self._refit.start()
        return super().event(event)
