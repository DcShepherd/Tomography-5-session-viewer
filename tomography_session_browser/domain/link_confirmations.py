"""Scalar user decisions; none of these records are scientific evidence."""
from dataclasses import dataclass
from typing import Literal

Decision = Literal['confirmed', 'dismissed', 'revoked']


@dataclass(frozen=True, slots=True)
class LinkConfirmation:
    source_id: str
    tomogram_id: str
    target_key: str
    signature: str
    decision: Decision


@dataclass(frozen=True, slots=True)
class ConfirmationSnapshot:
    records: tuple[LinkConfirmation, ...] = ()
    revision: int = 0
    digest: str = ''
    issue: str = ''
