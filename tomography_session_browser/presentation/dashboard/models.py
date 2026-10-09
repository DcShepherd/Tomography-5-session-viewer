"""Typed display models for dashboards, acquisition metrics and summaries."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
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
class SearchMapOverviewRowModel:
    id: str
    label: str
    status: str
    planned: int
    acquired: int
    failed_tilt_series: int
    incomplete_tilt_series: int
    unknown_tilt_series: int
    acquisition_associated: bool = True
    failed_batch_groups: int = 0
    batch_positions: int = 0
    acquired_batch_positions: int = 0

    @property
    def missing(self) -> int:
        if not self.acquisition_associated:
            return 0
        accounted = (
            self.acquired
            + self.failed_tilt_series
            + self.incomplete_tilt_series
            + self.unknown_tilt_series
        )
        return max(self.planned - accounted, 0)

    @property
    def score(self) -> tuple[int, int, int, int, int, str]:
        """Sort key: worst/most actionable rows first."""

        association_rank = 1 if self.acquisition_associated else 0
        status_rank = {
            TILE_STATUS_FAILED: 4,
            TILE_STATUS_WARNING: 3,
            TILE_STATUS_MISSING: 2,
            TILE_STATUS_NEUTRAL: 1,
            TILE_STATUS_COMPLETE: 0,
        }.get(self.status, 0)
        return (
            association_rank,
            status_rank,
            self.failed_tilt_series,
            self.incomplete_tilt_series + self.unknown_tilt_series + self.missing,
            self.planned,
            self.label.casefold(),
        )

    @property
    def is_affected(self) -> bool:
        """True when this search map has something a reviewer should act on."""

        if not self.acquisition_associated:
            return False
        if self.status in {TILE_STATUS_FAILED, TILE_STATUS_WARNING, TILE_STATUS_MISSING}:
            return True
        return bool(
            self.failed_tilt_series
            or self.incomplete_tilt_series
            or self.unknown_tilt_series
            or self.missing
            or self.acquired_batch_positions < self.batch_positions
        )

    @property
    def summary(self) -> str:
        if not self.acquisition_associated:
            return "N/A"
        return f"{self.acquired_batch_positions}/{self.batch_positions} batch positions"


@dataclass(slots=True)
class SearchMapOverviewModel:
    rows: list[SearchMapOverviewRowModel]
    complete: int
    incomplete: int
    failed: int
    unknown: int
    not_associated: int = 0

    @property
    def total(self) -> int:
        return len(self.rows)

    @property
    def worst(self) -> list[SearchMapOverviewRowModel]:
        """The rows that actually warrant attention, worst first.

        This used to return the top five by score regardless of whether
        anything was wrong, so a healthy session filled a section headed
        "MOST AFFECTED" with green, fully-complete maps. Only rows that are
        genuinely affected qualify; when none are, the section is empty and
        the card says so.
        """

        affected = [row for row in self.rows if row.is_affected]
        return sorted(affected, key=lambda row: row.score, reverse=True)[:5]

    @property
    def has_affected_rows(self) -> bool:
        return any(row.is_affected for row in self.rows)


@dataclass(slots=True)
class DashboardFilterModel:
    label: str
    count: int
    tab: str
    query: str
    tooltip: str


@dataclass(slots=True)
class WarningRowModel:
    severity: str  # "info" | "warning" | "error"
    sample: str | None
    message: str


@dataclass(slots=True)
class WarningGroupModel:
    label: str
    count: int
    severity: str  # "info" | "warning" | "error"
    items: list[str]


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
    image_size: tuple[int, int] | None  # pixels
    pixel_size_um: float | None  # display pixel size in micrometres
    tile_count: int
    acquisition_time: str | None


@dataclass(slots=True)
class AtlasSummaryModel:
    """Atlas-side summary surfaced on the dashboard.

    Distinct from the data-collection summary so atlas-only sessions still
    render meaningful information instead of a row of zero-count cards.

    The model carries two views of "how many atlases":

    * :attr:`counts` — the four-way breakdown
      (:class:`AtlasCountModel`) used by the dashboard widget and the
      PDF cover. This is the corrected, deduplicated number set.
    * :attr:`atlas_count` and :attr:`sample_count_with_atlas` — kept
      for backwards compatibility with older callers that read these
      fields directly. Both are now sourced from
      :class:`AtlasCountModel` so they agree with the four-way counts.

    The legacy ``atlas_count`` is the **sample atlas** count (atlases
    linked to data-collection samples) — the value the user expects to
    see in the "Atlases" header. It is NOT inflated by per-sample
    Atlas/ folders that belong to a screening session with no linked
    data collection.
    """

    atlas_count: int
    sample_count_with_atlas: int
    rows: list[AtlasRowModel]
    pixel_size_range_um: tuple[float, float] | None  # min, max across atlases
    image_size_range: tuple[tuple[int, int], tuple[int, int]] | None  # (smallest, largest)
    counts: AtlasCountModel = field(default_factory=AtlasCountModel)


@dataclass(slots=True)
class DashboardModel:
    """Aggregate model that drives the Session-tab dashboard.

    Two of the three high-level sections are independently optional:

    * ``has_atlases`` toggles the Atlas Summary block.
    * ``has_collection_data`` toggles the counts row, the timeline, defocus
      and dose charts, and the search-map and batch-position cards.

    Atlas-only sessions render only the Atlas Summary; data-collection-only
    sessions render only the Collection block; mixed sessions render both.
    The widget consumes these flags so we don't show empty zero-count cards
    on a session that was never going to acquire data.
    """

    title: str
    path: str
    kind: str
    microscope: str | None
    acquisition_start: str | None
    acquisition_end: str | None
    acquisition_duration: str | None  # human-readable elapsed time, e.g. "14 hours and 4 minutes"
    completeness: str  # "complete" | "warning" | "error"
    completeness_label: str
    counts: list[StatCardModel]
    outcomes: TiltSeriesOutcomeModel
    defocus_readout: DefocusPlotModel
    dose_information: DoseInformationPlotModel
    search_map_overview: SearchMapOverviewModel
    filters: list[DashboardFilterModel]
    warnings: list[WarningRowModel]
    warning_groups: list[WarningGroupModel]
    batch_positions: list[BatchPositionProgressModel]
    timeline_items: dict[str, TimelineItemModel]
    sample_count: int
    is_sample_scope: bool
    has_atlases: bool
    has_collection_data: bool
    atlas_summary: AtlasSummaryModel | None


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
    warnings: list[str]
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
