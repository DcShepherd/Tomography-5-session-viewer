"""Which frame of a tilt series' thumbnail stack shows its exposure area.

Tomography 5 keeps a downsampled copy of every tilt-series stack in a
``Thumbnails`` folder beside it (see ``parsers.tilt_thumbnail_parser``), in
the same frame order and with the same per-frame extended header. The frame
that shows the exposure area is the one at the series' reference angle: 0°
for a flat specimen, the pretilt for a lamella, where a tilt series starts
from the pretilt rather than from 0°.

Pure: parsed metadata in, a frame choice out. No Qt, no file access.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from tomography_session_browser.domain.models import MrcMetadata, TiltSeries
from tomography_session_browser.parsers.xml_parser import find_first
from tomography_session_browser.services.tilt_angle_service import explicit_stack_order_tilt_angles

#: ``tilt_angle_service``'s name for angles read from a stack's own extended header.
_OWN_HEADER = "MRC extended header"
#: How far the chosen frame may lie from the reference angle and still be "at" it.
#: Recorded angles sit within a few hundredths of the nominal one; the
#: smallest tilt step is about 1°.
REFERENCE_TOLERANCE_DEG = 0.5


@dataclass(frozen=True, slots=True)
class ExposureFrame:
    """The thumbnail frame nearest the reference angle, and its record."""

    index: int  # stack order, from 0
    frame_count: int
    tilt_angle: float
    reference_angle: float
    #: Where the angles that chose the frame came from, e.g. "MRC extended header".
    angle_source: str
    #: The frame's extended-header record (a ``MrcMetadata.frame_metadata`` entry).
    record: Mapping[str, Any] | None = None
    #: Whether ``record`` is the thumbnail's own rather than the stack's.
    record_is_thumbnail: bool = False

    @property
    def misses_reference(self) -> bool:
        """No frame at the reference angle: the nearest is another tilt."""

        return abs(self.tilt_angle - self.reference_angle) > REFERENCE_TOLERANCE_DEG


def is_lamella_session(session_dm: Mapping[str, Any] | None) -> bool:
    """Whether a parsed ``Session.dm`` describes a lamella session.

    Tomography 5 records ``<LamellaWorkflow>`` and ``<SpecimenType>`` (a flat
    grid is ``Slab``). Either naming a lamella is enough.
    """

    if not isinstance(session_dm, Mapping):
        return False
    workflow = find_first(session_dm, "LamellaWorkflow")
    if workflow is True or (isinstance(workflow, str) and workflow.strip().casefold() == "true"):
        return True
    specimen = find_first(session_dm, "SpecimenType")
    return isinstance(specimen, str) and "lamella" in specimen.casefold()


def starting_tilt_angle(tilt: TiltSeries) -> float | None:
    """The angle a tilt series was acquired from first; a lamella's pretilt.

    Tomography 5 writes MDOC sections in acquisition order, so the section
    with the lowest ZValue is the first image taken. ``None`` without an
    MDOC angle for it.
    """

    first = min(
        (section for section in tilt.sections if _angle(section.metadata.get("TiltAngle")) is not None),
        key=lambda section: section.z_value,
        default=None,
    )
    return _angle(first.metadata.get("TiltAngle")) if first is not None else None


def nearest_angle_index(angles: Sequence[float], target: float = 0.0) -> int | None:
    """Index of the angle closest to ``target``; the earlier frame wins a tie."""

    if not angles:
        return None
    return min(range(len(angles)), key=lambda index: (abs(angles[index] - target), index))


def exposure_frame(
    thumbnail: MrcMetadata,
    tilt: TiltSeries,
    reference_angle: float = 0.0,
) -> ExposureFrame | None:
    """The frame of ``tilt``'s thumbnail stack nearest ``reference_angle``.

    The angles come, in order, from:

    1. the thumbnail's own extended header, which describes its frames;
    2. when the thumbnail has as many frames as the stack (it keeps the
       stack's order), the stack's explicit angles by the viewer's rule:
       its extended header, then its MDOC, then a ``.tlt`` file;
    3. otherwise the tilt series' MDOC or a ``.tlt`` beside the thumbnail,
       if either describes exactly the thumbnail's frames.

    Angles are never inferred from a tilt range, so ``None`` means no
    recorded angle says which frame is which.
    """

    frame_count = int(thumbnail.nz or 0)
    if frame_count < 1:
        return None
    # The viewer's rule, applied to the thumbnail as if it were the stack.
    as_stack = explicit_stack_order_tilt_angles(
        replace(tilt, mrc_path=thumbnail.path, mrc_metadata=thumbnail), frame_count,
    )
    found = as_stack if as_stack is not None and as_stack[1] == _OWN_HEADER else None
    if found is None and frame_count == _stack_frame_count(tilt):
        stack_angles = explicit_stack_order_tilt_angles(tilt, frame_count)
        if stack_angles is not None:
            found = (stack_angles[0], f"tilt-series {stack_angles[1]}")
    if found is None:
        found = as_stack
    if found is None:
        return None
    angles, source = found
    index = nearest_angle_index(angles, reference_angle)
    if index is None:
        return None
    record = _record(thumbnail, index, frame_count)
    own = record is not None
    if record is None and tilt.mrc_metadata is not None:
        # The stack's record for the same frame: same acquisition, same order.
        record = _record(tilt.mrc_metadata, index, frame_count)
    return ExposureFrame(
        index=index,
        frame_count=frame_count,
        tilt_angle=float(angles[index]),
        reference_angle=float(reference_angle),
        angle_source=source,
        record=record,
        record_is_thumbnail=own,
    )


def _angle(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    angle = float(value)
    return angle if math.isfinite(angle) and -90 <= angle <= 90 else None


def _stack_frame_count(tilt: TiltSeries) -> int:
    metadata = tilt.mrc_metadata
    if metadata is not None and metadata.nz:
        return int(metadata.nz)
    return len(tilt.sections)


def _record(metadata: MrcMetadata, index: int, frame_count: int) -> Mapping[str, Any] | None:
    """Frame ``index``'s record, when there is one record per frame."""

    records = metadata.frame_metadata
    if len(records) != frame_count or not isinstance(records[index], Mapping):
        return None
    return records[index]


__all__ = [
    "REFERENCE_TOLERANCE_DEG",
    "ExposureFrame",
    "exposure_frame",
    "is_lamella_session",
    "nearest_angle_index",
    "starting_tilt_angle",
]
