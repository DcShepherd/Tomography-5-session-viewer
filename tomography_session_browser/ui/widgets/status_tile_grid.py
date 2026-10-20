"""Status band for the dashboard's count cards.

Every card shows one full-width band of the same height (plan C4). While each
item can have a segment at least ``MIN_SEGMENT_PX`` wide, the band is one
segment per item, colour-coded by status, stretched to fill the card;
otherwise it is one proportional bar with the same breakdown. Hover shows an
item's tooltip; clicking a segment emits ``tile_clicked(item_id, destination)``.

Items carry an optional ``group`` (a tilt series' batch position). The band
keeps each group's items together with a wider gap between groups, and can
name each group under its segments; a band whose items have no group is the
one-group case and looks as it always did. Gaps of both widths count against
the minimum segment width, so a band that cannot afford its groups collapses
to the proportional bar like any other that is too dense.

A band that names its groups names all of them or none. Whole names when every
one fits; otherwise the names without the prefix they all share (batch
positions ``Grid2_20260627_1`` to ``_20`` become ``1`` to ``20``, the
prefix given once by ``label_prefix_changed``); otherwise it collapses to the
proportional bar. Shortening each name from the right used to cut away the one
part that differed, so 20 groups read ``Grid…``, ``Grid2_2026…``.

It used to draw squares of at most 18 px, so 12 atlases filled a third of
the card at 4K, and a card that fell back to a thinner bar sat beside one
showing squares in the same row.

Used by the sample summary's cards (``ui/dashboard/sample_cards.py``).
The PDF report draws its own tile grid (``reports.graphics``), unchanged.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QHelpEvent, QKeyEvent, QMouseEvent, QPainter, QPen, QResizeEvent
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from tomography_session_browser.presentation.dashboard.models import TILE_STATUS_COMPLETE, TILE_STATUS_FAILED, TILE_STATUS_MISSING, TILE_STATUS_NEUTRAL, TILE_STATUS_WARNING, StatusTileModel
from tomography_session_browser.presentation.design_tokens import SPACE_S, SPACE_XS
from tomography_session_browser.presentation.formatting import shared_name_prefix
from tomography_session_browser.ui.theme import current_palette


# Height of the band, segmented or proportional, on every card.
BAND_HEIGHT_PX = 12

# Narrowest segment still drawn per item. Below this the band becomes one
# proportional bar, so the user sees a breakdown instead of specks.
MIN_SEGMENT_PX = 6

# Spacing between segments of one group, in pixels.
TILE_GAP = 3

# Spacing between groups.
GROUP_GAP = SPACE_S

# Fewest characters of a shortened group name (the ellipsis not counted) that
# still name the group. A name that fits whole is drawn however short it is.
MIN_LABEL_CHARS = 4

#: A group name as drawn: its left edge, the room it has, and its text.
GroupLabel = tuple[int, int, str]


def grouped(items: list[StatusTileModel]) -> list[tuple[str, list[StatusTileModel]]]:
    """``items`` gathered by group, groups in order of first appearance and
    each group's items in the order given."""

    groups: dict[str, list[StatusTileModel]] = {}
    for item in items:
        groups.setdefault(item.group, []).append(item)
    return list(groups.items())


class StatusTileGrid(QWidget):
    """One status band: a segment per item, or a proportional bar.

    ``has_visible_grid()`` is True while the band is segmented, that is
    while every item has a segment at least ``MIN_SEGMENT_PX`` wide and, on a
    band that names its groups, every group can be named.
    """

    tile_clicked = Signal(str, str)  # (item_id, destination_tab_label)
    # The prefix the group names drop, "" while they are whole or not drawn.
    label_prefix_changed = Signal(str)

    def __init__(
        self,
        items: list[StatusTileModel],
        *,
        accent: QColor | str | None = None,
        band_height: int = BAND_HEIGHT_PX,
        group_labels: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._groups = grouped(items)
        # What is shown, in the order it is shown: the groups, one after another.
        self._items = [item for _group, members in self._groups for item in members]
        self._band_height = band_height
        self._group_labels = group_labels and any(group for group, _members in self._groups)
        # Dropped from every name when the whole names do not all fit. A
        # batch number of one digit is a name here, unlike in a list row.
        self._label_prefix = shared_name_prefix(
            [group for group, _members in self._groups], min_remainder=1
        ) if self._group_labels else ""
        self._shown_prefix = ""
        self._tile_rects: list[tuple[QRect, StatusTileModel]] = []
        self._label_rects: list[tuple[QRect, str]] = []
        self._tile_size = 0
        self._has_visible_grid = bool(self._items)
        self._columns = 1
        self._keyboard_index = 0

        self._fixed_accent = accent
        self._load_colours()

        # The band is fixed-height and top-aligned; the parent card decides
        # how much vertical space it gets. Width expands to fill the card.
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(self._band_height + 2)
        self._sync_height()
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setObjectName("statusTileGrid")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Status items")
        self.setAccessibleDescription(
            f"{len(self._items)} items. Use the arrow keys to move and Enter to open an item."
        )

    def _load_colours(self) -> None:
        theme = current_palette()
        self._palette_text = QColor(theme.text)
        self._accent = QColor(self._fixed_accent) if self._fixed_accent is not None else QColor(theme.chart_blue)
        self._color_complete = QColor(theme.chart_green)
        self._color_warning = QColor(theme.chart_amber)
        self._color_failed = QColor(theme.chart_red)
        self._color_missing = QColor(theme.chart_red)
        # The card's legend dot for a neutral item, and the PDF's tile.
        self._color_unknown = QColor(theme.unknown)
        self._color_neutral = QColor(theme.chart_grey)
        self._color_label = QColor(theme.text_muted)

    def refresh_theme(self, *, accent: QColor | str | None = None) -> None:
        """Recolour for the current theme; ``accent`` replaces a fixed accent."""

        if accent is not None:
            self._fixed_accent = accent
        self._load_colours()
        self.update()

    # -------------------------------------------------------------------- API

    def items(self) -> list[StatusTileModel]:
        """The items in the order the band shows them (grouped)."""

        return list(self._items)

    def groups(self) -> list[tuple[str, list[StatusTileModel]]]:
        return [(group, list(members)) for group, members in self._groups]

    def has_visible_grid(self) -> bool:
        """Whether the grid currently has room to render individual tiles.

        ``False`` when there are too many items, or groups too narrow to be
        named, to render legibly at the current width — the band then shows
        the proportional bar.
        """

        return self._has_visible_grid

    def shown_label_prefix(self) -> str:
        """The prefix the drawn group names leave out ("" when none is left out)."""

        return self._shown_prefix

    def tile_size(self) -> int:
        """Width of each segment in pixels at the last paint (0 when proportional)."""

        return self._tile_size

    def tile_rects(self) -> list[tuple[QRect, StatusTileModel]]:
        """Read-only access to the painted segment rectangles. Used by tests."""

        return list(self._tile_rects)

    def tooltip_for(self, tile: StatusTileModel) -> str:
        """The item's tooltip, and its batch position when the band names them:
        the name under a one-series group may have had to be shortened."""

        if not (self._group_labels and tile.group):
            return tile.tooltip
        origin = "inferred from the series name" if tile.group_inferred else "recorded"
        return f"{tile.tooltip}\nBatch position {tile.group} ({origin})"

    def label_rects(self) -> list[tuple[QRect, str]]:
        """The group names drawn at the last paint, each with the room it was given."""

        return list(self._label_rects)

    # ----------------------------------------------------------------- layout

    def _gaps_width(self) -> int:
        """Space the gaps take: ``TILE_GAP`` inside a group, ``GROUP_GAP`` between."""

        boundaries = max(0, len(self._groups) - 1)
        return TILE_GAP * max(0, len(self._items) - 1 - boundaries) + GROUP_GAP * boundaries

    def segment_width(self, width: int) -> float:
        """Width each item's segment gets across ``width``, gaps included."""

        count = len(self._items)
        if count == 0 or width <= 0:
            return 0.0
        return (width - self._gaps_width()) / count

    def segment_rects(self, width: int) -> list[QRect]:
        """One rectangle per item, together spanning exactly ``width``."""

        segment = self.segment_width(width)
        rects = []
        index = 0
        for boundary, (_group, members) in enumerate(self._groups):
            for _member in members:
                # Items before this one, and the gaps before it: one per
                # neighbouring pair, wider where the pair straddles a group.
                left_edge = index * segment + (index - boundary) * TILE_GAP + boundary * GROUP_GAP
                left = round(left_edge)
                right = round(left_edge + segment)
                rects.append(QRect(left, 0, max(1, right - left), self._band_height))
                index += 1
        return rects

    def group_spans(self, rects: list[QRect]) -> list[tuple[str, int, int]]:
        """``(group, left, right)`` for each group, from its segments' rectangles."""

        spans = []
        first = 0
        for group, members in self._groups:
            last = first + len(members) - 1
            spans.append((group, rects[first].left(), rects[last].right()))
            first += len(members)
        return spans

    def label_plan(self, width: int) -> tuple[str, list[GroupLabel]] | None:
        """``(dropped prefix, labels)`` for the group names across ``width``,
        or None when some group cannot be named.

        Whole names when every one fits its group; otherwise every name loses
        the prefix they share and keeps its tail, which is what differs. A
        shortened name must keep ``MIN_LABEL_CHARS`` characters; ``…4`` for
        batch 14 would name the wrong batch. Unnamed groups are not labelled.
        """

        if not self._group_labels:
            return "", []
        metrics = self.fontMetrics()
        spans = [(group, left, right - left + 1)
                 for group, left, right in self.group_spans(self.segment_rects(width)) if group]
        if all(metrics.horizontalAdvance(group) <= room for group, _left, room in spans):
            return "", [(left, room, group) for group, left, room in spans]
        prefix = self._label_prefix
        # Past a shared prefix the tail differs; with none, either end may.
        mode = Qt.TextElideMode.ElideLeft if prefix else Qt.TextElideMode.ElideMiddle
        labels: list[GroupLabel] = []
        for group, left, room in spans:
            name = group[len(prefix):] if prefix and group.startswith(prefix) else group
            text = metrics.elidedText(name, mode, room)
            if text != name and len(text) - 1 < MIN_LABEL_CHARS:
                return None
            labels.append((left, room, text))
        return prefix, labels

    def _segmented(self) -> bool:
        width = self.width()
        if self.segment_width(width) < MIN_SEGMENT_PX:
            return False
        return self.label_plan(width) is not None

    def _label_row_height(self) -> int:
        return SPACE_XS + self.fontMetrics().height()

    def _wanted_height(self) -> int:
        """Tall enough for the band, and for the group names while they are drawn."""

        if self._group_labels and (self._segmented() or self.width() <= 0):
            return self._band_height + self._label_row_height() + 2
        return self._band_height + 2

    def _sync_height(self) -> None:
        # A band without names keeps the height its card gave it; one with
        # names gives the row back when it collapses to the proportional bar.
        if not self._group_labels:
            return
        height = self._wanted_height()
        if self.minimumHeight() != height or self.maximumHeight() != height:
            self.setFixedHeight(height)
        self._sync_prefix()

    def _sync_prefix(self) -> None:
        plan = self.label_plan(self.width()) if self._segmented() else None
        prefix = plan[0] if plan is not None else ""
        if prefix != self._shown_prefix:
            self._shown_prefix = prefix
            self.label_prefix_changed.emit(prefix)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 — Qt signature
        super().resizeEvent(event)
        self._sync_height()

    def event(self, event: QEvent) -> bool:  # noqa: N802 — Qt signature
        # Qt sends a ToolTip event once the pointer rests. The band has no
        # tooltip of its own, so the event used to travel on to the parent
        # card, whose help text replaced the item's tooltip that
        # mouseMoveEvent had just shown. Answering it here keeps the item's.
        # Off the band it still travels on, so the card's help shows there.
        if event.type() == QEvent.Type.ToolTip and isinstance(event, QHelpEvent):
            hit = self._hit_at(event.pos())
            if hit is not None:
                zone, tile = hit
                QToolTip.showText(event.globalPos(), self.tooltip_for(tile), self, zone)
                return True
        if event.type() == QEvent.Type.FontChange:
            self._sync_height()
        return super().event(event)

    # ------------------------------------------------------------------ paint

    def paintEvent(self, event) -> None:  # noqa: N802 — Qt signature
        self._tile_rects.clear()
        self._label_rects.clear()
        if not self._items:
            self._has_visible_grid = False
            return

        plan = self.label_plan(self.width()) if self.segment_width(self.width()) >= MIN_SEGMENT_PX else None
        if plan is None:
            # Too many items for a segment each, or groups too narrow to name.
            # Previously the widget simply drew nothing, so a card with (say)
            # 85 tilt series showed a conspicuous blank where its neighbours
            # showed a tile grid. A single proportional bar carries the same
            # breakdown.
            self._has_visible_grid = False
            self._tile_size = 0
            self._paint_proportion_bar()
            return

        self._has_visible_grid = True
        self._tile_size = int(self.segment_width(self.width()))
        # Arrow keys move along the one row.
        self._columns = len(self._items)
        radius = min(3, self._band_height // 4)
        rects = self.segment_rects(self.width())

        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            for index, (rect, tile) in enumerate(zip(rects, self._items)):
                self._tile_rects.append((rect, tile))
                painter.setBrush(self._color_for(tile.status))
                painter.drawRoundedRect(rect, radius, radius)
                if self.hasFocus() and index == self._keyboard_index:
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.setPen(QPen(QColor(current_palette().text_strong), 2))
                    painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), radius, radius)
                    painter.setPen(Qt.PenStyle.NoPen)
            self._paint_group_labels(painter, plan[1])
        finally:
            painter.end()

    def _paint_group_labels(self, painter: QPainter, labels: list[GroupLabel]) -> None:
        """Each group's name under its segments, as ``label_plan`` fitted it.

        The segments' tooltips name the whole batch position.
        """

        metrics = painter.fontMetrics()
        top = self._band_height + SPACE_XS
        painter.setPen(self._color_label)
        for left, room, text in labels:
            area = QRect(left, top, room, metrics.height())
            painter.drawText(area, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
            self._label_rects.append((area, text))

    def _paint_proportion_bar(self) -> None:
        """Draw one stacked bar summarising the same statuses as the tiles.

        Segments keep the tile order so the bar reads left-to-right as
        "mostly complete, then these failures", and every item is
        represented even when there are hundreds of them.
        """

        if not self._items:
            return
        # The same height and position as a segmented band, so the two read
        # as one kind of mark when cards in a row fall on either side.
        height = self._band_height
        top = 0
        full_width = self.width()
        if full_width <= 0:
            return

        counts: dict[str, int] = {}
        for tile in self._items:
            counts[tile.status] = counts.get(tile.status, 0) + 1
        ordered = [
            status
            for status in (
                TILE_STATUS_COMPLETE,
                TILE_STATUS_NEUTRAL,
                TILE_STATUS_WARNING,
                TILE_STATUS_FAILED,
                TILE_STATUS_MISSING,
            )
            if counts.get(status)
        ]
        ordered.extend(status for status in counts if status not in ordered)

        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            total = len(self._items)
            x = 0
            for index, status in enumerate(ordered):
                remaining = full_width - x
                if index == len(ordered) - 1:
                    segment = remaining
                else:
                    # Never let a non-zero group vanish to nothing.
                    segment = max(2, round(full_width * counts[status] / total))
                    segment = min(segment, max(2, remaining - 2 * (len(ordered) - index - 1)))
                if segment <= 0:
                    continue
                painter.setBrush(self._color_for(status))
                painter.drawRoundedRect(QRect(x, top, segment, height), 3, 3)
                x += segment
        finally:
            painter.end()

    def _color_for(self, status: str) -> QColor:
        if status == TILE_STATUS_COMPLETE:
            return self._color_complete
        if status == TILE_STATUS_WARNING:
            return self._color_warning
        if status == TILE_STATUS_FAILED:
            return self._color_failed
        if status == TILE_STATUS_MISSING:
            return self._color_missing
        if status == TILE_STATUS_NEUTRAL:
            # The colour of the card's legend dot for the same items ("2
            # unknown", "1 pending"). The tiles used the card's accent, so
            # the Atlases card drew its two unknown atlases in teal beside a
            # grey "2 unknown".
            return self._color_unknown
        return self._color_neutral

    # ------------------------------------------------------------- mouse / hover

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 — Qt signature
        hit = self._hit_at(event.position().toPoint())
        if hit is not None:
            zone, tile = hit
            QToolTip.showText(event.globalPosition().toPoint(), self.tooltip_for(tile), self, zone)
        else:
            QToolTip.hideText()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 — Qt signature
        if event.button() == Qt.MouseButton.LeftButton:
            tile = self._tile_at(event.position().toPoint())
            if tile is not None:
                self.tile_clicked.emit(tile.id, tile.destination)
                event.accept()
                return
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 — Qt signature
        if not self._items:
            super().keyPressEvent(event)
            return
        key = event.key()
        if key in {Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down}:
            delta = {
                Qt.Key.Key_Left: -1,
                Qt.Key.Key_Right: 1,
                Qt.Key.Key_Up: -max(1, self._columns),
                Qt.Key.Key_Down: max(1, self._columns),
            }[key]
            self._keyboard_index = max(
                0,
                min(len(self._items) - 1, self._keyboard_index + delta),
            )
            tile = self._items[self._keyboard_index]
            self.setAccessibleDescription(
                f"{self.tooltip_for(tile)}. Press Enter to open this item."
            )
            self.update()
            event.accept()
            return
        if key in {Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space}:
            tile = self._items[self._keyboard_index]
            self.tile_clicked.emit(tile.id, tile.destination)
            event.accept()
            return
        super().keyPressEvent(event)

    def _tile_at(self, point: QPoint) -> StatusTileModel | None:
        hit = self._hit_at(point)
        return hit[1] if hit is not None else None

    def _hit_at(self, point: QPoint) -> tuple[QRect, StatusTileModel] | None:
        for zone, tile in self.hit_zones():
            if zone.contains(point):
                return zone, tile
        return None

    def hit_zones(self) -> list[tuple[QRect, StatusTileModel]]:
        """Each segment's hover and click area: its column of the band.

        The gaps between segments are split between their neighbours, so the
        pointer is always over some item while it is on the band. Hovering
        used to drop the tooltip at every 3 px gap. Below the band belongs to
        no item, and the card's own help shows there.
        """

        zones: list[tuple[QRect, StatusTileModel]] = []
        left = 0
        for index, (rect, tile) in enumerate(self._tile_rects):
            if index + 1 < len(self._tile_rects):
                right = (rect.right() + self._tile_rects[index + 1][0].left()) // 2
            else:
                right = max(rect.right(), self.width() - 1)
            zones.append((QRect(QPoint(left, 0), QPoint(right, self._band_height - 1)), tile))
            left = right + 1
        return zones

    # --------------------------------------------------------------- size hint

    def sizeHint(self) -> QSize:  # noqa: N802 — Qt signature
        # A reasonable default; the parent layout always overrides this with
        # its own width via the setMinimumHeight + Expanding × Fixed policy.
        return QSize(120, self._wanted_height())
