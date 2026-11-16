"""The Atlas tab's one owner of the colour mode and what it colours (batch 4).

Qt-free. ``ViewerCoordinator`` keeps one and installs it with the collection
providers; the Atlas tab's ``TabSelection.atlas_overlay`` points to it, and it
is ``None`` on every other tab. The Positions card reads and writes the mode only
through this state.

- **Reset.** The mode returns to Status only when the workspace is replaced, so
  a scope change and a collection toggle keep it.
- **Generation.** It counts every new marker set, workspace and mode, so a
  result made for an older one can be told apart.
- **Fallback.** A mode that ``AtlasColouring.build`` finds ``Unavailable`` for
  the markers shown returns to Status, and ``notice`` keeps the reason for the
  card to show.
- **Tags** (batch 6). ``tags_provider`` gives, for a processing folder, a
  lookup of its tomograms' saved tags in the open tag projects. The state asks
  it once per folder and the lookup once per tomogram, and keeps the answers
  until ``tags_changed``, which drops every tag-derived result and counts a
  generation.
- **Thread.** The build runs on the GUI thread: ``tools/atlas_colouring_bench.py``
  measures it under 50 ms on 1,000 positions (``docs/PERFORMANCE.md``).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from tomography_session_browser.domain.markers import ImageMarker
from tomography_session_browser.presentation.atlas_colouring import (
    STATUS,
    AtlasColouring,
    ColourMode,
    ColouringSources,
    Findings,
    TagsOf,
    TomogramTagState,
    Unavailable,
    find_positions,
    mode_name,
    offered_modes,
)
from tomography_session_browser.services.markers.builders import AtlasMarkerSet, atlas_lod_marker_set

if TYPE_CHECKING:
    from tomography_session_browser.domain.models import Atlas
    from tomography_session_browser.domain.tomograms import ProcessingSource, Tomogram
    from tomography_session_browser.services.markers.context import MarkerContext


#: Marker sets kept at once: the tab's memo holds one list, a jump planner may ask for another.
SETS_KEPT = 4


class AtlasOverlayState:
    def __init__(self) -> None:
        self.workspace: Any | None = None
        self.mode: ColourMode = STATUS
        self.generation = 0
        #: Why the last mode returned to Status, until the mode is next chosen.
        self.notice: str | None = None
        #: The marker sets built lately, by the id of their list: the item shown and
        #: any other Atlas a caller asked about since (``SETS_KEPT``).
        self.marker_sets: dict[int, AtlasMarkerSet] = {}
        self._sources: ColouringSources | None = None
        self._colouring: tuple[AtlasMarkerSet, ColourMode, AtlasColouring] | None = None
        #: Each position's tomograms for the last marker set: shared by every mode.
        self._findings: tuple[AtlasMarkerSet, Findings] | None = None
        self._offered: tuple[AtlasMarkerSet, tuple[tuple[ColourMode, str | None], ...]] | None = None
        #: How many colourings were built: a test sees that Status mode builds none.
        self.build_count = 0
        #: A processing folder's tag lookup, or ``None`` with no open project (``TagProjects.atlas_tags``).
        self.tags_provider: Callable[[ProcessingSource], TagsOf | None] | None = None
        #: The answers by tomogram id, and the lookups by folder id, until ``tags_changed``.
        self._tags: dict[str, TomogramTagState | None] = {}
        self._lookups: dict[str, TagsOf | None] = {}
        self._folders: dict[str, ProcessingSource] | None = None

    def install(self, workspace: Any) -> None:
        """The tabs are installed for ``workspace``: a new workspace resets the mode."""

        if workspace is not self.workspace:
            self.workspace = workspace
            self.mode = STATUS
            self.notice = None
            self._sources = None
            self._folders = None
        self._tags = {}
        self._lookups = {}
        self._forget()

    def _forget(self) -> None:
        self.marker_sets = {}
        self._findings = None
        self._colouring = None
        self._offered = None
        self.generation += 1

    def markers(self, atlas: Atlas, context: MarkerContext, **options: Any) -> list[ImageMarker]:
        """The Atlas's markers (``atlas_lod_marker_set``), kept with their members."""

        marker_set = atlas_lod_marker_set(atlas, context, **options)
        while len(self.marker_sets) >= SETS_KEPT:
            del self.marker_sets[next(iter(self.marker_sets))]
        self.marker_sets[id(marker_set.markers)] = marker_set
        self.generation += 1
        return marker_set.markers

    def sources(self) -> ColouringSources | None:
        workspace = self.workspace
        if workspace is None:
            return None
        if self._sources is None:
            self._sources = ColouringSources.build(
                workspace.tomogram_placement, workspace.tomogram_links, workspace.processing_sources)
        return self._sources

    def tags_of(self, row: Tomogram) -> TomogramTagState | None:
        """``row``'s saved tags, or ``None`` when no open tag project lists it."""

        if row.id in self._tags:
            return self._tags[row.id]
        provider, workspace = self.tags_provider, self.workspace
        state = None
        if provider is not None and workspace is not None:
            if self._folders is None:
                self._folders = {folder.id: folder for folder in workspace.processing_sources}
            if row.source_id not in self._lookups:
                folder = self._folders.get(row.source_id)
                self._lookups[row.source_id] = provider(folder) if folder is not None else None
            lookup = self._lookups[row.source_id]
            state = lookup(row) if lookup is not None else None
        self._tags[row.id] = state
        return state

    def tags_changed(self) -> None:
        """A tag project opened, closed, saved or was edited: forget every tag-derived result."""

        self._tags = {}
        self._lookups = {}
        self._folders = None
        self._colouring = None
        self._offered = None
        self.generation += 1

    def _set_for(self, markers: list[ImageMarker]) -> AtlasMarkerSet | None:
        marker_set = self.marker_sets.get(id(markers))
        return marker_set if marker_set is not None and marker_set.markers is markers else None

    def findings(self, marker_set: AtlasMarkerSet, sources: ColouringSources) -> Findings:
        memo = self._findings
        if memo is None or memo[0] is not marker_set:
            memo = self._findings = (marker_set, find_positions(marker_set, sources))
        return memo[1]

    def colouring_for(self, markers: list[ImageMarker]) -> AtlasColouring | None:
        """The colouring of ``markers`` in the mode chosen; ``None`` draws status colours."""

        if self.mode == STATUS:
            return None
        marker_set = self._set_for(markers)
        sources = self.sources()
        if marker_set is None or sources is None:
            return None
        memo = self._colouring
        if memo is not None and memo[0] is marker_set and memo[1] == self.mode:
            return memo[2]
        built = AtlasColouring.build(marker_set, sources, self.mode, self.findings(marker_set, sources), self.tags_of)
        self.build_count += 1
        if isinstance(built, Unavailable):
            self.notice = f"{mode_name(self.mode)}: {built.reason}"
            self.mode = STATUS
            self.generation += 1
            return None
        self._colouring = (marker_set, self.mode, built)
        return built

    def offered(self, markers: list[ImageMarker]) -> tuple[tuple[ColourMode, str | None], ...]:
        """Every mode for ``markers``, with the reason it is left out; asked when the menu opens."""

        marker_set = self._set_for(markers)
        sources = self.sources()
        if marker_set is None or sources is None:
            return ((STATUS, None),)
        memo = self._offered
        if memo is not None and memo[0] is marker_set:
            return memo[1]
        offered = offered_modes(marker_set, sources, self.findings(marker_set, sources), self.tags_of)
        self._offered = (marker_set, offered)
        return offered

    def set_mode(self, mode: ColourMode) -> bool:
        """Choose ``mode``; ``True`` when it changed."""

        self.notice = None
        if mode == self.mode:
            return False
        self.mode = mode
        self._colouring = None
        self.generation += 1
        return True
