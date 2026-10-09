"""Overlays report components."""
from __future__ import annotations
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Sequence
from reportlab.lib import colors
from reportlab.platypus import Flowable
from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.presentation.design_tokens import LIGHT_PALETTE
from tomography_session_browser.reports import styles
from tomography_session_browser.presentation.marker_palette import FRAME_HALO, MarkerStyle, marker_style
from tomography_session_browser.services.atlas_marker_style import PRINT_STATUS_COLOURS


LOGGER = logging.getLogger("tomography_session_browser.reports.graphics")


MARK_LINES: tuple[tuple[float, float, float, float, bool], ...] = (
    # (x1, x2, y, opacity, is_centre)
    (7.22, 14.78, 3.0, 0.55, False),
    (5.15, 16.85, 6.0, 0.75, False),
    (3.44, 18.56, 9.0, 0.9, False),
    (2.0, 20.0, 11.6, 1.0, True),
    (3.44, 18.56, 14.2, 0.9, False),
    (5.15, 16.85, 17.2, 0.7, False),
    (7.22, 14.78, 20.0, 0.5, False),
)


PRINT_DASHES: dict[str, list[float]] = {"solid": [], "dash": [3, 2], "dot": [0.6, 1.4]}


PRINT_FRAME = "#334155"


def print_style(marker: ImageMarker) -> MarkerStyle:
    """The viewer's style for ``marker``, with the neutral frames for paper."""

    style = marker_style(
        marker,
        # Print is the light theme: its queued blue, and its text for a
        # marker type with no colour of its own.
        queued_stroke=PRINT_STATUS_COLOURS["queued"],
        fallback_stroke=LIGHT_PALETTE.text,
    )
    if style.halo:
        return replace(style, stroke=PRINT_FRAME, halo=False)
    return style


def marker_stroke(marker: ImageMarker) -> tuple[str, str]:
    """``(hex colour, reason)`` an overlay marker is stroked in.

    The viewer's rule (``presentation.marker_palette.marker_style``), so a marker
    is the colour it was on screen (harmonisation F1): targets (batch
    positions, tilt series, exposure areas) by their status, everything else
    by its type. The neutral frames (search map, overview) are the one
    exception, a dark slate on paper (``PRINT_FRAME``). ``reason`` is
    ``"type"`` or the state that set the colour (``"failed"``, ``"queued"``,
    ``"complete"``…). The one rule both the drawing and the overlay key use,
    so the key cannot name a colour the figure does not draw.
    """

    style = print_style(marker)
    return style.stroke, style.reason


def native_image_size(path: Path) -> tuple[int, int] | None:
    """Pixel size of a jpg/png via Pillow, or ``None`` if it cannot be read."""

    try:
        from PIL import Image as PILImage

        with PILImage.open(path) as im:
            return im.size
    except Exception as exc:  # pragma: no cover - defensive
        LOGGER.debug("Could not read native size for %s: %s", path, exc)
        return None


class OverlayedImage(Flowable):
    """Embed an image, preserve its aspect ratio, and draw markers on top.

    ``markers`` are :class:`ImageMarker` instances in *image-pixel*
    coordinates (the space :mod:`services.markers` produces). They
    are scaled by exactly the same factor as the image so they line up.

    If the image fails to load the flowable still occupies its advertised
    size and draws an "Image unavailable" placeholder so layout stays stable.
    """

    def __init__(
        self,
        image_path: Path,
        *,
        native_size: tuple[int, int],
        markers: Sequence[ImageMarker] = (),
        max_width: float,
        max_height: float,
        caption: str | None = None,
    ) -> None:
        super().__init__()
        self.image_path = Path(image_path)
        self.native_size = native_size
        self.markers = list(markers)
        self.max_width = max_width
        self.max_height = max_height
        self.caption = caption
        self._draw_w = 0.0
        self._draw_h = 0.0
        self._caption_h = 12.0 if caption else 0.0
        self.hAlign = "LEFT"

    def wrap(self, _avail_w, _avail_h):  # noqa: N802
        nw, nh = self.native_size
        if nw <= 0 or nh <= 0:
            self._draw_w = self.max_width
            self._draw_h = min(self.max_height, self.max_width)
            return self._draw_w, self._draw_h + self._caption_h
        scale = min(self.max_width / nw, self.max_height / nh)
        self._draw_w = nw * scale
        self._draw_h = nh * scale
        return self._draw_w, self._draw_h + self._caption_h

    def draw(self) -> None:
        c = self.canv
        c.saveState()

        # Caption sits *below* the image: y=0 is the bottom of the image.
        c.translate(0, self._caption_h)
        try:
            c.drawImage(
                str(self.image_path),
                0,
                0,
                width=self._draw_w,
                height=self._draw_h,
                preserveAspectRatio=True,
                anchor="sw",
                mask="auto",
            )
        except Exception as exc:
            LOGGER.warning("Could not embed image %s: %s", self.image_path, exc)
            c.setFillColor(styles.SURFACE_ALT)
            c.setStrokeColor(styles.BORDER_STRONG)
            c.rect(0, 0, self._draw_w, self._draw_h, stroke=1, fill=1)
            c.setFillColor(styles.TEXT_MUTED)
            c.setFont(styles.SANS_ITALIC, 8)
            c.drawCentredString(self._draw_w / 2, self._draw_h / 2, "Image unavailable")

        # A hairline frame keeps the image distinct from the page.
        c.setStrokeColor(styles.BORDER_STRONG)
        c.setLineWidth(0.5)
        c.rect(0, 0, self._draw_w, self._draw_h, stroke=1, fill=0)

        nw, nh = self.native_size
        if nw > 0 and nh > 0:
            scale_x = self._draw_w / nw
            scale_y = self._draw_h / nh
            # Clip overlays to the image: Tomo5 templates can extend past the
            # search-map edge and would otherwise bleed onto nearby text.
            c.saveState()
            clip = c.beginPath()
            clip.rect(0, 0, self._draw_w, self._draw_h)
            c.clipPath(clip, stroke=0, fill=0)
            self._draw_markers(scale_x, scale_y)
            c.restoreState()

        c.restoreState()

        if self.caption:
            c.setFillColor(styles.TEXT_MUTED)
            c.setFont(styles.SANS_ITALIC, 7.5)
            c.drawString(0, 2, self.caption[:200])

    def _draw_markers(self, scale_x: float, scale_y: float) -> None:
        ordered = [
            marker for marker in self.markers if marker.marker_type != MarkerType.BATCH_LABEL
        ] + [
            marker for marker in self.markers if marker.marker_type == MarkerType.BATCH_LABEL
        ]
        for marker in ordered:
            if not marker.visible:
                continue
            if marker.marker_type == MarkerType.BATCH_LABEL:
                self._draw_batch_label(marker, scale_x, scale_y)
                continue
            style = print_style(marker)
            # Markers approximated from a failed tilt's stage metadata are
            # dashed so they read apart from recorded acquisitions.
            inferred = bool(
                marker.metadata
                and marker.metadata.get("inferred_from_stage")
            )
            dash = [3, 2] if inferred else PRINT_DASHES.get(style.line, [])
            if style.halo:
                # The neutral frames' dark halo, as on screen.
                self._stroke_marker(marker, scale_x, scale_y, FRAME_HALO, 1.9, dash, alpha=0.55)
            self._stroke_marker(marker, scale_x, scale_y, style.stroke, 1.0 if inferred else 0.7, dash)

    def _stroke_marker(
        self,
        marker: ImageMarker,
        scale_x: float,
        scale_y: float,
        colour: str,
        width: float,
        dash: list[float],
        *,
        alpha: float = 1.0,
    ) -> None:
        c = self.canv
        c.saveState()
        try:
            c.setStrokeColor(colors.HexColor(colour), alpha=alpha)
            c.setLineWidth(width)
            c.setDash(dash) if dash else c.setDash()

            if marker.bbox is not None:
                x, y, w, h = marker.bbox
                rx = x * scale_x
                # Image space y goes down; PDF y goes up. Flip.
                ry = self._draw_h - (y + h) * scale_y
                c.rect(rx, ry, w * scale_x, h * scale_y, stroke=1, fill=0)
            elif marker.polygon:
                path = c.beginPath()
                first = True
                for px, py in marker.polygon:
                    qx = px * scale_x
                    qy = self._draw_h - py * scale_y
                    if first:
                        path.moveTo(qx, qy)
                        first = False
                    else:
                        path.lineTo(qx, qy)
                path.close()
                c.drawPath(path, stroke=1, fill=0)
            elif marker.x is not None and marker.y is not None:
                cx = marker.x * scale_x
                cy = self._draw_h - marker.y * scale_y
                radius = (marker.radius or 8) * scale_x
                c.circle(cx, cy, max(radius, 1.0), stroke=1, fill=0)
        finally:
            c.restoreState()

    def _draw_batch_label(self, marker: ImageMarker, scale_x: float, scale_y: float) -> None:
        if not marker.label:
            return

        c = self.canv
        text = marker.label
        font = "Helvetica-Bold"
        font_size = 10.5
        text_w = c.stringWidth(text, font, font_size)
        text_h = font_size * 1.05
        rx, top_y = batch_label_pdf_origin(
            marker,
            scale_x=scale_x,
            scale_y=scale_y,
            label_size=(text_w, text_h),
            image_size=(self._draw_w, self._draw_h),
        )
        baseline = self._draw_h - top_y - text_h + font_size * 0.12

        role = str(marker.metadata.get("label_colour_role") or "exposure")
        text_color = colors.HexColor("#1d4ed8" if role == "camera" else "#065f46")
        c.saveState()
        c.setFont(font, font_size)
        c.setFillColor(colors.Color(0.008, 0.023, 0.09, alpha=0.38))
        c.drawString(rx + 0.8, baseline - 0.8, text)
        c.setFillColor(colors.Color(0.972, 0.98, 0.988, alpha=0.72))
        for dx, dy in (
            (-0.75, 0),
            (0.75, 0),
            (0, -0.75),
            (0, 0.75),
            (-0.55, -0.55),
            (-0.55, 0.55),
            (0.55, -0.55),
            (0.55, 0.55),
        ):
            c.drawString(rx + dx, baseline + dy, text)
        c.setFillColor(text_color)
        c.drawString(rx, baseline, text)
        c.restoreState()


def batch_label_pdf_origin(
    marker: ImageMarker,
    *,
    scale_x: float,
    scale_y: float,
    label_size: tuple[float, float],
    image_size: tuple[float, float],
) -> tuple[float, float]:
    label_w, label_h = label_size
    image_w, image_h = image_size
    x = marker.x
    y = marker.y
    if (x is None or y is None) and marker.bbox is not None:
        x, y = marker.bbox[0], marker.bbox[1]
    if x is None or y is None:
        return 0.0, 0.0
    rx = float(x) * scale_x
    top_y = float(y) * scale_y

    return (
        min(max(0.0, rx), max(0.0, image_w - label_w)),
        min(max(0.0, top_y), max(0.0, image_h - label_h)),
    )


@dataclass(frozen=True, slots=True)
class OverlayKeyItem:
    label: str
    colour: str  # hex
    dashed: bool = False
    line: str = "solid"


KEYED_MARKER_TYPES: dict[str, str] = {
    MarkerType.EXPOSURE_AREA: "exposure area",
    MarkerType.CAMERA_FOV: "camera field",
    MarkerType.BATCH_POSITION: "batch position",
    MarkerType.TRACKING_AREA: "tracking area",
    MarkerType.FOCUS_AREA: "focus area",
    MarkerType.SEARCH_MAP: "search map",
    MarkerType.OVERVIEW: "overview",
    MarkerType.TILT_SERIES: "tilt series",
}


KEY_TYPE_ORDER = {marker_type: index for index, marker_type in enumerate(KEYED_MARKER_TYPES)}


KEY_REASON_ORDER = {
    "type": 0,
    "complete": 1,
    "incomplete": 2,
    "pending": 3,
    "failed": 4,
    "missing": 5,
    "queued": 6,
    "unresolved": 7,
}


STATUS_REASONS = frozenset({"complete", "incomplete", "pending", "missing"})


def overlay_key_items(markers: Iterable[ImageMarker]) -> list[OverlayKeyItem]:
    """The key entries for the overlays actually drawn in ``markers``.

    Entries are grouped by the outline actually drawn (:func:`marker_stroke`
    and the dashed stage-estimate style), so two marker types that share a
    colour share an entry ("Batch position / tracking area") and a status
    that overrides a type's colour is keyed by the status.
    """

    groups: dict[tuple[str, bool, str, str], dict[str, int]] = {}
    ranks: dict[tuple[str, bool, str, str], tuple[int, int]] = {}
    for marker in markers:
        if not marker.visible or marker.metadata.get("atlas_lod_role"):
            continue
        type_label = KEYED_MARKER_TYPES.get(marker.marker_type)
        if type_label is None:
            continue
        colour, reason = marker_stroke(marker)
        dashed = bool(marker.metadata.get("inferred_from_stage"))
        line = print_style(marker).line
        if reason == "queued":
            text = "queued (not acquired)"
        elif reason == "failed":
            text = "failed, approximated from stage metadata" if dashed else "failed"
        elif reason == "unresolved":
            text = "unresolved link"
        elif reason in STATUS_REASONS:
            text = f"{type_label}, {reason}"
            if dashed:
                text += ", approximated from stage metadata"
        else:
            text = f"{type_label}, approximated from stage metadata" if dashed else type_label
        key = (colour, dashed, "" if reason == "type" else reason, line)
        texts = groups.setdefault(key, {})
        type_rank = KEY_TYPE_ORDER[marker.marker_type]
        texts[text] = min(texts.get(text, type_rank), type_rank)
        rank = (KEY_REASON_ORDER[reason], type_rank)
        ranks[key] = min(ranks.get(key, rank), rank)
    items = []
    for key in sorted(groups, key=lambda value: ranks[value]):
        # Names in key order, whatever order the figures drew them in.
        label = " / ".join(sorted(groups[key], key=groups[key].__getitem__))
        items.append(OverlayKeyItem(label[:1].upper() + label[1:], key[0], dashed=key[1], line=key[3]))
    return items


class OverlayKey(Flowable):
    """A one-line key of overlay rings, wrapping when it runs out of width."""

    RING = 3.4
    SIZE = styles.TYPE_SMALL - 0.4

    def __init__(self, items: Sequence[OverlayKeyItem], *, lead: str | None = None) -> None:
        super().__init__()
        self.items = list(items)
        self.lead = lead
        self._lines: list[list[tuple[str, OverlayKeyItem | None, float]]] = []
        self.width = 0.0
        self.height = 0.0

    def wrap(self, avail_w, _avail_h):  # noqa: N802
        self.width = avail_w
        entries: list[tuple[str, OverlayKeyItem | None, float]] = []
        if self.lead:
            entries.append((self.lead, None, styles.string_width(self.lead, styles.SANS, self.SIZE)))
        for item in self.items:
            entries.append((item.label, item, self.RING * 2 + 3 + styles.string_width(item.label, styles.SANS, self.SIZE)))
        lines: list[list] = [[]]
        x = 0.0
        for entry in entries:
            if lines[-1] and x + entry[2] > avail_w:
                lines.append([])
                x = 0.0
            lines[-1].append(entry)
            x += entry[2] + 10.0
        self._lines = [line for line in lines if line]
        self.height = len(self._lines) * self.SIZE * 1.6
        return self.width, self.height

    def draw(self) -> None:
        c = self.canv
        c.saveState()
        for index, line in enumerate(self._lines):
            baseline = self.height - (index + 1) * self.SIZE * 1.6 + self.SIZE * 0.45
            x = 0.0
            for text, item, entry_w in line:
                if item is not None:
                    c.setStrokeColor(colors.HexColor(item.colour))
                    c.setLineWidth(0.9)
                    c.setDash(
                        [1.6, 1.2] if item.dashed or item.line == "dash" else [0.5, 1.1] if item.line == "dot" else []
                    )
                    c.circle(x + self.RING, baseline + self.SIZE * 0.32, self.RING, stroke=1, fill=0)
                    c.setDash([])
                    text_x = x + self.RING * 2 + 3
                    c.setFillColor(styles.TEXT)
                else:
                    text_x = x
                    c.setFillColor(styles.TEXT_MUTED)
                c.setFont(styles.SANS, self.SIZE)
                c.drawString(text_x, baseline, text)
                x += entry_w + 10.0
        c.restoreState()

