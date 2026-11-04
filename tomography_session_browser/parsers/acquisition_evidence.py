"""Bounded metadata reads for linking; never searches neighbouring projects."""
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path, PureWindowsPath
import re
import shlex
from typing import TypeVar
from xml.etree import ElementTree

from tomography_session_browser.domain.acquisition import (
    AcquisitionEvidence, Evidence, EvidenceFile, MdocSignature, TiltRecord, movie_name,
)
from tomography_session_browser.parsers.volume_header import is_cloud_placeholder
from tomography_session_browser.domain.reconstruction_names import reconstruction_stem

METADATA_LIMIT = 8 * 1024 * 1024
T = TypeVar('T')


def evidence_file(path: Path) -> EvidenceFile:
    target = path.resolve(strict=True)
    stat = target.stat()
    return EvidenceFile(target, stat.st_size, stat.st_mtime_ns)


def read_metadata(path: Path, parse: Callable[[str, Path], T]) -> Evidence[T]:
    """Resolve explicit symlinks only; changed/placeholder/partial data is failed evidence."""
    stamp = None
    try:
        # A dangling explicit symlink is incomplete, not an absent fallback.
        if not path.exists() and not path.is_symlink():
            return Evidence()
        stamp = evidence_file(path)
        if is_cloud_placeholder(stamp.path):
            raise ValueError('Cloud-only metadata; make it available locally and reload.')
        if stamp.size > METADATA_LIMIT:
            raise ValueError('Acquisition metadata exceeds the 8 MiB limit.')
        with stamp.path.open('rb') as stream:
            data = stream.read(METADATA_LIMIT+1)
        if len(data) > METADATA_LIMIT:
            raise ValueError('Acquisition metadata exceeds the 8 MiB limit.')
        value = parse(data.decode('utf-8-sig'), stamp.path.parent)
        if evidence_file(path) != stamp or is_cloud_placeholder(stamp.path):
            raise ValueError('Acquisition metadata changed while reading. Reload the folder.')
        return Evidence('complete', value, stamp)
    except (OSError, ValueError, RuntimeError, UnicodeError, ElementTree.ParseError) as exc:
        return Evidence('incomplete', source=stamp, issue=f'{path}: {exc}')


def recorded_path(raw: str, base: Path) -> Path:
    raw = raw.strip().strip('"\'')
    if not raw or '\x00' in raw:
        raise ValueError('Empty or invalid acquisition file path.')
    if raw.startswith('/') or PureWindowsPath(raw).is_absolute():
        return Path(raw)
    return (base / raw.replace('\\', '/')).resolve()


def parse_tomostar(text: str, base: Path) -> tuple[Path, ...]:
    columns: list[str] = []
    paths = []
    in_loop = False
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('data_'):
            continue
        if line == 'loop_':
            if in_loop:
                raise ValueError('Multiple tomostar loops are not supported as one acquisition table.')
            in_loop = True
            continue
        if line.startswith('_'):
            tokens = line.split()
            name = tokens[0]
            if name in columns or paths:
                raise ValueError('Duplicate or late tomostar column.')
            if len(tokens) > 1 and (len(tokens) != 2 or tokens[1] != f'#{len(columns)+1}'):
                raise ValueError('Tomostar column numbering is inconsistent.')
            columns.append(name)
            continue
        if not in_loop or '_wrpMovieName' not in columns:
            raise ValueError('Tomostar has no declared movie column.')
        fields = shlex.split(line, posix=False)
        if len(fields) != len(columns):
            raise ValueError('Tomostar row does not match its declared column count.')
        paths.append(recorded_path(fields[columns.index('_wrpMovieName')], base))
    if not in_loop or '_wrpMovieName' not in columns:
        raise ValueError('Tomostar has no declared movie column.')
    if not paths:
        raise ValueError('Tomostar declares movies but has no complete data rows.')
    return tuple(paths)


def parse_warp_xml(text: str, base: Path) -> tuple[Path, ...]:
    tree = ElementTree.fromstring(text)
    values = [element.text or '' for element in tree.iter() if element.tag.rsplit('}', 1)[-1] == 'MoviePath']
    if any(not block.strip() for block in values):
        raise ValueError('Warp XML contains an empty MoviePath entry.')
    return tuple(recorded_path(value, base) for block in values for value in block.splitlines() if value.strip())


def timestamp(value: str) -> str:
    """Canonical naive time, with recorded year width retained; no timezone/century guess."""
    value = ' '.join(value.split())
    # Only formats evidenced by MDOC fixtures: English month names and ISO dates.
    match = re.fullmatch(r'(\d{1,2})-([A-Za-z]{3})-(\d{2}|\d{4}) (\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?)', value)
    if match:
        day, month, year, clock = match.groups()
        months = {name: index for index, name in enumerate(
            ('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'), 1)}
        if month.casefold() not in months:
            raise ValueError('Unknown MDOC month.')
        # Validate, but retain a two-digit year instead of inventing its century.
        numeric_year = int(year) if len(year) == 4 else 2000+int(year)
        date = f'{numeric_year:04d}-{months[month.casefold()]:02d}-{int(day):02d}T{clock}'
        parsed = datetime.fromisoformat(date)
        return f'{year}-{parsed.month:02d}-{parsed.day:02d}T{parsed:%H:%M:%S}.{parsed.microsecond:06d}'
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?', value):
        raise ValueError('Unrecognised MDOC DateTime; no timezone or date is inferred.')
    return datetime.fromisoformat(value).isoformat(timespec='microseconds')


def signature(header: dict, rows: list[tuple[int, dict]], expected_count: int | None = None) -> MdocSignature:
    if not rows or (expected_count is not None and expected_count != len(rows)):
        raise ValueError('MDOC tilt count is missing or disagrees with the stack.')
    if sorted(z for z, _ in rows) != list(range(len(rows))):
        raise ValueError('MDOC records have duplicate or missing ZValue indexes.')
    records = []
    for z, row in sorted(rows):
        try:
            angle = Decimal(str(row['TiltAngle']))
            time = timestamp(str(row['DateTime']))
        except (KeyError, InvalidOperation) as exc:
            raise ValueError('Every MDOC tilt requires a finite angle and DateTime.') from exc
        if not angle.is_finite():
            raise ValueError('Non-finite MDOC tilt angle.')
        records.append(TiltRecord(z, time, angle))
    if len({(row.timestamp, row.angle) for row in records}) != len(records):
        raise ValueError('Duplicate MDOC time/angle records.')
    image = header.get('ImageFile')
    return MdocSignature(tuple(records), str(image) if image else None)


def parse_mdoc_link(text: str, base: Path) -> tuple[tuple[Path, ...] | str, MdocSignature | str]:
    header: dict[str, str] = {}
    rows: list[tuple[int, dict[str, str]]] = []
    current = header
    movie_issue = record_issue = ''
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        z = re.fullmatch(r'\[ZValue\s*=\s*(\d+)\]', line)
        if z:
            current = {}
            rows.append((int(z[1]), current))
        elif line.startswith('[') and line.endswith(']'):
            continue
        elif '=' in line:
            key, value = (part.strip() for part in line.split('=', 1))
            if key in current and key in ('SubFramePath', 'DateTime', 'TiltAngle', 'ImageFile'):
                if key == 'SubFramePath':
                    movie_issue = f'Duplicate MDOC {key}.'
                else:
                    record_issue = f'Duplicate MDOC {key}.'
            current[key] = value
        else:
            raise ValueError('Malformed MDOC record.')
    if not rows:
        raise ValueError('MDOC has no tilt records.')
    movies = tuple(recorded_path(row['SubFramePath'], base) for _, row in rows if row.get('SubFramePath'))
    if sorted(z for z, _ in rows) != list(range(len(rows))):
        movie_issue = 'Duplicate or missing MDOC ZValue indexes.'
    if any('SubFramePath' in row for _, row in rows) and (
            len(movies) != len(rows) or len({movie_name(p) for p in movies}) != len(movies)):
        movie_issue = 'MDOC movie list is partial or contains duplicate names.'
    try:
        record = signature(header, rows)
    except ValueError as exc:
        record = str(exc)  # Invalid C does not destroy independently complete movie evidence.
    return movie_issue or movies, record_issue or record


def read_mdoc_evidence(path: Path) -> AcquisitionEvidence:
    block = read_metadata(path, parse_mdoc_link)
    if block.state != 'complete' or block.value is None:
        return AcquisitionEvidence(movies=(Evidence(block.state, source=block.source, issue=block.issue),),
                                   mdocs=(Evidence(block.state, source=block.source, issue=block.issue),))
    movies, records = block.value
    return AcquisitionEvidence(movies=(Evidence('incomplete', source=block.source, issue=movies)
        if isinstance(movies, str) else Evidence('complete', movies, block.source),),
        mdocs=(Evidence('complete', records, block.source) if isinstance(records, MdocSignature)
               else Evidence('incomplete', source=block.source, issue=records),))


def stack_reference(path: Path) -> Evidence[Path]:
    try:
        if not path.exists() and not path.is_symlink():
            return Evidence()
        stamp = evidence_file(path)
        if is_cloud_placeholder(stamp.path):
            raise ValueError('Cloud-only stack; verification is deferred.')
        return Evidence('complete', stamp.path, stamp)
    except (OSError, ValueError, RuntimeError) as exc:
        return Evidence('incomplete', issue=f'{path}: {exc}')


def warp_evidence(root: Path, stem: str) -> AcquisitionEvidence:
    movies = read_metadata(root/'tomostar'/f'{stem}.tomostar', parse_tomostar)
    if movies.state == 'absent':
        movies = read_metadata(root/'warp_tiltseries'/f'{stem}.xml', parse_warp_xml)
    # Current layout is plural. Retain the explicit local legacy "mdoc" copy too;
    # conflicting complete copies are kept as separate evidence, never first-wins.
    mdocs = tuple(block for folder in ('mdocs', 'mdoc')
                  for block in read_mdoc_evidence(root/folder/f'{stem}.mdoc').mdocs if block.state != 'absent')
    return AcquisitionEvidence((movies,), mdocs)


def reconstruction_evidence(output: Path) -> AcquisitionEvidence:
    """Only exact dataset metadata in the same folder; never parent/sibling searches."""
    stem = reconstruction_stem(output.stem)
    names = tuple(dict.fromkeys((output.stem, stem)))
    blocks = [read_mdoc_evidence(output.parent / (name + suffix))
              for name in names for suffix in ('.mdoc', '.st.mdoc', '.mrc.mdoc')]
    return AcquisitionEvidence(tuple(block for value in blocks for block in value.movies if block.state != 'absent'),
        tuple(block for value in blocks for block in value.mdocs if block.state != 'absent'),
        (stack_reference(output.parent / f'{stem}.st'),))
