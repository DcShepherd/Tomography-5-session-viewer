"""Camera components for the motion."""
from __future__ import annotations
from dataclasses import dataclass
import math
from tomography_session_browser.ui.motion.tokens import CAMERA_PATH_RHO, DURATION_CAMERA_MAX_MS, DURATION_CAMERA_MS


@dataclass(frozen=True)
class ZoomPanPath:
    """A smooth zoom-and-pan path between two views of a plane.

    Views are a centre and a visible width. ``at(s)`` for ``s`` in
    ``[0, length]`` gives the view along the way; ``length`` measures the
    trip, so its duration can follow it.
    """

    start: tuple[float, float]
    end: tuple[float, float]
    start_width: float
    distance: float
    length: float
    rho: float
    r0: float
    zoom_sign: float

    def at(self, s: float) -> tuple[float, float, float]:
        s = min(self.length, max(0.0, s))
        w0, rho = self.start_width, self.rho
        if self.distance <= 0.0:
            width = w0 * math.exp(self.zoom_sign * rho * s)
            return self.start[0], self.start[1], width
        r0 = self.r0
        travelled = w0 / rho**2 * (math.cosh(r0) * math.tanh(rho * s + r0) - math.sinh(r0))
        width = w0 * math.cosh(r0) / math.cosh(rho * s + r0)
        fraction = min(1.0, max(0.0, travelled / self.distance))
        x = self.start[0] + (self.end[0] - self.start[0]) * fraction
        y = self.start[1] + (self.end[1] - self.start[1]) * fraction
        return x, y, width


def zoom_pan_path(
    start: tuple[float, float],
    start_width: float,
    end: tuple[float, float],
    end_width: float,
    *,
    rho: float = CAMERA_PATH_RHO,
) -> ZoomPanPath:
    """The path of van Wijk and Nuij (2003), "Smooth and efficient zooming
    and panning": a nearby target is panned to with a little zoom; a far one
    zooms out, crosses, and zooms back in, so the reviewer sees where the
    camera went instead of a blur of image.
    """

    w0, w1 = max(start_width, 1e-9), max(end_width, 1e-9)
    distance = math.hypot(end[0] - start[0], end[1] - start[1])
    if distance <= 1e-6 * max(w0, w1):
        return ZoomPanPath(
            start, end, w0, 0.0, abs(math.log(w1 / w0)) / rho, rho, 0.0, -1.0 if w1 < w0 else 1.0
        )
    # ln(-b + sqrt(b² + 1)) is -asinh(b), without the cancellation.
    b0 = (w1 * w1 - w0 * w0 + rho**4 * distance * distance) / (2.0 * w0 * rho**2 * distance)
    b1 = (w1 * w1 - w0 * w0 - rho**4 * distance * distance) / (2.0 * w1 * rho**2 * distance)
    r0, r1 = -math.asinh(b0), -math.asinh(b1)
    return ZoomPanPath(start, end, w0, distance, (r1 - r0) / rho, rho, r0, 1.0)


def camera_duration_ms(path_length: float) -> float:
    """``DURATION_CAMERA_MS`` for a hop, longer for a longer trip, capped."""

    return min(DURATION_CAMERA_MAX_MS, DURATION_CAMERA_MS + 160.0 * max(0.0, path_length - 1.0))

