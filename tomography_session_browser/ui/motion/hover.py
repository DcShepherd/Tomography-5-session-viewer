"""Hover components for the motion."""
from __future__ import annotations
import weakref
from PySide6.QtCore import QEvent, QModelIndex, QObject, QRect
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QAbstractItemView
from tomography_session_browser.ui.motion.spring import Spring
from tomography_session_browser.ui.motion.ticker import TICKER
from tomography_session_browser.ui.motion.tokens import HOVER_EVENTS, MOTION_MICRO, SpringSpec


def row_key(index: QModelIndex) -> tuple[int, ...]:
    """A hashable path for a row (column-independent)."""

    parts: list[int] = []
    current = index.sibling(index.row(), 0) if index.isValid() else QModelIndex()
    while current.isValid():
        parts.append(current.row())
        current = current.parent()
    return tuple(reversed(parts))


class ItemHoverAnimator(QObject):
    """Animated per-row hover progress (0..1) for a delegate-painted view.

    The delegate asks :meth:`progress` for a row and blends its hover fill by
    it, instead of switching on ``State_MouseOver``. Moving between rows fades
    the old row out while the new one fades in, and a reversal mid-fade
    continues smoothly. With motion off, progress is simply 0 or 1.
    """

    def __init__(self, view: QAbstractItemView, *, spec: SpringSpec = MOTION_MICRO) -> None:
        super().__init__(view)
        self._view = view
        self._spec = spec
        self._springs: dict[tuple[int, ...], Spring] = {}
        self._hovered: tuple[int, ...] | None = None
        # The view holds the Python side alive: Qt owns the C++ object as the
        # view's child, but without a Python reference the wrapper could be
        # collected while the viewport still routes events to this filter.
        setattr(view, "_tomo_hover_animator", self)
        view.setMouseTracking(True)
        self._viewport = view.viewport()
        self._viewport.installEventFilter(self)
        # Weak callbacks: signals from the model or scroll bar must never
        # reach a torn-down animator.
        ref = weakref.ref(self)

        def call(method: str, *_args) -> None:
            target = ref()
            if target is not None:
                getattr(target, method)()

        view.verticalScrollBar().valueChanged.connect(lambda *_a: call("_sync_to_cursor"))
        model = view.model()
        if model is not None:
            for signal in (model.modelReset, model.rowsInserted, model.rowsRemoved, model.layoutChanged):
                signal.connect(lambda *_a: call("reset"))

    def progress(self, index: QModelIndex) -> float:
        spring = self._springs.get(row_key(index))
        return spring.value if spring is not None else 0.0

    def hovered_key(self) -> tuple[int, ...] | None:
        return self._hovered

    def reset(self, *_args) -> None:
        """Forget all rows (their indexes are no longer meaningful)."""

        for spring in self._springs.values():
            TICKER.remove(spring)
        self._springs.clear()
        self._hovered = None

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        # Check the event type and the cached viewport before touching the
        # view at all: while a view is being destroyed its viewport still
        # receives Hide/Leave, after the scroll-area part of the view is gone,
        # and calling into the view then crashed the process on exit.
        kind = event.type()
        if kind not in HOVER_EVENTS or watched is not getattr(self, "_viewport", None):
            return False
        if kind == QEvent.Type.MouseMove:
            self._hover_at(event.position().toPoint())
        elif kind == QEvent.Type.Leave:
            self._set_hovered(None)
        else:  # Hide: nothing is visible to fade; drop state without repainting
            self.reset()
        return False

    def _sync_to_cursor(self, *_args) -> None:
        viewport = self._view.viewport()
        position = viewport.mapFromGlobal(QCursor.pos())
        if viewport.rect().contains(position):
            self._hover_at(position)

    def _hover_at(self, position) -> None:
        index = self._view.indexAt(position)
        self._set_hovered(row_key(index) if index.isValid() else None)

    def _set_hovered(self, key: tuple[int, ...] | None) -> None:
        if key == self._hovered:
            return
        previous, self._hovered = self._hovered, key
        if previous is not None:
            self._spring_for(previous).set_target(0.0)
        if key is not None:
            self._spring_for(key).set_target(1.0)

    def _spring_for(self, key: tuple[int, ...]) -> Spring:
        spring = self._springs.get(key)
        if spring is None:

            def repaint(_value: float, key: tuple[int, ...] = key) -> None:
                self._repaint_row(key)

            def forget(key: tuple[int, ...] = key) -> None:
                settled = self._springs.get(key)
                if settled is not None and settled.value == 0.0 and key != self._hovered:
                    del self._springs[key]

            spring = Spring(0.0, spec=self._spec, on_update=repaint, on_settled=forget, widget=self._view)
            self._springs[key] = spring
        return spring

    def _repaint_row(self, key: tuple[int, ...]) -> None:
        model = self._view.model()
        if model is None:
            return
        index = QModelIndex()
        for row in key:
            index = model.index(row, 0, index)
            if not index.isValid():
                return
        rect = self._view.visualRect(index)
        if rect.isValid():
            viewport = self._view.viewport()
            viewport.update(QRect(0, rect.top(), viewport.width(), rect.height()))

