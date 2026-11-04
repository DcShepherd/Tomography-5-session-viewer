"""Python's cyclic garbage collector held off for a short animation."""
from __future__ import annotations
import gc


class GcPause:
    """Hold off the cyclic collector for one bounded animation.

    A full collection over a loaded project takes about 30 ms on the UI
    thread, enough to drop two frames of a 240 ms move. Only if it was on, and
    always given back by the owner's every exit. Objects freed by reference
    counting still go at once.
    """

    def __init__(self) -> None:
        self._paused = False

    def pause(self) -> None:
        if not self._paused and gc.isenabled():
            gc.disable()
            self._paused = True

    def resume(self) -> None:
        if self._paused:
            self._paused = False
            gc.enable()
