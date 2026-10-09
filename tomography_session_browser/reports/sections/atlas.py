"""Sections atlas report components."""
from __future__ import annotations
import io
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
import numpy as np
from PIL import Image as PILImage
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Paragraph, Spacer
from tomography_session_browser.domain.markers import ImageMarker
from tomography_session_browser.domain.models import Atlas, BatchPosition, Overview, SearchMap, TiltSeries
from tomography_session_browser.parsers.mrc_parser import read_mrc_display_region
from tomography_session_browser.reports import graphics, styles
from tomography_session_browser.reports.pdf_export import PORTRAIT_USABLE_H, PORTRAIT_USABLE_W
from tomography_session_browser.services.markers.builders import atlas_lod_markers
from tomography_session_browser.services.markers.context import MarkerContext
from tomography_session_browser.services.atlas_lod import ATLAS_LABEL_MIN_ZOOM, atlas_batch_view
from tomography_session_browser.services.marker_clustering import MAX_ATLAS_ZOOM
from tomography_session_browser.services.atlas_metadata import atlas_projection_mismatch
from tomography_session_browser.reports.constants import ATLAS_DETAIL_MARGIN_DP, ATLAS_DETAIL_MAX_ZOOM, ATLAS_MRC_MATCH, ATLAS_PRINT_DPI, FIGURE_GUTTER, LOGGER
from tomography_session_browser.reports.images import embeddable_image_path, resolve_overlay_geometry
from tomography_session_browser.reports.layout import figure_cell, figure_row


def atlas_image_block(
    atlas: Atlas,
    *,
    label: str,
    generation_warnings: list[str],
    scope_overviews: Sequence[Overview] = (),
    scope_search_maps: Sequence[SearchMap] = (),
    scope_batch_positions: Sequence[BatchPosition] = (),
    scope_tilt_series: Sequence[TiltSeries] = (),
    failed_tilt_ids: frozenset[str] = frozenset(),
) -> list:
    """The dataset's atlas with batch-position status only, as the Atlas tab shows it.

    Two views follow the tab's zoom rule (:mod:`services.atlas_lod`): the
    whole atlas at fit zoom, where nearby positions merge into cluster rings
    and numbers are hidden, and a crop around this dataset's positions at a
    zoom of at least 2x, where they separate and are numbered. Overview and
    search-map outlines are left out.
    """

    embed = embeddable_image_path(atlas.image_path)
    if embed is None:
        text = f"{graphics.html_escape(label)} — atlas image not available."
        return [Paragraph(text, styles.NOTE_STYLE)]

    try:
        ctx_markers = MarkerContext(
            overviews=tuple(scope_overviews),
            search_maps=tuple(scope_search_maps),
            batch_positions=tuple(scope_batch_positions),
            tilt_series=tuple(scope_tilt_series),
            failed_tilt_ids=failed_tilt_ids,
        )
        raw_markers = atlas_lod_markers(
            atlas,
            ctx_markers,
            include_detail_markers=False,
        )
    except Exception as exc:  # pragma: no cover — defensive
        LOGGER.debug("Atlas marker computation failed: %s", exc)
        raw_markers = []
    native, markers = resolve_overlay_geometry(atlas, raw_markers, embed)
    batch_markers, unattributed = atlas_status_markers(markers)

    # Sized so the atlas still fits under a dataset's summary cards when the
    # dataset has up to eight search maps (four completion rows).
    cell_w = (PORTRAIT_USABLE_W - FIGURE_GUTTER) / 2
    whole_scale = min(cell_w / native[0], PORTRAIT_USABLE_H * 0.36 / native[1])
    whole_region = (0.0, 0.0, float(native[0]), float(native[1]))

    # The atlas MRC renders both views sharply; the JPG is the fallback.
    mrc = atlas_mrc_source(atlas, embed, native)

    def raster(region: tuple[float, float, float, float], scale: float) -> graphics.RasterCrop | None:
        return atlas_raster(mrc, region, scale) if mrc is not None else None

    if reason := atlas_projection_mismatch(atlas):
        figure = graphics.AtlasStatusFigure(
            embed, native_size=native, view=None, region=whole_region, scale=whole_scale,
            raster=raster(whole_region, whole_scale),
        )
        caption = f"{graphics.html_escape(label)}. Atlas markers unavailable: {graphics.html_escape(reason)}"
        return [graphics.keep_with_caption([figure], caption)]

    details = atlas_detail_regions([*batch_markers, *unattributed], native, whole_scale)
    tags = [chr(ord("A") + index) if len(details) > 1 else None for index in range(len(details))]
    whole = graphics.AtlasStatusFigure(
        embed,
        native_size=native,
        view=atlas_batch_view(
            batch_markers, unattributed,
            screen_scale=whole_scale / graphics.PT_PER_DP, logical_zoom=1.0,
        ),
        region=whole_region,
        scale=whole_scale,
        detail_regions=[(region, tag) for (region, _zoom), tag in zip(details, tags)],
        raster=raster(whole_region, whole_scale),
    )
    source_size = mrc.frame_size if mrc is not None else native
    size_text = f"{source_size[0]} × {source_size[1]} px"
    cells = [figure_cell(whole, "Whole atlas", f"{size_text} · fit zoom", cell_w, inline=True)]
    for (region, zoom), tag in zip(details, tags):
        detail_scale = whole_scale * zoom
        detail_view = atlas_batch_view(
            batch_markers, unattributed,
            screen_scale=detail_scale / graphics.PT_PER_DP, logical_zoom=zoom,
        )
        figure = graphics.AtlasStatusFigure(
            embed, native_size=native, view=detail_view, region=region, scale=detail_scale,
            name_clusters=True, raster=raster(region, detail_scale),
        )
        where = f"region {tag}" if tag else "boxed region"
        cells.append(figure_cell(figure, "Batch positions", f"{where} · {zoom:g}× zoom", cell_w, inline=True))

    # One line: where the atlas came from is already in the dataset header.
    if not details:
        caption = f"{graphics.html_escape(label)}: none of this dataset's batch positions could be placed on this atlas."
    else:
        caption = (
            f"{graphics.html_escape(label)}: this dataset's batch positions only, as the Atlas tab shows them "
            "at each zoom; a ring counts positions too close to tell apart."
        )
    figure_rows: list = []
    for start in range(0, len(cells), 2):
        if figure_rows:
            figure_rows.append(Spacer(1, 8))
        figure_rows.append(figure_row(cells[start:start + 2], cell_w, FIGURE_GUTTER, columns=2))
    return [graphics.keep_with_caption(
        [*figure_rows, Spacer(1, 3), graphics.AtlasMarkerPrintLegend()],
        caption,
    )]


@dataclass(frozen=True, slots=True)
class AtlasMrc:
    path: Path
    frame_size: tuple[int, int]
    scale: tuple[float, float]  # MRC pixels per embedded-image pixel, x and y


def atlas_mrc_source(atlas: Atlas, embed: Path, native: tuple[int, int]) -> AtlasMrc | None:
    """The atlas MRC, if the embedded JPG is a straight downscale of it.

    Markers are projected onto the JPG, so the MRC may replace it only when
    one maps onto the other by scaling alone: the same aspect and the same
    picture the same way up. Anything else keeps the JPG.
    """

    path = atlas.image_path
    if path is None:
        return None
    mrc = path if path.suffix.lower() == ".mrc" else path.with_suffix(".mrc")
    if mrc == embed or not mrc.exists():
        return None
    thumbnail = read_mrc_display_region(mrc, max_size=256)
    if thumbnail is None:
        return None
    nx, ny = thumbnail.frame_size
    scale_x, scale_y = nx / native[0], ny / native[1]
    if abs(scale_x - scale_y) > 0.01 * max(scale_x, scale_y):
        return None
    try:
        with PILImage.open(embed) as image:
            embedded = image.convert("L").resize(thumbnail.image.size, PILImage.Resampling.BILINEAR)
    except OSError:
        return None
    if normalised_correlation(np.asarray(thumbnail.image), np.asarray(embedded)) < ATLAS_MRC_MATCH:
        LOGGER.info("Atlas MRC %s does not match %s; using the JPG.", mrc.name, embed.name)
        return None
    return AtlasMrc(mrc, (nx, ny), (scale_x, scale_y))


def normalised_correlation(first: np.ndarray, second: np.ndarray) -> float:
    a = first.astype(np.float32) - float(first.mean())
    b = second.astype(np.float32) - float(second.mean())
    denominator = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / denominator) if denominator > 0 else 0.0


def atlas_raster(
    mrc: AtlasMrc,
    region: tuple[float, float, float, float],
    scale: float,
) -> graphics.RasterCrop | None:
    """``region`` (embedded-image pixels) rendered from the MRC at print resolution.

    ``scale`` is the figure's points per embedded-image pixel. The budget is
    the figure's own longest drawn side, since ``max_size`` caps the longest
    side: sizing every crop by the whole atlas's width left a tall or larger
    detail crop under the print resolution.
    """

    scale_x, scale_y = mrc.scale
    x, y, width, height = region
    box = (
        math.floor(x * scale_x),
        math.floor(y * scale_y),
        math.ceil((x + width) * scale_x),
        math.ceil((y + height) * scale_y),
    )
    longest_pt = max(width, height) * scale
    rendered = read_mrc_display_region(
        mrc.path, box, max_size=math.ceil(longest_pt / 72.0 * ATLAS_PRINT_DPI),
    )
    if rendered is None:
        return None
    buffer = io.BytesIO()
    rendered.image.save(buffer, "JPEG", quality=90)
    x0, y0, x1, y1 = rendered.box
    return graphics.RasterCrop(
        ImageReader(io.BytesIO(buffer.getvalue())),
        (x0 / scale_x, y0 / scale_y, x1 / scale_x, y1 / scale_y),
    )


def atlas_status_markers(markers: Sequence[ImageMarker]) -> tuple[list[ImageMarker], list[ImageMarker]]:
    """Batch-position leaves and unattributed failures; nothing else is drawn."""

    placed = [
        marker for marker in markers
        if marker.visible and marker.x is not None and marker.y is not None
    ]
    batch = [marker for marker in placed if marker.metadata.get("atlas_lod_role") == "batch_position"]
    unattributed = [marker for marker in placed if marker.metadata.get("atlas_lod_role") == "unattributed"]
    return batch, unattributed


def atlas_detail_regions(
    markers: Sequence[ImageMarker],
    native: tuple[int, int],
    whole_scale: float,
) -> list[tuple[tuple[float, float, float, float], float]]:
    """Crops and zooms that show every one of this dataset's positions numbered.

    A crop has the atlas's aspect at ``zoom`` times the fit scale, the same
    relation the Atlas tab's zoom has to its fit view. Positions are grouped
    so each group fits one crop at 2x with room for its glyphs and numbers
    (a margin may stop at the image edge); each group then gets the largest
    zoom in [2, 4], in quarter steps, that still holds it. If positions there
    still share a ring, the zoom rises toward the tab's maximum to the first
    step that separates them, as long as the crop still holds the group.
    """

    nw, nh = float(native[0]), float(native[1])

    def needed(group: Sequence[ImageMarker], zoom: float) -> tuple[float, float, float, float]:
        dp_to_px = graphics.PT_PER_DP / (whole_scale * zoom)
        margin_left, margin_top, margin_right, margin_bottom = (value * dp_to_px for value in ATLAS_DETAIL_MARGIN_DP)
        xs = [float(marker.x) for marker in group]
        ys = [float(marker.y) for marker in group]
        return (
            max(min(xs) - margin_left, 0.0),
            max(min(ys) - margin_top, 0.0),
            min(max(xs) + margin_right, nw),
            min(max(ys) + margin_bottom, nh),
        )

    def fits(group: Sequence[ImageMarker], zoom: float) -> bool:
        left, top, right, bottom = needed(group, zoom)
        return right - left <= nw / zoom + 1e-6 and bottom - top <= nh / zoom + 1e-6

    def region_for(group: Sequence[ImageMarker], zoom: float) -> tuple[float, float, float, float]:
        left, top, right, bottom = needed(group, zoom)
        width, height = nw / zoom, nh / zoom
        x = min(max((left + right) / 2 - width / 2, 0.0), nw - width)
        y = min(max((top + bottom) / 2 - height / 2, 0.0), nh - height)
        return x, y, width, height

    groups: list[list[ImageMarker]] = []
    for marker in sorted(markers, key=lambda value: (float(value.y), float(value.x), value.id)):
        for group in groups:
            if fits([*group, marker], ATLAS_LABEL_MIN_ZOOM):
                group.append(marker)
                break
        else:
            groups.append([marker])

    def separated(group: Sequence[ImageMarker], zoom: float) -> bool:
        batch = [marker for marker in group if marker.metadata.get("atlas_lod_role") == "batch_position"]
        view = atlas_batch_view(batch, screen_scale=whole_scale * zoom / graphics.PT_PER_DP, logical_zoom=zoom)
        return not view.clusters

    regions: list[tuple[tuple[float, float, float, float], float]] = []
    for group in groups:
        zoom = ATLAS_DETAIL_MAX_ZOOM
        while zoom > ATLAS_LABEL_MIN_ZOOM and not fits(group, zoom):
            zoom -= 0.25
        if not separated(group, zoom):
            candidate = zoom + 0.25
            while candidate <= MAX_ATLAS_ZOOM and fits(group, candidate):
                if separated(group, candidate):
                    zoom = candidate
                    break
                candidate += 0.25
        regions.append((region_for(group, zoom), zoom))
    return regions

