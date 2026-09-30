"""Generate README screenshots using synthetic, non-scientific demo data.

Run from the repository root inside the project Conda environment:

    python tools/generate_readme_screenshots.py

The script never reads microscope data. It builds in-memory domain objects and
temporary preview images, renders the real PySide6 widgets, and writes the
screenshots under ``docs/images/readme``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import os
from pathlib import Path
import shutil
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from PySide6.QtCore import QCoreApplication, QEvent, QThreadPool
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.models import (
    Atlas,
    BatchPosition,
    MdocSection,
    MrcMetadata,
    Overview,
    Sample,
    SearchMap,
    SearchTile,
    Session,
    TiltSeries,
)
from tomography_session_browser.domain.markers import MarkerType
from tomography_session_browser.parsers.session_scanner import SessionScanner
from tomography_session_browser.services.marker_service import (
    BEAM_DIAMETER_EXPOSURE_MRC_CONTEXT,
    search_map_markers,
)
from tomography_session_browser.services.settings_service import Settings
from tomography_session_browser.ui import main_window as main_window_module
from tomography_session_browser.ui.load_sessions_dialog import LoadSessionsDialog
from tomography_session_browser.ui.main_window import MainWindow, TAB_LABELS
from tomography_session_browser.ui.report_scope import ReportScopeDialog
from tomography_session_browser.ui import theme as theme_module
from tomography_session_browser.ui.theme import apply_theme, palette_for


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIRECTORY = REPOSITORY_ROOT / "docs" / "images" / "readme"
TEMP_DIRECTORY = REPOSITORY_ROOT / "tmp" / "readme_screenshot_demo"


def _font(size: int) -> ImageFont.ImageFont:
    for candidate in (
        "C:/Windows/Fonts/segoeui.ttf",
        "/System/Library/Fonts/SFNS.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _synthetic_micrograph(path: Path, *, seed: int, title: str, width: int = 1500, height: int = 950) -> None:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width]
    field = np.zeros((height, width), dtype=np.float32)
    for _ in range(34):
        centre_x = rng.uniform(0, width)
        centre_y = rng.uniform(0, height)
        radius = rng.uniform(28, 130)
        amplitude = rng.uniform(35, 115)
        distance = ((xx - centre_x) ** 2 + (yy - centre_y) ** 2) / (2 * radius**2)
        field += amplitude * np.exp(-distance)
    field += rng.normal(0, 12, size=(height, width))
    field -= field.min()
    field = 25 + 190 * field / max(field.max(), 1)
    image = Image.fromarray(field.astype(np.uint8), mode="L").convert("RGB")
    draw = ImageDraw.Draw(image, "RGBA")
    draw.rectangle((0, 0, width, 68), fill=(8, 18, 28, 220))
    draw.text((24, 16), f"SYNTHETIC DEMO · {title}", fill=(232, 244, 252, 255), font=_font(28))
    for _ in range(14):
        x = int(rng.uniform(60, width - 60))
        y = int(rng.uniform(110, height - 60))
        radius = int(rng.uniform(14, 42))
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=(61, 211, 192, 175), width=3)
    image.save(path, quality=92)


#: The synthetic search map: one grid square of holey carbon at 40 nm/px
#: (96 x 64 um), the scale of a Tomography 5 search-map montage.
SEARCH_MAP_SIZE = (2400, 1600)
SEARCH_MAP_PIXEL_SIZE_M = 40e-9
_HOLE_PITCH_PX = 100.0  # 4 um: an R2/2-style holey film
_HOLE_RADIUS_PX = 25.0  # 2 um holes
_HOLE_LATTICE_ANGLE = np.deg2rad(7.0)
#: Where the three demo targets sit, as fractions of the image: two in thin
#: ice, the failed one in the thicker ice towards the lower left.
_TARGET_FRACTIONS = ((0.40, 0.36), (0.66, 0.52), (0.24, 0.74))


def _smooth_noise(rng: np.random.Generator, shape: tuple[int, int], cell: int) -> np.ndarray:
    """Low-frequency noise in about [-1, 1]: a coarse random grid, upsampled."""

    height, width = shape
    coarse = rng.normal(0.0, 1.0, size=(height // cell + 3, width // cell + 3)).astype(np.float32)
    image = Image.fromarray(coarse, mode="F").resize((width + 2 * cell, height + 2 * cell), Image.Resampling.BICUBIC)
    field = np.asarray(image, dtype=np.float32)[cell : cell + height, cell : cell + width]
    return field / max(float(np.abs(field).max()), 1e-6)


def _hole_centre_near(x: float, y: float) -> tuple[float, float]:
    """The centre of the lattice hole nearest to image point (x, y)."""

    cos_a, sin_a = np.cos(_HOLE_LATTICE_ANGLE), np.sin(_HOLE_LATTICE_ANGLE)
    u = round((x * cos_a + y * sin_a) / _HOLE_PITCH_PX) * _HOLE_PITCH_PX
    v = round((-x * sin_a + y * cos_a) / _HOLE_PITCH_PX) * _HOLE_PITCH_PX
    return float(u * cos_a - v * sin_a), float(u * sin_a + v * cos_a)


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(np.clip(values, -60.0, 60.0)))


def _paint_cell(
    image: np.ndarray,
    in_hole: np.ndarray,
    centre: tuple[float, float],
    angle: float,
    length: float,
    width: float,
    darkness: float,
) -> None:
    """Darken a rod-shaped cell (a capsule with a membrane rim) into ``image``.

    A cell over a hole has far more contrast than one lying on carbon.
    """

    half = length / 2.0 + width
    x0 = max(int(centre[0] - half - 4), 0)
    x1 = min(int(centre[0] + half + 4), image.shape[1])
    y0 = max(int(centre[1] - half - 4), 0)
    y1 = min(int(centre[1] + half + 4), image.shape[0])
    if x0 >= x1 or y0 >= y1:
        return
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    along = (xx - centre[0]) * np.cos(angle) + (yy - centre[1]) * np.sin(angle)
    across = -(xx - centre[0]) * np.sin(angle) + (yy - centre[1]) * np.cos(angle)
    core = np.clip(along, -(length / 2.0 - width / 2.0), length / 2.0 - width / 2.0)
    distance = np.hypot(along - core, across) - width / 2.0
    inside = _sigmoid(distance / 1.8)
    rim = np.exp(-((distance + 1.5) ** 2) / (2 * 1.6**2))
    # Denser towards the long axis, like a cell's cytoplasm.
    density = 0.8 + 0.2 * np.clip(1.0 - np.abs(across) / (width / 2.0), 0.0, 1.0)
    contrast = darkness * (0.45 + 0.55 * in_hole[y0:y1, x0:x1])
    image[y0:y1, x0:x1] *= (1.0 - contrast * inside * density) * (1.0 - 0.14 * rim * (0.4 + 0.6 * in_hole[y0:y1, x0:x1]))


def _synthetic_search_map(path: Path, *, seed: int = 4207) -> list[tuple[float, float]]:
    """Write a plausible synthetic search map; return the demo targets' pixels.

    Entirely procedural, no microscope data: a grid square between copper
    bars, a holey carbon film whose ice thickens towards the lower left, rod-
    shaped cells lying across it, ice contamination, a torn patch of film and
    the faint tile seams of a montage. Each target is a cell over a hole.
    """

    width, height = SEARCH_MAP_SIZE
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)

    # The grid square: rounded, slightly rotated, bars at the top and sides.
    grid_angle = np.deg2rad(2.5)
    centre_x, centre_y = width / 2.0 + 10.0, height / 2.0 + 470.0
    gx = (xx - centre_x) * np.cos(grid_angle) + (yy - centre_y) * np.sin(grid_angle)
    gy = -(xx - centre_x) * np.sin(grid_angle) + (yy - centre_y) * np.cos(grid_angle)
    half, corner = 1135.0, 90.0
    qx, qy = np.abs(gx) - (half - corner), np.abs(gy) - (half - corner)
    edge = np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0) - corner
    open_area = _sigmoid(edge / 2.5)

    # Ice thickness (0 thin .. 1 thick): thin to the upper right, thicker
    # towards the lower left and against the bars, where the meniscus sits.
    thickness = 0.42 - 0.26 * (xx - width / 2.0) / (width / 2.0) + 0.22 * (yy - height / 2.0) / (height / 2.0)
    thickness += 0.42 * np.exp(np.clip(edge, -1e4, 0.0) / 230.0)
    thickness += 0.16 * _smooth_noise(rng, (height, width), 170)
    thickness = np.clip(thickness, 0.0, 1.0)

    film = 0.40 - 0.16 * thickness
    film += 0.03 * _smooth_noise(rng, (height, width), 70) + 0.018 * _smooth_noise(rng, (height, width), 14)

    # Holes: a square lattice; each hole has its own ice, a few are broken.
    cos_a, sin_a = np.cos(_HOLE_LATTICE_ANGLE), np.sin(_HOLE_LATTICE_ANGLE)
    u = xx * cos_a + yy * sin_a
    v = -xx * sin_a + yy * cos_a
    iu = np.round(u / _HOLE_PITCH_PX).astype(np.int32)
    iv = np.round(v / _HOLE_PITCH_PX).astype(np.int32)
    radial = np.hypot(u - iu * _HOLE_PITCH_PX, v - iv * _HOLE_PITCH_PX) / _HOLE_RADIUS_PX
    span = (int(iu.min()), int(iv.min()))
    table_shape = (int(iu.max()) - span[0] + 1, int(iv.max()) - span[1] + 1)
    hole_offset = rng.normal(0.0, 0.09, size=table_shape).astype(np.float32)
    broken = rng.random(size=table_shape) < 0.015
    local_thickness = np.clip(thickness + hole_offset[iu - span[0], iv - span[1]], 0.0, 1.1)
    # Where the ice is thinnest some holes hold none at all.
    empty = broken[iu - span[0], iv - span[1]] | (local_thickness < 0.06)
    hole_ice = 0.94 - 0.62 * local_thickness
    hole_ice *= 1.0 - 0.22 * np.clip(radial, 0, 1) ** 5  # the thicker meniscus at the rim
    hole_ice = np.where(empty, 0.97, hole_ice)
    in_hole = _sigmoid((radial - 1.0) * _HOLE_RADIUS_PX / 1.1)
    image = film * (1.0 - in_hole) + hole_ice * in_hole

    # A torn patch of film near the lower right: bare grid behind a jagged,
    # slightly curled carbon edge.
    tear_x, tear_y = width * 0.83, height * 0.87
    tear = np.hypot((xx - tear_x) / 115.0, (yy - tear_y) / 70.0)
    tear += 0.3 * _smooth_noise(rng, (height, width), 30) + 0.12 * _smooth_noise(rng, (height, width), 9)
    torn = _sigmoid((tear - 1.0) * 40.0)
    torn_rim = np.exp(-((tear - 1.03) ** 2) / 0.0012)
    image = image * (1.0 - torn) + 0.96 * torn
    image *= 1.0 - 0.35 * torn_rim * (1.0 - torn)
    in_hole = np.maximum(in_hole, torn)

    # Cells: one over each target hole; the rest partly in loose clumps, as
    # cells settle on a grid, partly scattered.
    targets = [_hole_centre_near(fx * width, fy * height) for fx, fy in _TARGET_FRACTIONS]
    cells = [(target, rng.uniform(0, np.pi), rng.uniform(80, 105), rng.uniform(19, 23)) for target in targets]
    clumps = [(rng.uniform(300, width - 300), rng.uniform(400, height - 200)) for _ in range(4)]
    while len(cells) < 42:
        if rng.random() < 0.45:
            clump = clumps[int(rng.integers(len(clumps)))]
            centre = (float(clump[0] + rng.normal(0, 110)), float(clump[1] + rng.normal(0, 80)))
        else:
            centre = (float(rng.uniform(100, width - 100)), float(rng.uniform(230, height - 40)))
        if np.hypot(centre[0] - tear_x, centre[1] - tear_y) < 170:
            continue
        if min(np.hypot(centre[0] - tx, centre[1] - ty) for tx, ty in targets) < 90:
            continue  # each target is one clear cell
        cells.append((centre, rng.uniform(0, np.pi), rng.uniform(50, 120), rng.uniform(15, 23)))
    for index, (centre, angle, length, cell_width) in enumerate(cells):
        _paint_cell(image, in_hole, centre, angle, length, cell_width, darkness=rng.uniform(0.44, 0.56))
        if index >= len(targets) and rng.random() < 0.2:  # a dividing pair
            offset = length + 4.0
            twin = (centre[0] + offset * np.cos(angle), centre[1] + offset * np.sin(angle))
            _paint_cell(image, in_hole, twin, angle + rng.normal(0, 0.12), length * 0.9, cell_width, darkness=0.5)

    # Ice contamination: small dense crystals, a few larger lumps.
    for _ in range(70):
        cx, cy = rng.uniform(0, width), rng.uniform(150, height)
        radius = rng.uniform(2.0, 7.0) if rng.random() < 0.85 else rng.uniform(10.0, 22.0)
        x0, x1 = max(int(cx - radius * 3), 0), min(int(cx + radius * 3), width)
        y0, y1 = max(int(cy - radius * 3), 0), min(int(cy + radius * 3), height)
        if x0 >= x1 or y0 >= y1:
            continue
        local = np.hypot(xx[y0:y1, x0:x1] - cx, yy[y0:y1, x0:x1] - cy) / radius
        image[y0:y1, x0:x1] *= 1.0 - rng.uniform(0.45, 0.75) * _sigmoid((local - 1.0) * 6.0)

    # Grid bars: opaque copper with a little texture.
    bars = 0.035 + 0.012 * _smooth_noise(rng, (height, width), 30)
    image = image * open_area + bars * (1.0 - open_area)

    # Montage: 3 x 2 tiles, each with its own gain and a faint vignette.
    tile_w, tile_h = width / 3.0, height / 2.0
    column = np.minimum((xx / tile_w).astype(np.int32), 2)
    row = np.minimum((yy / tile_h).astype(np.int32), 1)
    gains = 1.0 + rng.normal(0.0, 0.018, size=(2, 3)).astype(np.float32)
    local_x = (xx - (column + 0.5) * tile_w) / (tile_w / 2.0)
    local_y = (yy - (row + 0.5) * tile_h) / (tile_h / 2.0)
    image *= gains[row, column] * (1.0 - 0.035 * (local_x**2 + local_y**2))

    image += rng.normal(0.0, 0.022, size=image.shape).astype(np.float32)
    pixels = (14 + 226 * np.clip(image, 0.0, 1.0) ** 0.92).astype(np.uint8)
    picture = Image.fromarray(pixels, mode="L").filter(ImageFilter.GaussianBlur(0.7)).convert("RGB")
    # Top centre, on the bar: the viewer's badges and controls cover the corners.
    draw = ImageDraw.Draw(picture, "RGBA")
    label, font = "SYNTHETIC IMAGE · NOT MICROSCOPE DATA", _font(26)
    draw.text(((width - draw.textlength(label, font=font)) / 2, 30), label, fill=(210, 220, 228, 170), font=font)
    picture.save(path, quality=93)
    return targets


def _search_map_stage_offset(pixel: tuple[float, float]) -> tuple[float, float]:
    """The stage offset (m) from the map centre that the app draws at ``pixel``.

    The inverse of the app's search-map projection, including the Tomography 5
    point reflection through the image centre (``marker_service``). ``main``
    checks the round trip, so a change there fails here, not in the picture.
    """

    width, height = SEARCH_MAP_SIZE
    return (
        (width / 2.0 - 1.0 - pixel[0]) * SEARCH_MAP_PIXEL_SIZE_M,
        (pixel[1] - height / 2.0 + 1.0) * SEARCH_MAP_PIXEL_SIZE_M,
    )


def _prepare_temporary_files() -> tuple[dict[str, Path], list[tuple[float, float]]]:
    if TEMP_DIRECTORY.exists():
        shutil.rmtree(TEMP_DIRECTORY)
    preview_directory = TEMP_DIRECTORY / "previews"
    preview_directory.mkdir(parents=True)

    paths = {
        "atlas": preview_directory / "synthetic_atlas.jpg",
        "overview": preview_directory / "synthetic_overview.jpg",
        "search_map": preview_directory / "synthetic_search_map.jpg",
        "search_tile_1": preview_directory / "synthetic_search_tile_1.jpg",
        "search_tile_2": preview_directory / "synthetic_search_tile_2.jpg",
        "search_tile_3": preview_directory / "synthetic_search_tile_3.jpg",
        "exposure_1": preview_directory / "synthetic_exposure_1.jpg",
        "exposure_2": preview_directory / "synthetic_exposure_2.jpg",
        "exposure_3": preview_directory / "synthetic_exposure_3.jpg",
    }
    for index, (label, path) in enumerate(paths.items(), start=1):
        if label == "search_map":
            continue
        _synthetic_micrograph(path, seed=3100 + index, title=label.replace("_", " ").title())
    targets = _synthetic_search_map(paths["search_map"])

    atlas_root = TEMP_DIRECTORY / "Atlas_Screening_Demo"
    atlas_sample = atlas_root / "Sample1"
    collection_root = TEMP_DIRECTORY / "Data_Collection_Demo"
    collection_sample = collection_root / "Sample1"
    (atlas_sample / "Atlas").mkdir(parents=True)
    (collection_sample / "Batch").mkdir(parents=True)
    (collection_sample / "SearchMaps" / "SearchMap_1").mkdir(parents=True)
    (atlas_root / "ScreeningSession.dm").write_text("<ScreeningSession />", encoding="utf-8")
    (atlas_sample / "Sample.dm").write_text("<Sample />", encoding="utf-8")
    (atlas_sample / "Atlas" / "Atlas.dm").write_text("<Atlas />", encoding="utf-8")
    (collection_root / "Session.dm").write_text("<Session />", encoding="utf-8")
    (collection_sample / "Session.dm").write_text("<Session />", encoding="utf-8")
    paths["atlas_root"] = atlas_root
    paths["collection_root"] = collection_root
    return paths, targets


def _tilt_series(identifier: str, name: str, count: int, *, start: str, end: str) -> TiltSeries:
    del end
    start_time = datetime.fromisoformat(start)
    mrc_path = TEMP_DIRECTORY / f"{name}.mrc"
    mrc_path.touch()
    sections = [
        MdocSection(
            z_value=index,
            metadata={
                "DateTime": (start_time + timedelta(seconds=index * 55)).isoformat(sep=" "),
                "TiltAngle": -60.0 + (index * 3.0),
            },
        )
        for index in range(count)
    ]
    return TiltSeries(
        id=identifier,
        name=name,
        mrc_path=mrc_path,
        mdoc_path=TEMP_DIRECTORY / f"{name}.mdoc",
        tilt_count=count,
        tilt_range=(-60.0, 60.0),
        target_defocus=-3.0,
        acquisition_time_start=start,
        acquisition_time_end=(start_time + timedelta(seconds=max(count - 1, 0) * 55)).isoformat(sep=" "),
        metadata={"TiltStep": 3.0, "Detector": "Demo detector", "ProbeMode": "Microprobe"},
        mrc_metadata=MrcMetadata(
            path=mrc_path,
            size_bytes=0,
            nx=4096,
            ny=4096,
            nz=count,
            is_stack=True,
        ),
        sections=sections,
    )


def _target_metadata(pixel: tuple[float, float]) -> dict[str, object]:
    """Batch-position metadata that places a target, and its areas, at ``pixel``.

    The shape Tomography 5 records: the target's stage position, the beam
    diameter, and the exposure, focus and tracking areas as offsets from it.
    """

    stage_x, stage_y = _search_map_stage_offset(pixel)
    return {
        "PositionOnTileSet": {"StagePositionX": stage_x, "StagePositionY": stage_y},
        "BeamDiameter": 1.8e-6,
        "BeamDiameterContext": BEAM_DIAMETER_EXPOSURE_MRC_CONTEXT,
        "ExposureTemplateAreaParameters": {"Name": "Exposure", "PositionX": 0.0, "PositionY": 0.0},
        "FocusTemplateAreaParameters": {"Name": "Focus", "PositionX": 3.6e-6, "PositionY": 1.2e-6},
        "TrackingTemplateAreaParameters": {"Name": "Tracking", "PositionX": -3.6e-6, "PositionY": -1.2e-6},
    }


def _build_demo_sessions(
    paths: dict[str, Path], targets: list[tuple[float, float]]
) -> tuple[Session, Session, SearchMap]:
    display_root = Path("C:/Tomography5_Demo")
    atlas_session_path = display_root / "Atlas_Screening_Demo"
    atlas_sample_path = atlas_session_path / "Sample1"
    collection_session_path = display_root / "Data_Collection_Demo"
    collection_sample_path = collection_session_path / "Sample1"

    atlas = Atlas(
        id="demo-atlas",
        image_path=paths["atlas"],
        metadata={"AcquisitionDate": "2026-01-14 08:30", "PixelSize": "12.5 nm"},
    )
    atlas_sample = Sample(
        id=str(atlas_sample_path).replace("\\", "/"),
        name="1. Demo Grid",
        path=atlas_sample_path,
        atlas=atlas,
        metadata={"Sample.dm": {"Name": {"value": "Demo Grid"}}},
    )
    atlas_session = Session(
        id="demo-atlas-session",
        name="Atlas Screening Demo",
        path=atlas_session_path,
        kind=SessionKind.ATLAS_SCREENING,
        samples=[atlas_sample],
        metadata_summary={"counts": {"samples": 1, "atlases": 1}},
    )

    overview = Overview(
        id="demo-overview",
        name="Overview_1",
        image_path=paths["overview"],
        linked_search_map_ids=["demo-search-map"],
        linked_batch_position_ids=["demo-bp-1", "demo-bp-2", "demo-bp-3"],
    )
    search_map = SearchMap(
        id="demo-search-map",
        name="SearchMap_1",
        image_path=paths["search_map"],
        overview=overview,
        tile_paths=[paths[f"search_tile_{index}"] for index in range(1, 4)],
        linked_batch_position_ids=["demo-bp-1", "demo-bp-2", "demo-bp-3"],
        linked_tilt_series_ids=["demo-tilt-1", "demo-tilt-2", "demo-tilt-3"],
        metadata={
            "AcquisitionTime": "2026-01-15 09:12",
            "SearchMap.dm": {
                "ImageSize": {"width": SEARCH_MAP_SIZE[0], "height": SEARCH_MAP_SIZE[1]},
                "StagePosition": {"X": 0.0, "Y": 0.0},
                "pixelSize": {"x": {"numericValue": SEARCH_MAP_PIXEL_SIZE_M}},
            },
        },
    )
    tilts = [
        _tilt_series("demo-tilt-1", "Position_1", 41, start="2026-01-15 10:00:00", end="2026-01-15 10:38:00"),
        _tilt_series("demo-tilt-2", "Position_2", 38, start="2026-01-15 10:45:00", end="2026-01-15 11:20:00"),
        _tilt_series("demo-tilt-3", "Position_3", 3, start="2026-01-15 11:28:00", end="2026-01-15 11:31:00"),
    ]
    batches: list[BatchPosition] = []
    search_tiles: list[SearchTile] = []
    for index, tilt in enumerate(tilts, start=1):
        batch_id = f"demo-bp-{index}"
        tile_id = f"demo-tile-{index}"
        tile_path = paths[f"search_tile_{index}"]
        exposure_path = paths[f"exposure_{index}"]
        batches.append(
            BatchPosition(
                id=batch_id,
                name=f"Position_{index}",
                status="Acquired" if index < 3 else "Failed",
                search_image_path=tile_path,
                tracking_image_path=tile_path,
                exposure_image_path=exposure_path,
                linked_tilt_series_ids=[tilt.id],
                linked_search_map_id=search_map.id,
                linked_search_tile_id=tile_id,
                linked_overview_id=overview.id,
                metadata={"TargetDefocus": -3.0 - (index * 0.2), **_target_metadata(targets[index - 1])},
            )
        )
        tilt.linked_batch_position_id = batch_id
        search_tiles.append(
            SearchTile(
                id=tile_id,
                name=f"Search tile · Position {index}",
                image_path=tile_path,
                search_map_id=search_map.id,
                search_map_name=search_map.name,
                batch_position_id=batch_id,
                batch_position_name=f"Position_{index}",
                tile_index=index,
                acquisition_time=f"2026-01-15 09:{10 + index:02d}:00",
                link_method="synthetic demo link",
                linked_tilt_series_ids=[tilt.id],
            )
        )

    atlas_dm = atlas_sample_path / "Atlas" / "Atlas.dm"
    collection_sample = Sample(
        id="demo-collection-sample",
        name="1. Demo Grid",
        path=collection_sample_path,
        overviews=[overview],
        search_maps=[search_map],
        search_tiles=search_tiles,
        batch_positions=batches,
        tilt_series=tilts,
        metadata={"Session.dm": {"AtlasId": {"value": str(atlas_dm)}}},
        warnings=["Synthetic example: Position_3 stopped after three tilt images."],
    )
    collection_session = Session(
        id="demo-collection-session",
        name="Data Collection Demo",
        path=collection_session_path,
        kind=SessionKind.MULTIGRID,
        samples=[collection_sample],
        metadata_summary={
            "counts": {
                "samples": 1,
                "overviews": 1,
                "search_maps": 1,
                "search_tiles": 3,
                "batch_positions": 3,
                "tilt_series": 3,
            }
        },
    )
    return atlas_session, collection_session, search_map


def _check_targets_land_on_their_cells(
    session: Session, search_map: SearchMap, targets: list[tuple[float, float]]
) -> None:
    """Fail loudly if the app would draw a demo target away from its cell."""

    sample = session.samples[0]
    markers = search_map_markers(search_map, sample.batch_positions, tilt_series=sample.tilt_series)
    for batch, target in zip(sample.batch_positions, targets):
        marker = next(
            (item for item in markers if item.marker_type == MarkerType.BATCH_POSITION and item.linked_object_id == batch.id),
            None,
        )
        if marker is None or marker.x is None or marker.y is None:
            raise RuntimeError(f"No search-map marker for {batch.name}")
        if abs(marker.x - target[0]) > 2.0 or abs(marker.y - target[1]) > 2.0:
            raise RuntimeError(f"{batch.name} drawn at ({marker.x:.0f}, {marker.y:.0f}), expected {target}")


def _process_events(app: QApplication, *, milliseconds: int = 300) -> None:
    deadline = time.monotonic() + (milliseconds / 1000)
    while time.monotonic() < deadline:
        app.processEvents()
        # This harness does not enter app.exec(); processEvents alone leaves
        # deleteLater widgets visible behind the newly rendered dashboard.
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        time.sleep(0.01)


def _save_widget(widget, filename: str, app: QApplication) -> None:
    widget.show()
    _process_events(app, milliseconds=350)
    target = OUTPUT_DIRECTORY / filename
    if not widget.grab().save(str(target), "PNG"):
        raise RuntimeError(f"Could not save screenshot: {target}")


def main() -> int:
    os.chdir(REPOSITORY_ROOT)
    OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    paths, targets = _prepare_temporary_files()

    app = QApplication.instance() or QApplication(sys.argv[:1])
    # The Qt offscreen plugin does not always discover Windows fonts. Register
    # known system fonts explicitly so generated screenshots contain readable
    # glyphs on CI and sandboxed developer machines.
    for font_path in (
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/consola.ttf",
        "C:/Windows/Fonts/consolab.ttf",
    ):
        if Path(font_path).exists():
            QFontDatabase.addApplicationFont(font_path)
    theme_module.MONO_FONT_FAMILY = '"Consolas"'
    apply_theme(app, palette_for("dark"))
    main_window_module.save_settings = lambda *_args, **_kwargs: None
    main_window_module.add_recent_session = lambda settings, *_args, **_kwargs: settings

    folder_dialog = LoadSessionsDialog(SessionScanner(), "", None)
    folder_dialog.add_paths(
        [
            paths["atlas_root"].relative_to(REPOSITORY_ROOT),
            paths["collection_root"].relative_to(REPOSITORY_ROOT),
        ]
    )
    folder_dialog.resize(760, 520)
    _save_widget(folder_dialog, "01-select-session-folders.png", app)
    folder_dialog.close()

    atlas_session, collection_session, search_map = _build_demo_sessions(paths, targets)
    _check_targets_land_on_their_cells(collection_session, search_map, targets)
    window = MainWindow(settings=Settings(theme="dark"))
    window.resize(1600, 900)
    window._integrate_loaded_session(  # noqa: SLF001 - documentation screenshot harness
        atlas_session,
        str(atlas_session.path),
        replace=True,
        quiet=True,
        animate=False,
    )
    window._integrate_loaded_session(  # noqa: SLF001 - documentation screenshot harness
        collection_session,
        str(collection_session.path),
        replace=False,
        quiet=True,
        animate=False,
    )
    window.context_dock.hide()
    window.context_panel_action.setChecked(False)
    window.show()
    _process_events(app, milliseconds=600)
    _save_widget(window, "02-linked-session-dashboard.png", app)

    window.tabs.setCurrentIndex(TAB_LABELS.index("Search map"))
    window._viewer_tabs["Search map"].ensure_initial_preview_loaded()  # noqa: SLF001
    QThreadPool.globalInstance().waitForDone(5000)
    _process_events(app, milliseconds=1000)
    _save_widget(window, "03-search-map-review.png", app)

    groups = window._project_groups()  # noqa: SLF001 - documentation screenshot harness
    report_dialog = ReportScopeDialog(groups, current_scope=groups[0] if groups else None)
    report_dialog.resize(660, 600)
    _save_widget(report_dialog, "04-report-scope.png", app)
    report_dialog.close()
    window.close()

    shutil.rmtree(TEMP_DIRECTORY)
    print(f"Wrote README screenshots to {OUTPUT_DIRECTORY}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
