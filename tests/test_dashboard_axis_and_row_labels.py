"""Regression tests for chart axis ticks and dashboard row legibility.

Covers audit items 21 (axis labels were arbitrary fractions of the data
range — ``+10.9 / +5.3 / -0.2 / -5.8``) and 3 (row names collapsed to a
single character next to a fixed-width status chip: ``o…``, ``R…``).
"""

from __future__ import annotations

from math import isclose

import pytest

from tomography_session_browser.ui.session_presenter import BatchPositionProgressModel
from tomography_session_browser.ui.widgets.session_dashboard import (
    _ROW_LABEL_MIN_WIDTH,
    _ROW_LABEL_MIN_WIDTH_COMPACT,
    _WARNING_LABEL_MIN_WIDTH,
    _batch_outcome_chip_parts,
)
from tomography_session_browser.ui.widgets.time_chart import (
    nice_axis_ticks,
    time_axis_tick_count,
    value_axis_tick_count,
)


# ------------------------------------------------------------- axis ticks


def _is_round(value: float, step: float) -> bool:
    """True when ``value`` is an exact multiple of ``step``."""

    quotient = value / step
    return isclose(quotient, round(quotient), abs_tol=1e-6)


def test_ticks_are_multiples_of_a_single_round_step() -> None:
    _, _, ticks = nice_axis_ticks(-11.4, 10.9, max_ticks=7)
    assert len(ticks) >= 2
    step = ticks[1] - ticks[0]
    assert all(_is_round(tick, step) for tick in ticks)
    # Uniform spacing.
    gaps = [round(b - a, 9) for a, b in zip(ticks, ticks[1:])]
    assert len(set(gaps)) == 1


def test_defocus_range_produces_readable_labels() -> None:
    """The exact case seen on the Session dashboard before the fix."""

    low, high, ticks = nice_axis_ticks(-11.4, 10.9, max_ticks=7)
    assert ticks == [-15.0, -10.0, -5.0, 0.0, 5.0, 10.0, 15.0]
    assert low == -15.0
    assert high == 15.0


def test_camera_dose_range_produces_integer_labels() -> None:
    low, high, ticks = nice_axis_ticks(0.0, 3.9, max_ticks=7)
    assert ticks == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert low == 0.0
    assert high == 4.0


def test_axis_bounds_always_contain_the_data() -> None:
    for low, high in ((-11.4, 10.9), (0.0, 3.9), (0.06, 0.61), (-8.2, -3.1), (1e-4, 5e-4)):
        axis_min, axis_max, ticks = nice_axis_ticks(low, high, max_ticks=6)
        assert axis_min <= low
        assert axis_max >= high
        assert ticks[0] == axis_min
        assert ticks[-1] == axis_max


def test_tick_count_stays_within_budget() -> None:
    for max_ticks in (3, 4, 5, 6, 7):
        for low, high in ((-11.4, 10.9), (0.0, 3.9), (-1.0, 1.0), (0.0, 987654.0)):
            _, _, ticks = nice_axis_ticks(low, high, max_ticks=max_ticks)
            assert 2 <= len(ticks) <= max_ticks


def test_degenerate_range_still_yields_a_usable_axis() -> None:
    """A failed series can report one identical value for every frame."""

    axis_min, axis_max, ticks = nice_axis_ticks(0.33, 0.33, max_ticks=5)
    assert axis_max > axis_min
    assert len(ticks) >= 2
    assert axis_min <= 0.33 <= axis_max


def test_zero_width_range_at_zero_is_handled() -> None:
    axis_min, axis_max, ticks = nice_axis_ticks(0.0, 0.0, max_ticks=5)
    assert axis_max > axis_min
    assert len(ticks) >= 2


def test_inverted_range_is_normalised() -> None:
    assert nice_axis_ticks(10.0, -10.0, max_ticks=5) == nice_axis_ticks(-10.0, 10.0, max_ticks=5)


def test_non_finite_input_does_not_raise() -> None:
    axis_min, axis_max, ticks = nice_axis_ticks(float("nan"), 1.0, max_ticks=5)
    assert len(ticks) == 2
    assert axis_min is not None and axis_max is not None


def test_value_axis_tick_count_grows_with_height() -> None:
    counts = [value_axis_tick_count(height) for height in (100, 200, 300, 400, 700)]
    assert counts == sorted(counts)
    assert counts[0] >= 3
    assert counts[-1] <= 8


def test_value_axis_is_sized_from_height_not_width() -> None:
    """A tall narrow chart should not be limited to three gridlines."""

    assert value_axis_tick_count(600) > time_axis_tick_count(300)


def test_defocus_zero_tick_has_no_sign() -> None:
    from tomography_session_browser.ui.widgets.defocus_plot import DefocusScatterPlot

    format_tick = DefocusScatterPlot._format_y_tick
    assert format_tick(None, 0.0) == "0.0"
    assert format_tick(None, 5.0) == "+5.0"
    assert format_tick(None, -5.0) == "-5.0"


@pytest.mark.parametrize(
    ("low", "high"),
    [(-11.4, 10.9), (0.0, 3.9), (0.06, 0.61), (-8.2, -3.1), (2.0, 2.4)],
)
def test_snapped_axis_does_not_squander_its_range(low: float, high: float) -> None:
    """Rounding the bounds must not leave the data in a thin band.

    A too-small tick budget forces a coarse step: at four ticks the defocus
    axis could only reach +/-20 for data spanning +/-11, pushing every point
    into the middle half of the plot. The budget is sized so the data keeps
    at least ~60% of the axis.
    """

    budget = value_axis_tick_count(220)
    axis_min, axis_max, _ = nice_axis_ticks(low, high, max_ticks=budget)
    assert (high - low) / (axis_max - axis_min) >= 0.6


# -------------------------------------------------- dashboard row labels


def _batch_row(**overrides) -> BatchPositionProgressModel:
    defaults = dict(
        id="bp-1",
        label="orali_1",
        exposure_count=5,
        tilt_series=5,
        complete=5,
        warning=0,
        failed=0,
        queued=0,
        status="complete",
        tooltip="orali_1",
    )
    defaults.update(overrides)
    return BatchPositionProgressModel(**defaults)


def test_healthy_batch_row_chip_drops_the_zero_failure_count() -> None:
    """"5 complete · 0 failed" was noise and squeezed out the row name."""

    assert _batch_outcome_chip_parts(_batch_row()) == ["5 complete"]


def test_failed_batch_row_chip_drops_the_zero_complete_count() -> None:
    parts = _batch_outcome_chip_parts(_batch_row(complete=0, failed=2, status="failed"))
    assert parts == ["2 failed"]


def test_mixed_batch_row_chip_keeps_every_non_zero_outcome() -> None:
    parts = _batch_outcome_chip_parts(_batch_row(complete=3, failed=1, warning=2))
    assert parts == ["3 complete", "1 failed", "2 partial"]


def test_empty_batch_row_still_shows_a_count() -> None:
    """Never render a blank chip."""

    assert _batch_outcome_chip_parts(_batch_row(complete=0)) == ["0 complete"]


@pytest.mark.parametrize(
    "minimum",
    [_ROW_LABEL_MIN_WIDTH, _ROW_LABEL_MIN_WIDTH_COMPACT, _WARNING_LABEL_MIN_WIDTH],
)
def test_row_label_floors_fit_more_than_an_ellipsis(minimum: int) -> None:
    """At ~9pt Segoe UI a character is roughly 6-7px wide.

    The shipped behaviour gave these labels no floor at all, so they
    collapsed to one glyph plus an ellipsis. Anything under ~60px would
    still be unreadable.
    """

    assert minimum >= 60


def test_compact_label_floor_is_narrower_than_the_single_column_floor() -> None:
    assert _ROW_LABEL_MIN_WIDTH_COMPACT < _ROW_LABEL_MIN_WIDTH
