"""Stacked acquisition timeline used by the Session dashboard.

The widget consumes ``SessionTimeline`` from ``services.timeline_service`` and
renders timestamped tilt-series acquisitions as compact stacked lanes. When a
scope has only a handful of acquisitions, each tilt series gets its own lane.
Large scopes are grouped by the stable name prefix (for example ``vellio`` or
``orali``) so the card stays readable instead of drawing dozens of tiny rows.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSizePolicy, QToolTip, QWidget

from tomography_session_browser.services.timeline_service import (
    InferredPause,
    SessionTimeline,
    TimelineSegment,
)
from tomography_session_browser.ui.widgets.time_chart import (
    TIME_CHART_LEFT_GUTTER,
    TIME_CHART_RIGHT_GUTTER,
    TIME_CHART_TIMELINE_MIN_HEIGHT,
    format_absolute_time,
    format_density_bin_duration,
    format_elapsed,
    format_elapsed_offset,
    format_time_axis_tick,
    time_axis_tick_count,
)


_MAX_UNGROUPED_LANES = 13
_MAX_GROUPED_LANES = 13
_DENSITY_THRESHOLD_SEGMENTS = 50
_DENSITY_BIN_TARGET = 72
_HIGHLIGHT_FADE_ALPHA_FACTOR = 0.38
_HIGHLIGHT_RING_ALPHA = 118
_HIGHLIGHT_RING_WIDTH = 1.0


def _count_phrase(count: int, singular: str, plural: str | None = None) -> str:
    noun = singular if count == 1 else (plural or f"{singular}s")
    return f"{count} {noun}"


@dataclass(frozen=True)
class _TimelineLane:
    key: str
    label: str
    segments: tuple[TimelineSegment, ...]
    grouped: bool


class TimelineStrip(QWidget):
    segment_clicked = Signal(str, object)  # (tilt_series_id, 1-based frame_index | None)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._timeline: SessionTimeline | None = None
        self._lanes: tuple[_TimelineLane, ...] = ()
        self._segment_rects: list[tuple[QRectF, TimelineSegment]] = []
        self._pause_rects: list[tuple[QRectF, InferredPause]] = []
        self._density_rects: list[tuple[QRectF, dict[str, Any]]] = []
        self._summary_tooltip_rect = QRectF()
        self._summary_tooltip_text = ""
        self._last_density_strip_rect = QRectF()
        self._last_density_y_axis_label_rect = QRectF()
        self._last_density_y_axis_label_font = QFont(self.font())
        self._segment_metadata: dict[str, Any] = {}
        self._display_mode = "auto"
        self._highlighted_tilt_series_id: str | None = None
        self._segment_color_override: QColor | None = None
        self._pause_color_override: QColor | None = None
        self._track_color_override: QColor | None = None
        self._label_color_override: QColor | None = None
        self.setMinimumHeight(TIME_CHART_TIMELINE_MIN_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setAccessibleName("Acquisition timeline")
        self.setAccessibleDescription(
            "Timeline of tilt series acquisition periods with status colouring and inferred pause markers."
        )

    # ----- API ---------------------------------------------------------------

    def set_timeline(self, timeline: SessionTimeline | None) -> None:
        self._timeline = timeline
        self._lanes = self._build_lanes(timeline)
        self._apply_height_hint()
        self.update()

    def set_segment_metadata(self, metadata: dict[str, Any] | None) -> None:
        self._segment_metadata = dict(metadata or {})
        self._lanes = self._build_lanes(self._timeline)
        self._apply_height_hint()
        self.update()

    def set_palette(self, *, segment: str, pause: str, track: str, label: str) -> None:
        self._segment_color_override = QColor(segment)
        self._pause_color_override = QColor(pause)
        self._track_color_override = QColor(track)
        self._label_color_override = QColor(label)
        self.update()

    def set_display_mode(self, mode: str) -> None:
        """Set timeline display mode: ``auto``, ``density`` or ``lanes``."""

        if mode not in {"auto", "density", "lanes"}:
            mode = "auto"
        self._display_mode = mode
        self._apply_height_hint()
        self.update()

    def display_mode(self) -> str:
        return self._display_mode

    def set_highlighted_tilt_series_id(self, tilt_series_id: str | None) -> None:
        self._highlighted_tilt_series_id = tilt_series_id
        self.update()

    # ----- paint -------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt signature
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._segment_rects = []
            self._pause_rects = []
            self._density_rects = []
            self._summary_tooltip_rect = QRectF()
            self._summary_tooltip_text = ""
            self._last_density_strip_rect = QRectF()
            self._last_density_y_axis_label_rect = QRectF()

            timeline = self._timeline
            if timeline is None or not timeline.segments or not self._lanes:
                self._draw_empty(painter)
                return

            start = timeline.start
            end = timeline.end
            if start is None or end is None or end <= start:
                self._draw_empty(painter)
                return

            duration = (end - start).total_seconds()
            if duration <= 0:
                self._draw_empty(painter)
                return

            if self._use_density_view(timeline):
                self._draw_density_view(painter, timeline, start, end, duration)
                return

            colors = self._colors()
            metrics = painter.fontMetrics()
            left_width = self._left_label_width(metrics)
            right_width = self._right_label_width(metrics)
            chart_left = max(TIME_CHART_LEFT_GUTTER, left_width + 12)
            chart_right = self.width() - max(TIME_CHART_RIGHT_GUTTER, right_width + 12)
            chart_width = max(chart_right - chart_left, 40)
            chart_right = chart_left + chart_width

            header_top = 4
            lane_top = 34
            axis_top = self.height() - 24
            if axis_top <= lane_top + 18:
                axis_top = lane_top + 18

            self._draw_summary(
                painter,
                timeline,
                start,
                end,
                QPointF(chart_left, header_top + metrics.ascent()),
                colors["label"],
            )
            self._draw_axis(
                painter,
                start,
                end,
                duration,
                chart_left,
                chart_width,
                lane_top,
                axis_top,
                colors,
            )
            self._draw_lanes(
                painter,
                timeline,
                start,
                duration,
                chart_left,
                chart_width,
                chart_right,
                lane_top,
                axis_top,
                colors,
            )
        finally:
            painter.end()

    def _draw_lanes(
        self,
        painter: QPainter,
        timeline: SessionTimeline,
        start: datetime,
        duration: float,
        chart_left: float,
        chart_width: float,
        chart_right: float,
        lane_top: float,
        axis_top: float,
        colors: dict[str, QColor],
    ) -> None:
        lanes = self._lanes
        lane_count = len(lanes)
        gap = 6 if lane_count <= 4 else 4 if lane_count <= 8 else 3
        available = max(axis_top - lane_top - 8, 32)
        lane_height = max(7.0, min(24.0, (available - gap * (lane_count - 1)) / lane_count))
        total_height = lane_height * lane_count + gap * (lane_count - 1)
        y = lane_top + max((available - total_height) / 2, 0)

        small_font = QFont(self.font())
        small_font.setPointSizeF(max(self.font().pointSizeF() - (1.0 if lane_count > 8 else 0.5), 7.5))
        painter.setFont(small_font)
        metrics = painter.fontMetrics()

        for lane in lanes:
            row_rect = QRectF(chart_left, y, chart_width, lane_height)

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colors["track"])
            painter.drawRoundedRect(row_rect, min(5, lane_height / 2), min(5, lane_height / 2))

            painter.setPen(colors["text"])
            label = metrics.elidedText(lane.label, Qt.TextElideMode.ElideRight, max(int(chart_left - 14), 20))
            painter.drawText(
                QRectF(0, y - 3, chart_left - 14, lane_height + 6),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                label,
            )

            count_text = self._lane_count_text(lane)
            painter.setPen(colors["label"])
            painter.drawText(
                QRectF(chart_right + 10, y - 3, self.width() - chart_right - 10, lane_height + 6),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                count_text,
            )

            if self._highlighted_tilt_series_id:
                highlighted_segments = tuple(
                    segment for segment in lane.segments if segment.tilt_series_id == self._highlighted_tilt_series_id
                )
                faded_segments = tuple(
                    segment for segment in lane.segments if segment.tilt_series_id != self._highlighted_tilt_series_id
                )
                self._draw_lane_segments(
                    painter,
                    faded_segments,
                    start,
                    duration,
                    chart_left,
                    chart_width,
                    y,
                    lane_height,
                    colors,
                    faded=True,
                )
                self._draw_lane_segments(
                    painter,
                    highlighted_segments,
                    start,
                    duration,
                    chart_left,
                    chart_width,
                    y,
                    lane_height,
                    colors,
                    faded=False,
                    emphasise=True,
                )
            else:
                self._draw_lane_segments(
                    painter,
                    lane.segments,
                    start,
                    duration,
                    chart_left,
                    chart_width,
                    y,
                    lane_height,
                    colors,
                    faded=False,
                )

            for pause in timeline.pauses:
                if not any(segment.tilt_series_id == pause.tilt_series_id for segment in lane.segments):
                    continue
                rect = self._segment_rect(
                    pause.start,
                    pause.end,
                    start,
                    duration,
                    chart_left,
                    chart_width,
                    y,
                    lane_height,
                    min_width=4.0,
                )
                fill = QColor(colors["pause"])
                fill.setAlpha(self._highlighted_alpha_for_id(pause.tilt_series_id, 185))
                painter.setBrush(fill)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(rect, min(4, lane_height / 2), min(4, lane_height / 2))
                self._pause_rects.append((rect, pause))

            y += lane_height + gap

    def _draw_lane_segments(
        self,
        painter: QPainter,
        segments: tuple[TimelineSegment, ...],
        start: datetime,
        duration: float,
        chart_left: float,
        chart_width: float,
        y: float,
        lane_height: float,
        colors: dict[str, QColor],
        *,
        faded: bool,
        emphasise: bool = False,
    ) -> None:
        if not segments:
            return
        if faded:
            self._draw_faded_lane_segments_layer(
                painter,
                segments,
                start,
                duration,
                chart_left,
                chart_width,
                y,
                lane_height,
                colors,
            )
            return
        painter.setPen(Qt.PenStyle.NoPen)
        for segment in segments:
            rect = self._segment_rect(
                segment.start,
                segment.end,
                start,
                duration,
                chart_left,
                chart_width,
                y,
                lane_height,
                min_width=3.0,
            )
            painter.setBrush(self._segment_status_color(segment, colors))
            painter.drawRoundedRect(rect, min(4, lane_height / 2), min(4, lane_height / 2))
            if emphasise:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(colors["highlight"], _HIGHLIGHT_RING_WIDTH))
                painter.drawRoundedRect(
                    rect.adjusted(-1, -1, 1, 1),
                    min(5, lane_height / 2),
                    min(5, lane_height / 2),
                )
                painter.setPen(Qt.PenStyle.NoPen)
            self._segment_rects.append((rect, segment))

    def _draw_faded_lane_segments_layer(
        self,
        painter: QPainter,
        segments: tuple[TimelineSegment, ...],
        start: datetime,
        duration: float,
        chart_left: float,
        chart_width: float,
        y: float,
        lane_height: float,
        colors: dict[str, QColor],
    ) -> None:
        layer_rect = QRectF(chart_left - 2, y - 2, chart_width + 4, lane_height + 4).toAlignedRect()
        if layer_rect.width() <= 0 or layer_rect.height() <= 0:
            return
        layer = QPixmap(layer_rect.size())
        layer.fill(Qt.GlobalColor.transparent)
        layer_painter = QPainter(layer)
        try:
            layer_painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            layer_painter.translate(-layer_rect.left(), -layer_rect.top())
            layer_painter.setPen(Qt.PenStyle.NoPen)
            for segment in segments:
                rect = self._segment_rect(
                    segment.start,
                    segment.end,
                    start,
                    duration,
                    chart_left,
                    chart_width,
                    y,
                    lane_height,
                    min_width=3.0,
                )
                layer_painter.setBrush(self._segment_status_color(segment, colors))
                layer_painter.drawRoundedRect(rect, min(4, lane_height / 2), min(4, lane_height / 2))
                self._segment_rects.append((rect, segment))
        finally:
            layer_painter.end()
        painter.save()
        try:
            painter.setOpacity(_HIGHLIGHT_FADE_ALPHA_FACTOR)
            painter.drawPixmap(layer_rect.topLeft(), layer)
        finally:
            painter.restore()

    def _draw_density_view(
        self,
        painter: QPainter,
        timeline: SessionTimeline,
        start: datetime,
        end: datetime,
        duration: float,
    ) -> None:
        colors = self._colors()
        metrics = painter.fontMetrics()
        line_height = metrics.height()
        outer_left = 0
        axis_width = TIME_CHART_LEFT_GUTTER
        chart_left = outer_left + axis_width
        chart_right = self.width() - TIME_CHART_RIGHT_GUTTER
        chart_width = max(chart_right - chart_left, 40)
        header_top = 4
        summary_y = header_top + metrics.ascent()
        strip_top = header_top + line_height + 12
        bottom_axis_space = line_height + 30
        strip_height = max(42, self.height() - strip_top - bottom_axis_space)
        axis_top = strip_top + strip_height + 18
        bin_count = max(12, min(_DENSITY_BIN_TARGET, int(chart_width / 9)))
        bins = self._density_bins(
            timeline,
            start,
            duration,
            bin_count,
        )
        bin_label = format_density_bin_duration(timedelta(seconds=duration / max(len(bins), 1)))

        self._draw_summary(
            painter,
            timeline,
            start,
            end,
            QPointF(chart_left, summary_y),
            colors["label"],
            tooltip=(
                "Mode: Density strip\n"
                "Entity: Tilt series\n"
                f"Bin size: {bin_label}\n"
                "Switch to Lanes for grouped detail."
            ),
        )
        max_count = max((entry["count"] for entry in bins), default=1)
        axis_max = self._density_axis_max(max_count)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colors["track"])
        strip_rect = QRectF(chart_left, strip_top, chart_width, strip_height)
        self._last_density_strip_rect = QRectF(strip_rect)
        painter.drawRoundedRect(strip_rect, 6, 6)
        self._draw_density_scale(painter, strip_rect, outer_left, axis_max, colors)

        bin_width = chart_width / max(len(bins), 1)
        for index, entry in enumerate(bins):
            if entry["count"] <= 0:
                continue
            x = chart_left + index * bin_width
            intensity = min(entry["count"] / axis_max, 1.0)
            height = max(6, strip_height * intensity)
            y = strip_top + strip_height - height
            rect = QRectF(x + 1, y, max(bin_width - 2, 2), height)
            self._draw_density_bin_parts(painter, rect, entry, colors)
            if self._highlighted_tilt_series_id and any(
                segment.tilt_series_id == self._highlighted_tilt_series_id
                for segment in entry.get("segments", ())
            ):
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(colors["highlight"], _HIGHLIGHT_RING_WIDTH))
                painter.drawRoundedRect(rect.adjusted(-1, -1, 1, 1), 4, 4)
                painter.setPen(Qt.PenStyle.NoPen)
            hit_rect = QRectF(rect)
            if hit_rect.width() < 8:
                pad = (8 - hit_rect.width()) / 2
                hit_rect.adjust(-pad, 0, pad, 0)
            self._density_rects.append((hit_rect, entry))

        self._draw_axis(
            painter,
            start,
            end,
            duration,
            chart_left,
            chart_width,
            strip_top,
            axis_top,
            colors,
        )

    def _draw_density_scale(
        self,
        painter: QPainter,
        strip_rect: QRectF,
        axis_left: float,
        axis_max: int,
        colors: dict[str, QColor],
    ) -> None:
        metrics = painter.fontMetrics()
        gutter_width = strip_rect.left() - axis_left - 10
        self._draw_density_y_axis_label(painter, strip_rect, axis_left, colors["label"])

        tick_values = [0, axis_max]
        if axis_max >= 4:
            tick_values.insert(1, axis_max // 2)
        seen: set[int] = set()
        for value in tick_values:
            if value in seen:
                continue
            seen.add(value)
            fraction = value / max(axis_max, 1)
            y = strip_rect.bottom() - strip_rect.height() * fraction
            painter.setPen(QPen(colors["grid"], 1))
            painter.drawLine(QPointF(strip_rect.left(), y), QPointF(strip_rect.right(), y))
            painter.setPen(colors["label"])
            text = str(value)
            painter.drawText(
                QRectF(axis_left, y - metrics.height() / 2, gutter_width, metrics.height()),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                text,
            )

    def _draw_density_y_axis_label(
        self,
        painter: QPainter,
        strip_rect: QRectF,
        axis_left: float,
        color: QColor,
    ) -> None:
        label = "tilt series/bin"
        font = QFont(self.font())
        metrics = painter.fontMetrics()
        self._last_density_y_axis_label_font = QFont(font)
        painter.save()
        try:
            painter.setPen(color)
            painter.setFont(font)
            # Match the Defocus Readout card: keep the rotated axis title in
            # the left gutter, with numeric tick labels closer to the plot.
            x = max(axis_left + 8, axis_left + (strip_rect.left() - axis_left - metrics.height()) * 0.18)
            label_width = metrics.horizontalAdvance(label)
            self._last_density_y_axis_label_rect = QRectF(
                x,
                strip_rect.center().y() - label_width / 2,
                metrics.height(),
                label_width,
            )
            painter.translate(x + metrics.height() / 2, strip_rect.center().y())
            painter.rotate(-90)
            painter.drawText(
                QRectF(-strip_rect.height() / 2, -metrics.height() / 2, strip_rect.height(), metrics.height()),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                label,
            )
        finally:
            painter.restore()

    def _density_bins(
        self,
        timeline: SessionTimeline,
        start: datetime,
        duration: float,
        bin_count: int,
    ) -> list[dict[str, Any]]:
        bins = [
            {
                "count": 0,
                "complete": 0,
                "failed": 0,
                "warning": 0,
                "unknown": 0,
                "labels": [],
                "failed_labels": [],
                "warning_labels": [],
                "unknown_labels": [],
                "segments": [],
            }
            for _ in range(bin_count)
        ]
        for segment in timeline.segments:
            midpoint = segment.start + (segment.end - segment.start) / 2
            fraction = (midpoint - start).total_seconds() / duration
            index = max(0, min(bin_count - 1, int(fraction * bin_count)))
            metadata = self._metadata_for(segment)
            status = str(getattr(metadata, "status", "") or "").lower()
            bins[index]["count"] += 1
            bins[index]["segments"].append(segment)
            if status in {"failed", "missing", "error"}:
                bins[index]["failed"] += 1
                if len(bins[index]["failed_labels"]) < 8:
                    bins[index]["failed_labels"].append(segment.label)
            elif status in {"warning", "incomplete"}:
                bins[index]["warning"] += 1
                if len(bins[index]["warning_labels"]) < 8:
                    bins[index]["warning_labels"].append(segment.label)
            elif status in {"neutral", "unknown", "queued"}:
                bins[index]["unknown"] += 1
                if len(bins[index]["unknown_labels"]) < 8:
                    bins[index]["unknown_labels"].append(segment.label)
            else:
                bins[index]["complete"] += 1
            if len(bins[index]["labels"]) < 6:
                bins[index]["labels"].append(segment.label)
        for index, entry in enumerate(bins):
            start_offset = duration * (index / bin_count)
            end_offset = duration * ((index + 1) / bin_count)
            count = int(entry["count"])
            pieces = [
                f"{count} acquisition{'s' if count != 1 else ''} in this time bin",
                f"{format_elapsed_offset(timedelta(seconds=start_offset))}–{format_elapsed_offset(timedelta(seconds=end_offset))}",
            ]
            if entry["failed"]:
                pieces.append(f"{entry['failed']} failed")
                pieces.append(f"Failed: {', '.join(entry['failed_labels'])}")
            if entry["warning"]:
                pieces.append(f"{entry['warning']} incomplete")
                pieces.append(f"Incomplete: {', '.join(entry['warning_labels'])}")
            if entry["unknown"]:
                pieces.append(f"{entry['unknown']} unknown")
                pieces.append(f"Unknown: {', '.join(entry['unknown_labels'])}")
            if entry["labels"]:
                pieces.append(", ".join(entry["labels"]))
            highlighted_label = self._highlighted_label_for_segments(entry["segments"])
            if highlighted_label:
                pieces.append(f"Highlighted: {highlighted_label}")
            if count == 1 and entry["segments"]:
                pieces.append(f"Click to inspect {entry['segments'][0].label}.")
            elif count > 1:
                pieces.append("Click to choose one to inspect.")
            entry["tooltip"] = "\n".join(pieces)
        return bins

    @staticmethod
    def _density_axis_max(max_count: int) -> int:
        """Nice count-axis maximum for density bars.

        Low-density sessions deliberately use a nonzero headroom value so one
        acquisition event does not fill the whole strip and imply high density.
        """

        if max_count <= 2:
            return 4
        magnitude = 1
        while magnitude * 10 <= max_count:
            magnitude *= 10
        for multiplier in (1, 2, 5, 10):
            candidate = multiplier * magnitude
            if candidate >= max_count:
                return max(candidate, 4)
        return max_count

    def _draw_density_bin_parts(
        self,
        painter: QPainter,
        rect: QRectF,
        entry: dict[str, Any],
        colors: dict[str, QColor],
    ) -> None:
        total = max(int(entry["count"]), 1)
        highlighted_status = self._highlight_status_for_segments(entry["segments"])
        parts = [
            ("complete", int(entry["complete"]), colors["segment"], entry["labels"]),
            ("warning", int(entry["warning"]), colors["warning"], entry["warning_labels"]),
            ("failed", int(entry["failed"]), colors["failed"], entry["failed_labels"]),
            ("unknown", int(entry["unknown"]), colors["neutral"], entry["unknown_labels"]),
        ]
        y_bottom = rect.bottom()
        for _status, count, color, _labels in parts:
            if count <= 0:
                continue
            part_height = rect.height() * (count / total)
            part = QRectF(rect.left(), y_bottom - part_height, rect.width(), part_height)
            painter.setBrush(self._density_part_color(color, _status, highlighted_status, entry["segments"]))
            painter.drawRoundedRect(part, 3, 3)
            y_bottom -= part_height

    def _draw_summary(
        self,
        painter: QPainter,
        timeline: SessionTimeline,
        start: datetime,
        end: datetime,
        origin: QPointF,
        color: QColor,
        *,
        tooltip: str | None = None,
    ) -> None:
        metrics = painter.fontMetrics()
        pieces = [
            format_elapsed(end - start),
            f"{len(timeline.segments)} acquisition{'s' if len(timeline.segments) != 1 else ''}",
        ]
        if timeline.pauses:
            pieces.append(f"{len(timeline.pauses)} inferred pause{'s' if len(timeline.pauses) != 1 else ''}")
        summary = " · ".join(pieces)
        range_text = f"{format_absolute_time(start)} → {format_absolute_time(end)}"
        text = f"{summary} · {range_text}"
        available = max(self.width() - int(origin.x()), 30)
        elided = metrics.elidedText(text, Qt.TextElideMode.ElideRight, available)
        painter.setPen(color)
        painter.drawText(origin, elided)
        if tooltip:
            text_width = min(metrics.horizontalAdvance(elided), available)
            self._summary_tooltip_rect = QRectF(
                origin.x(),
                origin.y() - metrics.ascent() - 2,
                max(text_width, 12),
                metrics.height() + 4,
            )
            self._summary_tooltip_text = tooltip

    def _draw_axis(
        self,
        painter: QPainter,
        start: datetime,
        end: datetime,
        duration: float,
        chart_left: float,
        chart_width: float,
        lane_top: float,
        axis_top: float,
        colors: dict[str, QColor],
    ) -> None:
        painter.setPen(QPen(colors["grid"], 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)

        tick_count = time_axis_tick_count(chart_width)
        metrics = painter.fontMetrics()
        for index in range(tick_count):
            fraction = index / (tick_count - 1)
            x = chart_left + chart_width * fraction
            painter.drawLine(QPointF(x, lane_top - 3), QPointF(x, axis_top - 5))
            tick = start + (end - start) * fraction
            label = format_time_axis_tick(tick, start, end)
            label_width = metrics.horizontalAdvance(label)
            text_x = x - label_width / 2
            if index == 0:
                text_x = chart_left
            elif index == tick_count - 1:
                text_x = chart_left + chart_width - label_width
            painter.setPen(colors["label"])
            painter.drawText(QPointF(text_x, axis_top + metrics.ascent()), label)
            painter.setPen(QPen(colors["grid"], 1))

    # ----- tooltips ----------------------------------------------------------

    def event(self, event):  # noqa: N802 - Qt signature
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
            for rect, payload in self._density_rects:
                if rect.contains(pos):
                    if self._activate_density_payload(payload, event.globalPosition().toPoint()):
                        event.accept()
                        return
            for rect, segment in self._segment_rects:
                if rect.contains(pos):
                    self._emit_segment_clicked(segment)
                    event.accept()
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt signature
        pos = event.position().toPoint()
        clickable = any(rect.contains(pos) for rect, _payload in self._density_rects) or any(
            rect.contains(pos) for rect, _segment in self._segment_rects
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
                action.setIcon(_status_icon(status))
            action.triggered.connect(lambda _checked=False, selected=segment: self._emit_segment_clicked(selected))
        menu.exec(global_pos)
        return True

    def _emit_segment_clicked(self, segment: TimelineSegment) -> None:
        self.segment_clicked.emit(segment.tilt_series_id, None)

    def _tooltip_at(self, pos) -> str | None:
        if self._summary_tooltip_text and self._summary_tooltip_rect.contains(pos):
            return self._summary_tooltip_text
        for rect, payload in self._density_rects:
            if rect.contains(pos):
                return str(payload.get("tooltip") or "")
        for rect, pause in self._pause_rects:
            if rect.contains(pos):
                return pause.tooltip
        for rect, segment in self._segment_rects:
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

    def _build_lanes(self, timeline: SessionTimeline | None) -> tuple[_TimelineLane, ...]:
        if timeline is None or not timeline.segments:
            return ()
        segments = tuple(sorted(timeline.segments, key=lambda seg: (seg.start, seg.label)))
        metadata_grouped = self._lanes_from_segment_metadata(segments)
        if metadata_grouped is not None:
            return metadata_grouped
        if len(segments) <= _MAX_UNGROUPED_LANES:
            return tuple(
                _TimelineLane(
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
            key, label = _group_key(segment.label)
            grouped.setdefault(key, []).append(segment)
            labels.setdefault(key, label)

        lanes = [
            _TimelineLane(
                key=key,
                label=labels[key],
                segments=tuple(values),
                grouped=True,
            )
            for key, values in grouped.items()
        ]
        lanes.sort(key=lambda lane: min(segment.start for segment in lane.segments))

        if len(lanes) <= _MAX_GROUPED_LANES:
            return tuple(lanes)

        visible = lanes[: _MAX_GROUPED_LANES - 1]
        overflow_segments = tuple(segment for lane in lanes[_MAX_GROUPED_LANES - 1 :] for segment in lane.segments)
        visible.append(
            _TimelineLane(
                key="other",
                label=f"+{len(lanes) - len(visible)} groups",
                segments=overflow_segments,
                grouped=True,
            )
        )
        return tuple(visible)

    def _lanes_from_segment_metadata(
        self, segments: tuple[TimelineSegment, ...]
    ) -> tuple[_TimelineLane, ...] | None:
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
            _TimelineLane(
                key=key,
                label=labels[key],
                segments=tuple(values),
                grouped=True,
            )
            for key, values in grouped.items()
        ]
        lanes.sort(key=lambda lane: min(segment.start for segment in lane.segments))

        if len(lanes) <= _MAX_GROUPED_LANES:
            return tuple(lanes)

        visible = lanes[: _MAX_GROUPED_LANES - 1]
        overflow_segments = tuple(
            segment for lane in lanes[_MAX_GROUPED_LANES - 1 :] for segment in lane.segments
        )
        visible.append(
            _TimelineLane(
                key="other",
                label=f"+{len(lanes) - len(visible)} positions",
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
        lane_count = max(len(self._lanes), 1)
        if lane_count <= 1:
            height = 170
        elif lane_count <= 4:
            height = 182
        elif lane_count <= 8:
            height = 204
        else:
            height = 228
        self.setMinimumHeight(height)
        self.updateGeometry()

    def _use_density_view(self, timeline: SessionTimeline) -> bool:
        if self._display_mode == "density":
            return True
        if self._display_mode == "lanes":
            return False
        return len(timeline.segments) > _DENSITY_THRESHOLD_SEGMENTS

    def _left_label_width(self, metrics) -> int:
        if not self._lanes:
            return 76
        longest = max(metrics.horizontalAdvance(lane.label) for lane in self._lanes)
        return min(max(longest + 6, 76), 150)

    def _right_label_width(self, metrics) -> int:
        if not self._lanes:
            return 64
        longest = max(metrics.horizontalAdvance(self._lane_count_text(lane)) for lane in self._lanes)
        return min(max(longest + 6, 54), 90)

    @staticmethod
    def _lane_count_text(lane: _TimelineLane) -> str:
        if lane.grouped or len(lane.segments) > 1:
            return _count_phrase(len(lane.segments), "tilt series", "tilt series")
        tilts = len(lane.segments[0].sample_times)
        return _count_phrase(tilts, "frame") if tilts else "1 tilt series"

    def _metadata_for(self, segment: TimelineSegment) -> Any | None:
        return self._segment_metadata.get(segment.tilt_series_id)

    def _segment_color(self, segment: TimelineSegment, colors: dict[str, QColor]) -> QColor:
        return self._with_highlight_alpha(segment, self._segment_status_color(segment, colors))

    def _segment_status_color(self, segment: TimelineSegment, colors: dict[str, QColor]) -> QColor:
        metadata = self._metadata_for(segment)
        status = str(getattr(metadata, "status", "") or "").lower()
        if status in {"failed", "missing", "error"}:
            return QColor(colors["failed"])
        if status in {"warning", "incomplete"}:
            return QColor(colors["warning"])
        if status in {"neutral", "unknown", "queued"}:
            return QColor(colors["neutral"])
        return QColor(colors["segment"])

    def _with_highlight_alpha(self, segment: TimelineSegment, color: QColor) -> QColor:
        value = QColor(color)
        value.setAlpha(self._highlighted_alpha_for_id(segment.tilt_series_id, value.alpha()))
        return value

    def _highlighted_alpha_for_id(self, tilt_series_id: str | None, base_alpha: int) -> int:
        if self._highlighted_tilt_series_id and tilt_series_id != self._highlighted_tilt_series_id:
            return max(45, int(base_alpha * _HIGHLIGHT_FADE_ALPHA_FACTOR))
        return base_alpha

    def _density_part_color(
        self,
        color: QColor,
        status: str,
        highlighted_status: str | None,
        segments: list[TimelineSegment],
    ) -> QColor:
        value = QColor(color)
        if not self._highlighted_tilt_series_id:
            return value
        if not any(segment.tilt_series_id == self._highlighted_tilt_series_id for segment in segments):
            value.setAlpha(max(45, int(value.alpha() * _HIGHLIGHT_FADE_ALPHA_FACTOR)))
            return value
        if highlighted_status is not None and status != highlighted_status:
            value.setAlpha(max(65, int(value.alpha() * 0.55)))
        return value

    def _highlight_status_for_segments(self, segments: list[TimelineSegment]) -> str | None:
        if not self._highlighted_tilt_series_id:
            return None
        for segment in segments:
            if segment.tilt_series_id == self._highlighted_tilt_series_id:
                return self._status_key_for_segment(segment)
        return None

    def _highlighted_label_for_segments(self, segments: list[TimelineSegment]) -> str | None:
        if not self._highlighted_tilt_series_id:
            return None
        for segment in segments:
            if segment.tilt_series_id == self._highlighted_tilt_series_id:
                return segment.label
        return None

    def _status_key_for_segment(self, segment: TimelineSegment) -> str:
        metadata = self._metadata_for(segment)
        status = str(getattr(metadata, "status", "") or "").lower()
        if status in {"failed", "missing", "error"}:
            return "failed"
        if status in {"warning", "incomplete"}:
            return "warning"
        if status in {"neutral", "unknown", "queued"}:
            return "unknown"
        return "complete"

    @staticmethod
    def _segment_rect(
        seg_start: datetime,
        seg_end: datetime,
        start: datetime,
        duration: float,
        left: float,
        width: float,
        top: float,
        height: float,
        *,
        min_width: float,
    ) -> QRectF:
        x_start = left + ((seg_start - start).total_seconds() / duration) * width
        x_end = left + ((seg_end - start).total_seconds() / duration) * width
        return QRectF(x_start, top, max(x_end - x_start, min_width), height)

    def _colors(self) -> dict[str, QColor]:
        from tomography_session_browser.ui.theme import current_palette

        theme = current_palette()
        track = QColor(self._track_color_override or theme.surface_hi)
        grid = QColor(theme.border_strong)
        grid.setAlpha(130)
        return {
            "segment": QColor(self._segment_color_override or theme.chart_green),
            "pause": QColor(self._pause_color_override or theme.chart_red),
            "failed": QColor(theme.chart_red),
            "warning": QColor(theme.chart_amber),
            "neutral": QColor(theme.chart_grey),
            "track": track,
            "label": QColor(self._label_color_override or theme.text_muted),
            "text": QColor(theme.text),
            "grid": grid,
            "highlight": self._highlight_color(theme.accent_strong),
        }

    @staticmethod
    def _highlight_color(color: str) -> QColor:
        value = QColor(color)
        value.setAlpha(_HIGHLIGHT_RING_ALPHA)
        return value

    @staticmethod
    def _style_density_menu(menu: QMenu) -> None:
        from tomography_session_browser.ui.theme import current_palette

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
                "border-radius: 4px;"
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

    def _draw_empty(self, painter: QPainter) -> None:
        colors = self._colors()
        painter.setPen(colors["label"])
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No timestamped acquisitions to plot.")


def _group_key(label: str) -> tuple[str, str]:
    text = label.strip()
    stem = re.sub(r"\.[^.]+$", "", text)
    match = re.match(r"([A-Za-z][A-Za-z .-]*?)(?:[_ -]?\d.*)?$", stem)
    if match:
        base = match.group(1).strip(" _-.")
    else:
        base = re.split(r"[_ -]\d", stem, maxsplit=1)[0].strip(" _-.")
    if not base:
        base = stem or text
    pretty = base.replace("_", " ").strip()
    if pretty.isupper():
        label_text = pretty
    else:
        label_text = " ".join(part.capitalize() if part.islower() else part for part in pretty.split())
    return pretty.lower(), label_text


def _status_icon(status: str) -> QIcon:
    from tomography_session_browser.ui.theme import current_palette

    theme = current_palette()
    normalized = (status or "").lower()
    if normalized in {"failed", "missing", "error"}:
        color = QColor(theme.chart_red)
    elif normalized in {"warning", "incomplete"}:
        color = QColor(theme.chart_amber)
    elif normalized in {"neutral", "unknown", "queued"}:
        color = QColor(theme.chart_grey)
    else:
        color = QColor(theme.chart_green)
    pixmap = QPixmap(12, 12)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(theme.border_strong), 1))
        painter.setBrush(color)
        painter.drawEllipse(2, 2, 8, 8)
    finally:
        painter.end()
    return QIcon(pixmap)
