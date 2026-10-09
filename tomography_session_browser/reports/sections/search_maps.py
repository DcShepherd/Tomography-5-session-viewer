"""Sections search maps report components."""
from __future__ import annotations
import re
from collections import Counter
from typing import Iterable, Sequence
from reportlab.platypus import KeepTogether, Paragraph, Spacer
from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.domain.models import BatchPosition, Sample, SearchMap
from tomography_session_browser.parsers.path_utils import casefold_natural_key
from tomography_session_browser.reports import graphics, styles
from tomography_session_browser.reports.pdf_export import PORTRAIT_USABLE_H, PORTRAIT_USABLE_W
from tomography_session_browser.services.warning_classification import summarise_warnings
from tomography_session_browser.services.entity_links import search_map_batches
from tomography_session_browser.services.markers.builders import markers_for_object
from tomography_session_browser.services.markers.context import MarkerContext
from tomography_session_browser.services.tilt_series_validation import STATUS_COMPLETE, STATUS_FAILED, STATUS_INCOMPLETE, STATUS_UNKNOWN
from tomography_session_browser.presentation.dashboard.sections.batches import inferred_failed_batch_groups
from tomography_session_browser.presentation.dashboard.sections.search_maps import search_map_acquisition_rollup
from tomography_session_browser.reports.constants import DENSE_MARKER_COUNT, FIGURE_CELL_W, FIGURE_GUTTER, LOGGER
from tomography_session_browser.reports.context import SessionContext, successful_tilt_ids
from tomography_session_browser.reports.formatting import short_warning_summary
from tomography_session_browser.reports.images import embeddable_image_path, note_overlay_failure, report_image_markers, resolve_overlay_geometry
from tomography_session_browser.reports.layout import figure_cell, figure_row


def search_map_blocks(
    search_maps: Sequence[SearchMap],
    sample: Sample,
    ctx: SessionContext,
    generation_warnings: list[str],
    drawn_markers: list[ImageMarker] | None = None,
) -> list:
    """Search-map figures, two to a row; a densely marked map gets a full row."""

    flowables: list = []
    pending: list = []
    failed_tilt_ids = frozenset(
        v_id
        for v_id, v in ctx.validations.items()
        if v.status == STATUS_FAILED
    )
    # The same for every map of the grid. Without ``owners``, as before.
    inferred_groups = inferred_failed_batch_groups(sample.batch_positions, sample.tilt_series, ctx.validations)

    def flush() -> None:
        if pending:
            flowables.append(figure_row(list(pending), FIGURE_CELL_W, FIGURE_GUTTER, columns=2))
            flowables.append(Spacer(1, 12))
            pending.clear()

    for index, sm in enumerate(search_maps):
        embed = embeddable_image_path(sm.image_path or sm.mrc_path)
        if embed is None:
            generation_warnings.append(
                f"Search map {sm.name or sm.id} has no embeddable image for the PDF report."
            )
            flush()
            note = f"<b>{graphics.html_escape(sm.name)}</b> — no embeddable image."
            flowables.append(Paragraph(note, styles.NOTE_STYLE))
            flowables.append(Spacer(1, 6))
            continue

        try:
            raw_markers = markers_for_object(
                sm,
                context=MarkerContext(
                    search_maps=(sm,),
                    batch_positions=tuple(sample.batch_positions),
                    tilt_series=tuple(sample.tilt_series),
                    failed_tilt_ids=failed_tilt_ids,
                ),
                include_unresolved=False,
            )
        except Exception as exc:  # noqa: BLE001 - the figure still prints, without overlays
            raw_markers = []
            note_overlay_failure(generation_warnings, f"search map {sm.name or sm.id}", exc)
        native, markers = resolve_overlay_geometry(sm, report_image_markers(raw_markers), embed, generation_warnings)
        if drawn_markers is not None:
            drawn_markers.extend(markers)
        stage_inferred_failed_count = sum(
            1
            for marker in raw_markers
            if marker.marker_type == MarkerType.TILT_SERIES
            and marker.metadata.get("inferred_from_stage") is True
        )

        dense = len(markers) > DENSE_MARKER_COUNT
        cell_w = PORTRAIT_USABLE_W if dense else FIGURE_CELL_W
        overlayed = graphics.OverlayedImage(
            embed,
            native_size=native,
            markers=markers,
            max_width=cell_w,
            max_height=PORTRAIT_USABLE_H * (0.5 if dense else 0.33),
        )

        linked_batches = linked_batch_positions_for_search_map(sm, sample.batch_positions)
        rollup = search_map_acquisition_rollup(
            sm,
            search_maps,
            sample.batch_positions,
            sample.tilt_series,
            ctx.validations,
            # The completion table's input too (divergence-register rule 5), so
            # the rollup's planned/acquired agree with it although the caption
            # does not print them.
            successful_tilt_ids=successful_tilt_ids(ctx.validations),
            inferred_groups=inferred_groups,
        )
        linked_tilt = rollup.tilt_series_ids
        outcome = Counter(
            v.status for tid in linked_tilt if (v := ctx.validations.get(tid)) is not None
        )
        caption = search_map_caption(
            index + 1,
            sm,
            sample,
            native,
            linked_batches,
            generation_warnings,
            inferred_failed_tilt_count=max(stage_inferred_failed_count, rollup.failed_exposure_areas if rollup.failed_batch_groups else 0),
            inferred_failed_batch_count=rollup.failed_batch_groups,
        )
        chips = [
            graphics.Chip(styles.VALIDATION_TONES[status], f"{outcome[status]} {word}")
            for status, word in (
                (STATUS_COMPLETE, "complete"),
                (STATUS_INCOMPLETE, "incomplete"),
                (STATUS_FAILED, "failed"),
                (STATUS_UNKNOWN, "unknown"),
            )
            if outcome[status] or status == STATUS_COMPLETE
        ]
        if not linked_tilt:
            chips = []
        links = (
            f"{count_phrase(len(linked_batches), 'linked batch position')} · "
            f"{count_phrase(len(linked_tilt), 'tilt series', 'tilt series')}"
        )
        extra: list = [Paragraph(caption, styles.MUTED_STYLE)]
        if chips:
            extra.extend([Spacer(1, 1.5), graphics.Chips(chips, size=styles.TYPE_SMALL - 0.4, gap=8)])
        if sm.warnings:
            extra.append(Paragraph(
                f"Warnings: {graphics.html_escape(short_warning_summary(summarise_warnings(sm.warnings)))}",
                styles.WARNING_STYLE,
            ))
        cell = figure_cell(overlayed, sm.name, links, cell_w, extra=extra)
        if dense:
            flush()
            flowables.append(KeepTogether([cell]))
            flowables.append(Spacer(1, 12))
            continue
        pending.append(cell)
        if len(pending) == 2:
            flush()
    flush()
    return flowables


def linked_batch_positions_for_search_map(
    search_map: SearchMap,
    batch_positions: Sequence[BatchPosition],
) -> list[BatchPosition]:
    """Resolve search-map batch links from the parsed metadata model.

    Tomography sessions can carry the association on either side of the
    relationship: ``SearchMap.linked_batch_position_ids`` or
    ``BatchPosition.linked_search_map_id``. Use only those explicit parsed
    links here; do not infer report captions from ordering.
    """

    return sorted(search_map_batches(search_map, batch_positions), key=batch_position_caption_sort_key)


def search_map_caption(
    figure_number: int,
    search_map: SearchMap,
    sample: Sample,
    native_size: tuple[int, int],
    linked_batches: Sequence[BatchPosition],
    generation_warnings: list[str],
    inferred_failed_tilt_count: int = 0,
    inferred_failed_batch_count: int = 0,
) -> str:
    """The figure caption: number, what the overlays show, and the image size.

    The map's name and the dataset are already the figure's title and the
    section's heading, so the caption carries only what they do not.
    """

    overlay_clause = search_map_overlay_caption_clause(
        search_map,
        sample,
        linked_batches,
        generation_warnings,
        inferred_failed_tilt_count=inferred_failed_tilt_count,
        inferred_failed_batch_count=inferred_failed_batch_count,
    )
    return (
        f"Figure {figure_number}. {graphics.html_escape(overlay_clause)} "
        f"{native_size[0]} × {native_size[1]} px."
    )


def search_map_overlay_caption_clause(
    search_map: SearchMap,
    sample: Sample,
    linked_batches: Sequence[BatchPosition],
    generation_warnings: list[str],
    inferred_failed_tilt_count: int = 0,
    inferred_failed_batch_count: int = 0,
) -> str:
    labels = [
        batch_position_caption_label(batch)
        for batch in sorted(linked_batches, key=batch_position_caption_sort_key)
    ]
    labels = dedupe_preserving_order(labels)
    if len(labels) == 1:
        return f"Batch position {labels[0]} and its target areas."
    if len(labels) > 1:
        return f"Batch positions {format_natural_list(labels)} and their target areas."
    if inferred_failed_tilt_count:
        exposure_noun = "exposure area" if inferred_failed_tilt_count == 1 else "exposure areas"
        batch_text = ""
        if inferred_failed_batch_count:
            batch_noun = "batch position group" if inferred_failed_batch_count == 1 else "batch position groups"
            batch_text = f" across {inferred_failed_batch_count} inferred failed {batch_noun}"
        return f"{inferred_failed_tilt_count} inferred failed {exposure_noun}{batch_text}."

    warning = (
        "Search map caption could not resolve associated batch positions for "
        f"{search_map.name or search_map.id} in data collection {sample.name}; "
        "using generic overlay caption."
    )
    LOGGER.warning(warning)
    generation_warnings.append(warning)
    return "Batch positions and target areas; the associated batch positions were not resolved."


def batch_position_caption_label(batch: BatchPosition) -> str:
    label = batch.name or batch.id or "unknown"
    trailing_number = re.search(r"(\d+)(?!.*\d)", label)
    if trailing_number:
        return str(int(trailing_number.group(1)))
    return label


def batch_position_caption_sort_key(batch: BatchPosition) -> tuple[int, tuple[int | str, ...]]:
    label = batch_position_caption_label(batch)
    if label.isdigit():
        return (0, (int(label),))
    return (1, casefold_natural_key(label))


def format_natural_list(values: Sequence[str]) -> str:
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return f"{', '.join(values[:-1])} and {values[-1]}"


def dedupe_preserving_order(values: Iterable[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.casefold()
        if key in seen:
            continue
        unique.append(value)
        seen.add(key)
    return unique

