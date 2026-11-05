"""Small release checks for the runnable shell and shipped loader assets."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import struct
import sys
import tomllib
import zlib

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from tomography_session_browser.ui.widgets import loader_orbs

_ROOT = Path(__file__).resolve().parents[1]
_STARTUP = """
import sys
from PySide6.QtWidgets import QApplication
from tomography_session_browser.presentation.design_tokens import palette_for
from tomography_session_browser.services.settings_service import Settings
from tomography_session_browser.ui.main_window import MainWindow
from tomography_session_browser.ui.theme import apply_theme

app = QApplication([])
theme = sys.argv[1]
apply_theme(app, palette_for(theme))
window = MainWindow(settings=Settings(theme=theme), save_settings=lambda _settings: None)
window.show()
app.processEvents()
assert window.isVisible()
assert not window.windowIcon().isNull()
assert not window.grab().isNull()
window.close()
"""


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_application_starts_and_renders_in_each_theme(theme: str) -> None:
    # Start with a fresh QApplication and native event filters, without state
    # left by earlier GUI tests.
    result = subprocess.run(
        [sys.executable, "-B", "-c", _STARTUP, theme],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_loader_assets_include_the_current_compiled_shader() -> None:
    source = loader_orbs.SHADER_SOURCE_PATH.read_bytes().replace(b"\r\n", b"\n")
    recorded = (loader_orbs.ASSETS / "orbs.frag.sha256").read_text(encoding="utf-8").strip()
    assert recorded == hashlib.sha256(source).hexdigest(), "run tools/build_loader_shader.py"
    assert zlib.decompress(loader_orbs.SHADER_PATH.read_bytes()[4:])
    assert loader_orbs.QML_PATH.is_file()


def test_release_version_matches_package_metadata() -> None:
    from tomography_session_browser import __version__

    metadata = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert __version__ == metadata["project"]["version"] == "1.0.1"


def _synthetic_mrc(path: Path, *, space_group: int = 1) -> None:
    """A tiny volume with a format-level volume record and no producer label."""
    header = bytearray(1024)
    struct.pack_into("<4i", header, 0, 4, 4, 4, 2)
    struct.pack_into("<3i", header, 28, 4, 4, 4)
    struct.pack_into("<3f", header, 40, 40, 40, 40)
    struct.pack_into("<3f", header, 52, 90, 90, 90)
    struct.pack_into("<3i", header, 64, 1, 2, 3)
    struct.pack_into("<i", header, 88, space_group)
    header[208:212] = b"MAP "
    header[212:216] = b"DD\x00\x00"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + struct.pack("<64f", *range(64)))


@pytest.mark.parametrize("relative", ["Demo.mrc", "Demo/Demo.mrc"])
def test_unlabelled_volume_imports_as_a_tomogram(tmp_path: Path, relative: str) -> None:
    from tomography_session_browser.domain.enums import SessionKind
    from tomography_session_browser.parsers.processing_intake import load_processing_source
    from tomography_session_browser.services.session_loader import SessionLoader

    path = tmp_path / relative
    _synthetic_mrc(path)
    before = path.read_bytes()
    assert SessionLoader().classify(tmp_path) == SessionKind.PROCESSING
    source = load_processing_source(tmp_path)
    assert source.pipeline == "Reconstructions"
    assert len(source.tomograms) == 1
    assert source.tomograms[0].name == "Demo"
    assert source.tomograms[0].volumes[0].grid is not None
    assert path.read_bytes() == before


def test_acquisition_scan_excludes_unaccompanied_volumes(tmp_path: Path) -> None:
    from tomography_session_browser.parsers.tiltseries_parser import parse_tilt_series_in_folder

    _synthetic_mrc(tmp_path / "Volume.mrc")
    _synthetic_mrc(tmp_path / "Stack.mrc", space_group=0)
    warnings: list[str] = []
    series = parse_tilt_series_in_folder(tmp_path, warnings)
    assert [item.name for item in series] == ["Stack"]
    assert warnings == [
        "Volume.mrc: the header records a volume, not a tilt stack. It was not loaded as a tilt series."
    ]


@pytest.mark.parametrize("mdoc", ["Stack.mdoc", "Stack.mrc.mdoc"])
def test_acquisition_record_preserves_tilt_stack_classification(tmp_path: Path, mdoc: str) -> None:
    from tomography_session_browser.domain.enums import SessionKind
    from tomography_session_browser.parsers.tiltseries_parser import parse_tilt_series_in_folder
    from tomography_session_browser.services.session_loader import SessionLoader

    _synthetic_mrc(tmp_path / "Stack.mrc")
    (tmp_path / mdoc).write_text("[ZValue = 0]\nTiltAngle = 0\n", encoding="utf-8")
    assert SessionLoader().classify(tmp_path) != SessionKind.PROCESSING
    assert [item.name for item in parse_tilt_series_in_folder(tmp_path, [])] == ["Stack"]
