"""Canvas controls for the image viewer."""
from __future__ import annotations
import math
from pathlib import Path
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QLayout, QPushButton, QVBoxLayout, QWidget
from tomography_session_browser.ui.widgets.empty_state_panel import EmptyStatePanel
from tomography_session_browser.ui.widgets.canvas_shell import CanvasShell
from tomography_session_browser.ui.motion import live_section_reveal
from tomography_session_browser.services.pixel_size import scale_bar_pixel_size
from tomography_session_browser.ui.icons import themed_icon
from tomography_session_browser.presentation.design_tokens import CANVAS_CHROME, SPACE_M, SPACE_S, SPACE_XS
import logging
LOGGER = logging.getLogger("tomography_session_browser.ui.image_viewer")
from tomography_session_browser.ui.viewer.atlas_lod_items import AtlasMarkerLegend
from tomography_session_browser.ui.viewer.constants import BUTTON_ZOOM_STEP, FLOATING_CONTROL_BOTTOM_INSET_PX, FLOATING_CONTROL_INSET_PX, FLOATING_CONTROL_SIZE_PX, MAX_SCALE_BAR_SCREEN_PX, MIN_SCALE_BAR_SCREEN_PX, PYRAMID_LEVELS, QWIDGETSIZE_MAX, ZOOM_LABEL_MIN_WIDTH_PX, ZOOM_PANEL_HORIZONTAL_PADDING_PX
from tomography_session_browser.ui.viewer.preview_image import source_image_size_for_item
from tomography_session_browser.ui.viewer.scale_bar import ScaleBarWidget, format_pixel_size_label, format_scale_length_label, nice_scale_length_meters
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from tomography_session_browser.ui.viewer.viewer_tab import ViewerTab


class CanvasControls:
    """Canvas controls operations for one ViewerTab."""

    def __init__(self, view: ViewerTab) -> None:
        self.view = view

    def create_canvas(self, zoom_label_width: int, atlas_lod: bool, show_tilt_controls: bool) -> QWidget:
        viewer_panel = QWidget(self.view)
        viewer_layout = QVBoxLayout(viewer_panel)
        viewer_layout.setContentsMargins(0, 0, 0, 0)
        viewer_layout.setSpacing(6)
        viewer_shell = CanvasShell(viewer_panel)
        self.view.viewer_shell = viewer_shell
        viewer_shell_layout = QGridLayout(viewer_shell)
        viewer_shell_layout.setContentsMargins(0, 0, 0, 0)
        viewer_shell_layout.setSpacing(0)
        viewer_shell_layout.addWidget(self.view.viewer, 0, 0)
        viewer_shell.set_focus_source(self.view.viewer)

        # Stacked over the canvas so an empty list explains itself with a real
        # panel rather than a bare line of text on a black rectangle. Hidden
        # whenever there is an image to show.
        self.view.empty_state_panel = EmptyStatePanel(viewer_shell)
        self.view.empty_state_panel.action_requested.connect(self.view.empty_state_action_requested)
        self.view.empty_state_panel.setVisible(False)
        viewer_shell_layout.addWidget(self.view.empty_state_panel, 0, 0)

        top_right = QWidget(viewer_shell)
        top_right.setObjectName("viewerTopControls")
        top_right_layout = QVBoxLayout(top_right)
        top_right_layout.setContentsMargins(0, 0, 0, 0)
        top_right_layout.setSpacing(6)
        top_right_row = QHBoxLayout()
        top_right_row.setContentsMargins(0, 0, 0, 0)
        top_right_row.setSpacing(6)
        top_right_row.addWidget(self.view.overlay_button)
        top_right_layout.addLayout(top_right_row)
        top_right_layout.addWidget(self.view.overlay_panel, alignment=Qt.AlignmentFlag.AlignRight)
        top_right_anchor = QWidget(viewer_shell)
        top_right_anchor.setObjectName("viewerFloatingTopRightAnchor")
        top_right_anchor_layout = QVBoxLayout(top_right_anchor)
        top_right_anchor_layout.setContentsMargins(
            0,
            FLOATING_CONTROL_INSET_PX,
            FLOATING_CONTROL_INSET_PX,
            0,
        )
        top_right_anchor_layout.addWidget(top_right)
        viewer_shell_layout.addWidget(
            top_right_anchor,
            0,
            0,
            alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
        )
        self.overlay_anchor = top_right_anchor

        zoom_panel = QWidget(viewer_shell)
        self.view.zoom_panel = zoom_panel
        zoom_panel.setObjectName("viewerZoomPanel")
        zoom_panel.setFixedWidth(
            zoom_label_width + ZOOM_PANEL_HORIZONTAL_PADDING_PX
        )
        zoom_layout = QVBoxLayout(zoom_panel)
        zoom_layout.setContentsMargins(SPACE_XS, SPACE_XS, SPACE_XS, SPACE_XS)
        zoom_layout.setSpacing(SPACE_XS)
        # Export shares the zoom panel (plan C5): it used to float in a box of
        # its own a few pixels above, one more surface for the same corner.
        zoom_layout.addWidget(self.view.export_image_button, alignment=Qt.AlignmentFlag.AlignHCenter)
        divider = QFrame(zoom_panel)
        divider.setObjectName("viewerControlDivider")
        divider.setFrameShape(QFrame.Shape.NoFrame)
        divider.setFixedHeight(1)
        zoom_layout.addWidget(divider)
        zoom_layout.addWidget(self.view.zoom_in_button, alignment=Qt.AlignmentFlag.AlignHCenter)
        zoom_layout.addWidget(self.view.zoom_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        zoom_layout.addWidget(self.view.zoom_out_button, alignment=Qt.AlignmentFlag.AlignHCenter)
        # "Fit" is a zoom action, but it used to live in the opposite corner
        # from the zoom controls. Icon-only here because the panel is narrow;
        # the tooltip carries the name.
        self.view.fit_button.setText("")
        self.view.fit_button.setObjectName("viewerZoomButton")
        self.view.fit_button.setFixedSize(FLOATING_CONTROL_SIZE_PX, FLOATING_CONTROL_SIZE_PX)
        zoom_layout.addWidget(self.view.fit_button, alignment=Qt.AlignmentFlag.AlignHCenter)
        # The two bottom anchors follow the rendered image's edge, which moves
        # on every zoom step of a letterboxed image. They are sized by their
        # content and *moved* by ``_place_bottom_anchors`` rather than laid
        # out in the shell's grid with changing margins: a margin change
        # re-laid out the whole window, and squeezed the zoom stack for a frame
        # while its anchor caught up (plan F5.1).
        zoom_anchor = QWidget(viewer_shell)
        zoom_anchor.setObjectName("viewerFloatingBottomRightAnchor")
        zoom_anchor_layout = QVBoxLayout(zoom_anchor)
        zoom_anchor_layout.setContentsMargins(0, 0, 0, 0)
        zoom_anchor_layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        zoom_anchor_layout.addWidget(zoom_panel)
        self.zoom_anchor = zoom_anchor
        scale_bar_anchor = QWidget(viewer_shell)
        scale_bar_anchor.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            not atlas_lod,
        )
        scale_bar_anchor_layout = QVBoxLayout(scale_bar_anchor)
        scale_bar_anchor_layout.setContentsMargins(0, 0, 0, 0)
        scale_bar_anchor_layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        if atlas_lod:
            scale_bar_anchor_layout.addWidget(self.view.atlas_marker_legend)
            scale_bar_anchor_layout.addSpacing(SPACE_M)
        scale_bar_anchor_layout.addWidget(self.view.scale_bar)
        self.scale_bar_anchor = scale_bar_anchor
        for watched in (viewer_shell, zoom_anchor, scale_bar_anchor):
            watched.installEventFilter(self.view)
        image_badge_anchor = QWidget(viewer_shell)
        image_badge_anchor.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        image_badge_anchor_layout = QVBoxLayout(image_badge_anchor)
        image_badge_anchor_layout.setContentsMargins(
            FLOATING_CONTROL_INSET_PX,
            FLOATING_CONTROL_INSET_PX,
            0,
            0,
        )
        image_badge_anchor_layout.addWidget(self.view.image_badge)
        viewer_shell_layout.addWidget(
            image_badge_anchor,
            0,
            0,
            alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
        )
        viewer_layout.addWidget(viewer_shell, stretch=1)
        if show_tilt_controls:
            # One row (harmonisation A3): the readout beside the scrubber it
            # describes, rather than a boxed line above it.
            frame_row = QHBoxLayout()
            frame_row.setContentsMargins(0, 0, 0, 0)
            frame_row.setSpacing(SPACE_M)
            frame_row.addWidget(self.view.frame_label)
            frame_row.addWidget(self.view.slice_slider, stretch=1)
            viewer_layout.addLayout(frame_row)

        return viewer_panel

    def connect_controls(self) -> None:
        self.view.zoom_in_button.clicked.connect(lambda: self.view.viewer.ease_zoom_by(BUTTON_ZOOM_STEP))
        self.view.zoom_out_button.clicked.connect(lambda: self.view.viewer.ease_zoom_by(1.0 / BUTTON_ZOOM_STEP))
        self.view.fit_button.clicked.connect(self.view.viewer.ease_to_fit)
        self.view.export_image_button.clicked.connect(self.view._export_current_view)
        self.export_shortcut = QShortcut(
            QKeySequence.StandardKey.Save,
            self.view,
        )
        self.export_shortcut.setContext(
            Qt.ShortcutContext.WindowShortcut
        )
        # Each viewer tab owns the same shortcut. Only the visible tab keeps
        # its shortcut enabled, avoiding ambiguity while still allowing
        # Ctrl+S when focus is in a dock or another part of the main window.
        self.export_shortcut.setEnabled(False)
        self.export_shortcut.activated.connect(self.view._export_current_view)

    def create_controls(self, empty_text: str, atlas_lod: bool) -> tuple[QHBoxLayout, int]:
        controls = QHBoxLayout()
        controls.setSpacing(6)
        self.view.zoom_in_button = QPushButton("", self.view)
        self.view.zoom_out_button = QPushButton("", self.view)
        self.view.fit_button = QPushButton("Fit", self.view)
        self.view.export_image_button = QPushButton("", self.view)
        self.view.overlay_button = QPushButton("Overlays", self.view)
        self.view.overlay_button.setCheckable(True)
        self.view.overlay_button.setToolTip("Show overlay controls")
        self.view._set_canvas_control_icons()
        self.view.zoom_label = QLabel("100%", self.view)
        self.view.zoom_label.setObjectName("viewerZoomLabel")
        self.view.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Reserve enough stable width for four-digit values such as 1000%.
        # Resizing this floating strip at each digit boundary is distracting
        # and can make the Fit button appear to jump horizontally.
        zoom_label_width = max(
            ZOOM_LABEL_MIN_WIDTH_PX,
            self.view.zoom_label.fontMetrics().horizontalAdvance("9999%") + 8,
        )
        # Fixed in both directions: Qt passes a label's text change up the
        # layout chain unless its size is fully fixed, and with only the width
        # fixed every zoom step re-laid out and repainted the whole window.
        # An eased wheel zoom (plan F5.1) ran at ~34 fps because of it.
        self.view.zoom_label.ensurePolished()
        self.view.zoom_label.setFixedSize(zoom_label_width, self.view.zoom_label.sizeHint().height())
        self.view.image_badge = QLabel("", self.view)
        self.view.image_badge.setObjectName("viewerImageBadge")
        self.view.image_badge.setVisible(False)
        # Whether there is a badge to show, separate from whether it fits
        # beside Overlays; see ``_fit_top_floating_controls``.
        self.image_badge_wanted = False
        self.overlay_button_compact = False
        self.overlay_button_full_width = 0
        self.view.scale_bar = ScaleBarWidget(self.view)
        self.view.atlas_marker_legend = AtlasMarkerLegend(self.view)
        self.view.atlas_marker_legend.setVisible(atlas_lod)
        self.view.atlas_marker_legend.set_toggle_handler(self.view._set_atlas_legend_expanded)
        self.view.zoom_in_button.setToolTip("Zoom in")
        self.view.zoom_out_button.setToolTip("Zoom out")
        self.view.fit_button.setToolTip("Fit preview to the available space")
        self.view.export_image_button.setToolTip(
            "Export the current image view as a high-quality PNG (Ctrl+S)"
        )
        self.view.zoom_in_button.setAccessibleName("Zoom in")
        self.view.zoom_out_button.setAccessibleName("Zoom out")
        self.view.fit_button.setAccessibleName("Fit preview")
        self.view.export_image_button.setAccessibleName("Export current image view")
        self.view.overlay_button.setAccessibleName("Overlay controls")
        for button in (self.view.zoom_in_button, self.view.zoom_out_button, self.view.fit_button, self.view.overlay_button):
            button.setObjectName("viewerToolButton")
            button.setIconSize(QSize(16, 16))
        for button in (self.view.zoom_in_button, self.view.zoom_out_button):
            button.setObjectName("viewerZoomButton")
            button.setFixedSize(FLOATING_CONTROL_SIZE_PX, FLOATING_CONTROL_SIZE_PX)
        self.view.export_image_button.setObjectName("viewerZoomButton")
        self.view.export_image_button.setFixedSize(FLOATING_CONTROL_SIZE_PX, FLOATING_CONTROL_SIZE_PX)
        self.view.export_image_button.setEnabled(False)
        self.view.overlay_button.setFixedHeight(FLOATING_CONTROL_SIZE_PX)
        self.view.image_badge.setFixedHeight(FLOATING_CONTROL_SIZE_PX)
        # Exceptions only: hidden until a fallback, an unreadable MRC or a
        # warning has something to say (``ListPane.set_status_text``).
        self.view.status = QLabel("", self.view)
        self.view.status.setObjectName("viewerStatus")
        self.view.status.setWordWrap(True)
        self.view.status.setVisible(False)

        return controls, zoom_label_width

    def set_canvas_control_icons(self) -> None:
        """Icons of the controls floating over the canvas, in its chrome's colour.

        The canvas is near-black in both themes, so these controls keep the
        canvas chrome (``design_tokens.CANVAS_CHROME``) whatever the app's theme is;
        the app's icon colour would draw them dark on dark in the light theme.
        """

        colour = CANVAS_CHROME.icon
        self.view.overlay_button.setIcon(themed_icon("layers", color=colour, size=16))
        self.view.export_image_button.setIcon(themed_icon("download", color=colour, size=16))
        self.view.fit_button.setIcon(themed_icon("maximize", color=colour, size=16))
        self.view.zoom_in_button.setIcon(themed_icon("zoom-in", color=colour, size=16))
        self.view.zoom_out_button.setIcon(themed_icon("zoom-out", color=colour, size=16))

    def refresh_theme(self) -> None:
        """Refresh custom-painted and pixmap-based viewer controls after theme changes.

        Recolour only (plan F5.8a). Markers, statuses and validation do not
        depend on the theme, so nothing is derived again: the existing marker
        items are redrawn in the new colours. That redraw waits until the tab
        is shown when it is hidden; icons (cached pixmaps) change at once.
        It used to re-run marker derivation and tilt validation for every tab
        and redraw each tab's markers once per overlay checkbox, 350–570 ms of
        a theme switch.
        """

        self.view._set_canvas_control_icons()
        if self.entity_icon is not None:
            for row in self.view.parts.list_pane.rows.object_rows():
                row.setIcon(0, themed_icon(self.entity_icon, size=16))
        for key, button in self.view.parts.viewer_header.navigation_buttons.items():
            button.setIcon(themed_icon(self.view.parts.viewer_header.navigation_icon_names.get(key, "arrow-right"), size=14))
        # The menu holds jumps both ways, so its own button keeps the plain arrow.
        self.view.parts.viewer_header.navigation_menu_button.setIcon(themed_icon("arrow-right", size=14))
        if self.view.isVisible():
            self.view._recolour_canvas()
        else:
            self.canvas_theme_stale = True

    def recolour_canvas(self) -> None:
        self.canvas_theme_stale = False
        self.view.viewer.refresh_theme()
        self.view.image_badge.update()
        self.view.scale_bar.update()
        self.view.atlas_marker_legend.update()
        self.view.list.viewport().update()
        self.view.overlay_panel.update()
        self.view.update()

    def scale_bar_toggled(self) -> None:
        self.scale_bar_enabled = self.view.scale_bar_checkbox.isChecked()
        self.view._update_scale_bar()

    def floating_control_insets(self) -> tuple[int, int, int]:
        """Return ``(left, right, bottom)`` margins for the overlaid controls.

        Both bottom-corner overlays are positioned from the *rendered image*
        rather than the canvas: on a letterboxed image the canvas corner sits
        in the empty band below the data, which made the scale bar read as
        detached from what it measures.

        Crucially the bottom inset is shared, so the scale bar and the zoom
        stack always sit on one baseline. Anchoring only the scale bar to the
        image (as the first version of this did) left the two overlays
        stepped apart whenever the image did not fill the canvas.
        """

        left = FLOATING_CONTROL_INSET_PX
        right = FLOATING_CONTROL_INSET_PX
        bottom = FLOATING_CONTROL_BOTTOM_INSET_PX
        image_rect = self.view.viewer.rendered_image_rect()
        if image_rect is None:
            return left, right, bottom

        # The anchors and the viewport all span the viewer shell, so the
        # image rect's viewport-local offsets are the margins we want.
        # (Mapping through ``mapTo`` here double-counted the viewport origin
        # and pushed the bar into the middle of the image.)
        viewport = self.view.viewer.viewport().rect()
        left = max(FLOATING_CONTROL_INSET_PX, image_rect.left() + FLOATING_CONTROL_INSET_PX)
        right = max(
            FLOATING_CONTROL_INSET_PX,
            (viewport.right() - image_rect.right()) + FLOATING_CONTROL_INSET_PX,
        )
        bottom = max(
            FLOATING_CONTROL_BOTTOM_INSET_PX,
            (viewport.bottom() - image_rect.bottom()) + FLOATING_CONTROL_BOTTOM_INSET_PX,
        )
        return left, right, bottom

    def reposition_floating_controls(self) -> None:
        """Keep the scale bar and the zoom stack on a common baseline."""

        # Runs after every viewer resize and zoom change, which is also when
        # the top corners may start or stop colliding.
        self.view._fit_top_floating_controls()
        self.view._place_bottom_anchors()

    def place_bottom_anchors(self) -> None:
        """Put the scale bar and the zoom stack on one baseline over the image.

        Both use the same bottom inset from ``_floating_control_insets``, so
        whatever the image geometry they sit on the same line. A move touches
        no layout, so a zoom step re-lays out nothing.
        """

        shell = getattr(self.view, "viewer_shell", None)
        scale_anchor = getattr(self, "scale_bar_anchor", None)
        zoom_anchor = getattr(self, "zoom_anchor", None)
        if shell is None or scale_anchor is None or zoom_anchor is None:
            return
        left, right, bottom = self.view._floating_control_insets()
        baseline = shell.height() - bottom
        scale_anchor.move(left, baseline - scale_anchor.height())
        zoom_left = shell.width() - right - zoom_anchor.width()
        zoom_anchor.move(self.clear_of_open_panel(zoom_left, zoom_anchor), baseline - zoom_anchor.height())
        # A legend opening or closing rides along with its anchor (a zoom
        # step moves the anchor with a letterboxed image's edge).
        legend = getattr(self.view, "atlas_marker_legend", None)
        reveal = live_section_reveal(legend) if legend is not None else None
        if reveal is not None:
            reveal.align_to(self.view._rect_in_shell(legend))

    def clear_of_open_panel(self, zoom_left: int, zoom_anchor: QWidget) -> int:
        """Where the zoom stack's left edge goes: beside the Overlays panel when that panel would reach it.

        Judged against the lowest the stack can sit (an image that fills the
        canvas), so zooming a letterboxed image never flips it from side to side.
        """

        panel_rect = self.view._open_panel_rect()
        shell = self.view.viewer_shell
        if panel_rect is None or panel_rect.bottom() < shell.height() - FLOATING_CONTROL_BOTTOM_INSET_PX - zoom_anchor.height() - SPACE_S:
            return zoom_left
        return max(min(zoom_left, panel_rect.left() - SPACE_S - zoom_anchor.width()), 0)

    def zoom_changed(self, zoom: float) -> None:
        # Zoom is measured relative to fit-to-view, so a fitted image used to
        # read "100%", which looks like 1:1 pixels. Say "Fit" instead.
        if self.view.viewer.is_fit_mode:
            self.view.zoom_label.setText("Fit")
        else:
            self.view.zoom_label.setText(f"{max(1, round(zoom * 100))}%")
        self.view._update_scale_bar()
        self.view._reposition_floating_controls()
        if self.view.parts.mrc_loading.current_path is None or self.view.parts.mrc_loading.current_path.suffix.lower() != ".mrc":
            return
        source_max = self.view._known_mrc_source_max_for_path(self.view.parts.mrc_loading.current_path)
        if source_max <= 0:
            LOGGER.debug("Skipping MRC zoom refresh until preview metadata is available path=%s", self.view.parts.mrc_loading.current_path)
            return
        target = self.view.parts.mrc_loading.current_mrc_max_size
        if zoom > 1.5:
            for level in PYRAMID_LEVELS:
                if level > self.view.parts.mrc_loading.current_mrc_max_size:
                    target = min(level, source_max)
                    break
        if target < self.view.parts.mrc_loading.current_mrc_max_size:
            target = self.view.parts.mrc_loading.current_mrc_max_size
        if target == self.view.parts.mrc_loading.current_mrc_max_size:
            return
        self.view.parts.mrc_loading.current_mrc_max_size = target
        self.view._load_path(self.view.parts.mrc_loading.current_path, self.view.parts.mrc_loading.current_fallback, self.view.slice_slider.value())

    def known_mrc_source_max_for_path(self, path: Path) -> int:
        if path == self.view.parts.mrc_loading.current_path and self.view.parts.mrc_loading.native_size > 0:
            return self.view.parts.mrc_loading.native_size
        value = self.view.parts.tab_selection.current_value
        metadata = getattr(value, "mrc_metadata", None)
        if metadata is None or getattr(metadata, "path", None) != path:
            return 0
        nx = int(metadata.nx or 0)
        ny = int(metadata.ny or 0)
        return max(nx, ny)

    def update_image_badge(self) -> None:
        self.view.export_image_button.setEnabled(self.view.viewer.has_image())
        if self.view.parts.mrc_loading.displayed_path is None or self.view.parts.tab_selection.current_value is None or not self.view.viewer.has_image():
            self.view._set_image_badge_wanted(False)
            return
        kind = "MRC" if self.view.parts.mrc_loading.displayed_path.suffix.lower() == ".mrc" else self.view.parts.mrc_loading.displayed_path.suffix.lstrip(".").upper() or "Image"
        pixel_size = scale_bar_pixel_size(self.view.parts.tab_selection.current_value, self.view.parts.mrc_loading.displayed_path)
        pixel_text = f" · {format_pixel_size_label(pixel_size)}/px" if pixel_size is not None else ""
        self.view.image_badge.setText(f"{kind}{pixel_text}")
        self.view.image_badge.setToolTip(str(self.view.parts.mrc_loading.displayed_path))
        self.view._set_image_badge_wanted(True)

    def set_image_badge_wanted(self, wanted: bool) -> None:
        self.image_badge_wanted = wanted
        self.view._fit_top_floating_controls()

    def fit_top_floating_controls(self) -> None:
        """Keep the image badge and Overlays from overlapping in a narrow canvas.

        Fit-first, like the header (F1.8): Overlays drops its label and keeps
        its icon, then the badge stands down. The badge repeats the header's
        pixel-size chip and the context panel, so hiding it loses nothing;
        Overlays is the only way to the marker toggles, so it always stays.
        Display only: nothing here touches the image, markers or scale bar.
        """

        badge = getattr(self.view, "image_badge", None)
        button = getattr(self.view, "overlay_button", None)
        viewer = getattr(self.view, "viewer", None)
        if badge is None or button is None or viewer is None:
            return
        wanted = bool(getattr(self, "image_badge_wanted", False))
        if not self.overlay_button_compact:
            self.overlay_button_full_width = button.sizeHint().width()
        available = viewer.viewport().width() - 2 * FLOATING_CONTROL_INSET_PX
        badge_width = badge.sizeHint().width() + SPACE_S if wanted else 0
        compact = badge_width + self.overlay_button_full_width > available
        self.view._set_overlay_button_compact(compact)
        button_width = FLOATING_CONTROL_SIZE_PX if compact else self.overlay_button_full_width
        badge.setVisible(wanted and badge_width + button_width <= available)

    def set_overlay_button_compact(self, compact: bool) -> None:
        if compact == self.overlay_button_compact:
            return
        self.overlay_button_compact = compact
        button = self.view.overlay_button
        # The ``compact`` rule drops the side padding so the icon keeps its
        # room inside the square; the accessible name and tooltip still say
        # what the button is.
        button.setProperty("compact", compact)
        button.setText("" if compact else "Overlays")
        if not compact:
            # Release the square before the repolish re-applies the sheet's
            # own minimum width.
            button.setMinimumWidth(0)
            button.setMaximumWidth(QWIDGETSIZE_MAX)
        style = button.style()
        if style is not None:
            style.unpolish(button)
            style.polish(button)
        if compact:
            button.setFixedWidth(FLOATING_CONTROL_SIZE_PX)

    def update_scale_bar(self) -> None:
        if not self.scale_bar_enabled or self.view.parts.tab_selection.current_value is None or not self.view.viewer.has_image():
            self.view.scale_bar.setVisible(False)
            return
        pixel_size_m = scale_bar_pixel_size(self.view.parts.tab_selection.current_value, self.view.parts.mrc_loading.displayed_path)
        if pixel_size_m is None or pixel_size_m <= 0:
            self.view.scale_bar.setVisible(False)
            return
        shape = self.view.viewer.displayed_image_shape()
        if shape is None:
            self.view.scale_bar.setVisible(False)
            return
        displayed_height, displayed_width = shape
        if displayed_width <= 0 or displayed_height <= 0:
            self.view.scale_bar.setVisible(False)
            return
        source_size = source_image_size_for_item(self.view.parts.tab_selection.current_value, self.view.parts.mrc_loading.displayed_path)
        source_width = float(source_size[0]) if source_size is not None and source_size[0] else float(displayed_width)
        meters_per_scene_px = pixel_size_m * (source_width / float(displayed_width))
        zoom = self.view.viewer.zoom_scale()
        if zoom <= 0 or not math.isfinite(zoom):
            self.view.scale_bar.setVisible(False)
            return
        target_screen_px = min(
            MAX_SCALE_BAR_SCREEN_PX,
            max(
                MIN_SCALE_BAR_SCREEN_PX,
                self.view.viewer.viewport().width() * 0.15,
            ),
        )
        target_meters = (target_screen_px / zoom) * meters_per_scene_px
        length_meters = nice_scale_length_meters(target_meters)
        if length_meters is None:
            self.view.scale_bar.setVisible(False)
            return
        screen_px = (length_meters / meters_per_scene_px) * zoom
        if screen_px > MAX_SCALE_BAR_SCREEN_PX:
            smaller = nice_scale_length_meters(target_meters * 0.5)
            if smaller is not None:
                length_meters = smaller
                screen_px = (length_meters / meters_per_scene_px) * zoom
        if screen_px < MIN_SCALE_BAR_SCREEN_PX:
            larger = nice_scale_length_meters(target_meters * 2.0)
            if larger is not None:
                larger_px = (larger / meters_per_scene_px) * zoom
                if larger_px <= MAX_SCALE_BAR_SCREEN_PX:
                    length_meters = larger
                    screen_px = larger_px
        self.view.scale_bar.set_scale(
            screen_px,
            format_scale_length_label(length_meters),
        )

