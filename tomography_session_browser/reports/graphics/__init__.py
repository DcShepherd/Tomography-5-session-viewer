"""Static compatibility exports for report graphics."""
from tomography_session_browser.reports.graphics.anchors import AnchoredParagraph
from tomography_session_browser.reports.graphics.atlas_figure import AtlasMarkerPrintLegend
from tomography_session_browser.reports.graphics.atlas_figure import AtlasStatusFigure
from tomography_session_browser.reports.graphics.status import BAND_GAP
from tomography_session_browser.reports.graphics.status import BAND_MIN_SEGMENT
from tomography_session_browser.reports.graphics.status import Band
from tomography_session_browser.reports.graphics.status import Chip
from tomography_session_browser.reports.graphics.status import Chips
from tomography_session_browser.reports.graphics.status import CompletionBar
from tomography_session_browser.reports.graphics.status import Dot
from tomography_session_browser.reports.graphics.atlas_figure import FramedImage
from tomography_session_browser.reports.graphics.overlays import LOGGER
from tomography_session_browser.presentation.design_tokens import NORMAL_STATUS_GLYPHS
from tomography_session_browser.reports.graphics.overlays import OverlayKey
from tomography_session_browser.reports.graphics.overlays import OverlayKeyItem
from tomography_session_browser.reports.graphics.overlays import OverlayedImage
from tomography_session_browser.reports.graphics.overlays import PRINT_FRAME
from tomography_session_browser.reports.graphics.atlas_figure import PT_PER_DP
from tomography_session_browser.reports.graphics.anchors import PageRef
from tomography_session_browser.reports.graphics.status import ProjectBadge
from tomography_session_browser.reports.graphics.atlas_figure import RasterCrop
from tomography_session_browser.reports.graphics.cards import StatCard
from tomography_session_browser.reports.graphics.cards import StatCardData
from tomography_session_browser.reports.graphics.status import StatusGlyph
from tomography_session_browser.reports.graphics.status import StatusPill
from tomography_session_browser.reports.graphics.status import BADGE_TONES as _BADGE_TONES
from tomography_session_browser.reports.graphics.status import BAND_ORDER as _BAND_ORDER
from tomography_session_browser.reports.graphics.overlays import KEYED_MARKER_TYPES as _KEYED_MARKER_TYPES
from tomography_session_browser.reports.graphics.overlays import KEY_REASON_ORDER as _KEY_REASON_ORDER
from tomography_session_browser.reports.graphics.overlays import KEY_TYPE_ORDER as _KEY_TYPE_ORDER
from tomography_session_browser.reports.graphics.overlays import MARK_LINES as _MARK_LINES
from tomography_session_browser.reports.graphics.overlays import PRINT_DASHES as _PRINT_DASHES
from tomography_session_browser.reports.graphics.overlays import STATUS_REASONS as _STATUS_REASONS
from tomography_session_browser.reports.graphics.overlays import batch_label_pdf_origin as _batch_label_pdf_origin
from tomography_session_browser.reports.graphics.atlas_figure import draw_pdf_atlas_leaf as _draw_pdf_atlas_leaf
from tomography_session_browser.reports.graphics.atlas_figure import natural_key as _natural_key
from tomography_session_browser.reports.graphics.atlas_figure import overlaps as _overlaps
from tomography_session_browser.reports.graphics.overlays import print_style as _print_style
from tomography_session_browser.reports.graphics.text import caption_width
from tomography_session_browser.reports.graphics.cards import card
from tomography_session_browser.reports.graphics.cards import card_header
from tomography_session_browser.reports.graphics.cards import draw_brand_mark
from tomography_session_browser.reports.graphics.text import draw_caption
from tomography_session_browser.reports.graphics.status import draw_dot
from tomography_session_browser.reports.graphics.atlas_figure import draw_pdf_cluster
from tomography_session_browser.reports.graphics.status import draw_status_band
from tomography_session_browser.reports.graphics.status import draw_status_glyph
from tomography_session_browser.reports.graphics.text import ellipsis
from tomography_session_browser.reports.graphics.text import fit_text
from tomography_session_browser.reports.graphics.text import html_escape
from tomography_session_browser.reports.graphics.status import is_normal_status
from tomography_session_browser.reports.graphics.atlas_figure import keep_with_caption
from tomography_session_browser.reports.graphics.anchors import mark_page
from tomography_session_browser.reports.graphics.overlays import marker_stroke
from tomography_session_browser.reports.graphics.overlays import native_image_size
from tomography_session_browser.reports.graphics.overlays import overlay_key_items
from tomography_session_browser.reports.graphics.cards import plain_table
from tomography_session_browser.reports.graphics.anchors import section_heading
from tomography_session_browser.reports.graphics.cards import side_by_side
from tomography_session_browser.reports.graphics.cards import stat_card_row
from tomography_session_browser.reports.graphics.status import status_mark

__all__ = [
    "AnchoredParagraph",
    "AtlasMarkerPrintLegend",
    "AtlasStatusFigure",
    "Band",
    "RasterCrop",
    "Chip",
    "Chips",
    "CompletionBar",
    "Dot",
    "NORMAL_STATUS_GLYPHS",
    "OverlayKey",
    "OverlayKeyItem",
    "OverlayedImage",
    "PageRef",
    "ProjectBadge",
    "StatCard",
    "StatCardData",
    "StatusGlyph",
    "StatusPill",
    "card",
    "card_header",
    "caption_width",
    "draw_brand_mark",
    "draw_caption",
    "draw_pdf_cluster",
    "draw_status_band",
    "fit_text",
    "keep_with_caption",
    "mark_page",
    "native_image_size",
    "overlay_key_items",
    "plain_table",
    "section_heading",
    "side_by_side",
    "stat_card_row",
    "status_mark",
]

