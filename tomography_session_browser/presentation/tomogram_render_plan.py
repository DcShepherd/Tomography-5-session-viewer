"""Pure requested-plane construction shared by display and capture intent."""
from dataclasses import dataclass, replace
from pathlib import Path

from tomography_session_browser.domain.tomograms import Volume, VolumeFingerprint
from tomography_session_browser.presentation.tomogram_geometry import AXES, pane_view
from tomography_session_browser.presentation.tomogram_state import TomogramViewState
from tomography_session_browser.services.volume.models import RenderRecipe
from tomography_session_browser.services.volume.render import calibrated_spacing, plane_geometry, plane_spacing, viewport_region


@dataclass(frozen=True, slots=True)
class PaneViewport:
    axis: str
    size: tuple[int, int]
    pixel_ratio: float


@dataclass(frozen=True, slots=True)
class RequestedPlane:
    recipe: RenderRecipe
    position: tuple[int, int]


def render_plan(state: TomogramViewState, volume: Volume, panes: tuple[PaneViewport, ...],
                *, interactive: bool = False) -> tuple[RequestedPlane, ...]:
    if volume.grid is None:
        raise ValueError("A usable volume grid is required.")
    spacing = calibrated_spacing(volume, state.spacing_override)
    sizes = {pane.axis: pane.size for pane in panes}
    cross = state.cross_xyz or tuple(n // 2 for n in volume.grid.shape_xyz)
    requests = []
    for pane in panes:
        axis, (width, height) = pane.axis, pane.size
        nx, ny, nz = plane_geometry(volume, axis)
        sx, sy = plane_spacing(spacing, axis) if spacing else (1., 1.)
        active = axis == state.axis
        view = pane_view(state, volume, axis, sizes)
        region, size, position = viewport_region((nx, ny), pane.size, view.zoom, view.centre, sy / sx)
        if active and state.capture_region is not None:
            region = state.capture_region
            x0, y0, x1, y1 = region
            scale = min(width / (x1-x0), height / ((y1-y0) * sy / sx))
            size = max(1, round((x1-x0)*scale)), max(1, round((y1-y0)*scale*sy/sx))
            position = (width-size[0]) // 2, (height-size[1]) // 2
        plane = state.plane if active else cross[AXES[axis][2]]
        recipe = RenderRecipe(plane if plane is not None else nz // 2, region, size,
            state.black, state.white, state.spacing_override,
            state.show_bar and (active or not state.linked), state.smooth, axis, state.slices,
            state.lowpass, state.highpass, state.invert, True,
            levels_mode=state.levels_mode, brightness=state.brightness, contrast=state.contrast,
            levels_anchor=state.levels_anchor, rotation=state.rotation,
            pivot_xyz=state.pivot_xyz, pivot_grid_xyz=state.pivot_grid_xyz,
            interactive=interactive, scale_bar=state.scale_bar, pixel_ratio=pane.pixel_ratio)
        requests.append(RequestedPlane(recipe, position))
    return tuple(requests)


@dataclass(frozen=True, slots=True)
class CaptureIntent:
    row_key: str
    volume_id: str
    fingerprint: VolumeFingerprint
    recipe: RenderRecipe
    levels_revision: int
    scope_generation: int
    state: TomogramViewState
    directory: Path | None
    format: str
    native_sampling: bool

    def matches(self, row_key: str, volume: Volume, requested: RenderRecipe,
                levels_revision: int, scope_generation: int) -> bool:
        expected = self.recipe
        # Only an unresolved automatic calculation may fill its own levels.
        # Deliberate relevel has a separate revision, even if its values coincide.
        if expected.levels_mode != "Manual" and (expected.black is None or expected.white is None):
            expected = replace(expected, black=requested.black, white=requested.white,
                               levels_anchor=requested.levels_anchor)
        return ((row_key, volume.id, volume.fingerprint, levels_revision, scope_generation)
                == (self.row_key, self.volume_id, self.fingerprint, self.levels_revision, self.scope_generation)
                and expected == replace(requested, interactive=False))
