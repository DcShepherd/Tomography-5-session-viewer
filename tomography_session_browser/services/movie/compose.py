"""Qt-free frame composition: panes, scale bars, plane labels, intersection lines, the Ortho corner and the title.

The viewer paints its labels and lines with Qt; a movie bakes them into its pixels
here, with a fixed export typography that does not follow the app theme.
"""
from __future__ import annotations

import threading

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from tomography_session_browser.domain.tomograms import Volume
from tomography_session_browser.services.movie.models import MoviePane, MovieRequest
from tomography_session_browser.services.movie.sizing import even
from tomography_session_browser.services.volume.render import calibrated_spacing, intersection_point, plane_geometry, plane_spacing
from tomography_session_browser.services.volume.scale_bar import ScaleBarStyle, draw_scale_bar

#: The viewer's plane colours (`TOMOGRAM_PLANE_COLOURS`), fixed for exports in either theme.
PLANE_COLOURS = {"XY": "#5aa2ff", "XZ": "#c38cff", "YZ": "#ff8a65"}
TEXT_COLOUR = "#f0f3f7"
#: Export typography in logical pixels, and the smallest raster text height.
LABEL_PX, TITLE_PX, MINIMUM_TEXT_PX = 13, 18, 11
#: Annotations never shrink below this ratio, so labels stay readable at small Native sizes.
MINIMUM_ANNOTATION_RATIO = .6
NORMALS = {"XY": "Z", "XZ": "Y", "YZ": "X"}
#: The plane whose colour each pane's (vertical, horizontal) line takes, as in the viewer.
LINE_PLANES = {"XY": ("YZ", "XZ"), "XZ": ("YZ", "XY"), "YZ": ("XY", "XZ")}
#: The Ortho corner's rows: each coordinate in the colour of the plane it moves.
CORNER_ROWS = (("YZ", 0), ("XZ", 1), ("XY", 2))
_FONTS = threading.local()


def export_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """One font per size and weight for each thread; a FreeType face is not thread-safe."""
    fonts = getattr(_FONTS, "by_size", None)
    if fonts is None:
        fonts = _FONTS.by_size = {}
    font = fonts.get((size, bold))
    if font is None:
        for name in (("arialbd.ttf", "DejaVuSans-Bold.ttf") if bold else ("arial.ttf", "DejaVuSans.ttf")):
            try:
                font = ImageFont.truetype(name, size)
                break
            except OSError:
                continue
        else:
            font = ImageFont.load_default(size=size)
        fonts[(size, bold)] = font
    return font


def annotation_ratio(ratio: float) -> float:
    return max(ratio, MINIMUM_ANNOTATION_RATIO)


def text_px(logical: int, ratio: float) -> int:
    return max(MINIMUM_TEXT_PX, round(logical * ratio))


def plane_label(axis: str, plane: int, planes: int, *, oblique: bool, names: bool, numbers: bool) -> str:
    """The single-pane label, for example "XY · Z 131 / 256"; an oblique view names its reference-plane offset."""
    number = (f"offset {plane + 1} / {planes}" if oblique else f"{NORMALS[axis]} {plane + 1} / {planes}") if numbers else ""
    return " · ".join(part for part in ((axis if names else ""), number) if part)


def bar_scale(volume: Volume, pane: MoviePane) -> float | None:
    """Ångström per output pixel of a pane image, or None without a calibrated pixel size."""
    spacing = calibrated_spacing(volume, pane.recipe.spacing_override)
    if spacing is None:
        return None
    x0, _, x1, _ = pane.recipe.region
    return plane_spacing(spacing, pane.recipe.axis)[0] * (x1 - x0) / pane.raster.image[2]


def bar_notice(volume: Volume, pane: MoviePane, style: ScaleBarStyle, ratio: float) -> str:
    """Draw the bar on a blank image of the pane's size: the same rule decides the dialog and the export."""
    scale = bar_scale(volume, pane)
    if scale is None:
        return ""
    width, height = pane.raster.image[2:]
    return draw_scale_bar(Image.new("L", (width, height)), scale, style, pixel_ratio=annotation_ratio(ratio))[3]


def annotate_pane(volume: Volume, pane: MoviePane, pixels: np.ndarray, style: ScaleBarStyle,
                  ratio: float) -> tuple[np.ndarray, str]:
    """A rendered pane as RGB, with its scale bar when it carries one, and the bar's physical label."""
    image, label = Image.fromarray(pixels), ""
    if pane.scale_bar:
        scale = bar_scale(volume, pane)
        if scale is not None:
            image, label, _, notice = draw_scale_bar(image, scale, style, pixel_ratio=annotation_ratio(ratio))
            if notice:
                raise ValueError(f"{pane.recipe.axis}: {notice}")
    return np.asarray(image.convert("RGB")), label


def rgb(colour: str) -> tuple[int, int, int]:
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)


def draw_lines(frame: np.ndarray, request: MovieRequest, cross: tuple[int, int, int]) -> None:
    """Dashed intersection lines, at least 2 px wide, on even frame coordinates (4:2:0 keeps their colour)."""
    assert request.volume.grid is not None
    shape = request.volume.grid.shape_xyz
    width = even(max(2., request.geometry.ratio))
    dash, gap = 4 * width, 2 * width
    for pane in request.panes:
        axis = pane.recipe.axis
        x, y, w, h = pane.raster.image
        x0, y0, x1, y1 = pane.recipe.region
        u, v = intersection_point(axis, cross, shape)
        vertical, horizontal = LINE_PLANES[axis]
        if x0 <= u < x1:
            left = (round(x + (u - x0) * w / (x1 - x0)) - width // 2) // 2 * 2
            left = min(max(left, x), x + w - width)
            for top in range(y - y % 2, y + h, dash + gap):
                frame[max(top, y):min(top + dash, y + h), left:left + width] = rgb(PLANE_COLOURS[vertical])
        if y0 <= v < y1:
            top = (round(y + (v - y0) * h / (y1 - y0)) - width // 2) // 2 * 2
            top = min(max(top, y), y + h - width)
            for left in range(x - x % 2, x + w, dash + gap):
                frame[top:top + width, max(left, x):min(left + dash, x + w)] = rgb(PLANE_COLOURS[horizontal])


def draw_label(draw: ImageDraw.ImageDraw, origin: tuple[int, int], text: str, colour: str, size: int,
               pad: int, *, bold: bool = False) -> int:
    """Text on a black box, as the viewer draws its pane tag. Returns the box height."""
    font = export_font(size, bold=bold)
    left, top, right, bottom = font.getbbox(text)
    box = (origin[0], origin[1], origin[0] + right - left + 2 * pad, origin[1] + bottom - top + 2 * pad)
    draw.rectangle(box, fill=(0, 0, 0))
    draw.text((origin[0] + pad - left, origin[1] + pad - top), text, font=font, fill=rgb(colour))
    return int(round(box[3] - box[1]))


def compose(request: MovieRequest, images: dict[str, np.ndarray], plane: int) -> np.ndarray:
    """One encoder frame: black padding, the pane images and the annotations for this sweep plane."""
    geometry, labels, volume = request.geometry, request.labels, request.volume
    assert volume.grid is not None
    width, height = geometry.frame
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    for pane in request.panes:
        x, y, w, h = pane.raster.image
        frame[y:y + h, x:x + w] = images[pane.recipe.axis][:h, :w]
    sweep_index = "XYZ".index(NORMALS[request.sweep])
    cross = list(request.cross_xyz or tuple(n // 2 for n in volume.grid.shape_xyz))
    cross[sweep_index] = plane
    if request.layout == "Ortho" and labels.lines:
        draw_lines(frame, request, (cross[0], cross[1], cross[2]))
    if not (labels.names or labels.numbers or labels.title):
        return frame
    ratio = annotation_ratio(geometry.ratio)
    image = Image.fromarray(frame)
    draw = ImageDraw.Draw(image)
    size, pad, inset = text_px(LABEL_PX, ratio), max(2, round(4 * ratio)), max(2, round(6 * ratio))
    if request.layout == "Ortho":
        if labels.names:
            for pane in request.panes:
                x, y = pane.raster.pane[:2]
                draw_label(draw, (x + inset, y + inset), pane.recipe.axis, PLANE_COLOURS[pane.recipe.axis], size, pad)
        if labels.numbers:
            panes = {pane.recipe.axis: pane.raster.pane for pane in request.panes}
            left, top = panes["YZ"][0] + inset, panes["XZ"][1] + inset
            for plane_axis, index in CORNER_ROWS:
                text = f"{'XYZ'[index]} {cross[index] + 1} / {volume.grid.shape_xyz[index]}"
                top += draw_label(draw, (left, top), text, PLANE_COLOURS[plane_axis], size, pad,
                                  bold=index == sweep_index)
    else:
        pane = request.sweep_pane
        text = plane_label(request.sweep, plane, plane_geometry(volume, request.sweep)[2], oblique=request.oblique,
                           names=labels.names, numbers=labels.numbers)
        if text:
            x, y = pane.raster.image[:2]
            draw_label(draw, (x + inset, y + inset), text, PLANE_COLOURS[request.sweep], size, pad)
    if labels.title and geometry.title is not None:
        x, y, w, h = geometry.title
        font = export_font(min(max(2, h - 2 * pad), text_px(TITLE_PX, ratio)))
        left, top, right, bottom = font.getbbox(labels.title)
        draw.text((x + (w - (right - left)) // 2 - left, y + (h - (bottom - top)) // 2 - top), labels.title,
                  font=font, fill=rgb(TEXT_COLOUR))
    return np.asarray(image)
