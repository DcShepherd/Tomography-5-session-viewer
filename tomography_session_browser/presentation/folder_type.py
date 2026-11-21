"""Words for MRC files that no record decides (rule 5), and for how a source's type was found.

The pop-up (owner D12, D13 a), the "Read as" line in Context and its "Change…"
menu (D15 a), and the PDF's folder line for Warp file names. Text is short STE,
as approved on the mockup boards. Display only: the rules are in the parsers.
"""
from __future__ import annotations

from dataclasses import dataclass

from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.domain.folder_type import FolderQuestion, ReadAs, TypeFoundBy, UndecidedFile
from tomography_session_browser.services.session_intake import READ_AS_WORDS

QUESTION_BODY = (
    "The app cannot find the type of these MRC files. "
    "They have no MDOC file, no extended header and no Warp name."
)
QUESTION_NOTE = "The app keeps your selection for this folder. You can change it in Context. The files do not change."
QUESTION_COLUMNS = ("File", "X × Y", "Sections", "Pixel size")
DO_NOT_LOAD = "Do not load"
#: The receipt's reason for a folder the user did not load from the pop-up.
NOT_LOADED_BY_SELECTION = "You selected “Do not load”"
#: The Context line for a folder read by Warp's file names (rule 4), and the note under it.
READ_BY_WARP_NAMES = "Read as: tomograms, by Warp file names"
NO_PROJECT_NOTE = (
    "No Warp project folder holds these files. "
    "Thus, links to tilt series, CTF data and frame data are not available."
)
CHANGE_SELECTION = "Change…"
SELECT_AGAIN = "Select again at next load"
DO_NOT_LOAD_FOLDER = "Do not load this folder"


def question_title(question: FolderQuestion) -> str:
    return f"Select the type of {count_phrase(len(question.files), 'file')}"


def folder_chip(position: tuple[int, int]) -> str:
    """"Folder 1 of 3" when one load asks about two or more folders; empty for one (D13 a)."""

    number, total = position
    return f"Folder {number} of {total}" if total > 1 else ""


def file_cells(file: UndecidedFile) -> tuple[str, str, str, str]:
    """One table row: name, X × Y, sections and pixel size ("–" when the header has none)."""

    nx, ny, nz = file.size
    pixel = f"{file.pixel_size:.1f} Å" if file.pixel_size is not None else "–"
    return file.path.name, f"{nx} × {ny}", f"{nz:,}", pixel


def selection_text(selection: ReadAs, *, capital: bool = False) -> str:
    """"tomograms · your selection" (or "Tilt series · your selection" for a key/value row)."""

    words = READ_AS_WORDS[selection]
    return f"{words[:1].upper() + words[1:] if capital else words} · your selection"


def read_as_line(found_by: TypeFoundBy | None) -> str:
    """The Context line for a processing folder (it holds tomograms); empty when a record gave the type."""

    if found_by == TypeFoundBy.WARP_NAMES:
        return READ_BY_WARP_NAMES
    if found_by == TypeFoundBy.SELECTION:
        return f"Read as: {selection_text(ReadAs.TOMOGRAMS)}"
    return ""


@dataclass(frozen=True, slots=True)
class SelectionChange:
    """One item of the "Change…" menu: its label and the selection it keeps (None asks again)."""

    label: str
    selection: ReadAs | None
    remove: bool = False


def selection_changes(current: ReadAs) -> tuple[SelectionChange, ...]:
    """The "Change…" menu for a folder read by the user's selection (D15 a); each loads it again."""

    other = ReadAs.TILT_SERIES if current == ReadAs.TOMOGRAMS else ReadAs.TOMOGRAMS
    return (
        SelectionChange(f"Read as {READ_AS_WORDS[other]}", other),
        SelectionChange(SELECT_AGAIN, None),
        SelectionChange(DO_NOT_LOAD_FOLDER, None, remove=True),
    )


def warp_names_note_line(label: str) -> str:
    """The PDF's folder line for a folder read by Warp's file names (D15 a): the same note."""

    return f"{label} · {NO_PROJECT_NOTE}"
