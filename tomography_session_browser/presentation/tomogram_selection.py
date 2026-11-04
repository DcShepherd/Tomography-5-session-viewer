"""Tomogram review scopes. Membership is resolved by the workspace revision."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from tomography_session_browser.domain.models import Sample, Session
from tomography_session_browser.presentation.project_tree import ProjectTreeGroup, group_contains_value
from tomography_session_browser.presentation.scopes import LinkedSampleGroup
from tomography_session_browser.presentation.tomogram_versions import review_versions

if TYPE_CHECKING:
    from tomography_session_browser.presentation.workspace import Scope, Workspace

AcquisitionScope = Session | Sample | LinkedSampleGroup | ProjectTreeGroup | None


@dataclass(frozen=True, slots=True)
class AllTomograms:
    scope: AcquisitionScope
    version: str | None = None


@dataclass(frozen=True, slots=True)
class FolderTomograms:
    scope: AcquisitionScope
    source_id: str
    version: str | None = None


TomogramSelection = AllTomograms | FolderTomograms


def acquisition_scope(scope: Scope) -> Scope:
    return scope.scope if isinstance(scope, AllTomograms | FolderTomograms) else scope


#: The defined order of review kinds: the tree lists versions in it, and a
#: Tomograms category or folder row opens the first kind it holds.
REVIEW_VERSION_ORDER = ('Raw', 'Deconvolved', 'Denoised')


@dataclass(frozen=True, slots=True)
class TreeRowRefusal:
    reason: str


def versions_in(workspace: Workspace, selection: TomogramSelection) -> tuple[str, ...]:
    names = {volume.version for row in workspace.tomograms(selection) for volume in review_versions(row)}
    return (tuple(name for name in REVIEW_VERSION_ORDER if name in names)
            + tuple(sorted(names.difference(REVIEW_VERSION_ORDER))))


def tree_row_destination(workspace: Workspace, selection: TomogramSelection) -> TomogramSelection | TreeRowRefusal:
    """What a Tomograms row of the project tree opens in the Tomograms tab.

    A version row opens that version. A category or folder row opens one kind,
    the first of ``REVIEW_VERSION_ORDER`` its tomograms hold: Raw, else
    Deconvolved, else Denoised. Other kinds are never chosen in their place: a
    row holding only those opens as it is. A row with no tomograms opens nothing.
    """
    if not workspace.tomograms(selection):
        return TreeRowRefusal(selection_note(workspace, selection) or 'This scope has no tomograms to open.')
    if selection.version is not None:
        return selection
    present = versions_in(workspace, selection)
    kind = next((name for name in REVIEW_VERSION_ORDER if name in present), None)
    return selection if kind is None else replace(selection, version=kind)


def selection_note(workspace: Workspace, selection: TomogramSelection) -> str:
    if isinstance(selection, FolderTomograms) and not any(source.id == selection.source_id for source in workspace.processing_sources):
        label = selection.source_id.replace('\\', '/').rsplit('/', 1)[-1]
        return f'{label} is no longer loaded.'
    if selection.version and selection.version not in versions_in(workspace, selection):
        return f'{selection.version} is no longer in this folder.'
    return ''


def rebound_selection(workspace: Workspace, selection: TomogramSelection) -> TomogramSelection | None:
    """A missing acquisition disappears; a missing folder stays an empty current view.

    Keep an unavailable version until preparation has produced its explanatory
    note. Publication drops that version at the same level, never to a sibling.
    """
    from tomography_session_browser.presentation.scope_identity import resolve_scope_key, scope_key
    candidates = [*workspace.sessions, *workspace.samples, *workspace.project_groups]
    if isinstance(selection.scope, LinkedSampleGroup):
        from tomography_session_browser.presentation.scopes import build_linked_sample_group
        candidates.extend(build_linked_sample_group(selection.scope.label, list(samples)) for samples in workspace.sample_groups)
    parent = resolve_scope_key(candidates, scope_key(selection.scope))
    if parent is None and selection.scope is not None:
        return None
    return replace(selection, scope=parent)

@dataclass(frozen=True, slots=True)
class TomogramLocation:
    """Identifiers only for navigation history; None source means all folders."""
    acquisition_key: str
    source_id: str | None
    version: str | None

    @classmethod
    def capture(cls, selection: TomogramSelection) -> TomogramLocation:
        from tomography_session_browser.presentation.scope_identity import scope_key
        return cls(scope_key(selection.scope), selection.source_id if isinstance(selection, FolderTomograms) else None,
                   selection.version)

    def resolve(self, workspace: Workspace, candidates) -> TomogramSelection | None:
        from tomography_session_browser.presentation.scope_identity import resolve_scope_key
        parent = resolve_scope_key(candidates, self.acquisition_key)
        if parent is not None and not isinstance(parent, Session | Sample | LinkedSampleGroup | ProjectTreeGroup):
            return None
        if parent is None and self.acquisition_key != 'none':
            return None
        if self.source_id is not None:
            if not any(source.id == self.source_id for source in workspace.processing_sources):
                return None
            return FolderTomograms(parent, self.source_id, self.version)
        return AllTomograms(parent, self.version)


def initial_source_scope(workspace: Workspace, source_id: str) -> AcquisitionScope | AllTomograms:
    """Choose the newly loaded source's extant row after link preparation.

    A linked source lands on the one project group that holds its owners, so
    a load opens on that project's Summary (not on Tomograms).
    """
    residual = [group for group in workspace.project_groups if group.processing_source and group.processing_source.id == source_id]
    if len(residual) == 1:
        return residual[0]
    owners = {id(owner): owner for row in workspace.tomogram_placement.rows if row.source_id == source_id
              and (owner := workspace.tomogram_placement.owner_by_row.get(row.id)) is not None}
    groups = [group for group in workspace.project_groups
              if any(group_contains_value(group, owner) for owner in owners.values())]
    if len(groups) == 1:
        return groups[0]
    return AllTomograms(next(iter(owners.values()))) if len(owners) == 1 else AllTomograms(None)


def loaded_scope(workspace: Workspace, scope: Scope) -> Scope:
    """A worker may establish placement that retires the intake's residual row."""
    if (isinstance(scope, ProjectTreeGroup) and scope.processing_source is not None
            and not any(group.key == scope.key for group in workspace.project_groups)):
        return initial_source_scope(workspace, scope.processing_source.id)
    return scope
