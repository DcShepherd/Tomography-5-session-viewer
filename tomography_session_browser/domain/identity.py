"""When two values are one folder, or one parsed entity.

A folder can be spelt several ways (relative, another case, other slashes),
and some Tomography 5 layouts expose one parsed entity twice, through a
``Session`` list and through its ``Sample``. These keys let the project tree,
counts and lists treat each once, while keeping unrelated objects that share
a display name apart.

Three identities are in use, and ``dedupe_entities`` takes any of them:
``canonical_entity_key`` (the default), ``summary_entity_key`` (the scanner's
counts) and ``entity_id_key`` (links resolved by id). Where they disagree is
pinned in ``tests/test_divergence_register.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")

#: The file attributes that identify a parsed entity, most specific first.
_IDENTITY_PATHS = (
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
)


def path_identity_key(path: str | Path) -> str:
    """The key every spelling of one folder shares: absolute, forward
    slashes, casefolded."""

    value = Path(path)
    if not value.is_absolute():
        value = Path.cwd() / value
    return str(value).replace("\\", "/").casefold()


def canonical_entity_key(value: Any) -> tuple[str, str]:
    """Return a stable display identity for parsed/session entities.

    Some Tomography 5 layouts expose the same parsed objects both through
    ``Session`` lists and through their containing ``Sample``. Presenter and
    report totals should count those physical entities once, while still
    keeping unrelated objects with the same display name separate.
    """

    type_name = type(value).__name__
    for attr in _IDENTITY_PATHS:
        path = getattr(value, attr, None)
        if path:
            return type_name, f"path:{path_identity_key(path)}"
    exposure_paths = getattr(value, "exposure_image_paths", None)
    if exposure_paths:
        first_path = next((path for path in exposure_paths if path), None)
        if first_path:
            return type_name, f"path:{path_identity_key(first_path)}"
    entity_id = getattr(value, "id", None)
    if entity_id:
        return type_name, f"id:{entity_id}"
    return type_name, f"object:{id(value)}"


def summary_entity_key(value: Any) -> tuple[str, str]:
    """The identity ``Session.metadata_summary["counts"]`` counts with.

    An older twin of ``canonical_entity_key`` that differs only at the edges:
    it compares paths as written rather than resolved against the working
    directory, ignores ``exposure_image_paths``, and puts paths, ids and
    object ids in one namespace. Kept apart so the summary counts cannot move.
    """

    type_name = type(value).__name__
    for attr in _IDENTITY_PATHS:
        path = getattr(value, attr, None)
        if path:
            return type_name, str(Path(path)).replace("\\", "/").casefold()
    entity_id = getattr(value, "id", None)
    if entity_id:
        return type_name, str(entity_id)
    return type_name, str(id(value))


def entity_id_key(value: Any) -> str:
    """Identity by ``id`` alone: two entities sharing an id are one."""

    return str(getattr(value, "id", id(value)))


def dedupe_entities(
    values: Iterable[T],
    *,
    key: Callable[[T], Hashable] = canonical_entity_key,
) -> list[T]:
    """Preserve order while removing values whose ``key`` was already seen."""

    unique: list[T] = []
    seen: set[Hashable] = set()
    for value in values:
        identity = key(value)
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(value)
    return unique
