"""Scalar Ortho geometry. Coordinates are reconstruction voxel edges, Y/Z up."""
from dataclasses import dataclass, replace
from math import floor

from tomography_session_browser.domain.tomograms import Volume
from tomography_session_browser.presentation.tomogram_state import PaneGeometry, TomogramViewState
from tomography_session_browser.services.volume.render import calibrated_spacing, plane_geometry, plane_spacing
from tomography_session_browser.services.volume.models import RenderRecipe

AXES = {"XY": (0, 1, 2), "XZ": (0, 2, 1), "YZ": (2, 1, 0)}


@dataclass(frozen=True, slots=True)
class FocusFit:
    client: tuple[int, int]
    stage: tuple[int, int]
    dividers: tuple[int, int, int, int]
    show_list: bool


def focus_fit(volume: Volume, state: TomogramViewState, *, available: tuple[int, int],
              chrome: tuple[int, int], handle: int = 6, list_width: int = 300,
              stage_minimum: int = 640) -> FocusFit:
    """Fit the base grid in logical client bounds; never change a view recipe."""
    assert volume.grid is not None
    x, y, z = (size * step for size, step in zip(volume.grid.shape_xyz, spacing(volume, state)))
    ortho = state.layout == "Ortho"
    footprint = (x + z, y + z) if ortho else (x, y)
    wide = available[0] >= chrome[0] + list_width + stage_minimum
    reserved = list_width if wide else 0
    max_width = max(1, available[0] - chrome[0] - reserved)
    max_height = max(1, available[1] - chrome[1])
    gap = handle if ortho else 0
    scale = min(max(1, max_width-gap) / footprint[0], max(1, max_height-gap) / footprint[1])
    width = min(max_width, max(1, floor(footprint[0] * scale) + gap))
    if wide:
        width = min(max_width, max(stage_minimum, width))
    height = min(max_height, max(1, floor(footprint[1] * scale) + gap))
    left = max(1, round((width-gap) * x / (x+z)))
    top = max(1, round((height-gap) * z / (y+z)))
    return FocusFit((min(available[0], width + chrome[0] + reserved),
                     min(available[1], height + chrome[1])), (width, height),
                    (top, max(1, height-gap-top), left, max(1, width-gap-left)), wide)


def normal_axis(axis: str) -> str:
    return "XYZ"[AXES[axis][2]]


FILTERS_DEFERRED = " · filters deferred"
NO_PLANE = "XY · No plane displayed"


def plane_caption(axis: str, plane: int, count: int, *, ortho: bool, oblique: bool) -> str:
    """The plane band's readout of the displayed plane; plane and count are counted from 1."""
    if oblique:
        return f"Oblique {axis} · offset {plane} / {count}"
    return (f"{axis} · " if ortho else "") + f"{normal_axis(axis)} {plane} / {count}"


def reserved_captions(volume: Volume, recipe: RenderRecipe, *, ortho: bool) -> tuple[str, ...]:
    """The longest readouts this view can show, on any axis; the band reserves the widest.

    They follow the volume, layout, tilt and filters, never the plane, so scrubbing, playback,
    an axis change and the deferred-filter notice do not move the track.
    """
    oblique = any(recipe.rotation)
    notice = FILTERS_DEFERRED if recipe.lowpass or recipe.highpass else ""
    return tuple(plane_caption(axis, count, count, ortho=ortho, oblique=oblique) + notice
                 for axis in AXES for count in (plane_geometry(volume, axis)[2],))


@dataclass(frozen=True, slots=True)
class CursorReadout:
    display: str
    accessible: str
    tooltip: str


def source_mapped(recipe: RenderRecipe) -> bool:
    """A tilted or moved plane reads its voxel through the volume mapping."""
    return any(recipe.rotation) or recipe.pivot_xyz != recipe.pivot_grid_xyz


def reserved_cursor_readouts(volume: Volume, recipe: RenderRecipe) -> tuple[str, ...]:
    """The longest pointer readouts this view can show, on any axis; the band reserves the widest."""
    assert volume.grid is not None
    shape = volume.grid.shape_xyz
    if source_mapped(recipe):
        # A tilted plane reaches past the volume: allow a sign and twice the size.
        return ("Source  " + "  ".join(f"{axis}\u00a0-{2 * size}.00" for axis, size in zip("XYZ", shape)),)
    return tuple("  ".join(f"{'XYZ'[i]}\u00a0{shape[i]}" for i in AXES[axis][:2]) for axis in AXES)


def cursor_readout(volume: Volume, recipe: RenderRecipe, x: float, y: float) -> CursorReadout:
    """Use source mapping once for visible and complete accessible coordinates."""
    assert volume.grid is not None
    h, v, normal = AXES[recipe.axis]
    coordinates = [0, 0, 0]
    coordinates[h] = int(x) + 1
    coordinates[v] = volume.grid.shape_xyz[v] - int(y)
    coordinates[normal] = recipe.plane + 1
    if source_mapped(recipe):
        from tomography_session_browser.services.volume.oblique import mapping
        import numpy as np
        matrix, offset = mapping(volume, recipe.rotation, recipe.pivot_xyz, recipe.pivot_grid_xyz,
                                 calibrated_spacing(volume, recipe.spacing_override))
        source = matrix @ (np.asarray(coordinates)-1)+offset
        text = "Source  " + "  ".join(f"{axis}\u00a0{value+1:.2f}" for axis, value in zip("XYZ", source))
        return CursorReadout(text, text, "The voxel at the pointer, counted from 1. The app finds it in the volume from the tilted plane.")
    return CursorReadout("  ".join(f"{'XYZ'[i]}\u00a0{coordinates[i]}" for i in (h, v)),
                         "Source voxel " + ", ".join(f"{axis} {value}" for axis, value in zip("XYZ", coordinates)),
                         "The voxel at the pointer, counted from 1, as in IMOD")


def spacing(volume: Volume, state: TomogramViewState) -> tuple[float, float, float]:
    return calibrated_spacing(volume, state.spacing_override) or (1., 1., 1.)


def remember_axis(state: TomogramViewState) -> TomogramViewState:
    views = {view.axis: view for view in state.pane_views}
    views[state.axis] = PaneGeometry(state.axis, state.zoom, state.centre)
    return replace(state, pane_views=tuple(views.values()))


def select_axis(state: TomogramViewState, axis: str, volume: Volume) -> TomogramViewState:
    assert volume.grid is not None
    state = remember_axis(state)
    view = next((view for view in state.pane_views if view.axis == axis), PaneGeometry(axis))
    cross = state.cross_xyz or tuple(n // 2 for n in volume.grid.shape_xyz)
    return replace(state, axis=axis, plane=cross[AXES[axis][2]], zoom=view.zoom,
                   centre=view.centre, capture_region=None)


def linked_centre(state: TomogramViewState, volume: Volume) -> tuple[float, float, float]:
    assert volume.grid is not None
    centre = list(state.centre_xyz) if state.centre_xyz is not None else [n / 2 for n in volume.grid.shape_xyz]
    if state.centre_xyz is None and state.centre is not None:
        h, v, _ = AXES[state.axis]
        centre[h], centre[v] = state.centre[0], volume.grid.shape_xyz[v] - state.centre[1]
    if state.capture_region is not None:
        x0, y0, x1, y1 = state.capture_region
        h, v, _ = AXES[state.axis]
        centre[h], centre[v] = (x0+x1)/2, volume.grid.shape_xyz[v] - (y0+y1)/2
    return centre[0], centre[1], centre[2]


def pane_view(state: TomogramViewState, volume: Volume, axis: str,
              sizes: dict[str, tuple[int, int]]) -> PaneGeometry:
    assert volume.grid is not None
    if not state.linked:
        return PaneGeometry(axis, state.zoom, state.centre) if axis == state.axis else next(
            (view for view in state.pane_views if view.axis == axis), PaneGeometry(axis))
    calibration = spacing(volume, state)
    scale = state.linked_scale
    if state.capture_region is not None:
        x0, y0, x1, y1 = state.capture_region
        width, height = sizes[state.axis]
        sx, sy = plane_spacing(calibration, state.axis)
        scale = min(width / ((x1-x0) * sx), height / ((y1-y0) * sy))
    if scale is None and state.zoom is not None:
        scale = state.zoom / plane_spacing(calibration, state.axis)[0]
    if scale is None:
        scale = min(min(width / (plane_geometry(volume, a)[0] * plane_spacing(calibration, a)[0]),
                        height / (plane_geometry(volume, a)[1] * plane_spacing(calibration, a)[1]))
                    for a, (width, height) in sizes.items())
    h, v, _ = AXES[axis]
    centre = linked_centre(state, volume)
    return PaneGeometry(axis, scale * calibration[h], (centre[h], volume.grid.shape_xyz[v] - centre[v]))


def set_view(state: TomogramViewState, volume: Volume, zoom: float,
             centre: tuple[float, float]) -> TomogramViewState:
    assert volume.grid is not None
    h, v, _ = AXES[state.axis]
    xyz = list(linked_centre(state, volume))
    xyz[h], xyz[v] = centre[0], volume.grid.shape_xyz[v] - centre[1]
    return remember_axis(replace(state, zoom=zoom, centre=centre, capture_region=None,
        centre_xyz=tuple(xyz), linked_scale=zoom / spacing(volume, state)[h] if state.linked else state.linked_scale))
