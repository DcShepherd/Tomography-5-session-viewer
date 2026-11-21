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
    #: The full statement for one tomogram, as the screen shows it.
    qualification: str
    #: The statements that hold for every tomogram with this definition and
    #: source, in order; the PDF says each one once. Empty: ``qualification``
    #: is the one statement.
    caveats: tuple[str, ...] = ()
    #: This tomogram's own numbers behind the value; the PDF keeps them in its metadata.
    detail: str = ""
    #: ``source`` with the series name as ``<series>``; empty when the path holds no series name.
    pattern: str = ""


@dataclass(frozen=True, slots=True)
class ProcessingEvidence:
    alignment: str = "Not recorded"
    metrics: tuple[RecordedMetric, ...] = ()
    warnings: tuple[str, ...] = ()
