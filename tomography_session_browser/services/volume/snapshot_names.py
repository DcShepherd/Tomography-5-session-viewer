"""Read-only name prediction and the writer's shared collision candidates."""
from collections.abc import Callable, Iterator
from pathlib import Path
import re
import time

from tomography_session_browser.services.volume.review_repository import ReviewRepository


def sanitise_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._") or "tomogram"


def output_stem(name: str, version: str) -> str:
    """The tomogram and its version, once: "Archea_project20260727_1_deconvolved"."""
    name = sanitise_name(name)
    version = re.sub(r"[^A-Za-z0-9]+", "_", version).strip("_").lower()
    return name if name.casefold().endswith("_" + version.casefold()) else f"{name}_{version}"


def snapshot_candidates(folder: Path, name: str, version: str, format: str) -> Iterator[Path]:
    if format not in ("PNG", "TIFF", "JPEG"):
        raise ValueError("Choose PNG, TIFF or JPEG.")
    extension = {"PNG": ".png", "TIFF": ".tif", "JPEG": ".jpg"}[format]
    stem = output_stem(name, version)
    for number in range(1, 1000000):
        yield folder / f"{stem}_{number:02d}{extension}"


def reservation_path(candidate: Path) -> Path:
    return candidate.with_suffix(candidate.suffix + ".reserve")


def name_occupied(candidate: Path, history: Path | None = None, held: frozenset[Path] = frozenset()) -> bool:
    """Capture history keys records by output path. A name it still holds stays
    taken after its image is deleted, so a new capture never replaces that record.
    """
    if candidate.exists() or reservation_path(candidate).exists() or candidate in held:
        return True
    return history is not None and ReviewRepository(history).has_capture(str(candidate))


def next_snapshot_path(folder: Path, name: str, version: str, format: str, *,
                       history: Path | None = None, held: frozenset[Path] = frozenset(),
                       cancelled: Callable[[], bool] = lambda: False,
                       max_checks: int = 4096, seconds: float = .25) -> Path:
    """Advisory only: bounded probes, no reservation, no directory creation.

    A blocked filesystem call cannot be pre-empted; cancellation/time are checked
    between calls. The durable writer has its own exclusive reservation loop.
    """
    started = time.monotonic()
    for index, candidate in enumerate(snapshot_candidates(folder, name, version, format)):
        if cancelled():
            raise InterruptedError("Filename preview cancelled")
        if index >= max_checks or time.monotonic() - started >= seconds:
            raise ValueError("The advisory filename check reached its limit. The writer chooses the final name when saving.")
        if not name_occupied(candidate, history, held):
            return candidate
    raise ValueError("Could not find an advisory snapshot name.")
