"""V1: the empty-state variants finally have a surface.

E1 modelled six variants and tested them, but only the viewer's empty *title*
consumed them — so ``load_failed`` and ``missing_preview``, both fully
specified, could never actually be seen by anyone.
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
    COMMAND_SHOW_METADATA,
    KIND_FILTER_NO_RESULTS,
    KIND_LOAD_FAILED,
    KIND_MISSING_PREVIEW,
    filter_no_results_state,
    load_failed_state,
    missing_preview_state,
    no_session_state,
    unresolved_relationship_state,
)
from tomography_session_browser.ui.main_window import TAB_LABELS, MainWindow
from tomography_session_browser.ui.image_viewer import PreviewSources, ViewerTab
from tomography_session_browser.ui.widgets.empty_state_panel import EmptyStatePanel


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _panel() -> EmptyStatePanel:
    _app()
    panel = EmptyStatePanel()
    panel.resize(400, 300)
    return panel


# --- the variants that previously had no surface ---------------------------


def test_a_load_failure_offers_retry_and_the_alternatives() -> None:
    """This variant was unreachable before V1."""

    panel = _panel()
    panel.set_state(load_failed_state(path="C:/broken", error="Not a Tomography 5 folder."))

    assert panel.title_label.text() == "Could not load this folder"
    assert "Not a Tomography 5 folder." in panel.detail_label.text()
    assert panel.primary_button.text() == "Retry"
    assert panel.primary_button.isHidden() is False
    assert "Choose another folder" in panel.secondary_labels()
    assert "Copy details" in panel.secondary_labels()
    assert "C:/broken" in panel.evidence_label.text()


def test_a_missing_preview_shows_what_is_still_inspectable() -> None:
    panel = _panel()
    panel.set_state(
        missing_preview_state(
            name="Map 1",
            expected_path="C:/data/Map 1.jpg",
            metadata_available=True,
            warnings=("MRC header is shorter than 16 bytes.",),
        )
    )

    assert "Map 1" in panel.title_label.text()
    assert "C:/data/Map 1.jpg" in panel.evidence_label.text()
    assert "shorter than 16 bytes" in panel.evidence_label.text()


def test_copy_details_offers_the_text_and_asks_the_window_to_copy_it() -> None:
    """The panel offers the details; the window owns the clipboard.

    Both used to half-own it — the panel wrote the text and the window
    announced the copy — so a single action was split across two files with
    only a comment holding it together.
    """

    panel = _panel()
    state = load_failed_state(path="C:/broken", error="Bad folder.")
    panel.set_state(state)
    received: list[str] = []
    panel.action_requested.connect(received.append)
    QApplication.clipboard().setText("untouched")

    button = next(b for b in panel.findChildren(type(panel.primary_button)) if b.text() == "Copy details")
    button.click()

    assert received == [COMMAND_COPY_DETAILS]
    assert panel.copyable_details() == state.copyable_details
    assert QApplication.clipboard().text() == "untouched"


def test_a_failed_load_moves_focus_to_its_recovery_action() -> None:
    """A keyboard user must not have to hunt for Retry.

    Focus stayed wherever it was when the load failed, with nothing to say the
    button now existed.
    """

    panel = _panel()
    panel.show()
    QApplication.processEvents()

    panel.set_state(load_failed_state(path="C:/broken", error="Bad folder."))
    QApplication.processEvents()

    try:
        assert panel.primary_button.hasFocus() is True
    finally:
        panel.close()
        QApplication.processEvents()


# --- exactly one primary action --------------------------------------------


def test_the_primary_action_emits_its_command() -> None:
    panel = _panel()
    panel.set_state(no_session_state())
    received: list[str] = []
    panel.action_requested.connect(received.append)

    panel.primary_button.click()

    assert received == [COMMAND_OPEN_SESSION]


def test_a_variant_with_no_primary_action_hides_the_button() -> None:
    """An unresolved relationship has no destination, by design."""

    panel = _panel()
    panel.set_state(
        unresolved_relationship_state(
            subject="Position_1", explanation="Ambiguous between A and B."
        )
    )

    assert panel.primary_button.isHidden() is True
    assert "Ambiguous between A and B." in panel.detail_label.text()


def test_switching_variants_does_not_leave_stale_secondary_buttons() -> None:
    panel = _panel()
    panel.set_state(load_failed_state(path="C:/x", error="bad"))
    assert len(panel.secondary_labels()) == 2

    panel.set_state(no_session_state())

    assert panel.secondary_labels() == []


def test_clearing_the_state_empties_the_panel() -> None:
    panel = _panel()
    panel.set_state(no_session_state())

    panel.set_state(None)

    assert panel.title_label.text() == ""
    assert panel.primary_button.isHidden() is True


def test_the_panel_announces_itself() -> None:
    panel = _panel()
    state = filter_no_results_state(filter_text="vellio", total=20, entity_label="Search maps")
    panel.set_state(state)

    assert panel.accessibleName() == state.title
    assert "20" in panel.accessibleDescription()


# --- live wiring -----------------------------------------------------------


def _window(tmp_path: Path, *, with_maps: bool):
    _app()
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


def test_the_panel_appears_when_a_filter_matches_nothing(tmp_path: Path) -> None:
    _window_, tab = _window(tmp_path, with_maps=True)

    tab.list_filter.setText("no-such-map")

    assert tab.empty_state_panel.isHidden() is False
    assert "0 of 2" in tab.empty_state_panel.title_label.text()
    assert tab.empty_state_panel.primary_button.text() == "Clear filter"


def test_clearing_a_filter_reveals_the_underlying_missing_preview_state(tmp_path: Path) -> None:
    _window_, tab = _window(tmp_path, with_maps=True)
    tab.list_filter.setText("no-such-map")
    assert tab.empty_state_panel.isHidden() is False

    tab.list_filter.setText("")

    assert tab.empty_state_panel.isHidden() is False
    assert tab.empty_state_panel.state().kind == KIND_MISSING_PREVIEW


def test_the_panel_never_covers_a_loaded_image(tmp_path: Path) -> None:
    """Filtering to nothing while a preview is on screen is not an empty state.

    Found on real data: the image is real content, and hiding it behind a
    panel would destroy more information than the panel adds. That case is
    carried by the honest "0 of 20" count and the header's Clear action
    instead. Pinned so it stays a decision rather than an accident.
    """

    _window_, tab = _window(tmp_path, with_maps=True)

    class _Image:
        @staticmethod
        def has_image() -> bool:
            return True

    tab.viewer.has_image = _Image.has_image  # type: ignore[assignment]
    tab.list_filter.setText("no-such-map")

    assert tab.empty_state_panel.isHidden() is True
    assert tab.list_count.text() == "0 of 2"


def test_clear_filter_from_the_panel_actually_clears_it(tmp_path: Path) -> None:
    window, tab = _window(tmp_path, with_maps=True)
    tab.list_filter.setText("no-such-map")

    tab.empty_state_panel.primary_button.click()

    assert tab.list_filter.text() == ""
    assert tab.empty_state_panel.state().kind == KIND_MISSING_PREVIEW


def test_the_window_performs_the_copy_and_reports_it(tmp_path: Path) -> None:
    window, tab = _window(tmp_path, with_maps=True)
    state = load_failed_state(path="C:/broken", error="Bad folder.")
    tab.show_empty_state(state)
    QApplication.clipboard().setText("untouched")

    window._on_empty_state_action(COMMAND_COPY_DETAILS)

    assert QApplication.clipboard().text() == state.copyable_details
    assert "copied to the clipboard" in window.statusBar().currentMessage()


def test_copying_with_nothing_to_copy_says_so(tmp_path: Path) -> None:
    window, _tab = _window(tmp_path, with_maps=True)

    window._on_empty_state_action(COMMAND_COPY_DETAILS)

    assert "no details to copy" in window.statusBar().currentMessage()


def test_a_load_failure_outranks_a_zero_result_filter(tmp_path: Path) -> None:
    """A failed load is about the whole scope, not about one hidden row.

    The zero-filter branch used to be tested first, so an unrelated search box
    could replace Retry and the reason for it with "0 of N".
    """

    window, tab = _window(tmp_path, with_maps=True)
    tab.list_filter.setText("no-such-map")
    tab.show_empty_state(load_failed_state(path="C:/broken", error="Bad folder."))

    assert tab.empty_state_panel.state().kind == KIND_LOAD_FAILED
    assert "Retry" in tab.empty_state_panel.primary_button.text()


def test_a_missing_preview_still_gives_way_to_a_zero_result_filter(
    tmp_path: Path,
) -> None:
    """The other half of the rule: that row is not in the list to talk about."""

    window, tab = _window(tmp_path, with_maps=False)
    missing = SearchMap(id="missing", name="Unreadable map")
    tab.set_items([missing], lambda _value: PreviewSources(None), lambda value: value.name)
    assert tab.empty_state_panel.state().kind == KIND_MISSING_PREVIEW

    tab.list_filter.setText("no-such-map")

    assert tab.empty_state_panel.state().kind == KIND_FILTER_NO_RESULTS


def test_a_selected_item_with_no_preview_reaches_the_missing_preview_panel(
    tmp_path: Path,
) -> None:
    window, tab = _window(tmp_path, with_maps=False)
    missing = SearchMap(id="missing", name="Unreadable map")

    tab.set_items(
        [missing],
        lambda _value: PreviewSources(None),
        lambda value: value.name,
    )

    assert tab.empty_state_panel.state() is not None
    assert tab.empty_state_panel.state().kind == KIND_MISSING_PREVIEW
    assert "Unreadable map" in tab.empty_state_panel.title_label.text()


def test_selecting_an_mrc_backed_item_clears_the_previous_missing_preview_panel(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A row-specific empty state must not cover the next section's MRC load."""

    _app()
    mrc_path = tmp_path / "available.mrc"
    missing = SearchMap(id="missing", name="Unreadable map")
    available = SearchMap(id="available", name="Available map", mrc_path=mrc_path)
    tab = ViewerTab("empty")
    monkeypatch.setattr(tab._thread_pool, "start", lambda _task: None)
    tab.set_items(
        [missing, available],
        lambda value: PreviewSources(mrc_path if value is available else None),
        lambda value: value.name,
        auto_load_preview=False,
    )

    tab.select_object(missing)
    assert tab.empty_state_panel.isHidden() is False
    assert tab.empty_state_panel.state().kind == KIND_MISSING_PREVIEW

    tab.select_object(available)

    assert tab.empty_state_panel.isHidden() is True
    assert tab._empty_state_override is None


def test_a_failed_first_load_reaches_the_load_failure_panel(
    tmp_path: Path,
    monkeypatch,
) -> None:
    window = MainWindow()
    monkeypatch.setattr(
        "tomography_session_browser.ui.main_window.QMessageBox.critical",
        lambda *_args, **_kwargs: None,
    )

    window._on_session_load_failed(
        str(tmp_path / "broken"), True, False, False, "Not a Tomography 5 folder."
    )

    tab = window._viewer_tabs["Overview"]
    assert TAB_LABELS[window.tabs.currentIndex()] == "Overview"
    assert tab.empty_state_panel.state() is not None
    assert tab.empty_state_panel.state().kind == KIND_LOAD_FAILED


@pytest.mark.parametrize(
    "command",
    [
        COMMAND_OPEN_SESSION,
        COMMAND_CLEAR_FILTER,
        COMMAND_RETRY,
        COMMAND_CHOOSE_ANOTHER,
        COMMAND_SHOW_METADATA,
    ],
)
def test_every_offered_command_is_handled(command: str, tmp_path: Path, monkeypatch) -> None:
    """A button that emits a command nothing handles is a dead end."""

    window, _tab = _window(tmp_path, with_maps=True)
    calls: list[str] = []
    monkeypatch.setattr(window, "open_session", lambda: calls.append("open"))
    monkeypatch.setattr(window, "refresh_session", lambda: calls.append("refresh"))

    window._on_empty_state_action(command)

    if command == COMMAND_CLEAR_FILTER:
        assert window._viewer_tabs["Search map"].list_filter.text() == ""
    elif command == COMMAND_SHOW_METADATA:
        assert window.context_dock.isHidden() is False
    else:
        assert calls, f"{command} was emitted but nothing acted on it"
