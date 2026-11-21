"""Warnings the scope's analysis adds to its recorded ones.

The dashboard's Findings and the PDF (its warning list and cover totals) both
append these, so the two count the same warnings.
"""

from __future__ import annotations

from tomography_session_browser.domain.identity import canonical_entity_key
from tomography_session_browser.presentation.dashboard.analysis import ScopeAnalysis
from tomography_session_browser.presentation.dashboard.models import AttributedWarning, DashboardScope
from tomography_session_browser.presentation.dashboard.sections.search_maps import unresolved_inferred_failed_batch_groups


def derived_scope_warnings(scope: DashboardScope, analysis: ScopeAnalysis) -> list[AttributedWarning]:
    """One warning for each inferred failed batch that no search map could take.

    Each is attributed to every tilt series it covers.
    """

    derived: list[AttributedWarning] = []
    for group in unresolved_inferred_failed_batch_groups(
        scope.search_maps,
        scope.batch_positions,
        scope.tilt_series,
        analysis.validation_by_key,
        analysis.owners,
        inferred_groups=analysis.inferred_batch_groups,
    ):
        names = ", ".join(tilt.name or tilt.id for tilt in group.tilt_series)
        message = (
            f"Failed batch {group.label} could not be associated with a search map from "
            f"explicit links or image-stage metadata ({names})."
        )
        derived.append(AttributedWarning(message, frozenset(canonical_entity_key(tilt) for tilt in group.tilt_series)))
    return derived
