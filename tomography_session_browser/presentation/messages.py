"""Concrete, Qt-free prompt contracts. Copy never carries operation state."""
from collections.abc import Hashable
from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, TypeVar

Identity = TypeVar("Identity", bound=Hashable)


class ActionRole(StrEnum):
    SAFE = "safe"
    PRIMARY = "primary"
    DESTRUCTIVE = "destructive"


@dataclass(frozen=True, slots=True)
class MessageContent:
    title: str
    consequence: str
    items: tuple[str, ...] = ()
    details: str = ""

    def copy_text(self) -> str:
        return "\n\n".join(filter(None, (self.title, self.consequence, "\n".join(self.items), self.details)))


@dataclass(frozen=True, slots=True)
class MessageAction(Generic[Identity]):
    id: Identity
    label: str
    role: ActionRole = ActionRole.SAFE


@dataclass(frozen=True, slots=True)
class ActionPrompt(Generic[Identity]):
    content: MessageContent
    actions: tuple[MessageAction[Identity], ...]
    default_action_id: Identity
    escape_action_id: Identity

    def __post_init__(self) -> None:
        actions = {action.id: action for action in self.actions}
        if len(actions) != len(self.actions):
            raise ValueError("Prompt action identities must be unique.")
        for identity in (self.default_action_id, self.escape_action_id):
            if identity not in actions or actions[identity].role != ActionRole.SAFE:
                raise ValueError("Default and Escape must name explicit safe actions.")


@dataclass(frozen=True, slots=True)
class Cancelled:
    pass


@dataclass(frozen=True, slots=True)
class TextAccepted:
    value: str


@dataclass(frozen=True, slots=True)
class ItemChoice(Generic[Identity]):
    id: Identity
    label: str


@dataclass(frozen=True, slots=True)
class ItemAccepted(Generic[Identity]):
    item_id: Identity
