"""Plane-only capture navigation on verified, untransformed reconstruction grids."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from tomography_session_browser.domain.tomograms import Tomogram, Volume
from tomography_session_browser.presentation.tomogram_capture_history import CaptureEntry, CapturePage
from tomography_session_browser.presentation.tomogram_state import TomogramViewState
from tomography_session_browser.presentation.tomogram_versions import GeometryRefusal, review_versions, share_geometry
from tomography_session_browser.services.volume.models import RenderRecipe
from tomography_session_browser.services.volume.render import plane_geometry


@dataclass(frozen=True, slots=True)
class PlaneCapture:
    plane: int
    current_version: bool
    entry: CaptureEntry


@dataclass(frozen=True, slots=True)
class CaptureMarks:
    captures: tuple[PlaneCapture, ...] = ()
    notices: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MarkCluster:
    position: float
    captures: tuple[PlaneCapture, ...]

    @property
    def planes(self) -> tuple[int, ...]:
        return tuple(sorted({capture.plane for capture in self.captures}))


def transformed(view: TomogramViewState | RenderRecipe) -> bool:
    return any(view.rotation) or view.pivot_xyz != view.pivot_grid_xyz


def capture_marks(page: CapturePage, row: Tomogram, target: Volume,
                  state: TomogramViewState, displayed: RenderRecipe | None, *,
                  changed_sources: frozenset[str] = frozenset()) -> CaptureMarks:
    """A capture supplies a plane only; no image settings or version are restored."""
    if displayed is None or displayed.axis != state.axis:
        return CaptureMarks(notices=("Capture marks await the active pane's pixels.",))
    if state.row_key != row.id or state.volume_id != target.id or target not in review_versions(row):
        return CaptureMarks(notices=("Capture marks require the selected reconstruction identity.",))
    if target.id in changed_sources:
        return CaptureMarks(notices=("Capture marks are hidden: the selected source is changed or missing.",))
    if transformed(state) or transformed(displayed):
        return CaptureMarks(notices=("Capture marks are hidden for a transformed view.",))
    if target.grid is None or state.volume_signature not in (None, (target.fingerprint.size, target.fingerprint.mtime_ns)):
        return CaptureMarks(notices=("Capture marks require an unchanged source with a usable grid.",))
    volumes = {volume.id: volume for volume in review_versions(row)}
    omitted: Counter[str] = Counter()
    values = []
    for entry in page.entries:
        saved, recipe = entry.state, entry.record.request.recipe
        source = volumes.get(saved.volume_id or "")
        reason = ""
        if saved.row_key != row.id or source is None:
            reason = "unavailable reconstruction versions"
        elif source.id in changed_sources or entry.restore_notice or saved.volume_signature != (source.fingerprint.size, source.fingerprint.mtime_ns):
            reason = "changed or missing sources"
        elif transformed(saved) or transformed(recipe):
            reason = "transformed captures"
        elif recipe.axis != state.axis:
            reason = "captures on other axes"
        elif source.id != target.id:
            if isinstance(share_geometry(row, source, target, saved, state), GeometryRefusal):
                reason = "incompatible grids or display calibration"
        if not reason and not 0 <= recipe.plane < plane_geometry(target, state.axis)[2]:
            reason = "out-of-range planes"
        if reason:
            omitted[reason] += 1
        else:
            values.append(PlaneCapture(recipe.plane, source is not None and source.id == target.id, entry))
    notices = [f"{count} omitted: {reason}." for reason, count in sorted(omitted.items())]
    if page.total > len(page.entries):
        notices.insert(0, f"Only the newest {len(page.entries)} of {page.total} captures are considered; some may be ineligible.")
    return CaptureMarks(tuple(sorted(values, key=lambda value: (value.plane, value.entry.id))), tuple(notices))


def cluster_marks(marks: CaptureMarks, maximum: int, start: float, end: float, *,
                  reversed_: bool = False) -> tuple[MarkCluster, ...]:
    """Cluster adjacent marks less than four logical pixels apart on the groove."""
    positioned = []
    for capture in marks.captures:
        fraction = capture.plane / maximum if maximum > 0 else 0.
        if reversed_:
            fraction = 1. - fraction
        positioned.append((start + fraction * (end - start), capture))
    positioned.sort(key=lambda value: (value[0], value[1].entry.id))
    groups: list[list[tuple[float, PlaneCapture]]] = []
    for value in positioned:
        if not groups or value[0] - groups[-1][-1][0] >= 4:
            groups.append([])
        groups[-1].append(value)
    return tuple(MarkCluster(sum(value[0] for value in group) / len(group),
                            tuple(value[1] for value in group)) for group in groups)
