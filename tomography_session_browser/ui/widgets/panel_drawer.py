"""Shared slide-over drawer for Project and Context on narrow windows.

Project slides from the left and Context from the right, over the page.
Both use the same snapshot animation, input handling and focus return.

The drawer holds the very same panel widget, moved out of its dock, so
everything that fills the panel works unchanged. Which mode applies is a
transient layout state, worked out from the window's width; nothing about it
is saved, and the reviewer's docked preference is kept for when the window
widens again (plan R1).
"""

from __future__ import annotations

from collections.abc import Callable
from weakref import ref

from PySide6.QtCore import QEvent, QObject, QRect, Qt, Signal
from PySide6.QtGui import QColor, QHideEvent, QKeyEvent, QMouseEvent, QShowEvent
from PySide6.QtWidgets import QApplication, QFrame, QVBoxLayout, QWidget
import shiboken6

from tomography_session_browser.ui.motion import INTERRUPTING_INPUT, PanelSlide
from tomography_session_browser.ui.theme import current_palette

#: The drawer takes over when docking the panel would leave the page less
#: than this (the viewer's list plus an image worth looking at).
CONTEXT_DRAWER_WORKSPACE_PX = 720
#: A window must be this much roomier again before the panel docks, so a
#: width near the threshold does not flip the layout back and forth.
CONTEXT_DRAWER_HYSTERESIS_PX = 32
#: However narrow the window, the open drawer leaves this much page showing.
CONTEXT_DRAWER_MIN_PAGE_PX = 48
#: Over a list column narrower than this, the drawer is still this wide: the
#: panel's metadata rows wrap into slivers below it.
CONTEXT_DRAWER_MIN_WIDTH_PX = 280
#: Over a list column shorter than this, the drawer takes the page's full height instead.
CONTEXT_DRAWER_MIN_HEIGHT_PX = 240

#: Pressed alone they do nothing; each begins a shortcut such as Ctrl+2.
_MODIFIER_KEYS = frozenset(
    {Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_AltGr, Qt.Key.Key_Meta}
)


def context_panel_as_drawer(
    window_width: int,
    project_width: int,
    context_width: int,
    *,
    currently_drawer: bool,
) -> bool:
    """Whether the context panel should be a drawer rather than docked.

    Judged by the page's width *as if the panel were docked*, which does not
    depend on the mode itself, so a switch cannot feed back into the next
    decision. Pure, for the tests.
    """

    page = window_width - project_width - context_width
    limit = CONTEXT_DRAWER_WORKSPACE_PX + (CONTEXT_DRAWER_HYSTERESIS_PX if currently_drawer else 0)
    return page < limit


class WatchedGeometry(QObject):
    """Calls ``changed`` whenever a widget it watches moves, resizes, is shown or hidden."""

    def __init__(self, parent: QObject, changed: Callable[[], None]) -> None:
        super().__init__(parent)
        self._changed = changed

    def watch(self, widget: QWidget) -> None:
        widget.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API
        if event.type() in (QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.Hide):
            self._changed()
        return False


class PanelDrawer(QFrame):
    """A frame at either edge that slides over the page.

    ``hold`` puts the panel in it and ``release`` gives the panel back.
    ``open_drawer`` and ``close_drawer`` slide a picture of it (plan F5.2's
    ``PanelSlide``). The live drawer is in place throughout opening, so input
    reaches its controls immediately and dismisses the picture; input on the
    Context button (``toggle_control``) turns the picture round instead.
    Nothing re-lays out per frame. Escape in its window emits ``escape_pressed``
    while open, including when focus remained on the toggle or a list filter.
    """

    escape_pressed = Signal()

    def __init__(self, parent: QWidget, *, name: str = "Context", from_left: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName(f"{name.lower()}Drawer")
        self.setAccessibleName(f"{name} panel")
        self.setAccessibleDescription(f"{name} panel over the page")
        self._from_left = from_left
        self._previous_focus: ref[QWidget] | None = None
        self.setFrameShape(QFrame.Shape.NoFrame)
        layout = QVBoxLayout(self)
        # The left border (the style sheet's) separates it from the page.
        layout.setContentsMargins(0 if from_left else 1, 0, 1 if from_left else 0, 0)
        layout.setSpacing(0)
        self._panel: QWidget | None = None
        self._rect = QRect()
        self._open = False
        self._slide: PanelSlide | None = None
        #: What the drawer must stay beneath when it rises (the loader).
        self.stay_under: Callable[[], QWidget | None] | None = None
        #: The button that opens and closes the drawer (the Context button).
        #: Input on it turns a slide round rather than finishing it.
        self.toggle_control: Callable[[], QWidget | None] | None = None
        self.hide()

    # -- the panel -------------------------------------------------------------

    def hold(self, panel: QWidget) -> None:
        self._panel = panel
        self.layout().addWidget(panel)
        panel.show()

    def release(self) -> QWidget | None:
        panel, self._panel = self._panel, None
        if panel is not None:
            self.layout().removeWidget(panel)
        return panel

    # -- placing and sliding ---------------------------------------------------

    @property
    def is_open(self) -> bool:
        """Open or opening: what the Context button shows."""

        return self._open

    @property
    def sliding(self) -> bool:
        return self.slide is not None

    def place(self, rect: QRect) -> None:
        self._rect = QRect(rect)
        self.setGeometry(self._rect)

    def open_drawer(self, *, animate: bool) -> None:
        if not self._open:
            focus = QApplication.focusWidget()
            self._previous_focus = ref(focus) if focus is not None and focus.window() is self.window() else None
        self._open = True
        if animate and self._moving_slide(entering=False):
            self.show()
            self._rise()
            self._slide.reverse(backdrop=QColor(current_palette().background), on_finished=self._arrived)
            return
        self._dismiss_slide()
        self.setGeometry(self._rect)
        self.show()
        self._rise()
        if not animate:
            return
        # The picture and backdrop are mouse-transparent. Keep the live
        # controls beneath them: hiding the drawer here sent a first click
        # through to an unrelated control on the page.
        self.layout().activate()
        picture = self.grab()
        self._slide = self._watch(
            PanelSlide(
                self.parentWidget(),
                self._rect,
                picture,
                from_left=self._from_left,
                entering=True,
                backdrop=QColor(current_palette().background),
                on_finished=self._arrived,
            )
        )

    def close_drawer(self, *, animate: bool) -> None:
        was_open = self._open
        self._open = False
        if was_open:
            self.restore_focus()
        if animate and self._moving_slide(entering=True):
            self.hide()
            self._slide.reverse()  # opening: turn round
            return
        was_shown = self.isVisible()
        self._dismiss_slide()
        if not was_shown or not animate:
            self.hide()
            return
        geometry, picture = self.geometry(), self.grab()
        self.hide()
        self._slide = self._watch(
            PanelSlide(self.parentWidget(), geometry, picture, from_left=self._from_left, entering=False)
        )

    def restore_focus(self) -> None:
        focus = self._previous_focus() if self._previous_focus is not None else None
        self._previous_focus = None
        if focus is None or not shiboken6.isValid(focus) or not focus.isVisible() or not focus.isEnabled():
            focus = self.toggle_control() if self.toggle_control is not None else None
        if focus is not None and shiboken6.isValid(focus) and focus.isVisible() and focus.isEnabled():
            focus.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def _moving_slide(self, *, entering: bool) -> bool:
        """A slide still under way in that direction."""

        slide = self._slide
        return (
            slide is not None
            and shiboken6.isValid(slide)
            and not slide.isHidden()
            and slide.entering == entering
        )

    def _arrived(self) -> None:
        if self._open:
            self.show()
            self._rise()

    def _rise(self) -> None:
        """Above the page, but never above ``stay_under`` (it is not raised
        again: the loader's own Qt Quick windows must stay above it)."""

        self.raise_()
        above = self.stay_under() if self.stay_under is not None else None
        if above is not None and above.isVisible():
            self.stackUnder(above)

    def _watch(self, slide: PanelSlide) -> PanelSlide:
        QApplication.instance().installEventFilter(self)
        # Forgotten when it goes, unless a newer slide has replaced it.
        slide.destroyed.connect(lambda *_args, gone=slide: self._forget(gone))
        return slide

    def _forget(self, slide: PanelSlide) -> None:
        if self._slide is not slide:
            return
        self._slide = None
        # A window closing mid-slide deletes the drawer before its slide. Qt
        # has already dropped a deleted object's event filter then.
        if shiboken6.isValid(self):
            self._sync_input_filter()

    def _sync_input_filter(self) -> None:
        app = QApplication.instance()
        if (self._open and self.isVisible()) or self.slide is not None:
            app.installEventFilter(self)
        else:
            app.removeEventFilter(self)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        self._sync_input_filter()

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802 - Qt API
        super().hideEvent(event)
        self._sync_input_filter()

    def eventFilter(self, watched, event: QEvent) -> bool:  # noqa: N802 - Qt API
        if (self._open and isinstance(event, QKeyEvent) and event.key() == Qt.Key.Key_Escape
                and isinstance(watched, QWidget) and watched.window() is self.window()):
            if event.type() == QEvent.Type.ShortcutOverride:
                event.accept()  # the open drawer precedes a filter's own Escape
                return True
            if event.type() == QEvent.Type.KeyPress:
                self.escape_pressed.emit()
                return True
        # The receiver already belongs to the target layout. Reveal its
        # response immediately; do not consume or replay the input.
        if self.slide is not None and event.type() in INTERRUPTING_INPUT and not self._turns_the_slide(event):
            self._dismiss_slide()
        return False

    def _turns_the_slide(self, event: QEvent) -> bool:
        """Input whose response is the slide itself, so it must not end it.

        The Context button and its shortcut toggle the drawer, which turns
        the picture round from where it is (``open_drawer``,
        ``close_drawer``). Finishing the slide on their press made the
        picture jump to its end and then start over.
        """

        if isinstance(event, QKeyEvent) and event.key() in _MODIFIER_KEYS:
            return True
        toggle = self.toggle_control() if self.toggle_control is not None else None
        if toggle is None or not shiboken6.isValid(toggle) or not toggle.isVisible():
            return False
        if isinstance(event, QMouseEvent):
            # Positions rather than the receiver: the window sees the press
            # before the button does.
            return toggle.rect().contains(toggle.mapFromGlobal(event.globalPosition().toPoint()))
        if isinstance(event, QKeyEvent):
            return QApplication.focusWidget() is toggle
        return False

    @property
    def slide(self) -> PanelSlide | None:
        """The slide under way, if any (a finished one goes a turn later)."""

        slide = self._slide
        return slide if slide is not None and shiboken6.isValid(slide) and not slide.isHidden() else None

    def _dismiss_slide(self) -> None:
        slide, self._slide = self._slide, None
        if slide is not None:
            try:
                slide.dismiss()
            except RuntimeError:
                pass  # it had already settled and deleted itself
        self._sync_input_filter()
