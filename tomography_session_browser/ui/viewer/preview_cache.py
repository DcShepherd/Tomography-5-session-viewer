"""Preview cache for the image viewer."""
from __future__ import annotations
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from PySide6.QtGui import QImage
from tomography_session_browser.parsers.mrc_parser import NORMALISATION
import logging
LOGGER = logging.getLogger("tomography_session_browser.ui.image_viewer")


@dataclass(slots=True)
class CachedPreview:
    image: QImage
    slice_count: int
    warnings: list[str]
    estimated_bytes: int
    source_max: int = 0


PreviewCacheKey = tuple[Path, int, int, tuple[int, int], str]


class PreviewLruCache:
    def __init__(self, max_items: int = 32, max_bytes: int = 512 * 1024 * 1024) -> None:
        self._items: OrderedDict[PreviewCacheKey, CachedPreview] = OrderedDict()
        self._max_items = max_items
        self._max_bytes = max_bytes
        self._bytes = 0
        self._lock = RLock()

    def get(self, key: PreviewCacheKey) -> CachedPreview | None:
        with self._lock:
            item = self._items.get(key)
            LOGGER.debug("MRC preview cache %s key=%s", "hit" if item else "miss", key)
            if item is None:
                return None
            self._items.move_to_end(key)
            return item

    def put(self, key: PreviewCacheKey, item: CachedPreview) -> None:
        with self._lock:
            old = self._items.pop(key, None)
            if old is not None:
                self._bytes -= old.estimated_bytes
            self._items[key] = item
            self._bytes += item.estimated_bytes
            self._evict()

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._bytes = 0
            LOGGER.debug("MRC preview cache cleared")

    def _evict(self) -> None:
        while len(self._items) > self._max_items or self._bytes > self._max_bytes:
            key, item = self._items.popitem(last=False)
            self._bytes -= item.estimated_bytes
            LOGGER.debug("MRC preview cache evicted key=%s estimated_bytes=%s", key, item.estimated_bytes)


MRC_PREVIEW_CACHE = PreviewLruCache()

#: Thumbnails have a cache of their own. A contact sheet holds dozens, and in
#: the viewer's 32-item cache they would evict the images the reviewer is
#: stepping through; at a few tens of kilobytes each they are cheap to keep.
THUMBNAIL_CACHE = PreviewLruCache(max_items=256, max_bytes=64 * 1024 * 1024)


def clear_preview_cache() -> None:
    from tomography_session_browser.ui.viewer.preview_scheduler import preview_scheduler
    preview_scheduler().reset()
    MRC_PREVIEW_CACHE.clear()
    THUMBNAIL_CACHE.clear()


def preview_cache_key(path: Path, slice_index: int, max_size: int) -> PreviewCacheKey:
    try:
        stat = path.stat()
        mtime = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        mtime = (0, 0)
    return (path, slice_index, max_size, mtime, NORMALISATION)

