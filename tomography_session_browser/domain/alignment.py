"""Recorded IMOD tilt alignment: the angles and transforms a job wrote, as printed.

Values are exact ``Decimal`` numbers read from their text, so each keeps its
printed precision (``Decimal('-61.180')`` has exponent -3). Equality checks on
tomograms and sources do not compare them (``compare=False``): a source's
identity is its files, not hundreds of numbers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from tomography_session_browser.domain.recorded_metrics import ProcessingEvidence

if TYPE_CHECKING:  # domain.tomograms imports this module.
    from tomography_session_browser.domain.tomograms import VolumeFingerprint


@dataclass(frozen=True, slots=True)
class AlignmentTransforms:
    """One tilt angle per view (``.tlt``) and one 2×3 transform per view (``.xf``)."""

    tlt: tuple[Decimal, ...] = field(compare=False)
    xf: tuple[tuple[Decimal, ...], ...] = field(compare=False)


@dataclass(frozen=True, slots=True)
class AlignmentUnavailable:
    """Why the angles and transforms cannot be compared."""

    kind: Literal['absent', 'empty', 'unreadable']
    detail: str


RecordedAlignment = AlignmentTransforms | AlignmentUnavailable


@dataclass(frozen=True, slots=True)
class AlignmentOffer:
    """What one series folder of an alignment job gives a reconstruction it proves.

    ``evidence`` is written at intake already qualified as matched, and
    ``by_name`` as confirmed by the user on the name alone, so linking joins
    one of them and rewrites no text. ``stamps`` are the files both were read
    from; a saved by-name decision holds only while they stay the same.
    """

    series: str
    alignment: RecordedAlignment
    evidence: ProcessingEvidence = ProcessingEvidence()
    by_name: ProcessingEvidence = ProcessingEvidence()
    stamps: tuple[VolumeFingerprint, ...] = ()
