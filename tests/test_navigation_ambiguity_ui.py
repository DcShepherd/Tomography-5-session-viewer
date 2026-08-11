"""N2: the viewer must stay inert when the resolver refuses to choose.

N1 made the service ambiguity-aware. These tests pin the UI half of the
contract: an ambiguous relationship changes nothing on screen, and the status
bar reports the resolver's own explanation rather than a generic "not found".
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.models import (
    BatchPosition,
    MdocSection,
    Overview,
    Sample,
    SearchMap,
    SearchTile,
    Session,
    TiltSeries,
)
from tomography_session_browser.ui.main_window import TAB_LABELS, MainWindow


def _app() -> QApplication:
    app = QApplication.instance()
    return app or QApplication([])


def _tilt(tmp_path: Path, tilt_id: str, name: str) -> TiltSeries:
    mrc_path = tmp_path / f"{tilt_id}.mrc"
    mrc_path.write_bytes(b"0")
    return TiltSeries(
        id=tilt_id,
        name=name,
        mrc_path=mrc_path,
        sections=[MdocSection(z_value=index) for index in range(20)],
    )


def _window(session: Session) -> MainWindow:
    _app()
    window = MainWindow()
    window._sessions = [session]
    window._session = session
    window._active_context = session
    window._rebuild_sample_index()
    return window


def _session_with(tmp_path: Path, **sample_kwargs) -> Session:
    sample = Sample(id="sample-1", name="Sample1", path=tmp_path, **sample_kwargs)
    return Session(
        id="session-1",
        name="Session",
        path=tmp_path,
        kind=SessionKind.MULTIGRID,
        samples=[sample],
    )


def _ui_state(window: MainWindow) -> tuple:
    """The things an inert action must leave untouched."""

    return (
        window._active_context,
        window.tabs.currentIndex(),
        tuple(
            window._viewer_tabs[label].selected_marker_id() for label in TAB_LABELS[1:]
        ),
    )


def test_batch_position_with_two_search_maps_stays_inert(tmp_path: Path) -> None:
    """Plan regression: 'Batch position with two Search maps remains inert'."""

    batch = BatchPosition(id="batch-1", name="Position_1")
    first = SearchMap(id="sm-1", name="SearchMap_1", linked_batch_position_ids=[batch.id])
    second = SearchMap(id="sm-2", name="SearchMap_2", linked_batch_position_ids=[batch.id])
    session = _session_with(tmp_path, search_maps=[first, second], batch_positions=[batch])
    window = _window(session)

    before = _ui_state(window)
    window._viewer_navigation_requested(batch, "search_map")

    assert _ui_state(window) == before
    message = window.statusBar().currentMessage()
    assert "is ambiguous between" in message.lower()
    assert "SearchMap_1" in message
    assert "SearchMap_2" in message


def test_search_tile_with_two_tilt_series_stays_inert(tmp_path: Path) -> None:
    """Plan regression: 'Search tile with two Tilt series remains inert'."""

    first = _tilt(tmp_path, "tilt-1", "Position_1")
    second = _tilt(tmp_path, "tilt-2", "Position_2")
    tile = SearchTile(
        id="tile-1",
        name="Exposure 1",
        linked_tilt_series_ids=[first.id, second.id],
    )
    session = _session_with(tmp_path, search_tiles=[tile], tilt_series=[first, second])
    window = _window(session)

    before = _ui_state(window)
    window._viewer_navigation_requested(tile, "tilt_series")

    assert _ui_state(window) == before
    message = window.statusBar().currentMessage()
    assert "is ambiguous between" in message.lower()
    assert "Position_1" in message
    assert "Position_2" in message


def test_ambiguous_overview_link_from_batch_stays_inert(tmp_path: Path) -> None:
    batch = BatchPosition(id="batch-1", name="Position_1")
    first = Overview(
        id="ov-1",
        name="Overview_A",
        image_path=tmp_path / "a.jpg",
        linked_batch_position_ids=[batch.id],
    )
    second = Overview(
        id="ov-2",
        name="Overview_B",
        image_path=tmp_path / "b.jpg",
        linked_batch_position_ids=[batch.id],
    )
    session = _session_with(tmp_path, overviews=[first, second], batch_positions=[batch])
    window = _window(session)

    before = _ui_state(window)
    window._viewer_navigation_requested(batch, "overview")

    assert _ui_state(window) == before
    assert "is ambiguous between" in window.statusBar().currentMessage().lower()


def test_missing_link_reads_differently_from_an_ambiguous_one(tmp_path: Path) -> None:
    """Unresolved and ambiguous both stay inert, but must not read the same."""

    lonely = BatchPosition(id="batch-1", name="Position_1")
    session = _session_with(tmp_path, batch_positions=[lonely])
    window = _window(session)

    window._viewer_navigation_requested(lonely, "search_map")
    missing_message = window.statusBar().currentMessage()

    # "is ambiguous between" is the ambiguity phrasing; "no unambiguous ..."
    # is the missing-link phrasing and merely contains the same substring.
    assert "is ambiguous between" not in missing_message.lower()
    assert "no unambiguous search map" in missing_message.lower()


def test_unique_link_still_navigates(tmp_path: Path) -> None:
    """The conservative change must not disable a genuinely unique jump."""

    search_map = SearchMap(id="sm-1", name="SearchMap_1")
    batch = BatchPosition(
        id="batch-1",
        name="Position_1",
        linked_search_map_id=search_map.id,
    )
    session = _session_with(tmp_path, search_maps=[search_map], batch_positions=[batch])
    window = _window(session)

    window._viewer_navigation_requested(batch, "search_map")

    assert TAB_LABELS[window.tabs.currentIndex()] == "Search map"
    assert "Opened" in window.statusBar().currentMessage()


def test_ambiguous_metadata_is_not_rescued_by_a_marker_sweep(tmp_path: Path) -> None:
    """The N1 carry-over: a marker fallback must not overrule an ambiguity.

    Two Search maps both claim the batch, so the metadata is ambiguous. The
    marker sweep previously ran whenever the metadata produced no target and
    would have picked whichever map painted a matching marker first.
    """

    batch = BatchPosition(id="batch-1", name="Position_1")
    first = SearchMap(id="sm-1", name="SearchMap_1", linked_batch_position_ids=[batch.id])
    second = SearchMap(id="sm-2", name="SearchMap_2", linked_batch_position_ids=[batch.id])
    session = _session_with(tmp_path, search_maps=[first, second], batch_positions=[batch])
    window = _window(session)

    context = window._marker_context_for_batch_position(batch)
    resolution = window._search_map_resolution_for_batch_position(batch, context)

    assert resolution.ambiguous is True
    assert resolution.target is None
    # The metadata rule decided; the marker sweep never ran.
    assert resolution.provenance == "search_map.linked_metadata"
    assert set(resolution.candidate_ids) == {"sm-1", "sm-2"}


def test_a_refused_jump_explains_itself_on_the_page_not_only_in_the_status_bar(
    tmp_path: Path,
) -> None:
    """E1's unresolved-relationship variant had no caller at all.

    It was modelled and tested but never reached a surface, so the only place a
    reviewer was told a relationship could not be resolved was a status message
    that the very next action overwrites.
    """

    from tomography_session_browser.ui.empty_states import KIND_UNRESOLVED_RELATIONSHIP

    batch = BatchPosition(id="batch-1", name="Position_1")
    first = SearchMap(id="sm-1", name="SearchMap_1", linked_batch_position_ids=[batch.id])
    second = SearchMap(id="sm-2", name="SearchMap_2", linked_batch_position_ids=[batch.id])
    session = _session_with(tmp_path, search_maps=[first, second], batch_positions=[batch])
    window = _window(session)
    window.tabs.setCurrentIndex(TAB_LABELS.index("Batch position"))

    window._viewer_navigation_requested(batch, "search_map")

    panel = window._viewer_tabs["Batch position"].empty_state_panel
    state = panel.state()
    assert state is not None
    assert state.kind == KIND_UNRESOLVED_RELATIONSHIP
    assert "ambiguous between" in state.detail
    # It says what survives, so an inert action does not read as a broken one.
    assert any("remain readable" in line for line in state.evidence)
    # And it offers no destination, which is the entire point.
    assert state.primary is None


def test_the_refusal_panel_never_covers_a_loaded_image(tmp_path: Path) -> None:
    batch = BatchPosition(id="batch-1", name="Position_1")
    first = SearchMap(id="sm-1", name="SearchMap_1", linked_batch_position_ids=[batch.id])
    second = SearchMap(id="sm-2", name="SearchMap_2", linked_batch_position_ids=[batch.id])
    session = _session_with(tmp_path, search_maps=[first, second], batch_positions=[batch])
    window = _window(session)
    tab = window._viewer_tabs["Batch position"]
    window.tabs.setCurrentIndex(TAB_LABELS.index("Batch position"))
    tab.viewer.has_image = lambda: True  # type: ignore[assignment]

    window._viewer_navigation_requested(batch, "search_map")

    assert tab.empty_state_panel.state() is None
