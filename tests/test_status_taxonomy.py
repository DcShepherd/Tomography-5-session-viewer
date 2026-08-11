"""T1: status dimensions and the shared warning presentation.

The taxonomy must *describe* the vocabularies the services already produce, not
compete with them. Several tests below deliberately assert agreement with
existing code rather than restating a constant, so a future divergence fails
here instead of quietly shipping two opinions about the same object.
"""

from __future__ import annotations

import pytest

from tomography_session_browser.domain.enums import WarningSeverity
from tomography_session_browser.services import atlas_marker_style, item_status
from tomography_session_browser.services.navigation_service import (
    STATE_AMBIGUOUS,
    STATE_NAVIGABLE,
    STATE_UNRESOLVED,
)
from tomography_session_browser.services.status_taxonomy import (
    ACQUISITION_COMPLETE,
    ACQUISITION_FAILED,
    ACQUISITION_INCOMPLETE,
    ACQUISITION_PENDING,
    ACQUISITION_UNAVAILABLE,
    APPLICATION_LOAD_FAILED,
    APPLICATION_LOAD_WARNING,
    APPLICATION_LOADING,
    APPLICATION_READY,
    ATTENTION_ERROR,
    ATTENTION_INFORMATION,
    ATTENTION_WARNING,
    LINK_AMBIGUOUS,
    LINK_INFERRED,
    LINK_LINKED,
    LINK_UNRESOLVED,
    WarningPresentation,
    acquisition_state,
    application_state,
    attention_state,
    build_warning_presentation,
    is_actionable_attention,
    link_state,
    sort_presentations,
)


# --- reconciliation with what already exists -------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (item_status.STATUS_DONE, ACQUISITION_COMPLETE),
        (item_status.STATUS_PARTIAL, ACQUISITION_INCOMPLETE),
        (item_status.STATUS_INCOMPLETE_LABEL, ACQUISITION_INCOMPLETE),
        (item_status.STATUS_FAILED_LABEL, ACQUISITION_FAILED),
        (item_status.STATUS_MISSING, ACQUISITION_UNAVAILABLE),
        (item_status.STATUS_UNKNOWN_LABEL, ACQUISITION_UNAVAILABLE),
    ],
)
def test_viewer_row_statuses_map_onto_the_acquisition_dimension(status, expected) -> None:
    assert acquisition_state(status) == expected


def test_the_taxonomy_agrees_with_the_existing_row_labels() -> None:
    """The dimension must not become a second opinion about the same chip.

    ``item_status.display_status_label`` is what a reviewer already reads on a
    list row; the acquisition dimension has to say the same thing.
    """

    for status in (
        item_status.STATUS_DONE,
        item_status.STATUS_PARTIAL,
        item_status.STATUS_INCOMPLETE_LABEL,
        item_status.STATUS_FAILED_LABEL,
        item_status.STATUS_MISSING,
        item_status.STATUS_UNKNOWN_LABEL,
    ):
        assert acquisition_state(status) == item_status.display_status_label(status)


@pytest.mark.parametrize(
    ("glyph", "expected"),
    [
        (atlas_marker_style.STATUS_COLLECTED, ACQUISITION_COMPLETE),
        (atlas_marker_style.STATUS_PARTIAL, ACQUISITION_INCOMPLETE),
        (atlas_marker_style.STATUS_QUEUED, ACQUISITION_PENDING),
    ],
)
def test_atlas_glyph_statuses_map_onto_the_acquisition_dimension(glyph, expected) -> None:
    assert acquisition_state(glyph) == expected


def test_an_orphaned_failed_tilt_is_unavailable_acquisition_but_inferred_link() -> None:
    """Two dimensions, two answers — that is the point of separating them."""

    assert acquisition_state("unattributed") == ACQUISITION_UNAVAILABLE
    assert link_state(None, inferred=True) == LINK_INFERRED


@pytest.mark.parametrize(
    ("severity", "expected"),
    [
        (WarningSeverity.INFO, ATTENTION_INFORMATION),
        (WarningSeverity.WARNING, ATTENTION_WARNING),
        (WarningSeverity.ERROR, ATTENTION_ERROR),
    ],
)
def test_warning_severities_map_onto_the_attention_dimension(severity, expected) -> None:
    assert attention_state(str(severity)) == expected


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (STATE_NAVIGABLE, LINK_LINKED),
        (STATE_AMBIGUOUS, LINK_AMBIGUOUS),
        (STATE_UNRESOLVED, LINK_UNRESOLVED),
    ],
)
def test_navigation_resolutions_map_onto_the_link_dimension(state, expected) -> None:
    assert link_state(state) == expected


def test_unknown_values_degrade_conservatively() -> None:
    """An unrecognised status must not read as success."""

    assert acquisition_state("something-new") == ACQUISITION_UNAVAILABLE
    assert acquisition_state(None) == ACQUISITION_UNAVAILABLE
    assert link_state("something-new") == LINK_UNRESOLVED
    assert attention_state("something-new") == ATTENTION_WARNING


def test_application_state_is_independent_of_the_science() -> None:
    """Loading health is not acquisition health."""

    assert application_state(loading=True) == APPLICATION_LOADING
    assert application_state(loading=False) == APPLICATION_READY
    assert application_state(loading=False, warnings=3) == APPLICATION_LOAD_WARNING
    assert application_state(loading=False, failed=True) == APPLICATION_LOAD_FAILED
    # A failed load outranks a warning count.
    assert application_state(loading=False, failed=True, warnings=3) == APPLICATION_LOAD_FAILED


def test_only_warnings_and_errors_are_actionable() -> None:
    assert is_actionable_attention(ATTENTION_ERROR) is True
    assert is_actionable_attention(ATTENTION_WARNING) is True
    assert is_actionable_attention(ATTENTION_INFORMATION) is False


# --- the presentation model ------------------------------------------------


def test_presentation_carries_the_classifier_words_unchanged() -> None:
    """Parser warnings must not be rewritten, only shaped."""

    raw = ["vellio_1: only 3 tilt images present", "vellio_2: likely aborted"]
    presentation = build_warning_presentation(
        condition="Failed tilt series collection",
        severity="error",
        affected_count=2,
        explanation="Acquisition stopped before enough tilt images were captured.",
        examples=raw,
    )

    assert presentation.attention == ATTENTION_ERROR
    assert presentation.evidence == tuple(raw)
    assert presentation.raw_details == tuple(raw)
    assert presentation.why_it_matters.startswith("Acquisition stopped")


def test_a_presentation_without_a_destination_is_not_actionable() -> None:
    presentation = build_warning_presentation(condition="X", severity="warning")

    assert presentation.is_actionable is False


def test_attaching_a_destination_makes_it_actionable() -> None:
    presentation = build_warning_presentation(condition="X", severity="warning")

    with_action = presentation.with_action(
        next_action="Open the affected tilt series",
        destination="Tilt series",
    )

    assert with_action.is_actionable is True
    assert with_action.next_action == "Open the affected tilt series"
    # The original is unchanged; these are frozen values.
    assert presentation.is_actionable is False


def test_a_disabled_reason_overrides_a_destination() -> None:
    """A warning must never be a dead end without a stated reason."""

    presentation = build_warning_presentation(condition="X", severity="error").with_action(
        destination="Tilt series",
        disabled_explanation="The affected objects are outside the current scope.",
    )

    assert presentation.is_actionable is False
    assert presentation.disabled_explanation


def test_a_disabled_reason_can_be_cleared_once_a_destination_exists() -> None:
    """Regression: ``with_action`` coalesced with ``or`` and could only add.

    A warning that later acquired a destination kept its stale reason and
    stayed permanently inert. Passing ``""`` now genuinely clears the field,
    while omitting it still leaves the value alone.
    """

    inert = build_warning_presentation(condition="X", severity="error").with_action(
        disabled_explanation="The affected objects are outside the current scope."
    )

    resolved = inert.with_action(destination="Tilt series", disabled_explanation="")

    assert resolved.is_actionable is True
    assert resolved.disabled_explanation == ""
    # Omitting a field still leaves it as it was.
    assert resolved.with_action(safe_filter="Failed").destination == "Tilt series"


def test_summary_line_counts_are_readable() -> None:
    one = build_warning_presentation(condition="X", severity="info", affected_count=1)
    many = build_warning_presentation(condition="X", severity="info", affected_count=4)
    none = build_warning_presentation(condition="X", severity="info")

    assert one.summary_line == "X — 1 item"
    assert many.summary_line == "X — 4 items"
    assert none.summary_line == "X"


def test_sorting_puts_errors_first_then_the_larger_problem() -> None:
    rows = [
        build_warning_presentation(condition="info thing", severity="info", affected_count=99),
        build_warning_presentation(condition="small error", severity="error", affected_count=1),
        build_warning_presentation(condition="big warning", severity="warning", affected_count=50),
        build_warning_presentation(condition="big error", severity="error", affected_count=9),
    ]

    ordered = [row.condition for row in sort_presentations(rows)]

    assert ordered == ["big error", "small error", "big warning", "info thing"]


def test_presentations_are_immutable_value_objects() -> None:
    presentation = WarningPresentation(condition="X")

    with pytest.raises(Exception):
        presentation.condition = "Y"  # type: ignore[misc]


# --- one classification for both surfaces ----------------------------------


def test_dashboard_and_report_now_share_one_classification() -> None:
    """The two surfaces used to answer differently for the same warnings."""

    from tomography_session_browser.reports.warning_summary import summarise_warnings
    from tomography_session_browser.ui.session_presenter import warning_presentations

    warnings = [
        "vellio_1: only 3 tilt images present",
        "vellio_2: NaN in frame dose",
        "Atlas.mrc not found",
    ]

    rows = summarise_warnings(warnings)
    presentations = warning_presentations(warnings)

    assert {row.category for row in rows} == {p.condition for p in presentations}
    by_category = {row.category: row for row in rows}
    for presentation in presentations:
        row = by_category[presentation.condition]
        assert presentation.attention == attention_state(row.severity)
        assert presentation.why_it_matters == row.explanation


def test_tilt_angle_warnings_with_a_space_are_classified() -> None:
    """Regression: ``tilt-?angle`` never matched the parser's actual wording.

    The MRC parser emits "tilt angles" with a space, so a real category was
    silently degrading to "Other" on live data — and, because the report cover
    uses the same classifier, in the PDF as well.
    """

    from tomography_session_browser.reports.warning_summary import _classify_one

    for message in (
        "MRC extended-header tilt angles are invalid or implausible; ignoring them.",
        "tilt-angle metadata missing",
        "Tilt Angles disagree with the .tlt file",
    ):
        assert _classify_one(message).category == "Tilt-angle metadata fallback", message


def test_search_tile_fallback_links_are_reported_as_information() -> None:
    """A weak tile link is real link-confidence signal, not unclassified noise."""

    from tomography_session_browser.reports.warning_summary import _classify_one

    rule = _classify_one(
        "Search-map tile association used fallback matching: stage position within nearest tile."
    )

    assert rule.category == "Search-tile link used a fallback"
    assert attention_state(rule.severity) == ATTENTION_INFORMATION


def test_shared_presentations_are_ordered_by_attention() -> None:
    from tomography_session_browser.ui.session_presenter import warning_presentations

    presentations = warning_presentations(
        [
            "vellio_2: NaN in frame dose",
            "vellio_1: only 3 tilt images present",
        ]
    )

    if len(presentations) > 1:
        assert presentations[0].attention == ATTENTION_ERROR
