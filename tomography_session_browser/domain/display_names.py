from __future__ import annotations

import re


_SEARCH_MAP_OVERVIEW_RE = re.compile(
    r"^SearchMap_(?P<date>\d{8})_(?P<time>\d{6})\s*/\s*Overview$"
)


def count_phrase(count: int, singular: str, plural: str | None = None) -> str:
    """Return ``"<count> <noun>"`` with the noun agreeing with ``count``.

    Lives in ``domain`` because the same phrasing is needed by services
    (viewer row statuses), presenters, and widgets, and services must not
    import from ``ui``.

    ``plural`` is for nouns that are not formed by appending "s" — including
    invariant ones such as "tilt series", which must be passed explicitly so
    a single series does not read as "1 tilt serie".
    """

    noun = singular if count == 1 else (plural if plural is not None else f"{singular}s")
    return f"{count:,} {noun}"


def format_search_tile_display_name(name: str | None, search_map_name: str | None) -> str:
    """``"SA_control_1 · Tile 17"`` for a search tile named
    ``"SA_control_1 · Tile 17 · SearchMap_20260924_113244"``.

    Display only (harmonisation B3). Every row of a search map's tiles ended in
    the same map name, so a narrow list kept only that shared tail
    ("…archMap_20260924_113244") and dropped the part that tells rows apart.
    The map name moves to the row's second line; the parsed name, which is the
    tile's full description, is untouched and stays in the tooltip.
    """

    text = (name or "").strip()
    suffix = f" · {search_map_name}" if search_map_name else ""
    if suffix and text.endswith(suffix) and len(text) > len(suffix):
        return text[: -len(suffix)]
    return text


def format_overview_display_name(name: str | None) -> str:
    """Return a compact, display-only label for overview names.

    Tomography 5 names overviews after their source search-map timestamp
    (``SearchMap_YYYYMMDD_HHMMSS / Overview``). That is useful metadata, but
    too verbose for the overview list and atlas overlays. Keep the original
    parsed name untouched and only shorten names that match this exact shape.
    """

    text = (name or "").strip()
    match = _SEARCH_MAP_OVERVIEW_RE.fullmatch(text)
    if match is None:
        return text
    return f"Overview_{match.group('time')}"
