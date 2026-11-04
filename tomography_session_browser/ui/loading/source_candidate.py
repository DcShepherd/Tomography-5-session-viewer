"""Build a candidate from either acquisition or processing data, explicitly."""
from __future__ import annotations

from collections.abc import Mapping

from tomography_session_browser.domain.identity import path_identity_key
from tomography_session_browser.domain.models import Session
from tomography_session_browser.domain.tomograms import ProcessingSource
from tomography_session_browser.presentation.workspace import Workspace, Scope
from tomography_session_browser.presentation.project_tree import folds
from tomography_session_browser.presentation.tomogram_selection import AllTomograms, FolderTomograms, initial_source_scope


def source_candidate(current: Workspace, source: Session | ProcessingSource, replace: bool,
                     names: Mapping[str, str], selected_scope: Scope = None) -> tuple[Workspace, Scope]:
    sessions = [] if replace else list(current.sessions)
    processing = [] if replace else list(current.processing_sources)
    if isinstance(source, Session):
        sessions = [item for item in sessions if path_identity_key(item.path) != path_identity_key(source.path)]
        sessions.append(source)
    else:
        processing = [item for item in processing if item.id != source.id]
        processing.append(source)
    workspace = Workspace.build(sessions, names, processing_sources=processing,
                                link_confirmations=current.link_confirmations)
    scope = next((group for group in workspace.project_groups
                  if group.processing_source is source or any(item is source for item in group.sessions)), None)
    if not replace and isinstance(selected_scope, AllTomograms | FolderTomograms):
        rebound = workspace.rebind_scope(selected_scope)
        if rebound is not None:
            return workspace, rebound
    if isinstance(source, ProcessingSource) and scope is None:
        return workspace, initial_source_scope(workspace, source.id)
    if isinstance(source, Session) and folds(source, workspace) and scope is not None and scope.kind == 'collection':
        return workspace, source.samples[0]
    return workspace, scope
