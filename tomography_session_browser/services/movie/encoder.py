"""FFmpeg (libx264) and ffprobe as subprocesses: the session check, streamed encoding and the media checks.

The app never downloads an encoder. The conda-forge GPL FFmpeg build includes
x264; the LGPL build does not (plan §3.2). On Linux it can go in the app's
environment. On Windows it belongs in its own environment on PATH: in the app's
environment, conda-forge's icuuc.dll in Library/bin replaces the Windows ICU that
Qt6Core imports, and QtCore no longer loads.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from fractions import Fraction
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading

import numpy as np

#: H.264 levels from 3.0: (level, largest frame in macroblocks, macroblocks per second). CRF bitrates
#: are not known before encoding, so smaller frames still signal 3.0, which every player decodes.
LEVELS = (("3.0", 1620, 40500), ("3.1", 3600, 108000), ("3.2", 5120, 216000), ("4.0", 8192, 245760),
          ("4.1", 8192, 245760), ("4.2", 8704, 522240), ("5.0", 22080, 589824), ("5.1", 36864, 983040),
          ("5.2", 36864, 2073600), ("6.0", 139264, 4177920), ("6.1", 139264, 8355840), ("6.2", 139264, 16711680))
#: Many decoders, including PowerPoint's on some computers, stop at level 5.1.
WIDELY_PLAYED = "5.1"
PRESET, PROFILE, PIXEL_FORMAT = "slow", "high", "yuv420p"
#: How long a stopped encoder may take to exit before it is terminated.
GRACE_SECONDS = 2.
#: Bytes of encoder diagnostics kept from stderr.
DIAGNOSTICS = 16 * 1024
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def h264_level(width: int, height: int, fps: int) -> str | None:
    """The lowest level that the frame size and rate need, or None above level 6.2."""
    columns, rows = -(-width // 16), -(-height // 16)
    macroblocks = columns * rows
    for level, frame_size, rate in LEVELS:
        side = (8 * frame_size) ** .5
        if macroblocks <= frame_size and columns <= side and rows <= side and macroblocks * fps <= rate:
            return level
    return None


def encoder_threads() -> int:
    return max(1, min(4, (os.cpu_count() or 2) // 2))


@dataclass(frozen=True, slots=True)
class EncoderCheck:
    ffmpeg: Path | None
    ffprobe: Path | None
    reason: str = ""

    @property
    def available(self) -> bool:
        return self.ffmpeg is not None and self.ffprobe is not None and not self.reason


def find_tool(name: str) -> Path | None:
    """The active Python environment first, then PATH."""
    executable = name + (".exe" if os.name == "nt" else "")
    prefix = Path(sys.prefix)
    for folder in (prefix / "Library" / "bin", prefix / "bin", prefix / "Scripts", prefix):
        candidate = folder / executable
        if candidate.is_file():
            return candidate
    found = shutil.which(name)
    return Path(found) if found else None


def run_tool(arguments: list[str], *, data: bytes | None = None, timeout: float = 20.) -> subprocess.CompletedProcess:
    return subprocess.run(arguments, input=data, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)


def check_encoder(ffmpeg: Path | None = None, ffprobe: Path | None = None) -> EncoderCheck:
    """Both executables, libx264, yuv420p and a two-frame test encode. Run it off the UI thread."""
    ffmpeg = ffmpeg or find_tool("ffmpeg")
    ffprobe = ffprobe or find_tool("ffprobe")
    missing = "No FFmpeg with the H.264 encoder (libx264) was found in this environment or on PATH."
    if ffmpeg is None or ffprobe is None:
        return EncoderCheck(ffmpeg, ffprobe, missing)
    try:
        if run_tool([str(ffprobe), "-hide_banner", "-version"]).returncode:
            return EncoderCheck(ffmpeg, ffprobe, "ffprobe does not start. " + missing)
        details = run_tool([str(ffmpeg), "-hide_banner", "-h", "encoder=libx264"])
        text = details.stdout.decode("utf-8", "replace")
        if details.returncode or "libx264" not in text:
            return EncoderCheck(ffmpeg, ffprobe, missing)
        if PIXEL_FORMAT not in text:
            return EncoderCheck(ffmpeg, ffprobe, "This FFmpeg's libx264 does not support yuv420p.")
        frames = bytes(16 * 16 * 3 * 2)
        test = run_tool([str(ffmpeg), "-hide_banner", "-nostdin", "-loglevel", "error", "-f", "rawvideo",
                         "-pix_fmt", "rgb24", "-video_size", "16x16", "-framerate", "2", "-i", "pipe:0",
                         "-c:v", "libx264", "-pix_fmt", PIXEL_FORMAT, "-f", "null", "-"], data=frames)
        if test.returncode:
            return EncoderCheck(ffmpeg, ffprobe, "FFmpeg could not encode a test frame with libx264.")
    except (OSError, subprocess.SubprocessError) as exc:
        return EncoderCheck(ffmpeg, ffprobe, f"FFmpeg does not start: {exc}")
    return EncoderCheck(ffmpeg, ffprobe)


@dataclass(frozen=True, slots=True)
class EncodeSettings:
    width: int
    height: int
    fps: int
    frames: int
    crf: int
    level: str
    metadata: str = ""

    @property
    def timescale(self) -> int:
        return self.fps * 1000


def encode_arguments(ffmpeg: Path, settings: EncodeSettings, output: Path, *, source: str = "pipe") -> list[str]:
    """Limited-range BT.709 with explicit colour metadata, High profile, CRF, constant rate, fast start."""
    if source == "pipe":
        inputs = ["-f", "rawvideo", "-pix_fmt", "rgb24", "-video_size", f"{settings.width}x{settings.height}",
                  "-framerate", str(settings.fps), "-i", "pipe:0"]
    else:
        inputs = ["-framerate", str(settings.fps), "-start_number", "1", "-i", source]
    # FFmpeg 7.1 and later copy the colour tags from the filtered frames, not from the -color_* options, so
    # setparams stamps them on the frames; the options stay for builds that still read them.
    return [str(ffmpeg), "-hide_banner", "-nostdin", "-loglevel", "error", "-n", *inputs, "-an",
            "-vf", "scale=out_color_matrix=bt709:out_range=tv:flags=bicubic+accurate_rnd+full_chroma_int,format=yuv420p,"
                   "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
            "-c:v", "libx264", "-preset", PRESET, "-crf", str(settings.crf), "-profile:v", PROFILE,
            "-level:v", settings.level, "-pix_fmt", PIXEL_FORMAT, "-threads", str(encoder_threads()),
            "-x264-params", "nr=0", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
            "-color_range", "tv", "-frames:v", str(settings.frames), "-r", str(settings.fps),
            "-video_track_timescale", str(settings.timescale), "-movflags", "+faststart",
            *(("-metadata", f"comment={settings.metadata}") if settings.metadata else ()), "-f", "mp4", str(output)]


class EncoderFailed(RuntimeError):
    def __init__(self, message: str, diagnostics: str = "") -> None:
        super().__init__(message)
        self.diagnostics = diagnostics


def stop(process: subprocess.Popen) -> None:
    """Close the encoder; terminate it, then kill it, if it does not exit in time."""
    if process.stdin is not None and not process.stdin.closed:
        try:
            process.stdin.close()
        except OSError:
            pass
    try:
        process.wait(GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def encode(arguments: list[str], frames: Iterable[np.ndarray] | None, *, cancelled: Callable[[], bool],
           sent: Callable[[int], None] = lambda _count: None) -> str:
    """Run one encode. Raw frames go to stdin; stderr is read continuously and bounded.

    Returns the kept diagnostics. Raises InterruptedError on cancellation and
    EncoderFailed when the encoder exits with an error.
    """
    process = subprocess.Popen(arguments, stdin=subprocess.PIPE if frames is not None else subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
    kept: deque[bytes] = deque()
    size = [0]

    def drain() -> None:
        assert process.stderr is not None
        for line in iter(process.stderr.readline, b""):
            kept.append(line)
            size[0] += len(line)
            while size[0] > DIAGNOSTICS and len(kept) > 1:
                size[0] -= len(kept.popleft())

    reader = threading.Thread(target=drain, name="movie-encoder-stderr", daemon=True)
    reader.start()
    diagnostics = lambda: b"".join(kept).decode("utf-8", "replace").strip()  # noqa: E731
    try:
        if frames is not None:
            assert process.stdin is not None
            count = 0
            for frame in frames:
                if cancelled():
                    raise InterruptedError("Movie export cancelled")
                try:
                    process.stdin.write(np.ascontiguousarray(frame).tobytes())
                except (BrokenPipeError, OSError):
                    break  # The encoder stopped; its exit status and stderr say why.
                count += 1
                sent(count)
            try:
                process.stdin.close()
            except OSError:
                pass
        while True:
            try:
                status = process.wait(.2)
                break
            except subprocess.TimeoutExpired:
                if cancelled():
                    raise InterruptedError("Movie export cancelled")
    except BaseException:
        stop(process)
        reader.join(1.)
        raise
    reader.join(1.)
    if status:
        raise EncoderFailed(f"The encoder stopped with status {status}.", diagnostics())
    return diagnostics()


def probe(ffprobe: Path, path: Path) -> dict:
    result = run_tool([str(ffprobe), "-v", "error", "-count_frames", "-show_entries",
                       "stream=index,codec_type,codec_name,profile,level,pix_fmt,width,height,color_range,"
                       "color_space,color_transfer,color_primaries,r_frame_rate,avg_frame_rate,time_base,"
                       "nb_read_frames,sample_aspect_ratio:format=duration", "-of", "json", str(path)], timeout=600.)
    if result.returncode:
        raise EncoderFailed("The saved movie could not be decoded.", result.stderr.decode("utf-8", "replace"))
    return json.loads(result.stdout.decode("utf-8", "replace"))


def verify(report: dict, settings: EncodeSettings) -> tuple[str, ...]:
    """Every §3.3 check; raise with the first that fails, else return what passed."""
    streams = report.get("streams", [])
    if len(streams) != 1 or streams[0].get("codec_type") != "video":
        raise EncoderFailed("The movie must have exactly one video stream and no audio.")
    stream = streams[0]
    fps = f"{settings.fps}/1"
    expected = {
        "codec_name": "h264", "profile": "High", "pix_fmt": PIXEL_FORMAT, "color_range": "tv",
        "color_space": "bt709", "color_transfer": "bt709", "color_primaries": "bt709",
        "width": settings.width, "height": settings.height, "r_frame_rate": fps, "avg_frame_rate": fps,
        "time_base": f"1/{settings.timescale}", "level": round(float(settings.level) * 10),
    }
    for key, value in expected.items():
        if stream.get(key) != value:
            raise EncoderFailed(f"The movie check failed: {key} is {stream.get(key)!r}, not {value!r}.")
    if stream.get("sample_aspect_ratio") not in (None, "1:1", "0:1", "N/A"):
        raise EncoderFailed("The movie check failed: pixels are not square.")
    frames = int(stream.get("nb_read_frames", -1))
    if frames != settings.frames:
        raise EncoderFailed(f"The movie check failed: it has {frames} frames, not {settings.frames}.")
    duration = Fraction(str(report.get("format", {}).get("duration", "0")))
    if abs(duration - Fraction(settings.frames, settings.fps)) > Fraction(1, settings.fps):
        raise EncoderFailed(f"The movie check failed: its duration is {float(duration):.3f} s.")
    return ("exit status", "decodable", "H.264 High", PIXEL_FORMAT, "limited range BT.709", "dimensions",
            "frame count", "time base and rate", "duration")
