"""One movie export in a worker: admission, frames, encoding, checks and publication (plan §3.4–4.4).

The export pins its source's resident copy, if it has one, and reads planes
without filling the shared cache. Every failure is classified; nothing is
published until the checks pass, and a cancelled job leaves no files.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
import errno
import os
from pathlib import Path
import shutil
import time
from typing import TypeVar

import numpy as np
from PIL import Image

from tomography_session_browser.domain.tomograms import Volume
from tomography_session_browser.parsers.volume_header import fingerprint
from tomography_session_browser.services.movie.encoder import (EncodeSettings, EncoderCheck, EncoderFailed, encode,
                                                               encode_arguments, h264_level, probe, verify, PRESET,
                                                               PROFILE, PIXEL_FORMAT)
from tomography_session_browser.services.movie.frames import FrameSource
from tomography_session_browser.services.movie.models import (COMPRESSION, MOVIE, MovieFailure, MovieProgress,
                                                              MovieRequest, MovieResult)
from tomography_session_browser.services.movie.output import Staging, compact_metadata, publish, stage, write_manifest
from tomography_session_browser.services.volume.memory import MIB, VOLUME_MEMORY, BudgetExceeded
from tomography_session_browser.services.volume.store import resident_key

FRAMES, CHECKING, SAVING = "frames", "Checking", "Saving"
#: How often a worker reports progress, in seconds.
PROGRESS_INTERVAL = .1
#: A preview clip's length, in seconds.
PREVIEW_SECONDS = 3
T = TypeVar("T")


def source_unchanged(volume: Volume) -> bool:
    """The file still has the size and time that the frozen view recorded."""
    try:
        return fingerprint(volume.path) == volume.fingerprint
    except OSError:
        return False


def encoder_allowance(width: int, height: int) -> int:
    """A conservative allowance for x264's slow preset: lookahead, references and its own buffers."""
    return 64 * MIB + width * height * 120


def encode_settings(request: MovieRequest, frames: int, *, metadata: str = "") -> EncodeSettings:
    width, height = request.geometry.frame
    fps = request.timeline.settings.fps
    level = h264_level(width, height, fps)
    if level is None:
        raise MovieFailure("planes", f"Too large for an H.264 movie: {width} × {height}.",
                           "Select HD, Full HD or 4K, or save PNG frames only.")
    return EncodeSettings(width, height, fps, frames, COMPRESSION[request.output.compression], level, metadata)


class Reporter:
    def __init__(self, request: MovieRequest, progress: Callable[[MovieProgress], None]) -> None:
        self.request, self.progress = request, progress
        self.done, self.last = 0, 0.

    def frames(self, done: int) -> None:
        self.done = done
        now = time.monotonic()
        if now - self.last >= PROGRESS_INTERVAL or done == self.request.timeline.count:
            self.last = now
            self.progress(MovieProgress(self.request.job, FRAMES, done, self.request.timeline.count))

    def phase(self, phase: str) -> None:
        self.progress(MovieProgress(self.request.job, phase, self.request.timeline.count, self.request.timeline.count))


def write_png(path: Path, frame: np.ndarray, previous: tuple[np.ndarray, Path] | None) -> None:
    """A held frame is the same array: copy its PNG bytes instead of compressing it again."""
    if previous is not None and previous[0] is frame:
        shutil.copyfile(previous[1], path)
        return
    with path.open("xb") as handle:
        Image.fromarray(frame).save(handle, format="PNG")


def staged_frames(source: FrameSource, staging: Staging | None, reporter: Reporter) -> Iterator[np.ndarray]:
    """Frames in order; with staging, each is also saved as the PNG that the encoder receives."""
    previous: tuple[np.ndarray, Path] | None = None
    for index, frame in source.frames():
        if staging is not None:
            path = staging.frame(index)
            write_png(path, frame, previous)
            previous = (frame, path)
        reporter.frames(index + 1)
        yield frame


def destination(call: Callable[[], T]) -> T:
    """Boundary refusals name the folder problem, not a plane."""
    try:
        return call()
    except FileExistsError as exc:
        raise MovieFailure("destination", str(exc), "Change the name, then export again.") from exc
    except PermissionError as exc:
        raise MovieFailure("destination", "The folder cannot be written.", "Select a different folder.",
                           detail=str(exc)) from exc
    except (ValueError, FileNotFoundError, NotADirectoryError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else "The folder is missing."
        raise MovieFailure("destination", message, "Select a different folder.", detail=str(exc)) from exc


def classify(exc: BaseException, request: MovieRequest, done: int) -> MovieFailure:
    total = request.timeline.count
    where = f" It stopped at frame {done} of {total}." if 0 < done < total else ""
    if isinstance(exc, MovieFailure):
        return exc
    if isinstance(exc, InterruptedError):
        return MovieFailure("cancelled", "The export was cancelled.")
    if isinstance(exc, BudgetExceeded):
        return MovieFailure("memory", "There is not enough memory for this export." + where,
                            "Select a smaller size, close other tomograms, or raise the RAM share in Image settings.",
                            frame=done or None, detail=str(exc))
    if isinstance(exc, EncoderFailed):
        if "No space left" in exc.diagnostics or "not enough space" in exc.diagnostics.lower():
            return MovieFailure("storage", "The disk is full." + where, "Make space on the disk, or select a different folder.",
                                frame=done or None, detail=exc.diagnostics)
        return MovieFailure("encoder", "The encoder could not make this movie." + where,
                            "Try again. If it fails again, save PNG frames only.", frame=done or None,
                            detail=f"{exc}\n{exc.diagnostics}".strip())
    if isinstance(exc, OSError) and exc.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", -1)):
        return MovieFailure("storage", "The disk is full." + where, "Make space on the disk, or select a different folder.",
                            frame=done or None, detail=str(exc))
    if isinstance(exc, FileExistsError):
        return MovieFailure("destination", str(exc), "Change the name, then export again.", detail=str(exc))
    if isinstance(exc, PermissionError):
        return MovieFailure("destination", "The folder cannot be written." + where, "Select a different folder.",
                            frame=done or None, detail=str(exc))
    message = str(exc) or type(exc).__name__
    if "changed" in message or "unavailable" in message or "cloud" in message or "Reload" in message:
        return MovieFailure("source", message, "Reload the processing folder, then export again.", detail=message)
    if isinstance(exc, ValueError):
        frame = request.timeline.frames[min(done, total - 1)]
        return MovieFailure("planes", f"Plane {frame.plane + 1} cannot be used: {message}",
                            "Change From, To or Step, then export again.", frame=done or None, detail=message)
    return MovieFailure("encoder" if isinstance(exc, OSError) else "source", message, "Try again.", detail=message)


def run_export(request: MovieRequest, encoder: EncoderCheck, *, cancelled: Callable[[], bool],
               progress: Callable[[MovieProgress], None], live_roots: Callable[[], tuple[Path, ...]]) -> MovieResult:
    """Raise MovieFailure with a classified reason; return only after the checks and publication."""
    reporter = Reporter(request, progress)
    try:
        if request.encodes and not encoder.available:
            raise MovieFailure("encoder", "MP4 export is not available. " + encoder.reason,
                               "Install FFmpeg, then start the app again. Help, Files, has the steps.")
        settings = encode_settings(request, request.timeline.count, metadata=compact_metadata(request)) if request.encodes else None
        with VOLUME_MEMORY.borrow(resident_key(request.volume)):
            source = FrameSource(request, cancelled)
            width, height = request.geometry.frame
            allowance = source.working_bytes() + (encoder_allowance(width, height) if request.encodes else 0)
            with VOLUME_MEMORY.reserve(allowance, "movie-export"):
                staging = destination(lambda: stage(request))
                try:
                    return produce(request, encoder, settings, source, staging, reporter, cancelled, live_roots)
                except BaseException:
                    staging.discard()
                    raise
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise classify(exc, request, reporter.done) from exc


def produce(request: MovieRequest, encoder: EncoderCheck, settings: EncodeSettings | None, source: FrameSource,
            staging: Staging, reporter: Reporter, cancelled: Callable[[], bool],
            live_roots: Callable[[], tuple[Path, ...]]) -> MovieResult:
    frames = staged_frames(source, staging if request.writes_frames else None, reporter)
    checks: tuple[str, ...] = ()
    encoded = None
    if settings is not None:
        assert encoder.ffmpeg is not None and encoder.ffprobe is not None
        encode(encode_arguments(encoder.ffmpeg, settings, staging.movie), frames, cancelled=cancelled)
        if reporter.done != request.timeline.count:
            raise EncoderFailed(f"The encoder stopped after {reporter.done} frames.")
        reporter.phase(CHECKING)
        checks = verify(probe(encoder.ffprobe, staging.movie), settings)
        encoded = {"codec": "H.264 (libx264)", "preset": PRESET, "crf": settings.crf, "profile": PROFILE,
                   "level": settings.level, "pixel_format": PIXEL_FORMAT, "colour": "limited-range BT.709",
                   "frame_rate": settings.fps, "time_base": f"1/{settings.timescale}", "checks": list(checks)}
    else:
        for _ in frames:
            pass
    if cancelled():
        raise InterruptedError("Movie export cancelled")
    reporter.phase(SAVING)
    if request.output.save != MOVIE:
        write_manifest(staging, request, source.results, encoded)
    movie_bytes = staging.movie.stat().st_size if settings is not None else 0
    png_bytes = sum(path.stat().st_size for path in staging.frames.iterdir()) if request.writes_frames else 0
    if not source_unchanged(request.volume):
        raise MovieFailure("source", "The source changed during the export. No movie was saved.",
                           "Reload the processing folder, then export again.")
    target = destination(lambda: publish(staging, request, live_roots()))
    movie = None
    if settings is not None:
        movie = target if request.output.save == MOVIE else target / staging.movie.name
    timeline = request.timeline
    return MovieResult(request.job, target, movie, request.geometry.frame, timeline.count, timeline.duration,
                       movie_bytes or png_bytes, timeline.count if request.writes_frames else 0, checks)


@dataclass(frozen=True, slots=True)
class PreviewClip:
    job: int
    path: Path


def run_preview(request: MovieRequest, encoder: EncoderCheck, start: int, path: Path, *,
                cancelled: Callable[[], bool]) -> PreviewClip:
    """Three seconds from the timeline position, with the selected encoding settings, in the app's temporary folder."""
    if not encoder.available:
        raise MovieFailure("encoder", "Preview clip is not available. " + encoder.reason)
    assert encoder.ffmpeg is not None
    fps = request.timeline.settings.fps
    stop = min(request.timeline.count, start + PREVIEW_SECONDS * fps)
    settings = encode_settings(request, stop - start)
    path.parent.mkdir(parents=True, exist_ok=True)
    source = FrameSource(request, cancelled)
    try:
        with VOLUME_MEMORY.borrow(resident_key(request.volume)):
            encode(encode_arguments(encoder.ffmpeg, settings, path), (frame for _, frame in source.frames(start, stop)),
                   cancelled=cancelled)
    except BaseException as exc:
        path.unlink(missing_ok=True)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise classify(exc, request, 0) from exc
    return PreviewClip(request.job, path)


def preview_folder(base: Path) -> Path:
    return base / "TomographySessionBrowser" / "movie-previews"


def process_alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        try:
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259  # STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def clear_previews(folder: Path, *, everything_of_this_process: bool = False) -> None:
    """Delete clips of ended app runs (or this run's, at exit). A clip a player holds open stays until next time."""
    if not folder.is_dir():
        return
    for clip in folder.glob("preview-*-*.mp4"):
        try:
            pid = int(clip.name.split("-")[1])
        except (IndexError, ValueError):
            continue
        if (pid == os.getpid() and everything_of_this_process) or (pid != os.getpid() and not process_alive(pid)):
            try:
                clip.unlink()
            except OSError:
                pass
