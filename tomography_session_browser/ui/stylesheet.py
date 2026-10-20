"""The application style sheet: one QSS template, filled from a palette.

The template is split by what it styles into the fragments in
``ui/stylesheets/`` (joined in a fixed order: QSS precedence depends on it).
``build_stylesheet`` substitutes a ``ThemePalette`` and the type, radius and
font tokens (``presentation/design_tokens.py``) into it, then adds the tone
rules, so both themes stay in sync. ``ui/theme.py`` installs it.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QStandardPaths

from tomography_session_browser.presentation.design_tokens import (
    CANVAS_CHROME,
    DASHBOARD_TONE_COLOURS,
    MONO_FONT_FAMILY,
    RADIUS_CARD,
    RADIUS_CONTROL,
    RADIUS_POPOVER,
    SANS_FONT_FAMILY,
    STATUS_TONE_COLOURS,
    STATUS_TONE_FILL_ALPHA,
    TYPE_BODY,
    TYPE_CAPTION,
    TYPE_DISPLAY,
    TYPE_HEADING,
    TYPE_LEAD,
    TYPE_TITLE,
    ThemePalette,
    hex_rgb,
    readable_tone,
)
from tomography_session_browser.ui.stylesheets import QSS_TEMPLATE


def _status_tone_rules(palette: ThemePalette) -> str:
    rules = []
    for tone, slot in STATUS_TONE_COLOURS.items():
        colour = getattr(palette, slot).lstrip("#")
        red, green, blue = (int(colour[index : index + 2], 16) for index in (0, 2, 4))
        rules.append(
            f'QLabel#viewerChipStatus[tone="{tone}"] {{\n'
            f"    background: rgba({red}, {green}, {blue}, {STATUS_TONE_FILL_ALPHA});\n"
            f"    border-color: #{colour};\n"
            "}\n"
        )
    return "\n".join(rules)


def _dashboard_tone_rules(palette: ThemePalette) -> str:
    rules = []
    for tone, slot in DASHBOARD_TONE_COLOURS.items():
        colour = getattr(palette, slot)
        # surface_hi is the lowest-contrast dashboard surface in both palettes.
        text_colour = readable_tone(colour, palette.surface_hi, palette.text_strong)
        red, green, blue = hex_rgb(colour)
        rules.append(
            f'QFrame#metaCallout[tone="{tone}"] {{ background: rgba({red}, {green}, {blue}, 20); '
            f"border-color: rgba({red}, {green}, {blue}, 115); }}\n"
        )
        rules.append(
            f'QLabel#statusDot[tone="{tone}"] {{ color: {colour}; }}\n'
            f'QLabel#searchMapSummaryChip[tone="{tone}"] {{ border-color: {colour}; color: {text_colour}; }}\n'
            f'QLabel#sampleCount[tone="{tone}"] {{ color: {palette.text_strong}; border-color: {colour}; }}\n'
            f'QLabel#warningCount[tone="{tone}"] {{ color: {text_colour}; border-color: {colour}; }}\n'
            f'QLabel#toneText[tone="{tone}"] {{ color: {text_colour}; }}\n'
            f'QToolButton#inlineLink[tone="{tone}"], QToolButton#inlineLink[tone="{tone}"]:hover '
            f'{{ color: {text_colour}; }}\n'
            f'QLabel#statePill[tone="{tone}"] {{ color: {text_colour}; border-color: {colour}; '
            f"background: rgba({red}, {green}, {blue}, 20); }}\n"
        )
    rules.append('QLabel#searchMapSummaryChip[spaced="true"] { margin-top: 4px; }\n')
    return "\n".join(rules)


def _viewer_status_rules(palette: ThemePalette) -> str:
    """Tones for the viewer's provenance line, readable on the page."""

    warn = readable_tone(palette.chart_amber, palette.background, palette.text_strong)
    bad = readable_tone(palette.chart_red, palette.background, palette.text_strong)
    return (
        f'QLabel#viewerStatus[tone="warn"] {{ color: {warn}; }}\n'
        f'QLabel#viewerStatus[tone="bad"] {{ color: {bad}; font-weight: 600; }}\n'
    )


def _session_intake_rules(palette: ThemePalette) -> str:
    """Tone colours for the Load sessions window and the drop receipt."""

    red, green, blue = hex_rgb(palette.chart_red)
    good = readable_tone(palette.selection_marker, palette.surface, palette.text_strong)
    bad_on_surface = readable_tone(palette.chart_red, palette.surface, palette.text_strong)
    bad_on_panel = readable_tone(palette.chart_red, palette.panel, palette.text_strong)
    warn = readable_tone(palette.chart_amber, palette.surface_alt, palette.text_strong)
    return (
        f'QFrame#queuedFolderRow[tone="bad"] {{ background: rgba({red}, {green}, {blue}, 12); }}\n'
        f"QLabel#queuedFolderReason {{ color: {bad_on_panel}; }}\n"
        f'QLabel#queueCount[tone="good"] {{ color: {good}; }}\n'
        f'QLabel#queueCount[tone="bad"] {{ color: {bad_on_surface}; }}\n'
        f"QLabel#dropReceiptSkipped {{ color: {warn}; }}\n"
    )


_QSS_TEMPLATE = QSS_TEMPLATE


def build_stylesheet(palette: ThemePalette) -> str:
    sheet = _QSS_TEMPLATE.format(
        **palette.__dict__,
        **{f"canvas_{slot}": value for slot, value in CANVAS_CHROME.__dict__.items()},
        sans_font=SANS_FONT_FAMILY,
        mono_font=MONO_FONT_FAMILY,
        check_icon=_indicator_path(palette, "check"),
        canvas_check_icon=_indicator_path(CANVAS_CHROME, "check"),
        canvas_dash_icon=_indicator_path(CANVAS_CHROME, "dash"),
        radius_control=RADIUS_CONTROL,
        radius_card=RADIUS_CARD,
        radius_popover=RADIUS_POPOVER,
        type_caption=TYPE_CAPTION,
        type_body=TYPE_BODY,
        type_lead=TYPE_LEAD,
        type_title=TYPE_TITLE,
        type_heading=TYPE_HEADING,
        type_display=TYPE_DISPLAY,
    )
    # After the base ``viewerChipStatus`` rule they refine.
    return (
        sheet
        + "\n"
        + _status_tone_rules(palette)
        + "\n"
        + _dashboard_tone_rules(palette)
        + "\n"
        + _session_intake_rules(palette)
        + "\n"
        + _viewer_status_rules(palette)
    )


def _indicator_path(palette: ThemePalette, icon: str) -> str:
    """Absolute POSIX path to a check box mark (``check`` or ``dash``) recoloured for ``palette``.

    Written once per palette into the user's cache directory: Qt style sheets
    can only reference an image by URL, so this one icon has to exist on disk
    rather than being recoloured in memory like every other icon.

    Deliberately self-contained: ``ui.icons`` loads the theme, so reaching
    into it from here invites a circular import.
    """

    source = Path(__file__).resolve().parent.parent / "assets" / "icons" / f"{icon}.svg"
    colour = palette.primary_text
    try:
        cache_root = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.CacheLocation
        )
        cache_dir = Path(cache_root) if cache_root else source.parent
        cache_dir.mkdir(parents=True, exist_ok=True)
        target = cache_dir / f"{icon}-{colour.lstrip('#').lower()}.svg"
        if not target.exists():
            target.write_text(
                source.read_text(encoding="utf-8").replace("currentColor", colour),
                encoding="utf-8",
            )
        return target.as_posix()
    except OSError:
        # A read-only cache location must never break theming; the uncoloured
        # mark still reads as that mark.
        return source.as_posix()
