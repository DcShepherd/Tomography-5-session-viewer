"""Sections overviews report components."""
from __future__ import annotations
from typing import Sequence
from reportlab.platypus import Paragraph, Spacer
from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.domain.markers import ImageMarker
from tomography_session_browser.domain.models import Overview, Sample
from tomography_session_browser.reports import graphics, styles
from tomography_session_browser.reports.pdf_export import PORTRAIT_USABLE_H
from tomography_session_browser.services.markers.builders import markers_for_object
from tomography_session_browser.services.markers.context import MarkerContext
from tomography_session_browser.reports.constants import FIGURE_CELL_W, FIGURE_GUTTER
from tomography_session_browser.reports.images import embeddable_image_path, note_overlay_failure, report_image_markers, resolve_overlay_geometry
from tomography_session_browser.reports.layout import figure_cell, figure_row


def overview_grid(
    overviews: Sequence[Overview],
    sample: Sample,
    generation_warnings: list[str],
    failed_tilt_ids: frozenset[str] = frozenset(),
    drawn_markers: list[ImageMarker] | None = None,
) -> list:
    flowables: list = []
    cell_w = FIGURE_CELL_W
    cell_h = PORTRAIT_USABLE_H * 0.3

    pending_row: list = []
    for overview in overviews:
        embed = embeddable_image_path(overview.image_path)
        if embed is None:
            continue

        try:
            # The tilt series decide whether a batch was acquired. Without
            # them every position classed as queued, so the report hid or
            # blued acquired exposure areas the viewer shows as acquired.
            raw_markers = markers_for_object(
                overview,
                context=MarkerContext(
                    overviews=(overview,),
                    search_maps=tuple(sample.search_maps),
                    batch_positions=tuple(sample.batch_positions),
                    tilt_series=tuple(sample.tilt_series),
                    failed_tilt_ids=failed_tilt_ids,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - the figure still prints, without overlays
            raw_markers = []
            note_overlay_failure(generation_warnings, f"overview {overview.name}", exc)
        native, markers = resolve_overlay_geometry(overview, report_image_markers(raw_markers), embed)
        if drawn_markers is not None:
            drawn_markers.extend(markers)

        image = graphics.OverlayedImage(
            embed,
            native_size=native,
            markers=markers,
            max_width=cell_w,
            max_height=cell_h,
        )
        detail = (
            f"{native[0]} × {native[1]} px · "
            f"{count_phrase(len(overview.linked_search_map_ids), 'linked search map')}"
        )
        pending_row.append(figure_cell(image, overview.name, detail, cell_w))
        if len(pending_row) == 2:
            flowables.append(figure_row(pending_row, cell_w, FIGURE_GUTTER))
            flowables.append(Spacer(1, 10))
            pending_row = []
    if pending_row:
        flowables.append(figure_row(pending_row, cell_w, FIGURE_GUTTER, columns=2))
        flowables.append(Spacer(1, 10))

    if not flowables:
        flowables.append(
            Paragraph(
                f"No embeddable overview images for {graphics.html_escape(sample.name)}.",
                styles.NOTE_STYLE,
            )
        )
    return flowables

