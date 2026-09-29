"""Session folders dropped onto the project panel.

While folders are dragged over the panel, :class:`SessionDropOverlay` says
whether they can load: every one (sage), some (amber) or none (red), with a
row per folder and the reason a folder will not load. It is checked before
the drop (``services/session_intake.py``), so the answer is there before the
mouse is released.

A drop with anything loadable hands straight to the loader: the overlay goes
in the same turn and the existing loading screen is the confirmation. Nothing
here animates while that screen is up; drops are refused during a load. What a
drop skipped is kept by the receipt (:class:`SessionDropReceipt`), shown once
the loading screen has handed over, and :class:`ProjectDropHint` invites a
drop into an empty panel.

Motion is presentation only and follows ``motion_enabled``: with reduced
motion every state still appears, only the movement is dropped. One painted
widget, no opacity effect.
"""

from __future__ import annotations

from collections.abc import Callable
import math

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAccessible,
    QAccessibleAnnouncementEvent,
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
import shiboken6

from tomography_session_browser.services.session_intake import DropReceipt, DroppedFolder, FolderCheck, SessionIntake
from tomography_session_browser.ui.icons import themed_icon
from tomography_session_browser.ui.motion import TICKER, ease_out_cubic, motion_enabled
from tomography_session_browser.ui.theme import (
    RADIUS_CONTROL,
    SPACE_L,
    SPACE_M,
    SPACE_S,
    SPACE_XL,
    SPACE_XS,
    TYPE_BODY,
    TYPE_CAPTION,
    TYPE_LEAD,
    ThemePalette,
    current_palette,
)

# Module-level, so a test can see what the overlay tells screen readers.
_update_accessibility = QAccessible.updateAccessibility

# -- tones and glyphs ---------------------------------------------------------


def tone_colour(palette: ThemePalette, tone: str) -> QColor:
    """The palette colour for a drop tone (``valid``/``mixed``/``invalid``)."""

    slot = {
        "valid": palette.selection_marker,
        "good": palette.selection_marker,
        "mixed": palette.chart_amber,
        "warn": palette.chart_amber,
        "invalid": palette.chart_red,
        "bad": palette.chart_red,
        "accent": palette.accent,
    }.get(tone, palette.text_muted)
    return QColor(slot)


def _with_alpha(colour: QColor | str, alpha: float) -> QColor:
    result = QColor(colour)
    result.setAlphaF(max(0.0, min(1.0, alpha)))
    return result


def _folder_outline() -> QPainterPath:
    """A folder in a 24-unit box, the shape the toolbar's folder icons use."""

    path = QPainterPath()
    path.moveTo(4.0, 20.0)
    path.quadTo(2.0, 20.0, 2.0, 18.0)
    path.lineTo(2.0, 5.0)
    path.quadTo(2.0, 3.0, 4.0, 3.0)
    path.lineTo(7.9, 3.0)
    path.quadTo(8.9, 3.0, 9.6, 3.9)
    path.lineTo(10.4, 5.1)
    path.quadTo(11.1, 6.0, 12.1, 6.0)
    path.lineTo(20.0, 6.0)
    path.quadTo(22.0, 6.0, 22.0, 8.0)
    path.lineTo(22.0, 18.0)
    path.quadTo(22.0, 20.0, 20.0, 20.0)
    path.closeSubpath()
    return path


def paint_glyph(painter: QPainter, name: str, rect: QRectF, colour: QColor, *, stroke: float = 1.6) -> None:
    """Paint a stroke glyph (24-unit design) into ``rect``.

    Names: ``folder``, ``folder-down``, ``folder-plus``, ``folder-alert``,
    ``folder-x``, ``check-circle``, ``x-circle``, ``info``, ``alert``, ``x``.
    """

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    scale = min(rect.width(), rect.height()) / 24.0
    painter.translate(rect.center().x() - 12.0 * scale, rect.center().y() - 12.0 * scale)
    painter.scale(scale, scale)
    pen = QPen(colour, stroke)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    def line(x1: float, y1: float, x2: float, y2: float) -> None:
        painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

    def dot(x: float, y: float) -> None:
        painter.drawPoint(QPointF(x, y))

    if name.startswith("folder"):
        painter.drawPath(_folder_outline())
        if name == "folder-down":
            line(12.0, 10.0, 12.0, 16.0)
            line(9.0, 13.0, 12.0, 16.0)
            line(15.0, 13.0, 12.0, 16.0)
        elif name == "folder-plus":
            line(12.0, 10.0, 12.0, 16.0)
            line(9.0, 13.0, 15.0, 13.0)
        elif name == "folder-alert":
            line(12.0, 10.0, 12.0, 13.5)
            dot(12.0, 16.5)
        elif name == "folder-x":
            line(9.5, 11.0, 14.5, 16.0)
            line(14.5, 11.0, 9.5, 16.0)
    elif name in ("check-circle", "x-circle", "info"):
        painter.drawEllipse(QPointF(12.0, 12.0), 9.0, 9.0)
        if name == "check-circle":
            tick = QPainterPath()
            tick.moveTo(8.5, 12.5)
            tick.lineTo(11.0, 15.0)
            tick.lineTo(15.5, 10.0)
            painter.drawPath(tick)
        elif name == "x-circle":
            line(9.5, 9.5, 14.5, 14.5)
            line(14.5, 9.5, 9.5, 14.5)
        else:
            line(12.0, 16.0, 12.0, 11.5)
            dot(12.0, 8.0)
    elif name == "alert":
        triangle = QPainterPath()
        triangle.moveTo(12.0, 3.5)
        triangle.lineTo(21.5, 20.0)
        triangle.lineTo(2.5, 20.0)
        triangle.closeSubpath()
        painter.drawPath(triangle)
        line(12.0, 9.5, 12.0, 13.5)
        dot(12.0, 16.8)
    elif name == "x":
        line(18.0, 6.0, 6.0, 18.0)
        line(6.0, 6.0, 18.0, 18.0)
    painter.restore()


def paint_brackets(painter: QPainter, rect: QRectF, arm: float, radius: float = 4.0) -> None:
    """Four corner brackets: the drop target's frame (the pen is the caller's)."""

    left, top, right, bottom = rect.left(), rect.top(), rect.right(), rect.bottom()
    for x, y, dx, dy in ((left, top, 1, 1), (right, top, -1, 1), (left, bottom, 1, -1), (right, bottom, -1, -1)):
        path = QPainterPath()
        path.moveTo(x, y + dy * arm)
        path.lineTo(x, y + dy * radius)
        path.quadTo(x, y, x + dx * radius, y)
        path.lineTo(x + dx * arm, y)
        painter.drawPath(path)


def _announce(widget: QWidget, text: str) -> None:
    """Tell screen readers politely; never an Alert (Qt plays a sound for one)."""

    announcement = QAccessibleAnnouncementEvent(widget, text)
    announcement.setPoliteness(QAccessible.AnnouncementPoliteness.Polite)
    _update_accessibility(announcement)


class Glyph(QWidget):
    """A small painted glyph in a tone, optionally inside a soft ring."""

    def __init__(self, name: str, tone: str = "muted", size: int = 18, parent: QWidget | None = None, *, ring: bool = False) -> None:
        super().__init__(parent)
        self._name = name
        self._tone = tone
        self._ring = ring
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    @property
    def glyph(self) -> tuple[str, str]:
        return self._name, self._tone

    def set_glyph(self, name: str, tone: str) -> None:
        self._name, self._tone = name, tone
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        palette = current_palette()
        colour = tone_colour(palette, self._tone)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self._ring:
            muted = self._tone == "muted"
            painter.setPen(QPen(QColor(palette.border_strong) if muted else _with_alpha(colour, 0.3), 1.0))
            painter.setBrush(QColor(palette.surface) if muted else _with_alpha(colour, 0.1))
            painter.drawEllipse(bounds)
            side = bounds.width() * 0.44
            inner = QRectF(0.0, 0.0, side, side)
            inner.moveCenter(bounds.center())
            paint_glyph(painter, self._name, inner, colour)
        else:
            paint_glyph(painter, self._name, bounds, colour, stroke=1.8)


class GlyphButton(QAbstractButton):
    """An icon-only button (dismiss, remove) drawn from the palette."""

    def __init__(self, glyph: str, accessible_name: str, parent: QWidget | None = None, *, size: int = 26) -> None:
        super().__init__(parent)
        self._glyph = glyph
        self.setAccessibleName(accessible_name)
        self.setToolTip(accessible_name)
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return self.size()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        palette = current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        active = self.underMouse() or self.isDown()
        if active:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(palette.surface_hi))
            painter.drawRoundedRect(bounds, RADIUS_CONTROL, RADIUS_CONTROL)
        if self.hasFocus():
            painter.setPen(QPen(QColor(palette.accent), 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(bounds, RADIUS_CONTROL, RADIUS_CONTROL)
        colour = QColor(palette.text if active or self.hasFocus() else palette.text_muted)
        side = min(bounds.width(), bounds.height()) * 0.5
        inner = QRectF(0.0, 0.0, side, side)
        inner.moveCenter(bounds.center())
        paint_glyph(painter, self._glyph, inner, colour, stroke=2.0)


# -- the drop overlay ---------------------------------------------------------


class _AmbientClock:
    """Elapsed time on the shared ticker, for as long as the overlay shows.

    It has the ticker's interface (``step``, ``settled``) and never settles on
    its own, so it shares the one frame timer and stops only when told.
    """

    def __init__(self, on_step: Callable[[float], None]) -> None:
        self._on_step = on_step
        self._running = False
        self.elapsed = 0.0

    @property
    def settled(self) -> bool:
        return not self._running

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        self.elapsed = 0.0
        self._running = True
        TICKER.add(self)  # type: ignore[arg-type]  # same interface as Spring

    def stop(self) -> None:
        self._running = False
        TICKER.remove(self)  # type: ignore[arg-type]

    def step(self, dt: float) -> None:
        self.elapsed += max(0.0, dt)
        self._on_step(self.elapsed)

    def _notify_settled(self) -> None:
        return None


#: Motion, in seconds (see the canvas's motion spec).
SCRIM_FADE_S = 0.16
BRACKETS_CLOSE_S = 0.22
BREATHE_PERIOD_S = 2.4
BREATHE_DEPTH = 0.025
ORBIT_PERIOD_S = 9.0
LIFT_PX = 3.0
ROW_RISE_S = 0.32
ROW_RISE_PX = 6.0
ROW_STAGGER_S = 0.06
ROW_FIRST_DELAY_S = 0.08
NUDGE_DELAY_S = 0.2
NUDGE_S = 0.42
NUDGE_PX = 4.0

BRACKET_INSET_PX = SPACE_M
BRACKET_ARM_PX = 22.0
#: Opaque enough that the panel beneath never reads through the rows.
SCRIM_ALPHA = 0.95
_CONTENT_MARGIN_PX = 20.0
_EMBLEM_PX = 80.0
_EMBLEM_INNER_RADIUS = 29.0
_ORBIT_RADIUS = 38.0
_ROW_PX = 34.0
_ROW_PAD_PX = 7.0
#: Badge and glyph columns of a row: 10 + 26 + 10 before the text, 10 + 16 + 10 after.
_ROW_CHROME_PX = 82.0
_REASON_LINES = 3
_ROW_GAP_PX = 6.0


class SessionDropOverlay(QWidget):
    """The feedback painted over the project panel while folders are dragged."""

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.setObjectName("sessionDropOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._host = host
        self._intake: SessionIntake | None = None
        self._elapsed = 0.0
        self._animated = False
        self._clock = _AmbientClock(self._advance)
        self.destroyed.connect(lambda *_args, clock=self._clock: clock.stop())
        host.installEventFilter(self)
        self.hide()

    @property
    def intake(self) -> SessionIntake | None:
        return self._intake

    @property
    def animating(self) -> bool:
        return self._clock.running

    def show_intake(self, intake: SessionIntake) -> None:
        self._intake = intake
        self._elapsed = 0.0
        self._animated = motion_enabled(self)
        self.setGeometry(self._host.rect())
        self.setAccessibleName(intake.title)
        self.setAccessibleDescription(intake.detail)
        self.show()
        self.raise_()
        if self._animated:
            self._clock.start()
        else:
            self._clock.stop()
        self.update()
        _announce(self, f"{intake.title}. {intake.detail}")

    def clear(self) -> None:
        """Gone at once: a drop hands straight to the loading screen."""

        self._clock.stop()
        self._intake = None
        self.hide()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._clock.stop()
        super().hideEvent(event)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        if watched is self._host and event.type() == QEvent.Type.Resize and self.isVisible():
            self.setGeometry(self._host.rect())
        return False

    def _advance(self, elapsed: float) -> None:
        if not shiboken6.isValid(self) or not self.isVisible():
            self._clock.stop()
            return
        self._elapsed = elapsed
        self.update()

    # -- timing ---------------------------------------------------------------

    def _progress(self, start: float, duration: float) -> float:
        if not self._animated:
            return 1.0
        return ease_out_cubic(max(0.0, min(1.0, (self._elapsed - start) / duration)))

    def _wave(self, period: float) -> float:
        """0 → 1 → 0 over ``period``; still (0) without motion."""

        if not self._animated:
            return 0.0
        return 0.5 - 0.5 * math.cos(2.0 * math.pi * self._elapsed / period)

    def _nudge(self) -> float:
        if not self._animated:
            return 0.0
        t = (self._elapsed - NUDGE_DELAY_S) / NUDGE_S
        if not 0.0 < t < 1.0:
            return 0.0
        return math.sin(t * 4.0 * math.pi) * NUDGE_PX * (1.0 - t)

    # -- painting ---------------------------------------------------------------

    def _fonts(self) -> tuple[QFont, QFont, QFont, QFont, QFont]:
        base = QFont(self.font())
        title = QFont(base)
        title.setPointSizeF(TYPE_LEAD)
        title.setWeight(QFont.Weight.DemiBold)
        detail = QFont(base)
        detail.setPointSizeF(TYPE_CAPTION)
        name = QFont(base)
        name.setPointSizeF(TYPE_BODY)
        badge = QFont(base)
        badge.setPointSizeF(TYPE_CAPTION)
        badge.setWeight(QFont.Weight.Bold)
        return title, detail, name, badge, detail

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        intake = self._intake
        if intake is None:
            return
        palette = current_palette()
        tone = tone_colour(palette, intake.tone)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        bounds = QRectF(self.rect())

        fade = self._progress(0.0, SCRIM_FADE_S)
        painter.fillRect(bounds, _with_alpha(palette.panel, SCRIM_ALPHA * fade))
        painter.fillRect(bounds, _with_alpha(tone, 0.05 * fade))

        inset = BRACKET_INSET_PX * self._progress(0.0, BRACKETS_CLOSE_S)
        frame = bounds.adjusted(inset, inset, -inset, -inset)
        if intake.can_load:
            squeeze = BREATHE_DEPTH * self._wave(BREATHE_PERIOD_S)
            dx, dy = frame.width() * squeeze / 2.0, frame.height() * squeeze / 2.0
            frame = frame.adjusted(dx, dy, -dx, -dy)
        painter.setPen(QPen(_with_alpha(tone, 0.95 if intake.can_load else 0.8), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        paint_brackets(painter, frame, BRACKET_ARM_PX)

        self._paint_content(painter, palette, tone, intake, bounds)

    def _paint_content(
        self, painter: QPainter, palette: ThemePalette, tone: QColor, intake: SessionIntake, bounds: QRectF
    ) -> None:
        title_font, detail_font, name_font, badge_font, note_font = self._fonts()
        width = max(120.0, bounds.width() - 2.0 * _CONTENT_MARGIN_PX)
        left = bounds.center().x() - width / 2.0
        wrap = int(Qt.AlignmentFlag.AlignHCenter) | int(Qt.TextFlag.TextWordWrap)

        def text_height(font: QFont, text: str) -> float:
            if not text:
                return 0.0
            return float(QFontMetrics(font).boundingRect(0, 0, int(width), 10_000, wrap, text).height())

        title_h = text_height(title_font, intake.title)
        detail_h = text_height(detail_font, intake.detail)
        note_h = text_height(note_font, intake.footnote)
        fixed = _EMBLEM_PX + 18.0 + title_h + 4.0 + detail_h + (12.0 + note_h if note_h else 0.0)
        available = bounds.height() - 2.0 * (BRACKET_INSET_PX + SPACE_XL) - fixed - 18.0
        rows: list[DroppedFolder] = []
        used = 0.0
        more_h = QFontMetrics(note_font).height() + 4.0
        for index, folder in enumerate(intake.folders):
            height = self._row_height(folder, width, name_font, note_font)
            remaining = len(intake.folders) - index - 1
            reserve = more_h if remaining else 0.0
            if used + height + reserve > available and rows:
                break
            rows.append(folder)
            used += height + _ROW_GAP_PX
        hidden = len(intake.folders) - len(rows)
        rows_h = max(0.0, used - _ROW_GAP_PX) + (more_h if hidden else 0.0)
        total = fixed + (18.0 + rows_h if rows else 0.0)
        y = bounds.center().y() - total / 2.0

        self._paint_emblem(painter, palette, tone, intake, QPointF(bounds.center().x(), y + _EMBLEM_PX / 2.0))
        y += _EMBLEM_PX + 18.0
        painter.setFont(title_font)
        painter.setPen(QColor(palette.text_strong))
        painter.drawText(QRectF(left, y, width, title_h), wrap, intake.title)
        y += title_h + 4.0
        painter.setFont(detail_font)
        painter.setPen(QColor(palette.text_muted))
        painter.drawText(QRectF(left, y, width, detail_h), wrap, intake.detail)
        y += detail_h
        if rows:
            y += 18.0
            for index, folder in enumerate(rows):
                height = self._row_height(folder, width, name_font, note_font)
                shown = self._progress(ROW_FIRST_DELAY_S + ROW_STAGGER_S * index, ROW_RISE_S)
                painter.save()
                painter.setOpacity(shown)
                self._paint_row(
                    painter,
                    palette,
                    folder,
                    QRectF(left, y + ROW_RISE_PX * (1.0 - shown), width, height),
                    name_font,
                    badge_font,
                    note_font,
                )
                painter.restore()
                y += height + _ROW_GAP_PX
            if hidden:
                painter.setFont(note_font)
                painter.setPen(QColor(palette.text_muted))
                painter.drawText(QRectF(left, y, width, more_h), wrap, f"and {hidden} more")
                y += more_h
            else:
                y -= _ROW_GAP_PX
        if note_h:
            y += 12.0
            painter.setFont(note_font)
            painter.setPen(QColor(palette.text_muted))
            painter.drawText(QRectF(left, y, width, note_h), wrap, intake.footnote)

    @staticmethod
    def _reason_height(folder: DroppedFolder, width: float, note_font: QFont) -> float:
        """The reason wraps, up to three lines: a narrow panel still says why."""

        metrics = QFontMetrics(note_font)
        text_width = int(max(0.0, width - _ROW_CHROME_PX))
        wrapped = metrics.boundingRect(0, 0, text_width, 10_000, int(Qt.TextFlag.TextWordWrap), folder.reason).height()
        return float(min(wrapped, metrics.lineSpacing() * _REASON_LINES))

    def _row_height(self, folder: DroppedFolder, width: float, name_font: QFont, note_font: QFont) -> float:
        if not folder.problem:
            return _ROW_PX
        name_h = QFontMetrics(name_font).height()
        return _ROW_PAD_PX * 2.0 + name_h + self._reason_height(folder, width, note_font)

    def _paint_emblem(
        self, painter: QPainter, palette: ThemePalette, tone: QColor, intake: SessionIntake, centre: QPointF
    ) -> None:
        if intake.can_load:
            orbit = QPen(_with_alpha(tone, 0.6), 1.25)
            orbit.setCapStyle(Qt.PenCapStyle.RoundCap)
            orbit.setDashPattern([1.6, 5.6])
            painter.save()
            painter.translate(centre)
            painter.rotate(360.0 * self._elapsed / ORBIT_PERIOD_S if self._animated else 0.0)
            painter.setPen(orbit)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(0.0, 0.0), _ORBIT_RADIUS, _ORBIT_RADIUS)
            painter.restore()
            offset = QPointF(0.0, -LIFT_PX * self._wave(BREATHE_PERIOD_S))
        else:
            offset = QPointF(self._nudge(), 0.0)
        inner = centre + offset
        painter.setPen(QPen(_with_alpha(tone, 0.4), 1.0))
        painter.setBrush(_with_alpha(tone, 0.12))
        painter.drawEllipse(inner, _EMBLEM_INNER_RADIUS, _EMBLEM_INNER_RADIUS)
        glyph = {"valid": "folder-down", "mixed": "folder-alert"}.get(intake.tone, "folder-x")
        paint_glyph(painter, glyph, QRectF(inner.x() - 12.0, inner.y() - 12.0, 24.0, 24.0), tone)

        if not intake.can_load:
            return
        loadable = len(intake.ready) + len(intake.unchecked)
        label = f"{len(intake.ready)}/{len(intake.folders)}" if intake.problems else str(loadable)
        _title, _detail, _name, badge_font, _note = self._fonts()
        metrics = QFontMetrics(badge_font)
        pill_w = max(20.0, metrics.horizontalAdvance(label) + 12.0)
        pill = QRectF(centre.x() + _EMBLEM_PX / 2.0 - pill_w + (6.0 if intake.problems else 0.0), centre.y() - _EMBLEM_PX / 2.0 + 4.0, pill_w, 20.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(palette.panel))
        ring = pill.adjusted(-3.0, -3.0, 3.0, 3.0)
        painter.drawRoundedRect(ring, ring.height() / 2.0, ring.height() / 2.0)
        painter.setBrush(tone)
        painter.drawRoundedRect(pill, pill.height() / 2.0, pill.height() / 2.0)
        painter.setFont(badge_font)
        painter.setPen(QColor(palette.primary_text))
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, label)

    def _paint_row(
        self,
        painter: QPainter,
        palette: ThemePalette,
        folder: DroppedFolder,
        rect: QRectF,
        name_font: QFont,
        badge_font: QFont,
        note_font: QFont,
    ) -> None:
        bad = tone_colour(palette, "invalid")
        card = rect.adjusted(0.5, 0.5, -0.5, -0.5)
        if folder.problem:
            painter.setPen(QPen(_with_alpha(bad, 0.24), 1.0))
            painter.setBrush(_with_alpha(bad, 0.06))
        else:
            painter.setPen(QPen(QColor(palette.border), 1.0))
            painter.setBrush(_with_alpha(palette.surface, 0.92))
        painter.drawRoundedRect(card, RADIUS_CONTROL, RADIUS_CONTROL)

        centre_y = rect.center().y()
        badge = QRectF(rect.left() + 10.0, centre_y - 9.0, 26.0, 18.0)
        if folder.badge:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(palette.surface_hi))
            painter.drawRoundedRect(badge, RADIUS_CONTROL, RADIUS_CONTROL)
            painter.setPen(QColor(palette.text))
            badge_text = folder.badge
        else:
            dashed = QPen(QColor(palette.unknown), 1.0)
            dashed.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(dashed)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(badge.adjusted(0.5, 0.5, -0.5, -0.5), RADIUS_CONTROL, RADIUS_CONTROL)
            painter.setPen(QColor(palette.text_muted))
            badge_text = "–"
        painter.setFont(badge_font)
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, badge_text)

        mark = QRectF(rect.right() - 10.0 - 16.0, centre_y - 8.0, 16.0, 16.0)
        if folder.ready:
            paint_glyph(painter, "check-circle", mark, tone_colour(palette, "valid"), stroke=1.8)
        elif folder.status == FolderCheck.UNCHECKED:
            paint_glyph(painter, "info", mark, QColor(palette.text_muted), stroke=1.8)
        else:
            paint_glyph(painter, "x-circle", mark, bad, stroke=1.8)

        text_left = badge.right() + 10.0
        text_width = max(0.0, mark.left() - 10.0 - text_left)
        name_metrics = QFontMetrics(name_font)
        name = name_metrics.elidedText(folder.name, Qt.TextElideMode.ElideMiddle, int(text_width))
        painter.setFont(name_font)
        if folder.problem:
            painter.setPen(QColor(palette.text))
            name_rect = QRectF(text_left, rect.top() + _ROW_PAD_PX, text_width, name_metrics.height())
            painter.drawText(name_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
            painter.setFont(note_font)
            painter.setPen(bad)
            painter.drawText(
                QRectF(text_left, name_rect.bottom(), text_width, self._reason_height(folder, rect.width(), note_font)),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop) | int(Qt.TextFlag.TextWordWrap),
                folder.reason,
            )
        else:
            painter.setPen(QColor(palette.text_strong))
            painter.drawText(
                QRectF(text_left, rect.top(), text_width, rect.height()),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                name,
            )


def dropped_paths(mime_data) -> list[str]:
    """The local paths a drag carries (web links and the like are ignored)."""

    if mime_data is None or not mime_data.hasUrls():
        return []
    return [url.toLocalFile() for url in mime_data.urls() if url.isLocalFile() and url.toLocalFile()]


class SessionDropTarget(QObject):
    """Turns drags over ``widget`` into drop feedback and, on a drop, a load.

    ``assess`` checks the dragged paths (``None`` refuses the drag, as during
    a load); ``refused`` is the cheap check repeated while the drag moves;
    ``load`` receives the checked drop one turn later, once the operating
    system's drag has finished, so a load never starts inside the drop.
    """

    def __init__(
        self,
        widget: QWidget,
        overlay: SessionDropOverlay,
        *,
        assess: Callable[[list[str]], SessionIntake | None],
        refused: Callable[[], bool],
        load: Callable[[SessionIntake], None],
    ) -> None:
        super().__init__(widget)
        self._widget = widget
        self._overlay = overlay
        self._assess = assess
        self._refused = refused
        self._load = load
        widget.setAcceptDrops(True)
        widget.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        if watched is not self._widget:
            return False
        kind = event.type()
        if kind == QEvent.Type.DragEnter:
            self._drag_enter(event)
            return True
        if kind == QEvent.Type.DragMove:
            self._drag_move(event)
            return True
        if kind == QEvent.Type.DragLeave:
            self._overlay.clear()
            return True
        if kind == QEvent.Type.Drop:
            self._drop(event)
            return True
        return False

    def _drag_enter(self, event) -> None:
        paths = dropped_paths(event.mimeData())
        intake = self._assess(paths) if paths else None
        if intake is None:
            self._overlay.clear()
            event.ignore()
            return
        self._overlay.show_intake(intake)
        # Accepted either way, so the feedback stays while the drag is over
        # the panel; a drop that could load nothing shows the no-drop cursor.
        event.setDropAction(Qt.DropAction.CopyAction if intake.can_load else Qt.DropAction.IgnoreAction)
        event.accept()

    def _drag_move(self, event) -> None:
        intake = self._overlay.intake
        if intake is None or self._refused():
            self._overlay.clear()
            event.ignore()
            return
        if intake.can_load:
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.setDropAction(Qt.DropAction.IgnoreAction)
            event.ignore()

    def _drop(self, event) -> None:
        intake = self._overlay.intake
        self._overlay.clear()
        if intake is None or not intake.can_load or self._refused():
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        QTimer.singleShot(0, self, lambda: self._load(intake))


# -- after the drop -------------------------------------------------------------


class SessionDropReceipt(QFrame):
    """What a drop loaded and what it skipped, kept until dismissed.

    Shown only when something was skipped; a drop that loaded everything is
    confirmed by the usual toast. It never appears while the loading screen
    is up (the window shows it once the hand-off has finished).
    """

    dismissed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("dropReceipt")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAccessibleName("Drop result")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        head = QWidget(self)
        head_layout = QHBoxLayout(head)
        head_layout.setContentsMargins(SPACE_M, SPACE_M, SPACE_S, SPACE_M)
        head_layout.setSpacing(SPACE_S)
        self.icon = Glyph("check-circle", "good", 18, head)
        head_layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        self.title = QLabel("", head)
        self.title.setObjectName("dropReceiptTitle")
        self.title.setWordWrap(True)
        head_layout.addWidget(self.title, 1)
        self.dismiss_button = GlyphButton("x", "Dismiss", head)
        self.dismiss_button.clicked.connect(self.dismiss)
        head_layout.addWidget(self.dismiss_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(head)

        self.section = QFrame(self)
        self.section.setObjectName("dropReceiptSection")
        section_layout = QVBoxLayout(self.section)
        section_layout.setContentsMargins(SPACE_M, SPACE_S, SPACE_M, SPACE_M)
        section_layout.setSpacing(SPACE_XS)
        skipped_row = QHBoxLayout()
        skipped_row.setSpacing(SPACE_S)
        skipped_row.addWidget(Glyph("alert", "warn", 16, self.section))
        self.skipped = QLabel("", self.section)
        self.skipped.setObjectName("dropReceiptSkipped")
        skipped_row.addWidget(self.skipped, 1)
        section_layout.addLayout(skipped_row)
        self.lines = QLabel("", self.section)
        self.lines.setObjectName("dropReceiptLines")
        self.lines.setWordWrap(True)
        self.lines.setContentsMargins(SPACE_XL, 0, 0, 0)
        section_layout.addWidget(self.lines)
        layout.addWidget(self.section)
        self.hide()

    def show_receipt(self, receipt: DropReceipt) -> None:
        self.title.setText(receipt.title)
        self.skipped.setText(receipt.skipped_title)
        self.lines.setText("\n".join(receipt.skipped_lines))
        self.section.setVisible(bool(receipt.skipped))
        self.setAccessibleDescription(" ".join((receipt.title, receipt.skipped_title, *receipt.skipped_lines)))
        self.show()
        _announce(self, f"{receipt.title}. {receipt.skipped_title}".strip(". "))

    def dismiss(self) -> None:
        self.hide()
        self.dismissed.emit()


class ProjectDropHint(QWidget):
    """The empty project panel: drop folders here, or load them."""

    load_requested = Signal()

    def __init__(self, parent: QWidget | None = None, *, shortcut_text: str = "") -> None:
        super().__init__(parent)
        self.setObjectName("projectDropHint")
        # It takes the width the dock gives it and never sets the panel's
        # minimum: the dock's own minimum (220 px) and fitted width decide.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_L, SPACE_XL, SPACE_L, SPACE_XL)
        layout.setSpacing(SPACE_L)
        layout.addStretch(1)
        self.emblem = Glyph("folder-down", "muted", 56, self, ring=True)
        layout.addWidget(self.emblem, 0, Qt.AlignmentFlag.AlignHCenter)
        text = QVBoxLayout()
        text.setSpacing(SPACE_XS)
        self.title = QLabel("No sessions loaded", self)
        self.title.setObjectName("dropHintTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.title.setWordWrap(True)
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        text.addWidget(self.title)
        self.detail = QLabel("Drag atlas or data collection folders here — several at once is fine.", self)
        self.detail.setObjectName("dropHintDetail")
        self.detail.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.detail.setWordWrap(True)
        # Wraps to the panel rather than setting its minimum width.
        self.detail.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        text.addWidget(self.detail)
        layout.addLayout(text)
        self.load_button = QPushButton("Load sessions…", self)
        self.load_button.setIcon(themed_icon("folder-plus"))
        self.load_button.clicked.connect(self.load_requested)
        layout.addWidget(self.load_button, 0, Qt.AlignmentFlag.AlignHCenter)
        self.shortcut = QLabel(shortcut_text, self)
        self.shortcut.setObjectName("dropHintShortcut")
        self.shortcut.setVisible(bool(shortcut_text))
        layout.addWidget(self.shortcut, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().paintEvent(event)
        palette = current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(_with_alpha(palette.control_border, 0.6), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        frame = QRectF(self.rect()).adjusted(SPACE_M, SPACE_XS, -SPACE_M, -SPACE_M)
        paint_brackets(painter, frame, 18.0)
