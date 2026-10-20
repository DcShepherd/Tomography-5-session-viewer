"""Execute a planned jump; all destination and arrival policy lives below UI."""
from __future__ import annotations

from typing import Any, Callable, Protocol
from dataclasses import dataclass

from tomography_session_browser.presentation.navigation_plan import Jump
from tomography_session_browser.presentation.context_stack import entity_display_label_singular
from tomography_session_browser.services.navigation import NavigationResolution, tab_label_for_object
from tomography_session_browser.services.navigation.arrival import atlas_arrival_marker
from tomography_session_browser.presentation.tab_keys import TabKey
from tomography_session_browser.presentation.exposure_areas import exposure_areas


class NavigationHost(Protocol):
    """The window effects needed by a jump, without ownership of scientific rules."""
    viewer_tabs: dict[str, Any]

    def post_message(self, message: str) -> None: ...
    def inert_navigation_message(self, value: Any, key: str, resolution: NavigationResolution | None) -> str: ...
    def show_unresolved_relationship_panel(self, value: Any, key: str, resolution: NavigationResolution | None) -> None: ...
    def begin_navigation(self) -> Any: ...
    def enter_navigation_scope(self, target: Any) -> None: ...
    def select_viewer_object(self, target: Any) -> None: ...
    def label_for(self, value: Any) -> str: ...
    def show_arrival_message(self, message: str) -> None: ...
    def record_history(self, departure: Any) -> None: ...
    def navigate_to_tilt_series(self, tilt_id: str, *, source: str, set_summary_highlight: bool) -> None: ...


@dataclass(frozen=True)
class NavigationEffects:
    """Explicit window effects captured from their component owners for a jump."""
    viewer_tabs: dict[str, Any]
    post_message: Callable[[str], None]
    inert_navigation_message: Callable[[Any, str, NavigationResolution | None], str]
    show_unresolved_relationship_panel: Callable[[Any, str, NavigationResolution | None], None]
    begin_navigation: Callable[[], Any]
    enter_navigation_scope: Callable[[Any], None]
    select_viewer_object: Callable[[Any], None]
    label_for: Callable[[Any], str]
    show_arrival_message: Callable[[str], None]
    record_history: Callable[[Any], None]
    navigate_to_tilt_series: Callable[..., None]


def execute_jump(host: NavigationHost, source: Any, key: str, jump: Jump) -> None:
    resolution = jump.resolution
    if not resolution.navigable:
        # Posting its reason replaces the previous route's message; nothing
        # moves, so there is no departure.
        host.post_message(host.inert_navigation_message(source, key, resolution))
        host.show_unresolved_relationship_panel(source, key, resolution)
        return
    target = resolution.target
    if jump.tilt_hand_off:
        # This entry point owns its own scope, summary highlight and history.
        host.navigate_to_tilt_series(target.id, source="batch position exposure", set_summary_highlight=False)
        return
    region = jump.atlas_region if key == "atlas" else ()
    # The previous route's message ends, and where the reviewer is going from
    # is taken, before anything changes.
    departure = host.begin_navigation()
    host.enter_navigation_scope(target)
    host.select_viewer_object(target)
    tab_label = tab_label_for_object(target)
    subject = getattr(source, "name", None) or source.id
    subject = f"{entity_display_label_singular(tab_label_for_object(source))} {subject}"
    destination = f"{entity_display_label_singular(tab_label)} {host.label_for(target)}"
    if key == "atlas":
        tab = host.viewer_tabs[TabKey.ATLAS]
        found = atlas_arrival_marker(region, tab.viewer.markers)
        message = f"Opened {destination} linked to {subject}"
        if found is None:
            tab.select_marker(None)
            message += ". Its position is not marked on the Atlas."
        else:
            marker_id, kind = found
            tab.select_marker(marker_id, arrive=True, frame=jump.frame)
            message += "." if kind is None else f", at its {kind}."
    else:
        marker_id = jump.arrival_marker_id
        message = f"Opened {destination} linked to {subject}."
        if tab_label is not None and marker_id:
            tab = host.viewer_tabs[tab_label]
            tab.select_marker(marker_id, arrive=True, frame=jump.frame)
            area = next((area for area in exposure_areas(tab.held_markers(target) or ())
                         if area.marker_id == marker_id), None)
            if area is not None:
                message += f" {area.title} selected."
                if area.on_image is False:
                    message += " It is not on this image."
        elif tab_label is not None and jump.clear_unmatched_arrival:
            host.viewer_tabs[tab_label].select_marker(None)
            message += " No single exposure area matches this arrival, so none is highlighted."
    host.show_arrival_message(message)
    host.record_history(departure)
