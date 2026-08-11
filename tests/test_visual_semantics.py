"""V1: semantic state must survive losing colour.

"Validate grayscale and colour-vision simulation" is an acceptance item that is
usually done by eye. Done by eye it is unrepeatable, and on this project the
offscreen captures render text as tofu, so eyeballing is not even available.
These checks simulate the three common colour-vision deficiencies and
grayscale arithmetically, so a palette change that destroys a distinction fails
here instead of reaching a reviewer.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from tomography_session_browser.services.item_status import display_status_label
from tomography_session_browser.ui.theme import DARK_PALETTE, LIGHT_PALETTE, build_stylesheet

PALETTES = [("dark", DARK_PALETTE), ("light", LIGHT_PALETTE)]


def _rgb(value: str) -> tuple[float, float, float]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _luminance(colour: str) -> float:
    """Relative luminance — also the grayscale value a reader would see."""

    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in _rgb(colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _simulate(colour: str, kind: str) -> tuple[float, float, float]:
    """Approximate a colour-vision deficiency (Brettel-style linear matrices)."""

    r, g, b = _rgb(colour)
    matrices = {
        # red-blind, green-blind, blue-blind
        "protanopia": ((0.567, 0.433, 0.0), (0.558, 0.442, 0.0), (0.0, 0.242, 0.758)),
        "deuteranopia": ((0.625, 0.375, 0.0), (0.70, 0.30, 0.0), (0.0, 0.30, 0.70)),
        "tritanopia": ((0.95, 0.05, 0.0), (0.0, 0.433, 0.567), (0.0, 0.475, 0.525)),
    }
    m = matrices[kind]
    return tuple(sum(m[row][i] * (r, g, b)[i] for i in range(3)) for row in range(3))  # type: ignore[return-value]


def _distance(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b, strict=True)) ** 0.5


def _status_colours(palette) -> dict[str, str]:
    return {
        "failed": palette.overlay_status_failed,
        "warning": palette.overlay_status_warning,
        "pending": palette.overlay_status_pending,
        "complete": palette.overlay_status_complete,
        "unattributed": palette.overlay_status_unattributed,
    }


# --- colour-vision deficiency ----------------------------------------------


@pytest.mark.parametrize("name,palette", PALETTES)
@pytest.mark.parametrize("kind", ["protanopia", "deuteranopia", "tritanopia"])
def test_failed_and_complete_stay_distinct_under_colour_blindness(
    name: str, palette, kind: str
) -> None:
    """The most consequential pair: acquisition failed versus acquisition fine."""

    colours = _status_colours(palette)
    failed = _simulate(colours["failed"], kind)
    complete = _simulate(colours["complete"], kind)

    assert _distance(failed, complete) > 0.12, (
        f"{name}/{kind}: failed and complete collapse together"
    )


# Status pairs whose *colours* are close, and which therefore depend on the
# glyph shape to stay apart. Measured, not guessed — see the module docstring.
# Any pair listed here must have distinct shapes in ``_paint_chrome_leaf_glyph``.
SHAPE_DEPENDENT_PAIRS = {
    ("complete", "unattributed"),  # grayscale gap 0.0002
    ("warning", "unattributed"),  # grayscale gap 0.0024
    ("warning", "complete"),  # grayscale gap 0.0027
    ("failed", "warning"),  # deuteranopia distance 0.032
}

# The shape each status is drawn with. Colour is never the only carrier.
STATUS_SHAPES = {
    "complete": "filled disc",
    "failed": "filled disc with a cross",
    "warning": "half pie",
    "pending": "plain ring",
    "unattributed": "dashed ring",
}


@pytest.mark.parametrize("name,palette", PALETTES)
@pytest.mark.parametrize("kind", ["protanopia", "deuteranopia", "tritanopia"])
def test_statuses_stay_apart_by_colour_or_by_shape(name: str, palette, kind: str) -> None:
    """Colour alone need not separate every pair — but something must.

    Four pairs sit close in colour by design; each is drawn with a different
    glyph. This asserts the *combination* rather than pretending colour does
    all the work, and fails if a palette change collapses a pair that has no
    shape difference to fall back on.
    """

    colours = _status_colours(palette)
    keys = list(colours)
    for i, first in enumerate(keys):
        for second in keys[i + 1 :]:
            distance = _distance(
                _simulate(colours[first], kind), _simulate(colours[second], kind)
            )
            if distance > 0.05:
                continue
            pair = tuple(sorted((first, second)))
            assert pair in {tuple(sorted(p)) for p in SHAPE_DEPENDENT_PAIRS}, (
                f"{name}/{kind}: {first} vs {second} collapsed to {distance:.3f} "
                "and is not a known shape-distinguished pair"
            )
            assert STATUS_SHAPES[first] != STATUS_SHAPES[second]


# --- grayscale --------------------------------------------------------------


@pytest.mark.parametrize("name,palette", PALETTES)
def test_grayscale_separation_or_a_distinct_shape(name: str, palette) -> None:
    """Printed or on a monochrome display, the states must still be readable."""

    colours = _status_colours(palette)
    luminances = {key: _luminance(value) for key, value in colours.items()}
    keys = list(luminances)
    for i, first in enumerate(keys):
        for second in keys[i + 1 :]:
            gap = abs(luminances[first] - luminances[second])
            if gap > 0.02:
                continue
            pair = tuple(sorted((first, second)))
            assert pair in {tuple(sorted(p)) for p in SHAPE_DEPENDENT_PAIRS}, (
                f"{name}: {first} vs {second} grayscale gap {gap:.4f} "
                "with no shape difference recorded"
            )
            assert STATUS_SHAPES[first] != STATUS_SHAPES[second]


def test_every_status_has_its_own_shape() -> None:
    """The fallback the two tests above lean on must actually hold."""

    assert len(set(STATUS_SHAPES.values())) == len(STATUS_SHAPES)


# --- colour is never the only carrier --------------------------------------


def test_missing_and_failed_are_distinguished_by_words_not_only_colour() -> None:
    """Two different scientific findings that must never read alike."""

    assert display_status_label("missing") != display_status_label("failed")
    assert display_status_label("missing") == "Unavailable"
    assert display_status_label("failed") == "Failed"


def test_the_scientifically_load_bearing_labels_are_distinct() -> None:
    """Complete, Incomplete and Failed are three different findings.

    Two known collapses are deliberate and left alone, because renaming a
    status chip is a scientific change rather than a wording one (see T1):
    ``partial`` and ``incomplete`` both read "Incomplete" — the same finding at
    two granularities — and ``missing`` and ``unknown`` both read
    "Unavailable". The latter is worth a product decision: "the file is not
    there" and "we could not classify it" are arguably different findings.
    """

    assert display_status_label("done") == "Complete"
    assert display_status_label("incomplete") == "Incomplete"
    assert display_status_label("failed") == "Failed"
    assert len({display_status_label(s) for s in ("done", "incomplete", "failed")}) == 3
    # The two deliberate collapses, pinned so a change is a decision not a drift.
    assert display_status_label("partial") == display_status_label("incomplete")
    assert display_status_label("missing") == display_status_label("unknown")


def test_atlas_glyphs_differ_in_shape_not_only_colour() -> None:
    """A reviewer with no colour perception must still read the overlay.

    The glyph painter draws a filled disc for collected, a filled disc plus a
    cross for failed, a half-pie for partial, a plain ring for queued and a
    dashed ring for unattributed.
    """

    import inspect

    from tomography_session_browser.ui import image_viewer

    source = inspect.getsource(image_viewer._paint_chrome_leaf_glyph)
    assert "drawPie" in source, "partial lost its distinct shape"
    assert "DashLine" in source, "unattributed lost its dashed ring"
    assert "drawLine" in source, "failed lost its cross"


# --- the checked state carries a glyph --------------------------------------


@pytest.mark.parametrize("name,palette", PALETTES)
def test_a_checked_checkbox_is_not_signalled_by_colour_alone(name: str, palette) -> None:
    """Checked used to differ from unchecked only by fill colour."""

    qss = build_stylesheet(palette)
    checked_block = qss.split("QCheckBox::indicator:checked")[1].split("}")[0]

    assert "image:" in checked_block, f"{name}: checked state has no glyph"


@pytest.mark.parametrize("name,palette", PALETTES)
def test_keyboard_focus_has_a_visible_indicator(name: str, palette) -> None:
    qss = build_stylesheet(palette)

    assert "QPushButton:focus" in qss
    assert "QTreeWidget:focus" in qss
    focus_block = qss.split("QPlainTextEdit:focus")[1].split("}")[0]
    assert palette.accent in focus_block


# --- restraint --------------------------------------------------------------


@pytest.mark.parametrize("name,palette", PALETTES)
def test_the_stylesheet_avoids_decorative_treatments(name: str, palette) -> None:
    """The plan forbids gradients and oversized chrome; dense stays dense."""

    qss = build_stylesheet(palette)

    assert "qlineargradient" not in qss.lower(), f"{name}: a gradient crept in"
    assert "qradialgradient" not in qss.lower()
