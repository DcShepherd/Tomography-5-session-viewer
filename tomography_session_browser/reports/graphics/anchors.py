"""Anchors report components."""
from __future__ import annotations
from reportlab.platypus import Flowable, Paragraph
from tomography_session_browser.reports import styles
from tomography_session_browser.reports.graphics.overlays import LOGGER
from tomography_session_browser.reports.graphics.text import fit_text, html_escape


class AnchoredParagraph(Paragraph):
    """A paragraph that marks its page when drawn.

    ``anchor`` adds a PDF bookmark (outline entry at ``level``) and records
    the page for page references such as the cover's dataset index. The
    anchor is part of the heading so it lands on the heading's page even when
    the heading is pushed onto the next one. ``rule`` draws a hairline under
    it, and ``meta`` a muted note at its right.
    """

    def __init__(
        self,
        text,
        style,
        bulletText=None,
        frags=None,
        caseSensitive=1,
        encoding="utf8",
        *,
        anchor: str | None = None,
        outline: str | None = None,
        level: int = 0,
        rule: bool = False,
        meta: str | None = None,
    ) -> None:
        super().__init__(text, style, bulletText=bulletText, frags=frags, caseSensitive=caseSensitive, encoding=encoding)
        self.anchor = anchor
        self.outline = outline
        self.level = level
        self.rule = rule
        self.meta = meta

    def draw(self) -> None:
        c = self.canv
        if self.anchor:
            mark_page(c, self.anchor, self.outline, self.level, top=self.height)
        super().draw()
        if self.rule:
            c.saveState()
            c.setStrokeColor(styles.BORDER)
            c.setLineWidth(styles.HAIRLINE)
            c.line(0, -3.0, self.width, -3.0)
            c.restoreState()
        if self.meta:
            c.saveState()
            c.setFillColor(styles.TEXT_MUTED)
            c.setFont(styles.SANS, styles.TYPE_SMALL)
            baseline = self.height - self.style.fontSize * 1.02
            c.drawRightString(self.width, baseline, fit_text(self.meta, styles.SANS, styles.TYPE_SMALL, self.width * 0.55))
            c.restoreState()


def section_heading(label: str, *, anchor: str | None = None, level: int = 1, meta: str | None = None) -> AnchoredParagraph:
    """A subsection heading (Atlas, Overviews, ...) with a rule under it."""

    return AnchoredParagraph(html_escape(label), styles.H2_STYLE, anchor=anchor, outline=label, level=level, rule=True, meta=meta)


def mark_page(canvas, key: str, outline: str | None, level: int, *, top: float = 0.0) -> None:
    """Bookmark this position and record its page for page references."""

    try:
        canvas.bookmarkHorizontal(key, 0, top + 4)
        if outline:
            canvas.addOutlineEntry(outline, key, level=level, closed=level > 0)
    except Exception as exc:  # an out-of-order outline level must not stop a report
        LOGGER.debug("Could not add outline entry %s: %s", key, exc)
    record = getattr(canvas, "record_page_ref", None)
    if record is not None:
        record(key)


class PageRef(Flowable):
    """The page a key was drawn on, right-aligned in ``width``.

    The number is not known when the cover is laid out, so the canvas draws it
    as a form that it fills in once the whole report has been laid out (see
    ``pdf_export``). On a canvas without that support nothing is drawn.
    """

    def __init__(self, key: str, *, width: float = 22.0, size: float = styles.TYPE_SMALL) -> None:
        super().__init__()
        self.key = key
        self.width = width
        self.size = size

    def wrap(self, _avail_w, _avail_h):  # noqa: N802
        return self.width, self.size * 1.3

    def draw(self) -> None:
        form = getattr(self.canv, "page_ref_form", None)
        if form is None:
            return
        name = form(self.key, styles.MONO, self.size, styles.TEXT_MUTED)
        self.canv.saveState()
        self.canv.translate(self.width, self.size * 0.3)
        self.canv.doForm(name)
        self.canv.restoreState()

