"""Models for the image viewer."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ViewerNavigationAction:
    key: str
    label: str
    enabled: bool = True
    tooltip: str = ""
    selection_required: bool = False


@dataclass(frozen=True, slots=True)
class AtlasCollectionOverlayOption:
    key: str
    label: str
    visible: bool = True


@dataclass(frozen=True, slots=True)
class OverlayControl:
    """One row of the Overlays panel: a check box that sets what it ``toggles``.

    ``toggles`` are marker types or Atlas LOD keys; a row naming several sets
    them together. ``hint`` is the one line under the label that says how the
    layer behaves. ``parent`` is the label of the row this one sits under.
    ``swatch`` names the small drawing of the layer beside the label
    (``ui/viewer/overlay_swatches``).
    """

    label: str
    toggles: tuple[str, ...]
    hint: str = ""
    parent: str = ""
    swatch: str = ""


@dataclass(frozen=True, slots=True)
class OverlaySection:
    title: str
    controls: tuple[OverlayControl, ...]


@dataclass(frozen=True, slots=True)
class ViewerViewState:
    """What a viewer tab must remember across a rebuild.

    Identifiers and scalars only — never entities, pixmaps or markers — so a
    cache of these cannot keep a removed session alive or grow with image data.
    """

    #: ``entity_key_text`` of the selected object: never a bare ``.id``.
    object_key: str | None = None
    marker_id: str | None = None
    frame_index: int | None = None
    filter_text: str = ""
    filter_keys: frozenset[str] | None = None
    filter_scope_label: str = ""
    #: The object row at the top of the list and where its top edge sat
    #: (``RowIndex.anchor``): always an object row, never a header.
    list_anchor_key: str | None = None
    list_anchor_offset: int = 0
    #: Last listed object while an unlisted object occupies the page. This is
    #: separate from object_key, which still identifies what the page shows.
    list_selection_key: str | None = None

    @property
    def is_empty(self) -> bool:
        return self.object_key is None and not self.filter_text and self.filter_keys is None and self.list_anchor_key is None and self.list_selection_key is None

