"""Overlay panel for the image viewer."""
from __future__ import annotations
from collections.abc import Callable
from tomography_session_browser.ui.widgets.section_header import SectionHeader
from PySide6.QtCore import QPoint, QRect, Qt
from dataclasses import replace
from PySide6.QtWidgets import QButtonGroup, QCheckBox, QLabel, QVBoxLayout, QWidget
from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.ui.motion import SectionReveal, live_section_reveal, motion_enabled, settle_layouts
from tomography_session_browser.presentation.design_tokens import RADIUS_CARD, SPACE_S, SPACE_XS
from tomography_session_browser.ui.viewer.constants import ATLAS_LOD_DEFAULTS, MARKER_TYPE_LABELS, FLOATING_CONTROL_BOTTOM_INSET_PX, FLOATING_CONTROL_INSET_PX, OVERLAY_SECTIONS
from tomography_session_browser.ui.viewer.models import OverlayControl
from tomography_session_browser.ui.viewer.overlay_controls import OverlayBodyScroll, OverlayControlRow, OverlayPresetRow, control_shown
from tomography_session_browser.ui.viewer.overlay_preferences import (
    CLEAN_IMAGE, CUSTOM, PRESETS, STANDARD, OverlayPreferences, changes_from, effective_types, preset_name,
    unavailable_marker_types,
)
from tomography_session_browser.ui.viewer.overlay_swatches import with_swatch
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from tomography_session_browser.ui.viewer.canvas_controls import CanvasControls
    from tomography_session_browser.ui.viewer.tab_selection import TabSelection
    from tomography_session_browser.ui.viewer.viewer_tab import ViewerTab


#: The open panel's least height (it scrolls below that).
PANEL_ROOM_MIN_PX = 120
#: Below this much room above the zoom stack (the presets and the Customise
#: header), the panel reaches the canvas bottom and the stack steps aside.
PANEL_ROOM_ABOVE_ZOOM_MIN_PX = 220

class OverlayPanel:
    """The Overlays panel of one ViewerTab: which marker layers, Atlas detail
    and canvas aids the canvas draws, for ``selection``'s item.

    Floats over the canvas, clear of ``controls``' anchors.
    """

    def __init__(
        self,
        view: ViewerTab,
        *,
        selection: TabSelection,
        controls: CanvasControls,
        show_marker_controls: bool = True,
        atlas_lod: bool = False,
    ) -> None:
        self.view = view
        self.selection = selection
        self.controls = controls
        # While the Overlays toggle applies, before its reveal exists.
        self.panel_toggling = False
        self.show_marker_controls = show_marker_controls
        self.atlas_lod = atlas_lod
        self.rows: list[OverlayControlRow] = []
        #: Every key the panel sets, to the box that sets it (a row's keys share one).
        self.checks: dict[str, QCheckBox] = {}
        #: The requested layers: the one owner (``set_preferences``). Availability
        #: masks it and never changes it.
        self.preferences: OverlayPreferences = STANDARD
        self.unavailable_marker_types: frozenset[str] = frozenset()
        self.preset_rows: dict[str, OverlayPresetRow] = {}
        self.customise_header: SectionHeader | None = None
        self.customise_body: QWidget | None = None

    def create_widgets(self) -> None:
        show_marker_controls, atlas_lod = self.show_marker_controls, self.atlas_lod
        self.view.marker_legend_checkbox: QCheckBox | None = None
        self.view.overlay_panel = QWidget(self.view)
        self.view.overlay_panel.setObjectName("viewerOverlayPanel")
        overlay_panel_layout = QVBoxLayout(self.view.overlay_panel)
        overlay_panel_layout.setContentsMargins(10, 10, 10, 10)
        overlay_panel_layout.setSpacing(6)
        # Everything scrolls once the canvas cannot hold it.
        body = QWidget(self.view.overlay_panel)
        body.setObjectName("viewerOverlayBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(SPACE_XS)
        self.body_scroll = OverlayBodyScroll(body, self.view.overlay_panel)
        overlay_panel_layout.addWidget(self.body_scroll)
        sections = self.shown_sections(show_marker_controls, atlas_lod)
        # The presets on every image tab (decision 2); the rows under Customise.
        self.create_preset_controls(body, body_layout)
        self.customise_header = SectionHeader("Customise", body)
        self.customise_header.setToolTip("Choose each layer")
        self.customise_header.toggled.connect(self.customise_toggled)
        body_layout.addWidget(self.customise_header)
        self.customise_body = QWidget(body)
        self.customise_body.setObjectName("viewerOverlayCustomise")
        rows_parent, rows_layout = self.customise_body, QVBoxLayout(self.customise_body)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(SPACE_XS)
        self.customise_body.setVisible(False)
        body_layout.addWidget(self.customise_body)
        self.body_scroll.content_width = self.customise_width
        for title, controls in sections:
            rows_layout.addWidget(self.section_title(title, rows_parent))
            for control in controls:
                row = OverlayControlRow(control, rows_parent)
                row.box.stateChanged.connect(lambda _state, row=row: self.control_toggled(row))
                self.rows.append(row)
                self.checks.update(dict.fromkeys(control.toggles, row.box))
                rows_layout.addWidget(row)
        rows_layout.addWidget(self.section_title("Canvas", rows_parent))
        if atlas_lod:
            self.view.marker_legend_checkbox = with_swatch(QCheckBox("Positions card", rows_parent), "legend")
            self.view.marker_legend_checkbox.setToolTip("Show or hide the Positions card")
            self.view.marker_legend_checkbox.stateChanged.connect(self.marker_legend_toggled)
            rows_layout.addWidget(self.view.marker_legend_checkbox)
        self.view.scale_bar_checkbox = with_swatch(QCheckBox("Scale bar", rows_parent), "scale_bar")
        self.view.scale_bar_checkbox.setToolTip("Show or hide the scale bar")
        self.view.scale_bar_checkbox.stateChanged.connect(self.scale_bar_box_toggled)
        rows_layout.addWidget(self.view.scale_bar_checkbox)
        self.sync_controls()
        self.view.overlay_panel.setVisible(False)
        self.view.overlay_button.toggled.connect(self.overlay_panel_toggled)

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

    def create_preset_controls(self, body: QWidget, body_layout: QVBoxLayout) -> None:
        """Show: one radio per preset, and Custom while the value is no preset."""

        body_layout.addWidget(self.section_title("Show", body))
        self.preset_group = QButtonGroup(body)
        self.preset_group.setExclusive(True)
        for name, note in [(preset.name, preset.note) for preset in PRESETS] + [(CUSTOM, "")]:
            row = OverlayPresetRow(name, note, body)
            self.preset_group.addButton(row.radio)
            row.radio.clicked.connect(lambda _checked=False, name=name: self.preset_chosen(name))
            self.preset_rows[name] = row
            body_layout.addWidget(row)

    def preset_chosen(self, name: str) -> None:
        preset = next((preset for preset in PRESETS if preset.name == name), None)
        if preset is None:  # Custom only says what the rows show
            self.sync_presets()
            return
        self.set_preferences(preset.value)

    def customise_toggled(self, expanded: bool) -> None:
        body = self.customise_body
        if body is None or body.isHidden() == (not expanded):
            return

        def change() -> None:
            body.setVisible(expanded)
            self.constrain_overlay_panel()

        self.reveal_overlay_panel(change, kind="fold", opening=expanded)

    def set_preferences(self, preferences: OverlayPreferences) -> None:
        """Store the requested layers and show them: every change comes here.

        Rows, presets, Remove all (Clean image) and the canvas aids all pass
        through, and the canvas redraws once (``TabSelection.publish``).
        """

        self.preferences = preferences
        self.sync_controls()
        self.controls.refresh_overlay_button_text()
        self.selection.publish(self.selection.current_value)

    def preset_label(self) -> str:
        """The preset the requested layers equal, or "Custom"."""

        return preset_name(self.preferences)

    def effective_types(self) -> frozenset[str]:
        return effective_types(self.preferences, self.unavailable_marker_types)

    def sync_controls(self) -> None:
        """Show the requested value in every box and radio, without the boxes reporting a press."""

        for row in self.rows:
            self.sync_row(row)
        self.sync_presets()
        legend_box = self.view.marker_legend_checkbox
        if legend_box is not None:
            blocked = legend_box.blockSignals(True)
            legend_box.setChecked(self.preferences.legend)
            legend_box.blockSignals(blocked)
            self.view.positions_card.setVisible(self.preferences.legend)
            self.view.parts.collection_filter.sync()
            self.view.parts.colour_by.sync()
        scale_box = self.view.scale_bar_checkbox
        blocked = scale_box.blockSignals(True)
        scale_box.setChecked(self.preferences.scale_bar)
        scale_box.blockSignals(blocked)
        self.controls.scale_bar_toggled()

    def sync_presets(self) -> None:
        if not self.preset_rows:
            return
        name = self.preset_label()
        custom = self.preset_rows[CUSTOM]
        custom.setVisible(name == CUSTOM)
        changes = changes_from(self.preferences)
        custom.set_note(f"{changes} change{'s' if changes != 1 else ''} from Standard" if name == CUSTOM else "")
        for row_name, row in self.preset_rows.items():
            blocked = row.radio.blockSignals(True)
            row.radio.setChecked(row_name == name)
            row.radio.blockSignals(blocked)

    def section_title(self, title: str, parent: QWidget) -> QLabel:
        label = QLabel(title.upper(), parent)
        label.setObjectName("viewerOverlaySectionTitle")
        return label

    def section_motion_allowed(self, section: QWidget) -> bool:
        return motion_enabled(section) and self.view.isVisible() and self.view.window().isVisible()

    def rect_in_shell(self, widget: QWidget) -> QRect:
        return QRect(widget.mapTo(self.view.viewer_shell, QPoint(0, 0)), widget.size())

    def set_atlas_legend_expanded(self, expanded: bool) -> None:
        """Open or close the Positions card, growing up from the scale bar.

        The card changes size at once (one layout, as before); a picture of
        it opens or closes on a spring meanwhile. A second click mid-way turns
        the same motion around. The From chip follows the card at once.
        """

        legend = self.view.positions_card
        if expanded == legend.expanded:
            return
        reveal = live_section_reveal(legend)
        if not self.section_motion_allowed(legend) or legend.isHidden():
            if reveal is not None:
                reveal.dismiss()
            legend.set_expanded(expanded)
            self.view.parts.collection_filter.sync()
            return
        legend.set_expanded(expanded)
        self.view.parts.collection_filter.sync()
        settle_layouts(legend, self.controls.scale_bar_anchor)
        rect = self.rect_in_shell(legend)
        if reveal is not None:
            reveal.align_to(rect)
            reveal.retarget(expanded, legend.picture())
            return
        SectionReveal(
            legend,
            self.view.viewer_shell,
            QRect(rect.left(), rect.bottom() + 1 - legend.expanded_height(), legend.open_width(), legend.expanded_height()),
            legend.picture(),
            grows_down=False,
            fixed=RADIUS_CARD,
            riding=legend.HEADER_HEIGHT,
            closed=legend.HEADER_HEIGHT,
            opening=expanded,
            kind="legend",
            conceal=legend.set_concealed,
            # Closed, the card is as wide as its header: the width follows the spring.
            closed_width=legend.closed_width() if legend.notice.isHidden() else None,
            riding_width=legend.riding_width(),
        )

    def fold_top(self, kind: str) -> int:
        """Where a fold's content starts, from the panel's top edge: its header's foot."""

        assert kind == "fold" and self.customise_body is not None, kind
        return self.customise_body.mapTo(self.view.overlay_panel, QPoint(0, 0)).y()

    def reveal_overlay_panel(self, change: Callable[[], None], *, kind: str, opening: bool) -> None:
        """Apply ``change`` to the Overlays panel, then open or close it along its height.

        ``kind`` is ``"panel"`` (the whole panel shows or hides) or
        ``"fold"`` (the Customise fold inside it does; the controls below it
        ride its edge). The panel's real layout changes once, inside
        ``change``.
        """

        panel = self.view.overlay_panel
        shell = self.view.viewer_shell
        reveal = live_section_reveal(panel)
        if reveal is not None and reveal.kind != kind:
            reveal.dismiss()
            reveal = None
        if not self.section_motion_allowed(panel) or (kind != "panel" and not panel.isVisible()):
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
            if opening and self.rect_in_shell(panel) != reveal.geometry():
                reveal.dismiss()
            return
        before = self.rect_in_shell(panel)
        picture = None if opening else panel.grab()
        list_top = self.fold_top(kind) if kind != "panel" and not opening else 0
        change()
        settle_layouts(panel, shell)
        after = self.rect_in_shell(panel)
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
                list_top = self.fold_top(kind)
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
            on_progress=self.controls.sync_overlay_button_width if kind == "panel" else None,
        )

    def remove_all_overlays(self) -> None:
        """Clean image. Its empty sets clear every layer preference on purpose,
        unavailable ones included, so they stay off when another image has them.

        The collection filter (the From menu) stays as it is: it chooses which
        batch positions the Batch position layer draws, and is not a layer.
        Clearing it left that row drawing nothing once it was switched back on.
        """

        self.set_preferences(CLEAN_IMAGE)

    def control_toggled(self, row: OverlayControlRow) -> None:
        """A row was pressed: everything it sets goes to the new state, together."""

        checked = row.box.checkState() == Qt.CheckState.Checked
        self.set_preferences(self.preferences.with_keys(row.control.toggles, checked))

    def preference(self, key: str) -> bool:
        return self.preferences.requested(key)

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
        tooltip = note if (missing and not enabled) else (row.control.tooltip or f"Show or hide {row.control.label.lower()}")
        row.show_state(state, enabled, note, tooltip)

    def marker_legend_toggled(self) -> None:
        if self.view.marker_legend_checkbox is not None:
            self.set_preferences(replace(self.preferences, legend=self.view.marker_legend_checkbox.isChecked()))

    def scale_bar_box_toggled(self) -> None:
        self.set_preferences(replace(self.preferences, scale_bar=self.view.scale_bar_checkbox.isChecked()))

    def update_marker_type_availability(self, markers: list[ImageMarker]) -> None:
        """Note what ``markers`` cannot show and say so in the rows. Draws nothing:
        ``TabSelection.publish`` hands the effective types to the layer."""

        self.unavailable_marker_types = unavailable_marker_types(markers)
        for row in self.rows:
            self.sync_row(row)
        self.remeasure_folded_rows()

    def remeasure_folded_rows(self) -> None:
        """Measure the folded Customise rows again, so the panel's width does not change when the fold opens.

        Rows that change while hidden (an availability note) post no size
        change, so their cached widths go stale: ``updateGeometry`` clears each
        row's cached size in its parent's layout, a call a hidden row never gets.
        """

        body = self.customise_body
        if body is None or not body.isHidden():
            return
        for widget in (*body.findChildren(QWidget), body):
            widget.updateGeometry()
        self.body_scroll.updateGeometry()
        self.fit_to_room()

    def marker_unavailable_reason(self, marker_type: str) -> str | None:
        if marker_type not in self.unavailable_marker_types:
            return None
        if marker_type == MarkerType.CAMERA_FOV:
            return "Camera dimensions are unavailable for this item"
        if marker_type == MarkerType.BATCH_LABEL:
            return "Batch labels are unavailable for this item"
        return "Unavailable because beam diameter metadata is missing"

    def effective_marker_type_visible(self, marker_type: str) -> bool:
        return marker_type in self.effective_types()

    def open_panel_rect(self) -> QRect | None:
        """Where the open panel sits in the canvas, or None while it is closed.

        From the panel's own hints rather than its geometry, which lags a layout
        pass behind a change of rows.
        """

        panel = getattr(self.view, "overlay_panel", None)
        if panel is None or not self.view.overlay_button.isChecked():
            return None
        shell = self.view.viewer_shell
        top = self.panel_top()
        room = self.panel_room(top)
        size = panel.sizeHint()
        return QRect(
            shell.width() - FLOATING_CONTROL_INSET_PX - size.width(), top, size.width(), min(size.height(), room)
        )

    def customise_width(self) -> int:
        """The Customise rows' width, folded away or not (styled first: unstyled rows read narrower)."""

        body = self.customise_body
        if body is None:
            return 0
        body.ensurePolished()
        return body.sizeHint().width()

    def panel_top(self) -> int:
        """Where the panel's top edge is (or would be) in the canvas: under the Overlays button."""

        button = self.view.overlay_button
        return button.mapTo(self.view.viewer_shell, QPoint(0, button.height())).y() +             self.view.overlay_panel.parentWidget().layout().spacing()

    def panel_room(self, top: int) -> int:
        """How tall the open panel may be when its top is at ``top`` (canvas coordinates).

        It ends above the zoom stack, so the stack never moves for it; what
        does not fit scrolls. Only on a canvas too short for the presets and
        the Customise header does it reach the bottom, and the zoom stack
        then steps aside (``CanvasControls.clear_of_open_panel``).
        """

        shell = self.view.viewer_shell
        room = shell.height() - FLOATING_CONTROL_BOTTOM_INSET_PX - top
        stack_top = self.controls.zoom_stack_top()
        if stack_top is not None and stack_top - SPACE_S - top >= PANEL_ROOM_ABOVE_ZOOM_MIN_PX:
            room = min(room, stack_top - SPACE_S - top)
        return max(room, PANEL_ROOM_MIN_PX)

    def fit_to_room(self) -> None:
        """Bound the panel's height by its room (after a resize or a zoom moved the zoom stack).

        Also while the panel is closed, so it opens at its final width (the
        scroll bar's room is decided before it shows, not after).
        """

        panel = getattr(self.view, "overlay_panel", None)
        if panel is None:
            return
        room = self.panel_room(self.panel_top())
        if panel.maximumHeight() != room:
            panel.setMaximumHeight(room)
        # Judged with the Customise rows shown, so the scroll bar's room (and
        # with it the panel's width) does not change as the fold opens.
        self.body_scroll.reserve_scroll_bar(self.full_panel_height() > room)

    def full_panel_height(self) -> int:
        """The panel's height with the Customise rows shown, whether or not they are."""

        height = self.view.overlay_panel.sizeHint().height()
        body = self.customise_body
        if body is not None and body.isHidden():
            height += body.sizeHint().height() + body.parentWidget().layout().spacing()
        return height

    def constrain_overlay_panel(self) -> None:
        """Keep the overlay panel inside the canvas it belongs to, above the zoom stack (``panel_room``)."""

        # Intent, not ``isVisible()``: an unshown parent reports every child as
        # invisible, which skipped the bound entirely during construction.
        self.body_scroll.updateGeometry()
        self.fit_to_room()
        if self.open_panel_rect() is None:
            return
        self.controls.place_bottom_anchors()
        if not self.panel_toggling:  # the toggle syncs once its reveal exists
            self.controls.sync_overlay_button_width()

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
                self.constrain_overlay_panel()
            else:
                self.controls.place_bottom_anchors()

        if checked:
            self.remeasure_folded_rows()
        self.panel_toggling = True
        try:
            self.reveal_overlay_panel(change, kind="panel", opening=checked)
        finally:
            self.panel_toggling = False
        # The button follows the reveal; with no reveal (reduced motion), it
        # takes its end width now.
        self.controls.sync_overlay_button_width()

