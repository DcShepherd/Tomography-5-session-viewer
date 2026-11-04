"""Install a prepared raster payload without leaking preview policy to the window."""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from tomography_session_browser.domain.identity import entity_key_text
from tomography_session_browser.domain.markers import ImageMarker
from tomography_session_browser.domain.status import RowStatus
from tomography_session_browser.presentation.viewer_tabs import PreparedViewerTab, VIEWER_TAB_SOURCES
from tomography_session_browser.presentation.tab_keys import TabKey
from tomography_session_browser.services.item_status import ItemListStatus
from tomography_session_browser.ui.viewer.models import ViewerViewState

if TYPE_CHECKING:
    from tomography_session_browser.ui.viewer.viewer_tab import ViewerTab


def install_prepared_raster(
    tab: ViewerTab, payload: PreparedViewerTab, *,
    label_for: Callable[[Any], str],
    markers_for: Callable[[Any], list[ImageMarker]] | None,
    auto_load_preview: bool,
    restore_state: ViewerViewState | None,
) -> None:
    def status_for(value: Any) -> ItemListStatus:
        return payload.statuses.get(
            entity_key_text(value), ItemListStatus(status=RowStatus.UNKNOWN, summary=""))

    sources_for = VIEWER_TAB_SOURCES[TabKey(payload.label)]
    current = tab.current_object()
    page_is_listed = current is None or tab.contains_object(current)
    if (payload.items and page_is_listed and tab.has_same_items(payload.items)
            and (restore_state is None or tab.capture_view_state() == restore_state)):
        # Keep selection, filters, frame and scroll when only providers changed.
        # An unlisted page or another scope's remembered state requires a rebuild.
        tab.refresh_providers(
            sources_for, markers_for=markers_for,
            item_status_for=status_for if payload.statuses else None,
            prepared_sources=payload.sources,
        )
        return
    tab.set_items(
        payload.items, sources_for, label_for, markers_for=markers_for,
        item_status_for=status_for, prepared_sources=payload.sources,
        entries=payload.entries, auto_load_preview=auto_load_preview,
        restore_state=restore_state,
    )
