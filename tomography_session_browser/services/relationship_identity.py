"""Distinguish repeated relationship records from physical ID collisions."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from tomography_session_browser.domain.models import (
    Atlas,
    BatchPosition,
    Overview,
    SearchMap,
    SearchTile,
    TiltSeries,
    Sample,
    Session,
)

def _identity(value: Any) -> str:
    identifier = getattr(value, "id", None)
    return str(identifier) if identifier is not None else ""


def _signature(value: Any) -> tuple[Any, ...]:
    """Content fingerprint used to tell a duplicate from an ID collision.

    Display fields alone are not sufficient here. Two records can share an ID,
    name and status while pointing at different source files or relationships;
    collapsing those records would silently restore the first-match behaviour
    this resolver exists to remove. The fingerprint therefore includes the
    physical-source and relationship fields that define each navigable entity.
    """

    common = (
        type(value).__name__,
        _identity(value),
        getattr(value, "name", None),
        getattr(value, "status", None),
    )
    if isinstance(value, Overview):
        return common + (
            _path_signature(value.image_path),
            tuple(sorted(value.linked_search_map_ids or [])),
            tuple(sorted(value.linked_batch_position_ids or [])),
        )
    if isinstance(value, SearchMap):
        return common + (
            _path_signature(value.image_path),
            _path_signature(value.mrc_path),
            _path_signature(value.xml_path),
            _path_signature(value.dm_path),
            _referenced_signature(value.overview),
            tuple(sorted(value.linked_batch_position_ids or [])),
            tuple(sorted(value.linked_tilt_series_ids or [])),
        )
    if isinstance(value, SearchTile):
        return common + (
            _path_signature(value.image_path),
            _path_signature(value.xml_path),
            value.search_map_id,
            value.batch_position_id,
            value.tile_index,
            value.acquisition_time,
            value.link_method,
            tuple(sorted(value.linked_tilt_series_ids or [])),
        )
    if isinstance(value, BatchPosition):
        return common + (
            _path_signature(value.overview_image_path),
            _path_signature(value.search_image_path),
            _path_signature(value.search_metadata_path),
            _path_signature(value.tracking_image_path),
            _path_signature(value.exposure_image_path),
            tuple(_path_signature(path) for path in value.exposure_image_paths),
            value.linked_search_map_id,
            value.linked_search_tile_id,
            value.linked_overview_id,
            tuple(sorted(value.linked_tilt_series_ids or [])),
        )
    if isinstance(value, TiltSeries):
        return common + (
            _path_signature(value.mrc_path),
            _path_signature(value.mdoc_path),
            value.linked_batch_position_id,
        )
    if isinstance(value, Atlas):
        return common + (_path_signature(value.image_path),)
    if isinstance(value, (Sample, Session)):
        return common + (_path_signature(value.path),)
    return common


def _path_signature(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\\", "/").casefold()


def _referenced_signature(value: Any) -> tuple[str, str]:
    """Fingerprint for an entity held by reference on another entity.

    The ID alone is not enough for the same reason it is not enough at the top
    level: two records can share an ID and still be different objects. Pairing
    it with the physical source keeps a held reference as discriminating as the
    entity it points at.
    """

    if value is None:
        return ("", "")
    return (_identity(value), _path_signature(getattr(value, "image_path", None)))


def dedupe_navigation_candidates(candidates: Iterable[Any]) -> tuple[Any, ...]:
    """Collapse repeated entities, preserving first-seen order.

    Two-sided metadata routinely yields the same entity from more than one
    rule; that is one candidate, not an ambiguity.

    Records that share an ID *and* look alike are treated as one entity — real
    session folders can be parsed into duplicate records of a single batch
    position, and reporting those as an ambiguity would disable navigation over
    what is really one destination. Records that share an ID but differ in
    substance are a genuine collision and stay separate, because choosing
    between them would be a guess.
    """

    unique: list[Any] = []
    by_signature: dict[tuple[Any, ...], list[Any]] = {}
    seen_objects: set[int] = set()
    for candidate in candidates:
        if candidate is None or id(candidate) in seen_objects:
            continue
        seen_objects.add(id(candidate))
        bucket = by_signature.setdefault(_signature(candidate), [])
        # Equal paths and links do not make contradictory exposure templates
        # equivalent. Compare metadata only on a duplicate signature, avoiding
        # generic serialisation and its cost on ordinary unique candidates.
        if bucket and (not isinstance(candidate, BatchPosition)
                       or any(item.metadata == candidate.metadata for item in bucket)):
            continue
        bucket.append(candidate)
        unique.append(candidate)
    return tuple(unique)



#: Why a batch position whose ID is shared by contradictory records has no
#: destination and no tilt series (assembly refuses the name link).
AMBIGUOUS_BATCH_EXPLANATION = "Batch ID is not unique in this folder. No tilt series is linked to it."


def ambiguous_batch_ids(batch_positions: Iterable[BatchPosition]) -> frozenset[str]:
    """IDs shared by batch records that differ in substance.

    Repeated copies of one record (an alias reached twice, or identical XML
    rows) collapse through :func:`dedupe_navigation_candidates` and are not
    ambiguous.
    """

    groups: dict[str, list[BatchPosition]] = {}
    for batch in batch_positions:
        groups.setdefault(batch.id, []).append(batch)
    return frozenset(
        batch_id
        for batch_id, members in groups.items()
        if len(members) > 1 and len(dedupe_navigation_candidates(members)) > 1
    )
