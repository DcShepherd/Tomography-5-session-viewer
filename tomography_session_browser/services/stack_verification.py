"""Worker-only bounded byte verification for the final acquisition-link step."""
from collections.abc import Callable
from dataclasses import replace
import hashlib
from pathlib import Path
import time
from types import MappingProxyType

from tomography_session_browser.domain.acquisition import EvidenceFile, EvidenceMatch
from tomography_session_browser.parsers.acquisition_evidence import evidence_file
from tomography_session_browser.parsers.volume_header import is_cloud_placeholder, read_volume
from tomography_session_browser.services.acquisition_index import AcquisitionIndex
from tomography_session_browser.services.tomogram_links import resolve_tomogram_link

BLOCK = 64 * 1024
CHUNK = 1024 * 1024
VERIFY_LIMIT = 4 * 1024**3
VERIFY_SECONDS = 10.


class VerificationDeferred(ValueError):
    pass


class StackVerifier:
    """One preparation run owns the byte/time allowance and fingerprinted memo."""
    def __init__(self, *, cancelled: Callable[[], bool], byte_limit: int, seconds: float):
        self.cancelled = cancelled
        self.remaining = byte_limit
        self.deadline = time.monotonic()+seconds
        self.samples: dict[EvidenceFile, tuple[str, ...]] = {}
        self.digests: dict[EvidenceFile, str] = {}

    def check(self, stamp: EvidenceFile) -> None:
        if self.cancelled():
            raise InterruptedError('Obsolete acquisition-link preparation')
        if time.monotonic() > self.deadline:
            raise VerificationDeferred('Tilt-stack verification exceeded its worker time allowance; parent remains unresolved.')
        if is_cloud_placeholder(stamp.path) or evidence_file(stamp.path) != stamp:
            raise VerificationDeferred('A tilt stack is unavailable or changed. Reload before verifying its parent.')

    def read(self, stream, count: int, stamp: EvidenceFile) -> bytes:
        self.check(stamp)
        if count > self.remaining:
            raise VerificationDeferred('Tilt-stack verification exceeds the per-load byte allowance; parent remains unresolved.')
        value = stream.read(count)
        self.remaining -= len(value)
        return value

    def sample(self, stamp: EvidenceFile) -> tuple[str, ...]:
        self.check(stamp)
        if stamp not in self.samples:
            volume = read_volume(stamp.path, 'Acquisition stack')
            if volume.grid is None:
                raise VerificationDeferred('Tilt-stack MRC header is invalid or unsupported; verification is deferred.')
            with stamp.path.open('rb') as stream:
                header = self.read(stream, 1024, stamp)
                values = []
                for offset in (0, max(0, (stamp.size-BLOCK)//2), max(0, stamp.size-BLOCK)):
                    stream.seek(offset)
                    values.append(hashlib.sha256(self.read(stream, min(BLOCK, stamp.size-offset), stamp)).hexdigest())
            self.check(stamp)
            self.samples[stamp] = (hashlib.sha256(header).hexdigest(), *values)
        return self.samples[stamp]

    def digest(self, stamp: EvidenceFile) -> str:
        self.check(stamp)
        if stamp not in self.digests:
            if stamp.size > self.remaining:
                raise VerificationDeferred('Tilt-stack verification exceeds the per-load byte allowance; parent remains unresolved.')
            digest = hashlib.sha256()
            with stamp.path.open('rb') as stream:
                offset = 0
                while offset < stamp.size:
                    value = self.read(stream, min(CHUNK, stamp.size-offset), stamp)
                    if not value:
                        raise VerificationDeferred('Tilt stack ended during verification. Reload the folder.')
                    digest.update(value)
                    offset += len(value)
            self.check(stamp)
            self.digests[stamp] = digest.hexdigest()
        return self.digests[stamp]


def prepare_stack_verification(index: AcquisitionIndex, *, cancelled: Callable[[], bool] = lambda: False,
                               byte_limit: int = VERIFY_LIMIT, seconds: float = VERIFY_SECONDS) -> AcquisitionIndex:
    """Never resolve from samples; every potentially matching candidate must finish."""
    verifier = StackVerifier(cancelled=cancelled, byte_limit=byte_limit, seconds=seconds)
    by_key = {value.key: value for value in index.series}
    results = {}
    for item in index.processing:
        if cancelled():
            raise InterruptedError('Obsolete acquisition-link preparation')
        link = resolve_tomogram_link(item, index)
        if link.step != 'D' or link.evidence_state != 'incomplete':
            continue
        if any(b.state == 'incomplete' for b in item.acquisition.stacks):
            continue
        matches: set[str] = set()
        try:
            for block in item.acquisition.stacks:
                if block.state != 'complete':
                    continue
                if block.source is None:
                    raise VerificationDeferred('A raw-stack fingerprint is unavailable; verification is deferred.')
                source = block.source
                verifier.check(source)
                candidates = index.stack_sizes.get(source.size, ())
                if not candidates:
                    continue  # Size mismatch: do not sample any stack.
                source_sample = verifier.sample(source)
                for key in sorted(candidates):
                    candidate = by_key[key]
                    target = evidence_file(candidate.stack_path)
                    if candidate.stack_file is not None and target != candidate.stack_file:
                        raise VerificationDeferred('An acquisition stack changed since loading. Reload the session.')
                    if target.size != source.size:
                        raise VerificationDeferred('An acquisition stack changed size since loading.')
                    if verifier.sample(target) != source_sample:
                        continue
                    if verifier.digest(source) == verifier.digest(target):
                        matches.add(key)
            state = 'ambiguous' if len(matches) > 1 else ('unique' if matches else 'complete-no-match')
            results[item.id] = EvidenceMatch(state, tuple(sorted(matches)))
        except InterruptedError:
            raise
        except (OSError, ValueError, RuntimeError) as exc:
            results[item.id] = EvidenceMatch('incomplete', issue=str(exc))
        if cancelled():
            raise InterruptedError('Obsolete acquisition-link preparation')
    try:
        for stamp in verifier.samples:
            verifier.check(stamp)
    except InterruptedError:
        raise
    except (OSError, ValueError, RuntimeError) as exc:
        # A prior candidate may change while another digest runs. No partial
        # proof set may publish a unique result after losing that evidence.
        results = {key: EvidenceMatch('incomplete', issue=str(exc)) for key in results}
    return replace(index, stack_results=MappingProxyType(results))
