"""Assemble parsed sessions in memory; never write to source folders.

Linking runs once after parsing and before counts or UI preparation. The
matching order, warning boundaries and summary identity rules are preserved
from SessionScanner; they are intentionally not unified with other variants.
"""

from __future__ import annotations

import logging

from tomography_session_browser.diagnostics.profiling import NULL_PROFILE, LoadingProfiler
from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.identity import dedupe_entities, summary_entity_key
from tomography_session_browser.domain.models import Sample, Session
from tomography_session_browser.parsers.scan_progress import ScanProgress, ScanProgressReporter
from tomography_session_browser.services.acquisition_metadata import (
    ACQUISITION_METADATA_KEY,
    merge_acquisition_settings_metadata,
)
from tomography_session_browser.services.search_tile_linking import build_search_tiles

LOGGER = logging.getLogger(__name__)


def assemble_session(
    session: Session,
    *,
    profile: LoadingProfiler = NULL_PROFILE,
    progress: ScanProgress | None = None,
) -> None:
    """Finish a freshly parsed session, retaining its existing list identities."""

    if session.kind in {SessionKind.MULTIGRID, SessionKind.COLLECTION, SessionKind.SINGLE_COLLECTION}:
        report = ScanProgressReporter(progress if len(session.samples) == 1 else None)
        for sample in session.samples:
            report.part("links")
            with profile.phase("link_batch_positions"):
                _link_batch_positions_to_search_maps(sample)
                _link_batch_positions_to_tilt_series(sample)
            report.part("search tiles")
            try:
                with profile.phase("build_search_tiles"):
                    sample.search_tiles = build_search_tiles(
                        search_maps=sample.search_maps,
                        batch_positions=sample.batch_positions,
                        tilt_series=sample.tilt_series,
                    )
            except Exception as exc:  # defensive boundary for malformed user data
                message = f"Search tiles could not be parsed and was skipped: {exc}"
                sample.warnings.append(message)
                LOGGER.warning(message, exc_info=True)
                sample.search_tiles = []
    with profile.phase("summarise_counts"):
        session.metadata_summary["counts"] = session_counts(session)


def _link_batch_positions_to_tilt_series(sample: Sample) -> None:
    batch_by_name = {position.name: position for position in sample.batch_positions if position.name}
    batch_names = sorted(batch_by_name, key=len, reverse=True)
    for tilt in sample.tilt_series:
        batch = batch_by_name.get(tilt.name)
        if batch is None:
            # Strict prefix-only match. We deliberately don't
            # fuzzy-attach orphaned failed tilts (e.g. sang_1
            # whose batch was never recorded in
            # BatchPositionsList.xml) to a sibling batch — that
            # would tag the sibling's overlays as failed in the
            # GUI / PDF even though the sibling acquisition
            # succeeded. Orphans stay orphaned and simply don't
            # contribute to any search-map bar.
            batch = next(
                (batch_by_name[name] for name in batch_names if tilt.name.startswith(f"{name}_")),
                None,
            )
        if batch is None:
            continue
        tilt.linked_batch_position_id = batch.id
        if ACQUISITION_METADATA_KEY in batch.metadata:
            merge_acquisition_settings_metadata(tilt.metadata, batch.metadata, overwrite=False)
        if tilt.id not in batch.linked_tilt_series_ids:
            batch.linked_tilt_series_ids.append(tilt.id)
        search_map = next((item for item in sample.search_maps if item.id == batch.linked_search_map_id), None)
        if search_map is not None and tilt.id not in search_map.linked_tilt_series_ids:
            search_map.linked_tilt_series_ids.append(tilt.id)


def _link_batch_positions_to_search_maps(sample: Sample) -> None:
    search_maps_by_name = {search_map.name: search_map for search_map in sample.search_maps}
    for batch in sample.batch_positions:
        tile_set_name = _tile_set_name(batch.metadata.get("PositionOnTileSet"))
        if tile_set_name is None:
            continue
        search_map = search_maps_by_name.get(tile_set_name)
        if search_map is None:
            continue
        batch.linked_search_map_id = search_map.id
        search_map.linked_batch_position_ids.append(batch.id)


def session_counts(session: Session) -> dict[str, int]:
    """Count assembled entities using the existing summary identity rule."""

    samples = session.samples
    return {
        "samples": len(samples),
        "atlases": _summary_count(
            [atlas for atlas in [session.atlas, *(sample.atlas for sample in samples)] if atlas is not None]
        ),
        "overviews": _summary_count([*session.overviews, *(value for sample in samples for value in sample.overviews)]),
        "search_maps": _summary_count([*session.search_maps, *(value for sample in samples for value in sample.search_maps)]),
        "search_tiles": _summary_count([*session.search_tiles, *(value for sample in samples for value in sample.search_tiles)]),
        "batch_positions": _summary_count([*session.batch_positions, *(value for sample in samples for value in sample.batch_positions)]),
        "tilt_series": _summary_count([*session.tilt_series, *(value for sample in samples for value in sample.tilt_series)]),
    }


def _tile_set_name(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    tile_set_name = value.get("TileSetName")
    if isinstance(tile_set_name, str) and tile_set_name.strip():
        return tile_set_name.strip()
    return None


def _summary_count(values: list[object]) -> int:
    """How many entities ``values`` holds, as ``metadata_summary`` counts them."""

    return len(dedupe_entities(values, key=summary_entity_key))
