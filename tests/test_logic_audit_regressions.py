"""Cross-layer regressions for the September scientific-information audit."""
from __future__ import annotations

import os
import struct
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from tomography_session_browser.domain.models import (
    BatchPosition, MdocSection, MrcMetadata, Overview, Sample, Session, TiltSeries,
)
from tomography_session_browser.parsers.batch_parser import parse_batch_folder
from tomography_session_browser.parsers.session_scanner import SessionScanner
from tomography_session_browser.parsers.tiltseries_parser import parse_tilt_series
from tomography_session_browser.services.acquisition_metadata import acquisition_setting_value
from tomography_session_browser.services.batch_position_status import aggregate_batch_position_status
from tomography_session_browser.services.item_status import build_item_status_context, item_list_status
from tomography_session_browser.services.marker_service import MarkerContext
from tomography_session_browser.services.tilt_angle_service import (
    stack_order_mdoc_section_indices, stack_order_tilt_angles, tilt_angle_metadata_warnings,
)
from tomography_session_browser.services.tilt_series_validation import validate_tilt_series
from tomography_session_browser.ui.image_viewer import PreviewSources, ViewerTab, ViewerViewState
from tomography_session_browser.ui.main_window import MainWindow, TAB_LABELS
from tomography_session_browser.ui.session_presenter import (
    build_linked_sample_group, session_dashboard_model,
)


def _write_stack(path: Path, declared: int, present: int) -> None:
    header = bytearray(1024)
    struct.pack_into("<4i", header, 0, 4, 4, declared, 2)
    struct.pack_into("<3i", header, 28, 4, 4, declared)
    struct.pack_into("<3f", header, 40, 4, 4, declared)
    struct.pack_into("<3i", header, 64, 1, 2, 3)
    header[208:212] = b"MAP "
    header[212:216] = b"DA\x00\x00"
    path.write_bytes(header + bytes(4 * 4 * present * 4))


@pytest.mark.parametrize("declared,present,sections,expected_status,actual", [
    (2, 2, 35, "FAILED", 2),
    (35, 35, 2, "COMPLETE", 35),
    (35, 2, 35, "FAILED", 2),
    (35, 20, 35, "INCOMPLETE", 20),
    (35, 35, 35, "COMPLETE", 35),
])
def test_stack_evidence_controls_acquisition_count(tmp_path, declared, present, sections, expected_status, actual):
    path = tmp_path / "target.mrc"
    _write_stack(path, declared, present)
    mdoc = path.with_suffix(".mdoc")
    mdoc.write_text("TiltStart = -34\nTiltEnd = 34\nTiltStep = 2\n" + "".join(
        f"[ZValue = {i}]\nTiltAngle = {-34 + 2*i}\n" for i in range(sections)
    ))
    tilt = parse_tilt_series(path, mdoc)
    result = validate_tilt_series(tilt)
    assert result.actual_count == actual
    assert tilt.tilt_count == tilt.number_of_frames == actual
    assert result.status.upper() == expected_status
    context = build_item_status_context(tilt_series=[tilt])
    assert f"{actual}/35" in item_list_status(tilt, context).summary
    assert session_dashboard_model(tilt).outcomes.complete == (expected_status == "COMPLETE")


def test_unreadable_stack_does_not_claim_complete_from_sidecar(tmp_path):
    path = tmp_path / "broken.mrc"
    path.write_bytes(b"bad header")
    mdoc = path.with_suffix(".mdoc")
    mdoc.write_text("TiltStart = -34\nTiltEnd = 34\nTiltStep = 2\n" + "".join(
        f"[ZValue = {i}]\nTiltAngle = {-34+2*i}\n" for i in range(35)
    ))
    assert validate_tilt_series(parse_tilt_series(path, mdoc)).status.upper() != "COMPLETE"


def _batch_folder(path: Path, body: str) -> list[BatchPosition]:
    path.mkdir(parents=True)
    (path / "BatchPositionsList.xml").write_text(f"<Root>{body}</Root>")
    return parse_batch_folder(path)


def test_identical_batch_names_keep_separate_links_and_gui_contexts(tmp_path):
    batches = [_batch_folder(tmp_path / label / "Batch", """
        <BatchPositionParameters><Name>target_1</Name></BatchPositionParameters>
    """)[0] for label in ("Sample1", "Sample2")]
    assert batches[0].id != batches[1].id
    samples = []
    for i, batch in enumerate(batches):
        path = tmp_path / f"Sample{i+1}"
        tilt = TiltSeries(id=f"t{i}", name="target_1", mrc_path=path / "target_1.mrc")
        sample = Sample(id=f"s{i}", name=f"Sample{i+1}", path=path, batch_positions=[batch], tilt_series=[tilt])
        SessionScanner()._link_batch_positions_to_tilt_series(sample)
        assert tilt.linked_batch_position_id == batch.id
        samples.append(sample)
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        window._sessions = [Session(id="session", name="collection", path=tmp_path, samples=samples)]
        window._rebuild_sample_index()
        for batch, sample in zip(batches, samples):
            context = window._marker_context_for(batch, MarkerContext())
            assert context.batch_positions == tuple(sample.batch_positions)
            assert context.tilt_series == tuple(sample.tilt_series)
    finally:
        window.close()


def test_position_settings_are_not_global_defaults(tmp_path):
    path = tmp_path / "Batch"
    _batch_folder(path, """
      <BatchPositionParameters><Name>A</Name></BatchPositionParameters>
      <BatchPositionParameters><Name>B</Name><AcquisitionSpotIndex>9</AcquisitionSpotIndex></BatchPositionParameters>
    """)
    (path / "batchpositions.xml").write_text("""<Root><DetectorType>GlobalCamera</DetectorType>
      <BatchPosition><Name>A</Name><AcquisitionSpotIndex>7</AcquisitionSpotIndex><ProbeMode>Nanoprobe</ProbeMode></BatchPosition>
    </Root>""")
    first, second = parse_batch_folder(path)
    assert acquisition_setting_value(first.metadata, "Acquisition spot size") == "7"
    assert acquisition_setting_value(second.metadata, "Acquisition spot size") == "9"
    assert acquisition_setting_value(second.metadata, "Probe mode") is None
    assert acquisition_setting_value(second.metadata, "Detector") == "GlobalCamera"


def _angle_tilt(tmp_path, angles):
    path = tmp_path / "angles.mrc"
    return TiltSeries(id="angles", name="angles", mrc_path=path,
        metadata={"_sections": ["Tomography 5"], "ImageFile": path.name},
        mrc_metadata=MrcMetadata(path=path, size_bytes=0, nz=len(angles),
            tilt_angles=list(angles), tilt_angle_source="MRC extended header",
            frame_metadata=[{"tilt_angle": angle} for angle in angles]),
        sections=[MdocSection(z_value=i, metadata={"TiltAngle": angle, "Defocus": -(i+1),
            "TargetDefocus": -(i+11), "DateTime": f"2026-01-01 10:00:0{i}"})
            for i, angle in enumerate([0, 3, -3, -6, 6])])


@pytest.mark.parametrize("angles", [[6, 3, 0, -3, -6], [0, 6, -6, 3, -3], [-6, -3, 0, 3, 6]])
def test_defocus_metadata_follows_authoritative_frame_order(tmp_path, angles):
    tilt = _angle_tilt(tmp_path, angles)
    points = session_dashboard_model(tilt).defocus_readout.points
    by_frame = {p.frame_index: p for p in points}
    for i, angle in enumerate(angles, 1):
        section = next(s for s in tilt.sections if s.metadata["TiltAngle"] == angle)
        point = by_frame[i]
        assert point.tilt_angle == angle
        assert point.defocus_um == section.metadata["Defocus"]
        assert point.target_defocus_um == section.metadata["TargetDefocus"]
        assert point.acquisition_time.second == section.z_value


@pytest.mark.parametrize("angles", [[6, 3, 0, -3, -7], [6, 3, 0, -3, -3]])
def test_conflicting_or_ambiguous_mdoc_mapping_is_unavailable(tmp_path, angles):
    tilt = _angle_tilt(tmp_path, angles)
    assert stack_order_mdoc_section_indices(tilt) is None
    assert not session_dashboard_model(tilt).defocus_readout.points
    assert any("correspondence" in warning for warning in tilt_angle_metadata_warnings(tilt))


@pytest.mark.parametrize("angles", [[-999., 0., 999.], [float("nan"), 0., 3.], [0., float("inf"), 3.]])
def test_invalid_mdoc_angles_are_rejected_with_warning(tmp_path, angles):
    tilt = TiltSeries(id="t", name="t", mrc_path=tmp_path / "t.mrc",
        sections=[MdocSection(z_value=i, metadata={"TiltAngle": a}) for i, a in enumerate(angles)])
    assert stack_order_tilt_angles(tilt) is None
    assert any("invalid" in w for w in tilt_angle_metadata_warnings(tilt))
    assert validate_tilt_series(tilt).min_tilt is None
    assert validate_tilt_series(tilt).max_tilt is None
    _write_stack(tilt.mrc_path, len(angles), len(angles))
    mdoc = tilt.mrc_path.with_suffix(".mdoc")
    mdoc.write_text("".join(f"[ZValue = {i}]\nTiltAngle = {angle}\n" for i, angle in enumerate(angles)))
    parsed = parse_tilt_series(tilt.mrc_path, mdoc)
    assert parsed.tilt_range is None
    assert any("invalid" in warning for warning in parsed.warnings)
    tilt.mrc_path.with_suffix(".tlt").write_text("-3\n0\n3\n")
    assert stack_order_tilt_angles(tilt)[0] == [-3, 0, 3]


@pytest.mark.parametrize("angles", [[0.], [0., 0., 0.]])
def test_single_and_constant_valid_mdoc_angles_remain_available(tmp_path, angles):
    tilt = TiltSeries(id="t", name="t", mrc_path=tmp_path / "t.mrc",
        sections=[MdocSection(z_value=i, metadata={"TiltAngle": a}) for i, a in enumerate(angles)])
    assert stack_order_tilt_angles(tilt)[0] == angles


def _failed_sample(tmp_path):
    path = tmp_path / "target_1.mrc"
    _write_stack(path, 2, 2)
    tilt = parse_tilt_series(path)
    batch = BatchPosition(id="b", name="target_1", status="Acquired", linked_tilt_series_ids=[tilt.id])
    return Sample(id="s", name="Grid", path=tmp_path, batch_positions=[batch], tilt_series=[tilt])


def test_warning_and_batch_card_outcomes_survive_scope_changes(tmp_path):
    sample = _failed_sample(tmp_path)
    session = Session(id="session", name="Collection", path=tmp_path, samples=[sample])
    group = build_linked_sample_group("Grid", [sample])
    for scope in (session, sample, group, [session]):
        model = session_dashboard_model(scope)
        assert any("No matching MDOC" in w.message for w in model.warnings)
        batch_card = next(c for c in model.counts if c.label == "Batch positions")
        assert batch_card.items[0].status == "failed"


def test_atlas_status_does_not_attach_inferred_orphan(tmp_path):
    sample = _failed_sample(tmp_path)
    batch = sample.batch_positions[0]
    batch.linked_tilt_series_ids.clear()
    batch.status = "Pending"
    batch.metadata = {"ExposureTemplateAreaParameters": {}}
    ctx = build_item_status_context(batch_positions=[batch], tilt_series=sample.tilt_series)
    assert ctx.inferred_batch_labels
    result = aggregate_batch_position_status(batch, ctx)
    assert result.status == "queued"
    assert result.acquired == result.failed == 0
    batch.status = "Failed"
    assert aggregate_batch_position_status(batch, ctx).status == "failed"


@pytest.mark.parametrize("index", [None, 0, 4])
@pytest.mark.parametrize("lazy", [False, True])
@pytest.mark.parametrize("has_angles", [False, True])
def test_explicit_frame_restore_wins_over_default(tmp_path, monkeypatch, index, lazy, has_angles):
    app = QApplication.instance() or QApplication([])
    tilt = _angle_tilt(tmp_path, [-6, -3, 0, 3, 6])
    if not has_angles:
        tilt.mrc_metadata.tilt_angles.clear()
        tilt.sections.clear()
    viewer = ViewerTab("empty", show_list=False, show_tilt_controls=True)
    loads = []
    monkeypatch.setattr(viewer, "_load_path", lambda _path, _fallback, frame: loads.append(frame))
    viewer.set_items([tilt], lambda _: PreviewSources(tilt.mrc_path), lambda _: "tilt",
        restore_state=ViewerViewState(object_id=tilt.id, frame_index=index), auto_load_preview=not lazy)
    if lazy:
        viewer.ensure_initial_preview_loaded()
    assert loads[-1] == (2 if index is None else index)
    viewer.close()


def test_hidden_tilt_frame_notification_preserves_visible_context(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    tilt = _angle_tilt(tmp_path, [-6, -3, 0, 3, 6])
    try:
        viewer = window._viewer_tabs["Tilt series"]
        viewer._current_value = tilt
        viewer._current_path = tilt.mrc_path
        viewer._request_id = 100
        viewer.slice_slider.blockSignals(True)
        viewer.slice_slider.setRange(0, 4)
        viewer.slice_slider.setValue(2)
        viewer.slice_slider.blockSignals(False)
        monkeypatch.setattr(viewer, "_prefetch_neighbors", lambda *args: None)
        monkeypatch.setattr(viewer, "_prefetch_current_high_detail", lambda *args: None)
        window.tabs.setCurrentIndex(TAB_LABELS.index("Overview"))
        window._set_context("Overview", Overview(id="o", name="Current Overview", image_path=tmp_path / "o.jpg"))
        calls = []
        window.context_panel.set_text = lambda text: calls.append(text)
        image = QImage(4, 4, QImage.Format.Format_Grayscale8)
        image.fill(128)
        viewer._mrc_loaded(100, tilt.mrc_path, 2, image, 5, [], 16)
        assert calls == []
        assert viewer._displayed_slice_index == 2
        window.tabs.setCurrentIndex(TAB_LABELS.index("Tilt series"))
        calls.clear()
        window._viewer_frame_changed(tilt, 2, 5)
        assert any("Raw stack index: 2" in text for text in calls)
    finally:
        window.close()
