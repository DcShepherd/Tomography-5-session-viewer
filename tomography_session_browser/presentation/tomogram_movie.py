"""Movie export view model: the frozen view, the dialog's settings, the export request, section lines and notes.

The dialog, its preview and the worker all build from these functions, so each
section's line always describes the movie that is saved (plan §1.3–1.5, §2.2–2.4).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path

from tomography_session_browser.domain.tomograms import Volume
from tomography_session_browser.services.movie.compose import NORMALS, bar_notice
from tomography_session_browser.services.movie.encoder import WIDELY_PLAYED, h264_level
from tomography_session_browser.services.movie.models import (COMPRESSION, FRAMES_ONLY, MOVIE, MOVIE_AND_FRAMES, SAVES,
                                                              MovieLabels, MovieOutput, MoviePane, MovieRequest)
from tomography_session_browser.services.movie.output import automatic_name
from tomography_session_browser.services.movie.sizing import (MATCH, NATIVE, PANE_GAP, SHAPES, SIZES, ContentLayout,
                                                              FrameGeometry, PaneBox, SizeRefused, check_limits,
                                                              frame_geometry, with_title)
from tomography_session_browser.services.movie.timeline import (BACK, BOUNCE, ChosenHold, MovieTimeline,
                                                                TimelineSettings, band_defaults, build_timeline,
                                                                unused_reason)
from tomography_session_browser.services.volume.models import RenderRecipe
from tomography_session_browser.services.volume.render import calibrated_spacing, plane_geometry, plane_spacing
from tomography_session_browser.services.volume.scale_bar import ScaleBarStyle

ACTIVE, ORTHO = "Active pane", "Ortho"
CURRENT_VIEW, FULL_PLANES = "Current view", "Full planes"
FRAMINGS = (CURRENT_VIEW, FULL_PLANES)
ORTHO_REASON = "Switch the viewer to Ortho (O) to export three planes."
NO_PIXEL_SIZE = "No scale bar: the file has no pixel size. Set the spacing in Image settings, Calibration, to add one."
NO_FFMPEG = "Not available: FFmpeg is not installed"
FFMPEG_NOTE = ("MP4 export is not available. No FFmpeg with the H.264 encoder (libx264) was found. "
               "You can save PNG frames now. To save MP4 files, install FFmpeg, "
               "then start the app again. Help, Files, has the steps.")
#: Approximate PNG bytes per pixel of a composed tomogram frame.
PNG_BYTES_PER_PIXEL = 1.3
SHORT_MOVIE = 3.
ERROR, WARNING, INFORMATION = "error", "warning", "information"


@dataclass(frozen=True, slots=True)
class FrozenPane:
    """A pane as the viewer showed it: the accepted recipe (levels resolved), the pane's
    logical size, and where its image sat in it."""
    recipe: RenderRecipe
    pane_size: tuple[int, int]
    position: tuple[int, int]


@dataclass(frozen=True, slots=True)
class FrozenView:
    row_key: str
    tomogram: str
    volume: Volume
    layout: str
    axis: str
    panes: tuple[FrozenPane, ...]
    cross_xyz: tuple[int, int, int]
    linked: bool = True
    show_bar: bool = True
    band_rate: int = 10
    band_end: str = "Stop at end"

    def pane(self, axis: str) -> FrozenPane:
        return next(pane for pane in self.panes if pane.recipe.axis == axis)

    @property
    def axes(self) -> tuple[str, ...]:
        return tuple(pane.recipe.axis for pane in self.panes)

    @property
    def oblique(self) -> bool:
        recipe = self.pane(self.axis).recipe
        return any(recipe.rotation) or recipe.pivot_xyz != recipe.pivot_grid_xyz

    @property
    def calibrated(self) -> bool:
        return calibrated_spacing(self.volume, self.pane(self.axis).recipe.spacing_override) is not None

    def planes(self, axis: str) -> int:
        return plane_geometry(self.volume, axis)[2]

    def current(self, axis: str) -> int:
        """The plane that the viewer showed when the dialog opened: the value of Current."""
        if axis in self.axes:
            return self.pane(axis).recipe.plane
        return self.cross_xyz["XYZ".index(NORMALS[axis])]


@dataclass(frozen=True, slots=True)
class MovieSettings:
    view: str
    sweep: str
    timeline: TimelineSettings
    framing: str = CURRENT_VIEW
    size: str = NATIVE
    shape: str = MATCH
    scale_bar: bool = True
    names: bool = True
    numbers: bool = True
    lines: bool = False
    title_on: bool = False
    title: str = ""
    save: str = MOVIE
    compression: str = "Balanced quality"
    folder: str = ""
    #: Empty for the automatic name.
    name: str = ""

    @property
    def title_text(self) -> str:
        return self.title.strip() if self.title_on else ""


#: Remembered after a successful publication (D9); everything else comes from the viewer and the band.
REMEMBERED = ("save", "compression", "size", "shape", "framing", "loop_ready", "names", "numbers", "lines")
CHOICES = {"save": SAVES, "compression": tuple(COMPRESSION), "size": tuple(SIZES), "shape": tuple(SHAPES),
           "framing": FRAMINGS}


def remembered(settings: MovieSettings) -> dict:
    values = {key: getattr(settings.timeline if key == "loop_ready" else settings, key) for key in REMEMBERED}
    values["folder"] = settings.folder
    return values


def apply_remembered(settings: MovieSettings, data: object) -> MovieSettings:
    """Take only known, valid values; an old or damaged entry falls back to the defaults."""
    if not isinstance(data, dict):
        return settings
    changes: dict = {}
    for key, choices in CHOICES.items():
        if data.get(key) in choices:
            changes[key] = data[key]
    for key in ("names", "numbers", "lines"):
        if type(data.get(key)) is bool:
            changes[key] = data[key]
    if isinstance(data.get("folder"), str) and data["folder"]:
        changes["folder"] = data["folder"]
    settings = replace(settings, **changes)
    if type(data.get("loop_ready")) is bool:
        settings = replace(settings, timeline=replace(settings.timeline, loop_ready=data["loop_ready"]))
    return settings


def default_settings(view: FrozenView, memory: object = None, *, folder: str = "",
                     movie_available: bool = True) -> MovieSettings:
    fps, end = band_defaults(view.band_rate, view.band_end)
    axis = view.axis
    timeline = TimelineSettings(0, view.planes(axis) - 1, 1, fps, end, True)
    settings = MovieSettings(ORTHO if view.layout == "Ortho" else ACTIVE, axis, timeline,
                             scale_bar=view.show_bar and view.calibrated, folder=folder)
    settings = apply_remembered(settings, memory)
    if not movie_available:
        settings = replace(settings, save=FRAMES_ONLY)
    return settings


@dataclass(frozen=True, slots=True)
class LastSettings:
    """A tomogram's last settings, from Export or Cancel: the movie owner holds them in memory, so they end when the app closes. No file has them."""
    view: FrozenView
    settings: MovieSettings


def from_last_settings(last: LastSettings, view: FrozenView, *, movie_available: bool = True) -> tuple[MovieSettings, str]:
    """The settings for a new dialog on any version of that tomogram, and a note if some of them cannot apply.

    Plane numbers mean the same planes only on the same grid and the same swept
    pane, the rule by which the viewer's versions share a view. Otherwise the
    movie uses all planes and says so: it never moves a range or a hold to a
    nearby plane. A typed name stays with its version, so that two versions do not
    take one name.
    """
    saved, timing = last.settings, last.settings.timeline
    if view.layout != last.view.layout:
        choice, sweep = (ORTHO if view.layout == "Ortho" else ACTIVE), view.axis
    else:  # An active-pane movie sweeps the pane that is active now.
        choice, sweep = saved.view, (view.axis if saved.view == ACTIVE else saved.sweep)
    planes = view.planes(sweep)
    same_grid = view.volume.grid == last.view.volume.grid
    note = ""
    if not same_grid or sweep != saved.sweep:
        held = bool(timing.chosen)
        whole = (timing.first, timing.last) == (0, last.view.planes(saved.sweep) - 1)
        if not same_grid:
            note = f"These settings are from a version with a different grid. The movie uses all {planes} planes, so it may not match."
        elif held or not whole:
            note = f"These settings are from a movie of a different pane. It uses all {planes} planes."
        if note and held:
            note += " Holds at chosen frames are removed."
        timing = replace(timing, first=0, last=planes - 1, step=min(timing.step, max(1, planes - 1)), chosen=())
    settings = replace(saved, view=choice, sweep=sweep, timeline=timing, scale_bar=saved.scale_bar and view.calibrated,
                       name=saved.name if view.volume.id == last.view.volume.id else "",
                       save=saved.save if movie_available else FRAMES_ONLY)
    return settings, note


def change_view(settings: MovieSettings, view: FrozenView, choice: str) -> MovieSettings:
    """A change of View resets the sweep and its range, and clears holds at chosen frames."""
    if choice == ORTHO and view.layout != "Ortho":
        raise ValueError(ORTHO_REASON)
    return change_sweep(replace(settings, view=choice, lines=settings.lines and choice == ORTHO), view, view.axis)


def change_sweep(settings: MovieSettings, view: FrozenView, axis: str) -> MovieSettings:
    planes = view.planes(axis)
    return replace(settings, sweep=axis, timeline=replace(settings.timeline, first=0, last=planes - 1, chosen=()))


def hold_label(axis: str, hold: ChosenHold) -> str:
    return f"{NORMALS[axis]} {hold.plane + 1}" + (" (back)" if hold.direction == BACK else "")


def content_layout(view: FrozenView, settings: MovieSettings) -> ContentLayout:
    """Panes in logical units: the viewer's own for Current view; a fit of the full planes otherwise."""
    sweep = settings.sweep
    if settings.view == ACTIVE:
        if sweep != view.axis:
            raise ValueError("A single-pane movie sweeps its own pane.")
        pane = view.pane(sweep)
        if settings.framing == CURRENT_VIEW:
            width, height = pane.recipe.output_size
            box = PaneBox(sweep, (0., 0., width, height), (0., 0., width, height), pane.recipe.region)
            layout = ContentLayout((float(width), float(height)), (box,), sweep)
        else:
            physical = physical_size(view, sweep)
            scale = min(pane.pane_size[0] / physical[0], pane.pane_size[1] / physical[1])
            width, height = physical[0] * scale, physical[1] * scale
            nx, ny, _ = plane_geometry(view.volume, sweep)
            box = PaneBox(sweep, (0., 0., width, height), (0., 0., width, height), (0., 0., float(nx), float(ny)))
            layout = ContentLayout((width, height), (box,), sweep)
    else:
        if set(view.axes) != {"XY", "XZ", "YZ"}:
            raise ValueError(ORTHO_REASON)
        layout = ortho_layout(view, sweep, settings.framing)
    return with_title(layout) if settings.title_text else layout


def physical_size(view: FrozenView, axis: str) -> tuple[float, float]:
    spacing = calibrated_spacing(view.volume, view.pane(view.axis).recipe.spacing_override) or (1., 1., 1.)
    nx, ny, _ = plane_geometry(view.volume, axis)
    sx, sy = plane_spacing(spacing, axis)
    return nx * sx, ny * sy


def ortho_layout(view: FrozenView, sweep: str, framing: str) -> ContentLayout:
    """XZ above XY, YZ to its right, as the viewer arranges them; the corner sits above YZ."""
    gap = float(PANE_GAP)
    if framing == CURRENT_VIEW:
        sizes = {axis: view.pane(axis).pane_size for axis in ("XY", "XZ", "YZ")}
        left, right = max(sizes["XY"][0], sizes["XZ"][0]), sizes["YZ"][0]
        top, bottom = sizes["XZ"][1], max(sizes["XY"][1], sizes["YZ"][1])
        origins = {"XZ": (0., 0.), "XY": (0., top + gap), "YZ": (left + gap, top + gap)}
        boxes = []
        for axis in ("XZ", "XY", "YZ"):
            pane = view.pane(axis)
            x, y = origins[axis]
            width, height = pane.recipe.output_size
            px, py = pane.position
            pane_width, pane_height = pane.pane_size
            boxes.append(PaneBox(axis, (x, y, float(pane_width), float(pane_height)),
                                 (x + px, y + py, float(width), float(height)), pane.recipe.region))
        return ContentLayout((left + gap + right, top + gap + bottom), tuple(boxes), sweep)
    x, y = physical_size(view, "XY")
    z = physical_size(view, "YZ")[0]
    area = (sum(view.pane(axis).pane_size[0] for axis in ("XY", "YZ")),
            sum(view.pane(axis).pane_size[1] for axis in ("XZ", "XY")))
    scale = min(max(1., area[0] - gap) / (x + z), max(1., area[1] - gap) / (y + z))
    x, y, z = x * scale, y * scale, z * scale
    rects = {"XZ": (0., 0., x, z), "XY": (0., z + gap, x, y), "YZ": (x + gap, z + gap, z, y)}
    boxes = []
    for axis in ("XZ", "XY", "YZ"):
        nx, ny, _ = plane_geometry(view.volume, axis)
        boxes.append(PaneBox(axis, rects[axis], rects[axis], (0., 0., float(nx), float(ny))))
    return ContentLayout((x + gap + z, z + gap + y), tuple(boxes), sweep)


def geometry_for(view: FrozenView, settings: MovieSettings) -> tuple[ContentLayout, FrameGeometry]:
    layout = content_layout(view, settings)
    geometry = frame_geometry(layout, settings.size, settings.shape)
    check_limits(geometry, layout, settings.size, settings.shape)
    return layout, geometry


def movie_panes(view: FrozenView, settings: MovieSettings, geometry: FrameGeometry) -> tuple[MoviePane, ...]:
    """Frozen recipes at the export raster. The bar is drawn after rendering, at the export ratio:
    one on the sweeping pane when the panes share a scale, else one on each pane."""
    shared = settings.view == ACTIVE or view.linked or settings.framing == FULL_PLANES
    panes = []
    for raster in geometry.panes:
        frozen = view.pane(raster.axis).recipe
        recipe = replace(frozen, region=raster.region, output_size=raster.image[2:], pixel_ratio=1., show_bar=False,
                         interactive=False, native_sampling=False)
        if raster.axis == settings.sweep:
            recipe = replace(recipe, plane=settings.timeline.first)
        bar = settings.scale_bar and view.calibrated and (raster.axis == settings.sweep or not shared)
        panes.append(MoviePane(recipe, raster, bar))
    return tuple(panes)


def automatic(view: FrozenView, settings: MovieSettings) -> str:
    plane = f"ortho-{settings.sweep}" if settings.view == ORTHO else settings.sweep
    return automatic_name(view.tomogram, view.volume.version, plane, settings.timeline.first, settings.timeline.last)


def output_name(view: FrozenView, settings: MovieSettings) -> str:
    return settings.name.strip() or automatic(view, settings)


def build_request(view: FrozenView, settings: MovieSettings, job: int, roots: tuple[Path, ...]) -> MovieRequest:
    timeline = build_timeline(settings.timeline, view.planes(settings.sweep))
    _, geometry = geometry_for(view, settings)
    panes = movie_panes(view, settings, geometry)
    if not settings.folder:
        raise ValueError("Select a folder for the movie.")
    style = view.pane(view.axis).recipe.scale_bar
    for pane in panes:
        if pane.scale_bar and bar_notice(view.volume, pane, style, geometry.ratio):
            raise ValueError(bar_message(pane))
    output = MovieOutput(settings.save, settings.compression, Path(settings.folder), output_name(view, settings), roots)
    labels = MovieLabels(settings.names, settings.numbers, settings.lines and settings.view == ORTHO, settings.title_text)
    return MovieRequest(job, view.tomogram, view.volume, "Ortho" if settings.view == ORTHO else "Single",
                        settings.sweep, panes, geometry, timeline, labels, output, style, view.cross_xyz, view.oblique)


def bar_message(pane: MoviePane) -> str:
    return (f"The scale bar does not fit the {pane.recipe.axis} pane, which is {pane.raster.image[2]} pixels wide. "
            "Select a larger size or Full planes, or turn off Scale bar.")


def duration_text(seconds: float) -> str:
    return f"{seconds:.1f} s" if seconds < 60 else f"{int(seconds // 60)} min {seconds % 60:.1f} s"


def ordinal(value: int) -> str:
    return f"{value}{'th' if 11 <= value % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(value % 10, 'th')}"


def step_text(step: int) -> str:
    return "every plane" if step == 1 else f"every {ordinal(step)} plane"


def byte_text(count: float) -> str:
    return f"{count / 1e9:.1f} GB" if count >= 1e9 else f"{max(1, round(count / 1e6))} MB"


def file_size_text(count: float) -> str:
    """A saved file's size, as the progress window states it: "18.4 MB"."""
    return f"{count / 1e9:.1f} GB" if count >= 1e9 else f"{max(.1, count / 1e6):.1f} MB"


#: The dialog's sections, in order. ``MovieAssessment.lines`` has one line for each.
SECTIONS = ("Planes", "Timing", "Picture", "File")


@dataclass(frozen=True, slots=True)
class SectionLine:
    """A section of the dialog while it is closed: its settings in one sentence, and its problem in a few words."""
    text: str
    problem: str = ""
    #: ERROR or WARNING when there is a problem.
    kind: str = ""


@dataclass(frozen=True, slots=True)
class MovieAssessment:
    timeline: MovieTimeline | None
    geometry: FrameGeometry | None
    lines: tuple[SectionLine, SectionLine, SectionLine, SectionLine]
    notes: tuple[tuple[str, str], ...]
    #: Why Export is off, or "".
    blocked: str
    size_text: str
    estimate: str
    name: str
    path_text: str

    @property
    def errors(self) -> tuple[str, ...]:
        return tuple(text for kind, text in self.notes if kind == ERROR)


def size_parts(geometry: FrameGeometry) -> tuple[str, str]:
    """The frame size and its scale, as the Size row writes them: ("656 × 924", "one voxel per pixel")."""
    return f"{geometry.frame[0]} × {geometry.frame[1]}", geometry.scale_note


def extension(settings: MovieSettings) -> str:
    """After the name: the MP4 file's suffix, or the separator of the folder that holds the PNG frames."""
    return ".mp4" if settings.save == MOVIE else os.sep


def holds_text(timing: TimelineSettings) -> str:
    """Every hold that the settings ask for; a hold that the movie cannot use has its own words on the line."""
    count = len(timing.chosen) + sum((timing.start_hold > 0, timing.bounce and timing.turn_hold > 0,
                                      timing.end_available and timing.end_hold > 0))
    return "no holds" if count == 0 else "1 hold" if count == 1 else f"{count} holds"


def planes_line(settings: MovieSettings, timeline: MovieTimeline | None) -> SectionLine:
    timing = settings.timeline
    pane = f"Ortho, sweeping {settings.sweep}" if settings.view == ORTHO else settings.sweep
    text = " · ".join((pane, f"planes {timing.first + 1}–{timing.last + 1}", step_text(timing.step)))
    if timeline is not None:
        return SectionLine(text)
    return SectionLine(text, "Too few planes" if timing.first == timing.last else "Not valid", ERROR)


def timing_line(settings: MovieSettings, timeline: MovieTimeline | None) -> SectionLine:
    timing = settings.timeline
    end = ("bounce, loop-ready" if timing.loop_ready else "bounce") if timing.bounce else "single pass"
    length = (duration_text(timeline.duration),) if timeline is not None else ()
    text = " · ".join((f"{timing.fps} fps", end, *length, holds_text(timing)))
    unused = len(timeline.unused) if timeline is not None else 0
    if not unused:
        return SectionLine(text)
    return SectionLine(text, "1 hold not used" if unused == 1 else f"{unused} holds not used", WARNING)


def picture_line(view: FrozenView, settings: MovieSettings, geometry: FrameGeometry | None, problem: str) -> SectionLine:
    size = f"{settings.size} {size_parts(geometry)[0]}" if geometry is not None else settings.size
    shown = [label for label, on in (("scale bar", settings.scale_bar and view.calibrated), ("plane names", settings.names),
                                     ("plane numbers", settings.numbers),
                                     ("intersection lines", settings.lines and settings.view == ORTHO),
                                     ("title", bool(settings.title_text))) if on]
    parts = [size]
    if settings.shape != MATCH:
        parts.append(settings.shape.lower())
    if settings.framing == FULL_PLANES:
        parts.append("full planes")
    parts.append(", ".join(shown) if shown else "no labels")
    return SectionLine(" · ".join(parts), problem, ERROR if problem else "")


def file_line(settings: MovieSettings, name: str, movie_available: bool) -> SectionLine:
    movie = settings.save != FRAMES_ONLY
    quality = (settings.compression.lower(),) if movie else ()
    text = " · ".join((settings.save, *quality, name + extension(settings)))
    if not settings.folder:
        return SectionLine(text, "No folder", ERROR)
    if not movie_available:
        return SectionLine(text, "No FFmpeg", ERROR if movie else WARNING)
    return SectionLine(text)


def assess(view: FrozenView, settings: MovieSettings, *, movie_available: bool = True) -> MovieAssessment:
    """Everything the dialog shows about the movie, from one set of settings."""
    timing = settings.timeline
    notes: list[tuple[str, str]] = []
    picture_problem = ""
    timeline = geometry = None
    try:
        timeline = build_timeline(timing, view.planes(settings.sweep))
    except ValueError as exc:
        notes.append((ERROR, str(exc)))
    size_text = ""
    try:
        _, geometry = geometry_for(view, settings)
        size_text = " ".join(size_parts(geometry))
    except ValueError as exc:
        notes.append((ERROR, str(exc)))
        picture_problem = "Too large" if isinstance(exc, SizeRefused) else "No usable size"
    if geometry is not None and settings.scale_bar and view.calibrated:
        style = view.pane(view.axis).recipe.scale_bar
        for pane in movie_panes(view, settings, geometry):
            if pane.scale_bar and bar_notice(view.volume, pane, style, geometry.ratio):
                notes.append((ERROR, bar_message(pane)))
                picture_problem = "Scale bar does not fit"
    movie = settings.save != FRAMES_ONLY
    if movie and not movie_available:
        notes.append((ERROR, FFMPEG_NOTE))
    if timeline is not None:
        if (short := timeline.short_step) is not None:
            notes.append((INFORMATION, f"The last step is shorter: {short[0] + 1} → {short[1] + 1}."
                          + (" It occurs in both directions." if timing.bounce else "")))
        for hold in timeline.unused:
            notes.append((WARNING, unused_reason(hold, timing, NORMALS[settings.sweep])))
        if timeline.duration < SHORT_MOVIE:
            notes.append((INFORMATION, f"Short movie: {duration_text(timeline.duration)}."))
    if geometry is not None and movie:
        level = h264_level(*geometry.frame, timing.fps)
        if level is None:
            notes.append((ERROR, f"Too large for an H.264 movie: {geometry.frame[0]} × {geometry.frame[1]}. "
                                 "Select HD, Full HD or 4K, or save PNG frames only."))
            picture_problem = picture_problem or "Too large"
        elif float(level) > float(WIDELY_PLAYED):
            notes.append((WARNING, "4K above 30 fps may not play in PowerPoint on some computers." if settings.size == "4K"
                          else f"This size and rate need H.264 level {level}, which may not play in PowerPoint on some computers."))
    estimate = ""
    if settings.save != MOVIE and timeline is not None and geometry is not None:
        estimate = f"PNG frames: approximately {byte_text(timeline.count * geometry.frame[0] * geometry.frame[1] * PNG_BYTES_PER_PIXEL)}."
    name = output_name(view, settings)
    count = timeline.count if timeline is not None else 0
    # A movie alone is one file, which Folder and Name state. A folder of frames holds more than they can say.
    path_text = ""
    if settings.save == MOVIE_AND_FRAMES:
        path_text = f"Saves the folder {name}{os.sep} with the MP4, frames{os.sep} ({count} PNG files) and manifest.json"
    elif settings.save == FRAMES_ONLY:
        path_text = f"Saves the folder {name}{os.sep} with frames{os.sep} ({count} PNG files) and manifest.json"
    errors = [text for kind, text in notes if kind == ERROR]
    blocked = errors[0] if errors else ("Select a folder for the movie." if not settings.folder else "")
    lines = (planes_line(settings, timeline), timing_line(settings, timeline),
             picture_line(view, settings, geometry, picture_problem), file_line(settings, name, movie_available))
    return MovieAssessment(timeline, geometry, lines, tuple(notes), blocked, size_text, estimate, name, path_text)


def readout(view: FrozenView, settings: MovieSettings, timeline: MovieTimeline, index: int) -> str:
    """"Frame 201 of 276 · XY at Z 181 · hold · 20.0 s"; an oblique view names its offset."""
    frame = timeline.frames[index]
    where = (f"offset {frame.plane + 1}" if view.oblique and settings.view == ACTIVE
             else f"{NORMALS[settings.sweep]} {frame.plane + 1}")
    return " · ".join(part for part in (f"Frame {index + 1} of {timeline.count}", f"{settings.sweep} at {where}",
                                        "hold" if frame.hold else "", duration_text(timeline.time(index))) if part)


def step_hint(timeline: MovieTimeline | None, step: int) -> str:
    """Beside Step when it skips planes: at every plane, the section's line says all there is."""
    if step == 1:
        return ""
    return step_text(step) + (f", {len(timeline.samples)} planes" if timeline is not None else "")


def rate_hint(view: FrozenView, fps: int) -> str:
    """Beside Rate when it differs from the plane band, where it started."""
    return "" if fps == view.band_rate else f"The plane band plays at {view.band_rate}/s."


def preview_request(view: FrozenView, settings: MovieSettings, job: int) -> MovieRequest:
    """The dialog's frame preview and its clip use the export's own request, with a placeholder folder."""
    return build_request(view, replace(settings, folder=settings.folder or str(Path.home())), job, ())
