"""Build the scoped Atlas summary rows."""
from __future__ import annotations

from tomography_session_browser.domain.coerce import float_or_none, int_or_zero
from tomography_session_browser.domain.models import Atlas
from tomography_session_browser.parsers.xml_parser import find_first
from tomography_session_browser.presentation.dashboard.atlas_counts import canonical_atlas_key
from tomography_session_browser.presentation.dashboard.models import (
    AtlasCountModel,
    AtlasRowModel,
    AtlasSummaryModel,
    DashboardScope,
)
from tomography_session_browser.presentation.formatting import format_timestamp
from tomography_session_browser.services.item_status import atlas_tile_count


def atlas_summary_from_scope(
    scope: DashboardScope, atlas_counts: AtlasCountModel
) -> AtlasSummaryModel:
    """Atlas summary built from the already-resolved scope atlases.

    ``atlas_counts`` carries the deduplicated four-way counts derived
    from the original scope input (a Session, a list of Sessions, a
    Sample, or a LinkedSampleGroup). Those drive the headline numbers;
    the per-atlas detail rows continue to be sourced from
    ``scope.atlases`` so the atlas list stays comprehensive.
    """

    rows: list[AtlasRowModel] = []
    pixel_sizes: list[float] = []
    image_sizes: list[tuple[int, int]] = []
    seen_atlas_keys: set[str] = set()

    for sample_label, atlas in scope.atlases:
        key = canonical_atlas_key(atlas)
        if key in seen_atlas_keys:
            continue
        seen_atlas_keys.add(key)
        size = _atlas_image_size(atlas)
        pixel_um = _atlas_pixel_size_um(atlas)
        if pixel_um is not None:
            pixel_sizes.append(pixel_um)
        if size is not None:
            image_sizes.append(size)
        rows.append(
            AtlasRowModel(
                sample_label=sample_label,
                image_path=str(atlas.image_path) if atlas.image_path else None,
                image_size=size,
                pixel_size_um=pixel_um,
                tile_count=atlas_tile_count(atlas),
                acquisition_time=atlas_acquisition_time(atlas),
            )
        )

    pixel_range: tuple[float, float] | None = (min(pixel_sizes), max(pixel_sizes)) if pixel_sizes else None
    image_range: tuple[tuple[int, int], tuple[int, int]] | None = None
    if image_sizes:
        smallest = min(image_sizes, key=lambda s: s[0] * s[1])
        largest = max(image_sizes, key=lambda s: s[0] * s[1])
        image_range = (smallest, largest)

    # Legacy fields are now sourced from the corrected counts so any
    # reader that looked at ``atlas_count`` / ``sample_count_with_atlas``
    # gets the same number the four-way breakdown shows.
    return AtlasSummaryModel(
        atlas_count=atlas_counts.sample_atlas_count,
        sample_count_with_atlas=atlas_counts.samples_with_atlas_count,
        rows=rows,
        pixel_size_range_um=pixel_range,
        image_size_range=image_range,
        counts=atlas_counts,
    )


def _atlas_image_size(atlas: Atlas) -> tuple[int, int] | None:
    """Return ``(width, height)`` for the displayed atlas image in pixels.

    Prefers the MRC dimensions (the parser now selects ``Atlas_*.mrc`` as the
    primary image when available — that is the native-resolution mosaic).
    Falls back to the embedded XML ``ImageSize`` and, last, the cached JPG
    dimensions used as a fallback display image.
    """

    if atlas.mrc_metadata is not None:
        if atlas.mrc_metadata.nx and atlas.mrc_metadata.ny:
            return atlas.mrc_metadata.nx, atlas.mrc_metadata.ny
    embedded = find_first(atlas.metadata, "ImageSize") if isinstance(atlas.metadata, dict) else None
    if isinstance(embedded, dict):
        w = int_or_zero(embedded.get("width"))
        h = int_or_zero(embedded.get("height"))
        if w and h:
            return w, h
    cached = atlas.metadata.get("AtlasImageSize") if isinstance(atlas.metadata, dict) else None
    if isinstance(cached, dict):
        w = int_or_zero(cached.get("width"))
        h = int_or_zero(cached.get("height"))
        if w and h:
            return w, h
    return None


def _atlas_pixel_size_um(atlas: Atlas) -> float | None:
    """Return the atlas pixel size in micrometres, or ``None``.

    The XML stores pixel size in metres (``numericValue`` under
    ``pixelSize/x``); we centralise the unit conversion so callers always work
    in micrometres — the natural unit for users browsing electron microscopy
    sessions.
    """

    if not isinstance(atlas.metadata, dict):
        return None
    pixel_size = find_first(atlas.metadata, "pixelSize")
    if isinstance(pixel_size, dict):
        x_value = pixel_size.get("x")
        if isinstance(x_value, dict):
            value_m = float_or_none(x_value.get("numericValue"))
            if value_m is not None and value_m > 0:
                return value_m * 1e6  # metres → micrometres
    return None


def atlas_acquisition_time(atlas: Atlas) -> str | None:
    if not isinstance(atlas.metadata, dict):
        return None
    when = find_first(atlas.metadata, "acquisitionDateTime")
    if isinstance(when, str) and when:
        # Same wall-clock formatting as the context panel; the raw ISO string
        # (``2026-03-19T12:18:58.3951038+11:00``) was shown verbatim here.
        return format_timestamp(when)
    return None
