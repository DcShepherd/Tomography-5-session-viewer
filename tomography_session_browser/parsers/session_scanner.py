from __future__ import annotations

import logging
from pathlib import Path
import re
from typing import Callable, TypeVar
from contextlib import nullcontext

from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.models import Overview, Sample, Session
from tomography_session_browser.parsers.atlas_parser import parse_atlas_folder
from tomography_session_browser.parsers.batch_parser import parse_batch_folder
from tomography_session_browser.parsers.mrc_parser import read_mrc_metadata
from tomography_session_browser.parsers.path_utils import is_sample_dir, safe_id, sorted_paths
from tomography_session_browser.parsers.searchmap_parser import parse_search_map_folder
from tomography_session_browser.parsers.search_tile_parser import build_search_tiles
from tomography_session_browser.parsers.tiltseries_parser import parse_tilt_series_in_folder
from tomography_session_browser.parsers.xml_parser import find_first, parse_xml_file
from tomography_session_browser.services.acquisition_metadata import (
    ACQUISITION_METADATA_KEY,
    merge_acquisition_settings_metadata,
)
from tomography_session_browser.services.loading_profiler import LoadingProfiler

LOGGER = logging.getLogger(__name__)
T = TypeVar("T")


class SessionScanner:
    """Read-only scanner for Tomography 5 session-like folders."""

    def classify(self, path: Path) -> SessionKind:
        path = Path(path)
        if (path / "ScreeningSession.dm").exists():
            return SessionKind.ATLAS_SCREENING
        sample_dirs = self._sample_dirs(path)
        if (path / "Session.dm").exists() and sample_dirs:
            return SessionKind.MULTIGRID
        if self._looks_like_collection(path):
            return SessionKind.COLLECTION
        if self._looks_like_single_collection(path):
            return SessionKind.SINGLE_COLLECTION
        return SessionKind.UNKNOWN

    def load(self, path: Path, *, profile: LoadingProfiler | None = None) -> Session:
        root = Path(path)
        with _profile_phase(profile, "classify_layout"):
            kind = self.classify(root)
        session = Session(id=safe_id(root), name=root.name, path=root, kind=kind)

        if kind == SessionKind.ATLAS_SCREENING:
            with _profile_phase(profile, "parse_session_xml", count=1):
                session.metadata_summary["ScreeningSession.dm"] = parse_xml_file(root / "ScreeningSession.dm")
            sample_dirs = self._sample_dirs(root)
            with _profile_phase(profile, "load_screening_samples", count=len(sample_dirs)):
                session.samples = [self._load_screening_sample(sample_dir, profile=profile) for sample_dir in sample_dirs]
            root_atlas = _safe_parse(
                "Root Atlas",
                lambda: _profiled_parse(profile, "parse_root_atlas", lambda: parse_atlas_folder(root / "Atlas")),
                None,
                session.warnings,
            )
            if root_atlas:
                session.atlas = root_atlas
        elif kind == SessionKind.MULTIGRID:
            with _profile_phase(profile, "parse_session_xml", count=1):
                session.metadata_summary["Session.dm"] = parse_xml_file(root / "Session.dm")
            sample_dirs = self._sample_dirs(root)
            with _profile_phase(profile, "load_collection_samples", count=len(sample_dirs)):
                session.samples = [self._load_collection_sample(sample_dir, profile=profile) for sample_dir in sample_dirs]
        elif kind in {SessionKind.COLLECTION, SessionKind.SINGLE_COLLECTION}:
            sample = self._load_collection_sample(root, profile=profile)
            session.samples = [sample]
            session.atlas = sample.atlas
            session.overviews = sample.overviews
            session.search_maps = sample.search_maps
            session.batch_positions = sample.batch_positions
            session.tilt_series = sample.tilt_series
            if (root / "Session.dm").exists():
                with _profile_phase(profile, "parse_session_xml", count=1):
                    session.metadata_summary["Session.dm"] = parse_xml_file(root / "Session.dm")
        else:
            session.warnings.append("Folder did not match a known Tomography 5 layout.")

        with _profile_phase(profile, "summarise_counts"):
            session.metadata_summary["counts"] = self._counts(session)
        return session

    def _load_screening_sample(self, path: Path, *, profile: LoadingProfiler | None = None) -> Sample:
        sample = Sample(id=safe_id(path), name=path.name, path=path)
        sample_dm = path / "Sample.dm"
        if sample_dm.exists():
            with _profile_phase(profile, "parse_sample_xml", count=1):
                sample.metadata["Sample.dm"] = parse_xml_file(sample_dm)
        sample.name = self._sample_display_name(path, sample.metadata.get("Sample.dm"), [])
        sample.atlas = _safe_parse(
            f"{sample.name} Atlas",
            lambda: _profiled_parse(profile, "parse_sample_atlas", lambda: parse_atlas_folder(path / "Atlas")),
            None,
            sample.warnings,
        )
        if sample.atlas is None:
            sample.warnings.append("Screening sample has no parseable Atlas folder.")
        return sample

    def _load_collection_sample(self, path: Path, *, profile: LoadingProfiler | None = None) -> Sample:
        sample = Sample(id=safe_id(path), name=path.name, path=path)
        session_dm = path / "Session.dm"
        if session_dm.exists():
            with _profile_phase(profile, "parse_sample_session_xml", count=1):
                sample.metadata["Session.dm"] = parse_xml_file(session_dm)

        sample.atlas = _safe_parse("Atlas", lambda: _profiled_parse(profile, "parse_atlas", lambda: parse_atlas_folder(path / "Atlas")), None, sample.warnings)
        sample.overviews = _safe_parse("Overviews", lambda: self._parse_overviews(path, sample.warnings, profile=profile), [], sample.warnings)
        sample.search_maps = _safe_parse("Search maps", lambda: self._parse_search_maps(path, sample.warnings, profile=profile), [], sample.warnings)
        sample.overviews.extend(search_map.overview for search_map in sample.search_maps if search_map.overview is not None)
        sample.batch_positions = _safe_parse("Batch positions", lambda: _profiled_parse(profile, "parse_batch_positions", lambda: parse_batch_folder(path / "Batch", sample.warnings)), [], sample.warnings)
        sample.tilt_series = _safe_parse("Tilt series", lambda: _profiled_parse(profile, "parse_tilt_series", lambda: parse_tilt_series_in_folder(path, sample.warnings)), [], sample.warnings)
        sample.name = self._sample_display_name(path, sample.metadata.get("Session.dm"), [tilt.name for tilt in sample.tilt_series])
        with _profile_phase(profile, "link_batch_positions"):
            self._link_batch_positions_to_search_maps(sample)
            self._link_batch_positions_to_tilt_series(sample)
        sample.search_tiles = _safe_parse(
            "Search tiles",
            lambda: _profiled_parse(
                profile,
                "build_search_tiles",
                lambda: build_search_tiles(
                    search_maps=sample.search_maps,
                    batch_positions=sample.batch_positions,
                    tilt_series=sample.tilt_series,
                ),
            ),
            [],
            sample.warnings,
        )
        return sample

    def _parse_search_maps(self, path: Path, warnings: list[str] | None = None, *, profile: LoadingProfiler | None = None):
        search_root = path / "SearchMaps"
        if not search_root.exists():
            return []
        try:
            with _profile_phase(profile, "discover_search_maps"):
                folders = [folder for folder in search_root.iterdir() if folder.is_dir() and folder.name.startswith("SearchMap_")]
        except OSError as exc:
            if warnings is not None:
                warnings.append(f"Could not list SearchMaps folder: {exc}")
            LOGGER.warning("Could not list SearchMaps folder %s: %s", search_root, exc)
            return []
        maps = []
        folders = sorted_paths(folders)
        with _profile_phase(profile, "parse_search_maps", count=len(folders)):
            for folder in folders:
                parsed = _safe_parse(f"Search map {folder.name}", lambda folder=folder: parse_search_map_folder(folder), None, warnings)
                if parsed is not None:
                    maps.append(parsed)
        return maps

    def _parse_overviews(self, path: Path, warnings: list[str] | None = None, *, profile: LoadingProfiler | None = None) -> list[Overview]:
        try:
            with _profile_phase(profile, "discover_overviews"):
                overview_paths = sorted_paths(list((path / "Batch").glob("Overview_*.mrc")) if (path / "Batch").exists() else [])
        except OSError as exc:
            if warnings is not None:
                warnings.append(f"Could not list Overview images: {exc}")
            LOGGER.warning("Could not list overview images under %s: %s", path, exc)
            return []
        overviews = []
        with _profile_phase(profile, "parse_overview_mrc_headers", count=len(overview_paths)):
            for item in overview_paths:
                parsed = _safe_parse(
                    f"Overview {item.name}",
                    lambda item=item: Overview(id=safe_id(item), name=item.stem, image_path=item, mrc_metadata=read_mrc_metadata(item)),
                    None,
                    warnings,
                )
                if parsed is not None:
                    overviews.append(parsed)
        return overviews

    def _sample_dirs(self, path: Path) -> list[Path]:
        if not path.exists():
            return []
        try:
            return sorted_paths([item for item in path.iterdir() if is_sample_dir(item)])
        except OSError as exc:
            LOGGER.warning("Could not list sample directories under %s: %s", path, exc)
            return []

    def _looks_like_collection(self, path: Path) -> bool:
        return any(
            [
                (path / "Session.dm").exists(),
                (path / "Batch").exists(),
                (path / "SearchMaps").exists(),
                any(path.glob("*.mdoc")),
                any(path.glob("*.mrc")),
            ]
        )

    def _looks_like_single_collection(self, path: Path) -> bool:
        return any(
            [
                (path / "Batch").exists(),
                (path / "SearchMaps").exists(),
                any(path.glob("Overview_*.mrc")),
                any(item.with_suffix(".mdoc").exists() for item in path.glob("*.mrc")),
            ]
        )

    def _link_batch_positions_to_tilt_series(self, sample: Sample) -> None:
        batch_by_name = {position.name: position for position in sample.batch_positions if position.name}
        batch_names = sorted(batch_by_name, key=len, reverse=True)
        for tilt in sample.tilt_series:
            batch = batch_by_name.get(tilt.name)
            if batch is None:
                # Strict prefix-only match. We deliberately don't
                # fuzzy-attach orphaned failed tilts (e.g. sang_1
                # whose batch was never recorded in
                # BatchPositionsList.xml) to a sibling batch — that
                # would tag the sibling's overlays as failed in the
                # GUI / PDF even though the sibling acquisition
                # succeeded. Orphans stay orphaned and simply don't
                # contribute to any search-map bar.
                batch = next(
                    (batch_by_name[name] for name in batch_names if tilt.name.startswith(f"{name}_")),
                    None,
                )
            if batch is None:
                continue
            tilt.linked_batch_position_id = batch.id
            if ACQUISITION_METADATA_KEY in batch.metadata:
                merge_acquisition_settings_metadata(tilt.metadata, batch.metadata, overwrite=False)
            if tilt.id not in batch.linked_tilt_series_ids:
                batch.linked_tilt_series_ids.append(tilt.id)
            search_map = next((item for item in sample.search_maps if item.id == batch.linked_search_map_id), None)
            if search_map is not None and tilt.id not in search_map.linked_tilt_series_ids:
                search_map.linked_tilt_series_ids.append(tilt.id)

    def _link_batch_positions_to_search_maps(self, sample: Sample) -> None:
        search_maps_by_name = {search_map.name: search_map for search_map in sample.search_maps}
        for batch in sample.batch_positions:
            tile_set_name = _tile_set_name(batch.metadata.get("PositionOnTileSet"))
            if tile_set_name is None:
                continue
            search_map = search_maps_by_name.get(tile_set_name)
            if search_map is None:
                continue
            batch.linked_search_map_id = search_map.id
            search_map.linked_batch_position_ids.append(batch.id)

    def _sample_display_name(self, path: Path, metadata: object, tilt_names: list[str]) -> str:
        index = _sample_index(path.name)
        label = _metadata_label(metadata)
        if label:
            cleaned = _clean_label(label, path.name)
            if cleaned != path.name:
                return _with_index(index, cleaned)
        fallback = _tilt_series_root_name(tilt_names)
        if fallback:
            return _with_index(index, fallback)
        return _with_index(index, path.name)

    def _counts(self, session: Session) -> dict[str, int]:
        samples = session.samples
        return {
            "samples": len(samples),
            "atlases": len(
                _dedupe_entities(
                    [atlas for atlas in [session.atlas, *(sample.atlas for sample in samples)] if atlas is not None]
                )
            ),
            "overviews": len(_dedupe_entities([*session.overviews, *(value for sample in samples for value in sample.overviews)])),
            "search_maps": len(_dedupe_entities([*session.search_maps, *(value for sample in samples for value in sample.search_maps)])),
            "search_tiles": len(_dedupe_entities([*session.search_tiles, *(value for sample in samples for value in sample.search_tiles)])),
            "batch_positions": len(_dedupe_entities([*session.batch_positions, *(value for sample in samples for value in sample.batch_positions)])),
            "tilt_series": len(_dedupe_entities([*session.tilt_series, *(value for sample in samples for value in sample.tilt_series)])),
        }


def _sample_index(name: str) -> int | None:
    match = re.search(r"(\d+)$", name)
    return int(match.group(1)) if match else None


def _with_index(index: int | None, label: str) -> str:
    return f"{index}. {label}" if index is not None else label


def _metadata_label(metadata: object) -> str | None:
    if not isinstance(metadata, dict):
        return None
    label = find_first(metadata, "Name")
    if isinstance(label, dict):
        label = label.get("value")
    if isinstance(label, str) and label.strip():
        return label.strip()
    return None


def _tile_set_name(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    tile_set_name = value.get("TileSetName")
    if isinstance(tile_set_name, str) and tile_set_name.strip():
        return tile_set_name.strip()
    return None


def _clean_label(label: str, folder_name: str) -> str:
    normalized = label.strip().replace("/", " / ")
    if normalized.lower().endswith(folder_name.lower()):
        return folder_name
    return normalized


def _tilt_series_root_name(tilt_names: list[str]) -> str | None:
    roots = [_strip_tilt_suffix(name) for name in tilt_names]
    roots = [root for root in roots if root]
    if not roots:
        return None
    counts: dict[str, int] = {}
    for root in roots:
        counts[root] = counts.get(root, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))[0][0]


def _strip_tilt_suffix(name: str) -> str:
    previous = name
    while True:
        next_value = re.sub(r"_\d+$", "", previous)
        if next_value == previous:
            return previous
        previous = next_value


def _safe_parse(label: str, parser: Callable[[], T], fallback: T, warnings: list[str] | None = None) -> T:
    try:
        return parser()
    except Exception as exc:  # pragma: no cover - defensive boundary for malformed user data
        message = f"{label} could not be parsed and was skipped: {exc}"
        if warnings is not None:
            warnings.append(message)
        LOGGER.warning(message, exc_info=True)
        return fallback


def _profile_phase(profile: LoadingProfiler | None, name: str, *, count: int = 0):
    return profile.phase(name, count=count) if profile is not None else nullcontext()


def _profiled_parse(profile: LoadingProfiler | None, name: str, parser: Callable[[], T]) -> T:
    with _profile_phase(profile, name):
        return parser()


def _dedupe_entities(values: list[object]) -> list[object]:
    unique: list[object] = []
    seen: set[tuple[str, str]] = set()
    for value in values:
        key = _entity_identity_key(value)
        if key in seen:
            continue
        seen.add(key)
        unique.append(value)
    return unique


def _entity_identity_key(value: object) -> tuple[str, str]:
    for attr in (
        "mrc_path",
        "mdoc_path",
        "xml_path",
        "dm_path",
        "image_path",
        "search_metadata_path",
        "overview_image_path",
        "search_image_path",
        "tracking_image_path",
        "exposure_image_path",
        "path",
    ):
        path = getattr(value, attr, None)
        if path:
            return type(value).__name__, str(Path(path)).replace("\\", "/").casefold()
    entity_id = getattr(value, "id", None)
    if entity_id:
        return type(value).__name__, str(entity_id)
    return type(value).__name__, str(id(value))
