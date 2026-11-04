"""A separate prepared volume payload; volumes never become PreviewSources."""
from __future__ import annotations

from dataclasses import dataclass, replace
from collections import Counter

from tomography_session_browser.domain.recorded_metrics import RecordedMetric
from tomography_session_browser.domain.tomograms import Tomogram, ProcessingSource
from tomography_session_browser.presentation.tomogram_selection import AllTomograms, FolderTomograms, selection_note, versions_in
from tomography_session_browser.parsers.path_utils import natural_key
from tomography_session_browser.presentation.workspace import Scope, Workspace
from tomography_session_browser.services.tomogram_links import TomogramLink
from tomography_session_browser.domain.identity import entity_key_text
from tomography_session_browser.presentation.workspace import EntityKind
from tomography_session_browser.presentation.project_tree import ProjectTreeGroup

MAX_EMPTY_LINK_EXPLANATIONS = 24


@dataclass(frozen=True, slots=True)
class TomogramRow:
    tomogram: Tomogram
    group: str
    link: TomogramLink


@dataclass(frozen=True, slots=True)
class ProcessingScopeChoice:
    key: str
    label: str
    count: int


@dataclass(frozen=True, slots=True)
class PreparedTomogramTab:
    rows: tuple[TomogramRow, ...] = ()
    has_sources: bool = False
    processing_scopes: tuple[ProcessingScopeChoice, ...] = ()
    link_explanations: tuple[str, ...] = ()
    selection: AllTomograms | FolderTomograms | None = None
    sources: tuple[ProcessingSource, ...] = ()
    notice: str = ''
    processing_only: bool = False


#: List sorts. A value sort uses one recorded definition and its usual unit, so
#: only comparable values are ranked; other units follow, missing values last.
SORT_METRICS: dict[str, tuple[str, str]] = {
    "Residual": ("imod.tiltalign.mean", "nm"),
    "Thickness": ("warp.ctf_specimen_thickness", "Å"),
}
SORTS = ("Name", *SORT_METRICS)


def restored_sort(value: object) -> tuple[str, str]:
    """Only known sort identities may restore a ranking; legacy records use Name."""
    if isinstance(value, str) and value in SORTS:
        return value, ""
    return SORTS[0], "Saved ordering is unsupported; using Name."


def sort_metric(row: TomogramRow, sort: str) -> RecordedMetric | None:
    definition = SORT_METRICS.get(sort, ("", ""))[0]
    return next((metric for metric in row.tomogram.processing.metrics if metric.definition == definition), None)


def sorted_within_groups(rows: list[TomogramRow], sort: str) -> list[TomogramRow]:
    """Groups keep their order; Name keeps the prepared natural order inside each."""
    if sort not in SORT_METRICS:
        return list(rows)
    groups = {group: index for index, group in enumerate(dict.fromkeys(row.group for row in rows))}
    unit = SORT_METRICS[sort][1]

    def key(row: TomogramRow) -> tuple:
        metric = sort_metric(row, sort)
        if metric is None:
            return groups[row.group], 2, "", 0.
        return groups[row.group], int(metric.unit != unit), metric.unit, metric.value
    return sorted(rows, key=key)


def needs_attention(row: TomogramRow) -> bool:
    """One predicate for both the Summary route and the review segment."""
    return row.link.status == "ambiguous" or bool(row.link.warnings) or bool(row.tomogram.warnings) or any(
        volume.warnings for volume in row.tomogram.volumes)


def prepare_tomogram_tab(workspace: Workspace, scope: Scope) -> PreparedTomogramTab:
    items = workspace.tomograms(scope)
    selection = scope if isinstance(scope, AllTomograms | FolderTomograms) else None
    notice = selection_note(workspace, selection) if selection else ''
    if selection and selection.version and selection.version not in versions_in(workspace, selection):
        selection = replace(selection, version=None)
    source_ids = {item.source_id for item in items}
    if isinstance(scope, FolderTomograms):
        source_ids.add(scope.source_id)
    if isinstance(scope, ProjectTreeGroup) and scope.processing_source:
        source_ids.add(scope.processing_source.id)
    sources = [source for source in workspace.processing_sources if source.id in source_ids]
    name_counts = Counter(source.name for source in sources)
    source_names = {source.id: source.name if name_counts[source.name] == 1
                    else str(source.path) for source in sources}
    targets = {entity_key_text(value): value for value in workspace.entities(EntityKind.TILT_SERIES)}
    rows = []
    for item in items:
        link = workspace.tomogram_links[item.id]
        target = targets.get(link.target_key or "")
        owner = workspace.row_owner(target) if target is not None else None
        group = owner[1] if owner is not None else source_names[item.source_id]
        if owner is not None and len(source_ids) > 1:
            group += " · " + source_names[item.source_id]
        rows.append(TomogramRow(item, group, link))
    rows.sort(key=lambda row: (row.link.status != "resolved", natural_key(row.group), natural_key(row.tomogram.name)))
    choices = ()
    explanations = ()
    if not rows and any(source.tomograms for source in workspace.processing_sources) and not (
            isinstance(scope, ProjectTreeGroup) and scope.processing_source is not None):
        choices = tuple(ProcessingScopeChoice(group.key, group.display_name, len(workspace.tomograms(group)))
                        for group in workspace.project_groups if group.processing_source is not None)
        all_items = tuple(item for source in workspace.processing_sources for item in source.tomograms)
        explanations = tuple(f'{item.name}: {workspace.tomogram_links[item.id].explanation}' + (
            ' Its parent is outside this scope.' if workspace.tomogram_links[item.id].status == 'resolved' else '')
            for item in all_items[:MAX_EMPTY_LINK_EXPLANATIONS])
        if len(all_items) > MAX_EMPTY_LINK_EXPLANATIONS:
            explanations += (f'{len(all_items) - MAX_EMPTY_LINK_EXPLANATIONS} more tomograms. Open a processing folder to inspect their links.',)
    processing_only = isinstance(scope, ProjectTreeGroup) and scope.processing_source is not None
    return PreparedTomogramTab(tuple(rows), bool(workspace.processing_sources), choices, explanations,
                               selection, tuple(sources), notice, processing_only)
