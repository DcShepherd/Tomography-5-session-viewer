"""Tilt-series completeness validation.

Real Tomography 5 sessions occasionally contain tilt series whose acquisition
was aborted partway through - they end up with one or two MRC sections
instead of the planned 35-or-so. The parser doesn't know whether 35 was the
target, so the dashboard previously reported these as "complete" because
they had at least one mdoc section.

This module supplies the missing layer: a small inference engine that
combines planned metadata and peer-series evidence to decide whether a tilt
series is ``COMPLETE`` / ``INCOMPLETE`` / ``FAILED`` / ``UNKNOWN``.

Sources, in priority order:

1. **Explicit microscope/session metadata** - fields like ``TiltStart``,
   ``TiltEnd``, ``TiltStep`` if recorded.
2. **Planned MRC extended-header scheme** - ``start_tilt_angle``,
   ``end_tilt_angle`` and ``tilt_per_image`` when recorded.
3. **Session-level majority vote** - when no per-series source is available,
   take the most common section count across the same data-collection
   group as the expected value.

Acquired MDOC and MRC tilt angles remain useful observational evidence for the
displayed range and increment, but never define the expected count: an aborted
series only records the angles that were actually acquired.

A "failed" verdict overrides everything else when the actual section count
is below ``HARD_FAILURE_THRESHOLD``: fewer than five tilt images is unsalvageable
regardless of what the metadata says it was supposed to be.

All public functions are pure - no Qt, no I/O. The dashboard / context
panel call them with already-parsed ``TiltSeries`` objects.
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from collections.abc import Callable, Hashable, Mapping, Set
from dataclasses import dataclass
from typing import Any, Iterable

from tomography_session_browser.domain.coerce import float_or_none
from tomography_session_browser.domain.models import BatchPosition, Sample, TiltSeries
from tomography_session_browser.domain.identity import entity_key_text
from tomography_session_browser.domain.status import ValidationStatus
from tomography_session_browser.services.tilt_angle_service import plausible_tilt_angles

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

# Evidence-source labels used in the validation result so the UI can show the
# user *why* a verdict was reached.
SOURCE_SESSION_METADATA = "session_metadata"
# Retained as a compatibility label for older serialized/test data. Acquired
# MDOC angles are no longer emitted as an expected-count source.
SOURCE_MDOC = "mdoc"
SOURCE_MRC_EXTENDED = "mrc_extended_header"
SOURCE_INFERRED_MAJORITY = "inferred_session_majority"
SOURCE_NONE = "none"


@dataclass(slots=True)
class TiltSeriesValidation:
    """Result of validating one tilt series."""

    tilt_series_id: str
    name: str
    status: ValidationStatus
    actual_count: int
    expected_count: int | None
    min_tilt: float | None
    max_tilt: float | None
    tilt_increment: float | None
    reason: str
    evidence_source: str


def calculate_expected_tilt_count(
    min_tilt: float, max_tilt: float, tilt_increment: float
) -> int:
    """Return the expected number of tilt images for a given range/increment.

    Raises ``ValueError`` for non-finite inputs, non-positive increments or
    an overflowing quotient. Invalid planned evidence must remain recoverable.
    """

    if not all(math.isfinite(value) for value in (min_tilt, max_tilt, tilt_increment)) or tilt_increment <= 0:
        raise ValueError("tilt range must be finite and tilt_increment positive")
    span = abs(max_tilt - min_tilt)
    quotient = span / tilt_increment
    if not math.isfinite(quotient):
        raise ValueError("planned tilt count is not finite")
    return int(round(quotient)) + 1


def validate_tilt_series(
    tilt_series: TiltSeries,
    *,
    session_majority_count: int | None = None,
) -> TiltSeriesValidation:
    """Classify ``tilt_series`` against any available expected-count evidence.

    ``session_majority_count`` is the modal section count across the same
    data-collection group, used as a fallback when no explicit per-series
    expected count can be derived.
    """

    name = tilt_series.name or tilt_series.id
    actual = actual_tilt_count(tilt_series)
    diagnostics: list[str] = []
    expected, min_tilt, max_tilt, increment, source = _resolve_expected_count(
        tilt_series, session_majority_count, diagnostics
    )

    status, reason = _classify(actual, expected)
    metadata = tilt_series.mrc_metadata
    if metadata is not None and metadata.data_complete is False and status == ValidationStatus.COMPLETE:
        status = ValidationStatus.INCOMPLETE if metadata.available_frames is not None else ValidationStatus.UNKNOWN
        reason = UNREADABLE_STACK_REASON
    if diagnostics:
        reason = "; ".join((reason, *diagnostics))

    result = TiltSeriesValidation(
        tilt_series_id=tilt_series.id,
        name=name,
        status=status,
        actual_count=actual,
        expected_count=expected,
        min_tilt=min_tilt,
        max_tilt=max_tilt,
        tilt_increment=increment,
        reason=reason,
        evidence_source=source,
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
        source,
        min_tilt,
        max_tilt,
        increment,
        status,
        reason,
    )
    return result


def validate_session_tilt_series(
    tilt_series: Iterable[TiltSeries],
) -> list[TiltSeriesValidation]:
    """Validate every tilt series in a list, sharing session-majority context.

    The "session majority" is the modal *actual* section count across the
    set, restricted to entries that look complete enough to be the canonical
    case (≥ ``HARD_FAILURE_THRESHOLD`` sections). This deliberately ignores
    1-image failed acquisitions when computing the mode, so a session of
    18 × 35-image series + 3 × 1-image series infers ``expected = 35``.
    """

    series_list = list(tilt_series)
    counts = [
        actual_tilt_count(ts)
        for ts in series_list
        if actual_tilt_count(ts) >= HARD_FAILURE_THRESHOLD
    ]
    majority = _majority(counts)

    return [validate_tilt_series(ts, session_majority_count=majority) for ts in series_list]


def validate_tilt_series_by_group(
    tilt_series: Iterable[TiltSeries],
    group_key: Callable[[TiltSeries], Hashable],
) -> list[TiltSeriesValidation]:
    """Validate each group of series with that group's own session majority.

    ``group_key`` names the data-collection group (a grid) a series belongs
    to. The majority fallback is then never taken across grids with different
    tilt schemes. Results come back in input order.
    """

    series_list = list(tilt_series)
    groups: dict[Hashable, list[TiltSeries]] = {}
    for series in series_list:
        groups.setdefault(group_key(series), []).append(series)
    by_object: dict[int, TiltSeriesValidation] = {}
    for members in groups.values():
        for series, validation in zip(members, validate_session_tilt_series(members), strict=True):
            by_object[id(series)] = validation
    return [by_object[id(series)] for series in series_list]


class GridValidations:
    """Every tilt series' verdict, each against its own grid, worked out once.

    The one place the app classifies tilt series. A grid is a sample: its
    series share a tilt scheme, so they vote on the expected count together
    (``validate_session_tilt_series``) and never with another grid's. A
    scope narrower than a grid is still judged by the whole grid.

    A series in none of ``samples`` (a caller that knows no grids) is
    validated together with the other such series it is asked about.
    """

    def __init__(self, samples: Iterable[Sample] = ()) -> None:
        # ``id(series)`` -> (series, verdict). The series is kept so a reused
        # ``id`` of another object never reads this one's verdict.
        self._by_object: dict[int, tuple[TiltSeries, TiltSeriesValidation]] = {}
        for sample in samples:
            members = list(sample.tilt_series)
            for series, validation in zip(members, validate_session_tilt_series(members), strict=True):
                self._by_object.setdefault(id(series), (series, validation))
        self._keys_by_id: dict[str, set[str]] = {}
        for series, _ in self._by_object.values():
            self._keys_by_id.setdefault(series.id, set()).add(entity_key_text(series))

    def _known(self, series: TiltSeries) -> TiltSeriesValidation | None:
        entry = self._by_object.get(id(series))
        return entry[1] if entry is not None and entry[0] is series else None

    def for_series(self, tilt_series: Iterable[TiltSeries]) -> dict[str, TiltSeriesValidation]:
        """Verdicts by verified ID, or canonical row key when an ID collides.

        Collision knowledge comes from the whole registry, so narrowing to one
        grid cannot turn a colliding ID back into a unique identity. Consumers
        that have an entity use ``validation_for``, never a raw-ID lookup.
        """

        series_list = list(tilt_series)
        known = {id(series): verdict for series in series_list if (verdict := self._known(series)) is not None}
        ungridded = [series for series in series_list if id(series) not in known]
        if ungridded:
            known.update(
                (id(series), verdict)
                for series, verdict in zip(ungridded, validate_session_tilt_series(ungridded), strict=True)
            )
        keys = {series.id: set(self._keys_by_id.get(series.id, ())) for series in series_list}
        for series in series_list:
            keys[series.id].add(entity_key_text(series))
        return {
            (entity_key_text(series) if len(keys[series.id]) > 1 else series.id): known[id(series)]
            for series in series_list
        }

    def verdict(self, tilt_series: TiltSeries) -> TiltSeriesValidation:
        """``tilt_series``' verdict; validated alone when no grid holds it."""

        known = self._known(tilt_series)
        return known if known is not None else validate_tilt_series(tilt_series)


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


def _resolve_expected_count(
    tilt_series: TiltSeries, session_majority: int | None, diagnostics: list[str] | None = None,
) -> tuple[int | None, float | None, float | None, float | None, str]:
    """Walk planned evidence sources, then the session-majority fallback.

    Returns ``(expected_count, min_tilt, max_tilt, increment, source_label)``.
    Any of the float values may be ``None`` even on a successful classification
    when the source records only the count (e.g. session-majority inference).
    """

    # --- 1. session/microscope metadata ------------------------------------
    explicit_count, min_tilt, max_tilt, increment = _expected_from_session_metadata(tilt_series, diagnostics)
    if explicit_count is not None:
        return explicit_count, min_tilt, max_tilt, increment, SOURCE_SESSION_METADATA

    # --- 2. planned FEI/TFS tilt scheme in the MRC extended header ---------
    mrc_plan_count, min_tilt, max_tilt, increment = _expected_from_mrc_planned_scheme(tilt_series, diagnostics)
    if mrc_plan_count is not None:
        return mrc_plan_count, min_tilt, max_tilt, increment, SOURCE_MRC_EXTENDED

    # Acquired angles are observational. Preserve them for the displayed
    # range/increment, but do not mistake a truncated acquisition extent for
    # the planned scheme.
    observed = _observed_from_mdoc(tilt_series)
    if observed[0] is None and observed[1] is None:
        observed = _observed_from_mrc(tilt_series)
    min_tilt, max_tilt, increment = observed

    # --- 3. session majority ----------------------------------------------
    if session_majority and session_majority >= HARD_FAILURE_THRESHOLD:
        return session_majority, min_tilt, max_tilt, increment, SOURCE_INFERRED_MAJORITY

    return None, min_tilt, max_tilt, increment, SOURCE_NONE


def _expected_from_session_metadata(
    tilt_series: TiltSeries,
    diagnostics: list[str] | None = None,
) -> tuple[int | None, float | None, float | None, float | None]:
    """Try to derive the expected count from session/microscope metadata.

    Tomography 5 stores the planned tilt scheme in the ``TiltStart`` /
    ``TiltEnd`` / ``TiltStep`` keys (or close variants) in parsed metadata.
    ``tilt_series.tilt_range`` is deliberately excluded because the parser
    derives it from acquired MDOC angles.

    The previous implementation also accepted ``tilt_series.tilt_count``
    as an "explicit expected count". The parser sets that field to
    ``len(sections)``, i.e. the *actual* image count — so for a failed
    1-image acquisition like sepia_1 the validator returned
    ``expected = 1`` and never fell through to session-majority
    inference. We now require real planned-tilt metadata
    (range + increment), letting failed acquisitions drop into the
    acquired-angle observation / session-majority chain so they pick up the
    correct 35-image planned count.
    """

    metadata = getattr(tilt_series, "metadata", None) or {}

    min_tilt = _walk_float(metadata, ("MinTilt", "TiltStart", "tilt_start", "min_tilt"))
    max_tilt = _walk_float(metadata, ("MaxTilt", "TiltEnd", "tilt_end", "max_tilt"))
    increment = _walk_float(
        metadata, ("TiltStep", "TiltIncrement", "tilt_step", "tilt_increment", "step")
    )

    # Only return a count when explicit metadata contains the complete planned
    # range and increment.
    if (
        min_tilt is not None
        and max_tilt is not None
        and increment is not None
        and min_tilt != max_tilt
    ):
        try:
            return (
                calculate_expected_tilt_count(min_tilt, max_tilt, increment),
                min_tilt,
                max_tilt,
                increment,
            )
        except ValueError as exc:
            if diagnostics is not None:
                diagnostics.append(f"Session metadata planned tilt scheme ignored: {exc}.")

    return None, min_tilt, max_tilt, increment


def _expected_from_mrc_planned_scheme(
    tilt_series: TiltSeries,
    diagnostics: list[str] | None = None,
) -> tuple[int | None, float | None, float | None, float | None]:
    metadata = getattr(tilt_series, "mrc_metadata", None)
    if metadata is None:
        return None, None, None, None
    for frame in getattr(metadata, "frame_metadata", None) or []:
        raw_fields = frame.get("raw_fields") if isinstance(frame, dict) else None
        if not isinstance(raw_fields, dict):
            continue
        min_tilt = float_or_none(raw_fields.get("start_tilt_angle"))
        max_tilt = float_or_none(raw_fields.get("end_tilt_angle"))
        increment = float_or_none(raw_fields.get("tilt_per_image"))
        if (
            min_tilt is None
            or max_tilt is None
            or increment is None
            or min_tilt == max_tilt
        ):
            continue
        try:
            return (
                calculate_expected_tilt_count(min_tilt, max_tilt, increment),
                min_tilt,
                max_tilt,
                increment,
            )
        except ValueError as exc:
            message = f"MRC extended-header planned tilt scheme ignored: {exc}."
            if diagnostics is not None and message not in diagnostics:
                diagnostics.append(message)
    return None, None, None, None


def planned_tilt_warnings(tilt_series: TiltSeries) -> list[str]:
    """Load-time diagnostics; callers record them once on the parsed entity."""
    diagnostics: list[str] = []
    _resolve_expected_count(tilt_series, None, diagnostics)
    return diagnostics


def _observed_from_mdoc(
    tilt_series: TiltSeries,
) -> tuple[float | None, float | None, float | None]:
    angles = _mdoc_tilt_angles(tilt_series)
    if not plausible_tilt_angles(angles):
        return None, None, None
    if len(angles) < 2:
        return angles[0], angles[0], None
    increment = _modal_increment(angles)
    return min(angles), max(angles), increment


def _observed_from_mrc(
    tilt_series: TiltSeries,
) -> tuple[float | None, float | None, float | None]:
    metadata = getattr(tilt_series, "mrc_metadata", None)
    if metadata is None:
        return None, None, None
    angles = list(getattr(metadata, "tilt_angles", []) or [])
    if not plausible_tilt_angles(angles):
        return None, None, None
    if len(angles) < 2:
        return angles[0], angles[0], None
    increment = _modal_increment(angles)
    return min(angles), max(angles), increment


def _mdoc_tilt_angles(tilt_series: TiltSeries) -> list[float]:
    angles: list[float] = []
    for section in tilt_series.sections:
        meta = section.metadata or {}
        for key in ("TiltAngle", "tilt_angle", "Tilt"):
            value = float_or_none(meta.get(key))
            if value is not None:
                angles.append(value)
                break
    return angles


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


def _majority(values: list[int]) -> int | None:
    if not values:
        return None
    counter = Counter(values)
    return counter.most_common(1)[0][0]


def _modal_increment(values: list[float]) -> float | None:
    """Return the most common positive delta between adjacent sorted angles.

    Tilt schemes are usually uniform, so the modal step recovers the
    increment cleanly even with a few dropped sections. Falls back to
    ``None`` when no positive deltas exist.
    """

    sorted_values = sorted(values)
    deltas = [
        round(sorted_values[i + 1] - sorted_values[i], 3)
        for i in range(len(sorted_values) - 1)
    ]
    deltas = [d for d in deltas if d > 0]
    if not deltas:
        return None
    counter = Counter(deltas)
    return counter.most_common(1)[0][0]


def _walk_float(node: Any, keys: tuple[str, ...]) -> float | None:
    """Search a (possibly nested) dict for the first matching key as float."""

    if isinstance(node, dict):
        for key in keys:
            if key in node:
                value = float_or_none(node[key])
                if value is not None:
                    return value
        for value in node.values():
            found = _walk_float(value, keys)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _walk_float(item, keys)
            if found is not None:
                return found
    return None
