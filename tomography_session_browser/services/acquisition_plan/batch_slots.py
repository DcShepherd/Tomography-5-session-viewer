"""Independently recorded exposure-to-series coverage."""
from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.domain.models import BatchPosition
from tomography_session_browser.parsers.xml_parser import find_first
from tomography_session_browser.services.navigation.exposures import explicit_tilt_ids


def recorded_slot_tilt_ids(batch: BatchPosition) -> frozenset[str] | None:
    main = batch.metadata.get("ExposureTemplateAreaParameters")
    additional = find_first(batch.metadata.get("AdditionalExposureTemplateAreas"), "ExposureTemplateAreaParameters")
    areas = [main, *(additional if isinstance(additional, list) else [additional])]
    ids: set[str] = set()
    for area in areas:
        if not isinstance(area, dict):
            continue
        attributes = area.get("_attributes")
        if isinstance(attributes, dict) and attributes.get("nil") == "true":
            continue
        marker = ImageMarker("", MarkerType.EXPOSURE_AREA, batch.id, batch.id, metadata={"raw": area})
        links = explicit_tilt_ids(marker)
        if len(links) != 1 or links[0] in ids:
            return None
        ids.add(links[0])
    return frozenset(ids)
