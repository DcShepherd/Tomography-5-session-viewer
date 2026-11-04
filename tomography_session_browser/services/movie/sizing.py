"""One export pixel ratio for the whole frame: Native, or the largest fit in a box.

Content is laid out in logical units, as the viewer's panes are. The ratio turns
logical units into output pixels. Shape pads the content with black, and a fit
applies to the padded frame, so a fit never exceeds its box (D10).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math

from tomography_session_browser.services.volume.render import RENDER_PIXEL_LIMIT

NATIVE = "Native"
SIZES: dict[str, tuple[int, int] | None] = {NATIVE: None, "HD": (1280, 720), "Full HD": (1920, 1080), "4K": (3840, 2160)}
MATCH = "Match the view"
SHAPES: dict[str, float | None] = {MATCH: None, "16:9": 16 / 9, "4:3": 4 / 3, "Square": 1.}
#: The black gap between Ortho panes and the title band, in logical pixels.
PANE_GAP = 2
TITLE_BAND = 32


@dataclass(frozen=True, slots=True)
class PaneBox:
    """One pane in content coordinates (logical): its rectangle, its image inside it and the image's crop."""
    axis: str
    pane: tuple[float, float, float, float]
    image: tuple[float, float, float, float]
    region: tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class ContentLayout:
    size: tuple[float, float]
    panes: tuple[PaneBox, ...]
    sweep: str
    title_band: float = 0.

    @property
    def sweep_box(self) -> PaneBox:
        return next(box for box in self.panes if box.axis == self.sweep)

    @property
    def native_ratio(self) -> float:
        """Output pixels per logical pixel that give one voxel per pixel in the sweeping pane."""
        box = self.sweep_box
        return (box.region[2] - box.region[0]) / box.image[2]


@dataclass(frozen=True, slots=True)
class PaneRaster:
    axis: str
    pane: tuple[int, int, int, int]
    image: tuple[int, int, int, int]
    region: tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class FrameGeometry:
    ratio: float
    frame: tuple[int, int]
    panes: tuple[PaneRaster, ...]
    #: The title band at the top of the content, as (x, y, width, height); empty without a title.
    title: tuple[int, int, int, int] | None
    #: The export ratio over the Native ratio: 1 is one voxel per pixel.
    factor: float

    @property
    def scale_note(self) -> str:
        if abs(self.factor - 1) < .005:
            return "one voxel per pixel"
        return f"enlarges {self.factor:.2f}×" if self.factor > 1 else f"reduces to {self.factor:.2f}×"


class SizeRefused(ValueError):
    pass


def even(value: float) -> int:
    rounded = max(2, round(value))
    return rounded + rounded % 2


def with_title(layout: ContentLayout) -> ContentLayout:
    """Reserve a band above the panes, so the title never covers the image."""
    band = TITLE_BAND
    panes = tuple(replace(box, pane=(box.pane[0], box.pane[1] + band, *box.pane[2:]),
                          image=(box.image[0], box.image[1] + band, *box.image[2:])) for box in layout.panes)
    return replace(layout, size=(layout.size[0], layout.size[1] + band), panes=panes, title_band=band)


def padded(size: tuple[float, float], shape: str) -> tuple[float, float]:
    if shape not in SHAPES:
        raise ValueError("Unknown movie shape.")
    aspect = SHAPES[shape]
    width, height = size
    if aspect is None:
        return width, height
    return (height * aspect, height) if width / height < aspect else (width, width / aspect)


def frame_geometry(layout: ContentLayout, size: str, shape: str, *, ratio: float | None = None) -> FrameGeometry:
    """Place every pane at one ratio. Pixels are square; both dimensions are even."""
    if size not in SIZES:
        raise ValueError("Unknown movie size.")
    native = layout.native_ratio
    width, height = padded(layout.size, shape)
    box = SIZES[size]
    if ratio is None:
        ratio = native if box is None else min(box[0] / width, box[1] / height)
    if not math.isfinite(ratio) or ratio <= 0:
        raise ValueError("The view has no usable size. Use Fit, then open the dialog again.")
    frame = (even(width * ratio), even(height * ratio))
    if box is not None:
        frame = (min(frame[0], box[0]), min(frame[1], box[1]))
    content = (round(layout.size[0] * ratio), round(layout.size[1] * ratio))
    # Even offsets keep the content on the encoder's 2 × 2 chroma grid.
    left, top = (frame[0] - content[0]) // 4 * 2, (frame[1] - content[1]) // 4 * 2

    def place(rect: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
        x, y, w, h = rect
        x0, y0 = left + round(x * ratio), top + round(y * ratio)
        x1, y1 = min(frame[0], left + round((x + w) * ratio)), min(frame[1], top + round((y + h) * ratio))
        return x0, y0, max(1, x1 - x0), max(1, y1 - y0)

    panes = tuple(PaneRaster(box.axis, place(box.pane), place(box.image), box.region) for box in layout.panes)
    title = place((0, 0, layout.size[0], layout.title_band)) if layout.title_band else None
    return FrameGeometry(ratio, frame, panes, title, ratio / native)


def check_limits(geometry: FrameGeometry, layout: ContentLayout, size: str, shape: str) -> None:
    """Refuse a frame whose largest pane exceeds the renderer's limit, and name the largest size allowed."""
    largest = max(geometry.panes, key=lambda pane: pane.image[2] * pane.image[3])
    pixels = largest.image[2] * largest.image[3]
    if pixels <= RENDER_PIXEL_LIMIT:
        return
    allowed = frame_geometry(layout, size, shape, ratio=geometry.ratio * math.sqrt(RENDER_PIXEL_LIMIT / pixels) * .999)
    raise SizeRefused(f"Too large to render: the {largest.axis} pane needs {largest.image[2]} × {largest.image[3]} pixels. "
                      f"The largest size allowed is {allowed.frame[0]} × {allowed.frame[1]}. Select a smaller size.")
