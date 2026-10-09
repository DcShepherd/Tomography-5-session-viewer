"""Constants for the image viewer."""
from __future__ import annotations
from PySide6.QtCore import Qt
from tomography_session_browser.domain.markers import MarkerType
from tomography_session_browser.presentation.context_stack import open_action_label
from tomography_session_browser.presentation.design_tokens import SPACE_M, SPACE_XS
from tomography_session_browser.presentation.tab_keys import TabKey


VIEWER_OBJECT_ROLE = int(Qt.ItemDataRole.UserRole)


LIST_PRIMARY_ROLE = VIEWER_OBJECT_ROLE + 1


LIST_SUMMARY_ROLE = VIEWER_OBJECT_ROLE + 2


LIST_STATUS_ROLE = VIEWER_OBJECT_ROLE + 3


LIST_TOOLTIP_ROLE = VIEWER_OBJECT_ROLE + 4


LIST_TAGS_ROLE = VIEWER_OBJECT_ROLE + 5


FILTER_TAG_PREFIX = "is:"


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
    ("search", TabKey.SEARCH),
    ("batch", "Batch"),
    ("tilt_series", TabKey.TILT_SERIES),
)


ARRIVAL_FRAME_SHARE = 0.4


VIEWPORT_MEMORY_LIMIT = 32


OVERLAY_PANEL_MARGIN_PX = 24


ATLAS_COLLECTION_LIST_MAX_PX = 180


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


LIST_ROW_NAME_HEIGHT_PX = 18


LIST_ROW_SUMMARY_HEIGHT_PX = 16


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


MARKER_TYPES_WITH_CONTROLS = (
    MarkerType.OVERVIEW,
    MarkerType.SEARCH_MAP,
    MarkerType.BATCH_POSITION,
    MarkerType.EXPOSURE_AREA,
    MarkerType.CAMERA_FOV,
    MarkerType.BATCH_LABEL,
    MarkerType.FOCUS_AREA,
    MarkerType.TRACKING_AREA,
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


ATLAS_LOD_CONTROL_LABELS = (
    (ATLAS_LOD_CLUSTER, "Cluster batch positions", "cluster-positions", True),
    (ATLAS_LOD_LABELS, "Batch position labels", "position-labels", True),
    (ATLAS_LOD_TILE_GRIDS, "Search-map tile grids", "tile-grid", True),
    (ATLAS_LOD_EXPOSURES, "Per-exposure markers", "exposure-markers", False),
)


SHARED_NAME_PREFIX_PROPERTY = "sharedNamePrefix"


NAME_PREFIX_BOUNDARIES = "_- ."


MIN_SHARED_PREFIX_CHARS = 4

