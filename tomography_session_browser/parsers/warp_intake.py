"""Read only explicitly chosen Warp sources and known reconstruction directories."""
from __future__ import annotations

from collections import defaultdict
import os
from pathlib import Path
import re
import struct

from tomography_session_browser.domain.folder_type import TypeFoundBy
from tomography_session_browser.domain.identity import path_identity_key
from tomography_session_browser.domain.tomograms import WARP_OUTPUT, ProcessingSource, Tomogram, Volume
from tomography_session_browser.parsers.path_utils import natural_key
from tomography_session_browser.parsers.volume_header import CLOUD_ONLY_FILE_WARNING, cloud_only, read_volume
from tomography_session_browser.parsers.recorded_metrics import read_processing_evidence
from tomography_session_browser.parsers.acquisition_evidence import (
    parse_tomostar, read_metadata, reconstruction_evidence, warp_evidence,
)

# A naming table, never a recursive search through neighbouring projects.
WARP_VERSIONS = (
    ("warp_tiltseries/reconstruction", "Raw"),
    ("warp_tiltseries/reconstruction/deconv", "Deconvolved"),
    ("denoised", "Denoised"),
    ("warp_tiltseries/reconstruction/even", "Even"),
    ("warp_tiltseries/reconstruction/odd", "Odd"),
)
VOLUME_NAME = re.compile(r"^(?P<stem>.+)_(?P<spacing>\d+(?:\.\d+)?)Apx$", re.IGNORECASE)
#: Where Warp writes output inside its project. A folder chosen at one of these
#: loads that project (owner D1 and D3, 20 November 2026): Warp writes space
#: group 0 and no labels in its volumes, so their headers cannot prove them.
#: Like ``WARP_VERSIONS``, a naming table, not a search.
WARP_POSITIONS = (
    *(relative for relative, _version in WARP_VERSIONS),
    "warp_tiltseries",
    "warp_tiltseries/reconstruction/ctf",
    "warp_tiltseries/powerspectrum",
    "warp_frameseries/average",
    "warp_frameseries/powerspectrum",
)
#: The positions that hold Warp's tomograms. The others hold the CTF volume,
#: which has a tomogram's name, power spectra and frame averages (owner D9).
TOMOGRAM_POSITIONS = tuple(relative for relative, _version in WARP_VERSIONS)
#: A version folder's own name gives the version of a volume read without its project.
VERSION_OF_FOLDER = {relative.rsplit("/", 1)[-1]: version for relative, version in WARP_VERSIONS}
VOLUME_SUFFIXES = (".mrc", ".rec", ".map")


def is_warp_source(path: Path) -> bool:
    return (path / "warp_tiltseries.settings").is_file() or (
        (path / "warp_tiltseries").is_dir() and (path / "tomostar").is_dir())


def _positions(folder: Path) -> tuple[tuple[str, Path], ...]:
    """Each of ``WARP_POSITIONS`` that ``folder`` is at by names alone, with the ancestor it gives.

    Names are compared as written, as ``load_warp_source`` reads them.
    """
    found = []
    for relative in WARP_POSITIONS:
        names = tuple(relative.split("/"))
        if len(folder.parts) > len(names) and folder.parts[-len(names):] == names:
            found.append((relative, folder.parents[len(names) - 1]))
    return tuple(found)


def _position_roots(folder: Path) -> tuple[Path, ...]:
    """The ancestors that would hold ``folder`` at one of ``WARP_POSITIONS``, by names alone."""
    return tuple(root for _relative, root in _positions(folder))


def holds_other_warp_output(path: Path) -> bool:
    """``path`` is at a Warp position that holds no tomograms, by its names.

    These are ``warp_tiltseries/`` itself and the CTF, power-spectrum and
    frame-series folders. The CTF volume has a tomogram's name, so names prove
    nothing there (owner D9, 20 November 2026).
    """
    folder = Path(os.path.abspath(path))
    return any(relative not in TOMOGRAM_POSITIONS for relative, _root in _positions(folder))


def warp_volume_problem(path: Path) -> str | None:
    """Why ``path`` is not read as a Warp tomogram by its name, or None when it is (rule 4).

    The name must be Warp's (``<name>_<pixel size>Apx``). The header must hold
    more than one section and no extended header, which a tilt stack carries.
    A cloud-only file is not opened: the result is ``CLOUD_ONLY_FILE_WARNING``.
    """
    if not VOLUME_NAME.fullmatch(path.stem):
        return "it does not have a Warp tomogram name"
    return volume_problem(path)


def volume_problem(path: Path) -> str | None:
    """Why ``path``'s header does not hold a volume, or None (rules 2 and 6).

    A volume has more than one section and no extended header, which a tilt
    stack carries. A cloud-only file is not opened: ``CLOUD_ONLY_FILE_WARNING``.
    """
    if cloud_only(path):
        return CLOUD_ONLY_FILE_WARNING
    with path.open("rb") as handle:
        header = handle.read(1024)
    if len(header) != 1024:
        return "its MRC header is incomplete"
    endian = ">" if header[212:214] == b"\x11\x11" else "<"
    if struct.unpack_from(endian + "i", header, 92)[0]:
        return "it has an extended header, as a tilt stack has"
    if struct.unpack_from(endian + "i", header, 8)[0] <= 1:
        return "it has one section, so it is not a volume"
    return None


def load_warp_output(path: Path) -> ProcessingSource:
    """The chosen folder's own volumes that have Warp's tomogram name (rule 4, owner D7 b).

    No Warp project holds the folder, so no Warp evidence is read. The version
    comes from a version folder's name (``deconv`` gives Deconvolved), otherwise
    Raw. ``found_by`` records Warp's names, and Context says from it what is not
    available (owner D15 a: not a warning). Only the folder's own files are
    read, and a file that is not a Warp tomogram is named in a warning.
    """
    root = path.resolve()
    key = path_identity_key(root)
    version = VERSION_OF_FOLDER.get(root.name, "Raw")
    grouped: dict[str, list[Volume]] = defaultdict(list)
    issues: list[str] = []
    try:
        entries = sorted(root.iterdir(), key=lambda item: natural_key(item.name))
    except OSError as exc:
        entries, issues = [], [f"{root.name}: {exc}"]
    for entry in entries:
        try:
            if entry.suffix.lower() not in VOLUME_SUFFIXES or not entry.is_file():
                continue
            problem = warp_volume_problem(entry)
            if problem is None:
                match = VOLUME_NAME.fullmatch(entry.stem)
                assert match is not None
                grouped[match.group("stem")].append(read_volume(entry, version))
            elif problem == CLOUD_ONLY_FILE_WARNING:
                issues.append(f"{entry.name}: {problem}")
            else:
                issues.append(f"{entry.name} was not loaded: {problem}.")
        except OSError as exc:
            issues.append(f"{entry.name}: {exc}")
    tomograms = tuple(
        Tomogram(f"{key}::{stem}", stem, key, tuple(volumes), reconstruction_evidence(volumes[0].path),
                 duplicate_version_warnings(stem, volumes))
        for stem, volumes in sorted(grouped.items(), key=lambda item: natural_key(item[0])))
    if not tomograms:
        issues.append("No volumes with a Warp tomogram name were found in this folder.")
    return ProcessingSource(key, root.name, root, tomograms, WARP_OUTPUT, tuple(issues), found_by=TypeFoundBy.WARP_NAMES)


def warp_project_of(path: Path) -> Path | None:
    """The Warp project that holds ``path`` at one of ``WARP_POSITIONS``, or None.

    The ancestor must pass ``is_warp_source``: names alone prove nothing. A
    folder that is a Warp project itself belongs to no other project.
    """
    folder = Path(os.path.abspath(path))
    roots = _position_roots(folder)
    if not roots or is_warp_source(folder):
        return None
    projects = {root for root in roots if is_warp_source(root)}
    return next(iter(projects)) if len(projects) == 1 else None


def unproven_warp_output(path: Path) -> bool:
    """``path`` is at a Warp output position by its names, but no Warp project holds it.

    Its volumes are Warp's (space group 0, no labels), so the acquisition scanner
    would read them as tilt series (owner D2, 20 November 2026).
    """
    folder = Path(os.path.abspath(path))
    roots = _position_roots(folder)
    return bool(roots) and not is_warp_source(folder) and not any(is_warp_source(root) for root in roots)


def recorded_movie_paths(path: Path) -> tuple[Path, ...]:
    """Read the movie column of one tomostar; resolve relative paths at that file.

    Foreign absolute paths are retained as evidence, never rewritten to local
    basenames. A malformed table simply leaves the relationship unresolved.
    Kept as a seam: the volume tests pin the movie-path rule through it.
    """
    block = read_metadata(path, parse_tomostar)
    if block.state == 'incomplete':
        raise ValueError(block.issue)
    return tuple(dict.fromkeys(block.value or ()))


def confirmed_absent(path: Path) -> bool:
    try:
        path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return True
    except OSError:
        return False
    return False


def duplicate_version_warnings(stem: str, volumes: list[Volume]) -> tuple[str, ...]:
    """One warning per version that has more than one volume (for example Warp run at two pixel sizes)."""

    by_version: dict[str, list[Volume]] = defaultdict(list)
    for volume in volumes:
        by_version[volume.version].append(volume)
    return tuple(
        f"{stem} has {len(same)} {version} volumes: {', '.join(volume.path.name for volume in same)}. "
        "The app does not choose between them. Select one in the version list."
        for version, same in by_version.items() if len(same) > 1
    )


def load_warp_source(path: Path) -> ProcessingSource:
    path = path.resolve()
    if not is_warp_source(path):
        raise ValueError("Choose a Warp processing root containing warp_tiltseries.settings or warp_tiltseries and tomostar.")
    source_key = path_identity_key(path)
    grouped: dict[str, list[Volume]] = defaultdict(list)
    issues: list[str] = []
    versions_complete = True
    for relative, version in WARP_VERSIONS:
        folder = path / relative
        if not folder.is_dir():
            if not confirmed_absent(folder):
                issues.append(f"{relative}: could not be checked. It may still be there.")
                versions_complete = False
            continue
        try:
            files = sorted(folder.iterdir(), key=lambda item: natural_key(item.name))
        except OSError as exc:
            issues.append(f"{relative}: {exc}")
            versions_complete = False
            continue
        for file in files:
            if file.suffix.lower() not in (".mrc", ".rec", ".map"):
                continue
            if not file.is_file():
                if not file.is_dir():
                    issues.append(f"{file.relative_to(path).as_posix()}: could not be checked. It may still be there.")
                    versions_complete = False
                continue
            match = VOLUME_NAME.fullmatch(file.stem)
            stem = match.group("stem") if match else file.stem
            try:
                grouped[stem].append(read_volume(file, version))
            except OSError as exc:
                issues.append(f"{file.name}: {exc}")
                versions_complete = False
    tomograms = []
    for stem in sorted(grouped, key=natural_key):
        evidence = warp_evidence(path, stem)
        warnings = tuple(block.issue for block in (*evidence.movies, *evidence.mdocs) if block.state == 'incomplete')
        warnings += duplicate_version_warnings(stem, grouped[stem])
        processing = read_processing_evidence(path, stem)
        tomograms.append(Tomogram(f"{source_key}::{stem}", stem, source_key,
                                  tuple(grouped[stem]), evidence, warnings + processing.warnings, processing))
    if not tomograms:
        issues.append("No reconstruction MRC files were found in the known Warp output directories.")
    return ProcessingSource(source_key, path.name, path, tuple(tomograms),
                            warnings=tuple(issues), versions_complete=versions_complete)
