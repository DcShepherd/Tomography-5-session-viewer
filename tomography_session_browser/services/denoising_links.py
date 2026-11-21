"""Which loaded reconstruction a cryoCARE denoised volume came from (batch 6).

cryoCARE's ``predict_config_<ts>.json`` names the even and odd half sets it
denoised (``domain.denoising.DenoisingInput``). A loaded tomogram that holds
both files, as its Even and Odd volumes, is the input. Exactly one such
tomogram links; two or more are a tie and nothing is chosen. Names are never
compared. This reads no files.

A linked denoised row shows the input row's recorded values (its own and any
alignment job's), each qualified with the chain. A value recorded in pixels is
lent only when both volumes have the same grid and spacing.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from tomography_session_browser.domain.identity import path_identity_key
from tomography_session_browser.domain.recorded_metrics import ProcessingEvidence, RecordedMetric
from tomography_session_browser.domain.tomograms import ProcessingSource, Tomogram

#: The chain, said once in the PDF; each row's input is in its detail.
DENOISED = 'From the input reconstruction, whose even and odd half sets cryoCARE denoised into this volume.'


@dataclass(frozen=True, slots=True)
class DenoisingLink:
    status: Literal['linked', 'not_loaded', 'tie']
    #: One sentence for the context panel: where the volume came from.
    explanation: str
    #: The input tomogram and its source; None unless linked.
    tomogram_id: str | None = None
    source_id: str | None = None


def _halves(tomogram: Tomogram) -> tuple[str, str] | None:
    found = {volume.version: path_identity_key(volume.path) for volume in tomogram.volumes
             if volume.version in ('Even', 'Odd')}
    return (found['Even'], found['Odd']) if len(found) == 2 else None


def link_denoising(sources: Iterable[ProcessingSource]) -> Mapping[str, DenoisingLink]:
    """One outcome for each tomogram whose denoising input was recorded."""
    loaded = tuple(sources)
    holders: dict[tuple[str, str], list[tuple[ProcessingSource, Tomogram]]] = {}
    for source in loaded:
        for tomogram in source.tomograms:
            if (halves := _halves(tomogram)) is not None:
                holders.setdefault(halves, []).append((source, tomogram))
    links: dict[str, DenoisingLink] = {}
    for source in loaded:
        for tomogram in source.tomograms:
            given = tomogram.denoised_from
            if given is None:
                continue
            found = holders.get((path_identity_key(given.even), path_identity_key(given.odd)), [])
            if len(found) == 1:
                (origin, item), = found
                links[tomogram.id] = DenoisingLink(
                    'linked', f'Denoised by cryoCARE from the even and odd half sets of "{origin.name}" / {item.name} '
                    f'({given.config.name}).', item.id, origin.id)
            elif found:
                links[tomogram.id] = DenoisingLink('tie', 'Several loaded reconstructions hold the half sets that '
                    f'{given.config.name} names: {", ".join(f"{origin.name} / {item.name}" for origin, item in found)}. '
                    'None was chosen.')
            else:
                links[tomogram.id] = DenoisingLink('not_loaded', f'Denoised by cryoCARE from {given.even.name} and '
                    f'{given.odd.name} ({given.config.name}). That reconstruction is not loaded.')
    return MappingProxyType(links)


def _same_grid(first: Tomogram, second: Tomogram) -> bool:
    one, two = (item.volumes[0].grid if item.volumes else None for item in (first, second))
    return (one is not None and two is not None
            and (one.shape_xyz, one.spacing_xyz) == (two.shape_xyz, two.spacing_xyz))


def _lent(metric: RecordedMetric, origin: ProcessingSource, prefix: str, input_name: str) -> RecordedMetric:
    source, pattern = Path(metric.source), Path(metric.pattern) if metric.pattern else None
    absolute = source if source.is_absolute() else origin.path / source
    return replace(metric, source=str(absolute), qualification=f'{prefix} {metric.qualification}',
                   caveats=(DENOISED, *(metric.caveats or (metric.qualification,))),
                   detail=f'From "{origin.name}" / {input_name}. {metric.detail}'.rstrip(),
                   pattern=str(pattern if pattern.is_absolute() else origin.path / pattern) if pattern else '')


def denoised_row(tomogram: Tomogram, origin: ProcessingSource, input_row: Tomogram) -> Tomogram:
    """The denoised row with the input row's recorded values."""
    own = tomogram.processing
    prefix = (f'From "{origin.name}" / {input_row.name}, whose even and odd half sets cryoCARE denoised into '
              'this volume.')
    same = _same_grid(tomogram, input_row)
    metrics, withheld = [], []
    for metric in input_row.processing.metrics:
        if metric.unit == 'pixels' and not same:
            withheld.append(f'{metric.label} of "{origin.name}" / {input_row.name} is recorded in its pixels, and '
                            'this volume has another grid; it is not shown.')
        else:
            metrics.append(_lent(metric, origin, prefix, input_row.name))
    recorded = ProcessingEvidence().alignment
    alignment = own.alignment if own.alignment != recorded else input_row.processing.alignment
    return replace(tomogram, processing=ProcessingEvidence(alignment, (*own.metrics, *metrics),
                                                           (*own.warnings, *withheld)))


def lend_denoising(sources: Iterable[ProcessingSource], links: Mapping[str, DenoisingLink],
                   rows: Mapping[str, Tomogram]) -> Mapping[str, Tomogram]:
    """``rows`` (display copies) plus the denoised rows that are linked or tied.

    The input row is read from ``rows`` when it has a display copy, so values an
    alignment job lent to the reconstruction reach its denoised volume too.
    """
    loaded = tuple(sources)
    by_id = {source.id: source for source in loaded}
    tomograms = {tomogram.id: tomogram for source in loaded for tomogram in source.tomograms}
    result = dict(rows)
    for tomogram_id, link in links.items():
        if link.status == 'not_loaded':
            continue
        row = rows.get(tomogram_id, tomograms[tomogram_id])
        if link.tomogram_id is None or link.source_id is None:  # A tie: the reason, as a warning.
            result[tomogram_id] = replace(row, processing=replace(row.processing,
                                                                  warnings=(*row.processing.warnings, link.explanation)))
        else:
            input_row = rows.get(link.tomogram_id, tomograms[link.tomogram_id])
            result[tomogram_id] = denoised_row(row, by_id[link.source_id], input_row)
    return MappingProxyType(result)
