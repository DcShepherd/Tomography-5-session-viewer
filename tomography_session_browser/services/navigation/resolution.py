"""One candidate uniqueness rule for metadata resolution and marker fallbacks."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from tomography_session_browser.domain.models import Atlas
from tomography_session_browser.services.relationship_identity import dedupe_navigation_candidates as dedupe_navigation_candidates

from .models import NavigationResolution, STATE_NAVIGABLE, STATE_AMBIGUOUS, STATE_UNRESOLVED


def _identity(value: Any) -> str:
    identifier = getattr(value, "id", None)
    return str(identifier) if identifier is not None else ""


def _display_name(value: Any) -> str:
    name = getattr(value, "name", None)
    if isinstance(name, str) and name:
        return name
    if isinstance(value, Atlas) and value.image_path is not None:
        # An Atlas has no name, and its ID is its whole folder path. The
        # session and sample folders are what tell two candidates apart.
        sample_folder = value.image_path.parent.parent
        return f"{sample_folder.parent.name}/{sample_folder.name} atlas"
    return _identity(value) or "unnamed"


def _candidate_names(candidates: tuple[Any, ...]) -> list[str]:
    """Names for an ambiguity, each Atlas told apart by its folder when two
    read the same (as the project tree's ambiguous-link warning does)."""

    names = [_display_name(item) for item in candidates]
    repeated = {name for name in names if names.count(name) > 1}
    return [
        f"{name} ({item.image_path.parent.parent})"
        if name in repeated and isinstance(item, Atlas) and item.image_path is not None
        else name
        for name, item in zip(names, candidates, strict=True)
    ]


def navigable_resolution(target: Any, *, provenance: str, explanation: str) -> NavigationResolution:
    return NavigationResolution(
        target=target,
        candidate_ids=(_identity(target),),
        state=STATE_NAVIGABLE,
        explanation=explanation,
        provenance=provenance,
    )


def ambiguous_resolution(
    candidates: Iterable[Any],
    *,
    provenance: str,
    explanation: str,
) -> NavigationResolution:
    return NavigationResolution(
        target=None,
        candidate_ids=tuple(sorted(_identity(item) for item in candidates)),
        state=STATE_AMBIGUOUS,
        explanation=explanation,
        provenance=provenance,
    )


def unresolved_resolution(*, provenance: str, explanation: str) -> NavigationResolution:
    return NavigationResolution(
        state=STATE_UNRESOLVED,
        explanation=explanation,
        provenance=provenance,
    )


def explicit_id_resolution(
    candidates: Iterable[Any],
    *,
    provenance: str,
    navigable_explanation: str,
    ambiguous_explanation: str,
) -> NavigationResolution | None:
    """What an explicit recorded ID decides: its one object, or ambiguity.

    ``None`` when the ID names nothing in scope; the caller then consults the
    next source of evidence, since a dangling ID is not a failure on its own.
    """

    unique = dedupe_navigation_candidates(candidates)
    if len(unique) == 1:
        return navigable_resolution(unique[0], provenance=provenance, explanation=navigable_explanation)
    if len(unique) > 1:
        return ambiguous_resolution(unique, provenance=provenance, explanation=ambiguous_explanation)
    return None


def navigation_resolution_from_candidates(
    candidates: Iterable[Any],
    *,
    provenance: str,
    navigable_explanation: str,
    ambiguous_prefix: str,
    unresolved_explanation: str,
) -> NavigationResolution:
    """Offer a destination only when exactly one candidate survives.

    Shared by metadata resolution and marker-based fallback matching, so both
    apply the same uniqueness rule.
    """

    unique = dedupe_navigation_candidates(candidates)
    if len(unique) == 1:
        return navigable_resolution(unique[0], provenance=provenance, explanation=navigable_explanation)
    if len(unique) > 1:
        names = ", ".join(sorted(_candidate_names(unique)))
        return ambiguous_resolution(
            unique,
            provenance=provenance,
            explanation=f"{ambiguous_prefix}: {names}.",
        )
    return unresolved_resolution(provenance=provenance, explanation=unresolved_explanation)
