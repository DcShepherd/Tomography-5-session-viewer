from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import re
from statistics import median
from time import perf_counter
from typing import Any

from tomography_session_browser.domain.models import (
    Atlas,
    BatchPosition,
    Overview,
    Sample,
    SearchMap,
    SearchTile,
    Session,
    TiltSeries,
    MrcMetadata,
)
from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.parsers.path_utils import natural_key
from tomography_session_browser.parsers.xml_parser import find_first
from tomography_session_browser.services.batch_inference import (
    batch_inference_key,
    batch_inference_keys,
    inferred_batch_label_for_tilt,
)
from tomography_session_browser.services.marker_service import inferred_failed_tilt_ids_for_search_map
from tomography_session_browser.services.acquisition_metadata import (
    ACQUISITION_SPOT_LABEL,
    LEGACY_SPOT_LABEL,
    SEARCH_SPOT_LABEL,
    acquisition_setting_source,
    acquisition_setting_value,
    format_target_defocus_values,
    summarise_acquisition_setting,
)
from tomography_session_browser.services.timeline_service import parse_section_datetime
from tomography_session_browser.ui.session_linking import linked_sample_groups

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class EntityGroup:
    label: str
    values: list[Any]


@dataclass(slots=True)
class LinkedSampleGroup:
    label: str
    samples: list[Sample]
    atlas: Atlas | None
    overviews: list[Overview]
    search_maps: list[SearchMap]
    search_tiles: list[SearchTile]
    batch_positions: list[BatchPosition]
    tilt_series: list[TiltSeries]


@dataclass(slots=True)
class SessionSummarySection:
    label: str
    fields: list[tuple[str, str]]


@dataclass(slots=True)
class LinkedAtlasDashboardScope:
    """Dashboard-only wrapper for collection scopes with an external atlas.

    The tree selection remains a ``Session`` or ``Sample`` so non-atlas tabs
    stay scoped to that collection. This wrapper only supplies the linked
    atlas entries that the Atlas tab already exposes.
    """

    source: Session | Sample
    atlases: list[tuple[str, Atlas]]


@dataclass(slots=True)
class DashboardEntityScope:
    """Dashboard-only scope for selected acquisition entities.

    Search maps and batch positions only carry linked tilt-series ids. The
    main window resolves those ids while it still has the active project
    context, then passes the resolved tilt series here so presenter logic can
    stay pure and filesystem-free.
    """

    source: SearchMap | BatchPosition
    search_maps: list[SearchMap] = field(default_factory=list)
    batch_positions: list[BatchPosition] = field(default_factory=list)
    tilt_series: list[TiltSeries] = field(default_factory=list)
    samples: list[Sample] = field(default_factory=list)
    sample_groups: list[tuple[str, list[Sample]]] = field(default_factory=list)
    title: str | None = None
    path: str | None = None
    kind: str | None = None


def canonical_entity_key(value: Any) -> tuple[str, str]:
    """Return a stable display identity for parsed/session entities.

    Some Tomography 5 layouts expose the same parsed objects both through
    ``Session`` lists and through their containing ``Sample``. Presenter and
    report totals should count those physical entities once, while still
    keeping unrelated objects with the same display name separate.
    """

    type_name = type(value).__name__
    for attr in (
        "mrc_path",
        "mdoc_path",
        "xml_path",
        "dm_path",
        "image_path",
        "search_metadata_path",
        "overview_image_path",
        "search_image_path",
        "tracking_image_path",
        "exposure_image_path",
        "path",
    ):
        path = getattr(value, attr, None)
        if path:
            return type_name, f"path:{_normalise_identity_path(path)}"
    exposure_paths = getattr(value, "exposure_image_paths", None)
    if exposure_paths:
        first_path = next((path for path in exposure_paths if path), None)
        if first_path:
            return type_name, f"path:{_normalise_identity_path(first_path)}"
    entity_id = getattr(value, "id", None)
    if entity_id:
        return type_name, f"id:{entity_id}"
    return type_name, f"object:{id(value)}"


def dedupe_entities(values: Iterable[Any]) -> list[Any]:
    """Preserve order while removing duplicate parsed/display entities."""

    unique: list[Any] = []
    seen: set[tuple[str, str]] = set()
    for value in values:
        key = canonical_entity_key(value)
        if key in seen:
            continue
        seen.add(key)
        unique.append(value)
    return unique


def _normalise_identity_path(path: str | Path) -> str:
    value = Path(path)
    if not value.is_absolute():
        value = Path.cwd() / value
    return str(value).replace("\\", "/").casefold()


# =============================================================================
# Atlas counting — separated, deduplicated, and labelled
# =============================================================================
#
# The original implementation collapsed three unrelated concepts into a single
# "Atlases" number:
#
#   * the standalone Atlas screening session itself,
#   * the per-grid atlases that screening session contains, and
#   * the atlases linked to data-collection samples.
#
# That produced the wrong totals when a screening session was loaded alongside
# the matching data-collection session (e.g. 13 sample-level Atlas/ folders +
# 1 root Atlas/ → "14 atlases" / "14 samples with atlas"). The fix below
# splits the four concepts into distinct fields and dedupes by canonical
# session/sample/atlas keys so the same physical object can never be counted
# twice.


@dataclass(frozen=True, slots=True)
class AtlasCountModel:
    """Distinct atlas counts surfaced on the dashboard and PDF cover.

    * ``atlas_session_count``    — number of standalone Atlas-screening
      sessions in the loaded set.
    * ``data_collection_sample_count`` — data-collection samples (i.e.
      samples that contain at least one search map / batch position /
      tilt series / overview).
    * ``samples_with_atlas_count`` — data-collection samples that have an
      associated atlas, either directly on the sample or via the
      cross-session sample linker.
    * ``sample_atlas_count``     — distinct atlases linked to a
      data-collection sample. Deduplicated by canonical atlas key so the
      same atlas referenced by two data-collection samples is one row.
    """

    atlas_session_count: int = 0
    data_collection_sample_count: int = 0
    samples_with_atlas_count: int = 0
    sample_atlas_count: int = 0

    def as_label_dict(self) -> dict[str, int]:
        """Return labels suitable for the dashboard / report Counts table."""

        return {
            "Atlas sessions": self.atlas_session_count,
            "Data collection samples": self.data_collection_sample_count,
            "Samples with atlas": self.samples_with_atlas_count,
            "Sample atlases": self.sample_atlas_count,
        }


def is_data_collection_sample(sample: Sample) -> bool:
    """A sample with at least one piece of data-collection content.

    The cover/dashboard need to distinguish *atlas-only* samples (those
    that exist purely to expose an Atlas/ subfolder, as in a screening
    session) from samples where actual collection happened. Any of
    overviews / search maps / batch positions / tilt series counts as
    collection content. An empty Batch/BatchPositionsList.xml does NOT
    qualify on its own — the parser correctly produces zero batch
    positions for that case.
    """

    return bool(
        sample.search_maps
        or sample.search_tiles
        or sample.batch_positions
        or sample.tilt_series
        or sample.overviews
    )


def _canonical_path_key(path: Path | None) -> str | None:
    """Stable key for a filesystem path, robust to symlinks / case quirks."""

    if path is None:
        return None
    try:
        # ``resolve()`` is preferred — it produces an absolute, case-
        # canonical key on Windows. Fall back to a normalised string if
        # the path doesn't exist (resolve still works in modern Python
        # but pre-3.10 used to raise; defensive in any case).
        return str(Path(path).resolve())
    except OSError:
        return str(Path(path)).lower()


def _canonical_sample_key(sample: Sample) -> str:
    return _canonical_path_key(sample.path) or sample.id


def _canonical_atlas_key(atlas: Atlas) -> str:
    """Atlas key — prefer image_path, fall back to atlas.id.

    Two samples that point at the same Atlas/ folder share the same key,
    so the sample-atlas count never double-counts a shared atlas.
    """

    return _canonical_path_key(atlas.image_path) or atlas.id


def compute_atlas_counts(value: Any) -> AtlasCountModel:
    """Distinct atlas / data-collection counts for any tree selection.

    Accepts the same inputs as :func:`session_dashboard_model` —
    ``Session``, ``Sample``, ``LinkedSampleGroup`` or ``list[Session]``.
    Counts are deduplicated by canonical path so the same sample/atlas
    referenced from multiple UI nodes (e.g. a session, a linked-group
    container, and the sample itself) only contributes once.

    "Samples with atlas" counts the per-grid Atlas/ folders directly
    attached to a sample (i.e. ``sample.atlas`` is not None). Each
    atlas-screening session implicitly designates one of its samples
    (conventionally Sample0) as the global atlas representative, so we
    subtract one per atlas-screening session — this matches what the
    user sees in the project tree where Position 0 is the global atlas
    and Positions 1..N are the per-grid atlases. The same definition
    keeps "Samples with atlas" stable whether the screening session is
    loaded alone or alongside a data-collection session.

    Emits a debug log line summarising the inputs and the resulting
    counts so the user can verify exactly which sessions/samples were
    considered when a number looks wrong.
    """

    if isinstance(value, LinkedAtlasDashboardScope):
        base = compute_atlas_counts(value.source)
        linked_count = len({_canonical_atlas_key(atlas) for _label, atlas in value.atlases})
        return AtlasCountModel(
            atlas_session_count=base.atlas_session_count,
            data_collection_sample_count=base.data_collection_sample_count,
            samples_with_atlas_count=linked_count if linked_count else base.samples_with_atlas_count,
            sample_atlas_count=linked_count if linked_count else base.sample_atlas_count,
        )
    if isinstance(value, Sample):
        return _atlas_counts_from_samples([value], atlas_session_count=0)
    if isinstance(value, LinkedSampleGroup):
        return _atlas_counts_from_samples(value.samples, atlas_session_count=0)
    if isinstance(value, Session):
        return _atlas_counts_from_sessions([value])
    if isinstance(value, list):
        return _atlas_counts_from_sessions(value)
    return AtlasCountModel()


def _atlas_counts_from_sessions(sessions: list[Session]) -> AtlasCountModel:
    atlas_sessions = [s for s in sessions if s.kind == SessionKind.ATLAS_SCREENING]
    all_samples: list[Sample] = []
    for session in sessions:
        all_samples.extend(session.samples)

    counts = _atlas_counts_from_samples(
        all_samples, atlas_session_count=len(atlas_sessions)
    )

    LOGGER.debug(
        "atlas counts: total_sessions=%d atlas_session_paths=%s "
        "data_collection_session_paths=%s "
        "atlas_session_count=%d "
        "data_collection_sample_count=%d "
        "samples_with_atlas_count=%d "
        "sample_atlas_count=%d",
        len(sessions),
        [str(s.path) for s in atlas_sessions],
        [str(s.path) for s in sessions if s.kind != SessionKind.ATLAS_SCREENING],
        len(atlas_sessions),
        counts.data_collection_sample_count,
        counts.samples_with_atlas_count,
        counts.sample_atlas_count,
    )

    return AtlasCountModel(
        atlas_session_count=len(atlas_sessions),
        data_collection_sample_count=counts.data_collection_sample_count,
        samples_with_atlas_count=counts.samples_with_atlas_count,
        sample_atlas_count=counts.sample_atlas_count,
    )


def _atlas_counts_from_samples(
    samples: list[Sample],
    *,
    atlas_session_count: int,
) -> AtlasCountModel:
    seen_sample_keys: set[str] = set()
    excluded = 0
    dc_samples: list[Sample] = []
    direct_atlas_sample_keys: set[str] = set()
    sample_atlas_keys: set[str] = set()
    sample_atlas_paths: list[str] = []

    for sample in samples:
        key = _canonical_sample_key(sample)
        if key in seen_sample_keys:
            excluded += 1
            continue
        seen_sample_keys.add(key)
        if is_data_collection_sample(sample):
            dc_samples.append(sample)
        if sample.atlas is not None:
            direct_atlas_sample_keys.add(key)
            atlas_key = _canonical_atlas_key(sample.atlas)
            if atlas_key and atlas_key not in sample_atlas_keys:
                sample_atlas_keys.add(atlas_key)
                sample_atlas_paths.append(atlas_key)

    # Each atlas-screening session "consumes" one of its sample atlases
    # as the global / Sample0 representative — that one is the atlas
    # session itself, not a per-grid atlas. Subtracting it keeps the
    # count of "samples with atlas" matched to the project-tree view
    # (Position 0 = global, Positions 1..N = per-grid atlases).
    samples_with_atlas_count = max(
        0, len(direct_atlas_sample_keys) - atlas_session_count
    )
    sample_atlas_count = max(0, len(sample_atlas_keys) - atlas_session_count)

    LOGGER.debug(
        "atlas counts (sample scope): considered=%d data_collection=%d "
        "direct_atlas_samples=%d unique_atlases=%d "
        "atlas_session_global_subtraction=%d "
        "samples_with_atlas_after=%d sample_atlases_after=%d "
        "excluded_duplicates=%d sample_atlas_paths=%s",
        len(samples),
        len(dc_samples),
        len(direct_atlas_sample_keys),
        len(sample_atlas_keys),
        atlas_session_count,
        samples_with_atlas_count,
        sample_atlas_count,
        excluded,
        sample_atlas_paths,
    )

    return AtlasCountModel(
        atlas_session_count=0,
        data_collection_sample_count=len(dc_samples),
        samples_with_atlas_count=samples_with_atlas_count,
        sample_atlas_count=sample_atlas_count,
    )


def session_counts(session: Session) -> dict[str, int]:
    """Compact, label → count map shown in the legacy summary tree.

    The atlas-related entries come from :func:`compute_atlas_counts` so
    they always agree with the dashboard. Zero values for the
    legacy-collection items are dropped to keep the panel compact —
    atlas keys keep their zero values when an atlas-screening session is
    the only thing loaded, since "0 data collection samples" is
    informative in that case.
    """

    counts: dict[str, int] = {
        "Samples": len(session.samples),
    }

    atlas = compute_atlas_counts(session)
    # Order: Atlas sessions → Samples with atlas → Data collection
    # samples. "Sample atlases" is intentionally omitted — for the
    # common case it duplicates "Samples with atlas".
    if atlas.atlas_session_count > 0:
        counts["Atlas sessions"] = atlas.atlas_session_count
    counts["Samples with atlas"] = atlas.samples_with_atlas_count
    counts["Data collection samples"] = atlas.data_collection_sample_count

    counts["Overviews"] = len(_all_overviews(session))
    counts["Search maps"] = len(_all_search_maps(session))
    counts["Search tiles"] = len(_all_search_tiles(session))
    counts["Batch positions"] = len(_all_batch_positions(session))
    counts["Tilt series"] = len(_all_tilt_series(session))

    if counts["Overviews"] == 0:
        counts.pop("Overviews")
    return counts


def session_warnings(session: Session) -> list[str]:
    warnings = list(session.warnings)
    for sample in session.samples:
        warnings.extend(_prefix_warnings(sample.name, sample.warnings))
        if sample.atlas:
            warnings.extend(_prefix_warnings(f"{sample.name} / Atlas", sample.atlas.warnings))
        for search_map in sample.search_maps:
            warnings.extend(_prefix_warnings(f"{sample.name} / {search_map.name}", search_map.warnings))
        for search_tile in sample.search_tiles:
            warnings.extend(_prefix_warnings(f"{sample.name} / {search_tile.name}", search_tile.warnings))
        for batch_position in sample.batch_positions:
            label = batch_position.name or batch_position.id
            warnings.extend(_prefix_warnings(f"{sample.name} / {label}", batch_position.warnings))
        for tilt_series in sample.tilt_series:
            warnings.extend(_prefix_warnings(f"{sample.name} / {tilt_series.name}", tilt_series.warnings))
    return warnings


def session_summary_lines(session: Session) -> list[str]:
    blocks: list[str] = [
        f"Name: {session.name}",
        f"Kind: {session.kind.value}",
        f"Path: {session.path}",
    ]
    _emit_section(
        blocks,
        "Summary",
        [f"{label}: {value}" for label, value in session_counts(session).items()],
    )
    settings = _acquisition_setting_lines_for_scope(
        [tilt.metadata for tilt in _all_tilt_series(session)],
        [batch.metadata for batch in _all_batch_positions(session)],
    )
    _emit_section(blocks, "Acquisition", settings)
    warnings = session_warnings(session)
    if blocks and blocks[-1] != "":
        blocks.append("")
    blocks.append("Warnings:")
    if warnings:
        warning_groups = grouped_warnings(warnings)
        blocks.extend(f"  {label} ({len(items)})" for label, items in warning_groups.items())
    else:
        blocks.append("  None")
    return blocks


def grouped_warnings(warnings: Iterable[str]) -> dict[str, list[str]]:
    groups = {
        "NaN warnings": [],
        "Atlas files not found": [],
        "Missing metadata files": [],
        "Tilt-angle metadata warnings": [],
        "Missing image files": [],
        "Unrecognized or incorrect folder type": [],
        "Other": [],
    }
    for warning in warnings:
        lowered = warning.lower()
        if "nan" in lowered or "non-finite" in lowered:
            groups["NaN warnings"].append(warning)
        elif "atlas" in lowered and any(token in lowered for token in ("no ", "not found", "missing")):
            groups["Atlas files not found"].append(warning)
        elif any(token in lowered for token in ("tilt-angle", "tilt angle", "tlt file")):
            groups["Tilt-angle metadata warnings"].append(warning)
        elif any(token in lowered for token in ("no matching mdoc", "missing .mdoc", "missing metadata", "no .xml", "metadata")):
            groups["Missing metadata files"].append(warning)
        elif any(token in lowered for token in ("no search image", "no tracking image", "no exposure image", "no searchmap.jpg", "no searchmap.mrc", "image")):
            groups["Missing image files"].append(warning)
        elif any(token in lowered for token in ("did not match", "unsupported", "incorrect", "not a folder")):
            groups["Unrecognized or incorrect folder type"].append(warning)
        else:
            groups["Other"].append(warning)
    return {label: items for label, items in groups.items() if items}


def describe_object(value: Any) -> str:
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
        return _batch_position_description(value)
    if isinstance(value, TiltSeries):
        return _tilt_series_description(value)
    if isinstance(value, Overview):
        return _overview_description(value)
    return str(value)


def _emit_section(blocks: list[str], title: str, rows: list[str]) -> None:
    """Append a labelled section to ``blocks`` for the context panel.

    The :class:`MetadataPanel` parser treats a line of the form ``Title:``
    (no value after the colon) as a section header and renders subsequent
    indented lines as the section body. Empty section bodies are skipped so
    the panel does not render orphan headers.
    """

    if not rows:
        return
    if blocks and blocks[-1] != "":
        blocks.append("")
    blocks.append(f"{title}:")
    blocks.extend(f"  {row}" for row in rows)


def _emit_warnings_section(blocks: list[str], warnings: Iterable[str]) -> None:
    """Always emit a ``Warnings:`` section (with ``None`` when empty).

    Consistent section presence beats a one-line ``Warnings: none`` row;
    the brief calls for a stable section list so users can scan to where
    the warnings *would* live.
    """

    if blocks and blocks[-1] != "":
        blocks.append("")
    blocks.append("Warnings:")
    summarized = _summarize_warnings(list(warnings))
    if not summarized:
        blocks.append("  None")
        return
    blocks.extend(f"  - {warning}" for warning in summarized[:100])


def _sample_description(sample: Sample) -> str:
    blocks: list[str] = []
    # Free-standing top kv rows preserve the legacy contract used by
    # :meth:`MainWindow._context_description` (which strips/replaces the
    # ``Atlas: yes/no`` and appends its own ``Atlas linkage:`` section)
    # and by existing tests that grep for ``Sample:`` and ``Path:`` in
    # the rendered context.
    blocks.append(f"Sample: {sample.name}")
    blocks.append(f"Path: {sample.path}")
    blocks.append(f"Atlas: {'yes' if sample.atlas else 'no'}")
    _emit_section(
        blocks,
        "Summary",
        [
            f"Overviews: {len(sample.overviews)}",
            f"Search maps: {len(sample.search_maps)}",
            f"Search tiles: {len(sample.search_tiles)}",
            f"Batch positions: {len(sample.batch_positions)}",
            f"Tilt series: {len(sample.tilt_series)}",
        ],
    )
    settings = _acquisition_setting_lines_for_scope(
        [tilt.metadata for tilt in sample.tilt_series],
        [batch.metadata for batch in sample.batch_positions],
    )
    _emit_section(blocks, "Acquisition", settings)
    _emit_warnings_section(blocks, sample.warnings)
    return "\n".join(blocks)


def build_linked_sample_group(label: str, samples: list[Sample]) -> LinkedSampleGroup:
    atlas = next((sample.atlas for sample in samples if sample.atlas is not None), None)
    return LinkedSampleGroup(
        label=label,
        samples=samples,
        atlas=atlas,
        overviews=dedupe_entities(value for sample in samples for value in sample.overviews),
        search_maps=dedupe_entities(value for sample in samples for value in sample.search_maps),
        search_tiles=dedupe_entities(value for sample in samples for value in sample.search_tiles),
        batch_positions=dedupe_entities(value for sample in samples for value in sample.batch_positions),
        tilt_series=dedupe_entities(value for sample in samples for value in sample.tilt_series),
    )


def session_summary_sections(session: Session) -> list[SessionSummarySection]:
    if session.kind != SessionKind.MULTIGRID:
        return []
    sections: list[SessionSummarySection] = []
    for sample in session.samples:
        if not (sample.search_maps or sample.batch_positions or sample.tilt_series):
            continue
        sections.append(
            SessionSummarySection(
                label=sample.name,
                fields=[
                    ("Date of data collection", _sample_collection_start_date(sample)),
                    ("Total data collection time", _duration_text(_sample_collection_duration(sample))),
                    ("Data collection magnification", _sample_magnification_text(sample)),
                    ("Pixel size (Original)", _sample_original_pixel_size_text(sample)),
                    ("Detector", _batch_setting_summary(sample.batch_positions, "Detector")),
                    (SEARCH_SPOT_LABEL, _scope_setting_summary(
                        [tilt.metadata for tilt in sample.tilt_series],
                        [batch.metadata for batch in sample.batch_positions],
                        SEARCH_SPOT_LABEL,
                    )),
                    (ACQUISITION_SPOT_LABEL, _scope_setting_summary(
                        [tilt.metadata for tilt in sample.tilt_series],
                        [batch.metadata for batch in sample.batch_positions],
                        ACQUISITION_SPOT_LABEL,
                    )),
                    ("Probe mode", _batch_setting_summary(sample.batch_positions, "Probe mode")),
                    ("Search maps", str(len(sample.search_maps))),
                    ("Search tiles", str(len(sample.search_tiles))),
                    ("Batch positions", str(len(sample.batch_positions))),
                    ("Tilt series", str(len(sample.tilt_series))),
                ],
            )
        )
    return sections


def _atlas_description(atlas: Atlas) -> str:
    metadata = _first_metadata(atlas.metadata)
    blocks: list[str] = ["Atlas"]
    _emit_section(
        blocks,
        "Summary",
        [
            f"Pixel size: {_xml_pixel_size(metadata)}",
            f"Magnification: {_metadata_value(metadata, 'NominalMagnification')}",
            f"Tiles: {len(atlas.tile_paths)}",
            f"Tile metadata files: {len(atlas.tile_metadata_paths)}",
            f"Alignment files: {len(atlas.alignment_paths)}",
        ],
    )
    _emit_section(blocks, "Files", [f"Image: {_path_text(atlas.image_path)}"])
    _emit_section(blocks, "Metadata source", _mrc_metadata_lines(atlas.mrc_metadata))
    _emit_warnings_section(blocks, atlas.warnings)
    return "\n".join(blocks)


def _overview_description(overview: Overview) -> str:
    metadata = _first_metadata(overview.metadata)
    blocks: list[str] = [f"Overview: {overview.name}"]
    _emit_section(
        blocks,
        "Summary",
        [
            f"Pixel size: {_xml_pixel_size(metadata)}",
            f"Linked search maps: {len(overview.linked_search_map_ids)}",
        ],
    )
    _emit_section(blocks, "Files", [f"Image: {overview.image_path}"])
    _emit_warnings_section(blocks, overview.warnings)
    return "\n".join(blocks)


def _search_map_description(search_map: SearchMap) -> str:
    metadata = _first_metadata(search_map.metadata)
    blocks: list[str] = [f"Search map: {search_map.name}"]
    summary_rows = [
        f"Pixel size: {_xml_pixel_size(metadata)}",
        f"Magnification: {_metadata_value(metadata, 'NominalMagnification')}",
        f"Tiles: {len(search_map.tile_paths)}",
        f"Tile metadata files: {len(search_map.tile_metadata_paths)}",
        f"Linked batch positions: {len(search_map.linked_batch_position_ids)}",
        f"Linked tilt series: {len(search_map.linked_tilt_series_ids)}",
    ]
    planned, acquired = _search_map_tile_counts(search_map)
    if planned or acquired:
        summary_rows.append(f"Tile completion: {acquired} of {planned}")
    _emit_section(blocks, "Summary", summary_rows)
    _emit_section(
        blocks,
        "Files",
        [
            f"Image: {_path_text(search_map.image_path)}",
            f"MRC: {_path_text(search_map.mrc_path)}",
            f"XML: {_path_text(search_map.xml_path)}",
        ],
    )
    # Microscope information read directly from SearchMap.xml's
    # microscopeData block. Vacuum / pressure fields were dropped because
    # they don't carry actionable signal for a review session.
    _emit_section(blocks, "Microscope information", _microscope_state_lines(search_map.metadata))
    _emit_warnings_section(blocks, search_map.warnings)
    return "\n".join(blocks)


def _search_tile_description(search_tile: SearchTile) -> str:
    blocks: list[str] = [f"Search tile: {search_tile.name}"]
    _emit_section(
        blocks,
        "Summary",
        [
            f"Search map: {search_tile.search_map_name or 'unknown'}",
            f"Batch position: {search_tile.batch_position_name or 'unknown'}",
            f"Tile index: {search_tile.tile_index if search_tile.tile_index is not None else 'unknown'}",
            f"Acquisition time: {search_tile.acquisition_time or 'unknown'}",
            f"Link method: {search_tile.link_method or 'unresolved'}",
            f"Linked tilt series: {len(search_tile.linked_tilt_series_ids)}",
        ],
    )
    _emit_section(
        blocks,
        "Files",
        [
            f"Image: {_path_text(search_tile.image_path)}",
            f"XML: {_path_text(search_tile.xml_path)}",
        ],
    )
    _emit_section(blocks, "Metadata source", _mrc_metadata_lines(search_tile.mrc_metadata))
    _emit_warnings_section(blocks, search_tile.warnings)
    return "\n".join(blocks)


def _microscope_state_lines(metadata: dict[str, Any]) -> list[str]:
    """Surface instrument-identification fields recorded in SearchMap.xml.

    Vacuum mode, projection-chamber pressure, and sample pressure used to
    appear here, but they're not actionable signal for a review session
    (always ``Ready`` / 0 in practice), so the context panel omits them.
    Implicit pauses are inferred separately by the timeline service and
    clearly labelled there.
    """

    lines: list[str] = []
    instrument = find_first(metadata, "InstrumentModel")
    if isinstance(instrument, str) and instrument:
        lines.append(f"Instrument: {instrument}")

    voltage = find_first(metadata, "AccelerationVoltage")
    if voltage is not None:
        try:
            voltage_value = float(voltage)
            lines.append(f"Acceleration voltage: {voltage_value / 1000:.0f} kV")
        except (TypeError, ValueError):
            pass

    return lines


def _batch_position_description(batch_position: BatchPosition) -> str:
    blocks: list[str] = [f"Batch position: {batch_position.name or batch_position.id}"]
    _emit_section(
        blocks,
        "Summary",
        [
            f"Status: {batch_position.status or 'unknown'}",
            f"Exposure images: {len(batch_position.exposure_image_paths)}",
            f"Linked tilt series: {len(batch_position.linked_tilt_series_ids)}",
        ],
    )
    _emit_section(
        blocks,
        "Acquisition",
        [
            f"{label}: {_acquisition_setting_detail(batch_position.metadata, label)}"
            for label in ("Detector", SEARCH_SPOT_LABEL, ACQUISITION_SPOT_LABEL, "Probe mode")
        ],
    )
    _emit_section(
        blocks,
        "Files",
        [
            f"Search image: {_path_text(batch_position.search_image_path)}",
            f"Tracking image: {_path_text(batch_position.tracking_image_path)}",
        ],
    )
    _emit_warnings_section(blocks, batch_position.warnings)
    return "\n".join(blocks)


def _tilt_series_description(tilt_series: TiltSeries) -> str:
    quality_lines = _tilt_series_quality_lines(tilt_series)
    validation_lines = _tilt_series_validation_lines(tilt_series)
    blocks: list[str] = [f"Tilt series: {tilt_series.name}"]
    _emit_section(
        blocks,
        "Summary",
        [
            f"Tilt count: {tilt_series.tilt_count or 'unknown'}",
            f"Tilt range: {_range_text(tilt_series.tilt_range, 'deg')}",
            f"Tilt increment: {_tilt_increment_text(tilt_series)}",
            f"Pixel size: {_tilt_pixel_size_text(tilt_series)}",
            f"Binning: {_binning_text(tilt_series.binning)}",
            f"Exposure time: {_section_value_text(tilt_series, 'ExposureTime', 's')}",
            f"Target defocus: {_number_text(tilt_series.target_defocus, 'µm')}",
            f"Magnification: {_section_value_text(tilt_series, 'Magnification')}",
            f"Acquisition time: {_duration_text(_series_duration_seconds(tilt_series))}",
        ],
    )
    _emit_section(blocks, "Acquisition", _acquisition_setting_lines([tilt_series.metadata]))
    _emit_section(
        blocks,
        "Files",
        [
            f"MRC: {tilt_series.mrc_path}",
            f"MDOC: {_path_text(tilt_series.mdoc_path)}",
        ],
    )
    _emit_section(blocks, "Validation", validation_lines)
    _emit_section(blocks, "Quality", quality_lines)
    _emit_warnings_section(blocks, tilt_series.warnings)
    return "\n".join(blocks)


def _tilt_series_validation_lines(tilt_series: TiltSeries) -> list[str]:
    """Validation summary used by the right-hand context panel.

    Surfaces the same data the dashboard tile tooltips show — status,
    actual / expected counts, evidence source, and the human-readable
    reason — so the user can see *why* a tilt series was flagged FAILED or
    INCOMPLETE without leaving the Tilt-series tab.
    """

    from tomography_session_browser.services.tilt_series_validation import validate_tilt_series

    try:
        validation = validate_tilt_series(tilt_series)
    except Exception:  # pragma: no cover — defensive: validator must never crash UI
        return []
    expected_text = str(validation.expected_count) if validation.expected_count is not None else "unknown"
    lines = [
        f"Status: {validation.status.upper()}",
        f"Tilt images: {validation.actual_count} / {expected_text} expected",
    ]
    if validation.evidence_source and validation.evidence_source != "none":
        lines.append(f"Expected source: {validation.evidence_source.replace('_', ' ')}")
    if validation.min_tilt is not None and validation.max_tilt is not None:
        if validation.tilt_increment is not None:
            lines.append(
                f"Observed range: {validation.min_tilt:g}° to {validation.max_tilt:g}° "
                f"at {validation.tilt_increment:g}°"
            )
        else:
            lines.append(f"Observed range: {validation.min_tilt:g}° to {validation.max_tilt:g}°")
    if validation.reason:
        lines.append(f"Reason: {validation.reason}")
    return lines


def _tilt_series_quality_lines(tilt: TiltSeries) -> list[str]:
    """Return human-readable quality / completeness lines for a tilt series.

    Per the disk audit, mdoc files do not record explicit ``FocusError`` or
    ``Failed`` flags. The most useful indicators we *can* compute are:
    section-count vs. expected, defocus distribution stats, and a "no stack"
    flag when the .mrc disappears. Anything missing simply doesn't render.
    """

    lines: list[str] = []

    # Stack presence
    if tilt.mrc_path is None or not tilt.mrc_path.exists():
        lines.append("No stack on disk (mrc missing).")
        return lines  # everything else needs the stack

    # Sections vs. expected
    expected = _tilt_expected_sections(tilt)
    actual = len(tilt.sections)
    if expected:
        if actual + 1 < expected:
            lines.append(f"Sections: {actual} of {expected} expected (partial)")
        elif actual >= expected:
            lines.append(f"Sections: {actual} (complete)")
        else:
            lines.append(f"Sections: {actual} of {expected} expected")
    elif actual:
        lines.append(f"Sections: {actual}")

    if not actual:
        lines.append("Mdoc has no sections.")
        return lines

    # Defocus stats
    defocus_values = [
        v for section in tilt.sections
        if (v := _safe_number(section.metadata.get("Defocus"))) is not None
    ]
    if defocus_values:
        med = median(defocus_values)
        lo = min(defocus_values)
        hi = max(defocus_values)
        spread = hi - lo
        line = f"Defocus: median {med:+.2f} um (range {lo:+.2f} → {hi:+.2f}, spread {spread:.2f})"
        if abs(spread) > 1.5 and len(defocus_values) >= 5:
            line += "  ⚠ wide drift"
        lines.append(line)

    return lines


def _tilt_expected_sections(tilt: TiltSeries) -> int | None:
    if tilt.tilt_count and tilt.tilt_count > 0:
        return tilt.tilt_count
    if tilt.tilt_range and len(tilt.tilt_range) == 2:
        a, b = tilt.tilt_range
        if a is not None and b is not None and abs(b - a) > 0:
            return max(int(abs(b - a) / 2), 1)
    return None


def _safe_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
    blocks: list[str] = [f"Sample: {group.label}"]
    # Free-standing kv preserves the legacy ``Atlas: yes/no`` contract that
    # :meth:`MainWindow._context_description` post-processes.
    blocks.append(f"Atlas: {'yes' if group.atlas else 'no'}")
    _emit_section(
        blocks,
        "Summary",
        [
            f"Linked sessions: {len(group.samples)}",
            f"Overviews: {len(group.overviews)}",
            f"Search maps: {len(group.search_maps)}",
            f"Search tiles: {len(group.search_tiles)}",
            f"Batch positions: {len(group.batch_positions)}",
            f"Tilt series: {len(group.tilt_series)}",
        ],
    )
    settings = _acquisition_setting_lines_for_scope(
        [tilt.metadata for tilt in group.tilt_series],
        [batch.metadata for batch in group.batch_positions],
    )
    _emit_section(blocks, "Acquisition", settings)
    _emit_warnings_section(
        blocks, (warning for sample in group.samples for warning in sample.warnings)
    )
    return "\n".join(blocks)


def _search_map_group_description(label: str, values: list[Any]) -> str:
    search_maps = [value for value in values if isinstance(value, SearchMap)]
    metadata_values = [_first_metadata(search_map.metadata) for search_map in search_maps]
    batch_count = sum(len(search_map.linked_batch_position_ids) for search_map in search_maps)
    tilt_count = sum(len(search_map.linked_tilt_series_ids) for search_map in search_maps)
    return "\n".join(
        [
            f"{label}: {len(search_maps)} search maps",
            f"Pixel size: {_common_xml_pixel_size(metadata_values)}",
            f"Magnification: {_common_metadata_value(metadata_values, 'NominalMagnification')}",
            f"Linked batch positions: {batch_count}",
            f"Linked tilt series: {tilt_count}",
            "",
            _warning_block(warning for search_map in search_maps for warning in search_map.warnings),
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
            "",
            _warning_block(warning for tile in search_tiles for warning in tile.warnings),
        ]
    )


def _atlas_group_description(label: str, values: list[Any]) -> str:
    atlases = [value for value in values if isinstance(value, Atlas)]
    metadata_values = [_first_metadata(atlas.metadata) for atlas in atlases]
    return "\n".join(
        [
            f"{label}: {len(atlases)} atlases",
            f"Pixel size: {_common_xml_pixel_size(metadata_values)}",
            f"Magnification: {_common_metadata_value(metadata_values, 'NominalMagnification')}",
            f"Tiles: {sum(len(atlas.tile_paths) for atlas in atlases)}",
            "",
            _warning_block(warning for atlas in atlases for warning in atlas.warnings),
        ]
    )


def _tilt_series_group_description(label: str, values: list[Any]) -> str:
    tilt_series = [value for value in values if isinstance(value, TiltSeries)]
    successful = [tilt for tilt in tilt_series if _is_successful_tilt_series(tilt)]
    durations = [_series_duration_seconds(tilt) for tilt in successful]
    durations = [duration for duration in durations if duration is not None]
    all_dates = [_parse_datetime(value) for tilt in tilt_series for value in (tilt.acquisition_time_start, tilt.acquisition_time_end)]
    all_dates = [value for value in all_dates if value is not None]
    total_duration = (max(all_dates) - min(all_dates)).total_seconds() if len(all_dates) >= 2 else None
    return "\n".join(
        [
            f"{label}: {len(tilt_series)} tilt series",
            f"Successful tilt series: {len(successful)}",
            f"Magnification: {_common_section_value(tilt_series, 'Magnification')}",
            f"Tilt range: {_combined_tilt_range_text(tilt_series)}",
            f"Tilt increment: {_common_tilt_increment(tilt_series)}",
            f"Pixel size: {_common_tilt_pixel_size(tilt_series)}",
            f"Binning: {_common_binning(tilt_series)}",
            f"Exposure time: {_common_section_value(tilt_series, 'ExposureTime', 's')}",
            f"Target defocus: {_target_defocus_range_text(tilt_series)}",
            f"Total collection time: {_duration_text(total_duration)}",
            f"Average successful acquisition time: {_duration_text(sum(durations) / len(durations) if durations else None)}",
            "",
            _warning_block(warning for tilt in tilt_series for warning in tilt.warnings),
        ]
    )


def _warning_block(warnings: Iterable[str]) -> str:
    warning_list = _summarize_warnings(list(warnings))
    if not warning_list:
        return "Warnings: none"
    return "Warnings:\n" + "\n".join(f"  - {warning}" for warning in warning_list[:100])


def _mrc_metadata_lines(metadata: MrcMetadata | None) -> list[str]:
    """Body rows for a ``Metadata source`` section describing one MRC.

    Returns rows WITHOUT the leading section header so the caller can
    place them under whichever section heading is most appropriate
    (typically ``Metadata source`` in the new sectioned context panel).
    Returns an empty list when no metadata is available, so
    :func:`_emit_section` skips the section entirely.
    """

    if metadata is None:
        return []
    rows = [
        f"Dimensions: {_display_value(metadata.nx)} x {_display_value(metadata.ny)} x {_display_value(metadata.nz)}",
        f"Frames: {_display_value(metadata.nz)}",
        f"Mode/dtype: {_display_value(metadata.mode)}",
        f"Voxel size: {_voxel_size_text(metadata.voxel_size)}",
        f"Extended header: {metadata.extended_header_type or 'none'}",
        f"Parsed frame metadata: {len(metadata.frame_metadata)}",
        f"Tilt angles: {len(metadata.tilt_angles)} ({metadata.tilt_angle_source or 'unavailable'})",
    ]
    if metadata.warnings:
        rows.append(f"Parser warnings: {len(metadata.warnings)}")
    return rows


def _mrc_metadata_summary(metadata: MrcMetadata | None) -> str:
    """Legacy text-block summary kept for callers (e.g. PDF report) that
    embed the metadata inline rather than as a sectioned context-panel
    block. New code should prefer :func:`_mrc_metadata_lines` with
    :func:`_emit_section`.
    """

    if metadata is None:
        return "MRC metadata: not available"
    lines = ["MRC metadata:"]
    lines.extend(f"  {row}" for row in _mrc_metadata_lines(metadata))
    return "\n".join(lines)


def _voxel_size_text(value: tuple[float | None, float | None, float | None]) -> str:
    parts = ["unknown" if item is None else f"{item:g}" for item in value]
    return " x ".join(parts)


def _prefix_warnings(prefix: str, warnings: Iterable[str]) -> list[str]:
    return [f"{prefix}: {warning}" for warning in warnings]


def _path_text(path: Path | None) -> str:
    return str(path) if path is not None else "not found"


def _summarize_warnings(warnings: list[str]) -> list[str]:
    nan_fields: Counter[str] = Counter()
    remaining: list[str] = []
    for warning in warnings:
        if "NaN value omitted from numeric summaries" in warning:
            match = re.search(r"\b([A-Za-z][A-Za-z0-9_]*)\s*:\s*NaN value omitted", warning)
            field = match.group(1) if match else "metadata"
            nan_fields[field or "metadata"] += 1
        else:
            remaining.append(warning)
    summarized = [
        f"Multiple NaN values detected in {field} ({count} occurrences)."
        if count > 1
        else f"NaN value detected in {field}."
        for field, count in sorted(nan_fields.items())
    ]
    return summarized + remaining


def _first_metadata(metadata: dict[str, Any]) -> Any:
    for key in sorted(metadata):
        if key.startswith("Atlas_") and key.endswith(".xml"):
            return metadata[key]
    for key in ("SearchMap.xml", "SearchMap.dm", "Atlas.dm", "Overview.xml"):
        if key in metadata:
            return metadata[key]
    return next(iter(metadata.values()), None) if metadata else None


def _metadata_value(metadata: Any, key: str) -> str:
    value = find_first(metadata, key)
    if isinstance(value, dict):
        value = value.get("value")
    return _display_value(value)


def _common_metadata_value(metadata_values: list[Any], key: str) -> str:
    values = [_metadata_value(metadata, key) for metadata in metadata_values]
    return _common_text(values)


def _xml_pixel_size(metadata: Any) -> str:
    pixel_size = find_first(metadata, "pixelSize")
    value = _nested_numeric(pixel_size, "x")
    if value is None:
        return "unknown"
    return _format_length_from_meters(value)


def _common_xml_pixel_size(metadata_values: list[Any]) -> str:
    return _common_text([_xml_pixel_size(metadata) for metadata in metadata_values])


def _nested_numeric(value: Any, key: str) -> float | None:
    if not isinstance(value, dict):
        return None
    nested = value.get(key)
    if isinstance(nested, dict):
        numeric = nested.get("numericValue")
        if isinstance(numeric, int | float):
            return float(numeric)
    return None


def _format_length_from_meters(value: float) -> str:
    angstrom = value * 1e10
    if angstrom < 100:
        return f"{angstrom:.2f} A"
    nm = value * 1e9
    if nm < 1000:
        return f"{nm:.2f} nm"
    return f"{value:.3g} m"


def _display_value(value: Any) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _number_text(value: float | None, unit: str | None = None) -> str:
    if value is None:
        return "unknown"
    suffix = f" {unit}" if unit else ""
    return f"{value:g}{suffix}"


def _acquisition_setting_lines_for_scope(
    tilt_metadata: Iterable[dict[str, Any]],
    batch_metadata: Iterable[dict[str, Any]],
    *,
    include_unknown: bool = False,
) -> list[str]:
    tilt_metadata_list = list(tilt_metadata)
    batch_metadata_list = list(batch_metadata)
    lines: list[str] = []
    for label in ("Detector", SEARCH_SPOT_LABEL, ACQUISITION_SPOT_LABEL, "Probe mode"):
        value = _scope_setting_summary(tilt_metadata_list, batch_metadata_list, label)
        if value == "unknown" and not include_unknown:
            continue
        lines.append(f"{label}: {value}")
    return lines


def _acquisition_setting_lines(
    metadata_values: Iterable[dict[str, Any]],
    *,
    include_unknown: bool = False,
) -> list[str]:
    return _acquisition_setting_lines_for_scope(metadata_values, (), include_unknown=include_unknown)


def _batch_setting_summary(batch_positions: list[BatchPosition], label: str) -> str:
    return summarise_acquisition_setting(
        [batch.metadata for batch in batch_positions],
        label,
        missing="unknown",
    )


def _scope_setting_summary(
    tilt_metadata: Iterable[dict[str, Any]],
    batch_metadata: Iterable[dict[str, Any]],
    label: str,
) -> str:
    tilt_metadata_list = list(tilt_metadata)
    batch_metadata_list = list(batch_metadata)
    if label in {LEGACY_SPOT_LABEL, ACQUISITION_SPOT_LABEL}:
        tilt_value = summarise_acquisition_setting(tilt_metadata_list, label, missing="unknown")
        if tilt_value != "unknown":
            return tilt_value
        return summarise_acquisition_setting(batch_metadata_list, label, missing="unknown")
    if label == SEARCH_SPOT_LABEL:
        return summarise_acquisition_setting(batch_metadata_list, label, missing="unknown")
    return summarise_acquisition_setting(
        [*tilt_metadata_list, *batch_metadata_list],
        label,
        missing="unknown",
    )


def _acquisition_setting_detail(metadata: dict[str, Any], label: str) -> str:
    value = acquisition_setting_value(metadata, label)
    if value is None:
        return "unknown"
    source = acquisition_setting_source(metadata, label)
    return f"{value} ({source})" if source else value


def _range_text(value: tuple[float, float] | None, unit: str | None = None) -> str:
    if value is None:
        return "unknown"
    suffix = f" {unit}" if unit else ""
    return f"{value[0]:g} to {value[1]:g}{suffix}"


def _binning_text(value: int | None) -> str:
    return f"{value}x" if value is not None else "unknown"


def _tilt_pixel_size_text(tilt_series: TiltSeries) -> str:
    if tilt_series.pixel_size is None:
        return "unknown"
    if tilt_series.original_pixel_size and tilt_series.binning and tilt_series.binning > 1:
        return f"{tilt_series.pixel_size:g} A (original {tilt_series.original_pixel_size:g} A, binning {tilt_series.binning}x)"
    return f"{tilt_series.pixel_size:g} A"


def _section_value_text(tilt_series: TiltSeries, key: str, unit: str | None = None) -> str:
    values = _numeric_section_values(tilt_series, key)
    if not values:
        return "unknown"
    suffix = f" {unit}" if unit else ""
    if len(set(values)) == 1:
        return f"{values[0]:g}{suffix}"
    return f"{min(values):g} to {max(values):g}{suffix}"


def _numeric_section_values(tilt_series: TiltSeries, key: str) -> list[float]:
    values: list[float] = []
    for section in tilt_series.sections:
        value = section.metadata.get(key)
        if isinstance(value, int | float):
            values.append(float(value))
    return values


def _tilt_increment_text(tilt_series: TiltSeries) -> str:
    angles = sorted(_numeric_section_values(tilt_series, "TiltAngle"))
    increments = [round(abs(next_angle - angle), 2) for angle, next_angle in zip(angles, angles[1:]) if next_angle != angle]
    if not increments:
        return "unknown"
    return f"{median(increments):g} deg"


def _combined_tilt_range_text(tilt_series: list[TiltSeries]) -> str:
    ranges = [tilt.tilt_range for tilt in tilt_series if tilt.tilt_range is not None]
    if not ranges:
        return "unknown"
    return f"{min(item[0] for item in ranges):g} to {max(item[1] for item in ranges):g} deg"


def _common_tilt_increment(tilt_series: list[TiltSeries]) -> str:
    return _common_text([_tilt_increment_text(tilt) for tilt in tilt_series])


def _common_tilt_pixel_size(tilt_series: list[TiltSeries]) -> str:
    return _common_text([_tilt_pixel_size_text(tilt) for tilt in tilt_series])


def _common_binning(tilt_series: list[TiltSeries]) -> str:
    return _common_text([_binning_text(tilt.binning) for tilt in tilt_series])


def _common_section_value(tilt_series: list[TiltSeries], key: str, unit: str | None = None) -> str:
    return _common_text([_section_value_text(tilt, key, unit) for tilt in tilt_series])


def _target_defocus_range_text(tilt_series: list[TiltSeries]) -> str:
    values = [tilt.target_defocus for tilt in tilt_series if tilt.target_defocus is not None]
    return format_target_defocus_values(values, missing="unknown")


def _common_text(values: list[str]) -> str:
    useful = [value for value in values if value != "unknown"]
    if not useful:
        return "unknown"
    unique = sorted(set(useful))
    if len(unique) == 1:
        return unique[0]
    return f"{unique[0]} to {unique[-1]}"


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    for fmt in ("%d-%b-%Y  %H:%M:%S", "%d-%b-%y  %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _series_duration_seconds(tilt_series: TiltSeries) -> float | None:
    start = _parse_datetime(tilt_series.acquisition_time_start)
    end = _parse_datetime(tilt_series.acquisition_time_end)
    if start is None or end is None:
        return None
    return max((end - start).total_seconds(), 0)


def _duration_text(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    minutes, second = divmod(int(round(seconds)), 60)
    hours, minute = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minute}m {second}s"
    if minute:
        return f"{minute}m {second}s"
    return f"{second}s"


def _is_successful_tilt_series(tilt_series: TiltSeries) -> bool:
    if tilt_series.mrc_metadata is not None and tilt_series.mrc_metadata.size_bytes == 0:
        return False
    return bool(tilt_series.tilt_count and tilt_series.tilt_count > 1)


def _sample_collection_start_date(sample: Sample) -> str:
    starts = [_parse_datetime(tilt.acquisition_time_start) for tilt in sample.tilt_series]
    starts = [value for value in starts if value is not None]
    if not starts:
        return "unknown"
    return min(starts).date().isoformat()


def _sample_collection_duration(sample: Sample) -> float | None:
    starts = [_parse_datetime(tilt.acquisition_time_start) for tilt in sample.tilt_series]
    ends = [_parse_datetime(tilt.acquisition_time_end) for tilt in sample.tilt_series]
    starts = [value for value in starts if value is not None]
    ends = [value for value in ends if value is not None]
    if not starts or not ends:
        return None
    return max((max(ends) - min(starts)).total_seconds(), 0)


def _sample_magnification_text(sample: Sample) -> str:
    values = sorted({value for tilt in sample.tilt_series for value in _numeric_section_values(tilt, "Magnification")})
    if not values:
        return "unknown"
    if len(values) == 1:
        return f"{values[0]:g}"
    return f"{values[0]:g} to {values[-1]:g}"


def _sample_original_pixel_size_text(sample: Sample) -> str:
    values = sorted({tilt.original_pixel_size for tilt in sample.tilt_series if tilt.original_pixel_size is not None})
    if not values:
        return "unknown"
    return ", ".join(f"{value:g}" for value in values)


# =============================================================================
# Dashboard model — graphical Session-tab data
# =============================================================================
#
# The Session tab renders an at-a-glance view (counts cards, completion bars,
# acquisition donut, timeline strip) instead of a tree of strings. Building
# the data here keeps the widget purely presentational and lets us unit-test
# the model without instantiating Qt.


TILE_STATUS_COMPLETE = "complete"
TILE_STATUS_WARNING = "warning"
TILE_STATUS_FAILED = "failed"
TILE_STATUS_MISSING = "missing"
TILE_STATUS_NEUTRAL = "neutral"


@dataclass(slots=True)
class StatusTileModel:
    """One item in a stat card's tile grid.

    ``status`` drives the tile colour; ``id`` lets the dashboard emit a
    navigation signal so clicking a tile selects the underlying entity.
    """

    id: str
    name: str
    status: str  # one of TILE_STATUS_* constants
    tooltip: str
    destination: str = ""  # tab label to switch to on click ("Search map", etc.)


@dataclass(slots=True)
class StatCardModel:
    label: str
    value: int
    # ``sparkline`` is retained as a fallback for callers that haven't yet
    # migrated to the per-item status model. Newer renders prefer the tile
    # grid built from ``items`` / ``status_summary`` and ignore the sparkline.
    sparkline: list[int]
    items: list[StatusTileModel]
    status_summary: dict[str, int]
    destination: str  # tab label to switch to when the card itself is clicked


@dataclass(slots=True)
class TiltSeriesOutcomeModel:
    """Aggregated counts for the dashboard donut.

    Sourced from ``services.tilt_series_validation`` so the categories match
    the four formal statuses (Complete / Incomplete / Failed / Unknown).
    """

    complete: int
    incomplete: int
    failed: int
    unknown: int

    @property
    def total(self) -> int:
        return self.complete + self.incomplete + self.failed + self.unknown

    def as_segments(self) -> list[tuple[str, int]]:
        return [
            ("Complete", self.complete),
            ("Incomplete", self.incomplete),
            ("Failed", self.failed),
            ("Unknown", self.unknown),
        ]


@dataclass(slots=True)
class SearchMapCompletionModel:
    id: str
    label: str
    acquired: int
    planned: int
    # Number of tilt series linked to this search map that the validator
    # marked as failed. Surfaced as a chip beside the bar so users know the
    # raw "30 / 30 tiles" headline doesn't mean every downstream tilt series
    # succeeded. Defaults to 0 so older callers/tests don't need updating.
    failed_tilt_series: int = 0
    failed_batch_groups: int = 0

    @property
    def fraction(self) -> float:
        if self.planned <= 0:
            return 0.0
        return min(self.acquired / self.planned, 1.0)


@dataclass(slots=True)
class SearchMapAcquisitionRollup:
    planned: int
    acquired: int
    tilt_series_ids: list[str]
    failed_exposure_areas: int
    incomplete_tilt_series: int
    unknown_tilt_series: int
    failed_batch_groups: int
    stage_inferred_failed_tilts: int


@dataclass(slots=True)
class SearchMapOverviewRowModel:
    id: str
    label: str
    status: str
    planned: int
    acquired: int
    failed_tilt_series: int
    incomplete_tilt_series: int
    unknown_tilt_series: int
    acquisition_associated: bool = True
    failed_batch_groups: int = 0
    batch_positions: int = 0
    acquired_batch_positions: int = 0

    @property
    def missing(self) -> int:
        if not self.acquisition_associated:
            return 0
        accounted = (
            self.acquired
            + self.failed_tilt_series
            + self.incomplete_tilt_series
            + self.unknown_tilt_series
        )
        return max(self.planned - accounted, 0)

    @property
    def score(self) -> tuple[int, int, int, int, int, str]:
        """Sort key: worst/most actionable rows first."""

        association_rank = 1 if self.acquisition_associated else 0
        status_rank = {
            TILE_STATUS_FAILED: 4,
            TILE_STATUS_WARNING: 3,
            TILE_STATUS_MISSING: 2,
            TILE_STATUS_NEUTRAL: 1,
            TILE_STATUS_COMPLETE: 0,
        }.get(self.status, 0)
        return (
            association_rank,
            status_rank,
            self.failed_tilt_series,
            self.incomplete_tilt_series + self.unknown_tilt_series + self.missing,
            self.planned,
            self.label.casefold(),
        )

    @property
    def summary(self) -> str:
        if not self.acquisition_associated:
            return "N/A"
        return f"{self.acquired_batch_positions}/{self.batch_positions} batch positions"


@dataclass(slots=True)
class SearchMapOverviewModel:
    rows: list[SearchMapOverviewRowModel]
    complete: int
    incomplete: int
    failed: int
    unknown: int
    not_associated: int = 0

    @property
    def total(self) -> int:
        return len(self.rows)

    @property
    def worst(self) -> list[SearchMapOverviewRowModel]:
        return sorted(self.rows, key=lambda row: row.score, reverse=True)[:5]


@dataclass(slots=True)
class DashboardFilterModel:
    label: str
    count: int
    tab: str
    query: str
    tooltip: str


@dataclass(slots=True)
class CollectionHealthModel:
    total_tilt_series: int
    complete: int
    incomplete: int
    failed: int
    unknown: int
    search_maps: int
    batch_positions: int
    samples: int
    acquisition_duration: str | None
    missing_mdoc: int
    orphaned_failed_tilts: int


@dataclass(slots=True)
class WarningRowModel:
    severity: str  # "info" | "warning" | "error"
    sample: str | None
    message: str


@dataclass(slots=True)
class WarningGroupModel:
    label: str
    count: int
    severity: str  # "info" | "warning" | "error"
    items: list[str]


@dataclass(slots=True)
class SampleProgressModel:
    id: str
    label: str
    overviews: int
    search_maps: int
    batch_positions: int
    tilt_series: int
    complete: int
    warning: int
    failed: int
    queued: int

    @property
    def total(self) -> int:
        return self.complete + self.warning + self.failed + self.queued


@dataclass(slots=True)
class BatchPositionProgressModel:
    id: str
    label: str
    exposure_count: int
    tilt_series: int
    complete: int
    warning: int
    failed: int
    queued: int
    status: str
    tooltip: str

    @property
    def total(self) -> int:
        return self.complete + self.warning + self.failed + self.queued


@dataclass(slots=True)
class TimelineItemModel:
    """Display metadata for one timeline segment.

    ``TimelineStrip`` receives these objects as an id-keyed mapping. Keeping
    the shape here lets the presenter reuse validator and batch-position
    metadata without making the painter understand the domain model.
    """

    tilt_series_id: str
    lane_key: str
    lane_label: str
    tooltip: str
    status: str


@dataclass(slots=True)
class AppliedDefocusPointModel:
    """One microscope-applied defocus value parsed from an MDOC section."""

    sample_name: str
    data_collection_name: str
    linked_group_label: str | None
    tilt_series_id: str
    tilt_series_name: str
    frame_index: int
    frame_count: int
    tilt_angle: float | None
    acquisition_time: datetime | None
    frame_order: int
    applied_defocus_um: float
    metadata_source: str
    has_absolute_time: bool
    has_relative_time: bool
    tooltip: str | None = None


@dataclass(slots=True)
class AppliedDefocusPlotModel:
    """Chart-ready applied-defocus data for the dashboard scatter plot."""

    points: list[AppliedDefocusPointModel]
    total_tilt_series: int
    defocus_tilt_series: int
    timestamp_tilt_series: int
    x_mode: str  # "absolute_time" | "frame_order"
    x_axis_label: str
    note: str | None = None

    @property
    def has_points(self) -> bool:
        return bool(self.points)


@dataclass(slots=True)
class DoseInformationPointModel:
    """One camera dose value parsed from MRC per-frame metadata."""

    sample_name: str
    data_collection_name: str
    linked_group_label: str | None
    tilt_series_id: str
    tilt_series_name: str
    frame_index: int
    frame_count: int
    tilt_angle: float | None
    acquisition_time: datetime | None
    frame_order: int
    dose_e_per_angstrom2: float
    metadata_source: str
    has_absolute_time: bool
    tooltip: str | None = None


@dataclass(slots=True)
class DoseInformationPlotModel:
    """Chart-ready camera-dose data for the dashboard scatter plot."""

    points: list[DoseInformationPointModel]
    total_tilt_series: int
    dose_tilt_series: int
    timestamp_tilt_series: int
    x_mode: str  # "absolute_time" | "frame_order"
    x_axis_label: str
    median_dose_e_per_angstrom2: float | None = None
    min_dose_e_per_angstrom2: float | None = None
    max_dose_e_per_angstrom2: float | None = None
    status: str = "neutral"
    status_reason: str | None = None
    source_label: str | None = None
    note: str | None = None

    @property
    def has_points(self) -> bool:
        return bool(self.points)


@dataclass(slots=True)
class _InferredBatchGroup:
    key: str
    label: str
    tilt_series: list[TiltSeries]


@dataclass(slots=True)
class AtlasRowModel:
    sample_label: str
    image_path: str | None  # rendered atlas image (jpg) if present
    image_size: tuple[int, int] | None  # pixels
    pixel_size_um: float | None  # display pixel size in micrometres
    tile_count: int
    acquisition_time: str | None


@dataclass(slots=True)
class AtlasSummaryModel:
    """Atlas-side summary surfaced on the dashboard.

    Distinct from the data-collection summary so atlas-only sessions still
    render meaningful information instead of a row of zero-count cards.

    The model carries two views of "how many atlases":

    * :attr:`counts` — the four-way breakdown
      (:class:`AtlasCountModel`) used by the dashboard widget and the
      PDF cover. This is the corrected, deduplicated number set.
    * :attr:`atlas_count` and :attr:`sample_count_with_atlas` — kept
      for backwards compatibility with older callers that read these
      fields directly. Both are now sourced from
      :class:`AtlasCountModel` so they agree with the four-way counts.

    The legacy ``atlas_count`` is the **sample atlas** count (atlases
    linked to data-collection samples) — the value the user expects to
    see in the "Atlases" header. It is NOT inflated by per-sample
    Atlas/ folders that belong to a screening session with no linked
    data collection.
    """

    atlas_count: int
    sample_count_with_atlas: int
    rows: list[AtlasRowModel]
    pixel_size_range_um: tuple[float, float] | None  # min, max across atlases
    image_size_range: tuple[tuple[int, int], tuple[int, int]] | None  # (smallest, largest)
    counts: AtlasCountModel = field(default_factory=AtlasCountModel)


@dataclass(slots=True)
class DashboardModel:
    """Aggregate model that drives the Session-tab dashboard.

    Two of the three high-level sections are independently optional:

    * ``has_atlases`` toggles the Atlas Summary block.
    * ``has_collection_data`` toggles the Counts row, donut, search-map bars
      and timeline strip.

    Atlas-only sessions render only the Atlas Summary; data-collection-only
    sessions render only the Collection block; mixed sessions render both.
    The widget consumes these flags so we don't show empty zero-count cards
    on a session that was never going to acquire data.
    """

    title: str
    path: str
    kind: str
    microscope: str | None
    acquisition_start: str | None
    acquisition_end: str | None
    acquisition_duration: str | None  # human-readable elapsed time, e.g. "14 hours and 4 minutes"
    completeness: str  # "complete" | "warning" | "error"
    completeness_label: str
    counts: list[StatCardModel]
    outcomes: TiltSeriesOutcomeModel
    applied_defocus: AppliedDefocusPlotModel
    dose_information: DoseInformationPlotModel
    search_map_completion: list[SearchMapCompletionModel]
    search_map_overview: SearchMapOverviewModel
    collection_health: CollectionHealthModel
    filters: list[DashboardFilterModel]
    warnings: list[WarningRowModel]
    warning_groups: list[WarningGroupModel]
    samples: list[SampleProgressModel]
    batch_positions: list[BatchPositionProgressModel]
    timeline_items: dict[str, TimelineItemModel]
    sample_count: int
    is_sample_scope: bool
    has_atlases: bool
    has_collection_data: bool
    atlas_summary: AtlasSummaryModel | None


@dataclass(slots=True)
class _DashboardScope:
    """Normalised view of whatever the user has selected in the project tree.

    The dashboard model is built from this — keeping the scope separate from
    its source object lets us drive the same widget from a top-level
    ``Session``, an individual ``Sample``, a cross-session
    ``LinkedSampleGroup``, or a list of sessions, without scattering
    isinstance checks through the model builder.
    """

    title: str
    path: str
    kind: str
    atlases: list[tuple[str, Atlas]]  # (sample_label_for_display, atlas)
    overviews: list[Overview]
    search_maps: list[SearchMap]
    batch_positions: list[BatchPosition]
    tilt_series: list[TiltSeries]
    samples: list[Sample]
    sample_groups: list[tuple[str, list[Sample]]]
    warnings: list[str]
    is_sample_scope: bool = False


def session_dashboard_model(value: Any) -> DashboardModel:
    """Build a typed model for the Session-tab dashboard.

    Accepts ``Session``, ``Sample``, ``LinkedSampleGroup``, or a list of
    ``Session`` instances and dispatches to the appropriate scope builder.
    Aggregates ``session_counts`` / ``session_warnings`` semantics so the
    dashboard, the text summary, and the warnings panel stay consistent.
    """

    from tomography_session_browser.services.tilt_series_validation import (
        validate_session_tilt_series,
    )

    profile_start = perf_counter()
    scope = _scope_from(value)

    sample_count = len(scope.sample_groups)

    # Validate every tilt series once with session-majority inference, then
    # share the result across the dashboard (donut, tile grid, completion
    # bars, context tooltips). Doing it here keeps the inference consistent.
    validations_start = perf_counter()
    validations = validate_session_tilt_series(scope.tilt_series)
    validations_end = perf_counter()
    validation_by_id = {v.tilt_series_id: v for v in validations}
    inferred_batch_groups = _inferred_failed_batch_groups(
        scope.batch_positions,
        scope.tilt_series,
        validation_by_id,
    )
    orphaned_failed_tilts = sum(
        len(group.tilt_series)
        for group in inferred_batch_groups
        if not _group_matches_any_batch(group, scope.batch_positions)
    )
    batch_position_rows = _batch_position_progress_rows(
        scope.batch_positions,
        scope.tilt_series,
        validation_by_id,
        inferred_batch_groups=inferred_batch_groups,
    )

    # ------------- counts cards (with status tiles)
    cards: list[StatCardModel] = []
    card_specs = [
        ("Atlases", "Atlas", [atlas for _label, atlas in scope.atlases], _atlas_status),
        ("Overviews", "Overview", scope.overviews, _overview_status),
        ("Search maps", "Search map", scope.search_maps, _search_map_status),
        ("Batch positions", "Batch position", scope.batch_positions, _batch_position_status),
        ("Tilt series", "Tilt series", scope.tilt_series, None),  # status comes from validations
    ]
    for label, destination, items, status_fn in card_specs:
        tiles: list[StatusTileModel] = []
        if label == "Batch positions" and scope.is_sample_scope:
            for row in batch_position_rows:
                tiles.append(
                    StatusTileModel(
                        id=row.id,
                        name=row.label,
                        status=row.status,
                        tooltip=row.tooltip,
                        destination=destination,
                    )
                )
            summary = _summarise_statuses(tiles)
            cards.append(
                StatCardModel(
                    label=label,
                    value=len(batch_position_rows),
                    sparkline=[],
                    items=tiles,
                    status_summary=summary,
                    destination=destination,
                )
            )
            continue
        for index, item in enumerate(items):
            if status_fn is not None:
                status = status_fn(item)
                tooltip = _tile_tooltip(item, status)
            else:
                # Tilt series — drive status + tooltip from the validator.
                validation = validation_by_id.get(getattr(item, "id", None))
                if validation is None:
                    status = TILE_STATUS_NEUTRAL
                    tooltip = _tile_tooltip(item, status)
                else:
                    status = _tile_status_from_validation_status(validation.status)
                    tooltip = _validation_tooltip(validation)
            tiles.append(
                StatusTileModel(
                    id=getattr(item, "id", str(index)),
                    name=getattr(item, "name", None) or getattr(item, "id", f"item-{index}"),
                    status=status,
                    tooltip=tooltip,
                    destination=destination,
                )
            )
        summary = _summarise_statuses(tiles)
        spark = _scope_per_sample_counts(scope, label) if len(scope.sample_groups) > 1 else []
        cards.append(
            StatCardModel(
                label=label,
                value=len(items),
                sparkline=spark,
                items=tiles,
                status_summary=summary,
                destination=destination,
            )
        )

    # ------------- tilt series outcomes / applied-defocus audit
    outcomes = _outcomes_for(scope.tilt_series, validations)
    defocus_start = perf_counter()
    applied_defocus = _applied_defocus_plot_model(scope)
    dose_start = perf_counter()
    dose_information = _dose_information_plot_model(scope)
    dose_end = perf_counter()

    # ------------- search map completion bars
    # The bar reports *exposure-area* progress per search map (planned vs.
    # acquired exposures across the linked batch positions). This is a
    # different quantity from the SearchMap mosaic tile count — a search
    # map mosaic might be 60 tiles, but only have 1 batch with 6 planned
    # exposures. ``failed`` counts the search map's downstream tilt
    # series that the validator marked failed, surfaced as a chip
    # alongside the bar.
    from tomography_session_browser.services.tilt_series_validation import (
        STATUS_COMPLETE as _STATUS_COMPLETE,
    )

    successful_tilt_ids = frozenset(
        v.tilt_series_id for v in validations if v.status == _STATUS_COMPLETE
    )
    sm_completion: list[SearchMapCompletionModel] = []
    for search_map in scope.search_maps:
        rollup = search_map_acquisition_rollup(
            search_map,
            scope.search_maps,
            scope.batch_positions,
            scope.tilt_series,
            validation_by_id,
            successful_tilt_ids=successful_tilt_ids,
        )
        if rollup.planned == 0 and rollup.acquired == 0 and rollup.failed_exposure_areas == 0:
            continue  # nothing to show
        sm_completion.append(
            SearchMapCompletionModel(
                id=search_map.id,
                label=search_map.name or search_map.id,
                acquired=rollup.acquired,
                planned=rollup.planned,
                failed_tilt_series=rollup.failed_exposure_areas,
                failed_batch_groups=rollup.failed_batch_groups,
            )
        )
    sm_overview = _search_map_overview_rows(
        scope.search_maps,
        scope.batch_positions,
        scope.tilt_series,
        validations,
        successful_tilt_ids=successful_tilt_ids,
    )

    # ------------- warnings (severity is heuristic — same source data the
    # text view already used)
    warning_rows: list[WarningRowModel] = []
    for raw in scope.warnings:
        sample_name = None
        message = raw
        if ":" in raw:
            head, _, tail = raw.partition(":")
            if head and len(head) < 60:
                sample_name = head.strip().split("/")[0].strip() or None
                message = tail.strip()
        severity = _classify_warning(message)
        warning_rows.append(WarningRowModel(severity=severity, sample=sample_name, message=message))

    unresolved_failed_groups = _unresolved_inferred_failed_batch_groups(
        scope.search_maps,
        scope.batch_positions,
        scope.tilt_series,
        validation_by_id,
    )
    derived_warnings: list[str] = []
    for group in unresolved_failed_groups:
        names = ", ".join(tilt.name or tilt.id for tilt in group.tilt_series)
        message = (
            f"Failed batch {group.label} could not be associated with a search map from "
            f"explicit links or image-stage metadata ({names})."
        )
        warning_rows.append(WarningRowModel(severity="warning", sample=None, message=message))
        derived_warnings.append(message)

    warning_groups = _dashboard_warning_groups([*scope.warnings, *derived_warnings])
    sample_rows = _sample_progress_rows(scope.sample_groups, validation_by_id)
    timeline_items = (
        _timeline_items_for_scope(
            scope.batch_positions,
            scope.tilt_series,
            validation_by_id,
            inferred_batch_groups=inferred_batch_groups,
        )
        if scope.is_sample_scope
        else _timeline_items_by_sample(scope.sample_groups, validation_by_id)
    )

    # ------------- header
    error_count = sum(1 for row in warning_rows if row.severity == "error")
    warn_count = sum(1 for row in warning_rows if row.severity == "warning")
    if error_count:
        completeness = "error"
        completeness_label = f"{error_count} error{'s' if error_count != 1 else ''}"
    elif warn_count:
        completeness = "warning"
        completeness_label = f"{warn_count} warning{'s' if warn_count != 1 else ''}"
    else:
        completeness = "complete"
        completeness_label = "Complete"

    microscope = _scope_microscope_name(scope)
    start, end, duration = _scope_acquisition_window(scope)
    missing_mdoc_count = sum(1 for tilt in scope.tilt_series if tilt.mdoc_path is None or not tilt.sections)
    collection_health = CollectionHealthModel(
        total_tilt_series=outcomes.total,
        complete=outcomes.complete,
        incomplete=outcomes.incomplete,
        failed=outcomes.failed,
        unknown=outcomes.unknown,
        search_maps=len(scope.search_maps),
        batch_positions=len(scope.batch_positions),
        samples=sample_count,
        acquisition_duration=duration,
        missing_mdoc=missing_mdoc_count,
        orphaned_failed_tilts=orphaned_failed_tilts,
    )
    filters = [
        DashboardFilterModel("All", outcomes.total, "Tilt series", "", "Show all tilt series"),
        DashboardFilterModel("Failed", outcomes.failed, "Tilt series", "failed", "Show failed tilt series"),
        DashboardFilterModel("Incomplete", outcomes.incomplete, "Tilt series", "incomplete", "Show incomplete tilt series"),
        DashboardFilterModel("Unknown", outcomes.unknown, "Tilt series", "unknown", "Show tilt series with unknown status"),
        DashboardFilterModel("Missing MDOC", missing_mdoc_count, "Tilt series", "mdoc", "Show tilt series with missing or unusable MDOC metadata"),
        DashboardFilterModel(
            "Orphaned failed tilt series",
            orphaned_failed_tilts,
            "Tilt series",
            "inferred bp",
            "Show failed tilt series whose batch position was inferred from the name",
        ),
    ]

    has_atlases = bool(scope.atlases)
    has_collection_data = any(card.value > 0 for card in cards if card.label != "Atlases")
    # The original ``value`` carries information the flattened scope
    # has thrown away (which sessions are atlas-screening, what the
    # cross-session linker decided), so the atlas counter takes the
    # original input rather than the scope.
    atlas_counts = compute_atlas_counts(value)
    atlas_summary = (
        _atlas_summary_from_scope(scope, atlas_counts) if has_atlases else None
    )

    model = DashboardModel(
        title=scope.title,
        path=scope.path,
        kind=scope.kind,
        microscope=microscope,
        acquisition_start=start,
        acquisition_end=end,
        acquisition_duration=duration,
        completeness=completeness,
        completeness_label=completeness_label,
        counts=cards,
        outcomes=outcomes,
        applied_defocus=applied_defocus,
        dose_information=dose_information,
        search_map_completion=sm_completion,
        search_map_overview=sm_overview,
        collection_health=collection_health,
        filters=filters,
        warnings=warning_rows,
        warning_groups=warning_groups,
        samples=sample_rows,
        batch_positions=batch_position_rows,
        timeline_items=timeline_items,
        sample_count=sample_count,
        is_sample_scope=scope.is_sample_scope,
        has_atlases=has_atlases,
        has_collection_data=has_collection_data,
        atlas_summary=atlas_summary,
    )
    if LOGGER.isEnabledFor(logging.DEBUG):
        LOGGER.debug(
            "dashboard model timings scope=%s kind=%s tilt_series=%d "
            "validation_ms=%.1f defocus_ms=%.1f dose_ms=%.1f total_ms=%.1f "
            "defocus_points=%d dose_points=%d",
            scope.title,
            scope.kind,
            len(scope.tilt_series),
            (validations_end - validations_start) * 1000.0,
            (dose_start - defocus_start) * 1000.0,
            (dose_end - dose_start) * 1000.0,
            (perf_counter() - profile_start) * 1000.0,
            len(applied_defocus.points),
            len(dose_information.points),
        )
    return model


def _dashboard_warning_groups(warnings: list[str]) -> list[WarningGroupModel]:
    """Group raw warning text into compact dashboard rows."""

    rows: list[WarningGroupModel] = []
    for label, items in grouped_warnings(warnings).items():
        severities = [_classify_warning(item) for item in items]
        if "error" in severities:
            severity = "error"
        elif "warning" in severities:
            severity = "warning"
        else:
            severity = "info"
        rows.append(
            WarningGroupModel(
                label=label,
                count=len(items),
                severity=severity,
                items=list(items),
            )
        )
    return rows


def _sample_progress_rows(
    sample_groups: list[tuple[str, list[Sample]]],
    validation_by_id: dict[str, Any],
) -> list[SampleProgressModel]:
    """Return per-sample acquisition status rows for the dashboard.

    Linked sessions can expose the same biological sample twice: once from
    the atlas-screening session and once from the collection session. The
    dashboard receives de-duplicated groups and aggregates their collection
    objects into one visible row.
    """

    rows: list[SampleProgressModel] = []
    for label, samples in sample_groups:
        complete = warning = failed = queued = 0
        batch_positions = [batch for sample in samples for batch in sample.batch_positions]
        tilt_series = _dedupe_tilt_series([tilt for sample in samples for tilt in sample.tilt_series])
        inferred_groups = _inferred_failed_batch_groups(
            batch_positions,
            tilt_series,
            validation_by_id,
        )

        # Prefer tilt-series validation whenever tilt-series exist so failed
        # acquisitions are visible in the linked-session Samples card. Batch
        # status is only a fallback for scopes that have planned positions but
        # no parsed tilt-series records.
        if tilt_series:
            complete, warning, failed, queued = _tilt_status_counts(tilt_series, validation_by_id)
            planned = _planned_tilt_series_for_sample(
                batch_positions,
                tilt_series,
                inferred_groups,
            )
            queued += max(planned - (complete + warning + failed + queued), 0)
        elif batch_positions:
            complete, warning, failed, queued = _tile_status_counts(
                batch_positions, _batch_position_status
            )
        representative = _representative_sample(samples)
        rows.append(
            SampleProgressModel(
                id=representative.id,
                label=label,
                overviews=sum(len(sample.overviews) for sample in samples),
                search_maps=sum(len(sample.search_maps) for sample in samples),
                batch_positions=_display_batch_position_count(batch_positions, inferred_groups),
                tilt_series=len(tilt_series),
                complete=complete,
                warning=warning,
                failed=failed,
                queued=queued,
            )
        )
    return rows


def _dedupe_tilt_series(tilt_series: list[TiltSeries]) -> list[TiltSeries]:
    unique: list[TiltSeries] = []
    seen: set[str] = set()
    for tilt in tilt_series:
        key = tilt.id
        if key in seen:
            continue
        seen.add(key)
        unique.append(tilt)
    return unique


def _planned_tilt_series_for_sample(
    batch_positions: list[BatchPosition],
    tilt_series: list[TiltSeries],
    inferred_groups: list[_InferredBatchGroup],
) -> int:
    planned = sum(
        max(
            planned_exposures_for_batch(batch),
            len(_tilt_series_for_batch(batch, tilt_series))
            + len(_inferred_tilts_for_batch(batch, inferred_groups)),
        )
        for batch in batch_positions
    )
    planned += sum(
        len(group.tilt_series)
        for group in inferred_groups
        if not _group_matches_any_batch(group, batch_positions)
    )
    return max(planned, len(tilt_series))


def _display_batch_position_count(
    batch_positions: list[BatchPosition],
    inferred_groups: list[_InferredBatchGroup],
) -> int:
    inferred_only = sum(
        1 for group in inferred_groups if not _group_matches_any_batch(group, batch_positions)
    )
    return len(batch_positions) + inferred_only


def _batch_position_progress_rows(
    batch_positions: list[BatchPosition],
    tilt_series: list[TiltSeries],
    validation_by_id: dict[str, Any],
    *,
    inferred_batch_groups: list[_InferredBatchGroup] | None = None,
) -> list[BatchPositionProgressModel]:
    """Return sample-level rows that summarise each batch position.

    The selected sample dashboard is about acquisition positions rather than
    the project-wide sample list, so each row counts the tilt series linked to
    one batch position and colours the progress strip from the same validator
    statuses used by the donut and metric tiles.
    """

    rows: list[BatchPositionProgressModel] = []
    inferred_batch_groups = inferred_batch_groups or []
    consumed_inferred_keys: set[str] = set()
    for batch in batch_positions:
        inferred_tilts = _inferred_tilts_for_batch(batch, inferred_batch_groups)
        if inferred_tilts:
            consumed_inferred_keys.add(_inferred_batch_key_from_label(_batch_position_label(batch)))
        linked_tilts = _dedupe_tilt_series(_tilt_series_for_batch(batch, tilt_series) + inferred_tilts)
        exposure_count = _batch_exposure_count(batch, linked_tilts)
        complete, warning, failed, queued = _tilt_status_counts(linked_tilts, validation_by_id)

        planned_remainder = max(exposure_count - len(linked_tilts), 0)
        if planned_remainder:
            queued += planned_remainder

        batch_status = _batch_position_status(batch)
        if batch_status == TILE_STATUS_FAILED and failed == 0:
            if queued > 0:
                queued -= 1
            failed = 1
        elif not linked_tilts and exposure_count == 0:
            if batch_status == TILE_STATUS_COMPLETE:
                complete = 1
            elif batch_status == TILE_STATUS_WARNING:
                warning = 1
            elif batch_status == TILE_STATUS_FAILED:
                failed = 1
            else:
                queued = 1

        status = _dominant_batch_row_status(
            batch_status=batch_status,
            complete=complete,
            warning=warning,
            failed=failed,
            queued=queued,
        )
        label = _batch_position_label(batch)
        rows.append(
            BatchPositionProgressModel(
                id=batch.id,
                label=label,
                exposure_count=exposure_count,
                tilt_series=len(linked_tilts),
                complete=complete,
                warning=warning,
                failed=failed,
                queued=queued,
                status=status,
                tooltip=_batch_position_progress_tooltip(
                    label=label,
                    exposure_count=exposure_count,
                    linked_tilts=len(linked_tilts),
                    complete=complete,
                    warning=warning,
                    failed=failed,
                    queued=queued,
                    batch_status=batch_status,
                    frame_text=_group_tilt_series_frame_text(linked_tilts, validation_by_id)
                    if inferred_tilts
                    else None,
                    inferred=bool(inferred_tilts),
                ),
            )
        )
    for group in inferred_batch_groups:
        if group.key in consumed_inferred_keys or _group_matches_any_batch(group, batch_positions):
            continue
        complete, warning, failed, queued = _tilt_status_counts(group.tilt_series, validation_by_id)
        status = _dominant_batch_row_status(
            batch_status=TILE_STATUS_NEUTRAL,
            complete=complete,
            warning=warning,
            failed=failed,
            queued=queued,
        )
        frame_text = _group_tilt_series_frame_text(group.tilt_series, validation_by_id)
        rows.append(
            BatchPositionProgressModel(
                id=f"inferred:{group.key}",
                label=group.label,
                exposure_count=len(group.tilt_series),
                tilt_series=len(group.tilt_series),
                complete=complete,
                warning=warning,
                failed=failed,
                queued=queued,
                status=status,
                tooltip=_batch_position_progress_tooltip(
                    label=group.label,
                    exposure_count=len(group.tilt_series),
                    linked_tilts=len(group.tilt_series),
                    complete=complete,
                    warning=warning,
                    failed=failed,
                    queued=queued,
                    batch_status=TILE_STATUS_NEUTRAL,
                    frame_text=frame_text,
                    inferred=True,
                ),
            )
        )
    rows.sort(key=lambda row: _natural_label_key(row.label))
    return rows


def _timeline_items_for_scope(
    batch_positions: list[BatchPosition],
    tilt_series: list[TiltSeries],
    validation_by_id: dict[str, Any],
    *,
    inferred_batch_groups: list[_InferredBatchGroup] | None = None,
) -> dict[str, TimelineItemModel]:
    """Build timeline lane labels/tooltips for a sample-like dashboard scope."""

    items: dict[str, TimelineItemModel] = {}
    assigned_tilt_ids: set[str] = set()
    consumed_inferred_keys: set[str] = set()
    inferred_batch_groups = inferred_batch_groups or []
    for batch in batch_positions:
        inferred_tilts = _inferred_tilts_for_batch(batch, inferred_batch_groups)
        if inferred_tilts:
            consumed_inferred_keys.add(_inferred_batch_key_from_label(_batch_position_label(batch)))
        linked_tilts = _dedupe_tilt_series(_tilt_series_for_batch(batch, tilt_series) + inferred_tilts)
        if not linked_tilts:
            continue
        label = _batch_position_label(batch)
        exposure_count = _batch_exposure_count(batch, linked_tilts)
        batch_status = _batch_position_status(batch)
        complete, warning, failed, queued = _tilt_status_counts(linked_tilts, validation_by_id)
        failed_names = _tilt_names_with_status(linked_tilts, validation_by_id, TILE_STATUS_FAILED)
        tooltip = _timeline_group_tooltip(
            label=label,
            exposure_count=exposure_count,
            complete=complete,
            warning=warning,
            failed=failed,
            queued=queued,
            frame_text=_group_tilt_series_frame_text(linked_tilts, validation_by_id),
            inferred=bool(inferred_tilts),
            failed_names=failed_names,
        )
        for tilt in linked_tilts:
            validation = validation_by_id.get(tilt.id)
            status = _timeline_status_for(batch_status, validation)
            assigned_tilt_ids.add(tilt.id)
            items[tilt.id] = TimelineItemModel(
                tilt_series_id=tilt.id,
                lane_key=batch.id,
                lane_label=label,
                tooltip=tooltip,
                status=status,
            )
    for group in inferred_batch_groups:
        if group.key in consumed_inferred_keys or _group_matches_any_batch(group, batch_positions):
            continue
        complete, warning, failed, queued = _tilt_status_counts(group.tilt_series, validation_by_id)
        failed_names = _tilt_names_with_status(group.tilt_series, validation_by_id, TILE_STATUS_FAILED)
        tooltip = _timeline_group_tooltip(
            label=group.label,
            exposure_count=len(group.tilt_series),
            complete=complete,
            warning=warning,
            failed=failed,
            queued=queued,
            frame_text=_group_tilt_series_frame_text(group.tilt_series, validation_by_id),
            inferred=True,
            failed_names=failed_names,
        )
        for tilt in group.tilt_series:
            validation = validation_by_id.get(tilt.id)
            assigned_tilt_ids.add(tilt.id)
            items[tilt.id] = TimelineItemModel(
                tilt_series_id=tilt.id,
                lane_key=f"inferred:{group.key}",
                lane_label=group.label,
                tooltip=tooltip,
                status=_timeline_status_for(TILE_STATUS_NEUTRAL, validation),
            )
    for tilt in tilt_series:
        if tilt.id in assigned_tilt_ids:
            continue
        validation = validation_by_id.get(tilt.id)
        status = _timeline_status_for(TILE_STATUS_NEUTRAL, validation)
        label = tilt.name or tilt.id
        items[tilt.id] = TimelineItemModel(
            tilt_series_id=tilt.id,
            lane_key=tilt.id,
            lane_label=label,
            tooltip=_timeline_unlinked_tilt_tooltip(tilt=tilt, validation=validation, status=status),
            status=status,
        )
    return items


def _timeline_items_by_sample(
    sample_groups: list[tuple[str, list[Sample]]],
    validation_by_id: dict[str, Any],
) -> dict[str, TimelineItemModel]:
    """Group project/linked-session timeline lanes by sample.

    Large linked sessions can contain hundreds of tilt series. At that scale a
    lane per batch position is too dense, so the top-level dashboard groups the
    timeline by the same sample rows shown in the Samples card. Sample-scoped
    dashboards still use the more detailed batch-position lanes above.
    """

    items: dict[str, TimelineItemModel] = {}
    for label, samples in sample_groups:
        tilts = _dedupe_tilt_series([tilt for sample in samples for tilt in sample.tilt_series])
        if not tilts:
            continue
        complete, warning, failed, queued = _tilt_status_counts(tilts, validation_by_id)
        failed_names = _tilt_names_with_status(tilts, validation_by_id, TILE_STATUS_FAILED)
        tooltip_lines = [
            label,
            f"Tilt series: {len(tilts)}",
            f"Complete: {complete}",
            f"Incomplete: {warning}",
            f"Failed: {failed}",
            f"Unknown: {queued}",
        ]
        failed_text = _format_names_for_tooltip("Failed tilt series", failed_names)
        if failed_text:
            tooltip_lines.append(failed_text)
        tooltip = "\n".join(tooltip_lines)
        key = _sample_group_key(label, samples)
        for tilt in tilts:
            validation = validation_by_id.get(tilt.id)
            items[tilt.id] = TimelineItemModel(
                tilt_series_id=tilt.id,
                lane_key=key,
                lane_label=label,
                tooltip=tooltip,
                status=_timeline_status_for(TILE_STATUS_NEUTRAL, validation),
            )
    return items


def _sample_group_key(label: str, samples: list[Sample]) -> str:
    representative = _representative_sample(samples) if samples else None
    return representative.id if representative is not None else batch_inference_key(label)


def _search_map_overview_rows(
    search_maps: list[SearchMap],
    batch_positions: list[BatchPosition],
    tilt_series: list[TiltSeries],
    validations: list[Any],
    *,
    successful_tilt_ids: Iterable[str],
) -> SearchMapOverviewModel:
    validation_by_id = {validation.tilt_series_id: validation for validation in validations}
    rows: list[SearchMapOverviewRowModel] = []
    complete = incomplete = failed = unknown = not_associated = 0

    for search_map in search_maps:
        rollup = search_map_acquisition_rollup(
            search_map,
            search_maps,
            batch_positions,
            tilt_series,
            validation_by_id,
            successful_tilt_ids=successful_tilt_ids,
        )
        linked_batch_ids: set[str] = set(search_map.linked_batch_position_ids or [])
        linked_batches: list[BatchPosition] = []
        seen_linked_batch_objects: set[str] = set()
        for batch in batch_positions:
            if batch.linked_search_map_id == search_map.id:
                linked_batch_ids.add(batch.id)
                linked_batches.append(batch)
                seen_linked_batch_objects.add(batch.id)
            elif batch.id in linked_batch_ids and batch.id not in seen_linked_batch_objects:
                linked_batches.append(batch)
                seen_linked_batch_objects.add(batch.id)
        acquisition_associated = bool(linked_batch_ids or rollup.tilt_series_ids)
        acquired_batch_positions = sum(
            1
            for batch in linked_batches
            if _batch_position_acquired_for_overview(batch, validation_by_id)
        )
        row_failed = rollup.failed_exposure_areas
        row_incomplete = rollup.incomplete_tilt_series
        row_unknown = rollup.unknown_tilt_series

        if not acquisition_associated:
            status = TILE_STATUS_NEUTRAL
            not_associated += 1
        elif row_failed:
            status = TILE_STATUS_FAILED
            failed += 1
        elif rollup.planned <= 0:
            status = TILE_STATUS_NEUTRAL
            unknown += 1
        elif rollup.acquired >= rollup.planned and not (row_incomplete or row_unknown):
            status = TILE_STATUS_COMPLETE
            complete += 1
        elif rollup.acquired <= 0:
            status = TILE_STATUS_MISSING
            unknown += 1
        else:
            status = TILE_STATUS_WARNING
            incomplete += 1

        rows.append(
            SearchMapOverviewRowModel(
                id=search_map.id,
                label=search_map.name or search_map.id,
                status=status,
                planned=rollup.planned,
                acquired=rollup.acquired,
                failed_tilt_series=row_failed,
                incomplete_tilt_series=row_incomplete,
                unknown_tilt_series=row_unknown,
                acquisition_associated=acquisition_associated,
                failed_batch_groups=rollup.failed_batch_groups,
                batch_positions=len(linked_batch_ids) + rollup.failed_batch_groups,
                acquired_batch_positions=acquired_batch_positions,
            )
        )

    rows.sort(key=lambda row: (not row.acquisition_associated, _natural_label_key(row.label)))
    return SearchMapOverviewModel(
        rows=rows,
        complete=complete,
        incomplete=incomplete,
        failed=failed,
        unknown=unknown,
        not_associated=not_associated,
    )


def _batch_position_acquired_for_overview(
    batch: BatchPosition,
    validation_by_id: dict[str, Any],
) -> bool:
    statuses = [
        _tile_status_from_validation_status(validation.status)
        for tilt_id in batch.linked_tilt_series_ids
        if (validation := validation_by_id.get(tilt_id)) is not None
    ]
    if statuses:
        return all(status == TILE_STATUS_COMPLETE for status in statuses)
    return _batch_position_status(batch) == TILE_STATUS_COMPLETE


def _tilt_series_for_batch(
    batch: BatchPosition,
    tilt_series: list[TiltSeries],
) -> list[TiltSeries]:
    linked_ids = set(batch.linked_tilt_series_ids or [])
    linked: list[TiltSeries] = []
    seen: set[str] = set()
    for tilt in tilt_series:
        if tilt.id in seen:
            continue
        if tilt.id in linked_ids or tilt.linked_batch_position_id == batch.id:
            linked.append(tilt)
            seen.add(tilt.id)
    linked.sort(key=lambda tilt: (
        tilt.acquisition_time_start or "",
        natural_key(tilt.name or tilt.id),
    ))
    return linked


def _inferred_failed_batch_groups(
    batch_positions: list[BatchPosition],
    tilt_series: list[TiltSeries],
    validation_by_id: dict[str, Any],
) -> list[_InferredBatchGroup]:
    """Infer batch membership for unlinked failed tilt series by name.

    Tomography 5 sometimes leaves failed exposure attempts without a
    ``linked_batch_position_id`` or BatchPosition reference. We apply this
    conservative heuristic only to validator-failed, explicitly-unlinked tilt
    series: a final numeric repeat suffix is stripped from names shaped like
    ``<sample>_<batch>_<repeat>``. Thus ``vellio_1_2`` groups with
    ``vellio_1``, while ``vellio_1``, ``vellio_2`` and ``vellio_3`` remain
    distinct batch positions.
    """

    explicitly_linked = _explicitly_linked_tilt_ids(batch_positions, tilt_series)
    grouped: dict[str, _InferredBatchGroup] = {}
    for tilt in sorted(tilt_series, key=lambda item: (item.acquisition_time_start or "", natural_key(item.name or item.id))):
        if tilt.id in explicitly_linked or tilt.linked_batch_position_id:
            continue
        validation = validation_by_id.get(tilt.id)
        status = (
            _tile_status_from_validation_status(validation.status)
            if validation is not None
            else TILE_STATUS_NEUTRAL
        )
        if status != TILE_STATUS_FAILED:
            continue
        label = inferred_batch_label_for_tilt(tilt)
        if label is None:
            continue
        key = batch_inference_key(label)
        if key not in grouped:
            grouped[key] = _InferredBatchGroup(key=key, label=label, tilt_series=[])
        grouped[key].tilt_series.append(tilt)
    return list(grouped.values())


def _explicitly_linked_tilt_ids(
    batch_positions: list[BatchPosition],
    tilt_series: list[TiltSeries],
) -> set[str]:
    known_batch_ids = {batch.id for batch in batch_positions}
    linked = {tilt.id for tilt in tilt_series if tilt.linked_batch_position_id in known_batch_ids}
    for batch in batch_positions:
        linked.update(batch.linked_tilt_series_ids or [])
    return linked


def _batch_inference_keys(batch: BatchPosition) -> set[str]:
    return batch_inference_keys(batch)


def _inferred_tilts_for_batch(
    batch: BatchPosition,
    inferred_groups: list[_InferredBatchGroup],
) -> list[TiltSeries]:
    keys = _batch_inference_keys(batch)
    return [
        tilt
        for group in inferred_groups
        if group.key in keys
        for tilt in group.tilt_series
    ]


def _group_matches_any_batch(
    group: _InferredBatchGroup,
    batch_positions: list[BatchPosition],
) -> bool:
    return any(group.key in _batch_inference_keys(batch) for batch in batch_positions)


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


def _tilt_status_counts(
    tilt_series: list[TiltSeries],
    validation_by_id: dict[str, Any],
) -> tuple[int, int, int, int]:
    def _status_of(tilt: TiltSeries) -> str:
        validation = validation_by_id.get(tilt.id)
        return (
            _tile_status_from_validation_status(validation.status)
            if validation is not None
            else TILE_STATUS_NEUTRAL
        )

    return _tile_status_counts(tilt_series, _status_of)


def _batch_position_label(batch: BatchPosition) -> str:
    return batch.name or batch.id or "Batch position"


def _natural_label_key(label: str) -> tuple[Any, ...]:
    parts = re.split(r"(\d+)", label.casefold())
    return tuple(int(part) if part.isdigit() else part for part in parts)


def _batch_exposure_count(batch: BatchPosition, linked_tilts: list[TiltSeries]) -> int:
    planned = planned_exposures_for_batch(batch)
    if planned:
        return max(planned, len(linked_tilts))
    if batch.exposure_image_paths:
        return max(len(batch.exposure_image_paths), len(linked_tilts))
    if batch.linked_tilt_series_ids:
        return max(len(batch.linked_tilt_series_ids), len(linked_tilts))
    return len(linked_tilts)


def _dominant_batch_row_status(
    *,
    batch_status: str,
    complete: int,
    warning: int,
    failed: int,
    queued: int,
) -> str:
    if batch_status == TILE_STATUS_FAILED or failed:
        return TILE_STATUS_FAILED
    if warning:
        return TILE_STATUS_WARNING
    if complete and not queued:
        return TILE_STATUS_COMPLETE
    if complete:
        return TILE_STATUS_WARNING
    return batch_status if batch_status != TILE_STATUS_NEUTRAL else TILE_STATUS_NEUTRAL


def _batch_position_progress_tooltip(
    *,
    label: str,
    exposure_count: int,
    linked_tilts: int,
    complete: int,
    warning: int,
    failed: int,
    queued: int,
    batch_status: str,
    frame_text: str | None = None,
    inferred: bool = False,
) -> str:
    lines = [
        label,
        f"Exposures: {exposure_count}",
        f"Tilt series: {linked_tilts}",
    ]
    if complete:
        lines.append(f"Successful: {complete}")
    if failed:
        lines.append(f"Failed: {failed}")
    if warning:
        lines.append(f"Incomplete: {warning}")
    if queued:
        lines.append(f"Unconfirmed: {queued}")
    if frame_text:
        lines.append(f"Tilt series frames: {frame_text}")
    if inferred:
        lines.append("Inferred batch position from failed tilt series names")
    if batch_status != TILE_STATUS_NEUTRAL:
        lines.append(f"Batch status: {batch_status.replace('_', ' ')}")
    return "\n".join(lines)


def _timeline_status_for(batch_status: str, validation: Any | None) -> str:
    if validation is None:
        return batch_status if batch_status != TILE_STATUS_NEUTRAL else TILE_STATUS_NEUTRAL
    status = _tile_status_from_validation_status(validation.status)
    if status == TILE_STATUS_FAILED:
        return TILE_STATUS_FAILED
    if status == TILE_STATUS_WARNING:
        return TILE_STATUS_WARNING
    if status == TILE_STATUS_COMPLETE:
        return TILE_STATUS_COMPLETE
    # A failed batch with no usable per-tilt verdict should still be visible,
    # but a failed child must not recolour every sibling in the same parent
    # lane. Validation is the most specific unit when it exists.
    if batch_status == TILE_STATUS_FAILED:
        return TILE_STATUS_FAILED
    return batch_status if batch_status != TILE_STATUS_NEUTRAL else TILE_STATUS_NEUTRAL


def _timeline_segment_tooltip(
    *,
    label: str,
    exposure_count: int,
    tilt: TiltSeries,
    validation: Any | None,
    status: str,
) -> str:
    lines = [
        label,
        f"Exposures: {exposure_count}",
        f"Tilt series frames: {_tilt_series_frame_text(tilt, validation)}",
    ]
    pretty_status = status.replace("_", " ")
    if pretty_status and pretty_status != "complete":
        lines.append(f"Status: {pretty_status}")
    return "\n".join(lines)


def _timeline_group_tooltip(
    *,
    label: str,
    exposure_count: int,
    complete: int,
    warning: int,
    failed: int,
    queued: int,
    frame_text: str,
    inferred: bool,
    failed_names: list[str] | None = None,
) -> str:
    lines = [
        label,
        f"Exposures: {exposure_count}",
    ]
    if complete:
        lines.append(f"Successful: {complete}")
    if failed:
        lines.append(f"Failed: {failed}")
    if warning:
        lines.append(f"Incomplete: {warning}")
    if queued:
        lines.append(f"Unconfirmed: {queued}")
    lines.append(f"Tilt series frames: {frame_text}")
    failed_text = _format_names_for_tooltip("Failed tilt series", failed_names or [])
    if failed_text:
        lines.append(failed_text)
    if inferred:
        lines.append("Inferred batch position from failed tilt series names")
    return "\n".join(lines)


def _timeline_unlinked_tilt_tooltip(
    *,
    tilt: TiltSeries,
    validation: Any | None,
    status: str,
) -> str:
    lines = [
        tilt.name or tilt.id,
        "Batch position: unavailable",
        f"Tilt series frames: {_tilt_series_frame_text(tilt, validation)}",
    ]
    pretty_status = status.replace("_", " ")
    if pretty_status and pretty_status != "complete":
        lines.append(f"Status: {pretty_status}")
    return "\n".join(lines)


def _group_tilt_series_frame_text(
    tilt_series: list[TiltSeries],
    validation_by_id: dict[str, Any],
) -> str:
    values: list[str] = []
    seen: set[str] = set()
    for tilt in tilt_series:
        value = _tilt_series_expected_frame_text(tilt, validation_by_id.get(tilt.id))
        if value in seen:
            continue
        values.append(value)
        seen.add(value)
    if not values:
        return "unknown"
    if len(values) <= 3:
        return ", ".join(values)
    return f"{', '.join(values[:3])}, +{len(values) - 3} more"


def _tilt_names_with_status(
    tilt_series: list[TiltSeries],
    validation_by_id: dict[str, Any],
    status: str,
) -> list[str]:
    names: list[str] = []
    for tilt in tilt_series:
        validation = validation_by_id.get(tilt.id)
        if _timeline_status_for(TILE_STATUS_NEUTRAL, validation) == status:
            names.append(tilt.name or tilt.id)
    return names


def _format_names_for_tooltip(label: str, names: list[str], *, limit: int = 8) -> str:
    if not names:
        return ""
    if len(names) <= limit:
        return f"{label}: {', '.join(names)}"
    shown = ", ".join(names[:limit])
    return f"{label}: {shown}, +{len(names) - limit} more"


def _tilt_series_expected_frame_text(tilt: TiltSeries, validation: Any | None) -> str:
    expected = getattr(validation, "expected_count", None) if validation is not None else None
    if expected:
        return str(expected)
    actual = getattr(validation, "actual_count", None) if validation is not None else None
    if actual:
        return str(actual)
    if tilt.tilt_count:
        return str(tilt.tilt_count)
    if tilt.mrc_metadata is not None and tilt.mrc_metadata.nz:
        return str(tilt.mrc_metadata.nz)
    if tilt.sections:
        return str(len(tilt.sections))
    return "unknown"


def _tilt_series_frame_text(tilt: TiltSeries, validation: Any | None) -> str:
    actual = getattr(validation, "actual_count", None) if validation is not None else None
    expected = getattr(validation, "expected_count", None) if validation is not None else None
    if expected:
        if actual is not None and actual != expected:
            return f"{actual} / {expected} expected"
        return str(expected)
    if actual:
        return str(actual)
    if tilt.tilt_count:
        return str(tilt.tilt_count)
    if tilt.mrc_metadata is not None and tilt.mrc_metadata.nz:
        return str(tilt.mrc_metadata.nz)
    if tilt.sections:
        return str(len(tilt.sections))
    return "unknown"


def _sample_groups_from_samples(samples: list[Sample]) -> list[tuple[str, list[Sample]]]:
    return [(_clean_sample_label(sample.name), [sample]) for sample in samples]


def _linked_sample_groups_for_dashboard(sessions: list[Session]) -> list[tuple[str, list[Sample]]]:
    groups: list[tuple[str, list[Sample]]] = []
    for samples in linked_sample_groups(sessions):
        members = list(samples)
        if not _sample_group_is_active(members):
            continue
        groups.append((_sample_group_label(members), members))
    return groups


def _sample_group_is_active(samples: list[Sample]) -> bool:
    if any(is_data_collection_sample(sample) for sample in samples):
        return True
    return any(sample.atlas is not None and not _is_single_atlas_placeholder(sample) for sample in samples)


def _sample_group_label(samples: list[Sample], fallback: str | None = None) -> str:
    atlas_sample = next(
        (
            sample
            for sample in samples
            if sample.atlas is not None and not _is_single_atlas_placeholder(sample)
        ),
        None,
    )
    if atlas_sample is not None:
        return _full_sample_label(atlas_sample.name)

    collection = next(
        (
            sample
            for sample in samples
            if is_data_collection_sample(sample) and not _is_generic_sample_label(sample.name)
        ),
        None,
    )
    if collection is None:
        collection = next((sample for sample in samples if is_data_collection_sample(sample)), None)
    chosen = collection or (samples[0] if samples else None)
    return _full_sample_label(chosen.name if chosen is not None else (fallback or "Sample"))


def _representative_sample(samples: list[Sample]) -> Sample:
    collection = next((sample for sample in samples if is_data_collection_sample(sample)), None)
    if collection is not None:
        return collection
    atlas_sample = next(
        (
            sample
            for sample in samples
            if sample.atlas is not None and not _is_single_atlas_placeholder(sample)
        ),
        None,
    )
    return atlas_sample or samples[0]


def _clean_sample_label(label: str) -> str:
    text = re.sub(r"^\s*\d+\.\s*", "", label or "").strip()
    text = re.sub(r"^SSK[_-]+", "", text, flags=re.IGNORECASE).strip()
    if not text:
        return "Sample"
    if _is_generic_sample_label(text):
        return text
    text = text.replace("_", " ").strip()
    return " ".join(part.capitalize() if part.islower() else part for part in text.split())


def _full_sample_label(label: str) -> str:
    text = re.sub(r"^\s*\d+\.\s*", "", label or "").strip()
    return text or "Sample"


def _is_single_atlas_placeholder(sample: Sample) -> bool:
    return "single atlas" in sample.name.lower() and not is_data_collection_sample(sample)


def _is_generic_sample_label(label: str) -> bool:
    return bool(re.fullmatch(r"\s*(?:\d+\.\s*)?sample\d+\s*", label or "", flags=re.IGNORECASE))


# ---------------------------------------------------------------- scope adapters


def _scope_from(value: Any) -> _DashboardScope:
    """Dispatch to the right scope-builder for the user's tree selection."""

    if isinstance(value, LinkedAtlasDashboardScope):
        return _scope_from_linked_atlas_dashboard_scope(value)
    if isinstance(value, DashboardEntityScope):
        return _scope_from_dashboard_entity_scope(value)
    if isinstance(value, Session):
        return _scope_from_session(value)
    if isinstance(value, Sample):
        return _scope_from_sample(value)
    if isinstance(value, TiltSeries):
        return _scope_from_tilt_series(value)
    if isinstance(value, LinkedSampleGroup):
        return _scope_from_linked_group(value)
    if isinstance(value, list):
        return _scope_from_session_list(value)
    raise TypeError(f"session_dashboard_model does not handle {type(value).__name__}")


def _scope_from_linked_atlas_dashboard_scope(value: LinkedAtlasDashboardScope) -> _DashboardScope:
    base = _scope_from(value.source)
    return _DashboardScope(
        title=base.title,
        path=base.path,
        kind=base.kind,
        atlases=list(value.atlases),
        overviews=base.overviews,
        search_maps=base.search_maps,
        batch_positions=base.batch_positions,
        tilt_series=base.tilt_series,
        samples=base.samples,
        sample_groups=base.sample_groups,
        warnings=base.warnings,
        is_sample_scope=True,
    )


def _scope_from_dashboard_entity_scope(value: DashboardEntityScope) -> _DashboardScope:
    source = value.source
    title = value.title or getattr(source, "name", None) or getattr(source, "id", "Selection")
    path = value.path
    if path is None:
        path_candidates = (
            getattr(source, "mrc_path", None),
            getattr(source, "image_path", None),
            getattr(source, "xml_path", None),
            getattr(source, "overview_image_path", None),
            getattr(source, "search_image_path", None),
            getattr(source, "tracking_image_path", None),
            getattr(source, "exposure_image_path", None),
        )
        path = str(next((candidate for candidate in path_candidates if candidate is not None), ""))
    warnings = list(getattr(source, "warnings", []) or [])
    return _DashboardScope(
        title=title,
        path=path,
        kind=value.kind or type(source).__name__.replace("_", " ").lower(),
        atlases=[],
        overviews=[],
        search_maps=list(value.search_maps),
        batch_positions=list(value.batch_positions),
        tilt_series=list(value.tilt_series),
        samples=list(value.samples),
        sample_groups=list(value.sample_groups),
        warnings=warnings,
        is_sample_scope=True,
    )


def _scope_from_session(session: Session) -> _DashboardScope:
    atlases = _all_atlases(session)
    return _DashboardScope(
        title=session.name,
        path=str(session.path),
        kind=session.kind.value if hasattr(session.kind, "value") else str(session.kind),
        atlases=atlases,
        overviews=_all_overviews(session),
        search_maps=_all_search_maps(session),
        batch_positions=_all_batch_positions(session),
        tilt_series=_all_tilt_series(session),
        samples=list(session.samples),
        sample_groups=_sample_groups_from_samples(session.samples),
        warnings=session_warnings(session),
    )


def _scope_from_sample(sample: Sample) -> _DashboardScope:
    atlases: list[tuple[str, Atlas]] = []
    if sample.atlas is not None:
        atlases.append((sample.name, sample.atlas))
    return _DashboardScope(
        title=sample.name,
        path=str(sample.path),
        kind="sample",
        atlases=atlases,
        overviews=list(sample.overviews),
        search_maps=list(sample.search_maps),
        batch_positions=list(sample.batch_positions),
        tilt_series=list(sample.tilt_series),
        samples=[sample],
        sample_groups=[(_clean_sample_label(sample.name), [sample])],
        warnings=_prefix_warnings(sample.name, sample.warnings),
        is_sample_scope=True,
    )


def _scope_from_tilt_series(tilt: TiltSeries) -> _DashboardScope:
    """Narrow dashboard scope used when a tilt-series row is selected."""

    label = tilt.name or tilt.id
    return _DashboardScope(
        title=label,
        path=str(tilt.mrc_path),
        kind="tilt series",
        atlases=[],
        overviews=[],
        search_maps=[],
        batch_positions=[],
        tilt_series=[tilt],
        samples=[],
        sample_groups=[],
        warnings=list(tilt.warnings),
        is_sample_scope=True,
    )


def _scope_from_linked_group(group: LinkedSampleGroup) -> _DashboardScope:
    """Linked groups span sessions: aggregate everything the linker assigned."""

    atlases: list[tuple[str, Atlas]] = []
    for member in group.samples:
        if member.atlas is not None:
            atlases.append((member.name, member.atlas))
    if not atlases and group.atlas is not None:
        atlases.append((group.label, group.atlas))

    warnings: list[str] = []
    for member in group.samples:
        warnings.extend(_prefix_warnings(member.name, member.warnings))

    primary_path = ""
    for member in group.samples:
        if member.path:
            primary_path = str(member.path)
            break

    return _DashboardScope(
        title=group.label,
        path=primary_path,
        kind="linked sample group",
        atlases=atlases,
        overviews=list(group.overviews),
        search_maps=list(group.search_maps),
        batch_positions=list(group.batch_positions),
        tilt_series=list(group.tilt_series),
        samples=[_representative_sample(group.samples)],
        sample_groups=[(_sample_group_label(group.samples, fallback=group.label), list(group.samples))],
        warnings=warnings,
        is_sample_scope=True,
    )


def _scope_from_session_list(sessions: list[Session]) -> _DashboardScope:
    """Aggregate scope across multiple loaded sessions (the project root view)."""

    if not sessions:
        return _DashboardScope(
            title="No sessions loaded",
            path="",
            kind="empty",
            atlases=[],
            overviews=[],
            search_maps=[],
            batch_positions=[],
            tilt_series=[],
            samples=[],
            sample_groups=[],
            warnings=[],
        )
    if len(sessions) == 1:
        return _scope_from_session(sessions[0])

    atlases: list[tuple[str, Atlas]] = []
    overviews: list[Overview] = []
    search_maps: list[SearchMap] = []
    batch_positions: list[BatchPosition] = []
    tilt_series: list[TiltSeries] = []
    warnings: list[str] = []
    for session in sessions:
        atlases.extend(_all_atlases(session))
        overviews.extend(_all_overviews(session))
        search_maps.extend(_all_search_maps(session))
        batch_positions.extend(_all_batch_positions(session))
        tilt_series.extend(_all_tilt_series(session))
        warnings.extend(session_warnings(session))
    atlases = _dedupe_atlas_pairs(atlases)
    overviews = dedupe_entities(overviews)
    search_maps = dedupe_entities(search_maps)
    batch_positions = dedupe_entities(batch_positions)
    tilt_series = dedupe_entities(tilt_series)

    sample_groups = _linked_sample_groups_for_dashboard(sessions)
    samples = [_representative_sample(group_samples) for _, group_samples in sample_groups]

    title = " + ".join(session.name for session in sessions)
    return _DashboardScope(
        title=title,
        path=str(sessions[0].path),
        kind="linked sessions",
        atlases=atlases,
        overviews=overviews,
        search_maps=search_maps,
        batch_positions=batch_positions,
        tilt_series=tilt_series,
        samples=samples,
        sample_groups=sample_groups,
        warnings=warnings,
    )


def _scope_count(scope: _DashboardScope, label: str) -> int:
    if label == "Atlases":
        return len(scope.atlases)
    mapping = {
        "Overviews": "overviews",
        "Search maps": "search_maps",
        "Batch positions": "batch_positions",
        "Tilt series": "tilt_series",
    }
    return len(getattr(scope, mapping[label]))


def _dedupe_atlas_pairs(values: Iterable[tuple[str, Atlas]]) -> list[tuple[str, Atlas]]:
    unique: list[tuple[str, Atlas]] = []
    seen: set[tuple[str, str]] = set()
    for label, atlas in values:
        key = canonical_entity_key(atlas)
        if key in seen:
            continue
        seen.add(key)
        unique.append((label, atlas))
    return unique


def _scope_per_sample_counts(scope: _DashboardScope, label: str) -> list[int]:
    mapping = {
        "Overviews": "overviews",
        "Search maps": "search_maps",
        "Batch positions": "batch_positions",
        "Tilt series": "tilt_series",
    }
    attr = mapping.get(label)
    if attr is None:
        return []
    return [
        sum(len(getattr(sample, attr, [])) for sample in samples)
        for _, samples in scope.sample_groups
    ]


def _atlas_status(atlas: Atlas) -> str:
    """Status for a sample atlas tile in the sample-level metric row."""

    image_path = getattr(atlas, "image_path", None)
    if image_path is None:
        return TILE_STATUS_NEUTRAL
    try:
        return TILE_STATUS_COMPLETE if image_path.exists() else TILE_STATUS_FAILED
    except OSError:
        return TILE_STATUS_FAILED


def _overview_status(overview: Overview) -> str:
    """Status drives the tile colour for an Overview entry.

    The MRC/JPG image must be present on disk for the overview to render in
    the viewer; missing files are flagged so the user notices a parser gap
    without opening another panel.
    """

    image_path = getattr(overview, "image_path", None)
    if image_path is None:
        return TILE_STATUS_MISSING
    try:
        return TILE_STATUS_COMPLETE if image_path.exists() else TILE_STATUS_FAILED
    except OSError:
        return TILE_STATUS_FAILED


def _search_map_status(search_map: SearchMap) -> str:
    """Search-map status from the planned/acquired tile counts."""

    planned, acquired = _search_map_tile_counts(search_map)
    if planned <= 0:
        # Nothing planned — treat as a "loaded" neutral; we have no completion
        # signal to flag this against. Most often this happens when the
        # parser couldn't read GridSize from the XML.
        return TILE_STATUS_NEUTRAL
    if acquired >= planned:
        return TILE_STATUS_COMPLETE
    if acquired <= 0:
        return TILE_STATUS_MISSING
    return TILE_STATUS_WARNING


def _batch_position_status(batch: BatchPosition) -> str:
    """Map ``BatchPositionStatus`` to a tile state."""

    raw = (batch.status or "").strip().lower()
    if raw in ("acquired", "done", "completed", "complete"):
        return TILE_STATUS_COMPLETE
    if raw in ("pending", "queued", "scheduled", ""):
        return TILE_STATUS_NEUTRAL
    if raw in ("failed", "error"):
        return TILE_STATUS_FAILED
    return TILE_STATUS_WARNING


def _tilt_series_status(tilt: TiltSeries) -> str:
    """Single-series fallback status. Prefer the validator-driven path that
    the dashboard uses when it has a session-level context (which lets the
    majority-vote inference kick in)."""

    from tomography_session_browser.services.tilt_series_validation import (
        STATUS_COMPLETE as V_COMPLETE,
        STATUS_FAILED as V_FAILED,
        STATUS_INCOMPLETE as V_INCOMPLETE,
        validate_tilt_series,
    )

    validation = validate_tilt_series(tilt)
    return _tile_status_from_validation_status(validation.status)


def _tile_status_from_validation_status(validation_status: str) -> str:
    """Map a TiltSeriesValidation status onto the StatusTileModel palette.

    * complete   → green tile
    * incomplete → amber tile
    * failed     → red tile
    * unknown    → grey/neutral tile
    """

    from tomography_session_browser.services.tilt_series_validation import (
        STATUS_COMPLETE as V_COMPLETE,
        STATUS_FAILED as V_FAILED,
        STATUS_INCOMPLETE as V_INCOMPLETE,
    )

    if validation_status == V_COMPLETE:
        return TILE_STATUS_COMPLETE
    if validation_status == V_INCOMPLETE:
        return TILE_STATUS_WARNING
    if validation_status == V_FAILED:
        return TILE_STATUS_FAILED
    return TILE_STATUS_NEUTRAL


def _tile_tooltip(item: Any, status: str) -> str:
    name = getattr(item, "name", None) or getattr(item, "id", "<unknown>")
    pretty_status = status.replace("_", " ").capitalize()
    return f"{name}\nStatus: {pretty_status}"


def _summarise_statuses(tiles: list[StatusTileModel]) -> dict[str, int]:
    """Count tiles per status for the chip strip and grouped fallback."""

    summary: dict[str, int] = {}
    for tile in tiles:
        summary[tile.status] = summary.get(tile.status, 0) + 1
    return summary


def _outcomes_for(
    tilt_series: list[TiltSeries], validations: list | None = None
) -> TiltSeriesOutcomeModel:
    """Aggregate validations into the donut model.

    Pass pre-computed ``validations`` when the caller already has them
    (avoids re-running session-majority inference for every card). When
    ``None`` the helper validates the list itself, which is convenient for
    one-off uses.
    """

    from tomography_session_browser.services.tilt_series_validation import (
        STATUS_COMPLETE as V_COMPLETE,
        STATUS_FAILED as V_FAILED,
        STATUS_INCOMPLETE as V_INCOMPLETE,
        STATUS_UNKNOWN as V_UNKNOWN,
        validate_session_tilt_series,
    )

    if validations is None:
        validations = validate_session_tilt_series(tilt_series)

    complete = sum(1 for v in validations if v.status == V_COMPLETE)
    incomplete = sum(1 for v in validations if v.status == V_INCOMPLETE)
    failed = sum(1 for v in validations if v.status == V_FAILED)
    unknown = sum(1 for v in validations if v.status == V_UNKNOWN)
    return TiltSeriesOutcomeModel(
        complete=complete, incomplete=incomplete, failed=failed, unknown=unknown
    )


def _applied_defocus_plot_model(scope: _DashboardScope) -> AppliedDefocusPlotModel:
    """Build chart-ready MDOC applied-defocus points for a dashboard scope.

    SerialEM/Tomo5 MDOC ``Defocus`` and ``TargetDefocus`` values are already
    treated as micrometres elsewhere in this app's reports, so the dashboard
    keeps the parsed numeric value unchanged and labels it as applied/target
    defocus, not measured CTF defocus.
    """

    contexts = _tilt_series_label_contexts(scope)
    raw_points: list[AppliedDefocusPointModel] = []
    defocus_tilt_ids: set[str] = set()
    timestamp_tilt_ids: set[str] = set()
    frame_order = 0

    for tilt in scope.tilt_series:
        sample_name, linked_group = contexts.get(id(tilt), (scope.title or "Selection", None))
        frame_count = _frame_count_for_applied_defocus(tilt)
        for section_index, section in enumerate(tilt.sections, start=1):
            value, source_field = _section_applied_defocus_um(section.metadata)
            if value is None:
                continue
            frame_order += 1
            timestamp = parse_section_datetime(section.metadata)
            tilt_angle = _safe_number(section.metadata.get("TiltAngle"))
            defocus_tilt_ids.add(tilt.id)
            if timestamp is not None:
                timestamp_tilt_ids.add(tilt.id)
            source_label = f"MDOC {source_field}"
            point = AppliedDefocusPointModel(
                sample_name=sample_name,
                data_collection_name=sample_name,
                linked_group_label=linked_group,
                tilt_series_id=tilt.id,
                tilt_series_name=tilt.name or tilt.id,
                frame_index=section_index,
                frame_count=frame_count,
                tilt_angle=tilt_angle,
                acquisition_time=timestamp,
                frame_order=frame_order,
                applied_defocus_um=value,
                metadata_source=source_label,
                has_absolute_time=timestamp is not None,
                has_relative_time=False,
            )
            raw_points.append(point)

    if not raw_points:
        return AppliedDefocusPlotModel(
            points=[],
            total_tilt_series=len(scope.tilt_series),
            defocus_tilt_series=0,
            timestamp_tilt_series=0,
            x_mode="frame_order",
            x_axis_label="Frame order",
        )

    timestamped_points = [point for point in raw_points if point.acquisition_time is not None]
    if timestamped_points:
        points = timestamped_points
        x_mode = "absolute_time"
        x_axis_label = "Acquisition time"
        note = None
        if len(timestamp_tilt_ids) < len(defocus_tilt_ids):
            note = (
                f"Timestamps available for {len(timestamp_tilt_ids):,} / "
                f"{len(defocus_tilt_ids):,} tilt series with applied defocus."
            )
    else:
        points = raw_points
        x_mode = "frame_order"
        x_axis_label = "Frame order"
        note = "Acquisition timestamps unavailable; points are shown in frame order."

    return AppliedDefocusPlotModel(
        points=points,
        total_tilt_series=len(scope.tilt_series),
        defocus_tilt_series=len(defocus_tilt_ids),
        timestamp_tilt_series=len(timestamp_tilt_ids),
        x_mode=x_mode,
        x_axis_label=x_axis_label,
        note=note,
    )


def _dose_information_plot_model(scope: _DashboardScope) -> DoseInformationPlotModel:
    """Build camera-dose-per-image points from MRC per-frame metadata.

    Only explicit dose fields parsed from the MRC frame metadata are used.
    Dose-fraction bookkeeping fields are deliberately ignored because they do
    not represent camera dose per tilt image.
    """

    contexts = _tilt_series_label_contexts(scope)
    points: list[DoseInformationPointModel] = []
    dose_tilt_ids: set[str] = set()
    timestamp_tilt_ids: set[str] = set()
    source_labels: set[str] = set()
    frame_order = 0

    for tilt in scope.tilt_series:
        frame_metadata = tilt.mrc_metadata.frame_metadata if tilt.mrc_metadata is not None else []
        if not frame_metadata:
            continue
        sample_name, linked_group = contexts.get(id(tilt), (scope.title or "Selection", None))
        frame_count = _frame_count_for_applied_defocus(tilt)
        for frame_index, metadata in enumerate(frame_metadata, start=1):
            dose, source = _camera_dose_from_frame_metadata(metadata)
            if dose is None:
                continue
            frame_order += 1
            timestamp = _frame_metadata_datetime(metadata)
            if timestamp is not None:
                timestamp_tilt_ids.add(tilt.id)
            tilt_angle = _safe_number(metadata.get("tilt_angle"))
            dose_tilt_ids.add(tilt.id)
            source_label = source or "MRC frame metadata"
            source_labels.add(source_label)
            point = DoseInformationPointModel(
                sample_name=sample_name,
                data_collection_name=sample_name,
                linked_group_label=linked_group,
                tilt_series_id=tilt.id,
                tilt_series_name=tilt.name or tilt.id,
                frame_index=frame_index,
                frame_count=frame_count,
                tilt_angle=tilt_angle,
                acquisition_time=timestamp,
                frame_order=frame_order,
                dose_e_per_angstrom2=dose,
                metadata_source=source_label,
                has_absolute_time=timestamp is not None,
            )
            points.append(point)

    total_tilt_series = len(scope.tilt_series)
    if not points:
        return DoseInformationPlotModel(
            points=[],
            total_tilt_series=total_tilt_series,
            dose_tilt_series=0,
            timestamp_tilt_series=0,
            x_mode="frame_order",
            x_axis_label="Frame order",
        )

    if timestamp_tilt_ids and len(timestamp_tilt_ids) == len(dose_tilt_ids):
        x_mode = "absolute_time"
        x_axis_label = "Acquisition time"
        note = None
    else:
        x_mode = "frame_order"
        x_axis_label = "Frame order"
        if timestamp_tilt_ids:
            note = (
                f"Acquisition timestamps available for {len(timestamp_tilt_ids):,}/"
                f"{len(dose_tilt_ids):,} tilt series with dose metadata; "
                "points are shown in frame order."
            )
        else:
            note = "Acquisition timestamps unavailable; points are shown in frame order."

    if total_tilt_series and len(dose_tilt_ids) < total_tilt_series:
        partial_note = f"Dose metadata available for {len(dose_tilt_ids):,}/{total_tilt_series:,} tilt series."
        note = f"{partial_note} {note}" if note else partial_note

    values = [point.dose_e_per_angstrom2 for point in points]
    median_dose = median(values)
    min_dose = min(values)
    max_dose = max(values)
    status, status_reason = _dose_information_status(
        values,
        partial=bool(total_tilt_series and len(dose_tilt_ids) < total_tilt_series),
    )

    return DoseInformationPlotModel(
        points=points,
        total_tilt_series=total_tilt_series,
        dose_tilt_series=len(dose_tilt_ids),
        timestamp_tilt_series=len(timestamp_tilt_ids),
        x_mode=x_mode,
        x_axis_label=x_axis_label,
        median_dose_e_per_angstrom2=median_dose,
        min_dose_e_per_angstrom2=min_dose,
        max_dose_e_per_angstrom2=max_dose,
        status=status,
        status_reason=status_reason,
        source_label=_dose_source_label(source_labels),
        note=note,
    )

def _tilt_series_label_contexts(scope: _DashboardScope) -> dict[int, tuple[str, str | None]]:
    contexts: dict[int, tuple[str, str | None]] = {}
    linked_scope = scope.kind in {"linked sample group", "linked sessions"}
    for group_label, samples in scope.sample_groups:
        linked_label = scope.title if scope.kind == "linked sample group" else group_label if linked_scope else None
        for sample in samples:
            sample_label = _clean_sample_label(sample.name)
            for tilt in sample.tilt_series:
                contexts[id(tilt)] = (sample_label, linked_label)
    for tilt in scope.tilt_series:
        contexts.setdefault(id(tilt), (scope.title or tilt.name or tilt.id, None))
    return contexts


def _section_applied_defocus_um(metadata: dict[str, Any]) -> tuple[float | None, str]:
    value = _safe_number(metadata.get("Defocus"))
    if value is not None:
        return value, "Defocus"
    value = _safe_number(metadata.get("TargetDefocus"))
    if value is not None:
        return value, "TargetDefocus"
    return None, ""


def _frame_count_for_applied_defocus(tilt: TiltSeries) -> int:
    if tilt.number_of_frames and tilt.number_of_frames > 0:
        return int(tilt.number_of_frames)
    if tilt.tilt_count and tilt.tilt_count > 0:
        return int(tilt.tilt_count)
    if tilt.sections:
        return len(tilt.sections)
    if tilt.mrc_metadata is not None and tilt.mrc_metadata.nz:
        return int(tilt.mrc_metadata.nz)
    return 0


def _per_frame_metric_tooltip_lines(
    *,
    sample_name: str,
    tilt_series_name: str,
    frame_index: int,
    frame_count: int,
    tilt_angle: float | None,
    acquisition_time: datetime | None,
    frame_order: int,
) -> list[str]:
    """Build the 5 shared identification lines for a per-frame metric tooltip.

    Both the applied-defocus and the camera-dose tooltips lead with the
    same Sample / Tilt series / Frame / Tilt angle / Acquisition time
    block; only the metric line and the caveat differ. Centralising the
    shared portion keeps the two callers honest if e.g. the frame
    counter notation changes.
    """

    angle_text = f"{tilt_angle:g}\N{DEGREE SIGN}" if tilt_angle is not None else "n/a"
    if acquisition_time is not None:
        time_text = acquisition_time.isoformat(sep=" ", timespec="seconds")
    else:
        time_text = f"Frame order {frame_order}"
    return [
        f"Sample: {sample_name}",
        f"Tilt series: {tilt_series_name}",
        f"Frame: {frame_index} / {frame_count or '?'}",
        f"Tilt angle: {angle_text}",
        f"Acquisition time: {time_text}",
    ]


def _applied_defocus_tooltip(
    *,
    sample_name: str,
    tilt_series_name: str,
    frame_index: int,
    frame_count: int,
    tilt_angle: float | None,
    acquisition_time: datetime | None,
    frame_order: int,
    applied_defocus_um: float,
    source_field: str,
) -> str:
    lines = _per_frame_metric_tooltip_lines(
        sample_name=sample_name,
        tilt_series_name=tilt_series_name,
        frame_index=frame_index,
        frame_count=frame_count,
        tilt_angle=tilt_angle,
        acquisition_time=acquisition_time,
        frame_order=frame_order,
    )
    lines.extend(
        [
            f"Applied defocus: {applied_defocus_um:g} \N{MICRO SIGN}m",
            f"Source: MDOC {source_field}",
            "Values are microscope-applied defocus from MDOC metadata, not measured CTF defocus.",
        ]
    )
    return "\n".join(lines)


def applied_defocus_point_tooltip(point: AppliedDefocusPointModel) -> str:
    """Build the hover tooltip for one applied-defocus point on demand."""

    if point.tooltip:
        return point.tooltip
    source_field = point.metadata_source
    if source_field.startswith("MDOC "):
        source_field = source_field.removeprefix("MDOC ")
    return _applied_defocus_tooltip(
        sample_name=point.sample_name,
        tilt_series_name=point.tilt_series_name,
        frame_index=point.frame_index,
        frame_count=point.frame_count,
        tilt_angle=point.tilt_angle,
        acquisition_time=point.acquisition_time,
        frame_order=point.frame_order,
        applied_defocus_um=point.applied_defocus_um,
        source_field=source_field,
    )


def _camera_dose_from_frame_metadata(metadata: dict[str, Any]) -> tuple[float | None, str | None]:
    raw_fields = metadata.get("raw_fields")
    raw = raw_fields if isinstance(raw_fields, dict) else {}
    source = _mrc_metadata_source_label(metadata)

    for candidate in (
        raw.get("dose_e_per_angstrom2"),
        metadata.get("dose_e_per_angstrom2"),
    ):
        value = _safe_number(candidate)
        if _valid_camera_dose_e_per_angstrom2(value):
            return value, source

    # Thermo FEI/Tomo5 stores this dose field as electrons per square metre.
    # Convert only this explicit dose field; dose-fraction fields are not
    # camera dose per image and are intentionally ignored.
    for candidate in (metadata.get("dose"), raw.get("dose")):
        value = _safe_number(candidate)
        if value is None:
            continue
        converted = value * 1e-20
        if _valid_camera_dose_e_per_angstrom2(converted):
            return converted, source
    return None, None


def _mrc_metadata_source_label(metadata: dict[str, Any]) -> str:
    for key in ("metadata_source", "source"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "MRC frame metadata"


def _valid_camera_dose_e_per_angstrom2(value: float | None) -> bool:
    if value is None:
        return False
    return value > 0 and value < 1_000_000 and value == value and value not in (float("inf"), float("-inf"))


def _frame_metadata_datetime(metadata: dict[str, Any]) -> datetime | None:
    timestamp = metadata.get("timestamp")
    if isinstance(timestamp, datetime):
        return timestamp
    if isinstance(timestamp, str) and timestamp.strip():
        return _parse_loose_datetime(timestamp)
    return None


def _dose_source_label(source_labels: set[str]) -> str | None:
    labels = sorted(label for label in source_labels if label)
    if not labels:
        return None
    if len(labels) == 1:
        return labels[0]
    if all("mrc" in label.lower() for label in labels):
        return "MRC frame metadata"
    return "Multiple metadata sources"


def _dose_information_status(values: list[float], *, partial: bool) -> tuple[str, str | None]:
    if partial:
        return "warning", "partial data"
    if len(values) < 2:
        return "neutral", None
    med = median(values)
    spread = max(values) - min(values)
    if spread > max(0.5, abs(med) * 0.25):
        return "warning", "variable dose"
    return "neutral", None


def _dose_information_tooltip(
    *,
    sample_name: str,
    tilt_series_name: str,
    frame_index: int,
    frame_count: int,
    tilt_angle: float | None,
    acquisition_time: datetime | None,
    frame_order: int,
    dose_e_per_angstrom2: float,
    metadata_source: str,
) -> str:
    lines = _per_frame_metric_tooltip_lines(
        sample_name=sample_name,
        tilt_series_name=tilt_series_name,
        frame_index=frame_index,
        frame_count=frame_count,
        tilt_angle=tilt_angle,
        acquisition_time=acquisition_time,
        frame_order=frame_order,
    )
    lines.extend(
        [
            f"Camera dose per image: {dose_e_per_angstrom2:.3g} e⁻/Å²",
            f"Source: {metadata_source}",
            "This is camera dose per image, not cumulative tilt series dose.",
        ]
    )
    return "\n".join(lines)


def dose_information_point_tooltip(point: DoseInformationPointModel) -> str:
    """Build the hover/detail tooltip for one camera-dose point on demand."""

    if point.tooltip:
        return point.tooltip
    return _dose_information_tooltip(
        sample_name=point.sample_name,
        tilt_series_name=point.tilt_series_name,
        frame_index=point.frame_index,
        frame_count=point.frame_count,
        tilt_angle=point.tilt_angle,
        acquisition_time=point.acquisition_time,
        frame_order=point.frame_order,
        dose_e_per_angstrom2=point.dose_e_per_angstrom2,
        metadata_source=point.metadata_source,
    )


def _failed_tilt_series_per_search_map(
    search_maps: list[SearchMap],
    batch_positions: list[BatchPosition],
    tilt_series: list[TiltSeries],
    validation_by_id: dict[str, Any],
) -> dict[str, int]:
    """Tally failed validations per search map, including inferred failures."""

    from tomography_session_browser.services.tilt_series_validation import STATUS_FAILED as V_FAILED

    counts: dict[str, int] = {}
    for sm in search_maps:
        linked = set(
            search_map_tilt_series_ids(
                sm,
                batch_positions,
                tilt_series,
                validation_by_id,
            )
        )
        if not linked:
            continue
        counts[sm.id] = sum(
            1
            for ts_id in linked
            if (validation := validation_by_id.get(ts_id)) is not None
            and validation.status == V_FAILED
        )
    return counts


def _validation_tooltip(validation) -> str:
    """Multi-line tooltip used by the tilt-series tile grid and context panel."""

    lines = [
        validation.name,
        validation.status.upper(),
        f"{validation.actual_count} / {validation.expected_count or '?'} images",
    ]
    if validation.min_tilt is not None and validation.max_tilt is not None:
        if validation.tilt_increment:
            lines.append(
                f"Expected from tilt range {validation.min_tilt:g}° to {validation.max_tilt:g}° "
                f"at {validation.tilt_increment:g}°"
            )
        else:
            lines.append(f"Tilt range {validation.min_tilt:g}° to {validation.max_tilt:g}°")
    if validation.evidence_source and validation.evidence_source != "none":
        lines.append(f"Evidence: {validation.evidence_source.replace('_', ' ')}")
    if validation.reason:
        lines.append(f"Reason: {validation.reason}")
    return "\n".join(lines)


def _scope_microscope_name(scope: _DashboardScope) -> str | None:
    for tilt in scope.tilt_series:
        for section in tilt.sections:
            name = section.metadata.get("MicroscopeName") or section.metadata.get("InstrumentModel")
            if name:
                return str(name)
    return None


def _scope_acquisition_window(
    scope: _DashboardScope,
) -> tuple[str | None, str | None, str | None]:
    """Return ``(start_iso, end_iso, duration_human)`` for the acquisition window.

    ``duration_human`` is the elapsed wall-clock time between the earliest
    start and the latest end, formatted as e.g. ``"1 day, 14 hours,
    4 minutes"``. ``None`` when either end of the window is unavailable.
    """

    starts: list[datetime] = []
    ends: list[datetime] = []
    for tilt in scope.tilt_series:
        if tilt.acquisition_time_start:
            parsed = _parse_loose_datetime(tilt.acquisition_time_start)
            if parsed:
                starts.append(parsed)
        if tilt.acquisition_time_end:
            parsed = _parse_loose_datetime(tilt.acquisition_time_end)
            if parsed:
                ends.append(parsed)
    if not starts or not ends:
        return None, None, None
    start_dt = min(starts)
    end_dt = max(ends)
    return (
        start_dt.isoformat(sep=" ", timespec="minutes"),
        end_dt.isoformat(sep=" ", timespec="minutes"),
        format_duration_between(start_dt, end_dt),
    )


def format_duration_between(start: datetime, end: datetime) -> str | None:
    """Human-readable ``Δ days, hours, minutes`` between two timestamps.

    Negative or zero deltas return ``None`` so the caller can omit the
    suffix entirely. Days / hours / minutes that are zero are dropped from
    the output, except the smallest non-zero unit always renders. For very
    short windows we fall back to "less than a minute".
    """

    if end <= start:
        return None
    total_seconds = int((end - start).total_seconds())
    days, remainder = divmod(total_seconds, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes = remainder // 60
    parts: list[str] = []
    if days:
        parts.append(f"{days} day" + ("s" if days != 1 else ""))
    if hours:
        parts.append(f"{hours} hour" + ("s" if hours != 1 else ""))
    if minutes:
        parts.append(f"{minutes} minute" + ("s" if minutes != 1 else ""))
    if not parts:
        return "less than a minute"
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return f"{parts[0]}, {parts[1]} and {parts[2]}"


def _atlas_summary_from_scope(
    scope: _DashboardScope, atlas_counts: AtlasCountModel
) -> AtlasSummaryModel:
    """Atlas summary built from the already-resolved scope atlases.

    ``atlas_counts`` carries the deduplicated four-way counts derived
    from the original scope input (a Session, a list of Sessions, a
    Sample, or a LinkedSampleGroup). Those drive the headline numbers;
    the per-atlas detail rows continue to be sourced from
    ``scope.atlases`` so the atlas list stays comprehensive.
    """

    rows: list[AtlasRowModel] = []
    pixel_sizes: list[float] = []
    image_sizes: list[tuple[int, int]] = []
    seen_atlas_keys: set[str] = set()

    for sample_label, atlas in scope.atlases:
        key = _canonical_atlas_key(atlas)
        if key in seen_atlas_keys:
            continue
        seen_atlas_keys.add(key)
        size = _atlas_image_size(atlas)
        pixel_um = _atlas_pixel_size_um(atlas)
        if pixel_um is not None:
            pixel_sizes.append(pixel_um)
        if size is not None:
            image_sizes.append(size)
        rows.append(
            AtlasRowModel(
                sample_label=sample_label,
                image_path=str(atlas.image_path) if atlas.image_path else None,
                image_size=size,
                pixel_size_um=pixel_um,
                tile_count=len(atlas.tile_paths),
                acquisition_time=_atlas_acquisition_time(atlas),
            )
        )

    pixel_range: tuple[float, float] | None = (min(pixel_sizes), max(pixel_sizes)) if pixel_sizes else None
    image_range: tuple[tuple[int, int], tuple[int, int]] | None = None
    if image_sizes:
        smallest = min(image_sizes, key=lambda s: s[0] * s[1])
        largest = max(image_sizes, key=lambda s: s[0] * s[1])
        image_range = (smallest, largest)

    # Legacy fields are now sourced from the corrected counts so any
    # reader that looked at ``atlas_count`` / ``sample_count_with_atlas``
    # gets the same number the four-way breakdown shows.
    return AtlasSummaryModel(
        atlas_count=atlas_counts.sample_atlas_count,
        sample_count_with_atlas=atlas_counts.samples_with_atlas_count,
        rows=rows,
        pixel_size_range_um=pixel_range,
        image_size_range=image_range,
        counts=atlas_counts,
    )


def _count_session_attr(session: Session, label: str) -> int:
    mapping = {
        "Overviews": ("overviews", "overviews"),
        "Search maps": ("search_maps", "search_maps"),
        "Batch positions": ("batch_positions", "batch_positions"),
        "Tilt series": ("tilt_series", "tilt_series"),
    }
    top, sample_attr = mapping[label]
    total = len(getattr(session, top, []))
    for sample in session.samples:
        total += len(getattr(sample, sample_attr, []))
    return total


def _per_sample_counts(session: Session, label: str) -> list[int]:
    mapping = {
        "Overviews": "overviews",
        "Search maps": "search_maps",
        "Batch positions": "batch_positions",
        "Tilt series": "tilt_series",
    }
    attr = mapping[label]
    return [len(getattr(sample, attr, [])) for sample in session.samples]


def _tilt_series_outcomes(session: Session) -> TiltSeriesOutcomeModel:
    complete = partial = missing_mdoc = no_stack = 0
    for tilt in _all_tilt_series(session):
        if not tilt.mrc_path or not tilt.mrc_path.exists():
            no_stack += 1
            continue
        if tilt.mdoc_path is None or not tilt.sections:
            missing_mdoc += 1
            continue
        expected = _expected_section_count(tilt)
        actual = len(tilt.sections)
        if expected and actual + 1 < expected:
            partial += 1
        else:
            complete += 1
    return TiltSeriesOutcomeModel(
        complete=complete,
        partial=partial,
        missing_mdoc=missing_mdoc,
        no_stack=no_stack,
    )


def _expected_section_count(tilt: TiltSeries) -> int | None:
    """Approximate the expected number of mdoc sections.

    Uses the configured tilt range when available — most session schemas
    record the planned start/end angle and an angular step. We fall back to
    counting non-zero ``tilt_count`` from the parser. Returns ``None`` when
    we can't form a reliable estimate; the dashboard treats that as "not
    enough info to call this partial".
    """

    if tilt.tilt_count and tilt.tilt_count > 0:
        return tilt.tilt_count
    if tilt.tilt_range and len(tilt.tilt_range) == 2 and tilt.tilt_range[0] is not None and tilt.tilt_range[1] is not None:
        span = abs(tilt.tilt_range[1] - tilt.tilt_range[0])
        if span > 0:
            # Assume 2 degree step as a conservative upper bound estimate; we
            # only flag "partial" when the actual section count is well below
            # this floor, so the heuristic errs on the side of "complete".
            return max(int(span / 2), 1)
    return None


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _search_map_tile_counts(search_map: SearchMap) -> tuple[int, int]:
    """Return (planned, acquired) tile counts for a SearchMap.

    Tomography 5 records both numbers as nested ``<a:Key>/<a:Value>`` pairs
    inside ``SearchMap.xml`` and as a ``GridSize`` (width × height) in the
    ``TileSetAcquisitionOptions.xml`` companion. We try the explicit key-value
    block first, fall back to the grid product, and use the on-disk
    ``tile_paths`` count for "acquired" since it is what the user can actually
    open. Both numbers default to 0 when nothing is parseable.
    """

    metadata = search_map.metadata or {}
    planned = _walk_int(metadata, "NumberOfTilesPlanned")
    acquired = _walk_int(metadata, "NumberOfTilesAcquired")
    if planned == 0:
        grid = _walk_grid_size(metadata)
        if grid is not None:
            planned = grid
    if acquired == 0:
        # ``tile_paths`` typically contains a .jpg AND .mrc per tile — dedupe
        # by stem so the count reflects unique tiles.
        acquired = len({path.stem for path in search_map.tile_paths})
    return planned, acquired


def planned_exposures_for_batch(batch: BatchPosition) -> int:
    """Number of *planned* exposure areas configured on a batch position.

    A batch carries one main exposure
    (``ExposureTemplateAreaParameters``) and any number of additional
    ones (``AdditionalExposureTemplateAreas /
    ExposureTemplateAreaParameters`` — a single dict or a list). We
    only count entries that aren't tagged ``nil="true"``, so a slot
    that the microscope file marks as empty isn't double-counted.
    """

    metadata = batch.metadata or {}

    main = metadata.get("ExposureTemplateAreaParameters")
    has_main = isinstance(main, dict) and (
        not isinstance(main.get("_attributes"), dict)
        or main["_attributes"].get("nil") != "true"
    )

    raw_areas = find_first(metadata.get("AdditionalExposureTemplateAreas"), "ExposureTemplateAreaParameters")
    if isinstance(raw_areas, dict):
        additional_count = 1
    elif isinstance(raw_areas, list):
        additional_count = sum(
            1
            for entry in raw_areas
            if isinstance(entry, dict)
            and (
                not isinstance(entry.get("_attributes"), dict)
                or entry["_attributes"].get("nil") != "true"
            )
        )
    else:
        additional_count = 0

    return (1 if has_main else 0) + additional_count


def acquired_exposures_for_batch(
    batch: BatchPosition,
    *,
    successful_tilt_ids: Iterable[str] | None = None,
) -> int:
    """Successfully-acquired exposure count for a batch position.

    When ``successful_tilt_ids`` is provided (the dashboard /
    report path), we count only the linked tilt series whose
    validator status is ``COMPLETE`` — failed tilt series do *not*
    contribute. This is what the search-map progress bar should
    reflect: how many planned exposure areas resulted in a usable
    tilt series.

    When the caller omits the validator data we fall back to the
    raw count of linked tilt series (treating them all as
    successful), which matches the legacy behaviour.
    """

    if successful_tilt_ids is not None:
        successful = set(successful_tilt_ids)
        return sum(1 for tid in batch.linked_tilt_series_ids if tid in successful)
    return len(batch.linked_tilt_series_ids)


def search_map_batch_positions(
    search_map: SearchMap,
    batch_positions: Iterable[BatchPosition],
) -> list[BatchPosition]:
    """Batch positions explicitly associated with ``search_map``."""

    batches_by_id = {batch.id: batch for batch in batch_positions}
    linked: list[BatchPosition] = []
    seen: set[str] = set()
    for batch_id in search_map.linked_batch_position_ids or []:
        batch = batches_by_id.get(batch_id)
        if batch is None or batch.id in seen:
            continue
        linked.append(batch)
        seen.add(batch.id)
    for batch in batch_positions:
        if batch.id in seen:
            continue
        if batch.linked_search_map_id == search_map.id:
            linked.append(batch)
            seen.add(batch.id)
    return linked


def search_map_tilt_series_ids(
    search_map: SearchMap,
    batch_positions: Iterable[BatchPosition],
    tilt_series: Iterable[TiltSeries],
    validation_by_id: dict[str, Any] | None = None,
) -> list[str]:
    """Tilt-series IDs counted against a search map for display/reporting.

    The parsed links remain the primary source. Validator-failed orphan tilts
    can also be included when the existing conservative fallbacks identify a
    matching recorded batch or the overlay pipeline projects their MRC stage
    position inside this search map.
    """

    batches = list(batch_positions)
    tilts = list(tilt_series)
    linked_batches = search_map_batch_positions(search_map, batches)
    linked: list[str] = []
    seen: set[str] = set()

    def add(tilt_id: str | None) -> None:
        if not tilt_id or tilt_id in seen:
            return
        linked.append(tilt_id)
        seen.add(tilt_id)

    for tilt_id in search_map.linked_tilt_series_ids or []:
        add(tilt_id)
    for batch in linked_batches:
        for tilt_id in batch.linked_tilt_series_ids or []:
            add(tilt_id)

    if validation_by_id:
        inferred_groups, stage_ids = _inferred_failed_groups_for_search_map(
            search_map,
            [search_map],
            batches,
            tilts,
            validation_by_id,
        )
        for group in inferred_groups:
            for tilt in group.tilt_series:
                add(tilt.id)
        for tilt_id in stage_ids:
            add(tilt_id)

    return linked


def search_map_acquisition_rollup(
    search_map: SearchMap,
    search_maps: Iterable[SearchMap],
    batch_positions: Iterable[BatchPosition],
    tilt_series: Iterable[TiltSeries],
    validation_by_id: dict[str, Any],
    *,
    successful_tilt_ids: Iterable[str] | None = None,
) -> SearchMapAcquisitionRollup:
    batches = list(batch_positions)
    maps = list(search_maps)
    tilts = list(tilt_series)
    planned, acquired = search_map_exposure_progress(
        search_map,
        batches,
        successful_tilt_ids=successful_tilt_ids,
    )
    planned = max(planned, acquired)
    linked_ids: list[str] = []
    seen_ids: set[str] = set()

    def add_linked_id(tilt_id: str | None) -> None:
        if not tilt_id or tilt_id in seen_ids:
            return
        linked_ids.append(tilt_id)
        seen_ids.add(tilt_id)

    for tilt_id in search_map.linked_tilt_series_ids or []:
        add_linked_id(tilt_id)
    for batch in search_map_batch_positions(search_map, batches):
        for tilt_id in batch.linked_tilt_series_ids or []:
            add_linked_id(tilt_id)

    inferred_groups, stage_ids = _inferred_failed_groups_for_search_map(
        search_map,
        maps,
        batches,
        tilts,
        validation_by_id,
    )
    for group in inferred_groups:
        for tilt in group.tilt_series:
            add_linked_id(tilt.id)
    for tilt_id in stage_ids:
        add_linked_id(tilt_id)

    failed = incomplete = unknown = 0
    for tilt_id in linked_ids:
        validation = validation_by_id.get(tilt_id)
        if validation is None:
            continue
        if validation.status == TILE_STATUS_FAILED:
            failed += 1
        elif validation.status == "incomplete":
            incomplete += 1
        elif validation.status == "unknown":
            unknown += 1
    if failed or incomplete or unknown:
        planned = max(planned, acquired + failed + incomplete + unknown)

    return SearchMapAcquisitionRollup(
        planned=planned,
        acquired=acquired,
        tilt_series_ids=linked_ids,
        failed_exposure_areas=failed,
        incomplete_tilt_series=incomplete,
        unknown_tilt_series=unknown,
        failed_batch_groups=len({group.key for group in inferred_groups}),
        stage_inferred_failed_tilts=len(stage_ids),
    )


def _inferred_failed_groups_for_search_map(
    search_map: SearchMap,
    _search_maps: Iterable[SearchMap],
    batch_positions: list[BatchPosition],
    tilt_series: list[TiltSeries],
    validation_by_id: dict[str, Any],
) -> tuple[list[_InferredBatchGroup], frozenset[str]]:
    inferred_groups = _inferred_failed_batch_groups(batch_positions, tilt_series, validation_by_id)
    if not inferred_groups:
        return [], frozenset()

    assigned: dict[str, _InferredBatchGroup] = {}
    linked_batches = search_map_batch_positions(search_map, batch_positions)
    for batch in linked_batches:
        for group in inferred_groups:
            if group.key in _batch_inference_keys(batch):
                assigned[group.key] = group

    failed_tilt_ids = frozenset(
        tilt_id
        for tilt_id, validation in validation_by_id.items()
        if getattr(validation, "status", None) == TILE_STATUS_FAILED
    )
    stage_ids = inferred_failed_tilt_ids_for_search_map(
        search_map,
        tilt_series=tilt_series,
        failed_tilt_ids=failed_tilt_ids,
        batch_positions=batch_positions,
    )
    if stage_ids:
        for group in inferred_groups:
            if any(tilt.id in stage_ids for tilt in group.tilt_series):
                assigned[group.key] = group

    all_inferred_tilt_ids = {tilt.id for group in inferred_groups for tilt in group.tilt_series}
    assigned_tilt_ids = {tilt.id for group in assigned.values() for tilt in group.tilt_series}
    stage_ids = frozenset(
        tilt_id
        for tilt_id in stage_ids
        if tilt_id in assigned_tilt_ids or tilt_id not in all_inferred_tilt_ids
    )

    return list(assigned.values()), stage_ids


def _unresolved_inferred_failed_batch_groups(
    search_maps: Iterable[SearchMap],
    batch_positions: list[BatchPosition],
    tilt_series: list[TiltSeries],
    validation_by_id: dict[str, Any],
) -> list[_InferredBatchGroup]:
    inferred_groups = _inferred_failed_batch_groups(batch_positions, tilt_series, validation_by_id)
    unresolved = {group.key: group for group in inferred_groups}
    for search_map in search_maps:
        groups, _stage_ids = _inferred_failed_groups_for_search_map(
            search_map,
            search_maps,
            batch_positions,
            tilt_series,
            validation_by_id,
        )
        for group in groups:
            unresolved.pop(group.key, None)
    return list(unresolved.values())


def search_map_exposure_progress(
    search_map: SearchMap,
    batch_positions: Iterable[BatchPosition],
    *,
    successful_tilt_ids: Iterable[str] | None = None,
) -> tuple[int, int]:
    """Return ``(planned, acquired)`` exposure counts for ``search_map``.

    Aggregates planned + successfully-acquired exposures over every
    batch position that links to ``search_map.id``. Failed tilt
    series are excluded from the acquired total so the bar reflects
    *successful* acquisitions only — this matches what the user
    expects to see when, e.g., the Sang sample loses two tilt
    series and the bar should drop accordingly.

    ``successful_tilt_ids`` carries the validator's complete-status
    set; pass ``None`` for legacy behaviour (treat every linked
    tilt series as successful).
    """

    planned = 0
    acquired = 0
    for batch in search_map_batch_positions(search_map, batch_positions):
        planned += planned_exposures_for_batch(batch)
        acquired += acquired_exposures_for_batch(
            batch, successful_tilt_ids=successful_tilt_ids
        )
    return planned, acquired


def _walk_int(node: Any, key: str) -> int:
    """Search ``node`` (a possibly-nested dict) for ``key`` and parse to int."""

    if isinstance(node, dict):
        if key in node:
            return _safe_int(node[key])
        for value in node.values():
            found = _walk_int(value, key)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _walk_int(item, key)
            if found:
                return found
    return 0


def _walk_grid_size(node: Any) -> int | None:
    if isinstance(node, dict):
        grid = node.get("GridSize")
        if isinstance(grid, dict):
            width = _safe_int(grid.get("width"))
            height = _safe_int(grid.get("height"))
            product = width * height
            if product > 0:
                return product
        for value in node.values():
            found = _walk_grid_size(value)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _walk_grid_size(item)
            if found:
                return found
    return None


def _all_search_maps(session: Session) -> list[SearchMap]:
    items = list(session.search_maps)
    for sample in session.samples:
        items.extend(sample.search_maps)
    return dedupe_entities(items)


def _all_overviews(session: Session) -> list[Overview]:
    items = list(session.overviews)
    for sample in session.samples:
        items.extend(sample.overviews)
    return dedupe_entities(items)


def _all_search_tiles(session: Session) -> list[SearchTile]:
    items = list(session.search_tiles)
    for sample in session.samples:
        items.extend(sample.search_tiles)
    return dedupe_entities(items)


def _all_batch_positions(session: Session) -> list[BatchPosition]:
    items = list(session.batch_positions)
    for sample in session.samples:
        items.extend(sample.batch_positions)
    return dedupe_entities(items)


def _all_atlases(session: Session) -> list[tuple[str, Atlas]]:
    """Return displayable ``(sample_label, atlas)`` pairs for a session.

    Tomography atlas-screening folders often contain both a root Atlas/ and a
    ``Single Atlas`` sample that represent the session/global overview. Those
    are useful metadata, but they are not per-sample atlases and should not
    inflate the linked-session Atlas Summary or Atlas tab.
    """

    items: list[tuple[str, Atlas]] = []
    for sample in session.samples:
        if sample.atlas is not None and not _is_single_atlas_placeholder(sample):
            items.append((sample.name, sample.atlas))
    if not items and session.atlas is not None:
        items.append((session.name, session.atlas))
    return items


def _atlas_image_size(atlas: Atlas) -> tuple[int, int] | None:
    """Return ``(width, height)`` for the displayed atlas image in pixels.

    Prefers the MRC dimensions (the parser now selects ``Atlas_*.mrc`` as the
    primary image when available — that is the native-resolution mosaic).
    Falls back to the embedded XML ``ImageSize`` and, last, the cached JPG
    dimensions used as a fallback display image.
    """

    if atlas.mrc_metadata is not None:
        if atlas.mrc_metadata.nx and atlas.mrc_metadata.ny:
            return atlas.mrc_metadata.nx, atlas.mrc_metadata.ny
    embedded = find_first(atlas.metadata, "ImageSize") if isinstance(atlas.metadata, dict) else None
    if isinstance(embedded, dict):
        w = _safe_int(embedded.get("width"))
        h = _safe_int(embedded.get("height"))
        if w and h:
            return w, h
    cached = atlas.metadata.get("AtlasImageSize") if isinstance(atlas.metadata, dict) else None
    if isinstance(cached, dict):
        w = _safe_int(cached.get("width"))
        h = _safe_int(cached.get("height"))
        if w and h:
            return w, h
    return None


def _atlas_pixel_size_um(atlas: Atlas) -> float | None:
    """Return the atlas pixel size in micrometres, or ``None``.

    The XML stores pixel size in metres (``numericValue`` under
    ``pixelSize/x``); we centralise the unit conversion so callers always work
    in micrometres — the natural unit for users browsing electron microscopy
    sessions.
    """

    if not isinstance(atlas.metadata, dict):
        return None
    pixel_size = find_first(atlas.metadata, "pixelSize")
    if isinstance(pixel_size, dict):
        x_value = pixel_size.get("x")
        if isinstance(x_value, dict):
            value_m = _safe_number(x_value.get("numericValue"))
            if value_m is not None and value_m > 0:
                return value_m * 1e6  # metres → micrometres
    return None


def _atlas_acquisition_time(atlas: Atlas) -> str | None:
    if not isinstance(atlas.metadata, dict):
        return None
    when = find_first(atlas.metadata, "acquisitionDateTime")
    if isinstance(when, str) and when:
        return when
    return None


def _all_tilt_series(session: Session) -> list[TiltSeries]:
    items = list(session.tilt_series)
    for sample in session.samples:
        items.extend(sample.tilt_series)
    return dedupe_entities(items)


def _classify_warning(message: str) -> str:
    lower = message.lower()
    if any(word in lower for word in ("missing", "could not", "failed", "no mdoc", "no metadata", "corrupt")):
        return "error"
    if any(word in lower for word in ("partial", "approximate", "incomplete", "skipped", "unresolved")):
        return "warning"
    return "info"


def _session_microscope_name(session: Session) -> str | None:
    for tilt in _all_tilt_series(session):
        for section in tilt.sections:
            name = section.metadata.get("MicroscopeName") or section.metadata.get("InstrumentModel")
            if name:
                return str(name)
    return None


def _session_acquisition_window(session: Session) -> tuple[str | None, str | None]:
    starts: list[datetime] = []
    ends: list[datetime] = []
    for tilt in _all_tilt_series(session):
        if tilt.acquisition_time_start:
            parsed = _parse_loose_datetime(tilt.acquisition_time_start)
            if parsed:
                starts.append(parsed)
        if tilt.acquisition_time_end:
            parsed = _parse_loose_datetime(tilt.acquisition_time_end)
            if parsed:
                ends.append(parsed)
    start = min(starts).isoformat(sep=" ", timespec="minutes") if starts else None
    end = max(ends).isoformat(sep=" ", timespec="minutes") if ends else None
    return start, end


def _parse_loose_datetime(text: str) -> datetime | None:
    text = text.strip()
    for fmt in (
        "%d-%b-%Y  %H:%M:%S",
        "%d-%b-%Y %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
