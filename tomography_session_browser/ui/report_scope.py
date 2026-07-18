from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QHeaderView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.models import Sample, Session
from tomography_session_browser.reports import ProjectReportGroup
from tomography_session_browser.ui.icons import themed_icon
from tomography_session_browser.ui.project_model import ProjectTreeGroup, session_key


REPORT_GROUP_ROLE = int(Qt.ItemDataRole.UserRole)
REPORT_SESSION_ROLE = int(Qt.ItemDataRole.UserRole) + 1
REPORT_SAMPLE_ROLE = int(Qt.ItemDataRole.UserRole) + 2


@dataclass(frozen=True, slots=True)
class ReportScopeRequest:
    group_keys: frozenset[str]
    session_keys: frozenset[str]
    sample_keys: frozenset[str] = frozenset()

    @property
    def has_selection(self) -> bool:
        return bool(self.group_keys or self.session_keys or self.sample_keys)


@dataclass(frozen=True, slots=True)
class ReportScope:
    sessions: tuple[Session, ...]
    project_title: str
    project_groups: tuple[ProjectReportGroup, ...]
    default_filename: str


class ReportScopeDialog(QDialog):
    """Checkbox tree for choosing the sessions included in a PDF report."""

    def __init__(
        self,
        groups: Iterable[ProjectTreeGroup],
        *,
        current_scope: Any | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._groups = list(groups)
        self._item_updates_blocked = False
        self._group_items: dict[str, QTreeWidgetItem] = {}
        self._session_items: dict[str, QTreeWidgetItem] = {}
        self._sample_items: dict[str, QTreeWidgetItem] = {}

        self.setWindowTitle("Select report scope")
        self.setModal(True)
        self.resize(560, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        heading = QLabel("Choose sessions and samples for the report", self)
        heading.setObjectName("cardTitle")
        layout.addWidget(heading)

        self.detail = QLabel(
            "Select linked groups, sessions, or individual data collection samples. Linked atlas context is included once where needed.",
            self,
        )
        self.detail.setWordWrap(True)
        self.detail.setObjectName("mutedLabel")
        layout.addWidget(self.detail)

        self.tree = QTreeWidget(self)
        self.tree.setObjectName("projectTree")
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Scope", ""])
        self.tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.tree, stretch=1)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.select_current_button = QPushButton("Select current view", self)
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
            controls.addWidget(button)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.buttons = QDialogButtonBox(self)
        self.generate_button = self.buttons.addButton("Generate report", QDialogButtonBox.ButtonRole.AcceptRole)
        self.cancel_button = self.buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
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
        if current_scope is not None:
            self.select_scope(current_scope)
        self._update_generate_enabled()

    def selected_request(self) -> ReportScopeRequest:
        group_keys: set[str] = set()
        session_keys: set[str] = set()
        sample_keys: set[str] = set()
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            group = item.data(0, REPORT_GROUP_ROLE)
            if not isinstance(group, ProjectTreeGroup):
                continue
            if item.checkState(0) == Qt.CheckState.Checked:
                group_keys.add(group.key)
                continue
            self._collect_descendant_selection(item, session_keys, sample_keys)
        return ReportScopeRequest(frozenset(group_keys), frozenset(session_keys), frozenset(sample_keys))

    def select_scope(self, value: Any | None) -> None:
        self.clear_selection()
        if isinstance(value, ProjectTreeGroup):
            item = self._group_items.get(value.key)
            if item is not None:
                self._set_checked(item, True)
                self.tree.scrollToItem(item)
            return
        if isinstance(value, Session):
            item = self._session_items.get(session_key(value))
            if item is not None:
                self._set_checked(item, True)
                self.tree.scrollToItem(item)
            return
        if isinstance(value, Sample):
            for key, item in self._sample_items.items():
                if item.data(0, REPORT_SAMPLE_ROLE) is value:
                    self._set_checked(item, True)
                    self.tree.scrollToItem(item)
                    return
        self._update_generate_enabled()

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
                for sample in _reportable_samples(session):
                    item.addChild(self._sample_item(session, sample))
                if item.childCount():
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsAutoTristate)

    def _group_item(self, group: ProjectTreeGroup) -> QTreeWidgetItem:
        item = QTreeWidgetItem([group.display_name, _group_badge(group)])
        item.setToolTip(0, _group_tooltip(group))
        item.setToolTip(1, _group_badge_tooltip(group))
        item.setData(0, REPORT_GROUP_ROLE, group)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        if group.kind == "linked":
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsAutoTristate)
        item.setCheckState(0, Qt.CheckState.Unchecked)
        item.setIcon(0, themed_icon("layers" if group.kind == "linked" else "folder-open"))
        return item

    def _session_item(self, session: Session, badge: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([session.name or session.path.name, badge])
        item.setToolTip(0, str(session.path))
        item.setToolTip(1, _session_badge_tooltip(badge))
        item.setData(0, REPORT_SESSION_ROLE, session)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        samples = _reportable_samples(session)
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
        self.generate_button.setEnabled(self.selected_request().has_selection)

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


def normalise_report_scope(
    sessions: Iterable[Session],
    groups: Iterable[ProjectTreeGroup],
    request: ReportScopeRequest,
) -> ReportScope:
    loaded_sessions = list(sessions)
    groups_by_key = {group.key: group for group in groups}
    sessions_by_key = {session_key(session): session for session in loaded_sessions}
    selected_session_ids: set[int] = set()
    supporting_session_ids: set[int] = set()
    selected_samples_by_session: dict[int, list[Sample]] = {}
    explicit_labels: list[str] = []
    report_groups: list[ProjectReportGroup] = []

    for group in groups:
        if group.key not in request.group_keys:
            continue
        group_sessions = [session for session in group.sessions if session_key(session) in sessions_by_key]
        if not group_sessions:
            continue
        selected_session_ids.update(id(session) for session in group_sessions)
        explicit_labels.append(group.display_name)
        report_groups.append(_project_report_group(group.display_name, group, group_sessions, use_group_kind=True))

    covered_by_group = set(selected_session_ids)
    explicit_sessions: list[Session] = []
    for session in loaded_sessions:
        key = session_key(session)
        if key not in request.session_keys or id(session) in covered_by_group:
            continue
        explicit_sessions.append(session)
        selected_session_ids.add(id(session))
        explicit_labels.append(session.name or session.path.name)

    for session in loaded_sessions:
        if id(session) in selected_session_ids:
            continue
        selected_samples = [
            sample
            for sample in session.samples
            if sample_key(session, sample) in request.sample_keys
        ]
        if not selected_samples:
            continue
        selected_samples_by_session[id(session)] = selected_samples
        explicit_labels.extend(sample.name or sample.path.name for sample in selected_samples)

    for session in explicit_sessions:
        group = _group_for_session(groups_by_key.values(), session)
        group_sessions = [session]
        if _is_collection_session(session) and group is not None and group.atlas_session is not None:
            supporting_session_ids.add(id(group.atlas_session))
            group_sessions = [group.atlas_session, session]
        report_groups.append(
            _project_report_group(
                session.name or session.path.name,
                group,
                group_sessions,
                fallback_kind="AT" if session.kind == SessionKind.ATLAS_SCREENING else "DC",
            )
        )

    for session in loaded_sessions:
        selected_samples = selected_samples_by_session.get(id(session))
        if not selected_samples:
            continue
        group = _group_for_session(groups_by_key.values(), session)
        if _is_collection_session(session) and group is not None and group.atlas_session is not None:
            supporting_session_ids.add(id(group.atlas_session))
        filtered_session = _filtered_session_for_samples(session, selected_samples)
        report_groups.append(
            _project_report_group(
                session.name or session.path.name,
                group,
                [group.atlas_session, filtered_session]
                if group is not None and group.atlas_session is not None and _is_collection_session(session)
                else [filtered_session],
                fallback_kind="DC",
            )
        )

    selected_or_supporting = selected_session_ids | supporting_session_ids
    selected_sessions_list: list[Session] = []
    for session in loaded_sessions:
        if id(session) in selected_or_supporting:
            selected_sessions_list.append(session)
            continue
        selected_samples = selected_samples_by_session.get(id(session))
        if selected_samples:
            selected_sessions_list.append(_filtered_session_for_samples(session, selected_samples))
    selected_sessions = tuple(selected_sessions_list)
    if not selected_sessions:
        raise ValueError("Select at least one reportable session.")

    if len(explicit_labels) == 1:
        title = f"Report: {explicit_labels[0]}"
        filename_label = explicit_labels[0]
    else:
        title = "Report: Selected sessions"
        filename_label = "selected_sessions"

    return ReportScope(
        sessions=selected_sessions,
        project_title=title,
        project_groups=tuple(_dedupe_project_report_groups(report_groups)),
        default_filename=f"{_safe_filename(filename_label)}_report.pdf",
    )


def sample_key(session: Session, sample: Sample) -> str:
    return f"{session_key(session)}::sample::{sample.id or str(sample.path)}"


def _filtered_session_for_samples(session: Session, samples: list[Sample]) -> Session:
    return replace(session, samples=list(samples))


def _reportable_samples(session: Session) -> list[Sample]:
    if session.kind == SessionKind.ATLAS_SCREENING:
        return []
    return [
        sample
        for sample in session.samples
        if (
            sample.overviews
            or sample.search_maps
            or sample.search_tiles
            or sample.batch_positions
            or sample.tilt_series
        )
    ]


def _group_for_session(groups: Iterable[ProjectTreeGroup], session: Session) -> ProjectTreeGroup | None:
    for group in groups:
        if any(candidate is session for candidate in group.sessions):
            return group
    return None


def _project_report_group(
    display_name: str,
    group: ProjectTreeGroup | None,
    sessions: list[Session],
    *,
    fallback_kind: str = "DC",
    use_group_kind: bool = False,
) -> ProjectReportGroup:
    return ProjectReportGroup(
        display_name=display_name,
        automatic_name=group.automatic_name if group is not None else display_name,
        kind=_group_badge(group) if group is not None and use_group_kind else fallback_kind,
        session_names=tuple(session.name for session in sessions),
        session_paths=tuple(str(session.path) for session in sessions),
    )


def _dedupe_project_report_groups(groups: Iterable[ProjectReportGroup]) -> list[ProjectReportGroup]:
    result: list[ProjectReportGroup] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for group in groups:
        key = (group.display_name, group.session_paths)
        if key in seen:
            continue
        seen.add(key)
        result.append(group)
    return result


def _is_collection_session(session: Session) -> bool:
    return session.kind != SessionKind.ATLAS_SCREENING


def _safe_filename(label: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", label.strip()).strip("._")
    return cleaned or "session"


def _group_badge(group: ProjectTreeGroup | None) -> str:
    if group is None:
        return "DC"
    if group.kind == "linked":
        return "LS"
    if group.kind == "atlas":
        return "AT"
    if group.kind == "unresolved":
        return "!"
    return "DC"


def _group_badge_tooltip(group: ProjectTreeGroup) -> str:
    if group.kind == "linked":
        return "Linked Session"
    if group.kind == "atlas":
        return "Atlas-only session"
    if group.kind == "unresolved":
        return "Unresolved or ambiguous atlas link"
    return "Data collection session"


def _session_badge_tooltip(badge: str) -> str:
    if badge == "AT":
        return "Atlas-only session"
    if badge == "DC":
        return "Data collection session"
    if badge == "LS":
        return "Linked session"
    if badge == "!":
        return "Unresolved or ambiguous atlas link"
    return badge


def _group_tooltip(group: ProjectTreeGroup) -> str:
    lines = [group.display_name]
    if group.is_renamed:
        lines.append(f"Automatic name: {group.automatic_name}")
    lines.extend(str(session.path) for session in group.sessions)
    lines.extend(group.warnings)
    return "\n".join(lines)
