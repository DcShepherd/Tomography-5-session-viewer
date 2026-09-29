from __future__ import annotations

import logging
import math
from pathlib import Path
import re
from typing import Any

from tomography_session_browser.domain.models import Atlas
from tomography_session_browser.parsers.mrc_parser import read_mrc_metadata
from tomography_session_browser.parsers.path_utils import safe_id, sorted_paths
from tomography_session_browser.parsers.xml_parser import find_first, parse_xml_file
from tomography_session_browser.services.atlas_metadata import atlas_projection_mismatch, referenced_atlas_stem

LOGGER = logging.getLogger(__name__)
_TILE_ATLAS_ID_RE = re.compile(r"^Tile_\d+_\d+_(\d+)$")


def parse_atlas_folder(path: Path) -> Atlas | None:
    if not path.exists() or not path.is_dir():
        return None

    metadata = {}
    warnings: list[str] = []
    dm_path = path / "Atlas.dm"
    if dm_path.exists():
        metadata["Atlas.dm"] = _parse_xml_metadata(dm_path, warnings)

    # Tomography 5 keeps every acquisition when a grid's atlas is re-acquired,
    # so one Atlas folder can hold several Atlas_<id> mosaics. Atlas.dm names
    # the current one, and its node table (the overlay projection) describes
    # that mosaic only; the image, XML and tiles must all come from it.
    atlas_mrcs = sorted_paths(list(path.glob("Atlas_*.mrc")))
    atlas_jpgs = sorted_paths(list(path.glob("Atlas_*.jpg")))
    atlas_stems = list(dict.fromkeys(item.stem for item in sorted_paths(atlas_mrcs + atlas_jpgs)))
    atlas_stem = _current_atlas_stem(metadata.get("Atlas.dm"), atlas_stems, warnings)
    selected_atlas_id = _atlas_id(atlas_stem) if atlas_stem is not None else None
    if atlas_stem is not None:
        atlas_mrcs = [item for item in atlas_mrcs if item.stem == atlas_stem]
        atlas_jpgs = [item for item in atlas_jpgs if item.stem == atlas_stem]

    atlas_xmls = sorted_paths(list(path.glob("Atlas_*.xml")))
    if atlas_stem is not None:
        atlas_xmls = [item for item in atlas_xmls if item.stem == atlas_stem]
    for xml_path in atlas_xmls[:5]:
        metadata[xml_path.name] = _parse_xml_metadata(xml_path, warnings)

    # Prefer the Atlas MRC over the JPG when both exist: the MRC is the
    # native-resolution stitched mosaic (e.g. 4005x3971 px at 4.66 µm/px),
    # whereas the JPG is a heavily downsampled preview (~512 px) that
    # requires a separate effective-pixel-size correction. The MRC keeps
    # marker overlays at the correct scale automatically because its
    # ``voxel_size`` matches the per-tile XML pixel size exactly.
    image_path = atlas_mrcs[0] if atlas_mrcs else (atlas_jpgs[0] if atlas_jpgs else None)
    if image_path is None and not dm_path.exists():
        return None
    if image_path is None:
        warnings.append("Atlas metadata exists but no Atlas_*.jpg or Atlas_*.mrc was found.")

    alignment_paths = sorted_paths(
        list(path.glob("Overview_Alignment_*.mrc"))
        + list(path.glob("Overview_Alignment_*.jpg"))
        + list(path.glob("Overview_Alignment_*.xml"))
    )
    tile_paths = _without_other_atlas_tiles(
        sorted_paths(list(path.glob("Tile_*.mrc")) + list(path.glob("Tile_*.jpg"))),
        selected_atlas_id,
    )
    tile_metadata_paths = _without_other_atlas_tiles(
        sorted_paths(list(path.glob("Tile_*.xml"))),
        selected_atlas_id,
    )

    # Cache the rendered JPG dimensions whenever a JPG sibling exists. Used
    # as the *fallback* image size for atlases that ship only a downsampled
    # mosaic JPG and no MRC. When an Atlas MRC is the primary image_path,
    # ``_image_size`` reads ``mrc_metadata.nx/ny`` directly (native scale),
    # so this cache is not consulted.
    jpg_dims: tuple[int, int] | None = None
    jpg_path = atlas_jpgs[0] if atlas_jpgs else None
    if jpg_path is not None:
        jpg_dims = _read_image_dimensions(jpg_path)
        if jpg_dims is not None:
            metadata["AtlasImageSize"] = {"width": jpg_dims[0], "height": jpg_dims[1]}

    # JPG-only atlases need the effective mosaic pixel size correction —
    # the JPG is heavily downsampled and the per-tile XML pixel size is at
    # the capture (native) resolution. With an MRC available we use its
    # native voxel size instead and skip this correction.
    using_mrc = image_path is not None and image_path.suffix.lower() == ".mrc"
    if not using_mrc and jpg_dims is not None and tile_metadata_paths:
        effective = _compute_effective_mosaic_pixel_size(tile_metadata_paths, jpg_dims)
        if effective is not None:
            metadata["AtlasMosaicPixelSize"] = {"x": effective[0], "y": effective[1]}
            LOGGER.info(
                "atlas mosaic pixel size (JPG fallback): image=%s mosaic_size=%s "
                "effective_pixel_size=(%.3e, %.3e) m/px",
                image_path,
                jpg_dims,
                effective[0],
                effective[1],
            )
        else:
            LOGGER.debug(
                "atlas mosaic pixel size: could not compute for %s (mosaic_size=%s tile_xmls=%d)",
                image_path,
                jpg_dims,
                len(tile_metadata_paths),
            )

    atlas = Atlas(
        id=safe_id(path),
        image_path=image_path,
        mrc_metadata=read_mrc_metadata(image_path) if image_path is not None and image_path.suffix.lower() == ".mrc" else None,
        metadata=metadata,
        tile_paths=tile_paths,
        tile_metadata_paths=tile_metadata_paths,
        alignment_paths=alignment_paths,
        warnings=warnings,
    )
    if reason := atlas_projection_mismatch(atlas):
        atlas.warnings.append(f"Atlas markers unavailable: {reason}")
    return atlas


def _current_atlas_stem(atlas_dm: object, stems: list[str], warnings: list[str]) -> str | None:
    """Return the ``Atlas_<id>`` stem of the atlas mosaic to display.

    ``stems`` are the atlas images on disk in natural order. Atlas.dm's
    ``AtlasImageReference`` names the current atlas. Without a usable
    reference the latest acquisition is used: atlas IDs increase with
    acquisition time, so it is the last stem. Returns ``None`` when the folder
    holds no atlas image.
    """

    if not stems:
        return None
    latest = stems[-1]
    referenced = referenced_atlas_stem(atlas_dm)
    if referenced is not None and referenced not in stems:
        warnings.append(
            f"Atlas.dm names {referenced} as the current atlas but its image was not found "
            f"in the Atlas folder; showing the latest atlas on disk, {latest}."
        )
        referenced = None
    current = referenced or latest
    others = [stem for stem in stems if stem != current]
    if others:
        reason = "the current atlas in Atlas.dm" if referenced else "the latest acquisition"
        noun = "atlas" if len(others) == 1 else "atlases"
        warnings.append(
            f"Atlas was re-acquired: showing {current} ({reason}); "
            f"other {noun} {', '.join(others)} not displayed."
        )
    return current


def _atlas_id(stem: str) -> str:
    return stem.removeprefix("Atlas_")


def _without_other_atlas_tiles(paths: list[Path], selected_atlas_id: str | None) -> list[Path]:
    """Drop the tiles acquired for another atlas in the same folder.

    Tile names end with the ID of their atlas
    (``Tile_<tile id>_<index>_<atlas id>``). Tiles named any other way cannot
    be attributed, so they are kept. Compare with the selected acquisition,
    even when another acquisition's mosaic is no longer on disk.
    """

    if selected_atlas_id is None:
        return paths
    kept: list[Path] = []
    for item in paths:
        match = _TILE_ATLAS_ID_RE.match(item.stem)
        if match is None or match.group(1) == selected_atlas_id:
            kept.append(item)
    return kept


def _compute_effective_mosaic_pixel_size(
    tile_xml_paths: list[Path], mosaic_dims: tuple[int, int]
) -> tuple[float, float] | None:
    """Estimate the rendered mosaic's pixel size in metres/px.

    A Tomography 5 atlas JPG is a downsampled stitched mosaic of many tiles,
    each captured at the resolution recorded in its ``Tile_*.xml``
    (``pixelSize/x`` in metres). The mosaic image we display has its own,
    much coarser, pixel resolution. We compute that resolution by:

    1. Sampling all tile XMLs for their ``StagePosition`` and per-tile
       ``pixelSize`` and ``ReadoutArea`` (the captured image dimensions).
    2. Building the bounding box of tile *physical* extents on the stage —
       the min/max corners of every tile rectangle.
    3. Dividing that bounding box by the rendered mosaic dimensions to get
       metres-per-rendered-pixel.

    Returns ``(px_size_x_m, px_size_y_m)`` or ``None`` if the tile metadata
    is too sparse to make a credible measurement. We deliberately do not
    fall back to a rough heuristic — the call site has its own diagnostic
    path that surfaces "no scale" rather than rendering a wrong overlay.
    """

    if mosaic_dims[0] <= 0 or mosaic_dims[1] <= 0:
        return None

    # Cap the number of tile XMLs we parse to keep session-load fast.
    sample = list(tile_xml_paths)[:64]

    centres: list[tuple[float, float]] = []
    half_extents: list[tuple[float, float]] = []

    for tile_xml in sample:
        try:
            data = parse_xml_file(tile_xml)
        except Exception:  # pragma: no cover — defensive, parser already guards
            continue
        if _xml_problem(data):
            continue
        info = _tile_geometry(data)
        if info is None:
            continue
        centres.append(info[0])
        half_extents.append(info[1])

    if len(centres) < 2:
        return None

    min_x = min(c[0] - h[0] for c, h in zip(centres, half_extents))
    max_x = max(c[0] + h[0] for c, h in zip(centres, half_extents))
    min_y = min(c[1] - h[1] for c, h in zip(centres, half_extents))
    max_y = max(c[1] + h[1] for c, h in zip(centres, half_extents))

    span_x_m = max_x - min_x
    span_y_m = max_y - min_y
    if span_x_m <= 0 or span_y_m <= 0:
        return None

    return span_x_m / mosaic_dims[0], span_y_m / mosaic_dims[1]


def _tile_geometry(data: Any) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Return ``(centre_xy, half_extent_xy)`` in metres for one tile XML.

    Returns ``None`` if any required field is missing — we silently skip
    such tiles rather than guessing.
    """

    # Tomography 5 tile XMLs nest the stage coordinates under
    # ``stage/Position`` (with X/Y in metres). Some other documents in the
    # same session expose the stage coords directly as ``StagePosition``;
    # handle both shapes.
    stage = find_first(data, "stage")
    position: Any = stage.get("Position") if isinstance(stage, dict) else None
    if not isinstance(position, dict):
        position = find_first(data, "StagePosition")
    if not isinstance(position, dict):
        return None
    cx = _safe_float(position.get("X"))
    cy = _safe_float(position.get("Y"))
    if cx is None or cy is None:
        return None

    pixel = find_first(data, "pixelSize")
    if not isinstance(pixel, dict):
        return None
    px = _safe_float(_numeric_value(pixel.get("x")))
    py = _safe_float(_numeric_value(pixel.get("y")))
    if px is None or py is None or px <= 0 or py <= 0:
        return None

    readout = find_first(data, "ReadoutArea")
    if isinstance(readout, dict):
        width_px = _safe_float(readout.get("width"))
        height_px = _safe_float(readout.get("height"))
    else:
        width_px = height_px = None

    image_size = find_first(data, "ImageSize")
    if (width_px is None or height_px is None) and isinstance(image_size, dict):
        width_px = _safe_float(image_size.get("width"))
        height_px = _safe_float(image_size.get("height"))

    if width_px is None or height_px is None or width_px <= 0 or height_px <= 0:
        return None

    return (cx, cy), (width_px * px / 2.0, height_px * py / 2.0)


def _numeric_value(node: Any) -> Any:
    if isinstance(node, dict):
        return node.get("numericValue", node)
    return node


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _read_image_dimensions(path: Path) -> tuple[int, int] | None:
    """Read the pixel dimensions of a raster image without keeping it loaded.

    Returns ``None`` for any error — atlases load lazily and a missing image
    must not block session opening.
    """

    try:
        from PIL import Image  # type: ignore
    except ImportError:
        LOGGER.debug("Pillow not available; cannot read atlas image dimensions for %s", path)
        return None
    try:
        with Image.open(path) as image:
            return image.size  # (width, height)
    except (OSError, ValueError) as exc:
        LOGGER.debug("Could not read atlas image %s: %s", path, exc)
        return None


def _parse_xml_metadata(path: Path, warnings: list[str]) -> dict:
    data = parse_xml_file(path)
    message = data.get("_parse_error") or data.get("_read_error")
    if message:
        warnings.append(f"Could not parse atlas metadata XML {path.name}: {message}")
    return data


def _xml_problem(data: Any) -> bool:
    return isinstance(data, dict) and ("_parse_error" in data or "_read_error" in data)
