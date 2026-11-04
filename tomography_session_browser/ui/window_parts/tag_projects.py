"""Window-owned tag sessions; project and binding I/O always run on workers."""
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import replace
from pathlib import Path
from collections.abc import Callable

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QWidget

from tomography_session_browser.domain.identity import path_identity_key
from tomography_session_browser.domain.tomogram_tags import TagProject, TagSource
from tomography_session_browser.services.tomogram_tags.binding import BindingIndex, ProjectKey, tag_source
from tomography_session_browser.services.tomogram_tags.project import new_project
from tomography_session_browser.services.tomogram_tags.session import TagSession
from tomography_session_browser.services.tomogram_tags.store import TagProjectStore, LoadedProject, ProjectStatus


class TagProjects(QWidget):
    changed = Signal()
    project_moved = Signal(object, object)

    def __init__(self, parent: QWidget, protected_roots: Callable[[], tuple[Path, ...]]):
        super().__init__(parent)
        self.hide()
        self.protected_roots = protected_roots
        self.sessions: dict[str, TagSession] = {}
        self.bindings: dict[str, str] = {}
        self.issues: dict[str, str] = {}
        self.loading: set[str] = set()
        self.located: set[str] = set()
        self.tasks: list[tuple[Future, Callable]] = []
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tag-project-io")
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        self.stopping = False
        self.closed = False
        self.producer_drains = []
        self.pending_bindings: dict[str, tuple[Path, TagSession]] = {}
        self.relocations: dict[str, TagSession] = {}
        self.binding_tasks: set[TagSession] = set()
        self.loaded_sources = lambda: ()
        self.failed_bindings = {}
        self.opening: dict[str, set[Path]] = {}
        parent.destroyed.connect(self.dispose)

    def submit(self, work: Callable, finished: Callable) -> None:
        if self.closed:
            raise ValueError("Tag projects are closed.")
        self.tasks.append((self.executor.submit(work), finished))

    def poll(self) -> None:
        if self.closed:
            return
        sources = self.loaded_sources()
        loaded_keys = {path_identity_key(s.path) for s in sources}
        self.bindings = {k: v for k, v in self.bindings.items() if k in loaded_keys}
        self.located.intersection_update(loaded_keys)
        self.ensure(tuple(s.path for s in sources))
        changed = False
        for future, finished in tuple(self.tasks):
            if not future.done():
                continue
            self.tasks.remove((future, finished))
            finished(future)
            changed = True
        roots = self.protected_roots()
        for session in tuple(self.sessions.values()):
            session.store.protected_roots = roots
            changed = session.poll() or changed
        ready = {s for _, s in self.pending_bindings.values() if not s.dirty and s.project and not s.retired and s not in self.binding_tasks}
        for session in ready:
            folders = tuple(folder for folder, s in self.pending_bindings.values() if s is session)
            for folder in folders:
                self.pending_bindings.pop(path_identity_key(folder))
            self.binding_tasks.add(session)
            roots = self.protected_roots()
            loaded = LoadedProject(ProjectStatus.READY, session.project)
            self.submit(lambda f=folders, s=session, l=loaded, r=roots: BindingIndex(protected_roots=r).update_folders(f, s.store.path, l),
                        lambda future, f=folders, s=session: self.binding_saved(future, f, s))
        for key in tuple(self.relocations):
            session = self.sessions.get(key)
            if session and not session.dirty and session not in self.binding_tasks and not any(s is session for _, s in (*self.pending_bindings.values(), *self.failed_bindings.values())):
                self.finish_relocation(session)
                changed = True
        retained = {s for _, s in (*self.pending_bindings.values(), *self.failed_bindings.values())} | set(self.relocations.values())
        for key, session in tuple(self.sessions.items()):
            if key not in self.bindings.values() and key not in self.relocations and session not in retained and session not in self.binding_tasks and not session.dirty:
                if not session.writer or session.writer.flush(0):
                    session.close(None)
                    self.sessions.pop(key, None)
                    changed = True
        if changed:
            self.changed.emit()

    def ensure(self, folders: tuple[Path, ...]) -> None:
        wanted = tuple(p for p in folders if path_identity_key(p) not in self.located)
        if not wanted:
            return
        self.located.update(path_identity_key(p) for p in wanted)
        self.loading.update(path_identity_key(p) for p in wanted)
        roots = self.protected_roots()
        def work():
            return BindingIndex(protected_roots=roots).read()
        def finished(future):
            try:
                index = future.result()
                for folder in wanted:
                    key = path_identity_key(folder)
                    self.loading.discard(key)
                    path = index.get(key)
                    if path:
                        self.open(Path(path), folder)
            except Exception as exc:
                for folder in wanted:
                    self.loading.discard(path_identity_key(folder))
                    self.issues[path_identity_key(folder)] = str(exc)
        self.submit(work, finished)

    def session_for(self, folder: Path) -> TagSession | None:
        key = self.bindings.get(path_identity_key(folder))
        session = self.sessions.get(key or "")
        if session and session.project and tag_source(session.project, folder) is None:
            return None
        return session

    def attention_sessions(self) -> tuple[TagSession, ...]:
        """Unique projects with failed edits or bindings, derived from their owners."""
        sessions = [s for s in self.sessions.values() if s.dirty and s.save_state.reason]
        sessions.extend(s for _, s in self.failed_bindings.values())
        return tuple({path_identity_key(s.store.path.resolve()): s for s in sessions}.values())

    def key_for(self, folder: Path) -> ProjectKey | None:
        session = self.session_for(folder)
        return ProjectKey.at(session.store.path, session.project) if session and session.project else None

    def summary_input(self, payload):
        from tomography_session_browser.presentation.dashboard.tag_summary import TagContent, TagValue
        from tomography_session_browser.services.tomogram_tags.binding import bind_row
        sources = {s.id: s for s in payload.sources}
        rows = []
        for row in payload.rows:
            source = sources[row.tomogram.source_id]
            session = self.session_for(source.path)
            content = None
            if session and session.project:
                binding = bind_row(session.project, source, row.tomogram)
                if binding.ref:
                    saved = binding.saved
                    names = {t.id: t.name for t in session.project.catalogue}
                    content = TagContent(ProjectKey.at(session.store.path, session.project), session.store.path.stem,
                        tuple(sorted((TagValue(a.tag_id, names[a.tag_id], a.count) for a in saved.tags), key=lambda t: t.id)) if saved else (),
                        saved.counting_complete if saved else False)
            rows.append(replace(row, tags=content))
        return replace(payload, rows=tuple(rows), folder_projects=tuple(self.key_for(s.path) for s in payload.sources),
                       tags_unsaved=any(s.dirty for source in payload.sources if (s := self.session_for(source.path)) is not None))

    def open(self, path: Path, folder: Path, *, create: bool = False, restore: bool = False,
             project: TagProject | None = None, relocating: TagSession | None = None) -> None:
        if self.stopping and relocating is None:
            return
        key, folder_key = path_identity_key(path.resolve()), path_identity_key(folder)
        existing = self.sessions.get(key)
        if create and existing and existing.status == ProjectStatus.MISSING:
            existing.close(None, discard=True)
            self.sessions.pop(key)
            existing = None
        if existing and not existing.retired:
            if create:
                self.issues[folder_key] = "A project already exists there. Choose a new Save As destination."
                self.changed.emit()
                return
            if existing.project and tag_source(existing.project, folder):
                self.bindings[folder_key] = key
                self.bind_after_save(folder, existing)
                self.changed.emit()
            else:
                self.issues[folder_key] = "The selected project does not list this folder."
            return
        if key in self.opening:
            self.opening[key].add(folder)
            self.loading.add(folder_key)
            return
        self.opening[key] = {folder}
        if relocating:
            relocating.suspended = True
            self.relocations[key] = relocating
        self.loading.add(folder_key)
        roots = self.protected_roots()
        def work():
            store = TagProjectStore(path, roots)
            if create:
                value = project or new_project()
                if not any(path_identity_key(s.path) == folder_key for s in value.sources):
                    import uuid
                    value = replace(value, sources=(*value.sources, TagSource(uuid.uuid4().hex, folder, folder.name)))
                from tomography_session_browser.services.tomogram_tags.store import validate_target, TagConflict
                validate_target(store.path, roots)
                if store.current() is not None:
                    raise TagConflict("A file already exists at this path. Open it explicitly or choose a different name.")
                loaded = LoadedProject(ProjectStatus.READY, value)
            else:
                loaded = store.restore_backup() if restore else store.load()
            if loaded.project and tag_source(loaded.project, folder) is None:
                raise ValueError("This project does not list the selected processing folder.")
            if loaded.project and not create:
                BindingIndex(protected_roots=roots).update(folder, path, loaded)
            return store, loaded
        def finished(future):
            folders = self.opening.pop(key)
            for candidate in folders:
                self.loading.discard(path_identity_key(candidate))
            try:
                store, loaded = future.result()
                self.sessions[key] = TagSession(store, loaded)
                if relocating is None:
                    self.bindings[folder_key] = key
                if create:
                    if relocating:
                        self.relocations[key] = relocating
                        old_key = path_identity_key(relocating.store.path.resolve())
                        for source in loaded.project.sources:
                            if self.bindings.get(path_identity_key(source.path)) == old_key:
                                self.bind_after_save(source.path, self.sessions[key])
                    else:
                        self.bind_after_save(folder, self.sessions[key])
                self.issues.pop(folder_key, None)
                for candidate in folders - {folder}:
                    if loaded.project and tag_source(loaded.project, candidate):
                        self.bind_after_save(candidate, self.sessions[key])
                    else:
                        self.issues[path_identity_key(candidate)] = "The bound project does not list this folder."
            except Exception as exc:
                self.issues[folder_key] = str(exc)
                if relocating:
                    relocating.suspended = False
                    self.relocations.pop(key, None)
        self.submit(work, finished)
        self.changed.emit()

    def bind_after_save(self, folder: Path, session: TagSession) -> None:
        self.pending_bindings[path_identity_key(folder)] = folder, session

    def binding_saved(self, future, folders: tuple[Path, ...], session: TagSession) -> None:
        self.binding_tasks.discard(session)
        try:
            future.result()
            if not session.retired:
                new_key = path_identity_key(session.store.path.resolve())
                for folder in folders:
                    self.bindings[path_identity_key(folder)] = new_key
                    self.issues.pop(path_identity_key(folder), None)
                self.finish_relocation(session)
        except Exception as exc:
            for folder in folders:
                self.issues[path_identity_key(folder)] = "Project saved, but its folder binding failed: " + str(exc)
                self.failed_bindings[path_identity_key(folder)] = folder, session

    def finish_relocation(self, session: TagSession) -> None:
        old = self.relocations.pop(path_identity_key(session.store.path.resolve()), None)
        if old:
            old.close(None, discard=True)
            self.sessions.pop(path_identity_key(old.store.path.resolve()), None)
            self.pending_bindings = {k: v for k, v in self.pending_bindings.items() if v[1] is not old}
            self.failed_bindings = {k: v for k, v in self.failed_bindings.items() if v[1] is not old}
            self.project_moved.emit(ProjectKey.at(old.store.path, old.project), ProjectKey.at(session.store.path, session.project))

    def cancel_relocation(self, old: TagSession) -> None:
        for key, session in tuple(self.relocations.items()):
            if session is old:
                new = self.sessions.get(key)
                if new in self.binding_tasks:
                    return
                if new and new.writer and not new.writer.flush(0):
                    return
                if new and not new.close(None, discard=True):
                    return
                self.sessions.pop(key, None)
                self.relocations.pop(key)
                self.pending_bindings = {k: v for k, v in self.pending_bindings.items() if v[1] is not new}
                self.failed_bindings = {k: v for k, v in self.failed_bindings.items() if v[1] is not new}
                old.suspended = False

    def reload_session(self, session: TagSession) -> None:
        """The old idle lane is resolved before a replacement owner is published."""
        key = path_identity_key(session.store.path.resolve())
        if not session.close(None, discard=True):
            return
        self.sessions.pop(key, None)
        self.pending_bindings = {k: v for k, v in self.pending_bindings.items() if v[1] is not session}
        self.failed_bindings = {k: v for k, v in self.failed_bindings.items() if v[1] is not session}
        store = TagProjectStore(session.store.path, self.protected_roots())
        def finished(future):
            try:
                self.sessions[key] = TagSession(store, future.result())
            except Exception as exc:
                self.sessions[key] = TagSession(store, LoadedProject(ProjectStatus.UNAVAILABLE, reason=str(exc)))
        self.submit(store.load, finished)

    def unbind(self, folder: Path) -> None:
        key = path_identity_key(folder)
        roots = self.protected_roots()
        def finished(future):
            try:
                future.result()
                self.bindings.pop(key, None)
                self.issues.pop(key, None)
            except Exception as exc:
                self.issues[key] = str(exc)
        self.submit(lambda: BindingIndex(protected_roots=roots).update(folder, None), finished)

    def dispose(self) -> None:
        """Noninteractive destruction fallback; never waits for queued GUI delivery."""
        if self.closed:
            return
        for drain in self.producer_drains:
            drain()
        self.closed = True
        self.timer.stop()
        self.executor.shutdown(wait=True)
        for session in self.sessions.values():
            if session.writer:
                session.writer.close()

    def settled(self) -> bool:
        self.poll()
        return not self.tasks and not self.pending_bindings and all(not s.writer or s.writer.flush(0) for s in self.sessions.values())
