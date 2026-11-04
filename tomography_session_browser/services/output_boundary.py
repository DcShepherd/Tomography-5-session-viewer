"""Side-effect-free containment for app-owned output, including missing parents.

Owners retain their directory, file-type, locking and publication contracts.
"""
from pathlib import Path


def check_output_path(path: Path, protected_roots: tuple[Path, ...]) -> Path:
    """Return the resolved proposal, or refuse an output inside a source.

    Non-strict resolution follows existing parent aliases without creating a
    missing directory. Resolution errors propagate: an unknown boundary is not
    permission to write. Path comparisons use the host's case semantics.
    """
    target = path.resolve()
    if any(target.is_relative_to(root.resolve()) for root in protected_roots):
        raise ValueError("Choose a folder outside the loaded sources.")
    return target


def require_output_directory(directory: Path, protected_roots: tuple[Path, ...]) -> Path:
    """Require an existing directory outside the sources; never create it."""
    check_output_path(directory, protected_roots)
    folder = directory.resolve(strict=True)
    if not folder.is_dir():
        raise ValueError("The output destination is not a folder.")
    return folder
