"""Compose a report from shared context and section builders.

Legacy helper aliases remain static; implementations belong to their named
report modules. Patch globals in the defining module, not this import surface.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence
from reportlab.platypus import NextPageTemplate, PageBreak
from tomography_session_browser.presentation.workspace import Workspace
from tomography_session_browser.domain.models import Session
from tomography_session_browser.presentation.report_scope import ProjectReportGroup
from tomography_session_browser.reports import styles
from tomography_session_browser.reports.pdf_export import write_pdf
from tomography_session_browser.reports.context import report_sections, resolve_report_atlases, SessionContext
from tomography_session_browser.reports.formatting import footer_label, report_title
from tomography_session_browser.reports.sections.atlas_overview import linked_overview_section
from tomography_session_browser.reports.sections.cover import cover_page
from tomography_session_browser.reports.sections.dataset import data_collection_section


@dataclass(slots=True)
class ReportResult:
    path: Path
    page_count: int
    warnings: list[str]


def build_session_report(
    sessions: Sequence[Session],
    output_path: Path,
    *,
    include_thumbnails: bool = True,
    include_exposure_thumbnails: bool = False,
    project_title: str | None = None,
    project_groups: Sequence[ProjectReportGroup] | None = None,
    atlas_context_sessions: Sequence[Session] = (),
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
    """

    if not sessions:
        raise ValueError("build_session_report requires at least one session")

    styles.ensure_fonts()
    sessions = list(sessions)
    generation_warnings: list[str] = []
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
            )
        )

    page_count = write_pdf(
        output_path,
        flowables,
        title=title,
        footer_label=footer,
    )
    return ReportResult(path=output_path, page_count=page_count, warnings=generation_warnings)
