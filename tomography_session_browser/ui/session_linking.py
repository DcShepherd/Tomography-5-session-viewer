from __future__ import annotations

from pathlib import Path, PureWindowsPath

from tomography_session_browser.domain.models import Sample, Session
from tomography_session_browser.parsers.xml_parser import find_first


def linked_sample_groups(sessions: list[Session]) -> list[list[Sample]]:
    """Group samples across imported sessions, preferring AtlasId path metadata."""
    atlas_samples = [sample for session in sessions for sample in session.samples if sample.atlas is not None]
    atlas_lookup = _atlas_lookup(sessions)
    groups_by_key: dict[tuple[str, str], list[Sample]] = {}

    for session in sessions:
        for sample in session.samples:
            if sample.atlas is not None:
                key = ("atlas_path", sample.id)
            elif (linked_atlas := _matching_atlas_sample(sample, atlas_lookup)) is not None:
                key = ("atlas_path", linked_atlas.id)
            else:
                key = ("sample_index", _sample_key(sample))
            groups_by_key.setdefault(key, []).append(sample)

    linked_groups: list[list[Sample]] = []
    seen_keys: set[tuple[str, str]] = set()
    for atlas_sample in atlas_samples:
        key = ("atlas_path", atlas_sample.id)
        if key in groups_by_key:
            linked_groups.append(groups_by_key[key])
            seen_keys.add(key)

    fallback_keys = sorted(
        [key for key in groups_by_key if key not in seen_keys],
        key=lambda key: _sample_sort_key(key[1]),
    )
    linked_groups.extend(groups_by_key[key] for key in fallback_keys)
    return linked_groups


def sample_sort_key_for_group(samples: list[Sample]) -> tuple[int, int | str]:
    atlas_sample = next((sample for sample in samples if sample.atlas is not None), samples[0])
    return _sample_sort_key(_sample_key(atlas_sample))


def _atlas_lookup(sessions: list[Session]) -> list[tuple[str, Sample]]:
    lookup: list[tuple[str, Sample]] = []
    for session in sessions:
        for sample in session.samples:
            if sample.atlas is None:
                continue
            dm_path = sample.path / "Atlas" / "Atlas.dm"
            for candidate in _path_suffixes(dm_path, session.path.parent):
                lookup.append((candidate, sample))
    lookup.sort(key=lambda item: len(item[0]), reverse=True)
    return lookup


def _matching_atlas_sample(sample: Sample, atlas_lookup: list[tuple[str, Sample]]) -> Sample | None:
    atlas_id = _sample_atlas_id(sample)
    if atlas_id is None:
        return None
    normalized_atlas_id = _normalize_path(atlas_id)
    for atlas_path, atlas_sample in atlas_lookup:
        if normalized_atlas_id.endswith(atlas_path):
            return atlas_sample
    return None


def _sample_atlas_id(sample: Sample) -> str | None:
    session_dm = sample.metadata.get("Session.dm")
    atlas_id = find_first(session_dm, "AtlasId")
    if isinstance(atlas_id, dict):
        atlas_id = atlas_id.get("value")
    if isinstance(atlas_id, str) and atlas_id.strip():
        return atlas_id.strip()
    return None


def _path_suffixes(path: Path, base: Path) -> list[str]:
    suffixes = [_normalize_path(path)]
    try:
        suffixes.append(_normalize_path(path.relative_to(base)))
    except ValueError:
        pass
    return suffixes


def _normalize_path(value: str | Path) -> str:
    text = str(value).replace("\\", "/")
    return str(PureWindowsPath(text)).replace("\\", "/").lower()


def _sample_key(sample: Sample) -> str:
    folder_name = sample.path.name
    digits = "".join(char for char in folder_name if char.isdigit())
    return digits or folder_name.lower()


def _sample_sort_key(key: str) -> tuple[int, int | str]:
    return (0, int(key)) if key.isdigit() else (1, key)
