"""The movie dialog's parts (boards 3 and 3b): the frame preview, a section's line and its card, a labelled settings
row, segments sized to their text, typed fields, the rate field, picture chips and the chips of holds at chosen frames."""
from __future__ import annotations

from typing import TYPE_CHECKING
import unicodedata

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QRegularExpression, QSignalBlocker, QSize, Qt, Signal
from PySide6.QtGui import (QColor, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap, QRegularExpressionValidator,
                           QStandardItemModel)
from PySide6.QtWidgets import (QAbstractButton, QAbstractSpinBox, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel,
                               QLayout, QSizePolicy, QSpinBox, QStyle, QStyleOption, QToolButton, QVBoxLayout, QWidget)

from tomography_session_browser.presentation.design_tokens import (RADIUS_CARD, SPACE_M, SPACE_S, SPACE_XS,
                                                                   TOMOGRAM_PLANE_COLOURS)
from tomography_session_browser.presentation.tomogram_movie import ERROR, WARNING, SectionLine, hold_label
from tomography_session_browser.services.movie.timeline import CHOSEN_RANGE, FPS_RANGE, QUICK_RATES, ChosenHold
from tomography_session_browser.ui.dashboard.common import pill
from tomography_session_browser.ui.icons import themed_icon
from tomography_session_browser.ui.theme import current_palette
from tomography_session_browser.ui.tomograms.folding_section import Fold
from tomography_session_browser.ui.tomograms.image_settings import heading
from tomography_session_browser.ui.tomograms.layout_controls import Versions
from tomography_session_browser.ui.widgets.link_button import LinkButton

if TYPE_CHECKING:
    from tomography_session_browser.ui.tomograms.movie.dialog import MovieDialog

#: The settings grid of board 3b: the narrowest label column; the heights of a field and of a
#: segment with its 1 px margins. Push buttons take theirs from the style sheet.
LABEL_COLUMN, CONTROL_HEIGHT, SEGMENT_HEIGHT = 72, 28, 28
#: Typed values keep compact widths: plane numbers, hold times, and a chip's hold time.
PLANE_WIDTH, SECONDS_WIDTH, CHIP_SECONDS_WIDTH = 64, 52, 46
#: The chevron of a section's line: its box, and how far it spreads from its centre.
CHEVRON_BOX, CHEVRON_ARM = 14, 4.
#: The card's 1 px border, which the style sheet draws and the layout must leave room for.
CARD_BORDER = 1
#: A word this long is a name, not a word: a file name has no spaces to break at. It may wrap after a separator,
#: and after this many letters without one.
LONG_WORD, LONG_RUN = 20, 16
SEPARATORS = "_-."
ZERO_WIDTH_SPACE = "​"
PILL_TONES = {ERROR: "bad", WARNING: "warn"}


def breakable(text: str) -> str:
    """The text with break points inside its long words only, so short words still wrap whole."""
    def inside(word: str) -> str:
        parts, run = [], 0
        for letter, following in zip(word, word[1:] + " "):
            parts.append(letter)
            run += 1
            if (letter in SEPARATORS or run >= LONG_RUN) and not unicodedata.combining(following):
                parts.append(ZERO_WIDTH_SPACE)
                run = 0
        return "".join(parts)
    return " ".join(inside(word) if len(word) > LONG_WORD else word for word in text.split(" "))


def restyle(widget: QWidget) -> None:
    """A changed property reaches the style sheet only when the widget is polished again."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


class SectionLineButton(QAbstractButton):
    """The line of a section: its name, its settings in one sentence, its problem if it has one, and a chevron.

    The whole line is one button, so it never takes Enter from a field and a click
    anywhere on it opens or closes the section. The chevron turns with the fold.
    """

    def __init__(self, title: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("movieSectionLine")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        # A button's height is fixed by default: this one is as tall as its sentence wraps at the card's width.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.title = title
        self.turn = 0.
        row = QHBoxLayout(self)
        row.setContentsMargins(SPACE_M, SPACE_M, SPACE_M, SPACE_M)
        row.setSpacing(SPACE_M)
        self.name = heading(title, self)
        # Its width counts in no settings measure, and a name wraps inside it, so no file name widens the column.
        self.sentence = QLabel(self)
        self.sentence.setObjectName("movieSectionText")
        self.sentence.setWordWrap(True)
        self.problem = pill("", "")
        self.problem.hide()
        row.addWidget(self.name)
        row.addWidget(self.sentence, 1)
        row.addWidget(self.problem, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addSpacing(SPACE_M + CHEVRON_BOX)  # A layout's spacing does not count beside a spacer.
        for child in (self.name, self.sentence, self.problem):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.show_line(SectionLine(""))

    def show_line(self, line: SectionLine) -> None:
        self.sentence.setText(breakable(line.text))
        self.problem.setText(line.problem)
        self.problem.setVisible(bool(line.problem))
        tone = PILL_TONES.get(line.kind, "")
        if self.problem.property("tone") != tone:
            self.problem.setProperty("tone", tone)
            restyle(self.problem)
        self.setAccessibleName(f"{self.title}: {line.text}" + (f". {line.problem}." if line.problem else ""))

    def set_turn(self, turn: float) -> None:
        turn = max(0., min(1., turn))
        if turn != self.turn:
            self.turn = turn
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        option = QStyleOption()
        option.initFrom(self)
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget, option, painter, self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(self.width() - SPACE_M - CHEVRON_BOX / 2, self.height() / 2)
        painter.rotate(180 * self.turn)
        pen = QPen(QColor(current_palette().text_muted), 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        arm = CHEVRON_ARM
        painter.drawPolyline((QPointF(-arm, -arm / 2), QPointF(0, arm / 2), QPointF(arm, -arm / 2)))


class MovieSection(Fold):
    """One card of board 3b: its line, and under it the controls, which open and close as the app's folds do."""

    opened = Signal()

    def __init__(self, title: str, parent: QWidget, *, open_: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("movieSection")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setProperty("open", open_)
        self.column.setContentsMargins(CARD_BORDER, CARD_BORDER, CARD_BORDER, CARD_BORDER)
        self.line = SectionLineButton(title, self)
        self.line.setChecked(open_)
        self.column.addWidget(self.line)
        self.add_body(self.line, open_, (SPACE_M, 0, SPACE_M, SPACE_M))

    @property
    def padding(self) -> int:
        """The width that the card takes around a settings row."""
        margins = self.layout_.contentsMargins()
        return margins.left() + margins.right() + 2 * CARD_BORDER

    def show_open(self, open_: bool, *, animate: bool = True) -> None:
        if bool(self.property("open")) != open_:
            self.setProperty("open", open_)
            restyle(self)
        super().show_open(open_, animate=animate)

    def apply_height(self, height: float) -> None:
        super().apply_height(height)
        self.line.set_turn(height / self.full_height if self.full_height else 0.)

    def finish(self) -> None:
        super().finish()
        self.line.set_turn(1. if self.line.isChecked() else 0.)
        if self.line.isChecked():
            self.opened.emit()


class FramePreview(QWidget):
    """The composed frame, fitted and centred on the canvas; a short note while the next one is on its way."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("movieFramePreview")
        self.setMinimumSize(360, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName("Movie frame preview")
        self.image: QImage | None = None
        self.message = "Preparing the preview…"

    def set_frame(self, frame: np.ndarray) -> None:
        frame = np.ascontiguousarray(frame)
        height, width = frame.shape[:2]
        self.image = QImage(frame.data, width, height, frame.strides[0], QImage.Format.Format_RGB888).copy()
        self.message = ""
        self.update()

    def set_message(self, text: str) -> None:
        self.message = text
        self.image = None
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        palette = current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        outline = QPainterPath()
        outline.addRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), RADIUS_CARD, RADIUS_CARD)
        painter.fillPath(outline, QColor(palette.canvas))
        if self.image is None:
            painter.setPen(QColor(palette.text_muted))
            painter.drawText(self.rect().adjusted(SPACE_M, 0, -SPACE_M, 0),
                             Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.message)
        else:
            inner = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            scale = min(inner.width() / self.image.width(), inner.height() / self.image.height())
            width, height = self.image.width() * scale, self.image.height() * scale
            painter.save()
            painter.setClipPath(outline)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, scale < 1)
            painter.drawImage(QRectF(inner.center().x() - width / 2, inner.center().y() - height / 2, width, height),
                              self.image)
            painter.restore()
        painter.setPen(QPen(QColor(palette.border), 1))
        painter.drawPath(outline)


class SettingRow(QWidget):
    """One row of the settings grid: its label in the label column, then the controls on one line.

    The label is level with that line. Lines under the controls (notes, chips)
    go in ``lines``, which start under the controls. A word-wrapped hint, a
    layout or a widget that grows takes the spare width; otherwise the line
    keeps its controls at the left. A number among the items is extra space.
    """

    def __init__(self, parent: QWidget, label: str, *items: QWidget | QLayout | int, top: bool = False) -> None:
        super().__init__(parent)
        outer = self.outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(SPACE_S)
        self.line = QHBoxLayout()
        self.line.setSpacing(SPACE_S)
        outer.addLayout(self.line)
        self.caption = QLabel(label, self)
        self.caption.setMinimumHeight(CONTROL_HEIGHT)
        # A line of chips can wrap: its label stays level with the first chips.
        self.line.addWidget(self.caption, 0, Qt.AlignmentFlag.AlignTop if top else Qt.AlignmentFlag(0))
        self.line.addSpacing(SPACE_M - SPACE_S)
        grows = (QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        for item in items:
            if isinstance(item, int):
                self.line.addSpacing(item)
            elif isinstance(item, QLayout):
                self.line.addLayout(item, 1)
            else:
                wraps = isinstance(item, QLabel) and item.wordWrap()
                self.line.addWidget(item, 1 if wraps or item.sizePolicy().horizontalPolicy() in grows else 0)
        self.line.addStretch()
        self.lines = QVBoxLayout()
        self.lines.setSpacing(SPACE_S)
        outer.addLayout(self.lines)
        self.set_column(LABEL_COLUMN)
        for item in items:
            if isinstance(item, QWidget) and item.focusPolicy() != Qt.FocusPolicy.NoFocus:
                self.caption.setBuddy(item)
                break

    def set_column(self, width: int) -> None:
        self.caption.setFixedWidth(width)
        self.lines.setContentsMargins(width + SPACE_M, 0, 0, 0)


class Segments(Versions):
    """Choices as the shared segmented control, each segment as wide as its text.

    ``enable`` switches one choice off (or on) in place, with its reason as the tooltip:
    rebuilding the buttons would lose their state.
    """

    def __init__(self, parent: QWidget, items: tuple[str, ...], name: str, *, tips: tuple[str, ...] = (),
                 dots: tuple[str, ...] = ()) -> None:
        super().__init__(parent, label=lambda text: text)
        self.addItems(items)
        self.setAccessibleName(name)
        self.tips = tips or ("",) * len(items)
        for index, tip in enumerate(self.tips):
            if tip:
                self.setItemData(index, tip, Qt.ItemDataRole.ToolTipRole)
        self.rebuild()
        self.hide()
        self.segments.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        for button in self.buttons.buttons():
            button.setFixedHeight(SEGMENT_HEIGHT)
        self.segments.setAccessibleName(name)
        for index, colour in enumerate(dots):
            button = self.buttons.button(index)
            assert isinstance(button, QToolButton)
            button.setIcon(dot(colour))
            button.setIconSize(QSize(8, 8))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)

    def enable(self, index: int, enabled: bool, reason: str = "") -> None:
        tip = self.tips[index] if enabled else reason
        model = self.model()
        assert isinstance(model, QStandardItemModel)
        item = model.item(index)
        item.setEnabled(enabled)
        item.setToolTip(tip)
        button = self.buttons.button(index)
        button.setEnabled(enabled)
        button.setToolTip(tip or self.itemText(index))


def dot(colour: str) -> QIcon:
    """A pane's colour dot, as the viewer marks its panes."""
    pixmap = QPixmap(16, 16)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(colour))
    painter.drawEllipse(QRectF(0, 0, 16, 16))
    painter.end()
    return QIcon(pixmap)


def pane_dots(axes) -> tuple[str, ...]:
    return tuple(TOMOGRAM_PLANE_COLOURS[axis] for axis in axes)


def hint(parent: QWidget, text: str = "", *, wrap: bool = True) -> QLabel:
    """A muted hint beside its control, at the text size of the labels."""
    label = QLabel(text, parent)
    label.setObjectName("movieHint")
    label.setWordWrap(wrap)
    return label


def plain(field: QAbstractSpinBox, name: str, width: int) -> None:
    """A narrow typed value: no step arrows to hide its digits; wheel and keys still step."""
    field.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
    field.setKeyboardTracking(False)
    field.setAccessibleName(name)
    field.setAlignment(Qt.AlignmentFlag.AlignCenter)
    field.setFixedSize(width, CONTROL_HEIGHT)


class Seconds(QDoubleSpinBox):
    """A time in seconds, written as 0, 0.5 or 2: whole seconds have no decimal."""

    def __init__(self, parent: QWidget, name: str, low: float, high: float, width: int = SECONDS_WIDTH) -> None:
        super().__init__(parent)
        self.setRange(low, high)
        self.setSingleStep(.5)
        self.setDecimals(1)
        plain(self, name, width)

    def textFromValue(self, value: float) -> str:  # noqa: N802 - Qt API
        text = self.locale().toString(value, "f", 1)
        whole = self.locale().decimalPoint() + "0"
        return text[:-len(whole)] if text.endswith(whole) else text


def plane_field(parent: QWidget, name: str) -> QSpinBox:
    field = QSpinBox(parent)
    plain(field, name, PLANE_WIDTH)
    return field


class RateField(QComboBox):
    """The rate: type a whole number of frames per second in the range, or select a common rate."""

    valueChanged = Signal(int)  # noqa: N815 - the spin box's name, which the dialog connects to

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setAccessibleName("Rate, frames per second")
        low, high = FPS_RANGE
        self.setToolTip(f"Type a rate from {low} to {high} fps, or select a common rate")
        for value in QUICK_RATES:
            self.addItem(f"{value} fps", value)
        self.current = QUICK_RATES[0]
        edit = self.lineEdit()
        assert edit is not None
        edit.setValidator(QRegularExpressionValidator(QRegularExpression(r"\s*\d{0,2}\s*(f|fp|fps)?\s*"), self))
        edit.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        edit.editingFinished.connect(self.commit)
        self.activated.connect(lambda index: self.setValue(self.itemData(index)))
        self.setFixedHeight(CONTROL_HEIGHT)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        """Room for the widest rate and the arrow, in this font."""
        return QSize(self.fontMetrics().horizontalAdvance(f"{FPS_RANGE[1]} fps") + 2 * CONTROL_HEIGHT, CONTROL_HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return self.sizeHint()

    def value(self) -> int:
        return self.current

    def setValue(self, value: int) -> None:  # noqa: N802 - the spin box's name
        low, high = FPS_RANGE
        value = max(low, min(high, int(value)))
        changed = value != self.current
        self.current = value
        with QSignalBlocker(self):
            index = self.findData(value)
            self.setCurrentIndex(index)
            self.setEditText(f"{value} fps")
        if changed:
            self.valueChanged.emit(value)

    def commit(self) -> None:
        digits = "".join(character for character in self.currentText() if character.isdigit())
        self.setValue(int(digits) if digits and int(digits) > 0 else self.current)


class PictureChip(QToolButton):
    """One item the picture can show: on, it has a check mark and the accent border."""

    def __init__(self, text: str, parent: QWidget, tip: str) -> None:
        super().__init__(parent)
        self.setObjectName("movieShowChip")
        self.setText(text)
        self.setAccessibleName(text)
        self.setToolTip(tip)
        self.setCheckable(True)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setIconSize(QSize(13, 13))
        self.setFixedHeight(CONTROL_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggled.connect(self.mark)
        self.mark(False)

    def mark(self, checked: bool) -> None:
        self.setIcon(themed_icon("check", color=current_palette().accent, size=13) if checked else QIcon())


class HoldChip(QFrame):
    """One hold at a chosen frame: the plane (moves the timeline to it), its time and a remove button."""

    def __init__(self, dialog: MovieDialog, hold: ChosenHold, unused: str) -> None:
        super().__init__(dialog)
        self.setObjectName("movieHoldChip")
        self.setProperty("unused", bool(unused))
        self.setToolTip(unused)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(CONTROL_HEIGHT)
        label = hold_label(dialog.settings.sweep, hold)
        row = QHBoxLayout(self)
        row.setContentsMargins(SPACE_S, 0, SPACE_XS // 2, 0)
        row.setSpacing(SPACE_XS)
        plane = LinkButton(label, self, tooltip=f"Move the timeline to {label}")
        plane.clicked.connect(lambda: dialog.go_to_hold(hold))
        plane.setEnabled(not unused)
        row.addWidget(plane)
        self.seconds = Seconds(self, f"Hold time on {label}", *CHOSEN_RANGE, width=CHIP_SECONDS_WIDTH)
        self.seconds.setObjectName("movieChipSeconds")
        self.seconds.setFixedHeight(CONTROL_HEIGHT - 6)
        self.seconds.setValue(hold.seconds)
        self.seconds.valueChanged.connect(lambda value: dialog.change_hold(hold, value))
        row.addWidget(self.seconds)
        unit = QLabel("s", self)
        unit.setObjectName("movieHint")
        row.addWidget(unit)
        remove = QToolButton(self)
        remove.setObjectName("movieChipRemove")
        remove.setText("×")
        remove.setAccessibleName(f"Remove the hold on {label}")
        remove.setToolTip(f"Remove the hold on {label}")
        remove.clicked.connect(lambda: dialog.remove_hold(hold))
        row.addWidget(remove)
