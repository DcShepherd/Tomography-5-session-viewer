from __future__ import annotations

import re


_SEARCH_MAP_OVERVIEW_RE = re.compile(
    r"^SearchMap_(?P<date>\d{8})_(?P<time>\d{6})\s*/\s*Overview$"
)


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
