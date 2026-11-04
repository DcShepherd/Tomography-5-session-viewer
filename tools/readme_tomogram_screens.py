"""README screenshots of the Tomograms tab and movie export, from a synthetic tomogram.

``generate_readme_screenshots.py`` calls this module. Nothing here reads
microscope data: the volumes are procedural, and they carry a label that says so.
A complete Warp layout goes in a neutral folder that the module makes and removes,
so that no local path can show in a screenshot. Movie export needs FFmpeg with
libx264 on PATH.
"""

from __future__ import annotations

from collections.abc import Callable
import math
import os
from pathlib import Path
import shutil
import struct
import tempfile
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QApplication

#: A neutral path: it can show in the Context panel and in the export dialog.
DEMO_ROOT = Path("C:/Tomography5_Demo") if os.name == "nt" else Path(tempfile.gettempdir()) / "Tomography5_Demo"
#: z, y, x at 10 A per voxel, which is the sampling of a Warp reconstruction.
SHAPE = (112, 512, 512)
SPACING_A = 10.0
LABEL = "SYNTHETIC · NOT MICROSCOPE DATA"
TOMOGRAMS = (("TS_001", 7101), ("TS_002", 7102), ("TS_003", 7103))
WARP_FOLDERS = {
    "Raw": "warp_tiltseries/reconstruction",
    "Deconvolved": "warp_tiltseries/reconstruction/deconv",
    "Denoised": "denoised",
}


def _write_mrc(path: Path, values: np.ndarray) -> None:
    """A float16 MRC volume (mode 12), the format Warp writes."""

    nz, ny, nx = values.shape
    header = bytearray(1024)
    struct.pack_into("<4i", header, 0, nx, ny, nz, 12)
    struct.pack_into("<3i", header, 28, nx, ny, nz)
    struct.pack_into("<3f", header, 40, SPACING_A * nx, SPACING_A * ny, SPACING_A * nz)
    struct.pack_into("<3f", header, 52, 90, 90, 90)
    struct.pack_into("<3i", header, 64, 1, 2, 3)
    header[208:212] = b"MAP "
    header[212:216] = b"DD\x00\x00"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(header)
        handle.write(values.astype("<f2").tobytes())


def _stamp(volume: np.ndarray, centre: tuple[float, float, float], radius: float, amount: float, *, shell: bool = False) -> None:
    """Add a soft sphere, or a soft spherical shell, at (z, y, x)."""

    reach = int(radius + 6)
    box = [(max(int(c) - reach, 0), min(int(c) + reach + 1, n)) for c, n in zip(centre, volume.shape)]
    if any(low >= high for low, high in box):
        return
    z, y, x = np.ogrid[box[0][0]:box[0][1], box[1][0]:box[1][1], box[2][0]:box[2][1]]
    distance = np.sqrt((z - centre[0]) ** 2 + (y - centre[1]) ** 2 + (x - centre[2]) ** 2)
    if shell:
        weight = np.exp(-((distance - radius) ** 2) / (2 * 1.8**2))
    else:
        weight = 1.0 / (1.0 + np.exp(np.clip((distance - radius) / 1.3, -30.0, 30.0)))
    volume[box[0][0]:box[0][1], box[1][0]:box[1][1], box[2][0]:box[2][1]] += amount * weight.astype(np.float32)


def _smooth(volume: np.ndarray, *, axes: tuple[int, ...] = (0, 1, 2), passes: int = 1) -> np.ndarray:
    """A separable [1 2 1] filter: fast, and enough for correlated noise."""

    for _ in range(passes):
        for axis in axes:
            volume = 0.25 * (np.roll(volume, 1, axis) + np.roll(volume, -1, axis)) + 0.5 * volume
    return volume


def _cell_model(seed: int) -> np.ndarray:
    """A bacterial cell in thin ice: an envelope, ribosomes, granules and filaments. Dark means dense."""

    rng = np.random.default_rng(seed)
    nz, ny, nx = SHAPE
    volume = np.zeros(SHAPE, dtype=np.float32)
    angle = rng.uniform(0.5, 1.0)
    centre_x, centre_y = nx * rng.uniform(0.40, 0.60), ny * rng.uniform(0.44, 0.56)
    half_length, radius = 420.0, rng.uniform(165.0, 195.0)
    yy, xx = np.mgrid[0:ny, 0:nx].astype(np.float32)
    along = (xx - centre_x) * math.cos(angle) + (yy - centre_y) * math.sin(angle)
    across = -(xx - centre_x) * math.sin(angle) + (yy - centre_y) * math.cos(angle)
    in_plane = np.hypot(along - np.clip(along, -half_length, half_length), across)
    inside: list[np.ndarray] = []
    for z in range(nz):
        # A flattened cross-section: the cell fills most of the ice thickness.
        height = (z - nz / 2.0) / (nz * 0.47) * radius
        distance = np.sqrt(in_plane**2 + height**2) - radius
        volume[z] -= 1.05 * np.exp(-(distance**2) / (2 * 2.3**2))  # outer membrane
        volume[z] -= 0.45 * np.exp(-((distance + 13.0) ** 2) / (2 * 3.2**2))  # cell wall
        volume[z] -= 1.05 * np.exp(-((distance + 30.0) ** 2) / (2 * 2.3**2))  # inner membrane
        volume[z] -= 0.30 / (1.0 + np.exp(np.clip((distance + 31.0) / 2.0, -30.0, 30.0)))  # cytoplasm
        volume[z] -= 0.06 / (1.0 + np.exp(np.clip(distance / 2.0, -30.0, 30.0)))  # periplasm
        inside.append(distance < -46.0)
    mask = np.stack(inside)

    def inside_points(count: int, margin: int = 0) -> list[tuple[int, int, int]]:
        candidates = np.argwhere(mask[margin : nz - margin or None])
        chosen = candidates[rng.integers(0, len(candidates), size=count)]
        return [(int(z) + margin, int(y), int(x)) for z, y, x in chosen]

    if mask.any():
        for z, y, x in inside_points(3, margin=30):
            _stamp(volume, (z, y, x), rng.uniform(26.0, 38.0), -0.85)  # storage granules
        for z, y, x in inside_points(2, margin=26):
            _stamp(volume, (z, y, x), rng.uniform(20.0, 30.0), -0.9, shell=True)  # a vesicle
        for z, y, x in inside_points(620):
            # A ribosome: a large subunit and a small subunit.
            _stamp(volume, (z, y, x), rng.uniform(8.5, 10.5), -0.95)
            turn = rng.uniform(0.0, 2.0 * math.pi)
            _stamp(volume, (z + rng.uniform(-5, 5), y + 8.0 * math.sin(turn), x + 8.0 * math.cos(turn)), rng.uniform(5.0, 6.5), -0.8)
        for z, y, x in inside_points(7, margin=24):
            # A filament: a smooth random walk of small subunits.
            heading = rng.uniform(0.0, 2.0 * math.pi)
            for _step in range(70):
                _stamp(volume, (z, y, x), 3.3, -0.55)
                heading += rng.normal(0.0, 0.07)
                y, x = y + 3.2 * math.sin(heading), x + 3.2 * math.cos(heading)
    # A flagellum in the ice, which crosses one corner.
    for step in range(130):
        t = step / 129.0
        _stamp(volume, (nz * 0.5 + 8.0 * math.sin(t * 7.0), ny * (0.04 + 0.30 * t) + 7.0 * math.sin(t * 13.0), nx * (0.98 - 0.42 * t)), 5.0, -0.2)
    # Gold fiducials above and below the cell. They give the darkest values.
    for _ in range(9):
        z = float(rng.choice((rng.uniform(5, 16), rng.uniform(nz - 16, nz - 5))))
        _stamp(volume, (z, rng.uniform(20, ny - 20), rng.uniform(20, nx - 20)), rng.uniform(5.0, 7.0), -2.6)
    # The elongation along z that a limited tilt range gives.
    return _smooth(volume, axes=(0,), passes=4)


def _label_mask() -> np.ndarray:
    """The label as a mask, for the bottom left of every XY plane."""

    font: ImageFont.ImageFont | ImageFont.FreeTypeFont = ImageFont.load_default()
    for candidate in ("C:/Windows/Fonts/segoeuib.ttf", "/System/Library/Fonts/SFNS.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            font = ImageFont.truetype(candidate, 13)
            break
        except OSError:
            continue
    image = Image.new("L", (SHAPE[2] // 2 - 8, 26), 0)
    ImageDraw.Draw(image).text((6, 4), LABEL, fill=255, font=font)
    # MRC rows run from the bottom of the picture to the top.
    return np.flipud(np.asarray(image, dtype=np.float32) / 255.0)


def _versions(model: np.ndarray, seed: int) -> dict[str, np.ndarray]:
    """Raw, deconvolved and denoised: one object with less noise and more contrast each time."""

    rng = np.random.default_rng(seed + 1)
    noise = _smooth(rng.standard_normal(SHAPE, dtype=np.float32))
    versions = {
        "Raw": _smooth(model) * 0.55 + 1.15 * noise,
        "Deconvolved": _smooth(model) * 1.0 + 0.62 * noise,
        # Two more passes make the noise about half as strong: this leaves a soft texture.
        "Denoised": _smooth(model, passes=2) * 1.0 + 0.8 * _smooth(noise, passes=2),
    }
    mask = _label_mask()
    for values in versions.values():
        # Dark text on a clear band, so that no object in the volume hides a word.
        # The values are in the range of ice and of a dense object, so Auto contrast does not change.
        values[:, 6 : 6 + mask.shape[0], 8 : 8 + mask.shape[1]] = -1.6 * mask
    return versions


def write_demo_project(root: Path) -> Path:
    """Write a synthetic Warp project and return its root."""

    project = root / "Warp_Demo_Project"
    project.mkdir(parents=True)
    (project / "warp_tiltseries.settings").write_text("<Settings/>", encoding="utf-8")
    for name, seed in TOMOGRAMS:
        for version, values in _versions(_cell_model(seed), seed).items():
            _write_mrc(project / WARP_FOLDERS[version] / f"{name}_10.00Apx.mrc", values)
    return project


def _pump(app: QApplication, until: Callable[[], bool] | None = None, *, seconds: float = 0.3, timeout: float = 120.0, what: str = "") -> None:
    """Run the event loop until ``until`` is true, or for ``seconds`` when there is no condition."""

    started = time.monotonic()
    while True:
        app.processEvents()
        # There is no app.exec() here: processEvents alone leaves deleteLater widgets visible.
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        if until is None:
            if time.monotonic() - started >= seconds:
                return
        elif until():
            return
        if time.monotonic() - started > timeout:
            raise RuntimeError(f"Timed out while waiting for {what or 'the application'}")
        time.sleep(0.01)


def capture_tomogram_screens(app: QApplication, output: Path) -> None:
    """Load the synthetic project in the real window and save the four screenshots."""

    if shutil.which("ffmpeg") is None:
        raise RuntimeError("Movie screenshots need FFmpeg with libx264 on PATH")
    if DEMO_ROOT.exists():
        raise RuntimeError(f"{DEMO_ROOT} exists. Move it, then run this tool again.")
    # Imported here so that the caller can redirect the configuration folder first.
    from tomography_session_browser.presentation.tab_keys import TabKey
    from tomography_session_browser.services.settings_service import Settings
    from tomography_session_browser.ui.main_window import MainWindow
    from tomography_session_browser.ui.shell.chrome import TAB_LABELS

    movies = DEMO_ROOT / "Movies"
    try:
        project = write_demo_project(DEMO_ROOT)
        movies.mkdir()
        # The list needs little height with three rows: the tag panel gets the rest.
        settings = Settings(theme="dark", tomogram_snapshot_folder=str(movies), tomogram_tags_splitter=[300, 340])
        window = MainWindow(settings=settings, save_settings=lambda _settings: None)
        tab = window.ui.tomogram_tab

        def settled() -> bool:
            frame = tab.rendering.accepted
            return bool(frame and frame.complete and tab.rendering.is_current() and not tab.render_timer.isActive()
                        and not frame.result.recipe.interactive and not tab.oblique.timer.isActive())

        def save(widget, name: str) -> None:
            _pump(app, seconds=0.4)
            if not widget.grab().save(str(output / name), "PNG"):
                raise RuntimeError(f"Could not save {name}")
            print("saved", name, flush=True)

        window.resize(1600, 960)
        window.show()
        window.parts.loading.load_session(str(project), replace=True)
        _pump(app, lambda: bool(window.state.review.workspace.processing_sources) and not window.parts.loading.loading_overlay_active(),
              what="the demo project")
        window.ui.tabs.setCurrentIndex(TAB_LABELS.index(TabKey.TOMOGRAMS))
        _pump(app, settled, what="the first frame")
        _pump(app, lambda: tab.review_list.rows and tab.slider.isEnabled(), what="the tomogram list")
        window.ui.toast.dismiss()

        def show_version(index: int) -> None:
            tab.versions.select_index(index)
            _pump(app, seconds=0.3)
            _pump(app, settled, what="the selected version")

        # One plane, with the list and the Context panel. Focus off the tree: its ring is not part of the picture.
        tab.slider.setValue(tab.slider.maximum() // 2)
        show_version(list(WARP_FOLDERS).index("Deconvolved"))
        tab.setFocus()
        save(window, "05-tomograms-single-plane.png")

        # Three planes, with the Image panel open.
        show_version(list(WARP_FOLDERS).index("Denoised"))
        tab.change_layout("Ortho")
        _pump(app, settled, what="the Ortho frame")
        tab.image_button.setChecked(True)
        _pump(app, seconds=0.5)
        _pump(app, settled, what="the Ortho frame beside the Image panel")
        tab.setFocus()
        save(window, "06-tomograms-ortho-image-panel.png")
        tab.image_button.setChecked(False)
        tab.change_layout("Single")
        show_version(list(WARP_FOLDERS).index("Deconvolved"))

        # The export dialog is modal: its screenshots and its Export click run inside its own event loop.
        steps: list[str] = []

        def in_dialog() -> None:
            dialog = tab.movie.dialog
            if dialog is None:
                QTimer.singleShot(50, in_dialog)
                return
            _pump(app, lambda: tab.movie.movie_available() is not None and dialog.preview.image is not None, what="the movie preview")
            # A short range, so that the real export takes seconds. Bounce and a hold show what the timeline can do.
            middle = tab.slider.maximum() // 2
            dialog.change_timeline(first=middle - 20, last=middle + 20, end="Bounce")
            dialog.sync_controls()
            _pump(app, seconds=0.3)
            dialog.move_to(25)
            dialog.add_hold()
            dialog.resize(1220, 760)
            dialog.preview.image = None
            dialog.request_frame()
            _pump(app, lambda: dialog.preview.image is not None, what="the held frame")
            save(dialog, "07-export-movie-dialog.png")
            steps.append("exported")
            dialog.accept_export()

        QTimer.singleShot(50, in_dialog)
        tab.movie.open()
        if not steps:
            raise RuntimeError("The export dialog did not open")
        _pump(app, lambda: tab.movie.progress is not None and tab.movie.progress.state != "exporting", what="the movie export")
        progress = tab.movie.progress
        if progress.state != "saved":
            raise RuntimeError(f"The movie export failed: {progress.reason.text()}")
        save(progress, "08-movie-saved.png")
        progress.close_quietly()
        _pump(app, seconds=0.2)
        window.close()
        _pump(app, seconds=0.5)
        window.deleteLater()
        _pump(app, seconds=0.2)
    finally:
        shutil.rmtree(DEMO_ROOT, ignore_errors=True)
    if DEMO_ROOT.exists():
        print(f"Remove {DEMO_ROOT} by hand: a file was still open.", flush=True)
