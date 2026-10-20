"""State-specific empty and recovery experiences (plan item E1).

The viewer had one generic message — "No items for the current selection." —
for six genuinely different situations. Nothing loaded, a scope that really is
empty, a filter that matched nothing, a preview that failed to read, a load
that failed outright, and a relationship that could not be resolved all look
identical to the reviewer, and none of them says what to do next.

Each variant here names what happened, what remains inspectable, and offers
**exactly one visually primary safe action** — enforced by construction, since
``primary`` is a single field rather than a list. Anything else is secondary.

Pure and Qt-free.
"""

from __future__ import annotations

from dataclasses import dataclass, field

KIND_NO_SESSION = "no_session"
KIND_NO_ENTITIES = "no_entities"
KIND_FILTER_NO_RESULTS = "filter_no_results"
KIND_MISSING_PREVIEW = "missing_preview"
KIND_IMAGE_MISSING = "image_missing"
KIND_LOAD_FAILED = "load_failed"
KIND_UNRESOLVED_RELATIONSHIP = "unresolved_relationship"

# Commands a primary or secondary action can request.
COMMAND_OPEN_SESSION = "open_session"
COMMAND_CHOOSE_ANOTHER = "choose_another_folder"
COMMAND_RETRY = "retry_load"
COMMAND_CLEAR_FILTER = "clear_filter"
COMMAND_COPY_DETAILS = "copy_details"
COMMAND_RETURN_TO_SCOPE = "return_to_scope"
COMMAND_SHOW_METADATA = "show_metadata"


@dataclass(frozen=True, slots=True)
class EmptyStateAction:
    label: str
    command: str


@dataclass(frozen=True, slots=True)
class EmptyState:
    """What to show when there is nothing to show, and what to do about it."""

    kind: str
    title: str
    detail: str = ""
    # Exactly one primary action, by construction. A screen with two equally
    # weighted recommendations has none.
    primary: EmptyStateAction | None = None
    secondary: tuple[EmptyStateAction, ...] = ()
    # Facts that survive the emptiness — a path that does exist, metadata that
    # is still readable. The reviewer should never be told only what is absent.
    evidence: tuple[str, ...] = ()
    copyable_details: str = ""
    #: A file or folder to show as it is, with a button that copies it.
    path: str = ""

    @property
    def has_primary(self) -> bool:
        return self.primary is not None

    @property
    def actions(self) -> tuple[EmptyStateAction, ...]:
        return ((self.primary,) if self.primary else ()) + self.secondary


def no_session_state() -> EmptyState:
    return EmptyState(
        kind=KIND_NO_SESSION,
        title="No session loaded",
        detail="Open a Tomography 5 session folder to begin a review.",
        primary=EmptyStateAction("Open session folders", COMMAND_OPEN_SESSION),
    )


def no_entities_state(*, scope_name: str, entity_label: str) -> EmptyState:
    """A scope that loaded fine but contains none of this entity type."""

    scope = scope_name or "the current scope"
    return EmptyState(
        kind=KIND_NO_ENTITIES,
        title=f"No {entity_label.lower()} in {scope}",
        detail=(
            f"{scope} loaded successfully; it simply contains no "
            f"{entity_label.lower()}. Other tabs may still have data."
        ),
        primary=EmptyStateAction("Back to the session overview", COMMAND_RETURN_TO_SCOPE),
    )


def filter_no_results_state(
    *,
    filter_text: str,
    entity_label: str = "items",
) -> EmptyState:
    """No matching rows; the list owns the count and the Clear action."""

    return EmptyState(
        kind=KIND_FILTER_NO_RESULTS,
        title=f"No matching {entity_label.lower()}",
        detail=f"No {entity_label.lower()} match “{filter_text}”. Change or clear the filter in the list.",
    )


def missing_preview_state(
    *,
    name: str,
    expected_path: str = "",
    metadata_available: bool = False,
    warnings: tuple[str, ...] = (),
) -> EmptyState:
    """An entity whose image could not be read.

    The object is still real and its metadata is still inspectable; saying only
    "no preview" would imply the whole record is unusable.
    """

    evidence: list[str] = []
    if expected_path:
        evidence.append(f"Expected image: {expected_path}")
    if metadata_available:
        evidence.append("Metadata for this object is still readable.")
    evidence.extend(warnings)
    return EmptyState(
        kind=KIND_MISSING_PREVIEW,
        title=f"No preview available for {name}",
        detail=(
            "The image could not be read. The object and its metadata remain "
            "inspectable."
            if metadata_available
            else "The image could not be read."
        ),
        primary=(
            EmptyStateAction("Show metadata", COMMAND_SHOW_METADATA)
            if metadata_available
            else None
        ),
        evidence=tuple(evidence),
        copyable_details=expected_path,
    )


def image_missing_state(*, explanation: str, path: str = "") -> EmptyState:
    """An object whose image file is not there, though its record is.

    The sentence says what was looked for and not found; the path is where it
    was looked for, to read and to copy. The record is still inspectable, so the
    one primary action opens it.
    """

    return EmptyState(
        kind=KIND_IMAGE_MISSING,
        title="No image to show",
        detail=explanation,
        primary=EmptyStateAction("Show metadata", COMMAND_SHOW_METADATA),
        copyable_details=path,
        path=path,
    )


def load_failed_state(*, path: str, error: str) -> EmptyState:
    return EmptyState(
        kind=KIND_LOAD_FAILED,
        title="Could not load this folder",
        detail=error,
        primary=EmptyStateAction("Retry", COMMAND_RETRY),
        secondary=(
            EmptyStateAction("Choose another folder", COMMAND_CHOOSE_ANOTHER),
            EmptyStateAction("Copy details", COMMAND_COPY_DETAILS),
        ),
        evidence=(f"Folder: {path}",) if path else (),
        copyable_details=f"{path}\n{error}".strip(),
    )


def unresolved_relationship_state(
    *,
    subject: str,
    explanation: str,
    still_inspectable: tuple[str, ...] = (),
) -> EmptyState:
    """A relationship the resolver refused to guess at.

    Offers no destination on purpose — that is the whole point — but says what
    remains available, so an inert action does not read as a broken one.
    """

    return EmptyState(
        kind=KIND_UNRESOLVED_RELATIONSHIP,
        title=f"No unique destination from {subject}",
        detail=explanation,
        evidence=still_inspectable,
    )


def viewer_empty_state(
    *,
    has_sessions: bool,
    total_items: int,
    filter_text: str,
    scope_name: str,
    entity_label: str,
) -> EmptyState:
    """Choose the right variant for an empty viewer list.

    Order matters: nothing loaded outranks an empty scope, which outranks a
    filter that matched nothing.
    """

    if not has_sessions:
        return no_session_state()
    if total_items and filter_text:
        return filter_no_results_state(
            filter_text=filter_text,
            entity_label=entity_label,
        )
    return no_entities_state(scope_name=scope_name, entity_label=entity_label)
