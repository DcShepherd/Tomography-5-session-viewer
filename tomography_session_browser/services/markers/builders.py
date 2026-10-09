"""The marker entry points: one builder per image kind, and
``markers_for_object`` choosing among them."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import cast
import time

from tomography_session_browser.domain.coerce import as_str
from tomography_session_browser.domain.markers import (
    ImageMarker,
    MarkerType,
)
from tomography_session_browser.domain.models import (
    Atlas,
    BatchPosition,
    Overview,
    SearchMap,
    SearchTile,
    TiltSeries,
)
from tomography_session_browser.parsers.xml_parser import find_first
from tomography_session_browser.services.batch_label_service import (
    batch_label_markers,
    compact_batch_label_for_batch,
)
from tomography_session_browser.services.batch_position_status import (
    STATUS_QUEUED,
    STATUS_UNATTRIBUTED,
    aggregate_batch_position_status,
)
from tomography_session_browser.services.pixel_size import overlay_pixel_size
from tomography_session_browser.services.image_geometry import (
    ImageFrame,
    frame_diagnostic,
    image_frame,
    image_size_for_markers,
    position_on_tile_set,
    reflect_point_through_image_center,
    stage_position_for_markers,
    stage_position_from_metadata,
    stage_to_image,
)
from tomography_session_browser.services.item_status import build_item_status_context
from tomography_session_browser.services.tilt_series_validation import TiltSeriesValidation
from tomography_session_browser.diagnostics.profiling import record_aggregate_phase
from tomography_session_browser.services.navigation import resolve_batch_position_overview
from tomography_session_browser.services.markers.context import (
    LOGGER,
    MarkerContext,
    queued_overlay_reference,
)
from tomography_session_browser.services.markers.corrections import apply_grouped_overlay_position_correction
from tomography_session_browser.services.markers.atlas_affine import (
    atlas_alignment_transform,
    overview_alignment_transform,
    overview_region_marker,
    rotate_atlas_overlay_markers_180,
    search_map_alignment_transform,
    search_map_tile_markers,
)
from tomography_session_browser.services.markers.templates import (
    batch_marker_in_frame,
    template_area_markers,
    template_markers_in_frame,
    unresolved_batch_marker,
)
from tomography_session_browser.services.markers.shapes import (
    batch_tooltip,
    bbox_intersects,
    filter_off_image_markers,
    mark_markers_failed,
)
from tomography_session_browser.services.markers.inferred_failed import inferred_failed_tilt_markers


def markers_for_object(
    source: object,
    *,
    context: MarkerContext | None = None,
    batch_positions: Iterable[BatchPosition] = (),
    tilt_series: Iterable[TiltSeries] = (),
    include_unresolved: bool = False,
) -> list[ImageMarker]:
    started = time.perf_counter()
    try:
        return _markers_for_object_impl(
            source,
            context=context,
            batch_positions=batch_positions,
            tilt_series=tilt_series,
            include_unresolved=include_unresolved,
        )
    finally:
        record_aggregate_phase("build_markers", time.perf_counter() - started)


def _markers_for_object_impl(
    source: object,
    *,
    context: MarkerContext | None = None,
    batch_positions: Iterable[BatchPosition] = (),
    tilt_series: Iterable[TiltSeries] = (),
    include_unresolved: bool = False,
) -> list[ImageMarker]:
    if context is not None:
        batch_positions = context.batch_positions
        tilt_series = context.tilt_series
    if isinstance(source, Atlas):
        if context is None:
            LOGGER.debug("Skipping Atlas markers without marker context atlas=%s", source.id)
            return []
        return atlas_markers(source, context)
    if isinstance(source, Overview):
        if context is None:
            LOGGER.debug("Skipping Overview markers without marker context overview=%s", source.id)
            return []
        return overview_markers(source, context)
    if isinstance(source, SearchMap):
        failed_tilt_ids = (
            context.failed_tilt_ids if context is not None else frozenset()
        )
        return search_map_markers(
            source,
            batch_positions,
            include_unresolved=include_unresolved,
            failed_tilt_ids=failed_tilt_ids,
            tilt_series=tilt_series,
            validations=context.tilt_validations() if context is not None else None,
        )
    if isinstance(source, SearchTile):
        failed_tilt_ids = (
            context.failed_tilt_ids if context is not None else frozenset()
        )
        return search_tile_markers(
            source,
            batch_positions,
            search_maps=context.search_maps if context is not None else (),
            failed_tilt_ids=failed_tilt_ids,
            tilt_series=tilt_series,
            validations=context.tilt_validations() if context is not None else None,
        )
    if isinstance(source, BatchPosition):
        return batch_position_markers(source, tilt_series)
    return []


def atlas_markers(atlas: Atlas, context: MarkerContext) -> list[ImageMarker]:
    frame = image_frame(atlas)
    if frame is None:
        # Surface the *reason* so the Session-tab warnings panel and the
        # Atlas-tab metadata can show "Atlas markers unavailable: …" instead
        # of silently rendering nothing. The user can then decide whether to
        # accept approximate placement or open a different session.
        reason = frame_diagnostic(atlas)
        message = f"Atlas markers unavailable: {reason}"
        if message not in atlas.warnings:
            atlas.warnings.append(message)
        LOGGER.debug("Skipping Atlas overlays atlas=%s reason=%s", atlas.id, reason)
        return []
    atlas_alignment = atlas_alignment_transform(atlas)
    search_map_by_id = {search_map.id: search_map for search_map in context.search_maps}
    markers: list[ImageMarker] = []
    for overview in context.overviews:
        alignment = (
            overview_alignment_transform(overview, search_map_by_id)
            or atlas_alignment
        )
        marker = overview_region_marker(
            atlas.id,
            frame,
            overview,
            show_label=False,
            allow_off_image=True,
            alignment_transform=alignment,
        )
        if marker is not None:
            markers.append(marker)
    for search_map in context.search_maps:
        alignment = search_map_alignment_transform(search_map) or atlas_alignment
        markers.extend(
            search_map_tile_markers(
                atlas.id,
                frame,
                search_map,
                show_label=False,
                allow_off_image=True,
                alignment_transform=alignment,
            )
        )
    for batch in context.batch_positions:
        marker = batch_marker_in_frame(atlas.id, frame, batch)
        if marker is not None:
            markers.append(marker)
    if frame.atlas_pixel_affine is None:
        # Fallback only (atlas without a usable node table): the empirical 180
        # degree spin approximates the rotation/flip that the node-table affine
        # now encodes exactly. When the affine is in use the markers are already
        # in the correct atlas-pixel frame, so rotating again would re-introduce
        # the distance-dependent error this fix removes.
        rotate_atlas_overlay_markers_180(
            markers,
            image_size=frame.image_size,
            source_id=atlas.id,
        )
    filter_off_image_markers(markers, image_size=frame.image_size, source_id=atlas.id)
    return markers


def atlas_lod_markers(
    atlas: Atlas,
    context: MarkerContext,
    *,
    include_detail_markers: bool = True,
) -> list[ImageMarker]:
    """Build the status-aware Atlas marker source.

    Screen-space clustering remains a viewer-only operation.  PDF export uses
    the same status leaf markers with ``include_detail_markers=False`` so it
    retains its established overview/search-map footprint density.
    """

    base = atlas_markers(atlas, context)
    frame = image_frame(atlas)
    if frame is None:
        return base

    status_context = build_item_status_context(
        search_maps=context.search_maps,
        batch_positions=context.batch_positions,
        tilt_series=context.tilt_series,
        validations=context.tilt_validations(),
    )
    batch_by_id = {batch.id: batch for batch in context.batch_positions}
    for marker in base:
        if marker.marker_type == MarkerType.SEARCH_MAP:
            marker.metadata["atlas_lod_role"] = (
                "search_map_tile"
                if marker.metadata.get("tile_index") is not None
                else "search_map_footprint"
            )
            continue
        if marker.marker_type != MarkerType.BATCH_POSITION:
            continue
        batch = batch_by_id.get(marker.linked_object_id or "")
        if batch is None:
            continue
        status = aggregate_batch_position_status(batch, status_context)
        navigation = resolve_batch_position_overview(
            batch,
            search_maps=context.search_maps,
            overviews=context.overviews,
        )
        sample_name = (
            as_str(find_first(batch.metadata, "SampleName"))
            or as_str(find_first(batch.metadata, "Sample"))
            or "current atlas scope"
        )
        marker.label = compact_batch_label_for_batch(batch)
        marker.status = status.status
        marker.tooltip = (
            f"Batch position {marker.label}\n"
            f"Status: {status.status.title()}\n"
            f"Sample: {sample_name}\n"
            f"Planned: {status.planned} · Acquired: {status.acquired} · "
            f"Failed: {status.failed}\n"
            f"{navigation.explanation}"
        )
        marker.metadata.update(
            {
                "atlas_lod_role": "batch_position",
                "batch_position_id": batch.id,
                "batch_position_label": marker.label,
                "status_counts": {
                    "collected": status.complete,
                    "partial": status.incomplete + status.missing + status.unknown,
                    "failed": status.failed,
                    "queued": max(status.planned - status.acquired, 0),
                },
                "planned_count": status.planned,
                "acquired_count": status.acquired,
                "failed_count": status.failed,
                "navigation_enabled": navigation.navigable,
                "navigation_overview_id": (
                    navigation.overview.id if navigation.overview is not None else None
                ),
                "navigation_explanation": navigation.explanation,
            }
        )

    # The legacy Atlas source emits one rectangle per reconstructed tile. Add a
    # separate union footprint for the LOD path while retaining the original
    # tiles for high-zoom display.
    tiles_by_search_map: dict[str, list[ImageMarker]] = {}
    for marker in base:
        if (
            marker.marker_type == MarkerType.SEARCH_MAP
            and marker.metadata.get("atlas_lod_role") == "search_map_tile"
            and marker.linked_object_id
        ):
            tiles_by_search_map.setdefault(marker.linked_object_id, []).append(marker)
    footprints: list[ImageMarker] = []
    for search_map_id, tiles in tiles_by_search_map.items():
        boxes = [marker.bbox for marker in tiles if marker.bbox is not None]
        if not boxes:
            continue
        left = min(box[0] for box in boxes)
        top = min(box[1] for box in boxes)
        right = max(box[0] + box[2] for box in boxes)
        bottom = max(box[1] + box[3] for box in boxes)
        source = tiles[0]
        footprints.append(
            ImageMarker(
                id=f"{atlas.id}:search-map:{search_map_id}:lod-footprint",
                marker_type=MarkerType.SEARCH_MAP,
                linked_object_id=search_map_id,
                source_object_id=atlas.id,
                bbox=(left, top, right - left, bottom - top),
                tooltip=f"Search map footprint: {source.metadata.get('item_name') or search_map_id}",
                metadata={
                    "image_size": frame.image_size,
                    "atlas_lod_role": "search_map_footprint",
                    "item_name": source.metadata.get("item_name"),
                },
            )
        )

    # Highest-detail exposure geometry is generated through the existing
    # projection helpers and stays hidden by default in the Atlas viewer.
    detail_markers: list[ImageMarker] = []
    for batch in context.batch_positions:
        position = position_on_tile_set(batch)
        if position is None:
            continue
        generated = template_markers_in_frame(atlas.id, frame, batch, position)
        for marker in generated:
            if marker.marker_type not in {MarkerType.EXPOSURE_AREA, MarkerType.CAMERA_FOV}:
                continue
            marker.metadata["atlas_lod_role"] = "per_exposure"
            detail_markers.append(marker)

    explicitly_linked = {
        tilt_id
        for batch in context.batch_positions
        for tilt_id in (batch.linked_tilt_series_ids or [])
    }
    explicitly_linked.update(
        tilt.id
        for tilt in context.tilt_series
        if tilt.linked_batch_position_id in batch_by_id
    )
    orphan_failed_ids = frozenset(context.failed_tilt_ids.difference(explicitly_linked))
    unattributed = inferred_failed_tilt_markers(
        source=atlas,
        frame=frame,
        tilt_series=context.tilt_series,
        failed_tilt_ids=orphan_failed_ids,
        batch_positions=context.batch_positions,
    )
    for marker in unattributed:
        marker.status = STATUS_UNATTRIBUTED
        marker.label = as_str(marker.metadata.get("batch_label")) or "?"
        marker.tooltip = (
            f"Unattributed failed tilt series: {marker.label}\n"
            "Approximate location from MRC stage metadata.\n"
            "No confident batch-position link; navigation is unavailable."
        )
        marker.metadata.update(
            {
                "atlas_lod_role": "unattributed",
                "navigation_enabled": False,
                "navigation_explanation": "No confident batch-position link.",
            }
        )

    newly_projected = detail_markers + unattributed
    if frame.atlas_pixel_affine is None:
        rotate_atlas_overlay_markers_180(
            newly_projected,
            image_size=frame.image_size,
            source_id=atlas.id,
        )
    filter_off_image_markers(
        newly_projected,
        image_size=frame.image_size,
        source_id=atlas.id,
    )
    if include_detail_markers:
        return base + footprints + newly_projected
    return base + unattributed


def overview_markers(overview: Overview, context: MarkerContext) -> list[ImageMarker]:
    frame = image_frame(overview)
    if frame is None:
        LOGGER.debug("Skipping Overview overlays without reliable frame metadata overview=%s", overview.id)
        return []
    status_context = build_item_status_context(
        search_maps=context.search_maps,
        batch_positions=context.batch_positions,
        tilt_series=context.tilt_series,
        validations=context.tilt_validations(),
    )
    queued_reference = queued_overlay_reference(
        context.batch_positions,
        status_context,
    )
    markers: list[ImageMarker] = []
    for search_map in context.search_maps:
        if search_map.overview is not overview and search_map.overview is not None and search_map.overview.id != overview.id:
            continue
        markers.extend(search_map_tile_markers(overview.id, frame, search_map))
    apply_grouped_overlay_position_correction(
        markers,
        image_size=frame.image_size,
        corrected_types={MarkerType.SEARCH_MAP},
        panel_name="Overview",
    )
    failed_set = context.failed_tilt_ids
    for batch in context.batch_positions:
        if batch.linked_overview_id is not None and batch.linked_overview_id != overview.id:
            continue
        marker = batch_marker_in_frame(overview.id, frame, batch)
        if marker is not None:
            overlay_status = aggregate_batch_position_status(batch, status_context)
            batch_failed = any(tid in failed_set for tid in batch.linked_tilt_series_ids)
            if batch_failed:
                marker.status = "failed"
            elif overlay_status.status == STATUS_QUEUED:
                marker.status = STATUS_QUEUED
            markers.append(marker)
            template_markers = template_markers_in_frame(
                overview.id,
                frame,
                batch,
                # A batch marker exists only where its stage position was found.
                cast(tuple[float, float], marker.metadata.get("stage_position")),
                queued=overlay_status.status == STATUS_QUEUED,
                queued_reference=queued_reference,
            )
            if batch_failed:
                mark_markers_failed(template_markers)
            markers.extend(template_markers)

    # Same fallback as the SearchMap path: failed tilt series with
    # no recorded batch get an approximate marker derived from their
    # MRC extended-header stage position.
    markers.extend(
        inferred_failed_tilt_markers(
            source=overview,
            frame=frame,
            tilt_series=context.tilt_series,
            failed_tilt_ids=failed_set,
            batch_positions=context.batch_positions,
        )
    )
    markers.extend(batch_label_markers(markers, image_size=frame.image_size))
    return markers


def search_map_markers(
    search_map: SearchMap,
    batch_positions: Iterable[BatchPosition],
    *,
    include_unresolved: bool = False,
    failed_tilt_ids: frozenset[str] | set[str] = frozenset(),
    tilt_series: Iterable[TiltSeries] = (),
    validations: Mapping[str, TiltSeriesValidation] | None = None,
) -> list[ImageMarker]:
    batch_positions = tuple(batch_positions)
    tilt_series = tuple(tilt_series)
    markers: list[ImageMarker] = []
    frame = image_frame(search_map)
    failed_set = frozenset(failed_tilt_ids)
    status_context = build_item_status_context(
        search_maps=(search_map,),
        batch_positions=batch_positions,
        tilt_series=tilt_series,
        validations=validations,
    )
    queued_reference = queued_overlay_reference(
        batch_positions,
        status_context,
    )
    for batch in batch_positions:
        if batch.linked_search_map_id != search_map.id:
            continue
        position = position_on_tile_set(batch)
        if frame is None or position is None:
            LOGGER.debug("Skipping batch marker without complete search-map coordinates search_map=%s batch=%s", search_map.id, batch.id)
            if include_unresolved:
                markers.append(unresolved_batch_marker(search_map, batch))
            continue
        width, height = frame.image_size
        x, y = stage_to_image(frame, position)
        if not (0 <= x <= width and 0 <= y <= height):
            LOGGER.debug("Skipping off-image batch marker search_map=%s batch=%s x=%s y=%s", search_map.id, batch.id, x, y)
            if include_unresolved:
                markers.append(unresolved_batch_marker(search_map, batch))
            continue
        batch_marker = ImageMarker(
            id=f"{search_map.id}:batch:{batch.id}",
            marker_type=MarkerType.BATCH_POSITION,
            linked_object_id=batch.id,
            source_object_id=search_map.id,
            x=x,
            y=y,
            radius=10,
            label=batch.name or batch.id,
            tooltip=batch_tooltip(batch),
            status=batch.status,
            unresolved=False,
            metadata={
                "search_map_name": search_map.name,
                "image_size": frame.image_size,
                "coordinate_source": "stage_to_search_map_image",
                "stage_position": position,
            },
        )
        # Exposure / focus / tracking overlays on the SearchMap frame
        # are reflected through the image centre by the experimental
        # position-correction step (see CORRECTED_AREA_MARKER_TYPES).
        # Without the same reflection, the batch-position dot landed
        # at the un-reflected stage position, making it look offset
        # from its own templated exposures. Apply the same correction
        # so the dot sits at the centre of its template group.
        apply_grouped_overlay_position_correction(
            [batch_marker],
            image_size=frame.image_size,
            corrected_types={MarkerType.BATCH_POSITION},
            panel_name=frame.source,
        )
        overlay_status = aggregate_batch_position_status(batch, status_context)
        batch_failed = any(tid in failed_set for tid in batch.linked_tilt_series_ids)
        if batch_failed:
            batch_marker.status = "failed"
        elif overlay_status.status == STATUS_QUEUED:
            batch_marker.status = STATUS_QUEUED
        markers.append(batch_marker)
        template_markers = template_markers_in_frame(
            search_map.id,
            frame,
            batch,
            position,
            queued=overlay_status.status == STATUS_QUEUED,
            queued_reference=queued_reference,
        )
        if batch_failed:
            mark_markers_failed(template_markers)
        markers.extend(template_markers)

    # Failed tilt series whose batch was never recorded in
    # BatchPositionsList.xml don't get a marker via the loop above.
    # Fall back to the stage position parsed from the MRC extended
    # header so the user still sees an approximate failed location
    # on the search map.
    if frame is not None:
        markers.extend(
            inferred_failed_tilt_markers(
                source=search_map,
                frame=frame,
                tilt_series=tilt_series,
                failed_tilt_ids=failed_set,
                batch_positions=batch_positions,
            )
        )
        markers.extend(batch_label_markers(markers, image_size=frame.image_size))
    return markers


def search_tile_markers(
    search_tile: SearchTile,
    batch_positions: Iterable[BatchPosition],
    *,
    search_maps: Iterable[SearchMap] = (),
    failed_tilt_ids: frozenset[str] | set[str] = frozenset(),
    tilt_series: Iterable[TiltSeries] = (),
    validations: Mapping[str, TiltSeriesValidation] | None = None,
) -> list[ImageMarker]:
    batch_positions = tuple(batch_positions)
    search_maps = tuple(search_maps)
    tilt_series = tuple(tilt_series)
    projected = _search_map_markers_projected_to_search_tile(
        search_tile,
        batch_positions,
        search_maps=search_maps,
        failed_tilt_ids=failed_tilt_ids,
        tilt_series=tilt_series,
        validations=validations,
    )
    if projected is not None:
        return projected

    frame = image_frame(search_tile)
    if frame is None:
        LOGGER.debug("Skipping Search tile overlays without reliable frame metadata search_tile=%s", search_tile.id)
        return []
    batch = next(
        (item for item in batch_positions if item.id == search_tile.batch_position_id),
        None,
    )
    if batch is None:
        LOGGER.debug("Skipping Search tile overlays without linked batch search_tile=%s", search_tile.id)
        return []
    position = position_on_tile_set(batch) or stage_position_from_metadata(search_tile.metadata.get("BatchSearch.xml"))
    if position is None:
        LOGGER.debug("Skipping Search tile overlays without batch stage position search_tile=%s batch=%s", search_tile.id, batch.id)
        return []

    failed_set = frozenset(failed_tilt_ids)
    status_context = build_item_status_context(
        search_maps=search_maps,
        search_tiles=(search_tile,),
        batch_positions=batch_positions,
        tilt_series=tilt_series,
        validations=validations,
    )
    overlay_status = aggregate_batch_position_status(batch, status_context)
    queued_reference = queued_overlay_reference(batch_positions, status_context)
    batch_failed = any(tid in failed_set for tid in batch.linked_tilt_series_ids)
    x, y = stage_to_image(frame, position)
    width, height = frame.image_size
    markers: list[ImageMarker] = []
    if 0 <= x <= width and 0 <= y <= height:
        batch_marker = ImageMarker(
            id=f"{search_tile.id}:batch:{batch.id}",
            marker_type=MarkerType.BATCH_POSITION,
            linked_object_id=batch.id,
            source_object_id=search_tile.id,
            x=x,
            y=y,
            radius=10,
            label=batch.name or batch.id,
            tooltip=batch_tooltip(batch),
            status=(
                "failed"
                if batch_failed
                else STATUS_QUEUED
                if overlay_status.status == STATUS_QUEUED
                else batch.status
            ),
            unresolved=False,
            metadata={
                "search_tile_name": search_tile.name,
                "image_size": frame.image_size,
                "coordinate_source": "stage_to_search_tile_image",
                "stage_position": position,
            },
        )
        markers.append(batch_marker)
    else:
        LOGGER.debug(
            "Search tile batch centre is outside tile image search_tile=%s batch=%s x=%s y=%s image=%s",
            search_tile.id,
            batch.id,
            x,
            y,
            frame.image_size,
        )

    template_markers = template_markers_in_frame(
        search_tile.id,
        frame,
        batch,
        position,
        queued=overlay_status.status == STATUS_QUEUED,
        queued_reference=queued_reference,
    )
    if batch_failed:
        mark_markers_failed(template_markers)
    markers.extend(template_markers)
    markers.extend(batch_label_markers(markers, image_size=frame.image_size))
    return markers


def _search_map_markers_projected_to_search_tile(
    search_tile: SearchTile,
    batch_positions: Iterable[BatchPosition],
    *,
    search_maps: Iterable[SearchMap],
    failed_tilt_ids: frozenset[str] | set[str],
    tilt_series: Iterable[TiltSeries],
    validations: Mapping[str, TiltSeriesValidation] | None = None,
) -> list[ImageMarker] | None:
    search_map = next(
        (item for item in search_maps if item.id == search_tile.search_map_id),
        None,
    )
    batch = next(
        (item for item in batch_positions if item.id == search_tile.batch_position_id),
        None,
    )
    if search_map is None or batch is None:
        return None
    tile_frame = image_frame(search_tile)
    search_frame = image_frame(search_map)
    if tile_frame is None or search_frame is None:
        return None
    tile_bounds = _tile_bounds_in_search_map_frame(search_tile, search_frame)
    if tile_bounds is None:
        return None

    full_markers = search_map_markers(
        search_map,
        batch_positions,
        failed_tilt_ids=failed_tilt_ids,
        tilt_series=tilt_series,
        validations=validations,
    )
    projected: list[ImageMarker] = []
    for marker in full_markers:
        if marker.linked_object_id != batch.id:
            continue
        if marker.marker_type not in {
            MarkerType.BATCH_POSITION,
            MarkerType.EXPOSURE_AREA,
            MarkerType.CAMERA_FOV,
            MarkerType.TRACKING_AREA,
            MarkerType.FOCUS_AREA,
            MarkerType.CONDITION_AREA,
            MarkerType.TEMPLATE_AREA,
            MarkerType.LINK_LINE,
        }:
            continue
        projected_marker = _project_search_map_marker_to_tile(
            marker,
            search_tile_id=search_tile.id,
            tile_bounds=tile_bounds,
            tile_image_size=tile_frame.image_size,
        )
        if projected_marker is not None:
            projected.append(projected_marker)
    projected.extend(batch_label_markers(projected, image_size=tile_frame.image_size))
    return projected


def _tile_bounds_in_search_map_frame(
    search_tile: SearchTile,
    search_frame: ImageFrame,
) -> tuple[float, float, float, float] | None:
    tile_stage = stage_position_for_markers(search_tile)
    tile_size = image_size_for_markers(search_tile)
    tile_pixel = overlay_pixel_size(search_tile)
    if tile_stage is None or tile_size is None or tile_pixel is None:
        return None
    # Search Map overlays are rendered after the display-space reflection
    # correction. Convert the tile centre into that same corrected full-map
    # coordinate system, then derive the tile rectangle at Search Map scale.
    tile_center = reflect_point_through_image_center(
        *stage_to_image(search_frame, tile_stage),
        search_frame.image_size[0],
        search_frame.image_size[1],
    )
    width = tile_size[0] * tile_pixel / search_frame.pixel_size
    height = tile_size[1] * tile_pixel / search_frame.pixel_size
    if width <= 0 or height <= 0:
        return None
    return tile_center[0] - width / 2, tile_center[1] - height / 2, width, height


def _project_search_map_marker_to_tile(
    marker: ImageMarker,
    *,
    search_tile_id: str,
    tile_bounds: tuple[float, float, float, float],
    tile_image_size: tuple[int, int],
) -> ImageMarker | None:
    left, top, width, height = tile_bounds
    tile_width, tile_height = tile_image_size
    if width <= 0 or height <= 0 or tile_width <= 0 or tile_height <= 0:
        return None
    scale_x = tile_width / width
    scale_y = tile_height / height

    def point(x: float, y: float) -> tuple[float, float]:
        return (x - left) * scale_x, (y - top) * scale_y

    x = y = None
    if marker.x is not None and marker.y is not None:
        x, y = point(marker.x, marker.y)
    bbox = None
    if marker.bbox is not None:
        bx, by, bw, bh = marker.bbox
        px, py = point(bx, by)
        bbox = (px, py, bw * scale_x, bh * scale_y)
    polygon = [point(px, py) for px, py in marker.polygon] if marker.polygon else None

    if not _projected_marker_intersects(tile_image_size, x, y, bbox, polygon):
        return None
    metadata = dict(marker.metadata)
    metadata["image_size"] = tile_image_size
    metadata["coordinate_source"] = "search_map_overlay_projected_to_search_tile"
    metadata["search_map_marker_id"] = marker.id
    metadata["search_map_tile_bounds"] = tile_bounds
    return ImageMarker(
        id=f"{search_tile_id}:projected:{marker.id}",
        marker_type=marker.marker_type,
        linked_object_id=marker.linked_object_id,
        source_object_id=search_tile_id,
        x=x,
        y=y,
        radius=(marker.radius * (scale_x + scale_y) / 2.0) if marker.radius is not None else None,
        bbox=bbox,
        polygon=polygon,
        label=marker.label,
        tooltip=marker.tooltip,
        status=marker.status,
        visible=marker.visible,
        selected=marker.selected,
        unresolved=marker.unresolved,
        metadata=metadata,
    )


def _projected_marker_intersects(
    image_size: tuple[int, int],
    x: float | None,
    y: float | None,
    bbox: tuple[float, float, float, float] | None,
    polygon: list[tuple[float, float]] | None,
) -> bool:
    width, height = image_size
    if x is not None and y is not None and 0 <= x <= width and 0 <= y <= height:
        return True
    if bbox is not None and bbox_intersects(image_size, bbox):
        return True
    if polygon:
        return any(0 <= px <= width and 0 <= py <= height for px, py in polygon)
    return False


def batch_position_markers(batch: BatchPosition, tilt_series: Iterable[TiltSeries]) -> list[ImageMarker]:
    markers = template_area_markers(batch)
    for tilt in tilt_series:
        if tilt.linked_batch_position_id != batch.id:
            continue
        markers.append(
            ImageMarker(
                id=f"{batch.id}:tilt:{tilt.id}",
                marker_type=MarkerType.TILT_SERIES,
                linked_object_id=tilt.id,
                source_object_id=batch.id,
                x=None,
                y=None,
                radius=10,
                label=tilt.name,
                tooltip=f"Tilt series: {tilt.name}",
                unresolved=True,
            )
        )
    markers.extend(batch_label_markers(markers, exposure_indices={0}))
    return markers
