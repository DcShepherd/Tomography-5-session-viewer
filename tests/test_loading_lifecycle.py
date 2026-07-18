from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.domain.models import (
    Atlas,
    BatchPosition,
    MrcMetadata,
    Overview,
    Sample,
    SearchMap,
    SearchTile,
    Session,
    TiltSeries,
)
from tomography_session_browser.ui import main_window
from tomography_session_browser.ui.main_window import MainWindow, _prepare_session_ui_payload
from tomography_session_browser.ui.widgets.applied_defocus_plot import AppliedDefocusScatterPlot
from tomography_session_browser.ui.widgets.dose_information_plot import DoseInformationScatterPlot
from tomography_session_browser.ui.widgets.session_dashboard import SessionDashboard
import tomography_session_browser.ui.widgets.loading_overlay as loading_overlay_module


def test_large_session_ui_payload_is_prepared_without_widgets(tmp_path: Path) -> None:
    session = _large_session(tmp_path, search_maps=60, batch_positions=50, tilt_series=200)

    prepared = _prepare_session_ui_payload([session], session)

    assert prepared.dashboard_model is not None
    assert prepared.dashboard_model.collection_health.total_tilt_series == 200
    assert prepared.dashboard_model.collection_health.search_maps == 60
    assert prepared.dashboard_model.collection_health.batch_positions == 50
    assert len(prepared.viewer_tabs["Search map"].items) == 60
    assert len(prepared.viewer_tabs["Batch position"].items) == 50
    assert len(prepared.viewer_tabs["Tilt series"].items) == 200
    assert prepared.viewer_tabs["Tilt series"].statuses["ts-0"].status == "failed"
    assert prepared.viewer_tabs["Tilt series"].sources["ts-0"].primary is not None


def test_finish_loading_waits_for_repaint_turn_before_hiding_overlay(monkeypatch) -> None:
    app = _app()
    window = MainWindow()
    window._begin_loading("Finalising interface...")
    calls: list[bool] = []
    monkeypatch.setattr(window.loading_overlay, "hide_loading", lambda *, fade=True: calls.append(fade))

    window._finish_loading_after_repaint("Loading complete", fade=True)

    assert calls == []
    app.processEvents()
    assert calls == []
    app.processEvents()
    assert calls == [True]


def test_loader_tree_population_does_not_render_tabs_synchronously(monkeypatch, tmp_path: Path) -> None:
    _app()
    session = _large_session(tmp_path, search_maps=4, batch_positions=3, tilt_series=8)
    window = MainWindow()
    monkeypatch.setattr(main_window, "save_settings", lambda _settings: None)
    render_calls: list[str] = []
    monkeypatch.setattr(window, "_render_viewer_tabs", lambda *args, **kwargs: render_calls.append("tabs"))
    monkeypatch.setattr(window, "_render_dashboard_for_scope", lambda *args, **kwargs: render_calls.append("dashboard"))

    window._prepare_loaded_session(session, str(tmp_path), replace=True)

    assert render_calls == []


def test_loaded_session_integration_finishes_after_prepared_ui_repaint(monkeypatch, tmp_path: Path) -> None:
    app = _app()
    session = _large_session(tmp_path, search_maps=8, batch_positions=6, tilt_series=24)
    window = MainWindow()
    monkeypatch.setattr(main_window, "save_settings", lambda _settings: None)
    hide_calls: list[bool] = []
    monkeypatch.setattr(window.loading_overlay, "hide_loading", lambda *, fade=True: hide_calls.append(fade))

    window._loading_task_active = True
    window._begin_loading("Loading session...")
    window._integrate_loaded_session_cooperatively(session, str(tmp_path), replace=True, quiet=True, animate=False)

    deadline = time.monotonic() + 5.0
    while (window._loading_task_active or not hide_calls) and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)

    assert not window._loading_task_active
    assert hide_calls == [True]
    assert window._viewer_tabs["Tilt series"].list.topLevelItemCount() == 24


def test_loading_dashboard_defers_heavy_cards_until_overlay_hidden(monkeypatch, tmp_path: Path) -> None:
    app = _app()
    session = _large_session(tmp_path, search_maps=8, batch_positions=6, tilt_series=24)
    prepared = _prepare_session_ui_payload([session], session)
    window = MainWindow()
    window._sessions = [session]
    window._session = session
    window._active_context = session
    window._rebuild_sample_index()
    window.show()
    app.processEvents()

    calls: list[bool] = []

    def record_dashboard_model(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
        calls.append(bool(kwargs.get("defer_heavy_cards", False)))

    monkeypatch.setattr(window.session_dashboard, "set_model", record_dashboard_model)

    window._loading_task_active = True
    window._begin_loading("Loading session...")
    window._render_session_summary(animate_dashboard=True, prepared=prepared)

    assert calls == [True]
    assert window._deferred_loading_dashboard is not None

    window._loading_task_active = False
    window.loading_overlay.hide_loading(fade=False)
    app.processEvents()

    assert calls == [True, False]


def test_staged_loader_splits_session_summary_into_tree_and_dashboard_steps() -> None:
    """The staged loader must run the summary tree rebuild and the
    dashboard widget rebuild as separate stages.

    Both run on the main thread (the dashboard widget construction can't
    be moved off it). Running them back-to-back in a single stage starved
    the LoadingOverlay animation timer (>70 ms gap measured on a large reference session).
    Splitting them into two ``QTimer.singleShot(0, ...)``-separated
    stages lets the animation tick fire between them. This test pins the
    contract.
    """

    _app()
    window = MainWindow()

    tree_calls: list[bool] = []
    dashboard_calls: list[bool] = []

    def record_tree(*_args, **kwargs):
        tree_calls.append(True)

    def record_dashboard(*_args, **kwargs):
        dashboard_calls.append(True)

    window._render_session_summary_tree = record_tree  # type: ignore[assignment]
    window._render_session_summary_dashboard = record_dashboard  # type: ignore[assignment]

    # Drive the two staged sub-steps explicitly.
    window._render_loaded_session_summary(animate=False, prepared=None, dashboard_only=False)
    assert tree_calls == [True], "Tree-only stage must invoke the summary-tree builder."
    assert dashboard_calls == [], "Tree-only stage must NOT invoke the dashboard builder."

    window._render_loaded_session_summary(animate=False, prepared=None, dashboard_only=True)
    assert dashboard_calls == [True], "Dashboard-only stage must invoke the dashboard builder."
    assert tree_calls == [True], "Dashboard-only stage must NOT re-invoke the summary-tree builder."


def test_dashboard_deferred_model_skips_plot_widgets_until_requested(tmp_path: Path) -> None:
    app = _app()
    session = _large_session(tmp_path, search_maps=4, batch_positions=3, tilt_series=8)
    model = _prepare_session_ui_payload([session], session).dashboard_model
    dashboard = SessionDashboard()

    dashboard.set_model(model, defer_heavy_cards=True)
    app.processEvents()

    assert dashboard.findChild(AppliedDefocusScatterPlot) is None
    assert dashboard.findChild(DoseInformationScatterPlot) is None

    dashboard.set_model(model, defer_heavy_cards=False)
    app.processEvents()

    assert dashboard.findChild(AppliedDefocusScatterPlot) is not None


def test_loading_overlay_records_animation_timer_gaps(monkeypatch) -> None:
    _app()
    overlay = loading_overlay_module.LoadingOverlay()
    assert overlay.accessibleName() == "Loading overlay"
    assert "metadata" in overlay.accessibleDescription()
    timestamps = iter([0.0, 0.042, 0.184])
    monkeypatch.setattr(loading_overlay_module.time, "monotonic", lambda: next(timestamps))

    overlay.show_loading("Loading session...")
    overlay._advance()
    overlay._advance()

    snapshot = overlay.performance_snapshot()
    assert snapshot["tick_count"] == 1
    assert snapshot["max_interval_ms"] == 142.0
    assert snapshot["dropped_frames"] >= 1


def test_prepared_viewer_tabs_defer_offscreen_preview_loading(monkeypatch, tmp_path: Path) -> None:
    app = _app()
    session = _large_session(tmp_path, search_maps=3, batch_positions=3, tilt_series=6)
    prepared = _prepare_session_ui_payload([session], session)
    window = MainWindow()
    window._sessions = [session]
    window._session = session
    window._active_context = session
    window._rebuild_sample_index()
    window.tabs.setCurrentIndex(main_window.TAB_LABELS.index("Session"))

    loaded: list[str] = []
    def record_load(self, value, slice_index):  # noqa: ANN001
        del self, slice_index
        loaded.append(getattr(value, "id", "unknown"))

    monkeypatch.setattr(main_window.ViewerTab, "_load_value", record_load)

    window._render_viewer_tabs(prepared=prepared)

    assert loaded == []

    window.tabs.setCurrentIndex(main_window.TAB_LABELS.index("Tilt series"))
    app.processEvents()

    assert loaded == ["ts-0"]


def test_tab_switch_initial_selection_updates_context_panel(monkeypatch, tmp_path: Path) -> None:
    from tomography_session_browser.ui.image_viewer import VIEWER_OBJECT_ROLE

    app = _app()
    atlas = Atlas(id="atlas-1")
    overview = Overview(id="ov-1", name="Overview_1", image_path=tmp_path / "overview.jpg")
    search_maps = [
        SearchMap(id="sm-1", name="SearchMap_1"),
        SearchMap(id="sm-2", name="SearchMap_2"),
    ]
    search_tile = SearchTile(id="st-1", name="SearchTile_1")
    batch = BatchPosition(id="bp-1", name="Position_1")
    tilt = TiltSeries(id="ts-1", name="Tilt_1", mrc_path=tmp_path / "Tilt_1.mrc")
    sample = Sample(
        id="sample-1",
        name="Sample 1",
        path=tmp_path / "Sample1",
        atlas=atlas,
        overviews=[overview],
        search_maps=search_maps,
        search_tiles=[search_tile],
        batch_positions=[batch],
        tilt_series=[tilt],
    )
    session = Session(id="session-1", name="Session 1", path=tmp_path, kind=SessionKind.COLLECTION, samples=[sample])
    window = MainWindow()
    window._sessions = [session]
    window._session = session
    window._active_context = session
    window._rebuild_sample_index()
    window.tabs.setCurrentIndex(main_window.TAB_LABELS.index("Session"))
    window.show()
    app.processEvents()

    def record_load(self, value, slice_index):  # noqa: ANN001
        del slice_index
        self._current_value = value

    monkeypatch.setattr(main_window.ViewerTab, "_load_value", record_load)
    window._render_viewer_tabs()

    expected_by_tab = {
        "Atlas": atlas,
        "Overview": overview,
        "Search map": search_maps[0],
        "Search": search_tile,
        "Batch position": batch,
        "Tilt series": tilt,
    }
    for label, expected in expected_by_tab.items():
        window.context_panel.set_title("Stale")
        window.context_panel.set_text("stale")

        window.tabs.setCurrentIndex(main_window.TAB_LABELS.index(label))
        app.processEvents()

        viewer = window._viewer_tabs[label]
        current = viewer.list.currentItem()
        assert current is not None
        assert current.data(0, VIEWER_OBJECT_ROLE) is expected
        assert viewer._current_value is expected
        assert window.context_panel.text() == window._label_for(expected)

    window.tabs.setCurrentIndex(main_window.TAB_LABELS.index("Search map"))
    app.processEvents()
    search_viewer = window._viewer_tabs["Search map"]
    assert search_viewer._current_value is search_maps[0]
    assert window.context_panel.text() == search_maps[0].name

    search_viewer.list.setCurrentItem(search_viewer.list.topLevelItem(1))
    app.processEvents()

    assert search_viewer._current_value is search_maps[1]
    assert window.context_panel.text() == search_maps[1].name


def test_batch_exposure_marker_double_click_opens_related_tilt_series(monkeypatch, tmp_path: Path) -> None:
    _app()
    batch = BatchPosition(id="bp-1", name="Position_1", status="Acquired")
    tilt_path = tmp_path / "Position_1_3.mrc"
    tilt_path.write_bytes(b"stub")
    tilt = TiltSeries(
        id="ts-1",
        name="Position_1_3",
        mrc_path=tilt_path,
        linked_batch_position_id=batch.id,
    )
    batch.linked_tilt_series_ids.append(tilt.id)
    sample = Sample(id="sample-1", name="Sample 1", path=tmp_path, batch_positions=[batch], tilt_series=[tilt])
    session = Session(id="session-1", name="Session 1", path=tmp_path, kind=SessionKind.COLLECTION, samples=[sample])
    marker = ImageMarker(
        id="bp-1:exposure_area:Position_1_3",
        marker_type=MarkerType.EXPOSURE_AREA,
        linked_object_id=batch.id,
        source_object_id=batch.id,
        metadata={"raw": {"Name": "Position_1_3"}},
    )
    window = MainWindow()
    window._sessions = [session]
    window._rebuild_sample_index()
    window.tabs.setCurrentIndex(main_window.TAB_LABELS.index("Batch position"))
    selected: list[TiltSeries] = []
    monkeypatch.setattr(window, "_render_viewer_tabs", lambda: None)
    monkeypatch.setattr(window, "_select_viewer_object", lambda value: selected.append(value))

    window._select_marker(marker, navigate=True)

    assert selected == [tilt]
    assert window._selection_state.selected_object_type == "TiltSeries"
    assert window._selection_state.selected_object_id == tilt.id


def _large_session(tmp_path: Path, *, search_maps: int, batch_positions: int, tilt_series: int) -> Session:
    samples: list[Sample] = []
    for sample_index in range(13):
        sample_path = tmp_path / f"Sample{sample_index:02d}"
        sample_path.mkdir()
        samples.append(Sample(id=f"sample-{sample_index}", name=f"Sample {sample_index}", path=sample_path))

    maps = [
        SearchMap(id=f"sm-{index}", name=f"SearchMap_{index:02d}", linked_batch_position_ids=[])
        for index in range(search_maps)
    ]
    tilt_index = 0
    for batch_index in range(batch_positions):
        search_map = maps[batch_index % len(maps)]
        batch = BatchPosition(
            id=f"bp-{batch_index}",
            name=f"Position_{batch_index:03d}",
            status="Acquired",
            linked_search_map_id=search_map.id,
        )
        search_map.linked_batch_position_ids.append(batch.id)
        samples[batch_index % len(samples)].batch_positions.append(batch)

        repeats = (tilt_series // batch_positions) + (1 if batch_index < tilt_series % batch_positions else 0)
        for _repeat in range(repeats):
            if tilt_index >= tilt_series:
                break
            mrc_path = tmp_path / f"tilt_{tilt_index:03d}.mrc"
            mrc_path.write_bytes(b"stub")
            failed = tilt_index % 17 == 0
            tilt = TiltSeries(
                id=f"ts-{tilt_index}",
                name=f"tilt_{tilt_index:03d}",
                mrc_path=mrc_path,
                mrc_metadata=MrcMetadata(path=mrc_path, size_bytes=4, nz=4 if failed else 41),
                linked_batch_position_id=batch.id,
                acquisition_time_start=(datetime(2026, 1, 1, 9, 0, 0) + timedelta(minutes=tilt_index)).isoformat(),
                acquisition_time_end=(datetime(2026, 1, 1, 9, 2, 0) + timedelta(minutes=tilt_index)).isoformat(),
            )
            batch.linked_tilt_series_ids.append(tilt.id)
            search_map.linked_tilt_series_ids.append(tilt.id)
            samples[batch_index % len(samples)].tilt_series.append(tilt)
            tilt_index += 1

    for index, search_map in enumerate(maps):
        samples[index % len(samples)].search_maps.append(search_map)

    return Session(
        id="large",
        name="large",
        path=tmp_path,
        kind=SessionKind.COLLECTION,
        samples=samples,
    )


def _app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app
