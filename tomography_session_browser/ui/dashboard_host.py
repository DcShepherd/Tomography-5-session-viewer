"""Dashboard rendering, cached models and the loading overlay hand-off."""
from __future__ import annotations

from collections.abc import Callable
from collections import OrderedDict
import logging
from typing import Any
from PySide6.QtCore import QElapsedTimer, QTimer
from tomography_session_browser.domain.models import Atlas, BatchPosition, Overview, Sample, SearchMap, SearchTile, Session, TiltSeries
from tomography_session_browser.domain.identity import entity_key_text
from tomography_session_browser.presentation.context_stack import ContextStack
from tomography_session_browser.presentation.dashboard.models import DashboardEntityScope, DashboardModel, LinkedAtlasDashboardScope
from tomography_session_browser.presentation.dashboard.dashboard import session_dashboard_model
from tomography_session_browser.presentation.project_tree import ProjectTreeGroup, session_key
from tomography_session_browser.presentation.scopes import LinkedSampleGroup
from tomography_session_browser.presentation.workspace import Workspace
from tomography_session_browser.presentation.dashboard.scope_value import (
    DashboardScopeValue,
    timeline_from_tilt_series,
)
from tomography_session_browser.services.timeline_service import SessionTimeline, build_session_timeline
from tomography_session_browser.ui.loading.instrumentation import LoadInstrumentation
from tomography_session_browser.ui.dashboard.widget import SessionDashboard

LOGGER = logging.getLogger("tomography_session_browser.ui.main_window")

def dashboard_cache_key(scope_value: DashboardScopeValue) -> str:
    def key_for(value: Any) -> str:
        if isinstance(value, Session):
            return f"session:{session_key(value)}"
        if isinstance(value, Sample | Atlas | Overview | SearchMap | SearchTile | BatchPosition | TiltSeries):
            return entity_key_text(value)
        if isinstance(value, LinkedSampleGroup):
            return "linked-sample:" + ",".join(key_for(sample) for sample in value.samples)
        if isinstance(value, ProjectTreeGroup):
            return f"project-group:{value.key}"
        return f"{type(value).__name__}:{id(value)}"

    if isinstance(scope_value, list):
        return "sessions:" + ",".join(key_for(session) for session in scope_value)
    if isinstance(scope_value, LinkedAtlasDashboardScope):
        atlas_keys = ",".join(key_for(atlas) for _label, atlas in scope_value.atlases)
        return f"linked-atlas:{key_for(scope_value.source)}:{atlas_keys}"
    if isinstance(scope_value, DashboardEntityScope):
        tilt_keys = ",".join(key_for(tilt) for tilt in scope_value.tilt_series)
        batch_keys = ",".join(key_for(batch) for batch in scope_value.batch_positions)
        search_keys = ",".join(key_for(search_map) for search_map in scope_value.search_maps)
        return f"entity:{key_for(scope_value.source)}:{search_keys}:{batch_keys}:{tilt_keys}"
    return key_for(scope_value)


def timeline_for_scope(scope_value: DashboardScopeValue) -> SessionTimeline | None:
    """Build a timeline appropriate for ``scope_value`` so the strip in the
    dashboard reflects the same scope as the rest of the cards."""

    try:
        if isinstance(scope_value, Session):
            return build_session_timeline(scope_value)
        if isinstance(scope_value, list):
            # Concatenate per-session timelines into a single SessionTimeline.
            segments: list = []
            pauses: list = []
            for session in scope_value:
                tl = build_session_timeline(session)
                segments.extend(tl.segments)
                pauses.extend(tl.pauses)
            return SessionTimeline(segments=tuple(segments), pauses=tuple(pauses))
        if isinstance(scope_value, Sample):
            tilt_series = list(scope_value.tilt_series)
            return timeline_from_tilt_series(tilt_series)
        if isinstance(scope_value, LinkedSampleGroup):
            return timeline_from_tilt_series(list(scope_value.tilt_series))
        if isinstance(scope_value, DashboardEntityScope):
            return timeline_from_tilt_series(list(scope_value.tilt_series))
        if isinstance(scope_value, LinkedAtlasDashboardScope):
            return timeline_for_scope(scope_value.source)
        if isinstance(scope_value, ProjectTreeGroup):
            return timeline_for_scope(list(scope_value.sessions))
    except Exception:  # pragma: no cover — defensive
        return None
    return None


class DashboardHost:
    def __init__(
        self, widget: SessionDashboard, *, workspace: Callable[[], Workspace],
        scope_value: Callable[[], DashboardScopeValue], context_stack: Callable[[], ContextStack],
        display_model: Callable[[DashboardModel, DashboardScopeValue], DashboardModel],
        loading_active: Callable[[], bool], highlighted_tilt_id: Callable[[], str | None],
        request_render: Callable[..., None], instrumentation: LoadInstrumentation,
    ) -> None:
        self.widget = widget
        self.workspace, self.scope_value = workspace, scope_value
        self.context_stack, self.display_model = context_stack, display_model
        self.loading_active, self.highlighted_tilt_id = loading_active, highlighted_tilt_id
        self.request_render, self.instrumentation = request_render, instrumentation
        self.model_cache: dict[str, tuple[DashboardModel, SessionTimeline | None]] = {}
        self.pending: tuple[DashboardModel, SessionTimeline | None, bool] | None = None
        self.pending_refresh = False
        self.applied: tuple[object, ...] | None = None
        # Identifiers and scalars only; removed workspaces retain no scroll memory.
        self.scroll_positions: OrderedDict[str, int] = OrderedDict()
        self.scroll_scope_key: str | None = None

    def invalidate(self) -> None:
        self.model_cache.clear()
        self.applied = None
        self.scroll_positions.clear()
        self.scroll_scope_key = None

    def reset_pending(self) -> None:
        self.pending = None
        self.pending_refresh = False

    def render(
        self,
        *,
        animate: bool = False,
        model: DashboardModel | None = None,
        timeline: SessionTimeline | None = None,
        defer_heavy_cards: bool = False,
    ) -> None:
        """Rebuild the Session-tab dashboard for the current ``_scope``.

        Mapping:
        * ``Sample`` selected → dashboard scoped to that sample only.
        * ``LinkedSampleGroup`` selected → aggregate scope across the group.
        * ``Session`` selected → that session.
        * ``EntityGroup`` / ``None`` / project root → all loaded sessions
          (one session if only one is loaded; otherwise an aggregate).
        """

        timer = QElapsedTimer()
        timer.start()
        # Any rebuild draws the current palette, so a theme-switch rebuild
        # waiting for the Session tab is no longer needed.
        scope_value = self.scope_value()
        loading_active = self.loading_active()
        defer_heavy_cards = defer_heavy_cards or loading_active
        scope_ms = timer.elapsed()
        # Count every dashboard refresh request, including cache hits, so a
        # high rebuild count during load tells us the dashboard is being
        # asked to re-render too often. Cached rebuilds are still ~0 ms of
        # presenter work, but the widget set_model path still runs.
        self.instrumentation.dashboard_rebuilds += 1
        try:
            if model is None:
                cache_key = dashboard_cache_key(scope_value)
                cached = self.model_cache.get(cache_key)
                if cached is not None:
                    model, timeline = cached
                    model_ms = 0
                    timeline_ms = 0
                elif loading_active:
                    self.pending_refresh = True
                    LOGGER.debug(
                        "dashboard refresh deferred while loading scope_type=%s",
                        type(scope_value).__name__,
                    )
                    return
                else:
                    model_timer = QElapsedTimer()
                    model_timer.start()
                    model = session_dashboard_model(scope_value, workspace=self.workspace())
                    model_ms = model_timer.elapsed()
                    timeline_timer = QElapsedTimer()
                    timeline_timer.start()
                    timeline = timeline_for_scope(scope_value)
                    timeline_ms = timeline_timer.elapsed()
                    self.model_cache[cache_key] = (model, timeline)
            else:
                model_ms = 0
                timeline_ms = 0
                cache_key = dashboard_cache_key(scope_value)
                self.model_cache[cache_key] = (model, timeline)
            if loading_active:
                self.pending = (model, timeline, animate)
                self.pending_refresh = False
            render_timer = QElapsedTimer()
            render_timer.start()
            display_model = self.display_model(model, scope_value)
            stack = self.context_stack()
            self.widget.set_context_stack(stack)
            # Held by reference, not ``id()``: a freed model's address can be reused.
            signature = (model, timeline, display_model.title, defer_heavy_cards, stack)
            applied = self.applied
            if (applied is not None and applied[0] is model and applied[1] is timeline
                    and applied[2:] == signature[2:]):
                self.widget.set_highlighted_tilt_series_id(self.highlighted_tilt_id())
                return
            if self.scroll_scope_key is not None:
                self.scroll_positions[self.scroll_scope_key] = self.widget.scroll_position
                self.scroll_positions.move_to_end(self.scroll_scope_key)
            position = self.scroll_positions.get(cache_key, 0)
            while len(self.scroll_positions) > 50:
                self.scroll_positions.popitem(last=False)
            self.widget.set_model(
                display_model,
                timeline,
                animate=animate and not defer_heavy_cards,
                defer_heavy_cards=defer_heavy_cards,
                highlighted_tilt_series_id=self.highlighted_tilt_id(),
                scroll_position=position,
            )
            self.scroll_scope_key = cache_key
            self.applied = signature
            render_ms = render_timer.elapsed()
            self.instrumentation.last_dashboard_build_ms = int(render_ms)
            LOGGER.debug(
                "dashboard refresh timings scope_type=%s scope_ms=%d model_ms=%d timeline_ms=%d widget_ms=%d total_ms=%d defer_heavy=%s loading_active=%s",
                type(scope_value).__name__,
                scope_ms,
                model_ms,
                timeline_ms,
                render_ms,
                timer.elapsed(),
                defer_heavy_cards,
                loading_active,
            )
        except Exception as exc:  # pragma: no cover — defensive fallback
            self.applied = None
            LOGGER.warning("Dashboard model build failed: %s", exc, exc_info=True)
            self.widget.set_context_stack(self.context_stack())
            self.widget.set_model(None)


    def flush(self, *, now: bool = False) -> None:
        """Build the dashboard's held-back charts.

        ``now``: at once and without an entrance, under the leaving overlay,
        which reveals the finished page itself. Otherwise just after the
        overlay has gone (it was hidden without a hand-off).
        """

        pending = self.pending
        refresh = self.pending_refresh
        self.pending = None
        self.pending_refresh = False
        if pending is None and not refresh:
            return

        def render_after_overlay_paint() -> None:
            if pending is not None:
                model, timeline, animate = pending
                self.request_render(
                    model=model,
                    timeline=timeline,
                    animate=animate and not now,
                    defer_heavy_cards=False,
                )
            else:
                self.request_render(defer_heavy_cards=False)

        if now:
            render_after_overlay_paint()
        else:
            QTimer.singleShot(0, render_after_overlay_paint)
