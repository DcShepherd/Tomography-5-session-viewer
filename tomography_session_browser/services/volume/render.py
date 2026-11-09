"""Native axis-aligned sampling, accepted-plane statistics and fixed display levels."""
from __future__ import annotations

from dataclasses import replace
from collections.abc import Callable
import math
import numpy as np
from PIL import Image

from tomography_session_browser.domain.tomograms import Volume
from tomography_session_browser.services.volume.models import RenderRecipe, RenderResult, Histogram
from tomography_session_browser.services.volume.store import VolumeStore, VolumeUnreadable
from tomography_session_browser.services.volume.memory import VOLUME_MEMORY
from tomography_session_browser.services.volume.filters import filter_plane
from tomography_session_browser.services.volume.resample import resize_axis
from tomography_session_browser.services.volume.scale_bar import draw_scale_bar

#: The largest raster one render may produce, in pixels.
RENDER_PIXEL_LIMIT = 32 * 1024 * 1024


def plane_geometry(volume: Volume, axis: str) -> tuple[int, int, int]:
    if volume.grid is None or axis not in ("XY", "XZ", "YZ"):
        raise ValueError("A usable axis-aligned volume grid is required.")
    x, y, z = volume.grid.shape_xyz
    return {"XY": (x, y, z), "XZ": (x, z, y), "YZ": (z, y, x)}[axis]


def plane_spacing(spacing: tuple[float, float, float], axis: str) -> tuple[float, float]:
    x, y, z = spacing
    return {"XY": (x, y), "XZ": (x, z), "YZ": (z, y)}[axis]


def intersection_point(axis: str, cross_xyz: tuple[int, int, int], shape_xyz: tuple[int, int, int]) -> tuple[float, float]:
    """The intersection voxel's centre in a pane's display coordinates, where Y and Z run upwards.

    The viewer's crosshair and the movie's intersection lines share this transform.
    """
    horizontal, vertical = {"XY": (0, 1), "XZ": (0, 2), "YZ": (2, 1)}[axis]
    return cross_xyz[horizontal] + .5, shape_xyz[vertical] - cross_xyz[vertical] - .5


def native_region(region: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    """Integer voxel edges whose centres lie in the half-open visible crop."""
    x0, y0, x1, y1 = region
    return math.ceil(x0 - .5), math.ceil(y0 - .5), math.ceil(x1 - .5), math.ceil(y1 - .5)


def calibrated_spacing(volume: Volume, override: float | None) -> tuple[float, float, float] | None:
    if override is not None:
        if not math.isfinite(override) or override <= 0:
            raise ValueError("Confirmed voxel spacing must be finite and positive.")
        return (override, override, override)
    if volume.grid is None:
        return None
    x, y, z = volume.grid.spacing_xyz
    if any(value is None or not math.isfinite(value) or value <= 0 or abs(value - 1) < 1e-6
           for value in (x, y, z)):
        return None
    assert x is not None and y is not None and z is not None
    return (x, y, z)


def viewport_region(shape_xy: tuple[int, int], size: tuple[int, int], zoom: float | None,
                    centre: tuple[float, float] | None, aspect_y: float = 1) -> tuple[
                        tuple[float, float, float, float], tuple[int, int], tuple[int, int]]:
    nx, ny = shape_xy
    width, height = size
    if min(nx, ny, width, height) < 1:
        raise ValueError("The viewport and volume dimensions must be positive.")
    scale = zoom if zoom is not None else min(width / nx, height / (ny * aspect_y))
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("Zoom must be finite and positive.")
    cx, cy = centre or (nx / 2, ny / 2)
    left, top = cx - width / (2 * scale), cy - height / (2 * scale * aspect_y)
    right, bottom = left + width / scale, top + height / (scale * aspect_y)
    region = (max(0., left), max(0., top), min(float(nx), right), min(float(ny), bottom))
    x0, y0, x1, y1 = region
    if x1 <= x0 or y1 <= y0:
        raise ValueError("The viewport is outside the image. Use Fit to return.")
    output = (max(1, round((x1 - x0) * scale)), max(1, round((y1 - y0) * scale * aspect_y)))
    position = (round((x0 - left) * scale), round((y0 - top) * scale * aspect_y))
    return region, output, position


def render_xy(volume: Volume, recipe: RenderRecipe, *, cancelled: Callable[[], bool] = lambda: False,
              include_histogram: bool = True, share_planes: bool = True) -> RenderResult:
    if cancelled():
        raise InterruptedError("Obsolete volume render")
    if volume.grid is None:
        raise ValueError(" ".join(volume.warnings) or "Volume grid is unavailable.")
    nx, ny, nz = plane_geometry(volume, recipe.axis)
    x0, y0, x1, y1 = recipe.region
    if not math.isfinite(recipe.pixel_ratio) or not 0 < recipe.pixel_ratio <= 8:
        raise ValueError("Invalid display pixel ratio.")
    ow, oh = recipe.raster_size
    if not (0 <= recipe.plane < nz and 0 <= x0 < x1 <= nx and 0 <= y0 < y1 <= ny):
        raise ValueError("The requested plane/crop is outside this volume.")
    if min(recipe.output_size) < 1 or ow * oh > RENDER_PIXEL_LIMIT:
        raise ValueError("The output image exceeds the current rendering limit.")
    if recipe.native_sampling:
        if recipe.pixel_ratio != 1:
            raise ValueError("Native 1:1 captures require one output pixel per voxel.")
        x0, y0, x1, y1 = native_region(recipe.region)
        if (ow, oh) != (x1 - x0, y1 - y0) or min(ow, oh) < 1:
            raise ValueError("No native voxel centres in this region, or inconsistent 1:1 output. Zoom out and retry.")
    if recipe.interactive:
        from tomography_session_browser.services.volume.oblique import sample_oblique
        # Preview buffers scale with the physical display raster; no native
        # plane/filter intermediate or navigator is allocated during interaction.
        with VOLUME_MEMORY.reserve(ow * oh * 64, "render-working"):
            plane = sample_oblique(volume, recipe, calibrated_spacing(volume, recipe.spacing_override),
                                   cancelled=cancelled, display=True)
            if cancelled():
                raise InterruptedError("Obsolete volume render")
            return finish_render(volume, recipe, plane, recipe.region, include_histogram=False,
                                 display_sampled=True)
    intermediate = min(ow * ny, nx * oh)
    working_bytes = nx * ny * (np.dtype(volume.dtype).itemsize + (160 if recipe.lowpass or recipe.highpass else 64)) + intermediate + ow * oh * 9 + 144 * 144 * 4
    with VOLUME_MEMORY.reserve(working_bytes, "render-working"):
        if any(recipe.rotation) or recipe.pivot_xyz != recipe.pivot_grid_xyz:
            from tomography_session_browser.services.volume.oblique import sample_oblique
            native = sample_oblique(volume, recipe, calibrated_spacing(volume, recipe.spacing_override), cancelled=cancelled)
            plane = filter_plane(native, recipe.lowpass, recipe.highpass)
            if cancelled():
                raise InterruptedError("Obsolete volume render")
            return finish_render(volume, recipe, plane, (x0, y0, x1, y1), include_histogram=include_histogram)
        with VolumeStore().plane_lease(volume, recipe.plane, recipe.axis, recipe.slices, cancelled=cancelled,
                                       share=share_planes) as native:
            if cancelled():
                raise InterruptedError("Obsolete volume render")
            plane = filter_plane(native, recipe.lowpass, recipe.highpass)
            if cancelled():
                raise InterruptedError("Obsolete volume render")
            return finish_render(volume, recipe, plane, (x0, y0, x1, y1), include_histogram=include_histogram)


def finish_render(volume: Volume, recipe: RenderRecipe, plane: np.ndarray,
                  crop: tuple[float, float, float, float], *, include_histogram: bool = True,
                  display_sampled: bool = False) -> RenderResult:
    ny, nx = plane.shape
    ow, oh = recipe.raster_size
    x0, y0, x1, y1 = crop
    if recipe.flip_vertical and not display_sampled:
        plane = plane[::-1]
    finite = np.isfinite(plane)
    if not finite.any():
        raise VolumeUnreadable("This plane contains no finite image values.")
    black, white = recipe.black, recipe.white
    anchor = recipe.levels_anchor
    histogram = None
    if include_histogram or black is None or white is None:
        samples = plane[finite]
        # Exact float16 promotion avoids slow/rounded half-precision statistics;
        # percentiles retain the original samples and established level policy.
        statistics = samples.astype(np.float32) if samples.dtype.kind == "f" and samples.dtype.itemsize < 4 else samples
        mean, std = float(np.mean(statistics, dtype=np.float64)), float(np.std(statistics, dtype=np.float64))
        if include_histogram:
            counts, edges = np.histogram(statistics, bins=128)
            histogram = Histogram(tuple(int(n) for n in counts), float(edges[0]), float(edges[-1]), mean, std)
        if black is None or white is None:
            anchor = (recipe.axis, recipe.plane)
            if recipe.levels_mode == "±3σ":
                black, white = mean - 3 * std, mean + 3 * std
            else:
                black, white = (float(value) for value in np.percentile(samples, (0.5, 99.5)))
            if white <= black:
                white = black + max(abs(black) * 1e-6, 1.)
        del samples, statistics
    if not math.isfinite(black) or not math.isfinite(white) or white <= black:
        raise ValueError("White level must be finite and greater than black level.")
    if recipe.levels_mode not in ("Auto", "±3σ", "Manual") or not -1 <= recipe.brightness <= 1 or not .1 <= recipe.contrast <= 10:
        raise ValueError("Invalid contrast mode or adjustment.")
    values = np.asarray(plane, dtype=np.float32)
    pixels = (np.clip((values - black) * (255 / (white - black)), 0, 255)
              if recipe.contrast == 1 and recipe.brightness == 0 else
              np.clip(((values - black) / (white - black) - .5) * (255 * recipe.contrast) + 255 * (.5 + recipe.brightness), 0, 255))
    pixels[~finite] = 0
    if recipe.invert:
        pixels[finite] = 255 - pixels[finite]
    image = Image.fromarray(pixels.astype(np.uint8))
    spacing = calibrated_spacing(volume, recipe.spacing_override)
    sx, sy = plane_spacing(spacing, recipe.axis) if spacing else (1., 1.)
    # Derive the navigator from this exact full sampled/filtered slice, with
    # the accepted levels, orientation and physical aspect; never a pipeline PNG.
    nav = None
    if not display_sampled:
        nav_scale = min(144 / nx, 144 / (ny * sy / sx))
        nav = np.array(image.resize((max(1, round(nx * nav_scale)), max(1, round(ny * sy / sx * nav_scale))),
                                    resample=Image.Resampling.BOX), copy=True)
        nav.setflags(write=False)
    # Separate axes: anisotropic calibration may shrink X while enlarging Y.
    # Choose the smaller intermediate, without changing voxel-to-output mapping.
    if display_sampled:
        pass  # The sampled raster already has its final crop/orientation/size.
    elif ow * ny <= nx * oh:
        image = resize_axis(image, ow, x0, x1, 1, recipe.smooth)
        image = resize_axis(image, oh, y0, y1, 0, recipe.smooth)
    else:
        image = resize_axis(image, oh, y0, y1, 0, recipe.smooth)
        image = resize_axis(image, ow, x0, x1, 1, recipe.smooth)
    label = ""
    bar_bounds, bar_notice = None, ""
    if recipe.show_bar and spacing is not None:
        angstrom_per_pixel = plane_spacing(spacing, recipe.axis)[0] * (x1 - x0) / ow
        image, label, bar_bounds, bar_notice = draw_scale_bar(image, angstrom_per_pixel, recipe.scale_bar, pixel_ratio=recipe.pixel_ratio)
    result = np.array(image, copy=True)
    result.setflags(write=False)
    return RenderResult(result, replace(recipe, black=black, white=white, levels_anchor=anchor),
                        "User confirmed" if recipe.spacing_override else ("MRC header" if spacing else "Unresolved"), label,
                        histogram=histogram, buffer_lease=VOLUME_MEMORY.reserve((result.nbytes + (nav.nbytes if nav is not None else 0)) * 2 + 8192, "display-buffers"),
                        navigator=nav, bar_bounds=bar_bounds, bar_notice=bar_notice)
