from __future__ import annotations
from tomography_session_browser.ui.window_parts import viewers

from tomography_session_browser.presentation.workspace import EntityKind

import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QTreeWidgetItem

from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.ui.project_tree.widgets import HIGHLIGHT_ROLE
from tomography_session_browser.domain.models import Atlas, BatchPosition, Overview, Sample, SearchMap, SearchTile, Session, TiltSeries
from tomography_session_browser.presentation.report_scope import ProjectReportGroup
from tomography_session_browser.reports import build_session_report
from tomography_session_browser.presentation.project_tree import build_project_tree_groups
from tomography_session_browser.services.session_linking import (
    ATLAS_MATCH_EXACT,
    ATLAS_MATCH_SUFFIX,
    _atlas_lookup,
    matching_atlas_sample,
    resolve_atlas_for_sample,
)
from tomography_session_browser.ui.project_tree.widgets import OBJECT_ROLE
from tomography_session_browser.ui.shell.chrome import TAB_LABELS
from tomography_session_browser.ui.viewer.viewer_tab import ViewerTab


def _atlas_session(name: str, root: str) -> Session:
    sample = Sample(
        id=f"{name}-sample",
        name="Sample1",
        path=Path(root) / name / "Sample1",
        atlas=Atlas(id=f"{name}-atlas"),
    )
    return Session(
        id=name,
        name=name,
        path=Path(root) / name,
        kind=SessionKind.ATLAS_SCREENING,
        samples=[sample],
    )


def _collection_session(name: str, root: str, atlas_id: str | None = None) -> Session:
    metadata = {"Session.dm": {"AtlasId": {"value": atlas_id}}} if atlas_id else {}
    sample = Sample(
        id=f"{name}-sample",
        name="Sample1",
        path=Path(root) / name / "Sample1",
        batch_positions=[BatchPosition(id=f"{name}-bp", name="Position 1")],
        metadata=metadata,
    )
    return Session(
        id=name,
        name=name,
        path=Path(root) / name,
        kind=SessionKind.MULTIGRID,
        samples=[sample],
    )


def test_atlas_links_multiple_collection_sessions_under_one_group() -> None:
    atlas = _atlas_session("Atlas_A", "C:/project")
    atlas_id = "Atlas_A/Sample1/Atlas/Atlas.dm"
    first = _collection_session("DataCollection_01", "C:/project", atlas_id)
    second = _collection_session("DataCollection_02", "C:/project", atlas_id)

    groups = build_project_tree_groups([atlas, first, second])

    assert len(groups) == 1
    assert groups[0].kind == "linked"
    assert groups[0].automatic_name == "Atlas_A"
    assert [session.name for session in groups[0].collection_sessions] == [
        "DataCollection_01",
        "DataCollection_02",
    ]


def test_atlas_batch_marker_scope_follows_selected_collection_session() -> None:
    from tomography_session_browser.services.markers.context import MarkerContext
    from tomography_session_browser.ui.main_window import MainWindow

    QApplication.instance() or QApplication([])
    atlas_session = _atlas_session("Atlas_A", "C:/project")
    atlas_id = "Atlas_A/Sample1/Atlas/Atlas.dm"
    first = _collection_session(
        "DataCollection_01",
        "C:/project",
        atlas_id,
    )
    second = _collection_session(
        "DataCollection_02",
        "C:/project",
        atlas_id,
    )
    atlas = atlas_session.samples[0].atlas
    assert atlas is not None
    first_batch = first.samples[0].batch_positions[0]
    second_batch = second.samples[0].batch_positions[0]
    root = build_project_tree_groups(
        [atlas_session, first, second]
    )[0]
    base = MarkerContext(
        batch_positions=(first_batch, second_batch),
    )
    window = MainWindow()
    window.parts.loading.set_loaded_sessions([atlas_session, first, second])

    root_context = window.parts.scopes.atlas_marker_context_for_collection_scope(
        atlas,
        base,
        root,
    )
    first_context = window.parts.scopes.atlas_marker_context_for_collection_scope(
        atlas,
        base,
        first,
    )

    assert root_context.batch_positions == (
        first_batch,
        second_batch,
    )
    assert first_context.batch_positions == (first_batch,)
    options = window.parts.scopes.atlas_collection_overlay_options(atlas, root)
    assert [option.label for option in options] == [
        "DataCollection_01 (1)",
        "DataCollection_02 (1)",
    ]


def test_linked_root_collection_toggle_does_not_hide_narrow_node_batches() -> None:
    from tomography_session_browser.services.markers.context import MarkerContext
    from tomography_session_browser.ui.main_window import MainWindow
    from tomography_session_browser.presentation.project_tree import session_key

    QApplication.instance() or QApplication([])
    atlas_session = _atlas_session("Atlas_A", "C:/project")
    atlas_id = "Atlas_A/Sample1/Atlas/Atlas.dm"
    first = _collection_session(
        "DataCollection_01",
        "C:/project",
        atlas_id,
    )
    second = _collection_session(
        "DataCollection_02",
        "C:/project",
        atlas_id,
    )
    atlas = atlas_session.samples[0].atlas
    assert atlas is not None
    first_batch = first.samples[0].batch_positions[0]
    second_batch = second.samples[0].batch_positions[0]
    root = build_project_tree_groups(
        [atlas_session, first, second]
    )[0]
    base = MarkerContext(
        batch_positions=(first_batch, second_batch),
    )
    window = MainWindow()
    window.parts.loading.set_loaded_sessions([atlas_session, first, second])
    window.state.viewer.atlas_collection_visibility[
        (
            window.parts.scopes.atlas_collection_scope_key(root),
            session_key(second),
        )
    ] = False

    root_context = window.parts.scopes.atlas_marker_context_for_collection_scope(
        atlas,
        base,
        root,
    )
    second_context = window.parts.scopes.atlas_marker_context_for_collection_scope(
        atlas,
        base,
        second,
    )

    assert root_context.batch_positions == (first_batch,)
    assert second_context.batch_positions == (second_batch,)


def test_session_level_batch_fallback_is_kept_when_scope_is_unambiguous() -> None:
    from tomography_session_browser.services.markers.context import MarkerContext
    from tomography_session_browser.ui.main_window import MainWindow

    QApplication.instance() or QApplication([])
    atlas_session = _atlas_session("Atlas_A", "C:/project")
    collection = _collection_session(
        "DataCollection_01",
        "C:/project",
        "Atlas_A/Sample1/Atlas/Atlas.dm",
    )
    sample_batch = collection.samples[0].batch_positions.pop()
    collection.batch_positions = [sample_batch]
    atlas = atlas_session.samples[0].atlas
    assert atlas is not None
    window = MainWindow()
    window.parts.loading.set_loaded_sessions([atlas_session, collection])

    scoped = window.parts.scopes.atlas_marker_context_for_collection_scope(
        atlas,
        MarkerContext(batch_positions=(sample_batch,)),
        collection,
    )

    assert scoped.batch_positions == (sample_batch,)
    assert window.parts.scopes.atlas_collection_overlay_options(
        atlas,
        collection,
    )[0].label == "DataCollection_01 (1)"


def test_rendered_atlas_marker_provider_uses_tree_collection_scope(
    monkeypatch,
) -> None:
    from tomography_session_browser.ui.main_window import MainWindow

    QApplication.instance() or QApplication([])
    atlas_session = _atlas_session("Atlas_A", "C:/project")
    atlas_id = "Atlas_A/Sample1/Atlas/Atlas.dm"
    first = _collection_session(
        "DataCollection_01",
        "C:/project",
        atlas_id,
    )
    second = _collection_session(
        "DataCollection_02",
        "C:/project",
        atlas_id,
    )
    atlas = atlas_session.samples[0].atlas
    assert atlas is not None
    captured = []
    monkeypatch.setattr(
        viewers,
        "atlas_lod_markers",
        lambda _atlas, context: captured.append(context) or [],
    )
    window = MainWindow()
    window.parts.loading.set_loaded_sessions([atlas_session, first, second], scope=first)
    window.parts.viewers.render_viewer_tabs()

    window.ui.viewer_tabs['Atlas'].parts.tab_selection.markers_for(atlas)

    assert len(captured) == 1
    assert captured[0].batch_positions == tuple(
        first.samples[0].batch_positions
    )


def test_unrelated_collection_sessions_remain_separate() -> None:
    first = _collection_session("DataCollection_A", "C:/project")
    second = _collection_session("DataCollection_B", "C:/project")

    groups = build_project_tree_groups([first, second])

    assert [group.display_name for group in groups] == ["DataCollection_A", "DataCollection_B"]
    assert [group.kind for group in groups] == ["collection", "collection"]


def test_later_atlas_regroups_existing_collection_session() -> None:
    atlas_id = "Atlas_C/Sample1/Atlas/Atlas.dm"
    collection = _collection_session("DataCollection_03", "C:/project", atlas_id)

    assert build_project_tree_groups([collection])[0].kind == "collection"

    atlas = _atlas_session("Atlas_C", "C:/project")
    groups = build_project_tree_groups([collection, atlas])

    assert len(groups) == 1
    assert groups[0].kind == "linked"
    assert groups[0].automatic_name == "DataCollection_03"
    assert groups[0].atlas_session is atlas
    assert groups[0].collection_sessions == [collection]


def test_ambiguous_collection_link_stays_unresolved() -> None:
    first_atlas = _atlas_session("Atlas_D", "C:/project_a")
    second_atlas = _atlas_session("Atlas_D", "C:/project_b")
    collection = _collection_session(
        "DataCollection_04",
        "C:/collection",
        "Atlas_D/Sample1/Atlas/Atlas.dm",
    )

    groups = build_project_tree_groups([first_atlas, second_atlas, collection])

    assert [group.kind for group in groups] == ["atlas", "atlas", "unresolved"]
    assert "Ambiguous atlas link" in groups[-1].warnings[0]


def test_shared_relative_suffix_across_distinct_atlas_samples_stays_unresolved() -> None:
    """Two screening sessions reachable by the same relative AtlasId suffix.

    Regression for N3: the resolver used to return whichever candidate the
    length-sorted lookup happened to yield first, silently producing a linked
    group bound to an arbitrary atlas.
    """

    first_atlas = _atlas_session("Atlas_D", "C:/project_a")
    first_atlas.samples[0].id = "atlas-a-sample"
    second_atlas = _atlas_session("Atlas_D", "C:/project_b")
    second_atlas.samples[0].id = "atlas-b-sample"
    collection = _collection_session(
        "DataCollection_06",
        "C:/collection",
        "Atlas_D/Sample1/Atlas/Atlas.dm",
    )

    resolution = resolve_atlas_for_sample(
        collection.samples[0],
        _atlas_lookup([first_atlas, second_atlas]),
    )

    assert resolution.ambiguous is True
    assert resolution.sample is None
    assert resolution.linked is False
    assert {candidate.id for candidate in resolution.candidates} == {
        "atlas-a-sample",
        "atlas-b-sample",
    }

    groups = build_project_tree_groups([first_atlas, second_atlas, collection])

    assert [group.kind for group in groups] == ["atlas", "atlas", "unresolved"]
    warning = groups[-1].warnings[0]
    assert "Ambiguous atlas link" in warning
    # Both same-named candidates must remain distinguishable in the warning.
    assert "project_a" in warning
    assert "project_b" in warning


def test_exact_atlas_path_wins_over_a_competing_relative_suffix() -> None:
    """An exact normalised path is decisive even when a suffix also matches."""

    exact_atlas = _atlas_session("Atlas_F", "C:/project_a")
    exact_atlas.samples[0].id = "atlas-exact"
    suffix_atlas = _atlas_session("Atlas_F", "C:/project_b")
    suffix_atlas.samples[0].id = "atlas-suffix"
    collection = _collection_session(
        "DataCollection_07",
        "C:/collection",
        "C:/project_a/Atlas_F/Sample1/Atlas/Atlas.dm",
    )

    resolution = resolve_atlas_for_sample(
        collection.samples[0],
        _atlas_lookup([exact_atlas, suffix_atlas]),
    )

    assert resolution.linked is True
    assert resolution.sample is exact_atlas.samples[0]
    assert resolution.method == ATLAS_MATCH_EXACT

    groups = build_project_tree_groups([exact_atlas, suffix_atlas, collection])

    assert [group.kind for group in groups] == ["linked", "atlas"]
    assert groups[0].atlas_session is exact_atlas


def test_exact_atlas_path_survives_same_sample_ids_in_different_roots() -> None:
    """Grouping must not collapse back to the non-unique parser sample ID."""

    exact_atlas = _atlas_session("Atlas_H", "C:/project_a")
    competing_atlas = _atlas_session("Atlas_H", "C:/project_b")
    collection = _collection_session(
        "DataCollection_09",
        "C:/collection",
        "C:/project_a/Atlas_H/Sample1/Atlas/Atlas.dm",
    )

    assert exact_atlas.samples[0].id == competing_atlas.samples[0].id

    groups = build_project_tree_groups([exact_atlas, competing_atlas, collection])

    assert [group.kind for group in groups] == ["linked", "atlas"]
    assert groups[0].atlas_session is exact_atlas
    assert groups[0].collection_sessions == [collection]


def test_single_relative_suffix_match_still_links() -> None:
    """The conservative change must not break ordinary relative AtlasId links."""

    atlas = _atlas_session("Atlas_G", "C:/project")
    collection = _collection_session(
        "DataCollection_08",
        "C:/collection",
        "Atlas_G/Sample1/Atlas/Atlas.dm",
    )

    resolution = resolve_atlas_for_sample(collection.samples[0], _atlas_lookup([atlas]))

    assert resolution.linked is True
    assert resolution.method == ATLAS_MATCH_SUFFIX

    groups = build_project_tree_groups([atlas, collection])

    assert [group.kind for group in groups] == ["linked"]


def test_ambiguous_atlas_link_does_not_become_a_weak_sample_index_group() -> None:
    """An unresolved collection must not be linked in through the weak fallback.

    Both collections carry sample folders that yield the same weak sample-index
    key, so a regression that promoted weak groups would rejoin them to an
    atlas.
    """

    first_atlas = _atlas_session("Atlas_H", "C:/project_a")
    first_atlas.samples[0].id = "atlas-h-a"
    second_atlas = _atlas_session("Atlas_H", "C:/project_b")
    second_atlas.samples[0].id = "atlas-h-b"
    collection = _collection_session(
        "DataCollection_09",
        "C:/collection",
        "Atlas_H/Sample1/Atlas/Atlas.dm",
    )

    groups = build_project_tree_groups([first_atlas, second_atlas, collection])

    assert [group.kind for group in groups] == ["atlas", "atlas", "unresolved"]
    for group in groups:
        if group.kind == "atlas":
            assert group.collection_sessions == []


def test_atlas_link_suffix_must_start_at_path_component_boundary() -> None:
    atlas = _atlas_session("Atlas_A", "C:/project")
    collection = _collection_session(
        "DataCollection_05",
        "C:/collection",
        "C:/archive/XAtlas_A/Sample1/Atlas/Atlas.dm",
    )

    assert matching_atlas_sample(collection.samples[0], _atlas_lookup([atlas])) is None


def test_custom_project_group_name_is_display_only() -> None:
    atlas = _atlas_session("Atlas_E", "C:/project")
    group = build_project_tree_groups(
        [atlas],
        custom_names={"session:c:/project/atlas_e": "Renamed review group"},
    )[0]

    assert group.display_name == "Renamed review group"
    assert group.automatic_name == "Atlas_E"
    assert atlas.name == "Atlas_E"


def test_report_accepts_project_group_display_names(tmp_path: Path) -> None:
    session = _collection_session("OriginalSession", str(tmp_path))
    output = tmp_path / "renamed-report.pdf"

    result = build_session_report(
        [session],
        output,
        include_thumbnails=False,
        project_title="Renamed Project",
        project_groups=[
            ProjectReportGroup(
                display_name="Renamed Project",
                automatic_name="OriginalSession",
                kind="unlinked",
                session_names=("OriginalSession",),
                session_paths=(str(session.path),),
            )
        ],
    )

    assert result.path == output
    assert result.page_count >= 1
    assert output.exists()


def test_project_tree_omits_redundant_linked_samples_node() -> None:
    from tomography_session_browser.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    atlas = _atlas_session("Atlas_Screening_Demo", "C:/project")
    collection = _collection_session(
        "Data_Collection_Demo",
        "C:/project",
        "Atlas_Screening_Demo/Sample1/Atlas/Atlas.dm",
    )
    window = MainWindow()
    window.parts.loading.set_loaded_sessions([atlas, collection])
    window.parts.scopes.populate_tree()
    app.processEvents()

    labels: list[str] = []

    def collect(item) -> None:
        labels.append(item.text(0))
        for index in range(item.childCount()):
            collect(item.child(index))

    for index in range(window.ui.tree.topLevelItemCount()):
        collect(window.ui.tree.topLevelItem(index))

    assert window.ui.tree.topLevelItem(0).text(0) == "Data_Collection_Demo"
    assert window.ui.tree.topLevelItem(0).text(1) == "LS"
    assert window.ui.tree.topLevelItem(0).toolTip(1) == "Linked session — 1 data collection session"
    assert "Linked samples" not in labels


def test_linked_root_tab_activation_keeps_aggregate_viewer_lists(monkeypatch, tmp_path: Path) -> None:
    from tomography_session_browser.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    atlas_session = Session(
        id="atlas-session",
        name="Atlas_Screening_Demo",
        path=tmp_path / "Atlas_Screening_Demo",
        kind=SessionKind.ATLAS_SCREENING,
        samples=[
            Sample(
                id="atlas-alpha",
                name="Atlas sample alpha",
                path=tmp_path / "Atlas_Screening_Demo" / "Sample1",
                atlas=Atlas(id="atlas-alpha"),
            ),
            Sample(
                id="atlas-rubisco",
                name="Atlas sample beta",
                path=tmp_path / "Atlas_Screening_Demo" / "Sample2",
                atlas=Atlas(id="atlas-rubisco"),
            ),
        ],
    )
    collection_samples: list[Sample] = []
    for index, atlas_sample in enumerate(atlas_session.samples, start=1):
        search_map = SearchMap(id=f"sm-{index}", name=f"{atlas_sample.name}_SearchMap")
        overview = Overview(
            id=f"ov-{index}",
            name=f"{atlas_sample.name}_Overview",
            image_path=tmp_path / f"overview-{index}.mrc",
        )
        batch = BatchPosition(
            id=f"bp-{index}",
            name=f"{atlas_sample.name}_Position",
            linked_search_map_id=search_map.id,
            linked_overview_id=overview.id,
        )
        tilt = TiltSeries(
            id=f"ts-{index}",
            name=f"{atlas_sample.name}_Tilt",
            mrc_path=tmp_path / f"tilt-{index}.mrc",
            linked_batch_position_id=batch.id,
        )
        search_tile = SearchTile(
            id=f"tile-{index}",
            name=f"{atlas_sample.name}_Search",
            search_map_id=search_map.id,
            search_map_name=search_map.name,
            batch_position_id=batch.id,
            batch_position_name=batch.name,
            linked_tilt_series_ids=[tilt.id],
        )
        search_map.linked_batch_position_ids.append(batch.id)
        search_map.linked_tilt_series_ids.append(tilt.id)
        batch.linked_tilt_series_ids.append(tilt.id)
        collection_samples.append(
            Sample(
                id=f"collection-{index}",
                name=atlas_sample.name,
                path=tmp_path / "Data_Collection_Demo" / f"Sample{index}",
                overviews=[overview],
                search_maps=[search_map],
                search_tiles=[search_tile],
                batch_positions=[batch],
                tilt_series=[tilt],
                metadata={"Session.dm": {"AtlasId": {"value": str(atlas_sample.path / "Atlas" / "Atlas.dm")}}},
            )
        )
    collection_session = Session(
        id="collection-session",
        name="Data_Collection_Demo",
        path=tmp_path / "Data_Collection_Demo",
        kind=SessionKind.MULTIGRID,
        samples=collection_samples,
    )
    window = MainWindow(save_settings=lambda _settings: None)
    monkeypatch.setattr(
        ViewerTab,
        "_load_value",
        lambda self, value, _slice_index: setattr(self.parts.tab_selection, 'current_value', value),
    )
    window.parts.loading.set_loaded_sessions([atlas_session, collection_session])
    window.parts.scopes.populate_tree()
    linked_group = window.ui.tree.topLevelItem(0).data(0, OBJECT_ROLE)

    window.parts.viewers.select_from_left_tree(linked_group, linked_group.display_name)
    app.processEvents()

    assert window.state.review.scope is linked_group
    assert window.ui.viewer_tabs["Atlas"].list.topLevelItemCount() == 2
    assert window.ui.viewer_tabs["Search map"].list.topLevelItemCount() == 2
    assert window.ui.viewer_tabs["Search"].list.topLevelItemCount() == 2
    assert window.ui.viewer_tabs["Batch position"].list.topLevelItemCount() == 2
    assert window.ui.viewer_tabs["Tilt series"].list.topLevelItemCount() == 2

    window.state.review.scope = linked_group
    window.state.review.preserve_tree_root_context = False
    window.parts.viewers.render_viewer_tabs()
    window.ui.tabs.setCurrentIndex(TAB_LABELS.index("Atlas"))
    app.processEvents()

    assert window.state.review.scope is linked_group
    assert window.ui.viewer_tabs["Atlas"].list.topLevelItemCount() == 2
    assert window.ui.viewer_tabs["Search map"].list.topLevelItemCount() == 2

    window.ui.tabs.setCurrentIndex(TAB_LABELS.index("Atlas"))
    for _ in range(4):  # the panel follows once the page has painted
        app.processEvents()

    assert window.state.review.scope is linked_group
    # The scope stays the group, but the panel describes the image on screen.
    displayed_atlas = window.ui.viewer_tabs['Atlas'].parts.tab_selection.current_value
    assert displayed_atlas is not None
    assert window.ui.context_panel._raw_text == window.parts.context.context_description(displayed_atlas)
    assert window.ui.viewer_tabs["Atlas"].list.topLevelItemCount() == 2
    assert window.ui.viewer_tabs["Search map"].list.topLevelItemCount() == 2
    assert window.ui.viewer_tabs["Search"].list.topLevelItemCount() == 2
    assert window.ui.viewer_tabs["Batch position"].list.topLevelItemCount() == 2
    assert window.ui.viewer_tabs["Tilt series"].list.topLevelItemCount() == 2

    window.ui.tabs.setCurrentIndex(TAB_LABELS.index("Search map"))
    for _ in range(4):  # the panel follows once the page has painted
        app.processEvents()

    assert window.state.review.scope is linked_group
    displayed_map = window.ui.viewer_tabs['Search map'].parts.tab_selection.current_value
    assert displayed_map is not None
    assert window.ui.context_panel._raw_text == window.parts.context.context_description(displayed_map)
    assert window.ui.viewer_tabs["Atlas"].list.topLevelItemCount() == 2
    assert window.ui.viewer_tabs["Search map"].list.topLevelItemCount() == 2
    assert window.ui.viewer_tabs["Search"].list.topLevelItemCount() == 2
    assert window.ui.viewer_tabs["Batch position"].list.topLevelItemCount() == 2
    assert window.ui.viewer_tabs["Tilt series"].list.topLevelItemCount() == 2

    selected_search_tile = collection_samples[1].search_tiles[0]
    window.parts.viewers.viewer_item_selected(selected_search_tile)
    app.processEvents()

    assert window.state.review.scope is linked_group
    assert selected_search_tile.name in window.ui.context_panel._raw_text

    first_linked_sample = window.parts.scopes.context_for_object(collection_samples[0].search_maps[0])
    window.state.review.scope = first_linked_sample
    window.state.review.preserve_tree_root_context = True
    window.parts.viewers.render_viewer_tabs()
    window.parts.chrome.update_tab_counts()

    assert "Atlas  2" == window.ui.tabs.tabText(TAB_LABELS.index("Atlas"))
    assert "Search maps  2" == window.ui.tabs.tabText(TAB_LABELS.index("Search map"))
    assert window.ui.viewer_tabs["Atlas"].list.topLevelItemCount() == 2
    assert window.ui.viewer_tabs["Search map"].list.topLevelItemCount() == 2
    assert window.ui.viewer_tabs["Search"].list.topLevelItemCount() == 2
    assert window.ui.viewer_tabs["Batch position"].list.topLevelItemCount() == 2
    assert window.ui.viewer_tabs["Tilt series"].list.topLevelItemCount() == 2


def test_project_tree_badges_distinguish_linked_atlas_and_collection_groups() -> None:
    from tomography_session_browser.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    linked_atlas = _atlas_session("Atlas_Screening_Demo", "C:/project")
    linked_collection = _collection_session(
        "Data_Collection_Demo",
        "C:/project",
        "Atlas_Screening_Demo/Sample1/Atlas/Atlas.dm",
    )
    atlas_only = _atlas_session("Standalone_Atlas", "C:/project")
    unlinked_collection = _collection_session("Unlinked_Dataset", "C:/project")
    window = MainWindow()
    window.parts.loading.set_loaded_sessions([linked_atlas, linked_collection, atlas_only, unlinked_collection])
    window.parts.scopes.populate_tree()
    app.processEvents()

    linked_item = _top_level_item(window, "Data_Collection_Demo")
    atlas_item = _top_level_item(window, "Standalone_Atlas")
    collection_item = _top_level_item(window, "Unlinked_Dataset")

    assert linked_item is not None
    assert atlas_item is not None
    assert collection_item is not None
    assert linked_item.text(1) == "LS"
    assert linked_item.toolTip(1).startswith("Linked session")
    assert atlas_item.text(1) == "AT"
    assert collection_item.text(1) == "DC"

    linked_group = window.parts.scopes.project_groups()[0]
    window.state.review.project_display_names[linked_group.key] = "Renamed linked session"
    window.parts.project.refresh_project_state_after_edit(window.state.review.workspace.sessions, select_group_key=linked_group.key)
    app.processEvents()

    renamed_item = _top_level_item(window, "Renamed linked session")
    assert renamed_item is not None
    assert renamed_item.text(1) == "LS"


def test_project_tree_preserves_expansion_state_across_rebuilds(monkeypatch) -> None:
    from tomography_session_browser.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    atlas = _atlas_session("Atlas_Screening_Demo", "C:/project")
    collection = _collection_session(
        "Data_Collection_Demo",
        "C:/project",
        "Atlas_Screening_Demo/Sample1/Atlas/Atlas.dm",
    )
    unrelated = _collection_session("DataCollection_B", "C:/project")
    extra = _collection_session("DataCollection_C", "C:/project")
    window = MainWindow()
    window.parts.loading.set_loaded_sessions([atlas, collection, unrelated])
    window.parts.scopes.populate_tree()
    app.processEvents()

    linked_item = _top_level_item(window, "Data_Collection_Demo")
    unrelated_item = _top_level_item(window, "DataCollection_B")
    assert linked_item is not None
    assert unrelated_item is not None
    assert not linked_item.isExpanded()
    assert not unrelated_item.isExpanded()

    linked_item.setExpanded(True)
    unrelated_item.setExpanded(False)
    window.parts.loading.set_loaded_sessions([*window.state.review.workspace.sessions, extra])
    window.parts.scopes.populate_tree()
    app.processEvents()

    linked_item = _top_level_item(window, "Data_Collection_Demo")
    unrelated_item = _top_level_item(window, "DataCollection_B")
    extra_item = _top_level_item(window, "DataCollection_C")
    assert linked_item is not None and linked_item.isExpanded()
    assert unrelated_item is not None and not unrelated_item.isExpanded()
    assert extra_item is not None and not extra_item.isExpanded()

    linked_group = window.parts.scopes.project_groups()[0]
    window.state.review.project_display_names[linked_group.key] = "Renamed linked group"
    window.parts.project.refresh_project_state_after_edit(window.state.review.workspace.sessions, select_group_key=linked_group.key)
    app.processEvents()

    renamed_item = _top_level_item(window, "Renamed linked group")
    assert renamed_item is not None and renamed_item.isExpanded()

    window.state.review.project_display_names.pop(linked_group.key, None)
    window.parts.project.refresh_project_state_after_edit(window.state.review.workspace.sessions, select_group_key=linked_group.key)
    app.processEvents()

    linked_item = _top_level_item(window, "Data_Collection_Demo")
    assert linked_item is not None and linked_item.isExpanded()

    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    window.parts.project.remove_session_from_project(extra)
    app.processEvents()

    linked_item = _top_level_item(window, "Data_Collection_Demo")
    unrelated_item = _top_level_item(window, "DataCollection_B")
    assert linked_item is not None and linked_item.isExpanded()
    assert unrelated_item is not None and not unrelated_item.isExpanded()
    assert _top_level_item(window, "DataCollection_C") is None


def test_collection_session_scope_includes_linked_atlas_without_broadening_counts() -> None:
    from tomography_session_browser.ui.main_window import MainWindow
    from tomography_session_browser.presentation.dashboard.dashboard import session_dashboard_model

    app = QApplication.instance() or QApplication([])
    atlas = _atlas_session("Atlas_Screening_Demo", "C:/project")
    collection = _collection_session(
        "Data_Collection_Demo",
        "C:/project",
        "Atlas_Screening_Demo/Sample1/Atlas/Atlas.dm",
    )
    unrelated = _collection_session("DataCollection_B", "C:/project")
    window = MainWindow()
    window.parts.loading.set_loaded_sessions([atlas, collection, unrelated], scope=collection)
    window.parts.viewers.render_viewer_tabs()
    window.parts.chrome.update_tab_counts()
    app.processEvents()

    linked_atlases = window.state.review.workspace.entities(EntityKind.ATLAS, collection)
    assert linked_atlases == [atlas.samples[0].atlas]
    assert window.ui.tabs.tabText(1) == "Atlas  1"
    assert window.ui.tabs.tabText(5) == "Batch positions  1"
    assert window.state.review.workspace.entities(EntityKind.BATCH_POSITION, collection) == collection.samples[0].batch_positions
    assert window.parts.scopes.context_for_object(linked_atlases[0]) is collection
    assert window.state.review.workspace.contains(window.parts.scopes.viewer_tab_context(), linked_atlases[0])
    model = session_dashboard_model(window.parts.scopes.dashboard_scope_value())
    assert _card_value(model, "Atlases") == 1
    assert _card_value(model, "Batch positions") == 1


def test_collection_context_panel_reports_linked_atlas() -> None:
    from tomography_session_browser.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    atlas = _atlas_session("Atlas_Screening_Demo", "C:/project")
    atlas.samples[0].name = "Screening grid slot 1"
    atlas.samples[0].atlas.image_path = Path("C:/project/Atlas_Screening_Demo/Sample1/Atlas/Atlas.mrc")
    collection = _collection_session(
        "Data_Collection_Demo",
        "C:/project",
        "Atlas_Screening_Demo/Sample1/Atlas/Atlas.dm",
    )
    collection.samples[0].name = "Specimen Alpha"
    window = MainWindow()
    window.parts.loading.set_loaded_sessions([atlas, collection], scope=collection)

    window.parts.context.set_context(collection.name, collection)
    app.processEvents()
    text = window.ui.context_panel.toPlainText()

    assert "Atlas: Linked atlas from screening session" in text
    assert "Sample: Specimen Alpha" in text
    assert "Sample: Screening grid slot 1" not in text
    assert "Path:" in text
    assert "Atlas_Screening_Demo" in text
    assert "Sample1" in text
    assert "Atlas.mrc" in text
    assert "No atlas associated" not in text


def test_collection_session_atlas_scope_includes_only_per_sample_atlas_id_matches() -> None:
    from tomography_session_browser.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    atlas = _atlas_session("Atlas_Screening_Demo", "C:/project")
    second_atlas = Atlas(id="atlas-screening-demo-atlas-2")
    atlas.samples.append(
        Sample(
            id="atlas-screening-demo-sample-2",
            name="Screening slot 2",
            path=Path("C:/project") / "Atlas_Screening_Demo" / "Sample2",
            atlas=second_atlas,
        )
    )
    collection = _collection_session(
        "Data_Collection_Demo",
        "C:/project",
        "Atlas_Screening_Demo/Sample2/Atlas/Atlas.dm",
    )
    collection.samples[0].name = "Specimen Beta"
    window = MainWindow()
    window.parts.loading.set_loaded_sessions([atlas, collection], scope=collection)
    app.processEvents()

    assert window.state.review.workspace.entities(EntityKind.ATLAS, collection) == [second_atlas]
    assert "Sample: Specimen Beta" in window.parts.context.context_description(collection)


def test_collection_context_panel_reports_missing_atlas_only_when_unresolved() -> None:
    from tomography_session_browser.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    collection = _collection_session("DataCollection_B", "C:/project")
    window = MainWindow()
    window.parts.loading.set_loaded_sessions([collection], scope=collection)

    window.parts.context.set_context(collection.name, collection)
    app.processEvents()

    assert "Atlas: No atlas associated" in window.ui.context_panel.toPlainText()


def test_multi_sample_linked_atlas_scope_does_not_build_atlas_sparkline() -> None:
    from tomography_session_browser.ui.main_window import MainWindow
    from tomography_session_browser.presentation.dashboard.dashboard import session_dashboard_model

    app = QApplication.instance() or QApplication([])
    atlas = _atlas_session("Atlas_Screening_Demo", "C:/project")
    collection = _collection_session(
        "Data_Collection_Demo",
        "C:/project",
        "Atlas_Screening_Demo/Sample1/Atlas/Atlas.dm",
    )
    collection.samples.append(
        Sample(
            id="data-collection-demo-sample-2",
            name="Sample2",
            path=Path("C:/project") / "Data_Collection_Demo" / "Sample2",
            batch_positions=[BatchPosition(id="data-collection-demo-bp-2", name="Position 2")],
        )
    )
    window = MainWindow()
    window.parts.loading.set_loaded_sessions([atlas, collection], scope=collection)
    app.processEvents()

    model = session_dashboard_model(window.parts.scopes.dashboard_scope_value())
    atlas_card = next(card for card in model.counts if card.label == "Atlases")

    assert atlas_card.value == 1
    assert atlas_card.sparkline == []
    assert _card_value(model, "Batch positions") == 2


def test_collection_sample_scope_includes_only_its_linked_atlas() -> None:
    from tomography_session_browser.ui.main_window import MainWindow
    from tomography_session_browser.presentation.dashboard.dashboard import session_dashboard_model

    app = QApplication.instance() or QApplication([])
    atlas = _atlas_session("Atlas_Screening_Demo", "C:/project")
    atlas.samples.append(
        Sample(
            id="atlas-screening-demo-sample-2",
            name="Sample2",
            path=Path("C:/project") / "Atlas_Screening_Demo" / "Sample2",
            atlas=Atlas(id="atlas-screening-demo-atlas-2"),
        )
    )
    first = _collection_session(
        "Data_Collection_Demo",
        "C:/project",
        "Atlas_Screening_Demo/Sample1/Atlas/Atlas.dm",
    )
    second = _collection_session(
        "Data_Collection_Demo_2",
        "C:/project",
        "Atlas_Screening_Demo/Sample2/Atlas/Atlas.dm",
    )
    window = MainWindow()
    window.parts.loading.set_loaded_sessions([atlas, first, second])
    sample = first.samples[0]
    window.state.review.scope = sample
    window.parts.viewers.render_viewer_tabs()
    window.parts.chrome.update_tab_counts()
    app.processEvents()

    linked_atlases = window.state.review.workspace.entities(EntityKind.ATLAS, sample)
    assert linked_atlases == [atlas.samples[0].atlas]
    assert window.ui.tabs.tabText(1) == "Atlas  1"
    assert window.ui.tabs.tabText(5) == "Batch positions  1"
    assert window.state.review.workspace.entities(EntityKind.BATCH_POSITION, sample) == sample.batch_positions
    assert window.parts.scopes.context_for_object(linked_atlases[0]) is sample
    model = session_dashboard_model(window.parts.scopes.dashboard_scope_value())
    assert _card_value(model, "Atlases") == 1
    assert _card_value(model, "Batch positions") == 1


def test_unrelated_collection_scope_keeps_empty_atlas_state() -> None:
    from tomography_session_browser.ui.main_window import MainWindow
    from tomography_session_browser.presentation.dashboard.dashboard import session_dashboard_model

    app = QApplication.instance() or QApplication([])
    collection = _collection_session("DataCollection_B", "C:/project")
    window = MainWindow()
    window.parts.loading.set_loaded_sessions([collection], scope=collection)
    window.parts.viewers.render_viewer_tabs()
    window.parts.chrome.update_tab_counts()
    app.processEvents()

    assert window.state.review.workspace.entities(EntityKind.ATLAS, collection) == []
    assert window.ui.tabs.tabText(1) == "Atlas  0"
    model = session_dashboard_model(window.parts.scopes.dashboard_scope_value())
    assert _card_value(model, "Atlases") == 0


def test_dashboard_scope_returns_to_parent_session_after_tilt_list_selection(tmp_path: Path) -> None:
    from tomography_session_browser.ui.main_window import MainWindow
    from tomography_session_browser.presentation.dashboard.dashboard import session_dashboard_model

    app = QApplication.instance() or QApplication([])
    collection = _collection_session("Alpha", str(tmp_path))
    tilts = [
        TiltSeries(id=f"alpha-ts-{index}", name=f"alpha_2_{index}", mrc_path=tmp_path / f"alpha_2_{index}.mrc")
        for index in range(1, 4)
    ]
    collection.samples[0].tilt_series = tilts
    collection.samples.append(
        Sample(
            id="alpha-sample-2",
            name="Sample2",
            path=tmp_path / "Sample2",
            tilt_series=[TiltSeries(id="alpha-ts-4", name="alpha_2_4", mrc_path=tmp_path / "alpha_2_4.mrc")],
        )
    )
    window = MainWindow()
    window.parts.loading.set_loaded_sessions([collection])
    window.parts.scopes.populate_tree()
    parent_item = _top_level_item(window, "Alpha")
    assert parent_item is not None

    window.ui.tree.setCurrentItem(parent_item)
    window.parts.project.tree_selection_changed(parent_item, None)
    app.processEvents()
    assert window.parts.scopes.dashboard_scope_value() is collection
    assert _card_value(session_dashboard_model(window.parts.scopes.dashboard_scope_value()), "Tilt series") == 4

    window.parts.viewers.viewer_item_selected(tilts[1])
    assert window.state.viewer.selection_state.selected_object_id == "alpha-ts-2"
    assert window.state.dashboard.tilt_viewer_selected_tilt_series_id == "alpha-ts-2"
    assert window.state.dashboard.dashboard_highlighted_tilt_series_id is None
    assert window.parts.scopes.dashboard_scope_value() is collection

    # Guard against legacy stale focus state: tilt-series selection should not
    # collapse the Session Summary scope down to a single tilt.
    window.state.dashboard.dashboard_focus_value = tilts[1]
    assert window.parts.scopes.dashboard_scope_value() is collection
    assert _card_value(session_dashboard_model(window.parts.scopes.dashboard_scope_value()), "Tilt series") == 4

    window.parts.dashboard.on_dashboard_sample_clicked(collection.samples[0].id)
    assert window.parts.scopes.dashboard_scope_value() is collection.samples[0]
    assert _card_value(session_dashboard_model(window.parts.scopes.dashboard_scope_value()), "Tilt series") == 3

    tilt_item = _tree_item_with_object(window, tilts[1])
    assert tilt_item is not None
    window.ui.tree.setCurrentItem(tilt_item)
    window.parts.project.tree_selection_changed(tilt_item, parent_item)
    app.processEvents()
    assert window.parts.scopes.dashboard_scope_value() is collection.samples[0]
    assert window.state.dashboard.dashboard_highlighted_tilt_series_id is None

    window.parts.project.project_tree_item_pressed(tilt_item, 0)

    assert window.state.dashboard.dashboard_highlighted_tilt_series_id == tilts[1].id
    assert window.state.dashboard.pending_project_tree_clear_tilt_id is None
    assert not window.state.dashboard.project_tree_tilt_click_timer.isActive()
    assert bool(tilt_item.data(0, HIGHLIGHT_ROLE))
    assert _card_value(session_dashboard_model(window.parts.scopes.dashboard_scope_value()), "Tilt series") == 3

    window.parts.project.project_tree_item_pressed(tilt_item, 0)
    assert window.state.dashboard.pending_project_tree_clear_tilt_id == tilts[1].id
    assert window.state.dashboard.dashboard_highlighted_tilt_series_id == tilts[1].id
    window.state.dashboard.project_tree_tilt_click_timer.stop()
    window.parts.project.apply_pending_project_tree_highlight_clear()
    assert window.state.dashboard.dashboard_highlighted_tilt_series_id is None
    assert not bool(tilt_item.data(0, HIGHLIGHT_ROLE))

    viewer = window.ui.viewer_tabs["Tilt series"]
    viewer.list_filter.setText("manual filter")
    window.parts.project.project_tree_item_pressed(tilt_item, 0)
    assert window.state.dashboard.dashboard_highlighted_tilt_series_id == tilts[1].id
    window.parts.project.project_tree_item_pressed(tilt_item, 0)
    assert window.state.dashboard.pending_project_tree_clear_tilt_id == tilts[1].id
    window.parts.project.project_tree_item_double_clicked(tilt_item, 0)
    app.processEvents()
    assert window.state.dashboard.pending_project_tree_clear_tilt_id is None
    assert not window.state.dashboard.project_tree_tilt_click_timer.isActive()
    assert window.ui.tabs.currentIndex() == TAB_LABELS.index("Tilt series")
    # S1 contract change: an incompatible filter is cleared on arrival rather
    # than leaving a single unhidden row behind a count of 0.
    assert viewer.list_filter.text() == ""
    assert viewer.list_count.text() == str(viewer.list.topLevelItemCount())
    assert window.state.dashboard.dashboard_highlighted_tilt_series_id == tilts[1].id
    assert bool(tilt_item.data(0, HIGHLIGHT_ROLE))

    other_tilt_item = _tree_item_with_object(window, tilts[2])
    assert other_tilt_item is not None
    window.parts.project.project_tree_item_pressed(other_tilt_item, 0)
    assert not bool(tilt_item.data(0, HIGHLIGHT_ROLE))
    assert bool(other_tilt_item.data(0, HIGHLIGHT_ROLE))

    window.ui.tree.setCurrentItem(parent_item)
    window.parts.project.tree_selection_changed(parent_item, tilt_item)
    scope_owner = window.parts.viewers.scope_preparation
    deadline = time.monotonic() + 30
    while (scope_owner.running is not None or scope_owner.pending is not None) and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.001)
    assert scope_owner.running is None and scope_owner.pending is None, "scope preparation did not finish"
    app.processEvents()
    assert window.parts.scopes.dashboard_scope_value() is collection
    assert window.state.dashboard.dashboard_highlighted_tilt_series_id is None
    assert not bool(other_tilt_item.data(0, HIGHLIGHT_ROLE))
    assert _card_value(session_dashboard_model(window.parts.scopes.dashboard_scope_value()), "Tilt series") == 4


def _card_value(model, label: str):
    for card in model.counts:
        if card.label == label:
            return card.value
    return None


def _top_level_item(window, label: str):
    for index in range(window.ui.tree.topLevelItemCount()):
        item = window.ui.tree.topLevelItem(index)
        if item.text(0) == label:
            return item
    return None


def _tree_item_with_object(window, value) -> QTreeWidgetItem | None:
    def visit(item: QTreeWidgetItem) -> QTreeWidgetItem | None:
        if item.data(0, OBJECT_ROLE) is value:
            return item
        for child_index in range(item.childCount()):
            found = visit(item.child(child_index))
            if found is not None:
                return found
        return None


    for index in range(window.ui.tree.topLevelItemCount()):
        found = visit(window.ui.tree.topLevelItem(index))
        if found is not None:
            return found
    return None


def test_removing_a_linked_session_asks_first(monkeypatch) -> None:
    """Removing a whole group asked; removing one of its sessions did not."""

    from PySide6.QtWidgets import QMessageBox
    from tomography_session_browser.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    atlas = _atlas_session("Atlas_Screening_Demo", "C:/project")
    collection = _collection_session("Data_Collection_Demo", "C:/project", "Atlas_Screening_Demo/Sample1/Atlas/Atlas.dm")
    window = MainWindow()
    asked = []
    try:
        window.parts.loading.set_loaded_sessions([atlas, collection])
        window.parts.scopes.populate_tree()

        def decline(*args, **kwargs):
            asked.append(args[2])
            return QMessageBox.StandardButton.No

        monkeypatch.setattr(QMessageBox, "question", decline)
        window.parts.project.remove_session_from_project(collection)
        assert asked and "Data_Collection_Demo" in asked[0]
        assert window.state.review.workspace.sessions == (atlas, collection)

        monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
        window.parts.project.remove_session_from_project(collection)
        app.processEvents()
        assert window.state.review.workspace.sessions == (atlas,)
    finally:
        window.close()


def test_a_group_name_survives_the_group_becoming_linked(monkeypatch) -> None:
    """Importing the collection changed the group key and dropped the name."""

    from PySide6.QtWidgets import QInputDialog
    from tomography_session_browser.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    atlas = _atlas_session("Atlas_Screening_Demo", "C:/project")
    collection = _collection_session("Data_Collection_Demo", "C:/project", "Atlas_Screening_Demo/Sample1/Atlas/Atlas.dm")
    window = MainWindow()
    try:
        window.parts.loading.set_loaded_sessions([atlas])
        window.parts.scopes.populate_tree()
        atlas_group = window.parts.scopes.project_groups()[0]
        assert atlas_group.kind == "atlas"

        monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("Grid project", True))
        window.parts.project.rename_project_group(atlas_group)
        assert window.parts.scopes.project_groups()[0].display_name == "Grid project"

        window.parts.project.refresh_project_state_after_edit([*window.state.review.workspace.sessions, collection])
        app.processEvents()
        linked = window.parts.scopes.project_groups()[0]
        assert linked.kind == "linked" and linked.key != atlas_group.key
        assert linked.display_name == "Grid project"
        assert _top_level_item(window, "Grid project") is not None

        window.parts.project.revert_project_group_name(linked)
        assert window.parts.scopes.project_groups()[0].display_name == linked.automatic_name
        assert window.state.review.project_display_names == {}
    finally:
        window.close()


def test_names_saved_under_the_old_group_key_still_apply() -> None:
    atlas = _atlas_session("Atlas_Screening_Demo", "C:/project")
    collection = _collection_session("Data_Collection_Demo", "C:/project", "Atlas_Screening_Demo/Sample1/Atlas/Atlas.dm")
    key = build_project_tree_groups([atlas, collection])[0].key

    groups = build_project_tree_groups([atlas, collection], {key: "Legacy name"})

    assert groups[0].display_name == "Legacy name"


def test_tree_and_report_dialog_word_every_badge_identically() -> None:
    """"Linked Session - 1 …" / "Linked Session" / "Atlas-only project group" / … drifted apart."""

    from tomography_session_browser.ui.main_window import MainWindow
    from tomography_session_browser.ui.project_tree.widgets import OBJECT_ROLE
    from tomography_session_browser.presentation.project_tree import PROJECT_BADGE_MEANINGS
    from tomography_session_browser.ui.report_scope_dialog import REPORT_SESSION_ROLE, ReportScopeDialog

    app = QApplication.instance() or QApplication([])
    atlas = _atlas_session("Atlas_Screening_Demo", "C:/project")
    linked = _collection_session("Data_Collection_Demo", "C:/project", "Atlas_Screening_Demo/Sample1/Atlas/Atlas.dm")
    lonely_atlas = _atlas_session("Atlas_Other", "C:/project")
    standalone = _collection_session("DataCollection_B", "C:/project")
    sessions = [atlas, linked, lonely_atlas, standalone]
    window = MainWindow()
    try:
        window.parts.loading.set_loaded_sessions(sessions)
        window.parts.scopes.populate_tree()
        groups = window.parts.scopes.project_groups()
        dialog = ReportScopeDialog(groups, sessions=sessions)
        tree_items = {
            window.ui.tree.topLevelItem(i).data(0, OBJECT_ROLE).key: window.ui.tree.topLevelItem(i)
            for i in range(window.ui.tree.topLevelItemCount())
            if hasattr(window.ui.tree.topLevelItem(i).data(0, OBJECT_ROLE), "key")
        }
        assert {group.kind for group in groups} == {"linked", "atlas", "collection"}
        for group in groups:
            tree_tip = tree_items[group.key].toolTip(1)
            assert tree_tip == dialog._group_items[group.key].toolTip(1)
            assert tree_tip.startswith(PROJECT_BADGE_MEANINGS[tree_items[group.key].text(1)])

        linked_group = next(group for group in groups if group.kind == "linked")
        tree_children = {
            tree_items[linked_group.key].child(i).data(0, OBJECT_ROLE).name: tree_items[linked_group.key].child(i).toolTip(1)
            for i in range(tree_items[linked_group.key].childCount())
        }
        dialog_item = dialog._group_items[linked_group.key]
        for i in range(dialog_item.childCount()):
            child = dialog_item.child(i)
            session = child.data(0, REPORT_SESSION_ROLE)
            assert child.toolTip(1) == tree_children[session.name]
        assert set(tree_children.values()) == {"Atlas screening session", "Data collection session"}
        dialog.deleteLater()
    finally:
        window.close()
