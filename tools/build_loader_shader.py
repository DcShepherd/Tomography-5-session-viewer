"""Rebuild the loading overlay's orbs shader (plan F6.2).

Qt Quick loads shaders pre-packed as ``.qsb`` files. After editing
``tomography_session_browser/assets/loader/orbs.frag``, run from the
repository root in the project Conda environment::

    python tools/build_loader_shader.py

It runs Qt's ``qsb`` tool (installed with PySide6) and records the source's
SHA-256 next to the result. ``tests/test_release_smoke.py`` fails when the source
and the recorded hash differ, so a shader edit cannot ship without its build.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
LOADER = REPO / "tomography_session_browser" / "assets" / "loader"
SOURCE = LOADER / "orbs.frag"
OUTPUT = LOADER / "orbs.frag.qsb"
HASH = LOADER / "orbs.frag.sha256"


def _qsb() -> str:
    beside_python = Path(sys.executable).parent
    for folder in (beside_python / "Scripts", beside_python):
        for name in ("qsb.exe", "qsb", "pyside6-qsb.exe", "pyside6-qsb"):
            if (folder / name).is_file():
                return str(folder / name)
    found = shutil.which("qsb") or shutil.which("pyside6-qsb")
    if found is None:
        raise SystemExit("Qt's qsb tool was not found; it is installed with PySide6.")
    return found


def source_hash(path: Path = SOURCE) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def main() -> int:
    command = [
        _qsb(),
        "--glsl",
        "100 es,120,150",
        "--hlsl",
        "50",
        "--msl",
        "12",
        "-o",
        str(OUTPUT),
        str(SOURCE),
    ]
    subprocess.run(command, check=True)
    HASH.write_text(source_hash() + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT.relative_to(REPO)} and its source hash.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
