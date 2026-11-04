"""Loosely typed metadata values as typed ones, or ``None``.

Two families, and the difference matters:

* ``as_float``, ``as_int`` and ``as_str`` narrow. They accept a value that
  already has the type (a finite number, an int, a string with something in
  it) and refuse everything else, so ``"3"`` is not a number to them. Bools
  pass as ints, as they do in Python.
* ``float_or_none`` and ``text_or_none`` convert. Whatever ``float()`` or
  ``str()`` accepts, they accept.

``int_or_zero`` converts to a count, 0 on failure. It reads through
``float``, so a decimal string such as ``"3.0"`` is still a count (3). It was
two readers until divergence-register rule 4 chose this one.
"""

from __future__ import annotations

import math
from typing import Any


def as_float(value: Any) -> float | None:
    """A finite int or float, as a float; anything else is ``None``."""

    if not isinstance(value, int | float):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def as_int(value: Any) -> int | None:
    """An int; anything else, floats and strings included, is ``None``."""

    return int(value) if isinstance(value, int) else None


def as_str(value: Any) -> str | None:
    """A string with something in it, stripped; anything else is ``None``."""

    return value.strip() if isinstance(value, str) and value.strip() else None


def float_or_none(value: Any) -> float | None:
    """``float(value)``, or ``None`` when that fails. NaN and infinity pass."""

    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def text_or_none(value: Any) -> str | None:
    """``str(value)`` stripped, or ``None`` when nothing is left."""

    if value is None:
        return None
    text = str(value).strip()
    return text or None


def int_or_zero(value: Any) -> int:
    """``int(float(str(value)))``, or 0 when that fails: ``"3.0"`` is 3."""

    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0
