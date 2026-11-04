"""Source-owned reconstruction records; identity and byte validity are separate."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tomography_session_browser.domain.models import MrcMetadata
from tomography_session_browser.domain.recorded_metrics import ProcessingEvidence
from tomography_session_browser.domain.acquisition import AcquisitionEvidence


@dataclass(frozen=True, slots=True)
class VolumeFingerprint:
    path: Path
    size: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class VolumeGrid:
    shape_zyx: tuple[int, int, int]
    axes_crs: tuple[int, int, int]
    spacing_xyz: tuple[float | None, float | None, float | None]
    origin_xyz: tuple[float, float, float]
    starts_crs: tuple[int, int, int]

    @property
    def shape_xyz(self) -> tuple[int, int, int]:
        storage = tuple(reversed(self.shape_zyx))
        by_axis = dict(zip(self.axes_crs, storage, strict=True))
        return (by_axis[1], by_axis[2], by_axis[3])


@dataclass(frozen=True, slots=True)
class Volume:
    id: str
    path: Path
    version: str
    fingerprint: VolumeFingerprint
    grid: VolumeGrid | None
    mrc_metadata: MrcMetadata
    dtype: str = ""
    data_offset: int = 1024
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Tomogram:
    id: str
    name: str
    source_id: str
    volumes: tuple[Volume, ...]
    acquisition: AcquisitionEvidence = AcquisitionEvidence()
    warnings: tuple[str, ...] = ()
    processing: ProcessingEvidence = ProcessingEvidence()


@dataclass(frozen=True, slots=True)
class ProcessingSource:
    id: str
    name: str
    path: Path
    tomograms: tuple[Tomogram, ...]
    pipeline: str = "Warp"
    warnings: tuple[str, ...] = ()
