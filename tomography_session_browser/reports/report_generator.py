"""Compose a report from shared context and section builders.

Legacy helper aliases remain static; implementations belong to their named
report modules. Patch globals in the defining module, not this import surface.
"""
from __future__ import annotations
from collections.abc import Callable
from datetime import datetime
from functools import partial
from typing import Literal
from pathlib import Path
from typing import Sequence
from reportlab.platypus import NextPageTemplate, PageBreak
from tomography_session_browser.presentation.workspace import Workspace
from tomography_session_browser.domain.models import Session
from tomography_session_browser.presentation.report_scope import ProjectReportGroup
from tomography_session_browser.reports import styles
from tomography_session_browser.reports.pdf_export import ReportResult, write_pdf
from tomography_session_browser.reports.folder_report import build_folder_report
from tomography_session_browser.reports.context import report_sections, resolve_report_atlases, SessionContext
from tomography_session_browser.reports.formatting import footer_label, report_title
from tomography_session_browser.reports.sections.atlas_overview import linked_overview_section
from tomography_session_browser.reports.sections.cover import cover_page
from tomography_session_browser.reports.sections.dataset import data_collection_section
from tomography_session_browser.reports.sections.tomograms import tomogram_section
from tomography_session_browser.presentation.report_tomograms import ScopedTomograms, member_slice_plans
from tomography_session_browser.reports.sections.slices import numbered_tiles
from tomography_session_browser.reports.tomogram_slices import (
    CANCELLED, ReportSlices, render_slices, reserved_bytes, slice_keywords,
)
from tomography_session_browser.services.volume.memory import VOLUME_MEMORY
from tomography_session_browser.presentation.report_scope import ReportScope
from tomography_session_browser.services.output_boundary import check_output_path


SLICES, WRITING = "slices", "writing"
#: progress(stage, done, total): counted slices, then the PDF is being written.
ReportProgress = Callable[[Literal["slices", "writing"], int, int], None]


def build_session_report(
    sessions: Sequence[Session],
    output_path: Path,
    *,
    include_thumbnails: bool = True,
    include_exposure_thumbnails: bool = False,
    project_title: str | None = None,
    project_groups: Sequence[ProjectReportGroup] | None = None,
    atlas_context_sessions: Sequence[Session] = (),
    protected_roots: tuple[Path, ...] = (),
    tomograms: ScopedTomograms | None = None,
    slices: ReportSlices | None = None,
    generation_warnings: list[str] | None = None,
) -> ReportResult:
    """Render ``sessions`` to a PDF at ``output_path``.

    ``include_thumbnails=False`` keeps the report text-only (no atlas /
    overview / search-map images, and no exposure thumbnails). The graphical
    summary widgets — count cards, the outcome ring, status bands — render
    either way; they don't depend on raster content.

    ``include_exposure_thumbnails`` adds, after each dataset's tilt-series
    table, an image of every tilt series' exposure area from Tomography 5's
    ``Thumbnails`` stacks (nearest 0°, or a lamella's pretilt), four to a
    page, with that frame's metadata.

    ``tomograms`` is the accepted, reduced snapshot of a combined report; it
    adds a Tomograms block to each dataset that has one and a cover card.
    ``slices`` are the rendered central slices of its members; their records
    go into the PDF keywords. ``generation_warnings`` starts with the warnings
    made before composition (slice skips).
    """

    if not sessions:
        raise ValueError("build_session_report requires at least one session")

    roots = (*protected_roots, *(session.path.resolve() for session in (*sessions, *atlas_context_sessions)))
    check_output_path(output_path, roots)

    styles.ensure_fonts()
    sessions = list(sessions)
    generation_warnings = list(generation_warnings or ())
    generated_at = datetime.now()

    # Pre-compute per-session validation + warning summaries so we can
    # share them between the cover (totals) and the per-sample sections
    # (per-sample detail). Keeps validation deterministic and avoids
    # double work on large sessions.
    workspace = Workspace.build(sessions)
    per_session: list[SessionContext] = [
        SessionContext.build(session, workspace=workspace) for session in workspace.sessions
    ]
    resolve_report_atlases(per_session, atlas_context_sessions)
    sections = report_sections(per_session)

    flowables: list = []
    flowables.append(NextPageTemplate("portrait"))

    title = project_title or report_title(sessions)
    footer = footer_label(sessions, project_title=project_title, generated_at=generated_at)

    flowables.extend(
        cover_page(
            per_session,
            project_title=project_title,
            project_groups=project_groups or (),
            generated_at=generated_at,
            sections=sections,
            tomograms=tomograms,
            slices=slices,
        )
    )

    # Atlas-session overview, only for screening sessions in the export.
    linked_block = linked_overview_section(
        per_session,
        include_thumbnails,
        generation_warnings,
        anchors={id(section.ctx): section.key for section in sections if section.kind == "atlas"},
    )
    if linked_block:
        flowables.append(PageBreak())
        flowables.extend(linked_block)

    # Per-data-collection sections.
    datasets = [section for section in sections if section.kind == "dataset"]
    by_anchor = {section.anchor: section for section in tomograms.sections} if tomograms else {}
    for section in datasets:
        flowables.append(PageBreak())
        flowables.extend(
            data_collection_section(
                section.ctx,
                section.sample,
                include_thumbnails=include_thumbnails,
                include_exposure_thumbnails=include_exposure_thumbnails,
                generation_warnings=generation_warnings,
                position=(section.number, len(datasets)),
                anchor=section.key,
                tomograms=(tomogram_section(by_anchor[key], tomograms, slices)
                           if tomograms is not None and (key := f"{section.key}-tomograms") in by_anchor else None),
            )
        )

    page_count = write_pdf(
        output_path,
        flowables,
        title=title,
        footer_label=footer,
        protected_roots=roots,
        keywords=slice_keywords(numbered_tiles(tomograms, slices)) if tomograms and slices else None,
    )
    return ReportResult(path=output_path, page_count=page_count, warnings=generation_warnings)


def build_report(
    scope: ReportScope,
    output_path: Path,
    protected_roots: tuple[Path, ...] = (),
    *,
    cancelled: Callable[[], bool] = lambda: False,
    progress: ReportProgress = lambda stage, done, total: None,
) -> ReportResult:
    """The one reports entry point: the accepted scope, its composer, one writer.

    With the tomogram option off the session composer receives exactly the
    arguments it always has. With central slices, the images render first,
    under one reservation held until the PDF is written; ``cancelled`` is
    checked between images and before composition, which cannot stop.
    """

    if scope.sessions:
        extra = {"tomograms": scope.tomograms} if scope.prints_tomograms else {}
        compose = partial(
            build_session_report, list(scope.sessions), output_path, project_title=scope.project_title,
            project_groups=list(scope.project_groups), atlas_context_sessions=scope.atlas_context_sessions,
            include_exposure_thumbnails=scope.include_exposure_thumbnails, protected_roots=protected_roots,
            **extra)
    else:                              # the only place that picks the composer (D4 §5.1)
        assert scope.tomograms is not None
        compose = partial(build_folder_report, scope.tomograms, output_path, title=scope.project_title,
                          protected_roots=protected_roots)
    if not scope.prints_slices:
        return compose()
    assert scope.tomograms is not None
    plans = member_slice_plans(scope.tomograms)
    warnings: list[str] = []
    with VOLUME_MEMORY.reserve(reserved_bytes(plans), "report-slices"):
        slices = render_slices(plans, warnings, cancelled=cancelled,
                               progress=lambda done, total: progress(SLICES, done, total))
        if cancelled():
            raise InterruptedError(CANCELLED)
        progress(WRITING, 0, 0)
        return compose(slices=slices, generation_warnings=warnings)
