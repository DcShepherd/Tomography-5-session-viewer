"""Explicit canvas and tab component graphs."""
from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tomography_session_browser.ui.viewer.preview_camera import PreviewCamera
    from tomography_session_browser.ui.viewer.preview_image import PreviewImage
    from tomography_session_browser.ui.viewer.marker_layer import MarkerLayer
    from tomography_session_browser.ui.viewer.preview_interaction import PreviewInteraction
    from tomography_session_browser.ui.viewer.viewer_header import ViewerHeader
    from tomography_session_browser.ui.viewer.overlay_panel import OverlayPanel
    from tomography_session_browser.ui.viewer.canvas_controls import CanvasControls
    from tomography_session_browser.ui.viewer.frame_scrubber import FrameScrubber
    from tomography_session_browser.ui.viewer.mrc_loading import PreviewLoader
    from tomography_session_browser.ui.viewer.export import ViewerExport
    from tomography_session_browser.ui.viewer.tab_selection import TabSelection
    from tomography_session_browser.ui.viewer.list_pane import ViewerListPane


@dataclass(frozen=True)
class PreviewParts:
    marker_layer: MarkerLayer
    preview_camera: PreviewCamera
    preview_image: PreviewImage
    preview_interaction: PreviewInteraction


@dataclass(frozen=True)
class TabParts:
    canvas_controls: CanvasControls
    export: ViewerExport
    frame_scrubber: FrameScrubber
    list_pane: ViewerListPane
    mrc_loading: PreviewLoader
    overlay_panel: OverlayPanel
    tab_selection: TabSelection
    viewer_header: ViewerHeader
