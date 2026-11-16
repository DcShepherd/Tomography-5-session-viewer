"""What the Overlays panel asks the canvas to draw: one value, three presets.

Display only. ``OverlayPreferences`` is the requested value, including layers
the current image cannot show; availability masks it (``effective_types``)
and never changes it. No Qt classes here: the panel, the presets and the
publisher (``TabSelection.publish``) share these rules.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from tomography_session_browser.domain.markers import ImageMarker, MarkerType

if TYPE_CHECKING:
    from tomography_session_browser.presentation.atlas_colouring import AtlasColouring
from tomography_session_browser.ui.viewer.constants import (
    ATLAS_LOD_CLUSTER,
    ATLAS_LOD_DEFAULTS,
    ATLAS_LOD_LABELS,
    OVERLAY_MARKER_TYPES,
    OVERLAY_SECTIONS,
    ROWLESS_MARKER_TYPE_ROWS,
)


@dataclass(frozen=True, slots=True)
class OverlayPreferences:
    """The requested layers of one tab. Row keys only: rowless types follow their anchor."""

    marker_types: frozenset[str]
    lod: frozenset[str]
    legend: bool = True
    scale_bar: bool = True

    def with_keys(self, keys: Iterable[str], on: bool) -> OverlayPreferences:
        """These marker types or Atlas LOD keys switched ``on`` or off, together."""

        keys = tuple(keys)
        lod_keys = {key for key in keys if key in ATLAS_LOD_DEFAULTS}
        type_keys = set(keys) - lod_keys
        if on:
            return replace(self, marker_types=self.marker_types | type_keys, lod=self.lod | lod_keys)
        return replace(self, marker_types=self.marker_types - type_keys, lod=self.lod - lod_keys)

    def requested(self, key: str) -> bool:
        return key in (self.lod if key in ATLAS_LOD_DEFAULTS else self.marker_types)


STANDARD = OverlayPreferences(
    marker_types=frozenset(OVERLAY_MARKER_TYPES),
    lod=frozenset(key for key, on in ATLAS_LOD_DEFAULTS.items() if on),
)
POSITIONS_ONLY = OverlayPreferences(
    marker_types=frozenset({MarkerType.BATCH_POSITION}),
    lod=frozenset({ATLAS_LOD_LABELS, ATLAS_LOD_CLUSTER}),
)
CLEAN_IMAGE = OverlayPreferences(marker_types=frozenset(), lod=frozenset(), legend=False, scale_bar=False)


@dataclass(frozen=True, slots=True)
class OverlayPreset:
    name: str
    value: OverlayPreferences
    #: The one line under its name in the panel.
    note: str


PRESETS = (
    OverlayPreset("Standard", STANDARD, "Maps, positions and labels"),
    OverlayPreset("Positions only", POSITIONS_ONLY, "Batch positions and their numbers"),
    OverlayPreset("Clean image", CLEAN_IMAGE, "No overlays, legend or scale bar"),
)

#: What a value that is no preset reads as.
CUSTOM = "Custom"


def preset_name(preferences: OverlayPreferences) -> str:
    """The preset the value equals, or "Custom": plain equality, nothing inferred."""

    return next((preset.name for preset in PRESETS if preset.value == preferences), CUSTOM)


def changes_from(preferences: OverlayPreferences, base: OverlayPreferences = STANDARD) -> int:
    """How many panel rows (and canvas aids) differ from ``base``: a row of two keys counts once."""

    rows = [control.toggles for section in OVERLAY_SECTIONS for control in section.controls]
    changed = sum(
        1 for keys in rows if any(preferences.requested(key) != base.requested(key) for key in keys)
    )
    return changed + (preferences.legend != base.legend) + (preferences.scale_bar != base.scale_bar)


def effective_types(preferences: OverlayPreferences, unavailable: Iterable[str]) -> frozenset[str]:
    """The marker types the canvas draws (decision 15, the per-key rowless rule).

    A key is effective when it is requested and available. A rowless type is
    effective exactly when its anchor key is. The rule is the same on every
    image tab.
    """

    unavailable = frozenset(unavailable)
    keys = frozenset(key for key in preferences.marker_types if key not in unavailable)
    return keys | frozenset(rowless for rowless, anchor in ROWLESS_MARKER_TYPE_ROWS.items() if anchor in keys)


#: The beam-derived areas: unavailable together when the camera field is a fallback.
BEAM_AREA_TYPES = frozenset({MarkerType.EXPOSURE_AREA, MarkerType.FOCUS_AREA, MarkerType.TRACKING_AREA})


def unavailable_marker_types(markers: Iterable[ImageMarker]) -> frozenset[str]:
    """The row keys an item's markers cannot show, from the markers alone."""

    markers = list(markers)
    present = {marker.marker_type for marker in markers}
    unavailable: set[str] = set()
    if any(marker.marker_type == MarkerType.CAMERA_FOV and marker.metadata.get("filled_fallback") for marker in markers):
        unavailable |= BEAM_AREA_TYPES
    for marker_type in BEAM_AREA_TYPES:
        candidates = [marker for marker in markers if marker.marker_type == marker_type]
        if candidates and not any(marker.visible for marker in candidates):
            unavailable.add(marker_type)
    if MarkerType.CAMERA_FOV not in present:
        unavailable.add(MarkerType.CAMERA_FOV)
    if MarkerType.BATCH_LABEL not in present:
        unavailable.add(MarkerType.BATCH_LABEL)
    return frozenset(unavailable)


@dataclass(frozen=True, slots=True, eq=False)
class MarkerFrame:
    """Everything the marker layer draws for one item, handed over in one call.

    ``markers`` is compared by identity (the selection's cached list), so a
    frame for the same item and an unchanged visibility only reselects.
    """

    markers: list[ImageMarker]
    effective_types: frozenset[str]
    lod: frozenset[str]
    selected_id: str | None = None
    #: Colour by a value (batch 4); ``None`` draws status colours.
    colouring: AtlasColouring | None = None
