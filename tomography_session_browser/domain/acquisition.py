"""Immutable acquisition-link evidence; absence and failed reads are distinct."""
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Generic, Literal, TypeVar
import unicodedata

T = TypeVar('T')
LinkStep = Literal['A', 'B', 'C', 'D', 'E']
MatchState = Literal['absent', 'complete-no-match', 'unique', 'ambiguous', 'incomplete']


@dataclass(frozen=True, slots=True)
class EvidenceFile:
    path: Path
    size: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class Evidence(Generic[T]):
    state: Literal['absent', 'complete', 'incomplete'] = 'absent'
    value: T | None = None
    source: EvidenceFile | None = None
    issue: str = ''

    def __post_init__(self) -> None:
        if self.state == 'complete' and self.value is None:
            raise ValueError('Complete acquisition evidence requires a value.')


@dataclass(frozen=True, slots=True)
class TiltRecord:
    z_value: int
    timestamp: str
    angle: Decimal


@dataclass(frozen=True, slots=True)
class MdocSignature:
    records: tuple[TiltRecord, ...]
    image_file: str | None = None


@dataclass(frozen=True, slots=True)
class AcquisitionEvidence:
    # A uses recorded paths and B derives names from the same complete tables.
    movies: tuple[Evidence[tuple[Path, ...]], ...] = ()
    mdocs: tuple[Evidence[MdocSignature], ...] = ()
    stacks: tuple[Evidence[Path], ...] = ()


@dataclass(frozen=True, slots=True)
class EvidenceMatch:
    state: MatchState
    candidates: tuple[str, ...] = ()
    issue: str = ''
    warnings: tuple[str, ...] = ()


def movie_name(path: str | Path) -> str:
    """Exact NFC/case-insensitive basename, with either platform's separators."""
    return unicodedata.normalize('NFC', PurePosixPath(str(path).replace('\\', '/')).name).casefold()
