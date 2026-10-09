"""Sections atlas overview report components."""
from __future__ import annotations
from typing import Mapping, Sequence
from reportlab.platypus import PageBreak, Paragraph, Spacer
from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.models import Atlas, Session
from tomography_session_browser.reports import graphics, styles
from tomography_session_browser.reports.pdf_export import PORTRAIT_USABLE_W
from tomography_session_browser.reports.constants import THUMBNAILS_PER_ROW, THUMBNAIL_GUTTER
from tomography_session_browser.reports.context import SessionContext
from tomography_session_browser.reports.formatting import acquisition_window_text, session_kind_label
from tomography_session_browser.reports.images import embeddable_image_path
from tomography_session_browser.reports.layout import figure_cell, figure_row, meta_row


def linked_overview_section(
    contexts: Sequence[SessionContext],
    include_thumbnails: bool,
    generation_warnings: list[str],
    *,
    anchors: Mapping[int, str] | None = None,
) -> list:
    """An overview of each atlas screening session in the export."""

    # Supporting context alone does not create a screening-session section.
    atlas_contexts = [ctx for ctx in contexts if ctx.session.kind == SessionKind.ATLAS_SCREENING]
    if not atlas_contexts:
        return []

    flowables: list = []
    for index, ctx in enumerate(atlas_contexts):
        if index:
            flowables.append(PageBreak())
        anchor = (anchors or {}).get(id(ctx)) or f"atlas-session-{index + 1}"
        flowables.extend(session_overview_block(ctx, include_thumbnails, generation_warnings, anchor=anchor))
    return flowables


def session_overview_block(
    ctx: SessionContext,
    include_thumbnails: bool,
    generation_warnings: list[str],
    *,
    anchor: str | None = None,
) -> list:
    """An atlas screening session: its header, then every grid's atlas."""

    session = ctx.session
    sample_atlases = resolved_sample_atlases(session)
    flowables: list = [
        Paragraph("Atlas-session overview", styles.EYEBROW_STYLE),
        Spacer(1, 2),
        graphics.AnchoredParagraph(
            graphics.html_escape(session.name), styles.SECTION_TITLE_STYLE,
            anchor=anchor, outline=f"Atlas session: {session.name}", level=0,
        ),
        meta_row([
            ("Kind", f"{session_kind_label(session.kind)} session"),
            ("Acquired atlases", f"{len(sample_atlases):,}"),
            ("Acquisition", acquisition_window_text(ctx.dashboard)),
        ]),
        Spacer(1, 10),
    ]

    # The session-level Atlas/ folder typically only holds the .dm metadata;
    # the images live under each SampleN/Atlas/ subfolder.
    if include_thumbnails and sample_atlases:
        flowables.extend(atlas_thumbnail_grid(sample_atlases, generation_warnings))
    elif include_thumbnails:
        flowables.append(Paragraph("No embeddable atlas images on disk for this session.", styles.NOTE_STYLE))
    return flowables


def resolved_sample_atlases(session: Session) -> list[tuple[str, Atlas]]:
    """Return ``(label, atlas)`` for every sample-level atlas in ``session``.

    Skips samples whose Atlas/ folder didn't yield an image (parser
    falls back to ``image_path = None`` in that case). The label is
    the sample's display name so the grid caption identifies which
    grid each thumbnail belongs to.
    """

    items: list[tuple[str, Atlas]] = []
    for sample in session.samples:
        if sample.atlas is None:
            continue
        if embeddable_image_path(sample.atlas.image_path) is None:
            continue
        items.append((sample.name, sample.atlas))
    return items


def atlas_thumbnail_grid(
    atlases: Sequence[tuple[str, Atlas]],
    generation_warnings: list[str],
) -> list:
    """Per-sample atlas thumbnails, four to a row, as a contact sheet."""

    flowables: list = []
    cell_w = (PORTRAIT_USABLE_W - THUMBNAIL_GUTTER * (THUMBNAILS_PER_ROW - 1)) / THUMBNAILS_PER_ROW
    pending_row: list = []

    for label, atlas in atlases:
        embed = embeddable_image_path(atlas.image_path)
        if embed is None:
            continue
        native = graphics.native_image_size(embed)
        if (
            native is None
            and atlas.mrc_metadata is not None
            and atlas.mrc_metadata.nx
            and atlas.mrc_metadata.ny
        ):
            native = (atlas.mrc_metadata.nx, atlas.mrc_metadata.ny)
        if native is None or native[0] <= 0 or native[1] <= 0:
            native = (1000, 1000)

        image = graphics.OverlayedImage(
            embed,
            native_size=native,
            markers=(),  # no overlays at contact-sheet size
            max_width=cell_w,
            max_height=cell_w,
        )
        pending_row.append(figure_cell(image, label, f"{native[0]} × {native[1]} px", cell_w))
        if len(pending_row) == THUMBNAILS_PER_ROW:
            flowables.append(figure_row(pending_row, cell_w, THUMBNAIL_GUTTER))
            flowables.append(Spacer(1, 8))
            pending_row = []

    if pending_row:
        flowables.append(figure_row(pending_row, cell_w, THUMBNAIL_GUTTER, columns=THUMBNAILS_PER_ROW))

    if not flowables:
        flowables.append(
            Paragraph(
                "No embeddable atlas images on disk for this session.",
                styles.NOTE_STYLE,
            )
        )
    return flowables

