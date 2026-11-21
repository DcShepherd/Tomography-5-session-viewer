"""How the app finds the type of MRC files that have no MDOC (owner D7–D16, 20 November 2026).

The first rule that applies wins: a verified Warp project; an extended header
(a tilt series); a header that records a volume; Warp's tomogram name (a
tomogram); otherwise, for a file with more than one section, the user's
selection. A file with one section keeps the acquisition rule.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class ReadAs(StrEnum):
    """The user's selection for a folder whose MRC files no record decides.

    Kept in the app's settings by folder, never in the folder. "Do not load"
    is not kept: the folder is asked about again at the next load.
    """

    TOMOGRAMS = "tomograms"
    TILT_SERIES = "tilt_series"


class TypeFoundBy(StrEnum):
    """How the app found a source's type when no record says it (Context shows it)."""

    #: Warp's tomogram file names, with no Warp project above the folder (rule 4).
    WARP_NAMES = "warp_names"
    #: The user's selection (rule 5).
    SELECTION = "selection"


@dataclass(frozen=True, slots=True)
class UndecidedFile:
    """An MRC file that no record decides, as its header gives it (rule 5)."""

    path: Path
    size: tuple[int, int, int]
    #: Ångström per pixel from the header's cell, or None when the header has none.
    pixel_size: float | None


@dataclass(frozen=True, slots=True)
class FolderQuestion:
    """A folder to ask about: the selection decides how all of its ``files`` load."""

    path: Path
    files: tuple[UndecidedFile, ...]
