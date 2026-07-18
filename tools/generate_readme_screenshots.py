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
from PIL import Image, ImageDraw, ImageFont
from PySide6.QtCore import QThreadPool
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
from tomography_session_browser.parsers.session_scanner import SessionScanner
from tomography_session_browser.services.settings_service import Settings
from tomography_session_browser.ui import main_window as main_window_module
from tomography_session_browser.ui.main_window import MainWindow, OpenSessionsDialog, TAB_LABELS
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


def _prepare_temporary_files() -> dict[str, Path]:
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
        _synthetic_micrograph(path, seed=3100 + index, title=label.replace("_", " ").title())

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
    return paths


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


def _build_demo_sessions(paths: dict[str, Path]) -> tuple[Session, Session, SearchMap]:
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
        metadata={"AcquisitionTime": "2026-01-15 09:12", "PixelSize": "3.2 nm"},
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
                metadata={"TargetDefocus": -3.0 - (index * 0.2)},
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


def _process_events(app: QApplication, *, milliseconds: int = 300) -> None:
    deadline = time.monotonic() + (milliseconds / 1000)
    while time.monotonic() < deadline:
        app.processEvents()
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
    paths = _prepare_temporary_files()

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

    folder_dialog = OpenSessionsDialog(SessionScanner(), "", None)
    folder_dialog.atlas_path.setText(str(paths["atlas_root"].relative_to(REPOSITORY_ROOT)))
    folder_dialog.collection_path.setText(str(paths["collection_root"].relative_to(REPOSITORY_ROOT)))
    folder_dialog.resize(820, 290)
    _save_widget(folder_dialog, "01-select-session-folders.png", app)
    folder_dialog.close()

    atlas_session, collection_session, search_map = _build_demo_sessions(paths)
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
