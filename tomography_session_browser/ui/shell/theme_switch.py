"""Theme reveal, idle icon preparation and deferred dashboard recolouring."""
from __future__ import annotations
from collections.abc import Callable, Mapping
import shiboken6
from PySide6.QtCore import QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QPixmap
from PySide6.QtWidgets import QApplication, QMainWindow, QToolBar
from tomography_session_browser.presentation.design_tokens import ThemePalette, palette_for
from tomography_session_browser.services.settings_service import Settings
from tomography_session_browser.ui.icons import draw_in_advance, themed_icon, undrawn_in
from tomography_session_browser.ui.motion import THEME_REVEAL_GLOW_ALPHA, ThemeRevealOverlay, ThemeSeed, motion_enabled
from tomography_session_browser.ui.native_window import set_title_bar_dark
from tomography_session_browser.ui.theme import apply_theme, current_palette
from tomography_session_browser.ui.viewer.viewer_tab import ViewerTab
from tomography_session_browser.ui.project_tree.panel import ProjectTreePanel
from tomography_session_browser.ui.widgets.metadata_panel import MetadataPanel
from tomography_session_browser.ui.dashboard.widget import SessionDashboard
from tomography_session_browser.ui.widgets.loading_overlay import LoadingOverlay

THEME_SEED_DIAMETER_PX = 28
THEME_SEED_ICON_OFFSET_PX = 19
THEME_ICONS_IDLE_DELAY_MS = 800
THEME_ICONS_PER_TURN = 12

class ThemeSwitch:
    """Own presentation state; widget callbacks supply no scientific derivation."""

    def __init__(
        self, window: QMainWindow, *, toolbar: QToolBar, theme_action: QAction,
        settings: Settings, save_settings: Callable[[Settings], None],
        loading_active: Callable[[], bool], refresh_action_icons: Callable[[], None],
        refresh_branding: Callable[[], None], viewer_tabs: Mapping[str, ViewerTab],
        context_panel: MetadataPanel, project_tree_panel: ProjectTreePanel,
        session_dashboard: SessionDashboard, loading_overlay: LoadingOverlay,
    ) -> None:
        self.window = window
        self.toolbar, self.theme_action = toolbar, theme_action
        self.settings, self.save_settings = settings, save_settings
        self.loading_active = loading_active
        self.refresh_action_icons, self.refresh_branding = refresh_action_icons, refresh_branding
        self.viewer_tabs, self.context_panel = viewer_tabs, context_panel
        self.project_tree_panel, self.session_dashboard = project_tree_panel, session_dashboard
        self.loading_overlay = loading_overlay
        self.reveal: ThemeRevealOverlay | None = None
        self.icons_pending = False
        self.dashboard_stale = False

    @property
    def active_reveal(self) -> ThemeRevealOverlay | None:
        reveal = self.reveal
        return reveal if reveal is not None and shiboken6.isValid(reveal) else None

    def on_theme_toggled(self, checked: bool) -> None:
        new_theme = "light" if checked else "dark"
        palette = palette_for(new_theme)
        # A toggle mid-reveal: that theme is already on; finish showing it.
        self.finish_theme_reveal()
        # Acknowledge the click on screen before the restyle blocks the event
        # loop: the reveal shows its seed at once (plan F5.8b); without motion,
        # the seed alone (F5.8a).
        reveal = self.start_theme_reveal(palette)
        seed = None if reveal is not None else self.show_theme_seed(palette)
        self.settings.theme = new_theme
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, palette)
        self.refresh_action_icons()
        self.refresh_theme_dependent_widgets()
        self.save_settings(self.settings)
        if reveal is not None:
            # The title bar cannot take part; it changes as the reveal lands.
            reveal.after_switch(self.window.grab)
            return
        set_title_bar_dark(self.window, palette.name == "dark")
        if seed is not None:
            # The toolbar now matches it; let the new theme paint first.
            QTimer.singleShot(0, seed.dismiss)

    def theme_seed_place(self, palette: ThemePalette) -> tuple[QPoint, QPixmap] | None:
        """Where the seed goes (window coordinates) and its icon in ``palette``."""

        toolbar = self.toolbar
        button = toolbar.widgetForAction(self.theme_action) if toolbar is not None else None
        if button is None or not self.window.isVisible() or not button.isVisible():
            return None
        if button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly:
            local = button.rect().center()
        else:
            local = QPoint(THEME_SEED_ICON_OFFSET_PX, button.height() // 2)
        name = "sun" if self.theme_action.isChecked() else "moon"
        size = toolbar.iconSize()
        icon = themed_icon(name, color=palette.icon, size=size.width()).pixmap(size)
        return button.mapTo(self.window, local), icon

    def show_theme_seed(self, palette: ThemePalette) -> ThemeSeed | None:
        """Paint the theme button's icon in ``palette`` over the button, now."""

        place = self.theme_seed_place(palette)
        if place is None:
            return None
        centre, icon = place
        return ThemeSeed(
            self.window, centre, THEME_SEED_DIAMETER_PX, QColor(palette.surface), QColor(palette.border_strong), icon
        )

    def start_theme_reveal(self, palette: ThemePalette) -> ThemeRevealOverlay | None:
        """Cover the window with its old theme and the seed of ``palette``.

        None when there is nothing to animate over: reduced motion, a hidden
        or minimised window, or a load in progress (the loader covers it).
        """

        if not motion_enabled(self.window) or not self.window.isVisible() or self.window.isMinimized() or self.loading_active():
            return None
        place = self.theme_seed_place(palette)
        if place is None:
            return None
        centre, icon = place
        glow = QColor(palette.accent)
        glow.setAlpha(THEME_REVEAL_GLOW_ALPHA)
        self.reveal = ThemeRevealOverlay(
            self.window,
            self.window.grab(),
            QPointF(centre),
            seed_diameter=THEME_SEED_DIAMETER_PX,
            seed_fill=QColor(palette.surface),
            seed_edge=QColor(palette.border_strong),
            seed_icon=icon,
            glow=glow,
            on_finished=self.theme_reveal_landed,
        )
        return self.reveal

    def theme_reveal_landed(self) -> None:
        self.reveal = None
        set_title_bar_dark(self.window, current_palette().name == "dark")

    def finish_theme_reveal(self) -> None:
        reveal = self.reveal
        if reveal is not None and shiboken6.isValid(reveal):
            reveal.finish()
        self.reveal = None

    def schedule_other_theme_icons(self) -> None:
        """Draw the other theme's icons while idle, so a switch needn't (restyle spike)."""

        if not self.icons_pending:
            self.icons_pending = True
            QTimer.singleShot(THEME_ICONS_IDLE_DELAY_MS, self.window, self.draw_other_theme_icons)

    def draw_other_theme_icons(self) -> None:
        if not self.window.isVisible() or self.loading_active() or self.loading_overlay.isVisible():
            self.icons_pending = False
            return
        current = current_palette()
        other = palette_for("light" if current.name == "dark" else "dark").icon
        undrawn = undrawn_in(other, drawn_in=current.icon)
        # A few per turn: each is five pixel densities of one SVG.
        draw_in_advance(other, undrawn[:THEME_ICONS_PER_TURN])
        if len(undrawn) > THEME_ICONS_PER_TURN:
            QTimer.singleShot(0, self.window, self.draw_other_theme_icons)
            return
        self.icons_pending = False

    def refresh_theme_dependent_widgets(self) -> None:
        """Repaint custom/icon-based widgets that Qt stylesheets do not recolour."""

        for viewer_tab in self.viewer_tabs.values():
            viewer_tab.refresh_theme()
        self.refresh_branding()
        self.context_panel.refresh_theme()
        self.project_tree_panel.refresh_theme()
        self.project_tree_panel.tree.viewport().update()
        if self.session_dashboard.isVisible():
            # Recoloured in place. It used to be rebuilt, which with the
            # clean-up of the old cards cost ~150 ms of every switch.
            self.session_dashboard.refresh_theme()
        else:
            # Hidden: recoloured when the Session tab is next shown.
            self.dashboard_stale = True
        self.loading_overlay.update()

