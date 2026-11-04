"""Copy and surfaces derived from the authoritative load-batch result."""
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.presentation.empty_states import (
    COMMAND_CHOOSE_ANOTHER, COMMAND_COPY_DETAILS, COMMAND_RETRY,
    KIND_LOAD_FAILED, EmptyState, EmptyStateAction,
)
from tomography_session_browser.services.load_results import (
    FailureCategory, LoadBatchResult, LoadFailure, LoadIntent, LoadOutcome, OutcomeKind,
)
from tomography_session_browser.services.session_intake import ALREADY_LOADED_REASON


def failure_reason(failure: LoadFailure) -> str:
    return {
        FailureCategory.MISSING: "This folder could not be found",
        FailureCategory.UNREADABLE: "This folder could not be read",
        FailureCategory.UNREACHABLE: "This location could not be reached",
        FailureCategory.PREPARATION: "The app could not prepare this folder for review",
        FailureCategory.PUBLICATION: "The app could not prepare this folder for review",
        FailureCategory.UNKNOWN: "This folder could not be loaded",
    }[failure.category]


@dataclass(frozen=True, slots=True)
class LoadResultLine:
    name: str
    reason: str
    path: str


@dataclass(frozen=True, slots=True)
class LoadResultSection:
    title: str
    lines: tuple[LoadResultLine, ...]
    detail: str = ""


@dataclass(frozen=True, slots=True)
class LoadResultContent:
    title: str
    sections: tuple[LoadResultSection, ...]
    details: str
    can_retry: bool
    toast: str | None


def _notified_publications(result: LoadBatchResult) -> tuple[LoadOutcome, ...]:
    # Retry is an explicit user action, even when the original refresh was quiet.
    return tuple(item for item in result.outcomes if item.published
                 and (not item.attempt.request.quiet or item.attempt.number > 1))


class ResultSurface(StrEnum):
    NONE = "none"
    CANVAS = "canvas"
    RECEIPT = "receipt"
    TOAST = "toast"


def result_surface(result: LoadBatchResult, *, has_sources: bool) -> ResultSurface:
    if result.running:
        return ResultSurface.NONE
    if result.unresolved:
        return ResultSurface.RECEIPT if has_sources else ResultSurface.CANVAS
    if _notified_publications(result):
        return ResultSurface.TOAST
    return ResultSurface.NONE


def load_result_content(result: LoadBatchResult) -> LoadResultContent:
    failed = tuple(item for item in result.outcomes if item.kind == OutcomeKind.FAILED)
    retained = tuple(item for item in result.outcomes if item.kind == OutcomeKind.RETAINED)
    skipped = tuple(LoadResultLine(item.name, item.reason, item.path) for item in result.admission_skips)
    skipped += tuple(LoadResultLine(item.name, ALREADY_LOADED_REASON, item.attempt.request.path)
                     for item in result.outcomes if item.kind == OutcomeKind.SKIPPED)
    sections: list[LoadResultSection] = []
    if failed:
        sections.append(LoadResultSection(f"{count_phrase(len(failed), 'folder')} could not be loaded",
            tuple(LoadResultLine(item.name, failure_reason(item.failure), item.attempt.request.path)
                  for item in failed if item.failure is not None)))
    if skipped:
        sections.append(LoadResultSection(f"{count_phrase(len(skipped), 'folder')} skipped", skipped))
    if retained:
        sections.append(LoadResultSection(f"{count_phrase(len(retained), 'session')} could not be reloaded",
            tuple(LoadResultLine(item.name, failure_reason(item.failure), item.attempt.request.path)
                  for item in retained if item.failure is not None),
            "It shows what was loaded before the refresh." if len(retained) == 1
            else "They show what was loaded before the refresh."))
    reload_only = bool(result.requests) and all(request.intent == LoadIntent.RELOAD for request in result.requests)
    title = f"{'Reloaded' if reload_only else 'Loaded'} {count_phrase(result.published_count, 'session')}"
    diagnostics = [title]
    for item in result.outcomes:
        diagnostics.append(f"{item.kind.value}: {item.name}\nPath: {item.attempt.request.path}")
        if item.failure is not None:
            diagnostics.append(f"Stage: {item.failure.stage.value}\nReason: {failure_reason(item.failure)}\n{item.failure.diagnostic}")
    diagnostics.extend(f"Skipped: {item.name}\nPath: {item.path}\n{item.reason}" for item in result.admission_skips)
    notifications = _notified_publications(result)
    toast = None
    if not result.unresolved and not result.running and notifications:
        if len(notifications) == 1:
            item = notifications[0]
            verb = "Reloaded" if item.kind == OutcomeKind.RELOADED else "Loaded" if item.replaced_project else "Imported"
            toast = f"{verb} {item.name}"
        else:
            toast = title
    return LoadResultContent(title, tuple(sections), "\n\n".join(diagnostics),
        not result.running and any(item.retryable for item in result.outcomes), toast)


def load_result_state(result: LoadBatchResult) -> EmptyState:
    content = load_result_content(result)
    return EmptyState(kind=KIND_LOAD_FAILED,
        title=content.sections[0].title if content.sections else "No folders loaded",
        detail="Review the folders below. Source files have not changed.",
        primary=EmptyStateAction("Retry failed folders", COMMAND_RETRY) if content.can_retry
            else EmptyStateAction("Choose another folder", COMMAND_CHOOSE_ANOTHER),
        secondary=((EmptyStateAction("Choose another folder", COMMAND_CHOOSE_ANOTHER),) if content.can_retry else ())
            + (EmptyStateAction("Copy details", COMMAND_COPY_DETAILS),),
        evidence=tuple(f"{line.name} — {line.reason}" for section in content.sections for line in section.lines),
        copyable_details=content.details)
