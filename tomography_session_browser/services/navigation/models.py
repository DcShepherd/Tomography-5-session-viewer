"""Transient navigation results and states; no domain relationships are changed."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tomography_session_browser.domain.models import (
    BatchPosition,
    Overview,
    SearchMap,
    SearchTile,
    TiltSeries,
)

STATE_NAVIGABLE = "navigable"
STATE_AMBIGUOUS = "ambiguous"
STATE_UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class NavigationResolution:
    """One relationship resolved to exactly one destination, or to nothing.

    Transient and display-only: this is UI navigation state, never a domain
    relationship, and it is never written back onto the source model.

    ``provenance`` records which rule produced the outcome so a disabled action
    can explain itself and so tests can assert *why* a destination resolved,
    not merely that it did.
    """

    target: Any | None = None
    candidate_ids: tuple[str, ...] = ()
    state: str = STATE_UNRESOLVED
    explanation: str = ""
    provenance: str = ""
    #: The action needs a selection first, rather than a missing relationship.
    selection_required: bool = False

    @property
    def navigable(self) -> bool:
        return self.state == STATE_NAVIGABLE and self.target is not None

    @property
    def ambiguous(self) -> bool:
        return self.state == STATE_AMBIGUOUS

    @property
    def unresolved(self) -> bool:
        return self.state == STATE_UNRESOLVED


@dataclass(frozen=True, slots=True)
class ExposureSelection:
    """Display-only identity of one selected exposure area."""

    batch_position_id: str
    exposure_index: int | None
    exposure_area_id: str | None
    exposure_type: str
    linked_tilt_series_id: str | None = None


@dataclass(frozen=True, slots=True)
class ExposureTiltSeriesResolution:
    """Conservative exposure-to-tilt navigation result."""

    exposure: ExposureSelection | None
    tilt_series: TiltSeries | None
    tooltip: str
    ambiguous: bool = False
    resolution: NavigationResolution = field(default_factory=NavigationResolution)


@dataclass(frozen=True, slots=True)
class TiltSeriesNavigationTargets:
    """Resolved destinations for a selected tilt series.

    The four object attributes are the long-standing public surface. The
    matching ``*_resolution`` fields carry the ambiguity state and explanation
    for the same relationship and default to "unresolved" so existing
    construction sites keep working.

    Rebuild this with :func:`dataclasses.replace`, never by keyword from the
    legacy fields alone — that silently drops the resolutions, and with them
    every explanation the UI shows for a refused jump.
    """

    batch_position: BatchPosition | None = None
    search_tile: SearchTile | None = None
    search_map: SearchMap | None = None
    overview: Overview | None = None
    inferred_batch_label: str | None = None
    batch_position_resolution: NavigationResolution = field(default_factory=NavigationResolution)
    search_tile_resolution: NavigationResolution = field(default_factory=NavigationResolution)
    search_map_resolution: NavigationResolution = field(default_factory=NavigationResolution)
    overview_resolution: NavigationResolution = field(default_factory=NavigationResolution)


@dataclass(frozen=True, slots=True)
class BatchPositionOverviewTarget:
    overview: Overview | None
    explanation: str
    ambiguous: bool = False
    resolution: NavigationResolution = field(default_factory=NavigationResolution)

    @property
    def navigable(self) -> bool:
        return self.overview is not None and not self.ambiguous


@dataclass(frozen=True, slots=True)
class BatchPositionSearchMapTarget:
    search_map: SearchMap | None
    explanation: str
    ambiguous: bool = False
    resolution: NavigationResolution = field(default_factory=NavigationResolution)

    @property
    def navigable(self) -> bool:
        return self.search_map is not None and not self.ambiguous
