"""Move the existing title/selector between two slots, without another model."""
from PySide6.QtWidgets import QSizePolicy
from tomography_session_browser.ui.motion.section_reveal import settle_layouts

FOCUS_WIDE = 960


class FocusLayout:
    def __init__(self, tab):
        self.tab = tab

    def commit(self, enabled: bool) -> None:
        tab = self.tab
        tab.playback.stop()
        self.refresh()
        tab.focus_requested.emit(enabled)
        settle_layouts(tab, tab.window())
        tab.splitter.refresh()
        tab.refresh_adaptive()
        tab.canvas_layout.activate()
        frame = tab.stage_frame.layout()
        frame.activate()  # Toolbar width decides which cursor row is visible.
        # Resolve the row move before recording the destination rectangle.
        frame.invalidate()
        frame.activate()
        for splitter in (tab.stage.vertical, tab.stage.horizontal, tab.stage.top):
            splitter.refresh()
        tab.request_render()

    def refresh(self) -> None:
        tab = self.tab
        focus = tab.focus_button.isChecked()
        compact = tab.width() < (FOCUS_WIDE if focus else 760)
        destination = tab.version_row if focus else tab.title_row
        for index, widget in enumerate((tab.review_list.compact, tab.title)):
            other = tab.title_row if focus else tab.version_row
            other.removeWidget(widget)
            if destination.indexOf(widget) < 0:
                destination.insertWidget(index, widget, 1)
        tab.title_band.setVisible(not focus)
        tab.tags.column.sync(compact, destination)
        tab.review_list.compact.setVisible(compact)
        tab.review_list.compact.setMaximumWidth(460 if focus else 16777215)
        tab.title.setVisible(not compact)
        tab.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        tab.versions.setVisible(compact)
        tab.versions.segments.setVisible(not compact)
        tip = "Leave Focus (Ctrl+M or Esc)" if focus else "Focus the tomogram work area (Ctrl+M)"
        if focus and compact:
            tip += ". The list is hidden because there is not enough room; use the tomogram selector."
        tab.focus_button.setToolTip(tip)
        tab.focus_button.setAccessibleDescription(tip)
        for pane in tab.stage.panes.values():
            pane.hide_tag = focus
            pane.update()
