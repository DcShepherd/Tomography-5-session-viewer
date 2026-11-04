"""Transient Focus geometry; normal docking remains owned by ShellChrome."""
from __future__ import annotations
from dataclasses import dataclass
from PySide6.QtCore import QByteArray, QEvent, QObject, QRect, QSize, Qt, QTimer
from PySide6.QtWidgets import QWidget
from tomography_session_browser.presentation.tomogram_geometry import focus_fit


@dataclass(frozen=True, slots=True)
class FocusSnapshot:
    docks: QByteArray
    geometry: QByteArray
    rect: QRect
    available: QRect
    maximised: bool
    minimum: QSize
    project: bool
    context: bool
    image: bool
    project_open: bool
    context_open: bool
    project_drawer_mode: bool
    context_drawer_mode: bool
    side_sizes: tuple[int, ...]
    list_sizes: tuple[int, ...]
    visibility: tuple[tuple[QWidget, bool], ...]


class TomogramFocus(QObject):
    def __init__(self, window, ui, parts) -> None:
        super().__init__(window)
        self.window, self.ui, self.parts = window, ui, parts
        self.saved: FocusSnapshot | None = None
        self.refit = QTimer(self)
        self.refit.setSingleShot(True)
        self.refit.timeout.connect(self.fit)
        window.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if event.type() in (QEvent.Type.ScreenChangeInternal, QEvent.Type.DevicePixelRatioChange):
            self.request_refit()
        return super().eventFilter(watched, event)

    def request_refit(self) -> None:
        if self.saved is not None and self.parts.chrome.transient_focus:
            self.refit.start(0)

    def panel_invoked(self, checked: bool, panel: str) -> None:
        if self.saved is not None:
            self.ui.tomogram_tab.focus_button.setChecked(False)
            action = self.parts.chrome.actions.project if panel == "project" else self.parts.chrome.actions.context
            action.setChecked(checked)

    def prepare(self, enabled: bool) -> None:
        """Save normal shell geometry before grabbing can flush pending layouts."""
        chrome, tab = self.parts.chrome, self.ui.tomogram_tab
        if enabled and self.saved is None:
            widgets = (self.ui.main_toolbar, self.ui.context_header, self.ui.tabs.tabBar(), self.window.statusBar())
            self.saved = FocusSnapshot(self.window.saveState(), self.window.saveGeometry(), self.window.geometry(),
                self.window.screen().availableGeometry(),
                self.window.isMaximized(), self.window.minimumSize(), chrome.actions.project.isChecked(), chrome.actions.context.isChecked(),
                tab.image_button.isChecked(), chrome.project_drawer.is_open, chrome.drawer.is_open,
                chrome.project_drawer_mode, chrome.drawer_mode,
                tuple(self.ui.side_column.sizes()), tuple(tab.splitter.sizes()),
                tuple((widget, not widget.isHidden()) for widget in widgets))

    def set_enabled(self, enabled: bool) -> None:
        chrome, tab = self.parts.chrome, self.ui.tomogram_tab
        if enabled and not chrome.transient_focus:
            self.prepare(True)
            assert self.saved is not None
            chrome.transient_focus = True
            # Focus hides the surrounding chrome; allow its documented narrow
            # layout without changing the normal window's minimum afterwards.
            self.window.setMinimumSize(min(640, self.saved.minimum.width()), self.saved.minimum.height())
            for widget, _visible in self.saved.visibility:
                widget.hide()
            chrome.show_project_action_checked(False)
            chrome.show_context_action_checked(False)
            chrome.project_drawer.close_drawer(animate=False)
            chrome.project_dock.hide()
            chrome.context_shell.context.hide()
            chrome.update_context_drawer_mode()
            if chrome.image_panel is None:
                chrome.drawer.close_drawer(animate=False)
                chrome.context_dock.hide()
            self.window.showNormal()
            self.fit()
        elif not enabled and self.saved is not None:
            saved = self.saved
            self.saved = None
            self.refit.stop()
            # Restoration uses normal layout policy. Project/Context checks
            # below use the signal-blocked chrome setters; only a user toggle
            # enters the preference-writing paths.
            chrome.transient_focus = False
            self.window.setMinimumSize(saved.minimum)
            tab.image_button.setChecked(saved.image)
            for widget, visible in saved.visibility:
                widget.setVisible(visible)
            self.window.restoreGeometry(saved.geometry)
            if saved.maximised:
                self.window.showMaximized()
            elif self.window.screen().availableGeometry() != saved.available:
                self.clamp_geometry(QRect(saved.rect))
            else:
                self.window.setGeometry(saved.rect)
            # Dock widths belong to the restored client geometry, not the
            # smaller fitted Focus window (notably after native maximise).
            self.window.restoreState(saved.docks)
            chrome.update_context_drawer_mode(project_was_drawer=saved.project_drawer_mode,
                                             context_was_drawer=saved.context_drawer_mode)
            chrome.show_project_action_checked(saved.project)
            chrome.show_context_action_checked(saved.context)
            chrome.context_shell.context.setVisible(saved.context or chrome.image_panel is None)
            if chrome.project_drawer_mode:
                chrome.show_project_action_checked(saved.project_open)
                if saved.project_open:
                    chrome.project_drawer.open_drawer(animate=False)
            else:
                chrome.project_dock.setVisible(saved.project)
            if chrome.drawer_mode:
                chrome.open_context_drawer(saved.context_open)
            else:
                chrome.context_dock.setVisible(saved.context or chrome.image_panel is not None)
            layout = self.window.layout()
            if layout is not None:
                layout.activate()
            self.ui.side_column.setSizes(saved.side_sizes)
            tab.splitter.setSizes(saved.list_sizes)
            tab.stage.restore_dividers()
            tab.canvas.setFocus(Qt.FocusReason.ShortcutFocusReason)
            QTimer.singleShot(0, self.window, self.parts.loading.show_pending_load_result)

    def clamp_geometry(self, rect: QRect) -> None:
        area = self.window.screen().availableGeometry()
        geometry, frame = self.window.geometry(), self.window.frameGeometry()
        area.adjust(geometry.left()-frame.left(), geometry.top()-frame.top(),
                    geometry.right()-frame.right(), geometry.bottom()-frame.bottom())
        rect.setWidth(min(rect.width(), area.width()))
        rect.setHeight(min(rect.height(), area.height()))
        rect.moveLeft(max(area.left(), min(rect.left(), area.right()-rect.width()+1)))
        rect.moveTop(max(area.top(), min(rect.top(), area.bottom()-rect.height()+1)))
        self.window.setGeometry(rect)

    def fit(self) -> None:
        saved = self.saved
        tab = self.ui.tomogram_tab
        volume = tab.volume()
        if saved is None or volume is None or volume.grid is None:
            return
        self.parts.chrome.update_context_drawer_mode()
        layout = self.window.layout()
        if layout is not None:
            layout.activate()
        area = self.window.screen().availableGeometry()
        frame = self.window.frameGeometry().size() - self.window.size()
        available = (max(1, area.width()-frame.width()), max(1, area.height()-frame.height()))
        list_width = tab.list_column().width() if tab.list_column().isVisible() else 0
        chrome = (max(0, self.window.width()-tab.stage.width()-list_width),
                  max(0, self.window.height()-tab.stage.height()))
        fitted = focus_fit(volume, tab.state, available=available, chrome=chrome,
                           handle=tab.stage.vertical.handleWidth())
        rect = QRect(0, 0, *fitted.client)
        rect.moveCenter(saved.rect.center() if not saved.maximised else area.center())
        self.clamp_geometry(rect)
        tab.refresh_adaptive()
        if fitted.show_list:
            tab.splitter.setSizes((max(1, tab.width()-307), 300))
        tab.stage.apply_dividers(fitted.dividers)
        tab.request_render()
