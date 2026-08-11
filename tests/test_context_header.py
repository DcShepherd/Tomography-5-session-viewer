"""C1: the context header renders the stack and explains unavailability inline."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from tomography_session_browser.ui.context_stack import ContextStack, build_context_stack
from tomography_session_browser.ui.widgets.context_header import (
    EMPTY_SCOPE_TEXT,
    SCOPE_MIN_WIDTH_PX,
    ContextHeader,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _header() -> ContextHeader:
    _app()
    return ContextHeader()


def test_empty_header_says_no_session_rather_than_nothing() -> None:
    header = _header()

    assert header.scope_text() == EMPTY_SCOPE_TEXT
    assert header.crumb_text() == ""
    assert header.clear_filter_button.isVisible() is False


def test_a_long_scope_name_elides_instead_of_widening_the_header() -> None:
    """Regression: the scope was the one label in the strip that could not elide.

    A plain QLabel sized itself to the full text, so a long linked-group name
    widened the header — and with it the window's minimum width — rather than
    shortening. It keeps a floor so the crumb tail gives way first.
    """

    header = _header()
    header.set_context_stack(
        build_context_stack(scope_name="Reference collection B", section_key="Search")
    )
    short_minimum = header.minimumSizeHint().width()

    header.set_context_stack(
        build_context_stack(
            scope_name="Reference_Collection_C_With_A_Very_Long_Name_and_more",
            scope_kind="linked",
            section_key="Search",
        )
    )

    # The header's minimum no longer tracks the length of the name, so a long
    # scope cannot widen the window.
    assert header.minimumSizeHint().width() == short_minimum
    assert header.scope_label.minimumWidth() == SCOPE_MIN_WIDTH_PX

    # The full value survives for callers and the tooltip...
    assert header.scope_text().startswith("Reference_Collection_C")
    assert header.scope_label.toolTip() == header.scope_text()
    # ...while the rendering shortens to whatever room the label is given.
    header.scope_label.resize(SCOPE_MIN_WIDTH_PX, 20)
    assert header.scope_label.text() != header.scope_text()


def test_scope_is_shown_separately_from_the_crumb_tail() -> None:
    header = _header()
    header.set_context_stack(
        build_context_stack(
            scope_name="Reference_Collection_B_20260319",
            scope_kind="linked",
            section_key="Search",
            selection_name="vellio_1",
            marker_name="Tile 19",
        )
    )

    assert header.scope_text() == "Reference_Collection_B_20260319 (LS)"
    # The scope has its own emphasised label, so the tail must not repeat it.
    assert "Reference_Collection_B_20260319" not in header.crumb_text()
    assert "Search tiles" in header.crumb_text()
    assert "vellio_1 · Tile 19" in header.crumb_text()


def test_an_active_filter_shows_a_count_and_a_clear_action() -> None:
    header = _header()
    header.set_context_stack(
        build_context_stack(
            scope_name="Session A",
            section_key="Search map",
            filter_text="vellio",
            filter_visible=3,
            filter_total=20,
        )
    )

    assert "3 of 20" in header.notes_text()
    assert header.clear_filter_button.isHidden() is False


def test_clearing_the_filter_emits_a_request() -> None:
    header = _header()
    header.set_context_stack(
        build_context_stack(scope_name="S", filter_text="x", filter_visible=0, filter_total=9)
    )
    received: list[bool] = []
    header.filter_clear_requested.connect(lambda: received.append(True))

    header.clear_filter_button.click()

    assert received == [True]


def test_borrowed_atlas_context_is_stated_inline() -> None:
    header = _header()
    header.set_context_stack(
        build_context_stack(
            scope_name="Reference collection B",
            scope_kind="linked",
            relationship="Atlas context borrowed from Reference atlas 1",
        )
    )

    assert "borrowed from Reference atlas 1" in header.notes_text()


def test_unavailable_reasons_appear_inline_not_only_in_a_tooltip() -> None:
    """A disabled action's reason must be readable without hovering."""

    header = _header()
    header.set_context_stack(build_context_stack(scope_name="Session A"))
    header.set_unavailable_reasons(
        ["Search map link is ambiguous between: Map 1, Map 2."]
    )

    assert "Unavailable:" in header.notes_text()
    assert "ambiguous between: Map 1, Map 2." in header.notes_text()
    assert header.notes_label.isHidden() is False


def test_a_shared_reason_is_stated_once() -> None:
    header = _header()
    header.set_unavailable_reasons(["No linked overview found.", "No linked overview found."])

    assert header.notes_text().count("No linked overview found.") == 1


def test_blank_reasons_are_ignored() -> None:
    header = _header()
    header.set_unavailable_reasons(["", "   ", "Real reason."])

    assert header.notes_text() == "Unavailable: Real reason."


def test_notes_row_hides_when_there_is_nothing_to_say() -> None:
    header = _header()
    header.set_context_stack(build_context_stack(scope_name="Session A", section_key="Overview"))

    assert header.notes_text() == ""
    assert header.notes_label.isVisible() is False


def test_accessible_description_carries_the_whole_context() -> None:
    header = _header()
    header.set_context_stack(
        build_context_stack(
            scope_name="Session A",
            section_key="Tilt series",
            selection_name="vellio_1_2",
            filter_text="vellio",
            filter_visible=2,
            filter_total=8,
        )
    )

    description = header.accessibleDescription()
    assert "Session A" in description
    assert "Tilt series" in description
    assert "vellio_1_2" in description
    assert "2 of 8" in description


def test_setting_a_none_stack_resets_cleanly() -> None:
    header = _header()
    header.set_context_stack(build_context_stack(scope_name="Session A", filter_text="x"))
    header.set_context_stack(None)

    assert header.scope_text() == EMPTY_SCOPE_TEXT
    assert header.context_stack() == ContextStack()
    assert header.clear_filter_button.isVisible() is False


# --- live wiring -----------------------------------------------------------


def _live_window(tmp_path):
    from tomography_session_browser.domain.enums import SessionKind
    from tomography_session_browser.domain.models import Sample, SearchMap, Session
    from tomography_session_browser.ui.main_window import MainWindow

    _app()
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


def test_window_has_one_header_above_every_page(tmp_path) -> None:
    window, _maps = _live_window(tmp_path)

    assert isinstance(window.context_header, ContextHeader)
    assert window.context_header.scope_text().startswith(
        "Reference_Collection_B_20260319"
    )


def test_header_follows_the_tab_and_selection(tmp_path) -> None:
    from tomography_session_browser.ui.main_window import TAB_LABELS

    window, maps = _live_window(tmp_path)
    window.tabs.setCurrentIndex(TAB_LABELS.index("Search map"))
    window._viewer_tabs["Search map"].select_object(maps[1])
    window._refresh_context_header()

    assert "Search maps" in window.context_header.crumb_text()
    assert "Map 2" in window.context_header.crumb_text()


def test_header_clear_action_clears_the_visible_tab_filter(tmp_path) -> None:
    from tomography_session_browser.ui.main_window import TAB_LABELS

    window, _maps = _live_window(tmp_path)
    window.tabs.setCurrentIndex(TAB_LABELS.index("Search map"))
    tab = window._viewer_tabs["Search map"]
    tab.list_filter.setText("Map 2")
    window._refresh_context_header()
    assert window.context_header.clear_filter_button.isHidden() is False

    window.context_header.clear_filter_button.click()

    assert tab.list_filter.text() == ""
    # The filter note is gone. Any genuine unavailability reason for the
    # current selection legitimately stays — it is unrelated to the filter.
    assert "Filtered" not in window.context_header.notes_text()
    assert window.context_header.clear_filter_button.isVisible() is False
