"""Physical-space reslicing on a fixed reference grid, with bounded file mappings.

Coordinates denote zero-based voxel centres. Euler angles are degrees and act
as Rz @ Ry @ Rx in physical XYZ. Paired pivots allow changing the rotation
centre without translating the already displayed image.
"""
from __future__ import annotations

from collections.abc import Callable
import itertools
import math
import numpy as np

from tomography_session_browser.domain.tomograms import Volume
from tomography_session_browser.parsers.volume_header import fingerprint, is_cloud_placeholder
from tomography_session_browser.services.volume.memory import VOLUME_MEMORY
from tomography_session_browser.services.volume.models import RenderRecipe
from tomography_session_browser.services.volume.store import VolumeStore

AXES = {"XY": (0, 1, 2), "XZ": (0, 2, 1), "YZ": (2, 1, 0)}


def coordinates(value, *, optional: bool = False) -> tuple[float, float, float] | None:
    if value is None and optional:
        return None
    if not isinstance(value, (tuple, list)) or len(value) != 3 or any(
            type(v) not in (int, float) or not math.isfinite(v) for v in value):
        raise ValueError("Rotation and pivot coordinates must contain three finite numbers.")
    return float(value[0]), float(value[1]), float(value[2])


def rotation_matrix(angles: tuple[float, float, float]) -> np.ndarray:
    coordinates(angles)
    x, y, z = np.radians(angles)
    cx, cy, cz = np.cos((x, y, z))
    sx, sy, sz = np.sin((x, y, z))
    return np.array(((cz*cy, cz*sy*sx-sz*cx, cz*sy*cx+sz*sx),
                     (sz*cy, sz*sy*sx+cz*cx, sz*sy*cx-cz*sx),
                     (-sy, cy*sx, cy*cx)))


def mapping(volume: Volume, angles, pivot, grid_pivot, spacing) -> tuple[np.ndarray, np.ndarray]:
    assert volume.grid is not None
    centre = (np.asarray(volume.grid.shape_xyz, dtype=float)-1)/2
    source = centre if pivot is None else np.asarray(coordinates(pivot))
    reference = centre if grid_pivot is None else np.asarray(coordinates(grid_pivot))
    scale = np.asarray(spacing or (1., 1., 1.))
    matrix = rotation_matrix(angles) * scale[None, :] / scale[:, None]
    return matrix, source - matrix @ reference


def view_rotation(angles, axis: int, degrees: float) -> tuple[float, float, float]:
    """Compose about a current view axis, then recover the declared Euler basis."""
    delta = [0., 0., 0.]
    delta[axis] = degrees
    matrix = rotation_matrix(angles) @ rotation_matrix((delta[0], delta[1], delta[2]))
    y = math.asin(float(np.clip(-matrix[2, 0], -1, 1)))
    if abs(math.cos(y)) > 1e-8:
        x, z = math.atan2(matrix[2, 1], matrix[2, 2]), math.atan2(matrix[1, 0], matrix[0, 0])
    else:
        x, z = math.atan2(-matrix[1, 2], matrix[1, 1]), 0.
    return math.degrees(x), math.degrees(y), math.degrees(z)


def sample_oblique(volume: Volume, recipe: RenderRecipe, spacing, *, cancelled: Callable[[], bool],
                   display: bool = False) -> np.ndarray:
    """Trilinear final/stepped planes; approximate rotation drags cap at three slices.

    Outside-source samples are NaN and excluded from slab means/statistics.
    A budgeted mapping avoids repeated opens when the source fits spare memory;
    otherwise sections stream. An available compiled kernel and bounded slab
    reuse serve the full-quality path. Tiles bound interpolation buffers independently
    of depth. The caller reserves the output/filter plane.
    """
    grid = volume.grid
    assert grid is not None
    if is_cloud_placeholder(volume.path) or fingerprint(volume.path) != volume.fingerprint:
        raise ValueError("The volume is unavailable or changed. Reload the processing folder.")
    h, v, n = AXES[recipe.axis]
    dims = grid.shape_xyz
    matrix, offset = mapping(volume, recipe.rotation, recipe.pivot_xyz, recipe.pivot_grid_xyz, spacing)
    width, height_out = recipe.raster_size if display else (dims[h], dims[v])
    output = np.full((height_out, width), np.nan, dtype=np.float32)
    count = min(recipe.slices, 3) if recipe.interactive else recipe.slices
    planes = range(max(0, recipe.plane-count//2), min(dims[n], recipe.plane+count//2+1))
    if not recipe.interactive and not display:
        from tomography_session_browser.services.volume.native_reslice import sample_native
        if sample_native(volume, matrix, offset, (h, v, n), planes, output, VOLUME_MEMORY, cancelled=cancelled):
            return output
    storage = tuple(axis-1 for axis in grid.axes_crs)
    limits = np.asarray(dims)[:, None]-1
    section_bytes = grid.shape_zyx[1]*grid.shape_zyx[2]*np.dtype(volume.dtype).itemsize+8192
    store = VolumeStore()
    with VOLUME_MEMORY.reserve(section_bytes + width*32*1536, "oblique-read"), store.read_session(volume) as reader:
        column_centres = np.arange(width, dtype=float)
        x0, y0, x1, y1 = recipe.region
        if display:
            column_centres = x0 + (column_centres + .5) * ((x1-x0)/width) - .5
        for top in range(0, height_out, 32):
            if cancelled():
                raise InterruptedError("Obsolete oblique render")
            height = min(32, height_out-top)
            size = height * width
            row_centres = np.arange(top, top+height, dtype=float)
            if display:
                # Raster pixel centres -> reference voxel centres. Crop and flip
                # occur here exactly once; the affine remains in physical XYZ.
                row_centres = y0 + (row_centres + .5) * ((y1-y0)/height_out) - .5
                if recipe.flip_vertical:
                    row_centres = dims[v] - 1 - row_centres
            # Broadcast the two axes straight into the coordinate tile. No
            # full meshgrid copies are needed, and the affine is unchanged.
            q = np.empty((3, height, width), dtype=float)
            q[h], q[v] = column_centres, row_centres[:, None]
            q = q.reshape(3, size)
            total, hits = np.zeros(size), np.zeros(size, dtype=np.int32)
            for plane in planes:
                q[n] = plane
                p = matrix @ q + offset[:, None]
                if display:
                    # Nearest display sampling needs neither interpolation
                    # corners nor weights. Keep this path independent of depth.
                    nearest = np.rint(p).astype(np.int64)
                    valid = np.all((nearest >= 0) & (nearest <= limits), axis=0)
                    if reader.native is not None:
                        # Gather one tile directly. Clipping only makes the
                        # read safe; the original validity mask excludes all
                        # outside-source samples from the slab mean.
                        np.clip(nearest, 0, limits, out=nearest)
                        samples = np.asarray(reader.native[nearest[2], nearest[1], nearest[0]], dtype=np.float32)
                        finite = valid & np.isfinite(samples)
                        np.add(total, samples, out=total, where=finite)
                        hits += finite
                    else:
                        target = np.flatnonzero(valid)
                        index = nearest[:, valid]
                        column, row, section = index[list(storage)]
                        order = np.argsort(section, kind="stable")
                        boundaries = np.r_[0, np.flatnonzero(np.diff(section[order]))+1, len(order)]
                        for start, stop in zip(boundaries[:-1], boundaries[1:]):
                            if start == stop:
                                continue
                            if cancelled():
                                raise InterruptedError("Obsolete oblique render")
                            selected = order[start:stop]
                            with reader.section(int(section[selected[0]])) as data:
                                samples = np.asarray(data[row[selected], column[selected]], dtype=np.float32)
                            finite = np.isfinite(samples)
                            dest = target[selected[finite]]
                            total[dest] += samples[finite]
                            hits[dest] += 1
                    continue
                # Roundoff at exact right-angle boundaries is not missing data.
                valid = np.all((p >= -1e-8) & (p <= limits+1e-8), axis=0)
                p = np.clip(p, 0, limits)
                base = np.floor(p).astype(np.int64)
                fraction = p-base
                axis_indices = (base, np.minimum(base+1, limits))
                axis_weights = (1-fraction, fraction)
                values = np.zeros(size)
                finite = valid.copy()
                corners = ((0, 0, 0),) if recipe.interactive else itertools.product((0, 1), repeat=3)
                reads = []
                for corner in corners:
                    if cancelled():
                        raise InterruptedError("Obsolete oblique render")
                    index = (np.rint(p).astype(np.int64) if recipe.interactive else
                             tuple(axis_indices[side][axis] for axis, side in enumerate(corner)))
                    weight = (np.ones(size) if recipe.interactive else
                              axis_weights[corner[0]][0] * axis_weights[corner[1]][1] * axis_weights[corner[2]][2])
                    selected = valid & (weight > 1e-15)
                    if reader.native is not None:
                        samples = np.asarray(reader.native[index[2], index[1], index[0]], dtype=float)
                        usable = selected & np.isfinite(samples)
                        # Only a contributing non-finite corner invalidates a
                        # sample; a zero-weight corner must have no effect.
                        finite &= ~selected | usable
                        np.multiply(samples, weight, out=samples, where=usable)
                        np.add(values, samples, out=values, where=usable)
                    else:
                        column, row, section = (index[axis] for axis in storage)
                        reads.append((column[selected], row[selected], section[selected], np.flatnonzero(selected), weight[selected]))
                if reader.native is not None:
                    total[finite] += values[finite]
                    hits[finite] += 1
                    continue
                columns, rows, sections, targets, weights = (np.concatenate([read[i] for read in reads]) for i in range(5))
                # Group all eight interpolation corners so a section is mapped
                # only once per tile/slab plane, rather than once per corner.
                order = np.argsort(sections, kind="stable")
                boundaries = np.r_[0, np.flatnonzero(np.diff(sections[order]))+1, len(order)]
                for start, stop in zip(boundaries[:-1], boundaries[1:]):
                    if start == stop:
                        continue
                    if cancelled():
                        raise InterruptedError("Obsolete oblique render")
                    selected = order[start:stop]
                    target = targets[selected]
                    with store.section(volume, int(sections[selected[0]])) as data:
                        samples = np.asarray(data[rows[selected], columns[selected]], dtype=float)
                    finite[target[~np.isfinite(samples)]] = False
                    np.add.at(values, target, np.nan_to_num(samples)*weights[selected])
                total[finite] += values[finite]
                hits[finite] += 1
            np.divide(total, hits, out=total, where=hits > 0)
            total[hits == 0] = np.nan
            output[top:top+height] = total.reshape(height, width)
    if fingerprint(volume.path) != volume.fingerprint:
        raise ValueError("The volume changed while reading. Reload the processing folder.")
    return output
