"""The Project panel: its heading and count, filter, badge key, tree and drop
invitation, and what the tree shows of the loaded project.

Display only. ``MainWindow`` connects the tree's selection, activation and
context menu to itself (they change the scope or the project) and appends
its drop widgets to ``layout``.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

from PySide6.QtCore import QElapsedTimer, QSize, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QMenu, QToolButton, QWidgetAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.presentation.design_tokens import SPACE_M, SPACE_S
from tomography_session_browser.presentation.project_tree import PROJECT_BADGE_MEANINGS, ProjectTreeGroup
from tomography_session_browser.ui.list_decorations import is_project_badge
from tomography_session_browser.ui.motion import ItemHoverAnimator
from tomography_session_browser.presentation.project_folders import UserGroup, UserHeading, arrange
from tomography_session_browser.ui.project_tree.items import (ProjectTreeItems, set_tree_item_icon, style_project_group_item,
                                                            project_group_for_row, style_user_group_item, tree_expansion_id_for,
                                                            user_group_expansion_id, user_group_for_row)
from tomography_session_browser.presentation.scope_identity import scope_key
from tomography_session_browser.ui.project_tree.widgets import FOLDED_SESSION_ROLE
from tomography_session_browser.ui.project_tree.widgets import (
    EXPANSION_ROLE,
    HIGHLIGHT_ROLE,
    OBJECT_ROLE,
    ProjectTreeDelegate,
    ProjectTreeWidget,
)
from tomography_session_browser.ui.widgets.project_badge_legend import ProjectBadgeLegend
from tomography_session_browser.ui.widgets.session_drop import ProjectDropHint

#: Qt's QWIDGETSIZE_MAX: "no maximum" for a widget's size.
_QWIDGETSIZE_MAX = (1 << 24) - 1

# Badge meanings (``project_tree.PROJECT_BADGE_MEANINGS``) word the key
# under the project filter (``ProjectBadgeLegend``) and every badge tooltip.
PROJECT_BADGE_LEGEND_TOOLTIP = "\n".join(
    f"{badge} — {meaning}" for badge, meaning in PROJECT_BADGE_MEANINGS.items()
)


class ProjectTreePanel:
    def __init__(
        self,
        parent: QWidget,
        *,
        header: Callable[[str, QWidget, QLabel], QWidget],
        inset: int,
        has_sessions: Callable[[], bool],
        open_help: Callable[[], None] | None = None,
    ) -> None:
        self._has_sessions = has_sessions
        #: Expansion before a filter opened matching branches, restored when it clears.
        self._filter_expanded_ids: set[str] | None = None
        #: Every tilt-series row, for the cross-view highlight.
        self.tilt_items_by_id: dict[str, list[QTreeWidgetItem]] = {}
        self.scope_aliases: dict[str, QTreeWidgetItem] = {}

        self.widget = panel = QWidget(parent)
        panel.setObjectName("projectPanel")
        self.layout = layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.count = QLabel("0 sessions")
        self.count.setObjectName("panelCount")
        heading = header("PROJECT", panel, self.count)
        layout.addWidget(heading)
        self.legend_button = QToolButton(heading)
        self.legend_button.setText("?")
        self.legend_button.setAccessibleName("Project badge legend")
        self.legend_button.setToolTip("Explain project badges")
        self.legend_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.legend_button.setProperty("menuIndicator", "none")
        heading.layout().addWidget(self.legend_button)

        self.filter = QLineEdit()
        self.filter.setObjectName("treeSearch")
        self.filter.setPlaceholderText("Filter project...")
        self.filter.setAccessibleName("Filter project")
        self.filter.setClearButtonEnabled(True)
        self.filter.setToolTip("Filter project groups, sessions, samples, and acquisition items")
        self._clear_filter_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self.filter)
        self._clear_filter_shortcut.setProperty("helpBindingId", "project.filter.clear")
        self._clear_filter_shortcut.setWhatsThis("Clear the project filter")
        self._clear_filter_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self._clear_filter_shortcut.activated.connect(self.filter.clear)
        self.filter.textChanged.connect(self.apply_filter)
        # A line edit takes drops of text by default; folders dragged over
        # the filter belong to the panel's drop, not the filter.
        self.filter.setAcceptDrops(False)
        filter_wrap = QWidget(panel)
        filter_layout = QVBoxLayout(filter_wrap)
        filter_layout.setContentsMargins(inset, 0, inset, SPACE_S)
        filter_layout.addWidget(self.filter)
        layout.addWidget(filter_wrap)

        # The LS / AT / DC / ! badges previously had no in-app explanation at
        # all — their meaning lived only in the handover documents. The key
        # shows the badges the tree holds, drawn as the tree draws them, and
        # wraps between entries in a narrow panel (harmonisation E1).
        legend_menu = QMenu(self.legend_button)
        self.legend_button.setMenu(legend_menu)
        legend_body = QWidget(legend_menu)
        legend_layout = QVBoxLayout(legend_body)
        legend_layout.setContentsMargins(SPACE_M, SPACE_M, SPACE_M, SPACE_M)
        self.badge_legend = ProjectBadgeLegend(legend_body)
        self.badge_legend.setFixedWidth(260)
        legend_layout.addWidget(self.badge_legend)
        self.badge_legend.setToolTip(PROJECT_BADGE_LEGEND_TOOLTIP)
        self.badge_legend.setAccessibleDescription(PROJECT_BADGE_LEGEND_TOOLTIP)
        legend_action = QWidgetAction(legend_menu)
        legend_action.setDefaultWidget(legend_body)
        legend_menu.addAction(legend_action)
        legend_menu.addSeparator()
        self.legend_help_action = legend_menu.addAction("More in Help →")
        self.legend_help_action.setObjectName("moreInHelpAction")
        font = self.legend_help_action.font()
        font.setUnderline(True)
        self.legend_help_action.setFont(font)
        self.legend_help_action.setEnabled(open_help is not None)
        def more_in_help(_checked: bool = False) -> None:
            legend_menu.close()
            if open_help is not None:
                open_help()
        self.legend_help_action.triggered.connect(more_in_help)
        self.legend_button.hide()

        self.tree = tree = ProjectTreeWidget()
        tree.setObjectName("projectTree")
        # The delegate paints the selection as one row-wide fill (plan C9);
        # this turns off the sheet's per-column one. The report dialog's tree
        # shares the object name but not the delegate, so it keeps the sheet's.
        tree.setProperty("paintsRowSelection", True)
        tree.setColumnCount(2)
        tree.setHeaderHidden(True)
        tree.setIndentation(14)
        tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tree.setAlternatingRowColors(False)
        tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        tree.setAllColumnsShowFocus(True)
        tree_delegate = ProjectTreeDelegate(tree)
        tree_delegate.hover_animator = ItemHoverAnimator(tree)
        # No selection glide here: every project row prepares and publishes a
        # whole scope, which keeps the UI thread busy for hundreds of
        # milliseconds on real sessions, so a moving fill froze between rows
        # while the focus ring had already moved. The selected row is filled
        # at once; acquisition and Tomograms lists keep their glide.
        tree.setItemDelegate(tree_delegate)
        tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tree.header().setStretchLastSection(False)
        tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        tree.header().setMinimumSectionSize(44)
        tree.setIconSize(QSize(18, 18))
        # With nothing loaded the tree holds only recent sessions, if any, and
        # shrinks to them; the drop invitation takes the rest of the panel.
        tree.itemExpanded.connect(lambda _item: self.sync_empty_state())
        tree.itemCollapsed.connect(lambda _item: self.sync_empty_state())
        layout.addWidget(tree, stretch=1)
        self.drop_hint = ProjectDropHint(panel)
        layout.addWidget(self.drop_hint, stretch=1)

    # -- what the tree holds -------------------------------------------------

    def populate(
        self,
        items: ProjectTreeItems,
        groups: Sequence[ProjectTreeGroup],
        *,
        session_count: int,
        current_key: str | None,
        recent_paths: Iterable[str],
        highlighted_tilt_series_id: str | None,
        user_groups: Sequence[UserGroup] = (),
        open_user_group: str | None = None,
    ) -> None:
        """Rebuild the rows for ``groups``, keeping the selected group, the
        expanded rows, the filter and the highlight.

        ``user_groups`` puts the user's headings in place (B4); a rebuild clears
        every mark. ``open_user_group`` opens a heading the user just made.
        """

        tree = self.tree
        expanded_ids = self.expanded_node_ids()
        if open_user_group is not None:
            expanded_ids.add(user_group_expansion_id(next(group for group in user_groups if group.key == open_user_group)))
        previous_signal_state = tree.blockSignals(True)
        tree.setUpdatesEnabled(False)
        try:
            self.tilt_items_by_id = items.tilt_items_by_id
            self.scope_aliases = items.scope_aliases
            tree.clear()
            processing_count = len(items.processing_sources)
            roots = list(groups)
            if session_count or processing_count:
                session_label = count_phrase(session_count, "session")
                if processing_count and not session_count:
                    self.count.setText(count_phrase(processing_count, "processing folder"))
                else:
                    self.count.setText(session_label + (f" · {count_phrase(processing_count, 'processing folder')}" if processing_count else ""))
            else:
                # The panel's drop invitation says it (``ProjectDropHint``).
                self.count.setText("0 sessions")

            current_item: QTreeWidgetItem | None = None
            first_group_item: QTreeWidgetItem | None = None
            for entry in arrange(roots, user_groups):
                if isinstance(entry, UserHeading):
                    group_item = items.user_group_item(entry)
                    tree.addTopLevelItem(group_item)
                    group_item = group_item.child(0)  # A heading is never the fallback current row.
                else:
                    group_item = items.group_item(entry)
                    items.populate_group_item(group_item, entry)
                    tree.addTopLevelItem(group_item)
                if first_group_item is None:
                    first_group_item = group_item
            if current_key is not None:
                current_item = self.group_item_for_key(current_key)
            if current_item is None:
                current_item = first_group_item
            if current_item is not None:
                tree.setCurrentItem(current_item)
            recent = items.recent_sessions_section(recent_paths)
            if recent is not None:
                tree.addTopLevelItem(recent)
            self.restore_expanded_node_ids(expanded_ids)
            self.apply_filter(self.filter.text())
            self.set_highlighted_tilt_series_id(None, highlighted_tilt_series_id)
        finally:
            tree.setUpdatesEnabled(True)
            tree.blockSignals(previous_signal_state)
        self.sync_badge_legend()
        self.sync_empty_state()

    def badges(self) -> set[str]:
        """The project badges (LS, AT, DC, !) the tree currently shows."""

        badges: set[str] = set()
        pending = [self.tree.topLevelItem(index) for index in range(self.tree.topLevelItemCount())]
        while pending:
            item = pending.pop()
            if item is None:
                continue
            if is_project_badge(item.text(1)):
                badges.add(item.text(1))
            pending.extend(item.child(index) for index in range(item.childCount()))
        return badges

    def sync_badge_legend(self) -> None:
        """Key the badges the tree holds; none loaded, no key."""

        badges = self.badges() if self._has_sessions() else set()
        self.badge_legend.set_badges(badges)
        self.legend_button.setVisible(bool(badges))

    def sync_empty_state(self) -> None:
        """With nothing loaded, invite a drop; the tree keeps only recents.

        Display only: which widgets show, never what the tree holds.
        """

        empty = not self._has_sessions()
        self.drop_hint.setVisible(empty)
        rows = self.tree.topLevelItemCount()
        self.tree.setVisible(bool(rows) or not empty)
        if empty and rows:
            self.tree.setMaximumHeight(self._content_height())
        else:
            self.tree.setMaximumHeight(_QWIDGETSIZE_MAX)

    def _content_height(self) -> int:
        """The height the tree's shown rows need, so the hint gets the rest."""

        row_height = self.tree.sizeHintForRow(0)
        if row_height <= 0:
            row_height = self.tree.fontMetrics().height() + SPACE_S * 2
        shown = 0
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            if item.isHidden():
                continue
            shown += 1
            if item.isExpanded():
                shown += sum(1 for child in range(item.childCount()) if not item.child(child).isHidden())
        return shown * row_height + 2 * self.tree.frameWidth() + SPACE_S

    def refresh_theme(self) -> None:
        """Recolour what the style sheet cannot: group rows and every icon."""

        def refresh_tree_item(item: QTreeWidgetItem) -> None:
            value = item.data(0, OBJECT_ROLE)
            group = project_group_for_row(item)
            if group is not None:
                style_project_group_item(item, group)
            elif user_group_for_row(item) is not None:
                style_user_group_item(item)
            else:
                set_tree_item_icon(item, value)
            for child_index in range(item.childCount()):
                refresh_tree_item(item.child(child_index))

        for index in range(self.tree.topLevelItemCount()):
            refresh_tree_item(self.tree.topLevelItem(index))

    # -- expansion and filtering ---------------------------------------------

    def expanded_node_ids(self) -> set[str]:
        expanded: set[str] = set()

        def visit(item: QTreeWidgetItem) -> None:
            node_id = item.data(0, EXPANSION_ROLE)
            if isinstance(node_id, str) and item.isExpanded():
                expanded.add(node_id)
                if item.data(0, FOLDED_SESSION_ROLE) is not None:
                    expanded.add(tree_expansion_id_for('', item.data(0, OBJECT_ROLE)))
            for index in range(item.childCount()):
                visit(item.child(index))

        for index in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(index))
        return expanded

    def restore_expanded_node_ids(self, expanded_ids: set[str]) -> None:
        def visit(item: QTreeWidgetItem) -> None:
            node_id = item.data(0, EXPANSION_ROLE)
            sample_expanded = (item.data(0, FOLDED_SESSION_ROLE) is not None
                               and tree_expansion_id_for('', item.data(0, OBJECT_ROLE)) in expanded_ids)
            item.setExpanded(isinstance(node_id, str) and node_id in expanded_ids or sample_expanded)
            for index in range(item.childCount()):
                visit(item.child(index))

        for index in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(index))

    def apply_filter(self, text: str) -> None:
        """Show the rows whose name or tooltip holds ``text``, and their
        ancestors; clearing it restores the expansion from before."""

        query = text.strip().lower()
        if query and self._filter_expanded_ids is None:
            self._filter_expanded_ids = self.expanded_node_ids()

        def apply(item: QTreeWidgetItem) -> bool:
            own_match = not query or query in item.text(0).lower() or query in item.toolTip(0).lower()
            child_match = False
            for index in range(item.childCount()):
                child_match = apply(item.child(index)) or child_match
            visible = own_match or child_match
            item.setHidden(not visible)
            if query and child_match:
                item.setExpanded(True)
            return visible

        for index in range(self.tree.topLevelItemCount()):
            apply(self.tree.topLevelItem(index))
        if not query and self._filter_expanded_ids is not None:
            self.restore_expanded_node_ids(self._filter_expanded_ids)
            self._filter_expanded_ids = None

    # -- finding rows ----------------------------------------------------------

    def select_group_by_key(self, key: str) -> None:
        item = self.group_item_for_key(key)
        if item is not None:
            self.tree.setCurrentItem(item)

    def group_item_for_key(self, key: str) -> QTreeWidgetItem | None:
        pending = [self.tree.topLevelItem(index) for index in range(self.tree.topLevelItemCount())]
        while pending:
            item = pending.pop()
            value = project_group_for_row(item)
            if value is not None and value.key == key:
                return item
            pending.extend(item.child(index) for index in range(item.childCount()))
        return None

    @staticmethod
    def group_for_item(item: QTreeWidgetItem | None) -> ProjectTreeGroup | None:
        current = item
        while current is not None:
            value = project_group_for_row(current)
            if value is not None:
                return value
            current = current.parent()
        return None

    def alias_for_scope(self, scope: object) -> QTreeWidgetItem | None:
        return self.scope_aliases.get(scope_key(scope))

    # -- selection repaint and the cross-view highlight ----------------------

    def repaint_rows(self, current: QTreeWidgetItem | None, previous: QTreeWidgetItem | None) -> None:
        viewport = self.tree.viewport()
        dirty_rect = None
        for item in (current, previous):
            if item is None:
                continue
            rect = self.tree.visualItemRect(item)
            if not rect.isValid():
                continue
            rect.setLeft(0)
            rect.setRight(viewport.width())
            rect = rect.adjusted(0, -3, 0, 3)
            dirty_rect = rect if dirty_rect is None else dirty_rect.united(rect)
        if dirty_rect is None:
            return
        viewport.update(dirty_rect)
        QTimer.singleShot(0, lambda rect=dirty_rect: viewport.update(rect))

    def set_highlighted_tilt_series_id(
        self,
        previous_tilt_series_id: str | None,
        tilt_series_id: str | None,
    ) -> dict[str, int]:
        """Move the highlight between tilt-series rows; returns lookup and
        repaint timings in milliseconds."""

        timings = {"lookup_previous_ms": 0, "lookup_new_ms": 0, "repaint_ms": 0}
        previous_items: tuple[QTreeWidgetItem, ...] = ()
        new_items: tuple[QTreeWidgetItem, ...] = ()
        if previous_tilt_series_id:
            timer = QElapsedTimer()
            timer.start()
            previous_items = tuple(self.tilt_items_by_id.get(previous_tilt_series_id, ()))
            timings["lookup_previous_ms"] = timer.elapsed()
        if tilt_series_id:
            timer = QElapsedTimer()
            timer.start()
            new_items = tuple(self.tilt_items_by_id.get(tilt_series_id, ()))
            timings["lookup_new_ms"] = timer.elapsed()
        timer = QElapsedTimer()
        timer.start()
        if previous_items:
            self._set_rows_highlighted(previous_items, False)
        if new_items:
            self._set_rows_highlighted(new_items, True)
        timings["repaint_ms"] = timer.elapsed()
        return timings

    def _set_rows_highlighted(self, items: tuple[QTreeWidgetItem, ...], highlighted: bool) -> None:
        changed: list[QTreeWidgetItem] = []
        for item in items:
            if bool(item.data(0, HIGHLIGHT_ROLE)) == highlighted:
                continue
            item.setData(0, HIGHLIGHT_ROLE, highlighted)
            item.setData(1, HIGHLIGHT_ROLE, highlighted)
            changed.append(item)
        for item in changed:
            self.repaint_rows(item, item)
