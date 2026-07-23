from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QTreeWidgetItem

from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.models import Atlas, BatchPosition, Overview, Sample, SearchMap, SearchTile, Session, TiltSeries
from tomography_session_browser.reports import ProjectReportGroup, build_session_report
from tomography_session_browser.ui.project_model import build_project_tree_groups


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
    window._sessions = [atlas, collection]
    window._rebuild_sample_index()
    window._populate_tree()
    app.processEvents()

    labels: list[str] = []

    def collect(item) -> None:
        labels.append(item.text(0))
        for index in range(item.childCount()):
            collect(item.child(index))

    for index in range(window.tree.topLevelItemCount()):
        collect(window.tree.topLevelItem(index))

    assert window.tree.topLevelItem(0).text(0) == "Data_Collection_Demo"
    assert window.tree.topLevelItem(0).text(1) == "LS"
    assert window.tree.topLevelItem(0).toolTip(1) == "Linked Session - 1 data collection session"
    assert "Linked samples" not in labels


def test_linked_root_tab_activation_keeps_aggregate_viewer_lists(monkeypatch, tmp_path: Path) -> None:
    from tomography_session_browser.ui import main_window
    from tomography_session_browser.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    atlas_session = Session(
        id="atlas-session",
        name="Atlas_Screening_Demo",
        path=tmp_path / "Atlas_Screening_Demo",
        kind=SessionKind.ATLAS_SCREENING,
        samples=[
            Sample(
                id="atlas-vellio",
                name="Atlas sample alpha",
                path=tmp_path / "Atlas_Screening_Demo" / "Sample1",
                atlas=Atlas(id="atlas-vellio"),
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
    window = MainWindow()
    monkeypatch.setattr(main_window, "save_settings", lambda _settings: None)
    monkeypatch.setattr(
        main_window.ViewerTab,
        "_load_value",
        lambda self, value, _slice_index: setattr(self, "_current_value", value),
    )
    window._sessions = [atlas_session, collection_session]
    window._session = collection_session
    window._rebuild_sample_index()
    window._populate_tree()
    linked_group = window.tree.topLevelItem(0).data(0, main_window.OBJECT_ROLE)

    window._select_from_left_tree(linked_group, linked_group.display_name)
    app.processEvents()

    assert window._active_context is linked_group
    assert window._viewer_tabs["Atlas"].list.topLevelItemCount() == 2
    assert window._viewer_tabs["Search map"].list.topLevelItemCount() == 2
    assert window._viewer_tabs["Search"].list.topLevelItemCount() == 2
    assert window._viewer_tabs["Batch position"].list.topLevelItemCount() == 2
    assert window._viewer_tabs["Tilt series"].list.topLevelItemCount() == 2
    group_context_text = window.context_panel._raw_text

    window._active_context = linked_group
    window._preserve_tree_root_context = False
    window._render_viewer_tabs()
    window.tabs.setCurrentIndex(main_window.TAB_LABELS.index("Atlas"))
    app.processEvents()

    assert window._active_context is linked_group
    assert window._viewer_tabs["Atlas"].list.topLevelItemCount() == 2
    assert window._viewer_tabs["Search map"].list.topLevelItemCount() == 2

    window.tabs.setCurrentIndex(main_window.TAB_LABELS.index("Atlas"))
    app.processEvents()

    assert window._active_context is linked_group
    assert window.context_panel._raw_text == group_context_text
    assert window._viewer_tabs["Atlas"].list.topLevelItemCount() == 2
    assert window._viewer_tabs["Search map"].list.topLevelItemCount() == 2
    assert window._viewer_tabs["Search"].list.topLevelItemCount() == 2
    assert window._viewer_tabs["Batch position"].list.topLevelItemCount() == 2
    assert window._viewer_tabs["Tilt series"].list.topLevelItemCount() == 2

    window.tabs.setCurrentIndex(main_window.TAB_LABELS.index("Search map"))
    app.processEvents()

    assert window._active_context is linked_group
    assert window.context_panel._raw_text == group_context_text
    assert window._viewer_tabs["Atlas"].list.topLevelItemCount() == 2
    assert window._viewer_tabs["Search map"].list.topLevelItemCount() == 2
    assert window._viewer_tabs["Search"].list.topLevelItemCount() == 2
    assert window._viewer_tabs["Batch position"].list.topLevelItemCount() == 2
    assert window._viewer_tabs["Tilt series"].list.topLevelItemCount() == 2

    selected_search_tile = collection_samples[1].search_tiles[0]
    window._viewer_item_selected(selected_search_tile)
    app.processEvents()

    assert window._active_context is linked_group
    assert selected_search_tile.name in window.context_panel._raw_text

    first_linked_sample = window._context_for_object(collection_samples[0].search_maps[0])
    window._active_context = first_linked_sample
    window._preserve_tree_root_context = True
    window._render_viewer_tabs()
    window._update_tab_counts()

    assert "Atlas  2" == window.tabs.tabText(main_window.TAB_LABELS.index("Atlas"))
    assert "Search map  2" == window.tabs.tabText(main_window.TAB_LABELS.index("Search map"))
    assert window._viewer_tabs["Atlas"].list.topLevelItemCount() == 2
    assert window._viewer_tabs["Search map"].list.topLevelItemCount() == 2
    assert window._viewer_tabs["Search"].list.topLevelItemCount() == 2
    assert window._viewer_tabs["Batch position"].list.topLevelItemCount() == 2
    assert window._viewer_tabs["Tilt series"].list.topLevelItemCount() == 2


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
    window._sessions = [linked_atlas, linked_collection, atlas_only, unlinked_collection]
    window._rebuild_sample_index()
    window._populate_tree()
    app.processEvents()

    linked_item = _top_level_item(window, "Data_Collection_Demo")
    atlas_item = _top_level_item(window, "Standalone_Atlas")
    collection_item = _top_level_item(window, "Unlinked_Dataset")

    assert linked_item is not None
    assert atlas_item is not None
    assert collection_item is not None
    assert linked_item.text(1) == "LS"
    assert linked_item.toolTip(1).startswith("Linked Session")
    assert atlas_item.text(1) == "AT"
    assert collection_item.text(1) == "DC"

    linked_group = window._project_groups()[0]
    window._project_display_names[linked_group.key] = "Renamed linked session"
    window._refresh_project_state_after_edit(select_group_key=linked_group.key)
    app.processEvents()

    renamed_item = _top_level_item(window, "Renamed linked session")
    assert renamed_item is not None
    assert renamed_item.text(1) == "LS"


def test_project_tree_preserves_expansion_state_across_rebuilds() -> None:
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
    window._sessions = [atlas, collection, unrelated]
    window._rebuild_sample_index()
    window._populate_tree()
    app.processEvents()

    linked_item = _top_level_item(window, "Data_Collection_Demo")
    unrelated_item = _top_level_item(window, "DataCollection_B")
    assert linked_item is not None
    assert unrelated_item is not None
    assert not linked_item.isExpanded()
    assert not unrelated_item.isExpanded()

    linked_item.setExpanded(True)
    unrelated_item.setExpanded(False)
    window._sessions.append(extra)
    window._populate_tree()
    app.processEvents()

    linked_item = _top_level_item(window, "Data_Collection_Demo")
    unrelated_item = _top_level_item(window, "DataCollection_B")
    extra_item = _top_level_item(window, "DataCollection_C")
    assert linked_item is not None and linked_item.isExpanded()
    assert unrelated_item is not None and not unrelated_item.isExpanded()
    assert extra_item is not None and not extra_item.isExpanded()

    linked_group = window._project_groups()[0]
    window._project_display_names[linked_group.key] = "Renamed linked group"
    window._refresh_project_state_after_edit(select_group_key=linked_group.key)
    app.processEvents()

    renamed_item = _top_level_item(window, "Renamed linked group")
    assert renamed_item is not None and renamed_item.isExpanded()

    window._project_display_names.pop(linked_group.key, None)
    window._refresh_project_state_after_edit(select_group_key=linked_group.key)
    app.processEvents()

    linked_item = _top_level_item(window, "Data_Collection_Demo")
    assert linked_item is not None and linked_item.isExpanded()

    window._remove_session_from_project(extra)
    app.processEvents()

    linked_item = _top_level_item(window, "Data_Collection_Demo")
    unrelated_item = _top_level_item(window, "DataCollection_B")
    assert linked_item is not None and linked_item.isExpanded()
    assert unrelated_item is not None and not unrelated_item.isExpanded()
    assert _top_level_item(window, "DataCollection_C") is None


def test_collection_session_scope_includes_linked_atlas_without_broadening_counts() -> None:
    from tomography_session_browser.ui.main_window import MainWindow
    from tomography_session_browser.ui.session_presenter import session_dashboard_model

    app = QApplication.instance() or QApplication([])
    atlas = _atlas_session("Atlas_Screening_Demo", "C:/project")
    collection = _collection_session(
        "Data_Collection_Demo",
        "C:/project",
        "Atlas_Screening_Demo/Sample1/Atlas/Atlas.dm",
    )
    unrelated = _collection_session("DataCollection_B", "C:/project")
    window = MainWindow()
    window._sessions = [atlas, collection, unrelated]
    window._rebuild_sample_index()
    window._active_context = collection
    window._render_viewer_tabs()
    window._update_tab_counts()
    app.processEvents()

    linked_atlases = window._context_atlases(collection)
    assert linked_atlases == [atlas.samples[0].atlas]
    assert window.tabs.tabText(1) == "Atlas  1"
    assert window.tabs.tabText(5) == "Batch position  1"
    assert window._context_batch_positions(collection) == collection.samples[0].batch_positions
    assert window._context_for_object(linked_atlases[0]) is collection
    assert window._current_viewer_scope_contains(linked_atlases[0])
    model = session_dashboard_model(window._dashboard_scope_value())
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
    window._sessions = [atlas, collection]
    window._rebuild_sample_index()
    window._active_context = collection

    window._set_context(collection.name, collection)
    app.processEvents()
    text = window.context_panel.toPlainText()

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
    window._sessions = [atlas, collection]
    window._rebuild_sample_index()
    window._active_context = collection
    app.processEvents()

    assert window._context_atlases(collection) == [second_atlas]
    assert "Sample: Specimen Beta" in window._context_description(collection)


def test_collection_context_panel_reports_missing_atlas_only_when_unresolved() -> None:
    from tomography_session_browser.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    collection = _collection_session("DataCollection_B", "C:/project")
    window = MainWindow()
    window._sessions = [collection]
    window._rebuild_sample_index()
    window._active_context = collection

    window._set_context(collection.name, collection)
    app.processEvents()

    assert "Atlas: No atlas associated" in window.context_panel.toPlainText()


def test_multi_sample_linked_atlas_scope_does_not_build_atlas_sparkline() -> None:
    from tomography_session_browser.ui.main_window import MainWindow
    from tomography_session_browser.ui.session_presenter import session_dashboard_model

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
    window._sessions = [atlas, collection]
    window._rebuild_sample_index()
    window._active_context = collection
    app.processEvents()

    model = session_dashboard_model(window._dashboard_scope_value())
    atlas_card = next(card for card in model.counts if card.label == "Atlases")

    assert atlas_card.value == 1
    assert atlas_card.sparkline == []
    assert _card_value(model, "Batch positions") == 2


def test_collection_sample_scope_includes_only_its_linked_atlas() -> None:
    from tomography_session_browser.ui.main_window import MainWindow
    from tomography_session_browser.ui.session_presenter import session_dashboard_model

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
    window._sessions = [atlas, first, second]
    window._rebuild_sample_index()
    sample = first.samples[0]
    window._active_context = sample
    window._render_viewer_tabs()
    window._update_tab_counts()
    app.processEvents()

    linked_atlases = window._context_atlases(sample)
    assert linked_atlases == [atlas.samples[0].atlas]
    assert window.tabs.tabText(1) == "Atlas  1"
    assert window.tabs.tabText(5) == "Batch position  1"
    assert window._context_batch_positions(sample) == sample.batch_positions
    assert window._context_for_object(linked_atlases[0]) is sample
    model = session_dashboard_model(window._dashboard_scope_value())
    assert _card_value(model, "Atlases") == 1
    assert _card_value(model, "Batch positions") == 1


def test_unrelated_collection_scope_keeps_empty_atlas_state() -> None:
    from tomography_session_browser.ui.main_window import MainWindow
    from tomography_session_browser.ui.session_presenter import session_dashboard_model

    app = QApplication.instance() or QApplication([])
    collection = _collection_session("DataCollection_B", "C:/project")
    window = MainWindow()
    window._sessions = [collection]
    window._rebuild_sample_index()
    window._active_context = collection
    window._render_viewer_tabs()
    window._update_tab_counts()
    app.processEvents()

    assert window._context_atlases(collection) == []
    assert window.tabs.tabText(1) == "Atlas  0"
    model = session_dashboard_model(window._dashboard_scope_value())
    assert _card_value(model, "Atlases") == 0


def test_dashboard_scope_returns_to_parent_session_after_tilt_list_selection(tmp_path: Path) -> None:
    from tomography_session_browser.ui import main_window
    from tomography_session_browser.ui.main_window import MainWindow
    from tomography_session_browser.ui.session_presenter import session_dashboard_model

    app = QApplication.instance() or QApplication([])
    collection = _collection_session("Vellio", str(tmp_path))
    tilts = [
        TiltSeries(id=f"vellio-ts-{index}", name=f"vellio_2_{index}", mrc_path=tmp_path / f"vellio_2_{index}.mrc")
        for index in range(1, 4)
    ]
    collection.samples[0].tilt_series = tilts
    collection.samples.append(
        Sample(
            id="vellio-sample-2",
            name="Sample2",
            path=tmp_path / "Sample2",
            tilt_series=[TiltSeries(id="vellio-ts-4", name="vellio_2_4", mrc_path=tmp_path / "vellio_2_4.mrc")],
        )
    )
    window = MainWindow()
    window._sessions = [collection]
    window._rebuild_sample_index()
    window._populate_tree()
    parent_item = _top_level_item(window, "Vellio")
    assert parent_item is not None

    window.tree.setCurrentItem(parent_item)
    window._tree_selection_changed(parent_item, None)
    app.processEvents()
    assert window._dashboard_scope_value() is collection
    assert _card_value(session_dashboard_model(window._dashboard_scope_value()), "Tilt series") == 4

    window._viewer_item_selected(tilts[1])
    assert window._selection_state.selected_object_id == "vellio-ts-2"
    assert window._tilt_viewer_selected_tilt_series_id == "vellio-ts-2"
    assert window._dashboard_highlighted_tilt_series_id is None
    assert window._dashboard_scope_value() is collection

    # Guard against legacy stale focus state: tilt-series selection should not
    # collapse the Session Summary scope down to a single tilt.
    window._dashboard_focus_value = tilts[1]
    assert window._dashboard_scope_value() is collection
    assert _card_value(session_dashboard_model(window._dashboard_scope_value()), "Tilt series") == 4

    window._on_dashboard_sample_clicked(collection.samples[0].id)
    assert window._dashboard_scope_value() is collection.samples[0]
    assert _card_value(session_dashboard_model(window._dashboard_scope_value()), "Tilt series") == 3

    tilt_item = _tree_item_with_object(window, tilts[1])
    assert tilt_item is not None
    window.tree.setCurrentItem(tilt_item)
    window._tree_selection_changed(tilt_item, parent_item)
    app.processEvents()
    assert window._dashboard_scope_value() is collection.samples[0]
    assert window._dashboard_highlighted_tilt_series_id is None

    window._project_tree_item_pressed(tilt_item, 0)

    assert window._dashboard_highlighted_tilt_series_id == tilts[1].id
    assert window._pending_project_tree_clear_tilt_id is None
    assert not window._project_tree_tilt_click_timer.isActive()
    assert bool(tilt_item.data(0, main_window.HIGHLIGHT_ROLE))
    assert _card_value(session_dashboard_model(window._dashboard_scope_value()), "Tilt series") == 3

    window._project_tree_item_pressed(tilt_item, 0)
    assert window._pending_project_tree_clear_tilt_id == tilts[1].id
    assert window._dashboard_highlighted_tilt_series_id == tilts[1].id
    window._project_tree_tilt_click_timer.stop()
    window._apply_pending_project_tree_highlight_clear()
    assert window._dashboard_highlighted_tilt_series_id is None
    assert not bool(tilt_item.data(0, main_window.HIGHLIGHT_ROLE))

    viewer = window._viewer_tabs["Tilt series"]
    viewer.list_filter.setText("manual filter")
    window._project_tree_item_pressed(tilt_item, 0)
    assert window._dashboard_highlighted_tilt_series_id == tilts[1].id
    window._project_tree_item_pressed(tilt_item, 0)
    assert window._pending_project_tree_clear_tilt_id == tilts[1].id
    window._project_tree_item_double_clicked(tilt_item, 0)
    app.processEvents()
    assert window._pending_project_tree_clear_tilt_id is None
    assert not window._project_tree_tilt_click_timer.isActive()
    assert window.tabs.currentIndex() == main_window.TAB_LABELS.index("Tilt series")
    assert viewer.list_filter.text() == "manual filter"
    assert window._dashboard_highlighted_tilt_series_id == tilts[1].id
    assert bool(tilt_item.data(0, main_window.HIGHLIGHT_ROLE))

    other_tilt_item = _tree_item_with_object(window, tilts[2])
    assert other_tilt_item is not None
    window._project_tree_item_pressed(other_tilt_item, 0)
    assert not bool(tilt_item.data(0, main_window.HIGHLIGHT_ROLE))
    assert bool(other_tilt_item.data(0, main_window.HIGHLIGHT_ROLE))

    window.tree.setCurrentItem(parent_item)
    window._tree_selection_changed(parent_item, tilt_item)
    app.processEvents()
    assert window._dashboard_scope_value() is collection
    assert window._dashboard_highlighted_tilt_series_id is None
    assert not bool(other_tilt_item.data(0, main_window.HIGHLIGHT_ROLE))
    assert _card_value(session_dashboard_model(window._dashboard_scope_value()), "Tilt series") == 4


def _card_value(model, label: str):
    for card in model.counts:
        if card.label == label:
            return card.value
    return None


def _top_level_item(window, label: str):
    for index in range(window.tree.topLevelItemCount()):
        item = window.tree.topLevelItem(index)
        if item.text(0) == label:
            return item
    return None


def _tree_item_with_object(window, value) -> QTreeWidgetItem | None:
    def visit(item: QTreeWidgetItem) -> QTreeWidgetItem | None:
        if item.data(0, main_window.OBJECT_ROLE) is value:
            return item
        for child_index in range(item.childCount()):
            found = visit(item.child(child_index))
            if found is not None:
                return found
        return None

    from tomography_session_browser.ui import main_window

    for index in range(window.tree.topLevelItemCount()):
        found = visit(window.tree.topLevelItem(index))
        if found is not None:
            return found
    return None
