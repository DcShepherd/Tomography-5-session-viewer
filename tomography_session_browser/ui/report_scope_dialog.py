"""The checkbox dialog that chooses what a PDF report covers (plan B6, board 2b).

What a selection means lives in ``presentation/report_scope.py`` and what the
report then holds in ``presentation/report_preview.py``; this module draws the
tree, the "In this report" pane and the INCLUDE line, and collects the request.
The tree and the pane share one height, so their edges line up. The dialog
opens as tall as the longer of the two, up to 80 % of the screen, and keeps
that height while the user ticks.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                               QPushButton, QTreeWidgetItem, QVBoxLayout, QWidget)

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
from tomography_session_browser.presentation.report_preview import ReportPreview, report_preview
from tomography_session_browser.presentation.report_scope import (
    ReportScope,
    ReportScopeRequest,
    normalise_report_scope,
    reportable_samples,
    sample_key,
)
from tomography_session_browser.presentation.report_tomograms import ReportSource, ReportTomograms
from tomography_session_browser.ui.report_preview_pane import ReportPreviewPane
from tomography_session_browser.ui.report_scope_tree import ReportScopeTree
from tomography_session_browser.ui.report_tomogram_options import TomogramOptions
from tomography_session_browser.presentation.scopes import LinkedSampleGroup
from tomography_session_browser.presentation.design_tokens import SPACE_L, SPACE_M, SPACE_S
from tomography_session_browser.ui.dashboard.layout import FlowLayout
from tomography_session_browser.ui.theme import current_palette
from tomography_session_browser.ui.widgets.elided_label import ElidedLabel
from tomography_session_browser.ui.widgets.rounded_viewport import round_viewport


REPORT_GROUP_ROLE = int(Qt.ItemDataRole.UserRole)
REPORT_SESSION_ROLE = int(Qt.ItemDataRole.UserRole) + 1
REPORT_SAMPLE_ROLE = int(Qt.ItemDataRole.UserRole) + 2
REPORT_FOLDER_ROLE = int(Qt.ItemDataRole.UserRole) + 3
#: A user group heading (B4): a three-state parent of its acquisition members, never a scope itself.
REPORT_USER_GROUP_ROLE = int(Qt.ItemDataRole.UserRole) + 4
#: A folder row's own Type word, kept while the row reads "Not used".
REPORT_TYPE_ROLE = int(Qt.ItemDataRole.UserRole) + 5

#: Plain Type words for the badge codes (Q7); the tooltips keep the badge meanings.
TYPE_WORDS = {"LS": "Linked group", "AT": "Atlas", "DC": "Collection", "!": "Unresolved"}
NOT_USED = "Not used"
NOT_USED_TIP = "Not used: the report does not include linked tomograms."
FOLDER_TIP = "Ticks select the tomograms that the report shows. Ticks do not change links."
NO_SESSIONS = "No sessions are loaded. Load a session to make a report."
#: The dialog's width on a screen with room for it (board 2b); the tree takes three fifths.
DIALOG_WIDTH = 940
#: The tree keeps three rows on a short screen; above the cap both panels scroll (GR-03).
TREE_MIN_HEIGHT = 140
#: The opening height stops at this part of the available screen height.
SCREEN_SHARE = 0.8


def _repolish(widget: QWidget) -> None:
    """Re-run the style engine so a late ``setObjectName`` takes effect."""

    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)
    widget.update()


def _describe_current_scope(value: Any | None) -> str:
    """Name the scope under review, for the dialog's primary action."""

    if value is None:
        return ""
    for attribute in ("display_name", "name", "label"):
        candidate = getattr(value, attribute, None)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def _caption(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName("cardTitle")
    return label


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
        self._folder_heading: QTreeWidgetItem | None = None
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
        self._height_fitted = False
        self._group_items: dict[str, QTreeWidgetItem] = {}
        self._session_items: dict[str, QTreeWidgetItem] = {}
        self._sample_items: dict[str, QTreeWidgetItem] = {}

        self.setWindowTitle("Select report scope")
        self.setModal(True)
        available = self.screen().availableGeometry()
        # The height follows the content when the dialog first shows (_fit_height).
        self.resize(min(DIALOG_WIDTH, available.width() - 48), min(600, available.height() - 80))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_L, SPACE_L, SPACE_L, SPACE_L)
        layout.setSpacing(SPACE_M)
        # One grid, so the two panels share their top line and their height.
        panels = QGridLayout()
        panels.setHorizontalSpacing(SPACE_L)
        panels.setVerticalSpacing(SPACE_S)
        panels.setColumnStretch(0, 3)
        panels.setColumnStretch(1, 2)
        panels.setRowStretch(1, 1)

        # Its own tick tree (B4): no selection, a click ticks, one row-wide hover.
        # Made first, so Tab goes tree, its buttons, the options, the footer.
        self.tree = ReportScopeTree(self)
        round_viewport(self.tree)
        self.tree.setAccessibleName("Report content")
        self.tree.setAccessibleDescription(
            "Tick linked groups, sessions, samples or processing folders to include them in the report."
        )
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Content", "Type"])
        self.tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.setMinimumHeight(TREE_MIN_HEIGHT)

        # "view" described the widget; this is about the scientific scope being
        # reviewed. Its name is in the tooltip, so a long name cannot set the width (GR-03).
        scope_name = _describe_current_scope(current_scope)
        linked_preset = isinstance(current_scope, ProjectTreeGroup) and current_scope.kind == "linked"
        self.select_current_button = QPushButton("Current scope", self)
        self.select_current_button.setEnabled(current_scope is not None)
        if not scope_name:
            current_tip = "Not available: no scope is open."
        elif linked_preset:
            current_tip = f"Ticks the collections of {scope_name}, the scope that you examine."
        else:
            current_tip = f"Ticks {scope_name}, the scope that you examine."
        self.select_current_button.setToolTip(current_tip)
        self.select_current_button.setAccessibleDescription(current_tip)
        self.select_all_button = QPushButton("Select all", self)
        self.select_all_button.setToolTip("Ticks all rows.")
        self.clear_button = QPushButton("Clear", self)
        self.clear_button.setToolTip("Removes all ticks.")
        content_header = QHBoxLayout()
        content_header.setSpacing(SPACE_S)
        content_header.addWidget(_caption("Report content", self))
        content_header.addStretch(1)
        for button in (self.select_current_button, self.select_all_button, self.clear_button):
            button.setObjectName("dashboardFilterButton")
            content_header.addWidget(button)
        panels.addLayout(content_header, 0, 0)
        panels.addWidget(self.tree, 1, 0)

        # The count shortens before it widens the dialog.
        self.preview_badge = ElidedLabel("", parent=self, prefer_full_width=True)
        self.preview_badge.setObjectName("mutedLabel")
        self.preview_badge.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        preview_header = QHBoxLayout()
        preview_header.setSpacing(SPACE_S)
        preview_header.addWidget(_caption("In this report", self))
        preview_header.addWidget(self.preview_badge, 1)
        self.preview_pane = ReportPreviewPane(self)
        panels.addLayout(preview_header, 0, 1)
        panels.addWidget(self.preview_pane, 1, 1)
        layout.addLayout(panels, 1)

        # The INCLUDE line and the buttons share the footer, under both panels. On a
        # narrow screen the options wrap onto a second line instead of widening the dialog.
        footer = QHBoxLayout()
        footer.setSpacing(SPACE_L)
        include = QWidget(self)
        options = FlowLayout(spacing=SPACE_L, line_spacing=SPACE_S)
        include.setLayout(options)
        options.addWidget(_caption("Include", include))
        # The AT row's tick is the only atlas control (plan B5, Q6 A).
        self.include_exposure_thumbnails = QCheckBox("Exposure thumbnails", include)
        self.include_exposure_thumbnails.setChecked(include_exposure_thumbnails)
        exposure_help_text = (
            "Adds one image for each tilt series after its tilt-series table, four on a page: the image nearest 0°, "
            "or nearest the pretilt for a lamella, from its Thumbnails stack, with its time and camera dose."
        )
        self.include_exposure_thumbnails.setToolTip(exposure_help_text)
        self.include_exposure_thumbnails.setAccessibleDescription(exposure_help_text)
        self.include_exposure_thumbnails.toggled.connect(self._update_generate_enabled)
        options.addWidget(self.include_exposure_thumbnails)
        self.tomogram_options = TomogramOptions(include_tomograms=include_tomograms,
                                                include_slices=include_slices, parent=include)
        self.tomogram_options.setVisible(tomograms is not None or tomogram_issue is not None)
        self.tomogram_options.changed.connect(self._update_generate_enabled)
        options.addWidget(self.tomogram_options)
        # Centred on the buttons' line (owner note 1 on Q7); Qt centres the fixed-height buttons already.
        footer.addWidget(include, 1, Qt.AlignmentFlag.AlignVCenter)

        self.buttons = QDialogButtonBox(self)
        self.generate_button = self.buttons.addButton("Generate report", QDialogButtonBox.ButtonRole.AcceptRole)
        self.cancel_button = self.buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        # The confirming action sat at the same visual weight as Cancel and
        # the secondary selection buttons. Qt has already polished the button
        # by the time ``addButton`` returns, so the new object name needs an
        # explicit re-polish to pick up the #primaryButton rules.
        self.generate_button.setObjectName("primaryButton")
        _repolish(self.generate_button)
        self.generate_button.setDefault(True)
        self.generate_button.setAutoDefault(True)
        self.generate_button.setEnabled(False)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        footer.addWidget(self.buttons)
        layout.addLayout(footer)

        self.select_current_button.clicked.connect(lambda: self.select_scope(current_scope))
        self.select_all_button.clicked.connect(self.select_all)
        self.clear_button.clicked.connect(self.clear_selection)

        self._populate()
        # Every row shows when the dialog opens, and the opening height follows them.
        self.tree.expandAll()
        if current_scope is not None and not self._items_for_scope(current_scope):
            # Nothing in the tree stands for it, so the button would do nothing.
            self.select_current_button.setEnabled(False)
            reason = f"Not available: {scope_name or 'the current scope'} has no entry in this list."
            self.select_current_button.setToolTip(reason)
            self.select_current_button.setAccessibleDescription(reason)
        if current_scope is not None or not self._session_items:
            self.select_scope(current_scope)
        self._update_generate_enabled()

    def scope_preview_text(self, request: ReportScopeRequest | None = None) -> str:
        """What the report will contain, from the same function that decides it.

        Routing the preview through ``normalise_report_scope`` is the point: a
        summary derived from the checkboxes could describe a scope the
        generator never produces.
        """

        request = request if request is not None else self.selected_request()
        return self._preview(request)[1].text

    def _preview(self, request: ReportScopeRequest) -> tuple[ReportScope | None, ReportPreview]:
        """The normalised scope and its preview; no scope when the selection has none.

        Only the refusal of an unreportable selection is caught: any other
        failure is a fault that must not hide behind an enabled Generate.
        """

        if not self.tree.isEnabled():
            return None, report_preview(None, NO_SESSIONS)
        if not request.has_selection:
            return None, report_preview(None)
        try:
            scope = normalise_report_scope(self._sessions, self._groups, request, self._tomograms)
        except ValueError as exc:
            return None, report_preview(None, str(exc))
        return scope, report_preview(scope)

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
        self.tree.reveal(selected_items[0])
        self._update_generate_enabled()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        handle = self.windowHandle()
        if handle is not None and not self._follows_screen:
            handle.screenChanged.connect(lambda _screen: self._fit_to_screen())
            self._follows_screen = True
        if not self._height_fitted:
            self._height_fitted = True
            self._fit_height()
        self._fit_to_screen()
        current = self.tree.currentItem()
        if current is not None:
            self.tree.reveal(current)

    def _available_geometry(self) -> QRect:
        return self.screen().availableGeometry()

    def opening_body_height(self) -> int:
        """The panels' shared height that shows every tree row and every preview line."""

        tree = self.tree
        rows = sum(1 for item in self._iter_items() if not item.isHidden() and _shown(item))
        row_height = tree.sizeHintForRow(0) if tree.topLevelItemCount() else 0
        tree_height = (tree.header().sizeHint().height() + rows * max(row_height, 1)
                       + 2 * tree.frameWidth() + tree.contentsMargins().top() + tree.contentsMargins().bottom())
        return max(tree_height, self.preview_pane.content_height(), TREE_MIN_HEIGHT)

    def _fit_height(self) -> None:
        """Set the opening height once: the longer panel, up to 80 % of the screen (Q7).

        It does not change while the user ticks or folds rows; the user can
        still resize the dialog. Above the cap both panels scroll.
        """

        layout = self.layout()
        if layout is not None:
            layout.activate()
        chrome = self.height() - self.tree.height()          # margins, top line, footer
        cap = int(self._available_geometry().height() * SCREEN_SHARE)
        height = min(chrome + self.opening_body_height(), cap)
        self.resize(self.width(), max(height, self.minimumSizeHint().height()))

    def _fit_to_screen(self) -> None:
        """Keep the whole frame, title bar included, on the available screen (GR-03).

        The two panels are the layout's stretch row, so they give up height first.
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
            self._folder_heading = self._folders_item(sources)
            self.tree.addTopLevelItem(self._folder_heading)

    def _user_group_item(self, heading: UserHeading) -> QTreeWidgetItem:
        item = QTreeWidgetItem([heading.group.name, "Your group"])
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

        heading = QTreeWidgetItem(["Processing folders", "Folder group"])
        heading.setToolTip(0, FOLDER_TIP)
        heading.setData(1, REPORT_TYPE_ROLE, "Folder group")
        heading.setFlags(heading.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
        heading.setCheckState(0, Qt.CheckState.Unchecked)
        heading.setIcon(0, themed_icon("folder-open"))
        for source in sources:
            pipeline = source.source.display_pipeline
            # The folder's tomogram count moves to the tooltip: the preview counts what prints (Q7).
            detail = " · ".join(part for part in (pipeline, count_phrase(len(source.source.tomograms), 'tomogram')) if part)
            kind = f"{pipeline} folder" if pipeline else "Folder"
            item = QTreeWidgetItem([source.label, kind])
            item.setToolTip(0, f"{source.label}\n{detail}")
            item.setToolTip(1, detail)
            item.setData(0, REPORT_FOLDER_ROLE, source.source.id)
            item.setData(1, REPORT_TYPE_ROLE, kind)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Unchecked)
            item.setIcon(0, themed_icon("folder-open"))
            heading.addChild(item)
            self._folder_items[source.source.id] = item
        heading.setExpanded(True)
        return heading

    def _group_item(self, group: ProjectTreeGroup) -> QTreeWidgetItem:
        badge = project_group_badge(group)
        item = QTreeWidgetItem([group.display_name, TYPE_WORDS.get(badge, badge)])
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
        item = QTreeWidgetItem([session.name or session.path.name, TYPE_WORDS.get(badge, badge)])
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
        self._scope, preview = self._preview(request)
        self.generate_button.setEnabled(self._scope is not None)
        self.preview_pane.show_preview(preview)
        self.preview_badge.setText(preview.badge)
        folder_mode = self._scope is not None and self._scope.folder_report
        # A folder report cannot use exposure thumbnails, so the box is not shown (board 2, state b).
        self.include_exposure_thumbnails.setVisible(not folder_mode)
        self.tomogram_options.show_scope(self._scope, self._tomogram_issue)
        self._mark_unused_folders(self._scope is not None and bool(self._scope.sessions)
                                  and not self._scope.prints_tomograms)

    def _mark_unused_folders(self, unused: bool) -> None:
        """Folder ticks matter only when tomograms print: say so on the rows (board 2, state d)."""

        rows = [*([self._folder_heading] if self._folder_heading is not None else []), *self._folder_items.values()]
        muted = QColor(current_palette().text_muted)
        self._item_updates_blocked = True
        try:
            for item in rows:
                kind = item.data(1, REPORT_TYPE_ROLE)
                text = NOT_USED if unused else kind
                if item.text(1) == text:
                    continue
                item.setText(1, text)
                item.setToolTip(1, NOT_USED_TIP if unused else (item.toolTip(0).partition("\n")[2] or FOLDER_TIP))
                for column in (0, 1):
                    item.setData(column, Qt.ItemDataRole.ForegroundRole, muted if unused else None)
        finally:
            self._item_updates_blocked = False

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


def _shown(item: QTreeWidgetItem) -> bool:
    """Whether every ancestor of ``item`` is expanded, so the row is in the list."""
    parent = item.parent()
    while parent is not None:
        if not parent.isExpanded():
            return False
        parent = parent.parent()
    return True


def _is_processing_group(value: object) -> bool:
    return isinstance(value, ProjectTreeGroup) and value.processing_source is not None


def _group_tooltip(group: ProjectTreeGroup) -> str:
    lines = [group.display_name]
    if group.is_renamed:
        lines.append(f"Automatic name: {group.automatic_name}")
    lines.extend(str(session.path) for session in group.sessions)
    lines.extend(group.warnings)
    return "\n".join(lines)
