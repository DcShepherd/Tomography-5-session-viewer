"""Display facts from the exact reviewed merge proposal; no conflict inference."""
from dataclasses import dataclass
from datetime import datetime

from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.domain.tomogram_tags import TagAssignment
from tomography_session_browser.services.tomogram_tags.project import MergeProposal


@dataclass(frozen=True, slots=True)
class MergeSummary:
    removed: str
    surviving: str
    affected: int
    conflicts: int

    @property
    def title(self) -> str:
        return f"Merge “{self.removed}” into “{self.surviving}”"

    @property
    def detail(self) -> str:
        verb = "has" if self.conflicts == 1 else "have"
        conflict = f"{count_phrase(self.conflicts, 'tomogram')} {verb} different counts." if self.conflicts else "No counts need a choice."
        return (f"This affects {count_phrase(self.affected, 'tomogram')}. {conflict} "
                "Where only one tag has a count, that count is kept. Counts are never added.")


def merge_summary(proposal: MergeProposal) -> MergeSummary:
    names = {tag.id: tag.name for tag in proposal.original.catalogue}
    affected = sum(any(assignment.tag_id == proposal.removed_id for assignment in row.tags)
                   for row in proposal.original.tomograms)
    return MergeSummary(names[proposal.removed_id], names[proposal.surviving_id], affected, len(proposal.conflicts))


def observation_text(assignment: TagAssignment) -> str:
    """Local time with explicit offset; original timestamp remains in Copy."""
    stamp = datetime.fromisoformat(assignment.recorded_at.replace("Z", "+00:00")).astimezone()
    offset = stamp.strftime("%z")
    zone = f"UTC{offset[:3]}:{offset[3:]}"
    return f"{assignment.version_label or 'Version not recorded'}\n{stamp:%d %b %Y, %H:%M} {zone}"


@dataclass(frozen=True, slots=True)
class MergeCommitted:
    removed_id: str
    surviving_id: str
