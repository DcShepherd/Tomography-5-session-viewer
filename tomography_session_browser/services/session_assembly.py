"""Assemble parsed sessions in memory; never write to source folders.

Linking runs once after parsing and before counts or UI preparation. The
matching order, warning boundaries and summary identity rules are preserved
from SessionScanner; they are intentionally not unified with other variants.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import TypeVar

from tomography_session_browser.diagnostics.profiling import NULL_PROFILE, LoadingProfiler
from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.identity import dedupe_entities, summary_entity_key
from tomography_session_browser.domain.models import Atlas, BatchPosition, Sample, SearchMap, Session
from tomography_session_browser.parsers.scan_progress import ScanProgress, ScanProgressReporter
from tomography_session_browser.services.acquisition_metadata import (
    ACQUISITION_METADATA_KEY,
    merge_acquisition_settings_metadata,
)
from tomography_session_browser.services.image_geometry import frame_diagnostic, image_frame
from tomography_session_browser.services.search_tile_linking import build_search_tiles
from tomography_session_browser.services.tilt_series_validation import planned_tilt_warnings
from tomography_session_browser.services.navigation.models import NavigationResolution
from tomography_session_browser.services.navigation.resolution import dedupe_navigation_candidates, navigation_resolution_from_candidates

LOGGER = logging.getLogger(__name__)
T = TypeVar("T")


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
                map_targets = _link_batch_positions_to_search_maps(sample)
                _link_batch_positions_to_tilt_series(sample, map_targets=map_targets)
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
    for tilt in dedupe_entities([*session.tilt_series, *(tilt for sample in session.samples for tilt in sample.tilt_series)]):
        for warning in planned_tilt_warnings(tilt):
            if warning not in tilt.warnings:
                tilt.warnings.append(warning)
    for atlas in (session.atlas, *(sample.atlas for sample in session.samples)):
        if atlas is not None:
            note_unplaceable_atlas(atlas)
    with profile.phase("summarise_counts"):
        session.metadata_summary["counts"] = session_counts(session)


def note_unplaceable_atlas(atlas: Atlas) -> None:
    """Warn, once, when no marker can be placed on ``atlas`` and say why.

    The Session-tab warnings, the Atlas tab's metadata and the PDF then show
    "Atlas markers unavailable: …" instead of an atlas silently without
    markers. Recorded at load so every surface sees it from the start; marker
    building only reads it.
    """

    if image_frame(atlas) is not None:
        return
    message = f"Atlas markers unavailable: {frame_diagnostic(atlas)}"
    if message not in atlas.warnings:
        atlas.warnings.append(message)


def _candidate_index(values: Iterable[T], key: Callable[[T], str | None], kind: str) -> dict[str, NavigationResolution]:
    groups: dict[str, list[T]] = {}
    for value in values:
        if name := key(value):
            groups.setdefault(name, []).append(value)
    return {name: navigation_resolution_from_candidates(
        members, provenance=f"assembly.{kind}", navigable_explanation=f"Unique {kind} match.",
        ambiguous_prefix=f"Ambiguous {kind} association for {name}", unresolved_explanation=f"No {kind} match.",
    ) for name, members in groups.items()}


def _link_batch_positions_to_tilt_series(sample: Sample, *, map_targets: dict[int, SearchMap] | None = None) -> None:
    sample.batch_positions[:] = dedupe_navigation_candidates(sample.batch_positions)
    batch_by_name = _candidate_index(sample.batch_positions, lambda batch: batch.name, "batch name")
    maps_by_id = _candidate_index(sample.search_maps, lambda sm: sm.id, "search map ID")
    batch_names = sorted(batch_by_name, key=len, reverse=True)
    for tilt in sample.tilt_series:
        resolution = batch_by_name.get(tilt.name)
        if resolution is None:
            # Strict prefix-only match. We deliberately don't
            # fuzzy-attach orphaned failed tilts (e.g. sepia_1
            # whose batch was never recorded in
            # BatchPositionsList.xml) to a sibling batch — that
            # would tag the sibling's overlays as failed in the
            # GUI / PDF even though the sibling acquisition
            # succeeded. Orphans stay orphaned and simply don't
            # contribute to any search-map bar.
            resolution = next(
                (batch_by_name[name] for name in batch_names if tilt.name.startswith(f"{name}_")),
                None,
            )
        if resolution is None:
            continue
        if not resolution.navigable or not isinstance(resolution.target, BatchPosition):
            if resolution.explanation not in tilt.warnings:
                tilt.warnings.append(resolution.explanation)
            continue
        batch = resolution.target
        tilt.linked_batch_position_id = batch.id
        if ACQUISITION_METADATA_KEY in batch.metadata:
            merge_acquisition_settings_metadata(tilt.metadata, batch.metadata, overwrite=False)
        if tilt.id not in batch.linked_tilt_series_ids:
            batch.linked_tilt_series_ids.append(tilt.id)
        map_resolution = maps_by_id.get(batch.linked_search_map_id or "")
        search_map = (map_targets.get(id(batch)) if map_targets is not None
                      else map_resolution.target if map_resolution is not None and map_resolution.navigable else None)
        if search_map is not None and tilt.id not in search_map.linked_tilt_series_ids:
            search_map.linked_tilt_series_ids.append(tilt.id)


def _link_batch_positions_to_search_maps(sample: Sample) -> dict[int, SearchMap]:
    sample.search_maps[:] = dedupe_navigation_candidates(sample.search_maps)
    search_maps_by_name = _candidate_index(sample.search_maps, lambda sm: sm.name, "search map name")
    targets: dict[int, SearchMap] = {}
    for batch in sample.batch_positions:
        tile_set_name = _tile_set_name(batch.metadata.get("PositionOnTileSet"))
        if tile_set_name is None:
            continue
        resolution = search_maps_by_name.get(tile_set_name)
        if resolution is None:
            continue
        if not resolution.navigable or not isinstance(resolution.target, SearchMap):
            if resolution.explanation not in batch.warnings:
                batch.warnings.append(resolution.explanation)
            continue
        search_map = resolution.target
        batch.linked_search_map_id = search_map.id
        targets[id(batch)] = search_map
        if batch.id not in search_map.linked_batch_position_ids:
            search_map.linked_batch_position_ids.append(batch.id)
    return targets


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
