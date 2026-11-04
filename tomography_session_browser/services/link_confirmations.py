"""Atomic app-owned decisions with a bounded read and revision conflict refusal."""
from dataclasses import asdict
import hashlib
import json
import re
from pathlib import Path

from tomography_session_browser.domain.link_confirmations import ConfirmationSnapshot, LinkConfirmation
from tomography_session_browser.services.atomic_files import atomic_write, bounded_read, exclusive, FileChanged, FileTooLarge
from tomography_session_browser.services.settings_service import settings_path
from tomography_session_browser.services.output_boundary import check_output_path
from tomography_session_browser.domain.tomograms import Tomogram
from tomography_session_browser.services.acquisition_index import SeriesEvidence

LIMIT = 4 * 1024 * 1024


def verify_confirmation_sources(items: tuple[Tomogram, ...], targets: tuple[SeriesEvidence, ...]) -> None:
    stamps = {(volume.path, volume.fingerprint.size, volume.fingerprint.mtime_ns)
              for item in items for volume in item.volumes}
    evidence = [block.source for item in items for block in
                (*item.acquisition.movies, *item.acquisition.mdocs, *item.acquisition.stacks) if block.source is not None]
    evidence += [value for target in targets for value in target.provenance]
    evidence += [target.stack_file for target in targets if target.stack_file is not None]
    stamps.update((value.path, value.size, value.mtime_ns) for value in evidence)
    for path, size, mtime in stamps:
        stat = path.stat()
        if (stat.st_size, stat.st_mtime_ns) != (size, mtime):
            raise ValueError('A proposal source changed. Reload the source folders before confirming names.')


class LinkConfirmationStore:
    def __init__(self, path: Path | None = None):
        self.path = path or settings_path().with_name('link_confirmations.json')

    def read(self) -> ConfirmationSnapshot:
        try:
            if self.path.is_symlink():
                raise ValueError('Confirmation storage cannot be a symlink.')
            if not self.path.exists():
                return ConfirmationSnapshot()
            try:
                content = bounded_read(self.path, LIMIT)
            except FileTooLarge as exc:
                raise ValueError('Confirmation storage exceeds its size limit.') from exc
            except FileChanged as exc:
                raise ValueError('Confirmation storage changed while reading.') from exc
            data = json.loads(content)
            if data.get('schema') != 1 or type(data.get('revision')) is not int or data['revision'] < 0:
                raise ValueError('Unsupported confirmation storage schema/revision.')
            records, seen = [], set()
            for row in data['records']:
                if not isinstance(row, dict) or set(row) != {'source_id', 'tomogram_id', 'target_key', 'signature', 'decision'}:
                    raise ValueError('Invalid confirmation record.')
                if any(not isinstance(value, str) or not value for value in row.values()):
                    raise ValueError('Invalid confirmation field.')
                record = LinkConfirmation(**row)
                key = record.source_id, record.tomogram_id
                if record.decision not in ('confirmed', 'dismissed', 'revoked') or re.fullmatch('[a-f0-9]{64}', record.signature) is None or key in seen:
                    raise ValueError('Duplicate or invalid confirmation decision.')
                seen.add(key)
                records.append(record)
            return ConfirmationSnapshot(tuple(records), data['revision'], hashlib.sha256(content).hexdigest())
        except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
            return ConfirmationSnapshot(issue=f'Name-link decisions unavailable: {exc}')

    def write(self, expected: ConfirmationSnapshot, changes: tuple[LinkConfirmation, ...],
              protected_roots: tuple[Path, ...] = ()) -> ConfirmationSnapshot:
        keys = {(record.source_id, record.tomogram_id) for record in changes}
        if len(keys) != len(changes) or any(record.decision not in ('confirmed', 'dismissed', 'revoked') or
            re.fullmatch('[a-f0-9]{64}', record.signature) is None or
            not all((record.source_id, record.tomogram_id, record.target_key)) for record in changes):
            raise ValueError('Duplicate or invalid name-link decisions; nothing was saved.')
        lock = self.path.with_suffix('.lock')
        for path in (self.path, lock):
            check_output_path(path, protected_roots)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive(lock, 'Name-link decisions are being saved by another window. Try again after it finishes.'):
            current = self.read()
            if current.issue or expected.issue or (current.revision, current.digest) != (expected.revision, expected.digest):
                raise ValueError(current.issue or 'Name-link decisions changed in another window. Reload Review and try again.')
            records = {(record.source_id, record.tomogram_id): record for record in current.records}
            records.update(((record.source_id, record.tomogram_id), record) for record in changes)
            data = dict(schema=1, revision=current.revision + 1,
                        records=[asdict(value) for _, value in sorted(records.items())])
            encoded = json.dumps(data, allow_nan=False).encode('utf-8')
            if len(encoded) > LIMIT:
                raise ValueError('Confirmation storage exceeds its size limit.')
            atomic_write(self.path, encoded, protected_roots=protected_roots)
            return self.read()
