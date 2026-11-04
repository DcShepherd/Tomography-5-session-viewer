from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict


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


class MarkerMetadata(TypedDict, total=False):
    """What a marker records beyond its geometry, by the builder that wrote it.

    Every key is optional: each builder writes the ones it knows. Provenance
    keys are written for diagnosis and read by nothing; they are typed so a
    misspelt key fails the type check instead of reading as absent.
    """

    # Frame and provenance (every builder).
    image_size: tuple[int, int] | None
    coordinate_source: str
    stage_position: MarkerPoint | None
    raw_stage_position: MarkerPoint | None
    raw_image_position: MarkerPoint
    offset: tuple[float | None, float | None]
    radius_source: str
    source_image_size: tuple[int, int] | None
    source_pixel_size: float | None
    source_rect: MarkerBox
    alignment_transform_source: str
    alignment_rotation_rad: float
    alignment_translation: MarkerPoint
    alignment_rotation_origin: MarkerPoint
    atlas_overlay_transform: str
    atlas_overlay_rotation_anchor: MarkerPoint
    atlas_overlay_position_before_rotation: MarkerPoint
    legacy_overlay_transform: str
    overlay_transform_provenance: str
    global_reflection_before: MarkerPoint
    local_rotation_anchor: str
    local_rotation_before: MarkerPoint
    # What the marker stands for.
    item_name: str | None
    search_map_name: str
    search_tile_name: str
    search_map_marker_id: str
    search_map_tile_bounds: MarkerBox
    tile_index: int
    tile_count: int
    tile_grid: tuple[int, int]
    tile_layout_source: str
    batch_id: str | None
    batch_name: str
    batch_label: str | None
    batch_position_id: str
    batch_position_label: str | None
    #: The template XML the marker was read from.
    raw: Any
    area_name: str
    linked_area_name: str
    exposure_index: int | None
    exposure_type: str | None
    exposure_marker_id: str
    from_marker_id: str
    to_marker_id: str
    tilt_series_id: str
    inferred_from_stage: bool | None
    # Beam and camera field of view.
    camera_fov_source: str
    camera_fov_size_px: tuple[float, float]
    camera_fov_size_m: tuple[float, float]
    has_beam_diameter: bool
    filled_fallback: bool
    queued_position: bool
    queued_fov_fallback: bool
    queued_camera_source_batch_id: str | None
    queued_camera_fov_replacement: bool
    queued_beam_source_batch_id: str | None
    queued_beam_source: str | None
    # Batch labels.
    label_for_marker_id: str
    label_anchor: MarkerPoint
    label_source_geometry: MarkerBox
    label_placement: str
    label_gap_px: float
    label_font_size_px: float
    label_source_type: str
    label_colour_role: str
    # The Atlas level-of-detail overlay.
    atlas_lod_role: str
    status_counts: dict[str, int]
    planned_count: int
    acquired_count: int
    failed_count: int
    navigation_enabled: bool
    navigation_explanation: str
    navigation_overview_id: str | None
    member_ids: tuple[str, ...]
    member_bounds_scene: MarkerBox
    diameter_px: float
    atlas_label_placement: Any


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
    metadata: MarkerMetadata = field(default_factory=MarkerMetadata)


@dataclass(slots=True)
class SelectionState:
    selected_object_type: str | None = None
    selected_object_id: str | None = None
    selected_marker_id: str | None = None
    source: str | None = None
