"""Header-derived physical-Z slabs and version-explicit volume inventory."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import math

from tomography_session_browser.domain.tomograms import Tomogram, Volume, VolumeGrid
from tomography_session_browser.presentation.dashboard.index_chart import SlabReference
from tomography_session_browser.presentation.tomogram_versions import review_versions
from tomography_session_browser.presentation.volume_formatting import mode_text, file_size


@dataclass(frozen=True, slots=True)
class VolumeRow:
    label: str
    row_keys: frozenset[str]
    columns: tuple[str, ...]
    detail: str
    sort_values: tuple[tuple[str, float] | str | None, ...] = ()


def slab_nm(volume: Volume) -> float | None:
    grid = volume.grid
    z = grid.spacing_xyz[2] if grid else None
    if grid is None or z is None or not math.isfinite(z) or z <= 0 or abs(z - 1.) < 1e-6:
        return None
    return grid.shape_xyz[2] * z / 10


def slab_reference(item: Tomogram) -> SlabReference:
    versions = review_versions(item)
    sources = tuple(f"{volume.version}: {volume.path.name}" for volume in versions)
    if not versions or any(volume.grid is None or slab_nm(volume) is None for volume in versions):
        return SlabReference(sources=sources, note="Slab unavailable: a full version has no resolved Z spacing/grid.")
    if any(volume.grid != versions[0].grid for volume in versions[1:]):
        return SlabReference(sources=sources, note="Slab unavailable: full reconstruction versions have different geometry.")
    return SlabReference(slab_nm(versions[0]), sources, "Physical Z extent from the reconstruction header; not a specimen-thickness estimate.")


def volume_columns(volume: Volume) -> tuple[str, ...]:
    grid = volume.grid
    shape = " × ".join(map(str, grid.shape_xyz)) if grid else "Unavailable"
    spacing = " × ".join(f"{v:g}" if v is not None else "?" for v in grid.spacing_xyz) + " Å" if grid else "Unresolved"
    slab = slab_nm(volume)
    return shape, spacing, f"{slab:g} nm" if slab is not None else "Unresolved", mode_text(volume.dtype), file_size(volume.fingerprint.size)


def volume_sort_values(volume: Volume, size: int) -> tuple[tuple[str, float] | str | None, ...]:
    grid, slab = volume.grid, slab_nm(volume)
    voxels = math.prod(grid.shape_xyz) if grid else None
    z = grid.spacing_xyz[2] if grid else None
    return (("voxels", voxels) if voxels is not None else None, ("Å", z) if z is not None else None,
            ("nm", slab) if slab is not None else None, mode_text(volume.dtype), ("bytes", size))


def volume_inventory(items: tuple[Tomogram, ...]) -> tuple[VolumeRow, ...]:
    if len(items) <= 24:
        return tuple(VolumeRow(f"{item.name} · {volume.version}", frozenset((item.id,)), volume_columns(volume),
            f"{volume.path}\nGrid: {volume.grid}\n{slab_reference(item).note}\nSize sorts by voxel count; voxel spacing sorts by physical Z.",
            volume_sort_values(volume, volume.fingerprint.size)) for item in items for volume in item.volumes)
    groups: dict[tuple[VolumeGrid | None, str], list[tuple[Tomogram, Volume]]] = defaultdict(list)
    for item in items:
        for volume in item.volumes:
            groups[volume.grid, volume.dtype].append((item, volume))
    rows = []
    for members in groups.values():
        keys = frozenset(item.id for item, _ in members)
        volumes = tuple(volume for _, volume in members)
        total_size = sum(v.fingerprint.size for v in volumes)
        columns = (*volume_columns(volumes[0])[:-1], file_size(total_size))
        rows.append(VolumeRow(f"{len(keys)} tomograms · {len(volumes)} versions", keys, columns,
            f"Declared geometry: {volumes[0].grid}\nVersions: {', '.join(sorted({v.version for v in volumes}))}",
            volume_sort_values(volumes[0], total_size)))
    return tuple(rows)
