"""Regression tests for the second round of UX audit fixes.

Covers status-chip colour (5), chart series distinctness (7), inferred batch
rows (9), zero-value chips (11), terminology (12), tile counts (13),
timestamps (14), warning headline (15), "most affected" filtering (16), MRC
metadata legibility (17), header/focus theming (26/27), and keyboard
shortcuts (28).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from tomography_session_browser.domain.models import MdocSection, MrcMetadata, SearchMap, TiltSeries
from tomography_session_browser.domain.units import ANGSTROM, DEGREE
from tomography_session_browser.ui import session_presenter
from tomography_session_browser.ui.session_presenter import (
    SearchMapOverviewModel,
    SearchMapOverviewRowModel,
    describe_object,
)
from tomography_session_browser.ui.theme import DARK_PALETTE, LIGHT_PALETTE, build_stylesheet

PALETTES = (DARK_PALETTE, LIGHT_PALETTE)


# ------------------------------------------------------- 7: series colours


def _to_lab(hex_colour: str) -> tuple[float, float, float]:
    value = hex_colour.lstrip("#")
    channels = [int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)]

    def linear(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(c) for c in channels)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def _delta_e(a: str, b: str) -> float:
    la, lb = _to_lab(a), _to_lab(b)
    return sum((x - y) ** 2 for x, y in zip(la, lb)) ** 0.5


@pytest.mark.parametrize("palette", PALETTES, ids=lambda p: p.name)
def test_no_two_chart_series_tokens_are_identical(palette) -> None:
    """chart_blue and chart_violet shipped as the same hex.

    A five-sample session therefore drew series two and five in exactly the
    same colour, which reads as one series with twice the points.
    """

    tokens = ["accent", "chart_blue", "chart_green", "chart_amber", "chart_violet", "chart_red"]
    values = [getattr(palette, token) for token in tokens]
    assert len(set(values)) == len(values)


@pytest.mark.parametrize("palette", PALETTES, ids=lambda p: p.name)
def test_chart_blue_and_violet_are_separated_by_hue(palette) -> None:
    assert _delta_e(palette.chart_blue, palette.chart_violet) >= 25


@pytest.mark.parametrize("palette", PALETTES, ids=lambda p: p.name)
def test_every_plot_series_pair_is_perceptibly_different(palette) -> None:
    """Series colours are chosen by hashing the sample name.

    List order therefore confers nothing — any subset can appear together,
    so *every* pair has to be distinguishable, not just the leading ones.
    The tightest pair is accent (teal) against chart_green (sage);
    chart_green cannot move because it is also the app-wide "complete"
    status colour, so 17.5 is the floor this palette can honestly hold.
    """

    tokens = ["accent", "chart_blue", "chart_green", "chart_amber", "chart_violet", "chart_red"]
    values = [getattr(palette, token) for token in tokens]
    for index in range(len(values)):
        for other in range(index + 1, len(values)):
            assert _delta_e(values[index], values[other]) >= 17.5, f"{tokens[index]} vs {tokens[other]}"


# ----------------------------------------------- 16: "most affected" filter


def _overview_row(**overrides) -> SearchMapOverviewRowModel:
    defaults = dict(
        id="sm-1",
        label="SearchMap_1",
        status="complete",
        planned=2,
        acquired=2,
        failed_tilt_series=0,
        incomplete_tilt_series=0,
        unknown_tilt_series=0,
        batch_positions=2,
        acquired_batch_positions=2,
        acquisition_associated=True,
    )
    defaults.update(overrides)
    return SearchMapOverviewRowModel(**defaults)


def test_fully_acquired_search_map_is_not_most_affected() -> None:
    """A green 2/2 row in a section headed "MOST AFFECTED" is nonsense."""

    healthy = _overview_row()
    assert healthy.is_affected is False


def test_search_map_with_a_failed_tilt_series_is_affected() -> None:
    assert _overview_row(failed_tilt_series=1, status="failed").is_affected is True


def test_search_map_with_an_unacquired_batch_position_is_affected() -> None:
    assert _overview_row(acquired_batch_positions=1).is_affected is True


def test_unassociated_search_map_is_not_reported_as_affected() -> None:
    """"N/A" maps have nothing to complete, so they are not findings."""

    assert _overview_row(acquisition_associated=False).is_affected is False


def test_worst_returns_only_affected_rows() -> None:
    model = SearchMapOverviewModel(
        rows=[
            _overview_row(id="a", label="A"),
            _overview_row(id="b", label="B", failed_tilt_series=2, status="failed"),
            _overview_row(id="c", label="C"),
        ],
        complete=2,
        incomplete=0,
        failed=1,
        unknown=0,
    )
    assert [row.id for row in model.worst] == ["b"]
    assert model.has_affected_rows is True


def test_worst_is_empty_when_nothing_is_affected() -> None:
    model = SearchMapOverviewModel(
        rows=[_overview_row(id="a"), _overview_row(id="b")],
        complete=2,
        incomplete=0,
        failed=0,
        unknown=0,
    )
    assert model.worst == []
    assert model.has_affected_rows is False


# ------------------------------------------- 17: MRC metadata legibility


def test_mrc_mode_is_named_not_just_numbered() -> None:
    assert session_presenter._mrc_mode_text(6) == "6 (uint16)"
    assert session_presenter._mrc_mode_text(2) == "2 (float32)"
    assert session_presenter._mrc_mode_text(None) == "unknown"
    assert session_presenter._mrc_mode_text(99) == "99 (unrecognised)"


def test_voxel_size_carries_its_unit() -> None:
    text = session_presenter._voxel_size_text((27.3134, 27.3134, 27.3134))
    assert text.endswith(ANGSTROM)
    assert "×" in text
    assert " x " not in text


def test_voxel_size_without_values_says_unknown() -> None:
    assert session_presenter._voxel_size_text((None, None, None)) == "unknown"


def test_dimensions_use_a_multiplication_sign_and_a_unit() -> None:
    metadata = MrcMetadata(path=Path("a.mrc"), size_bytes=0, nx=2880, ny=2046, nz=1)
    text = session_presenter._dimensions_text(metadata)
    assert text == "2880 × 2046 × 1 px"


# ------------------------------------------------------ 14: timestamps


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-03-19T13:52:11.3176724+11:00", "2026-03-19 13:52:11"),
        ("2026-03-19 13:52:11", "2026-03-19 13:52:11"),
        (datetime(2026, 3, 19, 13, 52, 11), "2026-03-19 13:52:11"),
        (None, "unknown"),
        ("", "unknown"),
    ],
)
def test_timestamps_render_in_one_format(raw, expected) -> None:
    assert session_presenter._format_timestamp(raw) == expected


def test_unparseable_timestamp_is_passed_through_not_hidden() -> None:
    assert session_presenter._format_timestamp("sometime last Tuesday") == "sometime last Tuesday"


# --------------------------------------------------- 12/13: terminology


def _tilt(**overrides) -> TiltSeries:
    defaults = dict(
        id="t1",
        name="vellio_1",
        mrc_path=Path(__file__),  # a file that exists, so the quality block runs
        tilt_count=1,
        sections=[MdocSection(z_value=0, metadata={"TiltAngle": -0.02})],
    )
    defaults.update(overrides)
    return TiltSeries(**defaults)


def test_tilt_series_panel_uses_one_noun_for_its_image_count() -> None:
    text = describe_object(_tilt())
    assert "Tilt count:" not in text
    assert "Sections:" not in text
    assert "Tilt images:" in text


def test_quality_block_states_a_judgement_not_a_third_ratio() -> None:
    text = describe_object(_tilt())
    assert "Coverage:" in text


def test_search_map_tile_count_matches_the_chip_and_list_row() -> None:
    """"Tiles: 60" counted files; the chip and rows counted 30 distinct tiles."""

    search_map = SearchMap(
        id="sm",
        name="SearchMap_1",
        tile_paths=[Path(f"Tile_{index}{suffix}") for index in range(1, 31) for suffix in (".mrc", ".jpg")],
    )
    text = describe_object(search_map)
    assert "Tiles: 30" in text
    assert "Tile image files: 60" in text


# ------------------------------------------------ 26/27: chrome theming


def _style_block(stylesheet: str, selector: str) -> str:
    import re

    stripped = re.sub(r"/\*.*?\*/", "", stylesheet, flags=re.DOTALL)
    for raw in stripped.split("}"):
        if "{" not in raw:
            continue
        head, _, body = raw.rpartition("{")
        if selector in {part.strip() for part in head.split(",")}:
            return body
    raise AssertionError(f"selector not found: {selector}")


@pytest.mark.parametrize("palette", PALETTES, ids=lambda p: p.name)
def test_table_headers_are_themed(palette) -> None:
    """The report-scope tree drew a platform-grey header against themed chrome."""

    block = _style_block(build_stylesheet(palette), "QHeaderView::section")
    assert f"background: {palette.surface_alt};" in block
    assert f"color: {palette.text_muted};" in block


@pytest.mark.parametrize("palette", PALETTES, ids=lambda p: p.name)
def test_keyboard_focus_is_visible_on_controls(palette) -> None:
    stylesheet = build_stylesheet(palette)
    assert f"border: 1px solid {palette.accent};" in _style_block(stylesheet, "QPushButton:focus")


@pytest.mark.parametrize("palette", PALETTES, ids=lambda p: p.name)
def test_primary_button_label_clears_the_contrast_floor(palette) -> None:
    """Reusing ``accent`` for both palettes left the light button at 3.3:1.

    The right fill/label pairing inverts between themes, so each palette
    carries its own primary tokens.
    """

    block = _style_block(build_stylesheet(palette), "QPushButton#primaryButton")
    assert f"background: {palette.primary_fill};" in block
    assert f"color: {palette.primary_text};" in block
    assert _contrast(palette.primary_text, palette.primary_fill) >= 4.5
    assert _contrast(palette.primary_text, palette.primary_fill_hover) >= 4.5


def _contrast(foreground: str, background: str) -> float:
    def luminance(colour: str) -> float:
        value = colour.lstrip("#")
        channels = [int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)]

        def linear(c: float) -> float:
            return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

        r, g, b = (linear(c) for c in channels)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    a, b = luminance(foreground), luminance(background)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


# ---------------------------------------------------- 5: status chips


@pytest.mark.parametrize(
    ("status", "token"),
    [("failed", "chart_red"), ("done", "chart_green"), ("partial", "chart_amber")],
)
def test_header_status_chip_colour_follows_the_status(status: str, token: str) -> None:
    """A failed tilt series showed a sage accent chip in the header.

    Resolved against the *active* palette, since the badge colours are
    theme-aware and other tests in the session may have switched theme.
    """

    from tomography_session_browser.ui.image_viewer import _status_badge_colors
    from tomography_session_browser.ui.theme import current_palette

    foreground, _background = _status_badge_colors(status)
    assert foreground.name().lower() == getattr(current_palette(), token).lower()


def test_header_status_chip_is_not_a_fixed_accent() -> None:
    """The three outcomes must not all resolve to the same colour."""

    from tomography_session_browser.ui.image_viewer import _status_badge_colors

    names = {_status_badge_colors(status)[0].name() for status in ("failed", "done", "partial")}
    assert len(names) == 3


def test_status_chips_explain_themselves() -> None:
    from tomography_session_browser.ui.image_viewer import _status_chip_tooltip

    assert _status_chip_tooltip("failed").startswith("failed — ")
    assert _status_chip_tooltip("not-a-status") == "not-a-status"
