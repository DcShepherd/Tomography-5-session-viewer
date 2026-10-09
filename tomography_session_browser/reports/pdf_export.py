"""Page templates and document chrome for the session report.

Owns:

* page geometry (portrait + landscape templates) and margins
* the running header (the app mark and name, and the report title) and the
  footer (project label and "Page N of M")
* the canvas that fills in numbers only known once the whole report is laid
  out: the page total, and the page each dataset starts on (the cover's
  dataset index)

Those late numbers are drawn as PDF form XObjects. A page refers to a form by
name while it is drawn; the canvas defines every form just before it saves,
when the page count and every recorded page are final. One layout pass is
enough, and bookmarks stay on their real pages.

The generator submits a flat list of flowables. ``NextPageTemplate`` can
switch to the landscape template without re-implementing the chrome.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from reportlab.lib.pagesizes import LETTER, landscape
from reportlab.lib.units import inch
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import BaseDocTemplate, Flowable, Frame, PageTemplate

from tomography_session_browser.reports import styles
from tomography_session_browser.reports.graphics import draw_brand_mark, fit_text

PAGE_SIZE = LETTER
LEFT_MARGIN = RIGHT_MARGIN = 0.6 * inch
TOP_MARGIN = 0.78 * inch
BOTTOM_MARGIN = 0.62 * inch

PORTRAIT_USABLE_W = PAGE_SIZE[0] - LEFT_MARGIN - RIGHT_MARGIN
PORTRAIT_USABLE_H = PAGE_SIZE[1] - TOP_MARGIN - BOTTOM_MARGIN
LANDSCAPE_USABLE_W = landscape(PAGE_SIZE)[0] - LEFT_MARGIN - RIGHT_MARGIN
LANDSCAPE_USABLE_H = landscape(PAGE_SIZE)[1] - TOP_MARGIN - BOTTOM_MARGIN

_HEADER_MARK = 10.0
_HEADER_BASELINE = 0.44 * inch  # below the top edge
_HEADER_RULE = 0.56 * inch  # below the top edge
_FOOTER_BASELINE = 0.36 * inch
_CHROME_SIZE = 7.0


class _ReportCanvas(Canvas):
    """A canvas that defines its page-number forms when it saves."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._pages_shown = 0
        self._page_refs: dict[str, int] = {}
        self._ref_forms: dict[str, tuple[str, str, float, object]] = {}
        self._page_forms: dict[int, tuple[str, float, object]] = {}

    # -- recording -------------------------------------------------------------

    def record_page_ref(self, key: str) -> None:
        self._page_refs.setdefault(key, self.getPageNumber())

    def page_ref_form(self, key: str, font: str, size: float, colour) -> str:
        name = f"tsbPageRef{len(self._ref_forms)}"
        for existing, (ref_key, _font, _size, _colour) in self._ref_forms.items():
            if ref_key == key:
                return existing
        self._ref_forms[name] = (key, font, size, colour)
        return name

    def page_label_form(self, font: str, size: float, colour) -> str:
        number = self.getPageNumber()
        self._page_forms[number] = (font, size, colour)
        return f"tsbPageLabel{number}"

    # -- output ----------------------------------------------------------------

    def showPage(self) -> None:  # noqa: N802 - ReportLab API
        self._pages_shown += 1
        super().showPage()

    def save(self) -> None:
        total = self._pages_shown
        for number, (font, size, colour) in self._page_forms.items():
            self._right_aligned_form(f"tsbPageLabel{number}", f"Page {number} of {total}", font, size, colour)
        for name, (key, font, size, colour) in self._ref_forms.items():
            page = self._page_refs.get(key)
            self._right_aligned_form(name, str(page) if page else "", font, size, colour)
        super().save()

    def _right_aligned_form(self, name: str, text: str, font: str, size: float, colour) -> None:
        # A form inherits the graphics state it is drawn in, character
        # spacing included, so it sets its own rather than trusting the page.
        self.beginForm(name, lowerx=-400, lowery=-size, upperx=2, uppery=size * 2)
        width = self.stringWidth(text, font, size)
        text_object = self.beginText(-width, 0)
        text_object.setFont(font, size)
        text_object.setCharSpace(0)
        text_object.setWordSpace(0)
        text_object.setFillColor(colour)
        text_object.textOut(text)
        self.drawText(text_object)
        self.endForm()


class _ReportDoc(BaseDocTemplate):
    """Document with portrait + landscape templates and the report chrome."""

    def __init__(
        self,
        path: Path,
        *,
        title: str,
        footer_label: str,
    ) -> None:
        styles.ensure_fonts()
        self.page_count = 0
        self._title = title
        self._footer_label = footer_label

        super().__init__(
            str(path),
            pagesize=PAGE_SIZE,
            leftMargin=LEFT_MARGIN,
            rightMargin=RIGHT_MARGIN,
            topMargin=TOP_MARGIN,
            bottomMargin=BOTTOM_MARGIN,
            title=title,
            author="Tomography Session Browser",
            creator="Tomography Session Browser",
            subject="Tomography session report",
        )

        portrait_frame = Frame(
            self.leftMargin,
            self.bottomMargin,
            self.width,
            self.height,
            id="portrait_content",
            leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
        )
        landscape_size = landscape(PAGE_SIZE)
        landscape_frame = Frame(
            self.leftMargin,
            self.bottomMargin,
            landscape_size[0] - self.leftMargin - self.rightMargin,
            landscape_size[1] - self.topMargin - self.bottomMargin,
            id="landscape_content",
            leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
        )

        self.addPageTemplates(
            [
                PageTemplate(
                    id="portrait",
                    frames=[portrait_frame],
                    pagesize=PAGE_SIZE,
                    onPage=self._draw_chrome,
                ),
                PageTemplate(
                    id="landscape",
                    frames=[landscape_frame],
                    pagesize=landscape_size,
                    onPage=self._draw_chrome,
                ),
            ]
        )

    def build(self, flowables, **kwargs) -> None:  # noqa: D401 - ReportLab API
        kwargs.setdefault("canvasmaker", _ReportCanvas)
        super().build(flowables, **kwargs)

    def _draw_chrome(self, canvas: Canvas, doc: BaseDocTemplate) -> None:
        page_w, page_h = canvas._pagesize
        left = self.leftMargin
        right = page_w - self.rightMargin
        canvas.saveState()

        # Header: the app mark and name at the left, the report title at the
        # right, a hairline under both.
        baseline = page_h - _HEADER_BASELINE
        draw_brand_mark(canvas, left, baseline - 2.2, _HEADER_MARK)
        name_x = left + _HEADER_MARK + 5
        canvas.setFillColor(styles.TEXT_STRONG)
        canvas.setFont(styles.SANS_SEMIBOLD, 8.4)
        canvas.drawString(name_x, baseline, "Tomography")
        product_x = name_x + styles.string_width("Tomography", styles.SANS_SEMIBOLD, 8.4) + 5
        # Tracking is graphics state: keep it inside its own save/restore.
        canvas.saveState()
        text = canvas.beginText(product_x, baseline + 0.4)
        text.setFont(styles.MONO, 5.6)
        text.setCharSpace(0.7)
        text.setFillColor(styles.TEXT_MUTED)
        text.textOut("SESSION BROWSER")
        canvas.drawText(text)
        canvas.restoreState()
        product_w = styles.string_width("SESSION BROWSER", styles.MONO, 5.6) + 0.7 * 14

        title_room = right - (product_x + product_w) - 24
        canvas.setFillColor(styles.TEXT_MUTED)
        canvas.setFont(styles.SANS, _CHROME_SIZE)
        canvas.drawRightString(right, baseline, fit_text(self._title, styles.SANS, _CHROME_SIZE, title_room))

        canvas.setStrokeColor(styles.BORDER)
        canvas.setLineWidth(styles.HAIRLINE)
        rule_y = page_h - _HEADER_RULE
        canvas.line(left, rule_y, right, rule_y)

        # Footer: the project label at the left, "Page N of M" at the right.
        canvas.setFillColor(styles.TEXT_MUTED)
        canvas.setFont(styles.SANS, _CHROME_SIZE)
        canvas.drawString(left, _FOOTER_BASELINE, fit_text(self._footer_label, styles.SANS, _CHROME_SIZE, right - left - 80))
        page_form = getattr(canvas, "page_label_form", None)
        if page_form is not None:
            name = page_form(styles.SANS, _CHROME_SIZE, styles.TEXT_MUTED)
            canvas.saveState()
            canvas.translate(right, _FOOTER_BASELINE)
            canvas.doForm(name)
            canvas.restoreState()
        else:
            canvas.drawRightString(right, _FOOTER_BASELINE, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    def afterPage(self) -> None:
        super().afterPage()
        self.page_count = self.page


def write_pdf(
    output_path: Path,
    flowables: Sequence[Flowable],
    *,
    title: str,
    footer_label: str,
) -> int:
    """Render ``flowables`` to ``output_path`` and return the page count."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = _ReportDoc(output_path, title=title, footer_label=footer_label)
    doc.build(list(flowables))
    return doc.page_count or 1


__all__ = [
    "LANDSCAPE_USABLE_H",
    "LANDSCAPE_USABLE_W",
    "PAGE_SIZE",
    "PORTRAIT_USABLE_H",
    "PORTRAIT_USABLE_W",
    "write_pdf",
]
