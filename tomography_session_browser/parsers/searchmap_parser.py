from __future__ import annotations

from pathlib import Path

from tomography_session_browser.domain.models import Overview, SearchMap
from tomography_session_browser.parsers.mrc_parser import read_mrc_metadata
from tomography_session_browser.parsers.path_utils import safe_id, sorted_paths
from tomography_session_browser.parsers.xml_parser import parse_xml_file


def parse_search_map_folder(path: Path) -> SearchMap:
    metadata = {}
    warnings: list[str] = []
    search_map_id = safe_id(path)
    xml_path = _first_existing(path / "SearchMap.xml")
    dm_path = _first_existing(path / "SearchMap.dm")
    if xml_path:
        metadata["SearchMap.xml"] = _parse_xml_metadata(xml_path, warnings)
    if dm_path:
        metadata["SearchMap.dm"] = _parse_xml_metadata(dm_path, warnings)

    overview = _parse_overview(path, search_map_id)
    image_path = _first_existing(path / "SearchMap.jpg")
    mrc_path = _first_existing(path / "SearchMap.mrc")
    if image_path is None and mrc_path is None:
        warnings.append("Search map has no SearchMap.jpg or SearchMap.mrc.")

    tile_paths = sorted_paths(list(path.glob("Tile_*.mrc")) + list(path.glob("Tile_*.jpg")))
    tile_metadata_paths = sorted_paths(list(path.glob("Tile_*.xml")))
    options = _first_existing(path / "TileSetAcquisitionOptions.xml")
    if options:
        metadata["TileSetAcquisitionOptions.xml"] = _parse_xml_metadata(options, warnings)

    return SearchMap(
        id=search_map_id,
        name=path.name,
        image_path=image_path,
        mrc_path=mrc_path,
        mrc_metadata=read_mrc_metadata(mrc_path) if mrc_path is not None else None,
        xml_path=xml_path,
        dm_path=dm_path,
        overview=overview,
        tile_paths=tile_paths,
        tile_metadata_paths=tile_metadata_paths,
        metadata=metadata,
        warnings=warnings,
    )


def _parse_overview(path: Path, search_map_id: str) -> Overview | None:
    image_path = _first_existing(path / "Overview.mrc") or _first_existing(path / "Overview.jpg")
    if image_path is None:
        return None
    metadata = {}
    xml_path = _first_existing(path / "Overview.xml")
    if xml_path:
        metadata["Overview.xml"] = parse_xml_file(xml_path)
    return Overview(
        id=safe_id(image_path),
        name=f"{path.name} / {image_path.stem}",
        image_path=image_path,
        mrc_metadata=read_mrc_metadata(image_path) if image_path.suffix.lower() == ".mrc" else None,
        metadata=metadata,
        linked_search_map_ids=[search_map_id],
    )


def _first_existing(path: Path) -> Path | None:
    return path if path.exists() else None


def _parse_xml_metadata(path: Path, warnings: list[str]) -> dict:
    data = parse_xml_file(path)
    message = data.get("_parse_error") or data.get("_read_error")
    if message:
        warnings.append(f"Could not parse search-map metadata XML {path.name}: {message}")
    return data
