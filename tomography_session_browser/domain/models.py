from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tomography_session_browser.domain.enums import SessionKind


Metadata = dict[str, Any]


@dataclass(slots=True)
class FileReference:
    path: Path
    kind: str
    exists: bool = True
    metadata: Metadata = field(default_factory=dict)


@dataclass(slots=True)
class MrcMetadata:
    path: Path
    size_bytes: int
    nx: int | None = None
    ny: int | None = None
    nz: int | None = None
    mode: int | None = None
    is_stack: bool = False
    extended_header_bytes: int = 0
    extended_header_type: str | None = None
    tilt_angles: list[float] = field(default_factory=list)
    tilt_angle_source: str | None = None
    voxel_size: tuple[float | None, float | None, float | None] = (None, None, None)
    frame_metadata: list[Metadata] = field(default_factory=list)
    parser_name: str | None = None
    parser_version: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MdocSection:
    z_value: int
    metadata: Metadata = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Atlas:
    id: str
    image_path: Path | None = None
    mrc_metadata: MrcMetadata | None = None
    metadata: Metadata = field(default_factory=dict)
    tile_paths: list[Path] = field(default_factory=list)
    tile_metadata_paths: list[Path] = field(default_factory=list)
    alignment_paths: list[Path] = field(default_factory=list)
    linked_overview_ids: list[str] = field(default_factory=list)
    linked_search_map_ids: list[str] = field(default_factory=list)
    linked_batch_position_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Overview:
    id: str
    name: str
    image_path: Path
    mrc_metadata: MrcMetadata | None = None
    metadata: Metadata = field(default_factory=dict)
    linked_search_map_ids: list[str] = field(default_factory=list)
    linked_batch_position_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SearchMap:
    id: str
    name: str
    image_path: Path | None = None
    mrc_path: Path | None = None
    xml_path: Path | None = None
    dm_path: Path | None = None
    overview: Overview | None = None
    mrc_metadata: MrcMetadata | None = None
    tile_paths: list[Path] = field(default_factory=list)
    tile_metadata_paths: list[Path] = field(default_factory=list)
    metadata: Metadata = field(default_factory=dict)
    linked_batch_position_ids: list[str] = field(default_factory=list)
    linked_tilt_series_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SearchTile:
    id: str
    name: str
    image_path: Path | None = None
    xml_path: Path | None = None
    mrc_metadata: MrcMetadata | None = None
    metadata: Metadata = field(default_factory=dict)
    search_map_id: str | None = None
    search_map_name: str | None = None
    batch_position_id: str | None = None
    batch_position_name: str | None = None
    tile_index: int | None = None
    acquisition_time: str | None = None
    link_method: str | None = None
    linked_tilt_series_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BatchPosition:
    id: str
    name: str | None = None
    status: str | None = None
    overview_image_path: Path | None = None
    search_image_path: Path | None = None
    search_metadata_path: Path | None = None
    tracking_image_path: Path | None = None
    exposure_image_path: Path | None = None
    exposure_image_paths: list[Path] = field(default_factory=list)
    search_mrc_metadata: MrcMetadata | None = None
    tracking_mrc_metadata: MrcMetadata | None = None
    exposure_mrc_metadata: MrcMetadata | None = None
    metadata: Metadata = field(default_factory=dict)
    linked_tilt_series_ids: list[str] = field(default_factory=list)
    linked_search_map_id: str | None = None
    linked_search_tile_id: str | None = None
    linked_overview_id: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TiltSeries:
    id: str
    name: str
    mrc_path: Path
    mdoc_path: Path | None = None
    tilt_count: int | None = None
    tilt_range: tuple[float, float] | None = None
    pixel_size: float | None = None
    original_pixel_size: float | None = None
    binning: int | None = None
    defocus: float | None = None
    target_defocus: float | None = None
    number_of_frames: int | None = None
    acquisition_time_start: str | None = None
    acquisition_time_end: str | None = None
    metadata: Metadata = field(default_factory=dict)
    mrc_metadata: MrcMetadata | None = None
    sections: list[MdocSection] = field(default_factory=list)
    linked_batch_position_id: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Sample:
    id: str
    name: str
    path: Path
    atlas: Atlas | None = None
    overviews: list[Overview] = field(default_factory=list)
    search_maps: list[SearchMap] = field(default_factory=list)
    search_tiles: list[SearchTile] = field(default_factory=list)
    batch_positions: list[BatchPosition] = field(default_factory=list)
    tilt_series: list[TiltSeries] = field(default_factory=list)
    metadata: Metadata = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Session:
    id: str
    name: str
    path: Path
    kind: SessionKind = SessionKind.UNKNOWN
    atlas: Atlas | None = None
    overviews: list[Overview] = field(default_factory=list)
    search_maps: list[SearchMap] = field(default_factory=list)
    search_tiles: list[SearchTile] = field(default_factory=list)
    batch_positions: list[BatchPosition] = field(default_factory=list)
    tilt_series: list[TiltSeries] = field(default_factory=list)
    samples: list[Sample] = field(default_factory=list)
    metadata_summary: Metadata = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Project:
    id: str
    name: str
    sessions: list[Session] = field(default_factory=list)
