"""One immutable acquisition lookup per loaded universe; no stack I/O."""
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from types import MappingProxyType
import unicodedata

from tomography_session_browser.domain.acquisition import AcquisitionEvidence, Evidence, EvidenceFile, EvidenceMatch, TiltRecord, movie_name
from tomography_session_browser.domain.identity import entity_key_text, path_identity_key
from tomography_session_browser.domain.models import TiltSeries
from tomography_session_browser.domain.tomograms import Tomogram
from tomography_session_browser.parsers.acquisition_evidence import signature


def recorded_path_key(raw: str | Path, base: Path) -> str:
    text = str(raw).replace('\\', '/')
    if text.startswith('/') or PureWindowsPath(text).is_absolute():
        return text.rstrip('/').casefold()
    return path_identity_key((base/text).resolve())


@dataclass(frozen=True, slots=True)
class SeriesEvidence:
    key: str
    paths: frozenset[str]
    movies: frozenset[str]
    records: tuple[TiltRecord, ...]
    image_file: str | None
    stack_path: Path
    stack_size: int
    stack_file: EvidenceFile | None = None
    provenance: tuple[EvidenceFile, ...] = ()
    name: str = ''


@dataclass(frozen=True, slots=True)
class AcquisitionIndex:
    series: tuple[SeriesEvidence, ...]
    processing: tuple[Tomogram, ...]
    paths: Mapping[str, frozenset[str]]
    movies: Mapping[str, frozenset[str]]
    records: Mapping[tuple[TiltRecord, ...], frozenset[str]]
    stack_sizes: Mapping[int, frozenset[str]]
    stack_results: Mapping[str, EvidenceMatch] = field(default_factory=lambda: MappingProxyType({}))
    by_key: Mapping[str, SeriesEvidence] = field(init=False, repr=False, compare=False)
    names: Mapping[str, tuple[SeriesEvidence, ...]] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        names: dict[str, list[SeriesEvidence]] = {}
        for value in self.series:
            if value.name:
                names.setdefault(unicodedata.normalize('NFC', value.name).casefold(), []).append(value)
        object.__setattr__(self, 'by_key', MappingProxyType({value.key: value for value in self.series}))
        object.__setattr__(self, 'names', MappingProxyType({key: tuple(values) for key, values in names.items()}))


def synthetic_evidence(tilt: TiltSeries) -> AcquisitionEvidence:
    """Pure adapter for caller-built models; production parsers supply strict evidence."""
    rows = [(section.z_value, section.metadata) for section in tilt.sections]
    paths = tuple(Path(str(row['SubFramePath'])) for _, row in rows if row.get('SubFramePath'))
    complete = bool(paths) and len(paths) == len(rows) and len({movie_name(p) for p in paths}) == len(paths)
    movies = Evidence('complete' if complete else ('incomplete' if paths else 'absent'), paths)
    try:
        records = Evidence('complete', signature(tilt.metadata, rows, tilt.tilt_count))
    except ValueError as exc:
        records = Evidence('incomplete' if rows else 'absent', issue=str(exc))
    return AcquisitionEvidence((movies,), (records,))


def build_acquisition_index(series: Iterable[TiltSeries], tomograms: Iterable[Tomogram] = ()) -> AcquisitionIndex:
    frozen = []
    paths: dict[str, set[str]] = {}
    movies: dict[str, set[str]] = {}
    records: dict[tuple[TiltRecord, ...], set[str]] = {}
    sizes: dict[int, set[str]] = {}
    for tilt in series:
        key = entity_key_text(tilt)
        evidence = tilt.acquisition if tilt.acquisition is not None else synthetic_evidence(tilt)
        # Preserve A's exact recorded-file paths, including legacy section models.
        raw_paths: list[tuple[str | Path, Path]] = [(tilt.mrc_path, Path.cwd())]
        if tilt.mdoc_path is not None:
            raw_paths.append((tilt.mdoc_path, Path.cwd()))
            raw_paths.extend((str(s.metadata['SubFramePath']), tilt.mdoc_path.parent)
                             for s in tilt.sections if isinstance(s.metadata.get('SubFramePath'), str) and s.metadata['SubFramePath'])
        raw_paths.extend((path, Path.cwd()) for block in evidence.movies if block.state == 'complete'
                         for path in (block.value or ()))
        path_keys = frozenset(recorded_path_key(path, base) for path, base in raw_paths)
        count = tilt.tilt_count if tilt.tilt_count is not None else len(tilt.sections)
        complete_movies = [block.value for block in evidence.movies if block.state == 'complete' and block.value]
        names = frozenset(movie_name(path) for group in complete_movies for path in group)
        metadata_count = len(tilt.sections) or (len(complete_movies[0]) if complete_movies else 0)
        if (any(b.state == 'incomplete' for b in evidence.movies) or not complete_movies
                or any(len(group) != metadata_count or len({movie_name(p) for p in group}) != len(group) for group in complete_movies)):
            names = frozenset()
        valid_mdocs = [b.value for b in evidence.mdocs if b.state == 'complete' and b.value is not None]
        signatures = {value.records for value in valid_mdocs if len(value.records) == count}
        record = next(iter(signatures)) if len(signatures) == 1 and not any(b.state == 'incomplete' for b in evidence.mdocs) else ()
        image = valid_mdocs[0].image_file if valid_mdocs else None
        size = tilt.mrc_metadata.size_bytes if tilt.mrc_metadata is not None else 0
        provenance = tuple(b.source for b in (*evidence.movies, *evidence.mdocs) if b.source is not None)
        frozen.append(SeriesEvidence(key, path_keys, names, record, image, tilt.mrc_path, size, tilt.stack_file, provenance, tilt.name))
        for path in path_keys:
            paths.setdefault(path, set()).add(key)
        for name in names:
            movies.setdefault(name, set()).add(key)
        if record:
            records.setdefault(record, set()).add(key)
        if size:
            sizes.setdefault(size, set()).add(key)
    def freeze(values):
        return MappingProxyType({key: frozenset(items) for key, items in values.items()})
    return AcquisitionIndex(tuple(frozen), tuple(tomograms), freeze(paths), freeze(movies), freeze(records), freeze(sizes))
