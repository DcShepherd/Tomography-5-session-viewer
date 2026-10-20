"""Widget components for the timeline.

Auto shows lanes only when every lane has its own row and each row is at least
one name tall at the lanes' own height; otherwise it shows the density view.
It used to choose by series count alone, so 42 series over 20 batch positions
drew 12 crowded rows and a "+8 positions" row holding the last three hours.

Lane names are whole while every one fits the name gutter; otherwise all of
them drop the prefix they share (``Grid2_20260627_1`` to ``_20`` become ``1``
to ``20``), and ``lane_label_prefix_changed`` gives the prefix once.
"""
from __future__ import annotations
from collections import OrderedDict
from datetime import datetime
from typing import Any
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFontMetricsF, QPainter
from PySide6.QtWidgets import QMenu, QSizePolicy, QToolTip, QWidget
from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.presentation.design_tokens import RADIUS_CONTROL
from tomography_session_browser.presentation.formatting import shared_name_prefix
from tomography_session_browser.services.timeline_service import SessionTimeline, TimelineSegment
from tomography_session_browser.ui.theme import current_palette
from tomography_session_browser.ui.widgets.time_chart import TIME_CHART_TIMELINE_MIN_HEIGHT, format_absolute_time
from tomography_session_browser.ui.timeline.constants import AXIS_HEIGHT_PX, DENSITY_THRESHOLD_SEGMENTS, LANES_TOP_PX, MAX_GROUPED_LANES, MAX_LANE_LABEL_WIDTH_PX, MAX_UNGROUPED_LANES, OVERFLOW_LANE_KEY
from tomography_session_browser.ui.timeline.models import TimelineLane
from tomography_session_browser.ui.timeline.painter import TimelinePainter
from tomography_session_browser.ui.timeline.support import group_key, status_icon


class TimelineStrip(QWidget):
    segment_clicked = Signal(str, object)  # (tilt_series_id, 1-based frame_index | None)
    # The prefix the lane names drop, "" while they are whole or not drawn.
    lane_label_prefix_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._timeline: SessionTimeline | None = None
        self.painter = TimelinePainter(self)
        self._lanes: tuple[TimelineLane, ...] = ()
        self._segment_metadata: dict[str, Any] = {}
        self._display_mode = "auto"
        self._lane_prefix = ""
        self._highlighted_tilt_series_id: str | None = None
        self._keyboard_segment_index = 0
        self._segment_color_override: QColor | None = None
        self._pause_color_override: QColor | None = None
        self._track_color_override: QColor | None = None
        self._label_color_override: QColor | None = None
        self.setMinimumHeight(TIME_CHART_TIMELINE_MIN_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        # Match the Defocus and dose plot canvases: the dashboard card owns
        # the background, while this widget paints only axes, tracks, and data.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setObjectName("acquisitionTimeline")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Acquisition timeline")
        self.setAccessibleDescription(
            "Timeline of tilt series acquisition periods with status colouring and inferred pause markers."
        )

    # ----- API ---------------------------------------------------------------

    def set_timeline(self, timeline: SessionTimeline | None) -> None:
        self._timeline = timeline
        self._lanes = self._build_lanes(timeline)
        self._apply_height_hint()
        self._sync_lane_label_prefix()
        self.update()

    def set_segment_metadata(self, metadata: dict[str, Any] | None) -> None:
        self._segment_metadata = dict(metadata or {})
        self._lanes = self._build_lanes(self._timeline)
        self._apply_height_hint()
        self._sync_lane_label_prefix()
        self.update()

    def set_display_mode(self, mode: str) -> None:
        """Set timeline display mode: ``auto``, ``density`` or ``lanes``."""

        if mode not in {"auto", "density", "lanes"}:
            mode = "auto"
        self._display_mode = mode
        self._apply_height_hint()
        self._sync_lane_label_prefix()
        self.update()

    def display_mode(self) -> str:
        return self._display_mode

    def uses_density_view(self) -> bool:
        """Whether the strip shows the density view (chosen, or by Auto)."""

        return self._timeline is not None and self._use_density_view(self._timeline)

    def lanes_fit(self) -> bool:
        """Whether every lane has its own row, at least one name tall, at the
        height the lanes view asks for."""

        lanes = self._lanes
        if any(lane.key == OVERFLOW_LANE_KEY for lane in lanes):
            return False
        if not lanes:
            return True
        available = max(self._lanes_height_hint(len(lanes)) - AXIS_HEIGHT_PX - LANES_TOP_PX - 8, 32)
        gap, lane_height = self.painter.lane_rows(len(lanes), available)
        name_height = QFontMetricsF(self.painter.lane_label_font(len(lanes))).height()
        return lane_height + gap >= name_height

    def lane_label_prefix(self) -> str:
        """The prefix the drawn lane names leave out ("" when none is left out)."""

        return self._lane_prefix

    def lane_display_label(self, lane: TimelineLane) -> str:
        """A lane's name as drawn: without the shared prefix when one is left out."""

        prefix = self._lane_prefix
        if prefix and lane.key != OVERFLOW_LANE_KEY and lane.label.startswith(prefix):
            return lane.label[len(prefix):]
        return lane.label

    def _sync_lane_label_prefix(self) -> None:
        prefix = ""
        if self._lanes and not self.uses_density_view():
            named = [lane.label for lane in self._lanes if lane.key != OVERFLOW_LANE_KEY]
            metrics = self.fontMetrics()
            # The gutter keeps 6 px beside the longest name.
            if any(metrics.horizontalAdvance(label) + 6 > MAX_LANE_LABEL_WIDTH_PX for label in named):
                prefix = shared_name_prefix(named, min_remainder=1)
        if prefix != self._lane_prefix:
            self._lane_prefix = prefix
            self.lane_label_prefix_changed.emit(prefix)

    def set_highlighted_tilt_series_id(self, tilt_series_id: str | None) -> None:
        self._highlighted_tilt_series_id = tilt_series_id
        self.update()

    # ----- paint -------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt signature
        return self.painter.paint(event)


    # ----- tooltips ----------------------------------------------------------

    def event(self, event):  # noqa: N802 - Qt signature
        if event.type() == QEvent.Type.FontChange:
            self._apply_height_hint()
            self._sync_lane_label_prefix()
        if event.type() == event.Type.ToolTip and self._timeline is not None:
            pos = event.pos()
            label = self._tooltip_at(pos)
            if label:
                QToolTip.showText(event.globalPos(), label, self)
            else:
                QToolTip.hideText()
            event.accept()
            return True
        return super().event(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt signature
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            for rect, payload in self.painter.state.density_rects:
                if rect.contains(pos):
                    if self._activate_density_payload(payload, event.globalPosition().toPoint()):
                        event.accept()
                        return
            for rect, segment in self.painter.state.segment_rects:
                if rect.contains(pos):
                    self._emit_segment_clicked(segment)
                    event.accept()
                    return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt signature
        segments = [segment for _rect, segment in self.painter.state.segment_rects]
        if not segments:
            super().keyPressEvent(event)
            return
        key = event.key()
        if key in {Qt.Key.Key_Left, Qt.Key.Key_Right}:
            delta = -1 if key == Qt.Key.Key_Left else 1
            self._keyboard_segment_index = max(
                0,
                min(len(segments) - 1, self._keyboard_segment_index + delta),
            )
            segment = segments[self._keyboard_segment_index]
            self.setAccessibleDescription(
                f"{self._segment_tooltip(segment)} Press Enter to open this tilt series."
            )
            event.accept()
            return
        if key in {Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space}:
            self._emit_segment_clicked(segments[self._keyboard_segment_index])
            event.accept()
            return
        super().keyPressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt signature
        pos = event.position().toPoint()
        clickable = any(rect.contains(pos) for rect, _payload in self.painter.state.density_rects) or any(
            rect.contains(pos) for rect, _segment in self.painter.state.segment_rects
        )
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor) if clickable else QCursor(Qt.CursorShape.ArrowCursor))
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt signature
        self.unsetCursor()
        super().leaveEvent(event)

    def _activate_density_payload(self, payload: dict[str, Any], global_pos) -> bool:
        segments = list(payload.get("segments") or ())
        if not segments:
            return False
        if len(segments) == 1:
            self._emit_segment_clicked(segments[0])
            return True
        menu = QMenu(self)
        self._style_density_menu(menu)
        for segment in sorted(segments, key=lambda item: (item.start, item.label)):
            metadata = self._metadata_for(segment)
            status = str(getattr(metadata, "status", "") or "").lower()
            label = f"{segment.label} · {format_absolute_time(segment.start)}"
            if status:
                label += f" · {status}"
            action = menu.addAction(label)
            if hasattr(action, "setIcon"):
                action.setIcon(status_icon(status, self.devicePixelRatioF()))
            action.triggered.connect(lambda _checked=False, selected=segment: self._emit_segment_clicked(selected))
        menu.exec(global_pos)
        return True

    def _emit_segment_clicked(self, segment: TimelineSegment) -> None:
        self.segment_clicked.emit(segment.tilt_series_id, None)

    def _tooltip_at(self, pos) -> str | None:
        if self.painter.state.summary_tooltip_text and self.painter.state.summary_tooltip_rect.contains(pos):
            return self.painter.state.summary_tooltip_text
        for rect, payload in self.painter.state.density_rects:
            if rect.contains(pos):
                return str(payload.get("tooltip") or "")
        for rect, pause in self.painter.state.pause_rects:
            if rect.contains(pos):
                return pause.tooltip
        for rect, segment in self.painter.state.segment_rects:
            if rect.contains(pos):
                metadata = self._metadata_for(segment)
                tooltip = getattr(metadata, "tooltip", None) if metadata is not None else None
                if tooltip:
                    return str(tooltip)
                return self._segment_tooltip(segment)
        return None

    @staticmethod
    def _segment_tooltip(segment: TimelineSegment) -> str:
        tilts = len(segment.sample_times)
        frames_text = f"{tilts} tilt series frames" if tilts else "1 tilt series"
        return (
            f"{segment.label}\n"
            f"{format_absolute_time(segment.start)} → {format_absolute_time(segment.end)}\n"
            f"{frames_text}"
        )

    # ----- lane preparation --------------------------------------------------

    def _build_lanes(self, timeline: SessionTimeline | None) -> tuple[TimelineLane, ...]:
        if timeline is None or not timeline.segments:
            return ()
        segments = tuple(sorted(timeline.segments, key=lambda seg: (seg.start, seg.label)))
        metadata_grouped = self._lanes_from_segment_metadata(segments)
        if metadata_grouped is not None:
            return metadata_grouped
        if len(segments) <= MAX_UNGROUPED_LANES:
            return tuple(
                TimelineLane(
                    key=segment.tilt_series_id,
                    label=segment.label,
                    segments=(segment,),
                    grouped=False,
                )
                for segment in segments
            )

        grouped: "OrderedDict[str, list[TimelineSegment]]" = OrderedDict()
        labels: dict[str, str] = {}
        for segment in segments:
            key, label = group_key(segment.label)
            grouped.setdefault(key, []).append(segment)
            labels.setdefault(key, label)

        lanes = [
            TimelineLane(
                key=key,
                label=labels[key],
                segments=tuple(values),
                grouped=True,
            )
            for key, values in grouped.items()
        ]
        lanes.sort(key=lambda lane: min(segment.start for segment in lane.segments))

        if len(lanes) <= MAX_GROUPED_LANES:
            return tuple(lanes)

        visible = lanes[: MAX_GROUPED_LANES - 1]
        overflow_segments = tuple(segment for lane in lanes[MAX_GROUPED_LANES - 1 :] for segment in lane.segments)
        visible.append(
            TimelineLane(
                key=OVERFLOW_LANE_KEY,
                label=f"+{count_phrase(len(lanes) - len(visible), 'group')}",
                segments=overflow_segments,
                grouped=True,
            )
        )
        return tuple(visible)

    def _lanes_from_segment_metadata(
        self, segments: tuple[TimelineSegment, ...]
    ) -> tuple[TimelineLane, ...] | None:
        grouped: "OrderedDict[str, list[TimelineSegment]]" = OrderedDict()
        labels: dict[str, str] = {}
        has_metadata_lane = False
        for segment in segments:
            metadata = self._metadata_for(segment)
            key = getattr(metadata, "lane_key", "") if metadata is not None else ""
            label = getattr(metadata, "lane_label", "") if metadata is not None else ""
            if key:
                has_metadata_lane = True
            else:
                key = segment.tilt_series_id
                label = segment.label
            grouped.setdefault(str(key), []).append(segment)
            labels.setdefault(str(key), str(label or key))

        if not has_metadata_lane:
            return None

        lanes = [
            TimelineLane(
                key=key,
                label=labels[key],
                segments=tuple(values),
                grouped=True,
            )
            for key, values in grouped.items()
        ]
        lanes.sort(key=lambda lane: min(segment.start for segment in lane.segments))

        if len(lanes) <= MAX_GROUPED_LANES:
            return tuple(lanes)

        visible = lanes[: MAX_GROUPED_LANES - 1]
        overflow_segments = tuple(
            segment for lane in lanes[MAX_GROUPED_LANES - 1 :] for segment in lane.segments
        )
        visible.append(
            TimelineLane(
                key=OVERFLOW_LANE_KEY,
                label=f"+{count_phrase(len(lanes) - len(visible), 'position')}",
                segments=overflow_segments,
                grouped=True,
            )
        )
        return tuple(visible)

    def _apply_height_hint(self) -> None:
        if self._timeline is not None and self._use_density_view(self._timeline):
            self.setMinimumHeight(208)
            self.updateGeometry()
            return
        self.setMinimumHeight(self._lanes_height_hint(len(self._lanes)))
        self.updateGeometry()

    @staticmethod
    def _lanes_height_hint(lane_count: int) -> int:
        lane_count = max(lane_count, 1)
        if lane_count <= 1:
            return 170
        if lane_count <= 4:
            return 182
        if lane_count <= 8:
            return 204
        return 228

    def _use_density_view(self, timeline: SessionTimeline) -> bool:
        if self._display_mode == "density":
            return True
        if self._display_mode == "lanes":
            return False
        return len(timeline.segments) > DENSITY_THRESHOLD_SEGMENTS or not self.lanes_fit()


    def _metadata_for(self, segment: TimelineSegment) -> Any | None:
        return self._segment_metadata.get(segment.tilt_series_id)


    @staticmethod
    def _style_density_menu(menu: QMenu) -> None:
        theme = current_palette()
        if hasattr(menu, "setStyleSheet"):
            menu.setStyleSheet(
                "QMenu {"
                f"background: {theme.surface};"
                f"color: {theme.text};"
                f"border: 1px solid {theme.border_strong};"
                "padding: 6px;"
                "}"
                "QMenu::item {"
                "padding: 6px 14px 6px 10px;"
                f"border-radius: {RADIUS_CONTROL}px;"
                "background: transparent;"
                "}"
                "QMenu::item:selected {"
                f"background: {theme.selection};"
                f"color: {theme.selection_text};"
                "}"
                "QMenu::icon {"
                "padding-left: 4px;"
                "}"
            )
        if hasattr(menu, "setToolTipsVisible"):
            menu.setToolTipsVisible(True)


