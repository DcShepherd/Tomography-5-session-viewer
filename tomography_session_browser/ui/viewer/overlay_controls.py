"""One row of the Overlays panel, and the rule for what its box shows."""
from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QCheckBox, QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget

from tomography_session_browser.ui.viewer.constants import OVERLAY_HINT_INDENT_PX, OVERLAY_SUB_INDENT_PX
from tomography_session_browser.ui.viewer.models import OverlayControl
from tomography_session_browser.ui.viewer.overlay_swatches import with_swatch


def control_shown(preferences: Sequence[bool], available: Sequence[bool]) -> tuple[Qt.CheckState, bool]:
    """What a row's box shows, and whether it can be pressed.

    ``preferences`` and ``available`` have one entry per key the row sets.
    A row with nothing available is off and disabled. Otherwise the box
    follows the preferences, all of them: keys that differ (set apart, or one of
    them unavailable and never switched) show as partly checked, because the box
    would be claiming something about a layer the reader cannot see.
    """

    if not any(available):
        return Qt.CheckState.Unchecked, False
    if len(set(preferences)) > 1:
        return Qt.CheckState.PartiallyChecked, True
    return (Qt.CheckState.Checked if preferences[0] else Qt.CheckState.Unchecked), True


class OverlayBodyScroll(QScrollArea):
    """The panel's rows, as tall as they are until the canvas cannot hold them.

    A plain scroll area stops asking for height at about 24 lines of text, so a
    panel taller than that scrolled with room to spare. This one asks for the
    whole body, and gives way to whatever height the panel is capped at.
    """

    #: The shortest it shrinks to when there is more than that to show: a few rows and a scroll bar.
    MIN_HEIGHT = 72

    def __init__(self, body: QWidget, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("viewerOverlayScroll")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWidget(body)
        self._gutter = False

    def reserve_scroll_bar(self, needed: bool) -> None:
        """Keep room for the scroll bar while it is showing, so no row loses its end under it."""

        if needed != self._gutter:
            self._gutter = needed
            self.updateGeometry()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt signature
        hint = self.widget().sizeHint()
        return QSize(hint.width() + (self.verticalScrollBar().sizeHint().width() if self._gutter else 0), hint.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt signature
        hint = self.sizeHint()
        return QSize(hint.width(), min(self.MIN_HEIGHT, hint.height()))


class OverlayCheckBox(QCheckBox):
    """A box whose press turns everything it sets on, or off if all of it is on.

    A partly checked box would otherwise cycle through the partial state
    again; a press has to end with every layer it names showing the same thing.
    """

    def nextCheckState(self) -> None:  # noqa: N802 - Qt signature
        self.setCheckState(
            Qt.CheckState.Unchecked if self.checkState() == Qt.CheckState.Checked else Qt.CheckState.Checked
        )


class OverlayControlRow(QWidget):
    """A box with its one line of note, indented under the row it belongs to."""

    def __init__(self, control: OverlayControl, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("viewerOverlayRow")
        self.control = control
        layout = QVBoxLayout(self)
        layout.setContentsMargins(OVERLAY_SUB_INDENT_PX if control.parent else 0, 0, 0, 0)
        layout.setSpacing(0)
        self.box = OverlayCheckBox(control.label, self)
        if control.swatch:
            with_swatch(self.box, control.swatch)
        self.note = QLabel(self)
        self.note.setObjectName("viewerOverlayHint")
        self.note.setContentsMargins(OVERLAY_HINT_INDENT_PX, 0, 0, 0)
        layout.addWidget(self.box)
        layout.addWidget(self.note)
        self.set_note(control.hint)

    def show_state(self, state: Qt.CheckState, enabled: bool, note: str, tooltip: str) -> None:
        """Show ``state`` without the box reporting a press."""

        blocked = self.box.blockSignals(True)
        self.box.setEnabled(enabled)
        self.box.setCheckState(state)
        self.box.setToolTip(tooltip)
        self.box.blockSignals(blocked)
        self.set_note(note)

    def set_note(self, note: str) -> None:
        self.note.setText(note)
        self.note.setVisible(bool(note))
