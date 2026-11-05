"""What Manage tags reports: each catalogue tag's use and the project's last save."""
from dataclasses import dataclass
from datetime import datetime

from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.domain.tomogram_tags import TagProject


@dataclass(frozen=True, slots=True)
class CatalogueRow:
    tag_id: str
    name: str
    tomograms: int
    counted: int
    total: int | None


def catalogue_rows(project: TagProject) -> tuple[CatalogueRow, ...]:
    """Every tag by name. A total needs a recorded count: no count is not zero."""

    rows = []
    for tag in sorted(project.catalogue, key=lambda t: t.name.casefold()):
        assignments = [a for row in project.tomograms for a in row.tags if a.tag_id == tag.id]
        counts = [a.count for a in assignments if a.count is not None]
        rows.append(CatalogueRow(tag.id, tag.name, len(assignments), len(counts), sum(counts) if counts else None))
    return tuple(rows)


def merge_order(rows: tuple[CatalogueRow, ...]) -> tuple[str, str]:
    """(removed, surviving) for two chosen tags: the review starts by keeping the
    tag on more tomograms, and the first by name on a tie. The user can swap them."""

    first, second = rows
    return (second.tag_id, first.tag_id) if first.tomograms >= second.tomograms else (first.tag_id, second.tag_id)


def selection_text(shown: int, total: int, selected: int) -> str:
    tags = count_phrase(total, "tag") if shown == total else f"{shown} of {count_phrase(total, 'tag')}"
    return f"{tags} · {selected} selected" if selected else tags


def saved_text(status: str, when: datetime | None, now: datetime) -> str:
    """``Saved 14:02`` today and with its date on another day. Without a known
    time, as for every state except saved, only the state."""

    if when is None:
        return status
    return f"{status} {when:%H:%M}" if when.date() == now.date() else f"{status} {when:%d %b %Y %H:%M}"
