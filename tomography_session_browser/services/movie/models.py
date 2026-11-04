"""Immutable movie contracts that a worker receives and returns (plan §3.1)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from tomography_session_browser.domain.tomograms import Volume
from tomography_session_browser.services.movie.sizing import FrameGeometry, PaneRaster
from tomography_session_browser.services.movie.timeline import MovieTimeline
from tomography_session_browser.services.volume.models import RenderRecipe
from tomography_session_browser.services.volume.scale_bar import ScaleBarStyle

MOVIE, MOVIE_AND_FRAMES, FRAMES_ONLY = "Movie", "Movie and PNG frames", "PNG frames only"
SAVES = (MOVIE, MOVIE_AND_FRAMES, FRAMES_ONLY)
#: Compression choices and their x264 CRF, with the slow preset (plan §3.3).
COMPRESSION = {"Balanced quality": 20, "Higher quality": 18, "Smaller file": 24}
COMPRESSION_HINTS = {"Balanced quality": "For talks and web pages.",
                     "Higher quality": "Larger file. Use it when fine detail matters.",
                     "Smaller file": "For email. Fine detail can look blocky."}


@dataclass(frozen=True, slots=True)
class MoviePane:
    """A frozen pane: its export recipe (raster output, no baked bar), placement and annotations."""
    recipe: RenderRecipe
    raster: PaneRaster
    scale_bar: bool = False


@dataclass(frozen=True, slots=True)
class MovieLabels:
    names: bool = True
    numbers: bool = True
    lines: bool = False
    title: str = ""


@dataclass(frozen=True, slots=True)
class MovieOutput:
    save: str
    compression: str
    directory: Path
    name: str
    protected_roots: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class MovieRequest:
    job: int
    tomogram: str
    volume: Volume
    layout: str
    sweep: str
    panes: tuple[MoviePane, ...]
    geometry: FrameGeometry
    timeline: MovieTimeline
    labels: MovieLabels
    output: MovieOutput
    scale_bar: ScaleBarStyle = field(default_factory=ScaleBarStyle)
    cross_xyz: tuple[int, int, int] | None = None
    oblique: bool = False

    @property
    def sweep_pane(self) -> MoviePane:
        return next(pane for pane in self.panes if pane.recipe.axis == self.sweep)

    @property
    def encodes(self) -> bool:
        return self.output.save != FRAMES_ONLY

    @property
    def writes_frames(self) -> bool:
        return self.output.save != MOVIE


@dataclass(frozen=True, slots=True)
class MovieProgress:
    job: int
    phase: str
    done: int
    total: int


@dataclass(frozen=True, slots=True)
class MovieResult:
    job: int
    path: Path
    movie: Path | None
    frame_size: tuple[int, int]
    frames: int
    duration: float
    size_bytes: int
    png_frames: int = 0
    checks: tuple[str, ...] = ()


class MovieFailure(Exception):
    """A classified failure: one reason, one line of help, the frame where it stopped.

    Kinds: source, planes, memory, storage, encoder, destination, cancelled.
    """

    def __init__(self, kind: str, message: str, help: str = "", *, frame: int | None = None, detail: str = "") -> None:
        super().__init__(message)
        self.kind, self.message, self.help, self.frame, self.detail = kind, message, help, frame, detail
