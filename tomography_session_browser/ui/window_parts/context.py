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
    TiltSeries,
)
from tomography_session_browser.diagnostics.profiling import (
    record_aggregate_phase,
)
from tomography_session_browser.ui.viewer.viewer_tab import ViewerTab
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
from tomography_session_browser.presentation.project_tree import (
    ProjectTreeGroup,
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
    tilt_validation_for,
)
from tomography_session_browser.presentation.describe.entities import (
    describe_object,
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

class ContextController(QObject):
    def __init__(self, window: QMainWindow, state: WindowState, ui: WindowUi, parts: WindowParts) -> None:
        super().__init__(window)
        self.window, self.state, self.ui, self.parts = window, state, ui, parts

    def on_tab_changed(self, index: int) -> None:
        if self.parts.viewers.applying_payload is None:
            self.parts.viewers.cancel_scope_preparation()
        if 0 <= index < len(TAB_LABELS):
            label = TAB_LABELS[index]
            tilt_tab = self.ui.viewer_tabs.get(TabKey.TILT_SERIES)
            if tilt_tab is not None and label != TabKey.TILT_SERIES:
                # A tilt-series zoom lasts while the reviewer stays with the
                # series. Leaving the tab drops it, so the series is fitted
                # when they come back.
                tilt_tab.discard_zoom()
            self.state.review.session_context_sync_generation += 1
            self.state.review.settings.last_tab = str(label)
            # Deferred: this writes a JSON file, and doing it synchronously
            # inside the tab-change handler put a disk round-trip in front of
            # every single tab switch.
            self.parts.shell.schedule_settings_save()
            generation = self.state.review.session_context_sync_generation
            viewer_tab = self.ui.viewer_tabs.get(label)
            if viewer_tab is not None:
                preserve_scope = self.parts.viewers.tab_activation_preserves_broad_scope()
                viewer_tab.ensure_initial_preview_loaded(notify_selection=not preserve_scope)
                if not self.state.review.suppress_viewer_context and (
                    preserve_scope or getattr(viewer_tab.parts.tab_selection, 'current_value', None) is None
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


    def describe_tab_display(self, viewer_tab: ViewerTab, shown: tuple[str, str]) -> None:
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
        value = getattr(viewer_tab.parts.tab_selection, 'current_value', None)
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

        if not self.state.review.workspace.sessions:
            return
        context = self.state.review.scope

        # Resolve the cheap parts first (title and which description to
        # build); the description itself is what costs, so it is only built
        # once we know the panel is not already showing this scope.
        if isinstance(context, ProjectTreeGroup):
            title = context.display_name
            describe = lambda: describe_project_group(context)  # noqa: E731
        elif context is None:
            groups = self.parts.scopes.project_groups()
            if len(groups) == 1:
                title = groups[0].display_name
                describe = lambda: describe_project_group(groups[0])  # noqa: E731
            else:
                title = "Project"
                describe = lambda: describe_loaded_sessions(self.state.review.workspace.sessions)  # noqa: E731
        else:
            title = self.parts.scopes.label_for(context)
            describe = lambda: self.parts.context.context_description(context)  # noqa: E731

        scope_key = f"{type(context).__name__}:{id(context)}"
        self.parts.context_panel.show_scope(scope_key, title, describe)


    def refresh_context_header(self) -> None:
        """Restate where the reviewer is, and why anything is unavailable."""

        # A drill-down's reason lives exactly as long as the filter it explains.
        # This is the command that owns the reset; ``current_context_stack()``
        # only reads it.
        if not self.parts.context.active_tab_filter_text():
            self.state.dashboard.dashboard_filter_reason = ""
        self.ui.context_header.set_context_stack(self.parts.context.current_context_stack())
        self.ui.context_header.set_unavailable_reasons(self.parts.context.unavailable_action_reasons())


    def active_tab_filter_text(self) -> str:
        """Filter text on the visible entity tab, if there is one."""

        index = self.ui.tabs.currentIndex()
        if not 0 <= index < len(TAB_LABELS):
            return ""
        tab = self.ui.viewer_tabs.get(TAB_LABELS[index])
        return tab.list_filter.text().strip() if tab is not None else ""


    def unavailable_action_reasons(self) -> tuple[str, ...]:
        """Reasons the current selection's offered jumps are disabled.

        These previously lived only in hover tooltips, which a keyboard user
        never sees. The header shows them inline instead.
        """

        label = TAB_LABELS[self.ui.tabs.currentIndex()]
        if label == TabKey.SESSION:
            return ()
        tab = self.ui.viewer_tabs.get(label)
        current = getattr(tab.parts.tab_selection, 'current_value', None) if tab is not None else None
        if current is None:
            return ()
        try:
            actions = self.parts.navigation.viewer_navigation_actions(current)
        except Exception:  # pragma: no cover - header must never break the page
            LOGGER.warning("Could not derive navigation actions for the context header", exc_info=True)
            return ()
        return tuple(
            action.tooltip
            for action in actions
            if not action.enabled and action.tooltip
        )


    def on_empty_state_action(self, command: str) -> None:
        """Run a recovery action offered by an empty-state panel."""

        if command == EMPTY_COMMAND_OPEN_SESSION:
            self.parts.loading.open_session()
        elif command == EMPTY_COMMAND_CLEAR_FILTER:
            self.parts.context.clear_active_tab_filter()
        elif command == EMPTY_COMMAND_RETRY:
            if self.state.review.failed_session_path:
                self.parts.loading.load_session(self.state.review.failed_session_path, replace=not self.state.review.workspace.sessions)
            else:
                self.parts.loading.refresh_session()
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
        ordered = [tabs.get(TAB_LABELS[self.ui.tabs.currentIndex()])]
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
        tab = self.ui.viewer_tabs.get(label)
        if tab is None:
            return
        tab.list_filter.clear()
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
            tab = self.ui.viewer_tabs.get(section_key)
            if tab is not None:
                state = tab.capture_view_state()
                current = getattr(tab.parts.tab_selection, 'current_value', None)
                selection_name = self.parts.scopes.label_for(current) if current is not None else ""
                marker_name = self.parts.context.marker_display_name(tab, state.marker_id)
                filter_text = state.filter_text
                if filter_text:
                    filter_total = tab.list.topLevelItemCount()
                    filter_visible = sum(
                        1
                        for index in range(filter_total)
                        if not tab.list.topLevelItem(index).isHidden()
                    )

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
            scope_name=describe_scope_value(scope_value),
            scope_kind=scope_kind,
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


    def show_arrival_message(self, text: str, timeout: int = 0) -> None:
        """Post an arrival message, folding in any filter notice it would hide."""

        notice = self.state.review.pending_filter_notice
        self.state.review.pending_filter_notice = None
        self.window.statusBar().showMessage(f"{text} {notice}" if notice else text, timeout)


    def viewer_frame_changed(self, value: Any, frame_index: int, frame_count: int) -> None:
        if self.state.review.suppress_viewer_context or self.state.review.preserve_tree_root_context:
            return
        if not isinstance(value, TiltSeries):
            return
        viewer = self.ui.viewer_tabs.get(TabKey.TILT_SERIES)
        if self.ui.tabs.currentIndex() != TAB_LABELS.index(TabKey.TILT_SERIES) or viewer is None or viewer.parts.tab_selection.current_value is not value:
            return
        self.ui.context_panel.set_title(self.parts.scopes.label_for(value))
        lines = [
            describe_object(
                value,
                tilt_validation=tilt_validation_for(value, self.state.viewer.viewer_tilt_validations, self.state.review.workspace),
            ),
            "",
            "Current frame:",
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
        self.ui.context_panel.set_text("\n".join(lines))


    def sync_context_heading(self) -> None:
        """Name the item once (harmonisation A1): ``ContextPanel.sync_heading``
        with the title of the page the current tab shows."""

        index = self.ui.tabs.currentIndex()
        label = TAB_LABELS[index] if 0 <= index < len(TAB_LABELS) else ""
        viewer_tab = self.ui.viewer_tabs.get(label)
        page_title = viewer_tab.header_title.text_full() if viewer_tab is not None else None
        self.parts.context_panel.sync_heading(label, page_title)


    def set_context(self, label: str, value: Any) -> None:
        timer = QElapsedTimer()
        timer.start()
        self.parts.context_panel.show_item(label, self.parts.context.context_description(value))
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


    def context_description(self, value: Any) -> str:
        batch_tab = self.ui.viewer_tabs.get(TabKey.BATCH_POSITION)
        return ContextDescriber(self.state.review.workspace, self.parts.scopes.context_for_object).describe(
            value,
            tilt_validation=(
                tilt_validation_for(value, self.state.viewer.viewer_tilt_validations, self.state.review.workspace)
                if isinstance(value, TiltSeries)
                else None
            ),
            list_status=(
                batch_tab.item_status_for_value(value)
                if isinstance(value, BatchPosition) and batch_tab is not None
                else None
            ),
        )

