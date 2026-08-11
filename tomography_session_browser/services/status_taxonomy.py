"""Status dimensions and the shared warning-presentation model (plan item T1).

The app had accumulated four overlapping status vocabularies:

* ``services/item_status.py`` — ``done / partial / incomplete / failed /
  missing / unknown`` for viewer list rows;
* ``services/tilt_series_validation.py`` — ``complete / failed / incomplete /
  unknown`` for the scientific classification;
* ``services/batch_position_status.py`` and the Atlas glyph set — ``collected /
  partial / queued / failed / unattributed``;
* ``domain/enums.WarningSeverity`` — ``info / warning / error``.

They are not redundant: they answer *different questions* about the same
object. This module names those questions as explicit dimensions and maps each
existing vocabulary onto them.

**It renames nothing.** Where the taxonomy and an existing chip disagree, the
existing scientific meaning wins and the mapping adapts — a renamed status chip
would be a scientific change, not a wording change. Everything here is
display-only and derives from values the services already produce.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- the dimensions --------------------------------------------------------

# How the *application* is getting on with the data.
APPLICATION_LOADING = "Loading"
APPLICATION_READY = "Ready"
APPLICATION_LOAD_WARNING = "Load warning"
APPLICATION_LOAD_FAILED = "Load failed"

# What the *microscope* achieved.
ACQUISITION_COMPLETE = "Complete"
ACQUISITION_INCOMPLETE = "Incomplete"
ACQUISITION_FAILED = "Failed"
ACQUISITION_PENDING = "Pending"
ACQUISITION_UNAVAILABLE = "Unavailable"

# How much a relationship can be trusted.
LINK_LINKED = "Linked"
LINK_INFERRED = "Inferred"
LINK_UNRESOLVED = "Unresolved"
LINK_AMBIGUOUS = "Ambiguous"

# How much the reviewer should care.
ATTENTION_INFORMATION = "Information"
ATTENTION_WARNING = "Warning"
ATTENTION_ERROR = "Error"

# What the interface is doing to the object right now.
INTERACTION_SELECTED = "Selected"
INTERACTION_HIGHLIGHTED = "Highlighted"
INTERACTION_FILTERED = "Filtered"


ACQUISITION_STATES: tuple[str, ...] = (
    ACQUISITION_COMPLETE,
    ACQUISITION_INCOMPLETE,
    ACQUISITION_FAILED,
    ACQUISITION_PENDING,
    ACQUISITION_UNAVAILABLE,
)
LINK_STATES: tuple[str, ...] = (
    LINK_LINKED,
    LINK_INFERRED,
    LINK_UNRESOLVED,
    LINK_AMBIGUOUS,
)
ATTENTION_STATES: tuple[str, ...] = (
    ATTENTION_INFORMATION,
    ATTENTION_WARNING,
    ATTENTION_ERROR,
)


# --- reconciliation with the existing vocabularies -------------------------

# services/item_status.py row badges. Deliberately identical in meaning to
# ``item_status.display_status_label()``; this is the same decision expressed
# as a dimension rather than a second opinion.
_ITEM_STATUS_TO_ACQUISITION: dict[str, str] = {
    "done": ACQUISITION_COMPLETE,
    "complete": ACQUISITION_COMPLETE,
    "completed": ACQUISITION_COMPLETE,
    "acquired": ACQUISITION_COMPLETE,
    "collected": ACQUISITION_COMPLETE,
    "partial": ACQUISITION_INCOMPLETE,
    "incomplete": ACQUISITION_INCOMPLETE,
    "failed": ACQUISITION_FAILED,
    "error": ACQUISITION_FAILED,
    "queued": ACQUISITION_PENDING,
    "pending": ACQUISITION_PENDING,
    "scheduled": ACQUISITION_PENDING,
    "missing": ACQUISITION_UNAVAILABLE,
    "unknown": ACQUISITION_UNAVAILABLE,
    "unavailable": ACQUISITION_UNAVAILABLE,
    "n/a": ACQUISITION_UNAVAILABLE,
    # An orphaned failed tilt series that no batch claims. It is genuinely
    # unavailable as *acquisition* data for a batch; its link confidence is
    # what makes it special, and that is a different dimension.
    "unattributed": ACQUISITION_UNAVAILABLE,
}

_SEVERITY_TO_ATTENTION: dict[str, str] = {
    "info": ATTENTION_INFORMATION,
    "information": ATTENTION_INFORMATION,
    "warning": ATTENTION_WARNING,
    "warn": ATTENTION_WARNING,
    "error": ATTENTION_ERROR,
    "critical": ATTENTION_ERROR,
}

# services/navigation_service.py resolution states.
_RESOLUTION_TO_LINK: dict[str, str] = {
    "navigable": LINK_LINKED,
    "ambiguous": LINK_AMBIGUOUS,
    "unresolved": LINK_UNRESOLVED,
}


def acquisition_state(status: str | None) -> str:
    """Map any existing status string onto the acquisition dimension."""

    return _ITEM_STATUS_TO_ACQUISITION.get(
        (status or "").strip().lower(), ACQUISITION_UNAVAILABLE
    )


def attention_state(severity: str | None) -> str:
    """Map a warning severity onto the attention dimension."""

    return _SEVERITY_TO_ATTENTION.get(
        (severity or "").strip().lower(), ATTENTION_WARNING
    )


def link_state(resolution_state: str | None, *, inferred: bool = False) -> str:
    """Map a navigation resolution onto the link-confidence dimension.

    ``inferred`` covers the conservative failed-orphan batch label, which is a
    display grouping rather than a resolved link and must never read as one.
    """

    if inferred:
        return LINK_INFERRED
    return _RESOLUTION_TO_LINK.get(
        (resolution_state or "").strip().lower(), LINK_UNRESOLVED
    )


def application_state(*, loading: bool, failed: bool = False, warnings: int = 0) -> str:
    """Describe how loading itself went, independently of the science."""

    if loading:
        return APPLICATION_LOADING
    if failed:
        return APPLICATION_LOAD_FAILED
    if warnings > 0:
        return APPLICATION_LOAD_WARNING
    return APPLICATION_READY


def is_actionable_attention(attention: str) -> bool:
    """Whether this level warrants surfacing an action to the reviewer."""

    return attention in (ATTENTION_WARNING, ATTENTION_ERROR)


# The inverse of ``attention_state``, for the widgets that still colour and
# order by the raw severity string. Kept here so there is one place where the
# two vocabularies meet, rather than a private substring heuristic in a widget
# quietly forming a third opinion — which is exactly what this module exists to
# prevent.
_ATTENTION_TO_SEVERITY: dict[str, str] = {
    ATTENTION_INFORMATION: "info",
    ATTENTION_WARNING: "warning",
    ATTENTION_ERROR: "error",
}


def severity_for_attention(attention: str | None) -> str:
    """Raw severity string for an attention level."""

    return _ATTENTION_TO_SEVERITY.get(attention or "", "warning")


# --- the shared warning presentation ---------------------------------------


@dataclass(frozen=True, slots=True)
class WarningPresentation:
    """One warning, shaped for display — on the dashboard *and* in the PDF.

    Display-only. It never rewrites a parser warning and never invents a
    scientific consequence: ``condition`` and ``raw_details`` carry the
    parser's own words, and ``why_it_matters`` carries only the explanation
    the existing classifier already attached to that category.

    ``destination`` and ``safe_filter`` are how an aggregate drills into the
    objects it is about; ``disabled_explanation`` is why it cannot, when it
    cannot. Both are present so that a warning is never a dead end without a
    stated reason.
    """

    condition: str
    attention: str = ATTENTION_WARNING
    affected_count: int = 0
    affected_scope: str = ""
    why_it_matters: str = ""
    evidence: tuple[str, ...] = ()
    next_action: str = ""
    destination: str = ""
    safe_filter: str = ""
    disabled_explanation: str = ""
    raw_details: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_actionable(self) -> bool:
        """True when the reviewer can actually get to the affected objects."""

        return bool(self.destination or self.safe_filter) and not self.disabled_explanation

    @property
    def summary_line(self) -> str:
        """One-line rendering, e.g. ``Failed tilt series collection — 3 items``."""

        if self.affected_count:
            noun = "item" if self.affected_count == 1 else "items"
            return f"{self.condition} — {self.affected_count} {noun}"
        return self.condition

    def with_action(
        self,
        *,
        next_action: str | None = None,
        destination: str | None = None,
        safe_filter: str | None = None,
        disabled_explanation: str | None = None,
    ) -> "WarningPresentation":
        """Attach a drill-down, or the reason there cannot be one.

        Fields default to ``None`` meaning "leave as it was", so passing ``""``
        genuinely clears one. An earlier version coalesced with ``or``, which
        made ``disabled_explanation`` impossible to remove: a warning that
        later acquired a destination would have kept a stale reason and stayed
        permanently inert.
        """

        return WarningPresentation(
            condition=self.condition,
            attention=self.attention,
            affected_count=self.affected_count,
            affected_scope=self.affected_scope,
            why_it_matters=self.why_it_matters,
            evidence=self.evidence,
            next_action=self.next_action if next_action is None else next_action,
            destination=self.destination if destination is None else destination,
            safe_filter=self.safe_filter if safe_filter is None else safe_filter,
            disabled_explanation=(
                self.disabled_explanation
                if disabled_explanation is None
                else disabled_explanation
            ),
            raw_details=self.raw_details,
        )


def build_warning_presentation(
    *,
    condition: str,
    severity: str,
    affected_count: int = 0,
    affected_scope: str = "",
    explanation: str = "",
    examples: tuple[str, ...] | list[str] = (),
    raw_details: tuple[str, ...] | list[str] = (),
) -> WarningPresentation:
    """Build a presentation from an already-classified warning group.

    Takes the classifier's own output rather than re-deriving it, so the
    dashboard and the PDF cannot disagree about what a warning means.
    """

    return WarningPresentation(
        condition=condition,
        attention=attention_state(severity),
        affected_count=affected_count,
        affected_scope=affected_scope,
        why_it_matters=explanation,
        evidence=tuple(examples),
        raw_details=tuple(raw_details) or tuple(examples),
    )


def sort_presentations(
    presentations: list[WarningPresentation] | tuple[WarningPresentation, ...],
) -> list[WarningPresentation]:
    """Order by attention, then by how much is affected.

    Errors first, then warnings, then information; within a level the larger
    problem leads. This is the order the dashboard and the report cover should
    both use.
    """

    rank = {level: index for index, level in enumerate(reversed(ATTENTION_STATES))}
    return sorted(
        presentations,
        key=lambda item: (rank.get(item.attention, 1), -item.affected_count, item.condition),
    )
