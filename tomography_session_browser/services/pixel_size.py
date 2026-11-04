"""Metres (or ångströms) per pixel: three policies, side by side.

The app reads an image's pixel size in three places, and each reads it its
own way. They are kept as they are, named and pinned in
``tests/test_divergence_register.py``; unifying them is a scientific
decision, and ``tools/snapshot_models.py`` lists every real image where the
scale bar and the overlays disagree.

Overlays (``overlay_pixel_size``), metres per pixel, first that is positive:

1. Atlas only: ``AtlasMosaicPixelSize.x`` (the rendered mosaic, not a tile).
2. The first ``pixelSize`` in the metadata, its ``x`` only.
3. The MRC header (``overlay_mrc_pixel_size``): the first frame's raw FEI
   ``pixel_size_x``/``_y`` (metres), then its parsed ``pixel_size`` (Å), then
   ``voxel_size[0]`` (Å).

Scale bar (``scale_bar_pixel_size``), metres per pixel, by entity kind:

- Tilt series: ``TiltSeries.pixel_size`` (Å), else the MRC header.
- Batch position: the MRC header of the image on screen, else ``pixelSize``.
- Search tile: ``Tile.xml`` ``pixelSize``, else the MRC header.
- Search map: ``SearchMap.xml`` ``pixelSize``, then any ``pixelSize``, then
  the MRC header.
- Atlas: ``AtlasMosaicPixelSize`` ``x`` or ``y``, then ``pixelSize``, then the
  MRC header.
- Overview and anything else: ``pixelSize``, then the MRC header.

``pixelSize`` here is ``x`` or ``y``, whichever is positive first. The MRC
header (``scale_bar_mrc_pixel_size``) is the first positive voxel axis only,
read as metres when under 1e-6 and as ångströms otherwise; frame metadata is
not consulted.

MRC sampling (``mrc_pixel_size_angstrom``), ångströms per pixel, for the
parsers' MDOC cross-check and the PDF: every frame's ``pixel_size`` when they
agree (``None``, with a warning, when they do not), else ``voxel_size[0]``.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from tomography_session_browser.domain.coerce import as_float
from tomography_session_browser.domain.models import (
    Atlas,
    BatchPosition,
    MrcMetadata,
    Overview,
    SearchMap,
    SearchTile,
    TiltSeries,
)
from tomography_session_browser.parsers.xml_parser import find_first


# ---------------------------------------------------------------------------
# Overlays
# ---------------------------------------------------------------------------


def overlay_pixel_size(source: Atlas | Overview | SearchMap | SearchTile) -> float | None:
    """Metres per pixel, as the overlays read it (precedence above)."""

    # Atlas-specific override: prefer the rendered-mosaic pixel size when the
    # parser has computed one. The per-tile pixelSize in the XML measures the
    # *capture* resolution (e.g. 0.47 µm/px), but the displayed Atlas image
    # is a heavily downsampled mosaic with a much coarser effective pixel
    # size — using the per-tile value here makes Search Map / Overview
    # overlays render orders of magnitude too large.
    if isinstance(source, Atlas):
        mosaic = source.metadata.get("AtlasMosaicPixelSize") if isinstance(source.metadata, dict) else None
        if isinstance(mosaic, dict):
            x_value = as_float(mosaic.get("x"))
            if x_value is not None and x_value > 0:
                return x_value
    pixel_size = find_first(source.metadata, "pixelSize")
    if isinstance(pixel_size, dict):
        x_value = pixel_size.get("x")
        if isinstance(x_value, dict):
            value = as_float(x_value.get("numericValue"))
            if value is not None and value > 0:
                return value
    metadata = getattr(source, "mrc_metadata", None)
    if isinstance(metadata, MrcMetadata):
        return overlay_mrc_pixel_size(metadata)
    return None


def overlay_mrc_pixel_size(metadata: MrcMetadata | None) -> float | None:
    """Metres per pixel from an MRC header, as the overlays read it."""

    if metadata is None:
        return None
    frame = metadata.frame_metadata[0] if metadata.frame_metadata else None
    if isinstance(frame, dict):
        raw_fields = frame.get("raw_fields")
        if isinstance(raw_fields, dict):
            for key in ("pixel_size_x", "pixel_size_y"):
                value = as_float(raw_fields.get(key))
                if value is not None and value > 0:
                    return value
        value = as_float(frame.get("pixel_size"))
        if value is not None and value > 0:
            # Parsed frame display fields are always Angstroms; raw FEI fields
            # above are always metres.
            return value * 1e-10
    x = metadata.voxel_size[0]
    if isinstance(x, int | float) and x > 0:
        # MRC voxel sizes are canonical Angstroms.
        return float(x) * 1e-10
    return None


# ---------------------------------------------------------------------------
# Scale bar
# ---------------------------------------------------------------------------


def scale_bar_pixel_size(value: Any, displayed_path: Path | None = None) -> float | None:
    """Metres per pixel, as the scale bar and the viewer's ``/px`` label read
    it (precedence above). ``displayed_path`` picks a batch's image."""

    if isinstance(value, TiltSeries) and value.pixel_size is not None:
        return value.pixel_size * 1e-10
    if isinstance(value, BatchPosition):
        metadata = batch_mrc_metadata_for_path(value, displayed_path)
        pixel = scale_bar_mrc_pixel_size(metadata)
        if pixel is not None:
            return pixel
        return scale_bar_xml_pixel_size(value.metadata)
    if isinstance(value, SearchTile):
        return scale_bar_xml_pixel_size(value.metadata.get("Tile.xml")) or scale_bar_mrc_pixel_size(value.mrc_metadata)
    if isinstance(value, SearchMap):
        return scale_bar_xml_pixel_size(value.metadata.get("SearchMap.xml")) or scale_bar_xml_pixel_size(value.metadata) or scale_bar_mrc_pixel_size(value.mrc_metadata)
    if isinstance(value, Atlas):
        mosaic = value.metadata.get("AtlasMosaicPixelSize")
        if isinstance(mosaic, dict):
            pixel = as_float(mosaic.get("x")) or as_float(mosaic.get("y"))
            if pixel is not None and pixel > 0:
                return pixel
        return scale_bar_xml_pixel_size(value.metadata) or scale_bar_mrc_pixel_size(value.mrc_metadata)
    if isinstance(value, Overview):
        return scale_bar_xml_pixel_size(value.metadata) or scale_bar_mrc_pixel_size(value.mrc_metadata)
    metadata = getattr(value, "mrc_metadata", None)
    return scale_bar_mrc_pixel_size(metadata if isinstance(metadata, MrcMetadata) else None)


def batch_mrc_metadata_for_path(batch: BatchPosition, displayed_path: Path | None) -> MrcMetadata | None:
    if displayed_path is not None:
        if batch.search_image_path == displayed_path:
            return batch.search_mrc_metadata
        if batch.tracking_image_path == displayed_path:
            return batch.tracking_mrc_metadata
        if batch.exposure_image_path == displayed_path or displayed_path in batch.exposure_image_paths:
            return batch.exposure_mrc_metadata
    return batch.search_mrc_metadata or batch.tracking_mrc_metadata or batch.exposure_mrc_metadata


def scale_bar_mrc_pixel_size(metadata: MrcMetadata | None) -> float | None:
    if metadata is None:
        return None
    pixel = next((value for value in metadata.voxel_size if isinstance(value, int | float) and value > 0), None)
    if pixel is None:
        return None
    # MRC header voxel sizes are conventionally Å/px in this codebase. A tiny
    # value is treated as metres/px for safety when a parser has already
    # converted instrument metadata into SI units.
    return float(pixel) if pixel < 1e-6 else float(pixel) * 1e-10


def scale_bar_xml_pixel_size(metadata: Any) -> float | None:
    pixel_size = find_first(metadata, "pixelSize")
    if not isinstance(pixel_size, dict):
        return None
    x_value = pixel_size.get("x")
    y_value = pixel_size.get("y")
    for node in (x_value, y_value):
        if isinstance(node, dict):
            numeric = as_float(node.get("numericValue"))
            if numeric is not None and numeric > 0:
                return numeric
    return None


# ---------------------------------------------------------------------------
# MRC sampling
# ---------------------------------------------------------------------------


def mrc_pixel_size_angstrom(metadata: Any, warnings: list[str]) -> float | None:
    frame_values: list[float] = []
    for frame in getattr(metadata, "frame_metadata", None) or []:
        value = frame.get("pixel_size") if isinstance(frame, dict) else None
        try:
            numeric = float(value) if value is not None else None
        except (TypeError, ValueError):
            numeric = None
        if numeric is not None and math.isfinite(numeric) and numeric > 0:
            frame_values.append(numeric)
    if frame_values:
        reference = frame_values[0]
        if any(
            not math.isclose(value, reference, rel_tol=1e-6, abs_tol=1e-6)
            for value in frame_values[1:]
        ):
            text = ", ".join(f"{value:g}" for value in sorted(set(frame_values)))
            warnings.append(
                "MRC extended header has inconsistent pixel_size values: "
                f"{text}; pixel-size cross-check unavailable."
            )
            return None
        return reference
    voxel_size = getattr(metadata, "voxel_size", (None, None, None))
    value = voxel_size[0] if voxel_size else None
    try:
        numeric = float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    return numeric if numeric is not None and math.isfinite(numeric) and numeric > 0 else None
