"""Sections completion report components."""
from __future__ import annotations
from reportlab.platypus import Paragraph, Spacer, Table
from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.domain.models import Sample
from tomography_session_browser.reports import graphics, styles
from tomography_session_browser.presentation.dashboard.sections.batches import inferred_failed_batch_groups
from tomography_session_browser.presentation.dashboard.sections.search_maps import search_map_acquisition_rollup
from tomography_session_browser.reports.constants import CARD_INNER_W, COMPLETION_COLUMN_GAP
from tomography_session_browser.reports.context import SessionContext, successful_tilt_ids


def search_map_completion_block(sample: Sample, ctx: SessionContext, *, width: float = CARD_INNER_W) -> Table:
    """One completion row per search map, two to a line.

    The numbers are *exposure-area* progress (planned vs. acquired
    exposures across the linked batch positions of each search map),
    sourced from :func:`search_map_acquisition_rollup` so the dashboard and
    the report read the same value. Failed exposure areas get their own
    line under the bar, so "6 / 9" never reads as the whole story.
    """

    # The same for every map of the grid. Without ``owners``, as before.
    inferred_groups = inferred_failed_batch_groups(sample.batch_positions, sample.tilt_series, ctx.validations)

    entries: list[list] = []
    for sm in sample.search_maps:
        rollup = search_map_acquisition_rollup(
            sm,
            sample.search_maps,
            sample.batch_positions,
            sample.tilt_series,
            ctx.validations,
            successful_tilt_ids=successful_tilt_ids(ctx.validations),
            inferred_groups=inferred_groups,
        )
        if rollup.planned == 0 and rollup.acquired == 0 and rollup.failed_exposure_areas == 0:
            continue
        entries.append([sm.name or sm.id, rollup])

    if not entries:
        return graphics.plain_table([[Paragraph("No search map completion data.", styles.MUTED_STYLE)]], [width])

    columns = 2 if len(entries) > 1 else 1
    cell_w = (width - COMPLETION_COLUMN_GAP * (columns - 1)) / columns
    cells = [completion_cell(name, rollup, cell_w) for name, rollup in entries]
    rows = [cells[index:index + columns] for index in range(0, len(cells), columns)]
    rows[-1].extend([""] * (columns - len(rows[-1])))
    return Table(
        rows,
        colWidths=[cell_w + COMPLETION_COLUMN_GAP] * (columns - 1) + [cell_w],
        hAlign="LEFT",
        style=[
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-2, -1), COMPLETION_COLUMN_GAP),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ],
    )


def completion_cell(name: str, rollup, width: float) -> list:
    ratio_w = 46.0
    header = graphics.plain_table(
        [[
            Paragraph(graphics.html_escape(graphics.fit_text(name, styles.SANS, styles.TYPE_SMALL, width - ratio_w - 4)), styles.SMALL_STYLE),
            Paragraph(f"{rollup.acquired} / {rollup.planned}", styles.MONO_RIGHT_STYLE),
        ]],
        [width - ratio_w, ratio_w],
        valign="BOTTOM",
    )
    cell: list = [
        header,
        Spacer(1, 2.5),
        graphics.CompletionBar(
            acquired=rollup.acquired,
            planned=rollup.planned,
            failed=rollup.failed_exposure_areas,
            width=width,
        ),
    ]
    failed_bits: list[str] = []
    if rollup.failed_exposure_areas:
        failed_bits.append(count_phrase(rollup.failed_exposure_areas, "failed exposure area"))
    if rollup.failed_batch_groups:
        failed_bits.append(count_phrase(rollup.failed_batch_groups, "failed batch position group"))
    if failed_bits:
        cell.append(Spacer(1, 2.5))
        cell.append(Paragraph(
            f'<font color="{styles.TONES["bad"].ink_hex}">{" · ".join(failed_bits)}</font>',
            styles.SMALL_STYLE,
        ))
    return cell

