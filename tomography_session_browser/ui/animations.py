from __future__ import annotations

from PySide6.QtWidgets import QWidget

from tomography_session_browser.ui.motion import motion_enabled

# The opacity-effect fades that lived here (``fade_in`` and its staggered
# forms) are gone: an effect over a whole widget renders its subtree off
# screen every frame (plan principle 8). A widget whose content was replaced
# says so with ``motion.veil`` instead.


def animations_enabled(widget: QWidget | None = None) -> bool:
    """Return whether optional interface motion is enabled.

    Delegates to :func:`tomography_session_browser.ui.motion.motion_enabled`,
    which honours a widget opt-out, the ``tomo_reduce_motion`` application
    property, the ``TOMOAPP_REDUCE_MOTION`` environment variable and the
    operating system's reduced-motion setting. Essential state changes still
    happen immediately.
    """

    return motion_enabled(widget)
