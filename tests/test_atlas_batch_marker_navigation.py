from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import tomography_session_browser.ui.main_window as main_window
from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.domain.models import BatchPosition, Overview, Sample, SearchMap, Session
from tomography_session_browser.services.marker_service import MarkerContext
from tomography_session_browser.services.navigation_service import (
    resolve_batch_position_overview,
    resolve_batch_position_search_map,
)
from tomography_session_browser.ui.main_window import MainWindow, TAB_LABELS


def _app() -> QApplication:
    app = QApplication.instance()
    return app or QApplication([])


def test_batch_leaf_resolves_explicit_overview() -> None:
    overview = Overview(id="ov-1", name="Overview_1", image_path=Path("overview.jpg"))
    batch = BatchPosition(
        id="batch-1",
        name="Position_1",
        linked_overview_id=overview.id,
    )
    result = resolve_batch_position_overview(
        batch,
        search_maps=(),
        overviews=(overview,),
    )
    assert result.navigable is True
    assert result.overview is overview


def test_ambiguous_overview_link_stays_disabled_with_explanation() -> None:
    first = Overview(
        id="ov-1",
        name="Overview_A",
        image_path=Path("a.jpg"),
        linked_batch_position_ids=["batch-1"],
    )
    second = Overview(
        id="ov-2",
        name="Overview_B",
        image_path=Path("b.jpg"),
        linked_batch_position_ids=["batch-1"],
    )
    result = resolve_batch_position_overview(
        BatchPosition(id="batch-1"),
        search_maps=(),
        overviews=(first, second),
    )
    assert result.navigable is False
    assert result.ambiguous is True
    assert "ambiguous" in result.explanation.lower()
    assert "Overview_A" in result.explanation
    assert "Overview_B" in result.explanation


def test_batch_resolves_overview_through_unique_search_map() -> None:
    overview = Overview(id="ov-1", name="Overview_1", image_path=Path("overview.jpg"))
    search_map = SearchMap(id="sm-1", name="SearchMap_1", overview=overview)
    batch = BatchPosition(id="batch-1", linked_search_map_id=search_map.id)
    result = resolve_batch_position_overview(
        batch,
        search_maps=(search_map,),
        overviews=(overview,),
    )
    assert result.navigable is True
    assert result.overview is overview


def test_batch_search_map_resolution_stays_ambiguity_aware() -> None:
    first = SearchMap(
        id="sm-1",
        name="SearchMap_1",
        linked_batch_position_ids=["batch-1"],
    )
    second = SearchMap(
        id="sm-2",
        name="SearchMap_2",
        linked_batch_position_ids=["batch-1"],
    )
    result = resolve_batch_position_search_map(
        BatchPosition(id="batch-1"),
        search_maps=(first, second),
    )

    assert result.navigable is False
    assert result.ambiguous is True
    assert "SearchMap_1" in result.explanation
    assert "SearchMap_2" in result.explanation


def test_cluster_popup_member_opens_explicit_search_map(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _app()
    search_map = SearchMap(id="sm-1", name="SearchMap_1")
    batch = BatchPosition(
        id="batch-1",
        name="Position_1",
        linked_search_map_id=search_map.id,
    )
    sample = Sample(
        id="sample",
        name="Sample 1",
        path=tmp_path,
        search_maps=[search_map],
        batch_positions=[batch],
    )
    session = Session(
        id="session",
        name="Session",
        path=tmp_path,
        kind=SessionKind.COLLECTION,
        samples=[sample],
    )
    marker = ImageMarker(
        id="atlas:batch:batch-1",
        marker_type=MarkerType.BATCH_POSITION,
        linked_object_id=batch.id,
        source_object_id="atlas",
        metadata={
            "atlas_lod_role": "batch_position",
            "batch_position_id": batch.id,
        },
    )
    context = MarkerContext(
        search_maps=(search_map,),
        batch_positions=(batch,),
    )
    window = MainWindow()
    window._sessions = [session]
    window._session = session
    window._active_context = session
    window._rebuild_sample_index()
    monkeypatch.setattr(
        window,
        "_marker_context_for_batch_position",
        lambda _batch: context,
    )
    monkeypatch.setattr(window, "_context_for_object", lambda _value: None)
    selected_objects: list[object] = []
    selected_markers: list[str | None] = []
    monkeypatch.setattr(window, "_select_viewer_object", selected_objects.append)
    monkeypatch.setattr(
        window,
        "_marker_id_for_batch_position_navigation",
        lambda *_args, **_kwargs: "search-map:batch:batch-1",
    )
    monkeypatch.setattr(
        window._viewer_tabs["Search map"],
        "select_marker",
        selected_markers.append,
    )

    window._atlas_cluster_member_activated(marker)

    assert selected_objects == [search_map]
    assert selected_markers == ["search-map:batch:batch-1"]
    assert "SearchMap_1" in window.statusBar().currentMessage()


def test_atlas_leaf_arrival_selects_batch_marker_on_overview(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _app()
    overview = Overview(id="ov-1", name="Overview_1", image_path=tmp_path / "overview.jpg")
    search_map = SearchMap(id="sm-1", name="SearchMap_1", overview=overview)
    batch = BatchPosition(
        id="batch-1",
        name="Position_1",
        linked_overview_id=overview.id,
        linked_search_map_id=search_map.id,
    )
    sample = Sample(
        id="sample",
        name="Sample 1",
        path=tmp_path,
        overviews=[overview],
        search_maps=[search_map],
        batch_positions=[batch],
    )
    session = Session(
        id="session",
        name="Session",
        path=tmp_path,
        kind=SessionKind.MULTIGRID,
        samples=[sample],
    )
    context = MarkerContext(
        overviews=(overview,),
        search_maps=(search_map,),
        batch_positions=(batch,),
    )
    overview_marker = ImageMarker(
        id="overview:batch:batch-1",
        marker_type=MarkerType.BATCH_POSITION,
        linked_object_id=batch.id,
        source_object_id=overview.id,
        x=10.0,
        y=10.0,
    )
    atlas_marker = ImageMarker(
        id="atlas:batch:batch-1",
        marker_type=MarkerType.BATCH_POSITION,
        linked_object_id=batch.id,
        source_object_id="atlas",
        x=5.0,
        y=5.0,
        label="1",
        metadata={
            "atlas_lod_role": "batch_position",
            "batch_position_id": batch.id,
            "navigation_enabled": True,
        },
    )

    window = MainWindow()
    window._sessions = [session]
    window._session = session
    window._active_context = session
    window._rebuild_sample_index()
    monkeypatch.setattr(window, "_render_viewer_tabs", lambda *args, **kwargs: None)
    monkeypatch.setattr(window, "_context_for_object", lambda _value: None)
    monkeypatch.setattr(window, "_marker_context_for_batch_position", lambda _value: context)
    monkeypatch.setattr(window, "_marker_context_for_overview", lambda _value: context)
    monkeypatch.setattr(window, "_set_context", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        main_window,
        "markers_for_object",
        lambda value, **_kwargs: [overview_marker] if value is overview else [],
    )

    selected_objects: list[object] = []
    selected_markers: list[str | None] = []
    viewer = window._viewer_tabs["Overview"]
    monkeypatch.setattr(viewer, "select_object", selected_objects.append)
    monkeypatch.setattr(viewer, "select_marker", selected_markers.append)

    assert window._open_atlas_batch_overview(atlas_marker) is True
    assert TAB_LABELS[window.tabs.currentIndex()] == "Overview"
    assert selected_objects == [overview]
    assert selected_markers == [overview_marker.id]


def test_single_click_selects_atlas_leaf_without_opening_overview(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _app()
    batch = BatchPosition(id="batch-1", name="Position_1")
    session = Session(
        id="session",
        name="Session",
        path=tmp_path,
        kind=SessionKind.COLLECTION,
        batch_positions=[batch],
    )
    marker = ImageMarker(
        id="atlas:batch:batch-1",
        marker_type=MarkerType.BATCH_POSITION,
        linked_object_id=batch.id,
        source_object_id="atlas",
        x=5.0,
        y=5.0,
        label="1",
        metadata={
            "atlas_lod_role": "batch_position",
            "batch_position_id": batch.id,
            "navigation_enabled": True,
        },
    )
    window = MainWindow()
    window._sessions = [session]
    window._session = session
    window._rebuild_sample_index()
    opened: list[ImageMarker] = []
    monkeypatch.setattr(window, "_open_atlas_batch_overview", opened.append)
    monkeypatch.setattr(window, "_set_context", lambda *_args, **_kwargs: None)

    window._select_marker(marker, navigate=False)

    assert opened == []
