"""A window-owned live project value and its explicit persistence state."""
from concurrent.futures import Future
from dataclasses import dataclass, replace
from enum import StrEnum

from tomography_session_browser.domain.tomogram_tags import TagProject
from tomography_session_browser.services.tomogram_tags.store import (
    LoadedProject, ProjectStatus, TagConflict, TagProjectStore, TagWriteReceipt,
)
from tomography_session_browser.services.tomogram_tags.writer import TagProjectWriter


class SaveStatus(StrEnum):
    SAVED = "Saved"
    SAVING = "Saving"
    UNSAVED = "Unsaved"
    FAILED = "Failed"


@dataclass(frozen=True, slots=True)
class SaveState:
    status: SaveStatus
    revision: int | None = None
    reason: str = ""
    retryable: bool = False


class TagSession:
    def __init__(self, store: TagProjectStore, loaded: LoadedProject):
        self.store, self.project = store, loaded.project
        self.status, self.reason, self.has_backup = loaded.status, loaded.reason, loaded.has_backup
        self.writer: TagProjectWriter | None = None
        self.waiting: set[Future[TagWriteReceipt]] = set()
        self.retired = False
        self.suspended = False
        self.save_state = SaveState(SaveStatus.SAVED, self.project.revision if self.project else None)
        if self.project and self.status in (ProjectStatus.READY, ProjectStatus.RECOVERING):
            revision = self.project.revision if loaded.digest else -1
            self.writer = TagProjectWriter(store, loaded.digest, revision)
            if self.status == ProjectStatus.RECOVERING or loaded.digest is None:
                self.save()

    @property
    def editable(self) -> bool:
        return not self.retired and not self.suspended and self.project is not None and self.status in (ProjectStatus.READY, ProjectStatus.RECOVERING)

    @property
    def dirty(self) -> bool:
        return self.save_state.status != SaveStatus.SAVED

    def edit(self, project: TagProject, *, expected: TagProject | None = None) -> None:
        if not self.editable or self.project is None:
            raise ValueError(self.reason or "This project is not editable.")
        if expected is not None and self.project != expected:
            raise ValueError("The project changed. Recompute this operation.")
        if project.project_id != self.project.project_id:
            raise ValueError("An edit cannot replace a project's identity.")
        if project == self.project:
            return
        self.project = replace(project, revision=self.project.revision + 1)
        self.save_state = SaveState(SaveStatus.UNSAVED)
        self.save()

    def save(self) -> None:
        if not self.editable or self.writer is None or self.project is None:
            raise ValueError("This project cannot be saved.")
        try:
            self.waiting.add(self.writer.submit(self.project))
            self.save_state = SaveState(SaveStatus.SAVING)
        except (ValueError, RuntimeError) as exc:
            self.failed(exc)

    def failed(self, exc: Exception) -> None:
        if isinstance(exc, TagConflict):
            self.status, self.reason = ProjectStatus.CONFLICT, str(exc)
        self.save_state = SaveState(SaveStatus.FAILED, reason=str(exc), retryable=isinstance(exc, OSError))

    def acknowledge(self, receipt: TagWriteReceipt) -> None:
        if self.retired or self.project is None:
            return
        if self.save_state.status == SaveStatus.SAVED and self.save_state.revision is not None and receipt.revision <= self.save_state.revision:
            return
        if receipt.revision == self.project.revision:
            self.save_state = SaveState(SaveStatus.SAVED, receipt.revision)
            if self.status == ProjectStatus.RECOVERING:
                self.status, self.reason = ProjectStatus.READY, ""
        elif self.save_state.status != SaveStatus.FAILED:
            self.save_state = SaveState(SaveStatus.UNSAVED)

    def poll(self) -> bool:
        """Called by the UI receiver, never from a worker's future callback."""
        before = self.status, self.save_state
        done = sorted((f for f in self.waiting if f.done()),
                      key=lambda f: f.result().revision if f.exception() is None else -1)
        for future in done:
            self.waiting.remove(future)
            try:
                self.acknowledge(future.result())
            except Exception as exc:
                self.failed(exc)
        if self.waiting and self.save_state.status == SaveStatus.UNSAVED:
            self.save_state = SaveState(SaveStatus.SAVING)
        return before != (self.status, self.save_state)

    def close(self, timeout: float | None = 0., *, discard: bool = False) -> bool:
        if self.writer and not self.writer.flush(timeout):
            return False
        self.poll()
        if self.dirty and not discard:
            return False
        if self.writer and not self.writer.close(timeout):
            return False
        self.retired = True
        return True
