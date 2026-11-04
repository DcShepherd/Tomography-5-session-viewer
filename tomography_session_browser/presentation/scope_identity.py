"""Navigation and dashboard identities, shared only where their meaning agrees."""
from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from tomography_session_browser.domain.identity import entity_key_text
from tomography_session_browser.domain.models import Atlas, BatchPosition, Overview, Sample, SearchMap, SearchTile, Session, TiltSeries
from tomography_session_browser.presentation.dashboard.models import DashboardEntityScope, LinkedAtlasDashboardScope
from tomography_session_browser.presentation.project_tree import ProjectTreeGroup, session_key
from tomography_session_browser.presentation.scopes import EntityGroup, LinkedSampleGroup
from tomography_session_browser.presentation.tomogram_selection import AllTomograms, FolderTomograms

if TYPE_CHECKING:
    from tomography_session_browser.presentation.workspace import Scope


def _sample_path(sample: Sample) -> str:
    return str(sample.path).replace('\\', '/').rstrip('/').casefold()


def scope_key(value: object) -> str:
    if isinstance(value, AllTomograms):
        return 'tomograms:' + scope_key(value.scope) + ':all:' + (value.version or '-')
    if isinstance(value, FolderTomograms):
        return 'tomograms:' + scope_key(value.scope) + ':folder:' + value.source_id + ':' + (value.version or '-')
    if value is None:
        return 'none'
    if isinstance(value, Session):
        return 'Session:' + session_key(value)
    if isinstance(value, Sample):
        return 'Sample:' + _sample_path(value)
    if isinstance(value, LinkedSampleGroup):
        return 'linked:' + '|'.join(sorted(_sample_path(sample) for sample in value.samples))
    if isinstance(value, ProjectTreeGroup):
        return 'group:' + value.key
    if isinstance(value, EntityGroup):
        # Lengths make labels and child keys unambiguous even when they contain separators.
        parts = [value.label, *(entity_key_text(child) for child in value.values)]
        return 'entity-group:' + ''.join(f'{len(part)}:{part}' for part in parts)
    if isinstance(value, Atlas | Overview | SearchMap | SearchTile | BatchPosition | TiltSeries):
        return entity_key_text(value)
    raise TypeError(f'Unsupported navigation scope: {type(value).__name__}')


def resolve_scope_key(candidates: Iterable[Scope], key: str) -> Scope:
    matches = [candidate for candidate in candidates if scope_key(candidate) == key]
    return matches[0] if len(matches) == 1 else None


def dashboard_key(value: object) -> str:
    if isinstance(value, AllTomograms | FolderTomograms):
        return scope_key(value)
    if isinstance(value, Session):
        return 'session:' + session_key(value)
    if isinstance(value, Sample | Atlas | Overview | SearchMap | SearchTile | BatchPosition | TiltSeries):
        return entity_key_text(value)
    if isinstance(value, LinkedSampleGroup):
        return 'linked-sample:' + ','.join(dashboard_key(sample) for sample in value.samples)
    if isinstance(value, ProjectTreeGroup):
        return 'project-group:' + value.key
    if isinstance(value, list):
        return 'sessions:' + ','.join(dashboard_key(session) for session in value)
    if isinstance(value, LinkedAtlasDashboardScope):
        atlas_keys = ','.join(dashboard_key(atlas) for _label, atlas in value.atlases)
        return f'linked-atlas:{dashboard_key(value.source)}:{atlas_keys}'
    if isinstance(value, DashboardEntityScope):
        tilts = ','.join(dashboard_key(tilt) for tilt in value.tilt_series)
        batches = ','.join(dashboard_key(batch) for batch in value.batch_positions)
        maps = ','.join(dashboard_key(search_map) for search_map in value.search_maps)
        return f'entity:{dashboard_key(value.source)}:{maps}:{batches}:{tilts}'
    raise TypeError(f'Unsupported dashboard value: {type(value).__name__}')
