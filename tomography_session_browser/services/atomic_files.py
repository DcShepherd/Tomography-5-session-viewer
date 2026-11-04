"""Small atomic-file primitives for app-owned metadata, never source data."""
from collections.abc import Iterator
from contextlib import contextmanager
import os
from pathlib import Path
import sys
import uuid

from tomography_session_browser.services.output_boundary import check_output_path


class FileTooLarge(ValueError):
    pass


class FileChanged(ValueError):
    pass


def bounded_read(path: Path, limit: int) -> bytes:
    before = path.stat()
    if before.st_size > limit:
        raise FileTooLarge("File exceeds its size limit.")
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    after = path.stat()
    if len(content) > limit or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise FileChanged("File changed while reading.")
    return content


def atomic_write(path: Path, content: bytes, *, protected_roots: tuple[Path, ...] = ()) -> None:
    """Replace from an exclusive sibling temporary; clean only that temporary."""
    temporary = path.with_name("." + uuid.uuid4().hex + ".tmp")
    check_output_path(path, protected_roots)
    check_output_path(temporary, protected_roots)
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        check_output_path(path, protected_roots)
        check_output_path(temporary, protected_roots)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def move_exclusive(source: Path, destination: Path) -> None:
    """Archive without overwriting an existing file on either supported OS."""
    if os.name == "nt":
        # Windows rename refuses an existing destination. Avoid creating and
        # immediately replacing an archive placeholder (a sharing-race source).
        source.rename(destination)
        return
    with destination.open("xb"):
        pass
    try:
        os.replace(source, destination)
    except OSError:
        if source.exists():
            destination.unlink(missing_ok=True)
        raise


@contextmanager
def exclusive(path: Path, message: str = "This file is being saved by another window.") -> Iterator[None]:
    # Keep the inode: unlinking a lock permits simultaneous locks on different files.
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError(message) from exc
        try:
            yield
        finally:
            stream.seek(0)
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
