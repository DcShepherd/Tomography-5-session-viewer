"""Pure display model for "where am I?" (plan item C1).

The workspace has five independent axes that a reviewer has to keep straight:
the review *scope*, the entity *section*, the *selection* inside it, any
borrowed or unresolved *relationship* context, and the current *view state*.
Before this module they were spread across a window title, a hard-coded
dashboard eyebrow, a tab label and a list header, and the selected object was
often presented as though it were the scope.

Everything here is display-only, Qt-free and pure so it can be tested without a
widget, and it never resolves entities itself: callers pass in what they have
already resolved.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# One place where an internal tab key becomes user-facing text.
#
# The keys are dictionary keys used across the window (tab lookup, prepared
# payloads, preview sources, icons) and must NOT be renamed. Only the values
# are ever shown to a person.
ENTITY_DISPLAY_LABELS: dict[str, str] = {
    "Atlas": "Atlas",
    "Overview": "Overviews",
    "Search map": "Search maps",
    "Search": "Search tiles",
    "Batch position": "Batch positions",
    "Tilt series": "Tilt series",
    "Session": "Session",
}

# Singular forms, for sentences about one object rather than a section.
ENTITY_DISPLAY_LABELS_SINGULAR: dict[str, str] = {
    "Atlas": "atlas",
    "Overview": "overview",
    "Search map": "search map",
    "Search": "search tile",
    "Batch position": "batch position",
    "Tilt series": "tilt series",
}

SCOPE_BADGES: dict[str, str] = {
    "linked": "LS",
    "atlas": "AT",
    "collection": "DC",
    "unresolved": "!",
}

CRUMB_SEPARATOR = " › "  # single right-pointing angle quotation mark


def tab_key_for_display_label(text: str | None) -> str | None:
    """Inverse of :func:`entity_display_label`.

    The project tree labels its entity groups with display text, but tab
    lookup needs the internal key. Deriving the inverse here keeps the two
    directions from drifting — a hand-maintained second map is exactly how the
    tree's ``Search tiles`` group stopped resolving to the ``Search`` tab.
    """

    if not text:
        return None
    for key, label in ENTITY_DISPLAY_LABELS.items():
        if label == text:
            return key
    return text if text in ENTITY_DISPLAY_LABELS else None


def entity_display_label(tab_key: str | None) -> str:
    """User-facing name for an entity section.

    Returns the key unchanged when it is not a known tab, so an unexpected
    value is visible rather than silently blanked.
    """

    if not tab_key:
        return ""
    return ENTITY_DISPLAY_LABELS.get(tab_key, tab_key)


def entity_display_label_singular(tab_key: str | None) -> str:
    """User-facing name for a single object of this entity type."""

    if not tab_key:
        return ""
    return ENTITY_DISPLAY_LABELS_SINGULAR.get(tab_key, tab_key.lower())


def open_action_label(tab_key: str | None) -> str:
    """Verb-plus-destination label, e.g. ``Open linked tilt series``."""

    name = entity_display_label_singular(tab_key)
    return f"Open linked {name}" if name else "Open linked item"


@dataclass(frozen=True, slots=True)
class ContextStack:
    """Where the reviewer is, on every axis that matters.

    ``scope`` is the review scope from the project tree — never the selected
    object. Presenting a selection as the scope was the specific confusion
    this model exists to remove.
    """

    scope: str = ""
    scope_kind: str = ""
    section: str = ""
    selection: str = ""
    marker: str = ""
    relationship: str = ""
    filter_text: str = ""
    filter_visible: int | None = None
    filter_total: int | None = None

    @property
    def scope_badge(self) -> str:
        return SCOPE_BADGES.get(self.scope_kind, "")

    @property
    def scope_label(self) -> str:
        badge = self.scope_badge
        if not self.scope:
            return ""
        return f"{self.scope} ({badge})" if badge else self.scope

    @property
    def selection_label(self) -> str:
        """The selected row, plus the exact marker when one is active."""

        if self.selection and self.marker:
            return f"{self.selection} · {self.marker}"
        return self.selection or self.marker

    def crumbs(self) -> tuple[str, ...]:
        """The context path, coarsest first, with empty levels omitted."""

        parts = [self.scope_label, self.section, self.selection_label]
        return tuple(part for part in parts if part)

    @property
    def has_filter(self) -> bool:
        return bool(self.filter_text)

    @property
    def filter_summary(self) -> str:
        """``3 of 20`` style count, empty when no filter is active."""

        if not self.has_filter:
            return ""
        if self.filter_visible is None or self.filter_total is None:
            return f"Filter: {self.filter_text}"
        return f"{self.filter_visible} of {self.filter_total}"

    def view_state_notes(self) -> tuple[str, ...]:
        """Everything currently narrowing or annotating what is on screen."""

        notes: list[str] = []
        if self.has_filter:
            summary = self.filter_summary
            notes.append(f"Filtered — {summary}" if summary else "Filtered")
        if self.relationship:
            notes.append(self.relationship)
        return tuple(notes)

    def to_text(self) -> str:
        """Single-line rendering, e.g. for a status strip or a tooltip."""

        return CRUMB_SEPARATOR.join(self.crumbs())

    @property
    def is_empty(self) -> bool:
        return not self.crumbs()


def build_context_stack(
    *,
    scope_name: str | None = None,
    scope_kind: str = "",
    section_key: str | None = None,
    selection_name: str | None = None,
    marker_name: str | None = None,
    relationship: str = "",
    filter_text: str = "",
    filter_visible: int | None = None,
    filter_total: int | None = None,
) -> ContextStack:
    """Assemble a context stack from already-resolved pieces.

    ``section_key`` is an internal tab key; it is mapped to display text here
    so callers cannot leak a raw key such as ``Search`` into the interface.
    """

    return ContextStack(
        scope=(scope_name or "").strip(),
        scope_kind=scope_kind or "",
        section=entity_display_label(section_key),
        selection=(selection_name or "").strip(),
        marker=(marker_name or "").strip(),
        relationship=relationship.strip(),
        filter_text=filter_text.strip(),
        filter_visible=filter_visible,
        filter_total=filter_total,
    )


def borrowed_atlas_note(atlas_source: str | None) -> str:
    """Explain that Atlas context came from a linked screening session."""

    if not atlas_source:
        return ""
    return f"Atlas context borrowed from {atlas_source}"


def describe_scope_value(value: Any) -> str:
    """Best available display name for a scope object."""

    for attribute in ("display_name", "name", "label"):
        candidate = getattr(value, attribute, None)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""
