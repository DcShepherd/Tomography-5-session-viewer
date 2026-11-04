"""Settings-owned folder bindings and explicit matching to loaded scientific rows."""
from dataclasses import dataclass, replace
import json
from pathlib import Path

from tomography_session_browser.domain.identity import path_identity_key
from tomography_session_browser.domain.tomogram_tags import TagProject, TagSource, TomogramRef, TomogramTags, VolumeEvidence
from tomography_session_browser.domain.tomograms import ProcessingSource, Tomogram
from tomography_session_browser.services.atomic_files import atomic_write, bounded_read, exclusive
from tomography_session_browser.services.settings_service import settings_path
from tomography_session_browser.services.output_boundary import check_output_path
from tomography_session_browser.services.tomogram_tags.codec import fields, relative, text
from tomography_session_browser.services.tomogram_tags.project import move_source, has_content
from tomography_session_browser.services.tomogram_tags.store import LoadedProject, ProjectStatus, validate_target


@dataclass(frozen=True, slots=True)
class ProjectKey:
    path: str
    project_id: str

    @classmethod
    def at(cls, path: Path, project: TagProject) -> "ProjectKey":
        return cls(path_identity_key(path.resolve()), project.project_id)


@dataclass(frozen=True, slots=True)
class RowBinding:
    ref: TomogramRef | None
    saved: TomogramTags | None = None
    changed: bool = False
    reason: str = ""


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
    try:
        ref = reference(source, folder, row)
    except ValueError as exc:
        return RowBinding(None, reason=str(exc))
    saved = next((r for r in project.tomograms if (r.ref.source, r.ref.relative) == (ref.source, ref.relative)), None)
    live = {v.relative: v for v in ref.volumes}
    changed = saved is not None and any(live.get(v.relative) != v for v in saved.ref.volumes)
    return RowBinding(ref, saved, changed, "Reconstruction changed since tagged." if changed else "")


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
        volumes = {v.relative: v for v in live.volumes}
        valid = bool(saved.ref.volumes) and all(
            (v.relative in volumes and (v.size, v.grid, v.dtype) ==
             (volumes[v.relative].size, volumes[v.relative].grid, volumes[v.relative].dtype)) for v in saved.ref.volumes)
        matches.append(MoveMatch(saved.ref.relative, valid, "Matching recorded size and geometry; confirmation required." if valid else "Recorded size or geometry differs."))
    return MoveProposal(project, source_id, folder, tuple(matches))


def confirm_move(project: TagProject, proposal: MoveProposal) -> TagProject:
    if project != proposal.project or not all(m.eligible for m in proposal.matches):
        raise ValueError("The project changed or some tomograms do not match. Nothing was moved.")
    return move_source(project, proposal.source_id, proposal.folder.path)


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
            values = self.read()
            for folder in folders:
                if project_path is None:
                    values.pop(path_identity_key(folder), None)
                else:
                    values[path_identity_key(folder)] = str(project_path)
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


def suggested_path(folder: Path, snapshot_folder: Path) -> Path:
    return snapshot_folder / (folder.name + ".tags.json")
