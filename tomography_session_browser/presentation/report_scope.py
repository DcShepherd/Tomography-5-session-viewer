"""What a PDF report covers: the reviewer's selection, normalised.

``ReportScopeDialog`` (``ui/report_scope_dialog.py``) collects a
``ReportScopeRequest``; ``normalise_report_scope`` turns it into the
``ReportScope`` the report generator draws, adding the supporting Atlas
sessions a linked collection needs. Qt-free, so it is tested without a window.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.models import Sample, Session
from tomography_session_browser.presentation.report_datasets import has_collection_content
from tomography_session_browser.presentation.report_tomograms import (
    ReportTomograms, ScopedTomograms, member_slice_plans, select_report_tomograms, tomogram_preview_lines,
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
    include_atlas_overview: bool = False
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
    supporting_session_keys: frozenset[str] = frozenset()
    atlas_context_sessions: tuple[Session, ...] = ()
    include_atlas_overview: bool = False
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


def describe_report_scope(scope: "ReportScope") -> str:
    """Describe what the report will actually contain.

    Built from the *normalised* scope rather than from the checkboxes, because
    normalisation adds supporting Atlas sessions the user never ticked. A
    preview derived from the selection would quietly under-report what the PDF
    is about to include.
    """

    if scope.folder_report:
        assert scope.tomograms is not None
        return "\n".join(tomogram_preview_lines(scope.tomograms, scope.prints_slices))
    if not scope.sessions:
        return "Nothing selected"

    atlas_sessions = [
        session for session in scope.sessions if session.kind == SessionKind.ATLAS_SCREENING
    ]
    supporting_atlas_sessions = [
        session
        for session in atlas_sessions
        if session_key(session) in scope.supporting_session_keys
    ]
    selected_atlas_sessions = [
        session
        for session in atlas_sessions
        if session_key(session) not in scope.supporting_session_keys
    ]
    collection_sessions = [
        session for session in scope.sessions if session.kind != SessionKind.ATLAS_SCREENING
    ]
    samples = sum(len(reportable_samples(session)) for session in collection_sessions)
    session_level = sum(not reportable_samples(session) for session in collection_sessions)

    parts: list[str] = []
    if samples:
        parts.append(count_phrase(samples, "collection sample"))
    if session_level:
        parts.append(count_phrase(session_level, "collection session"))
    if selected_atlas_sessions:
        parts.append(count_phrase(len(selected_atlas_sessions), "Atlas session"))
    if supporting_atlas_sessions:
        # Named explicitly: these are pulled in by normalisation, so a reader
        # who did not tick them should still see them coming.
        parts.append(count_phrase(len(supporting_atlas_sessions), "supporting Atlas session"))
    if not parts:
        parts.append(count_phrase(len(scope.sessions), "session"))
    text = "Report will include " + " and ".join(parts)
    if collection_sessions:
        text += ". Available linked atlases appear beside each dataset."
        if scope.include_exposure_thumbnails:
            text += " Each tilt-series table is followed by its exposure thumbnails."
    if scope.prints_tomograms:
        assert scope.tomograms is not None
        text = "\n".join([text, *tomogram_preview_lines(scope.tomograms, scope.prints_slices)])
    return text


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
    supporting_session_ids: set[int] = set()
    selected_samples_by_session: dict[int, list[Sample]] = {}
    explicit_labels: list[str] = []
    report_groups: list[ProjectReportGroup] = []

    for group in groups:
        if group.key not in request.group_keys:
            continue
        group_sessions = [session for session in group.sessions if session_key(session) in sessions_by_key]
        primary_sessions = group_sessions
        if group.kind == "linked":
            primary_sessions = [
                session for session in group_sessions
                if _is_collection_session(session) or session_key(session) in request.session_keys
            ]
            if request.include_atlas_overview:
                primary_ids = {id(session) for session in primary_sessions}
                supporting_session_ids.update(
                    id(session) for session in group_sessions if id(session) not in primary_ids
                )
            else:
                group_sessions = primary_sessions
        if not group_sessions:
            continue
        selected_session_ids.update(id(session) for session in primary_sessions)
        explicit_labels.append(group.display_name)
        report_groups.append(_project_report_group(group.display_name, group, group_sessions, use_group_kind=True))

    covered_by_group = set(selected_session_ids)
    explicit_sessions: list[Session] = []
    for session in loaded_sessions:
        key = session_key(session)
        if key not in request.session_keys or id(session) in covered_by_group:
            continue
        explicit_sessions.append(session)
        selected_session_ids.add(id(session))
        explicit_labels.append(session.name or session.path.name)

    for session in loaded_sessions:
        if id(session) in selected_session_ids:
            continue
        selected_samples = [
            sample
            for sample in session.samples
            if sample_key(session, sample) in request.sample_keys
        ]
        if not selected_samples:
            continue
        selected_samples_by_session[id(session)] = selected_samples
        explicit_labels.extend(sample.name or sample.path.name for sample in selected_samples)

    for session in explicit_sessions:
        group = _group_for_session(groups_by_key.values(), session)
        group_sessions = [session]
        if request.include_atlas_overview and _is_collection_session(session) and group is not None and group.atlas_session is not None:
            supporting_session_ids.add(id(group.atlas_session))
            group_sessions = [group.atlas_session, session]
        report_groups.append(
            _project_report_group(
                session.name or session.path.name,
                group,
                group_sessions,
                fallback_kind="AT" if session.kind == SessionKind.ATLAS_SCREENING else "DC",
            )
        )

    for session in loaded_sessions:
        selected_samples = selected_samples_by_session.get(id(session))
        if not selected_samples:
            continue
        group = _group_for_session(groups_by_key.values(), session)
        if request.include_atlas_overview and _is_collection_session(session) and group is not None and group.atlas_session is not None:
            supporting_session_ids.add(id(group.atlas_session))
        filtered_session = _filtered_session_for_samples(session, selected_samples)
        report_groups.append(
            _project_report_group(
                session.name or session.path.name,
                group,
                [group.atlas_session, filtered_session]
                if request.include_atlas_overview and group is not None and group.atlas_session is not None and _is_collection_session(session)
                else [filtered_session],
                fallback_kind="DC",
            )
        )

    # An Atlas the reviewer selected explicitly is selected content even when
    # a chosen collection also needs it. Supporting provenance applies only to
    # sessions introduced by normalisation.
    supporting_session_ids.difference_update(selected_session_ids)
    selected_or_supporting = selected_session_ids | supporting_session_ids
    selected_sessions_list: list[Session] = []
    for session in loaded_sessions:
        if id(session) in selected_or_supporting:
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
        include_atlas_overview=request.include_atlas_overview,
        include_exposure_thumbnails=request.include_exposure_thumbnails,
        supporting_session_keys=frozenset(
            session_key(session)
            for session in loaded_sessions
            if id(session) in supporting_session_ids
        ),
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
