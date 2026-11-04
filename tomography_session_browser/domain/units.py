"""Canonical unit strings for user-facing text.

Every scientific unit the app prints goes through this module. Before it
existed the same quantity was spelled differently depending on which layer
rendered it — the tilt-series viewer header showed ``6.78 A/px`` while the
badge drawn over the very same image showed ``6.78 Å/px``, and the context
panel mixed ``deg`` with ``°`` and ``um`` with ``µm`` between adjacent
sections. Import the constants instead of typing the glyphs inline so the
app, the PDF report, and the tests can only disagree in one place.

All values are the correct typographic characters:

* ``Å`` is U+00C5 LATIN CAPITAL LETTER A WITH RING ABOVE (not ``A``).
* ``µ`` is U+00B5 MICRO SIGN (not the letter ``u``).
* ``°`` is U+00B0 DEGREE SIGN (not ``deg``).
* ``e⁻`` uses U+207B SUPERSCRIPT MINUS.
"""

from __future__ import annotations

from typing import Final


ANGSTROM: Final[str] = "Å"
ANGSTROM_PER_PIXEL: Final[str] = "Å/px"
ANGSTROM_PER_PIXEL_LONG: Final[str] = "Å/pixel"
MICROMETRE: Final[str] = "µm"
MICROMETRE_PER_PIXEL: Final[str] = "µm/px"
NANOMETRE: Final[str] = "nm"
NANOMETRE_PER_PIXEL: Final[str] = "nm/px"
DEGREE: Final[str] = "°"
ELECTRONS_PER_ANGSTROM_SQUARED: Final[str] = "e⁻/Å²"

#: Spellings that must never reach the interface. Used by the regression
#: test that scans user-facing modules for unit drift.
DISALLOWED_UNIT_SPELLINGS: Final[tuple[str, ...]] = ("A/px", "A/pixel", "um", "deg")
