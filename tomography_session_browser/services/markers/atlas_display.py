"""The Atlas tab's collection filter, applied after markers are built.

Display only. Markers are built from the whole grid, so hiding a collection
never changes a visible position's status, members or tooltip.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from tomography_session_browser.domain.identity import entity_key_text
from tomography_session_browser.domain.markers import ImageMarker
from tomography_session_browser.domain.models import BatchPosition, TiltSeries
from tomography_session_browser.services.markers.context import MarkerContext


@dataclass(frozen=True, slots=True)
class AtlasDisplayFilter:
    """Which batch positions and tilt series the Atlas tab draws.

    Keys are ``entity_key_text``. Markers are built from the whole context, so
    a hidden collection never changes a visible position's status or members;
    the filter only leaves hidden ones out of the output.
    """

    visible_batch_keys: frozenset[str] = frozenset()
    visible_tilt_keys: frozenset[str] = frozenset()


def visible_atlas_markers(
    markers: list[ImageMarker],
    context: MarkerContext,
    display_filter: AtlasDisplayFilter,
) -> list[ImageMarker]:
    """``markers`` without the batch positions and tilt series the filter hides.

    Records that share a batch ID share a folder, so they share visibility and
    one key serves them all.
    """

    batch_keys = _keys_by_id(context.batch_positions)
    tilt_keys = _keys_by_id(context.tilt_series)
    visible: list[ImageMarker] = []
    for marker in markers:
        role = marker.metadata.get("atlas_lod_role")
        if role in {"batch_position", "per_exposure"}:
            batch_id = marker.metadata.get("batch_position_id") or marker.metadata.get("batch_id") or marker.linked_object_id
            keep = _shown(batch_keys.get(str(batch_id or "")), display_filter.visible_batch_keys)
        elif role == "unattributed":
            keep = _shown(tilt_keys.get(marker.linked_object_id or ""), display_filter.visible_tilt_keys)
        else:
            keep = True
        if keep:
            visible.append(marker)
    return visible



def _keys_by_id(values: Iterable[BatchPosition] | Iterable[TiltSeries]) -> dict[str, set[str]]:
    keys: dict[str, set[str]] = {}
    for value in values:
        keys.setdefault(value.id, set()).add(entity_key_text(value))
    return keys


def _shown(keys: set[str] | None, visible: frozenset[str]) -> bool:
    # A marker of an entity outside the context is not the filter's to hide.
    return keys is None or not keys.isdisjoint(visible)
