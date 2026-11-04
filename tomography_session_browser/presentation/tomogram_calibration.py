"""When to ask for a pixel size, and what to suggest. Suggestions are never applied silently.

A header without a usable spacing (missing, non-finite or IMOD's 1 Å default)
leaves the scale bar off. The prompt offers values recorded elsewhere for the
same tomogram; the user confirms one as a display-only override.
"""
from __future__ import annotations

import math
import re

from tomography_session_browser.domain.tomograms import Tomogram, Volume
from tomography_session_browser.services.volume.render import calibrated_spacing

_FILE_SPACING = re.compile(r"(\d+(?:\.\d+)?)\s*Apx", re.IGNORECASE)

# The supported adapters expose header geometry and suggestions, not a verified
# per-output binning/resampling chain. A shared array shape cannot replace it.
CALIBRATION_SCOPE_NOTE = ("Calibration applies to this version only. Bulk application is unavailable because loaded metadata "
                          "does not verify equal reconstruction sampling across versions.")


def needs_pixel_size(volume: Volume | None, override: float | None) -> bool:
    return volume is not None and volume.grid is not None and override is None and calibrated_spacing(volume, None) is None


def header_note(volume: Volume) -> str:
    spacing = volume.grid.spacing_xyz if volume.grid is not None else (None, None, None)
    if all(value is not None and math.isfinite(value) and abs(value - 1) < 1e-6 for value in spacing):
        return "Header records 1.0 Å/px · scale bar off"
    return "Header has no usable pixel size · scale bar off"


def pixel_size_suggestions(item: Tomogram, volume: Volume) -> tuple[tuple[str, float], ...]:
    """Isotropic spacings from the tomogram's other versions, then a value in the file name."""
    found: list[tuple[str, float]] = []
    for other in item.volumes:
        spacing = calibrated_spacing(other, None) if other is not volume else None
        if spacing and len(set(spacing)) == 1:
            found.append((f"{other.version} · {spacing[0]:.2f} Å", spacing[0]))
    match = _FILE_SPACING.search(volume.path.name)
    if match and float(match.group(1)) > 0 and abs(float(match.group(1)) - 1) > 1e-6:
        found.append((f"File name · {float(match.group(1)):.2f} Å", float(match.group(1))))
    return tuple(dict.fromkeys(found))
