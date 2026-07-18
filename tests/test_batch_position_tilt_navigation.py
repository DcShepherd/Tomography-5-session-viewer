from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.markers import MarkerType
from tomography_session_browser.domain.models import BatchPosition, MrcMetadata, Sample, Session, TiltSeries
from tomography_session_browser.services.marker_service import markers_for_object
from tomography_session_browser.ui.main_window import MainWindow


def _app() -> QApplication:
    app = QApplication.instance()
    return app or QApplication([])


def _batch_session(tmp_path: Path) -> tuple[Session, BatchPosition, list[TiltSeries]]:
    metadata = {
        "ExposureTemplateAreaParameters": {
            "Name": "Exposure",
            "PositionX": "0",
            "PositionY": "0",
        },
        "TrackingTemplateAreaParameters": {
            "Name": "Tracking",
            "PositionX": "1e-7",
            "PositionY": "0",
        },
        "AdditionalExposureTemplateAreas": {
            "ExposureTemplateAreaParameters": [
                {
                    "Name": "Exposure 1",
                    "PositionX": "2e-7",
                    "PositionY": "0",
                },
                {
                    "Name": "Exposure 2",
                    "PositionX": "4e-7",
                    "PositionY": "0",
                },
            ]
        },
    }
    batch = BatchPosition(
        id="batch-orali-3",
        name="orali_3",
        exposure_mrc_metadata=MrcMetadata(
            path=tmp_path / "orali_3_exposure.mrc",
            size_bytes=0,
            nx=512,
            ny=512,
            voxel_size=(1e-8, 1e-8, None),
        ),
        metadata=metadata,
    )
    tilts = [
        TiltSeries(id=f"ts-{index}", name=name, mrc_path=tmp_path / f"{name}.mrc", linked_batch_position_id=batch.id)
        for index, name in enumerate(("orali_3", "orali_3_2", "orali_3_3"), start=1)
    ]
    batch.linked_tilt_series_ids = [tilt.id for tilt in tilts]
    sample = Sample(
        id="sample-1",
        name="Sample 1",
        path=tmp_path,
        batch_positions=[batch],
        tilt_series=tilts,
    )
    session = Session(
        id="session-1",
        name="session-1",
        path=tmp_path,
        kind=SessionKind.MULTIGRID,
        samples=[sample],
    )
    return session, batch, tilts


def _window_with_batch(tmp_path: Path) -> tuple[MainWindow, BatchPosition, list[TiltSeries]]:
    _app()
    session, batch, tilts = _batch_session(tmp_path)
    window = MainWindow()
    window._sessions = [session]
    window._session = session
    window._active_context = session
    window._rebuild_sample_index()
    window._render_viewer_tabs()
    return window, batch, tilts


def _tilt_action(window: MainWindow, batch: BatchPosition):
    return next(action for action in window._viewer_navigation_actions(batch) if action.key == "tilt_series")


def test_batch_position_tilt_jump_is_disabled_until_exposure_is_selected(tmp_path: Path) -> None:
    window, batch, _tilts = _window_with_batch(tmp_path)

    action = _tilt_action(window, batch)

    assert action.label == "\u2197 Tilt series"
    assert action.enabled is False
    assert "No tilt series" in action.tooltip


def test_batch_position_tilt_jump_uses_selected_additional_exposure(monkeypatch, tmp_path: Path) -> None:
    app = _app()
    window, batch, tilts = _window_with_batch(tmp_path)
    viewer = window._viewer_tabs["Batch position"]
    markers = markers_for_object(batch, context=window._marker_context_for_batch_position(batch))
    exposure_two = next(
        marker
        for marker in markers
        if marker.marker_type == MarkerType.EXPOSURE_AREA
        and marker.metadata.get("exposure_index") == 1
    )
    viewer.select_marker(exposure_two.id)

    action = _tilt_action(window, batch)

    assert action.enabled is True
    assert "associated with this exposure area" in action.tooltip

    calls: list[tuple[str, str, bool]] = []

    def fake_navigate(tilt_series_id: str, frame_index=None, *, source: str = "timeline", set_summary_highlight: bool = False) -> None:
        calls.append((tilt_series_id, source, set_summary_highlight))

    monkeypatch.setattr(window, "navigate_to_tilt_series", fake_navigate)
    window._viewer_navigation_requested(batch, "tilt_series")
    app.processEvents()

    assert calls == [(tilts[1].id, "batch position exposure", False)]


def test_batch_position_tilt_jump_stays_disabled_for_tracking_marker(tmp_path: Path) -> None:
    window, batch, _tilts = _window_with_batch(tmp_path)
    viewer = window._viewer_tabs["Batch position"]
    markers = markers_for_object(batch, context=window._marker_context_for_batch_position(batch))
    tracking = next(marker for marker in markers if marker.marker_type == MarkerType.TRACKING_AREA)
    viewer.select_marker(tracking.id)

    action = _tilt_action(window, batch)

    assert action.enabled is False
    assert "No tilt series" in action.tooltip
