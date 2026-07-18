"""Custom reportlab Flowables for the PDF session report.

The on-screen Session dashboard uses count cards, status-tile waffles,
donut charts and segmented status bars to communicate the same numbers
that would otherwise be a wall of text. The PDF uses the same vocabulary
so the printed report and the GUI read consistently.

Each widget here is a self-contained ``Flowable``: it advertises its own
size via ``wrap`` and paints itself in ``draw``. They never reach into
domain models — the generator passes already-shaped data
(``StatusSegment`` lists, count tuples, ``ImageMarker`` lists) so the
Flowables are easy to reuse in different sections of the report.

Drawing decisions worth noting:

* Status colours always come from :mod:`reports.styles` so a printed
  greyscale PDF still reads from the labels rather than depending on
  hue.
* The status tile grid mirrors :class:`StatusTileGrid` from the GUI: it
  renders one rounded square per item, but if the count blows past the
  configured threshold it switches to a grouped status-strip so the
  reader sees a meaningful total instead of unreadable specks.
* Image flowables preserve aspect ratio. Overlays (markers projected
  into image-pixel coordinates by ``services.marker_service``) are
  drawn with the *same* scale as the image, so a 3x rendered image
  shows 3x markers and they line up exactly.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import Flowable, KeepTogether, Paragraph, Spacer, Table

from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.reports import styles

LOGGER = logging.getLogger(__name__)


# =============================================================================
# Count / status cards
# =============================================================================


@dataclass(slots=True)
class CountCardData:
    label: str
    value: int
    sub_label: str | None = None  # e.g. "complete / failed / unknown"
    accent: str = "session"  # status_color name


class CountCard(Flowable):
    """A small rectangular card with a coloured accent stripe.

    Width is set by the caller; height adapts to the chosen text sizes.
    The cards are designed to live inside a :func:`card_row` table so a
    horizontal row of cards stays aligned.
    """

    def __init__(
        self,
        data: CountCardData,
        *,
        width: float,
        height: float = 0.85 * inch,
    ) -> None:
        super().__init__()
        self.data = data
        self.width = width
        self.height = height

    def wrap(self, _avail_w, _avail_h):  # noqa: N802 — Qt/reportlab lower-snake
        return self.width, self.height

    def draw(self) -> None:
        accent = styles.status_color(self.data.accent)
        c = self.canv

        # Card background + border.
        c.setFillColor(colors.HexColor("#ffffff"))
        c.setStrokeColor(colors.HexColor("#e2e8f0"))
        c.setLineWidth(0.5)
        c.roundRect(0, 0, self.width, self.height, 4, stroke=1, fill=1)

        # Accent stripe down the left edge.
        c.setFillColor(accent.fill)
        c.setStrokeColor(accent.fill)
        c.roundRect(0, 0, 4, self.height, 2, stroke=0, fill=1)

        # Big number.
        value_text = str(self.data.value)
        c.setFillColor(colors.HexColor("#0f172a"))
        c.setFont("Helvetica-Bold", 18)
        c.drawString(12, self.height - 22, value_text)

        # Label.
        c.setFillColor(colors.HexColor("#475569"))
        c.setFont("Helvetica", 8.5)
        c.drawString(12, self.height - 36, self.data.label)

        # Sub-label (status breakdown, e.g. "27 / 3 / 0").
        if self.data.sub_label:
            c.setFillColor(colors.HexColor("#64748b"))
            c.setFont("Helvetica-Oblique", 7.5)
            # Wrap sub-label in case it's longer than the card.
            text = self.data.sub_label
            max_w = self.width - 16
            if c.stringWidth(text, "Helvetica-Oblique", 7.5) > max_w:
                # Truncate with an ellipsis rather than overflowing.
                while text and c.stringWidth(text + "…", "Helvetica-Oblique", 7.5) > max_w:
                    text = text[:-1]
                text = text + "…"
            c.drawString(12, 6, text)


def card_row(cards: Sequence[CountCard], *, total_width: float, gap: float = 6) -> Table:
    """Lay out ``cards`` evenly across ``total_width`` in a single row."""

    if not cards:
        return Table([[Paragraph("", styles.SMALL_STYLE)]], colWidths=[total_width])
    n = len(cards)
    each = (total_width - gap * (n - 1)) / n
    # Each card knows its own width — but the table still needs colWidths
    # that match. Resize the cards in place so the table arithmetic agrees
    # with what they paint.
    for card in cards:
        card.width = each
    table = Table([list(cards)], colWidths=[each] * n, hAlign="LEFT")
    table.setStyle(
        [
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), gap),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]
    )
    return table


# =============================================================================
# Status tile waffle grid
# =============================================================================


@dataclass(slots=True)
class TileItem:
    name: str
    status: str  # "complete" | "incomplete" | "failed" | "unknown" | "neutral"
    tooltip: str = ""  # not rendered in the PDF, kept for parity with GUI model


class StatusTileGrid(Flowable):
    """Waffle of rounded coloured squares, one per item.

    The width is fixed by the caller; the height follows from the row
    count needed to fit ``items`` at ``tile_size``. If the chosen
    ``tile_size`` would produce more than ``max_rows`` rows, the tiles
    shrink down to ``min_tile`` and below that the grid switches to a
    grouped status strip so the output stays legible.
    """

    def __init__(
        self,
        items: Sequence[TileItem],
        *,
        width: float,
        tile_size: float = 8.5,
        gap: float = 1.5,
        max_rows: int = 6,
        min_tile: float = 4.5,
        threshold_for_strip: int = 60,
    ) -> None:
        super().__init__()
        self.items = list(items)
        self.width = width
        self.tile_size = tile_size
        self.gap = gap
        self.max_rows = max_rows
        self.min_tile = min_tile
        self.threshold_for_strip = threshold_for_strip
        self._draw_height = 0.0
        self._chosen_tile = tile_size
        self._cols = 0
        self._rows = 0
        self._strip_mode = False

    def _compute(self) -> None:
        if not self.items:
            self._draw_height = 0
            return

        # If we have more items than a sensible waffle can show, switch
        # to the grouped-status strip rendering instead.
        if len(self.items) > self.threshold_for_strip:
            self._strip_mode = True
            self._draw_height = 12
            return

        size = self.tile_size
        # Shrink tiles until the grid fits in ``max_rows``.
        while size >= self.min_tile:
            cols = max(1, int((self.width + self.gap) // (size + self.gap)))
            rows = math.ceil(len(self.items) / cols)
            if rows <= self.max_rows:
                self._chosen_tile = size
                self._cols = cols
                self._rows = rows
                self._draw_height = rows * (size + self.gap) - self.gap
                return
            size -= 0.5

        # Couldn't fit at min_tile → strip mode.
        self._strip_mode = True
        self._draw_height = 12

    def wrap(self, _avail_w, _avail_h):  # noqa: N802
        self._compute()
        return self.width, max(self._draw_height, 1)

    def draw(self) -> None:
        if not self.items:
            return
        if self._strip_mode:
            self._draw_strip()
            return

        c = self.canv
        c.saveState()
        size = self._chosen_tile
        for index, item in enumerate(self.items):
            col = index % self._cols
            row = index // self._cols
            x = col * (size + self.gap)
            # Top-down ordering so the first item is in the top-left.
            y = self._draw_height - (row + 1) * (size + self.gap) + self.gap
            color = styles.status_color(item.status).fill
            c.setFillColor(color)
            c.setStrokeColor(color)
            c.roundRect(x, y, size, size, max(1.0, size / 4), stroke=0, fill=1)
        c.restoreState()

    def _draw_strip(self) -> None:
        """Grouped-status strip used when there are too many items.

        Renders a 12-pt-high stacked horizontal bar segmented by status,
        with little count labels under each segment. This keeps the
        cover-page card useful for a session with hundreds of items.
        """

        from collections import Counter

        counts = Counter(item.status for item in self.items)
        order = ["complete", "incomplete", "failed", "unknown", "neutral"]
        active = [(s, counts[s]) for s in order if counts.get(s, 0)]
        total = sum(c for _, c in active)
        if total == 0:
            return

        c = self.canv
        c.saveState()
        x = 0.0
        bar_h = 8.0
        for status, count in active:
            seg_w = self.width * (count / total)
            color = styles.status_color(status).fill
            c.setFillColor(color)
            c.setStrokeColor(color)
            c.rect(x, self._draw_height - bar_h, seg_w, bar_h, stroke=0, fill=1)
            x += seg_w
        c.restoreState()


# =============================================================================
# Donut chart
# =============================================================================


@dataclass(slots=True)
class StatusSegment:
    label: str
    value: int
    status: str  # status_color name


class DonutChart(Flowable):
    """Status donut with a centre label.

    Echoes the GUI's ``DonutChart`` widget. Uses ``canvas.wedge`` so the
    chart works on any reportlab canvas without depending on the more
    elaborate chart machinery.
    """

    def __init__(
        self,
        segments: Sequence[StatusSegment],
        *,
        diameter: float = 1.4 * inch,
        center_label: str | None = None,
        center_caption: str | None = None,
    ) -> None:
        super().__init__()
        self.segments = list(segments)
        self.diameter = diameter
        self.center_label = center_label
        self.center_caption = center_caption

    def wrap(self, _avail_w, _avail_h):  # noqa: N802
        return self.diameter, self.diameter

    def draw(self) -> None:
        c = self.canv
        d = self.diameter
        cx, cy = d / 2, d / 2
        outer_r = d / 2
        inner_r = outer_r * 0.62
        total = sum(s.value for s in self.segments)

        # Background track.
        c.setFillColor(colors.HexColor("#e5e7eb"))
        c.setStrokeColor(colors.HexColor("#e5e7eb"))
        c.circle(cx, cy, outer_r, stroke=0, fill=1)

        # Segments — drawn as wedges, then overlaid by an inner circle to
        # make the donut hole.
        if total > 0:
            start = 90.0  # 12 o'clock
            for seg in self.segments:
                if seg.value <= 0:
                    continue
                extent = -360.0 * (seg.value / total)
                color = styles.status_color(seg.status).fill
                c.setFillColor(color)
                c.setStrokeColor(color)
                c.wedge(
                    cx - outer_r,
                    cy - outer_r,
                    cx + outer_r,
                    cy + outer_r,
                    start,
                    extent,
                    stroke=0,
                    fill=1,
                )
                start += extent

        # Donut hole.
        c.setFillColor(colors.HexColor("#ffffff"))
        c.setStrokeColor(colors.HexColor("#ffffff"))
        c.circle(cx, cy, inner_r, stroke=0, fill=1)

        # Centre label.
        if self.center_label:
            c.setFillColor(colors.HexColor("#0f172a"))
            c.setFont("Helvetica-Bold", 14)
            text_w = c.stringWidth(self.center_label, "Helvetica-Bold", 14)
            c.drawString(cx - text_w / 2, cy - 2, self.center_label)
        if self.center_caption:
            c.setFillColor(colors.HexColor("#64748b"))
            c.setFont("Helvetica", 7.5)
            text_w = c.stringWidth(self.center_caption, "Helvetica", 7.5)
            c.drawString(cx - text_w / 2, cy - 14, self.center_caption)


def donut_legend(segments: Sequence[StatusSegment]) -> Table:
    """A compact key paired with a :class:`DonutChart`."""

    rows = []
    for seg in segments:
        color = styles.status_color(seg.status)
        swatch = _ColorSwatch(color.fill, size=8)
        rows.append([swatch, Paragraph(f"<b>{seg.value}</b>  {seg.label}", styles.SMALL_STYLE)])
    table = Table(rows, colWidths=[12, None], hAlign="LEFT")
    table.setStyle(
        [
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 1),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]
    )
    return table


class _ColorSwatch(Flowable):
    """Small filled square used in legends."""

    def __init__(self, color, *, size: float = 8) -> None:
        super().__init__()
        self._color = color
        self._size = size

    def wrap(self, _avail_w, _avail_h):  # noqa: N802
        return self._size, self._size

    def draw(self) -> None:
        c = self.canv
        c.setFillColor(self._color)
        c.setStrokeColor(self._color)
        c.roundRect(0, 0, self._size, self._size, 1.5, stroke=0, fill=1)


# =============================================================================
# Stacked status bar (used for warning severity strip)
# =============================================================================


class StackedStatusBar(Flowable):
    """Horizontal stacked bar — one segment per status."""

    def __init__(
        self,
        segments: Sequence[StatusSegment],
        *,
        width: float,
        height: float = 10,
    ) -> None:
        super().__init__()
        self.segments = list(segments)
        self.width = width
        self.height = height

    def wrap(self, _avail_w, _avail_h):  # noqa: N802
        return self.width, self.height

    def draw(self) -> None:
        c = self.canv
        total = sum(max(s.value, 0) for s in self.segments)
        if total == 0:
            c.setFillColor(colors.HexColor("#e2e8f0"))
            c.setStrokeColor(colors.HexColor("#e2e8f0"))
            c.roundRect(0, 0, self.width, self.height, self.height / 2, stroke=0, fill=1)
            return

        x = 0.0
        for seg in self.segments:
            if seg.value <= 0:
                continue
            seg_w = self.width * (seg.value / total)
            color = styles.status_color(seg.status).fill
            c.setFillColor(color)
            c.setStrokeColor(color)
            c.rect(x, 0, seg_w, self.height, stroke=0, fill=1)
            x += seg_w


# =============================================================================
# Status badge (used inside the tilt-series table)
# =============================================================================


class StatusBadge(Flowable):
    """Small pill with a label and a status colour.

    Used as a table cell inside the tilt-series table. Renders the
    label in white-on-colour so it survives a greyscale print as a
    dark filled pill.
    """

    def __init__(
        self,
        label: str,
        status: str,
        *,
        width: float = 0.6 * inch,
        height: float = 12,
    ) -> None:
        super().__init__()
        self.label = label
        self.status = status
        self.width = width
        self.height = height

    def wrap(self, _avail_w, _avail_h):  # noqa: N802
        return self.width, self.height

    def draw(self) -> None:
        c = self.canv
        color = styles.status_color(self.status)
        c.setFillColor(color.bg)
        c.setStrokeColor(color.fill)
        c.setLineWidth(0.5)
        c.roundRect(0, 0, self.width, self.height, self.height / 2, stroke=1, fill=1)
        c.setFillColor(color.text)
        c.setFont("Helvetica-Bold", 7.5)
        text_w = c.stringWidth(self.label, "Helvetica-Bold", 7.5)
        c.drawString((self.width - text_w) / 2, (self.height - 6) / 2, self.label)


# =============================================================================
# Search-map completion strip
# =============================================================================


class CompletionStrip(Flowable):
    """Narrow horizontal progress bar for one search map.

    Renders three layers:

    1. A pill-shaped grey track spanning the full width.
    2. A flat green "acquired" segment whose width is proportional to
       ``acquired / planned``. Drawn as a plain rectangle (not a
       ``roundRect``) so very low fractions render correctly — the
       previous rounded fill collapsed into a malformed circle when
       its width was less than the corner radius (e.g. 1 of 60 tiles
       acquired produced a malformed dot).
    3. A flat red "failed" segment anchored to the right edge.

    The two coloured segments are clipped against the track so the
    rounded ends of the track stay clean even when the fill itself is
    drawn flat.
    """

    def __init__(
        self,
        *,
        acquired: int,
        planned: int,
        failed: int,
        width: float,
        height: float = 8,
    ) -> None:
        super().__init__()
        self.acquired = max(0, acquired)
        self.planned = max(self.acquired, planned)
        self.failed = max(0, failed)
        self.width = width
        self.height = height

    def wrap(self, _avail_w, _avail_h):  # noqa: N802
        return self.width, self.height

    def draw(self) -> None:
        c = self.canv
        c.saveState()

        # Track — pill shape via roundRect with corner radius == half
        # the height. Drawn first so the coloured fills sit on top.
        track_color = colors.HexColor("#e2e8f0")
        c.setFillColor(track_color)
        c.setStrokeColor(track_color)
        c.roundRect(0, 0, self.width, self.height, self.height / 2, stroke=0, fill=1)

        if self.planned <= 0:
            c.restoreState()
            return

        # Use the same pill outline as a clipping path so the flat
        # fills inherit the rounded ends without trying to roundRect
        # a tiny segment. PDFPathObject.roundRect takes a single
        # corner radius (vs. canvas.roundRect's separate rx/ry).
        path = c.beginPath()
        path.roundRect(0, 0, self.width, self.height, self.height / 2)
        c.clipPath(path, stroke=0, fill=0)

        # Acquired (left-anchored, flat).
        done_w = max(0.0, self.width * (self.acquired / self.planned))
        if done_w > 0:
            color = styles.status_color("complete").fill
            c.setFillColor(color)
            c.setStrokeColor(color)
            c.rect(0, 0, done_w, self.height, stroke=0, fill=1)

        # Failed (right-anchored, flat). Clipped to the same pill, so
        # it never extends past the right rounded edge.
        if self.failed > 0:
            fail_w = self.width * min(self.failed / self.planned, 1.0)
            color = styles.status_color("failed").fill
            c.setFillColor(color)
            c.setStrokeColor(color)
            c.rect(self.width - fail_w, 0, fail_w, self.height, stroke=0, fill=1)

        c.restoreState()


# =============================================================================
# OverlayedImage — embed a JPG with marker overlays drawn on top
# =============================================================================


# Map MarkerType → status colour key. Stays here so the report can re-use the
# same semantics without dragging in the GUI image_viewer.
_MARKER_COLOR: dict[str, str] = {
    MarkerType.SEARCH_MAP: "session",
    MarkerType.OVERVIEW: "session",
    MarkerType.BATCH_POSITION: "incomplete",
    MarkerType.TILT_SERIES: "complete",
    MarkerType.TEMPLATE_AREA: "session",
    MarkerType.EXPOSURE_AREA: "complete",
    MarkerType.CAMERA_FOV: "session",
    MarkerType.BATCH_LABEL: "complete",
    MarkerType.TRACKING_AREA: "incomplete",
    MarkerType.FOCUS_AREA: "session",
    MarkerType.CONDITION_AREA: "neutral",
    MarkerType.LINK_LINE: "neutral",
    MarkerType.GROUPED: "session",
    MarkerType.STAGE_CROSSHAIR: "neutral",
}


def native_image_size(path: Path) -> tuple[int, int] | None:
    """Try Pillow first, then fall back to ``None`` if anything fails.

    Used for jpg/png. MRC dimensions come from the existing parser; this
    function exists so the generator can ask "do I know how big this
    image actually is" without an explicit Pillow import.
    """

    try:
        from PIL import Image as PILImage

        with PILImage.open(path) as im:
            return im.size
    except Exception as exc:  # pragma: no cover — defensive
        LOGGER.debug("Could not read native size for %s: %s", path, exc)
        return None


class OverlayedImage(Flowable):
    """Embed an image, preserve aspect ratio, and draw markers on top.

    ``markers`` are :class:`ImageMarker` instances in *image-pixel*
    coordinates (the same space :mod:`services.marker_service`
    produces). The flowable scales them by exactly the same factor as
    the embedded image so they line up correctly on the page.

    If the image fails to load the flowable still occupies its
    advertised size and renders a "Image unavailable" placeholder so
    surrounding layout stays stable.
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

        # Caption sits *below* the image — translate so y=0 is the
        # bottom of the image, leaving the caption space underneath.
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
            c.setFillColor(colors.HexColor("#f1f5f9"))
            c.setStrokeColor(colors.HexColor("#cbd5e1"))
            c.rect(0, 0, self._draw_w, self._draw_h, stroke=1, fill=1)
            c.setFillColor(colors.HexColor("#64748b"))
            c.setFont("Helvetica-Oblique", 9)
            c.drawCentredString(
                self._draw_w / 2, self._draw_h / 2, "Image unavailable"
            )

        # Border around the image — a thin neutral outline keeps the
        # image distinct from surrounding text.
        c.setStrokeColor(colors.HexColor("#cbd5e1"))
        c.setLineWidth(0.4)
        c.rect(0, 0, self._draw_w, self._draw_h, stroke=1, fill=0)

        nw, nh = self.native_size
        if nw > 0 and nh > 0:
            scale_x = self._draw_w / nw
            scale_y = self._draw_h / nh
            # Clip overlay drawing to the image rectangle. Tomo5 exports
            # exposure / batch templates whose bounding boxes can extend
            # past the search-map edge (e.g. SearchMap_20260319_234438
            # in Rothia / data collection 2). Without clipping those
            # rectangles bleed onto the surrounding metadata text.
            c.saveState()
            clip = c.beginPath()
            clip.rect(0, 0, self._draw_w, self._draw_h)
            c.clipPath(clip, stroke=0, fill=0)
            self._draw_markers(scale_x, scale_y)
            c.restoreState()

        c.restoreState()

        if self.caption:
            c.setFillColor(colors.HexColor("#475569"))
            c.setFont("Helvetica-Oblique", 7.5)
            c.drawString(0, 2, self.caption[:200])

    def _draw_markers(self, scale_x: float, scale_y: float) -> None:
        c = self.canv
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
            # Failed tilt series propagate their status onto every
            # marker spawned from the same batch (template, exposure,
            # tracking, focus, batch dot). Honouring the status here
            # keeps the GUI and the PDF reading the same — failed
            # acquisitions are visually distinct from successful ones.
            if (marker.status or "").lower() == "failed":
                color = styles.status_color("failed")
            else:
                color = styles.status_color(
                    _MARKER_COLOR.get(marker.marker_type, "neutral")
                )
            c.setStrokeColor(color.fill)
            c.setLineWidth(0.7)
            # Markers approximated from a failed tilt's stage
            # metadata are drawn with a dashed stroke so the user
            # can tell them apart from recorded acquisitions. The
            # dash pattern is reset for the next marker via the
            # save/restore wrapping the whole draw_markers call.
            inferred = bool(
                marker.metadata
                and marker.metadata.get("inferred_from_stage")
            )
            if inferred:
                c.setDash([3, 2])
                c.setLineWidth(1.0)
            else:
                c.setDash()

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

    def _draw_batch_label(self, marker: ImageMarker, scale_x: float, scale_y: float) -> None:
        if not marker.label:
            return

        c = self.canv
        text = marker.label
        font = "Helvetica-Bold"
        font_size = 10.5
        text_w = c.stringWidth(text, font, font_size)
        text_h = font_size * 1.05
        rx, top_y = _batch_label_pdf_origin(
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


def _batch_label_pdf_origin(
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


# =============================================================================
# Convenience helpers
# =============================================================================


def chip_row(items: Sequence[tuple[str, str]], *, total_width: float) -> Table:
    """Render a horizontal row of small status chips.

    ``items`` is ``[(label, status), ...]``. Each chip is a coloured pill
    with the label in bold. Used for severity counters on the cover.
    """

    if not items:
        items = [("none", "neutral")]

    cells: list = []
    for label, status in items:
        color = styles.status_color(status)
        para = Paragraph(
            f'<font color="{styles.css_hex(color.text)}"><b>{label}</b></font>',
            styles.SMALL_STYLE,
        )
        cells.append(para)
    n = len(cells)
    col_w = total_width / n
    table = Table([cells], colWidths=[col_w] * n, hAlign="LEFT")
    style: list = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
    ]
    for i, (_, status) in enumerate(items):
        color = styles.status_color(status)
        style.append(("BACKGROUND", (i, 0), (i, 0), color.bg))
    table.setStyle(style)
    return table


def keep_with_caption(
    flowables: Sequence,
    caption: str,
) -> KeepTogether:
    """Tie a caption to its image so the page break never separates them."""

    return KeepTogether(
        list(flowables) + [Spacer(1, 2), Paragraph(caption, styles.CAPTION_STYLE)]
    )


__all__ = [
    "CompletionStrip",
    "CountCard",
    "CountCardData",
    "DonutChart",
    "OverlayedImage",
    "StackedStatusBar",
    "StatusBadge",
    "StatusSegment",
    "StatusTileGrid",
    "TileItem",
    "card_row",
    "chip_row",
    "donut_legend",
    "keep_with_caption",
    "native_image_size",
]
