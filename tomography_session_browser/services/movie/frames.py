"""Composed movie frames in timeline order, read without filling the shared plane cache."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace

import numpy as np

from tomography_session_browser.services.movie.compose import annotate_pane, compose
from tomography_session_browser.services.movie.models import MoviePane, MovieRequest
from tomography_session_browser.services.volume.models import RenderResult
from tomography_session_browser.services.volume.render import render_xy


class FrameSource:
    """Only the sweeping pane changes between frames. The other Ortho panes are
    rendered once; a held frame is composed once and sent again for each repeat."""

    def __init__(self, request: MovieRequest, cancelled: Callable[[], bool] = lambda: False) -> None:
        self.request, self.cancelled = request, cancelled
        self.companions: dict[str, np.ndarray] = {}
        self.last: tuple[int, np.ndarray] | None = None
        #: The first render of each pane, without pixels, for the manifest's provenance.
        self.results: dict[str, RenderResult] = {}

    def pane_image(self, pane: MoviePane, plane: int | None = None) -> np.ndarray:
        recipe = pane.recipe if plane is None else replace(pane.recipe, plane=plane)
        result = render_xy(self.request.volume, recipe, cancelled=self.cancelled, include_histogram=False,
                           share_planes=False)
        try:
            pixels, label = annotate_pane(self.request.volume, pane, result.pixels, self.request.scale_bar,
                                          self.request.geometry.ratio)
        finally:
            if result.buffer_lease is not None:
                result.buffer_lease.close()
        self.results.setdefault(recipe.axis, replace(result, pixels=np.empty((0, 0), np.uint8), navigator=None,
                                                     buffer_lease=None, bar_label=label))
        return pixels

    def frame(self, plane: int) -> np.ndarray:
        if self.last is not None and self.last[0] == plane:
            return self.last[1]
        images = {}
        for pane in self.request.panes:
            axis = pane.recipe.axis
            if axis == self.request.sweep:
                images[axis] = self.pane_image(pane, plane)
            else:
                if axis not in self.companions:
                    self.companions[axis] = self.pane_image(pane)
                images[axis] = self.companions[axis]
        composed = compose(self.request, images, plane)
        self.last = (plane, composed)
        return composed

    def frames(self, start: int = 0, stop: int | None = None) -> Iterator[tuple[int, np.ndarray]]:
        timeline = self.request.timeline
        for index in range(start, timeline.count if stop is None else min(stop, timeline.count)):
            if self.cancelled():
                raise InterruptedError("Movie export cancelled")
            yield index, self.frame(timeline.frames[index].plane)

    def working_bytes(self) -> int:
        """Composition buffers and the reused companion images, for admission."""
        width, height = self.request.geometry.frame
        panes = sum(pane.raster.image[2] * pane.raster.image[3] * 3 for pane in self.request.panes)
        return width * height * 3 * 4 + panes * 2
