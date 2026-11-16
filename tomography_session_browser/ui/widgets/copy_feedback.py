"""One "Copied" for every copy control (W4b).

The control's owner copies, then calls :meth:`CopyFeedback.confirm`. A text
control reads "Copied" for 1,200 ms; an icon control shows the tooltip
"Copied" for the same time, as the metadata panel's path button always did.
While "Copied" shows, a text control keeps the width it had, so nothing
beside it moves; it keeps its focus and its accessible name. A second copy
restarts the one timer. The owner calls :meth:`CopyFeedback.reset` when the
content to copy changes.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QAbstractButton, QToolTip

from tomography_session_browser.ui.widgets.session_drop import announce_politely

COPIED_TEXT = "Copied"
COPIED_MS = 1200


class CopyFeedback(QObject):
    def __init__(self, control: QAbstractButton) -> None:
        super().__init__(control)
        self._control = control
        self._label = control.text()
        self._icon_only = not self._label
        self._tooltip = control.toolTip()
        self._minimum_width = control.minimumWidth()
        self._shown = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(COPIED_MS)
        self._timer.timeout.connect(self.reset)
        if not self._icon_only:
            # The name stays "Copy details" while the text reads "Copied".
            control.setAccessibleName(control.accessibleName() or self._label)

    @property
    def showing(self) -> bool:
        return self._shown

    def confirm(self) -> None:
        """The copy happened: say so, and (re)start the one timer."""

        control = self._control
        if self._icon_only:
            if not self._shown:
                self._tooltip = control.toolTip()
            control.setToolTip(COPIED_TEXT)
            QToolTip.showText(control.mapToGlobal(control.rect().bottomLeft()), COPIED_TEXT, control)
        else:
            if not self._shown:
                # Hold today's width: a shorter "Copied" must not move what is beside it.
                self._minimum_width = control.minimumWidth()
                control.setMinimumWidth(max(control.width(), self._minimum_width))
            control.setText(COPIED_TEXT)
        self._shown = True
        self._timer.start()
        announce_politely(control, COPIED_TEXT)

    def reset(self) -> None:
        """Back to the plain control at once (the timer ran out, or the content changed)."""

        self._timer.stop()
        if not self._shown:
            return
        self._shown = False
        if self._icon_only:
            self._control.setToolTip(self._tooltip)
        else:
            self._control.setText(self._label)
            self._control.setMinimumWidth(self._minimum_width)
