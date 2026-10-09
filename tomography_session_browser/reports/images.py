"""Images report components."""
from __future__ import annotations
from pathlib import Path
from typing import Sequence
from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.domain.models import Atlas, Overview, SearchMap
from tomography_session_browser.reports import graphics
from tomography_session_browser.services.image_geometry import image_size_for_markers
from tomography_session_browser.services.markers.shapes import project_marker_to_size
from tomography_session_browser.reports.constants import EMBEDDABLE_IMAGE_SUFFIXES, LOGGER


def note_overlay_failure(generation_warnings: list[str], figure: str, exc: Exception) -> None:
    """Say so when a figure prints without overlays because markers failed.

    The figure is still useful without them, but a reader must not take an
    unmarked image for one with nothing to mark.
    """

    LOGGER.warning("Overlay markers for %s could not be computed", figure, exc_info=True)
    generation_warnings.append(
        f"Overlay markers for {figure} could not be computed ({exc}); "
        "the figure is shown without overlays."
    )


def resolve_overlay_geometry(
    source: Atlas | Overview | SearchMap,
    markers: Sequence[ImageMarker],
    embed_path: Path,
    generation_warnings: list[str] | None = None,
) -> tuple[tuple[int, int], list[ImageMarker]]:
    """Compute ``(native_size, projected_markers)`` for an overlay panel.

    Mirrors what the GUI's ``_project_marker`` does at render time:
    use the embedded image's actual pixel dimensions as the canvas and
    project every marker from its ``metadata['image_size']`` space
    into that canvas. Markers and image therefore share one
    coordinate system before the OverlayedImage flowable scales them
    by ``draw_size / native_size``.

    Falls back gracefully when Pillow can't read the JPG: we use
    ``image_size_for_markers`` (XML > MRC > cached) as the canvas
    and skip the projection step (markers already live in that
    coordinate system, so projection would be a no-op).
    """

    jpg_size = graphics.native_image_size(embed_path)
    if jpg_size is not None and jpg_size[0] > 0 and jpg_size[1] > 0:
        projected = [project_marker_to_size(m, jpg_size) for m in markers]
        return jpg_size, projected
    if generation_warnings is not None:
        generation_warnings.append(
            f"Could not read embedded raster image size for {embed_path}; using metadata dimensions for PDF overlays."
        )
    fallback = image_size_for_markers(source) or (1000, 1000)
    if fallback[0] <= 0 or fallback[1] <= 0:
        fallback = (1000, 1000)
    return fallback, list(markers)


def embeddable_image_path(path: Path | None) -> Path | None:
    """Return ``path`` (or a sibling jpg/png) if it can be embedded.

    Returns ``None`` when neither the original path nor any sibling
    rasterised version exists. Reportlab can only embed jpg/png/gif
    natively; mrc files require the Qt-bound preview pipeline so we
    skip them rather than attempt rendering inside the report.
    """

    if path is None:
        return None
    p = Path(path)
    if not p.exists():
        # Try a sibling rasterised version (Tomo5 sometimes stores
        # both ``Atlas_1.mrc`` and ``Atlas_1.jpg``).
        for suffix in EMBEDDABLE_IMAGE_SUFFIXES:
            sibling = p.with_suffix(suffix)
            if sibling.exists():
                return sibling
        return None
    if p.suffix.lower() in EMBEDDABLE_IMAGE_SUFFIXES:
        return p
    for suffix in EMBEDDABLE_IMAGE_SUFFIXES:
        sibling = p.with_suffix(suffix)
        if sibling.exists():
            return sibling
    return None


def report_image_markers(markers: Sequence[ImageMarker]) -> list[ImageMarker]:
    """Omit the reference template rectangle, preserving scientific overlays."""
    return [marker for marker in markers if marker.marker_type != MarkerType.TEMPLATE_AREA]

