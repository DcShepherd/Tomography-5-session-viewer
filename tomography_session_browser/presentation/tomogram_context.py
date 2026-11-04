"""One Qt-free Context model for displayed values, evidence and copied text."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from tomography_session_browser.domain.identity import entity_key_text
from tomography_session_browser.domain.models import TiltSeries
from tomography_session_browser.domain.recorded_metrics import RecordedMetric
from tomography_session_browser.domain.tomograms import ProcessingSource, Tomogram, Volume
from tomography_session_browser.presentation.recorded_metrics import metric_number
from tomography_session_browser.presentation.volume_formatting import file_size, mode_text, triple
from tomography_session_browser.services.tomogram_links import TomogramLink
from tomography_session_browser.services.volume.render import calibrated_spacing

ValueState = Literal['recorded', 'not_recorded', 'unresolved', 'absent', 'ambiguous']


@dataclass(frozen=True, slots=True)
class ValueEvidence:
    definition: str
    value: str
    unit: str
    source: str
    field: str
    qualifier: str

    def to_text(self) -> str:
        return '\n'.join((f'{self.value} {self.unit}'.rstrip(), f'Definition: {self.definition}',
                          f'Source: {self.source}', f'Field: {self.field}', self.qualifier))


@dataclass(frozen=True, slots=True)
class ContextValue:
    label: str
    value: str
    unit: str = ''
    qualifier: str = ''
    evidence: tuple[ValueEvidence, ...] = ()
    state: ValueState = 'recorded'

    @property
    def display(self) -> str:
        return f'{self.value} {self.unit}'.rstrip()

    @property
    def detail(self) -> str:
        return '\n'.join(part for part in (self.label + ': ' + self.display, self.qualifier,
                                          *(evidence.to_text() for evidence in self.evidence)) if part)


@dataclass(frozen=True, slots=True)
class ValueTile:
    label: str
    values: tuple[ContextValue, ...]


@dataclass(frozen=True, slots=True)
class ContextFile:
    label: str
    path: Path


@dataclass(frozen=True, slots=True)
class ParentContext:
    label: str
    explanation: str
    target_key: str | None = None


@dataclass(frozen=True, slots=True)
class TomogramContext:
    row_key: str
    name: str
    version: str
    parent: ParentContext
    tiles: tuple[ValueTile, ...]
    volume: tuple[ContextValue, ...]
    files: tuple[ContextFile, ...]
    alignment: str
    other_values: tuple[ContextValue, ...]
    header: tuple[ContextValue, ...]
    warnings: tuple[str, ...]

    def to_text(self) -> str:
        lines = [f'Tomogram: {self.name}', f'Version: {self.version}', 'Parent:',
                 f'  {self.parent.label}', f'  {self.parent.explanation}', 'Recorded values:']
        for tile in self.tiles:
            lines += [f'  {tile.label} · {value.label}: {value.display}' for value in tile.values]
        lines += ['Volume:', *(f'  {value.label}: {value.display}' for value in self.volume),
                  'Files:', *(f'  {file.label}: {file.path}' for file in self.files),
                  'How each value was found:', self.alignment]
        for value in (*[value for tile in self.tiles for value in tile.values], *self.other_values):
            lines.append(value.detail)
        lines += ['Header geometry:', *(value.detail for value in self.header)]
        if self.warnings:
            lines += ['Warnings:', *self.warnings]
        return '\n'.join(lines)


def source_path(source: ProcessingSource | None, recorded: str) -> Path:
    path = Path(recorded)
    return source.path / path if source is not None and not path.is_absolute() else path


def metric_value(metric: RecordedMetric, source: ProcessingSource | None, *, label: str | None = None,
                 factor: float = 1., unit: str | None = None) -> ContextValue:
    evidence = ValueEvidence(metric.definition, f'{metric.value:g}', metric.unit,
                             str(source_path(source, metric.source)), metric.field, metric.qualification)
    return ContextValue(label or metric.label, metric_number(metric, factor), unit or metric.unit,
                        metric.qualification, (evidence,))


def metric_tile(item: Tomogram, source: ProcessingSource | None, label: str,
                definitions: tuple[tuple[str, str], ...]) -> ValueTile:
    values = []
    for definition, name in definitions:
        metrics = tuple(metric for metric in item.processing.metrics if metric.definition == definition)
        if len(metrics) == 1:
            metric = metrics[0]
            convert = definition == 'warp.ctf_specimen_thickness' and metric.unit == 'Å'
            values.append(metric_value(metric, source, label=name, factor=.1 if convert else 1., unit='nm' if convert else None))
        elif metrics:
            values.append(ContextValue(name, 'Ambiguous recorded values',
                evidence=tuple(metric_value(metric, source).evidence[0] for metric in metrics), state='ambiguous'))
    return ValueTile(label, tuple(values) or (ContextValue(label, 'Not recorded', state='not_recorded'),))


def tomogram_context(item: Tomogram, volume: Volume | None, link: TomogramLink | None,
                     override: float | None = None, *, source: ProcessingSource | None = None,
                     parents: tuple[TiltSeries, ...] = ()) -> TomogramContext:
    # A stale widget must not lend another row's volume or calibration.
    if source is not None and source.id != item.source_id:
        source = None
    if volume is not None and not any(candidate == volume for candidate in item.volumes):
        volume, override = None, None
    matches = tuple(parent for parent in parents if link is not None and link.status == 'resolved'
                    and entity_key_text(parent) == link.target_key)
    parent = ParentContext(matches[0].name, link.explanation, link.target_key) if len(matches) == 1 and link else ParentContext(
        'Ambiguous' if link and link.status == 'ambiguous' else 'Unresolved',
        link.explanation + (' The recorded parent is not uniquely available in the loaded project.'
                            if link.status == 'resolved' else '') if link else 'No parent link has been recorded.')
    specs = (
        ('Thickness', (('warp.ctf_specimen_thickness', 'Warp CTF estimate'), ('imod.tomopitch.specimen_thickness', 'IMOD positioning'))),
        ('Residual', (('imod.tiltalign.mean', 'Mean residual'),)),
        ('CTF fit', (('warp.ctf_resolution', 'CTF fit'),)),
        ('Defocus', (('warp.ctf.defocus', 'Defocus'),)),
        ('Astigmatism', (('warp.ctf.defocus_delta', 'Astigmatism delta'),)),
    )
    tiles = [metric_tile(item, source, label, definitions) for label, definitions in specs]
    fields: list[ContextValue] = []
    header: list[ContextValue] = []
    files: list[ContextFile] = []
    if volume is None:
        voxel = ContextValue('Voxel', 'Unavailable', qualifier='No reconstruction selected.', state='absent')
        fields.append(ContextValue('Volume', 'Absent', state='absent'))
    else:
        files += [ContextFile('Folder', volume.path.parent), ContextFile('Volume', volume.path)]
        grid = volume.grid
        spacing = calibrated_spacing(volume, override)
        if spacing is None:
            voxel = ContextValue('Voxel', 'Unresolved', qualifier='Voxel navigation only; no resolved physical calibration.', state='unresolved')
        else:
            value = f'{spacing[0]:g}' if len(set(spacing)) == 1 else ' × '.join(f'{value:g}' for value in spacing)
            evidence = ValueEvidence('volume.effective_spacing', value, 'Å', str(volume.path),
                'User-confirmed spacing' if override is not None else 'MRC header spacing X, Y, Z',
                'Display-only override for this version; source unchanged.' if override is not None else 'Resolved header calibration.')
            voxel = ContextValue('Voxel', value, 'Å', evidence.qualifier, (evidence,))
        fields += [ContextValue('Size', ' × '.join(map(str, grid.shape_xyz)), qualifier='Physical X × Y × Z voxel dimensions.') if grid else
                   ContextValue('Size', 'Unresolved', qualifier='Volume header geometry is unavailable.', state='unresolved'),
                   ContextValue('Mode', mode_text(volume.dtype)), ContextValue('On disk', file_size(volume.fingerprint.size))]
        if grid is not None:
            for label, value, unit, field in (
                ('Axes', triple(grid.axes_crs), '', 'Column, row, section mapping'),
                ('Origin', triple(grid.origin_xyz), 'Å', 'MRC origin X, Y, Z'),
                ('Crop starts', triple(grid.starts_crs), '', 'MRC starts column, row, section'),
                ('Header spacing', ', '.join('Not recorded' if value is None else f'{value:g}' for value in grid.spacing_xyz), 'Å', 'MRC voxel size X, Y, Z')):
                header.append(ContextValue(label, value, unit, evidence=(ValueEvidence('volume.header', value, unit,
                                            str(volume.path), field, 'Recorded header geometry; no inferred registration.'),)))
        else:
            header.append(ContextValue('Header geometry', 'Unresolved', state='unresolved'))
    tiles.append(ValueTile('Voxel', (voxel,)))
    primary = {definition for _, definitions in specs for definition, _ in definitions}
    other = tuple(metric_value(metric, source) for metric in item.processing.metrics if metric.definition not in primary)
    for metric in item.processing.metrics:
        file = ContextFile('Thickness' if 'thickness' in metric.definition else 'Recorded metrics', source_path(source, metric.source))
        if not any(existing.path == file.path for existing in files):
            files.append(file)
    for line in item.processing.alignment.splitlines():
        if line.startswith('Alignment log: '):
            file = ContextFile('Alignment', source_path(source, line.removeprefix('Alignment log: ')))
            if not any(existing.path == file.path for existing in files):
                files.append(file)
    warnings = tuple(dict.fromkeys((*item.warnings, *item.processing.warnings, *(link.warnings if link else ()),
                                   *(volume.warnings if volume else ()))))
    return TomogramContext(item.id, item.name, volume.version if volume else 'Absent', parent, tuple(tiles),
                           tuple(fields), tuple(files), item.processing.alignment, other, tuple(header), warnings)
