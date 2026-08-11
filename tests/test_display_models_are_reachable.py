"""Every display model this programme added must reach a user.

The UX programme introduced eight pure display models, and several of them
were built, documented and thoroughly unit-tested while nothing in the running
application ever produced or rendered one. ``unresolved_relationship_state``
had no caller and two status dimensions were never asked for. Each was reported
as delivered, because the tests it had all passed — they simply exercised the
model directly rather than the app.

A unit test cannot tell the difference between "correct" and "correct and
unreachable". This one can: it asserts that each of these names is referenced
somewhere in the application outside its own defining module. It is a coarse
check by design — it proves a wire exists, not that it carries anything — but
it fails loudly the moment a model goes back to being tested-only.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE = REPO_ROOT / "tomography_session_browser"

# (symbol, module that defines it). The defining module is excluded from the
# search so a self-reference cannot satisfy the check.
DISPLAY_MODELS: tuple[tuple[str, str], ...] = (
    ("NavigationResolution", "services/navigation_service.py"),
    ("WarningPresentation", "services/status_taxonomy.py"),
    ("EmptyState", "ui/empty_states.py"),
    ("ContextStack", "ui/context_stack.py"),
    ("ViewerViewState", "ui/image_viewer.py"),
    ("HistoryLocation", "ui/navigation_history.py"),
)

# Entry points that must be called by the application, not only by tests. Each
# of these was orphaned at some point in the programme.
#
# ``AtlasLinkResolution`` and ``resolve_atlas_for_sample`` are deliberately
# absent: they are internal to ``session_linking`` and reach the app through
# ``atlas_link_resolutions``, which is listed instead.
PRODUCTION_ENTRY_POINTS: tuple[tuple[str, str], ...] = (
    ("unresolved_relationship_state", "ui/empty_states.py"),
    ("load_failed_state", "ui/empty_states.py"),
    ("missing_preview_state", "ui/empty_states.py"),
    ("severity_for_attention", "services/status_taxonomy.py"),
    ("acquisition_state", "services/status_taxonomy.py"),
    ("apply_tier", "ui/theme.py"),
    ("borrowed_atlas_note", "ui/context_stack.py"),
    ("atlas_link_resolutions", "ui/session_linking.py"),
    ("navigation_resolution_from_candidates", "services/navigation_service.py"),
)


def _application_sources(exclude: str) -> str:
    excluded = (PACKAGE / exclude).resolve()
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in PACKAGE.rglob("*.py")
        if path.resolve() != excluded and "__pycache__" not in path.parts
    )


@pytest.mark.parametrize(
    ("symbol", "module"), DISPLAY_MODELS, ids=[name for name, _ in DISPLAY_MODELS]
)
def test_every_display_model_is_used_by_the_application(symbol: str, module: str) -> None:
    assert symbol in _application_sources(module), (
        f"{symbol} is defined in {module} and referenced nowhere else in the "
        "application. A display model no widget consumes is not delivered, "
        "however well it is unit-tested."
    )


@pytest.mark.parametrize(
    ("symbol", "module"),
    PRODUCTION_ENTRY_POINTS,
    ids=[name for name, _ in PRODUCTION_ENTRY_POINTS],
)
def test_every_entry_point_has_a_production_caller(symbol: str, module: str) -> None:
    assert symbol in _application_sources(module), (
        f"{symbol} is only reachable from tests. Either wire it into the app "
        "or delete it — a function the reviewer can never trigger is dead "
        "weight that reads as a feature."
    )


def test_removed_review_next_card_has_no_dead_production_pipeline() -> None:
    """Removing a display surface also removes its otherwise unreachable model."""

    production = _application_sources("")

    assert "What to review next" not in production
    assert "TriageActionModel" not in production
    assert "triage_action_requested" not in production
