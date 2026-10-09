"""One candidate uniqueness rule for metadata resolution and marker fallbacks."""

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
)

from .models import NavigationResolution, STATE_NAVIGABLE, STATE_AMBIGUOUS, STATE_UNRESOLVED


def _identity(value: Any) -> str:
    identifier = getattr(value, "id", None)
    return str(identifier) if identifier is not None else ""


def _display_name(value: Any) -> str:
    name = getattr(value, "name", None)
    if isinstance(name, str) and name:
        return name
    if isinstance(value, Atlas) and value.image_path is not None:
        # An Atlas has no name, and its ID is its whole folder path. The
        # session and sample folders are what tell two candidates apart.
        sample_folder = value.image_path.parent.parent
        return f"{sample_folder.parent.name}/{sample_folder.name} atlas"
    return _identity(value) or "unnamed"


def _candidate_names(candidates: tuple[Any, ...]) -> list[str]:
    """Names for an ambiguity, each Atlas told apart by its folder when two
    read the same (as the project tree's ambiguous-link warning does)."""

    names = [_display_name(item) for item in candidates]
    repeated = {name for name in names if names.count(name) > 1}
    return [
        f"{name} ({item.image_path.parent.parent})"
        if name in repeated and isinstance(item, Atlas) and item.image_path is not None
        else name
        for name, item in zip(names, candidates, strict=True)
    ]


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

    unique: dict[tuple[Any, ...], Any] = {}
    seen_objects: set[int] = set()
    for candidate in candidates:
        if candidate is None or id(candidate) in seen_objects:
            continue
        seen_objects.add(id(candidate))
        unique.setdefault(_signature(candidate), candidate)
    return tuple(unique.values())


def navigable_resolution(target: Any, *, provenance: str, explanation: str) -> NavigationResolution:
    return NavigationResolution(
        target=target,
        candidate_ids=(_identity(target),),
        state=STATE_NAVIGABLE,
        explanation=explanation,
        provenance=provenance,
    )


def ambiguous_resolution(
    candidates: Iterable[Any],
    *,
    provenance: str,
    explanation: str,
) -> NavigationResolution:
    return NavigationResolution(
        target=None,
        candidate_ids=tuple(sorted(_identity(item) for item in candidates)),
        state=STATE_AMBIGUOUS,
        explanation=explanation,
        provenance=provenance,
    )


def unresolved_resolution(*, provenance: str, explanation: str) -> NavigationResolution:
    return NavigationResolution(
        state=STATE_UNRESOLVED,
        explanation=explanation,
        provenance=provenance,
    )


def navigation_resolution_from_candidates(
    candidates: Iterable[Any],
    *,
    provenance: str,
    navigable_explanation: str,
    ambiguous_prefix: str,
    unresolved_explanation: str,
) -> NavigationResolution:
    """Offer a destination only when exactly one candidate survives.

    Shared by metadata resolution and marker-based fallback matching, so both
    apply the same uniqueness rule.
    """

    unique = dedupe_navigation_candidates(candidates)
    if len(unique) == 1:
        return navigable_resolution(unique[0], provenance=provenance, explanation=navigable_explanation)
    if len(unique) > 1:
        names = ", ".join(sorted(_candidate_names(unique)))
        return ambiguous_resolution(
            unique,
            provenance=provenance,
            explanation=f"{ambiguous_prefix}: {names}.",
        )
    return unresolved_resolution(provenance=provenance, explanation=unresolved_explanation)
