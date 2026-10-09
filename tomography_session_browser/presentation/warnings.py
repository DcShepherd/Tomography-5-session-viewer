"""Shape warnings through the canonical service classification rules."""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import field
from typing import Any
from tomography_session_browser.domain.models import Sample, Session
from tomography_session_browser.presentation.dashboard.models import WarningGroupModel
from tomography_session_browser.presentation.scopes import LinkedSampleGroup
from tomography_session_browser.services.status_taxonomy import (
    WarningPresentation,
    build_warning_presentation,
    severity_for_attention,
    sort_presentations,
)
from tomography_session_browser.services.warning_classification import classify_warning, summarise_warnings


def entity_scope_warnings(value: Session | Sample, label: str, seen_entities: set[int]) -> list[str]:
    """Collect descendant warnings once without changing parser wording."""
    warnings: list[str] = []

    def add_entity(label: str, entity: Any | None) -> None:
        if entity is None or id(entity) in seen_entities:
            return
        seen_entities.add(id(entity))
        warnings.extend(prefix_warnings(label, getattr(entity, "warnings", ())))

    add_entity(f"{label} / Atlas", value.atlas)
    for attribute in ("overviews", "search_maps", "search_tiles", "batch_positions", "tilt_series"):
        for entity in getattr(value, attribute):
            add_entity(f"{label} / {entity.name or entity.id}", entity)
    return warnings


def session_warnings(session: Session) -> list[str]:
    warnings = prefix_warnings(f"{session.name} / Session", session.warnings)
    seen_entities: set[int] = set()
    for sample in session.samples:
        warnings.extend(prefix_warnings(f"{session.name} / {sample.name}", sample.warnings))
        warnings.extend(entity_scope_warnings(sample, sample.name, seen_entities))
    warnings.extend(entity_scope_warnings(session, session.name, seen_entities))
    return warnings


def warning_presentations(
    warnings: Iterable[str],
    *,
    affected_scope: str = "",
) -> list[WarningPresentation]:
    """Classify warnings once, for the dashboard and the PDF alike.

    The two surfaces previously disagreed: this presenter grouped warnings with
    ``grouped_warnings()``'s seven substring buckets while the report cover used
    the report classifier's regex rules with severities and explanations.
    Same warnings, two answers. This routes both through that classifier
    (now ``services/warning_classification.py``), the richer of the two, and
    shapes the result into the shared
    ``WarningPresentation`` from ``services/status_taxonomy.py``.

    ``grouped_warnings()`` is retained for the callers that want the raw
    strings grouped by category — the warning tree and the report body. Both
    now take their *counts* and their severity from here, so the two surfaces
    agree on the name, the number and the ordering of a finding.
    """

    return sort_presentations(
        [
            build_warning_presentation(
                condition=row.category,
                severity=row.severity,
                affected_count=row.affected_items or row.count,
                affected_scope=affected_scope,
                explanation=row.explanation,
                examples=tuple(row.examples),
            )
            for row in summarise_warnings(warnings)
        ]
    )


def warning_severity(warning: str) -> str:
    """Severity for one raw warning, from the shared classifier.

    The single entry point for "how bad is this one line?". A private
    substring rule in this module used to answer it separately, so the same
    finding could be coloured ``info`` on a row while being ordered as a
    ``warning`` in the group directly above it.
    """

    return classify_warning(warning).severity


def grouped_warnings(warnings: Iterable[str]) -> dict[str, list[str]]:
    """Group warnings by the shared classifier's categories.

    D2 migrated this off its own seven substring buckets and onto the same
    rules the report cover uses, so the dashboard and the PDF now name a
    warning identically. The signature is unchanged — callers still get an
    ordered mapping of heading to raw warning strings — but the headings are
    the shared category names and empty categories are omitted.
    """

    ordered: dict[str, list[str]] = {}
    for warning in warnings:
        ordered.setdefault(classify_warning(warning).category, []).append(warning)
    # Errors first, then warnings, then information, matching the dashboard
    # and report ordering from T1.
    severity_rank = {"error": 0, "warning": 1, "info": 2}
    return dict(
        sorted(
            ordered.items(),
            key=lambda item: (
                severity_rank.get(classify_warning(item[1][0]).severity, 1),
                -len(item[1]),
                item[0],
            ),
        )
    )


def linked_sample_group_warnings(group: LinkedSampleGroup) -> list[str]:
    warnings: list[str] = []
    seen_entities: set[int] = set()
    for member in group.samples:
        warnings.extend(prefix_warnings(member.name, member.warnings))
        warnings.extend(entity_scope_warnings(member, member.name, seen_entities))
    return warnings


def prefix_warnings(prefix: str, warnings: Iterable[str]) -> list[str]:
    return [f"{prefix}: {warning}" for warning in warnings]


def summarize_warnings(warnings: list[str]) -> list[str]:
    nan_fields: Counter[str] = Counter()
    remaining: list[str] = []
    for warning in warnings:
        if "NaN value omitted from numeric summaries" in warning:
            match = re.search(r"\b([A-Za-z][A-Za-z0-9_]*)\s*:\s*NaN value omitted", warning)
            field = match.group(1) if match else "metadata"
            nan_fields[field or "metadata"] += 1
        else:
            remaining.append(warning)
    summarized = [
        f"Multiple NaN values detected in {field} ({count} occurrences)."
        if count > 1
        else f"NaN value detected in {field}."
        for field, count in sorted(nan_fields.items())
    ]
    return summarized + remaining


def dashboard_warning_groups(warnings: list[str]) -> list[WarningGroupModel]:
    """Group raw warning text into compact dashboard rows.

    ``count`` is the number of **affected objects**, not the number of warning
    strings — the same number the report cover prints. The two surfaces used to
    disagree by an order of magnitude on real data ("85 tilt series have NaN
    dose fields" against "2,805 warnings"), which made the dashboard and the
    PDF look like they were describing different sessions. The raw strings
    remain in ``items`` for the expanded view.

    Severity comes from the shared classifier for the same reason: a private
    substring rule here was a third opinion about a finding the report and the
    ordering had already classified.
    """

    raw_by_category = grouped_warnings(warnings)
    return [
        WarningGroupModel(
            label=presentation.condition,
            count=presentation.affected_count,
            severity=severity_for_attention(presentation.attention),
            items=list(raw_by_category.get(presentation.condition, ())),
        )
        for presentation in warning_presentations(warnings)
    ]
