"""What the context panel says on a summary: where the data came from and how
it was acquired (design review B2.6, D33).

A summary page already carries the counts, the timeline and the findings, and
titles its own scope. Context adds only what those cannot: the source folders
(a copyable path for each) and the acquisition settings the scope shares.
Text only; nothing here reads a file or changes a session.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from tomography_session_browser.domain.display_names import count_phrase, session_kind_label
from tomography_session_browser.domain.folder_type import ReadAs, TypeFoundBy
from tomography_session_browser.domain.models import Atlas, BatchPosition, Sample, Session, TiltSeries
from tomography_session_browser.domain.units import ANGSTROM_PER_PIXEL
from tomography_session_browser.presentation.describe.metadata import (
    common_magnification_value,
    common_xml_pixel_size,
    first_metadata,
    scope_setting_summary,
    target_defocus_range_text,
    tilt_pixel_size_text,
)
from tomography_session_browser.presentation.describe.sections import emit_section
from tomography_session_browser.presentation.folder_type import selection_text
from tomography_session_browser.presentation.read_times import READ_INTO_APP, SourceReadTimes, read_time_text
from tomography_session_browser.presentation.workspace import EntityKind, display_atlases, session_entities
from tomography_session_browser.services.acquisition_metadata import ACQUISITION_SPOT_LABEL, SEARCH_SPOT_LABEL

SOURCE_FOLDERS = "Source folders"
ACQUISITION = "Acquisition"


@dataclass(frozen=True, slots=True)
class SourceFolder:
    """One folder the scope's data came from."""

    name: str
    detail: str
    path: Path
    #: When the app read the folder (D16); unknown, no row.
    read_at: datetime | None = None
    #: How the type was found, when no record says it (owner D15 a): "Tilt series · your selection".
    read_as: str = ""


#: The selection a session's folder was read by: a session read by selection holds tilt series.
SESSION_READ_AS = selection_text(ReadAs.TILT_SERIES, capital=True)


def session_folder(session: Session, read_times: SourceReadTimes | None = None) -> SourceFolder:
    """A session's folder: its kind, how many samples it holds, and when it was read."""

    kind = session_kind_label(session.kind)
    samples = count_phrase(len(session.samples), "sample") if session.samples else ""
    read_at = read_times.read_at(session) if read_times is not None else None
    return SourceFolder(session.name, " · ".join(part for part in (kind, samples) if part), session.path, read_at,
                        SESSION_READ_AS if session.found_by == TypeFoundBy.SELECTION else "")


def sample_folder(sample: Sample, selected: frozenset[str] = frozenset()) -> SourceFolder:
    """A sample's folder; ``selected`` holds the ids of samples whose session was read by selection."""

    return SourceFolder(sample.name, "", sample.path, read_as=SESSION_READ_AS if sample.id in selected else "")


def sessions_summary_lines(sessions: Sequence[Session], read_times: SourceReadTimes | None = None) -> list[str]:
    """The summary of one or several sessions: each folder, and their shared settings."""

    return summary_lines(
        [session_folder(session, read_times) for session in sessions],
        tilt_series=[
            tilt
            for session in sessions
            for tilt in session_entities(session, EntityKind.TILT_SERIES)
            if isinstance(tilt, TiltSeries)
        ],
        batch_positions=[
            batch
            for session in sessions
            for batch in session_entities(session, EntityKind.BATCH_POSITION)
            if isinstance(batch, BatchPosition)
        ],
        atlases=[atlas for session in sessions for atlas in display_atlases(session)],
    )


def summary_lines(
    folders: Sequence[SourceFolder],
    *,
    tilt_series: Sequence[TiltSeries] = (),
    batch_positions: Sequence[BatchPosition] = (),
    atlases: Sequence[Atlas] = (),
) -> list[str]:
    """The source folders, then the acquisition settings the scope shares."""

    blocks: list[str] = []
    emit_section(blocks, SOURCE_FOLDERS, _folder_rows(folders))
    emit_section(blocks, ACQUISITION, acquisition_rows(tilt_series, batch_positions, atlases))
    return blocks


def _folder_rows(folders: Sequence[SourceFolder]) -> list[str]:
    rows: list[str] = []
    for folder in folders:
        if rows:
            rows.append("")
        rows.append(folder.name)
        if folder.detail:
            rows.append(folder.detail)
        rows.append(f"Path: {folder.path}")
        if folder.read_as:
            rows.append(f"Read as: {folder.read_as}")
        if folder.read_at is not None:
            rows.append(f"{READ_INTO_APP}: {read_time_text(folder.read_at)}")
    return rows


def acquisition_rows(
    tilt_series: Sequence[TiltSeries],
    batch_positions: Sequence[BatchPosition],
    atlases: Sequence[Atlas],
) -> list[str]:
    """The settings the scope shares; what is unknown is left out.

    Spot sizes that agree are one row. A scope with no collection data (an
    atlas session) shows its atlas's magnification and pixel size instead.
    """

    tilt_metadata = [tilt.metadata for tilt in tilt_series]
    batch_metadata = [batch.metadata for batch in batch_positions]

    def setting(label: str) -> str:
        return scope_setting_summary(tilt_metadata, batch_metadata, label)

    rows = [_row("Detector", setting("Detector")), _row("Probe mode", setting("Probe mode"))]
    search, acquisition = setting(SEARCH_SPOT_LABEL), setting(ACQUISITION_SPOT_LABEL)
    if search == acquisition and search != "unknown":
        rows.append(f"Spot size: {search} (search and acquisition)")
    else:
        rows += [_row(SEARCH_SPOT_LABEL, search), _row(ACQUISITION_SPOT_LABEL, acquisition)]
    if tilt_series:
        rows += [
            _row("Pixel size", scope_pixel_size_text(tilt_series)),
            _row("Target defocus", target_defocus_range_text(list(tilt_series))),
        ]
    elif not batch_positions and atlases:
        metadata = [first_metadata(atlas.metadata) for atlas in atlases]
        pixel_size = common_xml_pixel_size(metadata)
        rows += [
            _row("Magnification", common_magnification_value(metadata)),
            _row("Pixel size", f"{pixel_size} (atlas XML)" if pixel_size != "unknown" else pixel_size),
        ]
    return [row for row in rows if row]


def _row(label: str, value: str) -> str:
    return "" if value == "unknown" else f"{label}: {value}"


def scope_pixel_size_text(tilt_series: Sequence[TiltSeries]) -> str:
    """One pixel size for a scope, with how it was reached; a range when it varies.

    Series that agree read as ``tilt_pixel_size_text`` reads one series
    (``6.78 Å/px (original 3.39 Å/px, binning 2×)``). Series that differ,
    whether by binning or by magnification, read as the span of their pixel
    sizes, because the derivation is no longer one thing to say.
    """

    known = [tilt for tilt in tilt_series if tilt.pixel_size is not None]
    if not known:
        return "unknown"
    texts = {tilt_pixel_size_text(tilt) for tilt in known}
    if len(texts) == 1:
        return texts.pop()
    sizes = sorted({float(tilt.pixel_size) for tilt in known if tilt.pixel_size is not None})
    if len(sizes) == 1:
        return f"{sizes[0]:g} {ANGSTROM_PER_PIXEL}"
    return f"{sizes[0]:g} to {sizes[-1]:g} {ANGSTROM_PER_PIXEL}"
