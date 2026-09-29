from __future__ import annotations

from pathlib import Path

from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.domain.models import Atlas, BatchPosition, Overview, SearchMap
from tomography_session_browser.reports import report_generator
from tomography_session_browser.reports.graphics import AtlasMarkerPrintLegend


def test_pdf_atlas_figure_uses_status_aware_leaf_source_and_print_legend(
    monkeypatch,
) -> None:
    atlas = Atlas(id="atlas", image_path=Path("atlas.jpg"))
    existing = [
        ImageMarker(
            id=f"marker-{index}",
            marker_type=MarkerType.BATCH_POSITION,
            linked_object_id=f"batch-{index}",
            source_object_id=atlas.id,
            x=float(index),
            y=float(index),
        )
        for index in range(3)
    ]
    captured: dict[str, object] = {}

    def status_source(value, context, *, include_detail_markers):
        assert value is atlas
        captured["context"] = context
        captured["include_detail_markers"] = include_detail_markers
        return existing

    class FakeOverlay:
        def __init__(self, _path, *, native_size, markers, max_width, max_height):
            captured["markers"] = list(markers)

    monkeypatch.setattr(report_generator, "_embeddable_image_path", lambda _path: Path("atlas.jpg"))
    monkeypatch.setattr(report_generator, "atlas_lod_markers", status_source)
    monkeypatch.setattr(
        report_generator,
        "_resolve_overlay_geometry",
        lambda _atlas, markers, _embed: ((100, 100), markers),
    )
    monkeypatch.setattr(report_generator.graphics, "OverlayedImage", FakeOverlay)
    monkeypatch.setattr(
        report_generator.graphics,
        "keep_with_caption",
        lambda items, caption: (items, caption),
    )
    monkeypatch.setattr(
        report_generator.graphics,
        "AtlasMarkerPrintLegend",
        lambda: "print-legend",
    )

    report_generator._atlas_image_block(
        atlas,
        label="Atlas",
        generation_warnings=[],
        scope_overviews=[Overview("ov", "Overview", Path("ov.jpg"))],
        scope_search_maps=[SearchMap("sm", "Search map")],
        scope_batch_positions=[BatchPosition("batch")],
    )

    assert [marker.id for marker in captured["markers"]] == [
        "marker-0",
        "marker-1",
        "marker-2",
    ]
    assert captured["include_detail_markers"] is False


def test_print_legend_advertises_its_real_layout_size() -> None:
    legend = AtlasMarkerPrintLegend()
    assert legend.wrap(500, 500) == (340.0, 132.0)


def test_pdf_fallback_atlas_explains_why_its_overlays_are_unavailable(tmp_path, monkeypatch) -> None:
    from PIL import Image

    image_path = tmp_path / "Atlas_100.jpg"
    Image.new("RGB", (16, 12), "grey").save(image_path)
    atlas = Atlas(
        id="atlas",
        image_path=image_path,
        metadata={"Atlas.dm": {"AtlasImageReference": {"BaseFileName": "Atlas_300"}}},
    )
    monkeypatch.setattr(report_generator.graphics, "keep_with_caption", lambda items, caption: (items, caption))

    [(items, caption)] = report_generator._atlas_image_block(
        atlas, label="Fallback atlas", generation_warnings=[],
        scope_overviews=[Overview("ov", "Overview", Path("ov.jpg"))],
    )

    assert "Atlas markers unavailable:" in caption
    assert "Atlas_300" in caption and "Atlas_100" in caption
    assert "1 overview marker" not in caption
    assert not any(isinstance(item, AtlasMarkerPrintLegend) for item in items)
