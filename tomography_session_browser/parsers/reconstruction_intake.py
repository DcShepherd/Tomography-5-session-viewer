"""One-level reconstruction discovery, proved by what the header records."""
from pathlib import Path
import struct

from tomography_session_browser.domain.identity import path_identity_key
from tomography_session_browser.domain.reconstruction_names import reconstruction_name
from tomography_session_browser.domain.tomograms import ProcessingSource, Tomogram, Volume
from tomography_session_browser.parsers.acquisition_evidence import reconstruction_evidence
from tomography_session_browser.parsers.path_utils import natural_key
from tomography_session_browser.parsers.recorded_metrics import read_imod_processing_evidence
from tomography_session_browser.parsers.scipion_jobs import ScipionJob, scipion_job
from tomography_session_browser.parsers.volume_header import fingerprint, is_cloud_placeholder, read_volume, records_volume

SUFFIXES = ('.mrc', '.rec', '.map')
ACQUISITION_FOLDERS = ('Batch', 'SearchMaps')


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


def has_own_mdoc(path: Path) -> bool:
    """An MDOC named for this exact file records it as the acquired stack."""
    return path.with_name(path.name + '.mdoc').exists() or path.with_suffix('.mdoc').exists()


def accepts_space_group(root: Path) -> bool:
    """A volume left in a Tomography 5 folder does not make the folder processing output."""
    return not any((root / name).exists() for name in ACQUISITION_FOLDERS)


def is_reconstruction(path: Path, *, space_group: bool = True) -> bool:
    """What the header records is proof; a filename or a 3-D shape alone is not.

    The record is IMOD's label or, when ``space_group`` allows it, the MRC
    format's own space group, because other producers do not write that label.
    """
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
    if extended != 0:
        return False
    if 1 <= count <= 10 and 'TILT: Tomographic reconstruction' in header[224:224 + 80 * count].decode('ascii', errors='replace'):
        return True
    return space_group and records_volume(header) and not has_own_mdoc(path)


def has_reconstructions(root: Path) -> bool:
    try:
        paths, _ = discover_files(root)
        space_group = accepts_space_group(root)
        for path in paths:
            try:
                if is_reconstruction(path, space_group=space_group):
                    return True
            except OSError:
                continue
    except OSError:
        pass
    return False


def folder_tomogram(path: Path, folder: Path, job: ScipionJob | None) -> str | None:
    """The tomogram name when ``path`` is its folder's reconstruction: same name, or the job's own naming."""
    if reconstruction_name(path.stem) == reconstruction_name(folder.name):
        return path.stem
    return job.tomogram_name(folder.name, path.stem) if job is not None else None


def read_proved(path: Path, version: str, stamp) -> Volume:
    volume = read_volume(path, version)
    if volume.fingerprint != stamp:
        raise ValueError('Reconstruction identity changed during intake; reload the folder.')
    return volume


def load_reconstructions(root: Path) -> ProcessingSource:
    root = root.resolve()
    key = path_identity_key(root)
    job = scipion_job(root)
    paths, discovery_issues = discover_files(root)
    items, issues = [], list(discovery_issues)
    proofs = {}
    space_group = accepts_space_group(root)
    for path in paths:
        try:
            stamp = fingerprint(path)
            if is_reconstruction(path, space_group=space_group) and fingerprint(path) == stamp:
                proofs[path] = stamp
        except OSError as exc:
            issues.append(f'{path.relative_to(root)}: {exc}')
    # Folder layout: each folder's one reconstruction, its name and, in a Scipion job, its half sets.
    accepted: dict[Path, tuple[str, tuple[tuple[Path, str], ...]]] = {}
    for folder in {path.parent for path in paths if path.parent != root}:
        proved = [path for path in paths if path.parent == folder and path in proofs]
        named = [(path, name) for path in proved if (name := folder_tomogram(path, folder, job)) is not None]
        if len(named) == 1:
            main, name = named[0]
            halves = tuple((path, version) for path in proved
                           if job is not None and (version := job.half_set(main.stem, path.stem)) is not None)
            accepted[main] = (name, halves)
        elif len(named) > 1:
            issues.append(f'{folder.name}: several reconstructions match the folder; none was selected.')
    half_sets = {path for _name, halves in accepted.values() for path, _version in halves}
    for path in paths:
        relative = path.relative_to(root).as_posix()
        if path in half_sets:
            continue
        try:
            if (path.parent != root and path not in accepted) or path not in proofs:
                issues.append(f'{relative}: reconstruction identity is unverified; file was not loaded.')
                continue
            name, halves = accepted.get(path, (path.stem, ()))
            volume = read_proved(path, 'Reconstruction', proofs[path])
            if volume.grid is None:
                issues.append(f'{relative}: ' + '; '.join(volume.warnings))
                continue
            volumes = [volume]
            for half, version in halves:
                try:
                    volumes.append(read_proved(half, version, proofs[half]))
                except (OSError, ValueError) as exc:
                    issues.append(f'{half.relative_to(root).as_posix()}: {exc}')
            evidence = reconstruction_evidence(path)
            processing = read_imod_processing_evidence(path.parent, path)
            items.append(Tomogram(f'{key}::{relative}', name, key, tuple(volumes), evidence,
                                  processing.warnings, processing))
        except (OSError, ValueError) as exc:
            issues.append(f'{relative}: {exc}')
    return ProcessingSource(key, job.label if job is not None else root.name, root, tuple(items),
                            'Reconstructions', tuple(issues))
