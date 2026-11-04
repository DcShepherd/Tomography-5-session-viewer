"""Constants report components."""
from __future__ import annotations
import logging
from reportlab.lib.units import inch
from tomography_session_browser.domain.units import ANGSTROM_PER_PIXEL, DEGREE
from tomography_session_browser.presentation.project_tree import PROJECT_BADGE_MEANINGS as _TREE_BADGE_MEANINGS
from tomography_session_browser.reports import styles
from tomography_session_browser.reports.pdf_export import PORTRAIT_USABLE_W
from tomography_session_browser.presentation.dashboard.models import TILE_STATUS_WORDS as _DASHBOARD_TILE_STATUS_WORDS, TILE_STATUS_COMPLETE, TILE_STATUS_FAILED, TILE_STATUS_MISSING, TILE_STATUS_NEUTRAL, TILE_STATUS_WARNING


LOGGER = logging.getLogger("tomography_session_browser.reports.report_generator")


EMBEDDABLE_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


CARD_INNER_W = PORTRAIT_USABLE_W - 2 * styles.CARD_PADDING


PROJECT_BADGE_MEANINGS = _TREE_BADGE_MEANINGS


TILE_STATUS_WORDS = _DASHBOARD_TILE_STATUS_WORDS


TILE_CHIP_ORDER = (
    TILE_STATUS_COMPLETE,
    TILE_STATUS_WARNING,
    TILE_STATUS_FAILED,
    TILE_STATUS_MISSING,
    TILE_STATUS_NEUTRAL,
)


CONTENTS_BAND_W = 50.0


CONTENTS_BAND_GAP = 8.0


CONTENTS_CELL_PAD = 6.0


TEXT_FIT = 0.25


THUMBNAILS_PER_ROW = 4


THUMBNAIL_GUTTER = 8.0


COMPLETION_COLUMN_GAP = 18.0


ATLAS_DETAIL_MAX_ZOOM = 4.0


ATLAS_DETAIL_MARGIN_DP = (14.0, 12.0, 40.0, 12.0)  # left, top, right, bottom


ATLAS_PRINT_DPI = 300


ATLAS_MRC_MATCH = 0.95


FIGURE_GUTTER = 12.0


FIGURE_CELL_W = (PORTRAIT_USABLE_W - FIGURE_GUTTER) / 2


DENSE_MARKER_COUNT = 30


SETTINGS_COLUMNS = 5


SETTINGS_CELL_PAD = 4.0


TILT_TABLE_HEADERS: tuple[str, ...] = (
    "Tilt series",
    "Status",
    "Images",
    "Tilt range",
    "Original pixel",
    "MRC pixel",
    "Target defocus",
)


TILT_TABLE_UNITS: dict[str, str] = {
    "Tilt range": DEGREE,
    "Original pixel": ANGSTROM_PER_PIXEL,
    "MRC pixel": ANGSTROM_PER_PIXEL,
    "Target defocus": "µm",
}


TILT_NAME_MAX_W = 1.8 * inch


TILT_CELL_PAD = 5.0


EXPOSURE_COLUMNS = 2


EXPOSURE_GUTTER = 18.0


EXPOSURE_ROW_GAP = 12.0


EXPOSURE_MAX_ASPECT = 0.75


EXPOSURE_PRINT_DPI = 200


EXPOSURE_JPEG_QUALITY = 88


EXPOSURE_OWN_ANGLES = "MRC extended header"

