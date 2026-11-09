"""ID-scoped immutable edits. Counts and their provenance always travel together."""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
import uuid

from tomography_session_browser.domain.tomogram_tags import (
    CaptureKey, CaptureLink, SavedCapture, Tag, TagAssignment, TagProject, TomogramRef, TomogramTags,
)
from tomography_session_browser.services.tomogram_tags.codec import count, name, saved_capture, text, timestamp, relative, MAX_ROWS, MAX_TAGS
from tomography_session_browser.services.volume.review_repository import ReviewRecord


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_project() -> TagProject:
    return TagProject(uuid.uuid4().hex, utc_now())


def row_key(row: TomogramTags) -> tuple[str, str]:
    return row.ref.source, row.ref.relative


def has_content(row: TomogramTags) -> bool:
    return bool(row.counting_complete or row.note or row.tags or row.captures or row.links)


def find_row(project: TagProject, ref: TomogramRef) -> TomogramTags:
    relative(ref.relative)
    if not any(s.id == ref.source for s in project.sources):
        raise ValueError("The tomogram's folder is not in this project.")
    return next((r for r in project.tomograms if row_key(r) == (ref.source, ref.relative)), TomogramTags(ref))


def put_row(project: TagProject, row: TomogramTags) -> TagProject:
    old = next((r for r in project.tomograms if row_key(r) == row_key(row)), None)
    if old == row:
        return project
    if not has_content(row):
        return replace(project, tomograms=tuple(r for r in project.tomograms if row_key(r) != row_key(row))) if old else project
    if old is None and len(project.tomograms) >= MAX_ROWS:
        raise ValueError("This project reached its tomogram limit.")
    rows = tuple(row if row_key(r) == row_key(row) else r for r in project.tomograms)
    return replace(project, tomograms=rows if old else (*rows, row))


def create_tag(project: TagProject, label: str) -> tuple[TagProject, Tag]:
    label = name(label)
    existing = next((t for t in project.catalogue if t.name.casefold() == label.casefold()), None)
    if existing:
        return project, existing
    if len(project.catalogue) >= MAX_TAGS:
        raise ValueError("This project reached its tag limit.")
    label = label[0].upper() + label[1:]
    tag = Tag(uuid.uuid4().hex, label, utc_now())
    return replace(project, catalogue=(*project.catalogue, tag)), tag


def assign(project: TagProject, ref: TomogramRef, assignment: TagAssignment) -> TagProject:
    if not any(t.id == assignment.tag_id for t in project.catalogue):
        raise ValueError("The tag is no longer in this project.")
    count(assignment.count)
    timestamp(assignment.recorded_at)
    relative(assignment.version_relative)
    text(assignment.version_label)
    row = find_row(project, ref)
    if not any(v.relative == assignment.version_relative for v in row.ref.volumes):
        added = next((v for v in ref.volumes if v.relative == assignment.version_relative), None)
        if added is not None:
            row = replace(row, ref=replace(row.ref, volumes=(*row.ref.volumes, added)))
    tags = tuple(t for t in row.tags if t.tag_id != assignment.tag_id)
    return put_row(project, replace(row, tags=(*tags, assignment)))


def set_note(project: TagProject, ref: TomogramRef, note: str) -> TagProject:
    return put_row(project, replace(find_row(project, ref), note=text(note, 4000)))


def set_complete(project: TagProject, ref: TomogramRef, complete: bool) -> TagProject:
    if type(complete) is not bool:
        raise ValueError("Counting complete must be true or false.")
    return put_row(project, replace(find_row(project, ref), counting_complete=complete))


def clean_captures(row: TomogramTags) -> TomogramTags:
    keys = {l.capture_key for l in row.links}
    return replace(row, captures=tuple(c for c in row.captures if c.key in keys))


def unassign(project: TagProject, ref: TomogramRef, tag_id: str) -> TagProject:
    row = find_row(project, ref)
    return put_row(project, clean_captures(replace(row, tags=tuple(t for t in row.tags if t.tag_id != tag_id),
                                                  links=tuple(l for l in row.links if l.tag_id != tag_id))))


def rename(project: TagProject, tag_id: str, label: str) -> TagProject:
    label = name(label)
    if not any(t.id == tag_id for t in project.catalogue):
        raise ValueError("The tag was removed.")
    if any(t.id != tag_id and t.name.casefold() == label.casefold() for t in project.catalogue):
        raise ValueError("A tag already has this name. Use Merge instead.")
    return replace(project, catalogue=tuple(replace(t, name=label) if t.id == tag_id else t for t in project.catalogue))


def remove_tag(project: TagProject, tag_id: str) -> TagProject:
    rows = tuple(clean_captures(replace(row, tags=tuple(t for t in row.tags if t.tag_id != tag_id),
                                       links=tuple(l for l in row.links if l.tag_id != tag_id))) for row in project.tomograms)
    return replace(project, catalogue=tuple(t for t in project.catalogue if t.id != tag_id), tomograms=tuple(r for r in rows if has_content(r)))


def freeze_capture(record: ReviewRecord) -> SavedCapture:
    if record.identity != record.order[1]:
        raise ValueError("Capture identity disagrees with its ordering key.")
    return saved_capture(CaptureKey(*record.order), record.data)


def link_capture(project: TagProject, ref: TomogramRef, tag_id: str, capture: SavedCapture) -> TagProject:
    row = find_row(project, ref)
    if not any(t.tag_id == tag_id for t in row.tags):
        raise ValueError("The tag is no longer on this tomogram.")
    if any(l.tag_id == tag_id and l.capture_key == capture.key for l in row.links):
        return project
    # Revalidate even values constructed directly rather than through admission.
    from tomography_session_browser.services.tomogram_tags.codec import thaw
    saved_capture(capture.key, thaw(capture.record))
    old = next((c for c in row.captures if c.key == capture.key), None)
    if old and old != capture:
        raise ValueError("A different retained view already has this capture key.")
    if len(row.links) >= 100_000 or (old is None and len(row.captures) >= 10_000):
        raise ValueError("This tomogram reached its capture-link limit.")
    captures = row.captures if old else (*row.captures, capture)
    return put_row(project, replace(row, captures=captures, links=(*row.links, CaptureLink(tag_id, capture.key, utc_now()))))


def unlink_capture(project: TagProject, ref: TomogramRef, tag_id: str, key: CaptureKey) -> TagProject:
    row = find_row(project, ref)
    return put_row(project, clean_captures(replace(row, links=tuple(l for l in row.links if (l.tag_id, l.capture_key) != (tag_id, key)))))


@dataclass(frozen=True, slots=True)
class MergeConflict:
    row: tuple[str, str]
    surviving: TagAssignment
    removed: TagAssignment


@dataclass(frozen=True, slots=True)
class MergeProposal:
    original: TagProject
    surviving_id: str
    removed_id: str
    conflicts: tuple[MergeConflict, ...]


def propose_merge(project: TagProject, surviving_id: str, removed_id: str) -> MergeProposal:
    if surviving_id == removed_id or not {surviving_id, removed_id} <= {t.id for t in project.catalogue}:
        raise ValueError("Choose two different existing tags.")
    conflicts = []
    for row in project.tomograms:
        tags = {t.tag_id: t for t in row.tags}
        a, b = tags.get(surviving_id), tags.get(removed_id)
        if a and b and a.count is not None and b.count is not None and a.count != b.count:
            conflicts.append(MergeConflict(row_key(row), a, b))
    return MergeProposal(project, surviving_id, removed_id, tuple(conflicts))


def commit_merge(project: TagProject, proposal: MergeProposal, choices: dict[tuple[str, str], str]) -> TagProject:
    if project != proposal.original:
        raise ValueError("The project changed. Review the merge again.")
    if set(choices) != {c.row for c in proposal.conflicts} or any(v not in (proposal.surviving_id, proposal.removed_id) for v in choices.values()):
        raise ValueError("Resolve every count conflict before merging.")
    rows = []
    for row in project.tomograms:
        tags = {t.tag_id: t for t in row.tags}
        a, b = tags.get(proposal.surviving_id), tags.pop(proposal.removed_id, None)
        if b:
            selected = tags.get(choices.get(row_key(row), "")) if row_key(row) in choices else None
            if row_key(row) in choices:
                selected = a if choices[row_key(row)] == proposal.surviving_id else b
            else:
                selected = b if a is None or (a.count is None and b.count is not None) else a
            assert selected is not None
            tags[proposal.surviving_id] = replace(selected, tag_id=proposal.surviving_id)
        links = {}
        for link in row.links:
            link = replace(link, tag_id=proposal.surviving_id) if link.tag_id == proposal.removed_id else link
            links.setdefault((link.tag_id, link.capture_key), link)
        rows.append(clean_captures(replace(row, tags=tuple(tags.values()), links=tuple(links.values()))))
    return replace(project, catalogue=tuple(t for t in project.catalogue if t.id != proposal.removed_id), tomograms=tuple(rows))


def move_source(project: TagProject, source_id: str, path: Path) -> TagProject:
    if not path.is_absolute() or not any(s.id == source_id for s in project.sources):
        raise ValueError("Choose an existing source and an absolute loaded folder.")
    from tomography_session_browser.domain.identity import path_identity_key
    if any(s.id != source_id and path_identity_key(s.path) == path_identity_key(path) for s in project.sources):
        raise ValueError("That folder already belongs to this project.")
    return replace(project, sources=tuple(replace(s, path=path, previous_paths=(*s.previous_paths, s.path))
                                          if s.id == source_id else s for s in project.sources))
