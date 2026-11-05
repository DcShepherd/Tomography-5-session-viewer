"""Own stored statements and the calibration projected into the current view."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING

from PySide6.QtCore import QThreadPool

from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.domain.tomograms import Volume
from tomography_session_browser.presentation.tomogram_calibration import (BulkReview, bulk_review, calibration_note, offered_count,
                                                                          open_warnings)
from tomography_session_browser.presentation.tomogram_state import TomogramViewState
from tomography_session_browser.services.volume.pixel_sizes import KIND, PixelSizes, Signature, StatedPixelSize, StoredPixelSizes, new_operation, signature_of
from tomography_session_browser.services.volume.review_writer import ReviewBatch, ReviewWrite
from tomography_session_browser.ui.loading.tasks import TaskSignals

if TYPE_CHECKING:
    from tomography_session_browser.ui.tomograms.tab import TomogramTab

#: Below this the review list has no use: Apply to this version does the same.
SEVERAL = 2
KEPT_AFTER_RESTART = "The app keeps these values after a restart. If a file changes, the app asks again for that file."


@dataclass(frozen=True, slots=True)
class RestoredCalibration:
    volume_id: str
    signature: Signature
    value: float | None


@dataclass(frozen=True, slots=True)
class BulkOperation:
    id: str
    value: float
    volumes: tuple[Volume, ...]


def applied_text(value: float, count: int) -> str:
    return f"{value:.3f} Å/voxel applied to {count_phrase(count, 'version')} · User confirmed"


def undone_text(value: float, undone: int, kept: int) -> str:
    text = f"{count_phrase(undone, 'version')} no longer {'uses' if undone == 1 else 'use'} {value:.3f} Å/voxel."
    if kept:
        text += (f" {count_phrase(kept, 'version')} changed after that and "
                 f"{'stays as it is' if kept == 1 else 'stay as they are'}.")
    return text


class PixelSizeOwner:
    def __init__(self, tab: TomogramTab) -> None:
        self.tab = tab
        self.values = PixelSizes()
        self.loaded = False
        self.restored: RestoredCalibration | None = None
        self.last: BulkOperation | None = None
        self.strip_sources: tuple | None = None
        self.warned = ""
        self.changed: set[tuple[str, tuple[int, int]]] = set()
        self.dialog = None
        # One lane for the fingerprint check; TomogramWorkers drains it on close.
        self.pool = QThreadPool(tab)
        self.pool.setMaxThreadCount(1)
        self.signals = TaskSignals()
        self.count_key: tuple | None = None
        self.count = 0
        self.published: dict[str, tuple[Signature, float]] = {}

    def is_changed(self, volume: Volume) -> bool:
        return (volume.id, signature_of(volume)) in self.changed

    def displayed(self, volume: Volume) -> float | None:
        marker = self.restored
        if marker is not None and (marker.volume_id, marker.signature) == (volume.id, signature_of(volume)):
            return marker.value
        return self.values.value_for(volume)

    def restore(self, state: TomogramViewState, volume: Volume) -> None:
        self.restored = RestoredCalibration(volume.id, signature_of(volume), state.spacing_override)
        self.tab.recipes.forget_version(volume.id, signature_of(volume))

    def sources(self) -> tuple:
        payload = self.tab.payload
        return payload.loaded_sources or payload.sources

    def review(self) -> BulkReview:
        return bulk_review(self.sources(), self.values.value_for, self.is_changed)

    def offered(self) -> int:
        """Cached: the commands refresh often, and a loaded project can hold hundreds of versions."""
        key = (self.values.revision, self.sources(), len(self.changed))
        if key != self.count_key:
            self.count_key = key
            self.count = offered_count(key[1], self.values.value_for, self.is_changed)
        return self.count

    def available(self) -> bool:
        return self.loaded and self.offered() >= SEVERAL

    def sync_entries(self) -> None:
        before = self.count_key
        self.offered()  # Also before storage has answered: a value confirmed by then still counts.
        available = self.available()
        self.tab.pixel_prompt.several.setVisible(available)
        self.tab.image_settings.several_button.setVisible(available)
        if self.count_key != before:
            self.sync_warnings()

    def sync_warnings(self) -> None:
        """The open version's warning here; the list and the Summary only when a value changed."""
        tab, volume = self.tab, self.tab.volume()
        warnings = open_warnings(volume, self.displayed(volume)) if volume is not None else ()
        tab.volume_warning.setText(" · ".join(warnings))
        tab.volume_warning.setVisible(bool(warnings))
        if volume is not None:
            value = self.displayed(volume)
            note = calibration_note(volume, value)
            if value is None and self.values.value_for(volume) is not None:
                note += "\nThis capture uses its original calibration. A confirmed value is available for the file."
            tab.image_settings.calibration_note.setText(note)
        stated = self.stated_now()
        if stated != self.published:
            self.published = stated
            tab.review_state.set_stated(stated)
            tab.review_list.stated_values_changed()
            tab.review_metadata_changed.emit()

    def stated_now(self) -> dict[str, tuple[Signature, float]]:
        return {entry.volume_id: (entry.signature, entry.value)
                for entry in self.values.entries.values() if entry.value is not None}

    @staticmethod
    def with_value(state: TomogramViewState, value: float | None) -> TomogramViewState:
        if value == state.spacing_override:
            return state
        # Linked scale is px/Å and belongs to the calibration it was measured with.
        return replace(state, spacing_override=value, linked_scale=None)

    def seeded(self, state: TomogramViewState, volume: Volume | None) -> TomogramViewState:
        return self.with_value(state, self.displayed(volume) if volume is not None else None)

    def sync_field(self, volume: Volume) -> None:
        if volume.grid is not None:
            self.tab.image_settings.spacing.setValue(
                self.tab.state.spacing_override or volume.grid.spacing_xyz[0] or 1.
            )

    def show_calibration(self, volume: Volume) -> None:
        tab = self.tab
        tab.state = self.seeded(tab.state, volume)
        self.sync_field(volume)
        tab.image_settings.sync_state()
        self.sync_warnings()
        tab.update_commands()
        tab.request_render()
        tab.metadata_changed.emit()

    def confirm(self, value: float | None) -> None:
        volume = self.tab.volume()
        if volume is None:
            return
        self.tab.playback.stop()
        previous = self.values.value_for(volume)
        self.restored = None
        if previous != value:
            self.tab.recipes.remember_calibration(volume, previous)
            self.write_value(volume, value)
        self.show_calibration(volume)

    def write_value(self, volume: Volume, value: float | None) -> None:
        entry = self.values.state(volume, value, new_operation())
        self.tab.persistence.write(KIND, volume.id, entry)

    def apply(self, volumes: tuple[Volume, ...], value: float) -> None:
        operation = new_operation()
        self.bulk_steps(volumes, value)
        entries = tuple(self.values.state(volume, value, operation) for volume in volumes)
        self.last = BulkOperation(operation, value, volumes)
        self.announce(applied_text(value, len(volumes)), undo=True, tooltip=KEPT_AFTER_RESTART)
        self.save(entries)
        self.publish_bulk(volumes)

    def undo(self) -> None:
        """Reverse exactly the last bulk action: a value set before or after it stays."""
        applied, self.last = self.last, None
        if applied is None:
            return
        owned = tuple(volume for volume in applied.volumes
                      if (entry := self.values.entry(volume)) is not None and entry.operation == applied.id)
        self.announce(undone_text(applied.value, len(owned), len(applied.volumes) - len(owned)))
        operation = new_operation()
        self.bulk_steps(owned, None)
        if owned:
            self.save(tuple(self.values.state(volume, None, operation) for volume in owned))
        self.publish_bulk(owned)

    def bulk_steps(self, volumes: tuple[Volume, ...], value: float | None) -> None:
        reached = {volume.id for volume in volumes}
        current = self.tab.volume()
        if current is not None and current.id in reached:
            reached.remove(current.id)
            previous = self.values.value_for(current)
            self.restored = None
            if previous != value:
                self.tab.recipes.remember_calibration(current, previous)
        self.tab.recipes.forget_statements(reached)

    def publish_bulk(self, volumes: tuple[Volume, ...]) -> None:
        current = self.tab.volume()
        if current is not None and any(volume.id == current.id for volume in volumes):
            self.tab.playback.stop()
            self.show_calibration(current)
        else:
            self.sync_warnings()
            self.tab.update_commands()

    def announce(self, text: str, *, undo: bool = False, warn: bool = False, tooltip: str = "") -> None:
        """The strip, not the status line: the next finished render rewrites the status line."""
        self.strip_sources = self.sources()
        self.tab.pixel_applied.show_text(text, undo=undo, warn=warn, tooltip=tooltip)

    def warn(self, message: str) -> None:
        """A problem joins the action it belongs to, and Undo stays available."""
        applied = self.last
        if applied is None:
            self.announce(message, warn=True)
        else:
            self.announce(f"{applied_text(applied.value, len(applied.volumes))} · {message}", undo=True, warn=True)

    def warn_once(self, message: str) -> None:
        """Storage is read again for each scope. A message the user closed stays closed."""
        if message != self.warned:
            self.warned = message
            self.warn(message)

    def dismiss(self) -> None:
        self.last = self.strip_sources = None
        self.tab.pixel_applied.hide()

    def save(self, entries: tuple[StatedPixelSize, ...]) -> None:
        persistence = self.tab.persistence
        batch = ReviewBatch(tuple(ReviewWrite(persistence.repository, KIND, entry.volume_id, asdict(entry), {})
                                  for entry in entries))
        try:
            persistence.watch(persistence.writer.submit_batch(batch), batch)
        except (OSError, ValueError):
            self.not_saved(batch.writes)

    def not_saved(self, writes: tuple[ReviewWrite, ...]) -> None:
        current = sum((entry := self.values.entries.get(write.identity)) is not None
                      and entry.operation == write.state.get("operation") for write in writes)
        earlier = len(writes) - current
        messages = []
        if current:
            messages.append(f"{count_phrase(current, 'pixel-size change')} could not be saved. "
                            "The current session keeps your changes. Previously saved values may return after a restart.")
        if earlier:
            messages.append(f"{count_phrase(earlier, 'earlier pixel-size change')} could not be saved. "
                            "Later changes replaced them in this session." if earlier > 1 else
                            "An earlier pixel-size change could not be saved. A later change replaced it in this session.")
        if messages:
            self.warn(" ".join(messages))

    def arrived(self, read: StoredPixelSizes) -> None:
        """Storage has answered: seed the open view, and let the prompt decide."""
        unreadable = self.values.merge(read)
        self.loaded = True
        if self.strip_sources is not None and self.sources() != self.strip_sources:
            self.dismiss()  # Those versions are no longer what is loaded, so Undo cannot speak for them.
        if unreadable:
            self.warn_once(f"{count_phrase(unreadable, 'saved pixel size')} could not be read. "
                           "The app asks again for those versions.")
        tab, volume = self.tab, self.tab.volume()
        if volume is not None and self.displayed(volume) != tab.state.spacing_override:
            self.show_calibration(volume)
        else:
            self.sync_warnings()
            tab.update_commands()
        tab.image_settings.refresh_actions()

    def read_failed(self) -> None:
        self.loaded = True
        self.warn_once("Saved pixel sizes could not be read. Values already loaded stay in use.")
        self.tab.update_commands()

    def open_review(self, value: float) -> None:
        from tomography_session_browser.ui.tomograms.bulk_pixel_dialog import BulkPixelDialog
        if self.dialog is not None:
            return
        self.dialog = dialog = BulkPixelDialog(self, value)

        def closed(_result: int) -> None:
            self.dialog = None
            dialog.deleteLater()

        dialog.finished.connect(closed)
        dialog.open()
