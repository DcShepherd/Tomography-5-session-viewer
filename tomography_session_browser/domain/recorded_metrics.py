"""Recorded processing evidence, independent of rendering and acquisition links."""
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RecordedMetric:
    definition: str
    label: str
    value: float
    unit: str
    source: str
    field: str
    qualification: str


@dataclass(frozen=True, slots=True)
class ProcessingEvidence:
    alignment: str = "Not recorded"
    metrics: tuple[RecordedMetric, ...] = ()
    warnings: tuple[str, ...] = ()
