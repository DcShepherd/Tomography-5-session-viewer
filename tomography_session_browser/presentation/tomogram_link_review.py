"""Review rows come from the canonical resolver, never from a second name lookup."""
from dataclasses import dataclass

from tomography_session_browser.domain.link_confirmations import LinkConfirmation, Decision
from tomography_session_browser.presentation.workspace import Workspace, EntityKind
from tomography_session_browser.domain.identity import entity_key_text


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
    return tuple(rows)
