"""Regression tests for menu theming and selection affordance.

Covers audit items 1 (``QMenu`` inherited the app's text colour but the
platform's background, so a light app theme on a dark desktop rendered
dark-on-dark) and 6 (the selection fill alone was ~1.01:1 against the panel
in light mode, making the current row invisible).
"""

from __future__ import annotations

import re

import pytest

from tomography_session_browser.ui.theme import (
    DARK_PALETTE,
    LIGHT_PALETTE,
    build_stylesheet,
)


PALETTES = (DARK_PALETTE, LIGHT_PALETTE)


def _style_block(stylesheet: str, selector: str) -> str:
    """Return the declaration block for an exact selector."""

    # Comments carry no braces, so they would otherwise be glued onto the
    # selector that follows them.
    stripped = re.sub(r"/\*.*?\*/", "", stylesheet, flags=re.DOTALL)
    for raw in stripped.split("}"):
        if "{" not in raw:
            continue
        head, _, body = raw.rpartition("{")
        selectors = {part.strip() for part in head.split(",")}
        if selector in selectors:
            return body
    raise AssertionError(f"selector not found in stylesheet: {selector}")


# ------------------------------------------------------------------ QMenu


@pytest.mark.parametrize("palette", PALETTES, ids=lambda p: p.name)
def test_menu_paints_its_own_background_and_text(palette) -> None:
    """Both must be set together, or the platform background leaks through.

    Setting only ``color`` (which the global ``QWidget`` rule does) leaves
    the native menu background in place. On a desktop whose theme opposes the
    app's, that is dark text on a dark menu.
    """

    stylesheet = build_stylesheet(palette)
    menu = _style_block(stylesheet, "QMenu")

    assert f"background: {palette.surface};" in menu
    assert f"color: {palette.text};" in menu
    assert f"border: 1px solid {palette.border_strong};" in menu


@pytest.mark.parametrize("palette", PALETTES, ids=lambda p: p.name)
def test_menu_items_carry_an_explicit_foreground(palette) -> None:
    item = _style_block(build_stylesheet(palette), "QMenu::item")
    assert f"color: {palette.text};" in item


@pytest.mark.parametrize("palette", PALETTES, ids=lambda p: p.name)
def test_disabled_menu_items_are_visually_distinct_from_enabled_ones(palette) -> None:
    """"Revert group name" rendered identically to enabled entries."""

    stylesheet = build_stylesheet(palette)
    enabled = _style_block(stylesheet, "QMenu::item")
    disabled = _style_block(stylesheet, "QMenu::item:disabled")

    assert f"color: {palette.text_muted};" in disabled
    assert f"color: {palette.text};" in enabled
    assert palette.text_muted != palette.text


@pytest.mark.parametrize("palette", PALETTES, ids=lambda p: p.name)
def test_disabled_menu_items_do_not_take_the_hover_highlight(palette) -> None:
    block = _style_block(build_stylesheet(palette), "QMenu::item:disabled:selected")
    assert "background: transparent;" in block
    assert f"color: {palette.text_muted};" in block


@pytest.mark.parametrize("palette", PALETTES, ids=lambda p: p.name)
def test_menu_separator_uses_the_theme_border(palette) -> None:
    block = _style_block(build_stylesheet(palette), "QMenu::separator")
    assert f"background: {palette.border};" in block


@pytest.mark.parametrize("palette", PALETTES, ids=lambda p: p.name)
def test_menu_selection_stays_readable(palette) -> None:
    """Highlighted item text must clear the body-contrast floor on its fill."""

    assert _contrast_ratio(palette.selection_text, palette.selection) >= 4.5


# -------------------------------------------------------------- selection


@pytest.mark.parametrize("palette", PALETTES, ids=lambda p: p.name)
def test_selected_rows_keep_their_stylesheet_fill(palette) -> None:
    selected = _style_block(build_stylesheet(palette), "QTreeWidget::item:selected")
    assert f"background: {palette.selection};" in selected
    assert f"color: {palette.selection_text};" in selected


@pytest.mark.parametrize("palette", PALETTES, ids=lambda p: p.name)
def test_selection_marker_is_not_a_stylesheet_border(palette) -> None:
    """``::item`` rules apply per column.

    A ``border-left`` there paints one radius-curved arc per column instead
    of a single bar per row — which is exactly what it looked like. The
    marker must come from the delegates, which can see the column index.
    """

    stylesheet = build_stylesheet(palette)
    for selector in ("QTreeWidget::item", "QTreeWidget::item:selected"):
        assert "border-left" not in _style_block(stylesheet, selector)


def test_selection_marker_geometry_is_a_leading_vertical_bar() -> None:
    from PySide6.QtCore import QRect

    from tomography_session_browser.ui.list_decorations import (
        SELECTION_MARKER_WIDTH,
        selection_marker_rect,
    )

    row = QRect(10, 40, 260, 30)
    bar = selection_marker_rect(row)

    assert bar.left() == row.left()
    assert bar.width() == SELECTION_MARKER_WIDTH
    # Inset vertically so consecutive selected rows do not form one long line.
    assert bar.top() > row.top()
    assert bar.bottom() < row.bottom()
    assert bar.height() > row.height() / 2


@pytest.mark.parametrize("palette", PALETTES, ids=lambda p: p.name)
def test_selection_and_highlight_markers_use_different_accents(palette) -> None:
    """A cross-view highlighted row must stay distinct from the selected one."""

    assert palette.accent != palette.accent_strong


@pytest.mark.parametrize("palette", PALETTES, ids=lambda p: p.name)
def test_selection_marker_clears_the_non_text_contrast_floor(palette) -> None:
    """The accent bar is what actually signals "this row is current".

    The fill cannot carry it: at 3:1 against the panel it would be a heavy
    block that fights the rest of the calm palette. The 3px bar can.
    """

    assert _contrast_ratio(palette.accent, palette.panel) >= 3.0


@pytest.mark.parametrize("palette", PALETTES, ids=lambda p: p.name)
def test_selection_fill_is_at_least_perceptible_against_the_panel(palette) -> None:
    """Light mode shipped at 1.01:1 — literally indistinguishable."""

    assert _contrast_ratio(palette.selection, palette.panel) >= 1.10


@pytest.mark.parametrize("palette", PALETTES, ids=lambda p: p.name)
def test_selected_row_text_remains_readable_on_the_fill(palette) -> None:
    assert _contrast_ratio(palette.selection_text, palette.selection) >= 4.5


def _contrast_ratio(foreground: str, background: str) -> float:
    fg = _relative_luminance(foreground)
    bg = _relative_luminance(background)
    return (max(fg, bg) + 0.05) / (min(fg, bg) + 0.05)


def _relative_luminance(hex_colour: str) -> float:
    value = hex_colour.strip().lstrip("#")
    channels = [int(value[index : index + 2], 16) / 255.0 for index in (0, 2, 4)]

    def linear(channel: float) -> float:
        if channel <= 0.04045:
            return channel / 12.92
        return ((channel + 0.055) / 1.055) ** 2.4

    red, green, blue = (linear(channel) for channel in channels)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue
