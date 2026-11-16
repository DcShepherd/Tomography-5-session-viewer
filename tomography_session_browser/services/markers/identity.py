"""One marker-id token per batch record in a marker context.

Batch IDs embed their folder, so only contradictory records in one
``BatchPositionsList.xml`` share an ID. Each such record keeps its own markers:
its token is ``<id>#<n>``, numbered in context order. Every other batch keeps
its ID as its token, so ordinary marker IDs do not change. Display only.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from tomography_session_browser.domain.markers import ImageMarker
from tomography_session_browser.domain.models import BatchPosition
from tomography_session_browser.services.relationship_identity import ambiguous_batch_ids

@dataclass(frozen=True, slots=True, eq=False)
class BatchIdentity:
    ambiguous_ids: frozenset[str] = frozenset()
    # Holds the records so the object identities below stay valid.
    _records: tuple[tuple[BatchPosition, str], ...] = field(default=(), repr=False)
    _by_object: dict[int, str] = field(default_factory=dict, repr=False)

    @classmethod
    def of(cls, batch_positions: Iterable[BatchPosition]) -> BatchIdentity:
        batches = tuple(batch_positions)
        ambiguous = ambiguous_batch_ids(batches)
        counts: dict[str, int] = {}
        records: list[tuple[BatchPosition, str]] = []
        for batch in batches:
            if batch.id not in ambiguous or any(batch is seen for seen, _ in records):
                continue
            counts[batch.id] = counts.get(batch.id, 0) + 1
            records.append((batch, f"{batch.id}#{counts[batch.id]}"))
        return cls(
            ambiguous_ids=ambiguous,
            _records=tuple(records),
            _by_object={id(batch): token for batch, token in records},
        )

    def is_ambiguous(self, batch: BatchPosition) -> bool:
        return batch.id in self.ambiguous_ids

    def token_for(self, batch: BatchPosition) -> str:
        return self._by_object.get(id(batch), batch.id)

    def stamp(self, batch: BatchPosition, markers: Iterable[ImageMarker]) -> None:
        """Flag ``batch``'s markers when its ID is ambiguous, for display lookups."""

        if self.is_ambiguous(batch):
            for marker in markers:
                marker.metadata["batch_id_ambiguous"] = True
