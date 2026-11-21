"""Display recorded values without pooling different definitions or weightings."""
from dataclasses import dataclass

from tomography_session_browser.domain.recorded_metrics import RecordedMetric
from tomography_session_browser.domain.tomograms import Tomogram

#: Warp writes CTF defocus to about 16 digits; displays show two decimals (10 nm).
#: IMOD alignment residuals show two decimals too ("0.95 nm"), on the screen and in the PDF.
TWO_DECIMALS = frozenset({"warp.ctf.defocus", "warp.ctf.defocus_delta",
                          "imod.tiltalign.mean", "imod.tiltalign.weighted_mean"})


def value_text(definition: str, value: float) -> str:
    """A displayed value of this definition: defocus, astigmatism and residuals to two decimals, others as recorded.

    Evidence keeps the recorded value.
    """
    return f"{round(value, 2) + 0.:.2f}" if definition in TWO_DECIMALS else f"{value:g}"  # + 0. drops -0.00.


def metric_number(metric: RecordedMetric, factor: float = 1.) -> str:
    return value_text(metric.definition, metric.value * factor)


@dataclass(frozen=True, slots=True)
class Ambiguous:
    """A tomogram records more than one value of a definition; no value is chosen."""
    count: int


def single_metric(item: Tomogram, definition: str) -> RecordedMetric | Ambiguous | None:
    """The one recorded metric of ``definition``: ``Ambiguous`` when there are several, ``None`` when none.

    The one rule for the metrics table, the context tiles and the Atlas colouring.
    """
    candidates = [metric for metric in item.processing.metrics if metric.definition == definition]
    if len(candidates) == 1:
        return candidates[0]
    return Ambiguous(len(candidates)) if candidates else None


def display_factor(metric: RecordedMetric) -> tuple[float, str]:
    """The factor and unit a metric is displayed in: Warp specimen thickness in Å shows in nm."""
    if metric.definition == 'warp.ctf_specimen_thickness' and metric.unit == 'Å':
        return .1, 'nm'
    return 1., metric.unit


def display_value(metric: RecordedMetric) -> tuple[str, float]:
    """``(unit, value)`` after the shared display rule."""
    factor, unit = display_factor(metric)
    return unit, metric.value * factor


@dataclass(frozen=True, slots=True)
class MetricRow:
    key: str
    name: str
    values: tuple[str, ...]
    detail: str
    sort_values: tuple[tuple[str, float] | str | None, ...] = ()


METRIC_COLUMNS = (
    ("warp.ctf_specimen_thickness", "Specimen thickness"),
    ("imod.tiltalign.mean", "Mean residual"),
    ("imod.tiltalign.weighted_mean", "Weighted mean residual"),
    ("warp.ctf_resolution", "CTF fit"),
    ("warp.ctf.defocus", "Defocus"),
    ("warp.ctf.defocus_delta", "Astigmatism delta"),
    ("imod.tomopitch.specimen_thickness", "IMOD positioning thickness"),
)


def metric_row(item: Tomogram) -> MetricRow:
    evidence = item.processing
    values, numbers = [], []
    for definition, _ in METRIC_COLUMNS:
        found = single_metric(item, definition)
        metric = found if isinstance(found, RecordedMetric) else None
        values.append(f"{metric_number(metric)} {metric.unit}" if metric else "Ambiguous recorded values" if found else "Not recorded")
        numbers.append((metric.unit, metric.value) if metric else None)
    lines = [f"Alignment evidence: {evidence.alignment}"]
    for metric in evidence.metrics:
        lines += [f"{metric.label}: {metric.value:g} {metric.unit}",
                  f"Source: {metric.source} · {metric.field}", metric.qualification]
    lines.extend(evidence.warnings)
    alignment = "IMOD output found" if evidence.alignment != "Not recorded" else "Not recorded"
    return MetricRow(item.id, item.name, (alignment, *values), "\n".join(dict.fromkeys("\n".join(lines).splitlines())),
                     (alignment, *numbers))
