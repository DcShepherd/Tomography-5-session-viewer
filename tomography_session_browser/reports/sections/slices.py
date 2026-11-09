"""The central-slice tile block of a Tomograms section (D4 §5.5).

Images are never rotated: each shows the XY plane as the viewer shows it,
fitted inside a cell of one size with its aspect ratio kept. Four columns give
square cells (16 on a page); three columns, used when any volume is higher
than it is wide, give portrait cells (9 on a page). A skipped tile keeps its
cell with "No image" and its print reason.
"""
from __future__ import annotations

from collections.abc import Sequence
import io

from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.platypus import CondPageBreak, Flowable, Paragraph, Spacer, Table, TableStyle

from tomography_session_browser.presentation.report_tomograms import ScopedTomograms, TomogramSection
from tomography_session_browser.presentation.tomogram_slices import SECTION_TEXT, slice_columns
from tomography_session_browser.reports import graphics, styles
from tomography_session_browser.reports.pdf_export import PORTRAIT_USABLE_W
from tomography_session_browser.reports.tomogram_slices import ReportSlices, SliceImage, Tile

GAP = 6.0
ROW_GAP = 8.0
#: Portrait cell proportion from the B5 board: about 164 × 191 pt.
PORTRAIT_RATIO = 191 / 164
NO_IMAGE = "No image"


def cell_size(columns: int) -> tuple[float, float]:
    width = (PORTRAIT_USABLE_W - (columns - 1) * GAP) / columns
    return width, width if columns == 4 else width * PORTRAIT_RATIO


class SliceCell(Flowable):
    """One fixed cell: the image fitted and centred, or "No image" and the reason."""

    def __init__(self, tile: Tile, width: float, height: float) -> None:
        super().__init__()
        self.tile, self.width, self.height = tile, width, height

    def wrap(self, _avail_w, _avail_h):  # noqa: N802
        return self.width, self.height

    def image_box(self) -> tuple[float, float, float, float]:
        assert isinstance(self.tile, SliceImage)
        pixels_w, pixels_h = self.tile.size
        scale = min(self.width / pixels_w, self.height / pixels_h)
        w, h = pixels_w * scale, pixels_h * scale
        return (self.width - w) / 2, (self.height - h) / 2, w, h

    def draw(self) -> None:
        c = self.canv
        c.saveState()
        c.setFillColor(styles.SURFACE_ALT)
        c.rect(0, 0, self.width, self.height, stroke=0, fill=1)
        if isinstance(self.tile, SliceImage):
            x, y, w, h = self.image_box()
            c.drawImage(ImageReader(io.BytesIO(self.tile.png)), x, y, width=w, height=h)
        else:
            reason = Paragraph(f"<b>{NO_IMAGE}</b><br/>{graphics.html_escape(self.tile.reason)}", styles.MUTED_STYLE)
            _, used = reason.wrap(self.width - 12, self.height)
            reason.drawOn(c, 6, (self.height - used) / 2)
        c.setStrokeColor(styles.BORDER_STRONG)
        c.setLineWidth(0.5)
        c.rect(0, 0, self.width, self.height, stroke=1, fill=0)
        c.restoreState()


def tile_label(tile: Tile) -> str:
    return " · ".join(part for part in (tile.tomogram.name, tile.version) if part)


def tile_cell(tile: Tile, width: float, height: float) -> list:
    cell: list = [SliceCell(tile, width, height), Spacer(1, 2),
                  Paragraph(graphics.html_escape(tile_label(tile)), styles.SMALL_STYLE)]
    if isinstance(tile, SliceImage):
        cell.append(Paragraph(graphics.html_escape(tile.caption), styles.MUTED_STYLE))
    return cell


def slice_grid(tiles: Sequence[Tile]) -> Table:
    columns = slice_columns(tiles)
    width, height = cell_size(columns)
    cells = [tile_cell(tile, width, height) for tile in tiles]
    rows = [cells[start:start + columns] for start in range(0, len(cells), columns)]
    rows[-1] += [""] * (columns - len(rows[-1]))
    table = Table(rows, colWidths=[width + GAP] * (columns - 1) + [width], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-2, -1), GAP),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), ROW_GAP),
    ]))
    return table


def central_slices(section: TomogramSection, slices: ReportSlices) -> list:
    """The block after the tables: heading, the rule said once, then the tiles."""
    tiles = slices.for_rows(section.members)
    if not tiles:
        return []
    height = cell_size(slice_columns(tiles))[1]
    return [CondPageBreak(height + 0.6 * inch),
            Paragraph("<b>Central slices</b>", styles.SMALL_STYLE), Spacer(1, 2),
            Paragraph(graphics.html_escape(SECTION_TEXT), styles.MUTED_STYLE), Spacer(1, 4),
            slice_grid(tiles)]


def numbered_tiles(scoped: ScopedTomograms, slices: ReportSlices) -> list[tuple[Tile, str]]:
    """Each printed tile in print order with its folder label, for the keyword record."""
    labels = {source.source.id: source.label for source in scoped.sources}
    return [(tile, labels.get(tile.tomogram.source_id, ""))
            for section in scoped.sections for tile in slices.for_rows(section.members)]


def cover_cards(slices: ReportSlices) -> list[graphics.StatCardData]:
    return [graphics.StatCardData("Central slices", f"{slices.shown:,}", [], []),
            graphics.StatCardData(NO_IMAGE, f"{slices.skipped:,}", [], [])]
