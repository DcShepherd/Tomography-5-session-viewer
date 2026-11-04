"""Translate existing acquisition verdicts into dashboard tile states."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any
from tomography_session_browser.services.tilt_series_validation import validation_for
from tomography_session_browser.domain.models import (
    Atlas,
    BatchPosition,
    Overview,
    SearchMap,
    TiltSeries,
)
from tomography_session_browser.domain.status import RowStatus, ValidationStatus, parse_recorded_status
from tomography_session_browser.presentation.dashboard.models import (
    StatusTileModel,
    TILE_STATUS_COMPLETE,
    TILE_STATUS_FAILED,
    TILE_STATUS_MISSING,
    TILE_STATUS_NEUTRAL,
    TILE_STATUS_WARNING,
    TiltSeriesOutcomeModel,
    tile_status_word,
)
from tomography_session_browser.services.item_status import (
    ItemStatusContext,
    display_status_label,
    item_list_status,
    search_map_tile_counts,
)
from tomography_session_browser.services.tilt_series_validation import SOURCE_MRC_EXTENDED, SOURCE_SESSION_METADATA, TiltSeriesValidation


def search_map_is_incomplete(search_map: SearchMap, context: ItemStatusContext) -> bool:
    """Whether the Search map tab's row for ``search_map`` reads Incomplete.

    The dashboard counts a search map as incomplete by the list's own verdict,
    so the count and the rows a drill-down opens cannot disagree.
    """

    return display_status_label(item_list_status(search_map, context).status) == "Incomplete"


def _tile_status_counts(
    items: Iterable[Any],
    status_of: Callable[[Any], str],
) -> tuple[int, int, int, int]:
    """Count items by tile status -> ``(complete, warning, failed, queued)``.

    The presenter classifies progress at three points (linked-sample
    progress rows, batch-position rows, and tilt-status counts) with
    identical bucket boundaries: complete / warning / failed / anything
    else falls into queued. Centralising the bucketing here keeps the
    three call sites honest if the bucket set ever changes.
    """

    complete = warning = failed = queued = 0
    for item in items:
        status = status_of(item)
        if status == TILE_STATUS_COMPLETE:
            complete += 1
        elif status == TILE_STATUS_WARNING:
            warning += 1
        elif status == TILE_STATUS_FAILED:
            failed += 1
        else:
            queued += 1
    return complete, warning, failed, queued


def tilt_status_counts(
    tilt_series: list[TiltSeries],
    validation_by_id: dict[str, Any],
) -> tuple[int, int, int, int]:
    def _status_of(tilt: TiltSeries) -> str:
        validation = validation_for(validation_by_id, tilt)
        return (
            tile_status_from_validation_status(validation.status)
            if validation is not None
            else TILE_STATUS_NEUTRAL
        )

    return _tile_status_counts(tilt_series, _status_of)


def atlas_tile_status(atlas: Atlas) -> str:
    """The Atlases card's tile status for ``atlas``.

    Public so the PDF cover colours an atlas it borrowed from a linked
    screening session by the same rule the dashboard card uses.
    """

    image_path = getattr(atlas, "image_path", None)
    if image_path is None:
        return TILE_STATUS_NEUTRAL
    # A file that is not on disk is missing, not a failed acquisition.
    try:
        return TILE_STATUS_COMPLETE if image_path.exists() else TILE_STATUS_MISSING
    except OSError:
        return TILE_STATUS_MISSING


def overview_status(overview: Overview) -> str:
    """Status drives the tile colour for an Overview entry.

    The MRC/JPG image must be present on disk for the overview to render in
    the viewer; missing files are flagged so the user notices a parser gap
    without opening another panel.
    """

    image_path = getattr(overview, "image_path", None)
    if image_path is None:
        return TILE_STATUS_MISSING
    try:
        return TILE_STATUS_COMPLETE if image_path.exists() else TILE_STATUS_MISSING
    except OSError:
        return TILE_STATUS_MISSING


def search_map_status(search_map: SearchMap) -> str:
    """Search-map status from the planned/acquired tile counts."""

    planned, acquired = search_map_tile_counts(search_map)
    if planned <= 0:
        # Nothing planned — treat as neutral ("unknown"); we have no completion
        # signal to flag this against. Most often this happens when the
        # parser couldn't read GridSize from the XML.
        return TILE_STATUS_NEUTRAL
    if acquired >= planned:
        return TILE_STATUS_COMPLETE
    if acquired <= 0:
        return TILE_STATUS_MISSING
    return TILE_STATUS_WARNING


def batch_position_status(batch: BatchPosition) -> str:
    """Map the recorded ``BatchPositionStatus`` to a tile state.

    Read through ``parse_recorded_status`` so the tiles and the list rows
    understand a recorded word the same way. Pending and unrecognised words are neutral.
    """

    recorded = parse_recorded_status(batch.status)
    if recorded is None:
        return TILE_STATUS_NEUTRAL
    return {
        RowStatus.DONE: TILE_STATUS_COMPLETE,
        RowStatus.FAILED: TILE_STATUS_FAILED,
        RowStatus.PARTIAL: TILE_STATUS_WARNING,
    }.get(recorded, TILE_STATUS_NEUTRAL)


def tile_status_from_validation_status(validation_status: str) -> str:
    """Map a TiltSeriesValidation status onto the StatusTileModel palette.

    * complete   → green tile
    * incomplete → amber tile
    * failed     → red tile
    * unknown    → grey/neutral tile
    """

    if validation_status == ValidationStatus.COMPLETE:
        return TILE_STATUS_COMPLETE
    if validation_status == ValidationStatus.INCOMPLETE:
        return TILE_STATUS_WARNING
    if validation_status == ValidationStatus.FAILED:
        return TILE_STATUS_FAILED
    return TILE_STATUS_NEUTRAL


def tile_tooltip(item: Any, status: str, card_label: str = "") -> str:
    name = getattr(item, "name", None) or getattr(item, "id", "<unknown>")
    # The card's own word for the status, not the internal one ("Neutral").
    return f"{name}\nStatus: {tile_status_word(card_label, status).capitalize()}"


def summarise_statuses(tiles: list[StatusTileModel]) -> dict[str, int]:
    """Count tiles per status for the chip strip and grouped fallback."""

    summary: dict[str, int] = {}
    for tile in tiles:
        summary[tile.status] = summary.get(tile.status, 0) + 1
    return summary


def outcomes_for(validations: Iterable[TiltSeriesValidation]) -> TiltSeriesOutcomeModel:
    """Aggregate a scope's verdicts into the donut model."""

    validations = list(validations)
    complete = sum(1 for v in validations if v.status == ValidationStatus.COMPLETE)
    incomplete = sum(1 for v in validations if v.status == ValidationStatus.INCOMPLETE)
    failed = sum(1 for v in validations if v.status == ValidationStatus.FAILED)
    unknown = sum(1 for v in validations if v.status == ValidationStatus.UNKNOWN)
    return TiltSeriesOutcomeModel(
        complete=complete, incomplete=incomplete, failed=failed, unknown=unknown
    )


def validation_tooltip(validation) -> str:
    """Multi-line tooltip used by the tilt-series tile grid and context panel."""

    lines = [
        validation.name,
        validation.status.upper(),
        f"{validation.actual_count} / {validation.expected_count or '?'} images",
    ]
    if validation.min_tilt is not None and validation.max_tilt is not None:
        range_label = (
            "Planned tilt range"
            if validation.evidence_source in {SOURCE_SESSION_METADATA, SOURCE_MRC_EXTENDED}
            else "Observed tilt range"
        )
        if validation.tilt_increment:
            lines.append(
                f"{range_label} {validation.min_tilt:g}° to {validation.max_tilt:g}° "
                f"at {validation.tilt_increment:g}°"
            )
        else:
            lines.append(
                f"{range_label} {validation.min_tilt:g}° to {validation.max_tilt:g}°"
            )
    if validation.evidence_source and validation.evidence_source != "none":
        lines.append(f"Evidence: {validation.evidence_source.replace('_', ' ')}")
    if validation.reason:
        lines.append(f"Reason: {validation.reason}")
    return "\n".join(lines)
