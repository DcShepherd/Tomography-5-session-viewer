"""Decode retained capture metadata only against explicitly loaded identities."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from tomography_session_browser.domain.tomograms import VolumeFingerprint
from tomography_session_browser.parsers.volume_header import fingerprint
from tomography_session_browser.presentation.tomogram_movie_history import MovieMark, movie_marks
from tomography_session_browser.presentation.tomogram_persistence import decode_state
from tomography_session_browser.presentation.tomogram_state import TomogramViewState
from tomography_session_browser.presentation.tomogram_tabs import TomogramRow, restored_sort
from tomography_session_browser.presentation.tomogram_versions import review_versions
from tomography_session_browser.services.volume.models import decode_recipe
from tomography_session_browser.services.volume.pixel_sizes import StatedPixelSize, StoredPixelSizes, read_stated
from tomography_session_browser.services.volume.render import plane_geometry
from tomography_session_browser.services.volume.review_repository import MAX_PAGE_SIZE, ReviewRecord, ReviewRepository, record_id
from tomography_session_browser.services.volume.snapshot import CaptureRecord, SnapshotRequest


@dataclass(frozen=True, slots=True)
class CaptureEntry:
    record: CaptureRecord
    state: TomogramViewState
    ordering_notice: str = ""
    identity: str = ""
    created_ns: int = 0
    restore_notice: str = ""

    @property
    def id(self) -> str:
        return self.identity or record_id(str(self.record.path))

    def metadata(self) -> ReviewRecord:
        request = self.record.request
        return ReviewRecord(self.id, (self.created_ns, self.id), {
            "schema": 1, "kind": "capture", "state": asdict(self.state), "output": str(self.record.path),
            "recipe": asdict(request.recipe), "format": request.format, "created_ns": self.created_ns})


@dataclass(frozen=True, slots=True)
class HistoryContext:
    rows: tuple[TomogramRow, ...]
    protected_roots: tuple[Path, ...]

    @property
    def identities(self) -> frozenset[tuple[str, str]]:
        return frozenset((row.tomogram.id, volume.id) for row in self.rows for volume in row.tomogram.volumes)


@dataclass(frozen=True, slots=True)
class CapturePage:
    entries: tuple[CaptureEntry, ...]
    next_cursor: tuple[int, str] | None
    total: int
    counts: tuple[tuple[str, int], ...] = ()
    retained_ids: frozenset[str] = frozenset()
    #: The newest saved movie of each row; None when this query did not read them
    #: (a history page), so the last known marks stay.
    movies: tuple[tuple[str, MovieMark], ...] | None = None


@dataclass(frozen=True, slots=True)
class ScopeHistory:
    offered_state: TomogramViewState | None
    ordering_notice: str
    opened: tuple[tuple[str, bool], ...]
    captures: CapturePage
    pixel_sizes: StoredPixelSizes = field(default_factory=StoredPixelSizes)


def restore_refusal(entry: CaptureEntry, rows: tuple[TomogramRow, ...], *, verify_source: bool = False) -> str:
    """One eligibility policy; actual restore additionally stats the captured source."""
    if entry.restore_notice and not verify_source:
        return entry.restore_notice
    request = entry.record.request
    row = next((r for r in rows if r.tomogram.id == request.row_key), None)
    volume = next((v for v in row.tomogram.volumes if v.id == request.volume.id), None) if row else None
    if volume is not None and volume.version in ("Even", "Odd"):
        return ("Half-volume review is unavailable. The saved capture is kept; the current view was not changed."
                if verify_source else "Half-volume review is unavailable; the capture is kept.")
    if volume is not None and volume.grid is None:
        return ("Captured source has no usable grid. The capture and current view were kept."
                if verify_source else "Captured source has no usable grid. The capture is kept.")
    try:
        actual = fingerprint(volume.path) if volume is not None and verify_source else volume.fingerprint if volume else None
        if actual != request.volume.fingerprint:
            return ("Captured source is missing, changed or outside this scope. The current view was kept."
                    if verify_source else "Captured source is changed or outside this scope. The capture is kept.")
    except OSError as exc:
        return str(exc)
    return ""


def decode_capture(record: ReviewRecord, context: HistoryContext) -> CaptureEntry | None:
    try:
        data = record.data
        state = decode_state(data["state"])
        row = next((row for row in context.rows if row.tomogram.id == state.row_key), None)
        volume = next((v for v in row.tomogram.volumes if v.id == state.volume_id), None) if row else None
        if row is None or volume is None or state.volume_signature is None:
            return None
        output = data["output"]
        if not isinstance(output, str) or not Path(output).is_absolute():
            return None
        recipe = decode_recipe(data["recipe"])
        format_ = data.get("format", "PNG")
        if format_ not in ("PNG", "TIFF", "JPEG") or recipe.axis != state.axis or recipe.plane != state.plane:
            return None
        if recipe.interactive or (recipe.rotation, recipe.pivot_xyz, recipe.pivot_grid_xyz) != (state.rotation, state.pivot_xyz, state.pivot_grid_xyz):
            return None
        unchanged = state.volume_signature == (volume.fingerprint.size, volume.fingerprint.mtime_ns)
        if unchanged and volume.grid is not None:
            nx, ny, nz = plane_geometry(volume, recipe.axis)
            if recipe.region[2] > nx or recipe.region[3] > ny or recipe.plane >= nz:
                return None
        # A changed grid must not erase its older history. Restore/export still
        # refuse that captured fingerprint before any use of its old geometry.
        volume = replace(volume, fingerprint=VolumeFingerprint(volume.path, *state.volume_signature))
        path = Path(output)
        request = SnapshotRequest(row.tomogram.id, row.tomogram.name, volume, recipe,
                                  path.parent, context.protected_roots, format_)
        _, notice = restored_sort(data["state"].get("sort", "Name"))
        return CaptureEntry(CaptureRecord(path, request), state, notice, record.identity, record.order[0])
    except (TypeError, ValueError, KeyError, RecursionError):
        return None


def capture_page(repository: ReviewRepository, context: HistoryContext, *, before: tuple[int, str] | None = None,
                 limit: int = 50, extra: tuple[ReviewRecord, ...] = (),
                 retain_ids: frozenset[str] = frozenset(), with_movies: bool = False,
                 cancelled: Callable[[], bool] = lambda: False) -> CapturePage:
    # Reconcile a bounded set of cross-page identities during the existing worker
    # scan, without keeping all records or rescanning the repository on the GUI.
    if len(retain_ids) > MAX_PAGE_SIZE:
        raise ValueError(f'At most {MAX_PAGE_SIZE} capture identities may be retained per query.')
    retained = set()
    def accept(record: ReviewRecord) -> bool:
        valid = decode_capture(record, context) is not None
        if valid and record.identity in retain_ids:
            retained.add(record.identity)
        return valid
    page = repository.page("capture", context.identities, before=before, limit=limit, extra=extra,
                           accept=accept, cancelled=cancelled)
    entries = []
    current: dict[Path, VolumeFingerprint | None] = {}
    for record in page.records:
        if cancelled():
            raise InterruptedError("Obsolete history query")
        entry = decode_capture(record, context)
        if entry is None:
            continue
        volume = entry.record.request.volume
        if volume.path not in current:
            try:
                current[volume.path] = fingerprint(volume.path)
            except (OSError, ValueError):
                current[volume.path] = None
        notice = ""
        if current[volume.path] is None:
            notice = "Captured source is missing or unreadable. The capture is kept."
        elif current[volume.path] != volume.fingerprint:
            notice = "Captured source changed. The capture is kept."
        entries.append(replace(entry, restore_notice=notice))
    movies = movie_marks(repository, context.identities, cancelled=cancelled) if with_movies else None
    return CapturePage(tuple(entries), page.next_cursor, page.total, page.counts, frozenset(retained), movies)


def scope_history(repository: ReviewRepository, context: HistoryContext, *, extra: tuple[ReviewRecord, ...] = (),
                  cancelled: Callable[[], bool] = lambda: False) -> ScopeHistory:
    # Pixel sizes reach every loaded version, so they are read for any scope, also an empty one.
    stated = read_stated(repository, cancelled=cancelled)
    if not context.identities:
        return ScopeHistory(None, "", (), CapturePage((), None, 0, movies=()), stated)
    # A view saved before pixel sizes had their own records still holds a confirmed
    # value. It counts until a record speaks for that file, and then the record wins.
    saved: dict[str, StatedPixelSize] = {}
    rows = {row.tomogram.id: row for row in context.rows}
    candidate: tuple[tuple[int, str], TomogramViewState, str] | None = None
    opened = set()
    for record in repository.iter_records("view", cancelled=cancelled):
        try:
            state = decode_state(record.data["state"])
            if (state.volume_id and state.volume_signature is not None and state.spacing_override is not None
                    and state.volume_id not in stated.entries and record_id(state.volume_id) not in stated.rejected):
                saved[state.volume_id] = StatedPixelSize(state.volume_id, state.volume_signature, state.spacing_override)
            row = rows.get(state.row_key or "")
            volume = next((v for v in review_versions(row.tomogram) if v.id == state.volume_id), None) if row else None
            if row is None or volume is None:
                continue
            _, notice = restored_sort(record.data["state"].get("sort", "Name"))
            if candidate is None or record.order > candidate[0]:
                candidate = record.order, state, notice
            if state.volume_signature == (volume.fingerprint.size, volume.fingerprint.mtime_ns) and record.data.get("opened") is True:
                opened.add(row.tomogram.id)
        except (TypeError, ValueError, KeyError, RecursionError):
            continue
    explicit: dict[str, tuple[tuple[int, str], bool]] = {}
    for record in repository.iter_records("opened", cancelled=cancelled):
        try:
            state = decode_state(record.data["state"])
            row = rows.get(state.row_key or "")
            volume = row.tomogram.volumes[0] if row and row.tomogram.volumes else None
            if row is None or volume is None or state.volume_signature != (volume.fingerprint.size, volume.fingerprint.mtime_ns):
                continue
            key = row.tomogram.id
            if key not in explicit or record.order > explicit[key][0]:
                explicit[key] = record.order, record.data.get("opened") is True
        except (TypeError, ValueError, KeyError, RecursionError):
            continue
    updates = {key: True for key in opened}
    updates.update({key: value[1] for key, value in explicit.items()})
    return ScopeHistory(candidate[1] if candidate else None, candidate[2] if candidate else "",
                        tuple(updates.items()), capture_page(repository, context, limit=200, extra=extra,
                                                                             with_movies=True, cancelled=cancelled),
                        StoredPixelSizes({**saved, **stated.entries}, stated.rejected))
