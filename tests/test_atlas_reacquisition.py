"""A re-acquired atlas: the Atlas folder holds several ``Atlas_<id>`` mosaics.

Tomography 5 keeps the earlier acquisition when a grid's atlas is recollected.
``Atlas.dm`` names the current atlas, and its node table (which the overlay
projection is fitted to) describes that mosaic only, so the parser must take
the image, XML and tiles from it. Before this, the first atlas on disk was
shown with the current atlas's node table.
"""

from __future__ import annotations

from pathlib import Path
import struct

import pytest

from tomography_session_browser.parsers.atlas_parser import parse_atlas_folder
from tomography_session_browser.reports.warning_summary import summarise_warnings
from tomography_session_browser.services.item_status import atlas_tile_count
from tomography_session_browser.services.marker_service import (
    MarkerContext,
    _atlas_pixel_affine,
    _image_frame,
    atlas_lod_markers,
    atlas_markers,
)
from tomography_session_browser.ui.session_presenter import describe_object, warning_presentations

_DM_HEADER = (
    '<AtlasSessionXml xmlns="http://schemas.datacontract.org/2004/07/'
    'Applications.SciencesAppsShared.GridAtlas.Persistence" '
    'xmlns:i="http://www.w3.org/2001/XMLSchema-instance">'
)


def _write_atlas_dm(folder: Path, current: str | None) -> None:
    if current is None:
        reference = '<AtlasImageReference i:nil="true"/>'
    else:
        reference = (
            '<AtlasImageReference xmlns:a="http://schemas.datacontract.org/2004/07/'
            'Fei.Applications.Common.Types">'
            f"<a:BaseFileName>{current}</a:BaseFileName><a:FileFormat>Mrc</a:FileFormat>"
            "</AtlasImageReference>"
        )
    (folder / "Atlas.dm").write_text(
        f"{_DM_HEADER}<Atlas>{reference}</Atlas></AtlasSessionXml>",
        encoding="utf-8",
    )


def _write_mrc(path: Path, nx: int, ny: int) -> None:
    header = bytearray(1024)
    struct.pack_into("<4i", header, 0, nx, ny, 1, 0)
    struct.pack_into("<3f", header, 40, float(nx), float(ny), 1.0)
    struct.pack_into("<3i", header, 64, 1, 2, 3)
    path.write_bytes(bytes(header) + bytes(nx * ny))


def _write_atlas(folder: Path, stem: str, nx: int, ny: int) -> None:
    _write_mrc(folder / f"{stem}.mrc", nx, ny)
    (folder / f"{stem}.xml").write_text(f"<MicroscopeImage><name>{stem}</name></MicroscopeImage>", encoding="utf-8")


def _write_tile(folder: Path, stem: str) -> None:
    (folder / f"{stem}.mrc").write_bytes(b"")
    (folder / f"{stem}.xml").write_text("<MicroscopeImage/>", encoding="utf-8")


def _reacquired_folder(tmp_path: Path, current: str | None) -> Path:
    folder = tmp_path / "Atlas"
    folder.mkdir()
    _write_atlas(folder, "Atlas_99", 8, 8)
    _write_atlas(folder, "Atlas_100", 16, 12)
    _write_tile(folder, "Tile_101_0_99")
    _write_tile(folder, "Tile_102_1_99")
    _write_tile(folder, "Tile_201_0_100")
    _write_tile(folder, "Tile_202_1_100")
    _write_tile(folder, "Tile_203_2_100")
    _write_atlas_dm(folder, current)
    return folder


def test_reacquired_atlas_uses_the_atlas_named_in_atlas_dm(tmp_path: Path) -> None:
    atlas = parse_atlas_folder(_reacquired_folder(tmp_path, "Atlas_100"))

    assert atlas is not None
    assert atlas.image_path.name == "Atlas_100.mrc"
    assert (atlas.mrc_metadata.nx, atlas.mrc_metadata.ny) == (16, 12)
    assert "Atlas_100.xml" in atlas.metadata
    assert "Atlas_99.xml" not in atlas.metadata
    tiles = {"Tile_201_0_100", "Tile_202_1_100", "Tile_203_2_100"}
    assert {path.stem for path in atlas.tile_paths} == tiles
    assert {path.stem for path in atlas.tile_metadata_paths} == tiles
    assert atlas.warnings == [
        "Atlas was re-acquired: showing Atlas_100 (the current atlas in Atlas.dm); "
        "other atlas Atlas_99 not displayed."
    ]


def test_atlas_dm_reference_takes_precedence_over_the_latest_atlas(tmp_path: Path) -> None:
    # Atlas.dm's node table describes the atlas it names, so that atlas is
    # shown even when a later acquisition is also on disk.
    atlas = parse_atlas_folder(_reacquired_folder(tmp_path, "Atlas_99"))

    assert atlas.image_path.name == "Atlas_99.mrc"
    assert {path.stem for path in atlas.tile_paths} == {"Tile_101_0_99", "Tile_102_1_99"}


def test_without_a_reference_the_latest_acquisition_is_used(tmp_path: Path) -> None:
    # Atlas IDs increase with acquisition time; natural order puts Atlas_100
    # after Atlas_99 (plain string order would not).
    atlas = parse_atlas_folder(_reacquired_folder(tmp_path, None))

    assert atlas.image_path.name == "Atlas_100.mrc"
    assert atlas.warnings == [
        "Atlas was re-acquired: showing Atlas_100 (the latest acquisition); "
        "other atlas Atlas_99 not displayed."
    ]


def test_missing_referenced_atlas_falls_back_to_the_latest_with_a_warning(tmp_path: Path) -> None:
    atlas = parse_atlas_folder(_reacquired_folder(tmp_path, "Atlas_300"))

    assert atlas.image_path.name == "Atlas_100.mrc"
    assert atlas.warnings[0] == (
        "Atlas.dm names Atlas_300 as the current atlas but its image was not found "
        "in the Atlas folder; showing the latest atlas on disk, Atlas_100."
    )


def test_reacquisition_warnings_classify_without_reading_as_failures(tmp_path: Path) -> None:
    missing = parse_atlas_folder(_reacquired_folder(tmp_path, "Atlas_300")).warnings
    summaries = {summary.category: summary.severity for summary in summarise_warnings(missing)}

    # The note that an earlier atlas is hidden is informational; a missing
    # referenced image is an ordinary atlas-file warning.
    assert summaries == {
        "Atlas files not found": "warning",
        "Atlas re-acquired": "info",
        "Atlas markers unavailable": "info",
    }


def test_reacquisition_has_its_own_category_on_the_dashboard_and_in_the_pdf(tmp_path: Path) -> None:
    # Regression: the note fell into the catch-all "Other" bucket on both.
    warnings = parse_atlas_folder(_reacquired_folder(tmp_path, "Atlas_100")).warnings
    scoped = [f"Sample1 / Atlas: {warning}" for warning in warnings]

    for batch in (warnings, scoped):
        rows = summarise_warnings(batch)
        presentations = warning_presentations(batch)
        assert [(row.category, row.severity) for row in rows] == [("Atlas re-acquired", "info")]
        assert [presentation.condition for presentation in presentations] == ["Atlas re-acquired"]
        assert presentations[0].why_it_matters == rows[0].explanation


def test_single_atlas_keeps_every_tile_and_adds_no_warning(tmp_path: Path) -> None:
    folder = tmp_path / "Atlas"
    folder.mkdir()
    _write_atlas(folder, "Atlas_100", 16, 12)
    _write_tile(folder, "Tile_201_0_100")
    _write_tile(folder, "Tile_7")  # legacy naming without an atlas ID
    _write_atlas_dm(folder, "Atlas_100")

    atlas = parse_atlas_folder(folder)

    assert atlas.image_path.name == "Atlas_100.mrc"
    assert {path.stem for path in atlas.tile_paths} == {"Tile_201_0_100", "Tile_7"}
    assert atlas.warnings == []


@pytest.mark.parametrize("has_nodes", [False, True])
def test_fallback_image_cannot_use_another_acquisitions_projection(tmp_path: Path, has_nodes: bool) -> None:
    folder = _reacquired_folder(tmp_path, "Atlas_100")
    nodes = "".join(
        f"<TileXml><StagePosition><X>{x * 1e-6}</X><Y>{y * 1e-6}</Y></StagePosition>"
        f"<AtlasPixelPosition><x>{x * 4 + 2}</x><y>{y * 4 + 2}</y>"
        "<width>2</width><height>2</height></AtlasPixelPosition></TileXml>"
        for x, y in ((0, 0), (1, 0), (0, 1), (1, 1))
    ) if has_nodes else ""
    dm = folder / "Atlas.dm"
    dm.write_text(dm.read_text().replace(
        "</Atlas>",
        "<StagePosition><X>0</X><Y>0</Y></StagePosition>"
        "<pixelSize><x>0.000001</x><y>0.000001</y></pixelSize>"
        f"<TilesEfficient>{nodes}</TilesEfficient></Atlas>",
    ))
    matched = parse_atlas_folder(folder)
    assert _image_frame(matched) is not None, "both valid affine and legacy frames still work"
    assert (_atlas_pixel_affine(matched) is not None) == has_nodes

    dm.write_text(dm.read_text().replace("<a:BaseFileName>Atlas_100", "<a:BaseFileName>Atlas_300"))
    before = dm.read_bytes()
    fallback = parse_atlas_folder(folder)
    assert fallback.image_path == matched.image_path
    assert fallback.image_path.exists(), "keep the fallback preview available"
    assert _atlas_pixel_affine(fallback) is None
    assert _image_frame(fallback) is None, "do not substitute legacy calibration from the wrong acquisition"
    warning = next(w for w in fallback.warnings if w.startswith("Atlas markers unavailable:"))
    assert "Atlas_300" in warning and "Atlas_100" in warning
    assert warning in describe_object(fallback), "the reason is inline in the metadata panel"
    for _ in range(2):
        assert atlas_markers(fallback, MarkerContext()) == []
        assert atlas_lod_markers(fallback, MarkerContext(), include_detail_markers=False) == []
    assert fallback.warnings.count(warning) == 1
    assert dm.read_bytes() == before


def test_tiles_from_an_acquisition_without_its_mosaic_are_excluded(tmp_path: Path) -> None:
    folder = tmp_path / "Atlas"
    folder.mkdir()
    _write_atlas(folder, "Atlas_100", 16, 12)
    _write_atlas_dm(folder, "Atlas_100")
    for stem in ("Tile_201_0_100", "Tile_101_0_99", "Tile_7"):
        _write_tile(folder, stem)
        (folder / f"{stem}.jpg").write_bytes(b"preview")

    atlas = parse_atlas_folder(folder)

    expected = {"Tile_201_0_100", "Tile_7"}
    assert {p.stem for p in atlas.tile_paths} == expected
    assert {p.stem for p in atlas.tile_metadata_paths} == expected
    assert atlas_tile_count(atlas) == 2
    assert (folder / "Tile_101_0_99.xml").exists(), "filter in memory; never remove source files"


def test_jpg_scale_uses_only_tiles_from_the_displayed_acquisition(tmp_path: Path, monkeypatch) -> None:
    from tomography_session_browser.parsers import atlas_parser

    folder = tmp_path / "Atlas"
    folder.mkdir()
    (folder / "Atlas_100.jpg").write_bytes(b"preview")
    _write_atlas_dm(folder, "Atlas_100")
    _write_tile(folder, "Tile_201_0_100")
    _write_tile(folder, "Tile_101_0_99")
    sampled: list[Path] = []

    def scale(paths, dimensions):
        sampled.extend(paths)
        return 1e-6, 1e-6

    monkeypatch.setattr(atlas_parser, "_read_image_dimensions", lambda _: (16, 12))
    monkeypatch.setattr(atlas_parser, "_compute_effective_mosaic_pixel_size", scale)
    atlas = parse_atlas_folder(folder)

    assert [p.stem for p in sampled] == ["Tile_201_0_100"]
    assert atlas.metadata["AtlasMosaicPixelSize"] == {"x": 1e-6, "y": 1e-6}
