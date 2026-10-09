"""Failed, orphaned tilt series placed approximately from their MRC stage
position. They are drawn and counted, never attached to a batch."""
from __future__ import annotations

from collections.abc import Iterable

from tomography_session_browser.domain.markers import (
    ImageMarker,
    MarkerType,
)
from tomography_session_browser.domain.models import (
    Atlas,
    BatchPosition,
    Overview,
    SearchMap,
    TiltSeries,
)
from tomography_session_browser.services.batch_label_service import compact_label_for_tilt
from tomography_session_browser.services.image_geometry import (
    ImageFrame,
    image_frame,
    mrc_stage_position,
    stage_to_image,
)
from tomography_session_browser.services.markers.corrections import apply_grouped_overlay_position_correction
from tomography_session_browser.services.markers.beam import exposure_marker_radius
from tomography_session_browser.services.markers.context import LOGGER


def inferred_failed_tilt_markers(
    *,
    source: Atlas | SearchMap | Overview,
    frame: ImageFrame,
    tilt_series: Iterable[TiltSeries],
    failed_tilt_ids: frozenset[str],
    batch_positions: Iterable[BatchPosition],
) -> list[ImageMarker]:
    """Approximate location markers for failed orphaned tilt series.

    A failed tilt series whose batch position never made it into
    ``BatchPositionsList.xml`` (e.g. the ``alpha_1`` /
    ``alpha_1_2`` failures) doesn't reach the regular batch-driven
    marker code path. We can still locate them on the SearchMap /
    Overview by reading the stage position from the tilt series'
    MRC extended-header metadata and projecting it through the same
    ``stage_to_image`` + reflection pipeline the batch dot uses.

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
    radius = exposure_marker_radius(batch_positions, frame.pixel_size)
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
        position = mrc_stage_position(tilt.mrc_metadata)
        if position is None:
            LOGGER.debug(
                "inferred-failed marker skipped: no stage position tilt=%s source=%s",
                tilt.id,
                getattr(source, "id", None),
            )
            continue
        x, y = stage_to_image(frame, position)
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
            apply_grouped_overlay_position_correction(
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

    frame = image_frame(search_map)
    if frame is None:
        return frozenset()
    markers = inferred_failed_tilt_markers(
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
