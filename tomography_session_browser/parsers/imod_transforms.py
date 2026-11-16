"""IMOD ``.tlt`` angles and ``.xf`` transforms, read as printed and compared by printed precision.

Two programs that write the same alignment print it differently: Scipion
rewrites Etomo's ``-19.486`` as ``-19.5`` and ``-61.18`` as ``-61.180``. Both
were rounded from one value, so two printed numbers agree when they differ by
no more than the sum of their half-units: ``|a - b| <= ½·10^(-dA) + ½·10^(-dB)``,
where ``d`` is the number of printed decimals. The tolerance comes from the
text, never from a chosen constant. See the divergence register.

Each token must be a plain ASCII number before ``Decimal`` reads it, because
``Decimal`` also accepts ``1_000``, non-ASCII digits, ``Infinity`` and ``sNaN``.
"""
from __future__ import annotations

from decimal import Decimal, localcontext
import re

from tomography_session_browser.domain.alignment import AlignmentTransforms, AlignmentUnavailable, RecordedAlignment

TOKEN = re.compile(r'[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d{1,3})?', re.ASCII)
MAX_DIGITS = 30
XF_COLUMNS = 6
#: Enough digits that a difference of two capped tokens with 3-digit exponents is exact.
EXACT_PRECISION = 2100


def _number(token: str) -> Decimal:
    if TOKEN.fullmatch(token) is None or sum(char.isdigit() for char in token.split('e')[0].split('E')[0]) > MAX_DIGITS:
        raise ValueError(f'"{token[:20]}" is not a plain number.')
    return Decimal(token)


def _rows(text: str, columns: int, name: str) -> tuple[tuple[Decimal, ...], ...]:
    lines = text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()  # A blank end of file is not a row; a blank line inside is.
    rows = []
    for number, line in enumerate(lines, 1):
        tokens = line.split()
        if len(tokens) != columns:
            raise ValueError(f'{name} line {number} has {len(tokens)} values; expected {columns}.')
        rows.append(tuple(_number(token) for token in tokens))
    return tuple(rows)


def parse_transforms(tlt: str | None, xf: str | None) -> RecordedAlignment:
    """The angles and transforms from the two files' text; None means the file is absent."""
    missing = [name for name, text in (('.tlt', tlt), ('.xf', xf)) if text is None]
    if missing:
        return AlignmentUnavailable('absent', f'No {" or ".join(missing)} file.')
    assert tlt is not None and xf is not None
    empty = [name for name, text in (('.tlt', tlt), ('.xf', xf)) if not text.strip()]
    if empty:
        return AlignmentUnavailable('empty', f'The {" and ".join(empty)} file is empty.')
    try:
        angles = tuple(row[0] for row in _rows(tlt, 1, '.tlt'))
        transforms = _rows(xf, XF_COLUMNS, '.xf')
    except ValueError as exc:
        return AlignmentUnavailable('unreadable', str(exc))
    if len(angles) != len(transforms):
        return AlignmentUnavailable('unreadable', f'.tlt has {len(angles)} views but .xf has {len(transforms)}.')
    return AlignmentTransforms(angles, transforms)


def _half_unit(value: Decimal) -> Decimal:
    exponent = value.as_tuple().exponent
    assert isinstance(exponent, int)  # Finite by the token rule.
    return Decimal(5).scaleb(exponent - 1)


def differences(first: AlignmentTransforms, second: AlignmentTransforms) -> int | None:
    """How many printed values disagree beyond their precision; None when the views do not pair up."""
    if len(first.tlt) != len(second.tlt):
        return None
    pairs = [*zip(first.tlt, second.tlt), *(pair for a, b in zip(first.xf, second.xf) for pair in zip(a, b))]
    with localcontext() as context:
        context.prec = EXACT_PRECISION
        return sum(abs(a - b) > _half_unit(a) + _half_unit(b) for a, b in pairs)
