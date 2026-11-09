"""Scipion job folders: a source label and the job's own file names (B6, owner 9 Nov 2026).

A Scipion project's ``Runs/`` holds one folder per job, ``NNNNNN_ProtClassName``,
with ``extra/`` (the outputs) and ``logs/``. Only folder names are read; never
the job's ``.sqlite`` sets. Choosing ``Runs/`` or the project still finds
nothing: there is no search of neighbouring jobs.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

JOB_NAME = re.compile(r"^(?P<number>\d{6})_(?P<protocol>Prot[A-Za-z0-9]+)$")
OUTPUTS = "extra"
#: Short labels for the protocols in the owner's project; any other is split into words.
LABELS = {
    "ProtImodTomoReconstruction": "IMOD reconstruction",
    "ProtImodEtomo": "IMOD Etomo",
    "ProtCryoCAREPrediction": "cryoCARE denoised",
}
#: cryoCARE writes ``<ts>_denoised/<ts>_.mrc``: (folder ending, file ending).
DENOISED_NAMES = {"ProtCryoCAREPrediction": ("_denoised", "_")}
HALF_SETS = (("_even", "Even"), ("_odd", "Odd"))


@dataclass(frozen=True, slots=True)
class ScipionJob:
    number: int
    protocol: str

    @property
    def label(self) -> str:
        name = LABELS.get(self.protocol) or " ".join(re.findall(r"[A-Z][a-z0-9]*|[a-z0-9]+", self.protocol[4:]))
        return f"{name} · {self.number}"

    def tomogram_name(self, folder: str, stem: str) -> str | None:
        """The tomogram name when ``stem`` is ``folder``'s output by this job's own naming, else None."""
        endings = DENOISED_NAMES.get(self.protocol)
        if endings is None or not folder.endswith(endings[0]):
            return None
        name = folder[:-len(endings[0])]
        return name if name and stem == name + endings[1] else None

    @staticmethod
    def half_set(tomogram: str, stem: str) -> str | None:
        """``Even`` or ``Odd`` when ``stem`` is a half set of the tomogram file ``tomogram``."""
        return next((version for ending, version in HALF_SETS if stem == tomogram + ending), None)


def job_of(folder: Path) -> ScipionJob | None:
    """The job a folder is, when its name is a job name and it has ``extra/`` and ``logs/``."""
    match = JOB_NAME.fullmatch(folder.name)
    try:
        if match is None or not (folder / OUTPUTS).is_dir() or not (folder / "logs").is_dir():
            return None
    except OSError:
        return None
    return ScipionJob(int(match["number"]), match["protocol"])


def scipion_job(root: Path) -> ScipionJob | None:
    """The job whose ``extra`` folder ``root`` is, or None."""
    return job_of(root.parent) if root.name == OUTPUTS else None


def processing_root(path: Path) -> Path:
    """A job folder reads as its ``extra`` folder; any other path is unchanged.

    Every place where a chosen path becomes a source key uses this, so the job
    folder and its ``extra`` folder are one source.
    """
    return path / OUTPUTS if job_of(path) is not None else path
