"""Bounded, read-only intake for explicitly selected processing roots.

IMOD command files are data, never executable input. AreTomo3 discovery uses
its MdocDone marker and only full reconstructed *_Vol.mrc outputs at the root;
*_EVN_Vol.mrc and *_ODD_Vol.mrc half sets are excluded.
Neither adapter infers acquisition identity, registration or physical orientation.
"""
from __future__ import annotations

from pathlib import Path

from tomography_session_browser.domain.identity import path_identity_key
from tomography_session_browser.domain.tomograms import ProcessingSource, Tomogram
from tomography_session_browser.parsers.path_utils import natural_key
from tomography_session_browser.parsers.volume_header import fingerprint, is_cloud_placeholder, read_volume
from tomography_session_browser.parsers.warp_intake import is_warp_source, load_warp_source
from tomography_session_browser.parsers.acquisition_evidence import reconstruction_evidence
from tomography_session_browser.domain.recorded_metrics import ProcessingEvidence
from tomography_session_browser.parsers.imod_commands import literal_path, parameter, standard_input
from tomography_session_browser.parsers.recorded_metrics import read_imod_processing_evidence
from tomography_session_browser.parsers.reconstruction_intake import has_reconstructions, load_reconstructions

IMOD_COMMANDS = ("tilt.com", "tilta.com", "tiltb.com")
ARETOMO_HALF_SETS = ("_evn_vol.mrc", "_odd_vol.mrc")
METADATA_LIMIT = 1024 * 1024


def processing_pipelines(path: Path) -> tuple[str, ...]:
    found = []
    if is_warp_source(path):
        found.append("Warp")
    if any((path / name).is_file() for name in IMOD_COMMANDS):
        found.append("IMOD")
    if (path / "MdocDone.txt").is_file():
        found.append("AreTomo3")
    if not found and has_reconstructions(path):
        found.append('Reconstructions')
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


def load_processing_source(path: Path) -> ProcessingSource:
    root = path.resolve()
    pipelines = processing_pipelines(root)
    if len(pipelines) != 1:
        raise ValueError("Choose one processing root with unambiguous Warp, IMOD or AreTomo3 metadata.")
    pipeline = pipelines[0]
    if pipeline == "Warp":
        return load_warp_source(root)
    if pipeline == 'Reconstructions':
        return load_reconstructions(root)
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
