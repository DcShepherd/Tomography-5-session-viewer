"""Is a saved movie still there? Read-only: one stat of the path the export recorded.

It never opens, writes or moves the movie, its frames or a session folder. The
Movies menu runs it in its own one-thread lane, so a slow network drive does not
hold up history reads.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
import stat
from typing import Literal


@dataclass(frozen=True, slots=True)
class OutputCheck:
    kind: Literal["file", "folder", "missing"]
    size: int | None = None


def check_output(path: Path) -> OutputCheck:
    try:
        status = path.stat()
    except (OSError, ValueError):
        return OutputCheck("missing")
    if stat.S_ISREG(status.st_mode):
        return OutputCheck("file", status.st_size)
    if stat.S_ISDIR(status.st_mode):
        return OutputCheck("folder")
    return OutputCheck("missing")


def check_outputs(paths: Iterable[Path], *, cancelled: Callable[[], bool] = lambda: False) -> tuple[tuple[Path, OutputCheck], ...]:
    found = []
    for path in paths:
        if cancelled():
            raise InterruptedError("Obsolete file check")
        found.append((path, check_output(path)))
    return tuple(found)
