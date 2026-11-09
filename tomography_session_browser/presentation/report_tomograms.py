"""Tomograms in PDF reports (D4 §5.2): one frozen population, one reducer.

The window freezes every loaded tomogram row once, when the report dialog
opens (``freeze_report_tomograms``). Each selection change reduces that same
snapshot (``select_report_tomograms``), so the preview, cover, sections and
slice requests share one membership decision. Membership is canonical
placement only: a row whose link names a candidate but is not placed becomes a
note, never a member. Qt-free and filesystem-free.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.domain.identity import entity_key_text
from tomography_session_browser.domain.models import Session
from tomography_session_browser.domain.tomograms import (
    SUSPICIOUS_SPACING_WARNING, UNRESOLVED_SPACING_WARNING, ProcessingSource, Tomogram, Volume,
)
from tomography_session_browser.parsers.path_utils import natural_key
from tomography_session_browser.presentation.dashboard.tomogram_summary_input import SummaryTomogram, TomogramSummaryInput
from tomography_session_browser.presentation.project_tree import processing_group_key, session_key
from tomography_session_browser.presentation.report_datasets import report_datasets
from tomography_session_browser.presentation.tomogram_placement import ExclusionKind
from tomography_session_browser.presentation.tomogram_slices import (
    SKIP_NO_VERSION, SKIP_UNREADABLE, SliceRequest, SliceSkip, plan_slice,
)
from tomography_session_browser.presentation.workspace import EntityKind, Workspace
from tomography_session_browser.services.tomogram_links import TomogramLink
from tomography_session_browser.services.tomogram_tags.binding import ProjectKey
from tomography_session_browser.services.tomogram_tags.store import ProjectStatus

RowState = Literal['placed', 'no_match', 'incomplete'] | ExclusionKind

#: The print noun for each evidence step, for exclusion and incomplete notes.
STEP_NOUNS: dict[str, str] = {'A': 'recorded paths', 'B': 'movie names', 'C': 'MDOC records',
                              'D': 'identical stacks', 'E': 'names'}

#: Print forms of the two calibration warnings: the screen text asks for
#: screen actions (F20), which a printed page cannot offer.
PRINT_WARNINGS: dict[str, str] = {
    UNRESOLVED_SPACING_WARNING: "No calibration. The image has no scale bar.",
    SUSPICIOUS_SPACING_WARNING: "The header spacing includes 1 Å. The calibration is not known.",
}


@dataclass(frozen=True, slots=True)
class ReportRow:
    summary: SummaryTomogram      # the screen's row: tomogram, link and frozen tag content
    state: RowState
    parent_label: str | None      # the verified parent's tilt-series name; never one of several
    dataset_label: str | None     # the placed parent's displayed owner
    candidate_labels: tuple[str, ...] = ()   # tilt-series names the link names, in candidate order

    @property
    def tomogram(self) -> Tomogram:
        return self.summary.tomogram

    @property
    def link(self) -> TomogramLink:
        return self.summary.link

    @property
    def key(self) -> str:
        return self.summary.tomogram.id


@dataclass(frozen=True, slots=True)
class SourceTags:
    """One folder's tag state, supplied by the UI tag owner."""
    project_key: ProjectKey | None
    project_status: ProjectStatus | None  # None when the folder has no tag project
    unsaved: bool

    @property
    def ready(self) -> bool:
        return self.project_status in (ProjectStatus.READY, ProjectStatus.RECOVERING) and self.project_key is not None


NO_TAGS = SourceTags(None, None, False)


@dataclass(frozen=True, slots=True)
class ReportSource:
    source: ProcessingSource
    label: str                    # display name, numbered when repeated; never a path
    tags: SourceTags


@dataclass(frozen=True, slots=True)
class ReportTomograms:
    """Every loaded row once, frozen when the report dialog opens."""
    rows: tuple[ReportRow, ...]
    sources: tuple[ReportSource, ...]
    stated: tuple[tuple[str, float], ...]
    acquisition_sessions: int


@dataclass(frozen=True, slots=True)
class TomogramSection:
    anchor: str                   # "dataset-N-tomograms" or "folder-N"; never a source id
    title: str
    members: tuple[ReportRow, ...]
    notes: tuple[ReportRow, ...]
    #: Folders whose rows are members: the folder itself in a folder report.
    #: Note-only folders do not contribute tags, counts or qualifications.
    source_ids: tuple[str, ...] = ()
    #: The dataset's tilt-table vector (entity keys), empty in a folder report.
    tilt_keys: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SessionNote:
    session_key: str
    session_name: str
    rows: tuple[ReportRow, ...]


@dataclass(frozen=True, slots=True)
class ScopedTomograms:
    sections: tuple[TomogramSection, ...]
    session_notes: tuple[SessionNote, ...]
    incomplete: tuple[ReportRow, ...]
    sources: tuple[ReportSource, ...]    # ticked folders, in folder order
    folders_left_out: int
    stated: tuple[tuple[str, float], ...]
    acquisition_sessions: int
    folder_report: bool = False

    @property
    def members(self) -> tuple[ReportRow, ...]:
        """Member rows once each, in section order."""
        return tuple({row.key: row for section in self.sections for row in section.members}.values())

    @property
    def excluded(self) -> tuple[ReportRow, ...]:
        """Note rows once each: dataset notes and session notes."""
        notes = [row for section in self.sections for row in section.notes]
        notes += [row for note in self.session_notes for row in note.rows]
        return tuple({row.key: row for row in notes}.values())

    @property
    def reportable(self) -> bool:
        """Members, notes or incomplete evidence; a folder report also prints empty folders."""
        if self.folder_report:
            return bool(self.sources)
        return bool(self.members or self.excluded or self.incomplete)

    def stated_for(self, volume: Volume) -> float | None:
        return dict(self.stated).get(volume.id)


# ------------------------------------------------------------------ freeze

def row_state(placed: bool, exclusion: ExclusionKind | None, link: TomogramLink) -> RowState:
    if placed:
        return 'placed'
    if exclusion is not None:
        return exclusion
    if link.status == 'unresolved' and link.step is not None and link.step != 'E':
        return 'incomplete'
    return 'no_match'


def folder_labels(workspace: Workspace) -> dict[str, str]:
    """Display names, numbered in folder order when two folders share one."""
    names = {source.id: workspace.group_names.get(processing_group_key(source.id), source.name)
             for source in workspace.processing_sources}
    counts = Counter(names.values())
    seen: Counter[str] = Counter()
    labels = {}
    for source in workspace.processing_sources:
        name = names[source.id]
        if counts[name] > 1:
            seen[name] += 1
            labels[source.id] = f"{name} ({seen[name]})"
        else:
            labels[source.id] = name
    return labels


def freeze_report_tomograms(workspace: Workspace, payload: TomogramSummaryInput,
                            tags: Mapping[str, SourceTags]) -> ReportTomograms:
    """Freeze the full loaded population: ``payload`` is ``summary_input(workspace, None, ...)``."""
    placement = workspace.tomogram_placement
    targets = {entity_key_text(value): value for value in workspace.entities(EntityKind.TILT_SERIES)}
    order = {source.id: index for index, source in enumerate(workspace.processing_sources)}
    rows = []
    for summary in payload.rows:
        tomogram, link = summary.tomogram, summary.link
        placed = tomogram.id in placement.owner_by_row
        state = row_state(placed, placement.exclusion(tomogram, link), link)
        parent = targets.get(link.target_key or '') if link.status == 'resolved' else None
        owner = workspace.row_owner(parent) if placed and parent is not None else None
        labels = tuple(targets[key].name if key in targets else key for key in link.candidates)
        rows.append(ReportRow(summary, state, parent.name if parent is not None else None,
                              owner[1] if owner is not None else None, labels))
    rows.sort(key=lambda row: (natural_key(row.tomogram.name), order.get(row.tomogram.source_id, 0), row.key))
    labels = folder_labels(workspace)
    sources = tuple(ReportSource(source, labels[source.id], tags.get(source.id, NO_TAGS))
                    for source in workspace.processing_sources)
    return ReportTomograms(tuple(rows), sources, tuple(payload.stated), len(workspace.sessions))


# ------------------------------------------------------------------ selection

def select_report_tomograms(snapshot: ReportTomograms, sessions: Sequence[Session],
                            source_ids: frozenset[str]) -> ScopedTomograms:
    """Reduce the snapshot to a report: every ticked row in exactly one state."""
    sources = tuple(source for source in snapshot.sources if source.source.id in source_ids)
    ticked = [row for row in snapshot.rows if row.tomogram.source_id in source_ids]
    left_out = len(snapshot.sources) - len(sources)

    def scoped(sections, session_notes=(), incomplete=(), folder_report=False) -> ScopedTomograms:
        return ScopedTomograms(tuple(sections), tuple(session_notes), tuple(incomplete), sources, left_out,
                               snapshot.stated, snapshot.acquisition_sessions, folder_report)

    if not sessions:
        sections = tuple(TomogramSection(f"folder-{index}", source.label,
                                         tuple(row for row in ticked if row.tomogram.source_id == source.source.id), (),
                                         (source.source.id,))
                         for index, source in enumerate(sources, 1))
        return scoped(sections, folder_report=True)

    datasets = []
    for session in sessions:
        for dataset in report_datasets(session):
            keys = tuple(entity_key_text(tilt) for tilt in dataset.tilt_series)
            datasets.append((dataset.name or dataset.path.name, keys, set(keys)))
    session_tilts = [(session, {entity_key_text(tilt) for tilt in session.tilt_series}) for session in sessions]
    members: list[list[ReportRow]] = [[] for _ in datasets]
    notes: list[list[ReportRow]] = [[] for _ in datasets]
    by_session: dict[str, tuple[Session, list[ReportRow]]] = {}
    incomplete = []
    for row in ticked:
        if row.state == 'placed':
            index = next((i for i, (_, _, keys) in enumerate(datasets) if row.link.target_key in keys), None)
            if index is not None:
                members[index].append(row)
                continue
        candidates = set(row.link.candidates)
        hits = [i for i, (_, _, keys) in enumerate(datasets) if candidates & keys]
        for index in hits:
            notes[index].append(row)
        if hits:
            continue
        named = [session for session, keys in session_tilts if candidates & keys]
        for session in named:
            by_session.setdefault(session_key(session), (session, []))[1].append(row)
        if named:
            continue
        if row.state == 'incomplete':
            incomplete.append(row)

    source_order = {source.source.id: index for index, source in enumerate(snapshot.sources)}
    sections = []
    for number, ((title, keys, _), placed, noted) in enumerate(zip(datasets, members, notes, strict=True), 1):
        if not placed and not noted:
            continue
        position = {key: index for index, key in enumerate(keys)}
        placed.sort(key=lambda row: (position.get(row.link.target_key or '', len(keys)),
                                     source_order.get(row.tomogram.source_id, 0), natural_key(row.tomogram.name), row.key))
        contributing = {row.tomogram.source_id for row in placed}
        sections.append(TomogramSection(f"dataset-{number}-tomograms", title, tuple(placed), tuple(noted),
                                        tuple(source.source.id for source in sources if source.source.id in contributing), keys))
    return scoped(sections, (SessionNote(key, session.name or session.path.name, tuple(rows))
                             for key, (session, rows) in by_session.items()), incomplete)


def section_sources(section: TomogramSection, scoped: ScopedTomograms) -> tuple[ReportSource, ...]:
    ids = set(section.source_ids)
    return tuple(source for source in scoped.sources if source.source.id in ids)


def section_input(section: TomogramSection, scoped: ScopedTomograms) -> TomogramSummaryInput:
    """The Summary input for a section's members: only their own sources, values and projects."""
    sources = section_sources(section, scoped)
    volume_ids = {volume.id for row in section.members for volume in row.tomogram.volumes}
    return TomogramSummaryInput(
        key=section.anchor, version=None, sort='Name', rows=tuple(row.summary for row in section.members),
        sources=tuple(source.source for source in sources), title=section.title,
        folder_projects=tuple(source.tags.project_key for source in sources),
        tags_unsaved=any(source.tags.unsaved for source in sources),
        stated=tuple((key, value) for key, value in scoped.stated if key in volume_ids))


def member_slice_plans(scoped: ScopedTomograms) -> dict[str, SliceRequest | SliceSkip]:
    """One plan per member row key, in section order. Notes get no image."""
    return {row.key: plan_slice(row.tomogram, scoped.stated_for) for row in scoped.members}


# ------------------------------------------------------------------ print wording

def exclusion_reason(row: ReportRow) -> str:
    """The print reason for a row that is not a member, keyed by its state."""
    step = row.link.step
    if row.state == 'ambiguous':
        names = ", ".join(row.candidate_labels)
        count = len(row.candidate_labels)
        if step == 'E':
            return f"The name is the same as {count} tilt series ({names}). The app did not select one."
        return f"The {STEP_NOUNS.get(step or '', 'acquisition data')} go to {count} tilt series ({names}). The app did not select one."
    return {
        'unconfirmed_name': "Same name, not confirmed.",
        'declined_name': "Same name. The reviewer declined the link.",
        'revoked_name': "Same name. The reviewer removed the link.",
        'nonunique_owner': "Its tilt series is in more than one dataset. The app did not select one.",
        'unrepresented_owner': "Its tilt series is in the session, not in a grid.",
        'no_match': "No loaded tilt series has this name or path.",
        'incomplete': f"The check of {STEP_NOUNS.get(step or '', 'acquisition data')} did not complete.",
        'placed': "",
    }[row.state]


def link_state_label(row: ReportRow) -> str:
    return "Linked" if row.link.status == 'resolved' else "Not linked"


def print_warning(text: str) -> str:
    """A tomogram warning in print: calibration warnings use their print form."""
    return PRINT_WARNINGS.get(text, text)


def incomplete_note(rows: Sequence[ReportRow], folders: int) -> str:
    """The cover note for incomplete evidence; it names the ticked-folder boundary."""
    if not rows:
        return ""
    counts = Counter(row.link.step for row in rows)
    steps = ", ".join(f"{STEP_NOUNS[step]} {counts[step]}" for step in STEP_NOUNS if counts.get(step))
    boundary = "the ticked folder" if folders == 1 else f"the {folders} ticked folders"
    return (f"Link data not complete: {count_phrase(len(rows), 'tomogram')} in {boundary} ({steps}). "
            "Their dataset is not known. The linked count does not include them.")


# ------------------------------------------------------------------ dialog preview

def _phrase(parts: Sequence[str]) -> str:
    return " · ".join(part for part in parts if part)


def _names(labels: Sequence[str]) -> str:
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]


def _counted(count: int, text: str) -> str:
    return f"{count} {text}" if count else ""


def _has_tags_or_note(row: ReportRow) -> bool:
    tags = row.summary.tags
    return tags is not None and (any(value.count for value in tags.tags) or bool(tags.note.strip()))


def folder_preview_line(source: ReportSource, rows: Sequence[ReportRow]) -> str:
    """One ticked folder in a folder report: its rows by link state."""
    if not rows:
        return f"{source.label}: no tomograms."
    linked = sum(row.link.status == 'resolved' for row in rows)
    incomplete = sum(row.state == 'incomplete' for row in rows)
    return source.label + ": " + _phrase((count_phrase(len(rows), "tomogram"), _counted(linked, "linked"),
                                         _counted(len(rows) - linked - incomplete, "not linked"),
                                         _counted(incomplete, "link data not complete"))) + "."


def slice_preview_line(plans: Sequence[SliceRequest | SliceSkip]) -> str:
    """How many volumes the central slices read, and why some rows get no image."""
    images = sum(isinstance(plan, SliceRequest) for plan in plans)
    text = f"Central slices: {count_phrase(images, 'image')} from {count_phrase(images, 'volume')}."
    skipped = Counter(plan.reason for plan in plans if isinstance(plan, SliceSkip))
    if skipped.get(SKIP_UNREADABLE):
        text += f" The app cannot read the volume header of {count_phrase(skipped[SKIP_UNREADABLE], 'tomogram')}."
    if skipped.get(SKIP_NO_VERSION):
        count = skipped[SKIP_NO_VERSION]
        text += f" {count_phrase(count, 'tomogram')} {'has' if count == 1 else 'have'} no volume to show."
    return text


def tomogram_preview_lines(scoped: ScopedTomograms, slices: bool) -> list[str]:
    """The dialog lines for printed tomogram content (D4 §5.1, board B0 dialog)."""
    lines: list[str] = []
    if scoped.folder_report:
        ticked = {source.source.id for source in scoped.sources}
        by_source = {section.source_ids[0]: section.members for section in scoped.sections if section.source_ids}
        lines += [folder_preview_line(source, by_source.get(source.source.id, ())) for source in scoped.sources]
    else:
        members = scoped.members
        ticked = {row.tomogram.source_id for row in members}
        if members:
            linked = f"{len(members)} from {count_phrase(len(ticked), 'folder')}"
        else:
            linked = "none"
        not_counted = len(scoped.excluded) + len(scoped.incomplete)
        lines.append("Linked tomograms: " + _phrase((linked, _counted(not_counted, "not counted"),
                                                       _counted(sum(map(_has_tags_or_note, members)), "with tags or notes"))) + ".")
    if slices:
        lines.append(slice_preview_line(tuple(member_slice_plans(scoped).values())))
    notes = []
    if scoped.folders_left_out:
        notes.append(f"The report does not include {count_phrase(scoped.folders_left_out, 'loaded folder')}.")
    unsaved = [source.label for source in scoped.sources if source.source.id in ticked and source.tags.unsaved]
    if unsaved:
        verb = "has" if len(unsaved) == 1 else "have"
        notes.append(f"{_names(unsaved)} {verb} tag changes that the reviewer did not save. "
                     "The report shows them as they are on the screen.")
    if notes:
        lines.append(" ".join(notes))
    return lines
