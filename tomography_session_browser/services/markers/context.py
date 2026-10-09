"""What overlay markers are built from: the scoped entities, the constants
and the acquisition geometry a queued batch borrows from its peers.

Display only, like every module in ``services/markers``: nothing here changes
a parsed entity.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
import logging
import math

from tomography_session_browser.domain.coerce import (
    as_float,
    as_str,
)
from tomography_session_browser.domain.markers import MarkerType
from tomography_session_browser.domain.models import (
    BatchPosition,
    Overview,
    SearchMap,
    TiltSeries,
)
from tomography_session_browser.parsers.xml_parser import find_first
from tomography_session_browser.services.pixel_size import overlay_mrc_pixel_size
from tomography_session_browser.services.item_status import (
    ItemStatusContext,
    batch_position_status_counts,
    validate_tilt_series_by_grid,
)
from tomography_session_browser.services.tilt_series_validation import TiltSeriesValidation

# Every submodule logs under the module name the markers had before the
# split, so existing log filters keep working.
LOGGER = logging.getLogger("tomography_session_browser.services.marker_service")


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
    # ``tilt_validations()`` once worked out. Every overlay builder
    # classifies the same tilt series, so a context does it once instead of
    # once per image. Not an input: ``replace`` gives the copy its own.
    _validations: list[dict[str, TiltSeriesValidation]] = field(
        default_factory=list, init=False, repr=False, compare=False,
    )

    def tilt_validations(self) -> dict[str, TiltSeriesValidation]:
        """``tilt_series`` validated as the overlay builders validate them:
        together, with no grid owners (``build_item_status_context`` given no
        samples). Callers must not change the mapping."""

        if not self._validations:
            self._validations.append(
                {v.tilt_series_id: v for v in validate_tilt_series_by_grid(self.tilt_series)}
            )
        return self._validations[0]


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


def queued_overlay_reference(
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
        context = as_str(batch.metadata.get("BeamDiameterContext"))
        diameter = as_float(find_first(batch.metadata, "BeamDiameter"))
        if (
            context == BEAM_DIAMETER_EXPOSURE_MRC_CONTEXT
            and diameter is not None
            and diameter > 0
        ):
            beam_candidates.append((batch, diameter))

        metadata = batch.exposure_mrc_metadata
        pixel_size = overlay_mrc_pixel_size(metadata)
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
            as_str(beam_batch.metadata.get("BeamDiameterSource"))
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
