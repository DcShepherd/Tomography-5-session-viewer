"""Tokens components for the motion."""
from __future__ import annotations
from dataclasses import dataclass
from PySide6.QtCore import QEvent, Qt


@dataclass(frozen=True, slots=True)
class SpringSpec:
    """Spring settings: stiffness (per unit mass) and damping ratio."""

    stiffness: float
    damping_ratio: float


MOTION_MICRO = SpringSpec(stiffness=500.0, damping_ratio=1.0)


MOTION_SMALL = SpringSpec(stiffness=380.0, damping_ratio=0.9)


MOTION_PANEL = SpringSpec(stiffness=700.0, damping_ratio=1.0)


MOTION_ZOOM = SpringSpec(stiffness=900.0, damping_ratio=1.0)


MOTION_SCROLL = SpringSpec(stiffness=700.0, damping_ratio=1.0)


DURATION_MICRO_MS = 110


DURATION_SMALL_MS = 180


DURATION_PANEL_MS = 240


DURATION_CAMERA_MS = 320


DURATION_CAMERA_MAX_MS = 640


DURATION_PULSE_MS = 650


STAGGER_MS = 25


STAGGER_MAX_ITEMS = 8


FRAME_INTERVAL_MS = 16


MAX_STEP_S = 0.05


INTERRUPTING_INPUT = frozenset(
    {
        QEvent.Type.MouseButtonPress,
        QEvent.Type.MouseButtonDblClick,
        QEvent.Type.KeyPress,
        QEvent.Type.Wheel,
        QEvent.Type.TouchBegin,
        QEvent.Type.NativeGesture,
    }
)


HOVER_EVENTS = frozenset({QEvent.Type.MouseMove, QEvent.Type.Leave, QEvent.Type.Hide})


WHEEL_MODIFIERS = (
    Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.AltModifier
)


PAGE_FADE_START_OPACITY = 0.6


THEME_REVEAL_MS = 560


THEME_REVEAL_FEATHER_PX = 36.0


THEME_REVEAL_FEATHER_RINGS = 4


THEME_REVEAL_GLOW_WIDTH_PX = 6.0


THEME_REVEAL_GLOW_ALPHA = 34


THEME_REVEAL_SETTLE_TURNS = 3


ANNULUS_BAND_PX = 24


CAMERA_PATH_RHO = 1.4

