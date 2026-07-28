from __future__ import annotations

from collections.abc import Iterable
import os

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QTimer
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect, QLayout, QWidget


def animations_enabled(widget: QWidget | None = None) -> bool:
    """Return whether optional interface motion is enabled.

    Hosts can provide a reduced-motion preference through the application
    property ``tomo_reduce_motion`` or the ``TOMOAPP_REDUCE_MOTION``
    environment variable. Essential state changes still happen immediately.
    """

    app = QApplication.instance()
    if app is None:
        return False
    reduced = app.property("tomo_reduce_motion")
    if reduced is not None and bool(reduced):
        return False
    env_value = os.environ.get("TOMOAPP_REDUCE_MOTION", "").strip().lower()
    if env_value in {"1", "true", "yes", "on"}:
        return False
    if widget is not None and widget.property("_tomo_disable_animations"):
        return False
    return True


def fade_in(
    widget: QWidget | None,
    *,
    duration_ms: int = 180,
    delay_ms: int = 0,
    start_opacity: float = 0.0,
    allow_hidden: bool = False,
) -> None:
    """Fade ``widget`` in if it is currently safe and useful to animate."""

    if widget is None:
        return
    if delay_ms > 0:
        QTimer.singleShot(delay_ms, lambda widget=widget: fade_in(
            widget,
            duration_ms=duration_ms,
            start_opacity=start_opacity,
            allow_hidden=allow_hidden,
        ))
        return
    if not _can_animate(widget, allow_hidden=allow_hidden):
        return

    existing = widget.graphicsEffect()
    if existing is not None:
        return

    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(start_opacity)
    widget.setGraphicsEffect(effect)

    animation = QPropertyAnimation(effect, b"opacity", widget)
    animation.setDuration(max(50, duration_ms))
    animation.setStartValue(start_opacity)
    animation.setEndValue(1.0)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)
    widget.setProperty("_tomo_fade_animation", animation)

    def finish() -> None:
        try:
            if widget.graphicsEffect() is effect:
                widget.setGraphicsEffect(None)
            widget.setProperty("_tomo_fade_animation", None)
            effect.deleteLater()
            animation.deleteLater()
        except RuntimeError:
            # Widget was destroyed while the short animation was running.
            pass

    animation.finished.connect(finish)
    animation.start()


def staggered_fade_in(
    widgets: Iterable[QWidget],
    *,
    duration_ms: int = 180,
    stagger_ms: int = 35,
    max_widgets: int = 8,
    start_opacity: float = 0.0,
    allow_hidden: bool = False,
) -> None:
    for index, widget in enumerate(list(widgets)[:max_widgets]):
        fade_in(
            widget,
            duration_ms=duration_ms,
            delay_ms=index * stagger_ms,
            start_opacity=start_opacity,
            allow_hidden=allow_hidden,
        )


def fade_in_layout_children(
    layout: QLayout | None,
    *,
    duration_ms: int = 180,
    stagger_ms: int = 35,
    max_widgets: int = 8,
    start_opacity: float = 0.0,
    allow_hidden: bool = False,
) -> None:
    if layout is None:
        return
    widgets: list[QWidget] = []
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item is None:
            continue
        widget = item.widget()
        if widget is not None:
            widgets.append(widget)
    staggered_fade_in(
        widgets,
        duration_ms=duration_ms,
        stagger_ms=stagger_ms,
        max_widgets=max_widgets,
        start_opacity=start_opacity,
        allow_hidden=allow_hidden,
    )


def _can_animate(widget: QWidget, *, allow_hidden: bool = False) -> bool:
    if not animations_enabled(widget):
        return False
    if not allow_hidden and not widget.isVisible():
        return False
    window = widget.window()
    if not allow_hidden and window is not None and not window.isVisible():
        return False
    return True
