"""Describe review entities and scopes as structured context-panel text."""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any
from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.parsers.atlas_parser import ATLAS_IMAGE_NOT_FOUND_WARNING
from tomography_session_browser.domain.models import (
    Atlas,
    BatchPosition,
    Overview,
    Sample,
    SearchMap,
    SearchTile,
    Session,
    TiltSeries,
)
from tomography_session_browser.presentation.describe.metadata import (
    acquisition_setting_detail,
    acquisition_setting_lines,
    combined_tilt_range_text,
    common_binning,
    common_magnification_value,
    common_section_value,
    common_tilt_increment,
    common_tilt_pixel_size,
    common_xml_pixel_size,
    first_metadata,
    magnification_value,
    microscope_state_lines,
    mrc_metadata_lines,
    section_value_text,
    standalone_tilt_validation,
    target_defocus_range_text,
    tilt_increment_text,
    tilt_pixel_size_text,
    tilt_series_quality_lines,
    tilt_series_validation_lines,
    xml_pixel_size,
)
from tomography_session_browser.presentation.describe.sections import emit_section
from tomography_session_browser.presentation.describe.summary import (
    sample_folder,
    sessions_summary_lines,
    summary_lines,
)
from tomography_session_browser.presentation.formatting import (
    angle_range_text,
    binning_text,
    format_duration_long,
    format_timestamp,
    number_text,
    path_text,
    series_duration_seconds,
)
from tomography_session_browser.presentation.scopes import EntityGroup, LinkedSampleGroup
from tomography_session_browser.presentation.warnings import summarize_warnings
from tomography_session_browser.services.acquisition_metadata import ACQUISITION_SPOT_LABEL, SEARCH_SPOT_LABEL
from tomography_session_browser.services.search_tile_linking import PROJECTION_LINK_METHOD
from tomography_session_browser.services.item_status import (
    ItemListStatus,
    atlas_tile_count,
    display_status_label,
    search_map_tile_counts,
)
from tomography_session_browser.services.tilt_series_validation import (
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_INCOMPLETE,
    STATUS_UNKNOWN,
    TiltSeriesValidation,
    summarise_validations,
    validate_tilt_series_by_group,
)
from tomography_session_browser.services.timeline_service import parse_datetime


def session_summary_lines(session: Session) -> list[str]:
    return sessions_summary_lines([session])


def describe_object(
    value: Any,
    *,
    tilt_validation: TiltSeriesValidation | None = None,
    list_status: ItemListStatus | None = None,
    describes_page_item: bool = False,
) -> str:
    """Describe ``value`` for the context panel.

    ``describes_page_item`` is true when the page beside the panel already
    titles this item and carries its status pill, so the description leaves
    out the ``Status`` row (design review B2.2). Everything else is the same.
    """

    if isinstance(value, EntityGroup):
        return _entity_group_description(value)
    if isinstance(value, LinkedSampleGroup):
        return _linked_sample_group_description(value)
    if isinstance(value, Session):
        return "\n".join(session_summary_lines(value))
    if isinstance(value, Sample):
        return _sample_description(value)
    if isinstance(value, Atlas):
        return _atlas_description(value)
    if isinstance(value, SearchMap):
        return _search_map_description(value)
    if isinstance(value, SearchTile):
        return _search_tile_description(value)
    if isinstance(value, BatchPosition):
        return _batch_position_description(value, list_status=None if describes_page_item else list_status)
    if isinstance(value, TiltSeries):
        return _tilt_series_description(value, validation=tilt_validation, with_status=not describes_page_item)
    if isinstance(value, Overview):
        return _overview_description(value)
    return str(value)


def _emit_warnings_section(blocks: list[str], warnings: Iterable[str]) -> None:
    """Emit a ``Warnings:`` section, and nothing when there are no warnings.

    An absent section is the quiet answer; the header's status pill and the
    dashboard carry the cases that need attention (design review F6).
    """

    summarized = summarize_warnings(list(warnings))
    emit_section(blocks, "Warnings", [f"- {warning}" for warning in summarized[:100]])


def _sample_description(sample: Sample) -> str:
    return "\n".join(
        summary_lines(
            [sample_folder(sample)],
            tilt_series=sample.tilt_series,
            batch_positions=sample.batch_positions,
            atlases=[sample.atlas] if sample.atlas is not None else [],
        )
    )


def _atlas_description(atlas: Atlas) -> str:
    metadata = first_metadata(atlas.metadata)
    blocks: list[str] = ["Atlas"]
    emit_section(
        blocks,
        "Summary",
        [
            f"Pixel size: {xml_pixel_size(metadata)}",
            f"Magnification: {magnification_value(metadata)}",
            f"Tiles: {atlas_tile_count(atlas)}",
            f"Tile image files: {len(atlas.tile_paths)}",
            f"Tile metadata files: {len(atlas.tile_metadata_paths)}",
            f"Alignment files: {len(atlas.alignment_paths)}",
        ],
    )
    emit_section(blocks, "Files", [f"Image: {path_text(atlas.image_path)}"])
    emit_section(blocks, "Metadata source", mrc_metadata_lines(atlas.mrc_metadata))
    if atlas.image_path is None:
        # The record is all there is: said once here, and its warning ("no mosaic
        # was found") is the page's, which says what was looked for and where.
        emit_section(blocks, "Recorded", ["Atlas record: Metadata only"])
        _emit_warnings_section(blocks, [w for w in atlas.warnings if w != ATLAS_IMAGE_NOT_FOUND_WARNING])
    else:
        _emit_warnings_section(blocks, atlas.warnings)
    return "\n".join(blocks)


def _overview_description(overview: Overview) -> str:
    metadata = first_metadata(overview.metadata)
    blocks: list[str] = [f"Overview: {overview.name}"]
    emit_section(
        blocks,
        "Summary",
        [
            f"Pixel size: {xml_pixel_size(metadata)}",
            f"Linked search maps: {len(overview.linked_search_map_ids)}",
        ],
    )
    emit_section(blocks, "Files", [f"Image: {overview.image_path}"])
    _emit_warnings_section(blocks, overview.warnings)
    return "\n".join(blocks)


def _search_map_description(search_map: SearchMap) -> str:
    metadata = first_metadata(search_map.metadata)
    blocks: list[str] = [f"Search map: {search_map.name}"]
    image_stems = {path.stem for path in search_map.tile_paths}
    metadata_stems = {path.stem for path in search_map.tile_metadata_paths}
    summary_rows = [
        f"Pixel size: {xml_pixel_size(metadata)}",
        f"Magnification: {magnification_value(metadata)}",
        f"Tiles: {len(image_stems)}",
    ]
    if image_stems != metadata_stems:
        # Each tile has an image and a metadata file; the counts are worth
        # reading only when the two sets of tiles differ (design review F8).
        summary_rows += [
            f"Tile image files: {len(search_map.tile_paths)}",
            f"Tile metadata files: {len(search_map.tile_metadata_paths)}",
        ]
    summary_rows += [
        f"Linked batch positions: {len(search_map.linked_batch_position_ids)}",
        f"Linked tilt series: {len(search_map.linked_tilt_series_ids)}",
    ]
    planned, acquired = search_map_tile_counts(search_map)
    if (planned or acquired) and planned != acquired:
        summary_rows.append(f"Tile completion: {acquired} of {planned}")
    emit_section(blocks, "Summary", summary_rows)
    emit_section(
        blocks,
        "Files",
        [
            f"Image: {path_text(search_map.image_path)}",
            f"MRC: {path_text(search_map.mrc_path)}",
            f"XML: {path_text(search_map.xml_path)}",
        ],
    )
    # Microscope information read directly from SearchMap.xml's
    # microscopeData block. Vacuum / pressure fields were dropped because
    # they don't carry actionable signal for a review session.
    emit_section(blocks, "Microscope information", microscope_state_lines(search_map.metadata))
    _emit_warnings_section(blocks, search_map.warnings)
    return "\n".join(blocks)


def _search_tile_description(search_tile: SearchTile) -> str:
    blocks: list[str] = [f"Search tile: {search_tile.name}"]
    # The primary method is the expected one; only a fallback (or no link at
    # all) is worth a row (design review F9).
    method_rows = (
        []
        if search_tile.link_method == PROJECTION_LINK_METHOD
        else [f"Link method: {search_tile.link_method or 'unresolved'}"]
    )
    emit_section(
        blocks,
        "Summary",
        [
            f"Search map: {search_tile.search_map_name or 'unknown'}",
            f"Batch position: {search_tile.batch_position_name or 'unknown'}",
            f"Tile index: {search_tile.tile_index if search_tile.tile_index is not None else 'unknown'}",
            f"Acquisition time: {format_timestamp(search_tile.acquisition_time)}",
            *method_rows,
            f"Linked tilt series: {len(search_tile.linked_tilt_series_ids)}",
        ],
    )
    emit_section(
        blocks,
        "Files",
        [
            f"Image: {path_text(search_tile.image_path)}",
            f"XML: {path_text(search_tile.xml_path)}",
        ],
    )
    emit_section(blocks, "Metadata source", mrc_metadata_lines(search_tile.mrc_metadata))
    _emit_warnings_section(blocks, search_tile.warnings)
    return "\n".join(blocks)


def _batch_position_description(
    batch_position: BatchPosition,
    *,
    list_status: ItemListStatus | None = None,
) -> str:
    blocks: list[str] = [f"Batch position: {batch_position.name or batch_position.id}"]
    # "Status" is the shared row status the list and header show; what the
    # microscope wrote is labelled as such, so "Acquired" beside a "Failed"
    # row reads as two facts rather than a contradiction.
    status_rows = [f"Status: {display_status_label(list_status.status)}"] if list_status is not None else []
    emit_section(
        blocks,
        "Summary",
        [
            *status_rows,
            f"Recorded status: {batch_position.status or 'none'}",
            f"Exposure images: {len(batch_position.exposure_image_paths)}",
            f"Linked tilt series: {len(batch_position.linked_tilt_series_ids)}",
        ],
    )
    emit_section(
        blocks,
        "Acquisition",
        [
            f"{label}: {acquisition_setting_detail(batch_position.metadata, label)}"
            for label in ("Detector", SEARCH_SPOT_LABEL, ACQUISITION_SPOT_LABEL, "Probe mode")
        ],
    )
    emit_section(
        blocks,
        "Files",
        [
            f"Search image: {path_text(batch_position.search_image_path)}",
            f"Tracking image: {path_text(batch_position.tracking_image_path)}",
        ],
    )
    _emit_warnings_section(blocks, batch_position.warnings)
    return "\n".join(blocks)


def _tilt_series_description(
    tilt_series: TiltSeries,
    *,
    validation: TiltSeriesValidation | None = None,
    with_status: bool = True,
) -> str:
    validation = validation or standalone_tilt_validation(tilt_series)
    quality_lines = tilt_series_quality_lines(tilt_series, validation=validation)
    validation_lines = tilt_series_validation_lines(tilt_series, validation=validation, with_status=with_status)
    blocks: list[str] = [f"Tilt series: {tilt_series.name}"]
    # The verdict and the rule behind it lead: a reviewer opens this panel to
    # learn why a series was flagged (design review R4).
    emit_section(blocks, "Validation", validation_lines)
    emit_section(
        blocks,
        "Summary",
        [
            f"Tilt images: {tilt_series.tilt_count or 'unknown'}",
            f"Tilt range: {angle_range_text(tilt_series.tilt_range)}",
            f"Tilt increment: {tilt_increment_text(tilt_series)}",
            f"Pixel size: {tilt_pixel_size_text(tilt_series)}",
            f"Binning: {binning_text(tilt_series.binning)}",
            f"Exposure time: {section_value_text(tilt_series, 'ExposureTime', 's')}",
            f"Target defocus: {number_text(tilt_series.target_defocus, 'µm')}",
            f"Magnification: {section_value_text(tilt_series, 'Magnification')}",
            f"Acquisition time: {format_duration_long(series_duration_seconds(tilt_series))}",
        ],
    )
    emit_section(blocks, "Acquisition", acquisition_setting_lines([tilt_series.metadata]))
    emit_section(
        blocks,
        "Files",
        [
            f"MRC: {tilt_series.mrc_path}",
            f"MDOC: {path_text(tilt_series.mdoc_path)}",
        ],
    )
    emit_section(blocks, "Quality", quality_lines)
    _emit_warnings_section(blocks, tilt_series.warnings)
    return "\n".join(blocks)


def _entity_group_description(group: EntityGroup) -> str:
    if not group.values:
        return f"{group.label}: none"
    if all(isinstance(value, SearchMap) for value in group.values):
        return _search_map_group_description(group.label, group.values)
    if all(isinstance(value, SearchTile) for value in group.values):
        return _search_tile_group_description(group.label, group.values)
    if all(isinstance(value, TiltSeries) for value in group.values):
        return _tilt_series_group_description(group.label, group.values)
    if all(isinstance(value, Atlas) for value in group.values):
        return _atlas_group_description(group.label, group.values)
    return "\n".join([f"{group.label}: {len(group.values)} items"])


def _linked_sample_group_description(group: LinkedSampleGroup) -> str:
    return "\n".join(
        summary_lines(
            [sample_folder(sample) for sample in group.samples],
            tilt_series=group.tilt_series,
            batch_positions=group.batch_positions,
            atlases=[group.atlas] if group.atlas is not None else [],
        )
    )


def _search_map_group_description(label: str, values: list[Any]) -> str:
    search_maps = [value for value in values if isinstance(value, SearchMap)]
    metadata_values = [first_metadata(search_map.metadata) for search_map in search_maps]
    batch_count = sum(len(search_map.linked_batch_position_ids) for search_map in search_maps)
    tilt_count = sum(len(search_map.linked_tilt_series_ids) for search_map in search_maps)
    return "\n".join(
        [
            f"{label}: {count_phrase(len(search_maps), 'search map')}",
            f"Pixel size: {common_xml_pixel_size(metadata_values)}",
            f"Magnification: {common_magnification_value(metadata_values)}",
            f"Linked batch positions: {batch_count}",
            f"Linked tilt series: {tilt_count}",
            *_warning_block(warning for search_map in search_maps for warning in search_map.warnings),
        ]
    )


def _search_tile_group_description(label: str, values: list[Any]) -> str:
    search_tiles = [value for value in values if isinstance(value, SearchTile)]
    resolved = sum(1 for tile in search_tiles if tile.image_path is not None)
    linked_tilts = sum(len(tile.linked_tilt_series_ids) for tile in search_tiles)
    return "\n".join(
        [
            f"{label}: {len(search_tiles)} search tiles",
            f"Resolved images: {resolved}",
            f"Linked tilt series: {linked_tilts}",
            *_warning_block(warning for tile in search_tiles for warning in tile.warnings),
        ]
    )


def _atlas_group_description(label: str, values: list[Any]) -> str:
    atlases = [value for value in values if isinstance(value, Atlas)]
    metadata_values = [first_metadata(atlas.metadata) for atlas in atlases]
    return "\n".join(
        [
            f"{label}: {count_phrase(len(atlases), 'atlas', 'atlases')}",
            f"Pixel size: {common_xml_pixel_size(metadata_values)}",
            f"Magnification: {common_magnification_value(metadata_values)}",
            f"Tiles: {sum(atlas_tile_count(atlas) for atlas in atlases)}",
            *_warning_block(warning for atlas in atlases for warning in atlas.warnings),
        ]
    )


def _tilt_series_group_description(label: str, values: list[Any]) -> str:
    tilt_series = [value for value in values if isinstance(value, TiltSeries)]
    # The validator's verdicts, in the dashboard's words. "Successful" used to
    # mean "five or more images", which counted incomplete series too.
    # Per grid, as everywhere else. A tree group carries no samples, but a
    # parsed stack sits in its grid's folder, so the folder names the grid.
    validations = validate_tilt_series_by_group(tilt_series, lambda tilt: Path(tilt.mrc_path).parent)
    outcome = summarise_validations(validations)
    complete_ids = {v.tilt_series_id for v in validations if v.status == STATUS_COMPLETE}
    complete = [tilt for tilt in tilt_series if tilt.id in complete_ids]
    durations = [series_duration_seconds(tilt) for tilt in complete]
    durations = [duration for duration in durations if duration is not None]
    all_dates = [parse_datetime(value) for tilt in tilt_series for value in (tilt.acquisition_time_start, tilt.acquisition_time_end)]
    all_dates = [value for value in all_dates if value is not None]
    total_duration = (max(all_dates) - min(all_dates)).total_seconds() if len(all_dates) >= 2 else None
    return "\n".join(
        [
            f"{label}: {count_phrase(len(tilt_series), 'tilt series', 'tilt series')}",
            f"Complete: {outcome[STATUS_COMPLETE]}",
            f"Incomplete: {outcome[STATUS_INCOMPLETE]}",
            f"Failed: {outcome[STATUS_FAILED]}",
            f"Unknown: {outcome[STATUS_UNKNOWN]}",
            f"Magnification: {common_section_value(tilt_series, 'Magnification')}",
            f"Tilt range: {combined_tilt_range_text(tilt_series)}",
            f"Tilt increment: {common_tilt_increment(tilt_series)}",
            f"Pixel size: {common_tilt_pixel_size(tilt_series)}",
            f"Binning: {common_binning(tilt_series)}",
            f"Exposure time: {common_section_value(tilt_series, 'ExposureTime', 's')}",
            f"Target defocus: {target_defocus_range_text(tilt_series)}",
            f"Total collection time: {format_duration_long(total_duration)}",
            f"Average acquisition time (complete series): {format_duration_long(sum(durations) / len(durations) if durations else None)}",
            *_warning_block(warning for tilt in tilt_series for warning in tilt.warnings),
        ]
    )


def _warning_block(warnings: Iterable[str]) -> list[str]:
    """The Warnings section exactly as ``_emit_warnings_section`` writes it,
    after the blank line that separates it; empty when there are none."""

    blocks: list[str] = [""]
    _emit_warnings_section(blocks, warnings)
    return blocks if len(blocks) > 1 else []
