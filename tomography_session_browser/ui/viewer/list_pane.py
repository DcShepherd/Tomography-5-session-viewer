"""List pane for the image viewer."""
from __future__ import annotations
from collections.abc import Callable
from pathlib import Path
from typing import Any
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QHeaderView, QSplitter, QTreeWidgetItem, QVBoxLayout, QWidget
from tomography_session_browser.services.preview_sources import PreviewSources
from tomography_session_browser.domain.markers import ImageMarker
from tomography_session_browser.presentation.viewer_items import panel_title_for_items as _panel_title_for_items, summary_for_item as _summary_for_item, status_label_for_item as _status_label_for_item
from tomography_session_browser.presentation.empty_states import KIND_LOAD_FAILED, EmptyState, viewer_empty_state
from tomography_session_browser.ui.widgets.empty_state_panel import EmptyStatePanel
from tomography_session_browser.ui.motion import ItemHoverAnimator
from tomography_session_browser.services.item_status import ItemListStatus, display_status_label
from tomography_session_browser.ui.widgets.smooth_tree import SmoothScrollTree
from tomography_session_browser.ui.viewer.constants import FILTER_TAG_PREFIX, LIST_PRIMARY_ROLE, LIST_STATUS_ROLE, LIST_SUMMARY_ROLE, LIST_TAGS_ROLE, LIST_TOOLTIP_ROLE, VIEWER_CANVAS_MIN_WIDTH_PX, VIEWER_LIST_FLOOR_PX, VIEWER_LIST_MIN_WIDTH_PX, VIEWER_OBJECT_ROLE
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
        splitter.setChildrenCollapsible(True)
        splitter.addWidget(viewer_panel)
        self.list_splitter = splitter if show_list else None
        if show_list:
            list_panel = QWidget(splitter)
            list_panel.setObjectName("viewerListPanel")
            list_layout = QVBoxLayout(list_panel)
            list_layout.setContentsMargins(0, 0, 0, 0)
            list_layout.setSpacing(0)
            list_top = QWidget(list_panel)
            list_top_layout = QHBoxLayout(list_top)
            list_top_layout.setContentsMargins(12, 10, 12, 8)
            list_top_layout.addWidget(self.view.list_header)
            list_top_layout.addStretch(1)
            list_top_layout.addWidget(self.view.list_count)
            list_layout.addWidget(list_top)
            filter_wrap = QWidget(list_panel)
            filter_layout = QVBoxLayout(filter_wrap)
            filter_layout.setContentsMargins(10, 0, 10, 8)
            filter_layout.addWidget(self.view.list_filter)
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

    def create_widgets(self, show_list: bool) -> None:
        self.view.list = SmoothScrollTree(self.view)
        self.view.list.setObjectName("viewerList")
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

        self.view.list_filter = QLineEdit(self.view)
        self.view.list_filter.setObjectName("viewerFilter")
        self.view.list_filter.setPlaceholderText("Filter items...")
        self.view.list_filter.setToolTip("Filter this list by name, status, or summary")
        self.view.list_filter.setAccessibleName("Filter viewer items")
        self.view.list_filter.setClearButtonEnabled(True)
        self.clear_filter_shortcut = QShortcut(
            QKeySequence(Qt.Key.Key_Escape),
            self.view.list_filter,
        )
        self.clear_filter_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self.clear_filter_shortcut.activated.connect(self.view.list_filter.clear)
        self.view.list_filter.setVisible(show_list)
        self.view.list_filter.textChanged.connect(self.view._filter_list)
        self.view.list_header = QLabel("Items", self.view)
        self.view.list_header.setObjectName("viewerPanelHeader")
        self.view.list_header.setVisible(show_list)
        self.view.list_count = QLabel("0", self.view)
        self.view.list_count.setObjectName("panelCount")
        self.view.list_count.setVisible(show_list)

    def filter_list(self, text: str) -> None:
        query = text.strip().lower()
        # ``is:<tag>`` tokens match a row's status tags exactly (the
        # dashboard's drill-downs use them); the rest is free text.
        words = query.split()
        tags = {word[len(FILTER_TAG_PREFIX):] for word in words if word.startswith(FILTER_TAG_PREFIX)}
        free_text = " ".join(word for word in words if not word.startswith(FILTER_TAG_PREFIX))
        visible_count = 0
        for index in range(self.view.list.topLevelItemCount()):
            item = self.view.list.topLevelItem(index)
            status = str(item.data(0, LIST_STATUS_ROLE) or "")
            summary = str(item.data(0, LIST_SUMMARY_ROLE) or "")
            row_tags = item.data(0, LIST_TAGS_ROLE) or ()
            matches = tags.issubset(row_tags) and (
                not free_text
                or free_text in item.text(0).lower()
                or free_text in status.lower()
                or free_text in summary.lower()
                or free_text in item.toolTip(0).lower()
            )
            item.setHidden(not matches)
            if matches:
                visible_count += 1
        total = self.view.list.topLevelItemCount()
        # Honest count under a filter: "0 of 20" rather than a bare "0", which
        # reads the same as a scope that genuinely holds nothing.
        if query:
            self.view.list_count.setText(f"{visible_count} of {total}")
            announced = f"{visible_count} of {total} shown, filtered by {query}"
        else:
            self.view.list_count.setText(str(total))
            announced = f"{total} shown"
        # The bare number is meaningless out of context to a screen reader.
        self.view.list_count.setAccessibleName("Visible item count")
        self.view.list_count.setAccessibleDescription(announced)
        self.view._refresh_empty_state_panel()

    def item_status_for_value(self, value: Any) -> ItemListStatus | None:
        """The shared row status this tab shows for ``value``, if it lists it."""

        if self.item_status_for is None or not any(item is value for item in self.view.parts.tab_selection.items):
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

        for index in range(self.view.list.topLevelItemCount()):
            tree_item = self.view.list.topLevelItem(index)
            value = tree_item.data(0, VIEWER_OBJECT_ROLE)
            if value is None:
                continue
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
        self.view._filter_list(self.view.list_filter.text())
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
        inferred from zero items, and "No items in Sample A" is markedly less
        useful than "No search maps in Sample A".
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
            total_items=self.view.list.topLevelItemCount(),
            filter_text=self.view.list_filter.text().strip(),
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
        information than the panel adds. That case is already carried by the
        honest ``0 of 20`` count and by the context header's filter note and
        Clear action.

        The panel is for when the canvas itself has nothing: no session loaded,
        a scope holding none of this entity, or a filter applied before any
        preview resolved.
        """

        panel = getattr(self.view, "empty_state_panel", None)
        if panel is None:
            return
        visible_rows = sum(
            1
            for index in range(self.view.list.topLevelItemCount())
            if not self.view.list.topLevelItem(index).isHidden()
        )
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
        filtered_to_nothing = bool(self.view.list_filter.text().strip()) and not visible_rows
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
        """The provenance line under the canvas: what is shown and from where.

        It is the only place a JPEG fallback, an unreadable MRC or the image's
        parse warnings are said, so those take a tone (harmonisation A3)
        rather than reading like the plain "MRC: name.mrc" beside them.
        """

        self.view.status.setText(text)
        self.view.status.setToolTip(tooltip or text)
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
        return f"{source}: {path.name}{warning_text}"

    def path_status_tooltip(
        self,
        source: str,
        path: Path,
        *,
        slice_count: int = 1,
        warnings: list[str] | None = None,
    ) -> str:
        return f"{source}: {path}{self.view._frame_text(slice_count)}{warning_tooltip(warnings or [])}"

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

