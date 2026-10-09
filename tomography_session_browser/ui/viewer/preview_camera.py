"""Preview camera for the image viewer."""
from __future__ import annotations
from collections.abc import Callable
import math
import time
from typing import Any
import shiboken6
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QTransform
from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.ui.motion import MarkerPulse, camera_duration_ms, motion_enabled, zoom_pan_path
from tomography_session_browser.services.marker_clustering import MAX_ATLAS_ZOOM
from tomography_session_browser.ui.theme import current_palette
import logging
LOGGER = logging.getLogger("tomography_session_browser.ui.image_viewer")
from tomography_session_browser.ui.viewer.constants import ARRIVAL_FRAME_SHARE, BUTTON_ZOOM_STEP, FIT_IN_VIEW_MARGIN_PX, PULSE_MAX_RADIUS_PX, PULSE_MIN_RADIUS_PX, REFIT_EASE_ARM_S, VIEWPORT_MEMORY_LIMIT
from tomography_session_browser.ui.viewer.marker_paint import marker_scene_geometry
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from tomography_session_browser.ui.viewer.preview_view import ImagePreviewView


class PreviewCamera:
    """Preview camera operations for one ImagePreviewView."""

    def __init__(self, view: ImagePreviewView) -> None:
        self.view = view

    def zoom_scale(self) -> float:
        return self.view._zoom_scale()

    def zoom_in(self) -> None:
        self.view._scale_by_center(BUTTON_ZOOM_STEP)

    def zoom_out(self) -> None:
        self.view._scale_by_center(1.0 / BUTTON_ZOOM_STEP)

    def ease_zoom_by(self, factor: float) -> None:
        """A zoom button's step, eased as the wheel's is, about the centre.

        The buttons cut while the wheel eased (plan F5.1 kept them instant).
        Clicks during a step add up, as notches do, and the zoom goes on from
        wherever a refit or flight had got to; reduced motion jumps. The
        instant ``zoom_in``/``zoom_out`` remain for code that needs the view
        changed at once.
        """

        if self.view.parts.preview_image.pixmap_item is None:
            return
        self.view._zoom_toward(factor, QPointF(self.view.viewport().rect().center()))

    def ease_to_fit(self) -> None:
        """The Fit button: ease back to the fit and land exactly on it.

        As a side panel's ride-along does (plan F5.2): only the view
        transform moves. ``fit_image`` stays instant for code.
        """

        if self.view.parts.preview_image.pixmap_item is None:
            return
        if not motion_enabled(self.view) or self.view.is_fit_mode:
            self.view.fit_image()
            return
        self.view._mark_user_interacted("fit")
        self.view._stop_view_motion()
        self.view._close_cluster_popup()
        self.fit_mode = True
        self.view._ease_to_fit()

    def set_atlas_zoom(self, zoom: float) -> None:
        if not self.view.parts.marker_layer.atlas_lod_enabled or self.view.parts.preview_image.pixmap_item is None:
            return
        target = min(MAX_ATLAS_ZOOM, max(1.0, float(zoom)))
        if target <= 1.0 + 1e-9:
            self.view.fit_image()
            return
        self.view._scale_by_center(target / max(self.zoom, 0.01))

    def fit_image(self) -> None:
        self.view._mark_user_interacted("fit")
        self.view._fit_image()

    def maybe_fit_initial_view(self) -> None:
        if not self.pending_initial_fit_flag or self.has_user_interacted:
            LOGGER.debug(
                "skip initial fit pending=%s user_interacted=%s logical_key=%s",
                self.pending_initial_fit_flag,
                self.has_user_interacted,
                self.view.parts.preview_image.current_image_key,
            )
            return
        self.view._fit_image()
        self.pending_initial_fit_flag = False

    def remember_current_view(self) -> None:
        """Store the current crop against the image being displayed.

        Only a view the user actually changed is worth restoring; a fitted
        view is reproduced exactly by fitting again, and remembering it would
        pin an image to a stale window size.
        """

        if self.view.parts.preview_image.current_image_key is None or self.view.parts.preview_image.pixmap_item is None:
            return
        memory_key = self.view._view_memory_key(self.view.parts.preview_image.current_image_key)
        if self.view_group_of is not None and memory_key != self.active_view_group:
            return  # a series already left is not remembered
        if not self.has_user_interacted or self.view.is_fit_mode:
            self.view_memory.pop(memory_key, None)
            return
        rect = self.view.mapToScene(self.view.viewport().rect()).boundingRect()
        item_rect = self.view.parts.preview_image.pixmap_item.boundingRect()
        if item_rect.width() <= 0 or item_rect.height() <= 0:
            return
        # Stored proportionally so a resized window, or a different pyramid
        # level of the same image, restores the same region rather than the
        # same pixel coordinates.
        self.view_memory[memory_key] = (
            rect.left() / item_rect.width(),
            rect.top() / item_rect.height(),
            rect.width() / item_rect.width(),
            rect.height() / item_rect.height(),
        )
        self.view_memory.move_to_end(memory_key)
        while len(self.view_memory) > VIEWPORT_MEMORY_LIMIT:
            self.view_memory.popitem(last=False)

    def share_view_within_groups(self, group_of: Callable[[Any], Any]) -> None:
        """Carry the crop between the images of one group, and only there.

        For the frames of a tilt series: a reviewer zooms in on something
        small and follows it through the tilt, so each frame opens where the
        last was left. ``group_of`` maps a logical image key to its group
        (the series). Another group opens fitted, and a group that was left
        is not remembered, so returning to a series shows it fitted. Without
        this, each image keeps its own crop.
        """

        self.view_group_of = group_of
        self.view_memory.clear()

    def begin_view_group(self, group: Any) -> None:
        """Another group (a tilt series) is being shown: forget the last one's crop."""

        if group != self.active_view_group:
            self.active_view_group = group
            self.view_memory.clear()

    def discard_user_view(self) -> None:
        """Forget every remembered crop and show the current image fitted,
        as if it had just been opened."""

        self.view_memory.clear()
        if self.view.parts.preview_image.pixmap_item is None or self.pending_initial_fit_flag:
            return
        if self.view.is_fit_mode and not self.has_user_interacted:
            return
        self.has_user_interacted = False
        self.view._fit_image()

    def view_memory_key(self, logical_image_key: Any) -> Any:
        if self.view_group_of is None:
            return logical_image_key
        return self.view_group_of(logical_image_key)

    def restore_remembered_view(self, logical_image_key: Any) -> bool:
        """Re-apply a remembered crop for ``logical_image_key``."""

        remembered = self.view_memory.get(self.view._view_memory_key(logical_image_key))
        if remembered is None or self.view.parts.preview_image.pixmap_item is None:
            return False
        item_rect = self.view.parts.preview_image.pixmap_item.boundingRect()
        if item_rect.width() <= 0 or item_rect.height() <= 0:
            return False
        left, top, width, height = remembered
        if width <= 0 or height <= 0:
            return False
        target = QRectF(
            left * item_rect.width(),
            top * item_rect.height(),
            width * item_rect.width(),
            height * item_rect.height(),
        )
        viewport_rect = self.view.viewport().rect()
        if viewport_rect.width() <= 0 or viewport_rect.height() <= 0:
            return False
        # Set the transform directly rather than calling fitInView: that
        # reserves room for the frame, so a round trip drifts a few percent
        # wider each time instead of landing back on the same crop.
        scale = min(
            viewport_rect.width() / target.width(),
            viewport_rect.height() / target.height(),
        )
        if scale <= 0:
            return False
        self.view.resetTransform()
        self.view.scale(scale, scale)
        self.view.centerOn(target.center())
        self.pending_initial_fit_flag = False
        self.has_user_interacted = True
        # A remembered crop is the user's own framing, not a fit to follow.
        self.fit_mode = False
        visible = self.view.mapToScene(viewport_rect).boundingRect()
        self.zoom = item_rect.width() / visible.width() if visible.width() > 0 else 1.0
        return True

    def forget_remembered_views(self) -> None:
        """Drop all viewport memory (session replaced or removed)."""

        self.view_memory.clear()

    def view_range(self) -> tuple[tuple[float, float], tuple[float, float]] | None:
        if self.view.parts.preview_image.pixmap_item is None:
            return None
        rect = self.view.mapToScene(self.view.viewport().rect()).boundingRect()
        return ((rect.left(), rect.right()), (rect.top(), rect.bottom()))

    @property
    def current_logical_image_key(self) -> Any | None:
        return self.view.parts.preview_image.current_image_key

    @property
    def pending_initial_fit(self) -> bool:
        return self.pending_initial_fit_flag

    @property
    def is_fit_mode(self) -> bool:
        """Whether an image is shown and the view is tracking fit-to-viewport."""

        return self.fit_mode and self.view.parts.preview_image.pixmap_item is not None

    def apply_fit(self) -> None:
        if self.view.parts.preview_image.pixmap_item is None:
            return
        self.view._stop_view_motion()
        self.view._close_cluster_popup()
        LOGGER.debug("fit_to_view logical_key=%s", self.view.parts.preview_image.current_image_key)
        self.view.fitInView(self.view.parts.preview_image.pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
        self.zoom = 1.0
        self.fit_mode = True
        self.last_view_range = self.view.view_range()
        self.view._schedule_marker_redraw()
        if self.view._zoom_changed is not None:
            self.view._zoom_changed(self.zoom)

    def zoom_toward(self, factor: float, anchor_view: QPointF) -> None:
        """Ease the zoom by ``factor``, keeping the point under ``anchor_view``.

        Retargetable: a notch that arrives mid-animation extends the target
        from where it was heading rather than from where the view is, so fast
        scrolling never loses notches, and the anchor follows the cursor.
        With reduced motion the spring jumps, which is the old instant zoom.
        """

        if self.view.parts.preview_image.pixmap_item is None:
            return
        self.view._dismiss_crossfade()  # the zoom must show at once
        self.view._stop_flight()  # the wheel takes the camera from wherever it is
        if self.refit_from is not None:
            self.view._end_refit_ease()  # zoom on from wherever the refit had got to
        self.view._close_cluster_popup()
        was_fit = self.fit_mode
        self.view._mark_user_interacted("wheel_zoom")
        spring = self.wheel_zoom
        if not spring.is_animating:
            spring.reset(math.log(max(self.zoom, 1e-6)))
        target = spring.target + math.log(factor)
        if self.view.parts.marker_layer.atlas_lod_enabled:
            target = min(math.log(MAX_ATLAS_ZOOM), max(0.0, target))
            if abs(target - spring.target) < 1e-9 and not spring.is_animating:
                # Zooming out at the Atlas floor changes nothing; stay fitted.
                self.fit_mode = was_fit
                return
        self.wheel_anchor = (QPointF(anchor_view), self.view.mapToScene(anchor_view.toPoint()))
        spring.set_target(target)

    def wheel_zoom_step(self, log_zoom: float) -> None:
        if self.view.parts.preview_image.pixmap_item is None or self.wheel_anchor is None:
            return
        zoom = math.exp(log_zoom)
        factor = zoom / max(self.zoom, 1e-6)
        self.zoom = zoom
        anchor_view, anchor_scene = self.wheel_anchor
        self.view.scale(factor, factor)
        # Re-anchor against the fixed viewport point every step, so scroll-bar
        # rounding cannot accumulate into a drift across the animation.
        drift = self.view.mapToScene(anchor_view.toPoint()) - anchor_scene
        self.view.centerOn(self.view.mapToScene(self.view.viewport().rect().center()) - drift)
        if self.view._zoom_changed is not None:
            self.view._zoom_changed(self.zoom)
        self.view._schedule_marker_redraw()

    def stop_wheel_zoom(self) -> None:
        """Leave an eased wheel zoom where it is; something else moves the view now."""

        if self.wheel_zoom.is_animating:
            self.wheel_zoom.reset(math.log(max(self.zoom, 1e-6)))
            self.wheel_anchor = None

    def ease_next_refit(self) -> None:
        """Ease, rather than snap, the fit-mode refit of the coming resize.

        For a side panel opening or closing: the fitted image rides along to
        its new fit. Only in fit mode and with motion on, and only for a
        resize within ``REFIT_EASE_ARM_S``, so a later window drag still
        refits at once. Only the view transform changes.
        """

        if self.view.is_fit_mode and motion_enabled(self.view):
            self.refit_ease_armed_until = time.monotonic() + REFIT_EASE_ARM_S

    def fit_target(self) -> tuple[float, QPointF] | None:
        """The (scale, scene centre) ``fitInView`` would produce now."""

        if self.view.parts.preview_image.pixmap_item is None:
            return None
        margin = FIT_IN_VIEW_MARGIN_PX
        view_rect = QRectF(self.view.viewport().rect()).adjusted(margin, margin, -margin, -margin)
        item_rect = self.view.parts.preview_image.pixmap_item.sceneBoundingRect()
        if view_rect.width() <= 0 or view_rect.height() <= 0 or item_rect.width() <= 0 or item_rect.height() <= 0:
            return None
        scale = min(view_rect.width() / item_rect.width(), view_rect.height() / item_rect.height())
        return scale, item_rect.center()

    def start_fit_ease(self) -> None:
        target = self.view._fit_target()
        if target is None:
            return
        self.refit_from = (self.view._zoom_scale(), self.view.mapToScene(self.view.viewport().rect().center()))
        self.refit_to = target
        # A shrinking viewport briefly shows the old, larger image; scroll
        # bars appearing for that moment would resize the viewport again.
        self.view._hold_scroll_bars()
        self.refit_ease.reset(0.0)
        self.refit_ease.set_target(1.0)

    def refit_ease_step(self, progress: float) -> None:
        if self.view.parts.preview_image.pixmap_item is None or self.refit_from is None or self.refit_to is None:
            return
        (from_scale, from_centre), (to_scale, to_centre) = self.refit_from, self.refit_to
        t = min(1.0, max(0.0, progress))
        # Geometric in scale, so the image grows or shrinks at an even pace.
        scale = from_scale * (to_scale / from_scale) ** t
        self.refitting = True
        try:
            self.view.setTransform(QTransform.fromScale(scale, scale))
            self.view.centerOn(from_centre + (to_centre - from_centre) * t)
        finally:
            self.refitting = False
        self.last_view_range = self.view.view_range()
        if self.view._zoom_changed is not None:
            self.view._zoom_changed(self.zoom)
        self.view._schedule_marker_redraw()

    def refit_ease_done(self) -> None:
        if self.refit_from is None:
            return
        self.view._end_refit_ease()
        if self.view.is_fit_mode:
            # Land exactly where a snap would have, whatever rounding the
            # eased frames accumulated.
            self.refitting = True
            try:
                self.view.fitInView(self.view.parts.preview_image.pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
                self.zoom = 1.0
                self.last_view_range = self.view.view_range()
            finally:
                self.refitting = False
            self.view._schedule_marker_redraw()
            if self.view._zoom_changed is not None:
                self.view._zoom_changed(self.zoom)

    def end_refit_ease(self) -> None:
        if self.refit_to is not None:
            # Zoom is relative to the fit; part-way there, say how far.
            self.zoom = self.view._zoom_scale() / max(self.refit_to[0], 1e-9)
        self.refit_ease.reset(self.refit_ease.value)
        self.refit_ease_armed_until = 0.0
        self.refit_from = self.refit_to = None
        self.view._release_scroll_bars()

    def hold_scroll_bars(self) -> None:
        """Keep scroll bars off while the view animates its transform: one
        showing for a frame would resize the viewport under the motion."""

        if self.scroll_policies is None:
            self.scroll_policies = (self.view.horizontalScrollBarPolicy(), self.view.verticalScrollBarPolicy())
            self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def release_scroll_bars(self) -> None:
        if self.scroll_policies is not None:
            horizontal, vertical = self.scroll_policies
            self.scroll_policies = None
            self.view.setHorizontalScrollBarPolicy(horizontal)
            self.view.setVerticalScrollBarPolicy(vertical)

    def stop_view_motion(self, *, finish_fit: bool = False) -> None:
        """Stop any eased zoom, refit or flight; something else moves the view now.

        ``finish_fit`` lands an interrupted refit on the exact fit, for
        interruptions that leave fit mode on (a press).
        """

        self.view._stop_wheel_zoom()
        self.view._stop_flight()
        # A dissolving picture covers the canvas; it must never hide a live
        # change the reviewer just made.
        self.view._dismiss_crossfade()
        if self.refit_from is not None:
            if finish_fit:
                self.view._refit_ease_done()
            else:
                self.view._end_refit_ease()

    def fly_to(
        self,
        centre: QPointF,
        scale: float | None = None,
        *,
        zoom: float | None = None,
        on_landed: Callable[[], None] | None = None,
    ) -> None:
        """Move the camera to ``centre`` (scene) at ``scale`` instead of cutting.

        The path pans with a little zoom when the target is near, and zooms
        out, crosses and zooms back in when it is far (never further out than
        the whole image), so the reviewer sees where the camera went. It lands
        exactly on ``scale`` and ``centre``; ``zoom`` is the zoom to report
        there (relative to fit), by default the current one scaled along.
        Reduced motion, a hidden view or an interruption end it at once or
        where it is. Only the view transform moves.
        """

        if self.view.parts.preview_image.pixmap_item is None:
            return
        self.view._stop_view_motion()
        self.view._close_cluster_popup()
        from_scale = self.view._zoom_scale()
        to_scale = from_scale if scale is None else max(float(scale), 1e-6)
        from_centre = self.view.mapToScene(self.view.viewport().rect().center())
        width = float(max(1, self.view.viewport().width()))
        path = zoom_pan_path(
            (from_centre.x(), from_centre.y()), width / from_scale, (centre.x(), centre.y()), width / to_scale
        )
        if path.length < 1e-3 and abs(to_scale - from_scale) <= 1e-9 * from_scale:
            # Already there (as far as the view can go): nothing to move.
            if on_landed is not None:
                on_landed()
            return
        self.flight_from = (from_scale, self.zoom)
        self.flight_to = (to_scale, QPointF(centre), zoom if zoom is not None else self.zoom * to_scale / from_scale)
        self.flight_landed = on_landed
        # A camera the program moved is not a fit to follow on resize.
        self.fit_mode = False
        if not motion_enabled(self.view) or not self.view.isVisible():
            self.view._land_flight()
            return
        self.flight_path = path
        self.view._hold_scroll_bars()
        self.flight.duration_s = camera_duration_ms(path.length) / 1000.0
        self.flight.start()

    @property
    def is_flying(self) -> bool:
        return self.flight.is_animating

    def flight_step(self, progress: float) -> None:
        path, start, end = self.flight_path, self.flight_from, self.flight_to
        if path is None or start is None or end is None or self.view.parts.preview_image.pixmap_item is None:
            return
        x, y, visible_width = path.at(progress * path.length)
        scale = self.view.viewport().width() / max(visible_width, 1e-9)
        fit = self.view._fit_target()
        if fit is not None:
            # Out to the whole image at most: further out, Qt centres the
            # smaller scene and the camera would leave its path.
            scale = max(scale, min(fit[0], start[0], end[0]))
        self.view.setTransform(QTransform.fromScale(scale, scale))
        self.view.centerOn(QPointF(x, y))
        self.zoom = start[1] * scale / start[0]
        self.last_view_range = self.view.view_range()
        if self.view._zoom_changed is not None:
            self.view._zoom_changed(self.zoom)
        self.view._schedule_marker_redraw()

    def land_flight(self) -> None:
        target, landed = self.flight_to, self.flight_landed
        self.flight_path = self.flight_from = self.flight_to = None
        self.flight_landed = None
        self.arrival_in_flight = None
        self.view._release_scroll_bars()
        if target is None or self.view.parts.preview_image.pixmap_item is None:
            return
        scale, centre, zoom = target
        self.view.setTransform(QTransform.fromScale(scale, scale))
        self.view.centerOn(centre)
        self.zoom = zoom
        self.last_view_range = self.view.view_range()
        if self.view._zoom_changed is not None:
            self.view._zoom_changed(self.zoom)
        self.view._redraw_markers()
        if landed is not None:
            landed()

    def stop_flight(self) -> None:
        """Leave a flight where it is: the reviewer, or a new image, has the camera."""

        if self.flight.is_animating:
            self.flight.stop()
        if self.flight_to is not None:
            self.flight_path = self.flight_from = self.flight_to = None
            self.flight_landed = None
            self.arrival_in_flight = None
            self.view._release_scroll_bars()
            self.view._schedule_marker_redraw()

    def arrive_at_marker(
        self,
        marker_id: str | None,
        *,
        ready: Callable[[], bool] | None = None,
        frame: bool = False,
    ) -> None:
        """Show where a jump landed: bring its marker into view, then pulse it.

        At fit the marker is already in view, so only the ring shows; on a
        zoomed view with the marker outside it, the camera flies there first.
        ``frame`` flies in until the marker fills part of the view, for a
        marker too small to find at fit (an Overview on its Atlas).
        Waits for the image (it may still be decoding; ``ready`` says whether
        the image shown is the destination's, and not the one it replaces),
        for the view to be shown, and for a crossfade to finish. ``None``
        cancels.
        """

        self.pending_arrival = marker_id or None
        self.arrival_ready = ready if self.pending_arrival is not None else None
        self.arrival_frames = frame and self.pending_arrival is not None
        if self.pending_arrival is not None:
            self.arrival_timer.start(0)

    def try_arrival(self) -> None:
        marker_id = self.pending_arrival
        if marker_id is None or self.view.parts.preview_image.pixmap_item is None or not self.view.isVisible():
            return  # _set_pixmap and showEvent try again
        if self.arrival_ready is not None and not self.arrival_ready():
            return  # still the previous item's image; _set_pixmap tries again
        if self.view.crossfade_in_progress:
            # Dissolve first, then move: a picture over the canvas would hide
            # the flight and freeze the ring.
            self.arrival_timer.start(20)
            return
        self.pending_arrival = None
        found = self.view._arrival_target(marker_id)
        if found is None:
            return
        centre, radius = found
        framing = None
        if self.arrival_frames:
            # Frame the marker itself, not the cluster that holds it at fit:
            # the cluster breaks up as the camera flies in.
            own = self.view._own_marker_geometry(marker_id)
            if own is not None:
                centre, radius = own
            framing = self.view._framing_for(radius)
        if framing is not None:
            scale, zoom = framing
            self.view.fly_to(centre, scale, zoom=zoom, on_landed=lambda: self.view._pulse_marker(marker_id))
            if self.view.is_flying:
                self.arrival_in_flight = marker_id
        elif not self.view.is_fit_mode and not self.view._comfortably_in_view(centre):
            self.view.fly_to(centre, on_landed=lambda: self.view._pulse_marker(marker_id))
            if self.view.is_flying:
                self.arrival_in_flight = marker_id
        else:
            self.view._pulse_marker(marker_id)

    def own_marker_geometry(self, marker_id: str) -> tuple[QPointF, float] | None:
        for marker in self.view.parts.marker_layer.source_markers:
            if marker.id == marker_id:
                return marker_scene_geometry(self.view._project_marker(marker))
        return None

    def framing_for(self, radius: float) -> tuple[float, float] | None:
        """The scale, and zoom from fit, at which a marker of ``radius`` fills
        ``ARRIVAL_FRAME_SHARE`` of the view: never past the Atlas zoom limit,
        never further out than the fit or than the view already is. A point
        marker (a batch position) frames at the limit."""

        if self.view.parts.preview_image.pixmap_item is None:
            return None
        viewport = self.view.viewport().rect()
        bounds = self.view.parts.preview_image.pixmap_item.boundingRect()
        if min(viewport.width(), viewport.height(), bounds.width(), bounds.height()) <= 0:
            return None
        fit_scale = min(viewport.width() / bounds.width(), viewport.height() / bounds.height())
        wanted = min(viewport.width(), viewport.height()) * ARRIVAL_FRAME_SHARE / (2.0 * max(radius, 1.0))
        scale = max(min(max(wanted, fit_scale), fit_scale * MAX_ATLAS_ZOOM), self.view._zoom_scale())
        return scale, scale / fit_scale

    def arrival_target(self, marker_id: str) -> tuple[QPointF, float] | None:
        """The marker's scene centre and radius, or its cluster's on the Atlas."""

        for marker in self.view.parts.marker_layer.last_display_markers:
            if marker.id == marker_id:
                return marker_scene_geometry(marker)
        for marker in self.view.parts.marker_layer.last_display_markers:
            if marker.marker_type == MarkerType.BATCH_CLUSTER and marker_id in tuple(
                marker.metadata.get("member_ids", ())
            ):
                return marker_scene_geometry(marker)
        for marker in self.view.parts.marker_layer.source_markers:
            if marker.id == marker_id:
                return marker_scene_geometry(self.view._project_marker(marker))
        return None

    def marker_screen_radius(self, marker_id: str) -> float:
        """The fixed on-screen radius of an Atlas marker (or its cluster), else 0."""

        for marker in self.view.parts.marker_layer.last_display_markers:
            if marker.id == marker_id or (
                marker.marker_type == MarkerType.BATCH_CLUSTER
                and marker_id in tuple(marker.metadata.get("member_ids", ()))
            ):
                diameter = marker.metadata.get("diameter_px")
                return float(diameter) / 2.0 if isinstance(diameter, int | float) else 0.0
        return 0.0

    def comfortably_in_view(self, scene_point: QPointF) -> bool:
        rect = self.view.viewport().rect()
        margin = max(24, round(0.08 * min(rect.width(), rect.height())))
        return rect.adjusted(margin, margin, -margin, -margin).contains(self.view.mapFromScene(scene_point))

    def reveal_marker(self, marker: ImageMarker) -> None:
        """Pan to a marker the keyboard moved to off-screen."""

        found = marker_scene_geometry(marker)
        if found is not None and not self.view.is_fit_mode and not self.view._comfortably_in_view(found[0]):
            self.view.fly_to(found[0])

    def pulse_marker(self, marker_id: str) -> None:
        if not motion_enabled(self.view) or self.view.parts.preview_image.pixmap_item is None:
            return
        found = self.view._arrival_target(marker_id)
        if found is None:
            return

        centre, scene_radius = found
        rect = self.view.parts.preview_image.pixmap_item.boundingRect()
        if rect.width() <= 0 or rect.height() <= 0:
            return
        # Relative to the image, so a new level of the same image (a pyramid
        # upgrade) keeps the ring on its marker.
        relative = ((centre.x() - rect.left()) / rect.width(), (centre.y() - rect.top()) / rect.height())
        relative_radius = scene_radius / rect.width()
        screen_radius = self.view._marker_screen_radius(marker_id)

        def locate() -> tuple[QPointF, float] | None:
            item = self.view.parts.preview_image.pixmap_item
            if item is None:
                return None
            bounds = item.boundingRect()
            point = self.view.viewportTransform().map(
                QPointF(bounds.left() + relative[0] * bounds.width(), bounds.top() + relative[1] * bounds.height())
            )
            radius = relative_radius * bounds.width() * self.view._zoom_scale()
            return point, min(PULSE_MAX_RADIUS_PX, max(PULSE_MIN_RADIUS_PX, radius, screen_radius)) + 4.0

        self.view._dismiss_pulse()
        self.pulse_overlay = MarkerPulse(self.view.viewport(), locate, QColor(current_palette().overlay_selected))

    def dismiss_pulse(self) -> None:
        pulse, self.pulse_overlay = self.pulse_overlay, None
        if pulse is not None and shiboken6.isValid(pulse):
            pulse.dismiss()

    @property
    def pulse(self) -> MarkerPulse | None:
        pulse = self.pulse_overlay
        return pulse if pulse is not None and shiboken6.isValid(pulse) and pulse.isVisible() else None

    def scale_by_center(self, factor: float) -> None:
        if self.view.parts.preview_image.pixmap_item is None:
            return
        self.view._stop_view_motion()
        self.view._close_cluster_popup()
        was_fit = self.fit_mode
        self.view._mark_user_interacted("button_zoom")
        if self.view.parts.marker_layer.atlas_lod_enabled:
            target_zoom = min(MAX_ATLAS_ZOOM, max(1.0, self.zoom * factor))
            factor = target_zoom / max(self.zoom, 0.01)
            if abs(factor - 1.0) < 1e-6:
                # Zooming out at the Atlas floor changes nothing; stay fitted.
                self.fit_mode = was_fit
                return
            self.zoom = target_zoom
        else:
            self.zoom *= factor
        anchor_scene = self.view.mapToScene(self.view.viewport().rect().center())
        self.view.scale(factor, factor)
        self.view.centerOn(anchor_scene)
        if self.view._zoom_changed is not None:
            self.view._zoom_changed(self.zoom)
        self.view._schedule_marker_redraw()

    def transform_scale(self) -> float:
        return max(abs(self.view.transform().m11()), 0.01)

