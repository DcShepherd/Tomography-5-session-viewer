"""Which dropped or queued folders can load (services/session_intake.py)."""

from __future__ import annotations

from pathlib import Path

from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.services.session_intake import (
    ALREADY_LOADED_REASON,
    MISSING_REASON,
    NOT_A_SESSION_REASON,
    SESSION_FILE_REASON,
    DropReceipt,
    FolderCheck,
    assess_paths,
    path_identity_key,
    recheck_unchecked,
)


def _classify(path: Path) -> SessionKind:
    return {
        "atlas": SessionKind.ATLAS_SCREENING,
        "grid": SessionKind.MULTIGRID,
        "collection": SessionKind.COLLECTION,
        "single": SessionKind.SINGLE_COLLECTION,
    }.get(Path(path).name.split("_")[0], SessionKind.UNKNOWN)


def _folders(tmp_path: Path, *names: str) -> list[Path]:
    paths = []
    for name in names:
        folder = tmp_path / name
        folder.mkdir()
        paths.append(folder)
    return paths


def test_every_session_folder_ready_reads_as_valid(tmp_path: Path) -> None:
    paths = _folders(tmp_path, "atlas", "collection_1", "collection_2")

    intake = assess_paths(paths, _classify)

    assert intake.tone == "valid"
    assert intake.ready_paths == tuple(str(path) for path in paths)
    assert [folder.badge for folder in intake.folders] == ["AT", "DC", "DC"]
    assert intake.title == "Release to load 3 sessions"
    assert intake.detail == "Sessions that share an atlas are linked"
    assert intake.footnote == ""


def test_kind_labels_follow_the_classification(tmp_path: Path) -> None:
    paths = _folders(tmp_path, "atlas", "grid", "collection", "single")

    labels = [folder.kind_label for folder in assess_paths(paths, _classify).folders]

    assert labels == ["Atlas screening", "Multi-grid collection", "Data collection", "Data collection"]


def test_a_folder_without_metadata_makes_the_drop_mixed(tmp_path: Path) -> None:
    atlas, collection, exports = _folders(tmp_path, "atlas", "collection", "Exports")

    intake = assess_paths([atlas, collection, exports], _classify)

    assert intake.tone == "mixed"
    assert intake.ready_paths == (str(atlas), str(collection))
    (problem,) = intake.problems
    assert problem.status == FolderCheck.NOT_A_SESSION
    assert problem.reason == NOT_A_SESSION_REASON
    assert problem.badge == ""
    assert intake.title == "2 of 3 folders can load"
    assert intake.detail == "Release to load 2 · 1 will be skipped"


def test_nothing_loadable_reads_as_invalid_and_leaves_the_project_alone(tmp_path: Path) -> None:
    (exports,) = _folders(tmp_path, "Exports")
    session_file = tmp_path / "Session.dm"
    session_file.write_text("<Session />", encoding="utf-8")

    intake = assess_paths([session_file, exports, tmp_path / "gone"], _classify)

    assert intake.tone == "invalid"
    assert not intake.can_load
    assert [folder.status for folder in intake.folders] == [
        FolderCheck.SESSION_FILE,
        FolderCheck.NOT_A_SESSION,
        FolderCheck.MISSING,
    ]
    assert intake.folders[0].reason == SESSION_FILE_REASON
    assert intake.folders[0].parent_folder == str(tmp_path)
    assert intake.folders[2].reason == MISSING_REASON
    assert intake.title == "Nothing here can load"
    assert intake.footnote == "Releasing here leaves the project unchanged"


def test_a_folder_already_in_the_project_is_not_loaded_again(tmp_path: Path) -> None:
    atlas, collection = _folders(tmp_path, "atlas", "collection")
    loaded = {path_identity_key(str(atlas).upper())}  # another spelling of it

    intake = assess_paths([atlas, collection], _classify, loaded_keys=loaded)

    assert intake.folders[0].status == FolderCheck.ALREADY_LOADED
    assert intake.folders[0].reason == ALREADY_LOADED_REASON
    assert intake.ready_paths == (str(collection),)

    only_loaded = assess_paths([atlas], _classify, loaded_keys=loaded)
    assert only_loaded.title == "Already in the project"
    assert only_loaded.detail == "These sessions are loaded already"


def test_one_folder_dropped_twice_is_checked_once(tmp_path: Path) -> None:
    (atlas,) = _folders(tmp_path, "atlas")

    intake = assess_paths([atlas, str(atlas) + "/", Path(str(atlas))], _classify)

    assert len(intake.folders) == 1
    assert intake.title == "Release to load 1 session"
    assert intake.detail == "It joins the current project"


def test_a_slow_check_leaves_the_rest_for_the_drop(tmp_path: Path) -> None:
    atlas, collection, exports = _folders(tmp_path, "atlas", "collection", "Exports")
    # The start, then one reading before each path after the first.
    ticks = iter([0.0, 1.0, 1.0])
    calls: list[str] = []

    def classify(path: Path) -> SessionKind:
        calls.append(Path(path).name)
        return _classify(path)

    intake = assess_paths([atlas, collection, exports], classify, budget_s=0.15, clock=lambda: next(ticks))

    assert calls == ["atlas"]  # the first is always checked
    assert [folder.status for folder in intake.folders] == [
        FolderCheck.READY,
        FolderCheck.UNCHECKED,
        FolderCheck.UNCHECKED,
    ]
    assert intake.tone == "valid"
    assert intake.can_load
    assert intake.title == "Release to check and load 3 folders"
    assert intake.detail == "2 folders will be checked on release"

    finished = recheck_unchecked(intake, classify)

    assert calls == ["atlas", "collection", "Exports"]
    assert finished.ready_paths == (str(atlas), str(collection))
    assert finished.tone == "mixed"


def test_the_receipt_names_what_was_skipped_and_why(tmp_path: Path) -> None:
    atlas, exports = _folders(tmp_path, "atlas", "Exports")
    intake = assess_paths([atlas, exports], _classify)

    receipt = DropReceipt(loaded_count=1, skipped=intake.problems)

    assert receipt.title == "Loaded 1 session"
    assert receipt.skipped_title == "1 folder skipped"
    assert receipt.skipped_lines == (f"Exports — {NOT_A_SESSION_REASON}",)


def test_an_unreadable_folder_is_reported_not_raised(tmp_path: Path) -> None:
    (locked,) = _folders(tmp_path, "collection")

    def classify(_path: Path) -> SessionKind:
        raise PermissionError("denied")

    (folder,) = assess_paths([locked], classify).folders

    assert folder.status == FolderCheck.NOT_A_SESSION
    assert folder.reason == "This folder could not be read"
