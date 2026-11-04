"""Revision-owned placement using only resolved, unique acquisition parents."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from tomography_session_browser.domain.identity import entity_key_text, path_identity_key
from tomography_session_browser.domain.models import Sample, Session, TiltSeries
from tomography_session_browser.domain.tomograms import ProcessingSource, Tomogram
from tomography_session_browser.presentation.project_tree import ProjectTreeGroup, session_key, display_samples
from tomography_session_browser.services.tomogram_links import TomogramLink

if TYPE_CHECKING:
    from tomography_session_browser.presentation.workspace import Workspace


@dataclass(frozen=True, slots=True)
class TomogramPlacement:
    rows: tuple[Tomogram, ...]
    by_parent: Mapping[str, tuple[Tomogram, ...]]
    owner_by_row: Mapping[str, Sample | Session]
    residual_by_source: Mapping[str, tuple[Tomogram, ...]]

    @classmethod
    def build(cls, workspace: Workspace) -> TomogramPlacement:
        from tomography_session_browser.presentation.workspace import EntityKind
        owners: dict[str, dict[str, Sample | Session]] = {}
        for session in workspace.sessions:
            sample_owners: dict[str, dict[str, Sample]] = {}
            samples = display_samples(session, workspace)
            for sample in samples:
                for tilt in sample.tilt_series:
                    sample_owners.setdefault(entity_key_text(tilt), {})[path_identity_key(sample.path)] = sample
            for tilt in workspace.entities(EntityKind.TILT_SERIES, session):
                key = entity_key_text(tilt)
                candidates = sample_owners.get(key)
                # With displayed sample rows, a session-only record has no
                # represented sample owner. Keep its tomograms residual.
                if not candidates and samples:
                    continue
                for owner in candidates.values() if candidates else (session,):
                    owner_key = ('sample:' + path_identity_key(owner.path)) if isinstance(owner, Sample) else 'session:' + session_key(owner)
                    owners.setdefault(key, {})[owner_key] = owner
        rows = tuple({row.id: row for source in workspace.processing_sources for row in source.tomograms}.values())
        parents: dict[str, list[Tomogram]] = {}
        placed: dict[str, Sample | Session] = {}
        residual: dict[str, list[Tomogram]] = {source.id: [] for source in workspace.processing_sources}
        for row in rows:
            link = workspace.tomogram_links.get(row.id)
            candidates = owners.get(link.target_key or '', {}) if link and link.status == 'resolved' else {}
            if len(candidates) == 1 and link is not None:
                placed[row.id] = next(iter(candidates.values()))
                parents.setdefault(link.target_key or '', []).append(row)
            else:
                residual[row.source_id].append(row)
        return cls(rows, MappingProxyType({key: tuple(values) for key, values in parents.items()}),
                   MappingProxyType(placed), MappingProxyType({key: tuple(values) for key, values in residual.items()}))

    def for_parents(self, parents: Sequence[TiltSeries]) -> tuple[Tomogram, ...]:
        keys = {row.id for parent in parents for row in self.by_parent.get(entity_key_text(parent), ())}
        return tuple(row for row in self.rows if row.id in keys)

    def residual_groups(self, sources: Sequence[ProcessingSource], names: Mapping[str, str]) -> tuple[ProjectTreeGroup, ...]:
        groups = []
        for source in sources:
            if source.tomograms and not self.residual_by_source[source.id]:
                continue
            key = 'processing:' + source.id
            groups.append(ProjectTreeGroup(key, 'processing', source.name, names.get(key, source.name), [],
                warnings=list(source.warnings), name_lookup_keys=(key,), processing_source=source))
        return tuple(groups)
