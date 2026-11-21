"""The report dialog's "In this report" pane (plan B6, Q7 B): what the PDF prints.

``report_preview`` reads the normalised ``ReportScope``, never the ticks: one
block for each full atlas overview and each dataset, in report order (atlas
overviews first, then the datasets, numbered as the PDF numbers them), with
the counts the PDF prints. Totals show only for two or more datasets or
folders. The dataset atlas comes from ``dataset_atlases``, the PDF's own
resolver. Exposure thumbnails have no count: a count needs a file read, and
the preview runs on each tick without one. Qt-free and filesystem-free.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.models import Sample, Session
from tomography_session_browser.presentation.report_datasets import DatasetAtlas, dataset_atlases, report_datasets
from tomography_session_browser.presentation.report_scope import FOLDER_REPORT_LINE, ReportScope
from tomography_session_browser.presentation.report_tomograms import (
    NO_DATASET_LINK, ReportRow, ScopedTomograms, TomogramSection, and_list, member_slice_plans, section_sources,
)
from tomography_session_browser.presentation.tomogram_slices import SKIP_NO_VERSION, SKIP_UNREADABLE, SliceRequest, SliceSkip

ItemKind = Literal["overview", "atlas", "tilt_series", "thumbnails", "tomograms", "not_counted", "not_linked",
                   "incomplete", "slices"]

NOTHING_TICKED = "Nothing is ticked."
ORDER_NOTE = "Listed in report order."
TOTAL = "Total"
FULL_OVERVIEW = "Full atlas overview"
ATLAS_IMAGE = "Atlas grid image"
#: Said whenever tomogram rows print (GR-02): what of the reviewer's own work enters the PDF.
REVIEW_STATE = "Review tags, counts and notes print as they were when this dialog opened."
#: Totals add these counts, in this order; the other items have no count or are not summed.
TOTAL_LABELS: dict[str, str] = {"tilt_series": "Tilt series", "tomograms": "Tomograms", "slices": "Central slices"}


@dataclass(frozen=True, slots=True)
class PreviewItem:
    kind: ItemKind
    label: str
    count: int | None = None

    @property
    def text(self) -> str:
        return self.label if self.count is None else f"{self.label}: {self.count:,}"


@dataclass(frozen=True, slots=True)
class PreviewBlock:
    """One atlas overview, dataset or folder, as the PDF prints it."""
    title: str
    subtitle: str = ""
    items: tuple[PreviewItem, ...] = ()
    #: Why the dataset has no atlas image, when a recorded link gives none.
    note: str = ""


@dataclass(frozen=True, slots=True)
class ReportPreview:
    #: A line above the blocks: the folder-report switch (Q8), "Nothing is ticked." or a refusal.
    lead: str = ""
    #: The count beside "In this report": "2 datasets · 1 atlas".
    badge: str = ""
    blocks: tuple[PreviewBlock, ...] = ()
    totals: tuple[PreviewItem, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        """The pane as plain lines: its accessible description, and what the tests read."""
        lines = [line for line in (self.lead, self.badge) if line]
        for block in self.blocks:
            lines += [block.title, *([block.subtitle] if block.subtitle else []),
                      *(f"- {item.text}" for item in block.items), *([block.note] if block.note else [])]
        if self.totals:
            lines += [TOTAL, *(f"- {item.text}" for item in self.totals)]
        lines += self.notes
        if self.blocks:
            lines.append(ORDER_NOTE)
        return "\n".join(lines)


def report_preview(scope: ReportScope | None, refusal: str = "") -> ReportPreview:
    """The pane for ``scope``; with no scope, ``refusal`` or "Nothing is ticked."."""
    if scope is None or not (scope.sessions or scope.folder_report):
        return ReportPreview(lead=refusal or NOTHING_TICKED)
    if scope.folder_report:
        return _folder_preview(scope)
    return _session_preview(scope)


def describe_report_scope(scope: ReportScope | None, refusal: str = "") -> str:
    """The preview as text, from the same function that decides the report."""
    return report_preview(scope, refusal).text


# ------------------------------------------------------------------ session report

def _session_preview(scope: ReportScope) -> ReportPreview:
    owners = [(session, report_datasets(session)) for session in scope.sessions]
    atlases = dataset_atlases(owners, scope.atlas_context_sessions)
    scoped = scope.tomograms if scope.prints_tomograms else None
    sections = {section.anchor: section for section in scoped.sections} if scoped is not None else {}
    plans = member_slice_plans(scoped) if scoped is not None and scope.prints_slices else {}
    overviews = [session for session in scope.sessions if session.kind == SessionKind.ATLAS_SCREENING]
    blocks = [PreviewBlock(_name(session), items=(PreviewItem("overview", FULL_OVERVIEW),)) for session in overviews]
    datasets: list[PreviewBlock] = []
    for session, samples in owners:
        for sample in samples:
            section = sections.get(f"dataset-{len(datasets) + 1}-tomograms")
            datasets.append(_dataset_block(session, sample, atlases[id(sample)], scope, section, scoped, plans))
    badge = " · ".join(part for part in (
        count_phrase(len(datasets), "dataset") if datasets else "",
        count_phrase(len(overviews), "atlas", "atlases") if overviews else "") if part)
    notes = _tomogram_notes(scoped, plans) if scoped is not None else ()
    return ReportPreview(badge=badge, blocks=(*blocks, *datasets),
                         totals=_totals(datasets) if len(datasets) > 1 else (), notes=notes)


def _dataset_block(session: Session, sample: Sample, atlas: DatasetAtlas, scope: ReportScope,
                   section: TomogramSection | None, scoped: ScopedTomograms | None,
                   plans: dict[str, SliceRequest | SliceSkip]) -> PreviewBlock:
    title = sample.name or sample.path.name
    # A session-level dataset has the session's name: say it once.
    subtitle = [] if title == _name(session) else [_name(session)]
    if atlas.linked and atlas.source_session:
        subtitle.append(f"atlas {atlas.source_session}")
    items: list[PreviewItem] = []
    if atlas.atlas is not None and atlas.atlas.image_path is not None:
        items.append(PreviewItem("atlas", ATLAS_IMAGE))
    if sample.tilt_series:
        items.append(PreviewItem("tilt_series", "Tilt series", len(sample.tilt_series)))
        if scope.include_exposure_thumbnails:
            items.append(PreviewItem("thumbnails", "Exposure thumbnails"))
    if section is not None and scoped is not None:
        items += _section_items(section, scoped, plans)
    return PreviewBlock(title, " · ".join(subtitle), tuple(items), atlas.note or "")


def _section_items(section: TomogramSection, scoped: ScopedTomograms,
                   plans: dict[str, SliceRequest | SliceSkip]) -> list[PreviewItem]:
    items = []
    if section.members:
        folders = and_list([source.label for source in section_sources(section, scoped)])
        items.append(PreviewItem("tomograms", f"Tomograms, from {folders}" if folders else "Tomograms",
                                 len(section.members)))
    notes = len({row.key for row in section.notes})
    if notes:
        items.append(PreviewItem("not_counted", NO_DATASET_LINK, notes))
    images = _images(section.members, plans)
    if plans and images:
        items.append(PreviewItem("slices", "Central slices", images))
    return items


# ------------------------------------------------------------------ folder report

def _folder_preview(scope: ReportScope) -> ReportPreview:
    scoped = scope.tomograms
    assert scoped is not None
    plans = member_slice_plans(scoped) if scope.prints_slices else {}
    rows = {section.source_ids[0]: section.members for section in scoped.sections if section.source_ids}
    blocks = []
    for source in scoped.sources:
        members = rows.get(source.source.id, ())
        linked = sum(row.link.status == "resolved" for row in members)
        incomplete = sum(row.state == "incomplete" for row in members)
        counts = [PreviewItem("tomograms", "Tomograms", len(members)),
                  PreviewItem("not_linked", "Not linked", len(members) - linked - incomplete),
                  PreviewItem("incomplete", "Link data not complete", incomplete)]
        if plans:
            counts.append(PreviewItem("slices", "Central slices", _images(members, plans)))
        # Zero is the normal state for the link problems; a folder with no tomograms says 0.
        # The pipeline is left out when the folder's label already is that word ("Warp").
        pipeline = source.source.display_pipeline if source.source.display_pipeline != source.label else ""
        blocks.append(PreviewBlock(source.label, pipeline,
                                   tuple(item for item in counts if item.count or item.kind == "tomograms")))
    return ReportPreview(lead=FOLDER_REPORT_LINE, badge=count_phrase(len(blocks), "folder"), blocks=tuple(blocks),
                         totals=_totals(blocks) if len(blocks) > 1 else (), notes=_tomogram_notes(scoped, plans))


# ------------------------------------------------------------------ shared

def _totals(blocks: Sequence[PreviewBlock]) -> tuple[PreviewItem, ...]:
    sums: Counter[str] = Counter()
    present: set[str] = set()
    for block in blocks:
        for item in block.items:
            if item.kind in TOTAL_LABELS and item.count is not None:
                sums[item.kind] += item.count
                present.add(item.kind)
    return tuple(PreviewItem(kind, label, sums[kind]) for kind, label in TOTAL_LABELS.items()  # type: ignore[arg-type]
                 if kind in present)


def _tomogram_notes(scoped: ScopedTomograms, plans: dict[str, SliceRequest | SliceSkip]) -> tuple[str, ...]:
    """What the blocks cannot show: rows outside every dataset, slice skips, unsaved tags."""
    notes = []
    if not scoped.folder_report:
        outside = len({row.key for note in scoped.session_notes for row in note.rows} | {row.key for row in scoped.incomplete})
        if outside:
            notes.append(f"Not in a dataset section: {count_phrase(outside, 'tomogram')}. The cover gives the cause.")
    skipped = Counter(plan.reason for plan in plans.values() if isinstance(plan, SliceSkip))
    if skipped.get(SKIP_UNREADABLE):
        notes.append(f"The app cannot read the volume header of {count_phrase(skipped[SKIP_UNREADABLE], 'tomogram')}.")
    if skipped.get(SKIP_NO_VERSION):
        count = skipped[SKIP_NO_VERSION]
        notes.append(f"{count_phrase(count, 'tomogram')} {'has' if count == 1 else 'have'} no volume to show.")
    printed = {row.tomogram.source_id for row in scoped.members}
    if printed:
        notes.append(REVIEW_STATE)
    unsaved = [source.label for source in scoped.sources if source.source.id in printed and source.tags.unsaved]
    if unsaved:
        verb = "has" if len(unsaved) == 1 else "have"
        notes.append(f"{and_list(unsaved)} {verb} tag changes that you did not save. The report includes them.")
    return tuple(notes)


def _images(rows: Iterable[ReportRow], plans: dict[str, SliceRequest | SliceSkip]) -> int:
    return sum(isinstance(plans.get(row.key), SliceRequest) for row in rows)


def _name(session: Session) -> str:
    return session.name or session.path.name
