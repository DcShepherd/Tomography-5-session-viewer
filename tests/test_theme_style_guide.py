from pathlib import Path
import re

from tomography_session_browser.ui.theme import (
    DARK_PALETTE,
    LIGHT_PALETTE,
    MONO_FONT_FAMILY,
    SANS_FONT_FAMILY,
    VISUAL_TIERS,
    build_stylesheet,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def _style_blocks_for_selector(stylesheet: str, selector: str) -> list[str]:
    blocks: list[str] = []
    for raw_block in stylesheet.split("}"):
        if selector not in raw_block or "{" not in raw_block:
            continue
        blocks.append(raw_block.rsplit("{", 1)[-1])
    return blocks


def test_style_guide_palette_tokens_are_applied() -> None:
    assert DARK_PALETTE.background == "#0c1116"
    assert DARK_PALETTE.accent == "#6fb3a8"
    assert DARK_PALETTE.chart_green == "#7eb287"
    assert DARK_PALETTE.canvas == "#04070b"
    assert DARK_PALETTE.overlay_selected == "#fbbf24"
    assert LIGHT_PALETTE.background == "#f3f5f7"
    assert LIGHT_PALETTE.accent == "#4f9388"
    assert LIGHT_PALETTE.chart_red == "#b85d5d"
    assert LIGHT_PALETTE.unknown == "#a8aeb6"
    assert LIGHT_PALETTE.canvas == DARK_PALETTE.canvas
    assert LIGHT_PALETTE.overlay_selected == DARK_PALETTE.overlay_selected


def test_light_and_dark_palettes_define_the_same_token_set() -> None:
    assert set(DARK_PALETTE.__dict__) == set(LIGHT_PALETTE.__dict__)


def test_core_text_tokens_clear_body_contrast_floor() -> None:
    for palette in (DARK_PALETTE, LIGHT_PALETTE):
        assert _contrast_ratio(palette.text, palette.background) >= 4.5
        assert _contrast_ratio(palette.text_muted, palette.background) >= 4.5
        assert _contrast_ratio(palette.text_strong, palette.background) >= 4.5


def test_image_overlay_tokens_remain_high_contrast() -> None:
    # Overlay colours are intentionally brighter than the app chrome because
    # they are drawn directly on microscope images, including light fields.
    assert DARK_PALETTE.marker_exposure == "#16a34a"
    assert DARK_PALETTE.marker_focus == "#2563eb"
    assert DARK_PALETTE.marker_tracking == "#facc15"
    assert DARK_PALETTE.marker_search == "#22c55e"
    assert LIGHT_PALETTE.marker_exposure == DARK_PALETTE.marker_exposure
    assert LIGHT_PALETTE.marker_search == DARK_PALETTE.marker_search
    assert DARK_PALETTE.overlay_status_unattributed == "#9ca3af"
    assert DARK_PALETTE.overlay_marker_halo == "#020617"
    assert LIGHT_PALETTE.overlay_status_unattributed == DARK_PALETTE.overlay_status_unattributed
    assert LIGHT_PALETTE.overlay_marker_halo == DARK_PALETTE.overlay_marker_halo
    assert DARK_PALETTE.atlas_marker_collected == "#48B06C"
    assert DARK_PALETTE.atlas_marker_partial == "#C78B09"
    assert DARK_PALETTE.atlas_marker_queued == "#599FD8"
    assert DARK_PALETTE.atlas_marker_failed == "#FD7468"
    assert DARK_PALETTE.atlas_marker_unattributed == "#8F9AA4"
    assert LIGHT_PALETTE.atlas_marker_collected == "#137D41"
    assert LIGHT_PALETTE.atlas_marker_partial == "#8E5E00"
    assert LIGHT_PALETTE.atlas_marker_queued == "#036EAE"
    assert LIGHT_PALETTE.atlas_marker_failed == "#C53732"
    assert LIGHT_PALETTE.atlas_marker_unattributed == "#606A74"


def test_generated_stylesheet_uses_central_type_and_theme_tokens() -> None:
    stylesheet = build_stylesheet(DARK_PALETTE)

    assert SANS_FONT_FAMILY in stylesheet
    assert MONO_FONT_FAMILY in stylesheet
    assert "," not in SANS_FONT_FAMILY
    assert "," not in MONO_FONT_FAMILY
    assert DARK_PALETTE.accent in stylesheet
    assert DARK_PALETTE.accent_line in stylesheet
    assert DARK_PALETTE.surface_hi in stylesheet


def test_viewer_floating_controls_keep_transparent_parent_chrome() -> None:
    stylesheet = build_stylesheet(LIGHT_PALETTE)

    top_control_blocks = _style_blocks_for_selector(stylesheet, "QWidget#viewerTopControls")
    assert top_control_blocks
    assert "background: transparent;" in top_control_blocks[-1]
    assert "border: 0;" in top_control_blocks[-1]

    tool_button_blocks = _style_blocks_for_selector(stylesheet, "QPushButton#viewerToolButton")
    assert tool_button_blocks
    assert "background:" in tool_button_blocks[0]
    assert "border: 1px solid" in tool_button_blocks[0]


def test_checked_viewer_tool_button_uses_solid_theme_contrast() -> None:
    for palette in (DARK_PALETTE, LIGHT_PALETTE):
        stylesheet = build_stylesheet(palette)
        checked_blocks = _style_blocks_for_selector(
            stylesheet,
            "QPushButton#viewerToolButton:checked",
        )

        assert checked_blocks
        assert f"background: {palette.primary_fill};" in checked_blocks[0]
        assert f"color: {palette.primary_text};" in checked_blocks[0]


def test_session_pill_keeps_capsule_shape() -> None:
    stylesheet = build_stylesheet(DARK_PALETTE)

    session_pill_blocks = _style_blocks_for_selector(stylesheet, "QFrame#sessionPill")
    assert session_pill_blocks
    assert "border-radius: 15px;" in session_pill_blocks[-1]
    assert "min-height: 26px;" in session_pill_blocks[-1]


def test_camera_dose_heading_matches_dashboard_card_title_type_scale() -> None:
    stylesheet = build_stylesheet(DARK_PALETTE)

    card_title = _style_blocks_for_selector(stylesheet, "QLabel#cardTitle")[-1]
    dose_heading = _style_blocks_for_selector(stylesheet, "QLabel#doseCardHeading")[-1]

    for rule in ("font-size: 8.5pt;", "font-weight: 600;", "letter-spacing: 0;"):
        assert rule in card_title
        assert rule in dose_heading
    assert f"color: {DARK_PALETTE.text_muted};" in card_title
    assert f"color: {DARK_PALETTE.text_muted};" in dose_heading
    assert "text-transform: uppercase;" not in dose_heading


def test_generated_stylesheet_has_no_expanded_letter_spacing() -> None:
    assert "letter-spacing: 0.08em;" not in build_stylesheet(DARK_PALETTE)
    assert "letter-spacing: 0.08em;" not in build_stylesheet(LIGHT_PALETTE)


def test_ui_hex_colours_are_centralised_in_theme_tokens() -> None:
    ui_dir = REPO_ROOT / "tomography_session_browser" / "ui"
    offenders: list[str] = []
    for path in ui_dir.rglob("*.py"):
        if path.name == "theme.py":
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"#[0-9A-Fa-f]{3,8}", text):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == []


def test_atlas_overlay_icons_are_present_and_use_current_color() -> None:
    icon_dir = REPO_ROOT / "tomography_session_browser" / "assets" / "icons"
    for name in (
        "cluster-positions.svg",
        "position-labels.svg",
        "tile-grid.svg",
        "exposure-markers.svg",
    ):
        text = (icon_dir / name).read_text(encoding="utf-8")
        assert 'viewBox="0 0 24 24"' in text
        assert 'stroke="currentColor"' in text
        assert 'stroke-width="1.8"' in text


def _contrast_ratio(foreground: str, background: str) -> float:
    fg = _relative_luminance(foreground)
    bg = _relative_luminance(background)
    lighter = max(fg, bg)
    darker = min(fg, bg)
    return (lighter + 0.05) / (darker + 0.05)


def _relative_luminance(hex_colour: str) -> float:
    value = hex_colour.strip().lstrip("#")
    channels = [int(value[index : index + 2], 16) / 255.0 for index in (0, 2, 4)]

    def linear(channel: float) -> float:
        if channel <= 0.04045:
            return channel / 12.92
        return ((channel + 0.055) / 1.055) ** 2.4

    red, green, blue = (linear(channel) for channel in channels)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


# --- the three visual tiers (V1) -------------------------------------------


def test_every_visual_tier_is_defined_in_both_palettes() -> None:
    for palette in (DARK_PALETTE, LIGHT_PALETTE):
        stylesheet = build_stylesheet(palette)
        for tier in VISUAL_TIERS:
            assert _style_blocks_for_selector(stylesheet, f'[tier="{tier}"]'), tier


def test_every_visual_tier_has_a_widget_that_carries_it() -> None:
    """The tiers were dead CSS: defined, documented, and used by nothing.

    ``theme.py`` declared ``tierLandmark``/``tierPrimary``/``tierSupporting``
    and no widget ever set them, so V1's "three visual tiers" could not be seen
    on screen and the native-display checklist item asking a human to verify
    them had nothing to look at. This test fails if that happens again.
    """

    sources = [
        path.read_text(encoding="utf-8")
        for path in (REPO_ROOT / "tomography_session_browser" / "ui").rglob("*.py")
        if path.name != "theme.py"
    ]
    joined = "\n".join(sources)

    for tier in VISUAL_TIERS:
        constant = f"TIER_{tier.upper()}"
        assert f"apply_tier(" in joined
        assert constant in joined, f"No widget claims the {tier} tier"


def test_tiered_widgets_do_not_also_set_type_inline() -> None:
    """An inline font rule is a fourth place type is decided.

    It also bakes the palette in at construction, so a theme switch leaves the
    widget in the previous theme's colour until it is rebuilt.
    """

    offenders: list[str] = []
    for path in (REPO_ROOT / "tomography_session_browser" / "ui").rglob("*.py"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "setStyleSheet(" not in line:
                continue
            if "font-size" in line or "font-weight" in line:
                offenders.append(f"{path.name}:{number}")

    assert offenders == [], (
        "Type is set inline at: "
        + ", ".join(offenders)
        + ". Give the widget an object name or a tier instead."
    )
