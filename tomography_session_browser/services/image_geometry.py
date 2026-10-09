"""Where a stage position lands on an image, and what an image's frame is.

The overlay projection's geometry, Qt-free and pure: each image's frame (its
pixel size, stage position, size and stage basis, read with the overlays'
precedence), the Atlas node-table affine, stage-to-pixel projection, and the
two Tomography 5 point corrections. ``services/markers`` builds markers on it;
the search-tile linking service uses it to place tiles on a search map. Moved here from
the marker service unchanged: a change to any of it moves every overlay, so it
needs regression tests and a visual check.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import math
from typing import Any

from tomography_session_browser.domain.coerce import as_float, as_int
from tomography_session_browser.domain.models import Atlas, BatchPosition, MrcMetadata, Overview, SearchMap, SearchTile
from tomography_session_browser.parsers.xml_parser import find_first
from tomography_session_browser.services.atlas_metadata import atlas_projection_mismatch
from tomography_session_browser.services.pixel_size import overlay_pixel_size

LOGGER = logging.getLogger(__name__)

# Minimum number of distinct atlas tile nodes required before we trust a fitted
# stage->pixel affine. Three non-degenerate points define an affine map; we ask
# for a few more so a single mis-recorded tile cannot dominate the least-squares
# fit. Atlases below this fall back to the legacy basis + 180 degree path.
_MIN_ATLAS_NODE_SAMPLES = 4
# If the fitted affine cannot reproduce the recorded node pixels to within this
# many pixels we treat the table as untrustworthy and fall back. A clean
# Tomography 5 atlas fits to well under 1 px.
_MAX_ATLAS_NODE_RESIDUAL_PX = 8.0


@dataclass(frozen=True, slots=True)
class AtlasPixelAffine:
    """Exact stage -> atlas-pixel affine recovered from the Atlas.dm node table.

    Tomography 5 records, for every atlas tile, the *measured* mapping between
    its stage coordinate and its pixel rectangle in the stitched atlas mosaic
    (``StagePosition`` -> ``AtlasPixelPosition``). Fitting an affine to that
    table reproduces the true rotation, anisotropic scale, handedness flip and
    origin of the atlas image — all in one transform — instead of approximating
    them with a normalised per-tile basis plus an empirical 180 degree spin.

    Coefficients map a stage coordinate in metres to a pixel in the atlas
    *montage* frame (``image_size``):

        px = a * stage_x + b * stage_y + c
        py = d * stage_x + e * stage_y + g

    ``pixel_size`` is the mean metres-per-pixel of the two fitted axes, kept so
    overlay *extents* (search-map / overview boxes) scale with the same physical
    resolution the centres are placed in.
    """

    coefficients: tuple[float, float, float, float, float, float]
    image_size: tuple[int, int]
    pixel_size: float
    source: str
    residual_px: float
    sample_count: int

    def apply(self, stage_x: float, stage_y: float) -> tuple[float, float]:
        a, b, c, d, e, g = self.coefficients
        return (a * stage_x + b * stage_y + c, d * stage_x + e * stage_y + g)


@dataclass(frozen=True, slots=True)
class ImageFrame:
    image_size: tuple[int, int]
    stage_position: tuple[float, float]
    pixel_size: float
    source: str
    stage_basis: tuple[tuple[float, float], tuple[float, float]] | None = None
    stage_basis_source: str | None = None
    # Exact stage->pixel affine for Atlas frames that ship a node table. When
    # present, ``stage_to_image`` uses it directly and the legacy 180 degree
    # atlas-overlay rotation is skipped (the affine already encodes rotation,
    # flip, anisotropic scale and origin). ``None`` for every non-atlas frame
    # and for atlases without a usable node table (legacy fallback path).
    atlas_pixel_affine: AtlasPixelAffine | None = None


def _atlas_pixel_affine(atlas: Atlas) -> AtlasPixelAffine | None:
    """Fit the exact stage->atlas-pixel affine from the Atlas.dm node table.

    Tomography 5's ``Atlas.dm`` stores one node per acquired atlas tile, each
    pairing the tile's microscope ``StagePosition`` (metres) with its
    ``AtlasPixelPosition`` rectangle (pixels in the stitched mosaic). That table
    is the microscope's own record of how stage coordinates map onto the atlas
    image, so fitting an affine to it yields the true rotation, anisotropic
    scale, handedness and origin in a single transform.

    Returns ``None`` (legacy fallback) when the table is missing, too small, or
    fits too poorly to trust.
    """

    if atlas_projection_mismatch(atlas):
        return None
    metadata = atlas.metadata.get("Atlas.dm") if isinstance(atlas.metadata, dict) else None
    if not isinstance(metadata, dict):
        return None
    pairs = _collect_atlas_node_pairs(metadata)
    if len(pairs) < _MIN_ATLAS_NODE_SAMPLES:
        if pairs:
            LOGGER.debug(
                "Atlas node table too small for affine fit atlas=%s nodes=%d", atlas.id, len(pairs)
            )
        return None
    fit = _fit_affine_least_squares(pairs)
    if fit is None:
        LOGGER.debug("Atlas node table affine fit was degenerate atlas=%s", atlas.id)
        return None
    coefficients, residual_px = fit
    if residual_px > _MAX_ATLAS_NODE_RESIDUAL_PX:
        LOGGER.warning(
            "Atlas node table affine residual too high; using legacy projection "
            "atlas=%s residual=%.2fpx nodes=%d",
            atlas.id,
            residual_px,
            len(pairs),
        )
        return None

    # The montage frame the AtlasPixelPosition values live in is the atlas image
    # itself. This becomes the marker ``image_size`` so the viewer/report scale
    # overlays onto the displayed atlas pixmap correctly (it differs from the
    # camera ``ReadoutArea`` that ``image_size_for_markers`` reports for the atlas).
    image_size = _atlas_montage_size(atlas, pairs)

    a, b, _c, d, e, _g = coefficients
    pixel_size = (math.hypot(a, d) ** -1 + math.hypot(b, e) ** -1) / 2.0

    return AtlasPixelAffine(
        coefficients=coefficients,
        image_size=image_size,
        pixel_size=pixel_size,
        source="Atlas.dm:StagePosition->AtlasPixelPosition",
        residual_px=residual_px,
        sample_count=len(pairs),
    )


def _atlas_montage_size(
    atlas: Atlas,
    pairs: list[tuple[tuple[float, float], tuple[float, float, float, float]]],
) -> tuple[int, int]:
    """Size of the stitched atlas mosaic the node rectangles are placed in.

    The Atlas MRC *is* that mosaic, so its ``nx``/``ny`` are exact. The far
    corner of the furthest node rectangle is only a lower bound: an atlas
    whose acquisition was aborted records nodes for the tiles it acquired,
    while the mosaic keeps its full planned size. Using the node extent there
    stretched every overlay away from the image origin, by up to the
    unacquired fraction of the mosaic. The extent remains the fallback for a
    JPG-only atlas, and for an MRC too small to hold the nodes (a frame the
    node table was not recorded in).
    """

    extent = (
        int(round(max(rect[0] + rect[2] for _, rect in pairs))),
        int(round(max(rect[1] + rect[3] for _, rect in pairs))),
    )
    metadata = atlas.mrc_metadata
    nx = metadata.nx if metadata is not None else None
    ny = metadata.ny if metadata is not None else None
    if nx is not None and ny is not None and nx > 0 and ny > 0:
        if extent[0] <= nx + 1 and extent[1] <= ny + 1:
            return nx, ny
        LOGGER.warning(
            "Atlas node rectangles extend beyond the atlas MRC; using the node extent as the "
            "overlay frame atlas=%s extent=%s mrc=%sx%s",
            atlas.id,
            extent,
            nx,
            ny,
        )
    return extent


def _collect_atlas_node_pairs(
    metadata: dict[str, Any],
) -> list[tuple[tuple[float, float], tuple[float, float, float, float]]]:
    """Walk parsed Atlas.dm and return ``(stage_xy_m, pixel_rect)`` per tile.

    ``pixel_rect`` is ``(x, y, width, height)`` of the tile's
    ``AtlasPixelPosition`` (top-left origin); callers use the rectangle centre
    as the point paired with the stage coordinate.
    """

    pairs: list[tuple[tuple[float, float], tuple[float, float, float, float]]] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            stage = node.get("StagePosition")
            pixel = node.get("AtlasPixelPosition")
            if isinstance(stage, dict) and isinstance(pixel, dict):
                sx = as_float(find_first(stage, "X"))
                sy = as_float(find_first(stage, "Y"))
                px = as_float(find_first(pixel, "x"))
                py = as_float(find_first(pixel, "y"))
                pw = as_float(find_first(pixel, "width"))
                ph = as_float(find_first(pixel, "height"))
                if (
                    sx is not None and sy is not None and px is not None
                    and py is not None and pw is not None and ph is not None
                ):
                    pairs.append(((sx, sy), (px, py, pw, ph)))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(metadata)
    return pairs


def _fit_affine_least_squares(
    pairs: list[tuple[tuple[float, float], tuple[float, float, float, float]]],
) -> tuple[tuple[float, float, float, float, float, float], float] | None:
    """Least-squares fit ``pixel_centre = A @ [stage_x, stage_y, 1]``.

    Pure-Python normal-equations solve (3 unknowns per axis) so the service
    layer stays free of a hard numpy dependency. Returns the six affine
    coefficients ``(a, b, c, d, e, g)`` and the worst per-node residual in
    pixels, or ``None`` if the stage points are collinear/degenerate.
    """

    # Build the 3x3 normal matrix N = MᵀM and right-hand sides for x and y, where
    # each row of M is [stage_x, stage_y, 1] and the targets are the tile-centre
    # pixel coordinates.
    n11 = n12 = n13 = n22 = n23 = n33 = 0.0
    bx1 = bx2 = bx3 = 0.0
    by1 = by2 = by3 = 0.0
    points: list[tuple[float, float, float, float]] = []
    for (sx, sy), (px, py, pw, ph) in pairs:
        cx = px + pw / 2.0
        cy = py + ph / 2.0
        points.append((sx, sy, cx, cy))
        n11 += sx * sx
        n12 += sx * sy
        n13 += sx
        n22 += sy * sy
        n23 += sy
        n33 += 1.0
        bx1 += sx * cx
        bx2 += sy * cx
        bx3 += cx
        by1 += sx * cy
        by2 += sy * cy
        by3 += cy

    normal = [
        [n11, n12, n13],
        [n12, n22, n23],
        [n13, n23, n33],
    ]
    coef_x = _solve_3x3(normal, [bx1, bx2, bx3])
    coef_y = _solve_3x3(normal, [by1, by2, by3])
    if coef_x is None or coef_y is None:
        return None

    a, b, c = coef_x
    d, e, g = coef_y
    residual = 0.0
    for sx, sy, cx, cy in points:
        qx = a * sx + b * sy + c
        qy = d * sx + e * sy + g
        residual = max(residual, math.hypot(qx - cx, qy - cy))
    return (a, b, c, d, e, g), residual


def _solve_3x3(matrix: list[list[float]], rhs: list[float]) -> tuple[float, float, float] | None:
    """Solve a 3x3 linear system by Gaussian elimination with partial pivoting."""

    augmented = [list(row) + [rhs[index]] for index, row in enumerate(matrix)]
    for column in range(3):
        pivot_row = max(range(column, 3), key=lambda r: abs(augmented[r][column]))
        if abs(augmented[pivot_row][column]) < 1e-30:
            return None
        augmented[column], augmented[pivot_row] = augmented[pivot_row], augmented[column]
        pivot = augmented[column][column]
        for r in range(3):
            if r == column:
                continue
            factor = augmented[r][column] / pivot
            for k in range(column, 4):
                augmented[r][k] -= factor * augmented[column][k]
    return (
        augmented[0][3] / augmented[0][0],
        augmented[1][3] / augmented[1][1],
        augmented[2][3] / augmented[2][2],
    )


def stage_to_image(frame: ImageFrame, stage_position: tuple[float, float]) -> tuple[float, float]:
    """The pixel in ``frame`` where a stage position (metres) lands."""

    if frame.atlas_pixel_affine is not None:
        # Atlas frame with a recovered node-table affine: this single transform
        # already carries rotation, handedness flip, anisotropic scale and the
        # true origin, so apply it directly in the montage pixel frame. No image
        # centring and no 180 degree post-rotation are needed (or wanted).
        return frame.atlas_pixel_affine.apply(stage_position[0], stage_position[1])
    width, height = frame.image_size
    delta_x = stage_position[0] - frame.stage_position[0]
    delta_y = stage_position[1] - frame.stage_position[1]
    if frame.stage_basis is not None:
        x_axis, y_axis = frame.stage_basis
        determinant = (x_axis[0] * y_axis[1]) - (x_axis[1] * y_axis[0])
        if abs(determinant) > 1e-24:
            image_delta_x = ((delta_x * y_axis[1]) - (delta_y * y_axis[0])) / determinant
            image_delta_y = ((x_axis[0] * delta_y) - (x_axis[1] * delta_x)) / determinant
            return (width / 2) + image_delta_x, (height / 2) + image_delta_y
    x = (width / 2) + (delta_x / frame.pixel_size)
    y = (height / 2) - (delta_y / frame.pixel_size)
    return x, y


def reflect_point_through_image_center(
    x: float,
    y: float,
    image_width: int,
    image_height: int,
) -> tuple[float, float]:
    """Experimental correction for Tomography 5 Search Map exposure overlays.

    The generated exposure marker positions appear to be related to the correct
    positions by a point reflection through the image centre.
    """
    reflected_x = image_width - 1 - x
    reflected_y = image_height - 1 - y
    return reflected_x, reflected_y


def rotate_point_180_around_anchor(
    x: float,
    y: float,
    anchor_x: float,
    anchor_y: float,
) -> tuple[float, float]:
    """Rotate a point 180 degrees around an anchor point.

    Used to correct linked Tomography 5 exposure positions around their central
    exposure tile.
    """
    return 2.0 * anchor_x - x, 2.0 * anchor_y - y


def frame_diagnostic(source: Atlas | Overview | SearchMap | SearchTile) -> str:
    """Return a human-readable description of which frame field is missing."""

    if isinstance(source, Atlas) and (reason := atlas_projection_mismatch(source)):
        return reason
    issues: list[str] = []
    if image_size_for_markers(source) is None:
        issues.append("image dimensions unknown")
    if stage_position_for_markers(source) is None:
        issues.append("no stage position recorded")
    if overlay_pixel_size(source) is None:
        issues.append("pixel size unknown")
    if not issues:
        return "frame metadata could not be assembled"
    return ", ".join(issues)


def image_frame(source: Atlas | Overview | SearchMap | SearchTile) -> ImageFrame | None:
    """The frame overlays on ``source`` are projected into, or ``None`` when its
    size, stage position or pixel size is unknown (``frame_diagnostic`` says which)."""

    # A mismatched node table must not fall through to the legacy calibration:
    # that geometry also comes from the other acquisition's Atlas.dm.
    if isinstance(source, Atlas) and atlas_projection_mismatch(source):
        return None
    image_size = image_size_for_markers(source)
    stage_position = stage_position_for_markers(source)
    pixel_size = overlay_pixel_size(source)
    if image_size is None or stage_position is None or pixel_size is None or pixel_size <= 0:
        LOGGER.debug(
            "Missing image frame metadata source=%s image_size=%s stage=%s pixel=%s",
            getattr(source, "id", None),
            image_size,
            stage_position,
            pixel_size,
        )
        return None

    # Detailed frame diagnostics are useful when investigating projection
    # units, but are too noisy for normal session-level INFO logging.
    pixel_um = pixel_size * 1e6
    fov_x_um = image_size[0] * pixel_um
    fov_y_um = image_size[1] * pixel_um
    LOGGER.debug(
        "image frame: type=%s id=%s image_size=%dx%d px metadata_pixel_size=%.6e m/px "
        "(=%.4f µm/px) stage_position=(%.6e, %.6e) m fov=%.1fx%.1f µm",
        type(source).__name__,
        getattr(source, "id", "<unknown>"),
        image_size[0],
        image_size[1],
        pixel_size,
        pixel_um,
        stage_position[0],
        stage_position[1],
        fov_x_um,
        fov_y_um,
    )

    stage_basis, stage_basis_source = _stage_basis(source, pixel_size)
    if stage_basis is not None:
        LOGGER.debug(
            "image frame stage basis: type=%s id=%s source=%s "
            "x_axis=(%.6e, %.6e) m/px y_axis=(%.6e, %.6e) m/px",
            type(source).__name__,
            getattr(source, "id", "<unknown>"),
            stage_basis_source,
            stage_basis[0][0],
            stage_basis[0][1],
            stage_basis[1][0],
            stage_basis[1][1],
        )

    # Atlas frames prefer the exact node-table affine when one is recorded. It
    # overrides the camera-readout ``image_size`` with the montage frame the
    # AtlasPixelPosition values live in, and supplies the metres-per-pixel the
    # overlay extents should scale with. Non-atlas frames keep the legacy path.
    atlas_pixel_affine: AtlasPixelAffine | None = None
    if isinstance(source, Atlas):
        atlas_pixel_affine = _atlas_pixel_affine(source)
        if atlas_pixel_affine is not None:
            LOGGER.info(
                "atlas overlay projection: id=%s using node-table affine source=%s "
                "nodes=%d residual=%.2fpx montage_image_size=%dx%d pixel_size=%.6e m/px "
                "(replaces camera-readout image_size=%dx%d and the legacy 180deg rotation)",
                getattr(source, "id", "<unknown>"),
                atlas_pixel_affine.source,
                atlas_pixel_affine.sample_count,
                atlas_pixel_affine.residual_px,
                atlas_pixel_affine.image_size[0],
                atlas_pixel_affine.image_size[1],
                atlas_pixel_affine.pixel_size,
                image_size[0],
                image_size[1],
            )
            image_size = atlas_pixel_affine.image_size
            pixel_size = atlas_pixel_affine.pixel_size
        else:
            LOGGER.info(
                "atlas overlay projection: id=%s no usable node table; using legacy "
                "stage-basis + 180deg rotation fallback",
                getattr(source, "id", "<unknown>"),
            )

    return ImageFrame(
        image_size=image_size,
        stage_position=stage_position,
        pixel_size=pixel_size,
        source=type(source).__name__,
        stage_basis=stage_basis,
        stage_basis_source=stage_basis_source,
        atlas_pixel_affine=atlas_pixel_affine,
    )


def image_size_for_markers(source: Atlas | Overview | SearchMap | SearchTile) -> tuple[int, int] | None:
    """Pixel dimensions in which marker coordinates are expressed.

    The PDF report needs the *same* image_size that the marker computation used
    so overlays scale correctly when drawn on top of an embedded JPG.
    Precedence: the XML ``ImageSize`` / ``ReadoutArea``, then the MRC header,
    then the atlas parser's cached JPG size.
    """

    for key in ("SearchMap.dm", "Atlas.dm", "Overview.dm", "Tile.xml"):
        value = find_first(source.metadata.get(key), "ImageSize")
        if isinstance(value, dict):
            width = as_int(value.get("width"))
            height = as_int(value.get("height"))
            if width is not None and height is not None and width > 0 and height > 0:
                return width, height
        value = find_first(source.metadata.get(key), "ReadoutArea")
        if isinstance(value, dict):
            width = as_int(value.get("width"))
            height = as_int(value.get("height"))
            if width is not None and height is not None and width > 0 and height > 0:
                return width, height
    metadata = getattr(source, "mrc_metadata", None)
    if (
        isinstance(metadata, MrcMetadata)
        and metadata.nx is not None
        and metadata.ny is not None
        and metadata.nx > 0
        and metadata.ny > 0
    ):
        return metadata.nx, metadata.ny
    # Fallback: ``atlas_parser`` caches the rendered JPG dimensions when the
    # XML mosaic-level ``ImageSize`` element is absent. Without this, the
    # Atlas tab silently rendered no markers on screening sessions.
    cached = source.metadata.get("AtlasImageSize") if isinstance(source.metadata, dict) else None
    if isinstance(cached, dict):
        width = as_int(cached.get("width"))
        height = as_int(cached.get("height"))
        if width is not None and height is not None and width > 0 and height > 0:
            return width, height
    return None


def stage_position_for_markers(source: Atlas | Overview | SearchMap | SearchTile) -> tuple[float, float] | None:
    """The stage position (metres) of ``source``'s centre: the MRC header's,
    then the XML ``StagePosition``, then any ``Position``."""

    metadata = getattr(source, "mrc_metadata", None)
    if isinstance(metadata, MrcMetadata):
        position = mrc_stage_position(metadata)
        if position is not None:
            return position
    for key in ("SearchMap.dm", "Atlas.dm", "Overview.dm"):
        value = find_first(source.metadata.get(key), "StagePosition")
        if isinstance(value, dict):
            x = as_float(value.get("X"))
            y = as_float(value.get("Y"))
            if x is not None and y is not None:
                return x, y
    value = find_first(source.metadata, "Position")
    return stage_position_from_metadata({"Position": value}) if isinstance(value, dict) else None


def stage_position_from_metadata(metadata: object) -> tuple[float, float] | None:
    """The first ``Position``'s ``X`` and ``Y`` in a metadata dict, or ``None``."""

    if not isinstance(metadata, dict):
        return None
    value = find_first(metadata, "Position")
    if not isinstance(value, dict):
        return None
    x = as_float(value.get("X"))
    y = as_float(value.get("Y"))
    return (x, y) if x is not None and y is not None else None


def _stage_basis(
    source: Atlas | Overview | SearchMap | SearchTile,
    pixel_size: float,
) -> tuple[tuple[tuple[float, float], tuple[float, float]] | None, str | None]:
    reference, reference_source = _reference_transformation_metadata(source)
    transformation = find_first(reference, "ReferenceTransformation") if isinstance(reference, dict) else None
    matrix = transformation.get("matrix") if isinstance(transformation, dict) else None
    if not isinstance(matrix, dict):
        return None, None
    m11 = as_float(matrix.get("_m11"))
    m12 = as_float(matrix.get("_m12"))
    m21 = as_float(matrix.get("_m21"))
    m22 = as_float(matrix.get("_m22"))
    if m11 is None or m12 is None or m21 is None or m22 is None:
        return None, None
    # ``ReferenceTransformation`` records the stage-space direction of the
    # image x/y pixel axes.  We preserve direction and handedness, then scale
    # each axis to the frame pixel size that marker coordinates are expressed
    # in.  This is especially important for Atlas mosaics: their parsed
    # metadata stores the transform inside ``Atlas_*.xml`` rather than as a
    # top-level ``Atlas.metadata["ReferenceTransformation"]`` entry, and
    # ignoring it mirrors/rotates child Overview/Search map overlays around
    # the atlas centre.
    x_axis = _normalized_scaled_vector((m11, m12), pixel_size)
    y_axis = _normalized_scaled_vector((m21, m22), pixel_size)
    if x_axis is None or y_axis is None:
        return None, None
    return (x_axis, y_axis), reference_source


def _reference_transformation_metadata(
    source: Atlas | Overview | SearchMap | SearchTile,
) -> tuple[dict[str, Any] | None, str | None]:
    candidates: list[tuple[str, object]] = [
        ("Tile.xml", source.metadata.get("Tile.xml")),
        ("SearchMap.xml", source.metadata.get("SearchMap.xml")),
        ("Overview.xml", source.metadata.get("Overview.xml")),
    ]
    if isinstance(source, Atlas):
        candidates.extend(
            (key, value)
            for key, value in source.metadata.items()
            if isinstance(key, str) and key.startswith("Atlas_") and key.endswith(".xml")
        )
    candidates.append(("metadata", source.metadata))
    for label, candidate in candidates:
        if isinstance(candidate, dict) and isinstance(find_first(candidate, "ReferenceTransformation"), dict):
            return candidate, label
    return None, None


def _normalized_scaled_vector(vector: tuple[float, float], scale: float) -> tuple[float, float] | None:
    length = ((vector[0] * vector[0]) + (vector[1] * vector[1])) ** 0.5
    if length <= 0 or scale <= 0:
        return None
    return (vector[0] / length * scale, vector[1] / length * scale)


def mrc_stage_position(metadata: MrcMetadata | None) -> tuple[float, float] | None:
    """The first frame's stage position (metres) from an MRC header."""

    if metadata is None:
        return None
    frame = metadata.frame_metadata[0] if metadata.frame_metadata else None
    if not isinstance(frame, dict):
        return None
    x = as_float(frame.get("stage_x"))
    y = as_float(frame.get("stage_y"))
    return (x, y) if x is not None and y is not None else None


def position_on_tile_set(batch: BatchPosition) -> tuple[float, float] | None:
    """A batch position's ``PositionOnTileSet`` stage position, or ``None``."""

    value = batch.metadata.get("PositionOnTileSet")
    if not isinstance(value, dict):
        return None
    x = as_float(value.get("StagePositionX"))
    y = as_float(value.get("StagePositionY"))
    return (x, y) if x is not None and y is not None else None
