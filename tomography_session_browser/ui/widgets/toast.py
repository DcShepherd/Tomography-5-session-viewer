"""A short confirmation that fades in over the workspace and away again.

Plan F5.9. Confirmations ("Loaded …", "Details copied …", "Saved report …")
used to go to the status bar, where they were easy to miss and some stayed
indefinitely. A toast shows at the bottom of the workspace for a few seconds
and fades. It never takes a click, so nothing under it is blocked.

Only news belongs here. Where a jump went, why it could not go, and work in
progress stay in the status bar and the context header (the navigation
contract: an explanation needs a place that does not vanish).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent, QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QWidget

from tomography_session_browser.ui.motion import DURATION_PANEL_MS, DURATION_SMALL_MS, Tween

# Module-level, so a test can see what a toast tells screen readers.
_update_accessibility = QAccessible.updateAccessibility

#: How long a toast stays, and an error (worth a longer read).
TOAST_MS = 4000
TOAST_ERROR_MS = 6000
#: Tones: a dot in the matching status colour before the text.
TONE_INFO, TONE_SUCCESS, TONE_ERROR = "info", "success", "error"
TOAST_HEIGHT_PX = 36
TOAST_MARGIN_PX = 16  # above the workspace's bottom edge
TOAST_MAX_WIDTH_PX = 560
TOAST_RISE_PX = 8  # it rises this far as it fades in
_PADDING_PX = 16
_DOT_PX = 8
_TEXT_SLACK_PX = 4


class Toast(QWidget):
    """One toast for ``host`` (the main window); a new message replaces it."""

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.setObjectName("toast")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._host = host
        self.text = ""
        self.tone = TONE_INFO
        self._shown_text = ""
        self.opacity = 0.0
        self._in_from = 0.0
        self._rise = 0.0
        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.timeout.connect(self._fade_out)
        self._in = Tween(DURATION_SMALL_MS, on_update=self._faded_in, widget=self)
        self._out = Tween(DURATION_PANEL_MS, on_update=self._faded_out, on_finished=self._gone, widget=self)
        self.destroyed.connect(
            lambda *_args, tweens=(self._in, self._out): [tween.stop() for tween in tweens if tween.is_animating]
        )
        host.installEventFilter(self)
        self.hide()

    @property
    def showing(self) -> bool:
        return self.isVisible() and not self._out.is_animating

    def show_message(self, text: str, *, tone: str = TONE_INFO, timeout_ms: int | None = None) -> None:
        """Show ``text`` for a few seconds, replacing any toast on screen."""

        self.text = text
        self.tone = tone
        self.setAccessibleName(text)
        self._out.stop()
        self._hold.stop()
        if not self._host.isVisible():
            # Nobody to show it to: no timers or motion left running.
            self.dismiss()
            return
        fresh = not self.isVisible()
        if fresh:
            self.opacity, self._rise = 0.0, float(TOAST_RISE_PX)
        self.show()
        self.raise_()
        self._place()
        if self.opacity < 1.0:
            # Fades in from wherever it is (a toast fading out comes back).
            self._in_from = self.opacity
            self._in.start()
        else:
            self.update()  # already up: the new words, at once
        hold = timeout_ms if timeout_ms is not None else (TOAST_ERROR_MS if tone == TONE_ERROR else TOAST_MS)
        self._hold.start(hold)
        # Screen readers: the status bar was read out; a toast must be too. An
        # announcement, never an Alert: on Windows Qt plays the system
        # "Asterisk" sound for each Alert, so repeated toasts chimed.
        announcement = QAccessibleAnnouncementEvent(self, text)
        politeness = QAccessible.AnnouncementPoliteness
        announcement.setPoliteness(politeness.Assertive if tone == TONE_ERROR else politeness.Polite)
        _update_accessibility(announcement)

    def dismiss(self) -> None:
        """Remove it now (tests, or a window closing)."""

        self._hold.stop()
        self._in.stop()
        self._out.stop()
        self._gone()

    # -- placement ----------------------------------------------------------

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        if watched is self._host:
            kind = event.type()
            if kind in (QEvent.Type.Resize, QEvent.Type.LayoutRequest) and self.isVisible():
                self._place()
            elif kind == QEvent.Type.Hide and not self._host.isVisible():
                self.dismiss()  # the window closed: nothing left to fade
        return False

    def _workspace(self) -> QRect:
        central = getattr(self._host, "centralWidget", lambda: None)()
        if central is not None and central.isVisible():
            return QRect(central.mapTo(self._host, QPoint(0, 0)), central.size())
        return self._host.rect()

    def _font(self) -> QFont:
        font = QFont(self.font())
        font.setWeight(QFont.Weight.DemiBold)
        return font

    def _place(self) -> None:
        self.ensurePolished()  # the app font arrives with the style sheet
        workspace = self._workspace()
        metrics = QFontMetrics(self._font())
        chrome = _PADDING_PX * 2 + _DOT_PX + 10
        # A few pixels of slack: eliding to exactly the measured advance still
        # cut every message short on screen (found natively).
        needed = metrics.horizontalAdvance(self.text) + chrome + _TEXT_SLACK_PX
        width = min(TOAST_MAX_WIDTH_PX, workspace.width() - 2 * TOAST_MARGIN_PX, needed)
        width = max(chrome + 40, width)
        self._shown_text = metrics.elidedText(self.text, Qt.TextElideMode.ElideRight, width - chrome)
        x = workspace.left() + (workspace.width() - width) // 2
        y = workspace.bottom() + 1 - TOAST_MARGIN_PX - TOAST_HEIGHT_PX + round(self._rise)
        self.setGeometry(x, y, width, TOAST_HEIGHT_PX)

    # -- motion ----------------------------------------------------------------

    def _faded_in(self, progress: float) -> None:
        self.opacity = self._in_from + (1.0 - self._in_from) * progress
        self._rise = TOAST_RISE_PX * (1.0 - self.opacity)
        self._place()
        self.update()

    def _fade_out(self) -> None:
        self._in.stop()
        if not self.isVisible():
            self._gone()
            return
        self._out.start()

    def _faded_out(self, progress: float) -> None:
        self.opacity = 1.0 - progress
        self.update()

    def _gone(self) -> None:
        self.opacity = 0.0
        self._rise = TOAST_RISE_PX
        self.hide()

    # -- painting ------------------------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        from tomography_session_browser.ui.theme import current_palette

        palette = current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(max(0.0, min(1.0, self.opacity)))
        pill = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = pill.height() / 2.0  # a pill: the radius follows the height
        painter.setPen(QPen(QColor(palette.border_strong), 1.0))
        painter.setBrush(QColor(palette.surface_hi))
        painter.drawRoundedRect(pill, radius, radius)
        dot = {
            TONE_SUCCESS: palette.chart_green,
            TONE_ERROR: palette.chart_red,
        }.get(self.tone, palette.accent)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(dot))
        centre_y = self.height() / 2.0
        painter.drawEllipse(QRectF(_PADDING_PX, centre_y - _DOT_PX / 2.0, _DOT_PX, _DOT_PX))
        painter.setPen(QColor(palette.text_strong))
        painter.setFont(self._font())
        text_left = _PADDING_PX + _DOT_PX + 10
        painter.drawText(
            QRectF(text_left, 0.0, self.width() - text_left - _PADDING_PX, self.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self._shown_text,
        )
