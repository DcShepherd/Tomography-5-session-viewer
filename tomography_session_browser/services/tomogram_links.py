"""A–D evidence ladder. Only absent/complete-no-match can try a weaker step."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from tomography_session_browser.domain.acquisition import Evidence, EvidenceMatch, LinkStep, MatchState, movie_name
from tomography_session_browser.domain.models import TiltSeries
from tomography_session_browser.domain.tomograms import Tomogram
from tomography_session_browser.domain.link_confirmations import LinkConfirmation, Decision
from tomography_session_browser.services.name_links import candidates, decision_for, proposal_signature
from tomography_session_browser.services.acquisition_index import AcquisitionIndex, build_acquisition_index, recorded_path_key

STEP_LABELS: dict[LinkStep, str] = {'A': 'recorded acquisition file path', 'B': 'movie file names',
                                  'C': 'MDOC times and angles', 'D': 'verified identical tilt stack',
                                  'E': 'name, confirmed by you'}
COUNT_LABELS: dict[LinkStep, str] = {'A': 'Recorded paths', 'B': 'Movie names',
                                   'C': 'MDOC records', 'D': 'Identical stacks', 'E': 'Confirmed names'}


@dataclass(frozen=True, slots=True)
class TomogramLink:
    status: Literal["resolved", "unresolved", "ambiguous"]
    target_key: str | None = None
    candidates: tuple[str, ...] = ()
    explanation: str = 'No complete acquisition evidence matches a loaded tilt series.'
    step: LinkStep | None = None
    evidence_state: MatchState = 'absent'
    warnings: tuple[str, ...] = ()
    proposal_signature: str = ''
    decision: Decision | None = None


def _failed(blocks: Iterable[Evidence]) -> EvidenceMatch | None:
    issues = [b.issue or 'Incomplete acquisition metadata.' for b in blocks if b.state == 'incomplete']
    return EvidenceMatch('incomplete', issue='; '.join(dict.fromkeys(issues))) if issues else None


def _matched(groups: list[set[str]]) -> EvidenceMatch:
    if not groups:
        return EvidenceMatch('absent')
    keys = tuple(sorted(set().union(*groups)))
    if len(keys) > 1 or (keys and any(not group for group in groups)):
        return EvidenceMatch('ambiguous', keys, 'Conflicting acquisition files do not identify one verified series.')
    return EvidenceMatch('unique' if keys else 'complete-no-match', keys)


def _paths(item: Tomogram, index: AcquisitionIndex) -> EvidenceMatch:
    failed = _failed(item.acquisition.movies)
    if failed:
        return failed
    paths = {recorded_path_key(path, Path.cwd()) for b in item.acquisition.movies
             if b.state == 'complete' for path in (b.value or ())}
    if not paths:
        return EvidenceMatch('absent')
    # Preserve A exactly: any recorded full path may prove the existing relationship.
    keys = tuple(sorted({key for path in paths for key in index.paths.get(path, ())}))
    return EvidenceMatch('ambiguous' if len(keys) > 1 else ('unique' if keys else 'complete-no-match'), keys)


def _movies(item: Tomogram, index: AcquisitionIndex) -> EvidenceMatch:
    failed = _failed(item.acquisition.movies)
    if failed:
        return failed
    groups = []
    for block in item.acquisition.movies:
        if block.state != 'complete' or not block.value:
            continue
        names = {movie_name(path) for path in block.value}
        if len(names) != len(block.value):
            return EvidenceMatch('incomplete', issue='Duplicate movie file names cannot establish step B.')
        if len(names) >= 3:
            groups.append(set.intersection(*(set(index.movies.get(name, ())) for name in names)))
    return _matched(groups) if groups else EvidenceMatch('complete-no-match' if any(
        b.state == 'complete' and b.value for b in item.acquisition.movies) else 'absent')


def _records(item: Tomogram, index: AcquisitionIndex) -> EvidenceMatch:
    failed = _failed(item.acquisition.mdocs)
    if failed:
        return failed
    blocks = [b.value for b in item.acquisition.mdocs if b.state == 'complete' and b.value is not None]
    result = _matched([set(index.records.get(b.records, ())) for b in blocks])
    if result.state == 'unique':
        target = index.by_key[result.candidates[0]]
        if any(b.image_file and target.image_file and movie_name(b.image_file) != movie_name(target.image_file) for b in blocks):
            result = EvidenceMatch(result.state, result.candidates,
                warnings=('MDOC ImageFile names differ; linkage uses the complete matching times and angles.',))
    return result


def resolve_tomogram_link(item: Tomogram, series: Iterable[TiltSeries] | AcquisitionIndex,
                         confirmations: tuple[LinkConfirmation, ...] = ()) -> TomogramLink:
    index = series if isinstance(series, AcquisitionIndex) else build_acquisition_index(series, (item,))
    steps: tuple[tuple[LinkStep, Callable[[Tomogram, AcquisitionIndex], EvidenceMatch]], ...] = (
        ('A', _paths), ('B', _movies), ('C', _records))
    for step, match in steps:
        outcome = match(item, index)
        if outcome.state not in ('absent', 'complete-no-match'):
            return _link(step, outcome)
    failed = _failed(item.acquisition.stacks)
    if failed:
        return _link('D', failed)
    if any(b.state == 'complete' for b in item.acquisition.stacks):
        outcome = index.stack_results.get(item.id, EvidenceMatch('incomplete',
            issue='Full tilt-stack verification is pending. The tomogram remains available without a parent.'))
        if outcome.state not in ('absent', 'complete-no-match'):
            return _link('D', outcome)
    found = candidates(item, index)
    if not found:
        return TomogramLink('unresolved')
    keys = tuple(sorted(value.key for value in found))
    if len(found) != 1:
        return TomogramLink('ambiguous', candidates=keys, step='E', evidence_state='ambiguous',
                            explanation='The name matches several loaded tilt series; no parent is selected.')
    target = found[0]
    signature = proposal_signature(item, target)
    record = decision_for(item, target, signature, confirmations)
    confirmed = record is not None and record.decision == 'confirmed'
    return TomogramLink('resolved' if confirmed else 'unresolved', target.key if confirmed else None,
        keys, 'Linked by name, confirmed by you.' if confirmed else 'Name match, unconfirmed. Review the proposed parent before linking.',
        'E', 'unique', proposal_signature=signature, decision=record.decision if record else None)


def _link(step: LinkStep, outcome: EvidenceMatch) -> TomogramLink:
    if outcome.state == 'unique':
        return TomogramLink('resolved', outcome.candidates[0], outcome.candidates,
            f'Linked by {STEP_LABELS[step]}.', step, outcome.state, outcome.warnings)
    if outcome.state == 'ambiguous':
        return TomogramLink('ambiguous', candidates=outcome.candidates, step=step,
            evidence_state=outcome.state, explanation=outcome.issue or
            f'{STEP_LABELS[step].capitalize()} match several loaded tilt series; no parent is selected.')
    return TomogramLink('unresolved', step=step, evidence_state=outcome.state,
        explanation=outcome.issue or 'No complete acquisition evidence matches a loaded tilt series.',
        warnings=(outcome.issue,) if outcome.issue else ())


def link_step_counts(links: Iterable[TomogramLink]) -> tuple[tuple[LinkStep, int], ...]:
    values = tuple(links)
    return tuple((step, sum(link.status == 'resolved' and link.step == step for link in values)) for step in STEP_LABELS)
