"""Prefix-only suggestions and the reversible tag-entry draft."""
from dataclasses import dataclass, replace
from enum import StrEnum

from tomography_session_browser.domain.tomogram_tags import Tag
from tomography_session_browser.services.tomogram_tags.codec import MAX_COUNT, count, name


@dataclass(frozen=True, slots=True)
class TagSuggestion:
    name: str
    tag_id: str | None
    assigned: bool = False


def matching_tags(query: str, catalogue: tuple[Tag, ...]) -> tuple[Tag, ...]:
    """Every tag whose name starts with the query, ignoring case and a leading #."""
    folded = query.strip().lstrip("#").strip().casefold()
    return tuple(t for t in sorted(catalogue, key=lambda t: (t.name.casefold(), t.id)) if t.name.casefold().startswith(folded))


def suggestions(query: str, catalogue: tuple[Tag, ...], assigned: frozenset[str] = frozenset()) -> tuple[TagSuggestion, ...]:
    query = query.strip().lstrip("#").strip()
    folded = query.casefold()
    matching = [TagSuggestion(t.name, t.id, t.id in assigned) for t in matching_tags(query, catalogue)]
    exact = any(t.name.casefold() == folded for t in catalogue)
    if query and not exact:
        try:
            label = name(query)
        except ValueError:
            return tuple(matching[:8])
        return (*matching[:7], TagSuggestion(label[0].upper() + label[1:], None))
    return tuple(matching[:8])


class EntryStage(StrEnum):
    TYPING = "Typing"
    COUNT = "Count"
    SAVED = "Saved"
    CANCELLED = "Cancelled"


class DraftValidity(StrEnum):
    VALID = "valid"
    INVALID = "invalid"


def count_error(current_count: int | None) -> str:
    """Explain invalid input without implying that the stored observation changed."""
    consequence = f"The count stays {current_count:,}." if current_count is not None else "This tag has no count yet."
    return f"Use a whole number from 0 to {MAX_COUNT:,}, or leave it blank. {consequence}"


@dataclass(frozen=True, slots=True)
class TagDraft:
    query: str = ""
    selected: TagSuggestion | None = None
    count_text: str = ""
    stage: EntryStage = EntryStage.TYPING

    def accept(self, suggestion: TagSuggestion, current_count: int | None = None) -> "TagDraft":
        return replace(self, selected=suggestion, count_text=str(current_count) if current_count is not None else "", stage=EntryStage.COUNT)

    def value(self) -> int | None:
        value = self.count_text.strip()
        if not value:
            return None
        if len(value) > len(str(MAX_COUNT)) or not value.isascii() or not value.isdecimal() or int(value) > MAX_COUNT:
            raise ValueError(count_error(None))
        return count(int(value))

    def save(self) -> "TagDraft":
        if self.stage != EntryStage.COUNT or self.selected is None:
            raise ValueError("Choose a tag first.")
        self.value()
        return replace(self, stage=EntryStage.SAVED)

    def cancel(self) -> "TagDraft":
        return replace(self, stage=EntryStage.CANCELLED)
