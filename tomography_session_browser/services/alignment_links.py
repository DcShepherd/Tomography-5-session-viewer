"""Which loaded alignment job proves each Scipion reconstruction's alignment.

A reconstruction job keeps the ``.tlt`` and ``.xf`` it reconstructed with. An
alignment series proves it when the series names match and every printed
angle and transform agrees within the printed precision
(``parsers.imod_transforms.differences``). Exactly one proof links; two or more
are a tie and nothing is chosen. Names alone link only when the user confirms
the one same-name series of a reconstruction whose own angles and transforms
cannot be compared; the saved decision holds while its signature (the files
on both sides) stays the same. This reads no files: everything was read at
intake.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
import hashlib
import json
from types import MappingProxyType
from typing import Literal

from tomography_session_browser.domain.link_confirmations import Decision, LinkConfirmation
from tomography_session_browser.domain.alignment import AlignmentOffer, AlignmentTransforms, AlignmentUnavailable
from tomography_session_browser.domain.reconstruction_names import reconstruction_name
from tomography_session_browser.domain.recorded_metrics import ProcessingEvidence
from tomography_session_browser.domain.tomograms import ProcessingSource, Tomogram
from tomography_session_browser.parsers.imod_transforms import differences


@dataclass(frozen=True, slots=True)
class AlignmentLink:
    status: Literal['linked', 'confirmed', 'tie', 'differs', 'unavailable']
    explanation: str
    #: The alignment sources that hold a series of the same name, in load order.
    candidates: tuple[str, ...] = ()
    #: The proving series when linked; the one same-name series when it can be confirmed by name.
    offer: AlignmentOffer | None = None
    #: The alignment source that proves or lends the link; None unless linked or confirmed.
    source_id: str | None = None
    #: Set only when the user can confirm (or has confirmed) the link by name.
    signature: str = ''
    decision: Decision | None = None

    @property
    def target_key(self) -> str:
        return f'{self.candidates[0]}::{self.offer.series}' if self.signature and self.offer else ''


def _quoted(names: Iterable[str]) -> str:
    return ', '.join(f'"{name}"' for name in names)


def by_name_signature(tomogram: Tomogram, source: ProcessingSource, offer: AlignmentOffer) -> str:
    """What a by-name decision was made on: both sides' files and the reason nothing was compared."""
    own = tomogram.own_alignment
    payload = (tomogram.source_id, tomogram.id, tomogram.name,
               [(volume.id, str(volume.path), volume.fingerprint.size, volume.fingerprint.mtime_ns)
                for volume in tomogram.volumes],
               own.detail if isinstance(own, AlignmentUnavailable) else None, source.id, offer.series,
               [(str(stamp.path), stamp.size, stamp.mtime_ns) for stamp in offer.stamps])
    return hashlib.sha256(json.dumps(payload, separators=(',', ':')).encode()).hexdigest()


def _sentence(detail: str) -> str:
    return detail[:1].lower() + detail[1:]


def _by_name(tomogram: Tomogram, own: AlignmentUnavailable, source: ProcessingSource, offer: AlignmentOffer,
             records: Mapping[tuple[str, str], LinkConfirmation]) -> AlignmentLink:
    signature = by_name_signature(tomogram, source, offer)
    link = AlignmentLink('unavailable', f'"{source.name}" has this series: Not compared: {_sentence(own.detail)}',
                         (source.id,), offer, signature=signature)
    record = records.get((tomogram.source_id, tomogram.id))
    if record is None or (record.target_key, record.signature) != (link.target_key, signature):
        return link
    if record.decision != 'confirmed':
        return replace(link, decision=record.decision)
    return replace(link, status='confirmed', explanation=f'Alignment from "{source.name}": Confirmed by name. {own.detail}',
                   source_id=source.id, decision='confirmed')


def _resolve(tomogram: Tomogram, candidates: list[tuple[ProcessingSource, AlignmentOffer]],
             records: Mapping[tuple[str, str], LinkConfirmation]) -> AlignmentLink:
    own = tomogram.own_alignment
    ids = tuple(dict.fromkeys(source.id for source, _offer in candidates))
    names = [source.name for source, _offer in candidates]
    if isinstance(own, AlignmentUnavailable):
        if len(candidates) == 1:
            return _by_name(tomogram, own, *candidates[0], records)
        return AlignmentLink('unavailable', f'{_quoted(names)} have this series: Not compared: {_sentence(own.detail)}', ids)
    assert isinstance(own, AlignmentTransforms)
    passed, refused = [], []
    for source, offer in candidates:
        found = differences(own, offer.alignment) if isinstance(offer.alignment, AlignmentTransforms) else None
        if found == 0:
            passed.append((source, offer))
        elif found is None:
            detail = offer.alignment.detail if isinstance(offer.alignment, AlignmentUnavailable) else 'the number of views differs'
            refused.append(f'"{source.name}": {detail}')
        else:
            refused.append(f'"{source.name}": {found} values differ')
    if len(passed) == 1:
        source, offer = passed[0]
        return AlignmentLink('linked', f'Alignment from "{source.name}": its tilt angles and transforms match.',
                             ids, offer, source.id)
    if passed:
        return AlignmentLink('tie', f'Several alignment jobs match this reconstruction: '
                             f'{_quoted(source.name for source, _offer in passed)}. None was chosen.', ids)
    return AlignmentLink('differs', 'The alignment jobs with this series do not match its tilt angles and '
                         f'transforms ({"; ".join(refused)}). Nothing was linked.', ids)


def link_alignments(sources: Iterable[ProcessingSource],
                    confirmations: Iterable[LinkConfirmation] = ()) -> Mapping[str, AlignmentLink]:
    """One outcome for each reconstruction that records its own alignment and has a same-name series."""
    loaded = tuple(sources)
    records = {(record.source_id, record.tomogram_id): record for record in confirmations}
    offers: dict[str, list[tuple[ProcessingSource, AlignmentOffer]]] = {}
    for source in loaded:
        for offer in source.alignments:
            offers.setdefault(reconstruction_name(offer.series), []).append((source, offer))
    links: dict[str, AlignmentLink] = {}
    for source in loaded:
        for tomogram in source.tomograms:
            candidates = offers.get(reconstruction_name(tomogram.name))
            if tomogram.own_alignment is not None and candidates:
                links[tomogram.id] = _resolve(tomogram, candidates, records)
    return MappingProxyType(links)


def borrow(tomogram: Tomogram, link: AlignmentLink) -> Tomogram:
    """The row with a linked job's evidence joined, or with the reason it was not linked.

    A link confirmed by name joins the by-name evidence and keeps its card, so
    the panel says the values were not compared.
    """
    own = tomogram.processing
    if link.status not in ('linked', 'confirmed') or link.offer is None:
        return replace(tomogram, processing=replace(own, warnings=(*own.warnings, link.explanation)))
    borrowed = link.offer.evidence if link.status == 'linked' else link.offer.by_name
    if link.status == 'confirmed':
        own = replace(own, warnings=(*own.warnings, link.explanation))
    alignment = '\n'.join(text for text in (own.alignment, borrowed.alignment) if text != ProcessingEvidence().alignment)
    return replace(tomogram, processing=ProcessingEvidence(alignment or own.alignment,
                                                           (*own.metrics, *borrowed.metrics),
                                                           (*own.warnings, *borrowed.warnings)))


def borrowed_rows(sources: Iterable[ProcessingSource], links: Mapping[str, AlignmentLink]) -> Mapping[str, Tomogram]:
    """Display copies of the rows that have an outcome; every other row keeps its loaded object."""
    return MappingProxyType({tomogram.id: borrow(tomogram, links[tomogram.id])
                             for source in sources for tomogram in source.tomograms if tomogram.id in links})


def merged_alignments(sources: Iterable[ProcessingSource],
                      links: Mapping[str, AlignmentLink]) -> Mapping[str, tuple[str, ...]]:
    """Alignment source id -> the reconstruction sources it lends to, in load order.

    Only a job with no warning of its own, every series of which is linked or
    confirmed to a loaded reconstruction, is here. The project panel shows such
    a job inside its reconstructions, not as its own row; any other job keeps
    its row, so its reason and its actions stay in view.
    """
    loaded = tuple(sources)
    owner = {tomogram.id: source.id for source in loaded for tomogram in source.tomograms}
    used: dict[str, set[str]] = {}
    targets: dict[str, dict[str, None]] = {}
    for tomogram_id, link in links.items():
        if link.source_id is not None and link.offer is not None:
            used.setdefault(link.source_id, set()).add(link.offer.series)
            targets.setdefault(link.source_id, {})[owner[tomogram_id]] = None
    return MappingProxyType({source.id: tuple(targets[source.id]) for source in loaded
                             if source.alignments and not source.warnings
                             and used.get(source.id) == {offer.series for offer in source.alignments}})


def linked_series_line(source: ProcessingSource, links: Mapping[str, AlignmentLink]) -> str:
    """How many of an alignment source's series prove a loaded reconstruction."""
    used = {link.offer.series for link in links.values() if link.source_id == source.id and link.offer is not None}
    total = len(source.alignments)
    return f'{len(used)} of {total} alignment series linked to a loaded reconstruction.'
