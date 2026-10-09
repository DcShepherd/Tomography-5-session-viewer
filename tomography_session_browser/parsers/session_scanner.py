from __future__ import annotations

import logging
from pathlib import Path
import re
from typing import Callable, TypeVar

from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.models import Overview, Sample, Session
from tomography_session_browser.parsers.atlas_parser import parse_atlas_folder
from tomography_session_browser.parsers.batch_parser import parse_batch_folder
from tomography_session_browser.parsers.mrc_parser import read_mrc_metadata
from tomography_session_browser.parsers.path_utils import is_sample_dir, safe_id, sorted_paths
from tomography_session_browser.parsers.scan_progress import ScanProgress, ScanProgressReporter
from tomography_session_browser.parsers.searchmap_parser import parse_search_map_folder
from tomography_session_browser.parsers.tiltseries_parser import parse_tilt_series_in_folder
from tomography_session_browser.parsers.xml_parser import find_first, parse_xml_file
from tomography_session_browser.diagnostics.profiling import NULL_PROFILE, LoadingProfiler

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
        # ``SessionKind.SINGLE_COLLECTION`` is never produced here: its old
        # test (Batch, SearchMaps, an Overview or an MRC with an MDOC) was a
        # subset of this one, so it could not be reached. Both kinds are
        # handled alike everywhere; the value stays for callers that pass it.
        if self._looks_like_collection(path):
            return SessionKind.COLLECTION
        return SessionKind.UNKNOWN

    def load(
        self,
        path: Path,
        *,
        profile: LoadingProfiler = NULL_PROFILE,
        progress: ScanProgress | None = None,
    ) -> Session:
        """Parse files in the session folder at ``path``.

        This returns raw entities. Use ``SessionLoader.load`` for inferred
        batch links, exposure-linked search tiles and final summary counts.

        ``progress(label, current, total)`` is told what is being read
        ("Reading samples", 4, 13): the samples of a multi-sample session, or
        the parts of a single sample. Display only; it changes nothing parsed,
        and an error in it never stops a load.
        """

        report = ScanProgressReporter(progress)
        root = Path(path)
        with profile.phase("classify_layout"):
            kind = self.classify(root)
        session = Session(id=safe_id(root), name=root.name, path=root, kind=kind)

        if kind == SessionKind.ATLAS_SCREENING:
            with profile.phase("parse_session_xml", count=1):
                metadata = parse_xml_file(root / "ScreeningSession.dm")
                session.metadata_summary["ScreeningSession.dm"] = metadata
                _append_xml_warning(session.warnings, metadata, "ScreeningSession.dm")
            sample_dirs = self._sample_dirs(root)
            with profile.phase("load_screening_samples", count=len(sample_dirs)):
                session.samples = [
                    self._load_screening_sample(sample_dir, profile=profile)
                    for sample_dir in report.each("Reading samples", sample_dirs)
                ]
            root_atlas = _safe_parse(
                "Root Atlas",
                lambda: _profiled_parse(profile, "parse_root_atlas", lambda: parse_atlas_folder(root / "Atlas")),
                None,
                session.warnings,
            )
            if root_atlas:
                session.atlas = root_atlas
        elif kind == SessionKind.MULTIGRID:
            with profile.phase("parse_session_xml", count=1):
                metadata = parse_xml_file(root / "Session.dm")
                session.metadata_summary["Session.dm"] = metadata
                _append_xml_warning(session.warnings, metadata, "Session.dm")
            sample_dirs = self._sample_dirs(root)
            with profile.phase("load_collection_samples", count=len(sample_dirs)):
                if len(sample_dirs) == 1:
                    # One sample: its parts are what there is to count.
                    session.samples = [self._load_collection_sample(sample_dirs[0], profile=profile, report=report)]
                else:
                    session.samples = [
                        self._load_collection_sample(sample_dir, profile=profile)
                        for sample_dir in report.each("Reading samples", sample_dirs)
                    ]
        elif kind in {SessionKind.COLLECTION, SessionKind.SINGLE_COLLECTION}:
            sample = self._load_collection_sample(root, profile=profile, report=report)
            session.samples = [sample]
            session.atlas = sample.atlas
            session.overviews = sample.overviews
            session.search_maps = sample.search_maps
            session.batch_positions = sample.batch_positions
            session.tilt_series = sample.tilt_series
            if (root / "Session.dm").exists():
                with profile.phase("parse_session_xml", count=1):
                    metadata = parse_xml_file(root / "Session.dm")
                    session.metadata_summary["Session.dm"] = metadata
                    _append_xml_warning(session.warnings, metadata, "Session.dm")
        else:
            session.warnings.append("Folder did not match a known Tomography 5 layout.")

        return session

    def _load_screening_sample(self, path: Path, *, profile: LoadingProfiler = NULL_PROFILE) -> Sample:
        sample = Sample(id=safe_id(path), name=path.name, path=path)
        sample_dm = path / "Sample.dm"
        if sample_dm.exists():
            with profile.phase("parse_sample_xml", count=1):
                metadata = parse_xml_file(sample_dm)
                sample.metadata["Sample.dm"] = metadata
                _append_xml_warning(sample.warnings, metadata, "Sample.dm")
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

    def _load_collection_sample(
        self,
        path: Path,
        *,
        profile: LoadingProfiler = NULL_PROFILE,
        report: ScanProgressReporter | None = None,
    ) -> Sample:
        # ``report`` counts this sample's parts, for a session of one sample.
        part = report.part if report is not None else (lambda _label: None)
        sample = Sample(id=safe_id(path), name=path.name, path=path)
        session_dm = path / "Session.dm"
        if session_dm.exists():
            with profile.phase("parse_sample_session_xml", count=1):
                metadata = parse_xml_file(session_dm)
                sample.metadata["Session.dm"] = metadata
                _append_xml_warning(sample.warnings, metadata, "Session.dm")

        part("atlas")
        sample.atlas = _safe_parse("Atlas", lambda: _profiled_parse(profile, "parse_atlas", lambda: parse_atlas_folder(path / "Atlas")), None, sample.warnings)
        part("overviews")
        sample.overviews = _safe_parse("Overviews", lambda: self._parse_overviews(path, sample.warnings, profile=profile), [], sample.warnings)
        part("search maps")
        sample.search_maps = _safe_parse("Search maps", lambda: self._parse_search_maps(path, sample.warnings, profile=profile), [], sample.warnings)
        sample.overviews.extend(search_map.overview for search_map in sample.search_maps if search_map.overview is not None)
        part("batch positions")
        sample.batch_positions = _safe_parse("Batch positions", lambda: _profiled_parse(profile, "parse_batch_positions", lambda: parse_batch_folder(path / "Batch", sample.warnings)), [], sample.warnings)
        part("tilt series")
        sample.tilt_series = _safe_parse("Tilt series", lambda: _profiled_parse(profile, "parse_tilt_series", lambda: parse_tilt_series_in_folder(path, sample.warnings)), [], sample.warnings)
        sample.name = self._sample_display_name(path, sample.metadata.get("Session.dm"), [tilt.name for tilt in sample.tilt_series])
        return sample

    def _parse_search_maps(self, path: Path, warnings: list[str] | None = None, *, profile: LoadingProfiler = NULL_PROFILE):
        search_root = path / "SearchMaps"
        if not search_root.exists():
            return []
        try:
            with profile.phase("discover_search_maps"):
                folders = [folder for folder in search_root.iterdir() if folder.is_dir() and folder.name.startswith("SearchMap_")]
        except OSError as exc:
            if warnings is not None:
                warnings.append(f"Could not list SearchMaps folder: {exc}")
            LOGGER.warning("Could not list SearchMaps folder %s: %s", search_root, exc)
            return []
        maps = []
        folders = sorted_paths(folders)
        with profile.phase("parse_search_maps", count=len(folders)):
            for folder in folders:
                parsed = _safe_parse(f"Search map {folder.name}", lambda folder=folder: parse_search_map_folder(folder), None, warnings)
                if parsed is not None:
                    maps.append(parsed)
        return maps

    def _parse_overviews(self, path: Path, warnings: list[str] | None = None, *, profile: LoadingProfiler = NULL_PROFILE) -> list[Overview]:
        try:
            with profile.phase("discover_overviews"):
                overview_paths = sorted_paths(list((path / "Batch").glob("Overview_*.mrc")) if (path / "Batch").exists() else [])
        except OSError as exc:
            if warnings is not None:
                warnings.append(f"Could not list Overview images: {exc}")
            LOGGER.warning("Could not list overview images under %s: %s", path, exc)
            return []
        overviews = []
        with profile.phase("parse_overview_mrc_headers", count=len(overview_paths)):
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


def _append_xml_warning(warnings: list[str], metadata: object, filename: str) -> None:
    if not isinstance(metadata, dict):
        return
    if message := metadata.get("_parse_error"):
        warnings.append(f"Could not parse metadata XML {filename}: {message}")
    elif message := metadata.get("_read_error"):
        warnings.append(f"Could not read metadata XML {filename}: {message}")


def _profiled_parse(profile: LoadingProfiler, name: str, parser: Callable[[], T]) -> T:
    with profile.phase(name):
        return parser()
