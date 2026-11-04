"""One-level reconstruction discovery, proved by the supplied IMOD header label."""
from pathlib import Path
import struct

from tomography_session_browser.domain.identity import path_identity_key
from tomography_session_browser.domain.reconstruction_names import reconstruction_name
from tomography_session_browser.domain.tomograms import ProcessingSource, Tomogram
from tomography_session_browser.parsers.acquisition_evidence import reconstruction_evidence
from tomography_session_browser.parsers.path_utils import natural_key
from tomography_session_browser.parsers.recorded_metrics import read_imod_processing_evidence
from tomography_session_browser.parsers.volume_header import fingerprint, is_cloud_placeholder, read_volume

SUFFIXES = ('.mrc', '.rec', '.map')


def discover_files(root: Path) -> tuple[tuple[Path, ...], tuple[str, ...]]:
    paths, issues = [], []
    for entry in sorted(root.iterdir(), key=lambda path: natural_key(path.name)):
        try:
            if entry.is_file() and entry.suffix.lower() in SUFFIXES:
                paths.append(entry)
            elif entry.is_dir() and not entry.is_symlink():
                paths.extend(path for path in sorted(entry.iterdir(), key=lambda path: natural_key(path.name))
                             if path.is_file() and path.suffix.lower() in SUFFIXES)
        except OSError as exc:
            issues.append(f'{entry.name}: {exc}')
    return tuple(path for path in paths if path.resolve().is_relative_to(root.resolve())), tuple(issues)


def is_reconstruction(path: Path) -> bool:
    """Header labels are proof; a filename or a 3-D shape alone is not."""
    if is_cloud_placeholder(path):
        return False
    if path.stem.casefold().endswith(('_ali', '_preali', '_st', '_aligned', '_fractions', '_projections')):
        return False
    stamp = fingerprint(path)
    with path.open('rb') as stream:
        header = stream.read(1024)
    if len(header) != 1024 or fingerprint(path) != stamp:
        return False
    endian = '>' if header[212:214] == b'\x11\x11' else '<'
    count = struct.unpack_from(endian + 'i', header, 220)[0]
    extended = struct.unpack_from(endian + 'i', header, 92)[0]
    if not 1 <= count <= 10 or extended != 0:
        return False
    labels = header[224:224 + 80 * count].decode('ascii', errors='replace')
    return 'TILT: Tomographic reconstruction' in labels


def has_reconstructions(root: Path) -> bool:
    try:
        paths, _ = discover_files(root)
        for path in paths:
            try:
                if is_reconstruction(path):
                    return True
            except OSError:
                continue
    except OSError:
        pass
    return False


def load_reconstructions(root: Path) -> ProcessingSource:
    root = root.resolve()
    key = path_identity_key(root)
    paths, discovery_issues = discover_files(root)
    items, issues = [], list(discovery_issues)
    proofs = {}
    for path in paths:
        try:
            stamp = fingerprint(path)
            if is_reconstruction(path) and fingerprint(path) == stamp:
                proofs[path] = stamp
        except OSError as exc:
            issues.append(f'{path.relative_to(root)}: {exc}')
    accepted = set()
    for folder in {path.parent for path in paths if path.parent != root}:
        matches = [path for path in paths if path.parent == folder and
                   reconstruction_name(path.stem) == reconstruction_name(folder.name) and path in proofs]
        if len(matches) == 1:
            accepted.add(matches[0])
        elif len(matches) > 1:
            issues.append(f'{folder.name}: several reconstructions match the folder; none was selected.')
    for path in paths:
        relative = path.relative_to(root).as_posix()
        try:
            if (path.parent != root and path not in accepted) or path not in proofs:
                issues.append(f'{relative}: reconstruction identity is unverified; file was not loaded.')
                continue
            volume = read_volume(path, 'Reconstruction')
            if volume.fingerprint != proofs[path]:
                raise ValueError('Reconstruction identity changed during intake; reload the folder.')
            if volume.grid is None:
                issues.append(f'{relative}: ' + '; '.join(volume.warnings))
                continue
            evidence = reconstruction_evidence(path)
            processing = read_imod_processing_evidence(path.parent, path)
            items.append(Tomogram(f'{key}::{relative}', path.stem, key, (volume,), evidence,
                                  processing.warnings, processing))
        except (OSError, ValueError) as exc:
            issues.append(f'{relative}: {exc}')
    return ProcessingSource(key, root.name, root, tuple(items), 'Reconstructions', tuple(issues))
