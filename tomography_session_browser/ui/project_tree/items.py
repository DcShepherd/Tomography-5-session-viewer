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
    display_samples,
    folds,
)
from tomography_session_browser.presentation.scope_identity import scope_key
from tomography_session_browser.presentation.tomogram_selection import AllTomograms, FolderTomograms, tree_row_destination, versions_in
from tomography_session_browser.presentation.scopes import EntityGroup, LinkedSampleGroup
from tomography_session_browser.presentation.workspace import EntityKind, Workspace, atlas_display_key
from tomography_session_browser.ui.icons import themed_icon
from tomography_session_browser.ui.navigation_icons import PROJECT_GROUP_ICONS, TAB_ICONS, TREE_ENTITY_GROUP_ICONS
from tomography_session_browser.ui.project_tree.widgets import EXPANSION_ROLE, OBJECT_ROLE, RECENT_PATH_ROLE, FOLDED_SESSION_ROLE, PROJECT_GROUP_ROLE
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
        self.processing_sources = workspace.processing_sources
        self.tilt_items_by_id: dict[str, list[QTreeWidgetItem]] = {}
        self.scope_aliases: dict[str, QTreeWidgetItem] = {}

    def group_item(self, group: ProjectTreeGroup) -> QTreeWidgetItem:
        """A project group's row, without its children."""

        badge = project_group_badge(group)
        item = QTreeWidgetItem([group.display_name, badge])
        item.setToolTip(0, project_group_tooltip(group, self._workspace))
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
        loaded = {path_identity_key(source.path) for source in (*self._workspace.sessions, *self._workspace.processing_sources)}
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
        samples = display_samples(session, self._workspace)
        if folds(session, self._workspace):
            self.scope_aliases[scope_key(session)] = session_item
            sample = samples[0]
            group = project_group_for_row(session_item)
            if group is not None:
                session_item.setData(0, PROJECT_GROUP_ROLE, group)
                self.scope_aliases[scope_key(group)] = session_item
            session_item.setData(0, FOLDED_SESSION_ROLE, session)
            session_item.setData(0, OBJECT_ROLE, sample)
            if sample.atlas:
                session_item.addChild(self.item('Atlas', sample.atlas))
            self._add_collection_groups(session_item, sample)
        elif samples:
            for sample in samples:
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
        self._add_tomograms(parent, holder)

    def _add_tomograms(self, parent: QTreeWidgetItem, holder: Session | Sample) -> None:
        selection = AllTomograms(holder)
        rows = self._workspace.tomograms(selection)
        if not rows:
            return
        category = self.item('Tomograms', selection)
        category.setText(1, str(len(rows)))
        self._say_what_opens(category, selection)
        parent.addChild(category)
        sources = [source for source in self.processing_sources if any(row.source_id == source.id for row in rows)]
        if len(sources) > 1:
            # A version of every folder has no row of its own: the category shows it.
            for version in versions_in(self._workspace, selection):
                self.scope_aliases[scope_key(AllTomograms(holder, version))] = category
            for source in sources:
                folder = FolderTomograms(holder, source.id)
                name = self._workspace.group_names.get('processing:' + source.id, source.name)
                item = self.item(f'{name} \u00b7 {source.pipeline}', folder)
                item.setToolTip(0, str(source.path))
                item.setText(1, str(len(self._workspace.tomograms(folder))))
                self._say_what_opens(item, folder)
                category.addChild(item)
                self._add_versions(item, folder)
        else:
            source = sources[0]
            self.scope_aliases[scope_key(FolderTomograms(holder, source.id))] = category
            self._add_versions(category, selection)
            for index in range(category.childCount()):
                child = category.child(index)
                value = child.data(0, OBJECT_ROLE)
                self.scope_aliases[scope_key(FolderTomograms(holder, source.id, value.version))] = child

    def _say_what_opens(self, item: QTreeWidgetItem, selection: AllTomograms | FolderTomograms) -> None:
        """Add the kind a category or folder row opens to its tooltip."""
        opens = tree_row_destination(self._workspace, selection)
        if isinstance(opens, AllTomograms | FolderTomograms) and opens.version:
            item.setToolTip(0, f'{item.toolTip(0)}\nOpens the {opens.version} reconstructions in the Tomograms tab.')

    def _add_versions(self, parent: QTreeWidgetItem, selection: AllTomograms | FolderTomograms) -> None:
        from dataclasses import replace
        for version in versions_in(self._workspace, selection):
            child = self.item(version, replace(selection, version=version))
            child.setText(1, str(sum(any(volume.version == version for volume in row.volumes)
                                   for row in self._workspace.tomograms(selection))))
            parent.addChild(child)

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


def project_group_for_row(item: QTreeWidgetItem) -> ProjectTreeGroup | None:
    value = item.data(0, PROJECT_GROUP_ROLE) or item.data(0, OBJECT_ROLE)
    return value if isinstance(value, ProjectTreeGroup) else None


def session_for_row(item: QTreeWidgetItem) -> Session | None:
    value = item.data(0, FOLDED_SESSION_ROLE) or item.data(0, OBJECT_ROLE)
    return value if isinstance(value, Session) else None


def project_group_tooltip(group: ProjectTreeGroup, workspace: Workspace) -> str:
    lines = [group.display_name]
    if group.is_renamed:
        lines.append(f"Automatic name: {group.automatic_name}")
    if group.kind == "linked":
        lines.append(f"Linked data collections: {len(group.collection_sessions)}")
    if group.processing_source is not None:
        lines.append(str(group.processing_source.path))
        count = len(workspace.tomograms(group))
        lines.append(f"{count} residual tomograms ({len(group.processing_source.tomograms)} in source)")
        lines.append("Residual tomograms; placed tomograms are under their acquisition samples.")
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
    icon_name = tree_icon_name_for(item.data(0, FOLDED_SESSION_ROLE) or value)
    if icon_name is not None:
        item.setIcon(0, themed_icon(icon_name))


def tree_icon_name_for(value: Any) -> str | None:
    """Return the restrained entity icon for one project-tree node."""

    if isinstance(value, AllTomograms | FolderTomograms):
        return 'volume-version' if value.version else ('folder-open' if isinstance(value, FolderTomograms) else 'volume')
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
    if isinstance(value, AllTomograms | FolderTomograms):
        return scope_key(value)
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
