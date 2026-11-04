"""One completed IMOD positioning recommendation, in its already-scaled pixels."""
from dataclasses import dataclass
import math
import re


@dataclass(frozen=True, slots=True)
class PositioningThickness:
    pixels: int
    computed_pixels: float
    scale: float
    border: float
    field: str


def positive(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value) or value <= 0:
        raise ValueError('Requires a finite positive value.')
    return value


def log_parameters(text: str) -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        key, separator, value = line.strip().partition(' = ')
        if not separator or key in values:
            raise ValueError('Malformed or duplicate tomopitch parameter.')
        values[key] = value.strip()
    return values


def parse_tomopitch(text: str) -> PositioningThickness:
    header = '*** Entries to program tomopitch ***'
    end = '*** End of entries ***'
    if text.count(header) != 1 or text.count(end) != 1 or not text.rstrip().endswith('SUCCESSFULLY COMPLETED'):
        raise ValueError('Expected one completed IMOD tomopitch run.')
    block, body = text.split(header, 1)[1].split(end, 1)
    params = log_parameters(block)
    if params.get('ModelFile') != 'tomopitch.mod':
        raise ValueError('Requires the sampled single tomopitch.mod boundary-model workflow.')
    scale = positive(params.get('ScaleFactor', ''))
    border = float(params.get('ExtraThickness', ''))
    if not math.isfinite(border) or border < 0:
        raise ValueError('Invalid tomopitch border thickness.')
    aggregate = 'Analysis of positions from all line pairs:'
    if body.count(aggregate) != 1:
        raise ValueError('Requires one aggregate boundary-model analysis.')
    final = body.split(aggregate, 1)[1]
    spacing = float(params.get('SpacingInY', '0'))
    if not math.isfinite(spacing) or params.get('NoXAxisTilt', '0') not in ('0', '1'):
        raise ValueError('Invalid positioning pitch parameters.')
    use_pitch = spacing != 0 and params.get('NoXAxisTilt', '0') == '0'
    field = 'x-tilted' if use_pitch else 'rotated'
    lines = [line.strip() for line in final.splitlines() if line.strip().startswith(field + ' ')]
    if len(lines) != 1:
        raise ValueError(f'Requires one final {field} thickness recommendation.')
    match = re.fullmatch(re.escape(field) + r'\s+lines imply added Z shift of\s+(\S+); thickness of\s+(\S+), set to\s+(\d+)', lines[0])
    if match is None:
        raise ValueError('Malformed final tomopitch thickness recommendation.')
    shift, computed, rounded = match.groups()
    if not math.isfinite(float(shift)):
        raise ValueError('Invalid positioning shift.')
    positive(rounded)  # Refuse integers outside the finite display/conversion range.
    computed_value, pixels = positive(computed), int(rounded)
    if pixels < computed_value or pixels <= 0:
        raise ValueError('Invalid rounded tomopitch recommendation.')
    return PositioningThickness(pixels, computed_value, scale, border, field)
