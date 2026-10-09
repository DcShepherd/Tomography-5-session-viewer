"""A batch position's template overlays (exposure, tracking, focus and
condition areas, the template box and exposure links), in its own template
image or projected into an overview or search-map frame."""
from __future__ import annotations

from tomography_session_browser.domain.coerce import (
    as_float,
    as_str,
)
from tomography_session_browser.domain.markers import (
    ImageMarker,
    MarkerType,
)
from tomography_session_browser.domain.models import (
    BatchPosition,
    SearchMap,
)
from tomography_session_browser.parsers.xml_parser import find_first
from tomography_session_browser.services.batch_label_service import compact_batch_label_for_batch
from tomography_session_browser.services.batch_position_status import STATUS_QUEUED
from tomography_session_browser.services.pixel_size import overlay_mrc_pixel_size
from tomography_session_browser.services.image_geometry import (
    ImageFrame,
    position_on_tile_set,
    reflect_point_through_image_center,
    stage_to_image,
)
from tomography_session_browser.services.markers.context import (
    BEAM_AREA_MARKER_TYPES,
    EXPERIMENTAL_OVERLAY_POSITION_CORRECTION,
    LOGGER,
    QueuedOverlayReference,
)
from tomography_session_browser.services.markers.corrections import (
    apply_grouped_overlay_position_correction,
    correct_focus_tracking_orientation,
    corrected_area_types_for_frame,
)
from tomography_session_browser.services.markers.beam import (
    area_radius,
    beam_radius_pixels,
    camera_fov_markers_from_exposures,
    queued_beam_radius_pixels,
)
from tomography_session_browser.services.markers.shapes import (
    batch_tooltip,
    bbox_intersects,
    filter_off_image_template_markers,
)


def template_area_markers(batch: BatchPosition) -> list[ImageMarker]:
    metadata = batch.search_mrc_metadata or batch.tracking_mrc_metadata or batch.exposure_mrc_metadata
    pixel_size = overlay_mrc_pixel_size(metadata)
    width = metadata.nx if metadata else None
    height = metadata.ny if metadata else None
    specs = [
        (MarkerType.EXPOSURE_AREA, "ExposureTemplateAreaParameters", "Exposure"),
        (MarkerType.TRACKING_AREA, "TrackingTemplateAreaParameters", "Tracking"),
        (MarkerType.FOCUS_AREA, "FocusTemplateAreaParameters", "Focus"),
        (MarkerType.CONDITION_AREA, "ConditionTemplateAreaParameters", "Condition"),
    ]
    markers: list[ImageMarker] = []
    template = _template_bbox_marker(batch, width, height)
    if template is not None:
        markers.append(template)
    for marker_type, key, label in specs:
        marker = _template_marker(
            batch,
            batch.metadata.get(key),
            marker_type,
            label,
            width,
            height,
            pixel_size,
            exposure_index=0 if marker_type == MarkerType.EXPOSURE_AREA else None,
            exposure_type="main" if marker_type == MarkerType.EXPOSURE_AREA else None,
        )
        if marker is not None:
            markers.append(marker)
    additional = batch.metadata.get("AdditionalExposureTemplateAreas")
    raw_areas = find_first(additional, "ExposureTemplateAreaParameters")
    if isinstance(raw_areas, dict):
        raw_areas = [raw_areas]
    if isinstance(raw_areas, list):
        for index, raw in enumerate(raw_areas, start=1):
            marker = _template_marker(
                batch,
                raw,
                MarkerType.EXPOSURE_AREA,
                as_str(find_first(raw, "Name")) or f"Exposure {index}",
                width,
                height,
                pixel_size,
                exposure_index=index,
                exposure_type="additional",
            )
            if marker is not None:
                markers.append(marker)
    markers.extend(camera_fov_markers_from_exposures(batch, markers, pixel_size, (width, height) if width is not None and height is not None else None))
    return markers


def template_markers_in_frame(
    source_id: str,
    frame: ImageFrame,
    batch: BatchPosition,
    batch_stage_position: tuple[float, float],
    *,
    queued: bool = False,
    queued_reference: QueuedOverlayReference | None = None,
) -> list[ImageMarker]:
    specs = [
        (MarkerType.EXPOSURE_AREA, "ExposureTemplateAreaParameters", "Exposure"),
        (MarkerType.TRACKING_AREA, "TrackingTemplateAreaParameters", "Tracking"),
        (MarkerType.FOCUS_AREA, "FocusTemplateAreaParameters", "Focus"),
        (MarkerType.CONDITION_AREA, "ConditionTemplateAreaParameters", "Condition"),
    ]
    markers: list[ImageMarker] = []
    template = _template_bbox_marker_in_frame(source_id, frame, batch, batch_stage_position)
    if template is not None:
        markers.append(template)
    for marker_type, key, label in specs:
        marker = _template_marker_in_frame(
            source_id,
            frame,
            batch,
            batch.metadata.get(key),
            marker_type,
            label,
            batch_stage_position,
            exposure_index=0 if marker_type == MarkerType.EXPOSURE_AREA else None,
            exposure_type="main" if marker_type == MarkerType.EXPOSURE_AREA else None,
            queued=queued,
            queued_reference=queued_reference,
        )
        if marker is not None:
            markers.append(marker)
    additional = batch.metadata.get("AdditionalExposureTemplateAreas")
    raw_areas = find_first(additional, "ExposureTemplateAreaParameters")
    if isinstance(raw_areas, dict):
        raw_areas = [raw_areas]
    if isinstance(raw_areas, list):
        for index, raw in enumerate(raw_areas, start=1):
            label = as_str(find_first(raw, "Name")) or f"Exposure {index}"
            marker = _template_marker_in_frame(
                source_id,
                frame,
                batch,
                raw,
                MarkerType.EXPOSURE_AREA,
                label,
                batch_stage_position,
                exposure_index=index,
                exposure_type="additional",
                queued=queued,
                queued_reference=queued_reference,
            )
            if marker is not None:
                markers.append(marker)
    corrected_types = corrected_area_types_for_frame(frame)
    apply_grouped_overlay_position_correction(
        markers,
        image_size=frame.image_size,
        corrected_types=corrected_types,
        panel_name=frame.source,
        default_central_name=batch.name or batch.id,
    )
    correct_focus_tracking_orientation(
        markers,
        frame=frame,
        batch=batch,
    )
    filter_off_image_template_markers(
        markers,
        image_size=frame.image_size,
        source_id=source_id,
        batch=batch,
    )
    camera_markers = camera_fov_markers_from_exposures(
        batch,
        markers,
        frame.pixel_size,
        frame.image_size,
        source_id=source_id,
        queued=queued,
        queued_reference=queued_reference,
    )
    if (
        queued
        and queued_beam_radius_pixels(queued_reference, frame.pixel_size) is None
        and camera_markers
    ):
        # No physical beam diameter exists for this queued target. Keep its
        # exposure marker as an interaction/label anchor, but draw only the
        # physically sized hollow camera footprint in the image.
        for marker in markers:
            if marker.marker_type in BEAM_AREA_MARKER_TYPES:
                marker.visible = False
                marker.metadata["queued_camera_fov_replacement"] = True
    markers.extend(camera_markers)
    markers.extend(_exposure_link_markers(source_id, batch, markers))
    return markers


def _template_marker_in_frame(
    source_id: str,
    frame: ImageFrame,
    batch: BatchPosition,
    raw: object,
    marker_type: str,
    label: str,
    batch_stage_position: tuple[float, float],
    *,
    exposure_index: int | None = None,
    exposure_type: str | None = None,
    queued: bool = False,
    queued_reference: QueuedOverlayReference | None = None,
) -> ImageMarker | None:
    if not isinstance(raw, dict) or raw.get("_attributes", {}).get("nil") == "true":
        return None
    position_x = as_float(raw.get("PositionX"))
    position_y = as_float(raw.get("PositionY"))
    if position_x is None or position_y is None:
        return None
    area_name = as_str(find_first(raw, "Name")) or label
    stage_x = batch_stage_position[0] + position_x
    stage_y = batch_stage_position[1] + position_y
    x, y = stage_to_image(frame, (stage_x, stage_y))
    width, height = frame.image_size
    defer_bounds_check = marker_type in corrected_area_types_for_frame(frame)
    if not (0 <= x <= width and 0 <= y <= height) and not defer_bounds_check:
        LOGGER.debug("Skipping off-image template marker source=%s batch=%s type=%s x=%s y=%s", source_id, batch.id, marker_type, x, y)
        return None
    beam_radius = (
        queued_beam_radius_pixels(queued_reference, frame.pixel_size)
        if queued
        else beam_radius_pixels(batch, frame.pixel_size)
    )
    radius = beam_radius or area_radius(marker_type)
    LOGGER.debug(
        "Overlay conversion type=%s batch=%s source=%s offset=(%s,%s)m target_image=%s pixel_size=%s position=(%s,%s) radius=%s",
        marker_type,
        batch.id,
        source_id,
        position_x,
        position_y,
        frame.image_size,
        frame.pixel_size,
        x,
        y,
        radius,
    )
    marker_metadata = {
        "batch_id": batch.id,
        "batch_name": batch.name or batch.id,
        "batch_label": compact_batch_label_for_batch(batch),
        "raw": raw,
        "image_size": frame.image_size,
        "coordinate_source": f"batch_template_stage_offset_to_{frame.source}",
        "stage_position": (stage_x, stage_y),
        "raw_image_position": (x, y),
        "radius_source": (
            "successful_tilt_series_exposure_mrc"
            if queued and beam_radius is not None
            else "default"
            if queued
            else "BeamDiameter"
            if beam_radius is not None
            else "default"
        ),
        "area_name": area_name,
    }
    if queued:
        marker_metadata.update(
            {
                "queued_position": True,
                "queued_beam_source_batch_id": (
                    queued_reference.beam_source_batch_id
                    if queued_reference is not None
                    else None
                ),
                "queued_beam_source": (
                    queued_reference.beam_source
                    if queued_reference is not None
                    else None
                ),
            }
        )
    if marker_type == MarkerType.EXPOSURE_AREA:
        marker_metadata.update(
            {
                "exposure_index": exposure_index,
                "exposure_type": exposure_type or ("main" if exposure_index == 0 else "additional"),
            }
        )
    return ImageMarker(
        id=f"{source_id}:batch:{batch.id}:{marker_type}:{area_name}",
        marker_type=marker_type,
        linked_object_id=batch.id,
        source_object_id=source_id,
        x=x,
        y=y,
        radius=radius,
        label=None,
        tooltip=(
            f"{area_name} area for {batch.name or batch.id}\n"
            + (
                f"Queued; beam diameter borrowed from successful batch "
                f"{queued_reference.beam_source_batch_id}."
                if queued and beam_radius is not None and queued_reference is not None
                else "Queued; beam diameter unavailable."
                if queued
                else ""
            )
        ).rstrip(),
        status=STATUS_QUEUED if queued else batch.status,
        unresolved=False,
        metadata=marker_metadata,
    )


def _exposure_link_markers(source_id: str, batch: BatchPosition, markers: list[ImageMarker]) -> list[ImageMarker]:
    main = next(
        (
            marker
            for marker in markers
            if marker.marker_type == MarkerType.EXPOSURE_AREA
            and marker.x is not None
            and marker.y is not None
            and marker.metadata.get("area_name") == "Exposure"
        ),
        None,
    )
    if main is None or main.x is None or main.y is None:
        return []
    main_x, main_y = main.x, main.y
    linked: list[ImageMarker] = []
    for marker in markers:
        if (
            marker is main
            or marker.marker_type != MarkerType.EXPOSURE_AREA
            or marker.x is None
            or marker.y is None
            or marker.metadata.get("area_name") == "Exposure"
        ):
            continue
        area_name = as_str(marker.metadata.get("area_name")) or marker.id
        linked.append(
            ImageMarker(
                id=f"{source_id}:batch:{batch.id}:link:{area_name}",
                marker_type=MarkerType.LINK_LINE,
                linked_object_id=batch.id,
                source_object_id=source_id,
                polygon=[(main_x, main_y), (marker.x, marker.y)],
                tooltip=f"Linked exposure: {batch.name or batch.id} -> {area_name}",
                status=batch.status,
                metadata={
                    "batch_id": batch.id,
                    "from_marker_id": main.id,
                    "to_marker_id": marker.id,
                    "linked_area_name": area_name,
                    "image_size": marker.metadata.get("image_size"),
                },
            )
        )
    return linked


def _template_marker(
    batch: BatchPosition,
    raw: object,
    marker_type: str,
    label: str,
    width: int | None,
    height: int | None,
    pixel_size: float | None,
    *,
    exposure_index: int | None = None,
    exposure_type: str | None = None,
) -> ImageMarker | None:
    if not isinstance(raw, dict) or raw.get("_attributes", {}).get("nil") == "true":
        return None
    position_x = as_float(raw.get("PositionX"))
    position_y = as_float(raw.get("PositionY"))
    x: float | None = None
    y: float | None = None
    unresolved = True
    if width is not None and height is not None and pixel_size is not None and position_x is not None and position_y is not None:
        x = (width / 2) + (position_x / pixel_size)
        y = (height / 2) - (position_y / pixel_size)
        unresolved = not (0 <= x <= width and 0 <= y <= height)
    radius = beam_radius_pixels(batch, pixel_size) or area_radius(marker_type)
    LOGGER.debug(
        "Batch overlay conversion type=%s batch=%s offset=(%s,%s)m image=(%s,%s) pixel_size=%s position=(%s,%s) radius=%s unresolved=%s",
        marker_type,
        batch.id,
        position_x,
        position_y,
        width,
        height,
        pixel_size,
        x,
        y,
        radius,
        unresolved,
    )
    marker_metadata = {
        "batch_id": batch.id,
        "batch_name": batch.name or batch.id,
        "batch_label": compact_batch_label_for_batch(batch),
        "raw": raw,
        "image_size": (width, height) if width is not None and height is not None else None,
        "coordinate_source": "template_offset_to_batch_image",
        "offset": (position_x, position_y),
        "radius_source": "BeamDiameter" if beam_radius_pixels(batch, pixel_size) is not None else "default",
        "area_name": as_str(find_first(raw, "Name")) or label,
    }
    if marker_type == MarkerType.EXPOSURE_AREA:
        marker_metadata.update(
            {
                "exposure_index": exposure_index,
                "exposure_type": exposure_type or ("main" if exposure_index == 0 else "additional"),
            }
        )
    return ImageMarker(
        id=f"{batch.id}:{marker_type}:{label}",
        marker_type=marker_type,
        linked_object_id=batch.id,
        source_object_id=batch.id,
        x=x,
        y=y,
        radius=radius,
        label=None,
        tooltip=f"{label} area for {batch.name or batch.id}",
        status=batch.status,
        unresolved=unresolved,
        metadata=marker_metadata,
    )


def unresolved_batch_marker(search_map: SearchMap, batch: BatchPosition) -> ImageMarker:
    return ImageMarker(
        id=f"{search_map.id}:batch:{batch.id}",
        marker_type=MarkerType.BATCH_POSITION,
        linked_object_id=batch.id,
        source_object_id=search_map.id,
        radius=10,
        label=batch.name or batch.id,
        tooltip=f"{batch_tooltip(batch)}\nCoordinates unavailable",
        status=batch.status,
        unresolved=True,
        metadata={"search_map_name": search_map.name},
    )


def batch_marker_in_frame(source_id: str, frame: ImageFrame, batch: BatchPosition) -> ImageMarker | None:
    position = position_on_tile_set(batch)
    if position is None:
        LOGGER.debug("Skipping batch marker without stage position source=%s batch=%s", source_id, batch.id)
        return None
    x, y = stage_to_image(frame, position)
    width, height = frame.image_size
    if EXPERIMENTAL_OVERLAY_POSITION_CORRECTION and frame.source == "Overview":
        reflected_x, reflected_y = reflect_point_through_image_center(x, y, width, height)
        LOGGER.debug(
            "Experimental overlay correction panel=%s type=%s item=%s raw=(%s,%s) reflected=(%s,%s) "
            "local_rotation_applied=%s parent=%s local=(%s,%s) final=(%s,%s)",
            frame.source,
            MarkerType.BATCH_POSITION,
            batch.name or batch.id,
            x,
            y,
            reflected_x,
            reflected_y,
            False,
            None,
            reflected_x,
            reflected_y,
            reflected_x,
            reflected_y,
        )
        x, y = reflected_x, reflected_y
    if not (0 <= x <= width and 0 <= y <= height):
        LOGGER.debug("Skipping off-image batch marker source=%s batch=%s x=%s y=%s image=%s", source_id, batch.id, x, y, frame.image_size)
        return None
    return ImageMarker(
        id=f"{source_id}:batch:{batch.id}",
        marker_type=MarkerType.BATCH_POSITION,
        linked_object_id=batch.id,
        source_object_id=source_id,
        x=x,
        y=y,
        radius=10,
        label=batch.name or batch.id,
        tooltip=batch_tooltip(batch),
        status=batch.status,
        unresolved=False,
        metadata={
            "image_size": frame.image_size,
            "coordinate_source": f"stage_to_{frame.source}",
            "stage_position": position,
        },
    )


def _template_bbox_marker(batch: BatchPosition, width: int | None, height: int | None) -> ImageMarker | None:
    if width is None or height is None:
        return None
    rect = _template_image_data_area(batch)
    if rect is None:
        return None
    return ImageMarker(
        id=f"{batch.id}:{MarkerType.TEMPLATE_AREA}:template",
        marker_type=MarkerType.TEMPLATE_AREA,
        linked_object_id=batch.id,
        source_object_id=batch.id,
        bbox=rect,
        label="Template",
        tooltip=f"Template field for {batch.name or batch.id}",
        status=batch.status,
        metadata={"image_size": (width, height), "coordinate_source": "TemplateImageDataArea"},
    )


def _template_bbox_marker_in_frame(source_id: str, frame: ImageFrame, batch: BatchPosition, batch_stage_position: tuple[float, float]) -> ImageMarker | None:
    rect = _template_image_data_area(batch)
    metadata = batch.search_mrc_metadata or batch.tracking_mrc_metadata or batch.exposure_mrc_metadata
    source_pixel_size = overlay_mrc_pixel_size(metadata)
    source_width = metadata.nx if metadata else None
    source_height = metadata.ny if metadata else None
    if rect is None or source_pixel_size is None or source_width is None or source_height is None:
        return None
    x, y, width, height = rect
    center_offset_x = ((x + width / 2) - source_width / 2) * source_pixel_size
    center_offset_y = -(((y + height / 2) - source_height / 2) * source_pixel_size)
    center = (batch_stage_position[0] + center_offset_x, batch_stage_position[1] + center_offset_y)
    center_x, center_y = stage_to_image(frame, center)
    target_width = width * source_pixel_size / frame.pixel_size
    target_height = height * source_pixel_size / frame.pixel_size
    bbox = (center_x - target_width / 2, center_y - target_height / 2, target_width, target_height)
    defer_bounds_check = MarkerType.TEMPLATE_AREA in corrected_area_types_for_frame(frame)
    if not bbox_intersects(frame.image_size, bbox) and not defer_bounds_check:
        LOGGER.debug("Skipping off-image template bbox source=%s batch=%s bbox=%s image=%s", source_id, batch.id, bbox, frame.image_size)
        return None
    return ImageMarker(
        id=f"{source_id}:batch:{batch.id}:{MarkerType.TEMPLATE_AREA}:template",
        marker_type=MarkerType.TEMPLATE_AREA,
        linked_object_id=batch.id,
        source_object_id=source_id,
        bbox=bbox,
        label="Template",
        tooltip=f"Template field for {batch.name or batch.id}",
        status=batch.status,
        metadata={
            "image_size": frame.image_size,
            "coordinate_source": f"TemplateImageDataArea_to_{frame.source}",
            "batch_id": batch.id,
            "source_rect": rect,
        },
    )


def _template_image_data_area(batch: BatchPosition) -> tuple[float, float, float, float] | None:
    raw = batch.metadata.get("TemplateImageDataArea")
    if not isinstance(raw, dict):
        return None
    x = as_float(raw.get("x"))
    y = as_float(raw.get("y"))
    width = as_float(raw.get("width"))
    height = as_float(raw.get("height"))
    if x is None or y is None or width is None or height is None or width <= 0 or height <= 0:
        return None
    return (x, y, width, height)
