"""Whole wheel notches from any wheel or touchpad."""
from __future__ import annotations

import math

from PySide6.QtCore import Qt
from PySide6.QtGui import QWheelEvent

#: Angle units in one notch of a standard wheel.
NOTCH = 120


class WheelNotches:
    """Gathers wheel angle units into whole notches, so a touchpad or a fine wheel never lands between steps.

    A new device, a change between vertical and horizontal, a change of
    inversion or a new gesture starts again. Angle units only: pixels have no
    portable conversion to a step.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.remainder = 0
        self.source: tuple[object, bool, bool] | None = None

    def steps(self, event: QWheelEvent) -> int:
        delta = event.angleDelta()
        source = (event.pointingDevice(), bool(delta.y() == 0 and delta.x()), event.inverted())
        if source != self.source or event.phase() == Qt.ScrollPhase.ScrollBegin:
            self.reset()
        self.source = source
        self.remainder += delta.y() or delta.x()
        steps = math.trunc(self.remainder / NOTCH)
        self.remainder -= steps * NOTCH
        if event.phase() == Qt.ScrollPhase.ScrollEnd:
            self.reset()
        return steps
