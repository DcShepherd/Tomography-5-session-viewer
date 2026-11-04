"""Compact facts from the same version-explicit Summary population."""
from dataclasses import dataclass

from tomography_session_browser.presentation.dashboard.summary import ProcessingSummary
from tomography_session_browser.presentation.volume_formatting import file_size
from tomography_session_browser.services.tomogram_links import COUNT_LABELS


@dataclass(frozen=True, slots=True)
class SummaryFact:
    label: str
    value: str
    detail: str
    explanation: str


def outcome_facts(summary: ProcessingSummary) -> tuple[SummaryFact, ...]:
    payload = summary.input
    volumes = tuple(row.volume for row in payload.rows if row.volume is not None) if payload.version else tuple(
            volume for row in payload.rows for volume in row.tomogram.volumes)
    grids = {volume.grid for volume in volumes if volume.grid is not None}
    shape = ' × '.join(map(str, next(iter(grids)).shape_xyz)) if len(grids) == 1 else (
        f'{len(grids)} geometries' if grids else 'Unavailable')
    size = file_size(sum(volume.fingerprint.size for volume in volumes)) if volumes else 'No volumes'
    versions = ' · '.join(f'{name} {count}' for name, count in summary.version_counts if name not in ('Even', 'Odd')) or 'None found'
    halves = ' · '.join(f'{name} {count}' for name, count in summary.version_counts if name in ('Even', 'Odd'))
    alignments = tuple(dict.fromkeys(row.tomogram.processing.alignment for row in payload.rows
                                    if row.tomogram.processing.alignment != 'Not recorded'))
    alignment = alignments[0] if len(alignments) == 1 else f'{len(alignments)} output types' if alignments else 'Not recorded'
    links = ' · '.join(f'{COUNT_LABELS[step]} {count}' for step, count in summary.link_counts if count) or 'Unresolved'
    coverage = f'{summary.usable_count} usable · {size}' if summary.usable_count else f'No usable volumes · {size}'
    return (
        SummaryFact('Versions', versions, 'Half sets: ' + halves if halves else '',
                    'Full reconstructions available in this selection. Even and Odd are half sets; they are not review versions.'),
        SummaryFact('Volume', shape, coverage,
                    'Declared physical voxel dimensions and file sizes from the selected version only, or all volumes when no version is selected. Missing versions contribute no volume figures.'),
        SummaryFact('Alignment', alignment, f'Output for {summary.alignment_count} of {summary.tomogram_count}' if summary.alignment_count else '',
                    'Recorded alignment output across all tomograms, independent of version. Import into this reconstruction is unverified; this application does not measure alignment quality.'),
        SummaryFact('Parent link', links, 'Across all tomograms',
                    'Verified acquisition relationships across all tomograms, independent of version. A name-only proposal is not a confirmed parent link.'),
    )
