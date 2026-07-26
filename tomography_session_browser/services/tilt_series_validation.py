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
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

from tomography_session_browser.domain.models import TiltSeries

LOGGER = logging.getLogger(__name__)


# Status constants. String literals (not an enum) so they round-trip through
# the dashboard tile model without bespoke serialisation.
STATUS_COMPLETE = "complete"
STATUS_INCOMPLETE = "incomplete"
STATUS_FAILED = "failed"
STATUS_UNKNOWN = "unknown"

# Hard-failure floor: under this section count the tilt series is treated as
# failed regardless of expected-count evidence. Tilt series with at least this
# many images are compared against the expected acquisition count instead, so
# a 5-image acquisition can be reported as incomplete rather than failed.
HARD_FAILURE_THRESHOLD = 5

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
    status: str  # one of STATUS_* constants
    actual_count: int
    expected_count: int | None
    min_tilt: float | None
    max_tilt: float | None
    tilt_increment: float | None
    reason: str
    evidence_source: str

    @property
    def is_failed(self) -> bool:
        return self.status == STATUS_FAILED

    @property
    def is_complete(self) -> bool:
        return self.status == STATUS_COMPLETE


def calculate_expected_tilt_count(
    min_tilt: float, max_tilt: float, tilt_increment: float
) -> int:
    """Return the expected number of tilt images for a given range/increment.

    Raises ``ValueError`` if ``tilt_increment`` is non-positive — a guard so
    upstream callers can surface a clear error rather than producing a
    nonsense expected count.
    """

    if tilt_increment <= 0:
        raise ValueError("tilt_increment must be positive")
    span = abs(max_tilt - min_tilt)
    return int(round(span / abs(tilt_increment))) + 1


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
    expected, min_tilt, max_tilt, increment, source = _resolve_expected_count(
        tilt_series, session_majority_count
    )

    status, reason = _classify(actual, expected)

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

    log_func = LOGGER.warning if status == STATUS_FAILED else LOGGER.debug
    log_func(
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


def summarise_validations(validations: Iterable[TiltSeriesValidation]) -> dict[str, int]:
    """Tally a list of validations into ``{status: count}`` for the dashboard."""

    summary: dict[str, int] = {
        STATUS_COMPLETE: 0,
        STATUS_INCOMPLETE: 0,
        STATUS_FAILED: 0,
        STATUS_UNKNOWN: 0,
    }
    for validation in validations:
        summary[validation.status] = summary.get(validation.status, 0) + 1
    return summary


# ----------------------------------------------------------------- internals


def actual_tilt_count(tilt_series: TiltSeries) -> int:
    """Return the number of tilt images we believe are actually present.

    Prefers the parsed mdoc sections (the most reliable source — they are
    the per-image records the microscope wrote). Falls back to the MRC
    ``nz`` (number of Z slices) if the mdoc is missing.
    """

    if tilt_series.sections:
        return len(tilt_series.sections)
    metadata = getattr(tilt_series, "mrc_metadata", None)
    if metadata is not None and metadata.nz:
        return int(metadata.nz)
    return 0


def _resolve_expected_count(
    tilt_series: TiltSeries, session_majority: int | None
) -> tuple[int | None, float | None, float | None, float | None, str]:
    """Walk planned evidence sources, then the session-majority fallback.

    Returns ``(expected_count, min_tilt, max_tilt, increment, source_label)``.
    Any of the float values may be ``None`` even on a successful classification
    when the source records only the count (e.g. session-majority inference).
    """

    # --- 1. session/microscope metadata ------------------------------------
    explicit_count, min_tilt, max_tilt, increment = _expected_from_session_metadata(tilt_series)
    if explicit_count is not None:
        return explicit_count, min_tilt, max_tilt, increment, SOURCE_SESSION_METADATA

    # --- 2. planned FEI/TFS tilt scheme in the MRC extended header ---------
    mrc_plan_count, min_tilt, max_tilt, increment = _expected_from_mrc_planned_scheme(tilt_series)
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
) -> tuple[int | None, float | None, float | None, float | None]:
    """Try to derive the expected count from session/microscope metadata.

    Tomography 5 stores the planned tilt scheme in the ``TiltStart`` /
    ``TiltEnd`` / ``TiltStep`` keys (or close variants) in parsed metadata.
    ``tilt_series.tilt_range`` is deliberately excluded because the parser
    derives it from acquired MDOC angles.

    The previous implementation also accepted ``tilt_series.tilt_count``
    as an "explicit expected count". The parser sets that field to
    ``len(sections)``, i.e. the *actual* image count — so for a failed
    1-image acquisition like sang_1 the validator returned
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
        and increment > 0
        and abs(max_tilt - min_tilt) > 0
    ):
        try:
            return (
                calculate_expected_tilt_count(min_tilt, max_tilt, increment),
                min_tilt,
                max_tilt,
                increment,
            )
        except ValueError:
            pass

    return None, min_tilt, max_tilt, increment


def _expected_from_mrc_planned_scheme(
    tilt_series: TiltSeries,
) -> tuple[int | None, float | None, float | None, float | None]:
    metadata = getattr(tilt_series, "mrc_metadata", None)
    if metadata is None:
        return None, None, None, None
    for frame in getattr(metadata, "frame_metadata", None) or []:
        raw_fields = frame.get("raw_fields") if isinstance(frame, dict) else None
        if not isinstance(raw_fields, dict):
            continue
        min_tilt = _safe_float(raw_fields.get("start_tilt_angle"))
        max_tilt = _safe_float(raw_fields.get("end_tilt_angle"))
        increment = _safe_float(raw_fields.get("tilt_per_image"))
        if (
            min_tilt is None
            or max_tilt is None
            or increment is None
            or increment <= 0
            or abs(max_tilt - min_tilt) <= 0
        ):
            continue
        try:
            return (
                calculate_expected_tilt_count(min_tilt, max_tilt, increment),
                min_tilt,
                max_tilt,
                increment,
            )
        except ValueError:
            return None, min_tilt, max_tilt, increment
    return None, None, None, None


def _observed_from_mdoc(
    tilt_series: TiltSeries,
) -> tuple[float | None, float | None, float | None]:
    angles = _mdoc_tilt_angles(tilt_series)
    if not angles:
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
    if not angles:
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
            value = _safe_float(meta.get(key))
            if value is not None:
                angles.append(value)
                break
    return angles


def _classify(actual: int, expected: int | None) -> tuple[str, str]:
    """Return ``(status, human_reason)`` from the actual / expected counts."""

    if actual == 0:
        return STATUS_FAILED, "no tilt images on disk"
    if actual < HARD_FAILURE_THRESHOLD:
        if expected is not None:
            return (
                STATUS_FAILED,
                f"only {actual} tilt image{'s' if actual != 1 else ''} present "
                f"(< {HARD_FAILURE_THRESHOLD}); expected {expected}",
            )
        return (
            STATUS_FAILED,
            f"only {actual} tilt image{'s' if actual != 1 else ''} present "
            f"(< {HARD_FAILURE_THRESHOLD})",
        )
    if expected is None:
        return (
            STATUS_UNKNOWN,
            f"{actual} tilt images present; no expected count available",
        )
    if actual >= expected:
        return STATUS_COMPLETE, f"{actual} of {expected} expected images present"
    return (
        STATUS_INCOMPLETE,
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


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _walk_float(node: Any, keys: tuple[str, ...]) -> float | None:
    """Search a (possibly nested) dict for the first matching key as float."""

    if isinstance(node, dict):
        for key in keys:
            if key in node:
                value = _safe_float(node[key])
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
