"""Central-slice rules for PDF reports (D4 §5.5): pure, Qt-free, no file access.

Each report member gets a ``SliceRequest`` or a ``SliceSkip`` with its print
reason. The request holds a viewer state; ``slice_recipe`` builds the render
recipe through the viewer's own ``render_plan``, so the printed image keeps the
viewer's vertical flip, physical aspect and scale bar.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from tomography_session_browser.domain.tomograms import Tomogram, Volume
from tomography_session_browser.presentation.tomogram_render_plan import PaneViewport, render_plan
from tomography_session_browser.presentation.tomogram_selection import REVIEW_VERSION_ORDER
from tomography_session_browser.presentation.tomogram_state import TomogramViewState
from tomography_session_browser.presentation.tomogram_versions import review_versions
from tomography_session_browser.services.volume.models import RenderRecipe
from tomography_session_browser.services.volume.render import calibrated_spacing, plane_geometry

#: The slab the report averages, in ångström (about 10 nm).
SLAB_TARGET_A = 100.0
#: Planes requested when the spacing is not known.
FALLBACK_PLANES = 5
MAX_PLANES = 101
#: The largest raster side and its pixel ratio. At 1.4 the scale-bar label
#: prints at 6 pt or larger on a 120 pt column.
TILE_RASTER = 512
PIXEL_RATIO = 1.4
TILE_LOGICAL = round(TILE_RASTER / PIXEL_RATIO)
#: The version the report shows: the most processed first.
SLICE_VERSION_ORDER = tuple(reversed(REVIEW_VERSION_ORDER))

SECTION_TEXT = ("Each image shows the middle XY plane. The report uses the first version in this sequence: "
                + ", ".join(SLICE_VERSION_ORDER)
                + ". Each image is the average of a slab that is approximately 10 nm thick.")

SpacingSource = Literal['header', 'stated', 'none']

SKIP_CLOUD = "The file is only in the cloud."
SKIP_CHANGED = "The file changed after the app read it."
SKIP_UNREADABLE = "The app cannot read the file."
SKIP_NO_VERSION = "The tomogram has no volume to show."


@dataclass(frozen=True, slots=True)
class SliceRequest:
    tomogram: Tomogram
    volume: Volume
    state: TomogramViewState
    planes: int
    spacing_source: SpacingSource
    #: Z spacing in ångström when calibrated.
    z_spacing: float | None
    #: True when the XY plane is higher than it is wide (physical aspect).
    portrait: bool

    @property
    def plane(self) -> int:
        assert self.state.plane is not None
        return self.state.plane

    @property
    def caption(self) -> str:
        return slab_caption(self.planes, self.z_spacing)


@dataclass(frozen=True, slots=True)
class SliceSkip:
    tomogram: Tomogram
    version: str
    reason: str
    portrait: bool = False


def slice_version(tomogram: Tomogram) -> Volume | None:
    """Denoised, else Deconvolved, else Raw; else the first reviewable version."""
    versions = review_versions(tomogram)
    for name in SLICE_VERSION_ORDER:
        match = next((volume for volume in versions if volume.version == name), None)
        if match is not None:
            return match
    return versions[0] if versions else None


def largest_odd(limit: int) -> int:
    return max(1, limit if limit % 2 else limit - 1)


def slab_planes(z_spacing: float | None, depth: int) -> int:
    """The odd plane count nearest to 10 nm (the smaller on a tie), clamped to the depth."""
    cap = min(MAX_PLANES, largest_odd(depth))
    if z_spacing is None:
        return min(FALLBACK_PLANES, cap)
    target = SLAB_TARGET_A / z_spacing
    if target <= 1:
        return 1
    lower = int((target - 1) // 2) * 2 + 1
    count = lower if target - lower <= lower + 2 - target else lower + 2
    return min(count, cap)


def slab_caption(planes: int, z_spacing: float | None) -> str:
    count = f"{planes} plane{'s' if planes != 1 else ''}"
    if z_spacing is None:
        return f"{count} · no calibration"
    return f"{count} · {planes * z_spacing / 10:.1f} nm"


def plan_slice(tomogram: Tomogram, stated_for: Callable[[Volume], float | None]) -> SliceRequest | SliceSkip:
    volume = slice_version(tomogram)
    if volume is None:
        return SliceSkip(tomogram, "", SKIP_NO_VERSION)
    if volume.grid is None or min(volume.grid.shape_zyx) < 1:
        return SliceSkip(tomogram, volume.version, SKIP_UNREADABLE)
    stated = stated_for(volume)
    try:
        spacing = calibrated_spacing(volume, stated)
    except ValueError:
        return SliceSkip(tomogram, volume.version, SKIP_UNREADABLE)
    nx, ny, nz = plane_geometry(volume, "XY")
    sx, sy, sz = spacing if spacing else (1., 1., None)
    portrait = ny * sy > nx * sx
    planes = slab_planes(sz, nz)
    state = TomogramViewState(row_key=tomogram.id, volume_id=volume.id, plane=nz // 2, slices=planes,
                              spacing_override=stated, levels_mode="Auto", show_bar=True)
    source: SpacingSource = 'stated' if stated is not None else ('header' if spacing else 'none')
    return SliceRequest(tomogram, volume, state, planes, source, sz, portrait)


def slice_recipe(request: SliceRequest) -> RenderRecipe:
    """The viewer's own recipe for the request: Single XY, full extent, Auto levels."""
    pane = PaneViewport("XY", (TILE_LOGICAL, TILE_LOGICAL), PIXEL_RATIO)
    recipe = render_plan(request.state, request.volume, (pane,))[0].recipe
    nz = plane_geometry(request.volume, "XY")[2]
    half = recipe.slices // 2
    # The reader clips a range at the volume's edges; the rules keep the
    # request inside, so the sampled count equals the printed count.
    assert 0 <= recipe.plane - half and recipe.plane + half < nz, "slab outside the volume"
    return recipe


def slice_columns(plans: Sequence[SliceRequest | SliceSkip]) -> int:
    """3 columns when any volume is higher than it is wide, else 4."""
    return 3 if any(plan.portrait for plan in plans) else 4
