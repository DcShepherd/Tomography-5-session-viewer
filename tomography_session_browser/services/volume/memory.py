"""One thread-safe budget for native planes, live render buffers and exports.

Reservations never wait on the GUI thread. Only unpinned cache entries can be
evicted. A failed reservation reports a recoverable limit, without publishing
partially constructed buffers. Headroom is sampled on every reservation.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
from threading import RLock
from typing import Callable, Iterator

import numpy as np

MIB = 1024 * 1024


def system_memory() -> tuple[int, int]:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        class Status(ctypes.Structure):
            _fields_ = [("length", wintypes.DWORD), ("load", wintypes.DWORD),
                        *[(name, ctypes.c_ulonglong) for name in (
                            "total", "available", "page_total", "page_available",
                            "virtual_total", "virtual_available", "extended")]]
        status = Status()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.total), int(status.available)
    elif Path("/proc/meminfo").exists():
        values = {line.split(":")[0]: int(line.split()[1]) * 1024
                  for line in Path("/proc/meminfo").read_text().splitlines() if ":" in line}
        return values["MemTotal"], values.get("MemAvailable", values["MemFree"])
    return 2 * 1024 * MIB, 1024 * MIB


class BudgetExceeded(ValueError):
    pass


class MemoryLease:
    def __init__(self, budget: MemoryBudget, size: int, kind: str) -> None:
        self.budget, self.size, self.kind = budget, size, kind
        self.closed = False

    def close(self) -> None:
        if not self.closed:
            with self.budget.lock:
                self.budget.live[self.kind] -= self.size
                self.closed = True

    def __enter__(self) -> MemoryLease:
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()


@dataclass
class CacheEntry:
    array: np.ndarray
    pins: int = 0
    retired: bool = False


class MemoryBudget:
    def __init__(self, *, percent: int = 40, limit: int | None = None,
                 probe: Callable[[], tuple[int, int]] = system_memory) -> None:
        self.percent, self.fixed_limit, self.probe = percent, limit, probe
        self.lock = RLock()
        self.live: dict[str, int] = {}
        self.cache: OrderedDict[tuple, CacheEntry] = OrderedDict()
        self.hits = self.misses = self.evictions = 0
        self.peak = 0

    def configure(self, percent: int) -> None:
        if type(percent) is not int or not 10 <= percent <= 60:
            raise ValueError("Tomogram memory share must be 10–60% of physical RAM.")
        with self.lock:
            self.percent = percent
            self.evict_to(self.limit())

    def limit(self) -> int:
        if self.fixed_limit is not None:
            return self.fixed_limit
        total, available = self.probe()
        reserve = max(512 * MIB, total // 10)
        # Add back only known committed buffers. Scratch reservations may not
        # have allocated yet and must never manufacture apparent headroom.
        committed = sum(entry.array.nbytes for entry in self.cache.values()) + self.live.get("display-buffers", 0) // 2
        return max(0, min(total * self.percent // 100, available + committed - reserve))

    def used(self) -> int:
        return sum(self.live.values()) + sum(entry.array.nbytes for entry in self.cache.values())

    def evict_to(self, target: int) -> None:
        for key in list(self.cache):
            if self.used() <= target:
                break
            if self.cache[key].pins == 0:
                del self.cache[key]
                self.evictions += 1

    def reserve(self, size: int, kind: str, *, speculative: bool = False, evict: bool = True,
                group_limit: int | None = None) -> MemoryLease:
        if size < 0:
            raise ValueError("Negative memory reservation")
        with self.lock:
            if group_limit is not None:
                group_used = self.live.get(kind, 0) + sum(
                    entry.array.nbytes for key, entry in self.cache.items() if key and key[0] == kind)
                if group_used + size > group_limit:
                    raise BudgetExceeded("The derived-plane cache allowance is busy or too small.")
            limit = self.limit()
            if speculative and any(self.live.get(kind, 0) for kind in ("render-working", "encoder")):
                raise BudgetExceeded("Foreground work has priority over native-plane prefetch.")
            if not speculative and evict:
                self.evict_to(limit - size)
            if self.used() + size > limit:
                raise BudgetExceeded("Tomogram memory budget is busy or too small for this operation. "
                                     "Stop playback, reduce slices/filters, or raise the RAM share in Image settings.")
            self.live[kind] = self.live.get(kind, 0) + size
            self.peak = max(self.peak, self.used())
            return MemoryLease(self, size, kind)

    @contextmanager
    def borrow(self, key: tuple) -> Iterator[np.ndarray | None]:
        with self.lock:
            entry = self.cache.get(key)
            if entry is not None and entry.retired:
                entry = None
            if entry is not None:
                entry.pins += 1
                self.cache.move_to_end(key)
                self.hits += 1
            else:
                self.misses += 1
        try:
            yield entry.array if entry is not None else None
        finally:
            if entry is not None:
                self.unpin(key, entry)

    def unpin(self, key: tuple, entry: CacheEntry) -> None:
        with self.lock:
            entry.pins -= 1
            if entry.retired and not entry.pins and self.cache.get(key) is entry:
                del self.cache[key]
            self.evict_to(self.limit())

    def contains(self, key: tuple) -> bool:
        with self.lock:
            entry = self.cache.get(key)
            return entry is not None and not entry.retired

    def retire(self, key: tuple) -> None:
        """Stop new borrows; existing readers remain accounted until release."""
        with self.lock:
            entry = self.cache.get(key)
            if entry is not None:
                entry.retired = True
                if not entry.pins:
                    del self.cache[key]

    def retire_group(self, kind: str, *, keep: tuple[tuple, ...] = ()) -> None:
        """Retire one derived-cache family; pinned readers retain their accounting."""
        with self.lock:
            retained = set(keep)
            for key in tuple(self.cache):
                if key and key[0] == kind and key not in retained:
                    self.retire(key)

    def publish(self, key: tuple, array: np.ndarray, reservation: MemoryLease) -> None:
        """Transfer an already-accounted completed plane to the evictable LRU."""
        with self.lock:
            if array.nbytes > reservation.size or reservation.closed:
                raise BudgetExceeded("A completed native plane must have a live allocation reservation.")
            previous = self.cache.get(key)
            if previous is not None and previous.retired:
                raise BudgetExceeded("A retired source is still in use. Continue streaming until its readers finish.")
            # Another reader may have completed the same plane first.
            if key not in self.cache:
                array.setflags(write=False)
                self.cache[key] = CacheEntry(array)
                self.cache.move_to_end(key)
            reservation.close()

    @contextmanager
    def adopt(self, key: tuple, array: np.ndarray, reservation: MemoryLease) -> Iterator[np.ndarray]:
        with self.lock:
            self.publish(key, array, reservation)
            entry = self.cache[key]
            entry.pins += 1
            for old in list(self.cache):
                if len(self.cache) <= 256:
                    break
                if not self.cache[old].pins:
                    del self.cache[old]
                    self.evictions += 1
        try:
            yield entry.array
        finally:
            self.unpin(key, entry)

    def clear_unpinned(self) -> None:
        with self.lock:
            self.evict_to(sum(self.live.values()))

    def snapshot(self) -> dict[str, int]:
        with self.lock:
            return {"limit": self.limit(), "used": self.used(), "resident": sum(e.array.nbytes for e in self.cache.values()),
                    "pinned": sum(e.array.nbytes for e in self.cache.values() if e.pins),
                    "live": sum(self.live.values()), "peak": self.peak, "hits": self.hits, "misses": self.misses, "evictions": self.evictions}


VOLUME_MEMORY = MemoryBudget()
