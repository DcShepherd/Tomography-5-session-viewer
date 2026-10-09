"""Atlas, overview and search-map alignment transforms, the region markers
they place, and the 180 degree atlas overlay rotation."""
from __future__ import annotations

from dataclasses import dataclass
import math

from tomography_session_browser.domain.display_names import format_overview_display_name
from tomography_session_browser.domain.markers import (
    ImageMarker,
    MarkerType,
)
from tomography_session_browser.domain.models import (
    Atlas,
    Overview,
    SearchMap,
)
from tomography_session_browser.parsers.xml_parser import find_first
from tomography_session_browser.services.image_geometry import (
    ImageFrame,
    image_frame,
    rotate_point_180_around_anchor,
    stage_to_image,
)
from tomography_session_browser.services.markers.shapes import (
    bbox_intersects,
    marker_center_point,
    move_marker_center,
)
from tomography_session_browser.services.markers.context import LOGGER


@dataclass(frozen=True, slots=True)
class AlignmentTransform:
    rotation_rad: float
    translation: tuple[float, float]
    source: str


def rotate_atlas_overlay_markers_180(
    markers: list[ImageMarker],
    *,
    image_size: tuple[int, int],
    source_id: str,
) -> None:
    """Rotate atlas overlay geometry around the atlas image centre.

    Tomography 5 atlas child regions are first projected into atlas pixel
    space from metadata-derived stage coordinates. Empirical comparison
    against a reference atlas shows the rendered atlas overlay layer must
    then be rotated 180 degrees around the image centre. Keep this as a final
    atlas-frame display transform so parsers and service-stage coordinates
    remain in microscope units.
    """

    width, height = image_size
    anchor = (width / 2.0, height / 2.0)
    for marker in markers:
        before = marker_center_point(marker)
        if before is None:
            if marker.polygon:
                marker.polygon = [
                    rotate_point_180_around_anchor(x, y, anchor[0], anchor[1])
                    for x, y in marker.polygon
                ]
            continue
        after = rotate_point_180_around_anchor(before[0], before[1], anchor[0], anchor[1])
        move_marker_center(marker, after)
        if marker.polygon:
            marker.polygon = [
                rotate_point_180_around_anchor(x, y, anchor[0], anchor[1])
                for x, y in marker.polygon
            ]
        marker.metadata["atlas_overlay_transform"] = "rotate_180_about_image_center"
        marker.metadata["atlas_overlay_rotation_anchor"] = anchor
        marker.metadata["atlas_overlay_position_before_rotation"] = before
        LOGGER.debug(
            "Atlas overlay rotated 180 source=%s type=%s id=%s before=%s after=%s anchor=%s",
            source_id,
            marker.marker_type,
            marker.id,
            before,
            after,
            anchor,
        )


def atlas_alignment_transform(atlas: Atlas) -> AlignmentTransform | None:
    return _alignment_transform_from_metadata(
        atlas.metadata.get("Atlas.dm") if isinstance(atlas.metadata, dict) else None,
        "AtlasAlignmentTransformation",
        "AtlasAlignmentTransformation",
    )


def search_map_alignment_transform(search_map: SearchMap) -> AlignmentTransform | None:
    return _alignment_transform_from_metadata(
        search_map.metadata.get("SearchMap.dm") if isinstance(search_map.metadata, dict) else None,
        "BackwardAlignmentTransformation",
        "BackwardAlignmentTransformation",
    )


def overview_alignment_transform(
    overview: Overview,
    search_map_by_id: dict[str, SearchMap],
) -> AlignmentTransform | None:
    transform = _alignment_transform_from_metadata(
        overview.metadata.get("Overview.dm") if isinstance(overview.metadata, dict) else None,
        "BackwardAlignmentTransformation",
        "BackwardAlignmentTransformation",
    )
    if transform is not None:
        return transform
    for search_map_id in overview.linked_search_map_ids:
        search_map = search_map_by_id.get(search_map_id)
        if search_map is None:
            continue
        transform = search_map_alignment_transform(search_map)
        if transform is not None:
            return transform
    return None


def _alignment_transform_from_metadata(
    metadata: object,
    key: str,
    source: str,
) -> AlignmentTransform | None:
    if not isinstance(metadata, dict):
        return None
    transform = find_first(metadata, key)
    if not isinstance(transform, dict) or _is_nil_node(transform):
        return None
    rotation = _alignment_float(find_first(transform, "Rotation")) or 0.0
    translation = find_first(transform, "Translation")
    translation_x = 0.0
    translation_y = 0.0
    if isinstance(translation, dict) and not _is_nil_node(translation):
        translation_x = (
            _alignment_float(translation.get("width"))
            or _alignment_float(translation.get("Width"))
            or _alignment_float(translation.get("x"))
            or _alignment_float(translation.get("X"))
            or 0.0
        )
        translation_y = (
            _alignment_float(translation.get("height"))
            or _alignment_float(translation.get("Height"))
            or _alignment_float(translation.get("y"))
            or _alignment_float(translation.get("Y"))
            or 0.0
        )
    if abs(rotation) <= 1e-15 and abs(translation_x) <= 1e-15 and abs(translation_y) <= 1e-15:
        return None
    return AlignmentTransform(
        rotation_rad=rotation,
        translation=(translation_x, translation_y),
        source=source,
    )


def apply_alignment_transform(
    stage_position: tuple[float, float],
    transform: AlignmentTransform | None,
    *,
    origin: tuple[float, float] = (0.0, 0.0),
) -> tuple[float, float]:
    if transform is None:
        return stage_position
    cos_theta = math.cos(transform.rotation_rad)
    sin_theta = math.sin(transform.rotation_rad)
    x, y = stage_position
    origin_x, origin_y = origin
    relative_x = x - origin_x
    relative_y = y - origin_y
    tx, ty = transform.translation
    return (
        origin_x + (relative_x * cos_theta) - (relative_y * sin_theta) + tx,
        origin_y + (relative_x * sin_theta) + (relative_y * cos_theta) + ty,
    )


def _alignment_metadata(
    transform: AlignmentTransform | None,
    *,
    origin: tuple[float, float] | None = None,
) -> dict[str, object]:
    if transform is None:
        return {}
    metadata: dict[str, object] = {
        "alignment_transform_source": transform.source,
        "alignment_rotation_rad": transform.rotation_rad,
        "alignment_translation": transform.translation,
    }
    if origin is not None:
        metadata["alignment_rotation_origin"] = origin
    return metadata


def _alignment_float(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _is_nil_node(value: dict[str, object]) -> bool:
    attributes = value.get("_attributes")
    return isinstance(attributes, dict) and attributes.get("nil") == "true"


def overview_region_marker(
    source_id: str,
    target_frame: ImageFrame,
    overview: Overview,
    *,
    show_label: bool = True,
    allow_off_image: bool = False,
    alignment_transform: AlignmentTransform | None = None,
) -> ImageMarker | None:
    source_frame = image_frame(overview)
    if source_frame is None:
        LOGGER.debug("Skipping overview region without source frame source=%s overview=%s", source_id, overview.id)
        return None
    stage_position = apply_alignment_transform(
        source_frame.stage_position,
        alignment_transform,
        origin=target_frame.stage_position,
    )
    center_x, center_y = stage_to_image(target_frame, stage_position)
    source_width, source_height = source_frame.image_size
    width = (source_width * source_frame.pixel_size) / target_frame.pixel_size
    height = (source_height * source_frame.pixel_size) / target_frame.pixel_size
    bbox = (center_x - width / 2, center_y - height / 2, width, height)
    if not allow_off_image and not bbox_intersects(target_frame.image_size, bbox):
        LOGGER.debug("Skipping off-image overview region source=%s overview=%s bbox=%s image=%s", source_id, overview.id, bbox, target_frame.image_size)
        return None
    label = format_overview_display_name(overview.name)
    return ImageMarker(
        id=f"{source_id}:overview:{overview.id}",
        marker_type=MarkerType.OVERVIEW,
        linked_object_id=overview.id,
        source_object_id=source_id,
        bbox=bbox,
        label=label if show_label else None,
        tooltip=f"Overview: {label}",
        metadata={
            "image_size": target_frame.image_size,
            "coordinate_source": f"overview_stage_extent_to_{target_frame.source}",
            "stage_position": stage_position,
            "raw_stage_position": source_frame.stage_position,
            "source_image_size": source_frame.image_size,
            "source_pixel_size": source_frame.pixel_size,
            **_alignment_metadata(alignment_transform, origin=target_frame.stage_position),
        },
    )


def search_map_tile_markers(
    source_id: str,
    target_frame: ImageFrame,
    search_map: SearchMap,
    *,
    show_label: bool = True,
    allow_off_image: bool = False,
    alignment_transform: AlignmentTransform | None = None,
) -> list[ImageMarker]:
    region = _search_map_region_marker(
        source_id,
        target_frame,
        search_map,
        MarkerType.SEARCH_MAP,
        show_label=show_label,
        allow_off_image=allow_off_image,
        alignment_transform=alignment_transform,
    )
    if region is None or region.bbox is None:
        return []
    tile_count, tile_layout_source = _search_map_tile_count(search_map)
    if tile_count <= 1:
        LOGGER.debug("Search map tile layout unavailable; rendering single region source=%s search_map=%s", source_id, search_map.id)
        return [region]
    columns = max(1, round(tile_count ** 0.5))
    rows = max(1, (tile_count + columns - 1) // columns)
    x, y, width, height = region.bbox
    overlap = 0.08
    tile_width = width / (columns - ((columns - 1) * overlap))
    tile_height = height / (rows - ((rows - 1) * overlap))
    step_x = tile_width * (1 - overlap)
    step_y = tile_height * (1 - overlap)
    markers: list[ImageMarker] = []
    for index in range(tile_count):
        row = index // columns
        column = index % columns
        tile_bbox = (x + column * step_x, y + row * step_y, tile_width, tile_height)
        markers.append(
            ImageMarker(
                id=f"{source_id}:search-map:{search_map.id}:tile:{index}",
                marker_type=MarkerType.SEARCH_MAP,
                linked_object_id=search_map.id,
                source_object_id=source_id,
                bbox=tile_bbox,
                label=search_map.name if show_label and index == 0 else None,
                tooltip=f"Search map tile {index + 1}/{tile_count}: {search_map.name}",
                metadata={
                    **region.metadata,
                    "item_name": search_map.name,
                    "tile_index": index,
                    "tile_count": tile_count,
                    "tile_grid": (columns, rows),
                    "tile_layout_source": tile_layout_source,
                },
            )
        )
    return markers


def _search_map_tile_count(search_map: SearchMap) -> tuple[int, str]:
    """Return the unique acquired-tile count used for atlas grid overlays.

    ``SearchMap.tile_paths`` intentionally carries both MRC and JPG tile files
    so callers can choose the best display source. Counting that list directly
    doubles the reconstructed atlas grid for Tomography 5 sessions that save
    both formats. Prefer the one-XML-per-acquired-tile metadata list, then fall
    back to unique image stems for older/incomplete datasets.
    """

    metadata_count = len({path.stem for path in search_map.tile_metadata_paths})
    if metadata_count > 0:
        return metadata_count, "tile_metadata_paths_unique_stems_reconstructed"
    image_count = len({path.stem for path in search_map.tile_paths})
    if image_count > 0:
        return image_count, "tile_paths_unique_stems_reconstructed"
    return 1, "single_region"


def _search_map_region_marker(
    source_id: str,
    target_frame: ImageFrame,
    search_map: SearchMap,
    marker_type: str,
    *,
    show_label: bool = True,
    allow_off_image: bool = False,
    alignment_transform: AlignmentTransform | None = None,
) -> ImageMarker | None:
    source_frame = image_frame(search_map)
    if source_frame is None:
        LOGGER.debug("Skipping search-map region without source frame source=%s search_map=%s", source_id, search_map.id)
        return None
    stage_position = apply_alignment_transform(
        source_frame.stage_position,
        alignment_transform,
        origin=target_frame.stage_position,
    )
    center_x, center_y = stage_to_image(target_frame, stage_position)
    source_width, source_height = source_frame.image_size
    width = (source_width * source_frame.pixel_size) / target_frame.pixel_size
    height = (source_height * source_frame.pixel_size) / target_frame.pixel_size
    bbox = (center_x - width / 2, center_y - height / 2, width, height)
    if not allow_off_image and not bbox_intersects(target_frame.image_size, bbox):
        LOGGER.debug("Skipping off-image search-map region source=%s search_map=%s bbox=%s image=%s", source_id, search_map.id, bbox, target_frame.image_size)
        return None
    return ImageMarker(
        id=f"{source_id}:search-map:{search_map.id}",
        marker_type=marker_type,
        linked_object_id=search_map.id,
        source_object_id=source_id,
        bbox=bbox,
        label=search_map.name if show_label else None,
        tooltip=f"Search map: {search_map.name}",
        unresolved=False,
        metadata={
            "image_size": target_frame.image_size,
            "coordinate_source": f"search_map_stage_extent_to_{target_frame.source}",
            "item_name": search_map.name,
            "stage_position": stage_position,
            "raw_stage_position": source_frame.stage_position,
            "source_image_size": source_frame.image_size,
            "source_pixel_size": source_frame.pixel_size,
            **_alignment_metadata(alignment_transform, origin=target_frame.stage_position),
        },
    )
