"""Axis-aligned volume review facade and QWidget worker receivers."""
from __future__ import annotations
from tomography_session_browser.presentation.help_content import LocalBinding, InteractionKind

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt, Signal, Slot, QSignalBlocker, QTimer, QEvent
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLayout, QPushButton,
                               QSpinBox, QSizePolicy, QSplitter, QVBoxLayout, QWidget,
                               QApplication, QLineEdit, QAbstractSpinBox)

from tomography_session_browser.domain.tomograms import Tomogram, Volume
from tomography_session_browser.presentation.design_tokens import SPACE_S, SPACE_XS
from tomography_session_browser.presentation.tomogram_state import PaneGeometry, TomogramViewState, ReviewSegment
from tomography_session_browser.presentation.tomogram_tabs import PreparedTomogramTab
from tomography_session_browser.presentation.tomogram_review_state import TomogramReviewState
from tomography_session_browser.presentation.tomogram_versions import SharedGeometry, review_versions, share_geometry
from tomography_session_browser.services.settings_service import Settings
from tomography_session_browser.services.volume.models import RenderResult
from tomography_session_browser.services.volume.render import calibrated_spacing
from tomography_session_browser.ui.tomograms.canvas import VolumeCanvas
from tomography_session_browser.ui.tomograms.stage import VolumeStage
from tomography_session_browser.ui.tomograms.stage_transition import StageTransition
from tomography_session_browser.ui.tomograms.layout_controls import (CoordinateReadout, PlaneReadout, IndicatorButton, ToolGroup,
                                                                    canvas_accent_icon, canvas_icon, reserved_width,
                                                                     Versions, build_toolbar)
from tomography_session_browser.ui.tomograms.oblique_controls import ObliqueControls
from tomography_session_browser.ui.tomograms.persistence import ReviewPersistence
from tomography_session_browser.ui.tomograms.history import CaptureHistory
from tomography_session_browser.ui.tomograms.history_loading import HistoryJob
from tomography_session_browser.services.volume.render import plane_geometry, plane_spacing
from tomography_session_browser.presentation import tomogram_geometry as geometry
from tomography_session_browser.ui.tomograms.playback import Playback
from tomography_session_browser.ui.tomograms.recipes import ImageRecipes
from tomography_session_browser.ui.tomograms.prefetch import NativePrefetch
from tomography_session_browser.ui.tomograms.residency import ResidentSource
from tomography_session_browser.ui.tomograms.tooltips import KeyboardTooltips
from tomography_session_browser.services.volume.memory import VOLUME_MEMORY
from tomography_session_browser.ui.tomograms.captures import Captures, CaptureJob, CaptureOutcome
from tomography_session_browser.ui.tomograms.pixel_prompt import PixelSizePrompt

#: Below this viewer width the plane band has no room for the cursor coordinates.
CURSOR_IN_BAND_WIDTH = 640
#: Below this viewer width the film button moves into the rate menu, so the slider keeps a usable length:
#: measured at 100, 150 and 200 %, the slider keeps at least the 133 px it has where the cursor joins the band.
FILM_IN_BAND_WIDTH = 470
#: How long the camera shows its check after a snapshot is saved.
SAVED_CHECK_MS = 1500
from tomography_session_browser.ui.tomograms.contact_sheet import ContactSheetExport
from tomography_session_browser.ui.tomograms.commands import install_commands, version_tooltip
from tomography_session_browser.ui.tomograms.image_settings import ImageSettings
from tomography_session_browser.ui.tomograms.rendering import VolumeRendering, RenderJob
from tomography_session_browser.ui.tomograms.review_list import TomogramList
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
from tomography_session_browser.ui.tomograms.header import LinkedHeader
from tomography_session_browser.ui.viewer.models import ViewerNavigationAction
from tomography_session_browser.ui.shortcut_help import request_shortcut_help
from tomography_session_browser.ui.tomograms.lifetime import TomogramWorkers
from tomography_session_browser.ui.tomograms.empty_area import EmptyArea
from tomography_session_browser.ui.tomograms.status_line import StatusLine
from tomography_session_browser.ui.tomograms.focus_layout import FocusLayout
from tomography_session_browser.ui.tomograms.plane_slider import PlaneSlider
from tomography_session_browser.ui.tomograms.tags.owner import TagOwner
from tomography_session_browser.ui.tomograms.movie.owner import MovieExport


class TomogramTab(QWidget):
    oblique: ObliqueControls
    layout_choice: Versions
    slices_control: QSpinBox
    zoom_choice: QPushButton
    focus_button: QPushButton
    link_button: QPushButton
    lines_button: QPushButton
    ortho_group: ToolGroup
    item_selected = Signal(object)
    displayed_changed = Signal()
    metadata_changed = Signal()
    query_changed = Signal(str)
    image_settings_requested = Signal(bool)
    load_requested = Signal()
    processing_scope_requested = Signal(str)
    review_metadata_changed = Signal()
    parent_requested = Signal(str)
    link_requested = Signal(str, str)
    focus_requested = Signal(bool)
    focus_preparing = Signal(bool)
    focus_refit_requested = Signal()
    all_tomograms_requested = Signal()
    scope_changed = Signal()

    def __init__(self, *, settings: Settings, save_settings: Callable[[Settings], None],
                 protected_roots: Callable[[], tuple[Path, ...]],
                 review_state: TomogramReviewState | None = None,
                 navigation_actions_for: Callable[[str], list[ViewerNavigationAction]] | None = None,
                 tag_projects=None) -> None:
        super().__init__()
        self.constructed = False
        self.retired = False
        self.settings, self.save_settings, self.protected_roots = settings, save_settings, protected_roots
        self.navigation_actions_for = navigation_actions_for
        self.review_state = review_state if review_state is not None else TomogramReviewState()
        VOLUME_MEMORY.configure(settings.tomogram_memory_percent)
        self.state = TomogramViewState()
        self.payload = PreparedTomogramTab()
        self.initialised = False
        self.view_memory: OrderedDict[tuple[str, str], TomogramViewState] = OrderedDict()
        self.last_versions: OrderedDict[str, str] = OrderedDict()
        self.review_list = TomogramList(self)
        self.stage = VolumeStage(self)
        self.title = ElidedLabel("No tomogram selected", mode=Qt.TextElideMode.ElideMiddle, parent=self)
        self.title.setObjectName("viewerTitle")
        self.snapshot_button = QPushButton("Snapshot", self)
        self.snapshot_button.setObjectName("tomogramSnapshot")
        self.folder_button = QPushButton("Change folder…", self)
        self.image_settings = ImageSettings(self)
        self.image_settings.hide()
        self.image_button = IndicatorButton("Image", self)
        self.image_button.setCheckable(True)
        from tomography_session_browser.ui.tomograms.version_selection import VersionSelection
        self.version_selection = VersionSelection(self)
        self.versions = Versions(self)
        self.versions.setMinimumWidth(160)
        self.versions.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.linked_header = LinkedHeader(self)
        self.parent_button = self.linked_header.buttons["tilt_series"]
        self.linked_header.requested.connect(self.open_link)
        self.versions.setAccessibleName("Reconstruction version")
        self.warning = QLabel(self)
        self.warning.setObjectName("viewerReason")
        self.warning.setWordWrap(True)
        # Preview text must not change the splitter minimum and trigger another render.
        self.readout = PlaneReadout(self)
        self.readout.setObjectName("tomogramReadout")
        self.cursor_label = CoordinateReadout(self)
        self.cursor_reserved: tuple[str, ...] = ("X 0000  Y 0000  Z 0000",)
        self.cursor_label.setObjectName("tomogramCursor")
        self.cursor_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.slider = PlaneSlider(self)
        self.slider.setAccessibleName("Requested Z plane")
        self.slider.setToolTip("Go to a different plane (PageUp, PageDown). You can also hold Shift and use the wheel over a pane.")
        self.slider.setEnabled(False)
        self.status = StatusLine(self)
        outer = self.outer = QVBoxLayout(self)
        outer.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        outer.setContentsMargins(SPACE_S, SPACE_S, SPACE_S, SPACE_S)
        outer.setSpacing(SPACE_S)
        # The viewer column holds the title, version and stage; the list beside
        # it runs the full height, as on the reviewed layout.
        self.canvas_column = QWidget(self)
        canvas_layout = self.canvas_layout = QVBoxLayout(self.canvas_column)
        canvas_layout.setContentsMargins(0, 0, SPACE_S, 0)
        canvas_layout.setSpacing(SPACE_S)
        self.title_band = QWidget(self.canvas_column)
        title_row = self.title_row = QHBoxLayout(self.title_band)
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(SPACE_S)
        title_row.addWidget(self.title, 1)
        title_row.insertWidget(0, self.review_list.compact, 1)
        title_row.addWidget(self.linked_header.menu_button)
        canvas_layout.addWidget(self.title_band)
        canvas_layout.addWidget(self.linked_header)
        canvas_layout.addWidget(self.warning)
        version_row = self.version_row = QHBoxLayout()
        version_row.setSpacing(SPACE_S)
        version_row.addWidget(self.versions.segments)
        version_row.addWidget(self.versions)
        self.versions.hide()
        version_row.addWidget(self.status, 1)
        self.fit_button = QPushButton("Fit", self)
        canvas_layout.addLayout(version_row)
        # One rounded canvas-chrome frame: toolbar, panes, plane band.
        self.stage_frame = QFrame(self.canvas_column)
        self.stage_frame.setObjectName("tomogramStageFrame")
        frame_layout = QVBoxLayout(self.stage_frame)
        frame_layout.setContentsMargins(1, 1, 1, 1)
        frame_layout.setSpacing(0)
        self.canvas_toolbar = build_toolbar(self)
        self.canvas_toolbar.resized.connect(lambda: self.canvas_toolbar.compact(self))
        self.canvas_toolbar.resized.connect(self.place_band)
        frame_layout.addWidget(self.canvas_toolbar)
        frame_layout.addWidget(self.stage, 1)
        canvas_layout.addWidget(self.stage_frame, 1)
        self.splitter = QSplitter(self)
        self.splitter.setObjectName("tomogramSplitter")  # A hairline with a wider drag area.
        self.splitter.setHandleWidth(7)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.canvas_column)
        self.splitter.addWidget(self.review_list)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setSizes((800, 296))
        self.splitter.splitterMoved.connect(lambda *_args: self.refresh_adaptive())
        outer.addWidget(self.splitter, 1)
        self.playback = Playback(self)
        self.movie = MovieExport(self)
        self.recipes = ImageRecipes(self)
        self.pixel_prompt = PixelSizePrompt(self)
        frame_layout.addWidget(self.pixel_prompt)
        plane_band = QWidget(self)
        self.plane_band = plane_band
        plane_band.setObjectName("tomogramPlaneBand")
        plane_band.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        band_layout = QVBoxLayout(plane_band)
        band_layout.setContentsMargins(0, 0, 0, 0)
        band_layout.setSpacing(0)
        plane_row = QHBoxLayout()
        band_layout.addLayout(plane_row)
        plane_row.setContentsMargins(SPACE_S, SPACE_XS, SPACE_S, SPACE_XS)
        plane_row.setSpacing(SPACE_S)
        plane_row.addWidget(self.playback.button)
        plane_row.addWidget(self.slider, 1)
        plane_row.addWidget(self.readout)
        plane_row.addWidget(self.movie.group)
        # The cursor starts on its own plane-band line; place_cursor moves it to the
        # band's end once the viewer is wide enough.
        self.plane_row = plane_row
        frame_layout.addWidget(plane_band)
        self.cursor_band = QWidget(plane_band)
        self.cursor_row = QHBoxLayout(self.cursor_band)
        self.cursor_row.setContentsMargins(SPACE_S, 0, SPACE_S, SPACE_XS)
        self.cursor_row.addWidget(self.cursor_label)
        band_layout.addWidget(self.cursor_band)
        self.prefetch = NativePrefetch(self)
        self.residency = ResidentSource(self)
        self.rendering = VolumeRendering(self)
        self.stage_transition = StageTransition(self)
        self.captures = Captures(self)
        self.review_list.capture_column.requested.connect(self.captures.show_menu)
        self.contact_sheet = ContactSheetExport(self)
        self.persistence = ReviewPersistence(self)
        self.history = CaptureHistory(self)
        self.empty_area = EmptyArea(self)
        self.focus_layout = FocusLayout(self)
        self.tags = TagOwner(self, tag_projects)
        self.splitter.addWidget(self.tags.column)
        self.splitter.setSizes((800, 296))
        self.status.tag_save.clicked.connect(self.tags.show_recovery)
        self.linked_header.changed.connect(self.refresh_adaptive)
        self.workers = TomogramWorkers((self.residency.pool, self.image_settings.next_file.pool, self.prefetch.pool,
                                        self.rendering.pool, self.captures.pool, self.movie.pool, self.movie.preview_pool),
                                       self.persistence.writer, self.history.pool)
        self.destroyed.connect(self.workers.close)
        self.status.resume.clicked.connect(self.persistence.resume)
        self.status.keep.clicked.connect(self.persistence.keep_current)
        # A saved snapshot shows a check on the camera for a moment; nothing else moves.
        self.saved_timer = QTimer(self)
        self.saved_timer.setSingleShot(True)
        self.saved_timer.setInterval(SAVED_CHECK_MS)
        self.saved_timer.timeout.connect(lambda: self.snapshot_button.setIcon(canvas_icon("camera")))
        self.render_timer = QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.setInterval(30)
        self.render_timer.timeout.connect(self.rendering.request)
        self.review_list.selected.connect(self.select_key)
        self.review_list.query_changed.connect(self.filters_changed)
        self.versions.currentIndexChanged.connect(self.version_changed)
        self.versions.activated.connect(self.version_selection.activate_displayed_version)
        self.slider.valueChanged.connect(self.plane_changed)
        self.snapshot_button.clicked.connect(self.captures.snapshot)
        self.folder_button.clicked.connect(self.captures.choose_folder)
        self.fit_button.clicked.connect(self.fit)
        self.image_button.toggled.connect(self.image_settings_requested)
        self.image_button.toggled.connect(self.update_commands)
        self.image_button.toggled.connect(lambda _checked: self.focus_refit_requested.emit())
        self.snapshot_button.setToolTip("Save the view as an image (Ctrl+S). Set the format, size and folder in Image settings.")
        self.fit_button.setToolTip("Show the full plane (F)")
        self.folder_button.setToolTip("Select the folder for snapshots (Ctrl+Shift+S)")
        self.image_button.setToolTip("Show or hide Image settings (I)")
        self.commands = install_commands(self)
        self.versions.setToolTip(version_tooltip())
        self.versions.segments.setToolTip(version_tooltip())
        self.keyboard_tooltips = KeyboardTooltips(self)
        self.apply_icons()
        self.constructed = True
        self.update_commands()

    @property
    def canvas(self) -> VolumeCanvas:
        return self.stage.panes[self.state.axis]

    def current_object(self) -> Tomogram | None:
        row = self.review_list.rows.get(self.state.row_key or "")
        return row.tomogram if row is not None else None

    def volume(self) -> Volume | None:
        row = self.current_object()
        return next((volume for volume in row.volumes if volume.id == self.state.volume_id), None) if row else None

    def object_for_key(self, key: str) -> Tomogram | None:
        row = self.review_list.rows.get(key)
        return row.tomogram if row else None

    def query(self) -> str:
        return self.review_list.query.text()

    def set_query(self, text: str, *, row_keys: frozenset[str] | None = None, scope_label: str = "") -> None:
        self.tags.filters.clear(notify=False)
        with QSignalBlocker(self.review_list.query), QSignalBlocker(self.review_list.segment):
            self.review_list.query.setText(text)
            if not text:
                self.review_list.set_segment(ReviewSegment.ALL)
        self.review_list.filters_changed()
        if row_keys is not None:
            self.review_list.restrict(row_keys, scope_label)
            if self.state.row_key not in self.review_list.ordered_keys:
                keys = self.review_list.ordered_keys
                if keys:
                    self.select_key(keys[0])

    def filter_description(self) -> str:
        segment = self.review_list.segment_key()
        label = self.review_list.segment.currentText() if segment != ReviewSegment.ALL else ""
        return " · ".join(value for value in (self.query(), label) if value)

    def list_counts(self) -> tuple[int, int]:
        return len(self.review_list.ordered_keys), len(self.review_list.rows)

    def list_column(self) -> QWidget:
        return self.tags.column

    def capture_view_state(self) -> TomogramViewState:
        return replace(self.state, query=self.query(), segment=self.review_list.segment_key(),
                       sort=self.review_list.sort.currentText())

    def remember_view(self) -> None:
        if self.state.row_key and self.state.volume_id:
            self.recipes.remember_levels()
            self.recipes.window_recipe = self.state
            self.persistence.save_view()
            self.last_versions[self.state.row_key] = self.state.volume_id
            self.last_versions.move_to_end(self.state.row_key)
            while len(self.last_versions) > 256:
                self.last_versions.popitem(last=False)
            key = (self.state.row_key, self.state.volume_id)
            self.view_memory[key] = self.capture_view_state()
            self.view_memory.move_to_end(key)
            while len(self.view_memory) > 256:
                self.view_memory.popitem(last=False)

    def install_prepared(self, payload: PreparedTomogramTab, restore_state: TomogramViewState | None = None) -> None:
        self.playback.stop()
        if self.initialised and self.payload == payload and (restore_state is None or restore_state == self.capture_view_state()):
            return
        self.remember_view()
        self.residency.select(None)
        self.render_timer.stop()
        self.rendering.invalidate()
        self.persistence.bind_payload(payload)
        self.payload = payload
        self.initialised = True
        self.state = TomogramViewState()
        self.review_list.install(payload)
        self.restore_view_state(restore_state or TomogramViewState())
        self.persistence.offer()
        self.scope_changed.emit()

    def restore_view_state(self, state: TomogramViewState, *, ordering_notice: str = "") -> None:
        self.playback.stop()
        sort = self.review_list.restore_filters(state, ordering_notice)
        state = replace(state, sort=sort)
        key = state.row_key if state.row_key in self.review_list.rows else next(iter(self.review_list.ordered_keys), None)
        self.state = state
        if key is not None:
            self.select_key(key, restore=state if state.row_key == key else None)
        else:
            self.state = replace(state, row_key=None, volume_id=None, plane=None)
            self.show_volume()
            with QSignalBlocker(self.versions):
                self.versions.clear()
            self.slider.setEnabled(False)
            self.image_settings.setEnabled(False)
            self.linked_header.update_actions([], "No tomogram selected")
            self.readout.reserve((geometry.NO_PLANE,))
            self.readout.setText(geometry.NO_PLANE)
            self.cursor_label.clear()
            self.image_settings.histogram.set_data(None, 0., 1.)
            self.title.setText("No tomograms in this scope")
            self.warning.setText("Load a supported processing folder to inspect reconstructed volumes.")
            self.warning.show()
            self.update_commands()

    def select_key(self, key: str, *, restore: TomogramViewState | None = None) -> None:
        self.version_selection.select_key(key, restore=restore)

    def version_changed(self, index: int) -> None:
        self.version_selection.version_changed(index)

    def show_volume(self) -> None:
        self.oblique.timer.stop()
        self.oblique.interactive = False
        self.oblique.sync()
        self.playback.stop()
        self.render_timer.stop()
        self.rendering.invalidate()
        volume, row = self.volume(), self.current_object()
        self.residency.select(volume)
        # The reserve stays until the new frame, so stepping between tomograms keeps the track still.
        self.readout.setText(geometry.NO_PLANE)
        self.cursor_label.clear()
        self.image_settings.histogram.set_data(None, 0., 1.)
        self.title.setText(row.name if row else "No tomogram selected")
        self.slider.setEnabled(volume is not None and volume.grid is not None)
        self.image_settings.setEnabled(volume is not None and volume.grid is not None)
        if volume is None:
            self.warning.setText("No full reconstruction is available. Load a full reconstruction to inspect this tomogram.")
            self.warning.setVisible(row is not None)
            self.update_commands()
            self.metadata_changed.emit()
            return
        link = self.review_list.rows[row.id].link if row else None
        actions = self.navigation_actions_for(row.id) if self.navigation_actions_for and row else [
            ViewerNavigationAction("tilt_series", "Tilt series", enabled=bool(link and link.status == "resolved"),
                                   tooltip=link.explanation if link else "No linked acquisition")]
        self.linked_header.update_actions(actions, link.explanation if link else "No linked acquisition")
        self.warning.setText(" · ".join((*volume.warnings, *((link.explanation,) if link and link.status == "ambiguous" else ()))))
        self.image_settings.calibration_note.setText(" · ".join(volume.warnings) or "Calibration from the MRC header.")
        signature = (volume.fingerprint.size, volume.fingerprint.mtime_ns)
        if self.state.volume_signature not in (None, signature):
            self.state = TomogramViewState(self.state.row_key, volume.id, query=self.query(),
                                          segment=self.review_list.segment_key(), sort=self.review_list.sort.currentText())
            self.warning.setText(self.warning.text() + " Source fingerprint changed; using fresh geometry and levels.")
        self.version_selection.show_notice()
        self.warning.setVisible(bool(self.warning.text()))
        self.state = replace(self.state, volume_signature=signature)
        if volume.grid is not None:
            if self.state.layout == "Single" and self.state.axis != "XY":
                self.state = replace(self.state, axis="XY", plane=None)
            self.stage.set_layout(self.state.layout)
            self.stage.restore_dividers()
            with QSignalBlocker(self.link_button), QSignalBlocker(self.lines_button):
                self.link_button.setChecked(self.state.linked)
                self.lines_button.setChecked(self.state.ortho_lines)
            with QSignalBlocker(self.layout_choice), QSignalBlocker(self.slices_control):
                self.layout_choice.setCurrentText(self.state.layout)
                self.slices_control.setValue(self.state.slices)
            self.layout_choice.sync()
            self.image_settings.sync_state()
            self.image_settings.sync_levels(self.state.black, self.state.white)
            nz = plane_geometry(volume, self.state.axis)[2]
            self.slider.setAccessibleName("Requested " + geometry.normal_axis(self.state.axis) + " plane")
            plane = self.state.plane if self.state.plane is not None and 0 <= self.state.plane < nz else nz // 2
            cross = [max(0, min(n - 1, value)) for n, value in zip(volume.grid.shape_xyz,
                     self.state.cross_xyz or tuple(n // 2 for n in volume.grid.shape_xyz))]
            cross[{"XY": 2, "XZ": 1, "YZ": 0}[self.state.axis]] = plane
            self.state = replace(self.state, plane=plane, cross_xyz=tuple(cross))
            with QSignalBlocker(self.slider):
                self.slider.setRange(0, nz - 1)
                self.slider.setValue(plane)
            value = self.state.spacing_override or volume.grid.spacing_xyz[0] or 1.
            self.image_settings.spacing.setValue(value)
            self.request_render(immediate=True)
        self.update_commands()
        self.metadata_changed.emit()

    def activate(self) -> None:
        self.request_render()

    def change_layout(self, mode: str) -> None:
        self.stage_transition.change_layout(mode)

    def change_axis(self, axis: str) -> None:
        self.playback.stop()
        self.stage.select(axis)

    def move_intersection(self, axis: str, x: float, y: float) -> None:
        volume = self.volume()
        if volume is None or volume.grid is None:
            return
        accepted = self.rendering.accepted
        if (any(self.state.rotation) or (accepted is not None and any(accepted.result.recipe.rotation))) and (
                accepted is None or accepted.job.generation != self.rendering.generation or self.render_timer.isActive()):
            self.status.message.setText("Wait for the requested orientation before placing the pivot.")
            return
        cross = list(self.state.cross_xyz or tuple(n // 2 for n in volume.grid.shape_xyz))
        horizontal, vertical = geometry.AXES[axis][:2]
        y = volume.grid.shape_xyz[vertical] - y
        for dimension, value in ((horizontal, x), (vertical, y)):
            cross[dimension] = max(0, min(volume.grid.shape_xyz[dimension] - 1, int(value)))
        self.apply_intersection(tuple(cross))

    def apply_intersection(self, cross: tuple[int, int, int]) -> None:
        """The corner and canvas share the existing intersection operation."""
        self.state = replace(self.state, cross_xyz=tuple(cross))
        self.oblique.place_pivot(cross)
        self.plane_changed(cross[{"XY": 2, "XZ": 1, "YZ": 0}[self.state.axis]])
        with QSignalBlocker(self.slider):
            self.slider.setValue(self.state.plane or 0)

    def change_slices(self, value: int) -> None:
        if value % 2 == 0:
            self.slices_control.setValue(value + 1)
            return
        self.recipes.change(relevel=True, slices=value)

    def toggle_navigator(self, visible: bool | None = None) -> None:
        visible = not self.settings.tomogram_navigator_visible if visible is None else visible
        self.settings.tomogram_navigator_visible = visible
        self.save_settings(self.settings)
        for pane in self.stage.panes.values():
            pane.navigator_enabled = visible
            pane.sync_navigator()

    def filters_changed(self, text: str) -> None:
        self.state = self.capture_view_state()
        self.persistence.schedule()
        self.update_commands()
        self.query_changed.emit(text)

    def update_commands(self) -> None:
        if self.retired:
            return
        self.slider.sync_marks()
        self.empty_area.refresh()
        self.image_settings.next_file.refresh()
        # Enabled states follow what is selected, not whether this plane's pixels
        # have arrived: a step must not flash Play and Snapshot off and on.
        # Playback waits for each requested frame; Snapshot waits and then saves.
        usable = self.slider.isEnabled() and self.volume() is not None
        playable = usable and self.slider.maximum() > 0
        self.playback.button.setEnabled(playable or self.playback.timer.isActive())
        self.playback.button.setToolTip("Play or pause the planes (Space)" if playable or self.playback.timer.isActive()
            else ("This axis has only one plane" if usable else "Select a tomogram to play its planes"))
        self.update_capture_command()
        self.movie.refresh()
        self.pixel_prompt.sync()
        # The count appears once there is something to count.
        counts = self.history.count_snapshot
        self.review_list.capture_column.set_counts(counts.rows if counts else {},
            (failure.job.request.row_key for failure in self.captures.failures),
            available=counts is not None, refreshing=self.history.pending_counts is not None, error=self.history.refresh_error)
        self.history.refresh_controls()
        self.status.refresh()
        # Active filters change what the image shows, so Image carries a dot while
        # they are on and its panel is closed; the tooltip names them.
        filters = []
        if self.state.lowpass:
            filters.append(f"lowpass {self.state.lowpass:g} cycles/px")
        if self.state.highpass:
            filters.append(f"highpass {self.state.highpass:g} Fourier px")
        self.image_button.set_indicated(bool(filters) and not self.image_button.isChecked())
        self.image_button.setToolTip("Show or hide Image settings (I)" + (
            f". Filters on: {', '.join(filters)}. Press G to stop them." if filters else ""))
        self.image_button.setAccessibleDescription(f"Filters on: {', '.join(filters)}" if filters else "")

    def update_capture_command(self) -> None:
        """Only capture eligibility depends on both requested and displayed planes."""
        try:
            reason = self.captures.block_reason()
        except ValueError as exc:
            reason = str(exc)
        self.snapshot_button.setEnabled(self.slider.isEnabled() and self.volume() is not None and not reason)
        self.snapshot_button.setToolTip(reason or "Save the view as an image (Ctrl+S). Set the format, size and folder in Image settings.")

    def step_tomogram(self, delta: int) -> None:
        keys = self.review_list.ordered_keys
        if self.state.row_key not in keys:
            return
        index = keys.index(self.state.row_key) + delta
        if 0 <= index < len(keys):
            self.select_key(keys[index])

    def step_plane(self, delta: int) -> None:
        value = max(self.slider.minimum(), min(self.slider.maximum(), self.slider.value() + delta))
        if value == self.slider.value():
            return
        with QSignalBlocker(self.slider):
            self.slider.setValue(value)
        self.plane_changed(value, immediate=True)

    def plane_changed(self, value: int, *, immediate: bool = False) -> None:
        if not self.playback.advancing:
            self.playback.stop()
        volume = self.volume()
        cross = list(self.state.cross_xyz or tuple(n // 2 for n in volume.grid.shape_xyz)) if volume and volume.grid else [0, 0, 0]
        cross[{"XY": 2, "XZ": 1, "YZ": 0}[self.state.axis]] = value
        self.state = replace(self.state, plane=value, cross_xyz=tuple(cross))
        # Plane stepping preserves the selected slab, interpolation and filters.
        # Retire any angle-drag preview before requesting this full-quality plane.
        was_interactive = self.oblique.interactive
        self.oblique.timer.stop()
        self.oblique.interactive = False
        self.request_render(immediate=True, view_only=not was_interactive)

    def request_render(self, *, immediate: bool = False, view_only: bool = False) -> None:
        """Ask for the current state's pixels.

        ``view_only`` is a plane step, pan or zoom: it goes out at once, and the
        render already running finishes and shows as a preview, so a drag or a
        held key keeps the image moving. Other changes wait for the debounce and
        retire the running render.
        """
        if self.constructed and not self.retired:
            self.stage_transition.requesting()
            self.rendering.supersede(keep_running=view_only)
            if (immediate or view_only) and self.isVisible():
                self.render_timer.stop()
                self.rendering.request()
            else:
                self.render_timer.start()
            if view_only:
                self.slider.project()
                self.update_capture_command()
            else:
                self.update_commands()

    def fit(self) -> None:
        self.playback.stop()
        self.stage.reset_scrubs()
        self.state = replace(self.state, zoom=None, centre=None, capture_region=None, linked_scale=None, centre_xyz=None, pane_views=() if self.state.linked else self.state.pane_views)
        self.request_render()

    def set_zoom(self, zoom: float) -> None:
        self.playback.stop()
        self.stage.reset_scrubs()
        volume, displayed = self.volume(), self.rendering.displayed_recipe()
        if volume is None or volume.grid is None:
            return
        nx, ny, _ = plane_geometry(volume, self.state.axis)
        centre = self.state.centre or (nx/2, ny/2)
        if self.state.linked:
            h, v, _ = geometry.AXES[self.state.axis]
            xyz = geometry.linked_centre(self.state, volume)
            centre = xyz[h], volume.grid.shape_xyz[v] - xyz[v]
        if displayed and self.state.capture_region:
            x0, y0, x1, y1 = displayed.region
            centre = (x0+x1)/2, (y0+y1)/2
        self.state = geometry.set_view(self.state, volume, max(.01, min(100., zoom)), centre)
        self.request_render(view_only=True)

    def zoom(self, factor: float) -> None:
        displayed = self.rendering.displayed_recipe()
        if displayed is not None:
            x0, _, x1, _ = displayed.region
            self.set_zoom(displayed.output_size[0] / (x1-x0) * factor)

    def pan(self, dx: float, dy: float) -> None:
        self.playback.stop()
        self.stage.reset_scrubs()
        volume, displayed = self.volume(), self.rendering.displayed_recipe()
        if volume is None or volume.grid is None or displayed is None:
            return
        nx, ny, _ = plane_geometry(volume, self.state.axis)
        x0, y0, x1, y1 = displayed.region
        scale = displayed.output_size[0] / (x1 - x0)
        spacing = calibrated_spacing(volume, self.state.spacing_override)
        sx, sy = plane_spacing(spacing, self.state.axis) if spacing else (1., 1.)
        aspect = sy / sx
        cx, cy = self.state.centre or (nx / 2, ny / 2)
        if self.state.linked:
            h, v, _ = geometry.AXES[self.state.axis]
            xyz = geometry.linked_centre(self.state, volume)
            cx, cy = xyz[h], volume.grid.shape_xyz[v] - xyz[v]
        if self.state.capture_region:
            cx, cy = (x0+x1)/2, (y0+y1)/2
        self.state = geometry.set_view(self.state, volume, scale, (max(0., min(nx, cx - dx / scale)),
                                                           max(0., min(ny, cy - dy / (scale * aspect)))))
        self.request_render(view_only=True)

    def cursor_changed(self, x: float, y: float) -> None:
        volume, displayed = self.volume(), self.rendering.displayed_recipe()
        if volume is not None and displayed is not None:
            readout = geometry.cursor_readout(volume, displayed, x, y)
            self.cursor_label.setText(readout.display)
            self.cursor_label.setAccessibleName(readout.accessible)
            self.cursor_label.setToolTip(readout.tooltip)

    @Slot(object, object)
    def _resident_finished(self, job, result) -> None:
        if not self.retired:
            self.residency.finished(job, result)

    @Slot(object, str)
    def _resident_failed(self, job, message) -> None:
        if not self.retired:
            self.residency.finished(job, False)

    @Slot(object, object)
    def _render_finished(self, job: RenderJob, result: RenderResult | None) -> None:
        if not self.retired:
            self.rendering.finished(job, result)

    @Slot(object, object)
    def _render_active(self, job: RenderJob, result: RenderResult) -> None:
        if not self.retired:
            self.rendering.active_ready(job, result)

    @Slot(object, str)
    def _render_failed(self, job: RenderJob, message: str) -> None:
        if not self.retired:
            self.rendering.failed(job, message)

    @Slot(object, object)
    def _capture_finished(self, job: CaptureJob, outcome: CaptureOutcome) -> None:
        if not self.retired:
            self.captures.finished(job, outcome)

    @Slot(object, object)
    def _history_finished(self, job: HistoryJob, result) -> None:
        if not self.retired:
            self.history.finished(job, result)

    @Slot(object, str)
    def _history_failed(self, job: HistoryJob, message: str) -> None:
        if not self.retired:
            self.history.failed(job, message)

    @Slot(object, str)
    def _capture_failed(self, job: CaptureJob, message: str) -> None:
        if not self.retired:
            self.captures.failed(job, message)

    @Slot(object, object)
    def _review_written(self, context, result) -> None:
        if not self.retired:
            self.persistence.finished(context, result)

    @Slot(object, str)
    def _review_write_failed(self, context, message: str) -> None:
        if not self.retired:
            self.persistence.failed(context, message)

    @Slot(object, object)
    def _contact_sheet_finished(self, output: Path, result: Path) -> None:
        if not self.retired:
            self.contact_sheet.finished(output, result)

    @Slot(object, str)
    def _contact_sheet_failed(self, output: Path, message: str) -> None:
        if not self.retired:
            self.contact_sheet.failed(output, message)

    @Slot(str, object, object)
    def _movie_event(self, kind: str, job: object, payload: object) -> None:
        if not self.retired:
            self.movie.receive(kind, job, payload)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.constructed:
            self.refresh_adaptive()

    def refresh_adaptive(self) -> None:
        self.focus_layout.refresh()
        self.linked_header.adapt(self.canvas_column.width() - SPACE_S, short=self.height() < 560, allowed=not self.focus_button.isChecked())
        # A narrow viewer keeps every control: labels give way before the row wraps.
        self.canvas_toolbar.compact(self)
        self.place_band()

    def place_band(self) -> None:
        width = self.canvas_toolbar.width()
        self.place_cursor(width >= CURSOR_IN_BAND_WIDTH)
        self.movie.place(width >= FILM_IN_BAND_WIDTH)

    def place_cursor(self, in_band: bool) -> None:
        """The cursor needs one whole line: at the band's end while it fits, else on a second plane-band line."""
        target, other = (self.plane_row, self.cursor_row) if in_band else (self.cursor_row, self.plane_row)
        self.cursor_band.setVisible(not in_band)
        if target.indexOf(self.cursor_label) >= 0:
            return
        other.removeWidget(self.cursor_label)
        target.insertWidget(target.count() if in_band else min(2, target.count()), self.cursor_label)
        self.cursor_label.show()  # Reparenting otherwise queues its visibility until the next event turn.
        self.cursor_label.setWordWrap(not in_band)
        self.fit_cursor()

    def reserve_cursor(self, texts: tuple[str, ...]) -> None:
        """In the band the cursor keeps room for the widest readout of the view, so the pointer never moves the track."""
        if texts != self.cursor_reserved:
            self.cursor_reserved = texts
            self.fit_cursor()

    def fit_cursor(self) -> None:
        in_band = self.plane_row.indexOf(self.cursor_label) >= 0
        self.cursor_label.setMinimumWidth(reserved_width(self.cursor_label.fontMetrics(), self.cursor_reserved) + 4
                                          if in_band else 0)
        self.cursor_label.updateGeometry()

    def change_focus(self, enabled: bool) -> None:
        self.focus_preparing.emit(enabled)
        self.stage_transition.change_focus(enabled, lambda: self.focus_layout.commit(enabled))

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            focus = QApplication.focusWidget()
            if isinstance(focus, (QLineEdit, QAbstractSpinBox)) and focus is not self.review_list.query:
                event.ignore()
                return
            if self.query():
                self.review_list.clear_filters()
            elif self.focus_button.isChecked():
                self.focus_button.setChecked(False)
            else:
                return super().keyPressEvent(event)
            event.accept()
            return
        super().keyPressEvent(event)

    def confirm_saved(self) -> None:
        self.snapshot_button.setIcon(canvas_accent_icon("check"))
        self.saved_timer.start()

    def apply_icons(self) -> None:
        """Title-row icons follow the theme; canvas chrome icons are fixed."""
        self.linked_header.apply_icons()

    def open_link(self, key: str) -> None:
        if self.navigation_actions_for is None and key == "tilt_series":
            self.parent_requested.emit(self.state.row_key or "")
        else:
            self.link_requested.emit(self.state.row_key or "", key)

    def sync_slices(self) -> None:
        with QSignalBlocker(self.slices_control):
            self.slices_control.setValue(self.state.slices)

    def toggle_linked(self, enabled: bool) -> None:
        self.playback.stop()
        volume = self.volume()
        if volume and volume.grid:
            axes = ("XY", "XZ", "YZ") if self.state.layout == "Ortho" else (self.state.axis,)
            sizes = {axis: (self.stage.panes[axis].width(), self.stage.panes[axis].height()) for axis in axes}
            views = tuple(geometry.pane_view(self.state, volume, axis, sizes) for axis in axes)
            active = next(view for view in views if view.axis == self.state.axis)
            self.state = replace(self.state, pane_views=views, zoom=active.zoom, centre=active.centre,
                                 linked=enabled, linked_scale=None, centre_xyz=None)
        else:
            self.state = replace(self.state, linked=enabled)
        self.request_render()

    def toggle_lines(self, enabled: bool) -> None:
        self.playback.stop()
        self.state = replace(self.state, ortho_lines=enabled)
        self.request_render()

    def refresh_theme(self) -> None:
        self.apply_icons()
        self.review_list.refresh_theme()
        self.history.refresh_theme()
        self.canvas.update()
        self.image_settings.update()

    def show_help(self) -> None:
        self.playback.stop()
        request_shortcut_help(self)

    def shutdown(self) -> None:
        if self.retired:
            return
        self.retired = True
        self.stage_transition.dispose()
        self.residency.close()
        self.captures.cancel_deferred()
        self.image_settings.next_file.shutdown()
        self.focus_button.setChecked(False)
        self.keyboard_tooltips.dispose()
        self.playback.dispose()
        self.persistence.save_view()
        self.render_timer.stop()
        self.oblique.timer.stop()
        self.rendering.invalidate()
        # Close, unlike navigation, drains both bounded lanes before Qt/Python
        # can destroy worker signal objects. Pending snapshots finish on disk.
        self.prefetch.cancel()
        self.slider.cancel_marks()
        self.history.shutdown()
        self.movie.shutdown()  # Cancel movie work before its lanes are joined.
        self.workers.close()
        self.tags.shutdown()

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.DeferredDelete and self.constructed:
            self.shutdown()
        return super().event(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.tags.owns_projects and not self.tags.lifecycle.request(self.close):
            event.ignore()
            return
        self.shutdown()
        super().closeEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self.constructed and not self.retired:
            self.activate()

    def hideEvent(self, event) -> None:  # noqa: N802
        if self.constructed and not self.retired:
            self.slider.cancel_marks()
            self.focus_button.setChecked(False)
            self.playback.stop()
            self.captures.cancel_deferred()  # A waiting snapshot never fires on a later visit.
            self.movie.waiting = None  # So does a movie dialog that waited for its frame.
            self.persistence.save_view()
            self.render_timer.stop()
            self.rendering.invalidate()
        super().hideEvent(event)


# Contextual keys handled by this module; consumed by the shared Help sheet.
LOCAL_KEYS = (
    LocalBinding('tomograms.tab.tomograms-esc', 'Tomograms', InteractionKind.KEYBOARD, ('Esc',), 'Dismiss the current menu, dialog, drawer, tooltip or editor first; then clear the filter or leave Focus'),
    LocalBinding('tomograms.tab.ortho-pane-shift-wheel', 'Ortho pane', InteractionKind.MOUSE, ('Shift+Wheel',), 'Step planes in that pane. Small angle steps accumulate. Pixel-only touchpad gestures do not step planes; use PageUp/PageDown. Pause playback and finish editing or rotating first.'),
    LocalBinding('tomograms.tab.ortho-pane-pointer', 'Ortho pane', InteractionKind.MOUSE, ('Pointer', 'Double-click'), 'Move into or click a pane to select the snapshot target; double-click to place the intersection. Selection keeps keyboard focus outside the stage.'),
    LocalBinding('tomograms.tab.image-wheel', 'Image', InteractionKind.MOUSE, ('Wheel', 'Drag'), 'Zoom with the wheel; drag to move the image. Snapshots include the image and scale bar, excluding Ortho lines and chrome.'),
    LocalBinding('tomograms.tab.orientation-box-drag', 'Orientation box', InteractionKind.MOUSE, ('Drag', 'Double-click'), 'Tilt the plane; double-click to set angles to zero. The arrow points towards PageUp and the dot is the pivot. Link shares physical scale and centre; unlink retains independent views.'),
)
