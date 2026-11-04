"""Version choice and visible fallback; window requests own placed selections."""
from __future__ import annotations
from dataclasses import replace
from typing import TYPE_CHECKING
from collections.abc import Callable
from PySide6.QtCore import Qt, QSignalBlocker
from tomography_session_browser.presentation.tomogram_state import TomogramViewState
from tomography_session_browser.presentation.tomogram_versions import review_versions, share_geometry, SharedGeometry
from tomography_session_browser.ui.tomograms.commands import version_tooltip

if TYPE_CHECKING:
    from tomography_session_browser.ui.tomograms.tab import TomogramTab


class VersionSelection:
    def __init__(self, tab: TomogramTab) -> None:
        self.tab = tab
        self.request_version: Callable[[str, TomogramViewState], None] | None = None

    def select_key(self, key: str, *, restore: TomogramViewState | None = None) -> None:
        tab = self.tab
        playing = tab.playback.timer.isActive()
        tab.playback.stop()
        row = tab.review_list.rows.get(key)
        if row is None:
            return
        if restore is None:
            tab.remember_view()
        preferred = restore.volume_id if restore else tab.last_versions.get(key)
        requested = tab.payload.selection.version if tab.payload.selection else None
        choices = review_versions(row.tomogram)
        volume = next((value for value in choices if value.version == requested), None) if requested else None
        volume = volume or next((value for value in choices if value.id == preferred), next(iter(choices), None))
        state = restore or tab.view_memory.get((key, volume.id if volume else None), TomogramViewState())
        if restore is not None and (volume is None or restore.volume_id != volume.id):
            state = TomogramViewState(query=state.query, segment=state.segment, sort=state.sort)
        if restore is None and key != tab.state.row_key and (state.lowpass or state.highpass):
            state = replace(state, lowpass=0., highpass=0., black=None, white=None)
        state = tab.recipes.source_entry(state, restore is not None)
        state = replace(state, row_key=key, volume_id=volume.id if volume else None,
                        sort=tab.review_list.sort.currentText())
        tab.stage_transition.prepare(row.tomogram, volume, state, allow=not playing)
        tab.state = state
        tab.review_list.select(key)
        with QSignalBlocker(tab.versions):
            tab.versions.clear()
            for value in choices:
                tab.versions.addItem(value.version, value.id)
                tab.versions.setItemData(tab.versions.count() - 1, f"{value.path}\n{version_tooltip()}", Qt.ItemDataRole.ToolTipRole)
                if volume is not None and value.id == volume.id:
                    tab.versions.setCurrentIndex(tab.versions.count() - 1)
            if requested and not any(value.version == requested for value in choices):
                tab.versions.addItem(requested, None)
                index = tab.versions.count() - 1
                tab.versions.setItemData(index, f'{requested} not available for this tomogram', Qt.ItemDataRole.ToolTipRole)
                tab.versions.model().item(index).setEnabled(False)
        tab.versions.rebuild()
        tab.show_volume()
        tab.item_selected.emit(row.tomogram)

    def activate_displayed_version(self, index: int) -> None:
        tab = self.tab
        volume = tab.volume()
        requested = tab.payload.selection.version if tab.payload.selection else None
        if volume is not None and volume.id == tab.versions.itemData(index) and requested and requested != volume.version:
            self.version_changed(index)

    def version_changed(self, index: int) -> None:
        tab = self.tab
        playing = tab.playback.timer.isActive()
        tab.playback.stop()
        row = tab.current_object()
        if row is None:
            return
        tab.remember_view()
        previous = tab.volume()
        volume_id = tab.versions.itemData(index)
        volume = next((v for v in row.volumes if v.id == volume_id), None)
        if volume is None:
            return
        state = tab.view_memory.get((row.id, volume_id), TomogramViewState(row.id, volume_id))
        geometry = share_geometry(row, previous, volume, tab.state, state)
        reason = '' if isinstance(geometry, SharedGeometry) else geometry.reason
        if isinstance(geometry, SharedGeometry):
            state = geometry.state
        state = replace(tab.recipes.version_entry(state, volume), sort=tab.review_list.sort.currentText())
        tab.stage_transition.prepare(row, volume, state, allow=not playing)
        if self.request_version is not None and not tab.payload.processing_only:
            self.request_version(volume.version, state)
            return
        tab.state = state
        tab.show_volume()
        if reason:
            tab.warning.setText(tab.warning.text() + " Independent view: " + reason)
            tab.warning.show()

    def show_notice(self) -> None:
        tab = self.tab
        volume = tab.volume()
        requested = tab.payload.selection.version if tab.payload.selection else None
        notice = tab.payload.notice
        if requested and (volume is None or volume.version != requested):
            shown = volume.version if volume else 'no full reconstruction'
            notice = f'{requested} not available for this tomogram · showing {shown}'
        if notice:
            tab.warning.setText(' · '.join(filter(None, (notice, tab.warning.text()))))
            tab.warning.show()

