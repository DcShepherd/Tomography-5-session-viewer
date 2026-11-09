"""Atomic, explicitly scoped contact sheets from frozen display recipes."""
from dataclasses import asdict
from collections.abc import Callable
import json
import os
from pathlib import Path
import uuid
from xml.sax.saxutils import escape

from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph

from tomography_session_browser.parsers.volume_header import fingerprint
from tomography_session_browser.presentation.tomogram_report_scope import ContactSheetScope, MAX_CONTACT_CAPTURES
from tomography_session_browser.reports.styles import ensure_fonts, SANS, TEXT, TEXT_MUTED, ACCENT, BORDER
from tomography_session_browser.services.volume.memory import VOLUME_MEMORY, MIB
from tomography_session_browser.services.volume.render import render_xy
from tomography_session_browser.services.output_boundary import check_output_path, require_output_directory

PAGE_SIZE = A4
SIDE_MARGIN = 36
CELL_W = 252


def column_x(slot: int, page_width: float = PAGE_SIZE[0]) -> float:
    """Left edge of a capture cell: two columns with equal side margins."""
    return SIDE_MARGIN + (slot % 2) * (page_width - 2 * SIDE_MARGIN - CELL_W)


def build_contact_sheet(scope: ContactSheetScope, output: Path, protected_roots: tuple[Path, ...],
                        *, cancelled: Callable[[], bool] = lambda: False) -> Path:
    if cancelled():
        raise InterruptedError("Contact sheet cancelled; no PDF was published.")
    captures = scope.captures
    if not 1 <= len(captures) <= MAX_CONTACT_CAPTURES:
        raise ValueError(f"Select between 1 and {MAX_CONTACT_CAPTURES} captures.")
    roots = (*protected_roots, *(root for c in captures for root in c.protected_roots),
             *(c.volume.path.parent for c in captures))
    folder = require_output_directory(output.parent, roots)
    output = folder / output.name
    if output.suffix.lower() != ".pdf":
        raise ValueError("Choose a PDF filename.")
    if output.exists():
        raise ValueError("The PDF already exists. Choose a new filename.")
    for capture in captures:
        if capture.recipe.interactive or capture.recipe.black is None or capture.recipe.white is None:
            raise ValueError("Only refined captures with fixed levels can enter a contact sheet.")
        if fingerprint(capture.volume.path) != capture.volume.fingerprint:
            raise ValueError("A captured source changed. No contact sheet was published.")
    temporary = folder / f".{uuid.uuid4().hex}.pdf.tmp"
    check_output_path(output, roots)
    check_output_path(temporary, roots)
    # ReportLab retains encoded image streams until save. Reserve the document,
    # not just one transient image, in addition to the renderer's own reservations.
    estimate = 16*MIB + sum(c.recipe.raster_size[0]*c.recipe.raster_size[1]*12 for c in captures)
    try:
        with VOLUME_MEMORY.reserve(estimate, "contact-sheet"):
            _compose(scope, temporary, cancelled)
            for capture in captures:
                if fingerprint(capture.volume.path) != capture.volume.fingerprint:
                    raise ValueError("A captured source changed during export. No contact sheet was published.")
            if cancelled():
                raise InterruptedError("Contact sheet cancelled; no PDF was published.")
            check_output_path(output, roots)
            check_output_path(temporary, roots)
            if os.name == "nt":
                os.rename(temporary, output)
            else:
                os.link(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def _compose(scope: ContactSheetScope, temporary: Path, cancelled: Callable[[], bool] = lambda: False) -> None:
    ensure_fonts()
    body = SANS
    style = ParagraphStyle("contact-caption", fontName=body, fontSize=8, leading=10, textColor=TEXT)
    width, height = PAGE_SIZE
    with temporary.open("xb") as stream:
        pdf = Canvas(stream, pagesize=PAGE_SIZE, pageCompression=1)
        pdf.setTitle("Tomogram capture contact sheet")
        pdf.setAuthor("Tomography Session Browser")
        pdf.setSubject("Frozen display captures; no acquisition report content")
        pdf.setKeywords(json.dumps({"schema": "tomography-session-browser.contact-sheet/1", "captures": [
            {"name": c.name, "source_filename": c.volume.path.name, "version": c.volume.version,
             "source_size": c.volume.fingerprint.size, "source_mtime_ns": c.volume.fingerprint.mtime_ns,
             "grid": asdict(c.volume.grid) if c.volume.grid else None, "recipe": asdict(c.recipe)}
            for c in scope.captures]}, allow_nan=False))
        for page in range(scope.page_count):
            pdf.setFillColor(ACCENT)
            pdf.setFont(body, 16)
            pdf.drawString(36, height-42, "Tomogram capture contact sheet")
            pdf.setFont(body, 9)
            pdf.setFillColor(TEXT_MUTED)
            pdf.drawString(36, height-59, f"{len(scope.captures)} selected captures | Frozen display recipes")
            for slot, capture in enumerate(scope.captures[page*4:page*4+4]):
                x = column_x(slot, width)
                top = height-82-(slot//2)*330
                result = render_xy(capture.volume, capture.recipe, cancelled=cancelled)
                image = Image.fromarray(result.pixels)
                ratio = min(252/image.width, 205/image.height)
                iw, ih = image.width*ratio, image.height*ratio
                pdf.drawImage(ImageReader(image), x+(252-iw)/2, top-205+(205-ih)/2, iw, ih)
                r = capture.recipe
                lines = [f"{page*4+slot+1}. {capture.name} | {capture.volume.version}",
                         capture.volume.path.name,
                         f"{r.axis} plane {r.plane+1} (1-based) | Requested averaging: {r.slices}",
                         f"Crop voxel edges: {', '.join(f'{v:.3f}' for v in r.region)}",
                         f"Levels: {r.black:g} to {r.white:g}",
                         f"LP sigma: {r.lowpass:g} cycles/pixel | HP radius: {r.highpass:g} Fourier pixels",
                         f"Rotation XYZ: {', '.join(f'{v:g}' for v in r.rotation)} degrees",
                         f"Calibration: {result.calibration} | Bar: {result.bar_label or 'not shown'}"]
                paragraph = Paragraph("<br/>".join(escape(line) for line in lines), style)
                _, ph = paragraph.wrap(252, 110)
                if ph > 110:
                    raise ValueError("A caption cannot fit this contact-sheet layout. Use individual snapshots for this capture.")
                paragraph.drawOn(pdf, x, top-214-ph)
                pdf.setStrokeColor(BORDER)
                pdf.rect(x-3, top-326, 258, 329, stroke=1, fill=0)
            pdf.setFillColor(TEXT_MUTED)
            pdf.setFont(body, 8)
            pdf.drawString(36, 24, "Display contact sheet - full recipes embedded in PDF metadata; source folders unchanged.")
            pdf.drawRightString(width-36, 24, f"{page+1} / {scope.page_count}")
            pdf.showPage()
        pdf.save()
        stream.flush()
        os.fsync(stream.fileno())
