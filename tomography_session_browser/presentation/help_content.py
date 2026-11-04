"""Semantic Help records and search; no widgets, bindings or scientific inference."""
from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import StrEnum

from tomography_session_browser.presentation.tab_keys import TabKey


class HelpPage(StrEnum):
    START = "start"
    READING = "reading"
    KEYBOARD = "keyboard"
    FILES = "files"


PAGE_LABELS = {
    HelpPage.START: "Start here",
    HelpPage.READING: "Reading the project",
    HelpPage.KEYBOARD: "Keyboard and mouse",
    HelpPage.FILES: "Files and safety",
}


class InteractionKind(StrEnum):
    KEYBOARD = "keyboard"
    MOUSE = "mouse"


class BindingScope(StrEnum):
    WINDOW = "window"
    CONTROL = "control"
    TAB = "tab"
    APPLICATION = "application"
    LOCAL = "local"


@dataclass(frozen=True, slots=True)
class LocalBinding:
    """A widget's existing handler metadata, before its catalogue supplies tabs."""
    identity: str
    context: str
    kind: InteractionKind
    tokens: tuple[str, ...]
    description: str


@dataclass(frozen=True, slots=True)
class HelpBinding:
    identity: str
    tabs: frozenset[TabKey]
    context: str
    scope: BindingScope
    kind: InteractionKind
    tokens: tuple[str, ...]
    description: str


def normalise_bindings(bindings: Iterable[HelpBinding]) -> tuple[HelpBinding, ...]:
    """Merge exact semantic aliases before formatting; preserve distinct contexts."""
    result: dict[tuple[str, str, BindingScope, InteractionKind, tuple[str, ...]], HelpBinding] = {}
    for binding in bindings:
        tokens = tuple(dict.fromkeys("+".join({"Return": "Enter", "Escape": "Esc"}.get(part, part)
            for part in token.split("+")) for token in binding.tokens))
        normal = replace(binding, tokens=tokens)
        key = (normal.identity, normal.context, normal.scope, normal.kind, normal.tokens)
        if key in result:
            previous = result[key]
            if previous.description != normal.description:
                raise ValueError(f"Conflicting Help copy for binding {normal.identity}")
            normal = replace(normal, tabs=previous.tabs | normal.tabs)
        result[key] = normal
    return tuple(result.values())


def bindings_for_tab(bindings: Iterable[HelpBinding], tab: TabKey | None) -> tuple[HelpBinding, ...]:
    return tuple(binding for binding in bindings if tab is None or tab in binding.tabs)


@dataclass(frozen=True, slots=True)
class HelpSection:
    identity: str
    page: HelpPage
    title: str
    paragraphs: tuple[str, ...]
    badge: str = ""


@dataclass(frozen=True, slots=True)
class HelpSearchHit:
    page: HelpPage
    section_id: str
    title: str
    context: str
    text: str
    binding: HelpBinding | None = None


def search_help(query: str, sections: Iterable[HelpSection], bindings: Iterable[HelpBinding]) -> tuple[HelpSearchHit, ...]:
    words = query.casefold().split()
    if not words:
        return ()
    hits: list[HelpSearchHit] = []
    for section in sections:
        text = " ".join((PAGE_LABELS[section.page], section.title, *section.paragraphs))
        if all(word in text.casefold() for word in words):
            hits.append(HelpSearchHit(section.page, section.identity, section.title,
                                      PAGE_LABELS[section.page], " ".join(section.paragraphs)))
    for binding in normalise_bindings(bindings):
        text = " ".join((binding.context, *binding.tokens, binding.description, *(tab.value for tab in binding.tabs)))
        if all(word in text.casefold() for word in words):
            hits.append(HelpSearchHit(HelpPage.KEYBOARD, binding.identity, binding.description,
                binding.context, " / ".join(binding.tokens), binding))
    return tuple(hits)
