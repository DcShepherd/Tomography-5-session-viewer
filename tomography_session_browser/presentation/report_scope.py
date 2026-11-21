"""What a PDF report covers: the reviewer's selection, normalised.

``ReportScopeDialog`` (``ui/report_scope_dialog.py``) collects a
``ReportScopeRequest``; ``normalise_report_scope`` turns it into the
``ReportScope`` the report generator draws; ``presentation/report_preview.py``
describes that scope in the dialog. A full atlas overview is in the report
only when its AT row is ticked; each dataset shows its linked atlas image
either way. Qt-free, so it is tested without a window.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.models import Sample, Session
from tomography_session_browser.presentation.report_datasets import has_collection_content
from tomography_session_browser.presentation.report_tomograms import (
    ReportTomograms, ScopedTomograms, member_slice_plans, select_report_tomograms,
)
from tomography_session_browser.presentation.tomogram_selection import FolderTomograms
from tomography_session_browser.presentation.tomogram_slices import SliceRequest
from tomography_session_browser.presentation.project_tree import (
    ProjectTreeGroup,
    project_group_badge,
    session_key,
)


@dataclass(frozen=True, slots=True)
class ProjectReportGroup:
    display_name: str
    automatic_name: str
    kind: str
    session_names: tuple[str, ...]
    session_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReportScopeRequest:
    group_keys: frozenset[str]
    session_keys: frozenset[str]
    sample_keys: frozenset[str] = frozenset()
    include_exposure_thumbnails: bool = False
    #: The remembered preferences (T and S in D4 §5.1), not effective inclusion.
    include_tomograms: bool = False
    include_slices: bool = False
    #: Ticked processing folders. Ticks filter tomogram rows; they never change links.
    processing_source_ids: frozenset[str] = frozenset()

    @property
    def has_acquisition(self) -> bool:
        return bool(self.group_keys or self.session_keys or self.sample_keys)

    @property
    def has_selection(self) -> bool:
        return self.has_acquisition or bool(self.processing_source_ids)


@dataclass(frozen=True, slots=True)
class ReportScope:
    sessions: tuple[Session, ...]
    project_title: str
    project_groups: tuple[ProjectReportGroup, ...]
    default_filename: str
    atlas_context_sessions: tuple[Session, ...] = ()
    #: Add an image of each tilt series' exposure area from its Thumbnails stack.
    include_exposure_thumbnails: bool = False
    #: Where the Save dialog starts, before the output-boundary check.
    default_directory: Path | None = None
    #: The reduced tomogram snapshot; None only when no snapshot exists.
    tomograms: ScopedTomograms | None = None
    include_tomograms: bool = False
    include_slices: bool = False

    @property
    def folder_report(self) -> bool:
        return not self.sessions and self.tomograms is not None

    @property
    def prints_tomograms(self) -> bool:
        """The D4 §5.1 table: a folder report always; a session report when T is on and there is something to print."""
        if self.tomograms is None:
            return False
        return self.folder_report or (self.include_tomograms and self.tomograms.reportable)

    @property
    def tomograms_available(self) -> bool:
        """Whether the tomogram option has anything to print, whatever T says."""
        return self.tomograms is not None and self.tomograms.reportable

    @property
    def slices_available(self) -> bool:
        """Whether at least one included row can have an image."""
        return (self.tomograms is not None and (self.folder_report or self.include_tomograms)
                and any(isinstance(plan, SliceRequest) for plan in member_slice_plans(self.tomograms).values()))

    @property
    def prints_slices(self) -> bool:
        return self.prints_tomograms and self.include_slices and self.slices_available


#: The first preview line of a folder report (plan Q8 A): one tick can change the
#: report type, so the change is said, not made silently.
FOLDER_REPORT_LINE = "Only processing folders are ticked. This makes a folder report."


def preset_folders(review_scope: object, report_scope: object, snapshot: ReportTomograms | None) -> frozenset[str]:
    """The folders the dialog ticks when it opens (D4 §5.1 folder presets).

    A ``FolderTomograms`` review scope ticks its folder; a residual processing
    group ticks its folder (and no acquisition); every other scope ticks all
    loaded folders. Ticks are not remembered.
    """
    if snapshot is None:
        return frozenset()
    loaded = frozenset(source.source.id for source in snapshot.sources)
    if isinstance(review_scope, FolderTomograms) and review_scope.source_id in loaded:
        return frozenset({review_scope.source_id})
    if isinstance(report_scope, ProjectTreeGroup) and report_scope.processing_source is not None             and report_scope.processing_source.id in loaded:
        return frozenset({report_scope.processing_source.id})
    return loaded


def normalise_report_scope(
    sessions: Iterable[Session],
    groups: Iterable[ProjectTreeGroup],
    request: ReportScopeRequest,
    snapshot: ReportTomograms | None = None,
) -> ReportScope:
    """The one reduction: acquisition selection, then the tomogram snapshot when one exists."""
    loaded_sessions = list(sessions)
    if snapshot is not None and not request.has_acquisition and request.processing_source_ids:
        return _folder_report_scope(loaded_sessions, request, snapshot)
    groups_by_key = {group.key: group for group in groups}
    sessions_by_key = {session_key(session): session for session in loaded_sessions}
    selected_session_ids: set[int] = set()
    selected_samples_by_session: dict[int, list[Sample]] = {}
    explicit_labels: list[str] = []
    report_groups: list[ProjectReportGroup] = []

    for group in groups:
        if group.key not in request.group_keys:
            continue
        group_sessions = [session for session in group.sessions if session_key(session) in sessions_by_key]
        if group.kind == "linked":
            # A linked group's tick means its collections; its atlas only when the AT row is ticked.
            group_sessions = [
                session for session in group_sessions
                if _is_collection_session(session) or session_key(session) in request.session_keys
            ]
        if not group_sessions:
            continue
        selected_session_ids.update(id(session) for session in group_sessions)
        explicit_labels.append(group.display_name)
        report_groups.append(_project_report_group(group.display_name, group, group_sessions, use_group_kind=True))

    covered_by_group = set(selected_session_ids)
    explicit_sessions = [session for session in loaded_sessions
                         if session_key(session) in request.session_keys and id(session) not in covered_by_group]
    selected_session_ids.update(id(session) for session in explicit_sessions)
    for session in loaded_sessions:
        if id(session) in selected_session_ids:
            continue
        selected_samples = [
            sample
            for sample in session.samples
            if sample_key(session, sample) in request.sample_keys
        ]
        if selected_samples:
            selected_samples_by_session[id(session)] = selected_samples

    # A ticked atlas joins the group of each ticked collection or sample of its own
    # linked group (plan B5, Q6): the report keeps the dataset's name and the cover
    # lists one group, as the removed supporting-overview option gave them. An atlas
    # ticked with no dataset of its group is an atlas-only report, as before.
    def linked_atlas(session: Session) -> Session | None:
        group = _group_for_session(groups_by_key.values(), session)
        atlas = group.atlas_session if group is not None and _is_collection_session(session) else None
        return atlas if atlas is not None and any(atlas is chosen for chosen in explicit_sessions) else None

    datasets = [session for session in explicit_sessions if _is_collection_session(session)]
    datasets += [session for session in loaded_sessions if id(session) in selected_samples_by_session]
    joined = {id(atlas) for atlas in map(linked_atlas, datasets) if atlas is not None}

    explicit_labels.extend(session.name or session.path.name for session in explicit_sessions if id(session) not in joined)
    for session in loaded_sessions:
        explicit_labels.extend(sample.name or sample.path.name for sample in selected_samples_by_session.get(id(session), ()))

    for session in explicit_sessions:
        if id(session) in joined:
            continue
        atlas = linked_atlas(session)
        report_groups.append(
            _project_report_group(
                session.name or session.path.name,
                _group_for_session(groups_by_key.values(), session),
                [atlas, session] if atlas is not None else [session],
                fallback_kind="AT" if session.kind == SessionKind.ATLAS_SCREENING else "DC",
            )
        )

    for session in loaded_sessions:
        selected_samples = selected_samples_by_session.get(id(session))
        if not selected_samples:
            continue
        atlas = linked_atlas(session)
        filtered_session = _filtered_session_for_samples(session, selected_samples)
        report_groups.append(
            _project_report_group(
                session.name or session.path.name,
                _group_for_session(groups_by_key.values(), session),
                [atlas, filtered_session] if atlas is not None else [filtered_session],
                fallback_kind="DC",
            )
        )

    selected_sessions_list: list[Session] = []
    for session in loaded_sessions:
        if id(session) in selected_session_ids:
            selected_sessions_list.append(session)
            continue
        selected_samples = selected_samples_by_session.get(id(session))
        if selected_samples:
            selected_sessions_list.append(_filtered_session_for_samples(session, selected_samples))
    selected_sessions = tuple(selected_sessions_list)
    if not selected_sessions:
        raise ValueError("Select at least one reportable session or processing folder.")

    if len(explicit_labels) == 1:
        title = f"Report: {explicit_labels[0]}"
        filename_label = explicit_labels[0]
    else:
        title = "Report: Selected sessions"
        filename_label = "selected_sessions"

    return ReportScope(
        sessions=selected_sessions,
        project_title=title,
        project_groups=tuple(_dedupe_project_report_groups(report_groups)),
        default_filename=f"{_safe_filename(filename_label)}_report.pdf",
        atlas_context_sessions=tuple(loaded_sessions),
        include_exposure_thumbnails=request.include_exposure_thumbnails,
        default_directory=selected_sessions[0].path.parent,
        tomograms=(select_report_tomograms(snapshot, selected_sessions, request.processing_source_ids)
                   if snapshot is not None else None),
        include_tomograms=request.include_tomograms,
        include_slices=request.include_slices,
    )


def _folder_report_scope(loaded_sessions: list[Session], request: ReportScopeRequest,
                         snapshot: ReportTomograms) -> ReportScope:
    """A report with no sessions: every row of each ticked processing folder."""
    scoped = select_report_tomograms(snapshot, (), request.processing_source_ids)
    if not scoped.sources:
        raise ValueError("Select at least one reportable session or processing folder.")
    labels = [source.label for source in scoped.sources]
    title_label, filename_label = (labels[0], labels[0]) if len(labels) == 1 else ("Selected folders", "selected_folders")
    return ReportScope(
        sessions=(),
        project_title=f"Report: {title_label}",
        project_groups=(),
        default_filename=f"{_safe_filename(filename_label)}_report.pdf",
        atlas_context_sessions=tuple(loaded_sessions),
        default_directory=scoped.sources[0].source.path.parent,
        tomograms=scoped,
        include_tomograms=request.include_tomograms,
        include_slices=request.include_slices,
    )


def sample_key(session: Session, sample: Sample) -> str:
    return f"{session_key(session)}::sample::{sample.id or str(sample.path)}"


def _filtered_session_for_samples(session: Session, samples: list[Sample]) -> Session:
    return replace(session, samples=list(samples))


def reportable_samples(session: Session) -> list[Sample]:
    """The real samples the dialog can tick (no synthetic session-level sample)."""
    if session.kind == SessionKind.ATLAS_SCREENING:
        return []
    return [sample for sample in session.samples if has_collection_content(sample)]


def _group_for_session(groups: Iterable[ProjectTreeGroup], session: Session) -> ProjectTreeGroup | None:
    for group in groups:
        if any(candidate is session for candidate in group.sessions):
            return group
    return None


def _project_report_group(
    display_name: str,
    group: ProjectTreeGroup | None,
    sessions: list[Session],
    *,
    fallback_kind: str = "DC",
    use_group_kind: bool = False,
) -> ProjectReportGroup:
    return ProjectReportGroup(
        display_name=display_name,
        automatic_name=group.automatic_name if group is not None else display_name,
        kind=project_group_badge(group) if group is not None and use_group_kind else fallback_kind,
        session_names=tuple(session.name for session in sessions),
        session_paths=tuple(str(session.path) for session in sessions),
    )


def _dedupe_project_report_groups(groups: Iterable[ProjectReportGroup]) -> list[ProjectReportGroup]:
    result: list[ProjectReportGroup] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for group in groups:
        key = (group.display_name, group.session_paths)
        if key in seen:
            continue
        seen.add(key)
        result.append(group)
    return result


def _is_collection_session(session: Session) -> bool:
    return session.kind != SessionKind.ATLAS_SCREENING


def _safe_filename(label: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", label.strip()).strip("._")
    return cleaned or "session"
