"""UI steps run one per event-loop turn, so the loading overlay keeps moving."""
from __future__ import annotations

import time
from collections.abc import Callable, Sequence

from PySide6.QtCore import QTimer

from tomography_session_browser.diagnostics.profiling import NULL_PROFILE, LoadingProfiler

Step = tuple[str, Callable[[], None]]


class StagedSteps:
    """Run ``steps`` in order, a ``QTimer.singleShot(0, ...)`` apart.

    Before each step ``on_step(index, message)`` runs (the window shows the
    message and the stage); each step's time lands in ``profile`` under its
    message. The first step to raise stops the run and goes to ``on_error``;
    otherwise ``on_done`` runs after the last.
    """

    def __init__(
        self,
        steps: Sequence[Step],
        *,
        on_step: Callable[[int, str], None],
        on_error: Callable[[Exception], None],
        on_done: Callable[[], None],
        profile: LoadingProfiler = NULL_PROFILE,
    ) -> None:
        self._steps = list(steps)
        self._on_step = on_step
        self._on_error = on_error
        self._on_done = on_done
        self._profile = profile

    def __len__(self) -> int:
        return len(self._steps)

    def start(self) -> None:
        QTimer.singleShot(0, lambda: self._run(0))

    def _run(self, index: int) -> None:
        if index >= len(self._steps):
            self._on_done()
            return
        message, step = self._steps[index]
        self._on_step(index, message)
        try:
            started = time.perf_counter()
            step()
            self._profile.add_phase(
                message.rstrip(".").lower().replace(" ", "_"),
                time.perf_counter() - started,
                ui_updates=1,
            )
            self._profile.record_ui_update()
        except Exception as exc:  # noqa: BLE001 - the window shows a clear error and keeps diagnostics
            self._on_error(exc)
            return
        QTimer.singleShot(0, lambda: self._run(index + 1))
