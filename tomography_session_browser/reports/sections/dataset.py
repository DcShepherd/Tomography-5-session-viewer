"""Sections dataset report components."""
from __future__ import annotations
from reportlab.lib.units import inch
from reportlab.platypus import CondPageBreak, Paragraph, Spacer
from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.domain.markers import ImageMarker
from tomography_session_browser.presentation.workspace import EntityKind
from tomography_session_browser.domain.models import Sample
from tomography_session_browser.reports import graphics, styles
from tomography_session_browser.reports.pdf_export import PORTRAIT_USABLE_W
from tomography_session_browser.services.tilt_series_validation import STATUS_FAILED, summarise_validations
from tomography_session_browser.reports.context import SessionContext, fallback_session_atlas
from tomography_session_browser.reports.formatting import tilt_outcome_sub_label
from tomography_session_browser.reports.layout import has_figures, headed_block, meta_row, overlay_key, stat_card_data, stat_cards_block
from tomography_session_browser.reports.sections.atlas import atlas_image_block
from tomography_session_browser.reports.sections.completion import search_map_completion_block
from tomography_session_browser.reports.sections.exposures import exposure_thumbnail_section
from tomography_session_browser.reports.sections.overviews import overview_grid
from tomography_session_browser.reports.sections.search_maps import search_map_blocks
from tomography_session_browser.reports.sections.settings import acquisition_settings_table
from tomography_session_browser.reports.sections.tilt_table import tilt_series_table


def data_collection_section(
    ctx: SessionContext,
    sample: Sample,
    *,
    include_thumbnails: bool,
    generation_warnings: list[str],
    include_exposure_thumbnails: bool = False,
    position: tuple[int, int] | None = None,
    anchor: str | None = None,
) -> list:
    anchor = anchor or f"dataset-{sample.id}"
    eyebrow = f"Dataset {position[0]} of {position[1]}" if position else "Dataset"
    atlas_meta: tuple[str, str | None, str | None]
    if id(sample) in ctx.atlas_notes:
        atlas_meta = ("Atlas", ctx.atlas_notes[id(sample)], "warn")
    elif id(sample) in ctx.atlas_sources:
        atlas_meta = ("Atlas", f"linked from {ctx.atlas_sources[id(sample)]}", None)
    elif ctx.sample_atlases.get(id(sample)) is not None:
        atlas_meta = ("Atlas", "recorded with this dataset", None)
    else:
        atlas_meta = ("Atlas", "none recorded", None)

    flowables: list = [
        Paragraph(graphics.html_escape(eyebrow), styles.EYEBROW_STYLE),
        Spacer(1, 2),
        graphics.AnchoredParagraph(
            graphics.html_escape(sample.name), styles.SECTION_TITLE_STYLE,
            anchor=anchor, outline=sample.name, level=0,
        ),
        meta_row([("Session", ctx.session.name), atlas_meta]),
        Spacer(1, 10),
    ]

    # IDs of validator-flagged failed tilt series. Threaded through to
    # the marker pipeline so failed batches' exposure / template
    # overlays render in the red "failed" colour, matching the GUI.
    failed_tilt_ids = frozenset(
        v_id for v_id, v in ctx.validations.items() if v.status == STATUS_FAILED
    )

    # ------- A. graphical summary
    flowables.extend(sample_graphical_summary(sample, ctx))

    # ------- B. atlas image
    atlas = ctx.sample_atlases.get(id(sample))
    if atlas is None and id(sample) not in ctx.atlas_notes:
        atlas = sample.atlas or fallback_session_atlas(ctx, sample)
    if include_thumbnails and atlas is not None:
        atlas_block = atlas_image_block(
            atlas,
            label=f"Atlas for {sample.name}",
            generation_warnings=generation_warnings,
            scope_overviews=sample.overviews,
            scope_search_maps=sample.search_maps,
            scope_batch_positions=sample.batch_positions,
            scope_tilt_series=sample.tilt_series,
            failed_tilt_ids=failed_tilt_ids,
        )
        if atlas_block:
            # The figure keeps its caption; the heading follows that block.
            flowables.extend(headed_block("Atlas", atlas_block, anchor=f"{anchor}-atlas"))

    # ------- C. overview images
    if include_thumbnails and sample.overviews:
        drawn: list[ImageMarker] = []
        block = overview_grid(
            sample.overviews,
            sample,
            generation_warnings,
            failed_tilt_ids=failed_tilt_ids,
            drawn_markers=drawn,
        )
        if block:
            # Each overview row measures its actual image and caption height.
            flowables.extend(headed_block(
                "Overviews", block,
                lead=overlay_key(drawn, figures=has_figures(block)),
                anchor=f"{anchor}-overviews",
                meta=count_phrase(len(sample.overviews), "overview"),
            ))

    # ------- D. search maps with overlays
    if include_thumbnails and sample.search_maps:
        drawn = []
        block = search_map_blocks(sample.search_maps, sample, ctx, generation_warnings, drawn_markers=drawn)
        flowables.extend(headed_block(
            "Search maps", block,
            lead=overlay_key(drawn, figures=has_figures(block)),
            anchor=f"{anchor}-search-maps",
            meta=count_phrase(len(sample.search_maps), "search map"),
        ))

    if sample.tilt_series:
        flowables.append(CondPageBreak(1.3 * inch))
        flowables.append(graphics.section_heading(
            "Tilt series",
            anchor=f"{anchor}-tilt-series",
            meta=tilt_outcome_sub_label(sample_validation_summary(sample, ctx)),
        ))
        flowables.extend(tilt_series_table(sample.tilt_series, ctx))

    # ------- G. exposure thumbnails (optional)
    if include_thumbnails and include_exposure_thumbnails and sample.tilt_series:
        flowables.extend(exposure_thumbnail_section(
            sample, ctx, generation_warnings, anchor=f"{anchor}-exposures",
        ))
    return flowables


def sample_validation_summary(sample: Sample, ctx: SessionContext) -> dict[str, int]:
    return summarise_validations([
        ctx.validations[tilt.id]
        for tilt in sample.tilt_series
        if tilt.id in ctx.validations
    ])


def sample_stat_cards(sample: Sample, ctx: SessionContext) -> list[tuple[graphics.StatCardData, int]]:
    """The dataset's count cards, chipped from the dashboard's own tiles.

    The session dashboard model already classified every item; a dataset
    takes the tiles of its own entities (ids are path-derived, so they do not
    collide across datasets).
    """

    entities = [
        ("Overviews", EntityKind.OVERVIEW, sample.overviews),
        ("Search maps", EntityKind.SEARCH_MAP, sample.search_maps),
        ("Batch positions", EntityKind.BATCH_POSITION, sample.batch_positions),
        ("Tilt series", EntityKind.TILT_SERIES, sample.tilt_series),
    ]
    cards = []
    for label, kind, items in entities:
        ids = {item.id for item in items}
        statuses = [
            tile.status
            for card in ctx.dashboard.counts
            if card.kind is kind
            for tile in card.items
            if tile.id in ids
        ]
        cards.append((stat_card_data(label, len(items), statuses), len(items)))
    return cards


def sample_graphical_summary(sample: Sample, ctx: SessionContext) -> list:
    flowables: list = list(stat_cards_block(sample_stat_cards(sample, ctx), scope_noun="dataset"))
    flowables.append(Spacer(1, styles.CARD_GAP))

    if sample.tilt_series:
        flowables.append(graphics.card(
            [acquisition_settings_table(sample.tilt_series, sample.batch_positions, ctx.validations)],
            width=PORTRAIT_USABLE_W,
            title="Acquisition settings",
            badge=count_phrase(len(sample.tilt_series), "tilt series", "tilt series"),
        ))
        flowables.append(Spacer(1, styles.CARD_GAP))

    if sample.search_maps:
        flowables.append(graphics.card(
            [
                Paragraph("Exposure areas acquired of those planned, per search map", styles.MUTED_STYLE),
                search_map_completion_block(sample, ctx),
            ],
            width=PORTRAIT_USABLE_W,
            title="Search map completion",
            badge=count_phrase(len(sample.search_maps), "map"),
        ))
        flowables.append(Spacer(1, styles.CARD_GAP))
    return flowables

