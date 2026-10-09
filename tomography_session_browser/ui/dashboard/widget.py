"""Widget components for the dashboard."""
from __future__ import annotations
from tomography_session_browser.ui.dashboard.constants import LOGGER
from PySide6.QtCore import QElapsedTimer, QModelIndex, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QLabel, QLayout, QScrollArea, QSizePolicy, QTableView, QToolButton, QVBoxLayout, QWidget
from tomography_session_browser.services.timeline_service import SessionTimeline
from tomography_session_browser.ui.motion import SmoothScroller, veil
from tomography_session_browser.ui.icons import themed_icon
from tomography_session_browser.presentation.context_stack import ContextStack
from tomography_session_browser.presentation.dashboard.models import AtlasRowModel, AtlasSummaryModel, BatchPositionProgressModel, DashboardModel, SearchMapOverviewModel, SearchMapOverviewRowModel, StatCardModel, WarningGroupModel, WarningRowModel
from tomography_session_browser.ui.widgets.defocus_plot import DefocusScatterPlot
from tomography_session_browser.ui.widgets.dose_information_plot import DoseInformationScatterPlot
from tomography_session_browser.presentation.design_tokens import SPACE_L
from tomography_session_browser.ui.widgets.stat_card import StatCard
from tomography_session_browser.ui.timeline.widget import TimelineStrip
from tomography_session_browser.ui.dashboard.atlas import atlas_row_widget, build_atlas_summary_card, meta_pair_compact
from tomography_session_browser.ui.dashboard.batch_positions import batch_position_row, build_batch_positions_card
from tomography_session_browser.ui.dashboard.common import copy_text, more_button
from tomography_session_browser.ui.dashboard.constants import DASHBOARD_REFILL_VEIL_MS, DASHBOARD_REFILL_VEIL_OPACITY, SIDE_BY_SIDE_CHARTS_MIN_PX
from tomography_session_browser.ui.dashboard.counts import build_counts_row, build_stat_card, zero_counts_line
from tomography_session_browser.ui.dashboard.defocus import build_defocus_readout_card
from tomography_session_browser.ui.dashboard.dose import build_dose_information_card
from tomography_session_browser.ui.dashboard.header import build_header, context_eyebrow_text, meta_item, metadata_icon_label
from tomography_session_browser.ui.dashboard.layout import DashboardPage, ResponsiveCardGrid, SideBySideRow
from tomography_session_browser.ui.dashboard.search_maps import build_search_maps_overview_card, search_map_overview_row, search_map_table_activated
from tomography_session_browser.ui.dashboard.timeline import build_timeline_card, set_timeline_mode, timeline_segment_clicked
from tomography_session_browser.ui.dashboard.warnings import build_warnings_card, warning_group_row, warning_row
from tomography_session_browser.presentation.tab_keys import TabKey


class SessionDashboard(QWidget):
    """The widget that replaces the old ``QTreeWidget`` Session-tab summary."""

    navigate_requested = Signal(str)
    search_map_clicked = Signal(str)
    sample_clicked = Signal(str)
    filter_requested = Signal(str, str, str)  # (destination tab, query, reason)
    point_clicked = Signal(str, object)  # tilt_series_id, 1-based frame_index | None
    point_double_clicked = Signal(str, object)  # tilt_series_id, 1-based frame_index | None
    timeline_tilt_series_requested = Signal(str, object)  # tilt_series_id, 1-based frame_index | None
    highlight_clear_requested = Signal()
    warning_details_requested = Signal(str, object)  # category, raw messages
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

    def set_model(
        self,
        model: DashboardModel | None,
        timeline: SessionTimeline | None = None,
        *,
        animate: bool = False,
        defer_heavy_cards: bool = False,
        highlighted_tilt_series_id: str | None = None,
    ) -> None:
        # Wipe the previous content and rebuild from scratch. The dashboard
        # is rebuilt only on session load / refresh, so the simpler clear &
        # rebuild flow is fine.
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

        self._content_layout.addWidget(self._build_header(model))

        # Section ordering depends on what the session actually contains. We
        # keep collection scopes dense and time-oriented: metric cards first,
        # then full-width acquisition timeline, then full-width applied
        # defocus. Atlas-only sessions still use the richer atlas summary.
        if not model.has_atlases and not model.has_collection_data:
            empty = QLabel(
                "Session loaded, but no atlases, overviews, search maps, batch positions, or "
                "tilt series were detected. Warnings below may explain what metadata was available."
            )
            empty.setObjectName("metaPlain")
            empty.setWordWrap(True)
            empty.setStyleSheet("padding: 24px;")
            self._content_layout.addWidget(empty)

        if not model.has_collection_data and model.has_atlases and model.atlas_summary is not None:
            self._content_layout.addWidget(self._build_atlas_summary_card(model.atlas_summary))

        if model.has_collection_data:
            self._content_layout.addWidget(self._build_counts_row(model))
            timeline_card = self._build_timeline_card(model, timeline)
            if not defer_heavy_cards and model.defocus_readout.points:
                # Both are time-based charts; on a very wide page they share
                # a row (plan D3), and stack as before otherwise.
                self._content_layout.addWidget(
                    SideBySideRow(
                        [timeline_card, self._build_defocus_readout_card(model)],
                        min_width=SIDE_BY_SIDE_CHARTS_MIN_PX,
                    )
                )
            else:
                self._content_layout.addWidget(timeline_card)
            if not defer_heavy_cards:
                if model.dose_information.has_points:
                    self._content_layout.addWidget(self._build_dose_information_card(model))
            else:
                LOGGER.debug(
                    "dashboard heavy cards deferred defocus_points=%d dose_points=%d",
                    len(model.defocus_readout.points),
                    len(model.dose_information.points),
                )

            lower_cards: list[QWidget] = []
            if model.search_map_overview.total:
                lower_cards.append(
                    self._build_search_maps_overview_card(model.search_map_overview)
                )
            if model.batch_positions:
                lower_cards.append(
                    self._build_batch_positions_card(model)
                )
            lower_cards.append(self._build_warnings_card(model))
            self._content_layout.addWidget(ResponsiveCardGrid(lower_cards))
        if not model.has_collection_data:
            self._content_layout.addWidget(self._build_warnings_card(model))
        self._content_layout.addStretch(1)
        if animate:
            QTimer.singleShot(0, self._animate_loaded_content)

    def refresh_theme(self) -> None:
        """Recolour the dashboard in place after a theme switch.

        The style sheet recolours what it styles, statuses included (they
        carry a ``tone``); painted parts read the palette when they paint.
        What is left is drawn once at build time: icons, and the stat cards'
        status dots and tile colours. A switch used to rebuild the whole
        dashboard for these (~150 ms with its clean-up).
        """

        for icon in self.findChildren(QLabel, "metaIcon"):
            name = icon.property("iconName")
            if isinstance(name, str):
                icon.setPixmap(themed_icon(name, size=14).pixmap(QSize(14, 14)))
        for button in self.findChildren(QToolButton, "metaCopyButton"):
            button.setIcon(themed_icon("copy", size=14))
        for card in self.findChildren(StatCard):
            card.refresh_theme()
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
                widget.deleteLater()

    def set_context_stack(self, stack: ContextStack | None) -> None:
        """Supply the review scope shown above the dashboard title."""

        self._context_stack = stack

    def _context_eyebrow_text(self, title: str = "") -> str:
        return context_eyebrow_text(self, title)

    def _build_header(self, model: DashboardModel) -> QWidget:
        return build_header(self, model)

    def _meta_item(
        self,
        label: str,
        value: str,
        *,
        icon_name: str,
        icon_accessible_name: str | None = None,
        value_object_name: str = "metaValue",
        elide_mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight,
        trailing: QWidget | None = None,
    ) -> QWidget:
        return meta_item(self, label, value, icon_name=icon_name, icon_accessible_name=icon_accessible_name, value_object_name=value_object_name, elide_mode=elide_mode, trailing=trailing)

    @staticmethod
    def _metadata_icon_label(icon_name: str, accessible_name: str) -> QLabel:
        return metadata_icon_label(icon_name, accessible_name)

    def _build_counts_row(self, model: DashboardModel) -> QWidget:
        # Palette slots, not colours, so a card can recolour in place.
        return build_counts_row(self, model)

    @staticmethod
    def _zero_counts_line(empty: list[StatCardModel]) -> QLabel:
        # U+00A0 inside each entry, so a wrap falls between entries only.
        return zero_counts_line(empty)

    def _build_stat_card(self, card_model: StatCardModel, accent_role: str) -> StatCard:
        return build_stat_card(self, card_model, accent_role)

    def _on_stat_tile_clicked(self, item_id: str, destination: str) -> None:
        """Bubble tile clicks up so the main window can select the item."""

        # Always emit the generic signal so the main window can resolve the
        # id and select the underlying entity. We also keep the
        # search-map-specific signal for backwards compatibility with the
        # completion-bar card which only carries SearchMap rows.
        self.tile_item_clicked.emit(destination, item_id)
        if destination == TabKey.SEARCH_MAP:
            self.search_map_clicked.emit(item_id)

    def _build_defocus_readout_card(self, model: DashboardModel) -> QWidget:
        return build_defocus_readout_card(self, model)

    def _build_dose_information_card(self, model: DashboardModel) -> QWidget:
        return build_dose_information_card(self, model)

    def _build_search_maps_overview_card(self, overview: SearchMapOverviewModel) -> QWidget:
        return build_search_maps_overview_card(self, overview)

    def _search_map_overview_row(self, row: SearchMapOverviewRowModel) -> QWidget:
        return search_map_overview_row(self, row)

    def _search_map_table_activated(self, table: QTableView, index: QModelIndex) -> None:
        return search_map_table_activated(self, table, index)

    def _more_button(self, label: str, destination: str, tooltip: str) -> QToolButton:
        return more_button(self, label, destination, tooltip)

    def _build_batch_positions_card(self, model: DashboardModel) -> QWidget:
        return build_batch_positions_card(self, model)

    def _batch_position_row(
        self,
        row_model: BatchPositionProgressModel,
        *,
        compact: bool = False,
    ) -> QWidget:
        return batch_position_row(self, row_model, compact=compact)

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

    def _build_atlas_summary_card(self, atlas_summary: AtlasSummaryModel) -> QWidget:
        return build_atlas_summary_card(self, atlas_summary)

    def _meta_pair_compact(self, label: str, value: str) -> QWidget:
        return meta_pair_compact(label, value)

    def _atlas_row_widget(self, row_model: AtlasRowModel) -> QWidget:
        return atlas_row_widget(row_model)

    def _build_warnings_card(self, model: DashboardModel) -> QWidget:
        return build_warnings_card(self, model)

    def _warning_row(self, row: WarningRowModel) -> QWidget:
        return warning_row(row)

    def _warning_group_row(self, group: WarningGroupModel) -> QWidget:
        return warning_group_row(self, group)

    # ----- helpers

    def _copy(self, text: str, *, source: QToolButton | None = None) -> None:
        return copy_text(text, source=source)

