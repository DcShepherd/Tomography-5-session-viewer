"""Movie names, staging on the destination filesystem, safe publication and the movie/1 manifest.

Names use the snapshot sanitiser and ``.reserve`` reservations. A job stages
everything in one hidden ``.movie-incomplete`` folder that it owns, publishes
it only after the checks, and never replaces an existing file (plan §4.3–4.4).
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import shutil
import uuid

from tomography_session_browser.domain.tomograms import Volume
from tomography_session_browser.parsers.volume_header import fingerprint
from tomography_session_browser.services.movie.models import FRAMES_ONLY, MOVIE, MOVIE_AND_FRAMES, MovieOutput, MovieRequest
from tomography_session_browser.services.output_boundary import check_output_path, require_output_directory
from tomography_session_browser.services.volume.models import RenderResult
from tomography_session_browser.services.volume.snapshot import snapshot_provenance
from tomography_session_browser.services.volume.snapshot_names import output_stem, reservation_path, sanitise_name

MANIFEST_SCHEMA = "tomography-session-browser.movie/1"
INCOMPLETE = ".movie-incomplete"
FRAME_NAME = "frame_{:08d}.png"


def automatic_name(tomogram: str, version: str, plane: str, first: int, last: int) -> str:
    """``<tomogram>_<version>_<plane>_<first>-<last>``, with one-based planes."""
    return f"{output_stem(tomogram, version)}_{plane}_{first + 1}-{last + 1}"


def candidates(folder: Path, name: str, save: str) -> Iterator[Path]:
    """The chosen name, then the snapshot sequence's numbers from 02."""
    if save not in (MOVIE, MOVIE_AND_FRAMES, FRAMES_ONLY):
        raise ValueError("Unknown movie output.")
    stem, suffix = sanitise_name(name), ".mp4" if save == MOVIE else ""
    yield folder / f"{stem}{suffix}"
    for number in range(2, 1000000):
        yield folder / f"{stem}_{number:02d}{suffix}"


def occupied(candidate: Path) -> bool:
    return candidate.exists() or reservation_path(candidate).exists()


def suggest(folder: Path, name: str, save: str, *, max_checks: int = 4096) -> Path:
    """Advisory only, for the dialog: the first free candidate. The writer reserves its own."""
    for index, candidate in enumerate(candidates(folder, name, save)):
        if index >= max_checks:
            break
        if not occupied(candidate):
            return candidate
    raise ValueError("Could not find a free movie name. Change the name.")


@dataclass(frozen=True, slots=True)
class Staging:
    folder: Path
    target: Path
    reservation: Path
    root: Path

    @property
    def movie(self) -> Path:
        return self.root / (self.target.name if self.target.suffix == ".mp4" else self.target.name + ".mp4")

    @property
    def frames(self) -> Path:
        return self.root / "frames"

    @property
    def manifest(self) -> Path:
        return self.root / "manifest.json"

    def frame(self, index: int) -> Path:
        return self.frames / FRAME_NAME.format(index + 1)

    def discard(self) -> None:
        """Delete only what this job owns: its staging folder and its reservation."""
        shutil.rmtree(self.root, ignore_errors=True)
        self.reservation.unlink(missing_ok=True)


def roots_for(output: MovieOutput, volume: Volume, live: tuple[Path, ...] = ()) -> tuple[Path, ...]:
    """The frozen roots, the roots loaded since, and the source volume's own folder."""
    return tuple(dict.fromkeys((*output.protected_roots, *live, volume.path.parent)))


def stage(request: MovieRequest) -> Staging:
    output = request.output
    roots = roots_for(output, request.volume)
    folder = require_output_directory(output.directory, roots)
    for candidate in candidates(folder, output.name, output.save):
        if occupied(candidate):
            continue
        lock = reservation_path(candidate)
        check_output_path(candidate, roots)
        check_output_path(lock, roots)
        try:
            handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        os.close(handle)
        root = folder / f".{uuid.uuid4().hex}{INCOMPLETE}"
        try:
            check_output_path(root, roots)
            root.mkdir()
            if request.writes_frames:
                (root / "frames").mkdir()
        except BaseException:
            shutil.rmtree(root, ignore_errors=True)
            lock.unlink(missing_ok=True)
            raise
        return Staging(folder, candidate, lock, root)
    raise ValueError("Could not reserve a unique movie name.")


def publish(staging: Staging, request: MovieRequest, live: tuple[Path, ...]) -> Path:
    """Check the source and the destination again, then publish in one rename that never replaces."""
    roots = roots_for(request.output, request.volume, live)
    folder = require_output_directory(staging.folder, roots)
    if fingerprint(request.volume.path) != request.volume.fingerprint:
        raise ValueError("The source changed during the export. No movie was saved.")
    target = check_output_path(staging.target, roots)
    check_output_path(staging.root, roots)
    if target.parent != folder or target.exists():
        raise FileExistsError(f"{target.name} exists. No file was replaced.")
    source = staging.movie if request.output.save == MOVIE else staging.root
    # Windows rename is atomic and refuses an existing destination. POSIX
    # rename can replace, so a file is linked exclusively there instead.
    if os.name == "nt" or source.is_dir():
        os.rename(source, target)
    else:
        os.link(source, target)
        source.unlink()
    staging.discard()
    return target


def write_manifest(staging: Staging, request: MovieRequest, results: dict[str, RenderResult],
                   encoder: dict | None) -> None:
    sweep = results[request.sweep]
    manifest = snapshot_provenance(request.tomogram, request.volume, sweep, schema=MANIFEST_SCHEMA)
    timeline = request.timeline
    settings = timeline.settings
    manifest.update({
        "layout": request.layout, "sweep": request.sweep,
        "panes": [{"axis": pane.recipe.axis, "recipe": asdict(results[pane.recipe.axis].recipe),
                   "pane": pane.raster.pane, "image": pane.raster.image, "scale_bar": pane.scale_bar,
                   "bar_label": results[pane.recipe.axis].bar_label} for pane in request.panes],
        "intersection_xyz": request.cross_xyz,
        "timeline": {"first": settings.first, "last": settings.last, "step": settings.step, "fps": settings.fps,
                     "end": settings.end, "loop_ready": settings.loop_ready if settings.bounce else None,
                     "holds": {"start": settings.start_hold, "turn": settings.turn_hold if settings.bounce else 0.,
                               "end": settings.end_hold if settings.end_available else 0.},
                     "chosen_holds": [asdict(hold) for hold in settings.chosen],
                     "unused_holds": [asdict(hold) for hold in timeline.unused],
                     "frames": timeline.count, "duration_s": timeline.duration,
                     "frame_planes": [frame.plane for frame in timeline.frames],
                     "frame_holds": [frame.hold for frame in timeline.frames],
                     "plane_index": "zero-based"},
        "output": {"save": request.output.save, "frame_size": request.geometry.frame, "ratio": request.geometry.ratio,
                   "scale_factor": request.geometry.factor, "title": request.labels.title,
                   "labels": asdict(request.labels), "png_frames": timeline.count if request.writes_frames else 0},
        "encoder": encoder,
        "provenance_policy": "Filenames, rendering recipes and the timeline embedded; no full paths.",
    })
    with staging.manifest.open("x", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=1, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())


def compact_metadata(request: MovieRequest) -> str:
    """The MP4 comment of a movie saved alone: what the manifest would say, briefly."""
    settings = request.timeline.settings
    return json.dumps({
        "schema": MANIFEST_SCHEMA, "tomogram": request.tomogram, "source_filename": request.volume.path.name,
        "version": request.volume.version, "source_size": request.volume.fingerprint.size,
        "source_mtime_ns": request.volume.fingerprint.mtime_ns, "layout": request.layout, "sweep": request.sweep,
        "first": settings.first, "last": settings.last, "step": settings.step, "fps": settings.fps, "end": settings.end,
        "loop_ready": settings.loop_ready if settings.bounce else None, "frames": request.timeline.count,
        "holds": [settings.start_hold, settings.turn_hold, settings.end_hold],
        "chosen_holds": [[hold.plane, hold.direction, hold.seconds] for hold in settings.chosen],
        "plane_index": "zero-based",
    }, separators=(",", ":"), allow_nan=False)
