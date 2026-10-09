"""Qt-free scopes shared by the workspace, presenter and widgets."""

from dataclasses import dataclass
from typing import Any

from tomography_session_browser.domain.identity import dedupe_entities
from tomography_session_browser.domain.models import (
    Atlas, BatchPosition, Overview, Sample, SearchMap, SearchTile, TiltSeries,
)


@dataclass(slots=True)
class EntityGroup:
    label: str
    values: list[Any]


@dataclass(slots=True)
class LinkedSampleGroup:
    label: str
    samples: list[Sample]
    atlas: Atlas | None
    overviews: list[Overview]
    search_maps: list[SearchMap]
    search_tiles: list[SearchTile]
    batch_positions: list[BatchPosition]
    tilt_series: list[TiltSeries]


def build_linked_sample_group(label: str, samples: list[Sample]) -> LinkedSampleGroup:
    atlas = next((sample.atlas for sample in samples if sample.atlas is not None), None)
    return LinkedSampleGroup(
        label=label,
        samples=samples,
        atlas=atlas,
        overviews=dedupe_entities(value for sample in samples for value in sample.overviews),
        search_maps=dedupe_entities(value for sample in samples for value in sample.search_maps),
        search_tiles=dedupe_entities(value for sample in samples for value in sample.search_tiles),
        batch_positions=dedupe_entities(value for sample in samples for value in sample.batch_positions),
        tilt_series=dedupe_entities(value for sample in samples for value in sample.tilt_series),
    )
