"""What the context panel says about a scope or an item.

``describe_object`` (``describe/entities.py``) describes one entity on its
own; ``ContextDescriber`` adds what only the loaded project knows: which atlas
the item belongs to, directly or through a linked screening session, and on
which sample. Text only: nothing here changes a session or a link.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from tomography_session_browser.domain.models import (
    Atlas,
    BatchPosition,
    Overview,
    Sample,
    SearchMap,
    SearchTile,
    Session,
    TiltSeries,
)
from tomography_session_browser.domain.traversal import has_collection_data
from tomography_session_browser.presentation.describe.entities import describe_object
from tomography_session_browser.presentation.project_tree import ProjectTreeGroup, project_group_badge
from tomography_session_browser.presentation.scopes import LinkedSampleGroup
from tomography_session_browser.presentation.workspace import (
    EntityKind,
    Workspace,
    atlas_display_key,
    display_atlases,
    display_sample_name,
)
from tomography_session_browser.services.batch_inference import entity_owner_keys
from tomography_session_browser.services.item_status import ItemListStatus, validate_tilt_series_by_grid
from tomography_session_browser.services.tilt_series_validation import TiltSeriesValidation

#: A scope's place in the project: the session, sample or group holding it.
ContextFor = Callable[[Any], "Session | Sample | LinkedSampleGroup | ProjectTreeGroup | None"]


def describe_project_group(group: ProjectTreeGroup) -> str:
    lines = [
        f"Project group: {group.display_name}",
        f"Automatic name: {group.automatic_name}",
        f"Type: {project_group_badge(group)}",
        f"Loaded sessions: {len(group.sessions)}",
    ]
    if group.kind == "linked":
        lines.append(f"Linked data collections: {len(group.collection_sessions)}")
    for session in group.sessions:
        lines.append("")
        lines.append(f"{session.name}")
        lines.append(f"  Kind: {session.kind.value}")
        lines.append(f"  Path: {session.path}")
    if group.warnings:
        lines.append("")
        lines.extend(group.warnings)
    return "\n".join(lines)


def describe_loaded_sessions(sessions: Sequence[Session]) -> str:
    """The project with no one group in scope: the sessions it holds."""

    return "\n".join(
        [f"Loaded sessions ({len(sessions)}):"]
        + [f"  - {session.name}" for session in sessions]
    )


def samples_for_context(context: Any) -> list[Sample]:
    """The samples a scope covers."""

    if isinstance(context, Sample):
        return [context]
    if isinstance(context, LinkedSampleGroup):
        return list(context.samples)
    if isinstance(context, Session):
        return list(context.samples)
    if isinstance(context, ProjectTreeGroup):
        return [sample for session in context.sessions for sample in session.samples]
    return []


def tilt_validation_for(
    tilt: TiltSeries,
    known: Mapping[str, TiltSeriesValidation],
    workspace: Workspace,
) -> TiltSeriesValidation | None:
    """The verdict the lists show (``known``), or one from the series' own grid.

    A series outside the current viewer scope used to be validated alone,
    with no peers to set an expected count, so the panel said UNKNOWN
    where the dashboard said Complete.
    """

    validation = known.get(tilt.id)
    if validation is not None:
        return validation
    for session in workspace.sessions:
        for sample in session.samples:
            if any(candidate is tilt for candidate in sample.tilt_series):
                owners = entity_owner_keys([sample])
                return next(
                    (
                        result
                        for result in validate_tilt_series_by_grid(sample.tilt_series, owners)
                        if result.tilt_series_id == tilt.id
                    ),
                    None,
                )
    return None


class ContextDescriber:
    """Describes items in ``workspace``; ``context_for`` places an item in it."""

    def __init__(self, workspace: Workspace, context_for: ContextFor) -> None:
        self._workspace = workspace
        self._context_for = context_for

    def describe(
        self,
        value: Any,
        *,
        tilt_validation: TiltSeriesValidation | None = None,
        list_status: ItemListStatus | None = None,
    ) -> str:
        text = describe_object(value, tilt_validation=tilt_validation, list_status=list_status)
        atlas_lines = self._atlas_linkage_context_lines(value)
        if not atlas_lines:
            return self._normalise_missing_atlas_text(text, value)
        lines = [
            line
            for line in text.splitlines()
            if line.strip() not in {"Atlas: no", "Atlas: yes", "Atlas: No atlas associated"}
        ]
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("Atlas linkage:")
        lines.extend(f"  {line}" for line in atlas_lines)
        return "\n".join(lines)

    def _normalise_missing_atlas_text(self, text: str, value: Any) -> str:
        if isinstance(value, Sample | LinkedSampleGroup):
            return text.replace("Atlas: no", "Atlas: No atlas associated")
        if isinstance(value, Session) and not self._workspace.entities(EntityKind.ATLAS, value):
            suffix = "\n\nAtlas linkage:\n  Atlas: No atlas associated"
            return text if "Atlas linkage:" in text else f"{text}{suffix}"
        return text

    def _atlas_linkage_context_lines(self, value: Any) -> list[str]:
        direct, linked = self._atlas_associations_for_context(value)
        if direct:
            return self._atlas_linkage_lines("Direct atlas", direct, value)
        if linked:
            return self._atlas_linkage_lines("Linked atlas from screening session", linked, value)
        return []

    def _atlas_associations_for_context(self, value: Any) -> tuple[list[Atlas], list[Atlas]]:
        if isinstance(value, Atlas):
            return [value], []
        if isinstance(value, LinkedSampleGroup):
            if value.atlas is not None:
                return [], [value.atlas]
            return [], []
        if isinstance(value, Sample):
            direct = [value.atlas] if value.atlas is not None else []
            linked = [] if direct else self._workspace.linked_atlases(value)
            return direct, linked
        if isinstance(value, Session):
            direct = display_atlases(value)
            linked = [] if direct else self._workspace.linked_atlases(value)
            return direct, linked
        if isinstance(value, SearchMap | BatchPosition | TiltSeries | SearchTile | Overview):
            context = self._context_for(value)
            if context is value:
                return [], []
            return self._atlas_associations_for_context(context)
        return [], []

    def _atlas_linkage_lines(self, label: str, atlases: list[Atlas], value: Any) -> list[str]:
        if not atlases:
            return []
        atlas = atlases[0]
        lines = [f"Atlas: {label}"]
        sample_labels = self._atlas_linkage_sample_labels(value, atlas)
        if sample_labels:
            lines.append(f"Sample: {', '.join(sample_labels)}")
        path = atlas_context_path(atlas)
        if path:
            lines.append(f"Path: {path}")
        if len(atlases) > 1:
            lines.append(f"Additional atlases: {len(atlases) - 1}")
        return lines

    def _atlas_linkage_sample_labels(self, value: Any, atlas: Atlas) -> list[str]:
        samples = samples_for_context(value)
        if not samples and isinstance(value, Atlas | Overview | SearchMap | SearchTile | BatchPosition | TiltSeries):
            samples = [sample for sample in self._workspace.samples if self._workspace.sample_contains(sample, value)]
        if not samples:
            context = self._context_for(value)
            samples = samples_for_context(context)

        atlas_key = atlas_display_key(atlas)
        matches: list[Sample] = []
        for sample in samples:
            if sample.atlas is atlas:
                matches.append(sample)
                continue
            if any(
                atlas_display_key(candidate) == atlas_key
                for candidate in self._workspace.linked_atlases(sample)
            ):
                matches.append(sample)

        collection_matches = [sample for sample in matches if has_collection_data(sample)]
        preferred = collection_matches or matches
        labels: list[str] = []
        seen: set[str] = set()
        for sample in preferred:
            label = display_sample_name(sample.name)
            key = label.casefold()
            if key in seen:
                continue
            seen.add(key)
            labels.append(label)
        return labels


def atlas_context_path(atlas: Atlas) -> str | None:
    if atlas.image_path is not None:
        return str(atlas.image_path)
    if atlas.mrc_metadata is not None and atlas.mrc_metadata.path is not None:
        return str(atlas.mrc_metadata.path)
    return None
