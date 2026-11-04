"""Immutable render requests and accepted pixel identity."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import math
import numpy as np
from tomography_session_browser.services.volume.memory import MemoryLease
from tomography_session_browser.services.volume.scale_bar import ScaleBarStyle, decode_style


@dataclass(frozen=True, slots=True)
class RenderRecipe:
    plane: int  # Zero-based normal-axis voxel index; UI readouts are 1-based.
    region: tuple[float, float, float, float]  # Pane voxel edges, after explicit orientation.
    output_size: tuple[int, int]  # Logical display pixels; spatial zoom uses these.
    black: float | None = None
    white: float | None = None
    spacing_override: float | None = None  # Explicit confirmed isotropic Å/voxel.
    show_bar: bool = True
    smooth: bool = True
    axis: str = "XY"
    slices: int = 1
    lowpass: float = 0.0  # Gaussian sigma in cycles per pixel; zero disables.
    highpass: float = 0.0  # Gaussian radius in Fourier pixels; zero disables.
    invert: bool = False
    flip_vertical: bool = False  # UI uses IMOD-style Y/Z upward; explicit in captures.
    native_sampling: bool = False  # Only native voxel centres within the visible region.
    levels_mode: str = "Auto"
    brightness: float = 0.0
    contrast: float = 1.0
    levels_anchor: tuple[str, int] | None = None
    rotation: tuple[float, float, float] = (0., 0., 0.)
    pivot_xyz: tuple[float, float, float] | None = None
    pivot_grid_xyz: tuple[float, float, float] | None = None
    interactive: bool = False
    scale_bar: ScaleBarStyle = ScaleBarStyle()
    pixel_ratio: float = 1.0  # Raster pixels per logical pixel; frozen in captures.

    @property
    def raster_size(self) -> tuple[int, int]:
        width, height = self.output_size
        return max(1, round(width * self.pixel_ratio)), max(1, round(height * self.pixel_ratio))


@dataclass(frozen=True, slots=True)
class Histogram:
    counts: tuple[int, ...]
    minimum: float
    maximum: float
    mean: float
    std: float


@dataclass(frozen=True, slots=True)
class RenderResult:
    pixels: np.ndarray
    recipe: RenderRecipe
    calibration: str
    bar_label: str = ""
    companions: tuple[tuple[str, RenderResult, tuple[int, int]], ...] = ()
    histogram: Histogram | None = None
    buffer_lease: MemoryLease | None = field(default=None, repr=False, compare=False)
    navigator: np.ndarray | None = field(default=None, repr=False, compare=False)
    bar_bounds: tuple[int, int, int, int] | None = None
    bar_notice: str = ""


def decode_recipe(data: dict) -> RenderRecipe:
    """Read only a supported, finite scalar capture recipe from app storage."""
    if not isinstance(data, dict):
        raise ValueError("Capture recipe must be an object")
    recipe = RenderRecipe(**{**data, "scale_bar": decode_style(data.get("scale_bar", {}))})
    if type(recipe.pixel_ratio) not in (int, float) or not math.isfinite(recipe.pixel_ratio) or not 0 < recipe.pixel_ratio <= 8:
        raise ValueError("Invalid captured display pixel ratio")
    from tomography_session_browser.services.volume.oblique import coordinates
    rotation = coordinates(recipe.rotation)
    pivot = coordinates(recipe.pivot_xyz, optional=True)
    grid_pivot = coordinates(recipe.pivot_grid_xyz, optional=True)
    if (pivot is None) != (grid_pivot is None) or type(recipe.interactive) is not bool:
        raise ValueError("Invalid captured oblique geometry")
    assert rotation is not None
    recipe = replace(recipe, rotation=rotation, pivot_xyz=pivot, pivot_grid_xyz=grid_pivot)
    if recipe.levels_mode not in ("Auto", "±3σ", "Manual") or type(recipe.brightness) not in (int, float) or not -1 <= recipe.brightness <= 1 or type(recipe.contrast) not in (int, float) or not .1 <= recipe.contrast <= 10:
        raise ValueError("Invalid captured contrast recipe")
    if recipe.axis not in ("XY", "XZ", "YZ") or type(recipe.plane) is not int or recipe.plane < 0:
        raise ValueError("Invalid captured plane")
    if not isinstance(recipe.region, (list, tuple)) or len(recipe.region) != 4 or any(type(v) not in (int, float) or not math.isfinite(v) for v in recipe.region):
        raise ValueError("Invalid captured crop")
    x0, y0, x1, y1 = recipe.region
    if not (0 <= x0 < x1 and 0 <= y0 < y1):
        raise ValueError("Invalid captured crop bounds")
    if not isinstance(recipe.output_size, (list, tuple)) or len(recipe.output_size) != 2 or any(type(v) is not int or v < 1 for v in recipe.output_size):
        raise ValueError("Invalid captured output")
    for flag in (recipe.show_bar, recipe.smooth, recipe.invert, recipe.flip_vertical, recipe.native_sampling):
        if type(flag) is not bool:
            raise ValueError("Invalid captured display flag")
    for value in (recipe.black, recipe.white, recipe.lowpass, recipe.highpass):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("Invalid captured scalar")
    if recipe.black is None or recipe.white is None or recipe.white <= recipe.black:
        raise ValueError("Invalid captured levels")
    if not 0 <= recipe.lowpass <= .5 or not 0 <= recipe.highpass <= 10000:
        raise ValueError("Invalid captured filters")
    if type(recipe.slices) is not int or not 1 <= recipe.slices <= 101 or recipe.slices % 2 != 1:
        raise ValueError("Invalid captured averaging")
    if recipe.spacing_override is not None and (type(recipe.spacing_override) not in (int, float) or not math.isfinite(recipe.spacing_override) or recipe.spacing_override <= 0):
        raise ValueError("Invalid captured calibration")
    anchor = recipe.levels_anchor
    if anchor is not None and (not isinstance(anchor, (list, tuple)) or len(anchor) != 2 or anchor[0] not in ("XY", "XZ", "YZ") or type(anchor[1]) is not int or anchor[1] < 0):
        raise ValueError("Invalid captured levels anchor")
    return replace(recipe, region=tuple(recipe.region), output_size=tuple(recipe.output_size),
                   levels_anchor=(anchor[0], anchor[1]) if anchor is not None else None)
