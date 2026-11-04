"""The display-only Tomography 5 overlay corrections: grouped position
correction about each exposure group's central marker, and focus/tracking
orientation. Marker types corrected per frame are decided here."""
from __future__ import annotations

from tomography_session_browser.domain.coerce import as_str
from tomography_session_browser.domain.markers import (
    ImageMarker,
    MarkerType,
)
from tomography_session_browser.domain.models import BatchPosition
from tomography_session_browser.services.image_geometry import (
    ImageFrame,
    reflect_point_through_image_center,
    rotate_point_180_around_anchor,
)
from tomography_session_browser.services.markers.context import (
    CORRECTED_AREA_MARKER_TYPES,
    EXPERIMENTAL_OVERLAY_POSITION_CORRECTION,
    LOGGER,
)
from tomography_session_browser.services.markers.shapes import (
    marker_center_point,
    move_marker_center,
)


def corrected_area_types_for_frame(frame: ImageFrame) -> set[str]:
    if frame.source == "SearchMap":
        return set(CORRECTED_AREA_MARKER_TYPES)
    if frame.source == "Overview":
        # Tracking shares the focus/exposure stage geometry — both are point
        # offsets from the batch position recorded in the same coordinate
        # system. Excluding TRACKING_AREA from the reflection here left
        # tracking markers half a frame away from focus / exposure on the
        # Overview view (visible in datasets where Tracking and Focus are
        # configured at the same physical spot). ``TEMPLATE_AREA`` is in
        # the set for the same reason — its bounding box must move with
        # the batch's reflected exposures.
        return {
            MarkerType.EXPOSURE_AREA,
            MarkerType.FOCUS_AREA,
            MarkerType.TRACKING_AREA,
            MarkerType.TEMPLATE_AREA,
        }
    return set()


def apply_grouped_overlay_position_correction(
    markers: list[ImageMarker],
    *,
    image_size: tuple[int, int],
    corrected_types: set[str],
    panel_name: str,
    default_central_name: str | None = None,
) -> None:
    if not EXPERIMENTAL_OVERLAY_POSITION_CORRECTION or not corrected_types:
        return
    corrected_markers = [
        marker
        for marker in markers
        if marker.marker_type in corrected_types and marker_center_point(marker) is not None
    ]
    by_group: dict[str, list[ImageMarker]] = {}
    for marker in corrected_markers:
        by_group.setdefault(marker.marker_type, []).append(marker)
    for marker_group in by_group.values():
        _apply_grouped_marker_correction(marker_group, image_size, panel_name, default_central_name)


def _apply_grouped_marker_correction(
    markers: list[ImageMarker],
    image_size: tuple[int, int],
    panel_name: str,
    default_central_name: str | None,
) -> None:
    marker_type = markers[0].marker_type if markers else None
    apply_local_child_rotation = marker_type == MarkerType.EXPOSURE_AREA
    by_name: dict[str, ImageMarker] = {}
    for marker in markers:
        name = _overlay_group_name(marker, default_central_name)
        if name not in by_name:
            by_name[name] = marker
    for marker in markers:
        marker_name = _overlay_group_name(marker, default_central_name)
        current_center = marker_center_point(marker)
        if current_center is None:
            continue
        reflected_center = reflect_point_through_image_center(current_center[0], current_center[1], image_size[0], image_size[1])
        move_marker_center(marker, reflected_center)
        marker.metadata["global_reflection_before"] = current_center
        marker.metadata["legacy_overlay_transform"] = "global_reflection"
        marker.metadata["overlay_transform_provenance"] = "legacy_display_correction"
        LOGGER.debug(
            "Experimental overlay correction panel=%s type=%s item=%s raw=(%s,%s) reflected=(%s,%s) "
            "local_rotation_applied=%s parent=%s local=(%s,%s) final=(%s,%s)",
            panel_name,
            marker.marker_type,
            marker_name,
            current_center[0],
            current_center[1],
            reflected_center[0],
            reflected_center[1],
            False,
            None,
            reflected_center[0],
            reflected_center[1],
            reflected_center[0],
            reflected_center[1],
        )
    if not apply_local_child_rotation:
        return
    for marker in markers:
        marker_name = _overlay_group_name(marker, default_central_name)
        central_name = _central_overlay_name(marker_name, by_name)
        if central_name is None:
            continue
        central = by_name[central_name]
        central_center = marker_center_point(central)
        marker_center = marker_center_point(marker)
        if central is marker or central_center is None or marker_center is None:
            continue
        local_center = rotate_point_180_around_anchor(marker_center[0], marker_center[1], central_center[0], central_center[1])
        move_marker_center(marker, local_center)
        existing_transform = as_str(marker.metadata.get("legacy_overlay_transform")) or "global_reflection"
        if "local_180_rotation" not in existing_transform:
            marker.metadata["legacy_overlay_transform"] = f"{existing_transform}+local_180_rotation"
        marker.metadata["local_rotation_anchor"] = central_name
        marker.metadata["local_rotation_before"] = marker_center
        LOGGER.debug(
            "Experimental overlay correction panel=%s type=%s item=%s raw=(%s,%s) reflected=(%s,%s) "
            "local_rotation_applied=%s parent=%s local=(%s,%s) final=(%s,%s)",
            panel_name,
            marker.marker_type,
            marker_name,
            marker.metadata.get("global_reflection_before", marker_center)[0],
            marker.metadata.get("global_reflection_before", marker_center)[1],
            marker_center[0],
            marker_center[1],
            True,
            central_name,
            local_center[0],
            local_center[1],
            local_center[0],
            local_center[1],
        )


def correct_focus_tracking_orientation(
    markers: list[ImageMarker],
    *,
    frame: ImageFrame,
    batch: BatchPosition,
) -> None:
    """Restore Focus/Tracking offsets after the shared frame reflection.

    Tomography 5 records Exposure, Focus, and Tracking offsets in the same
    target-relative coordinate system.  The whole-image reflection corrects
    the absolute batch location on Search Map and Overview frames, but also
    reverses every child vector around the primary Exposure.  Additional
    Exposure markers already receive a local 180-degree restoration in
    ``_apply_grouped_marker_correction``; Focus and Tracking need the same
    restoration whether they are coincident or distinct.
    """

    if frame.source not in {"SearchMap", "Overview"}:
        return
    primary = next(
        (
            marker
            for marker in markers
            if marker.marker_type == MarkerType.EXPOSURE_AREA
            and marker.metadata.get("exposure_index") == 0
        ),
        None,
    )
    if primary is None:
        return
    anchor = marker_center_point(primary)
    if anchor is None:
        return
    for marker_type, prefix in (
        (MarkerType.FOCUS_AREA, "focus"),
        (MarkerType.TRACKING_AREA, "tracking"),
    ):
        marker = next(
            (item for item in markers if item.marker_type == marker_type),
            None,
        )
        if marker is None:
            continue
        before = marker_center_point(marker)
        if before is None:
            continue
        after = rotate_point_180_around_anchor(
            before[0],
            before[1],
            anchor[0],
            anchor[1],
        )
        move_marker_center(marker, after)
        marker.metadata[f"{prefix}_orientation_transform"] = (
            "local_180_about_primary_exposure"
        )
        marker.metadata[f"{prefix}_orientation_anchor"] = primary.id
        marker.metadata[f"{prefix}_orientation_before"] = before
        LOGGER.debug(
            "%s orientation correction panel=%s batch=%s before=%s after=%s anchor=%s",
            prefix.title(),
            frame.source,
            batch.id,
            before,
            after,
            anchor,
        )


def _overlay_group_name(marker: ImageMarker, default_central_name: str | None = None) -> str:
    area_name = as_str(marker.metadata.get("area_name"))
    if area_name and area_name not in {"Exposure", "Focus", "Tracking"}:
        return area_name
    if default_central_name:
        return default_central_name
    return marker.label or as_str(marker.metadata.get("item_name")) or marker.linked_object_id or marker.id


def _central_overlay_name(marker_name: str, by_name: dict[str, ImageMarker]) -> str | None:
    name = marker_name
    while "_" in name:
        candidate, suffix = name.rsplit("_", 1)
        if not suffix.isdigit():
            return None
        if candidate in by_name:
            return candidate
        name = candidate
    return None
