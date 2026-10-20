"""The Atlas contact sheet and viewer share one conservative collection context."""
from __future__ import annotations

from collections.abc import Iterable

from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.domain.models import Atlas, Sample
from tomography_session_browser.domain.traversal import has_collection_data
from tomography_session_browser.presentation.dashboard.models import AtlasRowModel
from tomography_session_browser.presentation.workspace import Workspace, display_sample_name
from tomography_session_browser.services.item_status import ItemListStatus, atlas_tile_count, status_tag
from tomography_session_browser.services.warning_classification import (
    CATEGORY_ATLAS_FILES_NOT_FOUND, CATEGORY_ATLAS_REACQUIRED, classify_warning,
)


def collection_members(atlas: Atlas, workspace: Workspace | None, *, samples: Iterable[Sample] = ()) -> list[Sample]:
    """Loaded collections in the atlas owner's existing sample group; infer nothing."""

    if workspace is None:
        return []
    owner = next((sample for sample in (*samples, *workspace.samples) if sample.atlas is atlas), None)
    if owner is None:
        return []
    group = next((members for members in workspace.sample_groups if any(member is owner for member in members)), ())
    return [member for member in group if member is not owner and has_collection_data(member)]


def atlas_row(label: str, atlas: Atlas, workspace: Workspace | None, *, samples: Iterable[Sample] = ()) -> AtlasRowModel:
    """Name a collection and count its tilts only when exactly one is linked."""

    categories = {classify_warning(warning).category for warning in atlas.warnings}
    collections = collection_members(atlas, workspace, samples=samples)
    single = collections[0] if len(collections) == 1 else None
    return AtlasRowModel(
        sample_label=label,
        image_path=str(atlas.image_path) if atlas.image_path else None,
        missing=atlas.image_path is None or CATEGORY_ATLAS_FILES_NOT_FOUND in categories,
        reacquired=CATEGORY_ATLAS_REACQUIRED in categories,
        linked_sample=display_sample_name(single.name) if single is not None else None,
        tilt_series_count=len(single.tilt_series) if single is not None else None,
        atlas_id=atlas.id,
        collection_sample_ids=tuple(sample.id for sample in collections),
    )


def atlas_list_status(atlas: Atlas, row: AtlasRowModel, *, available: bool) -> ItemListStatus:
    """Render the shared row; normal mosaic metadata stays in its tooltip."""

    parts = [row.linked_sample] if row.linked_sample is not None else []
    if row.tilt_series_count is not None:
        parts.append(count_phrase(row.tilt_series_count, "tilt series", "tilt series"))
    status = "missing" if row.missing or not available else "available"
    details = [row.sample_label, f"Status: {status.title()}", *parts, count_phrase(atlas_tile_count(atlas), "tile")]
    if (metadata := atlas.mrc_metadata) is not None and metadata.nx and metadata.ny:
        details.append(f"{metadata.nx} × {metadata.ny}")
    details.extend(atlas.warnings)
    return ItemListStatus(status, " · ".join(parts), "\n".join(details), frozenset({status_tag(status)}))
