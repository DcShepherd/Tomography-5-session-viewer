"""Immutable review membership, independent of whatever volume the viewer shows."""
from __future__ import annotations

from dataclasses import dataclass

from tomography_session_browser.domain.tomograms import ProcessingSource, Tomogram, Volume
from tomography_session_browser.presentation.scope_identity import scope_key
from tomography_session_browser.presentation.tomogram_selection import AllTomograms, FolderTomograms
from tomography_session_browser.presentation.tomogram_tabs import TomogramRow, needs_attention, prepare_tomogram_tab, sorted_within_groups
from tomography_session_browser.presentation.tomogram_versions import review_versions
from tomography_session_browser.presentation.workspace import Scope, Workspace
from tomography_session_browser.services.tomogram_links import TomogramLink
from tomography_session_browser.presentation.tomogram_review_state import RowReview, TomogramReviewState
from tomography_session_browser.presentation.dashboard.tag_summary import TagContent
from tomography_session_browser.services.tomogram_tags.binding import ProjectKey


@dataclass(frozen=True, slots=True)
class SummaryTomogram:
    tomogram: Tomogram
    link: TomogramLink
    group: str
    volume: Volume | None
    review: RowReview = RowReview()
    tags: TagContent | None = None


@dataclass(frozen=True, slots=True)
class TomogramSummaryInput:
    key: str
    version: str | None
    sort: str
    rows: tuple[SummaryTomogram, ...]
    sources: tuple[ProcessingSource, ...]
    notice: str = ''
    title: str = 'Tomograms'
    folder_projects: tuple[ProjectKey | None, ...] = ()
    tags_unsaved: bool = False
    #: Volume id and the value the user stated, for the versions in these rows.
    stated: tuple[tuple[str, float], ...] = ()


def attention_keys(payload: TomogramSummaryInput) -> frozenset[str]:
    stated = dict(payload.stated)
    return frozenset(row.tomogram.id for row in payload.rows
                     if needs_attention(TomogramRow(row.tomogram, row.group, row.link), lambda volume: stated.get(volume.id)))


def summary_input(workspace: Workspace, scope: Scope, sort: str = 'Name',
                  review_state: TomogramReviewState | None = None) -> TomogramSummaryInput:
    payload = prepare_tomogram_tab(workspace, scope)
    selection = payload.selection
    version = selection.version if selection else None
    from tomography_session_browser.presentation.project_tree import ProjectTreeGroup
    title = 'Tomograms'
    if isinstance(scope, FolderTomograms):
        source = next((source for source in payload.sources if source.id == scope.source_id), None)
        title = workspace.group_names.get('processing:' + source.id, source.name) if source else 'Processing folder'
    elif isinstance(scope, ProjectTreeGroup) and scope.processing_source is not None:
        title = scope.display_name
    if version:
        title += ' · ' + version
    result = TomogramSummaryInput(scope_key(selection or scope), version, sort,
        tuple(SummaryTomogram(row.tomogram, row.link, row.group,
                              next((volume for volume in review_versions(row.tomogram) if volume.version == version), None))
              for row in sorted_within_groups(list(payload.rows), sort)), payload.sources, payload.notice, title)
    return with_review(result, review_state) if review_state is not None else result


def with_review(payload: TomogramSummaryInput, state: TomogramReviewState) -> TomogramSummaryInput:
    from dataclasses import replace
    sources = {source.id: source for source in payload.sources}
    return replace(payload, rows=tuple(replace(row, review=state.row(row.tomogram, sources[row.tomogram.source_id]))
                                       for row in payload.rows),
                   stated=tuple((volume.id, value) for row in payload.rows for volume in row.tomogram.volumes
                                if (value := state.stated_for(volume)) is not None))


def tomogram_dashboard_model(workspace: Workspace, selection: AllTomograms | FolderTomograms):
    from dataclasses import replace
    from tomography_session_browser.presentation.dashboard.dashboard import session_dashboard_model
    from tomography_session_browser.presentation.dashboard.scope_value import dashboard_scope_value
    from tomography_session_browser.presentation.dashboard.summary import summary_from_input
    payload = summary_input(workspace, selection)
    base = session_dashboard_model(dashboard_scope_value(workspace, selection.scope, workspace.atlas_label), workspace=workspace)
    return replace(base, title=payload.title, sample_label='', kind='processing', path='', summary=summary_from_input(payload))
