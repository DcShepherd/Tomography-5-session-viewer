"""Scalar-only volume browsing state, distinct from raster marker/frame state."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from tomography_session_browser.services.volume.scale_bar import ScaleBarStyle


class ReviewSegment(StrEnum):
    ALL = "all"
    NOT_OPENED = "not_opened"
    ATTENTION = "attention"


@dataclass(frozen=True, slots=True)
class PaneGeometry:
    axis: str
    zoom: float | None = None
    centre: tuple[float, float] | None = None


@dataclass(frozen=True, slots=True)
class TomogramViewState:
    row_key: str | None = None
    volume_id: str | None = None
    plane: int | None = None
    zoom: float | None = None
    centre: tuple[float, float] | None = None
    black: float | None = None
    white: float | None = None
    spacing_override: float | None = None
    query: str = ""
    segment: ReviewSegment = ReviewSegment.ALL
    volume_signature: tuple[int, int] | None = None
    layout: str = "Single"
    axis: str = "XY"
    cross_xyz: tuple[int, int, int] | None = None
    slices: int = 1
    lowpass: float = 0.0
    highpass: float = 0.0
    invert: bool = False
    show_bar: bool = True
    smooth: bool = True
    capture_region: tuple[float, float, float, float] | None = None
    linked: bool = True
    centre_xyz: tuple[float, float, float] | None = None
    linked_scale: float | None = None  # Logical px/Å when calibrated, otherwise px/voxel.
    pane_views: tuple[PaneGeometry, ...] = ()
    dividers: tuple[int, int, int, int] | None = None
    ortho_lines: bool = True
    levels_mode: str = "Auto"
    brightness: float = 0.0
    contrast: float = 1.0
    levels_anchor: tuple[str, int] | None = None
    rotation: tuple[float, float, float] = (0., 0., 0.)
    pivot_xyz: tuple[float, float, float] | None = None
    pivot_grid_xyz: tuple[float, float, float] | None = None
    pivot_locked: bool = False
    orientation_visible: bool = True
    rotation_axis: int = 2
    scale_bar: ScaleBarStyle = ScaleBarStyle()
    sort: str = "Name"

    @property
    def is_empty(self) -> bool:
        return self.row_key is None and not self.query and self.segment == ReviewSegment.ALL and self.sort == "Name"
