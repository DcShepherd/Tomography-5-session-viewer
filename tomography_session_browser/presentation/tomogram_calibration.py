"""When to ask for a pixel size, what to suggest, and which versions one value may reach.

A header without a usable spacing (missing, non-finite or IMOD's 1 Å default)
leaves the scale bar off. The prompt offers values recorded elsewhere for the
same tomogram; the user confirms one as a display-only override. Suggestions are
never applied silently.

One value may reach several versions on the user's explicit statement for a list
they reviewed. The app does not prove that these versions have equal sampling: the
supported adapters expose header geometry, not a verified per-output binning chain,
and a shared array shape or file name cannot replace it. So only a missing or
suspicious value is offered. A valid, anisotropic or confirmed value, including a
confirmed 1 Å, is never offered or replaced.

A stated value answers the header's calibration warning, so the app stops asking.
The file has not changed: Context still shows what its header records.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
import math
from pathlib import Path
import re

from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.domain.tomograms import CALIBRATION_WARNINGS, ProcessingSource, Tomogram, Volume
from tomography_session_browser.parsers.path_utils import natural_key
from tomography_session_browser.parsers.volume_header import fingerprint
from tomography_session_browser.presentation.tomogram_versions import review_versions
from tomography_session_browser.services.volume.render import calibrated_spacing

_FILE_SPACING = re.compile(r"(\d+(?:\.\d+)?)\s*Apx", re.IGNORECASE)

CALIBRATION_SCOPE_NOTE = ("Confirm applies to this version only. The app cannot check that "
                          "other versions have the same voxel size.")
#: A file name records about two decimals, so closer than this is the same value.
NAME_TOLERANCE = .005 + 1e-9


def needs_pixel_size(volume: Volume | None, override: float | None) -> bool:
    return volume is not None and volume.grid is not None and override is None and calibrated_spacing(volume, None) is None


def _without_calibration(volume: Volume) -> tuple[str, ...]:
    return tuple(warning for warning in volume.warnings if warning not in CALIBRATION_WARNINGS)


def open_warnings(volume: Volume, stated: float | None) -> tuple[str, ...]:
    """The warnings of one version that still ask for something."""
    return volume.warnings if stated is None else _without_calibration(volume)


def tomogram_warnings(item: Tomogram, stated_for: Callable[[Volume], float | None]) -> tuple[str, ...]:
    """The open warnings of every version, each once.

    A half set is no review choice and cannot take a value. Its calibration warning
    closes once the user stated a value here and each review version has a usable one.
    """
    if not any(volume.warnings for volume in item.volumes):
        return ()  # Most tomograms, and the Summary and the list ask for each one.
    reviewed = review_versions(item)
    settled = (any(stated_for(volume) is not None for volume in reviewed)
               and all(calibrated_spacing(volume, stated_for(volume)) is not None for volume in reviewed if volume.grid is not None))
    found = (_without_calibration(volume) if settled and volume not in reviewed else open_warnings(volume, stated_for(volume))
             for volume in item.volumes)
    return tuple(dict.fromkeys(warning for warnings in found for warning in warnings))


def calibration_note(volume: Volume, stated: float | None) -> str:
    """What the Calibration section says about the value in use."""
    if stated is not None:
        return "You gave this value. The MRC header does not change."
    return " · ".join(volume.warnings) or "Calibration from the MRC header."


def header_note(volume: Volume) -> str:
    spacing = volume.grid.spacing_xyz if volume.grid is not None else (None, None, None)
    if all(value is not None and math.isfinite(value) and abs(value - 1) < 1e-6 for value in spacing):
        return "Header records 1.0 Å/px · scale bar off"
    return "Header has no usable pixel size · scale bar off"


def file_name_spacing(path: Path) -> float | None:
    """The value a file name records, such as ``TS_1_10.00Apx.mrc``. A hint, never an authority."""
    match = _FILE_SPACING.search(path.name)
    value = float(match.group(1)) if match else 0.
    return value if value > 0 else None


def pixel_size_suggestions(item: Tomogram, volume: Volume) -> tuple[tuple[str, float], ...]:
    """Isotropic spacings from the tomogram's other versions, then a value in the file name."""
    found: list[tuple[str, float]] = []
    for other in item.volumes:
        spacing = calibrated_spacing(other, None) if other is not volume else None
        if spacing and len(set(spacing)) == 1:
            found.append((f"{other.version} · {spacing[0]:.2f} Å", spacing[0]))
    named = file_name_spacing(volume.path)
    if named is not None and abs(named - 1) > 1e-6:
        found.append((f"File name · {named:.2f} Å", named))
    return tuple(dict.fromkeys(found))


@dataclass(frozen=True, slots=True)
class NotOffered:
    one: str
    many: str

    def text(self, count: int) -> str:
        return f"{count} {self.one if count == 1 else self.many}"


VALID_HEADER = NotOffered("has a valid header", "have a valid header")
ANISOTROPIC_HEADER = NotOffered("has an anisotropic header", "have an anisotropic header")
PARTLY_RECORDED = NotOffered("has a partly recorded header", "have a partly recorded header")
CONFIRMED = NotOffered("already has a value you confirmed", "already have a value you confirmed")
NO_HEADER = NotOffered("has no usable header", "have no usable header")
CHANGED = NotOffered("changed on disk", "changed on disk")
_REASON_ORDER = (VALID_HEADER, ANISOTROPIC_HEADER, PARTLY_RECORDED, CONFIRMED, NO_HEADER, CHANGED)


def not_offered_reason(volume: Volume, stated: float | None) -> NotOffered | None:
    """Why one value may not reach this version, or None when it may.

    ``needs_pixel_size`` alone is not enough: a header such as (1, 2, 3) has no
    usable calibration, but it is anisotropic and cannot collapse to one scalar.
    """
    if volume.grid is None:
        return NO_HEADER
    if stated is not None:
        return CONFIRMED
    usable = [value for value in volume.grid.spacing_xyz if value is not None and math.isfinite(value) and value > 0]
    if not usable:
        return None
    if len(usable) < 3:
        return PARTLY_RECORDED
    if all(abs(value - 1) < 1e-6 for value in usable):
        return None
    return VALID_HEADER if len(set(usable)) == 1 else ANISOTROPIC_HEADER


@dataclass(frozen=True, slots=True)
class OfferedVersion:
    tomogram: str
    source: str
    volume: Volume
    voxels: str
    named: float | None
    header: str

    @property
    def label(self) -> str:
        return f"{self.tomogram} · {self.volume.version}"

    def disagrees(self, value: float) -> bool:
        return self.named is not None and abs(self.named - value) > NAME_TOLERANCE


@dataclass(frozen=True, slots=True)
class BulkReview:
    offered: tuple[OfferedVersion, ...]
    not_offered: tuple[tuple[NotOffered, int], ...] = ()

    @property
    def not_offered_total(self) -> int:
        return sum(count for _, count in self.not_offered)

    @property
    def not_offered_text(self) -> str:
        return ", ".join(reason.text(count) for reason, count in self.not_offered)


def _offered(source: str, item: Tomogram, volume: Volume) -> OfferedVersion:
    grid = volume.grid
    assert grid is not None  # A version without a grid is never offered.
    suspicious = any(value is not None and math.isfinite(value) and value > 0 for value in grid.spacing_xyz)
    return OfferedVersion(item.name, source, volume, "×".join(map(str, grid.shape_xyz)),
                          file_name_spacing(volume.path), "1.0 Å, suspicious" if suspicious else "None")


def bulk_review(sources: Iterable[ProcessingSource], stated_for: Callable[[Volume], float | None],
                changed: Callable[[Volume], bool] = lambda _volume: False) -> BulkReview:
    """Every loaded version that needs a value, and a count for each reason the others do not."""
    loaded = tuple(sources)
    several = len(loaded) > 1
    offered: list[OfferedVersion] = []
    reasons: dict[NotOffered, int] = {}
    seen: set[str] = set()
    for source in sorted(loaded, key=lambda value: natural_key(value.name)):
        for item in sorted(source.tomograms, key=lambda value: natural_key(value.name)):
            for volume in review_versions(item):
                if volume.id in seen:
                    continue
                seen.add(volume.id)
                reason = CHANGED if changed(volume) else not_offered_reason(volume, stated_for(volume))
                if reason is None:
                    offered.append(_offered(source.name if several else "", item, volume))
                else:
                    reasons[reason] = reasons.get(reason, 0) + 1
    return BulkReview(tuple(offered), tuple((reason, reasons[reason]) for reason in _REASON_ORDER if reason in reasons))


def offered_count(sources: Iterable[ProcessingSource], stated_for: Callable[[Volume], float | None],
                  changed: Callable[[Volume], bool] = lambda _volume: False) -> int:
    """The size of the review list, without building its rows."""
    seen: set[str] = set()
    for source in sources:
        for item in source.tomograms:
            for volume in review_versions(item):
                if not changed(volume) and not_offered_reason(volume, stated_for(volume)) is None:
                    seen.add(volume.id)
    return len(seen)


@dataclass(frozen=True, slots=True)
class BulkActions:
    """What the buttons say and do for the current ticks (mockup board 2b)."""
    count: str
    warning: str
    all_label: str
    all_primary: bool
    all_enabled: bool
    selected_label: str | None
    selected_primary: bool
    selected_enabled: bool


def _disagreeing(action: str, count: int) -> str:
    return f"{action} includes {count_phrase(count, 'file name')} that {'disagrees' if count == 1 else 'disagree'}"


def bulk_actions(total: int, ticked: int, disagreeing: int, disagreeing_ticked: int) -> BulkActions:
    count = f"{ticked} of {total} selected"
    if disagreeing:
        count += f" · {count_phrase(disagreeing, 'file name')} {'disagrees' if disagreeing == 1 else 'disagree'}"
    all_label = f"Apply to all {total}"
    warnings = [_disagreeing("Apply to all", disagreeing)] if disagreeing else []
    if total == 0 or ticked == total:
        return BulkActions(count, " · ".join(warnings), all_label, total > 0, total > 0, None, False, False)
    if ticked == 0:
        return BulkActions(count, " · ".join(warnings), all_label, False, True, "Apply to 0 selected", False, False)
    if disagreeing_ticked:
        warnings.insert(0, _disagreeing(f"Apply to {ticked} selected", disagreeing_ticked))
    return BulkActions(count, " · ".join(warnings), all_label, False, True, f"Apply to {ticked} selected", True, True)


def changed_on_disk(volumes: Iterable[Volume]) -> tuple[Volume, ...]:
    """Versions whose file no longer matches the fingerprint that was reviewed. Reads no voxels."""
    changed: list[Volume] = []
    for volume in volumes:
        try:
            current = fingerprint(volume.path)
        except (OSError, ValueError):
            changed.append(volume)
            continue
        if (current.size, current.mtime_ns) != (volume.fingerprint.size, volume.fingerprint.mtime_ns):
            changed.append(volume)
    return tuple(changed)


def changed_notice(labels: tuple[str, ...]) -> str:
    """Says which versions stopped the action, in a short line for any number of them."""
    shown = ", ".join(labels[:3])
    more = f" and {len(labels) - 3} more" if len(labels) > 3 else ""
    return f"{shown}{more} changed on disk. Review the updated list, then apply again."
