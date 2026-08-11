from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.models import Sample, Session
from tomography_session_browser.ui.session_linking import (
    atlas_link_resolutions,
    linked_sample_groups,
)


ProjectGroupKind = Literal["linked", "atlas", "collection", "unresolved"]


@dataclass(slots=True)
class ProjectTreeGroup:
    """Display-only project grouping for the left project tree.

    The grouping keeps source ``Session`` objects unchanged. Custom names
    live outside this model and only affect display/report titles.
    """

    key: str
    kind: ProjectGroupKind
    automatic_name: str
    display_name: str
    sessions: list[Session]
    atlas_session: Session | None = None
    collection_sessions: list[Session] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_renamed(self) -> bool:
        return self.display_name != self.automatic_name


def build_project_tree_groups(
    sessions: list[Session],
    custom_names: dict[str, str] | None = None,
) -> list[ProjectTreeGroup]:
    """Build top-level project groups from the currently loaded sessions.

    Linked atlas/data-collection relationships reuse ``linked_sample_groups``
    but only promote a relationship to top-level grouping when an atlas-bearing
    sample and collection-bearing sample are both present. Pure sample-index
    fallback groups between unrelated collection sessions remain separate.
    """

    custom_names = custom_names or {}
    unique_sessions = _dedupe_sessions(sessions)
    if not unique_sessions:
        return []

    parent_by_sample = {
        id(sample): session for session in unique_sessions for sample in session.samples
    }
    atlas_sessions = [session for session in unique_sessions if _is_atlas_session(session)]
    atlas_set = set(map(id, atlas_sessions))

    collection_to_atlases: dict[int, set[int]] = {}
    atlas_to_collections: dict[int, set[int]] = {id(session): set() for session in atlas_sessions}
    session_by_id = {id(session): session for session in unique_sessions}

    for samples in linked_sample_groups(unique_sessions):
        members = [parent_by_sample.get(id(sample)) for sample in samples]
        members = [session for session in members if session is not None]
        group_atlases = {
            id(session)
            for session in members
            if id(session) in atlas_set and any(_sample_has_atlas(sample, session) for sample in samples)
        }
        group_collections = {
            id(session)
            for session in members
            if id(session) not in atlas_set
            and any(_sample_belongs_to_session(sample, session) and _sample_has_collection_data(sample) for sample in samples)
        }
        if not group_atlases or not group_collections:
            continue
        for collection_id in group_collections:
            collection_to_atlases.setdefault(collection_id, set()).update(group_atlases)
        for atlas_id in group_atlases:
            atlas_to_collections.setdefault(atlas_id, set()).update(group_collections)

    # An AtlasId that matches several atlas samples never reaches the grouping
    # above, because linked_sample_groups deliberately refuses to pick one.
    # Record those candidates here so the collection surfaces as unresolved
    # rather than quietly becoming a standalone collection group.
    for sample, resolution in _ambiguous_atlas_samples(unique_sessions):
        collection_session = parent_by_sample.get(id(sample))
        if collection_session is None or id(collection_session) in atlas_set:
            continue
        if not _sample_has_collection_data(sample):
            continue
        candidate_atlas_ids = {
            id(candidate_session)
            for candidate in resolution.candidates
            if (candidate_session := parent_by_sample.get(id(candidate))) is not None
            and id(candidate_session) in atlas_set
        }
        if len(candidate_atlas_ids) > 1:
            collection_to_atlases.setdefault(id(collection_session), set()).update(
                candidate_atlas_ids
            )

    ambiguous_collection_ids = {
        collection_id
        for collection_id, related_atlases in collection_to_atlases.items()
        if len(related_atlases) > 1
    }
    for collection_ids in atlas_to_collections.values():
        collection_ids.difference_update(ambiguous_collection_ids)

    groups: list[ProjectTreeGroup] = []
    used_collection_ids: set[int] = set()

    for session in unique_sessions:
        session_id = id(session)
        if session_id not in atlas_set:
            continue
        linked_collection_ids = [
            collection_id
            for collection_id in atlas_to_collections.get(session_id, set())
            if collection_id in session_by_id
        ]
        linked_collections = _ordered_sessions(unique_sessions, linked_collection_ids)
        if linked_collections:
            used_collection_ids.update(map(id, linked_collections))
            key = f"linked:{session_key(session)}"
            if len(linked_collections) == 1:
                automatic_name = linked_collections[0].name or linked_collections[0].path.name or "Linked session"
            else:
                automatic_name = session.name or session.path.name or "Linked session"
            groups.append(
                ProjectTreeGroup(
                    key=key,
                    kind="linked",
                    automatic_name=automatic_name,
                    display_name=custom_names.get(key, automatic_name),
                    sessions=[session, *linked_collections],
                    atlas_session=session,
                    collection_sessions=linked_collections,
                )
            )
        else:
            key = f"session:{session_key(session)}"
            automatic_name = session.name or session.path.name or "Atlas session"
            groups.append(
                ProjectTreeGroup(
                    key=key,
                    kind="atlas",
                    automatic_name=automatic_name,
                    display_name=custom_names.get(key, automatic_name),
                    sessions=[session],
                    atlas_session=session,
                )
            )

    for session in unique_sessions:
        session_id = id(session)
        if session_id in atlas_set or session_id in used_collection_ids:
            continue
        if session_id in ambiguous_collection_ids:
            related_names = _candidate_labels(
                [
                    session_by_id[atlas_id]
                    for atlas_id in collection_to_atlases.get(session_id, set())
                    if atlas_id in session_by_id
                ]
            )
            key = f"unresolved:{session_key(session)}"
            automatic_name = session.name or session.path.name or "Unresolved data collection"
            groups.append(
                ProjectTreeGroup(
                    key=key,
                    kind="unresolved",
                    automatic_name=automatic_name,
                    display_name=custom_names.get(key, automatic_name),
                    sessions=[session],
                    collection_sessions=[session],
                    warnings=[
                        "Ambiguous atlas link: this data collection matches "
                        + ", ".join(related_names)
                        + ". It was kept separate."
                    ],
                )
            )
            continue

        key = f"session:{session_key(session)}"
        automatic_name = session.name or session.path.name or "Data collection"
        groups.append(
            ProjectTreeGroup(
                key=key,
                kind="collection",
                automatic_name=automatic_name,
                display_name=custom_names.get(key, automatic_name),
                sessions=[session],
                collection_sessions=[session],
            )
        )

    return groups


def _ambiguous_atlas_samples(sessions: list[Session]):
    """Yield ``(sample, resolution)`` for every sample with an undecidable AtlasId."""

    for sample_key_id, resolution in atlas_link_resolutions(sessions).items():
        if not resolution.ambiguous:
            continue
        sample = next(
            (
                candidate
                for session in sessions
                for candidate in session.samples
                if id(candidate) == sample_key_id
            ),
            None,
        )
        if sample is not None:
            yield sample, resolution


def _candidate_labels(sessions: list[Session]) -> list[str]:
    """Name the candidate atlas sessions, disambiguating identical names by path."""

    names = [session.name or session.path.name or "Atlas session" for session in sessions]
    if len(set(names)) == len(names):
        return sorted(names)
    return sorted(
        f"{name} ({session.path})" for name, session in zip(names, sessions, strict=True)
    )


def session_key(session: Session) -> str:
    return _path_identity_key(session.path)


def _path_identity_key(path: str | Path) -> str:
    value = Path(path)
    if not value.is_absolute():
        value = Path.cwd() / value
    return str(value).replace("\\", "/").casefold()


def group_contains_value(group: ProjectTreeGroup, value: object) -> bool:
    return any(_session_contains_value(session, value) for session in group.sessions)


def _dedupe_sessions(sessions: list[Session]) -> list[Session]:
    unique: list[Session] = []
    seen: set[str] = set()
    for session in sessions:
        key = session_key(session)
        if key in seen:
            continue
        seen.add(key)
        unique.append(session)
    return unique


def _ordered_sessions(all_sessions: list[Session], ids: list[int] | set[int]) -> list[Session]:
    wanted = set(ids)
    return [session for session in all_sessions if id(session) in wanted]


def _is_atlas_session(session: Session) -> bool:
    return session.kind == SessionKind.ATLAS_SCREENING


def _sample_has_atlas(sample: Sample, session: Session) -> bool:
    return _sample_belongs_to_session(sample, session) and sample.atlas is not None


def _sample_belongs_to_session(sample: Sample, session: Session) -> bool:
    return any(sample is candidate for candidate in session.samples)


def _sample_has_collection_data(sample: Sample) -> bool:
    return bool(
        sample.overviews
        or sample.search_maps
        or sample.search_tiles
        or sample.batch_positions
        or sample.tilt_series
    )


def _session_contains_value(session: Session, value: object) -> bool:
    if value is session or value is session.atlas:
        return True
    if value in session.overviews or value in session.search_maps:
        return True
    if value in session.search_tiles or value in session.batch_positions:
        return True
    if value in session.tilt_series:
        return True
    for sample in session.samples:
        if value is sample or value is sample.atlas:
            return True
        if value in sample.overviews or value in sample.search_maps:
            return True
        if value in sample.search_tiles or value in sample.batch_positions:
            return True
        if value in sample.tilt_series:
            return True
    return False
