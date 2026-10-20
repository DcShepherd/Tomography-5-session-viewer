"""Widget components for the dashboard."""
from __future__ import annotations
from PySide6.QtCore import QElapsedTimer, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QLabel, QLayout, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget
from collections.abc import Callable
import shiboken6
from tomography_session_browser.services.timeline_service import SessionTimeline
from tomography_session_browser.ui.motion import SmoothScroller, veil
from tomography_session_browser.presentation.context_stack import ContextStack
from tomography_session_browser.presentation.dashboard.models import DashboardModel
from tomography_session_browser.ui.widgets.defocus_plot import DefocusScatterPlot
from tomography_session_browser.ui.widgets.dose_information_plot import DoseInformationScatterPlot
from tomography_session_browser.presentation.dashboard.summary import (
    AtlasSessionSummary,
    CollectionSummary,
    EmptySummary,
    SampleSummary,
)
from tomography_session_browser.presentation.design_tokens import SPACE_L
from tomography_session_browser.ui.widgets.status_tile_grid import StatusTileGrid
from tomography_session_browser.ui.timeline.widget import TimelineStrip
from tomography_session_browser.ui.dashboard.atlas_session_summary import build_atlas_session_summary_page
from tomography_session_browser.ui.dashboard.collection_summary import build_collection_summary_page
from tomography_session_browser.ui.dashboard.constants import DASHBOARD_REFILL_VEIL_MS, DASHBOARD_REFILL_VEIL_OPACITY
from tomography_session_browser.ui.dashboard.defocus import build_defocus_readout_card
from tomography_session_browser.ui.dashboard.dose import build_dose_information_card
from tomography_session_browser.ui.dashboard.empty_summary import build_empty_summary_page
from tomography_session_browser.ui.dashboard.header import context_eyebrow_text
from tomography_session_browser.ui.dashboard.layout import DashboardPage
from tomography_session_browser.ui.dashboard.sample_summary import build_sample_summary_page
from tomography_session_browser.ui.dashboard.timeline import build_timeline_card, set_timeline_mode, timeline_segment_clicked


class SessionDashboard(QWidget):
    """The widget that replaces the old ``QTreeWidget`` Session-tab summary."""

    navigate_requested = Signal(str)
    #: A summary's own link to another scope: the id of a sample or a session.
    scope_requested = Signal(str)
    #: A link that narrows the page's own sheet (the name of the filter).
    page_filter_requested = Signal(str)
    filter_requested = Signal(object)  # DashboardFilterModel, including counted row membership
    point_clicked = Signal(str, object)  # tilt_series_id, 1-based frame_index | None
    point_double_clicked = Signal(str, object)  # tilt_series_id, 1-based frame_index | None
    timeline_tilt_series_requested = Signal(str, object)  # tilt_series_id, 1-based frame_index | None
    highlight_clear_requested = Signal()
    #: The header's "N source folders" link: show them (Context lists them).
    source_folders_requested = Signal()
    # Generic per-tile selection: emitted with the destination tab label and
    # the item id when the user clicks a status tile on any of the four
    # count cards. The main window resolves the id back to the underlying
    # entity and selects it in the appropriate viewer.
    tile_item_clicked = Signal(str, str)  # (destination, item_id)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sessionDashboard")
        self._context_stack: ContextStack | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("dashboardScroll")
        self._scroll.viewport().setObjectName("dashboardViewport")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        SmoothScroller(self._scroll)  # eased wheel scrolling (plan F5.5)
        outer.addWidget(self._scroll)

        self._content = DashboardPage()
        self._content.setObjectName("dashboardContent")
        # The page is always exactly the viewport's width. QScrollArea sizes
        # its widget to the viewport but never below the widget's minimum
        # width, the aggregate of every wide row; ignoring it horizontally
        # means that minimum can never hold the page wider than what shows.
        # Pinning the maximum to the dashboard's own width (the old fix) left
        # the page 11 px too wide whenever the vertical scroll bar appeared,
        # until some later layout pass let it go: the cards then shifted left
        # a moment after a load had finished.
        self._content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._scroll.setWidget(self._content)
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self._content_layout.setContentsMargins(SPACE_L, SPACE_L, SPACE_L, SPACE_L)
        self._content_layout.setSpacing(SPACE_L)
        self._scroll_restore = QTimer(self)
        self._scroll_restore.setSingleShot(True)
        self._scroll_restore.timeout.connect(self._finish_scroll_restore)
        self._restore_position = 0
        self._page_generation = 0

        self._placeholder = QLabel("Open a session folder to review acquisition health, warnings, and linked data.")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Use the QSS-styled ``metaPlain`` object name instead of hard-coding
        # a colour — keeps the placeholder readable on both themes.
        self._placeholder.setObjectName("metaPlain")
        self._placeholder.setStyleSheet("padding: 60px;")
        self._content_layout.addWidget(self._placeholder)
        self._content_layout.addStretch(1)
        self._timeline_display_mode = "auto"
        self._model: DashboardModel | None = None
        self._highlighted_tilt_series_id: str | None = None
        self._timeline_strip: TimelineStrip | None = None
        self._defocus_plot: DefocusScatterPlot | None = None
        self._dose_plot: DoseInformationScatterPlot | None = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Session dashboard")
        self.setAccessibleDescription(
            "Acquisition summary. Press Escape to clear a highlighted tilt series."
        )

    # ------------------------------------------------------------------ API

    @property
    def scroll_position(self) -> int:
        return self._restore_position if self._scroll_restore.isActive() else self._scroll.verticalScrollBar().value()

    def _finish_scroll_restore(self) -> None:
        self._content_layout.activate()
        self._scroll.verticalScrollBar().setValue(self._restore_position)

    def reveal_section(self, heading: QWidget, *, focus: QWidget | None = None) -> None:
        """Reveal the heading and focus its evidence after newly shown content lays out."""
        self._scroll_restore.stop()
        generation = self._page_generation

        def reveal() -> None:
            if generation != self._page_generation or not shiboken6.isValid(heading):
                return
            self._content_layout.activate()
            (focus if focus is not None else heading).setFocus(Qt.FocusReason.OtherFocusReason)
            self._scroll.verticalScrollBar().setValue(heading.mapTo(self._content, QPoint()).y() - SPACE_L)

        reveal()
        QTimer.singleShot(0, reveal)

    def set_model(
        self,
        model: DashboardModel | None,
        timeline: SessionTimeline | None = None,
        *,
        animate: bool = False,
        defer_heavy_cards: bool = False,
        highlighted_tilt_series_id: str | None = None,
        scroll_position: int | None = None,
    ) -> None:
        # Wipe the previous content and rebuild from scratch. The dashboard
        # is rebuilt only on session load / refresh, so the simpler clear &
        # rebuild flow is fine.
        position = self.scroll_position if scroll_position is None else scroll_position
        self._scroll_restore.stop()
        self._page_generation += 1
        self._clear()
        if model is None:
            self._model = None
            self._highlighted_tilt_series_id = None
            self._placeholder.show()
            self._content_layout.addWidget(self._placeholder)
            self._content_layout.addStretch(1)
            return
        self._placeholder.hide()
        self._model = model
        self._highlighted_tilt_series_id = self._normalise_highlight_id(model, highlighted_tilt_series_id)

        # Which page the scope gets was decided when the model was built; the
        # widget only dispatches on it, and the page builds its own header.
        self._page_builders()[type(model.summary)](model, timeline, defer_heavy_cards)
        self._content_layout.addStretch(1)
        # Make replacement sections participate in layout before restoring the
        # viewport. Qt otherwise queues their first show after our timer and
        # briefly reports an empty page, clamping the saved position to zero.
        for index in range(self._content_layout.count()):
            section = self._content_layout.itemAt(index).widget()
            if section is not None:
                section.show()
        self._restore_position = position
        self._scroll.verticalScrollBar().setValue(position)
        self._scroll_restore.start(0)
        if animate:
            QTimer.singleShot(0, self._animate_loaded_content)

    def _page_builders(self) -> dict[type, Callable[[DashboardModel, SessionTimeline | None, bool], None]]:
        return {
            EmptySummary: self._build_empty_page,
            AtlasSessionSummary: self._build_atlas_page,
            CollectionSummary: self._build_collection_summary_page,
            SampleSummary: self._build_sample_summary_page,
        }

    def _build_empty_page(self, model: DashboardModel, timeline: SessionTimeline | None, defer_heavy_cards: bool) -> None:
        assert isinstance(model.summary, EmptySummary)
        for section in build_empty_summary_page(self, model, model.summary):
            self._content_layout.addWidget(section)

    def _build_atlas_page(self, model: DashboardModel, timeline: SessionTimeline | None, defer_heavy_cards: bool) -> None:
        assert isinstance(model.summary, AtlasSessionSummary)
        for section in build_atlas_session_summary_page(self, model, model.summary):
            self._content_layout.addWidget(section)

    def _build_collection_summary_page(self, model: DashboardModel, timeline: SessionTimeline | None, defer_heavy_cards: bool) -> None:
        assert isinstance(model.summary, CollectionSummary)
        for section in build_collection_summary_page(self, model, model.summary, timeline, defer_heavy_cards):
            self._content_layout.addWidget(section)

    def _build_sample_summary_page(self, model: DashboardModel, timeline: SessionTimeline | None, defer_heavy_cards: bool) -> None:
        assert isinstance(model.summary, SampleSummary)
        for section in build_sample_summary_page(self, model, model.summary, timeline, defer_heavy_cards):
            self._content_layout.addWidget(section)

    def refresh_theme(self) -> None:
        """Recolour the dashboard in place after a theme switch.

        The style sheet recolours what it styles, statuses included (they
        carry a ``tone``); painted parts read the palette when they paint.
        What is left is the status bands, which keep their colours between
        paints. A switch used to rebuild the whole dashboard for these (~150 ms
        with its clean-up).
        """

        for grid in self.findChildren(StatusTileGrid):
            grid.refresh_theme()
        for widget in self.findChildren(QWidget):
            widget.update()

    def _animate_loaded_content(self) -> None:
        """Say the dashboard now shows another scope: a veil lifts off it.

        One veil over the view. It was a staggered fade of up to seven cards,
        each through an opacity effect that rendered the card, charts and
        all, off screen every frame (plan principle 8).
        """

        from tomography_session_browser.ui.theme import current_palette

        veil(
            self._scroll.viewport(),
            QColor(current_palette().background),
            start_opacity=DASHBOARD_REFILL_VEIL_OPACITY,
            duration_ms=DASHBOARD_REFILL_VEIL_MS,
        )

    # ------------------------------------------------------------------ build

    def _clear(self) -> None:
        # Remove every child widget from the content layout. ``takeAt`` on the
        # outer layout returns layout items; we deleteLater on their widgets.
        self._timeline_strip = None
        self._defocus_plot = None
        self._dose_plot = None
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None and widget is not self._placeholder:
                widget.hide()
                widget.deleteLater()

    def set_context_stack(self, stack: ContextStack | None) -> None:
        """Supply the review scope shown above the dashboard title."""

        self._context_stack = stack

    def _context_eyebrow_text(self, title: str = "") -> str:
        return context_eyebrow_text(self, title)

    def _on_stat_tile_clicked(self, item_id: str, destination: str) -> None:
        """Bubble tile clicks up so the main window can select the item."""

        # The main window resolves the id and selects the entity it names.
        self.tile_item_clicked.emit(destination, item_id)

    def _build_defocus_readout_card(self, model: DashboardModel) -> QWidget:
        return build_defocus_readout_card(self, model)

    def _build_dose_information_card(self, model: DashboardModel) -> QWidget:
        return build_dose_information_card(self, model)

    def _build_timeline_card(self, model: DashboardModel, timeline: SessionTimeline | None) -> QWidget:
        return build_timeline_card(self, model, timeline)

    def _set_timeline_mode(self, strip: TimelineStrip, buttons: list[QToolButton], mode: str) -> None:
        return set_timeline_mode(self, strip, buttons, mode)

    def _timeline_segment_clicked(self, tilt_series_id: str, frame_index: object = None) -> None:
        return timeline_segment_clicked(self, tilt_series_id, frame_index)

    def set_highlighted_tilt_series_id(self, tilt_series_id: str | None) -> dict[str, int]:
        timings: dict[str, int] = {}
        self._highlighted_tilt_series_id = (
            self._normalise_highlight_id(self._model, tilt_series_id)
            if self._model is not None
            else tilt_series_id
        )
        if self._timeline_strip is not None:
            timer = QElapsedTimer()
            timer.start()
            self._timeline_strip.set_highlighted_tilt_series_id(self._highlighted_tilt_series_id)
            timings["timeline_ms"] = timer.elapsed()
        if self._defocus_plot is not None:
            timer = QElapsedTimer()
            timer.start()
            self._defocus_plot.set_highlighted_tilt_series_id(self._highlighted_tilt_series_id)
            timings["defocus_ms"] = timer.elapsed()
        if self._dose_plot is not None:
            timer = QElapsedTimer()
            timer.start()
            self._dose_plot.set_highlighted_tilt_series_id(self._highlighted_tilt_series_id)
            timings["dose_ms"] = timer.elapsed()
        return timings

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt signature
        if event.key() == Qt.Key.Key_Escape and self._highlighted_tilt_series_id:
            self.highlight_clear_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    @staticmethod
    def _normalise_highlight_id(model: DashboardModel | None, tilt_series_id: str | None) -> str | None:
        if model is None:
            return None
        if not tilt_series_id:
            return None
        if tilt_series_id in model.timeline_items:
            return tilt_series_id
        if any(point.tilt_series_id == tilt_series_id for point in model.defocus_readout.points):
            return tilt_series_id
        if any(point.tilt_series_id == tilt_series_id for point in model.dose_information.points):
            return tilt_series_id
        return None

    # ----- helpers

