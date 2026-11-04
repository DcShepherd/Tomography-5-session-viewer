"""DashboardInteraction owns its window interaction responsibilities."""
from __future__ import annotations
from tomography_session_browser.presentation.project_tree import folded_session_for_sample
import logging
from dataclasses import (
    replace,
)
from PySide6.QtCore import (
    QObject,
    QElapsedTimer,
    Slot,
    QTimer,
)
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
)
from tomography_session_browser.ui.shell.chrome import (
    TAB_LABELS,
)
from tomography_session_browser.domain.models import (
    Sample,
    Session,
    TiltSeries,
)
from tomography_session_browser.services.item_status import (
    TAG_INFERRED_BATCH_PREFIX,
    filter_tag_query,
)
from tomography_session_browser.services.timeline_service import (
    SessionTimeline,
)
from tomography_session_browser.presentation.project_tree import (
    ProjectTreeGroup,
)
from tomography_session_browser.presentation.prepared_session import (
    PreparedSessionUi,
)
from tomography_session_browser.presentation.dashboard.scope_value import (
    DashboardScopeValue,
)
from tomography_session_browser.presentation.dashboard.models import (
    INFERRED_BATCH_TILE_PREFIX,
    DashboardModel,
    DashboardFilterModel,
)
from tomography_session_browser.presentation.scopes import (
    LinkedSampleGroup,
)

LOGGER = logging.getLogger("tomography_session_browser.ui.main_window")
from tomography_session_browser.ui.window_parts.contracts import (
    WindowParts,
    WindowUi,
)
from tomography_session_browser.ui.window_parts.state import (
    WindowState,
)
from tomography_session_browser.ui.window_parts.support import (
    LOGGER,
)
from tomography_session_browser.presentation.tab_keys import TabKey
from tomography_session_browser.presentation.dashboard.summary import ProcessingSummary, summary_from_input
from tomography_session_browser.presentation.dashboard.tomogram_summary_input import summary_input, with_review
from tomography_session_browser.presentation.tomogram_selection import AllTomograms, FolderTomograms
from tomography_session_browser.presentation.tomogram_review_state import ProcessingReviewCounts, review_counts
from tomography_session_browser.presentation.context_stack import entity_display_label_singular

class DashboardInteraction(QObject):
    def __init__(self, window: QMainWindow, state: WindowState, ui: WindowUi, parts: WindowParts) -> None:
        super().__init__(window)
        self.window, self.state, self.ui, self.parts = window, state, ui, parts
        self.processing_refresh = QTimer(self)
        self.processing_refresh.setSingleShot(True)
        self.processing_refresh.timeout.connect(self.refresh_processing_summary)

    def is_processing_scope(self) -> bool:
        scope = self.state.review.scope
        return isinstance(scope, AllTomograms | FolderTomograms) or (
            isinstance(scope, ProjectTreeGroup) and scope.processing_source is not None)

    @Slot()
    def request_processing_summary_refresh(self) -> None:
        if self.is_processing_scope():
            self.parts.dashboard_host.applied = None
            if self.ui.tabs.currentIndex() == TAB_LABELS.index(TabKey.SESSION) and not self.state.loading.loading_task_active:
                self.processing_refresh.start(0)

    @Slot()
    def refresh_processing_summary(self) -> None:
        if (self.is_processing_scope() and self.ui.tabs.currentIndex() == TAB_LABELS.index(TabKey.SESSION)
                and not self.state.loading.loading_task_active):
            payload = summary_input(self.state.review.workspace, self.state.review.scope,
                                    self.state.review.tomogram_sort, self.state.review.tomogram_review)
            summary = summary_from_input(payload)
            payload = self.parts.tag_projects.summary_input(payload)
            page = self.ui.session_dashboard._processing_page
            if page is not None and page.summary.chart_key == summary.chart_key and not payload.notice:
                payload = replace(payload, notice=page.summary.notice)
            if not self.ui.session_dashboard.update_processing_review(summary.chart_key, payload):
                self.render_session_summary()

    def processing_review_counts(self, summary: ProcessingSummary) -> ProcessingReviewCounts:
        payload = with_review(summary.input, self.state.review.tomogram_review)
        return review_counts(row.review for row in payload.rows)

    def render_session_summary(
        self,
        *,
        animate_dashboard: bool = False,
        prepared: PreparedSessionUi | None = None,
        defer_heavy_cards: bool | None = None,
    ) -> None:
        """Render the visible Session dashboard and update its tab counts."""
        self.parts.chrome.update_tab_counts()
        if not self.state.review.workspace.has_sources:
            self.ui.session_dashboard.set_context_stack(self.parts.context.current_context_stack())
            self.ui.session_dashboard.set_model(None)
            return
        self.parts.dashboard.render_dashboard_for_scope(
            animate=animate_dashboard,
            model=prepared.dashboard_model if prepared is not None else None,
            timeline=prepared.timeline if prepared is not None else None,
            defer_heavy_cards=defer_heavy_cards,
            raise_on_error=prepared is not None,
        )


    def render_dashboard_for_scope(self, *, animate: bool = False, model: DashboardModel | None = None,
                                   timeline: SessionTimeline | None = None, defer_heavy_cards: bool | None = None,
                                   raise_on_error: bool = False) -> None:
        self.parts.dashboard_host.render(animate=animate, model=model, timeline=timeline,
                                         defer_heavy_cards=defer_heavy_cards, raise_on_error=raise_on_error)


    def dashboard_display_model(self, model: DashboardModel, scope_value: DashboardScopeValue) -> DashboardModel:
        """Apply display-only project naming without altering source models."""

        scope = self.state.review.scope
        if isinstance(scope, Sample) and self.state.review.preserve_tree_root_context:
            session = folded_session_for_sample(scope, self.state.review.workspace)
            if session is not None:
                group = self.parts.scopes.project_group_for_session(session)
                title = group.display_name if group is not None and len(group.sessions) == 1 else session.name
                return replace(model, title=title, kind=session.kind.value, sample_label=scope.name,
                               tomograms=summary_input(self.state.review.workspace, AllTomograms(scope)))
        if self.is_processing_scope():
            payload = summary_input(self.state.review.workspace, scope, self.state.review.tomogram_sort,
                                    self.state.review.tomogram_review)
            payload = self.parts.tag_projects.summary_input(payload)
            if (isinstance(model.summary, ProcessingSummary) and model.summary.input.key == payload.key
                    and model.summary.input.sources == payload.sources and not payload.notice):
                payload = replace(payload, notice=model.summary.notice)
            return replace(model, title=payload.title, path='', kind='processing', sample_label='',
                           summary=summary_from_input(payload))
        if isinstance(scope, Session | Sample | LinkedSampleGroup) or (
                isinstance(scope, ProjectTreeGroup) and scope.processing_source is None):
            model = replace(model, tomograms=summary_input(self.state.review.workspace, AllTomograms(scope)))
        title: str | None = None
        if isinstance(self.state.review.scope, ProjectTreeGroup):
            title = self.state.review.scope.display_name
        elif isinstance(scope_value, list):
            groups = self.parts.scopes.project_groups()
            if len(groups) == 1:
                title = groups[0].display_name
        if title and getattr(model, "title", None) != title:
            try:
                return replace(model, title=title)
            except TypeError:
                return model
        return model


    @Slot()
    def open_tomograms_summary(self) -> None:
        scope = self.state.review.scope
        if not self.is_processing_scope():
            scope = AllTomograms(scope)
        self.parts.viewers.open_scope(scope, destination_tab=TabKey.TOMOGRAMS,
                                      departure=self.parts.history.begin_navigation())

    def navigate_to_tab(self, target: str) -> None:
        if target in TAB_LABELS:
            departure = self.parts.history.begin_navigation()
            self.ui.tabs.setCurrentIndex(TAB_LABELS.index(target))
            self.parts.history.record_history(departure)


    def on_dashboard_tile_clicked(self, destination: str, item_id: str) -> None:
        """Handle a click on a status tile in any of the four count cards.

        Resolve an ID in its source scope, then execute the verified entity
        through the shared jump owner. Missing or ambiguous destinations keep
        the current page and explain why the action cannot proceed.
        """

        if not item_id:
            self.parts.dashboard.navigate_to_tab(destination)
            return

        if item_id.startswith(INFERRED_BATCH_TILE_PREFIX):
            # No batch position was recorded, so the Batch position list has
            # no row for it. Its failed tilt series are what there is to see.
            token = item_id[len(INFERRED_BATCH_TILE_PREFIX):]
            # The token is "<label>@<grid>"; the reason names the label.
            label = token.split("@", 1)[0]
            self.parts.dashboard.on_dashboard_filter_requested(
                TabKey.TILT_SERIES,
                filter_tag_query(f"{TAG_INFERRED_BATCH_PREFIX}{token}"),
                f"Failed tilt series grouped under inferred batch position {label}; "
                "no batch position was recorded for them.",
            )
            return

        resolution = self.parts.navigation.resolve_action_target(item_id)
        target = resolution.target
        if not resolution.navigable:
            self.parts.context.post_navigation_message(resolution.explanation)
            return
        if isinstance(target, TiltSeries):
            self.parts.navigation.open_resolved_destination(
                target, message=f"Opened tilt series {self.parts.scopes.label_for(target)} from dashboard tile.")
            return

        self.parts.navigation.open_resolved_destination(
            target, message=f"Opened {entity_display_label_singular(destination)} {self.parts.scopes.label_for(target)} in the {destination} tab.")


    def on_dashboard_plot_point_clicked(self, tilt_series_id: str, frame_index: object = None) -> None:
        if not tilt_series_id:
            return
        self.state.dashboard.pending_dashboard_point_click = (tilt_series_id, frame_index)
        self.state.dashboard.dashboard_point_click_timer.start(QApplication.doubleClickInterval())


    def apply_pending_dashboard_point_click(self) -> None:
        pending = self.state.dashboard.pending_dashboard_point_click
        self.state.dashboard.pending_dashboard_point_click = None
        if pending is None:
            return
        tilt_series_id, _frame_index = pending
        if self.state.dashboard.dashboard_highlighted_tilt_series_id == tilt_series_id:
            self.parts.dashboard.set_dashboard_highlighted_tilt_series_id(None)
        else:
            self.parts.dashboard.set_dashboard_highlighted_tilt_series_id(tilt_series_id)


    def on_dashboard_plot_point_double_clicked(self, tilt_series_id: str, frame_index: object = None) -> None:
        self.state.dashboard.dashboard_point_click_timer.stop()
        self.state.dashboard.pending_dashboard_point_click = None
        self.parts.dashboard.navigate_to_tilt_series(
            tilt_series_id,
            frame_index=frame_index,
            source="summary plot",
            set_summary_highlight=True,
        )


    def on_dashboard_timeline_tilt_series_requested(self, tilt_series_id: str, frame_index: object = None) -> None:
        self.parts.dashboard.navigate_to_tilt_series(
            tilt_series_id,
            frame_index=frame_index,
            source="timeline",
            set_summary_highlight=False,
        )


    def on_dashboard_highlight_clear_requested(self) -> None:
        self.state.dashboard.dashboard_point_click_timer.stop()
        self.state.dashboard.pending_dashboard_point_click = None
        self.parts.dashboard.set_dashboard_highlighted_tilt_series_id(None)


    def set_dashboard_highlighted_tilt_series_id(self, tilt_series_id: str | None) -> None:
        if self.state.dashboard.dashboard_highlighted_tilt_series_id == tilt_series_id:
            return
        total_timer = QElapsedTimer()
        total_timer.start()
        previous = self.state.dashboard.dashboard_highlighted_tilt_series_id
        self.state.dashboard.dashboard_highlighted_tilt_series_id = tilt_series_id
        tree_timings = self.parts.project_panel.set_highlighted_tilt_series_id(previous, tilt_series_id)
        widget_timings: dict[str, int] = {}
        widget_timings = self.ui.session_dashboard.set_highlighted_tilt_series_id(tilt_series_id)
        LOGGER.info(
            "Highlight switch timing\n"
            "Lookup previous row:       %d ms\n"
            "Lookup new row:            %d ms\n"
            "Tree row repaint request:  %d ms\n"
            "Defocus repaint request:    %d ms\n"
            "Dose repaint request:       %d ms\n"
            "Timeline repaint request:   %d ms\n"
            "Total highlight switch:     %d ms\n"
            "Dashboard rebuilt:          no\n"
            "Plot data recomputed:       no\n"
            "Tree rebuilt:               no",
            tree_timings.get("lookup_previous_ms", 0),
            tree_timings.get("lookup_new_ms", 0),
            tree_timings.get("repaint_ms", 0),
            widget_timings.get("defocus_ms", 0),
            widget_timings.get("dose_ms", 0),
            widget_timings.get("timeline_ms", 0),
            total_timer.elapsed(),
        )


    def navigate_to_tilt_series(
        self, tilt_series_id: str, frame_index: object = None, *,
        source: str = "timeline", set_summary_highlight: bool = False,
    ) -> None:
        resolution = self.parts.navigation.resolve_action_target(tilt_series_id, entity_type=TiltSeries)
        target = resolution.target
        if not resolution.navigable or not isinstance(target, TiltSeries):
            self.parts.context.post_navigation_message(resolution.explanation)
            return
        self.parts.navigation.open_resolved_destination(
            target, message=f"Opened tilt series {self.parts.scopes.label_for(target)} from {source}.",
            frame_index=self.zero_based_frame_index(frame_index), highlight_summary=set_summary_highlight,
        )


    def select_tilt_series_frame(self, frame_index: int) -> None:
        viewer = self.ui.viewer_tabs.get(TabKey.TILT_SERIES)
        if viewer is not None:
            viewer.select_frame(frame_index)


    @staticmethod
    def zero_based_frame_index(frame_index: object) -> int | None:
        if frame_index is None:
            return None
        try:
            value = int(frame_index)
        except (TypeError, ValueError):
            return None
        return max(0, value - 1)


    def on_dashboard_scope_requested(self, object_id: str) -> None:
        """A summary's link to a sample or a session: that scope, on the Session tab."""

        resolution = self.parts.navigation.resolve_action_target(object_id, entity_type=(Sample, Session))
        target = resolution.target
        if not resolution.navigable:
            self.parts.context.post_navigation_message(resolution.explanation)
            return
        departure = self.parts.history.begin_navigation()
        context = self.parts.scopes.context_for_object(target)
        scope = context if isinstance(context, LinkedSampleGroup) else target if isinstance(target, (Sample, Session)) else None
        if scope is None:
            self.ui.tabs.setCurrentIndex(TAB_LABELS.index(TabKey.SESSION))
            self.parts.history.record_history(departure)
            return
        # A summary's own link: the sample's scope on the Session tab, with the
        # history entry recorded when that scope publishes.
        self.parts.viewers.open_scope(scope, destination_tab=TabKey.SESSION, departure=departure)


    @Slot(object)
    def on_dashboard_route_requested(self, route: DashboardFilterModel) -> None:
        self.on_dashboard_filter_requested(route.tab, route.query, route.tooltip,
                                          row_keys=route.row_keys, scope_label=route.scope_label)

    def on_dashboard_filter_requested(
        self,
        destination: str,
        query: str,
        reason: str = "",
        *,
        row_keys: frozenset[str] | None = None,
        scope_label: str = "",
    ) -> None:
        if destination not in TAB_LABELS:
            return
        departure = self.parts.history.begin_navigation()
        self.ui.tabs.setCurrentIndex(TAB_LABELS.index(destination))
        if destination == TabKey.TOMOGRAMS:
            self.ui.tomogram_tab.set_query(query, row_keys=row_keys, scope_label=scope_label)
        viewer_tab = self.ui.viewer_tabs.get(destination)
        if viewer_tab is not None:
            viewer_tab.set_query(query, row_keys=row_keys, scope_label=scope_label)
            if row_keys is not None:
                current = viewer_tab.list.currentItem()
                visible = viewer_tab.parts.list_pane.rows.visible_rows()
                if visible and (current is None or current.isHidden()):
                    viewer_tab.list.setCurrentItem(visible[0])
            if row_keys is not None:
                self.parts.context.show_toast(f"Showing {destination.lower()} entries from {scope_label or 'summary'}")
            elif query:
                self.parts.context.show_toast(f"Filtered {destination.lower()} list: {query}")
            else:
                self.parts.context.show_toast(f"Showing all {destination.lower()} entries")
        # State *why* at the destination. A filtered list with no explanation
        # is indistinguishable from a scope that simply has little in it.
        self.state.dashboard.dashboard_filter_reason = reason if query or row_keys is not None else ""
        self.parts.history.record_history(departure)
        self.parts.context.refresh_context_header()
