"""Page templates and document chrome for the session report.

Owns:

* page geometry (portrait + landscape templates)
* margins
* the running header (report title) and footer (project label + page number)

The generator submits a flat list of flowables. Inserting
``NextPageTemplate("landscape")`` (and back to ``"portrait"``) lets it
flip orientation for image-heavy sections without re-implementing the
rest of the chrome.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER, landscape
from reportlab.lib.units import inch
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import BaseDocTemplate, Flowable, Frame, PageTemplate


_HEADER_FOOTER_FONT = ("Helvetica", 8)
_HEADER_FOOTER_FONT_BOLD = ("Helvetica-Bold", 8)


class _ReportDoc(BaseDocTemplate):
    """Document with portrait + landscape templates and a custom chrome."""

    def __init__(
        self,
        path: Path,
        *,
        title: str,
        footer_label: str,
    ) -> None:
        self.page_count = 0
        self._title = title
        self._footer_label = footer_label

        super().__init__(
            str(path),
            pagesize=LETTER,
            leftMargin=0.6 * inch,
            rightMargin=0.6 * inch,
            topMargin=0.7 * inch,
            bottomMargin=0.6 * inch,
            title=title,
            author="Tomography Session Browser",
        )

        portrait_frame = Frame(
            self.leftMargin,
            self.bottomMargin,
            self.width,
            self.height,
            id="portrait_content",
        )
        landscape_size = landscape(LETTER)
        landscape_frame = Frame(
            self.leftMargin,
            self.bottomMargin,
            landscape_size[0] - self.leftMargin - self.rightMargin,
            landscape_size[1] - self.topMargin - self.bottomMargin,
            id="landscape_content",
        )

        self.addPageTemplates(
            [
                PageTemplate(
                    id="portrait",
                    frames=[portrait_frame],
                    pagesize=LETTER,
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

    def _draw_chrome(self, canvas: Canvas, doc: BaseDocTemplate) -> None:
        page_w, page_h = canvas._pagesize
        canvas.saveState()

        # Header — title flush left, thin separator under it.
        canvas.setFont(*_HEADER_FOOTER_FONT_BOLD)
        canvas.setFillColor(colors.HexColor("#0f172a"))
        canvas.drawString(self.leftMargin, page_h - 0.4 * inch, self._title)
        canvas.setStrokeColor(colors.HexColor("#cbd5e1"))
        canvas.setLineWidth(0.4)
        canvas.line(
            self.leftMargin,
            page_h - 0.46 * inch,
            page_w - self.rightMargin,
            page_h - 0.46 * inch,
        )

        # Footer — left: project/session label, right: page number.
        canvas.setFont(*_HEADER_FOOTER_FONT)
        canvas.setFillColor(colors.HexColor("#475569"))
        canvas.drawString(self.leftMargin, 0.35 * inch, self._footer_label)
        canvas.drawRightString(
            page_w - self.rightMargin,
            0.35 * inch,
            f"Page {canvas.getPageNumber()}",
        )
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


__all__ = ["write_pdf"]
