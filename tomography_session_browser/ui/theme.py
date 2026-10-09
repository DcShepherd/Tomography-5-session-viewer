"""Application theming: installing a palette, and keeping widgets current.

The app chrome follows the Slate & Sage style guide supplied with the
project. The palettes and tokens are ``presentation/design_tokens.py``; the
style sheet built from them is ``ui/stylesheet.py``. ``apply_theme`` installs
it and updates the icon-loader's default colour so freshly-built actions
match the theme.
"""

from __future__ import annotations

from typing import Final

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QWidget
import shiboken6

from tomography_session_browser.presentation.design_tokens import DARK_PALETTE, SANS_FONT_NAME, TYPE_BODY, ThemePalette
from tomography_session_browser.ui.stylesheet import build_stylesheet


_active_palette: ThemePalette = DARK_PALETTE

# The three visual tiers from V1, as dynamic-property values. Import these
# rather than spelling the strings at call sites, so a widget cannot silently
# claim a tier that has no rule behind it.
TIER_LANDMARK = "landmark"
TIER_PRIMARY = "primary"
TIER_SUPPORTING = "supporting"
VISUAL_TIERS: tuple[str, ...] = (TIER_LANDMARK, TIER_PRIMARY, TIER_SUPPORTING)


def apply_tier(widget: QWidget, tier: str) -> QWidget:
    """Classify ``widget`` into one of the three visual tiers.

    Returns the widget so it can be used inline where one is being built.
    Setting the property after the stylesheet is installed needs an explicit
    repolish, which is done here so callers cannot forget it.
    """

    if tier not in VISUAL_TIERS:
        raise ValueError(f"Unknown visual tier: {tier!r}")
    widget.setProperty("tier", tier)
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)
    return widget


def current_palette() -> ThemePalette:
    """Return the palette that ``apply_theme`` last applied.

    Custom-painted widgets (donut chart, sparklines, timeline strip) read
    this when they need a colour the QSS can't reach (e.g. centre-text on a
    paint event). Defaults to ``DARK_PALETTE`` until a theme is applied.
    """

    return _active_palette


_DEFERRED_POLISH_PROPERTY: Final[str] = "_tomo_polish_deferred"
_STALE_REGION_PROPERTY: Final[str] = "_tomo_theme_region_stale"


class _ThemeRegions(QObject):
    """Parts of the window restyled when shown, not at every theme switch.

    Restyling the application re-polishes every polished widget, visible or
    not: about 270 ms on a loaded session, two thirds of it for widgets on
    tab pages nobody is looking at. Qt's application-wide re-polish passes
    over widgets not marked polished, so just before a switch the widgets of
    hidden regions lose that mark; each is polished once with the new style
    when its region is next shown, before it paints. Themes differ only in
    colour, so nothing moves when a region catches up.

    The widgets are also flagged as having a style of their own for the
    switch only, which keeps them out of its style-change round too. Both
    flags are Qt's own and are given back at once (after the switch, or when
    shown): this relies on how ``QApplication::setStyle`` picks widgets, so
    re-check it on Qt upgrades (``tests/test_theme_regions.py`` does, in a
    fresh process; if Qt changes, a switch is only slower, or that test sees
    a stale colour).

    (Giving a region a style sheet of its own does *not* work: Qt then
    re-applies that sheet to each of its widgets at every switch, by the slow
    path, and a switch took 1.8 s instead of 0.3 s.)
    """

    def __init__(self) -> None:
        super().__init__()
        self._regions: list[QWidget] = []
        self._borrowed_set_style: list[QWidget] = []

    def add(self, widget: QWidget) -> None:
        widget.installEventFilter(self)
        self._regions.append(widget)

    def before_switch(self) -> None:
        self._regions = [region for region in self._regions if shiboken6.isValid(region)]
        for region in self._regions:
            if region.isVisible() or region.property(_STALE_REGION_PROPERTY):
                continue
            for widget in (region, *region.findChildren(QWidget)):
                if widget.testAttribute(Qt.WidgetAttribute.WA_WState_Polished):
                    widget.setAttribute(Qt.WidgetAttribute.WA_WState_Polished, False)
                    widget.setProperty(_DEFERRED_POLISH_PROPERTY, True)
                # Also out of the switch's style-change round, which makes
                # each widget recompute its sizes against the new sheet.
                if not widget.testAttribute(Qt.WidgetAttribute.WA_SetStyle):
                    widget.setAttribute(Qt.WidgetAttribute.WA_SetStyle, True)
                    self._borrowed_set_style.append(widget)
            region.setProperty(_STALE_REGION_PROPERTY, True)

    def after_switch(self) -> None:
        for widget in self._borrowed_set_style:
            if shiboken6.isValid(widget):
                widget.setAttribute(Qt.WidgetAttribute.WA_SetStyle, False)
        self._borrowed_set_style = []

    def stale(self, widget: QWidget) -> bool:
        return bool(widget.property(_STALE_REGION_PROPERTY))

    def refresh(self, region: QWidget) -> None:
        """Polish what the switch passed over in ``region``, with the current style."""

        if not self.stale(region):
            return
        region.setProperty(_STALE_REGION_PROPERTY, None)
        deferred = [
            widget
            for widget in (region, *region.findChildren(QWidget))
            if widget.property(_DEFERRED_POLISH_PROPERTY)
        ]
        for widget in deferred:
            widget.setProperty(_DEFERRED_POLISH_PROPERTY, None)
            widget.style().polish(widget)
            widget.setAttribute(Qt.WidgetAttribute.WA_WState_Polished, True)
        change = QEvent(QEvent.Type.StyleChange)
        for widget in deferred:
            QApplication.sendEvent(widget, change)
            widget.update()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API
        if event.type() == QEvent.Type.Show and isinstance(watched, QWidget) and self.stale(watched):
            self.refresh(watched)
        return False


_THEME_REGIONS = _ThemeRegions()


def mark_theme_region(widget: QWidget) -> None:
    """Restyle ``widget`` (and everything in it) only while or when it shows.

    For large parts of the window that are often hidden, such as tab pages.
    """

    _THEME_REGIONS.add(widget)


def apply_theme(app: QApplication, palette: ThemePalette | None = None) -> ThemePalette:
    """Install the stylesheet for ``palette`` on ``app`` and update icon colour.

    Returns the palette that was applied so callers can mirror their internal
    state without re-resolving the name.
    """

    global _active_palette  # noqa: PLW0603 — module-level state mirrors Qt
    chosen = palette or DARK_PALETTE
    _active_palette = chosen
    app.setFont(QFont(SANS_FONT_NAME, TYPE_BODY))
    sheet = build_stylesheet(chosen)
    current = app.styleSheet()
    if current != sheet:
        # Hidden theme regions are left out, and caught up when shown.
        _THEME_REGIONS.before_switch()
        try:
            if current:
                # Replacing one application style sheet with another
                # re-polishes every widget once per ancestor as well (Qt's
                # style-sheet style recurses into the children of every
                # widget it already lists): 1.2–1.5 s on a loaded session
                # (plan F5.8a). Clearing it first takes the path that polishes
                # each widget once, ~0.35 s for both steps. Nothing paints in
                # between.
                app.setStyleSheet("")
            app.setStyleSheet(sheet)
        finally:
            _THEME_REGIONS.after_switch()
    # Late import keeps ``theme`` free of UI-only dependencies at import time.
    from tomography_session_browser.ui.icons import set_default_icon_color

    set_default_icon_color(chosen.icon)
    return chosen
