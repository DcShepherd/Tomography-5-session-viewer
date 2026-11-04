"""Models components for the timeline."""
from __future__ import annotations
from dataclasses import dataclass
from enum import StrEnum
from tomography_session_browser.services.timeline_service import TimelineSegment


class TimelineMode(StrEnum):
    """How the timeline draws its segments, in the order its control offers them."""

    DENSITY = "density"
    LANES = "lanes"
    AUTO = "auto"

    @classmethod
    def of(cls, value: str) -> TimelineMode:
        """``value`` as a mode; anything unrecognised is ``AUTO``."""

        try:
            return cls(value)
        except ValueError:
            return cls.AUTO

    @property
    def label(self) -> str:
        return self.value.title()

    @property
    def tooltip(self) -> str:
        return _MODE_TOOLTIPS[self]


_MODE_TOOLTIPS = {
    TimelineMode.DENSITY: "Binned session-scale density view",
    TimelineMode.LANES: "Grouped lanes by sample or batch position",
    TimelineMode.AUTO: "Lanes when every lane has a row tall enough for its name, otherwise density",
}


@dataclass(frozen=True)
class TimelineLane:
    key: str
    label: str
    segments: tuple[TimelineSegment, ...]
    grouped: bool

