"""Count atlas sessions and sample atlases as separate, deduplicated concepts."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from tomography_session_browser.domain.traversal import has_collection_data, is_single_atlas_placeholder
from tomography_session_browser.domain.enums import SessionKind
from tomography_session_browser.domain.models import Atlas, Sample, Session
from tomography_session_browser.presentation.dashboard.models import AtlasCountModel, LinkedAtlasDashboardScope
from tomography_session_browser.presentation.scopes import LinkedSampleGroup

LOGGER = logging.getLogger("tomography_session_browser.ui.session_presenter")


def _canonical_path_key(path: Path | None) -> str | None:
    """Stable key for a filesystem path, robust to symlinks / case quirks."""

    if path is None:
        return None
    try:
        # ``resolve()`` is preferred — it produces an absolute, case-
        # canonical key on Windows. Fall back to a normalised string if
        # the path doesn't exist (resolve still works in modern Python
        # but pre-3.10 used to raise; defensive in any case).
        return str(Path(path).resolve())
    except OSError:
        return str(Path(path)).lower()


def _canonical_sample_key(sample: Sample) -> str:
    return _canonical_path_key(sample.path) or sample.id


def canonical_atlas_key(atlas: Atlas) -> str:
    """Atlas key — prefer image_path, fall back to atlas.id.

    Two samples that point at the same Atlas/ folder share the same key,
    so the sample-atlas count never double-counts a shared atlas.
    """

    return _canonical_path_key(atlas.image_path) or atlas.id


def compute_atlas_counts(value: Any) -> AtlasCountModel:
    """Distinct atlas / data-collection counts for any tree selection.

    Accepts the same inputs as :func:`session_dashboard_model` —
    ``Session``, ``Sample``, ``LinkedSampleGroup`` or ``list[Session]``.
    Counts are deduplicated by canonical path so the same sample/atlas
    referenced from multiple UI nodes (e.g. a session, a linked-group
    container, and the sample itself) only contributes once.

    "Samples with atlas" counts the per-grid Atlas/ folders directly
    attached to a sample (i.e. ``sample.atlas`` is not None). A "Single
    Atlas" placeholder sample (the screening session's global overview,
    ``is_single_atlas_placeholder``) is not a grid and is left out, by the
    same rule that leaves it off the Atlases card. One used to be
    subtracted per screening session instead, which undercounted every
    session that has no such placeholder.

    Emits a debug log line summarising the inputs and the resulting
    counts so the user can verify exactly which sessions/samples were
    considered when a number looks wrong.
    """

    if isinstance(value, LinkedAtlasDashboardScope):
        base = compute_atlas_counts(value.source)
        linked_count = len({canonical_atlas_key(atlas) for _label, atlas in value.atlases})
        return AtlasCountModel(
            atlas_session_count=base.atlas_session_count,
            data_collection_sample_count=base.data_collection_sample_count,
            samples_with_atlas_count=linked_count if linked_count else base.samples_with_atlas_count,
            sample_atlas_count=linked_count if linked_count else base.sample_atlas_count,
        )
    if isinstance(value, Sample):
        return _atlas_counts_from_samples([value], atlas_session_count=0)
    if isinstance(value, LinkedSampleGroup):
        return _atlas_counts_from_samples(value.samples, atlas_session_count=0)
    if isinstance(value, Session):
        return _atlas_counts_from_sessions([value])
    if isinstance(value, list):
        return _atlas_counts_from_sessions(value)
    return AtlasCountModel()


def _atlas_counts_from_sessions(sessions: list[Session]) -> AtlasCountModel:
    atlas_sessions = [s for s in sessions if s.kind == SessionKind.ATLAS_SCREENING]
    all_samples: list[Sample] = []
    for session in sessions:
        all_samples.extend(session.samples)

    counts = _atlas_counts_from_samples(
        all_samples, atlas_session_count=len(atlas_sessions)
    )

    LOGGER.debug(
        "atlas counts: total_sessions=%d atlas_session_paths=%s "
        "data_collection_session_paths=%s "
        "atlas_session_count=%d "
        "data_collection_sample_count=%d "
        "samples_with_atlas_count=%d "
        "sample_atlas_count=%d",
        len(sessions),
        [str(s.path) for s in atlas_sessions],
        [str(s.path) for s in sessions if s.kind != SessionKind.ATLAS_SCREENING],
        len(atlas_sessions),
        counts.data_collection_sample_count,
        counts.samples_with_atlas_count,
        counts.sample_atlas_count,
    )

    return AtlasCountModel(
        atlas_session_count=len(atlas_sessions),
        data_collection_sample_count=counts.data_collection_sample_count,
        samples_with_atlas_count=counts.samples_with_atlas_count,
        sample_atlas_count=counts.sample_atlas_count,
    )


def _atlas_counts_from_samples(
    samples: list[Sample],
    *,
    atlas_session_count: int,
) -> AtlasCountModel:
    seen_sample_keys: set[str] = set()
    excluded = 0
    dc_samples: list[Sample] = []
    direct_atlas_sample_keys: set[str] = set()
    sample_atlas_keys: set[str] = set()
    sample_atlas_paths: list[str] = []

    for sample in samples:
        key = _canonical_sample_key(sample)
        if key in seen_sample_keys:
            excluded += 1
            continue
        seen_sample_keys.add(key)
        if has_collection_data(sample):
            dc_samples.append(sample)
        if sample.atlas is not None and not is_single_atlas_placeholder(sample):
            direct_atlas_sample_keys.add(key)
            atlas_key = canonical_atlas_key(sample.atlas)
            if atlas_key and atlas_key not in sample_atlas_keys:
                sample_atlas_keys.add(atlas_key)
                sample_atlas_paths.append(atlas_key)

    samples_with_atlas_count = len(direct_atlas_sample_keys)
    sample_atlas_count = len(sample_atlas_keys)

    LOGGER.debug(
        "atlas counts (sample scope): considered=%d data_collection=%d "
        "direct_atlas_samples=%d unique_atlases=%d "
        "atlas_sessions=%d "
        "samples_with_atlas_after=%d sample_atlases_after=%d "
        "excluded_duplicates=%d sample_atlas_paths=%s",
        len(samples),
        len(dc_samples),
        len(direct_atlas_sample_keys),
        len(sample_atlas_keys),
        atlas_session_count,
        samples_with_atlas_count,
        sample_atlas_count,
        excluded,
        sample_atlas_paths,
    )

    return AtlasCountModel(
        atlas_session_count=0,
        data_collection_sample_count=len(dc_samples),
        samples_with_atlas_count=samples_with_atlas_count,
        sample_atlas_count=sample_atlas_count,
    )

