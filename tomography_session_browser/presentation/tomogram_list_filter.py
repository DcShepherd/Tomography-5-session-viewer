"""One pure owner for list membership; widgets only render the ordered keys."""
from dataclasses import dataclass
from collections.abc import Callable, Mapping

from tomography_session_browser.domain.tomogram_tags import TomogramTags
from tomography_session_browser.domain.tomograms import Volume
from tomography_session_browser.presentation.tomogram_state import ReviewSegment
from tomography_session_browser.presentation.tomogram_tabs import TomogramRow, needs_attention, sorted_within_groups
from tomography_session_browser.presentation.tomogram_tag_filters import TagFilter
from tomography_session_browser.services.tomogram_tags.binding import ProjectKey


@dataclass(frozen=True, slots=True)
class ListFilter:
    query: str = ""
    segment: ReviewSegment = ReviewSegment.ALL
    sort: str = "Name"
    route_keys: frozenset[str] | None = None
    scope_label: str = ""
    tags: TagFilter = TagFilter()


@dataclass(frozen=True, slots=True)
class ListMembership:
    ordered_keys: tuple[str, ...]
    working_set: frozenset[str]


def list_membership(rows: Mapping[str, TomogramRow], filters: ListFilter, working_set: frozenset[str] | None,
                    opened: set[str] | frozenset[str], *,
                    tag_rows: Mapping[str, tuple[ProjectKey | None, TomogramTags | None]] | None = None,
                    stated_for: Callable[[Volume], float | None] = lambda _volume: None) -> ListMembership:
    if working_set is None:
        working_set = frozenset(key for key, row in rows.items() if
            (filters.route_keys is None or key in filters.route_keys) and
            (filters.segment != ReviewSegment.NOT_OPENED or key not in opened) and
            (filters.segment != ReviewSegment.ATTENTION or needs_attention(row, stated_for)) and
            filters.tags.accepts(*(tag_rows or {}).get(key, (None, None))))
    query = filters.query.casefold()
    ordered = sorted_within_groups([row for key, row in rows.items() if key in working_set and
        query in row.tomogram.name.casefold()], filters.sort)
    return ListMembership(tuple(row.tomogram.id for row in ordered), working_set)


def keep_not_opened_membership(previous: ListFilter, restored: ListFilter) -> bool:
    return (previous.route_keys is None and not previous.tags.active and not restored.tags.active and
            previous.segment == restored.segment == ReviewSegment.NOT_OPENED and previous.query == restored.query)
