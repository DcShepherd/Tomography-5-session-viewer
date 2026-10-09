"""Overlay panel for the image viewer."""
from __future__ import annotations
from collections.abc import Callable
from typing import Any
from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QCheckBox, QFrame, QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget
from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.ui.motion import SectionReveal, live_section_reveal, motion_enabled, settle_layouts
from tomography_session_browser.ui.icons import themed_icon
from tomography_session_browser.presentation.design_tokens import CANVAS_CHROME, RADIUS_CARD
from tomography_session_browser.ui.viewer.constants import ATLAS_COLLECTION_LIST_MAX_PX, ATLAS_LOD_CONTROL_LABELS, MARKER_TYPES_WITH_CONTROLS, MARKER_TYPE_LABELS, OVERLAY_PANEL_MARGIN_PX
from tomography_session_browser.ui.viewer.models import AtlasCollectionOverlayOption
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from tomography_session_browser.ui.viewer.viewer_tab import ViewerTab


class OverlayPanel:
    """Overlay panel operations for one ViewerTab."""

    def __init__(self, view: ViewerTab) -> None:
        self.view = view

    def create_widgets(self, show_marker_controls: bool, atlas_lod: bool) -> None:
        self.marker_type_checks: dict[str, QCheckBox] = {}
        self.user_marker_type_visible: dict[str, bool] = {
            marker_type: True for marker_type in MARKER_TYPES_WITH_CONTROLS
        }
        self.unavailable_marker_types: set[str] = set()
        self.atlas_lod_checks: dict[str, QCheckBox] = {}
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
        if show_marker_controls:
            for marker_type in MARKER_TYPES_WITH_CONTROLS:
                checkbox = QCheckBox(MARKER_TYPE_LABELS.get(marker_type, marker_type), self.view.overlay_panel)
                checkbox.setChecked(True)
                checkbox.setToolTip(f"Show {MARKER_TYPE_LABELS.get(marker_type, marker_type).lower()} markers")
                checkbox.stateChanged.connect(lambda _state, marker_type=marker_type: self.view._marker_type_toggled(marker_type))
                self.marker_type_checks[marker_type] = checkbox
                overlay_panel_layout.addWidget(checkbox)
        if atlas_lod:
            for key, label, icon_name, default in ATLAS_LOD_CONTROL_LABELS:
                checkbox = QCheckBox(label, self.view.overlay_panel)
                checkbox.setChecked(default)
                checkbox.setToolTip(f"Show or hide {label.lower()}")
                checkbox.setIcon(themed_icon(icon_name, color=CANVAS_CHROME.icon, size=16))
                checkbox.setIconSize(QSize(16, 16))
                checkbox.stateChanged.connect(
                    lambda _state, option=key: self.view._atlas_lod_toggled(option)
                )
                self.atlas_lod_checks[key] = checkbox
                overlay_panel_layout.addWidget(checkbox)
            self.atlas_collection_section_button = QToolButton(
                self.view.overlay_panel
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
            overlay_panel_layout.addWidget(
                self.atlas_collection_section_button
            )

            self.atlas_collection_section = QWidget(self.view.overlay_panel)
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
            self.atlas_collection_scroll = QScrollArea(self.view.overlay_panel)
            self.atlas_collection_scroll.setObjectName("viewerAtlasCollectionScroll")
            self.atlas_collection_scroll.setWidgetResizable(True)
            self.atlas_collection_scroll.setFrameShape(QFrame.Shape.NoFrame)
            self.atlas_collection_scroll.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            self.atlas_collection_scroll.setMaximumHeight(ATLAS_COLLECTION_LIST_MAX_PX)
            self.atlas_collection_scroll.setWidget(self.atlas_collection_section)
            self.atlas_collection_scroll.setVisible(False)
            overlay_panel_layout.addWidget(self.atlas_collection_scroll)

            self.view.marker_legend_checkbox = QCheckBox(
                "Marker legend",
                self.view.overlay_panel,
            )
            self.view.marker_legend_checkbox.setChecked(True)
            self.view.marker_legend_checkbox.setToolTip(
                "Show or hide the Atlas marker legend"
            )
            self.view.marker_legend_checkbox.stateChanged.connect(
                self.view._marker_legend_toggled
            )
            overlay_panel_layout.addWidget(self.view.marker_legend_checkbox)
        self.view.scale_bar_checkbox = QCheckBox("Scale bar", self.view.overlay_panel)
        self.view.scale_bar_checkbox.setChecked(True)
        self.view.scale_bar_checkbox.setToolTip("Show or hide the scale bar")
        self.view.scale_bar_checkbox.stateChanged.connect(self.view._scale_bar_toggled)
        overlay_panel_layout.addWidget(self.view.scale_bar_checkbox)
        self.view.overlay_panel.setVisible(False)
        self.view.overlay_button.toggled.connect(self.view._overlay_panel_toggled)

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
        list_top = self.atlas_collection_scroll.y() if kind == "collections" and not opening else 0
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
                list_top = self.atlas_collection_scroll.y()
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
            checkbox = QCheckBox(
                option.label,
                self.atlas_collection_section,
            )
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
        for marker_type, checkbox in self.marker_type_checks.items():
            # An unavailable layer is already unchecked, but its saved preference
            # must also be cleared so it stays off when another image provides it.
            self.user_marker_type_visible[marker_type] = False
            checkbox.blockSignals(True)
            checkbox.setChecked(False)
            checkbox.blockSignals(False)
        for checkbox in self.atlas_lod_checks.values():
            checkbox.setChecked(False)
        collections_changed = False
        for key, checkbox in self.atlas_collection_checks.items():
            if not checkbox.isChecked():
                continue
            checkbox.blockSignals(True)
            checkbox.setChecked(False)
            checkbox.blockSignals(False)
            if self.view.parts.canvas_controls.on_atlas_collection_visibility_changed is not None:
                self.view.parts.canvas_controls.on_atlas_collection_visibility_changed(key, False)
            collections_changed = True
        if collections_changed:
            # Rebuild collection-scoped markers once, not once per collection.
            self.view.parts.tab_selection.forget_markers()
            self.view._refresh_marker_selection()
        if self.view.marker_legend_checkbox is not None:
            self.view.marker_legend_checkbox.setChecked(False)
        self.view.scale_bar_checkbox.setChecked(False)

    def marker_type_toggled(self, marker_type: str) -> None:
        checkbox = self.marker_type_checks[marker_type]
        self.user_marker_type_visible[marker_type] = checkbox.isChecked()
        self.view._apply_marker_type_visibility(marker_type)

    def atlas_lod_toggled(self, key: str) -> None:
        checkbox = self.atlas_lod_checks[key]
        self.view.viewer.set_atlas_lod_option(key, checkbox.isChecked())

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
        for marker_type, checkbox in self.marker_type_checks.items():
            unavailable_reason = self.view._marker_unavailable_reason(marker_type)
            effective_visible = self.view._effective_marker_type_visible(marker_type)
            checkbox.blockSignals(True)
            checkbox.setEnabled(unavailable_reason is None)
            checkbox.setChecked(effective_visible)
            checkbox.setToolTip(
                unavailable_reason
                or f"Show {MARKER_TYPE_LABELS.get(marker_type, marker_type).lower()} markers"
            )
            checkbox.blockSignals(False)
            self.view.viewer.set_marker_type_visible(marker_type, effective_visible)

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

    def apply_marker_type_visibility(self, marker_type: str) -> None:
        effective_visible = self.view._effective_marker_type_visible(marker_type)
        checkbox = self.marker_type_checks.get(marker_type)
        if checkbox is not None:
            checkbox.blockSignals(True)
            checkbox.setChecked(effective_visible)
            checkbox.blockSignals(False)
        self.view.viewer.set_marker_type_visible(marker_type, effective_visible)

    def set_atlas_collection_list_visible(self, visible: bool) -> None:
        section = getattr(self, "atlas_collection_section", None)
        scroll = getattr(self, "atlas_collection_scroll", None)
        if section is not None:
            section.setVisible(visible)
        if scroll is not None:
            scroll.setVisible(visible)

    def constrain_overlay_panel(self) -> None:
        """Keep the overlay panel inside the canvas it belongs to.

        With many collections the panel would otherwise grow past the bottom of
        the image and become partly unreachable. Bounding it to the viewport
        means the list scrolls instead of the panel escaping.
        """

        panel = getattr(self.view, "overlay_panel", None)
        # Intent, not ``isVisible()``: an unshown parent reports every child as
        # invisible, which skipped the bound entirely during construction.
        if panel is None or not self.view.overlay_button.isChecked():
            return
        available = max(self.view.viewer.viewport().height() - OVERLAY_PANEL_MARGIN_PX, 120)
        panel.setMaximumHeight(available)
        scroll = getattr(self, "atlas_collection_scroll", None)
        if scroll is not None and scroll.isVisible():
            # Give the dynamic list whatever the fixed controls do not need,
            # never more than its own cap.
            fixed = panel.sizeHint().height() - scroll.height()
            scroll.setMaximumHeight(
                max(min(ATLAS_COLLECTION_LIST_MAX_PX, available - fixed), 60)
            )

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

        self.view._reveal_overlay_panel(change, kind="panel", opening=checked)

