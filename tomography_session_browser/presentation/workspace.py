"""A frozen index of loaded sessions, with the existing viewer scope rules.

The index containers are immutable; domain and group objects retain their
existing mutability and identity. This is not a deep copy of scientific data.
Rebuild after changing loaded sessions or their structure. The window and
background preparation share this model throughout one loaded project.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, overload
import logging
import re
from types import MappingProxyType

from tomography_session_browser.domain.tomograms import ProcessingSource, Tomogram
from tomography_session_browser.domain.link_confirmations import LinkConfirmation
from tomography_session_browser.services.tomogram_links import TomogramLink, resolve_tomogram_link
from tomography_session_browser.services.acquisition_index import AcquisitionIndex, build_acquisition_index
from tomography_session_browser.domain.traversal import is_single_atlas_placeholder
from tomography_session_browser.domain.identity import entity_key_text, canonical_entity_key, dedupe_entities, path_identity_key
from tomography_session_browser.domain.models import (
    Atlas, BatchPosition, Overview, Sample, SearchMap, SearchTile, Session, TiltSeries,
)
from tomography_session_browser.presentation.project_tree import (
    ProjectTreeGroup, build_project_tree_groups, group_contains_value, session_key,
)
from tomography_session_browser.presentation.scopes import EntityGroup, LinkedSampleGroup
from tomography_session_browser.presentation.tomogram_placement import TomogramPlacement
from tomography_session_browser.presentation.tomogram_selection import AllTomograms, FolderTomograms, acquisition_scope, rebound_selection
from tomography_session_browser.services.markers.context import MarkerContext
from tomography_session_browser.services.session_linking import linked_sample_groups
from tomography_session_browser.services.tilt_series_validation import GridValidations

LOGGER = logging.getLogger(__name__)
Entity = Atlas | Overview | SearchMap | SearchTile | BatchPosition | TiltSeries
#: What the review is narrowed to. ``None`` is the whole loaded project: the
#: one form of the project root (a dashboard's several sessions together are
#: a ``DashboardScopeValue``, not a scope).
Scope = Session | Sample | LinkedSampleGroup | EntityGroup | ProjectTreeGroup | AllTomograms | FolderTomograms | None


class EntityKind(StrEnum):
    ATLAS = "atlases"
    OVERVIEW = "overviews"
    SEARCH_MAP = "search_maps"
    SEARCH_TILE = "search_tiles"
    BATCH_POSITION = "batch_positions"
    TILT_SERIES = "tilt_series"


_TYPES = {
    EntityKind.ATLAS: Atlas, EntityKind.OVERVIEW: Overview,
    EntityKind.SEARCH_MAP: SearchMap, EntityKind.SEARCH_TILE: SearchTile,
    EntityKind.BATCH_POSITION: BatchPosition, EntityKind.TILT_SERIES: TiltSeries,
}
_LIST_KINDS = tuple(kind for kind in EntityKind if kind != EntityKind.ATLAS)


def _children(owner: Session | Sample) -> list[Entity]:
    # This order is also the legacy find(id) precedence.
    values = [value for kind in _LIST_KINDS for value in getattr(owner, kind)]
    if owner.atlas is not None:
        values.append(owner.atlas)
    return values


def atlas_display_key(atlas: Atlas) -> str:
    # Deliberately not canonical_entity_key: atlas tabs have their own identity.
    return path_identity_key(atlas.image_path) if atlas.image_path is not None else atlas.id


def display_atlases(session: Session) -> list[Atlas]:
    values = [sample.atlas for sample in session.samples if sample.atlas is not None and not is_single_atlas_placeholder(sample)]
    return dedupe_entities(values, key=atlas_display_key) if values else ([session.atlas] if session.atlas is not None else [])


def session_entities(session: Session, kind: EntityKind) -> list[Entity]:
    """Distinct rows from a session, followed by its samples, in source order."""
    return _scope_entity_rows(kind,
        [*getattr(session, kind), *(value for sample in session.samples for value in getattr(sample, kind))],
    )


def _scope_entity_rows(kind: EntityKind, values: list[Any]) -> list[Any]:
    # Search rows are exposure-specific: distinct IDs may share one tile image.
    return dedupe_entities(values, key=entity_key_text if kind == EntityKind.SEARCH_TILE else canonical_entity_key)


def summary_atlas_pairs(session: Session) -> list[tuple[str, Atlas]]:
    """Presenter's labelled atlas variant: preserve raw names and duplicates."""
    values = [(sample.name, sample.atlas) for sample in session.samples
              if sample.atlas is not None and not is_single_atlas_placeholder(sample)]
    return values or ([(session.name, session.atlas)] if session.atlas is not None else [])


def display_sample_name(name: str) -> str:
    text = re.sub(r"^\s*\d+\.\s*", "", name or "").strip()
    return text or name or "Sample"


@dataclass(frozen=True, slots=True, eq=False)
class Workspace:
    sessions: tuple[Session, ...]
    group_names: Mapping[str, str]
    project_groups: tuple[ProjectTreeGroup, ...]
    sample_groups: tuple[tuple[Sample, ...], ...]
    _owners_by_id: Mapping[str, tuple[Sample, ...]] = field(repr=False)
    _owners_by_object: Mapping[int, tuple[Sample, ...]] = field(repr=False)
    _objects: Mapping[str, Session | Sample | Entity] = field(repr=False)
    _object_candidates: Mapping[str, tuple[Session | Sample | Entity, ...]] = field(repr=False)
    _atlases_by_sample: Mapping[int, tuple[Atlas, ...]] = field(repr=False)
    # Marker contexts already built, so every caller shares one, and with it
    # the tilt-series validation the context keeps. Keyed by the owning
    # sample group (this workspace's own tuple) and the failed-tilt set.
    _marker_contexts: dict[tuple[int, frozenset[str]], MarkerContext] = field(
        default_factory=dict, init=False, repr=False,
    )
    _project_marker_contexts: dict[frozenset[str], MarkerContext] = field(
        default_factory=dict, init=False, repr=False,
    )
    # ``tilt_validations``, once worked out.
    _grid_validations: list[GridValidations] = field(default_factory=list, init=False, repr=False)
    # ``id(entity)`` -> (the owner's ``id``, its header label), built on first use.
    _row_owners: dict[int, tuple[int, str]] = field(default_factory=dict, init=False, repr=False)

    processing_sources: tuple[ProcessingSource, ...] = ()
    tomogram_links: Mapping[str, TomogramLink] = field(default_factory=dict)
    acquisition_index: AcquisitionIndex = field(default_factory=lambda: build_acquisition_index(()))
    link_confirmations: tuple[LinkConfirmation, ...] = ()
    tomogram_placement: TomogramPlacement = field(init=False, repr=False)

    def __post_init__(self) -> None:
        placement = TomogramPlacement.build(self)
        object.__setattr__(self, "tomogram_placement", placement)
        acquisition_groups = tuple(group for group in self.project_groups if group.processing_source is None)
        object.__setattr__(self, "project_groups", acquisition_groups +
                           placement.residual_groups(self.processing_sources, self.group_names))

    def with_acquisition_index(self, index: AcquisitionIndex) -> Workspace:
        if index.series != self.acquisition_index.series or index.processing != self.acquisition_index.processing:
            raise ValueError('Acquisition evidence belongs to an obsolete workspace revision.')
        links = {item.id: resolve_tomogram_link(item, index, self.link_confirmations) for item in index.processing}
        return replace(self, acquisition_index=index, tomogram_links=MappingProxyType(links))

    def with_link_confirmations(self, records: tuple[LinkConfirmation, ...]) -> Workspace:
        links = {item.id: resolve_tomogram_link(item, self.acquisition_index, records) for item in self.acquisition_index.processing}
        return replace(self, link_confirmations=records, tomogram_links=MappingProxyType(links))

    @property
    def has_sources(self) -> bool:
        return bool(self.sessions or self.processing_sources)

    @property
    def source_roots(self) -> tuple[Path, ...]:
        """All loaded source roots, independent of the current review scope."""
        return (*(session.path for session in self.sessions),
                *(root for source in self.processing_sources for root in source.protected_roots))

    def rebind_scope(self, scope: Scope) -> Scope:
        """Rebind typed review scopes without broadening a removed folder."""
        if isinstance(scope, AllTomograms | FolderTomograms):
            return rebound_selection(self, scope)
        if isinstance(scope, ProjectTreeGroup) and scope.processing_source is not None:
            matches = [group for group in self.project_groups if group.key == scope.key]
            if len(matches) == 1:
                return matches[0]
            source = next((source for source in self.processing_sources if source.id == scope.processing_source.id), None)
            return replace(scope, processing_source=source, automatic_name=source.name,
                           display_name=self.group_names.get(scope.key, source.name)) if source is not None else None
        return scope

    def tomograms(self, scope: Scope = None) -> tuple[Tomogram, ...]:
        if isinstance(scope, FolderTomograms):
            return tuple(row for row in self.tomograms(AllTomograms(scope.scope)) if row.source_id == scope.source_id)
        if isinstance(scope, AllTomograms):
            return self.tomograms(scope.scope) if scope.scope is not None else tuple(
                row for row in self.tomogram_placement.rows if row.id in self.tomogram_placement.owner_by_row)
        if isinstance(scope, ProjectTreeGroup) and scope.processing_source is not None:
            return self.tomogram_placement.residual_by_source.get(scope.processing_source.id, ())
        if scope is None:
            return self.tomogram_placement.rows
        return self.tomogram_placement.for_parents(self.entities(EntityKind.TILT_SERIES, scope))

    @classmethod
    def build(cls, sessions: Sequence[Session], group_names: Mapping[str, str] | None = None, *,
              processing_sources: Sequence[ProcessingSource] = (),
              link_confirmations: tuple[LinkConfirmation, ...] = ()) -> Workspace:
        loaded = list(sessions)
        names = dict(group_names or {})
        try:
            linked = linked_sample_groups(loaded)
        except Exception:  # retain the window's recoverable scoping boundary
            LOGGER.warning("linked_sample_groups failed; falling back to per-Sample scoping", exc_info=True)
            linked = None
        groups = tuple(tuple(group) for group in (
            linked if linked is not None else [[sample] for session in loaded for sample in session.samples]
        ))
        owners: dict[str, tuple[Sample, ...]] = {}
        object_owners: dict[int, tuple[Sample, ...]] = {}
        ambiguous: set[str] = set()
        atlas_members: dict[int, tuple[Atlas, ...]] = {}
        for members in groups:
            atlases = tuple(dedupe_entities(
                [sample.atlas for sample in members if sample.atlas is not None and not is_single_atlas_placeholder(sample)],
                key=atlas_display_key,
            )) if linked is not None else ()
            for sample in members:
                atlas_members.setdefault(id(sample), atlases)
                for entity in _children(sample):
                    object_owners[id(entity)] = members
                    if entity.id in ambiguous:
                        continue
                    previous = owners.get(entity.id)
                    if previous is not None and {id(item) for item in previous} != {id(item) for item in members}:
                        ambiguous.add(entity.id)
                        owners.pop(entity.id, None)
                    else:
                        owners[entity.id] = members

        objects: dict[str, Session | Sample | Entity] = {}
        candidates: dict[str, list[Session | Sample | Entity]] = {}
        for session in loaded:
            for value in (session, *_children(session)):
                objects.setdefault(value.id, value)
                candidates.setdefault(value.id, []).append(value)
            for sample in session.samples:
                for value in (sample, *_children(sample)):
                    objects.setdefault(value.id, value)
                    candidates.setdefault(value.id, []).append(value)

        # Project grouping historically deduplicates folders, whereas marker
        # scoping retains the supplied sessions. Reuse links only for the same
        # universe; duplicate-folder inputs keep that existing distinction.
        unique_folders = len({session_key(session) for session in loaded}) == len(loaded)
        project = build_project_tree_groups(
            loaded, names, sample_groups=(linked or ()) if unique_folders else None,
        )
        sources = tuple({source.id: source for source in processing_sources}.values())
        tilts = [value for session in loaded for value in session_entities(session, EntityKind.TILT_SERIES) if isinstance(value, TiltSeries)]
        tomograms = tuple(item for source in sources for item in source.tomograms)
        index = build_acquisition_index(tilts if tomograms else (), tomograms)
        links = {item.id: resolve_tomogram_link(item, index, link_confirmations) for item in tomograms}
        return cls(tuple(loaded), MappingProxyType(names), tuple(project), groups,
                   MappingProxyType(owners), MappingProxyType(object_owners),
                   MappingProxyType(objects), MappingProxyType({key: tuple(values) for key, values in candidates.items()}),
                   MappingProxyType(atlas_members), processing_sources=sources, tomogram_links=MappingProxyType(links),
                   acquisition_index=index, link_confirmations=link_confirmations)

    # Each kind returns its own entity type, so a caller can pass the result on
    # as, say, ``list[SearchMap]``. Typing only: one implementation below.
    @overload
    def entities(self, kind: Literal[EntityKind.ATLAS], scope: Scope = None) -> list[Atlas]: ...
    @overload
    def entities(self, kind: Literal[EntityKind.OVERVIEW], scope: Scope = None) -> list[Overview]: ...
    @overload
    def entities(self, kind: Literal[EntityKind.SEARCH_MAP], scope: Scope = None) -> list[SearchMap]: ...
    @overload
    def entities(self, kind: Literal[EntityKind.SEARCH_TILE], scope: Scope = None) -> list[SearchTile]: ...
    @overload
    def entities(self, kind: Literal[EntityKind.BATCH_POSITION], scope: Scope = None) -> list[BatchPosition]: ...
    @overload
    def entities(self, kind: Literal[EntityKind.TILT_SERIES], scope: Scope = None) -> list[TiltSeries]: ...
    @overload
    def entities(self, kind: EntityKind, scope: Scope = None) -> list[Entity]: ...
    def entities(self, kind: EntityKind, scope: Scope = None) -> list[Any]:
        scope = acquisition_scope(scope)
        if isinstance(scope, ProjectTreeGroup) and scope.processing_source is not None:
            return []
        if kind == EntityKind.ATLAS:
            return self.atlases(scope)
        entity_type = _TYPES[kind]
        if isinstance(scope, EntityGroup):
            return [value for value in scope.values if isinstance(value, entity_type)]
        if isinstance(scope, (Sample, LinkedSampleGroup)):
            return list(getattr(scope, kind))
        if isinstance(scope, Session):
            return session_entities(scope, kind)
        sessions = scope.sessions if isinstance(scope, ProjectTreeGroup) else (
            [scope] if isinstance(scope, Session) else self.sessions
        )
        # All session-level rows precede all sample-level rows, not interleaved.
        return _scope_entity_rows(kind,
            [value for session in sessions for value in getattr(session, kind)]
            + [value for session in sessions for sample in session.samples for value in getattr(sample, kind)],
        )

    def atlases(self, scope: Scope = None) -> list[Atlas]:
        scope = acquisition_scope(scope)
        if isinstance(scope, ProjectTreeGroup) and scope.processing_source is not None:
            return []
        if isinstance(scope, EntityGroup):
            return [value for value in scope.values if isinstance(value, Atlas)]
        if isinstance(scope, LinkedSampleGroup):
            return [scope.atlas] if scope.atlas is not None else []
        if isinstance(scope, Sample):
            return self.linked_atlases(scope) or ([scope.atlas] if scope.atlas is not None else [])
        if isinstance(scope, Session):
            return self.linked_atlases(scope) or display_atlases(scope)
        sessions = scope.sessions if isinstance(scope, ProjectTreeGroup) else self.sessions
        return dedupe_entities([atlas for session in sessions for atlas in display_atlases(session)], key=atlas_display_key)

    def linked_atlases(self, scope: Session | Sample) -> list[Atlas]:
        samples = scope.samples if isinstance(scope, Session) else [scope]
        return dedupe_entities(
            [atlas for sample in samples for atlas in self._atlases_by_sample.get(id(sample), ())], key=atlas_display_key,
        )

    def atlas_label(self, atlas: Atlas) -> str:
        for session in self.sessions:
            for sample in session.samples:
                if sample.atlas is atlas:
                    return display_sample_name(sample.name)
            if session.atlas is atlas:
                return f"{session.name} Atlas"
        if atlas.image_path is not None and atlas.image_path.parent.name:
            return f"Atlas - {atlas.image_path.parent.name}"
        return "Atlas"

    def viewer_atlas_label(self, atlas: Atlas) -> str:
        """Live label variant: any sample wins over roots; allow timestamp fallback."""
        for sample in self.samples:
            if sample.atlas is atlas:
                return display_sample_name(sample.name)
        for session in self.sessions:
            if session.atlas is atlas:
                return f"{session.name} Atlas"
        if atlas.image_path is not None and atlas.image_path.parent.name:
            return f"Atlas - {atlas.image_path.parent.name}"
        metadata = atlas.metadata if isinstance(atlas.metadata, dict) else {}
        timestamp = metadata.get("acquisitionDateTime") or metadata.get("created")
        return f"Atlas - {timestamp}" if timestamp else "Atlas"

    def find(self, object_id: str | None) -> Session | Sample | Entity | None:
        """Retain first-match lookup; this is not a navigation resolver."""
        return self._objects.get(object_id) if object_id is not None else None

    def find_candidates(self, object_id: str | None) -> tuple[Session | Sample | Entity, ...]:
        """All records for an ID, including collisions and Session/Sample aliases.

        Resolution belongs to the caller's canonical uniqueness rule; this
        index deliberately does not apply a counting/deduplication policy.
        """
        return self._object_candidates.get(object_id, ()) if object_id is not None else ()

    def members_for(self, value: object) -> tuple[Sample, ...]:
        members = self._owners_by_object.get(id(value))
        return members if members is not None else self._owners_by_id.get(getattr(value, "id", ""), ())

    def row_owner(self, value: object) -> tuple[int, str] | None:
        """The sample that lists ``value``, as ``(identity, header label)``, for a list's group header.

        A session-level list (single-collection or fallback layouts) is owned
        by its session, and an entity both a session and its sample list belongs
        to the sample. ``None`` for a value nothing in the workspace lists.
        """

        if not self._row_owners:
            for session in self.sessions:
                for entity in _children(session):
                    self._row_owners[id(entity)] = (id(session), session.name)
                for sample in session.samples:
                    for entity in _children(sample):
                        self._row_owners[id(entity)] = (id(sample), display_sample_name(sample.name))
        return self._row_owners.get(id(value))

    def members_for_id(self, object_id: str) -> tuple[Sample, ...]:
        """ID-only ownership, retaining the Atlas collection controls' rule."""
        return self._owners_by_id.get(object_id, ())

    @property
    def samples(self) -> tuple[Sample, ...]:
        return tuple(sample for session in self.sessions for sample in session.samples)

    def linked_groups_for(self, sessions: Sequence[Session]) -> tuple[tuple[Sample, ...], ...]:
        """Keep link inference scoped to exactly the requested session set.

        The full project uses its cached groups. A subset can change ambiguity
        and fallback matches, so it must use the linker's original subset rule.
        """
        if tuple(map(id, sessions)) == tuple(map(id, self.sessions)):
            return self.sample_groups
        return tuple(tuple(group) for group in linked_sample_groups(list(sessions)))

    @staticmethod
    def sample_contains(sample: Sample, value: object) -> bool:
        return value is sample or value is sample.atlas or any(value in getattr(sample, kind) for kind in _LIST_KINDS)

    @staticmethod
    def session_contains(session: Session, value: object) -> bool:
        """Direct containment only, as in the legacy window helper."""
        return value is session or value is session.atlas or any(value in getattr(session, kind) for kind in _LIST_KINDS)

    def session_scope_contains(self, session: Session, value: object) -> bool:
        return self.session_contains(session, value) or any(self.sample_contains(sample, value) for sample in session.samples)

    def contains(self, scope: Scope, value: object) -> bool:
        if scope is None:
            return any(self.session_scope_contains(session, value) for session in self.sessions)
        if isinstance(scope, Session):
            return self.session_scope_contains(scope, value) or value in self.linked_atlases(scope)
        if isinstance(scope, Sample):
            return self.sample_contains(scope, value) or value in self.linked_atlases(scope)
        if isinstance(scope, LinkedSampleGroup):
            return any(self.sample_contains(sample, value) for sample in scope.samples)
        if isinstance(scope, ProjectTreeGroup):
            return group_contains_value(scope, value)
        if isinstance(scope, EntityGroup):
            return value in scope.values
        return False

    @property
    def tilt_validations(self) -> GridValidations:
        """Every loaded tilt series' verdict, each against its own grid: the
        dashboard, the lists, the overlays, Context and the PDF all read these."""

        if not self._grid_validations:
            self._grid_validations.append(GridValidations(self.samples))
        return self._grid_validations[0]

    def project_marker_context(self, failed_tilt_ids: frozenset[str]) -> MarkerContext:
        """Every overlayable item in the project: the fallback context for an
        entity no sample group owns. Built once per failed-tilt set."""

        context = self._project_marker_contexts.get(failed_tilt_ids)
        if context is None:
            context = MarkerContext(
                overviews=tuple(self.entities(EntityKind.OVERVIEW)),
                search_maps=tuple(self.entities(EntityKind.SEARCH_MAP)),
                batch_positions=tuple(self.entities(EntityKind.BATCH_POSITION)),
                tilt_series=tuple(self.entities(EntityKind.TILT_SERIES)),
                failed_tilt_ids=failed_tilt_ids,
                grid_validations=self.tilt_validations,
            )
            self._project_marker_contexts[failed_tilt_ids] = context
        return context

    def marker_context(self, value: object, fallback: MarkerContext) -> MarkerContext:
        """Return a MarkerContext scoped to ``value``'s linked-sample group.

        Falls back to ``fallback`` when no parent group is known — typical
        for session-level (top-level) entities that don't sit under a
        ``Sample`` in the parsed tree.

        A group's context is built once and then shared, so its tilt series
        are validated once however many images are drawn from it.

        Logs a debug breakdown of how many overlays were filtered out so
        cross-session bleed can be diagnosed quickly.
        """

        members = self.members_for(value)
        if not members:
            LOGGER.debug(
                "overlay context: no parent sample group for %s id=%s — using fallback (%d overviews, %d search_maps, %d batches)",
                type(value).__name__,
                getattr(value, "id", "<unknown>"),
                len(fallback.overviews),
                len(fallback.search_maps),
                len(fallback.batch_positions),
            )
            return fallback

        key = (id(members), fallback.failed_tilt_ids)
        cached = self._marker_contexts.get(key)
        if cached is not None:
            return cached

        # Aggregate items across every sample in the linked group. A linked
        # group spans sessions, so the screening sample can contribute the
        # atlas while the collection sample contributes the search maps.
        overviews: list[Overview] = []
        search_maps: list[SearchMap] = []
        batch_positions: list[BatchPosition] = []
        tilt_series: list[TiltSeries] = []
        for sample in members:
            overviews.extend(sample.overviews)
            search_maps.extend(sample.search_maps)
            batch_positions.extend(sample.batch_positions)
            tilt_series.extend(sample.tilt_series)

        scoped = MarkerContext(
            overviews=tuple(overviews),
            search_maps=tuple(search_maps),
            batch_positions=tuple(batch_positions),
            tilt_series=tuple(tilt_series),
            failed_tilt_ids=fallback.failed_tilt_ids,
            grid_validations=self.tilt_validations,
        )
        self._marker_contexts[key] = scoped

        if LOGGER.isEnabledFor(logging.DEBUG):
            group_label = " + ".join(sorted({sample.name for sample in members}))
            hidden_ovs = len(fallback.overviews) - len(scoped.overviews)
            hidden_sm = len(fallback.search_maps) - len(scoped.search_maps)
            hidden_bp = len(fallback.batch_positions) - len(scoped.batch_positions)
            LOGGER.debug(
                "overlay context: scoped to linked group [%s] for %s id=%s "
                "(visible: ov=%d sm=%d bp=%d, hidden: ov=%d sm=%d bp=%d)",
                group_label,
                type(value).__name__,
                getattr(value, "id", "<unknown>"),
                len(scoped.overviews),
                len(scoped.search_maps),
                len(scoped.batch_positions),
                hidden_ovs,
                hidden_sm,
                hidden_bp,
            )
            scoped_ov_ids = {o.id for o in scoped.overviews}
            scoped_sm_ids = {s.id for s in scoped.search_maps}
            for overview in fallback.overviews:
                if overview.id not in scoped_ov_ids:
                    parent = self._owners_by_id.get(overview.id)
                    parent_label = " + ".join(sorted({s.name for s in parent})) if parent else "<unknown>"
                    LOGGER.debug(
                        "overlay filtered: type=Overview name=%s parent_group=%s active_group=%s reason=different_linked_group",
                        getattr(overview, "name", "<unknown>"),
                        parent_label,
                        group_label,
                    )
            for search_map in fallback.search_maps:
                if search_map.id not in scoped_sm_ids:
                    parent = self._owners_by_id.get(search_map.id)
                    parent_label = " + ".join(sorted({s.name for s in parent})) if parent else "<unknown>"
                    LOGGER.debug(
                        "overlay filtered: type=SearchMap name=%s parent_group=%s active_group=%s reason=different_linked_group",
                        getattr(search_map, "name", "<unknown>"),
                        parent_label,
                        group_label,
                    )
        return scoped
