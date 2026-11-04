"""Processing metrics keep one canonical review-order vector, including gaps."""
from __future__ import annotations

from tomography_session_browser.presentation.dashboard.index_chart import IndexChartModel, IndexSeries, chart_model
from tomography_session_browser.presentation.dashboard.processing_volumes import slab_reference
from tomography_session_browser.presentation.tomogram_tabs import TomogramRow


def processing_charts(rows: tuple[TomogramRow, ...]) -> tuple[IndexChartModel, ...]:
    keys = tuple(row.tomogram.id for row in rows)
    names = tuple(row.tomogram.name for row in rows)
    definitions = (
        ("thickness", "Specimen thickness", "nm", "Recorded estimates from each program. The slab is reconstructed physical Z extent.", (
            ("warp.ctf_specimen_thickness", "Warp CTF estimate", "Å", .1, "dot"),
            ("imod.tomopitch.specimen_thickness", "IMOD positioning", "nm", 1., "diamond"))),
        ("residual", "Alignment residual", "nm", "IMOD mean fiducial residual; import into this reconstruction is unverified. Runs can use different settings.", (
            ("imod.tiltalign.mean", "IMOD mean", "nm", 1., "dot"),)),
        ("ctf", "CTF fit", "Å", "Recorded Warp CTF resolution estimate; smaller is finer.", (
            ("warp.ctf_resolution", "Warp", "Å", 1., "dot"),)),
        ("defocus", "Defocus", "µm", "Recorded Warp defocus; positive means underfocus. The recorded sign is retained.", (
            ("warp.ctf.defocus", "Warp", "µm", 1., "dot"),)),
    )
    charts = []
    tones = {"thickness": ("violet", "blue"), "residual": ("green",), "ctf": ("blue",), "defocus": ("violet",)}
    for key, title, unit, description, specs in definitions:
        series = []
        omitted_pixels = 0
        for series_index, (definition, label, raw_unit, factor, shape) in enumerate(specs):
            values, missing = [], []
            for row in rows:
                candidates = [metric for metric in row.tomogram.processing.metrics if metric.definition == definition]
                metric = candidates[0] if len(candidates) == 1 else None
                if metric is not None and metric.unit == raw_unit:
                    values.append(metric.value * factor)
                    missing.append("")
                else:
                    values.append(None)
                    missing.append("Ambiguous recorded values" if len(candidates) > 1 else
                                   f"Recorded in {metric.unit}, not plotted" if metric else "Not recorded")
                    if metric is not None and metric.unit == "pixels":
                        omitted_pixels += 1
            if any(value is not None for value in values):
                series.append(IndexSeries(definition, label, unit, tuple(values), tuple(missing), shape, tone=tones[key][series_index]))
        if series:
            if omitted_pixels:
                description += f" {omitted_pixels} in pixels, not plotted; see All recorded values."
            slabs = tuple(slab_reference(row.tomogram) for row in rows) if key == "thickness" else ()
            charts.append(chart_model(key, title, unit, description, keys, names, tuple(series), slabs=slabs))
    return tuple(charts)
