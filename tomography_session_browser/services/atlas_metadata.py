"""Read-only checks tying atlas projection metadata to its displayed mosaic."""

from __future__ import annotations

from tomography_session_browser.domain.models import Atlas
from tomography_session_browser.parsers.xml_parser import find_first


def referenced_atlas_stem(atlas_dm: object) -> str | None:
    """The mosaic named by Atlas.dm, when the reference was recorded."""

    if not isinstance(atlas_dm, dict):
        return None
    reference = find_first(atlas_dm, "AtlasImageReference")
    if not isinstance(reference, dict):
        return None
    name = reference.get("BaseFileName")
    if isinstance(name, dict):
        name = name.get("value")
    return name.strip() if isinstance(name, str) and name.strip() else None


def atlas_projection_mismatch(atlas: Atlas) -> str | None:
    """Explain a known acquisition mismatch; absent references retain legacy behaviour.

    The node table and other Atlas.dm geometry belong to its named mosaic.
    Falling back to another image cannot reuse either the affine or the
    legacy stage calibration from that file.
    """

    metadata = atlas.metadata if isinstance(atlas.metadata, dict) else {}
    reference = referenced_atlas_stem(metadata.get("Atlas.dm"))
    if atlas.image_path is None or reference is None or reference == atlas.image_path.stem:
        return None
    return (
        f"Atlas.dm describes {reference}, but the displayed image is {atlas.image_path.stem}. "
        "Projection metadata for the displayed acquisition is unavailable."
    )
