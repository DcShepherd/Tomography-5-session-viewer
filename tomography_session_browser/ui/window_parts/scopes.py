"""ScopeQueries owns its window interaction responsibilities."""
from __future__ import annotations
from typing import (
    Any,
)
from pathlib import (
    Path,
)
from PySide6.QtCore import (
    QObject,
)
from PySide6.QtWidgets import (
    QMainWindow,
    QTreeWidgetItem,
)
from tomography_session_browser.ui.dashboard_host import (
    dashboard_cache_key,
    timeline_for_scope,
)
from tomography_session_browser.domain.enums import (
    SessionKind,
)
from tomography_session_browser.domain.display_names import (
    format_overview_display_name,
    format_search_tile_display_name,
)
from tomography_session_browser.domain.identity import (
    dedupe_entities,
    path_identity_key,
)
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
from tomography_session_browser.presentation.navigation_context import (
    NavigationContext,
)
from tomography_session_browser.presentation.workspace import (
    EntityKind,
    Scope,
)
from tomography_session_browser.services.markers.context import MarkerContext
from tomography_session_browser.services.entity_links import (
    batch_search_maps_with_shared_tilts,
    batch_tilt_series,
    search_map_batches_with_shared_tilts,
    search_map_tilt_series,
)
from tomography_session_browser.ui.viewer.models import AtlasCollectionOverlayOption
from tomography_session_browser.services.timeline_service import (
    SessionTimeline,
)
from tomography_session_browser.presentation.project_tree import (
    ProjectTreeGroup,
    session_key,
)
from tomography_session_browser.ui.project_tree.items import (
    ProjectTreeItems,
)
from tomography_session_browser.presentation.dashboard.scope_value import (
    DashboardScopeValue,
)
from tomography_session_browser.presentation.describe.context import (
    samples_for_context,
)
from tomography_session_browser.ui.project_tree.widgets import (
    OBJECT_ROLE,
)
from tomography_session_browser.presentation.dashboard.scope_value import (
    dashboard_scope_value,
)
from tomography_session_browser.presentation.dashboard.models import (
    DashboardEntityScope,
)
from tomography_session_browser.presentation.scopes import (
    EntityGroup,
    LinkedSampleGroup,
)

from tomography_session_browser.ui.window_parts.contracts import (
    WindowParts,
    WindowUi,
)
from tomography_session_browser.ui.window_parts.state import (
    WindowState,
)
from tomography_session_browser.presentation.tab_keys import TabKey

class ScopeQueries(QObject):
    def __init__(self, window: QMainWindow, state: WindowState, ui: WindowUi, parts: WindowParts) -> None:
        super().__init__(window)
        self.window, self.state, self.ui, self.parts = window, state, ui, parts

    def scope_tab_counts(self) -> dict[str, int]:
        context = self.parts.scopes.tab_count_context()
        counts = {
            TabKey.ATLAS: len(self.state.review.workspace.entities(EntityKind.ATLAS, context)) if self.state.review.workspace.sessions else 0,
            TabKey.OVERVIEW: len(self.state.review.workspace.entities(EntityKind.OVERVIEW, context)) if self.state.review.workspace.sessions else 0,
            TabKey.SEARCH_MAP: len(self.state.review.workspace.entities(EntityKind.SEARCH_MAP, context)) if self.state.review.workspace.sessions else 0,
            TabKey.SEARCH: len(self.state.review.workspace.entities(EntityKind.SEARCH_TILE, context)) if self.state.review.workspace.sessions else 0,
            TabKey.BATCH_POSITION: len(self.state.review.workspace.entities(EntityKind.BATCH_POSITION, context)) if self.state.review.workspace.sessions else 0,
            TabKey.TILT_SERIES: len(self.state.review.workspace.entities(EntityKind.TILT_SERIES, context)) if self.state.review.workspace.sessions else 0,
        }
        return counts


    def tab_count_context(self) -> Scope:
        """Return the broad scope that should drive top-tab counts.

        Category nodes such as ``Overviews`` or ``Tilt series`` carry a narrow
        ``EntityGroup`` value so they can navigate directly to the matching
        tab. The tab badges, however, should describe the parent
        data-collection scope. Resolve that scope from the selected tree item
        first, falling back to the active context for programmatic navigation.
        """

        has_tree_scope, context = self.parts.scopes.tree_item_scope_context(self.ui.tree.currentItem())
        if has_tree_scope:
            return context
        if isinstance(self.state.review.scope, EntityGroup):
            context = self.parts.scopes.context_for_entity_group(self.state.review.scope)
            if context is not None:
                return context
        return self.state.review.scope


    def viewer_tab_context(self) -> Scope:
        """Return the scope used to populate entity lists in viewer tabs.

        The tab badges already use the current project-tree scope. Keep the
        list payloads on that same scope whenever a tree-level selection is
        being preserved, so passive preview loading or stale list selections
        cannot narrow a linked root to its first sample.
        """

        has_tree_scope, context = self.parts.scopes.tree_item_scope_context(self.ui.tree.currentItem())
        if isinstance(context, ProjectTreeGroup):
            return context
        if has_tree_scope and self.state.review.preserve_tree_root_context:
            return context
        if isinstance(self.state.review.scope, EntityGroup):
            context = self.parts.scopes.context_for_entity_group(self.state.review.scope)
            if context is not None:
                return context
        return self.state.review.scope


    def report_default_scope_value(self) -> Scope:
        item = self.ui.tree.currentItem()
        selected_session: Session | None = None
        while item is not None:
            value = item.data(0, OBJECT_ROLE)
            if isinstance(value, ProjectTreeGroup):
                return value
            # A linked sample is one grid, not its whole project group.
            if isinstance(value, Sample | LinkedSampleGroup):
                return value
            if isinstance(value, Session):
                selected_session = value
                break
            item = item.parent()
        if selected_session is not None:
            return selected_session
        return None


    def project_groups(self) -> list[ProjectTreeGroup]:
        return list(self.state.review.workspace.project_groups)


    def session_for_path(self, path: str | Path) -> Session | None:
        key = path_identity_key(path)
        for session in self.state.review.workspace.sessions:
            if session_key(session) == key or path_identity_key(session.path) == key:
                return session
        return None


    def project_group_for_session(self, session: Session) -> ProjectTreeGroup | None:
        for group in self.parts.scopes.project_groups():
            if any(candidate is session for candidate in group.sessions):
                return group
        return None


    def populate_tree(self) -> None:
        self.parts.project_panel.populate(
            ProjectTreeItems(self.state.review.workspace, self.parts.scopes.label_for),
            self.parts.scopes.project_groups(),
            session_count=len(self.state.review.workspace.sessions),
            current_key=self.parts.scopes.selected_project_group_key(),
            recent_paths=self.state.review.settings.recent_sessions,
            highlighted_tilt_series_id=self.state.dashboard.dashboard_highlighted_tilt_series_id,
        )


    def selected_project_group_key(self) -> str | None:
        if isinstance(self.state.review.scope, ProjectTreeGroup):
            return self.state.review.scope.key
        item = self.ui.tree.currentItem()
        while item is not None:
            value = item.data(0, OBJECT_ROLE)
            if isinstance(value, ProjectTreeGroup):
                return value.key
            item = item.parent()
        return None


    def label_for(self, value: Any) -> str:
        if isinstance(value, ProjectTreeGroup):
            return value.display_name
        if isinstance(value, LinkedSampleGroup):
            return value.label
        if isinstance(value, Session | Sample):
            return value.name
        if isinstance(value, EntityGroup):
            return value.label
        if isinstance(value, Overview):
            return format_overview_display_name(value.name)
        if isinstance(value, SearchMap | TiltSeries):
            return value.name
        if isinstance(value, SearchTile):
            return format_search_tile_display_name(value.name, value.search_map_name)
        if isinstance(value, BatchPosition):
            return value.name or value.id
        if isinstance(value, Atlas):
            return self.state.review.workspace.viewer_atlas_label(value)
        return str(value)


    def dashboard_scope_cache_key(self, scope_value: DashboardScopeValue) -> str:
        return dashboard_cache_key(scope_value)


    def dashboard_scope_value(self) -> DashboardScopeValue:
        if isinstance(self.state.dashboard.dashboard_focus_value, SearchMap | BatchPosition) and self.parts.scopes.dashboard_focus_matches_tree():
            return self.parts.scopes.dashboard_scope_for_focused_entity(self.state.dashboard.dashboard_focus_value)
        has_tree_scope, tree_context = self.parts.scopes.tree_item_scope_context(self.ui.tree.currentItem())
        if (
            has_tree_scope
            and (
                self.state.dashboard.dashboard_scope_prefers_tree
                or self.state.dashboard.dashboard_focus_value is not None
            )
        ):
            return self.parts.scopes.dashboard_scope_value_for_context(tree_context)
        return self.parts.scopes.dashboard_scope_value_for_context(self.state.review.scope)


    def dashboard_scope_for_focused_entity(self, value: SearchMap | BatchPosition) -> DashboardEntityScope:
        context = self.parts.scopes.context_for_object(value)
        samples = samples_for_context(context)
        sample_groups = [(sample.name, [sample]) for sample in samples]
        if isinstance(value, SearchMap):
            batches = search_map_batches_with_shared_tilts(
                value, self.state.review.workspace.entities(EntityKind.BATCH_POSITION, context)
            )
            tilts = search_map_tilt_series(value, self.state.review.workspace.entities(EntityKind.TILT_SERIES, context), batches)
            return DashboardEntityScope(
                source=value,
                search_maps=[value],
                batch_positions=batches,
                tilt_series=tilts,
                samples=samples,
                sample_groups=sample_groups,
                title=value.name or value.id,
                path=str(value.mrc_path or value.image_path or value.xml_path or ""),
                kind="search map",
            )
        batches = [value]
        tilts = batch_tilt_series(value, self.state.review.workspace.entities(EntityKind.TILT_SERIES, context))
        return DashboardEntityScope(
            source=value,
            search_maps=batch_search_maps_with_shared_tilts(
                value, self.state.review.workspace.entities(EntityKind.SEARCH_MAP, context)
            ),
            batch_positions=batches,
            tilt_series=tilts,
            samples=samples,
            sample_groups=sample_groups,
            title=value.name or value.id,
            path=str(value.search_image_path or value.tracking_image_path or value.exposure_image_path or ""),
            kind="batch position",
        )


    def dashboard_scope_value_for_context(self, scope: Scope) -> DashboardScopeValue:
        return dashboard_scope_value(self.state.review.workspace, scope, self.state.review.workspace.viewer_atlas_label)


    def dashboard_focus_matches_tree(self) -> bool:
        """Return true only when the project tree itself selected the focus.

        Viewer-tab/list selections are intentionally separate from Session
        Summary scope. A tilt series should narrow the dashboard only when the
        corresponding project-tree item is the active tree selection; otherwise
        a stale list-level tilt selection can leak into broader parent scopes.
        """

        item = self.ui.tree.currentItem()
        if item is None:
            return False
        return item.data(0, OBJECT_ROLE) is self.state.dashboard.dashboard_focus_value


    def build_timeline_for_scope(self, scope_value: DashboardScopeValue) -> SessionTimeline | None:
        return timeline_for_scope(scope_value)


    def tree_item_scope_context(self, item: QTreeWidgetItem | None) -> tuple[bool, Scope]:
        current = item
        while current is not None:
            value = current.data(0, OBJECT_ROLE)
            if isinstance(value, list):
                return True, None
            if isinstance(value, ProjectTreeGroup):
                return True, value
            if isinstance(value, Session | Sample | LinkedSampleGroup):
                return True, value
            current = current.parent()
        return False, None


    def context_for_entity_group(self, group: EntityGroup) -> Session | Sample | LinkedSampleGroup | ProjectTreeGroup | None:
        for value in group.values:
            context = self.parts.scopes.context_for_object(value)
            if context is not None:
                return context
        return None


    def context_for_object(self, value: Any) -> Session | Sample | LinkedSampleGroup | ProjectTreeGroup | None:
        return NavigationContext(self.state.review.workspace, self.state.review.scope, value).context_for_object(value)


    def same_context(self, left: Any, right: Any) -> bool:
        if left is right:
            return True
        if isinstance(left, LinkedSampleGroup) and isinstance(right, LinkedSampleGroup):
            return len(left.samples) == len(right.samples) and all(
                left_sample is right_sample for left_sample, right_sample in zip(left.samples, right.samples)
            )
        if isinstance(left, ProjectTreeGroup) and isinstance(right, ProjectTreeGroup):
            return left.key == right.key
        return False


    def atlas_collection_members(
        self,
        atlas: Atlas,
    ) -> list[Sample]:
        return list(self.state.review.workspace.members_for_id(atlas.id))


    def session_for_sample_identity(
        self,
        sample: Sample,
    ) -> Session | None:
        return next(
            (
                session
                for session in self.state.review.workspace.sessions
                if any(candidate is sample for candidate in session.samples)
            ),
            None,
        )


    def atlas_collection_samples_for_session(
        self,
        atlas: Atlas,
        session: Session,
    ) -> list[Sample]:
        members = self.parts.scopes.atlas_collection_members(atlas)
        return [
            sample
            for sample in members
            if any(candidate is sample for candidate in session.samples)
        ]


    def atlas_collection_sessions(
        self,
        atlas: Atlas,
    ) -> list[Session]:
        sessions: list[Session] = []
        for session in self.state.review.workspace.sessions:
            if session.kind == SessionKind.ATLAS_SCREENING:
                continue
            samples = self.parts.scopes.atlas_collection_samples_for_session(
                atlas,
                session,
            )
            if any(sample.batch_positions for sample in samples) or (
                session.batch_positions
                and self.parts.scopes.session_level_collection_entities_belong_to_atlas(
                    session,
                    samples,
                )
            ):
                sessions.append(session)
        return sessions


    @staticmethod
    def session_level_collection_entities_belong_to_atlas(
        session: Session,
        atlas_members: list[Sample],
    ) -> bool:
        collection_samples = [
            sample
            for sample in session.samples
            if bool(sample.overviews or sample.search_maps or sample.search_tiles or sample.batch_positions or sample.tilt_series)
        ]
        return bool(atlas_members) and all(
            any(sample is member for member in atlas_members)
            for sample in collection_samples
        )


    def atlas_collection_sessions_for_scope(
        self,
        atlas: Atlas,
        context: Scope,
    ) -> list[Session]:
        available = self.parts.scopes.atlas_collection_sessions(atlas)
        if isinstance(context, Session):
            return [context] if any(context is value for value in available) else available
        if isinstance(context, Sample):
            parent = self.parts.scopes.session_for_sample_identity(context)
            return (
                [parent]
                if parent is not None
                and any(parent is value for value in available)
                else available
            )
        if isinstance(context, LinkedSampleGroup):
            return [
                session
                for session in available
                if any(
                    any(candidate is sample for candidate in session.samples)
                    for sample in context.samples
                )
            ]
        if isinstance(context, ProjectTreeGroup):
            return [
                session
                for session in available
                if any(
                    session is candidate
                    for candidate in context.collection_sessions
                )
            ]
        return available


    def atlas_collection_scope_key(self, context: Scope) -> str:
        if isinstance(context, ProjectTreeGroup):
            return f"project:{context.key}"
        if isinstance(context, Session):
            return f"session:{session_key(context)}"
        if isinstance(context, Sample):
            return f"sample:{path_identity_key(context.path)}"
        if isinstance(context, LinkedSampleGroup):
            member_keys = sorted(
                path_identity_key(sample.path)
                for sample in context.samples
            )
            return "linked-sample:" + "|".join(member_keys)
        return "project:all"


    def atlas_collection_entities_for_session(
        self,
        atlas: Atlas,
        session: Session,
        context: Scope,
    ) -> tuple[list[BatchPosition], list[TiltSeries]]:
        samples = self.parts.scopes.atlas_collection_samples_for_session(
            atlas,
            session,
        )
        if isinstance(context, Sample) and any(
            context is sample for sample in samples
        ):
            samples = [context]
        elif isinstance(context, LinkedSampleGroup):
            samples = [
                sample
                for sample in samples
                if any(
                    sample is candidate
                    for candidate in context.samples
                )
            ]
        batches = dedupe_entities(
            batch
            for sample in samples
            for batch in sample.batch_positions
        )
        tilts = dedupe_entities(
            tilt
            for sample in samples
            for tilt in sample.tilt_series
        )
        if self.parts.scopes.session_level_collection_entities_belong_to_atlas(
            session,
            samples,
        ):
            batches = dedupe_entities(
                [*batches, *session.batch_positions]
            )
            tilts = dedupe_entities(
                [*tilts, *session.tilt_series]
            )
        return batches, tilts


    def atlas_collection_overlay_options(
        self,
        atlas: Atlas,
        context: Scope,
    ) -> list[AtlasCollectionOverlayOption]:
        sessions = self.parts.scopes.atlas_collection_sessions_for_scope(
            atlas,
            context,
        )
        scope_key = self.parts.scopes.atlas_collection_scope_key(context)
        name_counts: dict[str, int] = {}
        for session in sessions:
            name_counts[session.name] = name_counts.get(session.name, 0) + 1
        options: list[AtlasCollectionOverlayOption] = []
        for session in sessions:
            collection_key = session_key(session)
            batches, _tilts = self.parts.scopes.atlas_collection_entities_for_session(
                atlas,
                session,
                context,
            )
            label = session.name
            if name_counts.get(session.name, 0) > 1:
                label = f"{label} — {session.path}"
            options.append(
                AtlasCollectionOverlayOption(
                    key=collection_key,
                    label=f"{label} ({len(batches)})",
                    visible=self.state.viewer.atlas_collection_visibility.get(
                        (scope_key, collection_key),
                        True,
                    ),
                )
            )
        return options


    def atlas_marker_context_for_collection_scope(
        self,
        atlas: Atlas,
        base: MarkerContext,
        context: Scope,
    ) -> MarkerContext:
        available = self.parts.scopes.atlas_collection_sessions(atlas)
        if not available:
            return base
        scope_sessions = self.parts.scopes.atlas_collection_sessions_for_scope(
            atlas,
            context,
        )
        scope_key = self.parts.scopes.atlas_collection_scope_key(context)
        visible_sessions = [
            session
            for session in scope_sessions
            if self.state.viewer.atlas_collection_visibility.get(
                (scope_key, session_key(session)),
                True,
            )
        ]
        visible_batches: list[BatchPosition] = []
        visible_tilts: list[TiltSeries] = []
        for session in visible_sessions:
            batches, tilts = self.parts.scopes.atlas_collection_entities_for_session(
                atlas,
                session,
                context,
            )
            visible_batches.extend(batches)
            visible_tilts.extend(tilts)
        batch_object_ids = {id(value) for value in visible_batches}
        tilt_object_ids = {id(value) for value in visible_tilts}
        scoped_tilts = tuple(
            value
            for value in base.tilt_series
            if id(value) in tilt_object_ids
        )
        scoped_tilt_ids = {tilt.id for tilt in scoped_tilts}
        return MarkerContext(
            overviews=base.overviews,
            search_maps=base.search_maps,
            batch_positions=tuple(
                value
                for value in base.batch_positions
                if id(value) in batch_object_ids
            ),
            tilt_series=scoped_tilts,
            failed_tilt_ids=frozenset(
                tilt_id
                for tilt_id in base.failed_tilt_ids
                if tilt_id in scoped_tilt_ids
            ),
        )

