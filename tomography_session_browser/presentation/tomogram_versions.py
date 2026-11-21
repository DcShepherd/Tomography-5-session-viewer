"""Related reconstructions share declared coordinates, never intensity domains."""
from dataclasses import dataclass, replace

from tomography_session_browser.domain.tomograms import Tomogram, Volume
from tomography_session_browser.presentation.tomogram_state import TomogramViewState
from tomography_session_browser.services.volume.render import calibrated_spacing

GEOMETRY_FIELDS = ("plane", "zoom", "centre", "layout", "axis", "cross_xyz", "capture_region", "linked",
                   "centre_xyz", "linked_scale", "pane_views", "dividers", "ortho_lines", "rotation",
                   "pivot_xyz", "pivot_grid_xyz", "pivot_locked", "orientation_visible", "rotation_axis")


@dataclass(frozen=True, slots=True)
class SharedGeometry:
    state: TomogramViewState


@dataclass(frozen=True, slots=True)
class GeometryRefusal:
    reason: str


def review_versions(row: Tomogram) -> tuple[Volume, ...]:
    """Half sets remain source records but are not reconstruction review choices."""
    return tuple(volume for volume in row.volumes if volume.version not in ("Even", "Odd"))


def ambiguous_versions(row: Tomogram) -> frozenset[str]:
    """Version names held by more than one review volume: no rule may pick one of them by name."""
    names = [volume.version for volume in review_versions(row)]
    return frozenset(name for name in names if names.count(name) > 1)


def unique_version(row: Tomogram, name: str | None) -> Volume | None:
    """The one review volume called ``name``; ``None`` when there is none or more than one."""
    matches = [volume for volume in review_versions(row) if volume.version == name]
    return matches[0] if len(matches) == 1 else None


def version_label(row: Tomogram, volume: Volume) -> str:
    """The version name; when another review volume has the same name, also its pixel size or file name.

    The pixel size is used only when it differs between every volume of that
    name, so two labels are never the same.
    """
    if volume.version not in ambiguous_versions(row):
        return volume.version
    spacings = [_spacing_text(other) for other in review_versions(row) if other.version == volume.version]
    if None not in spacings and len(set(spacings)) == len(spacings):
        return f"{volume.version} · {_spacing_text(volume)} Å/px"
    return f"{volume.version} · {volume.path.name}"


def _spacing_text(volume: Volume) -> str | None:
    spacing = volume.grid.spacing_xyz[0] if volume.grid is not None else None
    return f"{spacing:.2f}" if spacing else None


def share_geometry(row: Tomogram, source: Volume | None, target: Volume,
                   current: TomogramViewState, destination: TomogramViewState) -> SharedGeometry | GeometryRefusal:
    if source is None or source not in row.volumes or target not in row.volumes or current.row_key != row.id:
        return GeometryRefusal("The versions do not have a shared reconstruction identity.")
    if source.grid is None or target.grid is None:
        return GeometryRefusal("A usable grid is missing.")
    for volume, state in ((source, current), (target, destination)):
        if state.volume_signature not in (None, (volume.fingerprint.size, volume.fingerprint.mtime_ns)):
            return GeometryRefusal("A source has changed since its view was saved.")
    # Full declared geometry: counts, storage-axis mapping, spacing, origin and
    # crop starts. Do not fit, rescale or estimate a registration from intensities.
    if source.grid != target.grid:
        return GeometryRefusal("Grid dimensions, axes, spacing or crop origin differ.")
    calibration = calibrated_spacing(source, current.spacing_override)
    if calibration is None or calibration != calibrated_spacing(target, destination.spacing_override):
        return GeometryRefusal("Display calibration is unresolved or differs.")
    return SharedGeometry(replace(destination, **{name: getattr(current, name) for name in GEOMETRY_FIELDS}))
