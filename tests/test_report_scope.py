from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.models import Atlas, BatchPosition, Sample, Session
from tomography_session_browser.ui.project_model import build_project_tree_groups, session_key
from tomography_session_browser.ui.report_scope import (
    ReportScopeDialog,
    ReportScopeRequest,
    describe_report_scope,
    normalise_report_scope,
    sample_key,
)


def test_report_scope_linked_parent_includes_linked_sessions_once() -> None:
    atlas, first, second, _standalone = _project_sessions()
    original_groups = build_project_tree_groups([atlas, first, second])
    groups = build_project_tree_groups([atlas, first, second], {original_groups[0].key: "Renamed linked report"})
    linked = groups[0]

    scope = normalise_report_scope(
        [atlas, first, second],
        groups,
        ReportScopeRequest(group_keys=frozenset({linked.key}), session_keys=frozenset({session_key(first)})),
    )

    assert scope.sessions == (atlas, first, second)
    assert scope.project_title == "Report: Renamed linked report"
    assert len(scope.project_groups) == 1
    assert scope.project_groups[0].display_name == "Renamed linked report"
    assert scope.project_groups[0].kind == "LS"


def test_report_scope_collection_selection_adds_linked_atlas_once() -> None:
    atlas, first, second, standalone = _project_sessions()
    groups = build_project_tree_groups([atlas, first, second, standalone])

    scope = normalise_report_scope(
        [atlas, first, second, standalone],
        groups,
        ReportScopeRequest(
            group_keys=frozenset(),
            session_keys=frozenset({session_key(first), session_key(second)}),
        ),
    )

    assert scope.sessions == (atlas, first, second)
    assert scope.project_title == "Report: Selected sessions"
    assert [group.display_name for group in scope.project_groups] == [first.name, second.name]
    assert describe_report_scope(scope).endswith("1 supporting Atlas session")


def test_report_scope_atlas_only_does_not_add_collections() -> None:
    atlas, first, second, _standalone = _project_sessions()
    groups = build_project_tree_groups([atlas, first, second])

    scope = normalise_report_scope(
        [atlas, first, second],
        groups,
        ReportScopeRequest(group_keys=frozenset(), session_keys=frozenset({session_key(atlas)})),
    )

    assert scope.sessions == (atlas,)
    assert scope.project_title == f"Report: {atlas.name}"
    assert scope.project_groups[0].kind == "AT"
    assert describe_report_scope(scope) == "Report will include 1 Atlas session"


def test_report_scope_can_filter_multigrid_session_to_selected_samples() -> None:
    atlas, collection, _second, _standalone = _project_sessions()
    collection.samples.append(
        Sample(
            id="Collection_A-rothia",
            name="Rothia",
            path=Path("C:/project") / "Collection_A" / "Rothia",
            batch_positions=[BatchPosition(id="Collection_A-rothia-bp", name="Position 2")],
        )
    )
    collection.samples.append(
        Sample(
            id="Collection_A-pyralis",
            name="pyralis",
            path=Path("C:/project") / "Collection_A" / "pyralis",
            batch_positions=[BatchPosition(id="Collection_A-pyralis-bp", name="Position 3")],
        )
    )
    groups = build_project_tree_groups([atlas, collection])

    scope = normalise_report_scope(
        [atlas, collection],
        groups,
        ReportScopeRequest(
            group_keys=frozenset(),
            session_keys=frozenset(),
            sample_keys=frozenset(
                {
                    sample_key(collection, collection.samples[0]),
                    sample_key(collection, collection.samples[1]),
                }
            ),
        ),
    )

    assert scope.sessions[0] is atlas
    assert scope.sessions[1] is not collection
    assert scope.sessions[1].name == collection.name
    assert [sample.name for sample in scope.sessions[1].samples] == ["Sample1", "Rothia"]
    assert [sample.name for sample in collection.samples] == ["Sample1", "Rothia", "pyralis"]
    assert scope.project_title == "Report: Selected sessions"


def test_report_scope_dialog_selects_current_scope_and_requires_selection() -> None:
    app = QApplication.instance() or QApplication([])
    atlas, first, second, standalone = _project_sessions()
    groups = build_project_tree_groups([atlas, first, second, standalone])
    dialog = ReportScopeDialog(groups, current_scope=None)
    app.processEvents()

    assert not dialog.generate_button.isEnabled()

    dialog.select_scope(groups[0])
    assert dialog.generate_button.isEnabled()
    assert dialog.selected_request().group_keys == frozenset({groups[0].key})

    dialog.clear_selection()
    assert not dialog.generate_button.isEnabled()

    dialog.select_scope(standalone)
    assert dialog.generate_button.isEnabled()
    assert dialog.selected_request().group_keys == frozenset(
        {next(group.key for group in groups if group.sessions == [standalone])}
    )

    linked_item = dialog._group_items[groups[0].key]
    child = linked_item.child(1)
    dialog.clear_selection()
    child.setCheckState(0, Qt.CheckState.Checked)
    app.processEvents()
    assert dialog.generate_button.isEnabled()
    assert dialog.selected_request().group_keys == frozenset()
    assert dialog.selected_request().session_keys == frozenset({session_key(first)})


def test_report_scope_dialog_exposes_selectable_multigrid_samples() -> None:
    app = QApplication.instance() or QApplication([])
    _atlas, _first, _second, collection = _project_sessions()
    collection.samples.append(
        Sample(
            id="Standalone_C-vellio",
            name="vellio",
            path=Path("C:/project") / "Standalone_C" / "vellio",
            batch_positions=[BatchPosition(id="Standalone_C-vellio-bp", name="Position 2")],
        )
    )
    collection.samples.append(
        Sample(
            id="Standalone_C-rothia",
            name="Rothia",
            path=Path("C:/project") / "Standalone_C" / "Rothia",
            batch_positions=[BatchPosition(id="Standalone_C-rothia-bp", name="Position 3")],
        )
    )
    groups = build_project_tree_groups([collection])
    dialog = ReportScopeDialog(groups, current_scope=None)
    app.processEvents()

    collection_item = dialog._group_items[groups[0].key]
    assert [collection_item.child(index).text(0) for index in range(collection_item.childCount())] == [
        "Sample1",
        "vellio",
        "Rothia",
    ]
    assert collection_item.child(0).text(1) == "Sample"
    assert collection_item.child(0).toolTip(1) == "Data collection sample"

    dialog.clear_selection()
    collection_item.child(1).setCheckState(0, Qt.CheckState.Checked)
    collection_item.child(2).setCheckState(0, Qt.CheckState.Checked)
    app.processEvents()

    assert dialog.selected_request().sample_keys == frozenset(
        {
            sample_key(collection, collection.samples[1]),
            sample_key(collection, collection.samples[2]),
        }
    )

    dialog.select_scope(collection.samples[1])
    assert dialog.selected_request().sample_keys == frozenset({sample_key(collection, collection.samples[1])})


def test_report_scope_dialog_parent_child_tristate_for_multigrid_samples() -> None:
    app = QApplication.instance() or QApplication([])
    collection = _multigrid_session("Multigrid_A", "C:/project", ("vellio", "Rothia", "pyralis"))
    groups = build_project_tree_groups([collection])
    dialog = ReportScopeDialog(groups, current_scope=None)
    app.processEvents()

    parent = dialog._group_items[groups[0].key]
    vellio = parent.child(0)
    rothia = parent.child(1)
    pyralis = parent.child(2)

    parent.setCheckState(0, Qt.CheckState.Checked)
    app.processEvents()
    assert parent.checkState(0) == Qt.CheckState.Checked
    assert [parent.child(index).checkState(0) for index in range(parent.childCount())] == [
        Qt.CheckState.Checked,
        Qt.CheckState.Checked,
        Qt.CheckState.Checked,
    ]
    assert dialog.selected_request().group_keys == frozenset({groups[0].key})

    pyralis.setCheckState(0, Qt.CheckState.Unchecked)
    app.processEvents()
    assert parent.checkState(0) == Qt.CheckState.PartiallyChecked
    assert dialog.selected_request().group_keys == frozenset()
    assert dialog.selected_request().sample_keys == frozenset(
        {
            sample_key(collection, collection.samples[0]),
            sample_key(collection, collection.samples[1]),
        }
    )

    scope = normalise_report_scope([collection], groups, dialog.selected_request())
    assert [sample.name for sample in scope.sessions[0].samples] == ["vellio", "Rothia"]

    rothia.setCheckState(0, Qt.CheckState.Unchecked)
    app.processEvents()
    assert dialog.selected_request().sample_keys == frozenset({sample_key(collection, collection.samples[0])})
    vellio.setCheckState(0, Qt.CheckState.Unchecked)
    app.processEvents()
    assert parent.checkState(0) == Qt.CheckState.Unchecked
    assert not dialog.generate_button.isEnabled()


def test_report_scope_mixes_selected_samples_and_unrelated_collection() -> None:
    atlas = _atlas_session("Atlas_A", "C:/project")
    atlas_id = "Atlas_A/Sample1/Atlas/Atlas.dm"
    multigrid = _multigrid_session("Collection_A", "C:/project", ("vellio", "Rothia", "pyralis"), atlas_id)
    unrelated = _collection_session("Standalone_C", "C:/project", None)
    groups = build_project_tree_groups([atlas, multigrid, unrelated])

    scope = normalise_report_scope(
        [atlas, multigrid, unrelated],
        groups,
        ReportScopeRequest(
            group_keys=frozenset({next(group.key for group in groups if group.sessions == [unrelated])}),
            session_keys=frozenset(),
            sample_keys=frozenset(
                {
                    sample_key(multigrid, multigrid.samples[0]),
                    sample_key(multigrid, multigrid.samples[1]),
                }
            ),
        ),
    )

    assert scope.sessions[0] is atlas
    assert scope.sessions[1].name == "Collection_A"
    assert [sample.name for sample in scope.sessions[1].samples] == ["vellio", "Rothia"]
    assert scope.sessions[2] is unrelated
    assert "pyralis" not in [sample.name for sample in scope.sessions[1].samples]


def _project_sessions() -> tuple[Session, Session, Session, Session]:
    atlas = _atlas_session("Atlas_A", "C:/project")
    atlas_id = "Atlas_A/Sample1/Atlas/Atlas.dm"
    first = _collection_session("Collection_A", "C:/project", atlas_id)
    second = _collection_session("Collection_B", "C:/project", atlas_id)
    standalone = _collection_session("Standalone_C", "C:/project", None)
    return atlas, first, second, standalone


def _atlas_session(name: str, root: str) -> Session:
    sample = Sample(
        id=f"{name}-sample",
        name="Sample1",
        path=Path(root) / name / "Sample1",
        atlas=Atlas(id=f"{name}-atlas"),
    )
    return Session(
        id=name,
        name=name,
        path=Path(root) / name,
        kind=SessionKind.ATLAS_SCREENING,
        samples=[sample],
    )


def _collection_session(name: str, root: str, atlas_id: str | None) -> Session:
    metadata = {"Session.dm": {"AtlasId": {"value": atlas_id}}} if atlas_id else {}
    sample = Sample(
        id=f"{name}-sample",
        name="Sample1",
        path=Path(root) / name / "Sample1",
        batch_positions=[BatchPosition(id=f"{name}-bp", name="Position 1")],
        metadata=metadata,
    )
    return Session(
        id=name,
        name=name,
        path=Path(root) / name,
        kind=SessionKind.MULTIGRID,
        samples=[sample],
    )


def _multigrid_session(
    name: str,
    root: str,
    sample_names: tuple[str, ...],
    atlas_id: str | None = None,
) -> Session:
    samples = []
    for index, sample_name in enumerate(sample_names):
        metadata = {"Session.dm": {"AtlasId": {"value": atlas_id}}} if atlas_id else {}
        samples.append(
            Sample(
                id=f"{name}-{sample_name}",
                name=sample_name,
                path=Path(root) / name / sample_name,
                batch_positions=[BatchPosition(id=f"{name}-{sample_name}-bp", name=f"Position {index + 1}")],
                metadata=metadata,
            )
        )
    return Session(
        id=name,
        name=name,
        path=Path(root) / name,
        kind=SessionKind.MULTIGRID,
        samples=samples,
    )


# --- P1: the preview must describe what the generator will produce ---------


def _app_instance() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_the_preview_names_supporting_atlas_sessions_the_user_never_ticked() -> None:
    """Normalisation pulls in Atlas context; the preview has to say so.

    A summary built from the checkboxes would report only the collection the
    user selected, and under-report what the PDF is about to contain.
    """

    atlas, first, _second, _standalone = _project_sessions()
    sessions = [atlas, first]
    groups = build_project_tree_groups(sessions)
    request = ReportScopeRequest(
        group_keys=frozenset(),
        session_keys=frozenset({session_key(first)}),
    )

    scope = normalise_report_scope(sessions, groups, request)
    text = describe_report_scope(scope)

    assert atlas in scope.sessions, "normalisation should add the supporting Atlas"
    assert "supporting Atlas session" in text
    assert text.startswith("Report will include")


def test_the_dialog_preview_comes_from_normalise_report_scope() -> None:
    """Pinning the wiring: preview and generation share one source of truth."""

    _app_instance()
    atlas, first, _second, _standalone = _project_sessions()
    sessions = [atlas, first]
    groups = build_project_tree_groups(sessions)
    dialog = ReportScopeDialog(groups, sessions=sessions)
    request = ReportScopeRequest(
        group_keys=frozenset(),
        session_keys=frozenset({session_key(first)}),
    )

    preview = dialog.scope_preview_text(request)
    expected = describe_report_scope(normalise_report_scope(sessions, groups, request))

    assert preview == expected
    dialog.deleteLater()


def test_an_empty_selection_previews_as_nothing() -> None:
    _app_instance()
    atlas, first, _second, _standalone = _project_sessions()
    groups = build_project_tree_groups([atlas, first])
    dialog = ReportScopeDialog(groups, sessions=[atlas, first])

    assert dialog.scope_preview_text(
        ReportScopeRequest(group_keys=frozenset(), session_keys=frozenset())
    ) == "Nothing selected"
    dialog.deleteLater()


def test_the_primary_action_names_the_scope_under_review() -> None:
    """"Select current view" described the widget, not the science."""

    _app_instance()
    atlas, first, _second, _standalone = _project_sessions()
    groups = build_project_tree_groups([atlas, first])
    dialog = ReportScopeDialog(groups, current_scope=first, sessions=[atlas, first])

    label = dialog.select_current_button.text()

    assert "Select current review scope" in label
    assert "Collection_A" in label
    assert "current view" not in label.lower()
    dialog.deleteLater()


def test_the_primary_action_degrades_when_there_is_no_current_scope() -> None:
    _app_instance()
    atlas, first, _second, _standalone = _project_sessions()
    groups = build_project_tree_groups([atlas, first])
    dialog = ReportScopeDialog(groups, current_scope=None, sessions=[atlas, first])

    assert dialog.select_current_button.text() == "Select current review scope"
    assert dialog.select_current_button.isEnabled() is False
    dialog.deleteLater()


def test_a_custom_group_name_changes_presentation_not_membership() -> None:
    """The rename contract, asserted against the normalised scope."""

    atlas, first, second, _standalone = _project_sessions()
    sessions = [atlas, first, second]
    plain = build_project_tree_groups(sessions)
    renamed = build_project_tree_groups(sessions, {plain[0].key: "Renamed for the report"})
    request = ReportScopeRequest(
        group_keys=frozenset({plain[0].key}), session_keys=frozenset()
    )

    before = normalise_report_scope(sessions, plain, request)
    after = normalise_report_scope(sessions, renamed, request)

    assert [s.name for s in before.sessions] == [s.name for s in after.sessions]
    assert describe_report_scope(before) == describe_report_scope(after)
    assert after.project_groups[0].display_name == "Renamed for the report"
