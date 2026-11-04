"""Calibrated display annotation, shared by the viewer and frozen exports."""
from dataclasses import dataclass
import math
import re
import threading

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# Image annotation presets are independent of the application's chrome theme.
BAR_COLOURS = (("White", "#ffffff"), ("Black", "#000000"))
#: The annotation's soft shadow at 80 % of the blurred coverage.
SHADOW_STRENGTH = [value * 4 // 5 for value in range(256)]
#: Logical pixels per point, and the gap between the bar and its label (3dmod's label Y offset).
POINT = 4 / 3
LABEL_GAP = 2
_FONTS = threading.local()


def label_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """The label font at one pixel size, loaded once per thread.

    Loading searches the system font folders on every call. Render and capture
    threads keep their own copies, since a FreeType face is not thread-safe.
    """
    fonts = getattr(_FONTS, "by_size", None)
    if fonts is None:
        fonts = _FONTS.by_size = {}
    font = fonts.get(size)
    if font is None:
        try:
            font = ImageFont.truetype("arial.ttf", size)
        except OSError:
            font = ImageFont.load_default(size=size)
        fonts[size] = font
    return font


@dataclass(frozen=True, slots=True)
class ScaleBarStyle:
    """Defaults follow 3dmod's Scale Bar dialog (IMOD ``scalebar.cpp``): at least
    50 px long, 8 px thick, lower right, 20 px in from each edge, and a 14 pt
    label 2 px above the bar. 3dmod draws black bars without a label by
    default; here the bar is white with a soft shadow and the label is on, so
    the distance reads on dark and light views and in every snapshot.
    """

    corner: str = "br"
    colour: str = BAR_COLOURS[0][1]
    minimum_width: int = 50
    exact_nm: float | None = None
    thickness: int = 8
    label: bool = True
    text: str = ""
    #: Points, as 3dmod's label Size; 1 pt is 4/3 logical pixels.
    text_size: int = 14
    # Fraction of free space inside the image; None uses the selected corner.
    position: tuple[float, float] | None = None
    #: Logical pixels from the image edges, as 3dmod's indentation in X and Y.
    inset_x: int = 20
    inset_y: int = 20
    label_above: bool = True


def decode_style(data: dict) -> ScaleBarStyle:
    if not isinstance(data, dict):
        raise ValueError("Invalid scale-bar settings")
    # Missing fields in historical recipes retain the original below-bar label.
    style = ScaleBarStyle(**{"label_above": False, **data})
    if style.corner not in ("tl", "tr", "bl", "br") or not isinstance(style.colour, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", style.colour):
        raise ValueError("Invalid scale-bar position or colour")
    for value, low, high in ((style.minimum_width, 30, 300), (style.thickness, 1, 20), (style.text_size, 9, 36),
                             (style.inset_x, 0, 200), (style.inset_y, 0, 200)):
        if type(value) is not int or not low <= value <= high:
            raise ValueError("Invalid scale-bar size")
    if style.exact_nm is not None and (type(style.exact_nm) not in (int, float) or not math.isfinite(style.exact_nm) or not 0 < style.exact_nm <= 1e9):
        raise ValueError("Invalid scale-bar length")
    if type(style.label) is not bool or type(style.label_above) is not bool or not isinstance(style.text, str) or len(style.text) > 80 or any(ord(c) < 32 for c in style.text):
        raise ValueError("Invalid scale-bar label")
    if style.position is not None:
        if not isinstance(style.position, (list, tuple)) or len(style.position) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in style.position):
            raise ValueError("Invalid scale-bar drag position")
        from dataclasses import replace
        style = replace(style, position=tuple(style.position))
    return style


def draw_scale_bar(image: Image.Image, angstrom_per_pixel: float, style: ScaleBarStyle, *, pixel_ratio: float = 1.) -> tuple[Image.Image, str, tuple[int, int, int, int] | None, str]:
    """Return pixels, the physical label, hit bounds and an explained non-fit.

    An exact bar is never shortened to fit. Custom text cannot change the
    physical length/label recorded in provenance. Position includes all text.
    """
    ow, oh = image.size
    inset_x, inset_y = round(style.inset_x * pixel_ratio), round(style.inset_y * pixel_ratio)
    thickness = max(1, round(style.thickness * pixel_ratio))
    stroke = max(1, round(pixel_ratio))
    available = ow - 2 * inset_x
    if available < 1 or oh < 2 * inset_y + thickness:
        return image, "", None, "Scale bar does not fit this view."
    if style.exact_nm is None:
        target = min(style.minimum_width * pixel_ratio, available) * angstrom_per_pixel
        power = 10 ** math.floor(math.log10(target))
        lengths = [step * power for step in (.1, .2, .5, 1, 2, 5, 10)]
        length = next(value for value in lengths if value >= target - 1e-9)
        if length / angstrom_per_pixel > available:
            length = max(value for value in lengths if value / angstrom_per_pixel <= available)
    else:
        length = style.exact_nm * 10
    bar_width = max(1, round(length / angstrom_per_pixel))
    physical_label = f"{length / 10:g} nm"
    text = (style.text or physical_label) if style.label else ""
    font = label_font(round(style.text_size * POINT * pixel_ratio))
    bounds = font.getbbox(text, stroke_width=stroke) if text else (0, 0, 0, 0)
    tw, th = math.ceil(bounds[2] - bounds[0]), math.ceil(bounds[3] - bounds[1])
    gap = round(LABEL_GAP * pixel_ratio)
    width, height = max(bar_width, tw), thickness + (gap + th if text else 0)
    if width > available or height > oh - 2 * inset_y:
        return image, "", None, "Scale bar or label does not fit. Reduce its length or text size, or zoom out."
    px, py = style.position or (float(style.corner.endswith("r")), float(style.corner.startswith("b")))
    x, y = inset_x + round(px * (available - width)), inset_y + round(py * (oh - 2 * inset_y - height))
    rgb = tuple(int(style.colour[i:i+2], 16) for i in (1, 3, 5))
    colour = rgb[0] if len(set(rgb)) == 1 else rgb
    if isinstance(colour, tuple):
        image = image.convert("RGB")
    outline = 0 if sum(rgb) > 382 else 255
    if image.mode == "RGB":
        outline = (outline,) * 3
    # The label is centred on the bar, under it as in 3dmod or above it.
    top = y + th + gap if text and style.label_above else y
    left = x + (width - bar_width) // 2
    bar = (left, top, left + bar_width - 1, top + thickness - 1)
    origin = (x + (width - tw) // 2 - bounds[0], (y if style.label_above else top + thickness + gap) - bounds[1])
    # A soft shadow in the contrasting tone keeps bar and label legible on any
    # background without a hard outline. It is drawn only around the annotation.
    pad = round(3 * pixel_ratio)
    box = (max(0, x - pad), max(0, y - pad), min(ow, x + width + pad), min(oh, y + height + pad))
    shadow = Image.new("L", (box[2] - box[0], box[3] - box[1]), 0)
    shade = ImageDraw.Draw(shadow)
    shade.rectangle((bar[0] - box[0], bar[1] - box[1], bar[2] - box[0], bar[3] - box[1]), fill=255)
    if text:
        shade.text((origin[0] - box[0], origin[1] - box[1]), text, font=font, fill=255, stroke_width=stroke)
    shadow = shadow.filter(ImageFilter.GaussianBlur(max(1., pixel_ratio))).point(SHADOW_STRENGTH)
    image.paste(outline, box, shadow)
    draw = ImageDraw.Draw(image)
    draw.rectangle(bar, fill=colour)
    if text:
        draw.text(origin, text, font=font, fill=colour)
    return image, physical_label, (x, y, width, height), ""
