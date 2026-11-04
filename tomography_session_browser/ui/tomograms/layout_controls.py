"""Segmented choices and the canvas toolbar, from stock Qt controls.

The toolbar is one row while it fits: layout, pivot and slice controls on the
left; zoom, help, Focus and Image on the right. Extra controls move to More.
Every control is a 24 px cell inside a bordered 28 px group, so Slices and
zoom read as matching steppers.
"""
from collections.abc import Callable, Mapping

from PySide6.QtCore import QRectF, QSize, Qt, Signal, QSignalBlocker
from PySide6.QtGui import QColor, QPainter, QIcon
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel, QMenu,
                               QPushButton, QSizePolicy, QSpinBox, QToolButton, QWidget, QWidgetAction)

from tomography_session_browser.presentation.design_tokens import CANVAS_CHROME, SPACE_S
from tomography_session_browser.presentation.tomogram_geometry import NO_PLANE
from tomography_session_browser.ui.icons import themed_icon
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
from tomography_session_browser.ui.tomograms.oblique_controls import ObliqueControls

ICON_SIZE = QSize(16, 16)
#: The height and minimum width of one toolbar cell; its group adds 4 px.
TOOL_CELL = 24
#: The toolbar starts fully compact and only gains labels that still fit on its
#: row, so its height (and the panes under it) never changes after first layout.
COMPACTEST = 4
UNBOUNDED = 16777215


def reserved_width(metrics, texts: tuple[str, ...]) -> int:
    """The widest of texts with every digit drawn as the font's widest digit."""
    digit = max("0123456789", key=metrics.horizontalAdvance)
    return max(metrics.horizontalAdvance("".join(digit if c.isdigit() else c for c in text)) for text in texts)


class PlaneReadout(ElidedLabel):
    """Reserve stable caption space without forcing narrow layouts wider.

    The reserve is the widest caption the current view can show (reserve), so the
    track keeps its length while the plane changes and gains the rest of the band.
    """

    def __init__(self, parent):
        super().__init__(NO_PLANE, parent=parent, mode=Qt.TextElideMode.ElideLeft)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.reserved: tuple[str, ...] = ("Oblique XY · offset 9999 / 9999",)

    def reserve(self, texts: tuple[str, ...]) -> None:
        if texts != self.reserved:
            self.reserved = texts
            self.updateGeometry()

    def sizeHint(self) -> QSize:  # noqa: N802
        metrics = self.fontMetrics()
        return QSize(reserved_width(metrics, self.reserved) + 6, metrics.height() + 2)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(24, self.sizeHint().height())


class CoordinateReadout(QLabel):
    """Request one complete line, wrapping only when the footer really needs it."""
    def __init__(self, parent):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def sizeHint(self) -> QSize:  # noqa: N802
        metrics = self.fontMetrics()
        return QSize(metrics.horizontalAdvance(self.text()) + 4, metrics.height() + 2)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        metrics = self.fontMetrics()
        return QSize(max((metrics.horizontalAdvance(part) for part in self.text().split("  ")), default=0) + 4, metrics.height() + 2)

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        # Empty (no pointer yet) and one line take the same height, so the first
        # pointer move never resizes the panes and refits the image.
        return max(super().heightForWidth(width), self.sizeHint().height())


def canvas_icon(name: str):
    """Explicit raster states: quiet idle, strong hover/focus and checked."""
    icon = QIcon()
    muted = themed_icon(name, color=CANVAS_CHROME.text_muted, size=16)
    strong = themed_icon(name, color=CANVAS_CHROME.text_strong, size=16)
    for dpr in (1., 1.25, 1.5, 2., 3.):
        icon.addPixmap(muted.pixmap(ICON_SIZE, dpr), QIcon.Mode.Normal, QIcon.State.Off)
        icon.addPixmap(strong.pixmap(ICON_SIZE, dpr), QIcon.Mode.Normal, QIcon.State.On)
        for mode in (QIcon.Mode.Active, QIcon.Mode.Selected):
            for state in (QIcon.State.Off, QIcon.State.On):
                icon.addPixmap(strong.pixmap(ICON_SIZE, dpr), mode, state)
    return icon


def canvas_accent_icon(name: str):
    """A canvas icon in the accent: a short confirmation, such as a saved snapshot."""
    return themed_icon(name, color=CANVAS_CHROME.accent, size=16)


class ZoomValue(QPushButton):
    """A stable zoom-value cell before and after the first accepted image."""

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.fontMetrics().horizontalAdvance("1000%") + 28, super().sizeHint().height())


class Versions(QComboBox):
    """Keep the typed combo API (and a narrow fallback); show the choices as segments.

    ``canvas`` segments sit in the dark canvas toolbar and may be icons; the
    others use the application's shared segmented control.
    """

    def __init__(self, parent, *, canvas: bool = False, icons: Mapping[str, str] | None = None,
                 label: Callable[[str], str] | None = None) -> None:
        super().__init__(parent)
        self.canvas, self.icons = canvas, dict(icons or {})
        self.label = label or (lambda text: text.split(" · ")[0])
        self.segments = QFrame(parent)
        self.segments.setObjectName("tomogramToolGroup" if canvas else "segmentedControl")
        self.row = QHBoxLayout(self.segments)
        margin = 1 if canvas else 0  # Canvas segments are a toolbar group of cells.
        self.row.setContentsMargins(margin, margin, margin, margin)
        self.row.setSpacing(0)
        if canvas:
            self.segments.setFixedHeight(TOOL_CELL + 4)
        self.buttons = QButtonGroup(self)
        self.currentIndexChanged.connect(self.sync)
        self.buttons.idClicked.connect(self.select_index)

    def select_index(self, index: int) -> None:
        if index == self.currentIndex():
            self.activated.emit(index)
        else:
            self.setCurrentIndex(index)

    def make_button(self, text: str) -> QAbstractButton:
        if self.canvas:
            button = QPushButton(text, self.segments)
            button.setFixedHeight(TOOL_CELL)
            button.setMinimumWidth(TOOL_CELL)
            return button
        button = QToolButton(self.segments)
        button.setObjectName("segmentButton")
        button.setText(text)
        return button

    def rebuild(self) -> None:
        for button in self.buttons.buttons():
            self.buttons.removeButton(button)
            self.row.removeWidget(button)
            button.deleteLater()
        for index in range(self.count()):
            text = self.itemText(index)
            button = self.make_button(self.label(text))
            button.setCheckable(True)
            button.setEnabled(bool(self.model().flags(self.model().index(index, 0)) & Qt.ItemFlag.ItemIsEnabled))
            button.setToolTip(self.itemData(index, Qt.ItemDataRole.ToolTipRole) or text)
            if text in self.icons:
                button.setIcon(canvas_icon(self.icons[text]))
                button.setIconSize(ICON_SIZE)
                button.setText("")
                button.setAccessibleName(text)
                if self.canvas:
                    button.setFixedWidth(TOOL_CELL)
            self.buttons.addButton(button, index)
            self.row.addWidget(button)
        self.sync()

    def relabel(self) -> None:
        """Follow changed item texts (counts) without rebuilding the buttons."""
        for button in self.buttons.buttons():
            index = self.buttons.id(button)
            button.setText(self.label(self.itemText(index)))
            tip = self.itemData(index, Qt.ItemDataRole.ToolTipRole)
            if tip:
                button.setToolTip(tip)

    def sync(self, *_args) -> None:
        for button in self.buttons.buttons():
            button.setChecked(self.buttons.id(button) == self.currentIndex())


def icon_button(parent: QWidget, icon: str, name: str, tip: str, *, checkable: bool = False) -> QPushButton:
    button = QPushButton(parent)
    button.setObjectName("tomogramIconButton")
    button.setIcon(canvas_icon(icon))
    button.setIconSize(ICON_SIZE)
    button.setAccessibleName(name)
    button.setToolTip(tip)
    button.setCheckable(checkable)
    return button


def glyph_button(parent: QWidget, glyph: str, name: str, tip: str = "") -> QPushButton:
    """A one-character cell: − and + of a stepper, or ?."""
    button = QPushButton(glyph, parent)
    button.setObjectName("tomogramIconButton")
    button.setAccessibleName(name)
    button.setToolTip(tip)
    return button


class ToolGroup(QFrame):
    """Related canvas controls as one bordered group of equal-height cells.

    The group stays on one line when the toolbar wraps. ``None`` among the
    widgets draws a hairline between cells.
    """

    def __init__(self, parent: QWidget, *widgets: QWidget | None) -> None:
        super().__init__(parent)
        self.setObjectName("tomogramToolGroup")
        self.setFixedHeight(TOOL_CELL + 4)
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(1, 1, 1, 1)
        self.row.setSpacing(0)
        for widget in widgets:
            if widget is None:
                hair = QFrame(self)
                hair.setObjectName("tomogramToolHair")
                hair.setFixedSize(1, 14)
                self.row.addSpacing(2)
                self.row.addWidget(hair, 0, Qt.AlignmentFlag.AlignVCenter)
                self.row.addSpacing(2)
                continue
            # Cells fill the group's height: an alignment would size them to
            # their hint, and QSS min-height replaces a code-set minimum.
            widget.setFixedHeight(TOOL_CELL)
            widget.setMinimumWidth(TOOL_CELL)
            if widget.objectName() == "tomogramIconButton":
                widget.setFixedWidth(TOOL_CELL)
            self.row.addWidget(widget)

    def refresh_visibility(self) -> None:
        self.row.invalidate()
        self.setVisible(any(self.row.itemAt(index).widget() is not None
                            and self.row.itemAt(index).widget().objectName() != 'tomogramToolHair'
                            and not self.row.itemAt(index).widget().isHidden()
                            for index in range(self.row.count())))
        self.updateGeometry()


class IndicatorButton(QPushButton):
    """A toolbar button that can carry a small accent dot, such as Image while filters are on."""

    def __init__(self, text: str, parent: QWidget) -> None:
        super().__init__(text, parent)
        self.indicated = False

    def set_indicated(self, indicated: bool) -> None:
        if indicated != self.indicated:
            self.indicated = indicated
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().paintEvent(event)
        if not self.indicated:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(CANVAS_CHROME.accent))
        painter.drawEllipse(QRectF(self.width() - 9, 3, 6, 6))
        painter.end()


class CanvasToolbar(QWidget):
    """A fixed-height row, with one ordered overflow policy for its controls."""

    resized = Signal()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if event.oldSize().width() != event.size().width():
            self.resized.emit()

    @staticmethod
    def apply_level(tab, level: int) -> None:
        for button, text, from_level in ((tab.focus_button, "Focus", 1), (tab.image_button, "Image", 2)):
            icon_only = level >= from_level
            button.setText("" if icon_only else text)
            button.setMinimumWidth(TOOL_CELL)
            button.setMaximumWidth(TOOL_CELL if icon_only else UNBOUNDED)
        tab.slices_label.setVisible(level < 3)
        tab.slices_icon.setVisible(level >= 3)
        # Last: the zoom menu, wheel and -/= keys remain without − / +.
        tab.zoom_out_button.setVisible(level < 4)
        tab.zoom_in_button.setVisible(level < 4)

    def needed_width(self) -> int:
        """One row's width from the controls' current hints (not the layout's cached ones)."""
        row = self.layout()
        assert isinstance(row, QHBoxLayout)
        widths = [row.itemAt(index).widget().sizeHint().width() for index in range(row.count())
                  if row.itemAt(index).widget() is not None and not row.itemAt(index).widget().isHidden()]
        return sum(widths) + row.spacing() * max(0, len(widths) - 1) + 16

    def compact(self, tab) -> None:
        """Compact labels, then move the least-needed controls into More."""
        row = self.layout()
        if not hasattr(self, 'overflow_controls') or self.width() <= 0 or getattr(self, '_fitting', False):
            return
        key = (self.width(), tab.state.layout)
        if key == getattr(self, "fitted", None):
            return
        self.fitted = key
        self._fitting = True
        try:
            self.more.hide()
            self.hidden_controls = []
            for control, _label in self.overflow_controls:
                control.show()
            for group in self.groups:
                group.show()
            tab.ortho_group.setVisible(tab.state.layout == 'Ortho')
            for level in range(COMPACTEST + 1):
                self.apply_level(tab, level)
                self.more.setVisible(level == COMPACTEST)
                row.invalidate()
                if self.needed_width() <= self.width():
                    break
            if self.needed_width() > self.width():
                self.more.show()
                for control, label in self.overflow_controls:
                    if control.isHidden() or (control in (tab.link_button, tab.lines_button) and tab.state.layout != 'Ortho'):
                        continue
                    control.hide()
                    if control is tab.slices_control:
                        tab.slices_icon.hide()
                    self.hidden_controls.append((control, label))
                    for group in self.groups:
                        if group is not tab.ortho_group or tab.state.layout == 'Ortho':
                            group.refresh_visibility()
                    row.invalidate()
                    if self.needed_width() <= self.width():
                        break
            # Level compaction also removes zoom buttons; keep their actions reachable.
            for control in (tab.zoom_out_button, tab.zoom_in_button):
                if control.isHidden() and all(item[0] is not control for item in self.hidden_controls):
                    self.hidden_controls.append((control, control.accessibleName()))
            self.more.setVisible(bool(self.hidden_controls))
            row.invalidate()
        finally:
            self._fitting = False

    def populate_more(self) -> None:
        self.more_menu.clear()
        for control, label in self.hidden_controls:
            if isinstance(control, QSpinBox):
                menu = self.more_menu.addMenu(label)
                editor = QSpinBox(menu)
                editor.setRange(control.minimum(), control.maximum())
                editor.setSingleStep(control.singleStep())
                editor.setValue(control.value())
                editor.setKeyboardTracking(False)
                editor.setAccessibleName(control.accessibleName())
                def apply_slices(value, original=control, field=editor):
                    original.setValue(value)
                    # The canonical control normalises even input to an odd slab.
                    # Echo only its accepted value, without a two-way signal loop.
                    with QSignalBlocker(field):
                        field.setValue(original.value())
                editor.valueChanged.connect(apply_slices)
                action = QWidgetAction(menu)
                action.setDefaultWidget(editor)
                menu.addAction(action)
                continue
            submenu = control.menu() if isinstance(control, QPushButton) else None
            if submenu is not None:
                submenu.setTitle(label)
                self.more_menu.addMenu(submenu)
                continue
            action = self.more_menu.addAction(label)
            action.setEnabled(control.isEnabled())
            action.setCheckable(control.isCheckable())
            action.setChecked(control.isChecked())
            action.triggered.connect(lambda _checked=False, button=control: button.click())


def build_toolbar(tab) -> CanvasToolbar:
    toolbar = CanvasToolbar(tab)
    toolbar.setObjectName("tomogramCanvasToolbar")
    toolbar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    toolbar.setFixedHeight(TOOL_CELL + 12)
    toolbar.setMinimumWidth(0)
    toolbar.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
    row = QHBoxLayout(toolbar)
    row.setContentsMargins(4, 4, 4, 4)
    row.setSpacing(SPACE_S)
    tab.layout_choice = Versions(toolbar, canvas=True, icons={"Single": "layout-single", "Ortho": "layout-ortho"})
    tab.layout_choice.addItems(("Single", "Ortho"))
    tab.layout_choice.setItemData(0, "Show one plane (O)", Qt.ItemDataRole.ToolTipRole)
    tab.layout_choice.setItemData(1, "Show the XY, XZ and YZ planes together (O)", Qt.ItemDataRole.ToolTipRole)
    tab.layout_choice.rebuild()
    tab.layout_choice.hide()
    tab.layout_choice.setAccessibleName("Plane layout")
    tab.layout_choice.currentTextChanged.connect(tab.change_layout)
    row.addWidget(tab.layout_choice.segments)
    tab.oblique = ObliqueControls(tab, toolbar)
    row.addWidget(ToolGroup(toolbar, tab.oblique.lock, None, tab.oblique.angles))
    tab.link_button = icon_button(toolbar, "link", "Link Ortho panes",
        "Keep the three planes at the same point and scale. Set to off to move and zoom each plane "
        "independently.", checkable=True)
    tab.link_button.setChecked(True)
    tab.link_button.toggled.connect(tab.toggle_linked)
    tab.lines_button = icon_button(toolbar, "crosshair", "Ortho intersection lines",
        "Show the intersection lines of the planes (L). Snapshots do not include these lines.", checkable=True)
    tab.lines_button.setChecked(True)
    tab.lines_button.toggled.connect(tab.toggle_lines)
    # The stage shows this group only in Ortho.
    tab.ortho_group = ToolGroup(toolbar, tab.link_button, tab.lines_button)
    tab.ortho_group.setVisible(tab.state.layout == "Ortho")
    row.addWidget(tab.ortho_group)
    slices_label = tab.slices_label = QLabel("Slices", toolbar)
    slices_label.setObjectName("tomogramToolbarLabel")
    tab.slices_icon = QLabel(toolbar)
    tab.slices_icon.setPixmap(canvas_icon("layers").pixmap(ICON_SIZE))
    tab.slices_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
    tab.slices_icon.setAccessibleName("Slices")
    tab.slices_icon.hide()
    tab.slices_control = QSpinBox(toolbar)
    tab.slices_control.setRange(1, 101)
    tab.slices_control.setSingleStep(2)
    tab.slices_control.setKeyboardTracking(False)
    tab.slices_control.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
    tab.slices_control.setAlignment(Qt.AlignmentFlag.AlignCenter)
    tab.slices_control.setAccessibleName("Slices averaged")
    tab.slices_control.setToolTip("Show the average of this number of planes, to decrease noise. Type a value, scroll, "
                                  "or click − or +.")
    tab.slices_control.valueChanged.connect(tab.change_slices)
    fewer = glyph_button(toolbar, "−", "Fewer slices")
    fewer.clicked.connect(tab.slices_control.stepDown)
    more = glyph_button(toolbar, "+", "More slices")
    more.clicked.connect(tab.slices_control.stepUp)
    slices = ToolGroup(toolbar, slices_label, tab.slices_icon, fewer, tab.slices_control, more)
    tab.slices_control.setFixedWidth(32)
    slices.setToolTip(tab.slices_control.toolTip())
    row.addWidget(slices)
    row.addStretch()
    # The same − value + shape as Slices; Fit closes the group.
    zoom_out = tab.zoom_out_button = glyph_button(toolbar, "−", "Zoom out", "Zoom out (-)")
    zoom_out.clicked.connect(lambda: tab.zoom(1 / 1.2))
    # The value opens the presets; the navigator is a setting there, not a zoom level.
    tab.zoom_choice = ZoomValue("Zoom", toolbar)
    tab.zoom_choice.setObjectName("tomogramZoomValue")
    tab.zoom_choice.setAccessibleName("Zoom presets")
    tab.zoom_choice.setToolTip("The zoom. Click to select a different zoom.")
    zoom_menu = QMenu(tab)  # A popup follows the application theme, outside the canvas chrome.
    zoom_menu.addAction("Fit to window\tF", tab.fit)
    for zoom in (.25, .5, 1., 2., 4.):
        zoom_menu.addAction(f"{zoom:.0%}", lambda value=zoom: tab.set_zoom(value))
    zoom_menu.addSeparator()
    tab.navigator_action = zoom_menu.addAction("Navigator when zoomed in\tN")
    tab.navigator_action.setCheckable(True)
    tab.navigator_action.triggered.connect(tab.toggle_navigator)
    zoom_menu.aboutToShow.connect(lambda: tab.navigator_action.setChecked(tab.settings.tomogram_navigator_visible))
    tab.zoom_choice.setMenu(zoom_menu)
    zoom_in = tab.zoom_in_button = glyph_button(toolbar, "+", "Zoom in", "Zoom in (=)")
    zoom_in.clicked.connect(lambda: tab.zoom(1.2))
    row.addWidget(ToolGroup(toolbar, zoom_out, tab.zoom_choice, zoom_in, None, tab.fit_button))
    # Snapshot is the review's main action, so it stays on the canvas; Image keeps its settings.
    tab.snapshot_button.setParent(toolbar)
    tab.snapshot_button.setText("")
    tab.snapshot_button.setObjectName("tomogramIconButton")
    tab.snapshot_button.setIcon(canvas_icon("camera"))
    tab.snapshot_button.setIconSize(ICON_SIZE)
    tab.snapshot_button.setAccessibleName("Snapshot")
    row.addWidget(ToolGroup(toolbar, tab.snapshot_button))
    help_button = glyph_button(toolbar, "?", "Tomogram controls and shortcuts", "Show the keys and mouse controls (?)")
    help_button.clicked.connect(tab.show_help)
    tab.focus_button = QPushButton("Focus", toolbar)
    tab.focus_button.setIcon(canvas_icon("maximize"))
    tab.focus_button.setIconSize(ICON_SIZE)
    tab.focus_button.setCheckable(True)
    tab.focus_button.setAccessibleName("Focus")
    tab.focus_button.setToolTip("Hide the other panels to make the image larger (Ctrl+M). Click again to show them.")
    tab.focus_button.toggled.connect(tab.change_focus)
    tab.image_button.setParent(toolbar)
    tab.image_button.setIcon(canvas_icon("panel-right"))
    tab.image_button.setIconSize(ICON_SIZE)
    tab.image_button.setAccessibleName("Image settings")
    row.addWidget(ToolGroup(toolbar, help_button, None, tab.focus_button, None, tab.image_button))
    toolbar.more = glyph_button(toolbar, '⋯', 'More canvas controls')
    toolbar.more_menu = QMenu(toolbar)
    toolbar.more.setMenu(toolbar.more_menu)
    toolbar.more_menu.aboutToShow.connect(toolbar.populate_more)
    row.addWidget(toolbar.more)
    toolbar.more.hide()
    toolbar.hidden_controls = []
    toolbar.groups = toolbar.findChildren(ToolGroup, options=Qt.FindChildOption.FindDirectChildrenOnly)
    toolbar.overflow_controls = [
        (tab.fit_button, 'Fit to window'), (tab.oblique.angles, 'Angles'),
        (fewer, 'Fewer slices'), (more, 'More slices'),
        (tab.zoom_out_button, 'Zoom out'), (tab.zoom_in_button, 'Zoom in'),
        (tab.oblique.lock, 'Lock rotation'), (tab.focus_button, 'Focus'),
        (help_button, 'Controls and shortcuts'), (tab.link_button, 'Link Ortho panes'),
        (tab.lines_button, 'Ortho intersection lines'),
        (tab.slices_control, 'Slices averaged'),
    ]
    # Reserve the widest numeric/checked forms, so accepting a frame cannot resize a cell.
    tab.oblique.lock.setFixedWidth(TOOL_CELL)
    tab.oblique.angles.setFixedWidth(tab.oblique.angles.fontMetrics().horizontalAdvance('X -180.0°  Y -180.0°  Z -180.0°') + 36)
    toolbar.apply_level(tab, COMPACTEST)
    return toolbar
