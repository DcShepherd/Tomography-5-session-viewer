"""Validated project I/O with explicit recovery and optimistic conflict refusal."""
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path
import stat
import time

from tomography_session_browser.domain.identity import path_identity_key
from tomography_session_browser.domain.tomogram_tags import TagProject
from tomography_session_browser.services.atomic_files import atomic_write, bounded_read, exclusive, move_exclusive, FileChanged, FileTooLarge
from tomography_session_browser.services.settings_service import settings_path
from tomography_session_browser.services.tomogram_tags.codec import PROJECT_LIMIT, decode_project, encode_project
from tomography_session_browser.services.output_boundary import check_output_path, require_output_directory


class ProjectStatus(StrEnum):
    LOADING = "Loading"
    READY = "Ready"
    RECOVERING = "Recovering"
    READ_ONLY = "ReadOnly"
    MISSING = "Missing"
    UNAVAILABLE = "Unavailable"
    CONFLICT = "Conflict"


class TagConflict(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class TagWriteReceipt:
    revision: int
    digest: str


@dataclass(frozen=True, slots=True)
class LoadedProject:
    status: ProjectStatus
    project: TagProject | None = None
    digest: str | None = None
    reason: str = ""
    has_backup: bool = False


def validate_target(path: Path, roots: tuple[Path, ...]) -> None:
    require_output_directory(path.parent, roots)
    # resolve() alone follows a file symlink. Refuse the indirection itself.
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if path.is_symlink() or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError("Tag output files cannot be symlinks or redirected files.")
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Tag output must be a regular file.")


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class TagProjectStore:
    def __init__(self, path: Path, protected_roots: tuple[Path, ...] = (), *, lock_directory: Path | None = None):
        self.path = path.absolute()
        self.backup = self.path.with_name(self.path.name + ".bak")
        self.protected_roots = protected_roots
        directory = lock_directory or settings_path().parent / "tag-project-locks"
        self.lock = directory / (hashlib.sha256(path_identity_key(self.path.resolve()).encode()).hexdigest() + ".lock")
        # Only one accepted header is retained. Its digest proves that the bytes
        # under the lock are the already validated copy, avoiding a second full
        # decode on every save. An outside change still refuses before this cache.
        self.validated_header: tuple[str, str, int] | None = None

    def last_saved(self) -> datetime | None:
        """When the project file last changed on disk, in local time, for display."""

        try:
            return datetime.fromtimestamp(self.path.stat().st_mtime)
        except OSError:
            return None

    def validate_paths(self) -> None:
        for path in (self.path, self.backup):
            validate_target(path, self.protected_roots)
        require_output_directory(self.lock.parent, self.protected_roots)
        validate_target(self.lock, self.protected_roots)

    def prepare_lock(self) -> None:
        for path in (self.path, self.backup, self.lock):
            check_output_path(path, self.protected_roots)
        self.lock.parent.mkdir(parents=True, exist_ok=True)
        validate_target(self.lock, self.protected_roots)

    def current(self) -> bytes | None:
        try:
            return bounded_read(self.path, PROJECT_LIMIT)
        except FileNotFoundError:
            return None

    def load(self) -> LoadedProject:
        try:
            # Reading user-selected project data is allowed, but never follow a file redirect.
            if not self.path.exists() and not self.path.is_symlink():
                return LoadedProject(ProjectStatus.MISSING, has_backup=self.backup.exists())
            validate_target(self.path, self.protected_roots)
            validate_target(self.backup, self.protected_roots)
            content = self.current()
            if content is None:
                return LoadedProject(ProjectStatus.MISSING, has_backup=self.backup.exists())
            try:
                raw = json.loads(content)
                if isinstance(raw, dict) and raw.get("kind") == "tomogram-tags" and type(raw.get("schema")) is int and raw["schema"] > 1:
                    return LoadedProject(ProjectStatus.READ_ONLY, reason="This project needs a newer version of the app.")
                project = decode_project(content)
                accepted = digest(content)
                self.validated_header = accepted, project.project_id, project.revision
                return LoadedProject(ProjectStatus.READY, project, accepted)
            except (ValueError, TypeError, KeyError, RecursionError):
                previous = decode_project(bounded_read(self.backup, PROJECT_LIMIT))
            self.prepare_lock()
            with exclusive(self.lock):
                for attempt in range(3):
                    try:
                        self.validate_paths()
                        if self.current() != content:
                            raise TagConflict("The project changed during recovery. Reload it.")
                        # Revalidate the backup on each attempt, before the move.
                        previous = decode_project(bounded_read(self.backup, PROJECT_LIMIT))
                        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                        damaged = self.path.with_name(f"{self.path.name}.damaged-{stamp}.json")
                        validate_target(damaged, self.protected_roots)
                        move_exclusive(self.path, damaged)
                        break
                    except PermissionError:
                        if attempt == 2:
                            raise
                        time.sleep(.05 * (attempt + 1))
            return LoadedProject(ProjectStatus.RECOVERING, previous, reason="Opened the previous copy; restoring the project file.", has_backup=True)
        except TagConflict as exc:
            return LoadedProject(ProjectStatus.CONFLICT, reason=str(exc))
        except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
            return LoadedProject(ProjectStatus.UNAVAILABLE, reason=str(exc), has_backup=self.backup.exists())

    def restore_backup(self) -> LoadedProject:
        """Only called after an explicit Restore choice for a missing primary."""
        self.prepare_lock()
        with exclusive(self.lock):
            self.validate_paths()
            if self.current() is not None:
                raise TagConflict("A project now exists at this path. Reload it before restoring.")
            project = decode_project(bounded_read(self.backup, PROJECT_LIMIT))
        return LoadedProject(ProjectStatus.RECOVERING, project, has_backup=True)

    def save(self, project: TagProject, expected_digest: str | None) -> TagWriteReceipt:
        encoded = encode_project(project)
        self.prepare_lock()
        for attempt in range(3):
            try:
                with exclusive(self.lock):
                    self.validate_paths()
                    try:
                        previous = self.current()
                    except (FileChanged, FileTooLarge) as exc:
                        raise TagConflict("The project changed outside this window. Reload or Save As.") from exc
                    actual = digest(previous) if previous is not None else None
                    if actual != expected_digest:
                        raise TagConflict("The project changed outside this window. Reload or Save As.")
                    if previous is not None:
                        if self.validated_header is not None and self.validated_header[0] == actual:
                            _, old_id, old_revision = self.validated_header
                        else:
                            old = decode_project(previous)
                            old_id, old_revision = old.project_id, old.revision
                        if old_id != project.project_id or project.revision <= old_revision:
                            raise ValueError("Project identity changed or its revision did not advance.")
                        atomic_write(self.backup, previous, protected_roots=self.protected_roots)
                    # Recheck destination protection immediately before each replacement.
                    self.validate_paths()
                    atomic_write(self.path, encoded, protected_roots=self.protected_roots)
                    committed = digest(encoded)
                    self.validated_header = committed, project.project_id, project.revision
                    return TagWriteReceipt(project.revision, committed)
            except PermissionError:
                if attempt == 2:
                    raise
                time.sleep(.05 * (attempt + 1))
        raise AssertionError("Unreachable save retry state")
