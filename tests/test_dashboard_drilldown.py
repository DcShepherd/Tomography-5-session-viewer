"""D2: dashboard affordances and drill-down.

Three defects this module fixes, each verified rather than assumed:
a zero-count card that promised a list it did not have, a defocus plot the
keyboard could not open anything from, and a dashboard that named warnings
differently from the PDF.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QToolButton

from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.models import MdocSection, Sample, Session, TiltSeries
from tomography_session_browser.ui.main_window import MainWindow
from tomography_session_browser.ui.session_presenter import (
    StatCardModel,
    WarningGroupModel,
    grouped_warnings,
)
from tomography_session_browser.ui.widgets.session_dashboard import SessionDashboard
from tomography_session_browser.ui.widgets.stat_card import StatCard


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _card(value: int) -> StatCard:
    _app()
    return StatCard(
        StatCardModel(
            label="Tilt series",
            value=value,
            sparkline=[],
            items=[],
            status_summary={},
            destination="Tilt series",
        )
    )


# --- zero-count cards must not promise a list ------------------------------


def test_a_populated_card_invites_activation() -> None:
    card = _card(12)

    assert card.cursor().shape() == Qt.CursorShape.PointingHandCursor
    assert card.focusPolicy() == Qt.FocusPolicy.StrongFocus
    assert "Press Enter or Space" in card.accessibleDescription()


def test_an_empty_card_does_not_promise_a_list() -> None:
    """It used to claim "Press Enter or Space to open the list" with 0 items."""

    card = _card(0)

    assert card.cursor().shape() == Qt.CursorShape.ArrowCursor
    assert card.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert "Press Enter or Space" not in card.accessibleDescription()
    assert "nothing to open" in card.accessibleDescription()


def test_an_empty_card_is_inert_on_activation() -> None:
    card = _card(0)
    fired: list[str] = []
    card.clicked.connect(fired.append)

    card.keyPressEvent(
        QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
    )

    assert fired == []


def test_a_populated_card_still_activates() -> None:
    card = _card(3)
    fired: list[str] = []
    card.clicked.connect(fired.append)

    for key in (Qt.Key.Key_Return, Qt.Key.Key_Space):
        card.keyPressEvent(
            QKeyEvent(QKeyEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)
        )

    assert fired == ["Tilt series", "Tilt series"]


# --- the defocus plot must be openable by keyboard -------------------------


def _plot():
    from tomography_session_browser.ui.widgets.defocus_plot import DefocusScatterPlot

    _app()
    plot = DefocusScatterPlot()

    class _Point:
        tilt_series_id = "tilt-1"
        frame_index = 0

    class _Model:
        points = [_Point()]

    plot.set_model(_Model()) if hasattr(plot, "set_model") else setattr(plot, "_model", _Model())
    plot._model = _Model()
    return plot


def test_enter_opens_from_the_defocus_plot() -> None:
    """Before D2 no key reached pointDoubleClicked, so Enter could not open."""

    plot = _plot()
    opened: list[str] = []
    highlighted: list[str] = []
    plot.pointDoubleClicked.connect(lambda tid, _f: opened.append(tid))
    plot.pointClicked.connect(lambda tid, _f: highlighted.append(tid))

    plot.keyPressEvent(
        QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
    )

    assert opened == ["tilt-1"]
    assert highlighted == []


def test_space_highlights_from_the_defocus_plot() -> None:
    plot = _plot()
    opened: list[str] = []
    highlighted: list[str] = []
    plot.pointDoubleClicked.connect(lambda tid, _f: opened.append(tid))
    plot.pointClicked.connect(lambda tid, _f: highlighted.append(tid))

    plot.keyPressEvent(
        QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier)
    )

    assert highlighted == ["tilt-1"]
    assert opened == []


def test_keyboard_matches_the_mouse_gestures() -> None:
    """Space mirrors single click; Enter mirrors double click."""

    plot = _plot()
    events: list[str] = []
    plot.pointClicked.connect(lambda *_: events.append("select"))
    plot.pointDoubleClicked.connect(lambda *_: events.append("open"))

    for key in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
        plot.keyPressEvent(
            QKeyEvent(QKeyEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)
        )

    assert events == ["select", "open", "open"]


# --- one warning vocabulary ------------------------------------------------


def test_dashboard_and_report_name_warnings_identically() -> None:
    from tomography_session_browser.reports.warning_summary import summarise_warnings

    warnings = [
        "MRC extended-header tilt angles are invalid or implausible; ignoring them.",
        "vellio_1: only 3 tilt images present",
        "vellio_2: NaN in frame dose",
    ]

    assert set(grouped_warnings(warnings)) == {
        row.category for row in summarise_warnings(warnings)
    }


def test_grouped_warnings_puts_errors_first() -> None:
    groups = list(
        grouped_warnings(
            [
                "vellio_2: NaN in frame dose",
                "vellio_1: only 3 tilt images present",
            ]
        )
    )

    assert groups[0] == "Failed tilt series collection"


def test_grouped_warnings_omits_empty_categories() -> None:
    groups = grouped_warnings(["vellio_1: only 3 tilt images present"])

    assert all(items for items in groups.values())
    assert len(groups) == 1


def test_warning_group_has_keyboard_accessible_detail_action() -> None:
    _app()
    dashboard = SessionDashboard()
    group = WarningGroupModel(
        label="Missing MDOC file",
        count=1,
        severity="warning",
        items=["Sample1 / tilt_1: No matching MDOC file found."],
    )
    emitted: list[tuple[str, list[str]]] = []
    dashboard.warning_details_requested.connect(
        lambda label, items: emitted.append((label, list(items)))
    )

    row = dashboard._warning_group_row(group)
    button = next(button for button in row.findChildren(QToolButton) if button.text() == "View")
    button.click()

    assert button.focusPolicy() != Qt.FocusPolicy.NoFocus
    assert emitted == [(group.label, group.items)]


# --- aggregates drill down to the right rows -------------------------------


def _window(tmp_path: Path):
    _app()
    tilts = []
    for index in range(3):
        mrc = tmp_path / f"t{index}.mrc"
        mrc.write_bytes(b"0")
        tilts.append(
            TiltSeries(
                id=f"t{index}",
                name=f"vellio_{index}",
                mrc_path=mrc,
                sections=[MdocSection(z_value=0)],  # 1 frame -> failed
            )
        )
    healthy_mrc = tmp_path / "ok.mrc"
    healthy_mrc.write_bytes(b"0")
    tilts.append(
        TiltSeries(
            id="ok",
            name="vellio_ok",
            mrc_path=healthy_mrc,
            sections=[MdocSection(z_value=i) for i in range(40)],
        )
    )
    sample = Sample(id="s", name="Sample1", path=tmp_path, tilt_series=tilts)
    session = Session(
        id="sess", name="Srujan", path=tmp_path, kind=SessionKind.MULTIGRID, samples=[sample]
    )
    window = MainWindow()
    window._sessions = [session]
    window._session = session
    window._active_context = session
    window._rebuild_sample_index()
    window._render_viewer_tabs()
    return window, tilts


def test_a_dashboard_filter_button_states_why_at_the_destination(tmp_path: Path) -> None:
    """A dashboard filter states why the destination list was narrowed."""

    window, _tilts = _window(tmp_path)

    window._on_dashboard_filter_requested(
        "Tilt series", "Failed", "Acquisition stopped before enough images were captured."
    )

    notes = window.context_header.notes_text()
    assert "Filtered" in notes
    assert "Acquisition stopped" in notes
