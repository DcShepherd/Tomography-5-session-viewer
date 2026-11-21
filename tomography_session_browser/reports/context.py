"""Context report components."""
from __future__ import annotations
from dataclasses import dataclass, field, replace
from typing import Mapping, Sequence
from tomography_session_browser.services.tilt_series_validation import validation_for, validation_key
from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.presentation.workspace import Workspace
from tomography_session_browser.domain.models import Atlas, Sample, Session, TiltSeries
from tomography_session_browser.domain.status import ValidationStatus
from tomography_session_browser.services.warning_classification import WarningSummary, summarise_warnings
from tomography_session_browser.services.tilt_series_validation import GridValidations, TiltSeriesValidation
from tomography_session_browser.presentation.dashboard.dashboard import analysed_dashboard_model
from tomography_session_browser.presentation.dashboard.models import DashboardModel
from tomography_session_browser.presentation.dashboard.derived_warnings import derived_scope_warnings
from tomography_session_browser.presentation.dashboard.scopes import scope_from
from tomography_session_browser.presentation.warnings import session_warnings
from tomography_session_browser.presentation.report_datasets import dataset_atlases, report_datasets


@dataclass(slots=True)
class SessionContext:
    session: Session
    dashboard: DashboardModel
    # Validation results keyed by tilt series id.
    validations: dict[str, TiltSeriesValidation]
    # Where they come from, for the figures' overlays (``Workspace.tilt_validations``).
    grid_validations: GridValidations
    # Samples that contain data-collection content (vs. atlas-only).
    collection_samples: list[Sample]
    # All warnings, deduped, with sample/object prefixes.
    warnings: list[str]
    warning_rows: list[WarningSummary]
    sample_atlases: dict[int, Atlas] = field(default_factory=dict)
    atlas_notes: dict[int, str] = field(default_factory=dict)
    # Where a borrowed atlas came from ("Session / Sample"), display only.
    atlas_sources: dict[int, str] = field(default_factory=dict)

    @classmethod
    def build(cls, session: Session, *, workspace: Workspace | None = None) -> "SessionContext":
        workspace = workspace if workspace is not None else Workspace.build([session])
        # The dashboard's own verdicts: every tilt series validated once,
        # each grid against its own expected count.
        dashboard, analysis = analysed_dashboard_model(session, workspace=workspace)
        validations = analysis.validation_by_key

        # Atlas-only / single-collection sessions sometimes hold their
        # data-collection content directly on the session rather than on a
        # Sample; ``report_datasets`` then gives one synthetic session-level
        # Sample so the per-sample section logic keeps working.
        collection_samples = report_datasets(session)

        # The recorded warnings plus the ones the dashboard derives, so the
        # cover's totals and the warning list match the dashboard's Findings.
        warnings = session_warnings(session)
        warnings += [
            warning.text for warning in derived_scope_warnings(scope_from(session, workspace), analysis)
            if warning.text not in warnings
        ]
        warning_rows = summarise_warnings(warnings)

        return cls(
            session=session,
            dashboard=dashboard,
            validations=validations,
            grid_validations=workspace.tilt_validations,
            collection_samples=collection_samples,
            warnings=warnings,
            warning_rows=warning_rows,
        )


def successful_tilt_ids(validations: Mapping[str, TiltSeriesValidation]) -> frozenset[str]:
    """The tilt series the validator calls complete: what a search map's
    "acquired" counts, in the completion table and the figure captions."""

    return frozenset(tilt_id for tilt_id, v in validations.items() if v.status == ValidationStatus.COMPLETE)


def resolve_report_atlases(
    contexts: Sequence[SessionContext], sources: Sequence[Session],
) -> None:
    """Resolve display-only atlas context without expanding exported content.

    ``presentation.report_datasets.dataset_atlases`` decides, so the dialog
    preview names the same atlases as the PDF.
    """
    resolved = dataset_atlases([(ctx.session, ctx.collection_samples) for ctx in contexts], sources)
    for ctx in contexts:
        for sample in ctx.collection_samples:
            atlas = resolved[id(sample)]
            if atlas.note is not None:
                ctx.atlas_notes[id(sample)] = atlas.note
            if atlas.source is not None:
                ctx.atlas_sources[id(sample)] = atlas.source
            if atlas.atlas is not None:
                ctx.sample_atlases[id(sample)] = atlas.atlas


def atlas_count_scope(ctx: SessionContext, represented: frozenset[int]) -> Session:
    """Transient copies give the existing counter only the resolved atlases."""
    samples = ctx.session.samples or ctx.collection_samples
    return replace(ctx.session, samples=[
        replace(sample, atlas=(
            sample.atlas if id(ctx.sample_atlases.get(id(sample))) in represented
            else ctx.sample_atlases.get(id(sample), sample.atlas)
        ))
        for sample in samples
    ])


@dataclass(frozen=True, slots=True)
class ReportSection:
    """One section after the cover, in report order (for the contents index)."""

    key: str
    kind: str  # "atlas" | "dataset"
    ctx: SessionContext
    sample: Sample | None
    number: int


def report_sections(contexts: Sequence[SessionContext]) -> list[ReportSection]:
    sections: list[ReportSection] = []
    atlas_contexts = [ctx for ctx in contexts if ctx.session.kind == SessionKind.ATLAS_SCREENING]
    for number, ctx in enumerate(atlas_contexts, 1):
        sections.append(ReportSection(f"atlas-session-{number}", "atlas", ctx, None, number))
    number = 0
    for ctx in contexts:
        for sample in ctx.collection_samples:
            number += 1
            sections.append(ReportSection(f"dataset-{number}", "dataset", ctx, sample, number))
    return sections


def fallback_session_atlas(ctx: SessionContext, sample: Sample) -> Atlas | None:
    """Use the session-level atlas if the sample doesn't have its own.

    Multi-grid sessions sometimes hold a single shared atlas at the
    session level rather than on each sample. The Atlas section in the
    sample block falls back to that so users still get a visual anchor.
    """

    if sample.atlas is not None:
        return sample.atlas
    if ctx.session.atlas is not None:
        return ctx.session.atlas
    return None


def lookup_validations(
    tilt_series: Sequence[TiltSeries],
    validations: Mapping[str, TiltSeriesValidation],
) -> dict[str, TiltSeriesValidation]:
    """``validations`` (the dashboard's verdicts) narrowed to ``tilt_series``."""

    return {
        validation_key(validations, tilt): validation
        for tilt in tilt_series
        if (validation := validation_for(validations, tilt)) is not None
    }

