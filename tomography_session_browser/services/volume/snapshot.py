"""Frozen display capture: bounded callers, exclusive names and atomic publication."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import uuid

from PIL import Image, PngImagePlugin

from tomography_session_browser.domain.tomograms import Volume
from tomography_session_browser.parsers.volume_header import fingerprint
from tomography_session_browser.services.volume.models import RenderRecipe, RenderResult
from tomography_session_browser.services.volume.memory import VOLUME_MEMORY, MIB
from tomography_session_browser.services.volume.render import render_xy, native_region
from tomography_session_browser.services.volume.snapshot_names import snapshot_candidates, name_occupied, reservation_path
from tomography_session_browser.services.output_boundary import check_output_path, require_output_directory


@dataclass(frozen=True, slots=True)
class SnapshotRequest:
    row_key: str
    name: str
    volume: Volume
    recipe: RenderRecipe
    directory: Path
    protected_roots: tuple[Path, ...]
    format: str = "PNG"
    # Names kept by capture history (its folder, and this window's unsaved
    # records) are never reused. Like the roots, set at dispatch; not output identity.
    history: Path | None = field(default=None, compare=False)
    held: frozenset[Path] = field(default=frozenset(), compare=False)


@dataclass(frozen=True, slots=True)
class CaptureRecord:
    path: Path
    request: SnapshotRequest


def save_snapshot(request: SnapshotRequest) -> CaptureRecord:
    # Also protect the resolved data directory when a source contains symlinks
    # to volumes outside its selected processing root.
    folder = require_output_directory(request.directory, (*request.protected_roots, request.volume.path.parent))
    if fingerprint(request.volume.path) != request.volume.fingerprint:
        raise ValueError("The captured source is missing or changed. Reload it before taking a new snapshot.")
    if request.recipe.black is None or request.recipe.white is None:
        raise ValueError("Capture requires accepted, fixed intensity levels.")
    if request.recipe.interactive:
        raise ValueError("Wait for the refined plane before taking a snapshot.")
    result = render_xy(request.volume, request.recipe)
    with VOLUME_MEMORY.reserve(result.pixels.nbytes * 8 + 4 * MIB, "encoder"):
        folder = require_output_directory(request.directory, (*request.protected_roots, request.volume.path.parent))
        return publish_snapshot(request, result, folder)


def publish_snapshot(request: SnapshotRequest, result: RenderResult, folder: Path) -> CaptureRecord:
    roots = (*request.protected_roots, request.volume.path.parent)
    folder = require_output_directory(folder, roots)
    reservation: Path | None = None
    output: Path | None = None
    for candidate in snapshot_candidates(folder, request.name, request.volume.version, request.format):
        if name_occupied(candidate, request.history, request.held):
            continue
        lock = reservation_path(candidate)
        check_output_path(candidate, roots)
        check_output_path(lock, roots)
        try:
            handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        os.close(handle)
        reservation, output = lock, candidate
        break
    if output is None or reservation is None:
        raise ValueError("Could not reserve a unique snapshot name.")
    temporary = folder / f".{uuid.uuid4().hex}{output.suffix}.tmp"
    try:
        check_output_path(temporary, roots)
        metadata = PngImagePlugin.PngInfo()
        provenance = snapshot_provenance(request.name, request.volume, result)
        description = json.dumps(provenance, allow_nan=False)
        metadata.add_text("TomographySnapshot", description)
        with temporary.open("xb") as handle:
            image = Image.fromarray(result.pixels)
            if request.format == "PNG":
                image.save(handle, format="PNG", pnginfo=metadata)
            elif request.format == "TIFF":
                image.save(handle, format="TIFF", compression="tiff_lzw", tiffinfo={270: description})
            else:
                image.save(handle, format="JPEG", quality=95, comment=description.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        if fingerprint(request.volume.path) != request.volume.fingerprint:
            raise ValueError("The source changed during capture. No snapshot was published.")
        # Windows rename is atomic and refuses an existing destination. POSIX
        # rename overwrites, so use exclusive link creation there instead.
        check_output_path(output, roots)
        check_output_path(temporary, roots)
        if os.name == "nt":
            os.rename(temporary, output)
        else:
            os.link(temporary, output)
        return CaptureRecord(output, request)
    finally:
        temporary.unlink(missing_ok=True)
        reservation.unlink(missing_ok=True)


def snapshot_provenance(name: str, volume: Volume, result: RenderResult, *,
                        schema: str = "tomography-session-browser.snapshot/1") -> dict:
    """Embedded provenance, shared with the movie manifest: filenames and recipes, never full paths."""
    grid = volume.grid
    assert grid is not None
    # Embedded provenance deliberately omits full paths and path-derived IDs.
    provenance = {
        "schema": schema, "tomogram": name,
        "source_filename": volume.path.name, "version": volume.version,
        "source_size": volume.fingerprint.size,
        "source_mtime_ns": volume.fingerprint.mtime_ns,
        "axes": f"{result.recipe.axis}; horizontal X for XY/XZ, Z for YZ; vertical {'decreasing' if result.recipe.flip_vertical else 'increasing'} Y for XY/YZ, Z for XZ; plane index is zero-based",
        "grid": asdict(grid), "recipe": asdict(result.recipe),
        "raster_size": result.recipe.raster_size,
        "sampled_region": native_region(result.recipe.region) if result.recipe.native_sampling else result.recipe.region,
        "calibration": result.calibration, "bar_label": result.bar_label, "bar_notice": result.bar_notice,
        "processing": "native axis-aligned voxel centres; slice mean; 2D Gaussian Fourier filters; fixed levels; area shrink",
        "provenance_policy": "Filenames and rendering recipe embedded; full paths kept only in app capture history.",
    }
    if any(result.recipe.rotation) or result.recipe.pivot_xyz != result.recipe.pivot_grid_xyz:
        from tomography_session_browser.services.volume.oblique import mapping
        from tomography_session_browser.services.volume.render import calibrated_spacing
        matrix, offset = mapping(volume, result.recipe.rotation, result.recipe.pivot_xyz,
                                 result.recipe.pivot_grid_xyz, calibrated_spacing(volume, result.recipe.spacing_override))
        provenance["axes"] = f"Reference-grid {result.recipe.axis}; zero-based voxel centres; source_xyz = matrix @ reference_xyz + offset"
        provenance["source_mapping"] = {"matrix": matrix.tolist(), "offset": offset.tolist(),
            "rotation_convention": "physical XYZ degrees; Rz @ Ry @ Rx", "extent": "fixed original reference grid",
            "outside_source": "missing; excluded from mean and histogram", "interpolation": "trilinear"}
        provenance["processing"] = "trilinear oblique samples; full requested slice mean; 2D Gaussian Fourier filters; fixed levels; area shrink"
    return provenance
