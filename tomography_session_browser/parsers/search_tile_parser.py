from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import re
from typing import Any

from tomography_session_browser.domain.markers import MarkerType
from tomography_session_browser.domain.models import BatchPosition, SearchMap, SearchTile, TiltSeries
from tomography_session_browser.parsers.mrc_parser import read_mrc_metadata
from tomography_session_browser.parsers.path_utils import natural_key, sorted_paths
from tomography_session_browser.parsers.xml_parser import find_all, find_first, parse_xml_file
from tomography_session_browser.domain.markers import ImageMarker
from tomography_session_browser.services.marker_service import (
    ImageFrame,
    _image_frame,
    _stage_to_image,
    reflect_point_through_image_center,
    search_map_markers,
)
from tomography_session_browser.services.timeline_service import datetime_sort_key


@dataclass(frozen=True, slots=True)
class _TileCandidate:
    image_path: Path | None
    xml_path: Path
    metadata: dict[str, Any]
    stage_position: tuple[float, float] | None
    acquisition_time: str | None
    tile_index: int | None
    image_size: tuple[int, int] | None
    pixel_size: float | None


@dataclass(frozen=True, slots=True)
class _ExposureTarget:
    display_name: str
    marker_area_name: str
    stage_position: tuple[float, float]
    raw: dict[str, Any] | None = None
    is_primary: bool = False


def build_search_tiles(
    *,
    search_maps: list[SearchMap],
    batch_positions: list[BatchPosition],
    tilt_series: list[TiltSeries],
) -> list[SearchTile]:
    """Build exposure-linked search-map tile rows for the Search tab.

    Tomography records the batch/exposure target in ``BatchPositionsList.xml``
    and the tile images in ``SearchMaps/SearchMap_*/Tile_*.xml``. In observed
    TFS output the batch search XML has the same acquisition timestamp as the
    source tile. We prefer that explicit timestamp match and only then fall
    back to a stage-position containment/nearest match.

    Performance: the search-map overlay projection used to compute
    :func:`search_map_markers` once per ``(batch, exposure target)`` pair. On
    large multigrid samples that scanned to ~6 s. Markers per batch are
    independent (each batch's grouped overlay correction is scoped to its own
    template markers), so we precompute the markers once per search map with
    *all* batches on that map, then index by ``(batch_id, area_name)`` for
    O(1) lookup inside the per-target loop. Output is identical to the
    per-batch invocation.
    """

    maps_by_id = {search_map.id: search_map for search_map in search_maps}
    tile_cache = {search_map.id: _tile_candidates(search_map) for search_map in search_maps}
    tilts_by_batch = _tilts_by_batch(batch_positions, tilt_series)
    marker_index_by_map, search_frame_by_map = _precompute_search_map_marker_index(
        search_maps=search_maps,
        batch_positions=batch_positions,
        tilt_series=tilt_series,
    )
    rows: list[SearchTile] = []
    seen: set[str] = set()

    for batch in batch_positions:
        if not batch.linked_search_map_id:
            continue
        search_map = maps_by_id.get(batch.linked_search_map_id)
        if search_map is None:
            continue
        linked_tilts = tilts_by_batch.get(batch.id, [])
        batch_metadata = _batch_search_metadata(batch)
        candidates = tile_cache.get(search_map.id, [])
        marker_index = marker_index_by_map.get(search_map.id, {})
        search_frame = search_frame_by_map.get(search_map.id)
        for target in _exposure_targets(batch):
            target_tilts = _tilts_for_target(target, batch, linked_tilts)
            candidate, method = _match_tile_for_target(
                candidates,
                search_map,
                batch,
                batch_metadata,
                target,
                linked_tilts,
                marker_index=marker_index,
                search_frame=search_frame,
            )
            row = _search_tile_from_match(
                search_map=search_map,
                batch=batch,
                target=target,
                candidate=candidate,
                method=method,
                batch_metadata=batch_metadata,
                linked_tilts=target_tilts,
            )
            if row.id in seen:
                continue
            seen.add(row.id)
            if target.is_primary:
                batch.linked_search_tile_id = row.id
            rows.append(row)

    rows.sort(
        key=lambda item: (
            natural_key(item.search_map_name or ""),
            item.tile_index or 0,
            natural_key(item.batch_position_name or item.name),
            natural_key(item.name),
        )
    )
    return rows


def _tile_candidates(search_map: SearchMap) -> list[_TileCandidate]:
    image_by_stem: dict[str, Path] = {}
    for path in sorted_paths(search_map.tile_paths):
        current = image_by_stem.get(path.stem)
        if current is None or (current.suffix.lower() != ".mrc" and path.suffix.lower() == ".mrc"):
            image_by_stem[path.stem] = path

    candidates: list[_TileCandidate] = []
    for xml_path in sorted_paths(search_map.tile_metadata_paths):
        metadata = parse_xml_file(xml_path)
        candidates.append(
            _TileCandidate(
                image_path=image_by_stem.get(xml_path.stem),
                xml_path=xml_path,
                metadata=metadata,
                stage_position=_stage_position(metadata),
                acquisition_time=_as_str(find_first(metadata, "acquisitionDateTime")),
                tile_index=_tile_index(xml_path),
                image_size=_image_size(metadata),
                pixel_size=_pixel_size(metadata),
            )
        )
    return candidates


def _batch_search_metadata(batch: BatchPosition) -> dict[str, Any]:
    if batch.search_metadata_path is not None and batch.search_metadata_path.exists():
        return parse_xml_file(batch.search_metadata_path)
    return {}


def _match_tile_for_target(
    candidates: list[_TileCandidate],
    search_map: SearchMap,
    batch: BatchPosition,
    batch_metadata: dict[str, Any],
    target: _ExposureTarget,
    linked_tilts: list[TiltSeries],
    *,
    marker_index: dict[tuple[str, str], ImageMarker] | None = None,
    search_frame: ImageFrame | None = None,
) -> tuple[_TileCandidate | None, str | None]:
    # Keep Search-tab tile membership in the same display coordinate system as
    # the Search Map tab. Tomography 5 search-map overlays are reflected and
    # locally corrected before display; matching tiles in raw stage space can
    # assign multi-exposure areas to a different tile than the one users see.
    projected = _match_tile_from_search_map_projection(
        candidates,
        search_map,
        batch,
        target,
        linked_tilts,
        marker_index=marker_index,
        search_frame=search_frame,
    )
    if projected[0] is not None:
        return projected
    return _match_tile(
        candidates,
        batch,
        batch_metadata if target.is_primary else {},
        stage_position=target.stage_position,
        prefer_time=target.is_primary,
    )


def _match_tile(
    candidates: list[_TileCandidate],
    batch: BatchPosition,
    batch_metadata: dict[str, Any],
    *,
    stage_position: tuple[float, float] | None = None,
    prefer_time: bool = True,
) -> tuple[_TileCandidate | None, str | None]:
    if not candidates:
        return None, None

    batch_time = _as_str(find_first(batch_metadata, "acquisitionDateTime"))
    batch_stage = stage_position or _stage_position(batch_metadata) or _position_on_tile_set(batch)

    if prefer_time and batch_time:
        exact = [candidate for candidate in candidates if candidate.acquisition_time == batch_time]
        if len(exact) == 1:
            return exact[0], "batch search acquisition time"
        if len(exact) > 1 and batch_stage is not None:
            return _nearest_tile(exact, batch_stage), "batch search acquisition time + nearest stage"

    if batch_stage is not None:
        containing = [
            candidate
            for candidate in candidates
            if _contains_stage(candidate, batch_stage)
        ]
        if len(containing) == 1:
            return containing[0], "stage position within tile"
        if containing:
            return _nearest_tile(containing, batch_stage), "stage position within nearest tile"
        nearest = _nearest_tile(candidates, batch_stage)
        if nearest is not None and _stage_distance(nearest, batch_stage) <= _tile_half_diagonal(nearest) * 1.15:
            return nearest, "nearest stage position"

    return None, None


def _match_tile_from_search_map_projection(
    candidates: list[_TileCandidate],
    search_map: SearchMap,
    batch: BatchPosition,
    target: _ExposureTarget,
    linked_tilts: list[TiltSeries],
    *,
    marker_index: dict[tuple[str, str], ImageMarker] | None = None,
    search_frame: ImageFrame | None = None,
) -> tuple[_TileCandidate | None, str | None]:
    if not candidates:
        return None, None
    # Prefer the precomputed marker index built once per search map. Fall
    # back to the original per-batch call when no index is supplied (e.g.
    # callers that don't go through :func:`build_search_tiles`).
    if search_frame is None:
        search_frame = _image_frame(search_map)
    if search_frame is None:
        return None, None
    marker: ImageMarker | None = None
    if marker_index is not None:
        marker = marker_index.get((batch.id, _normalise_name(target.marker_area_name)))
    else:
        for item in search_map_markers(search_map, [batch], tilt_series=linked_tilts):
            if (
                item.marker_type == MarkerType.EXPOSURE_AREA
                and item.linked_object_id == batch.id
                and _normalise_name(item.metadata.get("area_name")) == _normalise_name(target.marker_area_name)
                and item.x is not None
                and item.y is not None
            ):
                marker = item
                break
    if marker is None or marker.x is None or marker.y is None:
        return None, None
    hits = [
        candidate
        for candidate in candidates
        if _search_map_marker_in_tile(search_frame, candidate, marker.x, marker.y)
    ]
    if len(hits) == 1:
        return hits[0], "search-map overlay projection"
    if hits:
        return _nearest_projected_tile(search_frame, hits, marker.x, marker.y), "search-map overlay projection + nearest tile"
    return None, None


def _precompute_search_map_marker_index(
    *,
    search_maps: list[SearchMap],
    batch_positions: list[BatchPosition],
    tilt_series: list[TiltSeries],
) -> tuple[dict[str, dict[tuple[str, str], ImageMarker]], dict[str, ImageFrame | None]]:
    """Build a per-search-map ``{(batch_id, normalised_area_name): marker}``
    index for ``MarkerType.EXPOSURE_AREA`` markers.

    Calling :func:`search_map_markers` once per ``(batch, exposure target)``
    repeated the same per-batch marker construction work many times on
    multigrid samples; calling it once per search map with all batches is
    equivalent because the grouped overlay correction is scoped per-batch
    inside :func:`_template_markers_in_frame`.
    """

    marker_index: dict[str, dict[tuple[str, str], ImageMarker]] = {}
    search_frame_by_map: dict[str, ImageFrame | None] = {}
    if not search_maps or not batch_positions:
        return marker_index, search_frame_by_map

    batches_by_map: dict[str, list[BatchPosition]] = {}
    for batch in batch_positions:
        if batch.linked_search_map_id:
            batches_by_map.setdefault(batch.linked_search_map_id, []).append(batch)
    if not batches_by_map:
        return marker_index, search_frame_by_map

    for search_map in search_maps:
        if search_map.id not in batches_by_map:
            continue
        frame = _image_frame(search_map)
        search_frame_by_map[search_map.id] = frame
        if frame is None:
            marker_index[search_map.id] = {}
            continue
        # `failed_tilt_ids` is not set when building search tiles, so the
        # inferred-failed-tilt fallback inside `search_map_markers` is a
        # no-op. Pass the full tilt list so the call site is symmetric
        # with how the GUI builds markers later.
        all_markers = search_map_markers(
            search_map,
            batches_by_map[search_map.id],
            tilt_series=tilt_series,
        )
        idx: dict[tuple[str, str], ImageMarker] = {}
        for marker in all_markers:
            if (
                marker.marker_type == MarkerType.EXPOSURE_AREA
                and marker.linked_object_id
                and marker.x is not None
                and marker.y is not None
            ):
                area = _normalise_name(marker.metadata.get("area_name"))
                idx[(marker.linked_object_id, area)] = marker
        marker_index[search_map.id] = idx
    return marker_index, search_frame_by_map


def _search_tile_from_match(
    *,
    search_map: SearchMap,
    batch: BatchPosition,
    target: _ExposureTarget,
    candidate: _TileCandidate | None,
    method: str | None,
    batch_metadata: dict[str, Any],
    linked_tilts: list[TiltSeries],
) -> SearchTile:
    tile_stem = candidate.xml_path.stem if candidate is not None else "unresolved"
    tile_index = candidate.tile_index if candidate is not None else None
    name = _display_name(search_map.name, tile_index, target.display_name)
    metadata: dict[str, Any] = {
        "SearchMapName": search_map.name,
        "BatchPositionName": batch.name or batch.id,
        "BatchPositionId": batch.id,
        "SearchMapId": search_map.id,
        "BatchPosition": batch.metadata,
        "ExposureDisplayName": target.display_name,
        "ExposureAreaName": target.marker_area_name,
        "ExposureStagePosition": target.stage_position,
        "ExposureTemplateArea": target.raw,
        "ExposureIsPrimary": target.is_primary,
    }
    if candidate is not None:
        metadata["Tile.xml"] = candidate.metadata
        metadata["TileStagePosition"] = candidate.stage_position
    if batch_metadata:
        metadata["BatchSearch.xml"] = batch_metadata
    warnings: list[str] = []
    if candidate is None:
        warnings.append("No matching search-map tile could be resolved for this exposure area.")
    elif candidate.image_path is None:
        warnings.append("Matched search-map tile has no displayable MRC or JPG image.")
    if method and "nearest" in method.casefold():
        warnings.append(f"Search-map tile association used fallback matching: {method}.")
    return SearchTile(
        id=f"{search_map.id}:search-tile:{tile_stem}:batch:{batch.id}:exposure:{safe_token(target.display_name)}",
        name=name,
        image_path=candidate.image_path if candidate is not None else None,
        xml_path=candidate.xml_path if candidate is not None else None,
        mrc_metadata=read_mrc_metadata(candidate.image_path)
        if candidate is not None and candidate.image_path is not None and candidate.image_path.suffix.lower() == ".mrc"
        else None,
        metadata=metadata,
        search_map_id=search_map.id,
        search_map_name=search_map.name,
        batch_position_id=batch.id,
        batch_position_name=batch.name or batch.id,
        tile_index=tile_index,
        acquisition_time=candidate.acquisition_time if candidate is not None else None,
        link_method=method,
        linked_tilt_series_ids=[tilt.id for tilt in linked_tilts],
        warnings=warnings,
    )


def _exposure_targets(batch: BatchPosition) -> list[_ExposureTarget]:
    base = _position_on_tile_set(batch)
    if base is None:
        return []
    targets: list[_ExposureTarget] = []
    primary_raw = batch.metadata.get("ExposureTemplateAreaParameters")
    primary_position = _target_stage_position(base, primary_raw)
    if primary_position is not None:
        targets.append(
            _ExposureTarget(
                display_name=batch.name or batch.id,
                marker_area_name="Exposure",
                stage_position=primary_position,
                raw=primary_raw if isinstance(primary_raw, dict) else None,
                is_primary=True,
            )
        )
    raw_areas = _exposure_area_parameters(batch.metadata.get("AdditionalExposureTemplateAreas"))
    if raw_areas:
        for index, raw in enumerate(raw_areas, start=2):
            if not isinstance(raw, dict) or raw.get("_attributes", {}).get("nil") == "true":
                continue
            position = _target_stage_position(base, raw)
            if position is None:
                continue
            name = _as_str(find_first(raw, "Name")) or f"{batch.name or batch.id}_{index}"
            targets.append(
                _ExposureTarget(
                    display_name=name,
                    marker_area_name=name,
                    stage_position=position,
                    raw=raw,
                    is_primary=False,
                )
            )
    return targets


def _exposure_area_parameters(additional: object) -> list[dict[str, Any]]:
    if not isinstance(additional, dict):
        return []
    areas: list[dict[str, Any]] = []
    for path, value in find_all(additional, "ExposureTemplateAreaParameters"):
        if not path or path[-1] != "ExposureTemplateAreaParameters":
            continue
        if len(path) > 1 and path[-2] not in {"AdditionalExposureTemplateAreas", "ExposureTemplateAreas"}:
            continue
        raw_values = value if isinstance(value, list) else [value]
        areas.extend(raw for raw in raw_values if isinstance(raw, dict))
    return areas


def _target_stage_position(base: tuple[float, float], raw: object) -> tuple[float, float] | None:
    if not isinstance(raw, dict) or raw.get("_attributes", {}).get("nil") == "true":
        return None
    x = _as_float(raw.get("PositionX")) or 0.0
    y = _as_float(raw.get("PositionY")) or 0.0
    return base[0] + x, base[1] + y


def _tilts_for_target(
    target: _ExposureTarget,
    batch: BatchPosition,
    linked_tilts: list[TiltSeries],
) -> list[TiltSeries]:
    target_key = _normalise_name(batch.name or batch.id if target.is_primary else target.display_name)
    exact = [tilt for tilt in linked_tilts if _normalise_name(tilt.name) == target_key]
    return exact


def _tilts_by_batch(
    batch_positions: list[BatchPosition],
    tilt_series: list[TiltSeries],
) -> dict[str, list[TiltSeries]]:
    batch_by_id = {batch.id: batch for batch in batch_positions}
    out: dict[str, list[TiltSeries]] = {batch.id: [] for batch in batch_positions}
    for tilt in tilt_series:
        if tilt.linked_batch_position_id in batch_by_id:
            out.setdefault(tilt.linked_batch_position_id, []).append(tilt)
    for batch in batch_positions:
        known = {tilt.id for tilt in out.get(batch.id, [])}
        for tilt in tilt_series:
            if tilt.id in known or tilt.id not in (batch.linked_tilt_series_ids or []):
                continue
            out.setdefault(batch.id, []).append(tilt)
            known.add(tilt.id)
    for rows in out.values():
        rows.sort(
            key=lambda tilt: (
                *datetime_sort_key(tilt.acquisition_time_start),
                natural_key(tilt.name or tilt.id),
            )
        )
    return out


def _display_name(search_map_name: str, tile_index: int | None, batch_name: str) -> str:
    tile = f"Tile {tile_index}" if tile_index is not None else "Tile"
    return f"{batch_name} · {tile} · {search_map_name}"


def _search_map_marker_in_tile(
    frame: Any,
    candidate: _TileCandidate,
    x: float,
    y: float,
) -> bool:
    bounds = _tile_bounds_in_search_map(frame, candidate)
    if bounds is None:
        return False
    left, top, width, height = bounds
    return left <= x <= left + width and top <= y <= top + height


def _nearest_projected_tile(
    frame: Any,
    candidates: list[_TileCandidate],
    x: float,
    y: float,
) -> _TileCandidate | None:
    scored = []
    for candidate in candidates:
        bounds = _tile_bounds_in_search_map(frame, candidate)
        if bounds is None:
            continue
        left, top, width, height = bounds
        center = (left + width / 2, top + height / 2)
        distance = ((center[0] - x) ** 2 + (center[1] - y) ** 2) ** 0.5
        scored.append((distance, candidate))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0])
    return scored[0][1]


def _tile_bounds_in_search_map(frame: Any, candidate: _TileCandidate) -> tuple[float, float, float, float] | None:
    if candidate.stage_position is None or candidate.image_size is None or candidate.pixel_size is None:
        return None
    center = reflect_point_through_image_center(
        *_stage_to_image(frame, candidate.stage_position),
        frame.image_size[0],
        frame.image_size[1],
    )
    width = candidate.image_size[0] * candidate.pixel_size / frame.pixel_size
    height = candidate.image_size[1] * candidate.pixel_size / frame.pixel_size
    if width <= 0 or height <= 0:
        return None
    return center[0] - width / 2, center[1] - height / 2, width, height


def _nearest_tile(
    candidates: list[_TileCandidate],
    stage_position: tuple[float, float],
) -> _TileCandidate | None:
    scored = [
        (_stage_distance(candidate, stage_position), candidate)
        for candidate in candidates
        if candidate.stage_position is not None
    ]
    if not scored:
        return None
    scored.sort(key=lambda item: item[0])
    return scored[0][1]


def _contains_stage(candidate: _TileCandidate, stage_position: tuple[float, float]) -> bool:
    if candidate.stage_position is None:
        return False
    return _stage_distance(candidate, stage_position) <= _tile_half_diagonal(candidate)


def _stage_distance(candidate: _TileCandidate, stage_position: tuple[float, float]) -> float:
    if candidate.stage_position is None:
        return float("inf")
    return (
        ((candidate.stage_position[0] - stage_position[0]) ** 2)
        + ((candidate.stage_position[1] - stage_position[1]) ** 2)
    ) ** 0.5


def _tile_half_diagonal(candidate: _TileCandidate) -> float:
    if candidate.image_size is None or candidate.pixel_size is None:
        return 0.0
    width, height = candidate.image_size
    return (((width * candidate.pixel_size) ** 2) + ((height * candidate.pixel_size) ** 2)) ** 0.5 / 2.0


def _position_on_tile_set(batch: BatchPosition) -> tuple[float, float] | None:
    value = batch.metadata.get("PositionOnTileSet")
    if not isinstance(value, dict):
        return None
    x = _as_float(value.get("StagePositionX"))
    y = _as_float(value.get("StagePositionY"))
    return (x, y) if x is not None and y is not None else None


def _stage_position(metadata: dict[str, Any]) -> tuple[float, float] | None:
    value = find_first(metadata, "Position")
    if not isinstance(value, dict):
        return None
    x = _as_float(value.get("X"))
    y = _as_float(value.get("Y"))
    return (x, y) if x is not None and y is not None else None


def _image_size(metadata: dict[str, Any]) -> tuple[int, int] | None:
    value = find_first(metadata, "ReadoutArea")
    if isinstance(value, dict):
        width = _as_int(value.get("width"))
        height = _as_int(value.get("height"))
        if width is not None and height is not None and width > 0 and height > 0:
            return width, height
    value = find_first(metadata, "ImageSize")
    if isinstance(value, dict):
        width = _as_int(value.get("width"))
        height = _as_int(value.get("height"))
        if width is not None and height is not None and width > 0 and height > 0:
            return width, height
    return None


def _pixel_size(metadata: dict[str, Any]) -> float | None:
    value = find_first(metadata, "pixelSize")
    if not isinstance(value, dict):
        return None
    x_value = value.get("x")
    if isinstance(x_value, dict):
        numeric = _as_float(x_value.get("numericValue"))
        if numeric is not None and numeric > 0:
            return numeric
    return None


def _tile_index(path: Path) -> int | None:
    match = re.search(r"_(\d+)$", path.stem)
    return int(match.group(1)) if match else None


def _as_float(value: Any) -> float | None:
    if isinstance(value, int | float):
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, int):
        return int(value)
    return None


def _as_str(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _normalise_name(value: Any) -> str:
    return str(value or "").strip().casefold()


def safe_token(value: Any) -> str:
    text = str(value or "").strip()
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text) or "exposure"
