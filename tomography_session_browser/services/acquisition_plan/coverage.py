"""Match a uniform planned schedule to parser-confirmed measured angles."""
from collections import Counter
from enum import StrEnum
from math import ceil, floor, isfinite


class Coverage(StrEnum):
    COVERED = "covered"
    GAP = "gap"
    UNVERIFIED = "unverified"


def uniform_coverage(angles: tuple[float, ...], lower: float, step: float, expected: int) -> Coverage:
    if not angles or not isfinite(step) or step <= 0:
        return Coverage.UNVERIFIED
    tolerance = 0.05 + 1e-12
    slots: Counter[int] = Counter()
    for angle in angles:
        low_slot = (angle - tolerance - lower) / step
        high_slot = (angle + tolerance - lower) / step
        if high_slot < 0 or low_slot > expected - 1:
            continue
        first = ceil(max(0, low_slot))
        last = floor(min(expected - 1, high_slot))
        if last > first:
            return Coverage.UNVERIFIED
        if first == last:
            slots[first] += 1
    if len(slots) < expected:
        return Coverage.GAP
    if any(count > 1 for count in slots.values()):
        return Coverage.UNVERIFIED
    return Coverage.COVERED
