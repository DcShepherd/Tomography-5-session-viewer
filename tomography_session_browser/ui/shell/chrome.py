"""Responsive toolbar, tab counts, dock widths and transient context drawer.

Owns layout state only. Scope/count queries and loading/viewport callbacks are
injected; this component neither derives scientific links nor rebuilds viewers.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from PySide6.QtCore import QRect, QSize, Qt, QSignalBlocker
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import QDockWidget, QMainWindow, QTabWidget, QToolBar, QToolButton, QWidget
import shiboken6
from tomography_session_browser.presentation.context_stack import ENTITY_DISPLAY_LABELS
from tomography_session_browser.services.settings_service import Settings
from tomography_session_browser.ui.branding import TitleBarLockup
from tomography_session_browser.ui.fit_first import (
    TAB_STEP_ACTIVE,
    TAB_STEP_COUNT,
    TAB_STEP_FULL,
    TAB_STEP_ICON,
    TAB_STEP_NAMES,
    TAB_STEP_SHORT,
    TAB_STEPS,
    clamped_share,
    first_fitting_step,
    project_panel_as_drawer,
    tab_accessible_name,
    tab_text,
)
from tomography_session_browser.ui.motion import PanelSlide, motion_enabled
from tomography_session_browser.ui.theme import current_palette
from tomography_session_browser.ui.widgets.panel_drawer import (
    CONTEXT_DRAWER_MIN_HEIGHT_PX,
    CONTEXT_DRAWER_MIN_PAGE_PX,
    CONTEXT_DRAWER_MIN_WIDTH_PX,
    PanelDrawer,
    context_panel_as_drawer,
)
from tomography_session_browser.ui.widgets.context_header import ContextHeader
from tomography_session_browser.presentation.tab_keys import TabKey, available_tab_keys

# Proportional panels keep the context metadata's 300 px floor. User drags
# override these transient defaults without changing the saved preferences.
CONTEXT_DOCK_MIN_WIDTH_PX = 300
PROJECT_DOCK_WIDTH = (220, 0.15, 360)
CONTEXT_DOCK_WIDTH = (CONTEXT_DOCK_MIN_WIDTH_PX, 0.19, 460)
DOCK_DRAG_TOLERANCE_PX = 4
# A pair of wide docks can leave almost no workspace; never report the window
# width as a fallback in that case.
WORKSPACE_MIN_PX = 1
# QSS padding, overflow room and workspace margins beyond the measured items.
TOOLBAR_CHROME_PX = 40
TAB_BAR_CHROME_PX = 24
# Internal keys stay fixed; ENTITY_DISPLAY_LABELS supplies readable names.
TAB_LABELS: list[TabKey] = list(TabKey)

@dataclass(frozen=True, slots=True)
class ChromeActions:
    open: QAction
    refresh: QAction
    report: QAction
    back: QAction
    forward: QAction
    project: QAction
    context: QAction
    theme: QAction
    stepped: list[QAction]


class ShellChrome:
    def __init__(
        self, window: QMainWindow, *, project_dock: QDockWidget,
        context_dock: QDockWidget, drawer: PanelDrawer, context_shell: QWidget,
        project_drawer: PanelDrawer, project_shell: QWidget,
        toolbar: QToolBar, brand_label: TitleBarLockup, toolbar_spacer: QWidget,
        actions: ChromeActions, tabs: QTabWidget, context_header: ContextHeader,
        settings: Settings, save_settings: Callable[[Settings], None],
        loading_active: Callable[[], bool], ride_along: Callable[[], None],
        counts_for_scope: Callable[[], Mapping[str, int]],
        list_column_rect: Callable[[], QRect | None],
    ) -> None:
        self.window = window
        self.project_dock, self.context_dock = project_dock, context_dock
        self.drawer, self.context_shell = drawer, context_shell
        self.project_drawer, self.project_shell = project_drawer, project_shell
        self.toolbar, self.brand_label, self.toolbar_spacer = toolbar, brand_label, toolbar_spacer
        self.actions, self.tabs, self.context_header = actions, tabs, context_header
        self.settings, self.save_settings = settings, save_settings
        self.loading_active, self.ride_along = loading_active, ride_along
        self.counts_for_scope = counts_for_scope
        self.list_column_rect = list_column_rect
        self.dock_targets: dict[QDockWidget, int] = {}
        self.user_sized_docks: set[QDockWidget] = set()
        self.fitting_docks = False
        self.dock_fit_window_size = QSize()
        self.toolbar_step = 0
        self.toolbar_widths_cache: tuple[tuple, list[int]] | None = None
        self.brand_compact = False
        self.tab_step = TAB_STEP_FULL
        self.tab_count_values: dict[str, int] = {}
        #: The texts and fonts the widths were measured for, and each text variant's tab widths.
        self.tab_widths_cache: tuple[tuple, dict[int, list[int]]] | None = None
        #: What the tabs show now: (step, the current tab when that matters).
        self.tab_applied: tuple[int, int | None] | None = None
        self.panel_slides: dict[QDockWidget, PanelSlide] = {}
        self.drawer_mode = False
        self.project_drawer_mode = False

    def dock_width_specs(self) -> list[tuple[QDockWidget, tuple[int, float, int]]]:
        return [(self.project_dock, PROJECT_DOCK_WIDTH), (self.context_dock, CONTEXT_DOCK_WIDTH)]

    def fit_docks(self) -> None:
        """Size each side panel as a share of the window, within its clamp.

        Runs on window resizes only. Qt otherwise keeps a dock's width fixed
        and hands every spare pixel to the centre, which left the panels as
        slivers at 4K. A dock the reviewer has dragged keeps their width.
        """

        if self.fitting_docks:
            return
        docks, widths = [], []
        for dock, (minimum, share, maximum) in self.dock_width_specs():
            # ``isHidden`` rather than ``isVisible``: an unshown window's docks
            # are not "visible" yet, but they still need a starting width.
            if dock in self.user_sized_docks or dock.isHidden() or dock.isFloating():
                continue
            target = clamped_share(minimum, share, maximum, self.window.width())
            self.dock_targets[dock] = target
            docks.append(dock)
            widths.append(target)
        self.dock_fit_window_size = self.window.size()
        if not docks:
            return
        self.fitting_docks = True
        try:
            self.window.resizeDocks(docks, widths, Qt.Orientation.Horizontal)
        finally:
            self.fitting_docks = False

    def update_context_drawer_mode(self) -> None:
        """Dock the context panel, or fold it into a drawer, to suit the width.

        Judged from the window alone (the page's width as if the panel were
        docked), so switching cannot feed back. Only once the window is shown:
        showing a dock before that can flash it as a tiny top-level window.
        """

        if not self.window.isVisible():
            return
        self.update_project_drawer_mode()
        if self.project_dock.isHidden() or self.project_dock.isFloating():
            project = 0
        elif self.project_dock in self.user_sized_docks:
            project = self.project_dock.width()
        else:
            project = clamped_share(*PROJECT_DOCK_WIDTH, self.window.width())
        context = clamped_share(*CONTEXT_DOCK_WIDTH, self.window.width())
        drawer = self.project_drawer_mode or context_panel_as_drawer(
            self.window.width(), project, context, currently_drawer=self.drawer_mode
        )
        if drawer and not self.drawer_mode:
            self.enter_context_drawer()
        elif not drawer and self.drawer_mode:
            self.leave_context_drawer()
        elif drawer:
            self.place_context_drawer()

    def enter_context_drawer(self) -> None:
        self.drawer_mode = True
        slide = self.panel_slides.pop(self.context_dock, None)
        if slide is not None:
            try:
                slide.dismiss()
            except RuntimeError:
                pass
        self.context_dock.hide()
        self.drawer.hold(self.context_shell)
        self.place_context_drawer()
        # The button now says whether the drawer is open: closed to start.
        self.show_context_action_checked(False)

    def leave_context_drawer(self) -> None:
        self.drawer_mode = False
        self.drawer.close_drawer(animate=False)
        self.drawer.release()
        self.context_dock.setWidget(self.context_shell)
        # Back to the reviewer's docked preference, which the drawer never touched.
        visible = self.settings.context_panel_visible
        self.show_context_action_checked(visible)
        self.context_dock.setVisible(visible)
        self.fit_docks()

    def place_context_drawer(self) -> None:
        """Over the list column, when the shown page has one.

        The drawer replaces the list in place, so the tabs, the title and the
        image stay in view (review decision D22). A page with no list column
        (the Session summary), or one too short to hold the panel, gets the
        page's full height at the panel's usual share of the window.
        """

        central = self.window.centralWidget().geometry()
        room = max(0, self.window.width() - self.project_dock_room() - CONTEXT_DRAWER_MIN_PAGE_PX)
        column = self.list_column_rect()
        if column is not None and column.height() >= CONTEXT_DRAWER_MIN_HEIGHT_PX:
            width = min(max(column.width(), CONTEXT_DRAWER_MIN_WIDTH_PX), room)
            self.drawer.place(QRect(column.right() + 1 - width, column.top(), width, column.height()))
            return
        width = min(clamped_share(*CONTEXT_DOCK_WIDTH, self.window.width()), room)
        self.drawer.place(QRect(self.window.width() - width, central.top(), width, central.height()))

    def follow_list_column(self) -> None:
        """The list column moved or changed size (a longer reason line above it, a
        collapse): an open drawer goes with it."""

        if self.drawer_mode and self.drawer.is_open:
            self.place_context_drawer()

    def project_dock_room(self) -> int:
        dock = self.project_dock
        return 0 if dock.isHidden() or dock.isFloating() else dock.width()

    def open_context_drawer(self, open_: bool) -> None:
        animate = motion_enabled(self.window) and not self.loading_active()
        if open_:
            self.close_project_drawer()
            self.place_context_drawer()
            self.drawer.open_drawer(animate=animate)
        else:
            self.drawer.close_drawer(animate=animate)

    def close_context_drawer_from_keyboard(self) -> None:
        self.show_context_action_checked(False)
        self.open_context_drawer(False)

    def update_project_drawer_mode(self) -> None:
        drawer = project_panel_as_drawer(self.window.width(), currently_drawer=self.project_drawer_mode)
        if drawer != self.project_drawer_mode:
            self.project_drawer_mode = drawer
            if drawer:
                slide = self.panel_slides.pop(self.project_dock, None)
                if slide is not None and shiboken6.isValid(slide):
                    slide.dismiss()
                self.project_dock.hide()
                self.project_drawer.hold(self.project_shell)
                self.show_project_action_checked(False)
            else:
                self.project_drawer.close_drawer(animate=False)
                self.project_drawer.release()
                self.project_dock.setWidget(self.project_shell)
                visible = self.settings.project_panel_visible
                self.show_project_action_checked(visible)
                self.project_dock.setVisible(visible)
                self.fit_docks()
        if drawer:
            central = self.window.centralWidget().geometry()
            width = min(PROJECT_DOCK_WIDTH[2], central.width() - CONTEXT_DRAWER_MIN_PAGE_PX)
            self.project_drawer.place(QRect(central.left(), central.top(), width, central.height()))

    def close_project_drawer(self) -> None:
        """A tree choice, Escape or opening Context dismisses the transient panel."""
        if self.project_drawer_mode:
            self.show_project_action_checked(False)
            self.project_drawer.close_drawer(animate=False)

    def show_project_action_checked(self, checked: bool) -> None:
        blocked = self.actions.project.blockSignals(True)
        try:
            self.actions.project.setChecked(checked)
        finally:
            self.actions.project.blockSignals(blocked)

    def show_context_action_checked(self, checked: bool) -> None:
        blocked = self.actions.context.blockSignals(True)
        try:
            self.actions.context.setChecked(checked)
        finally:
            self.actions.context.blockSignals(blocked)

    def note_user_dock_widths(self, buttons: Qt.MouseButton) -> None:
        """Keep a width the reviewer dragged instead of refitting over it.

        A dock whose width moved away from its fitted width while the window
        kept its size, with a mouse button held, was dragged by its separator.
        Qt also squeezes docks during a window resize and a panel toggle moves
        the centre, but neither holds the window size *and* the button. A
        session-long choice, like any other layout observation, never saved.
        """

        if self.fitting_docks or not (buttons & Qt.MouseButton.LeftButton):
            return
        if self.window.size() != self.dock_fit_window_size:
            return
        for dock, target in self.dock_targets.items():
            if dock.isVisible() and not dock.isFloating() and abs(dock.width() - target) > DOCK_DRAG_TOLERANCE_PX:
                self.user_sized_docks.add(dock)

    def workspace_width(self) -> int:
        """Width of the central workspace, not of the whole window.

        The docks are user-resizable, so a wide window with a wide project tree
        and a wide metadata panel can leave the reviewing area as cramped as a
        small window. Chrome that serves the workspace should follow the
        workspace.
        """

        # Derived by subtracting the visible docks rather than reading the
        # central widget directly: before the window is laid out the central
        # widget's width is meaningless, and trusting it made an unshown window
        # report itself as cramped.
        width = self.window.width()
        for dock in (self.project_dock, self.context_dock):
            # A floating dock is visible but no longer takes space out of the
            # centre, so subtracting it would under-report the workspace.
            if dock is not None and dock.isVisible() and not dock.isFloating():
                width -= dock.width()
        # Clamp rather than falling back to the window width: the fallback
        # inverted the answer exactly when the workspace was most cramped.
        return max(width, WORKSPACE_MIN_PX)

    def refresh_branding(self) -> None:
        palette = current_palette()
        self.brand_label.set_theme(palette.name)
        # Whether the lockup is compact is one of the toolbar's fitted steps
        # (``TitleBarLockup.set_compact`` is that widget's own vocabulary),
        # so refreshing the brand refits the toolbar. Never a preference.
        self.fit_toolbar()
        self.brand_label.set_compact(self.brand_compact)

    def update_responsive_chrome(self) -> None:
        """Fit each piece of chrome to the room it has (plan F3, D1).

        Transient, recomputed on every resize and dock change, and never a
        saved preference: ``Settings.compact`` is a separate, persisted toggle.
        The brand refresh fits the toolbar.
        """

        self.refresh_branding()
        self.context_header.fit_to_width(self.workspace_width())
        self.fit_tabs()

    def toolbar_steps(self) -> list[tuple[frozenset[QAction], bool]]:
        """Toolbar presentations, richest first: (icon-only actions, compact brand).

        Back and Forward give up their labels first and Open and Import keep
        theirs longest (plan D1); the brand's tagline goes early because it
        names the app rather than doing anything.
        """

        history = {self.actions.back, self.actions.forward}
        secondary = {self.actions.refresh, self.actions.report}
        view = {self.actions.project, self.actions.context, self.actions.theme}
        primary = {self.actions.open}
        return [
            (frozenset(), False),
            (frozenset(history), False),
            (frozenset(history), True),
            (frozenset(history | secondary), True),
            (frozenset(history | secondary | view), True),
            (frozenset(history | secondary | view | primary), True),
        ]

    def apply_toolbar_step(self, icon_only: frozenset[QAction], brand_compact: bool) -> None:
        toolbar = self.toolbar
        for action in self.actions.stepped:
            button = toolbar.widgetForAction(action)
            if isinstance(button, QToolButton):
                button.setToolButtonStyle(
                    Qt.ToolButtonStyle.ToolButtonIconOnly
                    if action in icon_only
                    else Qt.ToolButtonStyle.ToolButtonTextBesideIcon
                )
        self.brand_compact = brand_compact
        self.brand_label.set_compact(brand_compact)

    def toolbar_required_width(self) -> int:
        """Width the toolbar needs to show every item without its overflow menu."""

        toolbar = self.toolbar
        widths = []
        for action in toolbar.actions():
            if not action.isVisible():
                continue
            widget = toolbar.widgetForAction(action)
            if widget is None or widget is self.toolbar_spacer:
                continue
            widget.ensurePolished()
            widths.append(max(0, widget.sizeHint().width()))
        layout = toolbar.layout()
        spacing = layout.spacing() if layout is not None else 0
        return sum(widths) + spacing * max(0, len(widths) - 1) + TOOLBAR_CHROME_PX

    def fit_toolbar(self) -> None:
        """Show as many toolbar labels as the window's width holds.

        Measuring means applying every step, and each restyles ten buttons
        and re-lays out the toolbar: six times on every resize event, as the
        tabs once were. The widths depend only on what ``_toolbar_measure_key``
        names, so they are measured once per change, and a step is applied
        only when the fitting one changes.
        """

        steps = self.toolbar_steps()
        key = self.toolbar_measure_key()
        if self.toolbar_widths_cache is None or self.toolbar_widths_cache[0] != key:
            required = []
            for icon_only, compact in steps:
                self.apply_toolbar_step(icon_only, compact)
                required.append(self.toolbar_required_width())
            self.toolbar_widths_cache = (key, required)
            self.toolbar_step = -1  # the toolbar now shows the last step measured
        step = first_fitting_step(self.toolbar_widths_cache[1], self.window.width())
        if step != self.toolbar_step:
            self.toolbar_step = step
            self.apply_toolbar_step(*steps[step])

    def toolbar_measure_key(self) -> tuple:
        """What the toolbar's widths depend on, apart from the step itself.

        The labels and their visibility, the fonts and icon size, the theme
        and pixel density (the brand lockup), and the width of every other
        item, the session pill above all, which follows the workspace.
        """

        toolbar = self.toolbar
        stepped = set(self.actions.stepped) | {self.actions.back, self.actions.forward}
        others = []
        for action in toolbar.actions():
            widget = toolbar.widgetForAction(action)
            if widget is None or widget is self.toolbar_spacer or widget is self.brand_label:
                continue
            if action in stepped:
                continue
            others.append((action.isVisible(), widget.sizeHint().width() if action.isVisible() else 0))
        labels = tuple((action.text(), action.isVisible()) for action in toolbar.actions() if action in stepped)
        return (
            labels,
            tuple(others),
            toolbar.font().toString(),
            toolbar.iconSize().toTuple(),
            current_palette().name,
            self.window.devicePixelRatioF(),
        )

    def fit_tabs(self) -> None:
        """Drop tab text in stages before relying on the tab bar's scroll arrows.

        Qt's fallback is a pair of small arrows that hide whole entity types
        behind a scroll gesture. Names shorten, then lose counts before all
        but the current tab's name go. Counts and then icons are fallbacks. Every tab stays
        reachable, its full text is in the tooltip and its accessible name.
        """

        bar = self.tabs.tabBar()
        bar.setExpanding(False)
        # Measuring means setting every variant's texts, and each ``setTabText``
        # re-lays out the bar: 28 of them per call, on every resize event,
        # were most of a window drag's cost. The widths depend only on the
        # texts, font and icon size, so they are measured once per change; the
        # current tab's step is worked out from them, so choosing another tab
        # measures nothing.
        variants = (TAB_STEP_FULL, TAB_STEP_SHORT, TAB_STEP_NAMES, TAB_STEP_COUNT, TAB_STEP_ICON)
        key = (tuple(self.tab_texts(step) for step in variants), bar.font().toString(), bar.iconSize().toTuple())
        if self.tab_widths_cache is None or self.tab_widths_cache[0] != key:
            widths = {}
            for step in variants:
                self.apply_tab_texts(step)
                widths[step] = [bar.tabSizeHint(index).width() for index in range(bar.count())]
            self.tab_widths_cache = (key, widths)
            self.tab_applied = None  # the bar now shows the last variant measured
        widths = self.tab_widths_cache[1]
        current = self.tabs.currentIndex()
        required = [self.tab_required_width(step, widths, current) for step in TAB_STEPS]
        step = first_fitting_step(required, self.workspace_width() - TAB_BAR_CHROME_PX)
        if self.tab_state(step) != self.tab_applied:
            self.apply_tab_texts(step)

    def refit_tabs_for_current(self) -> None:
        """Another tab was chosen: the one being looked at keeps its name.

        As ``update_tab_counts``: only a shown window has a width worth fitting
        to, so an unshown one just shows its step again.
        """

        if self.window.isVisible():
            self.fit_tabs()
        else:
            self.apply_tab_texts(self.tab_step)

    def tab_state(self, step: int) -> tuple[int, int | None]:
        """What fixes the tabs' texts: the step, and which tab is current when the step names only it."""

        return step, self.tabs.currentIndex() if step == TAB_STEP_ACTIVE else None

    @staticmethod
    def tab_required_width(step: int, widths: dict[int, list[int]], current: int) -> int:
        if step != TAB_STEP_ACTIVE:
            return sum(widths[step])
        counts = widths[TAB_STEP_COUNT]
        if not 0 <= current < len(counts):
            return sum(counts)
        return widths[TAB_STEP_SHORT][current] + sum(counts) - counts[current]

    def update_tab_counts(self) -> None:
        counts = dict(self.counts_for_scope())
        if counts == self.tab_count_values:
            return
        # Kept, so fitting the tabs to a new width measures the texts without
        # recounting every entity in scope.
        self.tab_count_values = counts
        # New counts can change the fit, but only a shown window has a width
        # worth fitting to; showing it runs ``_update_responsive_chrome``.
        if self.window.isVisible():
            self.fit_tabs()
        else:
            self.apply_tab_texts(self.tab_step)

    def apply_tab_availability(self, counts: Mapping[str, int], destination: TabKey | None = None) -> None:
        """Apply one scope's available tabs without intermediate tab-change callbacks."""
        available = available_tab_keys(counts)
        target = destination or TAB_LABELS[self.tabs.currentIndex()]
        if target not in available:
            target = TabKey.SESSION
        with QSignalBlocker(self.tabs):
            for index, key in enumerate(TAB_LABELS):
                if key in available:
                    self.tabs.setTabEnabled(index, True)
        # Emit the one real destination change: context, theme refresh and
        # leaving a tilt-series zoom still use their ordinary tab callback.
        self.tabs.setCurrentIndex(TAB_LABELS.index(target))
        with QSignalBlocker(self.tabs):
            for index, key in enumerate(TAB_LABELS):
                self.tabs.setTabEnabled(index, key in available)

    def tab_texts(self, step: int) -> tuple[str, ...]:
        counts = self.tab_count_values
        current = self.tabs.currentIndex()
        return tuple(
            # None until counted (and always for Session): the name alone.
            tab_text(
                label,
                ENTITY_DISPLAY_LABELS.get(label, label),
                None if label == TabKey.SESSION else counts.get(label),
                step,
                active=index == current,
            )
            for index, label in enumerate(TAB_LABELS)
        )

    def apply_tab_texts(self, step: int) -> None:
        # The full text lives in the tooltip at every step, and the name a screen
        # reader says ("Atlas, 12") never changes with what the tab shows.
        bar = self.tabs.tabBar()
        counts = self.tab_count_values
        for index, (text, full, label) in enumerate(
            zip(self.tab_texts(step), self.tab_texts(TAB_STEP_FULL), TAB_LABELS, strict=True)
        ):
            # Only what changed: each ``setTabText`` re-lays out the bar, and choosing
            # another tab changes two of the seven.
            if self.tabs.tabText(index) != text:
                self.tabs.setTabText(index, text)
            if self.tabs.tabToolTip(index) != full:
                self.tabs.setTabToolTip(index, full)
            accessible = tab_accessible_name(
                ENTITY_DISPLAY_LABELS.get(label, label), None if label == TabKey.SESSION else counts.get(label)
            )
            if bar.accessibleTabName(index) != accessible:
                bar.setAccessibleTabName(index, accessible)
        self.tab_step = step
        self.tab_applied = self.tab_state(step)

    def toggle_project_panel(self) -> None:
        visible = self.actions.project.isChecked()
        if self.project_drawer_mode:
            animate = motion_enabled(self.window) and not self.loading_active()
            if visible:
                self.show_context_action_checked(False)
                self.drawer.close_drawer(animate=False)
                self.update_project_drawer_mode()
                self.project_drawer.open_drawer(animate=animate)
                self.project_shell.setFocus(Qt.FocusReason.ShortcutFocusReason)
            else:
                self.project_drawer.close_drawer(animate=animate)
            return
        self.set_side_panel_visible(self.project_dock, visible)
        self.settings.project_panel_visible = visible
        self.save_settings(self.settings)
        # The project panel's room decides whether the context panel docks.
        self.update_context_drawer_mode()

    def reveal_context_panel(self) -> None:
        """Show the Context panel if it is hidden: docked, or as the drawer."""

        if self.drawer_mode:
            self.show_context_action_checked(True)
            self.open_context_drawer(True)
        elif not self.actions.context.isChecked():
            self.actions.context.setChecked(True)
            self.toggle_context_panel()

    def toggle_context_panel(self) -> None:
        visible = self.actions.context.isChecked()
        if self.drawer_mode:
            # The drawer over the page (plan D2): transient, never saved.
            self.open_context_drawer(visible)
            return
        self.set_side_panel_visible(self.context_dock, visible)
        self.settings.context_panel_visible = visible
        self.save_settings(self.settings)

    def set_side_panel_visible(self, dock: QDockWidget, visible: bool) -> None:
        """Show or hide a side panel, sliding it from its own edge (plan F5.2).

        The dock changes at once, so the window lays out a single time: a
        snapshot of the panel slides on an overlay, and the current viewer
        eases its fitted image to the new width alongside. Animating the
        dock's real width re-laid out the whole window on every frame.
        """

        previous = self.panel_slides.pop(dock, None)
        if previous is not None and (not shiboken6.isValid(previous) or previous.isHidden()):
            previous = None  # it had already settled
        can_move = not (
            dock.isVisible() == visible
            or dock.isFloating()
            or not self.window.isVisible()
            or not motion_enabled(self.window)
            # A slide raises itself; it must never cover the loader.
            or self.loading_active()
        )
        palette = current_palette()
        from_left = dock is self.project_dock
        # Each panel's own background (``projectPanel`` / ``contextShell``).
        backdrop = QColor(palette.panel if from_left else palette.background)
        if previous is not None and can_move and previous.entering != visible:
            # A toggle mid-slide turns it round from where it is.
            dock.setVisible(visible)
            if visible:
                self.window.layout().activate()
            self.ride_along()
            previous.reverse(backdrop=backdrop)
            self.panel_slides[dock] = previous
            return
        if previous is not None:
            previous.dismiss()
        if not can_move:
            dock.setVisible(visible)
            return
        self.ride_along()
        if visible:
            dock.setVisible(True)
            # Apply the new layout now, so the snapshot is the panel as it
            # will stand.
            self.window.layout().activate()
            geometry, picture = dock.geometry(), dock.grab()
        else:
            geometry, picture = dock.geometry(), dock.grab()
            dock.setVisible(False)
        self.panel_slides[dock] = PanelSlide(
            self.window, geometry, picture, from_left=from_left, entering=visible, backdrop=backdrop
        )
