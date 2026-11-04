"""Project-scoped predicates, evaluated when a working set is computed."""
from dataclasses import dataclass, replace
from enum import StrEnum

from tomography_session_browser.domain.tomogram_tags import TomogramTags
from tomography_session_browser.services.tomogram_tags.binding import ProjectKey


class CompletionFilter(StrEnum):
    ANY = "Any"
    COMPLETE = "Counting complete"
    INCOMPLETE = "Counting not complete"


@dataclass(frozen=True, slots=True)
class TagFilter:
    project: ProjectKey | None = None
    tag_ids: frozenset[str] = frozenset()
    completion: CompletionFilter = CompletionFilter.ANY

    @property
    def active(self) -> bool:
        return bool(self.tag_ids) or self.completion != CompletionFilter.ANY

    def accepts(self, key: ProjectKey | None, row: TomogramTags | None) -> bool:
        if not self.active:
            return True
        if self.project is None or key != self.project:
            return False
        assigned = {t.tag_id for t in row.tags} if row else set()
        complete = row.counting_complete if row else False
        return self.tag_ids <= assigned and (self.completion == CompletionFilter.ANY or
               complete == (self.completion == CompletionFilter.COMPLETE))

    def catalogue_changed(self, existing: frozenset[str], remapped: dict[str, str]) -> "TagFilter":
        ids = frozenset(remapped.get(i, i) for i in self.tag_ids)
        return replace(self, tag_ids=ids & existing)
