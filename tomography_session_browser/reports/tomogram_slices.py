"""Render the central slices of a report (D4 §5.5) on the report worker.

Each member tomogram gets one tile: an encoded 8-bit image, or a skip with its
print reason. The recipe comes from ``slice_recipe`` (the viewer's own
``render_plan``), so the image keeps the viewer's flip, aspect and scale bar.
One volume is read at a time; each render's display lease is dropped at once,
and ``share_planes=False`` keeps the viewer's plane cache. The operating-system
text of a failure goes only to the generation warnings, never to the PDF.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
import io
import json

from PIL import Image

from tomography_session_browser.domain.tomograms import Tomogram
from tomography_session_browser.presentation.report_tomograms import ReportRow
from tomography_session_browser.presentation.tomogram_slices import (
    SKIP_CHANGED, SKIP_CLOUD, SKIP_UNREADABLE, SliceRequest, SliceSkip, slice_recipe,
)
from tomography_session_browser.services.volume.memory import MIB, BudgetExceeded
from tomography_session_browser.services.volume.models import RenderRecipe
from tomography_session_browser.services.volume.render import render_xy
from tomography_session_browser.services.volume.store import SourceUnavailable, VolumeUnreadable, require_unchanged

KEYWORD_SCHEMA = "tomography-session-browser.report-slices/1"
CANCELLED = "Report cancelled. The app did not write a PDF."

#: progress(done, total) after each image.
SliceProgress = Callable[[int, int], None]


@dataclass(frozen=True, slots=True)
class SliceImage:
    request: SliceRequest
    png: bytes
    #: Raster pixels (width, height).
    size: tuple[int, int]
    #: The recipe as rendered, with the resolved black and white levels.
    recipe: RenderRecipe
    calibration: str
    bar_label: str

    @property
    def caption(self) -> str:
        return self.request.caption

    @property
    def tomogram(self) -> Tomogram:
        return self.request.tomogram

    @property
    def version(self) -> str:
        return self.request.volume.version

    @property
    def portrait(self) -> bool:
        return self.request.portrait


Tile = SliceImage | SliceSkip


@dataclass(frozen=True, slots=True)
class ReportSlices:
    """One tile per member row key, in member order."""
    tiles: dict[str, Tile]

    @property
    def shown(self) -> int:
        return sum(isinstance(tile, SliceImage) for tile in self.tiles.values())

    @property
    def skipped(self) -> int:
        return len(self.tiles) - self.shown

    def for_rows(self, rows: tuple[ReportRow, ...]) -> list[Tile]:
        return [self.tiles[row.key] for row in rows if row.key in self.tiles]


def reserved_bytes(plans: dict[str, SliceRequest | SliceSkip]) -> int:
    """Retained encoded tiles and ReportLab's image streams until the PDF is written."""
    rasters = (slice_recipe(plan).raster_size for plan in plans.values() if isinstance(plan, SliceRequest))
    return 16 * MIB + sum(width * height * 2 for width, height in rasters)


def render_slices(plans: dict[str, SliceRequest | SliceSkip], warnings: list[str], *,
                  cancelled: Callable[[], bool] = lambda: False,
                  progress: SliceProgress = lambda done, total: None) -> ReportSlices:
    """Render every request; a cloud-only, changed or unreadable file becomes a skip.

    ``BudgetExceeded`` aborts the report (it is a ``ValueError`` too, so it is
    caught first). Any other error reaches the failed-report path.
    """
    total = sum(isinstance(plan, SliceRequest) for plan in plans.values())
    tiles: dict[str, Tile] = {}
    done = 0
    progress(done, total)
    for key, plan in plans.items():
        if isinstance(plan, SliceSkip):
            tiles[key] = plan
            continue
        if cancelled():
            raise InterruptedError(CANCELLED)
        tiles[key] = render_one(plan, warnings, cancelled)
        done += 1
        progress(done, total)
    return ReportSlices(tiles)


def render_one(request: SliceRequest, warnings: list[str], cancelled: Callable[[], bool]) -> Tile:
    volume = request.volume
    try:
        result = render_xy(volume, slice_recipe(request), cancelled=cancelled,
                           share_planes=False, include_histogram=False)
        try:
            image = Image.fromarray(result.pixels)
            stream = io.BytesIO()
            image.save(stream, format="PNG", optimize=False)
            tile = SliceImage(request, stream.getvalue(), image.size, result.recipe,
                              result.calibration, result.bar_label)
        finally:
            # The display lease belongs to the screen; drop it with the result.
            if result.buffer_lease is not None:
                result.buffer_lease.close()
            del result
        require_unchanged(volume)
        return tile
    except (BudgetExceeded, InterruptedError):
        raise                          # a full budget or a cancel stops the report; neither is a skip
    except SourceUnavailable as error:
        reason = SKIP_CLOUD if error.reason == "cloud" else SKIP_CHANGED
        return _skip(request, reason, error, warnings)
    except (VolumeUnreadable, OSError) as error:
        return _skip(request, SKIP_UNREADABLE, error, warnings)


def _skip(request: SliceRequest, reason: str, error: Exception, warnings: list[str]) -> SliceSkip:
    warnings.append(f"Central slice of {request.tomogram.name} ({request.volume.version}): {reason} {error}")
    return SliceSkip(request.tomogram, request.volume.version, reason, request.portrait)


def slice_keywords(numbered: list[tuple[Tile, str]]) -> str:
    """One record per printed tile, keyed by tile number: no path, row, source or project key."""
    records: dict[str, dict] = {}
    for number, (tile, folder) in enumerate(numbered, start=1):
        if isinstance(tile, SliceSkip):
            records[str(number)] = {"name": tile.tomogram.name, "folder": folder, "version": tile.version,
                                    "skipped": tile.reason}
            continue
        request, volume = tile.request, tile.request.volume
        records[str(number)] = {
            "name": request.tomogram.name, "folder": folder, "source_filename": volume.path.name,
            "version": volume.version, "source_size": volume.fingerprint.size,
            "source_mtime_ns": volume.fingerprint.mtime_ns,
            "grid": asdict(volume.grid) if volume.grid else None, "recipe": asdict(tile.recipe),
            "planes": request.planes, "spacing_source": request.spacing_source,
            "calibration": tile.calibration, "bar": tile.bar_label,
        }
    return json.dumps({"schema": KEYWORD_SCHEMA, "tiles": records}, allow_nan=False)
