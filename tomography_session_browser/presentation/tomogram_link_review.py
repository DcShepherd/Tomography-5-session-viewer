"""Review rows come from the canonical resolver, never from a second name lookup."""
from dataclasses import dataclass
from typing import Literal

from tomography_session_browser.domain.link_confirmations import LinkConfirmation, Decision
from tomography_session_browser.presentation.workspace import Workspace, EntityKind
from tomography_session_browser.domain.alignment import AlignmentUnavailable
from tomography_session_browser.domain.identity import entity_key_text
from tomography_session_browser.presentation.messages import ActionPrompt, MessageAction, MessageContent

#: Which store a decision goes to: the parent tilt series, or the alignment job.
LinkKind = Literal['parent', 'alignment']


@dataclass(frozen=True, slots=True)
class NameLinkRow:
    source_id: str
    tomogram_id: str
    target_key: str
    signature: str
    tomogram: str
    target: str
    decision: Decision | None
    target_detail: str = ''
    kind: LinkKind = 'parent'
    #: Why nothing was compared (alignment rows only).
    not_compared: str = ''

    def record(self, decision: Decision) -> LinkConfirmation:
        return LinkConfirmation(self.source_id, self.tomogram_id, self.target_key, self.signature, decision)


def name_link_rows(workspace: Workspace, source_id: str = '') -> tuple[NameLinkRow, ...]:
    targets = workspace.acquisition_index.by_key
    objects = {entity_key_text(value): value for value in workspace.entities(EntityKind.TILT_SERIES)}
    rows = []
    for source in workspace.processing_sources:
        if source_id and source.id != source_id:
            continue
        for item in source.tomograms:
            link = workspace.tomogram_links.get(item.id)
            if link is None or link.step != 'E' or len(link.candidates) != 1 or not link.proposal_signature:
                continue
            target = targets[link.candidates[0]]
            owner = workspace.row_owner(objects.get(target.key))
            label = owner[1] if owner is not None else target.stack_path.parent.name
            rows.append(NameLinkRow(source.id, item.id, target.key, link.proposal_signature,
                f'{source.name} / {item.name}', f'{target.name} · {label}', link.decision, str(target.stack_path)))
    return tuple(rows) + alignment_rows(workspace, source_id)


def alignment_rows(workspace: Workspace, source_id: str = '') -> tuple[NameLinkRow, ...]:
    """Reconstructions whose one same-name alignment series the user can confirm by name."""
    names = {source.id: source.name for source in workspace.processing_sources}
    rows = []
    for source in workspace.processing_sources:
        if source_id and source.id != source_id:
            continue
        for item in source.tomograms:
            link = workspace.alignment_links.get(item.id)
            own = item.own_alignment
            if link is None or not link.signature or not isinstance(own, AlignmentUnavailable):
                continue
            rows.append(NameLinkRow(source.id, item.id, link.target_key, link.signature, f'{source.name} / {item.name}',
                                    names[link.candidates[0]], link.decision, kind='alignment',
                                    target_detail='\n'.join(str(stamp.path) for stamp in link.offer.stamps) if link.offer else '',
                                    not_compared=own.detail.rstrip('.')))
    return tuple(rows)


def alignment_prompt(row: NameLinkRow, tomogram: str) -> ActionPrompt[str]:
    """The question before one alignment job is linked to one reconstruction by name."""
    content = MessageContent(f'Use {row.target} for {tomogram}?',
                             f'Only the series name agrees. The values cannot be compared: {row.not_compared[:1].lower()}{row.not_compared[1:]}. '
                             'Saved in app settings. Source folders do not change.',
                             details='The decision holds while these files do not change:\n' + row.target_detail)
    return ActionPrompt(content, (MessageAction('confirm', 'Confirm'), MessageAction('cancel', 'Cancel')),
                        'confirm', 'cancel')
