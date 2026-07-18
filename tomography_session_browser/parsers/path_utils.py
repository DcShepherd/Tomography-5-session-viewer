from __future__ import annotations

import re
from pathlib import Path


SAMPLE_NAME_RE = re.compile(r"^Sample-?\d+$", re.IGNORECASE)


def sorted_paths(paths: list[Path]) -> list[Path]:
    return sorted(paths, key=lambda item: natural_key(item.name))


def natural_key(value: str) -> list[int | str]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


def is_sample_dir(path: Path) -> bool:
    return path.is_dir() and SAMPLE_NAME_RE.match(path.name) is not None


def safe_id(path: Path) -> str:
    return path.as_posix().replace("/", ":")
