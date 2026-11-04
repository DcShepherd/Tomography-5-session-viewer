"""Constants components for the timeline."""
from __future__ import annotations


MAX_UNGROUPED_LANES = 13


MAX_GROUPED_LANES = 13


DENSITY_THRESHOLD_SEGMENTS = 50


#: Key of the lane that gathers the lanes past ``MAX_GROUPED_LANES``.
OVERFLOW_LANE_KEY = "other"


#: Where the first lane starts and the height the time axis takes below them.
LANES_TOP_PX = 34
AXIS_HEIGHT_PX = 24


#: Widest the lane-name gutter grows; longer names are shortened.
MAX_LANE_LABEL_WIDTH_PX = 150


DENSITY_BIN_TARGET = 72


HIGHLIGHT_FADE_ALPHA_FACTOR = 0.38


HIGHLIGHT_RING_ALPHA = 118


HIGHLIGHT_RING_WIDTH = 1.0

