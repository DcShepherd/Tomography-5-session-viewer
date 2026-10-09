"""Which batches, search maps and tilt series the metadata links together.

Tomography 5 records a relationship on whichever side happened to be written:
``BatchPosition.linked_search_map_id`` or ``SearchMap.linked_batch_position_ids``,
``TiltSeries.linked_batch_position_id`` or ``BatchPosition.linked_tilt_series_ids``.
The predicates here accept either side, and only explicit metadata takes part:
inferred failed-batch labels (``batch_inference``) stay out.

The batches of a search map follow one rule, ``search_map_batches``: the
viewer's list rows, the dashboard and the PDF all use it (divergence-register
rule 1). The window's focused dashboard scope adds a named fallback,
``batches_sharing_tilt_series``: a batch that records no link to the map but
shares a linked tilt series with it. That is chained evidence, weaker than a
recorded link, so it only ever extends the rule
(``search_map_batches_with_shared_tilts``) and never stands in for it.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from tomography_session_browser.domain.identity import dedupe_entities, entity_id_key
from tomography_session_browser.domain.models import BatchPosition, SearchMap, TiltSeries
from tomography_session_browser.parsers.xml_parser import find_first


def batch_on_search_map(batch: BatchPosition, search_map: SearchMap) -> bool:
    """Either side records that ``batch`` was planned on ``search_map``."""

    return batch.linked_search_map_id == search_map.id or batch.id in (search_map.linked_batch_position_ids or [])


def shares_linked_tilt_series(batch: BatchPosition, search_map: SearchMap) -> bool:
    """``batch`` and ``search_map`` both list at least one tilt series ID."""

    return bool(set(batch.linked_tilt_series_ids or []).intersection(search_map.linked_tilt_series_ids or []))


def tilt_on_batch(tilt: TiltSeries, batch: BatchPosition) -> bool:
    """Either side records that ``tilt`` was acquired at ``batch``."""

    return tilt.id in (batch.linked_tilt_series_ids or []) or tilt.linked_batch_position_id == batch.id


# ---------------------------------------------------------------------------
# Batches of a search map: one rule, and the shared-tilt fallback
# ---------------------------------------------------------------------------


def search_map_batches(search_map: SearchMap, batch_positions: Iterable[BatchPosition]) -> list[BatchPosition]:
    """Batches linked to ``search_map`` from either side: the map's own ID list
    first, in its order, then batches naming the map in batch-list order. Once
    per batch ID, the first in ``batch_positions`` when an ID repeats."""

    batch_positions = list(batch_positions)
    batches_by_id: dict[str, BatchPosition] = {}
    for batch in batch_positions:
        batches_by_id.setdefault(batch.id, batch)
    linked: list[BatchPosition] = []
    seen: set[str] = set()
    for batch_id in search_map.linked_batch_position_ids or []:
        batch = batches_by_id.get(batch_id)
        if batch is None or batch.id in seen:
            continue
        linked.append(batch)
        seen.add(batch.id)
    for batch in batch_positions:
        if batch.id not in seen and batch.linked_search_map_id == search_map.id:
            linked.append(batch)
            seen.add(batch.id)
    return linked


def batches_sharing_tilt_series(
    search_map: SearchMap,
    batch_positions: Iterable[BatchPosition],
) -> list[BatchPosition]:
    """The fallback: batches that record no link to ``search_map`` but share a
    linked tilt series with it, in batch-list order, once per batch ID."""

    return dedupe_entities(
        [
            batch
            for batch in batch_positions
            if not batch_on_search_map(batch, search_map) and shares_linked_tilt_series(batch, search_map)
        ],
        key=entity_id_key,
    )


def search_map_batches_with_shared_tilts(
    search_map: SearchMap,
    batch_positions: Iterable[BatchPosition],
) -> list[BatchPosition]:
    """``search_map_batches``, then the ``batches_sharing_tilt_series``
    fallback (the window's focused dashboard scope)."""

    batch_positions = list(batch_positions)
    linked = search_map_batches(search_map, batch_positions)
    recorded = {batch.id for batch in linked}
    return linked + [
        batch for batch in batches_sharing_tilt_series(search_map, batch_positions) if batch.id not in recorded
    ]


def batch_search_maps_with_shared_tilts(
    batch: BatchPosition,
    search_maps: Iterable[SearchMap],
) -> list[SearchMap]:
    """The reverse of ``search_map_batches_with_shared_tilts``: search maps
    linked to ``batch`` from either side or sharing a linked tilt series."""

    return dedupe_entities(
        [
            search_map
            for search_map in search_maps
            if batch_on_search_map(batch, search_map) or shares_linked_tilt_series(batch, search_map)
        ],
        key=entity_id_key,
    )


# ---------------------------------------------------------------------------
# Tilt series of a batch or a search map
# ---------------------------------------------------------------------------


def batch_tilt_series(batch: BatchPosition, tilt_series: Iterable[TiltSeries]) -> list[TiltSeries]:
    """Tilt series linked to ``batch`` from either side, in input order, once
    per tilt ID."""

    return dedupe_entities([tilt for tilt in tilt_series if tilt_on_batch(tilt, batch)], key=entity_id_key)


def search_map_tilt_series(
    search_map: SearchMap,
    tilt_series: Iterable[TiltSeries],
    batch_positions: Iterable[BatchPosition],
) -> list[TiltSeries]:
    """Tilt series the map lists, or acquired at one of ``batch_positions``
    (the map's batches, from whichever policy the caller uses)."""

    linked_tilt_ids = set(search_map.linked_tilt_series_ids or [])
    linked_batch_ids = {batch.id for batch in batch_positions}
    return dedupe_entities(
        [
            tilt
            for tilt in tilt_series
            if tilt.id in linked_tilt_ids or tilt.linked_batch_position_id in linked_batch_ids
        ],
        key=entity_id_key,
    )


def explicitly_linked_tilt_ids(
    batch_positions: Iterable[BatchPosition],
    tilt_series: Iterable[TiltSeries],
) -> set[str]:
    """IDs of tilt series that metadata ties to one of ``batch_positions``.

    Everything else is a candidate for failed-batch inference by name.
    """

    batch_positions = list(batch_positions)
    known_batch_ids = {batch.id for batch in batch_positions}
    linked = {tilt.id for tilt in tilt_series if tilt.linked_batch_position_id in known_batch_ids}
    for batch in batch_positions:
        linked.update(batch.linked_tilt_series_ids or [])
    return linked


# ---------------------------------------------------------------------------
# Planned exposures
# ---------------------------------------------------------------------------


def planned_exposures_for_batch_position(
    batch: BatchPosition,
    linked_tilts: Iterable[TiltSeries] = (),
) -> int:
    """Planned exposure areas: the recorded templates, else the exposure
    images, else the linked tilt series, and never fewer than ``linked_tilts``.

    The one rule for list rows, dashboard tiles and search-map progress.
    """

    linked = len(list(linked_tilts))
    planned = planned_exposures_from_metadata(batch.metadata or {})
    if planned:
        return max(planned, linked)
    if batch.exposure_image_paths:
        return max(len(batch.exposure_image_paths), linked)
    if batch.linked_tilt_series_ids:
        return max(len(batch.linked_tilt_series_ids), linked)
    return linked


def planned_exposures_from_metadata(metadata: dict[str, Any]) -> int:
    """Count recorded exposure slots, excluding XML nil placeholders."""
    main = metadata.get("ExposureTemplateAreaParameters")
    has_main = isinstance(main, dict) and (
        not isinstance(main.get("_attributes"), dict)
        or main["_attributes"].get("nil") != "true"
    )

    raw_areas = find_first(metadata.get("AdditionalExposureTemplateAreas"), "ExposureTemplateAreaParameters")
    if isinstance(raw_areas, dict):
        raw_areas = [raw_areas]
    if isinstance(raw_areas, list):
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
