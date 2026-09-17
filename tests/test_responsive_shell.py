"""R1: responsive shell driven by workspace width, not window width.

The invariant that matters most here is the last section: resizing is not a
navigation event. It must not reload a preview, reset a viewport, clear a
filter or change what is selected.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel

from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.models import (
    MdocSection,
    Overview,
    Sample,
    SearchMap,
    Session,
    TiltSeries,
)
from tomography_session_browser.ui.image_viewer import VIEWER_OBJECT_ROLE
from tomography_session_browser.ui.main_window import (
    CONTEXT_DOCK_MIN_WIDTH_PX,
    TAB_LABELS,
    WORKSPACE_MEDIUM_PILL_PX,
    WORKSPACE_NARROW_CHROME_PX,
    WORKSPACE_WIDE_PILL_PX,
    MainWindow,
)

# The plan's acceptance widths. The odd pairs straddle real thresholds.
ACCEPTANCE_SIZES = [
    (960, 640),
    (1120, 720),
    (1366, 768),
    (1419, 900),
    (1420, 900),
    (1499, 900),
    (1500, 900),
    (2000, 1100),
]


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _window(tmp_path: Path) -> MainWindow:
    _app()
    mrc = tmp_path / "t.mrc"
    mrc.write_bytes(b"0")
    sample = Sample(
        id="s",
        name="Sample1",
        path=tmp_path,
        overviews=[Overview(id="ov", name="Overview_1", image_path=tmp_path / "ov.jpg")],
        search_maps=[SearchMap(id="sm-1", name="Map 1"), SearchMap(id="sm-2", name="Map 2")],
        tilt_series=[
            TiltSeries(
                id="t1",
                name="vellio_1",
                mrc_path=mrc,
                sections=[MdocSection(z_value=i) for i in range(20)],
            )
        ],
    )
    session = Session(
        id="sess", name="Srujan", path=tmp_path, kind=SessionKind.MULTIGRID, samples=[sample]
    )
    window = MainWindow()
    window._sessions = [session]
    window._session = session
    window._active_context = session
    window._rebuild_sample_index()
    window._render_viewer_tabs()
    return window


# --- the naming collision --------------------------------------------------


def test_derived_narrowness_is_not_the_persisted_preference(tmp_path: Path) -> None:
    """``Settings.compact`` and width-derived narrowness are different things.

    Both used to be called "compact", which is how a breakpoint ends up
    overwriting something the user chose.
    """

    window = _window(tmp_path)
    window.resize(960, 640)
    window._update_responsive_chrome()

    assert window._derived_narrow_chrome() is True
    # The persisted preference is untouched by a resize.
    assert window._settings.compact is False


def test_resizing_never_writes_a_layout_preference(tmp_path: Path) -> None:
    window = _window(tmp_path)
    before = window._settings.compact

    for width, height in ACCEPTANCE_SIZES:
        window.resize(width, height)
        window._update_responsive_chrome()

    assert window._settings.compact == before


# --- workspace width, not window width -------------------------------------


def test_workspace_width_ignores_hidden_docks(tmp_path: Path) -> None:
    window = _window(tmp_path)
    window.resize(1600, 900)

    assert window.workspace_width() == 1600


def test_a_wide_window_with_wide_docks_is_still_a_cramped_workspace(
    tmp_path: Path,
) -> None:
    """The point of the whole module: the window is not the workspace."""

    window = _window(tmp_path)
    window.resize(1600, 900)
    window.show()
    _app().processEvents()
    window.resizeDocks(
        [window.project_dock, window.context_dock],
        [500, 500],
        Qt.Orientation.Horizontal,
    )
    _app().processEvents()

    try:
        assert window.workspace_width() < 1600
    finally:
        window.close()
        _app().processEvents()


def test_dragging_docks_recomputes_chrome_without_a_window_resize(
    tmp_path: Path,
) -> None:
    window = _window(tmp_path)
    window.resize(2000, 900)
    window.show()
    _app().processEvents()
    window.resizeDocks(
        [window.project_dock, window.context_dock],
        [255, 300],
        Qt.Orientation.Horizontal,
    )
    _app().processEvents()
    assert window._toolbar_narrow is False

    window.resizeDocks(
        [window.project_dock, window.context_dock],
        [500, 500],
        Qt.Orientation.Horizontal,
    )
    _app().processEvents()

    try:
        assert window._derived_narrow_chrome() is True
        assert window._toolbar_narrow is True
    finally:
        window.close()
        _app().processEvents()


def test_workspace_width_degrades_to_the_window_before_layout(tmp_path: Path) -> None:
    """An unshown window must not report itself as cramped.

    Reading the central widget directly did exactly that, because its width is
    meaningless until the window has been laid out.
    """

    window = _window(tmp_path)
    window.resize(2000, 1100)

    assert window.workspace_width() == 2000
    assert window._derived_narrow_chrome() is False


def test_docks_wider_than_the_window_report_a_cramped_workspace(tmp_path: Path) -> None:
    """Regression: the fallback inverted the answer at the worst moment.

    ``return width if width > 0 else self.width()`` meant that dragging the
    docks out until nothing was left in the middle reported the *full window*
    width — "wide" precisely when the reviewing area had vanished.
    """

    window = _window(tmp_path)
    window.resize(1000, 700)
    window.show()
    _app().processEvents()
    window.resizeDocks(
        [window.project_dock, window.context_dock],
        [900, 900],
        Qt.Orientation.Horizontal,
    )
    _app().processEvents()

    try:
        assert window.workspace_width() < window.width()
        assert window._derived_narrow_chrome() is True
    finally:
        window.close()
        _app().processEvents()


def test_the_project_totals_strip_follows_the_workspace_too(tmp_path: Path) -> None:
    """It was the last piece of chrome still measuring the window.

    Widening the docks compacted the toolbar, branding, pill, tabs and header
    and left this strip printing full labels into a space that could not hold
    them.
    """

    window = _window(tmp_path)
    window.resize(2400, 900)
    window.show()
    _app().processEvents()
    window.resizeDocks(
        [window.project_dock, window.context_dock],
        [255, 300],
        Qt.Orientation.Horizontal,
    )
    _app().processEvents()
    window._update_status_summary()
    wide_text = window.status_counts.text()

    # Same window, wider docks: the workspace is now cramped even though
    # ``self.width()`` has not moved at all.
    window.resizeDocks(
        [window.project_dock, window.context_dock],
        [700, 700],
        Qt.Orientation.Horizontal,
    )
    _app().processEvents()
    window._update_status_summary()

    try:
        assert "Atlases" in wide_text
        assert window.width() == 2400
        assert window.status_counts.text() != wide_text
    finally:
        window.close()
        _app().processEvents()


def test_the_totals_strip_uses_plural_section_names(tmp_path: Path) -> None:
    """C1 settled every entity section on the plural; this strip lagged."""

    window = _window(tmp_path)
    window.resize(2200, 900)
    window._update_status_summary()

    text = window.status_counts.text()

    assert "Search maps" in text
    assert "Search tiles" in text
    assert "Batch positions" in text
    assert "Search map " not in text.replace("Search maps ", "")


@pytest.mark.parametrize("width,height", [(960, 640), (1366, 768), (2000, 1100)])
def test_context_descriptors_stay_on_one_line_at_supported_sizes(
    tmp_path: Path, width: int, height: int
) -> None:
    window = _window(tmp_path)
    window.resize(width, height)
    window.context_panel.set_text(
        "Summary:\n"
        "  Batch positions: 12\n"
        "  Linked data collections: 2\n"
        "Acquisition:\n"
        "  Acquisition spot size: 6\n"
        "  Expected source: session metadata"
    )
    window.show()
    _app().processEvents()

    try:
        labels = window.context_panel.findChildren(QLabel, "metaKey")
        assert window.context_dock.width() >= CONTEXT_DOCK_MIN_WIDTH_PX
        assert len({label.width() for label in labels}) == 1
        assert all(
            label.heightForWidth(label.width()) <= label.fontMetrics().lineSpacing() + 2
            for label in labels
        )
    finally:
        window.close()
        _app().processEvents()


# --- breakpoints -----------------------------------------------------------


@pytest.mark.parametrize(("width", "expected"), [(1419, True), (1420, False)])
def test_the_narrow_chrome_threshold(tmp_path: Path, width: int, expected: bool) -> None:
    window = _window(tmp_path)
    window.resize(width, 900)

    assert window._derived_narrow_chrome() is expected
    assert WORKSPACE_NARROW_CHROME_PX == 1420


def test_pill_tiers_match_the_documented_breakpoints(tmp_path: Path) -> None:
    window = _window(tmp_path)
    pill = window.session_pill_container

    for width, expected in (
        (WORKSPACE_WIDE_PILL_PX, 300),
        (WORKSPACE_WIDE_PILL_PX - 1, 220),
        (WORKSPACE_MEDIUM_PILL_PX, 220),
        (WORKSPACE_MEDIUM_PILL_PX - 1, 150),
    ):
        window.resize(width, 900)
        window._update_responsive_chrome()
        assert pill.maximumWidth() == expected, width


# --- tabs and header recomposition -----------------------------------------


def test_narrow_tabs_drop_text_but_keep_every_tab_reachable(tmp_path: Path) -> None:
    window = _window(tmp_path)
    window.resize(960, 640)
    window._update_responsive_chrome()

    assert window.tabs.count() == len(TAB_LABELS)
    for index in range(len(TAB_LABELS)):
        assert window.tabs.tabText(index) == ""
        # The name moves to the tooltip; it must not simply vanish.
        assert window.tabs.tabToolTip(index)
        assert not window.tabs.tabIcon(index).isNull()


def test_wide_tabs_restore_their_labels_and_counts(tmp_path: Path) -> None:
    window = _window(tmp_path)
    window.resize(960, 640)
    window._update_responsive_chrome()
    window.resize(2000, 1100)
    window._update_responsive_chrome()

    assert window.tabs.tabText(TAB_LABELS.index("Search map")).startswith("Search maps")
    assert "2" in window.tabs.tabText(TAB_LABELS.index("Search map"))


def test_the_header_stacks_rather_than_starving_the_scope(tmp_path: Path) -> None:
    """The scope must never be squeezed out — it answers "what am I seeing?"."""

    window = _window(tmp_path)
    window.tabs.setCurrentIndex(TAB_LABELS.index("Search map"))
    window._viewer_tabs["Search map"].select_object(
        window._viewer_tabs["Search map"]._items[1]
    )
    window._refresh_context_header()

    window.resize(960, 640)
    window._update_responsive_chrome()

    header = window.context_header
    assert header.is_narrow() is True
    assert header.scope_text().startswith("Srujan")
    # Stacked, so the leading separator that joins them on one line is dropped.
    assert not header.crumb_text().startswith(" ›")
    assert "Map 2" in header.crumb_text()


def test_the_header_returns_to_one_line_when_there_is_room(tmp_path: Path) -> None:
    window = _window(tmp_path)
    window.resize(960, 640)
    window._update_responsive_chrome()
    window.resize(2000, 1100)
    window._update_responsive_chrome()

    assert window.context_header.is_narrow() is False


# --- resizing is not a navigation event ------------------------------------


def test_resizing_changes_nothing_the_reviewer_chose(tmp_path: Path) -> None:
    """Resize must not reload previews, clear filters or change selection."""

    window = _window(tmp_path)
    window.tabs.setCurrentIndex(TAB_LABELS.index("Search map"))
    tab = window._viewer_tabs["Search map"]
    target = tab._items[1]
    tab.select_object(target)
    tab.list_filter.setText("Map")

    rows_before = [tab.list.topLevelItem(i) for i in range(tab.list.topLevelItemCount())]
    selected_before = tab.list.currentItem().data(0, VIEWER_OBJECT_ROLE)
    filter_before = tab.list_filter.text()
    tab_before = window.tabs.currentIndex()
    scope_before = window._active_context

    for width, height in ACCEPTANCE_SIZES:
        window.resize(width, height)
        window._update_responsive_chrome()

    rows_after = [tab.list.topLevelItem(i) for i in range(tab.list.topLevelItemCount())]
    assert rows_after == rows_before, "the viewer list was rebuilt"
    assert tab.list.currentItem().data(0, VIEWER_OBJECT_ROLE) is selected_before
    assert tab.list_filter.text() == filter_before
    assert window.tabs.currentIndex() == tab_before
    assert window._active_context is scope_before


def test_every_acceptance_width_renders_without_error(tmp_path: Path) -> None:
    window = _window(tmp_path)

    for width, height in ACCEPTANCE_SIZES:
        window.resize(width, height)
        window._update_responsive_chrome()
        assert window.tabs.count() == len(TAB_LABELS)
        assert window.context_header.scope_text()
