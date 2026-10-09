"""Sections settings report components."""
from __future__ import annotations
from typing import Mapping, Sequence
from reportlab.platypus import Paragraph, Table
from tomography_session_browser.domain.units import ANGSTROM_PER_PIXEL, DEGREE
from tomography_session_browser.domain.models import BatchPosition, TiltSeries
from tomography_session_browser.reports import graphics, styles
from tomography_session_browser.services.acquisition_metadata import ACQUISITION_SPOT_LABEL, SEARCH_SPOT_LABEL, format_target_defocus_values
from tomography_session_browser.services.tilt_series_validation import TiltSeriesValidation
from tomography_session_browser.reports.constants import CARD_INNER_W, SETTINGS_CELL_PAD, SETTINGS_COLUMNS, TEXT_FIT
from tomography_session_browser.reports.context import lookup_validations
from tomography_session_browser.reports.formatting import collect_expected_counts, collect_increments, collect_observed_counts, format_count_set, format_pixel_size, format_set, report_acquisition_setting, report_spot_size, section_floats, section_strings, total_acquisition_duration
from tomography_session_browser.reports.layout import longest_word, shrink_widths, spread_widths


def acquisition_settings_rows(
    tilt_series: Sequence[TiltSeries],
    batch_positions: Sequence[BatchPosition] = (),
    validations: Mapping[str, TiltSeriesValidation] | None = None,
) -> list[tuple[str, str]]:
    """``(label, value)`` for each acquisition setting, in display order."""

    validation_lookup = lookup_validations(tilt_series, validations)
    pixel_sizes = sorted({t.pixel_size for t in tilt_series if t.pixel_size is not None})
    original = sorted({t.original_pixel_size for t in tilt_series if t.original_pixel_size is not None})
    binnings = sorted({t.binning for t in tilt_series if t.binning is not None})
    target_def = [t.target_defocus for t in tilt_series if t.target_defocus is not None]

    tilt_ranges = [t.tilt_range for t in tilt_series if t.tilt_range is not None]
    tilt_lo = min((r[0] for r in tilt_ranges), default=None)
    tilt_hi = max((r[1] for r in tilt_ranges), default=None)

    increments = collect_increments(tilt_series)
    expected = collect_expected_counts(tilt_series, validation_lookup)
    observed = collect_observed_counts(tilt_series, validation_lookup)

    magnifications = sorted({
        v
        for t in tilt_series
        for v in section_floats(t, "Magnification")
    })
    exposures = sorted({
        v
        for t in tilt_series
        for v in section_floats(t, "ExposureTime")
    })
    detectors = sorted({
        v
        for t in tilt_series
        for v in section_strings(t, "CameraName")
    })
    duration = total_acquisition_duration(tilt_series)
    detector_text = report_acquisition_setting(
        batch_positions,
        "Detector",
        fallback=detectors,
    )

    return [
        ("Stack pixel size", format_pixel_size(pixel_sizes, ANGSTROM_PER_PIXEL)),
        ("Original pixel", format_pixel_size(original, ANGSTROM_PER_PIXEL) if original else "n/a"),
        ("Binning", format_set(binnings, suffix="×")),
        ("Magnification", format_set([f"{v:g}" for v in magnifications])),
        ("Target defocus", format_target_defocus_values(target_def)),
        (
            "Tilt range",
            f"{tilt_lo:g}{DEGREE} to {tilt_hi:g}{DEGREE}"
            if tilt_lo is not None and tilt_hi is not None
            else "n/a",
        ),
        ("Tilt increment", format_set([f"{v:g}{DEGREE}" for v in increments])),
        ("Expected images", format_count_set(expected)),
        ("Observed images", format_count_set(observed)),
        ("Exposure time", format_set([f"{v:g} s" for v in exposures])),
        ("Detector", detector_text),
        (SEARCH_SPOT_LABEL, report_acquisition_setting(batch_positions, SEARCH_SPOT_LABEL)),
        (ACQUISITION_SPOT_LABEL, report_spot_size(tilt_series, batch_positions)),
        ("Probe mode", report_acquisition_setting(batch_positions, "Probe mode")),
        ("Acquisition duration", duration),
    ]


def compute_settings_widths(rows: Sequence[tuple[str, str]], width: float) -> list[float]:
    """Settings-grid column widths summing to ``width``.

    The tilt table's rule: each column is as wide as its widest caption or
    readout and the room to spare becomes equal gaps. The columns are
    left-aligned, so each owns the gap after it and the last ends at the
    card's edge. Too little room wraps captions and readouts between words.
    """

    pad = SETTINGS_CELL_PAD
    value_size = styles.VALUE_STYLE.fontSize
    natural: list[float] = []
    narrowest: list[float] = []
    for column in range(SETTINGS_COLUMNS):
        cells = rows[column::SETTINGS_COLUMNS]
        keys = [label.upper() for label, _value in cells]
        values = [value for _label, value in cells]
        pads = (pad if column else 0.0) + (pad if column < SETTINGS_COLUMNS - 1 else 0.0)
        widest = max(
            [styles.string_width(key, styles.SANS_SEMIBOLD, styles.TYPE_CAPTION) for key in keys]
            + [styles.string_width(value, styles.MONO, value_size) for value in values]
            + [0.0]
        )
        word = max(
            longest_word(keys, styles.SANS_SEMIBOLD, styles.TYPE_CAPTION),
            longest_word(values, styles.MONO, value_size),
        )
        natural.append(widest + TEXT_FIT + pads)
        narrowest.append(word + TEXT_FIT + pads)
    if sum(natural) <= width:
        return spread_widths(natural, width, [1.0] * (SETTINGS_COLUMNS - 1) + [0.0])
    return shrink_widths(natural, narrowest, width)


def acquisition_settings_table(
    tilt_series: Sequence[TiltSeries],
    batch_positions: Sequence[BatchPosition] = (),
    validations: Mapping[str, TiltSeriesValidation] | None = None,
    *,
    width: float = CARD_INNER_W,
) -> Table:
    """Acquisition settings as the dashboard's metric grid.

    Each setting is an uppercase caption over a monospace readout, five to a
    line, like the Collection health card. A missing value reads "n/a" in the
    muted colour so it is visibly an absence rather than a value.
    """

    rows = acquisition_settings_rows(tilt_series, batch_positions, validations)
    data: list[list] = []
    style: list = [
        ("LEFTPADDING", (0, 0), (-1, -1), SETTINGS_CELL_PAD),
        ("RIGHTPADDING", (0, 0), (-1, -1), SETTINGS_CELL_PAD),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
        ("RIGHTPADDING", (-1, 0), (-1, -1), 0),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    for start in range(0, len(rows), SETTINGS_COLUMNS):
        chunk = rows[start:start + SETTINGS_COLUMNS]
        chunk = list(chunk) + [("", "")] * (SETTINGS_COLUMNS - len(chunk))
        line = len(data)
        data.append([Paragraph(graphics.html_escape(label), styles.KEY_STYLE) if label else "" for label, _value in chunk])
        data.append([
            Paragraph(graphics.html_escape(value), styles.VALUE_MUTED_STYLE if value == "n/a" else styles.VALUE_STYLE) if label else ""
            for label, value in chunk
        ])
        style.extend([
            ("TOPPADDING", (0, line), (-1, line), 0 if line == 0 else 6),
            ("BOTTOMPADDING", (0, line), (-1, line), 1),
            ("TOPPADDING", (0, line + 1), (-1, line + 1), 0),
            ("BOTTOMPADDING", (0, line + 1), (-1, line + 1), 0),
        ])
    return Table(data, colWidths=compute_settings_widths(rows, width), style=style, hAlign="LEFT")

