"""What a viewer list holds: object rows, and the headers and dividers between them (B4.3).

Qt-free. A tab's list is a sequence of **entries**: an ``ItemEntry`` for each
object, a ``GroupHeader`` that names a group of them, and a ``Divider`` that
separates two runs inside a group. Entries hold identifiers (an object's
``domain.identity.entity_key_text``) and scalars, never entities, so they can
be cached and compared like the rest of a prepared tab.

One rule decides each tab's entries:

* **Overviews, Search maps, Batch positions, Tilt series** are grouped by the
  sample that lists them, with a header per sample, **only when the list spans
  more than one sample** (D27, D31);
* **Search tiles** are grouped by batch position, always: the header names the
  batch and its search map and counts its exposures (D23, D31);
* **Tilt series** also take a divider between batches inside a sample, labelled
  only for an *inferred* group, the failed series no batch record accounts for
  (D34, option B).

Grouping never drops, merges or invents an object: ``GroupedItems.items`` is
the same objects as the input, in the order the entries show them.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from tomography_session_browser.domain.identity import entity_key_text
from tomography_session_browser.domain.models import BatchPosition, SearchTile, TiltSeries
from tomography_session_browser.presentation.tab_keys import TabKey
from tomography_session_browser.services.item_status import ItemListStatus

#: A tilt-series divider names an inferred group like this.
INFERRED_GROUP_SUFFIX = "inferred group, no batch record"
#: The header of search tiles that no batch position accounts for.
NO_BATCH_LABEL = "No linked batch position"


@dataclass(frozen=True, slots=True)
class ItemEntry:
    """A row that shows an object."""

    key: str


@dataclass(frozen=True, slots=True)
class GroupHeader:
    """A row that names a group: ``label``, then ``detail`` in a quieter tone.

    ``count`` and ``failed`` are of the whole group; the list shows the counts
    of the rows the filter leaves. ``noun`` (singular) makes the count read
    "5 exposures" rather than "5".
    """

    label: str
    count: int
    failed: int = 0
    detail: str = ""
    noun: str = ""


@dataclass(frozen=True, slots=True)
class Divider:
    """A line between two runs of rows; ``label`` names an inferred group."""

    label: str | None = None


Entry = ItemEntry | GroupHeader | Divider

#: ``value`` -> the owner's identity and its header label, or ``None`` if unowned.
OwnerOf = Callable[[Any], tuple[Hashable, str] | None]


@dataclass(frozen=True, slots=True)
class GroupedItems:
    """A tab's objects in display order, and the entries that show them."""

    items: list[Any]
    entries: tuple[Entry, ...]

    @property
    def has_groups(self) -> bool:
        return any(not isinstance(entry, ItemEntry) for entry in self.entries)


def flat(items: Sequence[Any]) -> GroupedItems:
    """The objects as they are, with no header and no divider."""

    values = list(items)
    return GroupedItems(values, tuple(ItemEntry(entity_key_text(value)) for value in values))


def failed_keys(statuses: Mapping[str, ItemListStatus]) -> frozenset[str]:
    """The rows whose status carries the ``failed`` tag: what a header counts as failed."""

    return frozenset(key for key, status in statuses.items() if "failed" in status.tags)


def _by_sample(items: Sequence[Any], owner_of: OwnerOf) -> list[tuple[str, list[Any]]]:
    """``(header label, objects)`` per owner, in the order owners first appear."""

    groups: dict[Hashable, tuple[str, list[Any]]] = {}
    for value in items:
        owner = owner_of(value) or (None, "No sample")
        groups.setdefault(owner[0], (owner[1], []))[1].append(value)
    return list(groups.values())


def sample_grouped(
    items: Sequence[Any],
    owner_of: OwnerOf,
    failed: frozenset[str],
    *,
    runs_of: Callable[[list[Any]], list[Entry]] | None = None,
) -> GroupedItems:
    """Group by sample when the list spans more than one; else leave it flat.

    ``runs_of`` turns one sample's objects into entries (item entries with
    dividers between them); by default each object is one item entry.
    """

    groups = _by_sample(items, owner_of)
    headed = len(groups) > 1
    ordered: list[Any] = []
    entries: list[Entry] = []
    for label, members in groups:
        if headed:
            members_failed = sum(1 for value in members if entity_key_text(value) in failed)
            entries.append(GroupHeader(label, len(members), members_failed))
        ordered.extend(members)
        entries.extend(runs_of(members) if runs_of is not None else [ItemEntry(entity_key_text(v)) for v in members])
    return GroupedItems(ordered, tuple(entries))


# --------------------------------------------------------------------- tilt series


def tilt_series_runs(
    batch_of: Mapping[str, str],
    inferred_label_of: Mapping[str, str],
) -> Callable[[list[Any]], list[Entry]]:
    """Item entries with a divider between the batches of one sample's tilt series.

    A *run* is consecutive series on one batch (the batch a record names, else
    the inferred group's label, else none). Between two runs there is a hairline;
    an inferred run also takes its label, which is what the row used to say.
    The list order is the sample's own: nothing is regrouped.
    """

    def entries_for(members: list[TiltSeries]) -> list[Entry]:
        entries: list[Entry] = []
        previous: tuple[str, str] | None = None
        for tilt in members:
            recorded = batch_of.get(tilt.id)
            inferred = inferred_label_of.get(tilt.id) if recorded is None else None
            run = ("batch", recorded) if recorded is not None else ("inferred", inferred) if inferred else ("none", "")
            if run != previous:
                if inferred:
                    entries.append(Divider(f"{inferred} · {INFERRED_GROUP_SUFFIX}"))
                elif previous is not None:
                    entries.append(Divider())
                previous = run
            entries.append(ItemEntry(entity_key_text(tilt)))
        return entries

    return entries_for


def batch_of_tilt(batches: Sequence[BatchPosition], tilts: Sequence[TiltSeries]) -> dict[str, str]:
    """``tilt.id`` -> the id of the batch a record ties it to, from either side."""

    by_batch_side = {tid: batch.id for batch in batches for tid in batch.linked_tilt_series_ids or []}
    found: dict[str, str] = {}
    for tilt in tilts:
        batch_id = tilt.linked_batch_position_id or by_batch_side.get(tilt.id)
        if batch_id:
            found[tilt.id] = batch_id
    return found


# ------------------------------------------------------------------ search tiles


def search_tiles_by_batch(tiles: Sequence[SearchTile], owner_of: OwnerOf | None = None) -> GroupedItems:
    """One header per batch position (and search map), counting its exposures.

    Groups come in the order their first tile appears; tiles no batch accounts
    for are the last group, so an unlinked tile stays visible. A tile never
    changes group, and the list's own order inside a group is kept. When the
    list spans more than one sample, a header also names its sample: batch
    names repeat from grid to grid.
    """

    owners = {id(tile): (owner_of(tile) if owner_of is not None else None) for tile in tiles}
    spans_samples = len({owner[0] for owner in owners.values() if owner is not None}) > 1

    groups: dict[tuple[str | None, str | None], list[SearchTile]] = {}
    for tile in tiles:
        groups.setdefault((tile.batch_position_id, tile.search_map_name), []).append(tile)
    ordered_groups = sorted(groups.items(), key=lambda item: item[0][0] is None)
    ordered: list[Any] = []
    entries: list[Entry] = []
    for (batch_id, map_name), members in ordered_groups:
        first = members[0]
        label = (first.batch_position_name or batch_id) if batch_id is not None else NO_BATCH_LABEL
        owner = owners[id(first)]
        detail = " · ".join(part for part in (owner[1] if spans_samples and owner else "", map_name or "") if part)
        entries.append(GroupHeader(str(label), len(members), detail=detail, noun="exposure"))
        ordered.extend(members)
        entries.extend(ItemEntry(entity_key_text(tile)) for tile in members)
    return GroupedItems(ordered, tuple(entries))


# ------------------------------------------------------------------------ the rule


def entries_for_tab(
    label: str,
    items: Sequence[Any],
    *,
    owner_of: OwnerOf,
    statuses: Mapping[str, ItemListStatus],
    batches: Sequence[BatchPosition] = (),
    inferred_labels: Mapping[str, str] | None = None,
) -> GroupedItems:
    """The one rule per tab (see the module docstring)."""

    if label == TabKey.SEARCH:
        return search_tiles_by_batch(items, owner_of)
    if label in (TabKey.OVERVIEW, TabKey.SEARCH_MAP, TabKey.BATCH_POSITION):
        return sample_grouped(items, owner_of, failed_keys(statuses))
    if label == TabKey.TILT_SERIES:
        runs = tilt_series_runs(batch_of_tilt(batches, list(items)), inferred_labels or {})
        return sample_grouped(items, owner_of, failed_keys(statuses), runs_of=runs)
    return flat(items)
