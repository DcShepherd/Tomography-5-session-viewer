"""C1: the context stack, and one place where tab keys become display text."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from tomography_session_browser.ui.context_stack import (
    ENTITY_DISPLAY_LABELS,
    ContextStack,
    build_context_stack,
    describe_scope_value,
    entity_display_label,
    entity_display_label_singular,
    open_action_label,
)
from tomography_session_browser.ui.main_window import TAB_LABELS


# --- terminology -----------------------------------------------------------


def test_every_tab_key_has_display_text() -> None:
    assert set(ENTITY_DISPLAY_LABELS) >= set(TAB_LABELS)


def test_the_search_tab_key_never_reaches_a_person_as_search() -> None:
    """The internal key is ``Search``; the thing on screen is ``Search tiles``.

    Renaming the key would break tab lookup at roughly thirty sites, so the key
    stays and only its display text is standardised.
    """

    assert "Search" in TAB_LABELS
    assert entity_display_label("Search") == "Search tiles"
    assert entity_display_label_singular("Search") == "search tile"
    # No section label may be the bare key.
    assert "Search" not in {
        entity_display_label(key) for key in TAB_LABELS if key != "Session"
    }


def test_entity_sections_are_consistently_plural() -> None:
    """C1's singular/plural decision: sections are collections, so plural."""

    for key in ("Overview", "Search map", "Search", "Batch position"):
        assert entity_display_label(key).endswith("s"), key


def test_an_unknown_key_is_shown_rather_than_blanked() -> None:
    assert entity_display_label("Nonsense") == "Nonsense"
    assert entity_display_label(None) == ""


def test_open_action_labels_are_verb_plus_destination() -> None:
    assert open_action_label("Tilt series") == "Open linked tilt series"
    assert open_action_label("Search") == "Open linked search tile"


# --- the stack itself ------------------------------------------------------


def test_the_stack_reads_coarsest_to_finest() -> None:
    stack = build_context_stack(
        scope_name="Reference_Collection_B_20260319",
        scope_kind="linked",
        section_key="Search",
        selection_name="vellio_1",
        marker_name="Tile 19",
    )

    assert stack.to_text() == (
        "Reference_Collection_B_20260319 (LS) › Search tiles › vellio_1 · Tile 19"
    )
    assert stack.crumbs() == (
        "Reference_Collection_B_20260319 (LS)",
        "Search tiles",
        "vellio_1 · Tile 19",
    )


def test_scope_is_separate_from_selection() -> None:
    """The selected object must never be presented as the review scope."""

    stack = build_context_stack(
        scope_name="Sample vellio",
        section_key="Tilt series",
        selection_name="vellio_1_2",
    )

    assert stack.scope == "Sample vellio"
    assert stack.selection == "vellio_1_2"
    assert stack.scope != stack.selection
    assert stack.crumbs()[0] == "Sample vellio"


@pytest.mark.parametrize(
    ("kind", "badge"),
    [("linked", "LS"), ("atlas", "AT"), ("collection", "DC"), ("unresolved", "!")],
)
def test_scope_badges_match_the_project_tree(kind: str, badge: str) -> None:
    stack = build_context_stack(scope_name="Scope", scope_kind=kind)
    assert stack.scope_badge == badge
    assert stack.scope_label == f"Scope ({badge})"


def test_empty_levels_are_omitted_not_rendered_blank() -> None:
    stack = build_context_stack(scope_name="Session A", section_key="Overview")

    assert stack.crumbs() == ("Session A", "Overviews")
    assert " ›  › " not in stack.to_text()


def test_a_stack_with_nothing_in_it_is_empty() -> None:
    assert ContextStack().is_empty is True
    assert ContextStack().to_text() == ""


def test_filter_state_is_reported_as_a_count() -> None:
    stack = build_context_stack(
        scope_name="Session A",
        section_key="Search map",
        filter_text="vellio",
        filter_visible=3,
        filter_total=20,
    )

    assert stack.has_filter is True
    assert stack.filter_summary == "3 of 20"
    assert "Filtered — 3 of 20" in stack.view_state_notes()


def test_filter_without_counts_still_says_what_is_filtering() -> None:
    stack = build_context_stack(scope_name="S", filter_text="vellio")
    assert stack.filter_summary == "Filter: vellio"


def test_no_filter_produces_no_view_state_noise() -> None:
    stack = build_context_stack(scope_name="Session A", section_key="Overview")
    assert stack.has_filter is False
    assert stack.view_state_notes() == ()


def test_relationship_context_is_surfaced_inline() -> None:
    stack = build_context_stack(
        scope_name="Reference collection B",
        scope_kind="linked",
        relationship="Atlas context borrowed from Reference atlas 1",
    )

    assert "borrowed from Reference atlas 1" in stack.view_state_notes()[0]


def test_marker_is_shown_against_its_row_not_instead_of_it() -> None:
    stack = build_context_stack(selection_name="vellio_1", marker_name="Tile 19")
    assert stack.selection_label == "vellio_1 · Tile 19"

    without_marker = build_context_stack(selection_name="vellio_1")
    assert without_marker.selection_label == "vellio_1"

    marker_only = build_context_stack(marker_name="Tile 19")
    assert marker_only.selection_label == "Tile 19"


def test_describe_scope_value_prefers_a_display_name() -> None:
    class Group:
        display_name = "Renamed group"
        name = "Automatic name"

    class Session:
        name = "Session A"

    assert describe_scope_value(Group()) == "Renamed group"
    assert describe_scope_value(Session()) == "Session A"
    assert describe_scope_value(object()) == ""


# --- live integration ------------------------------------------------------


def _live_window(tmp_path):
    from PySide6.QtWidgets import QApplication

    from tomography_session_browser.domain.enums import SessionKind
    from tomography_session_browser.domain.models import Sample, SearchMap, Session
    from tomography_session_browser.ui.main_window import MainWindow

    QApplication.instance() or QApplication([])
    maps = [SearchMap(id="sm-1", name="Map 1"), SearchMap(id="sm-2", name="Map 2")]
    sample = Sample(id="s-1", name="Sample1", path=tmp_path, search_maps=maps)
    session = Session(
        id="sess-1",
        name="Reference_Collection_B_20260319",
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
    return window, maps


def test_live_window_builds_a_scope_first_context_stack(tmp_path) -> None:
    from tomography_session_browser.ui.main_window import TAB_LABELS as LABELS

    window, maps = _live_window(tmp_path)
    window.tabs.setCurrentIndex(LABELS.index("Search map"))
    window._viewer_tabs["Search map"].select_object(maps[1])

    stack = window.current_context_stack()

    assert stack.scope == "Reference_Collection_B_20260319"
    assert stack.section == "Search maps"
    assert "Map 2" in stack.selection
    assert stack.crumbs()[0].startswith("Reference_Collection_B_20260319")


def test_live_context_stack_reports_an_active_filter(tmp_path) -> None:
    from tomography_session_browser.ui.main_window import TAB_LABELS as LABELS

    window, _maps = _live_window(tmp_path)
    window.tabs.setCurrentIndex(LABELS.index("Search map"))
    window._viewer_tabs["Search map"].list_filter.setText("Map 2")

    stack = window.current_context_stack()

    assert stack.has_filter is True
    assert stack.filter_summary == "1 of 2"


def _eyebrows(window) -> list[str]:
    from PySide6.QtWidgets import QLabel

    return [
        label.text()
        for label in window.session_dashboard.findChildren(QLabel)
        if label.objectName() == "screenEyebrow"
    ]


def test_dashboard_eyebrow_shows_the_real_scope_when_it_adds_something(tmp_path) -> None:
    """The hard-coded 'PROJECT · SESSION' eyebrow is gone.

    **Contract updated deliberately.** C1 replaced the hard-coded string with
    the real scope, and this test pinned the scope always appearing. C1 then
    also mounted the context header above *every* page — the dashboard
    included — and the dashboard title is normally the scope's own name, so on
    a session-scope dashboard the same words appeared three times within two
    lines. The eyebrow now states the scope only when the title is something
    narrower, which is when it carries information.
    """

    window, _maps = _live_window(tmp_path)
    window._render_dashboard_for_scope()

    # Title and scope agree here, so the eyebrow stays out of the way and the
    # context header above the tabs is the one place the scope is stated.
    assert "PROJECT · SESSION" not in _eyebrows(window)
    assert _eyebrows(window) == [""]
    assert "Reference_Collection_B_20260319" in window.context_header.scope_text()

    # A scope wider than the title still earns an eyebrow — a sample-scoped
    # dashboard inside a linked group is the real case.
    from tomography_session_browser.ui.context_stack import build_context_stack

    window.session_dashboard.set_context_stack(
        build_context_stack(
            scope_name="Reference_Collection_B_20260319 + Reference_Atlas_1",
            scope_kind="linked",
        )
    )
    window.session_dashboard.set_model(window.session_dashboard._model)

    assert any("REFERENCE_ATLAS_1" in text for text in _eyebrows(window)), _eyebrows(window)
