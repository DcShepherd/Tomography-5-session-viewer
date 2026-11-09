"""The checkbox dialog that chooses what a PDF report covers.

What a selection means lives in ``presentation/report_scope.py``; this module
only draws the tree and collects the request.
"""

from __future__ import annotations

from tomography_session_browser.ui.widgets.link_button import link_button
from typing import Any, Iterable, Sequence

from PySide6.QtCore import QEvent, QRect, QSize, Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QPushButton, QHeaderView,
                               QScrollArea, QSizePolicy, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.domain.models import Sample, Session
from tomography_session_browser.ui.icons import themed_icon
from tomography_session_browser.ui.navigation_icons import PROJECT_GROUP_ICONS, USER_GROUP_ICON
from tomography_session_browser.presentation.project_folders import UserGroup, UserHeading, arrange
from tomography_session_browser.presentation.project_tree import (
    ProjectTreeGroup,
    project_group_badge,
    project_group_badge_tooltip,
    session_badge_tooltip,
    session_key,
)
from tomography_session_browser.presentation.report_scope import (
    ReportScope,
    ReportScopeRequest,
    describe_report_scope,
    normalise_report_scope,
    reportable_samples,
    sample_key,
)
from tomography_session_browser.presentation.report_tomograms import ReportSource, ReportTomograms
from tomography_session_browser.ui.report_tomogram_options import TomogramOptions
from tomography_session_browser.presentation.scopes import LinkedSampleGroup
from tomography_session_browser.presentation.design_tokens import SPACE_L, SPACE_M, SPACE_S
from tomography_session_browser.ui.widgets.elided_label import WrappedElidedLabel
from tomography_session_browser.ui.widgets.rounded_viewport import round_viewport


REPORT_GROUP_ROLE = int(Qt.ItemDataRole.UserRole)
REPORT_SESSION_ROLE = int(Qt.ItemDataRole.UserRole) + 1
REPORT_SAMPLE_ROLE = int(Qt.ItemDataRole.UserRole) + 2
REPORT_FOLDER_ROLE = int(Qt.ItemDataRole.UserRole) + 3
#: A user group heading (B4): a three-state parent of its acquisition members, never a scope itself.
REPORT_USER_GROUP_ROLE = int(Qt.ItemDataRole.UserRole) + 4

SESSION_DETAIL = ("Ticks choose report content. Group checkboxes and Select all include full atlas overviews. "
                  "The current linked-scope preset selects collection datasets only.")
FOLDER_DETAIL = "For a session report, tick sessions or samples. For a folder report, tick only folders."
FOLDER_NOTE = "The report reads only the ticked folders. Ticks do not change links."
#: The tree keeps this height on a short screen; the options area scrolls instead (GR-03).
TREE_MIN_HEIGHT = 140
OPTIONS_MIN_HEIGHT = 72


class _OptionsScroll(QScrollArea):
    """A scroll area as tall as its content at the current width, never taller.

    Qt's own size hint does not follow word-wrapped content, so spare height went
    to the options instead of the tree. A content layout change asks again.
    """

    def setWidget(self, widget: QWidget) -> None:  # noqa: N802 - Qt API
        super().setWidget(widget)
        widget.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        if watched is self.widget() and event.type() == QEvent.Type.LayoutRequest:
            self.updateGeometry()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        if event.oldSize().width() != event.size().width():
            self.updateGeometry()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        hint, content = super().sizeHint(), self.widget()
        if content is None:
            return hint
        width = self.viewport().width() if self.viewport().width() > 0 else content.sizeHint().width()
        height = content.heightForWidth(width) if content.hasHeightForWidth() else content.sizeHint().height()
        return QSize(hint.width(), max(height, 0) + 2 * self.frameWidth())


def _repolish(widget: QWidget) -> None:
    """Re-run the style engine so a late ``setObjectName`` takes effect."""

    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)
    widget.update()


def _selection_summary_text(request: ReportScopeRequest) -> str:
    """Describe what was *clicked* — the fallback when no scope is resolvable."""

    if not request.has_selection:
        return "Nothing selected"
    parts = [
        count_phrase(len(request.group_keys), "project group"),
        count_phrase(len(request.session_keys), "session"),
        count_phrase(len(request.sample_keys), "sample"),
        count_phrase(len(request.processing_source_ids), "processing folder"),
    ]
    chosen = [part for part in parts if not part.startswith("0 ")]
    return "Selected: " + ", ".join(chosen)


def _describe_current_scope(value: Any | None) -> str:
    """Name the scope under review, for the dialog's primary action."""

    if value is None:
        return ""
    for attribute in ("display_name", "name", "label"):
        candidate = getattr(value, attribute, None)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


class ReportScopeDialog(QDialog):
    """Checkbox tree for choosing the sessions included in a PDF report."""

    def __init__(
        self,
        groups: Iterable[ProjectTreeGroup],
        *,
        current_scope: Any | None = None,
        sessions: Iterable[Session] | None = None,
        include_exposure_thumbnails: bool = False,
        tomograms: ReportTomograms | None = None,
        tomogram_issue: str | None = None,
        include_tomograms: bool = False,
        include_slices: bool = False,
        folder_preset: frozenset[str] = frozenset(),
        user_groups: Sequence[UserGroup] = (),
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._groups = list(groups)
        self._user_groups = tuple(user_groups)
        # Frozen when the dialog opened; every tick reduces this same snapshot.
        self._tomograms = tomograms
        self._tomogram_issue = tomogram_issue
        self._folder_preset = folder_preset
        self._scope: ReportScope | None = None
        self._folder_items: dict[str, QTreeWidgetItem] = {}
        # Needed to preview the *normalised* scope rather than the raw
        # selection. Falls back to the groups' own sessions so an existing
        # caller that does not pass them still gets a usable preview.
        self._sessions = (
            list(sessions)
            if sessions is not None
            else [
                session
                for group in self._groups
                for session in group.sessions
            ]
        )
        self._item_updates_blocked = False
        self._follows_screen = False
        self._group_items: dict[str, QTreeWidgetItem] = {}
        self._session_items: dict[str, QTreeWidgetItem] = {}
        self._sample_items: dict[str, QTreeWidgetItem] = {}

        self.setWindowTitle("Select report scope")
        self.setModal(True)
        available = self.screen().availableGeometry()
        self.resize(min(720, available.width() - 48), min(800, available.height() - 80))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_L, SPACE_L, SPACE_L, SPACE_L)
        layout.setSpacing(SPACE_M)

        heading = QLabel("Choose sessions and samples for the report", self)
        heading.setObjectName("cardTitle")
        layout.addWidget(heading)

        self.detail = QLabel(SESSION_DETAIL, self)
        self.detail.setWordWrap(True)
        self.detail.setObjectName("mutedLabel")
        layout.addWidget(self.detail)

        self.tree = QTreeWidget(self)
        self.tree.setObjectName("projectTree")
        round_viewport(self.tree)
        self.tree.setAccessibleName("Report scope")
        self.tree.setAccessibleDescription(
            "Choose linked groups, sessions, or samples to include in the report."
        )
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Scope", "Type"])
        self.tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.setMinimumHeight(TREE_MIN_HEIGHT)
        layout.addWidget(self.tree, stretch=1)
        self.folder_note = QLabel(FOLDER_NOTE, self)
        self.folder_note.setObjectName("mutedLabel")
        self.folder_note.setWordWrap(True)
        self.folder_note.setVisible(bool(tomograms is not None and tomograms.sources))
        layout.addWidget(self.folder_note)

        controls = QVBoxLayout()
        controls.setSpacing(SPACE_S)
        current_controls = QHBoxLayout()
        content_controls = QHBoxLayout()
        content_controls.setSpacing(SPACE_S)
        # "view" described the widget; this is about the scientific scope being
        # reviewed. The name sits beside the button, bounded to two lines, so a
        # long name cannot set the dialog's width (GR-03).
        scope_name = _describe_current_scope(current_scope)
        linked_preset = isinstance(current_scope, ProjectTreeGroup) and current_scope.kind == "linked"
        current_label = "Select current collections" if linked_preset else "Select current review scope"
        self.select_current_button = QPushButton(current_label, self)
        self.select_current_button.setEnabled(current_scope is not None)
        current_tip = (f"Select {scope_name}, the scope currently under review"
                       if scope_name else "No current reportable scope is available")
        self.select_current_button.setToolTip(current_tip)
        self.select_current_button.setAccessibleDescription(current_tip)
        self.current_scope_name = WrappedElidedLabel(scope_name, max_lines=2, parent=self)
        self.current_scope_name.setObjectName("mutedLabel")
        self.current_scope_name.setToolTip(scope_name)
        self.current_scope_name.setAccessibleName("Current review scope")
        self.current_scope_name.setVisible(bool(scope_name))
        self.select_all_button = QPushButton("Select all", self)
        self.clear_button = QPushButton("Clear selection", self)
        self.expand_button = QPushButton("Expand all", self)
        self.collapse_button = QPushButton("Collapse all", self)
        for button in (
            self.select_current_button,
            self.select_all_button,
            self.clear_button,
            self.expand_button,
            self.collapse_button,
        ):
            button.setObjectName("dashboardFilterButton")
        current_controls.addWidget(self.select_current_button)
        current_controls.addWidget(self.current_scope_name, 1, Qt.AlignmentFlag.AlignVCenter)
        for button in (self.select_all_button, self.clear_button, self.expand_button, self.collapse_button):
            content_controls.addWidget(button)
        content_controls.addStretch(1)
        controls.addLayout(current_controls)
        controls.addLayout(content_controls)
        layout.addLayout(controls)

        # The options scroll only when the screen is too short for them; the tree
        # keeps a usable height and the footer stays visible (GR-03). The widgets
        # are made inside the area so the Tab order stays tree, options, footer.
        self.options_scroll = _OptionsScroll(self)
        self.options_scroll.setObjectName("reportOptionsScroll")
        self.options_scroll.setWidgetResizable(True)
        self.options_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.options_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.options_scroll.setMinimumHeight(OPTIONS_MIN_HEIGHT)
        # Never taller than its content, so spare height goes to the tree.
        self.options_scroll.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        options = QWidget(self.options_scroll)
        options.setObjectName("reportOptions")
        options_layout = QVBoxLayout(options)
        options_layout.setContentsMargins(0, 0, 0, 0)
        options_layout.setSpacing(SPACE_M)

        self.include_atlas_overview = QCheckBox("Also include supporting atlas-session overviews", options)
        self.include_atlas_overview.setToolTip("Include the full supporting screening session, which may contain unrelated atlases.")
        atlas_help = QLabel("Full atlas-session overviews can include atlases unrelated to the selected datasets.", options)
        atlas_help.setObjectName("mutedLabel")
        atlas_help.setWordWrap(True)
        options_layout.addWidget(self.include_atlas_overview)
        options_layout.addWidget(atlas_help)
        self.include_atlas_overview.toggled.connect(self._update_generate_enabled)

        self.include_exposure_thumbnails = QCheckBox("Include exposure thumbnails for each tilt series", options)
        self.include_exposure_thumbnails.setChecked(include_exposure_thumbnails)
        exposure_help_text = (
            "Adds one image per tilt series from its Thumbnails stack after its tilt-series table, four to a page: "
            "the image nearest 0°, or nearest the pretilt for a lamella, with its time, camera dose and stage position."
        )
        self.include_exposure_thumbnails.setToolTip(exposure_help_text)
        self.include_exposure_thumbnails.setAccessibleDescription(exposure_help_text)
        exposure_help = QLabel(exposure_help_text, options)
        exposure_help.setObjectName("mutedLabel")
        exposure_help.setWordWrap(True)
        exposure_help.setVisible(False)
        self.thumbnail_details = link_button(parent=options)
        self.thumbnail_details.setText("Thumbnail details")
        self.thumbnail_details.setCheckable(True)
        self.thumbnail_details.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.thumbnail_details.setArrowType(Qt.ArrowType.RightArrow)
        self.thumbnail_details.setAccessibleDescription(exposure_help_text)
        def show_thumbnail_details(checked: bool) -> None:
            exposure_help.setVisible(checked)
            self.thumbnail_details.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)
            if checked:
                QTimer.singleShot(0, lambda: self.options_scroll.ensureWidgetVisible(exposure_help))
        self.thumbnail_details.toggled.connect(show_thumbnail_details)
        options_layout.addWidget(self.include_exposure_thumbnails)
        options_layout.addWidget(self.thumbnail_details)
        options_layout.addWidget(exposure_help)
        self.include_exposure_thumbnails.toggled.connect(self._update_generate_enabled)

        self.tomogram_options = TomogramOptions(include_tomograms=include_tomograms,
                                                include_slices=include_slices, parent=options)
        self.tomogram_options.setVisible(tomograms is not None or tomogram_issue is not None)
        self.tomogram_options.changed.connect(self._update_generate_enabled)
        options_layout.addWidget(self.tomogram_options)
        self.options_scroll.setWidget(options)
        layout.addWidget(self.options_scroll)

        self.selection_summary = QLabel("Nothing selected", self)
        self.selection_summary.setObjectName("mutedLabel")
        self.selection_summary.setWordWrap(True)
        layout.addWidget(self.selection_summary)

        self.buttons = QDialogButtonBox(self)
        self.generate_button = self.buttons.addButton("Generate report", QDialogButtonBox.ButtonRole.AcceptRole)
        self.cancel_button = self.buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        # The confirming action sat at the same visual weight as Cancel and
        # the five secondary selection buttons above it. Qt has already
        # polished the button by the time ``addButton`` returns, so the new
        # object name needs an explicit re-polish to pick up the #primaryButton
        # rules.
        self.generate_button.setObjectName("primaryButton")
        _repolish(self.generate_button)
        self.generate_button.setDefault(True)
        self.generate_button.setAutoDefault(True)
        self.generate_button.setEnabled(False)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.select_current_button.clicked.connect(lambda: self.select_scope(current_scope))
        self.select_all_button.clicked.connect(self.select_all)
        self.clear_button.clicked.connect(self.clear_selection)
        self.expand_button.clicked.connect(self.tree.expandAll)
        self.collapse_button.clicked.connect(self.tree.collapseAll)

        self._populate()
        if current_scope is not None and not self._items_for_scope(current_scope):
            # Nothing in the tree stands for it, so the button would do
            # nothing. Say so in its label, not only on hover.
            self.select_current_button.setEnabled(False)
            self.select_current_button.setText("Current review scope is not reportable")
            reason = f"{scope_name or 'The current scope'} has no entry in this report scope list."
            self.select_current_button.setToolTip(reason)
            self.select_current_button.setAccessibleDescription(reason)
        if current_scope is not None or not self._session_items:
            self.select_scope(current_scope)
        self._update_generate_enabled()

    def scope_preview_text(self, request: ReportScopeRequest | None = None) -> str:
        """What the report will contain, from the same function that decides it.

        Routing the preview through ``normalise_report_scope`` is the point: a
        summary derived from the checkboxes could describe a scope the
        generator never produces, and would miss the supporting Atlas sessions
        normalisation adds.
        """

        request = request if request is not None else self.selected_request()
        return self._preview(request)[1]

    def _preview(self, request: ReportScopeRequest) -> tuple[ReportScope | None, str]:
        """The normalised scope and its description; no scope when the selection has none.

        Only the refusal of an unreportable selection is caught: any other
        failure is a fault that must not hide behind an enabled Generate.
        """

        if not request.has_selection:
            return None, "Nothing selected"
        try:
            scope = normalise_report_scope(self._sessions, self._groups, request, self._tomograms)
        except ValueError as exc:
            return None, f"{_selection_summary_text(request)}. {exc}"
        return scope, describe_report_scope(scope)

    def accepted_scope(self) -> ReportScope | None:
        """The exact scope the last preview described; the flow dispatches it unchanged."""

        return self._scope

    def selected_request(self) -> ReportScopeRequest:
        group_keys: set[str] = set()
        session_keys: set[str] = set()
        sample_keys: set[str] = set()
        include_tomograms, include_slices = self.tomogram_options.preferences()
        tops = [self.tree.topLevelItem(index) for index in range(self.tree.topLevelItemCount())]
        # A user group heading is only a parent: its members count as top-level groups.
        rows = [child for item in tops for child in
                ([item.child(index) for index in range(item.childCount())]
                 if item.data(0, REPORT_USER_GROUP_ROLE) is not None else [item])]
        for item in rows:
            group = item.data(0, REPORT_GROUP_ROLE)
            if not isinstance(group, ProjectTreeGroup):
                continue
            if group.kind == "linked":
                selected_sessions: set[str] = set()
                self._collect_descendant_selection(item, selected_sessions, sample_keys)
                collection_keys = {session_key(session) for session in group.collection_sessions}
                # Lossless content compression preserves the group label. AT
                # remains explicit regardless of the aggregate parent's state.
                if collection_keys and collection_keys <= selected_sessions:
                    group_keys.add(group.key)
                    selected_sessions.difference_update(collection_keys)
                session_keys.update(selected_sessions)
                continue
            if item.checkState(0) == Qt.CheckState.Checked:
                group_keys.add(group.key)
                continue
            self._collect_descendant_selection(item, session_keys, sample_keys)
        return ReportScopeRequest(
            frozenset(group_keys), frozenset(session_keys), frozenset(sample_keys),
            include_atlas_overview=self.include_atlas_overview.isChecked(),
            include_exposure_thumbnails=self.include_exposure_thumbnails.isChecked(),
            include_tomograms=include_tomograms,
            include_slices=include_slices,
            processing_source_ids=frozenset(
                source_id for source_id, item in self._folder_items.items()
                if item.checkState(0) == Qt.CheckState.Checked
            ),
        )

    def select_scope(self, value: Any | None) -> None:
        """Tick the rows that stand for ``value``.

        When no row does, the reviewer's existing ticks are left alone: the
        button used to clear them first and then find nothing to select.
        """

        items = self._items_for_scope(value)
        if not items and not self._session_items:
            # Folders alone are loaded: the preset folders give a folder report (D4 §5.6).
            items = [self._folder_items[source_id] for source_id in sorted(self._folder_preset)
                     if source_id in self._folder_items]
            value = None
        if not items:
            return
        self.clear_selection()
        selected_items = items
        if isinstance(value, ProjectTreeGroup) and value.kind == "linked":
            selected_items = [self._session_items[session_key(session)] for session in value.collection_sessions]
        # A processing-folder scope ticks its folder alone, which is the
        # preset; every acquisition scope also ticks the preset folders.
        if value is not None and not _is_processing_group(value):
            selected_items = selected_items + [
                self._folder_items[source_id] for source_id in sorted(self._folder_preset) if source_id in self._folder_items
            ]
        for item in selected_items:
            self._set_checked(item, True)
            ancestor = item
            while ancestor is not None:
                ancestor.setExpanded(True)
                ancestor = ancestor.parent()
        self.tree.setCurrentItem(selected_items[0])
        self.tree.scrollToItem(selected_items[0], QTreeWidget.ScrollHint.PositionAtTop)
        self._update_generate_enabled()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        handle = self.windowHandle()
        if handle is not None and not self._follows_screen:
            handle.screenChanged.connect(lambda _screen: self._fit_to_screen())
            self._follows_screen = True
        self._fit_to_screen()
        if self.tree.currentItem() is not None:
            self.tree.scrollToItem(self.tree.currentItem(), QTreeWidget.ScrollHint.PositionAtTop)

    def _available_geometry(self) -> QRect:
        return self.screen().availableGeometry()

    def _fit_to_screen(self) -> None:
        """Keep the whole frame, title bar included, on the available screen (GR-03).

        The tree is the layout's stretch item, so it gives up height first.
        """
        available = self._available_geometry()
        frame, inner = self.frameGeometry(), self.geometry()
        border_w, border_h = frame.width() - inner.width(), frame.height() - inner.height()
        size = (min(self.width(), available.width() - border_w), min(self.height(), available.height() - border_h))
        if size != (self.width(), self.height()):
            self.resize(*size)
        frame = self.frameGeometry()
        x = min(max(frame.x(), available.left()), available.left() + available.width() - frame.width())
        y = min(max(frame.y(), available.top()), available.top() + available.height() - frame.height())
        if (x, y) != (frame.x(), frame.y()):
            self.move(x, y)

    def _items_for_scope(self, value: Any | None) -> list[QTreeWidgetItem]:
        """The rows that stand for ``value`` in this dialog; empty when none does."""

        if _is_processing_group(value):
            assert isinstance(value, ProjectTreeGroup) and value.processing_source is not None
            item = self._folder_items.get(value.processing_source.id)
            return [item] if item is not None else []
        if isinstance(value, ProjectTreeGroup):
            item = self._group_items.get(value.key)
            return [item] if item is not None else []
        if isinstance(value, Session):
            item = self._session_items.get(session_key(value))
            return [item] if item is not None else []
        if isinstance(value, LinkedSampleGroup):
            samples = list(value.samples)
        elif isinstance(value, Sample):
            samples = [value]
        else:
            return []
        rows = [
            item
            for item in self._sample_items.values()
            if any(item.data(0, REPORT_SAMPLE_ROLE) is sample for sample in samples)
        ]
        if rows:
            return rows
        # An atlas grid (or a sample without collection data) has no row of
        # its own; the session that holds it is the nearest reportable scope.
        holders = [
            session
            for session in self._sessions
            if any(candidate is sample for sample in samples for candidate in session.samples)
        ]
        return [
            self._session_items[session_key(session)]
            for session in holders
            if session_key(session) in self._session_items
        ]

    def select_all(self) -> None:
        self._item_updates_blocked = True
        try:
            for item in self._iter_items():
                self._set_checked(item, True)
        finally:
            self._item_updates_blocked = False
        self._update_generate_enabled()

    def clear_selection(self) -> None:
        self._item_updates_blocked = True
        try:
            for item in self._iter_items():
                self._set_checked(item, False)
        finally:
            self._item_updates_blocked = False
        self._update_generate_enabled()

    def _populate(self) -> None:
        sources = self._tomograms.sources if self._tomograms is not None else ()
        if not self._groups and not sources:
            self.detail.setText("No sessions loaded. Load a session before generating a report.")
            self.tree.setEnabled(False)
            return
        self.tree.clear()
        for entry in arrange(self._groups, self._user_groups):
            if isinstance(entry, UserHeading):
                heading = self._user_group_item(entry)
                self.tree.addTopLevelItem(heading)
                for group in entry.members:
                    heading.addChild(self._filled_group_item(group))
                heading.setExpanded(True)
            else:
                self.tree.addTopLevelItem(self._filled_group_item(entry))
        if sources:
            self.tree.addTopLevelItem(self._folders_item(sources))

    def _user_group_item(self, heading: UserHeading) -> QTreeWidgetItem:
        item = QTreeWidgetItem([heading.group.name, ""])
        item.setToolTip(0, f"{heading.group.name}\nYour group: {count_phrase(len(heading.members), 'row')}")
        item.setData(0, REPORT_USER_GROUP_ROLE, heading.group.key)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
        item.setCheckState(0, Qt.CheckState.Unchecked)
        item.setIcon(0, themed_icon(USER_GROUP_ICON))
        return item

    def _filled_group_item(self, group: ProjectTreeGroup) -> QTreeWidgetItem:
        """A group's row with its sessions or samples, as a top-level row or under a user group."""

        item = self._group_item(group)
        self._group_items[group.key] = item
        if group.kind == "linked":
            if group.atlas_session is not None:
                item.addChild(self._session_item(group.atlas_session, "AT"))
            for session in group.collection_sessions:
                item.addChild(self._session_item(session, "DC"))
            item.setExpanded(True)
        elif len(group.sessions) == 1:
            session = group.sessions[0]
            self._session_items[session_key(session)] = item
            for sample in reportable_samples(session):
                item.addChild(self._sample_item(session, sample))
            if item.childCount():
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsAutoTristate)
        return item

    def _folders_item(self, sources: Iterable[ReportSource]) -> QTreeWidgetItem:
        """The processing folders, after the acquisition groups; ticks filter rows only."""

        heading = QTreeWidgetItem(["Processing folders", ""])
        heading.setToolTip(0, FOLDER_NOTE)
        heading.setFlags(heading.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
        heading.setCheckState(0, Qt.CheckState.Unchecked)
        heading.setIcon(0, themed_icon("folder-open"))
        for source in sources:
            count = count_phrase(len(source.source.tomograms), "tomogram")
            item = QTreeWidgetItem([source.label, f"{source.source.pipeline} · {count}"])
            item.setToolTip(0, f"{source.label}\n{source.source.pipeline} · {count}")
            item.setData(0, REPORT_FOLDER_ROLE, source.source.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Unchecked)
            item.setIcon(0, themed_icon("folder-open"))
            heading.addChild(item)
            self._folder_items[source.source.id] = item
        heading.setExpanded(True)
        return heading

    def _group_item(self, group: ProjectTreeGroup) -> QTreeWidgetItem:
        item = QTreeWidgetItem([group.display_name, project_group_badge(group)])
        item.setToolTip(0, _group_tooltip(group))
        item.setToolTip(1, project_group_badge_tooltip(group))
        item.setData(0, REPORT_GROUP_ROLE, group)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        if group.kind == "linked":
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsAutoTristate)
        item.setCheckState(0, Qt.CheckState.Unchecked)
        item.setIcon(
            0,
            themed_icon(PROJECT_GROUP_ICONS.get(group.kind, "folder-open")),
        )
        return item

    def _session_item(self, session: Session, badge: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([session.name or session.path.name, badge])
        item.setToolTip(0, str(session.path))
        item.setToolTip(1, session_badge_tooltip(badge))
        item.setData(0, REPORT_SESSION_ROLE, session)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        samples = reportable_samples(session)
        if samples:
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsAutoTristate)
        item.setCheckState(0, Qt.CheckState.Unchecked)
        item.setIcon(0, themed_icon("folder-open"))
        self._session_items[session_key(session)] = item
        for sample in samples:
            item.addChild(self._sample_item(session, sample))
        return item

    def _sample_item(self, session: Session, sample: Sample) -> QTreeWidgetItem:
        key = sample_key(session, sample)
        item = QTreeWidgetItem([sample.name or sample.path.name, "Sample"])
        item.setToolTip(0, str(sample.path))
        item.setToolTip(1, "Data collection sample")
        item.setData(0, REPORT_SAMPLE_ROLE, sample)
        item.setData(0, REPORT_SESSION_ROLE, session)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(0, Qt.CheckState.Unchecked)
        item.setIcon(0, themed_icon("folder-open"))
        self._sample_items[key] = item
        return item

    def _collect_descendant_selection(
        self,
        item: QTreeWidgetItem,
        session_keys: set[str],
        sample_keys: set[str],
    ) -> None:
        for child_index in range(item.childCount()):
            child = item.child(child_index)
            session = child.data(0, REPORT_SESSION_ROLE)
            sample = child.data(0, REPORT_SAMPLE_ROLE)
            if isinstance(session, Session) and isinstance(sample, Sample):
                if child.checkState(0) == Qt.CheckState.Checked:
                    sample_keys.add(sample_key(session, sample))
                continue
            if isinstance(session, Session) and child.checkState(0) == Qt.CheckState.Checked:
                session_keys.add(session_key(session))
                continue
            self._collect_descendant_selection(child, session_keys, sample_keys)

    def _on_item_changed(self, _item: QTreeWidgetItem, _column: int) -> None:
        if self._item_updates_blocked:
            return
        self._item_updates_blocked = True
        try:
            state = _item.checkState(0)
            if state in (Qt.CheckState.Checked, Qt.CheckState.Unchecked):
                self._set_descendants_checked(_item, state)
            self._refresh_ancestor_check_states(_item.parent())
        finally:
            self._item_updates_blocked = False
        self._update_generate_enabled()

    def _update_generate_enabled(self) -> None:
        request = self.selected_request()
        self._scope, text = self._preview(request)
        self.generate_button.setEnabled(self._scope is not None)
        self.selection_summary.setText(text)
        folder_mode = self._scope is not None and self._scope.folder_report
        if self.tree.isEnabled():
            self.detail.setText(FOLDER_DETAIL if folder_mode else SESSION_DETAIL)
        self.tomogram_options.show_scope(self._scope, self._tomogram_issue)

    def _set_checked(self, item: QTreeWidgetItem, checked: bool) -> None:
        item.setCheckState(0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def _set_descendants_checked(self, item: QTreeWidgetItem, state: Qt.CheckState) -> None:
        for child_index in range(item.childCount()):
            child = item.child(child_index)
            child.setCheckState(0, state)
            self._set_descendants_checked(child, state)

    def _refresh_ancestor_check_states(self, item: QTreeWidgetItem | None) -> None:
        while item is not None:
            child_states = [item.child(index).checkState(0) for index in range(item.childCount())]
            if child_states and all(state == Qt.CheckState.Checked for state in child_states):
                item.setCheckState(0, Qt.CheckState.Checked)
            elif child_states and all(state == Qt.CheckState.Unchecked for state in child_states):
                item.setCheckState(0, Qt.CheckState.Unchecked)
            else:
                item.setCheckState(0, Qt.CheckState.PartiallyChecked)
            item = item.parent()

    def _iter_items(self):
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            yield from self._iter_item_tree(item)

    def _iter_item_tree(self, item: QTreeWidgetItem):
        yield item
        for child_index in range(item.childCount()):
            yield from self._iter_item_tree(item.child(child_index))


def _is_processing_group(value: object) -> bool:
    return isinstance(value, ProjectTreeGroup) and value.processing_source is not None


def _group_tooltip(group: ProjectTreeGroup) -> str:
    lines = [group.display_name]
    if group.is_renamed:
        lines.append(f"Automatic name: {group.automatic_name}")
    lines.extend(str(session.path) for session in group.sessions)
    lines.extend(group.warnings)
    return "\n".join(lines)
