"""What cryoCARE recorded about the input of one denoised volume.

Scipion's cryoCARE prediction job writes ``predict_config/predict_config_<ts>.json``
beside its output. The file names the even and odd half sets it denoised and
the trained model. Paths here are resolved at intake; nothing is inferred
from names.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DenoisingInput:
    config: Path
    even: Path
    odd: Path
    #: The trained model as recorded (a project-relative path), or ''.
    model: str = ''
