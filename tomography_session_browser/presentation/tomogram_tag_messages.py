"""Tag prompt identities and recovery rows, independent of Qt and widget state."""
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.presentation.messages import MessageContent
from tomography_session_browser.services.tomogram_tags.binding import MoveProposal, ProjectKey


@dataclass(frozen=True, slots=True)
class MoveAccepted:
    proposal: MoveProposal


class ProjectRoute(StrEnum):
    CREATE = "create"


class RecoveryAction(StrEnum):
    KEEP_WORKING = "keep_working"
    RETRY_ALL = "retry_all"
    CLOSE_UNRESOLVED = "close_unresolved"


@dataclass(frozen=True, slots=True)
class SaveProjectAs:
    project: ProjectKey


@dataclass(frozen=True, slots=True)
class RecoveryProject:
    key: ProjectKey
    path: Path
    unsaved: bool
    bindings: tuple[Path, ...]
    reason: str
    can_save_as: bool


def recovery_content(projects: tuple[RecoveryProject, ...]) -> MessageContent:
    unsaved = sum(p.unsaved for p in projects)
    binding = sum(bool(p.bindings) for p in projects)
    title = "Tag projects need attention" if binding else f"Tag changes in {count_phrase(unsaved, 'project')} are not saved"
    consequences = []
    if unsaved:
        consequences.append(f"Edits in {count_phrase(unsaved, 'project')} are not saved.")
    if binding:
        consequences.append(f"Folder bindings for {count_phrase(binding, 'project')} could not be recorded.")
    consequences.append("Keep this window open to resolve them. Successfully saved project files remain saved.")
    details = []
    for project in projects:
        details.extend((str(project.path), "Unsaved edits" if project.unsaved else "Project saved", project.reason))
        details.extend("Folder binding not recorded: " + str(folder) for folder in project.bindings)
    return MessageContent(title, " ".join(consequences), details="\n".join(filter(None, details)))


def recovery_close_label(projects: tuple[RecoveryProject, ...]) -> str:
    scope = count_phrase(len(projects), "project")
    return f"Close without resolving {scope}" if any(p.bindings for p in projects) else f"Discard changes in {scope} and close"
