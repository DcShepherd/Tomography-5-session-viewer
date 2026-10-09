"""Sections exposures report components."""
from __future__ import annotations
import io
import math
from pathlib import Path
from typing import Sequence
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Paragraph, Spacer
from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.domain.units import DEGREE
from tomography_session_browser.domain.models import Sample
from tomography_session_browser.parsers.mrc_parser import read_mrc_display_region, read_mrc_metadata
from tomography_session_browser.parsers.tilt_thumbnail_parser import find_tilt_series_thumbnails
from tomography_session_browser.reports import graphics, styles
from tomography_session_browser.reports.pdf_export import PORTRAIT_USABLE_W
from tomography_session_browser.services.tilt_thumbnails import is_lamella_session
from tomography_session_browser.presentation.dashboard.models import ExposureThumbnailModel
from tomography_session_browser.presentation.dashboard.sections.metrics import exposure_thumbnail_model
from tomography_session_browser.reports.constants import EXPOSURE_COLUMNS, EXPOSURE_GUTTER, EXPOSURE_JPEG_QUALITY, EXPOSURE_MAX_ASPECT, EXPOSURE_OWN_ANGLES, EXPOSURE_PRINT_DPI, EXPOSURE_ROW_GAP
from tomography_session_browser.reports.context import SessionContext
from tomography_session_browser.reports.layout import figure_row, headed_block, meta_row
from tomography_session_browser.reports.sections.tilt_table import TiltRow, tilt_row


def exposure_thumbnail_section(
    sample: Sample,
    ctx: SessionContext,
    generation_warnings: list[str],
    *,
    anchor: str,
) -> list:
    """Each tilt series' exposure image, four to a page, with its frame's metadata.

    The image nearest 0°, or in a lamella session the one nearest the
    pretilt (the angle a tilt series started from). What every image shares
    (the angle, what Cam. dose means) is said once, under the heading; each
    image carries only its own values and any exception. In tilt-table order.
    A tilt series without a thumbnail, without recorded angles to choose an
    image, or whose image cannot be read is named in a note rather than left
    out silently.
    """

    lamella = is_lamella_session(sample.metadata.get("Session.dm") or ctx.session.metadata_summary.get("Session.dm"))
    cell_w = (PORTRAIT_USABLE_W - EXPOSURE_GUTTER * (EXPOSURE_COLUMNS - 1)) / EXPOSURE_COLUMNS
    thumbnails = find_tilt_series_thumbnails(sample.tilt_series)
    models: list[ExposureThumbnailModel] = []
    cells: list = []
    missing: list[str] = []
    unplaced: list[str] = []
    unreadable: list[str] = []
    for tilt in sample.tilt_series:
        name = tilt.name or tilt.id
        path = thumbnails.get(tilt.id)
        if path is None:
            missing.append(name)
            continue
        model = exposure_thumbnail_model(tilt, read_mrc_metadata(path), lamella=lamella)
        if model is None:
            unplaced.append(name)
            continue
        image = exposure_image(path, model, cell_w)
        if image is None:
            unreadable.append(name)
            continue
        models.append(model)
        cells.append(exposure_cell(model, image, tilt_row(tilt, ctx), cell_w))

    if unreadable:
        generation_warnings.append(
            f"{sample.name}: the exposure thumbnail could not be read for {', '.join(unreadable)}."
        )
    if not cells and missing:
        generation_warnings.append(f"{sample.name}: no Thumbnails stacks were found beside its tilt series.")

    rows: list = []
    for start in range(0, len(cells), EXPOSURE_COLUMNS):
        if rows:
            rows.append(Spacer(1, EXPOSURE_ROW_GAP))
        rows.append(figure_row(cells[start:start + EXPOSURE_COLUMNS], cell_w, EXPOSURE_GUTTER, columns=EXPOSURE_COLUMNS))
    borrowed = [model.tilt_series_name for model in models if model.angle_source != EXPOSURE_OWN_ANGLES]
    notes = [
        Paragraph(graphics.html_escape(text), styles.NOTE_STYLE)
        for text in (
            names_note("No thumbnail stack", missing),
            names_note("No recorded tilt angles to choose an image", unplaced),
            names_note("Thumbnail could not be read", unreadable),
            names_note("Image chosen by the tilt series' own angles, as its thumbnail records none", borrowed),
        )
        if text
    ]
    if notes:
        notes.insert(0, Spacer(1, 6))
    total = count_phrase(len(sample.tilt_series), "tilt series", "tilt series")
    return headed_block(
        "Exposures",
        [*rows, *notes],
        lead=exposure_lead(models, lamella=lamella) if rows else [],
        anchor=anchor,
        meta=f"{len(cells):,} of {total}",
    )


def exposure_lead(models: Sequence[ExposureThumbnailModel], *, lamella: bool) -> list:
    """What every image in the section shares, said once: angle, specimen, Cam. dose."""

    items: list[tuple[str, str | None]] = []
    if lamella:
        # The images are at the pretilt, so it is their stage tilt too.
        items.append(("Specimen", "lamella"))
        pretilts = [model.reference_angle for model in models if not model.pretilt_unknown]
        if pretilts:
            items.append(("Pretilt", angle_span(pretilts)))
    else:
        # The stage tilt of the images at 0°; any other says so itself.
        at_reference = [model.tilt_angle for model in models if not model.misses_reference]
        if at_reference:
            items.append(("Stage tilt", angle_span(at_reference)))
    which = (
        "the one nearest its pretilt, the angle the tilt series started from"
        if lamella
        else f"the one nearest 0{DEGREE}"
    )
    text = (
        f"One image per tilt series, from its Thumbnails stack: {which}. Cam. dose is the dose the "
        f"camera recorded for that one image ({electrons()}/Å²), not the tilt series' accumulated dose."
    )
    lead: list = [meta_row(items), Spacer(1, 2)] if items else []
    return [*lead, Paragraph(graphics.html_escape(text), styles.MUTED_STYLE), Spacer(1, 6)]


def angle_span(values: Sequence[float]) -> str:
    low, high = f"{min(values):.2f}", f"{max(values):.2f}"
    return f"{low}{DEGREE}" if low == high else f"{low}{DEGREE} to {high}{DEGREE}"


def electrons() -> str:
    # Helvetica, the fallback face, has no superscript minus.
    return "e⁻" if styles.has_unicode_fonts() else "e"


def names_note(reason: str, names: Sequence[str]) -> str | None:
    if not names:
        return None
    return f"{reason}: {', '.join(names)}."


def exposure_image(path: Path, model: ExposureThumbnailModel, cell_w: float) -> graphics.FramedImage | None:
    """The chosen frame rendered as the viewer shows it, sized to the column."""

    nx, ny = model.image_size or (4, 3)
    scale = min(cell_w / nx, cell_w * EXPOSURE_MAX_ASPECT / ny)
    width, height = nx * scale, ny * scale
    rendered = read_mrc_display_region(
        path,
        slice_index=model.frame_index,
        max_size=math.ceil(max(width, height) / 72.0 * EXPOSURE_PRINT_DPI),
    )
    if rendered is None:
        return None
    # The frame's own aspect, in case the header and the data disagree.
    image_w, image_h = rendered.image.size
    scale = min(cell_w / image_w, cell_w * EXPOSURE_MAX_ASPECT / image_h)
    buffer = io.BytesIO()
    rendered.image.save(buffer, "JPEG", quality=EXPOSURE_JPEG_QUALITY)
    return graphics.FramedImage(
        ImageReader(io.BytesIO(buffer.getvalue())), width=image_w * scale, height=image_h * scale,
    )


def exposure_metadata(model: ExposureThumbnailModel) -> list[tuple[str, str]]:
    """``(label, value)`` lines under a thumbnail: only what is this image's own.

    The angle is the section's, stated once under its heading; an image whose
    angle differs says so in an exception line instead.
    """

    dose = (
        f"{model.camera_dose_e_per_angstrom2:.2f} {electrons()}/Å²"
        if model.camera_dose_e_per_angstrom2 is not None
        else "n/a"
    )
    stage = (
        f"{model.stage_um[0]:.1f}, {model.stage_um[1]:.1f} µm" if model.stage_um is not None else "n/a"
    )
    return [
        ("Acquired", model.acquisition_time or "n/a"),
        ("Cam. dose", dose),
        ("Stage X, Y", stage),
    ]


def exposure_exceptions(model: ExposureThumbnailModel) -> list[str]:
    """What sets this image apart from the section's shared angle, if anything."""

    lines: list[str] = []
    if model.pretilt_unknown:
        lines.append(f"Pretilt not recorded (no MDOC start angle); the image nearest 0{DEGREE} is shown.")
    if model.misses_reference:
        target = f"0{DEGREE}" if not model.lamella or model.pretilt_unknown else f"the {model.reference_angle:.2f}{DEGREE} pretilt"
        lines.append(
            f"No image at {target}; nearest at {model.tilt_angle:.2f}{DEGREE} "
            f"(frame {model.frame_index + 1} of {model.frame_count})."
        )
    return lines


def exposure_cell(model: ExposureThumbnailModel, image: graphics.FramedImage, row: TiltRow, cell_w: float) -> list:
    """A thumbnail under its tilt series' name and status, its own metadata beneath."""

    mark = graphics.status_mark(row.status_label, row.tone)
    mark_w = mark.wrap(cell_w, 100)[0]
    title = graphics.plain_table(
        [[Paragraph(f"<b>{graphics.html_escape(model.tilt_series_name)}</b>", styles.SMALL_STYLE), mark]],
        [cell_w - mark_w, mark_w],
        extra_style=[("ALIGN", (1, 0), (1, 0), "RIGHT")],
    )
    lines = exposure_metadata(model)
    key_w = max(
        styles.string_width(label.upper(), styles.SANS_SEMIBOLD, styles.TYPE_CAPTION) for label, _value in lines
    ) + 8.0
    details = graphics.plain_table(
        [
            [
                Paragraph(graphics.html_escape(label), styles.KEY_STYLE),
                Paragraph(graphics.html_escape(value), styles.MUTED_STYLE if value == "n/a" else styles.SMALL_STYLE),
            ]
            for label, value in lines
        ],
        [key_w, cell_w - key_w],
        valign="TOP",
        extra_style=[("BOTTOMPADDING", (0, 0), (-1, -1), 1.2)],
    )
    exceptions = [Paragraph(graphics.html_escape(text), styles.WARNING_STYLE) for text in exposure_exceptions(model)]
    return [title, Spacer(1, 3), image, Spacer(1, 4), details, *exceptions]

