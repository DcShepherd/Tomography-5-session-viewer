"""One ordered key list drives rows, compact selection and Previous/Next."""
from __future__ import annotations

from tomography_session_browser.ui.widgets.link_button import link_button
from PySide6.QtCore import QRectF, QSize, Qt, Signal, QSignalBlocker, QModelIndex
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QLineEdit, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTreeWidgetItem, QVBoxLayout, QWidget, QMenu, QSizePolicy

from tomography_session_browser.presentation.tomogram_tabs import (SORTS, PreparedTomogramTab, TomogramRow,
                                                                   needs_attention, restored_sort, sort_metric, sorted_within_groups)
from tomography_session_browser.services.volume.render import calibrated_spacing
from tomography_session_browser.presentation.recorded_metrics import metric_number
from tomography_session_browser.presentation.tomogram_tag_filters import TagFilter
from tomography_session_browser.presentation.tomogram_list_filter import ListFilter, list_membership, keep_not_opened_membership
from tomography_session_browser.presentation.design_tokens import (RADIUS_CONTROL, SPACE_M, SPACE_S, STATUS_TONE_COLOURS,
                                                                   TYPE_BODY, TYPE_CAPTION, readable_tone)
from tomography_session_browser.presentation.tomogram_state import ReviewSegment, TomogramViewState
from tomography_session_browser.presentation.viewer_entries import GroupHeader
from tomography_session_browser.ui.motion import ItemHoverAnimator, blend, veil
from tomography_session_browser.ui.motion.selection import SelectionGlide
from tomography_session_browser.ui.widgets.smooth_tree import SmoothScrollTree
from tomography_session_browser.ui.theme import current_palette
from tomography_session_browser.ui.icons import themed_icon
from tomography_session_browser.ui.tomograms.layout_controls import Versions
from tomography_session_browser.ui.tomograms.capture_column import CaptureColumn
from tomography_session_browser.ui.viewer.constants import LIST_GROUP_SHOWN_ROLE, LIST_ROW_SINGLE_HEIGHT_PX, LIST_REFILL_VEIL_OPACITY, VIEWER_ENTRY_ROLE
from tomography_session_browser.ui.viewer.group_rows import make_group_row, paint_group_row


class SelectorDelegate(QStyledItemDelegate):
    def initStyleOption(self, option, index) -> None:  # noqa: N802
        super().initStyleOption(option, index)
        option.textElideMode = Qt.TextElideMode.ElideMiddle


def row_tag(row: TomogramRow) -> tuple[str, str] | None:
    """The one exception a row shows inline, named; the row tooltip has the full text."""
    if row.link.status == "ambiguous":
        return "Ambiguous link", "bad"
    if any(calibrated_spacing(volume, None) is None for volume in row.tomogram.volumes if volume.grid is not None):
        return "No pixel size", "warn"
    if needs_attention(row):
        count = len({*row.link.warnings, *row.tomogram.warnings,
                     *(warning for volume in row.tomogram.volumes for warning in volume.warnings)})
        return (f"{count} warnings" if count > 1 else "Warning"), "warn"
    return None


def row_value(row: TomogramRow, sort: str) -> str:
    """The sorted value as displayed; empty under Name or when not recorded."""
    metric = sort_metric(row, sort)
    return f"{metric_number(metric)} {metric.unit}" if metric is not None else ""


class ReviewRowDelegate(QStyledItemDelegate):
    """Rows as the other viewer lists draw them: a rounded fill, the selection
    marker and a hairline. The shared name prefix is muted so the distinguishing
    suffix reads first; unopened rows are bold; an attention tag sits right."""

    def __init__(self, owner: TomogramList) -> None:
        super().__init__(owner)
        self.owner = owner
        self.selection_glide = SelectionGlide(owner.tree)

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # noqa: N802 - Qt API
        entry = index.data(VIEWER_ENTRY_ROLE)
        if entry is not None:
            return super().sizeHint(option, index)
        return QSize(super().sizeHint(option, index).width(), LIST_ROW_SINGLE_HEIGHT_PX)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:  # noqa: N802 - Qt API
        entry = index.data(VIEWER_ENTRY_ROLE)
        if entry is not None:
            self.selection_glide.paint(painter, option.rect)
            paint_group_row(painter, option, index, entry)
            return
        key = index.data(Qt.ItemDataRole.UserRole)
        row = self.owner.rows.get(key) if isinstance(key, str) else None
        if row is None:
            return
        theme = current_palette()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(0, 1, 0, -1)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = self.owner.hover_animator.progress(index)
        fill = blend(QColor(theme.panel), QColor(theme.surface_alt), hover)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(rect.adjusted(2, 1, -2, -1), RADIUS_CONTROL, RADIUS_CONTROL)
        self.selection_glide.paint(painter, option.rect)
        painter.setPen(QPen(QColor(theme.border), 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        left, right = rect.left() + SPACE_M, rect.right() - SPACE_M
        if self.owner.capture_column.width:
            self.owner.capture_column.paint(painter, option, key)
            right = self.owner.capture_column.rect(option.rect).left() - SPACE_S
        value = row_value(row, self.owner.sort.currentText())
        if value:
            font = QFont(option.font)
            font.setPointSizeF(TYPE_CAPTION)
            painter.setFont(font)
            width = painter.fontMetrics().horizontalAdvance(value)
            painter.setPen(QColor(theme.text if selected else theme.text_muted))
            painter.drawText(QRectF(right - width, rect.top(), width, rect.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, value)
            right -= width + SPACE_S
        tag = row_tag(row)
        if tag is not None:
            right = self.paint_tag(painter, option, rect, right, *tag) - SPACE_S
        font = QFont(option.font)
        font.setPointSizeF(TYPE_BODY)
        font.setWeight(QFont.Weight.DemiBold if key not in self.owner.opened else QFont.Weight.Normal)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        name = row.tomogram.name
        split = name.rfind("_")
        prefix, suffix = (name[:split], name[split:]) if split > 0 else ("", name)
        width = max(16, right - left)
        suffix = metrics.elidedText(suffix, Qt.TextElideMode.ElideRight, width)
        room = width - metrics.horizontalAdvance(suffix)
        prefix = metrics.elidedText(prefix, Qt.TextElideMode.ElideMiddle, room) if prefix and room > 16 else ""
        strong = QColor(theme.selection_text if selected else theme.text_strong)
        muted = QColor(theme.text if selected else theme.text_muted)
        text_rect = QRectF(left, rect.top(), width, rect.height())
        flags = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        painter.setPen(muted)
        painter.drawText(text_rect, flags, prefix)
        painter.setPen(strong)
        painter.drawText(text_rect.adjusted(metrics.horizontalAdvance(prefix), 0, 0, 0), flags, suffix)
        painter.restore()

    @staticmethod
    def paint_tag(painter: QPainter, option: QStyleOptionViewItem, rect, right: int, text: str, tone: str) -> int:
        theme = current_palette()
        colour = getattr(theme, STATUS_TONE_COLOURS[tone])
        font = QFont(option.font)
        font.setPointSizeF(TYPE_CAPTION)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        width, height = metrics.horizontalAdvance(text) + 2 * SPACE_S, metrics.height() + 4
        pill = QRectF(right - width, rect.center().y() - height / 2, width, height)
        painter.setPen(QPen(QColor(colour), 1))
        fill = QColor(colour)
        fill.setAlpha(26)
        painter.setBrush(fill)
        painter.drawRoundedRect(pill, height / 2, height / 2)
        painter.setPen(QColor(readable_tone(colour, theme.panel, theme.text_strong)))
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)
        return int(pill.left())


class TomogramList(QWidget):
    selected = Signal(str)
    query_changed = Signal(str)
    opened_changed = Signal(str, bool)
    filters_cleared = Signal()
    filter_restored = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("tomogramList")
        self.rows: dict[str, TomogramRow] = {}
        self.opened: set[str] = set()
        self.ordered_keys: tuple[str, ...] = ()
        self.membership: frozenset[str] | None = None
        self.tag_filter = TagFilter()
        self.tag_rows = lambda: {}
        self.route_keys: frozenset[str] | None = None
        self.scope_label = ""
        self.selected_key: str | None = None
        self.items_by_key: dict[str, QTreeWidgetItem] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_S, 0, 0, 0)
        layout.setSpacing(SPACE_S)
        self.segment = Versions(self, label=lambda text: text.replace(" · ", " "))
        for label, key in (("All", ReviewSegment.ALL), ("Not opened", ReviewSegment.NOT_OPENED),
                           ("Needs attention", ReviewSegment.ATTENTION)):
            self.segment.addItem(label, key)
        self.segment.setAccessibleName("Tomogram review segment")
        self.segment.setToolTip("Show all tomograms, the tomograms that you did not open, or the tomograms with warnings")
        self.segment.rebuild()
        self.segment.hide()
        self.segment.segments.setAccessibleName("Tomogram review segment")
        # Opening rows changes counts without changing the list or canvas width.
        self.segment.segments.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        for button in self.segment.buttons.buttons():
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.query = QLineEdit(self)
        self.query.setObjectName("viewerFilter")
        self.query.setPlaceholderText("Filter tomograms…")
        self.query.setAccessibleName("Filter tomograms")
        self.query.setClearButtonEnabled(True)
        self.sort = QComboBox(self)
        self.sort.setObjectName("tomogramSort")
        self.sort.addItems(SORTS)
        self.sort.setAccessibleName("Sort tomograms")
        self.sort.setToolTip("Sort the tomograms in each group. Residual: the IMOD tiltalign mean residual, in nm. "
                             "Thickness: the Warp estimate. Values in other units go next. Tomograms with no value go last. "
                             "These are recorded values. The app cannot confirm that they apply to this reconstruction.")
        self.sort_tooltip = self.sort.toolTip()
        self.sort.currentIndexChanged.connect(self.sort_changed)
        self.tree = SmoothScrollTree(self)
        self.hover_animator = ItemHoverAnimator(self.tree)
        self.tree.setObjectName("viewerList")
        self.tree.setProperty("tomogramReview", True)
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(0)
        self.tree.setMouseTracking(True)
        self.tree.setItemDelegate(ReviewRowDelegate(self))
        self.capture_column = CaptureColumn(self)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.context_menu)
        self.compact = QComboBox(parent)
        self.compact.setObjectName("tomogramTitleSelector")
        self.compact.setPlaceholderText("Current tomogram is outside this filter")
        self.compact.setItemDelegate(SelectorDelegate(self.compact))
        self.compact.setLabelDrawingMode(QComboBox.LabelDrawingMode.UseDelegate)
        self.compact.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.compact.setAccessibleName("Tomogram selector")
        self.compact.currentIndexChanged.connect(self.compact_selected)
        self.tree.currentItemChanged.connect(self.tree_selected)
        self.query.textChanged.connect(self.filters_changed)
        self.segment.currentIndexChanged.connect(self.filters_changed)
        filters = QHBoxLayout()
        filters.setSpacing(SPACE_S)
        filters.addWidget(self.query, 1)
        filters.addWidget(self.sort)
        layout.addWidget(self.segment.segments)
        layout.addLayout(filters)
        self.result_line = QWidget(self)
        self.result_line.setObjectName("viewerResultLine")
        result_layout = QHBoxLayout(self.result_line)
        result_layout.setContentsMargins(0, 0, 0, 0)
        self.result = QLabel(self)
        self.route_chip = QWidget(self)
        self.route_chip.setObjectName("routeFilterChip")
        chip_layout = QHBoxLayout(self.route_chip)
        chip_layout.setContentsMargins(SPACE_S, SPACE_S, SPACE_S, SPACE_S)
        self.provenance = QLabel(self.route_chip)
        self.provenance.setWordWrap(True)
        self.provenance.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.remove_route_button = link_button("×", self.route_chip, tooltip="Remove the Summary restriction; keep text and segment filters")
        self.remove_route_button.setAccessibleName("Remove Summary restriction")
        self.remove_route_button.clicked.connect(self.remove_route)
        chip_layout.addWidget(self.provenance, 1)
        chip_layout.addWidget(self.remove_route_button)
        self.clear_button = link_button(parent=self)
        self.clear_button.setText("Clear all filters")
        self.clear_button.setToolTip("Clear text, segment and the Summary row restriction")
        self.clear_button.clicked.connect(self.clear_filters)
        result_layout.addWidget(self.result)
        result_layout.addStretch(1)
        result_layout.addWidget(self.clear_button)
        layout.addWidget(self.result_line)
        layout.addWidget(self.route_chip)
        layout.addWidget(self.tree, 1)
        self.refresh_theme()

    def install(self, payload: PreparedTomogramTab) -> None:
        self.rows = {row.tomogram.id: row for row in payload.rows}
        self.membership = None
        self.route_keys, self.scope_label = None, ""
        self.rebuild()

    def filters_changed(self) -> None:
        self.membership = None
        self.route_keys, self.scope_label = None, ""
        self.rebuild()
        self.query_changed.emit(self.query.text())

    def remove_route(self) -> None:
        self.membership = None
        self.route_keys, self.scope_label = None, ""
        self.rebuild()
        self.query_changed.emit(self.query.text())

    def clear_filters(self) -> None:
        self.tag_filter = TagFilter()
        self.filters_cleared.emit()
        with QSignalBlocker(self.query), QSignalBlocker(self.segment):
            self.query.clear()
            self.set_segment(ReviewSegment.ALL)
        self.filters_changed()

    def restrict(self, keys: frozenset[str], label: str) -> None:
        self.route_keys = keys.intersection(self.rows)
        self.scope_label = label or "Summary"
        self.membership = self.route_keys
        self.rebuild()

    def sort_changed(self) -> None:
        # Sorting changes traversal, never the stable Not opened working set.
        self.set_ordering_notice("")
        self.rebuild()
        self.query_changed.emit(self.query.text())

    def set_ordering_notice(self, notice: str) -> None:
        self.sort.setToolTip(self.sort_tooltip + ("\n" + notice if notice else ""))
        self.sort.setAccessibleDescription(notice)

    def restore_filters(self, state: TomogramViewState, ordering_notice: str = "") -> str:
        sort, notice = restored_sort(state.sort)
        self.tag_filter = TagFilter()
        self.filter_restored.emit()
        same_membership = keep_not_opened_membership(self.filter_value(), ListFilter(state.query, state.segment, sort))
        self.route_keys, self.scope_label = None, ""
        with QSignalBlocker(self.query), QSignalBlocker(self.segment), QSignalBlocker(self.sort):
            self.query.setText(state.query)
            self.set_segment(state.segment)
            self.sort.setCurrentText(sort)
        self.set_ordering_notice(ordering_notice or notice)
        if not same_membership:
            self.membership = None
        self.rebuild()
        return sort

    def segment_key(self) -> ReviewSegment:
        return ReviewSegment(self.segment.currentData())

    def set_segment(self, key: ReviewSegment) -> None:
        self.segment.setCurrentIndex(self.segment.findData(key))

    @staticmethod
    def needs_attention(row: TomogramRow) -> bool:
        return needs_attention(row)

    def filter_value(self) -> ListFilter:
        return ListFilter(self.query.text(), self.segment_key(), self.sort.currentText(), self.route_keys, self.scope_label, self.tag_filter)

    def rebuild(self) -> None:
        result = list_membership(self.rows, self.filter_value(), self.membership, self.opened,
                                 tag_rows=self.tag_rows() if self.membership is None and self.tag_filter.active else None)
        self.membership, keys = result.working_set, result.ordered_keys
        self.ordered_keys = keys
        shown: dict[str, int] = {}
        for key in keys:
            shown[self.rows[key].group] = shown.get(self.rows[key].group, 0) + 1
        scroll = self.tree.verticalScrollBar().value()
        with QSignalBlocker(self.tree), QSignalBlocker(self.compact):
            self.items_by_key.clear()
            self.tree.clear()
            self.compact.clear()
            group = None
            for key in keys:
                row = self.rows[key]
                if row.group != group:
                    group = row.group
                    heading = make_group_row(GroupHeader(group, shown[group]))
                    heading.setData(0, LIST_GROUP_SHOWN_ROLE, (shown[group], 0))
                    heading.setData(0, Qt.ItemDataRole.AccessibleTextRole, f"{group}, {shown[group]}")
                    self.tree.addTopLevelItem(heading)
                item = QTreeWidgetItem([row.tomogram.name])
                item.setData(0, Qt.ItemDataRole.UserRole, key)
                item.setToolTip(0, "\n".join((row.tomogram.name, row.link.explanation, *row.link.warnings, *row.tomogram.warnings,
                                              *(warning for volume in row.tomogram.volumes for warning in volume.warnings))))
                font = item.font(0)
                font.setWeight(QFont.Weight.DemiBold if key not in self.opened else QFont.Weight.Normal)
                item.setFont(0, font)
                self.tree.addTopLevelItem(item)
                self.items_by_key[key] = item
                self.compact.addItem(row.tomogram.name, key)
                self.compact.setItemData(self.compact.count()-1, row.tomogram.name, Qt.ItemDataRole.ToolTipRole)
                if key == self.selected_key:
                    self.tree.setCurrentItem(item)
                    self.compact.setCurrentIndex(self.compact.count() - 1)
            if self.selected_key not in keys:
                self.tree.setCurrentIndex(QModelIndex())
                self.compact.setCurrentIndex(-1)
        # Restoring currentItem can start an eased scroll. A refill restores
        # its saved position synchronously, including the scroller's target.
        self.tree.executeDelayedItemsLayout()
        self.tree.scroller.scroll_to(scroll, animate=False)
        self.compact.setToolTip(self.compact.currentText())
        self.refresh_counts()
        veil(self.tree.viewport(), QColor(current_palette().panel), start_opacity=LIST_REFILL_VEIL_OPACITY)

    def refresh_counts(self) -> None:
        self.capture_column.reflow()
        self.result.setText(f"{len(self.ordered_keys)} of {len(self.rows)} shown")
        self.provenance.setText(f"from {self.scope_label}" if self.route_keys is not None else "")
        self.route_chip.setVisible(self.route_keys is not None)
        self.clear_button.setVisible(bool(self.query.text()) or self.segment_key() != ReviewSegment.ALL or self.route_keys is not None or self.tag_filter.active)
        not_opened = sum(key not in self.opened for key in self.rows)
        self.segment.setItemText(0, f"All · {len(self.rows)}")
        self.segment.setItemText(1, f"Not opened · {not_opened}")
        self.segment.setItemText(2, f"Needs attention · {sum(self.needs_attention(row) for row in self.rows.values())}")
        self.segment.setItemData(0, f"Show all {len(self.rows)} tomograms. Bold names are tomograms that you did not open.", Qt.ItemDataRole.ToolTipRole)
        self.segment.setItemData(2, f"Show the {sum(self.needs_attention(row) for row in self.rows.values())} tomograms "
                                 "with warnings", Qt.ItemDataRole.ToolTipRole)
        self.segment.setItemData(1, f"Show the {not_opened} tomograms that you did not open. A tomogram that you open "
                                 "stays until you change the filter.", Qt.ItemDataRole.ToolTipRole)
        self.segment.setToolTip(self.segment.currentData(Qt.ItemDataRole.ToolTipRole))
        self.segment.relabel()
        self.segment.sync()  # Restores also use a signal blocker around the combo.

    def refresh_theme(self) -> None:
        for index in range(self.sort.count()):
            self.sort.setItemIcon(index, themed_icon("arrow-up-down", size=14))
        self.capture_column.icon = themed_icon("camera", size=14)
        self.capture_column.reflow()
        self.tree.viewport().update()

    def select(self, key: str) -> None:
        self.selected_key = key
        with QSignalBlocker(self.tree), QSignalBlocker(self.compact):
            item = self.items_by_key.get(key)
            if item is None:
                self.tree.setCurrentIndex(QModelIndex())
            else:
                self.tree.setCurrentItem(item)
                self.tree.scrollToItem(item)
            self.compact.setCurrentIndex(self.compact.findData(key))
            self.compact.setToolTip(self.compact.currentText())

    def mark_opened(self, key: str) -> None:
        changed = key not in self.opened
        self.opened.add(key)
        if changed:
            self.opened_changed.emit(key, True)
            item = self.items_by_key.get(key)
            if item is not None:
                font = item.font(0)
                font.setBold(False)
                item.setFont(0, font)
            self.refresh_counts()  # Keep the stable working membership.

    def tree_selected(self, item: QTreeWidgetItem | None) -> None:
        if item is not None:
            key = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(key, str):
                self.selected.emit(key)

    def compact_selected(self, index: int) -> None:
        key = self.compact.itemData(index)
        if isinstance(key, str):
            self.selected.emit(key)

    def context_menu(self, point) -> None:
        item = self.tree.itemAt(point)
        key = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        if not isinstance(key, str):
            return
        menu = QMenu(self)
        menu.addAction("Captures…", lambda: self.capture_column.requested.emit(key))
        action = menu.addAction("Mark as not opened")
        if menu.exec(self.tree.viewport().mapToGlobal(point)) is action:
            self.opened.discard(key)
            self.opened_changed.emit(key, False)
            self.rebuild()
