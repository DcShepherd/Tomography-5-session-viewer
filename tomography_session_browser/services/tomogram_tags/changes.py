"""Compare a tag's saved source evidence with the loaded snapshot."""
from dataclasses import dataclass
from enum import StrEnum

from tomography_session_browser.domain.tomogram_tags import ChangeKind, TomogramRef, VolumeEvidence


class MissingEvidence(StrEnum):
    ABSENT = "absent"
    UNREADABLE = "unreadable"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class VolumeChange:
    kind: ChangeKind
    saved: VolumeEvidence
    live: VolumeEvidence | None
    missing: MissingEvidence | None = None


def compare_volumes(saved: TomogramRef, live: TomogramRef, *,
                    unreadable: frozenset[str] = frozenset(),
                    versions_complete: bool = False) -> tuple[VolumeChange, ...]:
    current = {version.relative: version for version in live.volumes}
    changes = []
    for before in saved.volumes:
        after = current.get(before.relative)
        if after is None:
            missing = MissingEvidence.ABSENT if versions_complete else MissingEvidence.UNKNOWN
            if before.relative in unreadable:
                missing = MissingEvidence.UNREADABLE
            changes.append(VolumeChange(ChangeKind.MISSING, before, None, missing))
        elif (before.size, before.grid, before.dtype) != (after.size, after.grid, after.dtype):
            changes.append(VolumeChange(ChangeKind.FILE_FACTS, before, after))
        elif before.mtime_ns != after.mtime_ns:
            changes.append(VolumeChange(ChangeKind.DATE, before, after))
    return tuple(changes)


def worst_kind(changes: tuple[VolumeChange, ...]) -> ChangeKind | None:
    kinds = {change.kind for change in changes}
    return next((kind for kind in ChangeKind if kind in kinds), None)


def established(changes: tuple[VolumeChange, ...]) -> tuple[VolumeChange, ...]:
    return tuple(change for change in changes if change.missing in (None, MissingEvidence.ABSENT))
