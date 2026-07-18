"""Acquisition timeline derivation.

The session files we ship against do not record explicit pause / resume
events (see the disk audit in the plan: no log files, no ``Skipped`` /
``Failed`` flags in mdoc). What they *do* record is a ``DateTime`` per
``MdocSection``. By looking at gaps between consecutive sections of the same
tilt-series we can infer when acquisition stopped for an unusual length of
time — most often an LN2 fill or a cassette swap. We never claim to know
*why* the gap occurred; the timeline strip in the dashboard renders these as
"inferred pause" segments and the tooltip explicitly says how the value was
derived.

Public API:

* ``parse_section_datetime(section_metadata)`` — best-effort parser for the
  several ``DateTime`` formats SerialEM / Tomo5 mdoc files use.
* ``build_acquisition_timeline(tilt_series)`` — collapses one tilt-series
  into a span (start, end, sampled times).
* ``detect_inferred_pauses(timeline)`` — returns pause segments using a
  ``5 * median(delta)`` heuristic (configurable via ``factor``).
* ``build_session_timeline(session)`` — convenience: aggregates timelines and
  pauses across every tilt series in a session.

Pure functions, no UI imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from tomography_session_browser.domain.models import Sample, Session, TiltSeries


_DATETIME_FORMATS: tuple[str, ...] = (
    "%d-%b-%Y  %H:%M:%S",
    "%d-%b-%Y %H:%M:%S",
    "%d-%b-%y  %H:%M:%S",
    "%d-%b-%y %H:%M:%S",
    "%d-%m-%Y  %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
)


@dataclass(frozen=True)
class TimelineSegment:
    """One contiguous acquisition window for a single tilt-series."""

    tilt_series_id: str
    label: str
    start: datetime
    end: datetime
    sample_times: tuple[datetime, ...]


@dataclass(frozen=True)
class InferredPause:
    """A gap in mdoc acquisition large enough to be flagged for the user.

    ``factor`` is how many multiples of the median inter-section delta this
    gap represents — the higher the value, the more confident the inference.
    """

    tilt_series_id: str
    label: str
    start: datetime
    end: datetime
    seconds: float
    factor: float

    @property
    def tooltip(self) -> str:
        minutes = max(self.seconds / 60.0, 0.0)
        if minutes >= 1.0:
            duration = f"{minutes:.1f} min"
        else:
            duration = f"{self.seconds:.0f} s"
        return (
            f"Inferred pause ({duration} gap on {self.label}). "
            f"~{self.factor:.0f}× the median inter-section spacing — derived "
            f"from mdoc DateTime values, no explicit pause event was recorded."
        )


@dataclass(frozen=True)
class SessionTimeline:
    segments: tuple[TimelineSegment, ...]
    pauses: tuple[InferredPause, ...]

    @property
    def start(self) -> datetime | None:
        if not self.segments:
            return None
        return min(seg.start for seg in self.segments)

    @property
    def end(self) -> datetime | None:
        if not self.segments:
            return None
        return max(seg.end for seg in self.segments)


def parse_section_datetime(metadata: dict[str, Any] | None) -> datetime | None:
    """Best-effort parser for the ``DateTime`` field in an mdoc section."""

    if not metadata:
        return None
    raw = metadata.get("DateTime") or metadata.get("datetime") or metadata.get("date_time")
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    # Last-ditch: try ISO 8601 directly. ``fromisoformat`` is permissive on 3.11+.
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def build_acquisition_timeline(tilt_series: TiltSeries) -> TimelineSegment | None:
    """Return a ``TimelineSegment`` for ``tilt_series``, or ``None`` if there is
    not enough timestamped mdoc data to build one."""

    times: list[datetime] = []
    for section in tilt_series.sections:
        ts = parse_section_datetime(section.metadata)
        if ts is not None:
            times.append(ts)
    if not times:
        return None
    times.sort()
    return TimelineSegment(
        tilt_series_id=tilt_series.id,
        label=tilt_series.name,
        start=times[0],
        end=times[-1],
        sample_times=tuple(times),
    )


def detect_inferred_pauses(
    segment: TimelineSegment, *, factor: float = 5.0, min_seconds: float = 60.0
) -> list[InferredPause]:
    """Return any inter-section gaps that exceed ``factor * median(delta)``.

    ``min_seconds`` keeps the heuristic from firing on fast tomograms where the
    median delta is a fraction of a second; pauses below this are not useful
    to surface to the user.
    """

    if len(segment.sample_times) < 3:
        return []
    deltas = [
        (segment.sample_times[i + 1] - segment.sample_times[i]).total_seconds()
        for i in range(len(segment.sample_times) - 1)
    ]
    sorted_deltas = sorted(deltas)
    median = _median(sorted_deltas)
    if median <= 0:
        return []
    threshold = max(median * factor, min_seconds)
    pauses: list[InferredPause] = []
    for i, delta in enumerate(deltas):
        if delta < threshold:
            continue
        pauses.append(
            InferredPause(
                tilt_series_id=segment.tilt_series_id,
                label=segment.label,
                start=segment.sample_times[i],
                end=segment.sample_times[i + 1],
                seconds=delta,
                factor=delta / median,
            )
        )
    return pauses


def build_session_timeline(session: Session) -> SessionTimeline:
    segments: list[TimelineSegment] = []
    pauses: list[InferredPause] = []
    for tilt_series in _iter_tilt_series(session):
        segment = build_acquisition_timeline(tilt_series)
        if segment is None:
            continue
        segments.append(segment)
        pauses.extend(detect_inferred_pauses(segment))
    segments.sort(key=lambda s: s.start)
    pauses.sort(key=lambda p: p.start)
    return SessionTimeline(segments=tuple(segments), pauses=tuple(pauses))


def _iter_tilt_series(session: Session) -> Iterable[TiltSeries]:
    seen: set[str] = set()
    for tilt in session.tilt_series:
        key = _tilt_identity_key(tilt)
        if key in seen:
            continue
        seen.add(key)
        yield tilt
    for sample in session.samples:
        for tilt in sample.tilt_series:
            key = _tilt_identity_key(tilt)
            if key in seen:
                continue
            seen.add(key)
            yield tilt


def _tilt_identity_key(tilt_series: TiltSeries) -> str:
    if tilt_series.mrc_path:
        return str(tilt_series.mrc_path).replace("\\", "/").casefold()
    if tilt_series.mdoc_path:
        return str(tilt_series.mdoc_path).replace("\\", "/").casefold()
    return tilt_series.id


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    n = len(values)
    if n % 2 == 1:
        return float(values[n // 2])
    return (values[n // 2 - 1] + values[n // 2]) / 2.0
