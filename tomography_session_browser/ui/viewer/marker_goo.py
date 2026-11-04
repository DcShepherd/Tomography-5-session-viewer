"""Atlas batch markers that bud apart, or flow together, as the zoom regroups them.

When a zoom changes which batch positions the Atlas merges (``services/atlas_lod``),
the new glyphs used to replace the old ones at once. Here a cluster that splits
swells and buds its members off like droplets: each pulls away on a neck that
thins and snaps (metaball bridges, after Hiroyuki Sato's construction), while
the parent shrinks away. Zooming out runs the same in reverse: positions flow
into the cluster that now holds them.

Display only. Every glyph is drawn where ``atlas_batch_view`` put it, from the
first frame: the droplets are painted by a widget floating over the canvas (so
an image export never contains them), and the real glyphs they turn into only
fade in as the droplets settle into them. Positions, clustering and counts are
untouched. With reduced motion, a hidden canvas, or more droplets in view than
:data:`GOO_MAX_DROPLETS`, nothing animates.

The geometry is plain Python (``goo_groups``, ``metaball_neck``); the widget
only maps scene points to the viewport and paints.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import math

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QTransform
from PySide6.QtWidgets import QGraphicsItem, QGraphicsView, QWidget

from tomography_session_browser.domain.markers import ImageMarker, MarkerType
from tomography_session_browser.services.atlas_marker_style import LEAF_DIAMETER_DP, LEAF_HALO_WIDTH_DP, LEAF_RING_RADIUS_DP, MARKER_INK
from tomography_session_browser.ui.motion import DURATION_GOO_MS, STAGGER_MAX_ITEMS, STAGGER_MS, Tween, motion_enabled
from tomography_session_browser.ui.viewer.marker_paint import atlas_status_color

#: Above this many droplets in view a regroup is shown at once: the canvas
#: stays responsive on a whole-grid atlas with thousands of positions.
GOO_MAX_DROPLETS = 120

#: A neck holds until its ends are this many radii (summed) apart, thinning on the way.
NECK_REACH = 2.4

#: How far the neck's ends spread round each circle (Sato's ``v``), before it thins.
NECK_SPREAD = 0.5

#: How long the neck's curves are, relative to the radii (Sato's handle size).
NECK_HANDLE = 2.4

#: Where in the motion the real glyphs start fading in, and the droplets start
#: settling into them.
SETTLE_FROM = 0.62

#: How far a droplet overshoots its place before it settles (``ease_out_back``).
OVERSHOOT = 1.35

Point = tuple[float, float]

#: A group drawn at one moment: circles (centre, radius, colour) and necks
#: (outline, colour at each end, each end's centre), in viewport coordinates.
GooShapes = tuple[list[tuple[Point, float, QColor]], list[tuple[QPainterPath, QColor, QColor, Point, Point]]]


@dataclass(frozen=True, slots=True)
class GooBlob:
    """One drawn batch glyph: a leaf or a cluster, at its scene position."""

    key: str
    members: tuple[str, ...]
    x: float
    y: float
    radius: float
    status: str


@dataclass(frozen=True, slots=True)
class GooDroplet:
    """A droplet travelling between a glyph of the old layout and one of the new."""

    start: Point
    end: Point
    start_radius: float
    end_radius: float
    status: str
    delay: float = 0.0


@dataclass(frozen=True, slots=True)
class GooGroup:
    """A cluster splitting into droplets (``splits``), or droplets merging into one."""

    anchor: Point
    anchor_status: str
    anchor_radius: float
    droplets: tuple[GooDroplet, ...]
    splits: bool
    #: The new glyphs this group turns into: they fade in as it settles.
    reveals: tuple[str, ...]


def blobs_from_markers(markers: Sequence[ImageMarker]) -> list[GooBlob]:
    """The Atlas batch glyphs among ``markers``, as ``atlas_lod_display_markers`` built them."""

    blobs: list[GooBlob] = []
    for marker in markers:
        if marker.x is None or marker.y is None:
            continue
        if marker.marker_type == MarkerType.BATCH_CLUSTER:
            members = tuple(str(member) for member in marker.metadata.get("member_ids", ()))
        elif marker.metadata.get("atlas_lod_role") in {"batch_position", "unattributed"}:
            members = (marker.id,)
        else:
            continue
        diameter = float(marker.metadata.get("diameter_px") or LEAF_DIAMETER_DP)
        radius = diameter / 2.0 if marker.marker_type == MarkerType.BATCH_CLUSTER else LEAF_RING_RADIUS_DP
        status = (marker.status or "queued").lower()
        if not bool(marker.metadata.get("navigation_enabled", True)):
            status = "unattributed"
        blobs.append(GooBlob(marker.id, members, float(marker.x), float(marker.y), radius, status))
    return blobs


def goo_groups(before: Sequence[GooBlob], after: Sequence[GooBlob]) -> list[GooGroup]:
    """How the glyphs of ``before`` become those of ``after``.

    A glyph of ``after`` comes from the old glyph holding most of its members.
    An old glyph that several new ones come from splits; a new glyph that
    takes in several old ones merges them. A glyph that only stays is left
    alone, and so is a member found in only one of the layouts.
    """

    old_of = {member: blob for blob in before for member in blob.members}
    targets: dict[str, list[GooBlob]] = {}
    sources: dict[str, list[GooBlob]] = {}
    for blob in after:
        counts: dict[str, int] = {}
        for member in blob.members:
            old = old_of.get(member)
            if old is not None:
                counts[old.key] = counts.get(old.key, 0) + 1
        if not counts:
            continue
        by_key = {old.key: old for old in before if old.key in counts}
        # Most members first; the key breaks a tie, so the result never depends on order.
        ordered = sorted(counts, key=lambda key: (-counts[key], key))
        targets.setdefault(ordered[0], []).append(blob)
        sources[blob.key] = [by_key[key] for key in ordered]
    before_by_key = {blob.key: blob for blob in before}
    groups: list[GooGroup] = []
    for old_key, children in sorted(targets.items()):
        if len(children) < 2:
            continue
        old = before_by_key[old_key]
        anchor = (old.x, old.y)
        # Round the parent in angle order, so neighbouring droplets share necks.
        children = sorted(children, key=lambda child: math.atan2(child.y - old.y, child.x - old.x))
        droplets = tuple(
            GooDroplet(
                start=anchor,
                end=(child.x, child.y),
                start_radius=min(child.radius, old.radius) * 0.85,
                end_radius=child.radius,
                status=child.status,
                delay=stagger(index),
            )
            for index, child in enumerate(children)
        )
        # The parent starts at its glyph's inner disc, so its buds show at once.
        groups.append(GooGroup(anchor, old.status, old.radius * 0.75, droplets, True, tuple(child.key for child in children)))
    for blob in after:
        olds = sources.get(blob.key, [])
        if len(olds) < 2:
            continue
        anchor = (blob.x, blob.y)
        olds = sorted(olds, key=lambda old: math.atan2(old.y - blob.y, old.x - blob.x))
        droplets = tuple(
            GooDroplet(
                start=(old.x, old.y),
                end=anchor,
                start_radius=old.radius,
                end_radius=old.radius * 0.45,
                status=old.status,
                delay=stagger(index),
            )
            for index, old in enumerate(olds)
        )
        groups.append(GooGroup(anchor, blob.status, blob.radius, droplets, False, (blob.key,)))
    return groups


def stagger(index: int) -> float:
    """A droplet's start delay, as a fraction of the motion: the shared stagger token."""

    return min(index, STAGGER_MAX_ITEMS - 1) * STAGGER_MS / DURATION_GOO_MS


def ease_out_back(t: float, overshoot: float = OVERSHOOT) -> float:
    t = min(1.0, max(0.0, t)) - 1.0
    return 1.0 + t * t * ((overshoot + 1.0) * t + overshoot)


def smoothstep(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return t * t * (3.0 - 2.0 * t)


def droplet_at(droplet: GooDroplet, t: float, *, splits: bool) -> tuple[Point, float]:
    """A droplet's centre (scene) and radius (dp) at ``t`` in 0..1."""

    span = max(1e-6, 1.0 - droplet.delay)
    local = min(1.0, max(0.0, (t - droplet.delay) / span))
    # A bud lets go at once and overshoots its place a little, as a droplet would.
    travel = ease_out_back(local) if splits else smoothstep(local) ** 1.3
    x = droplet.start[0] + (droplet.end[0] - droplet.start[0]) * travel
    y = droplet.start[1] + (droplet.end[1] - droplet.start[1]) * travel
    radius = droplet.start_radius + (droplet.end_radius - droplet.start_radius) * smoothstep(local)
    # A droplet wobbles a little as it lets go, and settles into the glyph that replaces it.
    radius *= 1.0 + 0.12 * math.sin(math.pi * local) * (1.0 - local)
    radius *= 1.0 - smoothstep((t - SETTLE_FROM) / (1.0 - SETTLE_FROM))
    return (x, y), max(0.0, radius)


def anchor_radius_at(group: GooGroup, t: float) -> float:
    """The parent's radius at ``t``: it gives up its volume as it splits, and gathers it as it merges."""

    if group.splits:
        return group.anchor_radius * (1.0 - smoothstep(t / 0.5)) ** 0.6
    return group.anchor_radius * smoothstep(t / 0.85) * (1.0 - smoothstep((t - SETTLE_FROM) / (1.0 - SETTLE_FROM)))


def reveal_opacity(t: float) -> float:
    """How far the real glyphs have faded in at ``t``."""

    return smoothstep((t - SETTLE_FROM) / (1.0 - SETTLE_FROM))


def metaball_neck(
    c1: Point, r1: float, c2: Point, r2: float, *, reach: float = NECK_REACH
) -> tuple[Point, Point, Point, Point, Point, Point, Point, Point] | None:
    """The bridge between two circles, as Sato's metaball construction draws it.

    Returns ``(p1, h1, h3, p3, p4, h4, h2, p2)``: the outline runs from ``p1``
    on the first circle by a cubic (handles ``h1``, ``h3``) to ``p3`` on the
    second, across to ``p4``, and back by a cubic (``h4``, ``h2``) to ``p2``.
    None once the circles are too far apart (the neck has snapped), when one
    holds the other, or when either has no size. The neck thins towards the
    snap so it pinches off rather than vanishing whole.
    """

    if r1 <= 0.0 or r2 <= 0.0:
        return None
    d = math.dist(c1, c2)
    total = r1 + r2
    if d >= total * reach or d <= abs(r1 - r2) or d <= 1e-9:
        return None
    if d < total:
        u1 = math.acos(clamp_unit((r1 * r1 + d * d - r2 * r2) / (2.0 * r1 * d)))
        u2 = math.acos(clamp_unit((r2 * r2 + d * d - r1 * r1) / (2.0 * r2 * d)))
        spread = NECK_SPREAD
    else:
        u1 = u2 = 0.0
        # Pinch: the neck's ends close in on the line between the centres before it snaps.
        stretch = (d - total) / (total * (reach - 1.0))
        spread = NECK_SPREAD * (1.0 - stretch) ** 1.5
    between = math.atan2(c2[1] - c1[1], c2[0] - c1[0])
    max_spread = math.acos(clamp_unit((r1 - r2) / d))
    a1 = between + u1 + (max_spread - u1) * spread
    a2 = between - u1 - (max_spread - u1) * spread
    a3 = between + math.pi - u2 - (math.pi - u2 - max_spread) * spread
    a4 = between - math.pi + u2 + (math.pi - u2 - max_spread) * spread
    p1 = on_circle(c1, r1, a1)
    p2 = on_circle(c1, r1, a2)
    p3 = on_circle(c2, r2, a3)
    p4 = on_circle(c2, r2, a4)
    handle = min(spread * NECK_HANDLE, math.dist(p1, p3) / total) * min(1.0, d * 2.0 / total)
    h1 = on_circle(p1, r1 * handle, a1 - math.pi / 2.0)
    h2 = on_circle(p2, r1 * handle, a2 + math.pi / 2.0)
    h3 = on_circle(p3, r2 * handle, a3 + math.pi / 2.0)
    h4 = on_circle(p4, r2 * handle, a4 - math.pi / 2.0)
    return p1, h1, h3, p3, p4, h4, h2, p2


def clamp_unit(value: float) -> float:
    return min(1.0, max(-1.0, value))


def on_circle(centre: Point, radius: float, angle: float) -> Point:
    return centre[0] + radius * math.cos(angle), centre[1] + radius * math.sin(angle)


class MarkerGoo(QWidget):
    """The droplets of one regroup, floating over the canvas while they move.

    ``scene_to_view`` gives the view's scene-to-viewport transform; it is asked
    once a frame, so the droplets stay on the image if the view moves.
    ``revealed`` are the real glyphs the droplets turn into: they start
    transparent and fade in as the droplets settle. Clicks pass through.
    """

    def __init__(
        self,
        parent: QWidget,
        groups: Sequence[GooGroup],
        scene_to_view: Callable[[], QTransform],
        revealed: Sequence[QGraphicsItem],
        *,
        duration_ms: float = DURATION_GOO_MS,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("markerGoo")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(parent.rect())
        self.groups = tuple(groups)
        self._scene_to_view = scene_to_view
        self._revealed = list(revealed)
        self._colours: dict[str, QColor] = {}
        self.progress = 0.0
        self._shapes: tuple[float, QTransform, list[GooShapes]] | None = None
        self._painted: QRectF | None = None
        for item in self._revealed:
            item.setOpacity(0.0)
        self._tween = Tween(duration_ms, on_update=self._stepped, on_finished=self._finish, widget=self, easing=lambda t: t)
        self.destroyed.connect(lambda *_args, tween=self._tween: tween.stop() if tween.is_animating else None)
        parent.installEventFilter(self)
        self.show()
        self.raise_()
        self._tween.start()

    @property
    def is_animating(self) -> bool:
        return self._tween.is_animating

    def _colour(self, status: str) -> QColor:
        colour = self._colours.get(status)
        if colour is None:
            colour = self._colours[status] = atlas_status_color(status)
        return colour

    def shapes(self) -> list[GooShapes]:
        """Each group now, in viewport coordinates: worked out once a frame for the paint and its region."""

        transform = self._scene_to_view()
        if self._shapes is not None and self._shapes[0] == self.progress and self._shapes[1] == transform:
            return self._shapes[2]
        m11, m12, m21, m22 = transform.m11(), transform.m12(), transform.m21(), transform.m22()
        dx, dy = transform.dx(), transform.dy()

        def to_view(point: Point) -> Point:
            return m11 * point[0] + m21 * point[1] + dx, m12 * point[0] + m22 * point[1] + dy

        t = self.progress
        result: list[GooShapes] = []
        for group in self.groups:
            anchor = to_view(group.anchor)
            anchor_colour = self._colour(group.anchor_status)
            circles: list[tuple[Point, float, QColor]] = []
            radius = anchor_radius_at(group, t)
            if radius > 0.05:
                circles.append((anchor, radius, anchor_colour))
            drops = []
            for droplet in group.droplets:
                centre, drop_radius = droplet_at(droplet, t, splits=group.splits)
                drops.append((to_view(centre), drop_radius, self._colour(droplet.status)))
            circles.extend(drop for drop in drops if drop[1] > 0.05)
            necks: list[tuple[QPainterPath, QColor, QColor, Point, Point]] = []
            for point, drop_radius, colour in drops:
                neck = neck_path(anchor, radius, point, drop_radius)
                if neck is not None:
                    necks.append((neck, anchor_colour, colour, anchor, point))
            if len(drops) > 1:
                # Neighbouring droplets cling to each other as they part.
                pairs = zip(drops, drops[1:] + (drops[:1] if len(drops) > 2 else []))
                for (first, first_radius, first_colour), (second, second_radius, second_colour) in pairs:
                    neck = neck_path(first, first_radius, second, second_radius)
                    if neck is not None:
                        necks.append((neck, first_colour, second_colour, first, second))
            result.append((circles, necks))
        self._shapes = (self.progress, QTransform(transform), result)
        return result

    def _dirty_rect(self) -> QRectF:
        left = top = math.inf
        right = bottom = -math.inf
        for circles, necks in self.shapes():
            for (x, y), radius, _colour in circles:
                reach = radius + LEAF_HALO_WIDTH_DP + 2.0
                left, top = min(left, x - reach), min(top, y - reach)
                right, bottom = max(right, x + reach), max(bottom, y + reach)
            for neck, *_rest in necks:
                box = neck.boundingRect()
                left, top = min(left, box.left() - 4.0), min(top, box.top() - 4.0)
                right, bottom = max(right, box.right() + 4.0), max(bottom, box.bottom() + 4.0)
        if left > right:
            return QRectF()
        return QRectF(left, top, right - left, bottom - top)

    def _stepped(self, progress: float) -> None:
        self.progress = progress
        rect = self._dirty_rect()
        dirty = rect if self._painted is None else rect.united(self._painted)
        self._painted = rect
        opacity = reveal_opacity(progress)
        for item in self._revealed:
            item.setOpacity(opacity)
        if not dirty.isEmpty():
            self.update(dirty.toAlignedRect())

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        halo = QPen(QColor(MARKER_INK), LEAF_HALO_WIDTH_DP)
        halo.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        for circles, necks in self.shapes():
            # The ink halo round each droplet first, as round each glyph; the
            # necks then fill over it where they join. A neck has no halo of its
            # own: thinning, it would turn into a dark thread.
            painter.setPen(halo)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for (x, y), radius, _colour in circles:
                painter.drawEllipse(QPointF(x, y), radius, radius)
            painter.setPen(Qt.PenStyle.NoPen)
            for neck, start_colour, end_colour, start, end in necks:
                if start_colour == end_colour:
                    painter.setBrush(start_colour)
                else:
                    gradient = QLinearGradient(QPointF(*start), QPointF(*end))
                    gradient.setColorAt(0.0, start_colour)
                    gradient.setColorAt(1.0, end_colour)
                    painter.setBrush(gradient)
                painter.drawPath(neck)
            for (x, y), radius, colour in circles:
                painter.setBrush(colour)
                painter.drawEllipse(QPointF(x, y), radius, radius)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        if event.type() == QEvent.Type.Resize and watched is self.parentWidget():
            self.setGeometry(watched.rect())
        return False

    def dismiss(self) -> None:
        """End now: the real glyphs show in full."""

        self._tween.stop()
        self._finish()

    def _finish(self) -> None:
        for item in self._revealed:
            # A redraw may already have taken the item out of the scene; setting
            # a removed item's opacity is harmless.
            item.setOpacity(1.0)
        self._revealed = []
        parent = self.parentWidget()
        if parent is not None:
            parent.removeEventFilter(self)
        self.hide()
        self.deleteLater()


def neck_path(c1: Point, r1: float, c2: Point, r2: float) -> QPainterPath | None:
    points = metaball_neck(c1, r1, c2, r2)
    if points is None:
        return None
    p1, h1, h3, p3, p4, h4, h2, p2 = points
    path = QPainterPath(QPointF(*p1))
    path.cubicTo(h1[0], h1[1], h3[0], h3[1], p3[0], p3[1])
    path.lineTo(p4[0], p4[1])
    path.cubicTo(h4[0], h4[1], h2[0], h2[1], p2[0], p2[1])
    path.closeSubpath()
    return path


def start_goo(
    view: QGraphicsView,
    before: Sequence[GooBlob],
    after: Sequence[GooBlob],
    items_by_key: dict[str, list[QGraphicsItem]],
) -> MarkerGoo | None:
    """Animate the regroup from ``before`` to ``after`` over ``view``'s viewport, if it may.

    Only the groups that touch the viewport move; the rest change at once.
    """

    if not motion_enabled(view) or not view.isVisible():
        return None
    transform = view.viewportTransform()
    area = QRectF(view.viewport().rect()).adjusted(-24.0, -24.0, 24.0, 24.0)
    groups = []
    for group in goo_groups(before, after):
        points = [group.anchor, *(droplet.end for droplet in group.droplets), *(droplet.start for droplet in group.droplets)]
        if any(area.contains(transform.map(QPointF(*point))) for point in points):
            groups.append(group)
    if not groups or sum(len(group.droplets) for group in groups) > GOO_MAX_DROPLETS:
        return None
    revealed = [item for group in groups for key in group.reveals for item in items_by_key.get(key, ())]
    return MarkerGoo(view.viewport(), groups, view.viewportTransform, revealed)


__all__ = [
    "GOO_MAX_DROPLETS",
    "GooBlob",
    "GooDroplet",
    "GooGroup",
    "MarkerGoo",
    "blobs_from_markers",
    "goo_groups",
    "metaball_neck",
    "start_goo",
]
