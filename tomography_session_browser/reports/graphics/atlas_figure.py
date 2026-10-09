"""Atlas figure report components."""
from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
from reportlab.lib import colors
from reportlab.platypus import Flowable, KeepTogether, Paragraph, Spacer
from tomography_session_browser.domain.markers import ImageMarker
from tomography_session_browser.reports import styles
from tomography_session_browser.services.atlas_lod import AtlasBatchView
from tomography_session_browser.services.atlas_marker_style import CLUSTER_HALO_WIDTH_DP, CLUSTER_INNER_GAP_DP, CLUSTER_SEGMENT_WIDTH_DP, IMAGE_STATUS_COLOURS, LABEL_CORNER_RADIUS_DP, LABEL_FONT_SIZE_DP, LEAF_DIAMETER_DP, LEAF_FAILED_GLYPH_WIDTH_DP, LEAF_FILL_RADIUS_DP, LEAF_HALO_WIDTH_DP, LEAF_RING_RADIUS_DP, LEAF_STATUS_WIDTH_DP, MARKER_INK, MARKER_LABEL_TEXT, PRINT_STATUS_COLOURS, cluster_diameter_dp, cluster_ring_segments
from tomography_session_browser.reports.graphics.overlays import LOGGER
from tomography_session_browser.reports.graphics.text import caption_width, draw_caption


PT_PER_DP = 0.6


class AtlasMarkerPrintLegend(Flowable):
    """The Atlas tab's batch-position legend on one line, cluster glyph included."""

    LEGEND_HEIGHT = 11.0
    GLYPH = 8.5
    CLUSTER = 10.5
    TEXT_SIZE = styles.TYPE_CAPTION + 0.2
    _ROWS = (
        ("collected", "Collected"),
        ("partial", "Partial"),
        ("queued", "Queued"),
        ("failed", "Failed"),
        ("unattributed", "Unattributed"),
    )
    _CLUSTER_LABEL = "Nearby positions (count)"
    _TITLE = "Batch position status"
    _GAP = 9.0

    def __init__(self) -> None:
        super().__init__()
        self.hAlign = "LEFT"
        size = self.TEXT_SIZE
        widths = [caption_width(self._TITLE) + 2.0]
        widths += [self.GLYPH + 3.0 + styles.string_width(label, styles.SANS, size) for _status, label in self._ROWS]
        widths.append(self.CLUSTER + 3.0 + styles.string_width(self._CLUSTER_LABEL, styles.SANS, size))
        self._widths = widths
        self.width = sum(widths) + self._GAP * (len(widths) - 1)
        self.height = self.LEGEND_HEIGHT

    def wrap(self, _avail_w, _avail_h):  # noqa: N802
        return self.width, self.height

    def draw(self) -> None:
        c = self.canv
        cy = self.height / 2
        size = self.TEXT_SIZE
        c.saveState()
        try:
            draw_caption(c, 0, cy - styles.TYPE_CAPTION * 0.36, self._TITLE)
            x = self._widths[0] + self._GAP
            for (status, label), width in zip(self._ROWS, self._widths[1:]):
                draw_pdf_atlas_leaf(
                    c,
                    x + self.GLYPH / 2,
                    cy,
                    status=status,
                    navigable=True,
                    colours_by_status=PRINT_STATUS_COLOURS,
                    halo=False,
                    diameter=self.GLYPH,
                    failed_glyph_color="#FFFFFF",
                )
                c.setFillColor(styles.TEXT)
                c.setFont(styles.SANS, size)
                c.drawString(x + self.GLYPH + 3.0, cy - size * 0.34, label)
                x += width + self._GAP
            draw_pdf_cluster(
                c,
                x + self.CLUSTER / 2,
                cy,
                diameter=self.CLUSTER,
                status="partial",
                status_counts={"collected": 2, "partial": 1},
                count=3,
                colours_by_status=PRINT_STATUS_COLOURS,
                halo=False,
                scale=self.CLUSTER / 22.0,
            )
            c.setFillColor(styles.TEXT)
            c.setFont(styles.SANS, size)
            c.drawString(x + self.CLUSTER + 3.0, cy - size * 0.34, self._CLUSTER_LABEL)
        finally:
            c.restoreState()


def draw_pdf_cluster(
    canvas,
    cx: float,
    cy: float,
    *,
    diameter: float,
    status: str,
    status_counts: dict[str, int],
    count: int,
    colours_by_status: dict[str, str] = IMAGE_STATUS_COLOURS,
    halo: bool = True,
    scale: float = PT_PER_DP,
) -> None:
    """ReportLab counterpart of the Atlas tab's cluster ring.

    A disc in the cluster's worst status, a ring of status segments in
    clockwise order from 12 o'clock, and the member count in the middle.
    ``diameter`` is in points; ``scale`` converts the tab's dp widths.
    """

    def colour(name: str) -> colors.Color:
        return colors.HexColor(colours_by_status.get(name, colours_by_status["queued"]))

    ring_radius = diameter / 2.0 - 2.3 * scale
    inner_radius = ring_radius - CLUSTER_SEGMENT_WIDTH_DP * scale / 2.0 - CLUSTER_INNER_GAP_DP * scale
    canvas.saveState()
    try:
        canvas.setFillColor(colour(status))
        canvas.circle(cx, cy, max(inner_radius, 0.5), stroke=0, fill=1)
        canvas.setLineCap(0)
        if halo:
            canvas.setStrokeColor(colors.HexColor(MARKER_INK))
            canvas.setLineWidth(CLUSTER_HALO_WIDTH_DP * scale)
            canvas.circle(cx, cy, ring_radius, stroke=1, fill=0)
        canvas.setLineWidth(CLUSTER_SEGMENT_WIDTH_DP * scale)
        for segment in cluster_ring_segments(status_counts):
            canvas.setStrokeColor(colour(segment.status))
            # Segments start at 12 o'clock and run clockwise; ReportLab's
            # angles, like Qt's, run counter-clockwise from 3 o'clock.
            canvas.arc(
                cx - ring_radius,
                cy - ring_radius,
                cx + ring_radius,
                cy + ring_radius,
                -segment.start_degrees,
                -segment.sweep_degrees,
            )
        font_size = diameter * (0.30 if count >= 100 else 0.36)
        canvas.setFillColor(colors.HexColor(MARKER_INK))
        canvas.setFont(styles.MONO_BOLD, font_size)
        canvas.drawCentredString(cx, cy - font_size * 0.36, str(count))
    finally:
        canvas.restoreState()


class FramedImage(Flowable):
    """A pre-rendered image at a fixed size, in the hairline frame every figure has.

    ``image`` is anything ``canvas.drawImage`` takes. If it cannot be drawn
    the frame still holds its place with an "Image unavailable" note.
    """

    def __init__(self, image: object, *, width: float, height: float) -> None:
        super().__init__()
        self.image = image
        self.width = width
        self.height = height
        self.hAlign = "LEFT"

    def wrap(self, _avail_w, _avail_h):  # noqa: N802
        return self.width, self.height

    def draw(self) -> None:
        c = self.canv
        c.saveState()
        try:
            c.drawImage(self.image, 0, 0, width=self.width, height=self.height)
        except Exception as exc:  # noqa: BLE001 - an empty frame beats a failed report
            LOGGER.warning("Could not embed a report image: %s", exc)
            c.setFillColor(styles.SURFACE_ALT)
            c.rect(0, 0, self.width, self.height, stroke=0, fill=1)
            c.setFillColor(styles.TEXT_MUTED)
            c.setFont(styles.SANS_ITALIC, 8)
            c.drawCentredString(self.width / 2, self.height / 2, "Image unavailable")
        c.setStrokeColor(styles.BORDER_STRONG)
        c.setLineWidth(0.5)
        c.rect(0, 0, self.width, self.height, stroke=1, fill=0)
        c.restoreState()


@dataclass(frozen=True, slots=True)
class RasterCrop:
    """A pre-rendered image and the box it covers, in the figure's image pixels.

    ``image`` is anything ``canvas.drawImage`` takes (an ``ImageReader`` over
    JPEG bytes embeds them as they are). ``box`` is ``(x0, y0, x1, y1)``.
    """

    image: object
    box: tuple[float, float, float, float]


class AtlasStatusFigure(Flowable):
    """An atlas, or a region of it, with batch positions as the Atlas tab draws them.

    ``view`` is the shared Atlas rule's answer for this zoom
    (:func:`services.atlas_lod.atlas_batch_view`): which positions are leaf
    glyphs, which merge into cluster rings, and where numbers go. Glyphs,
    rings and number plates keep the tab's physical size; only the image is
    scaled. ``region`` is ``(x, y, w, h)`` in image pixels and ``scale`` is
    points per image pixel. ``detail_regions`` outlines other figures' crops,
    each ``(region, tag)`` with an optional letter tag.

    ``name_clusters`` puts a plate listing a cluster's member numbers beside
    it: on paper that stands in for the member list the tab opens when a
    cluster is clicked.

    ``raster`` replaces ``image_path`` with a sharper rendering of part of
    the image (the atlas MRC); markers keep their ``native_size`` coordinates.
    """

    def __init__(
        self,
        image_path: Path,
        *,
        native_size: tuple[int, int],
        view: AtlasBatchView | None,
        region: tuple[float, float, float, float],
        scale: float,
        detail_regions: Sequence[tuple[tuple[float, float, float, float], str | None]] = (),
        name_clusters: bool = False,
        raster: RasterCrop | None = None,
    ) -> None:
        super().__init__()
        self.image_path = Path(image_path)
        self.native_size = native_size
        self.view = view
        self.region = region
        self.scale = scale
        self.detail_regions = list(detail_regions)
        self.name_clusters = name_clusters
        self.raster = raster
        self.width = region[2] * scale
        self.height = region[3] * scale
        self.hAlign = "LEFT"

    def wrap(self, _avail_w, _avail_h):  # noqa: N802
        return self.width, self.height

    def _to_page(self, x: float, y: float) -> tuple[float, float]:
        rx, ry, _rw, _rh = self.region
        return (x - rx) * self.scale, self.height - (y - ry) * self.scale

    def draw(self) -> None:
        c = self.canv
        nw, nh = self.native_size
        rx, ry, _rw, _rh = self.region
        c.saveState()
        clip = c.beginPath()
        clip.rect(0, 0, self.width, self.height)
        c.clipPath(clip, stroke=0, fill=0)
        drawn = False
        if self.raster is not None:
            try:
                x0, y0, x1, y1 = self.raster.box
                left, top = self._to_page(x0, y0)
                c.drawImage(
                    self.raster.image,
                    left,
                    top - (y1 - y0) * self.scale,
                    width=(x1 - x0) * self.scale,
                    height=(y1 - y0) * self.scale,
                )
                drawn = True
            except Exception as exc:  # noqa: BLE001 - the JPG below is the fallback
                # The sharper MRC rendering is optional; the JPG the markers
                # were projected onto still shows the same picture.
                LOGGER.warning("Could not embed the atlas MRC rendering for %s: %s", self.image_path, exc)
        if not drawn:
            try:
                c.drawImage(
                    str(self.image_path),
                    -rx * self.scale,
                    self.height + ry * self.scale - nh * self.scale,
                    width=nw * self.scale,
                    height=nh * self.scale,
                    mask="auto",
                )
            except Exception as exc:  # noqa: BLE001 - an empty frame beats a failed report
                LOGGER.warning("Could not embed image %s: %s", self.image_path, exc)
                c.setFillColor(styles.SURFACE_ALT)
                c.rect(0, 0, self.width, self.height, stroke=0, fill=1)
        for detail, tag in self.detail_regions:
            self._draw_detail_outline(detail, tag)
        if self.view is not None:
            self._draw_view(self.view)
        c.restoreState()
        c.saveState()
        c.setStrokeColor(styles.BORDER_STRONG)
        c.setLineWidth(0.5)
        c.rect(0, 0, self.width, self.height, stroke=1, fill=0)
        c.restoreState()

    def _draw_detail_outline(self, detail: tuple[float, float, float, float], tag: str | None) -> None:
        c = self.canv
        dx, dy, dw, dh = detail
        left, top = self._to_page(dx, dy)
        width, height = dw * self.scale, dh * self.scale
        c.saveState()
        c.setStrokeColor(colors.HexColor(MARKER_INK))
        c.setLineWidth(2.2)
        c.rect(left, top - height, width, height, stroke=1, fill=0)
        c.setStrokeColor(colors.HexColor(MARKER_LABEL_TEXT))
        c.setLineWidth(0.9)
        c.setDash([3, 2])
        c.rect(left, top - height, width, height, stroke=1, fill=0)
        c.setDash()
        if tag:
            plate = 16.0 * PT_PER_DP
            self._plate(left + 1.5, top - plate - 1.5, plate, plate, tag)
        c.restoreState()

    def _visible(self, x: float, y: float, margin: float) -> bool:
        px, py = self._to_page(x, y)
        return -margin <= px <= self.width + margin and -margin <= py <= self.height + margin

    def _draw_view(self, view: AtlasBatchView) -> None:
        c = self.canv
        k = PT_PER_DP
        # Everything drawn, as page rectangles, so cluster plates can avoid it.
        occupied: list[tuple[float, float, float, float]] = []
        clusters: list[tuple[float, float, float, tuple[ImageMarker, ...]]] = []
        for cluster, members in view.clusters:
            x, y = cluster.centroid_scene
            if not self._visible(x, y, 30.0):
                continue
            cx, cy = self._to_page(x, y)
            diameter = cluster_diameter_dp(len(members)) * k
            draw_pdf_cluster(
                c,
                cx,
                cy,
                diameter=diameter,
                status=cluster.status or "queued",
                status_counts=dict(cluster.status_counts),
                count=len(members),
            )
            occupied.append((cx - diameter / 2, cy - diameter / 2, diameter, diameter))
            clusters.append((cx, cy, diameter, members))
        leaf_size = LEAF_DIAMETER_DP * k
        for leaf in view.leaves:
            if leaf.x is None or leaf.y is None or not self._visible(leaf.x, leaf.y, 12.0):
                continue
            cx, cy = self._to_page(leaf.x, leaf.y)
            draw_pdf_atlas_leaf(
                c,
                cx,
                cy,
                status=(leaf.status or "queued").lower(),
                navigable=bool(leaf.metadata.get("navigation_enabled", True)),
                colours_by_status=IMAGE_STATUS_COLOURS,
                halo=True,
                diameter=leaf_size,
            )
            occupied.append((cx - leaf_size / 2, cy - leaf_size / 2, leaf_size, leaf_size))
        for leaf in view.leaves:
            placement = view.labels.get(leaf.id)
            if placement is None or not leaf.label or leaf.x is None or leaf.y is None:
                continue
            if not self._visible(leaf.x, leaf.y, 40.0):
                continue
            cx, cy = self._to_page(leaf.x, leaf.y)
            occupied.append(self._draw_number_plate(cx, cy, leaf.label, placement))
        if self.name_clusters:
            for cx, cy, diameter, members in clusters:
                # Only a ring that is in the picture gets its member list.
                if 0 <= cx <= self.width and 0 <= cy <= self.height:
                    self._draw_cluster_plate(cx, cy, diameter, members, occupied)

    def _plate(self, left: float, bottom: float, width: float, height: float, text: str) -> None:
        c = self.canv
        k = PT_PER_DP
        c.setFillColor(colors.HexColor(MARKER_INK))
        c.setFillAlpha(0.88)
        c.roundRect(left, bottom, width, height, LABEL_CORNER_RADIUS_DP * k, stroke=0, fill=1)
        c.setFillAlpha(1.0)
        size = LABEL_FONT_SIZE_DP * k
        c.setFillColor(colors.HexColor(MARKER_LABEL_TEXT))
        c.setFont(styles.MONO, size)
        c.drawCentredString(left + width / 2, bottom + height / 2 - size * 0.34, text)

    def _draw_number_plate(self, cx: float, cy: float, text: str, placement) -> tuple[float, float, float, float]:
        """The tab's number plate: an 88 % ink plate, offset right of the glyph."""

        c = self.canv
        k = PT_PER_DP
        left = cx + placement.x_offset_px * k
        top = cy - placement.y_offset_px * k
        width = placement.width_px * k
        height = placement.height_px * k
        c.saveState()
        if placement.leader:
            start = (cx + (LEAF_RING_RADIUS_DP + 3.0) * k, cy)
            end = (left, top - height / 2)
            c.setLineCap(0)
            c.setStrokeColor(colors.HexColor(MARKER_INK))
            c.setLineWidth(2.4 * k)
            c.line(*start, *end)
            c.setStrokeColor(colors.HexColor(MARKER_LABEL_TEXT))
            c.setLineWidth(1.0 * k)
            c.line(*start, *end)
        self._plate(left, top - height, width, height, text)
        c.restoreState()
        return left, top - height, width, height

    def _draw_cluster_plate(
        self,
        cx: float,
        cy: float,
        diameter: float,
        members: Sequence[ImageMarker],
        occupied: list[tuple[float, float, float, float]],
    ) -> None:
        """A plate naming a cluster's members, beside the ring where it is free."""

        labels = sorted({member.label for member in members if member.label}, key=natural_key)
        if not labels:
            return
        k = PT_PER_DP
        text = ", ".join(labels)
        size = LABEL_FONT_SIZE_DP * k
        width = styles.string_width(text, styles.MONO, size) + 10.0 * k
        height = 16.0 * k
        gap = 4.0 * k
        radius = diameter / 2
        candidates = (
            (cx + radius + gap, cy - height / 2),  # right
            (cx - radius - gap - width, cy - height / 2),  # left
            (cx - width / 2, cy + radius + gap),  # above
            (cx - width / 2, cy - radius - gap - height),  # below
        )
        for left, bottom in candidates:
            box = (left, bottom, width, height)
            inside = left >= 0 and bottom >= 0 and left + width <= self.width and bottom + height <= self.height
            if inside and not any(overlaps(box, other) for other in occupied):
                break
        else:
            left, bottom = candidates[0]
        self.canv.saveState()
        self._plate(left, bottom, width, height, text)
        self.canv.restoreState()
        occupied.append((left, bottom, width, height))


def draw_pdf_atlas_leaf(
    canvas,
    cx: float,
    cy: float,
    *,
    status: str,
    navigable: bool,
    colours_by_status: dict[str, str],
    halo: bool,
    diameter: float,
    failed_glyph_color: str = MARKER_INK,
) -> None:
    """ReportLab counterpart of the QPainter leaf-marker geometry."""

    scale = diameter / 14.0
    ring_radius = LEAF_RING_RADIUS_DP * scale
    fill_radius = LEAF_FILL_RADIUS_DP * scale
    colour_key = status if status in colours_by_status else "queued"
    if not navigable and status != "unattributed":
        colour_key = "unattributed"
    colour = colors.HexColor(colours_by_status[colour_key])
    canvas.saveState()
    try:
        if navigable and status in {"collected", "failed"}:
            canvas.setFillColor(colour)
            canvas.circle(cx, cy, fill_radius, stroke=0, fill=1)
        elif navigable and status == "partial":
            path = canvas.beginPath()
            path.moveTo(cx, cy)
            path.arc(
                cx - fill_radius,
                cy - fill_radius,
                cx + fill_radius,
                cy + fill_radius,
                90.0,
                180.0,
            )
            path.close()
            canvas.setFillColor(colour)
            canvas.drawPath(path, stroke=0, fill=1)

        canvas.setLineCap(0)
        canvas.setLineJoin(0)
        if halo:
            canvas.setDash()
            canvas.setStrokeColor(colors.HexColor(MARKER_INK))
            canvas.setLineWidth(LEAF_HALO_WIDTH_DP * scale)
            canvas.circle(cx, cy, ring_radius, stroke=1, fill=0)
        canvas.setStrokeColor(colour)
        canvas.setLineWidth(LEAF_STATUS_WIDTH_DP * scale)
        if status == "unattributed":
            canvas.setDash([3.9 * scale, 2.4 * scale], 0)
        else:
            canvas.setDash()
        canvas.circle(cx, cy, ring_radius, stroke=1, fill=0)

        if navigable and status == "failed":
            glyph = fill_radius * 0.62
            canvas.setDash()
            canvas.setStrokeColor(colors.HexColor(failed_glyph_color))
            canvas.setLineWidth(LEAF_FAILED_GLYPH_WIDTH_DP * scale)
            canvas.setLineCap(1)
            canvas.line(cx - glyph, cy - glyph, cx + glyph, cy + glyph)
            canvas.line(cx + glyph, cy - glyph, cx - glyph, cy + glyph)
    finally:
        canvas.restoreState()


def keep_with_caption(
    flowables: Sequence,
    caption: str,
) -> KeepTogether:
    """Tie a caption to its figure so a page break never separates them."""

    return KeepTogether(
        list(flowables) + [Spacer(1, 2), Paragraph(caption, styles.CAPTION_STYLE)]
    )


def overlaps(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def natural_key(text: str) -> tuple:
    return tuple(int(part) if part.isdigit() else part for part in re.split(r"(\d+)", text))

