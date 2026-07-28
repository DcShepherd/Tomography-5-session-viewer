from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


MarkerPoint = tuple[float, float]
MarkerBox = tuple[float, float, float, float]


class MarkerType:
    SEARCH_MAP = "search_map"
    OVERVIEW = "overview"
    BATCH_POSITION = "batch_position"
    BATCH_CLUSTER = "batch_cluster"
    TILT_SERIES = "tilt_series"
    TEMPLATE_AREA = "template_area"
    EXPOSURE_AREA = "exposure_area"
    CAMERA_FOV = "camera_fov"
    TRACKING_AREA = "tracking_area"
    FOCUS_AREA = "focus_area"
    CONDITION_AREA = "condition_area"
    BATCH_LABEL = "batch_label"
    LINK_LINE = "link_line"
    GROUPED = "grouped"
    STAGE_CROSSHAIR = "stage_crosshair"


@dataclass(slots=True)
class ImageMarker:
    id: str
    marker_type: str
    linked_object_id: str | None
    source_object_id: str | None
    x: float | None = None
    y: float | None = None
    radius: float | None = None
    bbox: MarkerBox | None = None
    polygon: list[MarkerPoint] | None = None
    label: str | None = None
    tooltip: str | None = None
    status: str | None = None
    visible: bool = True
    selected: bool = False
    unresolved: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SelectionState:
    selected_object_type: str | None = None
    selected_object_id: str | None = None
    selected_marker_id: str | None = None
    source: str | None = None
