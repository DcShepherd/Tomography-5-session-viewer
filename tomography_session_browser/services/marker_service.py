from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import logging
import math
import time
from typing import Any

from tomography_session_browser.domain.display_names import format_overview_display_name
from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.domain.models import Atlas, BatchPosition, MrcMetadata, Overview, SearchMap, SearchTile, TiltSeries
from tomography_session_browser.parsers.xml_parser import find_first
from tomography_session_browser.services.batch_label_service import (
    batch_label_markers,
    compact_batch_label_for_batch,
    compact_label_for_tilt,
)
from tomography_session_browser.services.batch_position_status import (
    STATUS_QUEUED,
    STATUS_UNATTRIBUTED,
    aggregate_batch_position_status,
)
from tomography_session_browser.services.item_status import (
    ItemStatusContext,
    batch_position_status_counts,
    build_item_status_context,
)
from tomography_session_browser.services.loading_profiler import record_aggregate_phase
from tomography_session_browser.services.navigation_service import resolve_batch_position_overview

LOGGER = logging.getLogger(__name__)

# Experimental display-only overlay correction. It is intentionally isolated so
# it can be disabled quickly if registration turns out to belong in a different
# display-transform layer.
EXPERIMENTAL_OVERLAY_POSITION_CORRECTION = True
# Marker types that get reflected through the image centre on Search-
# Map and Overview frames. Includes ``TEMPLATE_AREA`` (the bounding
# box that surrounds a batch position's exposure/tracking/focus
# overlays): without this the box stayed at the un-reflected stage
# position while the markers inside it moved, leaving the rectangle
# floating away from its own contents on the search-map view.
CORRECTED_AREA_MARKER_TYPES = {
    MarkerType.EXPOSURE_AREA,
    MarkerType.TRACKING_AREA,
    MarkerType.FOCUS_AREA,
    MarkerType.TEMPLATE_AREA,
}
BEAM_AREA_MARKER_TYPES = {
    MarkerType.EXPOSURE_AREA,
    MarkerType.TRACKING_AREA,
    MarkerType.FOCUS_AREA,
    MarkerType.CONDITION_AREA,
}
BEAM_DIAMETER_LOW_DOSE_CONTEXT = "backward_alignment_optics"
BEAM_DIAMETER_MICROSCOPE_CONTEXT = "microscope_data_optics"
BEAM_DIAMETER_EXPOSURE_MRC_CONTEXT = "exposure_mrc_illuminated_area"
MICROSCOPE_DATA_BEAM_DISPLAY_SCALE = 0.1


@dataclass(frozen=True, slots=True)
class MarkerContext:
    overviews: tuple[Overview, ...] = ()
    search_maps: tuple[SearchMap, ...] = ()
    batch_positions: tuple[BatchPosition, ...] = ()
    tilt_series: tuple[TiltSeries, ...] = ()
    # IDs of tilt series the validator has marked as failed. When a
    # batch's linked_tilt_series_ids intersects this set, the batch's
    # template / exposure / tracking / focus markers are tagged with
    # ``status = "failed"`` so the GUI and the PDF render them red.
    failed_tilt_ids: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class AtlasPixelAffine:
    """Exact stage -> atlas-pixel affine recovered from the Atlas.dm node table.

    Tomography 5 records, for every atlas tile, the *measured* mapping between
    its stage coordinate and its pixel rectangle in the stitched atlas mosaic
    (``StagePosition`` -> ``AtlasPixelPosition``). Fitting an affine to that
    table reproduces the true rotation, anisotropic scale, handedness flip and
    origin of the atlas image — all in one transform — instead of approximating
    them with a normalised per-tile basis plus an empirical 180 degree spin.

    Coefficients map a stage coordinate in metres to a pixel in the atlas
    *montage* frame (``image_size``):

        px = a * stage_x + b * stage_y + c
        py = d * stage_x + e * stage_y + g

    ``pixel_size`` is the mean metres-per-pixel of the two fitted axes, kept so
    overlay *extents* (search-map / overview boxes) scale with the same physical
    resolution the centres are placed in.
    """

    coefficients: tuple[float, float, float, float, float, float]
    image_size: tuple[int, int]
    pixel_size: float
    source: str
    residual_px: float
    sample_count: int

    def apply(self, stage_x: float, stage_y: float) -> tuple[float, float]:
        a, b, c, d, e, g = self.coefficients
        return (a * stage_x + b * stage_y + c, d * stage_x + e * stage_y + g)


@dataclass(frozen=True, slots=True)
class ImageFrame:
    image_size: tuple[int, int]
    stage_position: tuple[float, float]
    pixel_size: float
    source: str
    stage_basis: tuple[tuple[float, float], tuple[float, float]] | None = None
    stage_basis_source: str | None = None
    # Exact stage->pixel affine for Atlas frames that ship a node table. When
    # present, ``_stage_to_image`` uses it directly and the legacy 180 degree
    # atlas-overlay rotation is skipped (the affine already encodes rotation,
    # flip, anisotropic scale and origin). ``None`` for every non-atlas frame
    # and for atlases without a usable node table (legacy fallback path).
    atlas_pixel_affine: AtlasPixelAffine | None = None


@dataclass(frozen=True, slots=True)
class AlignmentTransform:
    rotation_rad: float
    translation: tuple[float, float]
    source: str


@dataclass(frozen=True, slots=True)
class QueuedOverlayReference:
    """Acquisition geometry borrowed from successful positions in one scope.

    A queued batch has no exposure MRC of its own, so its Search XML optics
    describe the search state rather than the beam that would have acquired a
    tilt series.  Keep the replacement display-only and provenance-bearing:
    use a single consistent exposure-state beam/FOV observed on validator-
    complete positions in the same marker context, otherwise leave that field
    unresolved.
    """

    beam_diameter_m: float | None = None
    beam_source_batch_id: str | None = None
    beam_source: str | None = None
    camera_fov_m: tuple[float, float] | None = None
    camera_source_batch_id: str | None = None
    camera_source: str | None = None


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
        )
    if isinstance(source, BatchPosition):
        return batch_position_markers(source, tilt_series)
    return []


def atlas_markers(atlas: Atlas, context: MarkerContext) -> list[ImageMarker]:
    frame = _image_frame(atlas)
    if frame is None:
        # Surface the *reason* so the Session-tab warnings panel and the
        # Atlas-tab metadata can show "Atlas markers unavailable: …" instead
        # of silently rendering nothing. The user can then decide whether to
        # accept approximate placement or open a different session.
        reason = _frame_diagnostic(atlas)
        message = f"Atlas markers unavailable: {reason}"
        if message not in atlas.warnings:
            atlas.warnings.append(message)
        LOGGER.debug("Skipping Atlas overlays atlas=%s reason=%s", atlas.id, reason)
        return []
    atlas_alignment = _atlas_alignment_transform(atlas)
    search_map_by_id = {search_map.id: search_map for search_map in context.search_maps}
    markers: list[ImageMarker] = []
    for overview in context.overviews:
        alignment = (
            _overview_alignment_transform(overview, search_map_by_id)
            or atlas_alignment
        )
        marker = _overview_region_marker(
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
        alignment = _search_map_alignment_transform(search_map) or atlas_alignment
        markers.extend(
            _search_map_tile_markers(
                atlas.id,
                frame,
                search_map,
                show_label=False,
                allow_off_image=True,
                alignment_transform=alignment,
            )
        )
    for batch in context.batch_positions:
        marker = _batch_marker_in_frame(atlas.id, frame, batch)
        if marker is not None:
            markers.append(marker)
    if frame.atlas_pixel_affine is None:
        # Fallback only (atlas without a usable node table): the empirical 180
        # degree spin approximates the rotation/flip that the node-table affine
        # now encodes exactly. When the affine is in use the markers are already
        # in the correct atlas-pixel frame, so rotating again would re-introduce
        # the distance-dependent error this fix removes.
        _rotate_atlas_overlay_markers_180(
            markers,
            image_size=frame.image_size,
            source_id=atlas.id,
        )
    _filter_off_image_markers(markers, image_size=frame.image_size, source_id=atlas.id)
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
    frame = _image_frame(atlas)
    if frame is None:
        return base

    status_context = build_item_status_context(
        search_maps=context.search_maps,
        batch_positions=context.batch_positions,
        tilt_series=context.tilt_series,
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
            _as_str(find_first(batch.metadata, "SampleName"))
            or _as_str(find_first(batch.metadata, "Sample"))
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
        position = _position_on_tile_set(batch)
        if position is None:
            continue
        generated = _template_markers_in_frame(atlas.id, frame, batch, position)
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
    unattributed = _inferred_failed_tilt_markers(
        source=atlas,
        frame=frame,
        tilt_series=context.tilt_series,
        failed_tilt_ids=orphan_failed_ids,
        batch_positions=context.batch_positions,
    )
    for marker in unattributed:
        marker.status = STATUS_UNATTRIBUTED
        marker.label = _as_str(marker.metadata.get("batch_label")) or "?"
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
        _rotate_atlas_overlay_markers_180(
            newly_projected,
            image_size=frame.image_size,
            source_id=atlas.id,
        )
    _filter_off_image_markers(
        newly_projected,
        image_size=frame.image_size,
        source_id=atlas.id,
    )
    if include_detail_markers:
        return base + footprints + newly_projected
    return base + unattributed


def overview_markers(overview: Overview, context: MarkerContext) -> list[ImageMarker]:
    frame = _image_frame(overview)
    if frame is None:
        LOGGER.debug("Skipping Overview overlays without reliable frame metadata overview=%s", overview.id)
        return []
    status_context = build_item_status_context(
        search_maps=context.search_maps,
        batch_positions=context.batch_positions,
        tilt_series=context.tilt_series,
    )
    queued_reference = _queued_overlay_reference(
        context.batch_positions,
        status_context,
    )
    markers: list[ImageMarker] = []
    for search_map in context.search_maps:
        if search_map.overview is not overview and search_map.overview is not None and search_map.overview.id != overview.id:
            continue
        markers.extend(_search_map_tile_markers(overview.id, frame, search_map))
    _apply_grouped_overlay_position_correction(
        markers,
        image_size=frame.image_size,
        corrected_types={MarkerType.SEARCH_MAP},
        panel_name="Overview",
    )
    failed_set = context.failed_tilt_ids
    for batch in context.batch_positions:
        if batch.linked_overview_id is not None and batch.linked_overview_id != overview.id:
            continue
        marker = _batch_marker_in_frame(overview.id, frame, batch)
        if marker is not None:
            overlay_status = aggregate_batch_position_status(batch, status_context)
            batch_failed = any(tid in failed_set for tid in batch.linked_tilt_series_ids)
            if batch_failed:
                marker.status = "failed"
            elif overlay_status.status == STATUS_QUEUED:
                marker.status = STATUS_QUEUED
            markers.append(marker)
            template_markers = _template_markers_in_frame(
                overview.id,
                frame,
                batch,
                marker.metadata.get("stage_position"),
                queued=overlay_status.status == STATUS_QUEUED,
                queued_reference=queued_reference,
            )
            if batch_failed:
                _mark_markers_failed(template_markers)
            markers.extend(template_markers)

    # Same fallback as the SearchMap path: failed tilt series with
    # no recorded batch get an approximate marker derived from their
    # MRC extended-header stage position.
    markers.extend(
        _inferred_failed_tilt_markers(
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
) -> list[ImageMarker]:
    batch_positions = tuple(batch_positions)
    tilt_series = tuple(tilt_series)
    markers: list[ImageMarker] = []
    frame = _image_frame(search_map)
    failed_set = frozenset(failed_tilt_ids)
    status_context = build_item_status_context(
        search_maps=(search_map,),
        batch_positions=batch_positions,
        tilt_series=tilt_series,
    )
    queued_reference = _queued_overlay_reference(
        batch_positions,
        status_context,
    )
    for batch in batch_positions:
        if batch.linked_search_map_id != search_map.id:
            continue
        position = _position_on_tile_set(batch)
        if frame is None or position is None:
            LOGGER.debug("Skipping batch marker without complete search-map coordinates search_map=%s batch=%s", search_map.id, batch.id)
            if include_unresolved:
                markers.append(_unresolved_batch_marker(search_map, batch))
            continue
        width, height = frame.image_size
        x, y = _stage_to_image(frame, position)
        if not (0 <= x <= width and 0 <= y <= height):
            LOGGER.debug("Skipping off-image batch marker search_map=%s batch=%s x=%s y=%s", search_map.id, batch.id, x, y)
            if include_unresolved:
                markers.append(_unresolved_batch_marker(search_map, batch))
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
            tooltip=_batch_tooltip(batch),
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
        _apply_grouped_overlay_position_correction(
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
        template_markers = _template_markers_in_frame(
            search_map.id,
            frame,
            batch,
            position,
            queued=overlay_status.status == STATUS_QUEUED,
            queued_reference=queued_reference,
        )
        if batch_failed:
            _mark_markers_failed(template_markers)
        markers.extend(template_markers)

    # Failed tilt series whose batch was never recorded in
    # BatchPositionsList.xml don't get a marker via the loop above.
    # Fall back to the stage position parsed from the MRC extended
    # header so the user still sees an approximate failed location
    # on the search map.
    if frame is not None:
        markers.extend(
            _inferred_failed_tilt_markers(
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
    )
    if projected is not None:
        return projected

    frame = _image_frame(search_tile)
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
    position = _position_on_tile_set(batch) or _stage_position_from_metadata(search_tile.metadata.get("BatchSearch.xml"))
    if position is None:
        LOGGER.debug("Skipping Search tile overlays without batch stage position search_tile=%s batch=%s", search_tile.id, batch.id)
        return []

    failed_set = frozenset(failed_tilt_ids)
    status_context = build_item_status_context(
        search_maps=search_maps,
        search_tiles=(search_tile,),
        batch_positions=batch_positions,
        tilt_series=tilt_series,
    )
    overlay_status = aggregate_batch_position_status(batch, status_context)
    queued_reference = _queued_overlay_reference(batch_positions, status_context)
    batch_failed = any(tid in failed_set for tid in batch.linked_tilt_series_ids)
    x, y = _stage_to_image(frame, position)
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
            tooltip=_batch_tooltip(batch),
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

    template_markers = _template_markers_in_frame(
        search_tile.id,
        frame,
        batch,
        position,
        queued=overlay_status.status == STATUS_QUEUED,
        queued_reference=queued_reference,
    )
    if batch_failed:
        _mark_markers_failed(template_markers)
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
    tile_frame = _image_frame(search_tile)
    search_frame = _image_frame(search_map)
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


def _camera_fov_markers_from_exposures(
    batch: BatchPosition,
    markers: Iterable[ImageMarker],
    target_pixel_size: float | None,
    image_size: tuple[int, int] | None,
    *,
    source_id: str | None = None,
    queued: bool = False,
    queued_reference: QueuedOverlayReference | None = None,
) -> list[ImageMarker]:
    """Create acquisition camera-FOV rectangles for exposure markers.

    Tomography 5 batch XML records exposure positions as stage offsets, but
    the actual collected camera footprint is best derived from the exposure
    preview MRC dimensions and calibrated pixel size. This keeps the FOV tied
    to recorded acquisition metadata while leaving beam diameter handling
    unchanged; if the batch lacks BeamDiameter, renderers can fill this marker
    as the visual collected-region fallback.
    """

    if target_pixel_size is None or target_pixel_size <= 0 or image_size is None:
        return []
    dimensions = (
        _queued_camera_fov_dimensions(queued_reference, target_pixel_size)
        if queued
        else None
    ) or _camera_fov_dimensions(batch, target_pixel_size)
    if dimensions is None:
        return []
    width_px, height_px, width_m, height_m, source = dimensions
    if width_px <= 0 or height_px <= 0:
        return []
    has_beam_diameter = (
        _queued_beam_radius_pixels(queued_reference, target_pixel_size) is not None
        if queued
        else _beam_radius_pixels(batch, target_pixel_size) is not None
    )
    out: list[ImageMarker] = []
    for marker in markers:
        if marker.marker_type != MarkerType.EXPOSURE_AREA or marker.x is None or marker.y is None:
            continue
        bbox = (
            marker.x - width_px / 2,
            marker.y - height_px / 2,
            width_px,
            height_px,
        )
        if not _bbox_intersects(image_size, bbox):
            continue
        area_name = _as_str(marker.metadata.get("area_name")) or marker.label or "Exposure"
        fallback_note = (
            ""
            if has_beam_diameter
            else "\nBeam diameter unavailable; hollow queued camera FOV shown."
            if queued
            else "\nBeam diameter unavailable; filled camera FOV shown as collected-region fallback."
        )
        out.append(
            ImageMarker(
                id=f"{marker.id}:{MarkerType.CAMERA_FOV}",
                marker_type=MarkerType.CAMERA_FOV,
                linked_object_id=marker.linked_object_id,
                source_object_id=source_id or marker.source_object_id,
                x=marker.x,
                y=marker.y,
                bbox=bbox,
                label=None,
                tooltip=(
                    f"Camera FOV for {area_name}\n"
                    f"{width_m * 1e6:.2f} x {height_m * 1e6:.2f} µm"
                    f"{fallback_note}"
                ),
                status=STATUS_QUEUED if queued else marker.status,
                unresolved=False,
                metadata={
                    "batch_id": batch.id,
                    "area_name": area_name,
                    "image_size": image_size,
                    "coordinate_source": "exposure_camera_fov_from_mrc_metadata",
                    "camera_fov_source": source,
                    "camera_fov_size_px": (width_px, height_px),
                    "camera_fov_size_m": (width_m, height_m),
                    "has_beam_diameter": has_beam_diameter,
                    "filled_fallback": not has_beam_diameter and not queued,
                    "queued_position": queued,
                    "queued_fov_fallback": queued and not has_beam_diameter,
                    "queued_camera_source_batch_id": (
                        queued_reference.camera_source_batch_id
                        if queued and queued_reference is not None
                        else None
                    ),
                    "exposure_marker_id": marker.id,
                    "exposure_index": marker.metadata.get("exposure_index"),
                    "exposure_type": marker.metadata.get("exposure_type"),
                },
            )
        )
    return out


def _camera_fov_dimensions(
    batch: BatchPosition,
    target_pixel_size: float,
) -> tuple[float, float, float, float, str] | None:
    metadata = batch.exposure_mrc_metadata or batch.tracking_mrc_metadata
    source = "exposure MRC" if batch.exposure_mrc_metadata is not None else "tracking MRC"
    if metadata is None or metadata.nx is None or metadata.ny is None:
        return None
    source_pixel_size = _mrc_pixel_size(metadata)
    if source_pixel_size is None or source_pixel_size <= 0:
        return None
    width_m = metadata.nx * source_pixel_size
    height_m = metadata.ny * source_pixel_size
    return (
        width_m / target_pixel_size,
        height_m / target_pixel_size,
        width_m,
        height_m,
        source,
    )


def _queued_camera_fov_dimensions(
    reference: QueuedOverlayReference | None,
    target_pixel_size: float,
) -> tuple[float, float, float, float, str] | None:
    if (
        reference is None
        or reference.camera_fov_m is None
        or target_pixel_size <= 0
    ):
        return None
    width_m, height_m = reference.camera_fov_m
    if width_m <= 0 or height_m <= 0:
        return None
    source = reference.camera_source or "successful tilt-series exposure MRC"
    return (
        width_m / target_pixel_size,
        height_m / target_pixel_size,
        width_m,
        height_m,
        source,
    )


def _tile_bounds_in_search_map_frame(
    search_tile: SearchTile,
    search_frame: ImageFrame,
) -> tuple[float, float, float, float] | None:
    tile_stage = _stage_position(search_tile)
    tile_size = _image_size(search_tile)
    tile_pixel = _pixel_size(search_tile)
    if tile_stage is None or tile_size is None or tile_pixel is None:
        return None
    # Search Map overlays are rendered after the display-space reflection
    # correction. Convert the tile centre into that same corrected full-map
    # coordinate system, then derive the tile rectangle at Search Map scale.
    tile_center = reflect_point_through_image_center(
        *_stage_to_image(search_frame, tile_stage),
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
    if bbox is not None and _bbox_intersects(image_size, bbox):
        return True
    if polygon:
        return any(0 <= px <= width and 0 <= py <= height for px, py in polygon)
    return False


def _exposure_marker_radius(
    batch_positions: Iterable[BatchPosition], pixel_size: float | None
) -> float:
    """Pick a radius for inferred-failed markers that visually
    matches the exposure-area overlays on the same frame.

    Walks the available batch positions in order, returning the
    first parseable beam-diameter-derived radius (so a sample with
    real ``BeamDiameter`` metadata uses the same beam size for the
    inferred marker that the templated exposures already use). Falls
    back to ``_area_radius(EXPOSURE_AREA)`` so the marker is at least
    as visible as a default-sized exposure overlay when no beam
    metadata is on disk.
    """

    for batch in batch_positions:
        radius = _beam_radius_pixels(batch, pixel_size)
        if radius is not None:
            return radius
    return _area_radius(MarkerType.EXPOSURE_AREA)


def _mark_markers_failed(markers: Iterable[ImageMarker]) -> None:
    """Tag every marker with ``status = "failed"`` so renderers paint
    them in the red status colour. Used when the batch the markers
    came from has at least one validator-flagged failed tilt series.
    """

    for marker in markers:
        marker.status = "failed"


def _inferred_failed_tilt_markers(
    *,
    source: Atlas | SearchMap | Overview,
    frame: ImageFrame,
    tilt_series: Iterable[TiltSeries],
    failed_tilt_ids: frozenset[str],
    batch_positions: Iterable[BatchPosition],
) -> list[ImageMarker]:
    """Approximate location markers for failed orphaned tilt series.

    A failed tilt series whose batch position never made it into
    ``BatchPositionsList.xml`` (e.g. the Sang ``sang_1`` /
    ``sang_1_2`` failures) doesn't reach the regular batch-driven
    marker code path. We can still locate them on the SearchMap /
    Overview by reading the stage position from the tilt series'
    MRC extended-header metadata and projecting it through the same
    ``_stage_to_image`` + reflection pipeline the batch dot uses.

    The result carries ``metadata['inferred_from_stage'] = True`` so
    the GUI / PDF renderers can dash-stroke it, signalling that the
    location is approximate rather than recorded.

    Falls through silently when:
    * the tilt series isn't in the failed set,
    * its batch *is* recorded (so the regular path already emitted a
      marker — we don't double up),
    * no stage position can be parsed from the MRC header, or
    * the projected coordinate falls outside the frame.
    """

    if not failed_tilt_ids:
        return []
    recorded_batch_ids = {bp.id for bp in batch_positions}
    width, height = frame.image_size
    # Match the inferred marker's radius to the exposure-area markers
    # rendered for sibling batches on the same frame. Use the first
    # batch whose BeamDiameter parses at this frame's pixel size, and
    # fall back to the default exposure-area radius so the marker is
    # always at least as large as the templated overlays — the
    # previous fixed radius=12 was hard to spot at typical search-map
    # zoom levels.
    radius = _exposure_marker_radius(batch_positions, frame.pixel_size)
    out: list[ImageMarker] = []
    for tilt in tilt_series:
        if tilt.id not in failed_tilt_ids:
            continue
        # Skip tilts whose batch *is* recorded — they're already
        # placed via the regular batch-marker loop.
        if (
            tilt.linked_batch_position_id is not None
            and tilt.linked_batch_position_id in recorded_batch_ids
        ):
            continue
        position = _mrc_stage_position(tilt.mrc_metadata)
        if position is None:
            LOGGER.debug(
                "inferred-failed marker skipped: no stage position tilt=%s source=%s",
                tilt.id,
                getattr(source, "id", None),
            )
            continue
        x, y = _stage_to_image(frame, position)
        if not (0 <= x <= width and 0 <= y <= height):
            # Stage position falls outside this image. Expected when
            # the same failed tilt's stage projects outside one
            # SearchMap (we still iterate every SearchMap so the
            # right one is picked up).
            continue
        marker = ImageMarker(
            id=f"{getattr(source, 'id', 'frame')}:inferred-failed:{tilt.id}",
            marker_type=MarkerType.TILT_SERIES,
            linked_object_id=tilt.id,
            source_object_id=getattr(source, "id", None),
            x=x,
            y=y,
            radius=radius,
            label=tilt.name,
            tooltip=(
                f"Failed tilt series: {tilt.name}\n"
                f"Approximate location inferred from MRC stage metadata."
            ),
            status="failed",
            unresolved=False,
            metadata={
                "image_size": frame.image_size,
                "coordinate_source": "mrc_extended_header_stage",
                "stage_position": position,
                "inferred_from_stage": True,
                "batch_label": compact_label_for_tilt(tilt),
            },
        )
        # SearchMap / Overview overlays use the legacy display-space
        # reflection applied to their regular batch/template markers. Atlas
        # frames own their transform separately: a node-table affine already
        # contains rotation and handedness, while the legacy Atlas path applies
        # one final 180-degree rotation in ``atlas_lod_markers``. Reflecting
        # here would therefore mirror node-affine markers or double-rotate
        # legacy Atlas markers.
        if not isinstance(source, Atlas):
            _apply_grouped_overlay_position_correction(
                [marker],
                image_size=frame.image_size,
                corrected_types={MarkerType.TILT_SERIES},
                panel_name=frame.source,
            )
        out.append(marker)
        LOGGER.info(
            "inferred-failed tilt marker: source=%s tilt=%s stage=%s image=(%s,%s)",
            getattr(source, "id", None),
            tilt.id,
            position,
            marker.x,
            marker.y,
        )
    return out


def batch_position_markers(batch: BatchPosition, tilt_series: Iterable[TiltSeries]) -> list[ImageMarker]:
    markers = _template_area_markers(batch)
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


def _template_area_markers(batch: BatchPosition) -> list[ImageMarker]:
    metadata = batch.search_mrc_metadata or batch.tracking_mrc_metadata or batch.exposure_mrc_metadata
    pixel_size = _mrc_pixel_size(metadata)
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
                _as_str(find_first(raw, "Name")) or f"Exposure {index}",
                width,
                height,
                pixel_size,
                exposure_index=index,
                exposure_type="additional",
            )
            if marker is not None:
                markers.append(marker)
    markers.extend(_camera_fov_markers_from_exposures(batch, markers, pixel_size, (width, height) if width is not None and height is not None else None))
    return markers


def _template_markers_in_frame(
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
            label = _as_str(find_first(raw, "Name")) or f"Exposure {index}"
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
    corrected_types = _corrected_area_types_for_frame(frame)
    _apply_grouped_overlay_position_correction(
        markers,
        image_size=frame.image_size,
        corrected_types=corrected_types,
        panel_name=frame.source,
        default_central_name=batch.name or batch.id,
    )
    _correct_focus_tracking_orientation(
        markers,
        frame=frame,
        batch=batch,
    )
    _filter_off_image_template_markers(
        markers,
        image_size=frame.image_size,
        source_id=source_id,
        batch=batch,
    )
    camera_markers = _camera_fov_markers_from_exposures(
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
        and _queued_beam_radius_pixels(queued_reference, frame.pixel_size) is None
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
    position_x = _as_float(raw.get("PositionX"))
    position_y = _as_float(raw.get("PositionY"))
    if position_x is None or position_y is None:
        return None
    area_name = _as_str(find_first(raw, "Name")) or label
    stage_x = batch_stage_position[0] + position_x
    stage_y = batch_stage_position[1] + position_y
    x, y = _stage_to_image(frame, (stage_x, stage_y))
    width, height = frame.image_size
    defer_bounds_check = marker_type in _corrected_area_types_for_frame(frame)
    if not (0 <= x <= width and 0 <= y <= height) and not defer_bounds_check:
        LOGGER.debug("Skipping off-image template marker source=%s batch=%s type=%s x=%s y=%s", source_id, batch.id, marker_type, x, y)
        return None
    beam_radius = (
        _queued_beam_radius_pixels(queued_reference, frame.pixel_size)
        if queued
        else _beam_radius_pixels(batch, frame.pixel_size)
    )
    radius = beam_radius or _area_radius(marker_type)
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


def _corrected_area_types_for_frame(frame: ImageFrame) -> set[str]:
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


def _filter_off_image_template_markers(
    markers: list[ImageMarker],
    *,
    image_size: tuple[int, int],
    source_id: str,
    batch: BatchPosition,
) -> None:
    kept: list[ImageMarker] = []
    for marker in markers:
        if _marker_intersects_image(image_size, marker):
            kept.append(marker)
            continue
        LOGGER.debug(
            "Skipping off-image corrected template marker source=%s batch=%s type=%s id=%s",
            source_id,
            batch.id,
            marker.marker_type,
            marker.id,
        )
    markers[:] = kept


def _filter_off_image_markers(
    markers: list[ImageMarker],
    *,
    image_size: tuple[int, int],
    source_id: str,
) -> None:
    kept: list[ImageMarker] = []
    for marker in markers:
        if _marker_intersects_image(image_size, marker):
            kept.append(marker)
            continue
        LOGGER.debug(
            "Skipping off-image corrected marker source=%s type=%s id=%s",
            source_id,
            marker.marker_type,
            marker.id,
        )
    markers[:] = kept


def _rotate_atlas_overlay_markers_180(
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
        before = _marker_center(marker)
        if before is None:
            if marker.polygon:
                marker.polygon = [
                    rotate_point_180_around_anchor(x, y, anchor[0], anchor[1])
                    for x, y in marker.polygon
                ]
            continue
        after = rotate_point_180_around_anchor(before[0], before[1], anchor[0], anchor[1])
        _move_marker_center(marker, after)
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


def _apply_grouped_overlay_position_correction(
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
        if marker.marker_type in corrected_types and _marker_center(marker) is not None
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
        current_center = _marker_center(marker)
        if current_center is None:
            continue
        reflected_center = reflect_point_through_image_center(current_center[0], current_center[1], image_size[0], image_size[1])
        _move_marker_center(marker, reflected_center)
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
        central_center = _marker_center(central)
        marker_center = _marker_center(marker)
        if central is marker or central_center is None or marker_center is None:
            continue
        local_center = rotate_point_180_around_anchor(marker_center[0], marker_center[1], central_center[0], central_center[1])
        _move_marker_center(marker, local_center)
        existing_transform = _as_str(marker.metadata.get("legacy_overlay_transform")) or "global_reflection"
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


def _correct_focus_tracking_orientation(
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
    anchor = _marker_center(primary)
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
        before = _marker_center(marker)
        if before is None:
            continue
        after = rotate_point_180_around_anchor(
            before[0],
            before[1],
            anchor[0],
            anchor[1],
        )
        _move_marker_center(marker, after)
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
    area_name = _as_str(marker.metadata.get("area_name"))
    if area_name and area_name not in {"Exposure", "Focus", "Tracking"}:
        return area_name
    if default_central_name:
        return default_central_name
    return marker.label or _as_str(marker.metadata.get("item_name")) or marker.linked_object_id or marker.id


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


def _atlas_alignment_transform(atlas: Atlas) -> AlignmentTransform | None:
    return _alignment_transform_from_metadata(
        atlas.metadata.get("Atlas.dm") if isinstance(atlas.metadata, dict) else None,
        "AtlasAlignmentTransformation",
        "AtlasAlignmentTransformation",
    )


def _search_map_alignment_transform(search_map: SearchMap) -> AlignmentTransform | None:
    return _alignment_transform_from_metadata(
        search_map.metadata.get("SearchMap.dm") if isinstance(search_map.metadata, dict) else None,
        "BackwardAlignmentTransformation",
        "BackwardAlignmentTransformation",
    )


def _overview_alignment_transform(
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
        transform = _search_map_alignment_transform(search_map)
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


def _apply_alignment_transform(
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


def _marker_center(marker: ImageMarker) -> tuple[float, float] | None:
    if marker.x is not None and marker.y is not None:
        return marker.x, marker.y
    if marker.bbox is not None:
        x, y, width, height = marker.bbox
        return x + width / 2, y + height / 2
    return None


def _move_marker_center(marker: ImageMarker, center: tuple[float, float]) -> None:
    if marker.x is not None and marker.y is not None:
        marker.x, marker.y = center
        return
    if marker.bbox is not None:
        _, _, width, height = marker.bbox
        marker.bbox = (center[0] - width / 2, center[1] - height / 2, width, height)


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
    if main is None:
        return []
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
        area_name = _as_str(marker.metadata.get("area_name")) or marker.id
        linked.append(
            ImageMarker(
                id=f"{source_id}:batch:{batch.id}:link:{area_name}",
                marker_type=MarkerType.LINK_LINE,
                linked_object_id=batch.id,
                source_object_id=source_id,
                polygon=[(main.x, main.y), (marker.x, marker.y)],
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
    position_x = _as_float(raw.get("PositionX"))
    position_y = _as_float(raw.get("PositionY"))
    x: float | None = None
    y: float | None = None
    unresolved = True
    if width is not None and height is not None and pixel_size is not None and position_x is not None and position_y is not None:
        x = (width / 2) + (position_x / pixel_size)
        y = (height / 2) - (position_y / pixel_size)
        unresolved = not (0 <= x <= width and 0 <= y <= height)
    radius = _beam_radius_pixels(batch, pixel_size) or _area_radius(marker_type)
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
        "radius_source": "BeamDiameter" if _beam_radius_pixels(batch, pixel_size) is not None else "default",
        "area_name": _as_str(find_first(raw, "Name")) or label,
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


def _unresolved_batch_marker(search_map: SearchMap, batch: BatchPosition) -> ImageMarker:
    return ImageMarker(
        id=f"{search_map.id}:batch:{batch.id}",
        marker_type=MarkerType.BATCH_POSITION,
        linked_object_id=batch.id,
        source_object_id=search_map.id,
        radius=10,
        label=batch.name or batch.id,
        tooltip=f"{_batch_tooltip(batch)}\nCoordinates unavailable",
        status=batch.status,
        unresolved=True,
        metadata={"search_map_name": search_map.name},
    )


def _overview_region_marker(
    source_id: str,
    target_frame: ImageFrame,
    overview: Overview,
    *,
    show_label: bool = True,
    allow_off_image: bool = False,
    alignment_transform: AlignmentTransform | None = None,
) -> ImageMarker | None:
    source_frame = _image_frame(overview)
    if source_frame is None:
        LOGGER.debug("Skipping overview region without source frame source=%s overview=%s", source_id, overview.id)
        return None
    stage_position = _apply_alignment_transform(
        source_frame.stage_position,
        alignment_transform,
        origin=target_frame.stage_position,
    )
    center_x, center_y = _stage_to_image(target_frame, stage_position)
    source_width, source_height = source_frame.image_size
    width = (source_width * source_frame.pixel_size) / target_frame.pixel_size
    height = (source_height * source_frame.pixel_size) / target_frame.pixel_size
    bbox = (center_x - width / 2, center_y - height / 2, width, height)
    if not allow_off_image and not _bbox_intersects(target_frame.image_size, bbox):
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


def _search_map_tile_markers(
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
    source_frame = _image_frame(search_map)
    if source_frame is None:
        LOGGER.debug("Skipping search-map region without source frame source=%s search_map=%s", source_id, search_map.id)
        return None
    stage_position = _apply_alignment_transform(
        source_frame.stage_position,
        alignment_transform,
        origin=target_frame.stage_position,
    )
    center_x, center_y = _stage_to_image(target_frame, stage_position)
    source_width, source_height = source_frame.image_size
    width = (source_width * source_frame.pixel_size) / target_frame.pixel_size
    height = (source_height * source_frame.pixel_size) / target_frame.pixel_size
    bbox = (center_x - width / 2, center_y - height / 2, width, height)
    if not allow_off_image and not _bbox_intersects(target_frame.image_size, bbox):
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


def _batch_marker_in_frame(source_id: str, frame: ImageFrame, batch: BatchPosition) -> ImageMarker | None:
    position = _position_on_tile_set(batch)
    if position is None:
        LOGGER.debug("Skipping batch marker without stage position source=%s batch=%s", source_id, batch.id)
        return None
    x, y = _stage_to_image(frame, position)
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
        tooltip=_batch_tooltip(batch),
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
    source_pixel_size = _mrc_pixel_size(metadata)
    source_width = metadata.nx if metadata else None
    source_height = metadata.ny if metadata else None
    if rect is None or source_pixel_size is None or source_width is None or source_height is None:
        return None
    x, y, width, height = rect
    center_offset_x = ((x + width / 2) - source_width / 2) * source_pixel_size
    center_offset_y = -(((y + height / 2) - source_height / 2) * source_pixel_size)
    center = (batch_stage_position[0] + center_offset_x, batch_stage_position[1] + center_offset_y)
    center_x, center_y = _stage_to_image(frame, center)
    target_width = width * source_pixel_size / frame.pixel_size
    target_height = height * source_pixel_size / frame.pixel_size
    bbox = (center_x - target_width / 2, center_y - target_height / 2, target_width, target_height)
    defer_bounds_check = MarkerType.TEMPLATE_AREA in _corrected_area_types_for_frame(frame)
    if not _bbox_intersects(frame.image_size, bbox) and not defer_bounds_check:
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
    x = _as_float(raw.get("x"))
    y = _as_float(raw.get("y"))
    width = _as_float(raw.get("width"))
    height = _as_float(raw.get("height"))
    if x is None or y is None or width is None or height is None or width <= 0 or height <= 0:
        return None
    return (x, y, width, height)


def _area_radius(marker_type: str) -> float:
    if marker_type == MarkerType.EXPOSURE_AREA:
        return 18.0
    if marker_type == MarkerType.FOCUS_AREA:
        return 16.0
    if marker_type == MarkerType.TRACKING_AREA:
        return 16.0
    if marker_type == MarkerType.CONDITION_AREA:
        return 16.0
    return 12.0


def _queued_overlay_reference(
    batch_positions: Iterable[BatchPosition],
    status_context: ItemStatusContext,
) -> QueuedOverlayReference:
    """Resolve one consistent successful-acquisition geometry reference.

    Only explicitly linked, validator-complete tilt series qualify a batch as
    a source.  If successful batches disagree on beam diameter or physical
    camera field, the corresponding value remains unresolved rather than
    choosing the first candidate.
    """

    successful: list[BatchPosition] = []
    for batch in batch_positions:
        counts = batch_position_status_counts(
            batch,
            status_context,
            include_inferred=False,
        )
        if counts.complete > 0:
            successful.append(batch)
    successful.sort(key=lambda batch: (batch.name or batch.id).casefold())

    beam_candidates: list[tuple[BatchPosition, float]] = []
    camera_candidates: list[tuple[BatchPosition, float, float]] = []
    for batch in successful:
        context = _as_str(batch.metadata.get("BeamDiameterContext"))
        diameter = _as_float(find_first(batch.metadata, "BeamDiameter"))
        if (
            context == BEAM_DIAMETER_EXPOSURE_MRC_CONTEXT
            and diameter is not None
            and diameter > 0
        ):
            beam_candidates.append((batch, diameter))

        metadata = batch.exposure_mrc_metadata
        pixel_size = _mrc_pixel_size(metadata)
        if (
            metadata is not None
            and metadata.nx is not None
            and metadata.ny is not None
            and metadata.nx > 0
            and metadata.ny > 0
            and pixel_size is not None
            and pixel_size > 0
        ):
            camera_candidates.append(
                (
                    batch,
                    metadata.nx * pixel_size,
                    metadata.ny * pixel_size,
                )
            )

    beam_batch: BatchPosition | None = None
    beam_diameter_m: float | None = None
    if beam_candidates and _consistent_values(
        [diameter for _, diameter in beam_candidates]
    ):
        beam_batch, beam_diameter_m = beam_candidates[0]

    camera_batch: BatchPosition | None = None
    camera_fov_m: tuple[float, float] | None = None
    if camera_candidates and _consistent_values(
        [width for _, width, _ in camera_candidates]
    ) and _consistent_values([height for _, _, height in camera_candidates]):
        camera_batch, width_m, height_m = camera_candidates[0]
        camera_fov_m = (width_m, height_m)

    return QueuedOverlayReference(
        beam_diameter_m=beam_diameter_m,
        beam_source_batch_id=beam_batch.id if beam_batch is not None else None,
        beam_source=(
            _as_str(beam_batch.metadata.get("BeamDiameterSource"))
            if beam_batch is not None
            else None
        ),
        camera_fov_m=camera_fov_m,
        camera_source_batch_id=(
            camera_batch.id if camera_batch is not None else None
        ),
        camera_source=(
            f"{camera_batch.exposure_image_path.name} exposure MRC"
            if camera_batch is not None
            and camera_batch.exposure_image_path is not None
            else "successful tilt-series exposure MRC"
            if camera_batch is not None
            else None
        ),
    )


def _consistent_values(values: list[float]) -> bool:
    if not values:
        return False
    reference = values[0]
    return all(
        math.isclose(value, reference, rel_tol=1e-4, abs_tol=1e-12)
        for value in values[1:]
    )


def _queued_beam_radius_pixels(
    reference: QueuedOverlayReference | None,
    pixel_size: float | None,
) -> float | None:
    if (
        reference is None
        or reference.beam_diameter_m is None
        or reference.beam_diameter_m <= 0
        or pixel_size is None
        or pixel_size <= 0
    ):
        return None
    return max(3.0, (reference.beam_diameter_m / 2.0) / pixel_size)


def _beam_radius_pixels(batch: BatchPosition, pixel_size: float | None) -> float | None:
    if pixel_size is None or pixel_size <= 0:
        return None
    diameter_value = _as_float(find_first(batch.metadata, "BeamDiameter"))
    if diameter_value is None or diameter_value <= 0:
        return None
    context = _as_str(batch.metadata.get("BeamDiameterContext"))
    radius_m = _beam_diameter_to_meters(diameter_value, context=context)
    if radius_m is None:
        LOGGER.debug("Unsupported BeamDiameter value batch=%s value=%s", batch.id, diameter_value)
        return None
    # The low-dose optics value used by the reference collection overlay is named
    # ``BeamDiameter`` in XML but behaves as the rendered radius after unit
    # conversion. Keep marker.radius in that same display convention.
    return max(3.0, radius_m / pixel_size)


def _beam_diameter_to_meters(value: float, *, context: str | None = None) -> float | None:
    if value <= 0:
        return None
    if context == BEAM_DIAMETER_EXPOSURE_MRC_CONTEXT:
        # The FEI extended header records the actual exposure-state beam
        # diameter in meters at the specimen plane. Convert directly to the
        # rendered radius — no empirical scaling required.
        return value / 2
    if context == BEAM_DIAMETER_LOW_DOSE_CONTEXT:
        return value * 1e-3
    if context == BEAM_DIAMETER_MICROSCOPE_CONTEXT:
        # Some reference search XML lacks the low-dose
        # BackwardAlignmentTransformation optics block used for
        # the visible exposure overlay. The remaining microscopeData optics
        # BeamDiameter is roughly 10x larger than the reference low-dose display
        # radius for the same acquisition family, so use it only as a scaled
        # fallback instead of applying the millimetre heuristic below.
        return value * MICROSCOPE_DATA_BEAM_DISPLAY_SCALE
    if value > 1e-5:
        # Tomography metadata stores BeamDiameter in millimetres in observed TFS XML.
        return value * 1e-3
    return value


# Minimum number of distinct atlas tile nodes required before we trust a fitted
# stage->pixel affine. Three non-degenerate points define an affine map; we ask
# for a few more so a single mis-recorded tile cannot dominate the least-squares
# fit. Atlases below this fall back to the legacy basis + 180 degree path.
_MIN_ATLAS_NODE_SAMPLES = 4
# If the fitted affine cannot reproduce the recorded node pixels to within this
# many pixels we treat the table as untrustworthy and fall back. A clean
# Tomography 5 atlas fits to well under 1 px.
_MAX_ATLAS_NODE_RESIDUAL_PX = 8.0


def _atlas_pixel_affine(atlas: Atlas) -> AtlasPixelAffine | None:
    """Fit the exact stage->atlas-pixel affine from the Atlas.dm node table.

    Tomography 5's ``Atlas.dm`` stores one node per acquired atlas tile, each
    pairing the tile's microscope ``StagePosition`` (metres) with its
    ``AtlasPixelPosition`` rectangle (pixels in the stitched mosaic). That table
    is the microscope's own record of how stage coordinates map onto the atlas
    image, so fitting an affine to it yields the true rotation, anisotropic
    scale, handedness and origin in a single transform.

    Returns ``None`` (legacy fallback) when the table is missing, too small, or
    fits too poorly to trust.
    """

    metadata = atlas.metadata.get("Atlas.dm") if isinstance(atlas.metadata, dict) else None
    if not isinstance(metadata, dict):
        return None
    pairs = _collect_atlas_node_pairs(metadata)
    if len(pairs) < _MIN_ATLAS_NODE_SAMPLES:
        if pairs:
            LOGGER.debug(
                "Atlas node table too small for affine fit atlas=%s nodes=%d", atlas.id, len(pairs)
            )
        return None
    fit = _fit_affine_least_squares(pairs)
    if fit is None:
        LOGGER.debug("Atlas node table affine fit was degenerate atlas=%s", atlas.id)
        return None
    coefficients, residual_px = fit
    if residual_px > _MAX_ATLAS_NODE_RESIDUAL_PX:
        LOGGER.warning(
            "Atlas node table affine residual too high; using legacy projection "
            "atlas=%s residual=%.2fpx nodes=%d",
            atlas.id,
            residual_px,
            len(pairs),
        )
        return None

    # The montage frame the AtlasPixelPosition values live in is the atlas image
    # itself: its extent is the far corner of the furthest tile rectangle. This
    # becomes the marker ``image_size`` so the viewer/report scale overlays onto
    # the displayed atlas pixmap correctly (it differs from the camera
    # ``ReadoutArea`` that ``_image_size`` reports for the atlas).
    montage_w = max(rect[0] + rect[2] for _, rect in pairs)
    montage_h = max(rect[1] + rect[3] for _, rect in pairs)
    image_size = (int(round(montage_w)), int(round(montage_h)))

    a, b, _c, d, e, _g = coefficients
    pixel_size = (math.hypot(a, d) ** -1 + math.hypot(b, e) ** -1) / 2.0

    return AtlasPixelAffine(
        coefficients=coefficients,
        image_size=image_size,
        pixel_size=pixel_size,
        source="Atlas.dm:StagePosition->AtlasPixelPosition",
        residual_px=residual_px,
        sample_count=len(pairs),
    )


def _collect_atlas_node_pairs(
    metadata: dict[str, Any],
) -> list[tuple[tuple[float, float], tuple[float, float, float, float]]]:
    """Walk parsed Atlas.dm and return ``(stage_xy_m, pixel_rect)`` per tile.

    ``pixel_rect`` is ``(x, y, width, height)`` of the tile's
    ``AtlasPixelPosition`` (top-left origin); callers use the rectangle centre
    as the point paired with the stage coordinate.
    """

    pairs: list[tuple[tuple[float, float], tuple[float, float, float, float]]] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            stage = node.get("StagePosition")
            pixel = node.get("AtlasPixelPosition")
            if isinstance(stage, dict) and isinstance(pixel, dict):
                sx = _as_float(find_first(stage, "X"))
                sy = _as_float(find_first(stage, "Y"))
                px = _as_float(find_first(pixel, "x"))
                py = _as_float(find_first(pixel, "y"))
                pw = _as_float(find_first(pixel, "width"))
                ph = _as_float(find_first(pixel, "height"))
                if None not in (sx, sy, px, py, pw, ph):
                    pairs.append(((sx, sy), (px, py, pw, ph)))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(metadata)
    return pairs


def _fit_affine_least_squares(
    pairs: list[tuple[tuple[float, float], tuple[float, float, float, float]]],
) -> tuple[tuple[float, float, float, float, float, float], float] | None:
    """Least-squares fit ``pixel_centre = A @ [stage_x, stage_y, 1]``.

    Pure-Python normal-equations solve (3 unknowns per axis) so the service
    layer stays free of a hard numpy dependency. Returns the six affine
    coefficients ``(a, b, c, d, e, g)`` and the worst per-node residual in
    pixels, or ``None`` if the stage points are collinear/degenerate.
    """

    # Build the 3x3 normal matrix N = MᵀM and right-hand sides for x and y, where
    # each row of M is [stage_x, stage_y, 1] and the targets are the tile-centre
    # pixel coordinates.
    n11 = n12 = n13 = n22 = n23 = n33 = 0.0
    bx1 = bx2 = bx3 = 0.0
    by1 = by2 = by3 = 0.0
    points: list[tuple[float, float, float, float]] = []
    for (sx, sy), (px, py, pw, ph) in pairs:
        cx = px + pw / 2.0
        cy = py + ph / 2.0
        points.append((sx, sy, cx, cy))
        n11 += sx * sx
        n12 += sx * sy
        n13 += sx
        n22 += sy * sy
        n23 += sy
        n33 += 1.0
        bx1 += sx * cx
        bx2 += sy * cx
        bx3 += cx
        by1 += sx * cy
        by2 += sy * cy
        by3 += cy

    normal = [
        [n11, n12, n13],
        [n12, n22, n23],
        [n13, n23, n33],
    ]
    coef_x = _solve_3x3(normal, [bx1, bx2, bx3])
    coef_y = _solve_3x3(normal, [by1, by2, by3])
    if coef_x is None or coef_y is None:
        return None

    a, b, c = coef_x
    d, e, g = coef_y
    residual = 0.0
    for sx, sy, cx, cy in points:
        qx = a * sx + b * sy + c
        qy = d * sx + e * sy + g
        residual = max(residual, math.hypot(qx - cx, qy - cy))
    return (a, b, c, d, e, g), residual


def _solve_3x3(matrix: list[list[float]], rhs: list[float]) -> tuple[float, float, float] | None:
    """Solve a 3x3 linear system by Gaussian elimination with partial pivoting."""

    augmented = [list(row) + [rhs[index]] for index, row in enumerate(matrix)]
    for column in range(3):
        pivot_row = max(range(column, 3), key=lambda r: abs(augmented[r][column]))
        if abs(augmented[pivot_row][column]) < 1e-30:
            return None
        augmented[column], augmented[pivot_row] = augmented[pivot_row], augmented[column]
        pivot = augmented[column][column]
        for r in range(3):
            if r == column:
                continue
            factor = augmented[r][column] / pivot
            for k in range(column, 4):
                augmented[r][k] -= factor * augmented[column][k]
    return (
        augmented[0][3] / augmented[0][0],
        augmented[1][3] / augmented[1][1],
        augmented[2][3] / augmented[2][2],
    )


def _stage_to_image(frame: ImageFrame, stage_position: tuple[float, float]) -> tuple[float, float]:
    if frame.atlas_pixel_affine is not None:
        # Atlas frame with a recovered node-table affine: this single transform
        # already carries rotation, handedness flip, anisotropic scale and the
        # true origin, so apply it directly in the montage pixel frame. No image
        # centring and no 180 degree post-rotation are needed (or wanted).
        return frame.atlas_pixel_affine.apply(stage_position[0], stage_position[1])
    width, height = frame.image_size
    delta_x = stage_position[0] - frame.stage_position[0]
    delta_y = stage_position[1] - frame.stage_position[1]
    if frame.stage_basis is not None:
        x_axis, y_axis = frame.stage_basis
        determinant = (x_axis[0] * y_axis[1]) - (x_axis[1] * y_axis[0])
        if abs(determinant) > 1e-24:
            image_delta_x = ((delta_x * y_axis[1]) - (delta_y * y_axis[0])) / determinant
            image_delta_y = ((x_axis[0] * delta_y) - (x_axis[1] * delta_x)) / determinant
            return (width / 2) + image_delta_x, (height / 2) + image_delta_y
    x = (width / 2) + (delta_x / frame.pixel_size)
    y = (height / 2) - (delta_y / frame.pixel_size)
    return x, y


def reflect_point_through_image_center(
    x: float,
    y: float,
    image_width: int,
    image_height: int,
) -> tuple[float, float]:
    """Experimental correction for Tomography 5 Search Map exposure overlays.

    The generated exposure marker positions appear to be related to the correct
    positions by a point reflection through the image centre.
    """
    reflected_x = image_width - 1 - x
    reflected_y = image_height - 1 - y
    return reflected_x, reflected_y


def rotate_point_180_around_anchor(
    x: float,
    y: float,
    anchor_x: float,
    anchor_y: float,
) -> tuple[float, float]:
    """Rotate a point 180 degrees around an anchor point.

    Used to correct linked Tomography 5 exposure positions around their central
    exposure tile.
    """
    return 2.0 * anchor_x - x, 2.0 * anchor_y - y


def _bbox_intersects(image_size: tuple[int, int], bbox: tuple[float, float, float, float]) -> bool:
    image_width, image_height = image_size
    x, y, width, height = bbox
    return x + width >= 0 and y + height >= 0 and x <= image_width and y <= image_height


def _marker_intersects_image(image_size: tuple[int, int], marker: ImageMarker) -> bool:
    if marker.bbox is not None:
        return _bbox_intersects(image_size, marker.bbox)
    if marker.polygon:
        xs = [point[0] for point in marker.polygon]
        ys = [point[1] for point in marker.polygon]
        return _bbox_intersects(
            image_size,
            (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)),
        )
    if marker.x is None or marker.y is None:
        return False
    radius = max(marker.radius or 0.0, 0.0)
    return (
        marker.x + radius >= 0
        and marker.y + radius >= 0
        and marker.x - radius <= image_size[0]
        and marker.y - radius <= image_size[1]
    )


def _frame_diagnostic(source: Atlas | Overview | SearchMap | SearchTile) -> str:
    """Return a human-readable description of which frame field is missing."""

    issues: list[str] = []
    if _image_size(source) is None:
        issues.append("image dimensions unknown")
    if _stage_position(source) is None:
        issues.append("no stage position recorded")
    if _pixel_size(source) is None:
        issues.append("pixel size unknown")
    if not issues:
        return "frame metadata could not be assembled"
    return ", ".join(issues)


def _image_frame(source: Atlas | Overview | SearchMap | SearchTile) -> ImageFrame | None:
    image_size = _image_size(source)
    stage_position = _stage_position(source)
    pixel_size = _pixel_size(source)
    if image_size is None or stage_position is None or pixel_size is None or pixel_size <= 0:
        LOGGER.debug(
            "Missing image frame metadata source=%s image_size=%s stage=%s pixel=%s",
            getattr(source, "id", None),
            image_size,
            stage_position,
            pixel_size,
        )
        return None

    # Detailed frame diagnostics are useful when investigating projection
    # units, but are too noisy for normal session-level INFO logging.
    pixel_um = pixel_size * 1e6
    fov_x_um = image_size[0] * pixel_um
    fov_y_um = image_size[1] * pixel_um
    LOGGER.debug(
        "image frame: type=%s id=%s image_size=%dx%d px metadata_pixel_size=%.6e m/px "
        "(=%.4f µm/px) stage_position=(%.6e, %.6e) m fov=%.1fx%.1f µm",
        type(source).__name__,
        getattr(source, "id", "<unknown>"),
        image_size[0],
        image_size[1],
        pixel_size,
        pixel_um,
        stage_position[0],
        stage_position[1],
        fov_x_um,
        fov_y_um,
    )

    stage_basis, stage_basis_source = _stage_basis(source, pixel_size)
    if stage_basis is not None:
        LOGGER.debug(
            "image frame stage basis: type=%s id=%s source=%s "
            "x_axis=(%.6e, %.6e) m/px y_axis=(%.6e, %.6e) m/px",
            type(source).__name__,
            getattr(source, "id", "<unknown>"),
            stage_basis_source,
            stage_basis[0][0],
            stage_basis[0][1],
            stage_basis[1][0],
            stage_basis[1][1],
        )

    # Atlas frames prefer the exact node-table affine when one is recorded. It
    # overrides the camera-readout ``image_size`` with the montage frame the
    # AtlasPixelPosition values live in, and supplies the metres-per-pixel the
    # overlay extents should scale with. Non-atlas frames keep the legacy path.
    atlas_pixel_affine: AtlasPixelAffine | None = None
    if isinstance(source, Atlas):
        atlas_pixel_affine = _atlas_pixel_affine(source)
        if atlas_pixel_affine is not None:
            LOGGER.info(
                "atlas overlay projection: id=%s using node-table affine source=%s "
                "nodes=%d residual=%.2fpx montage_image_size=%dx%d pixel_size=%.6e m/px "
                "(replaces camera-readout image_size=%dx%d and the legacy 180deg rotation)",
                getattr(source, "id", "<unknown>"),
                atlas_pixel_affine.source,
                atlas_pixel_affine.sample_count,
                atlas_pixel_affine.residual_px,
                atlas_pixel_affine.image_size[0],
                atlas_pixel_affine.image_size[1],
                atlas_pixel_affine.pixel_size,
                image_size[0],
                image_size[1],
            )
            image_size = atlas_pixel_affine.image_size
            pixel_size = atlas_pixel_affine.pixel_size
        else:
            LOGGER.info(
                "atlas overlay projection: id=%s no usable node table; using legacy "
                "stage-basis + 180deg rotation fallback",
                getattr(source, "id", "<unknown>"),
            )

    return ImageFrame(
        image_size=image_size,
        stage_position=stage_position,
        pixel_size=pixel_size,
        source=type(source).__name__,
        stage_basis=stage_basis,
        stage_basis_source=stage_basis_source,
        atlas_pixel_affine=atlas_pixel_affine,
    )


def project_marker_to_size(
    marker: ImageMarker, target_size: tuple[int, int]
) -> ImageMarker:
    """Return a copy of ``marker`` rescaled into ``target_size`` pixels.

    The marker's coordinates were computed in ``metadata['image_size']``
    space (the size returned by :func:`image_size_for_markers` —
    typically the SearchMap.dm ``ImageSize`` or the MRC ``nx/ny``).
    The displayed image might be a downsampled JPG with a different
    pixel resolution; this helper applies the same scale-to-display
    transform the GUI's ``_project_marker`` uses, so report rendering
    and on-screen rendering produce the same overlay positions.

    Falls back to the unmodified marker when ``metadata['image_size']``
    is missing or non-positive (preserves the previous behaviour for
    markers that don't carry the metadata).
    """

    image_size = marker.metadata.get("image_size") if marker.metadata else None
    if not (
        isinstance(image_size, tuple)
        and len(image_size) == 2
        and isinstance(image_size[0], (int, float))
        and isinstance(image_size[1], (int, float))
    ):
        return marker
    src_w = float(image_size[0])
    src_h = float(image_size[1])
    tgt_w = float(target_size[0])
    tgt_h = float(target_size[1])
    if src_w <= 0 or src_h <= 0 or tgt_w <= 0 or tgt_h <= 0:
        return marker
    scale_x = tgt_w / src_w
    scale_y = tgt_h / src_h
    bbox = None
    if marker.bbox is not None:
        x, y, w, h = marker.bbox
        bbox = (x * scale_x, y * scale_y, w * scale_x, h * scale_y)
    polygon = (
        [(x * scale_x, y * scale_y) for x, y in marker.polygon]
        if marker.polygon
        else None
    )
    return ImageMarker(
        id=marker.id,
        marker_type=marker.marker_type,
        linked_object_id=marker.linked_object_id,
        source_object_id=marker.source_object_id,
        x=marker.x * scale_x if marker.x is not None else None,
        y=marker.y * scale_y if marker.y is not None else None,
        radius=(
            marker.radius * (scale_x + scale_y) / 2.0
            if marker.radius is not None
            else None
        ),
        bbox=bbox,
        polygon=polygon,
        label=marker.label,
        tooltip=marker.tooltip,
        status=marker.status,
        visible=marker.visible,
        selected=marker.selected,
        unresolved=marker.unresolved,
        metadata=dict(marker.metadata),
    )


def image_size_for_markers(source: Atlas | Overview | SearchMap | SearchTile) -> tuple[int, int] | None:
    """Pixel dimensions in which marker coordinates are expressed.

    The PDF report needs the *same* image_size that the marker
    computation used so overlays scale correctly when drawn on top of
    an embedded JPG. Public wrapper around the existing private
    ``_image_size`` so callers don't have to reach into marker_service
    internals (or duplicate the XML/MRC/cached priority order).
    """

    return _image_size(source)


def inferred_failed_tilt_ids_for_search_map(
    search_map: SearchMap,
    *,
    tilt_series: Iterable[TiltSeries],
    failed_tilt_ids: frozenset[str] | set[str],
    batch_positions: Iterable[BatchPosition] = (),
) -> frozenset[str]:
    """Failed, orphaned tilt series whose stage projects onto ``search_map``.

    This mirrors the SearchMap overlay fallback without promoting the result to
    a parsed domain link. It lets summary/report code count the same
    approximate failed markers that the viewer and PDF overlay can draw.
    """

    frame = _image_frame(search_map)
    if frame is None:
        return frozenset()
    markers = _inferred_failed_tilt_markers(
        source=search_map,
        frame=frame,
        tilt_series=tilt_series,
        failed_tilt_ids=frozenset(failed_tilt_ids),
        batch_positions=batch_positions,
    )
    return frozenset(
        marker.linked_object_id
        for marker in markers
        if marker.linked_object_id is not None
    )


def _image_size(source: Atlas | Overview | SearchMap | SearchTile) -> tuple[int, int] | None:
    for key in ("SearchMap.dm", "Atlas.dm", "Overview.dm", "Tile.xml"):
        value = find_first(source.metadata.get(key), "ImageSize")
        if isinstance(value, dict):
            width = _as_int(value.get("width"))
            height = _as_int(value.get("height"))
            if width is not None and height is not None and width > 0 and height > 0:
                return width, height
        value = find_first(source.metadata.get(key), "ReadoutArea")
        if isinstance(value, dict):
            width = _as_int(value.get("width"))
            height = _as_int(value.get("height"))
            if width is not None and height is not None and width > 0 and height > 0:
                return width, height
    metadata = getattr(source, "mrc_metadata", None)
    if (
        isinstance(metadata, MrcMetadata)
        and metadata.nx is not None
        and metadata.ny is not None
        and metadata.nx > 0
        and metadata.ny > 0
    ):
        return metadata.nx, metadata.ny
    # Fallback: ``atlas_parser`` caches the rendered JPG dimensions when the
    # XML mosaic-level ``ImageSize`` element is absent. Without this, the
    # Atlas tab silently rendered no markers on screening sessions.
    cached = source.metadata.get("AtlasImageSize") if isinstance(source.metadata, dict) else None
    if isinstance(cached, dict):
        width = _as_int(cached.get("width"))
        height = _as_int(cached.get("height"))
        if width is not None and height is not None and width > 0 and height > 0:
            return width, height
    return None


def _stage_position(source: Atlas | Overview | SearchMap | SearchTile) -> tuple[float, float] | None:
    metadata = getattr(source, "mrc_metadata", None)
    if isinstance(metadata, MrcMetadata):
        position = _mrc_stage_position(metadata)
        if position is not None:
            return position
    for key in ("SearchMap.dm", "Atlas.dm", "Overview.dm"):
        value = find_first(source.metadata.get(key), "StagePosition")
        if isinstance(value, dict):
            x = _as_float(value.get("X"))
            y = _as_float(value.get("Y"))
            if x is not None and y is not None:
                return x, y
    value = find_first(source.metadata, "Position")
    return _stage_position_from_metadata({"Position": value}) if isinstance(value, dict) else None


def _stage_position_from_metadata(metadata: object) -> tuple[float, float] | None:
    if not isinstance(metadata, dict):
        return None
    value = find_first(metadata, "Position")
    if not isinstance(value, dict):
        return None
    x = _as_float(value.get("X"))
    y = _as_float(value.get("Y"))
    return (x, y) if x is not None and y is not None else None


def _stage_basis(
    source: Atlas | Overview | SearchMap | SearchTile,
    pixel_size: float,
) -> tuple[tuple[tuple[float, float], tuple[float, float]] | None, str | None]:
    reference, reference_source = _reference_transformation_metadata(source)
    transformation = find_first(reference, "ReferenceTransformation") if isinstance(reference, dict) else None
    matrix = transformation.get("matrix") if isinstance(transformation, dict) else None
    if not isinstance(matrix, dict):
        return None, None
    m11 = _as_float(matrix.get("_m11"))
    m12 = _as_float(matrix.get("_m12"))
    m21 = _as_float(matrix.get("_m21"))
    m22 = _as_float(matrix.get("_m22"))
    if None in (m11, m12, m21, m22):
        return None, None
    # ``ReferenceTransformation`` records the stage-space direction of the
    # image x/y pixel axes.  We preserve direction and handedness, then scale
    # each axis to the frame pixel size that marker coordinates are expressed
    # in.  This is especially important for Atlas mosaics: their parsed
    # metadata stores the transform inside ``Atlas_*.xml`` rather than as a
    # top-level ``Atlas.metadata["ReferenceTransformation"]`` entry, and
    # ignoring it mirrors/rotates child Overview/Search map overlays around
    # the atlas centre.
    x_axis = _normalized_scaled_vector((m11, m12), pixel_size)
    y_axis = _normalized_scaled_vector((m21, m22), pixel_size)
    if x_axis is None or y_axis is None:
        return None, None
    return (x_axis, y_axis), reference_source


def _reference_transformation_metadata(
    source: Atlas | Overview | SearchMap | SearchTile,
) -> tuple[dict[str, Any] | None, str | None]:
    candidates: list[tuple[str, object]] = [
        ("Tile.xml", source.metadata.get("Tile.xml")),
        ("SearchMap.xml", source.metadata.get("SearchMap.xml")),
        ("Overview.xml", source.metadata.get("Overview.xml")),
    ]
    if isinstance(source, Atlas):
        candidates.extend(
            (key, value)
            for key, value in source.metadata.items()
            if isinstance(key, str) and key.startswith("Atlas_") and key.endswith(".xml")
        )
    candidates.append(("metadata", source.metadata))
    for label, candidate in candidates:
        if isinstance(candidate, dict) and isinstance(find_first(candidate, "ReferenceTransformation"), dict):
            return candidate, label
    return None, None


def _normalized_scaled_vector(vector: tuple[float, float], scale: float) -> tuple[float, float] | None:
    length = ((vector[0] * vector[0]) + (vector[1] * vector[1])) ** 0.5
    if length <= 0 or scale <= 0:
        return None
    return (vector[0] / length * scale, vector[1] / length * scale)


def _pixel_size(source: Atlas | Overview | SearchMap | SearchTile) -> float | None:
    # Atlas-specific override: prefer the rendered-mosaic pixel size when the
    # parser has computed one. The per-tile pixelSize in the XML measures the
    # *capture* resolution (e.g. 0.47 µm/px), but the displayed Atlas image
    # is a heavily downsampled mosaic with a much coarser effective pixel
    # size — using the per-tile value here makes Search Map / Overview
    # overlays render orders of magnitude too large.
    if isinstance(source, Atlas):
        mosaic = source.metadata.get("AtlasMosaicPixelSize") if isinstance(source.metadata, dict) else None
        if isinstance(mosaic, dict):
            x_value = _as_float(mosaic.get("x"))
            if x_value is not None and x_value > 0:
                return x_value
    pixel_size = find_first(source.metadata, "pixelSize")
    if isinstance(pixel_size, dict):
        x_value = pixel_size.get("x")
        if isinstance(x_value, dict):
            value = _as_float(x_value.get("numericValue"))
            if value is not None and value > 0:
                return value
    metadata = getattr(source, "mrc_metadata", None)
    if isinstance(metadata, MrcMetadata):
        return _mrc_pixel_size(metadata)
    return None


def _mrc_stage_position(metadata: MrcMetadata | None) -> tuple[float, float] | None:
    if metadata is None:
        return None
    frame = metadata.frame_metadata[0] if metadata.frame_metadata else None
    if not isinstance(frame, dict):
        return None
    x = _as_float(frame.get("stage_x"))
    y = _as_float(frame.get("stage_y"))
    return (x, y) if x is not None and y is not None else None


def _mrc_pixel_size(metadata: MrcMetadata | None) -> float | None:
    if metadata is None:
        return None
    frame = metadata.frame_metadata[0] if metadata.frame_metadata else None
    if isinstance(frame, dict):
        raw_fields = frame.get("raw_fields")
        if isinstance(raw_fields, dict):
            for key in ("pixel_size_x", "pixel_size_y"):
                value = _as_float(raw_fields.get(key))
                if value is not None and value > 0:
                    return value
        value = _as_float(frame.get("pixel_size"))
        if value is not None and value > 0:
            # Parsed frame display fields are always Angstroms; raw FEI fields
            # above are always metres.
            return value * 1e-10
    x = metadata.voxel_size[0]
    if isinstance(x, int | float) and x > 0:
        # MRC voxel sizes are canonical Angstroms.
        return float(x) * 1e-10
    return None


def _legacy_image_size(search_map: SearchMap) -> tuple[int, int] | None:
    value = find_first(search_map.metadata.get("SearchMap.dm"), "ImageSize")
    if not isinstance(value, dict):
        return None
    width = _as_int(value.get("width"))
    height = _as_int(value.get("height"))
    return (width, height) if width is not None and height is not None else None


def _position_on_tile_set(batch: BatchPosition) -> tuple[float, float] | None:
    value = batch.metadata.get("PositionOnTileSet")
    if not isinstance(value, dict):
        return None
    x = _as_float(value.get("StagePositionX"))
    y = _as_float(value.get("StagePositionY"))
    return (x, y) if x is not None and y is not None else None


def _batch_tooltip(batch: BatchPosition) -> str:
    lines = [f"Batch position: {batch.name or batch.id}"]
    if batch.status:
        lines.append(f"Status: {batch.status}")
    if batch.linked_tilt_series_ids:
        lines.append(f"Linked tilt series: {len(batch.linked_tilt_series_ids)}")
    return "\n".join(lines)


def _as_float(value: Any) -> float | None:
    if not isinstance(value, int | float):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _as_int(value: Any) -> int | None:
    return int(value) if isinstance(value, int) else None


def _as_str(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
