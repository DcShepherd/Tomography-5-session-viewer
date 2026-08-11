from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from tomography_session_browser.domain.models import Sample, Session
from tomography_session_browser.parsers.xml_parser import find_first


# How an AtlasId was matched to an atlas-bearing sample.
ATLAS_MATCH_EXACT = "exact_path"
ATLAS_MATCH_SUFFIX = "relative_suffix"


@dataclass(frozen=True, slots=True)
class AtlasLinkResolution:
    """Which atlas sample an AtlasId refers to, or why that is undecidable.

    Display-only and transient. ``candidates`` lists every distinct
    atlas-bearing sample the AtlasId could refer to, so an unresolved project
    group can name them instead of silently binding to the first.
    """

    sample: Sample | None = None
    candidates: tuple[Sample, ...] = ()
    method: str = ""
    ambiguous: bool = False

    @property
    def linked(self) -> bool:
        return self.sample is not None and not self.ambiguous


def linked_sample_groups(sessions: list[Session]) -> list[list[Sample]]:
    """Group samples across imported sessions, preferring AtlasId path metadata."""
    atlas_samples = [sample for session in sessions for sample in session.samples if sample.atlas is not None]
    atlas_lookup = _atlas_lookup(sessions)
    groups_by_key: dict[tuple[str, str], list[Sample]] = {}

    for session in sessions:
        for sample in session.samples:
            if sample.atlas is not None:
                key = ("atlas_path", _atlas_group_key(sample))
            elif (resolution := resolve_atlas_for_sample(sample, atlas_lookup)).linked:
                key = ("atlas_path", _atlas_group_key(resolution.sample))
            else:
                # An ambiguous AtlasId deliberately falls back to weak
                # sample-index grouping. Weak groups never contain an
                # atlas-bearing sample, so they cannot be promoted into a
                # top-level linked project group.
                key = ("sample_index", _sample_key(sample))
            groups_by_key.setdefault(key, []).append(sample)

    linked_groups: list[list[Sample]] = []
    seen_keys: set[tuple[str, str]] = set()
    for atlas_sample in atlas_samples:
        key = ("atlas_path", _atlas_group_key(atlas_sample))
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
            dm_path = _atlas_dm_path(sample)
            for candidate in _path_suffixes(dm_path, session.path.parent):
                lookup.append((candidate, sample))
    lookup.sort(key=lambda item: len(item[0]), reverse=True)
    return lookup


def atlas_link_resolutions(sessions: list[Session]) -> dict[int, AtlasLinkResolution]:
    """Resolve the AtlasId of every non-atlas-bearing sample, keyed by ``id(sample)``.

    Only samples that actually record an AtlasId appear in the result, so an
    empty entry means "no atlas claimed" rather than "atlas not found".
    """

    atlas_lookup = _atlas_lookup(sessions)
    resolutions: dict[int, AtlasLinkResolution] = {}
    for session in sessions:
        for sample in session.samples:
            if sample.atlas is not None:
                continue
            if _sample_atlas_id(sample) is None:
                continue
            resolutions[id(sample)] = resolve_atlas_for_sample(sample, atlas_lookup)
    return resolutions


def resolve_atlas_for_sample(
    sample: Sample,
    atlas_lookup: list[tuple[str, Sample]],
) -> AtlasLinkResolution:
    """Resolve an AtlasId to exactly one atlas sample, or to nothing.

    An exact normalised path wins outright. Otherwise a single relative-suffix
    match may link, but several distinct matches stay unresolved rather than
    letting lookup ordering pick a winner.
    """

    atlas_id = _sample_atlas_id(sample)
    if atlas_id is None:
        return AtlasLinkResolution()

    normalized_atlas_id = _normalize_path(atlas_id)
    matched = _dedupe_samples(
        atlas_sample
        for atlas_path, atlas_sample in atlas_lookup
        if _path_ends_with(normalized_atlas_id, atlas_path)
    )
    if not matched:
        return AtlasLinkResolution()

    exact = tuple(
        candidate
        for candidate in matched
        if _normalize_path(_atlas_dm_path(candidate)) == normalized_atlas_id
    )
    if len(exact) == 1:
        return AtlasLinkResolution(sample=exact[0], candidates=exact, method=ATLAS_MATCH_EXACT)
    if len(exact) > 1:
        return AtlasLinkResolution(candidates=exact, method=ATLAS_MATCH_EXACT, ambiguous=True)

    if len(matched) == 1:
        return AtlasLinkResolution(
            sample=matched[0],
            candidates=matched,
            method=ATLAS_MATCH_SUFFIX,
        )
    return AtlasLinkResolution(candidates=matched, method=ATLAS_MATCH_SUFFIX, ambiguous=True)


def matching_atlas_sample(sample: Sample, atlas_lookup: list[tuple[str, Sample]]) -> Sample | None:
    """The unique atlas sample an AtlasId names, or ``None``.

    A thin accessor over :func:`resolve_atlas_for_sample` for callers that only
    need the decided answer and not the candidate list behind it.
    """

    return resolve_atlas_for_sample(sample, atlas_lookup).sample


def _dedupe_samples(samples) -> tuple[Sample, ...]:
    """Collapse repeated sample objects, preserving first-seen order.

    Deduplication is by object identity, not ``sample.id``: two screening
    sessions can legitimately contain samples that share an id string, and
    those are genuinely different candidates.
    """

    unique: dict[int, Sample] = {}
    for sample in samples:
        unique.setdefault(id(sample), sample)
    return tuple(unique.values())


def _atlas_dm_path(sample: Sample) -> Path:
    return sample.path / "Atlas" / "Atlas.dm"


def _atlas_group_key(sample: Sample) -> str:
    """Stable identity for an atlas-bearing sample within one project build.

    Sample IDs are display/parser identifiers and can legitimately repeat in
    screening sessions from different roots. The Atlas path is the strong
    relationship identity already used by the resolver, so grouping must keep
    using it after resolution instead of collapsing back to ``sample.id``.
    """

    return _normalize_path(_atlas_dm_path(sample))


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


def _path_ends_with(path: str, suffix: str) -> bool:
    """Match a normalized path suffix only at a component boundary."""

    normalized_path = path.rstrip("/")
    normalized_suffix = suffix.strip("/")
    return (
        normalized_path == normalized_suffix
        or normalized_path.endswith(f"/{normalized_suffix}")
    )


def _sample_key(sample: Sample) -> str:
    folder_name = sample.path.name
    digits = "".join(char for char in folder_name if char.isdigit())
    return digits or folder_name.lower()


def _sample_sort_key(key: str) -> tuple[int, int | str]:
    return (0, int(key)) if key.isdigit() else (1, key)
