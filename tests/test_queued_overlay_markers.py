from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QGraphicsEllipseItem
from reportlab.lib import colors

from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.domain.models import (
    BatchPosition,
    MrcMetadata,
    Overview,
    SearchMap,
    SearchTile,
    TiltSeries,
)
from tomography_session_browser.reports.graphics import OverlayedImage
from tomography_session_browser.services.atlas_marker_style import (
    PRINT_STATUS_COLOURS,
)
from tomography_session_browser.services.marker_service import (
    BEAM_DIAMETER_EXPOSURE_MRC_CONTEXT,
    BEAM_DIAMETER_MICROSCOPE_CONTEXT,
    MarkerContext,
    overview_markers,
    search_map_markers,
    search_tile_markers,
)
from tomography_session_browser.ui.image_viewer import ImagePreviewView, _marker_colors
from tomography_session_browser.ui.theme import current_palette


TARGET_PIXEL_SIZE_M = 1.0e-8
COLLECTED_BEAM_DIAMETER_M = 4.2e-6


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class _ReportCanvas:
    def __init__(self) -> None:
        self.stroke_colours = []

    def setStrokeColor(self, colour) -> None:  # noqa: N802
        self.stroke_colours.append(colour)

    def setLineWidth(self, *_args) -> None:  # noqa: N802
        pass

    def setDash(self, *_args) -> None:  # noqa: N802
        pass

    def circle(self, *_args, **_kwargs) -> None:
        pass


def _mrc_metadata(
    name: str,
    *,
    size: tuple[int, int],
    pixel_size: float,
    stage_position: tuple[float, float] = (0.0, 0.0),
    nz: int = 1,
) -> MrcMetadata:
    return MrcMetadata(
        path=Path(name),
        size_bytes=0,
        nx=size[0],
        ny=size[1],
        nz=nz,
        voxel_size=(pixel_size, pixel_size, None),
        frame_metadata=[
            {
                "stage_x": stage_position[0],
                "stage_y": stage_position[1],
                "raw_fields": {"pixel_size_x": pixel_size},
            }
        ],
    )


def _queued_batch(*, linked_search_map_id: str = "search-map") -> BatchPosition:
    return BatchPosition(
        id="queued-batch",
        name="Position_queued",
        status="Queued",
        linked_search_map_id=linked_search_map_id,
        linked_overview_id="overview",
        metadata={
            "PositionOnTileSet": {
                "StagePositionX": 0.0,
                "StagePositionY": 0.0,
            },
            "ExposureTemplateAreaParameters": {
                "Name": "Exposure",
                "PositionX": 0.0,
                "PositionY": 0.0,
            },
            "TrackingTemplateAreaParameters": {
                "Name": "Tracking",
                "PositionX": 1.0e-7,
                "PositionY": 0.0,
            },
            "FocusTemplateAreaParameters": {
                "Name": "Focus",
                "PositionX": 1.0e-7,
                "PositionY": 0.0,
            },
            # This is the wrong search-state fallback that previously sized
            # queued markers. It must not win over collected exposure data.
            "BeamDiameter": 1.1347672289078868e-5,
            "BeamDiameterContext": BEAM_DIAMETER_MICROSCOPE_CONTEXT,
            "BeamDiameterSource": "search.xml microscopeData/optics/BeamDiameter",
        },
    )


def _collected_reference(
    tmp_path: Path,
    *,
    include_beam: bool,
) -> tuple[BatchPosition, TiltSeries]:
    tilt_path = tmp_path / "collected.mrc"
    tilt_path.write_bytes(b"complete")
    tilt = TiltSeries(
        id="collected-tilt",
        name="Collected tilt",
        mrc_path=tilt_path,
        mrc_metadata=_mrc_metadata(
            "collected-stack.mrc",
            size=(64, 64),
            pixel_size=2.0e-10,
            nz=35,
        ),
        linked_batch_position_id="collected-batch",
    )
    metadata = {
        "ExposureTemplateAreaParameters": {
            "Name": "Exposure",
            "PositionX": 0.0,
            "PositionY": 0.0,
        }
    }
    if include_beam:
        metadata.update(
            {
                "BeamDiameter": COLLECTED_BEAM_DIAMETER_M,
                "BeamDiameterContext": BEAM_DIAMETER_EXPOSURE_MRC_CONTEXT,
                "BeamDiameterSource": (
                    "collected_Exposure.mrc FEI extended header illuminated_area"
                ),
            }
        )
    batch = BatchPosition(
        id="collected-batch",
        name="Position_collected",
        status="Acquired",
        exposure_image_path=Path("collected_Exposure.mrc"),
        exposure_image_paths=[Path("collected_Exposure.mrc")],
        exposure_mrc_metadata=_mrc_metadata(
            "collected_Exposure.mrc",
            size=(400, 300),
            pixel_size=2.0e-9,
        ),
        metadata=metadata,
        linked_tilt_series_ids=[tilt.id],
        linked_search_map_id="other-search-map",
    )
    return batch, tilt


def _search_map() -> SearchMap:
    return SearchMap(
        id="search-map",
        name="Search map",
        mrc_metadata=_mrc_metadata(
            "search-map.mrc",
            size=(1000, 1000),
            pixel_size=TARGET_PIXEL_SIZE_M,
        ),
    )


def _exposure(markers: list[ImageMarker]) -> ImageMarker:
    return next(
        marker
        for marker in markers
        if marker.marker_type == MarkerType.EXPOSURE_AREA
        and marker.linked_object_id == "queued-batch"
    )


def test_queued_search_map_uses_successful_tilt_series_beam_and_hollow_blue_style(
    tmp_path: Path,
) -> None:
    queued = _queued_batch()
    collected, tilt = _collected_reference(tmp_path, include_beam=True)

    markers = search_map_markers(
        _search_map(),
        [queued, collected],
        tilt_series=[tilt],
    )
    exposure = _exposure(markers)

    assert exposure.radius == pytest.approx(
        (COLLECTED_BEAM_DIAMETER_M / 2.0) / TARGET_PIXEL_SIZE_M
    )
    assert exposure.status == "queued"
    assert exposure.metadata["queued_position"] is True
    assert exposure.metadata["radius_source"] == "successful_tilt_series_exposure_mrc"
    assert exposure.metadata["queued_beam_source_batch_id"] == collected.id
    assert "search.xml" not in str(exposure.metadata["queued_beam_source"])

    pen, fill = _marker_colors(exposure)
    assert pen.name().lower() == current_palette().atlas_marker_queued.lower()
    assert fill.alpha() == 0


def test_qt_scene_renders_queued_exposure_as_hollow_but_focus_as_filled() -> None:
    _app()
    view = ImagePreviewView()
    image = QImage(160, 100, QImage.Format.Format_RGB32)
    image.fill(0)
    queued = ImageMarker(
        id="queued",
        marker_type=MarkerType.EXPOSURE_AREA,
        linked_object_id="batch",
        source_object_id="search-map",
        x=50.0,
        y=50.0,
        radius=20.0,
        status="queued",
        metadata={"queued_position": True},
    )
    focus = ImageMarker(
        id="focus",
        marker_type=MarkerType.FOCUS_AREA,
        linked_object_id="batch",
        source_object_id="search-map",
        x=110.0,
        y=50.0,
        radius=20.0,
    )

    view.set_image(image)
    view.set_markers([queued, focus])
    shapes = {
        item.data(0).id: item
        for item in view._marker_items
        if isinstance(item, QGraphicsEllipseItem)
        and isinstance(item.data(0), ImageMarker)
    }

    assert shapes["queued"].pen().color().name().lower() == (
        current_palette().atlas_marker_queued.lower()
    )
    assert shapes["queued"].brush().color().alpha() == 0
    assert shapes["focus"].pen().color().name().lower() == (
        current_palette().marker_focus.lower()
    )
    assert shapes["focus"].brush().color().alpha() > 0


def test_pdf_overlay_uses_the_queued_atlas_colour() -> None:
    queued = ImageMarker(
        id="queued",
        marker_type=MarkerType.EXPOSURE_AREA,
        linked_object_id="batch",
        source_object_id="search-map",
        x=50.0,
        y=50.0,
        radius=20.0,
        status="queued",
        metadata={"queued_position": True},
    )
    flowable = object.__new__(OverlayedImage)
    flowable.canv = _ReportCanvas()
    flowable.markers = [queued]
    flowable._draw_h = 100

    flowable._draw_markers(1.0, 1.0)

    expected = colors.HexColor(PRINT_STATUS_COLOURS["queued"])
    assert flowable.canv.stroke_colours == [expected]


def test_queued_overview_uses_the_same_successful_beam_reference(
    tmp_path: Path,
) -> None:
    queued = _queued_batch()
    collected, tilt = _collected_reference(tmp_path, include_beam=True)
    overview = Overview(
        id="overview",
        name="Overview",
        image_path=Path("overview.jpg"),
        mrc_metadata=_mrc_metadata(
            "overview.mrc",
            size=(1000, 1000),
            pixel_size=TARGET_PIXEL_SIZE_M,
        ),
    )

    markers = overview_markers(
        overview,
        MarkerContext(
            overviews=(overview,),
            batch_positions=(queued, collected),
            tilt_series=(tilt,),
        ),
    )

    assert _exposure(markers).radius == pytest.approx(
        (COLLECTED_BEAM_DIAMETER_M / 2.0) / TARGET_PIXEL_SIZE_M
    )


def test_queued_search_tile_replaces_unknown_beam_with_hollow_camera_fov(
    tmp_path: Path,
) -> None:
    queued = _queued_batch(linked_search_map_id="")
    collected, tilt = _collected_reference(tmp_path, include_beam=False)
    search_tile = SearchTile(
        id="search-tile",
        name="Search tile",
        batch_position_id=queued.id,
        mrc_metadata=_mrc_metadata(
            "search-tile.mrc",
            size=(1000, 1000),
            pixel_size=TARGET_PIXEL_SIZE_M,
        ),
    )

    markers = search_tile_markers(
        search_tile,
        [queued, collected],
        tilt_series=[tilt],
    )
    exposure = _exposure(markers)
    camera = next(
        marker
        for marker in markers
        if marker.marker_type == MarkerType.CAMERA_FOV
        and marker.linked_object_id == queued.id
    )

    assert exposure.visible is False
    assert exposure.metadata["queued_camera_fov_replacement"] is True
    assert camera.bbox is not None
    assert camera.bbox[2:] == pytest.approx((80.0, 60.0))
    assert camera.status == "queued"
    assert camera.metadata["queued_fov_fallback"] is True
    assert camera.metadata["filled_fallback"] is False
    assert camera.metadata["queued_camera_source_batch_id"] == collected.id

    pen, fill = _marker_colors(camera)
    assert pen.name().lower() == current_palette().atlas_marker_queued.lower()
    assert fill.alpha() == 0
