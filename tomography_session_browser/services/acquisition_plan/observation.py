"""Observed stack measurements, independent of planned geometry."""
from collections import Counter
from dataclasses import dataclass

from tomography_session_browser.domain.coerce import float_or_none
from tomography_session_browser.domain.models import TiltSeries
from tomography_session_browser.services.tilt_angle_service import plausible_tilt_angles, preloaded_stack_angles


@dataclass(frozen=True, slots=True)
class Observation:
    available_frames: int | None
    declared_depth: int | None
    mdoc_records: int
    angles: tuple[float, ...] = ()
    angular_disagreement: bool = False
    measured_range: tuple[float | None, float | None, float | None] = (None, None, None)
    payload_unreadable: bool = False

    @property
    def reported_count(self) -> int | None:
        """The image count to show; ``None`` when an unreadable stack hides it.

        MDOC records name planned or attempted frames. They never stand in for
        the count of an unreadable stack.
        """
        if self.available_frames is not None:
            return self.available_frames
        if self.payload_unreadable:
            return None
        return self.declared_depth or self.mdoc_records


def observe(tilt: TiltSeries) -> Observation:
    metadata = tilt.mrc_metadata
    available = metadata.available_frames if metadata is not None else None
    available = max(available, 0) if available is not None else None
    declared = metadata.nz if metadata is not None and metadata.nz and metadata.nz > 0 else None
    angles, disagree = preloaded_stack_angles(tilt, available) if available else ((), False)
    measured = (min(angles), max(angles), _modal_increment(list(angles))) if angles else _observed_from_mdoc(tilt)
    if measured[0] is None:
        measured = _observed_from_mrc(tilt)
    unreadable = metadata is not None and metadata.data_complete is False and available is None
    return Observation(available, declared, len(tilt.sections), angles, disagree, measured, unreadable)


def _observed_from_mdoc(
    tilt_series: TiltSeries,
) -> tuple[float | None, float | None, float | None]:
    angles = _mdoc_tilt_angles(tilt_series)
    if not plausible_tilt_angles(angles):
        return None, None, None
    if len(angles) < 2:
        return angles[0], angles[0], None
    increment = _modal_increment(angles)
    return min(angles), max(angles), increment


def _observed_from_mrc(
    tilt_series: TiltSeries,
) -> tuple[float | None, float | None, float | None]:
    metadata = getattr(tilt_series, "mrc_metadata", None)
    if metadata is None:
        return None, None, None
    angles = list(getattr(metadata, "tilt_angles", []) or [])
    if not plausible_tilt_angles(angles):
        return None, None, None
    if len(angles) < 2:
        return angles[0], angles[0], None
    increment = _modal_increment(angles)
    return min(angles), max(angles), increment


def _mdoc_tilt_angles(tilt_series: TiltSeries) -> list[float]:
    angles: list[float] = []
    for section in tilt_series.sections:
        meta = section.metadata or {}
        for key in ("TiltAngle", "tilt_angle", "Tilt"):
            value = float_or_none(meta.get(key))
            if value is not None:
                angles.append(value)
                break
    return angles


def _modal_increment(values: list[float]) -> float | None:
    """Return the most common positive delta between adjacent sorted angles.

    Tilt schemes are usually uniform, so the modal step recovers the
    increment cleanly even with a few dropped sections. Falls back to
    ``None`` when no positive deltas exist.
    """

    sorted_values = sorted(values)
    deltas = [
        round(sorted_values[i + 1] - sorted_values[i], 3)
        for i in range(len(sorted_values) - 1)
    ]
    deltas = [d for d in deltas if d > 0]
    if not deltas:
        return None
    counter = Counter(deltas)
    return counter.most_common(1)[0][0]
