"""Models for the image viewer."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ViewerNavigationAction:
    key: str
    label: str
    enabled: bool = True
    tooltip: str = ""


@dataclass(frozen=True, slots=True)
class AtlasCollectionOverlayOption:
    key: str
    label: str
    visible: bool = True


@dataclass(frozen=True, slots=True)
class ViewerViewState:
    """What a viewer tab must remember across a rebuild.

    Identifiers and scalars only — never entities, pixmaps or markers — so a
    cache of these cannot keep a removed session alive or grow with image data.
    """

    object_id: str | None = None
    marker_id: str | None = None
    frame_index: int | None = None
    filter_text: str = ""

    @property
    def is_empty(self) -> bool:
        return self.object_id is None and not self.filter_text


def viewer_object_id(value: Any) -> str | None:
    identifier = getattr(value, "id", None)
    return str(identifier) if identifier else None

