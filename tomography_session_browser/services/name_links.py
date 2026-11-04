"""Exact name proposals and signatures; no file reads and no implicit links."""
from dataclasses import asdict
import hashlib
import json

from tomography_session_browser.domain.link_confirmations import LinkConfirmation
from tomography_session_browser.domain.reconstruction_names import reconstruction_name
from tomography_session_browser.domain.tomograms import Tomogram
from tomography_session_browser.services.acquisition_index import AcquisitionIndex, SeriesEvidence


def candidates(item: Tomogram, index: AcquisitionIndex) -> tuple[SeriesEvidence, ...]:
    return index.names.get(reconstruction_name(item.name), ())


def proposal_signature(item: Tomogram, target: SeriesEvidence) -> str:
    payload = (item.source_id, item.id, item.name,
        tuple((volume.id, str(volume.path), volume.fingerprint.size, volume.fingerprint.mtime_ns) for volume in item.volumes),
        asdict(item.acquisition), target.key, target.name, sorted(target.paths), sorted(target.movies),
        tuple((row.z_value, row.timestamp, str(row.angle)) for row in target.records), target.image_file,
        str(target.stack_path), target.stack_size, asdict(target.stack_file) if target.stack_file else None,
        tuple(asdict(value) for value in target.provenance))
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str, separators=(',', ':')).encode()).hexdigest()


def decision_for(item: Tomogram, target: SeriesEvidence, signature: str,
                 records: tuple[LinkConfirmation, ...]) -> LinkConfirmation | None:
    found = [record for record in records if (record.source_id, record.tomogram_id) == (item.source_id, item.id)]
    if len(found) == 1 and (found[0].target_key, found[0].signature) == (target.key, signature):
        return found[0]
    return None
