"""Convert review selections into dashboard scopes without changing source data."""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any
from tomography_session_browser.domain.identity import canonical_entity_key, dedupe_entities
from tomography_session_browser.domain.models import (
    Atlas,
    BatchPosition,
    Overview,
    Sample,
    SearchMap,
    Session,
    TiltSeries,
)
from tomography_session_browser.domain.traversal import has_collection_data, is_single_atlas_placeholder
from tomography_session_browser.presentation.dashboard.atlas_counts import compute_atlas_counts
from tomography_session_browser.presentation.dashboard.models import DashboardEntityScope, DashboardScope, LinkedAtlasDashboardScope
from tomography_session_browser.presentation.scopes import LinkedSampleGroup
from tomography_session_browser.presentation.warnings import (
    entity_scope_warnings,
    linked_sample_group_warnings,
    prefix_warnings,
    session_warnings,
)
from tomography_session_browser.presentation.workspace import (
    EntityKind,
    Workspace,
    session_entities,
    summary_atlas_pairs,
)
from tomography_session_browser.services.batch_inference import batch_inference_key
from tomography_session_browser.services.item_status import validate_tilt_series_by_grid
from tomography_session_browser.services.tilt_series_validation import TiltSeriesValidation


def session_counts(session: Session) -> dict[str, int]:
    """Compact, label → count map shown in the legacy summary tree.

    The atlas-related entries come from :func:`compute_atlas_counts` so
    they always agree with the dashboard. Zero values for the
    legacy-collection items are dropped to keep the panel compact —
    atlas keys keep their zero values when an atlas-screening session is
    the only thing loaded, since "0 data collection samples" is
    informative in that case.
    """

    counts: dict[str, int] = {
        "Samples": len(session.samples),
    }

    atlas = compute_atlas_counts(session)
    # Order: Atlas sessions → Samples with atlas → Data collection
    # samples. "Sample atlases" is intentionally omitted — for the
    # common case it duplicates "Samples with atlas".
    if atlas.atlas_session_count > 0:
        counts["Atlas sessions"] = atlas.atlas_session_count
    counts["Samples with atlas"] = atlas.samples_with_atlas_count
    counts["Data collection samples"] = atlas.data_collection_sample_count

    counts["Overviews"] = len(session_entities(session, EntityKind.OVERVIEW))
    counts["Search maps"] = len(session_entities(session, EntityKind.SEARCH_MAP))
    counts["Search tiles"] = len(session_entities(session, EntityKind.SEARCH_TILE))
    counts["Batch positions"] = len(session_entities(session, EntityKind.BATCH_POSITION))
    counts["Tilt series"] = len(session_entities(session, EntityKind.TILT_SERIES))

    if counts["Overviews"] == 0:
        counts.pop("Overviews")
    return counts


def sample_group_key(label: str, samples: list[Sample]) -> str:
    representative = _representative_sample(samples) if samples else None
    return representative.id if representative is not None else batch_inference_key(label)


def _sample_groups_from_samples(samples: list[Sample]) -> list[tuple[str, list[Sample]]]:
    return [(clean_sample_label(sample.name), [sample]) for sample in samples]


def _linked_sample_groups_for_dashboard(sessions: list[Session], workspace: Workspace) -> list[tuple[str, list[Sample]]]:
    groups: list[tuple[str, list[Sample]]] = []
    for samples in workspace.linked_groups_for(sessions):
        members = list(samples)
        if not _sample_group_is_active(members):
            continue
        groups.append((_sample_group_label(members), members))
    return groups


def _sample_group_is_active(samples: list[Sample]) -> bool:
    if any(has_collection_data(sample) for sample in samples):
        return True
    return any(sample.atlas is not None and not is_single_atlas_placeholder(sample) for sample in samples)


def _sample_group_label(samples: list[Sample], fallback: str | None = None) -> str:
    atlas_sample = next(
        (
            sample
            for sample in samples
            if sample.atlas is not None and not is_single_atlas_placeholder(sample)
        ),
        None,
    )
    if atlas_sample is not None:
        return _full_sample_label(atlas_sample.name)

    collection = next(
        (
            sample
            for sample in samples
            if has_collection_data(sample) and not _is_generic_sample_label(sample.name)
        ),
        None,
    )
    if collection is None:
        collection = next((sample for sample in samples if has_collection_data(sample)), None)
    chosen = collection or (samples[0] if samples else None)
    return _full_sample_label(chosen.name if chosen is not None else (fallback or "Sample"))


def _representative_sample(samples: list[Sample]) -> Sample:
    collection = next((sample for sample in samples if has_collection_data(sample)), None)
    if collection is not None:
        return collection
    atlas_sample = next(
        (
            sample
            for sample in samples
            if sample.atlas is not None and not is_single_atlas_placeholder(sample)
        ),
        None,
    )
    return atlas_sample or samples[0]


def clean_sample_label(label: str) -> str:
    text = re.sub(r"^\s*\d+\.\s*", "", label or "").strip()
    if not text:
        return "Sample"
    if _is_generic_sample_label(text):
        return text
    text = text.replace("_", " ").strip()
    return " ".join(part.capitalize() if part.islower() else part for part in text.split())


def _full_sample_label(label: str) -> str:
    text = re.sub(r"^\s*\d+\.\s*", "", label or "").strip()
    return text or "Sample"


def _is_generic_sample_label(label: str) -> bool:
    return bool(re.fullmatch(r"\s*(?:\d+\.\s*)?sample\d+\s*", label or "", flags=re.IGNORECASE))


def scope_samples(scope: DashboardScope) -> list[Sample]:
    """Every sample a scope draws on: whose grid each entity sits on."""

    return [*scope.samples, *(sample for _label, samples in scope.sample_groups for sample in samples)]


def validate_scope_against_its_grids(
    scope: DashboardScope,
    owners: Mapping[str, str],
) -> list[TiltSeriesValidation]:
    """Validate the scope's tilt series, each against its whole grid.

    A dashboard narrowed to one search map or batch position holds only that
    entity's few series; on their own they set their own expected count, so a
    lone 20-image series read Complete there and Incomplete on its row. The
    grid's other series join the vote; only the scope's own are returned.
    """

    in_scope: dict[int, TiltSeries] = {id(tilt): tilt for tilt in scope.tilt_series}
    voters = list(in_scope.values())
    seen = set(in_scope)
    for sample in scope_samples(scope):
        for tilt in sample.tilt_series:
            if id(tilt) not in seen:
                seen.add(id(tilt))
                voters.append(tilt)
    verdicts = validate_tilt_series_by_grid(voters, owners)
    by_object = {id(tilt): verdict for tilt, verdict in zip(voters, verdicts, strict=True)}
    return [by_object[id(tilt)] for tilt in scope.tilt_series]


def scope_from(value: Any, workspace: Workspace | None = None) -> DashboardScope:
    """Dispatch to the right scope-builder for the user's tree selection."""

    if isinstance(value, LinkedAtlasDashboardScope):
        return _scope_from_linked_atlas_dashboard_scope(value, workspace)
    if isinstance(value, DashboardEntityScope):
        return _scope_from_dashboard_entity_scope(value)
    if isinstance(value, Session):
        return _scope_from_session(value, workspace)
    if isinstance(value, Sample):
        return _scope_from_sample(value)
    if isinstance(value, TiltSeries):
        return _scope_from_tilt_series(value)
    if isinstance(value, LinkedSampleGroup):
        return _scope_from_linked_group(value)
    if isinstance(value, list):
        return _scope_from_session_list(value, workspace)
    raise TypeError(f"session_dashboard_model does not handle {type(value).__name__}")


def _scope_from_linked_atlas_dashboard_scope(value: LinkedAtlasDashboardScope, workspace: Workspace | None = None) -> DashboardScope:
    base = scope_from(value.source, workspace)
    return DashboardScope(
        title=base.title,
        path=base.path,
        kind=base.kind,
        atlases=list(value.atlases),
        overviews=base.overviews,
        search_maps=base.search_maps,
        batch_positions=base.batch_positions,
        tilt_series=base.tilt_series,
        samples=base.samples,
        sample_groups=base.sample_groups,
        warnings=base.warnings,
        is_sample_scope=True,
    )


def _scope_from_dashboard_entity_scope(value: DashboardEntityScope) -> DashboardScope:
    source = value.source
    title = value.title or getattr(source, "name", None) or getattr(source, "id", "Selection")
    path = value.path
    if path is None:
        path_candidates = (
            getattr(source, "mrc_path", None),
            getattr(source, "image_path", None),
            getattr(source, "xml_path", None),
            getattr(source, "overview_image_path", None),
            getattr(source, "search_image_path", None),
            getattr(source, "tracking_image_path", None),
            getattr(source, "exposure_image_path", None),
        )
        path = str(next((candidate for candidate in path_candidates if candidate is not None), ""))
    warnings = list(getattr(source, "warnings", []) or [])
    return DashboardScope(
        title=title,
        path=path,
        kind=value.kind or type(source).__name__.replace("_", " ").lower(),
        atlases=[],
        overviews=[],
        search_maps=list(value.search_maps),
        batch_positions=list(value.batch_positions),
        tilt_series=list(value.tilt_series),
        samples=list(value.samples),
        sample_groups=list(value.sample_groups),
        warnings=warnings,
        is_sample_scope=True,
    )


def _scope_from_session(session: Session, workspace: Workspace | None = None) -> DashboardScope:
    workspace = workspace if workspace is not None else Workspace.build([session])
    atlases = summary_atlas_pairs(session)
    return DashboardScope(
        title=session.name,
        path=str(session.path),
        kind=session.kind.value,
        atlases=atlases,
        overviews=workspace.entities(EntityKind.OVERVIEW, session),
        search_maps=workspace.entities(EntityKind.SEARCH_MAP, session),
        batch_positions=workspace.entities(EntityKind.BATCH_POSITION, session),
        tilt_series=workspace.entities(EntityKind.TILT_SERIES, session),
        samples=list(session.samples),
        sample_groups=_sample_groups_from_samples(session.samples),
        warnings=session_warnings(session),
    )


def _scope_from_sample(sample: Sample) -> DashboardScope:
    atlases: list[tuple[str, Atlas]] = []
    if sample.atlas is not None:
        atlases.append((sample.name, sample.atlas))
    return DashboardScope(
        title=sample.name,
        path=str(sample.path),
        kind="sample",
        atlases=atlases,
        overviews=list(sample.overviews),
        search_maps=list(sample.search_maps),
        batch_positions=list(sample.batch_positions),
        tilt_series=list(sample.tilt_series),
        samples=[sample],
        sample_groups=[(clean_sample_label(sample.name), [sample])],
        warnings=[*prefix_warnings(sample.name, sample.warnings), *entity_scope_warnings(sample, sample.name, set())],
        is_sample_scope=True,
    )


def _scope_from_tilt_series(tilt: TiltSeries) -> DashboardScope:
    """Narrow dashboard scope used when a tilt-series row is selected."""

    label = tilt.name or tilt.id
    return DashboardScope(
        title=label,
        path=str(tilt.mrc_path),
        kind="tilt series",
        atlases=[],
        overviews=[],
        search_maps=[],
        batch_positions=[],
        tilt_series=[tilt],
        samples=[],
        sample_groups=[],
        warnings=list(tilt.warnings),
        is_sample_scope=True,
    )


def _scope_from_linked_group(group: LinkedSampleGroup) -> DashboardScope:
    """Linked groups span sessions: aggregate everything the linker assigned."""

    atlases: list[tuple[str, Atlas]] = []
    for member in group.samples:
        if member.atlas is not None:
            atlases.append((member.name, member.atlas))
    if not atlases and group.atlas is not None:
        atlases.append((group.label, group.atlas))

    warnings = linked_sample_group_warnings(group)

    primary_path = ""
    for member in group.samples:
        if member.path:
            primary_path = str(member.path)
            break

    return DashboardScope(
        title=group.label,
        path=primary_path,
        kind="linked sample group",
        atlases=atlases,
        overviews=list(group.overviews),
        search_maps=list(group.search_maps),
        batch_positions=list(group.batch_positions),
        tilt_series=list(group.tilt_series),
        samples=[_representative_sample(group.samples)],
        sample_groups=[(_sample_group_label(group.samples, fallback=group.label), list(group.samples))],
        warnings=warnings,
        is_sample_scope=True,
    )


def _scope_from_session_list(sessions: list[Session], workspace: Workspace | None = None) -> DashboardScope:
    """Aggregate scope across multiple loaded sessions (the project root view)."""

    if not sessions:
        return DashboardScope(
            title="No sessions loaded",
            path="",
            kind="empty",
            atlases=[],
            overviews=[],
            search_maps=[],
            batch_positions=[],
            tilt_series=[],
            samples=[],
            sample_groups=[],
            warnings=[],
        )
    workspace = workspace if workspace is not None else Workspace.build(sessions)
    if len(sessions) == 1:
        return _scope_from_session(sessions[0], workspace)

    atlases: list[tuple[str, Atlas]] = []
    overviews: list[Overview] = []
    search_maps: list[SearchMap] = []
    batch_positions: list[BatchPosition] = []
    tilt_series: list[TiltSeries] = []
    warnings: list[str] = []
    for session in sessions:
        atlases.extend(summary_atlas_pairs(session))
        overviews.extend(workspace.entities(EntityKind.OVERVIEW, session))
        search_maps.extend(workspace.entities(EntityKind.SEARCH_MAP, session))
        batch_positions.extend(workspace.entities(EntityKind.BATCH_POSITION, session))
        tilt_series.extend(workspace.entities(EntityKind.TILT_SERIES, session))
        warnings.extend(session_warnings(session))
    atlases = _dedupe_atlas_pairs(atlases)
    overviews = dedupe_entities(overviews)
    search_maps = dedupe_entities(search_maps)
    batch_positions = dedupe_entities(batch_positions)
    tilt_series = dedupe_entities(tilt_series)

    sample_groups = _linked_sample_groups_for_dashboard(sessions, workspace)
    samples = [_representative_sample(group_samples) for _, group_samples in sample_groups]

    title = " + ".join(session.name for session in sessions)
    return DashboardScope(
        title=title,
        path=str(sessions[0].path),
        kind="linked sessions",
        atlases=atlases,
        overviews=overviews,
        search_maps=search_maps,
        batch_positions=batch_positions,
        tilt_series=tilt_series,
        samples=samples,
        sample_groups=sample_groups,
        warnings=warnings,
    )


def _dedupe_atlas_pairs(values: Iterable[tuple[str, Atlas]]) -> list[tuple[str, Atlas]]:
    unique: list[tuple[str, Atlas]] = []
    seen: set[tuple[str, str]] = set()
    for label, atlas in values:
        key = canonical_entity_key(atlas)
        if key in seen:
            continue
        seen.add(key)
        unique.append((label, atlas))
    return unique


def scope_per_sample_counts(scope: DashboardScope, label: str) -> list[int]:
    mapping = {
        "Overviews": "overviews",
        "Search maps": "search_maps",
        "Batch positions": "batch_positions",
        "Tilt series": "tilt_series",
    }
    attr = mapping.get(label)
    if attr is None:
        return []
    return [
        sum(len(getattr(sample, attr, [])) for sample in samples)
        for _, samples in scope.sample_groups
    ]
