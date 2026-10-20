"""Overlay panel for the image viewer."""
from __future__ import annotations
from collections.abc import Callable
from typing import Any
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QCheckBox, QFrame, QLabel, QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget
from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.ui.motion import SectionReveal, live_section_reveal, motion_enabled, settle_layouts
from tomography_session_browser.presentation.design_tokens import RADIUS_CARD, SPACE_XS
from tomography_session_browser.ui.viewer.constants import ATLAS_COLLECTION_LIST_MAX_PX, ATLAS_LOD_DEFAULTS, MARKER_TYPE_LABELS, FLOATING_CONTROL_BOTTOM_INSET_PX, FLOATING_CONTROL_INSET_PX, OVERLAY_MARKER_TYPES, OVERLAY_SECTIONS
from tomography_session_browser.ui.viewer.models import AtlasCollectionOverlayOption, OverlayControl
from tomography_session_browser.ui.viewer.overlay_controls import OverlayBodyScroll, OverlayControlRow, control_shown
from tomography_session_browser.ui.viewer.overlay_swatches import with_swatch
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from tomography_session_browser.ui.viewer.viewer_tab import ViewerTab


class OverlayPanel:
    """Overlay panel operations for one ViewerTab."""

    def __init__(self, view: ViewerTab) -> None:
        self.view = view

    def create_widgets(self, show_marker_controls: bool, atlas_lod: bool) -> None:
        self.rows: list[OverlayControlRow] = []
        #: Every key the panel sets, to the box that sets it (a row's keys share one).
        self.checks: dict[str, QCheckBox] = {}
        self.user_marker_type_visible: dict[str, bool] = {key: True for key in OVERLAY_MARKER_TYPES}
        self.unavailable_marker_types: set[str] = set()
        self.view.marker_legend_checkbox: QCheckBox | None = None
        self.atlas_collection_overlay_options_for: (
            Callable[[Any], list[AtlasCollectionOverlayOption]] | None
        ) = None
        self.view.parts.canvas_controls.on_atlas_collection_visibility_changed: (
            Callable[[str, bool], None] | None
        ) = None
        self.atlas_collection_checks: dict[str, QCheckBox] = {}
        self.atlas_collection_section_button: QToolButton | None = None
        self.atlas_collection_section: QWidget | None = None
        self.atlas_collection_section_layout: QVBoxLayout | None = None
        self.view.overlay_panel = QWidget(self.view)
        self.view.overlay_panel.setObjectName("viewerOverlayPanel")
        overlay_panel_layout = QVBoxLayout(self.view.overlay_panel)
        overlay_panel_layout.setContentsMargins(10, 10, 10, 10)
        overlay_panel_layout.setSpacing(6)
        self.view.remove_all_overlays_button = QPushButton("Remove all overlays", self.view.overlay_panel)
        self.view.remove_all_overlays_button.setToolTip(
            "Hide all overlays in this tab, including the scale bar and marker legend"
        )
        self.view.remove_all_overlays_button.clicked.connect(self.view._remove_all_overlays)
        overlay_panel_layout.addWidget(self.view.remove_all_overlays_button)
        # Everything under the button scrolls once the canvas cannot hold it.
        body = QWidget(self.view.overlay_panel)
        body.setObjectName("viewerOverlayBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(SPACE_XS)
        self.body_scroll = OverlayBodyScroll(body, self.view.overlay_panel)
        overlay_panel_layout.addWidget(self.body_scroll)
        for title, controls in self.shown_sections(show_marker_controls, atlas_lod):
            body_layout.addWidget(self.section_title(title, body))
            for control in controls:
                row = OverlayControlRow(control, body)
                row.box.stateChanged.connect(lambda _state, row=row: self.control_toggled(row))
                self.rows.append(row)
                self.checks.update(dict.fromkeys(control.toggles, row.box))
                body_layout.addWidget(row)
        if atlas_lod:
            self.create_collection_controls(body, body_layout)
        if self.rows:
            body_layout.addWidget(self.section_title("Canvas aids", body))
        if atlas_lod:
            self.view.marker_legend_checkbox = with_swatch(QCheckBox("Marker legend", body), "legend")
            self.view.marker_legend_checkbox.setChecked(True)
            self.view.marker_legend_checkbox.setToolTip(
                "Show or hide the Atlas marker legend"
            )
            self.view.marker_legend_checkbox.stateChanged.connect(
                self.view._marker_legend_toggled
            )
            body_layout.addWidget(self.view.marker_legend_checkbox)
        self.view.scale_bar_checkbox = with_swatch(QCheckBox("Scale bar", body), "scale_bar")
        self.view.scale_bar_checkbox.setChecked(True)
        self.view.scale_bar_checkbox.setToolTip("Show or hide the scale bar")
        self.view.scale_bar_checkbox.stateChanged.connect(self.view._scale_bar_toggled)
        body_layout.addWidget(self.view.scale_bar_checkbox)
        for row in self.rows:
            self.sync_row(row)
        self.view.overlay_panel.setVisible(False)
        self.view.overlay_button.toggled.connect(self.view._overlay_panel_toggled)

    @staticmethod
    def shown_sections(show_marker_controls: bool, atlas_lod: bool) -> list[tuple[str, list[OverlayControl]]]:
        """The table's rows this tab offers: the Atlas options only on the Atlas tab."""

        sections = []
        for section in OVERLAY_SECTIONS:
            controls = [
                control
                for control in section.controls
                if (atlas_lod if all(key in ATLAS_LOD_DEFAULTS for key in control.toggles) else show_marker_controls)
            ]
            if controls:
                sections.append((section.title, controls))
        return sections

    def section_title(self, title: str, parent: QWidget) -> QLabel:
        label = QLabel(title.upper(), parent)
        label.setObjectName("viewerOverlaySectionTitle")
        return label

    def create_collection_controls(self, body: QWidget, body_layout: QVBoxLayout) -> None:
        self.atlas_collection_section_button = QToolButton(
            body
        )
        self.atlas_collection_section_button.setObjectName(
            "viewerOverlaySectionButton"
        )
        self.atlas_collection_section_button.setText(
            "Data collections"
        )
        self.atlas_collection_section_button.setCheckable(True)
        self.atlas_collection_section_button.setChecked(False)
        self.atlas_collection_section_button.setArrowType(
            Qt.ArrowType.RightArrow
        )
        self.atlas_collection_section_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.atlas_collection_section_button.setToolTip(
            "Show batch-position visibility controls by data collection"
        )
        self.atlas_collection_section_button.toggled.connect(
            self.view._atlas_collection_section_toggled
        )
        self.atlas_collection_section_button.setVisible(False)
        body_layout.addWidget(
            self.atlas_collection_section_button
        )

        self.atlas_collection_section = QWidget(body)
        self.atlas_collection_section.setObjectName(
            "viewerAtlasCollectionSection"
        )
        self.atlas_collection_section_layout = QVBoxLayout(
            self.atlas_collection_section
        )
        self.atlas_collection_section_layout.setContentsMargins(
            18,
            0,
            0,
            2,
        )
        self.atlas_collection_section_layout.setSpacing(4)
        self.atlas_collection_section.setVisible(False)
        # The per-collection list is dynamic — a linked root can carry many
        # collections — so it scrolls inside a bounded area rather than
        # growing the panel until it runs off the canvas.
        self.atlas_collection_scroll = QScrollArea(body)
        self.atlas_collection_scroll.setObjectName("viewerAtlasCollectionScroll")
        self.atlas_collection_scroll.setWidgetResizable(True)
        self.atlas_collection_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.atlas_collection_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.atlas_collection_scroll.setMaximumHeight(ATLAS_COLLECTION_LIST_MAX_PX)
        self.atlas_collection_scroll.setWidget(self.atlas_collection_section)
        self.atlas_collection_scroll.setVisible(False)
        body_layout.addWidget(self.atlas_collection_scroll)

    def set_atlas_collection_overlay_provider(
        self,
        provider: Callable[
            [Any],
            list[AtlasCollectionOverlayOption],
        ]
        | None,
        on_visibility_changed: Callable[[str, bool], None] | None,
    ) -> None:
        self.atlas_collection_overlay_options_for = provider
        self.view.parts.canvas_controls.on_atlas_collection_visibility_changed = on_visibility_changed
        self.view._update_atlas_collection_overlay_controls(self.view.parts.tab_selection.current_value)

    def atlas_collection_section_toggled(self, expanded: bool) -> None:
        if self.atlas_collection_section_button is not None:
            self.atlas_collection_section_button.setArrowType(
                Qt.ArrowType.DownArrow
                if expanded
                else Qt.ArrowType.RightArrow
            )
        if self.atlas_collection_section is not None:
            # The scroll area is what participates in the panel layout now, so
            # it is the thing that shows and hides.
            show = expanded and bool(self.atlas_collection_checks)
            scroll = getattr(self, "atlas_collection_scroll", None)
            if scroll is None or scroll.isHidden() == (not show):  # nothing opens or closes
                self.view._set_atlas_collection_list_visible(show)
                return
            self.view._reveal_overlay_panel(
                lambda: self.view._set_atlas_collection_list_visible(show),
                kind="collections",
                opening=show,
            )

    def section_motion_allowed(self, section: QWidget) -> bool:
        return motion_enabled(section) and self.view.isVisible() and self.view.window().isVisible()

    def rect_in_shell(self, widget: QWidget) -> QRect:
        return QRect(widget.mapTo(self.view.viewer_shell, QPoint(0, 0)), widget.size())

    def set_atlas_legend_expanded(self, expanded: bool) -> None:
        """Open or close the Atlas marker legend, growing up from the scale bar.

        The legend changes size at once (one layout, as before); a picture of
        it opens or closes on a spring meanwhile. A second click mid-way turns
        the same motion around.
        """

        legend = self.view.atlas_marker_legend
        if expanded == legend.expanded:
            return
        reveal = live_section_reveal(legend)
        if not self.view._section_motion_allowed(legend) or legend.isHidden():
            if reveal is not None:
                reveal.dismiss()
            legend.set_expanded(expanded)
            return
        legend.set_expanded(expanded)
        settle_layouts(legend, self.view.parts.canvas_controls.scale_bar_anchor)
        rect = self.view._rect_in_shell(legend)
        if reveal is not None:
            reveal.align_to(rect)
            reveal.retarget(expanded, legend.picture())
            return
        SectionReveal(
            legend,
            self.view.viewer_shell,
            QRect(rect.left(), rect.bottom() + 1 - legend.EXPANDED_HEIGHT, legend.WIDTH, legend.EXPANDED_HEIGHT),
            legend.picture(),
            grows_down=False,
            fixed=RADIUS_CARD,
            riding=legend.HEADER_HEIGHT,
            closed=legend.HEADER_HEIGHT,
            opening=expanded,
            kind="legend",
            conceal=legend.set_concealed,
        )

    def collections_top(self) -> int:
        """Where the Data collections list starts, from the panel's top edge."""

        return self.atlas_collection_scroll.mapTo(self.view.overlay_panel, QPoint(0, 0)).y()

    def reveal_overlay_panel(self, change: Callable[[], None], *, kind: str, opening: bool) -> None:
        """Apply ``change`` to the Overlays panel, then open or close it along its height.

        ``kind`` is ``"panel"`` (the whole panel shows or hides) or
        ``"collections"`` (the Data collections list inside it does; the
        controls below it ride its edge). The panel's real layout changes
        once, inside ``change``.
        """

        panel = self.view.overlay_panel
        shell = self.view.viewer_shell
        reveal = live_section_reveal(panel)
        if reveal is not None and reveal.kind != kind:
            reveal.dismiss()
            reveal = None
        if not self.view._section_motion_allowed(panel) or (kind != "panel" and not panel.isVisible()):
            if reveal is not None:
                reveal.dismiss()
            change()
            return
        if reveal is not None:
            # A reversal mid-way. Turn first: hiding the panel must not read
            # as the panel vanishing while it opens.
            reveal.retarget(opening)
            change()
            settle_layouts(panel, shell)
            # Heading open, the panel must be back where the picture is.
            # Heading closed, the picture keeps the open place (a hidden
            # panel's anchor narrows to the button, which moves the panel).
            if opening and self.view._rect_in_shell(panel) != reveal.geometry():
                reveal.dismiss()
            return
        before = self.view._rect_in_shell(panel)
        picture = None if opening else panel.grab()
        list_top = self.collections_top() if kind == "collections" and not opening else 0
        change()
        settle_layouts(panel, shell)
        after = self.view._rect_in_shell(panel)
        if opening:
            open_rect, picture = after, panel.grab()
        else:
            open_rect = before
        if kind == "panel":
            fixed = riding = RADIUS_CARD + 1  # the rounded border at each end
            closed = 0
        else:
            closed_rect = before if opening else after
            if opening:
                list_top = self.collections_top()
            # The list opens below the controls above it; the ones below it
            # ride its edge.
            fixed = list_top
            closed = closed_rect.height()
            riding = closed - fixed
            if closed_rect.width() != open_rect.width() or closed_rect.left() != open_rect.left() or riding < 0:
                return
        if open_rect.isEmpty() or picture is None or closed >= open_rect.height():
            return
        SectionReveal(
            panel,
            shell,
            open_rect,
            picture,
            grows_down=True,
            fixed=fixed,
            riding=riding,
            closed=closed,
            opening=opening,
            kind=kind,
        )

    def update_atlas_collection_overlay_controls(
        self,
        value: Any,
    ) -> None:
        if (
            not self.atlas_lod
            or self.atlas_collection_section_layout is None
            or self.atlas_collection_section_button is None
            or self.atlas_collection_section is None
        ):
            return
        while self.atlas_collection_section_layout.count():
            item = self.atlas_collection_section_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.atlas_collection_checks = {}
        options = (
            self.atlas_collection_overlay_options_for(value)
            if self.atlas_collection_overlay_options_for is not None
            and value is not None
            else []
        )
        self.atlas_collection_section_button.setText(
            f"Data collections ({len(options)})"
            if options
            else "Data collections"
        )
        self.atlas_collection_section_button.setVisible(bool(options))
        for option in options:
            checkbox = with_swatch(QCheckBox(option.label, self.atlas_collection_section), "collection")
            checkbox.setObjectName("viewerAtlasCollectionCheck")
            checkbox.setChecked(option.visible)
            checkbox.setToolTip(
                f"Show batch positions from {option.label}"
            )
            checkbox.stateChanged.connect(
                lambda _state, key=option.key: self.view._atlas_collection_toggled(
                    key
                )
            )
            self.atlas_collection_checks[option.key] = checkbox
            self.atlas_collection_section_layout.addWidget(checkbox)
        self.view._set_atlas_collection_list_visible(
            bool(options)
            and self.atlas_collection_section_button.isChecked()
        )
        # The list just changed length. Without this, an Atlas with many more
        # collections selected while the panel is open keeps a scroll cap
        # computed for the previous content until the next resize.
        self.view._constrain_overlay_panel()

    def atlas_collection_toggled(self, key: str) -> None:
        checkbox = self.atlas_collection_checks.get(key)
        if checkbox is None:
            return
        if self.view.parts.canvas_controls.on_atlas_collection_visibility_changed is not None:
            self.view.parts.canvas_controls.on_atlas_collection_visibility_changed(
                key,
                checkbox.isChecked(),
            )
        # The Atlas's markers follow its visible collections: build them anew.
        self.view.parts.tab_selection.forget_markers()
        self.view._refresh_marker_selection()

    def remove_all_overlays(self) -> None:
        self.view.viewer.hide_all_markers()
        for key in self.checks:
            # An unavailable layer is already unchecked, but its saved preference
            # must also be cleared so it stays off when another image provides it.
            if key in ATLAS_LOD_DEFAULTS:
                self.view.viewer.set_atlas_lod_option(key, False)
            else:
                self.user_marker_type_visible[key] = False
        for row in self.rows:
            self.sync_row(row)
        # The Data collections boxes stay as they are: they choose which batch
        # positions the Batch position layer draws, and are not layers. Clearing
        # them left that row drawing nothing once it was switched back on.
        if self.view.marker_legend_checkbox is not None:
            self.view.marker_legend_checkbox.setChecked(False)
        self.view.scale_bar_checkbox.setChecked(False)

    def control_toggled(self, row: OverlayControlRow) -> None:
        """A row was pressed: everything it sets goes to the new state, together."""

        checked = row.box.checkState() == Qt.CheckState.Checked
        for key in row.control.toggles:
            self.set_preference(key, checked)
        self.sync_row(row)

    def set_preference(self, key: str, visible: bool) -> None:
        if key in ATLAS_LOD_DEFAULTS:
            self.view.viewer.set_atlas_lod_option(key, visible)
        else:
            self.user_marker_type_visible[key] = visible
            self.view.viewer.set_marker_type_visible(key, self.effective_marker_type_visible(key))

    def preference(self, key: str) -> bool:
        if key in ATLAS_LOD_DEFAULTS:
            return self.view.viewer.atlas_lod_option(key)
        return self.user_marker_type_visible.get(key, True)

    def sync_row(self, row: OverlayControlRow) -> None:
        """Show what the row's keys are now, and say inline what is not available."""

        keys = row.control.toggles
        missing = [key for key in keys if key in self.unavailable_marker_types]
        state, enabled = control_shown(
            [self.preference(key) for key in keys], [key not in self.unavailable_marker_types for key in keys]
        )
        if not enabled:
            note = "; ".join(dict.fromkeys(self.marker_unavailable_reason(key) or "" for key in missing))
        elif missing:
            labels = " and ".join(MARKER_TYPE_LABELS.get(key, key) for key in missing)
            note = f"{labels} unavailable for this item"
        else:
            note = row.control.hint
        tooltip = note if (missing and not enabled) else f"Show or hide {row.control.label.lower()}"
        row.show_state(state, enabled, note, tooltip)

    def marker_legend_toggled(self) -> None:
        if self.view.marker_legend_checkbox is not None:
            self.view.atlas_marker_legend.setVisible(
                self.view.marker_legend_checkbox.isChecked()
            )

    def update_marker_type_availability(self, markers: list[ImageMarker]) -> None:
        has_camera_fov = any(marker.marker_type == MarkerType.CAMERA_FOV for marker in markers)
        has_batch_labels = any(marker.marker_type == MarkerType.BATCH_LABEL for marker in markers)
        has_camera_fallback = any(
            marker.marker_type == MarkerType.CAMERA_FOV
            and bool(marker.metadata.get("filled_fallback"))
            for marker in markers
        )
        unavailable = (
            {MarkerType.EXPOSURE_AREA, MarkerType.FOCUS_AREA, MarkerType.TRACKING_AREA}
            if has_camera_fallback
            else set()
        )
        for marker_type in {
            MarkerType.EXPOSURE_AREA,
            MarkerType.FOCUS_AREA,
            MarkerType.TRACKING_AREA,
        }:
            candidates = [
                marker for marker in markers if marker.marker_type == marker_type
            ]
            if candidates and not any(marker.visible for marker in candidates):
                unavailable.add(marker_type)
        if not has_camera_fov:
            unavailable.add(MarkerType.CAMERA_FOV)
        if not has_batch_labels:
            unavailable.add(MarkerType.BATCH_LABEL)
        self.unavailable_marker_types = unavailable
        for row in self.rows:
            self.sync_row(row)
            for key in row.control.toggles:
                if key not in ATLAS_LOD_DEFAULTS:
                    self.view.viewer.set_marker_type_visible(key, self.effective_marker_type_visible(key))

    def marker_unavailable_reason(self, marker_type: str) -> str | None:
        if marker_type not in self.unavailable_marker_types:
            return None
        if marker_type == MarkerType.CAMERA_FOV:
            return "Camera dimensions are unavailable for this item"
        if marker_type == MarkerType.BATCH_LABEL:
            return "Batch labels are unavailable for this item"
        return "Unavailable because beam diameter metadata is missing"

    def effective_marker_type_visible(self, marker_type: str) -> bool:
        return (
            self.user_marker_type_visible.get(marker_type, True)
            and marker_type not in self.unavailable_marker_types
        )

    def set_atlas_collection_list_visible(self, visible: bool) -> None:
        section = getattr(self, "atlas_collection_section", None)
        scroll = getattr(self, "atlas_collection_scroll", None)
        if section is not None:
            section.setVisible(visible)
        if scroll is not None:
            scroll.setVisible(visible)
        self.view._constrain_overlay_panel()

    def open_panel_rect(self) -> QRect | None:
        """Where the open panel sits in the canvas, or None while it is closed.

        From the panel's own hints rather than its geometry, which lags a layout
        pass behind a change of rows.
        """

        panel = getattr(self.view, "overlay_panel", None)
        if panel is None or not self.view.overlay_button.isChecked():
            return None
        shell = self.view.viewer_shell
        button = self.view.overlay_button
        top = button.mapTo(shell, QPoint(0, button.height())).y() + panel.parentWidget().layout().spacing()
        room = max(shell.height() - FLOATING_CONTROL_BOTTOM_INSET_PX - top, 120)
        size = panel.sizeHint()
        return QRect(
            shell.width() - FLOATING_CONTROL_INSET_PX - size.width(), top, size.width(), min(size.height(), room)
        )

    def constrain_overlay_panel(self) -> None:
        """Keep the overlay panel inside the canvas it belongs to.

        The panel is as tall as its rows, up to the canvas; what does not fit
        scrolls, and the Data collections list scrolls inside that. The zoom
        stack steps aside for it (``CanvasControls.place_bottom_anchors``).
        """

        # Intent, not ``isVisible()``: an unshown parent reports every child as
        # invisible, which skipped the bound entirely during construction.
        self.body_scroll.updateGeometry()
        rect = self.open_panel_rect()
        if rect is None:
            return
        panel = self.view.overlay_panel
        shell = self.view.viewer_shell
        room = max(shell.height() - FLOATING_CONTROL_BOTTOM_INSET_PX - rect.top(), 120)
        panel.setMaximumHeight(room)
        scroll = getattr(self, "atlas_collection_scroll", None)
        if scroll is not None and scroll.isVisible():
            # Give the dynamic list whatever the fixed controls do not need,
            # never more than its own cap.
            fixed = panel.sizeHint().height() - scroll.height()
            scroll.setMaximumHeight(
                max(min(ATLAS_COLLECTION_LIST_MAX_PX, room - fixed), 60)
            )
        self.body_scroll.reserve_scroll_bar(panel.sizeHint().height() > room)
        self.view._place_bottom_anchors()

    def close_overlay_panel(self) -> bool:
        """Close the overlay panel and return focus to the control that opened it.

        Returns True when a panel was actually closed, so a key handler knows
        whether it consumed the event.
        """

        if not getattr(self.view, "overlay_panel", None) or not self.view.overlay_panel.isVisible():
            return False
        self.view.overlay_button.setChecked(False)
        self.view.overlay_panel.setVisible(False)
        self.view.overlay_button.setFocus(Qt.FocusReason.ShortcutFocusReason)
        return True

    def overlay_panel_toggled(self, checked: bool) -> None:
        self.view.overlay_button.setToolTip(
            "Hide overlay controls" if checked else "Show overlay controls"
        )

        def change() -> None:
            self.view.overlay_panel.setVisible(checked)
            if checked:
                self.view._constrain_overlay_panel()
            else:
                self.view._place_bottom_anchors()

        self.view._reveal_overlay_panel(change, kind="panel", opening=checked)

