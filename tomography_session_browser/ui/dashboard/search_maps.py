"""Search maps components for the dashboard."""
from __future__ import annotations
from tomography_session_browser.ui.dashboard.constants import LOGGER
from PySide6.QtCore import QElapsedTimer, QModelIndex, Qt
from PySide6.QtWidgets import QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QTableView, QToolButton, QVBoxLayout, QWidget
from tomography_session_browser.domain.display_names import count_phrase as _count_phrase
from tomography_session_browser.presentation.dashboard.models import SearchMapOverviewModel, SearchMapOverviewRowModel, tile_status_word
from tomography_session_browser.presentation.design_tokens import SPACE_S
from tomography_session_browser.ui.theme import TIER_PRIMARY, apply_tier
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
from tomography_session_browser.ui.dashboard.atlas import SampleRowWidget
from tomography_session_browser.ui.dashboard.common import CardBox, card_header, card_layout, search_map_overview_tooltip, status_dot, status_tone
from tomography_session_browser.ui.dashboard.constants import READABLE_ROW_MAX_PX, SEARCH_MAP_STATUS_TOOLTIPS
from tomography_session_browser.ui.dashboard.layout import FlowLayout
from tomography_session_browser.ui.dashboard.search_map_table import SearchMapOverviewTableModel
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from tomography_session_browser.ui.dashboard.widget import SessionDashboard


def build_search_maps_overview_card(dashboard: SessionDashboard, overview: SearchMapOverviewModel) -> QWidget:
    card = CardBox()
    layout = card_layout(card)

    header, _count = card_header(
        "SEARCH MAPS OVERVIEW",
        _count_phrase(overview.total, "map"),
        tooltip=(
            "Search maps classified by whether their batch positions produced tilt series.\n"
            "The Search maps count card at the top of the dashboard classifies the same maps "
            "by whether the map's own tiles were acquired, so the two breakdowns differ."
        ),
    )
    layout.addLayout(header)

    subtitle = QLabel("Batch position completion per search map")
    subtitle.setObjectName("cardSubvalue")
    layout.addWidget(subtitle)

    # Five fixed-width chips in one row set a ~465px floor on this card,
    # which is most of why the lower dashboard row could not fit its
    # viewport. A flow layout lets them wrap on narrow windows instead of
    # forcing a horizontal scrollbar across the whole dashboard.
    summary = FlowLayout(spacing=8)
    for label, value, status in (
        ("complete", overview.complete, "complete"),
        ("incomplete", overview.incomplete, "warning"),
        ("failed", overview.failed, "failed"),
        ("unknown", overview.unknown, "neutral"),
        ("N/A", overview.not_associated, "neutral"),
    ):
        chip = QLabel(f"{value:,} {label}")
        chip.setObjectName("searchMapSummaryChip")
        # A zero count is not a finding. Painting "0 incomplete" amber and
        # "0 failed" red drew the eye to the absence of a problem.
        chip.setProperty("tone", status_tone(status if value else "neutral"))
        chip.setToolTip(SEARCH_MAP_STATUS_TOOLTIPS.get(label, label))
        summary.addWidget(chip)
    layout.addLayout(summary)

    if overview.worst:
        worst_title = QLabel("MOST AFFECTED")
        worst_title.setObjectName("cardTitle")
        worst_title.setToolTip(
            "Search maps with a failed, incomplete, or unresolved batch position. "
            "Fully acquired maps are not listed here."
        )
        layout.addWidget(worst_title)
        for row in overview.worst:
            layout.addWidget(dashboard._search_map_overview_row(row))
    elif overview.total:
        empty = QLabel("No search map in this scope has an outstanding batch position.")
        empty.setObjectName("metaPlain")
        empty.setWordWrap(True)
        layout.addWidget(empty)
    else:
        empty = QLabel("No search map completion metadata is available for this scope.")
        empty.setObjectName("metaPlain")
        empty.setWordWrap(True)
        layout.addWidget(empty)

    # Build the QTableView + its model lazily on first toggle click.
    # The previous code constructed both up-front and then hid the
    # widget; on large sessions that cost dashboard-build latency
    # every refresh, even when the user never opened the table. The
    # dose-details card already uses this lazy pattern — see
    # _build_dose_card above. Closure-captured ``table_ref`` lets us
    # both build on demand and re-show it on subsequent toggles.
    detail_container = QWidget()
    detail_layout = QVBoxLayout(detail_container)
    detail_layout.setContentsMargins(0, 0, 0, 0)
    detail_layout.setSpacing(0)
    detail_container.setVisible(False)
    table_ref: list[QTableView | None] = [None]

    toggle = QToolButton()
    toggle.setObjectName("dashboardFilterButton")
    toggle.setCheckable(True)
    toggle.setText("Show all search maps")
    toggle.setToolTip("Open a virtualised table of every search map in this scope")

    def _toggle_search_map_details(checked: bool) -> None:
        if checked and table_ref[0] is None:
            build_timer = QElapsedTimer()
            build_timer.start()
            table = QTableView(detail_container)
            table.setObjectName("dashboardSearchMapTable")
            table.setModel(SearchMapOverviewTableModel(overview.rows, table))
            table.setSortingEnabled(True)
            table.setAlternatingRowColors(True)
            table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            table.verticalHeader().hide()
            table.horizontalHeader().setStretchLastSection(True)
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            for column in range(1, 6):
                table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
            table.setMinimumHeight(220)
            table.doubleClicked.connect(
                lambda index, _table=table: dashboard._search_map_table_activated(_table, index)
            )
            detail_layout.addWidget(table)
            table_ref[0] = table
            LOGGER.debug(
                "search-map details table built rows=%d widget_ms=%d",
                len(overview.rows),
                build_timer.elapsed(),
            )
        detail_container.setVisible(checked)
        toggle.setText("Hide search map table" if checked else "Show all search maps")

    toggle.toggled.connect(_toggle_search_map_details)

    layout.addWidget(toggle, alignment=Qt.AlignmentFlag.AlignLeft)
    layout.addWidget(detail_container)
    layout.addStretch(1)
    card.setMinimumHeight(330)
    return card


def search_map_overview_row(dashboard: SessionDashboard, row: SearchMapOverviewRowModel) -> QWidget:
    wrap = SampleRowWidget(row.id)
    wrap.clicked.connect(dashboard.search_map_clicked.emit)
    wrap.setCursor(Qt.CursorShape.PointingHandCursor)
    wrap.setToolTip(search_map_overview_tooltip(row))
    wrap.setAccessibleName(f"Open search map {row.label}")
    # The outcome stays by the map's name on a wide card (plan D3).
    wrap.setMaximumWidth(READABLE_ROW_MAX_PX)
    layout = QHBoxLayout(wrap)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(SPACE_S)
    # The shared word for the row's status ("Warning" and "Neutral" used
    # to show through); nothing acquired yet is pending, so it is not red.
    word = tile_status_word("Search maps overview", row.status)
    tone = "unknown" if word == "pending" else status_tone(row.status)
    dot = status_dot(tone, compact=True)
    layout.addWidget(dot)
    label = ElidedLabel(row.label)
    label.setObjectName("metaValue")
    apply_tier(label, TIER_PRIMARY)
    label.setToolTip(row.label)
    layout.addWidget(label, stretch=1)
    status_text = word.capitalize()
    if status_text:
        status = QLabel(status_text)
        status.setObjectName("sampleCount")
        status.setProperty("tone", tone)
        status.setToolTip(search_map_overview_tooltip(row))
        layout.addWidget(status)
    summary = QLabel(row.summary)
    summary.setObjectName("metaPlain")
    summary.setToolTip(row.summary)
    layout.addWidget(summary)
    return wrap


def search_map_table_activated(dashboard: SessionDashboard, table: QTableView, index: QModelIndex) -> None:
    model = table.model()
    if not isinstance(model, SearchMapOverviewTableModel):
        return
    row = model.row(index.row())
    if row is not None:
        dashboard.search_map_clicked.emit(row.id)
