"""Content-based tag cards on the existing processing Summary page."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (QComboBox, QHeaderView, QLabel, QStyledItemDelegate, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget, QSizePolicy)

from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.presentation.dashboard.tag_summary import number_text, tag_summary
from tomography_session_browser.presentation.dashboard.index_chart import chart_model
from tomography_session_browser.ui.dashboard.common import CardBox, card_header, card_layout
from tomography_session_browser.ui.theme import TIER_SUPPORTING, apply_tier, current_palette
from tomography_session_browser.ui.widgets.chart_selection import ChartSelection
from tomography_session_browser.ui.widgets.index_chart import IndexChart
from tomography_session_browser.ui.widgets.link_button import link_button

NUMBER_ALIGNMENT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
TABLE_HELP = "Tomograms: tagged. Counted: with a number. Zero: looked, none seen. Click a tag to list its tomograms."


class TagNameDelegate(QStyledItemDelegate):
    """Tag names read as the links they are, in the theme painted now."""

    def initStyleOption(self, option, index) -> None:  # noqa: N802 - Qt API
        super().initStyleOption(option, index)
        option.palette.setColor(QPalette.ColorRole.Text, QColor(current_palette().link_text))


class TagCards(QWidget):
    def __init__(self, page, payload):
        super().__init__(page)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.page = page
        self.content = None
        self.chart = None
        self.selected_tag = None
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.status = QLabel(self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.layout_.addWidget(self.status)
        self.body = QWidget(self)
        self.body.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.layout_.addWidget(self.body)
        self.update_payload(payload)

    def update_payload(self, payload):
        content = (tuple(r.tomogram.id for r in payload.rows), tuple(r.tomogram.name for r in payload.rows),
                   tuple(r.tags for r in payload.rows), payload.folder_projects)
        if content == self.content:
            self.status.setText(self.status_text + (" · not saved yet" if payload.tags_unsaved else ""))
            return
        self.content = content
        keys, names, rows, folders = content
        summary = tag_summary(keys, rows, folder_projects=folders)
        self.status_text = (f"Your manual counts · {count_phrase(len(summary.rows), 'tag')} · "
                            f"counting complete in {summary.completed} of {len(summary.row_keys)} tomograms"
                            if summary.project else "Your manual counts")
        self.status.setText(self.status_text + (" · not saved yet" if payload.tags_unsaved else ""))
        names_by_key = dict(zip(keys, names, strict=True))
        plot_keys = summary.row_keys
        plot_names = tuple(names_by_key[key] for key in plot_keys)
        # Out of the old card's header before that card goes.
        self.layout_.insertWidget(0, self.status)
        self.status.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.chart = None
        if not summary.rows:
            self.setVisible(bool(summary.notice))
            if summary.notice:
                label = QLabel(summary.notice, self.body)
                label.setWordWrap(True)
                self.body_layout.addWidget(label)
            return
        self.show()
        table_card = CardBox(self.body)
        body = card_layout(table_card)
        header, _ = card_header("Tags and counts")
        self.status.setAlignment(NUMBER_ALIGNMENT)
        # The status takes the row's spare width from the header's own spacer.
        header.setStretch(1, 0)
        header.addWidget(self.status, 1)
        body.addLayout(header)
        help_line = apply_tier(QLabel(TABLE_HELP, table_card), TIER_SUPPORTING)
        help_line.setWordWrap(True)
        body.addWidget(help_line)
        self.table = QTableWidget(0, 6, table_card)
        self.table.setHorizontalHeaderLabels(("Tag", "Tomograms", "Counted", "Zero", "Total", "Median, counted"))
        self.table.setAccessibleName("Manual tags and counts")
        self.table.verticalHeader().hide()
        columns = self.table.horizontalHeader()
        columns.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, self.table.columnCount()):
            columns.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
            self.table.horizontalHeaderItem(column).setTextAlignment(NUMBER_ALIGNMENT)
        self.table.horizontalHeaderItem(0).setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.setItemDelegateForColumn(0, TagNameDelegate(self.table))
        self.summary_rows = summary.rows
        def fill_table(all_rows=False):
            shown = self.summary_rows if all_rows else self.summary_rows[:8]
            self.table.setRowCount(len(shown))
            for index, row in enumerate(shown):
                values = (row.name, str(row.tomograms), str(row.counted), number_text(row.zero if row.counted else None),
                          number_text(row.total), number_text(row.median))
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    if column:
                        item.setTextAlignment(NUMBER_ALIGNMENT)
                    self.table.setItem(index, column, item)
            self.table.setMinimumHeight(columns.sizeHint().height() +
                sum(self.table.rowHeight(i) for i in range(len(shown))) + 2 * self.table.frameWidth())
        fill_table()
        self.table.cellClicked.connect(lambda row, col: self.page.route(frozenset(self.summary_rows[row].row_keys), "Summary · " + self.summary_rows[row].name))
        body.addWidget(self.table)
        if len(summary.rows) > 8:
            more = link_button(f"Show all {len(summary.rows)} tags", table_card)
            more.clicked.connect(lambda: (fill_table(True), more.hide()))
            body.addWidget(more, alignment=Qt.AlignmentFlag.AlignLeft)
        if summary.notice:
            body.addWidget(QLabel(summary.notice, table_card))
        self.body_layout.addWidget(table_card)
        chart_card = CardBox(self.body)
        chart_body = card_layout(chart_card)
        selector = QComboBox(chart_card)
        selector.setObjectName("tagCountSelector")
        selector.setAccessibleName("Tag for count chart")
        for row in summary.rows:
            selector.addItem(row.name, row.tag_id)
        if self.selected_tag:
            selector.setCurrentIndex(max(0, selector.findData(self.selected_tag)))
        chart_header, _ = card_header("Count per tomogram")
        chart_header.insertWidget(1, selector)
        chart_body.addLayout(chart_header)
        hover = QLabel("Point to a tomogram to see its name and count", chart_card)
        hover.setTextFormat(Qt.TextFormat.PlainText)
        hover.setWordWrap(True)
        def choose():
            self.selected_tag = selector.currentData()
            series = next(s for s in summary.series if s.key == self.selected_tag)
            model = chart_model("tag-count", "Count per tomogram", "count", "Manual counts; – means tagged, not counted.", plot_keys, plot_names, (series,))
            if self.chart:
                chart_body.removeWidget(self.chart)
                self.chart.deleteLater()
            self.chart = chart = IndexChart(model, chart_card, focus_frame=chart_card)
            selection = ChartSelection(chart)
            selection.add(chart)
            selection.changed.connect(lambda: hover.setText(chart.describe(selection.shown())
                                                            or "Point to a tomogram to see its name and count"))
            selection.opened.connect(lambda _chart, selected, label=series.label: self.page.route(selected, "Summary · " + label))
            chart_body.insertWidget(1, self.chart)
        selector.currentIndexChanged.connect(choose)
        choose()
        chart_body.addWidget(hover)
        self.body_layout.addWidget(chart_card)
