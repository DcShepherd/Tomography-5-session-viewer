"""Classify tilt series from planned evidence and preloaded observations.

The grid registry supplies batch interruption and eligible peer context.
Classification and evidence selection do not access the filesystem.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Set
from dataclasses import dataclass
from typing import Iterable

from tomography_session_browser.domain.models import BatchPosition, Sample, TiltSeries
from tomography_session_browser.domain.identity import entity_key_text
from tomography_session_browser.domain.status import RowStatus, ValidationStatus, parse_recorded_status
from tomography_session_browser.services.entity_links import tilt_parent_candidates
from tomography_session_browser.services.acquisition_plan.evidence import (
    PlanEvidence, VerifiedPlan, plan_evidence,
    SOURCE_INFERRED_MAJORITY as SOURCE_INFERRED_MAJORITY,
    SOURCE_MRC_EXTENDED as SOURCE_MRC_EXTENDED,
    SOURCE_NONE as SOURCE_NONE,
    SOURCE_SESSION_METADATA as SOURCE_SESSION_METADATA,
    calculate_expected_tilt_count as calculate_expected_tilt_count,
)
from tomography_session_browser.services.acquisition_plan.coverage import Coverage, uniform_coverage
from tomography_session_browser.services.acquisition_plan.observation import Observation, observe
from tomography_session_browser.services.acquisition_plan.peers import peer_count

LOGGER = logging.getLogger(__name__)


# Hard-failure floor: under this section count the tilt series is treated as
# failed regardless of expected-count evidence. Tilt series with at least this
# many images are compared against the expected acquisition count instead, so
# a 5-image acquisition can be reported as incomplete rather than failed.
HARD_FAILURE_THRESHOLD = 5

# The reason given when the MDOC says a stack is complete but its image data is
# not (a verdict the counts alone do not explain).
UNREADABLE_STACK_REASON = (
    "MRC image data is incomplete or unreadable; MDOC records cannot establish stack completeness."
)

@dataclass(slots=True)
class TiltSeriesValidation:
    """Result of validating one tilt series."""

    tilt_series_id: str
    name: str
    status: ValidationStatus
    plan: PlanEvidence
    observation: Observation
    reason: str
    interrupted: bool = False
    coverage: Coverage | None = None
    diagnostics: tuple[str, ...] = ()


TiltRange = tuple[float | None, float | None, float | None]


def tilt_range_evidence(validation: TiltSeriesValidation) -> tuple[bool, TiltRange]:
    """``(planned, (minimum, maximum, increment))``.

    A verified plan gives the planned range. Otherwise the range is what the
    stack measured, and readers must label it as observed.
    """
    plan = validation.plan
    if isinstance(plan, VerifiedPlan):
        return True, (plan.minimum, plan.maximum, plan.increment)
    return False, validation.observation.measured_range


def validate_tilt_series(
    tilt_series: TiltSeries,
    *,
    session_majority_count: int | None = None,
    interrupted: bool = False,
) -> TiltSeriesValidation:
    """Classify ``tilt_series`` against any available expected-count evidence.

    ``session_majority_count`` is the modal section count across the same
    data-collection group, used as a fallback when no explicit per-series
    expected count can be derived.
    """

    name = tilt_series.name or tilt_series.id
    observation = observe(tilt_series)
    # An unreadable stack has no known count; it is classified Unknown below.
    actual = observation.reported_count or 0
    diagnostics: list[str] = []
    plan = plan_evidence(tilt_series, None if interrupted else session_majority_count, diagnostics)
    expected = plan.expected_count

    status, reason = _classify(actual, expected)
    coverage = None
    metadata = tilt_series.mrc_metadata
    if metadata is not None and metadata.data_complete is False:
        if metadata.available_frames is None:
            status, reason = ValidationStatus.UNKNOWN, UNREADABLE_STACK_REASON
        elif actual < HARD_FAILURE_THRESHOLD:
            reason = f"{reason} {UNREADABLE_STACK_REASON}"
        else:
            status, reason = ValidationStatus.INCOMPLETE, UNREADABLE_STACK_REASON
    elif actual >= HARD_FAILURE_THRESHOLD:
        if isinstance(plan, VerifiedPlan) and actual >= plan.expected_count:
            coverage = uniform_coverage(observation.angles, plan.minimum, plan.increment, plan.expected_count)
            if coverage is Coverage.COVERED and metadata is not None and metadata.data_complete is True:
                status = ValidationStatus.COMPLETE
                reason = f"All {plan.expected_count} planned tilt angles are covered."
                if interrupted:
                    reason += " The batch reported an interruption."
            elif coverage is Coverage.GAP:
                status = ValidationStatus.INCOMPLETE
                reason = f"{actual} of {plan.expected_count} expected images present. The measured angles leave gaps in the plan."
            else:
                status = ValidationStatus.INCOMPLETE if interrupted else ValidationStatus.UNKNOWN
                reason = f"{actual} of {plan.expected_count} expected images present. Angular coverage could not be confirmed."
        elif interrupted and expected is None:
            status = ValidationStatus.INCOMPLETE
            reason = "Acquisition reported an interruption. Planned range unavailable. Collected data remain available."
    if observation.angular_disagreement:
        diagnostics.append("MRC and MDOC angles disagree. Used MRC frame angles.")
    if diagnostics:
        reason = " ".join((reason if reason.endswith(".") else reason + ".", *diagnostics))

    result = TiltSeriesValidation(
        tilt_series_id=tilt_series.id,
        name=name,
        status=status,
        plan=plan,
        observation=observation,
        reason=reason,
        interrupted=interrupted,
        coverage=coverage,
        diagnostics=tuple(diagnostics),
    )

    # A failed tilt series is normal, expected input for this app — it is
    # exactly what the reviewer opened the tool to find. Logging each one at
    # WARNING put a wall of text on the console of anyone launching from a
    # terminal, and the same series is validated many times per load, so the
    # same line repeated 8+ times. The outcome is surfaced in the UI; keep
    # the console record at debug level.
    LOGGER.debug(
        "tilt-series validation: name=%s path=%s actual=%d expected=%s source=%s "
        "min_tilt=%s max_tilt=%s increment=%s status=%s reason=%s",
        name,
        getattr(tilt_series, "mrc_path", None),
        actual,
        expected,
        plan.source,
        *tilt_range_evidence(result)[1],
        status,
        reason,
    )
    return result


def validate_session_tilt_series(
    tilt_series: Iterable[TiltSeries],
    *,
    batch_positions: Iterable[BatchPosition] = (),
) -> list[TiltSeriesValidation]:
    """Validate every tilt series in a list, sharing session-majority context.

    The "session majority" is the modal *actual* section count across the
    set, restricted to entries that look complete enough to be the canonical
    case (≥ ``HARD_FAILURE_THRESHOLD`` sections). This deliberately ignores
    1-image failed acquisitions when computing the mode, so a session of
    18 × 35-image series + 3 × 1-image series infers ``expected = 35``.
    """

    series_list = list(tilt_series)
    batches = tuple(batch_positions)
    keys_by_id: dict[str, set[str]] = {}
    for series in series_list:
        keys_by_id.setdefault(series.id, set()).add(entity_key_text(series))
    interrupted = set()
    if batches:
        for series in series_list:
            parents, _ = tilt_parent_candidates(series, batches, tilt_id_collides=len(keys_by_id[series.id]) > 1)
            if len(parents) == 1 and parse_recorded_status(parents[0].status) == RowStatus.FAILED:
                interrupted.add(id(series))
    majority = peer_count(series_list, interrupted, HARD_FAILURE_THRESHOLD)
    return [
        validate_tilt_series(series, session_majority_count=majority, interrupted=id(series) in interrupted)
        for series in series_list
    ]


class GridValidations:
    """Every tilt series' verdict, each against its own grid, worked out once.

    The one place the app classifies tilt series. A grid is a sample: its
    series share a tilt scheme, so they vote on the expected count together
    (``validate_session_tilt_series``) and never with another grid's. A
    scope narrower than a grid is still judged by the whole grid.

    A series outside the supplied grids receives only its own evidence verdict.
    """

    def __init__(self, samples: Iterable[Sample] = (), *, ungridded: Iterable[TiltSeries] = ()) -> None:
        # ``id(series)`` -> (series, verdict). The series is kept so a reused
        # ``id`` of another object never reads this one's verdict.
        self._by_object: dict[int, tuple[TiltSeries, TiltSeriesValidation]] = {}
        for sample in samples:
            members = list(sample.tilt_series)
            for series, validation in zip(members, validate_session_tilt_series(members, batch_positions=sample.batch_positions), strict=True):
                self._by_object.setdefault(id(series), (series, validation))
        for series in ungridded:
            self._own_verdict(series)
        self._keys_by_id: dict[str, set[str]] = {}
        for series, _ in self._by_object.values():
            self._keys_by_id.setdefault(series.id, set()).add(entity_key_text(series))

    def _known(self, series: TiltSeries) -> TiltSeriesValidation | None:
        entry = self._by_object.get(id(series))
        return entry[1] if entry is not None and entry[0] is series else None

    def _own_verdict(self, series: TiltSeries) -> TiltSeriesValidation:
        known = self._known(series)
        if known is not None:
            return known
        verdict = validate_tilt_series(series)
        verdict.reason += " No grid context. Peer and batch inference are unavailable."
        self._by_object[id(series)] = series, verdict
        return verdict

    def for_series(self, tilt_series: Iterable[TiltSeries]) -> dict[str, TiltSeriesValidation]:
        """Verdicts by verified ID, or canonical row key when an ID collides.

        Collision knowledge comes from the whole registry, so narrowing to one
        grid cannot turn a colliding ID back into a unique identity. Consumers
        that have an entity use ``validation_for``, never a raw-ID lookup.
        """

        series_list = list(tilt_series)
        known = {id(series): self._own_verdict(series) for series in series_list}
        keys = {series.id: set(self._keys_by_id.get(series.id, ())) for series in series_list}
        for series in series_list:
            keys[series.id].add(entity_key_text(series))
        return {
            (entity_key_text(series) if len(keys[series.id]) > 1 else series.id): known[id(series)]
            for series in series_list
        }

    def verdict(self, tilt_series: TiltSeries) -> TiltSeriesValidation:
        """``tilt_series``' verdict; validated alone when no grid holds it."""

        return self._own_verdict(tilt_series)


def validation_key(validations: Mapping[str, object], series: TiltSeries) -> str:
    """The registry key for this entity; raw IDs remain compatible when unique."""
    canonical = entity_key_text(series)
    return canonical if canonical in validations else series.id


def validation_for(validations: Mapping[str, TiltSeriesValidation], series: TiltSeries) -> TiltSeriesValidation | None:
    return validations.get(validation_key(validations, series))


def tilt_in_set(series: TiltSeries, identities: Set[str]) -> bool:
    """Membership in validation-derived keys, including unique recorded IDs."""
    return entity_key_text(series) in identities or series.id in identities


def batch_has_failed_tilt(batch: BatchPosition, series: Iterable[TiltSeries], failed: Set[str]) -> bool:
    """Unique IDs keep their recorded link; collisions require reciprocal evidence."""
    if any(identifier in failed for identifier in batch.linked_tilt_series_ids):
        return True
    return any(tilt.linked_batch_position_id == batch.id and tilt_in_set(tilt, failed) for tilt in series)


def summarise_validations(validations: Iterable[TiltSeriesValidation]) -> dict[str, int]:
    """Tally a list of validations into ``{status: count}`` for the dashboard."""

    summary: dict[str, int] = {
        ValidationStatus.COMPLETE: 0,
        ValidationStatus.INCOMPLETE: 0,
        ValidationStatus.FAILED: 0,
        ValidationStatus.UNKNOWN: 0,
    }
    for validation in validations:
        summary[validation.status] = summary.get(validation.status, 0) + 1
    return summary


# ----------------------------------------------------------------- internals


def actual_tilt_count(tilt_series: TiltSeries) -> int:
    """Return the number of tilt images we believe are actually present.

    Prefer parser-observed image payload, then declared stack depth. MDOC
    records are a fallback only: their presence does not prove images exist.
    """

    metadata = getattr(tilt_series, "mrc_metadata", None)
    if metadata is not None:
        if metadata.available_frames is not None:
            return max(0, metadata.available_frames)
        if metadata.nz is not None and metadata.nz > 0:
            return int(metadata.nz)
    if tilt_series.sections:
        return len(tilt_series.sections)
    return 0


def planned_tilt_warnings(tilt_series: TiltSeries) -> list[str]:
    """Load-time diagnostics; callers record them once on the parsed entity."""
    diagnostics: list[str] = []
    plan_evidence(tilt_series, None, diagnostics)
    return diagnostics


def _classify(actual: int, expected: int | None) -> tuple[ValidationStatus, str]:
    """Return ``(status, human_reason)`` from the actual / expected counts."""

    if actual == 0:
        return ValidationStatus.FAILED, "no tilt images on disk"
    if actual < HARD_FAILURE_THRESHOLD:
        if expected is not None:
            return (
                ValidationStatus.FAILED,
                f"only {actual} tilt image{'s' if actual != 1 else ''} present "
                f"(< {HARD_FAILURE_THRESHOLD}); expected {expected}",
            )
        return (
            ValidationStatus.FAILED,
            f"only {actual} tilt image{'s' if actual != 1 else ''} present "
            f"(< {HARD_FAILURE_THRESHOLD})",
        )
    if expected is None:
        return (
            ValidationStatus.UNKNOWN,
            f"{actual} tilt images present; no expected count available",
        )
    if actual >= expected:
        return ValidationStatus.COMPLETE, f"{actual} of {expected} expected images present"
    return (
        ValidationStatus.INCOMPLETE,
        f"{actual} of {expected} expected images present",
    )
