from __future__ import annotations

import re

from tomography_session_browser.domain.models import BatchPosition, TiltSeries


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
