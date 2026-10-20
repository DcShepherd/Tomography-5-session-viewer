"""List pane for the image viewer."""
from __future__ import annotations
from collections.abc import Callable
from pathlib import Path
from typing import Any
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QHeaderView, QMenu, QSplitter, QToolButton, QTreeWidgetItem, QVBoxLayout, QWidget
from tomography_session_browser.services.preview_sources import PreviewSources
from tomography_session_browser.domain.markers import ImageMarker
from tomography_session_browser.presentation.design_tokens import SPACE_S
from tomography_session_browser.presentation.viewer_items import singular_title_for_items as _singular_title_for_items, panel_title_for_items as _panel_title_for_items, summary_for_item as _summary_for_item, status_label_for_item as _status_label_for_item
from tomography_session_browser.presentation.empty_states import KIND_LOAD_FAILED, EmptyState, viewer_empty_state
from tomography_session_browser.ui.widgets.empty_state_panel import EmptyStatePanel
from tomography_session_browser.ui.motion import ItemHoverAnimator
from tomography_session_browser.ui.theme import TIER_SUPPORTING, apply_tier
from tomography_session_browser.services.item_status import ItemListStatus, display_status_label, split_filter_query
from tomography_session_browser.ui.viewer.list_filter_bar import ListFilterBar
from tomography_session_browser.ui.viewer.row_index import RowIndex
from tomography_session_browser.ui.widgets.smooth_tree import SmoothScrollTree
from tomography_session_browser.ui.viewer.constants import LIST_PRIMARY_ROLE, LIST_STATUS_ROLE, LIST_SUMMARY_ROLE, LIST_TAGS_ROLE, LIST_TOOLTIP_ROLE, PLAIN_PROVENANCE, VIEWER_CANVAS_MIN_WIDTH_PX, VIEWER_LIST_FLOOR_PX, VIEWER_LIST_MIN_WIDTH_PX, VIEWER_OBJECT_ROLE, VIEWER_KEY_ROLE
from tomography_session_browser.ui.viewer.list_rows import ViewerListDelegate, list_status_mark, warning_count_text, warning_tooltip
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from tomography_session_browser.ui.viewer.viewer_tab import ViewerTab


class ViewerListPane:
    """List pane operations for one ViewerTab."""

    def __init__(self, view: ViewerTab) -> None:
        self.view = view

    def create_splitter(self, viewer_panel: QWidget, show_list: bool) -> QSplitter:
        splitter = QSplitter(Qt.Orientation.Horizontal, self.view)
        splitter.setObjectName("viewerSplitter")
        splitter.setChildrenCollapsible(True)
        splitter.addWidget(viewer_panel)
        self.list_splitter = splitter if show_list else None
        if show_list:
            list_panel = QWidget(splitter)
            list_panel.setObjectName("viewerListPanel")
            self.list_panel = list_panel
            list_layout = QVBoxLayout(list_panel)
            list_layout.setContentsMargins(SPACE_S, 0, SPACE_S, 0)
            list_layout.setSpacing(0)
            list_top = QWidget(list_panel)
            list_top_layout = QHBoxLayout(list_top)
            list_top_layout.setContentsMargins(0, 10, 0, SPACE_S)
            list_top_layout.addWidget(self.view.list_header)
            list_top_layout.addStretch(1)
            list_top_layout.addWidget(self.list_toggle)
            list_layout.addWidget(list_top)
            filter_wrap = QWidget(list_panel)
            filter_layout = QVBoxLayout(filter_wrap)
            filter_layout.setContentsMargins(0, 0, 0, SPACE_S)
            filter_layout.addWidget(self.filter_bar)
            list_layout.addWidget(filter_wrap)
            list_layout.addWidget(self.view.list, stretch=1)
            splitter.addWidget(list_panel)
            # The entity icon consumes 24 px including its text gap. Reserve
            # enough additional width for the icon and an ordinary entity
            # name such as ``SearchMap_1`` beside the longest common status
            # badge, rather than introducing immediate elision.
            self.view.list.setMinimumWidth(VIEWER_LIST_MIN_WIDTH_PX)
            viewer_panel.setMinimumWidth(VIEWER_CANVAS_MIN_WIDTH_PX)
            # Keep the right-side list wide enough for status summaries while
            # leaving the image viewer with most of the horizontal space. The
            # user can still drag or collapse the splitter handle.
            splitter.setSizes([1180, 300])
            splitter.setStretchFactor(0, 6)
            splitter.setStretchFactor(1, 1)
            splitter.setCollapsible(0, False)
            splitter.setCollapsible(1, True)

        return splitter

    def create_widgets(self, show_list: bool, status_segments: tuple[str, ...] = ("failed", "complete")) -> None:
        self.view.list = SmoothScrollTree(self.view)
        self.view.list.setObjectName("viewerList")
        # Every lookup of a row by its object goes through this, never a position.
        self.rows = RowIndex(self.view.list)
        self.view.list.setColumnCount(1)
        self.view.list.setHeaderHidden(True)
        self.view.list.setIconSize(QSize(16, 16))
        # A flat list: no row ever has children. The branch column it used to
        # reserve took the selection colour as a separate block beside the
        # row's own fill (plan C9), and cost the names 12 px.
        self.view.list.setRootIsDecorated(False)
        self.view.list.setIndentation(0)
        self.view.list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.view.list.header().setStretchLastSection(False)
        self.view.list.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        list_delegate = ViewerListDelegate(self.view.list)
        list_delegate.hover_animator = ItemHoverAnimator(self.view.list)
        self.view.list.setItemDelegate(list_delegate)
        self.view.list.setAlternatingRowColors(False)
        self.view.list.setMouseTracking(True)
        self.view.list.currentItemChanged.connect(self.view._item_changed)
        self.view.list.setVisible(show_list)

        self.filter_bar = ListFilterBar(self.view, offered=status_segments)
        self.filter_bar.setVisible(show_list)
        self.filter_bar.query_changed.connect(self.on_query_changed)
        self.view.list_header = QLabel("Items", self.view)
        self.view.list_header.setObjectName("viewerPanelHeader")
        self.view.list_header.setVisible(show_list)
        self.create_collapse_controls()

    # ------------------------------------------------- a list of one, collapsed

    def create_collapse_controls(self) -> None:
        """The toggle in the list's header, and the compact row that stands in for the list.

        The list stays by default (R8). Only while it holds exactly one object
        row may it be collapsed to a selector above the canvas, and the choice
        is this tab's, for the window's lifetime: nothing stores it.
        """

        self.list_panel: QWidget | None = None
        self.list_collapsed = False
        self.list_toggle = QToolButton(self.view)
        self.list_toggle.setObjectName("viewerListToggle")
        self.list_toggle.setText("Hide list")
        self.list_toggle.setToolTip("Hide the list: there is one item, and it stays selected")
        self.list_toggle.setAccessibleName("Hide the list")
        self.list_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.list_toggle.clicked.connect(lambda: self.set_list_collapsed(True))
        self.list_toggle.setVisible(False)

        self.compact_row = QWidget(self.view)
        self.compact_row.setObjectName("viewerCompactSelector")
        layout = QHBoxLayout(self.compact_row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_S)
        self.compact_heading = QLabel("", self.compact_row)
        self.compact_heading.setObjectName("viewerCompactHeading")
        apply_tier(self.compact_heading, TIER_SUPPORTING)
        layout.addWidget(self.compact_heading)
        self.compact_selector = QToolButton(self.compact_row)
        self.compact_selector.setObjectName("viewerCompactItem")
        self.compact_selector.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.compact_selector.setMenu(QMenu(self.compact_selector))
        layout.addWidget(self.compact_selector)
        layout.addStretch(1)
        self.show_list_button = QToolButton(self.compact_row)
        self.show_list_button.setObjectName("viewerShowList")
        self.show_list_button.setText("Show list")
        self.show_list_button.setAccessibleName("Show the list")
        self.show_list_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.show_list_button.clicked.connect(lambda: self.set_list_collapsed(False))
        layout.addWidget(self.show_list_button)
        self.compact_row.setVisible(False)

    def set_list_collapsed(self, collapsed: bool) -> None:
        """Remember the choice for this tab, and apply it if the list holds one row."""

        self.list_collapsed = collapsed
        self.sync_collapse()
        (self.show_list_button if collapsed else self.view.list).setFocus(Qt.FocusReason.OtherFocusReason)

    def sync_collapse(self) -> None:
        """Show the list, or its compact row, as this tab's rows and choice say.

        The toggle is there only while the list holds exactly one object row;
        with none or several the list is shown, and the choice waits for the
        next time this tab holds one.
        """

        panel = self.list_panel
        if panel is None:
            return
        rows = list(self.rows.object_rows())
        one_row = len(rows) == 1
        filtered = self.filter_bar.is_filtered()
        collapsed = self.list_collapsed and one_row and not filtered
        self.list_toggle.setVisible(one_row)
        panel.setVisible((bool(rows) or filtered) and not collapsed)
        self.compact_row.setVisible(collapsed)
        if collapsed:
            row = rows[0]
            name = str(row.data(0, LIST_PRIMARY_ROLE) or row.text(0))
            self.compact_heading.setText(_singular_title_for_items(self.view.parts.tab_selection.items))
            self.compact_selector.setText(name)
            self.compact_selector.setToolTip(f"{name}: the one item in this list")
            menu = self.compact_selector.menu()
            menu.clear()
            action = menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(True)
        else:
            # The list was out of sight while the item was chosen: show it on its row.
            current = self.view.parts.tab_selection.current_value
            row = self.rows.row_for_value(current) if current is not None else None
            if row is not None and self.view.list.currentItem() is not row:
                blocked = self.view.list.blockSignals(True)
                try:
                    self.view.list.setCurrentItem(row)
                finally:
                    self.view.list.blockSignals(blocked)

    # ----------------------------------------------------------------- query

    def query(self) -> str:
        """The list's filter, whole: the status tag a segment owns and the box's text."""

        return self.filter_bar.query()

    def set_query(self, text: str, *, notify: bool = True,
                  row_keys: frozenset[str] | None = None, scope_label: str = "") -> None:
        """Make ``text`` the filter; ``set_query("")`` clears all of it.

        ``notify=False`` is for a rebuild, which applies the filter itself once
        the rows exist, without the signal driving a refresh in its middle.
        """

        self.filter_bar.set_query(text, notify=notify, row_keys=row_keys, scope_label=scope_label)
        if not notify:
            self.view._filter_list()

    def on_query_changed(self, _query: str) -> None:
        self.view._filter_list()
        self.view.query_changed.emit(self.query())

    def filter_list(self, text: str | None = None) -> None:
        query = (self.query() if text is None else text).strip()
        # ``is:<tag>`` words match a row's status tags exactly (the status
        # segments and the dashboard's drill-downs use them); the rest is free text.
        parsed = split_filter_query(query)
        visible_count = 0
        total = 0
        in_scope_count = 0
        tag_counts: dict[str, int] = {}
        for item in self.rows.object_rows():
            total += 1
            status = str(item.data(0, LIST_STATUS_ROLE) or "")
            summary = str(item.data(0, LIST_SUMMARY_ROLE) or "")
            row_tags = item.data(0, LIST_TAGS_ROLE) or ()
            keys = self.filter_bar.row_keys
            in_scope = keys is None or item.data(0, VIEWER_KEY_ROLE) in keys
            if in_scope:
                in_scope_count += 1
                for tag in row_tags:
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
            matches = in_scope and parsed.matches(row_tags, item.text(0), status, summary, item.toolTip(0))
            item.setHidden(not matches)
            if matches:
                visible_count += 1
        self.rows.refresh_groups(failed_filtered="failed" in parsed.tags)
        # The segments say how many rows each status has, whatever the box says.
        self.filter_bar.set_counts(tag_counts, in_scope_count)
        self.filter_bar.set_result(visible_count, total)
        self.sync_collapse()
        self.view._refresh_empty_state_panel()

    def refresh_filter_feedback(self, visible: int, total: int) -> None:
        """Retain the image while explaining when its canonical row is hidden."""
        filtered = self.filter_bar.is_filtered()
        messages = []
        if filtered and not visible:
            messages.append(f"No matching {self.current_entity_label().lower()}.")
        current = self.view.parts.tab_selection.current_value
        row = self.rows.row_for_value(current) if current is not None else None
        if filtered and row is not None and row.isHidden() and self.view.viewer.has_image():
            messages.append("Current image is outside this filter.")
        feedback = " ".join(messages)
        self.filter_bar.set_feedback(feedback)
        announced = (f"{visible} of {total} shown, filtered by {self.filter_bar.description()}"
                     if filtered else f"{total} shown")
        self.view.list_header.setAccessibleDescription(". ".join(part for part in (announced, feedback) if part))

    def item_status_for_value(self, value: Any) -> ItemListStatus | None:
        """The shared row status this tab shows for ``value``, if it lists it."""

        if self.item_status_for is None or self.rows.row_for_value(value) is None:
            return None
        return self.item_status_for(value)

    def has_same_items(self, items: list[Any]) -> bool:
        """True when ``items`` is the identical list this tab already shows."""

        if len(items) != len(self.view.parts.tab_selection.items):
            return False
        return all(new is old for new, old in zip(items, self.view.parts.tab_selection.items, strict=True))

    def decorate_row(self, tree_item: QTreeWidgetItem, value: Any, label: str) -> None:
        """Write a row's status chip, summary, tooltip and announced text.

        Shared by the full rebuild and by ``refresh_providers``, so a row's
        decoration can never be derived from one provider while its markers
        come from another.
        """

        list_status = self.item_status_for(value) if self.item_status_for is not None else None
        raw_status = list_status.status if list_status is not None else _status_label_for_item(value)
        status = display_status_label(raw_status) if raw_status else ""
        summary = list_status.summary if list_status is not None else _summary_for_item(value)
        tooltip = list_status.tooltip if list_status is not None else ""
        tooltip_text = tooltip or (f"{label}\n{summary}" if summary else label)
        if list_status_mark(status) == "glyph" and "status:" not in tooltip_text.lower():
            # The row shows a glyph instead of the word (plan C6); name it here.
            tooltip_text = f"{tooltip_text}\nStatus: {status}"
        tree_item.setToolTip(0, tooltip_text)
        tree_item.setData(0, LIST_PRIMARY_ROLE, label)
        tree_item.setData(0, LIST_SUMMARY_ROLE, summary)
        tree_item.setData(0, LIST_STATUS_ROLE, status)
        tree_item.setData(0, LIST_TOOLTIP_ROLE, tooltip_text)
        tree_item.setData(0, LIST_TAGS_ROLE, sorted(list_status.tags) if list_status is not None else [])
        # A screen reader otherwise announces the label alone, dropping the
        # status chip and summary that are the point of the row.
        announced = ", ".join(part for part in (label, status, summary) if part)
        tree_item.setData(0, Qt.ItemDataRole.AccessibleTextRole, announced)

    def refresh_row_decorations(self) -> None:
        """Re-read every row's status without rebuilding the list."""

        for tree_item in self.rows.object_rows():
            value = tree_item.data(0, VIEWER_OBJECT_ROLE)
            label = str(tree_item.data(0, LIST_PRIMARY_ROLE) or tree_item.text(0))
            self.view._decorate_row(tree_item, value, label)
        self.view.list.viewport().update()

    def refresh_providers(
        self,
        sources_for: Callable[[Any], PreviewSources],
        *,
        markers_for: Callable[[Any], list[ImageMarker]] | None = None,
        item_status_for: Callable[[Any], ItemListStatus] | None = None,
        prepared_sources: dict[str, PreviewSources] | None = None,
    ) -> None:
        """Re-point the callbacks without tearing the list down.

        ``set_items`` rebuilds the rows *and* installs the marker/status
        providers, so a scope-unchanged rebuild used to be the only way to get
        fresh markers. This is that work without the teardown: the provider
        closures are replaced, the current image's overlay is repainted and
        every row's decoration is re-read, while selection, filter, frame and
        scroll position are untouched.

        Refreshing the rows matters as much as the markers. This path runs
        whenever ``has_same_items()`` reports the list is unchanged, so a
        re-derived status — a newly failed tilt series, a link that has since
        resolved — would otherwise repaint the overlay while leaving the row
        chips stating the previous answer, with nothing on screen to signal
        that the two disagreed.
        """

        self.view.parts.mrc_loading.sources_for = sources_for
        self.view.parts.mrc_loading.prepared_sources = prepared_sources or {}
        if item_status_for is not None:
            self.item_status_for = item_status_for
        if markers_for is not None:
            self.view.parts.tab_selection.markers_for = markers_for
        # New providers come with a rebuilt workspace: drop the old markers
        # even when nothing is selected to rebuild them for.
        self.view.parts.tab_selection.forget_markers()
        self.view._refresh_marker_selection()
        self.view._refresh_row_decorations()
        # The filter matches on status and summary text, so re-apply it: the
        # visible set and the count must agree with the decorations just
        # written, not with the ones they replaced.
        self.view._filter_list()
        # Navigation buttons carry the resolver's verdict for the current row;
        # that verdict is derived from the same providers.
        self.view._update_navigation_actions(self.view.parts.tab_selection.current_value)

    def set_scope_description(
        self,
        *,
        has_sessions: bool,
        scope_name: str,
        entity_label: str = "",
    ) -> None:
        """Tell this tab what scope and entity type it is showing.

        ``entity_label`` matters when the list is *empty*: the type cannot be
        inferred from zero items, and "No items in Collection" is markedly less
        useful than "No search maps in Collection".
        """

        self.has_sessions = has_sessions
        self.scope_name = scope_name or ""
        self.entity_label = entity_label or self.entity_label

    def current_entity_label(self) -> str:
        if self.view.parts.tab_selection.items:
            return _panel_title_for_items(self.view.parts.tab_selection.items)
        return self.entity_label or _panel_title_for_items(self.view.parts.tab_selection.items)

    def empty_state(self) -> EmptyState:
        """The state-specific empty experience for this tab right now."""

        return viewer_empty_state(
            has_sessions=self.has_sessions,
            total_items=self.rows.counts()[1],
            filter_text=self.filter_bar.description(),
            scope_name=self.scope_name,
            entity_label=self.view._current_entity_label(),
        )

    def show_empty_state(self, state: EmptyState) -> None:
        """Show a specific recovery state over an empty canvas."""

        self.empty_state_override = state
        self.view._refresh_empty_state_panel()

    def clear_empty_state_override(self) -> None:
        self.empty_state_override = None
        self.view._refresh_empty_state_panel()

    def refresh_empty_state_panel(self) -> None:
        """Show the panel only when there is genuinely nothing to look at.

        Deliberately does **not** cover a loaded image. Filtering the list to
        nothing while a valid preview is on screen is not an empty state — the
        image is real content, and hiding it behind a panel would destroy more
        information than the panel adds. The list explains a retained image
        outside its filter and keeps its count and Clear all filters action.

        The panel is for when the canvas itself has nothing: no session loaded,
        a scope holding none of this entity, or a filter applied before any
        preview resolved.
        """

        panel = getattr(self.view, "empty_state_panel", None)
        if panel is None:
            return
        visible_rows, total = self.rows.counts()
        self.refresh_filter_feedback(visible_rows, total)
        if self.view.viewer.has_image():
            self.view._set_empty_state_panel_visible(panel, False)
            return
        # Precedence between a zero-result filter and an override depends on
        # what the override is *about*.
        #
        # A failed load is about the whole scope, so it outranks the filter:
        # otherwise an unrelated search box could hide the Retry button and the
        # reason for it. A missing preview is about one row, and when the
        # filter matches nothing that row is not even in the list — announcing
        # it would describe something the reviewer cannot see, so the honest
        # "0 of N" wins there.
        override = self.empty_state_override
        filtered_to_nothing = self.filter_bar.is_filtered() and not visible_rows
        if override is not None and override.kind == KIND_LOAD_FAILED:
            state = override
        elif filtered_to_nothing:
            state = self.view.empty_state()
        elif override is not None:
            state = override
        elif visible_rows:
            self.view._set_empty_state_panel_visible(panel, False)
            return
        else:
            state = self.view.empty_state()
        panel.set_state(state)
        self.view._set_empty_state_panel_visible(panel, True)

    def set_empty_state_panel_visible(self, panel: EmptyStatePanel, visible: bool) -> None:
        panel.setVisible(visible)
        # While the panel shows, it carries the message; the canvas text would
        # repeat it.
        self.view.viewer.set_placeholder_visible(not visible)
        # The zoom and export stack, Overlays, and the scale bar and legend act
        # on an image. Over an empty canvas they read "Fit" beside nothing and,
        # in a narrow viewer, sat on top of the panel's own text and buttons
        # (plan B6, C5). Keyed to this panel rather than to ``has_image()``: an
        # image still decoding is not an empty canvas, and hiding the controls
        # for every asynchronous load would make them flicker on each selection.
        controls = self.view.parts.canvas_controls
        for anchor in (controls.zoom_anchor, controls.overlay_anchor, controls.scale_bar_anchor):
            anchor.setVisible(not visible)

    def set_status(self, path: Path, slice_count: int, warnings: list[str], source: str) -> None:
        self.view._set_status_text(
            self.view._compact_path_status(source, path, slice_count=slice_count, warnings=warnings),
            tooltip=self.view._path_status_tooltip(source, path, slice_count=slice_count, warnings=warnings),
            tone="warn" if warnings else "",
        )

    def set_status_text(self, text: str, *, tooltip: str | None = None, tone: str = "") -> None:
        """The line under the canvas, for exceptions only (design review F7).

        It is the only place a JPEG fallback, an unreadable MRC or the image's
        parse warnings are said, so those take a tone (harmonisation A3). A
        plain MRC or image has nothing to flag: its name is the status bar's
        and the badge's, so empty text hides the line altogether.
        """

        self.view.status.setText(text)
        self.view.status.setToolTip(tooltip or text)
        self.view.status.setVisible(bool(text))
        if (self.view.status.property("tone") or "") != tone:
            self.view.status.setProperty("tone", tone)
            style = self.view.status.style()
            style.unpolish(self.view.status)
            style.polish(self.view.status)

    def compact_path_status(
        self,
        source: str,
        path: Path,
        *,
        slice_count: int = 1,
        warnings: list[str] | None = None,
    ) -> str:
        # The frame is in the readout beside the scrubber; the tooltip keeps it.
        warning_text = warning_count_text(warnings or [])
        if not warning_text and source in PLAIN_PROVENANCE:
            return ""
        return f"{source}: {path.name}{warning_text}"

    def path_status_tooltip(
        self,
        source: str,
        path: Path,
        *,
        slice_count: int = 1,
        warnings: list[str] | None = None,
    ) -> str:
        frame = self.view._frame_text(slice_count)
        frame_line = f"\n{frame}" if frame else ""
        return f"{source}: {path}{frame_line}{warning_tooltip(warnings or [])}"

    def fit_list_minimum(self) -> None:
        """Lower the list's minimum only as far as the viewer needs (plan F3.5).

        Depends on the splitter's width alone, which the tab's layout sets, so
        changing the minimum cannot feed back into the width it was fitted to.
        """

        splitter = getattr(self, "list_splitter", None)  # None while being built
        if splitter is None or splitter.count() < 2:
            return
        viewer = splitter.widget(0)
        viewer_minimum = max(viewer.minimumWidth(), viewer.minimumSizeHint().width())
        room = splitter.width() - splitter.handleWidth() - viewer_minimum
        minimum = max(VIEWER_LIST_FLOOR_PX, min(VIEWER_LIST_MIN_WIDTH_PX, room))
        if self.view.list.minimumWidth() != minimum:
            self.view.list.setMinimumWidth(minimum)

