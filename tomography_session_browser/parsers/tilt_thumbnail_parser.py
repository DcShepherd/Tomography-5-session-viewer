"""Locate Tomography 5's per-tilt-series thumbnail stacks.

Tomography 5 writes a ``Thumbnails`` folder beside a dataset's tilt series.
For each stack it holds a downsampled copy with the same file name, frame
order and per-frame extended header. The folder is only located and read,
never written.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

from tomography_session_browser.domain.models import TiltSeries

LOGGER = logging.getLogger(__name__)

#: The folder's name, matched without regard to case.
THUMBNAIL_FOLDER_NAME = "thumbnails"


def find_tilt_series_thumbnails(tilt_series: Iterable[TiltSeries]) -> dict[str, Path]:
    """``{tilt-series id: thumbnail stack}`` for every tilt series that has one.

    A tilt series' thumbnail is the ``.mrc`` of the same name in a
    ``Thumbnails`` folder beside its stack. Each folder is listed once,
    however many tilt series share it. A folder that cannot be listed simply
    yields no thumbnails.
    """

    stacks_by_folder: dict[Path, dict[str, Path]] = {}
    found: dict[str, Path] = {}
    for tilt in tilt_series:
        stack = Path(tilt.mrc_path)
        folder = stack.parent
        if folder not in stacks_by_folder:
            stacks_by_folder[folder] = _thumbnail_stacks(folder)
        thumbnail = stacks_by_folder[folder].get(stack.name.casefold())
        if thumbnail is not None:
            found[tilt.id] = thumbnail
    return found


def _thumbnail_stacks(folder: Path) -> dict[str, Path]:
    """``{file name, case-folded: path}`` of the MRC files in ``folder``'s thumbnail folder."""

    try:
        candidates = sorted(
            child for child in folder.iterdir()
            if child.name.casefold() == THUMBNAIL_FOLDER_NAME and child.is_dir()
        )
    except OSError as exc:
        LOGGER.debug("Could not list %s for thumbnails: %s", folder, exc)
        return {}
    stacks: dict[str, Path] = {}
    for thumbnails in candidates:
        try:
            for item in sorted(thumbnails.iterdir()):
                if item.suffix.casefold() == ".mrc" and item.is_file():
                    stacks.setdefault(item.name.casefold(), item)
        except OSError as exc:
            LOGGER.debug("Could not list thumbnail folder %s: %s", thumbnails, exc)
    return stacks


__all__ = ["THUMBNAIL_FOLDER_NAME", "find_tilt_series_thumbnails"]
