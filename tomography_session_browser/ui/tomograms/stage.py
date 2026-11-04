"""Native splitter composition: large XY, XZ above, YZ to its right."""
from __future__ import annotations
from dataclasses import replace

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import QApplication, QSplitter, QVBoxLayout, QWidget
from tomography_session_browser.presentation import tomogram_geometry as geometry
from tomography_session_browser.services.volume.render import plane_geometry, plane_spacing
from tomography_session_browser.ui.tomograms.canvas import VolumeCanvas
from tomography_session_browser.ui.tomograms.commands import input_available
from tomography_session_browser.ui.tomograms.corner import IntersectionCorner


class VolumeStage(QWidget):
    def __init__(self, tab) -> None:
        super().__init__(tab)
        self.setObjectName("tomogramStage")
        self.tab = tab
        self.panes = {axis: VolumeCanvas(self) for axis in ("XY", "XZ", "YZ")}
        self.vertical = QSplitter(Qt.Orientation.Vertical, self)
        self.top = QSplitter(self)
        self.top.addWidget(self.panes["XZ"])
        self.corner = IntersectionCorner(tab, self)
        self.top.addWidget(self.corner)
        self.horizontal = QSplitter(self)
        self.horizontal.addWidget(self.panes["XY"])
        self.horizontal.addWidget(self.panes["YZ"])
        self.vertical.addWidget(self.top)
        self.vertical.addWidget(self.horizontal)
        for splitter in (self.vertical, self.horizontal, self.top):
            splitter.setChildrenCollapsible(False)
            splitter.setHandleWidth(6)
        self.vertical.setSizes((140, 520))
        self.horizontal.setSizes((700, 180))
        self.top.setSizes((700, 180))
        self.top.splitterMoved.connect(lambda *_args: self.horizontal.setSizes(self.top.sizes()))
        self.horizontal.splitterMoved.connect(lambda *_args: self.top.setSizes(self.horizontal.sizes()))
        self.vertical.splitterMoved.connect(self.remember_dividers)
        self.horizontal.splitterMoved.connect(self.remember_dividers)
        self.top.splitterMoved.connect(self.remember_dividers)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.vertical)
        for axis, pane in self.panes.items():
            pane.axis = axis
            pane.navigator_enabled = tab.settings.tomogram_navigator_visible
            pane.navigator_requested.connect(lambda x, y, axis=axis: self.recentre(axis, x, y))
            pane.navigator_dismissed.connect(self.hide_navigator)
            pane.setAccessibleName(f"Tomogram {axis} canvas")
            pane.setAccessibleDescription("Move into a pane to select it. Hold Shift and use the wheel to step planes. "
                                          "Pause playback and finish editing first.")
            pane.activated.connect(lambda axis=axis: tab.change_axis(axis))
            pane.hovered.connect(lambda axis=axis: self.hover(axis))
            pane.scrub_requested.connect(lambda event, axis=axis: self.scrub(axis, event))
            pane.intersection_requested.connect(lambda x, y, axis=axis: tab.move_intersection(axis, x, y))
            pane.viewport_changed.connect(lambda axis=axis: self.viewport_changed(axis))
            pane.zoom_requested.connect(lambda factor, axis=axis: self.zoom(axis, factor))
            pane.pan_requested.connect(lambda dx, dy, axis=axis: self.pan(axis, dx, dy))
            pane.cursor_moved.connect(lambda x, y, axis=axis: self.cursor(axis, x, y))
            pane.bar_moved.connect(self.move_bar)
        self.set_layout("Single")
        QApplication.instance().focusChanged.connect(self.reset_scrubs)

    def reset_scrubs(self, *_args) -> None:
        for pane in self.panes.values():
            pane.reset_scrub()

    def viewport_changed(self, axis: str) -> None:
        # Hidden splitter children can deliver deferred resize events while a
        # parent is grabbed/painted. Only visible panes contribute to the plan.
        if self.panes[axis].isVisible() and not self.tab.stage_transition.committing_layout:
            self.tab.request_render(view_only=True)

    def pointer_available(self) -> bool:
        # Preview/refinement is render work, not an input grab. Only an actual
        # manipulation or another control's input ownership blocks navigation.
        tab = self.tab
        return (tab.constructed and not tab.retired and tab.isVisible() and tab.slider.isEnabled()
                and tab.volume() is not None
                and not tab.playback.timer.isActive()
                and not QApplication.mouseButtons() and input_available(tab)
                and all(pane.drag is None and pane.bar_drag is None and not pane.rotating
                        and pane.orientation.drag is None for pane in self.panes.values()))

    def hover(self, axis: str) -> None:
        if self.tab.state.layout == "Ortho" and self.pointer_available():
            self.select(axis)

    def scrub(self, axis: str, event) -> None:
        pane = self.panes[axis]
        if not self.pointer_available():
            self.reset_scrubs()
            return
        self.select(axis)
        if event.angleDelta().isNull():
            pane.reset_scrub()  # Consume pixel-only gestures without inventing plane units.
        else:
            steps = pane.scrub_steps(event)
            if steps:
                self.tab.step_plane(steps)

    def select(self, axis: str) -> None:
        """One selection operation, independent of input policy and playback."""
        tab, volume = self.tab, self.tab.volume()
        if axis == tab.state.axis or volume is None or volume.grid is None:
            return
        focus = QApplication.focusWidget()
        focus_in_stage = focus is self or (focus is not None and self.isAncestorOf(focus))
        if tab.state.capture_region is not None:
            x0, y0, x1, y1 = tab.state.capture_region
            sx, sy = plane_spacing(geometry.spacing(volume, tab.state), tab.state.axis)
            scale = min(tab.canvas.width() / (x1-x0), tab.canvas.height() / ((y1-y0) * sy/sx))
            tab.state = geometry.set_view(tab.state, volume, min(100., scale), ((x0+x1)/2, (y0+y1)/2))
        tab.state = geometry.select_axis(tab.state, axis, volume)
        tab.slider.setAccessibleName("Requested " + geometry.normal_axis(axis) + " plane")
        tab.image_settings.update_filter_units()
        tab.cursor_label.clear()
        self.set_layout(tab.state.layout)
        with QSignalBlocker(tab.slider):
            tab.slider.setRange(0, plane_geometry(volume, axis)[2] - 1)
            tab.slider.setValue(tab.state.plane or 0)
        tab.request_render()
        if focus_in_stage:
            tab.canvas.setFocus(Qt.FocusReason.OtherFocusReason)

    def move_bar(self, x: float, y: float) -> None:
        from dataclasses import replace
        self.tab.recipes.change(scale_bar=replace(self.tab.state.scale_bar, position=(x, y)))

    def set_layout(self, mode: str) -> None:
        self.reset_scrubs()
        if hasattr(self.tab, "ortho_group"):
            self.tab.ortho_group.setVisible(mode == "Ortho")
        if hasattr(self.tab, "canvas_toolbar"):
            self.tab.canvas_toolbar.compact(self.tab)
        self.panes["XZ"].setVisible(mode == "Ortho")
        self.top.setVisible(mode == "Ortho")
        self.panes["YZ"].setVisible(mode == "Ortho")
        for axis, pane in self.panes.items():
            pane.hover_pending = True
            pane.active = axis == self.tab.state.axis
            pane.ortho = mode == "Ortho"
            pane.sync_navigator()
            pane.sync_orientation()
            pane.update()

    def change_layout(self, mode: str) -> None:
        """Commit the real layout once; presentation pictures do not drive geometry."""
        tab = self.tab
        tab.playback.stop()
        tab.state = replace(tab.state, layout=mode)
        with QSignalBlocker(tab.layout_choice):
            tab.layout_choice.setCurrentText(mode)
        tab.layout_choice.sync()
        if mode == "Single":
            tab.change_axis("XY")
        self.set_layout(mode)
        for splitter in (self.vertical, self.horizontal, self.top):
            splitter.refresh()
        tab.request_render()
        tab.focus_refit_requested.emit()

    def clear(self) -> None:
        for pane in self.panes.values():
            pane.clear()

    def set_annotations_visible(self, visible: bool) -> None:
        """Keep layout and displayed frame metadata intact while a picture dissolves."""
        for pane in self.panes.values():
            pane.annotations_visible = visible
            pane.sync_orientation()
            pane.sync_navigator()
            pane.update()
        for child in self.corner.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
            policy = child.sizePolicy()
            policy.setRetainSizeWhenHidden(True)
            child.setSizePolicy(policy)
            child.setVisible(visible)

    def remember_dividers(self, *_args) -> None:
        if self.tab.focus_button.isChecked():
            return
        from dataclasses import replace
        self.tab.state = replace(self.tab.state, dividers=tuple(self.vertical.sizes() + self.horizontal.sizes()))
        self.tab.persistence.schedule()

    def restore_dividers(self) -> None:
        sizes = self.tab.state.dividers
        self.apply_dividers(sizes or (140, 520, 700, 180))

    def apply_dividers(self, sizes: tuple[int, ...]) -> None:
        """Geometry application alone never publishes review state."""
        self.vertical.setSizes(sizes[:2])
        self.horizontal.setSizes(sizes[2:])
        self.top.setSizes(sizes[2:])

    def zoom(self, axis: str, factor: float) -> None:
        if axis == self.tab.state.axis:
            self.tab.zoom(factor)

    def pan(self, axis: str, dx: float, dy: float) -> None:
        if axis == self.tab.state.axis:
            self.tab.pan(dx, dy)

    def cursor(self, axis: str, x: float, y: float) -> None:
        if axis == self.tab.state.axis:
            self.tab.cursor_changed(x, y)

    def recentre(self, axis: str, x: float, y: float) -> None:
        from tomography_session_browser.presentation.tomogram_geometry import set_view
        tab, volume = self.tab, self.tab.volume()
        accepted = tab.rendering.accepted
        if axis != tab.state.axis or volume is None or accepted is None or accepted.job.generation != tab.rendering.generation:
            return
        tab.playback.stop()
        recipe = accepted.result.recipe
        scale = recipe.output_size[0] / (recipe.region[2]-recipe.region[0])
        tab.state = set_view(tab.state, volume, scale, (x, y))
        tab.request_render()

    def hide_navigator(self) -> None:
        self.tab.toggle_navigator(False)
        self.tab.canvas.setFocus(Qt.FocusReason.OtherFocusReason)
