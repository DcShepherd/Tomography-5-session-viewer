"""Display reduction with exact fractional pixel coverage at crop boundaries."""
from __future__ import annotations

import numpy as np
from PIL import Image


def resize_axis(image: Image.Image, size: int, start: float, stop: float,
                axis: int, smooth: bool) -> Image.Image:
    """Reduce by area; retain the existing bilinear/nearest enlargement policy.

    Pixel values are constant over each unit voxel footprint. Integral samples
    at output edges retain fractional coverage, unlike whole-pixel box bins.
    Prefix/interpolation scratch covers at most 32 rows or columns at a time.
    """
    width, height = image.size
    output = (size, height) if axis == 1 else (width, size)
    box = (start, 0, stop, height) if axis == 1 else (0, start, width, stop)
    if size >= stop-start:
        return image.resize(output, resample=Image.Resampling.BILINEAR if smooth else Image.Resampling.NEAREST, box=box)
    values = np.asarray(image)
    # The input is uint8 display data: integer prefixes are exact and cheaper
    # than floating accumulation. Widen for exceptionally long display rows.
    length = values.shape[axis]
    accumulator = np.uint32 if length <= np.iinfo(np.uint32).max // 255 else np.uint64
    edges = np.linspace(start, stop, size+1)
    indices = np.floor(edges).astype(np.intp)
    fractions = edges-indices
    edge_pixels = np.minimum(indices, length-1)
    pixels = np.empty(output[::-1], dtype=np.uint8)
    # Keep the exact prefix/edge arithmetic and uint8 rounding, but avoid
    # full-plane float64 gather/interpolation temporaries on every step.
    for first in range(0, values.shape[1-axis], 32):
        if axis == 0:
            # Keep columns in their storage orientation instead of transposing
            # the input blocks and copying a transposed full output into PIL.
            chunk = np.ascontiguousarray(values[:, first:first+32])
            prefix = np.empty((chunk.shape[0]+1, chunk.shape[1]), dtype=accumulator)
            prefix[0, :] = 0
            np.cumsum(chunk, axis=0, dtype=accumulator, out=prefix[1:, :])
            edge_values = prefix[indices, :] + chunk[edge_pixels, :] * fractions[:, None]
        else:
            chunk = values[first:first+32]
            prefix = np.empty((chunk.shape[0], chunk.shape[1]+1), dtype=accumulator)
            prefix[:, 0] = 0
            np.cumsum(chunk, axis=1, dtype=accumulator, out=prefix[:, 1:])
            edge_values = prefix[:, indices] + chunk[:, edge_pixels] * fractions
        reduced = np.diff(edge_values, axis=axis) / ((stop-start)/size)
        block = np.clip(np.floor(reduced+.5), 0, 255).astype(np.uint8)
        if axis == 0:
            pixels[:, first:first+32] = block
        else:
            pixels[first:first+32] = block
    return Image.fromarray(pixels)
