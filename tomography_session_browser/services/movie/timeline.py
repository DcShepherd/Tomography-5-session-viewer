"""Which plane each movie frame shows: sweeps, holds and the exact frame count.

Planes are zero-based here and one-based in the dialog. The dialog, its preview,
the summary and the export all read this one timeline, so they cannot disagree.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import math

SINGLE_PASS, BOUNCE = "Single pass", "Bounce"
ENDS = (SINGLE_PASS, BOUNCE)
OUT, BACK = "out", "back"
#: Start, Turn and End holds, in seconds.
HOLD_RANGE = (0., 10.)
#: Holds at chosen frames, in seconds; Hold here adds the default.
CHOSEN_RANGE = (.5, 10.)
CHOSEN_DEFAULT = 1.
FPS_RANGE = (1, 60)
QUICK_RATES = (5, 10, 15, 24, 25, 30, 50, 60)
#: The plane band's end behaviours, as `Playback.end` lists them.
BAND_ENDS = ("Stop at end", "Loop", "Bounce")


@dataclass(frozen=True, slots=True)
class ChosenHold:
    """A hold that belongs to one plane in one direction; it never moves to a nearby plane."""
    plane: int
    direction: str = OUT
    seconds: float = CHOSEN_DEFAULT


@dataclass(frozen=True, slots=True)
class TimelineSettings:
    first: int
    last: int
    step: int = 1
    fps: int = 10
    end: str = SINGLE_PASS
    loop_ready: bool = True
    start_hold: float = 0.
    turn_hold: float = 0.
    end_hold: float = 0.
    chosen: tuple[ChosenHold, ...] = ()

    @property
    def bounce(self) -> bool:
        return self.end == BOUNCE

    @property
    def end_available(self) -> bool:
        """A loop-ready Bounce has no last plane to hold."""
        return not (self.bounce and self.loop_ready)


@dataclass(frozen=True, slots=True)
class MovieFrame:
    plane: int
    direction: str = OUT
    #: A repeat of the frame before it: "start", "turn", "end" or "chosen".
    hold: str = ""
    #: "first", "turn" or "last" for the frames that carry those holds.
    role: str = ""


@dataclass(frozen=True, slots=True)
class MovieTimeline:
    settings: TimelineSettings
    samples: tuple[int, ...]
    frames: tuple[MovieFrame, ...]
    #: Chosen holds whose plane or direction this movie does not show.
    unused: tuple[ChosenHold, ...] = ()

    @property
    def count(self) -> int:
        return len(self.frames)

    @property
    def duration(self) -> float:
        return len(self.frames) / self.settings.fps

    @property
    def held(self) -> int:
        return sum(1 for frame in self.frames if frame.hold)

    @property
    def short_step(self) -> tuple[int, int] | None:
        """The shorter last step, as zero-based planes, when the step leaves a remainder."""
        if len(self.samples) < 2 or abs(self.samples[-1] - self.samples[-2]) == self.settings.step:
            return None
        return self.samples[-2], self.samples[-1]

    def time(self, index: int) -> float:
        return index / self.settings.fps

    def base(self, index: int) -> int:
        """The frame that a held frame repeats."""
        while index > 0 and self.frames[index].hold:
            index -= 1
        return index

    def segments(self) -> tuple[tuple[int, int, bool], ...]:
        """(first frame, frame count, held) for each run of the timeline bar."""
        runs: list[tuple[int, int, bool]] = []
        for index, frame in enumerate(self.frames):
            held = bool(frame.hold)
            if runs and runs[-1][2] == held:
                start, count, _ = runs[-1]
                runs[-1] = (start, count + 1, held)
            else:
                runs.append((index, 1, held))
        return tuple(runs)

    def frame_of(self, plane: int, direction: str) -> int | None:
        return next((index for index, frame in enumerate(self.frames)
                     if not frame.hold and frame.plane == plane and frame.direction == direction), None)


def hold_frames(seconds: float, fps: int) -> int:
    """floor(seconds × fps + 0.5), exact for the decimal seconds that the dialog accepts."""
    return math.floor(Fraction(seconds).limit_denominator(1000) * fps + Fraction(1, 2))


def sweep(first: int, last: int, step: int) -> tuple[int, ...]:
    """Inclusive forward samples. The To plane is always included, so a remainder gives a shorter last step."""
    direction = 1 if last >= first else -1
    samples = list(range(first, last + direction, step * direction))
    if samples[-1] != last:
        samples.append(last)
    return tuple(samples)


def validate(settings: TimelineSettings, planes: int) -> None:
    """Refuse a timeline with a reason the dialog can show as it is."""
    if not 0 <= settings.first < planes or not 0 <= settings.last < planes:
        raise ValueError(f"From and To must be planes 1 to {planes}.")
    if settings.first == settings.last:
        raise ValueError("A movie needs two or more planes. Change From or To.")
    if type(settings.step) is not int or settings.step < 1:
        raise ValueError("Step must be 1 or more.")
    if type(settings.fps) is not int or not FPS_RANGE[0] <= settings.fps <= FPS_RANGE[1]:
        raise ValueError(f"Rate must be {FPS_RANGE[0]} to {FPS_RANGE[1]} frames per second.")
    if settings.end not in ENDS:
        raise ValueError("End must be Single pass or Bounce.")
    for seconds in (settings.start_hold, settings.turn_hold, settings.end_hold):
        if not math.isfinite(seconds) or not HOLD_RANGE[0] <= seconds <= HOLD_RANGE[1]:
            raise ValueError("Each hold must be 0 to 10 seconds.")
    for hold in settings.chosen:
        if not math.isfinite(hold.seconds) or not CHOSEN_RANGE[0] <= hold.seconds <= CHOSEN_RANGE[1]:
            raise ValueError("A hold at a chosen frame must be 0.5 to 10 seconds.")
        if hold.direction not in (OUT, BACK):
            raise ValueError("A hold at a chosen frame must be on the way out or on the way back.")


def build_timeline(settings: TimelineSettings, planes: int) -> MovieTimeline:
    validate(settings, planes)
    samples = sweep(settings.first, settings.last, settings.step)
    base = [MovieFrame(plane, OUT) for plane in samples]
    if settings.bounce:
        back = samples[-2:0:-1] if settings.loop_ready else samples[-2::-1]
        base += [MovieFrame(plane, BACK) for plane in back]
    roles = {0: "first", len(base) - 1: "last"}
    if settings.bounce:
        roles[len(samples) - 1] = "turn"
    chosen = {(hold.plane, hold.direction): hold for hold in settings.chosen}
    frames: list[MovieFrame] = []
    used: set[tuple[int, str]] = set()
    fps = settings.fps
    for index, frame in enumerate(base):
        role = roles.get(index, "")
        frames.append(MovieFrame(frame.plane, frame.direction, role=role))
        repeats: list[tuple[str, int]] = []
        if index == 0:
            repeats.append(("start", hold_frames(settings.start_hold, fps)))
        if role == "turn":
            repeats.append(("turn", hold_frames(settings.turn_hold, fps)))
        key = (frame.plane, frame.direction)
        if key in chosen and key not in used:
            used.add(key)
            repeats.append(("chosen", hold_frames(chosen[key].seconds, fps)))
        if index == len(base) - 1 and settings.end_available:
            repeats.append(("end", hold_frames(settings.end_hold, fps)))
        for kind, count in repeats:
            frames.extend(MovieFrame(frame.plane, frame.direction, hold=kind) for _ in range(count))
    unused = tuple(hold for hold in settings.chosen if (hold.plane, hold.direction) not in used)
    return MovieTimeline(settings, samples, tuple(frames), unused)


def hold_here_reason(timeline: MovieTimeline, index: int) -> str:
    """Why Hold here is off on this frame, or "" when it may add a hold."""
    base = timeline.base(index)
    frame = timeline.frames[base]
    if any((hold.plane, hold.direction) == (frame.plane, frame.direction) for hold in timeline.settings.chosen):
        return "This frame has a hold. Change its time under Timing."
    if frame.role == "first":
        return "This is the first frame. Use the Start hold."
    if frame.role == "turn":
        return "This is the turning plane. Use the Turn hold."
    if frame.role == "last":
        return ("This is the last frame. Use the End hold." if timeline.settings.end_available
                else "This is the last frame. A loop-ready movie continues from its first plane.")
    return ""


def unused_reason(hold: ChosenHold, settings: TimelineSettings, axis: str) -> str:
    where = f"{axis} {hold.plane + 1}" + (" (back)" if hold.direction == BACK else "")
    if hold.direction == BACK and not settings.bounce:
        return f"The hold on {where} is not used: this movie does not go back."
    return f"The hold on {where} is not used: this movie does not show that plane."


def band_defaults(rate: int, band_end: str) -> tuple[int, str]:
    """The band's rate and end behaviour as movie defaults: Bounce gives Bounce; Stop at end and Loop give Single pass."""
    if band_end not in BAND_ENDS:
        raise ValueError("Unknown plane-band end behaviour.")
    return max(FPS_RANGE[0], min(FPS_RANGE[1], int(rate))), BOUNCE if band_end == "Bounce" else SINGLE_PASS
