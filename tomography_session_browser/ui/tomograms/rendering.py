"""Latest-request rendering; QWidget facade receives worker completions."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from threading import Event
import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QThreadPool, QTimer, Signal
from PySide6.QtGui import QImage

from tomography_session_browser.domain.tomograms import Volume
from tomography_session_browser.parsers.volume_header import fingerprint, is_cloud_placeholder
from tomography_session_browser.services.volume.models import RenderRecipe, RenderResult
from tomography_session_browser.services.volume.render import intersection_point, render_xy, plane_geometry
from tomography_session_browser.ui.loading.tasks import BackgroundTask, TaskSignals
from tomography_session_browser.presentation.tomogram_geometry import (AXES, FILTERS_DEFERRED, plane_caption,
                                                                       reserved_captions, reserved_cursor_readouts)
from tomography_session_browser.presentation.tomogram_render_plan import PaneViewport, RequestedPlane, render_plan

if TYPE_CHECKING:
    from tomography_session_browser.ui.tomograms.tab import TomogramTab


@dataclass(frozen=True, slots=True)
class RenderJob:
    generation: int
    row_key: str
    volume: Volume
    recipe: RenderRecipe
    position: tuple[int, int]
    companions: tuple[tuple[RenderRecipe, tuple[int, int]], ...] = ()
    cross_xyz: tuple[int, int, int] | None = None
    lines: bool = True
    cancelled: Event = field(default_factory=Event, repr=False, compare=False)
    include_histogram: bool = True


def render_job(job: RenderJob, publish_active=None, *, reusable_frames: tuple[RenderResult, ...] = ()) -> RenderResult | None:
    def plane(recipe: RenderRecipe, histogram: bool) -> RenderResult:
        cached = next((frame for frame in reusable_frames if frame.recipe == recipe
                       and (not histogram or frame.histogram is not None)), None)
        return cached if cached is not None else render_xy(job.volume, recipe, cancelled=job.cancelled.is_set,
                                                           include_histogram=histogram)
    try:
        if reusable_frames and (is_cloud_placeholder(job.volume.path) or fingerprint(job.volume.path) != job.volume.fingerprint):
            raise ValueError("The volume is unavailable or changed. Reload the processing folder.")
        active = plane(job.recipe, job.include_histogram)
        if job.companions and publish_active is not None and not job.cancelled.is_set():
            publish_active(job, active)
        companions = []
        for recipe, position in job.companions:
            if job.cancelled.is_set():
                return None
            recipe = replace(recipe, black=active.recipe.black, white=active.recipe.white,
                             levels_anchor=active.recipe.levels_anchor)
            # An active-plane step often changes only the crosshair in the
            # other panes. Their already leased pixels remain valid only for
            # the exact recipe on the same verified source.
            frame = plane(recipe, False)
            companions.append((recipe.axis, frame, position))
        if reusable_frames and fingerprint(job.volume.path) != job.volume.fingerprint:
            raise ValueError("The volume changed while rendering. Reload the processing folder.")
        return None if job.cancelled.is_set() else replace(active, companions=tuple(companions))
    except InterruptedError:
        if not job.cancelled.is_set():
            raise
        return None


@dataclass(frozen=True, slots=True)
class AcceptedFrame:
    job: RenderJob
    result: RenderResult
    complete: bool = True


class VolumeTaskSignals(TaskSignals):
    active_ready = Signal(object, object)


class VolumeRendering:
    """One render runs; the latest request waits; nothing older than the epoch is shown.

    A view-only change (plane, pan, zoom) keeps the running render: its frame is
    still shown as a preview, so scrubbing and held keys update at render speed
    instead of freezing until input stops. Any other change starts a new epoch,
    cancels the running render and never shows its pixels. Only the frame that
    matches the latest request is *current*: it alone sets levels, marks a row
    opened, prefetches, paces playback and allows a snapshot.
    """

    #: How long a requested plane may take before "Loading plane…" is shown.
    SLOW_MS = 250

    def __init__(self, tab: TomogramTab) -> None:
        self.tab = tab
        self.signals = VolumeTaskSignals()
        self.signals.finished.connect(tab._render_finished)
        self.signals.failed.connect(tab._render_failed)
        self.signals.active_ready.connect(tab._render_active)
        self.pool = QThreadPool(tab)
        self.pool.setMaxThreadCount(1)
        self.generation = 0
        self.epoch = 0
        self.running: RenderJob | None = None
        self.pending: RenderJob | None = None
        self.accepted: AcceptedFrame | None = None
        self.slow = QTimer(tab)
        self.slow.setSingleShot(True)
        self.slow.setInterval(self.SLOW_MS)
        self.slow.timeout.connect(self.show_slow)

    def invalidate(self) -> None:
        self.supersede()
        self.slow.stop()
        self.accepted = None
        self.tab.stage.clear()

    def supersede(self, *, keep_running: bool = False) -> None:
        """Retire work as soon as input arrives, before the debounce expires.

        ``keep_running`` is for view-only changes: the running render may still
        finish and be shown as a preview of the same epoch.
        """
        self.generation += 1
        self.pending = None
        if not keep_running:
            self.epoch = self.generation
            if self.running is not None:
                self.running.cancelled.set()
        self.tab.prefetch.cancel()

    def is_current(self) -> bool:
        accepted = self.accepted
        return accepted is not None and accepted.job.generation == self.generation

    def displayed_recipe(self) -> RenderRecipe | None:
        """Geometry follows the active pane's pixels, including during selection."""
        accepted, frame = self.accepted, self.tab.canvas.frame_owner
        if (accepted is not None and accepted.job.volume == self.tab.volume()
                and accepted.job.row_key == self.tab.state.row_key and isinstance(frame, RenderResult)
                and frame.recipe.axis == self.tab.state.axis):
            return frame.recipe
        return None

    def show_slow(self) -> None:
        if not self.is_current() and self.tab.volume() is not None:
            self.tab.status.message.note("Refining…" if any(self.tab.state.rotation) else "Loading plane…")

    def request(self) -> None:
        tab, volume = self.tab, self.tab.volume()
        if volume is None or volume.grid is None or not tab.isVisible():
            return
        tab.residency.ensure()
        if self.running is not None and self.running.generation < self.epoch:
            self.running.cancelled.set()
        self.generation += 1
        try:
            requests = self.plan()
            active = requests[0]
            cross = tab.state.cross_xyz or tuple(n // 2 for n in volume.grid.shape_xyz)
            self.pending = RenderJob(self.generation, tab.state.row_key or "", volume, active.recipe, active.position,
                tuple((request.recipe, request.position) for request in requests[1:]), cross,
                tab.state.ortho_lines and tab.state.layout == "Ortho",
                include_histogram=tab.image_settings.isVisible())
            tab.stage_transition.requested(self.pending)
            # A quick step never flashes a loading note; a slow one says so.
            self.slow.start()
            self.start_next()
        except ValueError as exc:
            tab.status.message.note(str(exc))

    def plan(self, *, refined: bool = False) -> tuple[RequestedPlane, ...]:
        tab, volume = self.tab, self.tab.volume()
        if volume is None:
            raise ValueError("Select a tomogram before requesting an image.")
        axes = [tab.state.axis] + ([axis for axis in ("XY", "XZ", "YZ") if axis != tab.state.axis]
                                  if tab.state.layout == "Ortho" else [])
        panes = tuple(PaneViewport(axis, (max(1, tab.stage.panes[axis].width()),
                                         max(1, tab.stage.panes[axis].height())),
                                   tab.stage.panes[axis].devicePixelRatioF()) for axis in axes)
        requests = render_plan(tab.state, volume, panes, interactive=tab.oblique.interactive and not refined)
        accepted = self.accepted
        if (accepted is not None and accepted.job.volume == volume
                and accepted.job.row_key == tab.state.row_key):
            levels = accepted.result.recipe
            requests = tuple(replace(request, recipe=replace(request.recipe,
                black=levels.black, white=levels.white, levels_anchor=levels.levels_anchor))
                if request.recipe.interactive and request.recipe.black is None
                and request.recipe.white is None and request.recipe.levels_mode == levels.levels_mode
                else request for request in requests)
        return requests

    def start_next(self) -> None:
        if self.running is not None or self.pending is None:
            return
        job, self.pending = self.pending, None
        self.running = job
        accepted = self.accepted
        reusable = ((replace(accepted.result, companions=()), *(frame for _, frame, _ in accepted.result.companions))
            if accepted is not None and accepted.complete and accepted.job.volume == job.volume
            and accepted.job.row_key == job.row_key else ())
        # Candidates live only with this worker, not with the next accepted job.
        self.pool.start(BackgroundTask(lambda: render_job(job, self.signals.active_ready.emit,
                                       reusable_frames=reusable), context=job,
                                       signals=self.signals, logger=logging.getLogger(__name__),
                                       failure_log="Volume rendering failed"))

    def displayable(self, job: RenderJob) -> bool:
        accepted = self.accepted
        return job.generation >= self.epoch and (accepted is None or job.generation >= accepted.job.generation)

    def active_ready(self, job: RenderJob, result: RenderResult) -> None:
        # Ortho: the side panes keep their last pixels until this job's arrive,
        # rather than blanking on every step.
        if self.running is job and self.displayable(job):
            self.publish(job, result, complete=False)

    def finished(self, job: RenderJob, result: RenderResult | None) -> None:
        if self.running is not job:
            return
        self.running = None
        if result is not None and self.displayable(job):
            self.publish(job, result)
        self.start_next()

    def publish(self, job: RenderJob, result: RenderResult, *, complete: bool = True) -> None:
        self.accepted = AcceptedFrame(job, result, complete)
        current = job.generation == self.generation
        for axis, frame, position in ((result.recipe.axis, result, job.position), *result.companions):
            pixels = frame.pixels
            image = QImage(pixels.data, pixels.shape[1], pixels.shape[0], pixels.strides[0],
                           QImage.Format.Format_RGB888 if pixels.ndim == 3 else QImage.Format.Format_Grayscale8).copy()
            image.setDevicePixelRatio(frame.recipe.pixel_ratio)
            self.tab.stage.panes[axis].accept_image(image, position, frame.recipe.region, frame)
            self.tab.stage.panes[axis].navigator.shape = plane_geometry(job.volume, axis)[:2]
            self.tab.stage.panes[axis].sync_navigator()
            h, v, _ = AXES[axis]
            pivot = frame.recipe.pivot_grid_xyz
            self.tab.stage.panes[axis].pivot_point = ((pivot[h]+.5, job.volume.grid.shape_xyz[v]-pivot[v]-.5)
                if pivot is not None and job.volume.grid else None)
            self.tab.stage.panes[axis].crosshair = (intersection_point(axis, job.cross_xyz, job.volume.grid.shape_xyz)
                                                     if job.lines and job.cross_xyz and job.volume.grid else None)
        final = current and complete and not result.recipe.interactive
        if current and not result.recipe.interactive:
            self.tab.state = replace(self.tab.state, black=result.recipe.black, white=result.recipe.white, levels_anchor=result.recipe.levels_anchor)
        if final:
            self.tab.review_list.mark_opened(job.row_key)
        assert job.volume.grid is not None
        axis = result.recipe.axis
        ortho = self.tab.state.layout == "Ortho"
        # The readout names the displayed plane, preview or not; the slider is the request.
        readout = plane_caption(axis, result.recipe.plane + 1, plane_geometry(job.volume, axis)[2],
                                ortho=ortho, oblique=any(result.recipe.rotation))
        if result.recipe.interactive and (result.recipe.lowpass or result.recipe.highpass):
            readout += FILTERS_DEFERRED
        self.tab.readout.reserve(reserved_captions(job.volume, result.recipe, ortho=ortho))
        self.tab.reserve_cursor(reserved_cursor_readouts(job.volume, result.recipe))
        self.tab.readout.setText(readout)
        self.tab.readout.setAccessibleName(f"{axis} plane, {readout}")
        if final:
            # Only the finished, requested frame rewrites the feedback line, so
            # steps never alternate it with a loading note.
            self.slow.stop()
            self.tab.status.message.note("Uncalibrated — check Image settings" if result.calibration == "Unresolved"
                                      else result.bar_notice or ("Display calibration override" if result.calibration == "User confirmed" else ""))
        self.tab.oblique.sync_display()
        self.tab.stage.corner.sync()
        if current:
            self.tab.image_settings.sync_levels(result.recipe.black, result.recipe.white)
        self.tab.image_settings.histogram.set_data(result.histogram, result.recipe.black or 0., result.recipe.white if result.recipe.white is not None else 1.)
        self.tab.image_settings.refresh_size_note()
        self.tab.image_settings.refresh_relevel()
        if self.tab.image_settings.isVisible():
            self.tab.image_settings.update_memory()
        if current and not result.recipe.interactive:
            self.tab.recipes.remember_levels()
        x0, _, x1, _ = result.recipe.region
        self.tab.zoom_choice.setText(f"{100 * result.recipe.output_size[0] / (x1-x0):.0f}%")
        self.tab.slider.project()
        self.tab.update_capture_command()
        self.tab.stage_transition.published(job)
        self.tab.displayed_changed.emit()
        if not final:
            return  # A preview: the requested frame will prefetch, pace playback and save.
        self.tab.persistence.schedule()
        if final:
            self.tab.captures.frame_ready()
            self.tab.movie.frame_ready()
        if complete:
            self.tab.prefetch.request()
            self.tab.playback.frame_accepted()

    def failed(self, job: RenderJob, message: str) -> None:
        if self.running is not job:
            return
        self.running = None
        if job.generation == self.generation:
            self.slow.stop()
            self.tab.playback.stop()
            self.tab.status.message.note(message)
            self.tab.update_commands()
        self.start_next()
