"""Read only explicitly chosen Warp sources and known reconstruction directories."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import re

from tomography_session_browser.domain.identity import path_identity_key
from tomography_session_browser.domain.tomograms import ProcessingSource, Tomogram, Volume
from tomography_session_browser.parsers.path_utils import natural_key
from tomography_session_browser.parsers.volume_header import read_volume
from tomography_session_browser.parsers.recorded_metrics import read_processing_evidence
from tomography_session_browser.parsers.acquisition_evidence import parse_tomostar, read_metadata, warp_evidence

# A naming table, never a recursive search through neighbouring projects.
WARP_VERSIONS = (
    ("warp_tiltseries/reconstruction", "Raw"),
    ("warp_tiltseries/reconstruction/deconv", "Deconvolved"),
    ("denoised", "Denoised"),
    ("warp_tiltseries/reconstruction/even", "Even"),
    ("warp_tiltseries/reconstruction/odd", "Odd"),
)
VOLUME_NAME = re.compile(r"^(?P<stem>.+)_(?P<spacing>\d+(?:\.\d+)?)Apx$", re.IGNORECASE)


def is_warp_source(path: Path) -> bool:
    return (path / "warp_tiltseries.settings").is_file() or (
        (path / "warp_tiltseries").is_dir() and (path / "tomostar").is_dir())


def recorded_movie_paths(path: Path) -> tuple[Path, ...]:
    """Read the movie column of one tomostar; resolve relative paths at that file.

    Foreign absolute paths are retained as evidence, never rewritten to local
    basenames. A malformed table simply leaves the relationship unresolved.
    """
    block = read_metadata(path, parse_tomostar)
    if block.state == 'incomplete':
        raise ValueError(block.issue)
    return tuple(dict.fromkeys(block.value or ()))


def load_warp_source(path: Path) -> ProcessingSource:
    path = path.resolve()
    if not is_warp_source(path):
        raise ValueError("Choose a Warp processing root containing warp_tiltseries.settings or warp_tiltseries and tomostar.")
    source_key = path_identity_key(path)
    grouped: dict[str, list[Volume]] = defaultdict(list)
    issues: list[str] = []
    for relative, version in WARP_VERSIONS:
        folder = path / relative
        if not folder.is_dir():
            continue
        try:
            files = sorted(folder.iterdir(), key=lambda item: natural_key(item.name))
        except OSError as exc:
            issues.append(f"{relative}: {exc}")
            continue
        for file in files:
            if file.suffix.lower() not in (".mrc", ".rec", ".map") or not file.is_file():
                continue
            match = VOLUME_NAME.fullmatch(file.stem)
            stem = match.group("stem") if match else file.stem
            try:
                grouped[stem].append(read_volume(file, version))
            except OSError as exc:
                issues.append(f"{file.name}: {exc}")
    tomograms = []
    for stem in sorted(grouped, key=natural_key):
        evidence = warp_evidence(path, stem)
        warnings = tuple(block.issue for block in (*evidence.movies, *evidence.mdocs) if block.state == 'incomplete')
        processing = read_processing_evidence(path, stem)
        tomograms.append(Tomogram(f"{source_key}::{stem}", stem, source_key,
                                  tuple(grouped[stem]), evidence, warnings + processing.warnings, processing))
    if not tomograms:
        issues.append("No reconstruction MRC files were found in the known Warp output directories.")
    return ProcessingSource(source_key, path.name, path, tuple(tomograms), warnings=tuple(issues))
