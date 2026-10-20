"""Typed display models for dashboards, acquisition metrics and summaries."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING
from tomography_session_browser.domain.models import (
    Atlas,
    BatchPosition,
    Overview,
    Sample,
    SearchMap,
    Session,
    TiltSeries,
)
from tomography_session_browser.presentation.workspace import EntityKind

if TYPE_CHECKING:
    from tomography_session_browser.presentation.dashboard.summary import DashboardSummary


@dataclass(slots=True)
class LinkedAtlasDashboardScope:
    """Dashboard-only wrapper for collection scopes with an external atlas.

    The tree selection remains a ``Session`` or ``Sample`` so non-atlas tabs
    stay scoped to that collection. This wrapper only supplies the linked
    atlas entries that the Atlas tab already exposes.
    """

    source: Session | Sample
    atlases: list[tuple[str, Atlas]]


@dataclass(slots=True)
class DashboardEntityScope:
    """Dashboard-only scope for selected acquisition entities.

    Search maps and batch positions only carry linked tilt-series ids. The
    main window resolves those ids while it still has the active project
    context, then passes the resolved tilt series here so presenter logic can
    stay pure and filesystem-free.
    """

    source: SearchMap | BatchPosition
    search_maps: list[SearchMap] = field(default_factory=list)
    batch_positions: list[BatchPosition] = field(default_factory=list)
    tilt_series: list[TiltSeries] = field(default_factory=list)
    samples: list[Sample] = field(default_factory=list)
    sample_groups: list[tuple[str, list[Sample]]] = field(default_factory=list)
    title: str | None = None
    path: str | None = None
    kind: str | None = None


@dataclass(frozen=True, slots=True)
class AtlasCountModel:
    """Distinct atlas counts surfaced on the dashboard and PDF cover.

    * ``atlas_session_count``    — number of standalone Atlas-screening
      sessions in the loaded set.
    * ``data_collection_sample_count`` — data-collection samples (i.e.
      samples that contain at least one search map / batch position /
      tilt series / overview).
    * ``samples_with_atlas_count`` — data-collection samples that have an
      associated atlas, either directly on the sample or via the
      cross-session sample linker.
    * ``sample_atlas_count``     — distinct atlases linked to a
      data-collection sample. Deduplicated by canonical atlas key so the
      same atlas referenced by two data-collection samples is one row.
    """

    atlas_session_count: int = 0
    data_collection_sample_count: int = 0
    samples_with_atlas_count: int = 0
    sample_atlas_count: int = 0


INFERRED_BATCH_TILE_PREFIX = "inferred:"


TILE_STATUS_COMPLETE = "complete"


TILE_STATUS_WARNING = "warning"


TILE_STATUS_FAILED = "failed"


TILE_STATUS_MISSING = "missing"


TILE_STATUS_NEUTRAL = "neutral"


TILE_STATUS_WORDS: dict[str, str] = {
    TILE_STATUS_COMPLETE: "complete",
    TILE_STATUS_WARNING: "incomplete",
    TILE_STATUS_FAILED: "failed",
    TILE_STATUS_MISSING: "missing",
    TILE_STATUS_NEUTRAL: "unknown",
}


_CARD_STATUS_WORDS: dict[str, dict[str, str]] = {
    "Batch positions": {TILE_STATUS_NEUTRAL: "pending"},
    "Search maps overview": {TILE_STATUS_MISSING: "pending"},
}


def tile_status_word(card_label: str, status: str) -> str:
    """The word ``status`` is read as on the card labelled ``card_label``."""

    specific = _CARD_STATUS_WORDS.get(card_label, {}).get(status)
    return specific or TILE_STATUS_WORDS.get(status, status.replace("_", " "))


@dataclass(slots=True)
class StatusTileModel:
    """One item in a stat card's tile grid.

    ``status`` drives the tile colour; ``id`` lets the dashboard emit a
    navigation signal so clicking a tile selects the underlying entity.
    """

    id: str
    name: str
    status: str  # one of TILE_STATUS_* constants
    tooltip: str
    destination: str = ""  # tab label to switch to on click ("Search map", etc.)
    #: The batch position a tilt-series tile belongs to, as the tilt series'
    #: list says it ("" when it has none); ``group_inferred`` when the batch
    #: was inferred from the series' name rather than recorded.
    group: str = ""
    group_inferred: bool = False


@dataclass(slots=True)
class StatCardModel:
    label: str
    value: int
    # ``sparkline`` is retained as a fallback for callers that haven't yet
    # migrated to the per-item status model. Newer renders prefer the tile
    # grid built from ``items`` / ``status_summary`` and ignore the sparkline.
    sparkline: list[int]
    items: list[StatusTileModel]
    status_summary: dict[str, int]
    destination: str  # tab label to switch to when the card itself is clicked
    #: Which entities the card counts; find a card by this, never by its label.
    kind: EntityKind
    #: One line under the number when the count alone would mislead: the Batch
    #: positions card, when some rows are failed groups inferred from series
    #: names rather than recorded batches ("4 recorded · 1 inferred failed").
    detail: str = ""


@dataclass(slots=True)
class TiltSeriesOutcomeModel:
    """Aggregated counts for the dashboard donut.

    Sourced from ``services.tilt_series_validation`` so the categories match
    the four formal statuses (Complete / Incomplete / Failed / Unknown).
    """

    complete: int
    incomplete: int
    failed: int
    unknown: int

    @property
    def total(self) -> int:
        return self.complete + self.incomplete + self.failed + self.unknown


@dataclass(slots=True)
class SearchMapAcquisitionRollup:
    planned: int
    acquired: int
    tilt_series_ids: list[str]
    failed_exposure_areas: int
    incomplete_tilt_series: int
    unknown_tilt_series: int
    failed_batch_groups: int
    stage_inferred_failed_tilts: int


@dataclass(slots=True)
class DashboardFilterModel:
    label: str
    count: int
    tab: str
    query: str
    tooltip: str
    #: The rows the summary counted, independent of the destination list's scope.
    row_keys: frozenset[str] | None = None
    scope_label: str = ""


@dataclass(frozen=True, slots=True)
class AttributedWarning:
    """A warning, and the parsed objects it was recorded against.

    ``entity_keys`` are ``domain/identity.canonical_entity_key`` values: the
    sample, session or entity whose ``.warnings`` it came from, or every tilt
    series a derived warning covers. Empty means "not attributed", which only
    a warning with no object at all can be. Display-only; nothing is written
    back to the object.
    """

    text: str
    entity_keys: frozenset[tuple[str, str]] = frozenset()


@dataclass(slots=True)
class WarningGroupModel:
    label: str
    count: int
    severity: str  # "info" | "warning" | "error"
    items: list[str]
    #: The union of the members' ``entity_keys``; ``None`` as soon as one
    #: member is not attributed, since the group then covers an unknown object.
    entity_keys: frozenset[tuple[str, str]] | None = None


@dataclass(slots=True)
class BatchPositionProgressModel:
    id: str
    label: str
    exposure_count: int
    tilt_series: int
    complete: int
    warning: int
    failed: int
    queued: int
    status: str
    tooltip: str
    #: True when this row is a display-only group inferred from orphaned
    #: failed tilt series rather than a parsed ``BatchPosition``. Inferred
    #: rows are why the card can list more entries than the Batch position
    #: tab counts, so the dashboard has to say which ones they are.
    inferred: bool = False

    @property
    def total(self) -> int:
        return self.complete + self.warning + self.failed + self.queued


@dataclass(slots=True)
class TimelineItemModel:
    """Display metadata for one timeline segment.

    ``TimelineStrip`` receives these objects as an id-keyed mapping. Keeping
    the shape here lets the presenter reuse validator and batch-position
    metadata without making the painter understand the domain model.
    """

    tilt_series_id: str
    lane_key: str
    lane_label: str
    tooltip: str
    status: str


@dataclass(slots=True)
class DefocusPointModel:
    """One per-image Defocus value and its MRC/MDOC source context."""

    sample_name: str
    data_collection_name: str
    linked_group_label: str | None
    tilt_series_id: str
    tilt_series_name: str
    frame_index: int
    frame_count: int
    tilt_angle: float | None
    acquisition_time: datetime | None
    frame_order: int
    defocus_um: float
    metadata_source: str
    has_absolute_time: bool
    has_relative_time: bool
    mrc_defocus_um: float | None = None
    mdoc_defocus_um: float | None = None
    target_defocus_um: float | None = None
    application_applied_defocus_um: float | None = None
    sources_agree: bool | None = None
    tooltip: str | None = None


@dataclass(slots=True)
class DefocusPlotModel:
    """Chart-ready per-image Defocus data for the dashboard scatter plot."""

    points: list[DefocusPointModel]
    total_tilt_series: int
    defocus_tilt_series: int
    timestamp_tilt_series: int
    x_mode: str  # "absolute_time" | "frame_order"
    x_axis_label: str
    note: str | None = None

    @property
    def has_points(self) -> bool:
        return bool(self.points)


@dataclass(slots=True)
class DoseInformationPointModel:
    """One camera dose value parsed from MRC per-frame metadata."""

    sample_name: str
    data_collection_name: str
    linked_group_label: str | None
    tilt_series_id: str
    tilt_series_name: str
    frame_index: int
    frame_count: int
    tilt_angle: float | None
    acquisition_time: datetime | None
    frame_order: int
    dose_e_per_angstrom2: float
    metadata_source: str
    has_absolute_time: bool
    tooltip: str | None = None


@dataclass(slots=True)
class DoseInformationPlotModel:
    """Chart-ready camera-dose data for the dashboard scatter plot."""

    points: list[DoseInformationPointModel]
    total_tilt_series: int
    dose_tilt_series: int
    timestamp_tilt_series: int
    x_mode: str  # "absolute_time" | "frame_order"
    x_axis_label: str
    median_dose_e_per_angstrom2: float | None = None
    min_dose_e_per_angstrom2: float | None = None
    max_dose_e_per_angstrom2: float | None = None
    status: str = "neutral"
    status_reason: str | None = None
    source_label: str | None = None
    note: str | None = None

    @property
    def has_points(self) -> bool:
        return bool(self.points)


@dataclass(slots=True)
class InferredBatchGroup:
    key: str
    label: str
    tilt_series: list[TiltSeries]
    #: The grid (sample folder) the group's series sit on, when known.
    owner: str | None = None
    #: Recorded batches this group may join: same name *and* same grid.
    batch_ids: frozenset[str] = frozenset()


@dataclass(slots=True)
class AtlasRowModel:
    sample_label: str
    image_path: str | None  # rendered atlas image (jpg) if present
    #: The atlas's files could not be found (its own warnings say so, or it has
    #: no image).
    missing: bool = False
    #: The grid's atlas was acquired more than once; this is the one shown.
    reacquired: bool = False
    #: The one data-collection sample linked to this atlas's sample, when there
    #: is exactly one; ``None`` for none or several (no guess).
    linked_sample: str | None = None
    #: That sample's tilt series; ``None`` while its collection is not loaded.
    tilt_series_count: int | None = None
    #: The atlas's own id: what a click on its tile opens.
    atlas_id: str = ""
    #: All loaded collection members, even when there is no single link label.
    collection_sample_ids: tuple[str, ...] = ()


@dataclass(slots=True)
class AtlasSummaryModel:
    """The atlases a scope holds, and how many there are.

    ``rows`` are the atlases the Atlas tab lists (a Single Atlas placeholder is
    not one: it is neither a grid nor a second atlas); ``counts`` is the
    four-way breakdown the PDF cover uses too.
    """

    rows: list[AtlasRowModel]
    counts: AtlasCountModel = field(default_factory=AtlasCountModel)


@dataclass(slots=True)
class DashboardModel:
    """Aggregate model that drives the Session-tab dashboard.

    ``summary`` says which page the scope gets (collection, sample, atlas
    session or empty) and carries what only that page needs; the fields here
    are what the pages share: the count cards, the outcomes, the charts, the
    filters and the warning groups. The PDF reads ``counts``, ``outcomes`` and
    the acquisition window.
    """

    title: str
    path: str
    kind: str
    microscope: str | None
    acquisition_start: str | None
    acquisition_end: str | None
    acquisition_duration: str | None  # human-readable elapsed time, e.g. "14 hours and 4 minutes"
    counts: list[StatCardModel]
    outcomes: TiltSeriesOutcomeModel
    defocus_readout: DefocusPlotModel
    dose_information: DoseInformationPlotModel
    filters: list[DashboardFilterModel]
    warning_groups: list[WarningGroupModel]
    batch_positions: list[BatchPositionProgressModel]
    timeline_items: dict[str, TimelineItemModel]
    sample_count: int
    is_sample_scope: bool
    has_atlases: bool
    has_collection_data: bool
    atlas_summary: AtlasSummaryModel | None
    #: Which page the dashboard draws, and what only that page needs.
    summary: DashboardSummary


@dataclass(slots=True)
class DashboardScope:
    """Normalised view of whatever the user has selected in the project tree.

    The dashboard model is built from this — keeping the scope separate from
    its source object lets us drive the same widget from a top-level
    ``Session``, an individual ``Sample``, a cross-session
    ``LinkedSampleGroup``, or a list of sessions, without scattering
    isinstance checks through the model builder.
    """

    title: str
    path: str
    kind: str
    atlases: list[tuple[str, Atlas]]  # (sample_label_for_display, atlas)
    overviews: list[Overview]
    search_maps: list[SearchMap]
    batch_positions: list[BatchPosition]
    tilt_series: list[TiltSeries]
    samples: list[Sample]
    sample_groups: list[tuple[str, list[Sample]]]
    warnings: list[AttributedWarning]
    is_sample_scope: bool = False


@dataclass(frozen=True, slots=True)
class ExposureThumbnailModel:
    """A tilt series' exposure image and what its frame recorded.

    Display-only, for the report's exposure thumbnails: names the frame of the
    tilt series' thumbnail stack at its reference angle (0°, or a lamella's
    pretilt; ``services.tilt_thumbnails``) and reads that frame's
    extended-header record by the dose plot's rules.
    """

    tilt_series_id: str
    tilt_series_name: str
    frame_index: int  # stack order, from 0
    frame_count: int
    tilt_angle: float
    #: 0°, or for a lamella its pretilt: the angle its tilt series started from.
    reference_angle: float
    lamella: bool
    #: A lamella whose starting angle is not recorded: 0° stood in for its pretilt.
    pretilt_unknown: bool
    #: No frame at the reference angle; ``tilt_angle`` is the nearest one.
    misses_reference: bool
    angle_source: str
    acquisition_time: str | None
    #: The camera dose recorded for this one image, not the series' total.
    camera_dose_e_per_angstrom2: float | None
    stage_um: tuple[float, float] | None
    image_size: tuple[int, int] | None  # the thumbnail's nx, ny, for layout
