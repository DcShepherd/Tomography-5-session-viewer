"""cryoCARE's ``predict_config_<ts>.json``: the input of one denoised volume (batch 6).

The file records project-relative paths, for example
``Runs/001516_ProtImodTomoReconstruction/extra/<ts>/<ts>_even.mrc``. The
project is the folder that holds ``Runs/``. The recorded ``output`` must be
the folder the volume was loaded from; otherwise the file belongs to another
copy of the project and nothing is taken from it.
"""
from __future__ import annotations

import json
from pathlib import Path

from tomography_session_browser.domain.denoising import DenoisingInput
from tomography_session_browser.parsers.recorded_metrics import read_metadata
from tomography_session_browser.parsers.scipion_jobs import DENOISED_NAMES, ScipionJob

CONFIG_FOLDER = 'predict_config'


def denoising_input(root: Path, job: ScipionJob | None, folder: Path,
                    name: str) -> tuple[DenoisingInput | None, tuple[str, ...]]:
    """The recorded input of tomogram ``name`` in ``folder``, or a warning that names the file."""
    if job is None or job.protocol not in DENOISED_NAMES:
        return None, ()
    config = (root / CONFIG_FOLDER / f'predict_config_{name}.json').absolute()
    try:
        text = read_metadata(config)
        if text is None:
            return None, ()
        return read_config(text, config, root.parent.parent.parent, folder), ()
    except (OSError, ValueError) as exc:
        return None, (f'{CONFIG_FOLDER}/{config.name}: {exc}',)


def read_config(text: str, config: Path, project: Path, folder: Path) -> DenoisingInput:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError('Expected one JSON object.')
    fields = {key: data.get(key) for key in ('even', 'odd', 'output')}
    if any(not isinstance(value, str) or not value.strip() for value in fields.values()):
        raise ValueError('The file does not name the even and odd input and the output folder.')
    model = data.get('path', '')
    if not isinstance(model, str):
        raise ValueError('The model path is not text.')
    if (project / fields['output']).resolve() != folder.resolve():
        raise ValueError(f'The recorded output folder is not this folder: {fields["output"]}.')
    return DenoisingInput(config, (project / fields['even']).resolve(), (project / fields['odd']).resolve(), model)
