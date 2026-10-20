"""The project tree's rows: one per group, session, sample, entity group and
acquisition item, plus the "Recent sessions" section.

Display only. A row carries its entity (``OBJECT_ROLE``) and a stable ID for
remembering whether it was expanded (``EXPANSION_ROLE``); building rows never
changes a session, its groups or its names.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import QTreeWidgetItem

from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.identity import path_identity_key
from tomography_session_browser.domain.models import (
    Atlas,
    BatchPosition,
    Overview,
    Sample,
    SearchMap,
    SearchTile,
    Session,
    TiltSeries,
)
from tomography_session_browser.domain.traversal import has_collection_data
from tomography_session_browser.presentation.project_tree import (
    SESSION_KIND_BADGES,
    PROJECT_BADGE_MEANINGS,
    ProjectTreeGroup,
    project_group_badge,
    project_group_badge_tooltip,
    session_badge_tooltip,
    session_key,
)
from tomography_session_browser.presentation.scopes import EntityGroup, LinkedSampleGroup
from tomography_session_browser.presentation.workspace import EntityKind, Workspace, atlas_display_key
from tomography_session_browser.ui.icons import themed_icon
from tomography_session_browser.ui.navigation_icons import PROJECT_GROUP_ICONS, TAB_ICONS, TREE_ENTITY_GROUP_ICONS
from tomography_session_browser.ui.project_tree.widgets import EXPANSION_ROLE, OBJECT_ROLE, RECENT_PATH_ROLE
from tomography_session_browser.ui.theme import current_palette
from tomography_session_browser.presentation.tab_keys import TabKey

#: How many recent sessions the tree offers below the project.
RECENT_SESSION_ROWS = 4


class ProjectTreeItems:
    """Builds the rows for one workspace.

    ``label_for`` names an entity as the window does. Every tilt-series row
    made is kept in ``tilt_items_by_id``, for the cross-view highlight.
    """

    def __init__(self, workspace: Workspace, label_for: Callable[[Any], str]) -> None:
        self._workspace = workspace
        self._label_for = label_for
        self.tilt_items_by_id: dict[str, list[QTreeWidgetItem]] = {}

    def group_item(self, group: ProjectTreeGroup) -> QTreeWidgetItem:
        """A project group's row, without its children."""

        badge = project_group_badge(group)
        item = QTreeWidgetItem([group.display_name, badge])
        item.setToolTip(0, project_group_tooltip(group))
        item.setToolTip(1, project_group_badge_tooltip(group))
        # Spell the badge out rather than leaving a screen reader to announce
        # two letters. Unresolved groups also state why, inline.
        meaning = PROJECT_BADGE_MEANINGS.get(badge, badge)
        accessible = f"{group.display_name}, {meaning}"
        if group.warnings:
            accessible = f"{accessible}. {group.warnings[0]}"
        item.setData(0, Qt.ItemDataRole.AccessibleTextRole, accessible)
        item.setData(1, Qt.ItemDataRole.AccessibleTextRole, meaning)
        item.setData(0, OBJECT_ROLE, group)
        item.setData(0, EXPANSION_ROLE, f"project:{group.key}")
        style_tree_item(item)
        style_project_group_item(item, group)
        return item

    def populate_group_item(self, group_item: QTreeWidgetItem, group: ProjectTreeGroup) -> None:
        if group.kind == "linked" and group.atlas_session is not None:
            atlas_item = self.item(group.atlas_session.name, group.atlas_session)
            self._populate_session_item(atlas_item, group.atlas_session)
            group_item.addChild(atlas_item)
            for session in group.collection_sessions:
                session_item = self.item(session.name, session)
                self._populate_session_item(session_item, session)
                group_item.addChild(session_item)
            return

        session = group.sessions[0] if group.sessions else None
        if session is not None:
            self._populate_session_item(group_item, session)

    def display_samples(self, session: Session) -> list[Sample]:
        """The samples the tree lists under ``session``: a lone multigrid
        session leaves out empty grids."""

        if len(self._workspace.sessions) > 1:
            return session.samples
        if session.kind.value == "multigrid":
            return [sample for sample in session.samples if _sample_is_inspectable(sample)]
        return session.samples

    def item(self, label: str, value: Any) -> QTreeWidgetItem:
        tag = tree_tag_for(value, self._workspace)
        item = QTreeWidgetItem([label, tag])
        item.setToolTip(0, label)
        if tag:
            meaning = tree_tag_tooltip_for(value, tag)
            item.setToolTip(1, meaning)
            item.setData(1, Qt.ItemDataRole.AccessibleTextRole, meaning)
            item.setData(0, Qt.ItemDataRole.AccessibleTextRole, f"{label}, {meaning}")
        item.setData(0, OBJECT_ROLE, value)
        item.setData(0, EXPANSION_ROLE, tree_expansion_id_for(label, value))
        style_tree_item(item)
        set_tree_item_icon(item, value)
        if isinstance(value, TiltSeries):
            self.tilt_items_by_id.setdefault(value.id, []).append(item)
        return item

    def recent_sessions_section(self, recent_paths: Iterable[str]) -> QTreeWidgetItem | None:
        """"Recent sessions": up to four recently opened folders not loaded now."""

        # Compared as folders, not strings: a loaded session spelled with other
        # case or slashes was still offered here as "closed".
        loaded = {path_identity_key(session.path) for session in self._workspace.sessions}
        recent = [
            path
            for path in recent_paths
            if path and path_identity_key(path) not in loaded
        ][:RECENT_SESSION_ROWS]
        if not recent:
            return None
        section = QTreeWidgetItem(["Recent sessions", ""])
        section.setToolTip(0, "Recent sessions")
        section.setData(0, EXPANSION_ROLE, "recent-sessions")
        style_tree_item(section)
        for path in recent:
            label = Path(path).name or path
            child = QTreeWidgetItem([label, "closed"])
            child.setToolTip(0, f"{path}\nActivate to reopen this session")
            child.setToolTip(1, "Recently opened session")
            child.setData(0, EXPANSION_ROLE, f"recent:{path_identity_key(Path(path))}")
            # These rows previously carried no data at all, so both clicking
            # and pressing Enter did nothing — they advertised a session the
            # app would not open.
            child.setData(0, RECENT_PATH_ROLE, path)
            child.setData(
                0,
                Qt.ItemDataRole.AccessibleTextRole,
                f"{label}, recently opened session, activate to reopen",
            )
            style_tree_item(child)
            section.addChild(child)
        return section

    def _populate_session_item(self, session_item: QTreeWidgetItem, session: Session) -> None:
        display_samples = self.display_samples(session)
        if display_samples:
            for sample in display_samples:
                session_item.addChild(self._sample_item(sample))
        else:
            self._add_entity_groups(session_item, session)

    def _sample_item(self, sample: Sample) -> QTreeWidgetItem:
        sample_item = self.item(sample.name, sample)
        if sample.atlas:
            sample_item.addChild(self.item("Atlas", sample.atlas))
        self._add_collection_groups(sample_item, sample)
        return sample_item

    def _add_entity_groups(self, parent: QTreeWidgetItem, session: Session) -> None:
        if session.atlas:
            parent.addChild(self.item("Atlas", session.atlas))
        self._add_collection_groups(parent, session)

    def _add_collection_groups(self, parent: QTreeWidgetItem, holder: Session | Sample) -> None:
        self._add_group(parent, "Overviews", holder.overviews)
        self._add_group(parent, "Search maps", holder.search_maps)
        self._add_group(parent, "Search tiles", holder.search_tiles)
        self._add_group(parent, "Batch positions", holder.batch_positions)
        self._add_group(parent, "Tilt series", holder.tilt_series)

    def _add_group(self, parent: QTreeWidgetItem, label: str, values: list[Any]) -> None:
        if not values:
            return
        group = QTreeWidgetItem([label, str(len(values))])
        group.setToolTip(0, group.text(0))
        entity_group = EntityGroup(label=label, values=values)
        group.setData(0, OBJECT_ROLE, entity_group)
        group.setData(0, EXPANSION_ROLE, tree_expansion_id_for(label, entity_group))
        style_tree_item(group)
        set_tree_item_icon(group, entity_group)
        parent.addChild(group)
        for value in values:
            group.addChild(self.item(self._label_for(value), value))


def _sample_is_inspectable(sample: Sample) -> bool:
    return bool(
        has_collection_data(sample)
        or sample.metadata
        or sample.warnings
    )


def project_group_tooltip(group: ProjectTreeGroup) -> str:
    lines = [group.display_name]
    if group.is_renamed:
        lines.append(f"Automatic name: {group.automatic_name}")
    if group.kind == "linked":
        lines.append(f"Linked data collections: {len(group.collection_sessions)}")
    lines.extend(f"{session.name}: {session.path}" for session in group.sessions)
    lines.extend(group.warnings)
    return "\n".join(lines)


def style_project_group_item(item: QTreeWidgetItem, group: ProjectTreeGroup) -> None:
    font = item.font(0)
    font.setWeight(QFont.Weight.DemiBold)
    item.setFont(0, font)
    item.setFont(1, font)
    palette = current_palette()
    item.setForeground(0, QBrush(QColor(palette.text_strong)))
    item.setBackground(0, QBrush(QColor(palette.surface_alt)))
    item.setBackground(1, QBrush(QColor(palette.surface_alt)))
    icon_name = PROJECT_GROUP_ICONS.get(group.kind, "folder-open")
    item.setIcon(0, themed_icon(icon_name))


def style_tree_item(item: QTreeWidgetItem) -> None:
    item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)


def set_tree_item_icon(item: QTreeWidgetItem, value: Any) -> None:
    icon_name = tree_icon_name_for(value)
    if icon_name is not None:
        item.setIcon(0, themed_icon(icon_name))


def tree_icon_name_for(value: Any) -> str | None:
    """Return the restrained entity icon for one project-tree node."""

    if isinstance(value, ProjectTreeGroup):
        return PROJECT_GROUP_ICONS.get(value.kind, "folder-open")
    if isinstance(value, Session):
        return (
            TAB_ICONS[TabKey.ATLAS]
            if value.kind == SessionKind.ATLAS_SCREENING
            else "folder-open"
        )
    if isinstance(value, Sample):
        return "folder-open"
    if isinstance(value, LinkedSampleGroup):
        return "layers"
    if isinstance(value, EntityGroup):
        return TREE_ENTITY_GROUP_ICONS.get(value.label)
    if isinstance(value, Atlas):
        return TAB_ICONS[TabKey.ATLAS]
    if isinstance(value, Overview):
        return TAB_ICONS[TabKey.OVERVIEW]
    if isinstance(value, SearchMap):
        return TAB_ICONS[TabKey.SEARCH_MAP]
    if isinstance(value, SearchTile):
        return TAB_ICONS[TabKey.SEARCH]
    if isinstance(value, BatchPosition):
        return TAB_ICONS[TabKey.BATCH_POSITION]
    if isinstance(value, TiltSeries):
        return TAB_ICONS[TabKey.TILT_SERIES]
    return None


def tree_expansion_id_for(label: str, value: Any) -> str:
    if isinstance(value, ProjectTreeGroup):
        return f"project:{value.key}"
    if isinstance(value, Session):
        return f"session:{session_key(value)}"
    if isinstance(value, Sample):
        return f"sample:{path_identity_key(value.path)}"
    if isinstance(value, Atlas):
        return f"atlas:{atlas_display_key(value)}"
    if isinstance(value, Overview | SearchMap | SearchTile | BatchPosition | TiltSeries):
        return f"{type(value).__name__}:{getattr(value, 'id', label)}"
    if isinstance(value, LinkedSampleGroup):
        sample_ids = ",".join(path_identity_key(sample.path) for sample in value.samples)
        return f"linked-sample:{sample_ids}"
    if isinstance(value, EntityGroup):
        child_ids = ",".join(getattr(child, "id", str(index)) for index, child in enumerate(value.values[:12]))
        return f"entity-group:{value.label}:{child_ids}"
    return f"label:{label}"


def tree_tag_for(value: Any, workspace: Workspace) -> str:
    if isinstance(value, (Sample, LinkedSampleGroup)):
        samples = value.samples if isinstance(value, LinkedSampleGroup) else [value]
        if any(has_collection_data(sample) for sample in samples):
            return str(len(workspace.entities(EntityKind.TILT_SERIES, value)))
        return ""
    if isinstance(value, Session):
        return SESSION_KIND_BADGES.get(value.kind.value, "?")
    return ""


def tree_tag_tooltip_for(value: Any, tag: str) -> str:
    if isinstance(value, (Sample, LinkedSampleGroup)):
        return f"{tag} tilt series" if tag else ""
    if isinstance(value, Session):
        return session_badge_tooltip(tag) if tag != "?" else "Unknown session type"
    return ""
