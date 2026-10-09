"""Frame scrubber for the image viewer."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import QLabel, QSlider, QStyle, QStyleOptionSlider, QWidget
from tomography_session_browser.domain.models import TiltSeries
from tomography_session_browser.services.tilt_angle_service import explicit_stack_order_tilt_angles
from tomography_session_browser.ui.theme import current_palette
import logging
LOGGER = logging.getLogger("tomography_session_browser.ui.image_viewer")
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from tomography_session_browser.ui.viewer.viewer_tab import ViewerTab


class FrameSelectionSlider(QSlider):
    """Slider with frame ticks drawn above the groove for tilt-series browsing."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setObjectName("frameScrubber")
        self.setMinimum(0)
        self.setMinimumHeight(42)
        self.setTickPosition(QSlider.TickPosition.TicksAbove)
        self.setTickInterval(1)
        self.setAccessibleName("Tilt-series frame")
        self.setAccessibleDescription(
            "Select a tilt-series frame. Use the Left and Right arrow keys to move one frame."
        )

    def paintEvent(self, event) -> None:
        frame_count = self.maximum() - self.minimum() + 1
        if frame_count <= 1:
            super().paintEvent(event)
            return

        super().paintEvent(event)

        option = QStyleOptionSlider()
        self.initStyleOption(option)
        groove = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider,
            option,
            QStyle.SubControl.SC_SliderGroove,
            self,
        )
        if groove.width() <= 0:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        painter.setPen(QPen(QColor(current_palette().text_muted), 1))
        y_long = max(2, groove.top() - 5)
        y_short = max(2, y_long - 6)
        for x in self.frame_tick_positions():
            painter.drawLine(x, y_short, x, y_long)
        painter.end()

    def frame_tick_positions(self) -> list[int]:
        return [self._handle_center_x_for_value(value) for value in range(self.minimum(), self.maximum() + 1)]

    def _handle_center_x_for_value(self, value: int) -> int:
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        option.sliderPosition = value
        option.sliderValue = value
        handle = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider,
            option,
            QStyle.SubControl.SC_SliderHandle,
            self,
        )
        return handle.center().x()


class FrameScrubber:
    """Frame scrubber operations for one ViewerTab."""

    def __init__(self, view: ViewerTab) -> None:
        self.view = view

    def create_widgets(self, show_tilt_controls: bool) -> None:
        self.view.frame_label = QLabel("Frame", self.view)
        self.view.frame_label.setObjectName("frameLabel")
        self.view.frame_label.setToolTip("Current tilt series frame")
        self.view.slice_slider = FrameSelectionSlider(self.view)
        self.view.slice_slider.setToolTip("Select a tilt-series frame; use Left or Right to move one frame")
        self.view.slice_slider.setVisible(show_tilt_controls)
        self.view.frame_label.setVisible(False)
        self.view.slice_slider.setEnabled(False)
        self.view.slice_slider.setVisible(False)
        self.view.slice_slider.valueChanged.connect(self.view._slider_changed)
        if show_tilt_controls:
            self.view._install_frame_shortcuts()

    def install_frame_shortcuts(self) -> None:
        for key, step in ((Qt.Key.Key_Left, -1), (Qt.Key.Key_Right, 1)):
            shortcut = QShortcut(QKeySequence(key), self.view)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda step=step: self.view._step_frame(step))

    def step_frame(self, step: int) -> None:
        if self.view.slice_slider.isHidden() or not self.view.slice_slider.isEnabled():
            return
        next_value = max(self.view.slice_slider.minimum(), min(self.view.slice_slider.maximum(), self.view.slice_slider.value() + step))
        if next_value != self.view.slice_slider.value():
            self.view.slice_slider.setValue(next_value)

    def select_frame(self, frame_index: int) -> None:
        """Select a zero-based frame in the currently loaded stack, if any."""

        if self.view.parts.mrc_loading.current_path is None:
            return
        frame_count = self.view._known_frame_count_for_path(self.view.parts.mrc_loading.current_path)
        if frame_count <= 0:
            return
        clamped = max(0, min(int(frame_index), frame_count - 1))
        if self.view.slice_slider.isVisible() and self.view.slice_slider.isEnabled():
            if self.view.slice_slider.value() != clamped:
                self.view.slice_slider.setValue(clamped)
            return
        self.view._load_path(self.view.parts.mrc_loading.current_path, self.view.parts.mrc_loading.current_fallback, clamped)

    def slider_changed(self, value: int) -> None:
        self.pending_slice_index = value
        self.view._update_frame_label(self.view.slice_slider.maximum() + 1)
        self.view._notify_frame_changed(self.view.slice_slider.maximum() + 1)
        if self.view.parts.mrc_loading.current_path is not None:
            self.slider_debounce.start()

    def load_pending_slice(self) -> None:
        if self.view.parts.mrc_loading.current_path is not None:
            self.view._load_path(self.view.parts.mrc_loading.current_path, self.view.parts.mrc_loading.current_fallback, self.pending_slice_index)

    def set_slice_state(self, slice_count: int, slice_index: int, *, valid_stack: bool) -> None:
        show_slider = self.show_tilt_controls and valid_stack and slice_count > 1
        self.view.frame_label.setVisible(show_slider)
        self.view.slice_slider.setVisible(show_slider)
        self.view.slice_slider.blockSignals(True)
        self.view.slice_slider.setEnabled(show_slider)
        self.view.slice_slider.setMaximum(max(0, slice_count - 1))
        self.view.slice_slider.setValue(min(slice_index, max(0, slice_count - 1)))
        self.view.slice_slider.blockSignals(False)
        frame_number = min(slice_index, max(0, slice_count - 1)) + 1
        frame_tooltip = (
            f"Frame {frame_number} of {slice_count}; use Left or Right to move one frame"
            if show_slider
            else "Tilt-series frame control"
        )
        self.view.slice_slider.setToolTip(frame_tooltip)
        self.view.slice_slider.setAccessibleDescription(frame_tooltip)
        self.view._update_frame_label(slice_count)
        self.view._notify_frame_changed(slice_count)

    def frame_text(self, slice_count: int) -> str:
        if self.view.slice_slider.isHidden() or slice_count <= 1:
            return ""
        angle = self.tilt_angle_for(self.view.parts.tab_selection.current_value, self.view.slice_slider.value()) if self.view.parts.tab_selection.current_value is not None else None
        angle_text = f"    Tilt angle: {angle}" if angle else ""
        return f" Frame {self.view.slice_slider.value() + 1} of {slice_count}{angle_text}"

    def update_frame_label(self, slice_count: int) -> None:
        text = self.view._frame_text(slice_count)
        self.view.frame_label.setText(text.strip() if text else "Frame")
        # Beside the scrubber, so it only ever grows: a readout that narrowed
        # from "Frame 10" to "Frame 9" would slide the scrubber under the cursor.
        self.view.frame_label.setMinimumWidth(max(self.view.frame_label.minimumWidth(), self.view.frame_label.sizeHint().width()))

    def default_frame_index(self, value: Any, path: Path, requested_index: int) -> int:
        if not self.show_tilt_controls or not isinstance(value, TiltSeries) or path.suffix.lower() != ".mrc":
            return requested_index
        metadata = value.mrc_metadata
        frame_count = int(metadata.nz) if metadata is not None and metadata.nz else value.number_of_frames or len(value.sections)
        if frame_count <= 0:
            return requested_index
        if frame_count <= 1:
            return 0
        angles = self.view._reliable_stack_order_angles_for(value, frame_count)
        if angles:
            frame_index = min(range(len(angles)), key=lambda index: abs(angles[index]))
            LOGGER.debug(
                "Tilt default frame from explicit tilt metadata path=%s frame=%s angle=%s",
                path,
                frame_index,
                angles[frame_index],
            )
            return frame_index
        LOGGER.debug("Tilt default frame fallback path=%s frame=%s reason=%s", path, frame_count // 2, "explicit tilt metadata unavailable")
        return frame_count // 2

    def reliable_stack_order_angles_for(self, value: Any, frame_count: int) -> list[float] | None:
        if not isinstance(value, TiltSeries):
            return None
        angle_info = explicit_stack_order_tilt_angles(value, frame_count)
        if angle_info is not None:
            angles, _source = angle_info
            return angles
        return None

    def notify_frame_changed(self, slice_count: int) -> None:
        if (
            self.on_frame_changed is None
            or self.view.parts.tab_selection.current_value is None
            or self.view.slice_slider.isHidden()
            or slice_count <= 1
        ):
            return
        self.on_frame_changed(self.view.parts.tab_selection.current_value, self.view.slice_slider.value(), slice_count)

