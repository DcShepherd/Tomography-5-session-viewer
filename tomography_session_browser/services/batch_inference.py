from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping

from tomography_session_browser.domain.models import BatchPosition, Sample, TiltSeries


def inferred_batch_label_for_tilt(tilt: TiltSeries) -> str | None:
    """Infer a likely batch-position label for orphaned failed tilt series.

    The heuristic is intentionally conservative: ``sample_1`` remains
    ``sample_1``, while ``sample_1_2`` and ``sample_1_3`` group under
    ``sample_1`` only when callers have already decided explicit batch
    metadata is absent or unreliable.
    """

    text = re.sub(r"\.[^.]+$", "", (tilt.name or tilt.id or "").strip())
    match = re.fullmatch(r"(.+?_\d+)(?:_\d+)?", text)
    if not match:
        return None
    return match.group(1)


def batch_inference_key(label: str | None) -> str:
    return re.sub(r"\s+", "_", (label or "").strip().lower())


def batch_inference_keys(batch: BatchPosition) -> set[str]:
    keys = {batch_inference_key(batch.name), batch_inference_key(batch.id)}
    return {key for key in keys if key}


def inferred_group_token(label: str | None, owner: str | None = None) -> str:
    """Names one inferred batch group: its label key, plus its grid when known.

    Two grids can each have orphans under the same label; the grid keeps their
    dashboard tiles and list filters apart. Lowercase and space-free, so it
    survives the list filter (which lowercases and splits on spaces).
    """

    key = batch_inference_key(label)
    if not owner:
        return key
    digest = hashlib.sha1(owner.casefold().encode("utf-8")).hexdigest()[:8]
    return f"{key}@{digest}"


def entity_owner_keys(samples: Iterable[Sample]) -> dict[str, str]:
    """``entity.id`` → the sample (grid) folder that holds it.

    Batch-position names repeat across grids ("Position_1" on every grid), so
    a name-inferred match is only allowed within one grid. Entities no sample
    lists stay unowned and match by name as before.
    """

    owners: dict[str, str] = {}
    for sample in samples:
        owner = str(sample.path)
        for entity in (*sample.batch_positions, *sample.tilt_series):
            owners.setdefault(entity.id, owner)
    return owners


def same_owner(owners: Mapping[str, str] | None, first_id: str, second_id: str) -> bool:
    """False only when both entities are known to sit on different grids."""

    if not owners:
        return True
    first, second = owners.get(first_id), owners.get(second_id)
    return first is None or second is None or first == second


def inferred_label_matches_batch(
    label: str | None,
    batch: BatchPosition,
    *,
    tilt_id: str,
    owners: Mapping[str, str] | None = None,
) -> bool:
    """Whether an orphan's inferred batch label names ``batch`` on its own grid."""

    key = batch_inference_key(label)
    return bool(key) and key in batch_inference_keys(batch) and same_owner(owners, tilt_id, batch.id)
