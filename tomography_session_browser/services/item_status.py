from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from tomography_session_browser.domain.coerce import int_or_zero
from tomography_session_browser.domain.display_names import count_phrase as _count_phrase, format_search_tile_display_name
from tomography_session_browser.domain.identity import canonical_entity_key, dedupe_entities, entity_id_key
from tomography_session_browser.domain.models import Atlas, BatchPosition, Overview, Sample, SearchMap, SearchTile, TiltSeries
from tomography_session_browser.parsers.path_utils import natural_key
from tomography_session_browser.services.batch_inference import (
    batch_inference_key,
    entity_owner_keys,
    inferred_batch_label_for_tilt,
    inferred_group_token,
    inferred_label_matches_batch,
)
from tomography_session_browser.services.entity_links import (
    explicitly_linked_tilt_ids,
    batch_tilt_series,
    planned_exposures_for_batch_position,
    search_map_batches,
    tilt_on_batch,
)
from tomography_session_browser.services.timeline_service import datetime_sort_key
from tomography_session_browser.services.tilt_series_validation import (
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_INCOMPLETE,
    STATUS_UNKNOWN,
    TiltSeriesValidation,
    actual_tilt_count,
    validate_tilt_series_by_group,
)


STATUS_DONE = "done"
STATUS_PARTIAL = "partial"
STATUS_INCOMPLETE_LABEL = "incomplete"
STATUS_FAILED_LABEL = "failed"
STATUS_MISSING = "missing"
STATUS_UNKNOWN_LABEL = "unknown"
#: Planned but not acquired yet: queued exposures, or a recorded "Queued".
STATUS_PENDING = "pending"


#: Row tags a list filter can ask for exactly, as ``is:<tag>``. The
#: dashboard's drill-down buttons count by the same predicates, so a button
#: and the rows it opens cannot disagree (free text such as "mdoc" matched
#: the wrong rows and "unknown" matched none).
#: A word of a filter query that asks for a tag: ``is:failed``.
FILTER_TAG_PREFIX = "is:"
TAG_MISSING_MDOC = "missing-mdoc"
TAG_ORPHANED_FAILED = "orphaned-failed"
TAG_INFERRED_BATCH_PREFIX = "inferred-batch:"


@dataclass(frozen=True, slots=True)
class ItemListStatus:
    status: str
    summary: str
    tooltip: str = ""
    tags: frozenset[str] = frozenset()


def filter_tag_query(tag: str) -> str:
    """The list-filter text that shows exactly the rows carrying ``tag``."""

    return f"{FILTER_TAG_PREFIX}{tag}"


def validation_status_tag(validation_status: str) -> str:
    """Tag for a tilt series' validator verdict (``complete``, ``failed``...)."""

    return validation_status


def status_tag(status: str) -> str:
    """Tag for a row's status, in the reviewer's word (``complete``, ``incomplete``,
    ``failed``, ``missing``, ``pending``, ``unknown``; a partial status is Incomplete).

    Every row type derives its status tag from its own list status with this
    one function, so ``is:<tag>`` means the same on every tab and a status
    segment's count is the number of rows the tag shows. Phrases use hyphens
    so each tag remains one query word (``with-acquisitions``).
    """

    return display_status_label(status).lower().replace(" ", "-")


#: The statuses a list offers as segments, in the order they are drawn.
#: Each tab offers the subset its rows can carry.
SEGMENT_TAGS = ("failed", "incomplete", "complete", "missing", "with-acquisitions", "no-acquisitions")


@dataclass(frozen=True, slots=True)
class FilterQuery:
    """What a list's filter asks for: rows carrying every ``tags`` entry
    (``is:<tag>`` words) and whose texts contain ``text``."""

    tags: frozenset[str] = frozenset()
    text: str = ""

    def matches(self, row_tags: Iterable[str], *haystacks: str) -> bool:
        wanted = self.text.lower()
        return self.tags.issubset(row_tags) and (not wanted or any(wanted in text.lower() for text in haystacks))


def split_filter_query(query: str) -> FilterQuery:
    """The tags and the free text of a filter query (``is:failed alpha``)."""

    words = query.strip().lower().split()
    tags = frozenset(word[len(FILTER_TAG_PREFIX):] for word in words if word.startswith(FILTER_TAG_PREFIX))
    return FilterQuery(tags, " ".join(word for word in words if not word.startswith(FILTER_TAG_PREFIX)))


def split_owned_tag(query: str, owned: Iterable[str]) -> tuple[str | None, str]:
    """``(the status tag a segment owns, the rest of the query)``.

    The first ``is:<tag>`` word whose tag is one of ``owned`` belongs to the
    segment; every other word (another ``is:`` tag such as ``is:missing-mdoc``
    included) stays in the rest, in order, so nothing the query asks for is hidden.
    """

    offered = set(owned)
    status: str | None = None
    rest: list[str] = []
    for word in query.split():
        tag = word[len(FILTER_TAG_PREFIX):].lower() if word.lower().startswith(FILTER_TAG_PREFIX) else None
        if status is None and tag in offered:
            status = tag
        else:
            rest.append(word)
    return status, " ".join(rest)


def join_filter_query(status: str | None, text: str) -> str:
    """The query a segment's tag and the box's text make together."""

    return " ".join(part for part in (filter_tag_query(status) if status else "", text.strip()) if part)


def inferred_batch_tag(label: str | None, owner: str | None = None) -> str:
    """Tag for failed tilt series grouped under one inferred batch label.

    Carries the grid when known (``batch_inference.inferred_group_token``),
    matching the dashboard tile that opens these rows.
    """

    return f"{TAG_INFERRED_BATCH_PREFIX}{inferred_group_token(label, owner)}"


def tilt_series_lacks_mdoc(tilt: TiltSeries) -> bool:
    """No MDOC, or an MDOC without sections: the "Missing MDOC" predicate."""

    return tilt.mdoc_path is None or not tilt.sections


@dataclass(frozen=True, slots=True)
class ItemStatusContext:
    search_maps: tuple[SearchMap, ...]
    search_tiles: tuple[SearchTile, ...]
    batch_positions: tuple[BatchPosition, ...]
    tilt_series: tuple[TiltSeries, ...]
    validations: dict[str, TiltSeriesValidation]
    inferred_batch_labels: dict[str, str]
    #: ``entity.id`` → grid (sample folder), from ``batch_inference.entity_owner_keys``.
    #: Empty when the caller knows no samples: matching is then by name alone.
    owners: Mapping[str, str] = field(default_factory=dict)
    # Operation-local snapshots; rebuilding the context refreshes availability.
    tilt_statuses: Mapping[int, str] = field(default_factory=dict)
    map_tile_counts: Mapping[int, tuple[int, int]] = field(default_factory=dict)
    #: ``id(search tile)`` -> the exposure names of the other rows that show the same tile file.
    shared_tile_names: Mapping[int, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BatchPositionStatusCounts:
    """Validator-backed acquisition counts for one batch position.

    The Atlas LOD overlay consumes this public, read-only summary instead of
    duplicating the viewer-list classification rules. ``include_inferred`` is
    deliberately configurable because orphaned failed tilt series remain
    distinct markers on the Atlas rather than being promoted into a scientific
    batch relationship.
    """

    planned: int
    complete: int
    incomplete: int
    failed: int
    missing: int
    unknown: int

    @property
    def acquired(self) -> int:
        return self.complete + self.incomplete + self.failed + self.unknown


def build_item_status_context(
    *,
    search_maps: Iterable[SearchMap] = (),
    search_tiles: Iterable[SearchTile] = (),
    batch_positions: Iterable[BatchPosition] = (),
    tilt_series: Iterable[TiltSeries] = (),
    samples: Iterable[Sample] = (),
    validations: Mapping[str, TiltSeriesValidation] | None = None,
    exists: Callable[[Path], bool] | None = None,
) -> ItemStatusContext:
    """Build the shared status context for viewer-list rows.

    This mirrors the dashboard/report source of truth: tilt series are
    classified through ``validate_tilt_series_by_grid`` and search maps /
    batch positions derive their list badges from the linked tilt series and
    planned exposure counts.

    ``samples`` says which grid holds each entity (any superset will do). It
    keeps name-inferred batch matches on one grid and gives each grid its own
    expected tilt count.

    ``validations`` is that classification when the caller already holds it
    for exactly these ``tilt_series`` and ``samples`` (``MarkerContext``
    keeps one), so it is not worked out again.
    """

    search_maps = tuple(search_maps)
    search_tiles = tuple(search_tiles)
    batch_positions = tuple(batch_positions)
    tilt_series = tuple(tilt_series)
    owners = entity_owner_keys(samples)
    validations = (
        dict(validations)
        if validations is not None
        else {v.tilt_series_id: v for v in validate_tilt_series_by_grid(tilt_series, owners)}
    )
    available = {path: (path.exists() if exists is None else exists(path)) for path in {tilt.mrc_path for tilt in tilt_series} if path is not None}
    return ItemStatusContext(
        search_maps=search_maps,
        search_tiles=search_tiles,
        batch_positions=batch_positions,
        tilt_series=tilt_series,
        validations=validations,
        inferred_batch_labels=_inferred_failed_batch_labels(
            batch_positions,
            tilt_series,
            validations,
        ),
        owners=owners,
        tilt_statuses={id(tilt): _status_from_validation(tilt, validations.get(tilt.id), available=available) for tilt in tilt_series},
        map_tile_counts={id(value): search_map_tile_counts(value) for value in search_maps},
        shared_tile_names=_shared_tile_names(search_tiles),
    )


def _exposure_name(search_tile: SearchTile) -> str:
    """``"orali_1_5"`` for the row ``"orali_1_5 · Tile 17"``: the exposure, without its tile."""

    return format_search_tile_display_name(search_tile.name, search_tile.search_map_name).split(" · ")[0]


def _shared_tile_names(search_tiles: Iterable[SearchTile]) -> dict[int, tuple[str, ...]]:
    """For each row whose tile file another row also shows, the other rows' exposures.

    A search tile is listed once per exposure acquired on it, so two rows can be
    one picture. Rows are one tile when they name one file (the canonical key,
    which for a row with no file is its own ``id``, so it is never shared).
    """

    by_file: dict[str, list[SearchTile]] = {}
    for tile in search_tiles:
        by_file.setdefault(canonical_entity_key(tile)[1], []).append(tile)
    shared: dict[int, tuple[str, ...]] = {}
    for members in by_file.values():
        if len(members) > 1:
            for tile in members:
                shared[id(tile)] = tuple(_exposure_name(other) for other in members if other is not tile)
    return shared


def _same_tile_note(names: tuple[str, ...]) -> str:
    """"same tile as orali_1_5", naming at most three rows."""

    shown = ", ".join(names[:3])
    return f"same tile as {shown}" + (f" and {len(names) - 3} more" if len(names) > 3 else "")


def validate_tilt_series_by_grid(
    tilt_series: Iterable[TiltSeries],
    owners: Mapping[str, str] | None = None,
) -> list[TiltSeriesValidation]:
    """Validate each grid's tilt series against that grid's own expected count.

    The expected count falls back to the most common image count among a
    grid's series. Taken across a whole project it mixed grids with different
    tilt schemes, so a series could read Complete in its sample and Incomplete
    at project scope. Series no sample owns are validated together.
    """

    owners = owners or {}
    return validate_tilt_series_by_group(tilt_series, lambda tilt: owners.get(tilt.id))


def item_list_status(value: Any, context: ItemStatusContext | None, *, include_inferred: bool = True) -> ItemListStatus:
    if context is None:
        return ItemListStatus(status=STATUS_UNKNOWN_LABEL, summary="")
    if isinstance(value, Overview):
        return _overview_status(value, context)
    if isinstance(value, SearchMap):
        return _search_map_status(value, context)
    if isinstance(value, SearchTile):
        return _search_tile_status(value, context)
    if isinstance(value, BatchPosition):
        return _batch_position_status(value, context, include_inferred=include_inferred)
    if isinstance(value, TiltSeries):
        return _tilt_series_status(value, context)
    return ItemListStatus(status=STATUS_UNKNOWN_LABEL, summary="")


def batch_position_status_counts(
    batch: BatchPosition,
    context: ItemStatusContext,
    *,
    include_inferred: bool = True,
) -> BatchPositionStatusCounts:
    """Return the shared planned/acquired outcome counts for ``batch``."""

    linked_tilts = _tilt_series_for_batch(
        batch,
        context,
        include_inferred=include_inferred,
    )
    planned = planned_exposures_for_batch_position(batch, linked_tilts)
    counts = _status_counts(linked_tilts, context)
    counts.missing += max(planned - len(linked_tilts), 0)
    return BatchPositionStatusCounts(
        planned=planned,
        complete=counts.complete,
        incomplete=counts.incomplete,
        failed=counts.failed,
        missing=counts.missing,
        unknown=counts.unknown,
    )


def _overview_status(overview: Overview, context: ItemStatusContext) -> ItemListStatus:
    """Recorded downstream acquisitions, never approximate failed-tilt markers."""

    maps = [value for value in context.search_maps
            if value.overview is overview or value.id in overview.linked_search_map_ids]
    batches = dedupe_entities(
        [batch for value in maps for batch in search_map_batches(value, context.batch_positions)],
        key=entity_id_key,
    )
    tilts = dedupe_entities(
        [tilt for batch in batches for tilt in batch_tilt_series(batch, context.tilt_series)],
        key=entity_id_key,
    )
    status = "with acquisitions" if batches else "no acquisitions"
    summary = (f"{_count_phrase(len(batches), 'batch position')} · "
               f"{_count_phrase(len(tilts), 'tilt series', 'tilt series')}" if batches else "no acquisitions")
    return ItemListStatus(
        status=status, summary=summary,
        tooltip=_join_tooltip(overview.name, [summary, _count_phrase(len(maps), "linked search map")], status=status),
        tags=frozenset({status_tag(status)}),
    )


def _search_map_status(search_map: SearchMap, context: ItemStatusContext) -> ItemListStatus:
    linked_batches = search_map_batches(search_map, context.batch_positions)
    counts = _StatusCounts()
    planned = 0
    for batch in linked_batches:
        batch_tilts = _tilt_series_for_batch(batch, context)
        batch_planned = planned_exposures_for_batch_position(batch, batch_tilts)
        planned += batch_planned
        counts += _status_counts(batch_tilts, context)
        counts.missing += max(batch_planned - len(batch_tilts), 0)

    tile_counts = context.map_tile_counts.get(id(search_map))
    tile_planned, tile_acquired = tile_counts if tile_counts is not None else search_map_tile_counts(search_map)
    tile_count = tile_acquired or len({path.stem for path in search_map.tile_paths})
    if planned:
        status = _status_from_counts(counts, planned=planned)
        progress = f"{counts.complete}/{planned} tilt series"
    else:
        status = STATUS_DONE if tile_count else STATUS_MISSING
        progress = "0 planned tilt series"

    if tile_planned > 0 and tile_acquired < tile_planned:
        tile_status = STATUS_MISSING if tile_acquired <= 0 else STATUS_PARTIAL
        status = _combine_status(status, tile_status)

    # Coverage is stated only when the mosaic is partial.
    tile_text = (
        f"{tile_acquired} of {tile_planned} tiles"
        if tile_planned > 0 and tile_acquired < tile_planned
        else _count_phrase(tile_count, "tile")
    )
    parts = [tile_text] if tile_planned > 0 and tile_acquired < tile_planned else []
    if linked_batches:
        parts += [_count_phrase(len(linked_batches), "batch position"), progress]
    else:
        parts.append("no acquisitions")
    if counts.failed:
        parts.append(f"{counts.failed} failed")
    if counts.incomplete:
        parts.append(f"{counts.incomplete} incomplete")
    tooltip = _join_tooltip(
        search_map.name,
        parts if tile_text in parts else [tile_text, *parts],
        status=status,
    )
    return ItemListStatus(
        status=status,
        summary=" · ".join(parts),
        tooltip=tooltip,
        # ``is:incomplete`` shows exactly the rows the dashboard counts as such.
        tags=frozenset({status_tag(status)}),
    )


def _search_tile_status(search_tile: SearchTile, context: ItemStatusContext) -> ItemListStatus:
    batch = next(
        (item for item in context.batch_positions if item.id == search_tile.batch_position_id),
        None,
    )
    # The row's name already says "<exposure> · Tile N" (display_names.
    # format_search_tile_display_name), and the group header above it names the
    # batch and the map, so the second line keeps only what is about this row:
    # a tile another row shows too, a second tilt series, a missing image.
    shared = context.shared_tile_names.get(id(search_tile), ())
    if batch is None:
        status = STATUS_DONE if search_tile.image_path is not None else STATUS_MISSING
        parts = [_same_tile_note(shared)] if shared else []
        if search_tile.image_path is None:
            parts.append("no image")
        tooltip_parts = [*([search_tile.search_map_name] if search_tile.search_map_name else []), *parts]
        tooltip = _join_tooltip(search_tile.name, tooltip_parts or ["No linked batch position"], status=status)
        return ItemListStatus(status=status, summary=" · ".join(parts), tooltip=tooltip, tags=frozenset({status_tag(status)}))

    batch_status = _batch_position_status(batch, context)
    linked_tilts = [
        tilt for tilt in context.tilt_series if tilt.id in set(search_tile.linked_tilt_series_ids or [])
    ]
    if linked_tilts:
        counts = _status_counts(linked_tilts, context)
        status = _status_from_counts(counts, planned=len(linked_tilts))
    else:
        status = batch_status.status
    if search_tile.image_path is None:
        status = _combine_status(status, STATUS_MISSING)
    parts: list[str] = [_same_tile_note(shared)] if shared else []
    if len(search_tile.linked_tilt_series_ids) > 1:
        parts.append(_count_phrase(len(search_tile.linked_tilt_series_ids), "tilt series", "tilt series"))
    if search_tile.image_path is None:
        parts.append("no image")
    tooltip_lines = [
        search_tile.name,
        f"Status: {display_status_label(status)}",
        f"Search map: {search_tile.search_map_name or 'unknown'}",
        f"Batch position: {search_tile.batch_position_name or 'unknown'}",
        f"Linked tilt series: {len(search_tile.linked_tilt_series_ids)}",
    ]
    if shared:
        tooltip_lines.append(f"Same tile file as: {', '.join(shared)}")
    if batch_status.summary:
        tooltip_lines.append(batch_status.summary)
    if search_tile.link_method:
        tooltip_lines.append(f"Linked by: {search_tile.link_method}")
    if search_tile.warnings:
        tooltip_lines.extend(search_tile.warnings)
    return ItemListStatus(
        status=status,
        summary=" · ".join(parts),
        tooltip="\n".join(tooltip_lines),
        tags=frozenset({status_tag(status)}),
    )


def _batch_position_status(batch: BatchPosition, context: ItemStatusContext, *, include_inferred: bool = True) -> ItemListStatus:
    details = batch_position_status_counts(batch, context, include_inferred=include_inferred)
    return batch_position_status_from_counts(batch, details)


def batch_position_status_from_counts(batch: BatchPosition, details: BatchPositionStatusCounts) -> ItemListStatus:
    """Classify already-derived counts for GUI, Atlas and presenter consumers."""
    planned = details.planned
    counts = _StatusCounts(
        complete=details.complete,
        incomplete=details.incomplete,
        failed=details.failed,
        missing=details.missing,
        unknown=details.unknown,
    )

    explicit_status = normalise_recorded_batch_status(batch.status)
    if planned == 0:
        status = explicit_status or STATUS_UNKNOWN_LABEL
    elif explicit_status == STATUS_FAILED_LABEL and not counts.failed:
        # The microscope's "Failed" is one failed exposure, then the batch is
        # classified like any other row: a batch that also completed a tilt
        # series is partial, not failed. Failed only when nothing succeeded.
        counts.failed += 1
        counts.missing = max(counts.missing - 1, 0)
        status = _status_from_counts(counts, planned=planned)
    else:
        status = _status_from_counts(counts, planned=planned)

    parts = [
        _count_phrase(planned, "exposure area") if planned else "no planned exposure areas"
    ]
    if counts.complete and counts.complete != planned:
        parts.append(f"{counts.complete} complete")
    if counts.failed:
        parts.append(f"{counts.failed} failed")
    if counts.incomplete:
        parts.append(f"{counts.incomplete} incomplete")
    if counts.missing:
        parts.append(f"{counts.missing} missing")
    tooltip = _join_tooltip(batch.name or batch.id, parts, status=status)
    return ItemListStatus(status=status, summary=" · ".join(parts), tooltip=tooltip, tags=frozenset({status_tag(status)}))


def _tilt_series_status(tilt: TiltSeries, context: ItemStatusContext) -> ItemListStatus:
    validation = context.validations.get(tilt.id)
    status = _tilt_status(tilt, context)
    # Only an exception has a fact to state: how many frames of how many. A
    # complete series says it with its glyph. The pixel size is the image's
    # (its badge, and Context), not something every row repeats.
    parts: list[str] = []
    if status != STATUS_DONE:
        if validation is not None:
            if validation.expected_count:
                parts.append(f"{validation.actual_count} of {validation.expected_count} frames")
            else:
                parts.append(_count_phrase(validation.actual_count, "frame"))
        else:
            actual = actual_tilt_count(tilt)
            parts.append(_count_phrase(actual, "frame") if actual else "unknown frames")
    # An inferred group is named by the divider above its rows (B4.3), so the
    # row does not repeat it; the tooltip and the filter tag still carry it.
    inferred = context.inferred_batch_labels.get(tilt.id)
    tooltip_lines = [tilt.name]
    if validation is not None:
        tooltip_lines.append(f"Status: {display_status_label(status)}")
        tooltip_lines.append(f"Reason: {validation.reason}")
    if inferred:
        tooltip_lines.append(f"Inferred batch position: {inferred}")
    tags: set[str] = set()
    if validation is not None:
        tags.add(validation_status_tag(validation.status))
    if tilt_series_lacks_mdoc(tilt):
        tags.add(TAG_MISSING_MDOC)
    if inferred:
        tags.add(inferred_batch_tag(inferred, context.owners.get(tilt.id)))
        inferred_key = batch_inference_key(inferred)
        if not any(
            inferred_label_matches_batch(inferred, batch, tilt_id=tilt.id, owners=context.owners)
            for batch in context.batch_positions
        ):
            tags.add(TAG_ORPHANED_FAILED)
    return ItemListStatus(
        status=status,
        summary=" · ".join(parts),
        tooltip="\n".join(tooltip_lines),
        tags=frozenset(tags),
    )


@dataclass(slots=True)
class _StatusCounts:
    complete: int = 0
    incomplete: int = 0
    failed: int = 0
    missing: int = 0
    unknown: int = 0

    def __iadd__(self, other: "_StatusCounts") -> "_StatusCounts":
        self.complete += other.complete
        self.incomplete += other.incomplete
        self.failed += other.failed
        self.missing += other.missing
        self.unknown += other.unknown
        return self


def _status_counts(
    tilt_series: Iterable[TiltSeries],
    context: ItemStatusContext,
) -> _StatusCounts:
    counts = _StatusCounts()
    for tilt in tilt_series:
        status = _tilt_status(tilt, context)
        if status == STATUS_DONE:
            counts.complete += 1
        elif status == STATUS_FAILED_LABEL:
            counts.failed += 1
        elif status == STATUS_INCOMPLETE_LABEL:
            counts.incomplete += 1
        elif status == STATUS_MISSING:
            counts.missing += 1
        else:
            counts.unknown += 1
    return counts


def _status_from_counts(counts: _StatusCounts, *, planned: int) -> str:
    if planned <= 0:
        return STATUS_UNKNOWN_LABEL
    if counts.complete >= planned and not (counts.failed or counts.incomplete or counts.missing):
        return STATUS_DONE
    if counts.failed and not counts.complete and not counts.incomplete:
        return STATUS_FAILED_LABEL
    if counts.complete or counts.failed or counts.incomplete:
        return STATUS_PARTIAL
    if counts.missing >= planned:
        # Nothing acquired yet of what was planned: pending, not "missing"
        # (that word is for a file that should be on disk and is not).
        return STATUS_PENDING
    return STATUS_INCOMPLETE_LABEL


def _combine_status(left: str, right: str) -> str:
    order = {
        STATUS_DONE: 0,
        STATUS_UNKNOWN_LABEL: 1,
        STATUS_PENDING: 2,
        STATUS_MISSING: 2,
        STATUS_INCOMPLETE_LABEL: 3,
        STATUS_PARTIAL: 4,
        STATUS_FAILED_LABEL: 5,
    }
    return left if order.get(left, 1) >= order.get(right, 1) else right


def _tilt_status(tilt: TiltSeries, context: ItemStatusContext) -> str:
    cached = context.tilt_statuses.get(id(tilt))
    return cached if cached is not None else _status_from_validation(tilt, context.validations.get(tilt.id))


def _status_from_validation(
    tilt: TiltSeries,
    validation: TiltSeriesValidation | None,
    *,
    available: Mapping[Path, bool] | None = None,
) -> str:
    if tilt.mrc_path is None or not (available[tilt.mrc_path] if available is not None else tilt.mrc_path.exists()):
        return STATUS_MISSING
    if validation is None:
        return STATUS_UNKNOWN_LABEL
    if validation.status == STATUS_COMPLETE:
        return STATUS_DONE
    if validation.status == STATUS_FAILED:
        return STATUS_FAILED_LABEL
    if validation.status == STATUS_INCOMPLETE:
        return STATUS_INCOMPLETE_LABEL
    if validation.status == STATUS_UNKNOWN:
        return STATUS_MISSING if validation.actual_count == 0 else STATUS_UNKNOWN_LABEL
    return STATUS_UNKNOWN_LABEL


def normalise_recorded_batch_status(status: str | None) -> str | None:
    """Read the status the microscope recorded for a batch position.

    The one reading of the raw word; the dashboard tiles use it too, so a
    word cannot mean "pending" in one place and "warning" in another.
    ``None`` for an empty or unrecognised word.
    """

    raw = (status or "").strip().lower()
    if raw in {"acquired", "done", "complete", "completed"}:
        return STATUS_DONE
    if raw in {"failed", "error"}:
        return STATUS_FAILED_LABEL
    if raw in {"pending", "queued", "scheduled"}:
        return STATUS_PENDING
    if raw == "missing":
        return STATUS_MISSING
    if raw in {"partial", "incomplete", "warning"}:
        return STATUS_PARTIAL
    return None


def _tilt_series_for_batch(
    batch: BatchPosition,
    context: ItemStatusContext,
    *,
    include_inferred: bool = True,
) -> list[TiltSeries]:
    linked: list[TiltSeries] = []
    seen: set[str] = set()
    for tilt in context.tilt_series:
        if tilt.id in seen:
            continue
        inferred_label = context.inferred_batch_labels.get(tilt.id) if include_inferred else None
        if (
            tilt_on_batch(tilt, batch)
            # Same name *and* same grid: "Position_1" repeats on every grid.
            or (
                inferred_label is not None
                and inferred_label_matches_batch(inferred_label, batch, tilt_id=tilt.id, owners=context.owners)
            )
        ):
            linked.append(tilt)
            seen.add(tilt.id)
    linked.sort(
        key=lambda tilt: (
            *datetime_sort_key(tilt.acquisition_time_start),
            natural_key(tilt.name or tilt.id),
        )
    )
    return linked


def search_map_tile_counts(search_map: SearchMap) -> tuple[int, int]:
    """``(planned, acquired)`` tiles of a search map's own mosaic."""

    metadata = search_map.metadata or {}
    planned = _walk_int(metadata, "NumberOfTilesPlanned")
    acquired = _walk_int(metadata, "NumberOfTilesAcquired")
    if planned == 0:
        grid = _walk_grid_size(metadata)
        if grid is not None:
            planned = grid
    if acquired == 0:
        acquired = len({path.stem for path in search_map.tile_paths})
    return planned, acquired


def atlas_tile_count(atlas: Atlas) -> int:
    """Number of acquired tiles in ``atlas``.

    ``Atlas.tile_paths`` holds both the ``.mrc`` and the ``.jpg`` of each tile
    so a viewer can choose a display source; counting it directly doubles the
    tiles. Tomography 5 writes one ``Tile_*.xml`` per acquired tile, so its
    unique stems are the count, with unique image stems as the fallback when
    no tile XML exists.
    """

    metadata_count = len({path.stem for path in atlas.tile_metadata_paths})
    if metadata_count:
        return metadata_count
    return len({path.stem for path in atlas.tile_paths})


def _walk_int(node: Any, key: str) -> int:
    if isinstance(node, dict):
        if key in node:
            return int_or_zero(node[key])
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
            width = int_or_zero(grid.get("width"))
            height = int_or_zero(grid.get("height"))
            product = width * height
            if product > 0:
                return product
        if "GridSizeX" in node and "GridSizeY" in node:
            x = int_or_zero(node.get("GridSizeX"))
            y = int_or_zero(node.get("GridSizeY"))
            return x * y if x and y else None
        for value in node.values():
            found = _walk_grid_size(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _walk_grid_size(item)
            if found is not None:
                return found
    return None


def _inferred_failed_batch_labels(
    batch_positions: tuple[BatchPosition, ...],
    tilt_series: tuple[TiltSeries, ...],
    validations: dict[str, TiltSeriesValidation],
) -> dict[str, str]:
    explicitly_linked = explicitly_linked_tilt_ids(batch_positions, tilt_series)
    labels: dict[str, str] = {}
    for tilt in tilt_series:
        if tilt.id in explicitly_linked or tilt.linked_batch_position_id:
            continue
        validation = validations.get(tilt.id)
        if validation is None or validation.status != STATUS_FAILED:
            continue
        label = inferred_batch_label_for_tilt(tilt)
        if label is not None:
            labels[tilt.id] = label
    return labels


def _join_tooltip(title: str, parts: list[str], *, status: str) -> str:
    lines = [title, f"Status: {display_status_label(status)}"]
    lines.extend(parts)
    return "\n".join(lines)


def display_status_label(status: str) -> str:
    """Return the consistent user-facing label for an internal item status.

    The validator's words throughout (Complete, Incomplete, Failed, Unknown),
    "Missing" for a file that is not on disk and "Pending" for planned work
    not acquired yet. "Unavailable" used to cover both Missing and Unknown,
    while the dashboard said "Unknown" for the same tilt series.
    """

    normalized = (status or "").strip().lower()
    labels = {
        STATUS_DONE: "Complete",
        "complete": "Complete",
        "completed": "Complete",
        "acquired": "Complete",
        STATUS_PARTIAL: "Incomplete",
        STATUS_INCOMPLETE_LABEL: "Incomplete",
        STATUS_FAILED_LABEL: "Failed",
        "error": "Failed",
        STATUS_MISSING: "Missing",
        STATUS_UNKNOWN_LABEL: "Unknown",
        "n/a": "Unknown",
        "queued": "Pending",
        STATUS_PENDING: "Pending",
        "warning": "Warning",
        "with acquisitions": "With acquisitions",
        "no acquisitions": "No acquisitions",
    }
    return labels.get(normalized, normalized.replace("_", " ").title())
