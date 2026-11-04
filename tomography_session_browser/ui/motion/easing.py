"""Easing components for the motion."""
from __future__ import annotations
from collections.abc import Callable


def cubic_bezier(x1: float, y1: float, x2: float, y2: float) -> Callable[[float], float]:
    """CSS timing curve: invert its monotone x coordinate before evaluating y."""
    if not (0 <= x1 <= 1 and 0 <= x2 <= 1):
        raise ValueError("Bezier x control points must be in [0, 1]")

    def coordinate(u: float, a: float, b: float) -> float:
        return 3 * (1-u)**2 * u * a + 3 * (1-u) * u*u * b + u**3

    def ease(t: float) -> float:
        if t <= 0 or t >= 1:
            return max(0., min(1., t))
        low, high, u = 0., 1., t
        for _ in range(24):
            error = coordinate(u, x1, x2) - t
            if abs(error) < 1e-7:
                break
            if error < 0:
                low = u
            else:
                high = u
            slope = 3*(1-u)**2*x1 + 6*(1-u)*u*(x2-x1) + 3*u*u*(1-x2)
            candidate = u-error/slope if slope > 1e-8 else -1.
            u = candidate if low < candidate < high else (low+high)/2
        return coordinate(u, y1, y2)

    return ease


EASE_STANDARD = cubic_bezier(.2, 0., 0., 1.)


def ease_out_cubic(t: float) -> float:
    return 1.0 - (1.0 - t) ** 3


def ease_in_out_cubic(t: float) -> float:
    """For travel with a start and an end: the camera eases away and lands."""

    return 4.0 * t * t * t if t < 0.5 else 1.0 - (-2.0 * t + 2.0) ** 3 / 2.0

