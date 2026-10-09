"""Contracts components for the motion."""
from __future__ import annotations


from typing import Protocol, runtime_checkable


@runtime_checkable
class Animation(Protocol):
    """The shared frame ticker's contract; it owns no scientific values."""

    @property
    def settled(self) -> bool: ...

    def step(self, dt: float) -> None: ...

    def notify_settled(self) -> None: ...
