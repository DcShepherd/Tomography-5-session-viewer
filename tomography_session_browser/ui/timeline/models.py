"""Models components for the timeline."""
from __future__ import annotations
from dataclasses import dataclass
from tomography_session_browser.services.timeline_service import TimelineSegment


@dataclass(frozen=True)
class TimelineLane:
    key: str
    label: str
    segments: tuple[TimelineSegment, ...]
    grouped: bool

