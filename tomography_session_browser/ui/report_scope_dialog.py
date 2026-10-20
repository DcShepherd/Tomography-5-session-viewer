"""The checkbox dialog that chooses what a PDF report covers.

What a selection means lives in ``presentation/report_scope.py``; this module
only draws the tree and collects the request.
"""

from __future__ import annotations

from typing import Any, Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QHeaderView,
    QTreeWidget,
    QTreeWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.domain.models import Sample, Session
from tomography_session_browser.ui.icons import themed_icon
from tomography_session_browser.ui.navigation_icons import PROJECT_GROUP_ICONS
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
from tomography_session_browser.presentation.scopes import LinkedSampleGroup
from tomography_session_browser.presentation.design_tokens import SPACE_L, SPACE_M, SPACE_S
from tomography_session_browser.ui.widgets.rounded_viewport import round_viewport


REPORT_GROUP_ROLE = int(Qt.ItemDataRole.UserRole)
REPORT_SESSION_ROLE = int(Qt.ItemDataRole.UserRole) + 1
REPORT_SAMPLE_ROLE = int(Qt.ItemDataRole.UserRole) + 2


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
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._groups = list(groups)
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

        self.detail = QLabel(
            "Ticks choose report content. Group checkboxes and Select all include full atlas overviews. The current linked-scope preset selects collection datasets only.",
            self,
        )
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
        layout.addWidget(self.tree, stretch=1)

        controls = QVBoxLayout()
        controls.setSpacing(SPACE_S)
        current_controls = QHBoxLayout()
        content_controls = QHBoxLayout()
        content_controls.setSpacing(SPACE_S)
        # "view" described the widget; this is about the scientific scope being
        # reviewed, and now names it.
        scope_name = _describe_current_scope(current_scope)
        linked_preset = isinstance(current_scope, ProjectTreeGroup) and current_scope.kind == "linked"
        current_label = "Select current collections" if linked_preset else "Select current review scope"
        self.select_current_button = QPushButton(
            f"{current_label}: {scope_name}"
            if scope_name
            else current_label,
            self,
        )
        self.select_current_button.setEnabled(current_scope is not None)
        self.select_current_button.setToolTip(
            f"Select {scope_name}, the scope currently under review"
            if scope_name
            else "No current reportable scope is available"
        )
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
        current_controls.addStretch(1)
        for button in (self.select_all_button, self.clear_button, self.expand_button, self.collapse_button):
            content_controls.addWidget(button)
        content_controls.addStretch(1)
        controls.addLayout(current_controls)
        controls.addLayout(content_controls)
        layout.addLayout(controls)

        self.include_atlas_overview = QCheckBox("Also include supporting atlas-session overviews", self)
        self.include_atlas_overview.setToolTip("Include the full supporting screening session, which may contain unrelated atlases.")
        atlas_help = QLabel("Full atlas-session overviews can include atlases unrelated to the selected datasets.", self)
        atlas_help.setObjectName("mutedLabel")
        atlas_help.setWordWrap(True)
        layout.addWidget(self.include_atlas_overview)
        layout.addWidget(atlas_help)
        self.include_atlas_overview.toggled.connect(self._update_generate_enabled)

        self.include_exposure_thumbnails = QCheckBox("Include exposure thumbnails for each tilt series", self)
        self.include_exposure_thumbnails.setChecked(include_exposure_thumbnails)
        exposure_help_text = (
            "Adds one image per tilt series from its Thumbnails stack after its tilt-series table, four to a page: "
            "the image nearest 0°, or nearest the pretilt for a lamella, with its time, camera dose and stage position."
        )
        self.include_exposure_thumbnails.setToolTip(exposure_help_text)
        self.include_exposure_thumbnails.setAccessibleDescription(exposure_help_text)
        exposure_help = QLabel(exposure_help_text, self)
        exposure_help.setObjectName("mutedLabel")
        exposure_help.setWordWrap(True)
        exposure_help.setVisible(False)
        self.thumbnail_details = QToolButton(self)
        self.thumbnail_details.setObjectName("inlineLink")
        self.thumbnail_details.setText("Thumbnail details")
        self.thumbnail_details.setCheckable(True)
        self.thumbnail_details.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.thumbnail_details.setArrowType(Qt.ArrowType.RightArrow)
        self.thumbnail_details.setAccessibleDescription(exposure_help_text)
        def show_thumbnail_details(checked: bool) -> None:
            exposure_help.setVisible(checked)
            self.thumbnail_details.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)
        self.thumbnail_details.toggled.connect(show_thumbnail_details)
        layout.addWidget(self.include_exposure_thumbnails)
        layout.addWidget(self.thumbnail_details)
        layout.addWidget(exposure_help)
        self.include_exposure_thumbnails.toggled.connect(self._update_generate_enabled)

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
        if current_scope is not None:
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
        if not request.has_selection:
            return "Nothing selected"
        try:
            scope = normalise_report_scope(self._sessions, self._groups, request)
        except Exception:  # pragma: no cover - a preview must never block the dialog
            return _selection_summary_text(request)
        return describe_report_scope(scope)

    def selected_request(self) -> ReportScopeRequest:
        group_keys: set[str] = set()
        session_keys: set[str] = set()
        sample_keys: set[str] = set()
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
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
        )

    def select_scope(self, value: Any | None) -> None:
        """Tick the rows that stand for ``value``.

        When no row does, the reviewer's existing ticks are left alone: the
        button used to clear them first and then find nothing to select.
        """

        items = self._items_for_scope(value)
        if not items:
            return
        self.clear_selection()
        selected_items = items
        if isinstance(value, ProjectTreeGroup) and value.kind == "linked":
            selected_items = [self._session_items[session_key(session)] for session in value.collection_sessions]
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
        if self.tree.currentItem() is not None:
            self.tree.scrollToItem(self.tree.currentItem(), QTreeWidget.ScrollHint.PositionAtTop)

    def _items_for_scope(self, value: Any | None) -> list[QTreeWidgetItem]:
        """The rows that stand for ``value`` in this dialog; empty when none does."""

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
        if not self._groups:
            self.detail.setText("No sessions loaded. Load a session before generating a report.")
            self.tree.setEnabled(False)
            return
        self.tree.clear()
        for group in self._groups:
            item = self._group_item(group)
            self.tree.addTopLevelItem(item)
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
        self.generate_button.setEnabled(request.has_selection)
        self.selection_summary.setText(self.scope_preview_text(request))

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


def _group_tooltip(group: ProjectTreeGroup) -> str:
    lines = [group.display_name]
    if group.is_renamed:
        lines.append(f"Automatic name: {group.automatic_name}")
    lines.extend(str(session.path) for session in group.sessions)
    lines.extend(group.warnings)
    return "\n".join(lines)
