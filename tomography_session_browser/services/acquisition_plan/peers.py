"""Infer a count from the eligible same-grid, unknown-scheme cohort."""
from collections import Counter
from collections.abc import Iterable

from tomography_session_browser.domain.models import TiltSeries
from tomography_session_browser.services.acquisition_plan.evidence import NoPlan, VerifiedPlan, plan_evidence


def peer_count(series: Iterable[TiltSeries], interrupted: set[int], minimum: int) -> int | None:
    schemes: set[tuple[float, float, float]] = set()
    donors: Counter[int] = Counter()
    for item in series:
        plan = plan_evidence(item, None, [])
        if isinstance(plan, VerifiedPlan):
            schemes.add((plan.minimum, plan.maximum, plan.increment))
            continue
        metadata = item.mrc_metadata
        if (id(item) not in interrupted and isinstance(plan, NoPlan)
                and plan.state in ("absent", "invalid") and metadata is not None
                and metadata.data_complete is True and metadata.available_frames is not None
                and metadata.available_frames >= minimum):
            donors[metadata.available_frames] += 1
    modes = donors.most_common(2)
    if len(schemes) > 1 or not modes or (len(modes) == 2 and modes[0][1] == modes[1][1]):
        return None
    return modes[0][0]
