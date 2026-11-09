"""ContextController owns its window interaction responsibilities."""
from __future__ import annotations
import logging
from typing import (
    Any,
    Callable,
)
from PySide6.QtCore import (
    QObject,
    QElapsedTimer,
    Qt,
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
    BatchPosition,
    Sample,
    Session,
    TiltSeries,
)
from tomography_session_browser.presentation.scopes import LinkedSampleGroup
from tomography_session_browser.diagnostics.profiling import (
    record_aggregate_phase,
)
from tomography_session_browser.ui.viewer.viewer_tab import ViewerTab
from tomography_session_browser.ui.tomograms.tab import TomogramTab
from tomography_session_browser.domain.tomograms import Tomogram
from tomography_session_browser.presentation.tomogram_context import TomogramContext, tomogram_context
from tomography_session_browser.presentation.processing_context import ProcessingContext, processing_context
from tomography_session_browser.presentation.dashboard.tomogram_summary_input import summary_input
from tomography_session_browser.presentation.dashboard.summary import ProcessingSummary
from tomography_session_browser.presentation.workspace import EntityKind
from tomography_session_browser.presentation.context_stack import (
    ContextStack,
    borrowed_atlas_note,
    build_context_stack,
    describe_scope_value,
)
from tomography_session_browser.presentation.empty_states import (
    COMMAND_CHOOSE_ANOTHER as EMPTY_COMMAND_CHOOSE_ANOTHER,
    COMMAND_CLEAR_FILTER as EMPTY_COMMAND_CLEAR_FILTER,
    COMMAND_COPY_DETAILS as EMPTY_COMMAND_COPY_DETAILS,
    COMMAND_OPEN_SESSION as EMPTY_COMMAND_OPEN_SESSION,
    COMMAND_RETRY as EMPTY_COMMAND_RETRY,
    COMMAND_RETURN_TO_SCOPE as EMPTY_COMMAND_RETURN_TO_SCOPE,
    COMMAND_SHOW_METADATA as EMPTY_COMMAND_SHOW_METADATA,
)
from tomography_session_browser.presentation.breadcrumbs import scope_crumbs
from tomography_session_browser.presentation.project_tree import (
    ProjectTreeGroup,
    scope_ancestors,
)
from tomography_session_browser.ui.widgets.toast import (
    TONE_INFO,
    TONE_SUCCESS,
)
from tomography_session_browser.presentation.workspace import (
    Scope,
)
from tomography_session_browser.presentation.describe.context import (
    ContextDescriber,
    describe_loaded_sessions,
    describe_project_group,
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
    compact_pair,
    LOGGER,
)
from tomography_session_browser.presentation.tab_keys import TabKey
from tomography_session_browser.presentation.tomogram_selection import AllTomograms, FolderTomograms

class ContextController(QObject):
    def __init__(self, window: QMainWindow, state: WindowState, ui: WindowUi, parts: WindowParts) -> None:
        super().__init__(window)
        self.window, self.state, self.ui, self.parts = window, state, ui, parts

    def on_tab_changed(self, index: int) -> None:
        if self.parts.viewers.applying_payload is None:
            # The reviewer chose another tab: a navigation, so the last route's
            # message ends, and a scope still being prepared is abandoned.
            self.parts.history.clear_navigation_message()
            self.parts.viewers.cancel_scope_preparation()
        if 0 <= index < len(TAB_LABELS):
            label = TAB_LABELS[index]
            tilt_tab = self.ui.viewer_tabs.get(TabKey.TILT_SERIES)
            if tilt_tab is not None and label != TabKey.TILT_SERIES:
                # A tilt-series zoom lasts while the reviewer stays with the
                # series. Leaving the tab drops it, so the series is fitted
                # when they come back.
                tilt_tab.parts.frame_scrubber.leave_tab()
                tilt_tab.discard_zoom()
            self.state.review.session_context_sync_generation += 1
            self.state.review.settings.last_tab = str(label)
            # Deferred: this writes a JSON file, and doing it synchronously
            # inside the tab-change handler put a disk round-trip in front of
            # every single tab switch.
            self.parts.shell.schedule_settings_save()
            generation = self.state.review.session_context_sync_generation
            self.parts.shell.sync_image_settings()
            viewer_tab = self.ui.review_tabs.get(label)
            if isinstance(viewer_tab, TomogramTab):
                viewer_tab.activate()
                self.parts.context.describe_tab_display(viewer_tab, (self.ui.context_panel.text(), self.ui.context_panel.toPlainText()))
            elif viewer_tab is not None:
                preserve_scope = self.parts.viewers.tab_activation_preserves_broad_scope()
                viewer_tab.ensure_initial_preview_loaded(notify_selection=not preserve_scope)
                if not self.state.review.suppress_viewer_context and (
                    preserve_scope or viewer_tab.current_object() is None
                ):
                    # Describing an item costs 25-100 ms, so it waits for the
                    # page's first paint, like the Session tab below.
                    shown = (self.ui.context_panel.text(), self.ui.context_panel.toPlainText())
                    self.parts.context.defer_until_page_repaint(
                        generation,
                        lambda: self.parts.context.describe_tab_display(viewer_tab, shown),
                    )
            elif label == TabKey.SESSION:
                if self.parts.theme.dashboard_stale:
                    # The theme changed while this tab was hidden. Recoloured
                    # before its first paint, so the old colours never show.
                    self.parts.theme.dashboard_stale = False
                    self.ui.session_dashboard.refresh_theme()
                # Commit ad273c5 put the context/status rebuild directly in
                # this signal handler. QTabWidget cannot display its new page
                # until the handler returns, so keep the panel correct but
                # allow one repaint turn before doing that secondary work.
                self.parts.context.defer_until_page_repaint(generation, self.parts.context.sync_context_panel_to_active_scope)
            self.parts.context.refresh_context_header()
            self.parts.context.sync_context_heading()


    def describe_tab_display(self, viewer_tab: ViewerTab | TomogramTab, shown: tuple[str, str]) -> None:
        """Point the context panel at what the activated tab shows.

        A broad scope (a project group) survives a tab switch, so the tab's
        item is not announced as a selection, which would narrow the scope.
        The panel must still follow the tab: left alone, it went on
        describing the previous tab's item beside this tab's image. A tab
        showing nothing gets the scope's description instead.

        ``shown`` is the panel's title and text at the switch. If they have
        changed since, something newer (a selection, a jump's destination)
        is already described there.
        """

        if (self.ui.context_panel.text(), self.ui.context_panel.toPlainText()) != shown:
            return
        value = viewer_tab.current_object()
        if value is None:
            self.parts.context.sync_context_panel_to_active_scope()
        else:
            self.parts.context.set_context(self.parts.scopes.label_for(value), value)


    def defer_until_page_repaint(self, generation: int, work: Callable[[], None]) -> None:
        """Run ``work`` once the tab page just shown has had a repaint turn.

        Two turns: the first lets the page paint. Dropped if the tab changes
        again first (``generation`` moves on).
        """

        def run() -> None:
            if generation == self.state.review.session_context_sync_generation:
                work()

        QTimer.singleShot(0, self.window, lambda: QTimer.singleShot(0, self.window, run))


    def sync_context_panel_to_active_scope(self) -> None:
        """Point the context panel and status bar at the dashboard's scope.

        Building a scope description walks the whole group, so the work is
        skipped when the panel is already showing this scope — returning to
        the Session tab repeatedly must not re-derive identical text.
        """

        if not self.state.review.workspace.has_sources:
            self.parts.context_panel.show_item("", "", item=None)
            self.parts.context.sync_context_heading()
            self.window.statusBar().clearMessage()
            return
        context = self.state.review.scope

        # Resolve the cheap parts first (title and which description to
        # build); the description itself is what costs, so it is only built
        # once we know the panel is not already showing this scope.
        if isinstance(context, ProjectTreeGroup):
            title = context.display_name
            describe = lambda: describe_project_group(context, self.state.review.workspace)  # noqa: E731
        elif context is None:
            groups = self.parts.scopes.project_groups()
            if len(groups) == 1:
                title = groups[0].display_name
                describe = lambda: describe_project_group(groups[0], self.state.review.workspace)  # noqa: E731
            else:
                title = "Project"
                describe = lambda: describe_loaded_sessions(self.state.review.workspace.sessions)  # noqa: E731
        else:
            title = self.parts.scopes.label_for(context)
            describe = lambda: self.parts.context.context_description(context)  # noqa: E731

        self.parts.context.show_scope_description(f"{type(context).__name__}:{id(context)}", title, describe)


    def show_scope_description(self, scope_key: str, title: str, describe: Callable[[], str]) -> None:
        """Show a scope's summary in the panel, and name the heading for it.

        The one way a scope reaches the panel: the panel remembers that what it
        shows is a scope, so the Session tab, whose page titles that scope, can
        hide the panel's own title line.
        """

        model = self.context_model(self.state.review.scope)
        if model is not None:
            self.parts.context_panel.show_model(model, item=self.state.review.scope, scope=True)
        else:
            self.parts.context_panel.show_scope(scope_key, title, describe)
        self.parts.context.sync_context_heading()


    def refresh_context_header(self) -> None:
        """Restate where the reviewer is."""

        # A drill-down's reason lives exactly as long as the filter it explains.
        # This is the command that owns the reset; ``current_context_stack()``
        # only reads it.
        if not self.parts.context.active_tab_filter_text():
            self.state.dashboard.dashboard_filter_reason = ""
        self.ui.context_header.set_context_stack(self.parts.context.current_context_stack())
        scope = self.state.review.scope
        self.parts.context_panel.sources.set_sources(self.parts.project.sources_for_scope(scope))
        self.parts.report.refresh_action()



    def active_tab_filter_text(self) -> str:
        """Filter text on the visible entity tab, if there is one."""

        index = self.ui.tabs.currentIndex()
        if not 0 <= index < len(TAB_LABELS):
            return ""
        tab = self.ui.review_tabs.get(TAB_LABELS[index])
        return tab.filter_description() if tab is not None else ""


    def on_empty_state_action(self, command: str) -> None:
        """Run a recovery action offered by an empty-state panel."""

        if command == EMPTY_COMMAND_OPEN_SESSION:
            self.parts.loading.open_session()
        elif command == EMPTY_COMMAND_CLEAR_FILTER:
            self.parts.context.clear_active_tab_filter()
        elif command == EMPTY_COMMAND_RETRY:
            self.parts.loading.retry_failed_loads()
        elif command == EMPTY_COMMAND_CHOOSE_ANOTHER:
            self.parts.loading.open_session()
        elif command == EMPTY_COMMAND_RETURN_TO_SCOPE:
            self.ui.tabs.setCurrentIndex(TAB_LABELS.index(TabKey.SESSION))
        elif command == EMPTY_COMMAND_SHOW_METADATA:
            if self.parts.chrome.drawer_mode:
                self.parts.chrome.show_context_action_checked(True)
                self.parts.chrome.open_context_drawer(True)
            else:
                self.ui.context_dock.show()
                self.ui.context_dock.raise_()
            self.ui.context_panel.setFocus(Qt.FocusReason.ShortcutFocusReason)
        elif command == EMPTY_COMMAND_COPY_DETAILS:
            # One owner for the clipboard. The panel used to write the text and
            # the window announce it, which split a single action across two
            # files with only a comment holding it together.
            self.parts.context.copy_empty_state_details()


    def copy_empty_state_details(self) -> None:
        """Copy the visible empty-state panel's details, and say so."""

        # Ordered by intent, not by realised visibility: the active tab first,
        # then any panel currently holding details. Keying off ``isVisible()``
        # would report every child of an unshown window as absent — the same
        # mistake R1 and R2 each had to correct once already.
        tabs = self.ui.viewer_tabs
        active = TAB_LABELS[self.ui.tabs.currentIndex()]
        ordered = [self.ui.session_dashboard if active == TabKey.SESSION else tabs.get(active)]
        ordered.extend(tabs.values())
        details = ""
        for tab in ordered:
            panel = getattr(tab, "empty_state_panel", None)
            if panel is not None and panel.copyable_details():
                details = panel.copyable_details()
                break
        clipboard = QApplication.clipboard()
        if not details or clipboard is None:
            self.parts.context.show_toast("There are no details to copy.")
            return
        clipboard.setText(details)
        self.parts.context.show_toast("Details copied to the clipboard.", tone=TONE_SUCCESS)


    def clear_active_tab_filter(self) -> None:
        """Clear the filter on the visible tab, from the header's Clear action."""

        label = TAB_LABELS[self.ui.tabs.currentIndex()]
        tab = self.ui.review_tabs.get(label)
        if tab is None:
            return
        tab.set_query("")
        self.parts.context.refresh_context_header()


    def current_context_stack(self) -> ContextStack:
        """Where the reviewer currently is, on every axis.

        The scope comes from the project tree, never from the selected object:
        presenting a selection as the review scope was the specific confusion
        C1 exists to remove.
        """

        scope_value = self.parts.scopes.viewer_tab_context()
        scope_kind = getattr(scope_value, "kind", "")
        if not isinstance(scope_kind, str):
            scope_kind = ""

        section_key = TAB_LABELS[self.ui.tabs.currentIndex()]
        selection_name = ""
        marker_name = ""
        filter_text = ""
        filter_visible: int | None = None
        filter_total: int | None = None
        if section_key and section_key != TabKey.SESSION:
            tab = self.ui.review_tabs.get(section_key)
            if tab is not None:
                current = tab.current_object()
                selection_name = self.parts.scopes.label_for(current) if current is not None else ""
                if isinstance(tab, ViewerTab):
                    marker_name = self.parts.context.marker_display_name(tab, tab.selected_marker_id())
                filter_text = tab.filter_description()
                if filter_text:
                    filter_visible, filter_total = tab.list_counts()

        # A dashboard drill-down explains its own filter; that reason outranks
        # the scope's standing relationship note while the filter is applied.
        #
        # Reading it is all that happens here. Clearing it used to happen here
        # too, which made a query method change what the next call returned —
        # so a second caller (a tooltip, a test) silently discarded the
        # reason. The filter handlers own the reset now.
        relationship = self.parts.context.scope_relationship_note(scope_value)
        if filter_text and self.state.dashboard.dashboard_filter_reason:
            relationship = self.state.dashboard.dashboard_filter_reason

        return build_context_stack(
            scope_name=self.parts.scopes.label_for(scope_value) if isinstance(scope_value, AllTomograms | FolderTomograms) else describe_scope_value(scope_value),
            scope_kind=scope_kind,
            breadcrumb_override=scope_crumbs(scope_value, self.parts.scopes.project_groups(),
                                             titles_scope=section_key == TabKey.SESSION,
                                             scope_name=self.parts.scopes.label_for(scope_value)),
            ancestors=[
                describe_scope_value(ancestor)
                for ancestor in scope_ancestors(scope_value, self.parts.scopes.project_groups())
            ],
            titles_scope=section_key == TabKey.SESSION,
            section_key=section_key,
            selection_name=selection_name,
            marker_name=marker_name,
            relationship=relationship,
            filter_text=filter_text,
            filter_visible=filter_visible,
            filter_total=filter_total,
        )


    @staticmethod
    def marker_display_name(tab: ViewerTab, marker_id: str | None) -> str:
        """Human-readable name for the active marker, if there is one."""

        if not marker_id:
            return ""
        for marker in getattr(tab.viewer.parts.marker_layer, 'last_display_markers', ()) or ():
            if getattr(marker, "id", None) == marker_id:
                label = getattr(marker, "label", None)
                if isinstance(label, str) and label.strip():
                    return label.strip()
                break
        # Marker ids are structured "source:type:object"; the tail is the most
        # meaningful fragment when no label was painted.
        tail = marker_id.rsplit(":", 1)[-1]
        return tail if tail and tail != marker_id else ""


    def scope_relationship_note(self, scope_value: Scope) -> str:
        """Borrowed-Atlas or unresolved-link context for the current scope.

        Every warning is stated, not just the first. A group with two
        unresolved links had the second silently dropped, and the notes row is
        already built to carry several items — the label elides and its tooltip
        keeps the full text.
        """

        if isinstance(scope_value, ProjectTreeGroup):
            if scope_value.warnings:
                return " · ".join(scope_value.warnings)
            if scope_value.kind == "linked" and scope_value.atlas_session is not None:
                return borrowed_atlas_note(scope_value.atlas_session.name)
        return ""


    def show_toast(self, text: str, *, tone: str = TONE_INFO) -> None:
        """Confirm something that just happened, briefly (plan F5.9).

        For news only: where a jump went, why one could not, and work in
        progress stay in the status bar, which does not fade.
        """

        self.ui.toast.show_message(text, tone=tone)


    def post_navigation_message(self, text: str, timeout: int = 0) -> None:
        """Say where a route went, or why it would not go.

        An untimed message stays until the next navigation begins and clears
        it (``HistoryController.begin_navigation``); one with a timeout ends
        itself. The text is remembered so that only this message is cleared,
        never a status the bar shows for other work.
        """

        self.state.history.navigation_message = text if not timeout else ""
        self.window.statusBar().showMessage(text, timeout)


    def show_arrival_message(self, text: str, timeout: int = 0) -> None:
        """Post an arrival message, folding in any filter notice it would hide."""

        notice = self.state.review.pending_filter_notice
        self.state.review.pending_filter_notice = None
        self.parts.context.post_navigation_message(f"{text} {notice}" if notice else text, timeout)


    def viewer_frame_changed(self, value: Any, frame_index: int | None, frame_count: int) -> None:
        if self.state.review.suppress_viewer_context:
            return
        if self.state.review.preserve_tree_root_context and not self.parts.context_panel.describes(value):
            return
        if not isinstance(value, TiltSeries):
            return
        viewer = self.ui.viewer_tabs.get(TabKey.TILT_SERIES)
        if self.ui.tabs.currentIndex() != TAB_LABELS.index(TabKey.TILT_SERIES) or viewer is None or viewer.current_object() is not value:
            return
        self.parts.context_panel.show_item(self.parts.scopes.label_for(value),
                                           self.with_frame(value, viewer, frame_index, frame_count), item=value)

    def with_frame(self, value: TiltSeries, viewer: ViewerTab, frame_index: int | None, frame_count: int) -> str:
        """A tilt series' description followed by the frame its viewer displays."""
        return "\n\n".join((self.parts.context.context_description(value),
                            "\n".join(self.frame_context_lines(viewer, value, frame_index, frame_count))))

    def displayed_description(self, value: Any) -> str:
        """The text ``set_context`` shows for ``value``: the tilt series on its page adds its displayed frame.

        Tools that check the panel compare against this, so they cannot drift from it (GR-07).
        """
        if (isinstance(value, TiltSeries) and value is self.parts.context.page_item()
                and not self.state.review.suppress_viewer_context):
            viewer = self.ui.viewer_tabs[TabKey.TILT_SERIES]
            return self.with_frame(value, viewer, viewer.parts.frame_scrubber.displayed_frame_index(),
                                   viewer.slice_slider.maximum() + 1)
        return self.parts.context.context_description(value)

    def frame_context_lines(self, viewer: ViewerTab, value: TiltSeries, frame_index: int | None, frame_count: int) -> list[str]:
        scrubber = viewer.parts.frame_scrubber
        pending = scrubber.pending_text(frame_count)
        if frame_index is None:
            return [scrubber.frame_text(frame_count).replace("\n", "; ")]
        lines = [
            "Displayed frame:",
            f"  Frame: {frame_index + 1} of {frame_count}",
            f"  Raw stack index: {frame_index}",
        ]
        angle = self.parts.viewers.tilt_angle_for(value, frame_index)
        source = self.parts.viewers.frame_angle_source(value, frame_count)
        if angle is not None:
            lines.append(f"  Tilt angle: {angle}")
        lines.append(f"  Angle source: {source}")
        frame_metadata = self.parts.viewers.frame_metadata_for(value, frame_index)
        if frame_metadata:
            beam_shift = compact_pair(frame_metadata.get("beam_shift_x"), frame_metadata.get("beam_shift_y"))
            if beam_shift:
                lines.append(f"  Beam shift X/Y: {beam_shift}")
        if pending:
            lines.append(f"  {pending}")
        return lines


    def sync_context_heading(self) -> None:
        """Name the item once (harmonisation A1): ``ContextPanel.sync_heading``
        with the item the page of the current tab titles."""

        index = self.ui.tabs.currentIndex()
        label = TAB_LABELS[index] if 0 <= index < len(TAB_LABELS) else ""
        self.parts.context_panel.sync_heading(label, self.parts.context.page_item())


    def set_context(self, label: str, value: Any) -> None:
        timer = QElapsedTimer()
        timer.start()
        if model := self.context_model(value):
            self.parts.context_panel.show_model(model, item=value, scope=isinstance(model, ProcessingContext))
            self.parts.context.refresh_context_header()
            self.parts.context.sync_context_heading()
            return
        self.parts.context_panel.show_item(
            label,
            self.displayed_description(value),
            item=value,
            scope=isinstance(value, Session | Sample | LinkedSampleGroup | ProjectTreeGroup | AllTomograms | FolderTomograms),
        )
        self.parts.context.refresh_context_header()
        self.parts.context.sync_context_heading()
        elapsed_ms = timer.elapsed()
        LOGGER.debug(
            "context panel update label=%s value_type=%s total_ms=%d",
            label,
            type(value).__name__,
            elapsed_ms,
        )
        # Aggregate into the active profile so it surfaces in the perf
        # report even when many small updates happen in sequence.
        try:
            record_aggregate_phase("update_context_panel", elapsed_ms / 1000.0)
        except Exception:  # pragma: no cover - perf must never break selection
            LOGGER.debug("Could not record context panel timing", exc_info=True)


    def page_item(self) -> Any:
        """The item the active tab's page titles, or None (Session tab, empty tab)."""

        index = self.ui.tabs.currentIndex()
        tab = self.ui.review_tabs.get(TAB_LABELS[index]) if 0 <= index < len(TAB_LABELS) else None
        return tab.current_object() if tab is not None else None

    def describes_page_item(self, value: Any) -> bool:
        """Whether the panel would describe the item the page already titles.

        Decided here, once, for the text (``context_description``) and for the
        heading (``sync_context_heading``): a marker selection, a scope or an
        item the tab does not show is not the page's item.
        """

        return value is not None and value is self.parts.context.page_item()

    def context_model(self, value: Any) -> TomogramContext | ProcessingContext | None:
        workspace = self.state.review.workspace
        if isinstance(value, Tomogram):
            tab = self.ui.tomogram_tab
            current = tab.current_object() is value
            sources = tuple(source for source in workspace.processing_sources if source.id == value.source_id)
            return tomogram_context(value, tab.volume() if current else None, workspace.tomogram_links.get(value.id),
                tab.state.spacing_override if current else None, source=sources[0] if len(sources) == 1 else None,
                parents=tuple(workspace.entities(EntityKind.TILT_SERIES)))
        if isinstance(value, AllTomograms | FolderTomograms) or (isinstance(value, ProjectTreeGroup) and value.processing_source is not None):
            payload = summary_input(workspace, value, self.state.review.tomogram_sort, self.state.review.tomogram_review)
            displayed = self.ui.session_dashboard._model
            if (displayed is not None and isinstance(displayed.summary, ProcessingSummary)
                    and displayed.summary.input.key == payload.key and displayed.summary.input.sources == payload.sources
                    and not payload.notice):
                from dataclasses import replace
                payload = replace(payload, notice=displayed.summary.notice)
            return processing_context(payload, self.state.review.source_reads)
        return None

    def context_description(self, value: Any) -> str:
        if model := self.context_model(value):
            return model.to_text()
        batch_tab = self.ui.viewer_tabs.get(TabKey.BATCH_POSITION)
        return ContextDescriber(self.state.review.workspace, self.parts.scopes.context_for_object).describe(
            value,
            describes_page_item=self.parts.context.describes_page_item(value),
            list_status=(
                batch_tab.item_status_for_value(value)
                if isinstance(value, BatchPosition) and batch_tab is not None
                else None
            ),
        )

