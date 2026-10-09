"""Preview path precedence, shared by preparation and the image viewer.

Selects sources without loading pixels or creating Qt objects. Microscope
folders are inspected only for a matching MRC file and are never modified.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable

from tomography_session_browser.domain.models import (
    Atlas, BatchPosition, Overview, SearchMap, SearchTile, TiltSeries,
)


@dataclass(frozen=True, slots=True)
class PreviewSources:
    primary: Path | None
    fallback: Path | None = None


def overview_preview_path(overview: Overview, *, exists: Callable[[Path], bool] | None = None) -> PreviewSources:
    return _prefer_matching_mrc(overview.image_path, exists)


def search_map_preview_path(search_map: SearchMap, *, exists: Callable[[Path], bool] | None = None) -> PreviewSources:
    return PreviewSources(search_map.mrc_path or search_map.image_path, search_map.image_path if search_map.mrc_path else None)


def search_tile_preview_path(search_tile: SearchTile, *, exists: Callable[[Path], bool] | None = None) -> PreviewSources:
    return _prefer_matching_mrc(search_tile.image_path, exists)


def batch_position_preview_path(batch_position: BatchPosition, *, exists: Callable[[Path], bool] | None = None) -> PreviewSources:
    preferred = (
        batch_position.search_image_path
        or batch_position.tracking_image_path
        or batch_position.exposure_image_path
        or next(iter(batch_position.exposure_image_paths), None)
        or batch_position.overview_image_path
    )
    return _prefer_matching_mrc(preferred, exists)


def tilt_series_preview_path(tilt_series: TiltSeries, *, exists: Callable[[Path], bool] | None = None) -> PreviewSources:
    return PreviewSources(tilt_series.mrc_path)


def atlas_preview_path(atlas: Atlas, *, exists: Callable[[Path], bool] | None = None) -> PreviewSources:
    return _prefer_matching_mrc(atlas.image_path, exists)


def _prefer_matching_mrc(path: Path | None, exists: Callable[[Path], bool] | None) -> PreviewSources:
    if path is None:
        return PreviewSources(None)
    if path.suffix.lower() == ".mrc":
        return PreviewSources(path)
    mrc_path = path.with_suffix(".mrc")
    if mrc_path.exists() if exists is None else exists(mrc_path):
        return PreviewSources(mrc_path, path)
    return PreviewSources(path)
