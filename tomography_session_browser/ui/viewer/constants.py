"""Constants for the image viewer."""
from __future__ import annotations
from PySide6.QtCore import Qt
from tomography_session_browser.domain.markers import MarkerType
from tomography_session_browser.presentation.context_stack import open_action_label
from tomography_session_browser.presentation.design_tokens import SPACE_M, SPACE_XS
from tomography_session_browser.presentation.tab_keys import TabKey
from tomography_session_browser.ui.viewer.models import OverlayControl, OverlaySection


VIEWER_OBJECT_ROLE = int(Qt.ItemDataRole.UserRole)


LIST_PRIMARY_ROLE = VIEWER_OBJECT_ROLE + 1


LIST_SUMMARY_ROLE = VIEWER_OBJECT_ROLE + 2


LIST_STATUS_ROLE = VIEWER_OBJECT_ROLE + 3


LIST_TOOLTIP_ROLE = VIEWER_OBJECT_ROLE + 4


LIST_TAGS_ROLE = VIEWER_OBJECT_ROLE + 5


#: The row's identity (``domain.identity.entity_key_text``), set on every object row.
VIEWER_KEY_ROLE = VIEWER_OBJECT_ROLE + 6


#: What a row that shows no object is: a ``GroupHeader`` or a ``Divider`` (B4.3).
VIEWER_ENTRY_ROLE = VIEWER_OBJECT_ROLE + 7


#: A group header's ``(rows shown, of them failed)``, rewritten by every filter.
LIST_GROUP_SHOWN_ROLE = VIEWER_OBJECT_ROLE + 8




MAX_PREVIEW_DIMENSION = 2048


NAVIGATION_ACTION_NAMES: dict[str, str] = {
    "atlas": open_action_label("Atlas"),
    "batch": open_action_label("Batch position"),
    "search": open_action_label("Search"),
    "search_map": open_action_label("Search map"),
    "overview": open_action_label("Overview"),
    "tilt_series": open_action_label("Tilt series"),
}


IMAGE_HIERARCHY = (TabKey.ATLAS, TabKey.OVERVIEW, TabKey.SEARCH_MAP, TabKey.SEARCH, TabKey.BATCH_POSITION, TabKey.TILT_SERIES)


NAVIGATION_TARGET_TABS = {
    "atlas": TabKey.ATLAS,
    "overview": TabKey.OVERVIEW,
    "search_map": TabKey.SEARCH_MAP,
    "search": TabKey.SEARCH,
    "batch": TabKey.BATCH_POSITION,
    "tilt_series": TabKey.TILT_SERIES,
}


JUMP_STEP_OUT = "out"


JUMP_STEP_IN = "in"


JUMP_ICONS = {JUMP_STEP_OUT: "step-out", JUMP_STEP_IN: "step-in"}


JUMP_DESCRIPTIONS = {
    JUMP_STEP_OUT: "Steps out to a broader view.",
    JUMP_STEP_IN: "Steps in to more detail.",
}


LINKED_HERE_TEXT = {
    TabKey.ATLAS: "this atlas",
    TabKey.OVERVIEW: "this overview",
    TabKey.SEARCH_MAP: "this search map",
    TabKey.SEARCH: "this tile",
    TabKey.BATCH_POSITION: "this batch",
    TabKey.TILT_SERIES: "this tilt series",
}


NAVIGATION_BUTTONS = (
    ("atlas", TabKey.ATLAS),
    ("overview", TabKey.OVERVIEW),
    ("search_map", TabKey.SEARCH_MAP),
    ("search", "Search tile"),
    ("batch", "Batch"),
    ("tilt_series", TabKey.TILT_SERIES),
)


ARRIVAL_FRAME_SHARE = 0.4


VIEWPORT_MEMORY_LIMIT = 32


ATLAS_COLLECTION_LIST_MAX_PX = 180


#: The swatch beside an Overlays row's label.
SWATCH_WIDTH_PX = 22
SWATCH_HEIGHT_PX = 14

#: A row under another in the Overlays panel is inset by this much, and its note
#: lines up with the label: after the box (14 px of indicator and its 1 px border on
#: each side, then 6 px, all the stylesheet's), the swatch, and the style's 4 px
#: between an icon and its text.
OVERLAY_SUB_INDENT_PX = 18
OVERLAY_HINT_INDENT_PX = 14 + 2 + 6 + SWATCH_WIDTH_PX + 4


FIRST_ZOOM_PREVIEW_DIMENSION = 4096


MRC_JPEG_FALLBACK_GRACE_MS = 220


PYRAMID_LEVELS = (2048, 4096, 8192)


MEDIUM_DETAIL_SCALE = 0.9


HIGH_DETAIL_SCALE = 1.6


WHEEL_ZOOM_BASE = 1.0015


BUTTON_ZOOM_STEP = 1.25


WHEEL_ZOOM_UNITS_PER_PIXEL = 2.0


LIST_REFILL_VEIL_OPACITY = 0.18


LIST_REFILL_VEIL_MS = 140


REFIT_EASE_ARM_S = 0.25


FIT_IN_VIEW_MARGIN_PX = 2


IMAGE_CROSSFADE_MS = 120


PULSE_MIN_RADIUS_PX = 10.0


PULSE_MAX_RADIUS_PX = 120.0


MIN_SCALE_BAR_SCREEN_PX = 58.0


MAX_SCALE_BAR_SCREEN_PX = 190.0


ZOOM_LABEL_MIN_WIDTH_PX = 48


HEADER_TITLE_MIN_PX = 140


ZOOM_PANEL_HORIZONTAL_PADDING_PX = 2 * SPACE_XS


FLOATING_CONTROL_INSET_PX = SPACE_M


FLOATING_CONTROL_SIZE_PX = 32


QWIDGETSIZE_MAX = (1 << 24) - 1


VIEWER_LIST_MIN_WIDTH_PX = 268


VIEWER_LIST_FLOOR_PX = 200


VIEWER_CANVAS_MIN_WIDTH_PX = 200


LIST_ROW_HEIGHT_PX = 56

LIST_ROW_SINGLE_HEIGHT_PX = 34


LIST_ROW_NAME_HEIGHT_PX = 18


LIST_ROW_SUMMARY_HEIGHT_PX = 16


#: A group's header, a hairline between runs, and a hairline that carries a label.
LIST_GROUP_HEADER_HEIGHT_PX = 32


LIST_DIVIDER_HEIGHT_PX = 9


LIST_DIVIDER_LABEL_HEIGHT_PX = 24


FLOATING_CONTROL_BOTTOM_INSET_PX = FLOATING_CONTROL_INSET_PX


EXPORT_RENDER_SCALE = 2.0


MAX_EXPORT_DIMENSION_PX = 8192


MAX_EXPORT_PIXELS = 50_000_000


DEFAULT_VISIBLE_MARKER_TYPES = (
    MarkerType.SEARCH_MAP,
    MarkerType.OVERVIEW,
    MarkerType.BATCH_POSITION,
    MarkerType.TILT_SERIES,
    MarkerType.LINK_LINE,
    MarkerType.EXPOSURE_AREA,
    MarkerType.CAMERA_FOV,
    MarkerType.BATCH_LABEL,
    MarkerType.TRACKING_AREA,
    MarkerType.FOCUS_AREA,
    MarkerType.CONDITION_AREA,
    MarkerType.STAGE_CROSSHAIR,
)


EXPOSURE_MARKER_HIGHLIGHT_WIDTH = 4.0


MARKER_TYPE_LABELS = {
    MarkerType.SEARCH_MAP: "Search map",
    MarkerType.OVERVIEW: "Overview",
    MarkerType.BATCH_POSITION: "Batch position",
    MarkerType.BATCH_CLUSTER: "Batch-position cluster",
    MarkerType.TILT_SERIES: "Tilt series",
    MarkerType.TEMPLATE_AREA: "Template",
    MarkerType.EXPOSURE_AREA: "Exposure area",
    MarkerType.CAMERA_FOV: "Camera FOV",
    MarkerType.BATCH_LABEL: "Batch labels",
    MarkerType.TRACKING_AREA: "Tracking",
    MarkerType.FOCUS_AREA: "Focus",
    MarkerType.CONDITION_AREA: "Condition",
    MarkerType.LINK_LINE: "Link",
    MarkerType.STAGE_CROSSHAIR: "Other",
}


ATLAS_LOD_CLUSTER = "cluster_batch_positions"


ATLAS_LOD_LABELS = "batch_position_labels"


ATLAS_LOD_TILE_GRIDS = "search_map_tile_grids"


ATLAS_LOD_EXPOSURES = "per_exposure_markers"


#: The Atlas options and how each starts. The viewer starts from these and the
#: Overlays panel lists them, so a key is added in one place.
ATLAS_LOD_DEFAULTS = {
    ATLAS_LOD_CLUSTER: True,
    ATLAS_LOD_LABELS: True,
    ATLAS_LOD_TILE_GRIDS: True,
    ATLAS_LOD_EXPOSURES: False,
}


#: The Overlays panel, row by row. A row names the marker types or Atlas LOD
#: keys its box sets; the panel is built from this and nothing else lists them.
OVERLAY_SECTIONS = (
    OverlaySection(
        "Acquisition areas",
        (
            OverlayControl("Overview", (MarkerType.OVERVIEW,), swatch="overview"),
            OverlayControl("Search map", (MarkerType.SEARCH_MAP,), swatch="search_map"),
            OverlayControl("Tile grids", (ATLAS_LOD_TILE_GRIDS,), parent="Search map", swatch="tile_grids"),
            OverlayControl("Batch position", (MarkerType.BATCH_POSITION,), hint="Coloured by status", swatch="batch_position"),
            OverlayControl("Per-exposure markers", (ATLAS_LOD_EXPOSURES,), parent="Batch position", swatch="per_exposure"),
        ),
    ),
    OverlaySection(
        "Beam and camera",
        (
            OverlayControl("Exposure area", (MarkerType.EXPOSURE_AREA,), swatch="exposure_area"),
            OverlayControl("Camera FOV", (MarkerType.CAMERA_FOV,), swatch="camera_fov"),
            OverlayControl("Focus and tracking", (MarkerType.FOCUS_AREA, MarkerType.TRACKING_AREA), swatch="focus_and_tracking"),
        ),
    ),
    OverlaySection(
        "Labels and grouping",
        (
            OverlayControl("Batch position numbers", (ATLAS_LOD_LABELS,), hint="Shown once zoomed in", swatch="position_numbers"),
            OverlayControl("Cluster nearby positions", (ATLAS_LOD_CLUSTER,), hint="Merged while zoomed out", swatch="cluster"),
            OverlayControl("Batch and exposure names", (MarkerType.BATCH_LABEL,), swatch="names"),
        ),
    ),
)


#: The marker types the table switches, in panel order.
OVERLAY_MARKER_TYPES = tuple(
    key
    for section in OVERLAY_SECTIONS
    for control in section.controls
    for key in control.toggles
    if key not in ATLAS_LOD_DEFAULTS
)


SHARED_NAME_PREFIX_PROPERTY = "sharedNamePrefix"


#: Sources whose provenance line says nothing when there is nothing to flag: a
#: plain MRC or image has its name in the status bar and its pixel size in the
#: badge, so the footer under the canvas is for exceptions only.
PLAIN_PROVENANCE = frozenset({"MRC", "Image"})
