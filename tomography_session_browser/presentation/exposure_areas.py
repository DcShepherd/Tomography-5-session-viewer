"""Exposure choices share the canvas labels and retain their image context."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.services.batch_label_service import exposure_batch_label
from tomography_session_browser.services.markers.shapes import marker_intersects_image
from tomography_session_browser.services.navigation.exposures import coerce_exposure_index


def exposure_area_title(marker: ImageMarker, fallback_number: int = 1) -> str:
    index = coerce_exposure_index(marker.metadata.get("exposure_index"))
    label = exposure_batch_label(marker) or str(index + 1 if index is not None else fallback_number)
    name = str(marker.metadata.get("area_name") or "").strip()
    return f"Exposure area {label}" + (f" ({name})" if name else "")


@dataclass(frozen=True, slots=True)
class ExposureArea:
    label: str
    marker_id: str
    title: str
    status: str
    #: None means the source did not record image dimensions.
    on_image: bool | None

    @property
    def image_note(self) -> str:
        if self.on_image is None:
            return "image position unavailable"
        return "on this image" if self.on_image else "not on this image"


def exposure_areas(markers: Iterable[ImageMarker]) -> list[ExposureArea]:
    areas = [marker for marker in markers if marker.marker_type == MarkerType.EXPOSURE_AREA]
    indexed = sorted(
        ((coerce_exposure_index(marker.metadata.get("exposure_index")), position, marker)
         for position, marker in enumerate(areas)),
        key=lambda item: (item[0] if item[0] is not None else item[1], item[1]),
    )
    choices = []
    for index, position, marker in indexed:
        number = (index if index is not None else position) + 1
        size = marker.metadata.get("image_size")
        on_image = (
            marker_intersects_image((int(size[0]), int(size[1])), marker)
            if isinstance(size, tuple) and len(size) == 2
            and all(isinstance(value, int | float) and value > 0 for value in size)
            else None
        )
        choices.append(ExposureArea(
            exposure_batch_label(marker) or str(number), marker.id,
            exposure_area_title(marker, number), marker.status or "Status not recorded", on_image,
        ))
    return choices
