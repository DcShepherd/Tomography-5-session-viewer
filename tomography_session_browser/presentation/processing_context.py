"""Source inventory and selection coverage from the shared Summary payload."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from tomography_session_browser.domain.tomograms import ProcessingSource
from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.presentation.dashboard.tomogram_summary_input import TomogramSummaryInput
from tomography_session_browser.presentation.tomogram_context import ContextFile
from tomography_session_browser.services.tomogram_links import COUNT_LABELS, link_step_counts


@dataclass(frozen=True, slots=True)
class SourceReadTimes:
    entries: tuple[tuple[ProcessingSource, datetime], ...] = ()

    def admitted(self, sources: tuple[ProcessingSource, ...], now: datetime) -> SourceReadTimes:
        return SourceReadTimes(tuple((source, next((at for previous, at in self.entries if previous is source), now))
                                     for source in sources))

    def read_at(self, source: ProcessingSource) -> datetime | None:
        return next((at for previous, at in self.entries if previous is source), None)


@dataclass(frozen=True, slots=True)
class SourceContext:
    source: ProcessingSource
    read_at: datetime | None
    versions: tuple[ContextFile, ...]

    @property
    def empty_note(self) -> str:
        return '' if self.versions else 'No reconstruction versions are loaded. Refresh to read this source again.'


@dataclass(frozen=True, slots=True)
class ProcessingContext:
    key: str
    title: str
    count: int
    version: str | None
    missing: int
    sources: tuple[SourceContext, ...]
    links: tuple[tuple[str, int], ...]
    notice: str
    warnings: tuple[str, ...]

    def to_text(self) -> str:
        lines = [self.title, f'Tomograms: {self.count}', f'Version: {self.version or "All review versions"}']
        if self.missing:
            lines.append(f'{self.version} missing for {self.missing} of {count_phrase(self.count, "tomogram")}.')
        if self.notice:
            lines.append(self.notice)
        for value in self.sources:
            lines += [f'Processing folder: {value.source.path}', f'Pipeline: {value.source.pipeline}',
                      f'Read into app: {read_time_text(value.read_at)}', 'Source version folders:',
                      *(f'  {file.label}: {file.path}' for file in value.versions)]
            if value.empty_note:
                lines.append(value.empty_note)
        if self.links:
            lines += ['Parent links:', *(f'  {label}: {count}' for label, count in self.links)]
        if self.warnings:
            lines += ['Warnings:', *self.warnings]
        return '\n'.join(lines)


def read_time_text(value: datetime | None) -> str:
    return value.isoformat(sep=' ', timespec='seconds') + ' · read-only' if value is not None else 'Not recorded'


def processing_context(payload: TomogramSummaryInput, read_times: SourceReadTimes = SourceReadTimes()) -> ProcessingContext:
    sources = []
    for source in payload.sources:
        # Inventory lists every observed source-version folder, not viewer fallbacks.
        folders = tuple(dict.fromkeys((volume.version, volume.path.parent) for item in source.tomograms for volume in item.volumes))
        sources.append(SourceContext(source, read_times.read_at(source), tuple(ContextFile(version, path) for version, path in folders)))
    links = tuple((COUNT_LABELS[step], count) for step, count in link_step_counts(row.link for row in payload.rows) if count)
    return ProcessingContext(payload.key, payload.title, len(payload.rows), payload.version,
        sum(row.volume is None for row in payload.rows) if payload.version else 0, tuple(sources), links, payload.notice,
        tuple(dict.fromkeys(warning for source in payload.sources for warning in source.warnings)))
