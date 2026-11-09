"""The user's own groups in the project panel (B4, owner 9 Nov 2026).

Display only, for this app session, like a rename. A group is a heading over
top-level rows (project groups and processing folders); it is not a
``ProjectTreeGroup`` kind, has no badge and no scope, and changes no link,
count or report scope.

Membership is stored by source identity: the ``session_key`` of each session
of a member row, or the processing group key of a folder. ``name_key`` would
not do: a collection's key changes when an atlas links to it. Rendering never
prunes membership: a row that is absent for now (a processing folder whose
tomograms are all placed) is hidden and comes back. Only Ungroup and Remove
from group take keys out.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from tomography_session_browser.presentation.project_tree import (
    PROJECT_BADGE_MEANINGS,
    ProjectTreeGroup,
    processing_group_key,
    project_group_badge,
    session_key,
)


@dataclass(frozen=True, slots=True)
class UserGroup:
    key: str
    name: str
    members: frozenset[str]


@dataclass(frozen=True, slots=True)
class UserHeading:
    """A user group as drawn now: only its members that are in the project."""

    group: UserGroup
    members: tuple[ProjectTreeGroup, ...]


def member_keys(row: ProjectTreeGroup) -> frozenset[str]:
    if row.processing_source is not None:
        return frozenset({processing_group_key(row.processing_source.id)})
    return frozenset(session_key(session) for session in row.sessions)


def owner_of(row: ProjectTreeGroup, user_groups: Sequence[UserGroup]) -> UserGroup | None:
    """The one user group that holds the row. A row whose sessions sit in two groups has none."""

    keys = member_keys(row)
    owners = [group for group in user_groups if group.members & keys]
    return owners[0] if len(owners) == 1 else None


def arrange(rows: Sequence[ProjectTreeGroup],
            user_groups: Sequence[UserGroup]) -> tuple[ProjectTreeGroup | UserHeading, ...]:
    """The top-level order: each heading at the place of its first member, with its members in order."""

    members: dict[str, list[ProjectTreeGroup]] = {}
    order: list[ProjectTreeGroup | str] = []
    for row in rows:
        owner = owner_of(row, user_groups)
        if owner is None:
            order.append(row)
            continue
        if owner.key not in members:
            members[owner.key] = []
            order.append(owner.key)
        members[owner.key].append(row)
    by_key = {group.key: group for group in user_groups}
    return tuple(UserHeading(by_key[entry], tuple(members[entry])) if isinstance(entry, str) else entry
                 for entry in order)


def headings(arranged: Iterable[ProjectTreeGroup | UserHeading]) -> tuple[UserHeading, ...]:
    return tuple(entry for entry in arranged if isinstance(entry, UserHeading))


def with_new_group(user_groups: Sequence[UserGroup], key: str, name: str,
                   rows: Sequence[ProjectTreeGroup]) -> tuple[UserGroup, ...]:
    """Add a group of ``rows``; a row is in one group only, so it leaves any other first."""

    keys = frozenset().union(*(member_keys(row) for row in rows))
    kept = without_keys(user_groups, keys)
    return (*kept, UserGroup(key, name, keys))


def without_keys(user_groups: Sequence[UserGroup], keys: frozenset[str]) -> tuple[UserGroup, ...]:
    """Take ``keys`` out of every group; a group left with no member is gone."""

    trimmed = (replace(group, members=group.members - keys) for group in user_groups)
    return tuple(group for group in trimmed if group.members)


def renamed(user_groups: Sequence[UserGroup], key: str, name: str) -> tuple[UserGroup, ...]:
    return tuple(replace(group, name=name) if group.key == key else group for group in user_groups)


def ungrouped(user_groups: Sequence[UserGroup], key: str) -> tuple[UserGroup, ...]:
    return tuple(group for group in user_groups if group.key != key)


def heading_text(heading: UserHeading, tomogram_count) -> str:
    """Context lines for a heading: what each member is. ``tomogram_count(row)`` counts a folder's rows."""

    lines = [f"Rows: {len(heading.members)}"]
    for row in heading.members:
        kind = PROJECT_BADGE_MEANINGS.get(project_group_badge(row), "Project group")
        if row.processing_source is not None:
            count = tomogram_count(row)
            kind = f"{kind}, {count} tomogram{'' if count == 1 else 's'}"
        lines.append(f"{row.display_name}: {kind}")
    return "\n".join(lines)
