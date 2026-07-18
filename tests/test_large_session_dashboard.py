from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QTableView, QToolButton

from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.domain.models import BatchPosition, MdocSection, MrcMetadata, Overview, Sample, SearchMap, Session, TiltSeries
from tomography_session_browser.reports.report_generator import _SessionContext, _aggregate_totals
from tomography_session_browser.services.timeline_service import TimelineSegment, SessionTimeline, build_session_timeline
from tomography_session_browser.ui.session_presenter import (
    AppliedDefocusPlotModel,
    AppliedDefocusPointModel,
    DashboardEntityScope,
    DoseInformationPlotModel,
    DoseInformationPointModel,
    applied_defocus_point_tooltip,
    build_linked_sample_group,
    dose_information_point_tooltip,
    session_counts,
    session_dashboard_model,
)
from tomography_session_browser.ui.image_viewer import ImagePreviewView
from tomography_session_browser.ui.theme import current_palette
from tomography_session_browser.ui.widgets.applied_defocus_plot import AppliedDefocusScatterPlot
from tomography_session_browser.ui.widgets.dashboard_card import DASHBOARD_CARD_MARGINS
from tomography_session_browser.ui.widgets.dose_information_plot import DoseInformationScatterPlot
from tomography_session_browser.ui.widgets.session_dashboard import SessionDashboard
from tomography_session_browser.ui.widgets.stat_card import StatCard
from tomography_session_browser.ui.widgets.time_chart import (
    TIME_CHART_ELAPSED_AXIS_LABEL,
    TIME_CHART_LEFT_GUTTER,
    format_time_axis_tick,
)
import tomography_session_browser.ui.widgets.timeline_strip as timeline_strip_module
from tomography_session_browser.ui.widgets.timeline_strip import TimelineStrip


def test_large_collection_dashboard_uses_summary_models(tmp_path: Path) -> None:
    session = _large_session(tmp_path, search_maps=60, batch_positions=50, tilt_series=210)

    model = session_dashboard_model(session)

    assert model.collection_health.total_tilt_series == 210
    assert model.collection_health.search_maps == 60
    assert model.collection_health.batch_positions == 50
    assert model.collection_health.samples == 13
    assert model.search_map_overview.total == 60
    assert len(model.search_map_overview.worst) == 5
    assert len(model.search_map_completion) <= 60
    assert any(filter_model.label == "Failed" and filter_model.count > 0 for filter_model in model.filters)


def test_session_scope_deduplicates_direct_and_sample_collections(tmp_path: Path) -> None:
    session = _large_session(tmp_path, search_maps=6, batch_positions=4, tilt_series=12)
    overview_path = tmp_path / "overview_001.mrc"
    overview_path.write_bytes(b"stub")
    overview = Overview(id="overview-1", name="Overview_001", image_path=overview_path)
    session.samples[0].overviews.append(overview)

    # Single-collection parsing exposes the same objects both on Session and
    # on its one Sample. The top-level dashboard/report scope must count the
    # physical entities once.
    session.overviews = [overview]
    session.search_maps = [item for sample in session.samples for item in sample.search_maps]
    session.batch_positions = [item for sample in session.samples for item in sample.batch_positions]
    session.tilt_series = [item for sample in session.samples for item in sample.tilt_series]
    for index, tilt in enumerate(session.tilt_series):
        timestamp = (datetime(2026, 1, 1, 9, index, 0)).strftime("%Y-%m-%d %H:%M:%S")
        tilt.sections = [MdocSection(z_value=0, metadata={"DateTime": timestamp})]

    model = session_dashboard_model(session)
    card_values = {card.label: card.value for card in model.counts}

    assert card_values["Overviews"] == 1
    assert card_values["Search maps"] == 6
    assert card_values["Batch positions"] == 4
    assert card_values["Tilt series"] == 12
    assert model.search_map_overview.total == 6
    assert model.collection_health.search_maps == 6
    assert model.collection_health.batch_positions == 4
    assert model.collection_health.total_tilt_series == 12
    assert len(model.timeline_items) == 12
    assert len(build_session_timeline(session).segments) == 12

    counts = session_counts(session)
    assert counts["Overviews"] == 1
    assert counts["Search maps"] == 6
    assert counts["Batch positions"] == 4
    assert counts["Tilt series"] == 12

    totals = _aggregate_totals([_SessionContext.build(session)])
    assert totals.overviews == 1
    assert totals.search_maps == 6
    assert totals.batch_positions == 4
    assert totals.tilt_series == 12


def test_search_maps_without_acquisition_links_are_na_and_sorted_last(tmp_path: Path) -> None:
    session = _large_session(tmp_path, search_maps=6, batch_positions=3, tilt_series=9)

    model = session_dashboard_model(session)
    overview = model.search_map_overview
    na_rows = [row for row in overview.rows if not row.acquisition_associated]

    assert overview.not_associated == 3
    assert len(na_rows) == 3
    assert all(row.summary == "N/A" for row in na_rows)
    assert all(not row.acquisition_associated for row in overview.rows[-3:])
    assert overview.complete + overview.incomplete + overview.failed + overview.unknown == (
        overview.total - overview.not_associated
    )
    assert all(row.acquisition_associated for row in overview.worst[:3])


def test_search_map_overview_counts_stage_inferred_failed_tilts(tmp_path: Path) -> None:
    search_map_path = tmp_path / "SearchMap_20260320_022428.mrc"
    search_map_path.write_bytes(b"stub")
    tilt_path = tmp_path / "vellio_1_2.mrc"
    tilt_path.write_bytes(b"stub")
    good_tilt_path = tmp_path / "vellio_2.mrc"
    good_tilt_path.write_bytes(b"stub")
    search_map = SearchMap(
        id="sm-failed",
        name="SearchMap_20260320_022428",
        mrc_path=search_map_path,
        mrc_metadata=MrcMetadata(
            path=search_map_path,
            size_bytes=4,
            nx=100,
            ny=100,
            frame_metadata=[{"stage_x": 0.0, "stage_y": 0.0, "pixel_size": 1e-6}],
        ),
    )
    failed_tilt = TiltSeries(
        id="ts-failed",
        name="vellio_1_2",
        mrc_path=tilt_path,
        mrc_metadata=MrcMetadata(
            path=tilt_path,
            size_bytes=4,
            nz=4,
            frame_metadata=[{"stage_x": 0.0, "stage_y": 0.0}],
        ),
    )
    batch = BatchPosition(
        id="bp-good",
        name="vellio_2",
        linked_search_map_id=search_map.id,
        linked_tilt_series_ids=["ts-good"],
    )
    good_tilt = TiltSeries(
        id="ts-good",
        name="vellio_2",
        mrc_path=good_tilt_path,
        linked_batch_position_id=batch.id,
        mrc_metadata=MrcMetadata(path=good_tilt_path, size_bytes=4, nz=35),
    )
    search_map.linked_batch_position_ids.append(batch.id)
    search_map.linked_tilt_series_ids.append(good_tilt.id)
    sample = Sample(
        id="sample-vellio",
        name="1. vellio",
        path=tmp_path,
        search_maps=[search_map],
        batch_positions=[batch],
        tilt_series=[failed_tilt, good_tilt],
    )
    session = Session(
        id="session-vellio",
        name="data collection 1. vellio",
        path=tmp_path,
        kind=SessionKind.COLLECTION,
        samples=[sample],
    )

    model = session_dashboard_model(session)

    row = model.search_map_overview.rows[0]
    assert row.status == "failed"
    assert row.acquisition_associated is True
    assert row.failed_tilt_series == 1
    assert row.failed_batch_groups == 1
    assert row.planned == 2
    assert row.acquired == 1
    assert row.batch_positions == 2
    assert row.acquired_batch_positions == 1
    assert row.summary == "1/2 batch positions"
    completion = model.search_map_completion[0]
    assert completion.failed_tilt_series == 1
    assert completion.failed_batch_groups == 1
    assert completion.planned == 2
    assert completion.acquired == 1


def test_search_map_overview_warns_when_failed_batch_search_map_is_unresolved(tmp_path: Path) -> None:
    missing_map_path = tmp_path / "SearchMap_20260320_022428.mrc"
    linked_map_path = tmp_path / "SearchMap_20260320_022806.mrc"
    missing_map_path.write_bytes(b"stub")
    linked_map_path.write_bytes(b"stub")
    missing_map = SearchMap(
        id="sm-missing",
        name="SearchMap_20260320_022428",
        mrc_path=missing_map_path,
    )
    linked_map = SearchMap(
        id="sm-linked",
        name="SearchMap_20260320_022806",
        mrc_path=linked_map_path,
    )
    batches = [
        BatchPosition(id="vellio_2", name="vellio_2", linked_search_map_id=linked_map.id),
        BatchPosition(id="vellio_3", name="vellio_3", linked_search_map_id=linked_map.id),
    ]
    tilts: list[TiltSeries] = []
    for name, actual, batch in (
        ("vellio_1", 1, None),
        ("vellio_1_2", 1, None),
        ("vellio_1_3", 0, None),
        ("vellio_2", 35, batches[0]),
        ("vellio_2_2", 35, batches[0]),
        ("vellio_2_3", 35, batches[0]),
        ("vellio_2_4", 35, batches[0]),
        ("vellio_3", 35, batches[1]),
        ("vellio_3_2", 35, batches[1]),
    ):
        mrc_path = tmp_path / f"{name}.mrc"
        mrc_path.write_bytes(b"stub")
        tilt = TiltSeries(
            id=name,
            name=name,
            mrc_path=mrc_path,
            mrc_metadata=MrcMetadata(path=mrc_path, size_bytes=4, nz=actual),
            linked_batch_position_id=batch.id if batch is not None else None,
        )
        tilts.append(tilt)
        if batch is not None:
            batch.linked_tilt_series_ids.append(tilt.id)
            linked_map.linked_tilt_series_ids.append(tilt.id)
    linked_map.linked_batch_position_ids.extend([batch.id for batch in batches])
    sample = Sample(
        id="sample-vellio",
        name="1. vellio",
        path=tmp_path,
        search_maps=[missing_map, linked_map],
        batch_positions=batches,
        tilt_series=tilts,
    )
    session = Session(
        id="session-vellio",
        name="data collection 1. vellio",
        path=tmp_path,
        kind=SessionKind.COLLECTION,
        samples=[sample],
    )

    model = session_dashboard_model(session)

    missing_row = next(row for row in model.search_map_overview.rows if row.id == missing_map.id)
    assert missing_row.summary == "N/A"
    assert missing_row.acquisition_associated is False
    linked_row = next(row for row in model.search_map_overview.rows if row.id == linked_map.id)
    assert linked_row.summary == "2/2 batch positions"
    warning_messages = [row.message for row in model.warnings]
    assert any("Failed batch vellio_1 could not be associated with a search map" in message for message in warning_messages)
    assert any("vellio_1_3" in message for message in warning_messages)


def test_timeline_status_stays_at_tilt_series_granularity(tmp_path: Path) -> None:
    session = _large_session(tmp_path, search_maps=10, batch_positions=5, tilt_series=25)

    model = session_dashboard_model(session)
    failed_timeline_items = [
        item for item in model.timeline_items.values() if item.status == "failed"
    ]

    assert len(failed_timeline_items) == model.outcomes.failed
    assert len(failed_timeline_items) < len(model.timeline_items)
    assert model.timeline_items["ts-0"].status == "failed"
    assert model.timeline_items["ts-1"].status == "complete"


def test_applied_defocus_model_uses_mdoc_defocus_and_time(tmp_path: Path) -> None:
    session = _defocus_session(tmp_path, include_times=True)

    model = session_dashboard_model(session).applied_defocus

    assert model.x_mode == "absolute_time"
    assert model.x_axis_label == "Acquisition time"
    assert model.total_tilt_series == 2
    assert model.defocus_tilt_series == 2
    assert model.timestamp_tilt_series == 2
    assert len(model.points) == 4
    assert [point.frame_index for point in model.points] == [1, 2, 1, 2]
    assert [point.applied_defocus_um for point in model.points] == [-2.0, -2.2, -3.0, -3.2]
    assert model.points[0].metadata_source == "MDOC Defocus"
    assert model.points[1].metadata_source == "MDOC TargetDefocus"
    assert model.points[0].tilt_angle == -60.0
    assert model.points[0].tooltip is None
    assert "not measured CTF defocus" in applied_defocus_point_tooltip(model.points[0])


def test_applied_defocus_model_falls_back_to_frame_order_without_timestamps(tmp_path: Path) -> None:
    session = _defocus_session(tmp_path, include_times=False)

    model = session_dashboard_model(session).applied_defocus

    assert model.x_mode == "frame_order"
    assert model.x_axis_label == "Frame order"
    assert model.note == "Acquisition timestamps unavailable; points are shown in frame order."
    assert [point.frame_order for point in model.points] == [1, 2, 3, 4]
    assert "Frame order 1" in applied_defocus_point_tooltip(model.points[0])


def test_applied_defocus_model_omits_untimestamped_points_when_time_is_available(tmp_path: Path) -> None:
    session = _defocus_session(tmp_path, include_times=True)
    session.samples[1].tilt_series[0].sections[0].metadata.pop("DateTime")
    session.samples[1].tilt_series[0].sections[1].metadata.pop("DateTime")

    model = session_dashboard_model(session).applied_defocus

    assert model.x_mode == "absolute_time"
    assert model.defocus_tilt_series == 2
    assert model.timestamp_tilt_series == 1
    assert len(model.points) == 2
    assert model.note == "Timestamps available for 1 / 2 tilt series with applied defocus."


def test_applied_defocus_model_scopes_linked_group_and_single_tilt(tmp_path: Path) -> None:
    session = _defocus_session(tmp_path, include_times=True)
    group = build_linked_sample_group("Linked A", session.samples)

    linked_model = session_dashboard_model(group).applied_defocus
    single_model = session_dashboard_model(session.samples[0].tilt_series[0]).applied_defocus

    assert {point.sample_name for point in linked_model.points} == {"Alpha", "Beta"}
    assert {point.linked_group_label for point in linked_model.points} == {"Linked A"}
    assert single_model.total_tilt_series == 1
    assert len(single_model.points) == 2
    assert {point.tilt_series_name for point in single_model.points} == {"alpha_ts"}


def test_dose_information_model_uses_mrc_camera_dose_metadata(tmp_path: Path) -> None:
    session = _dose_session(tmp_path, include_times=True)

    model = session_dashboard_model(session).dose_information

    assert model.x_mode == "absolute_time"
    assert model.x_axis_label == "Acquisition time"
    assert model.total_tilt_series == 2
    assert model.dose_tilt_series == 2
    assert model.timestamp_tilt_series == 2
    assert len(model.points) == 4
    assert model.median_dose_e_per_angstrom2 == 3.5
    assert model.min_dose_e_per_angstrom2 == 3.1
    assert model.max_dose_e_per_angstrom2 == 3.9
    assert model.status == "neutral"
    assert model.source_label == "MRC extended header"
    assert model.points[0].dose_e_per_angstrom2 == 3.1
    assert model.points[0].metadata_source == "MRC extended header"
    assert model.points[0].tooltip is None
    tooltip = dose_information_point_tooltip(model.points[0])
    assert "Source: MRC extended header" in tooltip
    assert "not cumulative tilt series dose" in tooltip


def test_dose_information_model_hides_absent_and_ignores_dose_fraction_metadata(tmp_path: Path) -> None:
    session = _dose_session(tmp_path, include_times=True)
    for sample in session.samples:
        for tilt in sample.tilt_series:
            for frame in tilt.mrc_metadata.frame_metadata:
                frame["raw_fields"] = {
                    "is_dose_fraction": True,
                    "dose_fraction_number": 4,
                }
                frame["dose"] = None

    model = session_dashboard_model(session).dose_information

    assert not model.has_points
    assert model.dose_tilt_series == 0


def test_dose_information_model_marks_partial_metadata_without_zero_fill(tmp_path: Path) -> None:
    session = _dose_session(tmp_path, include_times=False)
    session.samples[1].tilt_series[0].mrc_metadata.frame_metadata = []

    model = session_dashboard_model(session).dose_information

    assert model.has_points
    assert model.x_mode == "frame_order"
    assert model.dose_tilt_series == 1
    assert model.total_tilt_series == 2
    assert model.status == "warning"
    assert model.status_reason == "partial data"
    assert "Dose metadata available for 1/2 tilt series." in model.note
    assert all(point.dose_e_per_angstrom2 > 0 for point in model.points)


def test_dose_information_model_scopes_linked_group_entity_scope_and_single_tilt(tmp_path: Path) -> None:
    session = _dose_session(tmp_path, include_times=True)
    group = build_linked_sample_group("Linked dose", session.samples)
    sample = session.samples[0]
    search_map = sample.search_maps[0]
    batch = sample.batch_positions[0]
    tilt = sample.tilt_series[0]

    linked_model = session_dashboard_model(group).dose_information
    search_model = session_dashboard_model(
        DashboardEntityScope(
            source=search_map,
            search_maps=[search_map],
            batch_positions=[batch],
            tilt_series=[tilt],
            samples=[sample],
            sample_groups=[(sample.name, [sample])],
            title=search_map.name,
            kind="search map",
        )
    ).dose_information
    batch_model = session_dashboard_model(
        DashboardEntityScope(
            source=batch,
            search_maps=[search_map],
            batch_positions=[batch],
            tilt_series=[tilt],
            samples=[sample],
            sample_groups=[(sample.name, [sample])],
            title=batch.name,
            kind="batch position",
        )
    ).dose_information
    single_model = session_dashboard_model(tilt).dose_information

    assert {point.sample_name for point in linked_model.points} == {"Alpha", "Beta"}
    assert {point.linked_group_label for point in linked_model.points} == {"Linked dose"}
    assert search_model.total_tilt_series == 1
    assert batch_model.total_tilt_series == 1
    assert single_model.total_tilt_series == 1
    assert len(search_model.points) == 2
    assert len(batch_model.points) == 2
    assert len(single_model.points) == 2
    assert {point.tilt_series_name for point in single_model.points} == {"alpha_ts"}


def test_applied_defocus_widget_uses_adaptive_point_styles() -> None:
    _app()
    plot = AppliedDefocusScatterPlot()

    assert plot.accessibleName() == "Applied defocus plot"
    assert "Double-click" in plot.accessibleDescription()

    plot.set_model(_plot_model_with_points(500))
    assert plot.point_style() == (3.2, 210)

    plot.set_model(_plot_model_with_points(501))
    assert plot.point_style() == (2.2, 145)

    plot.set_model(_plot_model_with_points(3001))
    assert plot.point_style() == (1.4, 88)

    assert plot.color_for_label("Sample 00").name() == plot.color_for_label("Sample 00").name()


def test_applied_defocus_uses_shared_timeline_time_formatter() -> None:
    _app()
    plot = AppliedDefocusScatterPlot()
    model = _plot_model_with_points(2)
    start = model.points[0].acquisition_time
    end = start + timedelta(hours=2, minutes=10)
    tick = start + timedelta(minutes=43)

    assert plot._x_tick_label(model, tick.timestamp(), start.timestamp(), end.timestamp()) == format_time_axis_tick(
        tick,
        start,
        end,
    )
    assert plot._x_tick_label(model, tick.timestamp(), start.timestamp(), end.timestamp()) == "00:43"
    assert plot._x_axis_label(model) == TIME_CHART_ELAPSED_AXIS_LABEL


def test_applied_defocus_legend_sits_outside_plot_area() -> None:
    app = _app()
    plot = AppliedDefocusScatterPlot()
    plot.resize(1000, 320)
    plot.set_model(_plot_model_with_points(24, sample_count=12))
    app.processEvents()

    pixmap = QPixmap(plot.size())
    plot.render(pixmap)

    assert not pixmap.isNull()
    assert plot._last_legend_rect.isValid()
    assert plot._last_legend_rect.bottom() <= plot._last_plot_rect.top()
    assert plot._last_plot_rect.left() == TIME_CHART_LEFT_GUTTER
    assert plot._last_y_axis_label_rect.isValid()
    assert plot._last_y_axis_label_rect.left() >= 0
    assert plot._last_y_axis_label_rect.right() < plot._last_plot_rect.left()
    assert not plot._last_y_axis_label_rect.intersects(plot._last_plot_rect)


def test_applied_defocus_axis_titles_use_matching_fonts() -> None:
    app = _app()
    plot = AppliedDefocusScatterPlot()
    plot.resize(900, 320)
    plot.set_model(_plot_model_with_points(12, sample_count=2))
    app.processEvents()

    pixmap = QPixmap(plot.size())
    plot.render(pixmap)

    assert plot._last_y_axis_label_font.family() == plot._last_x_axis_label_font.family()
    assert plot._last_y_axis_label_font.pointSizeF() == plot._last_x_axis_label_font.pointSizeF()
    assert plot._last_y_axis_label_font.weight() == plot._last_x_axis_label_font.weight()
    assert plot._last_y_axis_label_font.bold() == plot._last_x_axis_label_font.bold()


def test_applied_defocus_plot_emits_tilt_series_reference_on_double_click() -> None:
    app = _app()
    plot = AppliedDefocusScatterPlot()
    plot.resize(900, 320)
    plot.set_model(_plot_model_with_points(1))
    app.processEvents()

    pixmap = QPixmap(plot.size())
    plot.render(pixmap)
    emitted: list[tuple[str, object]] = []
    plot.pointDoubleClicked.connect(lambda tilt_id, frame: emitted.append((tilt_id, frame)))

    rect, _point = plot._point_rects[0]
    event = _FakeDoubleClick(rect.center().toPoint())
    plot.mouseDoubleClickEvent(event)

    assert event.accepted
    assert emitted == [("ts-0", 1)]


def test_applied_defocus_plot_emits_tilt_series_reference_on_single_click() -> None:
    app = _app()
    plot = AppliedDefocusScatterPlot()
    plot.resize(900, 320)
    plot.set_model(_plot_model_with_points(1))
    app.processEvents()

    pixmap = QPixmap(plot.size())
    plot.render(pixmap)
    emitted: list[tuple[str, object]] = []
    plot.pointClicked.connect(lambda tilt_id, frame: emitted.append((tilt_id, frame)))

    rect, _point = plot._point_rects[0]
    event = _FakeDoubleClick(rect.center().toPoint())
    plot.mousePressEvent(event)

    assert event.accepted
    assert emitted == [("ts-0", 1)]


def test_dose_information_plot_emits_tilt_series_reference_on_double_click() -> None:
    app = _app()
    plot = DoseInformationScatterPlot()

    assert plot.accessibleName() == "Camera dose plot"
    assert "MRC metadata" in plot.accessibleDescription()

    plot.resize(900, 320)
    plot.set_model(_dose_plot_model_with_points(1))
    app.processEvents()

    pixmap = QPixmap(plot.size())
    plot.render(pixmap)
    emitted: list[tuple[str, object]] = []
    plot.pointDoubleClicked.connect(lambda tilt_id, frame: emitted.append((tilt_id, frame)))

    rect, _point = plot._point_rects[0]
    event = _FakeDoubleClick(rect.center().toPoint())
    plot.mouseDoubleClickEvent(event)

    assert event.accepted
    assert emitted == [("dose-ts-0", 1)]


def test_session_dashboard_relays_plot_click_and_double_click_requests(tmp_path: Path) -> None:
    app = _app()
    dashboard = SessionDashboard()
    dashboard.resize(1100, 900)
    dashboard.set_model(session_dashboard_model(_dose_session(tmp_path, include_times=True)))
    app.processEvents()

    clicked: list[tuple[str, object]] = []
    emitted: list[tuple[str, object]] = []
    timeline: list[tuple[str, object]] = []
    dashboard.point_clicked.connect(lambda tilt_id, frame: clicked.append((tilt_id, frame)))
    dashboard.point_double_clicked.connect(lambda tilt_id, frame: emitted.append((tilt_id, frame)))
    dashboard.timeline_tilt_series_requested.connect(lambda tilt_id, frame: timeline.append((tilt_id, frame)))
    dashboard.findChild(AppliedDefocusScatterPlot).pointClicked.emit("defocus-ts", 2)
    dashboard.findChild(DoseInformationScatterPlot).pointClicked.emit("dose-ts", 3)
    dashboard.findChild(AppliedDefocusScatterPlot).pointDoubleClicked.emit("defocus-ts", 2)
    dashboard.findChild(DoseInformationScatterPlot).pointDoubleClicked.emit("dose-ts", 3)
    dashboard._timeline_segment_clicked("timeline-ts", 4)

    assert clicked == [("defocus-ts", 2), ("dose-ts", 3)]
    assert emitted == [("defocus-ts", 2), ("dose-ts", 3)]
    assert timeline == [("timeline-ts", 4)]


def test_session_dashboard_does_not_show_header_highlight_chip(tmp_path: Path) -> None:
    app = _app()
    dashboard = SessionDashboard()
    model = session_dashboard_model(_dose_session(tmp_path, include_times=True))
    tilt_id = model.dose_information.points[0].tilt_series_id
    dashboard.set_model(model, highlighted_tilt_series_id=tilt_id)
    app.processEvents()

    chip = dashboard.findChild(QFrame, "dashboardHighlightChip")
    clear = dashboard.findChild(QToolButton, "dashboardHighlightClear")

    assert chip is None
    assert clear is None
    assert dashboard._highlighted_tilt_series_id == tilt_id


def test_session_dashboard_stacks_timeline_and_defocus_full_width(tmp_path: Path) -> None:
    app = _app()
    session = _large_session(tmp_path, search_maps=4, batch_positions=3, tilt_series=6)
    model = session_dashboard_model(session)
    dashboard = SessionDashboard()
    dashboard.resize(1100, 900)

    dashboard.set_model(model, timeline=None)
    app.processEvents()

    widgets = [
        item.widget()
        for index in range(dashboard._content_layout.count())
        if (item := dashboard._content_layout.itemAt(index)) is not None and item.widget() is not None
    ]
    count_cards = widgets[1].findChildren(StatCard)
    timeline_text = _label_texts(widgets[2])
    defocus_text = _label_texts(widgets[3])
    lower_text = _label_texts(widgets[4])

    assert ["Atlases", "Overviews", "Search maps", "Batch positions", "Tilt series"] == [
        card._model.label for card in count_cards
    ]
    assert "ACQUISITION TIMELINE" in timeline_text
    assert widgets[2].minimumHeight() >= 260
    assert "APPLIED DEFOCUS" in defocus_text
    assert "MDOC target defocus, not measured CTF" in defocus_text
    assert "0 / 6 tilt series" in defocus_text
    assert widgets[3].minimumHeight() >= 360
    assert widgets[3].findChild(AppliedDefocusScatterPlot).minimumHeight() >= 300
    assert "BATCH POSITIONS" in lower_text
    assert "SEARCH MAPS OVERVIEW" in lower_text
    assert "WARNINGS" in lower_text

    timeline_title_top, timeline_title_height = _card_title_geometry(widgets[2], "ACQUISITION TIMELINE")
    defocus_title_top, defocus_title_height = _card_title_geometry(widgets[3], "APPLIED DEFOCUS")
    assert timeline_title_top == defocus_title_top
    assert timeline_title_height == defocus_title_height

    lower_cards = [
        card
        for card in widgets[4].findChildren(QFrame)
        if card.objectName() == "dashboardCard" and card.layout() is not None
    ]
    lower_titles = [
        next(
            label.text()
            for label in card.findChildren(QLabel)
            if label.objectName() == "cardTitle"
        )
        for card in lower_cards
    ]
    assert lower_titles[:3] == ["SEARCH MAPS OVERVIEW", "BATCH POSITIONS", "WARNINGS"]
    for card in (*count_cards, widgets[2], widgets[3], *lower_cards):
        margins = card.layout().contentsMargins()
        assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == DASHBOARD_CARD_MARGINS


def test_session_dashboard_shows_dose_card_only_when_metadata_present(tmp_path: Path) -> None:
    app = _app()
    dashboard = SessionDashboard()
    dashboard.resize(1100, 900)

    empty_path = tmp_path / "empty"
    empty_path.mkdir()
    dashboard.set_model(session_dashboard_model(_large_session(empty_path, search_maps=2, batch_positions=2, tilt_series=4)))
    app.processEvents()
    assert "DOSE INFORMATION" not in _label_texts(dashboard)
    assert dashboard.findChild(DoseInformationScatterPlot) is None

    dose_path = tmp_path / "dose"
    dose_path.mkdir()
    dashboard.set_model(session_dashboard_model(_dose_session(dose_path, include_times=True)))
    app.processEvents()
    labels = _label_texts(dashboard)

    assert "DOSE INFORMATION" not in labels
    # The redundant "CAMERA DOSE PER IMAGE (e⁻/Å²)" heading was replaced
    # by the standard card_header pattern ("CAMERA DOSE" + coverage badge)
    # plus a one-line subtitle that carries the unit and metadata source.
    assert "CAMERA DOSE PER IMAGE (e⁻/Å²)" not in labels
    assert "CAMERA DOSE" in labels
    # The unit appears exactly once on the card, in the subtitle, so the
    # metric values stay clean numbers.
    assert "3.5" in labels
    assert "3.1–3.9" in labels
    assert sum(1 for label in labels if "e⁻/Å²" in label) == 1
    assert "3.5\u00a0e⁻/Å²" not in labels
    assert "3.1–3.9\u00a0e⁻/Å²" not in labels
    assert dashboard.findChild(DoseInformationScatterPlot) is not None


def test_session_dashboard_dose_range_value_does_not_elide_on_narrow_card(tmp_path: Path) -> None:
    app = _app()
    dashboard = SessionDashboard()
    dashboard.resize(520, 900)
    dashboard.set_model(session_dashboard_model(_dose_session(tmp_path, include_times=True)))
    dashboard.show()
    app.processEvents()

    labels = _label_texts(dashboard)

    assert "3.1–3.9" in labels
    assert "3..." not in labels
    median_label = _label_with_text(dashboard, "Median")
    range_label = _label_with_text(dashboard, "Range")
    assert range_label.mapTo(dashboard, QPoint(0, 0)).y() > median_label.mapTo(dashboard, QPoint(0, 0)).y()


def test_session_dashboard_dose_metrics_stay_visually_grouped_on_wide_card(tmp_path: Path) -> None:
    app = _app()
    dashboard = SessionDashboard()
    dashboard.resize(1420, 900)
    dashboard.set_model(session_dashboard_model(_dose_session(tmp_path, include_times=True)))
    dashboard.show()
    app.processEvents()

    median_title = _label_with_text(dashboard, "Median")
    range_title = _label_with_text(dashboard, "Range")
    median_x = median_title.mapTo(dashboard, QPoint(0, 0)).x()
    range_x = range_title.mapTo(dashboard, QPoint(0, 0)).x()

    assert 180 <= range_x - median_x <= 320


def test_session_dashboard_builds_dose_details_table_lazily(tmp_path: Path) -> None:
    app = _app()
    dashboard = SessionDashboard()
    dashboard.resize(1100, 900)
    dashboard.set_model(session_dashboard_model(_dose_session(tmp_path, include_times=True)))
    app.processEvents()

    table_count = len(dashboard.findChildren(QTableView, "dashboardSearchMapTable"))
    button = next(
        button
        for button in dashboard.findChildren(QToolButton, "dashboardFilterButton")
        if button.text() == "View dose details"
    )

    button.setChecked(True)
    app.processEvents()

    tables = dashboard.findChildren(QTableView, "dashboardSearchMapTable")
    assert len(tables) == table_count + 1
    assert any(table.model().rowCount() == 4 for table in tables)


def test_session_dashboard_builds_search_map_overview_table_lazily(tmp_path: Path) -> None:
    """The Search Map Overview card's table+model must be built only when
    the user opens the details toggle.

    Previously the QTableView and its model were constructed up-front and
    then hidden via ``setVisible(False)``. That added widget-build cost to
    every dashboard refresh regardless of whether the user ever opened the
    table; on multigrid sessions with many search maps it was visible in
    the dashboard rebuild latency profile. The fix mirrors the dose-details
    lazy pattern.
    """

    app = _app()
    dashboard = SessionDashboard()
    dashboard.resize(1100, 900)
    dashboard.set_model(session_dashboard_model(_large_session(tmp_path, search_maps=12, batch_positions=6, tilt_series=18)))
    app.processEvents()

    initial_tables = dashboard.findChildren(QTableView, "dashboardSearchMapTable")
    button = next(
        button
        for button in dashboard.findChildren(QToolButton, "dashboardFilterButton")
        if button.text() == "Show all search maps"
    )

    # Pre-toggle: search-map overview table must not exist yet.
    assert all(table.model() is None or table.model().rowCount() != 12 for table in initial_tables), (
        "Search map overview table was built before the user opened the details toggle. "
        "It should be lazy — see _build_search_map_overview_card."
    )

    button.setChecked(True)
    app.processEvents()

    after_tables = dashboard.findChildren(QTableView, "dashboardSearchMapTable")
    assert len(after_tables) == len(initial_tables) + 1, (
        "Toggling the search-map details should build exactly one new QTableView."
    )
    assert any(table.model() is not None and table.model().rowCount() == 12 for table in after_tables)


def test_timeline_strip_defaults_to_density_for_large_timeline() -> None:
    _app()
    start = datetime(2026, 1, 1, 9, 0, 0)
    segments = tuple(
        TimelineSegment(
            tilt_series_id=f"ts-{index}",
            label=f"TS_{index:03d}",
            start=start + timedelta(minutes=index * 3),
            end=start + timedelta(minutes=index * 3 + 2),
            sample_times=(start + timedelta(minutes=index * 3),),
        )
        for index in range(72)
    )
    strip = TimelineStrip()
    assert strip.accessibleName() == "Acquisition timeline"
    assert "status colouring" in strip.accessibleDescription()
    strip.set_timeline(SessionTimeline(segments=segments, pauses=()))

    assert strip._use_density_view(strip._timeline)  # density strip, not 72 lanes


def test_session_dashboard_preserves_timeline_mode_across_rebuild(tmp_path: Path) -> None:
    _app()
    session = _large_session(tmp_path, search_maps=4, batch_positions=4, tilt_series=8)
    model = session_dashboard_model(session)
    timeline = build_session_timeline(session)
    dashboard = SessionDashboard()

    dashboard.set_model(model, timeline=timeline)
    _app().processEvents()
    lanes_button = next(button for button in dashboard.findChildren(QToolButton) if button.text() == "Lanes")
    lanes_button.click()
    first_strip = dashboard.findChild(TimelineStrip)
    assert first_strip is not None
    assert first_strip.display_mode() == "lanes"

    dashboard.set_model(model, timeline=timeline)
    _app().processEvents()

    rebuilt_strip = dashboard.findChildren(TimelineStrip)[-1]
    rebuilt_lanes = next(button for button in dashboard.findChildren(QToolButton) if button.text() == "Lanes")
    assert rebuilt_strip is not None
    assert rebuilt_strip.display_mode() == "lanes"
    assert rebuilt_lanes.isChecked()


def test_timeline_navigation_selects_tilt_without_editing_filter(monkeypatch, tmp_path: Path) -> None:
    from tomography_session_browser.ui import main_window
    from tomography_session_browser.ui.image_viewer import VIEWER_OBJECT_ROLE
    from tomography_session_browser.ui.main_window import MainWindow

    app = _app()
    session = _dose_session(tmp_path, include_times=True)
    target = session.samples[1].tilt_series[0]
    window = MainWindow()
    window._sessions = [session]
    window._session = session
    window._active_context = session
    window._rebuild_sample_index()
    window.tabs.setCurrentIndex(main_window.TAB_LABELS.index("Session"))
    monkeypatch.setattr(main_window.ViewerTab, "_load_value", lambda self, value, slice_index: setattr(self, "_current_value", value))
    window._render_viewer_tabs()
    viewer = window._viewer_tabs["Tilt series"]
    viewer.list_filter.setText("manual filter")

    window.navigate_to_tilt_series(target.id, source="timeline")
    app.processEvents()

    assert window.tabs.currentIndex() == main_window.TAB_LABELS.index("Tilt series")
    assert viewer.list_filter.text() == "manual filter"
    assert viewer.list.currentItem().data(0, VIEWER_OBJECT_ROLE) is target
    assert window._tilt_viewer_selected_tilt_series_id == target.id
    assert window._dashboard_highlighted_tilt_series_id is None


def test_tilt_series_tab_selection_does_not_change_summary_highlight(monkeypatch, tmp_path: Path) -> None:
    from tomography_session_browser.ui import main_window
    from tomography_session_browser.ui.main_window import MainWindow

    app = _app()
    session = _dose_session(tmp_path, include_times=True)
    first = session.samples[0].tilt_series[0]
    second = session.samples[1].tilt_series[0]
    window = MainWindow()
    window._sessions = [session]
    window._session = session
    window._active_context = session
    window._rebuild_sample_index()
    window.tabs.setCurrentIndex(main_window.TAB_LABELS.index("Tilt series"))
    monkeypatch.setattr(main_window.ViewerTab, "_load_value", lambda self, value, slice_index: setattr(self, "_current_value", value))
    window._render_viewer_tabs()

    window._viewer_item_selected(first)
    app.processEvents()

    assert window._tilt_viewer_selected_tilt_series_id == first.id
    assert window._dashboard_highlighted_tilt_series_id is None

    window._set_dashboard_highlighted_tilt_series_id(first.id)
    window._viewer_item_selected(second)
    app.processEvents()

    assert window._tilt_viewer_selected_tilt_series_id == second.id
    assert window._dashboard_highlighted_tilt_series_id == first.id


def test_tree_tilt_selection_highlights_without_rescoping_dashboard(monkeypatch, tmp_path: Path) -> None:
    from tomography_session_browser.ui import main_window
    from tomography_session_browser.ui.main_window import MainWindow

    app = _app()
    session = _dose_session(tmp_path, include_times=True)
    target = session.samples[0].tilt_series[0]
    window = MainWindow()
    window._sessions = [session]
    window._session = session
    window._active_context = session
    window._rebuild_sample_index()
    window.tabs.setCurrentIndex(main_window.TAB_LABELS.index("Session"))
    monkeypatch.setattr(main_window.ViewerTab, "_load_value", lambda self, value, slice_index: setattr(self, "_current_value", value))

    window._select_from_left_tree(target, target.name)
    app.processEvents()

    assert window.tabs.currentIndex() == main_window.TAB_LABELS.index("Session")
    assert window._dashboard_scope_value() is session
    assert window._dashboard_highlighted_tilt_series_id is None

    window._set_dashboard_highlighted_tilt_series_id(target.id)

    assert window._dashboard_highlighted_tilt_series_id == target.id
    assert window.session_dashboard.findChild(QFrame, "dashboardHighlightChip") is None


def test_summary_plot_single_click_toggles_highlight_without_rebuild(monkeypatch, tmp_path: Path) -> None:
    from tomography_session_browser.ui import main_window
    from tomography_session_browser.ui.main_window import MainWindow

    app = _app()
    session = _dose_session(tmp_path, include_times=True)
    first = session.samples[0].tilt_series[0]
    second = session.samples[1].tilt_series[0]
    window = MainWindow()
    window._sessions = [session]
    window._session = session
    window._active_context = session
    window._rebuild_sample_index()
    window.tabs.setCurrentIndex(main_window.TAB_LABELS.index("Session"))
    monkeypatch.setattr(main_window.ViewerTab, "_load_value", lambda self, value, slice_index: setattr(self, "_current_value", value))
    window._render_dashboard_for_scope()
    app.processEvents()
    first_plot = window.session_dashboard.findChild(AppliedDefocusScatterPlot)

    window._on_dashboard_plot_point_clicked(first.id, 1)
    assert window._dashboard_highlighted_tilt_series_id is None
    window._dashboard_point_click_timer.stop()
    window._apply_pending_dashboard_point_click()

    assert window._dashboard_highlighted_tilt_series_id == first.id
    assert window.session_dashboard.findChild(AppliedDefocusScatterPlot) is first_plot

    window._on_dashboard_plot_point_clicked(second.id, 1)
    window._dashboard_point_click_timer.stop()
    window._apply_pending_dashboard_point_click()
    assert window._dashboard_highlighted_tilt_series_id == second.id

    window._on_dashboard_plot_point_clicked(second.id, 1)
    window._dashboard_point_click_timer.stop()
    window._apply_pending_dashboard_point_click()
    assert window._dashboard_highlighted_tilt_series_id is None


def test_summary_plot_double_click_cancels_pending_highlight(monkeypatch, tmp_path: Path) -> None:
    from tomography_session_browser.ui import main_window
    from tomography_session_browser.ui.main_window import MainWindow

    app = _app()
    session = _dose_session(tmp_path, include_times=True)
    target = session.samples[0].tilt_series[0]
    window = MainWindow()
    window._sessions = [session]
    window._session = session
    window._active_context = session
    window._rebuild_sample_index()
    window.tabs.setCurrentIndex(main_window.TAB_LABELS.index("Session"))
    monkeypatch.setattr(main_window.ViewerTab, "_load_value", lambda self, value, slice_index: setattr(self, "_current_value", value))
    window._render_viewer_tabs()

    window._on_dashboard_plot_point_clicked(target.id, 1)
    assert window._pending_dashboard_point_click == (target.id, 1)

    window._on_dashboard_plot_point_double_clicked(target.id, 1)
    app.processEvents()

    assert window._pending_dashboard_point_click is None
    assert not window._dashboard_point_click_timer.isActive()
    assert window.tabs.currentIndex() == main_window.TAB_LABELS.index("Tilt series")
    assert window._tilt_viewer_selected_tilt_series_id == target.id
    assert window._dashboard_highlighted_tilt_series_id == target.id


def test_scatter_plot_fades_unhighlighted_points() -> None:
    _app()
    plot = AppliedDefocusScatterPlot()
    model = _plot_model_with_points(2)
    plot.set_model(model)
    base_alpha = plot.point_style()[1]

    plot.set_highlighted_tilt_series_id("ts-1")

    assert plot._point_alpha(model.points[1], base_alpha) >= 235
    assert plot._point_alpha(model.points[0], base_alpha) < base_alpha


def test_timeline_density_helper_moves_to_summary_tooltip() -> None:
    app = _app()
    start = datetime(2026, 1, 1, 9, 0, 0)
    segments = tuple(
        TimelineSegment(
            tilt_series_id=f"ts-{index}",
            label=f"TS_{index:03d}",
            start=start + timedelta(minutes=index * 3),
            end=start + timedelta(minutes=index * 3 + 2),
            sample_times=(start + timedelta(minutes=index * 3),),
        )
        for index in range(72)
    )
    strip = TimelineStrip()
    strip.resize(900, 220)
    strip.set_display_mode("density")
    strip.set_timeline(SessionTimeline(segments=segments, pauses=()))
    app.processEvents()

    pixmap = QPixmap(strip.size())
    strip.render(pixmap)

    assert not pixmap.isNull()
    assert strip._summary_tooltip_text == (
        "Mode: Density strip\n"
        "Entity: Tilt series\n"
        "Bin size: 3 min\n"
        "Switch to Lanes for grouped detail."
    )
    assert strip._tooltip_at(strip._summary_tooltip_rect.center()) == strip._summary_tooltip_text
    assert strip._last_density_strip_rect.top() < 44


def test_timeline_density_y_axis_label_sits_vertically_in_gutter() -> None:
    app = _app()
    start = datetime(2026, 1, 1, 9, 0, 0)
    segments = tuple(
        TimelineSegment(
            tilt_series_id=f"ts-{index}",
            label=f"TS_{index:03d}",
            start=start + timedelta(minutes=index * 3),
            end=start + timedelta(minutes=index * 3 + 2),
            sample_times=(start + timedelta(minutes=index * 3),),
        )
        for index in range(72)
    )
    strip = TimelineStrip()
    strip.resize(900, 220)
    strip.set_display_mode("density")
    strip.set_timeline(SessionTimeline(segments=segments, pauses=()))
    app.processEvents()

    pixmap = QPixmap(strip.size())
    strip.render(pixmap)

    assert not pixmap.isNull()
    assert strip._last_density_y_axis_label_rect.isValid()
    assert strip._last_density_y_axis_label_rect.left() >= 0
    assert strip._last_density_y_axis_label_rect.right() < strip._last_density_strip_rect.left()
    assert strip._last_density_y_axis_label_rect.center().y() == strip._last_density_strip_rect.center().y()
    assert strip._last_density_y_axis_label_rect.height() > strip._last_density_y_axis_label_rect.width()


def test_timeline_segment_tooltip_fallback_does_not_crash() -> None:
    app = _app()
    start = datetime(2026, 1, 1, 9, 0, 0)
    segment = TimelineSegment(
        tilt_series_id="ts-1",
        label="TS_001",
        start=start,
        end=start + timedelta(minutes=2),
        sample_times=(start, start + timedelta(minutes=1), start + timedelta(minutes=2)),
    )
    strip = TimelineStrip()
    strip.resize(900, 220)
    strip.set_display_mode("lanes")
    strip.set_timeline(SessionTimeline(segments=(segment,), pauses=()))
    app.processEvents()

    pixmap = QPixmap(strip.size())
    strip.render(pixmap)

    assert not pixmap.isNull()
    assert strip._segment_rects
    tooltip = strip._tooltip_at(strip._segment_rects[0][0].center())
    assert tooltip == "TS_001\n2026-01-01 09:00 → 2026-01-01 09:02\n3 tilt series frames"


def test_density_bins_keep_proportional_status_counts() -> None:
    _app()
    start = datetime(2026, 1, 1, 9, 0, 0)
    segments = tuple(
        TimelineSegment(
            tilt_series_id=f"ts-{index}",
            label=f"TS_{index}",
            start=start + timedelta(minutes=index),
            end=start + timedelta(minutes=index, seconds=30),
            sample_times=(start + timedelta(minutes=index),),
        )
        for index in range(4)
    )
    strip = TimelineStrip()
    strip.set_segment_metadata(
        {
            "ts-0": SimpleNamespace(status="complete"),
            "ts-1": SimpleNamespace(status="failed"),
            "ts-2": SimpleNamespace(status="warning"),
            "ts-3": SimpleNamespace(status="unknown"),
        }
    )

    bins = strip._density_bins(
        SessionTimeline(segments=segments, pauses=()),
        start,
        4 * 60,
        1,
    )

    assert bins[0]["complete"] == 1
    assert bins[0]["failed"] == 1
    assert bins[0]["warning"] == 1
    assert bins[0]["unknown"] == 1
    assert len(bins[0]["segments"]) == 4
    assert bins[0]["tooltip"].startswith("4 acquisitions in this time bin")
    assert "Click to choose one to inspect." in bins[0]["tooltip"]
    assert "Failed: TS_1" in bins[0]["tooltip"]
    assert strip._density_axis_max(1) == 4
    assert strip._density_axis_max(3) == 5
    assert strip._density_axis_max(21) == 50


def test_timeline_highlight_fades_unrelated_density_and_lane_items() -> None:
    _app()
    start = datetime(2026, 1, 1, 9, 0, 0)
    segments = tuple(
        TimelineSegment(
            tilt_series_id=f"ts-{index}",
            label=f"TS_{index}",
            start=start + timedelta(minutes=index),
            end=start + timedelta(minutes=index, seconds=30),
            sample_times=(start + timedelta(minutes=index),),
        )
        for index in range(3)
    )
    strip = TimelineStrip()
    strip.set_segment_metadata(
        {
            "ts-0": SimpleNamespace(status="complete"),
            "ts-1": SimpleNamespace(status="failed"),
            "ts-2": SimpleNamespace(status="warning"),
        }
    )
    strip.set_highlighted_tilt_series_id("ts-1")
    colors = strip._colors()

    assert strip._segment_color(segments[1], colors).alpha() == colors["failed"].alpha()
    assert strip._segment_color(segments[0], colors).alpha() < colors["segment"].alpha()

    bins = strip._density_bins(SessionTimeline(segments=segments, pauses=()), start, 3 * 60, 1)
    highlighted_status = strip._highlight_status_for_segments(bins[0]["segments"])
    failed = strip._density_part_color(colors["failed"], "failed", highlighted_status, bins[0]["segments"])
    complete = strip._density_part_color(colors["segment"], "complete", highlighted_status, bins[0]["segments"])

    assert highlighted_status == "failed"
    assert failed.alpha() == colors["failed"].alpha()
    assert complete.alpha() < colors["segment"].alpha()
    assert "Highlighted: TS_1" in bins[0]["tooltip"]


def test_timeline_lane_highlight_routes_all_unrelated_segments_to_faded_layer() -> None:
    app = _app()
    start = datetime(2026, 1, 1, 9, 0, 0)
    faded_segments = tuple(
        TimelineSegment(
            tilt_series_id=f"a-{index}",
            label=f"A_{index}",
            start=start,
            end=start + timedelta(minutes=12),
            sample_times=(start,),
        )
        for index in range(5)
    )
    highlighted = TimelineSegment(
        tilt_series_id="b-0",
        label="B_0",
        start=start + timedelta(minutes=18),
        end=start + timedelta(minutes=30),
        sample_times=(start + timedelta(minutes=18),),
    )
    strip = TimelineStrip()
    strip.resize(900, 220)
    strip.set_display_mode("lanes")
    strip.set_segment_metadata(
        {
            **{
                segment.tilt_series_id: SimpleNamespace(status="complete", lane_key="lane-a", lane_label="Lane A")
                for segment in faded_segments
            },
            "b-0": SimpleNamespace(status="complete", lane_key="lane-b", lane_label="Lane B"),
        }
    )
    strip.set_highlighted_tilt_series_id("b-0")
    strip.set_timeline(SessionTimeline(segments=(*faded_segments, highlighted), pauses=()))
    app.processEvents()
    faded_calls: list[tuple[str, ...]] = []

    def record_faded_layer(_painter, segments, *_args) -> None:
        faded_calls.append(tuple(segment.tilt_series_id for segment in segments))

    strip._draw_faded_lane_segments_layer = record_faded_layer  # type: ignore[method-assign]

    pixmap = QPixmap(strip.size())
    pixmap.fill(Qt.GlobalColor.transparent)
    strip.render(pixmap)

    faded_ids = {tilt_id for call in faded_calls for tilt_id in call}
    assert faded_ids == {segment.tilt_series_id for segment in faded_segments}
    assert "b-0" not in faded_ids


def test_density_bin_single_acquisition_uses_lane_click_signal() -> None:
    _app()
    start = datetime(2026, 1, 1, 9, 0, 0)
    segment = TimelineSegment(
        tilt_series_id="ts-1",
        label="vellio_2_3",
        start=start,
        end=start + timedelta(minutes=2),
        sample_times=(start,),
    )
    strip = TimelineStrip()
    strip.set_segment_metadata({"ts-1": SimpleNamespace(status="failed")})
    emitted: list[tuple[str, object]] = []
    strip.segment_clicked.connect(lambda tilt_id, frame: emitted.append((tilt_id, frame)))
    payload = strip._density_bins(SessionTimeline(segments=(segment,), pauses=()), start, 120, 1)[0]

    assert strip._activate_density_payload(payload, QPoint(0, 0))
    assert emitted == [("ts-1", None)]
    assert "Click to inspect vellio_2_3." in payload["tooltip"]


def test_density_bin_multiple_acquisitions_opens_selection_menu(monkeypatch) -> None:
    _app()
    start = datetime(2026, 1, 1, 9, 0, 0)
    segments = (
        TimelineSegment(
            tilt_series_id="ts-1",
            label="vellio_2_3",
            start=start,
            end=start + timedelta(minutes=2),
            sample_times=(start,),
        ),
        TimelineSegment(
            tilt_series_id="ts-2",
            label="Rothia_2_2",
            start=start + timedelta(seconds=30),
            end=start + timedelta(minutes=2, seconds=30),
            sample_times=(start + timedelta(seconds=30),),
        ),
    )
    strip = TimelineStrip()
    strip.set_segment_metadata({"ts-1": SimpleNamespace(status="complete"), "ts-2": SimpleNamespace(status="warning")})
    emitted: list[tuple[str, object]] = []
    strip.segment_clicked.connect(lambda tilt_id, frame: emitted.append((tilt_id, frame)))
    created_menus: list[Any] = []

    class FakeAction:
        def __init__(self) -> None:
            self.triggered = self
            self._callback = None
            self.icon = None

        def connect(self, callback) -> None:
            self._callback = callback

        def setIcon(self, icon) -> None:
            self.icon = icon

        def trigger(self) -> None:
            self._callback(False)

    class FakeMenu:
        def __init__(self, _parent=None) -> None:
            self.actions: list[tuple[str, FakeAction]] = []
            self.stylesheet = ""
            self.tooltips_visible = False
            created_menus.append(self)

        def addAction(self, label: str) -> FakeAction:
            action = FakeAction()
            self.actions.append((label, action))
            return action

        def setStyleSheet(self, stylesheet: str) -> None:
            self.stylesheet = stylesheet

        def setToolTipsVisible(self, visible: bool) -> None:
            self.tooltips_visible = visible

        def exec(self, _global_pos) -> None:
            self.actions[1][1].trigger()

    monkeypatch.setattr(timeline_strip_module, "QMenu", FakeMenu)
    payload = strip._density_bins(SessionTimeline(segments=segments, pauses=()), start, 120, 1)[0]

    assert strip._activate_density_payload(payload, QPoint(0, 0))
    assert emitted == [("ts-2", None)]
    assert [label for label, _action in created_menus[0].actions] == [
        "vellio_2_3 · 2026-01-01 09:00 · complete",
        "Rothia_2_2 · 2026-01-01 09:00 · warning",
    ]
    assert current_palette().surface in created_menus[0].stylesheet
    assert current_palette().text in created_menus[0].stylesheet
    assert "QMenu::item:selected" in created_menus[0].stylesheet
    assert created_menus[0].tooltips_visible
    assert all(action.icon is not None for _label, action in created_menus[0].actions)


def test_low_zoom_overlay_view_keeps_individual_markers_visible() -> None:
    _app()
    view = ImagePreviewView()
    view.scale(0.4, 0.4)
    view.set_marker_type_visible(MarkerType.TEMPLATE_AREA, True)
    view.set_markers(
        [
            _viewer_marker("batch-1", MarkerType.BATCH_POSITION, 100, 100, linked_object_id="batch-1"),
            _viewer_marker("batch-2", MarkerType.BATCH_POSITION, 112, 105, linked_object_id="batch-2"),
            _viewer_marker("tilt-1", MarkerType.TILT_SERIES, 118, 108, linked_object_id="batch-2"),
            _viewer_marker("exposure-1", MarkerType.EXPOSURE_AREA, 100, 100, linked_object_id="batch-1"),
            _viewer_marker("tracking-1", MarkerType.TRACKING_AREA, 104, 104, linked_object_id="batch-1"),
            _viewer_marker("focus-1", MarkerType.FOCUS_AREA, 108, 108, linked_object_id="batch-1"),
            _viewer_marker("camera-1", MarkerType.CAMERA_FOV, 110, 110, linked_object_id="batch-1"),
            ImageMarker(
                id="template-1",
                marker_type=MarkerType.TEMPLATE_AREA,
                linked_object_id="batch-1",
                source_object_id="search-map-1",
                bbox=(80, 80, 40, 40),
            ),
        ]
    )

    displayed = view._display_markers()
    displayed_ids = {marker.id for marker in displayed}

    assert "batch-1" in displayed_ids
    assert "batch-2" in displayed_ids
    assert "tilt-1" in displayed_ids
    assert "exposure-1" in displayed_ids
    assert "tracking-1" in displayed_ids
    assert "focus-1" in displayed_ids
    assert "camera-1" in displayed_ids
    assert "template-1" in displayed_ids
    assert MarkerType.GROUPED not in {marker.marker_type for marker in displayed}


def test_overlay_visibility_is_stable_across_zoom_levels() -> None:
    _app()
    view = ImagePreviewView()
    view.set_marker_type_visible(MarkerType.TEMPLATE_AREA, True)
    view.set_markers(
        [
            _viewer_marker("batch-1", MarkerType.BATCH_POSITION, 100, 100, linked_object_id="batch-1"),
            _viewer_marker("tilt-1", MarkerType.TILT_SERIES, 112, 105, linked_object_id="batch-1"),
            _viewer_marker("exposure-1", MarkerType.EXPOSURE_AREA, 100, 100, linked_object_id="batch-1"),
            _viewer_marker("tracking-1", MarkerType.TRACKING_AREA, 104, 104, linked_object_id="batch-1"),
            _viewer_marker("focus-1", MarkerType.FOCUS_AREA, 108, 108, linked_object_id="batch-1"),
            _viewer_marker("camera-1", MarkerType.CAMERA_FOV, 110, 110, linked_object_id="batch-1"),
            ImageMarker(
                id="template-1",
                marker_type=MarkerType.TEMPLATE_AREA,
                linked_object_id="batch-1",
                source_object_id="search-map-1",
                bbox=(80, 80, 40, 40),
            ),
        ]
    )
    expected_ids = {marker.id for marker in view._display_markers()}

    for scale in (0.35, 0.8, 1.2, 1.8):
        view.resetTransform()
        view.scale(scale, scale)
        assert {marker.id for marker in view._display_markers()} == expected_ids


def test_marker_type_toggles_control_visibility_when_zoomed_out() -> None:
    _app()
    view = ImagePreviewView()
    view.scale(0.4, 0.4)
    view.set_markers(
        [
            _viewer_marker("batch-1", MarkerType.BATCH_POSITION, 100, 100, linked_object_id="batch-1"),
            _viewer_marker("exposure-1", MarkerType.EXPOSURE_AREA, 100, 100, linked_object_id="batch-1"),
            _viewer_marker("camera-1", MarkerType.CAMERA_FOV, 100, 100, linked_object_id="batch-1"),
        ]
    )

    assert "exposure-1" in {marker.id for marker in view._display_markers()}

    view.set_marker_type_visible(MarkerType.EXPOSURE_AREA, False)

    displayed_ids = {marker.id for marker in view._display_markers()}
    assert "batch-1" in displayed_ids
    assert "camera-1" in displayed_ids
    assert "exposure-1" not in displayed_ids


def test_dense_overlay_view_does_not_auto_hide_enabled_markers_at_low_zoom() -> None:
    _app()
    view = ImagePreviewView()
    view.scale(0.35, 0.35)
    markers = [
        _viewer_marker(f"batch-{index}", MarkerType.BATCH_POSITION, float(index), 100.0, linked_object_id=f"batch-{index}")
        for index in range(120)
    ]
    markers.extend(
        _viewer_marker(
            f"exposure-{index}",
            MarkerType.EXPOSURE_AREA,
            float(index),
            120.0,
            linked_object_id=f"batch-{index}",
        )
        for index in range(120)
    )
    view.set_markers(markers)

    displayed_ids = {marker.id for marker in view._display_markers()}

    assert len(displayed_ids) == 240
    assert "batch-0" in displayed_ids
    assert "exposure-0" in displayed_ids


def _viewer_marker(
    marker_id: str,
    marker_type: str,
    x: float,
    y: float,
    *,
    linked_object_id: str | None = None,
    selected: bool = False,
) -> ImageMarker:
    return ImageMarker(
        id=marker_id,
        marker_type=marker_type,
        linked_object_id=linked_object_id,
        source_object_id="search-map-1",
        x=x,
        y=y,
        radius=10,
        label=marker_id,
        status="complete",
        selected=selected,
    )


def _defocus_session(tmp_path: Path, *, include_times: bool) -> Session:
    samples: list[Sample] = []
    for sample_name in ("Alpha", "Beta"):
        sample_path = tmp_path / sample_name
        sample_path.mkdir()
        samples.append(Sample(id=sample_name.lower(), name=sample_name, path=sample_path))

    start = datetime(2026, 1, 1, 14, 5, 0)
    for sample_index, sample in enumerate(samples):
        mrc_path = sample.path / f"{sample.name.lower()}_ts.mrc"
        mrc_path.write_bytes(b"stub")
        sections = []
        for frame in range(2):
            metadata = {
                "TiltAngle": -60.0 + frame * 2.0,
                "Defocus" if frame == 0 else "TargetDefocus": -2.0 - sample_index - frame * 0.2,
            }
            if include_times:
                metadata["DateTime"] = (start + timedelta(minutes=sample_index * 10 + frame)).strftime(
                    "%d-%b-%Y  %H:%M:%S"
                )
            sections.append(MdocSection(z_value=frame, metadata=metadata))
        sample.tilt_series.append(
            TiltSeries(
                id=f"{sample.name.lower()}-ts",
                name=f"{sample.name.lower()}_ts",
                mrc_path=mrc_path,
                mdoc_path=mrc_path.with_suffix(".mdoc"),
                sections=sections,
                number_of_frames=len(sections),
            )
        )

    return Session(
        id="defocus-session",
        name="Defocus session",
        path=tmp_path,
        kind=SessionKind.COLLECTION,
        samples=samples,
    )


def _dose_session(tmp_path: Path, *, include_times: bool) -> Session:
    samples: list[Sample] = []
    start = datetime(2026, 1, 1, 15, 0, 0)
    for sample_index, sample_name in enumerate(("Alpha", "Beta")):
        sample_path = tmp_path / sample_name
        sample_path.mkdir()
        sample = Sample(id=sample_name.lower(), name=sample_name, path=sample_path)
        search_map = SearchMap(id=f"sm-{sample_index}", name=f"{sample.name}_SearchMap")
        batch = BatchPosition(
            id=f"bp-{sample_index}",
            name=f"{sample.name}_Position",
            linked_search_map_id=search_map.id,
        )
        search_map.linked_batch_position_ids.append(batch.id)
        sample.search_maps.append(search_map)
        sample.batch_positions.append(batch)

        mrc_path = sample.path / f"{sample.name.lower()}_ts.mrc"
        mrc_path.write_bytes(b"stub")
        dose_values = (3.1, 3.3) if sample_index == 0 else (3.7, 3.9)
        frame_metadata = []
        for frame_index, dose in enumerate(dose_values):
            frame = {
                "raw_index": frame_index,
                "tilt_angle": -60.0 + frame_index * 2.0,
                "dose": dose * 1e20,
                "metadata_source": "MRC extended header",
                "raw_fields": {
                    "dose": dose * 1e20,
                    "dose_e_per_angstrom2": dose,
                    "is_dose_fraction": False,
                },
            }
            if include_times:
                frame["timestamp"] = (start + timedelta(minutes=sample_index * 10 + frame_index)).isoformat()
            frame_metadata.append(frame)
        tilt = TiltSeries(
            id=f"{sample.name.lower()}-ts",
            name=f"{sample.name.lower()}_ts",
            mrc_path=mrc_path,
            number_of_frames=len(frame_metadata),
            mrc_metadata=MrcMetadata(
                path=mrc_path,
                size_bytes=4,
                nz=len(frame_metadata),
                is_stack=True,
                extended_header_type="FEI2",
                frame_metadata=frame_metadata,
            ),
            linked_batch_position_id=batch.id,
        )
        batch.linked_tilt_series_ids.append(tilt.id)
        search_map.linked_tilt_series_ids.append(tilt.id)
        sample.tilt_series.append(tilt)
        samples.append(sample)

    return Session(
        id="dose-session",
        name="Dose session",
        path=tmp_path,
        kind=SessionKind.COLLECTION,
        samples=samples,
    )


def _plot_model_with_points(count: int, *, sample_count: int = 1) -> AppliedDefocusPlotModel:
    start = datetime(2026, 1, 1, 14, 0, 0)
    points = [
        AppliedDefocusPointModel(
            sample_name=f"Sample {index % sample_count:02d}",
            data_collection_name=f"Sample {index % sample_count:02d}",
            linked_group_label=None,
            tilt_series_id=f"ts-{index}",
            tilt_series_name=f"ts-{index}",
            frame_index=1,
            frame_count=1,
            tilt_angle=None,
            acquisition_time=start + timedelta(seconds=index),
            frame_order=index + 1,
            applied_defocus_um=-2.0,
            metadata_source="MDOC Defocus",
            has_absolute_time=True,
            has_relative_time=False,
            tooltip="Values are microscope-applied defocus from MDOC metadata, not measured CTF defocus.",
        )
        for index in range(count)
    ]
    return AppliedDefocusPlotModel(
        points=points,
        total_tilt_series=count,
        defocus_tilt_series=count,
        timestamp_tilt_series=count,
        x_mode="absolute_time",
        x_axis_label="Acquisition time",
    )


def _dose_plot_model_with_points(count: int, *, sample_count: int = 1) -> DoseInformationPlotModel:
    start = datetime(2026, 1, 1, 15, 0, 0)
    points = [
        DoseInformationPointModel(
            sample_name=f"Sample {index % sample_count:02d}",
            data_collection_name=f"Sample {index % sample_count:02d}",
            linked_group_label=None,
            tilt_series_id=f"dose-ts-{index}",
            tilt_series_name=f"dose-ts-{index}",
            frame_index=1,
            frame_count=1,
            tilt_angle=None,
            acquisition_time=start + timedelta(seconds=index),
            frame_order=index + 1,
            dose_e_per_angstrom2=3.1,
            metadata_source="MRC extended header",
            has_absolute_time=True,
            tooltip="Source: MRC extended header",
        )
        for index in range(count)
    ]
    return DoseInformationPlotModel(
        points=points,
        total_tilt_series=count,
        dose_tilt_series=count,
        timestamp_tilt_series=count,
        x_mode="absolute_time",
        x_axis_label="Acquisition time",
        median_dose_e_per_angstrom2=3.1,
        min_dose_e_per_angstrom2=3.1,
        max_dose_e_per_angstrom2=3.1,
    )


class _FakeDoubleClick:
    def __init__(self, pos: QPoint) -> None:
        self._pos = pos
        self.accepted = False

    def button(self) -> Qt.MouseButton:
        return Qt.MouseButton.LeftButton

    def pos(self) -> QPoint:
        return self._pos

    def globalPos(self) -> QPoint:
        return self._pos

    def accept(self) -> None:
        self.accepted = True


def _large_session(tmp_path: Path, *, search_maps: int, batch_positions: int, tilt_series: int) -> Session:
    samples: list[Sample] = []
    tilt_index = 0
    for sample_index in range(13):
        sample_path = tmp_path / f"Sample{sample_index:02d}"
        sample_path.mkdir()
        sample = Sample(id=f"sample-{sample_index}", name=f"Sample {sample_index}", path=sample_path)
        samples.append(sample)

    maps = [
        SearchMap(id=f"sm-{index}", name=f"SearchMap_{index:02d}", linked_batch_position_ids=[])
        for index in range(search_maps)
    ]
    batches: list[BatchPosition] = []
    tilts: list[TiltSeries] = []
    for batch_index in range(batch_positions):
        search_map = maps[batch_index % len(maps)]
        batch = BatchPosition(
            id=f"bp-{batch_index}",
            name=f"Position_{batch_index:03d}",
            status="Acquired",
            linked_search_map_id=search_map.id,
        )
        search_map.linked_batch_position_ids.append(batch.id)
        batches.append(batch)
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
            tilts.append(tilt)
            samples[batch_index % len(samples)].tilt_series.append(tilt)
            tilt_index += 1

    session = Session(
        id="large",
        name="large",
        path=tmp_path,
        kind=SessionKind.COLLECTION,
        samples=samples,
    )
    # Keep top-level lists empty as real collection sessions usually carry data
    # on samples; the presenter aggregates from samples.
    for index, search_map in enumerate(maps):
        samples[index % len(samples)].search_maps.append(search_map)
    return session


def _app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _label_texts(widget) -> list[str]:
    return [label.text() for label in widget.findChildren(QLabel)]


def _label_with_text(widget, text: str) -> QLabel:
    for label in widget.findChildren(QLabel):
        if label.text() == text:
            return label
    raise AssertionError(f"Missing label {text!r}")


def _card_title_geometry(widget, title: str) -> tuple[int, int]:
    for label in widget.findChildren(QLabel):
        if label.objectName() == "cardTitle" and label.text() == title:
            return label.y(), label.height()
    raise AssertionError(f"Missing card title {title!r}")
