from __future__ import annotations

from enum import StrEnum


class SessionKind(StrEnum):
    UNKNOWN = "unknown"
    PROCESSING = "processing"
    ATLAS_SCREENING = "atlas_screening"
    MULTIGRID = "multigrid"
    COLLECTION = "collection"
    SINGLE_COLLECTION = "single_collection"
