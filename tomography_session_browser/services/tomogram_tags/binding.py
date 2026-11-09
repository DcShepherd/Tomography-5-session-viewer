"""Settings-owned folder bindings and explicit matching to loaded scientific rows."""
from dataclasses import dataclass, replace
from collections.abc import Callable, Iterable
from enum import StrEnum
import json
from pathlib import Path

from tomography_session_browser.domain.identity import path_identity_key
from tomography_session_browser.domain.tomogram_tags import ChangeKind, SourceAcceptance, TagProject, TagSource, TomogramRef, TomogramTags, VolumeEvidence
from tomography_session_browser.domain.tomograms import ProcessingSource, Tomogram
from tomography_session_browser.services.atomic_files import atomic_write, bounded_read, exclusive
from tomography_session_browser.services.settings_service import settings_path
from tomography_session_browser.services.output_boundary import check_output_path
from tomography_session_browser.services.tomogram_tags.codec import MAX_ACCEPTANCES, fields, relative, text, timestamp
from tomography_session_browser.services.tomogram_tags.changes import MissingEvidence, VolumeChange, compare_volumes, established, worst_kind
from tomography_session_browser.services.tomogram_tags.project import move_source, has_content, row_key
from tomography_session_browser.services.tomogram_tags.store import LoadedProject, ProjectStatus, TagProjectStore, validate_target


@dataclass(frozen=True, slots=True)
class ProjectKey:
    path: str
    project_id: str

    @classmethod
    def at(cls, path: Path, project: TagProject) -> "ProjectKey":
        return cls(path_identity_key(path.resolve()), project.project_id)

    @classmethod
    def of(cls, store: "TagProjectStore", project: TagProject) -> "ProjectKey":
        """The same key as ``at(store.path, project)``, without a filesystem call."""
        return cls(store.identity, project.project_id)


@dataclass(frozen=True, slots=True)
class RowBinding:
    ref: TomogramRef | None
    saved: TomogramTags | None = None
    changes: tuple[VolumeChange, ...] = ()
    reason: str = ""

    @property
    def needs_review(self) -> bool:
        return bool(self.changes)

    @property
    def source_changed(self) -> bool | None:
        if self.ref is None or self.saved is None or not self.saved.ref.volumes:
            return None
        return (True if established(self.changes) else None) if self.changes else False


@dataclass(frozen=True, slots=True)
class BoundRow:
    folder: ProcessingSource
    row: Tomogram
    binding: RowBinding

def tag_source(project: TagProject, folder: Path) -> TagSource | None:
    key = path_identity_key(folder)
    return next((s for s in project.sources if path_identity_key(s.path) == key), None)


def reference(source: TagSource, folder: ProcessingSource, row: Tomogram) -> TomogramRef:
    prefix = folder.id + "::"
    if row.source_id != folder.id or not row.id.startswith(prefix):
        raise ValueError("The tomogram does not have this folder's canonical identity.")
    volumes = tuple(VolumeEvidence(v.version, v.path.relative_to(folder.path).as_posix(),
                                  v.fingerprint.size, v.fingerprint.mtime_ns, v.grid, v.dtype)
                    for v in row.volumes if v.grid is not None)
    return TomogramRef(source.id, relative(row.id[len(prefix):]), volumes)


def bind_row(project: TagProject, folder: ProcessingSource, row: Tomogram) -> RowBinding:
    source = tag_source(project, folder.path)
    if source is None:
        return RowBinding(None, reason="This project does not list the processing folder.")
    return _bind(source, folder, row, lambda key: next((r for r in project.tomograms if row_key(r) == key), None))


def bind_rows(project: TagProject, folders: Iterable[ProcessingSource]) -> tuple[BoundRow, ...]:
    saved = {(r.ref.source, r.ref.relative): r for r in project.tomograms}
    sources = {path_identity_key(s.path): s for s in project.sources}
    return tuple(BoundRow(folder, row, _bind(source, folder, row, saved.get))
                 for folder in folders if (source := sources.get(path_identity_key(folder.path))) is not None
                 for row in folder.tomograms)


def _compare(saved: TomogramRef, live: TomogramRef, folder: ProcessingSource,
             row: Tomogram) -> tuple[VolumeChange, ...]:
    unreadable = frozenset(v.path.relative_to(folder.path).as_posix() for v in row.volumes if v.grid is None)
    return compare_volumes(saved, live, unreadable=unreadable, versions_complete=folder.versions_complete)


def _bind(source: TagSource, folder: ProcessingSource, row: Tomogram,
          lookup: Callable[[tuple[str, str]], TomogramTags | None]) -> RowBinding:
    try:
        ref = reference(source, folder, row)
        saved = lookup((ref.source, ref.relative))
        changes = _compare(saved.ref, ref, folder, row) if saved is not None else ()
    except ValueError as exc:
        return RowBinding(None, reason=str(exc))
    return RowBinding(ref, saved, changes)


@dataclass(frozen=True, slots=True)
class MoveMatch:
    relative: str
    eligible: bool
    reason: str


@dataclass(frozen=True, slots=True)
class MoveProposal:
    project: TagProject
    source_id: str
    folder: ProcessingSource
    matches: tuple[MoveMatch, ...]


def propose_move(project: TagProject, source_id: str, folder: ProcessingSource) -> MoveProposal:
    source = next((s for s in project.sources if s.id == source_id), None)
    if source is None:
        raise ValueError("The source is no longer in this project.")
    candidates = {r.id: r for r in folder.tomograms}
    matches = []
    for saved in project.tomograms:
        if saved.ref.source != source_id or not has_content(saved):
            continue
        row = candidates.get(folder.id + "::" + saved.ref.relative)
        if row is None:
            matches.append(MoveMatch(saved.ref.relative, False, "Tomogram not found."))
            continue
        live = reference(replace(source, path=folder.path), folder, row)
        changes = _compare(saved.ref, live, folder, row)
        valid = bool(saved.ref.volumes) and all(change.kind is ChangeKind.DATE for change in changes)
        matches.append(MoveMatch(saved.ref.relative, valid, "Matching recorded size and geometry; confirmation required." if valid else "Recorded size or geometry differs."))
    return MoveProposal(project, source_id, folder, tuple(matches))


def confirm_move(project: TagProject, proposal: MoveProposal) -> TagProject:
    if project != proposal.project or not all(m.eligible for m in proposal.matches):
        raise ValueError("The project changed or some tomograms do not match. Nothing was moved.")
    return move_source(project, proposal.source_id, proposal.folder.path)


class AcceptanceIssue(StrEnum):
    UNREADABLE = "unreadable"
    INCOMPLETE_DISCOVERY = "incomplete-discovery"
    NO_READABLE_VERSION = "no-readable"


@dataclass(frozen=True, slots=True)
class AcceptanceRow:
    row_id: str
    row_name: str
    folder_path: Path
    folder_name: str
    saved: TomogramTags
    live: TomogramRef
    changes: tuple[VolumeChange, ...]
    issue: AcceptanceIssue | None

    @property
    def key(self) -> tuple[str, str]:
        return row_key(self.saved)


@dataclass(frozen=True, slots=True)
class AcceptanceProposal:
    project: TagProject
    rows: tuple[AcceptanceRow, ...]


def propose_acceptance(project: TagProject, rows: Iterable[BoundRow]) -> AcceptanceProposal:
    review = []
    for item in rows:
        folder, row, binding = item.folder, item.row, item.binding
        if binding.saved is None or binding.ref is None or not binding.needs_review:
            continue
        missing = {change.missing for change in binding.changes}
        issue = None
        if MissingEvidence.UNKNOWN in missing:
            issue = AcceptanceIssue.INCOMPLETE_DISCOVERY
        elif MissingEvidence.UNREADABLE in missing:
            issue = AcceptanceIssue.UNREADABLE
        elif not binding.ref.volumes:
            issue = AcceptanceIssue.NO_READABLE_VERSION
        live = replace(binding.ref, volumes=tuple(sorted(binding.ref.volumes, key=lambda v: v.relative)))
        changes = tuple(sorted(binding.changes, key=lambda c: c.saved.relative))
        review.append(AcceptanceRow(row.id, row.name, folder.path, folder.name, binding.saved, live, changes, issue))
    return AcceptanceProposal(project, tuple(sorted(review, key=lambda row: row.key)))


def commit_acceptance(project: TagProject, proposal: AcceptanceProposal,
                      keys: frozenset[tuple[str, str]], accepted_at: str) -> TagProject:
    if project != proposal.project:
        raise ValueError("The project changed while you reviewed it. Nothing was accepted.")
    if not keys:
        raise ValueError("Select at least one tomogram to accept.")
    selected = {row.key: row for row in proposal.rows if row.issue is None and row.key in keys}
    if selected.keys() != keys:
        raise ValueError("Some selected tomograms cannot be accepted. Nothing was accepted.")
    timestamp(accepted_at)
    updates = {}
    for key, row in selected.items():
        if len(row.saved.acceptances) >= MAX_ACCEPTANCES:
            raise ValueError("A selected tomogram reached its acceptance limit. Nothing was accepted.")
        kind = worst_kind(established(row.changes))
        if kind is None:
            raise ValueError("Some selected tomograms cannot be accepted. Nothing was accepted.")
        updates[key] = replace(row.saved, ref=row.live,
                               acceptances=(*row.saved.acceptances, SourceAcceptance(accepted_at, kind)))
    return replace(project, tomograms=tuple(updates.get(row_key(row), row) for row in project.tomograms))


class BindingIndex:
    def __init__(self, path: Path | None = None, protected_roots: tuple[Path, ...] = ()):
        self.path = path or settings_path().with_name("tomogram-tag-bindings.json")
        self.protected_roots = protected_roots

    def read(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        validate_target(self.path, self.protected_roots)
        raw = fields(json.loads(bounded_read(self.path, 4 * 1024 * 1024)), "schema bindings")
        if raw["schema"] != 1 or not isinstance(raw["bindings"], dict) or len(raw["bindings"]) > 10_000:
            raise ValueError("Unsupported or oversized tag binding index.")
        result = {}
        for key, value in raw["bindings"].items():
            if not Path(text(value)).is_absolute():
                raise ValueError("Binding project paths must be absolute.")
            result[text(key)] = value
        return result

    def update(self, folder: Path, project_path: Path | None, loaded: LoadedProject | None = None) -> None:
        self.update_folders((folder,), project_path, loaded)

    def update_folders(self, folders: tuple[Path, ...], project_path: Path | None, loaded: LoadedProject | None = None) -> None:
        if project_path is not None:
            if loaded is None or loaded.project is None or loaded.status not in (ProjectStatus.READY, ProjectStatus.RECOVERING) or any(tag_source(loaded.project, f) is None for f in folders):
                raise ValueError("The loaded project must list this folder before binding it.")
            project_path = project_path.resolve()
        # The index is only a locator. Write after the corresponding project save;
        # a failure leaves the saved project intact and can be retried independently.
        lock = self.path.with_suffix(".lock")
        for path in (self.path, lock):
            check_output_path(path, self.protected_roots)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        validate_target(self.path, self.protected_roots)
        validate_target(lock, self.protected_roots)
        with exclusive(lock):
            saved = self.read()
            values = dict(saved)
            for folder in folders:
                if project_path is None:
                    values.pop(path_identity_key(folder), None)
                else:
                    values[path_identity_key(folder)] = str(project_path)
            # Every project open rebinds its folder, and the answer rarely changes.
            if values != saved or not self.path.exists():
                atomic_write(self.path, json.dumps({"schema": 1, "bindings": values}, allow_nan=False).encode("utf-8"),
                             protected_roots=self.protected_roots)

    def resolve(self, folder: Path, path: Path, loaded: LoadedProject) -> tuple[ProjectKey | None, str]:
        if self.read().get(path_identity_key(folder)) != str(path.resolve()):
            return None, "This folder is not bound to that project file."
        if loaded.project is None or loaded.status not in (ProjectStatus.READY, ProjectStatus.RECOVERING):
            return None, loaded.reason or "The bound project is unavailable."
        if tag_source(loaded.project, folder) is None:
            return None, "The bound project no longer lists this folder."
        return ProjectKey.at(path, loaded.project), ""


def suggested_path(folder: Path, snapshot_folder: Path | None) -> Path:
    """Named from the folder, never from the source label, which is display text (B6)."""
    return (snapshot_folder or Path.home()) / (folder.name + ".tags.json")
