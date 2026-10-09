"""Status report components."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Sequence
from reportlab.lib import colors
from reportlab.platypus import Flowable
from tomography_session_browser.presentation.design_tokens import LIGHT_PALETTE
from tomography_session_browser.reports import styles


def draw_dot(canvas, cx: float, cy: float, radius: float, colour: colors.Color) -> None:
    canvas.setFillColor(colour)
    canvas.circle(cx, cy, radius, stroke=0, fill=1)


NORMAL_STATUS_GLYPHS: dict[str, str] = {"complete": "disc", "available": "disc", "pending": "ring"}


def is_normal_status(status: str) -> bool:
    return (status or "").strip().lower() in NORMAL_STATUS_GLYPHS


def draw_status_glyph(canvas, cx: float, cy: float, size: float, colour: colors.Color, kind: str = "disc") -> None:
    """V1's glyph, as on the Atlas: a ring, with a disc inside for ``disc``."""

    scale = size / 14.0
    ring = size / 2.0 - 0.6 * scale - 0.4
    canvas.saveState()
    canvas.setStrokeColor(colour)
    canvas.setLineWidth(max(0.7, 1.6 * scale))
    canvas.circle(cx, cy, ring, stroke=1, fill=0)
    if kind == "disc":
        canvas.setFillColor(colour)
        canvas.circle(cx, cy, max(ring - 1.6 * scale - 0.35, 0.8), stroke=0, fill=1)
    canvas.restoreState()


class Dot(Flowable):
    """A filled status dot, sized for a legend or a table cell."""

    def __init__(self, tone: str, *, size: float = 5.5) -> None:
        super().__init__()
        self.tone = tone
        self.size = size

    def wrap(self, _avail_w, _avail_h):  # noqa: N802 - ReportLab API
        return self.size, self.size

    def draw(self) -> None:
        draw_dot(self.canv, self.size / 2, self.size / 2, self.size / 2, styles.tone(self.tone).fill)


class StatusGlyph(Flowable):
    """A normal status as V1's glyph (disc in a ring, or a plain ring)."""

    def __init__(self, status: str, tone: str, *, size: float = 8.0) -> None:
        super().__init__()
        self.status = status
        self.tone = tone
        self.size = size

    def wrap(self, _avail_w, _avail_h):  # noqa: N802
        return self.size, self.size

    def draw(self) -> None:
        kind = NORMAL_STATUS_GLYPHS.get(self.status.strip().lower(), "disc")
        draw_status_glyph(self.canv, self.size / 2, self.size / 2, self.size, styles.tone(self.tone).fill, kind)


class StatusPill(Flowable):
    """An exception status: the word in a pill outlined in its tone.

    Matches the viewer list pill: outline in the tone, a pale tint of it
    behind, the word in the strong text colour so it survives greyscale.
    """

    HEIGHT = 10.0
    FONT_SIZE = 6.3

    def __init__(self, label: str, tone: str, *, min_width: float = 0.0) -> None:
        super().__init__()
        self.label = label
        self.tone = tone
        self.min_width = min_width

    def wrap(self, _avail_w, _avail_h):  # noqa: N802
        text_w = styles.string_width(self.label, styles.SANS_SEMIBOLD, self.FONT_SIZE)
        self.width = max(self.min_width, text_w + 10.0)
        return self.width, self.HEIGHT

    def draw(self) -> None:
        c = self.canv
        tone = styles.tone(self.tone)
        c.saveState()
        c.setFillColor(tone.tint)
        c.setStrokeColor(tone.fill)
        c.setLineWidth(0.6)
        c.roundRect(0.3, 0.3, self.width - 0.6, self.HEIGHT - 0.6, (self.HEIGHT - 0.6) / 2, stroke=1, fill=1)
        c.setFillColor(styles.TEXT_STRONG)
        c.setFont(styles.SANS_SEMIBOLD, self.FONT_SIZE)
        c.drawCentredString(self.width / 2, (self.HEIGHT - self.FONT_SIZE * 0.72) / 2, self.label)
        c.restoreState()


def status_mark(status_label: str, tone: str) -> Flowable:
    """The glyph for a normal status, a pill for an exception."""

    if is_normal_status(status_label):
        return StatusGlyph(status_label, tone)
    return StatusPill(status_label, tone)


@dataclass(frozen=True, slots=True)
class Chip:
    tone: str
    text: str


class Chips(Flowable):
    """A wrapping row of status chips.

    ``style="dot"`` is the stat card's strip: a coloured dot and its words.
    ``style="outline"`` is the search-map summary chip: the words in the
    tone, inside a thin outline of it.
    """

    DOT_RADIUS = 2.2
    OUTLINE_HEIGHT = 11.0

    def __init__(
        self,
        chips: Sequence[Chip],
        *,
        style: str = "dot",
        size: float = styles.TYPE_SMALL - 0.3,
        gap: float = 9.0,
        max_lines: int | None = None,
    ) -> None:
        super().__init__()
        self.chips = list(chips)
        self.style = style
        self.size = size
        self.gap = gap
        self.max_lines = max_lines
        self._lines: list[list[tuple[Chip, float]]] = []
        self.width = 0.0
        self.height = 0.0

    @property
    def line_height(self) -> float:
        return self.OUTLINE_HEIGHT + 3 if self.style == "outline" else self.size * 1.55

    def _chip_width(self, chip: Chip) -> float:
        font = styles.SANS_SEMIBOLD if self.style == "outline" else styles.SANS
        text_w = styles.string_width(chip.text, font, self.size)
        if self.style == "outline":
            return text_w + 10.0
        return self.DOT_RADIUS * 2 + 3.0 + text_w

    def wrap(self, avail_w, _avail_h):  # noqa: N802
        self.width = avail_w
        lines: list[list[tuple[Chip, float]]] = [[]]
        x = 0.0
        for chip in self.chips:
            chip_w = self._chip_width(chip)
            if lines[-1] and x + chip_w > avail_w:
                lines.append([])
                x = 0.0
            lines[-1].append((chip, chip_w))
            x += chip_w + self.gap
        if self.max_lines is not None:
            lines = lines[: self.max_lines]
        self._lines = [line for line in lines if line]
        self.height = self.line_height * len(self._lines) if self._lines else 0.0
        return self.width, self.height

    def draw(self) -> None:
        c = self.canv
        c.saveState()
        for index, line in enumerate(self._lines):
            top = self.height - index * self.line_height
            x = 0.0
            for chip, chip_w in line:
                tone = styles.tone(chip.tone)
                if self.style == "outline":
                    box_y = top - self.OUTLINE_HEIGHT
                    c.setStrokeColor(tone.fill)
                    c.setFillColor(styles.SURFACE_ALT)
                    c.setLineWidth(0.6)
                    c.roundRect(x + 0.3, box_y + 0.3, chip_w - 0.6, self.OUTLINE_HEIGHT - 0.6, styles.CONTROL_RADIUS, stroke=1, fill=1)
                    c.setFillColor(tone.ink)
                    c.setFont(styles.SANS_SEMIBOLD, self.size)
                    c.drawString(x + 5.0, box_y + (self.OUTLINE_HEIGHT - self.size * 0.72) / 2, chip.text)
                else:
                    baseline = top - self.size * 1.12
                    draw_dot(c, x + self.DOT_RADIUS, baseline + self.size * 0.33, self.DOT_RADIUS, tone.fill)
                    c.setFillColor(styles.TEXT)
                    c.setFont(styles.SANS, self.size)
                    c.drawString(x + self.DOT_RADIUS * 2 + 3.0, baseline, chip.text)
                x += chip_w + self.gap
        c.restoreState()


BAND_MIN_SEGMENT = 2.4


BAND_GAP = 1.2


BAND_ORDER = ("good", "grey", "unknown", "warn", "bad")


def draw_status_band(canvas, x: float, y: float, width: float, height: float, tones: Sequence[str]) -> None:
    """One band: a segment per item, or a proportional bar when crowded."""

    canvas.saveState()
    if not tones:
        canvas.setFillColor(styles.SURFACE_HI)
        canvas.roundRect(x, y, width, height, min(1.2, height / 2), stroke=0, fill=1)
        canvas.restoreState()
        return
    count = len(tones)
    segment = (width - BAND_GAP * (count - 1)) / count
    radius = min(1.2, height / 2)
    if segment >= BAND_MIN_SEGMENT:
        for index, name in enumerate(tones):
            canvas.setFillColor(styles.tone(name).fill)
            canvas.roundRect(x + index * (segment + BAND_GAP), y, segment, height, min(radius, segment / 2), stroke=0, fill=1)
        canvas.restoreState()
        return
    counts: dict[str, int] = {}
    for name in tones:
        counts[name] = counts.get(name, 0) + 1
    ordered = [name for name in BAND_ORDER if counts.get(name)]
    ordered.extend(name for name in counts if name not in ordered)
    path = canvas.beginPath()
    path.roundRect(x, y, width, height, radius)
    canvas.clipPath(path, stroke=0, fill=0)
    cursor = x
    for index, name in enumerate(ordered):
        seg_w = (x + width - cursor) if index == len(ordered) - 1 else max(1.0, width * counts[name] / count)
        canvas.setFillColor(styles.tone(name).fill)
        canvas.rect(cursor, y, seg_w, height, stroke=0, fill=1)
        cursor += seg_w
    canvas.restoreState()


class Band(Flowable):
    """A status band on its own, e.g. a dataset's tilt outcomes in an index."""

    def __init__(self, tones: Sequence[str], *, width: float, height: float = 4.0) -> None:
        super().__init__()
        self.tones = list(tones)
        self.width = width
        self.height = height

    def wrap(self, _avail_w, _avail_h):  # noqa: N802
        return self.width, self.height

    def draw(self) -> None:
        draw_status_band(self.canv, 0, 0, self.width, self.height, self.tones)


class CompletionBar(Flowable):
    """A search map's completion track.

    Acquired exposure areas fill from the left in the good tone; failed ones
    from the right in the bad tone, both clipped to the pill so a very small
    fraction still draws cleanly.
    """

    def __init__(self, *, acquired: int, planned: int, failed: int, width: float | None = None, height: float = 4.0) -> None:
        super().__init__()
        self.acquired = max(0, acquired)
        self.planned = max(self.acquired, planned)
        self.failed = max(0, failed)
        self.width = width or 0.0
        self.height = height

    def wrap(self, avail_w, _avail_h):  # noqa: N802
        if not self.width:
            self.width = avail_w
        return self.width, self.height

    def draw(self) -> None:
        c = self.canv
        c.saveState()
        radius = self.height / 2
        c.setFillColor(styles.SURFACE_HI)
        c.roundRect(0, 0, self.width, self.height, radius, stroke=0, fill=1)
        if self.planned > 0:
            path = c.beginPath()
            path.roundRect(0, 0, self.width, self.height, radius)
            c.clipPath(path, stroke=0, fill=0)
            done = self.width * self.acquired / self.planned
            if done > 0:
                c.setFillColor(styles.TONES["good"].fill)
                c.rect(0, 0, done, self.height, stroke=0, fill=1)
            if self.failed:
                failed_w = self.width * min(self.failed / self.planned, 1.0)
                c.setFillColor(styles.TONES["bad"].fill)
                c.rect(self.width - failed_w, 0, failed_w, self.height, stroke=0, fill=1)
        c.restoreState()


BADGE_TONES = {"LS": "accent", "AT": "grey", "DC": "grey", "!": "warn"}


class ProjectBadge(Flowable):
    """The project tree's badge: LS, AT, DC or ! in a small rounded box."""

    WIDTH = 17.0
    HEIGHT = 10.5

    def __init__(self, badge: str) -> None:
        super().__init__()
        self.badge = badge

    def wrap(self, _avail_w, _avail_h):  # noqa: N802
        return self.WIDTH, self.HEIGHT

    def draw(self) -> None:
        c = self.canv
        role = BADGE_TONES.get(self.badge, "grey")
        if role == "accent":
            stroke, fill, ink = styles.ACCENT, colors.HexColor(LIGHT_PALETTE.selection), styles.TEXT_STRONG
        else:
            tone = styles.tone(role)
            stroke, fill, ink = tone.fill, styles.SURFACE_ALT, styles.TEXT_STRONG
        c.saveState()
        c.setStrokeColor(stroke)
        c.setFillColor(fill)
        c.setLineWidth(0.6)
        c.roundRect(0.3, 0.3, self.WIDTH - 0.6, self.HEIGHT - 0.6, styles.CONTROL_RADIUS, stroke=1, fill=1)
        c.setFillColor(ink)
        c.setFont(styles.MONO_BOLD, 6.2)
        c.drawCentredString(self.WIDTH / 2, (self.HEIGHT - 6.2 * 0.7) / 2, self.badge)
        c.restoreState()

