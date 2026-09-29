"""Small release checks for the runnable shell and shipped loader assets."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import zlib

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from tomography_session_browser.ui.widgets import loader_orbs

_ROOT = Path(__file__).resolve().parents[1]
_STARTUP = """
import sys
from PySide6.QtWidgets import QApplication
from tomography_session_browser.services.settings_service import Settings
from tomography_session_browser.ui.main_window import MainWindow
from tomography_session_browser.ui.theme import apply_theme, palette_for

app = QApplication([])
theme = sys.argv[1]
apply_theme(app, palette_for(theme))
window = MainWindow(settings=Settings(theme=theme))
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
