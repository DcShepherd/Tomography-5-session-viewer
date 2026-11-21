"""Bounded, read-only intake for explicitly selected processing roots.

IMOD command files are data, never executable input. AreTomo3 discovery uses
its MdocDone marker and only full reconstructed *_Vol.mrc outputs at the root;
*_EVN_Vol.mrc and *_ODD_Vol.mrc half sets are excluded.
Neither adapter infers acquisition identity, registration or physical orientation.
A Scipion alignment job (``scipion_jobs.ALIGNMENT_PROTOCOLS``) is an ``Alignment``
source: no tomograms, one alignment offer for each series folder.
A chosen folder at a known Warp output position loads its Warp project
(``source_root``). With no project, a folder of volumes that have Warp's
tomogram name loads as ``WARP_OUTPUT``, unless it has an acquisition record or
is a Warp position that holds no tomograms (``has_warp_output``). MRC files
that no record decides (``undecided_files``) load as the user selects: as
tomograms here (``SELECTED_VOLUMES``), or as tilt series by the scanner.
"""
from __future__ import annotations

from dataclasses import replace
import math
from pathlib import Path
import struct

from tomography_session_browser.domain.folder_type import TypeFoundBy, UndecidedFile
from tomography_session_browser.domain.identity import path_identity_key
from tomography_session_browser.domain.tomograms import SELECTED_VOLUMES, WARP_OUTPUT, ProcessingSource, Tomogram
from tomography_session_browser.parsers.path_utils import natural_key
from tomography_session_browser.parsers.volume_header import (
    CLOUD_ONLY_FILE_WARNING, fingerprint, is_cloud_placeholder, read_volume, records_volume,
)
from tomography_session_browser.parsers.warp_intake import (
    VOLUME_NAME, VOLUME_SUFFIXES, holds_other_warp_output, is_warp_source, load_warp_output,
    load_warp_source, unproven_warp_output, volume_problem, warp_project_of, warp_volume_problem,
)
from tomography_session_browser.parsers.acquisition_evidence import reconstruction_evidence
from tomography_session_browser.domain.recorded_metrics import ProcessingEvidence
from tomography_session_browser.parsers.imod_commands import literal_path, parameter, standard_input
from tomography_session_browser.parsers.recorded_metrics import read_alignment_offer, read_imod_processing_evidence
from tomography_session_browser.parsers.reconstruction_intake import has_reconstructions, load_reconstructions
from tomography_session_browser.parsers.scipion_jobs import ScipionJob, processing_root, scipion_job

IMOD_COMMANDS = ("tilt.com", "tilta.com", "tiltb.com")
ARETOMO_HALF_SETS = ("_evn_vol.mrc", "_odd_vol.mrc")
METADATA_LIMIT = 1024 * 1024
#: The owner's wording (D2, 20 November 2026).
WARP_PROJECT_NOT_FOUND = ("This folder has Warp output, but its Warp project folder was not found. "
                          "Choose the Warp project folder.")
#: An acquisition record of a folder's own, which its names do not outweigh.
ACQUISITION_RECORDS = ("Session.dm", "ScreeningSession.dm", "Batch", "SearchMaps")


def _has_acquisition_record(path: Path) -> bool:
    return any((path / name).exists() for name in ACQUISITION_RECORDS) or any(path.glob("*.mdoc"))


def refusal_reason(path: Path) -> str | None:
    """Why ``path`` must not load as acquisition data, or None.

    Its names put it at a Warp output position, but no Warp project holds it,
    so the acquisition scanner would make its volumes tilt series. A folder with
    an acquisition record of its own is not refused. Callers ask only once the
    folder is not processing output (``processing_pipelines``), so a folder of
    Warp-named volumes (``has_warp_output``) is never refused, and a version
    folder of volumes that no record decides is asked about (``undecided_files``).
    What is left is a Warp position that holds no tomograms (owner D9), and a
    version folder with no volume.
    """
    if not unproven_warp_output(path) or _has_acquisition_record(path) or undecided_files(path):
        return None
    return WARP_PROJECT_NOT_FOUND


def undecided_file(path: Path) -> UndecidedFile | None:
    """``path`` as its header gives it when no record decides its type (rule 5), else None.

    The acquisition scanner reads ``*.mrc`` files as tilt series. Rules 2–4 and
    6 decide first: an extended header, a header that records a volume, Warp's
    tomogram name and one section. A cloud-only file is not opened, and a file
    with a short header keeps the acquisition rule.
    """
    if path.suffix.lower() != ".mrc" or VOLUME_NAME.fullmatch(path.stem) or volume_problem(path) is not None:
        return None
    with path.open("rb") as handle:
        header = handle.read(1024)
    if len(header) != 1024 or records_volume(header):
        return None
    endian = ">" if header[212:214] == b"\x11\x11" else "<"
    size = struct.unpack_from(endian + "3i", header, 0)
    cell = struct.unpack_from(endian + "f", header, 40)[0]
    pixel = cell / size[0] if size[0] > 0 and math.isfinite(cell) and cell > 0 else None
    return UndecidedFile(path, size, pixel)


def undecided_files(path: Path) -> tuple[UndecidedFile, ...]:
    """The folder's own MRC files that no record decides (rule 5, owner D7 b and D9).

    Only in a folder with no acquisition record of its own, outside the Warp
    positions that hold no tomograms. The scanner would read these files as
    tilt series with no tilt angles, so the user selects how they load.
    """
    if _has_acquisition_record(path) or holds_other_warp_output(path):
        return ()
    try:
        entries = sorted(path.glob("*.mrc"), key=lambda item: natural_key(item.name))
    except OSError:
        return ()
    found = []
    for entry in entries:
        try:
            if entry.is_file() and (file := undecided_file(entry)) is not None:
                found.append(file)
        except OSError:
            continue
    return tuple(found)


def load_selected_volumes(path: Path) -> ProcessingSource:
    """The folder's MRC files that no record decides, as tomograms by the user's selection (rule 5).

    Each file is one tomogram, with the folder's own files only. Another MRC
    file in the folder is named in a warning.
    """
    root = path.resolve()
    key = path_identity_key(root)
    tomograms: list[Tomogram] = []
    issues: list[str] = []
    try:
        entries = sorted(root.glob("*.mrc"), key=lambda item: natural_key(item.name))
    except OSError as exc:
        entries, issues = [], [f"{root.name}: {exc}"]
    for entry in entries:
        try:
            if not entry.is_file():
                continue
            if undecided_file(entry) is not None:
                tomograms.append(Tomogram(f"{key}::{entry.name}", entry.stem, key,
                                          (read_volume(entry, "Reconstruction"),), reconstruction_evidence(entry)))
            elif (problem := volume_problem(entry)) == CLOUD_ONLY_FILE_WARNING:
                issues.append(f"{entry.name}: {problem}")
            else:
                issues.append(f"{entry.name} was not loaded: {problem or 'its header does not hold a volume'}.")
        except OSError as exc:
            issues.append(f"{entry.name}: {exc}")
    if not tomograms:
        issues.append("No MRC volumes without a type record were found in this folder.")
    return ProcessingSource(key, root.name, root, tuple(tomograms), SELECTED_VOLUMES, tuple(issues),
                            found_by=TypeFoundBy.SELECTION)


def has_warp_output(path: Path) -> bool:
    """A folder that holds a volume with Warp's tomogram name (rule 4, owner D7 b, 20 November 2026).

    In any folder with no acquisition record of its own, outside the Warp
    positions that hold no tomograms. Only the folder's own files are read, and
    a header only for a file with a Warp name (``warp_volume_problem``).
    """
    if _has_acquisition_record(path) or holds_other_warp_output(path):
        return False
    try:
        entries = list(path.iterdir())
    except OSError:
        return False
    for entry in entries:
        try:
            if (entry.suffix.lower() in VOLUME_SUFFIXES and VOLUME_NAME.fullmatch(entry.stem)
                    and entry.is_file() and warp_volume_problem(entry) is None):
                return True
        except OSError:
            continue
    return False


def source_root(path: Path) -> Path:
    """The folder that a chosen path loads; any other path is unchanged.

    A Scipion job folder loads its ``extra`` folder (``processing_root``). A
    folder at a known Warp output position loads its Warp project
    (``warp_project_of``), unless the folder has a pipeline marker of its own.
    Every place where a chosen path becomes a source key or a load uses this,
    so such a folder and its root are one source.
    """
    root = processing_root(path)
    if root != path or scipion_job(path) is not None:
        return root
    if any((path / name).is_file() for name in (*IMOD_COMMANDS, "MdocDone.txt")):
        return path
    return warp_project_of(path) or path


def processing_pipelines(path: Path, *, selected: bool = False) -> tuple[str, ...]:
    """The pipelines that ``path`` is output of. ``selected``: the user selected tomograms (rule 5)."""
    path = source_root(path)
    job = scipion_job(path)
    if job is not None and job.model_endings is not None:
        return ("Alignment",)  # Alone: beside another marker it would read as ambiguous.
    found = []
    if is_warp_source(path):
        found.append("Warp")
    if any((path / name).is_file() for name in IMOD_COMMANDS):
        found.append("IMOD")
    if (path / "MdocDone.txt").is_file():
        found.append("AreTomo3")
    if not found and has_reconstructions(path):
        found.append('Reconstructions')
    elif not found and has_warp_output(path):  # After the header proof (rule 3), as rule 4.
        found.append(WARP_OUTPUT)
    elif not found and selected and undecided_files(path):  # Rule 5, by the user's selection.
        found.append(SELECTED_VOLUMES)
    return tuple(found)


def imod_output(command: Path, root: Path) -> Path:
    before = fingerprint(command)
    if is_cloud_placeholder(command):
        raise ValueError("Cloud-only command file: make it available locally, then reload.")
    if before.size > METADATA_LIMIT:
        raise ValueError("Command file exceeds the metadata size limit.")
    with command.open("rb") as stream:
        data = stream.read(METADATA_LIMIT + 1)
    if len(data) > METADATA_LIMIT or fingerprint(command) != before:
        raise ValueError("Command file changed while being read. Reload the folder.")
    values = standard_input(data.decode('utf-8-sig'), 'tilt')
    output = literal_path(parameter(values, 'OutputFile'), root)
    if output.suffix.lower() not in (".mrc", ".rec", ".map"):
        raise ValueError("OutputFile is outside this folder or is not a supported reconstruction file.")
    return output


def load_processing_source(path: Path, *, selected: bool = False) -> ProcessingSource:
    """Load a processing root; a recognised Scipion job also protects the job folder around it.

    ``selected``: the user selected tomograms for the folder's undecided files (rule 5).
    """
    chosen = source_root(path)
    source = _load_source(chosen.resolve(), selected)
    return source if scipion_job(chosen) is None else replace(source, job_root=chosen.parent.resolve())


def _load_source(root: Path, selected: bool = False) -> ProcessingSource:
    pipelines = processing_pipelines(root, selected=selected)
    if len(pipelines) != 1:
        raise ValueError("Choose one processing folder with unambiguous Warp, IMOD, AreTomo3 or reconstruction metadata.")
    pipeline = pipelines[0]
    if pipeline == "Alignment":
        job = scipion_job(root)
        assert job is not None
        return load_alignment_job(root, job)
    if pipeline == "Warp":
        return load_warp_source(root)
    if pipeline == 'Reconstructions':
        return load_reconstructions(root)
    if pipeline == WARP_OUTPUT:
        return load_warp_output(root)
    if pipeline == SELECTED_VOLUMES:
        return load_selected_volumes(root)
    issues: list[str] = []
    outputs: set[Path] = set()
    if pipeline == "IMOD":
        for name in IMOD_COMMANDS:
            command = root / name
            if not command.is_file():
                continue
            try:
                outputs.add(imod_output(command, root))
            except (OSError, ValueError) as exc:
                issues.append(f"{name}: {exc}")
    else:
        # No aligned tilt stacks, EVN/ODD half sets, nested projects or filename grouping.
        outputs = {file.resolve() for file in root.iterdir()
                   if file.name.lower().endswith("_vol.mrc") and not file.name.lower().endswith(ARETOMO_HALF_SETS)
                   and file.is_file() and file.resolve().is_relative_to(root)}
    key = path_identity_key(root)
    tomograms = []
    for output in sorted(outputs, key=lambda item: natural_key(str(item.relative_to(root)))):
        try:
            volume = read_volume(output, "Reconstruction")
            relative = output.relative_to(root).as_posix()
            evidence = reconstruction_evidence(output)
            processing = read_imod_processing_evidence(root, output) if pipeline == 'IMOD' else ProcessingEvidence()
            tomograms.append(Tomogram(f"{key}::{relative}", relative, key, (volume,), evidence,
                                     processing.warnings, processing))
        except OSError as exc:
            issues.append(f"{output.name}: {exc}")
    if not tomograms:
        issues.append("No readable reconstruction files were found in the supported output layout.")
    return ProcessingSource(key, root.name, root, tuple(tomograms), pipeline, tuple(issues))


def load_alignment_job(root: Path, job: ScipionJob) -> ProcessingSource:
    """One offer for each ``<ts>/`` folder that holds ``<ts>.tlt`` or ``<ts>.xf``; nothing is a tomogram."""
    assert job.model_endings is not None
    offers, issues = [], []
    try:
        folders = sorted((entry for entry in root.iterdir() if entry.is_dir() and not entry.is_symlink()),
                         key=lambda entry: natural_key(entry.name))
    except OSError as exc:
        folders, issues = [], [f"{root.name}: {exc}"]
    for folder in folders:
        stem = folder.name
        try:
            if (folder / f"{stem}.tlt").exists() or (folder / f"{stem}.xf").exists():
                offers.append(read_alignment_offer(folder, stem, job.label, job.model_endings))
        except OSError as exc:
            issues.append(f"{stem}: {exc}")
    if not offers:
        issues.append("No alignment series were found in the supported layout.")
    return ProcessingSource(path_identity_key(root), job.label, root, (), "Alignment", tuple(issues),
                            alignments=tuple(offers))
