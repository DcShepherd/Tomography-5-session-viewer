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
from tomography_session_browser.presentation.describe.sections import emit_section
from tomography_session_browser.presentation.describe.summary import sessions_summary_lines
from tomography_session_browser.presentation.project_tree import ProjectTreeGroup
from tomography_session_browser.presentation.scopes import LinkedSampleGroup
from tomography_session_browser.presentation.workspace import (
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
    """A project group's source folders and shared settings (design review B2.6).

    The page titles the group and carries its counts, so this is only where
    its sessions came from and how they were acquired. A group's own link
    warnings stay: an ambiguous atlas match must remain visible, and no other
    page says it yet.
    """

    lines: list[str] = []
    if group.is_renamed:
        # Only a custom name has an automatic one worth saying; otherwise it
        # is the same words twice. The tree and report tooltips follow the rule.
        lines.append(f"Automatic name: {group.automatic_name}")
    lines += sessions_summary_lines(group.sessions)
    emit_section(lines, "Warnings", [f"- {warning}" for warning in group.warnings])
    return "\n".join(lines)


def describe_loaded_sessions(sessions: Sequence[Session]) -> str:
    """The project with no one group in scope: every session it holds."""

    return "\n".join(sessions_summary_lines(sessions))


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
        describes_page_item: bool,
        tilt_validation: TiltSeriesValidation | None = None,
        list_status: ItemListStatus | None = None,
    ) -> str:
        """The panel's text for ``value``: every description goes through here.

        ``describes_page_item`` says the page beside the panel already titles
        ``value`` and carries its status pill and its Linked strip. The text
        then leaves out the Status row, and the atlas section when the strip's
        Atlas jump offers the one atlas it would name.
        """

        text = describe_object(
            value,
            tilt_validation=tilt_validation,
            list_status=list_status,
            describes_page_item=describes_page_item,
        )
        if isinstance(value, Session | Sample | LinkedSampleGroup):
            # A scope's summary is its source folders and settings; which atlas
            # it has is the Atlas card's and the Atlas tab's to say.
            return text
        label, atlases = self._atlas_linkage(value)
        if not atlases or (describes_page_item and len(atlases) == 1):
            return text
        lines = text.splitlines()
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("Atlas linkage:")
        lines.extend(f"  {line}" for line in self._atlas_linkage_lines(label, atlases, value))
        return "\n".join(lines)

    def _atlas_linkage(self, value: Any) -> tuple[str, list[Atlas]]:
        """How ``value`` reaches an atlas, and which: direct, else linked, else none."""

        direct, linked = self._atlas_associations_for_context(value)
        if direct:
            return "Direct atlas", direct
        if linked:
            return "Linked atlas from screening session", linked
        return "", []

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
