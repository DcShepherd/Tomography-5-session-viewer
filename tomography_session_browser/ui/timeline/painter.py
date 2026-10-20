"""Painter components for the timeline."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil
from typing import Any
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from tomography_session_browser.services.timeline_service import InferredPause, SessionTimeline, TimelineSegment
from tomography_session_browser.ui.theme import current_palette
from tomography_session_browser.ui.widgets.time_chart import TIME_CHART_LEFT_GUTTER, TIME_CHART_RIGHT_GUTTER, format_absolute_time, format_density_bin_duration, format_elapsed, format_elapsed_offset, format_time_axis_tick, time_axis_tick_count
from dataclasses import field
from tomography_session_browser.ui.timeline.constants import AXIS_HEIGHT_PX, DENSITY_BIN_TARGET, HIGHLIGHT_FADE_ALPHA_FACTOR, HIGHLIGHT_RING_ALPHA, HIGHLIGHT_RING_WIDTH, LANES_TOP_PX, MAX_LANE_LABEL_WIDTH_PX
from tomography_session_browser.ui.timeline.models import TimelineLane
from tomography_session_browser.ui.timeline.support import timeline_count_phrase
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from tomography_session_browser.ui.timeline.widget import TimelineStrip


@dataclass
class TimelinePaintState:
    """Geometry from the last paint, shared with hit testing only."""

    last_density_y_axis_label_font: QFont
    segment_rects: list[tuple[QRectF, TimelineSegment]] = field(default_factory=list)
    pause_rects: list[tuple[QRectF, InferredPause]] = field(default_factory=list)
    density_rects: list[tuple[QRectF, dict[str, Any]]] = field(default_factory=list)
    summary_tooltip_rect: QRectF = field(default_factory=QRectF)
    summary_tooltip_text: str = ""
    last_density_strip_rect: QRectF = field(default_factory=QRectF)
    last_density_y_axis_label_rect: QRectF = field(default_factory=QRectF)


class TimelinePainter:
    """Render a timeline without owning its selection or input handlers."""

    def __init__(self, view: TimelineStrip) -> None:
        self.view = view
        self.state = TimelinePaintState(last_density_y_axis_label_font=QFont(view.font()))


    def paint(self, event) -> None:  # noqa: N802 - Qt signature
        painter = QPainter(self.view)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self.state.segment_rects = []
            self.state.pause_rects = []
            self.state.density_rects = []
            self.state.summary_tooltip_rect = QRectF()
            self.state.summary_tooltip_text = ""
            self.state.last_density_strip_rect = QRectF()
            self.state.last_density_y_axis_label_rect = QRectF()

            timeline = self.view._timeline
            if timeline is None or not timeline.segments or not self.view._lanes:
                self.draw_empty(painter)
                return

            start = timeline.start
            end = timeline.end
            if start is None or end is None or end <= start:
                self.draw_empty(painter)
                return

            duration = (end - start).total_seconds()
            if duration <= 0:
                self.draw_empty(painter)
                return

            if self.view._use_density_view(timeline):
                self.draw_density_view(painter, timeline, start, end, duration)
                return

            colors = self.colors()
            metrics = painter.fontMetrics()
            left_width = self.left_label_width(metrics)
            right_width = self.right_label_width(metrics)
            chart_left = max(TIME_CHART_LEFT_GUTTER, left_width + 12)
            chart_right = self.view.width() - max(TIME_CHART_RIGHT_GUTTER, right_width + 12)
            chart_width = max(chart_right - chart_left, 40)
            chart_right = chart_left + chart_width

            header_top = 4
            lane_top = LANES_TOP_PX
            axis_top = self.view.height() - AXIS_HEIGHT_PX
            if axis_top <= lane_top + 18:
                axis_top = lane_top + 18

            self.draw_summary(
                painter,
                timeline,
                start,
                end,
                QPointF(chart_left, header_top + metrics.ascent()),
                colors["label"],
            )
            self.draw_axis(
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
            self.draw_lanes(
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


    def draw_lanes(
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
        lanes = self.view._lanes
        lane_count = len(lanes)
        available = max(axis_top - lane_top - 8, 32)
        gap, lane_height = self.lane_rows(lane_count, available)
        total_height = lane_height * lane_count + gap * (lane_count - 1)
        y = lane_top + max((available - total_height) / 2, 0)

        painter.setFont(self.lane_label_font(lane_count))
        metrics = painter.fontMetrics()
        # Without the shared prefix, the tail is what tells lanes apart.
        elide = Qt.TextElideMode.ElideLeft if self.view.lane_label_prefix() else Qt.TextElideMode.ElideRight

        for lane in lanes:
            row_rect = QRectF(chart_left, y, chart_width, lane_height)

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colors["track"])
            painter.drawRoundedRect(row_rect, min(5, lane_height / 2), min(5, lane_height / 2))

            painter.setPen(colors["text"])
            label = metrics.elidedText(self.view.lane_display_label(lane), elide, max(int(chart_left - 14), 20))
            painter.drawText(
                QRectF(0, y - 3, chart_left - 14, lane_height + 6),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                label,
            )

            count_text = self.lane_count_text(lane)
            painter.setPen(colors["label"])
            painter.drawText(
                QRectF(chart_right + 10, y - 3, self.view.width() - chart_right - 10, lane_height + 6),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                count_text,
            )

            if self.view._highlighted_tilt_series_id:
                highlighted_segments = tuple(
                    segment for segment in lane.segments if segment.tilt_series_id == self.view._highlighted_tilt_series_id
                )
                faded_segments = tuple(
                    segment for segment in lane.segments if segment.tilt_series_id != self.view._highlighted_tilt_series_id
                )
                self.draw_lane_segments(
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
                self.draw_lane_segments(
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
                self.draw_lane_segments(
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
                rect = self.segment_rect(
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
                fill.setAlpha(self.highlighted_alpha_for_id(pause.tilt_series_id, 185))
                painter.setBrush(fill)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(rect, min(4, lane_height / 2), min(4, lane_height / 2))
                self.state.pause_rects.append((rect, pause))

            y += lane_height + gap


    def draw_lane_segments(
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
            self.draw_faded_lane_segments_layer(
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
            rect = self.segment_rect(
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
            painter.setBrush(self.segment_status_color(segment, colors))
            painter.drawRoundedRect(rect, min(4, lane_height / 2), min(4, lane_height / 2))
            if emphasise:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(colors["highlight"], HIGHLIGHT_RING_WIDTH))
                painter.drawRoundedRect(
                    rect.adjusted(-1, -1, 1, 1),
                    min(5, lane_height / 2),
                    min(5, lane_height / 2),
                )
                painter.setPen(Qt.PenStyle.NoPen)
            self.state.segment_rects.append((rect, segment))


    def draw_faded_lane_segments_layer(
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
        dpr = max(1.0, self.view.devicePixelRatioF())
        layer = QPixmap(
            ceil(layer_rect.width() * dpr),
            ceil(layer_rect.height() * dpr),
        )
        layer.setDevicePixelRatio(dpr)
        layer.fill(Qt.GlobalColor.transparent)
        layer_painter = QPainter(layer)
        try:
            layer_painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            layer_painter.translate(-layer_rect.left(), -layer_rect.top())
            layer_painter.setPen(Qt.PenStyle.NoPen)
            for segment in segments:
                rect = self.segment_rect(
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
                layer_painter.setBrush(self.segment_status_color(segment, colors))
                layer_painter.drawRoundedRect(rect, min(4, lane_height / 2), min(4, lane_height / 2))
                self.state.segment_rects.append((rect, segment))
        finally:
            layer_painter.end()
        painter.save()
        try:
            painter.setOpacity(HIGHLIGHT_FADE_ALPHA_FACTOR)
            painter.drawPixmap(layer_rect.topLeft(), layer)
        finally:
            painter.restore()


    def draw_density_view(
        self,
        painter: QPainter,
        timeline: SessionTimeline,
        start: datetime,
        end: datetime,
        duration: float,
    ) -> None:
        colors = self.colors()
        metrics = painter.fontMetrics()
        line_height = metrics.height()
        outer_left = 0
        axis_width = TIME_CHART_LEFT_GUTTER
        chart_left = outer_left + axis_width
        chart_right = self.view.width() - TIME_CHART_RIGHT_GUTTER
        chart_width = max(chart_right - chart_left, 40)
        header_top = 4
        summary_y = header_top + metrics.ascent()
        strip_top = header_top + line_height + 12
        bottom_axis_space = line_height + 30
        strip_height = max(42, self.view.height() - strip_top - bottom_axis_space)
        axis_top = strip_top + strip_height + 18
        bin_count = max(12, min(DENSITY_BIN_TARGET, int(chart_width / 9)))
        bins = self.density_bins(
            timeline,
            start,
            duration,
            bin_count,
        )
        bin_label = format_density_bin_duration(timedelta(seconds=duration / max(len(bins), 1)))

        self.draw_summary(
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
        axis_max = self.density_axis_max(max_count)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colors["track"])
        strip_rect = QRectF(chart_left, strip_top, chart_width, strip_height)
        self.state.last_density_strip_rect = QRectF(strip_rect)
        painter.drawRoundedRect(strip_rect, 6, 6)
        self.draw_density_scale(painter, strip_rect, outer_left, axis_max, colors)

        bin_width = chart_width / max(len(bins), 1)
        for index, entry in enumerate(bins):
            if entry["count"] <= 0:
                continue
            x = chart_left + index * bin_width
            intensity = min(entry["count"] / axis_max, 1.0)
            height = max(6, strip_height * intensity)
            y = strip_top + strip_height - height
            rect = QRectF(x + 1, y, max(bin_width - 2, 2), height)
            self.draw_density_bin_parts(painter, rect, entry, colors)
            if self.view._highlighted_tilt_series_id and any(
                segment.tilt_series_id == self.view._highlighted_tilt_series_id
                for segment in entry.get("segments", ())
            ):
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(colors["highlight"], HIGHLIGHT_RING_WIDTH))
                painter.drawRoundedRect(rect.adjusted(-1, -1, 1, 1), 4, 4)
                painter.setPen(Qt.PenStyle.NoPen)
            hit_rect = QRectF(rect)
            if hit_rect.width() < 8:
                pad = (8 - hit_rect.width()) / 2
                hit_rect.adjust(-pad, 0, pad, 0)
            self.state.density_rects.append((hit_rect, entry))

        self.draw_axis(
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


    def draw_density_scale(
        self,
        painter: QPainter,
        strip_rect: QRectF,
        axis_left: float,
        axis_max: int,
        colors: dict[str, QColor],
    ) -> None:
        metrics = painter.fontMetrics()
        gutter_width = strip_rect.left() - axis_left - 10
        self.draw_density_y_axis_label(painter, strip_rect, axis_left, colors["label"])

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


    def draw_density_y_axis_label(
        self,
        painter: QPainter,
        strip_rect: QRectF,
        axis_left: float,
        color: QColor,
    ) -> None:
        label = "tilt series/bin"
        font = QFont(self.view.font())
        metrics = painter.fontMetrics()
        self.state.last_density_y_axis_label_font = QFont(font)
        painter.save()
        try:
            painter.setPen(color)
            painter.setFont(font)
            # Match the Defocus Readout card: keep the rotated axis title in
            # the left gutter, with numeric tick labels closer to the plot.
            x = max(axis_left + 8, axis_left + (strip_rect.left() - axis_left - metrics.height()) * 0.18)
            label_width = metrics.horizontalAdvance(label)
            self.state.last_density_y_axis_label_rect = QRectF(
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


    def density_bins(
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
            metadata = self.view._metadata_for(segment)
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
            highlighted_label = self.highlighted_label_for_segments(entry["segments"])
            if highlighted_label:
                pieces.append(f"Highlighted: {highlighted_label}")
            if count == 1 and entry["segments"]:
                pieces.append(f"Click to inspect {entry['segments'][0].label}.")
            elif count > 1:
                pieces.append("Click to choose one to inspect.")
            entry["tooltip"] = "\n".join(pieces)
        return bins


    @staticmethod
    def density_axis_max(max_count: int) -> int:
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


    def draw_density_bin_parts(
        self,
        painter: QPainter,
        rect: QRectF,
        entry: dict[str, Any],
        colors: dict[str, QColor],
    ) -> None:
        total = max(int(entry["count"]), 1)
        highlighted_status = self.highlight_status_for_segments(entry["segments"])
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
            painter.setBrush(self.density_part_color(color, _status, highlighted_status, entry["segments"]))
            painter.drawRoundedRect(part, 3, 3)
            y_bottom -= part_height


    def draw_summary(
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
        available = max(self.view.width() - int(origin.x()), 30)
        elided = metrics.elidedText(text, Qt.TextElideMode.ElideRight, available)
        painter.setPen(color)
        painter.drawText(origin, elided)
        if tooltip:
            text_width = min(metrics.horizontalAdvance(elided), available)
            self.state.summary_tooltip_rect = QRectF(
                origin.x(),
                origin.y() - metrics.ascent() - 2,
                max(text_width, 12),
                metrics.height() + 4,
            )
            self.state.summary_tooltip_text = tooltip


    def draw_axis(
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


    @staticmethod
    def lane_rows(lane_count: int, available: float) -> tuple[float, float]:
        """``(gap, row height)`` for ``lane_count`` lanes in ``available`` pixels."""

        gap = 6 if lane_count <= 4 else 4 if lane_count <= 8 else 3
        lane_height = max(7.0, min(24.0, (available - gap * (lane_count - 1)) / max(lane_count, 1)))
        return gap, lane_height


    def lane_label_font(self, lane_count: int) -> QFont:
        """The lane names' font: a little smaller than the widget's, more so past 8 lanes."""

        small_font = QFont(self.view.font())
        small_font.setPointSizeF(max(self.view.font().pointSizeF() - (1.0 if lane_count > 8 else 0.5), 7.5))
        return small_font


    def left_label_width(self, metrics) -> int:
        if not self.view._lanes:
            return 76
        longest = max(metrics.horizontalAdvance(self.view.lane_display_label(lane)) for lane in self.view._lanes)
        return min(max(longest + 6, 76), MAX_LANE_LABEL_WIDTH_PX)


    def right_label_width(self, metrics) -> int:
        if not self.view._lanes:
            return 64
        longest = max(metrics.horizontalAdvance(self.lane_count_text(lane)) for lane in self.view._lanes)
        return min(max(longest + 6, 54), 90)


    @staticmethod
    def lane_count_text(lane: TimelineLane) -> str:
        if lane.grouped or len(lane.segments) > 1:
            return timeline_count_phrase(len(lane.segments), "tilt series", "tilt series")
        tilts = len(lane.segments[0].sample_times)
        return timeline_count_phrase(tilts, "frame") if tilts else "1 tilt series"


    def segment_status_color(self, segment: TimelineSegment, colors: dict[str, QColor]) -> QColor:
        metadata = self.view._metadata_for(segment)
        status = str(getattr(metadata, "status", "") or "").lower()
        if status in {"failed", "missing", "error"}:
            return QColor(colors["failed"])
        if status in {"warning", "incomplete"}:
            return QColor(colors["warning"])
        if status in {"neutral", "unknown", "queued"}:
            return QColor(colors["neutral"])
        return QColor(colors["segment"])


    def highlighted_alpha_for_id(self, tilt_series_id: str | None, base_alpha: int) -> int:
        if self.view._highlighted_tilt_series_id and tilt_series_id != self.view._highlighted_tilt_series_id:
            return max(45, int(base_alpha * HIGHLIGHT_FADE_ALPHA_FACTOR))
        return base_alpha


    def density_part_color(
        self,
        color: QColor,
        status: str,
        highlighted_status: str | None,
        segments: list[TimelineSegment],
    ) -> QColor:
        value = QColor(color)
        if not self.view._highlighted_tilt_series_id:
            return value
        if not any(segment.tilt_series_id == self.view._highlighted_tilt_series_id for segment in segments):
            value.setAlpha(max(45, int(value.alpha() * HIGHLIGHT_FADE_ALPHA_FACTOR)))
            return value
        if highlighted_status is not None and status != highlighted_status:
            value.setAlpha(max(65, int(value.alpha() * 0.55)))
        return value


    def highlight_status_for_segments(self, segments: list[TimelineSegment]) -> str | None:
        if not self.view._highlighted_tilt_series_id:
            return None
        for segment in segments:
            if segment.tilt_series_id == self.view._highlighted_tilt_series_id:
                return self.status_key_for_segment(segment)
        return None


    def highlighted_label_for_segments(self, segments: list[TimelineSegment]) -> str | None:
        if not self.view._highlighted_tilt_series_id:
            return None
        for segment in segments:
            if segment.tilt_series_id == self.view._highlighted_tilt_series_id:
                return segment.label
        return None


    def status_key_for_segment(self, segment: TimelineSegment) -> str:
        metadata = self.view._metadata_for(segment)
        status = str(getattr(metadata, "status", "") or "").lower()
        if status in {"failed", "missing", "error"}:
            return "failed"
        if status in {"warning", "incomplete"}:
            return "warning"
        if status in {"neutral", "unknown", "queued"}:
            return "unknown"
        return "complete"


    @staticmethod
    def segment_rect(
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


    def colors(self) -> dict[str, QColor]:
        theme = current_palette()
        track = QColor(self.view._track_color_override or theme.surface_hi)
        grid = QColor(theme.border_strong)
        grid.setAlpha(130)
        return {
            "segment": QColor(self.view._segment_color_override or theme.chart_green),
            "pause": QColor(self.view._pause_color_override or theme.chart_red),
            "failed": QColor(theme.chart_red),
            "warning": QColor(theme.chart_amber),
            "neutral": QColor(theme.chart_grey),
            "track": track,
            "label": QColor(self.view._label_color_override or theme.text_muted),
            "text": QColor(theme.text),
            "grid": grid,
            "highlight": self.highlight_color(theme.accent_strong),
        }


    @staticmethod
    def highlight_color(color: str) -> QColor:
        value = QColor(color)
        value.setAlpha(HIGHLIGHT_RING_ALPHA)
        return value


    def draw_empty(self, painter: QPainter) -> None:
        colors = self.colors()
        painter.setPen(colors["label"])
        painter.drawText(self.view.rect(), Qt.AlignmentFlag.AlignCenter, "No timestamped acquisitions to plot.")

