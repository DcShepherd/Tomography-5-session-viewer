"""Display recorded values without pooling different definitions or weightings."""
from dataclasses import dataclass

from tomography_session_browser.domain.recorded_metrics import RecordedMetric
from tomography_session_browser.domain.tomograms import Tomogram

#: Warp writes CTF defocus to about 16 digits; displays show two decimals (10 nm).
TWO_DECIMALS = frozenset({"warp.ctf.defocus", "warp.ctf.defocus_delta"})


def value_text(definition: str, value: float) -> str:
    """A displayed value of this definition: defocus and astigmatism to two decimals, others as recorded.

    Evidence keeps the recorded value.
    """
    return f"{round(value, 2) + 0.:.2f}" if definition in TWO_DECIMALS else f"{value:g}"  # + 0. drops -0.00.


def metric_number(metric: RecordedMetric, factor: float = 1.) -> str:
    return value_text(metric.definition, metric.value * factor)


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
        candidates = [metric for metric in evidence.metrics if metric.definition == definition]
        metric = candidates[0] if len(candidates) == 1 else None
        values.append(f"{metric_number(metric)} {metric.unit}" if metric else "Ambiguous recorded values" if candidates else "Not recorded")
        numbers.append((metric.unit, metric.value) if metric else None)
    lines = [f"Alignment evidence: {evidence.alignment}"]
    for metric in evidence.metrics:
        lines += [f"{metric.label}: {metric.value:g} {metric.unit}",
                  f"Source: {metric.source} · {metric.field}", metric.qualification]
    lines.extend(evidence.warnings)
    alignment = "IMOD output found" if evidence.alignment != "Not recorded" else "Not recorded"
    return MetricRow(item.id, item.name, (alignment, *values), "\n".join(dict.fromkeys("\n".join(lines).splitlines())),
                     (alignment, *numbers))
