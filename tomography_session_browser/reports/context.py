"""Context report components."""
from __future__ import annotations
from dataclasses import dataclass, field, replace
from typing import Mapping, Sequence
from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.presentation.workspace import Workspace
from tomography_session_browser.domain.models import Atlas, Sample, Session, TiltSeries
from tomography_session_browser.services.warning_classification import WarningSummary, summarise_warnings
from tomography_session_browser.services.tilt_series_validation import STATUS_COMPLETE, TiltSeriesValidation, validate_session_tilt_series
from tomography_session_browser.presentation.dashboard.dashboard import analysed_dashboard_model
from tomography_session_browser.presentation.dashboard.models import DashboardModel
from tomography_session_browser.presentation.warnings import session_warnings
from tomography_session_browser.services.session_linking import atlas_link_resolutions


@dataclass(slots=True)
class SessionContext:
    session: Session
    dashboard: DashboardModel
    # Validation results keyed by tilt series id.
    validations: dict[str, TiltSeriesValidation]
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
        validations = analysis.validation_by_id

        collection_samples = [
            sample
            for sample in session.samples
            if (
                sample.search_maps
                or sample.search_tiles
                or sample.batch_positions
                or sample.tilt_series
                or sample.overviews
            )
        ]
        # Atlas-only / single-collection sessions sometimes hold their
        # data-collection content directly on the session rather than on
        # a Sample. Synthesise a single "session-level" Sample in that
        # case so the per-sample section logic keeps working.
        if not collection_samples and (
            session.overviews or session.search_maps or session.search_tiles or session.batch_positions or session.tilt_series
        ):
            collection_samples.append(
                Sample(
                    id=session.id,
                    name=session.name,
                    path=session.path,
                    atlas=session.atlas,
                    overviews=session.overviews,
                    search_maps=session.search_maps,
                    search_tiles=session.search_tiles,
                    batch_positions=session.batch_positions,
                    tilt_series=session.tilt_series,
                    metadata=session.metadata_summary,
                    warnings=list(session.warnings),
                )
            )

        warnings = session_warnings(session)
        warning_rows = summarise_warnings(warnings)

        return cls(
            session=session,
            dashboard=dashboard,
            validations=validations,
            collection_samples=collection_samples,
            warnings=warnings,
            warning_rows=warning_rows,
        )


def successful_tilt_ids(validations: Mapping[str, TiltSeriesValidation]) -> frozenset[str]:
    """The tilt series the validator calls complete: what a search map's
    "acquired" counts, in the completion table and the figure captions."""

    return frozenset(tilt_id for tilt_id, v in validations.items() if v.status == STATUS_COMPLETE)


def resolve_report_atlases(
    contexts: Sequence[SessionContext], sources: Sequence[Session],
) -> None:
    """Resolve display-only atlas context without expanding exported content.

    Include every loaded candidate so ambiguity cannot disappear when the
    reviewer selects a subset. Synthetic session-level samples follow the same
    resolver as ordinary collection samples.
    """
    lookup_sessions = list(sources) + [
        replace(ctx.session, samples=ctx.session.samples or ctx.collection_samples)
        for ctx in contexts
    ]
    session_names = {
        id(sample): session.name
        for session in lookup_sessions
        for sample in session.samples
    }
    resolutions = atlas_link_resolutions(lookup_sessions)
    for ctx in contexts:
        for sample in ctx.collection_samples:
            resolution = resolutions.get(id(sample))
            atlas = sample.atlas
            if atlas is None and resolution is not None:
                if resolution.linked:
                    atlas = resolution.sample.atlas
                    source_session = session_names.get(id(resolution.sample))
                    ctx.atlas_sources[id(sample)] = " / ".join(
                        name for name in (source_session, resolution.sample.name) if name
                    )
                else:
                    ctx.atlas_notes[id(sample)] = (
                        "Atlas link is ambiguous; no atlas has been selected."
                        if resolution.ambiguous else
                        "Linked atlas is unavailable. Load the matching atlas session to include it."
                    )
            elif atlas is None:
                atlas = fallback_session_atlas(ctx, sample)
            if atlas is not None:
                ctx.sample_atlases[id(sample)] = atlas


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
    validations: Mapping[str, TiltSeriesValidation] | None = None,
) -> dict[str, TiltSeriesValidation]:
    if validations is None:
        return {
            validation.tilt_series_id: validation
            for validation in validate_session_tilt_series(tilt_series)
        }
    return {
        tilt.id: validation
        for tilt in tilt_series
        if (validation := validations.get(tilt.id)) is not None
    }

