"""Frozen interaction contract for cross-entity navigation (plan item G0).

This module is deliberately data, not behaviour. It enumerates every
relationship the app can navigate along, crossed with the zero/one/many
candidate cases, and records the outcome each case must produce.

Both the service-level tests (N1) and the UI adoption tests (N2) import this
table so that they assert against one shared definition instead of two
independently written sets of expectations. ``test_navigation_service.py``
additionally asserts that every case listed here has a corresponding executable
test, so a case cannot be silently dropped.

The states are imported from the service rather than re-spelled here, so the
contract cannot drift from the vocabulary the code actually returns.
"""

from __future__ import annotations

from dataclasses import dataclass

from tomography_session_browser.services.navigation_service import (
    STATE_AMBIGUOUS,
    STATE_NAVIGABLE,
    STATE_UNRESOLVED,
)

# Relationships the resolver covers.
TILT_TO_BATCH = "tilt_series -> batch_position"
TILT_TO_SEARCH_TILE = "tilt_series -> search_tile"
TILT_TO_SEARCH_MAP = "tilt_series -> search_map"
TILT_TO_OVERVIEW = "tilt_series -> overview"
BATCH_TO_OVERVIEW = "batch_position -> overview"
BATCH_TO_SEARCH_MAP = "batch_position -> search_map"
EXPOSURE_TO_TILT = "exposure -> tilt_series"


@dataclass(frozen=True, slots=True)
class ContractCase:
    """One frozen expectation: a relationship in a given candidate situation."""

    case_id: str
    relationship: str
    rule: str
    candidates: int
    expected_state: str
    note: str


NAVIGATION_CONTRACT: tuple[ContractCase, ...] = (
    # --- tilt series -> batch position -------------------------------------
    ContractCase(
        "tilt_batch_none",
        TILT_TO_BATCH,
        "no link recorded",
        0,
        STATE_UNRESOLVED,
        "An orphan tilt series resolves to nothing and stays inspectable.",
    ),
    ContractCase(
        "tilt_batch_explicit_one",
        TILT_TO_BATCH,
        "explicit linked_batch_position_id",
        1,
        STATE_NAVIGABLE,
        "The existing unique-link destination must not change.",
    ),
    ContractCase(
        "tilt_batch_explicit_absent",
        TILT_TO_BATCH,
        "explicit linked_batch_position_id absent from scope",
        0,
        STATE_UNRESOLVED,
        "A recorded ID outside the scope falls back to reciprocal links, then unresolved.",
    ),
    ContractCase(
        "tilt_batch_reciprocal_one",
        TILT_TO_BATCH,
        "one batch lists the tilt in linked_tilt_series_ids",
        1,
        STATE_NAVIGABLE,
        "Reciprocal metadata is a valid unique link.",
    ),
    ContractCase(
        "tilt_batch_reciprocal_many",
        TILT_TO_BATCH,
        "two batches list the same tilt",
        2,
        STATE_AMBIGUOUS,
        "Previously took the first match; must now stay inert.",
    ),
    # --- tilt series -> search tile ----------------------------------------
    ContractCase(
        "tilt_tile_none",
        TILT_TO_SEARCH_TILE,
        "no link recorded",
        0,
        STATE_UNRESOLVED,
        "Unresolved exposure targets remain visible without a destination.",
    ),
    ContractCase(
        "tilt_tile_direct_one",
        TILT_TO_SEARCH_TILE,
        "one tile lists the tilt in linked_tilt_series_ids",
        1,
        STATE_NAVIGABLE,
        "Already correct before N1; must stay correct.",
    ),
    ContractCase(
        "tilt_tile_direct_many",
        TILT_TO_SEARCH_TILE,
        "two tiles list the same tilt",
        2,
        STATE_AMBIGUOUS,
        "Already inert before N1, but reported no reason; now explains itself.",
    ),
    ContractCase(
        "tilt_tile_via_batch_one",
        TILT_TO_SEARCH_TILE,
        "one tile carries the batch_position_id",
        1,
        STATE_NAVIGABLE,
        "Batch-scoped fallback when no direct tilt link exists.",
    ),
    ContractCase(
        "tilt_tile_via_batch_many",
        TILT_TO_SEARCH_TILE,
        "two tiles carry the same batch_position_id",
        2,
        STATE_AMBIGUOUS,
        "Multiple exposure areas on one batch must not be guessed between.",
    ),
    # --- tilt series -> search map -----------------------------------------
    ContractCase(
        "tilt_map_none",
        TILT_TO_SEARCH_MAP,
        "no link recorded",
        0,
        STATE_UNRESOLVED,
        "",
    ),
    ContractCase(
        "tilt_map_via_batch_one",
        TILT_TO_SEARCH_MAP,
        "batch carries linked_search_map_id",
        1,
        STATE_NAVIGABLE,
        "",
    ),
    ContractCase(
        "tilt_map_reciprocal_one",
        TILT_TO_SEARCH_MAP,
        "one map lists the tilt in linked_tilt_series_ids",
        1,
        STATE_NAVIGABLE,
        "",
    ),
    ContractCase(
        "tilt_map_reciprocal_many",
        TILT_TO_SEARCH_MAP,
        "two maps list the same tilt",
        2,
        STATE_AMBIGUOUS,
        "Previously took the first match; must now stay inert.",
    ),
    # --- tilt series -> overview -------------------------------------------
    ContractCase(
        "tilt_overview_none",
        TILT_TO_OVERVIEW,
        "no link recorded",
        0,
        STATE_UNRESOLVED,
        "",
    ),
    ContractCase(
        "tilt_overview_via_batch_one",
        TILT_TO_OVERVIEW,
        "batch carries linked_overview_id",
        1,
        STATE_NAVIGABLE,
        "",
    ),
    ContractCase(
        "tilt_overview_via_search_map_one",
        TILT_TO_OVERVIEW,
        "resolved search map holds an Overview reference",
        1,
        STATE_NAVIGABLE,
        "",
    ),
    ContractCase(
        "tilt_overview_reciprocal_many",
        TILT_TO_OVERVIEW,
        "two overviews list the resolved search map",
        2,
        STATE_AMBIGUOUS,
        "Previously took the first match; must now stay inert.",
    ),
    # --- batch position -> overview ----------------------------------------
    ContractCase(
        "batch_overview_none",
        BATCH_TO_OVERVIEW,
        "no link recorded",
        0,
        STATE_UNRESOLVED,
        "Pre-existing behaviour, retained.",
    ),
    ContractCase(
        "batch_overview_explicit_one",
        BATCH_TO_OVERVIEW,
        "explicit linked_overview_id",
        1,
        STATE_NAVIGABLE,
        "Pre-existing behaviour, retained.",
    ),
    ContractCase(
        "batch_overview_explicit_absent",
        BATCH_TO_OVERVIEW,
        "explicit linked_overview_id absent from scope, nothing else recorded",
        0,
        STATE_UNRESOLVED,
        "Must stay distinct from ambiguous.",
    ),
    ContractCase(
        "batch_overview_explicit_absent_reciprocal_one",
        BATCH_TO_OVERVIEW,
        "explicit linked_overview_id absent from scope, one Overview lists the batch",
        1,
        STATE_NAVIGABLE,
        (
            "A dangling explicit ID is silent, not vetoing: the same relationship "
            "recorded from the other side is equally explicit metadata. Matches "
            "tilt_batch_explicit_absent, which this resolver used to contradict."
        ),
    ),
    ContractCase(
        "batch_overview_reciprocal_many",
        BATCH_TO_OVERVIEW,
        "two overviews list the batch",
        2,
        STATE_AMBIGUOUS,
        "Pre-existing behaviour, retained.",
    ),
    # --- batch position -> search map --------------------------------------
    ContractCase(
        "batch_map_none",
        BATCH_TO_SEARCH_MAP,
        "no link recorded",
        0,
        STATE_UNRESOLVED,
        "Pre-existing behaviour, retained.",
    ),
    ContractCase(
        "batch_map_explicit_one",
        BATCH_TO_SEARCH_MAP,
        "explicit linked_search_map_id",
        1,
        STATE_NAVIGABLE,
        "Pre-existing behaviour, retained.",
    ),
    ContractCase(
        "batch_map_explicit_absent_reciprocal_one",
        BATCH_TO_SEARCH_MAP,
        "explicit linked_search_map_id absent from scope, one map lists the batch",
        1,
        STATE_NAVIGABLE,
        "Same unified rule as batch_overview_explicit_absent_reciprocal_one.",
    ),
    ContractCase(
        "batch_map_explicit_absent_chained_only",
        BATCH_TO_SEARCH_MAP,
        "explicit linked_search_map_id absent from scope, only a shared tilt series links a map",
        0,
        STATE_UNRESOLVED,
        (
            "The other half of the rule: evidence chained through a third entity "
            "is not consulted once an explicit ID has dangled, because preferring "
            "a two-hop inference over the recorded value would be a guess."
        ),
    ),
    ContractCase(
        "batch_map_reciprocal_many",
        BATCH_TO_SEARCH_MAP,
        "two maps list the batch",
        2,
        STATE_AMBIGUOUS,
        "Pre-existing behaviour, retained.",
    ),
    # --- exposure area -> tilt series -------------------------------------
    ContractCase("exposure_missing_middle", EXPOSURE_TO_TILT, "compacted partial acquisition list", 0, STATE_UNRESOLVED,
                 "Missing exposure slots must not shift later acquisitions."),
    ContractCase("exposure_missing_singleton", EXPOSURE_TO_TILT, "only primary exposure acquired", 0, STATE_UNRESOLVED,
                 "A unique acquired tilt does not belong to every exposure."),
    ContractCase("exposure_unrelated_singleton", EXPOSURE_TO_TILT, "queued batch with another batch's sole tilt", 0, STATE_UNRESOLVED,
                 "Candidate count is not relationship evidence."),
    ContractCase("exposure_explicit_collision", EXPOSURE_TO_TILT, "one explicit ID matches two source paths", 2, STATE_AMBIGUOUS,
                 "A weaker name or ordering rule must never override explicit ambiguity."),
    ContractCase("exposure_complete_order", EXPOSURE_TO_TILT, "complete recorded template and linked order", 1, STATE_NAVIGABLE,
                 "Complete batch-order fallback remains available."),
    ContractCase(
        "exposure_tilt_none",
        EXPOSURE_TO_TILT,
        "no exposure-specific or batch-order link recorded",
        0,
        STATE_UNRESOLVED,
        "The exposure remains inspectable, but its open action stays inert.",
    ),
    ContractCase(
        "exposure_tilt_explicit_one",
        EXPOSURE_TO_TILT,
        "one consistent explicit tilt_series_id",
        1,
        STATE_NAVIGABLE,
        "An explicit exposure-to-tilt relationship is authoritative when unique.",
    ),
    ContractCase(
        "exposure_tilt_explicit_many",
        EXPOSURE_TO_TILT,
        "conflicting explicit tilt_series_id values",
        2,
        STATE_AMBIGUOUS,
        "Conflicting marker metadata must never resolve to the first value.",
    ),
)


def contract_case_ids() -> tuple[str, ...]:
    """Every case id, for coverage assertions."""

    return tuple(case.case_id for case in NAVIGATION_CONTRACT)


def case_for(case_id: str) -> ContractCase:
    """Look up a single frozen expectation."""

    for case in NAVIGATION_CONTRACT:
        if case.case_id == case_id:
            return case
    raise KeyError(f"Unknown navigation contract case: {case_id}")
