"""Stable tab registration and concrete factories at the UI composition boundary.

This table records identity, label and factory only. Scientific availability,
prepared payloads and rendering remain with their existing owners.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from tomography_session_browser.domain.markers import MarkerType
from tomography_session_browser.presentation.context_stack import ENTITY_DISPLAY_LABELS
from tomography_session_browser.presentation.tab_keys import TabKey
from tomography_session_browser.ui.dashboard.widget import SessionDashboard
from tomography_session_browser.ui.navigation_icons import TAB_ICONS
from tomography_session_browser.ui.viewer.viewer_tab import ViewerTab
from tomography_session_browser.ui.tomograms.tab import TomogramTab
from tomography_session_browser.ui.tomograms.navigation import TomogramNavigation

if TYPE_CHECKING:
    from tomography_session_browser.ui.window_parts.contracts import WindowParts, WindowUi


def create_summary_tab(label: TabKey, ui: WindowUi, parts: WindowParts) -> SessionDashboard:
    ui.session_dashboard = SessionDashboard()
    def connect_summary() -> None:
        ui.session_dashboard.navigate_requested.connect(parts.dashboard.navigate_to_tab)
        ui.session_dashboard.tomograms_requested.connect(parts.dashboard.open_tomograms_summary)
        ui.session_dashboard.tile_item_clicked.connect(parts.dashboard.on_dashboard_tile_clicked)
        ui.session_dashboard.scope_requested.connect(parts.dashboard.on_dashboard_scope_requested)
        ui.session_dashboard.filter_requested.connect(parts.dashboard.on_dashboard_route_requested)
        ui.session_dashboard.point_clicked.connect(parts.dashboard.on_dashboard_plot_point_clicked)
        ui.session_dashboard.point_double_clicked.connect(parts.dashboard.on_dashboard_plot_point_double_clicked)
        ui.session_dashboard.timeline_tilt_series_requested.connect(parts.dashboard.on_dashboard_timeline_tilt_series_requested)
        ui.session_dashboard.highlight_clear_requested.connect(parts.dashboard.on_dashboard_highlight_clear_requested)
        ui.session_dashboard.source_folders_requested.connect(parts.chrome.reveal_context_panel)

    ui.connections.append(connect_summary)
    return ui.session_dashboard


def create_image_tab(label: TabKey, ui: WindowUi, parts: WindowParts) -> ViewerTab:
    display_label = ENTITY_DISPLAY_LABELS.get(label, label)
    viewer_tab = ViewerTab(
        f"Open a session to browse {display_label.lower()} previews.",
        entity_icon=TAB_ICONS[label],
        show_list=True,
        protected_roots=lambda: parts.shell.state.review.workspace.source_roots,
        show_tilt_controls=label == TabKey.TILT_SERIES,
        on_item_selected=parts.viewers.viewer_item_selected,
        on_frame_changed=parts.context.viewer_frame_changed,
        tilt_angle_for=parts.viewers.tilt_angle_for,
        on_marker_selected=parts.viewers.viewer_marker_selected,
        on_marker_opened=parts.viewers.viewer_marker_opened,
        on_cluster_member_activated=parts.navigation.atlas_cluster_member_activated
        if label == TabKey.ATLAS
        else None,
        on_marker_selection_cleared=parts.viewers.viewer_marker_selection_cleared,
        show_marker_controls=label != TabKey.TILT_SERIES,
        atlas_lod=label == TabKey.ATLAS,
        status_segments={
            TabKey.ATLAS: ("missing",),
            TabKey.OVERVIEW: ("with-acquisitions", "no-acquisitions"),
            TabKey.SEARCH_MAP: ("failed", "incomplete", "complete"),
        }.get(label, ("failed", "complete")),
        # Exposure areas are chosen on a batch position, and only there (D9).
        show_exposure_chooser=label == TabKey.BATCH_POSITION,
        # The search-map tile grid covers much of an Overview;
        # selecting a tile on press made that area impossible to
        # pan. Double-click still opens the search map.
        pan_through_marker_types=(MarkerType.SEARCH_MAP,) if label == TabKey.OVERVIEW else (),
        # Following something small through the tilt: stepping
        # frames keeps the zoom; another series opens fitted.
        frames_share_zoom=label == TabKey.TILT_SERIES,
        navigation_actions_for=parts.navigation.viewer_navigation_actions
        if label in {TabKey.OVERVIEW, TabKey.SEARCH, TabKey.SEARCH_MAP, TabKey.BATCH_POSITION, TabKey.TILT_SERIES}
        else None,
        on_navigation_requested=parts.navigation.viewer_navigation_requested
        if label in {TabKey.OVERVIEW, TabKey.SEARCH, TabKey.SEARCH_MAP, TabKey.BATCH_POSITION, TabKey.TILT_SERIES}
        else None,
    )
    return viewer_tab


def create_tomogram_tab(label: TabKey, ui: WindowUi, parts: WindowParts) -> TomogramTab:
    state = parts.shell.state
    navigation = TomogramNavigation(parts)
    from tomography_session_browser.ui.window_parts.tag_projects import TagProjects
    parts.tag_projects = TagProjects(parts.shell.window, lambda: ui.tomogram_tab.protected_roots())
    parts.tag_projects.loaded_sources = lambda: state.review.workspace.processing_sources
    tab = TomogramTab(settings=state.review.settings, save_settings=state.review.save_settings,
                      review_state=state.review.tomogram_review,
                      navigation_actions_for=navigation.actions, tag_projects=parts.tag_projects,
                      protected_roots=lambda: state.review.workspace.source_roots)
    ui.tomogram_tab = tab
    def connect_tomograms() -> None:
        def refresh_metadata() -> None:
            if tab.isVisible():
                value = tab.current_object()
                if value is None:
                    parts.context.sync_context_panel_to_active_scope()
                else:
                    parts.context.set_context(parts.scopes.label_for(value), value)
        tab.item_selected.connect(parts.viewers.viewer_item_selected)
        tab.metadata_changed.connect(refresh_metadata)
        ui.context_panel_action.toggled.connect(lambda checked: refresh_metadata() if checked else None)
        tab.query_changed.connect(lambda _query: parts.context.refresh_context_header())
        tab.review_metadata_changed.connect(parts.dashboard.request_processing_summary_refresh)
        tab.review_list.opened_changed.connect(parts.dashboard.request_processing_summary_refresh)
        def sort_changed() -> None:
            state.review.tomogram_sort = tab.review_list.sort.currentText()
            parts.dashboard.request_processing_summary_refresh()
        tab.review_list.sort.currentIndexChanged.connect(sort_changed)
        ui.tabs.currentChanged.connect(parts.dashboard.request_processing_summary_refresh)
        tab.image_settings_requested.connect(lambda _visible: parts.shell.sync_image_settings())
        tab.focus_requested.connect(parts.shell.tomogram_focus.set_enabled)
        tab.focus_preparing.connect(parts.shell.tomogram_focus.prepare)
        tab.focus_refit_requested.connect(parts.shell.tomogram_focus.request_refit)
        ui.project_panel_action.triggered.connect(lambda checked: parts.shell.tomogram_focus.panel_invoked(checked, "project"))
        ui.context_panel_action.triggered.connect(lambda checked: parts.shell.tomogram_focus.panel_invoked(checked, "context"))
        tab.load_requested.connect(parts.loading.open_session)
        tab.processing_scope_requested.connect(parts.viewers.open_processing_scope)
        tab.version_selection.request_version = parts.viewers.select_tomogram_version
        tab.all_tomograms_requested.connect(parts.viewers.open_all_tomograms)
        tab.parent_requested.connect(parts.viewers.open_tomogram_parent)
        tab.link_requested.connect(navigation.open)
    ui.connections.append(connect_tomograms)
    return tab


@dataclass(frozen=True, slots=True)
class TabSpec:
    key: TabKey
    label: str
    factory: Callable[[TabKey, WindowUi, WindowParts], SessionDashboard | ViewerTab | TomogramTab]


TAB_SPECS: tuple[TabSpec, ...] = (
    TabSpec(TabKey.SESSION, ENTITY_DISPLAY_LABELS[TabKey.SESSION], create_summary_tab),
    *(TabSpec(key, ENTITY_DISPLAY_LABELS[key], create_image_tab) for key in (
        TabKey.ATLAS, TabKey.OVERVIEW, TabKey.SEARCH_MAP, TabKey.SEARCH,
        TabKey.BATCH_POSITION, TabKey.TILT_SERIES,
    )),
    TabSpec(TabKey.TOMOGRAMS, ENTITY_DISPLAY_LABELS[TabKey.TOMOGRAMS], create_tomogram_tab),
)
