"""Resolve plans from coherent, already parsed metadata records."""
from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from tomography_session_browser.domain.coerce import float_or_none
from tomography_session_browser.domain.models import TiltSeries

SOURCE_SESSION_METADATA = "session_metadata"
SOURCE_MRC_EXTENDED = "mrc_extended_header"
SOURCE_INFERRED_MAJORITY = "inferred_session_majority"
SOURCE_NONE = "none"


@dataclass(frozen=True, slots=True)
class VerifiedPlan:
    expected_count: int
    minimum: float
    maximum: float
    increment: float
    source: str
    source_file: Path | None
    field_path: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InferredCount:
    """A labelled same-grid peer count. Never a planned range."""

    expected_count: int
    source: str = SOURCE_INFERRED_MAJORITY


@dataclass(frozen=True, slots=True)
class NoPlan:
    state: Literal["absent", "invalid", "conflicting", "unsupported"] = "absent"
    expected_count: None = None
    source: str = SOURCE_NONE


PlanEvidence = VerifiedPlan | InferredCount | NoPlan


def calculate_expected_tilt_count(min_tilt: float, max_tilt: float, tilt_increment: float) -> int:
    if not all(math.isfinite(value) for value in (min_tilt, max_tilt, tilt_increment)) or tilt_increment <= 0:
        raise ValueError("tilt range must be finite and tilt_increment positive")
    quotient = abs(max_tilt - min_tilt) / tilt_increment
    if not math.isfinite(quotient):
        raise ValueError("planned tilt count is not finite")
    rounded = round(quotient)
    if not math.isclose(quotient, rounded, rel_tol=0.0, abs_tol=1e-6):
        raise ValueError("planned bounds do not align with the tilt increment")
    return rounded + 1


def _records(value: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[dict[str, Any], tuple[str, ...]]]:
    if isinstance(value, dict):
        yield value, path
        for key, child in value.items():
            yield from _records(child, (*path, str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _records(child, (*path, str(index)))


def _plans(
    records: Iterator[tuple[dict[str, Any], tuple[str, ...]]],
    fields: tuple[tuple[str, ...], ...],
    source: str,
    source_file: Path | None,
    diagnostics: list[str],
) -> list[VerifiedPlan]:
    plans: dict[tuple[float, float, float], VerifiedPlan] = {}
    for record, path in records:
        keys = tuple(next((key for key in aliases if key in record), None) for aliases in fields)
        if None in keys:
            continue
        minimum, maximum, step = (float_or_none(record[key]) if key is not None else None for key in keys)
        try:
            if minimum is None or maximum is None or step is None:
                raise ValueError("non-numeric planned tilt field")
            lower, upper = sorted((minimum, maximum))
            count = calculate_expected_tilt_count(lower, upper, step)
        except ValueError as exc:
            label = "Session metadata" if source == SOURCE_SESSION_METADATA else "MRC extended-header"
            message = f"{label} planned tilt scheme ignored: {exc}."
            if message not in diagnostics:
                diagnostics.append(message)
            continue
        plans.setdefault((lower, upper, step), VerifiedPlan(count, lower, upper, step, source, source_file, path))
    return list(plans.values())


def plan_evidence(tilt: TiltSeries, peer_count: int | None, diagnostics: list[str]) -> PlanEvidence:
    mdoc = _plans(_records(tilt.metadata or {}), (
        ("MinTilt", "TiltStart", "tilt_start", "min_tilt"),
        ("MaxTilt", "TiltEnd", "tilt_end", "max_tilt"),
        ("TiltStep", "TiltIncrement", "tilt_step", "tilt_increment"),
    ), SOURCE_SESSION_METADATA, tilt.mdoc_path, diagnostics)
    metadata = tilt.mrc_metadata
    mrc = _plans(
        ((frame["raw_fields"], ("frame_metadata", str(index), "raw_fields"))
         for index, frame in enumerate(metadata.frame_metadata or () if metadata else ())
         if isinstance(frame, dict) and isinstance(frame.get("raw_fields"), dict)),
        (("start_tilt_angle",), ("end_tilt_angle",), ("tilt_per_image",)),
        SOURCE_MRC_EXTENDED, tilt.mrc_path, diagnostics,
    )
    plans = mdoc + mrc
    if len({(plan.minimum, plan.maximum, plan.increment) for plan in plans}) > 1:
        diagnostics.append("Applicable planned tilt records disagree.")
        return NoPlan("conflicting")
    if plans:
        return plans[0]
    if peer_count is not None:
        return InferredCount(peer_count)
    return NoPlan("invalid" if diagnostics else "absent")
