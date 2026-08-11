"""E1: state-specific empty and recovery experiences.

One generic message — "No items for the current selection." — used to cover six
genuinely different situations, none of which said what to do next.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.models import Sample, SearchMap, Session
from tomography_session_browser.ui.empty_states import (
    COMMAND_CHOOSE_ANOTHER,
    COMMAND_CLEAR_FILTER,
    COMMAND_COPY_DETAILS,
    COMMAND_OPEN_SESSION,
    COMMAND_RETRY,
    KIND_FILTER_NO_RESULTS,
    KIND_LOAD_FAILED,
    KIND_MISSING_PREVIEW,
    KIND_NO_ENTITIES,
    KIND_NO_SESSION,
    KIND_UNRESOLVED_RELATIONSHIP,
    EmptyState,
    filter_no_results_state,
    load_failed_state,
    missing_preview_state,
    no_entities_state,
    no_session_state,
    unresolved_relationship_state,
    viewer_empty_state,
)


ALL_STATES = [
    no_session_state(),
    no_entities_state(scope_name="Reference collection B", entity_label="Search maps"),
    filter_no_results_state(filter_text="vellio", total=20, entity_label="Search maps"),
    missing_preview_state(name="Map 1", expected_path="C:/x.jpg", metadata_available=True),
    load_failed_state(path="C:/broken", error="Not a Tomography 5 folder."),
    unresolved_relationship_state(subject="Position_1", explanation="Ambiguous between A and B."),
]


# --- the shared invariant --------------------------------------------------


@pytest.mark.parametrize("state", ALL_STATES, ids=lambda s: s.kind)
def test_each_state_names_what_happened(state: EmptyState) -> None:
    assert state.title
    assert state.kind


@pytest.mark.parametrize("state", ALL_STATES, ids=lambda s: s.kind)
def test_no_state_offers_two_competing_primary_actions(state: EmptyState) -> None:
    """A screen with two equally weighted recommendations has none."""

    assert state.actions.count(state.primary) <= 1
    if state.primary is not None:
        assert state.primary not in state.secondary


def test_the_six_variants_are_distinct() -> None:
    assert len({state.kind for state in ALL_STATES}) == 6


# --- individual variants ---------------------------------------------------


def test_no_session_offers_opening_one() -> None:
    state = no_session_state()

    assert state.kind == KIND_NO_SESSION
    assert state.primary.command == COMMAND_OPEN_SESSION
    assert "Open session folders" == state.primary.label


def test_an_empty_scope_names_the_scope_and_the_entity() -> None:
    state = no_entities_state(
        scope_name="Reference_Collection_B_20260319",
        entity_label="Search maps",
    )

    assert state.kind == KIND_NO_ENTITIES
    assert "Reference_Collection_B_20260319" in state.title
    assert "search maps" in state.title.lower()
    # It must not read as a failure: the scope loaded fine.
    assert "loaded successfully" in state.detail
    assert state.has_primary is True


def test_zero_filter_results_keeps_the_count_honest() -> None:
    state = filter_no_results_state(filter_text="vellio", total=20, entity_label="Search maps")

    assert state.kind == KIND_FILTER_NO_RESULTS
    assert state.title == "0 of 20 search maps"
    assert "vellio" in state.detail
    assert state.primary.command == COMMAND_CLEAR_FILTER
    # The reviewer is told what is still there, not only what is absent.
    assert any("20" in item for item in state.evidence)


def test_a_missing_preview_says_what_is_still_inspectable() -> None:
    """"No preview" must not imply the whole record is unusable."""

    state = missing_preview_state(
        name="Map 1",
        expected_path="C:/data/Map 1.jpg",
        metadata_available=True,
        warnings=("MRC header is shorter than 16 bytes.",),
    )

    assert state.kind == KIND_MISSING_PREVIEW
    assert "metadata remain" in state.detail
    assert any("C:/data/Map 1.jpg" in item for item in state.evidence)
    assert any("still readable" in item for item in state.evidence)
    assert any("shorter than 16 bytes" in item for item in state.evidence)
    assert state.copyable_details == "C:/data/Map 1.jpg"


def test_a_missing_preview_without_metadata_offers_nothing_false() -> None:
    state = missing_preview_state(name="Map 1")

    assert state.has_primary is False
    assert "metadata remain" not in state.detail


def test_a_load_failure_offers_retry_first_then_alternatives() -> None:
    state = load_failed_state(path="C:/broken", error="Not a Tomography 5 folder.")

    assert state.kind == KIND_LOAD_FAILED
    assert state.primary.command == COMMAND_RETRY
    commands = [action.command for action in state.secondary]
    assert COMMAND_CHOOSE_ANOTHER in commands
    assert COMMAND_COPY_DETAILS in commands
    assert "C:/broken" in state.copyable_details
    assert "Not a Tomography 5 folder." in state.copyable_details


def test_an_unresolved_relationship_offers_no_destination_on_purpose() -> None:
    """Offering one would be the guess the resolver refused to make."""

    state = unresolved_relationship_state(
        subject="Position_1",
        explanation="Search map link is ambiguous between: Map 1, Map 2.",
        still_inspectable=("Its metadata and overlays remain available.",),
    )

    assert state.kind == KIND_UNRESOLVED_RELATIONSHIP
    assert state.has_primary is False
    assert "ambiguous between" in state.detail
    assert state.evidence


# --- variant selection -----------------------------------------------------


def test_nothing_loaded_outranks_an_empty_scope() -> None:
    state = viewer_empty_state(
        has_sessions=False,
        total_items=0,
        filter_text="",
        scope_name="",
        entity_label="Search maps",
    )

    assert state.kind == KIND_NO_SESSION


def test_a_filter_that_matched_nothing_is_not_an_empty_scope() -> None:
    state = viewer_empty_state(
        has_sessions=True,
        total_items=20,
        filter_text="vellio",
        scope_name="Reference collection B",
        entity_label="Search maps",
    )

    assert state.kind == KIND_FILTER_NO_RESULTS


def test_an_empty_scope_with_no_filter_says_so() -> None:
    state = viewer_empty_state(
        has_sessions=True,
        total_items=0,
        filter_text="",
        scope_name="Reference collection B",
        entity_label="Search maps",
    )

    assert state.kind == KIND_NO_ENTITIES


# --- live wiring -----------------------------------------------------------


def _tab(tmp_path: Path, *, with_maps: bool):
    from tomography_session_browser.ui.main_window import TAB_LABELS, MainWindow

    QApplication.instance() or QApplication([])
    maps = [SearchMap(id="sm-1", name="Map 1"), SearchMap(id="sm-2", name="Map 2")]
    sample = Sample(
        id="s", name="Sample1", path=tmp_path, search_maps=maps if with_maps else []
    )
    session = Session(
        id="sess",
        name="Reference collection B",
        path=tmp_path,
        kind=SessionKind.MULTIGRID,
        samples=[sample],
    )
    window = MainWindow()
    window._sessions = [session]
    window._session = session
    window._active_context = session
    window._rebuild_sample_index()
    window._render_viewer_tabs()
    window.tabs.setCurrentIndex(TAB_LABELS.index("Search map"))
    return window, window._viewer_tabs["Search map"]


def test_a_filtered_list_reports_zero_of_n(tmp_path: Path) -> None:
    _window, tab = _tab(tmp_path, with_maps=True)

    tab.list_filter.setText("no-such-map")

    assert tab.list_count.text() == "0 of 2"
    assert tab.empty_state().kind == KIND_FILTER_NO_RESULTS


def test_an_unfiltered_list_reports_a_plain_total(tmp_path: Path) -> None:
    _window, tab = _tab(tmp_path, with_maps=True)

    assert tab.list_count.text() == "2"


def test_an_empty_scope_names_itself_in_the_viewer(tmp_path: Path) -> None:
    _window, tab = _tab(tmp_path, with_maps=False)

    state = tab.empty_state()

    assert state.kind == KIND_NO_ENTITIES
    assert "Reference collection B" in state.title
